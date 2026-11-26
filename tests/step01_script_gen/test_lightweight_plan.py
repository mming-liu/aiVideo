# -*- coding: utf-8 -*-
"""轻量规划离线验收：原稿、定点声音修订、拒收及生成前放行.

匿名最小情节 + 假大纲服务；不读 `.tmp` / `output`，不发真实请求。
"""
from __future__ import annotations

import copy
import json

import pytest

from _text_gen.models import TextGenerationResult
from step01_script_gen import concurrent_extract as ce
from step01_script_gen import lightweight_plan as lw
from step01_script_gen import lightweight_voice_repair as vr
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
        self.prompts = getattr(self, "prompts", []) + [req.user_prompt]
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
    assert lw.plan_mode_setting() == "unified"
    assert lw.plan_mode_setting("unified") == "unified"
    for bad in ("legacy", "lightweight", "turbo"):
        with pytest.raises(ValueError):
            lw.plan_mode_setting(bad)


def test_unknown_mode_rejected_before_any_send(monkeypatch):
    for bad in ("legacy", "lightweight", "turbo"):
        monkeypatch.setenv(lw.PLAN_MODE_ENV, bad)
        started = []
        monkeypatch.setattr(ce, "_run_tasks", lambda *a, **k: started.append(1) or {})
        with pytest.raises(ValueError):
            ce.extract_all_concurrent(NOVEL, None, None, enable_characters=False,
                                      enable_scene_objects=False)
        assert started == []


def test_default_mode_dispatches_unified_generator(monkeypatch):
    from step01_script_gen import unified_plan as up
    calls = []
    monkeypatch.setattr("step01_script_gen.outline.generate_outline_result",
                        lambda *a, **k: calls.append("legacy") or {"executable": False})
    monkeypatch.setattr(lw, "generate_lightweight_outline_result",
                        lambda *a, **k: calls.append("lightweight") or {})
    monkeypatch.setattr(up, "generate_unified_outline_result",
                        lambda *a, **k: calls.append("unified") or {"executable": False})
    ce.extract_all_concurrent(NOVEL, None, None, enable_characters=False,
                              enable_scene_objects=False)
    assert calls == ["unified"]


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


def test_source_error_without_a_local_voice_fix_stops():
    bad = _answer()
    bad["events"][0]["beats"][1]["voice_text"] = "原文里没有这句话"
    svc = _Svc([bad, _answer()])
    res = _gen(svc)
    assert not res["executable"]
    assert svc.schemas == ["lightweight_outline"]


def _long_n1_case():
    kept = "小船行到河心，忽然起了大风，船身剧烈摇晃。"
    line = "河面的水波一层一层朝两岸铺开，两个人看着远处渐渐升起的白雾，谁都没有出声。" + kept
    source = NOVEL.replace(kept, line)
    answer = _answer()
    answer["events"][1]["beats"][1]["voice_text"] = line
    return kept, line, source, answer


def test_only_long_voice_is_replaced_in_the_single_revision():
    kept, line, source, answer = _long_n1_case()
    svc = _Svc([answer, {"edits": [{"id": "N1", "text": kept}], "unfixable": []}])
    res = lw.generate_lightweight_outline_result(source, service=svc)
    assert res["executable"], res["failure"]
    assert svc.schemas == ["lightweight_outline", "lightweight_voice_edits"]
    assert all(value in svc.prompts[0]
               for value in ("10 条", "280 个汉字", "42 个汉字", f"55–{vr.target_sec():g}"))
    assert "只有这一次修订机会" in svc.prompts[1]
    rows = vr.prepare(answer, source)["rows"]
    assert [r["id"] for r in rows if r["editable"]] == ["N1"]
    assert all(t in line and vr._check("narration", t, 11)["est_sec"] <= 11
               for t in rows[-1]["replacements"])
    expected, _ = lw.build_plan(_answer(), source)
    for field in ("dialogue_lines", "narration_lines", "shot_beats", "main_events", "voice_units"):
        assert res["plan"][field] == expected[field]
    check_plan_before_generation(res["plan"], novel_text=source)


def test_whole_answer_redraw_uses_up_the_only_revision(tmp_path):
    """整稿重答是非法修订：消耗唯一一次修订机会，不再请求，基线保持、不写缓存."""
    kept, line, source, answer = _long_n1_case()
    svc = _Svc([answer, _answer()])
    res = lw.generate_lightweight_outline_result(source, service=svc, cache_path=tmp_path / "c")
    assert not res["executable"]
    assert svc.schemas == ["lightweight_outline", "lightweight_voice_edits"]
    assert res["acceptance"]["codes"] == ["lightweight_voice_edit_rejected"]
    assert res["acceptance"]["residual"] == {"protocol": ["lightweight_voice_edit_rejected"]}
    assert not (tmp_path / lw.CACHE_FILE_NAME).exists()
    baseline, _ = lw.build_plan(answer, source)
    assert res["plan"]["narration_lines"] == baseline["narration_lines"]


