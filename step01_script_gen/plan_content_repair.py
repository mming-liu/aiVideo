# -*- coding: utf-8 -*-
"""**内容槽位适配器**（provider-neutral）：模型只作内容与关系选择，代码装配增量.

来源：规划收口计划 §5.6（2026-10-08）。真实修订暴露的形态是——让模型回**增量**
（`upsert` / `delete` / `order` / 绑定，且每条实体要重抄 uid、`order`、两个基线哈希）时，
它反复写下**数字占位**（实测：`"beat_events": [1, 1, 1, 2, ...]`、`upsert.beats[0]` 是
`int`），三轮额度耗尽仍无可用计划。而"只让它选内容与关系、由代码装配增量"的隔离原型
**一次就取得了真实生成 + 真实核验 + 零残余**。

本模块把那件事做成**生产路径**：

1. `content_slots(...)`：按问题的**稳定 UID**（fact / card）生成槽位，不从作品措辞猜目标；
   每条槽位带**能力、受影响事实、允许动作、允许目标、允许来源、冻结摘要**。
2. `slot_prompt(...)`：**槽位标识与允许集合由代码给**；模型只回答本槽的动作、正文、
   目标、插入位置与事件归属——不重抄实体表、不写 `order`、不写哈希。
3. `answer_problems(...)`：显式版本（`plan_content_repair.answer.v1`）+ **严格准入**：
   未知键、非字符串、槽位缺答/多答/重答、动作与目标越界一律拒收，**不按形状猜路线**。
4. `compile_delta(...)`：用基线身份、完整实体契约与**明确修改指令**装配**既有 delta**
   （`plan_revision.delta.v1`），未修改字段一律保留；临时 UID / 顺序 / 映射由本模块与
   既有 codec 负责。多槽改同一实体先合并，冲突**拒收并回灌**，不静默覆盖。

编译出来的 delta 交**既有应用器与同一条验收链**（`apply_delta_view` → `complete_candidate`
→ 统一验收）——本模块**不绕过验收**、不自己判内容对错。
"""
from __future__ import annotations

import copy
import json
import re

#: 请求 / 响应的**显式版本**（调用方按它选路，不按响应形状猜）
SLOT_REQUEST_SCHEMA = "plan_content_repair.request.v1"
SLOT_ANSWER_SCHEMA = "plan_content_repair.answer.v1"
REPAIR_RULE_VERSION = "plan_content_repair.v2.binding_policy"

#: 首批覆盖的四类动作（§5.6 第 1 条）
ACT_REWRITE = "rewrite_visual"
ACT_CREATE = "create_visual"
ACT_BIND = "bind_visual"
ACT_NARROW_CARD = "narrow_card"
#: P1a：声音迁移（对白↔旁白，方向由**已准入核验记录**的 `source_kind` 派生）与
#: 事实绑定到现有声音（目标只来自独立的 `voice_target_uids`）。
ACT_RECLASSIFY = "reclassify_voice"
ACT_BIND_VOICE = "bind_voice"
ACTIONS = (ACT_CREATE, ACT_BIND, ACT_REWRITE, ACT_NARROW_CARD,
           ACT_RECLASSIFY, ACT_BIND_VOICE)

#: 问题 code → 槽位种类（**只覆盖"内容/关系选择"能表达的那几类**）：
#: 别的 code（类型违约、事件重排、声音选材、来源不支持主张……）本适配器**不接**，
#: 调用方据此整轮改走既有 delta（§5.6 第 4 条：能表达才接入）。
SLOT_CODE_KIND = {
    # 载体没有完整表达这条事实 ⇒ 重写现有画面 / 补建画面并绑定 / 补绑现有画面
    "verify_fact_unsupported": "fact_visual",
    "verify_fact_unknown": "fact_visual",
    "verify_support_span_missing": "fact_visual",
    "verify_support_span_not_exhibited": "fact_visual",
    "presentation_visual_body_missing": "fact_visual",
    # 绑定指不到任何条目 ⇒ 补绑现有画面
    "presentation_carrier_dangling": "fact_binding",
    # 卡片正文空 / 不在原文 / 阅读预算超限 ⇒ 收窄原文卡片
    "presentation_card_text_empty": "card_text",
    "presentation_card_not_in_novel": "card_text",
    "presentation_reading_budget_exceeded": "card_text",
    # P1a：声音单位的来源类型与所在集合不符 ⇒ 迁到另一个声音集合（方向由记录派生）
    "verify_voice_not_speech": "voice_kind",
}

#: **动作字段契约**（单源）：`required` 必填、`forbidden` 该动作**不许出现**。
#: `answer_json_schema` 与 `answer_problems` 都从它生成/判定——一份契约两处消费。
ACTION_FIELDS = {
    ACT_CREATE: {"required": ("text", "insert_after_uid", "event_index"),
                 "forbidden": ("target_uid",)},
    ACT_BIND: {"required": ("target_uid",), "forbidden": ("insert_after_uid",
                                                          "event_index")},
    ACT_REWRITE: {"required": ("text", "target_uid"),
                  "forbidden": ("insert_after_uid", "event_index")},
    ACT_NARROW_CARD: {"required": ("text",),
                      "forbidden": ("target_uid", "insert_after_uid", "event_index")},
    ACT_RECLASSIFY: {"required": (),
                     "forbidden": ("text", "target_uid", "insert_after_uid",
                                   "event_index")},
    ACT_BIND_VOICE: {"required": ("target_uid",),
                     "forbidden": ("insert_after_uid", "event_index")},
}

#: **分派状态**（调用方按它选路，**不按 `why` 的文案猜**）。两种"不走槽位"必须分开
#: （复核 2026-10-08 P2 第 5 条）：
#:
#: - `STATE_UNKNOWN_TARGET`：本次诊断指向的修改实体**不存在/身份写错** ⇒ 上游记录
#:   契约失效 ⇒ **零 create_service、零 generate**、保留基线，责任送回记录生产者；
#: - `STATE_UNSUPPORTED`：实体是好的，只是**这个修法槽位接口表达不了** ⇒ 保留既有
#:   delta 备用路（原额度内一次调用，且验证其合法响应可应用）。
STATE_OK = "ok"
STATE_UNKNOWN_TARGET = "unknown_target"
STATE_UNSUPPORTED = "unsupported"
STATE_NO_SLOTS = "no_slots"
STATE_UNREADABLE = "unreadable"
#: 字段类型（`int` **不含 `bool`**——`event_index=True` 是违约，不是 1）
_FIELD_TYPE = {"slot_id": "str", "action": "str", "text": "str", "target_uid": "str",
               "insert_after_uid": "str", "event_index": "int"}


def _type_ok(value, spec) -> bool:
    if spec == "str":
        return isinstance(value, str)
    if spec == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    return True


#: 回答里**允许出现**的键（白名单：拼错的键静默忽略 = 选择被悄悄丢掉）
ANSWER_KEYS = ("slot_id", "action", "text", "target_uid", "insert_after_uid",
               "event_index")


def _brief(value, limit: int = 60) -> str:
    _s = repr(value)
    return _s if len(_s) <= limit else _s[:limit] + "…"


def _rec(code, slot_id, reason, value=None) -> dict:
    """本适配器的**问题记录**（`ref`/`uid` 同时写——`record_uid` 两个都认）."""
    _r = {"code": code, "kind": "repair_slot", "ref": str(slot_id or ""),
          "uid": str(slot_id or ""), "path": str(slot_id or ""),
          "reason": reason, "fix": ""}
    if value is not None:
        _r["value"] = value
    return _r


def record_uid(record) -> str:
    """问题记录里的**稳定身份**（`uid`，兼容 `ref`）——生产者在遍历时给出.

    ⚠️ 校验过两份生产者：`plan_feasibility._add(..., uid=)` 写 `uid`；
    `content_verification._rec(...)` 同时写 `ref` 与 `uid`。**不从 reason 文案里认编号**
    （"事实 f05 …"那种读法会在换作品/换编号时失效）。
    """
    _r = record if isinstance(record, dict) else {}
    return str(_r.get("uid") or _r.get("ref") or "")


#: 路径里的**实体索引**（`presentation.screen_cards[c07].text`）——出现它说明这条问题
#: 指向一个**具体实体**，生产者**有**身份可给。
_INDEXED_PATH_RE = re.compile(r"\[[^\]]+\]")


def path_names_an_entity(record) -> bool:
    """该问题记录的 `path` 是否指向**具体实体**（含 `[索引]`）.

    ⚠️ 与"缺 uid"合起来才构成**身份契约失效**（2026-10-11 真实生产链路实测）：
    计划级**聚合**问题（如"文字卡合计阅读需求超预算"，`path="presentation.screen_cards"`）
    **没有**单个实体可命名，缺 uid 是**设计**而非写错；而 `…screen_cards[c07].text` 这样
    已经指向具体实体却没给出 `uid`，才是生产者的契约失效。两者必须分开，否则一条聚合问题
    会把身份齐全的可表达问题一起判成零调用停止。
    """
    return bool(_INDEXED_PATH_RE.search(str((record or {}).get("path") or "")))


def _fact_of(view, uid) -> dict:
    for _f in (view or {}).get("frozen_facts") or []:
        if isinstance(_f, dict) and str(_f.get("uid") or "") == str(uid):
            return _f
    return {}


def _bindings_of(view, fact_uid) -> dict:
    for _b in (view or {}).get("fact_bindings") or []:
        if isinstance(_b, dict) and str(_b.get("fact_uid") or "") == str(fact_uid):
            return _b
    return {}


