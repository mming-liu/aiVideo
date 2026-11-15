# -*- coding: utf-8 -*-
"""计划顺序契约：`dialogue_lines` 数组序必须与节拍承载序一致（2026-11-15，P02）.

判据与投影分两层：
  · `voice_order_records`：承载次序唯一且与数组序不一致时返回结构化问题；
  · `apply_voice_order_projection`：唯一可确定时把数组、编号与全部 D 引用改到同一顺序，
    不唯一/孤儿/悬空时**原样返回**（不据此拒收合法输入）。

样本全部为匿名最小情节，不依赖当前测试小说的书名、人物或章节。
"""
from __future__ import annotations

import copy
import json

import pytest

from step01_script_gen.plan_feasibility import (
    VOICE_ORDER_CODE,
    apply_voice_order_projection,
    plan_contract_records,
    plan_dialogue_lines,
    plan_identity,
    voice_order_records,
)


def _beat(body: str, carrier: str) -> str:
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, body, carrier)


def _plan(lines, beats, *, facts=None, hook_carrier="", presentation=None,
          adaptations=None) -> dict:
    plan = {
        "dialogue_lines": list(lines),
        "shot_beats": list(beats),
        "required_facts": list(facts or []),
        "hook_carrier": hook_carrier,
        "main_events": ["事件一", "事件二"],
        "narration_lines": [],
    }
    if presentation is not None:
        plan["presentation"] = presentation
    if adaptations is not None:
        plan["voice_adaptations"] = adaptations
    return plan


def _conflict_plan() -> dict:
    """数组序 A,B,C；承载序 B,A,C —— 与 run k 同形（匿名情节）."""
    return _plan(
        ["甲说第一句。", "乙说第二句。", "甲说第三句。"],
        [_beat("开场", "画面"), _beat("乙开口", "台词2"),
         _beat("甲回应", "台词1"), _beat("收束", "台词3")],
        facts=[
            "事实一｜原文证据：“甲说第一句。”｜承载：B3/台词1",
            "事实二｜原文证据：“乙说第二句。”｜承载：B2/台词2",
        ],
        hook_carrier="B2/台词2",
    )


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------

def test_conflict_is_reported_with_identity_and_path():
    recs = voice_order_records(_conflict_plan())
    assert [r["code"] for r in recs] == [VOICE_ORDER_CODE]
    rec = recs[0]
    assert rec["kind"] == "dialogue"
    assert rec["index"] == 2                     # D2 的承载节拍排在 D1 之前
    assert rec["path"] == "dialogue_lines[1]"
    assert "B2" in rec["reason"] and "B3" in rec["reason"]


def test_contract_acceptance_carries_the_conflict():
    recs = plan_contract_records(_conflict_plan(), novel_text="")
    assert VOICE_ORDER_CODE in [r["code"] for r in recs]


def test_ordered_plan_has_no_conflict():
    plan = _plan(["甲说第一句。", "乙说第二句。"],
                 [_beat("甲开口", "台词1"), _beat("乙开口", "台词2")])
    assert voice_order_records(plan) == []


def test_no_carrier_contract_is_not_judged():
    """旧计划没有「载体：」标注 ⇒ 无可核 ≠ 被破坏."""
    plan = _plan(["甲说第一句。", "乙说第二句。"], ["节拍：甲开口", "节拍：乙开口"])
    assert voice_order_records(plan) == []
    assert apply_voice_order_projection(plan)[1]["changed"] is False


def test_multiple_carriers_are_legal_and_not_projected():
    """同一条台词被两条节拍共同承载：顺序不唯一 ⇒ 不自动排序，也不据此拒收."""
    plan = _plan(["甲说第一句。", "乙说第二句。"],
                 [_beat("甲开口", "台词1"), _beat("乙开口", "台词2"),
                  _beat("回响", "台词1")])
    assert voice_order_records(plan) == []
    new, info = apply_voice_order_projection(plan)
    assert info["changed"] is False
    assert plan_dialogue_lines(new) == plan_dialogue_lines(plan)


def test_orphan_is_left_to_existing_check():
    plan = _plan(["甲说第一句。", "乙说第二句。"],
                 [_beat("甲开口", "台词1"), _beat("画面", "画面")])
    assert voice_order_records(plan) == []
    assert apply_voice_order_projection(plan)[1]["changed"] is False


def test_dangling_carrier_is_left_to_existing_check():
    plan = _plan(["甲说第一句。"], [_beat("甲开口", "台词9")])
    assert voice_order_records(plan) == []
    assert apply_voice_order_projection(plan)[1]["changed"] is False


# ---------------------------------------------------------------------------
# 投影：改编号与引用，不改内容；幂等
# ---------------------------------------------------------------------------