def test_budget_trim_requires_full_reduction_and_preserves_beats(monkeypatch):
    monkeypatch.setenv("STEP01_VOICE_ALLOCATION_MAX_LINES", "12")
    monkeypatch.setenv("STEP01_VOICE_ALLOCATION_MAX_HAN", "600")
    answer = _answer()
    source = NOVEL
    additions = []
    for location in ("码头", "山坡", "田边", "路口", "溪边", "小桥", "山脚", "镇口", "岗亭"):
        text = f"远处的{location}亮起微弱灯火，守夜人缓缓提起沉重木箱，沿着青石台阶，慢慢走向岸边小船，回头再看。"
        source += text + "\n"
        beat = dict(text=f"{location}亮灯", purpose="交代环境", source=text,
                    voice_kind="narration", voice_text=text, speaker="")
        additions.append(beat)
    answer["events"][1]["beats"][1]["voice_text"] = "小船行到河心，风浪越发猛烈，船身左右摇晃不停，阿青紧紧抱住木箱，一刻也不敢松手。"
    source += answer["events"][1]["beats"][1]["voice_text"] + "\n"
    answer["events"][1]["beats"][1]["source"] = answer["events"][1]["beats"][1]["voice_text"]
    answer["events"][-1]["beats"].extend(additions)
    ctx = vr.prepare(answer, source)
    assert ctx is not None and ctx["seconds"] > vr.ceiling_sec()
    rows = [r for r in ctx["rows"] if r["text"] in {b["voice_text"] for b in additions}]
    deletes = [{"id": r["id"], "text": ""} for r in rows]
    # trim 只改达标所需的几句：一次修订删掉超出部分即可，其余声音不动
    need = ctx["seconds"] - vr.target_sec()
    chosen, cut = [], 0.0
    for r in sorted(rows, key=lambda r: -r["sec"]):
        if cut >= need:
            break
        chosen.append({"id": r["id"], "text": ""})
        cut += r["sec"]
    assert len(chosen) < len(deletes)
    svc = _Svc([answer, {"edits": chosen, "unfixable": []}])
    res = lw.generate_lightweight_outline_result(source, service=svc)
    assert res["executable"], res["failure"]
    assert svc.schemas == ["lightweight_outline", "lightweight_voice_edits"]
    assert f"至少 {round(ctx['seconds'] - vr.target_sec(), 2)} 秒" in svc.prompts[1]
    assert f"硬上限 {vr.ceiling_sec():g}s" in svc.prompts[1]
    assert f"软目标约 {vr.target_sec():.0f}s" in svc.prompts[1]
    assert len(res["plan"]["shot_beats"]) == 6 + len(additions)
    kept_texts = {b["voice_text"] for b in additions} - {
        r["text"] for r in rows if r["id"] in {c["id"] for c in chosen}}
    narr = [n["text"] if isinstance(n, dict) else n for n in res["plan"]["narration_lines"]]
    assert kept_texts <= set(narr)                       # 没点名的声音原样保留
    check_plan_before_generation(res["plan"], novel_text=source)


def test_partial_progress_stops_after_the_single_revision(tmp_path, monkeypatch):
    """有进展但仍超限：记录进展、给出分账残余，不再发第三次请求，不写缓存."""
    monkeypatch.setenv("STEP01_VOICE_ALLOCATION_MAX_LINES", "12")
    monkeypatch.setenv("STEP01_VOICE_ALLOCATION_MAX_HAN", "600")
    answer = _answer()
    source = NOVEL
    answer["events"][1]["beats"][1]["voice_text"] = "小船行到河心，风浪越发猛烈，船身左右摇晃不停，阿青紧紧抱住木箱，一刻也不敢松手。"
    answer["events"][1]["beats"][1]["source"] = answer["events"][1]["beats"][1]["voice_text"]
    source += answer["events"][1]["beats"][1]["voice_text"] + "\n"
    for location in ("码头", "山坡", "田边", "路口", "溪边", "小桥", "山脚", "镇口", "岗亭"):
        text = f"远处的{location}亮起微弱灯火，守夜人缓缓提起沉重木箱，沿着青石台阶，慢慢走向岸边小船，回头再看。"
        source += text + "\n"
        answer["events"][-1]["beats"].append(dict(text=f"{location}亮灯", purpose="交代环境",
                                                  source=text, voice_kind="narration",
                                                  voice_text=text, speaker=""))
    ctx = vr.prepare(answer, source)
    assert ctx is not None and ctx["seconds"] > vr.ceiling_sec()
    one = next(r for r in ctx["rows"] if r["deletable"])
    svc = _Svc([answer, {"edits": [{"id": one["id"], "text": ""}], "unfixable": []}])
    res = lw.generate_lightweight_outline_result(source, service=svc, cache_path=tmp_path / "c")
    assert not res["executable"]
    assert svc.schemas == ["lightweight_outline", "lightweight_voice_edits"]
    prog = res["revision"]["voice_progress"]
    assert len(prog) == 1 and prog[0]["to_sec"] < prog[0]["from_sec"]
    assert "budget" in res["acceptance"]["residual"]
    reason = next(p["reason"] for p in res["failure"]["problems"]
                  if p["code"] == "lightweight_voice_budget")
    assert "总时长" in reason and f"> {vr.ceiling_sec():g}s" in reason
    assert not (tmp_path / lw.CACHE_FILE_NAME).exists()


@pytest.mark.parametrize("edit", [{"id":"D2","text":""}, {"id":"D100","text":"新增"},
                                   {"id":"N1","text":"另一段的发言"}, {"id":"D1","text":""}])
def test_invalid_local_edits_leave_baseline_and_cache_untouched(edit, tmp_path):
    answer = _answer()
    line = "屋檐的水滴落在青石板上，走廊的影子随着灯光晃动，守门人站在雨中又等了很久，等到最后一个行人也离开了渡口。"
    answer["events"][0]["beats"][0].update(voice_kind="narration", voice_text=line)
    source = NOVEL + line
    svc = _Svc([answer, {"edits": [edit]}])
    result = lw.generate_lightweight_outline_result(source, service=svc, cache_path=tmp_path/"cache")
    assert not result["executable"] and len(svc.schemas) == 2
    assert result["acceptance"]["codes"] == ["lightweight_voice_edit_rejected"]
    assert not (tmp_path / lw.CACHE_FILE_NAME).exists()
    expected, _ = lw.build_plan(answer, source)
    assert result["plan"]["shot_beats"] == expected["shot_beats"]
    assert result["plan"]["voice_units"] == expected["voice_units"]


