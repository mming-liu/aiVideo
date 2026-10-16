"""火山引擎方舟 (Volcengine Ark) 视频生成 — Seedance 系列.

异步任务协议:
  POST {base}/contents/generations/tasks        # 创建任务 -> {id}
  GET  {base}/contents/generations/tasks/{id}   # 轮询 -> status / content.video_url

Auth: Authorization: Bearer {VOLCENGINE_API_KEY}

支持:
- T2V (文生视频): content 仅 text
- I2V (图生视频): content 含 first_frame / last_frame 图片
- 多关键帧/参考图: role=reference_image（首帧/尾帧优先占用 first_frame/last_frame）
- R2V 参考音频: role=reference_audio

官方文档: 火山方舟 视频生成 API
https://www.volcengine.com/docs/82379/1520758
"""

import base64
import mimetypes
import os
import re
import time
from functools import partial
from pathlib import Path
from typing import Optional

import requests

from .models import (
    OBS_EXPIRED,
    OBS_FAILED,
    MaterialRef,
    PreparedVideoRequest,
    TaskObservation,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from .service import VideoGenerationService
from .config import config
from shared.model_caps import (
    _model_prompt_cap,
    clamp_duration,
    get_seedance_cap,
    resolve_resolution,
)
from shared.config import (
    VOLCENGINE_API_KEY,
    VOLCENGINE_BASE_URL,
    VOLCENGINE_T2V_MODEL,
    VOLCENGINE_T2V_FALLBACK_MODELS,
    VOLCENGINE_I2V_MODEL,
    VOLCENGINE_I2V_FALLBACK_MODELS,
    VOLCENGINE_VIDEO_POLL_INTERVAL,
    VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS,
    VOLCENGINE_VIDEO_RESOLUTION,
    VOLCENGINE_VIDEO_RATIO,
    VOLCENGINE_VIDEO_DURATION,
)
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02b.log")


def _capability_revision(model: str) -> str:
    """能力注册表条目的指纹（进 prepared/content key；能力变化 ⇒ 请求失效）。"""
    import dataclasses
    import hashlib
    import json
    cap = get_seedance_cap(model)
    facts = {
        "model": model,
        "cap": dataclasses.asdict(cap) if dataclasses.is_dataclass(cap) else vars(cap),
        "prompt_cap": _model_prompt_cap(model),
    }
    blob = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    return "caps-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _append_audio(req: VideoGenerationRequest, content: list[dict], model: str) -> None:
    """追加参考音频（role=reference_audio）到 content —— **旧接口，保留兼容**。

    新路径（``_plan_content`` + ``_materialize_content``）在计划阶段决定是否携带
    参考音频（含能力门控与显式 modification 记录），本函数只为外部/历史调用保留。
    """
    if not req.reference_audio:
        return
    from shared.model_caps import get_seedance_cap
    cap = get_seedance_cap(model)
    if not getattr(cap, "supports_reference_audio", False):
        _verbose_write(f"  [镜头{req.clip_index}] [WARN] {model} 不支持 reference_audio，"
              f"已忽略参考音频参数")
        return
    url = _encode_media(req.reference_audio)
    if url:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": url},
            "role": "reference_audio",
        })


# 权重等级（数字越小越优先保留）
_SENT_WEIGHT_HIGH = 0   # 高权重：ff/motion/lf 三段、角色锚定、空间物体、硬约束
_SENT_WEIGHT_MID = 1    # 中等：场景、人物站位、人物一致性
_SENT_WEIGHT_LOW = 2    # 低权重：色彩氛围、视觉衔接、空镜约束、风格收束

# 关键标签：识别高/低权重段
_HIGH_KEYWORDS = (
    "【视频起点】", "【视频中段】", "【视频终点】", "【角色锚定】", "【空间物体】",
    "【道具锚定】", "【人物站位清单】", "【场景环境】", "保持", "必须", "禁止", "严禁",
    "锁定五官", "不能", "不得",
)
_LOW_KEYWORDS = (
    "【色彩氛围】", "视觉衔接", "画面无任何字幕", "整段视频每一帧都保持",
    "禁止在画面中叠加", "禁止任何人物", "连续接续", "风格收束",
)


