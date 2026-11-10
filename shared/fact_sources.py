# -*- coding: utf-8 -*-
"""必保留事实的**来源分级合同**（R2-1；纯函数，无模型/IO）.

设计边界（2026-11-10 R2 计划）：

- 只建合同与证据，不替用户拍板。旧计划默认 `unknown_legacy`，不自动升级为硬门，
  也不静默降级为 advisory；
- 来源元数据随计划走：legacy 用顶层 `fact_sources`（与 `required_facts` / `fact_uids`
  等长的并行表），视图层把它挂在 `frozen_facts[].source` 上，投影回计划时按事实顺序
  写回；`plan_basis` 不复制它，避免修订链里出现两份会漂移的副本；
- 现有硬门（事实正文冻结、逐字来源、承载互指、内容核验 partial）**不因本模块改变**；
  `source_problems` 是独立的复核入口，R2-6 再决定是否接入主门。

来源类别：
  `user_specified`  用户明确指定 —— 硬门；
  `core_event`      原文核心事件/关键因果 —— 硬门，须给核心性依据；
  `model_background` 模型自选背景补充 —— advisory/评阅；
  `unknown_legacy`  旧计划来源不明 —— 显式复核，不自动降级/硬保留。
"""
from __future__ import annotations

from shared.fact_metadata import split_claim_evidence

VERSION = "fact_sources.v1"
ADJUDICATED_BY_USER = "user"

SOURCE_USER = "user_specified"
SOURCE_CORE = "core_event"
SOURCE_MODEL_BACKGROUND = "model_background"
SOURCE_UNKNOWN = "unknown_legacy"
SOURCES = (SOURCE_USER, SOURCE_CORE, SOURCE_MODEL_BACKGROUND, SOURCE_UNKNOWN)

LEVEL_HARD = "hard"
LEVEL_ADVISORY = "advisory"
LEVEL_UNKNOWN = "unknown_legacy"
LEVELS = (LEVEL_HARD, LEVEL_ADVISORY, LEVEL_UNKNOWN)

DEFAULT_LEVEL = {
    SOURCE_USER: LEVEL_HARD,
    SOURCE_CORE: LEVEL_HARD,
    SOURCE_MODEL_BACKGROUND: LEVEL_ADVISORY,
    SOURCE_UNKNOWN: LEVEL_UNKNOWN,
}

LEGAL_FIX = {
    LEVEL_HARD: "不得删除或降级；只能改承载/拆分为原子事实/补原文证据",
    LEVEL_ADVISORY: "可省略；省略时须记录理由，不得把核心结果一起删掉",
    LEVEL_UNKNOWN: "先人工复核来源；不得自行降级为 advisory，也不得无依据硬保留",
}


def _facts(plan) -> list:
    return [str(f) for f in ((plan or {}).get("required_facts") or [])]


def _uids(plan, n: int) -> list:
    _raw = (plan or {}).get("fact_uids")
    if isinstance(_raw, list) and len(_raw) == n and all(str(x).strip() for x in _raw):
        return [str(x) for x in _raw]
    return ["f%02d" % (i + 1) for i in range(n)]


def _raw_by_uid(plan) -> dict:
    raw = (plan or {}).get("fact_sources")
    out: dict = {}
    if isinstance(raw, list):
        for i, rec in enumerate(raw, 1):
            if isinstance(rec, dict):
                key = str(rec.get("uid") or rec.get("fact_uid") or "f%02d" % i)
                out[key] = dict(rec)
    elif isinstance(raw, dict):
        for key, rec in raw.items():
            if isinstance(rec, dict):
                out[str(key)] = dict(rec)
    return out


def _omittable_hit(claim: str, evidence: str, omittable) -> bool:
    _claim = str(claim or "").strip()
    _ev = str(evidence or "").strip()
    for item in (omittable or []):
        _t = str(item or "").strip()
        if not _t:
            continue
        if _t in _claim or _claim in _t or _t in _ev or _ev in _t:
            return True
    return False