def _beat_uids(view) -> list:
    return [str(_b.get("uid") or "") for _b in (view or {}).get("beats") or []
            if isinstance(_b, dict)]


def _visual_carriers(view, fact_uid) -> list:
    """该事实当前由**哪些画面**承载（`KIND_VISUAL` 的载体 uid，按基线顺序）."""
    from shared.presentation_contract import KIND_VISUAL, ref_uid
    _out = []
    for _c in (_bindings_of(view, fact_uid).get("carriers") or []):
        if isinstance(_c, dict) and str(_c.get("kind") or "") == KIND_VISUAL:
            _u = ref_uid(_c)
            if _u:
                _out.append(_u)
    return _out


def _facts_of_card(view, card_id) -> list:
    """**共享卡片的事实**：从已准入的 `fact_bindings` 反查（不猜卡片用途）."""
    from shared.presentation_contract import KIND_CARD, ref_uid
    _out = []
    for _b in (view or {}).get("fact_bindings") or []:
        if not isinstance(_b, dict):
            continue
        for _c in (_b.get("carriers") or []):
            if isinstance(_c, dict) and str(_c.get("kind") or "") == KIND_CARD                     and ref_uid(_c) == str(card_id):
                _out.append(str(_b.get("fact_uid") or ""))
                break
    return _out


def _carriers_of(view, fact_uid) -> list:
    """该事实**当前的全部载体**（`{kind, uid}`，按基线顺序）——联合承载的完整上下文."""
    from shared.presentation_contract import ref_uid
    return [{"kind": str((_c or {}).get("kind") or ""), "uid": ref_uid(_c)}
            for _c in (_bindings_of(view, fact_uid).get("carriers") or [])
            if isinstance(_c, dict)]


def _fact_brief(view, fact_uid) -> dict:
    _f = _fact_of(view, fact_uid)
    return {"uid": str(fact_uid), "claim": str(_f.get("claim") or ""),
            "evidence": str(_f.get("evidence") or ""),
            "carriers": _carriers_of(view, fact_uid)}


def _card_of(view, card_id) -> dict:
    for _c in (view or {}).get("screen_cards") or []:
        if isinstance(_c, dict) and str(_c.get("card_id") or _c.get("uid") or "") \
                == str(card_id):
            return _c
    return {}


def content_slots(view, problems, *, allow_create=True, target_fact_uids=None,
                  verification_record=None) -> dict:
    """问题清单 → 内容槽位；**表达不了的 code 原样列出**（调用方据此整轮改走既有路径）.

    `returns {"slots": [...], "unexpressible": [...], "blocked": [...],
    "covered_codes": [...]}`：

    - **按稳定 UID 归并**：同一 fact / card 上的多条问题合成**一个**槽（多槽改同一实体
      会在编译期变成冲突，归并发生在生成槽位这一步）；
    - 每条槽位带 `allowed_actions` / `target_uids` / `insert_positions` / `event_uids`
      / `sources`——模型只在这几个集合里选；
    - **不猜目标**：没有 UID 的问题（`ref` 为空）视为不可表达。

    ⚠️ **两种"不走槽位"必须分开**（复核 2026-10-08 P2 第 5 条）：

    - `unexpressible`：**问题 code 本身**不在适配器覆盖范围内（类型违约、事件重排……）
      ——实体是好的，只是这个修法槽位接口表达不了 ⇒ 调用方**保留**既有 delta 备用路；
    - `blocked`：**本次诊断指向的修改实体不存在 / 身份写错**（上游记录契约失效）——
      必须返回 `invalid_target`、**零调用**，把责任送回记录生产者。
      旧写法把两者都塞进 `unexpressible`，于是"目标卡 cZZ 不存在"照样落回 delta 发一次
      POST（`§84` 的 P2 第 5 条实测）。
    """
    _facts_seen: list = []
    _cards_seen: list = []
    _voices_seen: list = []
    # 只用**已准入**（status=ok）的记录派生迁移方向；失败/未完成记录不授权。
    _rec_in = (verification_record
               if isinstance(verification_record, dict)
               and str(verification_record.get("status") or "") == "ok" else None)
    _slots: list = []
    _bad: list = []
    _blocked: list = []
    _covered: list = []
    _deferred: list = []
    _beats = _beat_uids(view)
    from shared.presentation_contract import selectable_visual_beat_uids
    _visual_targets=selectable_visual_beat_uids(view)
    _events = [str(_e) for _e in (view or {}).get("frozen_events") or []]
    for _p in (problems or []):
        _code = str((_p or {}).get("code") or "")
        _kind = SLOT_CODE_KIND.get(_code)
        if _kind is None:
            _bad.append(_code or "无code")
            continue
        _uid = record_uid(_p)
        if not _uid:
            # **"没有身份可给"与"身份写错"分开**（2026-10-11 真实生产链路实测）：
            # 计划级**聚合**问题（`path` 是裸集合、没有实体索引，如"文字卡合计阅读需求
            # 超预算"）**本来就没有**单个实体可命名 ⇒ 修法槽位在语义上表达不了，
            # 归 `unexpressible`（调用方保留备用路、走一次完整计划修订）。
            # 旧写法把这一类也判 `blocked` ⇒ `STATE_UNKNOWN_TARGET` ⇒ **整轮零调用停止**，
            # 实测把同轮 3 条**身份齐全、可以表达**的卡片问题一起卡死。
            # `path` 已经指向具体实体（含 `[索引]`）却没给 `uid` 的，仍是生产者契约失效。
            if path_names_an_entity(_p):
                _bad.append("%s（缺稳定 UID）" % (_code or "无code"))
                _blocked.append(_rec(
                    "repair_slot_target_unknown", "",
                    "问题 %s 没有给出**稳定 UID**（`uid`/`ref` 都为空）——生产者必须给出"
                    "它要改的那个实体的身份，否则无从知道改谁" % (_code or "无code")))
            else:
                _bad.append("%s（计划级聚合问题，单实体槽位表达不了）" % (_code or "无code"))
            continue
        _covered.append(_code)
        if _kind == "voice_kind":
            _vkind, _vu = _voice_unit_of(view, _uid)
            if _vkind is None:
                _bad.append("%s（目标声音 %r 不在基线视图里）" % (_code, _uid))
                _blocked.append(_rec(
                    "repair_slot_target_unknown", _uid,
                    "问题 %s 指向的声音 %r **不在基线视图里**——上游记录的身份写错了"
                    % (_code, _uid), _uid))
                continue
            _row = {}
            for _r in (_rec_in or {}).get("voice_units") or []:
                if isinstance(_r, dict) and str(_r.get("unit_uid") or "") == _uid:
                    _row = _r
                    break
            _src_kind = str(_row.get("source_kind") or "")
            if _src_kind == "novel_narration" and _vkind == "dialogue":
                _to = "narration"
            elif _src_kind == "novel_speech" and _vkind == "narration":
                _to = "dialogue"
            else:
                # 没有当前有效记录、或记录未给出明确的 novel_speech/novel_narration、
                # 或已经与集合对齐 ⇒ 不生成迁移权限（不猜方向）。
                _bad.append("%s（记录未给出可迁移的来源类型或已对齐：%r/%s）"
                            % (_code, _src_kind, _vkind))
                continue
            if _uid not in _voices_seen:
                _voices_seen.append(_uid)
                _text = str(_vu.get("text") or "")
                _slots.append({
                    "slot_id": "voice_kind:%s" % _uid, "kind": "voice_kind",
                    "fact_uid": "", "card_id": "",
                    "problem_codes": [_code],
                    "claim": "", "evidence": "",
                    "unit_uid": _uid, "unit_kind": _vkind, "unit_text": _text,
                    "reclassify": {"uid": _uid, "from": _vkind, "to": _to,
                                   "to_temp_uid": _voice_temp_uid(_uid)},
                    "allowed_actions": [ACT_RECLASSIFY],
                    "target_uids": [], "voice_target_uids": [],
                    "insert_positions": [], "current_carriers": [],
                    "facts": [], "event_uids": [], "sources": [_text],
                })
            continue
        if _kind == "card_text":
            # **先核实体真的存在**（复核 2026-10-08 P1）：身份写错时不许生成槽位、
            # 更不许发 POST——"目标不存在"要在调用**之前**判，不能花掉一次额度。
            if not _card_of(view, _uid):
                _bad.append("%s（目标卡片 %r 不在基线视图里）" % (_code, _uid))
                _blocked.append(_rec(
                    "repair_slot_target_unknown", _uid,
                    "问题 %s 指向的卡片 %r **不在基线视图里**——上游记录的身份写错了"
                    "（消费者拿不到这个实体，不能替它猜）" % (_code, _uid), _uid))
                continue
            if _uid not in _cards_seen:
                _cards_seen.append(_uid)
            continue
        if not _fact_of(view, _uid):
            _bad.append("%s（目标事实 %r 不在基线视图里）" % (_code, _uid))
            _blocked.append(_rec(
                "repair_slot_target_unknown", _uid,
                "问题 %s 指向的事实 %r **不在基线视图里**——上游记录的身份写错了"
                "（消费者拿不到这个实体，不能替它猜）" % (_code, _uid), _uid))
            continue
        # 2026-10-23（v5 第一包）：**显式单事实目标**。给了 `target_fact_uids` 时，
        # 非目标事实的问题不出槽位（记为 `deferred`，由完整验收继续报告），
        # 于是本轮只可能改到被选中的那一条事实。
        if target_fact_uids is not None and str(_uid) not in {str(_x) for _x in target_fact_uids}:
            _deferred.append(_code)
            continue
        if _uid not in _facts_seen:
            _facts_seen.append(_uid)
    for _uid in _facts_seen:
        _f = _fact_of(view, _uid)
        _cur = _visual_carriers(view, _uid)
        _vt = _voice_targets(view, _uid)
        _acts = ([ACT_CREATE] if allow_create else [])
        if _visual_targets:
            _acts += [ACT_BIND, ACT_REWRITE]
        if _vt:
            _acts.append(ACT_BIND_VOICE)
        _codes = sorted({str((_p or {}).get("code") or "") for _p in (problems or [])
                         if record_uid(_p) == _uid and SLOT_CODE_KIND.get(
                             str((_p or {}).get("code") or "")) in (
                                 "fact_visual", "fact_binding")})
        _slots.append({
            "slot_id": "slot:fact:%s" % _uid, "kind": "fact_visual",
            "fact_uid": _uid, "card_id": "",
            "problem_codes": _codes,
            "claim": str(_f.get("claim") or ""),
            "evidence": str(_f.get("evidence") or ""),
            # 2026-10-23（v5 第一包）：受限模式只开**绑定与改写**；插入由调用方显式关闭。
            "allowed_actions": _acts,
            # 补绑/重写都只能在**现有画面**里选；补建只能插在现有节拍之后（"" = 末尾）
            "target_uids": list(_visual_targets), "insert_positions": [""] + list(_beats),
            "voice_target_uids": [x["uid"] for x in _vt],
            "current_carriers": list(_cur),
            "facts": [_fact_brief(view, _uid)],
            "event_uids": list(_events), "sources": [str(_f.get("evidence") or "")],
        })
    for _cid in _cards_seen:
        _c = _card_of(view, _cid)
        # **卡片槽必须带上它要保留的冻结主张**（复核 P1）：从绑定反查全部受影响事实
        _cfacts = [_fact_brief(view, _f) for _f in _facts_of_card(view, _cid)
                   if _fact_of(view, _f)]
        _affected = [str((_p or {}).get("fact_uid") or "") for _p in (problems or [])
                     if record_uid(_p) == _cid and (_p or {}).get("fact_uid")]
        for _fu in _affected:
            if _fu and _fact_of(view, _fu) and                     not any(_x["uid"] == _fu for _x in _cfacts):
                _cfacts.append(_fact_brief(view, _fu))
        _codes = sorted({str((_p or {}).get("code") or "") for _p in (problems or [])
                         if record_uid(_p) == _cid and SLOT_CODE_KIND.get(
                             str((_p or {}).get("code") or "")) == "card_text"})
        _slots.append({
            "slot_id": "slot:card:%s" % _cid, "kind": "card_text",
            "fact_uid": "", "card_id": _cid,
            "problem_codes": _codes,
            "claim": "", "evidence": "",
            "facts": _cfacts,
            "allowed_actions": [ACT_NARROW_CARD],
            "target_uids": [_cid], "insert_positions": [],
            "current_carriers": [], "current_text": str(_c.get("text") or ""),
            "event_uids": [],
            "sources": [str(_x.get("evidence") or "") for _x in _cfacts
                        if str(_x.get("evidence") or "")],
        })
    return {"slots": _slots, "unexpressible": sorted(set(_bad)),
            "blocked": _blocked, "deferred": sorted(set(_deferred)),
            "covered_codes": sorted(set(_covered))}


