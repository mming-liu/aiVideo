"""局部编辑的保护区域合成（S24）.

合成公式：result = edited * alpha + original * (1 - alpha)。

alpha 的硬约束：
- core bbox 之外严格为 0；
- 框内被排他的前景高亮衣物/肤色、前景绿植、exclude_bboxes 严格为 0；
- 只有 core 内的可编辑区域为 1，边界向内羽化。

因此合成后，alpha=0 的像素必须与原图逐字节一致，不依赖模型自觉。
"""
from __future__ import annotations

from PIL import Image, ImageFilter
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


def snap_bbox_to_borders(bbox_ratio, *, threshold: float = 0.05) -> tuple[float, float, float, float]:
    """距离画布边缘 <= threshold 的 bbox 边吸附到 0/1，避免贴边残留。"""
    if not 0.0 <= float(threshold) < 0.5:
        raise ValueError(f"threshold 必须在 [0, 0.5): {threshold!r}")
    x1, y1, x2, y2 = (float(v) for v in bbox_ratio)
    if x1 <= threshold:
        x1 = 0.0
    if y1 <= threshold:
        y1 = 0.0
    if x2 >= 1.0 - threshold:
        x2 = 1.0
    if y2 >= 1.0 - threshold:
        y2 = 1.0
    if not all(0.0 <= v <= 1.0 for v in (x1, y1, x2, y2)):
        raise ValueError(f"bbox 比例越界: {bbox_ratio!r}")
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"bbox 必须 x2>x1 且 y2>y1: {bbox_ratio!r}")
    return x1, y1, x2, y2


def _rgb_hsv(arr: np.ndarray):
    rgb = arr.astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mx = rgb.max(axis=2).astype(np.float32)
    mn = rgb.min(axis=2).astype(np.float32)
    v = mx
    s = np.where(mx == 0, 0.0, (mx - mn) / np.maximum(mx, 1.0) * 255.0)
    h = np.zeros_like(v, dtype=np.float32)
    d = np.maximum(mx - mn, 1.0)
    m = mx == r
    h[m] = ((g - b)[m] / d[m]) % 6
    m = (mx == g) & (mx != r)
    h[m] = ((b - r)[m] / d[m]) + 2
    m = (mx == b) & (mx != r) & (mx != g)
    h[m] = ((r - g)[m] / d[m]) + 4
    return h * 60.0, s, v


def _dilate(binary: np.ndarray, px: int) -> np.ndarray:
    if px <= 0 or not binary.any():
        return binary
    size = int(px) * 2 + 1
    im = Image.fromarray((binary.astype(np.uint8) * 255), mode="L")
    im = im.filter(ImageFilter.MaxFilter(size))
    return np.asarray(im) > 0


def _bbox_binary(size: tuple[int, int], bbox_ratio) -> np.ndarray:
    w, h = size
    x1, y1, x2, y2 = _validate_bbox(size, bbox_ratio)
    mask = np.zeros((h, w), dtype=bool)
    mask[y1:y2, x1:x2] = True
    return mask


