"""step01_script_gen.outline - 章节大纲生成（ShortVideoDirector director-outline 吸收）.

在分镜生成前，先用 LLM 生成本章大纲（主要事件/角色出场/情绪弧线/集尾钩子），
注入分镜 prompt 作为「大纲锚点」，让分镜生成参考大纲，保证冲突/高潮分布。
"""
from __future__ import annotations

from shared.config import (DURATION_TARGET_MIN_SEC, DURATION_TARGET_MAX_SEC,
                           duration_band, voice_allocation_limits)

from shared.fact_metadata import (FACT_METADATA_VERSION, LEGACY_ACCEPTANCE_FIELD,
                                  LEGACY_ACCEPTANCE_VERSION)

import copy
import json
import hashlib
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from _text_gen import create_service, TextGenerationRequest
from _text_gen.deepseek_schema import OUTLINE_SCHEMA, REPLAN_OPS_SCHEMA
#: 呈现协议的常量与 codec（**单一事实源**在 `shared/presentation_contract.py`）——
#: 这里只 import 名字，不复制取值。
from shared.presentation_contract import (MODE_HYBRID, MODE_LEGACY, PLAN_CONTRACT_VERSION_BY_MODE,
                                          DELTA_SCHEMA, PRESENTATION_SCHEMA, REVISION_SCHEMA,
                                          apply_delta_view, apply_editable_view,
                                          carrier_capability_table,
                                          delta_request, plan_from_view,
                                          plan_mode, revision_request, view_from_plan)
from step01_script_gen.plan_feasibility import (PLAN_CONTRACT_VERSION,
                                                normalize_plan_ranges,
                                                source_norm_note)
from step01_script_gen.plan_revision import DEFAULT_MAX_CALLS as _REVISION_MAX_CALLS
from step01_script_gen.plan_revision import (failure_detail, residual_class,
                                             STOP_INVALID_TARGET,
                                             STOP_VERIFICATION_FAILED)




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
  "background_facts": ["f02"],
  "causal_chain": ["起因→行动→结果→悬念"],
  "character_motives": ["角色：想做什么；为什么；原文证据：..."],
  "shot_beats": ["节拍：...｜本镜信息目的：...｜原文依据：...｜载体：台词k 或 旁白k 或 画面"],
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
  部分，没有任何机制能证明它被承载）。
  **「原文证据」还必须是一段连续原文**：不得用「……」跳过中间叙述。逐字承载（台词/旁白）
  只能取一段连续原文，把两段相隔很远的话用省略号拼成一条事实，无论改哪条旁白都只能
  覆盖一半，而事实登记后被冻结、修订层改不了，整章会停在计划层。一条事实横跨两段
  不连续原文时，就**各自另立一条事实**——每条只引用一段连续原文、各自声明承载。
  **主张要写成原文直接成立的核心事实，不绑非必要的修辞限定词**：尤其别把「若非…也不会…」
  「险些…」这类假设/反事实句里的字面限定抄进主张——证据句含否定/条件词时，主张只保留
  上下文确已成立的核心判断（例：证据是「若非如此，他也不会花重金买下」，主张写「她是他
  花重金买来的」即可，不要写成带具体价码的强断言）。核验按主张逐项判，多抄一个限定就多
  一处 `fact_basis_invalid`，而修订额度是有限的。
  **主张里不得出现原文证据里没有的细节**（2026-11-15 实测 run 20261115T131952d 终局）：
  凶器/器物名、时间先后、因果关系都要以证据句为准——证据只写「一道寒光一闪」就**不许**
  主张写「用短刀刺进」（凶器是脑补）；两件事原文没明说先后/因果就**不许**用「便/于是/
  接着」连成一句（「三年孝期刚过**便**考中秀才」的「便」是原文没有的因果）。拿不准就
  写证据句直接支撑的那个弱断言，核验按「原文是否支持主张」逐项判，超出的每个细节都是
  一处 partial 终局阻断。
  **规格类信息必须独立成条**：尺寸、数量、容量、方位等会进入画面的硬规格
  （如船宽、载客人数、行程距离），须各自登记为独立事实、附原文证据并绑
  fact_refs——同一实体全章只有一个值，禁止在不同节拍里出现互相冲突的规格。
  **每条还要声明承载**（写在条目末尾）：`｜承载：B节拍号/台词k` = 由第 k 句选用台词说出口，
  `｜承载：B节拍号/画面` = 由该节拍的**画面**演出。承载必须是**真的演出这条事实**的那条节拍：
  代码会拿这条事实（含原文证据）与承载节拍的**正文**（镜头真的拍什么）或**选用台词**比对；
  **承载必须能完整表达这条事实的「原文证据」**——逐句都要表达得到。表达不了（原文那句太长、
  一镜说不完，或画面拍不出来）就**各自另立一条事实**，别硬挂一条只说得出一半的载体。
  ——① 只把事实引文抄进节拍的「原文依据」**不算**演出；② 只在「本镜信息目的」里写上这条
  事实、**正文里没有人名/地点/动作等要素**，也**不算**演出（声明不能自证），会被拒收。
  画面承载的节拍必须标「载体：画面」，台词承载的节拍必须标「载体：台词k」。
  **交代类事实**（人物身份/年龄/来历）的合法出路只有三条：① 把它写进某条节拍的**正文**
  （让镜头真的拍出来）；② 在 `dialogue_lines` 里选一句**人物台词**把它说出口
  （逐字来自原文；**旁白/叙述句不算台词**，不得把叙述文字改标成台词），并按编号声明承载；
  ③ 本集确实拍不出也说不出口，就**不要列进 required_facts**。
  **背景档**（2026-11-15 §10.6 两档判据，可选）：**不承担主线因果解释责任**的纯背景
  事实（人物过往履历、身世细节——观众不知道也不影响看懂本集因果），登记后可在计划里
  另立 `"background_facts": ["f02", ...]`（**已登记事实的编号**，按 required_facts
  顺序 f01/f02/…）声明为背景档：背景档事实表达不完整（partial）时**放行**，不再进
  修复轮耗额度。**主线因果事实（身份揭示、动作因果、结局走向）不许列入**——判据对
  它们照旧全量核验；声明了不在清单里的编号会被拒收。不声明就全部按主线档（缺省从严）。
  注意：背景档**不是逃避表达的借口**——能表达就表达，它只是承认「这条信息观众
  不知道也不影响看懂」的登记选择；说反了（contradicted）照样拒收。
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
  **每条声音条目要有自己的承载节拍**（2026-10-04 第四次实跑根因）：一条节拍只能标
  **一个**载体，所以 k 条旁白至少要落在 **k 条不同**的节拍上（各标 `载体：旁白1`、
  `载体：旁白2`…），台词同理。把多条旁白/台词都挂在同一条节拍上会被拒收
  （结果是那几条**成片里没人念**）。需要 k 条声音条目，就准备 k 条承载节拍。
  **旁白要选「一镜说得完」的原文句**（同一次实跑的 ③④ 类）：整条估时不超过单镜上限；
  **不要把好几个分句用逗号串成一句长旁白**——句内只有逗号/顿号、没有句末标点的长句
  无法合法切分（会被 `budget_unrepresentable` 拒收，须重选），宁可少讲一条，也别硬选。
  **旁白按 20~35 字一条写**（约 5~9 秒；单条不超过 {{NARR_MAX_CHARS}} 字 / {{SHOT_CAP}} 秒），
  一条旁白只讲一个信息点，不要把一整段身世/前情塞进一条。
  **先算发声账再定条数**：对白＋旁白按 4 字/秒估时，**合计不得超过 {{DURATION_MAX}} 秒**
  （合法区间 {{DURATION_MIN}}-{{DURATION_MAX}} 秒；优先目标 {{TARGET_MIN}}-{{TARGET_MAX}} 秒，
  但**不在目标带不是失败**，不要为凑目标机械增删声音）——这是总下界，画面动作不占它。算下来装不下的内容，
  要么改由画面承载，要么**不列进 required_facts**；不要先把事实全登记上、再指望修订轮
  加声音解决——事实一旦登记就冻结，修订层改不掉，只会把整集挤爆。
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
- dialogue_lines: 本集**选用的逐字台词**，从原文**逐字摘出**（不改写、不润色、不自造）；
  只收**人物说出口的话**——判定标准是原文里由「某道／说／问／喝道」引出的**引号内**文字，
  **只抄引号内的正文**。原文里的**叙述句（旁白）不算台词**，内心独白（「心中暗道」）也不算，
  **不得把叙述文字改标成台词**充数（旁白由镜头自己的旁白承担，见下条）。
  ⚠️ **本集至少要留 1 条**真正的人物台词：把叙述句塞进 dialogue_lines 会被核验判
  `verify_voice_not_speech` 并迁到旁白，**迁完就没有台词了**，生成门会以
  「计划未选用任何逐字台词」拒收整份计划。只收真正要拍的句子，宁少勿滥——每一句都占用
  时长，而时长由代码逐句估算（自报秒数不参与判定）
- omittable: 本集**可省略的内容**（不拍也不影响主线的原文段落），供预算不够时重选
- 时长预算（**先算再选，v6.132**）：本集正片**合法 {{DURATION_MIN}}-{{DURATION_MAX}} 秒**、**规划目标 {{TARGET_MIN}}-{{TARGET_MAX}} 秒（优先，不是硬门）**。
  目标带是引导，不是硬门：装得下、节奏合理时，不要为了落在目标带内填充或压缩内容。
  对白是时长的最大一项，**必须给画面动作/反应/过场留出时间**——每场只留最关键的一两句，
  不要把整段对话都列进来。**选多了本集就装不下**：会被预算门打回重选（反馈会带上代码
  算出的秒数），白费一轮；更严重的是会把整集挤爆。
- causal_chain: 每个关键转折必须具备起因→行动→结果→悬念；**每条末尾用「（Bk）」
  标注它对应的节拍编号**（k 按 `shot_beats` 顺序、必须真实存在；一条因果对应多条
  节拍时写「（B2、B3）」）。下游按这个身份把过程/时间证据限定到该关系；确实没有
  对应节拍时写「（未落节拍）」，不得省略标注。
- character_motives: 说明角色为什么这样做，并附原文证据
- shot_beats: 覆盖每条 main_events，按原文顺序排列；每个节拍只承载一条主要信息，标出信息目的和证据；
  末尾必须标**载体**（v6.138 事件履约契约）：`载体：台词k` = 由 `dialogue_lines` 第 k 句承载
  （k 按 dialogue_lines 顺序编号）；`载体：画面` = 由画面动作承载、不占台词时长。
  第三条合法值是 `载体：旁白k`（由 `narration_lines` 第 k 条旁白讲出来）。
  一个节拍只能有一个载体；之后重选台词增删时，受影响节拍的载体必须同步更新。
  **格式硬约束（解析器逐字匹配）**：节拍条目里的承载段必须以**全角竖线 `｜`** 起头，
  逐字写成 `｜载体：画面`、`｜载体：台词k` 或 `｜载体：旁白k`——`｜` 是全角竖线
  （U+FF5C），`：` 是全角冒号（U+FF1A）。**不要**写成 `；载体：画面`（中文分号）、
  `|载体：画面`（半角竖线）、`｜载体:画面`（半角冒号）或 `载体：画面`（前面没有 `｜`）。
  解析器只按 `｜载体：` 逐字匹配；分隔符不对时，这条节拍会被当成**没有登记载体**，
  后面所有承载检查都会失败。正例：`节拍：黄昏水湾全景｜本镜信息目的：交代环境｜原文依据：“夕阳西下……”｜载体：画面`；
  反例：`节拍：黄昏水湾全景｜本镜信息目的：交代环境｜原文依据：“夕阳西下……”；载体：画面`。
  每条节拍**恰好一个** `｜载体：` 段，放在条目末尾。
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
  **切分点只能落在原文已有标点处**：原文句中没有标点就整条落在承载节拍上——
  不得在句中另造切点、不得增删任何字符（含标点）。
  **旁白条目的起点必须落在原文标点边界**（句首，或前一个原文标点之后）：
  不得从句子中间开始登记（如把「若非如此，前几日往泰州府去时……」从「前几日」
  开始登记）——跨句衔接的前半段要么一并登记、要么整句不选。
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
from step01_script_gen.prompts import render_prompt_template as _render_duration_template
OUTLINE_PROMPT = _render_duration_template(OUTLINE_PROMPT)



_OUTLINE_SYSTEM_PROMPT = "你是短视频编剧，擅长从小说章节提炼叙事大纲，供分镜参考。只输出纯JSON。"

# ---------------------------------------------------------------------------
# 规划层重选（**一轮，带代码算出的数字**）——用户 2026-09-25 决定 ①
# ---------------------------------------------------------------------------
#: 最多重选几轮。用户明确要求"**一次**有数值反馈的规划重选"——这不是可调容量，
#: 不要为了"再多试一次"抬高：重选解决的是"选材超预算"，第二次不会变成另一种问题。
#: **口径（用户 2026-09-25 补充）**："一次"指**同一计划版本一次**，不是每次调用一次：
#: 已尝试过的结论记在 `plan_replan` 并随缓存持久化，计划/规则没变就直接沿用。
_REPLAN_MAX_ROUNDS = 1
#: 首版提示里"一条节拍同时要承载画面与事实时怎么办"这一句的**按轮次取值**。
#: 历史（复核之十二 P2 的实跑根因）：首版写"必须拆成两条"，而**修订轮**的 `shot_beats`
#: 由上一版**冻结**（条数与正文逐项比对）⇒ 照首版那句做每轮都被
#: `core_field_changed: shot_beats` 拒收（实测：章 2 连续 3 轮）。
#: **2026-10-18（A 窄版，用户裁决"允许同镜声画共存"）**：不再要求拆成两条——
#: 事实的画面承载与声音归属分别建模（事实侧 `承载：Bn/画面`、节拍侧 `载体：台词k/旁白k`），
#: 两者可以落在**同一节拍**；两个措辞都按此统一，且**不扩大**任何编辑权限。
_BEAT_EDIT_RULE = {
    "first_version": ("**不必为同一处内容拆成两条**——事实**画面承载**与**声音归属**可以落在"
                      "**同一节拍**：该节拍照常标 `载体：台词k`／`载体：旁白k`，事实那条写"
                      "`｜承载：Bn/画面` 即可；**画面正文仍须表达所绑定的事实**。"
                      "一条节拍只能标**一个**声音载体（k 条台词/旁白至少要落在 k 条不同节拍上）。"),
    "revision": ("**本轮不许增删/拆分/合并节拍**——事实画面承载与声音归属**可以落在同一节拍**，"
                 "只需**保留有效声音引用**；只能把内容挂到**已有**节拍上（改末尾的"
                 "「｜载体：…」登记）；确实只有改节拍才能成立时，本轮无法完成，如实回报。"),
}


#: 这一轮**回什么**（`_replan_route` 的**唯一裁决**的取值，也是证据里 `route` 的取值）：
#: `"ops"` = 只回操作（`REPLAN_OPS_SCHEMA`，代码在旧计划上应用）；`"full_plan"` = 回
#: **一整份新计划**（`OUTLINE_SCHEMA` + 逐字基线 + 核心内容守卫）。schema、反馈块、
#: 应用器**都消费这一个值**（复核之十二 P1）。
ROUTE_OPS = "ops"
ROUTE_FULL_PLAN = "full_plan"
#: **`plan_revision.v2` 完整可编辑视图替换**（R1/R2）：模型回一整份视图、代码派生编号
#: 与承载关系。它取代新请求里的 ops / 受限 full_plan 二选一；那两个协议保留为
#: **冻结响应回放/兼容适配器**（不猜形状、不作隐藏失败重试）。
#:
#: 2026-10-11 起它也是 **hybrid 链路的生产形态**（`_replan_route`：mode 决定 v2 的形态）。
ROUTE_VIEW = "view"
#: **`plan_revision.delta.v1` 按 UID 的增量修订**（B 包）：**legacy 链路的生产形态**。
#: 模型只回**改动**（按稳定 id 增减改条目、显式删除、完整顺序、绑定与出处），冻结主张 /
#: 事件 / `plan_basis` 由本地基线保留、**不要求重抄**。
#:
#: ⚠️ 2026-10-11 隔离对照实验后，**hybrid 不再走它**（改 `ROUTE_VIEW`，见 `_replan_route`）：
#: 同一模型对增量协议 5/5 违约、对完整视图 0 拒收。它仍保留完整实现（legacy 在用，
#: 且历史 delta 归档仍按原协议回放）。协议按这个常量选，**不按响应形状猜**
#: （复核 2026-10-06 第八轮）。
ROUTE_VIEW_DELTA = "view_delta"

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


# ---------------------------------------------------------------------------
# S09（2026-11-28）：拆分后兼容 re-export（父模块名字/调用点保持不变）
# ---------------------------------------------------------------------------
from step01_script_gen.orchestrator import outline_structure as _outline_structure  # noqa: F401
from step01_script_gen.orchestrator import outline_ops as _outline_ops  # noqa: F401
from step01_script_gen.orchestrator import outline_feedback as _outline_feedback  # noqa: F401
from step01_script_gen.orchestrator.outline_structure import (  # noqa: F401, E501
    _outline_max_tokens, _parse_outline_json, _with_protocol_fields, outline_structure_problems, plan_acceptance_problems, _split_acceptance_records, _sentence_alternatives, _budget_limits, _uid_tables_of, _uid_for, _calibrate_time_budget, _validate_outline, _check_outline_dialogue_grounding, _malformed_numbered_item, _reject_malformed_numbered_items, normalize_hybrid_plan, normalize_plan_for_mode, outline_to_anchor, presentation_mode, outline_prompt_for_mode, outline_schema_for_mode,
)
from step01_script_gen.orchestrator.outline_ops import (  # noqa: F401, E501
    apply_replan_ops, _norm_beat_carrier, _coerce_op_index, _norm_narration_entries, _brief_ids, _join_replan_problems, _plan_invariants_intact, _plan_evidence_intact, _strip_fact_carrier, _plan_carrier_declarations_intact, _hook_carrier_decl_text, _brief_value,
)
from step01_script_gen.orchestrator.outline_feedback import (  # noqa: F401, E501
    _problem_identity, _unexpanded_block, _revision_feedback_block, _rejection_feedback_block, _view_feedback_block, _structure_feedback_block, _allowed_fields_block, render_protected_fields_rule, render_protected_fields_inline, render_plan_baseline, render_full_plan_rule, _render_plan_for_revision, _full_plan_response_problem, _view_identity, _view_path, _view_response_shape, _response_summary, _canonical_view_map, _replan_by_ops, _replan_by_slots, _content_slots_applicable, _slots_revision_outcome, _slot_deferral, _voice_targets_from_slots, _split_terminal, _verification_record_for, _detail_vp, complete_candidate, _accept_view_candidate,
)


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


def render_view_protected_rule() -> str:
    """**v2 视图**的「不许改什么」（v2 词汇，与 `view_problems` **同一份口径**）.

    ⚠️ 为什么必须单独一份（实跑 2026-10-06 根因）：v2 修订的 prompt 此前拼的是
    `render_protected_fields_rule()`，而那份说的是 **v5 词汇**——`required_facts`、
    `shot_beats`、`｜承载：`、`｜载体：`。而 `_PLAN_VIEW_INSTRUCTION` 与请求里的视图
    本体说的是 **v2 词汇**——`beats`、`fact_bindings`、`voice_ref`。
    同一份 prompt 里**两套互相冲突的形状规格**，模型于是自己发明了第三种：
    实测三次响应分别回成 `"beats": [0,1,2,...]`（下标占位）、
    `"frozen_facts": [0,...]`，以及 v2 信封 + v5 计划体（`required_facts` /
    `narration_lines` / `screen_cards`）的**混合体**，三轮全部被拒收、额度耗尽。

    规则只有一份来源：`view_problems` 判什么，这里就说什么。
    """
    return chr(10).join([
        "**不许改什么**（v2 视图口径，与代码判据同源）：",
        "- `frozen_facts[].claim`（**正文主张**）与 `frozen_events`（事件集合与**顺序**）"
        "——冻结；只允许用 `evidence_updates` 更正**来源片段**；",
        "- 已有实体的 `uid`（`beats` / `dialogue_units` / `narration_units` / "
        "`screen_cards`）必须**原样保留**——改 uid 等于让所有引用悬空；",
        # R06（2026-10-30）：声源冻结必须**说出来**。守卫按有效声源比对（缺席 ⇒ narrator），
        # 而此前请求与规则块都没提过这件事——模型按一条没被告知的规则被反复拒收，
        # 属于"请求省略守卫会拒收的规则"。这里补一个 `frozen_voice_roles` 集合 + 两条规则
        # （冻结声源 + 允许整条撤下，2026-11-05 与 `must_delete` 反馈同一口径）。
        "- 旁白的**有效声源**随 `uid` 冻结：`frozen_voice_roles` 给出每条旁白当前的有效声源"
        "（**没有该字段 = narrator**）——不许把旁白改成角色发声，也不许给已有旁白补 "
        "`voice_role` 去换人；",
        "- **同一句正文不许换人念**：把基线旁白的正文原样改挂到角色发声的单元、或删掉后"
        "用新 uid 换人重建，都判 `view_voice_role_changed`；整条撤下并改由画面承载"
        "**是允许的**（把事实改挂到画面载体，并保持引用与顺序闭合）。",
        "- `plan_basis` 里的 `climax` / `causal_chain` / `time_budget` / `characters` / "
        "`emotional_arc` / `end_hook` / `key_scenes` / `character_motives` / `omittable`"
        "——**关系之外**的字段，一字不许改；",
        "- 不许自报通过、不许放宽预算、不许输出 `passed` / `executable` / `budget` "
        "之类结论字段；",
        # ⚠️ 判据是「**所有节拍共同**覆盖全部冻结事件 + 事件先后不得回退」
        # （`presentation_contract.view_problems` 的 `view_event_uncovered` /
        # `view_event_order_reversed`）。旧文案写的是"**每条** `event_uids` 必须覆盖全部
        # 冻结事件"——照它改，一份原本零问题的合法视图会立刻触发 `view_event_order_reversed`
        # （每条都覆盖全部事件 ⇒ 每条都要按同一顺序排全部事件 ⇒ 后一条必然相对前一条回退）。
        # 复核 2026-10-06（第十一轮）P2 实测确认。**改文案，不改判据。**
        "- 事件归属若已声明（`event_binding=declared`）：**所有节拍共同**覆盖全部冻结事件"
        "（不要求每条都覆盖），且事件在节拍里出现的先后**不得回退**。",
    ])







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
- 本集正片**合法 {bmin:g}-{bmax:g} 秒**，**规划目标 {tmin:g}-{tmax:g} 秒**（画面动作、反应、
  过场都要占时间）
