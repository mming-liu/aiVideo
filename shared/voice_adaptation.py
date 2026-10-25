# -*- coding: utf-8 -*-
"""显式批准的**配音/旁白改编层**。

设计约束（工作包 `adaptation_offline_spec.md` v2.1）：

- 计划里的 `dialogue_lines` / `narration_lines` 永远是**原文基底**；
- `voice_adaptations.entries` 保存实际播出的改编文本、完整出处与批准信息；
- 所有制作消费者读 `broadcast_*`；基底读取器 `base_*` 只允许来源/修订/计数入口，
  且必须显式 `allow_adapted_base=True`；
- 批准固定保存 `base_plan_digest` 与 `entries_digest`；任何编辑/投影只搬运，不重新批准。

本模块只做纯函数与不可变校验；不写文件、不调模型。
"""
from __future__ import annotations

import hashlib
import json
import copy
from datetime import datetime

SCHEMA = "voice_adaptation.v1"

NON_CONTENT_KEYS = frozenset({"plan_verification", "verification_rejected",
                              "plan_revision", "plan_replan",
                              "plan_contract_version"})


class AdaptationInvalidError(RuntimeError):
    """改编块存在但不可消费（开关关、批准失效、条目/出处/摘要不一致）."""

    def __init__(self, problems):
        self.problems = list(problems or [])
        super().__init__("voice adaptation invalid: %s"
                         % (self.problems[0].get("code") if self.problems else "unknown"))


class AdaptationConsumerError(RuntimeError):
    """生产消费者读取了基底文本，但该计划带改编且未显式允许."""


class AdaptationConfigError(RuntimeError):
    """开关配置不可读：不静默当作关闭/开启."""


def enabled() -> bool:
    """唯一来源：`shared.config.VOICE_ADAPTATION_ENABLED`（由现有 env 加载一次）."""
    try:
        from shared import config as _config
        _flag = getattr(_config, "VOICE_ADAPTATION_ENABLED", None)
    except Exception as exc:
        raise AdaptationConfigError("无法读取 shared.config.VOICE_ADAPTATION_ENABLED: %s"
                                    % exc) from exc
    if not isinstance(_flag, bool):
        raise AdaptationConfigError("shared.config.VOICE_ADAPTATION_ENABLED 不是布尔值")
    return _flag


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def entries_digest(entries) -> str:
    return hashlib.sha256(_canon(list(entries or [])).encode("utf-8")).hexdigest()


def base_plan_digest(plan) -> str:
    """去掉改编块与核验元数据后的计划摘要（批准时固定）."""
    if not isinstance(plan, dict):
        return ""
    body = {k: v for k, v in plan.items()
            if k != "voice_adaptations" and k not in NON_CONTENT_KEYS}
    return hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()


def _dialogues(plan) -> list:
    values = (plan or {}).get("dialogue_lines")
    if not isinstance(values, list):
        return []
    out = []
    for x in values:
        s = x if isinstance(x, str) else ""
        if s.strip():
            out.append(s)
    return out


def _narrations(plan) -> list:
    values = (plan or {}).get("narration_lines")
    if not isinstance(values, list):
        return []
    out = []
    for x in values:
        if not isinstance(x, dict):
            continue
        t = str(x.get("text") or "")
        if t.strip():
            out.append({"text": t, "source": str(x.get("source") or "")})
    return out


def _uid(kind: str, text: str, occurrence: int) -> str:
    h = hashlib.sha256(("%s\x00%s\x00%d" % (kind, text, occurrence)).encode("utf-8"))
    return "v-" + h.hexdigest()[:12]


def _base_units(plan) -> list:
    units = []
    seen: dict = {}
    for kind, items in (("dialogue", _dialogues(plan)),
                        ("narration", _narrations(plan))):
        for i, item in enumerate(items, 1):
            text = item if isinstance(item, str) else str(item.get("text") or "")
            source = "" if isinstance(item, str) else str(item.get("source") or "")
            key = (kind, text)
            seen[key] = seen.get(key, 0) + 1
            units.append({"kind": kind, "index": i, "base_uid": _uid(kind, text, seen[key]),
                          "base_text": text, "source": source})
    return units


def _problem(code, path, reason, *, kind="plan", index=None):
    return {"code": str(code), "kind": kind, "index": index, "path": path,
            "reason": str(reason),
            "basis": "改编层：基底不动、批准固定、所有制作消费者读播出文本"}


def _novel_norm(text) -> str:
    # 与 plan_feasibility._source_norm 同口径；惰性导入避免循环。
    from step01_script_gen.plan_feasibility import _source_norm
    return _source_norm(str(text or ""))