def test_projection_remaps_lines_carriers_facts_and_hook():
    plan = _conflict_plan()
    new, info = apply_voice_order_projection(copy.deepcopy(plan))
    assert info["changed"] is True
    assert info["mapping"] == {"1": 2, "2": 1, "3": 3}
    # 文本与主张逐字保留，只换位置
    assert plan_dialogue_lines(new) == ["乙说第二句。", "甲说第一句。", "甲说第三句。"]
    # 节拍载体按新编号写回
    assert "载体：台词1" in new["shot_beats"][1]     # B2 仍是"乙开口"
    assert "载体：台词2" in new["shot_beats"][2]     # B3 仍是"甲回应"
    # 事实承载声明同步
    assert new["required_facts"][0].endswith("B3/台词2")
    assert new["required_facts"][1].endswith("B2/台词1")
    # 钩子承载同步
    assert new["hook_carrier"] == "B2/台词1"
    # 事实正文与证据逐字不变
    assert "事实一｜原文证据：“甲说第一句。”" in new["required_facts"][0]
    # 计划身份随之改变（引用所指的编号含义变了）
    assert plan_identity(new) != plan_identity(plan)


def test_projection_is_idempotent():
    new, _ = apply_voice_order_projection(_conflict_plan())
    again, info = apply_voice_order_projection(copy.deepcopy(new))
    assert info["changed"] is False
    assert again == new
    assert voice_order_records(new) == []


def test_projection_remaps_presentation_uids_and_adaptation_index():
    plan = _plan(
        ["甲说第一句。", "乙说第二句。"],
        [_beat("乙开口", "台词2"), _beat("甲开口", "台词1")],
        presentation={"mode": "screen_hybrid", "dialogue_uids": ["d01", "d02"]},
        adaptations={"entries": [
            {"kind": "dialogue", "index": 1, "text": "甲说第一句。", "speaker": "甲"},
            {"kind": "dialogue", "index": 2, "text": "乙说第二句。", "speaker": "乙"},
        ]},
    )
    new, info = apply_voice_order_projection(copy.deepcopy(plan))
    assert info["changed"] is True
    assert new["presentation"]["dialogue_uids"] == ["d02", "d01"]
    assert [e["index"] for e in new["voice_adaptations"]["entries"]] == [2, 1]
    assert [e["text"] for e in new["voice_adaptations"]["entries"]] == [
        "甲说第一句。", "乙说第二句。"]


def test_projection_skips_when_dialogue_array_has_blank_entries():
    """原始数组与规范化读数条数不一致 ⇒ 不猜序号（结构判据负责）."""
    plan = _plan(["甲说第一句。", "", "乙说第二句。"],
                 [_beat("甲开口", "台词1"), _beat("乙开口", "台词2")])
    new, info = apply_voice_order_projection(plan)
    assert info["changed"] is False
    assert new is plan


# ---------------------------------------------------------------------------
# 跨内容：同一机制换一套实体/关系/编号仍成立
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sample", [
    {
        "lines": ["老陈让阿七把门关上。", "阿七反问为什么。", "老陈说外面有人。"],
        "carriers": ["台词3", "台词1", "台词2"],   # 承载序 3,1,2
    },
    {
        "lines": ["一", "二", "三", "四"],
        "carriers": ["台词4", "台词3", "台词2", "台词1"],   # 完全逆序
    },
])
def test_cross_content_projection_uses_same_mechanism(sample):
    beats = [_beat("画面", "画面")] + [_beat("动作%d" % i, c)
                                       for i, c in enumerate(sample["carriers"], 1)]
    plan = _plan(sample["lines"], beats)
    new, info = apply_voice_order_projection(copy.deepcopy(plan))
    assert info["changed"] is True
    assert sorted(plan_dialogue_lines(new)) == sorted(sample["lines"])
    assert voice_order_records(new) == []
    # 每句台词的承载节拍按新编号升序
    order = [int(b.rsplit("台词", 1)[1]) for b in new["shot_beats"] if "台词" in b]
    assert order == sorted(order)


# ---------------------------------------------------------------------------
# 生产接线：候选定稿先投影，再挂核验（不提前绑定旧身份）
# ---------------------------------------------------------------------------

def test_complete_candidate_projects_before_attach(monkeypatch):
    from step01_script_gen import outline as o
    from step01_script_gen import content_verify
    from shared.presentation_contract import view_from_plan

    seen = {}

    def _fake_attach(plan, controller, **kwargs):
        seen["lines"] = plan_dialogue_lines(plan)
        seen["digest_plan"] = plan
        return plan

    monkeypatch.setattr(content_verify, "attach_verification", _fake_attach)
    plan = _conflict_plan()
    view, vp = view_from_plan(plan)
    assert view is not None, vp
    cand, problems = o.complete_candidate(view, "", verification=object())
    assert seen["lines"] == ["乙说第二句。", "甲说第一句。", "甲说第三句。"]
    assert plan_dialogue_lines(cand) == seen["lines"]


def test_run_plan_revision_projects_before_controller(monkeypatch):
    from step01_script_gen import outline as o
    from step01_script_gen import plan_revision

    seen = {}

    class _Ctl:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, novel_text=""):
            seen["lines"] = plan_dialogue_lines(plan)
            return {"plan": plan, "stop_reason": "ok", "problems": [], "calls": 0,
                    "rounds": 0, "history": []}

    monkeypatch.setattr(plan_revision, "RevisionController", _Ctl)
    out = o.run_plan_revision(_conflict_plan(), "")
    assert seen["lines"] == ["乙说第二句。", "甲说第一句。", "甲说第三句。"]
    assert plan_dialogue_lines(out["plan"]) == seen["lines"]