- 节拍 {beats} 个（画面/台词/旁白各有归属）——**节拍不是时间下界**：生成契约里
  「镜数由时长预算决定，不由节拍数决定」，节拍多不等于装不下、节拍少也不等于装得下；
  **唯一**能算时间的是上面那条发声估时

⇒ **发声内容（对白 + 旁白）**一项就 {dialogue_sec:.2f} 秒，**本集按当前选材装不下**。
请重新取材（复核第 7 轮：对白与旁白**共用同一份发声时长预算**，砍不掉旁白就装不下）：

1. **只从原文逐字摘**真正必须拍的对白——不改写、不润色、不自造、不拿旁白当台词；
2. 按**规划目标**把发声估时压到 **{tmax:g} 秒以内**：
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
    # **`plan_revision.v2` 完整视图替换**（R1/R2）。旧两个协议保留为**冻结响应回放/
    # 兼容适配器**；新请求**一律**走 v2（工作包 1 第 3 条：旧 ops/full 不再是新请求的
    # 选项，只是回放入口）。
    #
    # ⚠️ 这一项**必须存在**（复核 2026-10-05 P1）：`_budget_feedback_block` 按
    # `_INTERFACE_ADVICE[_route]` 取文案，而预算分支**先于**接口分派执行——于是
    # 一个纯预算问题的 hybrid 计划，`_route` 已是 `ROUTE_VIEW`，却先走进预算分支去查
    # 这张表 ⇒ `KeyError: 'view'`。这不是"缺一条文案"，是**预算问题绕过了接口分派**；
    # 两处都要改（见 `revise_plan_once` 的 `_budget_ctx and _route == ROUTE_VIEW`）。
    ROUTE_VIEW_DELTA: {
        "reorder_rule": "改**关系**：每条事实由谁承载（`fact_bindings`）、每条节拍由谁发声"
                        "（`voice_ref`）、卡片挂在哪、事件归属——派生编号由代码算；",
        "beat_edit_rule": "**节拍正文、条数、载体都可以改**；节拍的「原文依据」仍须"
                          "**逐字复制原文**；改集合（增/删）时**必须**给出最终的完整 id 顺序；",
        "carrier_escape": "把该事实的 `carriers` 改指向另一个载体（另一条台词/旁白，"
                          "或新模式下的画面/文字卡），并让被删掉的载体解绑",
        "baseline_note": "请求里带了**当前可编辑视图本体**，你只回**改动**（增量）；"
                         "两个基线摘要原样回带。",
        "cannot_do": "若确实没有要改的地方，**什么都不给**（或所有改动块都空）——"
                     "代码会记 `no_change`（同样不采纳）。",
        "plan_fields_note": "`plan_basis` 里的 `climax` / `causal_chain` / `time_budget` / "
                            "`characters` 等字段由本地基线保留，**不用回抄、也不许改**；",
    },
    ROUTE_VIEW: {
        "reorder_rule": "改**关系**：每条事实由谁承载（`fact_bindings`）、每条节拍由谁发声"
                        "（`voice_ref`）、卡片挂在哪、事件归属——派生编号由代码算；",
        "beat_edit_rule": "**节拍正文、条数、载体都可以改**（v2 不锁 `shot_beats`）；"
                          "节拍的「原文依据」仍须**逐字复制原文**；",
        "carrier_escape": "把该事实的 `carriers` 改指向另一个载体（另一条台词/旁白，"
                          "或新模式下的画面/文字卡），并让被删掉的载体解绑",
        "baseline_note": "请求里带了**当前可编辑视图本体**，你要改的就是这一份；"
                         "两个基线摘要原样回带。",
        "cannot_do": "若确实没有任何合法修法，**原样回带同一份视图**——代码会比摘要，"
                     "一字不差会记成 `no_change`（同样不采纳）。**不要**自报通过、"
                     "不要放宽预算、不要输出 `passed`/`executable` 之类结论字段。",
        "plan_fields_note": "`frozen_facts[].claim`（正文主张）与 `frozen_events`（事件集合"
                            "与顺序）冻结；已有实体的 uid 必须保留；"
                            "`plan_basis` 里的 `climax`/`causal_chain`/`time_budget` 等"
                            "**关系之外**的字段也不许改。",
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
    # 2026-10 裁决 (a)：本函数只实现旧版预算重规划的两条协议（ROUTE_OPS /
    # ROUTE_FULL_PLAN）。生产 v2 路由对 legacy 计划给的是 ROUTE_VIEW_DELTA，而
    # `_INTERFACE_ADVICE` 里 view_delta 的文案是「只回增量、不用回抄」——拿它去拼
    # 全量 OUTLINE_SCHEMA 请求就是**协议错位**（模型按增量回、验收按全量查）。
    # 未显式给合法 route 时按旧路由器裁决，本函数只接受它支持的两条协议。
    _route = route if route in (ROUTE_OPS, ROUTE_FULL_PLAN) else _legacy_route(rep.get("problems"))
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


def _preserve_acceptance_policy(baseline, candidate):
    """Local acceptance policy follows every full-plan revision, never model input."""
    from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD
    from step01_script_gen.plan_coherence import FIELD as COHERENCE_FIELD
    from step01_script_gen.source_revision import FIELD as SOURCE_FIELD
    if isinstance(candidate, dict):
        candidate.pop("plan_verification", None)
        # 失败核验记录只作诊断，不随新计划继续流通（下一次核验会重新给记录）。
        candidate.pop("verification_rejected", None)
        candidate.pop(LEGACY_ACCEPTANCE_FIELD, None)
        if LEGACY_ACCEPTANCE_FIELD in baseline:
            candidate[LEGACY_ACCEPTANCE_FIELD] = baseline[LEGACY_ACCEPTANCE_FIELD]
        candidate.pop(COHERENCE_FIELD, None)
        if COHERENCE_FIELD in baseline:
            candidate[COHERENCE_FIELD] = baseline[COHERENCE_FIELD]
        candidate.pop(SOURCE_FIELD, None)
        if SOURCE_FIELD in baseline:
            candidate[SOURCE_FIELD] = copy.deepcopy(baseline[SOURCE_FIELD])


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
        if (key in _editable
                or key in ("plan_verification", "verification_rejected")
                or key.startswith("prop_state")):
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
    # 2026-10 裁决 (a)：同 `_budget_feedback_block`——本函数只支持 ROUTE_OPS /
    # ROUTE_FULL_PLAN 两条协议；若拿到 v2 的 ROUTE_VIEW_DELTA，`_ops_mode` 会变成
    # False，于是把「单镜不可表示」的 ops 请求当成全量 OUTLINE_SCHEMA 发出。
    _route = route if route in (ROUTE_OPS, ROUTE_FULL_PLAN) else _legacy_route(rep.get("problems"))
    _ops_mode = (_route == ROUTE_OPS)
    # 操作式分支**不带**"输出整份大纲"的格式要求（复核 2026-10-02 P1，与 `_replan_by_ops`
    # 同一根因）；非操作式分支（整份重选台词）仍然需要它。
    _base = base_prompt or (_REVISION_NOVEL_PROMPT if _ops_mode else OUTLINE_PROMPT
                            ).format(novel_text=novel_text[:PROMPT_MAX_CHARS],
                                     beat_edit_rule=_BEAT_EDIT_RULE["revision"])
    from shared.revision_questions import render_revision_questions
    _prompt = (_base + "\n"
               + _budget_feedback_block(rep, plan, novel_text, route=_route)
               + _rejection_feedback_block(rejections)
               + render_revision_questions())
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=_outline_max_tokens(), temperature=0.3,
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
    _preserve_acceptance_policy(plan, new_plan)
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

def default_revision_transport(mode: str) -> str:
    """当前**生产默认**的修订传输协议（进缓存身份）.

    **直接问路由器**（`_replan_route`），不抄一份等价表达式——抄出来的那份会在下一次
    改路由时忘了跟着改，缓存身份就与实际发出的协议脱钩（复核之十二的同一教训：
    「盘点表判得对，不等于请求消费了它」）。
    """
    return _replan_route(None, mode=mode)










_SENTENCE_RE = None












#: 反馈里**逐条展开**（带判据依据）的问题条数上限；超出部分**保留身份**、不许静默省略。
FEEDBACK_DETAIL_LIMIT = 12
#: 未展开项的**身份清单**上限（`code`+字段路径）——再长也要说出还剩几条。
FEEDBACK_IDENTITY_LIMIT = 40










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
#: 另有第四种 `"view"`：**`plan_revision.v2` 完整可编辑视图替换**能表达——关系层的任何
#: 改动（新增/删除/重绑载体、拆分节拍、卡片增删、声明事件归属、只更正出处）。v2 把
#: 承载关系收成**一份**（`fact_bindings`），模型只改视图，编号与登记由**代码派生**，
#: 所以"这个修法表达不了吗"在 v2 下几乎不再是问题——R §2.1 那类"操作按修改前编号、
#: 声明按修改后编号"的错位在 v2 下结构上不可能发生。
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
    # 空白事件**在有事件序号引用时**是结构错（`beat_events` 按序号引用事件）：
    # 修法是补齐那一条正文并重给完整计划——`plan_field` 那一档。
    "structure_events_item_empty": ("plan_field", "main_events"),
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
    # 背景事实表（`background_facts`）的结构错：声明键整表重给（整份计划重写可表达）
    "structure_background_facts_type": ("plan_field", "background_facts"),
    "structure_background_facts_duplicate": ("plan_field", "background_facts"),
    "structure_background_facts_unknown_uid": ("plan_field", "background_facts"),
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
    # 计划级：声明的验收版本无法识别（版本刷由**入口自己盖**，
    # 模型改不了这个字段）⇒ 操作协议表达不了，走整份重写。
    "planning_acceptance_version_invalid": ("content", "planning_acceptance_version"),
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
    # 复核 2026-10-04 第四轮 P1：旁白正文**没有表达**这条事实。修法两轴都在操作
    # 协议能力内——改声明（`fact_carriers` 换承载）或换旁白正文（`narration_lines`
    # 整表），与 `budget_unrepresentable` 同形，故两轴都认（只写一轴会在另一侧漏判）。
    "carrier_fact_narration_unrelated": ("ops", ("required_facts", "narration_lines")),
    # 路线 3（用户 2026-10-04）：**逐字类载体**（台词/旁白）的正文必须表达事实的
    # **全部原文证据**——只表达一半时新增的两个 code。修法都在操作协议能力内：改声明
    # （`fact_carriers`）或换载体正文（台词整条换 `dialogue_ops`；旁白整表 `narration_lines`）。
    # 画面是**转述**载体，不做逐分句覆盖（合法形态就是"用画面演出"），故无对应 code。
    "carrier_fact_line_incomplete": ("ops", ("required_facts", "dialogue_lines")),
    "carrier_fact_narration_incomplete": ("ops", ("required_facts", "narration_lines")),
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
    # ---- 版本 6（`screen_hybrid`）呈现协议：全部走 `plan_revision.v2` 视图替换 ----
    # 理由（R1/R2）：这些 code 核的是**结构化关系**（`presentation.fact_bindings` /
    # `beat_uids` / `beat_events` / `screen_cards`）。旧 ops 协议只认 `｜承载：` 文案与
    # 数字编号，**表达不了**这些关系——按位置造一份数字声明只会把"结构化关系"降级回
    # 旧形状（也就是新模式要根治的那个漏口）。所以它们一律标 `view`：由 v2 请求回整份
    # 视图、代码派生编号与应用器做引用转换。**不标 `plan_field`**——那会让 `_replan_route`
    # 把它们送去 `revise_plan_full`，而完整计划轮的核心守卫锁着 `shot_beats` 条数与正文，
    # 恰恰改不了这些关系（复核之十二的教训：能做什么操作由**接口层**说，不由文案说）。
    "presentation_block_missing": ("view", "presentation"),
    "presentation_schema_unknown": ("view", "presentation.schema"),
    "presentation_mode_invalid": ("view", "presentation.mode"),
    "presentation_narration_forbidden": ("view", "narration_lines"),
    "presentation_unreadable": ("view", "presentation"),
    "presentation_relation_invalid": ("view", "presentation"),
    "presentation_beat_uids_mismatch": ("view", "presentation.beat_uids"),
    "presentation_beat_uids_duplicate": ("view", "presentation.beat_uids"),
    "presentation_carrier_dangling": ("view", "presentation.fact_bindings"),
    "presentation_carrier_kind_invalid": ("view", "presentation.fact_bindings"),
    "presentation_visual_body_missing": ("view", "shot_beats"),
    "presentation_beat_evidence_missing": ("view", "shot_beats"),
    "presentation_beat_evidence_not_in_novel": ("view", "shot_beats"),
    "presentation_card_text_empty": ("view", "presentation.screen_cards"),
    "presentation_card_not_in_novel": ("view", "presentation.screen_cards"),
    "presentation_voice_text_empty": ("view", "presentation.fact_bindings"),
    "presentation_voice_not_in_novel": ("view", "presentation.fact_bindings"),
    "presentation_voice_orphan": ("view", "presentation.fact_bindings"),
    "presentation_reading_budget_exceeded": ("content", "presentation.screen_cards"),
    "presentation_hook_spoken_dangling": ("view", "presentation.hook_bindings"),
    "presentation_hook_spoken_lost": ("view", "hook_spoken"),
    "presentation_card_beat_missing": ("view", "screen_cards"),
    "presentation_card_item_invalid": ("view", "screen_cards"),
    # 卡片**容器**不是数组 / 字段（`text` / `template` / `placement` / `anchor`）类型不对 /
    # 可选出处 `source_spans` 的容器或成员类型不对：修法都是"按 schema 给出正确类型"，
    # 视图层表达得了（`upsert.screen_cards`）。
    "presentation_cards_type": ("view", "screen_cards"),
    "presentation_card_field_type": ("view", "screen_cards"),
    "presentation_card_source_spans_type": ("view", "screen_cards"),
    "presentation_card_source_spans_item": ("view", "screen_cards"),
    # 卡片节拍的**类型**违约（`1.9` / `true` / 字符串）：修法同"节拍号不对"——换一个
    # 合法整数节拍号，视图层表达得了。
    "presentation_card_beat_type": ("view", "screen_cards"),
    # 事件归属表的形状/行/序号违约：修法是把 `beat_events` 写成与节拍等长的数组的数组、
    # 每项用 1..N 的整数序号——视图层（`upsert.beats[].event_uids`）表达得了。
    "presentation_beat_events_shape": ("view", "presentation.beat_events"),
    "presentation_beat_events_item": ("view", "presentation.beat_events"),
    "presentation_beat_events_index": ("view", "presentation.beat_events"),
    # 内容核验（R4）：**不是**视图能改的——它要的是"核验记录"，由独立核验给出。
    # 标 `content` 是为了让它走能力更强的接口并在反馈里显式说明；真正的修法在
    # `shared/content_verification.py`（补证据 / 换载体 / 缩窄主张由生产者决定）。
    "verify_record_missing": ("content", "plan_verification"),
    # 「原文是否支持主张」这一轴（工作包 2 第 2 条）。它与载体轴**分账**：
    # 载体轴的修法是换载体/补正文，本轴的修法是**补齐出处或由故事侧改主张**——
    # 两者都不是视图能表达的，所以都标 `content` 并在反馈里说清是哪一轴。
    "verify_source_support_missing": ("content", "plan_verification"),
    "verify_source_verdict_invalid": ("content", "plan_verification"),
    "fact_basis_invalid": ("content", "required_facts"),
    # 规划期问题（2026-11-10）：一条事实的「原文证据」用省略号跨过原文中间内容，
    # 逐字承载（台词/旁白）只能覆盖一半；而事实正文在修订期冻结，换载体解决不了。
    # 标 content = "必须改内容"，让它走完整计划路由并在反馈里说清：
    # 真正的修法是在**生成大纲时**把复合事实拆成两条（各引一段连续原文）。
    "fact_evidence_noncontiguous": ("content", "required_facts"),
    # 规划期**格式**问题（2026-11-10 R1）：节拍载体段写了 `；载体：` / `|载体：` /
    # `｜载体:` 等解析器不认的分隔符。只改末尾的载体登记段即可（ops），不改正文；
    # 与 `fact_evidence_noncontiguous` 一样是规划层问题，但修法不同：那条要拆事实，
    # 这条只要把分隔符改回全角 `｜载体：`。
    "carrier_format_invalid": ("ops", "shot_beats"),
    "source_preservation_failed": ("content", "required_facts"),
    "verify_source_revision_context": ("content", "plan_verification"),
    "verify_source_preservation_record": ("content", "plan_verification"),
    "verify_source_preservation_identity": ("content", "plan_verification"),
    "verify_source_preservation_evidence": ("content", "plan_verification"),
    # **载体支座**（观众从实际展示正文里能不能得到这条主张）：修法是**换/加载体**，
    # 而载体与绑定正是视图层能改的（`upsert.screen_cards` + `fact_bindings`）——
    # 所以标 `view`，不标 `content`（后者会让这条记录看起来"接口表达不了"）。
    "verify_plan_coherence": ("view", "shot_beats"),
    "view_binding_legacy_visual_mismatch": ("view", "presentation.fact_bindings"),
    "view_binding_dangling": ("view", "presentation.fact_bindings"),
    "view_binding_empty": ("view", "presentation.fact_bindings"),
    "view_voice_unit_unplaced": ("view", "shot_beats"),
    "verify_coherence_record": ("content", "plan_verification"),
    "verify_coherence_identity": ("content", "plan_verification"),
    "verify_coherence_evidence": ("content", "plan_verification"),
    "verify_fact_unsupported": ("view", "presentation.fact_bindings"),
    "verify_fact_unknown": ("view", "presentation.fact_bindings"),
    "verify_support_span_missing": ("view", "presentation.fact_bindings"),
    "verify_support_span_not_exhibited": ("view", "presentation.fact_bindings"),
    # 送进核验的视图自己没过准入：修法是**把关系修好**（视图能表达）。
    "verify_input_rejected": ("view", "presentation"),
    # 这三类是**运行级**状态：修法是"再取一份核验记录"，不是改计划——
    # 与 `verify_record_missing` 同一档（由独立核验给出，不由视图层表达）。
    "verify_call_failed": ("content", "plan_verification"),
    "verify_bad_response": ("content", "plan_verification"),
    "verify_deadline_exhausted": ("content", "plan_verification"),
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
    "lightweight_plan_identity_invalid": "轻量计划身份块不成立：责任在轻量规划生产者；轻量计划不进修订分流（失败即停在计划层）",
    "canonical_beat_count_mismatch": "修订事务内部：新建声音后投影读回的身份配对失败（整轮拒收，不是计划验收问题）",
    "canonical_voice_kind_mismatch": "修订事务内部：新建声音后投影读回的身份配对失败（整轮拒收，不是计划验收问题）",
    "canonical_voice_identity_conflict": "修订事务内部：新建声音后投影读回的身份配对失败（整轮拒收，不是计划验收问题）",
    "response_not_json": "响应协议：正文不是 JSON",
    "outline_invalid": "首版尝试结果码（不是计划验收问题）",
    "deadline_exhausted": "运行级：规划截止耗尽",
    "structure_inconsistent": "生成前门的事务一致性码（不是计划验收问题）",
    "adaptation_gate_error": "适配门运行错误（不是计划验收问题）",
    "split_actions_not_exclusive": "拆分动作互斥校验（候选层结构问题）",
    "split_adoption_view_invalid": "拆分采纳视图非法（候选层结构问题）",
    "split_target_required": "拆分目标缺失（候选层结构问题）",
    "split_view_unreadable": "拆分视图不可读（候选层结构问题）",
    "verification_capacity_exhausted": "核验容量耗尽（运行级，不是计划验收问题）",
    "verification_unavailable": "核验不可用（运行级，不是计划验收问题）",
    "carrier_fact_label_mismatch": "S13 轻量多承载结构校验：责任在计划生产/分配事务，不进旧操作式修订分流",
    "carrier_narration_fact_ref_missing": "S13 多承载反向登记缺失：计划生产/分配事务责任，不进旧操作式修订分流",
}


#: 结构阶段**预期**为缺的核验记录：`structurally_valid` 为假时核验根本不发起。
_EXPECTED_WHEN_UNVERIFIABLE = ("verify_record_missing",)


def _route_problems(plan, problems) -> list:
    """路由只看**要求改什么**：结构不合格时缺核验记录是**预期状态**，从路由输入剔除.

    核验只对结构合格的候选发起（`structurally_valid`）。结构不合格时计划上必然没有
    `plan_verification`，验收因此报 `verify_record_missing`；该 code 登记为 `content`，
    `_fix_capability` 于是判"操作协议表达不了" ⇒ 强制整份重写——而这一轮真正要修的是
    **结构问题**，它恰恰操作协议表达得了。结果是模型被迫回整份计划。

    修法：把"结构阶段预期的缺记录"从**路由输入**剔除；**验收仍严格要求记录**
    （完整报告由验收层保留）。结构一旦合格，本函数原样返回。
    """
    if not problems:
        return problems
    try:
        from step01_script_gen.content_verify import structurally_valid
        if structurally_valid(plan):
            return problems
    except Exception:                                        # noqa: BLE001
        return problems                     # 判不了就不动路由输入（fail-closed）
    _drop = set(_EXPECTED_WHEN_UNVERIFIABLE)
    return [x for x in problems if str((x or {}).get("code") or "") not in _drop]


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
        if _cap[0] not in ("ops", "view"):
            _inexpr.append(_code or _mark)
    return {"expressible": not _inexpr and not _undecl,
            "inexpressible": sorted(set(_inexpr)),
            "undeclared": sorted(set(_undecl)),
            "needs_view": any(str((_p or {}).get("capability") or "") == "view"
                              for _p in (problems or []) if isinstance(_p, dict))}


