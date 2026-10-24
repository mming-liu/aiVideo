# -*- coding: utf-8 -*-
"""step01_script_gen.content_verify —— **内容核验的模型侧**：装配请求、解析响应.

职责边界（R4 / 主专题 §3.3）：

- **本模块**负责"怎么问、怎么解析"；
- `shared.content_verification` 负责**记录的形状与身份**（纯函数）与**调用上限**（控制器）；
- `outline.plan_acceptance_problems` 是唯一判断入口，缺记录不得判通过。

输入**分开提供**：冻结原文身份与原文上下文、事实正文、当前有效证据、**实际载体正文**
（画面正文/卡片正文/对白正文/旁白正文）、实际对白。引文**不混入已展示正文**——那就是
R §2.3 误收的成因。不给人名/关键词/重合阈值当出口，也不强迫复述引文里不属于主张的
附带信息。

**三类结论分开**（工作包 2 第 2 条），一个 `supported` 不能代替：

1. `source_verdict` —— **原文**是否支持这条**冻结主张**（来源支座；不支持 ⇒
   `fact_basis_invalid`，交回事实生产者，**执行者不许悄悄改主张换通过**）；
2. `carrier_verdict` —— **实际载体正文**是否完整表达该主张（表达支座）；
3. `voice_units[].source_kind` + `speaker` —— 对白是否真是原文里**人物说的话**。

**稳定 UID**：对白/旁白/卡片沿用计划里**保存的** uid（`d01`/`dz1`…），**不按位置重新
编号**——按位置编号会让"换了顺序"变成"说话人张冠李戴"。

**核验失败不当作内容可接受**：超时、坏响应、解析失败都记 `status != "ok"`。
"""
from __future__ import annotations

import json

from shared.content_verification import (CLAIM_OBLIGATION_ID as _CLAIM_ID,
                                         COMPONENT_KINDS, VERDICT_SUPPORTED,
                                         DISPLAY_TASK_SCHEMA,
                                         display_carriers, display_input_digest,
                                         display_task_input,
                                         SCHEMA_VERIFICATION,
                                         VERDICTS, DEFAULT_RULE_VERSION,
                                         frozen_obligations)

SYSTEM_PROMPT = ("你是内容核验员。你要分别核验下面的内容；另按请求核对整份计划的前后依赖，不要混为一谈："
                 "(1) 给定的**原文**是否支持这条**主张**；"
                 "(2) 观众从**实际展示正文**里能不能完整得到这条主张；"
                 "(3) 这条声音是不是原文里**人物说的话**、说话人是谁。"
                 "只输出纯JSON，不要解释。")

# Response shape remains v1; this revision makes the existing semantic-carrier
# policy explicit. Old judge records must not silently survive the new request.
VERIFY_RULE_VERSION = DEFAULT_RULE_VERSION

_VERIFY_SCHEMA = {
    "name": "content_verification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "source_support": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_uid": {"type": "string",
                                     "description": "**原样回带**给定的 fact_uid"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "evidence_spans": {
                            "type": "array", "items": {"type": "string"},
                            "description": "原文里支持该主张的片段（逐字，**出自原文**）"},
                        "reason": {"type": "string"},
                    },
                    "required": ["fact_uid", "verdict", "evidence_spans", "reason"],
                    "additionalProperties": False,
                },
            },
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_uid": {"type": "string",
                                     "description": "**原样回带**给定的 fact_uid"},
                        "verdict": {"type": "string",
                                    "enum": list(VERDICTS)},
                        "support_spans": {
                            "type": "array", "items": {"type": "string"},
                            "description": "**必须逐字出自实际展示正文**；"
                                           "不许拿「原文证据」「信息目的」或承载声明冒充"},
                        "missing": {"type": "string"},
                        "suggested_carriers": {"type": "array", "items": {"type": "string"}},
                        "obligations": {
                            "type": "array",
                            "description": "**代码给定的**待核义务逐条回带"
                                           "（id 与 text 原样照抄，不许增删改写）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {
                                        "type": "string",
                                        "description": "**原样照抄**清单里的 id"},
                                    "text": {
                                        "type": "string",
                                        "description": "**原样照抄**清单里的那条义务文字"},
                                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                                    "evidence": {
                                        "type": "array",
                                        "description": "**联合承载**：这条义务由哪些载体"
                                                       "各自展示了一部分；supported 至少一项",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "carrier_uid": {
                                                    "type": "string",
                                                    "description": "**哪个载体**展示了这部分"},
                                                "exhibitor_span": {
                                                    "type": "string",
                                                    "description": "**逐字出自该载体实际正文**"
                                                                   "的片段"},
                                            },
                                            "required": ["carrier_uid", "exhibitor_span"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "missing": {
                                        "type": "string",
                                        "description": "非 supported 时**必须**写清缺哪一部分"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["obligation_id", "text", "verdict",
                                             "evidence", "missing", "reason"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["fact_uid", "verdict", "support_spans", "missing",
                                 "suggested_carriers", "obligations"],
                    "additionalProperties": False,
                },
            },
            "voice_units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "unit_uid": {"type": "string",
                                     "description": "**原样回带**给定的 unit_uid"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "source_kind": {"type": "string",
                                        "enum": ["novel_speech", "novel_narration",
                                                 "not_in_novel"]},
                        "speaker": {"type": "string"},
                        "echo_text": {
                            "type": "string",
                            "description": "**原样回带**这条声音单位的正文——代码据此确认"
                                           "结论说的是**这条**台词，而不是别的"},
                        "reason": {"type": "string"},
                    },
                    "required": ["unit_uid", "verdict", "source_kind", "speaker",
                                 "echo_text", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["source_support", "facts", "voice_units"],
        "additionalProperties": False,
    },
}

from step01_script_gen.plan_coherence import SCHEMA as _COHERENCE_SCHEMA
_VERIFY_SCHEMA['schema']['properties']['coherence'] = _COHERENCE_SCHEMA
from step01_script_gen.source_revision import PRESERVATION_SCHEMA as _PRESERVATION_SCHEMA
_VERIFY_SCHEMA['schema']['properties']['source_preservation'] = _PRESERVATION_SCHEMA

VERIFY_SCHEMA = _VERIFY_SCHEMA

#: 展示任务的响应 schema：**只有 `facts` 一组**（来源轴与声音轴不走这个任务）。
#: 结构与主 schema 的 facts 组**同源**——直接引用那一份，不手抄第二份。
DISPLAY_SCHEMA = {
    "name": "content_verification_display",
    "schema": {"type": "object", "properties": {
        "facts": _VERIFY_SCHEMA["schema"]["properties"]["facts"]},
        "required": ["facts"], "additionalProperties": False},
}


def _carrier_block(view, fact_uid) -> list:
    """该事实的**实际载体**逐条列出（种类 + **实际展示正文**）.

    ⚠️ **「本镜信息目的」不进这里**（工作包 2 第 3 条）：`purpose` 是**声明**，观众在
    成片里看不到它。把它当"实际展示正文"交给核验，就等于允许模型拿一句声明冒充
    "画面里有这些信息"——那正是 R §2.3 的误收形态。它另列在「声明（不算证据）」栏。
    """
    from shared.presentation_contract import ref_uid

    def _objs(value) -> list:
        """只取对象成员。**这不是"过滤坏项继续走"**：核验请求的入口
        （`verify_view_with_service` / `build_verification_prompt`）先跑
        `view_admission_problems`，坏输入在那里就被拒、**一次模型调用都不会发**。
        这一层只保证"绕过边界直调也不会崩在半路"，判据仍然只有那一处。
        """
        return [x for x in (value if isinstance(value, list) else [])
                if isinstance(x, dict)]

    _out = []
    _beats = {str(b.get("uid") or ""): b for b in _objs(view.get("beats"))}
    _cards = {ref_uid(c): c for c in _objs(view.get("screen_cards"))}
    _units = {}
    for _u in _objs(view.get("dialogue_units")):
        _units[str(_u.get("uid") or "")] = ("dialogue", _u)
    for _u in _objs(view.get("narration_units")):
        _units[str(_u.get("uid") or "")] = ("narration", _u)
    for _b in _objs(view.get("fact_bindings")):
        if str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in _objs(_b.get("carriers")):
            _k, _u = str(_c.get("kind") or ""), ref_uid(_c)
            if _k == "visual" and _u in _beats:
                _out.append({"kind": "画面节拍", "uid": _u,
                             "text": str(_beats[_u].get("text") or ""),
                             "purpose": str(_beats[_u].get("purpose") or "")})
            elif _k == "screen_card" and _u in _cards:
                _out.append({"kind": "文字卡", "uid": _u,
                             "text": str(_cards[_u].get("text") or "")})
            elif _k in ("dialogue", "narration") and _u in _units:
                _out.append({"kind": "对白" if _k == "dialogue" else "旁白", "uid": _u,
                             "text": str(_units[_u][1].get("text") or "")})
    return _out


