"""图片局部编辑的保护区域合成.

只把编辑结果放进目标 bbox；bbox 外恢复原图像素，边界按 feather 做线性过渡。
用于首帧船篷这类局部修复：人物、伞、船头等框外区域必须保持原样，不能依赖
模型自觉。
"""
from __future__ import annotations

from PIL import Image
import numpy as np


def _validate_bbox(size: tuple[int, int], bbox_ratio) -> tuple[int, int, int, int]:
    w, h = size
    try:
        x1r, y1r, x2r, y2r = (float(v) for v in bbox_ratio)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bbox 必须是 4 个 0-1 比例: {bbox_ratio!r}") from exc
    if not all(0.0 <= v <= 1.0 for v in (x1r, y1r, x2r, y2r)):
        raise ValueError(f"bbox 比例越界: {bbox_ratio!r}")
    if x2r <= x1r or y2r <= y1r:
        raise ValueError(f"bbox 必须 x2>x1 且 y2>y1: {bbox_ratio!r}")
    x1, y1 = int(round(x1r * w)), int(round(y1r * h))
    x2, y2 = int(round(x2r * w)), int(round(y2r * h))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"bbox 落到像素后为空: {bbox_ratio!r}")
    return x1, y1, x2, y2


def _mask(size: tuple[int, int], bbox_ratio, feather: int) -> Image.Image:
    w, h = size
    x1, y1, x2, y2 = _validate_bbox(size, bbox_ratio)
    if feather <= 0:
        arr = np.zeros((h, w), dtype=np.uint8)
        arr[y1:y2, x1:x2] = 255
        return Image.fromarray(arr, mode="L")
    yy, xx = np.mgrid[0:h, 0:w]
    dx = np.maximum(np.maximum(x1 - xx, xx - x2), 0)
    dy = np.maximum(np.maximum(y1 - yy, yy - y2), 0)
    dist = np.sqrt(dx * dx + dy * dy)
    alpha = np.clip(1.0 - dist / float(feather), 0.0, 1.0)
    return Image.fromarray((alpha * 255).astype(np.uint8), mode="L")


def compose_protected_edit(original: Image.Image, edited: Image.Image,
                           bbox_ratio, *, feather: int = 8) -> Image.Image:
    """把 edited 限制在 bbox 内，bbox 外严格保留 original.

    feather>0 时只在 bbox 外一圈做线性过渡；离 bbox 超过 feather 的像素
    与原图逐字节一致。
    """
    if original.size != edited.size:
        raise ValueError(f"原图与编辑图尺寸不一致: {original.size} vs {edited.size}")
    if feather < 0:
        raise ValueError("feather 不能为负")
    mask = _mask(original.size, bbox_ratio, feather)
    return Image.composite(edited.convert("RGB"), original.convert("RGB"), mask)
