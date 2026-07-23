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
    # 推理步数（Agnes API 支持，默认 20）
    num_inference_steps: Optional[int] = None
    # 帧描述（用于区分首帧/尾帧/中间帧日志），可选
    frame_desc: str = ""
    # 启用图片幻觉检测（默认开启）
    enable_hallucination_detection: bool = True
    # 期望出现的物体列表（用于检测多余物体）
    expected_objects: Optional[list[str]] = None


@dataclass
class ImageGenerationResult:
    """单次图片生成结果."""

    success: bool
    save_path: Optional[str] = None
    error: Optional[str] = None
    # 原始图片 URL（用于图像检测）
    image_url: Optional[str] = None
    # 幻觉检测结果
    hallucination_detected: bool = False
    hallucination_issues: Optional[str] = None  # JSON string of issues