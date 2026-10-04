"""step01_script_gen.outline - 章节大纲生成（ShortVideoDirector director-outline 吸收）.

在分镜生成前，先用 LLM 生成本章大纲（主要事件/角色出场/情绪弧线/集尾钩子），
注入分镜 prompt 作为「大纲锚点」，让分镜生成参考大纲，保证冲突/高潮分布。
"""
from __future__ import annotations

import copy
import json
import hashlib
import os
import re
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from _text_gen import create_service, TextGenerationRequest
from _text_gen.deepseek_schema import OUTLINE_SCHEMA, REPLAN_OPS_SCHEMA
from step01_script_gen.plan_feasibility import (PLAN_CONTRACT_VERSION,
                                                normalize_plan_ranges,
                                                plan_contract_problems)
from step01_script_gen.plan_revision import DEFAULT_MAX_CALLS as _REVISION_MAX_CALLS
from step01_script_gen.plan_revision import failure_detail, residual_class


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
  "hook_spoken": ["钩子里必须由角色说出口的那句逐字台词（没有就给空数组 []）"],
  "hook_carrier": "B9/台词4（hook_spoken 每一句的承载节拍；没有就给空串）",
  "key_scenes": ["关键场景1", "场景2", ...],
  "required_facts": ["事实A｜原文证据：“原文原句”｜承载：B2/台词1",
                     "事实B｜原文证据：“原文原句”｜承载：B7/画面"],
  "causal_chain": ["起因→行动→结果→悬念"],
  "character_motives": ["角色：想做什么；为什么；原文证据：..."],
  "shot_beats": ["节拍：...｜本镜信息目的：...｜原文依据：...｜载体：台词k 或 画面"],
  "dialogue_lines": ["逐字台词1（原文原话）", "逐字台词2"],
  "narration_lines": [{{"text": "<原文里的叙述原句，逐字照抄>",
                      "source": "<这句旁白出自的那一句原文，逐字照抄，且包含上面的正文>",
                      "fact_refs": [1]}}],
  "omittable": ["可省略内容1", "..."],
  "time_budget": "对白/动作/反应的大致分配（不必报精确秒数）"
}}

# 规则
- main_events: 按时间顺序列出 3-6 个主要事件，覆盖全章 = **本集的事件顺序**
- required_facts: **必保留事实**（"必保留"的唯一清单），不得省略，必须附原文证据；
  **一条事实只写一件事**（一个命题）：身份、年龄、来历、继业、功名等要**各自成条**，
  不要把几件事并成一条；该条的「原文证据」必须**覆盖这条的全部内容**（引文覆盖不到的
  部分，没有任何机制能证明它被承载）
  **每条还要声明承载**（写在条目末尾）：`｜承载：B节拍号/台词k` = 由第 k 句选用台词说出口，
  `｜承载：B节拍号/画面` = 由该节拍的**画面**演出。承载必须是**真的演出这条事实**的那条节拍：
  代码会拿这条事实（含原文证据）与承载节拍的**正文**（镜头真的拍什么）或**选用台词**比对
  ——① 只把事实引文抄进节拍的「原文依据」**不算**演出；② 只在「本镜信息目的」里写上这条
  事实、**正文里没有人名/地点/动作等要素**，也**不算**演出（声明不能自证），会被拒收。
  画面承载的节拍必须标「载体：画面」，台词承载的节拍必须标「载体：台词k」。
  **交代类事实**（人物身份/年龄/来历）的合法出路只有三条：① 把它写进某条节拍的**正文**
  （让镜头真的拍出来）；② 在 `dialogue_lines` 里选一句**人物台词**把它说出口
  （逐字来自原文；**旁白/叙述句不算台词**，不得把叙述文字改标成台词），并按编号声明承载；
  ③ 本集确实拍不出也说不出口，就**不要列进 required_facts**。
  **第三种承载：图外旁白**（2026-09-30 复核第 6 轮裁决）——原文**叙述**里必须让观众知道、
   **画面又拍不出来**的内容（身份、年龄、来历、前情），可以由图外旁白讲出来：把它写成
  `narration_lines` 里的一条（**原文叙述的完整句子，逐字**），让某条节拍标 `载体：旁白k`，
  事实那条写 `｜承载：Bn/旁白k` 并在旁白条目的 `fact_refs` 里登记同一个序号（**两处必须
  互相登记**，只写一侧会被拒收）。`source` 是**出处**：必须能在原文里逐字找到，并且
  **包含**这条正文（正文就出自它）——代码会拿它跟原文核对，随手填一句原文当出处会被
  拒收；`fact_refs` 必须写**整数**序号（`2`，不是 `"2"`／`2.0`）。
  ⚠️ 上面 JSON 里 `<...>` 括起来的是**占位符**，照抄会被拒收（实测有模型把示例文字
  当成内容填进去）——`source` 必须换成原文里真实存在的那一句。
  旁白与画面**同时发生**，不需要为它额外加镜头；但它和台词一样**占用发声时长**，
  条数要与预算相称。**不得**把叙述句伪装成人物台词（那会被台词门拒收），
  也不得自造旁白——旁白必须逐字来自原文叙述。
  **旁白不是"什么都能装"的兜底**：年龄、字号、家世、功名这些仍要按故事需要**取舍**
  （该省就省），不能先把它们都转成旁白、再靠加时长容纳。
  **先定表达方式，再登记事实**（2026-09-30 复核第 5 轮：实跑的计划反复把身份/年龄/来历
  登记成必保留事实，却没有任何画面或台词真的表达它，于是每次都在计划层被打回）——
  按这个顺序做，不要反过来：
    1) 先想清楚这条事实**这一集到底怎么让观众知道**：哪个镜头**拍到**了什么、
       或者哪句**人物台词**说出口。想不清楚就说明它拍不出来；
    2) 再把它写成一条节拍的**正文**（或选进 `dialogue_lines`），让那条节拍是真的在演它；
    3) **最后**才把它登记进 `required_facts` 并标承载——承载指向的就是第 2 步那条节拍/那句台词。
  反面形态（一律会被拒收）：几条事实**都**指向同一个建立镜（例如都写 `承载：B1/画面`，
  而 B1 的正文只是环境交代）；把「两人**同框/同场**」当成「两人是**什么关系**」的演出
  ——同框只证明同处一景，证明不了身份、年龄、经历这类具体事实。
- dialogue_lines: 本集**选用的逐字台词/独白**，从原文**逐字摘出**（不改写、不润色、不自造）；
  只收**人物说出口的话**——原文里的**叙述句（旁白）不算台词**，**不得把叙述文字改标成台词**
  充数（旁白由镜头自己的旁白承担，见下条）；只收真正要拍的句子，宁少勿滥——每一句都占用
  时长，而时长由代码逐句估算（自报秒数不参与判定）
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
  第三条合法值是 `载体：旁白k`（由 `narration_lines` 第 k 条旁白讲出来）。
  一个节拍只能有一个载体；之后重选台词增删时，受影响节拍的载体必须同步更新。
  载体**必须写单值**：`载体：台词3`、`载体：旁白2`——**不许写区间**（`台词1-2`、
  `旁白1-5` 都是无效登记，会被判「载体非法」打回）。要表达"这条声音由连续几个镜头
  承载"，就写到**一条**节拍上（生成阶段会按句末展开成连续镜头），不要拆成多条。
  一条节拍**同时**要承载画面与台词时：{beat_edit_rule}
  **时间语义**（预算与顺序都按这一口径读）：`载体：台词k／旁白k` 的节拍与那段声音
  **同时发生**，不额外占画面时间，所以**拆声音节拍不会增加画面下界**；`载体：画面`
  的节拍才各自独占 `MIN_SHOT_DURATION` 秒画面时间。
  **画面与声音并行**：节拍标了「载体：台词k／旁白k」时，它的画面照样拍它自己的内容
  ——声音与画面**同时发生**，**不要**为了一句台词或一条旁白额外加镜头。
  **旁白跨镜连续播放**：一条旁白比一个镜头长时，让**承载它的那条节拍**在生成阶段
  展开成 2-4 个镜头（与台词同一个做法），按句末把这条旁白分句讲完，代码按
  「从承载它的那一镜起的**连续镜头片段**」核验，**不截句**。
  **同镜多段声音**：同一镜里既有台词又有旁白时，该镜同时声明**两条**节拍
  （一条「载体：台词k」、一条「载体：旁白k」）——声音条目各由自己的节拍承载。
- time_budget: 只写大致的分配倾向（**不必**报精确秒数——时长由代码按 dialogue_lines 逐句估算，
  自报秒数不参与判定）；对白与**同时发生**的画面动作不要重复计时
- emotional_arc: 一句话概括情绪走向
- climax: 全章最震撼/冲突最强的事件
- end_hook: 本章结尾已有的悬念，必须有原文依据；不得为了另拍钩子视频虚构事件
- hook_spoken: 钩子里**必须由角色说出口**的台词（如「喃喃自语『…』」里的那句），
  **逐字**抄自原文；它必须是 dialogue_lines 的**整句子集**（只说开头几个字不算），
  并且每一句都要在 `hook_carrier` 里声明承载节拍——那条节拍自己要标「载体：台词k」。
  引文只是被**提到**（如「他口中的那句话暗示…」）、或钩子本身是画面/悬念叙述 → 空数组。
  这一条不再靠「像不像说话」去猜：写进这个字段的，就是**必须拍出来说出口**的
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
#: 首版提示里"一条节拍同时要承载画面与台词时怎么办"这一句的**按轮次取值**
#: （复核之十二 P2 的实跑根因）：首版是**从零设计**，拆成两条是正解；而**修订轮**
#: （完整计划 / 预算整份重写）的 `shot_beats` 由上一版**冻结**（条数与正文逐项比对），
#: 照首版那句"必须拆成两条"做 ⇒ 每轮都被 `core_field_changed: shot_beats` 拒收
#: （实测：章 2 连续 3 轮）。
_BEAT_EDIT_RULE = {
    "first_version": ("必须**拆成两条**（画面一条、台词一条）——承载登记要求「这条节拍是"
                      "画面还是台词」唯一。"),
    "revision": ("**本轮不许增删/拆分/合并节拍**——画面与台词/旁白必须分属**不同节拍**，"
                 "只能把内容挂到**已有**节拍上（改末尾的「｜载体：…」登记）；确实只有改"
                 "节拍才能成立时，本轮无法完成，如实回报。"),
}


#: 这一轮**回什么**（`_replan_route` 的**唯一裁决**的取值，也是证据里 `route` 的取值）：
#: `"ops"` = 只回操作（`REPLAN_OPS_SCHEMA`，代码在旧计划上应用）；`"full_plan"` = 回
#: **一整份新计划**（`OUTLINE_SCHEMA` + 逐字基线 + 核心内容守卫）。schema、反馈块、
#: 应用器**都消费这一个值**（复核之十二 P1）。
ROUTE_OPS = "ops"
ROUTE_FULL_PLAN = "full_plan"

#: 重选**允许**改动的字段；其余字段必须原样返回（必保留事实与事件顺序不得变）
#: 契约**元数据**（版本戳）：不是内容、不参与「核心字段被改动」的比对——
#: 它由 `_validate_outline` 统一盖上，重选前后的两份计划本来就该是同一个值；
#: 若因迁移路径一份有一份没有，也不该判成「模型改了核心字段」。
_REPLAN_FREE_FIELDS = ("dialogue_lines", "narration_lines", "omittable", "time_budget",
                       "plan_contract_version")

#: 同样**允许改**的登记键（复核之十一 P1）："哪几句必须说出口 / 钩子由谁承载"是登记
#: 信息，不是事实本体——重排内容取舍时它必须跟着改。与 `_REPLAN_FREE_FIELDS` 一起构成
#: 「这一轮能改什么」的**唯一来源**：规则块由它渲染，守卫按它放行。
_REPLAN_DECL_FIELDS = ("hook_spoken", "hook_carrier")
#: 只在这些字段的**末尾**放开承载登记的字段（正文与「原文依据/原文证据」逐字不变）
_CARRIER_REGISTRATION_FIELDS = ("required_facts", "shot_beats")
#: 不参与比对的**运行时投影**（重选结论等派生状态——不是计划内容，也不是模型给的）
_REPLAN_IGNORED_KEYS = ("plan_replan",)


def _replan_editable_keys() -> set:
    """这一轮**可以改**的字段（规则块与守卫共用的唯一来源）."""
    return (set(_REPLAN_FREE_FIELDS) | set(_REPLAN_DECL_FIELDS)
            | set(_REPLAN_IGNORED_KEYS))


def replan_protected_fields() -> tuple:
    """**必须逐字保留**的字段（单一来源）：首版 JSON 契约的必需键 − 允许改的字段.

    复核之十一 P1 的教训：旧写法把"必须逐字保留"**写死在提示里**（只列 5 个键），守卫
    也只比对那 5 个键，而请求里**没有携带原值**——两份只有 `causal_chain` 不同的计划
    发出**完全相同**的请求，模型照抄都不知道该抄哪个值（实测：同一份响应对其中一份
    通过、对另一份报 `core_field_changed: causal_chain`）。现在三样东西同一个来源：
    ① 请求里附**逐字基线**（`render_plan_baseline`）；② 规则块由本函数渲染；
    ③ 守卫按本函数（的补集）比对。
    """
    _editable = _replan_editable_keys()
    return tuple(sorted(_f for _f in _plan_required_fields() if _f not in _editable))


def render_protected_fields_rule() -> str:
    """渲染「必须逐字保持不变」的字段清单（与守卫**同一份口径**，见 `replan_protected_fields`）."""
    _prot = list(replan_protected_fields())
    _carrier = [_f for _f in _prot if _f in _CARRIER_REGISTRATION_FIELDS]
    _plain = [_f for _f in _prot if _f not in _CARRIER_REGISTRATION_FIELDS]
    _lines = []
    if _plain:
        _lines.append("- %s——取值**一字不许改**；"
                      % "、".join("`%s`" % _f for _f in _plain))
    if _carrier:
        # **条数也要说**（复核之十二 P2）：守卫比的是**整张表**（逐项 + 条数），旧措辞只说
        # "正文与依据"，而模型最常做的修补恰恰是"补一条承载某句的节拍 / 把一条拆成两条"
        # ——实测 3 轮都因此被 `core_field_changed: shot_beats` 拒收。规则说清"表不能变长
        # 变短"，模型才有机会改走承载登记或改选材。
        _lines.append("- %s 的**正文、「原文依据/原文证据」与条数**——末尾的「｜承载：…」"
                      "「｜载体：…」**登记**可以跟着改（**不许新增/删除/拆分/合并条目**）。"
                      % "、".join("`%s`" % _f for _f in _carrier))
    return chr(10).join(_lines)


def render_protected_fields_inline() -> str:
    """同上一份清单的**单行**写法（预算重选的反馈块用）；字段来源完全相同."""
    _prot = list(replan_protected_fields())
    _carrier = [_f for _f in _prot if _f in _CARRIER_REGISTRATION_FIELDS]
    _plain = [_f for _f in _prot if _f not in _CARRIER_REGISTRATION_FIELDS]
    _txt = "、".join("`%s`" % _f for _f in _plain)
    if _carrier:
        _txt += ("；%s 的**正文、「原文依据/原文证据」与条数**（末尾承载登记可以跟着改，"
                 "条目不许增删）"
                 % "、".join("`%s`" % _f for _f in _carrier))
    return _txt


def render_plan_baseline(plan) -> str:
    """当前计划的**逐字基线**（完整计划修订 / 预算重选的请求都带它）.

    复核之十一 P1：规则要求"逐字保留"，就必须让模型**看得见**要保留的原值——
    "照抄旧值"是这一轮唯一被允许的省事做法，请求里少了它，模型只能靠猜。
    """
    return chr(10).join([
        "", "【当前计划：**逐字基线**（下面就按这份回，别改 `必须逐字保持不变` 的字段）】",
        "```json",
        json.dumps(plan, ensure_ascii=False, indent=2),
        "```"])

#: 强制重跑这一轮重选的开关（用户 2026-09-25：不自动重试，但要有**明确的重新规划入口**）.
#: 置 1 = 忽略计划里已记录的重选结论，重新跑一轮；默认空 = 沿用已记录的结论。
FORCE_REPLAN_ENV = "STEP01_FORCE_REPLAN"


def _force_replan() -> bool:
    return os.getenv(FORCE_REPLAN_ENV, "").strip() == "1"

_PLAN_BUDGET_FEEDBACK = """
# ★ 重选本集台词（预算反馈；本轮**只改台词**）

代码按**逐句时长估算器**核算了你上一版计划选用的台词（**不采信自报秒数**）：
- 已过原文逐字核验的台词：{lines} 句，估时 **{dialogue_sec:.2f} 秒**
- 已过原文逐字核验的**图外旁白**：{narr_lines} 条，估时 **{narration_sec:.2f} 秒**
- 本集正片**合法 {bmin:.0f}-{bmax:.0f} 秒**，**规划目标 {tmin}-{tmax} 秒**（画面动作、反应、
  过场都要占时间）
- 节拍 {beats} 个（画面/台词/旁白各有归属）——**节拍不是时间下界**：生成契约里
  「镜数由时长预算决定，不由节拍数决定」，节拍多不等于装不下、节拍少也不等于装得下；
  **唯一**能算时间的是上面那条发声估时

⇒ **发声内容（对白 + 旁白）**一项就 {dialogue_sec:.2f} 秒，**本集按当前选材装不下**。
请重新取材（复核第 7 轮：对白与旁白**共用同一份发声时长预算**，砍不掉旁白就装不下）：

1. **只从原文逐字摘**真正必须拍的对白——不改写、不润色、不自造、不拿旁白当台词；
2. 按**规划目标**把发声估时压到 **{tmax} 秒以内**：
   - 对白可以少选，每场只留最关键的一两句；
   - **旁白也可以少讲、甚至不讲**——旁白不是"什么都能装"的兜底，年龄、字号、家世、
     功名这些仍按**故事需要取舍**（该省就省），不能靠加时长容纳。
     删旁白要给 `narration_lines` 的**整表**（少给 = 只留这些）；被删旁白原先承载的
     事实**必须改由画面/台词承载**——`required_facts` 一条都不许删（删了会被拒收，
     这是本轮的核心内容守卫）；节拍上的
     「载体：旁白k」与事实上的「承载：B…/旁白k」必须同步改掉（编号会整体前移）；
3. **其余字段必须与上一版完全一致**（{protected}）：
   必保留事实一句都不能删，事件顺序不许改——**改了就会被拒收，这一轮白费**；
   （本行清单与代码里的守卫**同一份来源**：它列出的字段，守卫就逐项比对。
   {baseline_note}）
   唯一例外是**承载登记**（它是登记信息，不是事实本体，换台词就必须跟着改）：
   ① `shot_beats` 的「｜载体：…」标注（被删台词承载的节拍改标保留台词或「载体：画面」；
   节拍本体不许动）；② `required_facts` 每条末尾的「｜承载：B{{节拍号}}/台词k 或 画面」
   （**事实本体与它的「原文证据」一字不许改**）；③ 顶层 `hook_spoken` 与 `hook_carrier`
   （钩子那句必须整句留在 dialogue_lines 里；确实不再要求它说出口时，`hook_spoken` 给空数组、
   `hook_carrier` 给空串）；
4. {plan_fields_note}
"""

#: **与接口绑定**的措辞（复核之十二 P1）：同一份模板，按**本轮裁决的接口**渲染——
#: "这一轮能改什么 / 请求里那份计划该怎么用"必须与 schema / 应用器**一致**，否则模型
#: 照办也会被守卫拒收（`ops` 轮由代码在旧计划上打补丁：改节拍正文要带逐字原文依据，
#: 旧计划是**只读上下文**；`full_plan` 轮必须整份回、**节拍正文与原文依据一字不许改**
#: ——核心守卫比对的就是它，旧计划是**逐字基线**）。
_INTERFACE_ADVICE = {
    ROUTE_OPS: {
        "reorder_rule": "把该节拍拆成「画面动作镜」+「由**别人**在原文里"
                        "指向同一事实的完整短句承载」，两个动作的顺序必须与原文一致；",
        "beat_edit_rule": '**本轮允许改动 `shot_beats` 的正文与载体**（这是唯一例外）：'
                          '但每条节拍必须带「原文依据："…"」，引文要**逐字复制原文**'
                          '（至少连续 8 字逐字命中，少抄字会核不过）；',
        "carrier_escape": "用 `beat_ops` 把该节拍改成画面承载、该事实由别的合法短句承载",
        "baseline_note": "请求末尾附了**当前计划全文**（只读上下文——这一轮只回操作，"
                         "不要把它整份重写回来）。",
        # 复核之十二 P1：**做不到时的表达方式**也必须按接口说——操作协议里没有
        # `time_budget` 这个键，旧文案让模型去写标记，照做就被判「未知操作键」
        # （真实修订入口抓到过：`replan_ops` + "请在 `time_budget` 里写明…"）。
        "cannot_do": "若确实没有任何合法修法，就**回空操作**（`dialogue_ops`/`beat_ops` "
                     "给空数组、`fact_carriers` 给空数组；`hook_carrier`/`hook_spoken`/"
                     "`narration_lines` **整个键省略** = 不改）——这一轮会如实记成 "
                     "`no_change`（规划层认为没有可改之处），代码据此停下并把原因交还用户。"
                     "**不要**去写 `time_budget` / `omittable` 这类操作协议**碰不到**的字段"
                     "（会被判「未知操作键」，整轮白费）。",
        # 扫描用例抓到的第二处：`omittable` 同样**不在**操作协议里（可改字段只有
        # `dialogue_lines`/`shot_beats`/`required_facts`/`hook_carrier`/`hook_spoken`/
        # `narration_lines` 六项）——旧稿让模型"顺带更新 `omittable`"，照做同样是未知操作键
        "plan_fields_note": "`omittable` / `time_budget` / `plan_contract_version` 这类"
                            "字段操作协议**碰不到**，本轮不要写它们（要改选材就改 "
                            "`dialogue_lines` / `narration_lines`）。",
    },
    ROUTE_FULL_PLAN: {
        "reorder_rule": "把承载该节拍的「｜载体：…」改标成新选的那句台词或「画面」"
                        "（`shot_beats` 的**正文、顺序与条数一律不许改**）；",
        "beat_edit_rule": '**本轮 `shot_beats` 的正文与「原文依据」一字不许改**'
                          '（改了会被核心守卫拒收）——只许改末尾的「｜载体：…」登记段；',
        "carrier_escape": "把承载该节拍的「｜载体：…」改成「画面」、该事实由别的合法短句承载"
                          "（`shot_beats` 的正文不许动）",
        "baseline_note": "请求末尾附了**逐字基线**，照抄即可。",
        "cannot_do": "若原文确实没有能承载这些事实的合法台词与节拍，请在 `time_budget` 里"
                     "**明确写明**「台词不可表示：无合适替代」——代码会据此显式停止，"
                     "把内容取舍交还用户，而不是硬拆。",
        "plan_fields_note": "顺带更新 `omittable`（可省略内容）与 `time_budget`"
                            "（倾向性文字，不必报精确秒数）。",
    },
}

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
   而要**按原文先后顺序重排载体**——{reorder_rule}
5. {beat_edit_rule}
   **必保留事实**（`required_facts`）每条的「原文证据："…"」同样要逐字可核。
   **没有原文证据的节拍/必保留事实一律拒收**；
6. `main_events` 不得删除，**事件顺序**不得调换；
7. {cannot_do}
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
5. {cannot_do}
"""

#: 规划层在 `time_budget` 写了「无合适替代」时给模型的反馈（复核之十一：此前**没有**这块，
#: 于是这类计划收到的整份重写要求既没有原因、也没有旧计划）。判据由生产者记录触发
#: （`evaluate_plan` 的 `budget_no_alternative`），本块只解释**合法出路**。
#:
#: 复核之十二 P1：本块原来写着「`required_facts` 可以少条目，连同节拍一并取舍掉」——
#: 而核心守卫（`_plan_core_preserved`）要求事实本体与节拍正文、条数**逐项不变**。
#: **建议与守卫矛盾**的后果是实打实的：模型照反馈减少一条事实，随后被判
#: `core_field_changed: required_facts`（本轮实测复现）。已定裁决是**修反馈、保持守卫**：
#: 内容取舍只到"选哪几句台词"为止，必保留事实是用户给的硬边界。
_PLAN_NO_ALTERNATIVE_FEEDBACK = """
# ★ 规划层自己报告：不可表示的台词**无合适替代**（`time_budget` 标记）

