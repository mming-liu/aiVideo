# -*- coding: utf-8 -*-
"""承载单元的**时间与顺序语义**（第十五~十六轮复核）.

复核两轮把同一件事说透：**表达方式不等于时间是否独占**。

  · **第十五轮**：9 镜 × 10 秒、36 个节拍的稿被判「至少 108 秒」——因为下界按"节拍数 ×
    3 秒"算；当时只把**声音节拍**摘出来（它们与发声同时发生），画面节拍仍逐个乘 3 秒。
  · **第十六轮**：画面节拍同样不能乘——反例是「9 镜共 90 秒，每镜 4 个画面节拍 + 1 个
    对白节拍」，事件契约通过、下界仍报 108 秒。生成契约本就写着「**节拍数 ≠ 镜头数**……
    **镜数由时长预算决定，不由节拍数决定**」，`timecode` 的镜头时长也由文本与 motion_desc
    推出——全链路没有一处按节拍分配时间。⇒ **节拍不构成时间下界**，下界只取发声预算。

顺序侧同理：判据必须看**实际承载区间与实际落点**，不能只看声明数组——
`[1]→[2]→[1]→[3]`（隔镜回到已结束的节拍）与"正文先说 D2 却声明 `[1,2]`"都在这里钉住。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import plan_feasibility as pf  # noqa: E402
from step01_script_gen.plan_feasibility import (  # noqa: E402
    beats_floor_basis, check_event_contract, evaluate_plan, plan_dialogue_lines,
    plan_identity)


def _refs(beats=(), lines=()):
    return {"beats": list(beats), "lines": list(lines)}


def _beat(body, carrier) -> str:
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, body, carrier)


# ---------------------------------------------------------------------------
# ① 节拍**不构成时间下界**（第十六轮复核 P1-①）
# ---------------------------------------------------------------------------
def test_beats_do_not_constitute_a_time_lower_bound():
    """复核反例：9 镜 × 10 秒、每镜 4 个画面节拍 + 1 个对白节拍 ⇒ 下界**不是** 108 秒.

    判别性：把「节拍数 × MIN_SHOT_DURATION」加回下界 ⇒ 本用例读到 108.0。
    """
    beats = [_beat("画面节拍%d" % k, "画面") for k in range(1, 37)]      # 36 个画面节拍
    line = "他嗯了一声"
    beats.append(_beat("对白节拍", "台词1"))                            # 1 个对白节拍
    plan = {"main_events": ["事件1", "事件2", "事件3"], "climax": "船头对峙",
            "required_facts": ["她被杀｜原文证据：“她被杀”｜承载：B1/画面"],
            "causal_chain": ["起因→行动→结果→悬念"], "shot_beats": beats,
            "dialogue_lines": [line], "omittable": [], "hook_spoken": [],
            "time_budget": "对白约10秒；动作约30秒"}
    rep = evaluate_plan(plan, novel_text="她被杀。%s。" % line)

    assert rep["lower_bound_basis"] == "voice", rep
    assert rep["beats_floor_basis"]["beats"] == 37, rep["beats_floor_basis"]
    assert 37 * pf.MIN_SHOT_DURATION > 90.0, "前提：按'节拍 × 3 秒'读会超过 90 秒上限"
    assert rep["lower_bound_sec"] == rep["dialogue_sec"] < 90.0, rep


def test_voice_carried_beats_are_counted_but_not_timed():
    """读数仍要分账（画面/声音/未标），但这只用于排查，不参与下界."""
    plan = {"shot_beats": [_beat("甲", "台词1"), _beat("乙", "旁白2"), _beat("丙", "画面"),
                           "节拍：丁"]}
    assert beats_floor_basis(plan) == {"visual": 1, "voice": 2, "unlabeled": 1,
                                       "beats": 4}, beats_floor_basis(plan)


def test_evaluate_plan_exposes_the_lower_bound_basis():
    """下界的口径要显式写在读数里（`lower_bound_basis=voice`），别让读者猜."""
    plan = {"main_events": ["事件1", "事件2", "事件3"], "climax": "船头对峙",
            "required_facts": ["她被杀｜原文证据：“她被杀”｜承载：B1/画面"],
            "causal_chain": ["起因→行动→结果→悬念"],
            "shot_beats": [_beat("她被杀", "画面"), _beat("他嗯了一声", "台词1")],
            "dialogue_lines": ["嗯"], "omittable": [], "hook_spoken": [],
            "time_budget": "对白约10秒；动作约30秒"}
    rep = evaluate_plan(plan, novel_text="她被杀。他嗯了一声。")
    assert rep["lower_bound_basis"] == "voice", rep
    assert rep["lower_bound_sec"] == rep["dialogue_sec"], rep
    assert rep["beats_floor_basis"] == {"visual": 1, "voice": 1, "unlabeled": 0,
                                        "beats": 2}, rep["beats_floor_basis"]


# ---------------------------------------------------------------------------
# ② 事件顺序：共同承载 = 同时发生；隔镜复现 = 回退；台词看实际落点
# ---------------------------------------------------------------------------
def _plan_with(*carriers) -> dict:
    return {"shot_beats": [_beat("节拍%d" % (i + 1), c) for i, c in enumerate(carriers)],
            "dialogue_lines": ["甲", "乙"]}


def _script(*refs_per_shot, plan) -> dict:
    out = []
    _lines = list(plan.get("dialogue_lines") or [])
    for i, r in enumerate(refs_per_shot):
        b, l = (r if isinstance(r, tuple) else (r, []))
        _seg = "".join(str(_lines[k - 1]) for k in l if 1 <= k <= len(_lines))
        out.append({"index": i + 1, "narration_segment": _seg,
                    "plan_refs": _refs(b, l)})
    return {"shots": out, "plan_id": plan_identity(plan)}


def test_consecutive_shots_co_carrying_the_same_beats_are_not_an_order_violation():
    """`[1,2] → [1,2]` 是**共同承载**（连续多镜承载同一批节拍），不是回退."""
    plan = _plan_with("台词1", "台词1", "画面")
    script = _script(([1, 2], [1]), ([1, 2], [1]), ([3], []), plan=plan)
    ok, violations, declared = check_event_contract(script, plan)
    assert declared is True
    assert ok, violations
    assert not [v for v in violations if v["code"] == "beats_order_violation"], violations


def test_re_entering_a_finished_beat_after_a_gap_is_a_violation():
    """复核反例：`[1]→[2]→[1]→[3]`——首次声明序是 0,1,3（非降），但 B1 隔镜**再次进入**.

    判别性：只按首次声明排序 ⇒ 本用例读到 0 条违规（第十六轮复核点名的漏拦）。
    """
    plan = _plan_with("台词1", "台词1", "画面")
    script = _script(([1], [1]), ([2], [1]), ([1], [1]), ([3], []), plan=plan)
    ok, violations, declared = check_event_contract(script, plan)
    assert declared is True and not ok, violations
    _ord = [v for v in violations if v["code"] == "beats_order_violation"]
    assert len(_ord) == 1, violations
    assert "B1" in _ord[0]["detail"] and "再次" in _ord[0]["detail"], _ord[0]


def test_contiguous_multi_shot_carrying_is_not_a_gap():
    """连续三镜承载同一节拍是**延续**，不是隔镜复现（对照组，防把容忍一起收掉）."""
    plan = _plan_with("台词1", "台词1", "画面")
    script = _script(([1], [1]), ([1, 2], [1]), ([1, 2], [1]), ([3], []), plan=plan)
    ok, violations, declared = check_event_contract(script, plan)
    assert declared is True and ok, violations


def test_dialogue_order_is_judged_by_where_the_line_actually_is():
    """复核反例：正文先说 D2、再说 D1，只要声明写 `[1,2]`，旧口径与台词完整性门都放行.

    判据改为看**实际落点**（台词在旁白文本里的位置）。判别性：按声明顺序判 ⇒ 本用例 0 条违规。
    """
    plan = _plan_with("台词1", "台词2", "画面")
    _l1, _l2 = plan_dialogue_lines(plan)
    script = {"shots": [{"index": 1, "narration_segment": "%s%s" % (_l2, _l1),
                         "plan_refs": _refs([1, 2], [1, 2])}],
              "plan_id": plan_identity(plan)}
    ok, violations, declared = check_event_contract(script, plan)
    assert declared is True and not ok, violations
    _ord = [v for v in violations if v["code"] == "lines_order_violation"]
    assert len(_ord) == 1, violations
    assert "D1" in _ord[0]["detail"] and "D2" in _ord[0]["detail"], _ord[0]
    # 对照组：正文顺序与编号一致（声明顺序即使写反也不判——判的是**实际落点**）
    script2 = {"shots": [{"index": 1, "narration_segment": "%s%s" % (_l1, _l2),
                          "plan_refs": _refs([1, 2], [2, 1])}],
               "plan_id": plan_identity(plan)}
    _ok2, _v2, _ = check_event_contract(script2, plan)
    assert not [v for v in _v2 if v["code"] == "lines_order_violation"], _v2


def test_a_beat_declared_too_early_is_still_a_violation():
    """真回退仍要抓：B2 在镜头 1 首次出现，而 B1 要等到镜头 2."""
    plan = _plan_with("台词1", "台词1", "画面")
    script = _script(([2], [1]), ([1, 2], [1]), ([3], []), plan=plan)
    ok, violations, declared = check_event_contract(script, plan)
    assert declared is True and not ok
    _ord = [v for v in violations if v["code"] == "beats_order_violation"]
    assert len(_ord) == 1, violations
    assert "B1" in _ord[0]["detail"] and "B2" in _ord[0]["detail"], _ord[0]


def test_a_beat_jumping_ahead_across_several_shots_is_still_a_violation():
    """跨多镜的同型回退（B5 在 B3/B4 之前出现）也能定位."""
    plan = {"shot_beats": [_beat("节拍%d" % i, "画面") for i in range(1, 6)],
            "dialogue_lines": []}
    script = _script([1, 2], [5], [3, 4], plan=plan)
    ok, violations, _ = check_event_contract(script, plan)
    assert not ok
    _ord = [v for v in violations if v["code"] == "beats_order_violation"]
    assert len(_ord) == 1 and "B3" in _ord[0]["detail"] and "B5" in _ord[0]["detail"], _ord
    assert _ord[0]["shot"] == "3", _ord[0]


def test_reversal_inside_one_shot_is_still_a_violation():
    """同一镜内的画面节拍倒装仍算回退（声明顺序即顺序）."""
    plan = _plan_with("台词1", "台词1", "画面")
    script = _script(([1], []), ([2, 1], [1]), ([3], []), plan=plan)
    ok, violations, declared = check_event_contract(script, plan)
    assert declared is True and not ok
    assert any(v["code"] == "beats_order_violation" for v in violations), violations
