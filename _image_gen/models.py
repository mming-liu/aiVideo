"""图片生成数据模型 — 供应商无关."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ImageGenerationRequest:
    """单次图片生成请求.

    尺寸推荐使用档位式：size="2K" + ratio="9:16"，而非精确像素。
    这样可确保输出尺寸可预期，避免自动标准化导致的意外尺寸。
    """

    prompt: str
    shot_index: int
    negative_prompt: str = ""
    # 默认使用档位式尺寸 + 9:16 竖屏（文档推荐方式）
    size: str = "2K"
    ratio: str = "9:16"
    reference_image_path: Optional[str] = None
    # 可选：用于可复现结果
    seed: Optional[int] = None


@dataclass
class ImageGenerationResult:
    """单次图片生成结果."""

    success: bool
    save_path: Optional[str] = None
    error: Optional[str] = None
