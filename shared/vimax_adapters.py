"""ViMax ↔ aiVideo 适配器.

实现 ViMax 的 ImageGenerator / VideoGenerator Protocol（vimax.tools.protocols），
把 ViMax 的图像/视频生成调用经 aiVideo 现有的 _image_gen / _video_gen provider
（DashScope / Agnes）完成，强制竖屏 9:16，复用 style_prefix 的前缀与合规化。

aiVideo 的 provider 是同步的（create_task → wait_for_completion → 下载），
这里用 asyncio.to_thread 包成 async；并发由全局 Semaphore(N) 控制，
与底层 KeyScheduler（每 Key 独立冷却）配合，实现 Nx 吞吐量提升。
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
from style_prefix import STYLE_PREFIX, STYLE_PREFIX_SHORT, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt
from style_prefix import IMAGE_HALLUCINATION_LOCK_PROMPT, IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT, IMAGE_HALLUCINATION_EXTRA_NEGATIVE

from vimax.interfaces import ImageOutput, VideoOutput

# asyncio.to_thread 的总超时（秒），防止线程池线程永久卡住
_IMAGE_GEN_TIMEOUT = int(os.getenv("AGNES_IMAGE_GENERATE_TIMEOUT", "180"))


class ImageGeneratorAiVideoAdapter:
    """把 ViMax 图像生成调用接到 aiVideo 的 _image_gen provider。

    默认使用 asyncio.Semaphore(8) 控制并发数，让 8 个 API Key 可同时提交任务，
    每个 Key 独立冷却（KeyScheduler），实现 8x 吞吐量提升。
    不同实例各自独立（无全局锁），这样多摄像机可真正并行生成首帧/尾帧。
    """

    # 全局并发信号量：最多同时提交 8 个任务（对应 8 个 API Key，Bailian 单账号自动降为 1）
    _global_semaphore: "asyncio.Semaphore | None" = None
    _MAX_CONCURRENT = int(os.getenv("IMAGE_MAX_CONCURRENT", "0"))  # 0=自动

    def __init__(
        self,
        size: str = "1024x1792",  # 9:16 竖屏
        negative_prompt: str = NEGATIVE_PROMPT,
        skip_prefix: bool = False,    # 跳过风格前缀（白底肖像用）
        skip_sanitize: bool = False,  # 跳过合规词替换（英文 prompt 用）
        enable_hallucination_lock: bool = True,  # 启用图片幻觉锁图（默认开启）
        **_,
    ):
        self.size = size
        # 追加图片防幻觉负向词
        self.negative_prompt = f"{negative_prompt}、{IMAGE_HALLUCINATION_EXTRA_NEGATIVE}"
        self._skip_prefix = skip_prefix
        self._skip_sanitize = skip_sanitize
        self._enable_hallucination_lock = enable_hallucination_lock
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
        frame_desc = kwargs.get("frame_desc", "")
        # 参考图：根据 IMAGE_MULTI_REF 决定传单张还是多张
        # 1（默认）= 只取第一张（向后兼容）；2+ = 传入全部（上限 IMAGE_MULTI_REF）
        # force_multi_ref（首帧/尾帧生成路径）: 场景+角色+道具混合参考必须全部传入，
        # 不受 IMAGE_MULTI_REF 限制——单张参考无法同时锚定场景/人物/道具三要素。
        force_multi_ref = bool(kwargs.get("force_multi_ref", False))
        from shared.config import IMAGE_MULTI_REF
        refs = reference_image_paths or []
        if (force_multi_ref or IMAGE_MULTI_REF >= 2) and len(refs) > 1:
            ref_paths = refs[:max(IMAGE_MULTI_REF, len(refs))] if force_multi_ref else refs[:IMAGE_MULTI_REF]
            ref = None  # 多张走 reference_image_paths
        else:
            ref_paths = []
            ref = refs[0] if refs else None

        # 单图模型兜底：provider 不支持多参考图时，把多张肖像拼成单张参考表
        if ref_paths and not getattr(self._svc, "supports_multi_ref", True):
            try:
                from shared.portrait_stitch import stitch_portraits
                from shared.paths import vimax_working_dir
                sheet_path = vimax_working_dir() / "shots" / str(shot_index) / "ref_sheet.png"
                stitched = stitch_portraits(ref_paths, str(sheet_path))
                if stitched:
                    ref = stitched
                    ref_paths = []  # 降级为单张
            except Exception:
                # 拼图失败则退回第一张，保证不中断生成
                ref = ref_paths[0]
                ref_paths = []

        full_prompt = prompt
        if not skip_prefix:
            full_prompt = sanitize_prompt(ensure_prefix(prompt)) if not skip_sanitize else ensure_prefix(prompt)
        elif not skip_sanitize:
            full_prompt = sanitize_prompt(prompt)
        else:
            full_prompt = prompt

        # 图片防幻觉锁图关键词注入（仅在启用时）
        # 默认用完整锁图（含"场景布局不变"）；尾帧/关键帧通过 lock_prompt 传入
        # 无布局锁版本，允许构图随动作演变（详见 style_prefix.py 注释）。
        if self._enable_hallucination_lock:
            lock_prompt = kwargs.get("lock_prompt", IMAGE_HALLUCINATION_LOCK_PROMPT)
            full_prompt = f"{lock_prompt}，{full_prompt}"

        req = ImageGenerationRequest(
            prompt=full_prompt,
            shot_index=shot_index,
            negative_prompt=self.negative_prompt,
            size=size,
            # 显式保存路径：资产图/肖像等非帧图直接落盘，避免写默认 shot_N_ff.png
            save_path=kwargs.get("save_path"),
            # 纵横比透传（资产图传 16:9；帧图用默认 9:16）
            ratio=kwargs.get("ratio", "9:16"),
            reference_image_path=ref,
            reference_image_paths=ref_paths,
            frame_desc=frame_desc,
            # 传递幻觉检测开关（可选禁用）
            enable_hallucination_detection=self._enable_hallucination_lock,
            # 从 kwargs 传递期望物体列表
            expected_objects=kwargs.get("expected_objects"),
        )
        # 全局并发信号量：自动根据当前供应商调整并发数
        if ImageGeneratorAiVideoAdapter._global_semaphore is None:
            # Bailian 只有 1 个账号，只能串行；Agnes 有 8 个 Key 可并行
            if ImageGeneratorAiVideoAdapter._MAX_CONCURRENT > 0:
                concurrency = ImageGeneratorAiVideoAdapter._MAX_CONCURRENT
            else:
                from _image_gen.config import config as _img_config
                concurrency = 1 if _img_config.PROVIDER == "bailian" else 8
            ImageGeneratorAiVideoAdapter._global_semaphore = asyncio.Semaphore(concurrency)

        # 并发控制：Semaphore 保证同时只有 N 个任务在飞，超出排队等待
        async with ImageGeneratorAiVideoAdapter._global_semaphore:
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
    """把 ViMax 视频生成调用接到 aiVideo 的 _video_gen provider。

    提交阶段使用 Semaphore(8) 控制并发，让 8 个 Key 同时提交任务，
    提交后异步轮询各任务（不占用 Semaphore 名额）。
    """

    # 全局并发信号量：最多同时提交 8 个任务（对应 8 个 API Key，Bailian 自动降为 1）
    _global_semaphore: "asyncio.Semaphore | None" = None
    _MAX_CONCURRENT = int(os.getenv("VIDEO_MAX_CONCURRENT", "0"))  # 0=自动

    def __init__(
        self,
        size: str = "960x1792",  # 9:16 竖屏（与 _video_gen 默认一致）
        duration: int = 5,
        negative_prompt: str = NEGATIVE_PROMPT,
        prefix: str = STYLE_PREFIX_SHORT,
        **_,
    ):
        self.size = size
        self.duration = duration
        self.negative_prompt = negative_prompt
        self.prefix = prefix
        self._svc = _video_gen.create_service(_video_gen.config.PROVIDER)

    def get_key_index(self) -> int:
        """返回当前使用的 key 索引."""
        return self._svc.get_key_index()

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

        # 全局并发信号量：自动根据当前供应商调整并发数
        if VideoGeneratorAiVideoAdapter._global_semaphore is None:
            if VideoGeneratorAiVideoAdapter._MAX_CONCURRENT > 0:
                concurrency = VideoGeneratorAiVideoAdapter._MAX_CONCURRENT
            else:
                from _video_gen.config import VideoGenCfg
                concurrency = 1 if VideoGenCfg.PROVIDER == "bailian" else 8
            VideoGeneratorAiVideoAdapter._global_semaphore = asyncio.Semaphore(concurrency)

        # 提交阶段：Semaphore 控制并发（等有空位再提交）
        async with VideoGeneratorAiVideoAdapter._global_semaphore:
            task_id = await self.submit_task(req)
        print(f"  [ViMax适配器] task_id={task_id}")

        # 轮询阶段：提交后释放 Semaphore，允许下一个任务提交，当前任务异步等待
        result = await self.fetch_result(req, task_id)
        if not result.success or not result.save_path:
            raise RuntimeError(f"视频生成失败: {result.error}")
        with open(result.save_path, "rb") as f:
            data = f.read()
        return VideoOutput(fmt="bytes", ext="mp4", data=data)