def _replan_route(problems, *, mode=None) -> str:
    """**唯一的接口裁决**：这一轮回什么.

    当前实现分两层：

    **一层：显式回放开关**（`legacy_request_protocol`）。默认 `None` = 走生产分支。
    设成 `ROUTE_OPS`/`ROUTE_FULL_PLAN` 就固定那一条；设成 `ROUTE_AUTO` 则回到**旧路由器**
    （按能力盘点表推接口）。只有回放入口（`replay_frozen_response`）会设它。

    **二层：生产分支**——**新请求一律 v2**（工作包 1 第 3 条 / 复核 2026-10-05 P1；
    旧 ops / 受限 full_plan 只是**回放/兼容适配器**），v2 的**形态**按 `mode` 取：

    1. `mode == "screen_hybrid"` ⇒ `ROUTE_VIEW`（完整视图替换）。
       2026-10-11 的隔离生产链对照实验（见交付记录「对照实验：传输协议才是卡点」）：
       同一模型走增量 **5/5 被拒**、走完整视图 **0 拒收**——判据是**协议**而不是模型能力。
    2. 其余（legacy）⇒ `ROUTE_VIEW_DELTA`（按 UID 增量）。legacy 没有同条件的对照读数，
       按"没有证据就不动生产默认"保持不变。

    `schema`、反馈块、应用器**都消费这个返回值**：复核之十二 P1 的教训正是"盘点表判得对，
    不等于请求消费了它"——预算分支**优先于**能力裁决，内部又按"有没有不可表示条目"
    **另判一次**接口，于是 `expressible=False` 的计划照样发出**操作协议**请求。
    """
    # `legacy_request_protocol` 是**显式开关**（默认 None = 走生产分支）：只有把它设成
    # `ROUTE_OPS`/`ROUTE_FULL_PLAN`/`ROUTE_AUTO`，或由**回放入口**
    # （`replay_frozen_response`）显式指定时，才走旧协议。生产链路上没有任何地方设它。
    _legacy = legacy_request_protocol
    if _legacy is not None:
        # `ROUTE_AUTO` = 旧路由器（按能力盘点表推接口）；显式给 ops/full 则固定那一条。
        # **只有**回放开关打开时才走这里，生产链路恒为 v2。
        if _legacy != ROUTE_AUTO:
            return _legacy
        return _legacy_route(problems)
    # ---- 生产链路（`mode` 决定用哪条 v2 形态）----
    #
    # **hybrid ⇒ 完整视图替换**（2026-10-11 隔离生产链对照实验，见交付记录的
    # 「对照实验：传输协议才是卡点」一节）：同一条生产链、同一模型，只换协议——
    #   · `ROUTE_VIEW_DELTA`（增量）两轮 + 三轮共 **5/5 被拒**（`illegal_response`：
    #     类型占位 → 自相矛盾 → 合并后悬空），停在 `illegal_response`；
    #   · `ROUTE_VIEW`（完整视图）三轮**全部被接受**（0 拒收，台账 `finish=stop`、
    #     `ctok` 4291/4152/4300），停在 `calls_exhausted`。
    # 判据是"同一模型对增量的嵌套实体对象几乎必然违约、对完整视图可靠合规"⇒ 这是
    # **协议选择**问题，不是模型能力问题。**准入与验收判据一个字没改**（两条路共用
    # 同一份编排与验收链，差别只在请求/响应的形状）。
    #
    # ⚠️ 这**只换 hybrid**：legacy 没有同条件的对照读数，按"没有证据就不动生产默认"
    # 保持增量。两条协议都保留（`ROUTE_VIEW` 不再只是回放入口）。
    if str(mode or "") == MODE_HYBRID:
        return ROUTE_VIEW
    # legacy 链路：仍是**增量协议**（`plan_revision.delta.v1`）。
    return ROUTE_VIEW_DELTA


#: 回放开关的"按旧能力表选路"取值（**旧路由器**，仅回放时可达）。
ROUTE_AUTO = "auto"


def _legacy_route(problems) -> str:
    """**旧路由器**（2026-10-05 之前的口径）：按能力盘点表推接口.

    只在 `with_legacy_request_protocol(ROUTE_AUTO)` 下可达——那些测试断言的是**旧协议
    自身**的行为（守卫 / 分流依据 / 反馈文案），它们不再是生产路由。
    """
    if problems is None:
        return ROUTE_FULL_PLAN
    return ROUTE_OPS if _fix_capability(problems)["expressible"] else ROUTE_FULL_PLAN


#: **旧协议出口开关**（默认 `None` = 新请求一律 v2）。
#:
#: 它的**唯一**用途是**冻结响应回放**：拿一份历史 `replan_ops` / 完整计划的原始响应
#: 重放真实应用器、验证它会被怎样处置（复核 2026-10-05 P1：`KeyError: 'view'` 正是
#: "按内容猜接口"造成的——回放入口必须**自己说**走哪条路，不能由问题清单推）。
#:
#: ⚠️ 它**不是**"隐藏失败重试"的口子：回放入口不接受模型响应，也不写缓存。
legacy_request_protocol = None


def with_legacy_request_protocol(route: str | None):
    """上下文管理器：在**冻结响应回放**期间把接口固定为旧协议."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        global legacy_request_protocol
        _prev = legacy_request_protocol
        legacy_request_protocol = route
        try:
            yield
        finally:
            legacy_request_protocol = _prev
    return _cm()


#: 旧协议**只**用于回放的显式标记（调用点必须写这个，不写就不许走旧路）
ROUTE_REPLAY_OPS = ROUTE_OPS
ROUTE_REPLAY_FULL_PLAN = ROUTE_FULL_PLAN


def replay_frozen_response(route: str, plan, problems, budget, *, response,
                           novel_text: str = ""):
    """**冻结响应回放入口**（工作包 1 第 3 条 / 工作包 4 第 1 条）→ 控制器结果.

    拿一份**历史的** `replan_ops` / 完整计划原始响应，重放真实应用器、验证它会被怎样
    处置。它与生产请求的差别只有一处，而且是**显式**的：

    - 生产：`revise_plan_once` → `_replan_route` ⇒ 新请求一律 v2；
    - 回放：本入口**自己说**走哪条路（`route` 必填），并在 `with_legacy_request_protocol`
      里临时固定——**不由问题清单推接口**（"推"正是 `KeyError: 'view'` 的成因）。

    ⚠️ 它**不是**隐藏失败重试的口子：这里**不发任何模型调用**，只把 `response` 交给
    对应的真实应用器；`budget` 传 `None` 时不消耗额度。
    """
    if route not in (ROUTE_OPS, ROUTE_FULL_PLAN):
        raise ValueError("回放只支持旧协议（ops / full_plan），收到：%r" % (route,))
    with with_legacy_request_protocol(route):
        from step01_script_gen.plan_revision import RevisionBudget
        _bg = budget if budget is not None else RevisionBudget(max_calls=0)
        _probs = list(problems or [])
        if route == ROUTE_OPS:
            _new, _why = _replan_by_ops(plan, _probs, novel_text, response=response)
        else:
            _new, _why = revise_plan_full(plan, _probs, novel_text, response=response)
    if _new is None:
        return {"kind": "illegal", "detail": str(_why or ""), "plan": None,
                "applied": False}
    return {"kind": "applied", "plan": _new, "detail": str(_why or ""),
            "applied": True}


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


# ---------------------------------------------------------------------------
# `plan_revision.v2`：完整可编辑视图替换（R1 请求/响应装配 · R2 应用器接线）
# ---------------------------------------------------------------------------
#: v2 修订的**规则版本**（进请求与缓存身份；规则变了旧缓存不能复用）。
PLAN_VIEW_RULE_VERSION = "plan_revision.v2.1"

#: 内容核验的规则版本（与 `shared.content_verification.DEFAULT_RULE_VERSION` 同一份
#: 取值口径；进缓存键，改了就得重新核验）。
from step01_script_gen.content_verify import VERIFY_RULE_VERSION

#: 增量修订的**规则版本**（进请求与缓存身份；规则变了旧缓存不能复用）。
PLAN_DELTA_RULE_VERSION = "plan_revision.delta.v1"

#: 增量的**输出 schema**：信封 + 三个改动块 + 关系块。**不含** `view`。
#:
#: 该表同时生成请求内的类型规则与 TextGenerationRequest.json_schema。
#: 当前 agent_plan 传输仍为 json_object；模型在 prompt 中看到按本次身份编译的契约，
#: 本地 presentation_contract 独立准入。prompt 中有 schema 不证明远端受约束解码。
REPLAN_DELTA_SCHEMA = {
    "name": "plan_revision_delta_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "schema": {"type": "string", "const": "plan_revision.delta.v1"},
            "rule_version": {"type": "string"},
            "base_plan_hash": {"type": "string",
                               "description": "原样回带请求里的 base_plan_hash"},
            "fact_basis_hash": {"type": "string",
                                "description": "原样回带请求里的 fact_basis_hash"},
            # ⚠️ **内部类型与必填关系也要写出来**（D：不能只约束信封）。四类实体的
            # 字段契约单源在 `shared.presentation_contract.ENTITY_CONTRACT`（本地准入
            # 按它判），这里逐条对应——两处不一致就等于服务端与本地各说一套。
            "upsert": {
                "type": "object",
                "description": "新增或**整体替换**的条目，键限 beats / dialogue_units / "
                               "narration_units / screen_cards",
                "additionalProperties": False,
                "properties": {
                    "beats": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"uid": {"type": "string"},
                                       "event_uids": {"type": "array",
                                                      "items": {"type": "string"}},
                                       "text": {"type": "string"},
                                       "purpose": {"type": "string"},
                                       "evidence": {"type": "string"},
                                       "source": {"type": "object"},
                                       "voice_ref": {"type": ["object", "null"]}},
                        "required": ["uid", "event_uids", "text", "purpose",
                                     "evidence", "source", "voice_ref"]}},
                    "dialogue_units": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"uid": {"type": "string"},
                                       "text": {"type": "string"},
                                       "source": {"type": "object"}},
                        "required": ["uid", "text", "source"]}},
                    "narration_units": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"uid": {"type": "string"},
                                       "text": {"type": "string"},
                                       "source": {"type": "object"}},
                        "required": ["uid", "text", "source"]}},
                    "screen_cards": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"card_id": {"type": "string"},
                                       "beat_uid": {"type": "string"},
                                       "template": {"type": "string"},
                                       "text": {"type": "string"},
                                       "placement": {"type": "string"}},
                        "required": ["card_id", "beat_uid", "template", "text",
                                     "placement"]}},
                }},
            "delete": {"type": "object",
                       "description": "显式删除的稳定 id（卡片用 card_id）",
                       "additionalProperties": False,
                       "properties": {k: {"type": "array", "items": {"type": "string"}}
                                      for k in ("beats", "dialogue_units",
                                                "narration_units", "screen_cards")}},
            "order": {"type": "object",
                      "description": "集合有增删时的**最终完整 id 顺序**",
                      "additionalProperties": False,
                      "properties": {k: {"type": "array", "items": {"type": "string"}}
                                     for k in ("beats", "dialogue_units",
                                               "narration_units", "screen_cards")}},
            "fact_bindings": {"type": "array", "items": {
                "type": "object",
                "properties": {"fact_uid": {"type": "string"},
                               "carriers": {"type": "array", "items": {
                                   "type": "object",
                                   "properties": {"kind": {"type": "string",
                                                           "enum": ["visual", "screen_card",
                                                                    "dialogue", "narration"]},
                                                  "uid": {"type": "string"},
                                                  "beat_uid": {"type": "string"},
                                                  "card_id": {"type": "string"}},
                                   "required": ["kind"]}}},
                "required": ["fact_uid", "carriers"]}},
            "hook_bindings": {"type": "object",
                              "description": "给了就整块替换；`{}` 不是清空、`null` 拒收",
                              "properties": {"spoken_uids": {"type": "array",
                                                             "items": {"type": "string"}},
                                             "carriers": {"type": "array"}},
                              "required": ["spoken_uids", "carriers"]},
            "evidence_updates": {"type": "array", "items": {
                "type": "object",
                "properties": {"fact_uid": {"type": "string"},
                               "evidence": {"type": "string"}},
                "required": ["fact_uid", "evidence"]}},
            "evidence_delete": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema", "rule_version", "base_plan_hash", "fact_basis_hash"],
        "additionalProperties": False,
    },
}

def replan_delta_schema_for_request(request: dict) -> dict:
    """Compile request identities into the existing delta shape contract.

    The local merge remains authoritative for cross-field relations. Wire enums
    prevent unknown formal IDs before generation; new IDs retain the existing
    explicit ``new:`` contract. No baseline data or semantic rule is changed.
    """
    from shared.presentation_contract import DELTA_ID_KEY, fact_binding_policy
    schema = copy.deepcopy(REPLAN_DELTA_SCHEMA)
    props = schema["schema"]["properties"]
    for key in ("rule_version", "base_plan_hash", "fact_basis_hash"):
        props[key]["const"] = request[key]
    view = request["view"]
    for collection, identity_key in DELTA_ID_KEY.items():
        known = [item[identity_key] for item in view.get(collection, [])]
        new_id = {"type": "string", "pattern": "^new:.+$"}
        valid_id = {"anyOf": [{"type": "string", "enum": known}, new_id]} if known else new_id
        entity_props = props["upsert"]["properties"][collection]["items"]["properties"]
        entity_props[identity_key] = copy.deepcopy(valid_id)
        props["order"]["properties"][collection]["items"] = copy.deepcopy(valid_id)
        deleted = props["delete"]["properties"][collection]
        if known:
            deleted["items"] = {"type": "string", "enum": known}
        else:
            deleted["maxItems"] = 0
    facts = [fact["uid"] for fact in view["frozen_facts"]]
    carrier_limit=fact_binding_policy(view)['max_carriers']
    if carrier_limit is not None:
        props["fact_bindings"]["items"]["properties"]["carriers"]["maxItems"] = carrier_limit
    for key in ("fact_bindings", "evidence_updates"):
        if facts:
            props[key]["items"]["properties"]["fact_uid"] = {"type": "string", "enum": facts}
        else:
            props[key]["maxItems"] = 0
    return schema


#: **正文与出处必须逐字来自原文**——两条 v2 修订协议**共用这一份**（在
#: `_replan_by_view` 里拼进请求正文）。
#:
#: 为什么必须写在请求里（2026-10-11 的**离线回放**诊断，见交付记录「完整视图三轮轨迹回放」）：
#: 统一验收链按 `presentation_card_not_in_novel` / `presentation_voice_not_in_novel` /
#: `presentation_beat_evidence_not_in_novel` **逐条核**"正文是不是原文片段"，而修订请求
#: 此前**一个字都没提**这条规则——只有**首版**提示（`HYBRID_PROMPT_BLOCK`）里有。
#: 实测（run4 第 3 轮）：模型为补齐 `verify_fact_unsupported[f06]`（反馈原文是
#: "未展示听香是杨旭花两百贯宝钞买来的美人"）新建了一张卡，正文**直接照抄了反馈里那句
#: 描述**——那句是判据在说"哪里不够"，不是原文；新增卡片于是被判
#: `presentation_card_not_in_novel`。那一轮输入只有 2 条问题，回来却变成 5 条。
#:
#: 这就是"**请求不许省略守卫会拒收的规则**"，与复核之十二那条"反馈不许承诺守卫禁止的
#: 事"是同一枚硬币的两面。规则文字与 `plan_feasibility.source_contains` 同源：
#: 归一化后按**子串**命中；省略号统一形式后仍参与比对。
#: 规则正文的模板：归一化那句**不留手写副本**，由 `{norm_note}` 从判据渲染
#: （见 `plan_feasibility.source_norm_note`）。
_SOURCE_VERBATIM_RULE_TEMPLATE = """
**正文与出处必须逐字来自原文**（本地判据逐条核，违反整份候选不予采纳）：
- 卡片 `screen_cards[].text`、对白 `dialogue_units[].text`、旁白 `narration_units[].text`、
  节拍的「原文依据」`beats[].evidence`——**必须能在原文里一字不差地找到**：
  不许改写、不许概括、不许自造。（{norm_note}）
- ⚠️ **不许把「本轮未通过的项目」里那句说明抄成正文**：那是判据在说"哪里不够"，
  **不是原文**，照抄它一定核不过。要新增或改写正文时，请**回到上面的原文**里找出
  能表达该主张的那一句（或那段连续文字），**原样**抄进 `text`。
- 要给某条事实补载体、而原文里找不到能表达它的句子时：**改挂到一条已有逐字正文的
  载体上**（或另选一段逐字原文），**不要自己造句**。
"""


def source_verbatim_rule() -> str:
    """**逐字规则的请求正文**（hybrid / legacy 两条 v2 修订协议共用这一份）.

    ⚠️ 归一化那句**每次现渲染**（`source_norm_note()`），不在模块加载时冻结——
    判据的标点集一变，发出去的提示随即跟着变，不存在"改了判据、提示还是老口径"的窗口。
    """
    return _SOURCE_VERBATIM_RULE_TEMPLATE.replace("{norm_note}", source_norm_note())


_DELTA_PROTOCOL_HEADER = """【本轮修订协议：`plan_revision.delta.v1` 按 UID 的**增量**修订】
你**不要**回整份视图。只回**改动**——没提到的条目由本地按基线保留，冻结事实主张、
事件与 `plan_basis` **一个字都不用它重抄**。

**顶层键**（白名单，多一个未知键整份拒收）：
`schema`（固定 `"plan_revision.delta.v1"`）、`rule_version`（固定 `"plan_revision.delta.v1"`）、
`base_plan_hash`、`fact_basis_hash`（都原样回带请求里那两个），
再加下面按需出现的块。**不许**出现 `view` / `frozen_facts` / `frozen_events` /
`plan_basis` / `mode` / `event_binding` / `passed` / `executable` / `budget`。

- `upsert`：要**新增或整体替换**的条目，按集合分组（`beats` / `dialogue_units` /
  `narration_units` / `screen_cards`）。每一项必须是**带全部字段的完整对象**
  （整体替换、不做叶子合并）：节拍/对白/旁白用 `uid`，**卡片用 `card_id`**。
  已有条目写**原 id**；新增条目写以 `new:` 开头的**临时 id**（全响应内唯一）。
  ⚠️ 写一个不存在的正式 id **不会被当成新增**，会被拒收。
- `delete`：要删的**已有**稳定 id，按集合分组。
  ⚠️ **`delete` 里的 id 绝不能同时出现在 `upsert` 里**（自相矛盾，整份拒收、不猜优先级）：
  · 想**改**一条已有条目（正文/字段/关系）⇒ **只写 `upsert` + 原 id**，**不要**写进 `delete`；
  · 想**换掉**一条已有条目 ⇒ `upsert` 里给**新的 `new:` 临时 id**，`delete` 里写**旧的正式 id**
    ——两边用的是**不同**的 id。
- `order`：`upsert` 或 `delete` **改动了集合**时**必须**给出该集合**最终完整的 id 顺序**
  （含新增的临时 id）；只改正文、不动集合时可以省略。

