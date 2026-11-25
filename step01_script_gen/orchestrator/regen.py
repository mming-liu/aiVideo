# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.regen —— 退化判定、超限与结构性重生成、拆镜.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import json
import re
from collections import Counter as _Counter

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
# ============================================================================
# 5. 镜头数超限收敛重生成（v6.7 新增）
# ============================================================================

def _speech_body(narration: str) -> str:
    """从「说话人道：台词」中提取台词体（剥说话人前缀，去标点）.

    C4 修复（08-11）用：钩子台词与主分镜台词比对去重。台词比对前先剥
    「说话人道：」前缀（不同说话人说同一句也判重）并去空白标点。
    """
    if not narration:
        return ""
    import re as _re
    body = _re.split(r"[:：]", narration, maxsplit=1)[-1]
    return _re.sub(r"[\s　，。！？、；：""''（）【】《》…—-]", "", body)

def _candidate_model_for_audit() -> "str | None":
    """final semantic audit 用的模型（单模型配置下与生成同源）.

    与既有的「同模型审计」口径一致：候选模型只有一个时，审计就该用它——
    审计与被审对象同源才有意义；配了多个候选模型时没有唯一的「同源模型」，
    交 provider 默认（`None`），与旧行为相同。
    """
    try:
        from shared.config import SCRIPT_CANDIDATE_MODELS as _models
        return _models[0] if len(_models) == 1 else None
    except Exception:
        return None


def _complementary_repair_model(winner_model: str | None) -> str | None:
    """Resolve the repair model.

    Multi-model mode keeps the historical complementary-model behavior.  In
    single-model mode we explicitly keep planning/generation/audit/repair on
    the same model instead of falling through to the provider default model.
    """
    from shared.config import SCRIPT_CANDIDATE_MODELS
    models = list(SCRIPT_CANDIDATE_MODELS or [])
    if len(models) == 1:
        return winner_model or models[0]
    if not winner_model or len(models) < 2:
        return None
    if winner_model not in models:
        return None
    for _m in models:
        if _m != winner_model:
            return _m
    return None


def _actual_shots_total(script: dict) -> float:
    """正片**实际** `Σduration_sec`（v6.86 用户决定 B1：时长收敛的判定口径）.

    与 `project_post_split_metrics()` 的区别：那个是**拆镜后投影**（会把超 cap 的
    镜头按 cap 折算并计入拆镜次数），生产路径的拆镜 pre-pass 已停用
    （`timecode.py` 的 `allow_split=False`），所以投影描述的是**不会发生的剧本**。
    投影口径保留给**候选打分**（那里确实要在拆镜前预估），时长收敛与质量门统一用实际值。
    """
    return sum(float(s.get("duration_sec") or s.get("duration") or 0)
               for s in (script.get("shots") or []) if isinstance(s, dict))


def _blocking_not_degraded(old_q: dict, new_q: dict) -> tuple:
    """**全部 blocking**（hard + visual_risk）相对旧稿是否未退化；返回 (是否通过, 原因).

    v6.99（二次审核 P0-A）：「统一候选验收」此前只比较手工列出的 9 个
    `integrity_codes`，其余 blocking 一概不看——候选可以在解决结构超载的同时
    引入新的 KF 越界类阻塞并被采纳（第十一次重跑「超载归零、同时新出现 3 条 KF
    越界」就是这一型）。

    判据用 **Counter 多重集**，不是集合也不是总数：
      - 用集合会漏掉「同一问题从 1 次涨到 3 次」；
      - 用总数会漏掉「修掉 A、引入 B」（数量不变）。
    要求 `Counter(新) - Counter(旧) == 空`：目标问题允许消失，**不允许出现新身份，
    也不允许既有身份次数增加**。

    身份与镜头序号无关（见 `_blocking_identity`），所以整脚本重生造成的
    镜头重排不会把每个问题都算成「新身份」。
    """
    _new_ids = list(new_q.get("blocking_ids") or [])
    _old_ids = list(old_q.get("blocking_ids") or [])
    if not _new_ids:
        return True, ""
    if not _old_ids:
        # 旧稿干净而新稿不干净 → 只要新稿有 blocking 就是退化
        return False, "新稿出现 %d 条 blocking（旧稿 0 条）：%s" % (
            len(_new_ids), _new_ids[0][:80])
    _extra = _Counter(_new_ids) - _Counter(_old_ids)
    if _extra:
        _items = list(_extra.items())
        # 报「**净增**次数」而不是原文重述：`Counter` 差集给的是增量，
        # 写成「×N」会被读成「总次数」，日志里判不出是新增还是变多。
        _desc = "; ".join("%s（净增 %d 次）" % (k[:70], v) for k, v in _items[:3])
        return False, "新 blocking 身份 %d 类: %s" % (len(_items), _desc)
    return True, ""


def _integrity_not_degraded(old_q: dict, new_q: dict) -> tuple:
    """`new_q` 的完整性相对 `old_q` 是否**未退化**；返回 (是否通过, 原因).

    v6.87（2026-09-21 23:17 实测）：原判据是 `new_q["integrity_ok"] is False` →
    拒绝。**这是错的**：`integrity_ok` 是布尔，原稿本身有完整性异常时任何新稿
    也必然为 False，于是「新稿是否更差」根本无从判断，采纳分支永远不会通过
    ——结构收敛轮 5 → 4 的改善稿被拒，等于没有收敛轮。

    用户 2026-09-21 文档里的用词本来就是「完整性**不退化**」。所以：

      - `checker_failed`（计数为 None）→ **未知不得当成干净**：新稿未知则拒绝，
        旧稿未知则退回布尔口径（只有新稿明确干净才放行）；
      - 两边都可比 → 异常条数不得增加，**且不得出现新身份**（v6.98，审核 P1-2）。
    """
    _o = old_q.get("integrity_count")
    _n = new_q.get("integrity_count")
    if _n is None:
        return False, "新稿完整性检查器未通过（无法验证）"
    if _o is None:
        # 旧稿检查器也没通过 → 没有基线可比；只有新稿**明确干净**（0 条）才放行
        return (_n == 0), "旧稿无完整性基线（仅当新稿 0 条时放行）"
    if _n > _o:
        return False, f"完整性异常 {_o} → {_n} 条"
    # v6.98（审核 P1-2）：数量不增加还不够——**新身份**也不许出现。
    # 「修掉 A、引入 B」在数量口径下是 1 → 1，身份口径下 `new_ids - old_ids` 非空。
    _new_ids = set(new_q.get("integrity_ids") or [])
    if _new_ids:
        _extra = sorted(_new_ids - set(old_q.get("integrity_ids") or []))
        if _extra:
            return False, (f"新完整性身份 {len(_extra)} 类: "
                           + "; ".join(x[:80] for x in _extra[:3]))
    return True, ""


# ---------------------------------------------------------------------------
# v6.98（2026-09-22 审核 P0-2 复核确认）：内容不退化判据的**分母必须来自旧稿**。
#
# 原实现的分母由**候选自己**提供，确定性复现出 3 种逃逸（`_verify_review.py`）：
#   A 候选删光 `core_keywords` → 候选 total=0 → 覆盖率 None → 比较被跳过；
#   B 候选删光 narration     → 候选分母 0      → 比较被跳过；
#   C 10/10 grounded → 1/1 grounded（按比例删 9 镜）→ 比率都是 100%。
# 外加两条 fail-open：检查器抛异常 → `except: pass` → 等于「没有退化」。
#
# 修法（**不**建完整 ContentManifest，理由见审核文档第六节：需要的是
# 「候选不能提供自己的分母」这条性质，不是原文 span/事件 id 那一层——
# 后者在合法合并下粒度会变，反而把合并判成退化）：
#   1. **固定清单**：从**已接受的旧稿**导出「已覆盖的 core_keywords」与
#      「台词锚（去标点后的整句）」。候选只能证明自己覆盖了这份清单。
#   2. **fail-closed**：任一步异常 / None 一律算退化（拒绝候选 = 保留旧稿，
#      不会损坏数据，只会在日志里说明为什么没采纳）。
#   3. grounding 保留**比率**口径——合法合并会减少「有台词镜数」，计数口径
#      会把合并误伤（这正是本函数上一版改成比率的原因）；「删台词」由第 1 条
#      的台词清单兜住，两条合起来才既不放水也不误伤。
# ---------------------------------------------------------------------------
_INVENTORY_DROP = set(
    "，。！？；：、—,.!?;:…“”‘’（）()《》〈〉【】[] "
    + chr(34) + chr(39) + chr(9) + chr(10) + chr(13)
)


def _strip_punct(text) -> str:
    """去掉标点与空白——台词锚的比较口径（合并后子串仍在，不误判丢失）."""
    return "".join(ch for ch in str(text or "") if ch not in _INVENTORY_DROP)


def _covered_keywords(script: dict, novel_text: str) -> set:
    """脚本里**已覆盖**的 `core_keywords` 集合（命中源 = 画面描述全字段）."""
    from step01_script_gen.gap_analysis import (
        calculate_keyword_coverage, normalize_core_keywords,
    )

    covered: set = set()
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        c = calculate_keyword_coverage(s, novel_text)
        missing = {str(x) for x in (c.get("missing") or [])}
        for kw in normalize_core_keywords(s.get("core_keywords", [])):
            if kw not in missing:
                covered.add(kw)
    return covered


def _narration_anchors(script: dict) -> list:
    """台词锚清单：每个有台词的镜头贡献一句（去标点后的整句）."""
    out: list = []
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        a = _strip_punct(s.get("narration_segment"))
        if a:
            out.append(a)
    return out


def _grounding_rate(script: dict, novel_text: str):
    """有台词镜里能在原文定位的比例；无有台词镜 → None（**不等于**通过）."""
    from step01_script_gen.validation import _check_grounding

    total = ok = 0
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        if not str(s.get("narration_segment") or "").strip():
            continue
        total += 1
        if _check_grounding(s, novel_text):
            ok += 1
    return (ok / total) if total else None


