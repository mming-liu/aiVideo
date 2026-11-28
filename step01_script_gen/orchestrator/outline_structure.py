# -*- coding: utf-8 -*-
"""S09: `step01_script_gen.outline` 的 outline_structure 职责（2026-11-28 拆分）.

父模块保留生成/修订编排、提示常量和缓存提交；本模块只放结构校验、
ops 应用与修订反馈。父模块当前值在调用点经 `_pv`/`_pc` 读取，
导入期不导入 orchestrator 或父模块。
"""
from __future__ import annotations

from __future__ import annotations
from shared.config import (DURATION_MIN_SEC, DURATION_MAX_SEC)
import copy
import json
import os
import re
from _text_gen.deepseek_schema import OUTLINE_SCHEMA
from shared.presentation_contract import (plan_mode)

_PARENT = "step01_script_gen.outline"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name):
    return _pv(name)


def _outline_max_tokens() -> int:
    """One configured operation limit for outline requests and cache identity."""
    from _text_gen.config import config
    value = config.TEXT_OUTLINE_MAX_TOKENS
    if type(value) is not int or value <= 0:
        raise ValueError("TEXT_OUTLINE_MAX_TOKENS 必须是正整数")
    return value

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

def _with_protocol_fields(records) -> list:
    """把问题记录收成**协议字段**：`code / kind / index / uid / path / reason / basis /
    fix / capability / limits`.

    生产者各写各的时容易漏字段；这里只做**归一与缺省**，**不从文本里恢复任何身份**
    （缺编号就是 `None`，不用 0 或猜出来的数字占位）。`detail` 作为兼容别名一并保留
    （旧消费者/日志还在读它），内容是同一份 `reason`。

    R3（`docs/aiVideo-step01-planning-closure-2026-10-04.md`）要求记录**自带身份与修法
    能力**，报告层不得从文案再猜：

    - `uid`：实体的**稳定身份**（`b03`/`d01`/`n02`/`f04`/`c01`…）。`index` 是**当前
      版本的展示编号**，两者不是一回事——只在文案里写 `B3` 时，版本一变就再也对不上。
    - `capability`：这条问题**需要哪种修改能力**（`ops` / `content` / `plan_field`），
      由盘点表按 `code` 正面查；未登记留空并由 `_fix_capability` 判 `undeclared`
      （fail-closed）。**在这里补写而不是在反馈层查**：请求、日志、诊断读的是同一份。
    - `limits`：适用限制（例如预算实际值）——有就带，没有就是 `{}`，不编造。
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
        #: **实体稳定身份**：生产者给了就用；没给时**不猜**（留空），
        #: 由 `_index_to_uid()` 在**编号域明确**的调用点补（见 `plan_acceptance_problems`）
        _x.setdefault("uid", "")
        _x.setdefault("limits", {})
        if not _x.get("capability"):
            _cap = _pv("PLAN_FIX_CAPABILITY").get(str(_x.get("code") or ""))
            if _x.get("fix"):
                _cap = _pv("_FIX_MARK_TO_CAPABILITY").get(str(_x["fix"]), _cap)
            _x["capability"] = (_cap[0] if _cap else "")
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
    # **有事件序号引用时，"空白事件"不许按可净化项放过**（复核 2026-10-07 P1）：
    # `beat_events` 按**序号**引用 `main_events`。净化先删掉一个空项 ⇒ 后面每一条引用
    # 整体前移、静默指向**另一个事件**，而形状/覆盖/顺序检查看的是**净化后**的表，
    # 照样报零问题。判据是"键在不在"（表自己写坏了也仍是"打算按序号引用"）：
    # 给了 `beat_events` ⇒ 空事件是**结构错**，由首版入口反馈重新生成完整计划；
    # 没有序号引用的旧模式保留原净化行为。
    _indexed = "beat_events" in plan
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
            elif _indexed and not _e.strip():
                _rec("structure_events_item_empty", "plan", _i,
                     "main_events[%d]" % (_i - 1),
                     "`main_events` 第 %d 条是空的——它**占着一个事件序号**"
                     "（`beat_events` 按序号引用事件），删掉它或拿空串占位都会让后面的"
                     "引用整体指错；请把这一条的正文补齐，**重新给出完整计划**" % _i)
        # **可净化项**（旧契约：空白项过滤掉再判数量）——这里只数**有效**条目，
        # 不把"留了个空串"报成结构错误（那是净化事务的事，工作包 §3 三个责任分清）。
        # 有事件序号引用时空白项已经是结构错，计数按**原始条数**算，免得再报一条
        # "条数不对"把真正的原因盖住。
        # 这个条数同时给覆盖检查用（复核第 39 轮：两处口径必须一致）。
        _n_events = len([_e for _e in _events
                         if isinstance(_e, str) and (_indexed or _e.strip())])
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

    for _field, _tag in _pv("_POSITIONAL_FIELDS"):
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
    from shared.presentation_contract import plan_beat_body_problems
    _out.extend(plan_beat_body_problems(plan))
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

    # ---- 背景档声明（2026-11-15 §10.6 两档判据；可选字段，缺席 = 全部主线档）----
    _bg = plan.get("background_facts")
    if _bg is not None:
        if not isinstance(_bg, list) or any(
                not isinstance(x, str) or not x.strip() for x in _bg):
            _rec("structure_background_facts_type", "plan", None, "background_facts",
                 "`background_facts` 必须是**逐项非空字符串**的数组（必保留事实的"
                 "编号，如 [\"f02\",\"f03\"]；现在是 %s）" % type(_bg).__name__)
        else:
            _dup = sorted({x for x in _bg if _bg.count(x) > 1})
            if _dup:
                _rec("structure_background_facts_duplicate", "plan", None,
                     "background_facts",
                     "`background_facts` 有重复 uid：%s——同一事实声明两次没有意义"
                     % _dup)
            _rf = plan.get("required_facts")
            _n_facts = len(_rf) if isinstance(_rf, list) else 0
            _pres = plan.get("presentation")
            _decl = _pres.get("fact_uids") if isinstance(_pres, dict) else None
            if (isinstance(_decl, list) and len(_decl) == _n_facts
                    and all(isinstance(x, str) and x.strip() for x in _decl)):
                _known = [str(x).strip() for x in _decl]
            else:
                _known = ["f%02d" % _i for _i in range(1, _n_facts + 1)]
            _unk = [x for x in _bg if x.strip() not in _known]
            if _unk:
                _rec("structure_background_facts_unknown_uid", "plan", None,
                     "background_facts",
                     "`background_facts` 引用了不存在的事实编号：%s（现有 %s）"
                     "——背景档只收**已登记**的必保留事实编号"
                     % (_unk, _known))
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
    _struct = _pc("outline_structure_problems")(plan)
    if _struct:
        return _with_protocol_fields(_struct)
    from shared import voice_adaptation as _va
    _adapt_problems = _va.adaptation_problems(plan, novel_text)
    if _adapt_problems:
        return _with_protocol_fields(_adapt_problems)
    _out.extend(_with_protocol_fields(_split_acceptance_records(plan)))
    # 身份与路径由生产者给出（工作包 §2.2）：这里只**组合**记录，
    # 不再用正则从问题文案里认编号（复核第 33 轮 P1 的"从正文猜编号"）。
    _out.extend(plan_contract_records(plan, novel_text=novel_text))
    _out.extend(carrier_relation_records(
        plan.get("shot_beats") or [], len(plan_dialogue_lines(plan)),
        len(plan_narration_entries(plan))))
    _out = _with_protocol_fields(_out)
    _rep = evaluate_plan(plan, novel_text=novel_text)
    # ---- R3：给每条记录补上**实体稳定身份**（编号域明确时才补，不猜）----
    #
    # `index` 是**当前版本的展示编号**（B3/D2…），`uid` 是**稳定身份**——两者不是一回事：
    # 只在文案里写 `B3`，计划一改（拆一条节拍）编号就全变，问题记录再也对不上是哪一条。
    # 这里能补是因为**编号域明确**（`presentation` 块里存着 uid 表）；没有 uid 表的旧计划
    # 走 `presentation_contract` 的 `plan_basis`/事件未声明路径，`uid` 留空——**留空而不是
    # 编一个**，编出来的身份比没有身份更坏。
    _uid_tables = _uid_tables_of(plan)
    for _rec in _out:
        if _rec.get("uid"):
            continue
        _u = _uid_for(_rec, _uid_tables)
        if _u:
            _rec["uid"] = _u
        # 预算类记录的 `limits` **单源**在 `_budget_limits()`：预算记录是在本函数末尾由
        # `_rep["problems"]` 追加的，这里补不到它们（2026-10-21 实测：请求里一直是「（无）」）。
    _out = _with_protocol_fields(_out)
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
    # 2026-10-21：预算记录**在这之后**才进 `_out`，上面那个补 `limits` 的循环永远补不到它们
    # ⇒ 请求里一直写「预算/限制：（无）」。补在这里，且只补预算类。
    for _rec in _out:
        if not str(_rec.get("code") or "").startswith("budget"):
            continue
        if not (_rec.get("limits") or {}):
            _rec["limits"] = _budget_limits(plan, _rep, _rec, novel_text=novel_text)
    # T2：失败核验记录**不挂** `plan_verification`（它不是合法记录），但也不能因此
    # 把失败原因吞成 `verify_record_missing`——候选上另存 `verification_rejected`，
    # 验收继续按它的 code fail-closed（调用方据此停在 verification_failed，不重规划）。
    _rejected_ver = plan.get("verification_rejected")
    _valid_ver = plan.get("plan_verification")
    if (isinstance(_rejected_ver, dict)
            and str(_rejected_ver.get("code") or "").strip()
            and not (isinstance(_valid_ver, dict)
                     and str(_valid_ver.get("status") or "") == "ok")):
        _out = [_r for _r in _out if _r.get("code") != "verify_record_missing"]
        _why = str(_rejected_ver.get("error") or "").strip()
        _out.append({
            "code": str(_rejected_ver.get("code")),
            "kind": "verification", "index": None, "path": "plan_verification",
            "reason": "核验未成功完成（status=%s）：%s"
                      % (_rejected_ver.get("status") or "?", _why[:240] or "（无说明）"),
            "detail": _why,
            "basis": "核验失败记录不当作内容可接受；失败记录只作诊断，不挂成合法核验记录",
        })
    return _with_protocol_fields(_out)

def _split_acceptance_records(plan) -> list:
    """计划上的拆分状态转正式验收残余（无拆分上下文返回空）."""
    from shared import fact_split as _fs
    from shared import presentation_contract as _pc
    try:
        _view, _err = _pc.view_from_plan(plan)
    except Exception:                                                  # noqa: BLE001
        return []
    if not isinstance(_view, dict) or _err:
        return []
    _rec = plan.get("plan_verification") if isinstance(plan, dict) else None
    return _fs.split_acceptance_problems(_view, _rec)

def _sentence_alternatives(text, novel_text, *, cap_sec=12.0, limit=3) -> list:
    """给不可单镜表示的句子找**原文里更短的合法替代**（只列证据，不做内容判断）.

    条件：逐字来自原文、估时 ≤ 单镜上限（留 0.1s 余量）、与原句有 ≥6 字的最长公共子串
    （没有重叠的句子不是"替代"，是换内容）。按重叠长度排序取前 `limit` 条。
    找不到就是找不到——由调用方标 `must_delete`，不编造候选。
    """
    import re as _re
    from step01_script_gen.plan_feasibility import _estimate_duration
    global _SENTENCE_RE
    if _pv("_SENTENCE_RE") is None:
        _SENTENCE_RE = _re.compile(r"[^。！？!?…]+[。！？!?…]+|[^。！？!?…]+$")
        import importlib as _importlib
        setattr(_importlib.import_module(_PARENT), "_SENTENCE_RE", _SENTENCE_RE)
    target = str(text or "").strip()
    if not target or not novel_text:
        return []

    def _lcs(a, b) -> int:
        best, prev = 0, [0] * (len(b) + 1)
        for ch in a:
            cur = [0] * (len(b) + 1)
            for j, ch2 in enumerate(b, 1):
                if ch == ch2:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best:
                        best = cur[j]
            prev = cur
        return best

    out = []
    for m in _pv("_SENTENCE_RE").finditer(novel_text):
        cand = m.group(0).strip()
        if not cand or cand == target or len(cand) > len(target) * 2:
            continue
        sec = float(_estimate_duration(cand))
        if sec > cap_sec - 0.1:
            continue
        ov = _lcs(cand, target)
        if ov < 6:
            continue
        out.append({"text": cand, "sec": round(sec, 2), "overlap": ov})
    out.sort(key=lambda x: (-x["overlap"], -x["sec"]))
    return out[:limit]

def _budget_limits(plan, rep, rec=None, *, novel_text="") -> dict:
    """预算类记录的**实际值 + 逐条成本**（R3：必须给数字，不能只说"超预算"）.

    2026-10-21 实测缺口：修订请求里的 `budget_infeasible` 只有合计（98.69s / 上限 90s），
    没有**逐条估时**，模型算不出"删哪一条、删多少"；它只能靠新增或整体重写猜。
    这里按**验收同一套估算器**给出每条声音的成本与所需净减量，uid 用视图编号域
    （与问题清单里 `narration_units[uid=…]` 同一身份）。
    """
    out = {"budget": dict((rep or {}).get("budget") or {}),
           "lower_bound_sec": (rep or {}).get("lower_bound_sec")}
    try:
        _max = float((out["budget"] or {}).get("max") or 0.0)
        _low = float(out["lower_bound_sec"] or 0.0)
    except (TypeError, ValueError):
        _max = _low = 0.0
    if _max and _low:
        out["need_cut_sec"] = round(_low - _max, 2)
    if str((rec or {}).get("code") or "") == "budget_infeasible":
        from step01_script_gen.plan_feasibility import _estimate_duration
        # 身份域必须与问题清单**同一套**（`narration_units[uid=n02]`）：先取视图 uid，
        # 取不到再退到 `presentation` 表，最后才是展示编号 N2/D3——不让模型自己配对。
        _tables = _uid_tables_of(plan)
        _view_uids: dict = {}
        try:
            from shared import presentation_contract as _pc
            _v, _ = _pc.view_from_plan(plan)
            _view_uids = {"dialogue": [str(u.get("uid") or "")
                                       for u in (_v.get("dialogue_units") or [])],
                          "narration": [str(u.get("uid") or "")
                                        for u in (_v.get("narration_units") or [])]}
        except Exception:
            _view_uids = {}
        _units = []
        for _kind, _field, _label in (("dialogue", "dialogue_lines", "D"),
                                      ("narration", "narration_lines", "N")):
            _uids = _view_uids.get(_kind) or _tables.get(_kind) or []
            for _i, _line in enumerate((plan or {}).get(_field) or [], 1):
                _text = _line.get("text") if isinstance(_line, dict) else _line
                _uid = _uids[_i - 1] if len(_uids) >= _i else ""
                _units.append({"kind": _kind,
                               "uid": _uid or ("%s%d" % (_label, _i)),
                               "sec": round(float(_estimate_duration(str(_text or ""))), 2)})
        _units.sort(key=lambda x: (-x["sec"], x["uid"]))
        out["voice_units"] = _units
    if str((rec or {}).get("code") or "") == "budget_unrepresentable":
        # 只给"重选一句"不够：3 次真实修订都保留了同一句 12.65s 旁白。这里给**验算过的**
        # 原文替代（逐字、≤12s、与原句有重叠）；没有替代就明确要求删除，不编候选。
        from step01_script_gen.plan_feasibility import _estimate_duration
        _kind = str((rec or {}).get("kind") or "")
        _field = "narration_lines" if _kind == "narration" else "dialogue_lines"
        _idx = (rec or {}).get("index")
        _lines = (plan or {}).get(_field) or []
        _text = ""
        if isinstance(_idx, int) and 1 <= _idx <= len(_lines):
            _e = _lines[_idx - 1]
            _text = str(_e.get("text") if isinstance(_e, dict) else _e or "")
        if _text:
            out["source_text"] = _text
            out["est_sec"] = round(float(_estimate_duration(_text)), 2)
            _alts = _sentence_alternatives(_text, novel_text)
            if _alts:
                out["alternatives"] = _alts
            else:
                out["must_delete"] = True
                out["hint"] = ("原文没有更短且相关的合法句 ⇒ 删掉这条声音，"
                               "并把它所在节拍改成画面载体（不要让节拍失去载体）")
    return out

def _uid_tables_of(plan) -> dict:
    """计划的**稳定身份表**（`kind → [uid, …]`）.

    `presentation` 块存在时以它为准（hybrid）；legacy 的稳定事实表在**计划顶层**
    `fact_uids`（拆分后子 uid 也在这里）。两者都没有 ⇒ 空表（旧计划按位置编号）。
    """
    _pres = (plan or {}).get("presentation")
    _fact = (plan or {}).get("fact_uids")
    if not isinstance(_pres, dict):
        return ({"fact": [str(x) for x in _fact]}
                if isinstance(_fact, list) and _fact else {})
    return {"beat": [str(x) for x in (_pres.get("beat_uids") or [])],
            "dialogue": [str(x) for x in (_pres.get("dialogue_uids") or [])],
            "narration": [str(x) for x in (_pres.get("narration_uids") or [])],
            "fact": [str(x) for x in (_pres.get("fact_uids") or [])],
            "card": [str(x.get("card_id") or x.get("uid") or "")
                     for x in (_pres.get("screen_cards") or [])
                     if isinstance(x, dict)]}

def _uid_for(rec, tables) -> str:
    """一条问题记录 → **实体稳定身份**（`kind` + `index` 给出；域不明确时留空）."""
    _k = str(rec.get("kind") or "")
    _i = rec.get("index")
    if not isinstance(_i, int) or _i < 1:
        return ""
    _t = (tables or {}).get(_k) or []
    if len(_t) < _i or not _t[_i - 1]:
        return ""
    return _t[_i - 1]

def _calibrate_time_budget(budget: str) -> str:
    """把大纲的时间预算**校准到目标区间**（v6.116，审核）.

    实测缓存大纲写「对白约30s；动作约60s；反应约30s」≈ **120s**，而生成约束的规划
    目标带（70-80s 或 85-110s）只是优先：**落在合法区间内就不校准**。只有总量
    超出 `[DURATION_MIN, DURATION_MAX]` 时才按原比例收到目标中点；其余文字不变。
    解析不出数字时原样返回（自由文本没有矛盾数字，注入无害）。
    """
    text = str(budget or "").strip()
    if not text:
        return ""
    nums = [float(m.group(1)) for m in _pv("_BUDGET_NUM_RE").finditer(text)]
    total = sum(nums)
    if not nums or (DURATION_MIN_SEC <= total <= DURATION_MAX_SEC):
        return text
    target = (_pv("_TARGET_MIN_SEC") + _pv("_TARGET_MAX_SEC")) / 2.0
    scaled = iter(round(n * target / total, 1) for n in nums)

    def _rep(m):
        try:
            v = next(scaled)
        except StopIteration:
            return m.group(0)
        return ("%g" % v) + m.group(2)

    return _pv("_BUDGET_NUM_RE").sub(_rep, text) + (
        "（已按目标 %g-%g 秒校准）" % (_pv("_TARGET_MIN_SEC"), _pv("_TARGET_MAX_SEC")))

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
    _bad = _pc("outline_structure_problems")(outline)
    if _bad:
        print("[WARN] 大纲校验失败：%s（不注入不完整大纲）"
              % (str(_bad[0].get("reason") or _bad[0].get("code"))[:80]))
        return None
    events = outline.get("main_events") or []
    # **有事件序号引用时不做压缩**（复核 2026-10-07 P1）：`beat_events` 按序号引用
    # `main_events`，先删掉一个空项会让后面每一条引用整体前移，而形状/覆盖/顺序检查
    # 看的是**净化后**的表、照样全过——关系静默指错。所以：给了 `beat_events` 的只做
    # **不改变位置的空白清理**（空项在结构判据那一层已被拒收，走不到这里）；没有序号
    # 引用的旧模式**保留原净化行为**（去空 + strip）。
    _indexed = "beat_events" in outline
    if isinstance(events, list):
        cleaned_events = [str(e).strip() for e in events
                          if _indexed or str(e).strip()]
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
        if field in _pv("_POSITIONAL_NAMES"):
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
    #
    # ⚠️ 版本由**模式单源**给（工作包 1 第 5 条），不是无条件写死 5：复核 2026-10-05 P1
    # 实测 hybrid 计划过了净化后被盖成版本 5，于是「缓存读的时候按模式期待版本 6、
    # 盘上却是 5」——写读不一致，缓存永远命中不了，且**内容还是对的**（更坏：
    # 它会让人以为内容错了）。
    from shared.presentation_contract import MODE_HYBRID, PLAN_CONTRACT_VERSION_BY_MODE
    from step01_script_gen.plan_feasibility import PLAN_CONTRACT_VERSION
    _mode = plan_mode(outline)
    if _mode == MODE_HYBRID or (isinstance(outline.get("presentation"), dict)
                                and str((outline.get("presentation") or {}).get("mode")
                                        or "") == MODE_HYBRID):
        _mode = MODE_HYBRID
    outline["plan_contract_version"] = PLAN_CONTRACT_VERSION_BY_MODE.get(
        _mode, PLAN_CONTRACT_VERSION)
    if _mode == MODE_HYBRID:
        # `presentation.mode` 也要与版本一致（两者是**同一件事的两种表述**）
        outline["presentation"]["mode"] = MODE_HYBRID
    _baseline.clear()                     # **采纳**：把净化后的候选写回原对象
    _baseline.update(outline)
    return _baseline

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
                              % (field, i, _what, _pc("_brief_value")(item), tag)}
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

def normalize_hybrid_plan(plan):
    """旧形状的计划 + `screen_cards` → **版本 6** 的新模式计划（`returns (plan, problems)`）.

    这是**生产者侧的规范化**，不是语义判断：它只把"事实由谁承载"从 `｜承载：…` 文案搬到
    `presentation.fact_bindings` 这一份权威关系上，并按 `screen_cards[].beat` 把卡片接进
    承载集合（**画面与卡片联合承载**——卡片说的是镜头里看不出的那部分）。

    规范化**不改**正文、证据、条数、事件与台词；`narration_lines` **不清空**（新模式用了
    旁白就该被模式合同拒收，不能悄悄丢掉模型给的内容）。

    **稳定身份随块保存**（`fact_uids` / `beat_uids` / `dialogue_uids` / `narration_uids`）：
    不保存的话，投影读回时身份会被按数组位置重新编号——"只在内存里稳定"等于不稳定。

    **事件归属只搬不编**：`beat_events` 取自模型显式给出的 `beat_events`（`main_events`
    序号，逐节拍一个数组）。没给就标 `event_binding=unknown`，由验收链按"未声明"报出来
    ——**不按位置一对一编一份**，那会把"仓库外景"登记成"周明辞职"而检查照样通过。
    """
    from shared.presentation_contract import (KIND_CARD, KIND_VISUAL, MODE_HYBRID,
                                              PLAN_CONTRACT_VERSION_HYBRID,
                                              PRESENTATION_SCHEMA, _carrier_decl, _ref_out,
                                              _split_decls, card_declaration_problems,
                                              carrier_ref_from_decl, view_from_plan)
    if not isinstance(plan, dict) or not plan:
        return None, [{"code": "plan_missing", "kind": "plan", "index": None, "path": "plan",
                       "reason": "计划缺失或不是对象", "detail": "计划缺失或不是对象"}]
    # **结构判据与首版入口同源**（复核 2026-10-07：「首版入口与直接规范化入口共用判据」）：
    # 本函数按**位置**读 `shot_beats` / `dialogue_lines` / `main_events` / `required_facts`
    # ——旧写法 `[str(b) for b in (... or [])]` 在这些字段不是数组（`=5`）时抛
    # `TypeError`、是字符串时又逐字符遍历，还是"转换把违约吃掉"（与卡片同一根因）。
    # 先跑那一份**纯诊断**结构判据，不为空就原样吐出记录（调用方按结构化拒收处理）。
    _struct = _pc("outline_structure_problems")(plan)
    if _struct:
        return None, _struct
    _out = json.loads(json.dumps(plan, ensure_ascii=False, default=str))
    _beats = [str(b) for b in (_out.get("shot_beats") or [])]
    _lines = [str(x) for x in (_out.get("dialogue_lines") or [])]
    _events = [str(e) for e in (_out.get("main_events") or [])]
    _beat_uids = ["b%02d" % _i for _i in range(1, len(_beats) + 1)]
    _dial_uids = ["d%02d" % _i for _i in range(1, len(_lines) + 1)]
    _problems: list = []

    # ---- 卡片：**先过准入，再做编号与形状转换** ----
    # 复核 2026-10-07 P1：旧写法 `enumerate(_out.get("screen_cards") or [], 1)` 在
    # `screen_cards=1` 时抛 `TypeError`（首版入口**崩在半路**，不是"拒收"）、
    # `str(...)` 把 `text=123` 洗成合法字符串、列表推导把坏 `source_spans` 成员筛掉
    # ——三种都是"转换把违约吃掉了"。顺序固定为：**容器 → 成员与字段类型 → 编号范围
    # → 转换**；非法一律带**原字段路径 + 原值**记问题（字段判据在
    # `card_declaration_problems`，与视图实体合同同一份口径）。首版入口与直接规范化
    # 入口调的是**同一个函数**，判据只有这一份。
    _cards_raw = _out.get("screen_cards")
    if _cards_raw is not None and not isinstance(_cards_raw, list):
        _problems.append({"code": "presentation_cards_type", "kind": "card",
                          "index": None, "path": "screen_cards", "value": _cards_raw,
                          "reason": "`screen_cards` 必须是**数组**（当前是 %s）"
                                    "——不按空表兜底、不按字符/键遍历"
                                    % type(_cards_raw).__name__,
                          "detail": "卡片容器不是数组"})
        _cards_raw = []
    _cards: list = []
    _by_beat: dict = {}
    for _i, _c in enumerate(_cards_raw or [], 1):
        _where = "screen_cards[%d]" % (_i - 1)
        _cp = card_declaration_problems(_c, _where, _i)
        if _cp:
            _problems.extend(_cp)
            continue
        # ⚠️ **转换前校验原始类型与范围**（复核 2026-10-06 二轮 P1）：旧写法 `int(_b)`
        # 会把 `1.9` **截成 1**、把 `True` 当成 1、把 `"2"` 也收下——静默改掉的是
        # "这张卡挂在哪条节拍上"。只接受**整数**（`bool` 是 `int` 的子类，要单独挡掉）。
        _b = _c.get("beat")
        if _b is None:
            _problems.append({"code": "presentation_card_beat_missing", "kind": "card",
                              "index": _i, "path": "%s.beat" % _where, "value": _b,
                              "reason": "卡片没有给承载节拍 `beat`——必须显式写它挂在哪条"
                                        "节拍上（不按位置补、不按卡片顺序猜）",
                              "detail": "卡片的承载节拍缺失"})
            continue
        if not 1 <= _b <= len(_beats):
            _problems.append({"code": "presentation_card_beat_missing", "kind": "card",
                              "index": _i, "path": "%s.beat" % _where, "value": _b,
                              "reason": "卡片的承载节拍 B%d 不存在（计划共 %d 条节拍）"
                                        % (_b, len(_beats)),
                              "detail": "卡片的承载节拍不合法"})
            continue
        _cid = "c%02d" % _i
        # **类型已在准入里核过**，这里**不再 `str(...)`**：`text=123` 不该被洗成
        # 字符串，`template={"a":1}` 更不该变成一段对象字面量。
        _item = {"card_id": _cid, "beat_uid": _beat_uids[_b - 1],
                 "template": _c.get("template") or "background",
                 "text": _c.get("text") or "",
                 "placement": _c.get("placement") or "info_panel",
                 "anchor": _c.get("anchor") or "beat_start"}
        _spans = _c.get("source_spans")
        if _spans:
            _item["source_spans"] = [dict(x) for x in _spans]
        _cards.append(_item)
        _by_beat.setdefault(_beat_uids[_b - 1], []).append(_cid)
    if _problems:
        return None, _problems

    # ---- 承载关系：读**旧形状的权威**（`｜承载：Bn/…`），再挂上该节拍的卡片 ----
    _view, _vp = view_from_plan(_out, mode="legacy_audio")
    if _view is None:
        return None, [dict(x, path=x.get("path") or "required_facts") for x in _vp]
    # ⚠️ **用共用词表 + 实际卡片表解析**（复核 2026-10-06 二轮 P1）：旧写法读的是
    # `view_from_plan(mode="legacy_audio")` 的结果，而**旧模式没有卡片表**——首版写的
    # `｜承载：B1/文字卡1` 在那里解析不出，四条事实因此**零载体**（核验只好报"没有载体"）。
    # 现在按 hybrid 自己的卡片表解析，解析不出就**保留 `raw` + 原因**（不丢原貌）。
    _bindings: list = []
    _frozen = _view.get("frozen_facts") or []
    # 共用解析器要的是**带 uid 的实体表**（与视图里的形状一致）
    _beats_v = [{"uid": _u} for _u in _beat_uids]
    _units_d = [{"uid": _u} for _u in _dial_uids]
    _facts_txt = [str(f) for f in (_out.get("required_facts") or [])]
    for _fi, _f in enumerate(_frozen, 1):
        _decl = _carrier_decl(_facts_txt[_fi - 1]) if _fi - 1 < len(_facts_txt) else ""
        _r = carrier_ref_from_decl(_decl, beats=_beats_v, dialogue=_units_d,
                                   narration=(), cards=_cards)
        _car = []
        if _r:
            _car.append(_ref_out({k: v for k, v in _r.items() if k != "reason"}))
            _k, _u = str(_r.get("kind")), str(_r.get("uid") or "")
            if _k == KIND_VISUAL:
                # **联合承载**：该节拍上的卡片一起承载这条事实（卡片写的是镜头里看不出的
                # 那部分）。同一节拍绑多张卡时全部登记——多张卡各自表达不同侧面是正当的。
                for _cid in _by_beat.get(_u, []):
                    _car.append(_ref_out({"kind": "screen_card", "uid": _cid}))
            elif _k == KIND_CARD:
                # 反向同一条契约（提示块原话：「卡片挂在画面节拍上时，这条事实就是
                # **画面与卡片联合承载**」）：卡片自己带节拍，把那条节拍的画面一并登记。
                _beat_of = next((c["beat_uid"] for c in _cards
                                 if str(c["card_id"]) == _u), "")
                if _beat_of:
                    _car.append(_ref_out({"kind": "visual", "uid": _beat_of}))
        _bindings.append({"fact_uid": str(_f.get("uid")), "carriers": _car})

    # ---- 事件归属：**只搬模型显式给的那份**，且**转换前先校验原始类型与范围** ----
    # ⚠️ 复核 2026-10-06 二轮 P1：旧写法 `_idx = _row if isinstance(_row, list) else []`
    # 把 `beat_events=[1,1,1,2,3,4]`（**扁平表**，不是数组的数组）**无告警**转成六个空数组；
    # `isinstance(True, int)` 让布尔编号当整数；浮点编号被 `isinstance(x, int)` 静默过滤。
    # 三种都是"转换把违约吃掉了"。现在的规矩：**先检查原值，合法才转换**；不合法就带
    # **原字段路径 + 原值**报出来，由调用方按结构化拒收处理。
    _be_raw = _out.get("beat_events")
    _beat_events = []
    _declared = False
    if _be_raw is not None:
        if not isinstance(_be_raw, list) or len(_be_raw) != len(_beats):
            _problems.append({
                "code": "presentation_beat_events_shape", "kind": "plan", "index": None,
                "path": "beat_events", "value": _be_raw,
                "reason": "`beat_events` 必须是**与节拍等长**的数组的数组"
                          "（现在是 %s / 节拍 %d 条）——不补空、不按位置猜"
                          % ("非数组" if not isinstance(_be_raw, list)
                             else "%d 项" % len(_be_raw), len(_beats)),
                "detail": "事件归属表形状不合法"})
        else:
            _be_ok = True
            for _i, _row in enumerate(_be_raw, 1):
                if not isinstance(_row, list):
                    _problems.append({
                        "code": "presentation_beat_events_item", "kind": "beat",
                        "index": _i, "path": "beat_events[%d]" % (_i - 1), "value": _row,
                        "reason": "`beat_events[%d]` 必须是**数组**（现在是 %r：%s）"
                                  "——扁平表不等于「每条节拍各一个数组」"
                                  % (_i - 1, _row, type(_row).__name__),
                        "detail": "事件归属行不是数组"})
                    _be_ok = False
                    continue
                for _x in _row:
                    _bad_x = (isinstance(_x, bool) or not isinstance(_x, int)
                              or not 1 <= _x <= len(_events))
                    if _bad_x:
                        _problems.append({
                            "code": "presentation_beat_events_index", "kind": "beat",
                            "index": _i, "path": "beat_events[%d]" % (_i - 1),
                            "value": _x,
                            "reason": "`beat_events[%d]` 里的 %r 不是合法的事件序号"
                                      "（要 1..%d 的**整数**；布尔、浮点、字符串都"
                                      "不转换）" % (_i - 1, _x, len(_events)),
                            "detail": "事件归属序号不合法"})
                        _be_ok = False
            if _be_ok:
                _declared = True
                _beat_events = [[_events[_x - 1] for _x in _row] for _row in _be_raw]

    # 事件归属的形状/序号违约 ⇒ **整份规范化不产出**（结构化拒收，不交半成品）
    if _problems:
        return None, _problems

    _hb = _view.get("hook_bindings") or {}
    # ⚠️ `hook_carrier` 的**转换前类型判据不在这里**（复核 2026-10-07 三轮 ②）：
    # 上面的 `view_from_plan(_out, mode="legacy_audio")` 已经按**读盘共用判据**核过
    # （`view_hook_carrier_type`，带原值）——`=5` 那种计划根本走不到这里。
    # 判据只有一处，这里不再手写第二份。
    _hc_raw = _out.get("hook_carrier")
    # 钩子承载声明**也用同一份词表 + 实际卡片表**重解析（旧模式那条路上同样没有卡片表）
    _hook_carriers = []
    for _hd in _split_decls(str(_hc_raw or "")):
        _hr = carrier_ref_from_decl(_hd, beats=_beats_v, dialogue=_units_d,
                                    narration=(), cards=_cards)
        if _hr:
            _hook_carriers.append(_ref_out({k: v for k, v in _hr.items()
                                            if k != "reason"}))
    _pres_block = {
        "schema": PRESENTATION_SCHEMA, "mode": MODE_HYBRID,
        "fact_uids": [str(f.get("uid")) for f in (_view.get("frozen_facts") or [])],
        "beat_uids": _beat_uids,
        "dialogue_uids": _dial_uids,
        "narration_uids": [],
        "screen_cards": _cards,
        "fact_bindings": _bindings,
        "hook_bindings": {
            "spoken_uids": [str(x) for x in (_hb.get("spoken_uids") or [])],
            "carriers": _hook_carriers},
    }
    # ⚠️ **没声明就整块不写**（复核 2026-10-06 第十二轮 ①）：旧写法无条件写
    # `"beat_events": []`，源计划**没有**这个字段时写出来的是一个**长度不符的空表**
    # ——「没有声明过」被悄悄改成了「给了一张不合法的表」。读盘按新口径把后者**拒读**
    # （"表已给出却失配"不重新编号、不按未知兜底），于是两份计划本来只差一个"没写"，
    # 却一份读得出来、一份读不出来。两种状态必须分开：
    #   缺席 ⇒ `event_binding="unknown"`（既有语义，由验收链按"未声明"报出来）；
    #   给了但长度不符 ⇒ 读取准入拒收。
    if _declared:
        _pres_block["beat_events"] = _beat_events
    _out["presentation"] = _pres_block
    _out["plan_contract_version"] = PLAN_CONTRACT_VERSION_HYBRID
    _out.pop("screen_cards", None)          # 内容搬进 presentation（不再留第二份）
    _out.pop("beat_events", None)
    return _out, []

def normalize_plan_for_mode(plan, mode=None):
    """按模式规范化计划（旧模式**原样返回**；新模式走 `normalize_hybrid_plan`）."""
    from shared.presentation_contract import MODE_HYBRID
    if str(mode or presentation_mode()) != MODE_HYBRID:
        return plan, []
    return normalize_hybrid_plan(plan)

def outline_to_anchor(outline: dict | None, novel_text: str = "") -> str:
    """把大纲 dict 转成可注入分镜 prompt 的锚点文本.

    C2 修复：内部先过 _validate_outline——无效大纲（空 main_events/空 climax）
    返回空串，不注入空标签。

    v6.131：`novel_text` 透传给 `_validate_outline`，使**任何**渲染锚点的路径都做同一次
    原文核验（GAP 重生/结构收敛/超限收敛各自渲染时不会再出现未核验的台词）。默认空
    = 已验证过（主流程在候选扇出前已核验并就地收窄），此时不重复核验。
    """
    outline = _pc("_validate_outline")(outline, novel_text)
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

def presentation_mode(explicit: str | None = None) -> str:
    """本次运行的呈现模式（`legacy_audio` / `screen_hybrid`）——**缺省仍是旧模式**。

    模式进计划身份（`presentation.mode` + `plan_contract_version`），下游从**经提交验证
    的稿件**读它，不在 Step03/04 另传开关改稿（主专题 §6.3）。
    """
    from shared.presentation_contract import MODE_LEGACY, MODES
    _v = str(explicit or os.getenv(_pv("PRESENTATION_MODE_ENV")) or "").strip()
    if not _v:
        return MODE_LEGACY
    if _v not in MODES:
        raise ValueError("非法呈现模式 %r（合法：%s）" % (_v, list(MODES)))
    return _v

def outline_prompt_for_mode(mode: str, **fmt) -> str:
    """按模式给出规划提示（新模式追加规则块；旧模式**逐字不变**）."""
    from shared.presentation_contract import MODE_HYBRID
    _t = _pv("OUTLINE_PROMPT").format(**fmt)
    if str(mode) == MODE_HYBRID:
        # 词表**从单一事实源渲染**（不手写第二遍）：提示里教的写法与解析器认的写法
        # 必须是同一份，否则首版会写出解析器不认的声明（2026-10-06 实跑就是这么栽的）。
        from shared.presentation_contract import carrier_words_text
        from step01_script_gen.plan_feasibility import planning_resource_context
        _t = _t + _pv("HYBRID_PROMPT_BLOCK").replace("{carrier_words}", carrier_words_text())
        _t += "\n# 本次有效资源约束（与本地验收同源，不能自报上限）\n"
        _t += json.dumps(planning_resource_context(), ensure_ascii=False)
        _t += "\n# 首版输出类型规则（规则不是示例正文；数组成员须按类型实际填写）\n"
        _t += json.dumps(outline_schema_for_mode(mode)["schema"], ensure_ascii=False)
    return _t

def outline_schema_for_mode(mode: str):
    """按模式给出输出 schema（新模式多一个 `screen_cards`）."""
    from shared.presentation_contract import MODE_HYBRID
    from _text_gen.deepseek_schema import SCREEN_HYBRID_OUTLINE_SCHEMA
    return SCREEN_HYBRID_OUTLINE_SCHEMA if str(mode) == MODE_HYBRID else OUTLINE_SCHEMA
