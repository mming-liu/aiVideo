# -*- coding: utf-8 -*-
"""M04（2026-11-15）：审计预算准入、冻结候选与四项计时（零模型 / 零网络）.

缺口（用户《Step01 修复闭环收敛方案》M04）：`_audit_budget_allowance()` 只在
审计**即将开始时**读剩余预算并截短额度；把总预算从 900s 抬到 1200s 并不能
保证前序阶段留下足够时间。run g 剩 186s、run i 剩 126s 时最终语义审计仍被
通道超时打断，就是这个形态。

本模块提供**纯函数**（可注入模拟时钟），生产侧只负责把既有预算函数的读数
喂进来，不另造一套剩余预算计算：

  * `admit_action()`：启动额外生成 / 再生成 / 修复**之前**判定剩余时间能否容纳
    「本次动作 + 最终生效审计配置上限 + 提交收尾」。
  * `freeze_candidate()` / `verify_frozen_candidate()`：预算不足时保存冻结候选与
    `budget_exhausted` 明确终局；续验核对输入 revision、审计配置、规则身份，
    **不借用旧审计结果冒充本轮通过**。
  * `budget_timing_record()`：分别记录剩余预算、审计允许时长、服务端等待、
    流式耗时；不可得的读数一律 `None` 并标来源，禁止估算填充。

本模块不调模型、不发请求、不直接写正式产物。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

__all__ = [
    "BudgetAdmission", "SimClock", "admit_action", "audit_reserve_sec",
    "freeze_candidate", "verify_frozen_candidate", "budget_timing_record",
]

TIMING_FIELDS = ("remaining_budget_sec", "audit_allowance_sec",
                 "server_wait_sec", "stream_elapsed_sec")


class SimClock:
    """确定性模拟时钟：只用于离线验证准入 / 耗尽 / 续验，不碰真实时间."""

    def __init__(self, total_budget_sec: float = 1200.0, start: float = 0.0):
        self.total = float(total_budget_sec)
        self.now = float(start)

    def advance(self, sec: float) -> "SimClock":
        self.now += float(sec)
        return self

    def remaining(self) -> float | None:
        if self.total <= 0:
            return None
        return round(self.total - self.now, 1)


@dataclass(frozen=True)
class BudgetAdmission:
    allowed: bool
    reason: str
    remaining_budget_sec: float | None
    action_cost_sec: float
    audit_cap_sec: float
    commit_reserve_sec: float
    audit_reserve_sec: float
    required_sec: float
    projected_remaining_sec: float | None
    freeze_required: bool

    def as_record(self) -> dict:
        return {
            "allowed": self.allowed, "reason": self.reason,
            "remaining_budget_sec": self.remaining_budget_sec,
            "action_cost_sec": self.action_cost_sec,
            "audit_cap_sec": self.audit_cap_sec,
            "commit_reserve_sec": self.commit_reserve_sec,
            "audit_reserve_sec": self.audit_reserve_sec,
            "required_sec": self.required_sec,
            "projected_remaining_sec": self.projected_remaining_sec,
            "freeze_required": self.freeze_required,
        }


def audit_reserve_sec(audit_cap_sec: float,
                      commit_reserve_sec: float = 60.0) -> float:
    """**最终生效审计配置**的完整预留 = 审计上限 + 提交收尾余量.

    这里必须用配置的 cap（`_audit_budget_allowance()` 的 `info["cap"]`），
    不能用已被剩余预算截短过的 allowance——否则预留会随剩余缩水，判据恒真。
    """
    return float(audit_cap_sec) + float(commit_reserve_sec)


def admit_action(*, remaining_budget_sec: float | None,
                 action_cost_sec: float, audit_cap_sec: float,
                 commit_reserve_sec: float = 60.0) -> BudgetAdmission:
    """是否允许启动一个「额外动作」（生成 / 再生成 / 修复）.

    `remaining_budget_sec=None` = 无总预算约束（未 start 或预算 0），沿用旧行为。
    """
    cost = max(0.0, float(action_cost_sec))
    reserve = audit_reserve_sec(audit_cap_sec, commit_reserve_sec)
    required = cost + reserve
    if remaining_budget_sec is None:
        return BudgetAdmission(
            allowed=True, reason="no_total_budget_cap",
            remaining_budget_sec=None, action_cost_sec=cost,
            audit_cap_sec=float(audit_cap_sec),
            commit_reserve_sec=float(commit_reserve_sec),
            audit_reserve_sec=reserve, required_sec=required,
            projected_remaining_sec=None, freeze_required=False)
    left = max(0.0, float(remaining_budget_sec))
    allowed = left >= required
    return BudgetAdmission(
        allowed=allowed,
        reason=("budget_ok" if allowed else "budget_exhausted"),
        remaining_budget_sec=round(left, 1),
        action_cost_sec=cost, audit_cap_sec=float(audit_cap_sec),
        commit_reserve_sec=float(commit_reserve_sec),
        audit_reserve_sec=reserve, required_sec=required,
        projected_remaining_sec=round(max(0.0, left - cost), 1),
        freeze_required=not allowed)


def _stable(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:
        return repr(obj)


def _digest(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def freeze_candidate(*, script: dict, input_revision: str,
                     audit_config: dict, rule_fingerprint: str,
                     admission: BudgetAdmission | dict,
                     action: str, output_root: str = "") -> dict:
    """预算不足时保存的**冻结候选**（可 JSON 序列化）.

    只记事实：输入 revision、审计配置身份、规则身份、准入读数、隔离输出根。
    不含时间戳（覆盖重跑逐字节一致），不含任何旧审计结论。
    """
    rec = admission.as_record() if isinstance(admission, BudgetAdmission) \
        else dict(admission or {})
    return {
        "kind": "budget_exhausted",
        "action": str(action or ""),
        "input_revision": str(input_revision or ""),
        "audit_config": dict(audit_config or {}),
        "audit_config_fingerprint": _digest(_stable(audit_config or {})),
        "rule_fingerprint": str(rule_fingerprint or ""),
        "script_digest": _digest(_stable(script)),
        "admission": rec,
        "output_root": str(output_root or ""),
        "prior_audit_reused": False,
        "terminal": "budget_exhausted",
    }


def verify_frozen_candidate(record: dict, *, script: dict,
                            input_revision: str, audit_config: dict,
                            rule_fingerprint: str,
                            budget_admission: BudgetAdmission | dict | None = None
                            ) -> dict:
    """续验准入：从**冻结候选**进入现有完整审计 / 提交链之前的三项核对.

    绝不借用旧审计结果：`prior_audit_reused` 恒为 False，且记录里不携带任何
    `audit_payload` / `issues` / `status` 键（有就判为身份不符）。
    """
    rec = dict(record or {})
    cfg_now = dict(audit_config or {})
    cfg_fp = _digest(_stable(cfg_now))
    checks = {
        "input_revision_ok": (str(input_revision or "")
                              == str(rec.get("input_revision") or "")),
        "script_digest_ok": (_digest(_stable(script))
                             == str(rec.get("script_digest") or "")),
        "audit_config_ok": (cfg_fp == str(rec.get("audit_config_fingerprint") or "")),
        "rule_identity_ok": (str(rule_fingerprint or "")
                             == str(rec.get("rule_fingerprint") or "")),
        "no_prior_audit_payload": not any(
            k in rec for k in ("audit_payload", "issues", "status")),
    }
    admission = budget_admission.as_record() \
        if isinstance(budget_admission, BudgetAdmission) \
        else dict(budget_admission or {})
    if admission:
        checks["budget_recovered"] = bool(admission.get("allowed"))
    eligible = all(bool(v) for v in checks.values())
    return {
        "eligible": eligible,
        "checks": checks,
        "prior_audit_reused": False,
        "input_revision": str(input_revision or ""),
        "rule_fingerprint": str(rule_fingerprint or ""),
        "audit_config_fingerprint": cfg_fp,
        "frozen_terminal": rec.get("terminal") or "",
        "reason": ("frozen_candidate_verified" if eligible
                   else "frozen_candidate_identity_mismatch"),
    }


def budget_timing_record(*, remaining_budget_sec, audit_allowance_sec,
                         server_wait_sec, stream_elapsed_sec,
                         stream_timing_source: str = "provider_not_instrumented",
                         phase: str = "final_audit") -> dict:
    """四项读数**分别**记录；不可得为 None 并标来源，禁止估算填充."""
    return {
        "phase": str(phase or ""),
        "remaining_budget_sec": (None if remaining_budget_sec is None
                                 else round(float(remaining_budget_sec), 1)),
        "audit_allowance_sec": (None if audit_allowance_sec is None
                                else round(float(audit_allowance_sec), 1)),
        "server_wait_sec": (None if server_wait_sec is None
                            else round(float(server_wait_sec), 3)),
        "stream_elapsed_sec": (None if stream_elapsed_sec is None
                               else round(float(stream_elapsed_sec), 3)),
        "stream_timing_source": str(stream_timing_source or ""),
    }