def _content_inventory(script: dict, novel_text: str) -> dict:
    """**固定内容清单**（从旧稿导出，候选无权提供分母）.

    这是「不退化」判据的基线，也是审核 P0-2 的根因修法：候选只能证明自己
    覆盖了这份清单，不能自己决定比什么。
    """
    return {
        "keywords": _covered_keywords(script, novel_text),
        "narration": _narration_anchors(script),
        "grounding": _grounding_rate(script, novel_text),
    }


def _content_degradation(before: dict, after: dict, novel_text: str) -> list:
    """`after` 相对 `before` 的**内容退化**清单（空列表 = 不退化）.

    v6.86（用户决定 B2）：时长收敛采纳新稿的前提除「严格改善」与内容完整性外，
    还要求内容不退化。缩短时长最省力的做法就是**删内容**——四条判据正是防它：
    结构非法镜头数、台词清单、已覆盖关键词清单、grounding 率。

    v6.98（2026-09-22 审核 P0-2）：四条判据的分母/清单**全部取自 `before`**，
    且任何检查器异常一律按**退化**处理（fail-closed）。
    """
    reasons: list = []
    # 1) 结构非法镜头数不增加（合并会减少镜头数，但不会让单镜重新超载）
    try:
        _b = len(_structural_shot_issues(before))
        _a = len(_structural_shot_issues(after))
        if _a > _b:
            reasons.append(f"结构非法镜头 {_b} → {_a}")
    except Exception as e:
        reasons.append(f"结构非法镜头检查器异常，按退化处理: {e}")
    # 2) 台词清单：旧稿说过的每一句都必须仍在候选里（合并 = 子串仍在）
    try:
        _blob = "".join(_narration_anchors(after))
        _lost = [a for a in _narration_anchors(before) if a not in _blob]
        if _lost:
            reasons.append(f"台词丢失 {len(_lost)} 句: {[x[:14] for x in _lost[:3]]}")
    except Exception as e:
        reasons.append(f"台词清单检查器异常，按退化处理: {e}")
    # 3) 关键词清单：旧稿**已覆盖**的关键词不得失去覆盖
    try:
        _lost_kw = sorted(_covered_keywords(before, novel_text)
                          - _covered_keywords(after, novel_text))
        if _lost_kw:
            reasons.append(f"已覆盖关键词丢失 {len(_lost_kw)} 个: {_lost_kw[:6]}")
    except Exception as e:
        reasons.append(f"关键词清单检查器异常，按退化处理: {e}")
    # 4) grounding 率：只在**有台词的镜头**里算（无台词镜 `_check_grounding` 恒 True，
    #    放进分母只会稀释比率）。旧稿能算而候选**算不出**（已无有台词镜）= 退化，
    #    不再像旧实现那样「None 就跳过」。
    try:
        _b = _grounding_rate(before, novel_text)
        _a = _grounding_rate(after, novel_text)
        if _b is not None:
            if _a is None:
                reasons.append("grounding 无法验证（候选中已无有台词镜，而旧稿有）")
            elif _a < _b - 1e-9:
                reasons.append(f"grounding 率 {_b:.0%} → {_a:.0%}（有台词镜）")
    except Exception as e:
        reasons.append(f"grounding 检查器异常，按退化处理: {e}")
    return reasons


_ABSENT = object()


def _bind_regen_plan_identity(baseline: dict, candidate: dict, *, plan) -> list:
    """整份重生成采纳前：**绑定本请求的计划身份 + 核完整契约**；返回拒绝原因.

    2026-09-30 复核第 4/5 轮「重生成身份维护」：时长收敛 / 结构收敛 / GAP 重生成都会
    **整份替换**稿件（`script = new_script`），而它们此前只比「不退化」——计划身份、
    `plan_refs` 事件契约、选用台词逐字都不在判据里。一旦采纳，提交层的契约门只能在
    **已经丢掉基线**的前提下报错；更糟的是重生成稿通常不带 `chapter_outline`/`plan_id`，
    会被当成「无可核」放行。

    ⚠️ `plan` 是**必填关键字参数**、且由调用方给出**本请求的权威计划**（复核第 5 轮
    P1-1）：不能从 `baseline["chapter_outline"]` 取——生成候选时它还没写入，取不到就
    当「没有契约」放行，等于这道门在正常新稿路径上不生效；也不能把「缺少上下文」
    自动解释成旧稿兼容。`plan` 为 `None`/空**只**表示"本请求确实没有计划"（旧缓存
    路径），此时留一条可见日志后放行（没有计划 ⇒ 基线自身也没有契约可验）。

    ① **绑定**：把 `plan_identity(plan)` 与计划本身写进候选——身份由**本请求**决定，
       不要求模型回抄；
    ② **验证**：事件履约契约（`require_declaration=True`）+ 选用台词逐字完整性。
       任一不过 ⇒ 拒绝（调用方保留基线；绑定被回滚，不留半份状态）。
    """
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return []
    if not isinstance(plan, dict) or not plan:
        _mrt._logger.info("[REGEN] 本请求没有计划（旧缓存路径）⇒ 无契约可验，不绑定身份")
        return []
    _prev = {k: candidate.get(k, _ABSENT) for k in ("chapter_outline", "plan_id")}
    candidate["chapter_outline"] = plan
    try:
        from step01_script_gen.plan_feasibility import plan_identity
        _pid = plan_identity(plan)
    except Exception:
        _pid = ""
    if _pid:
        candidate["plan_id"] = _pid
    reasons: list = []
    try:
        from step01_script_gen.plan_feasibility import (
            check_event_contract, check_script_dialogue_integrity,
            plan_dialogue_lines)
    except Exception as e:                      # 导入失败 = 无法验证 → 不采纳
        reasons.append("契约检查器不可用，按拒绝处理: %s" % e)
        plan_dialogue_lines = None
    if not reasons:
        try:
            _ok, _viol, _ = check_event_contract(candidate, plan,
                                                 require_declaration=True)
            if not _ok:
                reasons.append("事件履约契约破损（%s）" % "；".join(
                    "%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                  str(v.get("detail"))[:40])
                    for v in (_viol or [])[:3]))
        except Exception as e:
            reasons.append("事件履约契约检查器异常，按拒绝处理: %s" % e)
        if plan_dialogue_lines and plan_dialogue_lines(plan):
            try:
                _d_ok, _d_broken = check_script_dialogue_integrity(candidate, plan)
                if not _d_ok:
                    reasons.append("选用台词未逐字保住 %d 句（例：%s）" % (
                        len(_d_broken),
                        str((_d_broken or [{}])[0].get("line") or "")[:20]))
            except Exception as e:
                reasons.append("台词完整性检查器异常，按拒绝处理: %s" % e)
    if reasons:
        # 拒绝时回滚绑定：调用方虽然丢弃候选，但不留半份状态更安全（准则 7）。
        for _k, _v in _prev.items():
            if _v is _ABSENT:
                candidate.pop(_k, None)
            else:
                candidate[_k] = _v
    return reasons


def _accept_regen_candidate(before: dict, candidate: dict, novel_text: str, *,
                            plan) -> tuple:
    """**统一候选验收入口**：任何「重生成稿 → 是否采纳」都必须从这一个出口过.

    v6.98（2026-09-22 审核 P0-1）：结构收敛轮的采纳逻辑被**复制成两条分支**——
    「新稿结构非法数 = 0」走一条直接 `return new_script` 的快速返回，完整性比较
    只在 `0 < len(new_issues) < len(issues)` 那条分支里。于是**修得最好的那次
    结构重生恰好拥有最弱的验收**：删掉关键事件、改坏角色状态、引入新的阻塞身份，
    只要「结构干净」就被采纳。

    根因修法（审核建议）：**不为「完全修好」保留快速返回**——目标指标为 0 只是
    目标指标的最佳值，不代表其他维度安全。所有 `N → M`（含 `M=0`）都只能从这里出去。

    判据（全部 fail-closed）：
      0. **全部 blocking 不退化**（无新身份、既有身份次数不增，见
         `_blocking_not_degraded`——v6.99 起是这道门的**主体**；只比 integrity
         子集会让「新引入 KF 类阻塞」的候选照样通过）；
      1. 完整性**不退化**（计数不增 + 无新身份，见 `_integrity_not_degraded`）；
      2. 内容**不退化**（结构非法数 / 台词清单 / 已覆盖关键词清单 / grounding 率，
         见 `_content_degradation`——清单取自 `before`，候选不能提供自己的分母）。

    Returns:
        `(是否采纳, 拒绝原因列表)`；原因列表为空即采纳。
    """
    try:
        old_q = _msc._candidate_quality_summary(before, novel_text)
    except Exception as e:
        return False, [f"原稿完整性摘要检查器异常，按拒绝处理: {e}"]
    try:
        new_q = _msc._candidate_quality_summary(candidate, novel_text)
    except Exception as e:
        return False, [f"候选完整性摘要检查器异常，按拒绝处理: {e}"]
    reasons: list = []
    # v6.99（二次审核 P0-A）：**第一道门就是「全部 blocking 不退化」**。
    # 它比下面的 integrity 子集更宽（9 个码 → 全部 hard/visual_risk），
    # 因为「统一验收」必须覆盖候选能引入的**所有**阻塞。
    _ok, _why = _blocking_not_degraded(old_q, new_q)
    if not _ok:
        reasons.append("blocking 退化：" + str(_why))
    _ok_i, _why_i = _integrity_not_degraded(old_q, new_q)
    if not _ok_i:
        reasons.append("完整性退化：" + str(_why_i))
    reasons.extend(_content_degradation(before, candidate, novel_text))
    # v6.157（2026-09-30 复核第 4/5 轮）：**计划身份与完整契约**——「不退化」不等于
    # 「还是这一集」。`plan` 由调用方给出**本请求的权威计划**（不从基线里翻找，
    # 见 `_bind_regen_plan_identity` 的说明），不合格同样保留基线。
    reasons.extend(_bind_regen_plan_identity(before, candidate, plan=plan))
    return (not reasons), reasons


