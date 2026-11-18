# -*- coding: utf-8 -*-
"""M4：提交前确定性预审门与注入幂等防重（2026-11-18）.

从 ``step01_script_gen/main.py`` 拆出的提交/送审前置判据；只读、零模型调用。
- ``pre_audit_gate``：送 LLM 终审前跑 C1–C7 + 跨字段/提示词矛盾检查；
- ``assert_injection_idempotent``：写回前拒绝 ``duplicate_injection`` 硬伤，
  防止二次拼接把同一块提示词再注入一遍。

严格模式由 ``STEP01_PRE_AUDIT_GATE=1`` 开启；默认关闭以保留既有修复轮路径
（确定性硬伤仍会在审计后的质量门被拦下）。
"""
from __future__ import annotations

import copy
import os

#: 失配键 → 人读文案（与 main 的日志/文档共用一套措辞）
REUSE_MISS_TEXT = {
    "status_not_ok": "旧审计没有结论（status != ok）",
    "content_identity_missing": "旧审计缺少 content_identity（复用证据不足）",
    "content_identity": "content_identity 不匹配（原文/计划/送审稿/契约已变）",
    "audit_focus": "content_identity 仅因定向项不同（audit_focus 已变）",
    "source_identity_missing": "旧审计缺少 source_identity（复用证据不足）",
    "source_identity": "source_identity 不匹配（原文/计划/契约已变）",
    "input_revision": "送审稿 revision 不同",
    "rule_source_fingerprint": "规则源码指纹不同",
    "duration_band": "时长带不同",
    "audit_scope": "审计范围不同",
    "audit_prompt_version": "审计提示词版本不同",
    "audit_focus_version": "定向项版本不同",
    "channel_provider": "审计通道 provider 不同",
    "channel_model": "审计通道 model 不同",
}

#: 剩余 hard 中，**必须重写文本/结构**才能消除的 code（交既有修复轮）.
#:
#: 这里刻意只列语义明确的几类：缺人/缺帧/重复注入/超单镜上限。未列出的 code
#: 一律走 `unresolved`（不猜修法、不放宽判据）——新增检测器时若要归入
#: `needs_llm_repair`，必须显式加进来并说明理由，不能靠「看起来像文本问题」推断。
LLM_REPAIR_CODES = frozenset({
    "layout_presence_gap",
    "kf_person_missing_in_layout",
    "duplicate_injection",
    "shot_duration_over_cap",
    "shot_content_over_cap",
})

#: **不得**由预审门自动消除的 hard：`duplicate_injection` 有独立的写回硬拒绝
#: 契约（`assert_injection_idempotent`），且它的修复必须改文本。预审门发现它时
#: 直接交既有修复轮/硬拒绝，不做「先修后审」——避免用字段前缀等副作用把检测
#: 判据绕过去，也避免两条路径对同一硬伤给出不同结论。
NO_AUTO_HEAL_CODES = frozenset({"duplicate_injection"})


class PreAuditError(RuntimeError):
    """送审前确定性预审门拒绝."""


def pre_audit_enabled() -> bool:
    """严格预审门开关（默认关闭，保留既有修复轮路径）."""
    return str(os.getenv("STEP01_PRE_AUDIT_GATE", "")).strip().lower() in (
        "1", "true", "yes", "on")


def pre_audit_gate(script: dict, *, include_rules: bool = True) -> dict:
    """返回 ``{ok, hard, advisory, findings}``；不写盘、不调模型."""
    from shared.script_preflight import run as _run

    findings = list(_run(script, include_rules=include_rules) or [])
    hard = [f for f in findings if str(f.get("severity") or "") == "hard"]
    advisory = [f for f in findings if str(f.get("severity") or "") != "hard"]
    return {"ok": not hard, "hard": hard, "advisory": advisory,
            "findings": findings}


