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
from _text_gen.deepseek_schema import OUTLINE_SCHEMA


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
        from step01_script_gen.plan_feasibility import representable_replacements
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
            _reps.append("- 「%s」→ %s" % (
                _line[:34],
                "；".join("「%s」" % c for c in _cands) if _cands else
                "**出处通篇没有句末标点，没有可表示替代 ⇒ 按第 4 条重排载体"
                "（禁止改派说话人、禁止逗号截断半句）**"))
        block += _PLAN_REPRESENTABILITY_FEEDBACK.format(
            n=len(_unrep), cap=active_shot_cap(), lines_block=_lines_block,
            replacements_block="\n".join(_reps))
    # v6.138（九审根因 1）：节拍载体悬空反馈——重选删承载台词必须同步更新载体标注
    _dangling = rep.get("beat_carrier_dangling") or []
    if _dangling:
        block += _PLAN_CARRIER_FEEDBACK.format(
            n=len(_dangling),
            lines_block="\n".join("- %s" % d for d in _dangling))
    if block:
        # ⚠️ 这句是模型**最后**读到的指令，必须与上面各块的口径一致：可表示性块开了
        # 「允许重排 shot_beats 正文与载体」的口子，这里若仍写「请原样返回」，等于
        # 让模型收到两句相反的话——实测首版就是这么被顶回去的（模型原样返回、
        # `_plan_core_preserved` 直接通过，例外分支根本没被走到）。
        _free = ("台词/可省略/时间倾向" if not _unrep else
                 "台词/可省略/时间倾向，以及**按上面第 4、5 条重排的 `shot_beats` "
                 "正文与载体**（重排后的每条节拍都要带逐字可核的「原文依据」）")
        block += ("\n## 上一版计划（除%s外，请原样返回）\n" % _free
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
    _base = base_prompt or OUTLINE_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS])
    _prompt = _base + "\n" + _budget_feedback_block(rep, plan, novel_text)
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=4000, temperature=0.3,
            json_schema=OUTLINE_SCHEMA, model=model))
    except Exception as exc:
        print(f"[WARN] 规划重选调用异常（保留首版计划）: {exc}")
        return None, "call_failed: %s" % exc
    if not result.success:
        print(f"[WARN] 规划重选失败（保留首版计划）: {result.error}")
        return None, "call_failed: %s" % result.error
    data = _parse_outline_json(result.text)
    new_plan = _validate_outline(data, novel_text) if isinstance(data, dict) else None
    if not new_plan:
        print("[WARN] 规划重选结果不合法（保留首版计划）")
        return None, "invalid_result"
    _broken = _plan_core_preserved(plan, new_plan)
    if _broken:
        # 2026-09-26 用户决定：**单镜可表示性**触发的重选，允许重排节拍与载体
        # （冯检校那句出处通篇没有句末标点，只能把该节拍拆成「画面动作」+
        # 「由别人在原文里的完整短句承载同一事实」）。这是 `_plan_core_preserved`
        # 的例外，所以**换一道更严的闸**而不是放宽它：新版本里每一条节拍、
        # 每一条必保留事实都必须带逐字可核的原文引文（`_plan_evidence_intact`）。
        # 预算超限触发的重选**不享受**这个例外——那条路仍然只许改台词。
        if rep.get("dialogue_unrepresentable"):
            # 先守不变量（事实不得删除、事件顺序不得调换），再核原文证据——
            # 顺序不能反：证据闸只看新稿里**还在**的条目（2026-09-26 复核实测）。
            _inv = _plan_invariants_intact(plan, new_plan)
            if _inv:
                print("[WARN] 重选破坏了计划不变量（%s）——不接受" % _inv)
                return None, "core_field_changed: %s（%s）" % (_broken, _inv)
            _why = _plan_evidence_intact(new_plan, novel_text)
            if not _why:
                print("[PLAN] 重选按「可表示性」重排了节拍/载体（核心字段「%s」有改动）"
                      "——已通过原文证据核验，作为**新计划版本**接受" % _broken)
                return new_plan, ""
            print("[WARN] 重选改动了核心字段「%s」且原文证据核验不过（%s）——不接受"
                  % (_broken, _why))
            return None, "core_field_changed: %s（%s）" % (_broken, _why)
        print("[WARN] 规划重选改动了核心字段「%s」——不接受（必保留事实/事件顺序必须原样）"
              % _broken)
        return None, "core_field_changed: %s" % _broken
    return new_plan, ""


def _rotate_outline_cache_on_regen(cache_path, new_key, new_outline) -> None:
    """重生成覆盖大纲缓存前：旧计划留 `.bak`（单文件轮换）+ 选材退化告警（v6.138.1）.

    缓存键含 outline prompt——**规则演进必然整批失效重生成**，而重新抽样可能显著
    变差（2026-09-26 实证：6 句→2 句，成片 38s 撞 duration_budget 硬门；且计划一换，
    全部历史快照因选材门失格，跨 run 收敛棘轮归零）。留档让退化可恢复；是否采用
    新计划仍由运行者裁决——**内容取舍不代决**，这里只保证可见。
    """
    from step01_script_gen.plan_feasibility import plan_dialogue_lines
    try:
        old = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(old, dict) or old.get("source_hash") == new_key:
        return
    old_outline = old.get("outline") if isinstance(old.get("outline"), dict) else None
    _old_n = len(plan_dialogue_lines(old_outline))
    _new_n = len(plan_dialogue_lines(new_outline))
    try:
        Path(str(cache_path) + ".bak").write_text(
            json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    if _new_n < _old_n:
        print(f"[WARN] 大纲缓存因规则变化重生成：选用台词 {_old_n} 句 → {_new_n} 句"
              f"（新计划选材变少，成片时长下界随之下降；旧计划已留档 {Path(str(cache_path) + '.bak').name}，"
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
    if (isinstance(_prev, dict) and _prev.get("result") in ("infeasible", "replan_failed")
            and not _force_replan()):
        print("[PLAN] 这一版计划已重选过一轮（%s）→ 不再重复重选（同一计划版本只重选一次）；"
              "如需重跑该轮：%s=1" % (_prev.get("result"), FORCE_REPLAN_ENV))
        return plan, dict(_prev, skipped="already_attempted")
    if isinstance(_prev, dict) and _force_replan():
        print("[PLAN] %s=1 → 忽略已记录的重选结论（%s），重跑这一轮"
              % (FORCE_REPLAN_ENV, _prev.get("result")))
    print("[PLAN] 首版计划不可行（对白 %.2fs / %d 句，上限 %.0fs；单镜不可表示 %d 句）"
          "→ 带数字反馈重选台词（%d 轮）"
          % (rep["dialogue_sec"], rep["dialogue_lines"], rep["budget"]["max"],
             len(rep.get("dialogue_unrepresentable") or []), _REPLAN_MAX_ROUNDS))
    info = {"rounds": _REPLAN_MAX_ROUNDS,
            "before_dialogue_sec": rep["dialogue_sec"], "before_lines": rep["dialogue_lines"]}
    new_plan, _why = replan_dialogue_within_budget(plan, rep, novel_text, model=model,
                                                   base_prompt=base_prompt,
                                                   system_prompt=system_prompt)
    if new_plan is None:
        # "重选没跑成"（调用/解析/校验失败）与"重选后仍超预算"是**两回事**：
        # 前者没有判定结论，不能据此说本集需拆集。
        info["result"] = "replan_failed"
        info["detail"] = _why
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