def source_records(plan) -> list:
    """按事实顺序返回来源记录（缺省 `unknown_legacy`，不猜）."""
    facts = _facts(plan)
    uids = _uids(plan, len(facts))
    raw = _raw_by_uid(plan)
    omittable = list((plan or {}).get("omittable") or [])
    out = []
    for i, fact in enumerate(facts):
        uid = uids[i]
        claim, evidence = split_claim_evidence(fact)
        rec = raw.get(uid) or {}
        source = str(rec.get("source") or SOURCE_UNKNOWN)
        if source not in SOURCES:
            source = SOURCE_UNKNOWN
        level = str(rec.get("level") or DEFAULT_LEVEL[source])
        if level not in LEVELS:
            level = LEVEL_UNKNOWN
        out.append({
            "uid": uid,
            "index": i + 1,
            "claim": claim,
            "evidence": evidence,
            "source": source,
            "level": level,
            "basis": str(rec.get("basis") or ""),
            "adjudicated_by": str(rec.get("adjudicated_by") or ""),
            "omittable": _omittable_hit(claim, evidence, omittable),
            "consumer": "outline.plan_contract_records → content_verification → "
                        "source_revision / split / downstream",
            "legal_fix": LEGAL_FIX.get(level, LEGAL_FIX[LEVEL_UNKNOWN]),
            "declared": bool(rec),
        })
    return out


def normalize_sources(plan, records=None):
    """返回**副本**，带与事实等长的 `fact_sources` 表；已有记录按 uid 保留。"""
    _plan = dict(plan or {})
    facts = _facts(_plan)
    uids = _uids(_plan, len(facts))
    raw = _raw_by_uid(_plan)
    if records is not None:
        for rec in records:
            if isinstance(rec, dict) and str(rec.get("uid") or "").strip():
                raw[str(rec["uid"])] = dict(rec)
    out = []
    for i, uid in enumerate(uids):
        rec = dict(raw.get(uid) or {})
        source = str(rec.get("source") or SOURCE_UNKNOWN)
        if source not in SOURCES:
            source = SOURCE_UNKNOWN
        level = str(rec.get("level") or DEFAULT_LEVEL[source])
        if level not in LEVELS:
            level = LEVEL_UNKNOWN
        rec.update({"uid": uid, "source": source, "level": level})
        out.append(rec)
    _plan["fact_sources"] = out
    return _plan


def adjudicated_records(mapping) -> list:
    """把**显式裁决**（uid → {source,level,basis,adjudicated_by}）转成来源记录.

    这是样本/实验数据的入口，不在通用模块里写死任何作品、事实编号或情节：
    f02a/f02b 的具体等级由调用方在隔离实验清单里给出。
    """
    out = []
    for uid, rec in (mapping or {}).items():
        if not isinstance(rec, dict):
            continue
        out.append({
            "uid": str(uid),
            "source": str(rec.get("source") or SOURCE_UNKNOWN),
            "level": str(rec.get("level") or LEVEL_UNKNOWN),
            "basis": str(rec.get("basis") or ""),
            "adjudicated_by": str(rec.get("adjudicated_by") or ""),
        })
    return out