def heal_pre_audit_hard(script: dict, *, novel_text: str = "",
                        include_rules: bool = True, runner=None) -> dict:
    """严格预审门的「先修后审」三态（T2，2026-11-18）.

    纯确定性：只调**已有** `constraints.run_hard_fix()`（v6.98 起不再调 LLM），
    对 `pre_audit_gate()` 报出的 hard 做一次局部修复，然后**重跑同一判据**。
    本函数不写盘、不采纳、不改任何 blocking 判据；是否用修复稿送审由调用方决定。

    返回：
        {
          "status": "clean" | "healed" | "needs_llm_repair" | "unresolved",
          "ok": bool,                 # clean/healed = True
          "script": dict | None,      # 仅 healed：修复后的副本
          "hard_before": [...], "hard_after": [...],
          "repairs": [{constraint,target,action}, ...],
          "reason": str, "deterministic": True,
        }

    三态边界：
      * healed：修复后 hard 归零；
      * needs_llm_repair：仍有 hard，且 code 全在 `LLM_REPAIR_CODES`（需重写文本/
        结构，交既有修复轮，不自行放宽）；
      * unresolved：预审/修复器异常、复检失败，或剩余 hard 无法归类——保持
        blocked 原语义，并给出可读原因。
    """
    out: dict = {"status": "clean", "ok": True, "script": None,
                 "hard_before": [], "hard_after": [], "repairs": [],
                 "reason": "", "deterministic": True}
    try:
        before = pre_audit_gate(script, include_rules=include_rules)
    except Exception as exc:                      # noqa: BLE001
        out.update({"status": "unresolved", "ok": False,
                    "reason": "送审前预审门无法判定: %s: %s"
                              % (type(exc).__name__, exc)})
        return out
    hard_before = list(before.get("hard") or [])
    out["hard_before"] = hard_before
    if not hard_before:
        return out

    # 注入重复有独立的写回硬拒绝契约：不在本函数里尝试自动消除。
    _no_heal = sorted({str(f.get("code") or "") for f in hard_before}
                      & NO_AUTO_HEAL_CODES)
    if _no_heal:
        out.update({"status": "needs_llm_repair", "ok": False,
                    "hard_after": hard_before,
                    "reason": "存在不得由确定性层自动消除的 hard：%s"
                              "（交既有修复轮/硬拒绝，不放宽）" % ", ".join(_no_heal)})
        return out

    if runner is None:
        from step01_script_gen.constraints import run_hard_fix as runner
    try:
        fixed, report = runner(copy.deepcopy(script), return_report=True,
                               novel_text=novel_text)
    except Exception as exc:                      # noqa: BLE001
        out.update({"status": "unresolved", "ok": False,
                    "reason": "确定性修复器不可用: %s: %s"
                              % (type(exc).__name__, exc)})
        return out
    if not isinstance(fixed, dict):
        out.update({"status": "unresolved", "ok": False,
                    "reason": "确定性修复器未返回 script 对象"})
        return out
    try:
        after = pre_audit_gate(fixed, include_rules=include_rules)
    except Exception as exc:                      # noqa: BLE001
        out.update({"status": "unresolved", "ok": False,
                    "reason": "修复后复检失败（无法判定是否归零）: %s: %s"
                              % (type(exc).__name__, exc)})
        return out
    hard_after = list(after.get("hard") or [])
    out["hard_after"] = hard_after
    out["repairs"] = [
        {"constraint": str(getattr(r, "constraint_name", "") or ""),
         "target": str(getattr(r, "target", "") or ""),
         "action": str(getattr(r, "action", "") or "")}
        for r in (getattr(report, "repairs", None) or [])
    ]
    if not hard_after:
        out.update({"status": "healed", "ok": True, "script": fixed,
                    "reason": "确定性 hard 已全部消除（%d 处）" % len(hard_before)})
        return out
    codes = sorted({str(f.get("code") or "") for f in hard_after})
    unknown = [c for c in codes if c not in LLM_REPAIR_CODES]
    if unknown:
        out.update({"status": "unresolved", "ok": False,
                    "reason": "修复后仍有无法归类的 hard：%s（不猜测修法，保持 blocked）"
                              % ", ".join(unknown)})
        return out
    out.update({"status": "needs_llm_repair", "ok": False,
                "reason": "修复后仍有需文本重生成的 hard：%s（交既有修复轮，不放宽）"
                          % ", ".join(codes)})
    return out


def assert_injection_idempotent(script: dict) -> None:
    """写回/送审前拒绝 ``duplicate_injection`` 硬伤（M4 注入幂等防重）."""
    gate = pre_audit_gate(script, include_rules=True)
    dups = [f for f in gate["hard"]
            if str(f.get("code") or "") == "duplicate_injection"]
    if dups:
        first = dups[0]
        raise PreAuditError(
            "duplicate_injection 硬伤：shot=%s frame=%s %s"
            % (first.get("shot"), first.get("frame"), first.get("message")))
