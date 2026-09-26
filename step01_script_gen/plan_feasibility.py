# -*- coding: utf-8 -*-
"""生成前可行性 + 原文核验：计划里的**必保留内容**能否装进本集预算（2026-09-25，第二步）.

用户 2026-09-25 的边界（原文摘要）：

> **在生成前选内容、算预算；装不下就停在规划层。**但"计划是唯一来源"应收窄为
> "**计划是本集内容取舍和预算的权威**"——小说原文仍是事实与台词的依据。
> 1. 大纲标明必保留事实及证据、选用的逐字台词、可省略内容和事件顺序；**时长由代码用
>    现有估算器计算，不采信模型自报秒数**。对白与可同时发生的画面动作**不能简单重复相加**。
> 2. 若必保留内容的**估算下界已超过 90 秒**，报告"本集规划不可行／需拆集"，**不生成完整稿**，
>    也不靠加快语速或删必保留事实过关。
> 3. **第二步只接计划与生成前校验**；拆镜／重生仍以初稿为验收基线的问题留给第三步。

2026-09-25 收口（审核结论：第二步尚未闭环）——四处断点与修法：

1. **只验算一次，且在候选扇出之前**。门挂在 `_generate_script_candidates()` 入口，不是每个
   候选各跑一遍。`PlanInfeasibleError` **刻意不继承 `RuntimeError`**：候选循环里的
   `except RuntimeError` 是"生成失败→重试"语义，计划不可行不属于该语义，不能进重试。
2. **缺计划 = 明确停止**（不再是"只有 outline 非空才过门"）。`OUTLINE_ENABLED=0` 或大纲
   提取失败时，生成前无从验算预算 → 显式停止，不带着未验算的计划生成完整稿。
3. **原文核验与预算核算同在一个边界**：选用的逐字台词必须能在原文里整句找到；找不到的
   从计划里剔除（写日志与诊断），剔除后为空 → 不可评估 → 停止（"空台词＋非空节拍"
   不再放行）。
4. 口径更正：本门在**完整分镜生成之前**，不是"任何模型调用之前"——主流程此前已并发
   调用过提取模型（角色/场景物体/大纲）。

## 口径（为什么这么算）

- **时长由代码算**：逐句 `timecode._estimate_duration()` 求和；计划里 `time_budget` 之类的
  **模型自报秒数一律不采信**（它只是概略文字，实测写着"对白约30秒"，而当轮实际对白 91s）。
- **下界 = max(对白估时, 节拍下限)，不做加法**：说话要占时间、节拍也要占画面时间，但两者
  **可以同时发生**（对白本就常发生在动作镜里）。加法值只作诊断（`additive_sum_sec`），
  **不作为判据**。
- 判据是**下界 > 预算上限**：下界已超上限 ⇒ 无论怎么排都装不下。
- **只剔除幻觉台词，不为预算删内容**：原文核验剔除的是"原文里根本没有的台词"（生成阶段
  自己编的）；预算不够走的是 `infeasible` 分支——**不动计划**，只报告。本模块不加速语速、
  不删必保留事实、不产出"精简后的计划"。
"""
from __future__ import annotations

import re

from step01_script_gen.timecode import MIN_SHOT_DURATION, _estimate_duration

# 计划里"可核算"所必需的字段（缺任意一个 → 不可评估 → fail-closed）
_REQUIRED_PLAN_FIELDS = ("dialogue_lines", "shot_beats")

# 原文核验的标点归一化集合（与 `validation._check_grounding` 同族，另含直角引号/书名号）
_PUNCT_CHARS = '，。！？；：、—,!?:;“”"\'（）()「」『』《》〈〉'


def budget_band() -> tuple:
    """本集时长预算区间 `(min, max)` —— 唯一事实源在 `validation.py` 的红果标准."""
    from step01_script_gen.validation import _REDGUO_DURATION_MAX, _REDGUO_DURATION_MIN
    return float(_REDGUO_DURATION_MIN), float(_REDGUO_DURATION_MAX)


class PlanInfeasibleError(Exception):
    """计划在生成前即判定不可行／无法验算（装不进预算 / 缺计划 / 台词无原文依据）.

    **刻意不继承 `RuntimeError`**（用户 2026-09-25 收口）：候选生成的重试循环用
    `except RuntimeError` 表示"生成失败→换个候选/再试一次"，而计划不可行**不是**生成
    失败——它必须直接终止、零次候选生成。继承 `RuntimeError` 会让它被那条分支按
    "重试"语义处理（收口前实测：不可行计划被重试 `SCRIPT_PARSE_RETRIES` 次）。
    """

    #: 供调用方/测试识别"这是规划层结论，不是生成失败"
    is_plan_verdict = True


