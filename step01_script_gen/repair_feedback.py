# -*- coding: utf-8 -*-
"""M03（2026-11-15）：修复轮的**有界结构化反馈**（零模型 / 零网络）.

设计契约（用户《Step01 修复闭环收敛方案》M03）：

* 反馈记录由**生产者**给出 `code` / `phase` / `field_path` / `allowed_paths`；
  自由文本 `text` 只用于展示，**不参与分类、不参与判据**。未知 code 一律按
  `engineering_failure` 处理（fail-closed），不允许靠猜字符串放宽放行条件。
* `retry_state` 是调用方持有的跨 attempt 容器。每个「冻结目标集合」最多获得
  **一次答卷纠正机会**；`source_revision` 不符即丢弃反馈，绝不借用上一轮的
  裁决结果冒充本轮通过。
* 工程故障（checker 失败 / writer 观测失败 / 帧映射内部失配）优先阻断依赖动作。
* 混合拒因逐项记录；只要有一条工程故障，整体即终局。

本模块不读稿、不调模型、不写盘，可独立单测。
"""
from __future__ import annotations

import hashlib
import json

__all__ = [
    "RECORD_FIELDS", "POLICY", "PHASE_APPLY", "PHASE_TRANSACTION",
    "PHASE_ENGINEERING", "feedback_record", "render_feedback",
    "frozen_signature", "answer_signature", "plan_feedback_state",
    "transaction_feedback_records",
]

RECORD_FIELDS = ("target", "phase", "code", "field_path",
                 "allowed_paths", "retryable", "details")

PHASE_APPLY = "apply"
PHASE_TRANSACTION = "transaction"
PHASE_ENGINEERING = "engineering"

# code -> (retryable, terminal, 展示用处理说明)
#
# 与用户策略表逐条对应：
#   未授权父路径 / 配对形状错误  → 具体路径 + 合法表达，允许一次纠正
#   身份来源无法证明              → 指明成对补帧表达，允许一次纠正
#   保护字段越权                  → 保留基线，反馈禁止改动项，重验继续完整执行
#   checker / writer / 映射失配  → 工程故障终局
#   合法应用但目标未解决          → 返回真实残余
#   相同拒因 + 同一补丁重复       → 停止该目标继续尝试
POLICY = {
    "shape_invalid": (
        True, False, "按示例给出合法形状，允许一次答卷纠正"),
    "pair_shape_invalid": (
        True, False,
        "按配对表合法表达重写：`frame_layouts.kfs` 的 ff/lf/kfs 必须与镜头的 "
        "`keyframes` 数目一一**同序对应**（kfs[i] 是第 i 个关键帧的布局）；"
        "缺帧改用 `keyframe_additions`（frame 与 layout 成对，由代码按 t_sec 装配），"
        "允许一次答卷纠正"),
    "unauthorized_path": (
        True, False, "只提交本轮授权的具体路径，允许一次答卷纠正"),
    "target_not_picked": (
        True, False, "只输出本轮点名目标，允许一次答卷纠正"),
    "frame_identity_unproven": (
        True, False,
        "改用 updates（具体叶子）+ keyframe_additions（frame/layout 成对）"
        "表达补帧，允许一次答卷纠正"),
    "layout_structure_invalid": (
        True, False,
        "已有帧必须逐字保留；只改已授权叶子，新增帧用成对补帧表达，允许一次纠正"),
    "new_blocking": (
        True, False,
        "**上一轮补丁被拒**（带反馈重试）：引入新阻塞的目标**已从本轮移除**，"
        "不要再输出它；其余目标给出真正消除矛盾的修改，允许一次纠正"),
    "protected_field": (
        False, False,
        "保留基线；禁止改动该保护字段，后续重验继续完整执行，"
        "不额外给答卷纠正机会"),
    "no_target_resolved": (
        False, False,
        "补丁已合法应用但目标未解决；返回真实残余，不额外给答卷纠正机会"),
    "engineering_failure": (
        False, True,
        "工程故障终局：停止依赖该结果的后续动作，不发起新的修复请求"),
    "budget_exhausted": (
        False, True, "预算不足：保存冻结候选与明确终局"),
    "duplicate_rejection": (
        False, True, "相同拒因与同一补丁重复：停止该目标继续尝试"),
}

