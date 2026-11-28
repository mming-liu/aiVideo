# -*- coding: utf-8 -*-
"""第 7 轮收口：统一「**声音条目 → 镜头或连续片段**」（2026-09-30 复核）.

复核给的四个已复现缺口，各配一条判别性回归：

1. **P1** 预算允许跨镜拆分，终检却要求整条旁白落在**某一个**镜头里
   （一条 13.65 秒旁白合法拆两段 → 计划判可行 → 两镜完整保留 → 终检仍报
   `plan_refs_narration_missing`）；
2. **P1** 事实编号被净化压缩（删掉前面的空事实后 F2→F1），旁白 `fact_refs=[2]` 不跟着走；
3. **P2**「互相登记」单向可绕（`fact_refs=[]`／`null`／非法元素／浮点／布尔全通过）；
4. **P2** 出处只是必填文字，没有被验证（`source` 改成「不存在的出处」照样过门）。

外加统一表示本身的读数（`plan_voice_entries` / `voice_units` / `voice_seconds`）。

判别性：`.tmp/revert_r7.py` 把这几处退回旧实现后，本文件必须失败
（见 `docs/step01-plan-carrier-contract-2026-09-30.md` §11）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import plan_feasibility as pf                       # noqa: E402

_H1 = "拿主意？拿什么主意？"
_N1 = "四人围桌喝茶。"
_N2 = "甲起身离席。"
_SRC = "丙道：“%s”%s%s" % (_H1, _N1, _N2)
# 事实的「原文证据」是逐字引文；承载要求与承载内容有实质交集
_FACT_D = '%s｜原文证据：“%s”｜承载：B1/台词1' % (_H1, _H1)
_FACT_N = '%s｜原文证据：“%s”｜承载：B2/旁白1' % (_N1, _N1)


def _beat(body, cite, carrier):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, cite, carrier)


def _plan(*, narration=None, facts=None):
    """一条台词 + 一条**两句话**的旁白（跨镜拆分的标准形态）."""
    plan = {"main_events": ["丙反问", "四人喝茶", "甲离席"], "climax": "甲离席",
            "required_facts": list(facts if facts is not None else [_FACT_D, _FACT_N]),
            "causal_chain": ["丙反问→四人喝茶→甲离席"],
            "shot_beats": [_beat("丙把话说了", _H1, "台词1"),
                           _beat("四人围桌喝茶随即甲起身离席", _N1 + _N2, "旁白1"),
                           _beat("茶盏留在桌上", _N1, "画面")],
            "dialogue_lines": [_H1], "hook_spoken": [], "time_budget": "对白约30%"}
    plan["narration_lines"] = ([{"text": _N1 + _N2, "source": _N1 + _N2,
                                 "fact_refs": [2]}]
                               if narration is None else narration)
    return plan


def _shots(second_half: str = _N2):
    """承载 B2 的两镜**连续**（旁白在这两镜里接力讲完）."""
    return [
        {"index": 1, "narration_segment": _H1, "plan_refs": {"beats": [1], "lines": [1]}},
        {"index": 2, "narration_segment": _N1, "plan_refs": {"beats": [2], "lines": []}},
        {"index": 3, "narration_segment": second_half,
         "plan_refs": {"beats": [2, 3], "lines": []}},
    ]


def _stamped(plan, shots):
    return {"shots": shots, "plan_id": pf.plan_identity(plan)}


# ---------------------------------------------------------------------------
# 1. 跨镜连续的旁白：终检与计划层同一判据
# ---------------------------------------------------------------------------
def test_narration_split_across_consecutive_shots_passes_the_terminal_check():
    """P1：计划说「可按句末拆」、成稿**真的连续拆成两镜** ⇒ 终检必须放行.

    旧实现（整条必须落在某一个镜头里）在这里报 `plan_refs_narration_missing`。
    """
    plan = _plan()
    assert pf.plan_contract_problems(plan) == [], pf.plan_contract_problems(plan)
    ok, viol, declared = pf.check_event_contract(_stamped(plan, _shots()), plan)
    assert declared and ok, viol


def test_narration_that_stops_halfway_is_still_a_violation():
    """反向判别性：后一镜没有把旁白讲完 ⇒ 仍然拒收（跨镜不是「一律放过」）."""
    plan = _plan()
    ok, viol, _ = pf.check_event_contract(
        _stamped(plan, _shots(second_half="甲端起茶盏。")), plan)
    assert not ok and any(v["code"] == "plan_refs_narration_missing" for v in viol), viol


def test_narration_split_across_non_consecutive_shots_is_rejected():
    """中间隔了别的镜头 ⇒ 不构成**连续**片段（判据与台词门同源：起点对齐 + 连续）."""
    plan = _plan()
    shots = [{"index": 1, "narration_segment": _H1,
              "plan_refs": {"beats": [1], "lines": [1]}},
             {"index": 2, "narration_segment": _N1,
              "plan_refs": {"beats": [2], "lines": []}},
             {"index": 3, "narration_segment": "丙把茶盏放下。",
              "plan_refs": {"beats": [2, 3], "lines": []}},
             {"index": 4, "narration_segment": _N2,
              "plan_refs": {"beats": [2], "lines": []}}]
    ok, viol, _ = pf.check_event_contract(_stamped(plan, shots), plan)
    assert not ok and any(v["code"] == "plan_refs_narration_missing" for v in viol), viol


# ---------------------------------------------------------------------------
# 2. 事实编号不得被净化压缩（F{n} 也是被引用的序号）
# ---------------------------------------------------------------------------
def test_fact_numbering_is_not_compressed_by_sanitizing():
    """P1：`required_facts` 里的空项 ⇒ **拒收**，不静默过滤（过滤会让 F 编号整体前移）."""
    from step01_script_gen import outline as _o
    bad = _plan()
    bad["required_facts"] = ["", _FACT_D, _FACT_N]     # 前面一个空事实
    assert _o._validate_outline(bad, _SRC) is None


def test_fact_positions_are_stable_through_validation():
    """反向判别性：合法计划经过 `_validate_outline` 后 F 编号**一位不动**."""
    from step01_script_gen import outline as _o
    plan = _plan()
    out = _o._validate_outline(plan, _SRC)
    assert out is not None
    assert out["required_facts"][0].startswith(_H1), out["required_facts"]
    assert out["required_facts"][1].startswith(_N1), out["required_facts"]
    # 旁白 `fact_refs=[2]` 仍指向同一条事实（编号没前移 ⇒ 互指仍成立）
    assert pf.plan_contract_problems(out) == [], pf.plan_contract_problems(out)


# ---------------------------------------------------------------------------
# 3. 「互相登记」必须**无条件**双向 + `fact_refs` 类型严格
# ---------------------------------------------------------------------------
def test_empty_fact_refs_cannot_bypass_the_mutual_registration():
    """P2：事实侧声明 `承载：B2/旁白1`，旁白侧 `fact_refs=[]` ⇒ 拒收（旧实现整条免检）."""
    plan = _plan(narration=[{"text": _N1 + _N2, "source": _N1 + _N2, "fact_refs": []}])
    probs = pf.plan_contract_problems(plan)
    assert probs and any("互相登记" in p for p in probs), probs


@pytest.mark.parametrize("refs", [None, "2", 2, [2.0], [True], ["2"], [None]])
def test_illegal_fact_refs_types_are_reported(refs):
    """P2：`null`／字符串／浮点／布尔／非法元素一律显式报错（不再被 `int()` 圆场）."""
    plan = _plan(narration=[{"text": _N1 + _N2, "source": _N1 + _N2,
                             "fact_refs": refs}])
    probs = pf.plan_contract_problems(plan)
    assert probs and any("fact_refs" in p for p in probs), probs


def test_missing_fact_refs_key_is_reported():
    """P2：`fact_refs` 键缺失（schema 说必填）也不放过."""
    plan = _plan(narration=[{"text": _N1 + _N2, "source": _N1 + _N2}])
    probs = pf.plan_contract_problems(plan)
    assert probs and any("fact_refs" in p for p in probs), probs


def test_legal_fact_refs_still_pass():
    """反向判别性：整数序号 + 双向登记 ⇒ 通过（不制造假阳性）."""
    plan = _plan()
    assert pf.plan_contract_problems(plan) == [], pf.plan_contract_problems(plan)


# ---------------------------------------------------------------------------
# 4. 出处必须可核（不是「必填文字」）
# ---------------------------------------------------------------------------
def test_source_must_be_found_verbatim_in_the_novel():
    """P2：`source` 改成「不存在的出处」 ⇒ 拒收（旧实现只查非空）."""
    plan = _plan(narration=[{"text": _N1 + _N2, "source": "不存在的出处",
                             "fact_refs": [2]}])
    probs = pf.plan_contract_problems(plan, novel_text=_SRC)
    assert probs and any("找不到" in p for p in probs), probs


def test_source_must_contain_the_narration_text():
    """P2：出处是另一句**真实**原文、但不含这条正文 ⇒ 拒收（出处证明不了依据）."""
    plan = _plan(narration=[{"text": _N1 + _N2, "source": _H1, "fact_refs": [2]}])
    probs = pf.plan_contract_problems(plan, novel_text=_SRC)
    assert probs and any("不在这条出处里" in p for p in probs), probs


def test_source_is_verified_against_the_real_novel_text():
    """反向判别性：出处逐字来自原文且**包含**正文 ⇒ 通过."""
    plan = _plan()
    assert pf.plan_contract_problems(plan, novel_text=_SRC) == [], \
        pf.plan_contract_problems(plan, novel_text=_SRC)


def test_source_is_not_judged_without_the_novel_text():
    """没有原文时不判出处（该维度需要原文；不把「缺输入」当「有问题」）."""
    plan = _plan(narration=[{"text": _N1 + _N2, "source": "不存在的出处",
                             "fact_refs": [2]}])
    assert pf.plan_contract_problems(plan) == []


# ---------------------------------------------------------------------------
# 5. 统一表示本身
# ---------------------------------------------------------------------------
def test_voice_entries_are_the_single_carrier_reading():
    """`plan_voice_entries` 是「节拍 → 声音条目」这条边的**唯一读数**."""
    entries = pf.plan_voice_entries(_plan())
    assert [e["id"] for e in entries] == ["D1", "N1"]
    assert entries[0]["beats"] == [1] and entries[1]["beats"] == [2]
    assert entries[1]["fact_refs"] == [2] and entries[1]["source"] == _N1 + _N2


def test_voice_seconds_share_one_estimator():
    """预算：对白与旁白**共用**同一估算器（分账相加 = 合计）."""
    plan = _plan()
    entries = pf.plan_voice_entries(plan)
    assert pf.voice_seconds(entries) == pytest.approx(
        pf.voice_seconds(entries, pf.VOICE_KIND_DIALOGUE)
        + pf.voice_seconds(entries, pf.VOICE_KIND_NARRATION))
    assert pf.voice_seconds(entries, pf.VOICE_KIND_NARRATION) == pytest.approx(
        pf.narration_seconds(pf.plan_narration_entries(plan)))


def test_voice_units_resolve_a_continuous_shot_run():
    """「声音条目 → 镜头或连续片段」：旁白落在**两镜连续片段**上，台词落在一镜."""
    units = pf.voice_units(_plan(), _shots())
    by_id = {u["id"]: u for u in units}
    assert by_id["N1"]["declared_shot"] == "2"
    assert by_id["N1"]["shot_indexes"] == ["2", "3"]
    assert by_id["N1"]["start_shot"] == "2" and by_id["N1"]["end_shot"] == "3"
    assert by_id["N1"]["n_shots"] == 2
    assert by_id["D1"]["shot_indexes"] == ["1"]


def test_voice_units_separate_undeclared_from_unconsumable():
    """「没有任何镜头声明承载它」与「声明了但拼不出连续片段」是两种结论."""
    units = pf.voice_units(_plan(), _shots(second_half="甲端起茶盏。"))
    n1 = {u["id"]: u for u in units}["N1"]
    assert n1["declared_shot"] == "2" and n1["shot_indexes"] == []


def test_source_is_not_re_judged_when_the_text_itself_is_not_verbatim():
    """判据边界：正文本身不在原文 ⇒ 交由逐字核验剔除（不被升级成「契约未完成」）.

    实跑形态（ch1）：旁白正文是多句原文拼接出来的一段，`source` 只是把它抄了一遍。
    那条旁白本来就该被 `_drop_unverified` 剔除并重映射引用；若出处核验在这里也报一条，
    契约门会在剔除**之前**停下整章。
    """
    plan = _plan(narration=[{"text": "这句原文里没有。也不是原文。",
                             "source": "这句原文里没有。也不是原文。",
                             "fact_refs": [2]}])
    probs = pf.plan_contract_problems(plan, novel_text=_SRC)
    assert probs == [], probs


def test_hook_cannot_be_carried_by_narration():
    """`hook_spoken` 是「**角色说出口**的台词」，旁白（画外音）承载不了它.

    旧实现把 `B2/旁白1` 归到「非台词 ⇒ 画面」那一支，报出的是「该节拍自己标的是…」，
    消息与实际声明不符。现在显式拒绝。
    """
    plan = _plan()
    plan["hook_spoken"] = [_H1]
    plan["hook_carrier"] = "B2/旁白1"
    probs = pf.plan_contract_problems(plan)
    assert probs and any("不能声明由**旁白**承载" in p for p in probs), probs