def _request_facts(slots) -> list:
    """请求要送的**全部相关冻结事实**（事实槽 + 卡片槽的受影响事实，按 uid 去重）."""
    _out, _seen = [], set()
    for _s in slots or []:
        for _x in (_s.get("facts") or []):
            _u = str(_x.get("uid") or "")
            if _u and _u not in _seen:
                _seen.add(_u)
                _out.append({"uid": _u, "claim": str(_x.get("claim") or ""),
                             "evidence": str(_x.get("evidence") or "")})
    return _out


def _beat_brief(view, uid) -> dict:
    """允许集合里的**一条现有画面**：uid + 正文 + 目的 + 事件归属（供模型作关系选择）."""
    for _b in (view or {}).get("beats") or []:
        if isinstance(_b, dict) and str(_b.get("uid") or "") == str(uid):
            return {"uid": str(uid), "text": str(_b.get("text") or ""),
                    "purpose": str(_b.get("purpose") or ""),
                    "event_uids": [str(x) for x in (_b.get("event_uids") or [])]}
    return {}


def _card_brief(view, card_id) -> dict:
    """允许集合里的**一张真实文字卡**（`card_id` 就是它的身份，不改写、不冒充画面）."""
    _c = _card_of(view, card_id)
    if not _c:
        return {}
    return {"card_id": str(card_id), "text": str(_c.get("text") or ""),
            "beat_uid": str(_c.get("beat_uid") or "")}


def _voice_brief(view, kind, uid) -> dict:
    """允许集合里的**一条声音单位**（对白 / 旁白**按 kind 分别查**，不跨种别兜底）."""
    from shared.presentation_contract import ref_uid
    for _pool, _k in (((view or {}).get("dialogue_units") or [], "dialogue"),
                      ((view or {}).get("narration_units") or [], "narration")):
        if _k != str(kind):
            continue
        for _u in _pool:
            if isinstance(_u, dict) and str(ref_uid(_u)) == str(uid):
                return {"uid": str(uid), "kind": _k,
                        "text": str(_u.get("text") or "")}
    return {}


def _voice_unit_of(view, uid) -> tuple:
    """`uid → (kind, unit)`（对白 / 旁白分别查；找不到返回 `(None, {})`）."""
    from shared.presentation_contract import ref_uid
    for _key, _kind in (("dialogue_units", "dialogue"),
                        ("narration_units", "narration")):
        for _u in (view or {}).get(_key) or []:
            if isinstance(_u, dict) and str(ref_uid(_u)) == str(uid):
                return _kind, _u
    return None, {}


def _voice_targets(view, fact_uid) -> list:
    """事实可绑定的**现有声音**：正文（去空白/标点）包含该事实的主张或证据.

    只做确定性包含，不做相似度；没有逐字/归一化包含就不出声音目标，绝不猜。
    """
    _f = _fact_of(view, fact_uid)
    _claim = _norm_key(_f.get("claim"))
    _ev = _norm_key(_f.get("evidence"))
    _out: list = []
    for _key in ("dialogue_units", "narration_units"):
        for _u in (view or {}).get(_key) or []:
            if not isinstance(_u, dict):
                continue
            _t = _norm_key(_u.get("text"))
            if not _t:
                continue
            if (_claim and _claim in _t) or (_ev and _ev in _t):
                _out.append({"kind": _key.split("_")[0],
                             "uid": str(_u.get("uid") or "")})
    return [x for x in _out if x["uid"]]


def _norm_key(text) -> str:
    """比较用的归一化：只留字母/数字（中文按 `isalnum` 保留），去空白与标点."""
    return "".join(ch for ch in str(text or "") if ch.isalnum())


def _voice_temp_uid(uid) -> str:
    """声音迁移的目标临时 uid（与 `_slug` 同一单射；scope 与 compile 共用）."""
    return "new:voice:%s" % _slug(uid)


def _novel_span(text, novel_text) -> str:
    """正文在原文中的**逐字连续子串**；取不到返回空串（调用方拒收，不相似匹配）."""
    _t = str(text or "")
    return _t if _t and _t in str(novel_text or "") else ""


#: 载体 kind → 请求里的实体块（**按 kind 查真实实体**；卡片身份绝不冒充画面 UID）.
_KIND_BLOCK = {"visual": "beats", "screen_card": "cards",
               "dialogue": "voice", "narration": "voice"}
#: 实体块 → 「查不到」时的责任说明（准入失败要带**路径**，不把坏引用悄悄过滤成空）.
_BLOCK_CN = {"beats": "现有画面", "cards": "现有文字卡", "voice": "现有声音单位"}


def _request_entities(view, slots) -> tuple:
    """请求要送的**实体闭包**：`(entities, blocked)`.

    `entities = {"beats": [uid...], "cards": [card_id...], "voice": [(kind, uid)...]}`。

    ⚠️ **上下文集合 = 动作目标/插入位置 + 受影响事实的完整载体闭包**（复核 2026-10-08
    P1「卡片联合承载」）：旧写法只收 `target_uids`/`insert_positions`，于是卡片槽
    （目标就是卡片本身）把 `card_id` 当成画面 UID 去查，查不到就发一条
    `uid=<card_id>/text=""` 的**假画面**，而**真的演出了这条事实的画面正文**（联合承载
    的另一个载体）根本没进请求——模型看不到"卡片要保住的那段演出还写在哪"。
    对白 / 旁白同样按关系补上，防止"卡片 + 台词"联合承载时只送卡片。

    去重只去**实体**；关系（`current_bindings`）逐项保留。查不到的引用**不净化**：
    记一条带路径的问题记录交调用方留档，可见但不假装它存在。
    """
    from shared.presentation_contract import ref_uid
    _ent = {"beats": [], "cards": [], "voice": []}
    _seen: set = set()
    _blocked: list = []

    def _add(kind, uid, why) -> None:
        _u = str(uid or "")
        if not _u:
            return
        _block = _KIND_BLOCK.get(str(kind))
        if _block is None:
            _blocked.append(_rec("repair_slot_carrier_kind_unknown", _u,
                                 "%s 引用了未知载体种类 %r（合法：%s）"
                                 % (why, kind, sorted(_KIND_BLOCK)), kind))
            return
        if (_block, _u) in _seen:
            return
        _seen.add((_block, _u))
        if _block == "voice":
            _b = _voice_brief(view, kind, _u)
        elif _block == "cards":
            _b = _card_brief(view, _u)
        else:
            _b = _beat_brief(view, _u)
        if not _b:
            _blocked.append(_rec("repair_slot_carrier_missing", _u,
                                 "%s 引用的%s %r **不在基线视图里**——不把它净化掉，"
                                 "也不拿别的种类的身份顶替"
                                 % (why, _BLOCK_CN[_block], _u), _u))
            return
        _ent[_block].append((str(kind), _u) if _block == "voice" else _u)

    for _s in slots or []:
        _sid = str(_s.get("slot_id") or "")
        _is_card = str(_s.get("kind") or "") == "card_text"
        for _u in _s.get("target_uids") or []:
            _add("screen_card" if _is_card else "visual", _u,
                 "槽位 %s 的可选目标" % _sid)
        for _u in _s.get("insert_positions") or []:
            if _u:
                _add("visual", _u, "槽位 %s 的插入位置" % _sid)
        _vu = str(_s.get("unit_uid") or "")
        if _vu:
            _add(str(_s.get("unit_kind") or ""), _vu,
                 "槽位 %s 的声音单位" % _sid)
        for _u in _s.get("voice_target_uids") or []:
            _vk, _ = _voice_unit_of(view, _u)
            if _vk:
                _add(_vk, _u, "槽位 %s 的候选声音" % _sid)
        # **受影响事实的完整载体闭包**：这条事实现在由哪些载体演出，逐条给出真实正文
        for _x in _s.get("facts") or []:
            _fu = str(_x.get("uid") or "")
            for _c in _x.get("carriers") or []:
                _add(str((_c or {}).get("kind") or ""), ref_uid(_c),
                     "事实 %s 的当前载体" % _fu)
    return _ent, _blocked


