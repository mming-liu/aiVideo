"""step01_script_gen.outline - 章节大纲生成（ShortVideoDirector director-outline 吸收）.

在分镜生成前，先用 LLM 生成本章大纲（主要事件/角色出场/情绪弧线/集尾钩子），
注入分镜 prompt 作为「大纲锚点」，让分镜生成参考大纲，保证冲突/高潮分布。
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from _text_gen import create_service, TextGenerationRequest
from _text_gen.deepseek_schema import OUTLINE_SCHEMA, REPLAN_OPS_SCHEMA


OUTLINE_PROMPT = """
阅读以下小说原文，生成章节大纲，供后续分镜生成参考。

# 小说原文
{novel_text}

# 输出格式
只输出纯JSON，格式如下：
{{
  "main_events": ["主要事件1", "主要事件2", ...],
  "characters": ["出场角色1", "角色2", ...],
  "emotional_arc": "本章情绪弧线（如：平静→惊疑→爆发→余波）",
  "climax": "本章高潮事件（冲突/转折点）",
  "end_hook": "结尾钩子（吸引观众看下一集）",
  "key_scenes": ["关键场景1", "场景2", ...],
  "required_facts": ["事实A｜原文证据：...", "事实B｜原文证据：..."],
  "causal_chain": ["起因→行动→结果→悬念"],
  "character_motives": ["角色：想做什么；为什么；原文证据：..."],
  "shot_beats": ["节拍：...｜本镜信息目的：...｜原文依据：...｜载体：台词k 或 画面"],
  "dialogue_lines": ["逐字台词1（原文原话）", "逐字台词2"],
  "omittable": ["可省略内容1", "..."],
  "time_budget": "对白/动作/反应的大致分配（不必报精确秒数）"
}}

# 规则
- main_events: 按时间顺序列出 3-6 个主要事件，覆盖全章 = **本集的事件顺序**
- required_facts: **必保留事实**（"必保留"的唯一清单），不得省略，必须附原文证据
- dialogue_lines: 本集**选用的逐字台词/独白**，从原文**逐字摘出**（不改写、不润色、不自造）；
  只收真正要拍的句子，宁少勿滥——每一句都占用时长，而时长由代码逐句估算（自报秒数不参与判定）
- omittable: 本集**可省略的内容**（不拍也不影响主线的原文段落），供预算不够时重选
- 时长预算（**先算再选，v6.132**）：本集正片**合法 60-90 秒**、**规划目标 70-80 秒**。
  对白是时长的最大一项，**必须给画面动作/反应/过场留出时间**——每场只留最关键的一两句，
  不要把整段对话都列进来。**选多了本集就装不下**：会被预算门打回重选（反馈会带上代码
  算出的秒数），白费一轮；更严重的是会把整集挤爆。
- causal_chain: 每个关键转折必须具备起因→行动→结果→悬念
- character_motives: 说明角色为什么这样做，并附原文证据
- shot_beats: 覆盖每条 main_events，按原文顺序排列；每个节拍只承载一条主要信息，标出信息目的和证据；
  末尾必须标**载体**（v6.138 事件履约契约）：`载体：台词k` = 由 `dialogue_lines` 第 k 句承载
  （k 按 dialogue_lines 顺序编号）；`载体：画面` = 由画面动作承载、不占台词时长。
  一个节拍只能有一个载体；之后重选台词增删时，受影响节拍的载体必须同步更新
- time_budget: 只写大致的分配倾向（**不必**报精确秒数——时长由代码按 dialogue_lines 逐句估算，
  自报秒数不参与判定）；对白与**同时发生**的画面动作不要重复计时
- emotional_arc: 一句话概括情绪走向
- climax: 全章最震撼/冲突最强的事件
- end_hook: 本章结尾已有的悬念，必须有原文依据；不得为了另拍钩子视频虚构事件
- key_scenes: 需要重点刻画的关键场景（动作/情感高潮）
"""


_OUTLINE_SYSTEM_PROMPT = "你是短视频编剧，擅长从小说章节提炼叙事大纲，供分镜参考。只输出纯JSON。"

# ---------------------------------------------------------------------------
# 规划层重选（**一轮，带代码算出的数字**）——用户 2026-09-25 决定 ①
# ---------------------------------------------------------------------------
#: 最多重选几轮。用户明确要求"**一次**有数值反馈的规划重选"——这不是可调容量，
#: 不要为了"再多试一次"抬高：重选解决的是"选材超预算"，第二次不会变成另一种问题。
#: **口径（用户 2026-09-25 补充）**："一次"指**同一计划版本一次**，不是每次调用一次：
#: 已尝试过的结论记在 `plan_replan` 并随缓存持久化，计划/规则没变就直接沿用。
_REPLAN_MAX_ROUNDS = 1
#: 重选**允许**改动的字段；其余字段必须原样返回（必保留事实与事件顺序不得变）
_REPLAN_FREE_FIELDS = ("dialogue_lines", "omittable", "time_budget")

#: 强制重跑这一轮重选的开关（用户 2026-09-25：不自动重试，但要有**明确的重新规划入口**）.
#: 置 1 = 忽略计划里已记录的重选结论，重新跑一轮；默认空 = 沿用已记录的结论。
FORCE_REPLAN_ENV = "STEP01_FORCE_REPLAN"


def _force_replan() -> bool:
    return os.getenv(FORCE_REPLAN_ENV, "").strip() == "1"

_PLAN_BUDGET_FEEDBACK = """
# ★ 重选本集台词（预算反馈；本轮**只改台词**）

代码按**逐句时长估算器**核算了你上一版计划选用的台词（**不采信自报秒数**）：
- 已过原文逐字核验的台词：{lines} 句，估时 **{dialogue_sec:.2f} 秒**
- 本集正片**合法 {bmin:.0f}-{bmax:.0f} 秒**，**规划目标 {tmin}-{tmax} 秒**（画面动作、反应、
  过场都要占时间）
- 镜头节拍 {beats} 个，画面下界 {floor:.2f} 秒

⇒ 对白一项就 {dialogue_sec:.2f} 秒，**本集按当前选材装不下**。请重新选择原文台词：

1. **只从原文逐字摘**真正必须拍的对白——不改写、不润色、不自造、不拿旁白当台词；
2. 按**规划目标**把对白估时压到 **{tmax} 秒以内**：可以少选，每场只留最关键的一两句；
3. **其余字段必须与上一版完全一致**（`main_events` / `required_facts` / `causal_chain` /
   `character_motives` / `shot_beats` / `climax` / `end_hook` / `key_scenes` / `characters`）：
   必保留事实一句都不能删，事件顺序不许改——**改了就会被拒收，这一轮白费**；
   唯一例外：`shot_beats` 的「｜载体：…」标注**必须**随台词增删同步更新
   （被删台词承载的节拍改标保留台词或「载体：画面」；节拍本体不许动）；
4. 顺带更新 `omittable`（可省略内容）与 `time_budget`（倾向性文字，不必报精确秒数）。
"""

_PLAN_REPRESENTABILITY_FEEDBACK = """
# ★ 重选本集台词 / 载体（单镜可表示性反馈）

以下 {n} 句选用台词**无法装进单镜 {cap:.0f} 秒**，且句内没有可用的**句末边界**
（。！？…；）切成 ≤4 段、每段 ≤ 上限——单镜装不下、按其他标点拆开会把一句台词
拆成句中碎片，下游硬门一律拒收（判据与拆分器/台词完整性门同源）：

{lines_block}

**已验算的可表示替代**（句末收尾、逐字来自原文，按保留信息量降序）：

{replacements_block}

请**按原文顺序重新规划受影响的节拍与载体**，硬边界如下：

1. **只从原文逐字摘**，不改写、不润色、不自造；
2. **有列出替代的，从列出的替代里选**（都已过单镜可表示性验算），不要另抽一句；
3. **不许用逗号截断的半句**——以「，」「、」收尾的片段虽然单镜装得下，成片里听起来
   是没说完的半句，不作替代；
4. 某句**没列替代**（它的出处通篇没有句末标点）时：**不得把某人的台词改派给另一个角色**，
   而要**按原文先后顺序重排载体**——把该节拍拆成「画面动作镜」+「由**别人**在原文里
   指向同一事实的完整短句承载」，两个动作的顺序必须与原文一致；
5. **本轮允许改动 `shot_beats` 的正文与载体**（这是唯一例外）：但每条节拍必须带
   「原文依据："…"」，引文要**逐字复制原文**（至少连续 8 字逐字命中，少抄字会核不过）；
   **必保留事实**（`required_facts`）每条的「原文证据："…"」同样要逐字可核。
   **没有原文证据的节拍/必保留事实一律拒收**；
