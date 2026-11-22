# -*- coding: utf-8 -*-
"""轻量规划（STEP01_PLAN_MODE=lightweight）离线验收：一进一出、重抽一次、失败不下发.

匿名最小情节 + 假大纲服务；不读 `.tmp` / `output`，不发真实请求。
"""
from __future__ import annotations

import copy
import json

import pytest

from _text_gen.models import TextGenerationResult
from step01_script_gen import concurrent_extract as ce
from step01_script_gen import lightweight_plan as lw
from step01_script_gen.outline import outline_to_anchor, plan_acceptance_problems
from step01_script_gen.plan_feasibility import (check_event_contract,
                                                check_plan_before_generation,
                                                plan_has_carrier_contract)
from step01_script_gen.prompts import render_execution_contract

pytestmark = pytest.mark.v2_request

NOVEL = ("黄昏时分，阿青提着灯笼走进渡口。\n"
         "渡口的老船夫问道：“这么晚了，你要去哪里？”\n"
         "阿青答道：“我要连夜过河，给东岸的姐姐送药。”\n"
         "老船夫叹了口气，解开了系船的绳子。\n"
         "小船行到河心，忽然起了大风，船身剧烈摇晃。\n"
         "阿青紧紧抱住药箱，一动也不敢动。\n"
         "天快亮时，小船终于靠上了东岸。\n")


def _answer(**over):
    a = {
        "characters": ["阿青", "老船夫"], "emotional_arc": "平静→紧张→释然",
        "climax": "小船在河心遇到大风", "end_hook": "阿青终于到了东岸",
        "omittable": [],
        "events": [
            {"summary": "阿青夜到渡口求渡", "beats": [
                {"text": "阿青提着灯笼走进渡口", "purpose": "交代时间地点",
                 "source": "黄昏时分，阿青提着灯笼走进渡口。", "voice_kind": "none",
                 "voice_text": "", "speaker": ""},
                {"text": "老船夫抬头打量阿青", "purpose": "船夫发问",
                 "source": "渡口的老船夫问道", "voice_kind": "dialogue",
                 "voice_text": "这么晚了，你要去哪里？", "speaker": "老船夫"},
                {"text": "阿青举起药箱回答", "purpose": "交代过河目的",
                 "source": "阿青答道", "voice_kind": "dialogue",
                 "voice_text": "我要连夜过河，给东岸的姐姐送药。", "speaker": "阿青"}]},
            {"summary": "小船在河心遇风", "beats": [
                {"text": "老船夫解开绳子，小船离岸", "purpose": "启程",
                 "source": "老船夫叹了口气，解开了系船的绳子。", "voice_kind": "none",
                 "voice_text": "", "speaker": ""},
                {"text": "河心大风，船身剧烈摇晃，阿青抱紧药箱", "purpose": "危机",
                 "source": "小船行到河心，忽然起了大风，船身剧烈摇晃。",
                 "voice_kind": "narration",
                 "voice_text": "小船行到河心，忽然起了大风，船身剧烈摇晃。", "speaker": ""}]},
            {"summary": "天亮时靠岸", "beats": [
                {"text": "晨光里小船靠上东岸", "purpose": "收束",
                 "source": "天快亮时，小船终于靠上了东岸。", "voice_kind": "none",
                 "voice_text": "", "speaker": ""}]},
        ],
        "facts": [
            {"claim": "阿青要连夜过河给东岸的姐姐送药",
             "evidence": "我要连夜过河，给东岸的姐姐送药。", "event": 1, "beat": 3,
             "carrier": "voice"},
            {"claim": "小船在河心遇大风剧烈摇晃",
             "evidence": "小船行到河心，忽然起了大风，船身剧烈摇晃。", "event": 2, "beat": 2,
             "carrier": "voice"},
            {"claim": "天快亮时小船靠上东岸", "evidence": "天快亮时，小船终于靠上了东岸。",
             "event": 3, "beat": 1, "carrier": "visual"},
        ],
    }
    a.update(over)
    return a


class _Svc:
    def __init__(self, texts):
        self.texts, self.schemas = list(texts), []

    def generate(self, req):
        self.schemas.append(str((req.json_schema or {}).get("name") or ""))
        _t = self.texts.pop(0)
        if _t is None:
            return TextGenerationResult(success=False, call_id="x", error="boom")
        return TextGenerationResult(success=True, call_id="c%d" % len(self.schemas),
                                    text=_t if isinstance(_t, str)
                                    else json.dumps(_t, ensure_ascii=False))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(lw.PLAN_MODE_ENV, raising=False)
    monkeypatch.delenv("STEP01_PRESENTATION_MODE", raising=False)
    monkeypatch.setattr("step01_script_gen.outline.round_archive_writer", lambda *a, **k: None)
    yield


def _gen(svc, tmp_path=None):
    return lw.generate_lightweight_outline_result(
        NOVEL, service=svc, cache_path=(tmp_path / "chapter_outline.cache.json")
        if tmp_path else None)


def test_mode_setting_default_and_unknown():
    assert lw.plan_mode_setting() == "legacy"
    assert lw.plan_mode_setting("lightweight") == "lightweight"
    with pytest.raises(ValueError):
        lw.plan_mode_setting("turbo")