def build_editable_scope(view, slots, *, answer=None) -> dict:
    """本轮**允许改什么**的单一事实源（请求与执行器共用同一份）.

    `answer=None`（请求阶段）：列出本轮**可能**发生的全部合法编辑——允许改绑定的
    `allowed_fact_uids`、允许改写正文的 `allowed_rewrite_beat_uids`、只绑不改的
    `allowed_bind_beat_uids`、允许插入的位置、允许改写的卡片、受保护字段与冻结集合、
    以及"目标节拍上还载着哪些**非目标**事实"（`co_carried_facts_by_target`）。

    给了 `answer`（执行阶段）：收敛成**该动作实际授权**的面——
    `bind_visual` 不给正文权限；`rewrite_visual` 只给所选 `target_uid` 的正文权限；
    两者都只给该槽事实的绑定权限。有效权限 = 请求权限 ∩ 回答动作授权。

    纯函数：不读文件、不调模型、不改入参。
    """
    from shared.presentation_contract import ref_uid
    _slots = [_s for _s in (slots or []) if isinstance(_s, dict)]
    _by_sid = {str(_s.get("slot_id")): _s for _s in _slots}
    _facts: set = set()
    _rewrite: set = set()
    _bind: set = set()
    _insert: set = set()
    _cards: set = set()
    _target_beats: set = set()
    _reclass: list = []
    _bind_voice: set = set()

    def _reclass_closure(entries):
        """迁移条目 → 需要同步的节拍 `voice_ref` 与事实绑定（只做同内容身份替换）."""
        _beats: set = set()
        _rebind: set = set()
        for _rc in entries or []:
            _vuid = str(_rc.get("uid") or "")
            _vfrom = str(_rc.get("from") or "")
            for _b in (view or {}).get("beats") or []:
                if not isinstance(_b, dict):
                    continue
                _vr = _b.get("voice_ref") or {}
                if ref_uid(_vr) == _vuid and str(_vr.get("kind") or "") == _vfrom:
                    _beats.add(str(_b.get("uid") or ""))
            for _b in (view or {}).get("fact_bindings") or []:
                if not isinstance(_b, dict):
                    continue
                for _c in _b.get("carriers") or []:
                    if ref_uid(_c) == _vuid and str(_c.get("kind") or "") == _vfrom:
                        _rebind.add(str(_b.get("fact_uid") or ""))
                        break
        return _beats, _rebind

    for _s in _slots:
        _acts = {str(_a) for _a in (_s.get("allowed_actions") or [])}
        if _s.get("kind") == "voice_kind":
            _rc = _s.get("reclassify")
            if isinstance(_rc, dict):
                _reclass.append(dict(_rc))
            continue
        if _s.get("kind") == "card_text":
            _cid = str(_s.get("card_id") or "")
            if _cid:
                _cards.add(_cid)
            continue
        _fu = str(_s.get("fact_uid") or "")
        if _fu:
            _facts.add(_fu)
        if ACT_BIND_VOICE in _acts:
            _bind_voice |= {str(x) for x in (_s.get("voice_target_uids") or [])}
        if ACT_CREATE in _acts:
            # 新建画面本轮**不在收紧范围**（插入是后续包）：用 `"*"` 表示"允许插入"，
            # 只保留"不许删除/重排既有节拍"这一条。
            _insert.add("*")
        for _t in _s.get("target_uids") or []:
            _t = str(_t)
            if ACT_REWRITE in _acts:
                _rewrite.add(_t)
                _target_beats.add(_t)
            if ACT_BIND in _acts:
                _bind.add(_t)
                _target_beats.add(_t)
    # 同载保护：绑在**目标节拍**上的其他事实（不是本轮的目标）
    _co: dict = {}
    for _b in (view or {}).get("fact_bindings") or []:
        if not isinstance(_b, dict):
            continue
        _fu = str(_b.get("fact_uid") or "")
        if _fu in _facts:
            continue
        for _c in _b.get("carriers") or []:
            _t = str(ref_uid(_c))
            if _t in _target_beats:
                _co.setdefault(_t, []).append(_fu)
    _hk = (view or {}).get("hook_bindings") or {}
    _hook = _hk if any(str(ref_uid(_c)) in _target_beats
                       for _c in (_hk.get("carriers") or [])) else {}
    if isinstance(answer, dict):
        _ef, _er, _eb, _ei, _ec = set(), set(), set(), set(), set()
        _ar, _abv = [], set()
        for _it in answer.get("answers") or []:
            if not isinstance(_it, dict):
                continue
            _s = _by_sid.get(str(_it.get("slot_id") or ""))
            if _s is None:
                continue
            _act = str(_it.get("action") or "")
            if _s.get("kind") == "voice_kind":
                _rc = _s.get("reclassify") or {}
                if _act == ACT_RECLASSIFY and isinstance(_rc, dict):
                    _ar.append(dict(_rc))
                continue
            if _s.get("kind") == "card_text":
                if _act == ACT_NARROW_CARD and str(_s.get("card_id") or "") in _cards:
                    _ec.add(str(_s.get("card_id")))
                continue
            _tgt = str(_it.get("target_uid") or "")
            if _act == ACT_REWRITE and _tgt in _rewrite:
                _er.add(_tgt)
                _ef.add(str(_s.get("fact_uid") or ""))
            elif _act == ACT_BIND and _tgt in _bind:
                _eb.add(_tgt)
                _ef.add(str(_s.get("fact_uid") or ""))
            elif _act == ACT_BIND_VOICE and _tgt in {
                    str(x) for x in (_s.get("voice_target_uids") or [])}:
                _abv.add(_tgt)
                _ef.add(str(_s.get("fact_uid") or ""))
            elif _act == ACT_CREATE and ACT_CREATE in {str(_a) for _a in
                                                       (_s.get("allowed_actions") or [])}:
                _ei.add("*")
                _ef.add(str(_s.get("fact_uid") or ""))
        _facts, _rewrite, _bind, _insert, _cards = _ef, _er, _eb, _ei, _ec
        _reclass, _bind_voice = _ar, _abv
    _voice_beats, _rebind_facts = _reclass_closure(_reclass)
    return {
        "phase": "effective" if isinstance(answer, dict) else "request",
        "allowed_fact_uids": sorted(x for x in _facts if x),
        "allowed_rewrite_beat_uids": sorted(_rewrite),
        "allowed_bind_beat_uids": sorted(_bind),
        "allowed_insert_after_uids": sorted(_insert),
        "allowed_card_ids": sorted(_cards),
        "beat_protected_fields": ["uid", "event_uids", "purpose", "evidence", "source",
                                  "voice_ref"],
        # P1a：声音集合不再整块冻结；未授权的单位仍逐字段冻结（scope_violations 判）。
        "frozen_collections": ["frozen_facts", "frozen_events"],
        "allowed_reclassify_voices": sorted(_reclass,
                                            key=lambda x: str(x.get("uid") or "")),
        "allowed_bind_voice_uids": sorted(_bind_voice),
        "allowed_rebind_fact_uids": sorted(_rebind_facts),
        "allowed_voice_ref_beat_uids": sorted(_voice_beats),
        "co_carried_facts_by_target": {k: sorted(set(v)) for k, v in _co.items()},
        "protected_hook": _hook,
    }


