# -*- coding: utf-8 -*-
"""C01：旧缓存装载分支的顺序投影（隔离缓存 + 桩服务，零网络）.

覆盖：缓存里的计划数组序与承载序冲突 ⇒ 装载时投影、旧核验记录作废、按新摘要
重新核验、缓存仍可复用；返回计划与修正后的顺序一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

import step01_script_gen.outline as o
from step01_script_gen.plan_feasibility import plan_dialogue_lines


def _beat(body: str, carrier: str) -> str:
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, body, carrier)


def _conflict_plan() -> dict:
    return {
        "main_events": ["甲关门", "乙发问", "甲回答"],
        "climax": "甲回答",
        "causal_chain": ["关门→发问→回答"],
        "time_budget": "画面为主",
        "characters": ["甲", "乙"],
        "emotional_arc": "平→紧",
        "end_hook": "外面有人",
        "shot_beats": [
            _beat("甲把门关上", "台词2"),
            _beat("乙问为什么", "台词1"),
            _beat("甲说外面有人", "画面"),
        ],
        "dialogue_lines": ["甲把门关上。", "乙问为什么。"],
        "narration_lines": [],
        "required_facts": [
            "甲把门关上｜原文证据：“甲把门关上。”｜承载：B1/台词2",
            "乙发问｜原文证据：“乙问为什么。”｜承载：B2/台词1",
            "甲说外面有人｜原文证据：“甲说外面有人。”｜承载：B3/画面",
        ],
        "hook_spoken": [],
        "hook_carrier": "",
        "plan_contract_version": 5,
        "planning_acceptance_version": "legacy_semantic.v3",
        "plan_coherence_version": "plan_coherence.v3",
    }


class _Res:
    def __init__(self, payload, cid="c1"):
        self.success = True
        self.error = ""
        self.call_id = cid
        self.text = json.dumps(payload, ensure_ascii=False)


def test_cache_load_projects_before_verification(tmp_path, monkeypatch):
    novel = "甲把门关上。乙问为什么。甲说外面有人。"
    cache = tmp_path / "outline_cache.json"
    counts = {"gen": 0, "verify": 0}

    class _Gen:
        def generate(self, req):
            counts["gen"] += 1
            return _Res(_conflict_plan(), "g%d" % counts["gen"])

    # 验收桩：本用例只测**缓存装载分支**的投影/复用控制流，不重建核验记录语义
    monkeypatch.setattr(o, "plan_acceptance_problems", lambda *a, **k: [])
    monkeypatch.setattr(o, "_validate_outline", lambda plan, *a, **k: plan)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Gen())

    def _verify(view, digest):
        counts["verify"] += 1
        return {"schema": "content_verification.v4", "status": "ok"}

    # 第一轮：首版（冲突计划）→ 投影 → 写缓存；拿到 source_hash
    r1 = o.generate_outline_result(novel, cache_path=cache, verify=_verify)
    assert r1.get("executable") is True, r1.get("failure")
    assert counts["gen"] == 1
    raw = json.loads(cache.read_text(encoding="utf-8"))
    assert plan_dialogue_lines(raw["outline"]) == ["乙问为什么。", "甲把门关上。"]
    assert raw["outline"]["shot_beats"][0].endswith("台词1")

    # 把缓存改回**冲突版**（模拟旧缓存），保留 source_hash / verification 键位
    raw["outline"] = _conflict_plan()
    raw["verification"] = None
    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    # 第二轮：装载时必须投影；生成 0 次、核验 1 次
    class _NoGen:
        def generate(self, req):
            raise AssertionError("缓存装载不应重新生成：%r" % req)

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _NoGen())
    r2 = o.generate_outline_result(novel, cache_path=cache, verify=_verify)
    assert r2["cache"]["hit"] is True, r2["cache"]
    assert r2["cache"].get("voice_order_projected"), r2["cache"]
    assert counts["gen"] == 1, "缓存命中后不得重新生成：%s" % counts
    assert counts["verify"] >= 2, "投影后必须按新摘要重新核验：%s" % counts
    assert plan_dialogue_lines(r2["plan"]) == ["乙问为什么。", "甲把门关上。"]
    assert r2["plan"]["shot_beats"][0].endswith("台词1")
    assert r2.get("executable") is True, r2.get("failure")