def _classify_sentence_weight(sent: str) -> int:
    """给单句打权重：基于关键词+标签+位置.

    优先级 0=高（必保留）/ 1=中（尽量保留）/ 2=低（超长时优先砍）
    """
    s = sent.strip()
    if not s:
        return _SENT_WEIGHT_LOW
    # 高权重关键词
    if any(k in s for k in _HIGH_KEYWORDS):
        return _SENT_WEIGHT_HIGH
    # 低权重关键词
    if any(k in s for k in _LOW_KEYWORDS):
        return _SENT_WEIGHT_LOW
    return _SENT_WEIGHT_MID


def _seedance_compress_prompt(full_prompt: str, max_chars: int,
                              ref_tags: list[str] | None = None) -> str:
    """Seedance prompt 智能压缩（避免静默截断丢关键约束）.

    关键事实（2026-08 火山方舟官方）：Seedance 视频接口硬约束中文有效 prompt
    上限 ≈ 500 字符（注册表 _model_prompt_cap），**超长部分静默截断不报错**；
    @图片引用标记不计入字符。
    粗暴 req.prompt[:500] 尾部截断会砍掉 prompt 末尾的关键约束（"动作连贯
    性/风格收束/视觉衔接/r2v 参考图清单"），导致角色漂移、抽卡暴涨等 bug。

    本函数做"语义优先"压缩：
    1. 剥离 @图N / @图片1 等引用标记（不计入 max_chars）
    2. 按中文标点（。！？；\n）切分句子
    3. 给每句打权重（高=必保留/中=尽量保留/低=可砍）
    4. 按原顺序累积到 max_chars 停止（不砍半句）
    5. ref_tags 拼在最前面（视频模型先看到参考图引用）

    Args:
        full_prompt: 完整 prompt 字符串
        max_chars: 正文最大字符数（不含 ref_tags；由调用方按 cap 动态推导）
        ref_tags: 已抽取的 @引用 标记列表；None 时自动从 full_prompt 抽
    Returns:
        压缩后的 prompt（ref_tags 在前 + 保留句子按原顺序）
    """
    if not full_prompt:
        return full_prompt

    # 1. 剥离 @图N / @图片1 等引用标记
    if ref_tags is None:
        # 匹配 @ 后跟非空白的连续字符（支持中文、空格前截止）
        ref_tags = re.findall(r"@\S+", full_prompt)
    content = re.sub(r"@\S+", "", full_prompt)
    content = content.strip()
    if not content:
        # 全部是 @引用，直接返回
        return " ".join(ref_tags)

    # 2. 按中文标点切分（保留分隔符，句级粒度不砍半句）
    # 正则：句末标点（。！？；）+ 换行/段分隔
    parts = re.split(r"(?<=[。！？；\n])\s*", content)
    sentences = [p.strip() for p in parts if p.strip()]

    # 3. 给每句打权重
    weighted = [(i, _classify_sentence_weight(s), s) for i, s in enumerate(sentences)]

    # 4. 两阶段累积：先保证所有高权重段必入选（无论 max_chars 多紧），
    # 再按原顺序填充中等权重段，最后用剩余空间装低权重段。
    # 原算法按 weight+orig_idx 排序后累积，会让低权重（原 idx 小）先填满、
    # 高权重（原 idx 大）因剩余空间不够被砍——修复后高权重强制保留。
    high_idx = [i for i, w, _ in weighted if w == _SENT_WEIGHT_HIGH]
    mid_idx = [i for i, w, _ in weighted if w == _SENT_WEIGHT_MID]
    low_idx = [i for i, w, _ in weighted if w == _SENT_WEIGHT_LOW]
    # 5. 累积字符到 max_chars：按"高权重全选 → 中权重按原顺序填剩余 → 低权重收尾"
    # 分两阶段：第一阶段高+中（按原顺序累积），超出 max_chars 的中权重段跳过
    selected: list[int] = []
    total = 0
    for i in high_idx + mid_idx:
        slen = len(sentences[i])
        if total + slen > max_chars:
            continue
        selected.append(i)
        total += slen
    # 第二阶段：剩余空间装低权重（按原顺序）
    for i in low_idx:
        slen = len(sentences[i])
        if total + slen > max_chars:
            continue
        selected.append(i)
        total += slen

    # 6. 按原顺序输出保留的句子
    selected_sorted = sorted(set(selected))
    kept = [sentences[i] for i in selected_sorted]
    body = "".join(kept)

    # 7. ref_tags 拼在最前面（不计入 max_chars）
    if ref_tags:
        return " ".join(ref_tags) + " " + body
    return body