def slot_request(view, slots, *, limits=None, remaining_calls=None,
                 remaining_sec=None) -> dict:
    """装配槽位请求（**显式版本**；与 delta 请求同源的两个基线摘要）.

    请求里带的是**允许集合本身**：每个槽位的动作、目标、插入位置、事件与来源都由代码
    列出，模型只在这些集合里选。**不要求它重抄实体表、`order` 或哈希**（§5.6 第 2 条）。

    实体**按种类分别给出**（`existing_beats` / `existing_cards` / `existing_voices`）——
    联合承载时模型要同时看到画面、卡片、对白/旁白的**真实正文**，才能决定"缩短哪一部分
    仍然保得住这条主张"（复核 2026-10-08 P1）。
    """
    from shared.presentation_contract import fact_basis_digest, view_digest, fact_binding_policy
    from step01_script_gen.plan_feasibility import planning_resource_context
    from shared.presentation_contract import ref_uid
    # **同一份可编辑范围**：请求里带出去的就是执行器要核的那份（不再各自临时算）。
    _scope = build_editable_scope(view, slots)
    _co = _scope.get("co_carried_facts_by_target") or {}
    protected = [_fact_brief(view, _fu) for _fu in
                 sorted({_f for _v in _co.values() for _f in _v}) if _fact_of(view, _fu)]
    protected_hook = _scope.get("protected_hook") or {}
    context_slots = list(slots or []) + [{'facts': protected}]
    if protected_hook:
        context_slots.append({'facts':[{'uid':'hook','carriers':protected_hook.get('carriers',[])}]})
    _ent, _blocked = _request_entities(view, context_slots)
    _bindings: list = []
    _seen_facts: set = set()
    for _s in slots or []:
        for _x in _s.get("facts") or []:
            _u = str(_x.get("uid") or "")
            if _u and _u not in _seen_facts:
                _seen_facts.add(_u)
                _bindings.append({"fact_uid": _u,
                                  "carriers": _x.get("carriers") or []})
    return {
        "schema": SLOT_REQUEST_SCHEMA, "rule_version": REPAIR_RULE_VERSION,
        "base_plan_hash": view_digest(view), "fact_basis_hash": fact_basis_digest(view),
        "limits": dict(limits or {}, remaining_calls=remaining_calls,
                       remaining_sec=remaining_sec),
        "resources": planning_resource_context(view),
        "frozen_facts": _request_facts(slots),
        "current_bindings": _bindings,
        "binding_policy": fact_binding_policy(view),
        "protected_facts":protected,
        "protected_hook":({'claim':(view.get('plan_basis') or {}).get('end_hook'),
                           'bindings':protected_hook} if protected_hook else {}),
        #: 本轮**可编辑范围**（与执行器同源；`answer` 收敛后的有效范围由 `build_editable_scope(view, slots, answer=…)` 得出）
        "editable_scope": _scope,
        "frozen_events": [str(_e) for _e in (view or {}).get("frozen_events") or []],
        "existing_beats": [_beat_brief(view, _u) for _u in _ent["beats"]],
        "existing_cards": [_card_brief(view, _u) for _u in _ent["cards"]],
        "existing_voices": [_voice_brief(view, _k, _u) for _k, _u in _ent["voice"]],
        #: 引用查不到 / 种类写错时的**带路径记录**（不净化、不静默丢）
        "context_problems": _blocked,
        "slots": [dict(_s) for _s in slots],
    }


_SLOT_RULE_HEAD = """# 这一轮只做**内容与关系选择**（代码装配增量并派生编号）

对下面**每一个槽位**恰好给一条回答。`slot_id` 必须原样回带，`action` 必须是该槽位
`allowed_actions` 里的一个。**不要**输出增量表、`order`、实体表或任何哈希——那些由代码
按基线装配；也不要把这些值抄回来。

各动作要给的字段（**必填/禁用由代码从同一份契约生成**，与随请求下发的结构化声明同源）：
"""

_SLOT_RULE_TAIL = """正文只写内容本身；**不要**写编号说明、解释、示例或 Markdown。事实、原文、事件顺序与
冻结字段都不许改。做不到就**按允许集合选一个最接近的合法动作**，不要自创动作或目标。
不使用的字段**不要出现**（不要给 `null`、不要给空数组凑数）——不适用字段带着值传进来
会被整轮拒收。

# 输出形状（**逐字照这个外壳，别换键名**）

只输出**一个** JSON 对象，顶层**只能**有 `schema` 与 `answers` 两个键：

{"schema": "plan_content_repair.answer.v1", "answers": [{"slot_id": "<原样回带>", "action": "<允许动作>", "…": "该动作的字段"}]}

**不要**输出 `type`、`slot_choices`、`result`、`data` 之类的别的顶层键名，也不要包一层
信封——本通道不会用结构化 schema 约束解码，形状只由这段文字承担，键名写错会被整轮拒收。
"""

#: 每个动作的**语义说明**（人写）；必填/禁用/空值规则由 `ACTION_FIELDS` **派生**，
#: 不在这段文案里重抄一遍（复核 2026-10-08 P2：手写的 schema 与手写的说明会各自漂移）。
_ACT_RULE = {
    ACT_CREATE: ("`text` = 新画面的**可演正文**（描述观众看到什么，不是抄原文）；"
                 "`insert_after_uid` = 插在哪个现有画面之后（`insert_positions` 里选，"
                 "空串 = 排在最后）；`event_index` = 这条画面归属的冻结事件序号"
                 "（1..N，**0 = 不归属任何冻结事件**，回忆/背景画面通常为 0）"),
    ACT_BIND: ("`target_uid` = **已经演出**该事实的现有画面（`target_uids` 里选）；"
               "`text` 只能给**空串**"),
    ACT_REWRITE: ("`target_uid` = 要改写的现有承载画面（`target_uids` 里选）；"
                  "`text` = 改写后的画面正文"),
    ACT_NARROW_CARD: "`text` = 从原文截取的**更短且逐字一致**的片段（改后不得超出阅读预算）",
    ACT_RECLASSIFY: "只回带 `slot_id` 与动作；迁移方向、目标集合与临时身份由代码从当前"
                    "已准入核验记录派生，**不要**填正文、目标或任何哈希",
    ACT_BIND_VOICE: "`target_uid` = 现有声音单位（`voice_target_uids` 里选）；`text` 只能给"
                    "空串；只替换该事实的单载体绑定，不改声音正文",
}


def _render_slot_rule() -> str:
    """选择规则正文——**动作分支的必填/禁用逐条从 `ACTION_FIELDS` 渲染**."""
    _lines = []
    for _act in ACTIONS:
        _spec = ACTION_FIELDS.get(_act) or {}
        _req = "、".join("`%s`" % _f for _f in (_spec.get("required") or ())) or "（无）"
        _ban = "、".join("`%s`" % _f for _f in (_spec.get("forbidden") or ()))
        _lines.append("- `%s`：必填 %s；%s%s"
                      % (_act, _req,
                         ("**不许出现** %s；" % _ban) if _ban else "",
                         _ACT_RULE.get(_act, "")))
    return _SLOT_RULE_HEAD + chr(10).join(_lines) + "\n\n" + _SLOT_RULE_TAIL


_SLOT_RULE = _render_slot_rule()


def slot_prompt(request, novel_text: str = "") -> str:
    """请求 → 实际发出的提示（正文块 + 选择规则 + 槽位表）."""
    _body = json.dumps(request, ensure_ascii=False, indent=1)
    _head = ("# 原文（冻结，不许改）\n%s\n" % novel_text) if novel_text else ""
    policy=request.get('binding_policy') or {}
    rule=('本次每项事实只允许一个载体。所选画面将替换该事实原有绑定，'
          '因此画面正文须完整表达整条主张，不能依赖被替换的声音载体补足。'
          if policy.get('selection')=='replace' else
          '所选画面加入该事实原有载体，按联合承载保留其他绑定。')
    rule+=' protected_facts 和 protected_hook 是可选目标还承载的其他内容；改写目标必须保留其完整主张和核心关系。'
    return ("%s\n# 槽位请求（只在这些集合里选）\n%s\n%s\n%s"
            % (_head, _body, _SLOT_RULE,rule))