代码**不代决内容取舍**。这一轮请给出**一整份新计划**，走其中一条：

1. **换承载方式**：把该事实改由**画面**承载（承载它的那条节拍的正文必须真的演出这条
   事实），或换成另一句**能在一镜里讲完**的原文台词（逐字原文、以句末标点收尾）；
   并同步改掉节拍末尾的「｜载体：…」与事实末尾的「｜承载：…」登记
   ——节拍**正文与原文依据一字不许改**，只许改末尾那一段登记；
2. **把装不下的那句从选材里去掉**（`dialogue_lines` 可以少条目）：但该事实**必须改由
   画面或别的合法短句承载**——`required_facts` 一条都不许删，`main_events` 与事件顺序
   也不许改（改了会被拒收，这一轮白费）。

**只把 `time_budget` 里那句标记删掉不算修好**：验收看的是内容取舍与承载关系，
不是那句标记。原文里确实找不到任何一种合法承载时，**保留这条标记**是**合法终局**——
生成前的门会据此显式停止，把内容取舍（删情节/换场次/拆集）交还用户。
"""

#: 规划层在 `time_budget` 写了「载体缺失：无合适承载」时给模型的反馈（同上）。
_PLAN_NO_CARRIER_FEEDBACK = """
# ★ 规划层自己报告：节拍**载体缺失、无合适承载**（`time_budget` 标记）

代码**不代决内容取舍**。两条合法出路：

1. **给出合法承载**：把这些节拍改标为仍保留的台词（`载体：台词k`）或 `载体：画面`
   ——**节拍正文与原文依据一字不许改**，只许改末尾的「｜载体：…」段；
2. **实在找不到承载**：保留这条标记——生成前的门会据此显式停止，把内容取舍交还用户
   （这是**合法终局**，不是失败）。

**只删标记不改内容不算修好**（验收看的是承载关系，不是那句标记）。
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


def _budget_feedback_block(rep: dict, plan: dict, novel_text: str = "",
                           route: str | None = None) -> str:
    """把**代码算出**的数字反馈给规划阶段（数字全部来自 `evaluate_plan`，不含自报值）.

    `route`（复核之十二 P1）：这一轮**回什么**——`"ops"`（只回操作，计划只读）或
    `"full_plan"`（回一整份新计划，附逐字基线）。由 `_replan_route` 的**唯一裁决**给；
    没给时按同一判据从 `rep["problems"]` 现算（直接调用本函数的老入口/用例）。反馈块
    里凡是"这一轮能改什么"的句子都按它渲染，**与 schema、应用器同一份口径**。

    v6.136：反馈按实际失败类型拼装——**超预算**块只在对白下界真超上限时给出
    （否则"本集装不下"是假命题）；**单镜可表示性**块在存在不可表示台词时给出
    （A 判据，点名台词 + 必保留事实/事件顺序硬边界）。
    **完整旧计划附在尾部**（六审 P1-1）：反馈要求"其余字段原样返回"，模型必须
    看得见旧字段——可表示性单独触发时（预算内）预算块不渲染，旧计划若只在
    预算块里，重选会因核心字段对不上被 `_plan_core_preserved` 拒收。
    """
    _b = rep.get("budget") or {}
    # 本轮的接口（**只裁决一次**）：调用方给就用它，没给就从同一批判据现算
    _route = route if route is not None else _replan_route(rep.get("problems"))
    _advice = _INTERFACE_ADVICE[_route]
    block = ""
    _lower = rep.get("lower_bound_sec")
    if _lower is not None and float(_lower) > float(_b.get("max") or 0.0):
        block += _PLAN_BUDGET_FEEDBACK.format(
            lines=rep.get("dialogue_lines", 0),
            dialogue_sec=float(rep.get("dialogue_sec") or 0.0),
            narr_lines=int(rep.get("narration_lines") or 0),
            narration_sec=float(rep.get("narration_sec") or 0.0),
            bmin=float(_b.get("min") or 0.0), bmax=float(_b.get("max") or 0.0),
            tmin=_TARGET_MIN_SEC, tmax=_TARGET_MAX_SEC,
            beats=int((rep.get("beats_floor_basis") or {}).get("beats") or 0),
            # "必须原样返回"的字段清单**不许在这里手写第二份**（复核之十一 P1）：
            # 渲染自 `replan_protected_fields()`——守卫比对的就是这份清单
            protected=render_protected_fields_inline(),
            # "那份计划该怎么用"同样按**本轮接口**说（复核之十二 P1）
            baseline_note=_advice["baseline_note"],
            plan_fields_note=_advice["plan_fields_note"],
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
                "② %s（**禁止把某人的台词改派给另一个角色**）"
                % (_line[:34], _menu, _advice["carrier_escape"]))
        block += _PLAN_REPRESENTABILITY_FEEDBACK.format(
            n=len(_unrep), cap=active_shot_cap(), lines_block=_lines_block,
            replacements_block="\n".join(_reps),
            reorder_rule=_advice["reorder_rule"],
            beat_edit_rule=_advice["beat_edit_rule"],
            # **做不到时怎么表达**同样按接口（复核之十二 P1：操作协议里没有 `time_budget`）
            cannot_do=_advice["cannot_do"])
    # v6.138（九审根因 1）：节拍载体悬空反馈——重选删承载台词必须同步更新载体标注
    # v6.143（2026-09-27 复核）：承载缺陷（重复标注 / 孤儿台词）与可表示性一样，
    # 都是「结构性问题」，必须走**操作式**修复——要求模型重写整份计划时它可以原样
    # 回抄（实测），承载关系反而没人动。
    _dup_c = rep.get("beat_carrier_duplicate") or []
    _orphan_c = rep.get("dialogue_orphan") or []
    _unlabeled_c = rep.get("beat_carrier_unlabeled") or []
    if _dup_c or _orphan_c or _unlabeled_c:
        _orphan_d = [x for x in _orphan_c if not str(x).startswith("N")]
        _orphan_n = [x for x in _orphan_c if str(x).startswith("N")]
        block += _PLAN_CARRIER_RELATION_FEEDBACK.format(
            dup=("、".join(_dup_c) or "无"),
            orphan=("、".join(_orphan_d) or "无"),
            orphan_narr=("、".join(_orphan_n) or "无"),
            unlabeled=("、".join(_unlabeled_c) or "无"),
            n_lines=int(rep.get("dialogue_lines") or 0),
            n_narr=int(rep.get("narration_lines") or 0))
    _dangling = rep.get("beat_carrier_dangling") or []
    if _dangling:
        block += _PLAN_CARRIER_FEEDBACK.format(
            n=len(_dangling),
            lines_block="\n".join("- %s" % d for d in _dangling),
            cannot_do=_advice["cannot_do"])
    # 规划层自己在 `time_budget` 里写的两条**停止标记**（"无合适替代" / "载体缺失：无合适承载"）：
    # 它们是"代码不代决、要么内容取舍、要么改承载"的信号，此前**没有任何专用块**
    # （复核之十一：这类计划收到的整份重写要求既没有原因、也没有旧计划）。判据取自
    # **生产者的记录**（`evaluate_plan` 的 `problems`），不从标记文本再猜一次。
    _codes = {str((p or {}).get("code") or "") for p in (rep.get("problems") or [])}
    if "budget_no_alternative" in _codes:
        block += _PLAN_NO_ALTERNATIVE_FEEDBACK
    if "carrier_missing_annotation" in _codes:
        block += _PLAN_NO_CARRIER_FEEDBACK
    # 操作式 = 模型只回操作（计划只读）；否则 = 回**一整份新计划**（要照抄基线）
    # **反馈与接口同源**（复核之十二 P1）：这一轮回的是"操作"还是"一整份计划"只由
    # `_route`（`_replan_route` 的唯一裁决）决定。旧写法在这里按"有没有不可表示条目"
    # **另判一次**——与 `replan_dialogue_within_budget` 的 `_ops_mode` 是同一个表达式的
    # 两份拷贝，且两份都不看能力裁决：混合问题（总量超限 × 单镜不可表示）于是发出的
    # 请求与**应用器**在同一个值上分叉。
    if _route == ROUTE_OPS:
        # 操作式（v6.143）：计划只作**只读上下文**，不再要求模型整份重写——
        # 上一版的「除…外请原样返回」正是模型回抄旧计划的退路。
        block += (_PLAN_OPS_INSTRUCTION
                  + "\n## 上一版计划（**只读上下文，不要重写它**）\n"
                  + render_plan_baseline(plan))
    else:
        # 非操作式 = 要求模型回**一整份新计划**：基线**无条件**附上（复核之十一 P1）——
        # 旧写法只在"已经拼出过某个反馈块"时才附，而"规划层自己报告无合适替代/载体缺失"
        # 这两类**没有专用块**（`_PLAN_NO_ALTERNATIVE_FEEDBACK` 之前根本不存在），
        # 于是模型收到的是一份没有旧计划、也没有原因的整份重写要求。
        block += ("\n## 上一版计划（**逐字基线**：除台词/旁白/可省略/时间倾向外，"
                  "其余字段请**原样返回**，照抄即可）"
                  + render_plan_baseline(plan))
    return block


def _plan_core_preserved(old: dict, new: dict) -> str:
    """重选后核心字段是否原样保留；返回第一个被改动/丢失的字段名（空串=通过）.

    "保持必保留事实和事件顺序"是用户给这轮重选的硬边界——模型改写这些字段就**不接受**
    （宁可用首版计划去报告需拆集，也不接受一份事实被悄悄改过的计划）。

    v6.138 例外：`shot_beats` 允许**纯载体标注**差异——重选增删台词时「｜载体：…」
    标注必须随行更新（九审根因 1），但节拍本体（去载体段后）必须逐字一致。

    复核之十一 P1：比对范围**由「允许改什么」的补集决定**（`_replan_editable_keys`），
    不再写死一份 5 个键的清单——规则块说"必须逐字保留"的字段，这里就要**逐项**比对
    （旧写法只比对 5 个键，`characters`/`emotional_arc` 这类被改写时守卫一言不发，
    规则与守卫两套口径）。同时请求侧附上**逐字基线**（`render_plan_baseline`），
    "照抄"是可行的（原来连原值都不给）。
    """
    _editable = _replan_editable_keys()
    for key in sorted(set(old or {}) | set(new or {})):
        if key in _editable or key.startswith("prop_state"):
            continue
        if (old or {}).get(key) == (new or {}).get(key):
            continue
        if key == "shot_beats":
            from step01_script_gen.plan_feasibility import _strip_beat_carrier
            _old = [_strip_beat_carrier(x) for x in ((old or {}).get(key) or [])]
            _new = [_strip_beat_carrier(x) for x in ((new or {}).get(key) or [])]
            if _old == _new:
                continue
        if key == "required_facts":
            _old = [_strip_fact_carrier(x) for x in ((old or {}).get(key) or [])]
            _new = [_strip_fact_carrier(x) for x in ((new or {}).get(key) or [])]
            if _old == _new:
                continue
        return key
    return ""


def replan_dialogue_within_budget(plan, rep, novel_text, *, model=None,
                                  base_prompt=None, system_prompt=None,
                                  timeout_sec=None, deadline_monotonic=None,
                                  rejections=None, evidence: dict | None = None,
                                  route: str | None = None) -> dict | None:
    """**带数值反馈的规划重选**：只重选台词，核心字段必须原样（唯一一轮）.

    `route`（复核之十二 P1）：这一轮**回什么**——`"ops"`（只回操作，代码在旧计划上
    应用）或 `"full_plan"`（回一整份新计划，核心内容守卫逐项比对）。由调用方传
    `_replan_route` 的**唯一裁决**；没传时按同一判据从 `rep["problems"]` 现算（老入口）。
    schema、反馈块、应用器**都消费它**。

    Returns `(新计划, "", call_id)`；调用失败 / 解析失败 / 校验不过 / 核心字段被改动 →
    `(None, 原因, call_id)`——`call_id` 是这一次调用的唯一标识（该轮响应的**完整正文**
    已按它落盘，修订轮据此关联证据）。**原因要分别记录**（用户 2026-09-25）：调用/解析失败**不是**判定结论，
    不能据此报告"需拆集"（见 `check_plan_before_generation` 的 `_replan_note`）。
    """
    from shared.config import PROMPT_MAX_CHARS
    # v6.143（2026-09-26 用户决定）：**可表示性触发**的重选走**操作式**——模型只回
    # 「换哪句台词 / 改哪条节拍」，由代码在旧计划上应用。要求它重写整份计划既给了
    # 「什么都不改、回抄旧稿」的退路（实测两次都这样），也让验收只能事后比对。
    # 预算超限触发仍走原路径（本来就是「只改台词」，另有全套核心字段校验）。
    # **接口只有一个来源**（复核之十二 P1）：这里原来是 `bool(_unrep or 重复/孤儿/未标)`
    # ——与 `_budget_feedback_block` 里同一个表达式的第二份拷贝，且两份都不看能力裁决。
    # 混合问题（总量超限 × 单镜不可表示）因此发出的请求与**应用器**在同一个值上分叉：
    # 表判"操作协议表达不了"，请求却按操作协议发（该协议删不掉条目）。
    _route = route if route is not None else _replan_route(rep.get("problems"))
    _ops_mode = (_route == ROUTE_OPS)
    # 操作式分支**不带**"输出整份大纲"的格式要求（复核 2026-10-02 P1，与 `_replan_by_ops`
    # 同一根因）；非操作式分支（整份重选台词）仍然需要它。
    _base = base_prompt or (_REVISION_NOVEL_PROMPT if _ops_mode else OUTLINE_PROMPT
                            ).format(novel_text=novel_text[:PROMPT_MAX_CHARS],
                                     beat_edit_rule=_BEAT_EDIT_RULE["revision"])
    _prompt = (_base + "\n"
               + _budget_feedback_block(rep, plan, novel_text, route=_route)
               + _rejection_feedback_block(rejections))
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=4000, temperature=0.3,
            json_schema=REPLAN_OPS_SCHEMA if _ops_mode else OUTLINE_SCHEMA,
            model=model,
            # 第 31 轮：**通道内重试也受同一剩余时间约束**——修订控制器给的额度里
            # 扣掉已经花掉的时间，不另开一份（`None` = 不设单次超时）
            timeout_sec=timeout_sec,
            # 工作包 §4 B：整次规划的**绝对截止时刻**——排队/退避/每次 POST 都按它重算
            deadline_monotonic=deadline_monotonic))
    except Exception as exc:
        print(f"[WARN] 规划重选调用异常（保留首版计划）: {exc}")
        # 异常出口同样带**调用身份**（复核之十一 P2）：provider 抛异常时台账里
        # 已经按 `call_id` 落了一行，调用方要能对上；异常不是从调用通道出来的
        # 时候是空串（不编造）。
        from shared.call_ledger import call_id_of
        return None, "call_failed: %s" % exc, call_id_of(exc)
    # `call_id` 必须在**读 `result.success` 之前**取（复核之十一 P2：旧写法把它放在
    # 成功分支之后，调用失败时会抛 `UnboundLocalError`——统一协议直接变成裸异常，
    # 连"这次调用是哪一个"都报不出来）
    _call_id = str(getattr(result, "call_id", "") or "")
    if not result.success:
        print(f"[WARN] 规划重选失败（保留首版计划）: {result.error}")
        return None, "call_failed: %s" % result.error, _call_id
    data = _parse_outline_json(result.text)
    if evidence is not None:
        # 归因证据束（复核之十一）：这一路也要记"回了什么形状、有没有被采纳"——
        # 与 ops/完整计划两路同一份口径（三态语义同 `_replan_by_ops`）。
        evidence["response"] = _response_summary(
            data, plan, expect=("ops" if _ops_mode else "full_plan"))
        evidence["applied"] = False
    if _ops_mode:
        if not isinstance(data, dict):
            print("[WARN] 规划重选结果解析失败（保留首版计划）")
            return None, "invalid_result: JSON 解析失败", _call_id
        new_plan, _why = apply_replan_ops(plan, data, novel_text)
        if new_plan is None:
            if _why == "no_change":
                print("[PLAN] 重选**没有给出任何操作**（显式空操作）——记 no_change，"
                      "不算一次有效重选")
            else:
                print("[WARN] 重选操作应用失败（%s）——保留首版计划" % _why)
            return None, _why, _call_id
        print("[PLAN] 重选操作已应用（台词 %d 处 / 节拍 %d 处）——作为新计划版本接受"
              % (len((data or {}).get("dialogue_ops") or []),
                 len((data or {}).get("beat_ops") or [])))
        if evidence is not None:
            evidence["applied"] = True
        return new_plan, "", _call_id
    # 回的是**一整份计划**⇒ 先过与 `revise_plan_full` **同一个**前置门（完整性 + 结构诊断：
    # 只回操作/部分字段要如实说，而不是被核心守卫报成 `core_field_changed`）
    _pre = _full_plan_response_problem(data)
    if _pre:
        print("[WARN] 规划重选结果不合法（保留首版计划）：%s" % _pre)
        return None, _pre, _call_id
    new_plan = _validate_outline(data, novel_text)
    if not new_plan:
        print("[WARN] 规划重选结果不合法（保留首版计划）")
        return None, "invalid_result: 净化事务拒收", _call_id
    _broken = _plan_core_preserved(plan, new_plan)
    if _broken:
        # 预算超限触发的重选：核心字段必须原样（只许改台词）。
        # 可表示性触发的那条路已在上面的 ops 分支 early-return——**不再**存在
        # 「用『新计划剩余条目有原文引文』代替旧计划核心字段比对」那种例外
        # （2026-09-27 复核：引文核验是**附加**条件，替代不了逐项比对，
        # 证明不了必保留事实没被删除、事件顺序没被调换）。
        print("[WARN] 规划重选改动了核心字段「%s」——不接受（必保留事实/事件顺序必须原样）"
              % _broken)
        return None, "core_field_changed: %s" % _broken, _call_id
    if evidence is not None:
        evidence["applied"] = True
    return new_plan, "", _call_id


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

    复核 2026-10-02 之四 D-2：**留档先做、解析只服务告警**。旧顺序是"先读、读不出就
    return（当没有旧稿）"，于是**读不出**这条入口跳过了留档、缓存照旧被覆盖——现在
    留档自己读盘（`_cache_backup`），读不出一样抛 `CacheBackupFailed`，调用方不覆盖；
    解析失败只影响**要不要告警**，不影响留档。
    """
    from step01_script_gen.plan_feasibility import plan_dialogue_lines
    # 留档失败**必须抛**（复核 2026-10-02 之三 P2 / 之四 D-2）：旧实现 `except OSError: pass`
    # 之后照样覆盖，于是"备份文件不存在、旧缓存已经没了"——调用方据此**不覆盖**缓存。
    try:
        _bak_name = _cache_backup(cache_path, ".bak")
    except OSError as exc:
        raise CacheBackupFailed("旧缓存留档失败（%s.bak）：%s" % (cache_path, exc)) from exc
    if not _bak_name:
        return                                    # 没有旧稿（首版写入）⇒ 无需留档/告警
    try:
        old = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return                                    # 留档已完成；旧内容判不出来就不告警
    if not isinstance(old, dict):
        return
    _key_changed = old.get("source_hash") != new_key
    old_outline = old.get("outline") if isinstance(old.get("outline"), dict) else None
    _old_n = len(plan_dialogue_lines(old_outline))
    _new_n = len(plan_dialogue_lines(new_outline))
    if _key_changed and _new_n < _old_n:
        print(f"[WARN] 大纲缓存因规则变化重生成：选用台词 {_old_n} 句 → {_new_n} 句"
              f"（新计划选材变少，成片时长下界随之下降；旧计划已留档 {_bak_name}，"
              "如需恢复请回拷并核对缓存键）")


def enforce_budget_by_replanning(plan, novel_text, *, model=None,
                                 base_prompt=None, system_prompt=None,
                                 timeout_sec=None, deadline_monotonic=None,
                                 ignore_prev: bool = False, rejections=None,
                                 evidence: dict | None = None,
                                 route: str | None = None) -> tuple:
    """规划阶段出口：代码验算 → 超预算就**带数字重选一次** → 再验算（`_REPLAN_MAX_ROUNDS`）.

    `route`（复核之十二 P1）：这一轮的**接口裁决**（`_replan_route`）——预算这条路的
    数字反馈照给，但"回操作还是回一整份计划"由**同一份裁决**定，不由这里另判一次。

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
    # 工作包 §4 C：**额度只由一份上下文管**。控制器驱动时（`ignore_prev=True`）不再让
    # `plan_replan.already_attempted` 抢先决定"这一版不再试"——次数归控制器的
    # `RevisionBudget`；`plan_replan` 仍照写，作为**历史诊断**留档。
    _prev = None if ignore_prev else (plan.get("plan_replan") if isinstance(plan, dict) else None)
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
    new_plan, _why, _call_id = replan_dialogue_within_budget(
        plan, rep, novel_text, model=model, base_prompt=base_prompt,
        system_prompt=system_prompt, timeout_sec=timeout_sec,
        deadline_monotonic=deadline_monotonic, rejections=rejections,
        # 归因证据束透传（复核之十一）：预算路的**原始响应/应用结果**此前一直是空的，
        # 归因时看不出"回了什么、有没有被采纳"——与 ops/完整计划两条路同一份口径。
        evidence=evidence,
        # 接口裁决透传（复核之十二 P1）：这一路**不另判一次**
        route=route)
    # 这一轮用的是**哪次调用**（复核之七）：`plan_replan` 随计划落盘，事后能顺着
    # call_id 找到那一轮的完整响应与模型/规则版本（bank 与诊断都只引用它，不自称已知）。
    if _call_id:
        info["call_id"] = _call_id
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


#: 修订闭环的默认上限：规划调用最多 3 次、总时长 600 秒（复核第 31 轮第 3 条——
#: 契约修订与预算重选**共用**这一份额度与截止时刻，通道内重试也吃同一份剩余时间）。
#: 次数上限**单源**在 `plan_revision.DEFAULT_MAX_CALLS`（同一份额度归控制器管）。
DEFAULT_REVISION_MAX_CALLS = _REVISION_MAX_CALLS
DEFAULT_REVISION_DEADLINE_SEC = 600.0

def _with_protocol_fields(records) -> list:
    """把问题记录收成**六字段协议**（工作包 §2.1）：`code / kind / index / path / reason / basis`.

    生产者各写各的时容易漏字段；这里只做**归一与缺省**，**不从文本里恢复任何身份**
    （缺编号就是 `None`，不用 0 或猜出来的数字占位）。`detail` 作为兼容别名一并保留
    （旧消费者/日志还在读它），内容是同一份 `reason`。
    """
    _out = []
    for _r in records or []:
        _x = dict(_r)
        _x.setdefault("kind", "plan")
        _x.setdefault("index", None)
        _x.setdefault("path", "")
        _x.setdefault("reason", str(_x.get("detail") or ""))
        _x.setdefault("detail", _x["reason"])
        _x.setdefault("basis", "")
        #: `fix` = 这条问题**需要哪种修法**（`""` = 操作协议可表达；
        #: `"reselect_dialogue"` = 必须新增/删除台词条目，操作协议表达不了）——
        #: 由生产者给；修订分流按它决定走操作式还是受约束的完整计划修订
        _x.setdefault("fix", "")
        _x.setdefault("code", "unspecified")
        _out.append(_x)
    return _out


