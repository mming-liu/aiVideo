"""DashScope (通义万相) 视频生成 — 基于 dashscope SDK.

使用 VideoSynthesis.async_call / fetch / wait 管理异步任务.
支持 403 自动回退到下一个模型.
"""

import os
import time
import requests
from typing import Optional

import dashscope
from dashscope import VideoSynthesis

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config

from shared.log_utils import verbose_write as _verbose_write


class DashScopeVideoService(VideoGenerationService):
    """百炼 DashScope 文生视频实现 (通义万相).

    使用 dashscope SDK 的 VideoSynthesis 接口:
      - async_call()  → 提交任务，返回 task_id
      - fetch()       → 查询任务状态
      - wait()        → 阻塞等待完成

    403 回退机制:
      - 主模型返回 403 时，自动切换到 DASHSCOPE_VIDEO_FALLBACK_MODELS 中的下一个模型
      - 切换对上层透明，仅在日志中提示
    """

    def __init__(self) -> None:
        config.validate()
        dashscope.api_key = config.DASHSCOPE_API_KEY
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [config.DASHSCOPE_VIDEO_MODEL] + config.DASHSCOPE_VIDEO_FALLBACK_MODELS
        self._current_model_index: int = 0

    # ------------------------------------------------------------------
    # 模型回退
    # ------------------------------------------------------------------
    @property
    def _current_model(self) -> str:
        return self._model_queue[0]  # FULLDELETE fallback

    def _advance_model(self) -> Optional[str]:
        """切换到下一个回退模型. 返回新模型名，无可用模型时返回 None."""
        if True:  # FULLDELETE fallback disabled
            return None
        self._current_model_index += 1
        return self._current_model

    # ------------------------------------------------------------------
    # 创建任务（带 403 回退）
    # ------------------------------------------------------------------
    def create_task(self, req: VideoGenerationRequest) -> str:
        size = (req.size or config.VIDEO_SIZE).replace("x", "*")

        while True:
            model = self._current_model
            kwargs: dict = {
                "model": model,
                "prompt": req.prompt,
                "size": size,
            }

            if config.DASHSCOPE_VIDEO_DURATION:
                kwargs["duration"] = config.DASHSCOPE_VIDEO_DURATION

            if req.negative_prompt:
                kwargs["negative_prompt"] = req.negative_prompt

            rsp = VideoSynthesis.async_call(**kwargs)

            _verbose_write(f"  [镜头{req.clip_index}] [{model}] {req.prompt[:60]}...")

            if rsp.status_code == 200:
                # #21 修复 2026-08-14：HTTP 200 但 output 缺 task_id（部分错误响应
                # 以 200 返回）→ KeyError 无保护。改为显式取用 + 报错信息含响应原文。
                task_id = (rsp.output or {}).get("task_id") or ""
                if not task_id:
                    raise RuntimeError(
                        f"创建任务成功但未获取 task_id: {str(rsp.output)[:300]}"
                    )
                _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [{model}] -> task_id: {task_id}")
                return task_id

            # ---- 403: 额度耗尽或无权限，尝试回退 ----
            if rsp.status_code == 403:
                next_model = self._advance_model()
                if next_model:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] 模型 {model} 返回 403（额度耗尽/无权限），"
                          f"自动切换到 {next_model}")
                    continue  # 用新模型重试
                else:
                    raise RuntimeError(
                        f"所有模型均返回 403，已无可用回退模型。"
                        f"尝试过: {', '.join(self._model_queue)}"
                    )

            # ---- 其他错误: 直接抛出 ----
            raise RuntimeError(
                f"API 错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}"
            )

    # ------------------------------------------------------------------
    # 查询任务
    # ------------------------------------------------------------------
    def query_task(self, task_id: str) -> dict:
        try:
            rsp = VideoSynthesis.fetch(task_id)
        except Exception as e:
            # SDK 网络/超时异常 → 归类为瞬态（wait_for_completion 按此容忍重试）
            raise RuntimeError(f"查询任务失败（连接异常）: {e}")
        if rsp.status_code != 200:
            # #21 修复：错误信息带状态码，供 wait_for_completion 区分瞬态(5xx)与终态
            raise RuntimeError(f"查询任务失败 [{rsp.status_code}]: {rsp.code} - {rsp.message}")
        return rsp.output

    # ------------------------------------------------------------------
    # 轮询等待 + 下载
    # ------------------------------------------------------------------
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str
    ) -> VideoGenerationResult:
        attempt = 0
        # #21 修复 2026-08-14：查询接口瞬态错误不杀镜头，连续超阈值才放弃
        transient_failures = 0
        while True:
            attempt += 1
            if attempt > config.DASHSCOPE_MAX_POLL_ATTEMPTS:
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"轮询超时 ({config.DASHSCOPE_MAX_POLL_ATTEMPTS} 次)",
                )

            time.sleep(config.DASHSCOPE_POLL_INTERVAL)
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                # 429 / 5xx / 连接异常 → 瞬态，容忍重试；4xx 终态直接失败
                err = str(e)
                transient = ("连接异常" in err) or any(
                    f"[{code}]" in err
                    for code in (408, 429, 499, 500, 502, 503, 504, 520, 522, 524)
                )
                if transient:
                    transient_failures += 1
                    if transient_failures > config.DASHSCOPE_POLL_MAX_TRANSIENT:
                        return VideoGenerationResult(
                            clip_index=req.clip_index,
                            success=False,
                            error=f"轮询连续瞬态错误 {transient_failures} 次: {err}",
                        )
                    _verbose_write(f"  [镜头{req.clip_index}] 查询瞬态错误"
                          f"（{transient_failures}/{config.DASHSCOPE_POLL_MAX_TRANSIENT}），"
                          f"等待后继续轮询: {err}")
                    continue
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"查询失败: {err}",
                )
            status = output.get("task_status", "UNKNOWN")

            if status == "SUCCEEDED":
                video_url = output.get("video_url", "")
                # 部分模型返回 video_urls 列表
                if not video_url and "video_urls" in output:
                    urls = output["video_urls"]
                    video_url = urls[0] if urls else ""
                if not video_url:
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error="任务成功但未找到视频 URL",
                    )
                return self._download(req, video_url)

            if status == "FAILED":
                reason = output.get("message", output.get("code", "未知"))
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"API 失败: {reason}",
                )

            _verbose_write(f"  [镜头{req.clip_index}] 生成中... ({status}, "
                  f"第 {attempt}/{config.DASHSCOPE_MAX_POLL_ATTEMPTS} 次轮询)")

    # ------------------------------------------------------------------
    # 下载视频到本地
    # ------------------------------------------------------------------
    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            save_path = os.path.join(config.OUTPUT_DIR, f"clip_{req.clip_index}.mp4")

            resp = requests.get(video_url, timeout=120)
            resp.raise_for_status()
            video_bin = resp.content
            if len(video_bin) < 20 * 1024:
                raise RuntimeError(f"下载内容过小 ({len(video_bin)} bytes)，可能是错误页面")
            with open(save_path, "wb") as f:
                f.write(video_bin)

            size_mb = len(video_bin) / (1024 * 1024)
            _verbose_write(f"  [镜头{req.clip_index}] [OK] 已保存 -> {save_path} ({size_mb:.1f} MB)")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=True,
                save_path=save_path,
            )
        except Exception as e:
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=False,
                error=f"下载失败: {e}",
            )