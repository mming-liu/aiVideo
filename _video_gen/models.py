"""标准数据结构：请求 / 响应"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VideoGenerationRequest:
    """单次视频生成请求."""

    prompt: str
    clip_index: int
    duration: int = 6
    negative_prompt: str = ""
    size: str = "960x1792"  # 9:16 竖屏
    # 首帧图本地路径（image-to-video）；None 或文件缺失时降级为纯 text-to-video
    image_path: Optional[str] = None
    # 尾帧图本地路径（KF2V 首尾帧模式专用）；None 时为普通 i2v
    last_frame_image_path: Optional[str] = None


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
