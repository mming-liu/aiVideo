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