def outline_structure_problems(plan) -> list:
    """**纯结构诊断**（工作包 §3 / 复核第 33 轮 P1-①）：只判、不改.

    规则逐项从 `_validate_outline` 迁出（每项注明来源），**不净化、不盖版本戳、不剔除**——
    "净化/剔除/重映射"是**另一件事务**（工作包 §3），改完必须重新验收。两个入口
    （首版净化入口与新验收链）消费**同一份**规则，避免"结构合法"两处各说各话。

    规则来源（逐条对应 `_validate_outline` 的检查项）：

    - `main_events`：必须是列表、3–6 条、逐项非空字符串（"C2 修复 08-11"第 1 条）；
    - `climax`：非空（"C2 修复 08-11"第 2 条）；
    - `required_facts` / `causal_chain` / `shot_beats`：非空；其中**编号字段**不许有非法项
      （`_reject_malformed_numbered_items` 的口径——删项会移动 B/D/F 编号）；
    - `shot_beats` 必须覆盖全部主要事件（"C2 修复 08-11"第 3 条）；
    - `narration_lines`：每条必须是对象且 `text` / `source` 均为非空字符串（第 6 轮口径）；
    - `hook_spoken`：存在时必须是数组（v3 契约：钩子"必须说出口"的显式字段）。

    整表/缺字段级问题 `kind="plan"`、`index=None`；条目级问题带该轴的 `index` 与路径。
    """
    _out = []
    _st = "计划结构：字段必须存在、类型正确、编号字段不许有非法项（来源：_validate_outline）"
    if not isinstance(plan, dict) or not plan:
        return [_with_protocol_fields([{"code": "structure_plan_missing", "kind": "plan",
                                        "path": "", "reason": "计划缺失或不是 JSON 对象",
                                        "basis": _st}])[0]]

    def _rec(code, kind, index, path, reason):
        _out.append({"code": code, "kind": kind, "index": index, "path": path,
                     "reason": reason, "basis": _st})

    _events = plan.get("main_events")
    _n_events = 0
    if not isinstance(_events, list):
        _rec("structure_events_type", "plan", None, "main_events",
             "`main_events` 不是数组（%s）" % type(_events).__name__)
    else:
        for _i, _e in enumerate(_events, 1):
            if not isinstance(_e, str):
                _rec("structure_events_item_type", "plan", None,
                     "main_events[%d]" % (_i - 1),
                     "`main_events` 第 %d 条不是字符串（%s）" % (_i, type(_e).__name__))
        # **可净化项**（旧契约：空白项过滤掉再判数量）——这里只数**有效**条目，
        # 不把"留了个空串"报成结构错误（那是净化事务的事，工作包 §3 三个责任分清）。
        # 这个条数同时给覆盖检查用（复核第 39 轮：两处口径必须一致）。
        _n_events = len([_e for _e in _events if isinstance(_e, str) and _e.strip()])
        _bad_item = any(x["code"] == "structure_events_item_type" for x in _out)
        if _n_events and not 3 <= _n_events <= 6:
            _rec("structure_events_count", "plan", None, "main_events",
                 "`main_events` 须有 3–6 条（现在 %d 条）" % _n_events)
        elif _n_events == 0 and not _bad_item:
            _rec("structure_events_count", "plan", None, "main_events",
                 "`main_events` 为空")

    _climax = plan.get("climax")
    if _climax is not None and not isinstance(_climax, str):
        # 复核第 37 轮：`climax=123` 曾被 `str(...)` 转换放行——schema 要的是字符串
        _rec("structure_field_type", "plan", None, "climax",
             "`climax` 不是字符串（%s）" % type(_climax).__name__)
    elif not str(_climax or "").strip():
        _rec("structure_missing_climax", "plan", None, "climax", "`climax` 为空或缺失")

    for _field, _tag in _POSITIONAL_FIELDS:
        _values = plan.get(_field)
        # 复核第 36 轮对齐旧入口，第 39 轮按用例修正：`_validate_outline` 故意不判
        # `dialogue_lines` **空**（"本集一句台词都没选用"归生成前可行性门，不造第二个判据），
        # 但"缺失 / null / 空数组"**不能推广成"任意非数组都放行"**——整表不是数组时
        # D 编号无从核验，与 `shot_beats` / `required_facts` 同为结构错误。
        if _field == "dialogue_lines":
            if _values is not None and not isinstance(_values, list):
                _rec("structure_field_type", "plan", None, _field,
                     "`%s` 不是数组（%s）" % (_field, type(_values).__name__))
            elif isinstance(_values, list):
                # 具体项由 `_malformed_numbered_item` 给（哪一项、实际是什么、期望是什么）
                _bad = _malformed_numbered_item(plan, _field, _tag)
                if _bad:
                    _rec("structure_positional_item", "plan", _bad["index"],
                         _field, _bad["reason"])
            continue
        if not isinstance(_values, list):
            _rec("structure_field_type", "plan", None, _field,
                 "`%s` 不是数组（%s）" % (_field, type(_values).__name__))
            continue
        if not _values:
            _rec("structure_field_empty", "plan", None, _field, "`%s` 为空" % _field)
            continue
        _bad = _malformed_numbered_item(plan, _field, _tag)
        if _bad:
            _rec("structure_positional_item", "plan", _bad["index"], _field, _bad["reason"])

    _cc = plan.get("causal_chain")
    if not isinstance(_cc, list):
        # 旧入口 `_validate_outline`：非数组 ⇒ `cleaned = []` ⇒ 拒收（复核实测 `"x"` 漏过）
        _rec("structure_field_type", "plan", None, "causal_chain",
             "`causal_chain` 不是数组（%s）" % type(_cc).__name__)
    elif not [x for x in _cc if isinstance(x, str) and x.strip()]:
        # `[None]` / `[""]`：旧入口净化后为空 ⇒ 拒收（复核实测漏过）
        _rec("structure_field_empty", "plan", None, "causal_chain",
             "`causal_chain` 无一有效条目（须是逐条非空的字符串数组）")

    # 覆盖检查与数量检查**同一口径**（复核第 39 轮：追加空白事件时数量按有效条数算、
    # 覆盖却按原始数组长度算 ⇒ 合法输入被误拒）
    _beats = plan.get("shot_beats")
    if isinstance(_beats, list) and _n_events and len(_beats) < _n_events:
        _rec("structure_beats_cover_events", "plan", None, "shot_beats",
             "`shot_beats` 未覆盖全部主要事件（%d 条节拍 < %d 条事件）"
             % (len(_beats), _n_events))

    _narr = plan.get("narration_lines")
    if _narr is not None:
        if not isinstance(_narr, list):
            _rec("structure_narration_type", "plan", None, "narration_lines",
                 "`narration_lines` 不是数组（旁白编号无法核验）")
        else:
            for _i, _e in enumerate(_narr, 1):
                _txt, _src = (None, None) if not isinstance(_e, dict) else (
                    _e.get("text"), _e.get("source"))
                if (not isinstance(_e, dict)
                        or not isinstance(_txt, str) or not _txt.strip()
                        or not isinstance(_src, str) or not _src.strip()):
                    _rec("structure_narration_item", "narration", _i,
                         "narration_lines[%d]" % (_i - 1),
                         "N%d 必须给 `text` 与 `source` 两个非空字符串（删掉会让 N 编号前移）"
                         % _i)

    if "hook_spoken" in plan and not isinstance(plan.get("hook_spoken"), list):
        _rec("structure_hook_spoken_type", "hook", None, "hook_spoken",
             "`hook_spoken` 必须是数组（v3 契约）")
    return _with_protocol_fields(_out)


def plan_acceptance_problems(plan, novel_text: str = "") -> list:
    """**唯一的计划验收入口**（复核第 31 轮第 2 条）：结构 / 原文 / 承载 / 预算.

    首版、缓存、规范化、契约修订、预算重选的产物**都从这一处过**；只有返回空表才算
    可执行、才进缓存。每项是 `{"code", "path", "detail", "basis"}`：

    - `code`：`structure_*` / `carrier` / `source` / `budget_infeasible`（前缀 `budget` 的
      归「预算类」，控制器据此把「还剩什么」与「为什么停」分账）；
    - `path`：**具体字段路径**（`required_facts[i].承载` / `shot_beats[i].载体` /
      `narration_lines[i]` / `dialogue_lines[i]` / `hook_carrier`；认不出来时留空）；
    - `basis`：这条判据的依据（改哪条规则就得改这里；反馈给规划器时也带着它）。
    """
    from step01_script_gen.plan_feasibility import (
        carrier_relation_records, evaluate_plan, plan_contract_records,
        plan_dialogue_lines, plan_narration_entries)
    _out = []
    if not isinstance(plan, dict) or not plan:
        # 复核第 40 轮：这条是**唯一**没走 `_with_protocol_fields` 的返回，
        # 缺 `kind` / `index` / `reason`（消费者按六字段读会拿到 KeyError）。
        return _with_protocol_fields([{"code": "structure_empty", "kind": "plan",
                                       "index": None, "path": "",
                                       "reason": "计划缺失或不是 JSON 对象",
                                       "basis": "计划结构：事件/节拍/台词字段必须存在"}])
    # ---- 结构：**先跑、并短路**（工作包 §3：结构无法安全遍历时不强行调用下游）----
    # 复核第 36 轮实测：`shot_beats=7` / `required_facts=7` / `dialogue_lines=7` 时先调
    # 承载检查会抛 `TypeError`；"只按 code 后缀判断能否继续"不足以保证可安全遍历，
    # 所以**有任何结构问题就直接返回结构记录**，不下探原文/承载/预算。
    _struct = outline_structure_problems(plan)
    if _struct:
        return _with_protocol_fields(_struct)
    # 身份与路径由生产者给出（工作包 §2.2）：这里只**组合**记录，
    # 不再用正则从问题文案里认编号（复核第 33 轮 P1 的"从正文猜编号"）。
    _out.extend(plan_contract_records(plan, novel_text=novel_text))
    _out.extend(carrier_relation_records(
        plan.get("shot_beats") or [], len(plan_dialogue_lines(plan)),
        len(plan_narration_entries(plan))))
    _out = _with_protocol_fields(_out)
    _rep = evaluate_plan(plan, novel_text=novel_text)
    # 原文核验按**逐条枚举**调用（工作包 §2.2：D/N 身份由调用处枚举提供）——
    # `verify_dialogue_lines` 的 `rejected` 只带句子与原因、不带序号，从它恢复序号就是
    # 复核第 33 轮 P1 说的"从正文猜编号"（`2024` 那句曾被报成 `dialogue_lines[2023]`）。
    _src_basis = "原文：选中的台词必须能在原文里**逐字**找到（且可单镜表示）"
    from step01_script_gen.plan_feasibility import verify_dialogue_lines
    for _i, _line in enumerate(plan.get("dialogue_lines") or [], 1):
        _kept, _rej = verify_dialogue_lines([_line], novel_text)
        if _rej:
            _out.append({"code": "source_not_in_novel", "kind": "dialogue", "index": _i,
                         "path": "dialogue_lines[%d]" % (_i - 1),
                         "reason": "D%d 不在原文里（逐字核验未通过）" % _i,
                         "detail": "D%d 不在原文里（逐字核验未通过）" % _i,
                         "basis": _src_basis})
    # 旁白与对白**同一套核验**（复核第 40 轮 P1）：正文不在原文里时，
    # `narration_source_records` 有意让路（那条归逐字核验），但逐字核验的结论此前
    # **没有任何记录**——验收链全绿、控制器直接 ok。这里按条枚举补上。
    _narr_basis = "原文：选中的旁白必须能在原文里**逐字**找到（不得自造/改写）"
    for _i, _e in enumerate(plan_narration_entries(plan), 1):
        _kept_n, _rej_n = verify_dialogue_lines([str(_e.get("text") or "")], novel_text)
        if _rej_n:
            _why = str((_rej_n[0] or {}).get("reason") or "")
            _out.append({"code": "source_narration_not_in_novel", "kind": "narration",
                         "index": _i, "path": "narration_lines[%d]" % (_i - 1),
                         "reason": "N%d 的正文不在原文里（%s）" % (_i, _why),
                         "detail": "N%d 的正文不在原文里（%s）" % (_i, _why),
                         "basis": _narr_basis})
    # `unevaluable` = **验收没做完**（不是通过、也不是"故事不可行"）：2026-10-01 复核
    # 第 32 轮 P1——旧链在"全部台词都不在原文"时底层报 unevaluable，控制器却返回 ok。
    if _rep.get("verdict") == "unevaluable":
        # **原因取自生产者的原话**（复核 2026-10-02 之八 P1）：旧写法读的是**不存在**的
        # 单数 `reason`（生产者给的是 `reasons` 列表）⇒ 记录只剩「验收未完成：」四个字，
        # 反馈给模型的也是这四个字（ch1 实跑的真实形态）。具体到字段的记录由
        # `evaluate_plan` 的停止分支产出，随下面的 `_rep["problems"]` **原样透传**；
        # 本条标记只说"验收**没做完**"这个状态（与"哪里不合格"不是一回事）。
        _why = "；".join(str(x) for x in (_rep.get("reasons") or [])
                        if str(x or "").strip())
        _out.append({"code": "acceptance_incomplete", "path": "",
                     "reason": "验收未完成：%s" % (_why[:200] or "（生产者未给出具体原因）"),
                     "basis": "验收必须可判定；判不了就是没通过（不等于故事不可行）"})
    # 预算轴与 `time_budget` 停止标记的记录由**生产者**给出（`evaluate_plan` 的
    # `problems`；复核第 40 轮 P1：从 `verdict` 汇总结论里挑原因会丢掉
    # 「无合适替代」「载体缺失」「载体悬空」三类），报告层只组合。
    _out.extend(_rep.get("problems") or [])
    return _with_protocol_fields(_out)


def _render_plan_for_revision(plan) -> str:
    """给规划器看的**当前计划**（编号与验收用的**同一套** B/D/N/F）.

    复核第 32 轮 P1：「实际修订请求没有携带当前计划，模型无法可靠对应 B/D/F 编号」。
    """
    _l = ["【当前计划（已规范化；下面的编号就是验收用的编号）】"]
    for _i, _b in enumerate(plan.get("shot_beats") or [], 1):
        _l.append("B%d %s" % (_i, _b))
    for _i, _t in enumerate(plan.get("dialogue_lines") or [], 1):
        _l.append("D%d %s" % (_i, _t))
    for _i, _e in enumerate(plan.get("narration_lines") or [], 1):
        if isinstance(_e, dict):
            _l.append("N%d %s（出处：%s；承载事实：%s）"
                      % (_i, _e.get("text"), _e.get("source"), _e.get("fact_refs")))
    for _i, _f in enumerate(plan.get("required_facts") or [], 1):
        _l.append("F%d %s" % (_i, _f))
    return chr(10).join(_l)


#: 反馈里**逐条展开**（带判据依据）的问题条数上限；超出部分**保留身份**、不许静默省略。
FEEDBACK_DETAIL_LIMIT = 12
#: 未展开项的**身份清单**上限（`code`+字段路径）——再长也要说出还剩几条。
FEEDBACK_IDENTITY_LIMIT = 40


def _problem_identity(rec) -> str:
    """问题记录的**身份**：`code（字段路径）`（展开与否都按这一份口径给）."""
    _code = str(rec.get("code") or "无code")
    _path = str(rec.get("path") or "").strip()
    return "%s（%s）" % (_code, _path) if _path else _code


def _unexpanded_block(recs) -> list:
    """未展开问题的**身份块**（计数 + 路径）：受长度限制必须分组时，总数与身份都要给.

    复核 2026-10-02 之六 P2：旧写法 `problems[:12]` 把第 13 项之后**静默省略**——ch1
    基线 28 项里，预算超限、不可表示等**根本没下发**，模型看不到；`prompt_tokens`
    增长只证明请求变长，证明不了反馈完整。这里把"没展开"说成事实：总数、按类别计数、
    每一条的身份（超长再显式说明还剩几条）。
    """
    if not recs:
        return []
    _by_code: dict = {}
    for _r in recs:
        _c = str(_r.get("code") or "无code")
        _by_code[_c] = _by_code.get(_c, 0) + 1
    _lines = ["", "**未展开的 %d 项**（它们**同样**在本轮验收范围内，不是「没有」）——"
                  "按类别计数：%s" % (len(recs),
                                    "、".join("%s×%d" % (c, n)
                                              for c, n in sorted(_by_code.items(),
                                                                 key=lambda x: (-x[1], x[0]))))]
    _ids = [_problem_identity(_r) for _r in recs[:FEEDBACK_IDENTITY_LIMIT]]
    _lines.append("它们的身份（code｜字段路径）：%s" % "；".join(_ids))
    if len(recs) > FEEDBACK_IDENTITY_LIMIT:
        _lines.append("（还有 %d 项身份未列出——完整清单见诊断的 `problems` / `baseline_problems` 栏）"
                      % (len(recs) - FEEDBACK_IDENTITY_LIMIT))
    return _lines


def _revision_feedback_block(problems, plan) -> str:
    """把验收问题**连同字段路径与判据依据**反馈给规划器（复核第 31 轮第 1、2 条）.

    2026-10-02 复核 P1：**可以改什么 / 必须保持不变**只在 `_PLAN_OPS_INSTRUCTION` 里
    写一份（与 schema 描述同源）；本函数只回答"这轮为什么被拦下"——问题清单 + 只读的
    当前计划（编号与验收同一套）。同一个契约写两遍必然漂移，而漂移的恰好是"哪些键能改、
    空值什么意思"这类**协议语义**。

    2026-10-02 复核之六 P2：**总数与未展开项的身份必须给**（旧写法 `[:12]` 静默省略）。
    """
    _recs = [x for x in (problems or []) if isinstance(x, dict)]
    _n = len(_recs)
    if _n > FEEDBACK_DETAIL_LIMIT:
        _head = "未通过的项目（**共 %d 项**；下列前 %d 项给出字段路径与判据依据）：" % (
            _n, FEEDBACK_DETAIL_LIMIT)
    else:
        _head = "未通过的项目（共 %d 项）：" % _n
    _lines = ["【本轮修订要求】上一版计划未通过统一验收。", "", _head]
    for _p in _recs[:FEEDBACK_DETAIL_LIMIT]:
        _lines.append("- %s｜%s｜%s" % (_p.get("path") or "（未定位到字段）",
                                       str(_p.get("detail") or "")[:160],
                                       str(_p.get("basis") or "")[:80]))
    if _n > FEEDBACK_DETAIL_LIMIT:
        _lines += _unexpanded_block(_recs[FEEDBACK_DETAIL_LIMIT:])
        _lines.append("本轮**能改的键**见上面的操作说明；改不动或没改完的项会在下一轮继续反馈。")
    _lines += ["", _render_plan_for_revision(plan)]
    return chr(10).join(_lines)


def _rejection_feedback_block(rejections) -> str:
    """把**上一轮被拒收的响应**（具体字段 + 原因）反馈给规划器（复核 2026-10-02 之五）.

    与 `_revision_feedback_block` **分账**且**都要给**：那里说的是"这份计划哪里不合格"
    （验收结论），这里说的是"你上一次回的东西为什么不合法"（响应违约）。只反馈前者，
    模型会重复同一个错误——实测同一次运行里两轮都回了同一个未知键 `type`。

    原因**原样取自生产者**（`apply_replan_ops` 的 `why`，已含字段路径与判据），本函数
    只排版：不解释、不归纳、不把"哪一处"换成"响应不合法"这类没有可操作信息的话。
    """
    _items = [x for x in (rejections or []) if isinstance(x, dict)]
    if not _items:
        return ""
    _lines = ["", "【上一轮你的回复被本地校验拒收：**计划一个字节都没有改**】",
              "被拒收的具体位置与原因（照原样引用）："]
    for _x in _items[-3:]:
        _lines.append("- 第 %s 轮：%s" % (_x.get("round"), str(_x.get("detail") or "")[:300]))
    _lines.append("请按上面的原因改正后，重新给出本轮要求的**完整**回复——"
                  "不要再出现被拒收的写法，也不要因为上次被拒就什么都不改"
                  "（空操作会被记成 `no_change`，同样不采纳）。")
    return chr(10).join(_lines)


#: **修法能力盘点表**（单一来源）：`问题 code → (动作, 修法落点字段)`。
#:
#: 动作有三种，决定这一轮走哪个接口：
#:
#: - `"ops"`        —— 操作协议能表达：替换/拆分/追加已有编号，或改声明键
#:                     （`dialogue_ops` / `beat_ops` / `fact_carriers` / `hook_carrier` /
#:                     `hook_spoken` / `narration_lines`）；
#: - `"content"`    —— 必须**新增/删除内容条目**（操作协议只能替换已有编号）⇒ 完整计划修订；
#: - `"plan_field"` —— 必须改操作协议**碰不到**的计划字段（例如 `time_budget`）⇒ 完整计划修订。
#:
#: 复核之十一 P1：分流**只看这张表**——"没有 `reselect_dialogue` 标记"这种**否定式**推断
#: 已被实测证伪：一条只在 `time_budget` 带「载体缺失：无合适承载」的合法计划被判
#: `expressible=True`、送进操作式接口，而该接口**拒收** `time_budget`（未知操作键），
#: 同一修正走完整计划接口反而守得住核心内容、验收归零。
#:
#: **表外/未登记 = 不可修**（fail-closed，记进 `undeclared` 并走完整计划修订）——
#: 记录级 `fix` 标记（`_FIX_MARK_TO_CAPABILITY`）是生产者对**这一例**的覆盖声明。
#: 生产者新加 code 却忘了登记时，行为是**安全**的（走能力更强的接口），且
#: `test_plan_phase_k_baseline_and_capability.py` 扫源码逐个核对本表与豁免表，
#: 表不会随新增记录悄悄腐烂。
#:
#: 落点字段是 `str`，或在**同一条 code 覆盖两个轴**时是 `tuple`（例如
#: `budget_unrepresentable` 的对白/旁白两侧）——两种形态的每一个落点都必须在操作
#: 协议能力范围内（同一份用例逐项核）。
PLAN_FIX_CAPABILITY = {
    # ---- 结构：整份计划要重建（结构门在验收链最前面短路，不会进操作应用器）----
    "structure_plan_missing": ("plan_field", "plan"),
    "structure_empty": ("plan_field", "plan"),
    "structure_events_type": ("plan_field", "main_events"),
    "structure_events_item_type": ("plan_field", "main_events"),
    "structure_events_count": ("plan_field", "main_events"),
    "structure_missing_climax": ("plan_field", "climax"),
    "structure_field_type": ("plan_field", "path 指定的字段"),
    "structure_field_empty": ("plan_field", "path 指定的字段"),
    "structure_positional_item": ("plan_field", "path 指定的编号字段"),
    "structure_beats_cover_events": ("plan_field", "shot_beats"),
    # 旁白表整个是声明键（`narration_lines`）——条目形态非法可以用整表声明修好
    "structure_narration_type": ("ops", "narration_lines"),
    "structure_narration_item": ("ops", "narration_lines"),
    "structure_hook_spoken_type": ("ops", "hook_spoken"),
    # ---- 验收链本身的停止分支：判不了 / 根本没法核 ----
    "acceptance_incomplete": ("plan_field", "plan"),
    "plan_missing": ("plan_field", "plan"),
    "plan_fields_missing": ("plan_field", "plan"),
    "source_text_missing": ("plan_field", "plan"),
    # ---- 原文逐字核验：换掉那一句 / 那一条（操作协议能表达）----
    "source_not_in_novel": ("ops", "dialogue_lines"),
    "source_narration_not_in_novel": ("ops", "narration_lines"),
    "source_narration_not_in_source": ("ops", "narration_lines"),
    "dialogue_line_not_in_novel": ("ops", "dialogue_lines"),
    # ---- 内容取舍：只有增删条目能解 ----
    "plan_no_dialogue_lines": ("content", "dialogue_lines"),
    # 总量超限的修法是**取舍发声条目**：对白只能整条换（操作协议表达不了增减），
    # 旁白是**整表声明**、可以少给——两轴都认（只写对白轴会在旁白侧漏判）
    "budget_infeasible": ("content", ("dialogue_lines", "narration_lines")),
    "budget_no_alternative": ("content", ("dialogue_lines", "narration_lines")),
    "carrier_hook_line_missing": ("content", "dialogue_lines"),
    # `carrier_fact_line_missing` 表内取"已有台词、补承载登记"；一句都没有时由记录级
    # 标记覆盖成"必须补选内容"（生产者按当时的台词表判，见 `plan_contract_records`）
    "carrier_fact_line_missing": ("ops", "required_facts"),
    # ---- 操作协议碰不到的计划字段 ----
    "carrier_missing_annotation": ("plan_field", "time_budget"),
    # 记录自带**轴**（`kind` = dialogue / narration）：对白侧换一句能在一镜里讲完的
    # 原文台词，旁白侧给 `narration_lines` 整表——两个落点都在操作协议能力内
    "budget_unrepresentable": ("ops", ("dialogue_lines", "narration_lines")),
    # ---- 承载/登记关系：改声明或改节拍载体标注就能修 ----
    "carrier_duplicate": ("ops", "shot_beats"),
    "carrier_unlabeled": ("ops", "shot_beats"),
    "carrier_beat_label_invalid": ("ops", "shot_beats"),
    "carrier_dangling": ("ops", "shot_beats"),
    "orphan_uncarried": ("ops", "shot_beats"),
    "carrier_fact_missing": ("ops", "required_facts"),
    "carrier_fact_decl_invalid": ("ops", "required_facts"),
    "carrier_fact_beat_missing": ("ops", "required_facts"),
    "carrier_fact_line_mismatch": ("ops", "required_facts"),
    "carrier_fact_line_unrelated": ("ops", "required_facts"),
    "carrier_fact_narration_missing": ("ops", "required_facts"),
    "carrier_fact_narration_mismatch": ("ops", "required_facts"),
    "carrier_fact_visual_mismatch": ("ops", "required_facts"),
    "carrier_fact_visual_unrelated": ("ops", "required_facts"),
    "carrier_narration_field": ("ops", "narration_lines"),
    "carrier_narration_item": ("ops", "narration_lines"),
    "carrier_narration_fact_refs": ("ops", "narration_lines"),
    "carrier_narration_fact_missing": ("ops", "narration_lines"),
    "carrier_narration_fact_mismatch": ("ops", "narration_lines"),
    "carrier_narration_fact_unregistered": ("ops", "narration_lines"),
    "carrier_narration_source_missing": ("ops", "narration_lines"),
    "carrier_hook_spoken_missing": ("ops", "hook_spoken"),
    "carrier_hook_spoken_type": ("ops", "hook_spoken"),
    "carrier_hook_spoken_item": ("ops", "hook_spoken"),
    "carrier_hook_decl_invalid": ("ops", "hook_carrier"),
    "carrier_hook_decl_duplicate": ("ops", "hook_carrier"),
    "carrier_hook_beat_missing": ("ops", "hook_carrier"),
    "carrier_hook_narration_not_spoken": ("ops", "hook_carrier"),
    "carrier_hook_extra_decl": ("ops", "hook_carrier"),
    "carrier_hook_carrier_mismatch": ("ops", "shot_beats"),
    "carrier_hook_unlabeled": ("ops", "shot_beats"),
}

