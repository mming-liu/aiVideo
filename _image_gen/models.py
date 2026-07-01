"""图片生成数据模型 — 供应商无关."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ImageGenerationRequest:
    """单次图片生成请求."""

    prompt: str
    shot_index: int
    negative_prompt: str = ""
    size: str = "1024x1792"  # 9:16 竖屏
    reference_image_path: Optional[str] = None


@dataclass
class ImageGenerationResult:
    """单次图片生成结果."""

    success: bool
    save_path: Optional[str] = None
    error: Optional[str] = None
