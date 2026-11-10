# -*- coding: utf-8 -*-
"""R3-1/R3-4：修订路由、字段白名单与非目标 core 冻结.

纯函数，不调模型。调用方（`outline.revise_plan_once` / `source_revision`）
消费同一份裁决，避免"问题里有 carrier、却仍走 source_basis"的错选。
"""
from __future__ import annotations

VERSION = "revision_route.v1"

ROUTE_CARRIER = "carrier_ops"
ROUTE_SOURCE = "source_basis_revision"
ROUTE_DEFAULT = "default"

#: carrier/ops 可改的字段（承载关系、节拍、声音；不得改主张/证据）
CARRIER_FIELDS = (
    "fact_bindings", "hook_bindings", "beats",
    "narration_units", "dialogue_units", "order",
)
#: source_basis_revision 只允许改事实的主张/证据；不得改承载/节拍/声音/core
SOURCE_FIELDS = ("required_facts",)

#: 修订中默认冻结的主线摘要字段（确需改动必须给依赖原因并单独留痕）
FROZEN_CORE_FIELDS = ("main_events", "climax", "end_hook")
ALL_CORE_FIELDS = ("main_events", "climax", "causal_chain", "end_hook",
                   "character_motives")

_CARRIER_PREFIXES = (
    "carrier_", "orphan_", "voice_", "view_voice", "view_binding",
    "beat_carrier", "presentation_carrier",
)
_SOURCE_CODES = {"fact_basis_invalid", "source_preservation_failed"}


def _code(p):
    return str((p or {}).get("code") or "") if isinstance(p, dict) else ""


def route_decision(problems) -> dict:
    """按问题集选通道；carrier 类优先于 source 类（R2-6 的错选根因）."""
    codes = [c for c in (_code(p) for p in (problems or [])) if c]
    carrier = [c for c in codes if c.startswith(_CARRIER_PREFIXES)]
    source = [c for c in codes if c in _SOURCE_CODES]
    if carrier:
        return {
            "version": VERSION,
            "route": ROUTE_CARRIER,
            "input_codes": codes,
            "carrier_codes": carrier,
            "source_codes": source,
            "deferred_source_codes": source,
            "allowed_fields": list(CARRIER_FIELDS),
            "reason": ("问题集含 carrier/orphan/voice 类（%s）⇒ 先修承载绑定与节拍；"
                       "source 类（%s）留到承载闭合后再处理"
                       % ("、".join(carrier), "、".join(source) or "无")),
        }
    if source:
        return {
            "version": VERSION,
            "route": ROUTE_SOURCE,
            "input_codes": codes,
            "carrier_codes": [],
            "source_codes": source,
            "deferred_source_codes": [],
            "allowed_fields": list(SOURCE_FIELDS),
            "reason": ("问题集只有来源证据类（%s）⇒ source_basis_revision；"
                       "不得改承载/节拍/声音" % "、".join(source)),
        }
    return {
        "version": VERSION,
        "route": ROUTE_DEFAULT,
        "input_codes": codes,
        "carrier_codes": [],
        "source_codes": [],
        "deferred_source_codes": [],
        "allowed_fields": None,
        "reason": "无 carrier/source 专属问题，交既有 _replan_route",
    }


def _add(fields, name):
    if name:
        fields.add(str(name))


def delta_touched_fields(delta) -> list:
    """从 delta 顶层/upsert/delete/order 提取实际触碰的字段名."""
    if not isinstance(delta, dict):
        return []
    out = set()
    for key in ("fact_bindings", "hook_bindings", "required_facts", "core_updates",
                "main_events", "climax", "end_hook", "causal_chain",
                "character_motives", "evidence_updates"):
        if key in delta:
            _add(out, key)
    for block in ("upsert", "delete", "order"):
        val = delta.get(block)
        if isinstance(val, dict):
            for key in val:
                _add(out, key)
    # `upsert`/`delete` 里的单数/复数别名统一到白名单名
    alias = {"fact_binding": "fact_bindings", "beat": "beats",
             "narration": "narration_units", "narration_unit": "narration_units",
             "dialogue": "dialogue_units", "dialogue_unit": "dialogue_units",
             "hook_binding": "hook_bindings", "required_fact": "required_facts"}
    return sorted({alias.get(x, x) for x in out})


def delta_field_violations(delta, allowed_fields) -> list:
    """跨通道字段修改：不在白名单内的字段逐条拒绝."""
    if allowed_fields is None:
        return []
    allowed = set(str(x) for x in allowed_fields)
    probs = []
    for field in delta_touched_fields(delta):
        if field in allowed:
            continue
        probs.append({
            "code": "route_field_violation", "kind": "revision",
            "path": field,
            "reason": "本轮通道不允许修改 %s（允许：%s）"
                      % (field, "、".join(sorted(allowed)) or "无"),
            "fix": "改回本轮通道允许的字段；跨通道修改会被拒收",
        })
    return probs


def core_update_problems(core_updates, allowed=None) -> list:
    """core 字段默认冻结；`allowed` 为 {field: reason} 时才放行并留痕."""
    if not core_updates:
        return []
    if not isinstance(core_updates, dict):
        return [{"code": "core_updates_not_object", "kind": "revision",
                 "path": "core_updates", "reason": "core_updates 必须是对象",
                 "fix": "不改就写 {}；确需改动按 allowed_core_updates 显式授权"}]
    allowed = allowed if isinstance(allowed, dict) else {}
    probs = []
    for field in core_updates:
        if field not in ALL_CORE_FIELDS:
            probs.append({
                "code": "core_field_unknown", "kind": "revision",
                "path": "core_updates.%s" % field,
                "reason": "未知 core 字段 %s" % field,
                "fix": "只允许 %s" % "、".join(ALL_CORE_FIELDS)})
            continue
        reason = str(allowed.get(field) or "").strip()
        if not reason:
            probs.append({
                "code": "core_field_frozen", "kind": "revision",
                "path": "core_updates.%s" % field,
                "reason": ("主线摘要 %s 在修订中默认冻结；没有依赖原因与显式授权，"
                           "不能借修订改写" % field),
                "fix": ("如确需改动，由调用方在 allowed_core_updates 给出 %s 的依赖"
                        "原因并单独留痕；不能由模型自行改" % field)})
    return probs


def core_update_trace(core_updates, allowed=None) -> dict:
    """放行后的留痕：字段 → 依赖原因；未放行的字段不写."""
    allowed = allowed if isinstance(allowed, dict) else {}
    return {str(k): str(allowed.get(k) or "")
            for k in (core_updates or {})
            if str(allowed.get(k) or "").strip()}


__all__ = [
    "VERSION", "ROUTE_CARRIER", "ROUTE_SOURCE", "ROUTE_DEFAULT",
    "CARRIER_FIELDS", "SOURCE_FIELDS", "FROZEN_CORE_FIELDS", "ALL_CORE_FIELDS",
    "route_decision", "delta_touched_fields", "delta_field_violations",
    "core_update_problems", "core_update_trace",
]