#: 记录级 `fix` 标记 → 能力（生产者对**这一例**的覆盖声明；表里没有的标记一律 fail-closed）
_FIX_MARK_TO_CAPABILITY = {
    "reselect_dialogue": ("content", "dialogue_lines"),
}

#: **不进修订分流**的 code（源码扫描用豁免表，每条附理由）。
#: 它们要么是**候选层/镜层级**的记录（核的是成片镜头，不是计划），
#: 要么是**响应协议/运行级**的状态码——都不会作为计划验收问题进 `_fix_capability`。
_FIX_CAPABILITY_OUT_OF_SCOPE = {
    "beats_not_carried": "候选层：镜头没承载节拍（核 script 不是 plan）",
    "plan_refs_line_not_in_shot": "候选层：镜头的 plan_refs 声明越界",
    "plan_refs_missing": "候选层：镜头缺 plan_refs 声明",
    "plan_refs_narration_missing": "候选层：镜头缺旁白声明",
    "plan_refs_not_a_number": "候选层：plan_refs 编号非法",
    "plan_refs_out_of_range": "候选层：plan_refs 编号越界",
    "plan_refs_pairing_broken": "候选层：plan_refs 配对破损",
    "plan_refs_stale_plan": "候选层：plan_refs 指向的计划版本已过期",
    "illegal_response": "响应协议：拒收记录（不是计划验收问题）",
    "response_not_json": "响应协议：正文不是 JSON",
    "outline_invalid": "首版尝试结果码（不是计划验收问题）",
    "deadline_exhausted": "运行级：规划截止耗尽",
    "structure_inconsistent": "生成前门的事务一致性码（不是计划验收问题）",
}


def _fix_capability(problems) -> dict:
    """这一轮要求的修法**能不能被操作式接口表达**（归因第一项，按盘点表判）.

    这是**正面结论**：`expressible=True` 只在"每一条问题都有一条**已登记**的修法、
    且落点都在操作协议能力范围内"时成立。三种"不成立"都要能分辨：

    - `inexpressible`：已登记，但动作是 `content`（必须增删条目）或 `plan_field`
      （必须改操作协议碰不到的字段）；
    - `undeclared`：**没有登记**（表里没有、记录里也没声明）——"未证明可修"**不是**
      "可修"（复核之十 P1 的教训：旧写法把"没有标记"当成"操作式可修"）；
    - 记录声明了未知标记：同样 fail-closed 记 `undeclared`。

    历史：复核之九按 `fix="reselect_dialogue"` 分流（只覆盖"要增删台词"一类）；
    复核之十把"未标记"改成"正面声明"；复核之十一发现仍是否定式推断——一条只在
    `time_budget` 带标记的计划被判可表达，而操作协议**拒收** `time_budget`。
    """
    _inexpr: list = []
    _undecl: list = []
    for _p in (problems or []):
        _code = str((_p or {}).get("code") or "")
        _mark = str((_p or {}).get("fix") or "")
        if _mark:
            _cap = _FIX_MARK_TO_CAPABILITY.get(_mark)
            if _cap is None:                       # 声明了但代码不认识 ⇒ 不许当可修
                _undecl.append("%s（未知修法标记 %s）" % (_code or "无code", _mark))
                continue
        else:
            _cap = PLAN_FIX_CAPABILITY.get(_code)
            if _cap is None:                       # 未登记 ⇒ fail-closed
                _undecl.append(_code or "无code")
                continue
        if _cap[0] != "ops":
            _inexpr.append(_code or _mark)
    return {"expressible": not _inexpr and not _undecl,
            "inexpressible": sorted(set(_inexpr)),
            "undeclared": sorted(set(_undecl))}


def _replan_route(problems) -> str:
    """**唯一的接口裁决**：这一轮回什么（`"ops"` = 只回操作；`"full_plan"` = 回一整份计划）.

    判据只有一个——`_fix_capability`（盘点表的**正面**声明）。`schema`、反馈块、
    应用器**都消费这个返回值**：复核之十二 P1 的教训正是"盘点表判得对，不等于请求
    消费了它"——预算分支**优先于**能力裁决，内部又按"有没有不可表示条目"**另判一次**
    接口，于是 `expressible=False` 的计划照样发出**操作协议**请求，而该协议删不掉条目：
    同一份 31→1、同步重绑、核心不变的响应，走完整计划接口能验收归零，走操作协议
    连门都进不去（未知操作键）——模型即使完全照办也完不成这个合法修法。

    `problems` 为 `None`（没有生产者记录）时按 fail-closed 处理：**无从证明"表达得了"**
    ⇒ 走能力更强的完整计划接口。
    """
    if problems is None:
        return ROUTE_FULL_PLAN
    return ROUTE_OPS if _fix_capability(problems)["expressible"] else ROUTE_FULL_PLAN


def _request_feedback(problems) -> dict:
    """请求里**给了多少**（归因第二项）：总数、展开条数、未展开条数.

    "原因进了请求"与"原因**完整**进了请求"是两件事：展开上限之外的那部分只给身份
    （`_unexpanded_block`），归因时要能看出这个差别。
    """
    _n = len([p for p in (problems or []) if isinstance(p, dict)])
    _exp = min(_n, FEEDBACK_DETAIL_LIMIT)
    return {"problems": _n, "expanded": _exp, "unexpanded": _n - _exp}


def _plan_required_fields() -> list:
    """完整计划的**必需顶层键**（单源：首版那份 schema 的 `required`）."""
    return [str(k) for k in ((OUTLINE_SCHEMA.get("schema") or {}).get("required") or [])]


def _response_summary(data, plan, expect: str = "ops") -> dict:
    """这一轮**原始响应**实际带了什么（归因第三项）：形状 + 键/条数 或 与上一版不同的字段.

    不看文案、不猜意图——只记"它回了什么形状、动了哪些字段"。`no_change` 的归因要靠它
    区分「回填了原声明（形状是 ops、但结果与输入一致）」与「什么都没回（空操作）」。

    `expect`（复核之十实跑暴露）：**形态由调用方给**（它知道这一轮要的是什么），
    **不许从响应内容猜**——完整计划里本来就带 `hook_spoken`/`narration_lines` 这些键，
    按内容猜会把"完整计划"误记成 `ops`（实跑里三个完整计划轮次的证据全记错了，
    归因会被这条错误证据带偏）。
    """
    if not isinstance(data, dict):
        return {"shape": "unparsed", "expected": expect}
    if expect == "ops":
        from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS as _OK
        _op_keys = [k for k in sorted(data) if k in _OK]
        return {"shape": "ops", "expected": "ops",
                "keys": {k: (len(data[k]) if isinstance(data[k], list) else 1)
                         for k in _op_keys}}
    _missing = [k for k in _plan_required_fields() if k not in data]
    # `plan_contract_version` 是**代码自己盖的**版本戳（`_validate_outline` 统一写）——
    # 原始响应里本来就不会有它，拿它当"改过的字段"是**假读数**（证据要如实：
    # 归因时不能把代码的盖章算成模型改了什么）。
    _stamped = {"plan_contract_version"}
    return {"shape": "full_plan", "expected": "full_plan",
            "missing_fields": _missing,
            "changed_fields": [k for k in sorted(set(plan or {}) | set(data))
                               if k not in _stamped
                               and (plan or {}).get(k) != data.get(k)]}


def _replan_by_ops(plan, problems, novel_text, *, model=None, base_prompt=None,
                   system_prompt=None, timeout_sec=None,
                   deadline_monotonic=None, rejections=None,
                   evidence: dict | None = None) -> tuple:
    """契约/承载类问题：操作式反馈（模型只回操作，代码在旧计划上应用）.

    `evidence`（复核之九 交付证据要求）：**可选出参**——把这一轮的归因证据
    （请求信息 / 原始响应形状 / 应用结果）原样填进去，供轮次档留档。不改返回元数，
    免得所有调用点跟着改。
    """
    from shared.config import PROMPT_MAX_CHARS
    # 复核 2026-10-02 P1：操作式请求里**不能**带「输出整份大纲」的 `OUTLINE_PROMPT`
    # （它的「# 输出格式」列着整份 JSON 字段表），否则与"只回操作"自相矛盾——模型按
    # "整份大纲"的理解把 schema 必填的声明键填成空值，就会静默清掉合法声明。
    # 操作说明**单源**在 `_PLAN_OPS_INSTRUCTION`（与预算重选的操作分支共用同一份）。
    _base = base_prompt or _REVISION_NOVEL_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS])
    _prompt = chr(10).join([_base, _PLAN_OPS_INSTRUCTION,
                            _revision_feedback_block(problems, plan),
                            _rejection_feedback_block(rejections)])
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=4000, temperature=0.3,
            json_schema=REPLAN_OPS_SCHEMA, model=model, timeout_sec=timeout_sec,
            deadline_monotonic=deadline_monotonic))
    except Exception as exc:                       # noqa: BLE001
        print("[WARN] 计划修订调用异常: %s" % exc)
        from shared.call_ledger import call_id_of
        return None, "call_failed: %s" % exc, call_id_of(exc)
    # 这一次调用的唯一标识（完整正文已按它落盘；修订轮据此关联证据）
    _call_id = str(getattr(result, "call_id", "") or "")
    if not result.success:
        print("[WARN] 计划修订调用失败: %s" % result.error)
        return None, "call_failed: %s" % result.error, _call_id
    data = _parse_outline_json(result.text)
    if evidence is not None:
        evidence["response"] = _response_summary(data, plan, expect="ops")
    if not isinstance(data, dict):
        return None, "invalid_result: JSON 解析失败", _call_id
    _new, _why = apply_replan_ops(plan, data, novel_text)
    if evidence is not None:
        evidence["applied"] = _new is not None
        evidence["applied_ops"] = bool(_new is not None and _why in ("", "no_change"))
    return _new, _why, _call_id


#: **受约束的完整计划修订**的规则块（复核 2026-10-02 之九 P1）：只有"必须新增/删除
#: 内容条目"这类修法才走这条（操作协议只能替换已有编号，表达不了它）。规则文本与
#: `_plan_core_preserved` 的**守卫同源**——可改字段直接从 `_REPLAN_FREE_FIELDS` 渲染，
#: 两边不许各写一份（写两份必然漂移，而漂移的恰好是"什么能动、什么不能动"）。
_FULL_PLAN_REVISION_RULE = """
## ★ 本轮请回**完整计划**（不是操作）

上一版计划里有**操作协议表达不了**的问题——要么必须新增/删除内容条目（例如一句台词都
没选用、钩子那句不在台词表里），要么必须改 `dialogue_ops`/`beat_ops` 碰不到的字段
（例如 `time_budget`）。只替换已有条目**做不出来**，所以本轮**不要**回
`dialogue_ops`/`beat_ops`，而是按上面那份 JSON 契约回**一整份新计划**（与首版同形）。

**必须逐字保持不变**（代码**逐项**比对；改动其中任何一项，本批**不受理**）：
__PROTECTED__

**可以改**：__FREE__，以及各处的承载登记。

**内容要求**：新选的每一句台词都必须**逐字**出现在原文里、能在一镜内讲完、以句末标点
结束；每条必保留事实与钩子都必须有有效承载（事实侧与旁白侧互相登记）；
每条节拍只留**一个**载体段。**回抄旧计划不算修好**（计划没有变化同样不被采纳）。

**本轮不许改动节拍的条数与正文**（增、删、拆分、合并都算改条数）——凡是"要靠动节拍
才能成立"的修法，**这一轮一律不许用**：改了会被拒收、整轮白费。请改走这两条路：
1. **改承载登记**：把内容挂到**已有**节拍上（改末尾的「｜载体：…」/「｜承载：…」段）；
2. **改选材**：`dialogue_lines` / `narration_lines` **可以增删**（少给 = 只留这些）——
   装不下的台词/旁白就从选材里去掉，或者换成能在**一镜**里讲完的原文短句。
确实只有改节拍才能成立时，本轮**无法完成**——如实回报，不要动 `shot_beats`。
"""


def render_full_plan_rule() -> str:
    """渲染完整计划修订的规则块（**单一渲染入口**）：可改/必须保留的字段各只有一份来源.

    请求与判别性用例都调它——"什么能动、什么不能动"只有一个来源
    （`replan_protected_fields()` / `_REPLAN_FREE_FIELDS`），改一处两边同时变
    （写两份必然漂移，而漂移的恰好是这条协议语义）。复核之十一 P1 之前，这份规则
    手写死了一份 5 个键的"必须逐字保持不变"清单，而守卫比对的是另一份——两边对不上。
    """
    return _FULL_PLAN_REVISION_RULE.replace(
        "__PROTECTED__", render_protected_fields_rule()).replace(
        "__FREE__",
        "、".join("`%s`" % _f for _f in _REPLAN_FREE_FIELDS)
        + "、`hook_spoken`、`hook_carrier`")


def _full_plan_response_problem(raw) -> str:
    """**完整计划响应**先过的两道门（单一来源：两个"回一整份计划"的入口共用）；"" = 通过.

    ① **完整性**（复核之十实跑暴露）：这一轮要的是**完整计划**（首版那份 JSON 契约），
       模型只回操作/部分字段时必须**如实这么说**——旧写法把这种响应交给核心守卫，
       拒收原因变成"改动了核心字段「causal_chain」"，模型据此改不动，归因也指向错原因；
    ② **结构诊断**（与首版同一条纯诊断）：给出**具体到字段/项号**的原因，而不是一句
       "结果不合法"——这条原因会进下一轮请求的拒收反馈。

    复核之十二 P1：预算重选的整份重写分支此前**没有**这两道门（`_validate_outline`
    拒收后只说一句 `invalid_result`）。接口裁决修好之后，混合问题（预算超限 × 单镜
    不可表示）也会走这条分支，两个"完整计划"入口必须是**同一条判据**。
    """
    if not isinstance(raw, dict):
        return "invalid_result: JSON 解析失败"
    _missing = [k for k in _plan_required_fields() if k not in raw]
    if _missing:
        _head = "、".join(_missing[:6]) + ("…" if len(_missing) > 6 else "")
        return ("invalid_result: 这轮要的是**完整计划**（首版那份 JSON 契约），"
                "响应里缺 %d 个顶层字段（%s）——请回**整份计划**，不是只回操作"
                % (len(_missing), _head))
    _bad = outline_structure_problems(raw)
    if _bad:
        return "invalid_result: %s" % _structure_detail(_bad)
    return ""


def revise_plan_full(plan, problems, novel_text, *, model=None, base_prompt=None,
                     system_prompt=None, timeout_sec=None, deadline_monotonic=None,
                     rejections=None, evidence: dict | None = None) -> tuple:
    """**受约束的完整计划修订**（复核 2026-10-02 之九 P1）：操作协议表达不了的修法走这条.

    什么时候用（复核之十一 P1 起由 `_fix_capability` 按**修法能力表**判定）：问题记录声明
    的修法落点落在操作协议之外——要**新增/删除内容条目**（操作协议只能替换已有编号：
    空台词表补 D1 报 `index 1 越界（共 0 句）`；回整份 `dialogue_lines` 被当未知键拒收），
    或要改 `dialogue_ops`/`beat_ops` **碰不到的计划字段**（例如 `time_budget` 的
    「载体缺失：无合适承载」标记）⇒ 模型即使完全遵照反馈也**完不成**这个合法修法。

    与操作式**共用**同一份额度、同一个截止、同一份完整验收与同一套终局归因；差别只有三点：
    ① 请求形态——回**完整计划**（`OUTLINE_SCHEMA`，与首版同一份契约），并附**逐字基线**
       （`render_plan_baseline`：规则要求"逐字保留"的字段必须有原值可查，否则模型连
       "照抄"都做不到）；② 守卫——先过**核心内容守卫**（`_plan_core_preserved`：
       除"可改字段"之外的每一项都逐项比对，只放开内容取舍与承载登记）；③ 再交给统一验收链。

    Returns `(新计划 | None, why, call_id)`；`why` 的取值与 `_replan_by_ops` 同族
    （`call_failed` / `invalid_result` / `core_field_changed: <字段>`）。
    """
    from shared.config import PROMPT_MAX_CHARS
    # 基底提示：与首版**同一个** JSON 契约（`# 输出格式` 一段照用，不另写一份字段表），
    # 但"节拍能不能动"这一句按轮次渲染——修订轮的节拍由上一版冻结（复核之十二 P2）
    _base = base_prompt or OUTLINE_PROMPT.format(
        novel_text=novel_text[:PROMPT_MAX_CHARS],
        beat_edit_rule=_BEAT_EDIT_RULE["revision"])
    _rule = render_full_plan_rule()
    # **逐字基线必须进请求**（复核之十一 P1）：规则说"必须逐字保持不变"，就得让模型
    # 看得见要保留的原值——否则两份只有 `causal_chain` 不同的计划发出**完全相同**的
    # 请求，同一份响应必然对其中一份报 `core_field_changed`（实测复现）。
    _prompt = chr(10).join([_base, _rule,
                            _revision_feedback_block(problems, plan),
                            _rejection_feedback_block(rejections),
                            render_plan_baseline(plan)])
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=4000, temperature=0.3,
            json_schema=OUTLINE_SCHEMA, model=model, timeout_sec=timeout_sec,
            deadline_monotonic=deadline_monotonic))
    except Exception as exc:                                   # noqa: BLE001
        print("[WARN] 完整计划修订调用异常（保留上一版计划）: %s" % exc)
        from shared.call_ledger import call_id_of
        return None, "call_failed: %s" % exc, call_id_of(exc)
    _call_id = str(getattr(result, "call_id", "") or "")
    if not result.success:
        print("[WARN] 完整计划修订调用失败（保留上一版计划）: %s" % result.error)
        return None, "call_failed: %s" % result.error, _call_id
    data = _parse_outline_json(result.text)
    if evidence is not None:
        evidence["response"] = _response_summary(data, plan, expect="full_plan")
        # **应用结果**三态（与 `_replan_by_ops` 同一口径）：`None` = 没有候选
        # （调用失败/还没走到应用）；`False` = 有候选但被拒收；`True` = 候选被采纳。
        # 复核之十一：这一栏此前在完整计划路上**一直是 None**，归因时看不出"回了但被拦"。
        evidence["applied"] = False
    if not isinstance(data, dict):
        return None, "invalid_result: JSON 解析失败", _call_id
    # **完整性 + 结构诊断先查**（复核之十实跑暴露；复核之十二起与预算重选的整份重写
    # 分支**共用同一个函数**——两个"回一整份计划"的入口不许各有各的判据）
    _pre = _full_plan_response_problem(data)
    if _pre:
        return None, _pre, _call_id
    new_plan = _validate_outline(data, novel_text)
    if not new_plan:
        return None, "invalid_result: 净化事务拒收", _call_id
    _broken = _plan_core_preserved(plan, new_plan)
    if _broken:
        # 与预算重选同一条守卫、同一句口径：宁可不采纳，也不接受一份核心内容被改过的计划
        print("[WARN] 完整计划修订改动了核心字段「%s」——不接受（事件/顺序/事实本体与出处"
              "/节拍正文与依据必须原样）" % _broken)
        return None, "core_field_changed: %s" % _broken, _call_id
    from step01_script_gen.plan_feasibility import (plan_dialogue_lines,
                                                   plan_narration_entries)
    if evidence is not None:
        evidence["applied"] = True
    print("[PLAN] 完整计划修订已应用（台词 %d 句 / 旁白 %d 条）——作为新计划版本接受"
          % (len(plan_dialogue_lines(new_plan)),
             len(plan_narration_entries(new_plan))))
    return new_plan, "", _call_id


