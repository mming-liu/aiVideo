# -*- coding: utf-8 -*-
"""S08：首版登记类问题的**确定性派生**与"唯一映射投影/不唯一留作残余"边界（2026-11-21）.

背景：11-21 首版 `plan_out` 有 7 条节拍、13 条台词、4 条旁白，登记阶段判定
`orphan_uncarried` 13 项（全部台词未被任何节拍承载）+ 1 项（旁白 N2 未被承载）。
本文件钉住：

1. **确定性**：登记问题是计划的纯函数（同一输入逐字同序产出；不依赖模型、文件或顺序）；
2. **唯一映射走代码投影**：节拍里写明 `载体：台词k` / `载体：旁白k` 时，v2 视图投影
   直接把声音引用映射成 `voice_ref`（不把"已经标好的关系"误报成孤儿）；
3. **不唯一映射只能留作登记残余**：选中了声音却没有任何节拍引用它（也没有任何可唯一
   断定的编号关系）时，必须继续报 `orphan_uncarried`，不能用相似度/固定编号猜改绑。

匿名样本覆盖规则本身；11-21 冻结现场作为**显式外部回归**（缺失时 skip 并注明）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from step01_script_gen import plan_feasibility as pf

_BEAT = "节拍：{text}｜本镜信息目的：{purpose}｜原文依据：{ev}｜载体：{carrier}"


def _beat(text, carrier, ev="原文依据", purpose="目的"):
    return _BEAT.format(text=text, purpose=purpose, ev=ev, carrier=carrier)


def test_registration_problems_are_a_deterministic_pure_projection():
    beats = [_beat("甲开口", "台词1"), _beat("乙开口", "台词2"),
             _beat("画面动作", "画面")]
    first = pf.carrier_relation_records(beats, n_lines=4, n_narration=2)
    second = pf.carrier_relation_records(list(beats), n_lines=4, n_narration=2)
    assert first == second, "同一计划两次登记派生结果不一致（非确定性）"
    _codes = [r["code"] for r in first]
    assert _codes.count("orphan_uncarried") == 4, first
    _orphans = {(r["kind"], r["index"]) for r in first
                if r["code"] == "orphan_uncarried"}
    assert _orphans == {("dialogue", 3), ("dialogue", 4),
                        ("narration", 1), ("narration", 2)}, _orphans
    # 记录身份/路径由代码当场给出，报告层无需从标签串反推编号
    for r in first:
        assert r["path"] and r["kind"] in ("dialogue", "narration", "beat")


def test_explicit_carrier_labels_project_to_voice_refs_instead_of_orphans():
    """节拍写明 `载体：台词k` ⇒ 视图投影给出 voice_ref，登记不再报孤儿（唯一映射）."""
    from shared import presentation_contract as pc

    beats = [_beat("甲开口", "台词1"), _beat("乙回应", "台词2")]
    # 与 `view_from_plan` 同口径的台词条目（legacy 文本数组）
    plan = {
        "shot_beats": beats,
        "dialogue_lines": ["你好", "再见"],
        "narration_lines": [],
        "required_facts": [], "background_facts": [], "main_events": [],
        "causal_chain": [], "key_scenes": [], "characters": [],
        "character_motives": [], "emotional_arc": [], "climax": "",
        "end_hook": "", "time_budget": {}, "omittable": [],
        "hook_carrier": "", "hook_spoken": [], "prop_state_candidates": [],
        "plan_contract_version": pf.PLAN_CONTRACT_VERSION,
        "planning_acceptance_version": "anonymous-fixture",
    }
    _view, _problems = pc.view_from_plan(plan)
    assert _view is not None, _problems
    _refs = [b.get("voice_ref") for b in _view.get("beats") or []]
    assert _refs == [{"kind": "dialogue", "uid": "d01"},
                     {"kind": "dialogue", "uid": "d02"}], _refs
    assert not [r for r in pf.carrier_relation_records(beats, n_lines=2)
                if r["code"] == "orphan_uncarried"]


def test_unmapped_voices_stay_as_registration_residuals():
    """没有唯一映射时不得猜：1 条画面节拍 + 3 条台词 ⇒ 3 条孤儿全部保留."""
    beats = [_beat("只有一个画面节拍", "画面")]
    records = pf.carrier_relation_records(beats, n_lines=3)
    assert [(r["code"], r["kind"], r["index"]) for r in records] == [
        ("orphan_uncarried", "dialogue", 1),
        ("orphan_uncarried", "dialogue", 2),
        ("orphan_uncarried", "dialogue", 3),
    ], records


# ---------------------------------------------------------------------------
# 外部回归：11-21 冻结首版计划（显式声明数据版本；缺失时 skip 并注明“未运行”）
# ---------------------------------------------------------------------------
_CORPUS = (Path(__file__).resolve().parents[2] / ".tmp" /
           "step01_plan_corpus_20261121")


@pytest.mark.skipif(not (_CORPUS / "frozen").is_dir(),
                    reason="外部冻结语料缺失（.tmp/step01_plan_corpus_20261121）——"
                           "S08 现场回归未运行（不是通过）")
def test_the_frozen_first_version_registration_residuals_are_reproduced():
    """11-21 首版：13 条台词 + 1 条旁白是**无唯一映射**的登记残余，逐项可复算."""
    frozen = _CORPUS / "frozen" / "20261121T181234-6228"
    rows = [json.loads(line) for line in
            (frozen / "step01_replan_rounds.jsonl").read_text(
                encoding="utf-8").splitlines() if line.strip()]
    plan = rows[0].get("plan_out") or rows[0].get("plan_in")
    beats = plan["shot_beats"]
    records = pf.carrier_relation_records(
        beats, n_lines=len(plan["dialogue_lines"]),
        n_narration=len(plan["narration_lines"]))
    orphans = {(r["kind"], r["index"]) for r in records
               if r["code"] == "orphan_uncarried"}
    assert {("dialogue", i) for i in range(1, 14)} == {
        o for o in orphans if o[0] == "dialogue"}, orphans
    assert ("narration", 2) in orphans, orphans
    # 首版节拍的显式 `载体：旁白k` 已由代码投影成 voice_ref（不是孤儿）
    from shared import presentation_contract as pc
    _view, _problems = pc.view_from_plan(plan)
    assert _view is not None, _problems
    _refs = {str(b.get("uid")): b.get("voice_ref")
             for b in _view.get("beats") or []}
    assert _refs["b01"] == {"kind": "narration", "uid": "n01"}
    assert _refs["b02"] == {"kind": "narration", "uid": "n03"}
    assert _refs["b05"] == {"kind": "narration", "uid": "n04"}
