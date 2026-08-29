"""约束引擎入口.

apply_constraints(ctx) 是整个框架的唯一对外入口：
  1. 遍历 ALL_CONSTRAINTS 注册表
  2. 每个 Constraint 调 apply(ctx)
  3. 收集 RepairRecord + Violation
  4. 返回 RepairReport

run_hard_fix(script) 是 main.py 用的便捷入口：
  - 默认：跑新 Constraint 框架（29 条全部）
  - HARD_FIX_USE_OLD=1：回退到旧 _hard_fix_shot_prompts 路径

回退机制：
  - HARD_FIX_USE_OLD=1: 完全回退到旧 _hard_fix_* 路径（Phase 1-4 兜底）
  - per-Constraint feature flag: CONSTRAINT_<NAME>_ENABLED=0 关闭单个约束
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional, Tuple, Type

from .types import RepairReport
from .context import ConstraintContext
from .base import Constraint


# ============================================================================
# 全局注册表（Phase 2+ 逐个填入 29 个 Constraint 子类）
# ============================================================================
ALL_CONSTRAINTS: list = []


def apply_constraints(
    ctx: ConstraintContext,
    *,
    only: Optional[Tuple[Type[Constraint], ...]] = None,
    skip: Optional[Tuple[Type[Constraint], ...]] = None,
) -> RepairReport:
    """执行所有 Constraint，返回 RepairReport.

    Args:
        ctx: 约束执行上下文
        only: 仅运行这些 Constraint 类（白名单）
        skip: 跳过这些 Constraint 类（黑名单）

    Returns:
        RepairReport 含 repairs / violations_remaining / duration_ms
    """
    # 紧急回退：HARD_FIX_USE_OLD=1 直接返回空 report（Phase 1-4 兜底）
    if os.getenv("HARD_FIX_USE_OLD", "0").strip() == "1":
        try:
            from shared.tee_logger import get_logger
            get_logger().warn(
                "[CONSTRAINT] HARD_FIX_USE_OLD=1, 跳过所有 Constraint, 走旧 _hard_fix_* 路径"
            )
        except Exception:
            pass
        return RepairReport()

    report = RepairReport()
    start = time.time()

    for c in ALL_CONSTRAINTS:
        if only and not isinstance(c, only):
            continue
        if skip and isinstance(c, skip):
            continue
        if not c.is_enabled():
            continue
        try:
            repairs = c.apply(ctx)
            if repairs:
                report.repairs.extend(repairs)
        except Exception as e:
            try:
                from shared.tee_logger import get_logger
                get_logger().warn(f"[CONSTRAINT] {c.name} 异常: {e}")
            except Exception:
                # logger 不可用时也静默（测试场景）
                pass

    # 取出每个 Constraint 上报的违例
    report.violations_remaining = ctx.drain_violations()
    report.duration_ms = (time.time() - start) * 1000
    return report


# ============================================================================
# main.py 集成入口（新旧切换）
# ============================================================================

# v6.50（08-28）：进程内只声明一次当前硬修复路径——
# 08-28 排障实测：日志里只有旧路径才有提示、新框架静默，读者无法从
# 日志判断本轮跑的是哪条路径（还被 pytest 混进来的同名告警误导）。
_PATH_ANNOUNCED = False


def _announce_path(use_old: bool) -> None:
    global _PATH_ANNOUNCED
    if _PATH_ANNOUNCED:
        return
    _PATH_ANNOUNCED = True
    try:
        from shared.tee_logger import get_logger
        log = get_logger()
        if log is None:
            return
        if use_old:
            log.warn(
                "[HARD_FIX] 本轮路径: 旧 _hard_fix_* 正则路径（HARD_FIX_USE_OLD=1）——"
                "若非有意切换请 unset 该环境变量"
            )
        else:
            log.info("[HARD_FIX] 本轮路径: 新 Constraint 框架")
    except Exception:
        pass


def run_hard_fix(
    script: Any,
    *,
    is_in_repair_loop: bool = False,
    is_final_pass: bool = False,
) -> Any:
    """main.py 用的便捷入口：跑硬修复层.

    Args:
        script: 完整 script dict
        is_in_repair_loop: True=main.py:1055 调用（repair round 末尾）
        is_final_pass: True=main.py:1664 调用（_compute_timecodes 后）

    Returns:
        修改后的 script（与旧 _hard_fix_shot_prompts 兼容，直接返回 script）

    Switch:
        - HARD_FIX_USE_OLD=1 → 走旧 _hard_fix_shot_prompts 路径（回退）
        - 默认 → 跑新 Constraint 框架（29 条全部）

    Context 参数（v6.42 接入）：
        - is_in_repair_loop: 标记 repair loop 末尾，避免某些约束在 repair 阶段误触
        - is_final_pass: 标记 _compute_timecodes 之后，确保 RuntimeExtendConstraint 时长拉伸生效
    """
    use_old = os.getenv("HARD_FIX_USE_OLD", "0").strip() == "1"
    _announce_path(use_old)
    if use_old:
        try:
            from step01_script_gen.validation import hard_fix_shot_prompts as _old_hfix
            return _old_hfix(script)
        except Exception as e:
            try:
                from shared.tee_logger import get_logger
                get_logger().warn(f"[HARD_FIX] 旧路径失败: {e}")
            except Exception:
                pass
            return script

    # 默认：跑新 Constraint 框架
    try:
        # 一次性：在新框架首次启用时跑一次冲突检测（v6.42 PoC）
        _maybe_run_conflict_detect_once()
        from .context import ConstraintContext
        from .element_markers import ElementMarkers
        ctx = ConstraintContext.from_script(
            script,
            markers=ElementMarkers(),
            is_in_repair_loop=is_in_repair_loop,
            is_final_pass=is_final_pass,
        )
        report = apply_constraints(ctx)
        # 日志：报告修复次数 + HARD 未解决数
        try:
            from shared.tee_logger import get_logger
            log = get_logger()
            if report.repairs:
                counts = report.repairs_by_constraint
                top = ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:5])
                log.info(f"[CONSTRAINT] 修复 {len(report.repairs)} 处 (top: {top})")
            hard_unresolved = report.hard_unresolved
            if hard_unresolved:
                log.warn(
                    f"[CONSTRAINT] HARD 未解决 {len(hard_unresolved)} 处 (建议 regen): "
                    + ", ".join(f"{v.constraint_name}@shot{v.shot_index}" for v in hard_unresolved[:5])
                )
        except Exception:
            pass
        # v6.42: HARD 未解决 → 触发 regen（opt-in: REGEN_AUTO_TRIGGER=1），
        # LLM 输出解析后 merge 回原 script
        try:
            from .regen_trigger import trigger_regen_and_merge
            script = trigger_regen_and_merge(script, report)
        except Exception:
            pass
        return script
    except Exception as e:
        try:
            from shared.tee_logger import get_logger
            get_logger().warn(f"[CONSTRAINT] 新框架失败: {e}，保留原版")
        except Exception:
            pass
        return script


# ============================================================================
# 一次性冲突检测（模块加载后第一次 run_hard_fix 触发）
# ============================================================================

_CONFLICT_DETECT_DONE = False


def _maybe_run_conflict_detect_once() -> None:
    """进程内只跑一次：扫所有注册约束的冲突."""
    global _CONFLICT_DETECT_DONE
    if _CONFLICT_DETECT_DONE:
        return
    _CONFLICT_DETECT_DONE = True
    try:
        from .conflict_detector import detect_and_log
        detect_and_log(ALL_CONSTRAINTS)
    except Exception:
        pass

