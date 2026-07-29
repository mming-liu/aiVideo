"""图片生成数据模型 — 供应商无关."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImageGenerationRequest:
    """单次图片生成请求.

    尺寸推荐使用档位式：size="2K" + ratio="9:16"，而非精确像素。
    这样可确保输出尺寸可预期，避免自动标准化导致的意外尺寸。

    参考图：
    - reference_image_path: 单张参考图（向后兼容）
    - reference_image_paths: 多张参考图列表（同框多角色时各自传入肖像）
    两者同时存在时，以 reference_image_paths 为准。
    """

    prompt: str
    shot_index: int
    negative_prompt: str = ""
    # 默认使用档位式尺寸 + 9:16 竖屏（文档推荐方式）
    size: str = "2K"
    ratio: str = "9:16"
    reference_image_path: Optional[str] = None
    # 多张参考图列表（同框多角色时各自传入肖像，提升人物一致性）
    reference_image_paths: list[str] = field(default_factory=list)
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

    def __post_init__(self):
        # 向后兼容：单张 reference_image_path 自动并入列表
        if self.reference_image_path and not self.reference_image_paths:
            self.reference_image_paths = [self.reference_image_path]


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