def plan_dialogue_lines(plan: dict | None) -> list:
    """计划里**选用的逐字台词**（非字符串项一律丢弃，不做任何补全）.

    只接受 `list`：字段被模型写成字符串时（schema 之外的异常输出）逐字符迭代会造出
    几十条假台词——一律当"没有可用台词"处理，由判定层 fail-closed 停止。
    """
    values = (plan or {}).get("dialogue_lines")
    if not isinstance(values, list):
        return []
    out = []
    for x in values:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def dialogue_seconds(lines) -> float:
    """逐句用现存估算器求和——**代码算，不采信计划里的自报秒数**."""
    return round(sum(_estimate_duration(s) for s in lines), 2)


def beats_floor_seconds(plan: dict | None) -> float:
    """节拍的画面下界：每个节拍至少要占 `MIN_SHOT_DURATION` 秒."""
    n = len([b for b in ((plan or {}).get("shot_beats") or []) if str(b or "").strip()])
    return round(n * float(MIN_SHOT_DURATION), 2)


# ---------------------------------------------------------------------------
# 原文核验：选用的台词是不是**逐字**来自原文
# ---------------------------------------------------------------------------
def _source_norm(text) -> str:
    """原文核验归一化：省略号折叠 → 删标点 → 删空白.

    **顺序不可换**——先把 `…/……/..` 折叠成占位符 `§`（普通字符，不会被标点归一化删掉），
    再删标点，最后把 `§` 还原成单字符省略号。反过来的话 `…` 会在删标点阶段就没了
    （与 `validation._check_grounding` 08-17 修掉的那个 bug 同源，这里按同一顺序）。
    """
    s = re.sub(r"\.{2,}|…{1,}", "§", str(text or ""))
    for p in _PUNCT_CHARS:
        s = s.replace(p, "")
    return s.replace("§", "…").replace(" ", "").replace("　", "").replace("\n", "")


def source_contains(novel_text: str, line: str) -> bool:
    """归一化后，这一句能否在原文里**整句**找到."""
    n = _source_norm(line)
    return bool(n) and n in _source_norm(novel_text)