def _request_admission(view) -> list:
    """核验请求的**输入准入**（与投影、卡片消费**共用同一份判据**）.

    复核 2026-10-06 第十二轮 ①：「核验请求形状不合格时**不调用模型**，也不归为
    "内容不支持"」。这里返回的问题记录由调用方转成 `PresentationContractError`，
    控制器把它记成 `FAIL_INPUT`（与调用失败、坏响应**分账**）。
    """
    from shared.presentation_contract import view_admission_problems
    _p = list(view_admission_problems(view))
    # **冻结主张为空 ⇒ 派生不出待核义务**（复核 2026-10-09 P1：待核集合来自冻结主张）。
    # 这份输入没有可核的支座：事后既判不出"哪一部分没被展示"，也没法核对集合。
    # 在这里挡住 ⇒ 零模型调用，控制器记 `FAIL_INPUT`（与"内容不支持"分账）。
    for _fu, _rows in frozen_obligations(view).items():
        if not _rows:
            _p.append({"code": "verify_obligation_basis_missing", "kind": "verification",
                       "ref": _fu, "uid": _fu, "path": "required_facts",
                       "reason": "事实 %s 的**冻结主张**为空，派生不出核验义务"
                                 "——待核集合必须来自主张本身" % _fu,
                       "fix": "由事实生产者补齐这条事实的主张（或删掉它）"})
    return _p


def build_verification_prompt(view, *, novel_text="", novel_digest="",
                              novel_version="", source_catalog=None) -> str:
    """核验请求的**正文块**（工作包 2 第 1 条：上下文必须**足够**核原文支持）.

    四段分开给：

    1. **冻结原文身份**（摘要 + 版本号）与**原文上下文**——核"原文是否支持主张"没有
       原文就无从判断，只能靠引文（而引文可能正是错的）；
    2. **事实主张**与**当前有效证据**（含 `evidence_updates` 的更正结果）；
    3. **实际展示正文**（画面正文/卡片正文/对白正文/旁白正文）——"观众能得到什么"；
    4. **声音单位**（沿用保存的 uid）。

    「声明」另列且明确标注**不算证据**。

    ⚠️ **准入不过 ⇒ 抛 `PresentationContractError`**（带 `problems`）：核验请求是拿
    这份视图去建的，"跳过坏项、照常问一次"会让模型在**清洗过的**内容上作答，而结论会被
    当成对**这一份**候选的内容结论。调用方（`verify_view_with_service` / 控制器）据此
    记 `FAIL_INPUT`，**不发起模型调用**。
    """
    _want_obligations = frozen_obligations(view)
    _adm = _request_admission(view)
    if _adm:
        raise PresentationContractError(_adm, where="核验请求")
    _lines = ["# 冻结原文身份",
              "原文摘要：%s" % (novel_digest or "（未提供）"),
              "原文版本：%s" % (novel_version or "（未提供）"), ""]
    _nt = str(novel_text or "")
    if _nt:
        from shared.config import PROMPT_MAX_CHARS as _PMC
        from step01_script_gen.source_references import render
        _lines += ["# 小说原文（核「原文是否支持主张」依据这里，**不是**依据引文）",
                   render(source_catalog) if source_catalog is not None else _nt[:_PMC], ""]
    else:
        _lines += ["# 小说原文（**未提供**——「原文是否支持主张」一律填 `unknown`，"
                   "不要凭引文猜）", "", ]
    _lines += ["# 待核验的**实际展示内容**（这是观众真的看到/听到的东西）", ""]
    for _f in (view.get("frozen_facts") or []):
        _fu = str(_f.get("uid"))
        _lines.append("## 事实 %s" % _fu)
        _lines.append("主张（**冻结**，你不能改写它）：%s" % str(_f.get("claim") or ""))
        _lines.append("当前有效证据：%s" % str(_f.get("evidence") or ""))
        # 2026-10-18（实测根因）：主张被**授权修正**之后，核验员仍照抄上一轮的判负措辞——
        # 原句是"…未限定为三年内"，而这一刻的主张与证据里**都没有**"三年内"。
        # 结果生产者已无事可做，循环只能以 no_change 终止、整集落不了盘。
        _lines.append("⚠️ 判负必须**引用上面这条主张或这段证据里真实存在的文字**；"
                      "**不得**引用此刻已不存在的限定（时间/条件/因果/否定）。"
                      "主张若已被授权修正过，就按**这一次**给出的文本独立重判，"
                      "不要沿用上一轮的结论或措辞。")
        # **待核义务由代码给定**（复核 2026-10-09 P1）：核验方只负责逐条回答，
        # 不许增删 id、不许改写 text——否则"删掉没被满足的义务"就能把整项做成
        # supported（"完整主张被结果子集替代"）。集合来自冻结主张，与候选展示无关。
        _obls = _want_obligations.get(_fu) or []
        _lines.append("**待核义务（冻结，由代码给定）**——下面每条都要在 `obligations[]`"
                      "里**恰好回带一行**，`obligation_id` 与 `text` **原样照抄**：")
        for _i, _o in enumerate(_obls, 1):
            _lines.append("  %d. [%s] %s" % (_i, _o["obligation_id"], _o["text"]))
        _car = _carrier_block(view, _fu)
        _lines.append("实际展示正文（**只有这些**是观众能看到的）：")
        for _c in _car:
            _lines.append("  - [%s %s] %s" % (_c["kind"], _c["uid"], _c["text"]))
        if not _car:
            _lines.append("  - （没有任何载体）")
        _decl = [c for c in _car if c.get("purpose")]
        if _decl:
            _lines.append("  （以下是**声明**，观众看不到，**不能**当证据：%s）"
                          % "；".join("%s=%s" % (c["uid"], c["purpose"]) for c in _decl))
        _lines.append("")
    _units = [(str(u.get("uid")), str(u.get("text") or ""), "对白")
              for u in (view.get("dialogue_units") or [])]
    _units += [(str(u.get("uid")), str(u.get("text") or ""), "旁白")
               for u in (view.get("narration_units") or [])]
    if _units:
        _lines += ["# 声音单位（uid **原样回带**；核对：是不是原文里**人物说的话**，"
                   "说话人是谁。`echo_text` 填这条的**原文正文**，一字不差）"]
        for _u, _t, _k in _units:
            _lines.append("- %s [%s] %s" % (_u, _k, _t))
        _lines.append("")
    _lines += [
        "# 判定规则（**三问分开答，不许用一个 supported 代替**）",
        "",
        # **取值集合必须写进提示**（复核 2026-10-08 真实校准实测）：本通道恒发
        # `json_object`、**不下发** `json_schema`，于是"允许取值"只写在 schema 里等于
        # 没写给模型——实测模型自造了 `missing` 这个值，三条义务因此判非法。
        # 取值单源仍是 `VERDICTS`，不在这里手抄第二份。
        "所有 `verdict` 只能取这四个值之一：%s。**不许自造别的写法**（`missing`/`absent`/`ok`/`true` 都不是合法取值；要表达「没被展示」就填 `partial`）。"
        % "、".join("`%s`" % _v for _v in VERDICTS),
        "",
        "## 问 1：`source_support[]` —— **原文**支持这条**冻结主张**吗？",
        "- `evidence_spans` 必须**逐字出自上面的原文**，不许拿引文当证据。",
        "- 核对完整主张及其全部主体、动作、否定、条件、时间、因果和认知限定；"
        "只支持其中一部分不能填 supported。原文的较弱信息不能证明主张新增的较强判断；"
        "reason 必须说明仍无依据的具体限定，不能把主张换成较弱的同主题概述再判通过。",
        "- 原文不支持 ⇒ 填 `partial`/`contradicted`/`unknown` 并写明原因。",
        "  代码会据此报 `fact_basis_invalid`（**来源支座不成立**），交回事实生产者按授权纠正；",
        "  **你不能改写主张来换取通过**。",
        "",
        "## 问 2：`facts[]` —— 观众从**实际展示正文**里能完整得到这条主张吗？",
        "- `support_spans` **必须逐字出自「实际展示正文」**：画面正文/卡片正文/对白正文。",
        "  「声明」栏、信息目的、承载声明、事实引用、较长出处**都不能冒充**。",
        "- `supported` 要求时间、否定、动作、身份、因果都不丢；",
        "  只表达一部分 ⇒ `partial` 并在 `missing` 写清缺什么；说的相反 ⇒ `contradicted`",
        "  并在 `missing` 写明**相反的主体/动作/关系**（展示出来的是什么）；",
        "  给定信息不足以判断 ⇒ `unknown` 并在 `missing` 写明**缺什么信息**（**不要**猜成 supported）。",
        "- 证据里**不属于这条主张**的附带信息没被展示，**不算缺**。",
        "- 联合承载（多个载体各表达一部分）**按并集**判断。",
        "- 画面节拍按演出的动作、状态和先后关系判断，不要求画面描述与主张使用相同措辞。",
        "  support_spans 逐字摘自画面描述，是引用记录的逐字要求，不是要求画面逐字复述主张。",
        "  不能要求文字卡再次复述已经由画面表达的部分；单纯写“交代这条事实”仍不算演出。",
        "- missing 只能指出冻结主张本身未被表达的部分，不能把原文证据中的附带细节加为新主张。",
        "  若实际正文已明确演出接手动作或时间跨度，不得因为没有同义的逐字卡片而判缺失。",
        "",
        "## 问 2b：`obligations[]` —— 上面给定的**待核义务**分别由谁展示",
        "- 待核义务是**代码按冻结主张给定的**（每个事实下面那份清单）：",
        "  **不许增删、不许改 id、不许改写 `text`**（`text` 原样照抄）——代码会逐条核对",
        "  集合与文本：缺项（`verify_obligation_missing`）/ 自造（`verify_obligation_unknown_id`）",
        "  / 重复（`verify_obligation_duplicate`）/ 改写（`verify_obligation_text_substituted`）都会被拒收。",
        "- `carrier_uid` 填上面列出的**那个 uid**（`uid=` 后面那串，**不要带类别、不要改写**），"
        "  `exhibitor_span` 逐字取自**同一个载体**的正文。",
        "- **联合承载**：一条义务可以由**多个载体**共同展示（例如「谎称」由对白表达、「去向」由画面",
        "  表达）——这时 `evidence[]` 里**每个载体各给一项**（`carrier_uid` + **逐字出自该载体实际",
        "  正文**的 `exhibitor_span`）。判断针对**所有证据的并集**，不要求单项自己表达整条义务。",
        "- 判 `supported` **至少给一项真实证据**（没有证据会被拒收：`verify_obligation_evidence_missing`）；",
        "  载体必须是**这条事实的载体**、片段必须**逐字出自那个载体的实际正文**——坏身份与",
        "  「引文冒充已展示内容」都会被拒收。",
        "- 其余三档（`partial`/`unknown`/`contradicted`）`evidence` 可以留空，但",
        "  **任何非 `supported` 的结论都必须写明 `missing`**（**包括 `contradicted`**），",
        "  而且要写**可被核对的差异**、不是通用套话：`contradicted` 要指出**相反的主体、动作或",
        "  关系**（展示出来的到底是什么），`partial` 要指出**哪一部分没被覆盖**，`unknown` 要指出",
        "  **缺什么信息**；空白会被拒收（`verify_obligation_missing_blank`）。",
        "- **事实裁决由逐条结果决定**（代码会核两者一致）：全部义务 `supported` ⇒ 该事实才能",
        "  填 `supported`；只要有一条不是，就填最坏的那一档（`contradicted` > `partial` >",
        "  `unknown` > `supported`），并把缺的那部分写进 `missing`。代码会拒收不一致的记录",
        "  （`verify_obligation_uncovered` / `verify_fact_verdict_inconsistent`）。",
        "## 问 3：`voice_units[]` —— 这条声音的出处与说话人",
        "- 原文里的**叙述句不是人物发言**（`source_kind=novel_narration`）；",
        "  来源分类按原文中的表达行为与上下文判定，不按引号、第一人称或当前声音槽猜测：",
        "  明确未说出口的思想、内心独白属于 novel_narration；有引号也不等于人物发言。",
        "  明确说出口的话属于 novel_speech；应阅读全文上下文，不能仅凭孤立引文或引号作来源结论。",
        "  对白槽必须是 `novel_speech` 并给出真实 `speaker`；旁白槽必须是 `novel_narration`，speaker 可为空。",
        "- `unit_uid` **原样回带**给定的值，不要重新编号。",
        "",
        "每个 fact_uid 在 `source_support` 与 `facts` 里**各恰好一条**；",
        "每个声音单位**恰好一条**。",
        "",
        "只输出纯JSON（uid 原样回带；下面是**本次输入的身份集合**，"
        "每个事实/声音单位都恰好一条，不能少、不能自造）：",
    ]
    _fact_ids = [str(_f.get("uid")) for _f in (view.get("frozen_facts") or [])
                 if str(_f.get("uid") or "")]
    _voice_ids = [str(_u.get("uid")) for _k in ("dialogue_units", "narration_units")
                  for _u in (view.get(_k) or []) if str(_u.get("uid") or "")]
    _example = {
        "source_support": [
            {"fact_uid": _fid, "verdict": "<按原文判定>",
             "evidence_spans": ["<逐字出自原文>"], "reason": ""}
            for _fid in _fact_ids],
        "facts": [
            {"fact_uid": _fid, "verdict": "<按展示判定>",
             "support_spans": ["<逐字出自实际展示正文>"],
             "missing": "", "suggested_carriers": [],
             "obligations": [{
                 "obligation_id": "<本事实下面列出的 id，原样照抄>",
                 "text": "<本事实下面列出的 text，原样照抄>",
                 "verdict": "<按展示判定>",
                 "evidence": [{"carrier_uid": "<展示它的载体 uid>",
                               "exhibitor_span": "<逐字出自该载体实际正文>"}],
                 "missing": "", "reason": ""}]}
            for _fid in _fact_ids],
        "voice_units": [
            {"unit_uid": _uid, "verdict": "<按原文判定>",
             "source_kind": "<novel_speech 或 novel_narration，按原文判断>",
             "speaker": "<原文中的说话人；旁白可空>",
             "echo_text": "<原样回带这条的正文>", "reason": ""}
            for _uid in _voice_ids],
    }
    _lines.append(json.dumps(_example, ensure_ascii=False, indent=1))
    _lines += [
        "本次事实身份：%s；本次声音身份：%s。"
        % (_fact_ids or "（无）", _voice_ids or "（无）"),
        "声音数组为空时输出 `[]`，不得凭事实 supported 或旁白类型补造声音结论。",
    ]
    from step01_script_gen.plan_coherence import prompt as coherence_prompt
    from step01_script_gen.source_revision import prompt as preservation_prompt
    result=chr(10).join(_lines) + coherence_prompt(view) + preservation_prompt(view)
    if source_catalog is not None:
        from step01_script_gen.source_references import instruction
        result+=instruction(source_catalog)
    return result