def adaptation_problems(plan, novel_text: str = "") -> list:
    """返回结构化问题；无问题返回 `[]`。调用方不得静默忽略."""
    if not isinstance(plan, dict):
        return []
    block = plan.get("voice_adaptations")
    if block is None:
        return []
    if not enabled():
        return [_problem("adaptation_disabled", "voice_adaptations",
                         "计划带改编块，但 VOICE_ADAPTATION_ENABLED 未开启")]
    if not isinstance(block, dict):
        return [_problem("adaptation_block_type", "voice_adaptations", "改编块必须是对象")]
    if str(block.get("schema") or "") != SCHEMA:
        return [_problem("adaptation_schema_unknown", "voice_adaptations.schema",
                         "未知改编协议：%s" % block.get("schema"))]
    approval = block.get("approval")
    if not isinstance(approval, dict) or approval.get("approved") is not True:
        return [_problem("adaptation_not_approved", "voice_adaptations.approval",
                         "改编块没有有效批准")]
    if str(approval.get("scope") or "") != "candidate":
        return [_problem("adaptation_scope_invalid", "voice_adaptations.approval.scope",
                         "批准范围必须是 candidate（不接受 all/plan 等宽范围）")]
    if not str(approval.get("approved_by") or "").strip():
        return [_problem("adaptation_approver_missing", "voice_adaptations.approval.approved_by",
                         "批准人字段缺失或为空")]
    _at = str(approval.get("approved_at") or "").strip()
    try:
        _dt = datetime.fromisoformat(_at.replace("Z", "+00:00"))
        if _dt.tzinfo is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError):
        return [_problem("adaptation_approved_at_invalid",
                         "voice_adaptations.approval.approved_at",
                         "批准时间必须是带时区的 ISO 8601")]
    pending = base_plan_digest(plan)
    fixed_base = str(approval.get("base_plan_digest") or "")
    if not fixed_base or pending != fixed_base:
        return [_problem("adaptation_approval_stale", "voice_adaptations.approval.base_plan_digest",
                         "基底已变化：批准失效，禁止解析旧绑定")]
    entries = block.get("entries")
    if not isinstance(entries, list):
        return [_problem("adaptation_entries_type", "voice_adaptations.entries",
                         "entries 必须是数组")]
    actual = entries_digest(entries)
    if str(block.get("digest") or "") != actual:
        return [_problem("adaptation_digest_mismatch", "voice_adaptations.digest",
                         "entries 与当前摘要不一致（entries 已改、摘要未改）")]
    if str(approval.get("entries_digest") or "") != actual:
        return [_problem("adaptation_entries_unapproved",
                         "voice_adaptations.approval.entries_digest",
                         "entries 已改但批准摘要未更新（重算 digest 不等于重新批准）")]

    base = {u["base_uid"]: u for u in _base_units(plan)}
    novel_norm = _novel_norm(novel_text) if novel_text else ""
    used: set = set()
    out: list = []
    for i, entry in enumerate(entries):
        path = "voice_adaptations.entries[%d]" % i
        if not isinstance(entry, dict):
            out.append(_problem("adaptation_entry_type", path, "条目必须是对象",
                                kind="voice", index=i))
            continue
        uid = str(entry.get("base_uid") or "")
        unit = base.get(uid)
        if unit is None:
            out.append(_problem("adaptation_base_uid_missing", path + ".base_uid",
                                "base_uid 不在冻结基底中（删除/改写后不得套旧绑定）",
                                kind="voice", index=i))
            continue
        if uid in used:
            out.append(_problem("adaptation_duplicate_base", path + ".base_uid",
                                "同一基底行被重复改编", kind="voice", index=i))
            continue
        used.add(uid)
        if str(entry.get("kind") or "") != unit["kind"]:
            out.append(_problem("adaptation_kind_mismatch", path + ".kind",
                                "kind 与基底行不符", kind="voice", index=i))
            continue
        if str(entry.get("base_text") or "") != unit["base_text"]:
            out.append(_problem("adaptation_base_text_mismatch", path + ".base_text",
                                "base_text 与基底行不逐字相等", kind="voice", index=i))
            continue
        source = str(entry.get("source") or "")
        text = str(entry.get("text") or "")
        if not text.strip():
            out.append(_problem("adaptation_text_empty", path + ".text",
                                "播出文本为空", kind="voice", index=i))
            continue
        if unit["kind"] == "dialogue" and not str(entry.get("speaker") or "").strip():
            out.append(_problem("adaptation_speaker_missing", path + ".speaker",
                                "对白改编必须保留说话人", kind="voice", index=i))
        if novel_norm:
            if not source.strip() or _novel_norm(source) not in novel_norm:
                out.append(_problem("adaptation_source_not_in_novel", path + ".source",
                                    "出处不在原文中逐字可查", kind="voice", index=i))
            elif _novel_norm(unit["base_text"]) not in _novel_norm(source):
                out.append(_problem("adaptation_source_not_cover_base", path + ".source",
                                    "出处未覆盖基底行", kind="voice", index=i))
    return out