def verify_dialogue_lines(lines, novel_text: str) -> tuple:
    """逐句核验选用台词；返回 `(kept, rejected)`，`rejected` 每项带原因.

    与 `validation._check_grounding` 的差别是**判据强度**：那边是"5 字滑窗命中"（旁白是
    改写过的叙述，不能要求整句逐字），这边是"整句包含"——选用台词按契约就是**逐字摘录**，
    整句找不到即视为幻觉台词。归一化规则同族（见 `_source_norm`），因此省略号/标点变体
    不会误伤。归一化后极短的台词命中会偏松，但代价极低（几个字），不做特殊分支。

    唯一一处宽容（与 `_check_grounding` 同源的理由）：**说话人前缀**（`张十三道：`）是
    生成层按规则0补的，不一定逐字来自原文——整句匹配不上时，再拿冒号后的台词体匹配一次。
    实测（2026-09-25，第 1 章）若不容忍前缀，真实稿件里带前缀的台词会**整批**被判"没有
    原文依据"，门会系统性误停。
    """
    novel_norm = _source_norm(novel_text)
    kept, rejected = [], []
    for s in lines:
        _forms = [s]
        _body = re.split(r"[:：]", s, maxsplit=1)[-1]
        if _body.strip() and _body != s:
            _forms.append(_body)
        if any(_source_norm(f) and _source_norm(f) in novel_norm for f in _forms):
            kept.append(s)
        else:
            rejected.append({"line": s,
                             "reason": "原文中未逐字找到"
                             if _source_norm(s) else "归一化后为空（只有标点/空白）"})
    return kept, rejected


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def evaluate_plan(plan: dict | None, *, novel_text: str = "",
                  budget_min: float | None = None,
                  budget_max: float | None = None) -> dict:
    """给出计划的**可行性判定**（纯函数：不改计划、不产出精简稿）.

    `novel_text` 是原文（事实与台词的依据）；核验与预算都用它，缺原文即不可评估
    （fail-closed）。

    Returns `{"verdict": "feasible"|"infeasible"|"unevaluable", "feasible",
    "evaluable", "missing", "dialogue_lines", "dialogue_sec", "dialogue_verified",
    "dialogue_rejected", "beats", "beats_floor_sec", "lower_bound_sec",
    "additive_sum_sec", "budget", "reasons", "suggestions", "note"}`。
    其中 `dialogue_lines`/`dialogue_sec` 口径是**核验通过**的那一组。
    """
    _bmin, _bmax = budget_band()
    bmin = float(budget_min if budget_min is not None else _bmin)
    bmax = float(budget_max if budget_max is not None else _bmax)

    def _stop(reasons, suggestions, *, missing=None, rejected=None) -> dict:
        return {"verdict": "unevaluable", "feasible": False, "evaluable": False,
                "missing": list(missing or []), "dialogue_lines": 0,
                "dialogue_sec": 0.0, "dialogue_verified": [],
                "dialogue_rejected": list(rejected or []),
                "beats": 0, "beats_floor_sec": 0.0, "lower_bound_sec": None,
                "additive_sum_sec": None, "budget": {"min": bmin, "max": bmax},
                "reasons": reasons, "suggestions": suggestions, "note": ""}

    # ① 计划缺失（大纲提取失败 / OUTLINE_ENABLED=0）——生成前无从验算，必须显式停止
    if not plan:
        return _stop(
            ["本集计划缺失（大纲提取失败或 OUTLINE_ENABLED=0）——生成前无法核验内容取舍与预算"],
            ["重跑规划（或打开 OUTLINE_ENABLED=1）后再生成；计划是内容取舍与预算的权威"],
            missing=list(_REQUIRED_PLAN_FIELDS))

    # ② 字段缺失
    missing = [f for f in _REQUIRED_PLAN_FIELDS if f not in plan]
    if not missing:
        # 字段在但全是空 → 同样不可评估（空清单不能当作"零成本"）
        if not plan_dialogue_lines(plan) and not (plan or {}).get("shot_beats"):
            missing = ["dialogue_lines/shot_beats（都是空）"]
    if missing:
        return _stop(
            ["计划缺少可核算字段 %s——无法在生成前验算（fail-closed）" % missing],
            ["让规划阶段补齐这些字段后重跑；不要带着未验算的计划生成完整稿"],
            missing=missing)

    # ③ 没有原文就无法核验台词依据（原文是事实与台词的依据）
    if not str(novel_text or "").strip():
        return _stop(["缺少原文文本，无法核验选用台词是否逐字来自原文（fail-closed）"],
                     ["带原文重跑；不要带着未核验的台词生成完整稿"])

    # ④ 原文核验：逐字找不到的台词不是"本集选用的内容"
    lines = plan_dialogue_lines(plan)
    kept, rejected = verify_dialogue_lines(lines, novel_text)
    if not kept:
        if lines:
            reasons = ["计划选用的 %d 句台词**全部**未在原文中逐字找到——"
                       "生成阶段会自行决定台词，计划就不是内容取舍的权威" % len(lines)]
        else:
            reasons = ["计划未选用任何逐字台词（`dialogue_lines` 为空）——"
                       "生成阶段会自行决定台词，计划就不是内容取舍的权威"]
        return _stop(reasons,
                     ["回到规划层按原文**逐字**摘出台词（宁少勿滥）后重跑"],
                     rejected=rejected)

    _dlg = dialogue_seconds(kept)
    _floor = beats_floor_seconds(plan)
    lower = round(max(_dlg, _floor), 2)
    reasons, suggestions = [], []
    if rejected:
        reasons.append("选用台词 %d 句未在原文中逐字找到，已从计划中剔除" % len(rejected))
    if lower > bmax:
        reasons.append(
            "必保留内容的估算下界 %.2fs 已超预算上限 %.0fs（对白 %.2fs / %d 句；节拍下限 %.2fs）"
            % (lower, bmax, _dlg, len(kept), _floor))
        reasons.append("下界已超上限 ⇒ 无论怎么排都装不下（不是靠排布/剪辑能救的）")
        suggestions.append("在规划层**重选**：按 `omittable` 换掉次要内容后重新规划")
        suggestions.append("或报告**本集需拆集**（本章内容超出单集预算）")
    elif lower < bmin:
        # 不足下限**不是**"不可行"，也**不能**据此说"内容不够"（用户 2026-09-25）：
        # 下界只说明"至少这么多"，对白与画面动作可同时发生，成片实际时长要等生成稿。
        # 这里只报告读数，判断留给实际时长。
        reasons.append(
            "必保留内容下界 %.2fs 低于下限 %.0fs——下界只说明「至少这么多」，"
            "**尚不能据下界判断成片是否达到下限**（对白与画面动作可同时发生）"
            % (lower, bmin))
        suggestions.append(
            "按生成稿的**实际正片时长**判断：若确实低于下限，再回规划层补内容"
            "（反应/环境/过场）或合并章节")
    return {"verdict": "infeasible" if lower > bmax else "feasible",
            "feasible": lower <= bmax, "evaluable": True, "missing": [],
            "dialogue_lines": len(kept), "dialogue_sec": _dlg,
            "dialogue_verified": kept, "dialogue_rejected": rejected,
            "beats": int(round(_floor / float(MIN_SHOT_DURATION))), "beats_floor_sec": _floor,
            "lower_bound_sec": lower,
            # 只作诊断：两者可同时发生，**不得**用来判可行性（用户 2026-09-25）
            "additive_sum_sec": round(_dlg + _floor, 2),
            "budget": {"min": bmin, "max": bmax},
            "reasons": reasons, "suggestions": suggestions,
            "note": "判据取 max(对白, 节拍下限)，对白与同时发生的画面动作不重复相加"}


