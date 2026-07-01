"""agnes ai 视频生成 — OpenAI 兼容协议 (image-to-video + 异步轮询).

提交: POST {AGNES_BASE_URL}{AGNES_VIDEO_ENDPOINT}
轮询: GET  {AGNES_BASE_URL}{AGNES_VIDEO_ENDPOINT}/{task_id}

支持 image-to-video: 若 req.image_path 指向有效文件，读取后 base64 编码为 data URL
放入 AGNES_VIDEO_IMAGE_FIELD (默认 image) 字段; 否则降级为纯 text-to-video.

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
    """agnes ai (OpenAI 兼容) 视频生成实现."""

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
    def _encode_image(self, path: Optional[str]) -> Optional[str]:
        """读取本地图片，返回 data URL (base64). 失败返回 None."""
        if not path:
            return None
        try:
            p = os.path.abspath(path)
            if not os.path.isfile(p):
                return None
            mime, _ = mimetypes.guess_type(p)
            mime = mime or "image/png"
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            return None

    # ------------------------------------------------------------------
    # URL 构造
    # ------------------------------------------------------------------
    def _submit_url(self) -> str:
        return f"{config.AGNES_BASE_URL.rstrip('/')}{config.AGNES_VIDEO_ENDPOINT}"

    def _query_url(self, task_id: str) -> str:
        return f"{self._submit_url()}/{task_id}"

    # ------------------------------------------------------------------
    # 响应字段解析（兼容多种 OpenAI 风格包装）
    # ------------------------------------------------------------------
    def _extract_task_id(self, data: dict) -> Optional[str]:
        for key in ("task_id", "id"):
            v = data.get(key)
            if v:
                return str(v)
        d = data.get("data") or {}
        if isinstance(d, dict):
            for key in ("task_id", "id"):
                v = d.get(key)
                if v:
                    return str(v)
        return None

    def _extract_video_url(self, output: dict) -> Optional[str]:
        for key in ("video_url", "url"):
            v = output.get(key)
            if v:
                return str(v)
        urls = output.get("video_urls")
        if urls and isinstance(urls, list) and urls[0]:
            return str(urls[0])
        d = output.get("data") or {}
        if isinstance(d, dict):
            for key in ("video_url", "url"):
                v = d.get(key)
                if v:
                    return str(v)
        return None

    def _extract_status(self, output: dict) -> str:
        s = output.get("status") or output.get("task_status") or ""
        return str(s).upper()

    # ------------------------------------------------------------------
    # 创建任务（带 403 回退）
    # ------------------------------------------------------------------
    def create_task(self, req: VideoGenerationRequest) -> str:
        size = req.size or config.VIDEO_SIZE
        image_data_url = self._encode_image(req.image_path)
        if req.image_path and not image_data_url:
            print(f"  [镜头{req.clip_index}] ⚠ 首帧图 {req.image_path} 读取失败，"
                  f"降级为 text-to-video")

        while True:
            model = self._current_model
            headers = {
                "Authorization": f"Bearer {config.AGNES_API_KEY}",
                "Content-Type": "application/json",
            }
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "size": size,
            }
            if req.duration:
                payload["duration"] = req.duration
            if req.negative_prompt:
                payload["negative_prompt"] = req.negative_prompt
            if image_data_url:
                payload[config.AGNES_VIDEO_IMAGE_FIELD] = image_data_url

            try:
                res = requests.post(
                    self._submit_url(), json=payload, headers=headers, timeout=120
                )
            except Exception as e:
                raise RuntimeError(f"Agnes 视频提交异常: {e}")

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
                task_id = self._extract_task_id(data)
                if not task_id:
                    raise RuntimeError(
                        f"Agnes 未返回 task_id: {str(data)[:300]}"
                    )
                mode_tag = "image-to-video" if image_data_url else "text-to-video"
                print(f"  [镜头{req.clip_index}] 任务已创建 [{model}] ({mode_tag}) "
                      f"-> task_id: {task_id}")
                return task_id

            # ---- 403: 额度耗尽或无权限，尝试回退 ----
            if res.status_code == 403:
                next_model = self._advance_model()
                if next_model:
                    print(f"  [镜头{req.clip_index}] ⚠ 模型 {model} 返回 403"
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

            if status in ("SUCCEEDED", "SUCCESS", "COMPLETED", "DONE"):
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