def test_invalid_structure_stops_without_a_new_outline():
    bad = _answer()
    bad["facts"][2]["carrier"] = "voice"          # 指向无声音节拍
    svc = _Svc([bad, "不是 JSON"])
    res = _gen(svc)
    assert res["executable"] is False and res["failure"]["code"] == "outline_invalid"
    assert svc.schemas == ["lightweight_outline"]


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


NOVEL_B = ("城门口贴出了一张告示。\n"
           "守卫高声念道：“自今日起，夜里不许出城。”\n"
           "人群里一阵骚动，卖菜的老妇转身就走。\n"
           "掌柜低声对伙计说：“把后门的货先搬进来。”\n"
           "伙计扛起两只麻袋，从后门进了库房。\n"
           "天黑以后，城门在吱呀声里合上了。\n"
           "整条街只剩下巡夜人的梆子声。\n")


def _answer_b():
    """另一份内容：4 个事件、2 句台词、2 条旁白、事实比例不同，编号重映射."""
    def _b(text, src, kind="none", vt="", sp=""):
        return {"text": text, "purpose": "推进", "source": src, "voice_kind": kind,
                "voice_text": vt, "speaker": sp}
    return {
        "characters": ["守卫", "掌柜", "伙计"], "emotional_arc": "平静→不安→沉寂",
        "climax": "城门合上", "end_hook": "只剩梆子声", "omittable": ["老妇离开"],
        "events": [
            {"summary": "告示宣布夜禁", "beats": [
                _b("守卫站在告示前高声宣读", "守卫高声念道", "dialogue",
                   "自今日起，夜里不许出城。", "守卫")]},
            {"summary": "掌柜吩咐收货", "beats": [
                _b("掌柜凑近伙计耳语", "掌柜低声对伙计说", "dialogue",
                   "把后门的货先搬进来。", "掌柜"),
                _b("伙计扛着麻袋走进库房", "伙计扛起两只麻袋，从后门进了库房。")]},
            {"summary": "城门关闭", "beats": [
                _b("城门缓缓合拢", "天黑以后，城门在吱呀声里合上了。", "narration",
                   "天黑以后，城门在吱呀声里合上了。")]},
            {"summary": "夜街寂静", "beats": [
                _b("空街上巡夜人提灯走过", "整条街只剩下巡夜人的梆子声。", "narration",
                   "整条街只剩下巡夜人的梆子声。")]},
        ],
        "facts": [
            {"claim": "官府告示规定自今日起夜里不许出城",
             "evidence": "自今日起，夜里不许出城。", "event": 1, "beat": 1, "carrier": "voice"},
            {"claim": "掌柜让伙计把后门的货先搬进来，伙计随即把麻袋扛进库房",
             "evidence": "伙计扛起两只麻袋，从后门进了库房。", "event": 2, "beat": 2,
             "carrier": "visual"},
        ],
    }


def test_second_content_sample_compiles_through_same_entry():
    svc = _Svc([_answer_b()])
    res = lw.generate_lightweight_outline_result(NOVEL_B, service=svc)
    assert res["executable"] is True, res["failure"]
    plan = res["plan"]
    assert len(plan["main_events"]) == 4 and len(plan["shot_beats"]) == 5
    assert [e["text"] for e in plan["narration_lines"]] == [
        "天黑以后，城门在吱呀声里合上了。", "整条街只剩下巡夜人的梆子声。"]
    assert plan["required_facts"][0].endswith("承载：B1/台词1")
    assert plan["required_facts"][1].endswith("承载：B3/画面")
    assert "（说话人：掌柜）" in render_execution_contract(plan)
    # 同一回答换到另一部原文：逐字核验必须拒收（不是靠作品名放行）
    res_x = lw.generate_lightweight_outline_result(NOVEL, service=_Svc([_answer_b(), _answer_b()]))
    assert res_x["executable"] is False


def test_hybrid_presentation_is_rejected_before_send(monkeypatch):
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")
    with pytest.raises(ValueError):
        ce.extract_all_concurrent(NOVEL, None, None, enable_characters=False,
                                  enable_scene_objects=False)


# ---------------------------------------------------------------------------
# 合并自 cff77a7 的匿名情节，改走 edits 定点修正协议（2026-11-22 合并）
# ---------------------------------------------------------------------------
LONG_C = ("你若执意要走出了事可怨不得我，这河水深得很，夜里行船最是危险，"
          "我这条船家也担待不起。")
SHORT_C = "你若执意要走出了事可怨不得我"   # 同一引语的逐字分句
NOVEL_C = ("黄昏时分，阿青提着灯笼走进渡口。\n"
           "老船夫摆手道：“" + LONG_C + "”\n"
           "老船夫又低声道：“你若执意要走，出了事可怨不得我。”\n"
           "阿青摇头。\n")


def _b(text, src, kind="none", vt="", sp=""):
    return {"text": text, "purpose": "推进", "source": src, "voice_kind": kind,
            "voice_text": vt, "speaker": sp}


