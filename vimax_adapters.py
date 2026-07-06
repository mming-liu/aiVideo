"""ViMax ↔ aiVideo 适配器.

实现 ViMax 的 ImageGenerator / VideoGenerator Protocol（vimax.tools.protocols），
把 ViMax 的图像/视频生成调用经 aiVideo 现有的 _image_gen / _video_gen provider
（DashScope / Agnes）完成，强制竖屏 9:16，复用 style_prefix 的前缀与合规化。

aiVideo 的 provider 是同步的（create_task → wait_for_completion → 下载），
这里用 asyncio.to_thread 包成 async；图片生成用 RateLimiter 控制并发速率，
不同摄像机间可真正并行（不同 adapter 实例各自独立，不再全局串行）。
"""

import asyncio
import os
from pathlib import Path
from typing import List, Optional

from PIL import Image

import _image_gen
import _video_gen
from _image_gen import ImageGenerationRequest
from _video_gen import VideoGenerationRequest
from style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt

from vimax.interfaces import ImageOutput, VideoOutput
from vimax.utils.rate_limiter import RateLimiter

# asyncio.to_thread 的总超时（秒），防止线程池线程永久卡住
_IMAGE_GEN_TIMEOUT = int(os.getenv("AGNES_IMAGE_GENERATE_TIMEOUT", "180"))

# 图片生成速率限制（从 .env 读取，None = 不限）
_IMAGE_RPM = os.getenv("IMAGE_MAX_REQUESTS_PER_MINUTE")
_IMAGE_RATE_LIMITER = RateLimiter(
    max_requests_per_minute=int(_IMAGE_RPM) if _IMAGE_RPM else None,
)


class ImageGeneratorAiVideoAdapter:
    """把 ViMax 图像生成调用接到 aiVideo 的 _image_gen provider。

    不同实例各自独立（无全局锁），由模块级 _IMAGE_RATE_LIMITER 统一控制速率，
    这样多摄像机可真正并行生成首帧/尾帧，同时避免请求过密被 API 限流。
    """

    def __init__(
        self,
        size: str = "1024x1792",  # 9:16 竖屏
        negative_prompt: str = NEGATIVE_PROMPT,
        skip_prefix: bool = False,    # 跳过风格前缀（白底肖像用）
        skip_sanitize: bool = False,  # 跳过合规词替换（英文 prompt 用）
        **_,
    ):
        self.size = size
        self.negative_prompt = negative_prompt
        self._skip_prefix = skip_prefix
        self._skip_sanitize = skip_sanitize
        self._svc = _image_gen.create_service(_image_gen.config.PROVIDER)

    async def generate_single_image(
        self,
        prompt: str,
        reference_image_paths: List[str] | None = None,
        **kwargs,
    ) -> ImageOutput:
        # 优先从 kwargs 取尺寸，其次实例默认
        size = kwargs.get("size", self.size)
        skip_prefix = kwargs.get("skip_prefix", self._skip_prefix)
        skip_sanitize = kwargs.get("skip_sanitize", self._skip_sanitize)
        shot_index = kwargs.get("shot_index", 0)
        ref = reference_image_paths[0] if reference_image_paths else None

        full_prompt = prompt
        if not skip_prefix:
            full_prompt = sanitize_prompt(ensure_prefix(prompt)) if not skip_sanitize else ensure_prefix(prompt)
        elif not skip_sanitize:
            full_prompt = sanitize_prompt(prompt)

        req = ImageGenerationRequest(
            prompt=full_prompt,
            shot_index=shot_index,
            negative_prompt=self.negative_prompt,
            size=size,
            reference_image_path=ref,
        )
        # 速率限制：控制图片 API 请求频率（多摄像机并行时不互相阻塞）
        await _IMAGE_RATE_LIMITER.acquire()
        result = await asyncio.wait_for(
            asyncio.to_thread(self._svc.generate, req),
            timeout=_IMAGE_GEN_TIMEOUT,
        )
        if not result.success or not result.save_path:
            raise RuntimeError(f"图像生成失败: {result.error}")
        img = Image.open(result.save_path)
        img.load()
        return ImageOutput(fmt="pil", ext="png", data=img)


class VideoGeneratorAiVideoAdapter:
    """把 ViMax 视频生成调用接到 aiVideo 的 _video_gen provider。"""

    def __init__(
        self,
        size: str = "960x1792",  # 9:16 竖屏（与 _video_gen 默认一致）
        duration: int = 5,
        negative_prompt: str = NEGATIVE_PROMPT,
        prefix: str = STYLE_PREFIX,
        **_,
    ):
        self.size = size
        self.duration = duration
        self.negative_prompt = negative_prompt
        self.prefix = prefix
        self._svc = _video_gen.create_service(_video_gen.config.PROVIDER)

    _VIDEO_SUBMIT_TIMEOUT = int(os.getenv("AGNES_VIDEO_SUBMIT_TIMEOUT", "180"))
    _VIDEO_FETCH_TIMEOUT = int(os.getenv("AGNES_VIDEO_FETCH_TIMEOUT", "1200"))

    async def submit_task(self, req) -> str:
        """提交视频生成任务，返回 task_id（不等待完成）."""
        return await asyncio.wait_for(
            asyncio.to_thread(self._svc.create_task, req),
            timeout=self._VIDEO_SUBMIT_TIMEOUT,
        )

    async def fetch_result(self, req, task_id: str) -> "_video_gen.VideoGenerationResult":
        """轮询等待任务完成并下载，返回结果."""
        return await asyncio.wait_for(
            asyncio.to_thread(self._svc.wait_for_completion, req, task_id),
            timeout=self._VIDEO_FETCH_TIMEOUT,
        )

    async def generate_single_video(
        self,
        prompt: str,
        reference_image_paths: List[str],
        **kwargs,
    ) -> VideoOutput:
        # 拼前缀 + 合规化（对齐 style_prefix.py）
        full_prompt = sanitize_prompt(ensure_prefix(prompt, self.prefix))
        # 首帧图：image-to-video；缺省降级为 text-to-video
        image_path = None
        if reference_image_paths:
            first = reference_image_paths[0]
            if first and Path(first).exists():
                image_path = first

        clip_index = kwargs.get("clip_index", 0)
        req = VideoGenerationRequest(
            prompt=full_prompt,
            clip_index=clip_index,
            duration=self.duration,
            negative_prompt=self.negative_prompt,
            size=self.size,
            image_path=image_path,
        )

        task_id = await self.submit_task(req)
        print(f"  [ViMax适配器] task_id={task_id}")
        result = await self.fetch_result(req, task_id)
        if not result.success or not result.save_path:
            raise RuntimeError(f"视频生成失败: {result.error}")
        with open(result.save_path, "rb") as f:
            data = f.read()
        return VideoOutput(fmt="bytes", ext="mp4", data=data)