def _build_gap_regen_prompt(novel_text: str, bank, objects_bank, outline, contract: str,
                            gap_context: str) -> tuple:
    """GAP 重生的**最终提示组装**：大纲锚点 → 反馈（gap_context）→ **执行契约（末尾）**.

    v6.132（用户 2026-09-25）：契约必须由最终组装处追加——builder 之后还有 gap_context，
    契约在 builder 里就不在末尾。

    Returns `(prompt, system_prompt)`。
    """
    _gap_builder, _gap_system = _mrt.resolve_model_prompt_profile(None)
    # v6.116（审核）：GAP 重生同样复用大纲锚点
    from step01_script_gen.outline import outline_to_anchor as _o2a
    # v6.128：道具候选短提示默认关闭（用户 2026-09-25 决定）
    from step01_script_gen.prop_state_anchor import prop_anchor_block_for as _pabf
    prompt = _gap_builder(
        novel_text, bank, objects_bank,
        _o2a(outline, novel_text),
        prop_anchor_block=_pabf(outline))
    prompt = prompt + gap_context
    return _mrt.append_execution_contract(prompt, contract), _gap_system


def _regen_over_limit(script: dict, novel_text: str, bank=None, objects_bank=None,
                      max_attempts: int | None = None,
                      model: str | None = None,
                      outline_anchor: str = "",
                      contract_block: str = "",
                      plan=None) -> dict:
    """镜头数/正片时长超限时，反馈给 LLM 合并精简，重新生成一次（软收敛）.

    与 gap_analysis 相互独立：这里只看「镜头数/总时长」超限，不校验内容分布。
    仍超限或 JSON 解析失败时保留原版，不中断流水线（下游有 TTS rate / merge
    truncation / stretch cap 兜底，v4.1 哲学是镜头数交 LLM，此处只收敛废输出）。

    v6.86（**用户决定 B1 + B2**，2026-09-21）：

      - **B1 判定口径 = 实际 `Σduration_sec`**（`_actual_shots_total`）。原实现量的是
        `project_post_split_metrics()` 的**拆镜后投影**，而质量门量的是实际值——两者
        不是同一个量，且拆镜 pre-pass 生产已停用，投影是「不会发生的剧本」。
      - **B2 采纳判据 = 严格改善 + 完整性不退化 + 内容不退化**。原实现
        `script = new_script` 解析成功即无条件替换、零判据，是时长维度唯一的环节
        且不设防；实测把 103.0s 改成 111.38s 也照单全收。「内容不退化」由
        `_content_degradation()` 给出：结构非法镜头数不增加、原文覆盖不退化、
        grounding 不退化——**缩短时长不得用「删内容」换**。
      - 修复预算**暂时保持 1 次**（用户决定：先看提示词容量自检的效果再定是否提高）。

    Returns:
        重新生成且**通过 B2 判据**的 script；否则返回原 script。

    v6.43（08-23 用户规则）镜头数不限：SHOT_COUNT_MAX<=0 时整个收敛+截断逻辑
    全部跳过（不再 regen、不再 force-truncate），镜头数完全由 LLM 决定。
    """
    from shared.config import SHOT_COUNT_MAX

    # 重试次数单源 shared.config.GAP_MAX_REGEN_ATTEMPTS（签名默认 None=不覆盖）
    if max_attempts is None:
        max_attempts = _mrt.GAP_MAX_REGEN_ATTEMPTS

    shots = script.get("shots", [])
    # v6.86（**用户决定 B1**，2026-09-21）：判定口径改用**实际** `Σduration_sec`。
    #
    # 原实现量的是 `project_post_split_metrics()` 的**拆镜后投影**（实测触发时
    # 103.0s），而质量门 `duration_budget` 量的是实际值（93.3s）。投影会把超 cap 的
    # 镜头按 cap 折算并**计入拆镜次数**，但拆镜 pre-pass 在生产路径**已停用**——
    # 投影描述的是一个**不会发生的剧本**。触发阈值与验收阈值因此不是同一个量：
    # 投影 < 90 而实际 > 90 时**该收敛的不触发**；投影 ≫ 实际时反复重生成，且要求
    # 模型压的是投影而不是门禁量的那个数。投影口径保留给候选打分。
    shots_total = _actual_shots_total(script)
    duration_over = shots_total > _mrt.DURATION_MAX_SEC + 0.01
    over_max = SHOT_COUNT_MAX > 0 and len(shots) > SHOT_COUNT_MAX

    # 镜头数不限时仍需保留时长硬约束：一次显式压缩重生成优于让 100s
    # 脚本静默通过，晚到生图/合成阶段才返工。
    if SHOT_COUNT_MAX <= 0 and not duration_over:
        return script

    for attempt in range(1, 1 + max_attempts):
        if not over_max and not duration_over:
            break

        current = len(script.get("shots", []))
        if over_max:
            _mrt._logger.warn(
                f"[COUNT] 镜头数 {current} 超上限 {SHOT_COUNT_MAX}，"
                f"反馈 LLM 合并冗余镜头（尝试 {attempt}/{max_attempts}）..."
            )
            _mrt._console(f"  [收敛] 镜头数 {current} 超上限 {SHOT_COUNT_MAX}，重新生成精简版（{attempt}/{max_attempts}）...")
        if duration_over:
            _mrt._logger.warn(
                f"[DURATION] 正片 {shots_total:.1f}s > {_mrt.DURATION_MAX_SEC:g}s，"
                f"反馈 LLM 重新安排表达与镜头（合法 {_mrt.DURATION_MIN_SEC:g}-{_mrt.DURATION_MAX_SEC:g}s；"
                f"目标 {_mrt.DURATION_TARGET_MIN_SEC:g}-{_mrt.DURATION_TARGET_MAX_SEC:g}s 仅优先，尝试 {attempt}/{max_attempts}）..."
            )
            _mrt._console(f"  [时长] 正片 {shots_total:.1f}s 超 {_mrt.DURATION_MAX_SEC:g}s，重新安排镜头（{attempt}/{max_attempts}）...")

        feedback = ""
        if over_max:
            from shared.config import SHOT_COUNT_MAX as _max
            feedback += (
                f"\n\n# ★ 镜头数硬约束（v6.40 08-22 升级，本轮拒收）\n"
                f"你上一轮出了 {current} 镜，超上限 {_max} {current - _max} 镜。\n"
                f"**本轮拒收 ≤ {_max} 镜的输出——必须严格 ≤ {_max} 镜**。\n\n"
                f"严格合并规则：\n"
                f"1. 同一主体 + 同一地点 + 连续同一动作 = 1 镜（用动作重叠法或 clip_transition=硬切合并）\n"
                f"2. 相机角度微调 / 同对话正反打 = 1 镜（不要拆）\n"
                f"3. 只有主体变 / 地点变 / 时空跳跃 / 旁白主题切换 = 拆镜\n"
                f"4. 删减冗余过渡镜头与重复表达的空镜\n"
                f"5. 保留全部关键剧情节点（开篇冲突 / 刺杀 / 高潮 / 收尾），不删剧情只合并冗余\n"
                f"6. **不要把多镜拆成单镜的细碎画面**——一镜只承载 1 个核心动作/情绪转折\n\n"
                f"v6.38 红果硬约束：正片总时长 {_mrt.DURATION_MIN_SEC:g}-{_mrt.DURATION_MAX_SEC:g}s（钩子另算）、单镜 3.5-{_shot_cap():.0f}s（不得超过 {_shot_cap():.0f}s）。\n"
            )
        if duration_over:
            feedback += (
                f"\n\n# ★ 正片时长硬约束（本轮拒收，最高优先级）\n"
                f"当前正片分镜合计 {shots_total:.1f}s，超过 {_mrt.DURATION_MAX_SEC:g}s 上限。\n"
                f"**本轮只要求回到合法区间 {_mrt.DURATION_MIN_SEC:g}-{_mrt.DURATION_MAX_SEC:g}s；"
                f"{_mrt.DURATION_TARGET_MIN_SEC:g}-{_mrt.DURATION_TARGET_MAX_SEC:g}s 是优先目标，不是必须达到的值**。\n"
                f"**合法修法：重新安排表达与镜头**——合并同地点/同主体/连续动作的重复镜头，"
                f"删纯重复反应与无信息过渡，按实际发声和动作需要重新分镜。\n"
                f"禁止：按比例压缩所有镜头、加速朗读、截断声音或动作、删掉关键剧情"
                f"（开篇冲突/刺杀/高潮/收尾）、致命伤、武器归属或台词原文依据。\n"
            )
        # v6.33（08-12）：附加 checklist 聚合反馈——镜头数收敛之外，把内容密度/景别
        # 跳跃/致命伤等真实缺陷也喂给 LLM（此前只喂镜头数/覆盖率，LLM 重生成还是老问题）
        _checklist_fb = _mrt.build_regen_feedback(script, novel_text)
        if _checklist_fb:
            feedback = feedback + _checklist_fb
        # v6.68: prompt/system 跟着实际模型走（接力模型可能是另一家模型）
        _rl_builder, _rl_system = _mrt.resolve_model_prompt_profile(model)
        # v6.131（2026-09-25 收口）：超限收敛也是**完整稿生成入口**，必须同样带上大纲锚点
        # ——否则它会绕开计划（选用的逐字台词/必保留事实/事件顺序）另写一版，等于在收敛
        # 这一步重新决定选材。此处此前只传 novel_text/bank/objects_bank（漏了大纲锚点）。
        prompt = _rl_builder(novel_text, bank, objects_bank, outline_anchor)
        prompt = prompt + feedback
        # 契约**最后**追加（用户 2026-09-25）：反馈之后才是模型最后一眼看到的内容
        prompt = _mrt.append_execution_contract(prompt, contract_block)

        _mrt._metrics_inc("calls.regen_over_limit")
        service = _mrt.create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        req = _mrt.TextGenerationRequest(
            system_prompt=_rl_system,
            user_prompt=prompt,
            json_schema=SCRIPT_SCHEMA,
            model=model,  # v6.67 接力修复：None=provider 默认（零行为变化）
            timeout_sec=_mrt._repair_timeout_sec(),  # v6.70.3: 超限收敛 180s
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _mrt._logger.warn(f"[WARN] 收敛重生成失败: {e}，保留原版")
            break
        if not result.success:
            _mrt._logger.warn(f"[WARN] 收敛重生成失败: {result.error}，保留原版")
            break

        import re
        raw = result.text.strip()
        raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

        try:
            new_script = _mrt.parse_response(raw, novel_text=novel_text,
                                             plan=plan or script.get("chapter_outline"))
        except (ValueError, json.JSONDecodeError) as e:
            _mrt._logger.warn(f"[WARN] 收敛重生成 JSON 解析失败，保留原版: {e}")
            break

        new_shots = new_script.get("shots", [])
        if not new_shots:
            _mrt._logger.warn("[WARN] 收敛重生成返回空 shots，保留原版")
            break

        # v6.86（**用户决定 B2**，2026-09-21）：采纳判据——**严格改善 + 内容完整性**。
        #
        # 原实现是 `script = new_script`：解析成功即**无条件替换**，没有任何质量判据。
        # 也就是说一次坏重生成不会被拒——而这是时长维度**唯一**的一道环节
        # （候选选择有排序、结构轮有采纳判据，唯独这里没有）。本轮实测：触发时
        # 投影 103.0s、最终投影 111.38s，路径结束时仍超限且更差，无环节能拦住。
        #
        # 判据：
        #   1. **严格改善**：实际正片时长必须比原稿更短（容差 0.01s，对齐定点）；
        #   2. **内容完整性**：不得引入完整性异常（text_integrity / 状态回退 /
        #      姿态互斥 / 时长预算等硬正确性问题，见 `_candidate_quality_summary`
        #      的 integrity 档）。
        # 两者任一不满足 → 保留原稿并结束（不做「再多试一次看看」）。
        _new_total = _actual_shots_total(new_script)
        if _new_total >= shots_total - 0.01:
            _mrt._logger.warn(
                f"[DURATION] 重生成未改善实际时长（{shots_total:.1f}s → "
                f"{_new_total:.1f}s），保留原版"
            )
            break
        # v6.98（审核 P0-1）：走**统一候选验收入口**（完整性不退化 + 内容不退化）。
        # v6.87 的判据是「**不退化**」而不是「必须干净」——原稿不干净时布尔判据会让
        # 采纳分支永不通过，时长收敛轮等于没有。
        _ok, _why = _accept_regen_candidate(script, new_script, novel_text, plan=plan)
        if not _ok:
            _mrt._logger.warn(
                f"[DURATION] 重生成缩短了时长（{shots_total:.1f}s → {_new_total:.1f}s）"
                f"但未通过不退化验收，保留原版：{_why}"
            )
            break

        # 收敛有效：替换并重算时间轴
        _mrt._logger.info(
            f"[DURATION] 实际时长改善：{shots_total:.1f}s → {_new_total:.1f}s，采纳新稿"
        )
        script = new_script
        _mrt._compute_timecodes(script)
        total_duration = float(script.get("total_duration", 0) or 0)
        shots_total = _new_total
        duration_over = shots_total > _mrt.DURATION_MAX_SEC + 0.01
        if over_max:
            if len(new_shots) <= SHOT_COUNT_MAX:
                _mrt._logger.info(
                    f"[COUNT] 收敛成功: {current} → {len(new_shots)} 镜, "
                    f"总时长 {total_duration:.0f}s"
                )
            over_max = len(new_shots) > SHOT_COUNT_MAX
        if not duration_over:
            _mrt._logger.info(
                f"[DURATION] 时长收敛成功: 正片 {shots_total:.1f}s（目标 {_mrt.DURATION_TARGET_MIN_SEC:g}-{_mrt.DURATION_TARGET_MAX_SEC:g}s）"
            )

    if over_max:
        # v6.40 08-22：regen 全部失败后不再"保留原版"——按 v6.38 红果硬约束**强制截断**
        # 到 SHOT_COUNT_MAX 镜。LLM 软话不收敛时这是最后兜底，优先保留前 N 镜（开篇+冲突+高潮）
        # 截断后再跑一次 _compute_timecodes 校正 timecode。
        from shared.config import SHOT_COUNT_MAX
        _mrt._console(
            f"  [COUNT] 重试 {max_attempts} 次后仍 {len(script.get('shots', []))} 镜，"
            f"按 v6.38 红果硬约束强制截断到 {SHOT_COUNT_MAX} 镜"
        )
        _mrt._logger.warn(
            f"[COUNT] 强制截断: {len(script.get('shots', []))} → {SHOT_COUNT_MAX} 镜（v6.38 兜底）"
        )
        shots = script.get("shots", [])
        script["shots"] = shots[:SHOT_COUNT_MAX]
        # 同步处理 hook：若 hook 之前被算入 shots 之外的，截断不影响 hook
        _mrt._compute_timecodes(script)
    return script


def _structural_shot_issues(script: dict) -> list:
    """结构收敛轮的目标集合：内容需求超 cap / 时长低于下限 / **开篇空镜过多**.

    v6.101（2026-09-23）：空镜问题也进这里。两个空镜约束 v6.99 起是 report-only
    （用户决定 B：代码不再删镜头），审核与用户都要求把删除诉求**交结构 regen**——
    但当时没有落地：收敛轮只读 `_shot_duration_issues`，看不见空镜问题。
    现在把 `opening_empty_shot_issues()`（advisory）纳入目标集合，
    于是「重做这几镜」会和超载/过短一样被点名、并受统一候选验收约束。

    ⚠️ **缺帧（`kf_segment_missing`）不在此集合**（2026-09-28 复核决定，撤回 v6.143 P3）：
    十次跑实测 11 次「缺帧触发整篇结构重生成」**0 次被采纳**（每次 3.3–4.3 分钟，
    并把 7/10 次跑顶过 900s 总预算、截断仍在收敛的定向修复轮）。缺帧的收敛通道是
    **质量门 `kf_segment_missing` + 定向修复轮**（实测 run 03 一轮 `resolved=6` 且
    `kf_structural_violations=[]` 被正常采纳）——整篇重写这条杠杆对它不成立：
    结构轮是「重做 shot-plan」，而候选验收要求「零新 blocking 身份」，整篇重写
    必然改动切分与内容 → 结构性不可采纳。
    """
    from step01_script_gen.quality_issues import (
        STRUCTURAL_SHOT_CODES, _shot_duration_issues, opening_empty_shot_issues,
    )
    out = [i for i in _shot_duration_issues(script)
           if i.code in STRUCTURAL_SHOT_CODES]
    out.extend(opening_empty_shot_issues(script))
    return out

def _structural_feedback(issues: list) -> str:
    """把结构问题写成**可执行的重做 shot-plan 指令**."""
    lines = []
    for i in issues:
        lines.append(f"  - 镜头{i.shot_id}: {i.message}")
    return (
        "\n\n# ★ 单镜承载能力硬约束（本轮拒收，最高优先级）\n"
        "上一稿有下列镜头在**结构上**不合法——这不是字段级问题，改文本没用：\n"
        + "\n".join(lines) + "\n\n"
        "**本轮必须重做 shot-plan（重新规划镜头切分），不是在已有镜头上做字段拼接**：\n"
        "1. **内容需求超单镜上限**：把该段剧情拆成**多个完整镜头**——每个子镜头自带\n"
        "   narration_segment / motion_desc / ff_desc / lf_desc / keyframes / frame_layouts\n"
        "   与自己的 duration_sec；台词按原文**连续切分**，禁止改写、禁止增删字，\n"
        f"   每个子镜的台词估时都必须 ≤ {_shot_cap():.0f}s（留余量，目标 4-7s）。\n"
        "2. **时长低于下限**：把该镜合并进相邻的同地点连续剧情，或补足动作/台词——\n"
        "   禁止靠事后加秒数凑时长。\n"
        f"3. 单镜 duration_sec 必须落在 3.5-{_shot_cap():.0f}s；正片合计 "
        f"{_mrt.DURATION_MIN_SEC:g}-{_mrt.DURATION_MAX_SEC:g}s（钩子另算；"
        f"{_mrt.DURATION_TARGET_MIN_SEC:g}-{_mrt.DURATION_TARGET_MAX_SEC:g}s 只是优先）。\n"
        "4. 禁止复制字段制造 ff==lf 的空壳子镜；每个子镜都要有独立的事件与画面。\n"
        "5. **开篇空镜过多**：抖音黄金 3 秒必须有主体入场——把多余的开篇空镜"
        "**合并或替换成带主体的建立镜**（人物入画、手持道具、明确动作），"
        "**不是**让你删镜头；每个替代镜仍要自带全部字段。\n"
    )


# ===========================================================================
# v6.89（发现 14 选项 2）：**单镜定向拆分**
#
# 为什么需要它：单镜内容需求 > cap（实测 9.8 / 12.5 / 14.7 / 20.2s）是唯一一条
# 没有任何路径能消掉的 blocking。prompt 侧的「单条台词也可以切开」显式许可
# **已被证伪**（2026-09-22 07:11：三个候选行为逐字不变），所以改为对**单个**
# 被点名的超载镜头做一次定向拆分请求。
#
# 与当年被禁止的「复制字段拆镜」的区别：那时是**代码复制**父镜字段造子镜，会产出
# ff==lf 的空壳；这里是**模型重新生成**每个子镜的完整对象。`SPLIT_SHOT_SCHEMA`
# 直接复用 `_SHOT`（全部字段必填），子镜在 schema 层就不可能是空壳。
# ===========================================================================
_STRUCT_SPLIT_MAX_SHOTS = 6      # 每次运行最多为几个超载镜头发拆分请求
# v6.89 实测（09-22 07:11 脚本）：一次运行典型有 **5 个**超载镜头（8/10/11/13/14）。
# 上限若只有 3，剩下 2 个必然残留为 hard，且「拆分是否有效」也无从判断——
# 拆一次是 ~2-4k token 的小调用，6 次仍远低于整脚本 regen 的 26k。

_NARRATION_STRIP_PUNCT = "，。！？；：、—,!?:;“”'（）()"


def _narration_body(narration: str) -> str:
    """旁白/台词的**可比对正文**：剥「说话人道：」前缀 + 省略号折叠 + 去标点.

    与 `validation._check_grounding` 的归一化同口径（那边逐字比对**原文**，这里
    校验「切开后拼回来是否逐字相等」）。一致性由回归锁定，见
    `tests/step01_script_gen/test_scoped_shot_split.py`。
    """
    text = str(narration or "")
    if not text:
        return ""
    # v6.138（九审 P1-3）：与台词门/核验层同一说话人前缀判据（单一事实源）——
    # 冒号前段含子句标点（"且慢，我告诉你："）不是前缀，不得从比对正文里丢掉
    from step01_script_gen.plan_feasibility import _split_speaker_prefix as _ssp
    body = _ssp(text)[1]
    body = body.replace(" ", "")
    body = re.sub(r"\.{2,}|…{1,}", "§", body)
    for ch in _NARRATION_STRIP_PUNCT:
        body = body.replace(ch, "")
    return body.replace("§", "…")


def _narration_continuous(parent: str, subs: list) -> tuple:
    """子镜台词拼回来与父镜**逐字相等**？返回 (是否通过, 原因)."""
    target = _narration_body(parent)
    if not target:
        return True, ""
    joined = "".join(_narration_body(s.get("narration_segment")) for s in subs)
    if joined == target:
        return True, ""
    if len(joined) != len(target):
        return False, "台词非逐字连续（原文 %d 字 → 拼回 %d 字）" % (len(target), len(joined))
    return False, "台词顺序或文字与原文不一致"


def _shot_cap() -> float:
    """单镜硬上限 = `min(视频模型能力, 项目上限)`——委托给唯一源（v6.97）."""
    from step01_script_gen.timecode import active_shot_cap

    return active_shot_cap()


def _content_need(shot: dict) -> float:
    """单镜内容最低需求（与质量门 `shot_content_over_cap` 同公式）.

    只用于**给子镜分配时长**；「这个拆分能不能接受」的最终判据走质量门本体
    （`_split_within_cap`），两者一致性由回归锁定。
    """
    from step01_script_gen.timecode import (
        MIN_SHOT_DURATION, NARRATION_FIXED_DURATION, _estimate_duration,
        _extract_motion_duration,
    )
    from shared.dialogue import has_dialogue

    narration = str(shot.get("narration_segment") or "")
    motion_min = _extract_motion_duration(str(shot.get("motion_desc") or "")) or 0.0
    speech = 0.0
    if narration:
        speech = _estimate_duration(narration) if has_dialogue(narration) \
            else NARRATION_FIXED_DURATION
    return max(motion_min, speech, MIN_SHOT_DURATION)


def _split_within_cap(sub_shots: list, cap: float | None = None) -> tuple:
    """子镜是否全部满足容量与下限——**直接问质量门本体**，不复制判据.

    探针末尾追加一个占位镜，使每个子镜都按「非末镜」口径受 >=3.5s 检查（否则末位
    子镜会绕过下限）。只认结构类 code（`shot_content_over_cap` /
    `shot_duration_below_min`），其余门禁不在此处裁决。
    """
    from step01_script_gen.quality_issues import (
        STRUCTURAL_SHOT_CODES, _shot_duration_issues,
    )

    if cap is None:
        cap = _shot_cap()
    # v6.95（②）：KF 时刻也必须落在单镜上限内——否则时长（= max(内容需求, KF)）
    # 无法在不超 cap 的前提下覆盖它。先于时长赋值检查，直接给出可执行的反馈。
    from step01_script_gen.timecode import max_kf_t_sec as _max_kf

    for s in sub_shots:
        _km = _max_kf(s)
        if _km > 0 and _km > cap + 0.01:
            return False, (
                "子镜(index=%s) 的 KF 时刻 %.2fs 超出单镜上限 %.1fs——"
                "请把关键帧写在镜长之内（或减少帧数）" % (s.get("index"), _km, cap)
            )

    idxs = {s.get("index") for s in sub_shots}
    probe = {"shots": list(sub_shots) + [
        {"index": "__probe__", "duration": 5.0, "duration_sec": 5.0,
         "narration_segment": "", "keyframes": [],
         "frame_layouts": {"ff": [], "lf": [], "kfs": []}},
    ]}
    for i in _shot_duration_issues(probe):
        if i.shot_id not in idxs or i.code not in STRUCTURAL_SHOT_CODES:
            continue
        return False, str(i.message)[:100]
    return True, ""


def _split_has_duplicate_pair(sub_shots: list) -> tuple:
    """子镜之间是否**相邻视觉重复**？返回 (是否通过, 原因).

    v6.89 续（2026-09-22 07:40 实测）：拆分**第一次真实生效**就在最终门带出 1 条
    `adjacent_duplicate_visuals`——「镜头11/12 相邻且视觉重复（相似度 89%）」。
    两条子镜来自同一父镜，模型把同一个动作写了两遍；拆分 prompt 里已经写了
    「禁止把同一个动作在两个子镜里各演一遍」，模型不遵守（与发现 14 同一类：
    **prompt 说了、模型不做**）。所以做成**确定性采纳判据**。

    判据复用全仓唯一判定 `find_adjacent_duplicate_pairs`（阈值与条件都不复制）。
    注意它**会跳过拆镜子镜**（半整数 index / `_split_from`），而子镜此刻正是
    半整数——所以先用临时整数 index 复制一份再判定。
    """
    from step01_script_gen.constraints.duplicate_shot_merge import (
        find_adjacent_duplicate_pairs,
    )

    probe = []
    for k, s in enumerate(sub_shots):
        c = dict(s)
        c["index"] = k + 1          # 临时整数，让判定器不再当它是拆镜子镜
        c.pop("_split_from", None)
        c.pop("_split_to", None)
        probe.append(c)
    for i, j, sim in find_adjacent_duplicate_pairs(probe):
        return False, (
            "子镜%d/%d 相邻视觉重复（相似度 %.0f%%）——同一个动作被拆成两镜各演一遍"
            % (i + 1, j + 1, sim * 100)
        )
    return True, ""


def _deterministic_narration_cuts(narr: str, cap: float, max_parts: int = 4):
    """按**句末合法切点**把一条台词确定性地切成 N 段，使**每段估时 ≤ cap**（B：统一判据）.

    2026-09-26 用户决策 A+B：切点判据与台词完整性门/规划层可表示性检查**单源**
    （`plan_feasibility.find_legal_split`，见其 docstring）——只认句末边界
    （。！？…；）；**逗号/顿号切点与等字长兜底废除**：句中碎片正是终稿台词门
    `cross_shot_split` 判破损、审计报"同一句被截成两半"的形态，"拆分成功、终稿
    失败"的直接机制（09-26 三次实跑复现）。无合法切法 → None——调用方
    `_scoped_split_one` 据此在**调用拆分模型之前**停下（原 v6.92 的"标点不足
    交整脚本 regen"路径收窄为"无句末合法切点"）。

    v6.92 历史注（仍然成立）：切点不交给模型——模型切分实测采纳率 1/4-2/4；
    本函数保持确定性纯函数，只是合法切点集合收窄为句末。
    """
    from step01_script_gen.plan_feasibility import find_legal_split
    return find_legal_split(narr, cap, max_parts=max_parts)


def _split_source_window(shot: dict, novel_text: str, span: int = 400) -> str:
    """该镜台词在原文中的位置 ±span 字（供模型挑切点参照）；定位不到返回空串."""
    body = _narration_body(shot.get("narration_segment"))
    novel = str(novel_text or "")
    if not body or not novel:
        return ""
    pos = novel.find(body[:8])
    if pos < 0:
        return ""
    return novel[max(0, pos - span):pos + len(body) + span]


def _build_split_prompt(shot: dict, novel_text: str, cap: float,
                        segments: list, feedback: str = "") -> str:
    """单镜定向拆分的用户 prompt（分段由 `_deterministic_narration_cuts` 给定）."""
    import json as _json

    need = _content_need(shot)
    idx = shot.get("index")
    n_parts = len(segments)
    clean = {k: v for k, v in shot.items() if not str(k).startswith("_")}
    window = _split_source_window(shot, novel_text)
    parts = [
        "# ★ 单镜定向拆分（结构必需，不是改写台词）\n\n",
        "下面这**一个**镜头内容装不下：按中文字速 ≈4 字/秒估，它的台词需要 "
        "**%.1fs**，而单镜硬上限是 **%.1fs**。整条留在一镜 = 整稿拒收，"
        "所以必须把它拆成 **2-3 个完整子镜**。\n\n" % (need, cap),
        "## 硬要求\n",
        "1. **台词分段已经替你切好了，逐字照用，不许改动**。见下方"
        "「必须使用的台词分段」——第 k 个子镜的 `narration_segment` 必须**恰好**是"
        "第 k 段，一个字都不能增删改。\n"
        "   **这不是改写台词**——「台词原句」约束的是文字不得改写/增删，"
        "**不是**「一句引号只能给一个镜头」；切开后的每一段仍然是原句。\n",
        "2. **每个子镜必须是完整镜头对象**：与首次生成同构的全部字段都要写"
        "（prompt / ff_desc / lf_desc / motion_desc / keyframes / frame_layouts / "
        "duration / core_keywords / narration_segment / subtitle …），"
        "禁止只给变化字段、禁止留空壳、禁止 ff_desc 与 lf_desc 相同。\n",
        "3. **子镜个数必须是 %d 个**（不多不少），每个子镜对应下方的一段台词；"
        "每段的估时已由切分保证 ≤ %.1fs。\n" % (n_parts, cap),
        "4. **保持前后连贯**：scene / location / 人物 / 服装 / 道具 / 伤处锚点 / 朝向与"
        "站位事实全部不变；第 1 个子镜接着**上一个镜头**的尾帧状态，最后一个子镜结束在"
        "原本这一镜的结束状态。\n",
        # v6.94（2026-09-22 21:26 实测）：拆分的**唯一**剩余失败原因是「相邻子镜
        # 视觉重复」（两次都是相似度 100%）——模型给同一句台词的两半写了完全相同的
        # 画面。所以这里必须把「怎么才算不同」讲具体。
        "5. **每个子镜必须换机位或景别**（这是硬要求，不是风格建议）：台词被拆成多镜后"
        "**说话人仍在说话**，所以他必须**保持在画面内**（不能切到听者反应镜——"
        "视频模型会没有嘴型对象）；但相邻子镜必须换一个不同的机位/景别，例如"
        "「子镜1 近景正面 → 子镜2 中景侧后 / 过肩」「子镜1 平视 → 子镜2 俯拍」。\n"
        "   相邻子镜的 `motion_desc` / `prompt` / `lf_desc` 会被算**动作文本相似度**，"
        "**相似 ≥85% 即判相邻视觉重复并拒绝整条拆分**——照抄上一子镜的写法必然被拒。\n",
        "\n## 必须使用的台词分段（**逐字照用，不得改动**）\n",
        "\n".join("  子镜%d 的 narration_segment = `%s`" % (k + 1, s)
                   for k, s in enumerate(segments)),
        "\n\n## 被点名的镜头（index=%s，需要 %.1fs）\n```json\n" % (idx, need),
        _json.dumps(clean, ensure_ascii=False, indent=1),
        "\n```\n",
    ]
    if window:
        parts.append("\n## 原文窗口（挑切点参照，切点必须是原文里真实存在的停顿处）\n")
        parts.append(window)
        parts.append("\n")
    if feedback:
        # v6.94（③）：上一次尝试被拒的**具体原因**回喂——第九次实测 prompt 里的
        # 「必须换机位/景别」被模型忽略，但把「你的子镜1/2 相似度 98%」摆出来
        # 是更具体的信号（与「反馈格式教了模型一个错误写法」是同一类经验：
        # 泛泛的要求不如**具体到某两个子镜**的失败事实）。
        parts.append(
            "\n## ⚠️ 上一次尝试被拒，必须修正\n"
            "上一次你给出的子镜被判为**相邻视觉重复**：%s\n"
            "本次必须让相邻子镜的 **motion_desc / prompt / lf_desc 互相不同**：\n"
            "  · 换**机位或景别**（例：子镜1 近景正面 → 子镜2 中景侧后 / 过肩；"
            "或平视 → 俯拍）；\n"
            "  · 说话人**仍要留在画面内**（台词在继续，不能切到听者反应镜）；\n"
            "  · 相邻子镜的动作文本相似度必须 **< 85%%**。\n" % feedback
        )
    parts.append('\n只输出 JSON：{"sub_shots": [ …**恰好 %d 个**完整镜头对象… ]}\n' % n_parts)
    return "".join(parts)


_SPLIT_MAX_ATTEMPTS = 2      # 首次 + 一次带反馈重试（仅针对「视觉重复」这类写歪）


def _scoped_split_one(shot: dict, novel_text: str, *, cap: float, fps: float,
                      model=None):
    """为一个超载镜头请求定向拆分；返回子镜列表，或 None（失败/被拒）.

    被拒的每一种原因都打日志——只有「为什么拒」可见，这条路径才可运维。

    v6.94（③）：**视觉重复**被拒时带反馈**重试一次**。prompt 里已写明「相邻子镜必须
    换机位/景别、说话人保持在画面内」，模型仍照抄（第九次实测相似度 98%）——与发现 14
    同型。只对这一类重试；结构类失败（段数不符 / 台词不逐字）是没照**给定输入**做，
    重试同样不划算。
    """
    import json as _json

    from step01_script_gen.constraints.short_shot_merge import MIN_SHOT_DURATION_SEC
    from step01_script_gen.timecode import _align_frame_duration
    from _text_gen.deepseek_schema import SPLIT_SHOT_SCHEMA

    idx = shot.get("index")
    # v6.92：**切点由代码确定性计算**，模型只撰写视觉字段。
    segments = _deterministic_narration_cuts(shot.get("narration_segment"), cap)
    if not segments:
        # v6.136 B：无**句末合法切点**（逗号/等字长兜底已废除，与台词完整性门同判据）
        # ——在调用拆分模型之前停下，交整脚本 regen / 规划层处理
        _mrt._logger.warn(
            "[SPLIT] 镜头%s 无句末合法切点（单句超长且句内只有逗号）——"
            "不调用拆分模型，交整脚本 regen／规划层重选"
            % idx
        )
        return None

    try:
        base = float(shot.get("index"))
    except (TypeError, ValueError):
        base = None

    _sp_builder, _sp_system = _mrt.resolve_model_prompt_profile(model)
    service = _mrt.create_service()
    feedback = ""

    for attempt in range(1, _SPLIT_MAX_ATTEMPTS + 1):
        _mrt._metrics_inc("calls.split_shot")
        prompt = _build_split_prompt(shot, novel_text, cap, segments,
                                     feedback=feedback)
        req = _mrt.TextGenerationRequest(
            system_prompt=_sp_system,
            user_prompt=prompt,
            json_schema=SPLIT_SHOT_SCHEMA,
            model=model,
            timeout_sec=_mrt._repair_timeout_sec(),
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _mrt._logger.warn("[SPLIT] 镜头%s 拆分请求失败: %s" % (idx, e))
            return None
        if not result.success:
            _mrt._logger.warn("[SPLIT] 镜头%s 拆分请求失败: %s" % (idx, result.error))
            return None

        raw = result.text.strip()
        raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        try:
            data = _json.loads(raw)
        except Exception as e:
            _mrt._logger.warn("[SPLIT] 镜头%s 拆分 JSON 解析失败: %s" % (idx, e))
            return None
        subs = data.get("sub_shots") if isinstance(data, dict) else None
        if not isinstance(subs, list) or not all(isinstance(s, dict) for s in subs):
            _mrt._logger.warn("[SPLIT] 镜头%s 未返回 sub_shots 数组" % idx)
            return None
        if len(subs) != len(segments):
            _mrt._logger.warn(
                "[SPLIT] 镜头%s 子镜数 %d 与指定段数 %d 不一致"
                % (idx, len(subs), len(segments))
            )
            return None

        ok, why = _narration_continuous(shot.get("narration_segment"), subs)
        if not ok:
            _mrt._logger.warn("[SPLIT] 镜头%s 拆分被拒：%s" % (idx, why))
            return None
        # v6.92：逐段逐字校验——切点是**我们给的**，所以这里能做精确比对：
        # 「补字 / 只回一段 / 段落错位」全部在此挡住。
        _bad_seg = None
        for k, (sub, seg) in enumerate(zip(subs, segments), 1):
            if _narration_body(sub.get("narration_segment")) != _narration_body(seg):
                _bad_seg = (
                    "[SPLIT] 镜头%s 拆分被拒：子镜%d 的台词与指定分段不一致"
                    "（指定 %d 字 → 实得 %d 字）"
                    % (idx, k, len(_narration_body(seg)),
                       len(_narration_body(sub.get("narration_segment"))))
                )
                break
        if _bad_seg:
            _mrt._logger.warn(_bad_seg)
            return None

        # 子镜时长由**内容需求**派生（不照抄模型给的 duration）：容器必须装得下内容。
        # 对齐到整数帧，下限 3.5s（非末镜口径）。
        from step01_script_gen.timecode import max_kf_t_sec as _max_kf
        _fps = float(fps or 24)
        out = []
        for k, sub in enumerate(subs):
            if base is not None:
                sub["index"] = round(base + (k + 1) / (len(subs) + 1), 3)
            # v6.138：事件履约声明随拆分继承——子镜承载与父镜相同的节拍/台词（台词
            # 已按段逐字校验，B/D 序列非降由「同值重复」构造成立，覆盖不变）
            if isinstance(shot.get("plan_refs"), dict):
                import copy as _copy_mod
                sub["plan_refs"] = _copy_mod.deepcopy(shot["plan_refs"])
            # v6.95（②）：时长由**内容需求**派生，但**必须同时覆盖该子镜自己的最大
            # KF 时刻**——帧写在哪个时刻，镜头就得长到能拍到它（同发现 18 的钩子）。
            # 只按内容需求派生的后果：模型写的 KF 落在镜长之外 → `kf_time_out_of_shot`。
            _kf_max = _max_kf(sub)
            dur = min(max(_content_need(sub), MIN_SHOT_DURATION_SEC, _kf_max), cap)
            dur = max(_align_frame_duration(dur, _fps), MIN_SHOT_DURATION_SEC)
            if _kf_max and dur < _kf_max:
                # 帧对齐可能取整到**略小于** KF 时刻（发现 18 同类）→ 覆盖优先
                dur = round(dur + 1.0 / _fps, 6)
            sub["duration"] = dur
            sub["duration_sec"] = dur
            out.append(sub)

        ok, why = _split_within_cap(out, cap)
        if not ok:
            # v6.95（②）：容量/KF 覆盖不合格也是**可修**的一类（模型写歪），带反馈重试
            if attempt < _SPLIT_MAX_ATTEMPTS:
                _mrt._logger.warn("[SPLIT] 镜头%s 子镜仍不合法，带反馈重试（%d/%d）：%s"
                             % (idx, attempt + 1, _SPLIT_MAX_ATTEMPTS, why))
                feedback = why
                continue
            _mrt._logger.warn("[SPLIT] 镜头%s 拆分被拒（重试后仍不合法）：%s" % (idx, why))
            return None

        ok, why = _split_has_duplicate_pair(out)
        if ok:
            if attempt > 1:
                _mrt._logger.info("[SPLIT] 镜头%s 带反馈重试后通过（机位/景别已区分）" % idx)
            return out
        if attempt < _SPLIT_MAX_ATTEMPTS:
            _mrt._logger.warn(
                "[SPLIT] 镜头%s 视觉重复，带反馈重试（%d/%d）：%s"
                % (idx, attempt + 1, _SPLIT_MAX_ATTEMPTS, why)
            )
            feedback = why
            continue
        _mrt._logger.warn("[SPLIT] 镜头%s 拆分被拒（重试后仍重复）：%s" % (idx, why))
        return None
    return None


def generate_shot_design_result(novel_text: str, plan, *, script: dict, issues: list,
                                bank=None, objects_bank=None, model=None,
                                deadline_monotonic=None) -> dict:
    """结构修订路径的**按需镜头设计**（S30）：只重做被点名的超载镜头.

    只在结构修订路径使用；普通章节不增加这次调用。输出沿用完整镜头字段（由既有
    `_scoped_split_one` 生成），并给出**明确的前后映射**。本函数不做以下事情：

    - 不让 `temporal_plan` 驱动自动合镜；
    - 不按相似度猜引用（子镜的 `plan_refs` 由父镜显式继承）；
    - 不为超过 120s 的整稿做比例压缩（那走整稿重规划）。

    Returns:
        `{"ok", "replacements": [{"parent_index", "children", "position"}],
          "problems", "calls", "changed"}`；失败时 `ok=False`，调用方保留基线。
    """
    from step01_script_gen.quality_issues import STRUCTURAL_SHOT_CODES
    if not isinstance(script, dict):
        return {"ok": False, "changed": False, "replacements": [],
                "problems": [{"code": "shot_design_script_missing",
                              "reason": "缺少完整分镜，无法做镜头设计"}], "calls": 0}
    over = [i for i in (issues or [])
            if str(getattr(i, "code", "")) == "shot_content_over_cap"
            and str(getattr(i, "code", "")) in STRUCTURAL_SHOT_CODES]
    if not over:
        return {"ok": True, "changed": False, "replacements": [], "problems": [], "calls": 0}
    cap = _shot_cap()
    fps = script.get("fps", 24)
    shots = script.get("shots") or []
    replacements, problems = [], []
    done = 0
    for issue in over:
        if done >= _STRUCT_SPLIT_MAX_SHOTS:
            problems.append({"code": "shot_design_budget_exhausted",
                             "shot": str(issue.shot_id),
                             "reason": "单轮定向拆分上限 %d" % _STRUCT_SPLIT_MAX_SHOTS})
            break
        pos = next((n for n, s in enumerate(shots)
                    if isinstance(s, dict) and str(s.get("index")) == str(issue.shot_id)),
                   None)
        if pos is None:
            problems.append({"code": "shot_design_parent_missing",
                             "shot": str(issue.shot_id), "reason": "点名镜头不在稿件里"})
            continue
        parent = shots[pos]
        _mrt._logger.info("[DESIGN] 镜头%s 需要 %.1fs > %.1fs，请求重做该镜镜头设计…"
                          % (issue.shot_id, _content_need(parent), cap))
        subs = _scoped_split_one(parent, novel_text, cap=cap, fps=fps, model=model)
        if not subs:
            problems.append({"code": "shot_design_rejected", "shot": str(issue.shot_id),
                             "reason": "子镜未通过容量/连续性/视觉重复判据，保留基线"})
            continue
        mapping = {"parent_index": parent.get("index"),
                   "children": [s.get("index") for s in subs],
                   "position": pos,
                   "plan_refs_inherited": bool(isinstance(parent.get("plan_refs"), dict))}
        replacements.append({"parent_index": parent.get("index"),
                             "position": pos, "children": subs, "mapping": mapping})
        done += 1
    return {"ok": bool(replacements) and not problems, "changed": bool(replacements),
            "replacements": replacements, "problems": problems, "calls": done,
            "deadline_monotonic": deadline_monotonic}


def _scoped_split_over_cap_shots(script: dict, novel_text: str, *, model=None) -> dict:
    """把超载镜头逐个定向拆分（最多 `_STRUCT_SPLIT_MAX_SHOTS` 个）；原地改 `shots`."""
    over = [i for i in _structural_shot_issues(script)
            if i.code == "shot_content_over_cap"]
    if not over:
        return script
    design = generate_shot_design_result(novel_text, script.get("chapter_outline"),
                                         script=script, issues=over, model=model)
    if not design.get("changed"):
        return script
    shots = script.get("shots") or []
    # 从后往前替换，位置不会因前一次插入而漂移；映射由设计结果显式给出。
    for item in sorted(design["replacements"], key=lambda x: x["position"], reverse=True):
        shots[item["position"]:item["position"] + 1] = item["children"]
        _mrt._logger.info(
            "[SPLIT] 镜头%s 已拆为 %d 镜：%s（映射 %s，台词逐字连续 ✓）"
            % (item["parent_index"], len(item["children"]),
               [round(float(s.get("duration") or 0), 2) for s in item["children"]],
               item["mapping"]["children"]))
        _mrt._console("  [拆分] 镜头%s → %d 镜（超载已消）"
                      % (item["parent_index"], len(item["children"])))
    try:
        _mrt.write_timeline_from_durations(script)
    except Exception as e:
        _mrt._logger.warn("[SPLIT] 拆分后 timeline 序列化失败: %s" % e)
    return script


def _regen_structural_over_limit(script: dict, novel_text: str, bank=None,
                                 objects_bank=None, max_attempts: int | None = None,
                                 model: str | None = None,
                                 outline_anchor: str = "",
                                 prop_anchor_block: str = "",
                                 contract_block: str = "",
                                 plan=None) -> dict:
    """**结构收敛轮**：单镜超 cap / 低于下限 → 重做 shot-plan（整脚本重生成）.

    二次审核发现 1（P0）：系统取消了「先复制字段、后拆镜」这一错误做法，却没有补上
    它的替代者——流程仍是「一次生成完整镜头对象 → 发现结构不合法 → 只报告」。
    本次实测镜头 10/11/13 的台词最低需求 13.7 / 14.4 / 20.0s 都超过 8s 上限，镜头 5
    只有 3.0s，**没有任何阶段负责把它们收敛掉**，必然带着 BLOCK 落盘。

    这里补上可执行的收敛路径：把结构问题点名反馈给 LLM，要求**重做 shot-plan**
    （超限段落拆成多个完整子镜；过短镜头合并相邻剧情片段）。采纳判据必须是
    **结构非法数严格变少**——不能用 `_quality_rank` 当门槛（它把内容分与全部
    blocking 计数混进同一元组，会否决「结构确实变少、内容项略多」的稿子，
    实测把 7 → 5 判成「未改善」），唯一附加守卫是不许引入完整性异常。

    Returns:
        收敛后的 script；无结构问题、无预算、或新稿不改善时原样返回。
    """
    from shared.config import GAP_MAX_REGEN_ATTEMPTS

    if max_attempts is None:
        max_attempts = GAP_MAX_REGEN_ATTEMPTS

    # v6.89（发现 14 选项 2）：**先做单镜定向拆分**。
    #
    # 它比整脚本重做 shot-plan 便宜一个数量级（单镜 ~2-4k token vs 全脚本 26k），
    # 且只动被点名的那一镜——已经验证过的其它镜头一个字节都不碰。整脚本 regen
    # 退居兜底（「过短镜头」只能靠它合并，拆不了）。
    script = _scoped_split_over_cap_shots(script, novel_text, model=model)

    for attempt in range(1, 1 + max_attempts):
        issues = _structural_shot_issues(script)
        if not issues:
            return script
        # v6.143 P3 已撤回（2026-09-28 复核）：目标集合回到「超载/过短 + 开篇空镜」，
        # 缺帧不再触发整篇重生成——它走质量门 + 定向修复轮（见 `_structural_shot_issues`）。
        _mrt._logger.warn(
            f"[STRUCT] {len(issues)} 个镜头结构不合法（内容超 cap / 低于下限 / 开篇空镜）："
            f"{[str(i.shot_id) for i in issues]}，重做 shot-plan（尝试 {attempt}/{max_attempts}）"
        )
        _mrt._console(f"  [结构] {len(issues)} 镜超载/过短/空镜，重做分镜规划"
                 f"（{attempt}/{max_attempts}）...")

        _sb_builder, _sb_system = _mrt.resolve_model_prompt_profile(model)
        # v6.116（审核）：重生必须**复用首稿用过的大纲锚点**——否则「花 79 秒重做
        # 整章」却把主要事件/高潮/时间预算全丢了，等于换个随机种子重来。
        prompt = _sb_builder(novel_text, bank, objects_bank, outline_anchor,
                             prop_anchor_block=prop_anchor_block)
        prompt = prompt + _structural_feedback(issues)
        try:
            _checklist_fb = _mrt.build_regen_feedback(script, novel_text)
            if _checklist_fb:
                prompt += _checklist_fb
        except Exception:
            pass
        # 契约**最后**追加（用户 2026-09-25）：所有反馈之后
        prompt = _mrt.append_execution_contract(prompt, contract_block)

        _mrt._metrics_inc("calls.regen_structural")
        service = _mrt.create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        # v6.101（2026-09-23 第十四次重跑实测）：结构收敛轮是**全脚本级**请求
        # （输入整章原文 + 反馈，输出 ~26k token），却一直用**修复轮**的 180s 超时。
        # 第十四次实测直接 `Agent Plan 客户端超时 (180s)` → 连候选都没拿到，
        # 「2 个超载镜重做 shot-plan」这条路整轮作废（第七次重跑时它能产出 → 属延迟波动）。
        # 改走与 GAP 重生同档的 `_full_script_timeout_sec()`（默认 300s）。
        req = _mrt.TextGenerationRequest(
            system_prompt=_sb_system,
            user_prompt=prompt,
            json_schema=SCRIPT_SCHEMA,
            model=model,
            timeout_sec=_mrt._full_script_timeout_sec(),
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _mrt._logger.warn(f"[WARN] 结构收敛重生成失败: {e}，保留原版")
            return script
        if not result.success:
            _mrt._logger.warn(f"[WARN] 结构收敛重生成失败: {result.error}，保留原版")
            return script

        import re as _sb_re
        raw = result.text.strip()
        raw = _sb_re.sub(r"^```json\s*", "", raw, flags=_sb_re.MULTILINE)
        raw = _sb_re.sub(r"^```\s*", "", raw, flags=_sb_re.MULTILINE)
        raw = _sb_re.sub(r"```\s*$", "", raw, flags=_sb_re.MULTILINE)
        try:
            new_script = _mrt.parse_response(raw, novel_text=novel_text,
                                             plan=plan or script.get("chapter_outline"))
        except (ValueError, json.JSONDecodeError) as e:
            _mrt._logger.warn(f"[WARN] 结构收敛 JSON 解析失败，保留原版: {e}")
            return script
        if not new_script.get("shots"):
            _mrt._logger.warn("[WARN] 结构收敛返回空 shots，保留原版")
            return script

        _mrt._compute_timecodes(new_script)
        new_issues = _structural_shot_issues(new_script)

        # 采纳判据：**结构非法数严格变少**由本轮的收敛目标主导。
        #
        # 原判据是「结构变少 **且** `_quality_rank` 不劣化」。实测（2026-09-21
        # 22:20:04）一份把非法镜头 **7 → 5** 的新稿被判成「未改善」丢弃：
        # `_quality_rank` 把内容分与**全部** blocking 计数混进同一个元组，新稿
        # 结构更好但内容项略多，元组就变大。结构轮存在的唯一目的就是收敛结构，
        # 判据却不由结构主导——等于没有收敛轮，本轮真实运行因此带着 7 个非法
        # 镜头落盘（`shot_content_over_cap` × 5 直接进 hard）。
        #
        # 现在：结构严格变少即采纳，唯一附加守卫是**不得引入完整性异常**
        # （text_integrity / 状态回退 / 姿态互斥 / 时长预算等硬正确性问题，
        # 见 `_candidate_quality_summary` 的 integrity 档）——那是内容崩坏，
        # 与「内容分低一点」不是一回事；内容项由随后的定向修复轮处理。
        # v6.98（审核 P0-1）：**目标指标为 0 不再走快速返回**。
        #
        # 原实现把「新稿结构非法数 = 0」当成 `return new_script` 的直接理由，于是
        # 完整性门（以及后来的内容门）**只在 `0 < M < N` 时才跑**——修得最干净的那
        # 一次重生反而拥有最弱的验收：删关键事件、改坏角色状态、引入新阻塞身份，
        # 只要「结构干净」就被采纳。目标指标的最佳值 ≠ 其他维度安全。
        #
        # 现在统一为：**先严格改善 → 再过统一验收 → 才提交**；`M = 0` 只是让它
        # 不需要再迭代（提交后直接结束），而不是绕过验收。
        if len(new_issues) >= len(issues):
            _mrt._logger.warn(
                f"[STRUCT] 新稿未改善（{len(issues)} → {len(new_issues)} 个非法镜头），"
                f"保留原版"
            )
            return script
        _ok, _why = _accept_regen_candidate(script, new_script, novel_text, plan=plan)
        if not _ok:
            # v6.87：判据是「**不退化**」，不是「必须干净」——原稿不干净时
            # 布尔判据会让采纳分支永不通过（实测 5 → 4 被拒）。
            _mrt._logger.warn(
                f"[STRUCT] 新稿结构改善（{len(issues)} → {len(new_issues)}）"
                f"但未通过不退化验收，保留原版：{_why}"
            )
            return script
        _mrt._logger.info(
            f"[STRUCT] 结构改善：{len(issues)} → {len(new_issues)} 个非法镜头，采纳新稿"
        )
        script = new_script
        if not new_issues:
            _mrt._logger.info(
                f"[STRUCT] 结构收敛成功：{len(issues)} 个非法镜头 → 0；"
                f"共 {len(script.get('shots', []))} 镜（已通过统一验收）"
            )
            _mrt._console("  [结构] 收敛成功：无超载/过短镜头")
            return script
        continue

    remaining = _structural_shot_issues(script)
    if remaining:
        # 无预算/未收敛：**明确结束为 BLOCK**，不再「再试一次看看」
        _mrt._logger.warn(
            f"[STRUCT] 结构收敛用尽 {max_attempts} 次尝试，仍有 "
            f"{len(remaining)} 个镜头超载/过短——交质量门判 BLOCK，需人工介入"
        )
        _mrt._console(f"  [结构] 收敛失败：仍有 {len(remaining)} 个镜头超载/过短（BLOCK）")
    return script


# 定向修复轮：GAP 通过后仍存在的高价值告警（开篇冲突/爆点密度/威胁源朝向/
# 关键帧质量/内容密度），只把「告警镜头 + 告警文本」发给 LLM 做单镜修复，
# 比全脚本重生成便宜、且不破坏已通过的镜头。P18（08-15）核心闭环。
# 08-15（v6.36）收紧：只留「字段级局部修复」类别——重写被限制在具体字段
# （ff/lf 锚、道具方位、强情绪链、ff/motion 时序），引入新噪音的风险低。
# 剔除：opening_hook/punch_density（全局重构，改前1/3段必引入噪音）、content_density
# （08-15 实测 1→9 重写越改越糟）、threat_gaze_coupling（B2 确定性注入已覆盖）。
# 08-15（PR-2）：keyframes_quality 退出修复轮——聚合告警（「N 个中间关键帧」无镜头号）
# 本就归 global 被过滤，且 post_fix 已确定性注入角色姿态/位置骨架，无需 LLM 修。
# 定向修复轮：GAP 通过后仍存在的高价值告警，只把「告警镜头 + 告警文本」发给 LLM 做单镜修复。
# 08-20（v6.37）扩展：9 → 33 类，覆盖所有字段级可手术修复的检查；结构性/全局/确定性已覆盖类仍排除。
# 排除（合并改写必破坏其它镜头 / 代码已兜底）：
#   - opening_hook / punch_density / content_density / combat_arc（全局或整段）
#   - keyframes_quality / threat_gaze_coupling / combat_arc_coda（确定性已注入）
#   - narrative_arc_distribution / platform_compliance / semantic_risk（结构性/合规另路处理）
_REPAIR_PRIORITY_CATEGORIES = (
    # === 锚点/位置（4） ===
    "ff_desc_anchors", "prop_spatial_anchor", "pose_continuity", "multi_person_staging",
    # === 情绪/表情（3） ===
    "strong_emotion_chain", "restrained_emotion", "emotion_visualization",
    # === 时序/节奏（5） ===
    "ff_motion_timeline", "kf_temporal_consistency", "kf_time_window_alignment",
    # v6.84 续（2026-09-21 实测）：`kf_event_window_mismatch` 是**最大阻塞桶**
    # （18 → 9 条，占本轮 visual_risk 的一半），却既不在本白名单、也不是
    # `STRUCTURAL_SHOT_CODES` → **没有任何收敛路径**，只能一路报告到 BLOCK。
    # 它的 issue 构造本来就带 `shot_id` / `field_path="keyframes.desc"` /
    # `repairable=True`，与同族的 `kf_time_window_alignment` 是同一个字段、
    # 同一类缺陷、同一套修法——一个接线了一个没接，属漏接。
    "kf_event_window_mismatch",
    # 2026-11-06：同镜同武器左右手矛盾——审计能看见、确定性层此前看不见；
    # 新检查给成对 repair_targets（冲突涉及的全部字段），必须进修复轮才有收敛路径。
    "weapon_hand_conflict",
    "shot_transition", "action_overlap",
    # === 景别/运镜（3） ===
    "shot_size_jump", "camera_movement", "camera_upgrade", "focal_switch",
    # === 服装/角色（2） ===
    "character_consistency", "character_entrance",
    # === 对白/口型（2） ===
    "dialogue_length", "voice_punctuation",
    # === 静态/时长（2） ===
    "dwell_time", "prompt_length_budget",
    # === 画面质量（8） ===
    "forbidden_abstract", "lighting_position", "color_tone_transition", "hand_lock",
    "over_shoulder_lock", "occlusion_layering", "gaze_direction", "gaze_convergence",
    # === 画面描述（4） ===
    "ff_lf_completeness", "ff_desc_length", "ff_multi_action", "frame_weapon_legality",
    # weapon_holder 不进修复轮（09-13 决策：无误报观察数据，先跑一章统计命中/误报；
    # issue 分级保留——仍经 feedback.build_regen_feedback 聚合进 GAP/regen 反馈文本。
    # 钩子的无主武器由 main.py「钩子后置校验轮」兜底，不经本清单）
    # === 打斗/致命（1） ===
    # v6.43 08-25：fatal_wound_continuity 移除——hard_fix 阶段
    # InjuryContinuityConstraint 已确定性补 anchor（prompt/lf_desc 追加
    # _INJURY_ANCHOR_TAIL = "伤处白袍心口一片暗红血迹洇开"），剩余 WARN 是
    # hard_fix 修复痕迹的镜像。如果保留进 LLM 修复轮，LLM 会把 hard_fix
    # 加的 anchor 当成"问题"误删（实测 08-25 跑批 LLM 修复 round1 又删了
    # 已补的 fatal_wound_continuity 6 处）。改为由 hard_fix 唯一负责，LLM
    # 修复轮不再过问。
    "combat_action",
    # === 空镜/字幕/合规（2） ===
    "negative_prompt", "subtitle_leak",
    # === 原文锚点 + 高潮优先（2） ===
    "grounding", "climax_priority",
)
# 08-15（v6.36）：2 轮 → 1 轮——实测修复轮越改越糟（38→55），降为单轮 top-6
# 聚焦最严重，杜绝「第2轮全量」把没问题的镜头也重写一遍。
# v6.101（2026-09-23，用户决定「提高修复预算」）：1 → 3。
#
# 第十四次重跑：22 条阻塞分布在 14 个镜头上，而一轮只动 3 镜、只解掉 1 条
# （`before=25 after=25`）。KF 类那 17 条**全在修复轮白名单里**，只是排不进
# 那 3 个名额——真正的瓶颈是预算，不是任何单个检查器。
_REPAIR_MAX_ROUNDS = 3