6. `main_events` 不得删除，**事件顺序**不得调换；
7. 若原文确实没有能承载这些事实的合法台词与节拍，请在 `time_budget` 里**明确写明**
   「台词不可表示：无合适替代」——代码会据此显式停止，把内容取舍交还用户，而不是硬拆。
"""

_PLAN_CARRIER_FEEDBACK = """
# ★ 更新节拍载体标注（载体反馈；本轮**只改台词与节拍的载体标注**）

以下 {n} 个节拍的载体标注指向的台词**不在当前选用台词里**（v6.138 事件履约契约）：

{lines_block}

节拍没有承载，成片就拍不出这件事（审计 MISSING_PLOT/MISSING_ACTION 的直接来源）。
请修复：

1. 把承载该节拍的**原文**台词重新选回 `dialogue_lines`（逐字，不改写）；
2. 或把该节拍的载体标注改为仍保留的台词（`载体：台词k`，k 按选用台词顺序编号）；
3. 或改标 `载体：画面`（由画面动作承载，不占台词时长）；
4. **节拍内容与顺序不得改动**——只许改「｜载体：…」标注，改了节拍本体整轮白费；
5. 若确实既无台词也无画面可承载，请在 `time_budget` 明确写「载体缺失：无合适承载」
   ——代码会据此显式停止，把内容取舍交还用户。
