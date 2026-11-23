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
    assert all(value in svc.prompts[0] for value in ("10 条", "280 个汉字", "42 个汉字", "55–80 秒"))
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


def test_budget_trim_requires_full_reduction_and_preserves_beats():
    answer = _answer()
    source = NOVEL
    additions = []
    for location in ("码头", "山坡", "田边", "路口", "溪边", "小桥", "山脚", "镇口", "岗亭"):
        text = f"远处的{location}亮起一盏灯，人们提起手里的行李，沿着岸边慢慢走向等候的船只。"
        source += text + "\n"
        beat = dict(text=f"{location}亮灯", purpose="交代环境", source=text,
                    voice_kind="narration", voice_text=text, speaker="")
        additions.append(beat)
    answer["events"][-1]["beats"].extend(additions)
    ctx = vr.prepare(answer, source)
    assert ctx["seconds"] > 90
    rows = [r for r in ctx["rows"] if r["text"] in {b["voice_text"] for b in additions}]
    deletes = [{"id": r["id"], "text": ""} for r in rows]
    # trim 只改达标所需的几句：一次修订删掉超出部分即可，其余声音不动
    need = ctx["seconds"] - 80
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
    assert f"至少 {round(ctx['seconds'] - 80, 2)} 秒" in svc.prompts[1]
    assert "硬上限 90s" in svc.prompts[1] and "引导目标 80s" in svc.prompts[1]
    assert len(res["plan"]["shot_beats"]) == 6 + len(additions)
    kept_texts = {b["voice_text"] for b in additions} - {
        r["text"] for r in rows if r["id"] in {c["id"] for c in chosen}}
    narr = [n["text"] if isinstance(n, dict) else n for n in res["plan"]["narration_lines"]]
    assert kept_texts <= set(narr)                       # 没点名的声音原样保留
    check_plan_before_generation(res["plan"], novel_text=source)


def test_partial_progress_stops_after_the_single_revision(tmp_path):
    """有进展但仍超限：记录进展、给出分账残余，不再发第三次请求，不写缓存."""
    answer = _answer()
    source = NOVEL
    for location in ("码头", "山坡", "田边", "路口", "溪边", "小桥", "山脚", "镇口", "岗亭"):
        text = f"远处的{location}亮起一盏灯，人们提起手里的行李，沿着岸边慢慢走向等候的船只。"
        source += text + "\n"
        answer["events"][-1]["beats"].append(dict(text=f"{location}亮灯", purpose="交代环境",
                                                  source=text, voice_kind="narration",
                                                  voice_text=text, speaker=""))
    ctx = vr.prepare(answer, source)
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
    assert "总时长" in reason and "> 90s" in reason
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
    monkeypatch.setenv(lw.PLAN_MODE_ENV, "lightweight")
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
    assert not any(a != b and a in b for a in opts for b in opts)   # 不列被包含的碎片
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