def _is_str(value) -> bool:
    return isinstance(value, str)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def answer_problems(answer, slots, *, where: str = "槽位回答") -> list:
    """槽位回答的**严格准入**：版本 / 白名单键 / 类型 / 槽位一一对应 / 动作与目标越界.

    **不按形状猜路线**（§5.6 第 2 条）：`schema` 不是本版就整份拒收；未知键不忽略；
    槽位**缺答、多答、重答**都拒收（少答一个槽 = 那条问题这一轮没被处理，不能当"改完了"）。
    """
    _p: list = []
    if not isinstance(answer, dict):
        return [_rec("repair_answer_not_object", "",
                     "%s 必须是对象（当前是 %s）" % (where, type(answer).__name__), answer)]
    if str(answer.get("schema") or "") != SLOT_ANSWER_SCHEMA:
        _p.append(_rec("repair_answer_schema_unknown", "schema",
                       "未知或缺失的回答 schema：%s（期望 %s）"
                       % (_brief(answer.get("schema")), SLOT_ANSWER_SCHEMA),
                       answer.get("schema")))
    for _k in sorted(set(answer) - {"schema", "answers"}):
        _p.append(_rec("repair_answer_unknown_key", _k,
                       "回答里有未知顶层键 `%s`（合法键：schema / answers）——不忽略，"
                       "拼错的键会让选择被悄悄丢掉" % _k, _k))
    _items = answer.get("answers")
    if not isinstance(_items, list):
        _p.append(_rec("repair_answer_items_type", "answers",
                       "`answers` 必须是数组（当前是 %s）" % type(_items).__name__, _items))
        return _p
    _by_id = {str(_s.get("slot_id")): _s for _s in (slots or [])}
    _seen: list = []
    for _i, _it in enumerate(_items):
        _at = "answers[%d]" % _i
        if not isinstance(_it, dict):
            _p.append(_rec("repair_answer_item_not_object", _at,
                           "%s 必须是对象（当前是 %s）" % (_at, type(_it).__name__), _it))
            continue
        for _k in sorted(set(_it) - set(ANSWER_KEYS)):
            _p.append(_rec("repair_answer_unknown_key", _at,
                           "%s 有未知键 `%s`（合法键：%s）" % (_at, _k, list(ANSWER_KEYS)),
                           _k))
        _sid = _it.get("slot_id")
        if not _is_str(_sid) or not _sid.strip():
            _p.append(_rec("repair_answer_field_type", _at,
                           "%s 的 `slot_id` 必须是非空字符串（当前是 %s）"
                           % (_at, _brief(_sid)), _sid))
            continue
        if _sid not in _by_id:
            _p.append(_rec("repair_answer_slot_unknown", _sid,
                           "回答里的 `slot_id` %r 不在本轮槽位里（不许自创槽位）" % _sid,
                           _sid))
            continue
        if _sid in _seen:
            _p.append(_rec("repair_answer_slot_duplicate", _sid,
                           "槽位 %s 被回答了不止一次（同槽多答不许后者覆盖前者）" % _sid))
            continue
        _seen.append(_sid)
        _slot = _by_id[_sid]
        _act = _it.get("action")
        if not _is_str(_act) or not _act.strip():
            _p.append(_rec("repair_answer_field_type", _sid,
                           "槽位 %s 的 `action` 必须是非空字符串（当前是 %s）"
                           % (_sid, _brief(_act)), _act))
            continue
        if _act not in (_slot.get("allowed_actions") or []):
            _p.append(_rec("repair_answer_action_not_allowed", _sid,
                           "槽位 %s 的动作 %r 不在允许集合 %s 里"
                           % (_sid, _act, list(_slot.get("allowed_actions") or [])), _act))
            continue
        # ---- ① **先核所有已提供字段**（类型 + 适用性），再核必填与取值 ----
        # 复核 2026-10-08 P2：旧写法只在**对应动作分支**里核 `target_uid` /
        # `insert_after_uid` / `event_index`，于是 `narrow_card` 带着
        # `target_uid=123` / `insert_after_uid=[]` / `event_index=True` 三例**都返回空
        # 问题**，编译时又把这些字段丢掉——"带语义的字段默默消失"。
        _spec = ACTION_FIELDS.get(_act) or {}
        _bad_field = False
        for _f in ANSWER_KEYS:
            if _f in ("slot_id", "action") or _f not in _it:
                continue
            _v = _it.get(_f)
            if _f in (_spec.get("forbidden") or ()):
                _p.append(_rec("repair_answer_field_not_applicable", _sid,
                               "槽位 %s 的动作 %s 不使用字段 `%s`（当前给了 %s）——"
                               "不适用字段不许带语义地传进来"
                               % (_sid, _act, _f, _brief(_v)), _v))
                _bad_field = True
                continue
            if not _type_ok(_v, _FIELD_TYPE[_f]):
                _p.append(_rec("repair_answer_field_type", _sid,
                               "槽位 %s 的 `%s` 类型不对：要 %s，当前是 %s（%s）"
                               "——不字符串化、不按动作分支跳过"
                               % (_sid, _f, _FIELD_TYPE[_f], _brief(_v),
                                  type(_v).__name__), _v))
                _bad_field = True
        if _bad_field:
            continue
        # ---- ② 必填与取值（**同一份契约**驱动，schema 也由它生成）----
        for _f in (_spec.get("required") or ()):
            if _f not in _it:
                _p.append(_rec("repair_answer_field_missing", _sid,
                               "槽位 %s 的动作 %s 必须给 `%s`" % (_sid, _act, _f)))
                _bad_field = True
        if _bad_field:
            continue
        _txt = str(_it.get("text") or "")
        if _act in (ACT_CREATE, ACT_REWRITE, ACT_NARROW_CARD) and not _txt.strip():
            _p.append(_rec("repair_answer_text_empty", _sid,
                           "槽位 %s 的动作 %s 必须给出**非空白**正文" % (_sid, _act), _txt))
            continue
        if _act == ACT_BIND and _txt != "":
            # **`""` 是唯一合法值**（不是"空白即可"）：结构化声明把 `text` 收成
            # `enum [""]`，两处判据必须逐字对齐——`"   "` 本地放行而 schema 拒绝
            # 就是"同源"没做到（复核 2026-10-08 P2）。
            _p.append(_rec("repair_answer_text_unexpected", _sid,
                           "槽位 %s 的动作 %s 只补关系、不改正文（`text` 只能给空串，"
                           "空白串也不算空）" % (_sid, _act), _txt))
            continue
        if _act == ACT_BIND_VOICE and _txt != "":
            _p.append(_rec("repair_answer_text_unexpected", _sid,
                           "槽位 %s 的动作 %s 只替换声音绑定、不改正文（`text` 只能给空串）"
                           % (_sid, _act), _txt))
            continue
        if _act in (ACT_BIND, ACT_REWRITE):
            _tgt = _it.get("target_uid", "")
            if _tgt not in (_slot.get("target_uids") or []):
                _p.append(_rec("repair_answer_target_not_allowed", _sid,
                               "槽位 %s 的目标 %s 不在允许集合里（要 %s）"
                               % (_sid, _brief(_tgt), list(_slot.get("target_uids") or [])),
                               _tgt))
                continue
        if _act == ACT_BIND_VOICE:
            _tgt = _it.get("target_uid", "")
            if _tgt not in (_slot.get("voice_target_uids") or []):
                _p.append(_rec("repair_answer_target_not_allowed", _sid,
                               "槽位 %s 的声音目标 %s 不在 `voice_target_uids` 里（要 %s）"
                               "——声音目标与画面目标分键，不互相顶替"
                               % (_sid, _brief(_tgt),
                                  list(_slot.get("voice_target_uids") or [])), _tgt))
                continue
        if _act == ACT_CREATE:
            _pos = _it.get("insert_after_uid", "")
            if _pos not in (_slot.get("insert_positions") or []):
                _p.append(_rec("repair_answer_position_not_allowed", _sid,
                               "槽位 %s 的插入位置 %s 不在允许集合里（要 %s；空串 = 末尾）"
                               % (_sid, _brief(_pos),
                                  list(_slot.get("insert_positions") or [])), _pos))
                continue
            _ei = _it.get("event_index")
            if not _is_int(_ei) or _ei < 0 or _ei > len(_slot.get("event_uids") or []):
                _p.append(_rec("repair_answer_event_index_invalid", _sid,
                               "槽位 %s 的 `event_index` 必须是 0..%d 的整数"
                               "（0 = 不归属任何冻结事件；当前是 %s）"
                               % (_sid, len(_slot.get("event_uids") or []), _brief(_ei)),
                               _ei))
                continue
    for _sid in _by_id:
        if _sid not in _seen:
            _p.append(_rec("repair_answer_slot_unanswered", _sid,
                           "槽位 %s 没有被回答——本轮要求的修改必须逐槽给出选择，"
                           "少答一个槽不算改完" % _sid))
    return _p


def source_spans_by_fact(record) -> dict:
    """**已准入的核验记录** → `fact_uid → 逐字来源片段`.

    新建画面的「原文依据」必须逐字出自原文（`presentation_beat_evidence_not_in_novel`），
    而冻结事实自带的 `evidence` **不保证逐字**（源轴允许核验方引用别的片段）。所以由代码
    从**已准入的那份记录**里取——那些片段是核验方引用、且已经被源轴判过"在本次原文里"
    的；**不要求模型抄原文**（§5.6 第 2 条）。
    """
    _out: dict = {}
    for _x in (record or {}).get("source_support") or []:
        if not isinstance(_x, dict):
            continue
        _spans = [str(_s) for _s in (_x.get("evidence_spans") or []) if str(_s).strip()]
        if _spans:
            _out[str(_x.get("fact_uid") or "")] = _spans
    return _out


def _pick_evidence(slot, wanted, novel_text, view=None, fact_uid="") -> str:
    """新建节拍的「原文依据」：**逐字**优先，谁也不抄原文（§5.6 第 2 条）.

    候选次序：**已准入核验记录里该事实的来源片段** → 槽位自带的证据/来源 →
    该事实**逐字的载体正文**（卡片正文本来就是逐字原文）。三者都拿不到逐字片段时，
    退回槽位证据原样——验收会如实报出来，不在这里编造出处。
    """
    from step01_script_gen.plan_feasibility import _source_norm
    _hay = _source_norm(novel_text or "")
    _cands = list(wanted or []) + [str(slot.get("evidence") or "")]         + [str(_t) for _t in (slot.get("sources") or []) if str(_t)]
    if view is not None and fact_uid:
        from shared.content_verification import all_carrier_texts
        _cands += sorted((str(_t) for _t in all_carrier_texts(view, fact_uid) if str(_t)),
                         key=len, reverse=True)
    for _c in _cands:
        if _c.strip() and (not _hay or _source_norm(_c) in _hay):
            return _c
    return str(slot.get("evidence") or "")


def _slug(value) -> str:
    """稳定 UID → 临时 UID 的**单射**片段（合法字符集内一对一，不做"字符替换"）.

    复核 2026-10-08 P2：旧写法"非字母数字一律换 `_`"**不是单射**——`case.a` 与 `case_a`
    清洗后都变成 `case_a`，于是两条**合法且不同**的槽位被编译成**同一个新实体**；正文
    不同时报 `repair_slot_conflict`（编译器自己制造的冲突，不是模型改了同一个实体）。

    现在的编码：安全字符（字母 / 数字 / `-`）原样保留，**其余每个字符**（含 `_` 本身）
    编成 `_<码点十六进制>_`。`_` 只作为转义定界符出现、转义段内只有十六进制数字 ⇒
    解析唯一 ⇒ 映射**单射**（不同槽位身份永不撞成同一实体）。临时 UID 的正式编号仍由
    既有 codec 派生，本函数只负责"这一步不丢身份"。
    """
    _out: list = []
    for _c in str(value):
        if _c == "-" or _c.isalnum():
            _out.append(_c)
        else:
            _out.append("_%x_" % ord(_c))
    return "".join(_out) or "x"