"""


def _parse_outline_json(raw: str) -> dict | None:
    """剥 code fence + 解析 JSON（首版大纲与重选共用同一条解析路径）."""
    raw = str(raw or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None


def _budget_feedback_block(rep: dict, plan: dict, novel_text: str = "") -> str:
    """把**代码算出**的数字反馈给规划阶段（数字全部来自 `evaluate_plan`，不含自报值）.

    v6.136：反馈按实际失败类型拼装——**超预算**块只在对白下界真超上限时给出
    （否则"本集装不下"是假命题）；**单镜可表示性**块在存在不可表示台词时给出
    （A 判据，点名台词 + 必保留事实/事件顺序硬边界）。
    **完整旧计划附在尾部**（六审 P1-1）：反馈要求"其余字段原样返回"，模型必须
    看得见旧字段——可表示性单独触发时（预算内）预算块不渲染，旧计划若只在
    预算块里，重选会因核心字段对不上被 `_plan_core_preserved` 拒收。
    """
    _b = rep.get("budget") or {}
    block = ""
    _lower = rep.get("lower_bound_sec")
    if _lower is not None and float(_lower) > float(_b.get("max") or 0.0):
        block += _PLAN_BUDGET_FEEDBACK.format(
            lines=rep.get("dialogue_lines", 0),
            dialogue_sec=float(rep.get("dialogue_sec") or 0.0),
            bmin=float(_b.get("min") or 0.0), bmax=float(_b.get("max") or 0.0),
            tmin=_TARGET_MIN_SEC, tmax=_TARGET_MAX_SEC,
            beats=rep.get("beats", 0),
            floor=float(rep.get("beats_floor_sec") or 0.0),
        )
    _unrep = rep.get("dialogue_unrepresentable") or []
    if _unrep:
        from step01_script_gen.plan_feasibility import (
            cross_quote_replacements, representable_replacements)
        from step01_script_gen.timecode import active_shot_cap
        _lines_block = "\n".join(
            "- 「%s」（估时 %.1f 秒；%s）"
            % (str(u.get("line") or "")[:60], float(u.get("est_sec") or 0.0),
               u.get("reason") or "")
            for u in _unrep)
        # 2026-09-26 用户决定：给**已验算过**的替代集合，不再让规划层自由重抽
        # （实测自由重选会把不可表示的句子原样留下）；没有句末收尾替代时，
        # 明确要求按原文顺序重排载体，而不是逗号硬拆。
        _reps = []
        for u in _unrep:
            _line = str(u.get("line") or "")
            _cands = representable_replacements(_line, novel_text)
            if _cands:
                _reps.append("- 「%s」→ %s" % (
                    _line[:34], "；".join("「%s」" % c for c in _cands)))
                continue
            # 2026-09-26 实测：只说「没有候选、请重排载体」时模型去**截断本句**
            # （给逗号半句，被应用器拒）。把**跨出处**的合法短句摆到它面前——
            # 同场景相邻发言里本来就有能承载同一事实的完整短句。
            _cross = cross_quote_replacements(_line, novel_text)
            _menu = ("；".join("「%s」（%s）" % (c["text"], c["from"]) for c in _cross)
                     if _cross else "（附近也没有可用短句）")
            _reps.append(
                "- 「%s」→ **本句出处通篇没有句末标点，不许截断本句**（逗号收尾的半句"
                "一律非法）。两条合法出路：① 换成**原文别处**的完整短句，候选：%s；"
                "② 用 `beat_ops` 把该节拍改成画面承载、该事实由别的合法短句承载"
                "（**禁止把某人的台词改派给另一个角色**）" % (_line[:34], _menu))
        block += _PLAN_REPRESENTABILITY_FEEDBACK.format(
            n=len(_unrep), cap=active_shot_cap(), lines_block=_lines_block,
            replacements_block="\n".join(_reps))
    # v6.138（九审根因 1）：节拍载体悬空反馈——重选删承载台词必须同步更新载体标注
    # v6.143（2026-09-27 复核）：承载缺陷（重复标注 / 孤儿台词）与可表示性一样，
    # 都是「结构性问题」，必须走**操作式**修复——要求模型重写整份计划时它可以原样
    # 回抄（实测），承载关系反而没人动。
    _dup_c = rep.get("beat_carrier_duplicate") or []
    _orphan_c = rep.get("dialogue_orphan") or []
    _unlabeled_c = rep.get("beat_carrier_unlabeled") or []
    if _dup_c or _orphan_c or _unlabeled_c:
        block += _PLAN_CARRIER_RELATION_FEEDBACK.format(
            dup=("、".join(_dup_c) or "无"),
            orphan=("、".join(_orphan_c) or "无"),
            unlabeled=("、".join(_unlabeled_c) or "无"),
            n_lines=int(rep.get("dialogue_lines") or 0))
    _dangling = rep.get("beat_carrier_dangling") or []
    if _dangling:
        block += _PLAN_CARRIER_FEEDBACK.format(
            n=len(_dangling),
            lines_block="\n".join("- %s" % d for d in _dangling))
    if block:
        if _unrep or _dup_c or _orphan_c or _unlabeled_c:
            # 操作式（v6.143）：计划只作**只读上下文**，不再要求模型整份重写——
            # 上一版的「除…外请原样返回」正是模型回抄旧计划的退路。
            block += (_PLAN_OPS_INSTRUCTION
                      + "\n## 上一版计划（**只读上下文，不要重写它**）\n"
                      + json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            block += ("\n## 上一版计划（除台词/可省略/时间倾向外，请原样返回）\n"
                      + json.dumps(plan, ensure_ascii=False, indent=2))
    return block


def _plan_core_preserved(old: dict, new: dict) -> str:
    """重选后核心字段是否原样保留；返回第一个被改动/丢失的字段名（空串=通过）.

    "保持必保留事实和事件顺序"是用户给这轮重选的硬边界——模型改写这些字段就**不接受**
    （宁可用首版计划去报告需拆集，也不接受一份事实被悄悄改过的计划）。

    v6.138 例外：`shot_beats` 允许**纯载体标注**差异——重选增删台词时「｜载体：…」
    标注必须随行更新（九审根因 1），但节拍本体（去载体段后）必须逐字一致。
    """
    for key in sorted(set(old or {}) | set(new or {})):
        if key in _REPLAN_FREE_FIELDS or key.startswith("prop_state") or key == "plan_replan":
            continue
        if (old or {}).get(key) == (new or {}).get(key):
            continue
        if key == "shot_beats":
            from step01_script_gen.plan_feasibility import _strip_beat_carrier
            _old = [_strip_beat_carrier(x) for x in ((old or {}).get(key) or [])]
            _new = [_strip_beat_carrier(x) for x in ((new or {}).get(key) or [])]
            if _old == _new:
                continue
        return key
    return ""


def replan_dialogue_within_budget(plan, rep, novel_text, *, model=None,
                                  base_prompt=None, system_prompt=None) -> dict | None:
    """**带数值反馈的规划重选**：只重选台词，核心字段必须原样（唯一一轮）.

    Returns `(新计划, "")`；调用失败 / 解析失败 / 校验不过 / 核心字段被改动 →
    `(None, 原因)`——**原因要分别记录**（用户 2026-09-25）：调用/解析失败**不是**判定结论，
    不能据此报告"需拆集"（见 `check_plan_before_generation` 的 `_replan_note`）。
    """
    from shared.config import PROMPT_MAX_CHARS
    # v6.143（2026-09-26 用户决定）：**可表示性触发**的重选走**操作式**——模型只回
    # 「换哪句台词 / 改哪条节拍」，由代码在旧计划上应用。要求它重写整份计划既给了
    # 「什么都不改、回抄旧稿」的退路（实测两次都这样），也让验收只能事后比对。
    # 预算超限触发仍走原路径（本来就是「只改台词」，另有全套核心字段校验）。
    _unrep = rep.get("dialogue_unrepresentable") or []
    # 承载缺陷走同一条操作式通道（见 `_budget_feedback_block` 的说明）
    _ops_mode = bool(_unrep or rep.get("beat_carrier_duplicate")
                     or rep.get("dialogue_orphan")
                     or rep.get("beat_carrier_unlabeled"))
    _base = base_prompt or OUTLINE_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS])
    _prompt = _base + "\n" + _budget_feedback_block(rep, plan, novel_text)
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=4000, temperature=0.3,
            json_schema=REPLAN_OPS_SCHEMA if _ops_mode else OUTLINE_SCHEMA,
            model=model))
    except Exception as exc:
        print(f"[WARN] 规划重选调用异常（保留首版计划）: {exc}")
        return None, "call_failed: %s" % exc
    if not result.success:
        print(f"[WARN] 规划重选失败（保留首版计划）: {result.error}")
        return None, "call_failed: %s" % result.error
    data = _parse_outline_json(result.text)
    if _ops_mode:
        if not isinstance(data, dict):
            print("[WARN] 规划重选结果解析失败（保留首版计划）")
            return None, "invalid_result: JSON 解析失败"
        new_plan, _why = apply_replan_ops(plan, data, novel_text)
        if new_plan is None:
            if _why == "no_change":
                print("[PLAN] 重选**没有给出任何操作**（显式空操作）——记 no_change，"
                      "不算一次有效重选")
            else:
                print("[WARN] 重选操作应用失败（%s）——保留首版计划" % _why)
            return None, _why
        print("[PLAN] 重选操作已应用（台词 %d 处 / 节拍 %d 处）——作为新计划版本接受"
              % (len((data or {}).get("dialogue_ops") or []),
                 len((data or {}).get("beat_ops") or [])))
        return new_plan, ""
    new_plan = _validate_outline(data, novel_text) if isinstance(data, dict) else None
    if not new_plan:
        print("[WARN] 规划重选结果不合法（保留首版计划）")
        return None, "invalid_result"
    _broken = _plan_core_preserved(plan, new_plan)
    if _broken:
        # 预算超限触发的重选：核心字段必须原样（只许改台词）。
        # 可表示性触发的那条路已在上面的 ops 分支 early-return——**不再**存在
        # 「用『新计划剩余条目有原文引文』代替旧计划核心字段比对」那种例外
        # （2026-09-27 复核：引文核验是**附加**条件，替代不了逐项比对，
        # 证明不了必保留事实没被删除、事件顺序没被调换）。
        print("[WARN] 规划重选改动了核心字段「%s」——不接受（必保留事实/事件顺序必须原样）"
              % _broken)
        return None, "core_field_changed: %s" % _broken
    return new_plan, ""


def _rotate_outline_cache_on_regen(cache_path, new_key, new_outline) -> None:
    """覆盖大纲缓存前：**无条件**留一份旧版 `.bak` + 选材退化告警（v6.138.1 / v6.143）.

    缓存键含 outline prompt——**规则演进必然整批失效重生成**，而重新抽样可能显著
    变差（2026-09-26 实证：6 句→2 句，成片 38s 撞 duration_budget 硬门；且计划一换，
    全部历史快照因选材门失格，跨 run 收敛棘轮归零）。留档让退化可恢复；是否采用
    新计划仍由运行者裁决——**内容取舍不代决**，这里只保证可见。

    v6.143（2026-09-27 复核）：**同键覆盖也要留旧版**。旧实现在 `source_hash` 未变时
    直接 return——而**规划重选**恰恰是同键覆盖（计划变了、键没变），于是那次覆盖
    把原计划**就地抹掉**、连恢复入口都没有（09-26 实测：章 2 那份不可行计划在缓存里
    被重选结果替换，`.bak` 不存在）。现在无论键是否变化都先落 `.bak`；
    「选材退化」的告警仍只在键变化（规则演进）时报，避免重选也被当成规则演进。
    """
    from step01_script_gen.plan_feasibility import plan_dialogue_lines
    try:
        old = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(old, dict):
        return
    _key_changed = old.get("source_hash") != new_key
    old_outline = old.get("outline") if isinstance(old.get("outline"), dict) else None
    _old_n = len(plan_dialogue_lines(old_outline))
    _new_n = len(plan_dialogue_lines(new_outline))
    _bak = Path(str(cache_path) + ".bak")
    try:
        _bak.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    if _key_changed and _new_n < _old_n:
        print(f"[WARN] 大纲缓存因规则变化重生成：选用台词 {_old_n} 句 → {_new_n} 句"
              f"（新计划选材变少，成片时长下界随之下降；旧计划已留档 {_bak.name}，"
              "如需恢复请回拷并核对缓存键）")


def enforce_budget_by_replanning(plan, novel_text, *, model=None,
                                 base_prompt=None, system_prompt=None) -> tuple:
    """规划阶段出口：代码验算 → 超预算就**带数字重选一次** → 再验算（`_REPLAN_MAX_ROUNDS`）.

    Returns `(plan, info)`；`info` 记录这一轮读数（写进计划的 `plan_replan` 供诊断留档），
    未触发重选时为空 dict。

    **不在这里判"需拆集"**：判定仍归生成前的门（`plan_feasibility.check_plan_before_generation`）
    ——重选后仍超限时它照旧停止，诊断里带上两次读数。
    """
    from step01_script_gen.plan_feasibility import evaluate_plan
    rep = evaluate_plan(plan, novel_text=novel_text)
    if rep["verdict"] != "infeasible" or _REPLAN_MAX_ROUNDS < 1:
        return plan, {}
    # 用户 2026-09-25 定口径：**"只重选一次" = 同一计划版本一次**，不是每次调用一次。
    # 上一轮已经把"重选后仍超限 / 重选失败"的结论记在计划里（`plan_replan`，随缓存持久化），
    # 计划与规则都没变时直接沿用结论——否则缓存里那份计划会让每轮运行都再烧一次模型。
    _prev = plan.get("plan_replan") if isinstance(plan, dict) else None
    from step01_script_gen.plan_feasibility import replan_criteria_version
    _crit_ver = replan_criteria_version()
    _prev_ver = str((_prev or {}).get("criteria_version") or "") if isinstance(_prev, dict) else ""
    _ver_matches = bool(_crit_ver) and _prev_ver == _crit_ver
    if (isinstance(_prev, dict)
            and _prev.get("result") in ("infeasible", "replan_failed", "no_change")
            and _ver_matches
            and not _force_replan()):
        print("[PLAN] 这一版计划已重选过一轮（%s）→ 不再重复重选（同一计划版本只重选一次）；"
              "如需重跑该轮：%s=1" % (_prev.get("result"), FORCE_REPLAN_ENV))
        return plan, dict(_prev, skipped="already_attempted")
    if isinstance(_prev, dict) and _prev.get("result") and not _ver_matches and not _force_replan():
        # 判据版本变了 ⇒ 旧结论不再代表「本判据下也不可行」，按没试过重跑一次
        # （2026-09-27 复核：沿用旧尺子的量数否定新尺子的重试是错的）
        print("[PLAN] 重选判据版本已变化（%s → %s）→ 旧结论不再沿用，重跑这一轮"
              % (_prev_ver or "无", _crit_ver))
    if isinstance(_prev, dict) and _force_replan():
        print("[PLAN] %s=1 → 忽略已记录的重选结论（%s），重跑这一轮"
              % (FORCE_REPLAN_ENV, _prev.get("result")))
    print("[PLAN] 首版计划不可行（对白 %.2fs / %d 句，上限 %.0fs；单镜不可表示 %d 句）"
          "→ 带数字反馈重选台词（%d 轮）"
          % (rep["dialogue_sec"], rep["dialogue_lines"], rep["budget"]["max"],
             len(rep.get("dialogue_unrepresentable") or []), _REPLAN_MAX_ROUNDS))
    info = {"rounds": _REPLAN_MAX_ROUNDS, "criteria_version": _crit_ver,
            "before_dialogue_sec": rep["dialogue_sec"], "before_lines": rep["dialogue_lines"]}
    new_plan, _why = replan_dialogue_within_budget(plan, rep, novel_text, model=model,
                                                   base_prompt=base_prompt,
                                                   system_prompt=system_prompt)
    if new_plan is None:
        # "重选没跑成"（调用/解析/校验失败）与"重选后仍超预算"是**两回事**：
        # 前者没有判定结论，不能据此说本集需拆集。
        info["result"] = "replan_failed"
        info["detail"] = _why
        # v6.143：`no_change`（模型显式回空操作）**是**一个结论——它等于「规划层
        # 认为没有可改的地方」，与"没跑成"分开记，否则诊断里看不出是哪种。
        if _why == "no_change":
            info["result"] = "no_change"
            print("[PLAN] 规划层重选**没有给出任何操作**——按 no_change 记录"
                  "（计划不变，仍不可行）；如需重跑：%s=1" % FORCE_REPLAN_ENV)
            return plan, info
        print("[PLAN] 规划层重选未完成（%s）——这**不是**「本集不可行」的判定；"
              "重跑该轮：%s=1" % (_why, FORCE_REPLAN_ENV))
        return plan, info
    rep2 = evaluate_plan(new_plan, novel_text=novel_text)
    info.update({"result": rep2["verdict"], "after_dialogue_sec": rep2["dialogue_sec"],
                 "after_lines": rep2["dialogue_lines"],
                 "rejected": len(rep2["dialogue_rejected"]),
                 "after_unrepresentable": len(rep2.get("dialogue_unrepresentable") or [])})
    if rep2["verdict"] == "infeasible":
        print("[PLAN] 重选后仍不可行（对白 %.2fs / %d 句 > %.0fs；单镜不可表示 %d 句）"
              "→ 由生成前的门报告「需拆集／无合适替代」"
              % (rep2["dialogue_sec"], rep2["dialogue_lines"], rep2["budget"]["max"],
                 len(rep2.get("dialogue_unrepresentable") or [])))
        return new_plan, info
    print("[PLAN] 重选后进入预算（对白 %.2fs → %.2fs；%d 句 → %d 句）"
          % (info["before_dialogue_sec"], info["after_dialogue_sec"],
             info["before_lines"], info["after_lines"]))
    return new_plan, info


def generate_outline(novel_text: str, model: str | None = None,
                     cache_path: Path | None = None) -> dict | None:
    """生成章节大纲；同章文本、模型和规则未变时复用磁盘缓存。"""
    if not novel_text:
        return None
    from shared.config import PROMPT_MAX_CHARS
    prompt = OUTLINE_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS])
    system_prompt = _OUTLINE_SYSTEM_PROMPT
    # v6.125.2（用户 2026-09-25 决定）：道具状态**候选锚点**随大纲一起携带。
    # 它由**确定性扫描**从原文得出（不经 LLM），只取 `motion_away` / `marker_after`
    # 两种形状，带原文位置与 `status="待核实"`；生成/GAP 重生用**独立短提示**注入
    # （不能并进 `outline_anchor`——doubao/agent_plan 剖面会把大纲锚点压到约 200 字，
    # v6.132 起必保【高潮事件】与【选用台词】，其余行只保留装得下的）。
    from step01_script_gen.prop_state_anchor import outline_prop_anchors
    _prop_anchors = outline_prop_anchors(novel_text)
    cache_key = None
    outline = None
    _from_cache = False
    if cache_path is not None:
        from _text_gen.config import TextGenCfg
        cache_path = Path(cache_path)
        cache_source = {
            "novel_text": novel_text,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "schema": OUTLINE_SCHEMA,
            "provider": TextGenCfg.PROVIDER,
            "model": model or TextGenCfg.default_text_model(),
            "temperature": 0.3,
            "max_tokens": 4000,
            # v6.125.2：候选锚点进缓存键——**提取规则变化**（同一段原文得出不同
            # 候选）必须让缓存失效，否则旧锚点会被当成新规则的结果复用。
            "prop_anchors": _prop_anchors,
        }
        cache_key = hashlib.sha256(json.dumps(
            cache_source, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")).hexdigest()
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and _validate_outline(cached.get("outline"), novel_text)):
                print(f"[CACHE] 复用章节大纲: {cache_path}")
                outline = cached["outline"]
                _from_cache = True
                # 缓存里若缺该字段（旧缓存但键相同——理论上不会，键已含锚点），
                # 用本次算出的补上，避免调用方拿到 None 而丢掉独立短提示
                if isinstance(outline, dict):
                    outline.setdefault("prop_state_candidates", _prop_anchors)
        except (OSError, ValueError, TypeError):
            pass
    if outline is None:
        service = create_service()
        req = TextGenerationRequest(
            system_prompt=system_prompt,
            user_prompt=prompt,
            max_tokens=4000,
            temperature=0.3,
            json_schema=OUTLINE_SCHEMA,
            model=model,
        )
        result = service.generate(req)
        if not result.success:
            return None
        data = _parse_outline_json(result.text)
        if data is None:
            return None
        # C2 修复（08-11）：大纲零校验——此前 main_events/climax 非空零校验，
        # 空大纲静默注入分镜 prompt（大纲锚点是一堆空标签，LLM 拿不到任何约束）。
        # 补两条校验：① 关键字段非空（main_events 至少1条、climax 非空）；
        # ② main_events 元素非空字符串。不合格时返回 None（调用方按"大纲生成失败"
        # 处理，不注入空锚点）。
        outline = _validate_outline(data, novel_text)
        if not outline:
            return None
        # 候选锚点是**确定性**产物（不经 LLM），挂在大纲 dict 上随稿流动；
        # `_validate_outline` 只做字段净化、不重建 dict，所以不会把它丢掉。
        outline["prop_state_candidates"] = _prop_anchors

    # v6.132（用户 2026-09-25 决定 ①）：规划阶段出口的**预算重选**——代码验算后发现选材
    # 超预算，就把代码算出的数字反馈给规划阶段重选一次（只重选台词，核心字段原样）。
    # 放在这里而不是生成前的门那里，是因为**缓存命中的计划同样要过这一关**：否则一份
    # 超预算的计划会被缓存下来，每轮都在门口被拦、永远没有重选的机会。
    outline, _replan = enforce_budget_by_replanning(
        outline, novel_text, model=model, base_prompt=prompt, system_prompt=system_prompt)
    if _replan:
        outline["plan_replan"] = _replan

    # 重选过就回写缓存（键不变）——下次直接复用选好的计划，不重复烧这一轮
    if cache_path is not None and cache_key is not None and not (_from_cache and not _replan):
        try:
            # v6.138.1：键变化 = 规则演进导致的重生成——覆盖前旧计划留 .bak 并在
            # 选材退化时告警（2026-09-26 实证：6 句→2 句的坏抽样会连锁打穿下游）
            _rotate_outline_cache_on_regen(cache_path, cache_key, outline)
            from shared.json_io import atomic_dump_json
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_dump_json(cache_path, {"source_hash": cache_key, "outline": outline},
                             ensure_ascii=False, indent=2)
        except OSError as exc:
            print(f"[WARN] 章节大纲缓存写入失败（本次继续使用）: {exc}")
    return outline


# 「原文依据/原文证据」里的引文（全角引号；生成层固定用这一对）
_EVIDENCE_QUOTE = re.compile(r'[“"]([^”"]{4,})[”"]')


_PLAN_CARRIER_RELATION_FEEDBACK = """
# ★ 承载关系必须**双向闭合**（当前计划不满足，本轮只修这个）

