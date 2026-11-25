# -*- coding: utf-8 -*-
"""生产接线测试：lightweight_plan 调用 voice_allocation(v2) / source_claim_fix / fact_binding_repair."""
from __future__ import annotations

import json

from _text_gen.models import TextGenerationResult
from step01_script_gen import lightweight_plan as lw
from step01_script_gen import voice_allocation as va

NOVEL = ("黄昏时分，甲站在渡口。\n"
         "甲说道：“这河水深得很，夜里行船最是危险，你若执意要走，须得小心暗礁。”\n"
         "乙说道：“我要过河送药。”\n"
         "乙登上小船，甲望着水面叹了口气。\n")
L1 = "这河水深得很，夜里行船最是危险，你若执意要走，须得小心暗礁。"
L2 = "我要过河送药。"


def _beat(text, src, kind="none", vt="", sp=""):
    return {"text": text, "purpose": "推进", "source": src, "voice_kind": kind,
            "voice_text": vt, "speaker": sp}


def _over_budget_answer():
    # 故意构造超单条/总长上限的台词，触发修订轮
    long_line = L1 + "你若执意要走，须得小心暗礁。"
    return {
        "characters": ["甲", "乙"], "emotional_arc": "焦急", "climax": "夜渡",
        "end_hook": "乙登船", "omittable": [],
        "events": [
            {"summary": "甲劝阻", "beats": [
                _beat("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _beat("甲开口劝阻", "甲说道", "dialogue", long_line, "甲")]},
            {"summary": "乙答话", "beats": [
                _beat("乙回答", "乙说道", "dialogue", L2, "乙")]},
            {"summary": "乙登船", "beats": [
                _beat("乙登上小船", "乙登上小船，甲望着水面叹了口气。")]},
        ],
        "facts": [{"claim": "甲劝乙小心暗礁", "evidence": L1,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }


def test_lightweight_outline_wires_voice_allocation_v2(monkeypatch):
    monkeypatch.setenv(va.ENV, "v2")
    monkeypatch.setenv("STEP01_VOICE_ALLOCATION_MAX_LINES", "10")
    # 压低单条上限，迫使首答进入 v2 修订分支
    monkeypatch.setattr("step01_script_gen.timecode.active_shot_cap", lambda: 5.0)
    monkeypatch.setattr("step01_script_gen.lightweight_voice_repair.active_shot_cap",
                        lambda: 5.0)

    first = {
        "characters": ["甲", "乙"], "emotional_arc": "焦急", "climax": "夜渡",
        "end_hook": "乙登船", "omittable": [],
        "events": [
            {"summary": "甲劝阻", "beats": [
                _beat("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _beat("甲开口劝阻", "甲说道", "dialogue", L1, "甲")]},
            {"summary": "乙答话", "beats": [
                _beat("乙回答", "乙说道", "dialogue", L2, "乙")]},
            {"summary": "乙登船", "beats": [
                _beat("乙登上小船", "乙登上小船，甲望着水面叹了口气。")]},
        ],
        "facts": [{"claim": "甲劝乙小心暗礁", "evidence": L1,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }

    # 第一轮返回超 5s 的声音（L1 约 6-7s），第二轮 v2 拆成两句短句
    schemas = []

    class _FakeSvc:
        def __init__(self):
            self.n = 0

        def generate(self, req):
            self.n += 1
            schemas.append((req.json_schema or {}).get("name"))
            if self.n == 1:
                return TextGenerationResult(success=True, text=json.dumps(first, ensure_ascii=False))
            # v2 响应：把 D1 拆成两条 <=5s 的候选，并重绑 f01
            v2_resp = {
                "voices": [{"id": "D1", "units": ["D1:c2", "D1:c3"]}],
                "facts": [{"id": "f01", "units": ["D1:1", "D1:2"]}],
            }
            # 若候选目录索引不是 c2/c3，则动态从 prompt 里取 <=5s 的两段
            prompt_json, _ = json.JSONDecoder().raw_decode(
                req.user_prompt.split("\n目录：", 1)[1])
            d1_units = [u for u in prompt_json["voices"]["D1"]["units"] if u["sec"] <= 4.0]
            if len(d1_units) >= 2:
                v2_resp["voices"][0]["units"] = [d1_units[0]["unit"], d1_units[1]["unit"]]
            return TextGenerationResult(success=True, text=json.dumps(v2_resp, ensure_ascii=False))

    res = lw.generate_lightweight_outline_result(NOVEL, service=_FakeSvc())
    assert schemas[0] == "lightweight_outline"
    assert schemas[1] == "lightweight_voice_allocation"
    assert res["executable"] is True
    assert res["plan"] is not None


def test_lightweight_outline_v2_forbids_dropping_facts(monkeypatch):
    monkeypatch.setenv(va.ENV, "v2")
    monkeypatch.setenv("STEP01_VOICE_ALLOCATION_MAX_LINES", "10")
    monkeypatch.setattr("step01_script_gen.timecode.active_shot_cap", lambda: 5.0)
    monkeypatch.setattr("step01_script_gen.lightweight_voice_repair.active_shot_cap",
                        lambda: 5.0)

    first = {
        "characters": ["甲", "乙"], "emotional_arc": "焦急", "climax": "夜渡",
        "end_hook": "乙登船", "omittable": [],
        "events": [
            {"summary": "甲劝阻", "beats": [
                _beat("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _beat("甲开口劝阻", "甲说道", "dialogue", L1, "甲")]},
            {"summary": "乙答话", "beats": [
                _beat("乙回答", "乙说道", "dialogue", L2, "乙")]},
            {"summary": "乙登船", "beats": [
                _beat("乙登上小船", "乙登上小船，甲望着水面叹了口气。")]},
        ],
        "facts": [{"claim": "甲劝乙小心暗礁", "evidence": L1,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }

    class _DroppingSvc:
        def __init__(self):
            self.n = 0

        def generate(self, req):
            self.n += 1
            if self.n == 1:
                assert "dropped_facts" not in (req.json_schema or {}).get("schema", {}).get("properties", {})
                return TextGenerationResult(success=True, text=json.dumps(first, ensure_ascii=False))
            # 第二轮模型越权企图通过 dropped_facts 删掉必保留事实
            assert "dropped_facts" not in (req.json_schema or {}).get("schema", {}).get("properties", {})
            prompt_json, _ = json.JSONDecoder().raw_decode(req.user_prompt.split("\n目录：", 1)[1])
            d1_units = [u for u in prompt_json["voices"]["D1"]["units"] if u["sec"] <= 4.0]
            bad_resp = {
                "voices": [{"id": "D1", "units": [d1_units[0]["unit"]]}],
                "facts": [{"id": "f01", "units": ["D1:1"]}],
                "dropped_facts": [{"id": "f01", "reason": "为了塞进时长"}],
            }
            return TextGenerationResult(success=True, text=json.dumps(bad_resp, ensure_ascii=False))

    res = lw.generate_lightweight_outline_result(NOVEL, service=_DroppingSvc())
    assert res["executable"] is False
    assert "allocation_dropped_facts_forbidden" in (res.get("acceptance") or {}).get("codes", [])


def test_correct_plan_sources_and_rebind_plan_carriers():
    ans = {
        "characters": ["甲", "乙"], "emotional_arc": "焦急", "climax": "夜渡",
        "end_hook": "乙登船", "omittable": [],
        "events": [
            {"summary": "甲劝阻", "beats": [
                _beat("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _beat("甲开口劝阻", "甲说道", "dialogue", L1, "甲")]},
            {"summary": "乙答话", "beats": [
                _beat("乙回答", "乙说道", "dialogue", L2, "乙")]},
            {"summary": "乙登船", "beats": [
                _beat("乙登上小船", "乙登上小船，甲望着水面叹了口气。")]},
        ],
        "facts": [{"claim": "甲劝乙小心暗礁且夜里危险", "evidence": L1,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }
    resp = {
        "splits": [{"id": "f01", "children": [
            {"claim": "夜里行船最是危险", "evidence": "这河水深得很，夜里行船最是危险"},
            {"claim": "执意要走须小心暗礁", "evidence": "你若执意要走，须得小心暗礁"},
        ]}],
        "source_preservation": {
            "target_uids": ["f01"], "verdict": "supported",
            "evidence_spans": [L1], "reason": "完整拆分原句警告与条件",
        },
    }
    plan, probs, cand = lw.correct_plan_sources(ans, resp, NOVEL)
    assert probs == [] and plan is not None and cand is not None
    assert len(plan["required_facts"]) == 2

    rebound, rprobs, meta = lw.rebind_plan_carriers(
        plan, [{"fact_uid": "f01", "add": [{"beat": 1, "kind": "visual"}]}], novel_text=NOVEL)
    assert rprobs == [] and rebound is not None
    assert "B1/画面" in rebound["required_facts"][0]
    assert meta["voice_entries"] == 2