#: 展示任务**自己的** system（单问：只看实际展示正文够不够）。
#: 复核 2026-10-09-r3 §4：展示请求不该复用三问（原文/展示/声音）的 system——
#: 任务边界与请求必须一致。
DISPLAY_SYSTEM_PROMPT = (
    "你是展示内容核验员。这次只回答**一个问题**：观众从给定的实际展示正文里，"
    "能不能得到给定的冻结主张（含全部待核义务）。原文、出处与声明都不在输入里，"
    "不要假设它们存在。只输出纯JSON，不要解释。")

# ============================================================================
# **隔离展示任务**（复核 2026-10-09-r2 §5②）：只判"实际展示正文够不够"
# ============================================================================
#
# 与主核验任务（三问同上下文）分开的原因：主任务的上下文里同时有**小说原文**、**当前出处**
# 与**声明**，模型可以拿"原文里有这句话""出处写着这件事"去替代"观众真的看到了"。
# 这个任务的输入**只有**冻结事实 + 给定义务 + 该事实当前绑定载体的**实际正文**：
# 原文、出处、purpose、来源结论、声音单位、任何预期标签**一律不进请求**。
#
# 它**不**替代来源轴与声音轴：那两条轴仍按各自的依据判（原文 / 说话人）。

#: 展示任务请求里**禁止出现**的字段/内容（校准入口机械断言，不靠"我记得没放"）。
#: 名字是给断言用的：命中这些名字或对应取值即视为越界。
DISPLAY_FORBIDDEN_KEYS = ("novel_text", "evidence", "evidence_updates", "purpose",
                          "source_support", "voice_units", "expected", "label")


def build_display_prompt(view, decomposition=None) -> str:
    """隔离展示任务的请求正文（**只**含冻结事实、给定义务与实际展示正文）.

    刻意**不提**原文、出处与声明：这一版就是要问"只看观众看得到的东西，够不够"。
    请求里也不出现"这条义务成立/不成立"之类**预期标签**——那是答案，不是输入。
    """
    from shared.presentation_contract import PresentationContractError
    from shared.content_verification import (decomposition_problems,
                                             expected_obligations)
    # **显式传入的分解表先过消费边界准入**（复核 2026-10-09-r4 P2）：非法表在这里就被
    # 结构化拒收，`create_service` 一次都不会建——"声明启用了分量但表坏了"与
    # "没有启用分量"是两件事，前者不许静默退回根义务模式。
    _bad_table = decomposition_problems(view, decomposition)
    if _bad_table:
        raise PresentationContractError(_bad_table, where="展示核验请求的分量表")
    _adm = _request_admission(view)
    if _adm:
        raise PresentationContractError(_adm, where="展示核验请求")
    _in = display_task_input(view, decomposition)
    _lines = [
        "# 判什么",
        "下面每条事实给出：**冻结主张**、由代码给定的**待核义务**清单、以及观众**实际看到/听到**",
        "的正文。请只根据这些**实际展示正文**判断：观众能不能得到这条主张（含全部待核义务）。",
        "",
        "**这里没有小说原文、没有出处、也没有任何「本镜信息目的」之类的声明**——",
        "不要假设正文之外还有别的东西；只凭列出的正文作答。",
        "",
    ]
    for _u in _in["facts"]:
        _lines.append("## 事实 %s" % _u["fact_uid"])
        _lines.append("主张（**冻结**，你不能改写它）：%s" % _u["claim"])
        _lines.append("**待核义务（冻结，由代码给定）**——每条在 `obligations[]` 里"
                      "**恰好回带一行**，`obligation_id` 与 `text` **原样照抄**：")
        for _i, _o in enumerate(_u["obligations"], 1):
            _lines.append("  %d. [%s] %s" % (_i, _o["obligation_id"], _o["text"]))
        _lines.append("实际展示正文（**只有这些**是观众能得到的东西）：")
        if _u["carriers"]:
            for _c in _u["carriers"]:
                # **uid 必须无歧义**（本轮实验实测）：渲染成 `[visual b09]` 时，
                # 模型有时把整段连同类别一起当成 `carrier_uid`（4/6 次记录因此
                # 被记成坏身份）。这里把 uid 单独标出来。
                _lines.append("  - 载体 uid=%s（%s）：%s"
                              % (_c["uid"], _c["kind"], _c["text"]))
        else:
            _lines.append("  - （没有任何载体）")
        _lines.append("")
    _lines += _DISPLAY_RULES
    return chr(10).join(_lines)