def revise_plan_once(plan, problems, budget, rejections=None, *, novel_text: str, model=None,
                     base_prompt=None, system_prompt=None) -> dict:
    """**一轮**修订（注入给控制器）：契约/承载问题走操作式反馈，预算不可行走原预算反馈.

    **接口只裁决一次**（复核之十二 P1）：`_replan_route(problems)` 的返回值同时决定
    ① 走哪条调用路径（预算反馈 / 操作式 / 受约束的完整计划修订）② 请求的 `schema`
    ③ 反馈块里"这一轮能改什么"的措辞 ④ 用哪个应用器。预算读数是**反馈的 flavor**
    （数字与已验算替代菜单只有它给得出），**不是**接口——code 全是 `budget*` 时走预算路，
    但那一轮回操作还是回一整份计划仍由同一个裁决定。

    两种反馈**共用**控制器给的 `budget`：单次请求超时用它的剩余时间（通道内重试也吃
    同一份时间），调用一次记一次账。返回 `{"kind": applied|no_change|call_failed|illegal",
    "plan", "detail", "call_id", "evidence"}`——`call_id` 是这一轮调用的唯一标识，控制器
    把它记进轮次记录（该轮响应的完整正文已按它落盘，可重放）；`evidence` 是归因证据束
    （修法可表达性 / 请求信息量 / 接口裁决 / 原始响应形状 / 应用结果三态）。

    `rejections`（复核 2026-10-02 之五）：此前被拒收的响应记录（控制器给，只读）。
    **两条反馈都进请求**——验收问题说"计划哪里不合格"，拒收记录说"你上次回的东西为什么
    不合法"；只给前者，模型会重复同一个错误。
    """
    from step01_script_gen.plan_feasibility import evaluate_plan
    _timeout = budget.remaining_sec()
    # 工作包 §4 B：控制器只开**一次**表（`budget.deadline_at`），通道里的排队/退避/
    # 每次 POST 都按它重算余量——不再各自拿一个"剩余秒数"重新解释
    _deadline_at = getattr(budget, "deadline_at", None)
    _budget_ctx = bool(problems) and all(
        str((p or {}).get("code") or "").startswith("budget") for p in problems)
    # **按修法能力分流**（复核 2026-10-02 之九 P1）：问题记录自带 `fix`（生产者给）——
    # 需要**新增/删除内容条目**时操作协议表达不了，走受约束的完整计划修订。
    # 旧写法只按"是不是预算问题"分：空台词表属于**验收/内容**问题，于是被送进只能替换
    # 已有编号的接口（实测：补 D1 → `index 1 越界（共 0 句）`；回整份 `dialogue_lines`
    # → 未知键拒收）⇒ 模型即使完全遵照反馈也修不动。
    # **按修法能力分流**（复核之十一 P1：判据收进 `_fix_capability` 的**正面**声明）——
    # 每一条问题都要有一条**已登记**的修法，且落点在操作协议能力范围内；否则走受约束的
    # 完整计划修订（同一份额度/截止/验收/守卫）。旧写法只问"有没有 `reselect_dialogue`
    # 标记"，一条只在 `time_budget` 带「载体缺失」的合法计划因此被送进**拒收该字段**的
    # 操作式接口（实测：`未知操作键 time_budget`）。
    _cap = _fix_capability(problems)
    _inops = sorted(set(_cap["inexpressible"]) | set(_cap["undeclared"]))
    # **接口只裁决一次**（复核之十二 P1）：这个值同时决定 ① 走哪条调用路径
    # ② 请求的 schema ③ 反馈块里"这一轮能改什么"的措辞 ④ 用哪个应用器。
    # 旧写法在这里按"是不是预算问题"选路、又在预算路内部按"有没有不可表示条目"
    # **另判一次**接口：盘点表明明判了 `expressible=False`，实际请求却仍是操作协议
    # （实测：31 句总量超限 × 一句不可单镜表示 ⇒ 发 `replan_ops`，而该协议**删不掉
    # 条目**——同一份 31→1、同步重绑、核心不变的响应，走完整计划接口能验收归零）。
    # **必须调同一个 `_replan_route`**（复核之十二 P2）：本行原先是手写的
    # `ROUTE_OPS if _cap["expressible"] else ROUTE_FULL_PLAN`——看着等价，但"没有生产者
    # 记录 ⇒ fail-closed 走完整计划"这条语义**只存在于助手函数里**，于是
    # `_replan_route(None)` 判完整计划、而 `revise_plan_once(..., problems=None, ...)`
    # 照样发操作协议（隔离复现）。判据只有一份，入口就得**调它**，不许抄它的表达式。
    # （`_cap` 仍是给证据用的那一份；同一个纯函数算两次，值必然相同。）
    _route = _replan_route(problems)
    # **归因证据束**（交付证据要求）：`no_change` 只说明"候选没变"，不能据此推断模型
    # 意图。这里把五项一次备齐——① 修法可表达性 ② 请求给了多少信息 ③ 原始响应的形状
    # 与改动 ④ 应用结果 ⑤ 残余问题（由控制器的轮次档一并留档）。①②在分流前后即可得，
    # ③④由具体分支填（出参不改返回元数）。`applied` 是**三态**：`None` = 没有候选
    # （调用失败/还没走到应用）、`False` = 有候选但被拒收、`True` = 候选被采纳——这是
    # "没变"与"没提修改"之间的分界线（`applied=False` **不表示**模型没提出修改）。
    _ev = {"fix_capability": _cap,
           "request": _request_feedback(problems),
           "route": _route,
           # 预算读数（数字 + 已验算替代菜单）只有预算路给得出；它是**反馈的 flavor**，
           # 不是接口——单独记一栏，免得把"走了预算反馈"读成"回了操作"
           "budget_context": _budget_ctx,
           "response": {}, "applied": None}
    if _budget_ctx:
        # 预算分支沿用**既有的一次重选语义**（"同一计划版本只重选一次"的去重标记、
        # 判据版本、回写 `plan_replan`），但这一轮仍然走控制器给的额度与剩余时间：
        # 额度管的是"花掉的规划机会"，去重标记管的是"这一版别再试"。
        # **接口由 `_route` 定**（`enforce_budget_by_replanning` 不再自选一次）。
        _new, _info = enforce_budget_by_replanning(
            plan, novel_text, model=model, base_prompt=base_prompt,
            system_prompt=system_prompt, timeout_sec=_timeout,
            deadline_monotonic=_deadline_at, ignore_prev=True,
            rejections=rejections, evidence=_ev, route=_route)
        _call_id = str(_info.get("call_id") or "")
        if not _info:
            return {"kind": "no_change", "detail": "预算已可行（无需重选）",
                    "evidence": _ev}
        if _info.get("skipped") == "already_attempted":
            return {"kind": "no_change", "detail": "已重选过（同一计划版本只重选一次）",
                    "call_id": _call_id, "evidence": _ev}
        if _info.get("result") in ("replan_failed", "no_change"):
            _d = str(_info.get("detail") or "")
            if _info.get("result") == "no_change":
                return {"kind": "no_change", "detail": _d, "call_id": _call_id,
                        "evidence": _ev}
            return {"kind": ("call_failed" if _d.startswith("call_failed") else "illegal"),
                    "detail": _d, "call_id": _call_id, "evidence": _ev}
        _plan = dict(_new or plan)
        _plan["plan_replan"] = _info
        return {"kind": "applied", "plan": _plan,
                "detail": "budget_reselect:%s" % _info.get("result"),
                "call_id": _call_id, "evidence": _ev}
    elif _route == ROUTE_FULL_PLAN:
        # `problems` 为空/缺记录时不是"有表达不了的修法"，而是**无从证明表达得了**（fail-closed）
        print("[PLAN] %s ⇒ 走受约束的完整计划修订"
              % ("本轮问题里有操作协议**表达不了**的修法（%s）" % "、".join(_inops)
                 if _inops else "没有生产者记录（无从证明操作协议表达得了）"))
        _new, _why, _call_id = revise_plan_full(
            plan, problems, novel_text, model=model, base_prompt=base_prompt,
            system_prompt=system_prompt, timeout_sec=_timeout,
            deadline_monotonic=_deadline_at, rejections=rejections,
            evidence=_ev)
    else:
        _new, _why, _call_id = _replan_by_ops(
            plan, problems, novel_text, model=model, base_prompt=base_prompt,
            system_prompt=system_prompt, timeout_sec=_timeout,
            deadline_monotonic=_deadline_at, rejections=rejections, evidence=_ev)
    if _new is None:
        _why = str(_why or "")
        if _why == "no_change":
            return {"kind": "no_change", "detail": _why, "call_id": _call_id,
                    "evidence": _ev}
        if _why.startswith("call_failed"):
            return {"kind": "call_failed", "detail": _why, "call_id": _call_id,
                    "evidence": _ev}
        return {"kind": "illegal", "detail": _why, "call_id": _call_id, "evidence": _ev}
    # **候选统一入口**（复核第 32 轮第 2 条）：候选也要先规范化，再验收、再算指纹——
    # 否则"规范化就能修的候选"会被当成没修（同一份计划两套判据）。
    return {"kind": "applied", "plan": normalize_plan_ranges(_new)[0],
            "detail": str(_why or ""), "call_id": _call_id, "evidence": _ev}