def compile_delta(view, slots, answer, *, novel_text: str = "",
                  evidence_by_fact=None) -> tuple:
    """槽位回答 → **既有 delta**（`plan_revision.delta.v1`）；`returns (delta, problems)`.

    只改被选择的那几条实体与那几条绑定：**未修改字段一律保留**（节拍的事件归属、出处、
    发声引用、卡片的 `template`/`placement`/`beat_uid`、其余事实的绑定……）。临时 UID、
    最终顺序与身份映射由本函数负责，**不要求模型抄写**。

    多槽落在**同一实体**上：内容一致 ⇒ 合并（同一事务）；内容不同 ⇒ **拒收并回灌**
    （`repair_slot_conflict`），不静默让后者覆盖前者。

    `novel_text` / `evidence_by_fact`（`source_spans_by_fact(记录)`）用于给**新建画面**
    选一条**逐字出自原文**的「原文依据」——要素由代码给，不让模型抄原文。
    """
    from shared.presentation_contract import (DELTA_RULE_VERSION, DELTA_SCHEMA,
                                              KIND_DIALOGUE, KIND_NARRATION,
                                              KIND_VISUAL, fact_basis_digest,
                                              ref_uid, view_digest, fact_binding_policy)
    _p: list = []
    _by_id = {str(_s.get("slot_id")): _s for _s in (slots or [])}
    _items = {str(_i.get("slot_id")): _i
              for _i in (answer or {}).get("answers") or [] if isinstance(_i, dict)}
    _base_beats = [_b for _b in (view or {}).get("beats") or [] if isinstance(_b, dict)]
    _ids = [str(_b.get("uid") or "") for _b in _base_beats]
    _cards = {str(_c.get("card_id") or _c.get("uid") or ""): _c
              for _c in (view or {}).get("screen_cards") or [] if isinstance(_c, dict)}
    _events = [str(_e) for _e in (view or {}).get("frozen_events") or []]
    _template = copy.deepcopy(_base_beats[0]) if _base_beats else {}
    _beats_up: dict = {}
    _cards_up: dict = {}
    _order = list(_ids)
    _order_changed = False
    _new_at: dict = {}          # 锚点 uid（"" = 末尾）→ 该锚点下的新画面（**按回答顺序**）
    _bindings: dict = {}
    _wrote: dict = {}
    _units_up: dict = {"dialogue": {}, "narration": {}}
    _units_del: dict = {"dialogue": [], "narration": []}
    _voice_order_changed = False

    def _claim_write(kind, uid, slot_id, payload) -> str:
        """同一实体被两个槽写：`"new"` 写一次 / `"same"` 复用（**该槽的关系照旧要应用**）
        / `"conflict"` 整轮拒收.

        复核 2026-10-08 P1：旧写法返回 `False` 后直接 `continue`，于是"正文一致"的第二个
        槽**连它自己那条绑定也一起丢了**（f02/f05 共享 b01 时 delta 里只剩 f02）。实体写入
        的去重与**每槽的关系修改是两件事**——去重只决定"这条实体写几次"。
        """
        _prev = _wrote.get((kind, uid))
        if _prev is None:
            _wrote[(kind, uid)] = (slot_id, payload)
            return "new"
        if _prev[1] == payload:
            return "same"
        _p.append(_rec("repair_slot_conflict", slot_id,
                       "%s %s 被两个槽写成不同内容（%s / %s）——不许后者覆盖前者"
                       % (kind, uid, _prev[0], slot_id), uid))
        return "conflict"

    def _binding(fact_uid):
        if fact_uid not in _bindings:
            _b = _bindings_of(view, fact_uid)
            _bindings[fact_uid] = copy.deepcopy(_b) if _b else {
                "fact_uid": fact_uid, "carriers": []}
        return _bindings[fact_uid]

    # **按回答顺序遍历**（复核 2026-10-08 P2）：同锚点的相对顺序以 `answers` 的顺序为准，
    # 不是槽位表顺序——否则"同一批槽位、只把回答换序"编译出来的顺序不变，模型明确表达
    # 的先后就被丢掉了。
    _seq = [(str(_i.get("slot_id")), _i) for _i in (answer or {}).get("answers") or []
            if isinstance(_i, dict) and str(_i.get("slot_id")) in _by_id]
    for _sid, _it in _seq:
        _slot = _by_id[_sid]
        _act = str(_it.get("action") or "")
        _txt = str(_it.get("text") or "")
        if _slot.get("kind") == "voice_kind":
            _rc = _slot.get("reclassify") if isinstance(_slot.get("reclassify"), dict) else {}
            _old = str(_rc.get("uid") or "")
            _from = str(_rc.get("from") or "")
            _to = str(_rc.get("to") or "")
            if _act != ACT_RECLASSIFY or _from not in ("dialogue", "narration") \
                    or _to not in ("dialogue", "narration") or _from == _to:
                _p.append(_rec("repair_voice_reclassify_invalid", _sid,
                               "声音迁移槽的方向/动作不合法", _old))
                continue
            _vk, _vu = _voice_unit_of(view, _old)
            if _vk != _from:
                _p.append(_rec("repair_slot_target_missing", _sid,
                               "槽位指向的声音 %r 不在 %s 集合里" % (_old, _from), _old))
                continue
            _hk = (view or {}).get("hook_bindings") or {}
            _hook_uids = {str(x) for x in (_hk.get("spoken_uids") or [])}
            _hook_car = any(isinstance(_c, dict) and ref_uid(_c) == _old
                            and str(_c.get("kind") or "") == _from
                            for _c in (_hk.get("carriers") or []))
            if _old in _hook_uids or _hook_car:
                _p.append(_rec("repair_voice_hook_bound", _sid,
                               "声音 %r 被 hook 引用，不允许迁移；hook 整块冻结" % _old,
                               _old))
                continue
            _text = str(_vu.get("text") or "")
            if _from == "dialogue" and _to == "narration":
                _span = _novel_span(_text, novel_text)
                if not _span:
                    _p.append(_rec("repair_voice_source_not_found", _sid,
                                   "对白迁旁白要求正文在原文中逐字存在，取不到包含它的"
                                   "连续区间：%r" % _text, _old))
                    continue
                _source = {"text": _span}
            else:
                _source = {}
            _new_uid = str(_rc.get("to_temp_uid") or _voice_temp_uid(_old))
            if _claim_write("声音迁移", _old, _sid,
                            (_from, _to, _text)) == "conflict":
                continue
            _units_del[_from].append(_old)
            _units_up[_to][_new_uid] = {"uid": _new_uid, "text": _text,
                                        "source": _source}
            for _b in _base_beats:
                _vr = _b.get("voice_ref") or {}
                if ref_uid(_vr) == _old and str(_vr.get("kind") or "") == _from:
                    _nb = copy.deepcopy(_beats_up.get(str(_b.get("uid") or "")) or _b)
                    _nb["voice_ref"] = {"kind": _to, "uid": _new_uid}
                    _beats_up[str(_b.get("uid") or "")] = _nb
            for _fb in (view or {}).get("fact_bindings") or []:
                if not isinstance(_fb, dict):
                    continue
                _cars = _fb.get("carriers") or []
                if not any(isinstance(_c, dict) and ref_uid(_c) == _old
                           and str(_c.get("kind") or "") == _from for _c in _cars):
                    continue
                _fu = str(_fb.get("fact_uid") or "")
                _b = _binding(_fu)
                _b["carriers"] = [
                    ({"kind": _to, "uid": _new_uid}
                     if isinstance(_c, dict) and ref_uid(_c) == _old
                     and str(_c.get("kind") or "") == _from else _c)
                    for _c in (_b.get("carriers") or [])]
            _voice_order_changed = True
            continue
        if _slot.get("kind") == "card_text":
            _cid = str(_slot.get("card_id") or "")
            _card = _cards.get(_cid)
            if _card is None:
                _p.append(_rec("repair_slot_target_missing", _sid,
                               "槽位指向的卡片 %r 不在基线视图里" % _cid, _cid))
                continue
            if _claim_write("卡片", _cid, _sid, _txt) == "conflict":
                continue
            _new = copy.deepcopy(_card)
            _new["text"] = _txt
            _cards_up[_cid] = _new
            continue
        _fu = str(_slot.get("fact_uid") or "")
        if _act == ACT_BIND_VOICE:
            _tgt = str(_it.get("target_uid") or "")
            _vk, _ = _voice_unit_of(view, _tgt)
            if _vk is None:
                _p.append(_rec("repair_slot_target_missing", _sid,
                               "槽位选择的声音 %r 不在基线视图里" % _tgt, _tgt))
                continue
            if not _fu:
                continue
            if _claim_write("声音绑定", _fu, _sid, _tgt) == "conflict":
                continue
            _binding(_fu)["carriers"] = [{"kind": _vk, "uid": _tgt}]
            continue
        _beat = None
        if _act == ACT_CREATE:
            _uid = "new:visual:%s" % _slug(_sid or _fu)
            _ei = int(_it.get("event_index") or 0)
            _ev = [_events[_ei - 1]] if 1 <= _ei <= len(_events) else []
            _beat = copy.deepcopy(_template)
            _beat.update({"uid": _uid, "text": _txt,
                          "purpose": str(_slot.get("claim") or ""),
                          "evidence": _pick_evidence(
                              _slot, (evidence_by_fact or {}).get(_fu), novel_text,
                              view=view, fact_uid=_fu),
                          "event_uids": _ev, "source": {},
                          "voice_ref": {"kind": KIND_VISUAL, "beat_uid": _uid}})
            if _claim_write("画面", _uid, _sid, _txt) == "conflict":
                continue
            _beats_up[_uid] = _beat
            # **同锚点的相对顺序由回答顺序定**（复核 2026-10-08 P2）：旧写法每来一个
            # 就 `insert(pos+1)`，后到的反而排在前面——同一锚点插两个画面时**编译期
            # 制造了事件回退**（真实应用器 `view_event_order_reversed`）。这里只登记，
            # 最后按回答顺序**一次性**生成最终 order。
            _new_at.setdefault(str(_it.get("insert_after_uid") or ""), []).append(_uid)
            _order_changed = True
        else:
            _tgt = str(_it.get("target_uid") or "")
            _src = next((_b for _b in _base_beats if str(_b.get("uid") or "") == _tgt), None)
            if _src is None:
                _p.append(_rec("repair_slot_target_missing", _sid,
                               "槽位选择的目标画面 %r 不在基线视图里" % _tgt, _tgt))
                continue
            if _act == ACT_REWRITE:
                if _claim_write("画面", _tgt, _sid, _txt) == "conflict":
                    continue
                _beat = copy.deepcopy(_beats_up.get(_tgt) or _src)
                _beat["text"] = _txt
                _beats_up[_tgt] = _beat
            else:
                _beat = _src
        if _beat is None or not _fu:
            continue
        _b = _binding(_fu)
        if fact_binding_policy(view)['selection']=='replace':
            _b['carriers']=[{'kind':KIND_VISUAL,'beat_uid':str(_beat.get('uid') or '')}]
            continue
        _exists = any(isinstance(_c, dict) and str(_c.get("kind") or "") == KIND_VISUAL
                      and str(_c.get("beat_uid") or _c.get("uid") or "")
                      == str(_beat.get("uid") or "")
                      for _c in (_b.get("carriers") or []))
        if not _exists:
            _b.setdefault("carriers", []).append(
                {"kind": KIND_VISUAL, "beat_uid": str(_beat.get("uid") or "")})
    if _p:
        return None, _p
    if _order_changed:
        # 一次生成最终顺序：原顺序展开，新画面插到各自锚点之后（同锚点按回答顺序）
        _final: list = []
        for _u in _ids:
            _final.append(_u)
            _final.extend(_new_at.get(_u, []))
        _final.extend(_new_at.get("", []))          # 空串锚点 = 排在最后
        _order = _final
    _final_d: list = []
    _final_n: list = []
    if _voice_order_changed:
        _new_beats = {str(b.get("uid") or ""): b for b in _base_beats}
        _new_beats.update({str(b.get("uid") or ""): b for b in _beats_up.values()
                           if isinstance(b, dict)})
        _pos = {u: i for i, u in enumerate(_order)}

        def _host_pos(_u):
            _unit_uid = str(_u.get("uid") or "")
            _best = None
            for _b in _new_beats.values():
                if ref_uid(_b.get("voice_ref")) == _unit_uid:
                    _i = _pos.get(str(_b.get("uid") or ""), len(_pos))
                    if _best is None or _i < _best:
                        _best = _i
            return _best if _best is not None else len(_pos)

        def _final_units(_kind):
            _base = [copy.deepcopy(u) for u in (view.get(_kind + "_units") or [])
                     if isinstance(u, dict)]
            _del = set(_units_del[_kind])
            _after = [u for u in _base if str(u.get("uid") or "") not in _del]
            _after.extend(copy.deepcopy(u) for u in _units_up[_kind].values())
            return [u for _, u in sorted(
                enumerate(_after), key=lambda p: (_host_pos(p[1]), p[0]))]

        _final_d = _final_units("dialogue")
        _final_n = _final_units("narration")
    _delta = {"schema": DELTA_SCHEMA, "rule_version": DELTA_RULE_VERSION,
              "base_plan_hash": view_digest(view),
              "fact_basis_hash": fact_basis_digest(view)}
    _ups: dict = {}
    if _beats_up:
        _ups["beats"] = list(_beats_up.values())
    if _cards_up:
        _ups["screen_cards"] = list(_cards_up.values())
    for _k in ("dialogue", "narration"):
        if _units_up[_k]:
            _ups[_k + "_units"] = list(_units_up[_k].values())
    if _ups:
        _delta["upsert"] = _ups
    _del_out: dict = {}
    for _k in ("dialogue", "narration"):
        if _units_del[_k]:
            _del_out[_k + "_units"] = list(_units_del[_k])
    if _del_out:
        _delta["delete"] = _del_out
    _ord: dict = {}
    if _order_changed:
        _ord["beats"] = _order
    if _voice_order_changed:
        _ord["dialogue_units"] = [str(u.get("uid") or "") for u in _final_d]
        _ord["narration_units"] = [str(u.get("uid") or "") for u in _final_n]
    if _ord:
        _delta["order"] = _ord
    if _bindings:
        _delta["fact_bindings"] = list(_bindings.values())
    return _delta, []


