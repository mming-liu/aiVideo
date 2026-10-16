"""火山方舟 Agent Plan (/api/plan/v3) 视频生成 — Seedance 系列.

与标准方舟 (api/v3) 的区别：
  - 端点：
      提交 POST {base}/contents/generations/tasks
      轮询 GET  {base}/contents/generations/tasks/{id}
      列表 GET  {base}/contents/generations/tasks?page_num=&page_size=...
      取消 DELETE {base}/contents/generations/tasks/{id}
  - 鉴权：Bearer AGENT_PLAN_API_KEY（**专属 key，不通用**）
  - 模型：doubao-seedance-2-0（默认；时长 4-15s；支持 reference_image/generate_audio）
  - 切换：VIDEO_PROVIDER=agent_plan（.env.content）；volcengine 不受影响

设计要点（2026-08-30）：
  - 复用 volcengine _encode_media（图片转 base64 data URL 逻辑一致）
  - 复用 volcengine _build_content 思路：r2v 走 reference_image；多关键帧降级；
    首帧/首尾帧按 model_caps 能力走 first_frame/last_frame role
  - 内置信号量：AGENT_PLAN_MAX_CONCURRENCY（默认 2，Small 套餐保守值）
  - 复用 vimax_working_dir 落盘路径（与 volcengine 一致）
  - TOS url 短期有效 → 任务成功立即落盘（避免缓存/延迟导致 url 失效）
  - 单一模型队列（Agent Plan 当前未配 fallback）

官方文档：火山方舟 Agent Plan / 视频生成
"""
from __future__ import annotations

import base64
import mimetypes
import os
import re
import threading
import time
from functools import partial
from pathlib import Path
from typing import Optional

import requests