- 同一个节拍写了**多个**「｜载体：…」段（承载关系不是唯一记录，代码只认第一段）：
  {dup}
- **没有任何节拍承载**的选中台词（生成契约里没人告诉模型这句放哪儿，成片会丢这句，
  候选层台词门随后整稿拒收）：{orphan}
- **没有载体标注**的节拍（计划带了标注就必须每条节拍恰有一个载体）：{unlabeled}

本集共 {n_lines} 句选用台词。请用 `beat_ops` 把承载关系补成闭合：每句台词都有
（且只有）一个节拍用「载体：台词k」声明承载它；每条节拍只留**一个**载体段。
**一条节拍要同时承载画面与台词时必须 `split`**（或 `insert_after` 另立一条）
——单条节拍只能有一个载体，用替换是改不出来的。
**改某条节拍的载体时，要同时把失去承载的那句指派到别的节拍上。**
"""


_PLAN_OPS_INSTRUCTION = """
## 请**只输出操作**（不要重写整份计划）

上面已给出代码验算过的替代与硬边界。你**只回要改的地方**，代码会把它应用到上一版
计划上——所以「什么都不改」是允许的，但要**显式回空数组**，不要靠回抄旧计划表达。

- `dialogue_ops`：`[{"index": 1, "replace_with": "换成哪句逐字原文"}]`
  （`index` 只填**数字**，如 `1`；不要写 `D1`/`"D1"`）。
  **不得截断本句**——以「__ILLEGAL_CUT__」收尾的半句一律非法（成片里是没说完的话）；
  `replace_with` 必须是原文里**逐字存在**且**单镜装得下**的**完整短句**（可以是别的
  角色、别的段落的句子）。有列出候选的，优先从候选里选；该句没列候选时，**不要截断它**，
  改走 `beat_ops` 重排载体。
