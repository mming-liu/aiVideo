# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.commit —— 提交、采纳凭据、finalize、钩子质量门.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import os
import copy
import json

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
from step01_script_gen.orchestrator import regen as _mrg
from step01_script_gen.orchestrator import final_audit as _mfa
from step01_script_gen.orchestrator import repair_accept as _mrc
from step01_script_gen.orchestrator import finalize as _mfz
from step01_script_gen.orchestrator import repair_round as _mrr
from step01_script_gen.orchestrator import persist as _mps
from step01_script_gen.orchestrator import blocked_ledger as _mbl
from step01_script_gen.orchestrator import generation as _mgn
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _write_blocked_manifest(revision: str, *, run_id: str, detail: str,
                            blocking: list, audit_status: str = "") -> None:
    """BLOCK 时只写**带 run id 的诊断 manifest**，不写三份产物.

    写 BLOCK manifest（而不是留着上一次 COMMITTED manifest）是刻意的：留着旧
    manifest 会让下游以为「上一版仍然有效」，于是带着本轮未解决的 blocking 继续
    生图。`verify_step01_commit_manifest` 见 `status != COMMITTED` 直接拒绝。
    """
    from shared.quality_gate import (build_step01_commit_manifest,
                                     write_step01_commit_manifest)
    try:
        manifest = build_step01_commit_manifest(
            run_id=run_id, created_at=_mrt._step01_now(), revision=revision,
            rule_source_fingerprint=_mrt.RULE_SOURCE_FINGERPRINT,
            rule_version=_mrt.RULE_VERSION, audit_scope=_mrt.STEP01_AUDIT_SCOPE,
            prompt_source_fingerprint=_mrt.PROMPT_SOURCE_FINGERPRINT,
            chapter_dir=_mrt.CHAPTER_DIR, status="BLOCK",
            blocking=blocking, detail=detail, audit_status=audit_status)
        write_step01_commit_manifest(_mrt.CHAPTER_DIR, manifest)
    except Exception as exc:
        _mrt._logger.warn(f"[COMMIT] BLOCK manifest 写入失败（不影响结论）: {exc}")


def _create_adoption_receipt(revision: str, run_id: str) -> str:
    """首次采纳：按现有 schema 生成凭据，**先验后写**，通过才落正式文件.

    缺失的身份信息显式写 null/说明，不编造；核验失败直接返回 create_failed，
    **不产生任何文件写入**——绝不留一份自称 adopted_and_verified 却没核验过的凭据。

    为什么不是"先写 .tmp 再 verify、失败删掉"（2026-11-14 改）：失败路径上的那次
    删除会把真正的失败原因顶掉。实测形态：`verify` 报 chapter_dir_mismatch → 走
    unlink → 在带删除审计的环境里进程被审计中断，调用方看到的是退出码 1，而
    "凭据为什么建不出来"这句话根本没进日志。核验不需要文件在盘上（verify 只核
    env/路径/摘要/清单），所以结构校验与身份核验都可以在内存里做完。
    """
    import hashlib as _hashlib
    from pathlib import Path as _Path
    from tools import production_context as _pc

    ch = _Path(_mrt.CHAPTER_DIR)
    formal = {}
    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        f = ch / name
        if not f.exists():
            return "create_failed:missing:%s" % name
        formal[name] = _hashlib.sha256(f.read_bytes()).hexdigest()
    from shared.config import duration_band
    _band = duration_band()
    receipt = {
        "schema": _pc.RECEIPT_SCHEMA,
        "status": "adopted_and_verified",
        "formal_chapter": str(ch),
        "output_base_override": str(_Path(os.getenv("OUTPUT_BASE") or ch.parent.parent.parent)),
        "script_revision": revision,
        "source_run_id": run_id,
        "adopted_at": _mrt._step01_now(),
        "scope": {"novel": os.getenv("NOVEL_NAME", ""),
                  "book_index": int(os.getenv("BOOK_INDEX", "1") or 1),
                  "book_name": os.getenv("BOOK_NAME", ""),
                  "chapter": int(os.getenv("NOVEL_CHAPTER", "1") or 1)},
        "formal_sha256": formal,
        "effective_config": {"DURATION_MIN_SEC": _band["min"],
                             "DURATION_MAX_SEC": _band["max"],
                             "DURATION_TARGET_MIN_SEC": _band["target_min"],
                             "DURATION_TARGET_MAX_SEC": _band["target_max"]},
        "authorization": {"adopted_by": "step01_commit",
                          "run_id": run_id,
                          "rule_version": _mrt.RULE_VERSION},
        "previous_receipt": None,
    }
    try:
        _pc.validate_receipt(receipt)
        _pc.verify(receipt, apply=False)
    except Exception as exc:                                  # noqa: BLE001
        # 带上稳定 code（ContextError.code）：调用方与日志按 code 判读，
        # 不必从中文消息里猜是哪一类失败。
        _code = str(getattr(exc, "code", "") or exc.__class__.__name__)
        return "create_failed:%s: %s" % (_code, exc)
    tmp = ch / "step01_adoption.json.tmp"
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ch / "step01_adoption.json")
    return "created"


def _sync_adoption_receipt(revision: str, run_id: str) -> str:
    """S06：COMMITTED 后把采纳凭据与新产物同步（同一事务内）.

    只更新**已存在**的凭据：首次采纳凭据由正式发布流程产生（本仓库只有消费端），
    在缺少契约生产端时凭空造一份会把"谁采纳的、依据什么"变成编造。返回状态串，
    写失败直接抛错——由调用方回滚整组发布。

    更新字段与 production_context 的消费口径一致：script_revision / source_run_id /
    adopted_at / formal_sha256（**SHA-256**，与 verify 的 _sha256_file 对齐）。
    """
    import hashlib as _hashlib
    from pathlib import Path as _Path

    p = _Path(_mrt.CHAPTER_DIR) / "step01_adoption.json"
    if not p.exists():
        return _create_adoption_receipt(revision, run_id)
    receipt = json.loads(p.read_text(encoding="utf-8"))
    formal = dict(receipt.get("formal_sha256") or {})
    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        f = _Path(_mrt.CHAPTER_DIR) / name
        if not f.exists():
            raise RuntimeError("采纳凭据更新缺少正式产物: %s" % name)
        formal[name] = _hashlib.sha256(f.read_bytes()).hexdigest()
    receipt["formal_sha256"] = formal
    receipt["script_revision"] = revision
    receipt["source_run_id"] = run_id
    receipt["adopted_at"] = _mrt._step01_now()
    receipt["status"] = "adopted_and_verified"
    # 时长带必须跟着新产物走：凭据里的 effective_config 是 verify() 的核验输入，
    # 沿用旧带会让"新稿 + 旧配置"通过不了导入顺序检查（也会误导下游）。
    from shared.config import duration_band as _db
    _b = _db()
    receipt["effective_config"] = {"DURATION_MIN_SEC": _b["min"],
                                   "DURATION_MAX_SEC": _b["max"],
                                   "DURATION_TARGET_MIN_SEC": _b["target_min"],
                                   "DURATION_TARGET_MAX_SEC": _b["target_max"]}
    atomic = p.with_suffix(".json.tmp")
    atomic.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    from tools import production_context as _pc2
    try:
        _pc2.validate_receipt(receipt, path=str(atomic))
    except Exception as exc:                                  # noqa: BLE001
        try:
            atomic.unlink()
        except OSError:
            pass
        raise RuntimeError("采纳凭据核验失败（不替换旧凭据）: %s" % exc) from exc
    old_bytes = p.read_bytes()
    atomic.replace(p)
    try:
        _pc2.load_receipt(p)
        _pc2.verify(receipt, apply=False)
    except Exception as exc:                                  # noqa: BLE001
        restore = p.with_suffix(".json.tmp")
        restore.write_bytes(old_bytes)
        restore.replace(p)
        raise RuntimeError("采纳凭据核验失败（已回退旧凭据）: %s" % exc) from exc
    return "updated"