# 任务状态归一化单源：``_video_gen.models.TaskObservation``（官方 status 可选值
# queued/running/cancelled/succeeded/failed/expired；expired=服务端自动终止，
# 也是终态失败）。两个 Seedance provider 共用同一份判据，不再各自维护元组。
# 2026-10-03（§V2 改动 6）：**提交**路径的自动重试已移除——prepared 入口每次至多
# 一个生成 POST，网络断开/5xx 返回 submission_unknown 交上层记录待恢复；
# 403 换模型由 _submit_over_queue 产生**新的 prepared 候选**（新 content_key）。
# 旧配置 VOLCENGINE_VIDEO_SUBMIT_RETRIES 仍可解析，但不再驱动自动重发。
# 轮询查询的瞬态错误容忍次数（查询接口 5xx/网络异常连续发生 N 次才判失败，
# 不再每次计入 attempts；与 VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS 配合使用）
_POLL_TRANSIENT_TOLERANCE = 5


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
# （clamp_duration / resolve_resolution，volcengine 与 agent_plan 共用单实现）。


def _resolve_ratio(req: VideoGenerationRequest, cap, is_first_last_frames: bool = False) -> str:
    """按模型能力推导 ratio.

    Seedance 2.5 在首帧/首尾帧场景仅支持 adaptive（输出自动保持首帧宽高比），
    此为硬约束、不可被 override；其余模型/场景：VOLCENGINE_VIDEO_RATIO 非空则强制，
    否则按 req.size（如 720x1280）推导 1:1 / 16:9 / 9:16。
    """
    if cap.i2v_frames_ratio_adaptive and is_first_last_frames:
        return "adaptive"
    override = (VOLCENGINE_VIDEO_RATIO or "").strip().lower()
    if override in ("16:9", "9:16", "1:1", "adaptive"):
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