- `beat_ops`：节拍操作，`op` 三选一（不写 `op` = `replace`）：
  - `{"op": "replace", "index": 2, "new_text": "新正文（含 ｜原文依据：“…”）",
     "new_carrier": "画面 或 台词k"}`——改写该节拍；
  - `{"op": "insert_after", "index": 2, "new_text": …同上…,
     "new_carrier": …}`——在该节拍**之后插入一条新节拍**（`index` 给 0 = 插到最前）；
  - `{"op": "split", "index": 2, "parts": [{"new_text": …, "new_carrier": …},
     {"new_text": …, "new_carrier": …}]}`——把该节拍**原地拆成多条**（至少两段，
     每段各自带原文依据与载体）。**一条节拍同时要承载画面和台词时，用 `split`**。
  （以上 `index` 一律只填**数字**，如 `2`；不要写 `B2`/`"B2"`。）

**索引一律指「改之前」的节拍编号**（你在上面看到的那份计划）：`split`/`insert_after`
会让编号顺延，所以同一批操作里**不要**引用还没出现的新编号。**同一条节拍一批里至多
一条操作**（同一编号出现两次 = 冲突，整轮拒收）。`new_carrier` 留空 = 沿用该节拍原有
载体；`split` 的每一段必须自己写载体（拆分的**目的**就是给各段不同载体）。

**不变量**（代码会逐项核，违反即整轮拒收）：`main_events` 不得增删或调换顺序；
**必保留事实一条都不许少**；`climax` 不得改。节拍正文改动后**必须**带逐字可核的
「原文依据」（长引文 ≥8 字连续命中；4-7 字的短引文要整段逐字）。