**提交前自检（这几条一旦违反，整份响应被拒收、不进入应用；本轮按问题类别只下发相关几条）**：
"""

#: 增量协议自检规则的**唯一来源**：`(规则键, 类别, 正文)`。
#:
#: 类别用于**按问题集裁剪**下发（2026-11-21 S05/D6；规则正文只有这一份，不复制第二份）：
#: - `always`：协议核心（条目形状、承载登记、声音引用、顺序与因果）；
#: - `budget`：只在**本轮有预算类问题**时下发（没有预算问题时不发预算规则）；
#: - `delete`：只在**本轮可能出现删除/增删内容**时下发（删改互斥、整条撤下、清单不删空）。
#:
#: 规则正文**不得写入具体小说、编号或情节模板**（内容无关性）：示例只给匿名形状；
#: 错误码可保留（它们是代码里的稳定标识），不写"第 N 轮实测"这类过程痕迹。
_DELTA_RULES = (
    ("delete_upsert_disjoint", "delete",
     "1. `delete` 里的每个 id 是否**也**出现在 `upsert` 里？—— 必须是空交集。"),
    ("collection_order", "delete",
     "2. 改动了集合（含 `new:` 新增 / 删条目）时，`order` 里是否给了该集合**最终完整**顺序？"),
    ("upsert_complete", "always",
     "3. `upsert` 的每一项是否都是**完整对象**（不是 id 字符串、不是数字）？"),
    ("carrier_registration", "always",
     "4. 事实的 `承载：Bn/…` 与节拍的 `载体：…` 是否**互相登记**？k 条旁白要各有一条标着\n"
     "   `载体：旁白k` 的节拍；一条节拍只能标一个载体。"),
    ("voice_ref_exists", "always",
     "5. 每个 `voice_ref.uid` 是否**确实存在**？——只能引用**当前计划里已有的**声音条目，或本次\n"
     "   `upsert.dialogue_units` / `upsert.narration_units` 里用 `new:` 声明的条目；引用一个\n"
     "   不存在的 id 会被拒收（`view_voice_ref_dangling`）。\n"
     "   **引用本次新增的声音时，`voice_ref.uid` 必须原样带 `new:` 前缀**（写 `new:<新 uid>`，\n"
     "   不能写成不带 `new:` 前缀的同一 uid）——声明、`order`、`voice_ref` 三处前缀必须一致。"),
    ("order_only", "always",
     "6. 只改**顺序**时不要把条目全塞进 `upsert`：给 `order` 的完整顺序即可。"),
    ("voice_unit_placed", "always",
     "7. **每条声音条目都必须仍有节拍承载**：改完之后，`dialogue_units` / `narration_units` 里的\n"
     "   **每一个** uid 都要能在某条节拍的 `voice_ref` 上找到；删节拍、改 `voice_ref` 或替换\n"
     "   声音条目时，别把某条声音弄成\"没有任何节拍念它\"（`view_voice_unit_unplaced`）。\n"
     "   **保留在 `dialogue_units` 或 `narration_units` 里的每个 uid，都必须同时被某条\n"
     "   `beats[].voice_ref`（形如 `{\"kind\": \"dialogue\", \"uid\": \"<声音 uid>\"}`）绑定**；严禁只保留\n"
     "   某个条目却把承载节拍的 `voice_ref` 设为 `null` 或删掉（必触发 `view_voice_unit_unplaced`）。"),
    ("budget", "budget",
     "8. **预算**：`budget` 里的总发声下界上限（{{DURATION_MAX}} s）与单镜上限（{{SHOT_CAP}} s）是**硬约束**。\n"
     "   某条声音超单镜上限、或总下界超预算时，**两条合法修法**：\n"
     "   · **保留原 uid 换短句**：`upsert` 原 id + **完整对象**，`text` 换成原文里更短、\n"
     "     带句末标点、一镜说得完的那一句；\n"
     "   · **整条撤下**：`delete` 它的 uid，把它原先承载的事实改挂到**画面**载体，并给出该集合\n"
     "     的完整 `order`。**撤下一条旁白是允许的**（反馈里的 `must_delete` 就是此意）。\n"
     "   ⚠️ **同一个 uid 的「换短句」与「整条删除」绝对互斥**（`delta_delete_and_upsert_same_uid`）：\n"
     "   选保留 uid 换短句就**只写 `upsert`**、严禁同时写进 `delete`；选整条撤下就**只写 `delete`**、\n"
     "   严禁同时写进 `upsert`。要「换」一条已有条目时用**新 `new:` 临时 id** 写\n"
     "   `upsert`、旧正式 id 写 `delete`。\n"
     "   ⚠️ **不可单镜表示的整条**（超单镜上限、句内又没有句末标点、无法合法切分的长句）：\n"
     "   **必须**撤下它，或换成原文里**带句末标点且一镜说得完**的短句；**不许原样保留**——\n"
     "   留着它计划永远过不了预算门。\n"
     "   **唯一不许**的是\"同一句换人念\"：把基线旁白的正文原样改挂到角色发声的单元上、或删掉后\n"
     "   用 `new:` 换人重建——都判 `view_voice_role_changed` 拒收（`frozen_voice_roles` 是\n"
     "   每条旁白的有效声源，随 uid 冻结）。\n"
     "   **不要靠新增旁白或新增节拍**——新增内容只会把下界推得更高。同理，一条事实用画面\n"
     "   承载时优先复用**已有**节拍，不要为它再造一个带声音的新节拍。"),
    ("dialogue_not_empty", "delete",
     "9. **不许把 `dialogue_units` 删空**：生成前要求计划至少保留一条**逐字台词**\n"
     "   （原文里人物真的说过的话）。删到空会被判「计划未选用任何逐字台词」而直接停稿；\n"
     "   要修「声音没有发声节拍」，就给已有台词补 `voice_ref` / 建承载节拍，不要整条删光。"),
    ("voice_not_speech", "delete",
     "10. **被核验判为「叙述冒充对白」的声音**（`verify_voice_not_speech` / 说话人为空 /\n"
     "    `budget_unrepresentable` 且切不出合法解）：**必须从 `dialogue_units` 里整条\n"
     "    `delete`**，把它原先承载的事实改挂到**画面**或一条**合规短旁白**上；不要靠改说话人名、\n"
     "    不要原样保留——这类条目留着一定过不了生成前的核验门。同样**只写 `delete`**，\n"
     "    不要把同一个 uid 再放进 `upsert`（同一 id 既删又改会被整份拒收）。"),
    ("causal_order", "always",
     "11. **节拍顺序按原文时间与因果先后，不得倒置**；承载「心理 + 行动」复合事实的节拍正文\n"
     "    必须写出**可拍的外部动作**（只写内心独白不算表达，会被判\n"
     "    `verify_fact_unsupported`）。调整顺序用 `order.beats` 的**完整顺序**，不要只改正文。"),
)

_DELTA_PROTOCOL_FOOTER = """- `fact_bindings`：只列**改动过**的事实，每项 `{"fact_uid": …, "carriers": […]}`；
  一条事实只能出现一次；`carriers` 里的每一项是 `{"kind": …, "uid"/"beat_uid"/"card_id": …}`。
  legacy_audio 每条事实最多一个载体，该载体须完整表达主张。**画面事实可以绑定承载
  旁白或对白的既有节拍**，保留其合法 `voice_ref`（声画共镜）；确需新增节拍时才新增。
  不把多项关系塞进旧模式单值承载位。**保留的声音单元必须有合法发声位置**，
  替换或移除声音须同步关系并满足内容保护要求。
- `hook_bindings`：要改就**整块**给，必须同时有 `spoken_uids` 与 `carriers`；
  只写一个 `{}` **不是清空**（清空要显式给完整的空关系），写 `null` 会被拒收。
- `evidence_updates`：只更正**来源片段**，按 `fact_uid` 增改，未提到的修正保留；
  正文主张一个字都不许动。
- `evidence_delete`：显式**撤销**某条事实已有的出处修正（只能删已有修正记录，
  不能删主张、也不能删基线出处）。

**改什么关系**：谁承载哪条事实（`fact_bindings`）、每条节拍由谁发声/演出
（`voice_ref`，`null` = 不发声）、卡片挂在哪条节拍、事件归属、来源片段。
**事件归属**（`event_binding=declared` 时）：**所有节拍共同**覆盖全部冻结事件
（不要求每条 `event_uids` 都覆盖全部事件），且事件在节拍里出现的先后**不得回退**。
**不许改**：事实主张、事件集合与顺序、`plan_basis` 里的字段、已有实体的 id。

**空修改**：什么都不给（或所有块都空）⇒ 记为 `no_change`，同样不采纳。"""


def _delta_rule_categories(problems) -> set:
    """本轮需要下发哪些类别的自检规则（只裁剪**说明**，不裁剪任何本地校验）.

    2026-11-21（S05/D6）：此前 11 条规则对所有轮次全量下发，其中预算/删除类规则
    在"本轮根本没有预算或删除可能"时只增加提示噪声。按问题类别裁剪：

    - 有预算类 code ⇒ 下发 `budget` 规则；
    - 修法能力是 `content`/`plan_field`（要增删内容或改操作协议碰不到的字段），
      或有"声音冒充对白/预算切不出"这类必须整条撤下的 code ⇒ 下发 `delete` 规则；
    - `always` 规则始终下发（协议形状与引用正确性不能省）。

    判不出来时**fail-open**（保留全部规则）：裁剪说明是为了减少噪声，不得因此漏掉
    模型照做会踩的坑。
    """
    if not problems:
        return {"always", "budget", "delete"}
    codes = [str((p or {}).get("code") or "") for p in (problems or [])
             if isinstance(p, dict)]
    cats = {"always"}
    if any(c.startswith("budget") for c in codes):
        cats.add("budget")
    _cap = _fix_capability(problems)
    _delete_hint = bool(_cap.get("inexpressible")) or any(
        c in ("verify_voice_not_speech", "verify_voice_speaker_missing",
              "verify_voice_role_changed") for c in codes)
    if _delete_hint:
        cats.add("delete")
    return cats


def delta_instruction_for(problems) -> str:
    """按问题集裁剪后的增量协议说明（与 `_PLAN_DELTA_INSTRUCTION` **同一份规则来源**）."""
    cats = _delta_rule_categories(problems)
    rules = [text for _rid, cat, text in _DELTA_RULES if cat in cats]
    return chr(10).join([_DELTA_PROTOCOL_HEADER.rstrip(), chr(10).join(rules),
                         "", _DELTA_PROTOCOL_FOOTER])


#: 全量协议说明（规则正文的唯一来源拼装；回放/文档/测试仍可读这一份）：
#: `_replan_by_view` 的请求用 `delta_instruction_for(problems)` 按类别裁剪。
_PLAN_DELTA_INSTRUCTION = chr(10).join([
    _DELTA_PROTOCOL_HEADER.rstrip(),
    chr(10).join(text for _rid, _cat, text in _DELTA_RULES),
    "", _DELTA_PROTOCOL_FOOTER,
])


#: v2 的**输出 schema**：一个信封（三个基线摘要 + 完整视图）。
#:
#: 完整视图保留为显式回放入口。类型规则也进入请求正文；agent_plan 的传输仍为
#: json_object，远端 strict schema 支持未经稳定验证。最终裁决由本地完整事务完成，
#: 不依据请求被接受或示例形状宣称解码约束已生效。
REPLAN_VIEW_SCHEMA = {
    "name": "plan_revision_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "schema": {"type": "string", "const": REVISION_SCHEMA},
            "rule_version": {"type": "string"},
            "base_plan_hash": {"type": "string",
                               "description": "原样回带请求里的 base_plan_hash"},
            "fact_basis_hash": {"type": "string",
                                "description": "原样回带请求里的 fact_basis_hash"},
            "view": {"type": "object",
                     "description": "改完之后**整份**可编辑视图（不是补丁）"},
        },
        "required": ["schema", "rule_version", "base_plan_hash", "fact_basis_hash", "view"],
        "additionalProperties": False,
    },
}

def replan_view_schema_for_request(request: dict) -> dict:
    """Render full-view field types from the shared entity contract.

    This describes the requested canonical output; local admission remains the
    authority for aliases, identities, content and cross-field relations.
    """
    from shared.presentation_contract import ENTITY_CONTRACT

    def field_schema(spec):
        if spec.endswith("|null"):
            return {"anyOf": [field_schema(spec[:-5]), {"type": "null"}]}
        if spec == "list[str]":
            return {"type": "array", "items": {"type": "string"}}
        return {"type": {"str": "string", "obj": "object", "list": "array",
                         "int": "integer"}[spec]}

    schema = copy.deepcopy(REPLAN_VIEW_SCHEMA)
    props = schema["schema"]["properties"]
    for key in ("rule_version", "base_plan_hash", "fact_basis_hash"):
        props[key]["const"] = request[key]
    view = request["view"]
    delta_props = replan_delta_schema_for_request(request)["schema"]["properties"]
    fields = {"schema": {"type": "string", "const": PRESENTATION_SCHEMA},
              "mode": {"type": "string", "const": view["mode"]},
              "event_binding": {"type": "string", "enum": ["declared", "unknown"]}}
    for key in ("frozen_facts", "frozen_events"):
        fields[key] = {"type": "array", "const": copy.deepcopy(view[key])}
    for key, (identity_key, contract) in ENTITY_CONTRACT.items():
        entity = {identity_key: {"type": "string"}}
        entity.update({name: field_schema(spec) for name, spec in contract.items()})
        fields[key] = {"type": "array", "items": {"type": "object",
                       "properties": entity, "required": list(entity)}}
    for key in ("fact_bindings", "hook_bindings", "evidence_updates"):
        fields[key] = copy.deepcopy(delta_props[key])
    fields["hook_bindings"]["properties"]["carriers"]["items"] = copy.deepcopy(
        fields["fact_bindings"]["items"]["properties"]["carriers"]["items"])
    fields["plan_basis"] = {"type": "object", "const": copy.deepcopy(view.get("plan_basis") or {})}
    if view["mode"] == MODE_HYBRID:
        fields["narration_units"]["maxItems"] = 0
        fields["event_binding"]["const"] = "declared"
    else:
        fields["screen_cards"]["maxItems"] = 0
    props["view"] = {"type": "object", "properties": fields,
                     "required": list(fields)}
    return schema


#: v2 的**操作说明**：改什么、不许改什么、按什么判。前者是「可编辑范围」（R2 的表），
#: 后者是「冻结边界」——两份都在这里，**与代码判据同源**（`view_problems` 判的那几条
#: 就是这里写死的那几条；写成两套必然漂移，而漂移的恰好是协议语义）。
_PLAN_VIEW_INSTRUCTION = """
【本轮修订协议：`plan_revision.v2` 完整可编辑视图替换】
你要回的是**改完之后整份**可编辑视图，不是补丁、不是操作列表。

★★ **最重要的一条：数组里每一项都要逐条写全，不许用序号或省略代替** ★★

- **对象数组**（`beats` / `dialogue_units` / `narration_units` / `screen_cards` /
  `fact_bindings`）：每一项都必须是**带全部字段的完整对象**；
- **字符串数组**（`frozen_events`，以及 `plan_basis` 下的 `characters` / `key_scenes` /
  `causal_chain` / `character_motives` / `omittable`）：每一项都必须是**原样的完整字符串**；
- 两类数组里都**不允许出现数字**（`1`、`2`、`3` 这类序号不是内容）。
**没有改动**的条目也要**逐字重抄一份完整的**：视图的体积不是问题，**完整性**才是判据。

列举式地说明四种**不可接受**的写法（**这里刻意不给示例**——上一次把错误示例写进
提示词，小模型把示例本身当成了模板照抄，三轮全被拒收）：
1. 用元素序号顶替内容；
2. 用「同上」「不变」「参见原视图」「...」之类省略；
3. 只列改动过的条目、其余留空；
4. 自造键名（把本协议的键名换成别的形状的字段名）。

代码会逐个字段核：数组里出现非对象、缺字段、键名对不上，整份候选**直接拒收**。

**响应骨架**（键名与层级**逐字照抄**，只把值换成改动后的内容）：

```json
{
  "schema": "plan_revision.v2",
  "rule_version": "plan_revision.v2.1",
  "base_plan_hash": "<原样回带请求里那个>",
  "fact_basis_hash": "<原样回带请求里那个>",
  "view": {
    "schema": "presentation_contract.v1",
    "mode": "legacy_audio 或 screen_hybrid（照抄）",
    "event_binding": "declared 或 unknown（照抄）",
    "frozen_facts": [{"uid": "f01", "claim": "…", "evidence": "…"}],
    "frozen_events": ["事件1", "事件2"],
    "beats": [{"uid": "b01", "event_uids": ["事件1"], "text": "…",
               "purpose": "…", "evidence": "…", "source": {},
               "voice_ref": {"kind": "visual", "uid": "b01"}}],
    "dialogue_units": [{"uid": "d01", "text": "…", "source": {}}],
    "narration_units": [],
    "screen_cards": [],
    "fact_bindings": [{"fact_uid": "f01",
                       "carriers": [{"kind": "visual", "uid": "b01"}]}],
    "hook_bindings": {"spoken_uids": [], "carriers": []},
    "evidence_updates": [],
    "plan_basis": {"climax": "…", "causal_chain": ["…"], "time_budget": "…",
                   "characters": ["…"], "emotional_arc": "…", "end_hook": "…",
                   "key_scenes": ["…"], "character_motives": ["…"],
                   "omittable": []}
  }
}
```

`carriers[].kind` 只能是 `visual` / `screen_card` / `dialogue` / `narration`；
前两种分别配 `beat_uid` / `card_id`，后两种配 `uid`。

**能改什么**（改这些的关系：谁承载哪条事实、每条节拍由谁发声/演出、卡片挂在哪、
事件归属、来源片段）：
- `beats`：条数、正文、信息目的、原文依据、每条节拍的 `voice_ref`（`null` = 不发声，
  或指向一条对白/旁白/卡片单位）；
- `dialogue_units` / `narration_units`：增删改；新条目用**本次响应内唯一**的临时 uid；
- `screen_cards`：增删改（`card_id` 新旧都要唯一）；
- `fact_bindings`：每条冻结事实**恰好一项**，它的 `carriers` 是这张事实的载体列表
  （可以多个 = 联合承载）；
- `hook_bindings`：需要说出口的钩子台词与它们的承载；
- `evidence_updates`：每项形状为 `{"fact_uid": "<冻结事实 uid>", "evidence": "<原文片段>"}`，
  身份键必须是 `fact_uid`；**只**更正原文出处，正文主张一个字都不许动。
- `screen_hybrid` 必须 `narration_units=[]`、`event_binding="declared"`；
  `legacy_audio` 必须 `screen_cards=[]`，旁白条目按当前视图的完整对象形状填写。

**不许改什么**：
- `frozen_facts[].claim`（**正文主张**冻结）、`frozen_events`（事件集合与顺序冻结）；
- **所有节拍共同**覆盖全部冻结事件（不要求每条 `event_uids` 都覆盖全部事件），
  且事件在节拍里出现的先后**不得回退**；
- 已有实体的 `uid` 必须原样保留（改 uid = 让所有引用悬空）；
- 不许自报通过、不许放宽预算、不许输出 `passed` / `executable` 之类结论字段。

**按什么判对错**：
- 表达不完整 → `partial`（缺什么写进 `missing`）；说的与主张相反 → `contradicted`；
  信息不足判不了 → `unknown`（**不要**猜成通过）；
- 新增/删除/改挂之后，**所有** `fact_bindings` / `hook_bindings` / `voice_ref`
  都必须跟着改到闭合状态，代码会逐条核；留下悬空引用整份候选会被拒收。
- **只补出处**（`evidence_updates`）也是**有效改动**，不算"没改"；
  但如果确实没有要改的地方，就**原样回带同一份视图**——代码会比摘要，
  一字不差会被记成 `no_change`（同样不采纳）。