_DISPLAY_RULES = [
    "# 判定规则",
    "所有 `verdict` 只能取这四个值之一：%s。**不许自造别的写法**。"
    % "、".join("`%s`" % _v for _v in VERDICTS),
    "",
    "- 只根据**上面列出的实际展示正文**判断；没列出来的东西（小说原文、人物内心、",
    "  卡片之外的设定、「本镜信息目的」之类的声明）**一律不算**观众能得到的内容。",
    "- `support_spans` **逐字出自实际展示正文**；联合承载按**并集**判断。",
    "- `carrier_uid` 填上面列出的**那个 uid**（`uid=` 后面那串，**不要带类别、不要改写**）；",
    "  `exhibitor_span` 逐字取自**同一个载体**的正文。",
    "- 每条义务一行：`obligation_id`/`text` **原样照抄**给定的那两条；`verdict`；",
    "  `evidence[]`（**联合承载**：每个展示了一部分的载体各一项，含 `carrier_uid` 与",
    "  **逐字出自该载体正文**的 `exhibitor_span`）；`missing`；`reason`。",
    "- 判 `supported` **至少一项真实证据**；判 `partial`/`unknown` 时证据可空，",
    "  但 `missing` **必须写清缺的是哪一部分**。",
    "- **事实裁决由逐条结果决定**：全部义务 supported ⇒ 该事实才能 supported；",
    "  否则取最坏的一档（`contradicted` > `partial` > `unknown` > `supported`）。",
    "",
    "只输出纯JSON（uid 原样回带）：",
    '{"facts": [{"fact_uid": "f01", "verdict": "supported",',
    '            "support_spans": ["<逐字出自实际展示正文>"],',
    '            "missing": "", "suggested_carriers": [],',
    '            "obligations": [{"obligation_id": "%s",' % _CLAIM_ID,
    '                             "text": "<上面给的那条义务的文字，原样照抄>",',
    '                             "verdict": "supported",',
    '                             "evidence": [{"carrier_uid": "<载体 uid>",',
    '                                           "exhibitor_span": "<逐字出自它>"}],',
    '                             "missing": "", "reason": ""}]}]}',
]


def stamp_display_record(record, view, decomposition=None) -> dict:
    """给展示记录盖**由代码产生的身份**（生产者出口与离线回放**共用这一处**）.

    复核 2026-10-09-r3 P1：`verify_display_with_service` 直接把解析结果返回，记录**没有**
    `obligation_basis`，于是生产出口产出的合法响应过不了本轴验收；而测试手工补摘要把
    这个缺口盖住了。所以盖章必须发生在**生产出口**，且只有这一份实现。

    盖三样（都不接受模型自报）：规则版本 / 义务支座摘要 / 展示身份摘要。
    """
    from shared.content_verification import (DEFAULT_RULE_VERSION,
                                             obligation_basis_digest)
    if not isinstance(record, dict):
        return record
    _out = dict(record)
    _out["schema"] = SCHEMA_VERIFICATION
    _out["status"] = str(_out.get("status") or "ok")
    _out["rule_version"] = DEFAULT_RULE_VERSION
    _out["obligation_basis"] = obligation_basis_digest(view, decomposition)
    _out["display_digest"] = display_input_digest(view, decomposition)
    return _out

def _call_evidence(request, result, prompt, params) -> dict:
    """一次模型调用的**证据包**（成功 / 截断 / 异常**共用这一个构造**）.

    复核 2026-10-09-r5 P2：旧写法在 `success=False` 时只把 `error` 字符串抛出去，
    provider 已经返回的**正文、usage、call_id、finish_reason、truncated 全被丢掉**——
    截断的调用于是既没有原始证据、也没有可复算的参数。这里只构造证据，**不判成败**；
    判定与语义结论由调用方在拿到证据之后做。

    ⚠️ 敏感头**不入包**：只带请求对象本身（调用方可按需取字段），不搬 headers。
    """
    return {"request": request, "result": result, "prompt": str(prompt or ""),
            "raw": str(getattr(result, "text", "") or ""),
            "error": str(getattr(result, "error", "") or ""),
            "success": bool(getattr(result, "success", False)),
            "call_id": str(getattr(result, "call_id", "") or ""),
            "finish_reason": str(getattr(result, "finish_reason", "") or ""),
            "truncated": bool(getattr(result, "truncated", False)),
            "usage": getattr(result, "usage", None),
            "resolved_model": str(getattr(result, "model", "") or ""),
            "params": dict(params or {})}


def _evidence_bundle(ev: dict, view, decomposition, *, failure_code=None) -> dict:
    """证据包 → 调用方可直接落档/消费的返回体（**失败时 `record=None`**）.

    P2-D：失败类别由调用方显式传入（`failure_code`），不在这里猜——坏 JSON 与传输失败
    走不同 code，调用方按实际情况定。
    """
    from shared.content_verification import FAIL_CALL, FAILURE_CODES
    _out = {k: ev.get(k) for k in
            ("prompt", "raw", "call_id", "finish_reason", "truncated",
             "usage", "resolved_model", "params", "error")}
    _out["record"], _out["problems"], _out["status"] = None, [], "failed"
    _out["display_digest"] = display_input_digest(view, decomposition)
    if not ev.get("success"):
        _code = failure_code or FAIL_CALL
        _out["problems"] = [{"code": FAILURE_CODES.get(_code, "verify_call_failed"),
                             "kind": "verification", "ref": "", "uid": "",
                             "path": "call",
                             "reason": "核验调用未成功（%s）：%s"
                                       % (ev.get("finish_reason") or
                                          ("截断" if ev.get("truncated") else "provider 报错"),
                                          ev.get("error") or "（无 error 文本）"),
                             "fix": "看原始响应与 finish_reason；**失败不产生有效记录**"}]
    return _out


def parse_display_response(text):
    """展示任务响应 → **`(记录, 问题)`**；记录里只有 `facts` 轴（另两组是空表）.

    形状仍走**同一份记录契约**（`record_shape_problems`）——展示轴不是"简化版协议"，
    少一项、类型不对、自造字段一样整份拒收。
    """
    from shared.content_verification import record_shape_problems
    from step01_script_gen.outline import _parse_outline_json
    _d = _parse_outline_json(text)
    _rec = {"schema": SCHEMA_VERIFICATION, "status": "ok",
            "source_support": [],
            "facts": (_d or {}).get("facts") if isinstance(_d, dict) else None,
            "voice_units": []}
    _bad = record_shape_problems(_rec, where="展示核验响应")
    if _bad:
        return None, _bad
    return _rec, []