def round_archive_writer(path=None):
    """修订轮与首版尝试的**留档写入器**（`<台账目录>/step01_replan_rounds.jsonl`）.

    复核 2026-10-02 之七：每轮记录带 `call_id`（完整正文另存）、输入计划的**本体**与指纹、
    候选指纹、拒收记录与**完整验收结果** ⇒ 可以**重放真实应用器**（读回归档正文 +
    `plan_in` 跑 `apply_replan_ops`，比对拒收/验收结果），不必再靠日志前缀猜。
    没设台账路径 ⇒ 返回 `None`（不留档，行为同改造前）。
    """
    from shared.call_ledger import ledger_path
    _lp = Path(path) if path else ledger_path()
    if _lp is None:
        return None
    _p = Path(_lp).parent / "step01_replan_rounds.jsonl"

    def _write(rec: dict) -> None:
        # **自己保证目录存在**（复核 2026-10-02 之八 P2）：留档不许依赖"别人先建过目录"——
        # 首次尝试早于任何台账行时，父目录还不存在，旧写法直接 FileNotFoundError
        # （成功的首版就这样丢掉过记录）。
        _p.parent.mkdir(parents=True, exist_ok=True)
        with open(_p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return _write


def run_plan_revision(plan, novel_text: str, *, model=None, base_prompt=None,
                      system_prompt=None, max_calls: int = DEFAULT_REVISION_MAX_CALLS,
                      deadline_sec=DEFAULT_REVISION_DEADLINE_SEC,
                      run_deadline_at=None, budget=None, on_round=None) -> dict:
    """把计划交给**修订控制器**跑一遍（所有来源共用同一条验收链、同一份额度与截止时刻）.

    `run_deadline_at` 是**整次 Step 01 的绝对截止时刻**（monotonic，工作包 §4 B）：
    构造 `RevisionBudget` 时只算一次 `min(整次截止, 现在 + deadline_sec)`，之后每轮
    不再重新开表；`None` = 只用 `deadline_sec`（旧行为）。

    `budget`（复核 2026-10-02 之五）：**外面已经开好的那张表**——首版被拒后的重请求
    与这里的修订必须共用它（首版重问花掉的机会，修订就不该再花一次）。给了 `budget`
    就以它为准；`run_deadline_at` 仍一并传入，只在没给 `budget` 时用于开表。
    """
    from step01_script_gen.plan_revision import RevisionBudget, RevisionController
    from step01_script_gen.plan_feasibility import normalize_plan_ranges
    # **统一候选入口**（复核第 32 轮第 2 条）：首版、缓存版、每次修订候选都先规范化，
    # 再验收、再算指纹。"只需规范化即可通过"的计划因此**零调用**通过。
    _plan0 = normalize_plan_ranges(plan)[0]
    if budget is None:
        budget = RevisionBudget(max_calls=max_calls, deadline_sec=deadline_sec,
                                run_deadline_at=run_deadline_at)
    # 逐轮证据的落点（复核之七）：生产入口默认写到台账旁边；测试/调用方可以覆盖
    if on_round is None:
        on_round = round_archive_writer()
    _ctl = RevisionController(
        accept=lambda _p, _nt="": plan_acceptance_problems(normalize_plan_ranges(_p)[0], _nt),
        revise=lambda _p, _pr, _bg, _rj: revise_plan_once(
            _p, _pr, _bg, _rj, novel_text=novel_text, model=model, base_prompt=base_prompt,
            system_prompt=system_prompt),
        budget=budget, on_round=on_round,
        # **控制器的逐轮记录必须落到日志**（复核 2026-10-02 之五）：`log` 这个口子一直
        # 在，但生产入口从没接——"第 N 轮为什么被拒收/为什么停"只在返回对象里，
        # 真实运行（诊断只留最后一条）时整轮轨迹看不见。
        log=print)
    return _ctl.run(_plan0, novel_text)


class CacheBackupFailed(OSError):
    """旧缓存**留档失败** ⇒ 调用方**不许**覆盖缓存（旧稿是唯一副本）.

    复核 2026-10-02 之三 P2：旧实现把留档失败 `except OSError: pass` 掉，照样打印
    "旧缓存已留档 X"、照样覆盖——备份文件不存在而旧缓存已经没了。
    """


def _cache_backup(cache_path, suffix: str) -> str:
    """把旧缓存**按字节原样**留档到 `<cache><suffix>` → 返回**实际写成的文件名**.

    - **旧文件不存在** ⇒ 返回 `""`（首版写入，没有旧稿可留）；
    - **读不出 / 写不成** ⇒ 抛 `OSError` —— 调用方据此**不覆盖**（旧稿是唯一副本）。

    留档与覆盖**共用这一处**（版本失效、验收拒收、常规轮转三条路都走它）：写不成就
    不许覆盖——失败如实上报，不假装留过档。

    复核 2026-10-02 之四 D-2：旧实现是**调用方先读、再把解析好的内容传进来**，于是
    "旧文件读不出"这条入口绕开了上面那条规则——轮转函数把读失败当"没有旧稿"直接
    `return`，随后照旧覆盖（离线实测：`written=True`、无备份、旧文件被换掉）。现在
    留档**自己读盘**：读不出就是留档失败，与写不成同一条出口。按**字节**留档还有一个
    好处——解析不了的内容（坏 JSON / 非本版契约）同样留得住，不会因为"读得懂才留"丢掉证据。
    """
    _bak = Path(str(cache_path) + suffix)
    try:
        _blob = Path(str(cache_path)).read_bytes()
    except FileNotFoundError:
        return ""                       # 没有旧稿 ⇒ 无需留档（首版写入）
    _bak.write_bytes(_blob)             # 写不成 ⇒ OSError ⇒ 调用方不覆盖
    return _bak.name


def _backup_rejected_cache(cache_path, why: str) -> tuple:
    """缓存里的计划**没过统一验收** ⇒ 留档 + 明确报「需重新规划」（工作包 §4 C）.

    与"契约版本不符"分开报：这里是**内容/承载没闭合**（可能是历史失败版本被写进了缓存，
    或规则收紧后旧版本不再合格）。不复用、不静默沿用——重新规划。

    Returns `(文件名, 错误)`：留档成功时 `(名字, "")`；失败时 `(None, 原因)`——**不许**
    再声称已留档，调用方据此**不覆盖**旧缓存（复核 2026-10-02 之三 P2）。
    """
    _why = str(why)[:80]
    try:
        _name = _cache_backup(cache_path, ".bak-rejected")
    except OSError as exc:
        print("[PLAN] 缓存里的计划未通过统一验收（%s）⇒ **不复用**；但**旧缓存留档失败**"
              "（%s）⇒ 本次**不覆盖**缓存（旧稿是唯一副本，先保住它）" % (_why, exc))
        return None, str(exc)
    print("[PLAN] 缓存里的计划未通过统一验收（%s）⇒ **不复用**（可执行缓存不包含失败"
          "版本）：按首版计划重跑并覆盖缓存；旧缓存已留档 %s" % (_why, _name))
    return _name, ""


def _backup_stale_contract_cache(cache_path, got) -> tuple:
    """计划契约版本不符的旧缓存：**备份** + 明确报「需重新规划」（不是「故事不可行」）.

    2026-09-30 复核裁决：「旧缓存缺新字段应标为**需重新规划**，不能误报『故事不可行』；
    保留旧缓存备份，并通过计划契约版本使第二章重新产出首版计划。」
    `(文件名, 错误)` 同 `_backup_rejected_cache`：留档失败 ⇒ 不覆盖、如实报。
    """
    _ver = "缓存版本 %s ≠ %d" % (got if got is not None else "无", PLAN_CONTRACT_VERSION)
    try:
        _name = _cache_backup(cache_path, ".bak-contract%d" % PLAN_CONTRACT_VERSION)
    except OSError as exc:
        print("[PLAN] 大纲缓存缺少本版计划契约（%s）⇒ **需重新规划**；但**旧缓存留档失败**"
              "（%s）⇒ 本次**不覆盖**缓存（旧稿是唯一副本，先保住它）" % (_ver, exc))
        return None, str(exc)
    print("[PLAN] 大纲缓存缺少本版计划契约（承载字段；%s）"
          "⇒ **需重新规划**（不是「故事不可行」）：按**首版计划**重跑并覆盖缓存；"
          "旧缓存已留档 %s" % (_ver, _name))
    return _name, ""


def _outline_result_skeleton(source: str = "initial") -> dict:
    """结构化大纲结果的**空骨架**（工作包 §7.2）：每个键**永远都在**，缺省即"没成".

    下游只按这些键读（`executable` / `source` / `plan` / `acceptance` / `revision` /
    `failure` / `cache`），不猜内部结构，也不靠 `plan is not None` 判断能不能下发。
    """
    return {
        "executable": False,
        "source": source,
        "plan": None,
        "acceptance": {"problems": [], "codes": [], "residual_class": "none",
                       "verdict": "blocked"},
        "revision": {"stop_reason": "", "problems": [], "residual_class": "none",
                     "calls": 0, "rounds": 0, "history": [],
                     "deadline_exhausted": False, "plan_level_conclusion": False,
                     "rejections": [], "quota_exhausted": False},
        "failure": {"code": "", "detail": "", "source": source},
        "cache": {"hit": False, "written": False, "write_error": None,
                  "backup": None, "backup_error": None, "key": None},
    }


def _fail(res: dict, code: str, detail: str, problems=None, attempts=None,
          budget=None) -> dict:
    """结构化失败：`failure` 与 `revision.stop_reason` **同一键位**给同一个码（§7.2）.

    早停在调用/解析/结构这一层时控制器根本没跑——这里**仍然**把码写进
    `revision.stop_reason`：下游读"为什么停"只有一处口径，细节与来源由 `failure` 给。

    `problems` / `attempts`（复核 2026-10-02 之五）：首版被拒时的**具体记录**（字段、
    实际值、期望）与**每次尝试**的原因。此前这里只写一句"首版结构校验未通过（关键字段
    缺失/类型错误）"——违反的是**哪条规则**、**哪个字段**都丢了，既指不准也回灌不了。

    `budget`（复核 2026-10-02 之五）：**首版重问与修订共用同一张表**，所以早停时
    `revision.calls` 也要报这张表**实际花掉的规划机会**——否则"首版重问了 3 次"的
    运行会显示 `calls=0`，结构化读数与实际调用（台账）对不上（计量口径必须一致）。
    """
    _f = {"code": str(code), "detail": str(detail), "source": res["source"]}
    if problems:
        _f["problems"] = [x for x in problems]
    if attempts:
        _f["attempts"] = list(attempts)
    res["failure"] = _f
    res["revision"]["stop_reason"] = str(code)
    # 时间门早停时 `deadline_exhausted` 也要置位（复核之六 P1）：否则结构化结果自相矛盾
    # ——`stop_reason="deadline_exhausted"` 而 `deadline_exhausted=False`，消费方按后者
    # 判断就会把"没时间了"读成"内容不合格"。
    if str(code) == "deadline_exhausted":
        res["revision"]["deadline_exhausted"] = True
    if budget is not None:
        res["revision"]["calls"] = int(getattr(budget, "calls_used", 0) or 0)
        res["revision"]["quota_exhausted"] = bool(getattr(budget, "calls_left", 1) <= 0)
    return res


def _structure_detail(problems, limit: int = 3) -> str:
    """结构问题记录 → **具体到字段**的一句话（终局诊断用；截断必须显式）."""
    _recs = [x for x in (problems or []) if isinstance(x, dict)]
    if not _recs:
        return "首版结构校验未通过"
    _lines = ["%s｜%s" % (str(x.get("path") or x.get("kind") or "（未定位）"),
                          str(x.get("reason") or x.get("detail") or "")[:200])
              for x in _recs[:limit]]
    _more = ("" if len(_recs) <= limit
             else "（另有 %d 条，见 `failure.problems`）" % (len(_recs) - limit))
    return "首版结构校验未通过——" + "；".join(_lines) + _more


def _structure_feedback_block(problems) -> str:
    """首版被**结构校验**拒收 ⇒ 下一轮请求里的改正要求（复核 2026-10-02 之五）.

    这是"回灌"的落地处：**具体字段与原因**来自 `outline_structure_problems` 的记录
    （生产者给），不是"结构校验未通过"这类没有可操作信息的话。要求是**重新给完整计划**，
    不是操作——不安全的首版对象**不许**进操作应用器（复核 2026-10-02 之五）。

    与验收反馈同一条口径（复核之六 P2）：展开列不完时**总数与身份都要给**。
    """
    _recs = [x for x in (problems or []) if isinstance(x, dict)]
    if not _recs:
        return ""
    _lines = ["", "【上一版计划被本地结构校验拒收：**没有被采纳、没有写缓存**】",
              "被拒收的具体位置与原因（**共 %d 项**；下列前 %d 项，照原样引用）："
              % (len(_recs), min(len(_recs), FEEDBACK_DETAIL_LIMIT))]
    for _p in _recs[:FEEDBACK_DETAIL_LIMIT]:
        _lines.append("- %s｜%s" % (str(_p.get("path") or _p.get("kind") or "（未定位）"),
                                    str(_p.get("reason") or _p.get("detail") or "")[:240]))
    if len(_recs) > FEEDBACK_DETAIL_LIMIT:
        _lines += _unexpanded_block(_recs[FEEDBACK_DETAIL_LIMIT:])
    _lines.append("请**重新给出完整计划**（不是操作），上面每一处都按说明改正："
                  "该给字符串的字段不要给对象/数组，类型必须与约定的 JSON 形状一致。")
    return chr(10).join(_lines)


def generate_outline_result(novel_text: str, model: str | None = None,
                            cache_path: Path | None = None, *,
                            deadline_monotonic: float | None = None,
                            run_deadline_at: float | None = None,
                            max_revision_calls: int = DEFAULT_REVISION_MAX_CALLS,
                            revision_deadline_sec: float = DEFAULT_REVISION_DEADLINE_SEC) -> dict:
    """**结构化大纲结果**（工作包 §4 D / §7.2）：生产链路的唯一结果声明.

    链路：缓存判据 → 首版（必要时）→ **完整验收** → 必要时进 `run_plan_revision`
    （同一份验收链、同一份额度与截止）→ 只有 `executable=True` 才写可执行缓存并把计划
    交给生成器。`source` 只有**真正复用磁盘缓存**时才记 `cache`。

    同一份请求上下文：`deadline_monotonic`（首版模型调用）与 `run_deadline_at`（控制器
    只开一次的那张表）必须来自**同一个**绝对时刻——不一致时取**更早者**并告警，不静默
    各开一张表（§7.2）。`None` = 不设截止（旧行为）。
    """
    _dls = [float(x) for x in (deadline_monotonic, run_deadline_at) if x is not None]
    _dl = min(_dls) if _dls else None
    if len(_dls) == 2 and abs(_dls[0] - _dls[1]) > 1e-9:
        print("[WARN] deadline_monotonic(%r) 与 run_deadline_at(%r) 不一致——取更早者 %r"
              % (deadline_monotonic, run_deadline_at, _dl))
    deadline_monotonic = run_deadline_at = _dl

    res = _outline_result_skeleton()
    if not novel_text:
        return _fail(res, "outline_invalid", "原文为空（无法规划）")

    from shared.config import PROMPT_MAX_CHARS
    prompt = OUTLINE_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS],
                                   beat_edit_rule=_BEAT_EDIT_RULE["first_version"])
    system_prompt = _OUTLINE_SYSTEM_PROMPT
    # v6.125.2（用户 2026-09-25 决定）：道具状态**候选锚点**随大纲一起携带。
    # 它由**确定性扫描**从原文得出（不经 LLM），只取 `motion_away` / `marker_after`
    # 两种形状，带原文位置与 `status="待核实"`；生成/GAP 重生用**独立短提示**注入
    # （不能并进 `outline_anchor`——doubao/agent_plan 剖面会把大纲锚点压到约 200 字，
    # v6.132 起必保【高潮事件】与【选用台词】，其余行只保留装得下的）。
    from step01_script_gen.prop_state_anchor import outline_prop_anchors
    _prop_anchors = outline_prop_anchors(novel_text)
    # **规划层只开一张表**（复核 2026-10-02 之五）：首版被拒后的重请求与后续修订
    # **共用**同一份额度与截止——首版重问不是"另开一轮"，它花的就是这次规划的机会。
    # 表在**第一次模型调用之前**开（首版本身不占额，但重请求要从它扣）。
    from step01_script_gen.plan_revision import RevisionBudget, STOP_DEADLINE
    _budget = RevisionBudget(max_calls=max_revision_calls, deadline_sec=revision_deadline_sec,
                             run_deadline_at=run_deadline_at)
    # **唯一的规划截止时刻**（复核 2026-10-02 之六 P1）：`budget.deadline_at` 就是
    # `min(整次截止, 现在 + 本阶段上限)`——首版、重问、修订、最终下发与写缓存**全部**
    # 消费它。旧写法只有首版之外的路径看这张表：本阶段上限（如 0.02s）早于整次截止时，
    # 控制器已判 `deadline_exhausted`，外层却按整次截止算资格 ⇒ `executable=True` 并
    # **真的写出了缓存**（离线实测复现）。时间门只有一份，不允许两处各算一次。
    _plan_deadline = _budget.deadline_at
    cache_key = None
    outline = None
    _from_cache = False
    #: 旧缓存**留档失败**（盘点过后才知道）⇒ 本轮**不许覆盖**缓存：旧稿是唯一副本
    _backup_failed = False
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
        res["cache"]["key"] = cache_key
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            # 计划契约版本（2026-09-30 复核裁决）：旧缓存的计划**没有承载字段**，
            # 缺的是**登记信息**、不是内容 ⇒ 标「需重新规划」，不能误报「故事不可行」。
            # 处置：备份旧缓存 + 按**首版计划**重新产出（不套用 `STEP01_FORCE_REPLAN`
            # 那轮预算重选——它对一份已判时长可行的缓存本就不适用）。
            _cv_got = cached.get("plan_contract_version") if isinstance(cached, dict) else None
            # 工作包 §4 C：可执行缓存**只放通过统一验收的版本**——历史失败元数据
            # （`plan_replan` 里的 result/detail）不能当成本轮验收的替代品。
            _cached_plan = cached.get("outline") if isinstance(cached, dict) else None
            _cached_problems = ([]
                                if not isinstance(_cached_plan, dict)
                                else (plan_acceptance_problems(_cached_plan, novel_text) or []))
            _cached_ok = (isinstance(_cached_plan, dict) and not _cached_problems)
            if (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and _cv_got == PLAN_CONTRACT_VERSION and _cached_ok
                    and _validate_outline(cached.get("outline"), novel_text)):
                print(f"[CACHE] 复用章节大纲: {cache_path}")
                outline = cached["outline"]
                _from_cache = True
                res["cache"]["hit"] = True
                res["source"] = "cache"
                # 缓存里若缺该字段（旧缓存但键相同——理论上不会，键已含锚点），
                # 用本次算出的补上，避免调用方拿到 None 而丢掉独立短提示
                if isinstance(outline, dict):
                    outline.setdefault("prop_state_candidates", _prop_anchors)
            elif (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and _cv_got != PLAN_CONTRACT_VERSION):
                # `cache.backup` 记**盘上真实文件名**（helper 返回值）——两处各拼一次
                # 文件名必然漂移（`.bak-contractN` 的 N 是**新版**契约号，不是旧缓存那版）。
                # 留档失败 ⇒ `(None, 原因)`：**不许**覆盖旧缓存（复核 2026-10-02 之三 P2）
                _bak, _berr = _backup_stale_contract_cache(cache_path, _cv_got)
                res["cache"]["backup"] = _bak
                if _berr:
                    res["cache"]["backup_error"] = _berr
                    _backup_failed = True
            elif (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and not _cached_ok):
                _bak, _berr = _backup_rejected_cache(
                    cache_path,
                    "；".join(str(x.get("reason") or x.get("code"))
                              for x in _cached_problems[:2]) or "计划不是对象")
                res["cache"]["backup"] = _bak
                if _berr:
                    res["cache"]["backup_error"] = _berr
                    _backup_failed = True
        except (OSError, ValueError, TypeError) as exc:
            # 读/解析失败 ⇒ 走首版（工作包 §7.5）；失败原因另记，不当成"故事不可行"
            print("[WARN] 章节大纲缓存读取失败（按首版重新规划）: %s" % exc)

    if outline is None:
        service = create_service()
        #: 每次被拒的首版：`{"code", "detail", "problems"}`——终局诊断与**下一轮请求**都从
        #: 这里取（复核 2026-10-02 之五：旧写法把"违反哪条规则、哪个字段"丢在 stdout，
        #: 诊断只剩一句"关键字段缺失/类型错误"，既指不准也回灌不了）。
        _attempts = []

        def _archive_first(round_no: int, kind: str, detail: str, problems,
                           call_id: str, plan_out=None) -> None:
            """首版**每一次尝试**都留档（复核 2026-10-02 之八 P2）：成功、非法、调用失败.

            旧写法只在"不合格"的路径上写轮次档：**成功的首版在台账里有一次调用、
            却没有对应的尝试记录**（两章实跑都是这个形态），事后对不上"这一版计划是
            哪次调用回来的"。留档失败只告警，**不改裁决**（与修订轮同一口径）。
            """
            _w = round_archive_writer()
            if _w is None:
                return
            try:
                _w({"phase": "first_version", "round": round_no, "kind": kind,
                    "call_id": str(call_id or ""),
                    "detail": str(detail or "")[:400],
                    "plan_in": None, "plan_out": plan_out,
                    "problems_out": list(problems or [])})
            except Exception as _exc:                               # noqa: BLE001
                print("[WARN] 首版尝试留档失败（**证据缺失**，裁决不受影响）：%s" % _exc)

        while True:
            # **首版本身不占额**（既有契约）；每次**重请求**花掉一次规划机会——与修订
            # 共用控制器那一张表（`_budget`），所以"首版被拒后重问"不是另开一轮。
            # 截止时刻用**同一份** `_plan_deadline`（复核之六 P1：首版过去只拿整次截止，
            # 本阶段上限更早时会被绕过）。
            req = TextGenerationRequest(
                system_prompt=system_prompt,
                user_prompt=(prompt if not _attempts
                             else prompt + _structure_feedback_block(_attempts[-1]["problems"])),
                max_tokens=4000,
                temperature=0.3,
                json_schema=OUTLINE_SCHEMA,
                model=model,
                deadline_monotonic=_plan_deadline,
            )
            result = service.generate(req)
            # **时间门优先于归因**（与控制器"修订返回后先核截止"同一条口径）：过点后
            # 不再发 POST、不再重问；但**合格**的响应仍走完整验收链——验收报告的结论
            # 由最终时间门给（§7.2：`deadline` 优先于 ok/blocked，计划自身的问题照旧
            # 逐条留在 `problems` 里），否则"过点"会连带把"计划其实合格"这条信息也吞掉。
            _expired = (_plan_deadline is not None
                        and time.monotonic() >= float(_plan_deadline))
            # 首版这一次调用也要留档（复核之七）：它有 `call_id`（完整正文已落盘）与
            # 具体记录；重放不需要应用器（首版走的是解析 + 结构校验）。
            _first_cid = str(getattr(result, "call_id", "") or "")
            if not result.success:
                # **调用失败也要关联尝试记录**（复核之八 P2）：这一轮确实发过一次调用
                _archive_first(len(_attempts) + 1, "call_failed", str(result.error),
                               [], _first_cid)
                # **传输/通道失败维持独立归因**（复核 2026-10-02 之五）：它不是"响应违约"，
                # 重发也修不好（只会把一次通道故障变成三次扣费）——它就是这一轮的终局。
                # 但过点时终局是**时间门**（复核之六 P1：任一时间门耗尽都统一到它）。
                if _expired:
                    return _fail(res, "deadline_exhausted",
                                 "规划截止时刻已到 ⇒ 候选不许下发、不许写缓存"
                                 "（首版第 %d 次调用未完成：%s）"
                                 % (len(_attempts) + 1, str(result.error)[:120]),
                                 attempts=_attempts, budget=_budget)
                return _fail(res, "call_failed", str(result.error), attempts=_attempts,
                             budget=_budget)
            data = _parse_outline_json(result.text)
            if data is None:
                _attempts.append({
                    "code": "illegal_response",
                    "detail": "首版返回不是可解析的 JSON 对象",
                    "problems": [{"code": "response_not_json", "path": "",
                                  "reason": "模型回的正文不是可解析的 JSON 对象"
                                            "（要整份 JSON，不要说明文字/代码块外的散文）",
                                  "basis": "首版响应必须是 schema 要求的 JSON 对象"}]})
            else:
                # C2 修复（08-11）：大纲零校验——此前 main_events/climax 非空零校验，
                # 空大纲静默注入分镜 prompt（大纲锚点是一堆空标签，LLM 拿不到任何约束）。
                # 结构判据只有一份（`outline_structure_problems`，纯诊断），具体到
                # 字段与项号；净化与版本戳留在 `_validate_outline` 里作为独立事务。
                _bad = outline_structure_problems(data)
                if not _bad:
                    outline = _validate_outline(data, novel_text)
                if not _bad and not outline:
                    # 结构诊断说没问题、净化事务却拒收：两处判据不一致 ⇒ fail-closed 拒收，
                    # 并把"不一致"本身记成问题（不静默当成"首版结构校验未通过"）
                    _bad = [{"code": "structure_inconsistent", "kind": "plan", "path": "",
                             "reason": "结构诊断与净化事务结论不一致（净化入口拒收）",
                             "basis": "两处判据必须同源；不一致时按 fail-closed 拒收"}]
                if not _bad:
                    # 候选锚点是**确定性**产物（不经 LLM），挂在大纲 dict 上随稿流动；
                    # `_validate_outline` 只做字段净化、不重建 dict，所以不会把它丢掉。
                    outline["prop_state_candidates"] = _prop_anchors
                    # **成功也要有尝试记录**（复核之八 P2）：`plan_out` 是这一版
                    # 通过结构与净化后的计划本体（重放/对上"哪次调用回的"都要它）
                    _archive_first(len(_attempts) + 1, "ok",
                                   "首版通过结构与净化判据（进入统一验收）",
                                   [], _first_cid, plan_out=outline)
                    break
                _attempts.append({"code": "outline_invalid",
                                  "detail": _structure_detail(_bad), "problems": _bad})
                print("[WARN] 首版结构校验未通过（第 %d 次）：%s"
                      % (len(_attempts), _attempts[-1]["detail"][:200]))
            _archive_first(len(_attempts), _attempts[-1]["code"],
                           _attempts[-1]["detail"], _attempts[-1]["problems"],
                           _first_cid)
            # 走到这里 = 响应**不合法**（解析或结构）。过点 ⇒ 终局是**时间门**，
            # 不拿"首版不合格"顶替（两条轴：内容 vs 时间）。
            if _expired:
                return _fail(res, "deadline_exhausted",
                             "规划截止时刻已到 ⇒ 候选不许下发、不许写缓存"
                             "（首版第 %d 次响应也不合格）" % len(_attempts),
                             attempts=_attempts, budget=_budget)
            # **同一既定额度与截止内**重请求（复核 2026-10-02 之五）：额度/截止用尽就
            # 按最后一次的**响应违约**收口（`_fail` 的 `attempts` 留着每一次的原因）。
            # 不把不安全的首版对象送进操作应用器——重请求的是**完整首版计划**。
            _stop = _budget.stop_reason()
            if _stop:
                if _stop == STOP_DEADLINE:
                    # **时间门耗尽 ⇒ 终局统一为 `deadline_exhausted`**（复核之六 P1）：
                    # 不能再报"首版结构不合格"——那是响应内容问题，与"没时间了"是两条轴。
                    print("[PLAN] 规划截止时刻已到（首版已尝试 %d 次）⇒ 停在计划层"
                          % len(_attempts))
                    return _fail(res, "deadline_exhausted",
                                 "规划截止时刻已到 ⇒ 候选不许下发、不许写缓存"
                                 "（首版已尝试 %d 次）" % len(_attempts),
                                 attempts=_attempts, budget=_budget)
                print("[PLAN] 首版连续 %d 次不合格（%s）⇒ 停在计划层：%s"
                      % (len(_attempts), _stop, _attempts[-1]["detail"][:200]))
                return _fail(res, _attempts[-1]["code"], _attempts[-1]["detail"],
                             problems=_attempts[-1]["problems"], attempts=_attempts,
                             budget=_budget)
            _budget.spend()
            print("[PLAN] 首版不合格 ⇒ 把**具体字段与原因**反馈给规划器后重问"
                  "（已用 %d/%d 次规划机会）" % (_budget.calls_used, _budget.max_calls))

    # v6.156（2026-10-01 工作包）：**保守规范化在验收之前**——只做表达转换（载体区间展开
    # + 全部节拍引用重映射），语义错配（声明了承载、节拍其实不承载）仍交规划器。
    # 首版、缓存版**都从这里过**，是同一条链；没有区间写法时一个字节都不改。
    _plan0, _norm = normalize_plan_ranges(outline)
    if _norm:
        print("[PLAN] 规范化：展开载体区间 %d 处（%s）——语义错配仍交规划器"
              % (len(_norm), "、".join("B%d %s" % (c["beat"], c["range"]) for c in _norm)))
    # **统一验收 + 有界修订**（工作包 §4 D / §7.5）：首版不占三次额度；契约/承载未闭合
    # 与预算不可行**共用**同一条验收链、同一份额度与截止（控制器只开一次表）。
    # **不传** `base_prompt` / `system_prompt`：操作式请求的基底是**只读上下文**
    # （`_REVISION_NOVEL_PROMPT`），不能把"输出整份大纲"的 `OUTLINE_PROMPT` 当基底
    # （2026-10-02 复核 P1 的形态：请求协议自相矛盾会把 schema 必填键填成破坏性缺省值）。
    # 这两个参数只留给测试/调用方**覆盖**，默认值即正确契约。
    _rev = run_plan_revision(_plan0, novel_text, model=model,
                             max_calls=max_revision_calls,
                             deadline_sec=revision_deadline_sec,
                             run_deadline_at=run_deadline_at, budget=_budget)
    _plan = _rev.get("plan")
    _final, _ = normalize_plan_ranges(_plan) if isinstance(_plan, dict) else (_plan, [])
    _probs = (plan_acceptance_problems(_final, novel_text)
              if isinstance(_final, dict) else [])
    # **资格只看这一份截止**（复核之六 P1）：`_plan_deadline` = min(整次截止, 本阶段上限)，
    # 与首版/重问/修订/写缓存用的是同一个值。旧写法只看整次截止，本阶段上限更早时
    # 控制器已判 `deadline_exhausted`，这里却放行并写出缓存。
    _past = _plan_deadline is not None and time.monotonic() >= float(_plan_deadline)

    res["plan"] = _final
    res["revision"] = {_k: _rev.get(_k) for _k in
                       ("stop_reason", "problems", "residual_class", "calls", "rounds",
                        "history", "deadline_exhausted", "plan_level_conclusion",
                        "rejections", "quota_exhausted")}
    res["acceptance"] = {
        "problems": list(_probs or []),
        "codes": [str(x.get("code") or "") for x in (_probs or [])],
        "residual_class": residual_class(_probs),
        # `deadline` 优先于 `ok`/`blocked`：过点的计划无论内容如何都**不许下发**，
        # 验收报告的结论就是"时间门"（计划自身的问题仍逐条留在 `problems` 里）。
        "verdict": ("deadline" if _past else ("ok" if not _probs else "blocked")),
    }
    # `executable` 的判据只有这一条（§7.2）：**完整验收为空**且**未过截止**。
    res["executable"] = bool(not _probs and not _past)
    if not res["executable"]:
        if _past:
            _code = "deadline_exhausted"
            _detail = "规划截止时刻已到 ⇒ 候选不许下发、不许写缓存"
        else:
            _stop = str(_rev.get("stop_reason") or "")
            # 停止原因就是失败码（`illegal_response`/`no_change`/`loop`/`calls_exhausted`/
            # `deadline_exhausted`/`contract_unclosed`/`budget_infeasible`…）；异常地
            # 出现"ok 但仍有问题"时记 `contract_unclosed`（计划层结论候选）。
            _code = _stop if _stop and _stop != "ok" else "contract_unclosed"
            # **主原因**取控制器那一轮的**真实详情**（复核 2026-10-02 之四 D-1：旧写法
            # 直接拿基线自己的内容问题当 detail，`call_failed: transport reset at gateway`
            # 被 F1 承载问题顶掉——归因反了。基线问题是 main 写诊断时**另列**的附栏，
            # 由 `acceptance.problems` 给，不在这里冒充失败原因）。
            _detail = failure_detail(_rev, problems=_probs)
        return _fail(res, _code, _detail)
    res["failure"] = None

    # 可执行缓存：**只有 executable=True 才写**（§7.5）。缓存命中的版本没被改动过就不重写
    # （保持既有"命中且无需重选 ⇒ 不重写"的行为）；写盘失败**只降级缓存可用性**，
    # 不降级计划可用性，旧文件保留。
    # **留档失败 ⇒ 不覆盖**（复核 2026-10-02 之三 P2）：旧缓存没能留档时，覆盖就等于
    # 把旧稿抹掉且无处恢复——宁可这次不更新缓存，也不丢唯一副本。
    # **发布前再核一次时间门**（复核 2026-10-02 之七）：缓存留档（`_cache_backup`）、
    # 轮转、临时文件准备都算"准备期间"——旧写法只在这之前算过一次，准备期间的耗时会
    # 漏出去（本阶段上限更早时尤其明显）。过点 ⇒ **不发布、保留旧缓存**、终局为时间门。
    def _deadline_hit() -> bool:
        return (_plan_deadline is not None
                and time.monotonic() >= float(_plan_deadline))

    def _downgrade_to_deadline(why: str) -> dict:
        """把已判可执行的计划按**时间门**收口：不下发、不写缓存，报告结论记 `deadline`."""
        print("[PLAN] " + why)
        res["acceptance"]["verdict"] = "deadline"
        res["executable"] = False
        res["failure"] = {"code": "deadline_exhausted", "detail": why,
                          "source": res["source"]}
        res["revision"]["stop_reason"] = "deadline_exhausted"
        res["revision"]["deadline_exhausted"] = True
        return res

    if _deadline_hit():
        return _downgrade_to_deadline(
            "规划截止时刻已到（**缓存发布准备期间**）⇒ 不覆盖缓存、不下发")

    _need_write = (not (_from_cache and not int(_rev.get("calls") or 0))) and not _backup_failed
    if cache_path is not None and cache_key is not None and _need_write:
        try:
            # **发布动作之前**再核一次（复核之七）：留档/轮转本身也要时间，过点就**不许**
            # 动旧缓存——这里返回时盘上那份还是旧的（`.bak` 是新增的副本，不是覆盖）。
            if _deadline_hit():
                return _downgrade_to_deadline(
                    "规划截止时刻已到（**缓存留档/轮转之前**）⇒ 保留旧缓存、不下发")
            # v6.138.1：键变化 = 规则演进导致的重生成——覆盖前旧计划留 .bak 并在
            # 选材退化时告警（2026-09-26 实证：6 句→2 句的坏抽样会连锁打穿下游）
            _rotate_outline_cache_on_regen(cache_path, cache_key, _final)
            if _deadline_hit():
                return _downgrade_to_deadline(
                    "规划截止时刻已到（**缓存临时文件准备期间**）⇒ 保留旧缓存、不下发")
            from shared.json_io import (commit_json_swap, discard_json_swap,
                                        prepare_json_swap)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            # **准备与替换必须分开**（复核 2026-10-02 之八 P1）：`atomic_dump_json` 把
            # "写临时文件"与"替换旧文件"合成一步，调用方**没有地方**在两者之间再核一次
            # 截止——注入写入延迟后实测：这一轮按 `deadline_exhausted` 收口，
            # 旧缓存却已经被换掉了（**既发布了、又没下发**：终局检查只挡住下发）。
            _tmp_json = prepare_json_swap(
                cache_path,
                {"source_hash": cache_key,
                 "plan_contract_version": PLAN_CONTRACT_VERSION,
                 "outline": _final},
                ensure_ascii=False, indent=2)
            if _deadline_hit():
                discard_json_swap(_tmp_json)
                return _downgrade_to_deadline(
                    "规划截止时刻已到（**缓存临时文件写入期间**）⇒ **删除临时文件**、"
                    "保留旧缓存、不下发")
            commit_json_swap(cache_path, _tmp_json)
            res["cache"]["written"] = True
        except CacheBackupFailed as exc:
            res["cache"]["backup_error"] = str(exc)
            print(f"[WARN] 大纲缓存**未覆盖**：旧缓存留档失败（{exc}）——旧稿原样保留，"
                  "本次继续使用已验收计划")
        except (OSError, ValueError, TypeError) as exc:
            # 与"读缓存"同一套口径（那里也是这第三种）：**序列化/写入失败只降级缓存
            # 可用性**，不许把一轮合格的规划炸掉（复核之八：临时写入本身就是一个
            # 故障切点，`prepare_json_swap` 已清理残留，这里负责如实记录）
            res["cache"]["write_error"] = str(exc)
            print(f"[WARN] 章节大纲缓存写入失败（本次继续使用已验收计划）: {exc}")

    # 写入期间过点 ⇒ 终局仍是**时间门**（缓存里那份是已验收计划，留着无妨；关键是
    # **这一轮不下发**——否则"过点还能拿到 executable=True"又是一条绕过路径）。
    if _deadline_hit():
        return _downgrade_to_deadline("规划截止时刻已到（**缓存写入期间**）⇒ 不下发")
    return res


def generate_outline(novel_text: str, model: str | None = None,
                     cache_path: Path | None = None,
                     deadline_monotonic: float | None = None) -> dict | None:
    """兼容转发（工作包 §7.3）：**只把通过统一验收的计划**交给旧调用方.

    生产链路（main → `concurrent_extract`）消费 `generate_outline_result`；本函数留给
    旧调用方与兼容测试，**不绕过验收、不自建第二条缓存写路径**：`executable=False`
    ⇒ 返回 None（旧调用方的"大纲生成失败"分支）。
    """
    _res = generate_outline_result(novel_text, model=model, cache_path=cache_path,
                                   deadline_monotonic=deadline_monotonic,
                                   run_deadline_at=deadline_monotonic)
    if not _res.get("executable"):
        _f = _res.get("failure") or {}
        print("[PLAN] 未产出可执行计划（%s：%s）——不下发"
              % (_f.get("code") or "unknown", str(_f.get("detail") or "")[:120]))
        return None
    return _res["plan"]


# 「原文依据/原文证据/end_hook」里的引文提取：**实现迁到 `plan_feasibility.evidence_quotes`**
# （2026-09-30：计划契约门也要读钩子引文，两处各留一份会把同一套引号规则分裂成两个
# 版本）。这里保留旧名 `_evidence_quotes` 作转发，调用点与本文件其余规则不动。
# 兼容范围见那边的注释：成对引号 / 未闭合开引号都认，证据标准不变（≥8 字连续命中）。
def _evidence_quotes(text) -> list:
    """取出「原文依据/原文证据/end_hook」里的引文正文（成对引号或未闭合开引号）."""
    from step01_script_gen.plan_feasibility import evidence_quotes
    return evidence_quotes(text)


_PLAN_CARRIER_RELATION_FEEDBACK = """
# ★ 承载关系必须**双向闭合**（当前计划不满足，本轮只修这个）

- 同一个节拍写了**多个**「｜载体：…」段（承载关系不是唯一记录，代码只认第一段）：
  {dup}
- **没有任何节拍承载**的选中台词（生成契约里没人告诉模型这句放哪儿，成片会丢这句，
  候选层台词门随后整稿拒收）：{orphan}
- **没有任何节拍承载**的图外旁白（选中了旁白却没人讲，成片里没人念）：{orphan_narr}
- **没有载体标注**的节拍（计划带了标注就必须每条节拍恰有一个载体）：{unlabeled}

本集共 {n_lines} 句选用台词、{n_narr} 条图外旁白。请用 `beat_ops` 把承载关系补成闭合：
每句台词都有（且只有）一个节拍用「载体：台词k」声明承载它，每条旁白都有（且只有）
一个节拍用「载体：旁白k」声明承载它；每条节拍只留**一个**载体段。
**一条节拍要同时承载画面与台词时必须 `split`**（或 `insert_after` 另立一条）
——单条节拍只能有一个载体，用替换是改不出来的。
**改某条节拍的载体时，要同时把失去承载的那句指派到别的节拍上。**
"""


#: 操作式请求的**只读上下文**基底：只给原文，**不给**"输出整份大纲"的格式要求。
#: 2026-10-02 复核 P1：此前操作式请求把 `OUTLINE_PROMPT`（含「# 输出格式」整份 JSON
#: 字段表）当基底，与"只回操作"自相矛盾——模型会按"整份大纲"的要求把 schema 必填的
#: 声明键填成空值，静默清掉合法声明（`hook_carrier` 就是这么被清掉的）。
#: 两条操作式路径（`_replan_by_ops`、预算重选的操作分支）**共用**它。
_REVISION_NOVEL_PROMPT = """
阅读以下小说原文——**它只是上下文**，不要输出整份大纲。

# 小说原文
{novel_text}
"""