# 事务裁决 reason → 反馈 code。未列出的 reason 一律按工程故障（fail-closed）。
_TRANSACTION_CODE = {
    "checker_failed": "engineering_failure",
    "postprocess_check_failed": "engineering_failure",
    "new_blocking": "new_blocking",
    "postprocess_target_lost": "no_target_resolved",
    "evidence_removed": "protected_field",
    "kf_structure_invalid": "layout_structure_invalid",
    "layout_structure_invalid": "layout_structure_invalid",
    "locked_changed": "protected_field",
    "locked_field_changed": "protected_field",
    "non_target_changed": "protected_field",
    "no_target_resolved": "no_target_resolved",
    # §14 P3 的四类**效果抵消归因**：模型改对了、被后处理 writer 抵消。
    # 它们既不是工程故障，也不是模型越权，属于「合法应用但目标未解决」的
    # 真实残余；冲突本身另有 `layout_conflict` 通道进入下一次请求，这里
    # 绝不能把它们 fail-closed 成终局，否则第二次请求会被错误挡在门外。
    "postprocess_effect_reverted": "no_target_resolved",
    "actor_presence_conflict": "no_target_resolved",
    "actor_binding_ambiguous": "no_target_resolved",
    "repair_capability_missing": "no_target_resolved",
}


