"""约束引擎入口.

apply_constraints(ctx) 是整个框架的唯一对外入口：
  1. 遍历 ALL_CONSTRAINTS 注册表
  2. 每个 Constraint 调 apply(ctx)
  3. 收集 RepairRecord + Violation
  4. 返回 RepairReport

run_hard_fix(script) 是 main.py 用的便捷入口：跑 Constraint 框架。

回退机制：
  - per-Constraint feature flag: CONSTRAINT_<NAME>_ENABLED=0 关闭单个约束
  - 旧 _hard_fix_* 路径与 HARD_FIX_USE_OLD 全局开关已于 2026-09-12 删除
    （新约束只在 Constraint 框架，旧路径回退即降级；开关残留仅告警不生效）
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional, Tuple, Type

from .types import RepairReport
from .context import ConstraintContext
from .base import Constraint


# 环境故障类型：约束**根本没跑**，而不是「跑了但内容不合规」。
# 这类异常以前被宽 `except Exception` 吞掉、只留一行 warn，调用方拿到的 report
# 看起来完全正常——**约束静默消失且无人知道**。实测形态（2026-10 复核）：
# `constraints/frame_anchor_rebuild.py` → `shared.vimax_bridge`
# → `vimax.interfaces.{camera,character,...}` → `from pydantic import field_validator`；
# 环境缺 `pydantic>=2` 时该约束整轮不执行，画面锚定块不再重建，落盘稿却打 PASS。
# 失败要显式：与质量门/台词门同口径 fail-closed，禁止靠默认关闭或 warn 证明门禁闭环。
ENV_FAULT_TYPES = (ImportError, SyntaxError, NameError)


def fail_closed_types() -> tuple:
    """必须**上抛**的异常类型：约束没跑完，调用方不得拿到一份「看起来正常」的报告.

    2026-10-28（M07）：除环境故障外，**合同不可消费**也要 fail-closed。实测形态：
    `voice_adaptation` 判改编块不可消费（`AdaptationInvalidError`），它从
    `narration_subtitle_consistency` / `dialogue_density` 里抛出，被本模块的宽
    `except Exception` 记一行 warn 后继续——结果 `finalize` 返回**空错误**，
    同一条输入因为「带不带 novel 文本」产出两个不同的持久化对象
    （`narration` 155 字 vs 400 字），提交入口与离线工具因此各写一份。
    合同读不出来 = 约束没有生效，必须显式失败。
    """
    types = list(ENV_FAULT_TYPES)
    try:
        from shared.voice_adaptation import (AdaptationConfigError,
                                             AdaptationInvalidError)
        types.extend([AdaptationInvalidError, AdaptationConfigError])
    except Exception:
        pass
    try:
        from shared.presentation_contract import PresentationContractError
        types.append(PresentationContractError)
    except Exception:
        pass
    return tuple(types)


def _raise_env_fault(where: str, exc: BaseException) -> None:
    """环境故障显式上报（先留可定位的一行 ERROR，再原样抛出保留原始 traceback）."""
    try:
        from shared.tee_logger import get_logger
        _log = get_logger()
        if _log is not None:
            _log.error(
                f"[CONSTRAINT] {where} 无法执行：环境故障 {type(exc).__name__}: {exc}"
                f"——该约束本轮**没有生效**，禁止带着缺失的修复继续（fail-closed）"
            )
    except Exception:
        # 日志不可用时也必须抛；这里只保证「先留痕」尽力而为，不改变失败语义
        pass
    raise exc


# ============================================================================
# 全局注册表（Phase 2+ 逐个填入 29 个 Constraint 子类）
# ============================================================================
ALL_CONSTRAINTS: list = []


def apply_constraints(
    ctx: ConstraintContext,
    *,
    only: Optional[Tuple[Type[Constraint], ...]] = None,
    skip: Optional[Tuple[Type[Constraint], ...]] = None,
    on_constraint_done: Optional[Callable[[str, Any], None]] = None,
) -> RepairReport:
    """执行所有 Constraint，返回 RepairReport.

    Args:
        ctx: 约束执行上下文
        only: 仅运行这些 Constraint 类（白名单）
        skip: 跳过这些 Constraint 类（黑名单）

    Returns:
        RepairReport 含 repairs / violations_remaining / duration_ms
    """
    # 2026-09-12：HARD_FIX_USE_OLD 紧急回退已随旧 _hard_fix_* 路径删除——
    # 该开关在此处原语义是「返回空 report 什么都不修」，旧路径删除后会变成
    # 静默空转，比降级更危险，故一并移除（run_hard_fix 侧保留告警）。

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
            # v6.80：阶段屏障钩子——每个约束执行后回调，供写入跟踪使用
            # （见 shared/semantic_barrier.py）。默认 None = 零行为变更。
            if on_constraint_done is not None:
                on_constraint_done(c.name, ctx.script)
        except Exception as e:
            # 环境故障 = 约束没跑 ⇒ 显式失败；约束自身的逻辑异常仍按原语义
            # 「保留原版 + warn」继续（那是内容/实现问题，不该让整章崩掉）。
            if isinstance(e, fail_closed_types()):
                _raise_env_fault(f"约束 {c.name}", e)
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
# main.py 集成入口
# ============================================================================

# 日志只声明一次当前硬修复路径（08-28 排障遗留；现仅一条路径，保留单次声明）。
_PATH_ANNOUNCED = False


def _announce_path() -> None:
    global _PATH_ANNOUNCED
    if _PATH_ANNOUNCED:
        return
    _PATH_ANNOUNCED = True
    try:
        from shared.tee_logger import get_logger
        log = get_logger()
        if log is None:
            return
        log.info("[HARD_FIX] 本轮路径: Constraint 框架")
    except Exception:
        pass


def run_hard_fix(
    script: Any,
    *,
    is_in_repair_loop: bool = False,
    is_final_pass: bool = False,
    return_report: bool = False,
    novel_text: str = "",
    on_constraint_done: Optional[Callable[[str, Any], None]] = None,
    repair_effects: Any = None,
) -> Any:
    """main.py 用的便捷入口：跑硬修复层.

    Args:
        script: 完整 script dict
        is_in_repair_loop: True=main.py:1055 调用（repair round 末尾）
        is_final_pass: True=main.py:1664 调用（_compute_timecodes 后）
        novel_text: 小说原文（用于 grounding 类约束，如 dialogue_grounding / dialogue_density 注入）
        on_constraint_done: 每个约束执行后的回调（阶段屏障 / 效果账本的 writer 节点）
        repair_effects: **只读**的「已授权效果」视图（`constraints.types.RepairEffectView`）.
            有它时约束才能知道「本轮已被授权删掉的布局条目」；没有时（缺省）约束
            行为**逐字不变**。这里只负责把它挂到 context 上，不做任何判断。

    Returns:
        修改后的 script（直接返回 script）；
        return_report=True 时返回 (script, RepairReport)

    2026-09-12：旧 _hard_fix_* 正则路径（HARD_FIX_USE_OLD=1 回退）已删除——
    新约束（kf_timeline_normalize/duplicate_shot_merge/char_side_continuity 等）
    只存在于 Constraint 框架，旧路径早已功能落后，回退即降级。若仍设置该环境
    变量仅告警一次并按新框架执行。

    Context 参数（v6.42 接入）：
        - is_in_repair_loop: 标记 repair loop 末尾，避免某些约束在 repair 阶段误触
        - is_final_pass: 标记 _compute_timecodes 之后，确保 RuntimeExtendConstraint 时长拉伸生效
    """
    if os.getenv("HARD_FIX_USE_OLD", "").strip() == "1":
        try:
            from shared.tee_logger import get_logger
            get_logger().warn(
                "[HARD_FIX] HARD_FIX_USE_OLD=1 已失效：旧 _hard_fix_* 路径已于 "
                "2026-09-12 删除（新约束只在 Constraint 框架，旧路径回退即降级）。"
                "按 Constraint 框架执行。请 unset 该环境变量。"
            )
        except Exception:
            pass
    _announce_path()
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
            novel_text=novel_text,
        )
        # §14 P1：把**只读**效果视图挂到 context 上（缺省 None = 逐字不变）。
        # 走 context 而不是给 34 个约束各加一个构造参数；视图是冻结的，约束只读。
        if repair_effects is not None:
            ctx.repair_effects = repair_effects
        # v6.80：仅在真的传了钩子时才带参调用——保持默认调用形状不变，
        # 避免破坏 `apply_constraints = lambda ctx: report` 这类 monkeypatch
        # （带关键字参数会 TypeError，并被下方宽 except 静默吞掉）。
        if on_constraint_done is not None:
            report = apply_constraints(ctx, on_constraint_done=on_constraint_done)
        else:
            report = apply_constraints(ctx)
        # 日志：报告修复次数 + HARD 未解决数
        try:
            from shared.tee_logger import get_logger
            log = get_logger()
            # get_logger() 声明返回 Optional[TeeLogger]，未初始化时（pytest / 非 main
            # 调用）返回 None。此前直接 log.info(...) 抛 AttributeError，被下面的
            # except 吞掉——**恰好把本段唯一的交付物（warn 明细）静默掉**，与它要修
            # 的缺陷同形。兜底到 print（GBK 安全：仅 ASCII + 中文）。
            def _emit(line: str, *, warn: bool = False) -> None:
                if log is not None:
                    (log.warn if warn else log.info)(line)
                else:
                    print(line)

            if report.repairs:
                counts = report.repairs_by_constraint
                top = ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:5])
                _emit(f"[CONSTRAINT] 修复 {len(report.repairs)} 处 (top: {top})")
            # v6.62.6（09-09）把 action='warn' 的记录**打出来**。
            # 此前引擎只打聚合计数（「修复 N 处 (top: ...)」）和 HARD 未解决项，
            # warn 类记录只存在于内存 report 里，落不到任何人能看到的地方——
            # 于是 v6.62.4 按用户决策加的「W6 拉伸倍数过大只告警、不触发重生成」
            # 成了死功能：09-08 实测正片拉伸 1.39x（阈值 1.20）却零告警输出。
            # 告警是这类「只观测不干预」决策的**唯一交付物**，不打出来等于没做。
            #
            # v6.62.7（09-09）改「按记录取前 N」为**按约束分组、每类打 1 条样例**：
            # 逐条截断会被高频约束刷屏——实测同一轮 18 条 warn 里 14 条来自
            # frame_layouts_required，前 8 条几乎全被它占满，而真正稀有且高价值的
            # 那几条（W6 拉伸超阈、对白密度 10%、开篇无角色）正好排在后面被吃掉。
            # 告警的价值与出现次数成反比，分组后每类保底可见 1 条 + 总数。
            warns = [r for r in report.repairs if getattr(r, "action", "") == "warn"]
            if warns:
                grouped: dict[str, list] = {}
                for r in warns:
                    grouped.setdefault(r.constraint_name, []).append(r)
                for cname, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
                    head = items[0]
                    _where = f"shot{head.shot_index}" if head.shot_index is not None else "script"
                    _more = f"（另 {len(items) - 1} 处）" if len(items) > 1 else ""
                    _emit(f"[CONSTRAINT-WARN] {cname}@{_where}{_more}: "
                          f"{getattr(head, 'detail', '') or head.target}", warn=True)
                _emit(f"[CONSTRAINT-WARN] 合计 {len(warns)} 条告警 / {len(grouped)} 类约束",
                      warn=True)
            hard_unresolved = report.hard_unresolved
            if hard_unresolved:
                _emit(
                    f"[CONSTRAINT] HARD 未解决 {len(hard_unresolved)} 处 (建议 regen): "
                    + ", ".join(f"{v.constraint_name}@shot{v.shot_index}" for v in hard_unresolved[:5]),
                    warn=True,
                )
        except Exception:
            pass
        # v6.98（2026-09-22 审核 P0-4 复核确认）：**run_hard_fix 里不再调 LLM**。
        #
        # 原实现在约束循环**全部结束之后**调 `trigger_regen_and_merge()`——也就是
        # `on_constraint_done` 取完最后一个快照之后。后果不是一个字段的问题：
        #   · `TimeFieldWriteTracker`（阶段屏障）看不见它的任何时间写入；
        #   · `preserved.update(new_shot)` 可覆盖任意字段，hook 只要在输出里出现就
        #     **整对象替换**，characters 按 ident 新增/覆盖——目标集合、字段授权、
        #     blocking 身份、内容清单**一个门都没有**；
        #   · merge 之后不重跑约束链、不重算语义 revision、不观察 tracker 终态。
        # `REGEN_AUTO_TRIGGER` 当前在 `.env.content` 里未启用（默认 0），所以这是一条
        # **已接线但未通电**的生产路径——不能靠「默认关闭」证明门禁闭环。
        #
        # 审核建议的首选修法：确定性 hard-fix **只产出结构化违例**，LLM 侧统一交给
        # main 中已有的事务式修复/收敛管线（`_repair_shot_warnings` /
        # `_regen_over_limit` / `_regen_structural_over_limit`——那里有目标集合、字段
        # 授权、不退化验收、逐镜 revision 快照）。`report.hard_unresolved` 已被主流程
        # 消费，`regen_trigger` 的 LLM 能力保留给显式调用方与测试。
        return (script, report) if return_report else script
    except Exception as e:
        # 环境故障一路向上抛（见 ENV_FAULT_TYPES）：硬修复层整体没跑却返回
        # 「原版 + 空 report」，等于把「什么都没修」伪装成「无需修复」。
        if isinstance(e, fail_closed_types()):
            _raise_env_fault("硬修复层（run_hard_fix）", e)
        try:
            from shared.tee_logger import get_logger
            get_logger().warn(f"[CONSTRAINT] 新框架失败: {e}，保留原版")
        except Exception:
            pass
        return (script, RepairReport()) if return_report else script


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
