"""火山方舟 Agent Plan (/api/plan/v3) 视频生成 — Seedance 系列.

与标准方舟 (api/v3) 的区别：
  - 端点：
      提交 POST {base}/content_generation/tasks
      轮询 GET  {base}/content_generation/tasks/{id}
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
from .models import VideoGenerationRequest, VideoGenerationResult
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
)
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02b.log")

_STATUS_SUCCEEDED = ("succeeded", "success", "done", "completed")
_STATUS_FAILED = ("failed", "cancelled", "error", "expired")

_RES_ORDER = ("480p", "720p", "1080p", "4k")
_RATIO_VALID = ("16:9", "9:16", "1:1", "adaptive")


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


def _clamp_duration(value, cap) -> int:
    """夹到模型支持时长区间 [cap.duration_range]；解析失败回退区间中值."""
    lo, hi = cap.duration_range
    try:
        d = int(float(value))
    except (TypeError, ValueError):
        return (lo + hi) // 2
    return max(lo, min(hi, d))


def _resolve_resolution(req: VideoGenerationRequest, cap) -> str:
    """按模型能力推导分辨率（与 volcengine 同款逻辑）."""
    override = (AGENT_PLAN_VIDEO_RESOLUTION or "").strip().lower()
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

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {AGENT_PLAN_API_KEY}",
            "Content-Type": "application/json",
        }

    # =======================================================================
    # 创建任务
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        with self._semaphore:
            return self._submit(req)

    def _build_content(self, req: VideoGenerationRequest, model: str) -> list[dict]:
        """构建 Seedance content 数组（text 在前，媒体在后，按场景语义分配 role）.

        与 volcengine 同款规则（共用 Seedance 能力）：
          - r2v（reference_paths）→ role=reference_image
          - 多关键帧：能力支持→全部 reference_image；否则退化为首尾帧
          - 首帧/首尾帧：所有 Seedance 支持
        """
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        content: list[dict] = [{"type": "text", "text": req.prompt}]

        def _append_image(path, role):
            url = _encode_media(path)
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}, "role": role})

        # ---- 全模态参考生视频（r2v）----
        if req.reference_paths:
            if not cap.supports_reference_image:
                raise RuntimeError(
                    f"{model} 不支持参考生视频（role=reference_image）。"
                    f"r2v 模式请将 AGENT_PLAN_T2V_MODEL 设为 2.0/2.5 系列模型"
                )
            from shared.ref_compose import get_ref_limit, fit_flat
            ref_limit = get_ref_limit(model, default=12)
            ref_paths = [p for p in (req.reference_paths or []) if p and Path(p).exists()]
            if len(ref_paths) > ref_limit:
                from shared.paths import vimax_working_dir
                sheet_dir = vimax_working_dir() / "shots" / str(req.clip_index)
                ref_paths = fit_flat(ref_paths, ref_limit, sheet_dir, f"r2v{req.clip_index}")
                _verbose_write(
                    f"  [镜头{req.clip_index}] Agent Plan 参考图 {len(req.reference_paths)} 张超上限 "
                    f"{ref_limit}，无损压缩为 {len(ref_paths)} 张"
                )
            for ref_path in ref_paths:
                _append_image(ref_path, "reference_image")
            return content

        # ---- 多关键帧：能力支持→全部 reference_image；否则退化为首尾帧 ----
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).exists()]
        if kfs:
            if cap.supports_reference_image:
                for p in kfs:
                    _append_image(p, "reference_image")
                return content
            if len(kfs) > 2:
                _verbose_write(
                    f"  [镜头{req.clip_index}] {model} 不支持参考生视频，"
                    f"多关键帧退化为首尾帧（丢弃中间 {len(kfs) - 2} 张）"
                )
                kfs = [kfs[0], kfs[-1]]

        # ---- 首帧/首尾帧（所有 Seedance 模型支持）----
        if kfs:
            _append_image(kfs[0], "first_frame")
            if len(kfs) > 1:
                _append_image(kfs[-1], "last_frame")
        else:
            _append_image(req.image_path, "first_frame")
            _append_image(req.last_frame_image_path, "last_frame")
        return content

    def _submit(self, req: VideoGenerationRequest) -> str:
        from shared.model_caps import get_seedance_cap

        model = self._model
        cap = get_seedance_cap(model)

        # 首帧/首尾帧场景（图生视频-首帧/首尾帧，非全模态参考）
        is_first_last_frames = bool(req.image_path or req.last_frame_image_path) \
            and not req.reference_paths and not req.extra_keyframe_paths

        payload: dict = {
            "model": model,
            "content": self._build_content(req, model),
            "resolution": _resolve_resolution(req, cap),
            "ratio": _resolve_ratio(req, cap, is_first_last_frames),
            "duration": _clamp_duration(AGENT_PLAN_VIDEO_DURATION or req.duration, cap),
            "watermark": False,
        }
        if cap.supports_generate_audio:
            voice_enabled = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
            payload["generate_audio"] = voice_enabled
        if req.seed is not None and getattr(cap, "supports_seed", False):
            payload["seed"] = req.seed

        _verbose_write(
            f"  [镜头{req.clip_index}] [Agent Plan Seedance] 正在提交任务 [{model}]..."
        )
        _verbose_write(
            f"[DEBUG] agent_plan_video_submit: model={model}, "
            f"resolution={payload['resolution']}, ratio={payload['ratio']}, "
            f"duration={payload['duration']}, seed={req.seed}"
        )
        _verbose_write(f"[DEBUG] agent_plan_video_submit: prompt全文={req.prompt}")
        for i, item in enumerate(payload["content"]):
            if item["type"] == "text":
                _verbose_write(f"[DEBUG] agent_plan_video_submit: content[{i}] text")
            else:
                u = item.get("image_url") or {}
                raw = str(u.get("url", ""))
                _verbose_write(
                    f"[DEBUG] agent_plan_video_submit: content[{i}] {item['type']} "
                    f"role={item.get('role')}, len={len(raw)}"
                )

        last_error = ""
        backoff = 2.0
        for attempt in range(1, 5):  # 简化版：4 次重试（与 volcengine 共享 5xx/429 容忍）
            try:
                resp = requests.post(
                    f"{self._base_url}/content_generation/tasks",
                    json=payload,
                    headers=self._headers,
                    timeout=180,
                )
            except requests.exceptions.RequestException as e:
                last_error = f"提交网络异常: {e}"
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] {last_error}，"
                    f"{backoff:.0f}s 后重试 (尝试 {attempt}/4)"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            _verbose_write(
                f"[DEBUG] agent_plan_video_submit response: status={resp.status_code}, "
                f"body={resp.text[:500]}"
            )

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as e:
                    raise RuntimeError(
                        f"Agent Plan Seedance 响应 JSON 解析失败: {e} ({resp.text[:200]})"
                    )
                task_id = data.get("id") or data.get("task_id")
                if not task_id:
                    raise RuntimeError(f"Agent Plan Seedance 未返回 task_id: {resp.text[:300]}")
                _verbose_write(
                    f"  [镜头{req.clip_index}] 任务已创建 [Agent Plan] -> task_id: {task_id}"
                )
                return str(task_id)

            if resp.status_code == 429:
                wait = 30.0
                m = re.search(r"retry[-_]after[\"':=\s]+(\d+)", resp.text, re.IGNORECASE)
                if m:
                    wait = float(m.group(1))
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] 429 限速，等待 {wait:.0f}s 后重试 "
                    f"(尝试 {attempt}/4)"
                )
                time.sleep(wait)
                continue

            if 500 <= resp.status_code < 600:
                last_error = f"服务端 {resp.status_code}: {resp.text[:200]}"
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] 服务端 {resp.status_code}，"
                    f"{backoff:.0f}s 后重试 (尝试 {attempt}/4)"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            # 客户端错误（401/403/400/404）不重试
            break

        status = resp.status_code if "resp" in locals() else "?"
        body = resp.text[:500] if "resp" in locals() else last_error
        raise RuntimeError(f"Agent Plan Seedance API 错误 [{status}]: {body}")

    # =======================================================================
    # 查询任务
    # =======================================================================
    def query_task(self, task_id: str) -> dict:
        resp = requests.get(
            f"{self._base_url}/content_generation/tasks/{task_id}",
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
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] Agent Plan 查询失败: {last_error[:200]}"
                )
                time.sleep(AGENT_PLAN_VIDEO_POLL_INTERVAL)
                continue

            status = str(output.get("status") or "").lower()
            _verbose_write(
                f"  [镜头{req.clip_index}] Agent Plan 任务状态: {status} "
                f"(已等待 {int(time.time() - start_time)}s)"
            )

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
                # TOS url 短期有效 → 立即下载落盘
                return self._download(req, video_url)

            if status in _STATUS_FAILED:
                err = output.get("error") or {}
                code = err.get("code") if isinstance(err, dict) else ""
                msg = err.get("message") if isinstance(err, dict) else str(err)
                _verbose_write(f"  [镜头{req.clip_index}] Agent Plan 任务失败: {code} - {msg}")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"生成失败: {code} - {msg}",
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
        import shutil
        import tempfile
        from shared.paths import vimax_working_dir

        work_dir = vimax_working_dir()
        save_dir = work_dir / "shots" / str(req.clip_index)
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "video.mp4"

        _verbose_write(f"  [镜头{req.clip_index}] Agent Plan 下载视频: {video_url}")
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
            _verbose_write(f"  [镜头{req.clip_index}] Agent Plan 视频已保存 -> {save_path}")
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