def _replan_note(plan) -> tuple:
    """规划层重选记录 → `(说明, 补充建议, 是否**没有判定结论**)`.

    用户 2026-09-25：**"重选没跑成"与"重选后仍超预算"必须分开**。调用/解析/校验失败
    （`result="replan_failed"`）**没有判定结论**，不能据此报告"本集需拆集"；只有
    `result="infeasible"`（真的产出过第二版计划并复核过）才是"仍装不下"的结论。
    第三个返回值同时驱动三件事：撤下"拆集"建议、异常开头不写"不可行／需拆集"、
    诊断写 `conclusion="replan_incomplete"`。
    """
    _r = (plan or {}).get("plan_replan") if isinstance(plan, dict) else None
    if not isinstance(_r, dict):
        return "", "", False
    try:
        from step01_script_gen.outline import FORCE_REPLAN_ENV
    except Exception:
        FORCE_REPLAN_ENV = "STEP01_FORCE_REPLAN"
    if _r.get("result") == "replan_failed":
        return ("规划层重选**未完成**（%s）——这**不是**「本集不可行」的判定，"
                "不能据此报告需拆集" % (_r.get("detail") or "调用/解析失败"),
                "先重跑该轮重选（`%s=1`）或删除大纲缓存后重新规划" % FORCE_REPLAN_ENV,
                True)
    if _r.get("result") == "infeasible":
        return ("规划层已重选 %d 轮后仍超预算（对白 %.2fs → %.2fs）⇒ 需拆集，"
                "或按 `omittable` 重选后再规划"
                % (int(_r.get("rounds") or 1),
                   float(_r.get("before_dialogue_sec") or 0.0),
                   float(_r.get("after_dialogue_sec") or 0.0)), "", False)
    return "", "", False


def _log():
    """复用主流程 logger；独立调用（如单测）时退回标准 logging（不制造 import 环）."""
    try:
        from step01_script_gen.main import _logger as lg
        return lg
    except Exception:
        import logging
        return logging.getLogger("step01.plan")


def _drop_unverified(plan, rep: dict) -> list:
    """把计划里的 `dialogue_lines` 就地收窄为**核验通过**的那一组.

    这是本模块**唯一**改计划的地方，且只删"原文里根本没有的台词"；预算不够走
    `infeasible` 分支、**不动计划**。收窄后生成侧（`outline_to_anchor` 等）只能看到
    核验过的台词——"计划是本集内容取舍的权威"靠这一步落地。
    """
    if not isinstance(plan, dict):
        return []
    raw = plan.get("dialogue_lines")
    if not isinstance(raw, list):
        return []
    kept = list(rep.get("dialogue_verified") or [])
    if raw == kept:
        return []
    plan["dialogue_lines"] = kept
    return list(rep.get("dialogue_rejected") or [])