_PLAN_OPS_INSTRUCTION = """
## 请**只输出操作**（不要重写整份计划）

代码已把**要处理的问题**列出来了（预算/可表示性触发时还带代码验算过的替代与硬边界）。
你**只回要改的地方**，代码会把它应用到上一版计划上——所以「什么都不改」是允许的：
`dialogue_ops`/`beat_ops` 给空数组即可，**只改声明、不动台词与节拍也是允许的**；
不要靠回抄旧计划表达。

**声明键的三种写法**（`fact_carriers` / `hook_carrier` / `hook_spoken` /
`narration_lines`；与请求 schema 的描述、应用器的裁决**同一套**）：
**省略该键 = 不改**（旧值原样保留；这些键在 schema 里没有 `null` 取值，要"不改"就省略，
不要写 `null`）；**显式给空值** = 按该键自己的语义：__DECL_SEMANTICS__；**给内容** = 设置。

- `dialogue_ops`：`[{"index": 1, "replace_with": "换成哪句逐字原文"}]`
  （`index` 只填**数字**，如 `1`；不要写 `D1`/`"D1"`）。
  **不得截断本句**——以「__ILLEGAL_CUT__」收尾的半句一律非法（成片里是没说完的话）；
  `replace_with` 必须是原文里**逐字存在**且**单镜装得下**的**完整短句**（可以是别的
  角色、别的段落的句子）。有列出候选的，优先从候选里选；该句没列候选时，**不要截断它**，
  改走 `beat_ops` 重排载体。
- `fact_carriers`（**只要本批动了 `dialogue_ops` 或 `beat_ops` 就必须逐项给全**——
  节拍的插入/拆分同样会让编号顺延，不重声明就会指到别条节拍上）：
  `[{"index": 2, "beat": 4, "mode": "画面"}]`——逐条声明每个必保留事实由**哪条节拍**
  承载、以**什么方式**表达（__CARRIER_FORMS__，`beat` 用**改之后**的节拍编号）。
  声明 `台词k` / `旁白k` 时，那条节拍必须标着**同一个**「载体：台词k」/「载体：旁白k」；
  声明 `旁白k` 时旁白条目还要在 `fact_refs` 里登记同一个 F 序号（两处互相登记）。
  **事实本体与「原文证据」一个字都不许改**——出处是事实的来源，不因换了台词而失效。
  缺哪条、指错节拍、或声明与该节拍载体不符，本批**整批拒收**。
- `hook_carrier`（字符串，**可省略**）：`"B13/台词3"`，多句用「；」分隔（如
  `"B13/台词3；B15/台词2"`）——钩子要求说出口的每一句（见 `hook_spoken`）都必须
  **同时**出现在 `dialogue_lines` 里，并按**改之后**的编号声明承载节拍；声明的节拍
  自己要标着同一个载体。本批不涉及钩子承载就别写这个键（要不要"不改"/"清除"见上面的
  三种写法）。
- `narration_lines`（对象数组，可选）：本集图外旁白的**完整**声明
  （`[{"text": 原文叙述原句, "source": 这句旁白逐字出自的那句原文, "fact_refs": [F序号…]}]`）。
  `source` 是**出处**：必须能在原文里逐字找到，且**包含**这条正文（正文就出自它）
  ——随便填一句原文当出处会被拒收；`fact_refs` 必须是**整数**数组，事实侧声明
  「承载：B…/旁白k」时这里必须回填同一个 F 序号（**两处互相登记，单向不算**）。
  旁白编号 `N1..Nn` 会被 `旁白k` 引用，所以要改就**整表给全**（要清除见上面的三种写法；
  清空时引用它的节拍与事实的承载也必须同步改掉）。
- `hook_spoken`（字符串数组，**可省略**）：钩子**必须说出口**的台词（逐字原文），
  必须是 `dialogue_lines` 的**整句子集**（要不要清除见上面的三种写法）。反过来，只删了
  `dialogue_lines` 里那句、却仍要求它说出口，本批会被**整批拒收**——两处必须同时改。
- `beat_ops`：节拍操作，`op` 三选一（不写 `op` = `replace`）：
  - `{"op": "replace", "index": 2, "new_text": "新正文（含 ｜原文依据：“…”）",
     "new_carrier": "__CARRIER_FORMS__"}`——改写该节拍；
  - `{"op": "insert_after", "index": 2, "new_text": …同上…,
     "new_carrier": …}`——在该节拍**之后插入一条新节拍**（`index` 给 0 = 插到最前）；
  - `{"op": "split", "index": 2, "parts": [{"new_text": …, "new_carrier": "__CARRIER_FORMS__"},
     {"new_text": …, "new_carrier": …}]}`——把该节拍**原地拆成多条**（至少两段，
     每段各自带原文依据与载体）。**一条节拍同时要承载画面和台词时，用 `split`**。
  （以上 `index` 一律只填**数字**，如 `2`；不要写 `B2`/`"B2"`。
  `new_text` 写 `节拍：<正文>｜…` 或**只给正文**都行——落计划时统一补上 `节拍：`；
  两种写法都**必须自带** `｜原文依据：“…”`，缺正文或缺依据整批拒收。）

**索引一律指「改之前」的节拍编号**（你在上面看到的那份计划）：`split`/`insert_after`
会让编号顺延，所以同一批操作里**不要**引用还没出现的新编号。__SLOT_RULE__
`new_carrier` 留空 = 沿用该节拍原有载体；`split` 的每一段必须自己写载体
（拆分的**目的**就是给各段不同载体）。

**响应形态**：__RESPONSE_SHAPE__。代码按请求 schema 逐项核对——**缺键、`null`、
类型不对、出现没定义过的键（含条目里的未知键）一律整轮拒收**（`illegal_response`）；
代码不替你补默认值，也不修改你给的值（数字不会变成字符串）。

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

#: 一批里**每个编号的槽位规则**（唯一源）：**改动槽**（`replace` / `split`）至多一条 +
#: **插入槽**（`insert_after`）至多一条，两个槽位互不覆盖——「改这条节拍」与「在它后面
#: 插一条」是两件事（P1-3 复核：只记"上一条操作种类"时，`replace → insert_after →
#: replace` 的第三条会把第一条的改动静默覆盖掉）。
#:
#: 提示里那句"同一编号出现两次 = 冲突"是**手写的旧规则**，与应用器**相反**（复核
#: 2026-10-02 之三 P2：实测同一编号各做一次 `replace` 与 `insert_after` 会被正常应用、
#: 完整验收通过）——模型照提示只会**放弃**这个合法组合。现在这句由本常量渲染，
#: 并由对照用例锁住（提示文案 ↔ 真实应用行为）。
BEAT_OP_SLOT_RULE = (
    "同一编号在一批里**至多一条改动操作**（`replace` / `split` 共用一个槽位）+ "
    "**至多一条 `insert_after`**（两个槽位互不覆盖）；同槽位出现两次 = 整轮拒收。")


#: 提示里的非法切点字符表**由判据侧的常量渲染**（不在提示里手抄一遍字符）——
#: 复核口径：提示文字是手写规则，一致性必须由**守卫用例**保证，而不是靠同步习惯。
from step01_script_gen.plan_feasibility import CUT_ILLEGAL_CHARS as _ILLEGAL_CUT

_PLAN_OPS_INSTRUCTION = _PLAN_OPS_INSTRUCTION.replace(
    "__ILLEGAL_CUT__", "」「".join(_ILLEGAL_CUT))

#: 同类守卫用的标记：提示里确实渲染进了全部非法字符（空表时视为未接）
assert "__ILLEGAL_CUT__" not in _PLAN_OPS_INSTRUCTION

#: 声明键的**空值语义**同样由判据侧渲染（2026-10-02 复核 P2：提示手抄了一份"空值 = 清除"，
#: 而 `fact_carriers: []` 的裁决是"不改"——**提示与裁决相反**，模型照提示写就写错）。
#: 同源守卫见 `test_plan_phase_c_declaration_ops.py::test_c09_*`：逐键核对提示 / schema
#: 描述 / 应用器裁决三处。
from step01_script_gen.plan_feasibility import decl_semantics_clause as _decl_semantics_clause

_PLAN_OPS_INSTRUCTION = _PLAN_OPS_INSTRUCTION.replace(
    "__DECL_SEMANTICS__", _decl_semantics_clause())
assert "__DECL_SEMANTICS__" not in _PLAN_OPS_INSTRUCTION

#: "允许写哪些载体"同样**由判据侧渲染**（复核 2026-10-02 之二 P2：提示只列「画面/台词k」，
#: `旁白k` 这条**合法通道**没教——桩直传正确操作能过，模型照契约却写不出来）。
from step01_script_gen.plan_feasibility import carrier_mode_hint as _carrier_mode_hint

_PLAN_OPS_INSTRUCTION = _PLAN_OPS_INSTRUCTION.replace(
    "__CARRIER_FORMS__", _carrier_mode_hint())
assert "__CARRIER_FORMS__" not in _PLAN_OPS_INSTRUCTION

#: **槽位规则**与**响应形态**同样由判据侧渲染（复核 2026-10-02 之三 P2/P1）：
#: 前者是手写旧规则与应用器行为相反（模型照着写就放弃合法组合），后者是必填键规则
#: 必须与请求 schema 的 `required` 同源。两处都有守卫用例钉住"提示 ↔ 应用器/schema"。
from step01_script_gen.plan_feasibility import (
    replan_response_shape_clause as _response_shape_clause)

_PLAN_OPS_INSTRUCTION = _PLAN_OPS_INSTRUCTION.replace("__SLOT_RULE__", BEAT_OP_SLOT_RULE)
_PLAN_OPS_INSTRUCTION = _PLAN_OPS_INSTRUCTION.replace(
    "__RESPONSE_SHAPE__", _response_shape_clause())
assert "__SLOT_RULE__" not in _PLAN_OPS_INSTRUCTION
assert "__RESPONSE_SHAPE__" not in _PLAN_OPS_INSTRUCTION


def _norm_beat_carrier(raw, n_lines: int, old_record, n_narration: int = 0) -> tuple:
    """规范化一条操作给的载体 → `(载体文本, 原因)`；`原因` 非空 = 拒收.

    - 空 → 沿用 `old_record`（被改节拍）原有的载体——模型常常只想改正文，这比整轮
      拒收有用；沿用不到（原节拍本来就没有载体段）时留空，由承载关系验收判
      （那才是唯一判据）；
    - `画面` → 照收；`台词k` → k 必须在 `1..n_lines` 内（节拍操作**不**改台词条数）；
    - 其余一律拒收：载体是**唯一记录**，含糊值等于没声明（宁可显式失败）。

    **完整匹配**（2026-10-01 复核第 30 轮）：文法与数字转换**单源**在
    `plan_feasibility.parse_beat_carrier`。旧实现按"数字前缀"截取——模型给的
    `new_carrier="台词1-2"`（一条连续片段）被**静默**改成 `台词1`，契约检查还判通过：
    模型声明的意思被缩小了，而这正是修订控制器要反馈给规划器的东西。现在 `台词1-2`
    / `台词1、台词2` / `台词²` / 超长数字一律走"载体只能是…"这条拒收，并且**不抛异常**。
    """
    # 载体写法**单源**在 `CARRIER_KIND_FORMS` / `carrier_mode_hint()`（判据侧）——
    # 这里不再手抄"画面/台词/旁白"与那句拒收文案（复核 2026-10-02 的非阻塞观察）。
    from step01_script_gen.plan_feasibility import (
        CARRIER_KIND_FORMS, carrier_mode_hint, parse_beat_carrier)
    car = str(raw or "").strip()
    if not car and old_record is not None:
        kind, k = old_record.carrier()
        if kind == "dialogue" and k:
            car = "台词%d" % k
        elif kind == "narration" and k:
            car = "旁白%d" % k
        else:
            car = "画面" if kind == "visual" else ""
    if not car:
        return "", ""
    _kind, _k = parse_beat_carrier(car)
    if _kind == "visual":
        return CARRIER_KIND_FORMS["visual"][1], ""
    if _kind == "dialogue":
        _axis = CARRIER_KIND_FORMS["dialogue"][0]
        if _k is not None and 1 <= _k <= int(n_lines or 0):
            return "%s%d" % (_axis, _k), ""
        return "", "载体「%s」越界（本集共 %d 句台词）" % (car[:12], int(n_lines or 0))
    # 2026-09-30 复核第 6 轮：`旁白k` 是第三种合法载体（图外旁白）。没有这一支，
    # 「把某条节拍改成由旁白承载」这个唯一能修好交代类事实的操作会在应用器层被拒。
    if _kind == "narration":
        _axis = CARRIER_KIND_FORMS["narration"][0]
        if _k is not None and 1 <= _k <= int(n_narration or 0):
            return "%s%d" % (_axis, _k), ""
        return "", ("旁白载体「%s」越界（本集共 %d 条旁白；旁白要先写进 "
                    "narration_lines）" % (car[:12], int(n_narration or 0)))
    return "", "载体只能是 %s，收到「%s」" % (carrier_mode_hint(), car[:12])


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
                    require_carrier: bool = False, n_narration: int = 0) -> tuple:
    """把一条操作的 `(new_text, new_carrier)` 落成节拍文本 → `(文本, 原因)`.

    判据读 `BeatRecord` 的**字段**，不再靠字符串拼接：`new_text` 是**整条节拍正文**
    （自带「｜原文依据：…」）；模型若把旧标注一起抄进 `new_text`，这里**先删干净再
    重建**，从表示上消除「｜载体：画面｜载体：画面」这类重复标注（实跑出现过——
    旧实现靠事后数字符串才发现）。
    """
    from step01_script_gen.plan_feasibility import (
        BEAT_SEG_BODY, BEAT_SEG_CARRIER, BEAT_SEG_EVIDENCE, BeatRecord,
        plan_narration_entries)
    _rec = BeatRecord.parse(new_text)
    # **表示形态归一**（2026-10-02 两章实跑）：schema 把 `new_text` 说成"该节拍的新正文"，
    # 模型就照"正文"写——`｜本镜信息目的：…｜原文依据：“…”` 齐全、**没有** `节拍：` 标签。
    # 老判据只认 `节拍：` 段，于是这种显然可读的写法被记成"节拍正文为空"，整轮修订以
    # `illegal_response` 收场（ch1/ch2 实跑都停在这一步）。开头那段**未识别**且非空的
    # 段就是正文：补上标签再往下判。真正没有正文（整条只有标签段）仍然拒收——归一只认
    # 位置明确的一种写法，不做猜测。
    if (not _rec.body and _rec.segments and _rec.segments[0][0] is None
            and _rec.segments[0][1].strip()):
        _raw = _rec.segments[0][1]
        _lead = len(_raw) - len(_raw.lstrip())
        _rec.segments[0] = (BEAT_SEG_BODY, _raw[:_lead] + BEAT_SEG_BODY + _raw[_lead:])
    if not _rec.body:
        return None, "节拍正文为空（`new_text` 要给正文：可写 `节拍：<正文>｜…`，也可只给正文）"
    if not _rec.has(BEAT_SEG_EVIDENCE):
        return None, "节拍正文缺「原文依据」"
    _car, _why = _norm_beat_carrier(new_carrier, n_lines, old_record, n_narration)
    if _why:
        return None, _why
    if require_carrier and not _car:
        # P1-4（2026-09-27 复核）：**新建**的节拍没有「原有载体」可沿用——留空就等于
        # 凭空多出一条没有承载记录的节拍。旧计划**整体**无标注时兼容分支不会报未标注
        # （`carrier_relation_problems` 的「计划带了标注才判」口径），这条新节拍会静默
        # 无标注 ⇒ 必须在这里显式失败，而不是靠下游兜。
        return None, ("新建的节拍必须显式给 `new_carrier`（%s）——"
                      "没有旧节拍可沿用载体，留空等于多出一条无承载记录的节拍"
                      % _carrier_mode_hint())
    _rec.drop(BEAT_SEG_CARRIER)
    if _car:
        _rec.set(BEAT_SEG_CARRIER, _car)
    return _rec.render(), ""


def _norm_narration_entries(raw) -> tuple:
    """把本批的 `narration_lines` 归一成**整表** → `(表, 原因)`；`raw is None` ⇒ `(None, "")`.

    归一只能做**一次**，而且要在这里就做完：旁白条数决定 `旁白k` 的合法范围，所以它必须
    在本批**所有承载引用校验之前**算出来（复核 2026-10-02 P1：旧实现拿**旧计划**的条数
    校验 `beat_ops` / `fact_carriers`——"同批新增 N1 + 指定它由哪条节拍承载"这种**合法
    联动修法**被判"本集共 0 条旁白"，记成 `illegal_response`；把 N1 先塞进旧计划反而能过）。
    """
    if raw is None:
        return None, ""
    if not isinstance(raw, list):
        return None, ("`narration_lines` 必须是对象数组（text/source/fact_refs）——"
                      "现在是 %s" % type(raw).__name__)
    _out: list = []
    for _i, _e in enumerate(raw, 1):
        # **不许圆场**（复核 2026-10-02 之三 P1）：`text`/`source` 必须是**字符串**——
        # 旧实现 `str(_e.get("text"))` 把模型给的数字 `123` 变成 `"123"` 写进计划，
        # 违反严格 schema 的响应被静默"修好"、整批记成应用成功。形态校验
        # （`replan_ops_response_problems`）已在应用器入口挡一道，这里是同一判据的第二道。
        if (not isinstance(_e, dict) or not isinstance(_e.get("text"), str)
                or not isinstance(_e.get("source"), str)
                or not _e.get("text").strip() or not _e.get("source").strip()):
            return None, ("`narration_lines` 第 %d 条非法（text/source 必须是**字符串**"
                          "且非空——数字/缺失都不接受）：%s" % (_i, str(_e)[:40]))
        # 与计划门同一口径（复核第 7 轮 P2）：`fact_refs` 必须存在、是数组、
        # 元素是**真正的整数**——`int("2")`/`int(True)`/`int(2.0)` 会把写错类型
        # 静默变成「引用了另一条事实」，应用器不能替模型圆场。
        _refs = _e.get("fact_refs")
        if not isinstance(_refs, list):
            return None, ("`narration_lines` 第 %d 条缺 `fact_refs`（整数数组；这条旁白"
                          "不承载任何事实就写 `[]`）：%s" % (_i, str(_e)[:40]))
        _bad = [x for x in _refs if isinstance(x, bool) or not isinstance(x, int)]
        if _bad:
            return None, ("`narration_lines` 第 %d 条的 `fact_refs` 里有非整数项（%s）"
                          "——必须写整数序号" % (_i, str(_bad[0])[:20]))
        _out.append({"text": _e.get("text").strip(),
                     "source": _e.get("source").strip(),
                     "fact_refs": [int(x) for x in _refs]})
    return _out, ""


def _hook_carrier_decl_text(raw) -> str:
    """把 `hook_carrier` 的**受支持形态**归一成声明串（空串 = 清除）.

    形态两种（历史兼容）：声明串 `"B3/台词2；B5/画面"`、对象 `{"beat": 3, "mode": "台词2"}`。
    **归一之后走同一条校验**——2026-10-02 复核 P2：旧实现给对象形态单开一条分支，直接
    拼出 `B{beat}/{mode}` 写进计划，越界编号（`B999/台词1`）与非法载体一律放行、记成
    "应用成功"，问题要等到后续验收才出现（C 的规定是越界操作**先判 `illegal_response`**）。
    """
    if isinstance(raw, dict):
        _hb = raw.get("beat")
        return "B%s/%s" % (str("" if _hb is None else _hb).strip(),
                           str(raw.get("mode") or "").strip())
    return str(raw or "").strip()


#: 身份清单的渲染上限：超过就**显式给出总数**（复核之六 P2 同一条口径——反馈与拒收
#: 记录里不许 `[:N]` 静默省略）。这里渲染出的字符串会**进下一轮的请求**：只点名前几条
#: 会让模型以为剩下的不存在。
_ID_BRIEF_CAP = 12


def _brief_ids(items, cap: int = _ID_BRIEF_CAP) -> str:
    """身份清单渲染成一行；超过上限时给「等 **共 N 条**」而不是静默砍掉."""
    _it = [str(x) for x in items]
    if len(_it) <= cap:
        return "，".join(_it)
    return "%s 等 **共 %d 条**" % ("，".join(_it[:cap]), len(_it))


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
    # **本地响应校验**（工作包 §4 C；复核 2026-10-02 之三 P1）：一处按**请求 schema** 核对
    # 形态——必填键、`null` 的允许位置、类型、以及**没定义过的键**（含嵌套条目）。旧实现把这
    # 些散成几条手写判断，结果 ①旁白 `text: 123` 被 `str()` 圆场、②旁白条目里的未知键被丢掉、
    # ③必填键 `dialogue_ops: null` 被当缺席读——三种"违反严格 schema 的响应"都能应用成功。
    # 这里一律拒收（`illegal_response`），**不靠转换或丢字段修正模型响应**。
    from step01_script_gen.plan_feasibility import (
        replan_ops_response_problems, REPLAN_OP_KEYS, effective_decl_ops)
    _shape = replan_ops_response_problems(ops)
    if _shape:
        # 截断**必须显式**，而且**总数写在最前面**：这条文本会进下一轮请求的拒收反馈，
        # 消费方（历史 200 字 / 拒收记录 400 字 / 提示块）都按**前缀**截断——把"另有
        # N 条"放在句尾等于没写（复核 2026-10-02 之五实测：尾巴先被截掉）。
        _head = ("%d 条形态问题：" % len(_shape) if len(_shape) <= 6
                 else "%d 条形态问题（只列前 6 条）：" % len(_shape))
        return None, "invalid_result: %s%s" % (_head, "；".join(_shape[:6]))
    _dlg = list(ops.get("dialogue_ops") or [])
    _bat = list(ops.get("beat_ops") or [])
    # **声明类操作**（`fact_carriers` / `hook_carrier` / `hook_spoken` / `narration_lines`）
    # 同样算"要改"：复核第 45 轮实测——只给声明时旧实现落 `no_change`，即"承载/钩子声明
    # 修不了"，而反馈块早就把它们列成"可以改"（请求与消费契约自相矛盾）。
    _decl = effective_decl_ops(ops)
    if not _dlg and not _bat and not _decl:
        return None, "no_change"

    new = json.loads(json.dumps(plan, ensure_ascii=False))
    from step01_script_gen.plan_feasibility import plan_narration_entries
    lines = list(plan_dialogue_lines(new))
    n_lines = len(lines)
    # **本批改完之后的旁白表**（复核 2026-10-02 P1）：先归一、先落地，之后所有
    # `旁白k` 的**引用范围**都按它校验——同一批"新增旁白 + 指定它由哪条节拍承载"
    # 是完全合法的联动修法，不能拿旧计划的条数把它判越界。
    _narr_out, _why_narr = _norm_narration_entries(ops.get("narration_lines"))
    if _why_narr:
        return None, _why_narr
    if _narr_out is not None:
        new["narration_lines"] = _narr_out
    # **本批完成后的**旁白条数：`旁白k` 的**引用范围**与残留**孤儿**都按它算
    # （复核 2026-10-02 之二 P1：按旧表枚举会把本批**删掉**的旁白当成残留孤儿）
    _n_narration = (len(plan_narration_entries(plan)) if _narr_out is None
                    else len(_narr_out))
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
                                             require_carrier=True,
                                             n_narration=_n_narration)
                if _why:
                    return None, "第 %d 条节拍 split 第 %d 段：%s" % (_i, _j, _why)
                _rendered.append(_txt)
            _edit_ops[_i] = (_kind, _rendered)
        elif _kind == BEAT_OP_INSERT_AFTER:
            _txt, _why = _render_beat_op(op.get("new_text"), op.get("new_carrier"),
                                         None, n_lines, require_carrier=True,
                                         n_narration=_n_narration)
            if _why:
                return None, "第 %d 条节拍（插入）：%s" % (_i, _why)
            _insert_ops[_i] = [_txt]
        else:
            _txt, _why = _render_beat_op(op.get("new_text"), op.get("new_carrier"),
                                         _old, n_lines, n_narration=_n_narration)
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
    from step01_script_gen.plan_feasibility import plan_narration_entries
    # 承载关系的残留检查**按候选计划**（复核 2026-10-02 之二 P1）：旧实现按**旧表**枚举，
    # 于是"同批删除 N2、并把承载它的节拍改成画面"这种合法联动修法会被判「N2 无承载」
    # ——**被本批删掉的那条**被当成残留孤儿。两轴分开判：
    # - **节拍侧**（重复/非法标注）与**台词**孤儿：本批改坏了就拒收（既有裁决：改台词/
    #   节拍必须保持每句被承载）；
    # - **旁白**孤儿交**同一条验收链**：`narration_lines` 是**声明表**，新增/删除/替换
    #   都由声明操作表达，"还没人承载"是**结果不完整**（验收链报 `orphan_uncarried`，
    #   控制器据此反馈下一轮），不是"操作非法"（在这里拒收会以 `illegal_response`
    #   结束整轮，模型连反馈都拿不到）。身份取自**记录**，不从标签串反解。
    from step01_script_gen.plan_feasibility import carrier_relation_records
    _rel = carrier_relation_records(new.get("shot_beats"),
                                   len(plan_dialogue_lines(new)), _n_narration)
    _dup = ["B%d" % r["index"] for r in _rel if r["code"] == "carrier_duplicate"]
    _unlabeled = ["B%d" % r["index"] for r in _rel if r["code"] == "carrier_unlabeled"]
    _orphan_d = [r for r in _rel if r["code"] == "orphan_uncarried"
                 and r["kind"] == "dialogue"]
    # **责任范围 = 本批能改的东西**（复核 2026-10-02 之六 P1）：承载关系只有**台词操作**
    # （换掉某句 ⇒ 原指向它的节拍不再承载它）与**节拍操作**（改/插入/拆分 ⇒ 载体标注变了）
    # 才动得了；声明键（`fact_carriers` / `hook_*` / `narration_lines`）改不了台词内容，
    # 也改不了节拍上的「载体：」标注。旧写法对**每个**批次都跑这三条检查，于是"只修一条
    # 事实承载"的合法修订被**基线里本来就有的**孤儿台词判成 `illegal_response`
    # （离线实测：同一响应去掉基线孤儿后正常应用）。基线缺陷该由**验收链**报告、由控制器
    # 反馈下一轮，不该在这一批头上记一笔。改台词/节拍的闭合要求**继续保留，不放宽**。
    _touches_carriers = bool(_dlg or _bat)
    if _touches_carriers:
        if _dup:
            return None, "节拍载体标注重复：%s（每条节拍只许一个载体）" % _brief_ids(_dup)
        if _unlabeled:
            return None, ("节拍缺载体标注：%s（计划带了标注就必须每条恰有一个载体）"
                          % _brief_ids(_unlabeled))
        if _orphan_d:
            return None, ("替换后 %s 无任何节拍承载（承载关系必须双向闭合）"
                          % _brief_ids("D%d" % r["index"] for r in _orphan_d))
    # 只核**被改写的**条目：未改动的条目是既有状态（早就过了流水线），把整份计划
    # 一起核会把「原本就抄得不精确的旧条目」也算到本轮头上而误拒——操作式的语义
    # 是「代码在旧计划上打补丁」，责任范围就是补丁本身。
    if _changed:
        _why = _plan_evidence_intact({"shot_beats": _changed, "required_facts": []},
                                     novel_text)
        if _why:
            return None, _why
    # 替换台词后：事实/钩子的承载必须**逐项声明**（2026-09-30 复核裁决）——
    # 计划必须自洽，否则「生成按 dialogue_lines 写、审计按事实/钩子判」两边各自
    # 自洽，任何修复轮都改不动。不过就拒收本轮（见函数注释的完整理由）。
    _cd = _plan_carrier_declarations_intact(new, ops, len(plan_dialogue_lines(new)),
                                            n_narration=_n_narration)
    if _cd:
        return None, _cd
    # **应用后内容确实没变 ⇒ no_change**（工作包 §4 C）：例如把 `hook_carrier` 显式清成
    # 它本来就是的空值、或把某条台词换成它自己——"显式操作"不等于"内容变了"。
    if json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str) ==             json.dumps(new, ensure_ascii=False, sort_keys=True, default=str):
        return None, "no_change"
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
    # 承载段（`｜承载：…`）是**后加的**登记信息，加它不等于改了事实本体 ⇒ 比对前剥掉
    _old_facts = [_strip_fact_carrier(f) for f in (_old.get("required_facts") or [])]
    _new_facts = [_strip_fact_carrier(f) for f in (_new.get("required_facts") or [])]
    _miss = [f for f in _old_facts if f not in _new_facts]
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
        for quoted in _evidence_quotes(text):
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


#: 必保留事实条目里的**承载段**（`｜承载：B2/画面`）——承载关系必须**落进计划**，
#: 否则只在 ops 里存在，`render_execution_contract` 传不给生成器（2026-09-30 复核）。
#: **实现与判据都在 `plan_feasibility`**：那道门是首版/重选版/缓存版**共用**的
#: （复核：「前移到所有计划的生成前验收……不接受『只有重选受约束』」），本文件只保留
#: 旧名转发，不维护第二份实现。
_FACT_CARRIER_SEG = "承载："


def _strip_fact_carrier(fact) -> str:
    """去掉事实条目里的承载段（不变量比对用：加了承载 ≠ 改了事实本体）."""
    from step01_script_gen.plan_feasibility import strip_fact_carrier
    return strip_fact_carrier(fact)


def _plan_carrier_declarations_intact(new: dict, ops: dict, n_lines: int, *,
                                      n_narration=None) -> str:
    """重选**替换了选用台词**时，必须逐项声明事实与钩子的承载；返回 "" = 通过.

    2026-09-30 复核裁决（第二章「计划选 5 句、审计却报漏句」的实质）：

    - `required_facts` 的「原文证据」是事实的**出处**，旧台词被换掉**不使出处失效**
      ⇒ 既不机械改写引文，也**不**仅因出处引用了未选台词就拒收；
    - 真正的缺口是**事实/钩子没有明确的成片承载**（实测：F2「四年前潜入青州」无承载；
      `end_hook` 要求「喃喃自语」绣春刀那句，而所选 5 句不含它）。审计报的其实是
      **内容缺失**，只是归因写成了「漏选用台词」——生成侧只按 `dialogue_lines` 写、
      审计侧按事实/钩子判，两边各自自洽，修复轮永远改不动；
    - 因此：替换台词时，重选结果必须**逐项**声明每条必保留事实的承载节拍与表达方式；
      **不过就拒收本轮**（不接受带警告放行，也不自动加重选轮次）。

    ⚠️ 本函数只做两件事：**把 ops 声明落进计划**（持久字段，随执行契约传给生成器）
    + 调 `plan_feasibility.plan_contract_problems`（**首版/重选版/缓存版共用的那道门**）。
    判据**不在这里实现**——同一套承载规则维护两份，等于改一处、另一处照旧。

    作用范围：`ops` 里没有 `dialogue_ops` 时不做这套校验——没换台词 ⇒ 承载关系不变，
    沿用既有的证据/不变量/承载闭合三项校验；首版与缓存版的承载由生成前统一入口
    （`check_plan_before_generation`）的契约门判。
    """
    if not isinstance(ops, dict):
        return ""
    from step01_script_gen.plan_feasibility import (
        FACT_CARRIER_SEG, carrier_mode_problem, coerce_ref_index, effective_decl_ops,
        parse_carrier_decl, plan_contract_problems, plan_has_carrier_contract,
        plan_narration_entries, strip_fact_carrier)
    # 改台词/节拍 ⇒ 编号可能顺延，承载必须逐项重声明（既有裁决）；**只有声明**的批次
    # 也必须走到这里（复核第 45 轮：旧实现在入口就落 `no_change`，声明根本改不动）。
    _touched_ids = bool(ops.get("dialogue_ops") or ops.get("beat_ops"))
    if not _touched_ids and not effective_decl_ops(ops):
        return ""
    facts = list((new or {}).get("required_facts") or [])
    decls = (ops or {}).get("fact_carriers")
    _hc_raw = ops.get("hook_carrier")
    # 2026-09-30 复核（第 3 组）：承载维护**不能按「本批改没改台词」决定**。
    # 只要**计划带承载契约**（v2：节拍标了载体 / 事实声明了承载），任何会改变节拍编号
    # 或台词编号的操作（**含 `beat_ops` 的插入与拆分**）都必须把承载重新声明一遍——
    # 否则 `承载：Bn/…` 会指到顺延后的别条节拍上，且新旧编号都「在范围内」，静默错位。
    # 没有承载契约的历史计划沿用容忍口径（无可核 ≠ 被破坏），由生成前的统一入口判。
    if not plan_has_carrier_contract(new) and not decls and not _hc_raw:
        return ""
    n_beats = len((new or {}).get("shot_beats") or [])
    # `旁白k` 的合法范围按**本批完成后**的表算（调用方已落地 `narration_lines`；
    # 复核 2026-10-02 P1）：缺参数时退回旧行为（本函数的历史调用方）。
    if n_narration is None:
        n_narration = len(plan_narration_entries(new))
    if decls is None and not _touched_ids:
        decls = []          # 声明独立修改：没提 `fact_carriers` = 不改承载
    if decls is not None and not isinstance(decls, list):
        return ("`fact_carriers` 必须是对象数组（现在是 %s）——每条给出 "
                "{index, beat, mode}" % type(decls).__name__)
    if not isinstance(decls, list):
        return ("本批改动了节拍/台词，必须逐项声明必保留事实的承载（缺 fact_carriers）："
                "每条事实都要给出承载节拍与表达方式，否则承载会指到顺延后的别条节拍上，"
                "或变成「审计报内容缺失、生成侧无从遵从」")
    by_index: dict = {}
    for d in decls:
        if not isinstance(d, dict):
            return "fact_carriers 条目必须是对象：%s" % str(d)[:40]
        _i = coerce_ref_index(d.get("index"), "")
        if _i is None:
            return "fact_carriers 条目的 index 必须是数字：%s" % str(d)[:40]
        if _i in by_index:
            return "必保留事实 F%d 的承载被声明了多次" % _i
        by_index[_i] = d
    for _i in sorted(by_index):
        if _i < 1 or _i > len(facts):
            return "fact_carriers 指向不存在的必保留事实 F%d（共 %d 条）" % (_i, len(facts))
    # **逐项给全**只对"改了台词/节拍"的批次成立（编号顺延会静默错位）；**声明独立修改**
    # 只落声明到的那几条——否则"只清钩子承载"这种批次会因为 F1 没被重新声明而被拒。
    _missing = ([i for i in range(1, len(facts) + 1) if i not in by_index]
                if _touched_ids else [])
    if _missing:
        return ("必保留事实 %s 未声明承载——替换台词后每条事实都必须有承载节拍与"
                "表达方式（否则审计会报内容缺失，而生成侧无从遵从）"
                % "、".join("F%d" % i for i in _missing[:4]))
    # ---- ① 声明落进计划：`｜承载：Bn/mode`（本体与原文证据一字不改）+ hook_carrier ----
    # 结构先判（工作包 §4 C：**越界编号 ⇒ 非法响应**）：种类与编号范围在这里挡，
    # "这条承载是否真演出了事实""画面/声音是否一致"这类**语义**仍归统一契约门。
    # 未声明到的条目**原样保留**（声明独立修改是"逐条打补丁"；整表重写只在
    # `_touched_ids` 那条路径上发生——那时 `_missing` 已经要求逐项给全）
    _segs = [str(f) for f in facts]
    for _i in sorted(by_index):
        _d = by_index[_i]
        _bi = coerce_ref_index(_d.get("beat"), "")
        if _bi is None or not 1 <= int(_bi) <= n_beats:
            return ("fact_carriers 的承载节拍越界：F%d → B%s（共 %d 条节拍）"
                    % (_i, _d.get("beat"), n_beats))
        _mode = str(_d.get("mode") or "").strip()
        _mp = carrier_mode_problem(_mode, n_lines, n_narration)
        if _mp:
            return "fact_carriers F%d：%s" % (_i, _mp)
        # 只对**声明到的那条**去掉旧承载段再写新声明（未声明的条目原样保留）
        _segs[_i - 1] = "%s｜%sB%d/%s" % (strip_fact_carrier(_segs[_i - 1]),
                                          FACT_CARRIER_SEG, int(_bi), _mode)
    new["required_facts"] = _segs
    # `narration_lines` **不在这里处理**：它已由 `apply_replan_ops` 在入口处
    # 归一并落地——旁白条数要先算出来，才能校验本批写下的所有 `旁白k` 引用
    # （复核 2026-10-02 P1：同批新增旁白并指定承载节拍曾被判越界）。
    if ops.get("hook_carrier") is not None:          # `null` = 缺席 = 不改
        _hc_txt = _hook_carrier_decl_text(ops.get("hook_carrier"))
        if not _hc_txt:
            # 显式空值 = **清掉**旧声明（2026-09-30 复核第 4 轮 P2-4；2026-10-02 起 schema
            # 不再强制这个键 ⇒ "不改"由**省略**表达，"清除"由空串表达，两者不再混同）。
            # 旧实现只在非空时覆盖，于是「钩子不再要求说出口」这一批会把旧声明留在计划里
            # → 变成「声明了承载、却没有要求说出口的句子」，被多声明判据拒收、且无法修复。
            # **复核之十二：清除一个空声明必须幂等**——`hook_carrier: ""` 与"没有这个键"
            # 是**同一个状态**（首版严格 schema 下这个键恒为 `""`），而旧写法无条件 `pop`：
            # 于是"清除"本身被记成内容变过（空串 → 键消失），`no_change` 判不出来、
            # 计划也白白多了一次无意义的写。
            if str(new.get("hook_carrier") or "").strip():
                new.pop("hook_carrier", None)
        else:
            # **两种形态走同一条校验**（2026-10-02 复核 P2）：编号范围与载体种类都在这里
            # 挡掉，越界/非法一律拒收本轮，不许带着坏声明进后续验收。
            for _d in _hc_txt.split("；"):
                _decl = _d.strip()
                if not _decl:
                    continue
                _b, _kind, _k = (parse_carrier_decl(_decl) or (None, None, None))
                if _b is None:
                    return ("`hook_carrier` 里的声明非法：%r（应为 `B2/台词1` 或 `B2/画面`）"
                            % _decl[:24])
                if not 1 <= int(_b) <= n_beats:
                    return "`hook_carrier` 的节拍越界：%s（共 %d 条节拍）" % (_decl[:24], n_beats)
                _mp = carrier_mode_problem(("画面" if _kind == "画面" else "%s%s" % (_kind, _k)),
                                           n_lines, n_narration)
                if _mp:
                    return "`hook_carrier`：%s" % _mp
            new["hook_carrier"] = _hc_txt
    if ops.get("hook_spoken") is not None:           # `null` = 缺席 = 不改
        _hs = ops.get("hook_spoken")
        if isinstance(_hs, str):
            _hs = [_hs] if _hs.strip() else []
        if not isinstance(_hs, list):
            return ("`hook_spoken` 必须是字符串数组（现在是 %s）——钩子要求说出口的"
                    "台词逐字给全；不再要求时说出口的句子就给空数组" % type(_hs).__name__)
        _bad = [x for x in _hs if not isinstance(x, str) or not x.strip()]
        if _bad:
            return ("`hook_spoken` 里有空/非法条目（%r）——必须是原文逐字台词"
                    % (str(_bad[0])[:20],))
        new["hook_spoken"] = [x.strip() for x in _hs]
    # ---- ② 改过台词/节拍的批次：结果必须当场闭合契约（既有裁决：不过就拒收本轮）----
    if not _touched_ids:
        # **声明独立修改**：没有编号顺延的风险，语义问题（承载是否真演出、画面是否一致）
        # 交**同一条**验收链判断——拒收/继续由控制器按统一停止原因收口（工作包 §4 C）。
        return ""
    # 校验交给共用契约门（编号/范围/语义交集/画面一致/钩子整句）
    return "；".join(plan_contract_problems(new)[:2])



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
    # 复核第 41 轮 P1：核验/重映射的异常**不许在这里被吞掉**——本函数在候选副本上
    # 运行，异常一律传到事务边界（`_validate_outline` 据此拒收，基线一个字节不改）。
    # 旧写法"告警后 return / 继续"会把**半成品**（已剔除台词但引用没重映射）当候选提交。
    from step01_script_gen.plan_feasibility import (
        plan_dialogue_lines, verify_dialogue_lines)
    kept, rejected = verify_dialogue_lines(plan_dialogue_lines(outline), novel_text)
    if not rejected:
        return
    outline["dialogue_lines"] = kept
    # v6.157（2026-09-30 复核 P1-3）：剔除台词会**压缩 D 编号**——引用必须同步重映射。
    # 本入口（首版/缓存都经它）此前只改 `dialogue_lines`，于是节拍载体、事实承载、
    # 钩子承载仍指旧编号（「事实仍指旧 D2、钩子仍指不存在的 D3」）。重映射与
    # `plan_feasibility._drop_unverified` **同一个函数**，不新造第二套。
    from step01_script_gen.plan_feasibility import _remap_dialogue_refs
    _notes = _remap_dialogue_refs(outline, lines, kept)
    print("[WARN] 大纲选用台词 %d 句未在原文中逐字找到，已剔除：%s"
          % (len(rejected), "；".join(str(d.get("line"))[:20] for d in rejected)))
    for _n in _notes:
        print("[WARN] %s" % _n)


#: 元素位置**被其他字段按序号引用**的列表：`shot_beats` 的序号就是 `B{n}`（节拍
#: 载体「台词k」、事实载体 `承载：Bn/台词k`、`hook_carrier` 都按它编号），
#: `dialogue_lines` 的序号就是 `D{n}`（同一批声明的台词侧编号），
#: `required_facts` 的序号就是 `F{n}`（旁白条目的 `fact_refs` 按它引用，
#: 2026-09-30 复核第 7 轮 P1 补入——旧注释写的「没有别的字段按它们的序号引用」
#: 在新增 N 引用之后不再成立）。
_POSITIONAL_FIELDS = (("shot_beats", "B"), ("dialogue_lines", "D"),
                      ("required_facts", "F"))

#: 上面这些字段的名字集合（后面「只 strip、不压缩」的判断用它）
_POSITIONAL_NAMES = tuple(_f for _f, _ in _POSITIONAL_FIELDS)


def _brief_value(value, limit: int = 60) -> str:
    """非法元素的**短摘要**（诊断与回灌提示用；不落整份对象）."""
    _s = repr(value)
    return _s if len(_s) <= limit else _s[:limit] + "…"


def _malformed_numbered_item(outline: dict, field: str, tag: str) -> dict | None:
    """编号字段里的非法元素 → **具体记录**（哪一项、实际是什么、期望是什么）；合法 ⇒ None.

    2026-10-02 复核之五：旧写法只返回 `True`，于是"具体哪一项、实际长什么样"只打在
    stdout，诊断与回灌提示里都只剩一句"`shot_beats` 里有非法项"。ch2 实跑正是这个形态：
    模型把节拍写成了对象，终局诊断却只说"首版结构校验未通过（关键字段缺失/类型错误）"，
    既指不出规则也回灌不了。记录由**生产者**给（与 §3"身份由生产者给"同一条规矩），
    消费方（终局诊断、下一轮请求）只透传。
    """
    values = outline.get(field)
    if values is None:
        return None                       # 字段缺失另有判据（不在这里补空表）
    if not isinstance(values, list):
        print("[WARN] 大纲校验失败：%s 不是列表（%r）——%s 编号无法核验，拒收"
              % (field, values, tag))
        return {"index": None, "path": field,
                "reason": "`%s` 不是数组（%s）——%s 编号无法核验，本计划拒收"
                          % (field, type(values).__name__, tag)}
    for i, item in enumerate(values, 1):
        if not isinstance(item, str) or not item.strip():
            _what = ("是空字符串" if isinstance(item, str)
                     else "不是字符串（%s）" % type(item).__name__)
            print("[WARN] 大纲校验失败：%s 第 %d 项非法（%r）——它的序号是 %s 编号，"
                  "删掉会让后面整体前移；本计划拒收（不静默压缩）"
                  % (field, i, item, tag))
            return {"index": i, "path": field,
                    "reason": "`%s` 第 %d 项%s：%s——这个数组的每一项都必须是**字符串**"
                              "（形如 `节拍：<正文>｜本镜信息目的：…｜原文依据：…｜载体：…`），"
                              "它占着 %s 编号，删掉/改写会让后面整体前移；本计划拒收"
                              "（不静默压缩、不把对象转成字符串）"
                              % (field, i, _what, _brief_value(item), tag)}
    return None


def _reject_malformed_numbered_items(outline: dict, field: str, tag: str) -> bool:
    """编号字段里出现非法元素（非字符串 / 空白 / 整个不是列表）→ **拒收，不压缩编号**.

    2026-09-30 复核第 4 轮 P1-2：旧实现把这类元素**过滤掉**再继续，于是 `B{n}`/`D{n}`
    整体前移、所有引用静默指错（实测形态：`hook_carrier="B2/台词1"` 在删掉一个空节拍
    后仍然写着 B2，却已经指到别条节拍上）。要求是「改序列位置的操作收进同一事务」，
    这里取更省事的另一半：**非法项直接拒收**——计划不合法就重生成，不做静默改意。

    （兼容名：判据与记录归一到 `_malformed_numbered_item` 一处。）
    """
    return _malformed_numbered_item(outline, field, tag) is not None


def _validate_outline(outline: dict | None, novel_text: str = "") -> dict | None:
    """大纲合法性校验（C2 修复 08-11）.

    校验项：
    1. main_events 有 3-6 条非空事件（空列表/全空串 → 不合格）
    2. climax 非空（高潮事件是分镜分布的锚点，空则锚点失效）
    3. required_facts / causal_chain 非空，shot_beats 至少覆盖每条事件
    4. 净化：列表元素 strip；**编号字段（shot_beats / dialogue_lines /
       required_facts）不压缩、不过滤**——非法元素一律拒收
       （见 `_reject_malformed_numbered_items`）。`required_facts` 的序号就是
       `F{n}`（旁白 `fact_refs` 按它引用），过滤空项会让编号整体前移、引用静默指错
    5. v6.131：`dialogue_lines` **逐字来自原文**核验（见 `_check_outline_dialogue_grounding`）
    不合格返回 None（调用方不注入空锚点，避免「【本章主要事件】」空标签进 prompt）。
    """
    if not isinstance(outline, dict) or not outline:
        return None
    # **A 步 5：净化也走候选副本**（工作包 §3）。此前是就地改传入的 dict ——
    # 诊断没过就直接 `return None`，但**输入已经被改过一半**（`main_events` 已净化、
    # 版本戳可能已盖）。现在所有净化只发生在副本上，**失败 ⇒ 基线一个字节不改**；
    # 通过才把副本写回并返回（保持既有"就地生效 + 返回对象"的调用契约）。
    _baseline = outline
    outline = copy.deepcopy(outline)
    # **结构判据只有一份**（工作包 §3 / 复核第 36–37 轮）：本函数不再自持内联副本，
    # 直接消费 `outline_structure_problems`（纯诊断、不改输入）。净化与版本戳留在
    # 本函数里作为**独立事务**，净化后由调用链重新验收。
    _bad = outline_structure_problems(outline)
    if _bad:
        print("[WARN] 大纲校验失败：%s（不注入不完整大纲）"
              % (str(_bad[0].get("reason") or _bad[0].get("code"))[:80]))
        return None
    events = outline.get("main_events") or []
    if isinstance(events, list):
        cleaned_events = [str(e).strip() for e in events if str(e).strip()]
        outline["main_events"] = cleaned_events
    else:
        cleaned_events = []
        outline["main_events"] = []
    # 编号字段先查非法项：`main_events` / `causal_chain` 的过滤是安全的（没有别的
    # 字段按它们的序号引用），而 `shot_beats` / `dialogue_lines` / `required_facts`
    # 的删除会移动 B/D/F 编号——旁白条目的 `fact_refs` 正是按 F 序号引用的。
    # 旁白条目同样按**序号**被引用（`旁白k`）⇒ 非法项一律拒收，不压缩编号
    for field in ("required_facts", "causal_chain", "shot_beats"):
        values = outline.get(field)
        if field in _POSITIONAL_NAMES:
            # 编号字段：只 strip（不改长度）。非法项已在上面拒收，这里**不许**过滤，
            # 否则元素位置一变，按序号引用它的那些声明就整体指错（复核第 7 轮 P1）。
            if not isinstance(values, list) or not values:
                print(f"[WARN] 大纲校验失败：{field} 为空（不注入不完整大纲）")
                return None
            outline[field] = [item.strip() for item in values]
            continue
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
            # `dialogue_lines` 的非法项已在上面拒收，这里只剩 strip（不改长度）；
            # `omittable` 没有编号引用，仍按净化口径过滤。
            outline[field] = ([item.strip() for item in values
                               if isinstance(item, str) and item.strip()]
                              if field == "omittable"
                              else [item.strip() for item in values])
    # **事务边界**：净化与核验的任何异常 ⇒ 拒收（返回 None），基线保持原样。
    # 候选是深复制，所以"拒收"不需要额外回滚——这正是候选副本事务的意义。
    try:
        _check_outline_dialogue_grounding(outline, novel_text)
    except Exception as _exc:                     # noqa: BLE001
        print("[WARN] 大纲净化事务失败（保留基线，不注入不完整大纲）：%s" % _exc)
        return None
    # 2026-09-30 复核第 5 轮 P2-3：**把契约版本写进计划本体**。消费入口（契约门、
    # 钩子字段口径）据此区分「v3 的新稿」与「标注了旧版本的老计划」——兼容必须
    # 依据明确版本，不能依据"字段缺失"（否则新稿漏写字段会被当成"没有要求"放过）。
    # 首版、缓存复用、重选结果都经本函数，故一处即覆盖三条入口。
    from step01_script_gen.plan_feasibility import PLAN_CONTRACT_VERSION
    outline["plan_contract_version"] = PLAN_CONTRACT_VERSION
    _baseline.clear()                     # **采纳**：把净化后的候选写回原对象
    _baseline.update(outline)
    return _baseline


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
