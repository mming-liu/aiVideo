"""S24 通用前景排他掩码回归（纯合成图，不读生产 output、不发外部请求）."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
import pytest

from shared.image_edit import (
    build_protection_alpha,
    compose_protected_edit,
    snap_bbox_to_borders,
    verify_composite_integrity,
)


CORE = (0.0, 0.30, 0.60, 0.90)


def _original() -> Image.Image:
    arr = np.zeros((300, 300, 3), dtype=np.uint8)
    arr[:, :] = (50, 40, 25)  # 深色船篷底
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((120, 100, 160, 160), fill=(245, 245, 245))  # 白色衣袖
    d.rectangle((10, 180, 60, 250), fill=(30, 120, 40))      # 前景绿荷叶
    return im


def test_snap_bbox_to_borders():
    assert snap_bbox_to_borders((0.04, 0.3, 0.6, 0.96)) == (0.0, 0.3, 0.6, 1.0)
    assert snap_bbox_to_borders((0.06, 0.3, 0.6, 0.94)) == (0.06, 0.3, 0.6, 0.94)
    with pytest.raises(ValueError):
        snap_bbox_to_borders((0.3, 0.3, 0.3, 0.6))


def test_alpha_zero_outside_core_and_on_protected_pixels():
    original = _original()
    alpha = build_protection_alpha(
        original, CORE, feather=0, protect_bright_garment=True,
        protect_foreground_foliage=True, protect_dilate_px=4,
        foliage_bboxes=[(0.0, 0.55, 0.25, 0.90)],
    )
    # core 外严格 0
    assert int(alpha[:90].max()) == 0
    assert int(alpha[:, 180:].max()) == 0
    # 白袖与前景荷叶严格 0
    assert int(alpha[130, 140]) == 0
    assert int(alpha[200, 30]) == 0
    # 可编辑船篷区域 > 0
    assert int(alpha[120, 80]) > 0


def test_composite_preserves_all_alpha_zero_pixels():
    original = _original()
    edited = Image.new("RGB", original.size, (180, 170, 150))
    result, alpha = compose_protected_edit(
        original, edited, CORE, feather=0, return_alpha=True,
        protect_bright_garment=True, protect_foreground_foliage=True,
        protect_dilate_px=4, foliage_bboxes=[(0.0, 0.55, 0.25, 0.90)],
    )
    assert result.getpixel((20, 20)) == original.getpixel((20, 20))
    assert result.getpixel((140, 130)) == original.getpixel((140, 130))
    assert result.getpixel((30, 200)) == original.getpixel((30, 200))
    assert result.getpixel((80, 120)) == edited.getpixel((80, 120))

    report = verify_composite_integrity(original, result, alpha)
    assert report["ok"] is True
    assert report["max_protected_diff"] == 0


def test_verify_detects_tampered_protected_pixel():
    original = _original()
    result, alpha = compose_protected_edit(
        original, Image.new("RGB", original.size, (180, 170, 150)),
        CORE, feather=0, return_alpha=True,
        protect_bright_garment=True, protect_foreground_foliage=True,
        protect_dilate_px=4, foliage_bboxes=[(0.0, 0.55, 0.25, 0.90)],
    )
    result.putpixel((20, 20), (255, 0, 0))
    with pytest.raises(ValueError):
        verify_composite_integrity(original, result, alpha)


def test_repair_plan_outputs_snapped_defect_and_protect_bboxes():
    from types import SimpleNamespace
    from shared.image_qc import build_repair_plan

    result = SimpleNamespace(
        issues=[SimpleNamespace(category="prompt_semantics",
                                description="画面出现船篷", severity="critical")],
        entities=[
            {"label": "person", "desc": "white robe, standing", "bbox": [100, 200, 300, 800]},
            {"label": "boat", "bbox": [0, 450, 370, 640]},
        ],
    )
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit"
    assert plan["defect_bbox"][0] == 0.0
    assert plan["protect_bboxes"] == [(0.1, 0.2, 0.3, 0.8)]


def test_explicit_protect_mask_is_strict_zero():
    original = _original()
    mask = np.zeros((300, 300), dtype=bool)
    mask[110:150, 70:110] = True
    alpha = build_protection_alpha(original, CORE, feather=0, protect_masks=[mask])
    assert int(alpha[120, 80]) == 0
    assert int(alpha[120, 150]) > 0


def test_exclude_bboxes_are_strict_zero():
    original = _original()
    alpha = build_protection_alpha(
        original, CORE, feather=0, exclude_bboxes=[(0.40, 0.40, 0.50, 0.50)],
    )
    assert int(alpha[135, 135]) == 0
    assert int(alpha[110, 90]) > 0