def adjudication_problems(mapping) -> list:
    """显式裁决本身的合同：模型自选背景不得被偷偷升 hard；用户指定不得被降级."""
    probs = []
    for rec in adjudicated_records(mapping):
        uid = rec["uid"]
        source, level = rec["source"], rec["level"]
        by = rec["adjudicated_by"]
        if source not in SOURCES:
            probs.append({"code": "fact_source_invalid", "kind": "fact",
                          "uid": uid, "path": "adjudication.%s.source" % uid,
                          "reason": "裁决里的来源类别 %r 非法" % source,
                          "fix": "改成四类之一"})
        if level not in LEVELS:
            probs.append({"code": "fact_source_level_invalid", "kind": "fact",
                          "uid": uid, "path": "adjudication.%s.level" % uid,
                          "reason": "裁决里的等级 %r 非法" % level,
                          "fix": "改成 hard/advisory/unknown_legacy"})
        if source == SOURCE_MODEL_BACKGROUND and level == LEVEL_HARD and by != ADJUDICATED_BY_USER:
            probs.append({"code": "fact_source_level_conflict", "kind": "fact",
                          "uid": uid, "path": "adjudication.%s.level" % uid,
                          "reason": "模型自选背景不能自动升为 hard；只有用户显式裁决才可例外",
                          "fix": "保持 advisory，或由用户 adjudicated_by=user 显式升级"})
        if source == SOURCE_USER and level != LEVEL_HARD and by != ADJUDICATED_BY_USER:
            probs.append({"code": "fact_source_level_conflict", "kind": "fact",
                          "uid": uid, "path": "adjudication.%s.level" % uid,
                          "reason": "用户明确指定的事实不能被模型/修订层降级",
                          "fix": "保持 hard；如确需降级，必须由用户显式裁决"})
        if level == LEVEL_HARD and not rec["basis"].strip():
            probs.append({"code": "fact_source_basis_missing", "kind": "fact",
                          "uid": uid, "path": "adjudication.%s.basis" % uid,
                          "reason": "hard 裁决没有给依据", "fix": "补用户指定/核心事件依据"})
    return probs


def apply_adjudication(plan, mapping):
    """把显式裁决合入计划副本；返回 `(plan, problems)`，不改原对象."""
    probs = adjudication_problems(mapping)
    return normalize_sources(plan, records=adjudicated_records(mapping)), probs


def source_problems(plan) -> list:
    """来源合同的**独立复核**记录（不接入主门；R2-6 再裁决是否 blocking）."""
    facts = _facts(plan)
    if not facts:
        return []
    uids = _uids(plan, len(facts))
    raw = (plan or {}).get("fact_sources")
    probs: list = []
    if raw is not None:
        _raw_len = len(raw) if isinstance(raw, (list, dict)) else -1
        if isinstance(raw, list) and _raw_len != len(facts):
            probs.append({
                "code": "fact_sources_length_mismatch", "kind": "plan",
                "index": None, "path": "fact_sources",
                "reason": "`fact_sources` 必须与 `required_facts` 等长（现在 %d / %d）"
                          % (_raw_len, len(facts)),
                "fix": "按事实顺序补齐/删除来源记录；不要按位置猜"})
        if not isinstance(raw, (list, dict)):
            probs.append({
                "code": "fact_sources_type_invalid", "kind": "plan",
                "index": None, "path": "fact_sources",
                "reason": "`fact_sources` 必须是数组（按事实顺序）或对象（按 uid）",
                "fix": "改成数组/对象；不要用字符串占位"})
    for rec in source_records(plan):
        path = "fact_sources[%d]" % (rec["index"] - 1)
        if rec["source"] not in SOURCES:
            probs.append({
                "code": "fact_source_invalid", "kind": "fact", "index": rec["index"],
                "path": path,
                "reason": "F%d 的来源类别 %r 非法（合法：%s）"
                          % (rec["index"], rec["source"], list(SOURCES)),
                "fix": "改成四类之一；不确定就写 unknown_legacy"})
        if rec["level"] == LEVEL_HARD and not rec["basis"].strip():
            probs.append({
                "code": "fact_source_basis_missing", "kind": "fact", "index": rec["index"],
                "path": path,
                "reason": "F%d 标为 hard，但没有给核心性依据（用户指定/原文核心事件）"
                          % rec["index"],
                "fix": "补 basis（谁指定/哪条核心事件/哪段原文证据）；补不出就降为 "
                       "unknown_legacy 复核"})
        if rec["level"] == LEVEL_HARD and not rec["evidence"].strip():
            probs.append({
                "code": "fact_source_evidence_missing", "kind": "fact",
                "index": rec["index"], "path": path,
                "reason": "F%d 标为 hard，但没有原文证据" % rec["index"],
                "fix": "补连续原文证据；补不出就不能当硬门"})
        if rec["level"] == LEVEL_UNKNOWN:
            probs.append({
                "code": "fact_source_unknown", "kind": "fact", "index": rec["index"],
                "path": path,
                "reason": "F%d 来源未分级（%s）；不能静默当作 hard 或 advisory"
                          % (rec["index"], rec["source"]),
                "fix": "人工复核：用户指定/原文核心事件 → hard；模型自选背景 → advisory；"
                       "复核前不得自行降级或硬保留"})
        if (rec["source"] == SOURCE_MODEL_BACKGROUND and rec["level"] == LEVEL_HARD
                and rec.get("adjudicated_by") != ADJUDICATED_BY_USER):
            probs.append({
                "code": "fact_source_level_conflict", "kind": "fact",
                "index": rec["index"], "path": path,
                "reason": "F%d 是模型自选背景，却被标为 hard；只有用户显式裁决才可例外"
                          % rec["index"],
                "fix": "保持 advisory；如用户明确要求 hard，补 adjudicated_by=user"})
        if (rec["source"] == SOURCE_USER and rec["level"] != LEVEL_HARD
                and rec.get("adjudicated_by") != ADJUDICATED_BY_USER):
            probs.append({
                "code": "fact_source_level_conflict", "kind": "fact",
                "index": rec["index"], "path": path,
                "reason": "F%d 是用户明确指定的事实，不能被降级" % rec["index"],
                "fix": "保持 hard；如确需降级，必须由用户显式裁决"})
    return probs