def check_plan_before_generation(plan: dict | None, *, chapter_dir=None,
                                 novel_text: str = "") -> dict:
    """**生成前唯一入口**（挂于候选扇出之前）：原文核验 + 预算核算，只验算一次.

    可行 → 把 `dialogue_lines` 收窄为核验通过的集合，返回报告（调用方据此记录同一组数：
    对白估时、下界、预算）。不可行/不可评估/缺计划 → 写诊断 `step01_plan_infeasible.json`
    并抛 `PlanInfeasibleError`（**不生成完整稿**）。
    """
    rep = evaluate_plan(plan, novel_text=novel_text)
    # 重选记录（若有）——把"重选没跑成"与"重选后仍超预算"分开写进诊断与报错（用户 2026-09-25）
    _note, _extra_sug, _no_verdict = _replan_note(plan)
    if _note:
        rep["reasons"] = list(rep["reasons"]) + [_note]
    if _no_verdict:
        rep["suggestions"] = [s for s in rep["suggestions"] if "拆集" not in s]
    if _extra_sug:
        rep["suggestions"] = list(rep["suggestions"]) + [_extra_sug]
    _log().info(
        "[PLAN] 对白 %d 句/估时 %.2fs（逐字核验剔除 %d 句）；节拍 %d 个/下限 %.2fs；"
        "下界 %.2fs；预算 %.0f–%.0fs（%s）"
        % (rep["dialogue_lines"], rep["dialogue_sec"], len(rep["dialogue_rejected"]),
           rep["beats"], rep["beats_floor_sec"],
           rep["lower_bound_sec"] if rep["lower_bound_sec"] is not None else -1.0,
           rep["budget"]["min"], rep["budget"]["max"], rep["note"]))
    if rep["verdict"] == "feasible" and rep["reasons"]:
        # 可行但带读数（如"下界低于下限——尚不能据下界判断成片是否达到下限"）：
        # **通过路径也要看得见**（用户 2026-09-25 要的就是这条口径被报出来，
        # 而不是只在停下来时才出现在报错里）。
        _log().info("[PLAN] 注意：%s" % "；".join(rep["reasons"]))
    if rep["dialogue_rejected"]:
        # 准则 7：剔除必须看得见（哪几句、为什么）
        _log().warn("[PLAN] 下列选用台词未在原文中逐字找到，已从计划中剔除"
                    "（不参与预算，也不会进生成提示）："
                    + "；".join("%s（%s）" % (str(d.get("line"))[:40], d.get("reason"))
                                for d in rep["dialogue_rejected"]))
    if rep["evaluable"]:
        _drop_unverified(plan, rep)
    if rep["verdict"] == "infeasible":
        _dump(chapter_dir, rep, plan, no_verdict=_no_verdict)
        # 用户 2026-09-25：**异常开头也必须分开**。`replan_failed`（调用/解析失败）没有判定
        # 结论，开头写"本集规划不可行／需拆集"就是把工具故障误报成内容结论；首版超预算的
        # **数字事实照旧保留**（在 reasons 里）。
        _head = ("规划重选未完成——尚无「本集不可行」的结论" if _no_verdict
                 else "本集规划不可行／需拆集")
        raise PlanInfeasibleError(
            "%s：%s ⇒ 不生成完整稿（%s）"
            % (_head, "；".join(rep["reasons"]), "；".join(rep["suggestions"])))
    if rep["verdict"] == "unevaluable":
        _dump(chapter_dir, rep, plan, no_verdict=_no_verdict)
        raise PlanInfeasibleError(
            "计划无法在生成前验算：%s ⇒ 不生成完整稿（%s）"
            % ("；".join(rep["reasons"]), "；".join(rep["suggestions"])))
    return rep


def _dump(chapter_dir, rep: dict, plan=None, *, no_verdict: bool = False) -> None:
    """把判定落成**诊断产物**（正式三件套不动）——准则 7：失败必须看得见.

    `verdict` 记的是**计划本身的读数**（事实：超预算 / 无法验算），`conclusion` 记的是
    **可以据此下的结论**：`teardown`（确认装不下）/ `replan_incomplete`（重选没跑成，
    尚无结论，用户 2026-09-25）。
    """
    if chapter_dir is None:
        return
    plan_replan = (plan or {}).get("plan_replan") if isinstance(plan, dict) else None
    try:
        from pathlib import Path
        from shared.json_io import atomic_dump_json
        atomic_dump_json(Path(chapter_dir) / "step01_plan_infeasible.json",
                         {"verdict": rep["verdict"],
                          "conclusion": ("replan_incomplete" if no_verdict
                                         else ("teardown" if rep["verdict"] == "infeasible"
                                               else "")),
                          "reasons": rep["reasons"],
                          "suggestions": rep["suggestions"],
                          "dialogue_lines": rep["dialogue_lines"],
                          "dialogue_sec": rep["dialogue_sec"],
                          "dialogue_rejected": rep["dialogue_rejected"],
                          "beats_floor_sec": rep["beats_floor_sec"],
                          "lower_bound_sec": rep["lower_bound_sec"],
                          # v6.132：规划层是否已经重选过一轮、重选前后各是多少——
                          # 「重选后仍超限」与「压根没重选」是两种不同的处置依据
                          "replan": (plan_replan if isinstance(plan_replan, dict) else None),
                          "budget": rep["budget"], "note": rep["note"]},
                         ensure_ascii=False, indent=2)
    except Exception:
        pass