from .config import config
from .models import (
    OBS_EXPIRED,
    OBS_FAILED,
    PreparedVideoRequest,
    TaskObservation,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from .service import VideoGenerationService
from shared.config import (
    AGENT_PLAN_API_KEY,
    AGENT_PLAN_BASE_URL,
    AGENT_PLAN_T2V_MODEL,
    AGENT_PLAN_VIDEO_DURATION,
    AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS,
    AGENT_PLAN_VIDEO_POLL_INTERVAL,
    AGENT_PLAN_VIDEO_RATIO,
    AGENT_PLAN_VIDEO_RESOLUTION,
    AGENT_PLAN_MAX_CONCURRENCY,
    AGENT_PLAN_VIDEO_RPM,
)
from shared.model_caps import clamp_duration, get_seedance_cap, resolve_resolution
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02b.log")

# 任务状态归一化单源：``_video_gen.models.TaskObservation``（与 volcengine 同判据）。

_RATIO_VALID = ("16:9", "9:16", "1:1", "adaptive")
# ⚠️ 与 volcengine provider 的已知分歧（本次只上移共用函数，不做行为对齐）：
# 1) 本 provider 无 prompt 压缩兜底（volcengine 有 >cap-40 触发的压缩链）；
# 2) 429 处理策略不同。对齐与否待后续决策（见硬编码清理计划 C1）。
# 2026-10-03（§V2 改动 6）：**提交**路径不再有 POST 重试循环——prepared 入口每次
# 至多一个生成 POST；429/网络/5xx 由 classify_post_response 分类后交上层记录。


def _capability_revision(model: str) -> str:
    """能力注册表条目的指纹（进 prepared/content key；能力变化 ⇒ 请求失效）。"""
    import dataclasses
    import hashlib
    import json
    from shared.model_caps import _model_prompt_cap
    cap = get_seedance_cap(model)
    facts = {
        "model": model,
        "cap": dataclasses.asdict(cap) if dataclasses.is_dataclass(cap) else vars(cap),
        "prompt_cap": _model_prompt_cap(model),
    }
    blob = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    return "caps-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _compose_dir(prepared, context) -> Path:
    """参考表落盘目录：候选上下文 ⇒ attempt 内（**绝不**写正式 shots/）。"""
    attempt = getattr(context, "attempt_dir", None) or getattr(context, "experiment_root", None)
    if attempt:
        d = Path(attempt) / "assets" / "ref_sheets"
    else:
        from shared.paths import shot_work_dir
        d = shot_work_dir(int((prepared.source or {}).get("shot_index") or 0))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _encode_media(path: Optional[str]) -> Optional[str]:
    """读取本地文件，返回 base64 data URL（Seedance 接受 data URI）."""
    if not path:
        return None
    try:
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return None
        mime, _ = mimetypes.guess_type(p)
        if not mime:
            mime = "image/png"
        with open(p, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


# _clamp_duration / _RES_ORDER / _resolve_resolution 已上移 shared/model_caps.py
# （clamp_duration / resolve_resolution，与 volcengine 共用单实现）。


def _resolve_ratio(req: VideoGenerationRequest, cap, is_first_last_frames: bool = False) -> str:
    """按模型能力推导 ratio（与 volcengine 同款逻辑）.

    Seedance 2.5 在首帧/首尾帧场景仅支持 adaptive（不可被 override）；
    其余模型/场景：AGENT_PLAN_VIDEO_RATIO 非空则强制，否则按 req.size 推导。
    """
    if cap.i2v_frames_ratio_adaptive and is_first_last_frames:
        return "adaptive"
    override = (AGENT_PLAN_VIDEO_RATIO or "").strip().lower()
    if override in _RATIO_VALID:
        return override
    size = (req.size or "").strip().lower().replace("*", "x")
    try:
        w, h = size.split("x")
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        return "9:16"
    if w == h:
        return "1:1"
    return "16:9" if w > h else "9:16"


class AgentPlanVideoService(VideoGenerationService):
    """火山方舟 Agent Plan Seedance 视频生成服务.

    支持：
      - T2V（文生视频）
      - I2V（图生视频，含首尾帧/多关键帧降级）
      - R2V（参考图，按模型能力自动收敛；Agent Plan 当前走 doubao-seedance-2-0）

    并发护栏：AGENT_PLAN_MAX_CONCURRENCY（默认 2）信号量内置——不依赖调用方。
    """

    def __init__(self) -> None:
        config.validate()
        if not AGENT_PLAN_API_KEY:
            raise ValueError(
                "没有配置 AGENT_PLAN_API_KEY（.env.keys 中设置，Agent Plan 专属 key，"
                "与 VOLCENGINE_API_KEY 不通用）"
            )
        self._model = AGENT_PLAN_T2V_MODEL
        self._base_url = AGENT_PLAN_BASE_URL.rstrip("/")
        # 套餐并发护栏（Small 套餐保守值 2）
        self._semaphore = threading.BoundedSemaphore(
            value=max(1, AGENT_PLAN_MAX_CONCURRENCY)
        )
        # 提交限速（2026-09-13 审计修复）：此前只有并发信号量，提交突发全靠
        # 429 事后重试；与文本/图片侧 RPM 调度同口径，提交前主动摊平间隔
        self._submit_lock = threading.Lock()
        self._last_submit_ts = 0.0
        self._min_submit_interval = 60.0 / max(1, AGENT_PLAN_VIDEO_RPM)

    def _throttle_submit(self) -> None:
        """按 AGENT_PLAN_VIDEO_RPM 摊平提交间隔（与并发信号量互补）."""
        with self._submit_lock:
            wait = self._min_submit_interval - (time.time() - self._last_submit_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_submit_ts = time.time()

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {AGENT_PLAN_API_KEY}",
            "Content-Type": "application/json",
        }

    # =======================================================================
    # 创建任务（旧接口：委托同一 prepare + **一次** POST，不再内置 POST 重试循环）
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        prepared = self.prepare_request(req)
        return self.create_prepared_task(prepared)


    # =======================================================================
    # V 包（计划 §V1/§V2）：prepare（**无提交**）+ prepared 提交（**一次** POST）
    # =======================================================================
    def _mode_of(self, req: VideoGenerationRequest) -> str:
        if req.reference_paths:
            return "r2v"
        if req.image_path or req.last_frame_image_path or req.extra_keyframe_paths:
            return "image"
        return "text"

    def _credential_profile_ref(self) -> Optional[str]:
        """配置侧**非敏感**凭据身份（不截取/不散列密钥）。无配置 ⇒ None。"""
        profile_id = os.getenv("VIDEO_CREDENTIAL_PROFILE_ID", "").strip()
        revision = os.getenv("VIDEO_CREDENTIAL_REVISION", "").strip()
        if not profile_id:
            return None
        return f"{profile_id}@{revision or 'r0'}"

    def get_task_binding(self, task_id: str) -> dict:
        ref = self._credential_profile_ref()
        if ref is None:
            return {"status": "binding_unavailable", "provider": "agent_plan",
                    "key_slot": None, "credential_profile_ref": None, "binding_version": 1}
        return {"status": "ok", "provider": "agent_plan", "key_slot": 0,
                "credential_profile_ref": ref, "binding_version": 1}

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        if not isinstance(binding, dict) or binding.get("status") != "ok":
            return False
        ref = self._credential_profile_ref()
        if ref is None or binding.get("credential_profile_ref") != ref:
            return False
        return True

    def prepare_request(self, req, context=None) -> PreparedVideoRequest:
        """组装 provider **实际生效**的请求事实：不提交、不上传、不写正式产物。"""
        from .models import MaterialRef, VideoCapabilityError, new_request_id
        from shared.enhancement_io import sha256_file

        model = self._model
        prompt, modifications = self._effective_prompt(req, model)
        items, content_mods = self._plan_content(req, model)
        # 文本项**始终**是 content 的第一项（官方协议：text 在前、媒体在后）
        items.insert(0, {"type": "text", "text": prompt})
        modifications.extend(content_mods)
        parameters = self._build_parameters(req, model)

        materials: list[MaterialRef] = []
        for item in items:
            if item.get("type") == "text":
                continue
            paths = ([item["source_path"]] if item.get("source_path")
                     else list(item.get("compose") or []))
            for src in paths:
                p = Path(src)
                if not p.is_file():
                    raise VideoCapabilityError("material_missing",
                                               f"素材不存在，禁止提交: {p}",
                                               field_path=f"content[{item.get('role')}]")
                materials.append(MaterialRef(
                    role=str(item.get("role") or ""),
                    kind=str(item.get("kind") or "image"),
                    source_path=str(p), sha256=sha256_file(p),
                    bytes=p.stat().st_size, ref_id=f"r{len(materials) + 1}"))

        prepared = PreparedVideoRequest(
            request_id=new_request_id("agentplan"),
            provider="agent_plan", model_id=model, mode=self._mode_of(req),
            prompt=prompt, parameters=parameters, materials=materials,
            modifications=modifications,
            credential_profile_ref=self._credential_profile_ref(),
            key_slot=0, binding_version=1,
            capability_revision=_capability_revision(model))
        prepared.content_plan = items
        prepared.source = {"shot_index": req.clip_index}
        prepared.compute_content_key()
        return prepared

    def _effective_prompt(self, req: VideoGenerationRequest, model: str) -> tuple:
        """实际会发出去的文本 + 派生版本记录（§V1：压缩发生在 prepare 内）。"""
        from shared.model_caps import _model_prompt_cap
        cap_len = _model_prompt_cap(model)
        text = str(req.prompt or "")
        modifications: list[dict] = []
        if cap_len and len(text) > cap_len - 40:
            from .volcengine_provider import _seedance_compress_prompt
            ref_tags = re.findall(r"@\S+", text)
            ref_len = sum(len(t) + 1 for t in ref_tags)
            target_body = max(200, cap_len - ref_len - 20)
            compressed = _seedance_compress_prompt(text, max_chars=target_body,
                                                   ref_tags=ref_tags)
            if len(compressed) < len(text):
                modifications.append({
                    "kind": "prompt_compress", "from_chars": len(text),
                    "to_chars": len(compressed), "ref_tags": len(ref_tags),
                    "target_body": target_body, "model_cap": cap_len})
                text = compressed
        if cap_len and len(text) > cap_len:
            modifications.append({"kind": "prompt_hard_truncate", "from_chars": len(text),
                                  "to_chars": cap_len, "model_cap": cap_len})
            text = text[:cap_len]
        if req.camera_movement and "运镜：" not in text[:20]:
            text = f"运镜：{req.camera_movement}\n{text}"
            modifications.append({"kind": "camera_prefix",
                                  "camera_movement": req.camera_movement})
        return text, modifications

    def _plan_content(self, req: VideoGenerationRequest, model: str) -> tuple:
        """有序素材计划 + 显式修改记录（与 volcengine 同款 role 规则）。"""
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        items: list[dict] = []
        modifications: list[dict] = []

        def _image(path, role):
            items.append({"type": "image", "kind": "image", "role": role,
                          "source_path": str(path)})

        if req.reference_paths:
            if not cap.supports_reference_image:
                raise VideoCapabilityError(
                    "role_unsupported",
                    f"{model} 不支持参考生视频（role=reference_image）。"
                    f"r2v 模式请将 AGENT_PLAN_T2V_MODEL 设为 2.0/2.5 系列模型",
                    field_path="references[role=reference]")
            from shared.ref_compose import get_ref_limit
            ref_limit = get_ref_limit(model)
            ref_paths = [p for p in (req.reference_paths or []) if p]
            missing = [p for p in ref_paths if not Path(p).is_file()]
            if missing:
                raise VideoCapabilityError("material_missing",
                                           f"参考素材不存在: {missing}", field_path="references")
            if len(ref_paths) > ref_limit:
                modifications.append({
                    "kind": "reference_compose", "reason": "reference_capacity",
                    "from": len(ref_paths), "to": ref_limit, "model": model,
                    "sources": [Path(p).name for p in ref_paths]})
                items.append({"type": "image", "kind": "image", "role": "reference_image",
                              "compose": ref_paths, "limit": ref_limit,
                              "tag": f"r2v{req.clip_index}"})
                _verbose_write(
                    f"  [镜头{req.clip_index}] Agent Plan 参考图 {len(ref_paths)} 张超上限 "
                    f"{ref_limit}，无损压缩为 {ref_limit} 张")
            else:
                for ref_path in ref_paths:
                    _image(ref_path, "reference_image")
            return items, modifications

        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).is_file()]
        if kfs:
            if cap.supports_reference_image:
                for p in kfs:
                    _image(p, "reference_image")
                return items, modifications
            if len(kfs) > 2:
                dropped = len(kfs) - 2
                modifications.append({"kind": "keyframe_degrade", "reason": "model_unsupported",
                                      "dropped": dropped, "kept": 2, "model": model})
                _verbose_write(
                    f"  [镜头{req.clip_index}] {model} 不支持参考生视频，"
                    f"多关键帧退化为首尾帧（丢弃中间 {dropped} 张）")
                kfs = [kfs[0], kfs[-1]]

        if kfs:
            _image(kfs[0], "first_frame")
            if len(kfs) > 1:
                _image(kfs[-1], "last_frame")
        else:
            if req.image_path:
                _image(req.image_path, "first_frame")
            if req.last_frame_image_path:
                _image(req.last_frame_image_path, "last_frame")
        return items, modifications

    def _build_parameters(self, req: VideoGenerationRequest, model: str) -> dict:
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        is_first_last_frames = bool(req.image_path or req.last_frame_image_path) \
            and not req.reference_paths and not req.extra_keyframe_paths
        parameters: dict = {
            "resolution": resolve_resolution(req.size, cap, AGENT_PLAN_VIDEO_RESOLUTION),
            "ratio": _resolve_ratio(req, cap, is_first_last_frames),
            "duration": clamp_duration(AGENT_PLAN_VIDEO_DURATION or req.duration, cap),
            "watermark": False,
        }
        if cap.supports_generate_audio:
            parameters["generate_audio"] = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
        # 2026-09-12（移植官方 byted-ark-seedance-skill）：draft 样片 / flex 服务档——
        # 仅能力开启的模型透传（1.5 pro 支持，2.5 官方明确不支持）；默认关（不传字段）
        if getattr(cap, "supports_draft", False) \
                and os.getenv("AGENT_PLAN_VIDEO_DRAFT", "").strip().lower() in ("1", "true"):
            parameters["draft"] = True
        _service_tier = os.getenv("AGENT_PLAN_VIDEO_SERVICE_TIER", "").strip().lower()
        if getattr(cap, "supports_flex_tier", False) and _service_tier:
            parameters["service_tier"] = _service_tier
        if req.seed is not None and getattr(cap, "supports_seed", False):
            parameters["seed"] = req.seed
        return parameters

    def _materialize_content(self, prepared: PreparedVideoRequest, context=None) -> list:
        """把冻结事实变成请求体内容：**校验素材摘要**后再编码（变了 ⇒ 拒收、零 POST）。"""
        from shared.enhancement_io import sha256_file
        from shared.ref_compose import fit_flat

        for m in prepared.materials:
            if not m.source_path:
                continue
            p = Path(m.source_path)
            if not p.is_file():
                raise VideoCapabilityError("material_missing",
                                           f"素材已不存在: {p}", field_path="materials")
            if m.sha256 and sha256_file(p) != m.sha256:
                raise VideoCapabilityError(
                    "material_changed",
                    f"素材字节在 prepare 之后发生变化，拒绝提交（同一 request_key 不得换素材）: {p}",
                    field_path="materials")

        content: list = []
        for item in (prepared.content_plan or []):
            if item.get("type") == "text":
                content.append({"type": "text", "text": item.get("text", "")})
                continue
            if item.get("compose"):
                sheet_dir = _compose_dir(prepared, context)
                composed = fit_flat(list(item["compose"]), int(item["limit"]), sheet_dir,
                                    str(item.get("tag") or "r2v"))
                for path in composed:
                    url = _encode_media(path)
                    if not url:
                        raise VideoCapabilityError("material_unreadable",
                                                   f"参考表编码失败: {path}")
                    content.append({"type": "image_url", "image_url": {"url": url},
                                    "role": item.get("role")})
                continue
            url = _encode_media(item.get("source_path"))
            if not url:
                raise VideoCapabilityError("material_unreadable",
                                           f"素材编码失败: {item.get('source_path')}")
            content.append({"type": "image_url", "image_url": {"url": url},
                            "role": item.get("role")})
        return content

    def create_prepared_task(self, prepared: PreparedVideoRequest, context=None) -> str:
        """materialize + **一次** POST（网络断开/5xx 返回 unknown，不自行重发）。"""
        from .models import SUBMIT_UNKNOWN, VideoSubmissionError
        from .service import classify_post_response

        if not prepared.is_ready():
            raise VideoCapabilityError(
                "unsupported", prepared.unsupported_reason or "prepared 请求不可提交")

        payload: dict = {"model": prepared.model_id,
                         "content": self._materialize_content(prepared, context)}
        payload.update(prepared.parameters or {})
        self._log_submit(prepared, payload)

        with self._semaphore:
            self._throttle_submit()
            try:
                resp = requests.post(
                    f"{self._base_url}/contents/generations/tasks",
                    json=payload,
                    headers=self._headers,
                    timeout=180,
                )
            except requests.exceptions.RequestException as e:
                raise VideoSubmissionError(
                    SUBMIT_UNKNOWN,
                    f"提交网络异常（不知是否已建任务，禁止自行重发）: {e}") from e

        _verbose_write(
            f"[DEBUG] agent_plan_video_submit response: status={resp.status_code}, "
            f"body={resp.text[:500]}")
        task_id = classify_post_response(resp, provider="agent_plan")
        _verbose_write(
            f"  [镜头{prepared.parameters.get('shot_index', '')}] "
            f"任务已创建 [Agent Plan] -> task_id: {task_id}")
        return task_id

    def _log_submit(self, prepared: PreparedVideoRequest, payload: dict) -> None:
        idx = (prepared.source or {}).get("shot_index", "")
        _verbose_write(f"  [镜头{idx}] [Agent Plan Seedance] 正在提交任务 [{prepared.model_id}]...")
        _verbose_write(
            f"[DEBUG] agent_plan_video_submit: model={prepared.model_id}, "
            f"resolution={payload.get('resolution')}, ratio={payload.get('ratio')}, "
            f"duration={payload.get('duration')}, seed={payload.get('seed')}, "
            f"content_key={prepared.content_key[:16]}")
        _verbose_write(
            f"[DEBUG] agent_plan_video_submit: prompt长度={len(prepared.prompt)}, "
            f"前200字={prepared.prompt[:200]}")
        for i, item in enumerate(payload.get("content") or []):
            if item["type"] == "text":
                _verbose_write(
                    f"[DEBUG] agent_plan_video_submit: content[{i}] text len={len(item['text'])}")
            else:
                u = item.get("image_url") or {}
                raw = str(u.get("url", ""))
                _verbose_write(
                    f"[DEBUG] agent_plan_video_submit: content[{i}] {item['type']} "
                    f"role={item.get('role')}, len={len(raw)}")

    def query_observation(self, task_id: str) -> TaskObservation:
        """规范化状态与结果引用（不把日志当返回协议）。"""
        try:
            raw = self.query_task(task_id)
        except Exception as exc:  # noqa: BLE001
            return TaskObservation(task_id=str(task_id), error=str(exc)[:300])
        return TaskObservation.from_raw(task_id, raw)


    # =======================================================================
    # 查询任务
    # =======================================================================
    def query_task(self, task_id: str) -> dict:
        resp = requests.get(
            f"{self._base_url}/contents/generations/tasks/{task_id}",
            headers=self._headers,
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Agent Plan Seedance 查询失败 [{resp.status_code}]: {resp.text[:300]}"
            )
        return resp.json()

    # =======================================================================
    # 轮询结果
    # =======================================================================
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str, timeout: int | None = None
    ) -> VideoGenerationResult:
        with self._semaphore:
            return self._wait_for_completion_impl(req, task_id, timeout)

    def _wait_for_completion_impl(
        self, req: VideoGenerationRequest, task_id: str, timeout: int | None
    ) -> VideoGenerationResult:
        start_time = time.time()
        last_error = ""
        if timeout is None:
            timeout = AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS * AGENT_PLAN_VIDEO_POLL_INTERVAL

        attempts = 0
        while attempts < AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS and time.time() - start_time < timeout:
            attempts += 1
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                last_error = str(e)
                # 2026-09-13 审计修复：4xx（鉴权失效/参数错误/任务不存在）是终态，
                # 继续轮询只会把轮询预算白耗完——fail-fast 返回失败
                if any(f"[{c}]" in last_error for c in (400, 401, 403, 404)):
                    _verbose_write(
                        f"  [镜头{req.clip_index}] [!] Agent Plan 查询终态错误，"
                        f"停止轮询: {last_error[:200]}"
                    )
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"查询终态失败: {last_error}",
                    )
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] Agent Plan 查询失败: {last_error[:200]}"
                )
                time.sleep(AGENT_PLAN_VIDEO_POLL_INTERVAL)
                continue

            obs = TaskObservation.from_raw(task_id, output)
            status = obs.status
            _verbose_write(
                f"  [镜头{req.clip_index}] Agent Plan 任务状态: {obs.raw_status} "
                f"(已等待 {int(time.time() - start_time)}s)"
            )

            if obs.is_success():
                video_url = (obs.result or {}).get("video_url")
                if not video_url:
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"任务成功但未找到 video_url: {str(output)[:300]}",
                    )
                if obs.usage:
                    _verbose_write(f"  [镜头{req.clip_index}] 计费信息: {obs.usage}")
                # TOS url 短期有效 → 立即下载落盘
                return self._download(req, video_url)

            if obs.status in (OBS_FAILED, OBS_EXPIRED):
                msg = obs.error or obs.raw_status
                _verbose_write(f"  [镜头{req.clip_index}] Agent Plan 任务失败: {obs.raw_status} - {msg}")
                # 2026-09-12 用户要求：失败时日志打印完整报错（不截断，便于排障）
                _verbose_write(f"[DEBUG] [镜头{req.clip_index}] Agent Plan 任务失败完整响应: {output}")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"生成失败: {msg}",
                )

            # queued / running
            time.sleep(AGENT_PLAN_VIDEO_POLL_INTERVAL)

        return VideoGenerationResult(
            clip_index=req.clip_index,
            success=False,
            error=f"轮询超时 ({timeout}s): {last_error}",
        )

    # =======================================================================
    # 下载
    # =======================================================================
    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        """下载结果：候选上下文 ⇒ attempt 内；否则正式 ``shots/<idx>/video.mp4``。"""
        from shared.paths import shot_video_file

        from .service import http_download_video, resolve_download_target

        target = resolve_download_target(req, shot_video_file(req.clip_index))
        _verbose_write(f"  [镜头{req.clip_index}] Agent Plan 下载视频: {video_url} -> {target}")
        result = http_download_video(video_url, target, clip_index=req.clip_index,
                                     provider="agent_plan")
        if result.success:
            _verbose_write(f"  [镜头{req.clip_index}] Agent Plan 视频已保存 -> {result.save_path}")
        return result