def _answer_c(line):
    """3 个事件、1 句台词（超单镜）、1 条声音承载的事实."""
    return {
        "characters": ["阿青", "老船夫"], "emotional_arc": "迟疑→坚持",
        "climax": "老船夫劝阻", "end_hook": "阿青摇头", "omittable": [],
        "events": [
            {"summary": "阿青夜到渡口", "beats": [
                _b("阿青提着灯笼站在渡口", "黄昏时分，阿青提着灯笼走进渡口。"),
                _b("老船夫摆手劝阻", "老船夫摆手道", "dialogue", line, "老船夫")]},
            {"summary": "老船夫说明河险", "beats": [_b("老船夫指向黑沉沉的河面", "老船夫又低声道")]},
            {"summary": "阿青仍要过河", "beats": [_b("阿青摇头", "阿青摇头。")]},
        ],
        "facts": [{"claim": "老船夫劝阿青不要连夜走", "evidence": line,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }


def test_replace_offers_only_same_utterance_candidates_and_keeps_speaker():
    """超单镜台词只能换成**同一引语**内的逐字短句；相邻另一条引语不在候选里."""
    est = vr._check("dialogue", LONG_C, 12)["est_sec"]
    assert est > 12.0
    ctx = vr.prepare(_answer_c(LONG_C), NOVEL_C)
    assert ctx["scope"] == "replace"
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    opts = row["replacements"]
    assert 0 < len(opts) <= vr.MAX_OPTIONS
    assert all(t in LONG_C and vr._check("dialogue", t, 11)["est_sec"] <= 11 for t in opts)
    # replace 范围不塞预算最短项：菜单全部是完整候选（S11 ch1 的碎片诱因）
    assert set(opts) <= set(row["_all_options"])
    contained = [t for t in opts if any(t != o and t in o for o in opts)]
    assert len(contained) <= 2, opts
    assert "你若执意要走，出了事可怨不得我。" not in opts
    pick = opts[0]
    svc = _Svc([_answer_c(LONG_C), {"edits": [{"id": row["id"], "text": pick}],
                                     "unfixable": []}])
    res = lw.generate_lightweight_outline_result(NOVEL_C, service=svc)
    assert res["executable"] is True, res["failure"]
    assert res["revision"]["scope"] == "replace"
    assert svc.schemas == ["lightweight_outline", "lightweight_voice_edits"]
    assert "%.2f" % est in svc.prompts[1] or str(round(est, 2)) in svc.prompts[1]
    assert res["plan"]["dialogue_lines"] == [pick]
    assert lw.dialogue_speakers(res["plan"])[pick] == "老船夫"


NOVEL_T = ("黄昏时分，阿青提着灯笼走进渡口。\n"
           "老船夫问道：“这么晚了，你要去哪里，莫不是要连夜过河去东岸。”\n"
           "阿青答道：“姐姐病重，我要连夜过河送药，不能再等天亮了。”\n"
           "老船夫叹道：“河上风大浪急，你一个人撑船过河，我实在放心不下。”\n"
           "阿青摇头。\n")
T_LINES = ["这么晚了，你要去哪里，莫不是要连夜过河去东岸。",
           "姐姐病重，我要连夜过河送药，不能再等天亮了。",
           "河上风大浪急，你一个人撑船过河，我实在放心不下。"]


def _answer_t():
    """3 句可单镜表示的台词（合计超上限），第 1 句被事实引用、第 3 句可省."""
    return {
        "characters": ["阿青", "老船夫"], "emotional_arc": "焦急", "climax": "老船夫劝阻",
        "end_hook": "阿青摇头", "omittable": ["老船夫的第三句劝说"],
        "events": [
            {"summary": "阿青夜到渡口", "beats": [
                _b("阿青提着灯笼站在渡口", "黄昏时分，阿青提着灯笼走进渡口。"),
                _b("老船夫开口询问", "老船夫问道", "dialogue", T_LINES[0], "老船夫")]},
            {"summary": "阿青说明来意", "beats": [
                _b("阿青放下药箱回答", "阿青答道", "dialogue", T_LINES[1], "阿青"),
                _b("老船夫打量药箱", "阿青答道")]},
            {"summary": "老船夫仍不放心", "beats": [
                _b("老船夫伸手拦住阿青", "老船夫叹道", "dialogue", T_LINES[2], "老船夫"),
                _b("阿青握紧船桨", "阿青摇头。")]},
        ],
        "facts": [
            {"claim": "老船夫问阿青是否要连夜过河", "evidence": T_LINES[0],
             "event": 1, "beat": 2, "carrier": "voice"},
            {"claim": "阿青握紧船桨准备连夜过河", "evidence": "阿青摇头。",
             "event": 3, "beat": 2, "carrier": "visual"},
        ],
    }


@pytest.fixture
def _small_band(monkeypatch):
    monkeypatch.setattr("step01_script_gen.validation._REDGUO_DURATION_MAX", 15.0)
    monkeypatch.setattr("step01_script_gen.validation._REDGUO_DURATION_MIN", 5.0)


def test_trim_deletes_optional_voice_and_keeps_must_keep(_small_band):
    ctx = vr.prepare(_answer_t(), NOVEL_T)
    assert ctx["scope"] == "trim" and ctx["goal"] == 15.0
    by_text = {r["text"]: r for r in ctx["rows"]}
    assert by_text[T_LINES[0]]["deletable"] is False      # 被事实引用：只能换短
    assert by_text[T_LINES[2]]["deletable"] is True
    edits = [{"id": by_text[T_LINES[1]]["id"], "text": ""},
             {"id": by_text[T_LINES[2]]["id"], "text": ""}]
    svc = _Svc([_answer_t(), {"edits": edits}])
    res = lw.generate_lightweight_outline_result(NOVEL_T, service=svc)
    assert res["executable"] is True, res["failure"]
    assert res["revision"]["scope"] == "trim"
    assert res["plan"]["dialogue_lines"] == [T_LINES[0]]
    assert len(res["plan"]["shot_beats"]) == 6             # 节拍一个不少


def test_trim_refuses_deleting_must_keep_voice(_small_band):
    ctx = vr.prepare(_answer_t(), NOVEL_T)
    must = next(r for r in ctx["rows"] if r["text"] == T_LINES[0])
    with pytest.raises(ValueError, match="不允许"):
        vr.apply(_answer_t(), ctx, {"edits": [{"id": must["id"], "text": ""}]})


def test_whole_answer_redraw_is_rejected_and_baseline_kept(_small_band):
    """整稿重答（哪怕更短）不是 edits 事务 ⇒ 拒收，原稿不被替换."""
    redraw = _answer_t()
    redraw["events"][2]["beats"][0].update(voice_kind="none", voice_text="", speaker="")
    with pytest.raises(ValueError, match="不接受整稿"):
        vr.apply(_answer_t(), vr.prepare(_answer_t(), NOVEL_T), redraw)


# ---------------------------------------------------------------------------
# 2026-11-23：绑定证据取候选、不可修原因、无新问题守卫
# ---------------------------------------------------------------------------
PARA_A = "甲！"
PARA_B = "那个人昨夜已经死了，此刻正躺在后院的马车里，天气太热，再拖下去就瞒不住了。"
NOVEL_E = ("黄昏时分，乙推门进了小院。\n" + PARA_A + "\n" + PARA_B
           + "\n乙沉默不语，半晌才低声道：“我早该想到的。”\n")


def _answer_e(narration, evidence=PARA_B):
    """一条旁白被事实引用；正文把原文两段连成一句（不逐字），证据本身逐字命中."""
    return {
        "characters": ["甲", "乙"], "emotional_arc": "惊→定", "climax": "乙得知死讯",
        "end_hook": "乙沉默", "omittable": [],
        "events": [
            {"summary": "乙进院", "beats": [_b("乙推门进院", "黄昏时分，乙推门进了小院。")]},
            {"summary": "死讯", "beats": [
                _b("乙看向后院马车", PARA_B, "narration", narration, "")]},
            {"summary": "乙沉默", "beats": [
                _b("乙低头沉默", "乙沉默不语", "dialogue", "我早该想到的。", "乙")]},
        ],
        "facts": [{"claim": "那个人已死且尸体藏在后院马车里", "evidence": evidence,
                   "event": 2, "beat": 1, "carrier": "voice"}],
    }


JOINED = (PARA_A + "那个人昨夜已经死了，此刻正躺在后院的马车里，天气太热，再拖下去就瞒不住了，"
          "所有人都还以为他只是出门远行，谁也没有想到会有今天。")


def test_non_verbatim_voice_gets_candidates_from_its_bound_evidence():
    ctx = vr.prepare(_answer_e(JOINED), NOVEL_E)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert JOINED not in NOVEL_E and PARA_B in NOVEL_E
    assert PARA_B in row["replacements"]
    assert ctx["blocked"] == []
    svc = _Svc([_answer_e(JOINED), {"edits": [{"id": row["id"], "text": PARA_B}],
                                     "unfixable": []}])
    res = lw.generate_lightweight_outline_result(NOVEL_E, service=svc)
    assert res["executable"] is True, res["failure"]
    assert [n["text"] for n in res["plan"]["narration_lines"]] == [PARA_B]
    check_plan_before_generation(res["plan"], novel_text=NOVEL_E)


def test_no_legal_option_stops_before_sending_with_a_reason():
    """证据也不逐字（覆盖不了）⇒ 不发修订请求，记录逐条不可修原因."""
    ctx = vr.prepare(_answer_e(JOINED, evidence="原文里没有的证据"), NOVEL_E)
    assert [b["id"] for b in ctx["blocked"]] == ["N1"]
    svc = _Svc([_answer_e(JOINED, evidence="原文里没有的证据")])
    res = lw.generate_lightweight_outline_result(NOVEL_E, service=svc)
    assert res["executable"] is False and svc.schemas == ["lightweight_outline"]
    assert "lightweight_voice_unfixable" in res["acceptance"]["codes"]


def test_declared_unfixable_is_recorded_and_stops():
    row_id = next(r["id"] for r in vr.prepare(_answer_e(JOINED), NOVEL_E)["rows"] if r["editable"])
    svc = _Svc([_answer_e(JOINED),
                {"edits": [], "unfixable": [{"id": row_id, "reason": "缺了藏尸地点这一层"}]}])
    res = lw.generate_lightweight_outline_result(NOVEL_E, service=svc)
    assert res["executable"] is False and len(svc.schemas) == 2
    probs = res["failure"]["problems"]
    assert [p["code"] for p in probs] == ["lightweight_voice_declared_unfixable"]
    assert "藏尸地点" in probs[0]["reason"]


def test_empty_edits_without_reason_is_rejected_without_retry():
    svc = _Svc([_answer_e(JOINED), {"edits": [], "unfixable": []}])
    res = lw.generate_lightweight_outline_result(NOVEL_E, service=svc)
    assert res["executable"] is False and len(svc.schemas) == 2
    assert res["acceptance"]["codes"] == ["lightweight_voice_edit_rejected"]
    assert res["revision"]["voice_progress"] == []


def test_unfixable_must_name_an_editable_voice():
    ctx = vr.prepare(_answer_e(JOINED), NOVEL_E)
    with pytest.raises(ValueError, match="unfixable"):
        vr.apply(_answer_e(JOINED), ctx, {"edits": [], "unfixable": [{"id": "D9", "reason": "x"}]})


# ---------------------------------------------------------------------------
# S06（2026-11-23）：分镜请求里的声音时长说明——从实际发送的请求读
# ---------------------------------------------------------------------------
def _capture_storyboard_prompt(monkeypatch, plan, novel):
    import step01_script_gen.main as m
    box = {}

    class _Stop(RuntimeError):
        pass

    class _Cap:
        def generate(self, req):
            box["prompt"] = req.user_prompt
            box["schema"] = (req.json_schema or {}).get("name")
            raise _Stop("stop-after-capture")
    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Cap())
    with pytest.raises(_Stop):
        m.gen_spy_script(novel, outline=copy.deepcopy(plan))
    return box


def test_storyboard_request_carries_same_source_voice_timing(monkeypatch):
    from step01_script_gen.plan_feasibility import budget_band, voice_entry_costs
    plan = _gen(_Svc([_answer()]))["plan"]
    box = _capture_storyboard_prompt(monkeypatch, plan, NOVEL)
    p = box["prompt"]
    assert "六、声音时长" in p
    rows = voice_entry_costs(plan)
    assert rows
    for r in rows:                                   # 每条声音都在，读数与验收同源
        if r["kind"] == "dialogue":
            assert "台词%s" % r["index"] in p
            assert "%.2fs" % r["body_sec"] in p and "%.2fs" % r["est_sec"] in p
        else:
            assert "N%s（旁白）：播出正文约 %.2fs" % (r["index"], r["body_sec"]) in p
    tot = round(sum(r["body_sec"] for r in rows), 2)
    assert "全章声音合计约 %.2fs" % tot in p
    _lo, _hi = budget_band()
    assert "正片合法区间 %g–%gs" % (_lo, _hi) in p
    assert "同时发生" in p and "相加" in p           # 不把声音和画面机械相加；同镜多条按顺序相加
    # 不伪造逐镜最短时长；契约其余部分仍在（全部选用台词）
    assert "镜头1" not in p.split("六、声音时长", 1)[1]
    for line in plan["dialogue_lines"]:
        assert (line if isinstance(line, str) else line["text"]) in p


def test_dialogue_speaker_shown_in_timing_block(monkeypatch):
    plan = _gen(_Svc([_answer()]))["plan"]
    speakers = lw.dialogue_speakers(plan)
    p = _capture_storyboard_prompt(monkeypatch, plan, NOVEL)["prompt"]
    timing = p.split("六、声音时长", 1)[1]
    for who in set(speakers.values()):
        assert "（%s）" % who in timing


# ---------------------------------------------------------------------------
# 2026-11-23（复核 3 项）：跨说话人候选、压缩菜单漏短句
# ---------------------------------------------------------------------------
LONG_X = ("这河水深得很，夜里行船最是危险，你若执意要走，出了事可怨不得我，"
          "我这条船家也担待不起。")
OTHER_X = "东岸药铺打烊了，今晚抓不到药了。"
NOVEL_X = ("黄昏时分，阿青提着灯笼走进渡口。\n"
           "老船夫摆手道：“" + LONG_X + "”\n"
           "阿青低声道：“" + OTHER_X + "”\n"
           "阿青摇头。\n")


def _answer_x():
    """老船夫一句超单镜台词；绑定事实的证据却摘自阿青另一条引语."""
    return {
        "characters": ["阿青", "老船夫"], "emotional_arc": "迟疑", "climax": "劝阻",
        "end_hook": "阿青摇头", "omittable": [],
        "events": [
            {"summary": "阿青夜到渡口", "beats": [
                _b("阿青提着灯笼站在渡口", "黄昏时分，阿青提着灯笼走进渡口。"),
                _b("老船夫摆手劝阻", "老船夫摆手道", "dialogue", LONG_X, "老船夫")]},
            {"summary": "阿青低语", "beats": [_b("阿青低声说话", "阿青低声道")]},
            {"summary": "阿青摇头", "beats": [_b("阿青摇头", "阿青摇头。")]},
        ],
        "facts": [{"claim": "老船夫劝阿青不要连夜走", "evidence": OTHER_X,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }


def test_cross_speaker_evidence_is_not_a_replacement_source():
    """候选必须与原声音同一发言归属；别人的引语不能进「只改正文」的事务."""
    ctx = vr.prepare(_answer_x(), NOVEL_X)
    assert ctx["scope"] == "replace"
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert all("药铺" not in t for t in row["replacements"]), row["replacements"]
    with pytest.raises(ValueError, match="不允许"):
        vr.apply(_answer_x(), ctx, {"edits": [{"id": row["id"], "text": OTHER_X}]})


W_LINES = [
    "这河水深得很。夜里行船最是危险。你若执意要走就走吧。",
    "我这条船小得很。风浪大了容易翻。你还是等天亮再走。",
    "你一个人撑船。遇上急流怎么办。我实在放心不下。",
    "天亮以后水面清。行船也稳当。何必急着这一夜。",
    "夜里的风说来就来。谁也料不准。我劝你听一句。",
    "河心的漩涡最厉害。船进去了出不来。别拿命去赌。",
    "你带着药箱不方便。落水先顾哪一样。想想家里人等。",
    "我撑了三十年船。见过太多翻船事。不差这一晚上。",
    "家里人等不到你。比什么都着急。你该替他们想想。",
    "你若执意要走。我也拦不住你。只盼你平安到岸。",
]
NOVEL_W = ("黄昏时分，阿青提着灯笼走进渡口。\n"
           + "\n".join("老船夫第%d次开口道：“%s”" % (i + 1, t)
                       for i, t in enumerate(W_LINES))
           + "\n阿青摇头。\n")


def _answer_w():
    """10 条被事实引用的台词，每条 3 个短句：压缩时不能把首句短句全丢掉."""
    events = [{"summary": "劝阻一", "beats": []}, {"summary": "劝阻二", "beats": []},
              {"summary": "劝阻三", "beats": []}]
    facts = []
    for i, t in enumerate(W_LINES):
        e = i // 4
        events[e]["beats"].append(_b("老船夫开口劝阻%d" % (i + 1),
                                     "老船夫第%d次开口道" % (i + 1),
                                     "dialogue", t, "老船夫"))
        facts.append({"claim": "老船夫的劝告%d" % (i + 1), "evidence": t,
                      "event": e + 1, "beat": len(events[e]["beats"]), "carrier": "voice"})
    events.append({"summary": "阿青摇头", "beats": [_b("阿青摇头", "阿青摇头。")]})
    return {"characters": ["阿青", "老船夫"], "emotional_arc": "焦急", "climax": "劝阻",
            "end_hook": "阿青摇头", "omittable": [], "events": events, "facts": facts}


def test_non_verbatim_voice_does_not_borrow_another_speakers_quote():
    """正文不逐字时只能从**归属同一说话人**的证据引语取候选；归属不符就不给候选."""
    joined = OTHER_X + "你还是等天亮再去吧，这河水夜里实在走不得，我也不敢撑船。"
    ans = _answer_x()
    beat = ans["events"][0]["beats"][1]
    beat["voice_text"] = joined
    ctx = vr.prepare(ans, NOVEL_X)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert row["replacements"] == []
    assert [b["id"] for b in ctx["blocked"]] == [row["id"]]


def test_compressed_menu_does_not_predict_unfixable_when_short_options_exist(monkeypatch):
    """不可修预判不能只看压缩后的菜单：保留首句短句后同一样本可以一次修订通过."""
    monkeypatch.setattr("step01_script_gen.validation._REDGUO_DURATION_MAX", 30.0)
    monkeypatch.setattr("step01_script_gen.validation._REDGUO_DURATION_MIN", 5.0)
    ctx = vr.prepare(_answer_w(), NOVEL_W)
    assert ctx["scope"] == "trim"
    assert ctx["blocked"] == [], ctx["blocked"]
    mn = {r["id"]: min(vr._estimate_duration(t) for t in r["replacements"])
          for r in ctx["rows"] if r["editable"]}
    assert sum(mn.values()) <= 30.0 + 0.01
    assert all(v <= 3.1 for v in mn.values()), mn
    edits = [{"id": rid, "text": min(r["replacements"], key=vr._estimate_duration)}
             for r in ctx["rows"] for rid in [r["id"]] if r["editable"]]
    svc = _Svc([_answer_w(), {"edits": edits, "unfixable": []}])
    res = lw.generate_lightweight_outline_result(NOVEL_W, service=svc)
    assert res["executable"] is True, res["failure"]
    assert res["revision"]["scope"] == "trim"
    assert svc.schemas == ["lightweight_outline", "lightweight_voice_edits"]


# ---------------------------------------------------------------------------
# S07（2026-11-23）：长发言的句尾候选、事实绑定声音的证据内候选优先
# ---------------------------------------------------------------------------
#: 足够多的短分句：从发言开头枚举的窗口会先用满每段 40 条上限（冻结现场 D10 135 字即如此）
_LONG_SPEECH = ("你这话说得轻巧！城里的情形，你又不是不知道，铺子这半年，处处碰壁，账上没钱，"
                "伙计走了，客人少了，货也压着，想要翻身，全靠咱们，这几个人了。去年东家，"
                "还能替咱们，出些本钱，帮咱们，撑起一间，小小分号，如今东家，自己都，周转不开，"
                "东家的全部指望都押在咱们身上，你还指望东家会轻饶你吗？")
_KEY = "东家的全部指望都押在咱们身上，你还指望东家会轻饶你吗？"
NOVEL_L = ("夜里，乙在后堂坐下。\n掌柜冷笑道：“" + _LONG_SPEECH + "”\n乙低下头，不再说话。\n")


#: 证据与冻结现场同形：一长段（从「去年东家」到句末，中间只有逗号），不是那一句短句
_EVIDENCE_LONG = _LONG_SPEECH[_LONG_SPEECH.index("去年东家"):]


def _answer_l(evidence=_EVIDENCE_LONG):
    return {
        "characters": ["掌柜", "乙"], "emotional_arc": "冷→惧", "climax": "掌柜斥责",
        "end_hook": "乙低头", "omittable": [],
        "events": [
            {"summary": "乙进后堂", "beats": [_b("乙坐下", "夜里，乙在后堂坐下。")]},
            {"summary": "掌柜斥责", "beats": [
                _b("掌柜冷笑", "掌柜冷笑道", "dialogue", _LONG_SPEECH, "掌柜")]},
            {"summary": "乙低头", "beats": [_b("乙低头", "乙低下头，不再说话。")]},
        ],
        "facts": [{"claim": "掌柜指出东家不会轻饶他们，因为东家的全部指望都押在他们身上",
                   "evidence": evidence, "event": 2, "beat": 1, "carrier": "voice"}],
    }


def test_tail_sentence_of_a_long_speech_is_a_candidate():
    """句尾那句（承载事实的那句）必须进候选池——旧实现只从发言开头枚举、到上限就停."""
    ctx = vr.prepare(_answer_l(), NOVEL_L)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert any(_KEY in t or t in _KEY for t in row["_all_options"] if len(t) >= 10)
    assert any(t.endswith("你还指望东家会轻饶你吗？") for t in row["_all_options"])


def test_fact_bound_voice_menu_puts_evidence_options_first():
    ctx = vr.prepare(_answer_l(), NOVEL_L)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    head = row["replacements"][:vr.EVIDENCE_SLOTS]
    assert head and all(t in _EVIDENCE_LONG for t in head)        # 证据内候选排在最前
    assert any(t.endswith("你还指望东家会轻饶你吗？") for t in row["replacements"])
    assert len(row["replacements"]) <= vr.MAX_OPTIONS
    svc = _Svc([_answer_l(), {"edits": [{"id": row["id"], "text": row["replacements"][0]}],
                              "unfixable": []}])
    res = lw.generate_lightweight_outline_result(NOVEL_L, service=svc)
    assert res["executable"] is True, res["failure"]
    assert res["plan"]["dialogue_lines"] == [row["replacements"][0]]
    assert lw.dialogue_speakers(res["plan"])[row["replacements"][0]] == "掌柜"


def test_evidence_first_does_not_apply_to_fact_free_voice():
    a = _answer_l()
    a["facts"] = [{"claim": "乙在后堂坐下", "evidence": "夜里，乙在后堂坐下。",
                   "event": 1, "beat": 1, "carrier": "visual"}]
    row = next(r for r in vr.prepare(a, NOVEL_L)["rows"] if r["editable"])
    assert row["claims"] == []
    ranked, _pool = vr._options(row, NOVEL_L, 11)
    # 无事实绑定 ⇒ 没有证据位；replace 范围也不塞预算候选，菜单就是排名前四项
    assert row["replacements"] == vr._menu(ranked)

# ---------------------------------------------------------------------------
# 2026-11-24 S10：候选记录的身份、归属与窗口（复核 3 项 + 菜单/池同源）
# ---------------------------------------------------------------------------
LONG_A = ("这河水深得很，夜里行船最是危险，你若执意要走，出了事可怨不得我，"
          "我这条船家也担待不起。")
NOVEL_A = ("黄昏时分，甲站在渡口。\n"
           "甲对乙说道：“" + LONG_A + "”\n"
           "乙低声道：“东岸药铺打烊了，今晚抓不到药了。”\n")


def _answer_speaker(text, speaker, source="甲对乙说道"):
    return {
        "characters": list(dict.fromkeys(["甲", "乙", speaker])), "emotional_arc": "迟疑", "climax": "劝阻",
        "end_hook": "乙沉默", "omittable": [],
        "events": [
            {"summary": "甲到渡口", "beats": [
                _b("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _b("甲开口劝阻", source, "dialogue", text, speaker)]},
            {"summary": "乙低声", "beats": [_b("乙低声说话", "乙低声道")]},
            {"summary": "乙沉默", "beats": [_b("乙沉默", "乙沉默不语。")]},
        ],
        "facts": [{"claim": "甲劝乙不要连夜走", "evidence": text,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }


def test_addressee_name_is_not_the_speaker():
    """「甲对乙说」的主体是甲：乙出现在引语前不能把甲的话归给乙（S10 反例）."""
    ctx = vr.prepare(_answer_speaker(LONG_A, "乙"), NOVEL_A)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert row["replacements"] == [], row["replacements"]
    assert ctx["blocked"] and "归属" in ctx["blocked"][0]["reason"]
    # 同一段话归给甲时才是合法来源
    ctx_a = vr.prepare(_answer_speaker(LONG_A, "甲"), NOVEL_A)
    row_a = next(r for r in ctx_a["rows"] if r["editable"])
    assert row_a["replacements"]
    assert all("药铺" not in t for t in row_a["replacements"])


def test_duplicate_utterance_uses_the_matching_speaker_occurrence():
    """同一句在原文出现两次、归属不同：只用与声明说话人一致的那一处."""
    dup = LONG_A
    novel = ("甲道：“" + dup + "”\n"
             "乙道：“" + dup + "”\n")
    ans = _answer_speaker(dup, "乙", source="乙道")
    ctx = vr.prepare(ans, novel)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert row["replacements"], row["_candidate_notes"]
    assert all(c["line"] == 1 and c["origin"] == "own" for c in row["_pool"])


def test_ambiguous_owner_blocks_with_reason_instead_of_guessing():
    """引语前有两个名字（和/与）⇒ 主体歧义：不给候选，说明原因."""
    dup = LONG_A
    novel = "甲和乙都道：“" + dup + "”\n"
    ans = _answer_speaker(dup, "甲", source="甲和乙都道")
    ctx = vr.prepare(ans, novel)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert row["replacements"] == []
    assert "归属" in ctx["blocked"][0]["reason"]


def test_candidates_keep_line_span_identity():
    """候选保存原文行号与绝对区间；文本必须等于该区间切片（不再事后 find 猜位置）."""
    ctx = vr.prepare(_answer_c(LONG_C), NOVEL_C)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    lines = NOVEL_C.splitlines()
    assert row["_pool"]
    for c in row["_pool"]:
        assert lines[c["line"]][c["start"]:c["end"]] == c["text"]
        assert c["quote"] is not None and c["quote"][1] > c["end"] - 1


def test_first_sentence_window_survives_pool_and_menu():
    """首句也要有候选：长发言的第一句短句必须在池与菜单里（S10 窗口起点 0）."""
    first = "东家不会轻饶你们。"
    rest = ("后面那笔账还没算清，铺子里的人手也不够用，货还压在码头上，"
            "你们倒先想着怎么分钱了，这事要是捅到衙门里去，谁担得起。")
    text = first + rest
    novel = "掌柜道：“" + text + "”\n"
    ans = _answer_speaker(text, "掌柜", source="掌柜道")
    ctx = vr.prepare(ans, novel)
    (row,) = [r for r in ctx["rows"] if r["editable"]]
    assert any(c["text"] == first for c in row["_pool"])
    assert first in row["replacements"]