**承载关系必须双向闭合**：每条节拍至多一个「｜载体：…」段；**每一句选中的台词
都要至少被一个节拍声明承载**（`载体：台词k`）。你改某条节拍的载体时，要保证没有
哪句台词因此失去全部承载——把原承载改掉就同时把新承载指派到别的节拍上。
（代码会重新验算整张承载关系，违反即整轮拒收。）
"""


#: 节拍操作的种类（2026-09-27 用户决定，合并方案第 2 步）：从「只能替换」扩到可
#: 插入/拆分。评审指出章 2 不收敛的根因之一是「节拍操作表达能力不足」——替换改不了
#: 节拍**数量**，而承载关系缺陷恰恰需要「把一条节拍拆成画面 + 台词两条」（一条节拍
#: 同时承载两个事实时，替换没法让它只承载一个）或「补一条承载 D1 的节拍」。
BEAT_OP_REPLACE = "replace"
BEAT_OP_INSERT_AFTER = "insert_after"
BEAT_OP_SPLIT = "split"
BEAT_OP_KINDS = (BEAT_OP_REPLACE, BEAT_OP_INSERT_AFTER, BEAT_OP_SPLIT)


#: 提示里的非法切点字符表**由判据侧的常量渲染**（不在提示里手抄一遍字符）——
#: 复核口径：提示文字是手写规则，一致性必须由**守卫用例**保证，而不是靠同步习惯。
from step01_script_gen.plan_feasibility import CUT_ILLEGAL_CHARS as _ILLEGAL_CUT

_PLAN_OPS_INSTRUCTION = _PLAN_OPS_INSTRUCTION.replace(
    "__ILLEGAL_CUT__", "」「".join(_ILLEGAL_CUT))

#: 同类守卫用的标记：提示里确实渲染进了全部非法字符（空表时视为未接）
assert "__ILLEGAL_CUT__" not in _PLAN_OPS_INSTRUCTION


def _norm_beat_carrier(raw, n_lines: int, old_record) -> tuple:
    """规范化一条操作给的载体 → `(载体文本, 原因)`；`原因` 非空 = 拒收.

    - 空 → 沿用 `old_record`（被改节拍）原有的载体——模型常常只想改正文，这比整轮
      拒收有用；沿用不到（原节拍本来就没有载体段）时留空，由承载关系验收判
      （那才是唯一判据）；
    - `画面` → 照收；`台词k` → k 必须在 `1..n_lines` 内（节拍操作**不**改台词条数）；
    - 其余一律拒收：载体是**唯一记录**，含糊值等于没声明（宁可显式失败）。
    """
    from step01_script_gen.plan_feasibility import _BEAT_CARRIER_DIALOGUE_PREFIX
    car = str(raw or "").strip()
    if not car and old_record is not None:
        kind, k = old_record.carrier()
        car = ("台词%d" % k) if (kind == "dialogue" and k) else (
            "画面" if kind == "visual" else "")
    if not car:
        return "", ""
    if car == "画面":
        return car, ""
    if car.startswith(_BEAT_CARRIER_DIALOGUE_PREFIX):
        digits = ""
        for ch in car[len(_BEAT_CARRIER_DIALOGUE_PREFIX):]:
            if ch.isdigit():
                digits += ch
            else:
                break
        k = int(digits) if digits else 0
        if 1 <= k <= int(n_lines or 0):
            return "台词%d" % k, ""
        return "", "载体「%s」越界（本集共 %d 句台词）" % (car[:12], int(n_lines or 0))
    return "", "载体只能是「画面」或「台词k」，收到「%s」" % car[:12]


def _coerce_op_index(raw, tag: str, axis: str) -> tuple:
    """操作 `index` → `(int, 原因)`；按**字段轴**解析编号.

    与 `plan_refs` 同源（`coerce_ref_index`）：接受整数 / 数字串 / **本轴**标签串
    （台词操作 `D1`、节拍操作 `B1`）。

    **错轴标签必须拒绝**：`"B1"` 出现在 `dialogue_ops` 里说明模型把两套编号体系搞混了
    ——按数字静默照收（B1 → 1）会把改动落到**另一条**对象上，比拒收危险得多。
    """
    from step01_script_gen.plan_feasibility import coerce_ref_index
    _v = coerce_ref_index(raw, tag)
    if _v is not None:
        return _v, ""
    _s = str(raw or "").strip()
    _other = "B" if tag == "D" else "D"
    if _s[:1].upper() == _other:
        return None, ("index 用了**另一套编号**「%s」——%s操作填的是%s编号（只填数字，"
                      "如 1）" % (_s[:12], axis, "台词" if tag == "D" else "节拍"))
    return None, "index 不是编号：%r（只填数字，如 1；不要带 %s 前缀）" % (raw, tag)


def _render_beat_op(new_text, new_carrier, old_record, n_lines: int, *,
                    require_carrier: bool = False) -> tuple:
    """把一条操作的 `(new_text, new_carrier)` 落成节拍文本 → `(文本, 原因)`.

    判据读 `BeatRecord` 的**字段**，不再靠字符串拼接：`new_text` 是**整条节拍正文**
    （自带「｜原文依据：…」）；模型若把旧标注一起抄进 `new_text`，这里**先删干净再
    重建**，从表示上消除「｜载体：画面｜载体：画面」这类重复标注（实跑出现过——
    旧实现靠事后数字符串才发现）。
    """
    from step01_script_gen.plan_feasibility import (
        BEAT_SEG_CARRIER, BEAT_SEG_EVIDENCE, BeatRecord)
    _rec = BeatRecord.parse(new_text)
    if not _rec.body:
        return None, "节拍正文为空"
    if not _rec.has(BEAT_SEG_EVIDENCE):
        return None, "节拍正文缺「原文依据」"
    _car, _why = _norm_beat_carrier(new_carrier, n_lines, old_record)
    if _why:
        return None, _why
    if require_carrier and not _car:
        # P1-4（2026-09-27 复核）：**新建**的节拍没有「原有载体」可沿用——留空就等于
        # 凭空多出一条没有承载记录的节拍。旧计划**整体**无标注时兼容分支不会报未标注
        # （`carrier_relation_problems` 的「计划带了标注才判」口径），这条新节拍会静默
        # 无标注 ⇒ 必须在这里显式失败，而不是靠下游兜。
        return None, ("新建的节拍必须显式给 `new_carrier`（画面 或 台词k）——"
                      "没有旧节拍可沿用载体，留空等于多出一条无承载记录的节拍")
    _rec.drop(BEAT_SEG_CARRIER)
    if _car:
        _rec.set(BEAT_SEG_CARRIER, _car)
    return _rec.render(), ""


def apply_replan_ops(plan: dict, ops, novel_text: str) -> tuple:
    """把模型给的**操作**应用到旧计划上（**代码执行**，模型不重写整份 JSON）.

    2026-09-26 用户决定：实测两次重选，模型把上一版计划原样回显，而事后比对把
    「没改动」判成合规——要求模型整份重写既给了它「什么都不改」的退路，也让验收
    只能事后比对。改成操作式后：**改了什么由操作决定**，模型没有「回抄」这一退路。

    2026-09-27 用户决定（合并方案第 2 步）：操作从「只能替换」扩到
    `replace` / `insert_after` / `split`，并改为**整批三步走**：

    ① **先检查**——全部操作对着**修改前**的快照验（种类、编号范围、一批内不冲突、
       正文自带原文依据、载体合法且不越界）；一条不过整轮拒收，不半途改一半；
    ② **再统一应用**——对快照做纯函数变换（`split` 原地展开、`insert_after` 顺插）；
    ③ **后重新编号**——编号 = **应用后**的位置（B1..B%d）。下游生成契约
       （`prompts.render_execution_contract` 的 `B%d` / `D%d` 标签）与候选层
       `plan_refs` 一律按位置渲染，故自动跟随；本函数接收的 `index` 只在**这一轮**
       对照模型看到的反馈有意义，**不得**跨轮复用。

    Returns `(new_plan, "")`；`(None, "no_change")` = 模型没给任何操作（**显式**，
    不再算一次有效重选）；其余为拒收原因。
    """
    from step01_script_gen.plan_feasibility import (
        BeatRecord, check_dialogue_representable, is_sentence_end_terminated,
        plan_dialogue_lines, source_contains)
    # 1b（2026-09-27 复核）：**解析失败 / 结构错误 / 真正的空操作**必须分开——
    # 前两者落成 `no_change` 会把「模型没按契约回」记成「模型认为无需改动」，
    # 诊断上彻底指错方向（而且会被当成一次有效结论存进 plan_replan）。
    if not isinstance(ops, dict):
        return None, "invalid_result: 重选结果不是 JSON 对象"
    for _key in ("dialogue_ops", "beat_ops"):
        _v = ops.get(_key)
        if _v is None:
            continue
        if not isinstance(_v, list):
            return None, "invalid_result: %s 不是数组" % _key
        if any(not isinstance(x, dict) for x in _v):
            return None, "invalid_result: %s 里有非对象条目" % _key
    _dlg = list(ops.get("dialogue_ops") or [])
    _bat = list(ops.get("beat_ops") or [])
    if not _dlg and not _bat:
        return None, "no_change"

    new = json.loads(json.dumps(plan, ensure_ascii=False))
    lines = list(plan_dialogue_lines(new))
    n_lines = len(lines)
    #: **修改前的快照**：本批全部 `index` 都对着它编号（节拍操作会改变条数与顺序，
    #: 若边改边编号，同一批里 `split` 之后的操作就会指到别的节拍上）。
    beats_in = list(new.get("shot_beats") or [])
    n_beats = len(beats_in)
    snapshot = [BeatRecord.parse(b) for b in beats_in]

    # ---- ① 台词操作：规范化 + 批内冲突检查 ---------------------------------
    _dlg_seen: dict = {}
    for op in _dlg:
        _i, _why_i = _coerce_op_index(op.get("index"), "D", "台词")
        if _why_i:
            return None, "dialogue_op：%s" % _why_i
        if not 1 <= _i <= n_lines:
            return None, "dialogue_op index %s 越界（共 %d 句）" % (op.get("index"), n_lines)
        if _i in _dlg_seen:
            # 一批里同一句被换两次 ⇒ 结果取决于应用顺序，不是**唯一记录**
            return None, "操作冲突：D%d 同时被多条台词操作替换（同一句至多一条）" % _i
        _dlg_seen[_i] = True
        _rep = str(op.get("replace_with") or "").strip()
        if not _rep:
            return None, "dialogue_op 第 %d 句的 replace_with 为空" % _i
        if not source_contains(novel_text, _rep):
            return None, "第 %d 句的替代未在原文逐字找到：%s" % (_i, _rep[:24])
        if not check_dialogue_representable(_rep)["representable"]:
            return None, "第 %d 句的替代不可单镜表示：%s" % (_i, _rep[:24])
        # 1a（2026-09-27 复核）：**逗号/顿号结尾的半句一律非法**——提示里明令禁止过
        # （实测模型两次都拿自己出处的逗号截断前缀当替代），但当时只有提示、没人拦：
        # 短半句是"可表示"的，能一路通过。判据与提示同源（`is_sentence_end_terminated`）。
        if not is_sentence_end_terminated(_rep):
            return None, ("第 %d 句的替代以「%s」收尾——逗号/顿号截断的半句一律非法"
                          "（成片里是没说完的话）：%s"
                          % (_i, _rep[-1] if _rep else "", _rep[:24]))
        lines[_i - 1] = _rep
    new["dialogue_lines"] = lines

    # ---- ② 节拍操作：**先整批检查**（范围/冲突/正文/载体），一条不过整轮拒收 ----
    #: index → {"edited": 改过它的操作种类 or None, "inserted": 是否已在其后插入}
    #: P1-3（2026-09-27 复核）：**两个槽位分别判**。「只记上一条操作种类」有洞——
    #: `replace → insert_after → replace` 时，第三条看到「上一条是插入」就被放行，
    #: 随后**覆盖**掉第一条 replace（改动被静默吞掉）。改该节拍 / 在它后面插入是
    #: **两件互不覆盖**的事，各自至多一次。
    _beat_seen: dict = {}
    _edit_ops: dict = {}          # index → (kind, [渲染好的节拍文本, ...])——改该节拍
    _insert_ops: dict = {}        # index → [渲染好的节拍文本, ...]——在该节拍**之后**插入
    for op in _bat:
        _kind = str(op.get("op") or "").strip() or BEAT_OP_REPLACE
        if _kind not in BEAT_OP_KINDS:
            return None, "beat_op 的操作种类非法：%r（只能是 %s）" % (
                _kind, " / ".join(BEAT_OP_KINDS))
        _i, _why_i = _coerce_op_index(op.get("index"), "B", "节拍")
        if _why_i:
            return None, "beat_op：%s" % _why_i
        # `insert_after` 的 0 号位 = 插到最前（其余操作必须指到已有节拍）
        _lo, _hi = (0, n_beats) if _kind == BEAT_OP_INSERT_AFTER else (1, n_beats)
        if not _lo <= _i <= _hi:
            return None, ("beat_op index %s 越界（%s 允许 %d..%d，共 %d 条节拍）"
                          % (op.get("index"), _kind, _lo, _hi, n_beats))
        _slot = _beat_seen.setdefault(_i, {"edited": None, "inserted": False})
        _is_insert = (_kind == BEAT_OP_INSERT_AFTER)
        if _slot["inserted"] if _is_insert else (_slot["edited"] is not None):
            _prev_kind = "insert_after" if _is_insert else _slot["edited"]
            return None, ("操作冲突：B%d 同时被「%s」与「%s」两条操作改动"
                          "（同一节拍至多一条**改动** + 一条**插入**——索引指"
                          "**修改前**的编号，同槽位重复时后一条会覆盖前一条）"
                          % (_i, _prev_kind, _kind))
        if _is_insert:
            _slot["inserted"] = True
        else:
            _slot["edited"] = _kind
        # 被改节拍的旧记录（`insert_after` 是**新增**一条，没有旧载体可沿用）
        _old = snapshot[_i - 1] if _kind != BEAT_OP_INSERT_AFTER else None
        if _kind == BEAT_OP_SPLIT:
            _parts = op.get("parts")
            if not isinstance(_parts, list) or len(_parts) < 2:
                return None, "第 %d 条节拍的 split 至少要给两段" % _i
            _rendered = []
            for _j, _p in enumerate(_parts, 1):
                if not isinstance(_p, dict):
                    return None, "第 %d 条节拍的 split 第 %d 段不是对象" % (_i, _j)
                # split 的每段都必须自己写载体（拆分的**目的**就是给两段不同载体，
                # 沿用旧载体等于没拆）——故不传 `old_record`。
                _txt, _why = _render_beat_op(_p.get("new_text"),
                                             _p.get("new_carrier"), None, n_lines,
                                             require_carrier=True)
                if _why:
                    return None, "第 %d 条节拍 split 第 %d 段：%s" % (_i, _j, _why)
                _rendered.append(_txt)
            _edit_ops[_i] = (_kind, _rendered)
        elif _kind == BEAT_OP_INSERT_AFTER:
            _txt, _why = _render_beat_op(op.get("new_text"), op.get("new_carrier"),
                                         None, n_lines, require_carrier=True)
            if _why:
                return None, "第 %d 条节拍（插入）：%s" % (_i, _why)
            _insert_ops[_i] = [_txt]
        else:
            _txt, _why = _render_beat_op(op.get("new_text"), op.get("new_carrier"),
                                         _old, n_lines)
            if _why:
                return None, "第 %d 条节拍：%s" % (_i, _why)
            _edit_ops[_i] = (_kind, [_txt])

    # ---- ③ 统一应用（对**快照**做纯函数变换；位置即**应用后**的编号）--------
    out: list = list(_insert_ops.get(0) or [])      # insert_after index=0 → 插到最前
    for _i, _b in enumerate(beats_in, 1):
        _edit = _edit_ops.get(_i)
        if _edit is None:
            out.append(_b)
        else:
            _kind, _rendered = _edit
            if _kind == BEAT_OP_SPLIT:
                out.extend(_rendered)               # 原地展开成多段
            else:
                out.append(_rendered[0])
        out.extend(_insert_ops.get(_i) or [])       # 插在该节拍**之后**
    new["shot_beats"] = out
    # 本批**改写过**的条目（改写的 + 插入的）——原文证据只核这些（见下方说明）
    _changed = ([t for _k, _ts in _edit_ops.values() for t in _ts]
                + [t for _ts in _insert_ops.values() for t in _ts])

    # 逐项保护必保留事实与事件顺序（操作只在旧计划上打补丁，结构上本就不会破——
    # 2026-09-27 复核要求把它作为**显式**验收写进代码，而不是靠"结构上不会"）
    _inv = _plan_invariants_intact(plan, new)
    if _inv:
        return None, _inv
    # 替换台词后**重新验算整张承载关系**（不能只验算字数）——2026-09-27 复核：
    # 重选可能把某句换掉、又把原指向它的节拍改成「载体：画面」，让那句**无人承载**；
    # 这种事不在计划层显形，会一路拖到候选层才被台词门整稿拒收。
    from step01_script_gen.plan_feasibility import carrier_relation_problems
    _dup, _orphan, _unlabeled = carrier_relation_problems(
        new.get("shot_beats"), len(plan_dialogue_lines(new)))
    if _dup:
        return None, "节拍载体标注重复：%s（每条节拍只许一个载体）" % "，".join(_dup[:4])
    if _unlabeled:
        return None, "节拍缺载体标注：%s（计划带了标注就必须每条恰有一个载体）" % "，".join(_unlabeled[:4])
    if _orphan:
        return None, "替换后 %s 无任何节拍承载（承载关系必须双向闭合）" % "，".join(_orphan[:4])
    # 只核**被改写的**条目：未改动的条目是既有状态（早就过了流水线），把整份计划
    # 一起核会把「原本就抄得不精确的旧条目」也算到本轮头上而误拒——操作式的语义
    # 是「代码在旧计划上打补丁」，责任范围就是补丁本身。
    if _changed:
        _why = _plan_evidence_intact({"shot_beats": _changed, "required_facts": []},
                                     novel_text)
        if _why:
            return None, _why
    return new, ""


def _plan_invariants_intact(old: dict, new: dict) -> str:
    """重排版本必须守住的不变量；返回 "" = 通过.

    2026-09-26 复核发现：只靠 `_plan_evidence_intact` 挡不住「**删掉一条必保留事实**
    或**调换事件顺序**，同时让剩下的条目都带合法引文」——那只核对了新稿里**仍然存在**
    的条目，对「少了什么 / 顺序变了没有」完全不看（实测：删事实 + 倒序 main_events
    仍判通过）。而 `_plan_core_preserved` 恰恰是因为这些字段**变了**才把控制权交过来，
    所以这一段必须逐项比对：

    - `main_events`：**逐项且同序**一致（事件顺序不得调换，事实不得删除）；
    - `required_facts`：旧条目**一条都不许少**（允许新增——重排只会让承载更明确）；
    - `climax`：不得改动。
    """
    _old = old or {}
    _new = new or {}
    if list(_old.get("main_events") or []) != list(_new.get("main_events") or []):
        return "main_events 被删除或调换了顺序"
    _miss = [f for f in (_old.get("required_facts") or [])
             if f not in (_new.get("required_facts") or [])]
    if _miss:
        return "必保留事实被删除 %d 条（如 %s）" % (len(_miss), str(_miss[0])[:26])
    if str(_old.get("climax") or "") != str(_new.get("climax") or ""):
        return "climax 被改动"
    return ""


def _plan_evidence_intact(plan: dict, novel_text: str) -> str:
    """重排过节拍/载体的**新计划版本**的原文证据核验；返回 "" = 通过.

    为什么需要：单镜可表示性触发的重选，**允许改动 `shot_beats` 的正文与载体**
    （2026-09-26 用户决定：冯检校的长句出处通篇没有句末标点，只能把该节拍按原文
    顺序拆成「画面动作」+「由别人在原文里的完整短句承载同一事实」）。这是对
    `_plan_core_preserved` 的**例外**，所以必须换一道**更严**的闸，而不是把核心字段
    校验放宽：新版本里**每一条节拍、每一条必保留事实都必须带逐字可核的原文引文**。

    引文按「省略号分段，任一段 ≥4 字逐字命中原文」判定——模型引用时惯用
    「前段……」（如 `"杨旭让人宰了……"`），整串包含会因为省略号残留而永远不命中
    （与 `_check_grounding` 的单字/截断问题同源），故按段取最长命中。
    """
    if not str(novel_text or "").strip():
        return "无原文可核（调用方未传原文）"
    from step01_script_gen.plan_feasibility import _source_norm
    novel_norm = _source_norm(novel_text)

    _MIN_RUN = 8      # 长引文：至少要有这么长的**连续**逐字命中
    _MIN_SHORT = 4    # 短引文：整段必须逐字命中；归一化后不足这个长度不算证据

    def _hit(text: str) -> bool:
        """引文（按省略号分段）是否**逐字有据**（每段单独判定，任一段成立即算）.

        为什么不用「整段包含」：模型抄引文经常少一两个字（实测章 2
        `"这人虽是一身寒酸…"`，原文是「**这**个人虽是…」——丢了开头一个「个」），
        整段包含会把这种**内容忠实**的引文判死，机制永远跑不动。
        为什么不用「5 字滑窗任一命中」（`validation._check_grounding` 的口径）：
        那是给**改写过的旁白**用的最松档，拿它当「节拍有没有原文依据」的闸太松，
        伪造的节拍靠常用词组就能蒙过去。取「≥8 字连续命中」——单一规则、可陈述，
        少抄一两字不误杀、编造节拍过不去。

        短引文（归一化后 4-7 字，如 `"住嘴！"`）走**整段**逐字：这类引文本来就短，
        再允许少抄字就等于不设闸；归一化后不足 4 字的（纯标点/两字词）**不算证据**，
        避免拿 `"好！"` 这种当原文依据。
        """
        for quoted in _EVIDENCE_QUOTE.findall(str(text or "")):
            for seg in re.split(r"…+|\.{2,}", quoted):
                seg = _source_norm(seg.strip())
                if len(seg) < _MIN_SHORT:
                    continue
                if len(seg) < _MIN_RUN:
                    if seg in novel_norm:
                        return True
                    continue
                if any(seg[i:i + _MIN_RUN] in novel_norm
                       for i in range(len(seg) - _MIN_RUN + 1)):
                    return True
        return False

    for i, beat in enumerate((plan or {}).get("shot_beats") or [], 1):
        if "原文依据" not in str(beat or ""):
            return "节拍 %d 缺「原文依据」（重排过的版本必须逐条给原文出处）" % i
        if not _hit(beat):
            return "节拍 %d 的原文依据逐字核不过：%s" % (i, str(beat)[:60])
    for i, fact in enumerate((plan or {}).get("required_facts") or [], 1):
        if "原文证据" not in str(fact or ""):
            return "必保留事实 %d 缺「原文证据」" % i
        if not _hit(fact):
            return "必保留事实 %d 的原文证据逐字核不过：%s" % (i, str(fact)[:60])
    return ""


def _check_outline_dialogue_grounding(outline: dict, novel_text: str) -> None:
    """大纲校验补充（v6.131，2026-09-25 收口）：选用的台词必须**逐字来自原文**.

    与 `plan_feasibility.check_plan_before_generation()` **同一条边界、同一套函数**：
    原来的 `_validate_outline` 只净化字段（strip/去空），不核实 `dialogue_lines` 是否真
    来自原文——计划因此还不是"内容取舍的权威"。这里不重复实现核验逻辑，只调用它；核验
    不过的台词**从大纲里剔除**（剔除后为空 → 交给生成前可行性门按 fail-closed 停止，
    本函数不抛异常：`_validate_outline` 的契约是"返回可用大纲或 None"）。

    无原文可核验时（调用方没传）原样返回——判定留给生成前那道门，不在校验层造第二个判据。
    """
    if not str(novel_text or "").strip():
        return
    lines = (outline or {}).get("dialogue_lines")
    if not isinstance(lines, list) or not lines:
        return
    try:
        from step01_script_gen.plan_feasibility import (
            plan_dialogue_lines, verify_dialogue_lines)
        kept, rejected = verify_dialogue_lines(plan_dialogue_lines(outline), novel_text)
    except Exception as exc:  # 核验器异常不得静默降级为"全部通过"
        print(f"[WARN] 大纲台词原文核验异常（保留原样，由生成前可行性门判定）: {exc}")
        return
    if not rejected:
        return
    outline["dialogue_lines"] = kept
    print("[WARN] 大纲选用台词 %d 句未在原文中逐字找到，已剔除：%s"
          % (len(rejected), "；".join(str(d.get("line"))[:20] for d in rejected)))


def _validate_outline(outline: dict | None, novel_text: str = "") -> dict | None:
    """大纲合法性校验（C2 修复 08-11）.

    校验项：
    1. main_events 有 3-6 条非空事件（空列表/全空串 → 不合格）
    2. climax 非空（高潮事件是分镜分布的锚点，空则锚点失效）
    3. required_facts / causal_chain 非空，shot_beats 至少覆盖每条事件
    4. 净化：列表元素 strip、过滤空串
    5. v6.131：`dialogue_lines` **逐字来自原文**核验（见 `_check_outline_dialogue_grounding`）
    不合格返回 None（调用方不注入空锚点，避免「【本章主要事件】」空标签进 prompt）。
    """
    if not isinstance(outline, dict) or not outline:
        return None
    events = outline.get("main_events") or []
    if isinstance(events, list):
        cleaned_events = [str(e).strip() for e in events if str(e).strip()]
        outline["main_events"] = cleaned_events
    else:
        cleaned_events = []
        outline["main_events"] = []
    if not 3 <= len(cleaned_events) <= 6:
        print("[WARN] 大纲校验失败：main_events 须有 3-6 条（不注入不完整大纲）")
        return None
    for field in ("required_facts", "causal_chain", "shot_beats"):
        values = outline.get(field)
        cleaned = ([item.strip() for item in values
                    if isinstance(item, str) and item.strip()]
                   if isinstance(values, list) else [])
        if not cleaned:
            print(f"[WARN] 大纲校验失败：{field} 为空（不注入不完整大纲）")
            return None
        outline[field] = cleaned
    if len(outline["shot_beats"]) < len(cleaned_events):
        print("[WARN] 大纲校验失败：shot_beats 未覆盖全部主要事件")
        return None
    climax = str(outline.get("climax") or "").strip()
    if not climax:
        print("[WARN] 大纲校验失败：climax（高潮事件）为空（不注入空大纲锚点）")
        return None
    outline["climax"] = climax
    # v6.131：`dialogue_lines` / `omittable` 只净化（去空/去空白），**不在这里判空**——
    # 「本集一句台词都没选用」由生成前可行性门按 fail-closed 停止（单一判据，不造第二个）。
    # 字段缺失时**不补空列表**：保持"缺字段"语义，诊断才能指出"计划不完整"而不是"没选台词"。
    for field in ("dialogue_lines", "omittable"):
        values = outline.get(field)
        if isinstance(values, list):
            outline[field] = [item.strip() for item in values
                              if isinstance(item, str) and item.strip()]
    _check_outline_dialogue_grounding(outline, novel_text)
    return outline


# 与 prompts.py「总时长硬约束」同源的规划目标（合法区间 60-90s，规划目标 70-80s）
_TARGET_MIN_SEC = 70
_TARGET_MAX_SEC = 80
# 匹配「30s」「30秒」「30 s」
_BUDGET_NUM_RE = __import__("re").compile(r"(\d+(?:\.\d+)?)(\s*[sS秒])")


def _calibrate_time_budget(budget: str) -> str:
    """把大纲的时间预算**校准到目标区间**（v6.116，审核）.

    实测缓存大纲写「对白约30s；动作约60s；反应约30s」≈ **120s**，而生成约束的规划
    目标是 **70-80s**——两个信号互相矛盾（`_validate_outline` 此前不检查这一项），
    模型会照着大纲超配时长。校准按**原比例**缩放到目标中点，其余文字不变；解析不出
    数字时原样返回（自由文本没有矛盾数字，注入无害）。
    """
    text = str(budget or "").strip()
    if not text:
        return ""
    nums = [float(m.group(1)) for m in _BUDGET_NUM_RE.finditer(text)]
    total = sum(nums)
    if not nums or (_TARGET_MIN_SEC <= total <= _TARGET_MAX_SEC):
        return text
    target = (_TARGET_MIN_SEC + _TARGET_MAX_SEC) / 2.0
    scaled = iter(round(n * target / total, 1) for n in nums)

    def _rep(m):
        try:
            v = next(scaled)
        except StopIteration:
            return m.group(0)
        return ("%g" % v) + m.group(2)

    return _BUDGET_NUM_RE.sub(_rep, text) + (
        "（已按目标 %d-%d 秒校准）" % (_TARGET_MIN_SEC, _TARGET_MAX_SEC))


def outline_to_anchor(outline: dict | None, novel_text: str = "") -> str:
    """把大纲 dict 转成可注入分镜 prompt 的锚点文本.

    C2 修复：内部先过 _validate_outline——无效大纲（空 main_events/空 climax）
    返回空串，不注入空标签。

    v6.131：`novel_text` 透传给 `_validate_outline`，使**任何**渲染锚点的路径都做同一次
    原文核验（GAP 重生/结构收敛/超限收敛各自渲染时不会再出现未核验的台词）。默认空
    = 已验证过（主流程在候选扇出前已核验并就地收窄），此时不重复核验。
    """
    outline = _validate_outline(outline, novel_text)
    if not outline:
        return ""
    parts = []
    if outline.get("main_events"):
        parts.append("【本章主要事件】" + "；".join(outline["main_events"]))
    if outline.get("emotional_arc"):
        parts.append(f"【情绪弧线】{outline['emotional_arc']}")
    if outline.get("climax"):
        parts.append(f"【高潮事件】{outline['climax']}")
    if outline.get("end_hook"):
        parts.append(f"【结尾钩子】{outline['end_hook']}")
    if outline.get("characters"):
        parts.append("【出场角色】" + "、".join(outline["characters"]))
    if outline.get("required_facts"):
        parts.append("【必须保留事实】" + "；".join(outline["required_facts"]))
    if outline.get("causal_chain"):
        parts.append("【因果链】" + "；".join(outline["causal_chain"]))
    if outline.get("character_motives"):
        parts.append("【角色动机】" + "；".join(outline["character_motives"]))
    if outline.get("dialogue_lines"):
        # v6.131（2026-09-25 收口）：本集**选用的逐字台词**必须进锚点——否则生成模型看不到
        # 计划选了哪些台词，仍会自行决定对白，"计划是内容取舍的权威"落不了地。这一组已过
        # 原文逐字核验（未通过的已被剔除，见 `_check_outline_dialogue_grounding`）。
        # 约束写进**标头**（标头不含 `；`）：条目区必须是纯台词，压缩剖面按 `；` 整条保留，
        # 混进说明文字会被切成假条目。
        parts.append("【选用台词·逐字，本集只拍这些，不得替换或另编；增减须回规划层】"
                     + "；".join(outline["dialogue_lines"]))
    if outline.get("shot_beats"):
        parts.append("【镜头节拍】" + "；".join(outline["shot_beats"]))
    # v6.116：预算先校准——大纲写 120s 而生成目标 70-80s 会让模型超配时长
    _budget = _calibrate_time_budget(outline.get("time_budget"))
    if _budget:
        parts.append(f"【时间预算】{_budget}")
    return "\n".join(parts)
