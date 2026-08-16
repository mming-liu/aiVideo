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
import time
from functools import partial
from pathlib import Path
from typing import Optional

import requests

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config
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

def _append_audio(req: VideoGenerationRequest, content: list[dict]) -> None:
    """追加参考音频（role=reference_audio）到 content."""
    if not req.reference_audio:
        return
    url = _encode_media(req.reference_audio)
    if url:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": url},
            "role": "reference_audio",
        })


_STATUS_SUCCEEDED = ("succeeded", "success", "done", "completed")
# 官方 status 可选值：queued/running/cancelled/succeeded/failed/expired。
# expired=任务超时被服务端自动终止，也是终态失败（此前漏判，会傻轮询到超时）
_STATUS_FAILED = ("failed", "cancelled", "error", "expired")


def _clamp_duration(value, cap) -> int:
    """夹到模型支持时长区间 [cap.duration_range]；解析失败回退区间中值."""
    lo, hi = cap.duration_range
    try:
        d = int(float(value))
    except (TypeError, ValueError):
        return (lo + hi) // 2
    return max(lo, min(hi, d))


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


# 分辨率排序（升序），用于把推导档位收敛到模型允许的最高档
_RES_ORDER = ("480p", "720p", "1080p", "4k")


def _resolve_resolution(req: VideoGenerationRequest, cap) -> str:
    """按模型能力推导分辨率：先按 req.size（如 720x1280）定档，再收敛到
    cap.resolutions 中「≤ 推导值」的最高档（如 2.5 只支持 720p，1080p 推导→720p）.
    可选 override：VOLCENGINE_VIDEO_RESOLUTION 非空则强制该档位（仍按能力收敛）."""
    override = (VOLCENGINE_VIDEO_RESOLUTION or "").strip().lower()
    size = (req.size or "").strip().lower().replace("*", "x")
    if override in _RES_ORDER:
        derived = override
    else:
        try:
            w, h = size.split("x")
            w, h = int(w), int(h)
        except (ValueError, AttributeError):
            derived = "720p"
        else:
            short = min(w, h)
            derived = "1080p" if short >= 1080 else ("720p" if short >= 720 else "480p")
    if derived in cap.resolutions:
        return derived
    best = cap.resolutions[0]
    for r in cap.resolutions:
        if _RES_ORDER.index(r) <= _RES_ORDER.index(derived):
            best = r
    return best


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
        self._current_i2v_index = 0
        self._base_url = VOLCENGINE_BASE_URL.rstrip("/")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {VOLCENGINE_API_KEY}",
            "Content-Type": "application/json",
        }

    # =======================================================================
    # 创建任务
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        # 判断模式：有参考图/首尾帧走 I2V 队列，否则 T2V 队列
        has_refs = bool(req.reference_paths or req.extra_keyframe_paths)
        has_frames = bool(req.image_path or req.last_frame_image_path)
        if has_refs or has_frames:
            return self._create_i2v_task(req)
        return self._create_t2v_task(req)

    def _build_content(self, req: VideoGenerationRequest, model: str) -> list[dict]:
        """构建 Seedance content 数组（text 在前，媒体在后，按场景语义分配 role）.

        官方文档规定 3 种场景互斥、不可混用（创建视频生成任务 API）：
        - 全模态参考生视频：role=reference_image（仅 Seedance 2.5 / 2.0 系列支持）
        - 图生视频-多关键帧：能力支持时全部 reference_image（prompt 用「图N」
          指代时序，实现 A→B→C→D）；不支持时退化为首尾两帧
        - 图生视频-首帧/首尾帧：role=first_frame / last_frame（所有 Seedance 支持）
        """
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        content: list[dict] = [{"type": "text", "text": req.prompt}]

        def _append_image(path, role):
            url = _encode_media(path)
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}, "role": role})

        # ---- 全模态参考生视频（r2v）：reference_paths → reference_image ----
        if req.reference_paths:
            if not cap.supports_reference_image:
                raise RuntimeError(
                    f"{model} 不支持参考生视频（role=reference_image，仅 Seedance 2.5/2.0 系列）。"
                    f"r2v 模式请将 VOLCENGINE_I2V_MODEL 设为 2.0/2.5 模型"
                )
            from shared.ref_compose import get_ref_limit, fit_flat
            ref_limit = get_ref_limit(model, default=12)
            ref_paths = [p for p in (req.reference_paths or []) if p and Path(p).exists()]
            if len(ref_paths) > ref_limit:
                from shared.paths import vimax_working_dir
                sheet_dir = vimax_working_dir() / "shots" / str(req.clip_index)
                ref_paths = fit_flat(ref_paths, ref_limit, sheet_dir, f"r2v{req.clip_index}")
                _verbose_write(f"  [镜头{req.clip_index}] 参考图 {len(req.reference_paths)} 张超上限 "
                      f"{ref_limit}，无损压缩为 {len(ref_paths)} 张")
            for ref_path in ref_paths:
                _append_image(ref_path, "reference_image")
            _append_audio(req, content)
            return content

        # ---- 多关键帧：能力支持→全部 reference_image（全模态）；否则退化为首尾帧 ----
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).exists()]
        if kfs:
            if cap.supports_reference_image:
                for p in kfs:
                    _append_image(p, "reference_image")
                _append_audio(req, content)
                return content
            if len(kfs) > 2:
                _verbose_write(f"  [镜头{req.clip_index}] {model} 不支持参考生视频，"
                      f"多关键帧退化为首尾帧（丢弃中间 {len(kfs) - 2} 张）")
                kfs = [kfs[0], kfs[-1]]

        # ---- 首帧/首尾帧（所有 Seedance 模型支持）----
        if kfs:
            _append_image(kfs[0], "first_frame")
            if len(kfs) > 1:
                _append_image(kfs[-1], "last_frame")
        else:
            _append_image(req.image_path, "first_frame")
            _append_image(req.last_frame_image_path, "last_frame")
        _append_audio(req, content)
        return content

    def _submit(self, req: VideoGenerationRequest, model: str) -> str:
        """提交单个任务，返回 task_id；失败抛异常."""
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        # 首帧/首尾帧场景（图生视频-首帧/首尾帧，非全模态参考）：用于 2.5 ratio adaptive 判定
        is_first_last_frames = bool(req.image_path or req.last_frame_image_path) \
            and not req.reference_paths and not req.extra_keyframe_paths
        payload: dict = {
            "model": model,
            "content": self._build_content(req, model),
            "resolution": _resolve_resolution(req, cap),
            "ratio": _resolve_ratio(req, cap, is_first_last_frames),
            "duration": _clamp_duration(VOLCENGINE_VIDEO_DURATION or req.duration, cap),
            "watermark": False,
        }
        # 有声视频：按 VIDEO_MODEL_VOICE 决定是否下发 generate_audio。
        # VIDEO_MODEL_VOICE=1（默认）→ 对话/背景音交给视频模型，开；
        # =0 → 语音走 step03 TTS，prompt_builder 已写「禁止任何人声」，必须关掉
        # 模型发声，否则人声 + step03 TTS 双重语音（与 prompt_builder _MODEL_VOICE 一致）。
        # 不支持的模型（1.0 系列）不传该字段（避免参数报错）。
        if cap.supports_generate_audio:
            voice_enabled = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
            payload["generate_audio"] = voice_enabled
        if req.seed is not None:
            payload["seed"] = req.seed

        _verbose_write(f"  [镜头{req.clip_index}] [Seedance] 正在提交任务 [{model}]...")
        _verbose_write(f"[DEBUG] video_submit: model={model}, "
              f"resolution={payload['resolution']}, ratio={payload['ratio']}, "
              f"duration={payload['duration']}, seed={req.seed}")
        _verbose_write(f"[DEBUG] video_submit: prompt全文={req.prompt}")
        for i, item in enumerate(payload["content"]):
            if item["type"] == "text":
                _verbose_write(f"[DEBUG] video_submit: content[{i}] text")
            else:
                u = item.get("image_url") or item.get("audio_url") or {}
                raw = str(u.get("url", ""))
                _verbose_write(f"[DEBUG] video_submit: content[{i}] {item['type']} "
                      f"role={item.get('role')}, len={len(raw)}")

        resp = requests.post(
            f"{self._base_url}/contents/generations/tasks",
            json=payload,
            headers=self._headers,
            timeout=180,
        )
        _verbose_write(f"[DEBUG] video_submit response: status={resp.status_code}, "
              f"body={resp.text[:500]}")

        if resp.status_code == 200:
            data = resp.json()
            task_id = data.get("id") or data.get("task_id")
            if not task_id:
                raise RuntimeError(f"Seedance 未返回 task_id: {resp.text[:300]}")
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [Seedance] -> task_id: {task_id}")
            return str(task_id)

        raise RuntimeError(f"Seedance API 错误 [{resp.status_code}]: {resp.text[:500]}")

    def _create_t2v_task(self, req: VideoGenerationRequest) -> str:
        last_error = ""
        for model in self._t2v_model_queue:
            try:
                return self._submit(req, model)
            except RuntimeError as e:
                err = str(e)
                if "403" in err:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] T2V 模型 {model} 返回 403，"
                          f"尝试下一个")
                    last_error = err
                    continue
                raise
        raise RuntimeError(f"T2V 任务创建失败: 所有模型均失败 ({last_error})")

    def _create_i2v_task(self, req: VideoGenerationRequest) -> str:
        from shared.model_caps import get_seedance_cap
        last_error = ""
        for model in self._i2v_model_queue[self._current_i2v_index:]:
            # r2v（reference_paths）能力前置过滤：1.0/1.5 系列不支持参考生视频，
            # 直接跳过，避免 _build_content 抛错中断整个队列
            if req.reference_paths and not get_seedance_cap(model).supports_reference_image:
                last_error = f"{model} 不支持参考生视频（仅 Seedance 2.5/2.0 系列）"
                _verbose_write(f"  [镜头{req.clip_index}] [!] {model} 不支持 r2v 参考图，尝试下一个")
                continue
            try:
                return self._submit(req, model)
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
        while attempts < VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS and time.time() - start_time < timeout:
            attempts += 1
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                last_error = str(e)
                time.sleep(2)
                continue

            status = str(output.get("status") or "").lower()
            _verbose_write(f"  [镜头{req.clip_index}] 任务状态: {status} "
                  f"(已等待 {int(time.time() - start_time)}s)")

            if status in _STATUS_SUCCEEDED:
                content = output.get("content") or {}
                video_url = content.get("video_url")
                if not video_url:
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"任务成功但未找到 video_url: {str(output)[:300]}",
                    )
                if output.get("usage"):
                    _verbose_write(f"  [镜头{req.clip_index}] 计费信息: {output['usage']}")
                return self._download(req, video_url)

            if status in _STATUS_FAILED:
                err = output.get("error") or {}
                code = err.get("code") if isinstance(err, dict) else ""
                msg = err.get("message") if isinstance(err, dict) else str(err)
                _verbose_write(f"  [镜头{req.clip_index}] 任务失败: {code} - {msg}")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"生成失败: {code} - {msg}",
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
