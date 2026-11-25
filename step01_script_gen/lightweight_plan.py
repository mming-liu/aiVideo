# -*- coding: utf-8 -*-
"""轻量规划（`STEP01_PLAN_MODE=lightweight`，S14 M06/M07，2026-11-22）.

**一次**轻量大纲请求（声音预算超限时仅允许局部修订）：模型只给有序事件/节拍、必保留事实、
选用声音（逐字原话）、说话人与落点；编号、`载体：`、`承载：`、`fact_refs` 与可消费的
`chapter_outline` 全由代码派生（经 `presentation_contract.plan_from_view` 投影）。

生成前只做三类确定性校验，全部复用既有统一验收链（`outline.plan_acceptance_problems`）：
① 结构与引用可解析；② 声音正文逐字出自原文；③ 总发声预算与单条发言可播。
**不调用** `delta.v1` 修订协议、不调用计划层 LLM `content_verification`，也不伪造核验记录：
计划带 `planning_acceptance_version=lightweight_plan.v1` 与 `lightweight_plan` 身份块，
验收链据此**只**免除"必须有核验记录"这一条；承载、逐字、预算、`plan_refs`、事件与对白门
照常生效（缺承载字段不会让这些门失效——承载缺失本身就是验收问题）。

默认 `legacy`：不设或设为 `legacy` 时生产路径逐字不变。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path

PLAN_MODE_ENV = "STEP01_PLAN_MODE"
PLAN_MODE_LEGACY = "legacy"
PLAN_MODE_LIGHTWEIGHT = "lightweight"
PLAN_MODES = (PLAN_MODE_LEGACY, PLAN_MODE_LIGHTWEIGHT)
CONTRACT_VERSION = "lightweight_outline.v1"
#: 逻辑请求上限：首答 + 最多一次定点声音修订（2026-11-23 主线计划 S04）。非法回答也消耗这次
#: 修订机会，不暗中重答；底层传输重试由 provider 另计，不占这里的次数。
MAX_ATTEMPTS = 2

#: 残余问题分账（不是"小说不可满足"的判定）：同一问题码只属于一类。
RESIDUAL_CLASSES = ("budget", "structure", "source", "attribution", "claim_pending", "protocol")
CACHE_FILE_NAME = "chapter_outline.lightweight.cache.json"


def plan_mode_setting(explicit: str | None = None) -> str:
    """本次运行的规划模式；未知值**在任何发送之前**抛 `ValueError`."""
    _v = str(explicit if explicit is not None else os.getenv(PLAN_MODE_ENV) or "").strip()
    if not _v:
        return PLAN_MODE_LEGACY
    if _v not in PLAN_MODES:
        raise ValueError("非法规划模式 %s=%r（合法：%s）" % (PLAN_MODE_ENV, _v, list(PLAN_MODES)))
    return _v


def preflight_problems() -> str:
    """发送前的组合检查：轻量模式只支持旧呈现模式（legacy_audio）."""
    _mode = plan_mode_setting()
    if _mode != PLAN_MODE_LIGHTWEIGHT:
        return ""
    from step01_script_gen.outline import presentation_mode
    from shared.presentation_contract import MODE_LEGACY
    if presentation_mode() != MODE_LEGACY:
        return ("STEP01_PLAN_MODE=lightweight 只支持 %s 呈现模式（当前 %s）"
                % (MODE_LEGACY, presentation_mode()))
    return ""


def _novel_digest(novel_text) -> str:
    return hashlib.sha256(str(novel_text or "").encode("utf-8")).hexdigest()


def identity_problems(plan, novel_text: str = "") -> str:
    """轻量计划身份块是否成立；空串 = 成立。验收链凭它（而不是只凭版本号）免除核验记录."""
    _blk = (plan or {}).get("lightweight_plan") if isinstance(plan, dict) else None
    if not isinstance(_blk, dict):
        return "缺 `lightweight_plan` 身份块"
    if str(_blk.get("contract_version") or "") != CONTRACT_VERSION:
        return "合同版本 %r ≠ %s" % (_blk.get("contract_version"), CONTRACT_VERSION)
    if str(_blk.get("content_verification") or "") != "not_run":
        return "content_verification 必须如实记为 not_run"
    if novel_text and str(_blk.get("novel_digest") or "") != _novel_digest(novel_text):
        return "原文摘要与本次原文不符"
    return ""


#: 模型面 schema（扁平、无编号；编号由代码派生）
LIGHTWEIGHT_SCHEMA = {
    "name": "lightweight_outline",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "characters": {"type": "array", "items": {"type": "string"}},
            "emotional_arc": {"type": "string"},
            "climax": {"type": "string"},
            "end_hook": {"type": "string"},
            "omittable": {"type": "array", "items": {"type": "string"}},
            "events": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "beats": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "purpose": {"type": "string"},
                            "source": {"type": "string"},
                            "voice_kind": {"type": "string",
                                           "enum": ["none", "dialogue", "narration"]},
                            "voice_text": {"type": "string"},
                            "speaker": {"type": "string"},
                        },
                        "required": ["text", "purpose", "source", "voice_kind",
                                     "voice_text", "speaker"],
                        "additionalProperties": False}},
                },
                "required": ["summary", "beats"], "additionalProperties": False}},
            "facts": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                    "event": {"type": "integer"},
                    "beat": {"type": "integer"},
                    "carrier": {"type": "string", "enum": ["visual", "voice"]},
                },
                "required": ["claim", "evidence", "event", "beat", "carrier"],
                "additionalProperties": False}},
        },
        "required": ["characters", "emotional_arc", "climax", "end_hook", "omittable",
                     "events", "facts"],
        "additionalProperties": False,
    },
}

LIGHTWEIGHT_PROMPT = """# 任务：为下面这一章小说做**轻量分镜计划**（只做内容取舍，编号由代码生成）