def _assert_consumable(plan, novel_text: str = "") -> None:
    problems = adaptation_problems(plan, novel_text)
    if problems:
        raise AdaptationInvalidError(problems)


def _entry_by_uid(plan) -> dict:
    block = plan.get("voice_adaptations") or {}
    return {str(e.get("base_uid")): e for e in (block.get("entries") or [])
            if isinstance(e, dict)}


def broadcast_dialogue_lines(plan, novel_text: str = "") -> list:
    _assert_consumable(plan, novel_text)
    base = _dialogues(plan)
    by_uid = _entry_by_uid(plan)
    out = []
    for unit in _base_units(plan):
        if unit["kind"] != "dialogue":
            continue
        entry = by_uid.get(unit["base_uid"])
        out.append(str(entry.get("text")) if entry else unit["base_text"])
    return out


def broadcast_narration_entries(plan, novel_text: str = "") -> list:
    _assert_consumable(plan, novel_text)
    base_entries = [x for x in ((plan or {}).get("narration_lines") or [])
                    if isinstance(x, dict) and str(x.get("text") or "").strip()]
    by_uid = _entry_by_uid(plan)
    units = [u for u in _base_units(plan) if u["kind"] == "narration"]
    out = []
    for base, unit in zip(base_entries, units):
        entry = by_uid.get(unit["base_uid"])
        out.append({"text": str(entry.get("text")) if entry else base["text"],
                    "source": str(entry.get("source")) if entry else str(base.get("source") or ""),
                    "fact_refs": list(base.get("fact_refs") or [])})
    return out


def base_dialogue_lines(plan, *, allow_adapted_base: bool = False) -> list:
    if isinstance(plan, dict) and plan.get("voice_adaptations") and not allow_adapted_base:
        raise AdaptationConsumerError(
            "base_dialogue_lines called for a plan with voice_adaptations; "
            "use broadcast_dialogue_lines")
    return _dialogues(plan)


def base_narration_entries(plan, *, allow_adapted_base: bool = False) -> list:
    if isinstance(plan, dict) and plan.get("voice_adaptations") and not allow_adapted_base:
        raise AdaptationConsumerError(
            "base_narration_entries called for a plan with voice_adaptations; "
            "use broadcast_narration_entries")
    return _narrations(plan)


def decorate_view(view: dict, plan: dict, novel_text: str = "") -> list:
    """把改编块与播出文本写入视图 unit；返回问题（非空表示不可消费）."""
    if not isinstance(view, dict) or not isinstance(plan, dict):
        return []
    block = plan.get("voice_adaptations")
    if not block:
        return []
    problems = adaptation_problems(plan, novel_text)
    view["voice_adaptations"] = copy.deepcopy(block)
    if problems:
        view["adaptation_problems"] = problems
        return problems
    by_uid = _entry_by_uid(plan)
    # 基底身份按计划序列计算，再按数组位置映射到视图 unit（1:1）。
    base_units = _base_units(plan)
    narr = [u for u in base_units if u["kind"] == "narration"]
    dlg = [u for u in base_units if u["kind"] == "dialogue"]
    for units, subset in ((view.get("dialogue_units") or [], dlg),
                          (view.get("narration_units") or [], narr)):
        for unit, base in zip(units, subset):
            entry = by_uid.get(base["base_uid"])
            unit["base_text"] = base["base_text"]
            if entry:
                unit["text"] = str(entry.get("text"))
                unit["adapted"] = True
                unit["adaptation"] = {
                    "base_uid": base["base_uid"],
                    "reason": str(entry.get("reason") or ""),
                    "speaker": str(entry.get("speaker") or ""),
                }
    return []


def view_text_mismatch(view: dict) -> list:
    """plan_from_view 前核：unit.text 必须与对应改编条目逐字一致."""
    block = (view or {}).get("voice_adaptations")
    if not block:
        return []
    entries = block.get("entries") or []
    by_uid = {str(e.get("base_uid")): e for e in entries if isinstance(e, dict)}
    out = []
    for key in ("dialogue_units", "narration_units"):
        for unit in (view.get(key) or []):
            if not unit.get("adapted"):
                continue
            uid = str((unit.get("adaptation") or {}).get("base_uid") or "")
            entry = by_uid.get(uid)
            if entry is None:
                out.append(_problem("adaptation_view_text_mismatch", key,
                                    "unit 标记为改编但找不到条目", kind="voice"))
                continue
            if str(unit.get("text") or "") != str(entry.get("text") or ""):
                out.append(_problem("adaptation_view_text_mismatch", key,
                                    "unit.text 与改编条目 text 不一致", kind="voice"))
    return out