"""


#: v5 问题记录里的**字段路径前缀 → v2 视图字段**（R3：报告层不许让模型看见
#: 它**改不动**的字段名）。键是 v5 记录里的路径前缀，值是视图里的对应位置。
_V5_PATH_TO_VIEW = {
    "required_facts[": "fact_bindings[",
    "shot_beats[": "beats[",
    "narration_lines[": "narration_units[",
    "dialogue_lines[": "dialogue_units[",
    "presentation.screen_cards[": "screen_cards[",
}

# Legacy inline labels are diagnostics, not fields in an editable view.
_V5_VIEW_FIELD_TAILS = {
    "shot_beats[": {".载体": ".voice_ref", ".原文依据": ".evidence",
                    ".本镜信息目的": ".purpose"},
    "required_facts[": {".承载": ".carriers"},
}














#: 内容槽位可 **defer**（下一轮继续处理）的能力档（S04，2026-11-21）：
#: `ops`（delta 操作协议表达得了）与 `view`（视图协议表达得了）。
#: `content`（须增删内容）与 `plan_field`（结构/计划字段）仍挡路——本轮槽位修不了它们，
#: 硬塞进槽位轮只会白花一次调用；未登记能力同样 fail-closed 挡路。
_SLOT_DEFER_ACTIONS = ("ops", "view")














def _replan_by_split(plan, problems, novel_text, budget, rejections=None, *,
                     model=None, system_prompt=None, evidence=None,
                     verification=None, ctx=None, target_fact_uids=None) -> dict:
    """**拆分独占轮**：回答 → compile_split → run_split_transaction → terminal.

    只做一件事：把模型选出的子主张/证据/既有载体编译成事务输入。uid、scope、
    authority、账本由代码生成；M 在事务内一次，R 用控制器一次且不纠正。
    """
    from shared.config import PROMPT_MAX_CHARS
    from shared.content_verification import plan_digest_of
    from step01_script_gen import plan_content_repair as _pcr
    from step01_script_gen import split_adoption, split_judge as _sj
    from step01_script_gen import split_transaction as _st
    _ev = evidence if isinstance(evidence, dict) else {}
    _ctx = ctx if isinstance(ctx, dict) else {}
    _base_view = _ctx.get("view")
    _found = _ctx.get("found") or {}
    _targets = [str(x) for x in (target_fact_uids or []) if str(x)]
    _slots = [s for s in (_found.get("slots") or []) if isinstance(s, dict)
              and str(s.get("kind") or "") == "split_fact"]
    _deferred = sorted(set([str(x) for x in (_ctx.get("deferred") or [])]
                           + [str(x) for x in (_found.get("deferred") or [])]))
    _ev.update({"route": "split_transaction",
                "slots": [str(s.get("slot_id") or "") for s in _slots],
                "deferred": _deferred})
    if (len(_slots) != 1 or len(_targets) != 1
            or not isinstance(_base_view, dict)
            or verification is None):
        return {"kind": "invalid_target", "detail": "拆分槽/目标/R 控制器不唯一或不完整",
                "call_id": "", "evidence": _ev}
    _slot = _slots[0]
    _record = plan.get("plan_verification") if isinstance(plan, dict) else None
    if not isinstance(_record, dict):
        return {"kind": "invalid_target", "detail": "拆分缺少已准入 R0",
                "call_id": "", "evidence": _ev}
    _limits = {"mode": str(_ctx.get("mode") or ""),
               "rule_version": _pcr.REPAIR_RULE_VERSION,
               "max_calls": getattr(budget, "max_calls", None),
               "calls_left": getattr(budget, "calls_left", None),
               "deadline_at": getattr(budget, "deadline_at", None)}
    _req = _pcr.slot_request(
        _base_view, _slots, limits=_limits,
        remaining_calls=getattr(budget, "calls_left", None),
        remaining_sec=budget.remaining_sec() if budget is not None else None)
    _prompt = chr(10).join([
        _REVISION_NOVEL_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS]),
        "# 本轮只做事实拆分（其他问题已 deferred，不要处理）",
        _pcr.slot_prompt(_req, novel_text=novel_text),
        "请现在给出拆分回答；不要返回其他动作。",
    ])
    _timeout = budget.remaining_sec() if budget is not None else None
    try:
        _res = create_service().generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, temperature=0.3,
            json_schema=_pcr.answer_json_schema(_slots), model=model,
            deadline_monotonic=getattr(budget, "deadline_at", None),
            timeout_sec=_timeout))
    except Exception as exc:                                         # noqa: BLE001
        _ev["failure_stage"] = "split_answer_call"
        _ev["error"] = "%s: %s" % (type(exc).__name__, exc)
        return {"kind": "call_failed",
                "detail": "拆分回答调用异常：%s: %s" % (type(exc).__name__, exc),
                "call_id": "", "evidence": _ev,
                "terminal": _split_terminal("split_failed", detail="拆分回答调用异常",
                                            evidence=_ev)}
    _cid = str(getattr(_res, "call_id", "") or "")
    _ev["answer_call_id"] = _cid
    if not _res.success:
        _ev["failure_stage"] = "split_answer_call"
        return {"kind": "call_failed",
                "detail": "拆分回答调用失败：%s" % str(getattr(_res, "error", "") or ""),
                "call_id": _cid, "evidence": _ev,
                "terminal": _split_terminal("split_failed", detail="拆分回答调用失败",
                                            evidence=_ev)}
    _data = _parse_outline_json(_res.text)
    if not isinstance(_data, dict):
        _ev["failure_stage"] = "split_answer_parsing"
        _ev["response"] = {"kind": type(_data).__name__}
        return {"kind": "illegal", "detail": "拆分回答不是 JSON 对象",
                "call_id": _cid, "evidence": _ev,
                "terminal": _split_terminal("split_failed", detail="拆分回答不是 JSON 对象",
                                            evidence=_ev)}
    _normalized, _norm_recs, _norm_problems = _pcr.normalize_slot_answer(_data, _slots)
    _ans_problems = list(_norm_problems) + list(
        _pcr.answer_problems(_normalized, _slots))
    _ev["response"] = {"kind": type(_data).__name__,
                       "normalization": _norm_recs,
                       "answers": _normalized.get("answers") if isinstance(
                           _normalized, dict) else None}
    _ev["answer_problems"] = list(_ans_problems)
    if _ans_problems:
        return {"kind": "illegal", "detail": "拆分回答不合法：%s"
                % "、".join(str(x.get("code") or "") for x in _ans_problems),
                "call_id": _cid, "evidence": _ev,
                "terminal": _split_terminal("split_failed",
                                            detail="拆分回答不合法", evidence=_ev)}
    _ops, _ledger, _scope, _compile_problems = _pcr.compile_split(
        _base_view, plan, _slot, _normalized, record=_record, novel_text=novel_text)
    _ev["split_ops"] = _ops
    _ev["split_ledger"] = _ledger
    _ev["split_scope"] = _scope
    _ev["compile_problems"] = list(_compile_problems or [])
    if _compile_problems:
        return {"kind": "illegal", "detail": "拆分编译失败：%s"
                % "、".join(str(x.get("code") or "") for x in _compile_problems),
                "call_id": _cid, "evidence": _ev,
                "terminal": _split_terminal("split_failed",
                                            detail="拆分编译失败", evidence=_ev)}
    _baseline_problems = list(plan_acceptance_problems(plan, novel_text) or [])
    _ev["baseline_problems"] = _baseline_problems
    _m_log: list = []

    def _m_call(prompt):
        _raw, _m_cid = _sj.call_split_m(
            prompt, model=model,
            deadline_monotonic=getattr(budget, "deadline_at", None),
            timeout_sec=_timeout)
        _m_log.append({"prompt": prompt, "raw": _raw, "call_id": _m_cid})
        return _raw, _m_cid

    def _acceptance(cand_plan, nt):
        _cand_problems = list(plan_acceptance_problems(cand_plan, nt) or [])
        _ev["candidate_problems"] = _cand_problems
        _cand_view, _vp = view_from_plan(cand_plan)
        if _cand_view is None or _vp:
            return list(_vp) or [{"code": "split_adoption_view_invalid",
                                  "reason": "候选投影不可读"}]
        return split_adoption.adoption_problems(
            base_view=_base_view, candidate_view=_cand_view,
            base_plan=plan, candidate_plan=cand_plan, base_record=_record,
            candidate_record=cand_plan.get("plan_verification"),
            parent_uid=str(_slot.get("fact_uid") or ""),
            child_uids=[str(c.get("uid")) for c in (_ops.get("children") or [])],
            baseline_problems=_baseline_problems,
            candidate_problems=_cand_problems)

    _tx = _st.run_split_transaction(
        _base_view, plan, _record, _ops, scope=_scope, ledger=_ledger,
        novel_text=novel_text, m_call=_m_call, m_call_id="",
        verify_candidate=lambda cand: verification.record_for(
            cand, plan_digest_of(plan_from_view(cand)),
            allow_correction=False),
        acceptance=_acceptance)
    _ev["m_calls"] = _m_log
    _ev["transaction"] = {k: _tx.get(k) for k in
                          ("ok", "stage", "state", "m_calls", "problems",
                           "status", "judgment", "error")}
    _ev["split_candidate"] = _tx.get("plan")
    if _tx.get("ok"):
        _final = list(plan_acceptance_problems(_tx.get("plan"), novel_text) or [])
        _ev["final_residuals"] = _final
        _ev["split_state"] = _tx.get("state")
        return {"kind": "applied", "plan": _tx.get("plan"),
                "detail": "split_transaction:committed",
                "call_id": _cid, "evidence": _ev,
                "terminal": _split_terminal(
                    "split_applied_with_deferred", applied=True,
                    detail="拆分已提交；未过截止且完整验收已算", evidence=_ev)}
    _stage = str(_tx.get("stage") or "")
    _problems = list(_tx.get("problems") or [])
    _codes = {str(x.get("code") or "") for x in _problems}
    # **确定失败优先于 pending**（v2.2 §4.1）：不得用"任一 pending 码在场"的布尔或——
    # 那会让同轮的 `split_adoption_degraded` 被 `split_adoption_unconfirmed` 掩盖。
    # 待裁决只有两个来源：M 未判 supported / M 调用失败；以及候选验收问题码**集合
    # 相等**于 {unconfirmed}（空集不落入 pending）。
    _pending = bool(
        (_stage == "m" and _tx.get("status") == "pending_adjudication")
        or (_stage == "acceptance"
            and _codes == {"split_adoption_unconfirmed"}))
    _stop = "split_pending_adjudication" if _pending else "split_failed"
    return {"kind": ("pending_adjudication" if _pending else "illegal"),
            "detail": "split_transaction:%s:%s" % (
                _stage, "、".join(sorted(_codes)) or "无问题码"),
            "call_id": _cid, "evidence": _ev,
            "terminal": _split_terminal(_stop, detail="split_transaction:%s" % _stage,
                                        evidence=_ev)}




def _replan_by_voice_split(plan, problems, novel_text, budget, rejections=None, *,
                           model=None, system_prompt=None, evidence=None,
                           verification=None, ctx=None) -> dict:
    """**新建声音 + 事实拆分**一轮（2026-11-22，M07 ch1「先 C 后 A」的 A）.

    触发：legacy 单载体替换下，复合事实的唯一载体已经逐字表达了其中一层，缺的那层
    来自另一处已准入原话（`content_slots` 的 `voice_split`）。普通 create_voice 会把
    已表达的那层替换掉，所以这里**复用既有事务**而不另造协议：

    1. 一次回答同时给两个既有形状的槽：`create_voice`（缺的那层）+ `split_fact`
       （子事实由修订方提出；子载体 = 当前载体或本回答新建的声音）；
    2. `compile_delta` + `apply_delta_view` 在 V0 的副本上得到 V1（不采纳、不验）；
    3. `compile_split` 按 V0/R0 授权、按 V1 核载体；
    4. `split_transaction.run_create_voice_then_split`：授权仍用 (V0, R0)，M 判完整
       保留一次，R 只核最终候选 V2；采纳契约 `split_adoption` 以 V1 为结构基线。

    任何一步失败都不写回 V1/V2，返回与 `_replan_by_split` 同一形状的结果。
    """
    from shared.config import PROMPT_MAX_CHARS
    from shared.content_verification import plan_digest_of
    from step01_script_gen import plan_content_repair as _pcr
    from step01_script_gen import split_adoption, split_judge as _sj
    from step01_script_gen import split_transaction as _st
    _ev = evidence if isinstance(evidence, dict) else {}
    _ctx = ctx if isinstance(ctx, dict) else {}
    _base_view = _ctx.get("view")
    _mode = str(_ctx.get("mode") or plan_mode(plan) or MODE_LEGACY)
    _entries = [e for e in (_ctx.get("voice_split") or []) if isinstance(e, dict)]
    _deferred = sorted({str(x) for x in (_ctx.get("deferred") or [])})
    _ev.update({"route": "voice_split_transaction", "deferred": _deferred})
    _record = plan.get("plan_verification") if isinstance(plan, dict) else None
    if (not _entries or not isinstance(_base_view, dict) or verification is None
            or not isinstance(_record, dict)):
        return {"kind": "invalid_target", "call_id": "", "evidence": _ev,
                "detail": "新建声音+拆分：缺目标、基线视图、R0 或核验控制器"}
    _entry = _entries[0]
    _slots = [s for s in (_entry.get("slots") or []) if isinstance(s, dict)]
    _vslot = next((s for s in _slots if s.get("kind") == "fact_visual"), None)
    _sslot = next((s for s in _slots if s.get("kind") == "split_fact"), None)
    if _vslot is None or _sslot is None:
        return {"kind": "invalid_target", "call_id": "", "evidence": _ev,
                "detail": "新建声音+拆分：槽位不完整"}
    _parent = str(_entry.get("fact_uid") or "")
    _ev["target_fact_uid"] = _parent
    _ev["slots"] = [str(s.get("slot_id") or "") for s in _slots]
    _ev["voice_source_spans"] = list(_vslot.get("voice_source_spans") or [])
    print("[PLAN] ⇒ 走**新建声音 + 事实拆分**（%s：单载体替换会丢掉已表达的那层；"
          "其余 deferred：%s）" % (_parent, "、".join(_deferred) or "无"))
    _limits = {"mode": _mode, "rule_version": _pcr.REPAIR_RULE_VERSION,
               "max_calls": getattr(budget, "max_calls", None),
               "calls_left": getattr(budget, "calls_left", None),
               "deadline_at": getattr(budget, "deadline_at", None)}
    _req = _pcr.slot_request(
        _base_view, _slots, limits=_limits,
        remaining_calls=getattr(budget, "calls_left", None),
        remaining_sec=budget.remaining_sec() if budget is not None else None)
    _prompt = chr(10).join([
        _REVISION_NOVEL_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS]),
        _view_feedback_block(problems, _base_view),
        _rejection_feedback_block(rejections),
        "# 本轮只修这一条复合事实：新建缺失那层的声音，并把它拆成各由一个载体承载的子事实",
        _pcr.slot_prompt(_req, novel_text=novel_text),
        "请现在对两个槽位各给一条回答；不要返回其他动作。",
    ])
    _timeout = budget.remaining_sec() if budget is not None else None

    def _fail(kind, stage, detail, cid=""):
        _ev["failure_stage"] = stage
        return {"kind": kind, "detail": detail, "call_id": cid, "evidence": _ev}

    try:
        _res = create_service().generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, temperature=0.3,
            json_schema=_pcr.answer_json_schema(_slots), model=model,
            deadline_monotonic=getattr(budget, "deadline_at", None),
            timeout_sec=_timeout))
    except Exception as exc:                                         # noqa: BLE001
        return _fail("call_failed", "voice_split_answer_call",
                     "call_failed: %s: %s" % (type(exc).__name__, exc))
    _cid = str(getattr(_res, "call_id", "") or "")
    _ev["answer_call_id"] = _cid
    if not _res.success:
        return _fail("call_failed", "voice_split_answer_call",
                     "call_failed: %s" % str(getattr(_res, "error", "") or ""), _cid)
    _data = _parse_outline_json(_res.text)
    _ev["raw_response_text"] = str(getattr(_res, "text", "") or "")
    if not isinstance(_data, dict):
        return _fail("illegal", "answer_parsing", "槽位回答不是可解析的 JSON 对象", _cid)
    _norm, _norm_recs, _norm_p = _pcr.normalize_slot_answer(_data, _slots)
    _ans_p = list(_norm_p) + list(_pcr.answer_problems(_norm, _slots, where="槽位回答"))
    _ev["slot_answer_normalized"] = _norm
    _ev["normalization_records"] = _norm_recs
    _ev["answer_problems"] = [str((x or {}).get("code") or "") for x in _ans_p]
    if _ans_p:
        return _fail("illegal", "answer_contract", _detail_vp(_ans_p), _cid)
    _items = [i for i in (_norm.get("answers") or []) if isinstance(i, dict)]
    _vans = {"schema": _norm.get("schema"),
             "answers": [i for i in _items if i.get("slot_id") == _vslot["slot_id"]]}
    _sans = {"schema": _norm.get("schema"),
             "answers": [i for i in _items if i.get("slot_id") == _sslot["slot_id"]]}
    if (len(_vans["answers"]) != 1 or len(_sans["answers"]) != 1
            or str(_vans["answers"][0].get("action") or "") != _pcr.ACT_CREATE_VOICE):
        return _fail("illegal", "answer_contract",
                     "新建声音+拆分必须同时回答 create_voice 与 split_fact 两个槽", _cid)
    # ---- V0 → V1：只应用 create_voice（副本，不采纳、不核验）----
    _ev_by_fact = _pcr.source_spans_by_fact(_record)
    _delta, _cp = _pcr.compile_delta(_base_view, [_vslot], _vans, novel_text=novel_text,
                                     evidence_by_fact=_ev_by_fact)
    if _delta is None:
        _ev["compile_problems"] = [str((x or {}).get("code") or "") for x in _cp]
        return _fail("illegal", "slot_compilation", _detail_vp(_cp), _cid)
    _scope_v = _pcr.build_editable_scope(_base_view, [_vslot], answer=_vans)
    _v1, _v1p, _v1r = apply_delta_view(_base_view, _delta, mode=_mode,
                                       accept=lambda _v: [], editable_scope=_scope_v)
    if _v1 is None or str((_v1r or {}).get("kind") or "") != "applied":
        _ev["create_voice_problems"] = [str((x or {}).get("code") or "") for x in _v1p or []]
        return _fail("illegal", "create_voice_application",
                     _detail_vp(_v1p) or "新建声音的增量被拒收", _cid)
    _tmp = _pcr._create_voice_temp_uid(_vslot)
    _raw_new = str(((_v1r or {}).get("mapping") or {}).get(_tmp) or "")
    if not _raw_new:
        return _fail("illegal", "create_voice_application",
                     "新建声音没有得到最终身份（mapping 缺 %s）" % _tmp, _cid)
    # legacy 的身份是**位置编号**：插入新节拍/新台词后，投影再读回会顺延编号。拆分、
    # 核验与采纳都消费读回后的视图，所以 V1 先规范成读回形态，再按**同一节拍序列的
    # 逐位配对**换算身份（不按文字相似度猜）。
    _v1n, _uid_map, _map_p = _canonical_view_map(_v1)
    if _v1n is None:
        return _fail("illegal", "create_voice_projection",
                     _detail_vp(_map_p) or "新建声音后的视图无法投影读回", _cid)
    _new_uid = str(_uid_map.get(_raw_new) or "")
    _alias = {_tmp: _new_uid}
    for _c in (_sslot.get("allowed_carrier_uids") or []):
        _u = str((_c or {}).get("uid") or "")
        if _u and _u != _tmp and _uid_map.get(_u):
            _alias[_u] = str(_uid_map[_u])
    if not _new_uid or len(_alias) != len(_sslot.get("allowed_carrier_uids") or []):
        return _fail("illegal", "create_voice_projection",
                     "读回后无法逐位确定新建声音或保留载体的身份（%s）" % _alias, _cid)
    _v1 = _v1n
    _ev["new_voice_uid"] = _new_uid
    _ev["carrier_alias"] = dict(_alias)
    # ---- 拆分编译：授权按 V0/R0，载体按 V1 ----
    _ops, _ledger, _scope, _cpp = _pcr.compile_split(
        _base_view, plan, _sslot, _sans, record=_record, novel_text=novel_text,
        carrier_view=_v1, carrier_alias=_alias)
    _ev["split_ops"] = _ops
    _ev["split_scope"] = _scope
    if _cpp:
        _ev["compile_problems"] = [str((x or {}).get("code") or "") for x in _cpp]
        return _fail("illegal", "split_compilation", _detail_vp(_cpp), _cid)
    if not any(str((c.get("carrier") or {}).get("uid") or "") == _new_uid
               for c in (_ops.get("children") or [])):
        return _fail("illegal", "split_compilation",
                     "子事实没有一个绑到本轮新建的声音——缺的那层仍无人承载", _cid)
    _baseline_problems = list(plan_acceptance_problems(plan, novel_text) or [])
    _m_log: list = []

    def _m_call(prompt):
        _raw, _m_cid = _sj.call_split_m(
            prompt, model=model,
            deadline_monotonic=getattr(budget, "deadline_at", None),
            timeout_sec=_timeout)
        _m_log.append({"prompt": prompt, "raw": _raw, "call_id": _m_cid})
        return _raw, _m_cid

    def _acceptance(cand_plan, nt):
        _cand_problems = list(plan_acceptance_problems(cand_plan, nt) or [])
        _ev["candidate_problems"] = _cand_problems
        _cand_view, _vp = view_from_plan(cand_plan)
        if _cand_view is None or _vp:
            return list(_vp) or [{"code": "split_adoption_view_invalid",
                                  "reason": "候选投影不可读"}]
        return split_adoption.adoption_problems(
            base_view=_v1, candidate_view=_cand_view,
            base_plan=plan, candidate_plan=cand_plan, base_record=_record,
            candidate_record=cand_plan.get("plan_verification"),
            parent_uid=_parent,
            child_uids=[str(c.get("uid")) for c in (_ops.get("children") or [])],
            baseline_problems=_baseline_problems,
            candidate_problems=_cand_problems, new_voice_uids=[_new_uid],
            base_uid_map={k: v for k, v in _uid_map.items() if k != _raw_new})

    _tx = _st.run_create_voice_then_split(
        v0=_base_view, r0=_record, create_voice=lambda _v: copy.deepcopy(_v1),
        split_args={"split_ops": _ops, "scope": _scope, "ledger": _ledger,
                    "novel_text": novel_text, "acceptance": _acceptance},
        verify_candidate=lambda cand: verification.record_for(
            cand, plan_digest_of(plan_from_view(cand)), allow_correction=False),
        m_call=_m_call, m_call_id="")
    _ev["m_calls"] = _m_log
    _ev["transaction"] = {k: _tx.get(k) for k in
                          ("ok", "stage", "state", "m_calls", "problems",
                           "status", "judgment", "error", "rollback")}
    if _tx.get("ok"):
        _ev["applied"] = True
        return {"kind": "applied", "plan": _tx.get("plan"), "call_id": _cid,
                "detail": "voice_split_transaction:committed（新建 %s，%s → %s）"
                          % (_new_uid, _parent, "、".join(
                              str(c.get("uid")) for c in _ops.get("children") or [])),
                "evidence": _ev}
    _stage = str(_tx.get("stage") or "")
    _codes = {str((x or {}).get("code") or "") for x in (_tx.get("problems") or [])}
    _pending = bool((_stage == "m" and _tx.get("status") == "pending_adjudication")
                    or (_stage == "acceptance"
                        and _codes == {"split_adoption_unconfirmed"}))
    _detail = "voice_split_transaction:%s:%s" % (_stage, "、".join(sorted(_codes))
                                                 or "无问题码")
    _ev["failure_stage"] = "voice_split_" + (_stage or "unknown")
    if _pending:
        return {"kind": "pending_adjudication", "detail": _detail, "call_id": _cid,
                "evidence": _ev,
                "terminal": _split_terminal("split_pending_adjudication",
                                            detail=_detail, evidence=_ev)}
    return {"kind": "illegal", "detail": _detail, "call_id": _cid, "evidence": _ev}




def _replan_by_view(plan, problems, novel_text, budget, rejections=None, *,
                    model=None, system_prompt=None, evidence=None,
                    mode=None, verification=None, protocol=None, verify_axes=None,
                    allowed_fields=None) -> tuple:
    """**视图协议一轮修订**：装配请求 → 调模型 → 严格解析 → 完整关系事务应用.

    `returns (new_plan|None, why, call_id, evidence)`——与 `_replan_by_ops` /
    `revise_plan_full` **同一份返回形状**，`revise_plan_once` 的三态分派因此不用分叉。

    与旧两路的差别只在**协议**：`protocol` 由调用方按路由显式给出（hybrid ⇒ `ROUTE_VIEW`
    完整视图；legacy ⇒ `ROUTE_VIEW_DELTA` 增量，见 `_replan_route`）；代码派生编号并做
    引用转换。旧两路保留为**冻结响应回放/兼容适配器**，这里不做形状猜测、不作隐藏失败重试。
    """
    from shared.config import PROMPT_MAX_CHARS
    _mode = str(mode or plan_mode(plan) or MODE_LEGACY)
    _base_view, _vp = view_from_plan(plan, mode=_mode)
    # **读取准入只判一次**（复核 2026-10-06 第十二轮 ①）：旧写法在这里另挑一个
    # `view_ref_identity_conflict` 当停止条件——那是在共同准入之外**又维护一张放行
    # 名单**：读盘说"读不成"、这里说"还能走"的时候，两份判断就分叉了（uid 表失配、
    # 载体容器类型违约都是这么漏过去的）。现在只依据读取结果本身停或走，完整诊断
    # 原样进证据。
    if evidence is not None and _vp:
        evidence["baseline_read_problems"] = [str((_p or {}).get("code") or "")
                                              for _p in _vp]
    if _base_view is None:
        return None, "基线读不成可编辑视图：%s" % _detail_vp(_vp), "",             (evidence or {})
    # 关系判据先跑一遍：基线自己就非法时，**先把它报给模型**（问题清单就是它），
    # 但视图仍要发下去——模型得看得见当前形态才改得动。
    # **协议由调用方按路由显式给出**（不按响应形状猜）：hybrid 走 `ROUTE_VIEW` 完整视图
    # 替换，legacy 走 `ROUTE_VIEW_DELTA` 增量。两者共用**同一份**编排与验收链。
    #
    # ⚠️ **先定协议、再装配请求**（复核第十轮 ② 的同一类根因）：`_limits` 是请求对象的一部分，
    # 它的 `rule_version` 此前**写死 v2**，于是增量请求的 `limits` 与 `rule_version` 两处
    # 自相矛盾——离线矩阵/回放按请求对象读规则版本时会读到另一套。
    _proto = str(protocol or ROUTE_VIEW)
    _is_delta = _proto == ROUTE_VIEW_DELTA
    _rule = PLAN_DELTA_RULE_VERSION if _is_delta else PLAN_VIEW_RULE_VERSION
    _limits = {"mode": _mode,
               "rule_version": _rule,
               "max_calls": getattr(budget, "max_calls", None),
               "calls_left": getattr(budget, "calls_left", None),
               "deadline_at": getattr(budget, "deadline_at", None)}
    _req = (delta_request if _is_delta else revision_request)(
        _base_view, problems=problems, rejections=rejections,
        rule_version=_rule,
        remaining_calls=getattr(budget, "calls_left", None),
        remaining_sec=budget.remaining_sec() if budget is not None else None,
        limits=_limits)
    # 请求**必须带着视图本体**：协议说"回一份改完的整份视图"，而模型连当前视图都没见过
    # 时就只能凭空编一份——那不是修订，是重写。`revision_request` 是这份请求的**结构化
    # 形态**（R1 的请求必备字段，也是离线矩阵的断言对象）；模型面这一路用等价的**文本块**
    # 渲染同一份内容，两者不许各自演化（下面每个字段都从 `_req` 取，不重新拼）。
    from step01_script_gen.plan_feasibility import planning_resource_context
    _response_contract = (replan_delta_schema_for_request(_req)
                          if _is_delta else replan_view_schema_for_request(_req))
    _payload = json.dumps({"base_plan_hash": _req["base_plan_hash"],
                           "fact_basis_hash": _req["fact_basis_hash"],
                           "frozen_facts": _req["frozen_facts"],
                           "frozen_events": _req["frozen_events"],
                           # R06：与守卫同一份口径的有效声源集合（缺席 ⇒ narrator）。
                           "frozen_voice_roles": _req["frozen_voice_roles"],
                           "view": _req["view"],
                           "budget": _req["budget"],
                           "limits": _req["limits"],
                           "resources": planning_resource_context(_base_view),
                           "response_contract": _response_contract["schema"]},
                          ensure_ascii=False, indent=1)
    _prompt = chr(10).join([
        _REVISION_NOVEL_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS]),
        # S05（2026-11-21 D6）：按**本轮问题类别**裁剪自检规则（预算/删除类只在相关时下发；
        # 规则正文只有 `_DELTA_RULES` 一份来源，全量版本仍由 `_PLAN_DELTA_INSTRUCTION` 拼装）。
        (_render_duration_template(delta_instruction_for(problems))
         if _is_delta else _PLAN_VIEW_INSTRUCTION),
        # **两条协议都拼**（2026-10-11 离线回放诊断）：统一验收链核"正文是不是原文片段"，
        # 请求就必须说出来——只在首版提示里有，等于让修订轮去踩一个没人告诉它的坑。
        source_verbatim_rule(),
        # ⚠️ **必须用 v2 词汇那一份**：拼 v5 的 `render_protected_fields_rule()`
        # 会让同一份 prompt 里出现两套互相冲突的形状规格（实跑 2026-10-06 根因）
        render_view_protected_rule(),
        # S06（2026-11-21 D4）：载体能力表（呈现协议单源生成），替代未核实的
        # "建议改的载体"指令；只描述能力，不做语义判定。
        carrier_capability_table(),
        "",
        "# 当前可编辑视图（**你要改的就是这一份**；两个基线摘要必须原样回带）",
        _payload,
        "response_contract 是输出类型规则（不是待抄的内容）；已有实体 id 只可取 enum，"
        "新增只可用 new: 前缀。" +
        ("请按该契约生成实际正文、删除表和完整顺序。" if _is_delta else
         "请按本次 mode 的字段契约回带整份 view；不输出 delete/order 增量块。"),
        _view_feedback_block(problems, _base_view),
        _rejection_feedback_block(rejections),
        _allowed_fields_block(allowed_fields) if _is_delta else "",
        "请现在实际修复上面的计划问题，并提交完成的 %s 改动；不要返回示例或空修改。"
        % (DELTA_SCHEMA if _is_delta else REVISION_SCHEMA),
    ])
    if evidence is not None and _is_delta and allowed_fields is not None:
        evidence["allowed_fields_declared"] = sorted(str(x) for x in allowed_fields)
    _sys = system_prompt or _OUTLINE_SYSTEM_PROMPT
    if evidence is not None:
        from shared.revision_request_evidence import revision_request_evidence
        evidence["request"] = revision_request_evidence(
            _sys, _prompt, _payload, _response_contract)
    _timeout = budget.remaining_sec() if budget is not None else None
    # ⚠️ **不传 `max_tokens`**（实测 2026-10-06，探针 4 条独立样本）：v2 响应要回带**整份**
    # 可编辑视图，实测 8.5k~23.4k 字符，而这里原先硬写死 `max_tokens=8000` —— 模型真去
    # 干活的那几次全部 `finish_reason=length` 被**截断**，接回来就是非法 JSON，必须重问。
    # 该值本质是「延迟 vs 输出长度」的权衡参数，按项目约定归 `.env`
    # （`AGENT_PLAN_TEXT_MAX_TOKENS`，`agent_plan_provider` 已明确删过同类的硬编码兜底）；
    # 这里再写一个数字等于把那条约定又推翻一次。缺省交给配置，`None` ⇒ 不覆盖。
    try:
        _res = create_service().generate(TextGenerationRequest(
            system_prompt=_sys, user_prompt=_prompt,
            temperature=0.3,
            json_schema=_response_contract,
            model=model,
            deadline_monotonic=getattr(budget, "deadline_at", None),
            timeout_sec=_timeout))
    except Exception as exc:                                               # noqa: BLE001
        return None, "call_failed: %s: %s" % (type(exc).__name__, exc), "", (evidence or {})
    _cid = str(getattr(_res, "call_id", "") or "")
    if not _res.success:
        return None, "call_failed: %s" % _res.error, _cid, (evidence or {})
    _data = _parse_outline_json(_res.text)
    if not isinstance(_data, dict):
        return None, "响应不是可解析的 JSON 对象", _cid, (evidence or {})
    if evidence is not None:
        evidence["response"] = _view_response_shape(_data, _proto)
    # **期望规则版本由分派者传**（复核 2026-10-06 第十一轮 P2）：完整视图入口此前从不核
    # `rule_version`（缺失或 `unknown.v999` 都 `applied`）。两条入口共用同一条验收链，
    # 版本判据也不能只有增量那条有——这里把**本轮实际按哪套规则问的**传下去。
    _accept = lambda _v: _accept_view_candidate(_v, novel_text, verification, verify_axes=verify_axes)
    if _is_delta:
        from step01_script_gen.revision_route import delta_field_violations
        _field_bad = delta_field_violations(_data, allowed_fields)
        if _field_bad:
            if evidence is not None:
                evidence["route_field_violations"] = _field_bad
            return None, "route_field_violation: %s" % "；".join(
                str(x.get("reason") or x.get("path") or "") for x in _field_bad), \
                _cid, (evidence or {})
        _cand_view, _probs, _res_k = apply_delta_view(_base_view, _data, mode=_mode,
                                                      accept=_accept)
    else:
        _cand_view, _probs, _res_k = apply_editable_view(
            _base_view, _data, mode=_mode, accept=_accept,
            expected_rule_version=_rule)
    _kind = _res_k["kind"]
    if _kind == "no_change":
        return None, "no_change", _cid, evidence
    if _kind == "illegal":
        return None, _detail_vp(_probs) or "候选被拒收（形状/基线/引用/保护边界）", \
            _cid, evidence
    # ⚠️ **必须复用 `complete_candidate` 交回来的那个对象**（工作包 1 第 1 条）。
    # 旧写法在这里 `plan_from_view(_cand_view)` **再投影一次**：那份副本没有核验记录，
    # 于是「控制器验收通过 → 返回候选没记录 → 最终真实验收判 `verify_record_missing`」。
    # 投影两次在"只补出处/同摘要"的边缘还会产生两份**内容一致但对象不同**的候选，
    # 控制器拿一份判、下发另一份。
    _new, _final_problems = complete_candidate(_cand_view, novel_text, verification, verify_axes=verify_axes)
    if _new is None:
        # **投影准入不过**：结构化拒收（`complete_candidate` 交回的是生产者记录）。
        # 这一轮**没有**可下发的候选——不能带着 `None` 往下走，也不能把"应用成功了"
        # 写进记录而实际交不出东西。
        return None, "候选无法投影成计划：%s" % _detail_vp(_final_problems), _cid, (evidence or {})
    # ⚠️ **传输协议与候选视图格式是两件事**（复核 2026-10-06 第十轮 P2）：增量轮发的是
    # `plan_revision.delta.v1`，但合并出来的候选**仍是一份完整视图**（`REVISION_SCHEMA`）。
    # 旧写法把三项全写死成 v2，于是增量轮的记录**冒充 v2**——按记录选协议回放、
    # 按记录统计协议分布、按记录归因都会读错。三项各自取**实际分派**的那一份。
    _new["plan_revision"] = {
        # 本轮**实际发出/实际分派**的传输协议（回放按它选入口）
        "schema": _proto,
        "transport_protocol": _proto,
        # 请求的规则版本（进缓存身份的那一个）
        "rule_version": _rule,
        # 候选**产物的格式**：合并/应用之后交给 `complete_candidate` 的就是这个
        "view_schema": REVISION_SCHEMA,
        "view_rule_version": PLAN_VIEW_RULE_VERSION,
        "mapping": _res_k["mapping"],
        "residuals": list(_final_problems),
        "call_id": _cid}
    if evidence is not None:
        evidence["applied"] = True
        evidence["residuals"] = len(_final_problems)
        evidence["mapping"] = _res_k["mapping"]
    return _new, "view_applied: %d 条残余" % len(_final_problems), _cid, evidence










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






def revise_plan_full(plan, problems, novel_text, *, model=None, base_prompt=None,
                     system_prompt=None, timeout_sec=None, deadline_monotonic=None,
                     rejections=None, evidence: dict | None = None,
                     response=None) -> tuple:
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
    # ---- **冻结响应回放**：给了 `response` 就**不调模型**（旧协议的兼容入口）----
    if response is not None:
        data = response if isinstance(response, dict) else _parse_outline_json(response)
        if not isinstance(data, dict):
            return None, "invalid_result: 回放响应不是可解析的 JSON 对象", ""
        if evidence is not None:
            evidence["response"] = dict(
                _response_summary(data, plan, expect="full_plan"), replayed=True)
            evidence["applied"] = False
        # 与**生产那条路**逐项同序（回放要回答的是"这份历史响应会被怎样处置"，
        # 少一步就不是同一个答案）：响应形状 → 净化事务 → 核心守卫。
        _pre = _full_plan_response_problem(data)
        if _pre:
            return None, _pre, ""
        new_plan = _validate_outline(data, novel_text)
        _preserve_acceptance_policy(plan, new_plan)
        if not new_plan:
            return None, "invalid_result: 净化事务拒收", ""
        _broken = _plan_core_preserved(plan, new_plan)
        if _broken:
            return None, "core_field_changed: %s" % _broken, ""
        if evidence is not None:
            evidence["applied"] = True
        return new_plan, "", ""
    # 基底提示：与首版**同一个** JSON 契约（`# 输出格式` 一段照用，不另写一份字段表），
    # 但"节拍能不能动"这一句按轮次渲染——修订轮的节拍由上一版冻结（复核之十二 P2）
    _base = base_prompt or OUTLINE_PROMPT.format(
        novel_text=novel_text[:PROMPT_MAX_CHARS],
        beat_edit_rule=_BEAT_EDIT_RULE["revision"])
    _rule = render_full_plan_rule()
    # **逐字基线必须进请求**（复核之十一 P1）：规则说"必须逐字保持不变"，就得让模型
    # 看得见要保留的原值——否则两份只有 `causal_chain` 不同的计划发出**完全相同**的
    # 请求，同一份响应必然对其中一份报 `core_field_changed`（实测复现）。
    from shared.revision_questions import render_revision_questions
    _prompt = chr(10).join([_base, _rule,
                            _revision_feedback_block(problems, plan),
                            _rejection_feedback_block(rejections),
                            render_plan_baseline(plan),
                            render_revision_questions()])
    try:
        service = create_service()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _OUTLINE_SYSTEM_PROMPT,
            user_prompt=_prompt, max_tokens=_outline_max_tokens(), temperature=0.3,
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
    _preserve_acceptance_policy(plan, new_plan)
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


def _source_core_authorization(plan, problems) -> dict:
    """来源修正通道的核心摘要授权 ``{field: 依赖原因}``（`revision_route.core_update_problems` 消费）.

    1. 问题记录显式给出 ``core_dependency`` + ``core_fields`` 时按记录授权（原有协议）。
    2. 2026-11-22（M06 / S11 core_field_frozen ×4）：来源修正的提示要求「核心摘要含同一无来源
       推断时在 core_updates 同步纠正」，来源保留核验也明确要求「不能在关联事件或其他摘要留下
       同一无来源推断」；但全仓没有任何生产者写 ``core_dependency``，授权表恒为空，于是每一份
       按提示同步了 main_events / climax 的合法响应都被判 ``core_field_frozen``，连续 5 轮耗尽额度。
       这里把通道本身的依赖写成显式授权：仅当计划带当前有效的来源负面结论（与
       ``source_revision.eligible_targets`` 同一判据）时，对核心摘要字段放行，原因写明授权事实，
       并由 ``core_update_trace`` 留痕；改动是否只去掉无来源部分，仍由独立的来源保留核验判定。
       其他通道不经过这里，核心摘要冻结规则不变。
    """
    from step01_script_gen import source_revision as _sr
    allowed: dict = {}
    for _p in (problems or []):
        if not isinstance(_p, dict):
            continue
        _dep = str(_p.get("core_dependency") or "").strip()
        if not _dep:
            continue
        for _f in (_p.get("core_fields") or []):
            allowed[str(_f)] = _dep
    targets = _sr.eligible_targets(plan)
    if targets:
        reason = ("来源修正：授权事实 %s 的无来源推断可能同样写在核心摘要里，须同步纠正；"
                  "是否只去掉无来源部分由独立来源保留核验判定" % "、".join(targets))
        for _f in _sr.CORE_FIELDS:
            allowed.setdefault(_f, reason)
    return allowed


def revise_plan_once(plan, problems, budget, rejections=None, *, novel_text: str, model=None,
                     base_prompt=None, system_prompt=None, verification=None, verify_axes=None,
                     target_fact_uids=None, allowed_repair_actions=None) -> dict:
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
    from step01_script_gen.revision_route import (ROUTE_CARRIER, ROUTE_SOURCE,
                                                  route_decision)
    record = plan.get("plan_verification")
    _rejected = plan.get("verification_rejected")
    _failed = (_rejected if isinstance(_rejected, dict)
               and str(_rejected.get("status") or "") != "ok" else None)
    if ((isinstance(record, dict) and record.get("status") != "ok")
            or _failed is not None):
        _why = (record if isinstance(record, dict) else _failed) or {}
        return {"kind": "verification_failed", "call_id": "",
                "detail": "核验记录纠正未完成，候选保持不变：%s"
                          % _why.get("error", ""),
                "evidence": {"applied": False, "no_model_call": True}}
    _route_dec = route_decision(problems)
    _timeout = budget.remaining_sec()
    # 工作包 §4 B：控制器只开**一次**表（`budget.deadline_at`），通道里的排队/退避/
    # 每次 POST 都按它重算余量——不再各自拿一个"剩余秒数"重新解释
    _deadline_at = getattr(budget, "deadline_at", None)
    # **拆分独占轮在来源修正/动作收敛之前短路**：只走 split 槽与事务；来源问题只
    # deferred，绝不进入 `source_revision.revise_with_service`（那会抢先发另一种修法）。
    _split_actions = ({str(x) for x in allowed_repair_actions}
                      if allowed_repair_actions is not None else set())
    if "split_fact" in _split_actions and _split_actions != {"split_fact"}:
        return {"kind": "invalid_target", "call_id": "",
                "detail": "split_fact 只能独占 allowed_repair_actions（当前 %s）"
                          % sorted(_split_actions),
                "evidence": {"route": "split_transaction",
                             "blocked": [{"code": "split_actions_not_exclusive"}]}}
    _split_mode = _split_actions == {"split_fact"}
    if _split_mode:
        _slot_ok, _slot_why, _slot_ctx = _content_slots_applicable(
            plan, problems, allow_create=False, target_fact_uids=target_fact_uids,
            verification=verification, novel_text=novel_text,
            allowed_repair_actions=allowed_repair_actions,
            allow_split=True, split_target_uids=target_fact_uids)
        _ev = {"route": "split_transaction",
               "content_slots": {"applicable": bool(_slot_ok),
                                 "why": str(_slot_why),
                                 "state": str((_slot_ctx or {}).get("state") or ""),
                                 "blocked": list((_slot_ctx or {}).get("blocked") or []),
                                 "deferred": list((_slot_ctx or {}).get("deferred") or [])}}
        if not _slot_ok:
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        return _replan_by_split(
            plan, problems, novel_text, budget, rejections, model=model,
            system_prompt=system_prompt, evidence=_ev, verification=verification,
            ctx=_slot_ctx, target_fact_uids=target_fact_uids)
    from step01_script_gen import source_revision as _source_revision
    if _route_dec["route"] == ROUTE_SOURCE:
        if not _source_revision.eligible_targets(plan):
            return {'kind':'verification_failed','call_id':'',
                    'detail':'来源修正缺少当前有效的独立负面核验，先纠正核验记录，候选保持不变',
                    'evidence':{'applied':False,'no_model_call':True,
                                'route':'source_basis_revision',
                                'route_decision':_route_dec}}
        _allowed_core = _source_core_authorization(plan, problems)
        _src_res = _source_revision.revise_with_service(
            plan, problems, novel_text=novel_text, service=create_service(),
            model=model, timeout_sec=_timeout, deadline_monotonic=_deadline_at,
            verification=verification,
            allowed_core_updates=(_allowed_core or None))
        # S04（2026-11-21 D3）：来源分流**必须把本轮不处理的 code 记成 deferred**——
        # 旧实现只发 source 修法，同轮的核验类问题既不处理也不记账，直到终局才重新出现。
        # 这是一条只读证据（不改变修法），供轮次留档与后续归因消费。
        if isinstance(_src_res, dict):
            _src_res.setdefault("evidence", {})
            if isinstance(_src_res["evidence"], dict):
                _src_res["evidence"].setdefault("route_decision", _route_dec)
                _src_res["evidence"].setdefault(
                    "deferred_codes", list(_route_dec.get("deferred_codes") or []))
        return _src_res
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
    _cap = _fix_capability(_route_problems(plan, problems))
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
    _route = _replan_route(_route_problems(plan, problems), mode=plan_mode(plan))
    # 显式动作收敛只能走消费该限制的槽位路径；不得落入 ops/完整计划/预算重选。
    if allowed_repair_actions is not None:
        _route = ROUTE_VIEW_DELTA
    # **归因证据束**（交付证据要求）：`no_change` 只说明"候选没变"，不能据此推断模型
    # 意图。这里把五项一次备齐——① 修法可表达性 ② 请求给了多少信息 ③ 原始响应的形状
    # 与改动 ④ 应用结果 ⑤ 残余问题（由控制器的轮次档一并留档）。①②在分流前后即可得，
    # ③④由具体分支填（出参不改返回元数）。`applied` 是**三态**：`None` = 没有候选
    # （调用失败/还没走到应用）、`False` = 有候选但被拒收、`True` = 候选被采纳——这是
    # "没变"与"没提修改"之间的分界线（`applied=False` **不表示**模型没提出修改）。
    _ev = {"fix_capability": _cap,
           "request": _request_feedback(problems),
           "route": _route,
           "route_decision": _route_dec,
           # 预算读数（数字 + 已验算替代菜单）只有预算路给得出；它是**反馈的 flavor**，
           # 不是接口——单独记一栏，免得把"走了预算反馈"读成"回了操作"
           "budget_context": _budget_ctx,
           "response": {}, "applied": None}
    # **预算只是反馈的 flavor，不是接口**（工作包 1 第 3 条）。旧写法 `if _budget_ctx:`
    # 把预算问题**先于**接口分派短路进 `enforce_budget_by_replanning`——那条路自己
    # 再按 `_INTERFACE_ADVICE[_route]` 取文案，于是 hybrid 的纯预算问题（`_route`
    # 已经是 `ROUTE_VIEW`）先撞上 `KeyError: 'view'`（复核 2026-10-05 P1）。
    # 现在：**接口分派在前**，`_route == ROUTE_VIEW` 永远走 v2；只有 legacy 的纯预算
    # 轮才保留旧的重选路径（它是**预算反馈**的既有语义，不是另一套响应协议）。
    # 2026-10 裁决 (a)：legacy 生产路由恒为 ROUTE_VIEW_DELTA，原条件
    # `_route not in (ROUTE_VIEW, ROUTE_VIEW_DELTA)` 因此**永远为 False**——
    # 纯预算超限的 legacy 计划再也进不来（拿不到数值验算表、不写 `plan_replan`）。
    # 改为按「非完整视图替换」判定，并把本函数支持的旧协议显式传下去。
    if _budget_ctx and allowed_repair_actions is None \
            and _route != ROUTE_VIEW and plan_mode(plan) != MODE_HYBRID:
        _budget_route = _legacy_route(problems)
        # 预算分支沿用**既有的一次重选语义**（"同一计划版本只重选一次"的去重标记、
        # 判据版本、回写 `plan_replan`），但这一轮仍然走控制器给的额度与剩余时间：
        # 额度管的是"花掉的规划机会"，去重标记管的是"这一版别再试"。
        # **接口由 `_route` 定**（`enforce_budget_by_replanning` 不再自选一次）。
        _new, _info = enforce_budget_by_replanning(
            plan, novel_text, model=model, base_prompt=base_prompt,
            system_prompt=system_prompt, timeout_sec=_timeout,
            deadline_monotonic=_deadline_at, ignore_prev=True,
            rejections=rejections, evidence=_ev, route=_budget_route)
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
        # 顺序投影先于核验（见 `complete_candidate` 同一条口径）
        from step01_script_gen.plan_feasibility import apply_voice_order_projection
        _plan, _voice_order = apply_voice_order_projection(_plan)
        if _voice_order.get("changed"):
            _plan = dict(_plan)
            _plan.pop("plan_verification", None)
            _plan.pop("verification_rejected", None)
            _ev["voice_order"] = _voice_order
        if verification is not None:
            from step01_script_gen.content_verify import attach_verification
            _plan = attach_verification(_plan, verification)
        return {"kind": "applied", "plan": _plan,
                "detail": "budget_reselect:%s" % _info.get("result"),
                "call_id": _call_id, "evidence": _ev}
    elif _route in (ROUTE_VIEW, ROUTE_VIEW_DELTA):
        from step01_script_gen import plan_content_repair as _pcr_state
        # **`plan_revision.v2` 完整视图替换**（R1/R2）：模型回一整份视图、代码派生编号与
        # 引用映射。走这条路的原因是**接口级**的：本次计划的关系在
        # `presentation.fact_bindings` 里（或问题里有 `view` 能力的 code），旧 ops 协议
        # 只认 `｜承载：` 文案与数字编号，**表达不了**那些关系。
        # **内容槽位优先**（规划收口计划 §5.6 第 4 条）：本轮问题**全部**能被"内容与
        # 关系选择"表达时，让模型只做选择、由代码装配增量——真实修订里反复出现的**数字
        # 占位**（`upsert.beats[0]` 是 int）正是"要求它重抄实体表 / order / 哈希"造成的。
        # 表达不了（`_content_slots_applicable` 按稳定 UID 判）⇒ 落回既有 delta。
        _slot_ok, _slot_why, _slot_ctx = _content_slots_applicable(
            plan, problems, allow_create=(target_fact_uids is None),
            target_fact_uids=target_fact_uids, verification=verification,
            novel_text=novel_text,
            allowed_repair_actions=allowed_repair_actions)
        _ev["content_slots"] = {"applicable": bool(_slot_ok), "why": str(_slot_why),
                                "state": str((_slot_ctx or {}).get("state") or ""),
                                "blocked": list((_slot_ctx or {}).get("blocked") or []),
                                "deferred": list((_slot_ctx or {}).get("deferred") or [])}
        if not _slot_ok and str((_slot_ctx or {}).get("state") or "") \
                == _pcr_state.STATE_UNKNOWN_TARGET:
            # **零调用**：本次诊断指向的实体不存在/身份写错 ⇒ 上游记录契约失效。
            # 一次 `create_service` / `generate` 都不发（`len(calls)==0` 由测试钉住），
            # 基线计划原样保留，把责任送回**记录生产者**（问题记录随返回带回）。
            print("[PLAN] ⇒ **目标身份失效**：本次诊断指向的实体不在基线视图里 ⇒ "
                  "零调用、保留基线；责任在**问题记录的生产者**（不是模型）")
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        if not _slot_ok and str((_slot_ctx or {}).get("state") or "") \
                == _pcr_state.STATE_ACTION_CONVERGENCE_EMPTY:
            print("[PLAN] ⇒ 限定动作与合法动作交集为空 ⇒ 零调用、保留基线")
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        if not _slot_ok and allowed_repair_actions is not None:
            print("[PLAN] ⇒ 限定动作不可由槽位路径执行 ⇒ 零调用、保留基线；"
                  "不落回完整计划/delta/预算重选")
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        if _slot_ok and str((_slot_ctx or {}).get("state") or "") \
                == _pcr_state.STATE_VOICE_SPLIT:
            return _replan_by_voice_split(
                plan, problems, novel_text, budget, rejections, model=model,
                system_prompt=system_prompt, evidence=_ev, verification=verification,
                ctx=_slot_ctx)
        if _slot_ok:
            _ev["content_slots"]["slots"] = [str(_s["slot_id"])
                                             for _s in _slot_ctx["found"]["slots"]]
            print("[PLAN] ⇒ 走**内容槽位**修订（模型只选内容与关系、代码装配增量；%d 个槽：%s）"
                  % (len(_slot_ctx["found"]["slots"]),
                     "、".join(str(_s["slot_id"]) for _s in _slot_ctx["found"]["slots"])))
            _new, _why, _call_id, _ev = _replan_by_slots(
                plan, problems, novel_text, budget, rejections,
                model=model, system_prompt=system_prompt, evidence=_ev,
                verification=verification, ctx=_slot_ctx, verify_axes=verify_axes,
                target_fact_uids=target_fact_uids,
                allow_create=(target_fact_uids is None))
        else:
            # **打印实际生效的那一条**（复核 2026-10-11：旧文案硬编码，看不出走的是哪条）：
            # 协议名照实打，括号里的**原因**也照实说——hybrid 是**计划模式**定的，
            # legacy 才是"问题里有只能改关系的修法"。
            _protocol_label = ("plan_revision.delta.v1 增量修订" if _route == ROUTE_VIEW_DELTA
                               else "plan_revision.v2 完整视图替换")
            _why_label = ("本计划为 %s ⇒ 完整视图协议" % (plan_mode(plan) or MODE_LEGACY)
                          if _route == ROUTE_VIEW
                          else ("本轮问题里有只能改**关系**的修法：%s"
                                % ("、".join(_inops) or str(plan_mode(plan) or MODE_LEGACY))))
            print("[PLAN] ⇒ 走 %s（%s；内容槽位不适用：%s）"
                  % (_protocol_label, _why_label, _slot_why))
            _new, _why, _call_id, _ev = _replan_by_view(
                plan, problems, novel_text, budget, rejections,
                model=model, system_prompt=system_prompt, evidence=_ev,
                mode=plan_mode(plan) or MODE_LEGACY, verification=verification,
                protocol=_route, verify_axes=verify_axes,
                allowed_fields=(_route_dec["allowed_fields"]
                                if _route_dec["route"] == ROUTE_CARRIER else None))
    elif _route == ROUTE_FULL_PLAN:
        from step01_script_gen import plan_content_repair as _pcr_state
        # 2026-10-23（v5 第一包）：事实目标与声音残余同轮时，**仍走窄槽位**修可表达的那部分；
        # 声音残余留在 `problems`，由最终完整验收继续报告。只有"没有可表达槽位"才退回整份修订。
        _slot_ok, _slot_why, _slot_ctx = _content_slots_applicable(
            plan, problems, allow_create=(target_fact_uids is None),
            target_fact_uids=target_fact_uids, verification=verification,
            novel_text=novel_text,
            allowed_repair_actions=allowed_repair_actions)
        _ev["content_slots"] = {"applicable": bool(_slot_ok), "why": str(_slot_why),
                                "state": str((_slot_ctx or {}).get("state") or ""),
                                "blocked": list((_slot_ctx or {}).get("blocked") or []),
                                "deferred": list((_slot_ctx or {}).get("deferred") or [])}
        if not _slot_ok and str((_slot_ctx or {}).get("state") or "") == _pcr_state.STATE_UNKNOWN_TARGET:
            print("[PLAN] ⇒ **目标身份失效**：本次诊断指向的实体不在基线视图里 ⇒ "
                  "零调用、保留基线；责任在**问题记录的生产者**（不是模型）")
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        if not _slot_ok and str((_slot_ctx or {}).get("state") or "") \
                == _pcr_state.STATE_ACTION_CONVERGENCE_EMPTY:
            print("[PLAN] ⇒ 限定动作与合法动作交集为空 ⇒ 零调用、保留基线")
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        if not _slot_ok and allowed_repair_actions is not None:
            print("[PLAN] ⇒ 限定动作不可由槽位路径执行 ⇒ 零调用、保留基线；"
                  "不落回完整计划/delta/预算重选")
            return {"kind": "invalid_target", "detail": _slot_why, "call_id": "",
                    "evidence": _ev}
        if _slot_ok and str((_slot_ctx or {}).get("state") or "") \
                == _pcr_state.STATE_VOICE_SPLIT:
            return _replan_by_voice_split(
                plan, problems, novel_text, budget, rejections, model=model,
                system_prompt=system_prompt, evidence=_ev, verification=verification,
                ctx=_slot_ctx)
        if _slot_ok:
            _ev["content_slots"]["slots"] = [str(_s["slot_id"])
                                             for _s in _slot_ctx["found"]["slots"]]
            _ev["content_slots"]["deferred"] = list((_slot_ctx or {}).get("deferred") or [])
            print("[PLAN] ⇒ 走**内容槽位**修订（只修可表达的事实/画面；声音残余留给完整验收：%s）"
                  % ("、".join(_ev["content_slots"]["deferred"]) or "无"))
            _new, _why, _call_id, _ev = _replan_by_slots(
                plan, problems, novel_text, budget, rejections,
                model=model, system_prompt=system_prompt, evidence=_ev,
                verification=verification, ctx=_slot_ctx, verify_axes=verify_axes,
                target_fact_uids=target_fact_uids,
                allow_create=(target_fact_uids is None))
        else:
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
    _candidate = normalize_plan_ranges(_new)[0]
    # 顺序投影先于核验（同一口径）：投影后旧记录作废，由下面按新摘要重新核验
    from step01_script_gen.plan_feasibility import apply_voice_order_projection
    _candidate, _voice_order = apply_voice_order_projection(_candidate)
    if _voice_order.get("changed"):
        _candidate = dict(_candidate)
        _candidate.pop("plan_verification", None)
        _candidate.pop("verification_rejected", None)
        _ev["voice_order"] = _voice_order
    if verification is not None:
        from step01_script_gen.content_verify import attach_verification
        _candidate = attach_verification(_candidate, verification)
    return {"kind": "applied", "plan": _candidate,
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


def _early_revision_result(plan, stop, problems, detail="", *, seed=None) -> dict:
    """拆分初始化在控制器之前收口（**零模型调用**）的统一返回形状.

    `seed` 记录 seed 分账（v2.2 §1.2）：未到 seed 步骤 ⇒ `{"attempted": False}`；
    seed 自身失败 ⇒ `{"attempted": True, "ok": False, "code": ..., "error": ...}`。
    顶层 `seed` 与首轮 `history[0].evidence.seed` 同一份（供调用方一次读到）。
    """
    _probs = list(problems or [])
    _seed = dict(seed) if isinstance(seed, dict) else {"attempted": False}
    return {"plan": plan, "stop_reason": str(stop), "problems": _probs,
            "residual_class": residual_class(_probs), "calls": 0, "rounds": 0,
            "seed": _seed,
            "history": [{"round": 0, "kind": str(stop),
                         "detail": str(detail)[:400], "call_id": "",
                         "problems_before": len(_probs), "no_model_call": True,
                         "evidence": {"initialization": "split_preflight",
                                      "seed": dict(_seed)}}],
            "deadline_exhausted": False, "plan_level_conclusion": False,
            "rejections": [], "quota_exhausted": False}


def _split_init_block(plan, novel_text, verification, target_fact_uids, *,
                      seed_meta=None):
    """拆分模式的**付费预检**：在 `attach_verification` 之前跑.

    返回 `None` = 预检通过（R0 已 seed）；返回 dict = 立即收口（0 次发送）。
    缺失/无效 R0 **不补建**，也不先调 `attach_verification`。

    `seed_meta`（可选出参）在 seed **成功**时写入 `{"attempted": True, "ok": True}`；
    seed 前的检查失败不写（seed 未被调用），seed 自身失败由收口结果携带。
    """
    from shared import fact_split as fs
    from shared.content_verification import plan_digest_of
    _targets = [str(x) for x in (target_fact_uids or []) if str(x)]
    if len(_targets) != 1:
        return _early_revision_result(
            plan, STOP_INVALID_TARGET,
            [{"code": "split_target_required",
              "reason": "拆分必须显式给出恰好一个 target_fact_uids（当前 %r）"
                        % (_targets,)}], "split_target_required")
    if verification is None:
        return _early_revision_result(
            plan, STOP_VERIFICATION_FAILED,
            [{"code": "verification_unavailable",
              "reason": "拆分需要核验控制器（R0 复用与 R2），当前未提供"}],
            "verification_unavailable")
    try:
        _capacity = verification.capacity()
    except AttributeError:
        _capacity = 0
    if _capacity <= 0:
        return _early_revision_result(
            plan, STOP_VERIFICATION_FAILED,
            [{"code": "verification_capacity_exhausted",
              "reason": "核验控制器没有可用容量（R2 无法执行）"}],
            "verification_capacity_exhausted")
    _view, _vp = view_from_plan(plan)
    if _view is None or _vp:
        return _early_revision_result(
            plan, STOP_VERIFICATION_FAILED, list(_vp) or [
                {"code": "split_view_unreadable", "reason": "拆分基线视图不可读"}],
            "split_view_unreadable")
    _record = plan.get("plan_verification") if isinstance(plan, dict) else None
    if not isinstance(_record, dict):
        return _early_revision_result(
            plan, STOP_VERIFICATION_FAILED,
            [{"code": "verify_record_missing",
              "reason": "拆分需要当前计划已有的 R0；不付费补建"}],
            "split_r0_missing")
    _pre = fs.split_preflight_problems(
        _view, plan, _record, _targets[0], novel_text=novel_text)
    if _pre:
        return _early_revision_result(plan, STOP_INVALID_TARGET, _pre,
                                      "split_preflight")
    # **seed 分账**（v2.2 §1.2）：上面的检查失败 ⇒ seed 未被调用（attempted=false）；
    # seed 自身返回失败或抛异常 ⇒ 记"已尝试"、零模型调用退出，异常不逃逸。
    try:
        _seeded = bool(verification.seed(plan_digest_of(plan), _record,
                                         plan=plan))
        _seed_err = ""
    except Exception as exc:                                         # noqa: BLE001
        _seeded = False
        _seed_err = "%s: %s" % (type(exc).__name__, exc)
    if not _seeded:
        return _early_revision_result(
            plan, STOP_VERIFICATION_FAILED,
            [{"code": "split_r0_seed_failed",
              "reason": "R0 无法装入核验缓存（身份/原文/规则/义务支座不符）%s"
                        % ("：" + _seed_err if _seed_err else "")}],
            "split_r0_seed_failed",
            seed={"attempted": True, "ok": False,
                  "code": "split_r0_seed_failed", "error": _seed_err})
    if isinstance(seed_meta, dict):
        seed_meta.update({"attempted": True, "ok": True})
    return None


def run_plan_revision(plan, novel_text: str, *, model=None, base_prompt=None,
                      system_prompt=None, max_calls: int = DEFAULT_REVISION_MAX_CALLS,
                      deadline_sec=DEFAULT_REVISION_DEADLINE_SEC,
                      run_deadline_at=None, budget=None, on_round=None,
                      verification=None, target_fact_uids=None,
                      allowed_repair_actions=None) -> dict:
    """把计划交给**修订控制器**跑一遍（所有来源共用同一条验收链、同一份额度与截止时刻）.

    `run_deadline_at` 是**整次 Step 01 的绝对截止时刻**（monotonic，工作包 §4 B）：
    构造 `RevisionBudget` 时只算一次 `min(整次截止, 现在 + deadline_sec)`，之后每轮
    不再重新开表；`None` = 只用 `deadline_sec`（旧行为）。

    `budget`（复核 2026-10-02 之五）：**外面已经开好的那张表**——首版被拒后的重请求
    与这里的修订必须共用它（首版重问花掉的机会，修订就不该再花一次）。给了 `budget`
    就以它为准；`run_deadline_at` 仍一并传入，只在没给 `budget` 时用于开表。
    """
    from step01_script_gen.plan_revision import RevisionBudget, RevisionController
    from step01_script_gen.plan_feasibility import (apply_voice_order_projection,
                                                    normalize_plan_ranges)
    # **统一候选入口**（复核第 32 轮第 2 条）：首版、缓存版、每次修订候选都先规范化，
    # 再验收、再算指纹。"只需规范化即可通过"的计划因此**零调用**通过。
    _plan0 = normalize_plan_ranges(plan)[0]
    # **顺序投影先于 attach_verification**（2026-11-15）：首版/缓存版同样要过这一道；
    # 投影后旧核验记录绑的是旧摘要，摘掉后由下面的 attach 按新摘要重新核验。
    _plan0, _voice_order0 = apply_voice_order_projection(_plan0)
    if _voice_order0.get("changed") and isinstance(_plan0, dict):
        _plan0 = dict(_plan0)
        _plan0.pop("plan_verification", None)
        _plan0.pop("verification_rejected", None)
    _split_actions = ({str(x) for x in allowed_repair_actions}
                      if allowed_repair_actions is not None else set())
    if "split_fact" in _split_actions and _split_actions != {"split_fact"}:
        return _early_revision_result(
            _plan0, STOP_INVALID_TARGET,
            [{"code": "split_actions_not_exclusive",
              "reason": "split_fact 只能独占 allowed_repair_actions（当前 %s）"
                        % sorted(_split_actions)}], "split_actions_not_exclusive")
    _split_mode = _split_actions == {"split_fact"}
    _seed_meta: dict = {}
    if _split_mode:
        # **预检先于 attach_verification**：缓存缺该摘要时，attach 可能付费核验；
        # 拆分模式只复用已有 R0，seed 失败即停，不补建。
        _early = _split_init_block(_plan0, novel_text, verification,
                                   target_fact_uids, seed_meta=_seed_meta)
        if _early is not None:
            return _early
    elif verification is not None and isinstance(_plan0, dict):
        from step01_script_gen.content_verify import attach_verification
        _plan0 = attach_verification(_plan0, verification)
    if budget is None:
        budget = RevisionBudget(max_calls=max_calls, deadline_sec=deadline_sec,
                                run_deadline_at=run_deadline_at)
    # 逐轮证据的落点（复核之七）：生产入口默认写到台账旁边；测试/调用方可以覆盖
    if on_round is None:
        on_round = round_archive_writer()
    def _accept(_p, _nt=""):
        """**唯一的验收链**；新模式把内容核验记录挂上再判（R4：缺记录不得判通过）.

        核验**每份新摘要最多一次**（控制器内部按摘要复用），结构非法的候选根本不进这里
        （它在上游就被拒收），所以"结构非法候选不调核验"是自然成立的。
        """
        _pp = normalize_plan_ranges(_p)[0]
        if verification is not None and isinstance(_pp, dict):
            from step01_script_gen.content_verify import attach_verification
            _pp = attach_verification(_pp, verification)
        return plan_acceptance_problems(_pp, _nt)

    _ctl = RevisionController(
        accept=_accept,
        revise=lambda _p, _pr, _bg, _rj: revise_plan_once(
            _p, _pr, _bg, _rj, novel_text=novel_text, model=model, base_prompt=base_prompt,
            system_prompt=system_prompt, verification=verification,
            target_fact_uids=target_fact_uids,
            allowed_repair_actions=allowed_repair_actions),
        budget=budget, on_round=on_round,
        # **控制器的逐轮记录必须落到日志**（复核 2026-10-02 之五）：`log` 这个口子一直
        # 在，但生产入口从没接——"第 N 轮为什么被拒收/为什么停"只在返回对象里，
        # 真实运行（诊断只留最后一条）时整轮轨迹看不见。
        log=print)
    _out = _ctl.run(_plan0, novel_text)
    if _seed_meta.get("attempted"):
        # **成功 seed 也要有结构化证据**（v2.2 矩阵 #1）：顶层 `seed` 与首轮
        # `evidence.seed` 同一份；失败分支由 `_early_revision_result` 携带。
        _out["seed"] = dict(_seed_meta)
        _hist = _out.get("history") or []
        if _hist and isinstance(_hist[0], dict):
            _ev0 = _hist[0].setdefault("evidence", {})
            if isinstance(_ev0, dict):
                _ev0.setdefault("seed", dict(_seed_meta))
    return _out


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
    # 失败结果必须显式不可执行：缓存/内层入口可能已经把 executable 置 True，
    # 只写 failure 会让消费方按 executable 继续下发（S29 实测）。
    res["executable"] = False
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




def generate_outline_result(novel_text: str, model: str | None = None,
                            cache_path: Path | None = None, *,
                            deadline_monotonic: float | None = None,
                            run_deadline_at: float | None = None,
                            max_revision_calls: int = DEFAULT_REVISION_MAX_CALLS,
                            revision_deadline_sec: float = DEFAULT_REVISION_DEADLINE_SEC,
                            max_verifications: int | None = None,
                            verify=None) -> dict:
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

    from step01_script_gen.plan_coherence import FIELD as COHERENCE_FIELD, VERSION as COHERENCE_VERSION
    from shared.config import PROMPT_MAX_CHARS
    # **按模式给提示与 schema**（主专题 §6.3）：旧模式**逐字不变**（同一个
    # `OUTLINE_PROMPT` / `OUTLINE_SCHEMA`），新模式多一段规则块与 `screen_cards`/
    # `beat_events` 两个字段。模式由 `presentation_mode()` 早读（Step01 早期 CLI 或
    # `STEP01_PRESENTATION_MODE`），进缓存键 ⇒ 换模式必然重新规划。
    _mode = presentation_mode()
    _expect_contract = PLAN_CONTRACT_VERSION_BY_MODE[_mode]
    prompt = outline_prompt_for_mode(
        _mode, novel_text=novel_text[:PROMPT_MAX_CHARS],
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
            "schema": outline_schema_for_mode(_mode),
            "presentation_mode": _mode,
            # 修订规则版本进缓存键（工作包 1 第 6 条）：规则变了，**同一份原文**下
            # 缓存里的修订结果不再可用。此前键里没有它，于是改完规则后仍命中旧缓存，
            # 表现为"代码改了、跑出来还是老样子"。
            "revision_rule_version": (PLAN_VIEW_RULE_VERSION if _mode == MODE_HYBRID
                                      else "legacy_ops.v1"),
            # **传输协议也进身份**（复核 2026-10-06 第十轮 P2 的同一类根因）：同一份原文、
            # 同一套规则，走增量与走完整视图替换**问模型的是两件事**，产出的候选可以不同。
            # 键里没有它 = 换协议后仍命中旧缓存，表现为「协议换了、跑出来还是老样子」；
            # 这与上面 `revision_rule_version` 记的那条是同一个道理，只是维度不同。
            "revision_transport": default_revision_transport(_mode),
            # 新模式还要锁**核验身份**：核验规则或原文身份变了，旧核验记录不可复用。
            "verify_rule_version": VERIFY_RULE_VERSION,
            "fact_metadata_version": FACT_METADATA_VERSION,
            LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION if _mode == MODE_LEGACY else "",
            COHERENCE_FIELD: COHERENCE_VERSION,
            "provider": TextGenCfg.PROVIDER,
            "model": model or TextGenCfg.default_text_model(),
            "temperature": 0.3,
            "max_tokens": _outline_max_tokens(),
            # v6.125.2：候选锚点进缓存键——**提取规则变化**（同一段原文得出不同
            # 候选）必须让缓存失效，否则旧锚点会被当成新规则的结果复用。
            "prop_anchors": _prop_anchors,
            # S34：时长带与声音策略是产出该计划的**配置身份**。60-90/70-80 与
            # 60-120/85-110、10/280 与 12/360 不能互相命中缓存。
            "duration_band": duration_band(),
            "voice_limits": voice_allocation_limits(),
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
            _cached_plan = cached.get("outline") if isinstance(cached, dict) else None
            # ---- **核验记录**：命中时按身份复用，不匹配则拒用（工作包 1 缓存生命周期）----
            #
            # 读数（复核 2026-10-05）：只修版本后 `cache_hit=True` 但**核验仍调了一次**——
            # 缓存里的核验记录没有被认出来。原因：缓存里存了 `plan_verification`，读回时
            # 没有与**本次**的候选/原文/模式/规则四项身份核一遍，于是要么被丢弃（重核）
            # 要么被照单全收（拿旧记录当新的）。这里做**逐项身份核对**：
            # 候选摘要 + 原文摘要 + 规则版本 + 记录状态，四项全对才复用。
            _cached_ver_rec = (cached.get("verification") if isinstance(cached, dict)
                               else None)
            if not isinstance(_cached_ver_rec, dict):
                _cached_ver_rec = (_cached_plan.get("plan_verification")
                                   if isinstance(_cached_plan, dict) else None)
            _ver_reusable = False
            if (isinstance(_cached_ver_rec, dict) and _mode in (MODE_HYBRID, MODE_LEGACY)
                    and isinstance(_cached_plan, dict)):
                from shared.content_verification import (plan_digest_of,
                                                        record_basis_matches, record_shape_problems)
                from step01_script_gen.content_verify import novel_identity
                _want = plan_digest_of(_cached_plan)
                _nv = novel_identity(novel_text)
                # **形状判据与解析 / 控制器装载 / 统一验收同源**（复核 2026-10-07
                # 三轮 ②）：旧写法在这里手写三条 `isinstance(..., list)`，与别处各写
                # 一份——"被缓存拒了、又被别处收下"正是这么来的。
                _ver_shape = record_shape_problems(_cached_ver_rec,
                                                   where="缓存中的核验记录")
                _ver_reusable = (
                    not _ver_shape
                    and str(_cached_ver_rec.get("status") or "") == "ok"
                    and str(_cached_ver_rec.get("plan_digest") or "") == _want
                    and str(_cached_ver_rec.get("novel_digest") or "") == _nv["digest"]
                    and str(_cached_ver_rec.get("rule_version") or "") == VERIFY_RULE_VERSION
                    # **义务支座**（复核 2026-10-09 P1）：待核集合是从**冻结主张**派生的，
                    # 支座变了（主张改过 / 换了派生口径）旧记录就不许复用。这里与统一验收共用
                    # 同一份判据函数（`record_basis_matches`）；读不出视图 ⇒ False（fail-closed）。
                    and record_basis_matches(_cached_ver_rec, _cached_plan)
                    # 缓存装载同样消费拆分状态：pending/invalid 不得因缓存命中被采纳。
                    and not _split_acceptance_records(_cached_plan))
                if not _ver_reusable:
                    res["cache"]["verification_rejected"] = {
                        "status": _cached_ver_rec.get("status"),
                        "plan_digest": str(_cached_ver_rec.get("plan_digest") or "")[:12],
                        "want_digest": _want[:12],
                        "novel_digest": str(_cached_ver_rec.get("novel_digest") or "")[:12],
                        "want_novel": _nv["digest"][:12],
                        "rule_version": _cached_ver_rec.get("rule_version"),
                        "codes": [str(x.get("code")) for x in _ver_shape]}
            elif _cached_ver_rec is not None:
                res["cache"]["verification_rejected"] = {"reason": "非新模式不需要核验记录"}
            # ⚠️ **被拒/不适用 的记录不许随计划继续流通**（复核 2026-10-07 三轮 ②）：
            # 它还挂在计划本体里，统一验收若只看"有没有记录"，就会拿一份**明确被拒**的
            # 记录当作"已核验"，于是 `verification_rejected` 与 `executable=True`
            # 同时出现（实测形态）。摘掉之后，这一份候选的验收会如实报"缺核验记录"。
            # T2：失败核验记录只作诊断，**不随缓存计划流通**（它不是合法记录）。
            if isinstance(_cached_plan, dict):
                _cached_plan = dict(_cached_plan)
                _cached_plan.pop("verification_rejected", None)
            if isinstance(_cached_plan, dict) and not _ver_reusable:
                _cached_plan = dict(_cached_plan)
                _cached_plan.pop("plan_verification", None)
            if _ver_reusable and isinstance(_cached_plan, dict):
                # **复原**到内容上：候选身份一致（同一份计划），记录可以安全沿用
                _cached_plan = dict(_cached_plan)
                _cached_plan["plan_verification"] = _cached_ver_rec
            # 2026-10 裁决 P1-1：**先规范化，再统一验收**。
            # `normalize_plan_ranges` 原本要到下面（`_plan0, _norm = normalize_plan_ranges(outline)`）
            # 才执行，于是这里的 `plan_acceptance_problems` 判的是**未规范化的编号**：
            # 一份「规范化之后才合法」的缓存计划会被误判为不合格（缓存命中不了、白跑一次
            # 规划），反之靠旧编号侥幸通过的会被错收。验收对象必须与实际要下发的对象同形态。
            # 传副本：避免就地规范化的实现顺带改写 `cached["outline"]` 原始对象。
            if isinstance(_cached_plan, dict):
                _cached_plan = normalize_plan_ranges(dict(_cached_plan))[0]
                # **顺序投影**（2026-11-15）：缓存计划同样先按承载次序校正编号。
                # 投影改变了计划 ⇒ 旧核验记录绑的是旧摘要，摘掉后由统一流程按新摘要
                # 重新核验；缓存本身仍可复用为**待核验候选**（`verification_reused=False`）。
                from step01_script_gen.plan_feasibility import apply_voice_order_projection
                _cached_plan, _voice_order_cache = apply_voice_order_projection(_cached_plan)
                if _voice_order_cache.get("changed"):
                    _cached_plan = dict(_cached_plan)
                    _cached_plan.pop("plan_verification", None)
                    _cached_plan.pop("verification_rejected", None)
                    _ver_reusable = False
                    res["cache"]["voice_order_projected"] = _voice_order_cache
            # 工作包 §4 C：可执行缓存**只放通过统一验收的版本**——历史失败元数据
            # （`plan_replan` 里的 result/detail）不能当成本轮验收的替代品。
            _cached_problems = ([]
                                if not isinstance(_cached_plan, dict)
                                else (plan_acceptance_problems(_cached_plan, novel_text) or []))
            _cached_ok = (isinstance(_cached_plan, dict) and not _cached_problems)
            # 投影过的缓存计划**本轮还没有核验记录**（旧记录已作废）：只要剩下的问题
            # 全是 `verify_record_missing`，就把它当**待核验候选**复用，交统一流程重新
            # 核验；其它问题仍按原口径拒收并留档（不因投影放过内容问题）。
            _cached_reusable = bool(_cached_ok) or (
                bool(res["cache"].get("voice_order_projected"))
                and isinstance(_cached_plan, dict)
                and all(str(x.get("code") or "") == "verify_record_missing"
                        for x in _cached_problems))
            # **内容与外层必须一致**（工作包 1 第 5 条）：外层键说 hybrid、内容却是
            # legacy（或反过来）时命中缓存，等于拿一份**没经过新协议**的计划当新模式的
            # 计划下发——而且验收链已经按新规则判过"通过"了，问题更隐蔽。
            _content_mode = plan_mode(_cached_plan) if isinstance(_cached_plan, dict) else ""
            _mode_consistent = (_content_mode == _mode and isinstance(_cached_plan, dict)
                                and _cached_plan.get(COHERENCE_FIELD) == COHERENCE_VERSION)
            if _mode == MODE_LEGACY:
                _mode_consistent = (_mode_consistent and isinstance(_cached_plan, dict)
                                    and _cached_plan.get(LEGACY_ACCEPTANCE_FIELD) == LEGACY_ACCEPTANCE_VERSION)
            if isinstance(_cached_plan, dict) and not _mode_consistent:
                res["cache"]["mode_mismatch"] = {
                    "expected": _mode, "got": _content_mode,
                    "plan_contract_version": _cv_got}
            if (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and _cv_got == _expect_contract and _cached_reusable
                    and _mode_consistent
                    and _validate_outline(cached.get("outline"), novel_text)):
                print(f"[CACHE] 复用章节大纲: {cache_path}")
                # ⚠️ 用**复原过核验记录的那一份**，不是 `cached["outline"]` 原始对象
                # （复核 2026-10-05 P2：复原写在 `_cached_plan` 上，这里却读原始对象，
                # 于是记录没进 `outline`，控制器自然又核了一遍）。
                outline = _cached_plan if isinstance(_cached_plan, dict) \
                    else cached["outline"]
                _from_cache = True
                res["cache"]["hit"] = True
                res["source"] = "cache"
                res["cache"]["verification_reused"] = bool(_ver_reusable)
                # 缓存里若缺该字段（旧缓存但键相同——理论上不会，键已含锚点），
                # 用本次算出的补上，避免调用方拿到 None 而丢掉独立短提示
                if isinstance(outline, dict):
                    outline.setdefault("prop_state_candidates", _prop_anchors)
            elif (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and _cv_got != _expect_contract):
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
                max_tokens=_outline_max_tokens(),
                temperature=0.3,
                json_schema=outline_schema_for_mode(_mode),
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
                if _mode == MODE_LEGACY:
                    data[LEGACY_ACCEPTANCE_FIELD] = LEGACY_ACCEPTANCE_VERSION
                _bad = outline_structure_problems(data)
                if not _bad:
                    outline = _validate_outline(data, novel_text)
                    # **按模式规范化**（新模式：内容 + 卡片 → 版本 6 的结构化关系）。
                    # 规范化是**生产者侧**的动作，不改变正文/证据/条数/事件；它失败与结构
                    # 不合格同级——不进验收链。
                    if _mode == MODE_HYBRID and isinstance(outline, dict):
                        outline, _bad = normalize_hybrid_plan(outline)
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
    if _mode == MODE_LEGACY and isinstance(outline, dict):
        outline = dict(outline, **{LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION})
    from step01_script_gen.plan_coherence import mark_plan
    outline = mark_plan(outline)
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
    _verify_ctl = None
    if _mode in (MODE_HYBRID, MODE_LEGACY):
        # **内容核验控制器**（R4 / 工作包 2）：新模式的完整支持关系由独立核验给出，
        # 确定性判据只核形状/引用/出处/时长。按候选摘要复用有效结果；新候选核验与
        # 记录协议纠正共用最多 4 次发送，与修订调用分账、共用同一张截止表。
        from shared.content_verification import VerificationController
        from step01_script_gen.content_verify import (novel_identity,
                                                      verify_view_with_service)
        _novel_id = novel_identity(novel_text)

        def _verify(view, digest):
            # 2026-10：`verify` 是**测试/调用方覆盖**（默认 None ⇒ 走生产核验）。
            # 与上面的 `base_prompt`/`system_prompt` 同类：只提供覆盖缝，默认值即正确契约。
            # 存在的理由：核验协议（记录形状、义务支座）演化后，老用例若只能靠
            # `create_service` 的顺序回放来"回答"核验，就会把规划用的响应预算耗光
            # （实测 31 条用例因此误红）；有了这个缝，用例可注入自己的固定核验。
            if verify is not None:
                return verify(view, digest)
            # 核验请求**带原文上下文与原文身份**（工作包 2 第 1 条）：没有原文就无从
            # 判断"原文是否支持主张"，只能靠引文——而引文正是可能写错的那一份。
            return verify_view_with_service(
                view, digest, model=model, deadline_monotonic=_plan_deadline,
                novel_text=novel_text, novel_digest=_novel_id["digest"],
                novel_version=_novel_id["version"],source_references=True)

        def _protocol_problems(record, view, digest):
            from shared.content_verification import record_protocol_problems
            return record_protocol_problems(record, view, plan_digest=digest,
                                            novel_text=novel_text,
                                            novel_digest=_novel_id["digest"])

        def _repair_verification(view, digest, failure, previous):
            # 同上：覆盖缝对"核验记录纠正"这条路同样生效（固定核验不需要按纠正重发）。
            if verify is not None:
                return verify(view, digest)
            return verify_view_with_service(
                view, digest, model=model, deadline_monotonic=_plan_deadline,
                novel_text=novel_text, novel_digest=_novel_id["digest"],
                novel_version=_novel_id["version"],
                source_references=True,
                correction={"problems": failure.get("problems", []),
                            "error": failure.get("error", ""), "previous_response": previous})

        # 核验次数按**显式参数**注入（默认 None = 沿用 MAX_VERIFICATIONS_PER_RUN）：
        # 只给实验/调用方一个可复核的入口，不做环境变量隐式覆盖，避免实验额度
        # 混进生产默认。
        _vc_kw = ({"max_verifications": int(max_verifications)}
                  if max_verifications is not None else {})
        _verify_ctl = VerificationController(verify=_verify,
                                             require_coherence=True,
                                             validate_record=_protocol_problems,
                                             repair=_repair_verification,
                                             deadline_at=_plan_deadline,
                                             rule_version=VERIFY_RULE_VERSION,
                                             log=print,
                                             # S10（2026-11-21 用户裁决·方案1）：生产入口启用
                                             # 稳定性兜底（单次语义负面复核一次，一致负面才阻断；
                                             # coherence 轴降为 advisory）。库默认关闭，观察工具不受影响。
                                             stability_confirmation=True,
                                             # 原文身份由控制器盖章（复核 2026-10-07
                                             # 三轮 ②）：记录必须绑**本次**原文。
                                             novel_digest=_novel_id["digest"],
                                             **_vc_kw)
        # ---- **把已验证的缓存记录交给控制器复用**（复核 2026-10-05 P2）----
        #
        # 读数：命中缓存后 `cache_hit=True`、生成 0 次、**核验仍 1 次**。原因就在这里：
        # 控制器是**新建**的，`records` 是空的，于是 `record_for()` 看不到任何已核验的
        # 候选，又调了一次模型。缓存里那份记录的身份在**读缓存时**已经核过（候选摘要 /
        # 原文摘要 / 规则版本 / 状态 + 三组结果），这里只需要把它**装进控制器**，
        # 后面的验收就会走"同摘要复用"那条路 ⇒ **生成 0 次、核验 0 次**。
        #
        # 只装**当前这份候选**对应的记录：别的候选的记录装进来也会被摘要挡住，
        # 但没必要——装一个就够，且装错的风险（拿别份候选的记录）由摘要判据兜住。
        if _from_cache and isinstance(outline, dict):
            _rec = outline.get("plan_verification")
            if isinstance(_rec, dict):
                from shared.content_verification import plan_digest_of
                _dig = plan_digest_of(outline)
                # **装载只有 `seed` 这一个入口**（复核 2026-10-07 三轮 ②）：它跑的是
                # 与解析/缓存读回/统一验收**同一份**记录契约——"缓存那边拒了、这边
                # 直接塞进 `records`"这条路关掉。
                # **把当前候选计划一起交进去**（复核 2026-10-09-r2 P2）：装载入口要能
                # 独立核对**义务支座**——只信记录自报的摘要，等于把「待核集合对不对」
                # 交给被检查的一方。给了计划就走共用判据，读不出视图 ⇒ 不装载。
                if _verify_ctl.seed(_dig, _rec, plan=outline):
                    print("[PLAN] 复用缓存中已验证的核验记录（候选 %s…）——本次不再核验"
                          % _dig[:12])
                else:
                    print("[PLAN] 缓存里的核验记录不合格（摘要不符 / 形状非法 / 规则版本"
                          " / 义务支座不符）⇒ 不复用，按没有记录处理")
        print("[PLAN] 内容核验已开启（候选核验和记录纠正共用最多 %d 次调用；"
              "原文 %s…）" % (_verify_ctl.max_verifications, _novel_id["digest"][:12]))
    _rev = run_plan_revision(_plan0, novel_text, model=model,
                             max_calls=max_revision_calls,
                             deadline_sec=revision_deadline_sec,
                             run_deadline_at=run_deadline_at, budget=_budget,
                             verification=_verify_ctl)
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
    if _verify_ctl is not None:
        # **核验台账单列**（§3.3「单列调用台账，不构成运行授权」）
        res["verification"] = dict(_verify_ctl.as_dict(),
                                   corrections=list(_verify_ctl.corrections),
                                   records=len(_verify_ctl.records))
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
                # ⚠️ 外层版本取**模式对应值**（复核 2026-10-05 P1：此前无条件写
                # `PLAN_CONTRACT_VERSION`＝5，而内容是 hybrid 的版本 6 ⇒ 第二次读取
                # 按 `_expect_contract`＝6 比对不上，**缓存永远命中不了**，表现为
                # "每次都重新规划+重新核验"。外层与内容必须一致。
                {"source_hash": cache_key,
                 "plan_contract_version": _expect_contract,
                 "presentation_mode": _mode,
                 # 核验身份随缓存走：命中时据此**复用**已合法确认的核验记录，
                 # 不必为同一份候选再调一次模型（工作包 1「缓存生命周期」）。
                 "verification": (dict(_final.get("plan_verification"))
                                  if isinstance(_final.get("plan_verification"), dict)
                                  else None),
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
  两种写法都**必须自带** `｜原文依据：“…”`——**引号不能省**，也不要把整条节拍串粘进依据；
   缺正文、缺依据或依据没有引号，整批拒收。）

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
都要至少被一个节拍声明承载**（`载体：台词k`）。**分隔符必须是全角竖线 `｜` +
全角冒号 `：`**；`；载体：`、`|载体：`、`｜载体:` 都不算登记（解析器逐字匹配）。
你改某条节拍的载体时，要保证没有
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






def _render_beat_op(new_text, new_carrier, old_record, n_lines: int, *,
                    require_carrier: bool = False, n_narration: int = 0) -> tuple:
    """把一条操作的 `(new_text, new_carrier)` 落成节拍文本 → `(文本, 原因)`.

    判据读 `BeatRecord` 的**字段**，不再靠字符串拼接：`new_text` 是**整条节拍正文**
    （自带「｜原文依据：…」）；模型若把旧标注一起抄进 `new_text`，这里**先删干净再
    重建**，从表示上消除「｜载体：画面｜载体：画面」这类重复标注（实跑出现过——
    旧实现靠事后数字符串才发现）。
    """
    from step01_script_gen.plan_feasibility import (
        BEAT_SEG_BODY, BEAT_SEG_CARRIER, BEAT_SEG_EVIDENCE, BeatRecord)
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






#: 身份清单的渲染上限：超过就**显式给出总数**（复核之六 P2 同一条口径——反馈与拒收
#: 记录里不许 `[:N]` 静默省略）。这里渲染出的字符串会**进下一轮的请求**：只点名前几条
#: 会让模型以为剩下的不存在。
_ID_BRIEF_CAP = 12












#: 必保留事实条目里的**承载段**（`｜承载：B2/画面`）——承载关系必须**落进计划**，
#: 否则只在 ops 里存在，`render_execution_contract` 传不给生成器（2026-09-30 复核）。
#: **实现与判据都在 `plan_feasibility`**：那道门是首版/重选版/缓存版**共用**的
#: （复核：「前移到所有计划的生成前验收……不接受『只有重选受约束』」），本文件只保留
#: 旧名转发，不维护第二份实现。
_FACT_CARRIER_SEG = "承载："









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










# 与 prompts.py「总时长合同」同源：合法区间是硬门，目标带只是优先（S34）。
_TARGET_MIN_SEC = DURATION_TARGET_MIN_SEC
_TARGET_MAX_SEC = DURATION_TARGET_MAX_SEC
# 匹配「30s」「30秒」「30 s」
_BUDGET_NUM_RE = __import__("re").compile(r"(\d+(?:\.\d+)?)(\s*[sS秒])")






# ---------------------------------------------------------------------------
# 呈现模式：旧模式（图外旁白） / 新模式（画面文字卡）
# ---------------------------------------------------------------------------
PRESENTATION_MODE_ENV = "STEP01_PRESENTATION_MODE"




#: 新模式追加到规划提示的规则块（**只在新模式下拼接**，不改旧模式的提示面）。
HYBRID_PROMPT_BLOCK = """
# 本集为**画面文字卡模式**（`screen_hybrid`）
- 本集**不使用图外旁白**：`narration_lines` 必须给**空数组** `[]`。原文叙述里必须让观众
  知道、画面又拍不出来的内容，改用**文字卡**表达。
