"""标准数据结构：请求 / 响应"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VideoGenerationRequest:
    """单次视频生成请求.

    尺寸：推荐 9:16 竖屏用于短视频。
    num_frames 必须 <= 441 且遵循 8n+1 规则。

    多关键帧模式支持:
    - 仅 image_path: 普通图生视频 (1帧)
    - image_path + last_frame_image_path: 首尾双锚定 (2帧)
    - extra_keyframe_paths: 多关键帧叙事 (3-4帧)，实现 A->B->C->D 完整动作序列
    """

    prompt: str
    clip_index: int
    duration: int = 6
    negative_prompt: str = ""
    size: str = "720x1280"  # 9:16 竖屏
    # 首帧图本地路径（image-to-video）；None 或文件缺失时降级为纯 text-to-video
    image_path: Optional[str] = None
    # 尾帧图本地路径（KF2V 首尾帧模式专用）；None 时为普通 i2v
    last_frame_image_path: Optional[str] = None
    # 多关键帧路径列表（extra_body.image 数组），支持 3-4 帧完整动作序列
    # 优先级高于 last_frame_image_path（如果两者同时存在，取 extra_keyframe_paths）
    extra_keyframe_paths: list[str] = None
    # 可选：用于可复现结果（文档支持）
    seed: Optional[int] = None
    # 推理步数（Agnes API 支持）
    num_inference_steps: Optional[int] = None

    def __post_init__(self):
        if self.extra_keyframe_paths is None:
            self.extra_keyframe_paths = []


@dataclass
class VideoGenerationResult:
    """单次视频生成结果."""

    clip_index: int
    success: bool
    save_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BatchGenerationResult:
    """批量生成汇总."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"视频生成完毕：总计 {self.total} | 成功 {self.succeeded} | 失败 {self.failed}",
        ]
        for r in self.results:
            status = "OK" if r.success else f"FAIL: {r.error}"
            path = r.save_path or ""
            lines.append(f"  镜头{r.clip_index:>2}  [{status}]  {path}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)
