"""agnes ai 视频生成 — 异步轮询模式.

提交: POST /v1/videos
轮询: GET  /agnesapi?video_id=<VIDEO_ID>

模型: agnes-video-v2.0
  - text-to-video: prompt + width + height + num_frames + frame_rate
  - image-to-video: 附加 image 字段 (data URL base64)

403 自动回退: 主模型返回 403 时切换到 AGNES_VIDEO_FALLBACK_MODELS 中的下一个,
回退在实例生命周期内保持 (与 DashScope 一致).
"""

import base64
import mimetypes
import os
import time
from typing import Optional

import requests

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config


_SYNC_PREFIX = "__sync__"


class AgnesVideoService(VideoGenerationService):
    """agnes ai 视频生成实现 (agnes-video-v2.0)."""

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [
            config.AGNES_VIDEO_MODEL
        ] + config.AGNES_VIDEO_FALLBACK_MODELS
        self._current_model_index: int = 0

    # ------------------------------------------------------------------
    # 模型回退
    # ------------------------------------------------------------------
    @property
    def _current_model(self) -> str:
        return self._model_queue[self._current_model_index]

    def _advance_model(self) -> Optional[str]:
        """切换到下一个回退模型. 返回新模型名，无可用模型时返回 None."""
        if self._current_model_index + 1 >= len(self._model_queue):
            return None
        self._current_model_index += 1
        return self._current_model

    # ------------------------------------------------------------------
    # 首帧图编码
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_image(path: Optional[str]) -> Optional[str]:
        """读取本地图片，返回 data URL (base64)，padding 已标准化.

        Agnes video API 对 base64 padding 较敏感，编码后主动补齐 padding 并
        确保长度是 4 的倍数（消除 "incorrect padding" / "1 more than a multiple of 4" 错误）.
        """
        if not path:
            return None
        try:
            p = os.path.abspath(path)
            if not os.path.isfile(p):
                return None
            with open(p, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("ascii")
            # 标准化 padding：去掉多余 = 后按 4 字节补齐，消除 Agnes 端 padding 校验错误
            b64 = b64.rstrip("=")
            rem = len(b64) % 4
            if rem:
                b64 += "=" * (4 - rem)
            mime, _ = mimetypes.guess_type(p)
            return f"data:{mime or 'image/png'};base64,{b64}"
        except Exception:
            return None

    # ------------------------------------------------------------------
    # num_frames 约束
    # ------------------------------------------------------------------
    @staticmethod
    def _clamp_num_frames(raw: int) -> int:
        """Agnes 约束: num_frames = 8n + 1，取最接近的合法值（至少 1）."""
        if raw < 1:
            return 1
        n = round((raw - 1) / 8)
        n = max(n, 0)
        result = 8 * n + 1
        return result

    # ------------------------------------------------------------------
    # 响应字段解析
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_video_id(data: dict) -> Optional[str]:
        """从创建任务响应中提取 video_id."""
        for key in ("video_id", "id", "task_id"):
            v = data.get(key)
            if v:
                return str(v)
        # 嵌套在 data 字段中
        d = data.get("data") or {}
        if isinstance(d, dict):
            for key in ("video_id", "id", "task_id"):
                v = d.get(key)
                if v:
                    return str(v)
        return None

    @staticmethod
    def _extract_video_url(output: dict) -> Optional[str]:
        """从轮询响应中提取视频下载 URL."""
        for key in ("video_url", "url", "remixed_from_video_id"):
            v = output.get(key)
            if v and isinstance(v, str) and v.startswith("http"):
                return str(v)
        urls = output.get("video_urls")
        if urls and isinstance(urls, list) and urls[0]:
            return str(urls[0])
        d = output.get("data") or {}
        if isinstance(d, dict):
            for key in ("video_url", "url", "remixed_from_video_id"):
                v = d.get(key)
                if v and isinstance(v, str) and v.startswith("http"):
                    return str(v)
        return None

    @staticmethod
    def _extract_status(output: dict) -> str:
        """从轮询响应中提取任务状态，统一转大写."""
        s = output.get("status") or output.get("task_status") or ""
        return str(s).upper()

    # ------------------------------------------------------------------
    # 创建任务（带 403 回退 + 5xx 自动重试）
    # ------------------------------------------------------------------
    def create_task(self, req: VideoGenerationRequest) -> str:
        image_data_url = self._encode_image(req.image_path)
        if req.image_path and not image_data_url:
            print(f"  [镜头{req.clip_index}] 首帧图 {req.image_path} 读取失败，"
                  f"降级为 text-to-video")

        # 计算尺寸与帧数
        width = config.AGNES_VIDEO_WIDTH
        height = config.AGNES_VIDEO_HEIGHT
        frame_rate = config.AGNES_VIDEO_FRAME_RATE
        num_frames = config.AGNES_VIDEO_NUM_FRAMES
        if num_frames <= 0 and req.duration > 0:
            raw = req.duration * frame_rate
            # Agnes 约束: num_frames = 8n + 1，取最接近的合法值
            num_frames = self._clamp_num_frames(raw)

        _submit_retry = 0

        while True:
            model = self._current_model
            headers = {
                "Authorization": f"Bearer {config.AGNES_API_KEY}",
                "Content-Type": "application/json",
            }
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "width": width,
                "height": height,
                "frame_rate": frame_rate,
            }
            if num_frames > 0:
                payload["num_frames"] = num_frames
            if image_data_url:
                payload[config.AGNES_VIDEO_IMAGE_FIELD] = image_data_url

            print(f"  [镜头{req.clip_index}] [prompt -> {model}] "
                  f"{req.prompt[:200]}{'...' if len(req.prompt) > 200 else ''}")

            try:
                res = requests.post(
                    self._submit_url(), json=payload, headers=headers, timeout=120
                )
            except Exception as e:
                # 连接错误也做重试
                _submit_retry += 1
                if _submit_retry <= config.AGNES_VIDEO_SUBMIT_RETRIES:
                    print(f"  [镜头{req.clip_index}] 提交异常: {e}，"
                          f"{config.AGNES_VIDEO_SUBMIT_RETRY_DELAY}s 后重试 "
                          f"({_submit_retry}/{config.AGNES_VIDEO_SUBMIT_RETRIES})")
                    time.sleep(config.AGNES_VIDEO_SUBMIT_RETRY_DELAY)
                    continue
                raise RuntimeError(f"Agnes 视频提交异常（重试{_submit_retry}次后仍失败）: {e}")

            # ---- 200 ----
            if res.status_code == 200:
                data = res.json()

                # sync 模式: 直接返回 video_url（用前缀标记）
                if config.AGNES_VIDEO_MODE == "sync":
                    vurl = self._extract_video_url(data)
                    if vurl:
                        print(f"  [镜头{req.clip_index}] 任务已创建(同步) [{model}] "
                              f"-> 直接返回 video_url")
                        return f"{_SYNC_PREFIX}{vurl}"
                    raise RuntimeError(
                        f"Agnes 同步模式但未返回 video_url: {str(data)[:300]}"
                    )

                # async 模式
                video_id = self._extract_video_id(data)
                if not video_id:
                    raise RuntimeError(
                        f"Agnes 未返回 video_id: {str(data)[:300]}"
                    )
                mode_tag = "image-to-video" if image_data_url else "text-to-video"
                print(f"  [镜头{req.clip_index}] 任务已创建 [{model}] ({mode_tag}) "
                      f"-> video_id: {video_id}")
                return video_id

            # ---- 5xx: 服务端暂时不可用，等待后重试 ----
            if 500 <= res.status_code < 600:
                _submit_retry += 1
                if _submit_retry <= config.AGNES_VIDEO_SUBMIT_RETRIES:
                    print(f"  [镜头{req.clip_index}] 服务端 {res.status_code}，"
                          f"{config.AGNES_VIDEO_SUBMIT_RETRY_DELAY}s 后重试 "
                          f"({_submit_retry}/{config.AGNES_VIDEO_SUBMIT_RETRIES})")
                    time.sleep(config.AGNES_VIDEO_SUBMIT_RETRY_DELAY)
                    continue
                raise RuntimeError(
                    f"Agnes 视频提交失败 [{res.status_code}] (重试{_submit_retry}次): "
                    f"{res.text[:500]}"
                )

            # ---- 429: 速率限制，等待后重试 ----
            if res.status_code == 429:
                _submit_retry += 1
                if _submit_retry <= config.AGNES_VIDEO_SUBMIT_RETRIES:
                    delay = config.AGNES_VIDEO_SUBMIT_RETRY_DELAY + 30  # 至少比常规重试多等30s
                    print(f"  [镜头{req.clip_index}] 速率限制 429，"
                          f"{delay}s 后重试 "
                          f"({_submit_retry}/{config.AGNES_VIDEO_SUBMIT_RETRIES})")
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"Agnes 视频速率限制 [429] (重试{_submit_retry}次): "
                    f"{res.text[:500]}"
                )

            # ---- 403: 额度耗尽或无权限，尝试回退 ----
            if res.status_code == 403:
                next_model = self._advance_model()
                if next_model:
                    print(f"  [镜头{req.clip_index}] [!] 模型 {model} 返回 403"
                          f"（额度耗尽/无权限），自动切换到 {next_model}")
                    continue  # 用新模型重试
                else:
                    raise RuntimeError(
                        f"所有模型均返回 403，已无可用回退模型。"
                        f"尝试过: {', '.join(self._model_queue)}"
                    )

            # ---- 其他错误 ----
            raise RuntimeError(
                f"Agnes 视频提交失败 [{res.status_code}]: {res.text[:500]}"
            )

    # ------------------------------------------------------------------
    # URL 构造
    # ------------------------------------------------------------------
    def _submit_url(self) -> str:
        return f"{config.AGNES_BASE_URL.rstrip('/')}/v1/videos"

    def _query_url(self, video_id: str) -> str:
        """轮询端点: GET /agnesapi?video_id=<VIDEO_ID>."""
        return (f"{config.AGNES_BASE_URL.rstrip('/')}"
                f"{config.AGNES_VIDEO_POLL_ENDPOINT}?video_id={video_id}")

    # ------------------------------------------------------------------
    # 查询任务
    # ------------------------------------------------------------------
    def query_task(self, task_id: str) -> dict:
        try:
            res = requests.get(
                self._query_url(task_id),
                headers={"Authorization": f"Bearer {config.AGNES_API_KEY}"},
                timeout=120,
            )
        except Exception as e:
            raise RuntimeError(f"Agnes 查询异常: {e}")
        if res.status_code != 200:
            raise RuntimeError(
                f"Agnes 查询失败 [{res.status_code}]: {res.text[:300]}"
            )
        return res.json()

    # ------------------------------------------------------------------
    # 轮询等待 + 下载
    # ------------------------------------------------------------------
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str
    ) -> VideoGenerationResult:
        # sync 模式: task_id 实际是 video_url
        if task_id.startswith(_SYNC_PREFIX):
            return self._download(req, task_id[len(_SYNC_PREFIX):])

        attempt = 0
        while True:
            attempt += 1
            if attempt > config.AGNES_VIDEO_MAX_POLL_ATTEMPTS:
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"轮询超时 ({config.AGNES_VIDEO_MAX_POLL_ATTEMPTS} 次)",
                )

            time.sleep(config.AGNES_VIDEO_POLL_INTERVAL)
            output = self.query_task(task_id)
            status = self._extract_status(output)

            if status in ("SUCCEEDED", "SUCCESS", "COMPLETED", "DONE", "COMPLETE"):
                video_url = self._extract_video_url(output)
                if not video_url:
                    # 残量诊断日志，方便排查新增响应结构
                    print(f"  [镜头{req.clip_index}] [DEBUG] 未找到 video_url, "
                          f"响应: {str(output)[:500]}")
                video_url = self._extract_video_url(output)
                if not video_url:
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error="任务成功但未找到 video_url",
                    )
                return self._download(req, video_url)

            if status in ("FAILED", "FAIL", "ERROR"):
                reason = (output.get("message") or output.get("error")
                          or output.get("code") or "未知")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"API 失败: {reason}",
                )

            print(f"  [镜头{req.clip_index}] 生成中... ({status}, "
                  f"第 {attempt}/{config.AGNES_VIDEO_MAX_POLL_ATTEMPTS} 次轮询)")

    # ------------------------------------------------------------------
    # 下载视频到本地
    # ------------------------------------------------------------------
    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            save_path = os.path.join(config.OUTPUT_DIR, f"clip_{req.clip_index}.mp4")

            video_bin = requests.get(video_url, timeout=120).content
            with open(save_path, "wb") as f:
                f.write(video_bin)

            size_mb = len(video_bin) / (1024 * 1024)
            print(f"  [镜头{req.clip_index}] [OK] 已保存 -> {save_path} ({size_mb:.1f} MB)")
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