__all__ = ["ACTIONS", "ACT_BIND", "ACT_BIND_VOICE", "ACT_CREATE", "ACT_NARROW_CARD",
           "ACT_RECLASSIFY", "ACT_REWRITE",
           "ANSWER_KEYS", "REPAIR_RULE_VERSION", "SLOT_ANSWER_SCHEMA",
           "SLOT_CODE_KIND", "SLOT_REQUEST_SCHEMA", "answer_problems", "compile_delta",
           "answer_json_schema", "content_slots", "record_uid", "slot_prompt",
           "slot_request"]


#: 字段类型 → 结构化声明里的类型名（**同一份 `_FIELD_TYPE`**，不另写一套）
_SCHEMA_TYPE = {"str": "string", "int": "integer"}


def _field_schema(field: str, action: str) -> dict:
    """一个字段在**某个动作分支**里的结构化声明（类型 + 允许集合）.

    `bind_visual` 的 `text` 收成 `enum [""]`——它与本地准入**同一条判据**（见
    `answer_problems` 的 `_act == ACT_BIND and _txt != ""`）。**没有 `null` 分支**：
    这些字段都没有"给 null = 不改"的语义，要表达"不改"就不给这个字段/按规则给空串。
    """
    if field == "text" and action in (ACT_BIND, ACT_BIND_VOICE):
        return {"type": "string", "enum": [""]}
    return {"type": _SCHEMA_TYPE.get(_FIELD_TYPE.get(field, "str"), "string")}


def answer_json_schema(slots) -> dict:
    """回答的**结构化契约**（随请求发下去）——**与本地准入同源**（复核 2026-10-08 P2）.

    旧写法把 schema 手写死（所有动作都只 `required: ["slot_id","action"]`）：`create_visual`
    少给 `text`/`insert_after_uid`/`event_index` 时——**schema 说合法、本地判三条缺失**。
    模型照 schema 就漏字段，真按 schema 约束的接口还会把不完整回答判成"合法形状"。

    现在**动作分支由 `ACTION_FIELDS` 派生**：每个"本轮真的有槽位允许"的动作一个分支，
    分支内的 `required` / 字段类型 / 允许集合 / 禁用字段（`additionalProperties: false`
    之下**不出现的键**就是禁用键）都来自同一份契约；分支的 `slot_id` 只列**允许该动作**
    的那些槽（不把动作集合放宽到别的槽）。

    ⚠️ **两处诚实的表达边界**（不假装 schema 能表达）：

    ① **取值集合是逐槽的**：每个槽的 `target_uids` / `insert_positions` / 事件数量各不
       相同，一个动作分支只能声明"哪些槽允许这个动作 + 字段类型"；具体取值仍由本地准入
       判（`repair_answer_target_not_allowed` / `..._position_not_allowed` /
       `..._event_index_invalid`）。
    ② **本通道不下发它**：`_text_gen/agent_plan_provider.py` 恒发 `json_object`、
       `json_schema` 只用于调用前记账（`structured_output_note`）。所以形状约束**实际由
       本地准入兜底**——证据里记"未下发"，不把 HTTP 200 当成"约束已生效"。
    """
    _branches = []
    for _act in ACTIONS:
        _ids = [str(_s.get("slot_id")) for _s in (slots or [])
                if _act in (_s.get("allowed_actions") or [])]
        if not _ids:
            continue
        _spec = ACTION_FIELDS.get(_act) or {}
        _props = {"slot_id": {"type": "string", "enum": _ids},
                  "action": {"type": "string", "enum": [_act]}}
        _req = ["slot_id", "action"]
        for _f in (_spec.get("required") or ()):
            _props[_f] = _field_schema(_f, _act)
            if _f not in _req:
                _req.append(_f)
        for _f in ANSWER_KEYS:
            if _f in _props or _f in ("slot_id", "action"):
                continue
            if _f in (_spec.get("forbidden") or ()):
                continue
            _props[_f] = _field_schema(_f, _act)     # 允许但非必填（bind 的 text）
        _branches.append({"type": "object", "properties": _props, "required": _req,
                          "additionalProperties": False})
    _item = {"anyOf": _branches} if _branches else {"type": "object"}
    return {"name": "plan_content_repair_answer", "schema": {
        "type": "object",
        "properties": {"schema": {"type": "string", "enum": [SLOT_ANSWER_SCHEMA]},
                       "answers": {"type": "array", "items": _item}},
        "required": ["schema", "answers"],
        "additionalProperties": False}}