## 要给出的内容
1. `events`：本章 3–6 个主要事件，**按时间顺序**。每个事件写 `summary`（一句话）和 `beats`
   （该事件的节拍，按发生顺序；每条节拍是一个叙事单元，不是一个镜头）。
2. 每条节拍：
   - `text`：画面上**可观察**的人物动作、姿态、道具与环境（可演，不写心理概括）；
   - `purpose`：这条节拍让观众知道什么；
   - `source`：**逐字**取自原文的一段依据（一字不改）；
   - `voice_kind`：这条节拍有没有声音——`none`（纯画面）/ `dialogue`（人物说出口的台词）/
     `narration`（原文叙述句作旁白）；
   - `voice_text`：有声音时，**逐字**取自原文、一字不改的那句话（台词只取引号里说出口的
     内容，不带「某某道：」）；没有声音时给空串；
   - `speaker`：台词的说话人（原文里是谁说的）；旁白与纯画面给空串。
   一条声音只放在**一条**节拍上；同一句原话不要放两次。单条声音要能在一个镜头里说完
   （约 {cap:g} 秒以内；太长就选原文里更短的完整一句）。
3. `facts`：观众必须得到的关键事实（3–12 条）。每条：
   - `claim`：一句话主张（主体、对象、先后、因果写清楚）；
   - `evidence`：**逐字**取自原文的证据；
   - `event` / `beat`：由哪个事件的第几条节拍承载（都从 1 开始数）；
   - `carrier`：`voice` = 由该节拍的声音说出来（该节拍必须有声音）；`visual` = 由该节拍的
     画面演出来。
4. `climax`、`end_hook`、`emotional_arc`、`characters`（出场角色）、`omittable`（可省略的内容）。

## 总时长（硬约束，代码会逐句估时核算）
正片 {band_min:g}–{band_max:g} 秒。全章旁白和对白合计不超过 {lines} 条、{han} 个汉字，
每条不超过 {line_han} 个汉字，声音目标 55–{target} 秒。交稿前逐句计数，为画面和停顿留时。
只选推动剧情、画面演不出来的话；能用画面演的不要再配旁白，不要把整段原文都放进声音。

