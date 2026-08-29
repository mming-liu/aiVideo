# -*- coding: utf-8 -*-
"""v6.54.1 QC 驱动局部修复单元测试：build_repair_plan 分类.

局部失败（睁眼/刀接触/红痕/篷/发光）→ edit（bbox 并集）；全局失败（人数/
复制/动作错）或 bbox 不可得/区域过大 → regenerate。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.image_qc import (
    QcIssue,
    QcResult,
    _bbox_area_pct,
    _select_bboxes_for_pattern,
    _union_bboxes,
    build_repair_plan,
)


def _result(issues: list[QcIssue], entities: list[dict] | None) -> QcResult:
    return QcResult(has_hallucination=bool(issues), issues=issues, ok=True,
                    entities=entities)


# 与 L1 检测输出同构的实体（0-1000 归一 bbox）
_ENTITIES = [
    {"label": "person", "desc": "black outfit, masked, holding sword, kneeling",
     "bbox": [40, 380, 330, 860]},
    {"label": "person", "desc": "white robe lying supine, eyes open",
     "bbox": [300, 500, 900, 720]},
    {"label": "person", "desc": "light blue hanfu standing at stern",
     "bbox": [620, 120, 830, 420]},
    {"label": "boat", "desc": "narrow wooden sampan, open deck",
     "bbox": [250, 420, 980, 760]},
]

_CRIT = lambda cat, d: QcIssue(category=cat, description=d, severity="critical")


def test_knife_contact_edit_with_union_bbox():
    """刀接触 critical → edit；bbox = 持刀者 ∪ 躺卧者."""
    result = _result([_CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit"
    assert plan["matched_patterns"] == ["knife_contact"]
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 持刀者(40,380,330,860) ∪ 躺卧者(300,500,900,720) → (40,380,900,860) → /1000
    assert (x1, y1, x2, y2) == (0.04, 0.38, 0.9, 0.86)
    assert "悬停" in plan["edit_prompt"] and "红框" in plan["edit_prompt"]


def test_red_mark_edit_all_persons_union():
    result = _result([_CRIT("anchor_mismatch", "「画面任何人身上是否出现红色痕迹、伤口或血迹？」答 yes，期望 no")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit" and plan["matched_patterns"] == ["red_mark"]
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 三人并集 (40,120,900,860)
    assert (x1, y1, x2, y2) == (0.04, 0.12, 0.9, 0.86)


def test_eyes_closed_face_top45_math():
    """睁眼断言 → 躺卧者框顶 45%（脸部）."""
    result = _result([_CRIT("anchor_mismatch", "「画面是否表现：仰面朝上双目紧闭？」答 no，期望 yes")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit" and plan["matched_patterns"] == ["eyes_closed"]
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 躺卧者 (300,500,900,720)：y2 = 500 + 220*0.45 = 599
    assert (x1, y1, x2, y2) == (0.3, 0.5, 0.9, 0.599)


def test_person_count_regenerate():
    """人数错=全局 → regenerate（局部编辑修不了构图）."""
    result = _result([_CRIT("extra_person", "图中实体人数 4 vs 名单 3（容差 0）")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "regenerate"


def test_mixed_local_and_global_regenerate():
    """刀接触（局部）+ 人数（全局）混合 → regenerate（任一不匹配即全局）."""
    result = _result([
        _CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no"),
        _CRIT("extra_person", "图中实体人数 4 vs 名单 3（容差 0）"),
    ], _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "regenerate"
    assert "非局部" in plan["reason"]


def test_no_entities_regenerate():
    """检测层无实体（bbox 不可得）→ regenerate."""
    result = _result([_CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no")],
                     None)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "regenerate" and "bbox" in plan["reason"]


def test_area_over_70_regenerate():
    """修复区域并集 >70% 画幅 → 非局部，regenerate."""
    huge = [{"label": "person", "desc": "white robe lying", "bbox": [0, 0, 1000, 800]}]
    result = _result([_CRIT("anchor_mismatch", "「画面任何人身上是否出现红色痕迹、伤口或血迹？」答 yes，期望 no")],
                     huge)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "regenerate" and "画幅" in plan["reason"]


def test_no_critical_no_edit():
    result = QcResult(has_hallucination=False, issues=[], ok=True, entities=_ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "regenerate" and plan["reason"] == "无 critical"


def test_canopy_boat_upper_half():
    """篷断言 → 非人实体（船）框上半."""
    result = _result([_CRIT("anchor_mismatch", "「画面中是否出现「篷」？」答 yes，期望 no")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit" and plan["matched_patterns"] == ["canopy"]
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 船 (250,420,980,760) 上半 → y2 = 420 + 170 = 590
    assert (x1, y1, x2, y2) == (0.25, 0.42, 0.98, 0.59)


def test_bbox_utils_math():
    assert _union_bboxes([[10, 20, 300, 400], [200, 100, 500, 600]]) == (10, 20, 500, 600)
    assert abs(_bbox_area_pct((0, 0, 500, 400)) - 20.0) < 1e-6
    # _is_lying 的宽高比分支（横宽人形）
    from shared.image_qc import _is_lying
    assert _is_lying({"desc": "", "bbox": [0, 0, 800, 300]}) is True
    assert _is_lying({"desc": "standing tall", "bbox": [0, 0, 200, 600]}) is False


def test_select_bboxes_filters_invalid():
    from shared.image_qc import _select_bboxes_for_pattern
    bad = [{"label": "person", "desc": "white robe lying", "bbox": None},
           {"label": "person", "desc": "white robe lying", "bbox": [100, 200, 400, 600]}]
    boxes = _select_bboxes_for_pattern("lying_face_top45", bad, bad)
    assert len(boxes) == 1 and abs(boxes[0][3] - (200 + 400 * 0.45)) < 1e-6
