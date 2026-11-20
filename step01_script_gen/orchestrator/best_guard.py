# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.best_guard —— 跨运行 BEST-GUARD 选稿.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import json

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
from step01_script_gen.orchestrator import blocked_ledger as _mbl
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _cross_run_best_guard(script: dict, novel_text: str, *,
                          plan: "dict | None" = None) -> tuple:
    """跨 run 保优护栏（P4.6，2026-09-11）：防止更好的历史版本被差 run 覆盖.

    v6.103（2026-09-23 总计划阶段 4.1）：**它只选择候选，不再落盘**。

    旧实现在「新终稿已落盘」之后还可能把历史快照写回 `script_result.json`，
    并在 finalize **之后**重跑钩子质量门（可能重生 hook）——于是最终落盘的 hook
    从未被 final semantic audit 覆盖过；同时正式路径上有**两个**写入者，提交时序
    无法收敛成一个点。

    现在它返回 `(被选中的脚本 | None, 元数据)`：调用方拿到候选后仍走
    `_commit_step01_outputs()` 这一条公共管线（hook QC → finalize → revision →
    quality → final audit → 提交）。历史快照必须通过**当前版本**的质量初筛才有
    资格成为候选，但「能不能落盘」由公共管线的最终审计决定。

    Returns:
        `(selected_script, meta)`；没有更优快照时 `(None, meta)`。
    """
    meta: dict = {"selected": False, "reason": "", "new_score": None,
                  "best_score": None, "best_path": None}
    try:
        import os as _os
        from step01_script_gen.plan_feasibility import check_script_dialogue_integrity
        from shared.quality_gate import SCRIPT_REVISION_VERSION

        snap_dir = _mrt.OUTPUT_FILE.parent / "snapshots"
        if not snap_dir.is_dir():
            return None, meta
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        if not snaps:
            return None, meta

        scan_n = max(1, int(_os.getenv("BEST_GUARD_SCAN_N", "8")))
        margin = float(_os.getenv("BEST_GUARD_MARGIN", "3"))
        keep_best = _os.getenv("KEEP_BEST_SNAPSHOT", "1") == "1"
        # v6.123（用户口径：候选留痕）：一旦选中历史稿，本次新生成的稿件在内存里被
        # 丢弃（`script = _bg_selected`），而**失败路径的快照保存的是被选稿**——于是
        # 「本次新稿质量如何」事后无从评价。旧文案还宣称「本次 N 分版本保留在
        # snapshots/」，实际上这一步什么都没写（日志与事实不符）。
        # 在**选择之前**真写一份独立诊断快照，并把真实路径带进 meta 供日志引用。
        # 文件名**不用** `script_result_` 前缀：那是候选扫描的口径，而这份是
        # **未定稿**（没有 chapter_outline、没过 hook QC），不得进入候选池。
        if keep_best:
            meta["new_candidate_snapshot"] = _mbl._snapshot_new_candidate(script)

        new_score = _msc._score_script(script, novel_text)["total"]
        new_quality = _msc._candidate_quality_summary(script, novel_text)
        new_rank = _msc._quality_rank(new_score, new_quality)
        meta.update({"new_score": new_score,
                     "new_risk": new_quality.get("blocking_risk_count")})

        # 本次稿件的语义 revision——用它排除「就是当前这一版」的快照。
        # 旧实现靠「snaps[:-1] 排除最新一份」来排除本次快照；那隐含假设「最新
        # 快照刚由本次写入」。选择点提前到落盘**之前**后该假设不再成立（最新快照
        # 是上一次 run 的，是合法候选），所以改成按 revision 排除。
        # 2026-09-30 复核第 4/5 轮：「是不是同一份稿」只比**现行版本**的完整身份；
        # 「是不是已知欠账」按**登记版本**逐版比对（旧版本身份只匹配明确属于那个
        # 版本的旧记录）。两处都不能把各版本混成一个等价关系——「同一段文字、契约
        # 不同」的两份稿 v2 不同而 v1 相同，混用会让好稿被旧欠账连坐。
        _cur_aliases = _mbl._revision_aliases_or_empty(script)
        if not _cur_aliases:
            # 「算不出身份」既不能证明快照不是当前这一版，也不能证明它不欠账——
            # 显式不选稿（准则 7），不把无法核验的东西放进候选池。
            meta["reason"] = "revision_uncomputable"
            _mrt._logger.warn("[BEST-GUARD] 本次终稿 revision 别名无法计算 → 本轮不做跨 run 选稿")
            return None, meta
        _blocked_by_ver = _mbl._load_blocked_revisions()
        _blocked_names: list = []

        # v6.124.2：非分镜稿**不进候选池**（见 `_non_script_snapshot_reason`）
        _invalid: list = []
        # v6.135（审核约束①）：历史快照也按**本轮计划**执行台词完整性门——快照生成时
        # 的计划与本轮不同，它没保住本轮选材 = 进提交门后必然死锁（09-26 实损链）。
        # 门**不参与**下方"全欠账放宽"（那是欠账台账的例外，不是完整性门的）。
        _dlg_broken_snaps: list = []
        _stale_plan_snaps: list = []

        def _plan_snapshot_reason(old: dict) -> str:
            """快照的 `plan_refs` 是不是**对本轮计划**作的声明（2026-09-27 复核 P0-2）.

            带身份且与本轮计划不符 → 排除（它的编号指的不是同一条节拍；只查编号范围
            会静默放行——`insert_after`/`split` 会重排 B 编号）。**无身份**的老快照按
            既定兼容政策继续参与排名，但**不算**「通过了本轮事件契约」（提交门的语义
            审计仍是它的兜底）。
            """
            if plan is None:
                return ""
            # 就地 import：本闭包所在的 BEST-GUARD 函数与候选门的 import 块**不在同一
            # 作用域**（实跑被 `test_best_guard_excludes_broken_snapshot...` 抓到：
            # NameError 被外围的「护栏失败不影响主流程」吞掉，身份门**静默失效**）。
            from step01_script_gen.plan_feasibility import plan_identity_mismatch
            return plan_identity_mismatch(old, plan)

        def _dlg_snapshot_reason(old: dict) -> str:
            if plan is None:
                return ""
            try:
                _ok, _broken = check_script_dialogue_integrity(old, plan)
            except Exception as _e:
                # v6.135 四审 P1：门自身异常 → 该快照**无法证明台词完整** → 排除出
                # 候选池（fail-closed：宁可不选，不可选中无法验证的快照），原因留痕
                return "gate_error: %s" % _e
            if _ok:
                return ""
            return "%d 句破损，例：%s（%s）" % (
                len(_broken), str((_broken or [{}])[0].get("line") or "")[:30],
                (_broken or [{}])[0].get("reason"))

        candidates = []
        for sp in snaps:
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    old = json.load(f)
            except Exception as _e:
                _invalid.append(f"{sp.name}（JSON 解析失败：{_e}）")
                continue
            _why = _mbl._non_script_snapshot_reason(old)
            if _why:
                _invalid.append(f"{sp.name}（{_why}）")
                continue
            if not _mrt.HOOK_ENABLED:
                old.pop("hook", None)
            _old_aliases = _mbl._revision_aliases_or_empty(old)
            if not _old_aliases:
                # 同上：快照身份不可核 → 本次不具候选资格，原因留痕（而不是当作
                # 「不是当前这一版、也不欠账」默默放行）。
                _invalid.append(f"{sp.name}（revision 别名无法计算：身份不可核）")
                continue
            # 就是当前这一版：只比**现行版本**的完整身份（复核第 5 轮 P1-2）。
            if _old_aliases.get(SCRIPT_REVISION_VERSION) == _cur_aliases.get(
                    SCRIPT_REVISION_VERSION):
                continue
            # v6.115（审核）：跳过**已知语义欠账**的旧稿——它们确定性分数可能很高
            # （所以此前总被选中），却带着审计反复报的旧问题。
            # 2026-09-30 第 5 轮：**逐版本**比对——台账条目只按其登记版本的身份命中
            # （v1 记录只认 v1 身份，v2 记录只认 v2 身份），跨版本不算等价。
            if any(_old_aliases.get(_v) in (_blocked_by_ver.get(_v) or ())
                   for _v in _old_aliases):
                _blocked_names.append(sp.name)
                continue
            _dlg_why = _dlg_snapshot_reason(old)
            if _dlg_why:
                _dlg_broken_snaps.append(f"{sp.name}（{_dlg_why}）")
                continue
            _plan_why = _plan_snapshot_reason(old)
            if _plan_why:
                _stale_plan_snaps.append(f"{sp.name}（{_plan_why}）")
                continue
            candidates.append((sp, old))
        # 诊断清单去重（`invalid` / 台词破损 / 计划不符各自只扫一遍，但同一份快照
        # 可能同时命中多条）——准则 7 要的是**准确**的可见性，不是更大的数字。
        _dlg_broken_snaps = list(dict.fromkeys(_dlg_broken_snaps))
        _stale_plan_snaps = list(dict.fromkeys(_stale_plan_snaps))
        if _dlg_broken_snaps:
            # 显式记录（准则 7）：哪几份快照因台词破损被排除，为什么
            _mrt._logger.warn(
                "[BEST-GUARD] 台词完整性门排除 %d 份历史快照（未过本轮计划的选材门，"
                "不参与评分）：%s"
                % (len(_dlg_broken_snaps), _dlg_broken_snaps[:6])
            )
            meta["dialogue_broken_snapshots"] = _dlg_broken_snaps
        if _stale_plan_snaps:
            # 显式记录（准则 7 / 复核 P0-2）：哪几份快照因**计划身份不符**被排除。
            # 这些快照的 plan_refs 是对另一版计划作的声明，编号即使仍在范围内也指向
            # 别的节拍（`insert_after`/`split` 会重排 B 编号）。
            _mrt._logger.warn(
                "[BEST-GUARD] 计划身份不符，排除 %d 份历史快照（它们的 plan_refs 不是"
                "对本轮计划作的声明，不参与评分）：%s"
                % (len(_stale_plan_snaps), _stale_plan_snaps[:6])
            )
            meta["stale_plan_snapshots"] = _stale_plan_snaps
        if _invalid:
            # 显式记录（准则 7）：跳过的原因必须能看见，否则"为什么没选历史稿"无从归因
            _mrt._logger.warn(
                "[BEST-GUARD] 跳过 %d 份**非分镜稿**的 JSON（不评分、不参与选稿；"
                "常见原因是审计/诊断产物落进了 snapshots/ 候选目录）：%s"
                % (len(_invalid), _invalid[:6])
            )
            meta["invalid_candidates"] = _invalid
        # ------------------------------------------------------------------
        # 2026-09-27（用户裁决 4）：**欠账稿不再放回候选池**。
        #
        # 旧行为是「全都有欠账 → 放开限制，按欠账轮次升序选一份」。实测（08:11
        # run）：全部 20 份快照都在台账内 → 选中上一轮刚 BLOCK 的 `072416`
        # （风险 2 只是**确定性**风险，欠的语义问题不在这个数里）→ 重跑同一套审计、
        # 同样的 4 条 blocking、`resolved=0` → 再 BLOCK，并把它自己的新 revision
        # 也写进台账。每轮原地空转：既拿不到新稿的检验结果，又把「确定性风险低」
        # 读成了「已能提交」。
        #
        # 现在的语义：**已 BLOCK 且判据/修复契约未变**的稿不再作为「最佳」稿被重复
        # 修复；没有合格历史候选时如实返回 None——本轮新稿照常进提交门（门仍可
        # BLOCK，正式产物仍受保护：不过门只写 BLOCK manifest）。
        #
        # 判据变过的欠账稿不在此列：`_load_blocked_revisions()` 只把**指纹相符**的
        # 条目当欠账（旧结论可能正是被修掉的那条判据得出的）。
        if not candidates and _blocked_names:
            _debt_map = _mbl._load_blocked_debt()
            _owed = sorted(
                ((int(_debt_map.get(r, 1)), r)
                 for _revs in _blocked_by_ver.values() for r in _revs),
                reverse=True)[:5]
            _mrt._logger.warn(
                "[BEST-GUARD] 全部历史快照都在欠账台账内（%d 份，判据指纹未变）"
                "——本轮**不选历史稿**，让新稿直接进提交门检验；"
                "欠账最重的几份：%s（正式产物仍受保护）"
                % (len(_blocked_names),
                   "、".join(f"{r[:12]}×{n}" for n, r in _owed)))
            meta["reason"] = "all_snapshots_owed"
            meta["blocked_snapshots"] = _blocked_names
            return None, meta
        candidates = candidates[-scan_n:]

        best_path = None
        best_score = -1.0
        best_quality = None
        best_rank = None
        best_script = None
        _prefilter_rejected: list[str] = []
        for sp, old in candidates:
            try:
                sc = _msc._score_script(old, novel_text)["total"]
                q = _msc._candidate_quality_summary(old, novel_text)
                rank = _msc._quality_rank(sc, q)
            except Exception:
                continue
            # 初筛是候选资格，不是冠军资格。先排除不合格快照再排名，
            # 否则第一名被拒后，同一窗口内的次优合格快照永远得不到机会。
            _new_risk = new_quality.get("blocking_risk_count")
            _old_risk = q.get("blocking_risk_count")
            # v6.149（复核根因 3）：候选资格初筛**用与提交点同源的确定性判据**。
            # 此前初筛只看质量门的风险计数，而**跨字段**检查是另一个来源——实测
            # 2026-09-29：快照 `script_result_20260929_053150.json`（它本身就是同一天
            # 早先被判死的输出）以「风险1」入选，一进提交点就带 `result_action_support`
            # 被判 blocking，于是反复被选中、反复 BLOCK（59 份欠账重评也没拦住它）。
            # 全部快照都不合格时 BEST-GUARD 返回 None → 按既有路径改选新稿进提交门。
            _cf_blocking = _mbl._snapshot_cross_field_blocking(old)
            if _cf_blocking is None:
                _prefilter_rejected.append(f"{sp.name}（跨字段校验器异常，未能校验）")
                continue
            if _cf_blocking:
                _prefilter_rejected.append(
                    "%s（跨字段 blocking %d 条，例：%s）"
                    % (sp.name, len(_cf_blocking),
                       str(getattr(_cf_blocking[0], "code", "?"))))
                continue
            if (q.get("quality_checked") is not True
                    or rank[0] > 0 or rank[1] > 0
                    or (_new_risk is not None and _old_risk is not None
                        and int(_old_risk) > int(_new_risk))):
                _prefilter_rejected.append(
                    f"{sp.name}（风险{_old_risk}，结构={q.get('structural_ok')}，"
                    f"完整性={q.get('integrity_ok')}）")
                continue
            # v6.73: compare integrity -> blocking risks -> total, not only
            # the raw story score.  A clean 90 must not lose to a corrupted 100.
            # 2026-09-27（用户裁决 4）：欠账轮次不再参与排序——候选池里已经没有
            # 欠账稿（它们不再被放回），那道次键恒为 0，留着只会误导读者。
            _key = rank
            if best_rank is None or _key < best_rank:
                best_score = sc
                best_quality = q
                best_rank = rank
                best_path = sp
                best_script = old

        if best_path is None:
            if _prefilter_rejected:
                # v6.152（C5）：清单**打全**——此前 `[:6]` 截断，事后无法复核
                # 到底哪几份被拒（run 01 就因此答不出 081241 是否在列）。
                _mrt._logger.warn("[BEST-GUARD] %d 份快照未通过当前质量初筛：%s"
                             % (len(_prefilter_rejected), _prefilter_rejected))
                meta["prefilter_rejected_snapshots"] = _prefilter_rejected
                meta["reason"] = "snapshot_failed_prefilter"
            return None, meta
        if _prefilter_rejected:
            _mrt._logger.warn("[BEST-GUARD] %d 份快照未通过当前质量初筛：%s"
                         % (len(_prefilter_rejected), _prefilter_rejected))
            meta["prefilter_rejected_snapshots"] = _prefilter_rejected
        meta.update({"best_score": best_score, "best_path": str(best_path),
                     "best_risk": (best_quality or {}).get("blocking_risk_count")})

        _same_risk_tier = (
            best_rank[:3] == new_rank[:3]
        )
        # 09-26 P1：**新稿胜出也要留日志**。此前只有「快照胜出」有两行 WARN，
        # 新稿胜出（同档在 margin 内 / 新稿风险档不更差）静默 return——20:49 run
        # 日志里只能看到 BEST-GUARD 存在，看不出快照为什么失利，稳定版与新版本的
        # 取舍无法复核。这里补一行对称 WARN（含 best/new 分数与风险、tier、margin）。
        def _log_new_kept(_reason: str):
            _mrt._logger.warn(
                "[BEST-GUARD] 保留新终稿（" + _reason + "）：本次 "
                + str(new_score) + " 分/风险"
                + str(new_quality.get("blocking_risk_count"))
                + " vs 快照最优 " + str(best_score) + " 分/风险"
                + str((best_quality or {}).get("blocking_risk_count"))
                + "（" + best_path.name + "，tier "
                + str(tuple(best_rank)) + " vs " + str(tuple(new_rank))
                + "，margin=" + str(margin) + "）"
                + "——快照未胜出，不替换"
            )

        if _same_risk_tier:
            if best_score <= new_score + margin:
                meta["reason"] = "same_tier_within_margin"
                _log_new_kept("同风险档，快照分未超出新稿 + margin")
                return None, meta
        else:
            # Prefer the safer snapshot whenever its integrity/risk tier is
            # strictly better than the new run, regardless of raw score.
            if not (best_rank[:3] < new_rank[:3]):
                meta["reason"] = "new_tier_not_worse"
                _log_new_kept("新稿结构/完整性/风险档不更差")
                return None, meta

        _mrt._logger.warn(
            "[BEST-GUARD] 快照综合排序胜出：新终稿 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + "；快照最优 "
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "（" + best_path.name + "）"
        )
        if not keep_best:
            _mrt._logger.warn("[BEST-GUARD] KEEP_BEST_SNAPSHOT=0 → 仅告警，保留新终稿")
            meta["reason"] = "keep_best_disabled"
            return None, meta

        # v6.123：文案必须与事实一致——真写了才说「已留档 <路径>」，没写成要说没写成。
        _new_snap = str(meta.get("new_candidate_snapshot") or "")
        _mrt._logger.warn(
            "[BEST-GUARD] 选中历史快照作为候选（" + str(best_score) + " 分/风险"
            + str(best_quality.get("blocking_risk_count")) + "）；本次 "
            + str(new_score) + " 分版本"
            + ("已留档 " + _new_snap if _new_snap
               else "**快照写入失败，未留档**（见上方 WARN）")
            + "。候选仍走公共提交管线（hook QC → finalize → quality → final audit）"
        )
        _mrt._console(
            "  [BEST-GUARD] 本次 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + "，快照综合排序更优："
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "，已**选中**历史最优版为候选（仍须通过公共提交与最终审计）"
        )
        meta["selected"] = True
        meta["reason"] = "snapshot_strictly_better"
        # v6.129（用户 2026-09-25 根因）：**在选稿边界**把快照**自身**的 revision
        # 一并带出去。它在选稿时成立，但主流程随后会用**本轮大纲**覆盖
        # `script["chapter_outline"]`，而 `chapter_outline` 参与 revision 哈希
        # （`shared/quality_gate._stable_payload`）→ 分镜一字未改，进入提交点时
        # revision 已经变成另一个值（实测 f434e509→380da4c1→0fe1f2b2）。
        # 台账若只记后两者，下次 guard 仍拿快照自身的 revision 查台账 → 漏拦、
        # 同一份欠账稿被反复选中（这就是旧稿反复入选的完整触发链）。
        meta["selected_path"] = str(best_path)
        # 版本字典一并带出去：提交点 BLOCK 时台账要把**主 key 的登记版本**记上，
        # 否则换算法之后这份欠账又查不到了（2026-09-30 复核第 4/5 轮）。
        _sel_aliases = _mbl._revision_aliases_or_empty(best_script)
        meta["selected_revision"] = str(_sel_aliases.get(SCRIPT_REVISION_VERSION) or "")
        meta["selected_revision_aliases"] = _sel_aliases
        if not meta["selected_revision"]:
            _mrt._logger.warn("[BEST-GUARD] 快照自身 revision 计算失败"
                         "（欠账台账可能漏一条）")
        return best_script, meta
    except Exception as _bg_e:
        try:
            _mrt._logger.warn(f"[BEST-GUARD] 护栏执行失败（不影响主流程）: {_bg_e}")
        except Exception:
            pass
        meta["reason"] = f"error: {_bg_e}"
        return None, meta