def is_hard(plan, uid) -> bool:
    for rec in source_records(plan):
        if rec["uid"] == str(uid):
            return rec["level"] == LEVEL_HARD
    return False


def must_select(rec) -> bool:
    """hard 是否**必须选入**计划（R3 裁决 A）.

    - 用户明确指定 / 原文核心事件：必须选入且完整展示；
    - 模型自选背景被用户显式升 hard：只约束「选入后不得半句」，不强制选入；
    - unknown_legacy / 非法来源：不据此强制选入（交复核）。
    """
    rec = rec or {}
    source = str(rec.get("source") or "")
    if source in (SOURCE_USER, SOURCE_CORE):
        return str(rec.get("level") or "") == LEVEL_HARD
    return False


def selection_problems(plan, required_records=None) -> list:
    """只对**必须选入**的 hard 事实报「未选入」；模型背景 hard 不报.

    `required_records`：调用方显式给出的来源合同记录（可包含未选入的事实）；
    不传时只检查计划里已选入的记录（因此不会凭空知道缺失的 hard）。
    """
    out = []
    records = (list(required_records) if required_records is not None
               else source_records(plan))
    uids = [str(x) for x in ((plan or {}).get("fact_uids") or [])]
    present = set(uids) if uids else {
        "f%02d" % (i + 1) for i in range(len((plan or {}).get("required_facts") or []))}
    for rec in records:
        if not must_select(rec):
            continue
        if str(rec["uid"]) in present:
            continue
        out.append({
            "code": "hard_fact_not_selected", "kind": "fact",
            "uid": rec["uid"], "path": "fact_uids",
            "reason": ("%s 是用户指定/原文核心 hard 事实，但当前计划没有选入"
                       % rec["uid"]),
            "fix": "选入该事实并完整展示；模型自选背景 hard 不受此约束",
        })
    return out


__all__ = [
    "VERSION", "SOURCE_USER", "SOURCE_CORE", "SOURCE_MODEL_BACKGROUND",
    "SOURCE_UNKNOWN", "SOURCES", "LEVEL_HARD", "LEVEL_ADVISORY", "LEVEL_UNKNOWN",
    "LEVELS", "DEFAULT_LEVEL", "LEGAL_FIX", "ADJUDICATED_BY_USER",
    "source_records", "normalize_sources", "source_problems", "is_hard",
    "must_select", "selection_problems",
    "adjudicated_records", "adjudication_problems", "apply_adjudication",
]