def test_unknown_mode_rejected_before_any_send(monkeypatch):
    monkeypatch.setenv(lw.PLAN_MODE_ENV, "turbo")
    started = []
    monkeypatch.setattr(ce, "_run_tasks", lambda *a, **k: started.append(1) or {})
    with pytest.raises(ValueError):
        ce.extract_all_concurrent(NOVEL, None, None, enable_characters=False,
                                  enable_scene_objects=False)
    assert started == []


def test_default_mode_dispatches_legacy_generator(monkeypatch):
    calls = []
    monkeypatch.setattr("step01_script_gen.outline.generate_outline_result",
                        lambda *a, **k: calls.append("legacy") or {"executable": False})
    monkeypatch.setattr(lw, "generate_lightweight_outline_result",
                        lambda *a, **k: calls.append("lightweight") or {})
    ce.extract_all_concurrent(NOVEL, None, None, enable_characters=False,
                              enable_scene_objects=False)
    assert calls == ["legacy"]


def test_success_is_one_request_and_consumable(tmp_path):
    svc = _Svc([_answer()])
    res = _gen(svc, tmp_path)
    assert res["executable"] is True, res["failure"]
    assert svc.schemas == ["lightweight_outline"]   # 无 delta、无计划层内容核验
    plan = res["plan"]
    assert "plan_verification" not in plan
    assert plan["lightweight_plan"]["content_verification"] == "not_run"
    assert plan_acceptance_problems(plan, NOVEL) == []
    assert plan["dialogue_lines"] == ["这么晚了，你要去哪里？", "我要连夜过河，给东岸的姐姐送药。"]
    assert len(plan["shot_beats"]) == 6 and len(plan["main_events"]) == 3
    assert plan_has_carrier_contract(plan)
    # 生成前门（同一入口）放行；锚点与执行契约可渲染，说话人随台词交给分镜
    check_plan_before_generation(copy.deepcopy(plan), novel_text=NOVEL)
    assert outline_to_anchor(copy.deepcopy(plan), NOVEL)
    contract = render_execution_contract(plan)
    assert "（说话人：阿青）" in contract and "（说话人：老船夫）" in contract
    # 缓存单独成文件，且命中时零请求
    assert (tmp_path / lw.CACHE_FILE_NAME).exists()
    assert not (tmp_path / "chapter_outline.cache.json").exists()
    svc2 = _Svc([])
    res2 = _gen(svc2, tmp_path)
    assert res2["executable"] and res2["source"] == "cache" and svc2.schemas == []


def test_one_retry_after_invalid_content():
    bad = _answer()
    bad["events"][0]["beats"][1]["voice_text"] = "原文里没有这句话"
    svc = _Svc([bad, _answer()])
    res = _gen(svc)
    assert res["executable"] is True
    assert svc.schemas == ["lightweight_outline", "lightweight_outline"]
    assert res["revision"]["calls"] == 2


def test_two_failures_stop_without_storyboard():
    bad = _answer()
    bad["facts"][2]["carrier"] = "voice"          # 指向无声音节拍
    svc = _Svc([bad, "不是 JSON"])
    res = _gen(svc)
    assert res["executable"] is False and res["failure"]["code"] == "outline_invalid"
    assert svc.schemas == ["lightweight_outline", "lightweight_outline"]


def test_call_failure_is_not_retried():
    svc = _Svc([None])
    res = _gen(svc)
    assert res["executable"] is False and res["failure"]["code"] == "call_failed"
    assert len(svc.schemas) == 1


def test_wrong_identity_is_not_waived():
    plan = _gen(_Svc([_answer()]))["plan"]
    forged = copy.deepcopy(plan)
    forged["lightweight_plan"]["novel_digest"] = "0" * 64
    codes = [p["code"] for p in plan_acceptance_problems(forged, NOVEL)]
    assert "lightweight_plan_identity_invalid" in codes
    stripped = copy.deepcopy(plan)
    stripped.pop("lightweight_plan")
    codes = [p["code"] for p in plan_acceptance_problems(stripped, NOVEL)]
    assert "lightweight_plan_identity_invalid" in codes


def test_missing_plan_refs_still_blocks_candidates():
    plan = _gen(_Svc([_answer()]))["plan"]
    script = {"plan_id": "", "shots": [{"index": 1, "narration_segment": "x"}]}
    ok, violations, _declared = check_event_contract(script, plan, require_declaration=True)
    assert ok is False and violations[0]["code"] == "plan_refs_missing"


def test_duplicate_voice_and_missing_speaker_rejected():
    a = _answer()
    a["events"][1]["beats"][1]["voice_text"] = a["events"][0]["beats"][2]["voice_text"]
    a["events"][0]["beats"][1]["speaker"] = ""
    codes = {p["code"] for p in lw.answer_problems(a)}
    assert {"lightweight_voice_duplicate", "lightweight_speaker_missing"} <= codes


def test_hybrid_presentation_is_rejected_before_send(monkeypatch):
    monkeypatch.setenv(lw.PLAN_MODE_ENV, "lightweight")
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")
    with pytest.raises(ValueError):
        ce.extract_all_concurrent(NOVEL, None, None, enable_characters=False,
                                  enable_scene_objects=False)
