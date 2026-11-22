# -*- coding: utf-8 -*-
"""轻量规划（`STEP01_PLAN_MODE=lightweight`，S14 M06/M07，2026-11-22）.

**一次**轻量大纲请求（内容不合法时最多重抽一次）：模型只给有序事件/节拍、必保留事实、
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
#: 首次内容不合法时最多重抽一次 ⇒ 至多 2 次大纲请求
MAX_ATTEMPTS = 2
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
正片 {band_min:g}–{band_max:g} 秒，画面也要占时间。**所有声音（台词 + 旁白）合计不超过约
{voice_sec:g} 秒，约 {voice_chars} 个汉字**（按每秒约 4 字估）；超过就整份拒收。
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
    for _fi, _f in enumerate(answer["facts"], 1):
        _fu = "f%02d" % _fi
        _frozen.append({"uid": _fu, "claim": _s(_f.get("claim")),
                        "evidence": _s(_f.get("evidence"))})
        _bu = _pos[(int(_f["event"]), int(_f["beat"]))]
        _car = (_beat_voice[_bu] if _s(_f.get("carrier")) == "voice"
                else {"kind": pc.KIND_VISUAL, "uid": _bu})
        _binds.append({"fact_uid": _fu, "carriers": [_car]})
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
    return {"schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_LEGACY,
            "event_binding": "declared", "frozen_facts": _frozen, "frozen_events": _events,
            "beats": _beats, "dialogue_units": _dlg, "narration_units": _nar,
            "screen_cards": [], "fact_bindings": _binds,
            "hook_bindings": {"spoken_uids": [], "carriers": []},
            "evidence_updates": [], "plan_basis": _basis}


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
    _bmin, _bmax = budget_band()
    _voice_sec = voice_budget_sec()
    return LIGHTWEIGHT_PROMPT.format(cap=float(active_shot_cap()),
                                     band_min=float(_bmin),
                                     band_max=float(_bmax),
                                     voice_sec=_voice_sec, voice_chars=int(_voice_sec * 4),
                                     novel_text=str(novel_text or "")[:PROMPT_MAX_CHARS])


def voice_budget_sec() -> float:
    """给规划者的声音总量**引导值**：上限的 70%（画面也要占时间）。硬判据仍是 `evaluate_plan`."""
    from step01_script_gen.plan_feasibility import budget_band
    return round(float(budget_band()[1]) * 0.7, 0)


_REVISION_FULL = "full"
_REVISION_REPLACE = "replace"
_REVISION_TRIM = "trim"


def revision_scope(problems) -> str:
    """上一版问题 → 本次修订的**范围**（由问题类别决定，不从文案猜）.

    - `replace`：只差「某句不能播」——其余声音/节拍/事实原样保留，只替换问题句；
    - `trim`：总量超上限——由模型选择删/换哪些可省声音，代码复算；
    - `full`：结构/引用/逐字等其它问题——退回完整回答（编号与关系仍由代码派生）。
    """
    _codes = {str(x.get("code") or "") for x in (problems or [])}
    if not _codes:
        return _REVISION_FULL
    if _codes <= {"budget_unrepresentable"}:
        return _REVISION_REPLACE
    if _codes <= {"budget_infeasible", "budget_unrepresentable"}:
        return _REVISION_TRIM
    return _REVISION_FULL


def _voice_ledger(answer) -> list:
    """回答里每条声音的**位置**（事件/节拍，1 起）与承载它的事实主张.

    顺序与 `build_view` 生成 D/N 编号的顺序一致（先事件后节拍，各自累计），
    所以问题记录里的 `kind` + `index` 能精确映射回节拍——反馈与范围守卫共用这一份。
    """
    if not isinstance(answer, dict):
        return []
    _facts_at: dict = {}
    for _f in (answer.get("facts") or []):
        if not isinstance(_f, dict) or str(_f.get("carrier") or "") != "voice":
            continue
        _e, _b = _f.get("event"), _f.get("beat")
        if isinstance(_e, int) and not isinstance(_e, bool) and isinstance(_b, int):
            _facts_at.setdefault((_e, _b), []).append(_s(_f.get("claim")))
    _rows, _d, _n = [], 0, 0
    for _ei, _ev in enumerate((answer.get("events") or []), 1):
        if not isinstance(_ev, dict):
            continue
        for _bi, _b in enumerate((_ev.get("beats") or []), 1):
            if not isinstance(_b, dict):
                continue
            _kind = _s(_b.get("voice_kind")) or "none"
            if _kind not in ("dialogue", "narration"):
                continue
            if _kind == "dialogue":
                _d += 1
                _index, _id = _d, "D%d" % _d
            else:
                _n += 1
                _index, _id = _n, "N%d" % _n
            _rows.append({"kind": _kind, "index": _index, "id": _id,
                          "text": _s(_b.get("voice_text")),
                          "speaker": _s(_b.get("speaker")),
                          "event": _ei, "beat": _bi,
                          "facts": list(_facts_at.get((_ei, _bi)) or [])})
    return _rows


def _flagged_beats(answer, problems) -> set:
    """`budget_unrepresentable` 问题 → 被点名的节拍 `{(event, beat)}`."""
    _by = {(r["kind"], r["index"]): r for r in _voice_ledger(answer)}
    _out = set()
    for _p in problems or []:
        if str(_p.get("code") or "") != "budget_unrepresentable":
            continue
        _r = _by.get((str(_p.get("kind") or ""), _p.get("index")))
        if _r:
            _out.add((_r["event"], _r["beat"]))
    return _out


def _legal_alternatives(text, kind, novel_text, *, limit=3) -> list:
    """原文里更短的**逐字**句，且按同一条单镜判据已验算可表示（含对白前缀余量）.

    对白只从**引语内**取（同一条引语的句末前缀 + 相邻引语的完整短句），不用跨叙述句
    的片段——那会把「”老船夫又低声道：“…」这种带引用符号的半截摆给模型。
    """
    from step01_script_gen.plan_feasibility import (cross_quote_replacements,
                                                   representable_replacements,
                                                   voice_entry_representability)
    from step01_script_gen.outline import _sentence_alternatives
    from step01_script_gen.timecode import active_shot_cap
    if not text or not novel_text:
        return []
    _cap = float(active_shot_cap())
    _cands: list = []
    if kind == "dialogue":
        _cands += list(representable_replacements(text, novel_text, limit=6))
        _cands += [str(x.get("text") or "")
                   for x in cross_quote_replacements(text, novel_text, limit=4)]
    # 同句内的句末片段（引语里非前缀的短句）与全章整句都可能是合法替代：
    # 只收**不含引用符号/换行**的整句——那种「”老船夫又低声道：“…」的半截不能当台词。
    _cands += [str(x.get("text") or "")
               for x in _sentence_alternatives(text, novel_text, cap_sec=_cap, limit=8)]
    _seen, _out = {text}, []
    for _t in _cands:
        _t = _t.strip()
        if (not _t or _t in _seen or len(_t) < 4
                or any(ch in _t for ch in "\n\r“”「」")):
            continue
        _seen.add(_t)
        _chk = voice_entry_representability(kind, _t)
        if not _chk["representable"]:
            continue
        _out.append({"text": _t, "sec": round(float(_chk["est_sec"]), 1)})
        if len(_out) >= limit:
            break
    return _out


def _voice_row_line(row) -> str:
    """一条声音的读数：**单镜判据估时**（与拒收记录同一入口）+ 播出正文估时."""
    from step01_script_gen.plan_feasibility import voice_entry_representability
    from step01_script_gen.timecode import _estimate_duration
    _chk = voice_entry_representability(row["kind"], row["text"])
    _sec = float(_chk["est_sec"])
    _body = float(_estimate_duration(row["text"]))
    _tag = "台词" if row["kind"] == "dialogue" else "旁白"
    _show = row["text"][:60] + ("…" if len(row["text"]) > 60 else "")
    _line = "- %s %.1fs %s「%s」" % (row["id"], _sec, _tag, _show)
    if row["kind"] == "dialogue" and abs(_sec - _body) >= 0.05:
        _line += "（播出正文 %.1fs + 说话人前缀余量）" % _body
    return _line


def _revision_detail_lines(scope, rows, flagged, problems, novel_text) -> list:
    """按修订范围给**定向**细节：点名句的合法替代 / 必保留与可省的声音."""
    from step01_script_gen.plan_feasibility import voice_entry_representability
    _flagged = [r for r in rows if (r["event"], r["beat"]) in flagged]
    _out: list = []
    if scope == _REVISION_REPLACE:
        _out.append("## 必须替换的声音（其余一律逐字保留）")
        for _r in _flagged:
            _chk = voice_entry_representability(_r["kind"], _r["text"])
            _out.append(_voice_row_line(_r)
                        + ("：%s" % _chk["reason"] if _chk.get("reason") else ""))
            _alts = _legal_alternatives(_r["text"], _r["kind"], novel_text)
            if _alts:
                _out.append("  原文里可用的较短句（逐字、已按同一判据验算）：%s"
                            % "；".join("「%s」%.1fs" % (a["text"], a["sec"]) for a in _alts))
            else:
                _out.append("  本地没有找到与原句重合足够多、可直接替换的短句 ⇒ 可以自行从原文另选"
                            "一句**完整、逐字**的短句承载同一条事实（不得截断原句、不得自造台词）；"
                            "确实找不到就如实报告「无法在单镜上限内保留这条事实」，不得删掉事实，"
                            "也不得只把载体改成画面来算作已表达。")
        return _out
    # 总量超限：区分必保留/可省，并给超单镜句的替代
    _must = [r for r in rows if r["facts"]]
    _free = [r for r in rows if not r["facts"]]
    _out.append("## 必保留的声音（被事实引用：不能删，只能换成更短的逐字句）")
    if _must:
        for _r in _must:
            _out.append(_voice_row_line(_r) + "（事实：%s）"
                        % "；".join(x[:40] for x in _r["facts"][:2]))
    else:
        _out.append("- （上一版没有声音被事实引用）")
    _out.append("## 可省的声音（没有被任何事实引用：删掉或换短最省事）")
    if _free:
        for _r in _free:
            _out.append(_voice_row_line(_r))
    else:
        _out.append("- （没有可省的声音——所有声音都被事实引用；减不到上限就如实报告"
                    "「本章无法在预算内保留全部必保留事实」及还超多少秒，不要新增或改写事实）")
    _over = [r for r in rows if not voice_entry_representability(r["kind"], r["text"])["representable"]]
    if _over:
        _out.append("## 超出单镜的句子")
        for _r in _over:
            _chk = voice_entry_representability(_r["kind"], _r["text"])
            _out.append(_voice_row_line(_r) + "：%s" % _chk["reason"])
            _alts = _legal_alternatives(_r["text"], _r["kind"], novel_text)
            if _alts:
                _out.append("  原文里可用的较短句（逐字、已验算）：%s"
                            % "；".join("「%s」%.1fs" % (a["text"], a["sec"]) for a in _alts))
    return _out


def _voice_cost_lines(plan, novel_text) -> list:
    """完整重答时的逐条读数（与验收同一判据入口）；不再建议「删掉改用画面」."""
    from step01_script_gen.plan_feasibility import voice_entry_costs
    if not isinstance(plan, dict):
        return []
    _rows = voice_entry_costs(plan, novel_text=novel_text)
    if not _rows:
        return []
    _total = round(sum(float(r["body_sec"]) for r in _rows), 1)
    from step01_script_gen.timecode import active_shot_cap
    _out = ["", "上一版的声音估时（共 %d 句，合计 %.1f 秒；本集声音引导上限约 %g 秒，"
                "硬上限 %g 秒；单句不超过 %g 秒）——删掉或换短的，从最长的开始："
            % (len(_rows), _total, voice_budget_sec(), _total_cap(),
               float(active_shot_cap()))]
    for _r in sorted(_rows, key=lambda r: -float(r["body_sec"]))[:12]:
        _out.append(_voice_row_line(_r))
    return _out


def _estimate_body(text) -> float:
    from step01_script_gen.timecode import _estimate_duration
    return round(float(_estimate_duration(str(text or ""))), 1)


def _revision_block(answer, problems, plan, novel_text) -> str:
    """第二次请求的**受限修订**反馈：范围、点名项、必保留项与可复算的读数."""
    _scope = revision_scope(problems)
    _rows = _voice_ledger(answer)
    _flagged = _flagged_beats(answer, problems)
    _lines = ["", "# 上一版回答被本地校验拒收（没有被采纳）"]
    if _scope == _REVISION_REPLACE:
        _lines.append("这次只做**定点替换**：上一版只差 %d 句不能播，其余内容已经合格。"
                      "必须保留：事件与节拍的条数/顺序、每条节拍的画面文字、未点名的声音"
                      "（正文与说话人）、全部事实（claim / 承载方式 / 指向的节拍）。"
                      "代码会逐项比对上一版，范围之外的改动整份拒收。" % len(_flagged))
    elif _scope == _REVISION_TRIM:
        _total = round(sum(float(_estimate_body(r["text"])) for r in _rows), 1)
        _need = round(max(0.0, _total - _total_cap()), 1)
        _lines.append("这次只做**减量修订**：上一版声音合计 %.1fs（按播出正文；硬上限 %.0fs），"
                      "需再减 **%.1fs** 才可能通过。代码改完会重新逐句估时并复算合计，"
                      "仍超或仍有单句不能播就整份拒收。" % (_total, _total_cap(), _need))
        _lines.append("全部事实（claim / 承载方式 / 指向的节拍）必须保留；不得靠「删掉声音、"
                      "把载体改成画面」来算作已表达。")
    else:
        _lines.append("请按下列原因改正后**重新给出完整回答**：")
    for _x in list(problems or [])[:12]:
        _lines.append("- %s｜%s" % (str(_x.get("path") or "（整份）"),
                                    str(_x.get("reason") or _x.get("code") or "")[:240]))
    if len(problems or []) > 12:
        _lines.append("- （另有 %d 条）" % (len(problems) - 12))
    if _scope == _REVISION_FULL:
        _lines += _voice_cost_lines(plan, novel_text)
    else:
        _lines += _revision_detail_lines(_scope, _rows, _flagged, problems, novel_text)
    return chr(10).join(_lines)


def _revision_scope_problems(prev_answer, new_answer, scope, flagged) -> list:
    """**范围守卫**：定向修订里范围外的改动整份拒收（不猜意图、不静默采纳）.

    冻结面：事件/节拍条数、节拍画面文字、事实（claim/承载方式/指向节拍）、以及
    `replace` 范围内未点名的声音。`trim` 允许删掉**未被事实引用**的声音、允许把
    被引用的声音换成更短的同类型逐字句；不允许新增声音或把必保留声音删成画面。
    """
    if scope not in (_REVISION_REPLACE, _REVISION_TRIM):
        return []
    if not isinstance(prev_answer, dict) or not isinstance(new_answer, dict):
        return []
    _out: list = []

    def _bad(path, reason):
        _out.append(_rec("revision_scope_violation", path, reason))

    _pe = prev_answer.get("events") if isinstance(prev_answer.get("events"), list) else []
    _ne = new_answer.get("events") if isinstance(new_answer.get("events"), list) else []
    if len(_pe) != len(_ne):
        _bad("events", "事件条数被改动（上一版 %d、本次 %d）——定向修订不得增删事件"
             % (len(_pe), len(_ne)))
        return _out
    _fact_beats = {(r["event"], r["beat"]) for r in _voice_ledger(prev_answer) if r["facts"]}
    for _ei, (_a, _b) in enumerate(zip(_pe, _ne), 1):
        if not isinstance(_a, dict) or not isinstance(_b, dict):
            _bad("events[%d]" % (_ei - 1), "事件不是对象")
            continue
        if _s(_a.get("summary")) != _s(_b.get("summary")):
            _bad("events[%d].summary" % (_ei - 1), "事件摘要被改写——定向修订不得重排事件")
        _pb = _a.get("beats") if isinstance(_a.get("beats"), list) else []
        _nb = _b.get("beats") if isinstance(_b.get("beats"), list) else []
        if len(_pb) != len(_nb):
            _bad("events[%d].beats" % (_ei - 1),
                 "节拍条数被改动（上一版 %d、本次 %d）——定向修订不得增删节拍"
                 % (len(_pb), len(_nb)))
            continue
        for _bi, (_x, _y) in enumerate(zip(_pb, _nb), 1):
            _path = "events[%d].beats[%d]" % (_ei - 1, _bi - 1)
            if not isinstance(_x, dict) or not isinstance(_y, dict):
                _bad(_path, "节拍不是对象")
                continue
            if _s(_x.get("text")) != _s(_y.get("text")):
                _bad(_path + ".text", "节拍画面文字被改写——定向修订不得重写画面")
            _pk = _s(_x.get("voice_kind")) or "none"
            _nk = _s(_y.get("voice_kind")) or "none"
            _pt, _nt = _s(_x.get("voice_text")), _s(_y.get("voice_text"))
            _ps, _ns = _s(_x.get("speaker")), _s(_y.get("speaker"))
            if (_pk, _pt, _ps) == (_nk, _nt, _ns):
                continue
            _pos = (_ei, _bi)
            if scope == _REVISION_REPLACE:
                if _pos not in flagged:
                    _bad(_path + ".voice_text",
                         "未点名的声音被改写（%s）——只许替换被点名的句子" % (_pk or "画面"))
                elif _nk != _pk or _nk == "none":
                    _bad(_path + ".voice_kind",
                         "被点名的声音只能换成同一类型（%s）的逐字短句" % _pk)
            else:
                if _pk == "none" and _nk != "none":
                    _bad(_path + ".voice_kind", "上一版没有声音的节拍新增了声音——减量修订不得新增内容")
                elif _pos in _fact_beats and (_nk == "none" or not _nt):
                    _bad(_path + ".voice_text", "被事实引用的必保留声音被删——只能换短，不能删")
                elif _pos in _fact_beats and _nk != _pk:
                    _bad(_path + ".voice_kind", "必保留声音的类型被改（%s→%s）" % (_pk, _nk))
    _pf = prev_answer.get("facts") if isinstance(prev_answer.get("facts"), list) else []
    _nf = new_answer.get("facts") if isinstance(new_answer.get("facts"), list) else []
    if len(_pf) != len(_nf):
        _bad("facts", "事实条数被改动（上一版 %d、本次 %d）——必保留事实不得增删"
             % (len(_pf), len(_nf)))
    else:
        for _fi, (_a, _b) in enumerate(zip(_pf, _nf), 1):
            if not isinstance(_a, dict) or not isinstance(_b, dict):
                continue
            if _s(_a.get("claim")) != _s(_b.get("claim")):
                _bad("facts[%d].claim" % (_fi - 1), "事实主张被改写——必保留事实不得改写")
            if _s(_a.get("carrier")) != _s(_b.get("carrier")):
                _bad("facts[%d].carrier" % (_fi - 1),
                     "事实的承载方式被改（%s→%s）——不得把「改用画面」当成已表达"
                     % (_s(_a.get("carrier")), _s(_b.get("carrier"))))
            if (_a.get("event"), _a.get("beat")) != (_b.get("event"), _b.get("beat")):
                _bad("facts[%d].beat" % (_fi - 1), "事实指向的节拍被改动——必保留事实的落点不得搬动")
    return _out


def _total_cap() -> float:
    """硬上限与验收链**同源**（`budget_band`），不再各读一份配置常量."""
    from step01_script_gen.plan_feasibility import budget_band
    return float(budget_band()[1])


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


def generate_lightweight_outline_result(novel_text: str, model=None, cache_path=None, *,
                                        deadline_monotonic=None, run_deadline_at=None,
                                        service=None) -> dict:
    """轻量大纲：至多 `MAX_ATTEMPTS` 次请求；返回与 `generate_outline_result` 同形的结果.

    失败（调用失败 / 两次都不合法 / 过截止）不发分镜请求：`executable=False`，
    由 `_stop_if_outline_unavailable` 按既有诊断停在计划层。
    """
    from _text_gen import TextGenerationRequest, create_service
    from step01_script_gen.outline import (_OUTLINE_SYSTEM_PROMPT, _fail,
                                           _outline_max_tokens, _outline_result_skeleton,
                                           _parse_outline_json, plan_acceptance_problems,
                                           round_archive_writer)
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
    _prev_plan = None
    _prev_answer = None
    _scope = _REVISION_FULL
    _flagged: set = set()
    _last_problems: list = []
    for _n in range(1, MAX_ATTEMPTS + 1):
        if _dl is not None and time.monotonic() >= _dl:
            return _fail(res, "deadline_exhausted",
                         "规划截止时刻已到 ⇒ 不发轻量大纲请求（已尝试 %d 次）" % (_n - 1),
                         attempts=_attempts)
        _req = TextGenerationRequest(
            system_prompt=_OUTLINE_SYSTEM_PROMPT,
            user_prompt=prompt + (_revision_block(_prev_answer, _last_problems,
                                                  _prev_plan, novel_text)
                                  if _last_problems else ""),
            max_tokens=_outline_max_tokens(), temperature=0.3,
            json_schema=LIGHTWEIGHT_SCHEMA, model=model, deadline_monotonic=_dl)
        _r = _svc.generate(_req)
        res["revision"]["calls"] = _n
        _cid = str(getattr(_r, "call_id", "") or "")
        if not getattr(_r, "success", False):
            _attempts.append({"code": "call_failed", "detail": str(getattr(_r, "error", ""))})
            _write_round(_archive, _n, "call_failed", _cid, [], None)
            return _fail(res, "call_failed", str(getattr(_r, "error", "")), attempts=_attempts)
        _data = _parse_outline_json(getattr(_r, "text", ""))
        if not isinstance(_data, dict):
            _last_problems = [_rec("response_not_json", "", "回答不是可解析的 JSON 对象")]
            _plan = None
        else:
            _plan, _last_problems = build_plan(_data, novel_text)
            if _plan is not None:
                _plan["prop_state_candidates"] = _anchors
                # 定向修订的**范围守卫**：只许改被点名/可省的条目，其余逐项比对上一版。
                # 与验收链记录合并，让模型与报告都看得到「为什么这次不采纳」。
                _scope_out = (_revision_scope_problems(_prev_answer, _data, _scope, _flagged)
                              if _n > 1 else [])
                _last_problems = _scope_out + list(
                    plan_acceptance_problems(_plan, novel_text) or [])
        if _n == 1 and isinstance(_data, dict):
            _prev_answer = _data
            _scope = revision_scope(_last_problems)
            _flagged = _flagged_beats(_data, _last_problems)
        _last_plan = _plan if _plan is not None else _last_plan
        _prev_plan = _plan
        _kind = "ok" if (_plan is not None and not _last_problems) else "illegal"
        _write_round(_archive, _n, _kind, _cid, _last_problems, _plan)
        _attempts.append({"code": _kind, "call_id": _cid,
                          "problems": [str(x.get("code") or "") for x in _last_problems]})
        if _kind == "ok":
            break
    res["plan"] = _last_plan
    res["acceptance"] = {"problems": problem_records(_last_problems),
                         "codes": sorted({str(x.get("code") or "") for x in _last_problems}),
                         "residual_class": "none" if not _last_problems else "content",
                         "verdict": "ok" if not _last_problems else "blocked"}
    res["revision"]["scope"] = _scope
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
