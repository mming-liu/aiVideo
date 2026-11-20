"""step01_script_gen - 分镜脚本生成入口（v6 增强版）

功能：
  - 从小说提取章节内容
  - 调用 LLM 生成分镜脚本
  - 生成钩子分镜（v6新增）用于吸引观众
  - 验证音画同步 + 合规校验 + 敏感词校验（v6增强）
  - 注入全局渲染参数（style_prefix.inject_global_config_into_script）
  - 计算时间轴（含运镜量化参数）
  - 角色提取与持久化

输出：
  - script_result.json: 分镜脚本（位于 OUTPUT_BASE/book_N/chapter_N/）
"""
# <split:generated>
from __future__ import annotations
import sys
import os
from pathlib import Path
import json
# </split:generated>

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 运行范围（--chapter / --book / --book-name）必须在下面第一个 config/env 读取之前生效：
# shared.config 的 NOVEL_CHAPTER 与本章的 CHAPTER_DIR/OUTPUT_FILE/_logger 都是 import 时求值，
# 在 main() 里再设 env 已经晚了（会静默用错章）。守卫见 shared/run_scope.bootstrap 文档。
if __name__ == "__main__":
    from shared.run_scope import bootstrap as _bootstrap_scope
    _bootstrap_scope("step01 分镜脚本生成", presentation_mode=True)

# <split:generated>
# 编排实现已按职责拆到 step01_script_gen/orchestrator/；本文件保留入口 main()。
# 门面：对本模块的读写（含 mock.patch / monkeypatch）转发到实现所在模块。
from step01_script_gen.orchestrator import _facade
from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
from step01_script_gen.orchestrator import regen as _mrg
from step01_script_gen.orchestrator import repair_apply as _mra
from step01_script_gen.orchestrator import final_audit as _mfa
from step01_script_gen.orchestrator import repair_accept as _mrc
from step01_script_gen.orchestrator import finalize as _mfz
from step01_script_gen.orchestrator import repair_targets as _mtg
from step01_script_gen.orchestrator import repair_round as _mrr
from step01_script_gen.orchestrator import persist as _mps
from step01_script_gen.orchestrator import blocked_ledger as _mbl
from step01_script_gen.orchestrator import generation as _mgn
from step01_script_gen.orchestrator import commit as _mcm
_facade.install(sys.modules[__name__], [_mrt, _msc, _mrg, _mra, _mfa, _mrc, _mfz, _mtg, _mrr, _mps, _mbl, _mgn, _mcm])
# </split:generated>



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


def _fatal_summary(exc, tb: str) -> str:
    """失败摘要的**单行完整**形态（复核 2026-10-04）.

    `tb.strip().splitlines()[-1]` 在异常消息**含换行**时只拿到最后**一行片断**——计划层的
    停止原因恰好是多行的（问题清单逐条列全），于是控制台上只剩半句：

        [FATAL] 2) F8 声明由 B8 的**画面**承载，但该节拍标的是「载体：台词2」——一条节拍）——额度已耗尽；这是**响应违约**（模型没

    前半截（"连续 2 次响应被拒收…1) F6 …"）在别的行上，读失败摘要的人看不到。改成从
    **异常对象**取完整消息并压掉换行——与 `shared.error_fmt` 的单行打印约定一致。
    """
    if exc is not None:
        _text = "%s: %s" % (type(exc).__name__, exc)
    else:
        _tb = (tb or "").strip()
        _text = _tb.splitlines()[-1] if _tb else ""
    return " ".join(str(_text).split())


def _presentation_mode_argv(argv=None) -> str:
    """早期解析 `--presentation-mode`（**不用 argparse**：本模块没有顶层 parser，
    且必须能在任何配置/日志求值之前跑完）.

    只认这一个参数；其余 argv 原样忽略（Step01 由上层 runner 传参）。非法值抛
    `ValueError` —— 由 `main()` 的调用方转成退出码 2，**不在生成侧才发现**。
    """
    import sys as _sys
    _av = list(_sys.argv[1:] if argv is None else argv)
    _flag = "--presentation-mode"
    if _flag not in _av:
        return ""
    _i = _av.index(_flag)
    if _i + 1 >= len(_av):
        raise ValueError("--presentation-mode 缺少取值（legacy_audio|screen_hybrid）")
    return _av[_i + 1]


