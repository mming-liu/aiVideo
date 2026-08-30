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


def test_knife_contact_edit_with_intersection_bbox():
    """刀接触 critical → edit；bbox = 持刀者 ∩ 躺卧者 + 20% 边距（v6.32.1）.

    旧版用并集 → 区域超 70% 画幅 → 触发 regenerate 闸口，局部修复永不触发。
    新版用交集：接触点所在即两框重叠处，向外扩 20% 边距覆盖接触带周边。
    """
    result = _result([_CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit"
    assert plan["matched_patterns"] == ["knife_contact"]
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 持刀者 (40,380,330,860) ∩ 躺卧者 (300,500,900,720) = (300,500,330,720)
    # 交集宽 = 30, 高 = 220 → 20% 边距 = 6 (x), 44 (y)
    # 扩后 = (294, 456, 336, 764) → /1000 = (0.294, 0.456, 0.336, 0.764)
    assert abs(x1 - 0.294) < 1e-6
    assert abs(y1 - 0.456) < 1e-6
    assert abs(x2 - 0.336) < 1e-6
    assert abs(y2 - 0.764) < 1e-6
    assert "悬停" in plan["edit_prompt"] and "红框" in plan["edit_prompt"]


def test_knife_contact_no_intersection_connecting_band():
    """持刀者与躺卧者不重叠（间隙小）→ 取连接带."""
    entities = [
        {"label": "person", "desc": "black outfit holding sword standing",
         "bbox": [100, 200, 250, 600]},   # 持刀者
        {"label": "person", "desc": "white robe lying supine",
         "bbox": [350, 300, 700, 600]},   # 躺卧者，x 间隙 = 100
    ]
    result = _result([_CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no")],
                     entities)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit"
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 间隙在 x = 100；较小框短边 = min(150, 350) = 150；连接带带宽 = 150/4 = 37.5
    # x 范围 (250, 350)；y 范围取两框重叠区间 (300, 600) + 37.5/2 上下扩
    # = (281.25, 618.75) → /1000
    # 验证连接带小于并集（修复区域更精准）
    assert (x2 - x1) * (y2 - y1) < 0.4  # 远小于 0.86（旧版并集）


def test_knife_contact_too_far_union_fallback():
    """持刀者与躺卧者间隙 > 较大框短边 → 兜底用并集（罕见）."""
    entities = [
        {"label": "person", "desc": "black outfit holding sword",
         "bbox": [50, 100, 150, 200]},   # 持刀者 100x100
        {"label": "person", "desc": "white robe lying supine",
         "bbox": [800, 500, 950, 700]},  # 躺卧者，x 间隙 = 650 > 100
    ]
    result = _result([_CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no")],
                     entities)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit"
    x1, y1, x2, y2 = plan["bbox_ratio"]
    # 兜底并集：(50, 100, 950, 700) → /1000
    assert (x1, y1, x2, y2) == (0.05, 0.1, 0.95, 0.7)


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
    """v6.x.x A 项：人数错=全局 → 走 extra_person 修图（small_person 选区）.

    旧版一刀切 regenerate（v6.51）；新版把"图中实体人数 vs 名单"加入
    _LOCALIZED_FIX_PATTERNS，选区 smaller_person 排除最大人物，触发 i2i
    "减少 1 个人物" 修复。
    """
    result = _result([_CRIT("extra_person", "图中实体人数 4 vs 名单 3（容差 0）")],
                     _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit", \
        f"人数错现在走 extra_person 修复（smaller_person 选区），实测 {plan['action']}"
    assert "extra_person" in plan["matched_patterns"]


def test_mixed_local_and_global_regenerate():
    """v6.x.x：刀接触（局部）+ 人数（全局）混合 → 走 extra_person 修复（非局部不再卡全局）.

    旧版"任一不匹配即全局"过于严苛——人数错和刀接触是两件独立的事，
    各自有局部修复路径。新版两者都命中模式 → 走 edit 模式。
    """
    result = _result([
        _CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no"),
        _CRIT("extra_person", "图中实体人数 4 vs 名单 3（容差 0）"),
    ], _ENTITIES)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "edit", \
        f"混合命中都走局部修复（knife + extra_person），实测 {plan['action']}"


def test_no_entities_regenerate():
    """检测层无实体（bbox 不可得）→ regenerate."""
    result = _result([_CRIT("anchor_mismatch", "「刀具是否已接触或切入任何人的身体？」答 yes，期望 no")],
                     None)
    plan = build_repair_plan({}, "first_frame", result)
    assert plan["action"] == "regenerate" and "bbox" in plan["reason"]


def test_area_over_70_regenerate():
    """v6.x.x：area 护栏按模式分阈值——knife_contact 严守 70%（特小区域合理）.

    实抽验证：旧版一刀切 70% 让其他模式（glowing/eyes_closed）命中率 0%，
    因为 multi-person 场景下 union 面积天然 80%+。新版 knife_contact 仍 70%
    （接触带小区域保护），其他模式放宽到 95%。
    """
    # 80% 画幅 + 持刀者（desc 含"持匕首"触发 _is_knife_holder）→ knife_zone 出 bbox
    # → 80% > 70% knife 阈值 → regenerate
    huge = [{"label": "person", "desc": "持匕首的人，lying", "bbox": [0, 0, 1000, 800]}]
    result = _result([_CRIT("knife_contact", "刀具是否已接触或切入任何人的身体？")],
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