def _persist_call(path, row):
    """Append and confirm one call event before advancing to another stage."""
    if path is None:
        return
    import os
    from pathlib import Path
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({k: v for k, v in row.items()
                                 if k not in ("request", "result")}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _check_verification_deadline(req):
    import time
    if req.deadline_monotonic is not None and time.monotonic() >= req.deadline_monotonic:
        exc = TimeoutError("核验发送前已过截止时刻")
        exc.failure_code = "verify_deadline"
        raise exc


def _send_verification(req, prompt, params, *, archive_path=None, call_context=None):
    from step01_script_gen.outline import create_service
    context = dict(call_context or {})
    evidence = {"prompt": prompt, "system_prompt": req.system_prompt,
                "json_schema": req.json_schema, "params": params, **context}
    sent = 0
    input_confirmed = False
    try:
        _persist_call(archive_path, dict(evidence, stage="input"))
        input_confirmed = True
        _check_verification_deadline(req)
        svc = create_service()
        _check_verification_deadline(req)
        sent = 1
        result = svc.generate(req)
        evidence = dict(_call_evidence(req, result, prompt, params), **context)
    except BaseException as exc:
        evidence.update(error=str(exc), sent=sent)
        if not input_confirmed:
            evidence.update(archive_error=str(exc), archive_confirmed=False)
        try:
            _persist_call(archive_path, dict(evidence, stage="exception"))
        except Exception as archive_error:
            evidence.update(archive_error=str(archive_error), archive_confirmed=False)
        exc.evidence = evidence
        exc.sent = sent
        raise
    try:
        _persist_call(archive_path, dict(evidence, stage="result", sent=sent))
    except Exception as exc:
        evidence.update(archive_error=str(exc), archive_confirmed=False)
        exc.evidence = evidence
        exc.sent = sent
        raise
    return result, evidence


def verify_display_with_service(view, *, decomposition=None, model=None,
                                max_tokens=4000, deadline_monotonic=None,
                                archive_path=None, call_context=None) -> dict:
    """真的发一次**展示任务**请求（**走生产同一条调用路径**），返回**可留档的整包**.

    返回 `{"prompt": 实发正文, "raw": 原始响应, "record": 记录, "problems": 问题,
    "params": 实发参数}`——复核 2026-10-09-r2 §4 要求"每 run 独立保存请求/响应/参数/
    指纹"，所以这里把**实际送出去的东西**整包交回，不靠调用方复算。

    与 `verify_view_with_service` 分开：请求里**没有**原文、出处与声明，所以它只回答
    展示轴；来源轴与声音轴仍由主任务按各自的依据判。
    """
    from _text_gen.models import TextGenerationRequest
    from step01_script_gen.outline import create_service
    _prompt = build_display_prompt(view, decomposition)   # 两种准入都在这之前 ⇒ 零调用
    # **请求级输出上限**（复核 2026-10-09-r5 §3③）：默认沿用旧值以兼容，
    # 校准调用显式传值；参数与请求对象一起留档，不从配置倒推。
    _params = {"max_tokens": int(max_tokens), "temperature": 0.0, "model": model,
               "json_schema_name": DISPLAY_SCHEMA.get("name")}
    _req = TextGenerationRequest(
        system_prompt=DISPLAY_SYSTEM_PROMPT, user_prompt=_prompt,
        max_tokens=int(max_tokens), temperature=0.0,
        json_schema=DISPLAY_SCHEMA, model=model,
        deadline_monotonic=deadline_monotonic)
    _res, _ev = _send_verification(_req, _prompt, _params,
                                  archive_path=archive_path, call_context=call_context)
    if not _res.success:
        # **失败也交证据**（正文/用量/`finish_reason`/截断都留着），不抛异常把元数据丢掉。
        # P2-D：展示任务的 `success=False` 是传输失败，记 `FAIL_CALL` 不是 `bad_response`。
        from shared.content_verification import FAIL_CALL
        return _evidence_bundle(_ev, view, decomposition, failure_code=FAIL_CALL)
    _raw = _ev["raw"]
    _rec, _bad = parse_display_response(_raw)
    # **盖章在生产出口**（复核 r3 P1）：解析器只判形状，身份由这里按本次冻结请求给。
    if isinstance(_rec, dict) and not _bad:
        _rec = stamp_display_record(_rec, view, decomposition)
    # **provider 事实元数据**（复核 2026-10-09-r4 §4）：解析出的模型名、是否截断、
    # `finish_reason` 与用量都留档——不拿当前配置倒推历史，也不事后补。
    _bundle = _evidence_bundle(_ev, view, decomposition)
    _bundle.update(record=_rec, problems=_bad, status="ok" if _rec else "failed")
    if _bad:
        _bundle["failure_code"] = "verify_bad_response"
    return _bundle


# ============================================================================
# **三轴组合生成**（复核 r6 §4.3）
# ============================================================================
#
# 来源轴与声音轴来自**完整任务**（它拿得到原文与说话人依据）；展示事实来自**独立展示
# 任务**（它只拿冻结主张与各载体实际正文）。两份结论**按同一候选、同一原文**组合；
# 完整任务的 `facts` **不参与**组合——那是它顺带给的、没有按展示任务的口径判过。

def _bind_axes_identity(record: dict, *, plan_digest: str, novel_digest: str,
                        call_id: str) -> dict:
    """给展示记录绑定**本次调用的身份**（P1-A）。

    展示任务的盖章函数 `stamp_display_record` 只写规则/义务/展示摘要，**不写**候选/原文/
    call_id——那三样按**本次组合调用的实际上下文**给，不接受模型自报。`call_id` 取
    展示 bundle 自己的 `call_id`（provider 返回的那个），不是模型在 JSON 里写的字符串。
    """
    if not isinstance(record, dict):
        return record
    _out = dict(record)
    _out["plan_digest"] = str(plan_digest)
    _out["novel_digest"] = str(novel_digest)
    _out["call_id"] = str(call_id)
    return _out


def verify_view_axes_with_service(view, plan_digest, *, decomposition=None,
                                  model=None, deadline_monotonic=None,
                                  novel_text="", novel_digest="", novel_version="",
                                  remaining=None, source_model=None,
                                  display_model=None, display_max_tokens=4000,
                                  archive_path=None) -> dict:
    """**三轴组合生成**：完整任务（来源/声音）+ 独立展示任务 → `(记录, 问题, 证据)`.

    复核 r6 §4.3 的调用计量：**两次物理调用分别记账**——`calls` 里各有自己的
    `call_id`、原始响应与参数，**不把"组合核验"记成一次**。`remaining` 是本次可用的
    核验次数：**不足 2 次就停**（`verify_axes_capacity`），不许"半核"之后给出
    `supported`——半份证据比没有证据更坏，因为它看起来像核过了。

    返回 `{"status", "record", "problems", "calls", "evidence", "sent"}`；只有 `record`
    非 None 才是**有效成功记录**。`sent` 是**实际物理发送数**（与"尝试数"分账）：
    没发生发送的拒收不计入——§4.2 要求控制器按真实发送结算，不靠调用点自增。

    `source_model` / `display_model` 允许**逐轴给模型**（控制器从已准入上下文的 `models`
    读出来）；不传则回落到 `model`（兼容旧调用）。
    """
    from shared.content_verification import (FAIL_BAD_RESPONSE, FAIL_CALL,
                                             FAIL_INPUT, FAILURE_CODES,
                                             combine_axes_record)
    from shared.presentation_contract import PresentationContractError
    _out = {"status": "failed", "record": None, "problems": [], "calls": [],
            "evidence": {}, "sent": 0}

    def _fail(code, reason):
        _out["problems"] = [{"code": code, "kind": "verification", "ref": "",
                             "uid": "", "path": "axes",
                             "reason": str(reason),
                             "fix": "任一轴未成 ⇒ **不产生有效记录**；不要拿另一轴顶上"}]
        return _out

    # Request policy is part of input admission, before either service is created.
    if type(display_max_tokens) is not int or display_max_tokens <= 0:
        return _fail("verify_input_rejected", "展示输出上限必须为正整数")
    for name, value in (("model", model), ("source_model", source_model),
                        ("display_model", display_model)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return _fail("verify_input_rejected", "%s 必须为非空白模型名" % name)
    if remaining is not None and (type(remaining) is not int or remaining < 0):
        return _fail("verify_input_rejected", "核验容量必须为非负整数")

    # P1-C：**所有确定性输入准入在第一笔调用之前完成**——容量/view/上下文/表/
    # 候选与原文身份、两轴模型/参数，以及两轴输入可构造性。任一不合格 ⇒ 零调用。
    from shared.carrier_entailment import PROTOCOL, protocol_of
    from shared.quantified_verification import PROTOCOL as QUANTIFIED
    from shared.content_verification import verification_context_of
    _entailment = protocol_of(view) in (PROTOCOL, QUANTIFIED)
    _slots = 3 if _entailment else 2
    if remaining is not None and int(remaining) < _slots:
        return _fail("verify_axes_capacity",
                     "本次可用核验次数不足 %d——" % _slots +
                     "**不做半核**，交给上游按容量处理")

    # 完整任务输入准入（来源/声音轴）
    from shared.presentation_contract import PresentationContractError
    _adm_sv = _request_admission(view)
    if _adm_sv:
        return _fail(FAILURE_CODES.get(FAIL_INPUT, "verify_input_rejected"),
                     "来源/声音任务的输入未通过准入：%s"
                     % "、".join(str(x) for x in _adm_sv[:4]))
    _ctx_bad, _ctx_table = verification_context_of(view)
    if _ctx_bad:
        return _fail("verify_input_rejected", "核验上下文未通过准入")
    if _entailment and decomposition != _ctx_table:
        return _fail("verify_input_rejected", "展示表与已准入的新协议上下文不一致")

    # 展示任务输入准入（展示轴：view + 分解表）
    from shared.content_verification import decomposition_problems
    _adm_table = decomposition_problems(view, decomposition)
    if _adm_table:
        return _fail(FAILURE_CODES.get(FAIL_INPUT, "verify_input_rejected"),
                     "展示任务的分解表未通过准入：%s"
                     % "、".join(str((x or {}).get("code")) for x in _adm_table[:4]))
    try:
        _adm_disp = _request_admission(view)
        if _adm_disp:
            return _fail(FAILURE_CODES.get(FAIL_INPUT, "verify_input_rejected"),
                         "展示任务的 view 未通过准入：%s"
                         % "、".join(str(x) for x in _adm_disp[:4]))
    except Exception:  # noqa: BLE001
        return _fail(FAILURE_CODES.get(FAIL_INPUT, "verify_input_rejected"),
                     "展示任务的输入准入检查抛异常")

    # ① 完整任务：来源 + 声音（失败时生产出口把 result/request/evidence 挂在异常上）
    _sv_call: dict = {"axis": "source_voice"}
    try:
        _sv = verify_view_with_service(
            view, plan_digest, model=(source_model or model),
            deadline_monotonic=deadline_monotonic,
            novel_text=novel_text, novel_digest=novel_digest,
            novel_version=novel_version, archive_path=archive_path,
            call_context={"axis": "source_voice", "sequence": 1,
                          "plan_digest": plan_digest, "novel_digest": novel_digest,
                          "context": view.get("plan_basis", {}).get("verification_context")})
        _sv_call.update(call_id=str(_sv.get("call_id") or ""), status="ok")
        _out["sent"] += 1                       # 实际发出且成功
    except PresentationContractError as exc:
        # **输入没资格核**：与"调了没通"分账（一次调用都没发）
        _out["calls"].append(dict(_sv_call, status="input_rejected"))
        return _fail(FAILURE_CODES.get(FAIL_INPUT, "verify_input_rejected"),
                     "来源/声音任务的输入未通过准入：%s"
                     % "、".join(str(x) for x in (getattr(exc, "codes", []) or [])[:4]))
    except Exception as exc:                                          # noqa: BLE001
        # P2-D：来源/声音异常分两类——坏 JSON 抛 `VerificationResponseError`（记
        # `FAIL_BAD_RESPONSE`），传输失败抛 `RuntimeError`（记 `FAIL_CALL`）。
        from shared.content_verification import VerificationResponseError
        _out["sent"] += int(getattr(exc, "sent", 0))
        _ev = {k: v for k, v in (getattr(exc, "evidence", None) or {}).items()
               if k not in ("request", "result")}
        _sv_call.update(call_id=str(_ev.get("call_id") or ""), status="failed",
                        finish_reason=str(_ev.get("finish_reason") or ""),
                        truncated=bool(_ev.get("truncated")),
                        raw=str(_ev.get("raw") or ""), error=str(exc))
        _out["calls"].append(_sv_call)
        _out["evidence"]["source_voice"] = _ev
        _code = FAIL_BAD_RESPONSE if isinstance(exc, VerificationResponseError) else FAIL_CALL
        return _fail(getattr(exc, "failure_code", FAILURE_CODES.get(_code, "verify_call_failed")),
                     "来源/声音任务未成功：%s" % exc)

    # ② 独立展示任务（准入已在上面统一完成，这里只处理传输异常）
    if _entailment:
        from step01_script_gen.carrier_entailment import verify_display_entailment
        _bundle = verify_display_entailment(
            view, decomposition=decomposition, model=(display_model or model),
            max_tokens=display_max_tokens, deadline_monotonic=deadline_monotonic,
            archive_path=archive_path, call_context={"plan_digest": plan_digest,
                "novel_digest": novel_digest, "context": view.get("plan_basis", {}).get("verification_context")})
        _out["sent"] += _bundle["sent"]
        _out["calls"] = [_sv_call] + _bundle["calls"]
        _out["evidence"] = {"source_voice": _sv.get("_call_evidence", {}),
                            **_bundle["evidence"]}
        if not _bundle.get("record"):
            _out["problems"] = _bundle["problems"]
            return _out
        _display_rec = _bind_axes_identity(_bundle["record"], plan_digest=plan_digest,
            novel_digest=novel_digest, call_id=_bundle["call_id"])
        _rec, _bad = combine_axes_record(view, source_voice=_sv, display=_display_rec,
            decomposition=decomposition, plan_digest=plan_digest, novel_digest=novel_digest)
        _out.update(status="ok" if _rec else "failed", record=_rec, problems=_bad)
        return _out
    try:
        _bundle = verify_display_with_service(
            view, decomposition=decomposition, model=(display_model or model),
            max_tokens=display_max_tokens,
            deadline_monotonic=deadline_monotonic, archive_path=archive_path,
            call_context={"axis": "display", "sequence": 2,
                          "plan_digest": plan_digest, "novel_digest": novel_digest,
                          "context": view.get("plan_basis", {}).get("verification_context")})
    except Exception as exc:                                          # noqa: BLE001
        # P2-D：展示任务的传输异常（与来源/声音轴对称）——记 `FAIL_CALL`，保留证据。
        _out["sent"] += int(getattr(exc, "sent", 0))
        _ev = {k: v for k, v in (getattr(exc, "evidence", None) or {}).items()
               if k not in ("request", "result")}
        _disp_call = {"axis": "display", "status": "failed",
                      "call_id": str(_ev.get("call_id") or ""),
                      "finish_reason": str(_ev.get("finish_reason") or ""),
                      "truncated": bool(_ev.get("truncated")),
                      "raw": str(_ev.get("raw") or ""), "error": str(exc)}
        _out["calls"] = [_sv_call, _disp_call]
        _out["evidence"] = {"source_voice": _sv.get("_call_evidence", {}),
                            "display": _ev}
        return _fail(getattr(exc, "failure_code", FAILURE_CODES.get(FAIL_CALL, "verify_call_failed")),
                     "展示任务传输失败：%s" % exc)
    _out["sent"] += 1                           # 展示调用已发出（成功或 success=False 都算）
    _disp_call = {"axis": "display", "status": str(_bundle.get("status") or ""),
                  "call_id": str(_bundle.get("call_id") or ""),
                  "finish_reason": str(_bundle.get("finish_reason") or ""),
                  "truncated": bool(_bundle.get("truncated"))}
    _out["calls"] = [_sv_call, _disp_call]
    _out["evidence"] = {"source_voice": _sv.get("_call_evidence", {}),
                        "display": _bundle}
    # P2-D：展示失败按其**实际失败类别**传递，不以 `record is None` 猜——bundle 的
    # `problems` 已带 code（来自生产者或 `_evidence_bundle` 的 failure_code）。
    if not _bundle.get("record"):
        _probs = _bundle.get("problems") or []
        _codes = [str((x or {}).get("code")) for x in _probs[:4]]
        return _fail(_bundle.get("failure_code") or (_codes[0] if _codes else "verify_call_failed"),
                     "展示任务未成：%s" % "；".join(_codes))

    # P1-A：展示记录绑定本次调用身份（候选/原文/call_id）——用 bundle 的 `call_id`
    # （provider 返回的那个），不能接受模型自报。
    _display_rec = _bind_axes_identity(
        _bundle["record"], plan_digest=plan_digest, novel_digest=novel_digest,
        call_id=_bundle.get("call_id") or "")

    # ③ 组合（形状 / 身份 / 支座判据都在共用判据那一处）
    _rec, _bad = combine_axes_record(
        view, source_voice=_sv, display=_display_rec,
        decomposition=decomposition, plan_digest=plan_digest,
        novel_digest=novel_digest)
    if _rec is None:
        _out["problems"] = list(_bad)
        return _out
    _out.update(status="ok", record=_rec, problems=[])
    return _out


# ============================================================================
# **分量分解任务**（修正版 P1，复核 2026-10-09-r3 §5②）
# ============================================================================
#
# 只读**冻结主张**把它的必要关系列出来（主体/动作/时间/否定/条件/因果/身份…）。
# 请求里**只有**冻结 fact_uid 与完整主张：没有载体、没有原文出处、没有"候选缺什么"、
# 没有预期答案。分量表随**冻结基座**（uid + 完整主张 + 规则版本）复用，
# 同一基座在首版/各候选/缓存读回**用同一张表**，展示改动不重新分解。
DECOMPOSITION_SYSTEM_PROMPT = (
    "你是计划核验助手。这次只做一件事：把给定的**冻结主张**拆成它必须同时成立的"
    "几层关系（谁/做了什么/什么时间/是否否定/什么条件/因果/身份）。"
    "只输出纯JSON，不要解释。")

_DECOMPOSITION_SCHEMA = {
    "name": "content_verification_decomposition",
    "schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "description": "每个给定事实一条；分量只多不少",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_uid": {"type": "string",
                                     "description": "**原样回带**给定的 uid"},
                        "components": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string",
                                             "enum": list(COMPONENT_KINDS)},
                                    "relation": {
                                        "type": "string",
                                        "description": "这一层关系说的是什么（一句话）"},
                                    "span": {
                                        "type": "string",
                                        "description": "**逐字照抄主张里**对应的那段文字"},
                                },
                                "required": ["kind", "relation", "span"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["fact_uid", "components"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["facts"],
        "additionalProperties": False,
    },
}
DECOMPOSITION_SCHEMA = _DECOMPOSITION_SCHEMA


def build_decomposition_prompt(view) -> str:
    """分解请求正文：**只有**冻结 fact_uid 与完整主张（复核 r3 §5② 的禁入清单）.

    **不送**：载体与展示正文、原文出处（`evidence`）、`purpose`、声音单位、
    "候选缺了什么"、任何预期答案或标签。这份请求与候选无关——同一冻结基座
    在任何候选下发出的正文**逐字相同**。
    """
    _lines = [
        "# 做什么",
        "把下面每条**冻结主张**拆成它**必须同时成立**的几层关系。只输出 JSON。",
        "",
        "**这里没有剧情、没有画面、也没有任何「实际展示了什么」的信息**——",
        "不要猜候选能不能演出来，只按主张本身的字面拆。",
        "",
        "规则：",
        "- 每层关系给 `kind`（%s 之一）、`relation`（这一层说的是什么）与 `span`"
        % "、".join(COMPONENT_KINDS),
        "- `span` 必须**逐字照抄主张里的那一段文字**（不许改写、不许跨段拼接）；",
        "- 主张里的**每一个字都要被某一条 `span` 覆盖**（漏掉的部分会让整张表作废）；",
        "- 不许把同一段文字拆成两条；不许新增主张里没有的内容。",
        "",
    ]
    for _f in (view.get("frozen_facts") or []):
        if not isinstance(_f, dict):
            continue
        _lines.append("## 事实 %s" % str(_f.get("uid") or ""))
        _lines.append("冻结主张（**逐字**，你不能改写它）：%s" % str(_f.get("claim") or ""))
        _lines.append("")
    _lines += [
        "只输出纯JSON：",
        '{"facts": [{"fact_uid": "f01", "components": [',
        '    {"kind": "subject", "relation": "<这一层关系>", "span": "<逐字照抄主张里的一段>"}'
        ']}]}',
    ]
    return chr(10).join(_lines)


def parse_decomposition_response(text, view):
    """分解响应 → **`(表, 问题)`**；表按 `component_problems` 逐项准入（不合格整份拒收）."""
    from shared.content_verification import component_problems
    from step01_script_gen.outline import _parse_outline_json
    _d = _parse_outline_json(text)
    if not isinstance(_d, dict) or not isinstance(_d.get("facts"), list):
        return None, [{"code": "verify_decomposition_container", "kind": "verification",
                       "ref": "", "uid": "", "path": "facts",
                       "reason": "分解响应必须是 {facts: [...]}", "fix": ""}]
    _want = {str(_f.get("uid") or ""): str(_f.get("claim") or "")
             for _f in (view.get("frozen_facts") or []) if isinstance(_f, dict)}
    _tbl, _p = {}, []
    _seen: list = []
    for _i, _r in enumerate(_d["facts"]):
        if not isinstance(_r, dict):
            _p.append({"code": "verify_decomposition_member_not_object",
                       "kind": "verification", "ref": str(_i), "uid": str(_i),
                       "path": "facts[%d]" % _i, "reason": "必须是对象", "fix": ""})
            continue
        _fu = str(_r.get("fact_uid") or "")
        if _fu not in _want:
            _p.append({"code": "verify_decomposition_fact_unknown", "kind": "verification",
                       "ref": _fu, "uid": _fu, "path": "facts[%d].fact_uid" % _i,
                       "reason": "不是本次冻结事实", "fix": ""})
            continue
        if _fu in _seen:
            _p.append({"code": "verify_decomposition_fact_duplicate",
                       "kind": "verification", "ref": _fu, "uid": _fu,
                       "path": "facts[%d].fact_uid" % _i,
                       "reason": "同一事实出现了不止一次", "fix": ""})
            continue
        _seen.append(_fu)
        _bad = component_problems(_fu, _want[_fu], _r.get("components"))
        if _bad:
            _p += _bad
            continue
        _tbl[_fu] = list(_r.get("components") or [])
    _miss = sorted(set(_want) - set(_seen))
    if _miss:
        _p.append({"code": "verify_decomposition_fact_missing", "kind": "verification",
                   "ref": _miss[0], "uid": _miss[0], "path": "facts",
                   "reason": "这些冻结事实没有分量表：%s" % _miss, "fix": ""})
    if _p:
        return None, _p
    return _tbl, []


def run_decomposition(view, *, model=None, deadline_monotonic=None) -> dict:
    """真的发一次**分解**请求（走生产同一条调用路径），返回可留档整包."""
    from _text_gen.models import TextGenerationRequest
    from step01_script_gen.outline import create_service
    # 分解任务的输出由 `parse_decomposition_response` 逐项准入；这里只保证输入合法
    _prompt = build_decomposition_prompt(view)
    _params = {"max_tokens": 4000, "temperature": 0.0, "model": model,
               "json_schema_name": DECOMPOSITION_SCHEMA.get("name")}
    _req = TextGenerationRequest(
        system_prompt=DECOMPOSITION_SYSTEM_PROMPT, user_prompt=_prompt,
        max_tokens=4000, temperature=0.0,
        json_schema=DECOMPOSITION_SCHEMA, model=model,
        deadline_monotonic=deadline_monotonic)
    _svc = create_service()
    _res = _svc.generate(_req)
    _ev = _call_evidence(_req, _res, _prompt, _params)
    if not _res.success:
        _b = _evidence_bundle(_ev, {"frozen_facts": []}, None)
        _b.update(table=None)
        return _b
    _raw = _ev["raw"]
    _tbl, _bad = parse_decomposition_response(_raw, view)
    _b = _evidence_bundle(_ev, {"frozen_facts": []}, None)
    _b.update(table=_tbl, problems=_bad, status="ok" if _tbl is not None else "failed")
    return _b


def parse_verification_response(text, *, source_catalog=None):
    """响应正文 → **`(记录, 问题)`**（`记录 is None` ⇔ 问题非空）.

    ⚠️ 复核 2026-10-07 三轮 P1：旧写法把三组结果里的**非对象成员过滤掉**
    （`[dict(x) for x in ... if isinstance(x, dict)]`）——"答错了一项"于是变成
    "少了一项"，而缺项检查看的是**过滤后**的表，照样判通过。现在解析**一个字都不改**：
    三组按原值放进记录，交给**同一份记录契约**（`record_shape_problems`）判；不合格
    整份拒收并带回**字段路径 + 原值**，由控制器记 `FAIL_BAD_RESPONSE`（不是 `call_failed`
    ——"通了、答得不成形状"与"没通"是两类）。
    """
    from shared.content_verification import record_shape_problems
    from step01_script_gen.outline import _parse_outline_json
    _d = _parse_outline_json(text)
    if source_catalog is not None:
        from step01_script_gen.source_references import expand
        _d,_bad=expand(_d,source_catalog)
        if _bad:
            return None,_bad
    _rec = {"schema": SCHEMA_VERIFICATION, "status": "ok",
            "source_support": (_d or {}).get("source_support") if isinstance(_d, dict) else None,
            "facts": (_d or {}).get("facts") if isinstance(_d, dict) else None,
            "voice_units": (_d or {}).get("voice_units") if isinstance(_d, dict) else None}
    if isinstance(_d, dict) and 'coherence' in _d:
        _rec['coherence'] = _d['coherence']
    if isinstance(_d, dict) and 'source_preservation' in _d:
        _rec['source_preservation'] = _d['source_preservation']
    if source_catalog is not None and isinstance(_d,dict) and '_source_reference_evidence' in _d:
        _rec['_source_reference_evidence']=_d['_source_reference_evidence']
    _bad = record_shape_problems(_rec, where="核验响应")
    if _bad:
        return None, _bad
    return _rec, []


def verify_view_with_service(view, plan_digest, *, model=None, deadline_monotonic=None,
                             novel_text="", novel_digest="", novel_version="",
                             archive_path=None, call_context=None, max_tokens=None,
                             correction=None, source_references=False,
                             reasoning_effort=None):
    """真的去核验一次（**只有新模式、且显式开启时才会走到这里**）.

    ⚠️ **准入先跑，再建服务**：输入不合法时**一次调用都不发**（复核第十二轮 ① 的
    「核验请求」边界）。抛 `PresentationContractError`，控制器记 `FAIL_INPUT`。
    """
    from _text_gen.models import TextGenerationRequest
    from step01_script_gen.outline import create_service
    from shared.presentation_contract import PresentationContractError
    _adm = _request_admission(view)
    if _adm:
        raise PresentationContractError(_adm, where="核验请求")
    from _text_gen.config import config
    _max_tokens = config.CONTENT_VERIFY_MAX_TOKENS if max_tokens is None else max_tokens
    if type(_max_tokens) is not int or _max_tokens <= 0:
        raise ValueError("CONTENT_VERIFY_MAX_TOKENS / max_tokens 必须是正整数")
    _params = {"max_tokens": _max_tokens, "temperature": 0.0, "model": model,
               "json_schema_name": (VERIFY_SCHEMA or {}).get("name"),
               # A/B 对照需要**按请求**对齐思考强度：通道默认值不同（A 关闭 / B low），
               # 只靠环境变量会被 .env.content 覆盖（2026-10-18 实测），故显式透传。
               "reasoning_effort": reasoning_effort}
    from step01_script_gen.source_references import catalog_for
    _source_catalog=catalog_for(novel_text) if source_references else None
    _prompt = build_verification_prompt(
        view, novel_text=novel_text, novel_digest=novel_digest,
        novel_version=novel_version,source_catalog=_source_catalog)
    if correction:
        _prompt += ("\n\n【核验记录纠正：候选、原文、主张和待核义务完全不变】\n"
                    "纠正下面的核验协议问题，重新输出完整 JSON，不要修改计划。"
                    +("来源 evidence_spans 仍只填写本次 src_ 编号，由代码展开原文。"
                      if source_references else
                      "来源片段逐字复制原文；不连续的片段分成数组项，不得拼接或转述。")+
                    "展示片段逐字来自对应载体。精简 reason 和重复引文，保持所有主张、"
                    "义务、主体、否定、条件与因果关系，输出紧凑 JSON。\n"
                    + json.dumps(correction, ensure_ascii=False))
    _req = TextGenerationRequest(
        system_prompt=SYSTEM_PROMPT, user_prompt=_prompt,
        max_tokens=_max_tokens, temperature=0.0,
        json_schema=VERIFY_SCHEMA, model=model,
        reasoning_effort=reasoning_effort,
        deadline_monotonic=deadline_monotonic)
    _res, _ev = _send_verification(_req, _prompt, _params,
                                  archive_path=archive_path, call_context=call_context)
    if not _res.success:
        # 生产三轴仍抛异常（控制器据此记 `FAIL_CALL`），但**把实际结果与请求挂上去**：
        # 只留一句 error 字符串会让截断/坏响应失去可复算的原始证据（复核 r5 P2）。
        _exc = RuntimeError("核验调用失败：%s" % _res.error)
        _exc.result = _res
        _exc.request = _req
        _exc.evidence = _ev
        _exc.sent = 1
        raise _exc
    from shared.content_verification import VerificationResponseError, \
        record_shape_problems
    _rec, _bad = parse_verification_response(_res.text,source_catalog=_source_catalog)
    if _rec is None:
        # **形状不合格 ⇒ 整份拒收**，并带上问题记录：控制器据此记 `bad_response`
        # （与"调用失败"分账），报告里也能指出**哪一组、哪一项、原值是什么**。
        _exc = VerificationResponseError(_bad, where="核验响应")
        _exc.request, _exc.result, _exc.evidence, _exc.sent = _req, _res, _ev, 1
        raise _exc
    _rec["plan_digest"] = str(plan_digest)
    _rec["novel_digest"] = str(novel_digest or "")
    _rec["call_id"] = str(getattr(_res, "call_id", "") or "")
    _rec["_call_evidence"] = {k: v for k, v in _ev.items()
                              if k not in ("request", "result")}
    return _rec


def structurally_valid(plan) -> bool:
    """候选的**结构**是否已经合格——结构不合格的候选**不调用内容核验**（工作包 2 第 5 条）.

    核验是一次真实的模型调用；花在一个连引用都不闭合的候选上是纯浪费，而且核验结果
    会被当"内容结论"读，把"形状坏了"误报成"内容不支持"。顺序必须是：
    **结构 → （合格才）内容核验 → 完整验收**。

    复核 2026-10-06 二轮 P1：旧实现**只**跑 `outline_structure_problems`（旧大纲字段），
    于是"首版有非法载体引用"判下来仍是 True，核验照样发起一次——把"形状坏了"的候选送进
    了内容裁决。现在**共用**计划层的形状与关系判据：计划结构 → 读盘准入 → 视图形状与
    引用闭合 → 模式与事件关系。**不**要求"已有核验记录"（那会把"还没核"读成"结构不合格"）。
    """
    import json as _json
    from step01_script_gen.outline import outline_structure_problems
    from shared.presentation_contract import (parse_view, plan_mode, view_from_plan,
                                              view_problems)
    try:
        if outline_structure_problems(plan):
            return False
        _mode = plan_mode(plan) or None
        _view, _vp = view_from_plan(plan, mode=_mode)
        if _view is None or _vp:
            return False
        _copy = _json.loads(_json.dumps(_view))
        _pv, _pvp = parse_view(_copy, expected_mode=_mode)
        if _pv is None or _pvp:
            return False
        return not view_problems(_json.loads(_json.dumps(_view)))
    except Exception:                                                   # noqa: BLE001
        # 结构判据自己抛异常 ⇒ 无法证明结构合格 ⇒ 按**不合格**处理（fail-closed）：
        # "判不了"不是"可以放行"。
        return False


def verification_record_delivery(rec):
    """核验记录的交付判定：**只有 `status == "ok"` 才可当作合法核验记录**.

    失败记录（`bad_response` / `call_failed` / `input_admission_failed` /
    `deadline_exhausted`）说明“这次没验成”，不能挂到候选计划上被下一个消费者读成
    “已经核过”。判定与持久化分开：本函数纯判定，`deliver_verification_record`
    负责落盘与 `run_log` 记录。
    """
    if not isinstance(rec, dict):
        return {"accepted": False, "rejected_status": "<missing>",
                "rejected_code": ""}
    status = str(rec.get("status") or "")
    if status == "ok":
        return {"accepted": True, "rejected_status": None, "rejected_code": None}
    return {"accepted": False, "rejected_status": status or "<missing>",
            "rejected_code": str(rec.get("code") or "")}


def deliver_verification_record(rec, *, run_dir, log=None, transmitted=0):
    """按 `status` 落盘核验记录，并把失败状态写进 `run_log['verify']`.

    - `status == "ok"` ⇒ `verification_record.json`（规范记录）；
    - 其余（含 `status` 缺失）⇒ `verification_record.rejected.json`，
      并在 `log['verify']` 写 `rejected_status` / `rejected_code`；
    - 两种情况都清掉另一种文件，**不留下上一轮的规范记录冒充本轮结果**。

    返回 `verification_record_delivery(rec)` 的判定结果。
    """
    import pathlib

    outcome = verification_record_delivery(rec)
    root = pathlib.Path(run_dir)
    canonical = root / "verification_record.json"
    rejected = root / "verification_record.rejected.json"
    if outcome["accepted"]:
        canonical.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        rejected.unlink(missing_ok=True)
    else:
        if isinstance(rec, dict):
            rejected.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        canonical.unlink(missing_ok=True)
    if isinstance(log, dict):
        entry = log.setdefault("verify", {})
        entry["transmitted"] = int(transmitted or 0)
        entry["status"] = rec.get("status") if isinstance(rec, dict) else None
        if not outcome["accepted"]:
            entry["rejected_status"] = outcome["rejected_status"]
            entry["rejected_code"] = outcome["rejected_code"]
    return outcome


def attach_verification(plan, controller, *, verify=None, verify_axes=None):
    """取该候选的核验记录并挂到计划上（**不改计划内容**，只加诊断/核验键）.

    摘要由代码算（`plan_digest_of`，排除核验记录自身与诊断元数据）——核验方自报的摘要
    不作数（`VerificationController.record_for` 会盖章）。**生产与消费共用这一个函数**
    （复核 2026-10-05 P1：此前生产者按计划算、消费者按视图算，正常记录被判摘要不符）。

    结构不合格的候选**不发起核验**（`structurally_valid`）：那种情况下挂一条
    `failed` 记录，比挂一条看起来“核过了但没结论”的记录更诚实。但**失败记录不挂**
    （T2）：只有 `status == "ok"` 才写入 `plan_verification`；否则候选不带合法记录，
    失败 code 另存 `verification_rejected`，统一验收按它 fail-closed，运行器按它写
    `run_log['verify'].rejected_status` 与 `.rejected.json`。
    """
    from shared.content_verification import plan_digest_of
    from shared.presentation_contract import view_from_plan
    if not isinstance(plan, dict):
        return plan
    if getattr(controller, 'require_coherence', False):
        from step01_script_gen.plan_coherence import mark_plan
        plan = mark_plan(plan)
    if not structurally_valid(plan):
        return plan
    _dig = plan_digest_of(plan)
    _view, _ = view_from_plan(plan)
    if _view is None:
        return plan
    # **两轴路径必须显式选中**（复核 r7 §4.2）：给了 `verify_axes` 才走 `record_axes_for`
    # （两槽预留、按实际发送结算）；不给就是旧的单轴 `record_for`——**生产默认不启用**。
    if verify_axes is not None:
        _rec = controller.record_axes_for(_view, _dig, verify_axes=verify_axes)
    else:
        _rec = controller.record_for(_view, _dig, verify=verify)
    _out = dict(plan)
    _out.pop("verification_rejected", None)
    if verification_record_delivery(_rec)["accepted"]:
        _out["plan_verification"] = _rec
    else:
        # 失败记录另挂诊断键：它不是合法核验记录，验收不得把它当 `plan_verification`
        # 读；但失败 code 要保留下来（否则调用方会把“核验没做成”误读成“缺记录”）。
        _out.pop("plan_verification", None)
        if isinstance(_rec, dict):
            _out["verification_rejected"] = _rec
    return _out


def novel_identity(novel_text) -> dict:
    """**冻结原文身份**（摘要 + 版本号）——核验请求与缓存身份共用（工作包 2 第 1 条）.

    没有它，「原文是否支持主张」这一问就只能靠引文判断——而引文正是可能写错的那一份。
    """
    import hashlib as _hl
    _t = str(novel_text or "")
    return {"digest": _hl.sha256(_t.encode("utf-8")).hexdigest(),
            "chars": len(_t),
            "version": "novel_sha256:%s" % _hl.sha256(
                _t.encode("utf-8")).hexdigest()[:16]}


__all__ = ["SYSTEM_PROMPT", "VERIFY_SCHEMA", "build_verification_prompt",
           "parse_verification_response", "verify_view_with_service",
           "attach_verification", "verification_record_delivery",
           "deliver_verification_record", "structurally_valid", "novel_identity",
           "VERDICT_SUPPORTED"]