- 文字卡写在 `screen_cards` 里，每条：
  `{{"beat": B节拍号, "template": "character|time_place|background",
     "text": "**原文里逐字照抄的片段**"}}`
  `beat` 是**承载它的那条节拍**（那条节拍的正文要演出这段信息对应的动作，
  卡片只是把观众看不出来的东西写清楚）。
- `text` 必须是原文的**逐字片段**（能在原文里一字不差地找到），不许改写、不许概括、
  不许自造。卡片只是把原文写出来，不新增内容。
  （**修订轮**同一口径见 `source_verbatim_rule()`——那条同时覆盖卡片/对白/旁白/原文依据，
  且点名了"别把反馈里的描述抄成正文"这个实测踩过的坑；两处口径相同，改一处要同步另一处。）
- 三类模板：`character` 人物介绍（姓名/字/年龄/身份）、`time_place` 时空说明、
  `background` 背景信息。**模板标题是样式**，不用你写。
- 卡片**有阅读时间预算**：一条卡的字数越多，需要的展示时间越长；预算不够会被拒收
  （须选完整且更短的逐字片段，或把可演的信息转移给画面/合法对白；不删核心事实）。
  分页不会降低总阅读时间，多张卡还各有静置成本。**不要**把整段原文都搬进卡片。
- 人物对白照旧：`dialogue_lines` 与「载体：台词k」的规则不变。
- **事件归属要显式写**：`beat_events` 与 `shot_beats` **等长**，第 i 项写第 i 条节拍承接的
  `main_events` 序号（1 起，可以是多个）。每个主要事件至少被一条节拍承接，事件先后不得
  回退。**代码不会替你按顺序猜**——不写就会被判「事件归属未声明」。
- 卡片挂在画面节拍上时，这条事实就是**画面与卡片联合承载**：画面演出动作，卡片把镜头里
  看不出的姓名/时间/身份写清楚。两边合起来才算完整表达。
- **卡片承载的事实这样声明**：`｜承载：B节拍号/卡片k`——`k` 是 `screen_cards` 里的
  **第 k 条**（1 起，全局序号），节拍号写**这张卡挂在哪条节拍上**。写 `文字卡k` 一样认。
  词表是 `{carrier_words}`；**写不出来、序号越界、卡片不在声明的节拍上，都会被拒收并
  原样回给你**（不替你猜、不按位置补、不静默清成「没有载体」）。
"""








