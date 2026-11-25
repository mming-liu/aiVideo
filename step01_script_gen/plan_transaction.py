# -*- coding: utf-8 -*-
"""中立计划事务（S27，2026-11-25）.

从轻量计划入口抽出的**内容身份事务**：冻结块比对、允许重分配的关系块、
候选视图准入与投影。它不依赖轻量模式，也不解释声音/事实的业务含义——
调用方（legacy/lightweight/unified）各自提供投影与验收回调。

`compile_reallocated_plan()` 仍保留为兼容入口；其冻结检查与投影经由本模块，
避免"轻量专用事务"继续膨胀成第二个生产系统。
"""
from __future__ import annotations

import copy

#: 重分配时**不得改变**的视图块：主张/证据/事件/父子谱系/钩子/卡片/出处修正。
#: 编号不在冻结范围：它由投影按候选顺序重新派生。
FROZEN_VIEW_BLOCKS = ("frozen_facts", "frozen_events", "hook_bindings",
                      "screen_cards", "evidence_updates")
#: 允许重分配的关系块（用于变更清单，不参与判据）。
MUTABLE_VIEW_BLOCKS = ("beats", "dialogue_units", "narration_units", "fact_bindings")


def frozen_payload(view) -> dict:
    return {key: copy.deepcopy((view or {}).get(key)) for key in FROZEN_VIEW_BLOCKS}


def mutable_payload(view) -> dict:
    out = {}
    for key in MUTABLE_VIEW_BLOCKS:
        value = (view or {}).get(key)
        out[key] = copy.deepcopy(value) if isinstance(value, list) else []
    return out


def frozen_drift(base_view, candidate_view) -> list:
    """冻结块差异键；空表 = 内容身份未漂移.

    `plan_basis` 与冻结块同等重要（它承载来源/事件/事实身份），单独计入 `plan_basis`。
    """
    base, cand = frozen_payload(base_view), frozen_payload(candidate_view)
    drift = [key for key in FROZEN_VIEW_BLOCKS if base.get(key) != cand.get(key)]
    if (base_view or {}).get("plan_basis") != (candidate_view or {}).get("plan_basis"):
        drift.append("plan_basis")
    return drift


def admission_problems(candidate_view) -> list:
    from shared.presentation_contract import view_admission_problems
    return list(view_admission_problems(candidate_view or {}) or [])


def project_view(candidate_view):
    """视图 → 计划；失败时抛 `PresentationContractError`（由调用方转记录）."""
    from shared.presentation_contract import plan_from_view
    return plan_from_view(candidate_view)


__all__ = ["FROZEN_VIEW_BLOCKS", "MUTABLE_VIEW_BLOCKS", "frozen_payload",
           "mutable_payload", "frozen_drift", "admission_problems", "project_view"]