def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存.

    控制台静默策略：运行期间所有 print()（校验 WARN、LLM 推理过程等）
    重定向到 step01.log，控制台只显示关键进度和最终摘要。

    **早期 CLI**（主专题 §6.3）：`--presentation-mode legacy_audio|screen_hybrid`，
    **缺省仍是 `legacy_audio`**。在拿锁、建目录、算缓存键**之前**解析并写入运行身份——
    模式进计划/缓存身份（`presentation.mode` + `plan_contract_version`），下游 Step03/04
    从**经提交验证的稿件**读它，不在下游另传开关改稿。非法模式在启动期就退出（码 2），
    不走到生成侧才发现。
    """
    from step01_script_gen.outline import presentation_mode

    _cli_mode = presentation_mode(_presentation_mode_argv())
    globals()["PRESENTATION_MODE"] = _cli_mode
    os.environ["STEP01_PRESENTATION_MODE"] = _cli_mode

    _mrt._logger.separator()
    _mrt._logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _mrt._logger.info("  新增: 钩子分镜 + 敏感词校验")
    _mrt._logger.separator()

    # v6.70.2：章节级 single-flight 锁——禁止两个 step01 并发写同一章
    # （09-14 实测两个 Python 进程交错写 step01.log/script_result.json）。
    if not _mrt._acquire_step01_lock():
        return

    _mrt._timing_start()  # v6.70.3: 阶段计时起点（拿锁成功 = 本 run 独占计时器）
    # 每次模型调用记一条**带 run_id** 的台账（2026-10-02 复核：metrics.calls 全空、
    # 日志里也没有 run_id，事后无法对账）。落点：章节目录的 step01_calls.jsonl。
    from shared.call_ledger import set_ledger_path
    try:
        set_ledger_path(_mrt.CHAPTER_DIR / "step01_calls.jsonl")
    except Exception as _degrade_exc:
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:main", _degrade_exc)

    try:
        # 无声视频模型告警：VIDEO_MODEL_VOICE=1（对话交给视频模型）但当前模型不生成语音时，
        # 成片会无对白/背景音。Seedance 1.0 系列 / MiniMax v1 Hailio 均无声（08-16 适配）。
        _mgn._warn_silent_video_model()

        # 创建输出目录
        _mrt.CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

        # 运行期间 print() 全部重定向到 step01.log，控制台静默
        # 最终摘要用 _console() 写 _REAL_STDOUT（模块级保存的真实 stdout）
        # 同时让 logger 控制台输出静默，避免 logger.print 经重定向回流日志造成双写
        sys.stdout = _mrt._redirect_to_log()
        _mrt._logger.console_silent = True

        # 读取章节
        novel_text = _mgn.read_chapter(None, chapter=_mrt.NOVEL_CHAPTER)
        # v6.115（审核）：缓存指纹此前只取章节**前 1000 字**——后文改动不会改变
        # 指纹，于是「原文变了但缓存命中」→ 用旧稿当新稿用。指纹必须覆盖全部输入。
        novel_source_text = novel_text

        # 加载角色库（v6.1 新增）
        character_bank = _mrt.CharacterBank()
        character_bank.load()

        # 加载场景物体库（v6.6 新增）
        scene_objects_bank = _mrt.SceneObjectsBank()
        scene_objects_bank.load()

        # v6.43（08-25）：3 个 LLM 预提取并发跑（角色/场景物体/大纲）——
        # 原来按顺序执行 = 3 × LLM 延迟（~16s 总耗时），并发后 = max(3) ≈ 5s，
        # 节省 ~10s。3 个 extractor 各自独立 schema/服务/写入目标，并发安全。
        pre_extracted_outline = None
        # 工作包 §4 D / §7.4：**整次运行唯一的绝对截止时刻**在这里算一次，之后
        # `extract_all_concurrent` → `generate_outline_result` → 修订控制器 → 生成前门
        # 全部用它；任何一层都不许再算 `now + cap`（`_planning_deadline_at` 只在此处调用）。
        _deadline_monotonic = _mrt._planning_deadline_at()
        # 结构化大纲结果（§7.2）：未启用大纲提取时为 None；失败原因从它读，不再丢成 None。
        _outline_result = None
        if _mrt.CHARACTER_EXTRACTION_ENABLED or _mrt.SCENE_OBJECT_EXTRACTION_ENABLED or _mrt.OUTLINE_ENABLED:
            _mrt._logger.info("[INFO] 并发预提取 角色/场景物体/大纲（3 路并发）...")
            from step01_script_gen.concurrent_extract import extract_all_concurrent
            from shared.config import SCRIPT_CANDIDATE_MODELS as _pre_models
            _pre_model = _pre_models[0] if len(_pre_models) == 1 else None
            pre_results = extract_all_concurrent(
                novel_text,
                character_bank,
                scene_objects_bank,
                enable_characters=_mrt.CHARACTER_EXTRACTION_ENABLED,
                enable_scene_objects=_mrt.SCENE_OBJECT_EXTRACTION_ENABLED,
                enable_outline=_mrt.OUTLINE_ENABLED,
                model=_pre_model,
                outline_cache_path=_mrt.CHAPTER_DIR / "chapter_outline.cache.json",
                outline_deadline_monotonic=_deadline_monotonic,
                log_func=_mrt._logger.info,
            )
            # === 角色：注册到 bank（主流程后段仍可复用） ===
            # `None` = **这次没产出**（调用/解析失败）⇒ **不写库、不报"已保存"**
            # （2026-10-02 日志复核：鉴权失败那轮把 0 条写进库并报成功）。
            # `[]` = 真的没有角色 ⇒ 正常保存（内容不变，仍留一次来源 run_id）。
            chars = pre_results.get("characters")
            if chars is None:
                _mrt._logger.warn("[WARN] 角色预提取未产出（不写库；既有角色库原样保留）")
            else:
                pre_extracted_chars = chars
                for c in chars:
                    ident = c.get("identifier", "")
                    if not ident:
                        continue
                    if c.get("is_new", True):
                        character_bank.register_character(
                            ident,
                            c.get("static_features", ""),
                            c.get("dynamic_features", ""),
                            _mrt.NOVEL_CHAPTER,
                        )
                    elif c.get("dynamic_features"):
                        character_bank.update_dynamic_features(
                            ident, c.get("dynamic_features", ""),
                        )
                        character_bank.update_character_chapter(ident, _mrt.NOVEL_CHAPTER)
                # 来源=**这一轮确实发生**的调用（从台账取，复核之七）：条目上记
                # `last_call_ids`（最近若干次）+ `last_source`，不编造模型调用；
                # 写盘**成功**才说"已保存"（旧写法无条件报成功）。
                from shared import call_ledger as _cl
                _ok = character_bank.save(
                    run_id=_mrt._step01_run_id(),
                    call_ids=_cl.call_ids_for(schema="characters"))
                if _ok:
                    _mrt._logger.info(f"[INFO] 预提取到 {len(chars)} 个角色特征（已保存到角色库）")
                else:
                    _mrt._logger.warn("[WARN] 角色库**未写入**（保存失败，盘上原样保留）——"
                                 "本次预提取结果未留档")
            # === 场景物体：注册到 bank ===
            objs = pre_results.get("scene_objects")
            if objs is None:
                _mrt._logger.warn("[WARN] 场景物体预提取未产出（不写库；既有物体库原样保留）")
            else:
                for o in objs:
                    scene_objects_bank.register_object(o, _mrt.NOVEL_CHAPTER)
                from shared import call_ledger as _cl
                _ok = scene_objects_bank.save(
                    run_id=_mrt._step01_run_id(),
                    call_ids=_cl.call_ids_for(schema="scene_objects"))
                if _ok:
                    _mrt._logger.info(f"[INFO] 预提取到 {len(objs)} 个场景物体（已保存到场景物体库）")
                else:
                    _mrt._logger.warn("[WARN] 场景物体库**未写入**（保存失败，盘上原样保留）"
                                 "——本次预提取结果未留档")
            # === 大纲：预提取，主分镜生成时注入 prompt（**结构化结果**，§7.2） ===
            _outline_result = pre_results.get("outline")
            if isinstance(_outline_result, dict) and _outline_result.get("executable"):
                pre_extracted_outline = _outline_result.get("plan")
            # `executable=False` **不在这里停**（§7.6）：缓存命中路径不需要计划，停点放在
            # 真正要用它的生成入口——见下方 `_stop_if_outline_unavailable` 的调用。

        # 检查缓存（场景物体库内容变化也视为源变更，v6.6）
        objects_bank_data = json.dumps(
            scene_objects_bank._data, ensure_ascii=False, sort_keys=True
        )
        prompt_changed = _mrt._prompt_changed(
            Path(__file__).parent / "prompts.py", _mrt.SCRIPT_FILE, novel_source_text, _mrt.NOVEL_CHAPTER,
            objects_bank_data, rule_version=_mrt.RULE_VERSION,
        )

        if not prompt_changed:
            _mrt._logger.info(f"[CACHE] 脚本已缓存: {_mrt.SCRIPT_FILE}")
            with open(_mrt.SCRIPT_FILE, "r", encoding="utf-8") as f:
                script = json.load(f)
            if not _mrt.HOOK_ENABLED:
                script.pop("hook", None)
            # 补写章节元信息（部名/章名，老缓存可能缺失）
            if not script.get("chapter_title"):
                script.update(_mrt.get_chapter_meta(_mrt.resolve_novel_file(), _mrt.NOVEL_CHAPTER))
            # 重新计算时间轴（使用增强算法）
            _mrt._compute_timecodes(script)
            # v7: 缓存命中时也要跑场景/道具资产提取（老缓存无 scene_assets/prop_assets），
            # 结果写回 script 再落盘，保证 step02a 资产生成能拿到数据
            _mps._extract_assets_and_update_script(script)
            # v6.62.3（09-08）：缓存命中路径此前**漏跑 hard_fix** —— 只算时间轴+提资产就
            # 落盘，导致两个落盘点行为不一致（正常生成路径见 line ~1926「每次落盘前跑一次
            # hard_fix」，v6.41 P19 铁律）。后果 09-08 实测：命中缓存把 v6.61 期的节拍词
            # 占位（「冲突，反转，真相，局势骤变」17 处）+ F8 合并标记（3 处）原样重新落盘，
            # 且 log 承诺的 W6 时长拉伸（constraints/runtime_extend）从未执行（正片 54.9s
            # < 60s 硬下限）。必须放在所有 transform 之后、dump 之前，且**不可**再调
            # _compute_timecodes（会按 motion_desc 覆盖 W6 拉伸的 duration_sec）。
            # v6.83（二次审核发现 1 实施 #3）：缓存命中路径此前直接跑 hard-fix、
            # **没有接入阶段屏障**（发现 1 第 2 条失守点）。现在与正常生成路径走
            # 同一个收口函数。
            # v6.103：finalize 由唯一提交点统一执行（此处不再重复跑一遍，
            # 避免同一个 run 里出现两个「最后一次时间写入者」）。
            # C5 修复（08-11）：fps 一致性校验（timeline 由提交点的 finalize 产出）
            # v6.62.3：必须晚于 hard_fix —— runtime_extend 会改 duration_sec，
            # 早于 hard_fix 校验会拿到过期时长（与非缓存路径同序）。
            _mgn._check_fps_consistency(script)
            # v6.103（总计划阶段 4）：缓存命中路径此前**只写 script_result.json**
            # ——不产 quality、不产 final audit、不写 commit manifest，于是「三份
            # 产物同 revision」在缓存路径上从来不成立，下游 `require_semantic_gate`
            # 必然拿到过期或缺失的审计。现在与正常生成路径共用唯一提交点。
            # 钩子禁止台词清单：正常路径由钩子生成分支采集（在缓存分支**之后**的
            # 代码里赋值）——缓存命中时那条分支根本不会执行，直接引用会
            # UnboundLocalError。这里就地按同一口径重建（shots 已在缓存里）。
            _cached_forbidden: list = []
            for _cs in script.get("shots") or []:
                _cb = _mrg._speech_body(_cs.get("narration_segment", ""))
                if _cb:
                    _cached_forbidden.append(_cb)
            if pre_extracted_outline:
                script["chapter_outline"] = pre_extracted_outline
            script, _commit_summary = _mcm._commit_step01_outputs(
                script, novel_text,
                plan=script.get("chapter_outline") or {},
                hook_ctx={"bank": character_bank,
                          "objects_bank": scene_objects_bank,
                          "forbidden_lines": _cached_forbidden,
                          "producer_model": None},
                audit_model=_mrg._candidate_model_for_audit(),
                label="缓存命中提交")
            # C1：**两处提交返回走同一个投影**——提交 BLOCK 必须落到运行终局，
            # 缓存路径此前只打了一行 console，metrics/timing 仍报 ok。
            _mrt._project_commit_outcome(_commit_summary)
            _mrt._timing_mark("commit", status=_commit_summary.get("status"))
            _mps._snapshot_run_artifacts(script)
            _mrt._logger.info("时间轴已重新计算")
            # 缓存命中：恢复 stdout 并显示最终摘要
            sys.stdout = _mrt._REAL_STDOUT
            _mrt._logger.console_silent = False
            shots = script.get("shots", [])
            _mrt._console("")
            _mrt._console("=" * 56)
            _mrt._console("  [步骤1] 分镜脚本摘要（缓存命中，未重新生成）"
                     if _commit_summary.get("status") == "COMMITTED"
                     else "  [步骤1] ✗ BLOCK —— 正式产物未更新（缓存命中路径）")
            _mrt._console("=" * 56)
            if _commit_summary.get("status") != "COMMITTED":
                _mrt._console(f"  [BLOCK] {_commit_summary.get('detail')}")
            _mrt._console(f"  输出: {_mrt.SCRIPT_FILE}")
            _mrt._console(f"  镜头数: {len(shots)}" + ("  + 钩子" if script.get("hook") else ""))
            _mrt._console(f"  总时长: {script.get('total_duration', 0):.1f}s")
            _sa = script.get("scene_assets", {})
            _pa = script.get("prop_assets", {})
            if _sa or _pa:
                _mrt._console(f"  资产: 场景{len(_sa)} / 道具{len(_pa)}")
            _mrt._console("=" * 56)
            return

        # 生成脚本（含 JSON 解析重试）
        # LLM 偶发输出损坏的 JSON（DeepSeek 尤甚：内部中文引号未转义/缺逗号等）。
        # 解析失败时重新调用 LLM 重试，而不是让整个流水线崩溃（每次约 3-6 分钟）。
        # v6.64（P4.5，09-11）：多路径择优——生成 SCRIPT_CANDIDATE_TOPK 个独立候选，
        # 取质量分最高的进入 GAP 循环。用 token 换稳定性，对冲模型方差。
        # 默认 doubao=2 / deepseek=1（向下兼容：=1 时行为零变化）。
        # v6.132（用户 2026-09-25）：生成执行契约（不压缩）——四个完整稿生成入口共用。
        # 在这里算：计划已定稿（缓存命中或重选都已完成），后续 GAP/收敛轮直接复用。
        # 工作包 §4 D / §7.6：真正要用计划时才判"能不能下发"——缓存命中路径已在上面
        # 提前返回，不受影响；这里停下 ⇒ **零次候选生成**，且失败原因保真到 main。
        _mgn._stop_if_outline_unavailable(_outline_result, novel_text, _deadline_monotonic)

        _contract = _mrt.render_execution_contract(pre_extracted_outline)
        _mrt._logger.info("[INFO] 生成执行契约 %d 字符（选用台词 %d 句）"
                     % (len(_contract),
                        len((pre_extracted_outline or {}).get("dialogue_lines") or [])))
        _candidates = _mgn._generate_script_candidates(
            novel_text, character_bank, scene_objects_bank, pre_extracted_outline,
            deadline_monotonic=_deadline_monotonic,
        )
        _mrt._timing_mark("candidate_generation",
                     n_candidates=len(_candidates),
                     best_score=_candidates[0][0]["total"] if _candidates else None)


        # 选总分最高的候选
        _candidates.sort(key=_msc._candidate_sort_key)
        _best_score, script = _candidates[0]
        # v6.67 接力修复：记录当前稿件的产出模型。GAP 重生成若被采纳会重置为 None
        #（产出者回到 provider 默认模型，接力标识失效）——见下方 GAP 循环。
        _producer_model = _best_score.get("_model")
        _mrt._logger.warn(f"[DEBUG-RELAY] _best_score keys={list(_best_score.keys())[:5]}, _producer_model={_producer_model!r}")
        if _mrt.SCRIPT_CANDIDATE_TOPK > 1:
            _mrt._logger.info(
                f"[CANDIDATE] 多路径择优完成：{len(_candidates)} 个候选 → "
                f"最优分 {_best_score['total']}（{_best_score['detail']}）"
            )
            _mrt._console(
                f"  [择优] {len(_candidates)} 个候选 → 最优 {_best_score['total']}分 "
                f"（{_best_score['detail']}）"
            )
            # 其余候选存盘供调试
            import copy as _cp
            try:
                from shared.json_io import atomic_dump_json as _adj
                _cand_dir = _mrt.CHAPTER_DIR / "candidates"
                _cand_dir.mkdir(parents=True, exist_ok=True)
                for _ci, (_cs, _cscript) in enumerate(_candidates):
                    _cpath = _cand_dir / f"candidate_{_ci+1}_score{int(_cs['total'])}.json"
                    _adj(_cpath, _msc._strip_runtime_fields(_cp.deepcopy(_cscript)),
                         ensure_ascii=False, indent=2)
            except Exception as _degrade_exc:
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("step01_script_gen/main.py:main", _degrade_exc)

        # ========== 差距分析（v5.0 新增）==========
        # 若差距过大，重新生成（最多2次）

        if _mrt.GAP_ANALYSIS_ENABLED:
            # v6.62.4（09-08）预算动态化：原 `for attempt in range(GAP_MAX_REGEN_ATTEMPTS)`。
            # agent_plan(doubao) 预算是 1（v6.62.1 实测「2 次重试全部劣化回退、白烧
            # 5 分钟」刻意压到 1）。但「正片零高潮覆盖」是**结构性缺陷**（整段剧情没拍），
            # 不是措辞问题，值得单独破例给 1 次。仅在预算即将耗尽时追加，且只追加一次；
            # deepseek（预算 2）不会凭空变 3，除非它也零高潮。
            # 追加那次自动受下方 v6.47 `_is_retry_degraded` 回退保护（时长塌缩/台词
            # 失锚即回退上一版），所以多给一次不引入劣化风险。
            _gap_budget = _mrt.GAP_MAX_REGEN_ATTEMPTS
            _zero_climax_bonus_used = False
            attempt = 0
            while attempt < _gap_budget:
                # 快速校验
                validation = _mrt.validate_script_gap(script, novel_text)

                if validation["overall_pass"]:
                    _mrt._logger.info(f"[GAP] 差距分析通过 (尝试 {attempt + 1}/{_gap_budget})")
                    # 保存脚本并生成完整报告
                    partial_path = _mrt.CHAPTER_DIR / "script_result.partial.json"
                    from shared.json_io import atomic_dump_json as _adj
                    _adj(partial_path, _msc._strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
                    report = _mrt.generate_gap_report(
                        str(partial_path),
                        _mrt.resolve_novel_file(),
                        _mrt.NOVEL_CHAPTER
                    )
                    # 打印报告摘要
                    _mrt.print_gap_report(report)
                    # 清理临时文件
                    if partial_path.exists():
                        partial_path.unlink()
                    break
                if not _mrt.gap_needs_full_regen(validation):
                    _mrt._logger.warn(
                        "[GAP] 仅软告警（角色特征锚点等确定性可修复项），"
                        "跳过整稿重生，转交下游约束修复"
                    )
                    _mrt._console("  [GAP] 仅软告警，跳过整稿重生")
                    break

                # 发现问题，反馈给 LLM 重新生成
                # v6.62.4：零高潮=结构性缺陷 → 预算耗尽前额外追加 1 次（只一次）
                _dist = validation.get("distribution", {}) or {}
                if (_dist.get("climax_shots", 0) == 0
                        and not _zero_climax_bonus_used
                        and attempt + 1 >= _gap_budget):
                    _gap_budget += 1
                    _zero_climax_bonus_used = True
                    _mrt._logger.warn(
                        "[GAP] 正片零高潮覆盖（结构性缺陷：原文核心冲突整段未拍）"
                        f"→ 额外追加 1 次重试预算（{_gap_budget} 次）"
                    )
                    _mrt._console("  [GAP] 正片零高潮覆盖 → 额外追加 1 次重试")

                _mrt._logger.warn(f"[GAP] 发现差距问题， 重新生成 (尝试 {attempt + 1}/{_gap_budget})...")

                # 获取差距分析上下文并追加到 prompt（函数已在模块级导入）
                gap_context = _mrt._load_gap_analysis_context(validation)
                # v6.47（08-27）反馈前置：重生成铁律放在反馈区**最前**——长 prompt 后段
                # 规则易失声（实测重试版把总时长写塌到 11s、编造原文没有的台词），
                # 必须在反馈前重申非协商规则作为拒收标准
                gap_context = _mrt._build_regen_iron_rules() + gap_context
                # v6.33（08-12）：附加 checklist 聚合反馈——差距分析之外，把内容密度/景别
                # 跳跃/致命伤等真实缺陷也喂给重生成 LLM（此前只喂覆盖率/分布，重生成还是老问题）
                _checklist_fb = _mrt.build_regen_feedback(script, novel_text)
                if _checklist_fb:
                    gap_context = gap_context + _checklist_fb

                # 追加差距反馈到 prompt，重新生成
                # v6.68: GAP 重生成未指定模型（=provider 默认模型），prompt 剖面同步跟随。
                # 默认模型 deepseek-v4-flash → deepseek 剖面不截断，救援能直接看到原文高潮
                #（此前 1600 截断下救援只能靠 gap_context 里的高潮摘录）。
                # v6.132（用户 2026-09-25）：最终组装（锚点 → 反馈 → **契约在末尾**）
                # 抽成 `_build_gap_regen_prompt()`——"块在提示里"不等于"块在末尾"，
                # 抽出来才能对**最终请求**断言末尾位置。
                prompt, _gap_system = _mrg._build_gap_regen_prompt(
                    novel_text, character_bank, scene_objects_bank,
                    pre_extracted_outline, _contract, gap_context)

                _mrt._metrics_inc("calls.gap_regen")
                service = _mrt.create_service()
                from _text_gen.deepseek_schema import SCRIPT_SCHEMA
                req = _mrt.TextGenerationRequest(
                    system_prompt=_gap_system,
                    user_prompt=prompt,
                    temperature=0.2,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
                    json_schema=SCRIPT_SCHEMA,
                    timeout_sec=_mrt._full_script_timeout_sec(),  # v6.70.3: GAP 重生 300s 分级
                )
                result = service.generate(req)
                if not result.success:
                    # v6.62.5（09-08）：原先这里 `raise RuntimeError` 会**穿出 GAP 循环
                    # 直接崩掉 step01**（本循环只 catch ValueError/JSONDecodeError）。
                    # v6.62.5 起 provider 把「输出截断」也判为 success=False，这条路径
                    # 从罕见（网络错误）变常见（截断），必须与站点 422/1146 一致地软降级：
                    # 保留上一版脚本继续跑，而不是让整个流水线挂掉。
                    _mrt._logger.warn(f"[WARN] GAP 重生成失败，保留上一版: {result.error}")
                    _mrt._console("  [GAP] 重生成失败（详见 step01.log），保留上一版")
                    break

                import re
                raw = result.text.strip()
                raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
                raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
                raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

                try:
                    new_script = _mrt.parse_response(raw, novel_text=novel_text)
                except (ValueError, json.JSONDecodeError) as e:
                    # 重生成结果 JSON 损坏 → 保留上一版，不中断流水线
                    _mrt._logger.warn(f"[WARN] 重生成 JSON 解析失败，保留上一版: {e}")
                    break
                # v6.62.7（09-09）零高潮救援：先判断这次重试是不是**修好了结构性
                # 缺陷**。回退保护此前对「结构性缺陷 vs 措辞瑕疵」一视同仁，于是把
                # 唯一一次修好结构的重试也扔掉——09-08 实测链路：正片零高潮 →
                # v6.62.4 特批 1 次重试 → 23:31 重试版以「台词失锚 2 条」被判退化回退
                # → 终稿仍零高潮（原文 3100-4847 字的刺杀/尖叫/掩尸整段没拍，刺客与
                # 张十三被提取进 characters 却零出镜，成片是 63s 吹箫风光片配一个
                # 「公子遇刺」的钩子）。两个修复互相抵消，跟 v6.62.6 那次同构。
                _new_climax = ((_mrt.validate_script_gap(new_script, novel_text)
                                .get("distribution") or {}).get("climax_shots", 0))
                _climax_rescue = _dist.get("climax_shots", 0) == 0 and _new_climax > 0
                # 救援态下被豁免的软判据仍要留痕，否则「放宽了什么」不可追溯
                _waived = (_msc._retry_degrade_reason(script, new_script, novel_text)
                           if _climax_rescue else None)
                # v6.47（08-27）回退保护：重试版质量退化（时长塌缩/台词失锚变多）→
                # 保留上一版。GAP 通过≠质量好——08-27 重试版 11 镜×1s 靠加镜头骗过
                # 覆盖检查但时长崩了，事后 W6 把 1s 拉到 8s（8 倍慢动作）。
                if _msc._is_retry_degraded(script, new_script, novel_text,
                                      climax_rescue=_climax_rescue):
                    _reason = _msc._retry_degrade_reason(script, new_script, novel_text,
                                                    climax_rescue=_climax_rescue)
                    _mrt._logger.warn(f"[GAP] 重试版质量退化：{_reason}，回退保留上一版")
                    _mrt._console(f"  [GAP] 重试版质量退化，回退保留上一版（{_reason}）")
                    break
                # v6.157（2026-09-30 复核第 4 轮「重生成身份维护」）：GAP 重生成
                # 也是**整份替换**——采纳前绑定本轮计划身份并核完整契约，不合格保留
                # 上一版（此前这里只判「质量退化」，契约与身份都不在判据里）。
                _id_why = _mrg._bind_regen_plan_identity(script, new_script,
                                                    plan=pre_extracted_outline)
                if _id_why:
                    _mrt._logger.warn("[GAP] 重生成稿未保住计划身份/契约：%s，回退保留上一版"
                                 % "；".join(_id_why[:2]))
                    _mrt._console("  [GAP] 重生成稿未保住计划身份/契约，回退保留上一版")
                    break
                if _climax_rescue:
                    _mrt._logger.warn(
                        f"[GAP] 零高潮救援：上一版 0 个高潮镜 → 重试版 {_new_climax} 个，"
                        f"采纳重试版" + (f"（已豁免软判据：{_waived}）" if _waived else "")
                    )
                    _mrt._console(f"  [GAP] 零高潮救援：采纳重试版（+{_new_climax} 高潮镜）")
                script = new_script
                # v6.67 接力修复：GAP 重生成走的是 provider 默认模型，产出者标识重置
                _producer_model = None
                # 不在循环内 post_process：循环退出后统一在 line 334 做一次
                # （code-audit 07-31 #7：原 :328 与 :334 重复调用 _post_process）
                attempt += 1   # v6.62.4：while 循环手动推进（原 for 由 range 推进）
            else:
                # 达到最大尝试次数仍有问题，记录但不中断
                # while...else 与原 for...else 语义一致：无 break 走完才告警
                _mrt._logger.warn("[GAP] 达到最大重试次数，仍有差距问题，将继续处理")
            _mrt._timing_mark("gap_loop")  # v6.70.3: 通过/耗尽均在此收口（含 else 分支）

        # v6.67（09-12）接力修复：赢家生成、输家修复——修复轮用互补模型换视角。
        # 修复 prompt 已有「只动告警镜头」+ Mask 字段约束，换模型影响面可控。
        # 候选池空/单模型/产出者无标识（GAP 重生成已采纳）→ _repair_model=None=
        # 同 provider 默认模型，零行为变化。GAP 重生成本身暂不接力（方案②范围）。
        _repair_model = _mrg._complementary_repair_model(_producer_model)
        if _repair_model:
            if _repair_model == _producer_model:
                _mrt._logger.info(f"[RELAY] 单模型继续修复: {_repair_model}")
                _mrt._console(f"  [单模型] 规划/生成/审查/修复均使用 {_repair_model}")
            else:
                _mrt._logger.info(
                    f"[RELAY] 产出模型 {_producer_model} → 修复轮接力 {_repair_model}"
                )
                _mrt._console(
                    f"  [接力] 修复轮改用 {_repair_model}（产出模型 {_producer_model} 的互补模型）"
                )

        # v6.7: 镜头数严重超限时收敛重生成（独立于 gap_analysis，默认开启）
        # v6.70.3: 超总预算 → 跳过可选修复轮（失败即保留原版语义，跳过无数据风险）
        # M04：额外动作启动**之前**为最终审计预留完整时间（不是等到审计开始时
        # 才截短额度）。动作耗时取最终生效的通道超时上限，保守按最坏情况算。
        _adm_regen = (_mrt._action_budget_admission(
            "regen_over_limit", _mrt._action_cost_sec("regen"))
            if _mrt.SHOT_COUNT_REGEN_ENABLED else {"allowed": False})
        if _mrt.SHOT_COUNT_REGEN_ENABLED and _adm_regen.get("allowed"):
            _mrt._timing_mark("pre_regen_over_limit")
            from step01_script_gen.outline import outline_to_anchor as _o2a
            script = _mrg._regen_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                # v6.131：与 GAP 重生/结构收敛同源——带上大纲锚点（含核验过的选用台词）
                outline_anchor=_o2a(pre_extracted_outline, novel_text),
                contract_block=_contract,
                # 2026-09-30 复核第 5 轮 P1-1：**本请求的权威计划**显式交给采纳入口
                # （不能让它去基线里翻 `chapter_outline`——候选阶段它还没写入）
                plan=pre_extracted_outline,
            )
            _mrt._timing_mark("regen_over_limit")
        elif _mrt.SHOT_COUNT_REGEN_ENABLED:
            _mrt._timing_mark("regen_over_limit", status="skipped_budget")
            _freeze_regen = _mfa._freeze_budget_exhausted(
                script, action="regen_over_limit", admission=_adm_regen)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_regen)
            _mrt._logger.warn("[BUDGET] 跳过镜头超限收敛轮（保留当前稿；终局=%s）"
                         % _freeze_regen.get("terminal"))
            _mrt._console("  [预算] 未给最终审计留下完整时间，冻结候选后停止额外生成")

        # v6.84（二次审核发现 1，P0）：**结构收敛轮**——单镜内容需求超 cap /
        # 时长低于下限。这两类缺陷字段级修复轮修不了（不是文本问题，是镜头切分
        # 问题），此前只有报告没有收敛路径，必然带着 BLOCK 落盘。这里让 LLM
        # **重做 shot-plan**（超限段落拆成多个完整子镜；过短镜头并入相邻剧情）。
        _adm_structural = (_mrt._action_budget_admission(
            "regen_structural_over_limit", _mrt._action_cost_sec("regen"))
            if _mrt.SHOT_COUNT_REGEN_ENABLED else {"allowed": False})
        if _mrt.SHOT_COUNT_REGEN_ENABLED and _adm_structural.get("allowed"):
            _mrt._timing_mark("pre_regen_structural")
            from step01_script_gen.outline import outline_to_anchor as _o2a
            # v6.128：结构收敛轮的道具候选短提示同样默认关闭（用户 2026-09-25 决定）
            from step01_script_gen.prop_state_anchor import (
                prop_anchor_block_for as _pabf)
            script = _mrg._regen_structural_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                outline_anchor=_o2a(pre_extracted_outline, novel_text),
                prop_anchor_block=_pabf(pre_extracted_outline),
                contract_block=_contract,
                plan=pre_extracted_outline,      # 同上（复核第 5 轮 P1-1）
            )
            _mrt._timing_mark("regen_structural")
        elif _mrt.SHOT_COUNT_REGEN_ENABLED:
            _mrt._timing_mark("regen_structural", status="skipped_budget")
            _freeze_structural = _mfa._freeze_budget_exhausted(
                script, action="regen_structural_over_limit",
                admission=_adm_structural)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_structural)

        # v6.73: same-model independent audit before targeted repair.
        _audit_issues = []
        try:
            from shared.config import SCRIPT_CANDIDATE_MODELS as _audit_models
            _audit_enabled = (
                len(_audit_models) == 1
                and os.getenv("SINGLE_MODEL_AUDIT", "1").strip() == "1"
            )
            _budget_hit_mid = _mrt._budget_exceeded_with_pause("mid_pipeline_audit")
            if _audit_enabled and not _budget_hit_mid:
                from step01_script_gen.audit import run_same_model_audit_with_status
                _mrt._metrics_inc("calls.audit")
                _allow, _deadline, _to_info = _mrt._audit_budget_allowance()
                _mrt._logger.info("[AUDIT] 本次审计可用 %ss（%s）｜总预算剩余 %s 秒"
                             % (_allow, _to_info.get("why") or "-",
                                _to_info.get("left") if _to_info.get("left") is not None
                                else "未知"))
                _audit_result = run_same_model_audit_with_status(
                    novel_text, pre_extracted_outline, script,
                    model=_repair_model or _producer_model,
                    timeout_sec=int(_to_info.get("cap") or _mrt.AUDIT_TIMEOUT_CAP_SEC),
                    deadline_ts=_deadline,
                )
                _audit_issues = _audit_result.issues
                # M04：中间轮审计同样分别记四项读数（不进产物，只进 metrics/日志）。
                _mid_timing = _mrt._audit_budget_timing(
                    _audit_result, allowance_sec=_allow,
                    left_sec=_to_info.get("left"), phase="mid_pipeline")
                _mrt._metrics_set("audit.budget_timing_mid", _mid_timing)
                _mrt._logger.info(
                    "[AUDIT][BUDGET] mid_pipeline 剩余预算=%s｜审计允许=%ss｜"
                    "服务端等待=%ss｜流式耗时=%s（来源=%s）"
                    % (_mid_timing["remaining_budget_sec"],
                       _mid_timing["audit_allowance_sec"],
                       _mid_timing["server_wait_sec"],
                       _mid_timing["stream_elapsed_sec"],
                       _mid_timing["stream_timing_source"]))
                _mrt._metrics_set("audit.status", _audit_result.status)
                _mrt._metrics_set("audit.issues", len(_audit_issues))
                # v6.112：中间轮审计也进同一个台账（phase 区分），
                # 否则「修复轮之前那一次审计说了什么」仍然无处可查。
                _mfa._record_audit_diag(
                    {"status": _audit_result.status, "error": _audit_result.error,
                     # v6.117（方案 A）：中间轮审计与提交轮走同一个解析器，可能同样走
                     # 专用通道——台账必须记实际通道/模型，否则同一份台账里两个 phase
                     # 的 provider 归属口径不一致。
                     "model": (getattr(_audit_result, "channel_model", "") or ""
                               or _repair_model or _producer_model),
                     "channel_provider": getattr(_audit_result, "channel_provider", "") or "",
                     "schema_deviations": [str(d) for d in (
                         getattr(_audit_result, "schema_deviations", None) or [])],
                     "usage": getattr(_audit_result, "usage", None),
                     "issues": [i.to_dict() for i in _audit_issues]},
                    phase="mid_pipeline", round_index=0, script=script)
                try:
                    from shared.json_io import atomic_dump_json as _audit_dump
                    # v6.103：中间轮的 advisory 审计**不得**写正式
                    # `step01_audit.json`——那是唯一提交点的产物，被这里覆盖会
                    # ① 让「提交点之外的第二个写入者」重新出现；
                    # ② 破坏 final audit 的复用判据（revision/scope 都对不上）。
                    _audit_dump(
                        _mrt.CHAPTER_DIR / "step01_audit.advisory.json",
                        {
                            "status": _audit_result.status,
                            # 与提交轮同一口径：记**实际提交**的模型/通道
                            "model": (getattr(_audit_result, "channel_model", "") or ""
                                      or _repair_model or _producer_model),
                            "channel_provider": getattr(_audit_result, "channel_provider", "") or "",
                            "usage": getattr(_audit_result, "usage", None),
                            "error": _audit_result.error,
                            "issues": [i.to_dict() for i in _audit_issues],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                except Exception as _audit_dump_err:
                    _mrt._logger.warn(f"[AUDIT] 审查结果落盘失败（不阻断）: {_audit_dump_err}")
                if _audit_result.status == "ok":
                    _mrt._logger.info(f"[AUDIT] same-model audit issues={len(_audit_issues)}")
                else:
                    _mrt._metrics_set("audit.error", _audit_result.error)
                    _mrt._logger.warn(
                        f"[AUDIT] same-model audit {_audit_result.status}"
                        f"（区别于 0 issue，不阻断）: {_audit_result.error}"
                    )
        except Exception as _audit_err:
            _mrt._metrics_set("audit.status", "exception")
            _mrt._metrics_set("audit.error", str(_audit_err))
            _mrt._logger.warn(f"[AUDIT] same-model audit failed（不阻断）: {_audit_err}")

        # v6.35（08-15，P18 闭环）：GAP 通过后仍存在的高价值告警（开篇冲突/
        # 爆点密度/威胁源朝向/关键帧质量/内容密度等）→ 定向修复轮，只重写
        # 告警镜头（此前这些 WARN 纯打印落盘，无人修，带伤进 step02）。
        _adm_repair = (_mrt._action_budget_admission(
            "repair_rounds", _mrt._action_cost_sec("repair"))
            if _mrt.REPAIR_ROUNDS_ENABLED else {"allowed": False})
        if _mrt.REPAIR_ROUNDS_ENABLED and _adm_repair.get("allowed"):
            _mrt._timing_mark("pre_repair_rounds")
            script = _mrr._repair_shot_warnings(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                audit_issues=_audit_issues,
            )
            _mrt._timing_mark("repair_rounds")
        elif _mrt.REPAIR_ROUNDS_ENABLED:
            _mrt._timing_mark("repair_rounds", status="skipped_budget")
            _freeze_repair = _mfa._freeze_budget_exhausted(
                script, action="repair_rounds", admission=_adm_repair)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_repair)
            _mrt._logger.warn("[BUDGET] 跳过定向修复轮（保留当前稿，告警仍会落 SAVE-WARN；"
                         "终局=%s）" % _freeze_repair.get("terminal"))
            _mrt._console("  [预算] 未给最终审计留下完整时间，跳过定向修复轮")

        # 后处理：注入全局配置 + 合规过滤 + 敏感词校验
        script = _mgn._post_process(script)

        # 脚本已更新，删除旧的音频和视频文件。
        # 2026-08-09 用户需求：若新旧脚本只是分镜数量差异且总时长≤3min，逐镜对比后
        # 保留未变镜头的帧/视频产物，只清理新增/改动/删除的镜头（避免重跑 step02 全量
        # 重新生成白烧额度）。
        # 2026-08-09 修复：原实现把此段放在 script 生成之前，引用未赋值变量 script
        # 触发 UnboundLocalError，逐镜保留从未生效（每次都全量清理）。现在移到
        # _post_process 之后——新分镜已定稿（含收敛/合规过滤），旧 script_result.json
        # 仍在磁盘可读，对比才有意义；且 LLM 生成失败时旧产物也不会被提前删掉。
        _preserve_shots: list[int] = []
        try:
            from shared.shot_diff import compute_shot_diff, should_preserve_artifacts
            _old_script = json.load(open(_mrt.SCRIPT_FILE, encoding="utf-8")) if _mrt.SCRIPT_FILE.exists() else None
            if _old_script and should_preserve_artifacts(_old_script, script):
                _diff = compute_shot_diff(_old_script, script)
                _preserve_shots = _diff.get("unchanged", [])
                _mrt._logger.info(
                    f"[PRESERVE] 逐镜对比放行：未变 {len(_preserve_shots)} 镜"
                    f"（保留旧产物），新增/改动 {len(_diff.get('added', [])) + len(_diff.get('changed', []))} 镜重新生成"
                )
                # 只清理 changed/removed 镜头的帧/视频（未变镜头保留）
                for _sub_path, _shots_root in (
                    (_mrt.vimax_working_dir(), _mrt.vimax_shots_dir()),
                    (_mrt.stretched_dir(), _mrt.stretched_dir() / "shots"),
                ):
                    if _sub_path.exists():
                        if _shots_root.exists():
                            for _sid in _diff.get("changed", []) + _diff.get("removed", []):
                                _shot_dir = _shots_root / str(_sid)
                                if _shot_dir.exists():
                                    import shutil as _sh
                                    _sh.rmtree(_shot_dir)
                                    _mrt._logger.verbose(f"  [已清理] {_sub_path.name}/shots/{_sid}/（内容变更）")
        except Exception as _preserve_err:
            _mrt._logger.warn(f"[PRESERVE] 逐镜对比失败，全量清理: {_preserve_err}")

        if not _preserve_shots:
            _mrt._logger.info(f"[INFO] 分镜脚本已更新，清理旧的音视频文件...")
            for subdir_path in (_mrt.audio_dir(), _mrt.vimax_working_dir(), _mrt.stretched_dir()):
                if subdir_path.exists():
                    import shutil
                    try:
                        shutil.rmtree(subdir_path)
                        _mrt._logger.verbose(f"  [已删除] {subdir_path.name}/")
                    except PermissionError as _e:
                        # 09-13 实测：Explorer/看图器/杀毒占用目录 → WinError 32。
                        # 清理失败就 FATAL 会吞掉整个 LLM 生成结果（此时 script 尚未
                        # 落盘，两轮实跑各 ~10 min 全白跑）。旧帧由内容哈希自动失效
                        # （ff/kf desc 变更 → hash mismatch → step02a 重抽），跳过即可。
                        _mrt._logger.warn(f"  [WARN] {subdir_path.name}/ 被占用（{type(_e).__name__}），"
                                     "跳过清理——旧文件由内容哈希自动失效")
        # 删除最终视频和字幕（脚本结构变了，最终产物必须重做）
        for fpath in (_mrt.final_file(), _mrt.subtitles_file()):
            if fpath.exists():
                try:
                    fpath.unlink()
                    _mrt._logger.verbose(f"  [已删除] {fpath.name}")
                except PermissionError:
                    _mrt._logger.warn(f"  [WARN] {fpath.name} 被占用（播放器？），跳过重删")

        # ========================================================================
        # 钩子分镜生成（v6 沿用 + v6.39 调用顺序硬约束）
        # ========================================================================
        # 严格 3 步调用顺序（C4 修复 08-11 + v6.32 强化 08-12），禁止任何步骤乱序：
        #
        #   ┌─────────────────────────────────────────────────────────────┐
        #   │  ① 提取 forbidden_lines  ← 必须在主分镜 shots 落盘后做         │
        #   │     (剥说话人前缀的台词体列表，注入钩子 prompt 防重复穿帮)      │
        #   │                          ↓                                  │
        #   │  ② 调用 hook = gen_hook_script(forbidden_lines=...)          │
        #   │     (钩子 LLM 在 prompt 看到禁止清单，从源头不写重复台词)       │
        #   │                          ↓                                  │
        #   │  ③ 合并 + 去重 retry + 兜底清空                               │
        #   │     (挂载后比对台词，仍重复则重试 2 次；再重复则清空台词字段)     │
        #   └─────────────────────────────────────────────────────────────┘
        #
        # 关键 invariant（破坏任意一条会引发「钩子台词=主分镜台词」倒叙穿帮）：
        #   - 步骤① forbidden_lines 必须在 shots 落盘后才能采集（空列表=无效）
        #   - 步骤② gen_hook_script 调用时 forbidden_lines 必须非 None
        #   - 步骤③ _hook_dup_hits 比对时 shots 列表未变（避免 race）
        # ========================================================================
        _forbidden_lines: list[str] | None = None
        if _mrt.HOOK_ENABLED and _mfz._hook_max_sends() > 0:
            #: 钩子分类预算（初始 1 次 + 重复重试；上限 `_hook_max_sends()`）
            _hook_sends = 0
            # ===== ① 提取 forbidden_lines（assert 保护调用顺序）=====
            _shots_for_hook = script.get("shots", [])
            assert _shots_for_hook, (
                "HOOK pipeline 顺序保护：步骤②前必须先生成主分镜 shots。"
                "当前 script['shots'] 为空——主分镜 LLM 调用可能失败或被跳过。"
            )
            _forbidden_lines: list[str] = []
            for _s in _shots_for_hook:
                _body = _mrg._speech_body(_s.get("narration_segment", ""))
                if _body:
                    _forbidden_lines.append(_body)
            _mrt._logger.info(f"[INFO] 钩子禁止台词清单已就绪（{len(_forbidden_lines)} 条）")

            # ===== ② 调用 hook LLM（注入 forbidden_lines）=====
            _mrt._logger.info("\n[INFO] 开始生成钩子分镜...")
            hook = _mgn.gen_hook_script(
                novel_text,
                bank=character_bank,
                objects_bank=scene_objects_bank,
                forbidden_lines=_forbidden_lines,  # 必传参数，v6.32 强约束
            )
            _hook_sends += 1
            _mrt._timing_mark("hook_generation", ok=hook is not None)

            if hook:
                # ===== ③ 合并 + 去重 retry + 兜底清空 =====
                script["hook"] = hook
                _mrt._logger.info(f"[OK] 钩子分镜已生成: {hook.get('narration_segment', '')[:30]}...")

                # 闭包：引用外层 _shots_for_hook（步骤③ shots 列表不变 invariant）
                def _hook_dup_hits() -> list:
                    """钩子台词与主分镜任一镜头台词重复的镜头 index 列表（空=无重复）."""
                    _hl = _mrg._speech_body(hook.get("narration_segment", ""))
                    _hits = []
                    if not _hl:
                        return _hits
                    for _s in _shots_for_hook:  # ← 闭包用 _shots_for_hook 而非 script.get，避免 race
                        _sb = _mrg._speech_body(_s.get("narration_segment", ""))
                        if _sb and (_hl == _sb or _hl in _sb or _sb in _hl):
                            _hits.append(_s.get("index", "?"))
                    return _hits

                # C4 修复（08-11）+ v6.32 强化（08-12）：挂载后立即比对台词去重，
                # 重复则自动重新生成钩子（换一句台词），最多再试 2 次；仍重复才 WARN
                # 提示人工处理。钩子是「最精彩瞬间」的倒叙，与主分镜台词重复会直接
                # 穿帮（观众刚在钩子听到、又在下个镜头原样听到）。
                _dup_hits = _hook_dup_hits()
                _regen_attempt = 0
                while (_dup_hits and _regen_attempt < 2
                       and _hook_sends < _mfz._hook_max_sends()):
                    _regen_attempt += 1
                    _hook_line_prev = _mrg._speech_body(hook.get("narration_segment", ""))
                    _mrt._logger.warn(
                        f"[WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复（C4 修复），"
                        f"自动重新生成钩子换台词（尝试 {_regen_attempt}/2）..."
                    )
                    _mrt._console(f"  [WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复，重新生成钩子（{_regen_attempt}/2）...")
                    _dup_note = (
                        f"上一版钩子台词「{_hook_line_prev[:30]}」与主分镜镜头 {_dup_hits} 的台词重复"
                        f"（观众刚在钩子听到、又在正片听到，倒叙穿帮）。请严格换一句完全不同的台词：\n"
                        f"1. 优先选主分镜其它镜头未用过的对话（如角色丙的质问/密谋、角色甲的台词、管事丁的试探）；\n"
                        f"2. 若所有人物对话都与主分镜重复，则把 narration_segment 和 subtitle 设为空字符串\"\"，"
                        f"钩子改纯画面动作开场（袭击者破水而出、匕首出鞘的瞬间），靠视觉冲击不靠台词。"
                    )
                    _hook_retry = _mgn.gen_hook_script(
                        novel_text, bank=character_bank, objects_bank=scene_objects_bank,
                        forbidden_lines=_forbidden_lines, dup_note=_dup_note,
                    )
                    _hook_sends += 1
                    if not _hook_retry:
                        break  # 重生成失败，保留原钩子
                    hook = _hook_retry
                    script["hook"] = hook
                    _mrt._logger.info(f"[OK] 钩子分镜已重新生成: {hook.get('narration_segment', '')[:30]}...")
                    _dup_hits = _hook_dup_hits()
                if _dup_hits:
                    # 确定性兜底（08-12）：重试仍重复则清空钩子台词，改纯画面开场——
                    # 钩子画面（刺客破水+匕首下刺）本身是强开场，宁可无台词也不倒叙穿帮。
                    hook["narration_segment"] = ""
                    hook["subtitle"] = ""
                    _mrt._logger.warn(
                        f"[WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复且重生成仍未解决，"
                        f"已强制改为纯画面开场（narration_segment 清空）——无台词优于倒叙穿帮。"
                    )
                    _mrt._console(f"  [WARN] 钩子台词仍重复，已改为纯画面开场")
                # v6.18：钩子在 parse_response 之后才挂载（_validate_script 已跑完），
                # 必须补自动修复 + 校验，否则钩子 ff_desc 零校验零修复（多动作堆叠
                # 直接落盘，导致首帧图画不出三个同时态动作）。
                try:
                    from step01_script_gen.validation import post_fix_shot_prompts, validate_hook
                    post_fix_shot_prompts(script)  # 幂等：shots 已修复过，主要触发 hook 分支
                    validate_hook(script["hook"], script.get("characters", []))
                except Exception as _hook_fix_err:
                    _mrt._logger.warn(f"[WARN] 钩子修复/校验异常（不阻断）: {_hook_fix_err}")

                # ===== ④ 钩子后置校验轮（v6.70.1，09-13；09-14 抽出共享门）=====
                # 结构性缺口：钩子生成时点在定向修复轮之后，checklist 的 LLM 修复轮
                # 永远轮不到 hook——09-13 实测：修复轮 600s 超时保留原版 + 钩子原生
                # 输出带无主刀尖直接落盘 → step02a QC 名单打架整镜烧穿。质量门抽为
                # _ensure_hook_quality 共享：对两条正式输出来源（本次 run /
                # BEST-GUARD 恢复的快照）同等生效。
                try:
                    _mcm._ensure_hook_quality(
                        script, novel_text=novel_text, bank=character_bank,
                        objects_bank=scene_objects_bank,
                        forbidden_lines=_forbidden_lines,
                        producer_model=_producer_model)
                except Exception as _e:
                    _mrt._logger.warn(f"[WARN] 钩子后置校验异常（不阻断）: {_e}")
            else:
                _mrt._logger.info("[INFO] 跳过钩子分镜")

        # 提取角色（跨章节一致性）
        if _mrt.CHARACTER_EXTRACTION_ENABLED:
            if pre_extracted_chars is not None:
                # 复用预提取结果，避免二次 LLM 调用（角色库已在预提取阶段保存）
                chars = pre_extracted_chars
                _mrt._logger.info(f"[INFO] 复用预提取的 {len(chars)} 个角色")
            else:
                # 预提取阶段失败时才重新提取
                _mrt._logger.info("[INFO] 提取角色信息...")
                # #26 修复 2026-08-14：LLM 提取失败不应中断整条流水线——
                # 回退到脚本解析阶段已收集的 characters（可能为空），仅 WARN。
                # 2026-10-02：提取器把"没产出"（调用/解析失败）与"真的没有角色"分开
                # ——前者返回 `None`，这里同样按失败回退（旧实现两者都是 `[]`，
                # 于是 `script["characters"]` 会被写成 None / 记成"0 个"）。
                try:
                    chars = _mrt.extract_characters(novel_text, character_bank)
                    if chars is None:
                        _mrt._logger.warn(
                            "[WARN] 角色提取未产出（调用/解析失败）——回退使用脚本内已有 characters")
                        chars = script.get("characters", []) or []
                except Exception as _char_err:
                    _mrt._logger.warn(
                        f"[WARN] 角色提取失败（不阻断）: {_char_err}，"
                        f"回退使用脚本内已有 characters")
                    chars = script.get("characters", []) or []
            script["characters"] = chars
            # 注意：角色库已在预提取阶段保存，无需重复保存
            _mrt._logger.info(f"[INFO] 已提取 {len(chars)} 个角色")

        # 写入场景物体锚定（v6.6）：供下游 step02/validation 引用统一尺寸
        _scene_objects_out = {}
        for _oid in scene_objects_bank.get_all_identifiers():
            _obj = scene_objects_bank.get_object(_oid)
            if _obj:
                _scene_objects_out[_oid] = {
                    k: _obj[k] for k in ("category", "subtype", "length_m", "width_m",
                                         "height_m", "capacity", "structure",
                                         "consistent_terms")
                    if k in _obj
                }
        script["scene_objects"] = _scene_objects_out

        # v7: 场景/道具资产提取（依赖 script 的 shots[].scene/location/prompt，
        # 必须在脚本生成/收敛之后执行；缓存命中路径也调用同一函数）
        _mps._extract_assets_and_update_script(script)
        _mrt._timing_mark("asset_extraction")

        # 计算时间轴（v5.0 运镜量化参数）
        _mrt._compute_timecodes(script)
        # 08-23 v6.41 P19 闭环：硬修复层必须在 _compute_timecodes 之后调用——
        # 此前 hard_fix 在 REPAIR 内部调用，但 line 1659 _compute_timecodes 会按
        # motion_desc 重算 duration_sec，覆盖 W6 时长拉伸的值。改为：每次落盘前
        # 跑一次 hard_fix_shot_prompts（含 W6 时长拉伸），保证最终值持久化。
        # v6.42：改用新 Constraint 框架（is_final_pass=True 让 RuntimeExtendConstraint 拉伸时长）
        # v6.83（09-20，二次审核发现 1 实施 #3）：三条路径统一走
        # `finalize_step01_script()` —— hard-fix（含阶段屏障）+ timeline 序列化 +
        # 最终时间不变量，顺序写在一处，任何入口都不能绕过。
        script, _finalize_errs = _mfz.finalize_step01_script(
            script, novel_text=novel_text, label="落盘前")
        # C5 修复（08-11）：fps 一致性校验（timeline 此时已存在）
        _mgn._check_fps_consistency(script)

        # 章节元信息（部名/章名，来自小说标题行「第一部 杀青州 第NNN章 章名」）
        script.update(_mrt.get_chapter_meta(_mrt.resolve_novel_file(), _mrt.NOVEL_CHAPTER))

        # 写入 hash
        # v6.83（二次审核发现 6）：**必须用非下划线键**。
        #
        # 落盘前会调 `_strip_runtime_fields()`，它递归删除所有下划线开头的键——
        # 写 `_source_hash` 等于写了个立刻被删的字段，`_prompt_changed()` 下次
        # 永远读到空值、永远返回 True：**每次运行 step01 都会重新调用模型**，
        # 「重跑 = 缓存重验」这条路径从未生效过（chapter 001 实测
        # `_source_hash=None`）。缓存/version 迁移测试也因此失去意义。
        # 读侧仍兼容旧文件里的 `_source_hash`（见 hash_cache._prompt_changed）。
        script["source_hash"] = _mrt._hash_source(
            str(Path(__file__).parent / "prompts.py"), novel_source_text, _mrt.NOVEL_CHAPTER,
            objects_bank_data, rule_version=_mrt.RULE_VERSION,
        )

        # v6.50（08-29）：保存前重算 shot_count——timecode pre-pass 拆镜发生在
        # parse 期校正之后，元数据会落后实际镜头数（15:12 run 16 vs 20 实测）
        script["shot_count"] = len(script.get("shots", []))

        # v6.70.1（09-14）：BEST-GUARD 可能选中历史快照——v6.103 起它**只选候选**，
        # 不再落盘、不再 finalize、不再跑钩子门；被选中的快照与本次终稿走**同一条**
        # 公共提交管线（hook QC → finalize → revision → quality → final audit）。
        if not _mrt.HOOK_ENABLED:
            script.pop("hook", None)
        # v6.135（工作包 sunny-kindling-wilkinson，审核约束③时序）：**终稿台词完整性门**
        # ——盖在定向拆分（`_scoped_split_over_cap_shots` 会重排 narration）与
        # finalize **之后**、BEST-GUARD 之前：这一版才是实际送入选稿/提交门的最终形态。
        # 09-26 02:30 实损证明碎片可能由拆分层产生（候选层单镜完整、拆后句中截断），
        # 只查候选会漏。破损 ⇒ 显式失败（决策点 1），不静默落回历史快照。
        from step01_script_gen.plan_feasibility import (DialogueGateError,
                                                        DialogueIncompleteError,
                                                        check_script_dialogue_integrity,
                                                        dump_dialogue_incomplete,
                                                        plan_dialogue_lines)
        # v6.156（2026-09-30 复核第 5 组）：事件履约契约**同源复核**——拆分层可能
        # 让 plan_refs 的节拍/台词关系断开（覆盖门与台词门各自都能过），这里与台词门
        # 同位置、同 fail-closed 口径再核一次。
        try:
            from step01_script_gen.plan_feasibility import (EventContractBrokenError,
                                                           check_event_contract)
            # 同上（P1-1）：拆分/finalize 之后的终稿层同样要求逐镜声明
            _ct_ok, _ct_viol, _ = check_event_contract(
                script, pre_extracted_outline, require_declaration=True)
        except Exception as _e:
            _ct_ok = False
            _ct_viol = [{"code": "gate_error", "shot": "", "detail": str(_e)}]
            _mrt._logger.error(f"[CANDIDATE] 事件履约契约门异常（终稿层，fail-closed）：{_e}")
        if not _ct_ok:
            _mrt._logger.error(
                "[CANDIDATE] event contract failed（终稿层，拆分/finalize 后）："
                + "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                          str(v.get("detail"))[:60])
                            for v in _ct_viol[:3]))
            raise EventContractBrokenError(_ct_viol)
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(script, pre_extracted_outline)
        except Exception as _e:
            # v6.135 四审 P1：门自身异常 = 无法证明台词完整 → fail-closed 显式 BLOCK
            # （独立诊断），不裸崩、不退回原样匹配放行
            _dlg_ok = False
            _dlg_broken = [{"line": "", "reason": "gate_error: %s" % _e}]
            _mrt._logger.error(f"[CANDIDATE] 台词门异常（终稿层，fail-closed BLOCK）：{_e}")
        if not _dlg_ok:
            _dlg_total = len(plan_dialogue_lines(pre_extracted_outline))
            dump_dialogue_incomplete(_mrt.CHAPTER_DIR, [_dlg_broken], total_lines=_dlg_total,
                                     layer="终稿层", plan=pre_extracted_outline)
            _dlg_gate_err = str(_dlg_broken[0].get("reason") or "").startswith("gate_error")
            _mrt._logger.error(
                "[CANDIDATE] dialogue integrity failed（终稿台词完整性门，拆分/finalize 后）："
                + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                            for b in _dlg_broken[:3]))
            if _dlg_gate_err:
                # v6.135 五审：纯门故障 ≠ 稿件不完整——独立异常类 + 诊断
                # conclusion=dialogue_gate_error（dump 按批次自动归类）
                raise DialogueGateError(
                    "终稿台词完整性硬门自身异常（fail-closed 不放行）：%s"
                    % str(_dlg_broken[0].get("reason") or "")[:200],
                    broken_batches=[_dlg_broken])
            raise DialogueIncompleteError(
                "终稿台词完整性硬门：%d/%d 句破损（示例：%s…［%s］）——"
                "碎片大概率由定向拆分产生，破损稿不进入选稿/提交门"
                % (len(_dlg_broken), _dlg_total,
                   str(_dlg_broken[0].get("line") or "")[:40],
                   _dlg_broken[0].get("reason")),
                broken_batches=[_dlg_broken])
        _bg_selected, _bg_meta = _cross_run_best_guard(script, novel_text,
                                                       plan=pre_extracted_outline)
        if _bg_selected is not None:
            script = _bg_selected
        if pre_extracted_outline:
            script["chapter_outline"] = pre_extracted_outline
        _mrt._metrics_update("best_guard", {
            "selected": bool(_bg_meta.get("selected")),
            "reason": _bg_meta.get("reason"),
            "new_score": _bg_meta.get("new_score"),
            "best_score": _bg_meta.get("best_score"),
            # v6.152（C5）：被初筛拒的快照清单落进产物（此前只在内存 meta 里，
            # 日志又截断到 6 条 ⇒ 事后无从复核）
            "prefilter_rejected": list(
                _bg_meta.get("prefilter_rejected_snapshots") or []),
        })

        # ===== 唯一最终提交点（v6.103，总计划阶段 4）=====
        # hook QC/重生 → finalize → revision → 确定性 quality → final semantic
        # audit → （有 blocking 则受限修复 ≤2 轮）→ staged 三件套自验 →
        # audit → quality → script 依次原子替换 → step01_commit.json。
        # 三条入口（正常生成 / 缓存命中 / BEST-GUARD 选中）都只走这里。
        script, _commit_summary = _mcm._commit_step01_outputs(
            script, novel_text,
            plan=script.get("chapter_outline") or pre_extracted_outline,
            hook_ctx={"bank": character_bank,
                      "objects_bank": scene_objects_bank,
                      "forbidden_lines": _forbidden_lines,
                      "producer_model": _producer_model},
            audit_model=_repair_model or _producer_model,
            label="落盘前提交",
            # v6.129：选稿边界记录的快照自身 revision（见 `_cross_run_best_guard`）
            source_revision=str(_bg_meta.get("selected_revision") or ""),
            source_aliases=dict(_bg_meta.get("selected_revision_aliases") or {}))
        # C1：提交结果 → 运行终局（**与缓存命中路径同一投影**）
        _mrt._project_commit_outcome(_commit_summary)
        # C5 修复（08-11）：fps 一致性校验（finalize 之后 timeline 已存在）
        _mgn._check_fps_consistency(script)
        # 提交成功后留档（失败时提交点自己会写诊断快照）
        if _commit_summary.get("status") == "COMMITTED":
            _mps._snapshot_run_artifacts(script)
        _mrt._timing_mark("commit", status=_commit_summary.get("status"))

        # v6.70.1（09-13）：提交后未解决 issue 汇总——对最终写盘版本生效。
        # 定向修复轮超时/失败走「保留原版」时只有一行 WARN，残伤数据静默落盘
        # （09-13 hook 无主刀尖即此形态），这里显式打出（不阻塞；确定性护栏已在
        # hard_fix 修过一轮，剩下的是 LLM 级）。
        try:
            _final_hook_issues = _msc._collect_hook_issues(script) if script.get("hook") else []
            _final_hard = _msc._collect_repair_warnings(script, novel_text)
            _n_hard_shots = len([k for k in (_final_hard.get("shots") or {})
                                 if k not in ("_has_hard",)])
            if _final_hard.get("_has_hard") or _final_hook_issues:
                _mrt._logger.warn(
                    f"[SAVE-WARN] ⚠️ 落盘脚本仍带未解决 issue：{_n_hard_shots} 镜 + "
                    f"钩子 {len(_final_hook_issues)} 条——修复轮可能超时保留原版，"
                    f"请过目 step01 日志再进 step02a")
                _mrt._console(f"  ⚠️ 落盘前仍有未解决 issue（{_n_hard_shots} 镜 / 钩子 "
                         f"{len(_final_hook_issues)} 条），详见日志")
        except Exception as _e:
            _mrt._logger.warn(f"[WARN] 落盘前 issue 汇总异常（不阻断）: {_e}")

        # 质量报告由提交点产出（staged → 自验 → 原子替换），这里只消费它的结果。
        _quality = _commit_summary.get("quality") or {}
        _shot_total = sum(float(s.get("duration_sec") or 0) for s in script.get("shots", []))
        _hook_total = float((script.get("hook") or {}).get("duration_sec") or 0)
        _mrt._metrics_update("duration", {
            "duration_band": _mrt.duration_band(),
            "shots_total_sec": round(_shot_total, 3),
            "hook_sec": round(_hook_total, 3),
            "total_sec": round(_shot_total + _hook_total, 3),
            "budget_status": (
                "PASS" if _mrt.DURATION_MIN_SEC - 0.01 <= _shot_total <= _mrt.DURATION_MAX_SEC + 0.01 else "BLOCK"
            ),
        })
        _mrt._metrics_update("quality", {
            "status": _quality.get("status"),
            "hard_count": _quality.get("hard_count", 0),
            "visual_risk_count": _quality.get("visual_risk_count", 0),
            "advisory_count": _quality.get("advisory_count", 0),
            "video_advisory_count": _quality.get("video_advisory_count", 0),
            "blocking_categories": _quality.get("blocking_categories", []),
            "rule_version": _quality.get("rule_version"),
        })
        if not _quality or _quality.get("status") is None:
            # 2026-09-27（复核三轮）：提交提前中止时（本轮实测：最终审计调用
            # `request_failed: Connection error`）**根本没有产出质量报告**——
            # `_commit_summary` 里没有 `quality` 键，于是下面那个 `else` 会打出
            # 「PASS: 无 HARD / VisualRisk 阻塞项」。那是**没检出**被说成**没问题**
            # （工作准则 7），而且同一个 run 的 manifest 里还列着 10 条 blocking。
            # 「没有可用的质量判定」必须显式说出来。
            _mrt._logger.warn(
                "[QUALITY-GATE] 未产出质量报告（提交提前中止 / 审计未完成）——"
                "本次没有可用的质量判定，不得据此认为无阻塞项"
            )
        elif _quality.get("blocking"):
            _mrt._logger.warn(
                f"[QUALITY-GATE] BLOCK: hard={_quality.get('hard_count', 0)}, "
                f"visual={_quality.get('visual_risk_count', 0)}; "
                f"categories={_quality.get('blocking_categories', [])}"
            )
            _mrt._console("  [QUALITY-GATE] BLOCK: 仍有生图阻塞告警，见 step01_quality.json")
        else:
            _mrt._logger.info("[QUALITY-GATE] PASS: 无 HARD / VisualRisk 阻塞项")
        # v6.103：**只有真的提交了**才能说「已保存」。提交点 BLOCK 时三份正式产物
        # 一个字都没换（旧脚本会被骗成「已保存」——真实重跑实测过：BLOCK 之后仍打
        # 了「[OK] 分镜脚本已保存至 …」，而文件时间戳还是上一版）。
        _committed = _commit_summary.get("status") == "COMMITTED"
        if _committed:
            _mrt._logger.info(f"[OK] 分镜脚本已提交至 {_mrt.OUTPUT_FILE}")
        else:
            _mrt._logger.error(
                f"[BLOCK] 分镜脚本**未提交**（三份正式产物保持上一版）："
                f"{_commit_summary.get('detail')}"
            )

        # ---- 最终摘要：写真实 stdout（控制台），绕过重定向 ----
        shots = script.get("shots", [])
        _mrt._console("")
        _mrt._console("=" * 56)
        _mrt._console("  [步骤1] " + ("完成 —— 分镜脚本摘要" if _committed
                                  else "✗ BLOCK —— 正式产物未更新"))
        _mrt._console("=" * 56)
        if not _committed:
            _mrt._console(f"  [BLOCK] {_commit_summary.get('detail')}")
            _mrt._console("  step01_commit.json 标记为 BLOCK；下游（step02a）会拒绝启动")
        _mrt._console(f"  输出: {_mrt.OUTPUT_FILE}")
        _mrt._console(f"  镜头数: {len(shots)}" + ("  + 钩子" if script.get("hook") else ""))
        _mrt._console(f"  总时长: {script.get('total_duration', 0):.1f}s")
        _scene_objs = script.get("scene_objects", {})
        if _scene_objs:
            _mrt._console(f"  场景物体: {', '.join(_scene_objs.keys())}")
        _sa = script.get("scene_assets", {})
        _pa = script.get("prop_assets", {})
        if _sa or _pa:
            _mrt._console(f"  资产: 场景{len(_sa)}个 {', '.join(_sa.keys())} / 道具{len(_pa)}个 {', '.join(_pa.keys())}")
        for k in ("resolution", "ratio", "fps", "render_style"):
            if k in script:
                _mrt._console(f"  {k}: {script[k]}")
        _mrt._console("  前 3 镜:")
        for shot in shots[:3]:
            idx = shot.get("index", "?")
            sub = (shot.get('subtitle', '') or '').strip()
            if sub:
                _mrt._console(f"    - 镜{idx}: {sub[:42]}")
            else:
                # subtitle 为空时回退到 main_subject（避免误报"空镜"）
                ms = (shot.get('main_subject', '') or '').strip()
                _mrt._console(f"    - 镜{idx}: [{ms[:42]}] （subtitle 空）" if ms else f"    - 镜{idx}: (无 main_subject)")
        _mrt._console("=" * 56)
        _mrt._console("  ⚠️ 详细校验 WARN 已写入 step01.log，控制台不再重复输出")
        _mrt._console("=" * 56)

        # 恢复 stdout
        sys.stdout = _mrt._REAL_STDOUT
        _mrt._logger.console_silent = False
    finally:
        _mrt._release_step01_lock()
        # **终局判定单点**（2026-10-02 复核）：`finally` 里 `sys.exc_info()` 非空 ⇒ 本轮
        # 是带着异常退出的（含 `PlanInfeasibleError` 这类"计划层停下"）⇒ metrics/timing
        # 如实写 failed + 码；旧实现无论怎么死都写 `_total status=ok`。
        _exc = sys.exc_info()[0]
        if _exc is not None:
            _mrt._run_finish("failed", getattr(_exc, "__name__", str(_exc)))
        _mrt._timing_dump(_mrt.CHAPTER_DIR / "step01_timing.json")  # v6.70.3: 阶段耗时指标落盘
        _mrt._metrics_dump(_mrt.CHAPTER_DIR / "step01_metrics.json")  # 调用/修复/时长结构化指标


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        # 完整 traceback 写入 step01.log（verbose 仅文件，不依赖 stderr）
        for line in tb.splitlines():
            _mrt._logger.verbose(line)
        # 恢复真实 stdout，控制台显示一行错误摘要
        sys.stdout = _mrt._REAL_STDOUT
        _mrt._logger.console_silent = False
        _mrt._logger.error(f"[FATAL] {_fatal_summary(sys.exc_info()[1], tb)}")
        sys.exit(1)