def build_protection_alpha(
    original: Image.Image,
    bbox_ratio,
    *,
    feather: int = 8,
    snap_borders: bool = False,
    snap_threshold: float = 0.05,
    exclude_bboxes=(),
    protect_bright_garment: bool = False,
    protect_skin: bool = False,
    protect_foreground_foliage: bool = False,
    protect_dilate_px: int = 12,
    garment_bboxes=(),
    foliage_bboxes=(),
) -> np.ndarray:
    """构建 S24 alpha：core 外 0，core 内可编辑，保护对象严格 0。"""
    if feather < 0:
        raise ValueError("feather 不能为负")
    if protect_dilate_px < 0:
        raise ValueError("protect_dilate_px 不能为负")
    if snap_borders:
        bbox_ratio = snap_bbox_to_borders(bbox_ratio, threshold=snap_threshold)
    w, h = original.size
    x1, y1, x2, y2 = _validate_bbox((w, h), bbox_ratio)
    yy, xx = np.mgrid[0:h, 0:w]
    inside = (xx >= x1) & (xx < x2) & (yy >= y1) & (yy < y2)
    alpha = np.zeros((h, w), dtype=np.float32)
    if feather <= 0:
        alpha[inside] = 1.0
    else:
        # 向内羽化：core 边界 alpha=0，向内 feather 像素后到 1；
        # 这样 core 外始终严格 0，不会把编辑结果羽化到框外。
        d = np.minimum.reduce([xx - x1, x2 - 1 - xx, yy - y1, y2 - 1 - yy]).astype(np.float32)
        alpha = np.where(inside, np.clip(d / float(feather), 0.0, 1.0), 0.0)

    protected = np.zeros((h, w), dtype=bool)
    for b in exclude_bboxes or ():
        protected |= _bbox_binary((w, h), b)

    arr = np.asarray(original.convert("RGB"))
    hh, ss, vv = _rgb_hsv(arr)
    if protect_bright_garment:
        # 高亮白衣：框内这些像素必须保持原图，不能跟着船篷一起重画。
        bright = (vv >= 180) & (ss <= 75)
        if garment_bboxes:
            region = np.zeros((h, w), dtype=bool)
            for b in garment_bboxes:
                region |= _bbox_binary((w, h), b)
            bright &= region
        protected |= bright
    if protect_skin:
        # 肤色默认关闭：船篷/木船的暖棕也落在近似肤色区间，自动保护会吞掉
        # 可编辑船篷。确有脸/手在框内时用 exclude_bboxes 精确保护。
        protected |= ((vv >= 165) & (ss >= 30) & (ss <= 135)
                      & ((hh <= 15) | (hh >= 345)))
    if protect_foreground_foliage:
        green = (hh >= 30) & (hh <= 170) & (ss >= 40) & (vv >= 30)
        if foliage_bboxes:
            region = np.zeros((h, w), dtype=bool)
            for b in foliage_bboxes:
                region |= _bbox_binary((w, h), b)
            green &= region
        protected |= green

    protected = _dilate(protected, int(protect_dilate_px))
    alpha[protected] = 0.0
    # 对可编辑区域做轻微羽化，保护洞仍强制 0；core 外仍强制 0。
    if feather > 0:
        im = Image.fromarray((alpha * 255).astype(np.uint8), mode="L")
        im = im.filter(ImageFilter.GaussianBlur(radius=max(1.0, feather / 2.0)))
        alpha = np.asarray(im).astype(np.float32) / 255.0
    alpha[protected] = 0.0
    alpha[~inside] = 0.0
    return np.clip(alpha * 255.0, 0, 255).astype(np.uint8)


def compose_protected_edit(
    original: Image.Image,
    edited: Image.Image,
    bbox_ratio,
    *,
    feather: int = 8,
    snap_borders: bool = False,
    snap_threshold: float = 0.05,
    exclude_bboxes=(),
    protect_bright_garment: bool = False,
    protect_skin: bool = False,
    protect_foreground_foliage: bool = False,
    protect_dilate_px: int = 12,
    garment_bboxes=(),
    foliage_bboxes=(),
    return_alpha: bool = False,
):
    """把 edited 限制在 bbox 内，alpha=0 的区域严格保留 original。"""
    if original.size != edited.size:
        raise ValueError(f"原图与编辑图尺寸不一致: {original.size} vs {edited.size}")
    alpha = build_protection_alpha(
        original, bbox_ratio, feather=feather, snap_borders=snap_borders,
        snap_threshold=snap_threshold, exclude_bboxes=exclude_bboxes,
        protect_bright_garment=protect_bright_garment,
        protect_skin=protect_skin,
        protect_foreground_foliage=protect_foreground_foliage,
        protect_dilate_px=protect_dilate_px, garment_bboxes=garment_bboxes,
        foliage_bboxes=foliage_bboxes,
    )
    mask = Image.fromarray(alpha, mode="L")
    result = Image.composite(edited.convert("RGB"), original.convert("RGB"), mask)
    if return_alpha:
        return result, alpha
    return result


def verify_composite_integrity(
    original: Image.Image,
    result: Image.Image,
    alpha,
    *,
    raise_on_failure: bool = True,
) -> dict:
    """断言 alpha==0 区域 result 与 original 逐字节一致。"""
    if original.size != result.size:
        raise ValueError(f"原图与结果尺寸不一致: {original.size} vs {result.size}")
    a = np.asarray(original.convert("RGB")).astype(np.int16)
    b = np.asarray(result.convert("RGB")).astype(np.int16)
    mask = np.asarray(alpha) == 0
    protected = int(mask.sum())
    max_diff = 0
    if protected:
        diff = np.abs(a - b).max(axis=2)
        max_diff = int(diff[mask].max())
    ok = max_diff == 0
    report = {
        "ok": ok,
        "protected_pixels": protected,
        "max_protected_diff": max_diff,
        "total_pixels": int(mask.size),
    }
    if not ok and raise_on_failure:
        raise ValueError(f"保护区域被改动: max_protected_diff={max_diff}")
    return report
