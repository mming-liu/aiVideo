# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.repair_round —— 定向修复轮 `_repair_shot_warnings`.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import copy

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
from step01_script_gen.orchestrator import regen as _mrg
from step01_script_gen.orchestrator import repair_apply as _mra
from step01_script_gen.orchestrator import final_audit as _mfa
from step01_script_gen.orchestrator import repair_accept as _mrc
from step01_script_gen.orchestrator import repair_targets as _mtg
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _repair_shot_warnings(script: dict, novel_text: str,
                          bank=None, objects_bank=None,
                          model: str | None = None,
                          audit_issues: list[_mrt.Issue] | None = None,
                          max_rounds: int | None = None,
                          repair_diag: list | None = None,
                          block_reasons: list | None = None,
                          retry_state: dict | None = None,
                          audit_attempted: set | None = None,
                          defer_targets: set | None = None,
                          effect_state: dict | None = None,
                          audit_relations: dict | None = None) -> dict:
    """定向修复轮（v6.35，P18 闭环核心）。

    GAP 通过后仍有高价值告警 → 只重写告警镜头的相关字段，重新校验，收敛即停。
    全脚本重生成会把没问题的镜头也重画一遍（白烧额度 + 引入新随机）；
    单镜修复只动有问题的地方，且每次轮次只挑「最值得修」的镜头（防 prompt 爆炸）。

    v6.43 08-25 HARD-only 策略：
    - 无 HARD 违例时（_has_hard=False）**整轮跳过**，节省 60s LLM 调用
    - WARN/soft-suggestion 全部由 hard_fix 兜底（runtime_extend / 12 个 constraint）
    - 实测：精简 prompt + hard_fix 增强后，HARD 违例基本为 0，**修复轮几乎不再触发**

    Args:
        repair_diag: 可选。给了就把**每一轮的修复事务诊断**追加进去（调用方写台账）：
            轮次 / 目标 / 联动组 / **回退前的 new_blocking_keys** / 回退了哪些目标 /
            接受与否 / resolved / 修复前后 revision。早退也会追加——记录点在决策落地
            之后、早退之前，所以「本轮为什么失败」不会随 `return` 一起丢掉。

    Returns:
        修复后的 script（任一轮失败/无告警则原样返回）。
    """
    # v6.87：**合并后立刻**抓的「LLM 写的锁定字段」快照（循环内赋值）。在这里
    # 预置，避免「某轮提交了替换却没走到赋值」时尾部读到未定义名——v6.83 的
    # `allowed_targets` 未定义名事故就是这个形态（被外层 except 吞掉、整段接受
    # 判定从未生效）。
    _mask_violations_after_patch: list = []
    # v6.104.1：KF 结构验收快照（合并后立刻取，见循环内）
    _kf_struct_after_patch: list = []
    # M02：合并后、后处理前的**授权效果**快照（见 `_authorized_changes_lost`）
    _post_merge_snapshot: "dict | None" = None

    # v6.94（2026-09-22 实测）：④ 的根因**不在这里**——是轮尾 `run_hard_fix` 跑到
    # `shot_index_renumber`（它没有 repair-loop 守卫），把结构收敛轮拆出的半整数
    # 子镜归一成整数，其后所有 index 平移；而 `_snapshot_untargeted` 与本轮点名的
    # `picked` / audit `shot_id` 都按 index 标识镜头 → 还原到错镜 + 找不到目标
    # → `非目标镜头被改动 9 处: [13, 13.333, 14, 14.25, 14.5]` 这种**假拒绝**。
    #
    # 修法在 `shot_index_renumber` 一侧（加 `ctx.is_in_repair_loop` 守卫，轮内不
    # 重编号），本函数不再需要「轮首先跑到幂等」那种绕法——实测它反而会把 fixture
    # 的 `index=7` 改成 `1`，导致 audit issue 的 `shot_id` 失配、修复轮提前退出。

    # v6.111（审核 P1-1）：**本轮次上限由调用方给**。
    #
    # 提交点的预算契约是「最多 2 个修复轮」（总计划 §1.1，用户已确认），而本函数
    # 自身默认跑 `_REPAIR_MAX_ROUNDS = 3` 轮——外层 2 次 × 内层 3 轮 = 最多 6 次
    # LLM 修复调用，与已确认的预算不符。提交路径显式传 `max_rounds=1`，由外层
    # 的两轮循环精确控制总预算；GAP 路径不传，保持原有的分批收敛能力。
    _rounds = max(1, int(max_rounds)) if max_rounds else _mrg._REPAIR_MAX_ROUNDS
    # v6.137（2026-09-26 第七次实跑根因）：**new_blocking 型拒绝带反馈重试一轮**。
    # 实测（run 20260926T083731）：4 个目标里只有 shot_4 的补丁引入新 blocking，
    # 按目标回退后其余 3 个目标的补丁也未通过验收（resolved=0/字段触达=0）→
    # `not _round_ok` 直接 return——修复阶段一轮即死，提交层"零改善 BLOCK"。
    # 而拒绝原因本身可反馈：肇事目标已定位（剔除不再重试）、其余目标"上一轮
    # 补丁没修掉"也该告诉 LLM。重试只做一次（_retry_used），其余拒绝原因
    # （locked_changed/non_target_changed 等）保持原停止语义。
    # v6.137：重试状态**跨调用**——提交路径每次只给 max_rounds=1，内层 continue
    # 装不下第二轮；调用方（提交层）传同一个 dict 进来：入口预载上一轮的
    # 「剔除目标+反馈」并清空（消费），出口在 new_blocking 终拒时回填新状态，
    # 由调用方决定是否用下一次 attempt 续跑（预算仍由外层 §1.1 上限管住）。
    # M03（2026-11-15）：**结构化反馈状态**。`retry_state` 是调用方持有的跨
    # attempt 容器；入口先取快照再清空，判据一律读结构化字段，自由文本只作展示。
    # revision 身份：反馈必须绑定同一份冻结基线稿；跨稿一律丢弃，绝不借用上一轮
    # 的裁决结果冒充本轮通过。
    from step01_script_gen.repair_feedback import (
        render_feedback as _render_fb,
        plan_feedback_state as _plan_fb,
        transaction_feedback_records as _tx_records,
        answer_signature as _answer_sig,
        feedback_record as _fb_record,
    )
    _retry_state_snapshot: dict = dict(retry_state or {})
    try:
        from shared.quality_gate import compute_script_revision as _csr_entry
        _entry_revision = str(_csr_entry(script) or "")
    except Exception:
        _entry_revision = ""
    _prior_revision = str(_retry_state_snapshot.get("source_revision") or "")
    _revision_mismatch = bool(_prior_revision and _entry_revision
                              and _prior_revision != _entry_revision)
    if _revision_mismatch:
        _mrt._logger.warn("[REPAIR] 反馈 source_revision 与本轮基线不符（%s ≠ %s）→ "
                     "丢弃跨稿反馈，不借用上一轮裁决"
                     % (_prior_revision[:12], _entry_revision[:12]))
        _retry_state_snapshot = {}
    _retry_records = [dict(r) for r in
                      (_retry_state_snapshot.get("feedback_records") or [])
                      if isinstance(r, dict)]
    _rejected_targets: set = set(_retry_state_snapshot.get("excluded") or [])
    _retry_feedback = (_render_fb(_retry_records)
                       or str(_retry_state_snapshot.get("feedback") or ""))
    _retry_used = (bool(_retry_state_snapshot.get("used_correction"))
                   or bool(_retry_state_snapshot.get("terminal")))
    _retry_terminal = bool(_retry_state_snapshot.get("terminal"))
    # §14 P3：**跨轮/跨调用**的布局效果冲突——上一次已发出去的冲突反馈。
    # 「相同冲突且请求信息无变化 ⇒ 停止盲修并封存残余」要能跨调用判出来：提交路径
    # 每次只给 `max_rounds=1`，同一对话的第二次请求是**下一次 attempt**，只能借
    # `retry_state` 携带。额度与截止沿用既有实现（这里**不改重试上限**）。
    _effect_conflict_prev = dict(_retry_state_snapshot.get("layout_conflict") or {})
    if retry_state:
        retry_state.clear()
    # v6.140（修复 3，九审根因 3）：调用方传来的**本 run 暂缓目标**（失败联动组
    # 的成员）与重试剔除集合同一过滤口径——暂缓的组不再被重发。⚠️ 合并集必须
    # **每轮重算**（`_rejected_targets` 会在轮次间被重试门更新），不能在循环外
    # 快照——2026-09-26 单测实锤：快照版让重试轮的剔除静默失效。
    _defer = set(defer_targets or ())
    # S03：上一轮因**体积**被暂缓的目标，本轮优先装（否则永远排在队尾饿死）。
    _carry_deferred: set = set()
    for _round in range(1, _rounds + 1):
        # M03：**工程故障 / 重复拒因 / 一次纠正机会已用完** ⇒ 在发请求之前终局。
        # 「停止」必须是发请求**之前**的动作，否则只是事后描述。
        if _retry_terminal:
            _stop_msg = ("修复反馈终局（%s）：不再发起新的修复请求；基线原样保留，"
                         "残余按原样交给提交点判 BLOCK"
                         % (_retry_state_snapshot.get("stop_reason") or "terminal"))
            _mrt._logger.error("[REPAIR] " + _stop_msg)
            if block_reasons is not None:
                block_reasons.append(_stop_msg)
            if repair_diag is not None:
                repair_diag.append({
                    "repair_round": _round, "kind": "feedback_terminal",
                    "stop_reason": _retry_state_snapshot.get("stop_reason") or "",
                    "feedback_records": _retry_records,
                    "source_revision": _prior_revision,
                    "sealed_before_send": True,
                })
            return script
        # S02/S03：本轮事务的帧来源映射，绑定本轮基线稿，每轮重新生成。
        # 装配/授权合并/守卫/效果登记/按目标回退重验共用这一份。
        _frame_mappings: dict = {}
        _frame_mappings_guard: dict = {}   # 守卫/效果/重验消费的同一份映射
        _frame_mappings_final: dict = {}   # 台账记录**最终保留稿**对应的映射
        _excluded_all = _rejected_targets | _defer   # v6.140：每轮重算（见上）
        # §14 P3：**已封存的布局效果冲突 ⇒ 不再发请求**。
        #
        # 「相同冲突且请求信息无变化 ⇒ 停止盲修并封存残余」要在**发请求之前**生效，
        # 否则「停止」只是事后描述：上一轮已经把同一条冲突反馈发出去、又原样收到同一个
        # 回答，再发第三次就是纯空转。封存只影响本判据，**不改重试上限与截止**。
        if _effect_conflict_prev.get("sealed"):
            _seal_msg = (
                "布局效果冲突已封存（reason=%s，责任 writer=%s，目标=%s）——"
                "相同冲突与请求信息在上一轮已被证明无变化，本轮不再发修复请求；"
                "残余按原样交给提交点判 BLOCK"
                % (_effect_conflict_prev.get("reason") or "?",
                   _effect_conflict_prev.get("writer") or "?",
                   _effect_conflict_prev.get("targets") or []))
            _mrt._logger.error("[REPAIR] %s" % _seal_msg)
            if block_reasons is not None:
                block_reasons.append(_seal_msg)
            if repair_diag is not None:
                repair_diag.append({"repair_round": _round,
                                    "kind": "effect_conflict_sealed",
                                    "reason": _effect_conflict_prev.get("reason") or "",
                                    "writer": _effect_conflict_prev.get("writer") or "",
                                    "targets": _effect_conflict_prev.get("targets") or [],
                                    "signature": _effect_conflict_prev.get("sig") or "",
                                    "source_revision": _effect_conflict_prev.get(
                                        "source_revision") or "",
                                    "sealed_before_send": True})
            return script
        # v6.101：轮数从 1 放到 3 之后，**每轮开始前**再查一次总预算。
        # 调用点（`main` 里 `REPAIR_ROUNDS_ENABLED and not _budget_exceeded()`）
        # 只保证「开始修的时候没超预算」，挡不住多轮叠加把单次 run 拖到 20 分钟
        # （第十四次重跑已经 12 分钟）。已完成的轮次保留，只是不再发下一批。
        if _round > 1 and _mrt._budget_exceeded_with_pause("repair_round"):
            _mrt._logger.warn("[BUDGET] 总预算超时，停止后续定向修复轮（已通过的轮次保留）")
            return script
        per_shot_dict = _msc._collect_repair_warnings(script, novel_text)
        import copy as _copy_before_accept
        _det_before_state = _copy_before_accept.deepcopy(per_shot_dict)
        # Merge same-model audit findings into the first repair round.  Audit is
        # auxiliary input; deterministic checks still decide final acceptance.
        # v6.137：**重试轮同样合并**——new_blocking 重试轮的遗留目标往往只有
        # audit 类告警（确定性阻塞可能已在上一轮解决），不合并则 picked 为空、
        # 重试轮空转返回。
        if (_round == 1 or _retry_feedback) and audit_issues:
            audited = per_shot_dict.setdefault("shots", {})
            # 2026-11-07（复核 P1-2）：**只指向锁定字段**的审计项必须在进入
            # per_shot_dict / 联动组 / "### 待修镜头" 之前就摘掉。否则它会：
            #   ① 与其它问题组成联动组、挤掉真正可修的确定性问题；
            #   ② 在提示里命令模型改 narration_segment，而 allowed_fields 里
            #      这些字段已被 _split_executable_allowed 剥掉 → 模型一动手就
            #      「越权字段补丁」，整轮 rejected_by_validation（run17 r1 实测）。
            # 2026-11-19 第 1 包 M04-b：此前缺这条 import，NameError 被下方 except 吞掉，
            # 预过滤从 1e368c6 起从未生效。
            from step01_script_gen.repair_fields import allowed_repair_fields
            _shots_by_id_audit = {"shot_%s" % _s.get("index"): _s
                                  for _s in (script.get("shots") or [])
                                  if isinstance(_s, dict)}
            if isinstance(script.get("hook"), dict):
                _shots_by_id_audit["hook"] = script["hook"]
            _locked_only: list = []
            for _issue in audit_issues:
                if not _mrt.is_blocking(_issue):
                    continue
                _tid_audit = _mfa._target_of_record(_issue)
                if _tid_audit and _tid_audit in _shots_by_id_audit:
                    try:
                        _audit_fields = allowed_repair_fields(
                            [], [_issue], target_shot_id=_tid_audit,
                            shots_by_id=_shots_by_id_audit)
                        _exec_audit, _locked_audit = _mra._split_executable_allowed(_audit_fields)
                    except Exception:
                        _audit_fields = []
                    if _audit_fields and not _exec_audit:
                        _locked_only.append("%s@%s→%s" % (_issue.code, _tid_audit,
                                                         sorted(_locked_audit)))
                        continue
                per_shot_dict["_has_hard"] = True
                _sid = _issue.shot_id
                _detail = (
                    f"{_mrt.MODEL_AUDIT_WARNING_PREFIX} "
                    f"code={_issue.code} frame={_issue.frame_id or '-'} "
                    f"entity={_issue.entity_id or '-'} field={_issue.field_path or '-'} "
                    f"evidence={_issue.evidence or '-'} :: {_issue.message}"
                )
                # v6.120：按 `_target_of_record` 归一（`"shot5"` ≡ `"5"`）。
                # 旧实现直接拼 `f"shot_{_sid}"`：模型第 2 轮写 `"shot5"` 时这里会挂到
                # 不存在的 `shot_shot5` 上 → 成为本轮 picked 目标 → LLM 被要求修一个
                # 不存在的镜头、真正的 shot 5 反而没进目标集（离线实跑复现）。
                if str(_sid or "").strip() == "":
                    continue          # 无定位镜头的全局问题：不挂到任何目标
                audited.setdefault(_mfa._target_of_record(_issue), []).append(_detail)
            if _locked_only:
                _mrt._logger.info("[REPAIR] 跳过仅指向锁定字段的审计项 %d 条: %s"
                             % (len(_locked_only), _locked_only[:5]))
        # v6.43 08-25：HARD-only 触发——没 hard violation 就不调 LLM
        if not per_shot_dict.get("_has_hard", False):
            if _round == 1:
                _mrt._logger.info(
                    "[REPAIR] 跳过：无 HARD 违例（HARD-only 策略），"
                    "WARN 已由 hard_fix 兜底"
                )
            return script
        per_shot = per_shot_dict.get("shots", {})
        # 过滤结构性全局告警（无法单镜修复的），只留镜头级
        shot_items = {k: v for k, v in per_shot.items() if k.startswith("shot_") or k == "hook"}
        if not shot_items:
            return script
        # 第1轮：只挑告警最多的前 6 镜，聚焦修最严重的（防 prompt 膨胀）；
        # 第2轮：剩余告警镜头**全部**返给 LLM 一次修完（08-15 用户要求——低告警镜头
        # 此前可能两轮都进不了 top-6 而永不修复）。每轮 _collect_repair_warnings 都
        # 重新校验当前脚本：第1轮已修的告警消失、未修的保留，故第2轮"全部"天然覆盖
        # 第1轮遗留 + 第2轮新增。
        import itertools
        # v6.101：**每一轮都按告警数取前 K**。
        #
        # 原实现只有第 1 轮限量，第 2 轮起 `picked = dict(shot_items)`（全部带告警的
        # 镜头）——08-15 的用意是「低告警镜头别永远轮不到修补」，但那条路径在
        # `_REPAIR_MAX_ROUNDS = 1` 下**从未执行过**。现在轮数放开到 3，若照原样
        # 第 2 轮会一次性发 10+ 个完整镜头对象，必然 `finish_reason=length`
        # 整份丢弃（v6.84 定分批的原始原因）。改为**每轮都限量**：
        # 按告警数排序取前 K，多轮自然覆盖「第 1 轮遗留 + 新增」。
        # v6.107（2026-09-24 第二十九次重跑实测）：名额分配必须**先给带确定性阻塞的
        # 镜头**。
        #
        # 实测：提交点第 1 轮只有 1 条确定性阻塞（shot 9 的 `kf_event_window_mismatch`）
        # 和 4 条模型审计问题，4 个名额按「告警条数」排序时两类目标条数相同 →
        # shot 9 掉出前 4，被替换的是 2/5/8 等纯审计目标 → 那一轮**根本没有尝试**
        # 那条确定性阻塞，`resolved=0` → 停止修复 → BLOCK。
        #
        # 而验收里 `resolved` 计量的是**确定性**阻塞（`audit_resolved` 只是另一条
        # 并行判据），质量门也是按它判 BLOCK。所以排序主键用「是否带确定性阻塞」，
        # 次键才用告警条数——否则修复轮会把预算花在计量不到的目标上。
        _det_targets = _mtg._deterministic_targets(per_shot_dict.get("issues"))
        # v6.138（九审 P1-2）：上轮肇事目标在**取名额之前**过滤——剔除后空出的名额
        # 由排序中的后续目标补齐，重试轮不再空转容量。v6.140：本 run 暂缓的联动组
        # 成员同一过滤口径。
        picked = _mtg._pick_repair_targets(shot_items, _det_targets,
                                      _mrc._REPAIR_MAX_SHOTS_PER_ROUND,
                                      excluded=_excluded_all)
        if _excluded_all:
            _skipped = sorted(set(shot_items) & _excluded_all)
            if _skipped:
                _mrt._logger.warn("[REPAIR] 本轮剔除目标（肇事剔除 %s｜联动组暂缓 %s）"
                             % (sorted(set(shot_items) & _rejected_targets),
                                sorted(set(shot_items) & _defer)))
        if not picked:
            return script
        import copy as _copy_repair
        _round_before_script = _copy_repair.deepcopy(script)
        # M01：四阶段归因快照。B 在「后处理结束 / 事务裁决前」取，C 在按目标回退后取；
        # 两者都必须在 `script = _round_before_script`（整轮回退）之前落定，否则
        # 诊断会把事务回滚算成后处理抵消（run j 的 36 字段即由此而来）。
        _postprocess_snapshot: "dict | None" = None
        _post_rollback_snapshot: "dict | None" = None
        # S04：模型原始返回的镜头（本轮若没走到替换解析就保持空表，台账据此标依据）
        _model_shots_round: dict = {}
        _locked_before = _mra._locked_repair_signature(script)
        _locked_after_patch = _locked_before
        # §14 P1：本轮**效果账本**——四个冻结节点里的前两个（原候选 / 授权合并后）
        # 在这里起头。效果只属于**本轮事务**，跨轮不作事实（跨轮只留证据），
        # 所以每轮重建，不进生产分镜 schema。
        from step01_script_gen.repair_effects import (
            RepairEffectLedger, build_layout_effects, resolve_frame_actor_binding,
            classify_effect_outcome, conflict_signature,
            render_conflict_feedback, WRITER_GLOBAL_STYLE, WRITER_TIMELINE,
            WRITER_POST_FIX, WRITER_HARD_FIX, WRITER_INJURY_ANCHOR,
            WRITER_RESTORE_UNTARGETED,
        )
        from step01_script_gen.constraints.types import RepairEffectView
        from step01_script_gen.repair_fields import shot_by_target
        from shared.quality_gate import compute_script_revision as _csr_eff
        # ⚠️ 这个 revision 是「**本轮基线稿**」的，**不是**
        # `_commit_step01_outputs(source_revision=...)` 里那个「BEST-GUARD 选中快照
        # 自身」的 revision——两者在 BEST-GUARD 路径上必然不同，传错会让效果的版本
        # 身份绑到另一份稿子上（`remap_layout_effects` 的核对随之全部失效）。
        _effect_revision = _csr_eff(_round_before_script)
        # S03 补正：四节点从「原候选」起就按**本轮点名目标**冻结帧结构，而不是
        # 只按已登记效果取目标。纯中间补帧没有布局条目增删效果，但仍有装配来源
        # 映射；漏掉它，后处理 writer 在这类目标上插帧就没有任何节点能观测到。
        _effect_ledger = RepairEffectLedger(round_id=str(_round),
                                            source_revision=_effect_revision,
                                            frame_targets=set(picked))
        _effect_ledger.freeze_candidate(_round_before_script, _effect_revision)
        _effect_bindings: dict = {}
        _effect_view = None
        _effect_outcome: dict = {}
        _det_before_count = sum(
            len(v) for k, v in per_shot_dict.get("shots", {}).items()
            if k.startswith("shot_") or k == "hook"
        )
        _audit_before_count = (
            sum(1 for _i in (audit_issues or []) if _mrt.is_blocking(_i))
            if _round == 1 else 0
        )
        # 08-15（PR-3）：跨镜告警对镜补选——top-6 按告警数排序时，被选镜告警里的
        # 「镜头N→M」对镜可能掉出前 6（两镜告警数相同会一起进，但若 N 另有别的
        # 告警挤掉 M 就散了）。补选保证 N 入选时 M 必然一起，merge 门闩才能采纳
        # M 的承接改写。只补告警句点名的对镜，不把整串 run 全加（12→22 不加中间）。
        import re as _pr3re
        for _tid in list(picked):
            for _w in picked[_tid]:
                _pm = _pr3re.search(r"镜头\d+→(\d+)", _w)
                if _pm:
                    _partner = f"shot_{_pm.group(1)}"
                    # v6.138（九审 P1-2）/v6.140：补选不得越过肇事剔除与本 run 暂缓集
                    if (_partner in shot_items and _partner not in picked
                            and _partner not in _excluded_all):
                        picked[_partner] = shot_items[_partner]

        # 构建单镜修复 prompt：只带被选镜头原文 + 告警
        shots_by_id = {f"shot_{s.get('index')}": s for s in script.get("shots", [])}
        if "hook" in picked and script.get("hook"):
            shots_by_id["hook"] = script["hook"]

        # ---- v6.119：成对目标**成组扩目标**（用户口径：联动目标必须作为一个事务）----
        #
        # 一条审计问题可以声明跨镜的 `repair_targets`（同一事实在 shot5 与 shot7-9
        # 的 ff_desc 里互相矛盾，只改 shot5 永远修不好）。而目标集此前只按「问题
        # **定位**在哪」挑镜头、授权构建处又按 `shot_id` 过滤 issue → 联动镜头既进
        # 不了本轮、也拿不到授权，结构上不可能修好（实测 shot 5 的跨镜睁眼问题就是
        # 这样活到最后一轮）。
        #
        # 规则：
        #   ① 组内镜头**整组**进本轮——不拆成半次修复；
        #   ② 组超单轮容量 → **整组放弃**并显式记录（半次修复会制造新的自相矛盾，
        #      正是 shot 5 被整镜回退的成因），留到最终审计按 BLOCK 处理；
        #   ③ 组内镜头与本轮已选目标或问题定位有交集才算「本轮相关」；
        #   ④ 只有**校验通过**的成对条目参与（镜头必须存在、路径必须是该镜真实叶子）。
        from step01_script_gen.repair_fields import (
            validate_repair_targets, _target_id_of, _is_structural_code,
            _is_model_audit_code,
        )
        # v6.122（用户决定 B）：分组用**联动组专用上限**（6），不是单轮上限（4）。
        _repair_groups, _pair_rejects, _oversize_groups = _mtg._linked_repair_groups(
            audit_issues, shots_by_id, picked, cap=_mrc._REPAIR_MAX_SHOTS_PER_GROUP)
        if _oversize_groups:
            # 超过联动组上限 → **立即明确 BLOCK**：不调用 LLM、不再发审计。
            #
            # 为什么不再「整组跳过 + 留给最终审计」：跳过它只意味着同一份稿的审计
            # 每轮重复声明同一组、直到轮数与审计额度耗尽——那不是显式失败，是必然
            # 失败（组能力上改不完，而拆组=半条修复、删目标=漏声明，两者都禁止）。
            # 省下这两轮 LLM 修复 + 审计调用，把结论直接交给提交点。
            for _grp in _oversize_groups:
                _reason = (
                    "联动组 %s 共 %d 镜，超过联动组上限 %d → **无法整组修复**"
                    "（拆组=半条修复、删目标=漏声明，两者都禁止；该镜数是单条问题的"
                    "声明或多条共享镜头合并后的并集）——请检查审计契约或稿件本身"
                    % (sorted(_grp), len(_grp), _mrc._REPAIR_MAX_SHOTS_PER_GROUP))
                _mrt._logger.error("[REPAIR] %s" % _reason)
                if block_reasons is not None:
                    block_reasons.append(_reason)
            return script
        _grouped_all: set = set()
        for _g in _repair_groups:
            _grouped_all |= _g
        _active_groups: list = []
        # v6.122：**按组大小降序**处理。大组（>4 镜）本轮**独占名额**，如果先处理
        # 小组、它会把容量占掉，随后大组因「可腾位不足」被跳过——那正是要避免的
        # 结构性失败（同一份审计每轮都会声明它，跳过=必然 BLOCK）。
        _exclusive_taken = False
        for _grp in sorted(_repair_groups, key=lambda g: (-len(g), sorted(g))):
            # v6.138（九审 P1-2）：扩选入口同一排除约束——组内含上轮肇事目标 →
            # 整组跳过（组是原子事务，剔除单个成员=半组修复；重发大概率复发）。
            # v6.140：本 run 已暂缓的联动组同理（失败组不重发）。该组的阻塞留待
            # 提交门按 BLOCK 处理，与其他组的修复互不拖累。
            if _grp & _excluded_all:
                _mrt._logger.warn(
                    "[REPAIR] 联动组 %s 含剔除/暂缓目标 %s → 整组跳过"
                    "（重发大概率复发；该组问题留待提交门）"
                    % (sorted(_grp), sorted(_grp & _excluded_all)))
                continue
            if len(_grp) > _mrc._REPAIR_MAX_SHOTS_PER_ROUND:
                # **大组独占本轮名额**：组外目标（含其他联动组的成员）整批让位。
                # 让位是**整组推迟**而不是拆分——其他组的成员要么全在组外、要么
                # 与本组合并（`_linked_repair_groups` 已按共享镜头合并），所以
                # 不会留下半组在稿子上。
                if _exclusive_taken:
                    _mrt._logger.warn(
                        "[REPAIR] 联动组 %s 共 %d 镜也超过单轮上限 %d → 本轮已由"
                        "另一组独占，**整组推迟到下一轮**（不拆成半次修复）"
                        % (sorted(_grp), len(_grp), _mrc._REPAIR_MAX_SHOTS_PER_ROUND)
                    )
                    continue
                _exclusive_taken = True
                _deferred = sorted(t for t in picked
                                   if t != "global" and t not in _grp)
                for _t in _deferred:
                    picked.pop(_t, None)
                _mrt._logger.info(
                    "[REPAIR] 联动组 %s 共 %d 镜 > 单轮上限 %d → **独占本轮名额**"
                    "；让出（推迟到下一轮）：%s"
                    % (sorted(_grp), len(_grp), _mrc._REPAIR_MAX_SHOTS_PER_ROUND,
                       _deferred)
                )
            elif not (_grp <= set(picked)):
                # 小组（≤ 单轮上限）：沿用 v6.119 的腾位——只让出「不在任何联动组
                # 里」的单镜目标（联动组优先，否则组永远挤不进容量）。
                # 腾位算的是**本轮实际容量**（= 单轮上限），不是组的镜数。
                _round_cap = _mrc._REPAIR_MAX_SHOTS_PER_ROUND
                _need = len(set(picked) | _grp) - _round_cap
                if _need > 0:
                    _evictable = sorted(t for t in picked
                                        if t != "global" and t not in _grouped_all)
                    if len(_evictable) < _need:
                        _mrt._logger.warn(
                            "[REPAIR] 联动组 %s 无法在本轮容量 %d 内成组"
                            "（可腾位 %d < 需 %d）→ 整组推迟到下一轮"
                            "（不拆成可能互相矛盾的半次修复）"
                            % (sorted(_grp), _round_cap, len(_evictable), _need)
                        )
                        continue
                    for _t in _evictable[:_need]:
                        picked.pop(_t, None)
                    _mrt._logger.info(
                        "[REPAIR] 为联动组 %s 让出单镜目标 %s（本轮容量 %d）"
                        % (sorted(_grp), _evictable[:_need], _round_cap)
                    )
            for _g in _grp:
                # v6.122：**组内成员一律进本轮**（自身没有告警就给空列表）。
                # 联动组的定义就是「这些镜头必须一起改」；只纳入有自身告警的那些，
                # 组在稿子上就只能改一半——其余成员的矛盾原样留着（用户口径：
                # 整组修/整组验收/整组回退，不留半组）。授权与改动依据由
                # `_issue_reaches` + 成对目标提供，不依赖 `shot_items`。
                picked.setdefault(_g, shot_items.get(_g) or [])
            _active_groups.append(_grp)
        if _active_groups:
            _mrt._logger.info("[REPAIR] 本轮联动组（整组修/整组验收/整组回退）: %s"
                         % [sorted(g) for g in _active_groups])
        if _pair_rejects:
            # 成对目标校验失败必须显式说清——它直接决定「本轮能不能修这条问题」
            _mrt._logger.warn("[REPAIR] repair_targets 校验未通过 %d 项（这些条目不参与授权）: %s"
                         % (len(_pair_rejects), _pair_rejects[:8]))

        # S03（2026-11-07）：**按最终编码字节装批**，不是只按条数。
        # 先保留必须联动的整组，再按体积裁掉尾部独立目标；被裁目标写进本轮 diag，
        # 由下一轮 / 下一次提交尝试继续。不静默丢问题，也不为凑体积截断镜头正文、
        # 证据或授权字段（那是用缺信息换字节，不可验收）。
        if _carry_deferred and any(t in picked for t in _carry_deferred):
            _first = {t: picked[t] for t in list(picked) if t in _carry_deferred}
            _rest = {t: picked[t] for t in list(picked) if t not in _carry_deferred}
            picked = {**_first, **_rest}
        if _mtg._REPAIR_MAX_REQUEST_BYTES > 0:
            _picked_bytes = {_tid: _mtg._estimate_repair_target_bytes(
                shots_by_id.get(_tid), _ws) for _tid, _ws in picked.items()}
            _units: list = []
            _seen_ids: set = set()
            for _grp in (_active_groups or []):
                _ids = [t for t in _grp if t in picked and t not in _seen_ids]
                if _ids:
                    _units.append(_ids)
                    _seen_ids |= set(_ids)
            for _tid in picked:
                if _tid not in _seen_ids:
                    _units.append([_tid])
                    _seen_ids.add(_tid)
            _budget = max(0, _mtg._REPAIR_MAX_REQUEST_BYTES - _mtg._REPAIR_FIXED_OVERHEAD_BYTES)
            _cap_keep: list = []
            _cap_defer: list = []
            _cap_used = 0
            for _u in _units:
                _ub = sum(_picked_bytes.get(t, 0) for t in _u)
                if _cap_keep and (_cap_used + _ub) > _budget:
                    _cap_defer.extend(_u)
                    continue
                _cap_keep.extend(_u)
                _cap_used += _ub
            if _cap_defer:
                picked = {t: ws for t, ws in picked.items()
                          if t not in set(_cap_defer)}
                _mrt._logger.warn(
                    "[REPAIR] 请求体积超预算（上限 %d B，已装 %d B）——本轮暂缓 %s，"
                    "下一轮/下一次提交尝试继续" % (_mtg._REPAIR_MAX_REQUEST_BYTES, _cap_used,
                                                sorted(_cap_defer)))
                if repair_diag is not None:
                    repair_diag.append({"repair_round": _round,
                                        "kind": "deferred_by_capacity",
                                        "deferred": sorted(_cap_defer),
                                        "used_bytes": _cap_used,
                                        "cap_bytes": _mtg._REPAIR_MAX_REQUEST_BYTES})
                _mrt._metrics_inc("repair.deferred_by_capacity", len(_cap_defer))
                _carry_deferred = set(_cap_defer)

        _mrt._console(f"  [修复轮{_round}] {len(picked)} 镜待修（{', '.join(picked)}）...")
        # 08-18 方案 C：先统计全片各 label 的 WARN 总数（按分镜聚合之前算一次），
        # 后面拼 parts 时每条 WARN 末尾追加"（同类全片共 N 处）"——让 LLM 改 shot_4
        # 时知道"ff_desc_anchors 错误全片 25 处"而不是孤例 1 处，全局视图促动认真修。
        # 08-21 mode 合并：_label_total 总是计算（v1 mode A 不算 → v2 算轻量版 → v3 复评
        # 决定全量注入全片错误分布 + top-3 hint + 全片分布三件套同时给 LLM）
        _label_total: dict = {}
        from collections import Counter as _label_counter
        _label_total = _label_counter()
        for _tid, _ws in per_shot.items():
            for _w in _ws:
                if _w.startswith("["):
                    _end = _w.find("]")
                    if _end > 0:
                        _label_total[_w[1:_end]] += 1
        # top-3 高频 hint（轻量版全片视图，无分布表，只取前 3 类）
        # ——给 LLM 一个"本章主要矛盾"信号，不带成本又比纯 warn 列表多了全局感
        _top3_hint = ""
        if _label_total:
            _top3 = _label_total.most_common(3)
            if _top3:
                _top3_hint = (
                    "\n**全片高频问题（top-3，仅作参考）**：" +
                    "、".join(f"[{l}] {n}处" for l, n in _top3) + "\n"
                )
        parts = []
        # 08-15（v6.36 P18 修复）：删除「全片结构问题」前言注入。v6.36 已把开篇冲突/
        # 爆点密度/内容密度 剔除出修复类别（全局重构必引入噪音），此时 per_shot["global"]
        # 只剩 keyframes 聚合告警（以「N 个中间关键帧」开头、无镜头号、被 shot_items
        # 过滤）——把它当结构问题灌给 LLM 只会诱导重排前1/3段镜头，正是开篇冲突/爆点
        # 密度回归的来源。keyframes 改走确定性 skeleton（PR-2），不进修复轮。
        for tid, warns in picked.items():
            if tid == "global":
                continue
            s = shots_by_id.get(tid)
            if not s:
                continue
            # 08-15 邻镜上下文（用户要求）：镜头衔接/动作重叠是**跨镜**类告警，
            # 第1轮 top-6 时邻镜不在 prompt 里，LLM 看不到邻镜结尾/开头就修不了
            # 成对的衔接问题——把「前镜 lf_desc + 后镜 ff_desc」一并给 LLM。
            # 第2轮全量时邻镜字段已在各自块里，此处冗余无害。
            prev_s = shots_by_id.get(f"shot_{s.get('index', 0) - 1}")
            next_s = shots_by_id.get(
                f"shot_1" if tid == "hook" else f"shot_{s.get('index', 0) + 1}")
            ctx_lines = []
            if prev_s:
                ctx_lines.append(f"前镜({prev_s.get('index')}) lf_desc: {prev_s.get('lf_desc', '')}")
            if next_s:
                ctx_lines.append(f"后镜({next_s.get('index')}) ff_desc: {next_s.get('ff_desc', '')}")
            ctx = ("\n" + "\n".join(ctx_lines) + "\n") if ctx_lines else ""
            # 08-18：每条 WARN 末尾追加"（同类全片共 N 处）"
            # 08-21 mode 合并：总是追加（让 LLM 知道同类问题全片共 N 处）
            _warn_lines = []
            for w in warns:
                if w.startswith("[") and w.find("]") > 0:
                    _lbl = w[1:w.find("]")]
                    _total = _label_total.get(_lbl, 1)
                    _warn_lines.append(f"  - {w}（同类全片共 {_total} 处）")
                else:
                    _warn_lines.append(f"  - {w}")
            if not _warn_lines:
                # v6.122：联动组成员可能**自身没有任何告警**（问题定位在锚点镜）——
                # 那时它的改动依据只在 REPAIR FEEDBACK 的 `audit_issues` 里，
                # 明确指路，避免模型面对空「问题:」无从下手。
                _warn_lines.append(
                    "  - （本镜自身无告警：它是联动目标，改动依据见 REPAIR FEEDBACK "
                    "里本镜的 audit_issues；只改 allowed_fields 列出的字段）")
            _warns_str = "\n".join(_warn_lines)
            parts.append(f"### 待修镜头 {tid}\n"
                         f"当前 ff_desc: {s.get('ff_desc', '')}\n"
                         f"当前 lf_desc: {s.get('lf_desc', '')}\n"
                         f"当前 prompt: {s.get('prompt', '')}\n"
                         f"当前 color_tone: {s.get('color_tone', '')}\n"
                         f"{ctx}"
                         f"问题:\n{_warns_str}")
        if not parts:
            return script

        # 08-18：在 prompt 开头加"全片错误分布"段
        # 08-21 mode 合并：全片错误分布总是注入（v1 mode A 删掉，v2 复评发现有用）
        # ——给 LLM 全局视图，决策树 + 正反例才能精准定位
        _global_block = (
            "\n**全片错误分布（同类问题总数，按类别聚合）**：\n"
            + "\n".join(f"  - [{l}] 全片共 {n} 处"
                        for l, n in _label_total.most_common())
            + "\n"
        )

        # v6.37：把修复轮 prompt 拆成结构化多轮输入
        # - system_prompt：完整规则（不变）
        # - input[0]：INPUT（当前分镜 + 角色锚点 + skills）——**不含 novel，防 LLM 重生成**
        # - input[1]：REPAIR FEEDBACK（结构化 JSON）+ TASK（修复指令）——纯修复内容
        # 关键：移除 novel text，避免 LLM 看到原文后重新拆镜（违反"手术刀原则"）
        import json as _json
        _repair_context = {
            "hook": shots_by_id.get("hook") if "hook" in picked else None,
            "shots": {tid: shots_by_id[tid] for tid in picked if tid in shots_by_id},
        }
        _current_script_json = _json.dumps(
            _repair_context, ensure_ascii=False, indent=1, default=str
        )

        # 角色锚点（仅含服装色等查询字段，不含规则）——修 服装色 必备
        _char_anchors_str = ""
        if bank:
            try:
                _ca = _mrt._build_character_anchors(bank)
                if _ca and _ca != "无已知角色":
                    _char_anchors_str = (
                        "## INPUT 2：角色锚点（仅供字段查询，不重生成）\n"
                        "以下角色名+服装色等锁定字段，修复时服装色必查本表：\n"
                        f"{_ca}"
                    )
            except Exception:
                _char_anchors_str = ""

        # skills（S03）：**按本轮问题的依赖选段**，不再无条件塞 1.3 万字全集。
        # 未登记的题型仍回退完整上下文并显式告警——减少体积不能以静默丢信息为代价。
        try:
            _skill_sections, _skill_unclassified = _mtg._repair_skill_sections(
                picked,
                {"_audit": [{"code": getattr(_ai, "code", ""),
                             "message": getattr(_ai, "message", "")}
                            for _ai in (audit_issues or [])]})
            if _skill_unclassified:
                _mrt._logger.warn(
                    "[REPAIR] 本轮问题类型尚未登记技能依赖——保留**完整**技能上下文"
                    "（体积换安全；登记依赖后再裁剪）")
                _skills_str = _mrt._load_knowledge_skills()
            else:
                _skills_str = _mrt._load_knowledge_skills_for(_skill_sections)
            _mrt._logger.info("[REPAIR] 技能段=%s（未分类=%s，%d 字）"
                         % (sorted(_skill_sections) or "无", _skill_unclassified,
                            len(_skills_str)))
        except Exception as _skill_err:
            _mrt._logger.warn("[REPAIR] 技能段选择失败，回退完整上下文: %s" % _skill_err)
            try:
                _skills_str = _mrt._load_knowledge_skills()
            except Exception:
                _skills_str = ""

        # Turn 0：INPUT（当前分镜 + 角色锚点 + skills，不含 novel）
        _input_turn = (
            "## INPUT 1：当前分镜（待修复）\n"
            "```json\n"
            f"{_current_script_json}\n"
            "```\n\n"
            f"{_char_anchors_str}"
        )
        if _skills_str:
            _input_turn += (
                "\n\n## INPUT 3：写作风格参考（skills 词库）\n"
                "运镜/光线/招式/打斗等词汇从这里取（不要重新发挥）：\n"
                f"{_skills_str}"
            )

        # 08-21 mode 合并：总是注入原文切片（原 mode B 特性，对 grounding 类问题很有用，
        # 合并后无副作用：_input_turn 的 narration_segment 锚定校验本来就该有原文对照）
        # - 按 narration_segment 在 novel_text 里做最长公共子串定位，前后各扩 200 字
        # - 多个 picked 镜共享同一段原文时自动合并去重
        # - 仅供「ground 修复」用，禁止触发 LLM 重新拆镜（依然保留手术刀原则）
        if novel_text:
            try:
                _novel_slice = _mra._build_novel_slice_for_picked(picked, shots_by_id, novel_text, ctx=200)
                if _novel_slice:
                    _input_turn += (
                        "\n\n## INPUT 4：原文切片（仅供 ground 待修问题，禁改动 narration_segment）\n"
                        "以下是从原文中按 picked 镜的 narration_segment 定位切出的片段，"
                        "用于 ground「动作时刻」「致命伤 anchor」「kf 时段」等问题：\n"
                        "**只用来查时间/动作/伤情对应关系，禁止用于重新拆镜、合并镜头、"
                        "改动 narration_segment 文本。**\n"
                        f"{_novel_slice}"
                    )
            except Exception as _e:
                _mrt._logger.warn(f"[WARN] 原文切片构建失败，回退到无原文切片: {_e}")

        # Turn 2：REPAIR FEEDBACK + TASK
        # 警告转为结构化 JSON（按 shot_xxx 分组），避免 freeform 文本混排字段
        # 08-21 mode 合并：总是带全片错误分布
        _audit_by_shot = {}
        from step01_script_gen.repair_fields import (validate_repair_targets,
                                                     _is_structural_code,
                                                     _target_id_of)
        _inexec_issue_keys: list = []      # 只能改锁定字段的审计问题（v6.150）
        for _ai in (audit_issues or []):
            # v6.150（复核发现 3）：**按问题**过滤，而不是只按字段授权面过滤。
            # 同一镜头可能同时挂着「只能改锁定字段」与「可修」的问题——只过滤字段
            # 会让前者仍进反馈、仍被模型要求修（白耗轮次），而它最终修复不可执行。
            _ai_paths = [str(getattr(_ai, "field_path", "") or "")]
            _ai_declared = getattr(_ai, "repair_targets", None)
            if isinstance(_ai_declared, list):
                for _p in _ai_declared:
                    if isinstance(_p, dict) and _p.get("field_path"):
                        _ai_paths.append(str(_p.get("field_path")))
            _ai_exec, _ai_locked = _mra._split_executable_allowed(_ai_paths)
            if _ai_paths and not _ai_exec:
                _inexec_issue_keys.append((str(getattr(_ai, "code", "") or ""),
                                           _ai_locked[:2]))
                continue
            _detail = {
                "code": _ai.code,
                "frame_id": _ai.frame_id,
                "entity_id": _ai.entity_id,
                "field_path": _ai.field_path,
                "evidence": _ai.evidence,
                "message": _ai.message,
            }
            _keys = {_mfa._target_of_record(_ai)}
            # v6.122：**成对目标点到的镜头也要看到这条问题的原文**。联动组成员常常
            # 自身没有任何告警（问题只定位在锚点镜），若只给它 allowed_fields 而不给
            # 问题描述，模型无从知道那一镜要改成什么——「整组入选」却修不动，
            # 结果还是半条修复。
            _declared = getattr(_ai, "repair_targets", None)
            if _declared:
                _v, _rej = validate_repair_targets(
                    _declared, shots_by_id,
                    structural=_is_structural_code(str(getattr(_ai, "code", "") or "")))
                if not _rej:
                    _keys |= {_target_id_of(_p["shot_id"]) for _p in _v}
            for _k in _keys:
                if _k:
                    _audit_by_shot.setdefault(_k, []).append(_detail)
        if _inexec_issue_keys:
            _mrt._logger.warn(
                "[REPAIR] %d 条审计问题**只能改锁定字段**——最终修复不可执行，"
                "已从本轮修复反馈中摘除（保留 blocking 交上游）：%s"
                % (len(_inexec_issue_keys), _inexec_issue_keys[:4]))
            _mrt._metrics_inc("repair.inexecutable_locked_issues", len(_inexec_issue_keys))
        # v6.83（二次审核发现 2）：**按 issue 授权**——每个 target 只允许改它自己的
        # 问题点名的字段。允许集合由 deterministic warning + audit issue 推导
        # （`repair_fields.allowed_repair_fields`）；**推不出字段路径的目标不进本轮
        # 定向修复**（那类交整镜 regen），因为没有任何依据限制它可以改什么。
        from step01_script_gen.repair_fields import allowed_repair_fields

        # v6.103：授权优先取**结构化 issue 的精确字段路径**（确定性层 + 模型审计），
        # 只有当结构化路径一条都推不出来时才回退到从告警文案解析关键词。
        #
        # 为什么必须这样：文案是给人看的——「关键帧2 描述称…」里的「关键帧」会把
        # 授权面撑到 `keyframes.*.desc`，而这条 issue 真正要改的是
        # `frame_layouts.kfs.*.on_screen` 这一个叶子。宽授权会让模型顺手动同镜的
        # 其他帧，正是总计划 §2.3「禁止顺带修改」要拦的。
        # v6.119：issue 的**可达性**判据从「问题定位在本镜」改成「成对目标含本镜」。
        # 上面那条旧判据把跨镜 issue 挡在了组内成员之外——联动镜头因此拿不到授权，
        # 结构上修不好（shot 5 跨镜睁眼问题的成因之一）。
        _all_structured: list = [
            _msc._issue_from_record(_rec) for _rec in (per_shot_dict.get("issues") or [])
        ] + list(audit_issues or [])

        def _issue_reaches(_ai, _tid: str) -> bool:
            """该 issue 是否参与 `_tid` 的授权.

            有成对目标 → **只看成对目标里有没有这一镜**（校验通过的才算）；
            没有成对目标 → 沿用「问题定位在哪」的旧口径（旧审计产物 / 模型写空数组）。
            """
            _declared = getattr(_ai, "repair_targets", None)
            _model_audit = _is_model_audit_code(getattr(_ai, "code", ""))
            if _model_audit or _declared is not None:
                # 模型审计（成对目标是契约）或显式声明 → **只**认成对目标：
                #   缺 / `[]` / 任一无效 → 不参与任何镜头的授权（fail-closed，
                #   不退回「问题定位在哪」的旧口径——那是静默降级）；
                #   全部有效 → 只给成对目标点到的镜头。
                if not _declared:
                    return False
                _valid, _rejected = validate_repair_targets(
                    _declared, shots_by_id,
                    structural=_is_structural_code(getattr(_ai, "code", "")))
                if _rejected:
                    return False          # 整条拒绝，不留半组
                return any(_target_id_of(_p["shot_id"]) == _tid for _p in _valid)
            return _mfa._target_of_record(_ai) == _tid

        _allowed_by_target: dict = {}
        # M02（2026-11-12）：本轮**授权面台账**——`allowed` / `declared` /
        # `beyond_declared` 三栏。台账要能回答「这一轮为什么允许改这些字段」，
        # 而不是只能从提示词正文反推（S06 的 `repair_diag.json` 没有这一栏，
        # 「授权面是否足够」当时无法从盘上核实）。
        _auth_ledger: dict = {}
        _unauthorized_targets: list = []
        _inexecutable_locked: dict = {}     # tid → 命中的锁定字段（v6.149 预检）
        for _tid, _ws in picked.items():
            if _tid == "global":
                continue
            _structured = [_ai for _ai in _all_structured if _issue_reaches(_ai, _tid)]
            _allowed = (allowed_repair_fields(
                            [], _structured, target_shot_id=_tid,
                            shots_by_id=shots_by_id)
                        or allowed_repair_fields(
                            _ws, [], target_shot_id=_tid, shots_by_id=shots_by_id))
            if not _allowed:
                _unauthorized_targets.append(_tid)
                continue
            # v6.149 可执行性预检：摘掉轮级锁定字段。逐**目标**判断——同一目标若还有
            # 别的可执行字段，照常进修复（不按 code 一刀切）。
            _exec, _locked_hit = _mra._split_executable_allowed(_allowed)
            if not _exec:
                _inexecutable_locked[_tid] = _locked_hit or [str(a) for a in _allowed]
                continue
            _allowed_by_target[_tid] = _exec
            _declared_paths = _mrc._declared_repair_paths(_structured, _tid)
            _auth_ledger[_tid] = {
                "allowed": sorted(str(p) for p in _exec),
                "declared": sorted(_declared_paths),
                "beyond_declared": sorted(str(p) for p in _exec
                                          if str(p) not in _declared_paths),
            }
        if _unauthorized_targets:
            _mrt._logger.warn(
                "[REPAIR] %d 个目标的问题未点名字段路径，本轮不走定向修复：%s"
                "——本实现**没有**自动整镜 regen 路径，这些目标会保留到最终审计，"
                "仍不通过就由提交点 BLOCK（不放开授权去硬修）"
                % (len(_unauthorized_targets), _unauthorized_targets[:6])
            )
        if _inexecutable_locked:
            _mrt._logger.warn(
                "[REPAIR] %d 个目标的修复**只能改锁定字段**——最终修复不可执行，"
                "保留 blocking 交上游阶段（本轮不为它们发起修复，也不放开授权硬修）：%s"
                % (len(_inexecutable_locked),
                   dict(list(_inexecutable_locked.items())[:4])))
            _mrt._metrics_inc("repair.inexecutable_locked_targets",
                         len(_inexecutable_locked))
        if not _allowed_by_target:
            _mrt._logger.warn("[REPAIR] 无任何目标具备明确字段授权，跳过本轮定向修复")
            return script
        # M02：同一份结构化授权记录，驱动两处请求说明、compact 答卷示例与应用校验。
        # 生成的是「当前稿上可实际应用的具体路径」，不是父字段或通配路径。
        from step01_script_gen.repair_authorization import (
            repair_authorization_spec as _auth_spec_fn,
            render_authorization_guidance as _auth_guidance_fn)
        _auth_spec_by_target = {
            str(tid): _auth_spec_fn(shots_by_id.get(tid) or {}, paths)
            for tid, paths in _allowed_by_target.items()}
        _auth_guidance = _auth_guidance_fn(_auth_spec_by_target)
        # v6.155（2026-10-04 实测根因，台账 §19.2）：入轮目标里若**没有任何一个**目标
        # 能改到本轮 blocking 点名的字段，这一轮**注定零产出**——章 2 实测：5 条 blocking
        # 的 `repair_targets` 全空 ⇒ 零授权，修复轮照样发起了一次调用，模型按审计建议改了
        # `frame_layouts.*`，全部被判「未授权」回填 ⇒ 字段触达=0 ⇒ `no_target_resolved`
        # ⇒ 整轮回退 ⇒ BLOCK。白烧一次调用，日志还看起来像「修了但没修好」。
        # 这里在**发起前**判死并如实记原因（与 `_unauthorized_targets` 同一口径：不放开
        # 授权去硬修，只是不再为一个不可能成功的轮次付代价）。
        _blocking_issues = [_ai for _ai in (audit_issues or []) if _mrt.is_blocking(_ai)]
        if _blocking_issues and not _mtg._repair_blocking_coverage(
                _blocking_issues, _det_targets, _allowed_by_target):
            _mrt._logger.warn(
                "[REPAIR] %d 条 blocking 的授权面与本轮入轮目标的授权面**没有交集**"
                "——本轮任何修复都不可能让它们中的任何一条通过验收，跳过本轮定向修复"
                "（保留 blocking 交提交点 BLOCK，不放开授权去硬修）"
                % len(_blocking_issues))
            _mrt._metrics_inc("repair.round_skipped_no_blocking_coverage",
                         len(_blocking_issues))
            return script

        _feedback_json = {
            "mode": "unified",
            "global_error_distribution": [
                {"label": l, "total": n} for l, n in _label_total.most_common()
            ],
            "shots": {},
        }
        for _tid, _ws in picked.items():
            if _tid == "global" or _tid not in _allowed_by_target:
                continue
            # S03：**不再复制镜头正文**。当前值只有一份权威表示——INPUT 1。
            # 这里只给身份、授权面与问题；模型要读现值就看 INPUT 1 的同名 shot。
            _feedback_json["shots"][_tid] = {
                "shot_ref": "INPUT 1 里键为 %s 的对象" % _tid,
                "audit_issues": _audit_by_shot.get(_tid, []),
                # **只允许改这些字段**；其余字段逐字照抄原镜
                "allowed_fields": sorted(_allowed_by_target[_tid]),
                # S04：这些字段会被**一起核对**（不是授权面）。改描述却不改同帧布局，
                # 会让两者不一致并在下一轮被当成新问题；模型可以据此一次改到位。
                "related_check_fields": _mtg._related_check_fields_for(
                    _tid, _allowed_by_target[_tid], shots_by_id),
                "warnings": _ws,
            }
        # §14 P3：**冲突 issue 进下一修复请求**的承载层——`_feedback_json["conflicts"]`
        # 与下面的 `_effect_conflict_block` 同源（同一份 `render_conflict_feedback`）。
        # 内含四项：source_revision、目标、原文/绑定证据、请求→应用→撤销阶段、允许修改范围。
        if _effect_conflict_prev.get("text"):
            _feedback_json["conflicts"] = {
                "attribution": _effect_conflict_prev.get("reason", ""),
                "writer": _effect_conflict_prev.get("writer", ""),
                "source_revision": _effect_conflict_prev.get("source_revision", ""),
                "targets": _effect_conflict_prev.get("targets") or [],
                "text": _effect_conflict_prev["text"],
            }
        _effect_conflict_block = str(_effect_conflict_prev.get("text") or "")

        # 08-21 mode 合并：单一 unified prompt 替代原 A/B/C 三套
        # 含：全片错误分布（_input_turn）+ top-3 hint + 4 类约束 + 决策树 + 3 组正反例
        # + 同角色跨镜一致性 + 5 条修复要点 + 同镜内 anchor 保留
        _repair_turn = (
            "## REPAIR FEEDBACK（结构化 JSON，按 shot 分组）\n"
            "```json\n"
            f"{_json.dumps(_feedback_json, ensure_ascii=False, indent=1)}\n"
            "```\n\n"
            + _top3_hint + _retry_feedback + _effect_conflict_block +
            "## TASK（精简修复）\n"
            # v6.120（用户决定）：原文案要求「一条 warn 只动一处」，与成对授权
            # 直接冲突：一条 kf 时序矛盾的正解往往是 `kf.desc + kf.stage +
            # 布局条目` 三处一起改，只改一处的「半条修复」自相矛盾、引入新阻塞、
            # 整组被回退（2026-09-25 实测 shot 5 正是这个形态）。
            "请**逐条**修复下方列出的问题；每条问题只改**解决它所需的已授权字段**——"
            "**需要联动时一次改全**（例如 kf 时序矛盾牵涉 kf 的 desc / stage /"
            "对应布局条目，就一起改；审计已把这些字段都列为授权目标）。\n"
            "优先只返回被点名镜头的窄字段补丁 `updates`（路径和值），无需重抄未改字段。格式："
            f"{_mra._repair_answer_example(shots_by_id, picked, _allowed_by_target, compact=True)}\n"
            "上面是本次授权字段与当前值的 JSON 示例；只提交需要修改的授权路径。`updates` 可为路径到值的字典，或 field_path/value 列表；未提交的字段由代码保留。完整 shot 替换必须单独使用；`updates` 与 `keyframe_additions` **可以在同一目标上并用**（先落字段补丁，再装配新帧）。\n"
            f"{_auth_guidance}"
            "**hook 必须放在 `hook_replacement` 里**（它与普通镜头字段不同，无 scene/location），"
            "普通镜头放 `shot_replacements`；两个键都可为空数组/省略。\n"
            # 2026-09-27（用户决定）：缺帧这类**联动**修复改「成对数据」形状——
            # 模型给帧 + 对应布局，**代码**按时间顺序装配两半。实测返回完整镜头时
            # 模型会把 keyframes 加到 4 而 frame_layouts.kfs 留在 3，长度校验整条拒收。
            "**联动修复（问题里出现「缺帧 / 没有按时间归属它的关键帧」时）用成对形状**："
            "{\"shot_replacements\":[{\"shot_id\":\"3\",\"keyframe_additions\":"
            "[{\"frame\":{\"desc\":\"…描写该时段动作…\",\"t_sec\":0.5,"
            "\"stage\":\"pre\"},\"layout\":[{\"name\":\"…\",\"pose\":\"…\","
            "\"on_screen\":true}]}]}]}——`frame` 与 `layout` **必须成对给出**，"
            "代码按 `frame.t_sec` 的时间顺序把两半装进**同一声序号**"
            "（两表长度由装配保证一致），缺任一半整条拒收。"
            "**代码不会替你猜姿态/持物**：`layout` 里的角色姿态、持物、on_screen "
            "必须由你写全。\n"
            "**同一个镜头只能用一种表达补同一帧**：若 `updates`／整镜替换里已经包含该帧，"
            "禁止再用 `keyframe_additions` 追加它——时刻重复会被判重复事件，整轮回滚。\n"
            "shot 必须与首次生成**同构**——index/narration_segment/subtitle/prompt/motion_desc/"
            "ff_desc/lf_desc/duration/keyframes/frame_layouts 等**成套出现**，缺一即被拒。\n"
            "**未点名的镜头绝不允许出现在 shot_replacements 里**；每个被点名镜头**恰好出现一次**"
            "（重复、遗漏、多出都会让整批回滚）。\n"
            "`shot_id` 用**裸镜号**（例如 \"10\"，**不是** \"shot_10\"；"
            "REPAIR FEEDBACK 里的 `shot_10` 只是分组键）。\n"
            "**字段授权（硬约束）**：REPAIR FEEDBACK 里每个镜头的 `allowed_fields` 列出该镜"
            "**本轮允许修改的字段路径**；未列入的字段必须**逐字照抄原镜**，"
            "改一个字节都会让整批回滚（motion_desc / temporal_plan / keyframes / duration "
            "默认不在授权内，除非问题明确点名）。\n"
            "结构字段必须彼此自洽：keyframes 与 frame_layouts.kfs 等长；"
            "kf.t_sec 落在 (0, duration] 内；stage 取 pre/approach/climax/react/exit/after。\n"
            "**若 `allowed_fields` 里出现 `keyframes`**（只出现在「kf 时段缺段」这类"
            "**必须增删帧**才能修的问题上）：允许**增删帧**，但必须同时满足——"
            "① `frame_layouts.kfs` 同步增删，两者**等长且序号对应**；"
            "② 每帧 t_sec 落在 (0, duration] 且**按时间升序**排列；"
            "③ 新增帧的 desc/stage 对应 `motion_desc` 的**未覆盖时段**"
            "（time_ratio = 段中点 / duration_sec）；"
            "④ **`motion_desc` 的原有事件文本逐字保留、顺序不变**——"
            "只允许追加过程描述，禁止删事件、改顺序、合并时段"
            "（那是让告警消失而不是修好，会被确定性闸门整轮回退）；"
            "⑤ **默认不动已有帧**（只在列表里**追加**新帧）：把已有帧挪到别的"
            "时刻却**不改它的文案**，会让内容落进另一个时段，立刻触发新的"
            "`kf_event_window_mismatch`（实测：补 1 帧换来 1 条新阻塞，整轮被回退）。"
            "**例外（问题文案明确要求时）**：当问题写「已有帧与相邻段**时间/内容"
            "错位**」时，允许**一并调整**该帧的 `t_sec` / `desc` 与**对应序号**的"
            "`frame_layouts.kfs` 条目——但改 `t_sec` 必须与改文案**成对**做；"
            "只搬时刻不改文案等于把缺陷挪到相邻段（实测：1-2s 的「反手一掌」"
            "被画在 2.5s、文案写「掌风扫向」）。**验收口径：每个需帧时段都有一帧、"
            "且该帧文案描写的是它自己那段。**\n\n"
            "**手术刀原则（最高优先级）**：\n"
            "1. 一条 warn 只改**它授权范围内的字段值**；镜头内其余字段**逐字保留**——"
            "优先输出 updates 窄补丁；兼容完整镜头时也不得重写未授权字段。"
            # v6.116（审核）：**例外**——同镜多个关联问题必须一次改到自洽，
            # 否则「一条 warn 只改一处」会让 motion/KF/尾帧三处互相矛盾。
            # v6.120（用户决定）：例外**不再局限于「同镜多个问题」**——一条问题
            # 本身就可能需要多字段联动（kf.desc + kf.stage + 布局条目）。
            "**例外（联动必须一次改全）**：一条问题需要多个字段才能改到自洽时"
            "（例如 kf 时序矛盾要同时改该帧的 desc / stage / 所属布局条目），"
            "**必须一次改全**——漏改的部分会被回填，留下自相矛盾的半条修复；"
            "同镜多个关联问题同理（补了过程动作就同步补对应 KF 与尾帧承接），"
            "不得机械地一处一处改而互相矛盾；\n"
            "2. 禁止**无关**新增（动作/情绪/运镜/光位/转场/视角/装饰性方位）；"
            # v6.116（审核）：原句「禁止新增任何短语」与正解**自相矛盾**——被点名的问题
            # 常常正是「motion_desc 缺少跃船动作」，正解就是**补动作**。改为只禁无关新增。
            "**为修复被点名的问题，可以在授权字段补必要动作**——"
            "「motion_desc 缺少跃船过程」的正解就是补上该动作，不得因为「禁止新增」而回避；"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但**禁止写成「望着她」「看角色甲」这类无空间方位词**\n"
            "3. 禁止未经授权改景别/时长；台词/剧情/narration_segment 文本保持原样\n"
            "4. 禁止增删镜头、改 shot.index\n"
            "5. 只输出 shot_replacements，不输出整本脚本；未列问题的镜头绝不进入；"
            "替换镜头内**未被 warn 点到的字段逐字保留**（可照抄原镜文本）\n"
            "patch 未覆盖的字段**逐字保留**\n"
            "6. **lf_desc 至少 40 字，含本镜所有出镜角色的完整静态布局**（姿态/位置/服装色/视线）"
            "——过短（如「已没入水下，角色甲仰卧船头」单句）会丢角色状态、产生内部矛盾\n\n"
            "**关键字段保留白名单（最高优先级，违反 = 致命伤/漂色/连续性破缺）**：\n"
            "即使该字段不在 warn 列表中，下述字段也**禁止删/改/移位**——\n"
            "C 模式实验教训：改服装色 warn 连带删掉致命伤 anchor（shot14/24/25）。\n"
            "  - **致命伤 anchor**：心口/洇痕/暗红/中刀/中剑/伤口/血痕/腹部/胸口/心窝 等\n"
            "  - **服装色**（每个出镜角色）：素白轻袍/一袭春衫/青衣小帽/玄色劲装 等（取自角色库）\n"
            "  - **角色当前状态**（坐/站/仰卧/跪坐/持物 等已建立姿态）\n"
            "  - **锚点连贯子句**（前镜 lf_desc 已锚定的姿态/位置/朝向，跨镜承接必须保留）\n"
            "  - **color_tone**（场景色调基底词，如「冷蓝星光」/「暖金烛光」——跨镜共享）\n\n"
            "**Mask 字段清单（本轮禁止修改）**：\n"
            "  - narration_segment（台词，原文锚定，改则 grounding 校验必挂）\n"
            "  - index（结构性字段，动了会改镜头排序）\n"
            "  - duration_sec（由时间轴序列化函数从 duration 统一派生；**需要改时长时改 duration**，且仅限本轮 warn 明确授权的镜头）\n"
            "  - subtitle（与 narration_segment 逐字相同，规则 1 锁定）\n"
            "  - variation_type（small/large 变体类型，动了会改 keyframe 数量）\n"
            "  - scene / location（场景字段，规则 7.5 锁定）\n\n"
            "**同镜内 warn 改字段时必须保留同字段 anchor（最高优先级）**：\n"
            "若一条 warn 改 X 字段（ff_desc/lf_desc/prompt），该字段内已存在的\n"
            "  致命伤 anchor / 服装色 / 角色状态 / 锚点连贯子句 必须**逐字保留**。\n"
            "例：ff_desc 报「缺服装色」→ 仅追加缺色角色的服装色，**禁止**顺手删\n"
            "  已有的「肩头暗红洇痕」；lf_desc 报「缺服装色」→ 同样只补服装色，\n"
            "  已有的「仰卧/血痕/配角捂嘴」等不删。\n\n"
            "**修复决策树（最高优先级，不知道改哪里时按表走）**：\n"
            "- ff_desc/lf_desc 报「缺服装色」→ 仅在对应字段末尾追加缺色角色服装色，\n"
            "  已有 anchor 全保留（见上「同镜内 anchor」原则）\n"
            "- ff_desc/lf_desc/prompt 报「视线无空间方位」→ 把「看/盯/望+人名」\n"
            "  改写为「目光紧盯人名（画X）」；无空间方位词 = 必挂\n"
            "- 报「致命伤/洇痕缺失」→ 在 prompt + lf_desc 补「心口处白袍暗红洇痕持续可见」\n"
            "- 报「kf 时序矛盾」→ 改 kf.desc（不要改 prompt），按 pre-stab/stab/post-stab 递进写；"
            "若该帧的 `stage` / 所属布局条目（`frame_layouts.kfs[i][n].props` 等）与该矛盾"
            "直接相关（如道具在丢后又出现），**一并改**——这些字段审计已列为授权目标，"
            "只改 desc 会留下半条修复\n"
            "- 报「景别跳跃」→ 后一镜景别词改为中间级，同步改 prompt 开头 + ff_desc 构图\n"
            "- 报「光位模糊」→ 「柔和光线」改为「45°侧光/顶光/逆光」等具体光位\n"
            "- 报「kf 时段缺段」→ 按 motion_desc 时段表补 1 帧定格（time_ratio = 段中点/duration_sec）\n\n"
            "**3 组正反例（最高优先级）**——C 模式实验的具体教训：\n"
            "1. **致命伤保护**：\n"
            "   - ❌ 反例：报「缺服装色」→ 整段重写 ff_desc（顺手删肩头暗红洇痕）\n"
            "   - ✅ 正例：报「缺服装色」→ 仅追加「袭击者玄色劲装」5 字到 ff_desc 末尾，\n"
            "     已有的「肩头暗红洇痕/仰卧/配角跪坐」一字不动\n"
            "2. **视线方位**：\n"
            "   - ❌ 反例：报「视线无方位」→ 删整句「角色甲望一眼星空」\n"
            "   - ✅ 正例：报「视线无方位」→ 改写为「角色甲仰望画上方星空」\n"
            "     （加方位词 + 保留原意，**这是规则 22.5a 硬性要求，禁砍**）\n"
            "3. **lf_desc 完整性（v6.37 实测坑）**：\n"
            "   - ❌ 反例：报「服装色遗漏」→ 整句改写「角色甲仰卧」为「角色甲仰卧船头」\n"
            "     （丢原 anchor 短语 + lf_desc 砍到 21 字 / 产生内部矛盾\n"
            "     「已没入水下」+「举刀正对」单句并存）\n"
            "   - ✅ 正例：报「服装色遗漏」→ 仅追加「素色轻袍」到 lf_desc 末尾，\n"
            "     原 lf_desc 完整保留 + ≥40 字下限（防 lf_desc 整体被砍短）\n\n"
            "**同角色跨镜一致性（最高优先级）**：\n"
            "同一角色在多镜出现时，**服装色 / 当前状态（坐/站/仰卧）/ kf 顺序** 必须跨镜一致：\n"
            "- 服装色：shot_1 角色甲素色轻袍 → shot_5 角色甲也必须素色轻袍，禁中途换色\n"
            "- 当前状态：shot_3 角色甲盘坐 → shot_4 角色甲仍盘坐，禁无动机跳变（站起/躺下需承接）\n"
            "- 致命伤持续：被刺后所有镜头都需带「肩头暗红洇痕」（不只是伤镜）\n\n"
            "修复要点：\n"
            "1. ff/lf_desc 锚点：威胁源写朝向/目标、出镜角色重述服装色\n"
            "2. keyframes desc：补角色姿态/位置锚定\n"
            "3. 保持原文锚定（narration_segment 逐字来自原文）、服装色、致命伤连续性不变\n"
            "4. 镜头衔接/动作重叠（跨镜）：看 INPUT 1 中该镜的 ff_desc / lf_desc"
            "——相邻镜头同地点时在结尾补衔接词（自然过渡/淡入淡出等），后一镜"
            "开头承接前一镜结尾的动作，禁止无承接硬切\n"
            "5. 景别跳跃（无夹心的真跳跃）：把后一镜的景别改为中间级（如全景→中景），"
            "同步改该镜 prompt 开头的景别词与 ff_desc 构图——禁止只改景别词不改"
            "构图（图视不一致），也禁止增删镜头"
        )

        # user_inputs：两条 user 消息
        _user_inputs = [
            {"role": "user", "content": _input_turn},
            {"role": "user", "content": _repair_turn},
        ]

        # 向后兼容：旧 user_prompt 也保留（其他 provider 走 chat 时仍拼一段）
        # 旧实现：prompt = build_prompt(novel_text, bank, objects_bank) + repair_prompt
        # 新实现：user_inputs 拆两条；user_prompt 用作"未支持多轮的 provider"的回退
        # v6.68: prompt/system 跟着实际模型走（修复 prompt 本身 provider 中立）
        _rep_builder, _rep_system = _mrt.resolve_model_prompt_profile(model)
        prompt = _rep_builder(novel_text, bank, objects_bank)

        # 08-21 mode 合并：单一 unified fallback（与 _repair_turn 同源，压缩为单行 + 决策树/正反例/跨镜一致性）
        prompt = prompt + (
            "\n\n# 定向修复\n"
            "以下是分镜问题清单。请**逐条**修复；每条问题只改**解决它所需的已授权字段**——"
            "**需要联动时一次改全**（如 kf 时序矛盾牵涉 kf 的 desc / stage / 布局条目）。"
            "优先返回被点名镜头的窄字段补丁 `updates`（路径和值）。格式："
            f"{_mra._repair_answer_example(shots_by_id, picked, _allowed_by_target, compact=True)}\n"
            "上面是本次授权字段与当前值的 JSON 示例；只提交需要修改的授权路径。`updates` 可为路径到值的字典，或 field_path/value 列表；未提交的字段由代码保留。完整 shot 替换必须单独使用；`updates` 与 `keyframe_additions` **可以在同一目标上并用**（先落字段补丁，再装配新帧）。\n"
            f"{_auth_guidance}"
            "hook 放 hook_replacement，普通镜头放 shot_replacements，两者都可为空；"
            "shot_id 用裸镜号（\"10\"，不是 \"shot_10\"）；"
            "每个被点名镜头恰好出现一次（重复/遗漏/多出则整批回滚）。"
            "未点名镜头绝不出现；镜头内**未被 warn 授权的字段逐字保留**"
            "（见 REPAIR FEEDBACK 的 allowed_fields）。"
            f"{_global_block}"
            + _top3_hint +
            "**手术刀原则（最高优先级）**：一条 warn 只改它授权范围内、解决该问题所需的字段"
            "（需要联动时**一次改全**）——不要批量重写整镜；"
            "禁止**无关**新增（动作/情绪/运镜/光位/转场/视角/装饰性方位）——"
            "但为修复被点名的问题**可以补必要动作**（如「motion_desc 缺跃船过程」就补该动作）；"
            "同镜多个关联问题要一次性让 motion_desc/KF/尾帧自洽，"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但禁止写成无空间方位词；"
            "禁止未经授权改景别/时长；台词/剧情/narration_segment 文本保持原样；禁止增删镜头、改 shot.index；"
            "**lf_desc 至少 40 字**，含本镜所有出镜角色完整静态布局——过短会产生内部矛盾。\n"
            "**关键字段保留白名单（最高优先级）**：致命伤 anchor（心口/洇痕/暗红/中刀/中剑/伤口/血痕 等）"
            "+ 服装色（每个出镜角色，取自角色库）+ 角色当前状态（坐/站/仰卧/跪坐/持物 等）"
            "+ 锚点连贯子句（前镜 lf_desc 已锚定的姿态/位置/朝向，跨镜承接必须保留）"
            "+ color_tone（场景色调基底词）——即使不在 warn 列表中也**禁止删/改/移位**"
            "（C 模式实验教训：改服装色 warn 连带删掉致命伤 anchor）。\n"
            "**Mask 字段清单（本轮禁止修改）**：narration_segment / index / duration_sec /"
            " subtitle / variation_type / scene / location——结构性字段，改则破坏排序/时长/原文锚定。\n"
            "**若 `allowed_fields` 含 `keyframes`**（「kf 时段缺段」这类问题）："
            "允许增删帧，但 `frame_layouts.kfs` 必须同步增删（等长、序号对应）、"
            "t_sec 落在 (0, duration] 且按时间升序、新帧对应 motion_desc 未覆盖时段；"
            "**motion_desc 原有事件文本逐字保留、顺序不变**（只可追加，禁删改合并）；"
            "**已有帧默认逐字保留**，只在列表追加新帧（只挪时刻不改文案会让它落进"
            "别的时段 → 立刻多出一条窗口错配）；**仅当问题文案说「时间/内容错位」时**，"
            "才可同时改该帧的 t_sec + desc + 对应布局条目（改时刻必须与改文案成对）；"
            "缺帧优先用 `keyframe_additions` 成对形状（frame + layout 一起给，代码装配）。"
            "**新帧 desc 必须逐字嵌入该时段的动作短语**、且不要复述别的时段——"
            "新帧文案若更像相邻段，会被判窗口错配、整条回退。\n"
            "**同镜内 warn 改字段时必须保留同字段 anchor（最高优先级）**：改 ff_desc/lf_desc/prompt"
            " 的某条 warn 时，**禁止**顺手删该字段已有的 anchor 子句（致命伤/服装色/角色状态）。\n"
            "**修复决策树**（精简版）：缺服装色→追加；视线无方位→加画X/目光；致命伤缺→补洇痕；"
            "kf矛盾→改kf.desc（连带 stage/布局条目一起改）；景别跳→改中间级；光位模糊→具体光位。\n"
            "**3 组正反例（按例做）**：致命伤保护=追加5字不删anchor；视线方位=加方位词不砍原意；"
            "lf_desc完整性=追加不砍到<40字（防「已没入水下」+「举刀正对」矛盾）。\n"
            "**同角色跨镜一致性**：服装色/状态/kf顺序跨镜一致（角色甲素色轻袍不中途换色，致命伤持续到所有伤后镜）。"
            + _retry_feedback
            + "\n".join(parts)
        )

        _mrt._metrics_inc("calls.repair_round")
        # v6.133：上限只在这里取一次——既发给请求，也随调用记录落台账（余量可算）
        _repair_cap = _mtg._repair_max_output_tokens()
        # v6.133（2026-09-26 用户复核）：`create_service()` 原先在 try **之外**——
        # 工厂因配置/依赖问题抛异常时既不会被记成 `call_failed`，也进不了提交层归因
        # （只会以未捕获异常收场）。现在把初始化也纳入同一条失败记录路径
        # （`_repair_cap` 因此必须先取，否则记录里带不出本轮上限）。
        # 定向修复的**通道覆盖**（2026-09-28 实测后新增，语义与审计同源）：
        # agent_plan 通道把 `json_schema` 降级成 json_object → 修复的形状契约失效 →
        # `deepseek-v4.1-flash` **整镜原样回抄**（6/6 次 resolved=0、audit_field_touched=0），
        # 修复轮等于不可用；原生 deepseek 通道保留 strict json_schema。
        # 放在 try **之外**：配置写错（设了 provider 没给 model）必须**显式失败**，
        # 不能被下面「服务初始化失败 → 保留原版」吞成静默降级。
        _rep_channel_provider, _rep_channel_model = _mtg.resolve_repair_channel()
        try:
            service = (_mrt.create_service() if _rep_channel_provider is None
                       else _mrt.create_service(_rep_channel_provider))
        except Exception as e:
            _mrt._logger.warn(f"[WARN] 定向修复服务初始化失败（保留原版，未做任何后处理）: {e}")
            _mtg._record_repair_call_failure(repair_diag, _round,
                                        error="服务初始化失败：%s" % e,
                                        max_tokens=_repair_cap)
            return script
        from _text_gen.deepseek_schema import REPAIR_SHOT_REPLACEMENT_SCHEMA
        req = _mrt.TextGenerationRequest(
            system_prompt=_rep_system,
            user_prompt=prompt,
            user_inputs=_user_inputs,  # v6.37 结构化多轮
            # v6.101：8000 → 16000（每批 4 个完整镜头对象 + 上下文）。
            # v6.133（用户 2026-09-25 实跑 BLOCK）：**不再硬编码 16000**——该上限含
            # 思维链 token，实测同一请求一次截断一次成功（见 `_repair_max_output_tokens`），
            # 而 .env.content 对修复轮的推荐值本就是 50000。默认不传 = 跟随通道全局值。
            max_tokens=_repair_cap,
            temperature=0.1,
            json_schema=REPAIR_SHOT_REPLACEMENT_SCHEMA,
            # 通道覆盖优先（2026-09-28）：设了修复专用通道就用它的模型；
            # 否则沿用调用方模型（v6.67 接力修复语义不变）。
            model=_rep_channel_model or model,
            timeout_sec=_mrt._repair_timeout_sec(),  # v6.70.3: 修复轮 180s——超时保留原版，不再盲等 600s
        )

        # S03（复核 P1）：按 provider 的**最终 payload** 计量——messages 只是其中
        # 一部分，model/上限/采样/response_format/stream 同样是请求体。
        # 超限 ⇒ 不发送，明确记录并延后本轮目标。
        if _mtg._REPAIR_MAX_REQUEST_BYTES > 0:
            _final_bytes = 0
            try:
                if _rep_channel_provider in (None, "agent_plan"):
                    from _text_gen.agent_plan_provider import preview_wire as _wpv
                    _wp, _wb = _wpv(req, model=req.model)
                    # 只构造一次：容量判定、摘要与真正发送共用同一份 payload/bytes
                    from dataclasses import replace as _dc_replace
                    req = _dc_replace(req, prebuilt_payload=_wp, prebuilt_body=_wb)
                else:
                    from _text_gen.wire import encode_json_body as _we
                    _wb = _we({"messages": ([{"role": "system",
                                             "content": req.system_prompt}]
                                            + list(req.user_inputs or []))})
                _final_bytes = len(_wb)
            except Exception as _enc_err:
                _mrt._logger.warn("[REPAIR] 最终 payload 编码计量失败（按超限处理，不发送）: %s"
                             % _enc_err)
            if _final_bytes == 0 or _final_bytes > _mtg._REPAIR_MAX_REQUEST_BYTES:
                _omsg = ("修复请求最终 payload %d 字节 > 上限 %d（目标 %s）——不发送；"
                         "本轮目标整体延后，待装批或人工拆分"
                         % (_final_bytes, _mtg._REPAIR_MAX_REQUEST_BYTES, sorted(picked)))
                _mrt._logger.warn("[REPAIR] " + _omsg)
                if repair_diag is not None:
                    repair_diag.append({"repair_round": _round,
                                        "kind": "oversize_request_refused",
                                        "request_bytes": _final_bytes,
                                        "cap_bytes": _mtg._REPAIR_MAX_REQUEST_BYTES,
                                        "targets": sorted(picked)})
                if block_reasons is not None:
                    block_reasons.append(_omsg)
                _mrt._metrics_inc("repair.oversize_request_refused")
                return script
        # v6.84（二次审核发现 3）：模型调用失败/截断**立即返回**，不进提交尾部。
        #
        # 原实现用 `break` 跳出循环，然后仍会执行循环后的全局 `post_fix_shot_prompts()`
        # 与 `run_hard_fix()`——模型根本没产出任何合法 replacement，流程却对整本脚本
        # 做后处理，再把自己造成的非目标镜头变化判成 `non_target_changed` 整轮回滚。
        # 本次实测就是这个形态：8000 token 截断 → JSON 丢弃 → 仍跑全局后处理 →
        # 未点名镜头 7 被改 → `resolved=0` 回滚。
        try:
            result = service.generate(req)
        except Exception as e:
            _mrt._logger.warn(f"[WARN] 定向修复失败（保留原版，未做任何后处理）: {e}")
            _mtg._record_repair_call_failure(repair_diag, _round, error=str(e),
                                        max_tokens=_repair_cap)
            return script
        if not result.success:
            _mrt._logger.warn(f"[WARN] 定向修复失败（保留原版，未做任何后处理）: {result.error}")
            _mtg._record_repair_call_failure(
                repair_diag, _round, error=str(result.error),
                truncated=bool(getattr(result, "truncated", False)),
                usage=getattr(result, "usage", None), max_tokens=_repair_cap)
            return script
        # v6.133：**成功调用也记 usage**——判断 `REPAIR_MAX_OUTPUT_TOKENS` 该设多大，
        # 靠的是"实际花了多少输出 token（含思维链）"，而不是"这次有没有踩线"。
        # 2026-09-25 实测：同一请求同一上限，一次截断一次成功 ⇒ 余量已在临界。
        _mtg._record_repair_call_usage(repair_diag, _round,
                                  usage=getattr(result, "usage", None),
                                  max_tokens=_repair_cap)

        import re as _re, json as _patch_json
        raw = result.text.strip()
        raw = _re.sub(r"^```json\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"^```\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"```\s*$", "", raw, flags=_re.MULTILINE)
        try:
            _patch_data = _patch_json.loads(raw)
        except Exception as e:
            _mrt._logger.warn(f"[WARN] 定向修复补丁 JSON 解析失败（保留原版，未做任何后处理）: {e}")
            _mtg._record_repair_unusable(repair_diag, _round,
                                    reason="parse_failed", detail=str(e),
                                    raw_head=result.text)
            return script
        # v6.77（第四阶段）：修复轮收**完整镜头替换**，不再是字段级补丁。
        # 结构字段成套出现由 schema（复用 _SHOT）保证；这里再做一遍语义校验，
        # 任一镜不通过则**整条丢弃**（不做部分应用），然后原子替换。
        # v6.83（二次审核发现 3）：envelope 的两个数组**都允许为空**。
        # 只点名 hook 的一轮，符合新 schema 的自然响应是
        # `{"shot_replacements": [], "hook_replacement": {...}}`；旧实现要求
        # `shot_replacements` 非空，在调用 applier **之前**就把它拒掉——hook-only
        # 修复因此在主调用路径上不可达（只有直接调 helper 才走得通）。
        _repls = _patch_data.get("shot_replacements") if isinstance(_patch_data, dict) else None
        _hook_repl = _patch_data.get("hook_replacement") if isinstance(_patch_data, dict) else None
        if _repls is None:
            _repls = []
        if not isinstance(_repls, list):
            _mrt._logger.warn("[WARN] 定向修复 shot_replacements 不是数组，保留原版"
                         "（未做任何后处理）")
            _mtg._record_repair_unusable(repair_diag, _round, reason="invalid_structure",
                                    detail="shot_replacements 不是数组：%s"
                                           % type(_repls).__name__,
                                    raw_head=result.text)
            return script
        if not _repls and not isinstance(_hook_repl, dict):
            _mrt._logger.warn("[WARN] 定向修复未返回任何替换镜头"
                         "（shot_replacements 与 hook_replacement 皆空），保留原版"
                         "（未做任何后处理）")
            _mtg._record_repair_unusable(repair_diag, _round, reason="empty_patch",
                                    detail="shot_replacements 与 hook_replacement 皆空",
                                    raw_head=result.text)
            return script
        # v6.80（发现 9 #1/#2）：hook 走独立的 `_HOOK` 承载；整批校验通过才提交。
        # v6.83（发现 4）：`expected_targets` 要求每个 picked 目标**恰好出现一次**——
        # 「全有或全无」必须覆盖「本轮请求的事务」，而不只是「返回的条目」。
        # `picked` 的键是 `shot_<id>` / `hook`；envelope 里的 `shot_id` 是裸 id。
        # 目标集合比较必须用同一口径，否则每个 picked 目标都会被判「缺失」。
        _expected_ids = {str(t)[5:] if str(t).startswith("shot_") else str(t)
                         for t in picked}
        # S04（2026-11-11）：留一份**模型原始返回**的镜头，供四栏台账区分
        # 「模型请求改了什么」与「授权后实际改了什么」。只认完整镜头替换；
        # 窄字段补丁没有完整对象，台账会如实标 requested_basis。
        _model_shots_round = {
            str(_it.get("shot_id")): _it.get("shot")
            for _it in (_repls or [])
            if isinstance(_it, dict) and isinstance(_it.get("shot"), dict)
        }
        _rej_records: list = []   # M03：生产者分类的结构化拒收
        merged, _n_applied, _rejected = _mra._apply_shot_replacements(
            script, _repls, set(picked), hook_replacement=_hook_repl,
            allowed_by_target=_allowed_by_target,
            expected_targets=_expected_ids,
            frame_mappings_out=_frame_mappings,
            rejections_out=_rej_records)
        # 映射绑定本轮基线 revision；跨 revision 的映射在消费前会被丢弃。
        for _mp in _frame_mappings.values():
            if isinstance(_mp, dict):
                _mp["source_revision"] = _effect_revision
        if _rejected:
            # v6.87：`_rejected` 里既可能是真被拒的条目，也可能是「某目标未返回替换
            # （其余已提交）」这类**信息性**回报（A1 逐目标提交后新出现）。分开打。
            _info_only = [r for r in _rejected if "本轮未返回替换" in r]
            _hard_rej = [r for r in _rejected if "本轮未返回替换" not in r]
            if _hard_rej:
                _mrt._logger.info(f"[REPAIR] 替换被拒 {len(_hard_rej)} 条: {_hard_rej[:3]}")
            if _info_only:
                _mrt._logger.info(f"[REPAIR] 部分提交说明: {_info_only[:2]}")
        if _n_applied <= 0:
            _mrt._logger.warn("[WARN] 定向修复替换镜头均未通过校验，保留原版（未做任何后处理）")
            _rejected_details = [str(x) for x in _rejected]
            _mtg._record_repair_unusable(repair_diag, _round, reason="rejected_by_validation",
                                    detail="；".join(_rejected_details[:3]),
                                    raw_head=result.text,
                                    extra={"rejected": list(_rejected),
                                           "authorization": _auth_ledger,
                                           "feedback_records": _rej_records})
            # M03：拒收原因由**应用器生产者**分类，不再靠「长度不等」之类的
            # 自由文本子串猜测。工程故障（帧映射内部失配等）优先终局；
            # 每个冻结目标集合最多发放一次答卷纠正机会。
            if retry_state is not None:
                _apply_plan = _plan_fb(
                    records=_rej_records,
                    prior_state=_retry_state_snapshot,
                    frozen_targets=sorted(picked),
                    source_revision=_effect_revision,
                    answer_sig=_answer_sig(_repls))
                retry_state.clear()
                retry_state.update(_apply_plan)
                _mrt._logger.warn(
                    "[REPAIR] 替换拒收反馈：codes=%s correction=%s terminal=%s"
                    % ([r.get("code") for r in _rej_records],
                       _apply_plan.get("correction_granted"),
                       _apply_plan.get("terminal")))
            return script
        _mrt._logger.info(f"[REPAIR] 应用完整镜头替换 {_n_applied} 镜")
        # M02：留一份「合并后、后处理前」的深拷贝——后处理是在**同一个对象**上
        # 就地改写的（`script = merged` 之后各处 writer 直接写 `script`），不拷贝
        # 就没有任何基线能回答「授权改动有没有被后处理抵消」。
        _post_merge_snapshot = copy.deepcopy(merged)
        # v6.87（2026-09-21 23:19 实测）：**锁定字段权威快照必须在合并后立刻取**。
        #
        # `_FINAL_LOCKED_FIELDS` 要回答的问题是「**LLM** 有没有改动锁定字段」。
        # 但在事务末尾才与「本轮开始前」比较时，中间隔着 `post_fix_shot_prompts` /
        # `run_hard_fix` / `write_timeline_from_durations` 这一串**管线自己的确定性
        # 归一化**——它们对被点名镜头的写入会被算成 LLM 越权。
        #
        # 实测（本轮 `resolved=11` 却整轮回退，reason=non_target_changed）：B.1
        # `narration_subtitle_consistency` 把被点名镜头 subtitle 末尾多余的「。」去掉了，
        # 而 `subtitle` 在锁定字段表里 → `_final_field_mask_violations` 报违例 →
        # 11 条已解决的阻塞一起丢。**这不是 LLM 越权，是管线在收拾 LLM 的稿子。**
        #
        # 正确基线 = 合并后、后处理前的状态：它只含 LLM 的改动。LLM 若真动了锁定
        # 字段，这里立刻就能看见（提交前另有 `check_replacement_authorized` 拦截）。
        # v6.98（2026-09-22 审核 P2-1 复核确认）：比较对象必须是 **merged**。
        #
        # 原实现先算再赋值，而此刻 `script` 仍是**旧稿**（该区间唯一的 `script =`
        # 就在下面一行）——于是这里比的是「旧稿 vs 旧稿」，`_mask_violations_after_patch`
        # **恒为空**：LLM 真的改了锁定字段也看不见（`locked_by_llm_changed` 成死代码）。
        # 实测确认：该区间内 `script` 未被就地改写，唯一赋值点即 `script = merged`。
        # 现在改成对 `merged` 比较——它正是「合并后、后处理前」，只含 LLM 的写入。
        _mask_violations_after_patch = _mra._final_field_mask_violations(
            _round_before_script, merged, set(picked),
            allowed_by_target=_allowed_by_target)
        # v6.104.1（2026-09-24 第二十四次重跑实测）：KF 结构验收的基线必须与
        # `_mask_violations_after_patch` **同源**——「合并后、后处理前」，只含
        # LLM 的写入。
        #
        # 原实现把它算在**后处理之后**的 `script` 上，于是 `run_hard_fix` 对原稿
        # 既有的重复残片做清洗（实测 shot 8 的 motion 里「语气眼神眼神眼神眼神眼神
        # 冰冷」被清成正常文本）时，判据拿**带残片的原文**去比 → 报「原事件文本
        # 丢失」→ 本轮 3 个已提交的替换被整轮回退，`resolved=0`。这与 v6.87/v6.95
        # 修的是同一类缺陷：闸门只管 LLM 的写入，管线自己的确定性清洗不算越权。
        _kf_struct_after_patch = _mrc._kf_structural_violations(
            _round_before_script, merged, _allowed_by_target, set(picked),
            novel_text)
        # v6.154（复核之二十 P1）：**帧子列表**被逐字点名时只允许纯增删条目
        # （保留条目逐字不变、相对顺序不变、非点名帧不动）。与 kf 闸门同时算、
        # 同源基线（都只看 LLM 的写入，不看管线自己的确定性清洗）。
        # S03 补正：**不再过滤**异常映射后退回同下标比较。跨 revision、缺映射、
        # 来源歧义一律由守卫产出结构化违例，直接进入本轮事务否决项；守卫与
        # 效果登记消费同一份映射、同一份基线 revision。
        _frame_mappings_guard = dict(_frame_mappings)
        _frame_mappings_final = dict(_frame_mappings_guard)
        _layout_struct_after_patch = _mrt.frame_layout_structural_violations(
            _round_before_script, merged, _allowed_by_target, set(picked),
            frame_mappings=_frame_mappings_guard, revision=_effect_revision)
        # §14 P1 节点②：**授权合并后**就是效果的基线——它只含 LLM 的写入。
        # 只登记「被逐字点名的帧子列表」上、且已过上述结构守卫的增删；
        # 之后每个后处理 writer 跑完再冻一次，看这条已授权效果有没有被抵消。
        try:
            _effect_records = build_layout_effects(
                _round_before_script, merged, _allowed_by_target,
                _effect_revision, round_id=str(_round),
                frame_mappings=_frame_mappings_guard,
                revision=_effect_revision)
        except Exception as _eff_err:
            _effect_records = []
            _mrt._logger.warn("[REPAIR] 布局效果登记失败（本轮按无效果处理）: %s" % _eff_err)
        # M01：授权集合在请求构造阶段（本函数前段）才生成，账本构造时它还不存在。
        # 在 merged 节点与第一个 writer 观测之前补上，让此后每个节点都冻结本轮
        # 授权路径的值指纹；缺了它就无法定位「首次把合并后取值改掉」的责任 writer。
        # candidate 节点早于授权生成，其基线快照由诊断阶段单独按同一集合计算。
        _effect_ledger.allowed_by_target = dict(_allowed_by_target or {})
        _effect_ledger.freeze_merged(merged, _effect_records)
        if _effect_records:
            _mrt._logger.info(
                "[REPAIR] 本轮已授权布局效果 %d 条: %s"
                % (len(_effect_records),
                   [(e["shot_id"], e["frame_locator"],
                     "删" + str([x.get("name") for x in e["removed_entries"]]),
                     "加" + str([x.get("name") for x in e["added_entries"]]))
                    for e in _effect_records[:4]]))
            # 人物绑定裁决（只读）：条目是类别别名时，它与已在场人物的身份关系
            # 决定后面的归因走哪一类。关系取**稿件**的人物表与 `chapter_outline`，
            # 不从名字或句式猜（缺关系就是缺，交回生产者）。
            for _eff in _effect_records:
                _names = [str((x or {}).get("name") or "")
                          for x in (_eff.get("removed_entries") or [])
                          if isinstance(x, dict)]
                _names += [str((x or {}).get("name") or "")
                           for x in (_eff.get("added_entries") or [])
                           if isinstance(x, dict)]
                # **逐条目**绑定（2026-10-03 复核 P1-9）：只挑一个条目判关系，会让
                # 该帧所有删除条目一起获得"不必重建"的许可。这里对每个条目各解析一次
                # （解析器自己会核该帧的在场事实），键为「目标＋帧＋条目」。
                for _name in sorted({n for n in _names if n}):
                    try:
                        _binding = resolve_frame_actor_binding(
                            shot_by_target(_round_before_script, _eff["shot_id"]),
                            _eff["frame_locator"],
                            _round_before_script.get("characters"),
                            _round_before_script.get("chapter_outline"),
                            novel_text, entry_name=_name,
                            # §14 P2 窄协议：生产者（审计）显式声明的身份关系优先于
                            # 一切推断——「小厮 ≡ 张十三」只有它给得出来。没给 ⇒ 返回
                            # ambiguous，不猜（这正是 `repair_capability_missing` 的来源）。
                            declared_relations=audit_relations or None)
                        _effect_bindings[RepairEffectView.binding_key(
                            _eff["shot_id"], _eff["frame_locator"], _name)] = _binding
                    except Exception as _bind_err:
                        _mrt._logger.warn("[REPAIR] 人物绑定解析失败（按未解析处理）: %s"
                                     % _bind_err)
            # 只读视图：供**同一事务内**的约束按帧消费（本轮已授权删除的条目不得
            # 按类别别名重建）。没有它时约束行为逐字不变。
            _effect_view = RepairEffectView(
                round_id=str(_round), source_revision=_effect_revision,
                effects=tuple(_effect_records),
                bindings={k: v.as_record() for k, v in _effect_bindings.items()})
        script = merged
        # v6.95（①，2026-09-22 实测）：**锁定字段基线必须在合并后立刻取**。
        #
        # 原实现在**尾部**（归一化与还原之后）才赋值 `_locked_after_patch`，于是它
        # 比的是「本轮前 vs 全流程之后」，把**管线自己的确定性清洗**算成 LLM 越权——
        # 实测第十次：拆分子镜台词末尾的「，」被归一化清掉 → `locked_changed` → 整轮
        # 假拒绝（`new_blocking=0`、`非目标镜头被改动`=0、`resolved=5` 全被这一个闸门否掉）。
        # 这与 v6.87 修 mask 基线是**同一原则**：闸门只管 **LLM 的**写入。
        _locked_after_patch = _mra._locked_repair_signature(script)
        # v6.143（P4）：**合并后立刻**取字段级差异，供两处消费——① 被拒时的日志
        # （原来只在 `locked_changed` 分支里现算）；② `_locate_offending_targets`
        # 的兜底定位源（锁定字段**已授权**时 `_final_field_mask_violations` 会跳过它，
        # 但轮级签名照样变了，此时只能靠这份 diff 定位到镜）。
        _locked_diff_round: list = []
        try:
            _locked_diff_round = _mra._locked_signature_diff(_locked_before, _locked_after_patch)
        except Exception:
            _locked_diff_round = []
        # 修复后需重新算时间轴/注入全局渲染参数
        # 08-15 修复：正确函数在 style_prefix（_post_process:186 同款），原 import 自
        # shared.vimax_bridge 不存在 → 修复轮合并后 ImportError 崩溃，修复结果从未落地
        merged = _mrt.style_prefix.inject_global_config_into_script(merged)
        # §14 P1 节点③之一：全局渲染参数注入**也是后处理 writer**（它改写 prompt 等
        # 字段）。只挂 `on_constraint_done` 覆盖不到它——少挂一个，抵消就会被归到
        # 后一个 writer 头上，责任 writer 指错比不指更坏。
        _effect_ledger.observe_writer(WRITER_GLOBAL_STYLE, merged)
        # v6.79（09-20）：修复轮只**序列化** timeline，不再调 `_compute_timecodes`。
        #
        # 原实现用 `_compute_timecodes(merged, allow_split=False)`，注释称它「只重建
        # timeline」——但 `allow_split=False` **只关闭了拆镜 pre-pass**，后续完整估时
        # 逻辑照跑，仍会在 `duration_sec` 上覆盖每个镜头（实测：输入 `duration_sec=4`、
        # `motion_desc="0-6秒：动作"` 的镜头被改成 6 秒）。也就是说修复轮依旧带着一个
        # 「重估时长」的副作用，而 `duration_sec` 是 Mask 字段。
        #
        # `write_timeline_from_durations()` 是纯序列化（不重估、不拆镜、不增删镜头），
        # 正是修复轮需要的唯一职责。
        # 见 docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 3。
        try:
            _mrt.write_timeline_from_durations(merged)
        except Exception as _tl_e:
            _mrt._logger.warn(f"[WARN] 修复轮 timeline 序列化失败（保留其自身时间轴）: {_tl_e}")
        _effect_ledger.observe_writer(WRITER_TIMELINE, merged)
        script = merged
        _mrt._logger.info(f"[REPAIR] 第 {_round} 轮定向修复完成，重新校验...")
    # 08-15：修复轮合并后强制再跑一遍 post_fix_shot_prompts（幂等）——merge 保留的
    # 旧镜头可能带着改版前就有的机械残留（如「迅速@lf」，parse_response 只修过
    # prompt/motion），确定性清掉；也防修复 LLM 引入新的机械噪音。下一轮
    # _collect_repair_warnings 校验的就是已 post_fix 的文本，少报一条废告警。
    # v6.84（二次审核发现 3 实施 #3）：事务内的确定性后处理**只作用于被点名镜头**。
    #
    # 全局 `post_fix_shot_prompts` / `run_hard_fix` 会改没被点名的镜头（跨镜约束尤其
    # 如此），于是「LLM 合法修复 + 后处理顺手动了别镜」被 `non_target_changed` 判为
    # 违规、整轮回滚，`resolved=0`——合法修复白做。这里先把未点名镜头逐字节快照，
    # 后处理跑完再还原：后处理只负责收拾本轮改动可能引入的问题。
        _frozen = _mtg._snapshot_untargeted(script, set(picked))
        from step01_script_gen.validation import post_fix_shot_prompts as _pfix
        try:
            script = _pfix(script)
        except Exception as _pfx:
            _mrt._logger.warn(f"[WARN] 修复后 post_fix 失败（保留原版）: {_pfx}")
        _effect_ledger.observe_writer(WRITER_POST_FIX, script)
        # 08-19：C 模式默认后，修复轮 LLM 可能删掉致命伤锚定（A→C 实测 2 个镜头"
        # 心口暗红洇痕"被删，违反规则 8 最高优先级）。修复完成后跑 hard_fix 致命伤
        # 兜底 + kf 时段对齐，把 LLM 弄丢的确定性补回来（幂等，不伤已有正确内容）。
        # v6.42：改用新 Constraint 框架；HARD_FIX_USE_OLD 回退开关已删除（2026-09-12）
        #
        # §14 P1：本调用点此前**没传** `on_constraint_done`——于是约束层对帧布局的
        # 每一次 append 都无处观测，第 21 轮 ch2 的抵消只能靠事后定位才看得出来。
        # 现在两个回调同时挂上：`on_constraint_done` 给**每个约束**一个冻结点（抵消的
        # 责任粒度止于那一个约束），`repair_effects` 把本轮的只读效果视图交给约束，
        # 让「按类别别名重建一个已被授权删掉的条目」这件事在**写入侧**就被拦下。
        from step01_script_gen.constraints import run_hard_fix as _hfix
        # 2026-11-12 复核：把**本轮被授权改写的镜头**告诉约束层。后处理不得把
        # 授权修复改回去（`framing_jump` 曾把「大特写 → 大远景」替换回原值）。
        _repair_indices = set()
        for _tid in picked:
            _bare = str(_tid)[5:] if str(_tid).startswith("shot_") else str(_tid)
            try:
                _repair_indices.add(int(_bare))
            except (TypeError, ValueError):
                continue
        try:
            script = _hfix(script, is_in_repair_loop=True, is_final_pass=False,
                           novel_text=novel_text,
                           on_constraint_done=_effect_ledger.observe_writer,
                           repair_effects=_effect_view,
                           repair_shot_indices=_repair_indices)
        except Exception as _hfx:
            # 同上：环境故障不降级（否则约束链没跑完仍继续往下走）
            from step01_script_gen.constraints.engine import ENV_FAULT_TYPES
            if isinstance(_hfx, ENV_FAULT_TYPES):
                raise
            _mrt._logger.warn(f"[WARN] 修复后 hard_fix 兜底失败（保留原版）: {_hfx}")
        _effect_ledger.observe_writer(WRITER_HARD_FIX, script)
        # 08-19：hard_fix_injury_continuity 按 location 精确匹配 + victim 提取不准，
        # 会漏判"仰卧但 location 写岸边"这类情况。此处保留**识别**，但不再补写正文
        # （2026-10 复核）。原实现在这里把「。伤处白袍暗红洇痕持续可见」直接拼进 prompt，
        # 三个问题：
        #   ① 它**造出模型没写过的状态**——"白袍"是硬编码服装，与真实人物着装无关，
        #      正是"血迹所属人物 / 伤处"混淆的污染源之一；
        #   ② 它与 `prompt_semantic.registry.INJURY_ANCHOR_TAIL`（"伤处白袍心口一片
        #      暗红血迹洇开…"）是**两个不同字符串**，两套幂等判据互相看不见，会与约束
        #      侧叠加到同一镜头；
        #   ③ 它用 `if "杨旭" not in _text` 把规则绑死在本作主角上——人名不得当通用判据。
        # 现行口径与约束侧一致：**只报告候选**，由终审判断是否真的缺伤处描述。
        _injury_lay_words = ("仰卧", "倒下", "仰倒", "躺于", "躺倒", "倒地", "瘫倒")
        # v6.50（08-29）：build 判定改用 STRONG 词表——"白袍"等服装词会让
        # 刺杀前镜头（开篇白袍吹箫）被当致命伤建立点
        from step01_script_gen.prompt_semantic.registry import STRONG_INJURY_ANCHORS as _anchor_words
        # 找 build_idx（第一个有 anchor 词的镜头 = 致命伤建立点）
        _build_idx = None
        for _i, _s in enumerate(script.get("shots", [])):
            _txt = _s.get("prompt", "") + _s.get("ff_desc", "")
            if any(w in _txt for w in _anchor_words):
                _build_idx = _i
                break
        _inj_candidates = []
        if _build_idx is not None:
            for _i, _s in enumerate(script.get("shots", [])):
                if _i <= _build_idx:
                    continue  # 致命伤建立点之前不判（夜宴仰卧喝酒不算伤）
                _text = _s.get("prompt", "") + _s.get("ff_desc", "") + _s.get("lf_desc", "")
                if not any(w in _text for w in _injury_lay_words):
                    continue  # 本镜没人卧倒/倒地
                if any(w in _text for w in _anchor_words):
                    continue  # 已有伤处锚，无需提示
                _inj_candidates.append(_s.get("index", _i))
        if _inj_candidates:
            _mrt._logger.warn(
                "[REPAIR] 致命伤后疑似缺伤处描述（不自动补写，交终审判定）: 镜 %s"
                % _inj_candidates)
        # §14 P1 节点③之三：原先此处**直写** `shot["prompt"]`，是后处理 writer。
        # 现不再写入，但仍给 ledger 留一个观测点，避免阶段屏障漏记该节点的状态快照。
        _effect_ledger.observe_writer(WRITER_INJURY_ANCHOR, script)


        # v6.84：还原未点名镜头（`_frozen` 快照）——后处理不得改动事务外的镜头。
        _restored_n = _mtg._restore_untargeted(script, _frozen)
        if _restored_n:
            _mrt._logger.info(f"[REPAIR] 事务内后处理已限定范围：还原 {_restored_n} 个未点名镜头")
        # §14 P1 节点③之四：还原未点名镜头同样是后处理 writer（它把事务外的镜头
        # 写回基线）。至此四个节点的中间三个全部落地，且**每个 writer 各自一个节点**。
        _effect_ledger.observe_writer(WRITER_RESTORE_UNTARGETED, script)
        # §14 P1 节点④：**最终重验后**——下面取的就是本轮接受判据所依据的那份稿子。
        _effect_ledger.freeze_reverify(script, _effect_revision)
        # M01 阶段 B：后处理全部结束、**事务裁决之前**的唯一权威稿。
        # 此后 `script` 可能被按目标回退或整轮回退改写，诊断不能再拿它当 B。
        _postprocess_snapshot = copy.deepcopy(script)
        # S03 补正：后处理来源核验。writer 若插入/删除/重排帧，或观测回调被
        # `apply_constraints` 吞成 warn，都不得继续消费旧定位符——生成结构化
        # 违例，与布局守卫共用同一个事务否决项（不是只打日志）。
        _frame_locator_issues: list = []
        try:
            from step01_script_gen.repair_effects import (
                frame_locator_integrity as _fli)
            _frame_locator_issues = _fli(
                _post_merge_snapshot, script, _effect_ledger.effects,
                frame_mappings=_frame_mappings_guard)
        except Exception as _fli_err:
            _frame_locator_issues = [
                "帧定位符完整性核验失败（视为失效）: %s" % _fli_err]
        _ledger_obs_errors = _effect_ledger.observation_errors()
        _ledger_struct_changes = _effect_ledger.structure_changes()
        for _obs in _ledger_obs_errors:
            _frame_locator_issues.append(
                "后处理观测回调失败（writer=%s）：%s；本轮布局效果不予采信"
                % (_obs["writer"], _obs["error"]))
        if _frame_locator_issues:
            _layout_struct_after_patch = list(_layout_struct_after_patch) \
                + _frame_locator_issues
            _mrt._logger.warn("[REPAIR] 帧定位符完整性否决 %d 条（结构变化 %d / "
                         "观测错误 %d）: %s"
                         % (len(_frame_locator_issues),
                            len(_ledger_struct_changes),
                            len(_ledger_obs_errors),
                            _frame_locator_issues[:3]))

        _round_ok = False   # v6.101：本轮事务是否被接受
        _no_gain = False    # v6.102：本轮是否「通过验收但零改善」
        # Acceptance is identity-based, not message-count based.  The same
        # structured collector runs before and after; audit-only findings are
        # verified by evidence disappearance.  Any checker failure or new blocking
        # identity rolls the round back.
        try:
            _after_state = _msc._collect_repair_warnings(script, novel_text)
            # v6.79（09-20）：Mask 字段签名在**全部后处理结束后**才取。
            #
            # 原先在 `_apply_shot_replacements` 之后立刻取值，比较的是「LLM patch 直接
            # 造成的变化」；随后跑的 timeline 序列化 / post_fix_shot_prompts / hard_fix
            # 对 `duration_sec`、`narration_segment`、`subtitle` 等 Mask 字段的副作用
            # **不进比较**，于是它们随整轮一起被接受——「duration_sec 是 Mask 字段」
            # 这条 prompt 契约在运行时并不成立（审核发现 3）。
            #
            # Mask 字段清单与修复 prompt 里给 LLM 的清单同源
            # （narration_segment / index / duration_sec / subtitle / variation_type /
            #  scene / location），见 `_locked_repair_signature`。未点名镜头的变化另由
            # 下面的逐镜 revision gate 兜住。
            # v6.95：`_locked_after_patch` 已在**合并后立刻**取（见循环内），这里不再重取
            # ——重取会把归一化/还原的写入算进来，正是上面那个假拒绝的成因。
            _locked_changed = _locked_before != _locked_after_patch
            if _locked_changed:
                # 2026-11-07（D4）：本轮显式授权了某镜的 duration 时，duration_sec 是
                # 代码同步出来的**派生值**，不算越权。只放行这一类差异；narration_segment
                # / subtitle / scene / location / variation_type 仍一票否决。
                try:
                    _dur_allowed = {str(_tid) for _tid, _fs in (_allowed_by_target or {}).items()
                                    if "duration" in {str(_x) for _x in (_fs or ())}}
                    _remain = []
                    for _line in _mra._locked_signature_diff(_locked_before, _locked_after_patch,
                                                        limit=200):
                        _sid2, _, _rest = str(_line).partition(".")
                        _fld2 = _rest.split(":", 1)[0].strip()
                        if _sid2 in _dur_allowed and _fld2 == "duration_sec":
                            continue
                        _remain.append(_line)
                    if not _remain:
                        _locked_changed = False
                except Exception:
                    pass
            # v6.77（第四阶段）：修复轮是「镜头事务」——只许动被点名的镜头。
            # 用逐镜语义 revision 快照判定：不在 picked 里的镜头若发生任何语义变化，
            # 本轮即拒绝（原判据只看聚合的 blocking 身份，看不见"顺手动别镜"）。
            from shared.quality_gate import per_shot_revisions, changed_shot_keys
            _picked_keys = {str(_p)[5:] for _p in picked if str(_p).startswith("shot_")}
            if "hook" in picked:
                _picked_keys.add("hook")
            _non_target = [
                _k for _k in changed_shot_keys(
                    per_shot_revisions(_round_before_script), per_shot_revisions(script))
                if str(_k) not in _picked_keys
            ]
            # v6.80（发现 9 #4）想抓的是「后处理对锁定字段的写入逃过比较」。
            # v6.87 修正了它的方向：后处理是**管线自己的确定性归一化**，不算越权；
            # 真正要抓的是 **LLM** 的写入，而那个基线只能在合并后立刻取
            # （`_mask_violations_after_patch`，见上）。两者分开命名，日志才指得清。
            _locked_by_llm = list(_mask_violations_after_patch or [])
            # v6.83：`allowed_targets` 此前是**未定义名**（HEAD 里传的是全部
            # `audit_issues`，工作树改成这个名字时漏了定义）——整段接受判定被外层
            # `except` 吞掉并**回退整轮修复**，于是 v6.79～v6.82 加的
            # non-target gate / 最终字段 mask 在生产里从未真正跑过。
            # 这里按名字的本义补齐：只把**本轮点名目标**的 audit issue 当作可解目标
            # ——非点名镜头的问题本轮修不了，把它们算进 `has_targets` 只会让
            # `no_target_resolved` 误判。
            _allowed_targets = [
                _ai for _ai in (audit_issues or [])
                if _mfa._target_of_record(_ai) in picked
            ]
            # v6.103（总计划阶段 3）：跨字段 issue 的判据证据不得被删掉。
            # 必须用**后处理结束后的最终 script** 来查——后处理层可能把它补回来
            # （幂等约束会重建布局条目），那就不是「删证据」而是「还没修完」。
            _evidence_removed = _mrc._cross_field_evidence_removed(
                _det_before_state, script, set(picked))
            # v6.104：结构验收在**合并后立刻**算好（见循环内 `_kf_struct_after_patch`）
            # ——基线只含 LLM 的写入，与 `_mask_violations_after_patch` 同源。
            _kf_struct = _kf_struct_after_patch
            _layout_struct = _layout_struct_after_patch
            _decision = _mrc._repair_acceptance_decision(
                _det_before_state, _after_state, _allowed_targets, script, _locked_changed,
                non_target_changed=_non_target,
                locked_by_llm_changed=_locked_by_llm,
                script_before=_round_before_script,
                evidence_removed=_evidence_removed,
                kf_structural_violations=_kf_struct,
                layout_structural_violations=_layout_struct,
                audit_attempted=audit_attempted,
                merged_script=_post_merge_snapshot,
                merged_state=_mrc._g7_merged_state(_post_merge_snapshot, novel_text))
            _mrt._logger.info(
                "[REPAIR] 接受条件: "
                f"before={_decision['before']}+audit{_decision['audit_targets']} "
                f"after={_decision['after']} resolved={_decision['resolved']} "
                f"字段触达={_decision.get('audit_field_touched', 0)} "
                f"new={_decision['new_blocking']}"
                # 09-26 P0-2：把兜底记账的**问题身份**一并打出来。只报计数时
                # 「同一身份第二轮不再给兜底分」（v6.139 修复 2）在真 run 里
                # **无法核验**——本轮 3 次修复轮都是 `字段触达=N` 而无身份，
                # 只能靠单测证明该判据存在。
                + (" 触达身份=" + str(_decision.get("audit_touched_ids"))
                   if _decision.get("audit_touched_ids") else "")
            )
            _mrt._metrics_inc("repair.decisions")
            _mrt._metrics_update("repair.last_decision", _decision)
            # v6.139：本轮兜底记账的问题身份先暂存——**轮次最终落定**（未被零改善/
            # 回退路径撤销）后才并入「已尝试」集合；被回退轮次的触达没进稿子，
            # 不消耗该身份的一次兜底记账名额。
            _touched_ids_round = list(_decision.get("audit_touched_ids") or [])
            # §14 P3：**效果被后处理抵消 ≠ 模型没修好**。
            #
            # 第 21 轮 ch2 的完整因果链：审计说「小厮 是重复实体」→ 模型删对了
            # （授权与合并都通过）→ 同轮的 `group_actor_continuity` 按「文本提到群体」
            # 加回来 → 验收看到与基线相同 ⇒ `字段触达=0` ⇒ `no_target_resolved`
            # ⇒ 整轮回退 ⇒ BLOCK。**没有一步是模型没改**，却被记成模型的问题。
            #
            # 这里只改**归因**，不改采纳判据：`_round_ok` 仍按原样为假、基线照样
            # 保留（§P0：身份关系矛盾或未解决时保留原采纳稿）。四类归因
            # （`postprocess_effect_reverted` / `actor_presence_conflict` /
            # `actor_binding_ambiguous` / `repair_capability_missing`）落点：
            #   · `_decision["reason"]`——本轮 reason 的自由串（消费者：诊断、日志、
            #     `_locate_offending_targets` 之外的所有 reason 判读）；
            #   · `_decision["effect_conflict"]`——完整事实（mechanism/writer/
            #     first_revert/bindings），随 `repair_diag` 落台账；
            #   · `_feedback_json["conflicts"]` + `_repair_turn`——下一轮**修复请求**
            #     的承载层（§P3「冲突issue须进入下一修复请求」）。
            _effect_outcome = {}
            try:
                _effect_outcome = classify_effect_outcome(
                    _effect_ledger, script,
                    bindings=dict(_effect_bindings))
            except Exception as _eff_cls_err:
                _mrt._logger.warn("[REPAIR] 布局效果归因失败（沿用既有 reason）: %s"
                             % _eff_cls_err)
            if _effect_outcome.get("reason"):
                _decision["effect_conflict"] = _effect_outcome
                if str(_decision.get("reason") or "") == "no_target_resolved":
                    # 只在**归因指向模型之外**时改写：`no_target_resolved` 的原意是
                    # 「模型没修」，而账本已经证明模型改了、是后处理抵消的。
                    _decision["reason"] = _effect_outcome["reason"]
                    _decision["origin_reason"] = "no_target_resolved"
                _mrt._logger.warn(
                    "[REPAIR] 已授权布局效果被抵消：reason=%s mechanism=%s "
                    "责任writer=%s 目标=%s 首次撤销=%s"
                    % (_effect_outcome["reason"], _effect_outcome.get("mechanism"),
                       _effect_outcome.get("writer") or "?",
                       _effect_outcome.get("targets"),
                       (_effect_outcome.get("first_revert") or {})))
            if _decision["accepted"]:
                _mrt._metrics_inc("repair.accepted")
                _round_ok = True
            else:
                _mrt._metrics_inc("repair.rejected")
            # v6.102（第十六次修复项 #1）：**通过验收但零改善**也是空转。
            #
            # 实测（第十五次）：第 2/3 轮的 `before/after/resolved` **完全相同**
            # （`resolved=0`），却仍被判「接受」——靠的是 `audit_resolved=2`，
            # 而这个数**第 2 轮起是陈旧的**：调用点把 `_audit_before_count` 写成
            # `if _round == 1 else 0`，而 audit 判据问的是「evidence 是否还在稿子里」，
            # 第 1 轮清掉后它**永远为真**。于是同一批 4 镜被反复改写两次、
            # 白烧 2 次 LLM 调用，没有任何可测量的收益。
            #
            # 判据：被接受 **且** `resolved == 0` **且** `audit_resolved == 0`
            # → 等价于空操作：回退它的写入并结束循环（下一批还是同一批目标）。
            _no_gain = (bool(_decision.get("accepted"))
                        and not int(_decision.get("resolved") or 0)
                        and not int(_decision.get("audit_field_touched") or 0))
            # v6.105（2026-09-24 第二十六次重跑实测）：**先尝试按目标回退**，再考虑
            # 整轮回退。
            #
            # 实测：一轮 `resolved=7 new=1`——7 条阻塞被真正解决，只因 shot 8 的补帧
            # 连带改动了已有帧、产生 1 条新的窗口错配，整轮（含另外 3 个目标的合法
            # 修复）被一起丢掉。这与 v6.86 之前的「整批全有或全无」是**同一个缺陷的
            # 应用侧与验收侧**：v6.86 已经把应用改成逐目标提交，验收侧必须同步——
            # 否则「逐目标提交」在验收这一关又被收回。
            #
            # 安全性不变：最终被采纳的结果**仍然满足「没有新增 blocking」**（这正是
            # 总计划 §6 的停止条件），只是不再让一个坏目标连坐其它好目标。定位不到
            # 目标（全局问题 / 新阻塞落在非目标镜头）时不做这种回退，退回整轮回退。
            # v6.119（用户决定）：**先在回退之前**捕获并记录新增阻塞的**身份**。
            #
            # 为什么必须在这里：`new_blocking_keys` 原本仅有一条日志，位置在
            # 「整轮回退」分支里；而本分支一旦成功按目标回退，`_decision` 就被
            # `_dec_try` 覆盖（其 `new_blocking_keys` 已是**回退后的空列表**）→
            # 那条日志再也不会执行。2026-09-25 离线取证时确认：`new=3` 只剩计数，
            # 「哪三条」无从回答。keys 在此捕获后被下游覆盖也不受影响。
            _nb_keys_round = [list(k) for k in (_decision.get("new_blocking_keys") or [])]
            _offend_raw = _mtg._new_blocking_targets(
                _decision.get("new_blocking_keys") or [], set(picked))
            # v6.143（P4，2026-09-27 十次连跑根因）：**其余可定位闸门也走按目标回退**。
            #
            # 此前只有 `new_blocking` 能按目标回退（v6.105）；`locked_changed` /
            # `locked_field_changed` / `kf_structure_invalid` 一律**整轮**回退——
            # 实测 10 次跑 20 个修复轮算出 `resolved=109`，其中 **60 条（55%）**被
            # 整轮丢弃（`locked_changed` 5轮丢35、`new_blocking` 2轮丢12、
            # `kf_structure_invalid` 3轮丢13），`run 03` 一次丢 **13 条**。
            #
            # 三个闸门本身都对（都在防「让告警消失而不是修好」），错的只是粒度：
            # 「shot 6 改了锁定台词」不该让同轮 shot 1/5/9 的真修复一起陪葬。
            # 定位源见 `_locate_offending_targets`（消息里的 `<目标名>` 前缀）；
            # **定位不到就保持整轮语义**（`non_target_changed` / `evidence_removed`
            # / `checker_failed` 明确不在此列——见该函数 docstring）。
            if not _offend_raw:
                _offend_raw = _mtg._locate_offending_targets(
                    _decision, set(picked), _locked_diff_round)
                if _offend_raw:
                    _mrt._logger.info(
                        "[REPAIR] 按闸门消息定位到肇事目标 %s（reason=%s）→ 只回退它们，"
                        "同轮其余目标的修复保留"
                        % (sorted(_offend_raw), _decision.get("reason")))
            _offend_round = _offend_raw
            # 原始拒绝原因（`_decision` 会被 `_dec_try` 覆盖，日志要报的是**起因**）
            _origin_reason = str(_decision.get("reason") or "")
            # v6.144 取证：按目标回退会把 `_decision` 换成回退后那版（accepted，
            # reason 恒为空），起因与被保护/被删的证据文本随之丢失——台账里只能看到
            # 一个空 reason，事后无法判断模型是**删了**证据还是**改写**了证据。
            _evidence_removed_round = list(_decision.get("evidence_removed") or [])
            _rolled_back_groups: list = []
            if not _decision["accepted"] and _offend_raw:
                if _nb_keys_round:
                    _mrt._logger.warn(
                        "[REPAIR] 新增 blocking %d 条（**按目标回退前**记录）: %s%s  "
                        "定位=%s"
                        % (len(_nb_keys_round), _nb_keys_round[:10],
                           (f"（另 {len(_nb_keys_round) - 10} 条见台账）"
                            if len(_nb_keys_round) > 10 else ""),
                           sorted(_offend_round) or "无法定位（全局或非目标镜头）")
                    )
                # v6.119：联动组**整组回退**——组内任一镜肇事即回退整组。
                # 只回退肇事那一镜等于把一次联动修复拆成半次，剩下的半次会自相矛盾
                # （shot 5 的 kf 时序倒置 + 灯盏回返正是这么留下的）。
                _offend, _rolled_back_groups = _mtg._expand_rollback_by_groups(
                    _offend_round, _active_groups)
                _offend_round = _offend
                if _offend and _offend != set(picked):
                    # v6.144 取证：`script` 下面会被换成回退后的版本，肇事目标的
                    # **修复文本**必须先抓下来，否则「删了证据还是改写了证据」
                    # 在日志里无从区分。
                    _rb_texts = {}
                    for _t_rb in _offend:
                        _s_rb = _mtg._shot_by_target_id(script, _t_rb) or {}
                        _rb_texts[_t_rb] = {
                            _f: str(_s_rb.get(_f) or "")
                            for _f in ("lf_desc", "motion_desc", "ff_desc", "prompt")}
                    try:
                        _try_script = _mtg._restore_targets(script, _round_before_script, _offend)
                        _merged_try = _mtg._restore_targets(merged, _round_before_script, _offend)
                        # S03：回退目标已恢复基线 ⇒ 恒等映射；其余目标保留各自映射。
                        # 同步撤销回退目标的映射与效果，避免「回退后仍按位移坐标比较」。
                        from step01_script_gen.repair_fields import (
                            identity_frame_mapping as _identity_frame_mapping)
                        _offend_keys: set = set()
                        for _t_key in _offend:
                            _s_key = str(_t_key)
                            _offend_keys.add(_s_key)
                            _offend_keys.add("shot_%s" % (
                                _s_key[5:] if _s_key.startswith("shot_") else _s_key))
                        _try_mappings = {
                            _k: _v for _k, _v in _frame_mappings_guard.items()
                            if _k not in _offend_keys}
                        for _t_rb in _offend:
                            _s_b = _mtg._shot_by_target_id(_round_before_script, _t_rb) or {}
                            _try_mappings[_t_rb] = _identity_frame_mapping(
                                len((_s_b.get("frame_layouts") or {}).get("kfs") or []),
                                source_revision=_effect_revision)
                        # v6.143（P4）：锁定字段判据必须**按回退后的稿子重算**。
                        # 传原来的轮级 `_locked_changed` 会让 locked 型拒绝在回退后
                        # 依然为真（它描述的是**回退前**那一版），按目标回退永远过不了
                        # 验收——这正是 P4 要修的那条链。
                        _locked_try = (_locked_before
                                       != _mra._locked_repair_signature(_merged_try))
                        _dec_try = _mrc._repair_acceptance_decision(
                            _det_before_state,
                            _msc._collect_repair_warnings(_try_script, novel_text),
                            _allowed_targets, _try_script, _locked_try,
                            non_target_changed=_non_target,
                            locked_by_llm_changed=_mra._final_field_mask_violations(
                                _round_before_script, _merged_try, set(picked),
                                allowed_by_target=_allowed_by_target),
                            script_before=_round_before_script,
                            evidence_removed=_mrc._cross_field_evidence_removed(
                                _det_before_state, _try_script, set(picked)),
                            kf_structural_violations=_mrc._kf_structural_violations(
                                _round_before_script, _merged_try,
                                _allowed_by_target, set(picked), novel_text),
                            layout_structural_violations=_mrt.frame_layout_structural_violations(
                                _round_before_script, _merged_try,
                                _allowed_by_target, set(picked),
                                frame_mappings=_try_mappings,
                                revision=_effect_revision),
                            merged_script=_merged_try,
                            merged_state=_mrc._g7_merged_state(_merged_try, novel_text))
                    except Exception as _try_err:
                        _mrt._logger.warn(f"[REPAIR] 按目标回退尝试失败（退回整轮回退）: {_try_err}")
                        _dec_try = None
                    if _dec_try and _dec_try.get("accepted") and not _dec_try.get("new_blocking"):
                        script = _try_script
                        # M01 阶段 C：按目标回退后的稿（未发生回退时保持 None，
                        # 诊断会把 C 视同 B，不虚构一个阶段）。
                        _post_rollback_snapshot = copy.deepcopy(script)
                        _decision = _dec_try
                        _round_ok = True
                        # S03 补正：台账要写**最终保留稿**的映射，而不是回退前的
                        # 装配映射——否则「回退目标已恢复恒等映射」在证据里看不见，
                        # 下游会把一份已被撤销的位移坐标当成事实继续消费。
                        _frame_mappings_final = dict(_try_mappings)
                        # S03 补正：回退目标的效果与映射同步撤销。历史节点留在
                        # 原账本里（回退前事实），下游归因只拿「最终保留稿」的视图。
                        _kept_targets = set(picked) - set(_offend)
                        _ledger_view = _effect_ledger.filtered_view(_kept_targets)
                        _dropped = len(_effect_ledger.effects) - len(_ledger_view.effects)
                        _decision["effects_rolled_back"] = _dropped
                        try:
                            _effect_outcome = classify_effect_outcome(
                                _ledger_view, script,
                                bindings=dict(_effect_bindings))
                            _decision["effect_conflict"] = _effect_outcome or {}
                        except Exception as _eff_rb:
                            _mrt._logger.warn("[REPAIR] 回退后效果归因重算失败: %s" % _eff_rb)
                        if _dropped:
                            _mrt._logger.info("[REPAIR] 按目标回退同步撤销 %d 条已登记效果"
                                         % _dropped)
                        # v6.139：决策换成了按目标回退后的版本，兜底记账身份同步
                        _touched_ids_round = list(_dec_try.get("audit_touched_ids") or [])
                        # 决策换了，no-gain 判据必须跟着重算（否则会拿被否决那轮
                        # 的 accepted=False 当作「不是空转」）
                        _no_gain = (bool(_dec_try.get("accepted"))
                                    and not int(_dec_try.get("resolved") or 0)
                                    and not int(_dec_try.get("audit_field_touched") or 0))
                        _mrt._metrics_inc("repair.accepted_after_target_rollback")
                        _mrt._metrics_update("repair.last_decision", _dec_try)
                        _mrt._logger.info(
                            "[REPAIR] 按目标回退 %s（origin_reason=%s）后通过验收："
                            "resolved=%d new=0（同轮其余目标的修复保留）"
                            % (sorted(_offend), _origin_reason,
                               _dec_try.get("resolved") or 0)
                        )
                        if _evidence_removed_round:
                            _mrt._logger.warn(
                                "[REPAIR] 回退前判定（origin,%s）%s"
                                % (_origin_reason, str(_evidence_removed_round[:2])[:300]))
                        for _t_rb in sorted(_rb_texts):
                            _s_after = _mtg._shot_by_target_id(script, _t_rb) or {}
                            for _f, _bv in _rb_texts[_t_rb].items():
                                _av = str(_s_after.get(_f) or "")
                                if _bv != _av:
                                    _mrt._logger.warn(
                                        "[REPAIR] 回退目标 %s.%s 修复稿⇒回退后：%s ⇒ %s"
                                        % (_t_rb, _f, _bv[:110], _av[:110]))
                    elif _dec_try:
                        # v6.137：回退重试失败必须可见（本 run 的盲区——重试为何没过无从归因）
                        _mrt._logger.warn(
                            "[REPAIR] 按目标回退 %s 后仍未通过验收"
                            "（reason=%s resolved=%s 字段触达=%s new=%s）→ 整轮回退"
                            % (sorted(_offend), _dec_try.get("reason"),
                               _dec_try.get("resolved") or 0,
                               _dec_try.get("audit_field_touched") or 0,
                               _dec_try.get("new_blocking") or 0))
                        # v6.146 取证：**回退失败**这条分支此前不打修复稿文本，
                        # 于是「模型把哪一段改成了什么」在事后无从判断——而它决定
                        # 「合法改写被误杀」还是「动作被改没了」这两种相反结论。
                        for _t_rb in sorted(_rb_texts):
                            _rb_base = _mtg._shot_by_target_id(_round_before_script, _t_rb) or {}
                            for _f, _rep in _rb_texts[_t_rb].items():
                                _bs = str(_rb_base.get(_f) or "")
                                if _bs != _rep:
                                    _mrt._logger.warn(
                                        "[REPAIR] 回退未过 目标 %s.%s 基线⇒修复稿：%s ⇒ %s"
                                        % (_t_rb, _f, _bs[:110], _rep[:110]))
            if not _decision["accepted"] or not _round_ok:
                # v6.93 修（自查发现的 bug）：诊断必须在**回退之前**抓快照。
                #
                # 原实现把 `_shot_field_diff(_round_before_script, script)` 放在
                # `script = _round_before_script` **之后**——那时 `script` 已经等于基线，
                # 于是永远得到空 diff（2026-09-22 21:31 实测：`非目标镜头被改动 9 处`
                # 后面没有 `变化字段:` 行，就是这么来的）。诊断自己失效 = 白加。
                _script_at_reject = script
                script = _round_before_script
                # v6.87：**必须打出到底什么变了**——`non_target_changed` 连续三次成为
                # 回退原因，而日志从不说明是哪个镜头/哪个字段，只能靠事后复现才能定位。
                if _decision.get("non_target_changed"):
                    _mrt._logger.warn(
                        f"[REPAIR] 非目标镜头被改动 {len(_decision['non_target_changed'])} 处: "
                        f"{_decision['non_target_changed'][:5]}"
                    )
                    # 字段级定位（v6.92）：否则只能靠重新复现一次运行来猜是哪个字段
                    try:
                        _fld = _mtg._shot_field_diff(_round_before_script, _script_at_reject)
                    except Exception:
                        _fld = []
                    if _fld:
                        _mrt._logger.warn(f"[REPAIR] 变化字段: {_fld}")
                    else:
                        # 自相矛盾要**说出来**，不能静默：`changed_shot_keys` 说这些镜头变了，
                        # 而语义字段逐个比对却无差异 → 说明两者口径不一致（或诊断调用点写错）。
                        # 这是把「诊断失效」本身变成可见信号——v6.93 就吃过一次静默失效。
                        _mrt._logger.warn(
                            "[REPAIR] ⚠️ 非目标改动不在语义字段内（诊断与逐镜 revision 口径不一致，"
                            "请查 `_shot_field_diff` 的调用点是否在回退之后）"
                        )
                if _decision.get("locked_changed"):
                    # v6.94：字段级定位——`locked_changed` 只有布尔，从不说变了什么
                    try:
                        _lsd = _mra._locked_signature_diff(_locked_before, _locked_after_patch)
                    except Exception:
                        _lsd = []
                    if _lsd:
                        _mrt._logger.warn("[REPAIR] 锁定字段变化: %s" % _lsd)
                if _decision.get("locked_by_llm_changed"):
                    _mrt._logger.warn(
                        f"[REPAIR] LLM 改动了锁定字段 "
                        f"{len(_decision['locked_by_llm_changed'])} 处: "
                        f"{_decision['locked_by_llm_changed'][:5]}"
                    )
                if _decision.get("evidence_removed"):
                    _mrt._logger.warn(
                        "[REPAIR] 跨字段判据证据被删除 "
                        f"{len(_decision['evidence_removed'])} 处（删证据能让检测器闭嘴，"
                        "但那不是修复）："
                        f"{_decision['evidence_removed'][:3]}"
                    )
                if _decision.get("postprocess_target_lost"):
                    _mrt._logger.warn(
                        "[REPAIR] 后处理丢失已达成的目标（target-loss）"
                        f"{len(_decision['postprocess_target_lost'])} 条，拒绝本轮: "
                        f"{_decision['postprocess_target_lost'][:3]}")
                if _decision.get("new_blocking_keys"):
                    _nb_all = list(_decision["new_blocking_keys"])
                    _mrt._logger.warn(
                        "[REPAIR] 本轮新增 blocking "
                        f"{_decision['new_blocking']} 条: {_nb_all[:10]}"
                        + (f"（另 {len(_nb_all) - 10} 条见 step01_repair_rounds.json）"
                           if len(_nb_all) > 10 else "")
                        + (f"（其中 KF 帧数变动的镜头 {_decision['reindexed_kf_shots']}"
                           " 已按「该镜的该类问题」比较，位置序号不计）"
                           if _decision.get("reindexed_kf_shots") else "")
                    )
                if _decision.get("kf_structural_violations"):
                    _mrt._logger.warn(
                        "[REPAIR] KF 结构验收未过 "
                        f"{len(_decision['kf_structural_violations'])} 处（授权增删 KF 的"
                        "配套闸门，**只计本轮新引入/恶化的**：KF 须落在镜内且按时段单调，"
                        "motion 原事件文本须仍在、顺序不变；稿内既有越窗帧由最终质量门"
                        "绝对判定）："
                        f"{_decision['kf_structural_violations'][:3]}"
                    )
                _mrt._logger.warn(
                    "[REPAIR] 修复未满足接受条件"
                    f"（reason={_decision['reason']}, resolved={_decision['resolved']}, "
                    f"字段触达={_decision.get('audit_field_touched', 0)}, "
                    f"new={_decision['new_blocking']}, "
                    f"checker_failed={_decision['checker_failed']}, "
                    f"locked_changed={_decision['locked_changed']}），回退本轮修复"
                )
        except Exception as _acc_err:
            script = _round_before_script
            _mrt._metrics_inc("repair.check_failed")
            _mrt._metrics_set("repair.last_error", str(_acc_err))
            _mrt._logger.warn(f"[REPAIR] 接受条件检查失败，回退本轮修复: {_acc_err}")
        # ---- v6.119：本轮修复事务的诊断记录（含**回退前**捕获的 new_blocking_keys）----
        # 记录点在「决策已落地」之后、早退之前——下面两个 `return` 不会把诊断带走。
        # 台账消费方据此回答：这一轮引入了哪几条新阻塞、落在哪些镜头、回退了哪些
        # 目标/联动组、修前修后的 revision 各是什么。
        if repair_diag is not None:
            try:
                from shared.quality_gate import (
                    compute_script_revision as _csr_diag)
                # ---- M01：四阶段归因（只读事实，不改变任何接受判据）----
                # A=合并后 / B=后处理结束、裁决前 / C=按目标回退后 / D=最终稿。
                # 逐 writer 的授权路径快照由账本节点提供，用来定位首次改动的 writer。
                _attr = {"fields": {}, "summary": {}}
                try:
                    from step01_script_gen.repair_attribution import (
                        authorized_path_snapshot as _aps,
                        attribute_field_loss as _afl)
                    _b_snap = _postprocess_snapshot or script
                    _c_snap = _post_rollback_snapshot or _b_snap
                    _attr = _afl(snapshots={
                        "baseline": _aps(_round_before_script, _allowed_by_target),
                        "merged": _aps(_post_merge_snapshot, _allowed_by_target),
                        "postprocess": _aps(_b_snap, _allowed_by_target),
                        "post_rollback": _aps(_c_snap, _allowed_by_target),
                        "final": _aps(script, _allowed_by_target),
                    }, writer_nodes=_effect_ledger.nodes,
                        preview_scripts={
                            "baseline": _round_before_script,
                            "merged": _post_merge_snapshot,
                            "postprocess": _b_snap,
                            "post_rollback": _c_snap,
                            "final": script,
                        })
                except Exception as _attr_err:                 # noqa: BLE001
                    _attr = {"fields": {}, "summary": {},
                             "error": "%s: %s" % (type(_attr_err).__name__,
                                                  _attr_err)}
                repair_diag.append({
                    "repair_round": _round,
                    "targets": sorted(picked),
                    "groups": [sorted(g) for g in _active_groups],
                    "new_blocking_keys": _nb_keys_round,
                    "new_blocking_targets": sorted(_offend_round),
                    "rolled_back_groups": _rolled_back_groups,
                    "targets_rolled_back": sorted(_offend_round) if _rolled_back_groups else [],
                    "accepted": bool(_decision.get("accepted")),
                    "round_ok": bool(_round_ok),
                    "resolved": int(_decision.get("resolved") or 0),
                    "reason": str(_decision.get("reason") or ""),
                    # v6.144 取证：按目标回退后 `reason` 恒为空，起因（origin_reason）
                    # 与证据明细只能从这里读。
                    "origin_reason": _origin_reason,
                    "evidence_removed": _evidence_removed_round,
                    "audit_field_touched": int(
                        _decision.get("audit_field_touched") or 0),
                    "audit_touched_ids": [list(t) for t in
                                          (_decision.get("audit_touched_ids") or [])],
                    "revision_before": _csr_diag(_round_before_script),
                    "revision_after": _csr_diag(script),
                    # §14 P3：四类归因的**台账承载层**——reason 自由串只够人读，
                    # 这里落完整事实（mechanism/writer/first_revert/bindings）。
                    # 消费者：`step01_repair_rounds.json` 的读者与离线回放。
                    "effect_conflict": (_decision.get("effect_conflict") or None),
                    # S04（2026-11-11）：四栏台账——模型请求 / 实际应用 / 越权回填 /
                    # 投影后残余。消费方据此看「半条修复」，不必自己推。
                    "related_readback": _mtg._round_related_readback(
                        _round_before_script, script, _allowed_by_target,
                        model_shots=_model_shots_round, novel_text=novel_text),
                    # S04：**合并前**被拒的条目（越权字段补丁等）进台账。
                    # 这类拒绝不经过 `merge_authorized`，因此不会出现在回读的
                    # `reverted` 里；不单独记，台账就会漏掉「模型想改但被挡在门外」
                    # 这一半事实（2026-11-12 实跑：shot7/shot11 因 keyframes 被拒）。
                    "rejected": list(_rejected or [])[:6],
                    # M02（2026-11-12）：本轮**请求允许范围**与「声明之外扩出来的字段」。
                    # 三栏台账（allowed / declared / beyond_declared）回答两个问题：
                    #   ① 这一轮到底允许模型改哪些字段；
                    #   ② 其中哪些不是问题自己声明的，而是关联推导/同帧实体互授得来的。
                    # 没有这一栏时，「授权面是否足够」只能从提示词正文反推（S06 即是）。
                    "authorization": _auth_ledger,
                    # M02：**已授权的改动有没有落到稿子上**。只记账、不改判据；
                    # 空字典 = 本轮授权路径上的值没有被后处理改动。
                    # M01：旧实现比较「合并后 → 已整轮回退的最终稿」，把模型写入、
                    # writer 改写与事务回滚混成一项（run j 的 36 字段）。现固定为
                    # A→B（后处理抵消），事务回滚 B→D 另记，逐字段见 field_attribution。
                    "postprocess_reverted": _mrc._authorized_changes_lost(
                        _post_merge_snapshot,
                        _postprocess_snapshot or script, _allowed_by_target),
                    "transaction_rollback": _mrc._authorized_changes_lost(
                        _postprocess_snapshot or _post_merge_snapshot,
                        script, _allowed_by_target),
                    "field_attribution": _attr.get("fields") or {},
                    "field_attribution_summary": _attr.get("summary") or {},
                    "field_attribution_error": _attr.get("error") or "",
                    "attribution_stages": {
                        "baseline": _csr_diag(_round_before_script),
                        "merged": _csr_diag(_post_merge_snapshot)
                                  if isinstance(_post_merge_snapshot, dict) else "",
                        "postprocess": _csr_diag(_postprocess_snapshot)
                                       if isinstance(_postprocess_snapshot, dict) else "",
                        "final": _csr_diag(script),
                        "postprocess_captured": _postprocess_snapshot is not None,
                        "post_rollback_captured": _post_rollback_snapshot is not None,
                    },
                    # S03 补正：帧来源核验的可评阅证据（映射来源 / 定位符完整性 /
                    # writer 结构变化 / 观测回调错误 / 回退撤销的效果数）。
                    "frame_mappings": {
                        str(k): {kk: vv for kk, vv in v.items()
                                 if kk in ("source", "source_revision",
                                           "before_count", "after_count",
                                           "old_to_new", "added_indices")}
                        for k, v in _frame_mappings_final.items()
                        if isinstance(v, dict)},
                    # S03 补正：合并后布局守卫的**原始违例**（含按目标回退前的
                    # 起因），与 origin_reason 互为佐证；不随按目标回退被覆盖。
                    "layout_structural_violations": list(
                        _layout_struct_after_patch or []),
                    "frame_locator_integrity": list(_frame_locator_issues),
                    "writer_structure_changes": _ledger_struct_changes,
                    "writer_observation_errors": _ledger_obs_errors,
                    # 四节点（① 原候选 / ② 授权合并 / ③ 逐 writer / ④ 最终重验）
                    # 的精简证据：节点类型、责任 writer、撤销记录、帧结构状态。
                    "effect_ledger_nodes": [
                        {"node": n.get("node"), "writer": n.get("writer"),
                         "reverted": n.get("reverted") or [],
                         "frame_structure": n.get("frame_structure") or {},
                         # M01：只记观测条数，不把整份授权路径快照写进台账。
                         "authorized_paths_observed": len(
                             n.get("authorized_paths") or {}),
                         "error": n.get("error") or ""}
                        for n in _effect_ledger.as_records()],
                    "effects_rolled_back": int(_decision.get("effects_rolled_back") or 0),
                })
            except Exception as _diag_err:
                _mrt._logger.warn(f"[REPAIR] 修复轮诊断记录失败（不影响结论）: {_diag_err}")
        # §14 P3：**冲突进下一请求**，以及**相同冲突 + 请求信息无变化 ⇒ 停止盲修**.
        #
        # 判据只看「会改变下一轮请求内容」的那几项（原因/责任 writer/目标/被抵消的
        # 条目/镜头/帧），不含摘要与时间戳——否则每轮都不同，「无变化」永远判不出来。
        # 相同 ⇒ 再发一次是同一份请求，必然得到同一个结果：停止，并把残余封存成
        # 显式阻塞理由（不清 BLOCK、不写虚假 PASS、不改重试上限与截止）。
        if _effect_outcome.get("reason"):
            _eff_sig = conflict_signature(_effect_outcome)
            _eff_prev_sig = str(_effect_conflict_prev.get("sig") or "")
            if _eff_prev_sig and _eff_sig and _eff_sig == _eff_prev_sig:
                _seal = (
                    "布局修复效果被后处理抵消且**冲突与请求信息均无变化**"
                    "（reason=%s，责任 writer=%s，目标=%s，被抵消条目=%s）——"
                    "上一轮已把同一条冲突发出去、又原样收到同一个回答，"
                    "**不再发第三次**（重试上限与截止未改）；"
                    "该残余按原样交给提交点判 BLOCK"
                    % (_effect_outcome["reason"],
                       _effect_outcome.get("writer") or "?",
                       _effect_outcome.get("targets"),
                       (_effect_outcome.get("first_revert") or {}).get("restored_names"))
                )
                _mrt._logger.error("[REPAIR] %s" % _seal)
                if block_reasons is not None:
                    block_reasons.append(_seal)
                if repair_diag is not None:
                    repair_diag.append({
                        "repair_round": _round,
                        "kind": "effect_conflict_sealed",
                        "reason": _effect_outcome["reason"],
                        "mechanism": _effect_outcome.get("mechanism", ""),
                        "writer": _effect_outcome.get("writer", ""),
                        "targets": _effect_outcome.get("targets") or [],
                        "first_revert": _effect_outcome.get("first_revert"),
                        "signature": _eff_sig,
                        "source_revision": _effect_revision,
                        "sealed_before_send": False,
                    })
                if retry_state is not None:
                    # **留档而不清空**：下一次调用要在**发请求之前**就停下
                    # （见循环开头的封存判据）。清空等于把封存忘掉，第三次照发。
                    retry_state["layout_conflict"] = {
                        "sig": _eff_sig,
                        "reason": _effect_outcome["reason"],
                        "writer": _effect_outcome.get("writer", ""),
                        "targets": _effect_outcome.get("targets") or [],
                        "source_revision": _effect_revision,
                        "text": _effect_conflict_prev.get("text") or "",
                        "sealed": True,
                    }
                return script
            if retry_state is not None:
                retry_state["layout_conflict"] = {
                    "sig": _eff_sig,
                    "text": render_conflict_feedback(_effect_outcome),
                    "reason": _effect_outcome["reason"],
                    "writer": _effect_outcome.get("writer", ""),
                    "targets": _effect_outcome.get("targets") or [],
                    "source_revision": _effect_revision,
                }
        if not _round_ok:
            # v6.137：new_blocking 型拒绝 → **带反馈重试一次**（剔除肇事目标）。
            # 内层还有轮次就内层 continue（GAP 路径，max_rounds>=2）；没有（提交
            # 路径 max_rounds=1）就把状态导出 retry_state，由调用方用下一次
            # attempt 续跑（预算仍由外层 §1.1 上限管住，审计经 _reusable 复用）。
            # 其余拒绝原因（locked_changed / non_target_changed / evidence_removed
            # 等系统性越权）保持原停止语义——那些不是"换个目标重发"能解决的。
            #
            # v6.138（九审 P1-2）：排除集只用**真实肇事目标**（`_offend_raw`），不用
            # 组扩展后的回退集——整组回退是**回滚原子性**（v6.119），不是该组成员
            # 都引入了新 blocking；把非肇事成员也排除，会让"整组回退"退化为
            # "整组放弃"（重试轮名额被白吃，剩余目标无人修）。
            _remaining = [p for p in picked if p not in _offend_raw]
            # M03：事务拒收的反馈由**裁决生产者**分类。new_blocking 只剔除真实
            # 肇事目标（`_offend_raw`），其余目标各带一条可纠正记录；其余 reason
            # 按策略表映射，工程故障（checker / writer 观测 / 定位符失效）优先终局。
            _tx_recs: list = []
            if (_decision.get("reason") == "new_blocking"
                    and _offend_raw and _remaining):
                for _t in _remaining:
                    _tx_recs.append(_fb_record(
                        code="new_blocking", target=str(_t),
                        phase="transaction",
                        field_path="",
                        allowed_paths=sorted(str(a) for a in
                                             (_allowed_by_target.get(_t) or ())),
                        details={"offending_targets": sorted(_offend_raw),
                                 "new_blocking_keys": _nb_keys_round[:2]},
                        text="目标 %s 的补丁引入新 blocking，已剔除；其余目标"
                             "上一轮补丁未通过验收" % sorted(_offend_raw)))
            else:
                _tx_recs = _tx_records(
                    reason=str(_origin_reason or _decision.get("reason") or ""),
                    targets=sorted(picked), offending=sorted(_offend_raw),
                    allowed_by_target=_allowed_by_target,
                    frame_locator_issues=list(_frame_locator_issues or []),
                    observation_errors=list(_ledger_obs_errors or []),
                    details={"resolved": int(_decision.get("resolved") or 0),
                             "new_blocking": int(_decision.get("new_blocking") or 0),
                             "checker_failed": bool(_decision.get("checker_failed")),
                             "accepted": False,
                             "source_revision": _effect_revision})
            # 诊断写回**同一条事务记录**：台账消费者按 `rounds[-1]` 取事务
            # 记录，追加一条带 `repair_round` 的独立记录会把它顶掉（既有回放
            # 测试实锤）。结构化反馈是这一轮事务的一部分，不是新的一轮。
            if repair_diag is not None:
                for _d in reversed(repair_diag):
                    if "groups" in _d and _d.get("repair_round") == _round:
                        _d["feedback_records"] = _tx_recs
                        _d["feedback_phase"] = "transaction"
                        _d["feedback_source_revision"] = _effect_revision
                        break
            # 状态裁决与 `retry_state` 是否传入无关：GAP 路径（max_rounds>=2、
            # 不传 retry_state）原本就靠内层 `continue` 重试一次，不能被 M03
            # 顺手掐掉。只有**写入**跨 attempt 容器才需要 retry_state。
            _tx_plan = _plan_fb(
                records=_tx_recs,
                prior_state=_retry_state_snapshot,
                frozen_targets=sorted(picked),
                source_revision=_effect_revision,
                answer_sig=_answer_sig(_repls),
                excluded_override=(sorted(_offend_raw)
                                   if (_decision.get("reason") == "new_blocking"
                                       and _offend_raw) else None))
            if retry_state is not None:
                # §14 P3 的 `layout_conflict`（含 sealed）在这一轮可能刚写入；
                # 结构化反馈不得把它 clear 掉，否则封存会在下一次调用失效。
                _conflict_carry = dict(retry_state.get("layout_conflict") or {})
                retry_state.clear()
                retry_state.update(_tx_plan)
                if _conflict_carry:
                    retry_state["layout_conflict"] = _conflict_carry
            _mrt._logger.warn(
                "[REPAIR] 事务拒收反馈：reason=%s codes=%s correction=%s terminal=%s"
                % (_origin_reason,
                   [r.get("code") for r in _tx_recs],
                   _tx_plan.get("correction_granted"),
                   _tx_plan.get("terminal")))
            if (not _retry_used and _tx_plan.get("correction_granted")
                    and _round < _rounds):
                _retry_used = True
                _rejected_targets |= set(_offend_raw)
                _retry_feedback = _tx_plan.get("feedback") or ""
                _mrt._logger.warn(
                    "[REPAIR] 带反馈重试（结构化）：剔除 %s，其余 %s 重新修复（仅一次）"
                    % (sorted(_offend_round), sorted(_remaining)))
                continue
            # 本轮已被回退 → 停止（其余原因：再发下一批没有意义）
            return script
        if _no_gain:
            # v6.102：通过验收但零改善 → 回退本轮（不留无收益的改写）并停止后续轮次
            script = _round_before_script
            _mrt._metrics_inc("repair.no_gain_rolled_back")
            _mrt._logger.warn(
                "[REPAIR] 本轮无任何可测量改善（resolved=0 / 字段触达=0）→ "
                "回退本轮并停止后续轮次（继续发只会重复同一批目标）"
            )
            # M03：「合法应用但目标未解决」= 真实残余，不是工程故障，也不发
            # 新的答卷纠正。把残余结构化交给提交层与外部证据，不写虚假 PASS。
            _residual = _tx_records(
                reason="no_target_resolved", targets=sorted(picked),
                allowed_by_target=_allowed_by_target,
                details={"resolved": int(_decision.get("resolved") or 0),
                         "audit_field_touched": int(
                             _decision.get("audit_field_touched") or 0),
                         "before": int(_decision.get("before") or 0),
                         "after": int(_decision.get("after") or 0),
                         "source_revision": _effect_revision})
            if repair_diag is not None:
                for _d in reversed(repair_diag):
                    if "groups" in _d and _d.get("repair_round") == _round:
                        _d["feedback_records"] = _residual
                        _d["feedback_phase"] = "transaction_residual"
                        _d["feedback_source_revision"] = _effect_revision
                        break
            _res_plan = _plan_fb(
                records=_residual, prior_state=_retry_state_snapshot,
                frozen_targets=sorted(picked),
                source_revision=_effect_revision,
                answer_sig=_answer_sig(_repls))
            if retry_state is not None:
                _conflict_carry = dict(retry_state.get("layout_conflict") or {})
                retry_state.clear()
                retry_state.update(_res_plan)
                if _conflict_carry:
                    retry_state["layout_conflict"] = _conflict_carry
            return script
        _mrt._metrics_inc("repair.rounds_accepted")
        # §14 P3：**通过验收的轮次**才把效果交出去（`effect_state`）。
        #
        # 为什么必须交出去：提交路径在 `_repair_shot_warnings` 之后还会跑一次
        # `finalize_step01_script`，那里 `run_hard_fix(is_final_pass=True)`
        # **再跑一遍** `group_actor_continuity`（该约束全文没有 is_final_pass /
        # is_in_repair_loop 守卫）。只保证修复轮内的规则成立，合法删除会在 finalize
        # 被补回 → 新审计重报 blocking → 又判零改善。所以规则要在**两个 pass** 上成立。
        #
        # 只交**最终仍然成立**的效果：被抵消的那条已经在稿子上不成立了，把它的
        # 「不得重建」约束带过去反而会阻止后处理做正确的事。
        if effect_state is not None and _effect_records:
            try:
                _still = _effect_ledger.surviving_keys(script)
            except Exception:
                _still = set()
            _keep = [e for e in _effect_records
                     if "%s|%s" % (e["shot_id"], e["frame_locator"]) in _still]
            if _keep:
                effect_state.setdefault("effects", []).extend(_keep)
                for _e in _keep:
                    # 逐条目绑定 ⇒ 按前缀带过去（只带帧级键会丢掉所有条目绑定）
                    _pfx = "%s|%s|" % (_e["shot_id"], _e["frame_locator"])
                    for _bk, _bd in _effect_bindings.items():
                        if _bk.startswith(_pfx):
                            effect_state.setdefault("bindings", {})[_bk] = _bd.as_record()
                effect_state["revision"] = _effect_revision
        # v6.139：轮次落定 → 本轮兜底触达正式记入「已尝试」集合（下一轮起同一
        # 问题身份不再给兜底分，见 `_repair_acceptance_decision`）。
        if audit_attempted is not None and _touched_ids_round:
            audit_attempted.update(_touched_ids_round)
        # 本轮通过验收 → 继续下一批（v6.101：每轮一个独立事务）

    return script