## 原文
{novel_text}
"""


# ---------------------------------------------------------------------------
# 回答 → 视图 → 计划（编号与关系全由代码派生）
# ---------------------------------------------------------------------------
def _rec(code, path, reason) -> dict:
    return {"code": code, "kind": "plan", "index": None, "path": path,
            "reason": reason, "detail": reason,
            "basis": "轻量规划：回答须能确定性地编译成计划（编号与关系由代码派生）"}


def _s(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def answer_problems(answer) -> list:
    """回答形状与引用的确定性检查（agent_plan 可能把 json_schema 降成 json_object）."""
    _p: list = []
    if not isinstance(answer, dict):
        return [_rec("lightweight_answer_not_object", "", "回答不是 JSON 对象")]
    _events = answer.get("events")
    if not isinstance(_events, list) or not _events:
        return [_rec("lightweight_events_missing", "events", "`events` 必须是非空数组")]
    _seen_summary: set = set()
    _seen_voice: dict = {}
    _beats_per_event: list = []
    for _ei, _ev in enumerate(_events, 1):
        _path = "events[%d]" % (_ei - 1)
        if not isinstance(_ev, dict):
            _p.append(_rec("lightweight_event_invalid", _path, "事件 %d 不是对象" % _ei))
            _beats_per_event.append([])
            continue
        _sum = _s(_ev.get("summary"))
        if not _sum:
            _p.append(_rec("lightweight_event_invalid", _path + ".summary",
                           "事件 %d 缺 `summary`" % _ei))
        elif _sum in _seen_summary:
            _p.append(_rec("lightweight_event_duplicate", _path + ".summary",
                           "事件 %d 的 summary 与前面的事件重复（事件归属会有歧义）" % _ei))
        _seen_summary.add(_sum)
        _beats = _ev.get("beats")
        if not isinstance(_beats, list) or not _beats:
            _p.append(_rec("lightweight_event_beats_missing", _path + ".beats",
                           "事件 %d 没有节拍" % _ei))
            _beats_per_event.append([])
            continue
        _kinds = []
        for _bi, _b in enumerate(_beats, 1):
            _bp = "%s.beats[%d]" % (_path, _bi - 1)
            if not isinstance(_b, dict):
                _p.append(_rec("lightweight_beat_invalid", _bp, "节拍不是对象"))
                _kinds.append("none")
                continue
            if not _s(_b.get("text")):
                _p.append(_rec("lightweight_beat_invalid", _bp + ".text", "节拍缺画面正文"))
            _vk = _s(_b.get("voice_kind")) or "none"
            if _vk not in ("none", "dialogue", "narration"):
                _p.append(_rec("lightweight_voice_kind_invalid", _bp + ".voice_kind",
                               "voice_kind 只能是 none/dialogue/narration（现在 %r）" % _vk))
                _vk = "none"
            _vt = _s(_b.get("voice_text"))
            if _vk != "none" and not _vt:
                _p.append(_rec("lightweight_voice_text_missing", _bp + ".voice_text",
                               "有声音的节拍必须给逐字 `voice_text`"))
            if _vk == "none" and _vt:
                _p.append(_rec("lightweight_voice_kind_invalid", _bp + ".voice_kind",
                               "给了 voice_text 却标 voice_kind=none"))
            if _vk == "dialogue" and not _s(_b.get("speaker")):
                _p.append(_rec("lightweight_speaker_missing", _bp + ".speaker",
                               "台词必须给说话人"))
            if _vk != "none" and _vt:
                if _vt in _seen_voice:
                    _p.append(_rec("lightweight_voice_duplicate", _bp + ".voice_text",
                                   "同一句原话已放在 %s，不要放两次" % _seen_voice[_vt]))
                _seen_voice.setdefault(_vt, _bp)
            _kinds.append(_vk)
        _beats_per_event.append(_kinds)
    _facts = answer.get("facts")
    if not isinstance(_facts, list) or not _facts:
        _p.append(_rec("lightweight_facts_missing", "facts", "`facts` 必须是非空数组"))
        return _p
    for _fi, _f in enumerate(_facts, 1):
        _fp = "facts[%d]" % (_fi - 1)
        if not isinstance(_f, dict) or not _s(_f.get("claim")) or not _s(_f.get("evidence")):
            _p.append(_rec("lightweight_fact_invalid", _fp, "事实缺 claim / evidence"))
            continue
        _e, _b = _f.get("event"), _f.get("beat")
        if (isinstance(_e, bool) or isinstance(_b, bool) or not isinstance(_e, int)
                or not isinstance(_b, int) or not 1 <= _e <= len(_beats_per_event)
                or not 1 <= _b <= len(_beats_per_event[_e - 1])):
            _p.append(_rec("lightweight_fact_target_invalid", _fp + ".beat",
                           "事实 %d 指向的事件/节拍不存在（event=%r beat=%r）" % (_fi, _e, _b)))
            continue
        _car = _s(_f.get("carrier"))
        if _car not in ("visual", "voice"):
            _p.append(_rec("lightweight_fact_carrier_invalid", _fp + ".carrier",
                           "carrier 只能是 visual/voice"))
        elif _car == "voice" and _beats_per_event[_e - 1][_b - 1] == "none":
            _p.append(_rec("lightweight_fact_carrier_invalid", _fp + ".carrier",
                           "事实 %d 声明由声音承载，但事件 %d 第 %d 条节拍没有声音"
                           % (_fi, _e, _b)))
        # S13：可选的多承载（有序）；第一项必须与 event/beat 相同（身份不漂移）。
        _cars = _f.get("carriers")
        if _cars is not None:
            if not isinstance(_cars, list) or not _cars:
                _p.append(_rec("lightweight_fact_carriers_invalid", _fp + ".carriers",
                               "carriers 必须是非空数组"))
                continue
            _first = (_cars[0] or {}) if isinstance(_cars[0], dict) else {}
            if (_first.get("event"), _first.get("beat")) != (_e, _b):
                _p.append(_rec("lightweight_fact_carriers_invalid", _fp + ".carriers",
                               "carriers 的第一项必须与事实的 event/beat 相同"))
            for _ci, _c in enumerate(_cars, 1):
                if not isinstance(_c, dict):
                    _p.append(_rec("lightweight_fact_carriers_invalid",
                                   _fp + ".carriers[%d]" % (_ci - 1), "承载项必须是对象"))
                    continue
                _ce, _cb = _c.get("event"), _c.get("beat")
                if (isinstance(_ce, bool) or isinstance(_cb, bool)
                        or not isinstance(_ce, int) or not isinstance(_cb, int)
                        or not 1 <= _ce <= len(_beats_per_event)
                        or not 1 <= _cb <= len(_beats_per_event[_ce - 1])):
                    _p.append(_rec("lightweight_fact_carriers_invalid",
                                   _fp + ".carriers[%d]" % (_ci - 1),
                                   "承载项指向的事件/节拍不存在"))
                elif _car == "voice" and _beats_per_event[_ce - 1][_cb - 1] == "none":
                    _p.append(_rec("lightweight_fact_carriers_invalid",
                                   _fp + ".carriers[%d]" % (_ci - 1),
                                   "承载项节拍没有声音"))
    return _p


def build_view(answer) -> dict:
    """已过 `answer_problems` 的回答 → 旧模式可编辑视图（编号/引用由代码生成）."""
    from shared import presentation_contract as pc
    _events = [_s(e.get("summary")) for e in answer["events"]]
    _beats, _dlg, _nar, _beat_voice = [], [], [], {}
    _pos = {}
    for _ei, _ev in enumerate(answer["events"], 1):
        for _bi, _b in enumerate(_ev["beats"], 1):
            _uid = "b%02d" % (len(_beats) + 1)
            _pos[(_ei, _bi)] = _uid
            _vk = _s(_b.get("voice_kind")) or "none"
            _vt = _s(_b.get("voice_text"))
            if _vk == "dialogue":
                _vu = "d%02d" % (len(_dlg) + 1)
                _dlg.append({"uid": _vu, "text": _vt, "source": {"text": _vt}})
                _ref = {"kind": pc.KIND_DIALOGUE, "uid": _vu}
            elif _vk == "narration":
                _vu = "n%02d" % (len(_nar) + 1)
                _nar.append({"uid": _vu, "text": _vt, "source": {"text": _vt}})
                _ref = {"kind": pc.KIND_NARRATION, "uid": _vu}
            else:
                _ref = {"kind": pc.KIND_VISUAL, "uid": _uid}
            _beat_voice[_uid] = _ref
            _beats.append({"uid": _uid, "text": _s(_b.get("text")),
                           "event_uids": [_events[_ei - 1]],
                           "purpose": _s(_b.get("purpose")),
                           "evidence": _s(_b.get("source")), "source": {},
                           "voice_ref": _ref})
    _frozen, _binds = [], []
    _capacity = 1
    for _fi, _f in enumerate(answer["facts"], 1):
        _fu = "f%02d" % _fi
        _frozen.append({"uid": _fu, "claim": _s(_f.get("claim")),
                        "evidence": _s(_f.get("evidence"))})
        _targets = _f.get("carriers") or [{"event": _f["event"], "beat": _f["beat"]}]
        _refs = []
        for _t in _targets:
            _bu = _pos[(int(_t["event"]), int(_t["beat"]))]
            _refs.append(_beat_voice[_bu] if _s(_f.get("carrier")) == "voice"
                         else {"kind": pc.KIND_VISUAL, "uid": _bu})
        _capacity = max(_capacity, len(_refs))
        _binds.append({"fact_uid": _fu, "carriers": _refs})
    _chain = "→".join(
        "%s（%s）" % (_events[_ei - 1], "、".join(
            "B%d" % int(_pos[(_ei, _bi)][1:])
            for _bi in range(1, len(answer["events"][_ei - 1]["beats"]) + 1)))
        for _ei in range(1, len(_events) + 1))
    _basis = {
        "fact_uids": [f["uid"] for f in _frozen],
        "climax": _s(answer.get("climax")),
        "causal_chain": [_chain],
        "characters": [_s(x) for x in (answer.get("characters") or []) if _s(x)],
        "emotional_arc": _s(answer.get("emotional_arc")),
        "end_hook": _s(answer.get("end_hook")),
        "key_scenes": list(_events),
        "omittable": [_s(x) for x in (answer.get("omittable") or []) if _s(x)],
    }
    if answer.get("source_splits"):
        _basis["source_splits"] = copy.deepcopy(answer["source_splits"])
        if answer.get("source_preservation"):
            _basis["source_preservation"] = copy.deepcopy(answer["source_preservation"])
    _view = {"schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_LEGACY,
             "event_binding": "declared", "frozen_facts": _frozen, "frozen_events": _events,
             "beats": _beats, "dialogue_units": _dlg, "narration_units": _nar,
             "screen_cards": [], "fact_bindings": _binds,
             "hook_bindings": {"spoken_uids": [], "carriers": []},
             "evidence_updates": [], "plan_basis": _basis}
    if _capacity > 1:
        _view["carrier_capacity"] = _capacity
    return _view


def build_plan(answer, novel_text: str) -> tuple:
    """回答 → 可消费的 `chapter_outline`；返回 `(plan | None, problems)`.

    problems 为空只说明**编译成功**；可执行性由统一验收链判（见 `generate_*`）。
    """
    from shared import presentation_contract as pc
    from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LIGHTWEIGHT_ACCEPTANCE_VERSION
    from step01_script_gen.plan_coherence import mark_plan
    _p = answer_problems(answer)
    if _p:
        return None, _p
    _view = build_view(answer)
    try:
        _plan = pc.plan_from_view(_view)
    except pc.PresentationContractError as _exc:
        return None, [_rec(str((x or {}).get("code") or "lightweight_view_invalid"),
                           str((x or {}).get("path") or ""),
                           str((x or {}).get("reason") or "视图投影失败"))
                      for x in (_exc.problems or [])] or [
            _rec("lightweight_view_invalid", "", "视图投影失败")]
    _speakers = {}
    for _ev in answer["events"]:
        for _b in _ev["beats"]:
            if (_s(_b.get("voice_kind")) or "none") != "none":
                _speakers[_s(_b.get("voice_text"))] = _s(_b.get("speaker"))
    _plan = dict(_plan)
    _plan[LEGACY_ACCEPTANCE_FIELD] = LIGHTWEIGHT_ACCEPTANCE_VERSION
    #: 声音单位的**声明**（说话人来自模型回答，不是核验结论；无 verdict / verified）
    _plan["voice_units"] = (
        [{"unit_uid": u["uid"], "source_kind": "novel_speech",
          "speaker": _speakers.get(u["text"], ""), "echo_text": u["text"],
          "declared_by": "lightweight_outline"} for u in _view["dialogue_units"]]
        + [{"unit_uid": u["uid"], "source_kind": "novel_narration", "speaker": "",
            "echo_text": u["text"], "declared_by": "lightweight_outline"}
           for u in _view["narration_units"]])
    _plan["lightweight_plan"] = {"contract_version": CONTRACT_VERSION,
                                 "novel_digest": _novel_digest(novel_text),
                                 "content_verification": "not_run"}
    _plan = mark_plan(_plan)
    from step01_script_gen.plan_feasibility import evaluate_plan
    try:
        _rep = evaluate_plan(_plan, novel_text=novel_text)
        _plan["time_budget"] = "台词约%.0f秒，旁白约%.0f秒（代码按语速估算）" % (
            float(_rep.get("dialogue_sec") or 0), float(_rep.get("narration_sec") or 0))
    except Exception:                                                # noqa: BLE001
        _plan["time_budget"] = ""
    from step01_script_gen.outline import _validate_outline
    _ok = _validate_outline(copy.deepcopy(_plan), novel_text)
    if not _ok:
        from step01_script_gen.outline import outline_structure_problems
        return None, (outline_structure_problems(_plan)
                      or [_rec("lightweight_outline_invalid", "",
                               "计划未通过结构净化（台词逐字或字段形状）")])
    return _ok, []


def dialogue_speakers(plan) -> dict:
    """轻量计划声明的说话人：`{台词正文: 说话人}`（非轻量计划返回空表）."""
    if not isinstance(plan, dict) or not isinstance(plan.get("lightweight_plan"), dict):
        return {}
    return {str(u.get("echo_text") or ""): str(u.get("speaker") or "")
            for u in (plan.get("voice_units") or [])
            if isinstance(u, dict) and u.get("source_kind") == "novel_speech"
            and str(u.get("speaker") or "")}


# ---------------------------------------------------------------------------
# 生成入口（与 `outline.generate_outline_result` 同一结果契约）
# ---------------------------------------------------------------------------
def _prompt(novel_text: str) -> str:
    from shared.config import PROMPT_MAX_CHARS
    from step01_script_gen.plan_feasibility import budget_band
    from step01_script_gen.timecode import active_shot_cap
    from step01_script_gen import lightweight_voice_repair as vr
    _bmin, _bmax = budget_band()   # 与验收链同源，不另读配置常量
    return LIGHTWEIGHT_PROMPT.format(cap=float(active_shot_cap()),
                                     band_min=float(_bmin),
                                     band_max=float(_bmax),
                                     lines=vr.line_limit(), han=vr.MAX_HAN,
                                     line_han=vr.MAX_LINE_HAN, target=vr.TARGET_SEC,
                                     novel_text=str(novel_text or "")[:PROMPT_MAX_CHARS])


def _cache_key(prompt: str, model) -> str:
    from _text_gen.config import TextGenCfg
    from shared.fact_metadata import LIGHTWEIGHT_ACCEPTANCE_VERSION
    from step01_script_gen.outline import _outline_max_tokens
    from step01_script_gen.plan_coherence import VERSION as COHERENCE_VERSION
    _src = {"plan_mode": PLAN_MODE_LIGHTWEIGHT, "contract_version": CONTRACT_VERSION,
            "acceptance_version": LIGHTWEIGHT_ACCEPTANCE_VERSION,
            "coherence_version": COHERENCE_VERSION, "prompt": prompt,
            "schema": LIGHTWEIGHT_SCHEMA, "provider": TextGenCfg.PROVIDER,
            "model": model or TextGenCfg.default_text_model(), "temperature": 0.3,
            "max_tokens": _outline_max_tokens()}
    return hashlib.sha256(json.dumps(_src, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def cache_path_for(cache_path):
    """轻量模式的缓存**单独成文件**：legacy 读取方永远不会读到它，反之亦然."""
    return None if cache_path is None else Path(cache_path).with_name(CACHE_FILE_NAME)


def _read_cache(path, key, novel_text):
    from step01_script_gen.outline import plan_acceptance_problems
    try:
        _c = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(_c, dict) or _c.get("source_hash") != key \
            or _c.get("plan_mode") != PLAN_MODE_LIGHTWEIGHT:
        return None
    _plan = _c.get("outline")
    if not isinstance(_plan, dict) or identity_problems(_plan, novel_text):
        return None
    if plan_acceptance_problems(_plan, novel_text):
        return None
    return _plan


def _write_cache(path, key, plan) -> str:
    _tmp = Path(str(path) + ".tmp")
    try:
        _tmp.parent.mkdir(parents=True, exist_ok=True)
        _tmp.write_text(json.dumps({"source_hash": key, "plan_mode": PLAN_MODE_LIGHTWEIGHT,
                                    "contract_version": CONTRACT_VERSION, "outline": plan},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(_tmp, path)
        return ""
    except OSError as _exc:
        return "%s: %s" % (type(_exc).__name__, _exc)


def _residual_class(code: str) -> str:
    """问题码 → 残余类别（结构化分类，不从文案推断）."""
    c = str(code or "")
    if c.startswith(("budget_", "lightweight_voice_budget", "lightweight_voice_unfixable",
                     "duration_")):
        return "budget"
    if "speaker" in c or "attribution" in c or "ambiguous" in c:
        return "attribution"
    if c.startswith(("source_", "fact_evidence_")):
        return "source"
    if c.startswith(("structure_", "carrier_", "lightweight_fact_carrier", "plan_no_",
                     "acceptance_incomplete")):
        return "structure"
    if c.startswith(("lightweight_voice_edit", "lightweight_voice_declared", "response_not_json")):
        return "protocol"
    return "structure"


def residual_report(problems, claim_pending=()) -> dict:
    """按类分账的残余：{类别: [问题码…]}；`claim_pending` 单列需人工/后续核验的声音编号."""
    out = {k: [] for k in RESIDUAL_CLASSES}
    for p in problems or []:
        code = str((p or {}).get("code") or "")
        cls = _residual_class(code)
        if code not in out[cls]:
            out[cls].append(code)
    out["claim_pending"] = sorted(set(claim_pending))
    return {k: v for k, v in out.items() if v}


def _budget_block(ctx) -> str:
    """修订请求里的预算读数：硬上限与引导目标分开，条数/字数另列（数字全部由代码算）."""
    from step01_script_gen import lightweight_voice_repair as vr
    from step01_script_gen.plan_feasibility import budget_band
    from step01_script_gen.timecode import active_shot_cap
    _, ceiling = budget_band()
    rows = ctx.get("rows") or []
    sec = float(ctx.get("seconds") or 0.0)
    lines, han = len(rows), sum(int(r.get("han") or 0) for r in rows)
    cap = float(active_shot_cap())
    out = ["", "# 本稿预算读数（代码按同一估时入口计算）"]
    if sec > ceiling + 0.01:
        out.append("- 声音总时长 %.2fs，**硬上限 %.0fs**：至少减 %.2fs 才不超硬上限；"
                   "引导目标 %ds（至少减 %.2fs）。" % (sec, ceiling, sec - ceiling, vr.TARGET_SEC,
                                                     max(0.0, sec - vr.TARGET_SEC)))
    else:
        out.append("- 声音总时长 %.2fs，未超硬上限 %.0fs（引导目标 %ds）。" % (sec, ceiling, vr.TARGET_SEC))
    out.append("- 声音条数 %d（上限 %d）%s；汉字 %d（上限 %d）%s。"
               % (lines, vr.line_limit(),
                  "，超 %d 条" % (lines - vr.line_limit()) if lines > vr.line_limit() else "",
                  han, vr.MAX_HAN, "，超 %d 字" % (han - vr.MAX_HAN) if han > vr.MAX_HAN else ""))
    over = [r["id"] for r in rows if float(r.get("single_sec") or 0) > cap
            or int(r.get("han") or 0) > vr.MAX_LINE_HAN]
    if over:
        out.append("- 单条超 %.0fs 或超 %d 字：%s。" % (cap, vr.MAX_LINE_HAN, "、".join(over)))
    out.append("- 只有这一次修订机会；修订后仍不合格即停在计划层，不会再次请求。")
    return "\n".join(out)


def _over_limits(ctx) -> str:
    """`lightweight_voice_budget` 的具体超限项（不再笼统写"条数、汉字数或时长"）."""
    from step01_script_gen import lightweight_voice_repair as vr
    from step01_script_gen.plan_feasibility import budget_band
    from step01_script_gen.timecode import active_shot_cap
    _, ceiling = budget_band()
    rows = ctx.get("rows") or []
    parts = []
    if float(ctx.get("seconds") or 0) > ceiling + 0.01:
        parts.append("总时长 %.2fs > %.0fs" % (ctx["seconds"], ceiling))
    if len(rows) > vr.line_limit():
        parts.append("条数 %d > %d" % (len(rows), vr.line_limit()))
    han = sum(int(r.get("han") or 0) for r in rows)
    if han > vr.MAX_HAN:
        parts.append("汉字 %d > %d" % (han, vr.MAX_HAN))
    over = [r["id"] for r in rows if float(r.get("single_sec") or 0) > float(active_shot_cap())
            or int(r.get("han") or 0) > vr.MAX_LINE_HAN]
    if over:
        parts.append("单条超限 %s" % "、".join(over))
    return "；".join(parts) or "声音超限"


def _claim_pending(first, final, ids) -> list:
    """被事实引用、且正文换成了**非完整绑定证据**的声音编号：出处相关，主张是否完整待核."""
    from step01_script_gen import lightweight_voice_repair as vr
    if not isinstance(first, dict) or not isinstance(final, dict):
        return []
    before = {(r["event"], r["beat"]): r for r in vr.voices(first, ids)}
    out = []
    for r in vr.voices(final, ids):
        b = before.get((r["event"], r["beat"]))
        if b and b["claims"] and r["text"] != b["text"] and r["text"] not in b["evidence"]:
            out.append(r["id"])
    return out


def generate_lightweight_outline_result(novel_text: str, model=None, cache_path=None, *,
                                        deadline_monotonic=None, run_deadline_at=None,
                                        service=None) -> dict:
    """轻量大纲：至多 `MAX_ATTEMPTS` 次请求；返回与 `generate_outline_result` 同形的结果.

    失败（调用失败 / 局部修订未通过 / 过截止）不发分镜请求：`executable=False`，
    由 `_stop_if_outline_unavailable` 按既有诊断停在计划层。
    """
    from _text_gen import TextGenerationRequest, create_service
    from step01_script_gen.outline import (_OUTLINE_SYSTEM_PROMPT, _fail,
                                           _outline_max_tokens, _outline_result_skeleton,
                                           _parse_outline_json, plan_acceptance_problems,
                                           round_archive_writer)
    from step01_script_gen import lightweight_voice_repair as vr
    from step01_script_gen.plan_revision import problem_records
    _dls = [float(x) for x in (deadline_monotonic, run_deadline_at) if x is not None]
    _dl = min(_dls) if _dls else None
    res = _outline_result_skeleton()
    res["plan_mode"] = PLAN_MODE_LIGHTWEIGHT
    if not novel_text:
        return _fail(res, "outline_invalid", "原文为空（无法规划）")
    _pf = preflight_problems()
    if _pf:
        return _fail(res, "outline_invalid", _pf)
    prompt = _prompt(novel_text)
    _cpath = cache_path_for(cache_path)
    _key = _cache_key(prompt, model)
    res["cache"]["key"] = _key
    if _cpath is not None:
        _hit = _read_cache(_cpath, _key, novel_text)
        if _hit is not None:
            res.update(executable=True, source="cache", plan=_hit, failure=None)
            res["cache"]["hit"] = True
            res["acceptance"] = {"problems": [], "codes": [], "residual_class": "none",
                                 "verdict": "ok"}
            return res
    from step01_script_gen.prop_state_anchor import outline_prop_anchors
    _anchors = outline_prop_anchors(novel_text)
    _svc = service or create_service()
    _archive = round_archive_writer()
    _attempts: list = []
    _last_plan = None
    original = None        # 当前基线：首答，或最近一次被采纳的部分修订
    scope = None
    _base_codes: set = set()
    res["revision"]["scope"] = "none"   # replace / trim：基于当前基线的 edits 定点修正
    res["revision"]["voice_progress"] = []
    _last_problems: list = []
    _first_answer = None
    _first_ids = None

    def _blocked_stop(ctx):
        """修订前就能判定无合法动作：不发请求，给出逐条原因."""
        return [_rec("lightweight_voice_unfixable", str(b["id"]), b["reason"])
                for b in ctx.get("blocked") or []]

    for _n in range(1, MAX_ATTEMPTS + 1):
        if scope and scope.get("blocked"):
            _last_problems = _last_problems + _blocked_stop(scope)
            break
        if _dl is not None and time.monotonic() >= _dl:
            return _fail(res, "deadline_exhausted",
                         "规划截止时刻已到 ⇒ 不发轻量大纲请求（已尝试 %d 次）" % (_n - 1),
                         attempts=_attempts)
        _req = TextGenerationRequest(
            system_prompt=_OUTLINE_SYSTEM_PROMPT,
            user_prompt=(vr.prompt(scope) + _budget_block(scope) + "\n"
                         + json.dumps(_last_problems, ensure_ascii=False)
                         if scope else prompt),
            max_tokens=_outline_max_tokens(), temperature=0.3,
            json_schema=vr.SCHEMA if scope else LIGHTWEIGHT_SCHEMA, model=model, deadline_monotonic=_dl)
        _r = _svc.generate(_req)
        res["revision"]["calls"] = _n
        _cid = str(getattr(_r, "call_id", "") or "")
        if not getattr(_r, "success", False):
            _attempts.append({"code": "call_failed", "detail": str(getattr(_r, "error", ""))})
            _write_round(_archive, _n, "call_failed", _cid, [], None)
            return _fail(res, "call_failed", str(getattr(_r, "error", "")), attempts=_attempts)
        _data = _parse_outline_json(getattr(_r, "text", ""))
        _plan = None
        _last_problems = []
        _declared = _voice_done = False
        if scope:
            try:
                _data = vr.apply(original, scope, _data)
            except vr.DeclaredUnfixable as exc:
                _declared = True
                _last_problems = [_rec("lightweight_voice_declared_unfixable", str(x["id"]),
                                       str(x["reason"])[:240]) for x in exc.items]
            except ValueError as exc:
                _last_problems = [_rec("lightweight_voice_edit_rejected", "edits", str(exc))]
        remaining = None
        if not _last_problems:
            if not isinstance(_data, dict):
                _last_problems = [_rec("response_not_json", "", "回答不是可解析的 JSON 对象")]
            else:
                _plan, _last_problems = build_plan(_data, novel_text)
                if _plan is not None:
                    _plan["prop_state_candidates"] = _anchors
                    _last_problems = list(plan_acceptance_problems(_plan, novel_text) or [])
                if not answer_problems(_data):
                    remaining = vr.prepare(_data, novel_text, scope["ids"] if scope else None)
                    if remaining:
                        _last_problems.append(_rec("lightweight_voice_budget", "events",
                                                   _over_limits(remaining)))
        _codes = {str(x.get("code") or "") for x in _last_problems} - vr.VOICE_CODES
        if scope is None:
            if isinstance(_data, dict):
                _first_answer = _data
            if remaining:
                original, scope, _base_codes = _data, remaining, _codes
                _first_ids = scope["ids"]
                res["revision"]["scope"] = scope["scope"]
        elif _plan is not None and not _codes <= _base_codes:
            # 合法事务但引入了声音修订解决不了的新问题：不推进基线
            _last_problems = [_rec("lightweight_voice_edit_regressed", "edits",
                                   "修订后出现新问题：%s" % "、".join(sorted(_codes - _base_codes)))]
            _plan = None
        elif _plan is not None:
            # 合法的部分进展成为新基线；编号沿用首答，下一轮只开放残余超限项
            res["revision"]["voice_progress"].append(
                {"round": _n, "from_sec": scope["seconds"],
                 "to_sec": remaining["seconds"] if remaining else vr.total(vr.voices(_data))})
            original, _base_codes = _data, _codes
            if remaining:
                scope = remaining
                res["revision"]["scope"] = scope["scope"]
            else:
                _voice_done = True     # 声音已无残余；剩下的问题不是声音修订能解决的
        if _plan is not None and (scope is None or _data is original):
            _last_plan = _plan
        _kind = "ok" if (_plan is not None and not _last_problems) else "illegal"
        _write_round(_archive, _n, _kind, _cid, _last_problems, _plan)
        _attempts.append({"code": _kind, "call_id": _cid,
                          "problems": [str(x.get("code") or "") for x in _last_problems]})
        if _kind == "ok" or scope is None or _declared or _voice_done:
            break
    else:
        if scope and scope.get("blocked") and _last_problems:
            _last_problems = _last_problems + _blocked_stop(scope)
    res["plan"] = _last_plan
    _pending = (_claim_pending(_first_answer, original, _first_ids)
                if original is not None and original is not _first_answer else [])
    res["revision"]["claim_pending"] = _pending
    res["acceptance"] = {"problems": problem_records(_last_problems),
                         "codes": sorted({str(x.get("code") or "") for x in _last_problems}),
                         "residual_class": "none" if not _last_problems else "content",
                         "residual": residual_report(_last_problems, _pending),
                         "verdict": "ok" if not _last_problems else "blocked"}
    if _last_problems or _last_plan is None:
        return _fail(res, "outline_invalid",
                     "轻量大纲 %d 次回答都未通过确定性校验：%s"
                     % (len(_attempts), "；".join(str(x.get("reason") or x.get("code"))
                                                   for x in _last_problems[:3])),
                     problems=_last_problems, attempts=_attempts)
    if _dl is not None and time.monotonic() >= _dl:
        return _fail(res, "deadline_exhausted", "轻量大纲已通过校验，但规划截止时刻已到",
                     attempts=_attempts)
    res.update(executable=True, failure=None)
    res["revision"]["stop_reason"] = "ok"
    if _cpath is not None:
        _err = _write_cache(_cpath, _key, _last_plan)
        res["cache"]["written"] = not _err
        res["cache"]["write_error"] = _err or None
    return res


def _write_round(writer, round_no, kind, call_id, problems, plan_out) -> None:
    if writer is None:
        return
    try:
        writer({"phase": "lightweight_outline", "round": round_no, "kind": kind,
                "call_id": str(call_id or ""), "plan_in": None, "plan_out": plan_out,
                "problems_out": list(problems or [])})
    except Exception as _exc:                                         # noqa: BLE001
        print("[WARN] 轻量大纲留档失败（证据缺失，裁决不受影响）：%s" % _exc)


__all__ = ["PLAN_MODE_ENV", "PLAN_MODES", "PLAN_MODE_LEGACY", "PLAN_MODE_LIGHTWEIGHT",
           "CONTRACT_VERSION", "MAX_ATTEMPTS", "LIGHTWEIGHT_SCHEMA", "plan_mode_setting",
           "preflight_problems", "identity_problems", "answer_problems", "build_view",
           "build_plan", "dialogue_speakers", "cache_path_for",
           "generate_lightweight_outline_result"]
