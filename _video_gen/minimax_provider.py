"""MiniMax 视频生成 — Hailuo v1（旧模型，省钱）+ MiniMax-H3 v2（新模型，功能全）.

双协议（官方文档）:
  v1 (Hailuo): POST {base}/v1/video_generation
    - T2V / I2V（仅首帧 first_frame_image，不支持尾帧/参考图/参考音频）
    - 轮询 GET {base}/v1/query/video_generation?task_id= → file_id
    - 下载 GET {base}/v1/files/retrieve?file_id= → file.download_url
  v2 (MiniMax-H3): POST {base}/v2/video_generation
    - T2V / I2V（首尾帧）/ R2V（参考图/参考音频），content[] 多模态
    - 轮询 GET {base}/v2/query/video_generation/{task_id} → task.content.url
    - i2v 与 r2v 互斥：有 reference_* 就不能有 first_frame/last_frame

路由（省成本优先）:
- 仅 prompt / prompt+单首帧 → v1 Hailuo（便宜旧模型）
- 含尾帧 / 多关键帧 / 参考图 / 参考音频 → v2 MiniMax-H3（贵但能力全）

403 回退: T2V / I2V 各自模型队列内 403 自动切下一个（仅 MiniMax 系列模型）。

官方文档:
  https://platform.minimax.io/docs/api-reference/video-generation-t2v
  https://platform.minimax.io/docs/api-reference/video-generation-i2v
  https://platform.minimax.io/docs/api-reference/video-generation-v2-create
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

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config
from shared.config import (
    MINIMAX_API_KEY,
    MINIMAX_BASE_URL,
    MINIMAX_T2V_MODEL,
    MINIMAX_T2V_FALLBACK_MODELS,
    MINIMAX_I2V_MODEL,
    MINIMAX_I2V_FALLBACK_MODELS,
    MINIMAX_H3_MODEL,
    MINIMAX_VIDEO_RESOLUTION,
    MINIMAX_VIDEO_POLL_INTERVAL,
    MINIMAX_VIDEO_MAX_POLL_ATTEMPTS,
)
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02b.log")

# v1 查询状态（官方）：Preparing/Queueing/Processing/Success/Fail
_V1_STATUS_SUCCEEDED = ("success", "done")
_V1_STATUS_FAILED = ("fail", "failed", "error", "cancelled")
# v2 查询状态（官方）：queued/running/succeeded/failed/cancelled
_V2_STATUS_SUCCEEDED = ("succeeded", "success", "done")
_V2_STATUS_FAILED = ("failed", "cancelled", "error", "expired")


def _encode_media(path: Optional[str]) -> Optional[str]:
    """读取本地文件，返回 base64 data URL（v1 first_frame_image / v2 content 接受 data URI）."""
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


def _resolve_ratio(req: VideoGenerationRequest) -> str:
    """按 req.size（如 720x1280）推导 ratio；未知回退 9:16 竖屏."""
    size = (req.size or "").strip().lower().replace("*", "x")
    try:
        w, h = size.split("x")
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        return "9:16"
    if w == h:
        return "1:1"
    return "16:9" if w > h else "9:16"


# =======================================================================
# MiniMax H3 官方时间线 prompt 渲染（2026-08 适配）
# =======================================================================
# H3 官方 Video Prompt Writing Guide：期望三段结构
#   integrated_multimodal_description（时间线：谁+在哪+做什么+镜头怎么动，[Shot N] 分镜标记）
#   overall_soundscape（环境/动作/非语言人声）
#   non_diegetic_music（观众才听得到的 BGM）
# 对白用 (S1) says: <d>[language] 台词</d>，I2V 需首帧对齐指令。
# 我们流水线是「一镜一个视频」，所以每个 clip 都是 [Shot 1] 单分镜时间线。

_DIALOGUE_RE = re.compile(r'（用中文普通话说(?:，([^）]*))?）"([^"]*)"')
_AUDIO_CUE_RE = re.compile(r'（背景音效：([^）]*)）')


def _h3_render(req: VideoGenerationRequest) -> str:
    """把 step02 生成的扁平中文 prompt 重组为 MiniMax H3 时间线结构（全中文）.

    2026-08 决策：模型均为国内模型，prompt 只留中文；官方英文结构符改为中文等价。
    - 提取「（背景音效：X）」→ 环境音
    - 「（用中文普通话说，音色）\"台词\"」→ 说话人编号 + <d>[中文] 台词</d>
      （(S1)/<d> 是官方对白绑定语法，非英文内容，保留）
    - 包装为「画面描述 / 环境音 / 背景音乐」三段
    - 有首帧图时前置首帧对齐指令
    """
    text = (req.prompt or "")[:7000]  # 官方 Input Requirements: Prompt length ≤ 7000 chars

    # 1) 背景音效 → 环境音
    m = _AUDIO_CUE_RE.search(text)
    soundscape = m.group(1).strip() if m else ""
    text = _AUDIO_CUE_RE.sub("", text)

    # 2) 对白 → 说话人编号 + <d>[中文] 台词</d>
    speakers: list[str] = []

    def _conv(match):
        voice = (match.group(1) or "").strip()
        line = match.group(2).strip()
        speakers.append(line)
        ident = f"带{voice}音色的角色" if voice else "角色"
        return f"{ident} (S{len(speakers)}) 说：<d>[中文] {line}</d>"

    text = _DIALOGUE_RE.sub(_conv, text)
    text = text.replace("所有语音必须为中文普通话，禁止任何英文语音。", "").strip()

    # 3) 三段结构（全中文）
    body = f"画面描述：[镜头1] {text}"
    if soundscape:
        body += f"\n\n环境音：{soundscape}，其余环境音贴合场景。"
    else:
        body += "\n\n环境音：环境音贴合场景（风声、水声、衣料与器物声等）。"
    body += "\n背景音乐：无"

    # 4) 首帧对齐指令（全中文）
    if req.image_path or req.extra_keyframe_paths:
        body = (
            "目标视频 0 秒处完全参考 <图片1>（来自[镜头1]）。\n\n" + body
        )
    # 兜底：组装后仍超 7000 则截断（官方 Input Requirements: Prompt length ≤ 7000 chars）
    if len(body) > 7000:
        body = body[:7000]
        _verbose_write(f"  [镜头{req.clip_index}] [!] H3 prompt 超 7000 字，已截断为 7000 字")
    return body


# =======================================================================
# MiniMax Hailuo v1 方括号运镜指令（2026-08 适配）
# =======================================================================
# v1 官方支持 15 种方括号运镜指令（区分大小写、同括号 ≤3 组合），比自然语言更精准。
# 按中文运镜词映射，命中多个时取前 3 个组合（官方推荐先用 1 个）。

_HAILUO_CAMERA_RULES: list[tuple[str, str]] = [
    ("推近|推镜|推进|推入|缓慢推", "Push in"),
    ("拉远|后拉|拉镜|拉开|拉出", "Pull out"),
    ("横移|侧移|左移|右移|平移", "Truck left"),
    ("左摇|右摇|摇镜|摇移", "Pan right"),
    ("仰拍|上摇|抬升", "Tilt up"),
    ("俯拍|下摇|压低", "Tilt down"),
    ("升镜|升降|上移|下移机位", "Pedestal up"),
    ("固定机位|固定镜头|静止|定镜", "Static shot"),
    ("跟随|跟拍|跟镜|跟移", "Tracking shot"),
    ("抖动|手持晃动", "Shake"),
    ("变焦|特写推|推拉镜头", "Zoom in"),
]


def _hailuo_camera_prefix(prompt: str) -> str:
    """把中文运镜词映射为 Hailuo v1 方括号指令前缀（≤3 组合，匹配不到返回空）.

    命中多个时按 prompt 出现顺序取前 3 个（官方同括号 ≤3；先用 1 个最稳）。

    注意：此函数从 prompt 全文搜索，是旧路径。新路径优先用结构化字段
    req.camera_movement，见 _hailuo_camera_from_structured。
    """
    if not prompt:
        return ""
    hits: list[tuple[int, str]] = []
    for pat, cmd in _HAILUO_CAMERA_RULES:
        m = re.search(pat, prompt)
        if m:
            hits.append((m.start(), cmd))
    if not hits:
        return ""
    hits.sort(key=lambda t: t[0])  # 按 prompt 出现顺序
    matched: list[str] = []
    for _, cmd in hits:
        if cmd not in matched:
            matched.append(cmd)
        if len(matched) >= 3:
            break
    return "[" + ",".join(matched) + "] "


def _hailuo_camera_from_structured(camera_movement: str) -> str:
    """从结构化 camera_movement 字段（中文运镜词）生成 Hailuo v1 方括号指令.

    比 _hailuo_camera_prefix 更精准：字段值就是干净的运镜词，不会误匹配
    prompt 正文中的"推开门"、"拉紧绳索"等含运镜字的普通动词。

    返回形如 "[Push in] " 或 ""（无匹配时）。
    官方推荐 ≤3 个组合，但结构化字段通常只有 1 个主运镜，直接全部映射。
    """
    if not camera_movement:
        return ""
    matched: list[str] = []
    for pat, cmd in _HAILUO_CAMERA_RULES:
        if re.search(pat, camera_movement):
            if cmd not in matched:
                matched.append(cmd)
            if len(matched) >= 3:
                break
    if not matched:
        return ""
    return "[" + ",".join(matched) + "] "


class MiniMaxVideoService(VideoGenerationService):
    """MiniMax 视频生成服务.

    双协议自动路由:
    - v1 Hailuo（省钱）: 仅 prompt（T2V）或 prompt + 单首帧（I2V）
    - v2 H3（贵）: 含尾帧 / 多关键帧 / 参考图 / 参考音频
    """

    def __init__(self) -> None:
        config.validate()
        if not MINIMAX_API_KEY:
            raise ValueError(
                "没有配置 MINIMAX_API_KEY（.env.keys 中设置）"
            )
        self._t2v_model_queue: list[str] = [MINIMAX_T2V_MODEL] + MINIMAX_T2V_FALLBACK_MODELS
        self._i2v_model_queue: list[str] = [MINIMAX_I2V_MODEL] + MINIMAX_I2V_FALLBACK_MODELS
        self._current_i2v_index = 0
        self._base_url = MINIMAX_BASE_URL.rstrip("/")
        # 轮询参数
        self._poll_interval = max(MINIMAX_VIDEO_POLL_INTERVAL, 3)
        self._max_poll_attempts = max(MINIMAX_VIDEO_MAX_POLL_ATTEMPTS, 1)

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        }

    # =======================================================================
    # 创建任务
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        # 判断是否需要 v2 H3：尾帧 / 多关键帧 / 参考图 / 参考音频
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).exists()]
        has_last = bool(req.last_frame_image_path)
        has_multi_kf = len(kfs) >= 2
        has_refs = bool(req.reference_paths)
        has_audio = bool(req.reference_audio)
        if has_last or has_multi_kf or has_refs or has_audio:
            return self._create_h3_task(req)
        # 单首帧（image_path 或 1 张关键帧）→ v1 I2V；纯 prompt → v1 T2V
        if req.image_path or kfs:
            return self._create_v1_i2v_task(req)
        return self._create_v1_t2v_task(req)

    # -----------------------------------------------------------------------
    # v1 Hailuo
    # -----------------------------------------------------------------------
    def _resolve_v1_resolution(self, req: VideoGenerationRequest, model: str) -> str:
        """v1 分辨率：01 系列仅 720P；Hailuo 系列按 req.size 短边定档（默认 768P）."""
        if "01" in model.lower():
            return "720P"
        size = (req.size or "").strip().lower().replace("*", "x")
        try:
            w, h = size.split("x")
            short = min(int(w), int(h))
        except (ValueError, AttributeError):
            return MINIMAX_VIDEO_RESOLUTION or "768P"
        if short >= 1080:
            return "1080P"
        if short >= 720:
            return "768P"
        return "512P"

    def _resolve_v1_duration(self, req: VideoGenerationRequest, model: str, resolution: str) -> int:
        """v1 时长：01 系列 / 1080P 仅 6s；其余 768P 可 6 或 10."""
        if "01" in model.lower() or resolution == "1080P":
            return 6
        try:
            d = int(req.duration or 6)
        except (TypeError, ValueError):
            return 6
        return 6 if d <= 6 else 10

    def _submit_v1(self, req: VideoGenerationRequest, model: str, payload: dict) -> str:
        """提交单个 v1 任务，返回 task_id；失败抛异常."""
        _verbose_write(f"  [镜头{req.clip_index}] [MiniMax v1] 正在提交任务 [{model}]...")
        _verbose_write(f"[DEBUG] video_submit: model={model}, resolution={payload.get('resolution')}, "
              f"duration={payload.get('duration')}")
        _verbose_write(f"[DEBUG] video_submit: prompt全文={req.prompt}")

        resp = requests.post(
            f"{self._base_url}/v1/video_generation",
            json=payload,
            headers=self._headers,
            timeout=180,
        )
        _verbose_write(f"[DEBUG] video_submit response: status={resp.status_code}, "
              f"body={resp.text[:500]}")

        if resp.status_code == 200:
            data = resp.json()
            task_id = data.get("task_id")
            if not task_id:
                raise RuntimeError(f"MiniMax v1 未返回 task_id: {resp.text[:300]}")
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [MiniMax v1] -> task_id: {task_id}")
            return f"v1:{task_id}"

        raise RuntimeError(f"MiniMax v1 API 错误 [{resp.status_code}]: {resp.text[:500]}")

    def _create_v1_t2v_task(self, req: VideoGenerationRequest) -> str:
        last_error = ""
        for model in self._t2v_model_queue:
            resolution = self._resolve_v1_resolution(req, model)
            # v6.58 (Phase C-1): 结构化 camera_movement 字段驱动方括号运镜指令，
            # 比 prompt 全文搜索更精准（不会误匹配"推开门"等普通动词）。
            cam_prefix = _hailuo_camera_from_structured(req.camera_movement or "")
            payload: dict = {
                "model": model,
                "prompt": (cam_prefix + req.prompt)[:2000],
                "resolution": resolution,
                "duration": self._resolve_v1_duration(req, model, resolution),
                "prompt_optimizer": False,  # 已人工精调，关闭改写
            }
            try:
                return self._submit_v1(req, model, payload)
            except RuntimeError as e:
                err = str(e)
                if "403" in err:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] T2V 模型 {model} 返回 403，"
                          f"尝试下一个")
                    last_error = err
                    continue
                raise
        raise RuntimeError(f"T2V 任务创建失败: 所有模型均失败 ({last_error})")

    def _create_v1_i2v_task(self, req: VideoGenerationRequest) -> str:
        last_error = ""
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).exists()]
        first_frame = req.image_path or (kfs[0] if kfs else None)
        first_frame_b64 = _encode_media(first_frame) if first_frame else None
        for model in self._i2v_model_queue[self._current_i2v_index:]:
            resolution = self._resolve_v1_resolution(req, model)
            # v6.58 (Phase C-1): 结构化 camera_movement 字段驱动方括号运镜指令
            cam_prefix = _hailuo_camera_from_structured(req.camera_movement or "")
            payload: dict = {
                "model": model,
                "prompt": (cam_prefix + req.prompt)[:2000],
                "resolution": resolution,
                "duration": self._resolve_v1_duration(req, model, resolution),
                "prompt_optimizer": False,
            }
            if first_frame_b64:
                payload["first_frame_image"] = first_frame_b64
            try:
                return self._submit_v1(req, model, payload)
            except RuntimeError as e:
                err = str(e)
                if "403" in err:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] I2V 模型 {model} 返回 403，"
                          f"尝试下一个")
                    self._current_i2v_index += 1
                    last_error = err
                    continue
                raise
        raise RuntimeError(f"I2V 任务创建失败: 所有模型均失败 ({last_error})")

    # -----------------------------------------------------------------------
    # v2 MiniMax-H3
    # -----------------------------------------------------------------------
    def _build_h3_content(self, req: VideoGenerationRequest) -> list[dict]:
        """构建 H3 content 数组.

        官方规定 i2v 与 r2v 互斥：
        - reference_paths / reference_audio → R2V 模式（全 reference_*，忽略首尾帧）
        - 否则 I2V 模式：首帧 + 尾帧（多关键帧丢弃中间帧，首尾双锚定）
        """
        content: list[dict] = [{"type": "text", "text": _h3_render(req)}]

        def _append_image(path, role):
            url = _encode_media(path)
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}, "role": role})

        # ---- R2V 模式：参考图/参考音频 ----
        if req.reference_paths:
            from shared.ref_compose import get_ref_limit, fit_flat
            ref_limit = get_ref_limit(MINIMAX_H3_MODEL, default=9)
            ref_paths = [p for p in (req.reference_paths or []) if p and Path(p).exists()]
            if len(ref_paths) > ref_limit:
                from shared.paths import vimax_working_dir
                sheet_dir = vimax_working_dir() / "shots" / str(req.clip_index)
                ref_paths = fit_flat(ref_paths, ref_limit, sheet_dir, f"r2v{req.clip_index}")
                _verbose_write(f"  [镜头{req.clip_index}] 参考图 {len(req.reference_paths)} 张超上限 "
                      f"{ref_limit}，无损压缩为 {len(ref_paths)} 张")
            for ref_path in ref_paths:
                _append_image(ref_path, "reference_image")
            self._append_audio(req, content)
            return content

        # ---- I2V 模式：首帧 + 尾帧（多关键帧首尾双锚定，丢弃中间帧）----
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).exists()]
        first = req.image_path or (kfs[0] if kfs else None)
        if kfs and len(kfs) > 2:
            _verbose_write(f"  [镜头{req.clip_index}] MiniMax-H3 i2v 与 r2v 互斥，"
                  f"多关键帧退化为首尾双锚定（丢弃中间 {len(kfs) - 2} 张）")
        if first:
            _append_image(first, "first_frame")
        if req.last_frame_image_path:
            _append_image(req.last_frame_image_path, "last_frame")
        elif kfs and len(kfs) > 1:
            _append_image(kfs[-1], "last_frame")
        self._append_audio(req, content)
        return content

    def _append_audio(self, req: VideoGenerationRequest, content: list[dict]) -> None:
        if req.reference_audio:
            url = _encode_media(req.reference_audio)
            if url:
                content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})

    def _submit_h3(self, req: VideoGenerationRequest) -> str:
        content = self._build_h3_content(req)
        # ratio: T2V（纯 text）必须具体 ratio；i2v/r2v 必须 adaptive
        has_media = any(item["type"] != "text" for item in content)
        payload: dict = {
            "model": MINIMAX_H3_MODEL,
            "content": content,
            "resolution": (MINIMAX_VIDEO_RESOLUTION or "768P").upper(),
            "duration": max(4, min(15, int(req.duration or 6))),
            "ratio": "adaptive" if has_media else _resolve_ratio(req),
        }

        _verbose_write(f"  [镜头{req.clip_index}] [MiniMax-H3] 正在提交任务...")
        _verbose_write(f"[DEBUG] video_submit: model={MINIMAX_H3_MODEL}, "
              f"resolution={payload['resolution']}, duration={payload['duration']}, "
              f"ratio={payload['ratio']}, seed={req.seed}")
        _verbose_write(f"[DEBUG] video_submit: prompt全文={req.prompt}")
        for i, item in enumerate(content):
            if item["type"] == "text":
                _verbose_write(f"[DEBUG] video_submit: content[{i}] text")
            else:
                u = item.get("image_url") or item.get("audio_url") or {}
                raw = str(u.get("url", ""))
                _verbose_write(f"[DEBUG] video_submit: content[{i}] {item['type']} "
                      f"role={item.get('role')}, len={len(raw)}")

        resp = requests.post(
            f"{self._base_url}/v2/video_generation",
            json=payload,
            headers=self._headers,
            timeout=180,
        )
        _verbose_write(f"[DEBUG] video_submit response: status={resp.status_code}, "
              f"body={resp.text[:500]}")

        if resp.status_code == 200:
            data = resp.json()
            task_id = data.get("task_id")
            if not task_id:
                raise RuntimeError(f"MiniMax-H3 未返回 task_id: {resp.text[:300]}")
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [MiniMax-H3] -> task_id: {task_id}")
            return f"v2:{task_id}"

        raise RuntimeError(f"MiniMax-H3 API 错误 [{resp.status_code}]: {resp.text[:500]}")

    def _create_h3_task(self, req: VideoGenerationRequest) -> str:
        return self._submit_h3(req)

    # =======================================================================
    # 查询任务
    # =======================================================================
    def query_task(self, task_id: str) -> dict:
        protocol, raw_id = self._parse_task_id(task_id)
        if protocol == "v2":
            resp = requests.get(
                f"{self._base_url}/v2/query/video_generation/{raw_id}",
                headers=self._headers,
                timeout=60,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"MiniMax-H3 查询失败 [{resp.status_code}]: {resp.text[:300]}")
            return resp.json()
        resp = requests.get(
            f"{self._base_url}/v1/query/video_generation",
            params={"task_id": raw_id},
            headers=self._headers,
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax v1 查询失败 [{resp.status_code}]: {resp.text[:300]}")
        return resp.json()

    def _parse_task_id(self, task_id: str) -> tuple[str, str]:
        if isinstance(task_id, str) and task_id.startswith("v2:"):
            return "v2", task_id[3:]
        if isinstance(task_id, str) and task_id.startswith("v1:"):
            return "v1", task_id[3:]
        return "v1", str(task_id)

    def _fetch_v1_download_url(self, file_id: str) -> str:
        """v1 任务成功后按 file_id 换取下载地址."""
        resp = requests.get(
            f"{self._base_url}/v1/files/retrieve",
            params={"file_id": file_id},
            headers=self._headers,
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax v1 文件检索失败 [{resp.status_code}]: {resp.text[:300]}")
        data = resp.json()
        file = data.get("file") or {}
        url = file.get("download_url") or data.get("download_url")
        if not url:
            raise RuntimeError(f"MiniMax v1 文件检索未返回 download_url: {resp.text[:300]}")
        return url

    # =======================================================================
    # 轮询结果
    # =======================================================================
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str, timeout: int | None = None
    ) -> VideoGenerationResult:
        protocol, _ = self._parse_task_id(task_id)
        start_time = time.time()
        last_error = ""
        if timeout is None:
            timeout = self._max_poll_attempts * self._poll_interval

        attempts = 0
        while attempts < self._max_poll_attempts and time.time() - start_time < timeout:
            attempts += 1
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                last_error = str(e)
                time.sleep(2)
                continue

            if protocol == "v2":
                task = output.get("task") or {}
                status = str(task.get("status") or "").lower()
                _verbose_write(f"  [镜头{req.clip_index}] H3 任务状态: {status} "
                      f"(已等待 {int(time.time() - start_time)}s)")
                if status in _V2_STATUS_SUCCEEDED:
                    video_url = (task.get("content") or {}).get("url")
                    if not video_url:
                        return VideoGenerationResult(
                            clip_index=req.clip_index,
                            success=False,
                            error=f"H3 任务成功但未找到 content.url: {str(output)[:300]}",
                        )
                    if task.get("usage"):
                        _verbose_write(f"  [镜头{req.clip_index}] 计费信息: {task['usage']}")
                    return self._download(req, video_url)
                if status in _V2_STATUS_FAILED:
                    err = task.get("error") or {}
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    _verbose_write(f"  [镜头{req.clip_index}] H3 任务失败: {msg}")
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"生成失败: {msg}",
                    )
            else:
                status = str(output.get("status") or "").lower()
                _verbose_write(f"  [镜头{req.clip_index}] v1 任务状态: {status} "
                      f"(已等待 {int(time.time() - start_time)}s)")
                if status in _V1_STATUS_SUCCEEDED:
                    file_id = output.get("file_id")
                    if not file_id:
                        return VideoGenerationResult(
                            clip_index=req.clip_index,
                            success=False,
                            error=f"v1 任务成功但未找到 file_id: {str(output)[:300]}",
                        )
                    try:
                        video_url = self._fetch_v1_download_url(file_id)
                    except RuntimeError as e:
                        return VideoGenerationResult(
                            clip_index=req.clip_index,
                            success=False,
                            error=str(e),
                        )
                    return self._download(req, video_url)
                if status in _V1_STATUS_FAILED:
                    msg = output.get("status_msg") or output.get("error") or ""
                    _verbose_write(f"  [镜头{req.clip_index}] v1 任务失败: {msg}")
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"生成失败: {msg}",
                    )

            # queued / running / Processing：按配置间隔继续轮询
            time.sleep(self._poll_interval)

        return VideoGenerationResult(
            clip_index=req.clip_index,
            success=False,
            error=f"轮询超时 ({timeout}s): {last_error}",
        )

    # =======================================================================
    # 下载
    # =======================================================================
    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        import shutil
        import tempfile
        from shared.paths import vimax_working_dir

        work_dir = vimax_working_dir()
        save_dir = work_dir / "shots" / str(req.clip_index)
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "video.mp4"

        _verbose_write(f"  [镜头{req.clip_index}] 下载视频: {video_url}")
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
            resp = requests.get(video_url, timeout=180)
            resp.raise_for_status()
            video_bin = resp.content
            if len(video_bin) < 20 * 1024:
                raise RuntimeError(f"下载内容过小 ({len(video_bin)} bytes)，可能是错误页面")
            with open(tmp_path, "wb") as f:
                f.write(video_bin)
            shutil.copy2(tmp_path, str(save_path))
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
            _verbose_write(f"  [镜头{req.clip_index}] 视频已保存 -> {save_path}")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=True,
                save_path=str(save_path),
            )
        except Exception as e:
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=False,
                error=f"下载失败: {e}",
            )