class VolcengineVideoService(VideoGenerationService):
    """火山方舟 Seedance 视频生成服务.

    支持:
    - T2V (text-to-video)
    - I2V (image-to-video, first_frame / first_frame + last_frame)
    - 多关键帧/参考图 (extra_keyframe_paths / reference_paths -> reference_image)
    - 参考音频 (reference_audio -> reference_audio)

    403 自动回退: T2V / I2V 各自模型队列 + 回退列表.
    """

    def __init__(self) -> None:
        config.validate()
        if not VOLCENGINE_API_KEY:
            raise ValueError(
                "没有配置 VOLCENGINE_API_KEY（.env.keys 中设置，兼容 ARK_API_KEY）"
            )
        self._t2v_model_queue: list[str] = [VOLCENGINE_T2V_MODEL] + VOLCENGINE_T2V_FALLBACK_MODELS
        self._i2v_model_queue: list[str] = [VOLCENGINE_I2V_MODEL] + VOLCENGINE_I2V_FALLBACK_MODELS
        self._base_url = VOLCENGINE_BASE_URL.rstrip("/")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {VOLCENGINE_API_KEY}",
            "Content-Type": "application/json",
        }

    # =======================================================================
    # V 包（计划 §V1/§V2）：prepare（**无提交**）+ prepared 提交（**一次** POST）
    #
    # 旧 ``create_task`` 保留兼容，但它只是"prepare → create_prepared_task"的包装：
    # 同一份正文/参数/参考事实只组装一次（避免不同入口不同正文），且**每次**
    # prepared 入口至多一个生成 POST——网络断开/5xx 不再自行重发。
    # =======================================================================
    def _resolve_prepare_model(self, req: VideoGenerationRequest) -> str:
        """prepare 默认模型：有参考图/帧走 I2V 队列头，否则 T2V 队列头。"""
        has_refs = bool(req.reference_paths or req.extra_keyframe_paths)
        has_frames = bool(req.image_path or req.last_frame_image_path)
        queue = self._i2v_model_queue if (has_refs or has_frames) else self._t2v_model_queue
        return queue[0]

    def prepare_request(self, req, context=None) -> PreparedVideoRequest:
        """组装 provider **实际生效**的请求事实：不提交、不上传、不写正式产物。

        压缩/合规改写在这里产生**派生版本**并记入 ``modifications``（不能等到 submit
        之后才知道文本变了）；参考容量/关键帧降级同样写显式修改记录，不静默丢弃。
        """
        return self._prepare_for_model(req, self._resolve_prepare_model(req), context)

    def _mode_of(self, req: VideoGenerationRequest) -> str:
        if req.reference_paths:
            return "r2v"
        if req.image_path or req.last_frame_image_path or req.extra_keyframe_paths:
            return "image"
        return "text"

    def _prepare_for_model(self, req: VideoGenerationRequest, model: str,
                           context=None) -> PreparedVideoRequest:
        from .models import MaterialRef, PreparedVideoRequest, new_request_id
        from shared.enhancement_io import sha256_file

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
                    raise VideoCapabilityError(
                        "material_missing", f"素材不存在，禁止提交: {p}",
                        field_path=f"content[{item.get('role')}]")
                materials.append(MaterialRef(
                    role=str(item.get("role") or ""),
                    kind=str(item.get("kind") or "image"),
                    source_path=str(p), sha256=sha256_file(p),
                    bytes=p.stat().st_size, ref_id=f"r{len(materials) + 1}"))

        prepared = PreparedVideoRequest(
            request_id=new_request_id("volc"),
            provider="volcengine", model_id=model, mode=self._mode_of(req),
            prompt=prompt, parameters=parameters, materials=materials,
            modifications=modifications,
            credential_profile_ref=self._credential_profile_ref(),
            key_slot=0, binding_version=1,
            capability_revision=_capability_revision(model))
        prepared.content_plan = items
        prepared.source = {"shot_index": req.clip_index}
        prepared.compute_content_key()
        return prepared

    def _effective_prompt(self, req: VideoGenerationRequest, model: str,
                          context=None) -> tuple:
        """实际会发出去的文本 + 派生版本记录（Seedance 中文有效上限 ≈500 字符）。"""
        cap_len = _model_prompt_cap(model)
        text = str(req.prompt or "")
        modifications: list[dict] = []
        if cap_len and len(text) > cap_len - 40:
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
        """有序素材计划（**不**编码、**不**上传）+ 显式修改记录。

        官方文档规定 3 种场景互斥、不可混用（创建视频生成任务 API）：
        - 全模态参考生视频：role=reference_image（仅 Seedance 2.5 / 2.0 系列支持）
        - 图生视频-多关键帧：能力支持时全部 reference_image，否则退化为首尾两帧
        - 图生视频-首帧/首尾帧：role=first_frame / last_frame（所有 Seedance 支持）
        """
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        items: list[dict] = []
        modifications: list[dict] = []

        def _image(path, role):
            items.append({"type": "image", "kind": "image", "role": role,
                          "source_path": str(path)})

        def _audio_item():
            if not req.reference_audio:
                return
            if not getattr(cap, "supports_reference_audio", False):
                modifications.append({"kind": "param_dropped", "field": "reference_audio",
                                      "reason": f"{model} 不支持 reference_audio"})
                _verbose_write(f"  [镜头{req.clip_index}] [WARN] {model} 不支持 reference_audio，"
                               f"已忽略参考音频参数")
                return
            items.append({"type": "audio", "kind": "audio", "role": "reference_audio",
                          "source_path": str(req.reference_audio)})

        # ---- 全模态参考生视频（r2v）：reference_paths → reference_image ----
        if req.reference_paths:
            if not cap.supports_reference_image:
                raise VideoCapabilityError(
                    "role_unsupported",
                    f"{model} 不支持参考生视频（role=reference_image，仅 Seedance 2.5/2.0 系列）。"
                    f"r2v 模式请将 VOLCENGINE_I2V_MODEL 设为 2.0/2.5 模型",
                    field_path="references[role=reference]")
            from shared.ref_compose import get_ref_limit
            ref_limit = get_ref_limit(model)
            ref_paths = [p for p in (req.reference_paths or []) if p]
            missing = [p for p in ref_paths if not Path(p).is_file()]
            if missing:
                raise VideoCapabilityError(
                    "material_missing", f"参考素材不存在: {missing}",
                    field_path="references")
            if len(ref_paths) > ref_limit:
                modifications.append({
                    "kind": "reference_compose", "reason": "reference_capacity",
                    "from": len(ref_paths), "to": ref_limit, "model": model,
                    "sources": [Path(p).name for p in ref_paths]})
                items.append({"type": "image", "kind": "image", "role": "reference_image",
                              "compose": ref_paths, "limit": ref_limit,
                              "tag": f"r2v{req.clip_index}"})
                _verbose_write(f"  [镜头{req.clip_index}] 参考图 {len(ref_paths)} 张超上限 "
                               f"{ref_limit}，无损压缩为 {ref_limit} 张")
            else:
                for ref_path in ref_paths:
                    _image(ref_path, "reference_image")
            _audio_item()
            return items, modifications

        # ---- 多关键帧：能力支持→全部 reference_image（全模态）；否则退化为首尾帧 ----
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).is_file()]
        if kfs:
            if cap.supports_reference_image:
                for p in kfs:
                    _image(p, "reference_image")
                _audio_item()
                return items, modifications
            if len(kfs) > 2:
                dropped = len(kfs) - 2
                modifications.append({"kind": "keyframe_degrade", "reason": "model_unsupported",
                                      "dropped": dropped, "kept": 2, "model": model})
                _verbose_write(f"  [镜头{req.clip_index}] {model} 不支持参考生视频，"
                               f"多关键帧退化为首尾帧（丢弃中间 {dropped} 张）")
                kfs = [kfs[0], kfs[-1]]

        # ---- 首帧/首尾帧（所有 Seedance 模型支持）----
        if kfs:
            _image(kfs[0], "first_frame")
            if len(kfs) > 1:
                _image(kfs[-1], "last_frame")
        else:
            if req.image_path:
                _image(req.image_path, "first_frame")
            if req.last_frame_image_path:
                _image(req.last_frame_image_path, "last_frame")
        _audio_item()
        return items, modifications

    def _build_parameters(self, req: VideoGenerationRequest, model: str) -> dict:
        """实际下发的模型参数（进内容 key；不含 URL/凭据/输出路径）。"""
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        is_first_last_frames = bool(req.image_path or req.last_frame_image_path) \
            and not req.reference_paths and not req.extra_keyframe_paths
        parameters: dict = {
            "resolution": resolve_resolution(req.size, cap, VOLCENGINE_VIDEO_RESOLUTION),
            "ratio": _resolve_ratio(req, cap, is_first_last_frames),
            "duration": clamp_duration(VOLCENGINE_VIDEO_DURATION or req.duration, cap),
            "watermark": False,
        }
        if cap.supports_generate_audio:
            parameters["generate_audio"] = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
        if req.seed is not None and getattr(cap, "supports_seed", False):
            parameters["seed"] = req.seed
        return parameters

    def _compose_dir(self, prepared, context) -> Path:
        """参考表落盘目录：候选上下文 ⇒ attempt 内（**绝不**写正式 shots/）。"""
        attempt = getattr(context, "attempt_dir", None) or getattr(context, "experiment_root", None)
        if attempt:
            d = Path(attempt) / "assets" / "ref_sheets"
        else:
            from shared.paths import shot_work_dir
            d = shot_work_dir(int((prepared.source or {}).get("shot_index") or 0))
        d.mkdir(parents=True, exist_ok=True)
        return d

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
                sheet_dir = self._compose_dir(prepared, context)
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
            if item.get("type") == "audio":
                content.append({"type": "audio_url", "audio_url": {"url": url},
                                "role": item.get("role")})
            else:
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

        _verbose_write(f"[DEBUG] video_submit response: status={resp.status_code}, "
                       f"body={resp.text[:500]}")
        task_id = classify_post_response(resp, provider="volcengine")
        _verbose_write(f"  [镜头{prepared.parameters.get('clip_index', '')}] "
                       f"任务已创建 [Seedance] -> task_id: {task_id}")
        return task_id

    def _log_submit(self, prepared: PreparedVideoRequest, payload: dict) -> None:
        """脱敏语义 payload 与内容摘要（不打印 body 敏感字段）。"""
        idx = prepared.parameters.get("clip_index", "")
        _verbose_write(f"  [镜头{idx}] [Seedance] 正在提交任务 [{prepared.model_id}]...")
        _verbose_write(f"[DEBUG] video_submit: model={prepared.model_id}, "
                       f"resolution={payload.get('resolution')}, ratio={payload.get('ratio')}, "
                       f"duration={payload.get('duration')}, seed={payload.get('seed')}, "
                       f"content_key={prepared.content_key[:16]}")
        for i, item in enumerate(payload.get("content") or []):
            if item["type"] == "text":
                _verbose_write(f"[DEBUG] video_submit: content[{i}] text len={len(item['text'])}")
            else:
                u = item.get("image_url") or item.get("audio_url") or {}
                raw = str(u.get("url", ""))
                _verbose_write(f"[DEBUG] video_submit: content[{i}] {item['type']} "
                               f"role={item.get('role')}, len={len(raw)}")

    def _submit_over_queue(self, req: VideoGenerationRequest, queue: list,
                           *, allow_r2v: bool) -> str:
        """旧生产路径：模型队列 + 403 回退。**每个模型都是一个新的 prepared 候选**
        （新 content_key），且每个 prepared 入口至多一个 POST（§V2 改动 6）。"""
        from shared.model_caps import get_seedance_cap
        from .models import VideoSubmissionError

        last_error = ""
        for model in queue:
            if allow_r2v and not get_seedance_cap(model).supports_reference_image:
                last_error = f"{model} 不支持参考生视频（仅 Seedance 2.5/2.0 系列）"
                _verbose_write(f"  [镜头{req.clip_index}] [!] {model} 不支持 r2v 参考图，尝试下一个")
                continue
            prepared = self._prepare_for_model(req, model)
            try:
                return self.create_prepared_task(prepared)
            except VideoSubmissionError as exc:
                if exc.http_status == 403:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] 模型 {model} 返回 403，尝试下一个")
                    last_error = str(exc)
                    continue
                raise
        if allow_r2v and not any(get_seedance_cap(m).supports_reference_image
                                 for m in queue):
            _verbose_write(
                f"  [镜头{req.clip_index}] [!] 所有 I2V 模型均不支持 r2v 参考生视频，"
                f"自动降级为 text-to-video（丢弃 reference_paths）")
            req_text = VideoGenerationRequest(
                prompt=req.prompt, clip_index=req.clip_index, duration=req.duration,
                negative_prompt=req.negative_prompt, size=req.size,
                image_path=None, last_frame_image_path=None,
                extra_keyframe_paths=[], reference_paths=[], reference_audio=None,
                seed=req.seed,
            )
            return self._submit_over_queue(req_text, self._t2v_model_queue, allow_r2v=False)
        raise RuntimeError(f"任务创建失败: 所有模型均失败 ({last_error})")

    def _credential_profile_ref(self) -> Optional[str]:
        """配置侧**非敏感**凭据身份（不截取/不散列密钥）。无配置 ⇒ None。"""
        profile_id = os.getenv("VIDEO_CREDENTIAL_PROFILE_ID", "").strip()
        revision = os.getenv("VIDEO_CREDENTIAL_REVISION", "").strip()
        if not profile_id:
            return None
        return f"{profile_id}@{revision or 'r0'}"

    def get_task_binding(self, task_id: str) -> dict:
        """单 key 通道同样返回**显式 profile**；无显式配置 ⇒ binding_unavailable。"""
        ref = self._credential_profile_ref()
        if ref is None:
            return {"status": "binding_unavailable", "provider": "volcengine",
                    "key_slot": None, "credential_profile_ref": None, "binding_version": 1}
        return {"status": "ok", "provider": "volcengine", "key_slot": 0,
                "credential_profile_ref": ref, "binding_version": 1}

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        """恢复前校验配置引用：profile 不符/已移除 ⇒ False（credential_binding_unavailable）。"""
        if not isinstance(binding, dict) or binding.get("status") != "ok":
            return False
        ref = self._credential_profile_ref()
        if ref is None or binding.get("credential_profile_ref") != ref:
            return False
        return True

    # =======================================================================
    # 创建任务（旧接口：委托同一 prepare + 一次 POST；候选上下文下无回退链）
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        from .models import execution_context_from_request
        if execution_context_from_request(req) is not None:
            # 候选模式：一次人工确认 = 至多一个生成 POST；换模型须走新的 prepared 候选
            prepared = self.prepare_request(req)
            return self.create_prepared_task(prepared)
        has_refs = bool(req.reference_paths or req.extra_keyframe_paths)
        has_frames = bool(req.image_path or req.last_frame_image_path)
        if has_refs or has_frames:
            return self._submit_over_queue(req, self._i2v_model_queue,
                                           allow_r2v=bool(req.reference_paths))
        return self._submit_over_queue(req, self._t2v_model_queue, allow_r2v=False)

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
            raise RuntimeError(f"Seedance 查询失败 [{resp.status_code}]: {resp.text[:300]}")
        return resp.json()

    # =======================================================================
    # 轮询结果
    # =======================================================================
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str, timeout: int | None = None
    ) -> VideoGenerationResult:
        start_time = time.time()
        last_error = ""
        if timeout is None:
            timeout = VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS * VOLCENGINE_VIDEO_POLL_INTERVAL

        attempts = 0
        transient_streak = 0  # 查询瞬态错误（5xx/网络）连续计数；不计入 attempts
        while attempts < VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS and time.time() - start_time < timeout:
            attempts += 1
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                last_error = str(e)
                transient_streak += 1
                if transient_streak >= _POLL_TRANSIENT_TOLERANCE:
                    # 连续 N 次查询失败 → 视为任务异常，结束轮询
                    _verbose_write(
                        f"  [镜头{req.clip_index}] [!] 查询接口连续 {transient_streak} 次失败，"
                        f"放弃轮询: {last_error}"
                    )
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"轮询查询连续失败 ({transient_streak} 次): {last_error}",
                    )
                # 指数退避：2s / 4s / 8s 上限 30s
                wait = min(2.0 ** transient_streak, 30.0)
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] 查询失败 ({transient_streak}/{_POLL_TRANSIENT_TOLERANCE})，"
                    f"{wait:.0f}s 后重试: {last_error[:100]}"
                )
                time.sleep(wait)
                continue
            # 成功响应 → 重置瞬态计数，按正常间隔继续轮询
            transient_streak = 0

            status = str(output.get("status") or "").lower()
            _verbose_write(f"  [镜头{req.clip_index}] 任务状态: {status} "
                  f"(已等待 {int(time.time() - start_time)}s)")

            # 状态与结果引用走**同一份规范化**（§V1：不把日志/文本当返回协议）
            obs = TaskObservation.from_raw(task_id, output)

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
                return self._download(req, video_url)

            if obs.status in (OBS_FAILED, OBS_EXPIRED):
                msg = obs.error or obs.raw_status
                _verbose_write(f"  [镜头{req.clip_index}] 任务失败: {obs.raw_status} - {msg}")
                # 2026-09-12 用户要求：失败时日志打印完整报错（不截断，便于排障）
                _verbose_write(f"[DEBUG] [镜头{req.clip_index}] 任务失败完整响应: {output}")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"生成失败: {msg}",
                )

            # queued / running：按配置间隔继续轮询
            time.sleep(VOLCENGINE_VIDEO_POLL_INTERVAL)

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
        _verbose_write(f"  [镜头{req.clip_index}] 下载视频: {video_url} -> {target}")
        result = http_download_video(video_url, target, clip_index=req.clip_index,
                                     provider="volcengine")
        if result.success:
            _verbose_write(f"  [镜头{req.clip_index}] 视频已保存 -> {result.save_path}")
        return result