def _stable(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:
        return repr(obj)


def _digest(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def feedback_record(*, code: str, target: str = "", phase: str = PHASE_APPLY,
                    field_path: str = "", allowed_paths=(), details=None,
                    text: str = "", retryable=None, terminal=None) -> dict:
    """规范化一条反馈记录；`text` 只留作展示，绝不参与分类."""
    _code = str(code or "")
    if _code not in POLICY:
        # fail-closed：未知/伪造的 code 不得获得「可纠正」权限
        _code = "engineering_failure"
    _retryable, _terminal, _hint = POLICY[_code]
    paths = sorted({str(p) for p in (allowed_paths or ()) if str(p)})
    rec = {
        "target": str(target or ""),
        "phase": str(phase or PHASE_APPLY),
        "code": _code,
        "field_path": str(field_path or ""),
        "allowed_paths": paths,
        "retryable": bool(_retryable if retryable is None else retryable),
        "details": dict(details or {}),
        # 展示专用字段（不进任何判据）
        "terminal": bool(_terminal if terminal is None else terminal),
        "hint": _hint,
        "text": str(text or ""),
    }
    return rec


def render_feedback(records) -> str:
    """把结构化记录渲染成**仅用于展示**的请求文本."""
    recs = [r for r in (records or []) if isinstance(r, dict)]
    if not recs:
        return ""
    lines = ["\n**上一轮修复反馈（结构化，按目标）**："]
    for r in recs:
        head = "- [%s/%s] 目标 %s" % (
            r.get("phase") or PHASE_APPLY,
            r.get("code") or "?",
            r.get("target") or "(全局)")
        if r.get("field_path"):
            head += " · 路径 `%s`" % r["field_path"]
        lines.append(head)
        if r.get("text"):
            lines.append("  原因：%s" % str(r["text"])[:260])
        paths = r.get("allowed_paths") or []
        if paths:
            lines.append("  本轮合法可写路径：%s" % ", ".join(
                "`%s`" % p for p in paths[:12]))
        lines.append("  处理：%s" % (r.get("hint") or POLICY.get(
            r.get("code"), POLICY["engineering_failure"])[2]))
    lines.append("- 每个冻结目标集合只有一次答卷纠正机会；同一补丁重复提交即停止。\n")
    return "\n".join(lines)


def frozen_signature(targets) -> str:
    """冻结目标集合的稳定身份（顺序无关）."""
    return _digest(_stable(sorted({str(t) for t in (targets or ())})))


def answer_signature(replacements) -> str:
    """本轮答卷的稳定指纹，用于识别「同一补丁重复提交」."""
    return _digest(_stable(replacements))


def _identity(rec: dict) -> tuple:
    return (str(rec.get("code") or ""), str(rec.get("target") or ""),
            str(rec.get("field_path") or ""))


def plan_feedback_state(*, records, prior_state: dict | None,
                        frozen_targets, source_revision: str,
                        answer_sig: str = "",
                        excluded_override=None) -> dict:
    """裁决要不要发放「一次答卷纠正机会」，并返回**应写入 retry_state** 的状态.

    返回值永远是一份完整状态（调用方 `retry_state.clear(); retry_state.update(...)`）。
    判据只看结构化字段与稳定签名，不读 `text`。
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    prior = dict(prior_state or {})
    sig = frozen_signature(frozen_targets)
    prev_sig = str(prior.get("frozen_signature") or "")
    prev_answer = str(prior.get("answer_signature") or "")
    prev_records = [r for r in (prior.get("feedback_records") or [])
                    if isinstance(r, dict)]
    used = bool(prior.get("used_correction"))

    base = {
        "feedback_records": recs,
        "feedback": render_feedback(recs),
        "source_revision": str(source_revision or ""),
        "frozen_targets": sorted({str(t) for t in (frozen_targets or ())}),
        "frozen_signature": sig,
        "answer_signature": str(answer_sig or ""),
        "used_correction": used,
        "correction_granted": False,
        "terminal": False,
        "stop_reason": "",
        "excluded": [],
        "phase": (recs[0].get("phase") if recs else PHASE_APPLY),
    }

    # ① 工程故障优先阻断依赖动作
    if any(r.get("terminal") or r.get("code") == "engineering_failure"
           for r in recs):
        base["terminal"] = True
        base["stop_reason"] = "engineering_failure"
        return base

    # ② 相同拒因 + 同一补丁重复 ⇒ 停止该目标继续尝试
    cur_ids = {_identity(r) for r in recs}
    prev_ids = {_identity(r) for r in prev_records}
    if (cur_ids and cur_ids == prev_ids and answer_sig
            and answer_sig == prev_answer):
        base["terminal"] = True
        base["stop_reason"] = "duplicate_rejection"
        base["excluded"] = sorted({str(r.get("target")) for r in recs
                                   if r.get("target")})
        base["feedback"] = render_feedback(recs + [feedback_record(
            code="duplicate_rejection", target="", phase=PHASE_TRANSACTION,
            text="上一轮同一份答卷产生完全相同的拒因，本目标停止继续尝试")])
        return base

    # ③ 每个冻结目标集合最多一次答卷纠正机会
    if used and prev_sig == sig:
        base["terminal"] = True
        base["stop_reason"] = "correction_budget_exhausted"
        return base

    retryable = any(bool(r.get("retryable")) for r in recs)
    if not retryable:
        # 合法应用但目标未解决 / 保护字段越权：不是工程故障，正常终局，
        # 但反馈与真实残余必须留在状态里供提交层与外部证据复核。
        base["stop_reason"] = "not_retryable"
        return base

    if excluded_override is not None:
        # 事务侧已知**真实肇事目标**（`_offend_raw`）：记录里的 target 是「仍待修的
        # 剩余目标」，不能拿它当排除集，否则会把该修的目标反而剔除（v6.138）。
        excluded = sorted({str(t) for t in excluded_override})
    else:
        excluded = sorted({str(r.get("target")) for r in recs
                           if r.get("code") == "new_blocking" and r.get("target")})
    base.update({
        "used_correction": True,
        "correction_granted": True,
        "excluded": excluded,
        "stop_reason": "",
    })
    return base


def transaction_feedback_records(*, reason: str, targets=(),
                                 offending=(), allowed_by_target=None,
                                 field_path: str = "",
                                 frame_locator_issues=(),
                                 observation_errors=(),
                                 details=None) -> list:
    """把**事务裁决的生产者结果**转成结构化记录（不解析自由文本）.

    `frame_locator_issues` / `observation_errors` 非空即追加工程故障记录，
    由 `plan_feedback_state` 优先阻断。
    """
    code = _TRANSACTION_CODE.get(str(reason or ""), "engineering_failure")
    tlist = sorted({str(t) for t in (targets or ())})
    off = sorted({str(t) for t in (offending or ())})
    d = dict(details or {})
    d.setdefault("reason", str(reason or ""))
    if off:
        d["offending_targets"] = off

    paths = []
    for t in tlist:
        paths.extend(str(p) for p in ((allowed_by_target or {}).get(t) or ()))
    records = []
    if tlist:
        for t in tlist:
            records.append(feedback_record(
                code=code, target=t, phase=PHASE_TRANSACTION,
                field_path=field_path, allowed_paths=sorted(set(paths)),
                details=d, text="事务裁决 reason=%s" % reason))
    else:
        records.append(feedback_record(
            code=code, target="", phase=PHASE_TRANSACTION,
            field_path=field_path, allowed_paths=sorted(set(paths)),
            details=d, text="事务裁决 reason=%s（全局）" % reason))

    for issue in (frame_locator_issues or ()):
        records.append(feedback_record(
            code="engineering_failure", target="", phase=PHASE_ENGINEERING,
            details={"kind": "frame_locator_integrity"}, text=str(issue)))
    for err in (observation_errors or ()):
        writer = err.get("writer") if isinstance(err, dict) else ""
        records.append(feedback_record(
            code="engineering_failure", target="", phase=PHASE_ENGINEERING,
            details={"kind": "writer_observation_error",
                     "writer": str(writer or "")},
            text=str(err.get("error") if isinstance(err, dict) else err)))
    return records