def _commit_step01_outputs(script: dict, novel_text: str, *,
                           plan: "dict | None" = None,
                           hook_ctx: "dict | None" = None,
                           audit_model: "str | None" = None,
                           label: str = "落盘前",
                           source_revision: str = "",
                           source_aliases: "dict | None" = None,
                           bind_identity: bool = False) -> tuple:
    """**唯一最终提交点**：三条入口（正常 / 缓存 / BEST-GUARD）都只走这里.

    `source_revision` / `source_aliases`：BEST-GUARD 选中的**历史快照自身**的
    revision 与各版本身份（选稿边界算好带过来的，v6.129 / 2026-09-30）。它可能与
    本函数入口的 revision 不同（大纲覆盖会改哈希），也不等于修后 revision——
    BLOCK 时三者都要登记，否则选稿那边永远查不到这份欠账。

    Returns:
        `(script, summary)`。`summary["status"]` 是 `COMMITTED` / `BLOCK`；
        BLOCK 时正式三份产物**未被替换**，只写了 BLOCK manifest 与诊断快照。
    """
    from shared.quality_gate import (SCRIPT_REVISION_VERSION,
                                     build_step01_commit_manifest,
                                     compute_script_revision,
                                     ensure_script_revision,
                                     require_semantic_gate,
                                     verify_step01_commit_manifest,
                                     write_step01_commit_manifest)

    # 入口即复制（2026-10-28）：本函数会就地规范化稿件（例如按开关 pop 掉 hook），
    # 而失败路径要求**调用方手里的基线一字不变**——原实现直接把调用方的 dict 改掉，
    # 一次 finalize 失败就会把被保护基线换成「改了一半」的版本。返回值是副本，
    # 调用方必须使用返回值（三条入口本来就只看返回值）。
    script = copy.deepcopy(script)

    run_id = _mrt._step01_run_id()
    summary: dict = {"status": "BLOCK", "run_id": run_id, "audit_repair_rounds": 0,
                     # v6.133：修复轮结论（improved / no_progress / call_failed /
                     # patch_unusable / oversized_group）——"零改善"不再吞掉
                     # "修复调用失败"与"响应不可用"这两类
                     "repair_outcome": "",
                     "blocking": [], "reused_audit": False, "detail": ""}
    _mrt._metrics_inc("commit.attempts")

    # S36：提交前硬门。身份缺失可由此刻生效 scope 补齐；已有值与 scope 冲突
    # 必须拒收，不能用「补正确值」覆盖错章/错部。此检查在质量门与审计之前，
    # 保证三份产物、revision 与执行绑定看到同一身份。
    from shared.run_scope import ensure_script_identity
    _identity_issues = (ensure_script_identity(script) if bind_identity else [])
    if _identity_issues:
        _identity_revision = ""
        try:
            _identity_revision = compute_script_revision(script)
        except Exception:  # noqa: BLE001 - 诊断路径，不让哈希失败掩盖身份问题
            pass
        detail = "章节身份校验失败：%s" % "；".join(
            str(item.get("message") or item.get("code")) for item in _identity_issues)
        _mrt._logger.error(f"[COMMIT] x {detail}")
        _write_blocked_manifest(_identity_revision, run_id=run_id, detail=detail,
                                blocking=_identity_issues, audit_status="not_run")
        summary["blocking"] = _identity_issues
        summary["detail"] = detail
        return script, summary

    # M2.3 / CP2（2026-11-16）：**候选已生成并 finalize**——阶段边界即落盘。
    # 之后任何阶段被预算/网络/上限拦下，都不必重新生成正文。
    from shared import step01_checkpoint as _ckpt
    try:
        summary["checkpoint_cp2"] = _ckpt.write(
            _mrt.CHAPTER_DIR, _ckpt.CP2_CANDIDATE_GENERATED, script=script,
            extra={"run_id": run_id, "label": label,
                   "source_revision": source_revision}, log=_mrt._logger)
    except Exception as _degrade_exc:  # noqa: BLE001
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:_commit_step01_outputs", _degrade_exc)

    # v6.125（用户 2026-09-25 决定，第 1 项）：BLOCK 时必须**同时**登记「进入提交点
    # 的原稿 revision」。
    #
    # 只登记修后 revision 的后果（实测 run 193736）：台账新增的是修后的
    # `1d1cb454`，而被 BEST-GUARD 选中的 `script_result_20260924_215530.json`
    # 自身 revision `f434e5090d7b` **不在台账内** → 下一轮选稿照样可以选中它 →
    # 又 BLOCK 一次。台账的本意（"让选稿避开已知欠账的旧稿"）在这条路径上落空。
    #
    # 2026-09-27（用户裁决 4）：`_cross_run_best_guard` 里「全部候选都欠账 →
    # 放开限制」的兜底分支**已删除**——判据指纹未变的欠账稿不再被放回候选池
    # （没有合格历史候选时由新稿进提交门）。这条登记因此是**硬排除**，不再是
    # 只在「有别的候选时」生效的软护栏。
    _entry_revision = ""
    _entry_aliases: dict = {}
    try:
        # 2026-09-30 复核第 4 轮 P1：别名要在**这一时点**算（此刻的 `script` 是入口
        # 原稿）——后面 `script` 会被修复轮替换，再算就对不上这份欠账了。
        _entry_aliases = _mbl._revision_aliases_or_empty(script)
        _entry_revision = str(_entry_aliases.get(SCRIPT_REVISION_VERSION) or "")
    except Exception as _e:
        _mrt._logger.warn(f"[COMMIT] 进入提交点的 revision 计算失败（欠账台账少记一条）: {_e}")
    summary["entry_revision"] = _entry_revision

    # M1.2：局部修订的增量审计作用域。仅在有已 COMMITTED 基线、且确有改动镜
    # 时才启用；作用域严格等于实际改动镜（不拉邻镜），邻镜连续性由确定性检查兜底。
    _active_audit_scope: set = set()
    try:
        _baseline_script = _mfa._committed_baseline_script()
        if _baseline_script is not None:
            _changed = _mfa._changed_shot_ids(script, _baseline_script)
            if 0 < len(_changed) < len(_baseline_script.get("shots") or []):
                _active_audit_scope = _changed
                _mrt._logger.info(
                    "[COMMIT] 增量审计作用域：changed_shot_ids=%s（其余镜头仅确定性质量门全量生效）"
                    % ",".join(sorted(_active_audit_scope)))
    except Exception as _scope_err:
        _mrt._logger.warn("[COMMIT] 增量审计作用域计算失败，回退全量审计: %s" % _scope_err)

    # 统一收口：缓存稿和 BEST-GUARD 历史快照可能仍含旧钩子——归一在
    # `project_for_commit` 里做（唯一实现）；这里只保证禁用时不跑钩子质量门。

    # ---- 1. hook QC / 必要的 hook regen（允许语义写入的最后阶段之前）----
    if (_mfz._is_hook_enabled() and hook_ctx is not None and isinstance(script.get("hook"), dict)):
        try:
            _ensure_hook_quality(script, novel_text=novel_text, **hook_ctx)
        except Exception as exc:
            _mrt._logger.warn(f"[COMMIT] 钩子质量门异常（继续提交，问题会在审计里暴露）: {exc}")

    # §14 P3：**已通过验收的布局效果**跨 attempt 累积，供下面 `_finalize` 那一趟
    # `is_final_pass=True` 的 hard-fix 复用——「本轮已授权删除的条目不得按类别别名
    # 重建」这条规则因此要在**两个 pass** 上都成立，而不是只在修复轮内成立。
    # （必须声明在 `_finalize` **之前**：闭包按调用时解析，而 `_finalize` 在下面
    #   就被调了一次。）
    _effect_state: dict = {"effects": [], "bindings": {}, "revision": ""}

    def _finalize(_s):
        # §14：把「已通过验收的布局效果」交给 finalize 那趟 hard-fix。没有效果时
        # `repair_effects=None` ⇒ 约束行为**逐字不变**（三条入口共用本函数）。
        _view = None
        if _effect_state.get("effects"):
            try:
                from step01_script_gen.constraints.types import RepairEffectView
                _view = RepairEffectView(
                    round_id="finalize", source_revision=_effect_state.get("revision", ""),
                    effects=tuple(_effect_state["effects"]),
                    bindings=dict(_effect_state.get("bindings") or {}))
            except Exception as _eff_view_err:
                _mrt._logger.warn(f"[COMMIT] 布局效果视图构建失败（finalize 按无效果处理）:"
                             f" {_eff_view_err}")
                _view = None
        # 投影算法只此一份（离线交付工具调同一个函数）：复制、hook 归一、finalize、
        # 剥运行字段、绑 revision。返回错误 ⇒ 由调用方决定 BLOCK。
        _out, _errs = _mfz.project_for_commit(_s, novel_text, label=label,
                                         repair_effects=_view)
        if _errs:
            raise _mfz.FinalizeError("; ".join(_errs))
        return _out

    try:
        script = _finalize(script)
    except _mfz.FinalizeError as exc:
        # finalize **正常返回但报告了错误**：不审计、不建 staged、不写三份产物，
        # 只写 BLOCK manifest 让下游知道这一版没有通过。`script` 仍是进入时的基线
        # （`_finalize` 在副本上跑），下一次尝试拿到的不是被改了一半的稿子。
        summary["repair_outcome"] = "finalize_failed"
        summary["detail"] = str(exc)
        summary["blocking"] = [{"code": "finalize_failed", "severity": "blocking",
                                "message": str(exc)}]
        _mfa._clear_staged_dir()
        _write_blocked_manifest(_entry_revision, run_id=run_id, detail=str(exc),
                                blocking=summary["blocking"])
        _mrt._console(f"  [COMMIT] finalize 未通过：{str(exc)[:160]}")
        return script, summary

    audit = None
    quality = None
    blocking: list = []
    # v6.135（工作包 sunny-kindling-wilkinson，审核约束③）：提交层**二次校验**（辅门，
    # 防单候选/缓存路径绕过候选层门）。位置契约：hook QC、finalize 等**所有可能改动
    # narration 的步骤之后**、最终提交判定之前。破损 ⇒ 不进审计/修复轮（修复按锁定
    # 契约不可修 `narration_segment`，09-26 02:30 实损：locked_changed 死锁），
    # BLOCK 原因记 `dialogue_truncated`（与 locked_changed/零改善区分，决策点 2 口径：
    # 这是生成稿完整性问题，不触发计划重选语义）。
    _dlg_skip_audit = False
    # 台词/事件两道契约门都通过后，才能把「仅指向锁定字段」的审计项降级；
    # 否则仍按 blocking 交回上游，避免掩盖真实台词/契约破损。
    _contracts_ok = True
    # v6.135：门只核「稿件 vs 本轮计划的选材台词」。**计划覆盖面（三审 P2-1 更正）**：
    # 生成/快照两入口的计划经 `check_plan_before_generation` 保证 dialogue_lines 非空；
    # **缓存入口的旧缓存可能没有 chapter_outline——此时门不启用（兼容取舍，显式记录），
    # 这类稿件不受台词门保护**，不得引申为"三入口均受硬门保护"。要给旧缓存补保护，
    # 需先给缓存路径接计划来源（另一工作包）。
    from step01_script_gen.plan_feasibility import (check_script_dialogue_integrity,
                                                    plan_dialogue_lines)
    _dlg_plan_lines = plan_dialogue_lines(plan) if plan else []
    _dlg_ok, _dlg_broken = True, []
    if _dlg_plan_lines:
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(script, plan)
        except Exception as _e:
            # v6.135 三审 P1-1：门自身异常 = **无法证明台词完整** → fail-closed BLOCK，
            # 不放行（缓存路径只靠本门复核，放行 = 门失效时裸奔进审计/提交）。
            _dlg_ok = False
            _dlg_broken = [{"line": "", "reason": "gate_error: %s" % _e}]
            _mrt._logger.error(f"[COMMIT] 台词完整性门异常，fail-closed BLOCK：{_e}")
    if not _dlg_ok:
        _dlg_total = len(_dlg_plan_lines)
        _gate_error = bool(_dlg_broken) and str(
            _dlg_broken[0].get("reason") or "").startswith("gate_error")
        # v6.135 四审 P2：门自身异常 ≠ 稿件截断——结构化消费者按 repair_outcome/
        # blocking[0].code 统计，检查故障不得计入 dialogue_truncated（把门故障算成
        # 稿件问题会污染"截断率"口径）。独立代码 dialogue_gate_error，detail 已区分。
        summary["repair_outcome"] = "dialogue_gate_error" if _gate_error \
            else "dialogue_truncated"
        summary["detail"] = (
            ("台词完整性硬门自身异常（fail-closed 不放行）：%s"
             % str(_dlg_broken[0].get("reason") or "")[:200])
            if _gate_error else
            "台词完整性硬门：%d/%d 句破损（%s），需在候选层重取——"
            "提交前修复按锁定契约不可修 narration_segment（09-26 locked_changed 死锁）"
            % (len(_dlg_broken), _dlg_total,
               "；".join("%s（%s）" % (b.get("line", "")[:30], b.get("reason"))
                         for b in _dlg_broken[:3])))
        _mrt._logger.error(f"[COMMIT] x {summary['detail']}")
        _mrt._console(f"  [COMMIT] 台词完整性硬门 BLOCK（{len(_dlg_broken)}/{_dlg_total} 句破损）")
        blocking = [{"code": summary["repair_outcome"], "severity": "blocking",
                     "message": summary["detail"]}]
        _contracts_ok = False
        _dlg_skip_audit = True
    # v6.156（2026-09-30 复核第 5 组）：**事件履约契约同源复核**。拆分/修复/终稿都可能
    # 让「哪个镜头承载哪条节拍、说哪句台词」断开，而覆盖门与台词门**各自都能过**
    # （B1 声明由 D1 承载，成稿却在承载 B2 的镜头说 D1）。判据与候选入口**同一个
    # 函数**（不新造第二套口径）；只在计划带承载契约时生效（旧缓存沿用容忍口径）。
    if _dlg_plan_lines and not blocking:
        from step01_script_gen.plan_feasibility import check_event_contract
        try:
            # 2026-09-30 复核 P1-1：**终检也要强制声明**——删掉 `plan_id` 或整稿不填
            # `plan_refs` 时，候选入口过门不等于后续稿仍受约束（时长/结构/GAP 重生成
            # 会整稿替换）。带承载契约的计划在这里缺声明 ⇒ 显式失败。
            _ct_ok, _ct_viol, _ct_declared = check_event_contract(
                script, plan, require_declaration=True)
        except Exception as _e:
            _ct_ok = False
            _ct_viol = [{"code": "gate_error", "shot": "", "detail": str(_e)}]
            _mrt._logger.error(f"[COMMIT] 事件履约契约门异常，fail-closed BLOCK：{_e}")
        if not _ct_ok:
            summary["repair_outcome"] = "event_contract_broken"
            summary["detail"] = (
                "事件履约契约破损（B→D→镜头关系，拆分/修复后同源复核）："
                + "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                          str(v.get("detail"))[:60])
                            for v in _ct_viol[:3]))
            _mrt._logger.error(f"[COMMIT] x {summary['detail']}")
            _mrt._console("  [COMMIT] 事件履约契约 BLOCK（节拍/台词/镜头关系断开）")
            blocking = [{"code": "event_contract_broken", "severity": "blocking",
                         "message": summary["detail"]}]
            _contracts_ok = False
            _dlg_skip_audit = True
    # `audit_ok` 必须**显式**记录：只看 `audit` 是否为 None 是 fail-open ——
    # 审计「调用失败」时 payload 照样是 dict（status=request_failed），
    # `if blocking or not audit` 会放行到提交。调用失败 **不等于** 0 issue。
    audit_ok = False
    # v6.135：台词完整性门已判破损时**零轮循环**——直接落到 BLOCK 路径
    # （blocking 已带 dialogue_truncated；summary["detail"] 已写）。
    _retry_state: dict = {}   # v6.137：修复轮 new_blocking 拒绝后的跨 attempt 重试状态
    # v6.139（修复 2，九审根因 2）：跨 attempt 的「已尝试未核实」审计问题身份集
    # ——触达兜底对同一身份只记一次账； `(shot, family)` 归并措辞漂移。
    _audit_attempted: set = set()
    _prev_audit_ids: set = set()   # 上一轮审计的身份集（跨轮比对：仍在/消失/新现）
    # 2026-11-14 S06 G1：上一轮 blocking 问题对象（不只是身份）——本轮若不再报出，
    # 必须有确定性证据证明已修，否则按「模型沉默 ≠ 已修复」拒收。
    _prev_audit_issues: list = []
    # G1 的结构化关闭依据基线：上一轮修复前的稿件快照（实体出现→消失的转变
    # 只能拿两版对照，不能只看当前稿）。deepcopy 与 `_prev_audit_issues` 同步。
    _prev_audit_script: dict | None = None
    # G1 的**独立关闭依据**：上一轮确定性质量门的 blocking 身份 `(镜头, family)`
    # 与"那一轮检查是否真的跑完"。只有「同一问题的确定性检查由报出转为干净」才算
    # 关闭——历史尝试记录（`_audit_attempted`）不作依据（2026-11-14 复核）。
    _prev_det_families: set = set()
    _prev_det_checked = False
    # v6.140（修复 3，九审根因 3）：本 run **暂缓**的联动组成员——某轮零解决且
    # 失败者是联动组时整组暂缓，剩余预算继续修其他目标；组问题留待提交门按
    # BLOCK（不做半条修复）。止损从「全局停机」限定到「失败组」。
    _deferred_targets: set = set()

    def _contract_reject_reason(_s) -> str:
        """**采纳修复稿之前**的契约复核（与候选入口同一个函数）；`""` = 通过.

        2026-09-30 复核第 4 轮 P1-1：提交层的契约门位于修复循环**之前**，而修复轮
        会写 narration / 拆镜 / 删补镜头并整份替换稿件（`script = _finalize(repaired)`）。
        破损的修复稿**不采纳**（保留基线），不把「改之前是对的」当成「改之后还是对的」。
        """
        if not _dlg_plan_lines:
            return ""
        from step01_script_gen.plan_feasibility import check_event_contract
        try:
            _ok, _viol, _ = check_event_contract(_s, plan, require_declaration=True)
        except Exception as _e:
            return "gate_error: %s" % _e
        if _ok:
            return ""
        return "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                      str(v.get("detail"))[:60])
                         for v in (_viol or [])[:3])

    # v6.153（2026-10-03 复核之十九 P1）：**同一次提交循环内**的审计复用。
    # 修复事务被**整轮回退**时稿件一个字没改（`compute_script_revision` 相同），
    # 于是下一轮的审计请求与上一轮**逐字相同**——再 POST 一次就是既定规则
    # （总计划 §1.3）禁止的"同一 revision 反复抽样博一次偶然 PASS"。实测代价：
    # 同一次运行内同请求指纹两次调用，白花 133.5 秒，且换回一份**不同**的问题
    # 清单（2 条 vs 5 条），把"零改善"这个结论也搅浑。
    # 键是**整条请求的身份**（提示逐字 + 请求参数），不是 revision：稿变了、
    # 定向项变了、规则/提示版本变了、档位或通道变了，身份都会变 ⇒ 自然重审。
    # **失败**的审计不进 memo（失败不能冒充可复用结论）。
    _audit_memo: dict = {}
    # T2（2026-11-18）：strict 预审门的「healed」修复稿由**调用方显式决定**是否
    # 消费。这里只允许每轮提交消费一次，避免自愈→送审→又自愈的循环。
    _pre_audit_heal_used = False
    for _attempt in range(0 if _dlg_skip_audit else _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS + 1):
        quality = _msc._build_step01_quality_gate(script, novel_text)
        _mrt._timing_mark("commit_quality", status=quality.get("status"))
        _q_blocking = _mfa._quality_blocking_records(quality)
        _audit_focus_now = _mfa._final_audit_focus(script, quality)
        _audit_identity = _mfa._final_audit_identity(
            script, novel_text, plan, _audit_focus_now, audit_model, run_id)
        _reuse_source = ""
        audit = _audit_memo.get(_audit_identity)
        if audit is not None:
            _reuse_source = "in_loop"
            _audit_reused = True
        else:
            # S05：把**内容身份**一起交给复用判定（原文/计划/定向项/契约都要对上）
            audit = _mfa._reusable_final_audit(
                script, expected_identity=_audit_identity,
                expected_source_identity=_mfa._final_audit_source_identity(
                    script, novel_text, plan, audit_model),
                # M2.3：同稿同契约、仅定向项不同时把失配点精确归到 audit_focus
                expected_identity_without_focus=_mfa._final_audit_identity(
                    script, novel_text, plan, [], audit_model, run_id))
            _audit_reused = audit is not None
            if _audit_reused:
                _reuse_source = "disk"
                _mrt._logger.info(
                    "[COMMIT] 复用已有 final audit（revision + 规则指纹 + scope 全同且为 ok）"
                )
            else:
                audit = _mfa._run_final_semantic_audit(
                    script, novel_text, plan, model=audit_model,
                    focus=_audit_focus_now, run_id=run_id)
                # T2（2026-11-18）：strict 预审门报告 `healed` 时，修复稿只是
                # **建议**——由这里显式决定是否消费到本轮内存稿。消费后必须重算
                # 质量门/定向项/审计身份再送审，避免拿旧身份复用或错配 CP3。
                if str(audit.get("status") or "") == "pre_audit_healed":
                    _healed_script = audit.get("healed_script")
                    if isinstance(_healed_script, dict) and not _pre_audit_heal_used:
                        _pre_audit_heal_used = True
                        script = _healed_script
                        _healed_meta = {
                            "hard_before": len(audit.get("issues") or []),
                            "repairs": len((audit.get("healed_report") or {})
                                           .get("repairs") or []),
                        }
                        summary["pre_audit_healed"] = _healed_meta
                        _mrt._logger.warn(
                            "[COMMIT] 送审前确定性局部自愈已消费到本轮内存稿"
                            "（不写正式产物）：hard %d→0，repairs=%d；重算身份后送审"
                            % (_healed_meta["hard_before"], _healed_meta["repairs"]))
                        quality = _msc._build_step01_quality_gate(script, novel_text)
                        _mrt._timing_mark("commit_quality", status=quality.get("status"))
                        _q_blocking = _mfa._quality_blocking_records(quality)
                        _audit_focus_now = _mfa._final_audit_focus(script, quality)
                        _audit_identity = _mfa._final_audit_identity(
                            script, novel_text, plan, _audit_focus_now,
                            audit_model, run_id)
                        audit = _mfa._run_final_semantic_audit(
                            script, novel_text, plan, model=audit_model,
                            focus=_audit_focus_now, run_id=run_id)
                        if str(audit.get("status") or "") == "pre_audit_healed":
                            audit = {
                                **audit,
                                "status": "blocked",
                                "audit_status": "blocked",
                                "error": ("pre_audit_hard: 自愈后再次要求自愈"
                                          "（拒绝循环，保持 blocked）"),
                                "pre_audit_status": "unresolved",
                            }
                    else:
                        audit = {
                            **audit,
                            "status": "blocked",
                            "audit_status": "blocked",
                            "error": ("pre_audit_hard: 自愈稿不可用或本轮已自愈过"
                                      "（拒绝循环，保持 blocked）"),
                            "pre_audit_status": "unresolved",
                        }
                _mrt._timing_mark("commit_audit", status=audit.get("status"))
                # 只把**真的跑出结论**、且身份可复核的审计放进 memo。
                _memo = _mfa._memoizable_final_audit(audit)
                if _memo is not None:
                    _audit_memo[_audit_identity] = _memo
        if _reuse_source == "in_loop":
            summary["reused_audit"] = True
            _mrt._logger.info(
                "[COMMIT] 复用**本轮已完成**的 final audit（稿件与请求逐字未变，不再调用）："
                "身份 %s" % _audit_identity[:16]
            )
        elif _reuse_source == "disk":
            summary["reused_audit"] = True
        # v6.112：每轮审计都进诊断台账（含复用轮），供区分「跨镜顺带修好」
        # 与「同一内容 severity 波动」——见 `_record_audit_diag`。
        _mfa._record_audit_diag(audit, phase="commit_round", round_index=_attempt + 1,
                           reused=_audit_reused, run_id=run_id, script=script,
                           reuse_source=_reuse_source)
        # 审计失败（超时/请求失败/解析失败）**不等于 0 issue** —— 直接 BLOCK。
        if str(audit.get("status") or "").strip().lower() != "ok":
            summary["detail"] = (
                "final semantic audit %s: %s" % (audit.get("status"), audit.get("error"))
            )
            _mrt._logger.warn(f"[COMMIT] x {summary['detail']}（调用失败 != 0 issue，直接 BLOCK）")
            break
        audit_ok = True
        # M2.3 / CP3（2026-11-16）：**首轮审计完成**——阶段边界即落盘（含复用轮）。
        # 只记首轮：后续轮次由 CP4（修复轮）承担，避免同一份稿反复写大文件。
        if _attempt == 0:
            from shared import step01_checkpoint as _ckpt
            try:
                summary["checkpoint_cp3"] = _ckpt.write(
                    _mrt.CHAPTER_DIR, _ckpt.CP3_INITIAL_AUDIT_DONE, script=script,
                    audit=audit, extra={"run_id": run_id, "round": 1,
                                        "reused": bool(_audit_reused)}, log=_mrt._logger)
            except Exception as _degrade_exc:  # noqa: BLE001
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("step01_script_gen/main.py:_commit_step01_outputs", _degrade_exc)
        # blocking = 审计条目 ∪ 确定性质量门条目（两者都必须被修掉才算通过）
        if _contracts_ok:
            _downgraded = _mfa._downgrade_locked_only_audit_issues(audit, contracts_ok=True)
            _stage_downgraded = _mfa._downgrade_stage_enum_false_positives(
                audit, script, contracts_ok=True)
            _delta_downgraded = _mfa._downgrade_out_of_delta_audit_issues(
                audit, _active_audit_scope, contracts_ok=True)
            if _downgraded:
                _mrt._logger.warn(
                    "[COMMIT] 本轮 %d 条审计项仅指向已过契约门的锁定字段，已降级为 warning"
                    % _downgraded)
            if _stage_downgraded:
                _mrt._logger.warn(
                    "[COMMIT] 本轮 %d 条审计项把合法 stage 枚举误判为非法，已降级为 warning"
                    % _stage_downgraded)
            if _delta_downgraded:
                _mrt._logger.warn(
                    "[COMMIT] 本轮 %d 条审计项落在未改动镜头（增量作用域），已降级为 warning"
                    % _delta_downgraded)
        blocking = _mfa._audit_blocking_records(audit) + _q_blocking
        # 2026-11-14 S06 G1（批准，仅阻塞级）+ P1/G1（完整裁决）：
        # **上一轮 blocking 本轮不再报出 ≠ 已修复**——必须有确定性关闭依据。
        # 证据是自由文本、从未字面出现在稿件里时 `_audit_issue_resolved` 返回 True
        # （无法区分），这类条目不在本闸门可判范围内，保持既有语义。
        #
        # 关闭依据只有三类，都必须**独立于"模型动过手"**：
        #   (a) 判据证据已不在稿件中（`_audit_issue_resolved`）；
        #   (b) **同一 (镜头, family) 的确定性检查**上一轮报出、本轮干净
        #       （两侧都真的跑完，checker_failed 不算）。这是"同一问题换了一个
        #       确定性判据复核通过"，不是尝试记录；
        #   (c) 问题点名的实体在修复前的目标布局里存在、修复后消失
        #       （`_audit_issue_structurally_removed`，结构化内容事实）。
        #
        # 明确**不再**接受「字段触达 / 历史尝试」当关闭依据（2026-11-14 复核）：
        # `_audit_attempted` 只记录"模型改过被点名的字段、且那一轮通过了验收"，
        # 而验收本身可以靠字段触达兜底——于是"改字段 → 修复轮接受 → 下轮审计沉默"
        # 就能豁免 G1。`_audit_attempted` 仍保留给兜底记账（同一身份只记一次），
        # 但不再参与本闸门。
        #
        # 旧豁免「判据证据缺失/过短就跳过」也已移除：缺证据不能反证已修复，
        # 落进未闭合（待复核）。
        if _prev_audit_issues:
            _now_ids_g1 = {
                _mrc._audit_issue_identity(_i)
                for _i in _mfa._audit_payload_to_issues(audit) if _mrt.is_blocking(_i)}
            _now_det_families = {_mrc._record_family_identity(_r) for _r in _q_blocking
                                 if isinstance(_r, dict)}
            _now_det_checked = not bool(quality.get("checker_failed"))
            _g1_unclosed = _mrc._g1_unclosed_issues(
                _prev_audit_issues, _now_ids_g1, script,
                prev_det_families=_prev_det_families,
                now_det_families=_now_det_families,
                det_checked=_prev_det_checked and _now_det_checked,
                prev_script=_prev_audit_script)
            if _g1_unclosed:
                for _pi in _g1_unclosed:
                    blocking.append({
                        "code": "blocking_unclosed_without_evidence",
                        "severity": "visual_risk",
                        "shot_id": getattr(_pi, "shot_id", ""),
                        "frame_id": getattr(_pi, "frame_id", "") or "",
                        "entity_id": getattr(_pi, "entity_id", "") or "",
                        "field_path": getattr(_pi, "field_path", "") or "",
                        "message": ("上一轮 blocking 本轮未再报出，但缺少确定性关闭依据"
                                    "（判据证据仍在稿件中，且同一问题的确定性检查"
                                    "本轮没有由报出转为干净——模型沉默、仅改字段、"
                                    "历史尝试都不等于已修复；G1 门禁）"),
                    })
                summary["detail"] = (
                    "G1：上一轮 %d 条 blocking 本轮未再报出且无确定性关闭依据"
                    "（证据仍在稿中或不可判，且无确定性复核通过；"
                    "blocking 必须有依据关闭）"
                    % len(_g1_unclosed))
                _mrt._logger.warn("[COMMIT] x %s" % summary["detail"])
                break
        if not blocking:
            break
        _mrt._logger.warn(
            "[COMMIT] 第 %d 轮发现 %d 条 blocking（审计 %d + 确定性质量门 %d）：%s"
            % (_attempt + 1, len(blocking), len(_mfa._audit_blocking_records(audit)),
               len(_q_blocking),
               [(b.get("shot_id"), b.get("code")) for b in blocking[:5]])
        )
        # 2026-10-28（M08）：审计包可以显式声明「只审计、不修复」。关闭传输重试
        # **不等于**关闭修复轮：修复轮会再发一次模型调用，还可能在下一轮触发
        # **第二次审计**。`STEP01_AUDIT_ONLY=1` 时本轮审计即终局——保留 blocking、
        # 不调用修复、不再发审计。默认关闭，未设该变量时行为逐字不变。
        if str(os.getenv("STEP01_AUDIT_ONLY", "0")).strip() == "1":
            summary["repair_outcome"] = "audit_only"
            summary["detail"] = (
                "STEP01_AUDIT_ONLY=1：保留 %d 条 blocking，未进入修复轮（不再发调用/审计）"
                % len(blocking))
            _mrt._logger.warn(f"[COMMIT] {summary['detail']}")
            break
        # v6.139（修复 2）：审计问题**跨轮身份比对**——按 (shot, family) 归并措辞
        # 漂移（同一问题四轮四名是实测常态），跨轮追踪「具体哪些事件矛盾还在」，
        # 不以审计条数判断收敛。
        _audit_issue_recs = _mfa._audit_payload_to_issues(audit)
        _now_ids = {_mrc._audit_issue_identity(i) for i in _audit_issue_recs
                    if _mrt.is_blocking(i)}
        if _prev_audit_ids:
            _still = _now_ids & _prev_audit_ids
            _gone = _prev_audit_ids - _now_ids
            _fresh = _now_ids - _prev_audit_ids
            _mrt._logger.warn(
                "[COMMIT] 审计身份比对（(镜头,族) 归并措辞漂移）：仍在 %s｜消失 %s｜"
                "新现 %s%s"
                % (sorted(_still) or "无", sorted(_gone) or "无", sorted(_fresh) or "无",
                   "｜其中 %s 为已尝试未核实身份（触达兜底已停用）"
                   % sorted(_fresh & _audit_attempted) if _fresh & _audit_attempted else "")
            )
        _prev_audit_ids = _now_ids
        _prev_audit_issues = _audit_issue_recs
        try:
            import copy as _copy_g1
            _prev_audit_script = _copy_g1.deepcopy(script)
        except Exception:
            _prev_audit_script = None
        # G1 的独立关闭依据基线：本轮（修复前）确定性质量门的 blocking 身份，
        # 以及检查是否真的跑完。下一轮审计若对同一 (镜头, family) 沉默，
        # 只有"确定性检查由报出转为干净"才算有依据关闭。
        _prev_det_families = {_mrc._record_family_identity(_r) for _r in _q_blocking
                              if isinstance(_r, dict)}
        _prev_det_checked = not bool(quality.get("checker_failed"))
        if _attempt >= _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS:
            summary["detail"] = (
                "达到 %d 个修复轮上限后仍有 %d 条 blocking"
                "（审计 %d + 确定性质量门 %d）"
                % (_msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS, len(blocking),
                   len(_mfa._audit_blocking_records(audit)), len(_q_blocking))
            )
            break
        # M04：提交层的每一轮修复同样要能容纳「本轮动作 + 最终审计 + 收尾」。
        # 不足即冻结候选并给 `budget_exhausted` 明确终局，不把「时间不够」
        # 写成「内容不可行」，也不借用上一轮审计结论。
        _adm_commit = _mrt._action_budget_admission(
            "commit_repair_round", _mrt._action_cost_sec("repair"))
        if _mrt._budget_exceeded_with_pause("commit_repair_round") or not _adm_commit.get("allowed"):
            _freeze_commit = _mfa._freeze_budget_exhausted(
                script, action="commit_repair_round",
                admission=_adm_commit, run_id=run_id)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_commit)
            summary["repair_outcome"] = "budget_exhausted"
            summary["detail"] = (
                "预算不足：剩余 %.1fs < 本轮修复 %.1fs + 最终审计 %.1fs + 收尾 %.1fs；"
                "已保存冻结候选（%s），停止 final audit 修复轮"
                % (float(_adm_commit.get("remaining_budget_sec") or 0),
                   float(_adm_commit.get("action_cost_sec") or 0),
                   float(_adm_commit.get("audit_cap_sec") or 0),
                   float(_adm_commit.get("commit_reserve_sec") or 0),
                   _freeze_commit.get("output_root") or "隔离根"))
            _mrt._logger.warn(f"[COMMIT] {summary['detail']}")
            break
        # v6.119（用户口径）：进修复轮**之前**先查成对目标契约——不可执行就显式
        # BLOCK，不再花两个审计轮去博一次「也许能顺带修好」（那纯属烧审计额度）。
        _shots_by_id_rt = {f"shot_{_s.get('index')}": _s
                           for _s in (script.get("shots") or [])
                           if isinstance(_s, dict)}
        if isinstance(script.get("hook"), dict):
            _shots_by_id_rt["hook"] = script["hook"]
        # 镜头号记法归一（`"shot5"` → `shot_5`，双射、指向不变）本身在
        # `_target_id_of` 里做；这里只让它**可见**——同一份提示词下模型可能第 1 轮
        # 写 `"5"`、第 2 轮写 `"shot5"`，静默归一就没人知道输入形态变了。
        from step01_script_gen.repair_fields import shot_id_aliases
        _sid_aliases = shot_id_aliases((audit or {}).get("issues") or [])
        if _sid_aliases:
            _mrt._logger.warn("[COMMIT] 审计镜头号记法已归一（%d 处，指向不变）：%s"
                         % (len(_sid_aliases), _sid_aliases[:8]))
        _rt_problems = _mfa._audit_repair_target_problems(audit, _shots_by_id_rt)
        if _rt_problems:
            # v6.142（09-26 用户决定，P1-3）：**降级为告警，不再就地 BLOCK**。
            # 旧口径（v6.119）是「任一条目标无效 → 整条拒绝」+「有一条不可执行就
            # 整轮 BLOCK、**一轮修复都不发**」。20:25 run 实锤：shot 3 的 12 条越界
            # 目标把整轮修复全部掐掉，同轮里完全可修的其它目标一个都没动——被一条
            # 坏目标连坐。新口径：有问题的条目**保留 blocking**（下一轮审计照常
            # 重报），本轮不授权它；其余合法目标照常进修复轮。改不动 / 全是坏目标
            # 的终局，由既有「提交前修复轮零改善即停」判据给出（失败照常显式）。
            _mrt._logger.warn(
                "[COMMIT] %d 条审计条目缺少可执行的定向修复目标"
                "（保留 blocking；本轮不授权该条，本轮其余目标继续修）：%s"
                % (len(_rt_problems), _rt_problems[:3]))
        # ---- 受限修复（字段授权 + 内容保留 + 候选验收，全部走修复事务）----
        _before_rev = compute_script_revision(script)
        _repair_diag: list = []
        _repair_block_reasons: list = []
        # M2.2b（2026-11-16，2026-11-17 补双层）：**成对额度预检**。修复与"修后复审"
        # 是一对：两层（逻辑调用 / 网络尝试）任一不足以覆盖两条时，发起修复只会产出
        # 一份没人复审的孤儿稿（19:01 实测：修复 accepted 后第 3 次调用被 cap 拦下，
        # 成果只留在内存）。这里先保存当前快照再报 budget_exhausted，不发起孤儿调用。
        _paired = _mps._paired_repair_budget()
        if _paired["short"]:
            _mps._persist_repair_round(script, round_no=_attempt, audit=audit,
                                  novel_text=novel_text)
            summary["repair_outcome"] = "budget_exhausted"
            summary["budget_paired_precheck"] = _paired
            summary["detail"] = (
                "成对额度预检不通过（不足项：%s）：逻辑 修复 %s / 复审 %s / 全局 %s；"
                "尝试 修复 %s / 复审 %s / 全局 %s——不足以覆盖「1 次修复 + 1 次修后"
                "复审」→ 不发起孤儿修复；已保存当前稿快照"
                % (", ".join(_paired.get("short_on") or []),
                   _paired.get("remaining_logical_repair"),
                   _paired.get("remaining_logical_audit"),
                   _paired.get("remaining_logical_total"),
                   _paired.get("remaining_attempts_repair"),
                   _paired.get("remaining_attempts_audit"),
                   _paired.get("remaining_attempts_total")))
            _mrt._logger.warn("[COMMIT] " + summary["detail"])
            break
        repaired = _mrr._repair_shot_warnings(
            script, novel_text,
            bank=(hook_ctx or {}).get("bank"),
            objects_bank=(hook_ctx or {}).get("objects_bank"),
            model=audit_model,
            audit_issues=_mfa._audit_payload_to_issues(audit),
            max_rounds=1,          # §1.1：提交点每一轮只发一次修复调用
            repair_diag=_repair_diag,
            block_reasons=_repair_block_reasons,
            retry_state=_retry_state,
            audit_attempted=_audit_attempted,
            defer_targets=_deferred_targets,
            effect_state=_effect_state,
            # §14 P2 窄协议：审计产出的「称谓 → 已在场人物」显式关系。
            # 修复端**只**用它裁决「这条删除该不该保留」；缺它 = 未声明 ⇒ 不猜。
            audit_relations=_mfa._audit_payload_to_relations(audit),
        )
        # v6.119：修复事务诊断立刻落台账（不等提交结果）——BLOCK 路径正是最需要它
        # 的时候，而 BLOCK 时后续代码可能直接 break/return。
        _mfa._record_repair_diag(run_id=run_id, attempt=_attempt + 1,
                            records=_repair_diag)
        # v6.122（用户决定 B）：**一次改不完的联动组 → 立即 BLOCK**。
        # 这一轮没有调用 LLM、也不会再发审计（省下两轮修复 + 两次审计调用）：
        # 组超过上限时跳过它只会让同一份审计每轮重复声明、直到额度耗尽。
        if _repair_block_reasons:
            summary["repair_outcome"] = "oversized_group"
            summary["detail"] = (
                "联动修复目标超出单轮可修上限（%d 条）：%s"
                % (len(_repair_block_reasons), _repair_block_reasons[:2]))
            _mrt._logger.error(f"[COMMIT] {summary['detail']}")
            break
        summary["audit_repair_rounds"] += 1
        # 零改善：修复轮被拒或没动一个字 → 再发一轮只会重复同一批目标。
        #
        # v6.133（用户 2026-09-25）：**"模型没修好"与"修复请求没得到完整结果"必须分开报**。
        # 09-25 实跑的真实 BLOCK 直接原因是修复响应被截断（`max_output_tokens`，16k 上限含
        # 思维链 token）→ 整份丢弃 → 稿件一个字没变 → 这里只报"零改善"，把调用失败说成了
        # 内容结论。现在按台账里的 `kind="call_failed"` 记录区分，并把原因写进 detail。
        if compute_script_revision(repaired) == _before_rev:
            # v6.137：new_blocking 被拒 → 重试状态已导出（脚本回到基线，revision
            # 与被审计稿一致 ⇒ 下一轮审计**零审计调用**）。只在
            # 下一次 attempt 仍属修复预算（§1.1 最多 2 轮）时续跑。
            # v6.153：这句此前是**空头承诺**——盘上的 `_reusable_final_audit` 读的是
            # 正式产物/sidecar，而本轮刚跑完的审计**根本没写盘**，于是下一轮照旧
            # POST（实测同指纹两次调用、白花 133.5 秒）。现在由循环内的
            # `_audit_memo`（按**请求身份**索引）兜住这条路径。
            # M03：只有**结构化状态明确发放了一次答卷纠正机会**、且不是终局时
            # 才续跑。`feedback` 文本只作展示，不再充当判据；工程故障 / 重复
            # 拒因 / 一次机会已用完都在发请求之前终局。
            # 窄兼容通道：只有 v6.137 遗留格式（外部桩手写 `feedback`、完全
            # 没有 `feedback_records`）才按旧信号续跑。生产路径的
            # `_repair_shot_warnings` 一律写结构化状态，不会走这条，也不把文本
            # 当分类依据。
            _legacy_pending = (bool(_retry_state.get("feedback"))
                               and not _retry_state.get("feedback_records")
                               and not _retry_state.get("terminal"))
            _grant = (bool(_retry_state.get("correction_granted"))
                      or _legacy_pending)
            if (_grant and not _retry_state.get("terminal")
                    and _attempt + 1 < _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS):
                _mrt._logger.warn(
                    "[COMMIT] 修复轮带结构化反馈重试待跑"
                    "（codes=%s，剔除 %s，revision=%s）→ 下一轮继续"
                    % ([r.get("code") for r in
                        (_retry_state.get("feedback_records") or [])],
                       _retry_state.get("excluded"),
                       str(_retry_state.get("source_revision") or "")[:12]))
                continue
            _call_failed = [r for r in _repair_diag if r.get("kind") == "call_failed"]
            # v6.133（2026-09-26 复核补口）：**"响应不可用"与"没改动稿件"也要分开**。
            # API 调用成功但补丁 JSON 解析失败 / envelope 结构无效 / 没返回替换 /
            # 替换全被校验拒——这四条此前只打日志，提交层看不见记录，于是与
            # "补丁有效但零改善"共用一个 `no_progress`，把"收到不可用响应"说成了
            # "修复没进展"。现在它们各自落 `kind="patch_unusable"` + `reason`。
            _unusable = [r for r in _repair_diag if r.get("kind") == "patch_unusable"]
            if _call_failed:
                summary["repair_outcome"] = "call_failed"
                summary["detail"] = ("修复调用失败/响应不完整（未得到完整结果，"
                                     "不是「修复无进展」）：%s"
                                     % str(_call_failed[-1].get("error") or "调用失败")[:200])
                _mrt._logger.error(f"[COMMIT] {summary['detail']}")
            elif _unusable:
                _u_last = _unusable[-1]
                summary["repair_outcome"] = "patch_unusable"
                summary["detail"] = (
                    "修复响应不可用（拿到了完整响应、但补丁不可用，不是「修复无进展」）："
                    "%s%s" % (_u_last.get("reason") or "unknown",
                              ("｜%s" % _u_last.get("detail"))
                              if _u_last.get("detail") else ""))
                _mrt._logger.error(f"[COMMIT] {summary['detail']}")
            else:
                # `no_progress` 只表示**稿件没有改动**（且台账里没有调用失败/不可用补丁
                # 记录）；能取到上一轮事务时把它的结论一并写出，便于分辨"改完被回滚"
                # 与"根本没动"。
                _tx = [r for r in _repair_diag if "accepted" in r]
                # v6.140（修复 3，九审根因 3）：零解决发生在**真联动组**（≥2 成员，
                # `_linked_repair_groups` 会把单镜问题也折成单成员组）身上 → 止损
                # 局部化——组整组暂缓（本 run 后续轮次不再选它），剩余预算继续修
                # 其他目标；组的 blocking 保留，仍会照常 BLOCK（不做半条修复）。
                # 只有当 blocking 里还存在**组外**目标时才值得续跑，否则与直接
                # 停止等价、白烧一轮。
                _grp_members: set = set()
                if _tx:
                    for _g in (_tx[-1].get("groups") or []):
                        if len(_g) >= 2:
                            _grp_members.update(_g)
                _outside_exists = False
                if _grp_members:
                    from step01_script_gen.repair_fields import _target_id_of
                    for _b in blocking:
                        _tid = _target_id_of(str(_b.get("shot_id") or ""))
                        if _tid and _tid not in _grp_members:
                            _outside_exists = True
                            break
                if (_grp_members and _outside_exists
                        and _attempt + 1 < _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS):
                    _deferred_targets |= _grp_members
                    _mrt._logger.warn(
                        "[COMMIT] 联动组 %s 本轮零解决（reason=%s）→ 整组暂缓"
                        "（本 run 后续轮次不再选它）；组外目标（如 %s）继续用剩余"
                        "预算修复；组的 blocking 保留、仍会照常 BLOCK"
                        % (sorted(_grp_members), _tx[-1].get("reason"),
                           [b.get("shot_id") for b in blocking
                            if _target_id_of(str(b.get("shot_id") or ""))
                            not in _grp_members][:3]))
                    continue
                summary["repair_outcome"] = "no_progress"
                if _tx:
                    summary["detail"] = (
                        "提交前修复轮零改善（稿件未变；上一轮事务 accepted=%s resolved=%s "
                        "reason=%s），停止修复"
                        % (bool(_tx[-1].get("accepted")), _tx[-1].get("resolved"),
                           str(_tx[-1].get("reason") or "-")[:80]))
                else:
                    summary["detail"] = ("提交前修复轮零改善（稿件未变；台账中无调用失败/"
                                         "不可用补丁记录），停止修复")
                _mrt._logger.warn(f"[COMMIT] {summary['detail']}")
            script = repaired
            break
        try:
            _cand = _finalize(repaired)
        except _mfz.FinalizeError as exc:
            # 修复稿 finalize 失败 ⇒ **不采纳**：`script` 保持修复前基线（它自己的
            # finalize 已通过），本轮审计 blocking 与诊断原样保留；不再继续消耗调用。
            summary["repair_outcome"] = "repair_finalize_failed"
            summary["detail"] = str(exc)
            summary["repair_finalize_error"] = str(exc)
            break
        _ct_why = _contract_reject_reason(_cand)
        if _ct_why:
            # 契约破损的修复稿**不采纳**：`script` 保持在基线（本轮的审计 blocking 与
            # 这份基线同 revision，仍然有效），并显式说明「这一轮为什么白跑」。
            summary["repair_outcome"] = "contract_broken"
            summary["detail"] = (
                "修复稿破坏事件履约契约（%s）→ **不采纳**，保留基线；"
                "基线自身的 blocking 仍在（见 blocking 清单）" % _ct_why)
            _mrt._logger.error(f"[COMMIT] x {summary['detail']}")
            _mrt._console("  [COMMIT] 修复稿破坏事件履约契约 → 不采纳（保留基线）")
            break
        summary["repair_outcome"] = "improved"
        # M2.2（2026-11-16）：**先落盘再复审**——采纳即快照。修复已经花钱且通过
        # finalize/契约两道门，若随后的复审被发送上限或网络拦下，本轮成果不得丢失。
        summary["repair_round_snapshot"] = _mps._persist_repair_round(
            _cand, round_no=_attempt + 1, audit=audit, novel_text=novel_text)
        # M-A2（2026-11-17，用户复核第 3 条）：**采纳稿持久化失败必须阻断后续调用**。
        # "写失败只告警"对辅助检查点可以，对"已花钱且已采纳的修复稿"不行——继续发
        # 修后复审，只会产出一份无法恢复的结论。这里直接停，并把原因写进终局。
        if not (summary["repair_round_snapshot"] or {}).get("written"):
            _why = (summary["repair_round_snapshot"] or {}).get("error") or "unknown"
            summary["repair_outcome"] = "repair_snapshot_failed"
            summary["detail"] = (
                "修复稿已采纳但**落盘失败**（%s）→ 停止后续调用：继续复审只会产出一份"
                "无法恢复的结论。请检查磁盘/权限后重跑" % _why)
            _mrt._logger.error("[COMMIT] x " + summary["detail"])
            _mrt._console("  [COMMIT] 修复稿落盘失败 → 停止（不发起修后复审）")
            break
        script = _cand

    # ---- 写后不可变守卫：audit 前后 revision 必须相同 ----
    revision = ensure_script_revision(script)
    audit_rev = str((audit or {}).get("input_revision") or "")
    if audit and audit_rev and audit_rev != revision:
        # 审计之后又有语义写入 → 本轮审计作废（不写产物，交下一轮重来）
        summary["detail"] = (
            "final audit 之后 revision 发生变化（audit=%s current=%s）——审计作废"
            % (audit_rev[:12], revision[:12])
        )
        _mrt._logger.warn(f"[COMMIT] x {summary['detail']}")
        blocking = blocking or [{"code": "revision_changed_after_audit",
                                 "severity": "blocking", "message": summary["detail"]}]

    # S05（2026-11-11）：审计的**实际结论状态**贯通到提交摘要、提交清单与额度收据。
    # 此前它只在 BLOCK 分支里作为局部变量存在，COMMITTED 的 `step01_commit.json`
    # 因此写着空 `audit_status`——事后无法区分「审计跑成功」与「没有可用审计」。
    summary["audit_status"] = str((audit or {}).get("status") or "")

    if blocking or not audit_ok:
        summary["blocking"] = blocking
        summary["detail"] = summary["detail"] or (
            "final semantic audit 未通过" if audit_ok else "final semantic audit 未产出可用结论")
        # v6.152（C1/C2，2026-09-29 五次跑实测）：终局若是**审计调用失败**
        # （超时 / 传输耗尽 / 解析耗尽），`blocking` 还停在**上一次成功审计那一轮**
        # 的清单上——那是陈旧清单，写进 manifest 会误导读数（run 02 报 22 条、
        # run 04 报 6 条，真因却是调用失败），登记进欠账台账更会把一次**临时故障**
        # 变成稿件的**长期语义欠账**，污染 BEST-GUARD 选稿。
        # 处置：① manifest 只记本次失败这一条（`blocking_codes` 不再陈旧）；
        #      ② 陈旧清单降级为 `summary` 里的**诊断**字段，不进 manifest；
        #      ③ **不登记欠账**——「没有可用审计结论」不等于「稿件有这些欠账」。
        # 正式产物 `step01_audit.json` 依旧不动（与另两份同 revision）；本次是否
        # 审出来了由 manifest 的 `audit_status` 与 blocked sidecar 承担。
        _stale_blocking = blocking          # 只在审计调用失败时降级为诊断字段
        blocking, _audit_status, _audit_call_failed = _mfa._terminal_blocking_records(
            audit, blocking)
        summary["blocking"] = blocking
        # S05：终局状态以**本次实际审计**为准（含「调用失败」这类非 ok 值）
        summary["audit_status"] = _audit_status
        if _audit_call_failed:
            summary["last_known_blocking"] = _stale_blocking
            summary["last_known_blocking_note"] = (
                "审计调用失败（%s）——上述清单来自**上一次成功审计那一轮**，"
                "仅作诊断，不是本次结论" % _audit_status)
        _mrt._metrics_inc("commit.blocked")
        if _audit_call_failed:
            _mrt._logger.warn(
                "[COMMIT] 审计调用失败（%s）——本次**不登记欠账台账**："
                "「没有可用审计结论」不是稿件的语义欠账，登记会污染选稿台账；"
                "陈旧清单已降级为诊断字段 last_known_blocking（%d 条）"
                % (_audit_status, len(_stale_blocking)))
        else:
            _debt_issues = _mbl._debt_issue_records(blocking)
            _mbl._record_blocked_revision(revision, issues=_debt_issues,
                                     versions=_mbl._revision_aliases_or_empty(script))
            # v6.125（第 1 项）：同时登记**原稿** revision——被选中的历史稿本身才是
            # 「已知欠账的那一份」，只记修后版本等于把它留在候选池里反复入选。
            if _entry_revision and _entry_revision != revision:
                _mbl._record_blocked_revision(_entry_revision, issues=_debt_issues,
                                         versions=_entry_aliases)
                _mrt._logger.warn(
                    "[COMMIT] 欠账台账同时登记进入提交点的原稿 revision %s"
                    "（修后 %s）——否则该历史稿会因漏记而在后续选稿中再次入选"
                    % (_entry_revision[:12], revision[:12]))
            # v6.129（用户 2026-09-25 根因）：**选稿边界**记录的快照自身 revision 也要
            # 登记。它与入口/修后 revision 都可能不同（大纲覆盖参与哈希），而 guard 下次
            # 恰恰是用这个值查台账——不记它，同一份欠账稿会第三次被选中。
            if source_revision and source_revision not in (revision, _entry_revision):
                _mbl._record_blocked_revision(source_revision, issues=_debt_issues,
                                         versions=dict(source_aliases or {}))
                _mrt._logger.warn(
                    "[COMMIT] 欠账台账登记**被选中的历史快照自身** revision %s"
                    "（提交入口 %s / 修后 %s）——BEST-GUARD 用快照自身 revision 判欠账"
                    % (source_revision[:12], (_entry_revision or "")[:12], revision[:12]))
        _mfa._write_blocked_audit_sidecar(audit)
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=blocking,
                               audit_status=_audit_status)
        _mfa._clear_staged_dir()
        try:
            _mps._snapshot_run_artifacts(script, audit=audit, novel_text=novel_text)  # 诊断证据 + 不可变配对
        except Exception:
            pass
        _mrt._console(f"  [COMMIT] x BLOCK：{summary['detail']}")
        _mrt._console("  [COMMIT] 三份正式产物未替换（step01_commit.json 标记为 BLOCK）")
        return script, summary

    # ---- staged 三件套 + 真自验 ----
    stage_dir = _mrt.CHAPTER_DIR / "_staged"
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json
        staged = {}
        for _name, _payload in (("step01_audit.json", audit),
                                ("step01_quality.json", quality),
                                (_mrt.OUTPUT_FILE.name, _msc._strip_runtime_fields(script))):
            _path = stage_dir / _name
            atomic_dump_json(_path, _payload, ensure_ascii=False, indent=2)
            staged[_name] = _path
        # 用**真实** gate 函数读 staged 文件——不是 mock，也不是自己重写一遍判据。
        staged_script = json.loads(staged[_mrt.OUTPUT_FILE.name].read_text(encoding="utf-8"))
        require_semantic_gate(
            staged["step01_quality.json"], staged["step01_audit.json"],
            staged_script, stage="step01")
    except Exception as exc:
        summary["detail"] = f"staged 自验失败：{exc}"
        _mrt._metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=blocking)
        _mfa._clear_staged_dir()
        _mrt._console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    # ---- 发布事务（S06）：一次事务覆盖**全部**发布步骤 ----
    # 单个文件的原子写不能保证"整组同时切换"：进程在写之间被杀，会留下
    # 新 audit + 旧 script 的身份不一致组合。这里先备份、再写、验完再 commit；
    # **manifest 构造与写入也在事务内**——它抛错同样要回滚，不能留下半套发布。
    from shared.publish_txn import PublishTransaction
    _expected_old = None
    if _mrt.OUTPUT_FILE.is_file():
        try:
            _old_obj = json.loads(_mrt.OUTPUT_FILE.read_text(encoding="utf-8"))
            if isinstance(_old_obj, dict):
                _expected_old = compute_script_revision(_old_obj)
        except Exception:
            _expected_old = None
    _txn = PublishTransaction(_mrt.CHAPTER_DIR, expected_old=_expected_old,
                              label="step01:%s" % run_id)
    manifest = None
    try:
        _txn.begin()
        from shared.json_io import atomic_dump_json
        atomic_dump_json(_mrt.CHAPTER_DIR / "step01_audit.json", audit,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(_mrt.CHAPTER_DIR / "step01_quality.json", quality,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(_mrt.OUTPUT_FILE, _msc._strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
        # S44/P1：必需计划缓存与三件套同事务发布；失败则回滚提交。
        _plan_for_cache = plan if isinstance(plan, dict) and plan else script.get("chapter_outline")
        _cache_book = str(script.get("book_name") or os.getenv("BOOK_NAME") or "").strip()
        _cache_chapter = script.get("chapter_index")
        if not isinstance(_cache_chapter, int) or isinstance(_cache_chapter, bool):
            _cache_chapter = getattr(_mrt, "NOVEL_CHAPTER", None)
        if (isinstance(_plan_for_cache, dict) and _plan_for_cache and _cache_book
                and isinstance(_cache_chapter, int) and not isinstance(_cache_chapter, bool)):
            from shared.plan_cache import FILENAME as _PLAN_CACHE_FILENAME, write_plan_cache
            write_plan_cache(
                _mrt.CHAPTER_DIR / _PLAN_CACHE_FILENAME, _plan_for_cache,
                scope={"book_name": _cache_book, "chapter": _cache_chapter},
                script_revision=revision)
        # commit manifest 是「提交完成」的唯一标志
        manifest = build_step01_commit_manifest(
            run_id=run_id, created_at=_mrt._step01_now(), revision=revision,
            rule_source_fingerprint=_mrt.RULE_SOURCE_FINGERPRINT,
            rule_version=_mrt.RULE_VERSION, audit_scope=_mrt.STEP01_AUDIT_SCOPE,
            prompt_source_fingerprint=_mrt.PROMPT_SOURCE_FINGERPRINT,
            chapter_dir=_mrt.CHAPTER_DIR, status="COMMITTED",
            # S05：COMMITTED 也记审计实际状态——空字符串只表示「没有可用审计」，
            # 不能和「审计跑成功」混为一谈。
            audit_status=str(summary.get("audit_status") or ""))
        write_step01_commit_manifest(_mrt.CHAPTER_DIR, manifest)
        # 语义门 + 凭据同步 + 完整新文件组最终核验（同事务内，任一步失败全量回滚）
        committed_script = json.loads(_mrt.OUTPUT_FILE.read_text(encoding="utf-8"))
        require_semantic_gate(_mrt.CHAPTER_DIR / "step01_quality.json",
                              _mrt.CHAPTER_DIR / "step01_audit.json", committed_script,
                              stage="step01")
        summary["adoption"] = _sync_adoption_receipt(revision, run_id)
        verify_step01_commit_manifest(_mrt.CHAPTER_DIR, committed_script, stage="step01",
                                      expected_chapter=_mrt.NOVEL_CHAPTER)
    except Exception as exc:
        try:
            _restored = _txn.rollback("发布失败: %s" % exc)
            _rb = "已回滚 %d 个文件" % len(_restored)
        except Exception as _rb_err:                             # noqa: BLE001
            _rb = "回滚不完整（锁保留，待 recover_pending）：%s" % _rb_err
        # 保持阶段语义：替换阶段失败 vs 提交后自验失败，调用方/测试都按它判读。
        _stage = "提交后自验失败" if manifest is not None else "正式产物替换失败"
        summary["detail"] = f"{_stage}：{exc}（{_rb}）"
        _mrt._metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=[])
        _mfa._clear_staged_dir()
        _mrt._console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    _txn.commit()
    summary["status"] = "COMMITTED"
    summary["revision"] = revision
    summary["manifest"] = manifest
    # 调用方（main 的摘要/metrics）消费同一个 quality 对象——不再重算，
    # 避免「落盘的报告」与「打印的报告」是两个对象（那正是漂移的老形态）。
    summary["quality"] = quality
    summary["audit"] = audit
    _mrt._metrics_inc("commit.committed")
    _mrt._metrics_update("commit.last", {
        "status": "COMMITTED", "revision": revision,
        "audit_repair_rounds": summary["audit_repair_rounds"],
        "reused_audit": summary["reused_audit"],
        "blocking": 0, "run_id": run_id,
    })
    _mrt._logger.info(
        f"[COMMIT] OK 三份产物已提交（revision={revision[:12]}，"
        f"审计修复轮 {summary['audit_repair_rounds']}，"
        f"复用审计 {'是' if summary['reused_audit'] else '否'}）"
    )
    return script, summary


def _prepare_hook_for_qc(script: dict) -> None:
    _mrt._metrics_inc("hook.prepare")
    """Deterministically normalize a hook before the quality gate.

    Hook generation happens after the main hard-fix pass.  Without this, a hook
    with complete ``frame_layouts`` but missing legacy ``char_layout`` is sent
    for a full LLM regeneration, which is slow and random.  These constraints
    only synchronize already-known structure; they do not invent plot content.
    """
    from step01_script_gen.validation import post_fix_shot_prompts
    from step01_script_gen.constraints import ConstraintContext
    from step01_script_gen.constraints.element_markers import ElementMarkers
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint)
    from step01_script_gen.constraints.char_layout_sync import (
        CharLayoutSyncConstraint)
    from step01_script_gen.constraints.surface_backfill import (
        SurfaceBackfillConstraint)
    from step01_script_gen.constraints.gaze_state_repair import (
        GazeStateRepairConstraint)

    post_fix_shot_prompts(script)
    ctx = ConstraintContext.from_script(script, markers=ElementMarkers())
    for constraint in (
        FrameSpecConsistencyConstraint(),
        CharLayoutSyncConstraint(),
        SurfaceBackfillConstraint(),
        GazeStateRepairConstraint(),
    ):
        constraint.apply(ctx)


def _ensure_hook_quality(script: dict, *, novel_text: str, bank=None,
                         objects_bank=None, forbidden_lines=None,
                         producer_model=None) -> list[str]:
    """钩子质量门（v6.70.1，09-13；09-14 抽出供 BEST-GUARD 恢复路径共用）.

    hook 先过确定性结构补齐，再进 checklist；仍有 issue 才换互补模型重生 1 次。
    正式输出有两条来源（本次 run / BEST-GUARD 恢复的快照+补修复），门必须对
    两者同等生效——09-14 实测：恢复路径绕过本门，旧快照的病句/无主刀尖经
    非幂等补修复污染（刀尖句翻倍 + 对象库碎片）直接落盘。重生后补跑检查 7
    （FrameSpec 出镜覆盖）——重生钩子的 spec 同样可能漏人。返回残余 issues
    （空 = 过门）。
    """
    try:
        _prepare_hook_for_qc(script)
    except Exception as _prep_err:
        _mrt._logger.warn(f"[WARN] 钩子质量门前结构补齐失败（继续检查）: {_prep_err}")
    # v6.84（二次审核发现 5）：**先把钩子的时长/时间轴派生出来，再收集 issue**。
    #
    # 原顺序是「收集 issue → …」，而钩子质量门跑在 `_compute_timecodes` **之前**
    # ——`duration_sec` 还没派生，`final_time_invariant_issues()` 里 `dur<=0` 会
    # 整段跳过 KF 检查。本次实测钩子首帧 `t_sec=0.0` 就是这样漏过质量门的
    # （`hook.initial_issues=0` / `hook.final_issues=0`），直到落盘前的最终门才
    # 报出来，那时已经没有 regen 机会。现在门内先做与 `_compute_timecodes`
    # **同一份**时长推导，再用**同一套**最终不变量检查。
    try:
        from step01_script_gen.timecode import sync_hook_timeline
        sync_hook_timeline(script)
    except Exception as _sync_err:
        _mrt._logger.warn(f"[WARN] 钩子质量门时长派生失败（继续检查）: {_sync_err}")
    issues = _msc._collect_hook_issues(script)
    _mrt._metrics_set("hook.initial_issues", len(issues))
    if not issues:
        _mrt._metrics_set("hook.final_issues", 0)
        return []
    _mrt._logger.warn(
        f"[HOOK-QC] 钩子检出 {len(issues)} 条 issue，互补模型重生："
        + " | ".join(issues[:3]))
    _mrt._console(f"  [钩子校验] 检出 {len(issues)} 条 issue → 互补模型重生钩子…")
    _mrt._metrics_inc("hook.regenerations")
    _hook_regen = _mgn.gen_hook_script(
        novel_text, bank=bank, objects_bank=objects_bank,
        forbidden_lines=forbidden_lines,
        model=_mrg._complementary_repair_model(producer_model),
    )
    if not _hook_regen:
        _mrt._logger.warn("[HOOK-QC] ⚠️ 钩子重生失败，带 issue 落盘（人工过目）：\n  - "
                     + "\n  - ".join(issues))
        _mrt._console("  [钩子校验] ⚠️ 重生失败，钩子带 issue 落盘")
        return issues
    script["hook"] = _hook_regen
    try:
        from step01_script_gen.validation import validate_hook
        _prepare_hook_for_qc(script)
        validate_hook(script["hook"], script.get("characters", []))
    except Exception as _e:
        _mrt._logger.warn(f"[WARN] 重生钩子修复/校验异常（不阻断）: {_e}")
    from step01_script_gen.timecode import sync_hook_timeline as _sync_hk
    try:
        _sync_hk(script)
    except Exception as _degrade_exc:
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:_ensure_hook_quality", _degrade_exc)
    remaining = _msc._collect_hook_issues(script)
    _mrt._metrics_set("hook.final_issues", len(remaining))
    # v6.84（09-21 重跑发现）：钩子时长可能在门内被改（`sync_hook_timeline` 按
    # motion/台词重算，或换成了重生版钩子）。**必须重建时间轴**，否则 hook 的
    # `duration_sec` 与 `timeline[0].end` 不一致 → `timeline_consistency` BLOCK
    # （实测：timeline 写的是 4.0s，钩子被重算成 3.5s，total 78.14 vs sum 74.14）。
    try:
        _mrt.write_timeline_from_durations(script)
    except Exception as _tl_e:
        _mrt._logger.warn(f"[WARN] 钩子质量门后重建时间轴失败: {_tl_e}")
    if remaining:
        _mrt._logger.warn("[HOOK-QC] ⚠️ 重生后钩子仍有 issue（保留落盘，人工过目）：\n  - "
                     + "\n  - ".join(remaining))
        _mrt._console("  [钩子校验] ⚠️ 重生后仍有 issue，已按原样落盘（见日志）")
    else:
        _mrt._logger.info("[OK] 钩子后置校验通过（互补模型重生版）")
        _mrt._console("  [钩子校验] 重生钩子通过校验")
    return remaining
