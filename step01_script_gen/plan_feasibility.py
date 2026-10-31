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

import copy
import json
import re

from step01_script_gen.timecode import (MIN_SHOT_DURATION, _estimate_duration,
                                        active_shot_cap)
from shared import voice_adaptation as _va

# 计划里"可核算"所必需的字段（缺任意一个 → 不可评估 → fail-closed）
_REQUIRED_PLAN_FIELDS = ("dialogue_lines", "shot_beats")

# 原文核验的标点归一化集合（与 `validation._check_grounding` 同族，另含直角引号/书名号）
# ⚠️ **成对收录**（2026-10-10 真实闭环实测）：原先只收 `“”` 与 ASCII `'`，**漏了中文单引号
# `‘’`** ⇒ 模型把原文的 `"噗"` 回成 `'噗'` 时，来源轴逐字门判"非逐字"（其余 51 字逐字相同），
# 真实修订闭环因此卡在来源轴。归一化集是**共用判据**（来源轴 span 门 + 展示侧落点定位），
# 引号风格差异不应改变"逐字"结论。
_PUNCT_CHARS = '，。！？；：、—,!?:;“”"\'‘’（）()「」『』《》〈〉'


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


class DialogueIncompleteError(Exception):
    """候选层**台词完整性硬门**：计划选用的台词在稿件里破损（截断／跨镜句中拆散）.

    2026-09-26（工作包 sunny-kindling-wilkinson）：09-26 02:30 实跑 BLOCK 的根因是
    「选材台词被拆镜截断 → 审计报 `truncated_text` → 唯一正确修复字段是
    `narration_segment`（锁定字段）→ 绑定闸门 `locked_changed` 拒收 → 零改善即
    BLOCK」——缺陷的 curing 点放错了层。本门把台词完整性提前到候选层：破损稿
    **在被送进 BEST-GUARD／提交门之前**就被判不合格。

    **刻意不继承 `RuntimeError`**（与 `PlanInfeasibleError` 同一哲学）：这不是
    "生成失败→按重试语义吞掉"的故障——它带**内容结论**（哪些台词破损），调用方
    必须把它与 LLM 调用失败区分报告。
    """

    #: 供调用方/测试识别"这是台词完整性结论"
    is_dialogue_verdict = True

    def __init__(self, message: str, broken_batches=None):
        super().__init__(message)
        #: 每个破损候选的 broken 明细（list[list[dict]]），供诊断与日志
        self.broken_batches = list(broken_batches or [])


class DialogueGateError(DialogueIncompleteError):
    """台词完整性门**自身异常**（检查故障），不是「稿件台词破损」的内容结论.

    v6.135 五审（2026-09-26）：纯门故障（如净化器 `sanitize_narration` 抛错）不得
    顶着 `DialogueIncompleteError` 的类名把检查故障归为稿件不完整——顶层结论必须
    随实际失败类型区分（诊断 conclusion=dialogue_gate_error，见
    `dump_dialogue_incomplete`）。继承父类：既有 `except DialogueIncompleteError`
    与 `is_dialogue_verdict` 识别不受影响；混合失败（破损+门异常）仍抛父类，
    各类计数留在 attempt_stats。
    """


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


#: 计划里的**编号旁白条目**字段（2026-09-30 复核第 6 轮裁决：旁白是第三种承载方式）.
#: 条目形如 `{"text": 原文叙述原句, "source": 同一句（出处）, "fact_refs": [F序号…]}`，
#: 序号即 `N1..Nn`；承载写成 `B2/旁白1`。**不把叙述伪装成人物对白**：旁白条目单独成表，
#: 与 `dialogue_lines`（人物说出口的话）互不混用。
NARRATION_LINES_FIELD = "narration_lines"


def plan_narration_entries(plan) -> list:
    """计划里的编号旁白条目（非对象/空文本一律丢弃，不做补全）.

    与 `plan_dialogue_lines` 同口径：异常输出当"没有可用旁白"处理，缺什么由判定层
    fail-closed 停止，不在这里补内容。
    """
    from shared.narration_voice import role_fields
    values = (plan or {}).get(NARRATION_LINES_FIELD)
    if not isinstance(values, list):
        return []
    out: list = []
    for x in values:
        if not isinstance(x, dict):
            continue
        _t = str(x.get("text") or "").strip()
        if not _t:
            continue
        out.append({"text": _t,
                    "source": str(x.get("source") or "").strip(),
                    "fact_refs": _strict_fact_refs(x.get("fact_refs")), **role_fields(x)})
    return out


def narration_seconds(entries) -> float:
    """旁白的发声总时长——与对白**共用**同一估算器（复核第 6 轮第 2 项）.

    旁白与画面**同时发生**（不额外加镜头），但它的发声时间必须进预算：否则模型写的
    旁白在成片里会被视频时长截掉。
    """
    _items = entries if isinstance(entries, list) else plan_narration_entries(entries)
    return round(sum(_estimate_duration(str(e.get("text") or "")) for e in _items), 2)


#: 声音条目的两种来源（计划里**要被人念出来**的内容）：人物台词 Dk 与图外旁白 Nk。
#:
#: 2026-09-30 复核第 7 轮：「先统一『声音条目 → 镜头或连续片段』的表示，让计划、
#: 编号变换、预算、生成验收和音轨消费共用」。此前每层各留一套假设——预算按两张表
#: 各算各的、生成验收一层按 `plan_refs.lines` 核台词、另一层按节拍载体核旁白、
#: 音轨消费只认 `shots[].narration_segment`——同一条声音在不同层有不同身份，
#: 于是出现「计划判可行（可拆两段）、终检却要求整条落在某一个镜头里」。
VOICE_KIND_DIALOGUE = "dialogue"
VOICE_KIND_NARRATION = "narration"


def _strict_fact_refs(raw) -> list:
    """旁白 `fact_refs` 的**严格**读数：只认真正的整数（`bool`/浮点/字符串一律不收）.

    2026-09-30 复核第 7 轮 P2：「浮点数和布尔值还会被转成整数引用」——`int(True)==1`、
    `int(2.0)==2`、`int("2")==2` 会把**写错类型**静默变成「引用了另一条事实」。
    类型非法由 `_narration_field_problems` 显式报错；这里只保证**不伪造引用**。
    """
    if not isinstance(raw, list):
        return []
    return [int(x) for x in raw
            if isinstance(x, int) and not isinstance(x, bool)]


def plan_voice_entries(plan, *, novel_text: str = "") -> list:
    """计划里的**声音条目**（对白 Dk + 图外旁白 Nk）——**统一表示，单源**.

    返回 `[{kind, index, id, text, source, fact_refs, beats, speaker?}]`
    （对白在前，各按编号升序）：

    - `kind`：`"dialogue"` / `"narration"`；`index` 从 1 起（`id` = `D1` / `N1`）；
    - `text`：**实际播出文本**——无改编时是逐字原文；有 `voice_adaptations` 时是
      已批准改编句（基底仍保存在计划数组里，只用于出处检查）；
    - `source` / `fact_refs`：只有旁白有（对白恒为 `""` / `[]`）；
    - `beats`：**承载它的节拍号**（升序；空表 = 无人承载，由承载关系门报）。

    改编块存在但未开启/批准失效/条目不一致时抛 `AdaptationInvalidError`，不静默退回基底。
    """
    if not isinstance(plan, dict):
        return []
    if not plan.get("voice_adaptations"):
        # 无改编：完全沿用原有读取/规范化行为（空白、类型、fact_refs 不动）。
        _dlg = plan_dialogue_lines(plan)
        _narr_base = plan_narration_entries(plan)
        _narr_bc = _narr_base
    else:
        _dlg = _va.broadcast_dialogue_lines(plan, novel_text)
        _narr_base = plan_narration_entries(plan)
        _narr_bc = _va.broadcast_narration_entries(plan, novel_text)
    # 对白条目的**说话人**来自已批准改编（`voice_adaptations.entries[].speaker`）。
    # 2026-10-31 复核：条目此前不带 speaker，于是消费侧只能靠文本前缀或 main_subject
    # 猜——而改编句拆镜后前缀会丢。这里只搬运**已批准条目**里的 speaker（上面
    # broadcast_dialogue_lines 已经校验过改编块，未批准/失效会先抛错）。
    _spk: dict = {}
    if isinstance(plan, dict) and plan.get("voice_adaptations"):
        for _e in (plan["voice_adaptations"].get("entries") or []):
            if str(_e.get("kind") or "") != VOICE_KIND_DIALOGUE:
                continue
            _idx, _name = _e.get("index"), str(_e.get("speaker") or "").strip()
            if isinstance(_idx, int) and _name:
                _spk[_idx] = _name
    entries: list = []
    for _i, _t in enumerate(_dlg, 1):
        entries.append({"kind": VOICE_KIND_DIALOGUE, "index": _i, "id": "D%d" % _i,
                        "text": _t, "source": "", "fact_refs": [], "beats": [],
                        **({"speaker": _spk[_i]} if _i in _spk else {})})
    for _i, _e in enumerate(_narr_base, 1):
        _bc = _narr_bc[_i - 1] if _i <= len(_narr_bc) else _e
        entries.append({"kind": VOICE_KIND_NARRATION, "index": _i, "id": "N%d" % _i,
                        "text": str(_bc.get("text") or _e["text"]),
                        "source": _e["source"],
                        "fact_refs": list(_e.get("fact_refs") or ()), "beats": [],
                        **({"voice_role": _e["voice_role"]} if "voice_role" in _e else {})})
    _index = {(e["kind"], e["index"]): e for e in entries}
    for _b, _bt in enumerate((plan.get("shot_beats") or []), 1):
        _kind, _k = BeatRecord.parse(str(_bt)).carrier()
        _ent = _index.get((_kind, _k)) if _k else None
        if _ent is not None:
            _ent["beats"].append(_b)
    return entries


def broadcast_narration_entries(plan, novel_text: str = "") -> list:
    """旁白**播出文本**；无改编时直接返回原有规范化结果."""
    if not isinstance(plan, dict) or not plan.get("voice_adaptations"):
        return plan_narration_entries(plan)
    return _va.broadcast_narration_entries(plan, novel_text)


def broadcast_dialogue_lines(plan, novel_text: str = "") -> list:
    """对白**播出文本**；无改编时直接返回原有规范化结果."""
    if not isinstance(plan, dict) or not plan.get("voice_adaptations"):
        return plan_dialogue_lines(plan)
    return _va.broadcast_dialogue_lines(plan, novel_text)


def approved_voice_texts(plan, *, novel_text: str = "") -> list:
    """计划**批准播出**的全部声音文本（旁白 Nk + 对白 Dk，含已批准改编）.

    用途：后处理（finalize 的确定性约束）必须能区分「模型自写文本」与「计划批准
    文本」——后者是播出合同，不得被省略号归一、说话人回填等启发式改写。
    缺席计划返回空表；不可消费的计划向外报告错误，不静默降级。
    """
    if not isinstance(plan, dict):
        return []
    entries = plan_voice_entries(plan, novel_text=novel_text)
    out: list = []
    for e in entries:
        t = str(e.get("text") or "").strip()
        if t:
            out.append(t)
    return out



def approved_voice_fragments(plan, *, novel_text: str = "") -> tuple[str, ...]:
    """Full approved entries and contiguous sentence spans; never rewrite text.

    This is a preservation guard, not proof of coverage, speaker identity or timing.
    Failure to read an adaptation propagates to the caller instead of enabling
    heuristic rewriting on an unreadable plan.
    """
    result = set()
    for text in approved_voice_texts(plan, novel_text=novel_text):
        boundaries = [0] + [i + 1 for i, ch in enumerate(text)
                            if ch in _SENT_END_CHARS
                            and (i + 1 == len(text) or text[i + 1] not in _SENT_END_CHARS)]
        if boundaries[-1] != len(text):
            boundaries.append(len(text))
        for n, start in enumerate(boundaries[:-1]):
            for end in boundaries[n + 1:]:
                fragment = text[start:end].strip()
                if fragment:
                    result.add(fragment)
    return tuple(sorted(result))


def is_approved_voice_fragment(text, fragments, characters=None) -> bool:
    """Exact match, optionally removing a recognized speaker prefix."""
    from shared.dialogue import strip_speaker_prefix
    if not isinstance(text, str) or not text:
        return False
    return text in fragments or strip_speaker_prefix(text, characters) in fragments


def voice_text_key(text) -> str:
    """声音文本的**保真比对键**：省略号折叠 + 删标点/空白，其余逐字保留.

    只用于判断「这段是不是某条批准文本」；返回键相等不代表可以改写原文本——
    冻结的是原字符串，本函数不产生播出文本。
    """
    return _source_norm(text)


def voice_seconds(entries, kind=None) -> float:
    """声音条目的发声时长合计（对白与旁白**共用同一估算器**，可按种类分账）."""
    _items = entries if isinstance(entries, list) else plan_voice_entries(entries)
    return round(sum(_estimate_duration(str(e.get("text") or ""))
                     for e in _items
                     if kind is None or e.get("kind") == kind), 2)


def voice_units(plan, shots) -> list:
    """声音条目 → **镜头或连续片段**（成片里的实际落点）——生成验收与音轨消费共用.

    每条返回 `{kind, index, id, text, source, fact_refs, start_shot, end_shot,
    shot_indexes, n_shots}`：

    - **起点** = 声明承载它的镜头里**第一个有文本的**那一镜（对白还可由
      `plan_refs.lines` 直接声明，与节拍承载取并集）；前导空镜（建立镜）合法——
      与台词侧「锚在真的说这句的那一镜」同口径 ⇒ 它是 `start_shot` 而不是
      `declared_shot`（后者是**完整镜头坐标**里最早声明承载节拍的那一镜，可能为空镜）；
    - `shot_indexes` = 从起点起**连续**消费完这条声音文本的镜头序号序列（判据与
      `_line_span_in_units` 同源：单镜整句包含，或起点对齐 + 跨镜连续分解），
      **且这些镜头在完整镜头列表里也必须连续**——中间夹一个没有文本的镜头即
      「空镜中断承载」，落点作废（复核第 8 轮：只在单元序列里连续会放过
      「第 2、4 镜」这种被切断的形态）；
      一条旁白因此可以**跨镜连续播放**，一条台词也可以按句末切分跨镜讲完——
      这正是复核第 7 轮 P1 的缺口：以前旁白在这一层要求「整条落在某一个镜头里」，
      而计划层的可表示性判据允许按句末切分，两份假设不一致；
    - 拼不出连续片段时 `shot_indexes` 为空表（调用方各自按自己的判据报错：
      生成验收报 `plan_refs_*`，音轨消费按「缺音轨必须失败」处理）。
    """
    entries = plan_voice_entries(plan)
    if not entries:
        return []
    _shots = [s for s in (shots or []) if isinstance(s, dict)]
    # **两套坐标并存，不许混用**（复核第 8 轮 P1）：
    #   镜头坐标 `_shot_pos`：镜头序号 → 在完整镜头列表里的位置（判「连续片段」用）；
    #   单元坐标 `_unit_pos`：镜头序号 → 在**文本单元序列**里的下标（喂匹配器用）。
    # 旧实现把镜头坐标直接当单元坐标喂给匹配器：前面有空镜时起点整体偏后 ⇒ 整条旁白
    # 被误报缺失；中间有空镜时「2、4 镜」在单元序列里相邻 ⇒ 被判成连续。
    _shot_pos = {str(s.get("index")): i for i, s in enumerate(_shots)}
    units_raw: list = []
    _unit_shot: list = []
    _unit_pos: dict = {}
    for _s in _shots:
        _n = str(_s.get("narration_segment") or "").strip()
        if _n:
            _unit_pos[str(_s.get("index"))] = len(units_raw)
            _unit_shot.append(str(_s.get("index")))
            units_raw.append(_n)
    _beats = [str(b) for b in ((plan or {}).get("shot_beats") or [])]
    _decl: dict = {}
    for s in _shots:
        sid = str(s.get("index"))
        r = s.get("plan_refs") if isinstance(s.get("plan_refs"), dict) else {}

        def _mark(_kind, _k):
            _lst = _decl.setdefault((_kind, _k), [])
            if sid not in _lst:
                _lst.append(sid)

        for _raw in (r.get("beats") or []):
            _b = coerce_ref_index(_raw, "B")
            if not _b or not 1 <= _b <= len(_beats):
                continue
            _kind, _k = BeatRecord.parse(_beats[_b - 1]).carrier()
            if _k:
                _mark(_kind, _k)
        for _raw in (r.get("lines") or []):
            _d = coerce_ref_index(_raw, "D")
            if _d:
                _mark(VOICE_KIND_DIALOGUE, _d)
    out: list = []
    for e in entries:
        _sids = _decl.get((e["kind"], e["index"])) or []
        _first = _sids[0] if _sids else None
        _span: list = []
        # 起点：承载镜头里**第一个有文本的**那一个（前导空镜是合法的「建立镜」，
        # 与台词侧「锚在真的说出这句的那一镜」同口径）——坐标在这里从镜头坐标
        # 换成单元坐标。
        _u0 = next((_unit_pos[_s] for _s in _sids if _s in _unit_pos), None)
        if _u0 is not None:
            _hit, _join, _units = _line_span_in_units(e["text"], units_raw, _u0)
            if _hit:
                _cand = [_unit_shot[_u] for _u in _units
                         if 0 <= _u < len(_unit_shot)]
                # **连续片段按镜头坐标连续**：中间夹一个没有文本的镜头 = 声音被切断
                # （空镜中断承载）。只在单元序列里连续是不够的——那正是反例 2 的假绿。
                if _cand and all(_shot_pos[_cand[_i + 1]] == _shot_pos[_cand[_i]] + 1
                                 for _i in range(len(_cand) - 1)):
                    _span = _cand
        out.append({"kind": e["kind"], "index": e["index"], "id": e["id"],
                    "text": e["text"], "source": e["source"],
                    "fact_refs": list(e["fact_refs"]),
                    **({"voice_role": e["voice_role"]} if "voice_role" in e else {}),
                    # 最早声明承载它的那条节拍的镜头（None = 没有任何镜头声明：
                    # 「无人承载」由覆盖门单独报，与「声明了但文本拼不出来」是两种结论）
                    "declared_shot": _first,
                    "start_shot": _span[0] if _span else None,
                    "end_shot": _span[-1] if _span else None,
                    "shot_indexes": _span, "n_shots": len(_span),
                    "est_sec": round(_estimate_duration(e["text"]), 2)})
    return out


#: 省略号折叠（`_source_norm` 与切片映射必须**同一条**正则）
_ELLIPSIS_RE = re.compile(r"\.{2,}|…{1,}")

#: 分片后要剥掉的**边缘排版符号**（引号/括号/空白）——它们不该进合成文本；
#: 句末标点（。！？…）**保留**：那是台词的一部分。
_EDGE_QUOTES = "“”‘’\"'「」『』（）()《》〈〉 	　"


def _norm_index_map(text) -> tuple:
    """`(归一化文本, [归一化下标] → 原文字符下标)`——按归一化位置切原文用.

    与 `_source_norm` **同一套变换、同一顺序**（先折省略号、再删标点与空白），
    否则切片会与核验口径错位。`_source_norm` 只删字不改序，所以每个归一化字符都能
    对回一个原文字符（省略号折叠取整段的首字符）。
    """
    s = str(text or "")
    out: list = []
    idx: list = []
    i, n = 0, len(s)
    while i < n:
        m = _ELLIPSIS_RE.match(s, i)
        if m:
            out.append("…")
            idx.append(i)
            i = m.end()
            continue
        ch = s[i]
        if ch not in _PUNCT_CHARS and ch not in (" ", "　", "\n"):
            out.append(ch)
            idx.append(i)
        i += 1
    return "".join(out), idx


def shot_voice_entries(shot, plan) -> list:
    """该镜**声明承载**的声音条目（对白在前、各自按序号升序）——分流的唯一依据.

    对白：该镜 `plan_refs.lines` 直接声明的 ∪ 它声明的节拍所承载的；
    旁白：由节拍承载（旁白没有 `plan_refs.lines` 一类的直接声明）。
    """
    if not isinstance(shot, dict):
        return []
    entries = plan_voice_entries(plan)
    if not entries:
        return []
    _index = {(e["kind"], e["index"]): e for e in entries}
    _beats = [str(b) for b in ((plan or {}).get("shot_beats") or [])]
    r = shot.get("plan_refs") if isinstance(shot.get("plan_refs"), dict) else {}
    picked: list = []
    _seen: set = set()

    def _take(_kind, _k):
        _e = _index.get((_kind, _k))
        if _e is not None and (_kind, _k) not in _seen:
            _seen.add((_kind, _k))
            picked.append(_e)

    for _raw in (r.get("lines") or []):
        _d = coerce_ref_index(_raw, "D")
        if _d:
            _take(VOICE_KIND_DIALOGUE, _d)
    for _raw in (r.get("beats") or []):
        _b = coerce_ref_index(_raw, "B")
        if not _b or not 1 <= _b <= len(_beats):
            continue
        _kind, _k = BeatRecord.parse(_beats[_b - 1]).carrier()
        if _k:
            _take(_kind, _k)
    _order = {VOICE_KIND_DIALOGUE: 0, VOICE_KIND_NARRATION: 1}
    return sorted(picked, key=lambda e: (_order.get(e["kind"], 9), e["index"]))


def shot_dialogue_entry(shot, plan):
    """该镜**对白片段**的结构化承载条目（唯一一条才返回，否则 None）.

    判据与 `shot_voice_parts` 同源（`shot_voice_entries`），所以不会"在
    `voice_adaptations` 里碰巧找到一个 speaker"——条目必须是**本镜声明**的。
    多于一条对白条目时返回 None：多人对白不能被整体套成一个声线。

    2026-10-31 复核：说话人解析要用这个，而不是 `main_subject` 的长度启发式。
    """
    entries = shot_dialogue_entries(shot, plan)
    return entries[0] if len(entries) == 1 else None


def shot_dialogue_entries(shot, plan) -> list:
    """该镜声明的**全部**对白条目（顺序同 `shot_voice_entries`）.

    判定"是否只有一个人说话"要用这张表，而不是 `shot_dialogue_entry` 的 None：
    None 既可能表示"没有对白"，也可能表示"有多条"，两者的处置完全不同。
    """
    return [e for e in shot_voice_entries(shot, plan)
            if str(e.get("kind") or "") == VOICE_KIND_DIALOGUE]


def shot_voice_parts(shot, plan) -> dict:
    """单镜的**声音分流**：把 `narration_segment` 按结构化声音条目分成对白/旁白.

    复核第 8 轮 P1：「串行、异步 TTS 与视频提示消费同一份对白／旁白分流结果，
    不能再把整段 `narration_segment` 当对白文本」。三种消费方（step02 视频提示、
    step03 串行/异步 TTS）都从这里取，判据只有一份。

    返回 `{"dialogue", "narration", "prefix", "entries", "split_ok"}`：

    - `prefix` = 说话人前缀（音色识别要用，**不进**合成文本）；
    - 该镜的每个条目在正文里按**归一化位置**定位并切片（长台词跨镜拆分时只取本镜
      实际消费的那一段，不是整条）；
    - 定位不成序（净化替换 / 跨镜形态异常）⇒ `split_ok=False` 且**不丢内容**
      （整段归给它承载的第一类），交调用方告警；这种形态上游生成验收本来就会拒收。

    计划**没有声音条目**（旧稿/无契约）时两个正文都是空串、`entries` 为空，
    调用方回退历史口径——不静默改变无契约稿的行为。
    """
    out = {"dialogue": "", "narration": "", "prefix": "", "entries": [],
           "split_ok": True}
    seg = str((shot or {}).get("narration_segment") or "").strip()
    if not seg:
        return out
    entries = shot_voice_entries(shot, plan)
    if not entries:
        return out
    _pfx, body = _split_speaker_prefix(seg)
    out["prefix"], out["entries"] = _pfx, entries
    norm_body, idx = _norm_index_map(body)
    found: list = []          # [(起始归一化下标, 结束, 条目)]
    missing: list = []
    for e in entries:
        _ne = _source_norm(str(e.get("text") or ""))
        _p = norm_body.find(_ne) if _ne else -1
        if _p >= 0:
            found.append((_p, _p + len(_ne), e))
        else:
            missing.append(e)

    def _raw(_a, _b):
        """按归一化区间切原文（切到**原始边界**，句末标点不丢；两端排版符号剥掉）."""
        _s = idx[_a] if _a < len(idx) else len(body)
        _t = idx[_b] if _b < len(idx) else len(body)
        return body[_s:_t].strip(_EDGE_QUOTES), _t

    if not missing:
        # **按正文里的实际位置**排序与核验——**不预设「对白在前、旁白在后」**：顺序
        # 假设只属于排版，不属于切片。旧实现按 kind 排序后要求位置非降，旁白句写在
        # 对白句之前（合法写法）就会判 `split_ok=False`，随后整段归给对白 ⇒ 同一条
        # 旁白被对白轨与旁白轨各合成一次、合并时叠着播（复核第 8 轮确认的真缺陷）。
        found.sort(key=lambda x: x[0])
        _cnorm = 0                    # 游标在**归一化坐标**里走（`_a`/`_b` 都是归一化下标）
        _ok = True
        for _a, _b, e in found:
            if _a < _cnorm:           # 重叠（例如旁白正文包含对白句）：分不干净，走降级
                _ok = False
                break
            _piece, _ = _raw(_a, _b)
            out[e["kind"]] += _piece
            _cnorm = _b
        if _ok:
            return out
        found = []
        missing = list(entries)

    if found and len({e["kind"] for e in missing}) == 1:
        # 有定位不到的条目：用**已知切片之间的空缺**回填给那一类——绝不把整段丢给对白
        # （那正是「旁白被对白轨念一遍、旁白轨再念一遍」的来源）。
        _mk = missing[0]["kind"]
        _known = sorted(found, key=lambda x: x[0])
        _cnorm = 0                    # 同上：归一化坐标
        _ok = True
        for _a, _b, e in _known:
            if _a < _cnorm:
                _ok = False
                break
            if _a > _cnorm:
                _gap, _ = _raw(_cnorm, _a)
                out[_mk] += _gap
            _piece, _ = _raw(_a, _b)
            out[e["kind"]] += _piece
            _cnorm = _b
        if _ok:
            if _cnorm < len(idx):
                _tail, _ = _raw(_cnorm, len(idx))
                out[_mk] += _tail
            return out

    # 两道都失败（条目文本在本镜文本里一个都定位不到 / 互相重叠）：降级 + 显式标记。
    # 调用方必须看得见——`gen_narration_tts` 会**拒绝**这种镜头的旁白，宁可不生成，
    # 也不产出一条会被对白轨重复念的音轨。
    out["split_ok"] = False
    if any(e["kind"] == VOICE_KIND_DIALOGUE for e in entries):
        out["dialogue"] = body
    else:
        out["narration"] = body
    return out


def shot_needs_dialogue_track(shot, plan) -> bool:
    """该镜**应该**有一条对白 TTS 轨吗——与生成器（`gen_shot_tts`）**同一判据**.

    复核第 8 轮视角扫描实测：收尾核对与缺轨检查若仍用标点启发式 `has_dialogue`，那么
    「纯旁白镜、正文里带引号」会让两道检查都判「缺对白轨」而整章失败——而生成器按
    结构化分流根本不会为它产出音轨。判据必须只有一份。
    """
    parts = shot_voice_parts(shot, plan)
    if parts["entries"]:
        return bool(parts["dialogue"].strip())
    from shared.dialogue import has_dialogue
    return has_dialogue(str((shot or {}).get("narration_segment", "")).strip())


def layout_voice_entries(units, windows_by_shot, durations=None,
                         occupied=None) -> tuple:
    """声音条目 → **最终时间轴上的播放区间**（顺序、重叠、可用时长一并核）.

    复核第 8 轮 P1：「两条顺序旁白在音轨落点表中都得到 start=5.0，随后会同时播放」
    ——「在哪些镜头」解决不了「镜内何时开始、按什么顺序」。排版规则（确定性、单源）：

    - **顺序** = 对白在前、各自按序号升序（D1..Dn，N1..Nn）；同一镜内的多条声音按此
      顺序**依次**播放，前一条把**它覆盖的每个镜头**的可用起点都往后推 ⇒ 不重叠；
    - 每条的可用时长 = 从它的起点到**承载片段末镜**的窗口终点；排不进 ⇒ 报
      「可用时长不足」，**不截句**、不静默重叠。

    `windows_by_shot` = `{镜头序号: (start, end)}`：step04 用真实 `clip_windows`；
    step03 用计划 `duration_sec` 累加出的窗口——两处**同一份排版规则**。
    `durations` = `{声音 id: 秒}`：**手上已有真实音轨时（step04）必须传它**——用
    `_estimate_duration(text)` 的估时（±20%）排出来的时间轴，同镜两条旁白仍可能小幅重叠。
    `occupied` = `{声音 id: [(start, end), …]}`：**已经由调用方确定的播放区间**（对白轨
    从每个承载镜的窗口起点开始播）——直接登记为已占、不由排版决定其起点。这样「排版用的
    占用区间」与「实际播放的音轨」**逐条对应**：一条对白跨 N 镜就有 N 条物理音轨，
    每条计一次（2026-10-01 复核：只算第一条会让旁白提前进入后面的对白区间）。
    返回 `(tracks, problems)`；`tracks` 每项含 `start/end/dur/order_in_shot`。
    """
    tracks: list = []
    problems: list = []
    # **全局已占区间**（不是「按镜头记游标」）：前一条可能**跨镜**播放，序号更小、
    # 起点更早的后一条若只看自己起点那个镜头的游标，就会排进已被占用的时间段——
    # 同镜与跨镜必须一视同仁（视角扫描实测：N2 跨 1-2 镜时 N1 仍排在区间内，重叠
    # 1.00s 而 problems 为空）。
    _busy: list = []
    _order = {VOICE_KIND_DIALOGUE: 0, VOICE_KIND_NARRATION: 1}
    _units = sorted(units or [],
                    key=lambda x: (_order.get(x.get("kind"), 9), x.get("index") or 0))
    for u in _units:
        # ① 已确定的播放区间（对白轨）先全部登记为已占——排版顺序保证对白在前
        _fixed = list((occupied or {}).get(u.get("id")) or [])
        if _fixed:
            for _iv in _fixed:
                _busy.append((float(_iv[0]), float(_iv[1])))
            _busy.sort()
            continue
        run = list(u.get("shot_indexes") or [])
        if not run:
            continue
        w0 = windows_by_shot.get(run[0])
        w1 = windows_by_shot.get(run[-1]) or w0
        if not w0:
            continue
        # 时长必须先算：推起点要用**本条**的时长判断区间是否重叠（用上一轮的残留值
        # 会让「从 0 开始的 3 秒旁白」压进已被占的 [2.0,3.2] 而不报错）。
        dur = float(((durations or {}).get(u.get("id"))) or u.get("est_sec") or 0)
        start = float(w0[0])
        _pushed = True
        while _pushed:                 # 推到不与任何已排布区间重叠为止
            _pushed = False
            for _s0, _e0 in _busy:
                # **区间重叠**（不是「起点落进区间」）：跨镜旁白占着 [2.0,3.2] 时，
                # 另一条从 0 开始的 3 秒旁白同样与它重叠——只看起点会漏掉这种形态。
                if start < _e0 and start + dur > _s0:
                    start = _e0
                    _pushed = True
        # 该条之前有几条**从同一镜开始**（排版顺序的可读读数）
        _order_in_shot = sum(1 for t in tracks if t["shot_indexes"][0] == run[0])
        avail = float(w1[1]) - start
        if dur and avail + 1e-6 < dur:
            problems.append(
                "%s「%s…」排不进承载片段：起点 %.2fs、可用 %.2fs < 需要 %.2fs"
                "（同一镜内排在它前面的声音占掉了时间）——请减少内容或调整镜头，不截句"
                % (u.get("id"), str(u.get("text"))[:16], start, avail, dur))
        tracks.append({"kind": u.get("kind"), "index": u.get("index"), "id": u.get("id"),
                       "text": u.get("text"), "source": u.get("source"),
                       "shot_indexes": run, "order_in_shot": _order_in_shot,
                       "start": round(start, 3), "end": round(start + dur, 3),
                       "dur": dur})
        _busy.append((start, start + dur))
        _busy.sort()
    return tracks, problems


def beats_floor_basis(plan: dict | None) -> dict:
    """节拍的**内容读数**（按承载方式分账）——⚠️ **不构成时间下界**（第 16 轮复核）.

    分账口径（与事件顺序判据同一份承载语义）：
      - `voice`：`载体：台词k／旁白k` 的节拍，与那段声音**同时发生**，时间已计入
        发声预算（`dialogue_sec` / `narration_sec`）；
      - `visual`：`载体：画面` 的节拍；
      - `unlabeled`：没标 / 非法载体的节拍。

    **为什么不乘 `MIN_SHOT_DURATION` 当画面下界**（2026-10-01 复核第 16 轮，反例：
    9 镜 × 10 秒、每镜 4 个画面节拍 + 1 个对白节拍的稿事件契约通过，却被判"至少
    108 秒"）：生成契约明写「**节拍数 ≠ 镜头数**：每个节拍通常展开为 2-4 个镜头……
    **镜数由时长预算决定，不由节拍数决定**」（`prompts.render_execution_contract`），
    `timecode` 的镜头时长也由**文本与 motion_desc** 推出（`_duration_for_text`）——
    全链路没有一处按"节拍"分配时间。所以"节拍数 × 3 秒"既不是下界也不是上界，它是把
    **表达方式**（模型写了几个节拍）当成了"独占几个时间段"。一条节拍到底独占多少时间，
    只能看它**实际被哪些镜承载**（在生成稿里），计划层无从判断——所以计划层的下界只
    取**发声预算**那一条有真实估算器的量。
    """
    visual = voice = unlabeled = 0
    for _b in ((plan or {}).get("shot_beats") or []):
        if not str(_b or "").strip():
            continue
        _kind, _k = BeatRecord.parse(str(_b)).carrier()
        if _kind in ("dialogue", "narration"):
            voice += 1
        elif _kind == "visual":
            visual += 1
        else:
            unlabeled += 1
    return {"visual": visual, "voice": voice, "unlabeled": unlabeled,
            "beats": visual + voice + unlabeled}


# ---------------------------------------------------------------------------
# 原文核验：选用的台词是不是**逐字**来自原文
# ---------------------------------------------------------------------------
def _source_norm(text) -> str:
    """原文核验归一化：省略号折叠 → 删标点 → 删空白.

    **顺序不可换**——先把 `…/……/..` 折叠成占位符 `§`（普通字符，不会被标点归一化删掉），
    再删标点，最后把 `§` 还原成单字符省略号。反过来的话 `…` 会在删标点阶段就没了
    （与 `validation._check_grounding` 08-17 修掉的那个 bug 同源，这里按同一顺序）。
    """
    s = _ELLIPSIS_RE.sub("§", str(text or ""))
    for p in _PUNCT_CHARS:
        s = s.replace(p, "")
    # 2026-10-28（M07）：换行也必须归一。原文按字节读进来是 CRLF，而计划里的
    # adaptation.source 是 LF（或反之）——只删 `\n` 会把 `\r` 留下，同一句原文
    # 于是"逐字可查"变成"查不到"，改编块被判不可消费（实测：同一份 novel 用
    # `read_text()` 与 `decode("utf-8-sig")` 读入，两个 finalize 结果差 245 字）。
    return (s.replace("§", "…").replace(" ", "").replace("　", "")
            .replace("\r", "").replace("\n", ""))


def source_contains(novel_text: str, line: str) -> bool:
    """归一化后，这一句能否在原文里**整句**找到."""
    n = _source_norm(line)
    return bool(n) and n in _source_norm(novel_text)


def source_norm_note() -> str:
    """**原文核验归一化**的一句话说明——**从判据本身渲染**，不手写第二份口径.

    2026-10-11 复核 r12 的教训：修订请求里手写的那句写成"省略号不计"，而 `_source_norm`
    把省略号**折叠成一个 `…` 之后保留**下来、**照样参与比对**——提示承诺了判据不做的事。
    反方向同样危险（说"不计"而实际会拒，模型就去踩一个它以为不存在的坑）。

    这类"提示里再抄一份判据口径"与复核之十二那条"抄一份等价表达式"同源，改法也同一个：
    **口径由判据渲染**。标点集从 `_PUNCT_CHARS` 现取，省略号的处理照 `_source_norm`
    的实际顺序说——判据改了，这句话自己跟着变。
    """
    return ("按本地 `source_contains` 归一化比对：省略号先折叠成一个 `…`"
            "（`…` / `……` / `..` 都算同一个，**折叠后仍参与比对，不可自行增删**），"
            "再删去这些标点：`%s`，最后删去空格、全角空格与换行；其余字符逐字比对。"
            % "".join(_PUNCT_CHARS))


def verify_dialogue_lines_indexed(lines, novel_text: str) -> tuple:
    """逐句核验选用台词并**保留原序号**；返回 `(kept, rejected)`.

    工作包 §2.2：D/N 身份由**生产者在遍历时**给出——`kept` 每项 `{"index": k,
    "line": s}`，`k` 是传入序列里 1 起的**原序号**（重复句子各占各的序号，
    不用 set / 首次文本匹配决定保留哪条）。`rejected` 与旧接口同形。
    以下为原判据说明：

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
    for _i, s in enumerate(lines, 1):
        _forms = [s]
        # v6.138（九审 P1-3）：只有**真正的说话人前缀**（短且无子句标点）才容忍
        # 非逐字——"且慢，我告诉你：你先走。"的冒号前段是台词正文，不得丢掉再核
        _pfx, _body = _split_speaker_prefix(s)
        if _body.strip() and _body != s:
            _forms.append(_body)
        if any(_source_norm(f) and _source_norm(f) in novel_norm for f in _forms):
            kept.append({"index": _i, "line": s})
        else:
            # 复核第 40 轮：**拒收条目也要有身份**——此前只有通过条目带原序号，
            # 下游只能靠文本回找位置（重复句子必然指错）。
            rejected.append({"index": _i, "line": s,
                             "reason": "原文中未逐字找到"
                             if _source_norm(s) else "归一化后为空（只有标点/空白）"})
    return kept, rejected


def verify_dialogue_lines(lines, novel_text: str) -> tuple:
    """兼容转发：`(kept, rejected)`，`kept` 是字符串清单（由带序号的版本渲染）."""
    _kept, rejected = verify_dialogue_lines_indexed(lines, novel_text)
    return [x["line"] for x in _kept], rejected


# ---------------------------------------------------------------------------
# 台词完整性硬门（2026-09-26，工作包 sunny-kindling-wilkinson）
#
# 为什么在候选层：`narration_segment` 是必保留内容（选材契约的锚），提交前修复
# 按设计禁止触碰（v6.94 绑定闸门）。选材台词一旦在生成/拆分层被拆镜截断，审计报
# `truncated_text` 后唯一正确修复就是改旁白 → 死锁（09-26 02:30 实损：
# `locked_changed` → 零改善 BLOCK）。所以完整性必须在候选层把关，而不是靠提交门
# 的审计"发现"。
# ---------------------------------------------------------------------------
# 句末标点：跨镜拼接的拆点必须落在句末——句中碎片正是审计 `truncated_text` 的
# 触发形态（09-26 实损：shot12 "…有些生意上的事情急需" 戛然而止）。
_SENT_END_CHARS = "。！？…；!?~"
_SENT_CLOSERS = "”’」』》〉）)]\"' 　"

# 跨镜拼接判据的*观测*反例（2026-09-26 实测，勿删）：
#   ① "截断+下一镜重复前缀"：shot12="…事情急需"（句中截断）+ shot13="公子爷赶回去处理，…"
#      ——纯整稿拼接能把台词拼回来，但 shot12 句中截断必然被审计报 truncated_text
#      → 死锁。句末完整性约束把它判破损。
#   ② "相邻镜头偶然跨界拼接"：台词恰好跨两镜的尾部+头部拼成——整段相等约束
#      （逐镜全消费/起点对齐）排除这类偶然命中。

#: 说话人前缀头部**不得**包含的标点（v6.138，九审 P1-3 单一事实源）：
#: 子句/句末标点出现在头部 ⇒ 冒号前是台词正文（"且慢，我告诉你"），不是说话人
#: ——说话人名不含这些标点。破折号/引号/括号不在此列（"张十三（低声）道："合法）。
_SPEAKER_PREFIX_FORBIDDEN_PUNCT = "，。！？…；、,;.!?"


def _is_speaker_prefix_head(head: str) -> bool:
    """头部文本是否够格当**说话人前缀**：非空、≤12 字、无子句/句末标点."""
    head = str(head or "").strip()
    return bool(head) and len(head) <= 12 and not any(
        ch in _SPEAKER_PREFIX_FORBIDDEN_PUNCT for ch in head)


def _split_speaker_prefix(text: str) -> tuple:
    """`张十三道：台词` → `("张十三道：", "台词")`；无头部说话人前缀 → `("", text)`.

    与拆分器 `_narration_parts` 同构，但**只认头部短前缀**（说话人名 ≤12 字）——
    台词正文里更深的冒号（"且慢，我告诉你：走开。"）不是前缀，留在正文里，
    否则切分估时会把它错当成每段都要重复的开销。

    v6.138（九审 P1-3）：「短」不再是唯一判据——头部含子句/句末标点（，；…等）
    的一律**不是**说话人前缀。实测反例：核验层曾把 `且慢，我告诉你：你先走。` 的
    冒号前段当前缀丢掉，用 `你先走。` 就通过了原文核验（原文只有后半句也放行）。
    说话人名（张十三/听香/群演）不含这些标点；该判据是 `verify_dialogue_lines` /
    本函数 / `_speaker_body` 的**单一事实源**。
    """
    text = str(text or "")
    m = re.split(r"[:：]", text, maxsplit=1)
    if len(m) == 2 and _is_speaker_prefix_head(m[0]):
        return m[0] + "：", m[1]
    return "", text


def find_legal_split(text: str, cap: float | None = None, *,
                     prefix_allowance_chars: int = 0,
                     max_parts: int = 4) -> list | None:
    """**统一判据求解**（A+B 单源，2026-09-26 用户决策）：按**句末边界**把 text 切成
    ≤`max_parts` 段、每段估时 ≤ `cap`.

    合法切点的唯一定义（规划层可表示性检查、定向拆分器、台词完整性门三方共用）：
    **句末边界** = `_SENT_END_CHARS`（。！？…；）标点串**之后**——连续标点串
    （如 `……`）只在串尾切，不在串中间切（六审 P2）。逗号/顿号切点与等字长兜底
    **一律非法**——句中碎片正是终稿台词门 `cross_shot_split` 判破损、审计报
    "同一句被截成两半"的形态（09-26 三次实跑复现"拆分成功、终稿失败"的直接机制）。

    求解 = **完整动态规划**（六审 P1-3）：本判据直接驱动规划重选与显式停止，
    误报"不可表示"的代价是错误的内容取舍——切点多时不截窗，精确求「最少段数、
    同段数内最长段最短」的解（六审 P2：两段已合法时不选三段）。

    Args:
        text: 台词/旁白原文，可带头部说话人前缀（前缀保留在每段开头）。
        cap: 单镜上限秒数；None 取 `active_shot_cap()`（模型能力注册表）。
        prefix_allowance_chars: 规划层台词还没有说话人前缀，按「每段将补这么长
            前缀」保守估时（生成层规则0前缀 "张十三道：" ≈5 字）。拆分器传 0
            （text 自带真实前缀）。
        max_parts: 段数上限（拆分器与 SPLIT_SHOT_SCHEMA 同为 4）。

    Returns:
        切段列表（各段含前缀），或 None——**text 整体已 ≤ cap（无需切）或不存在
        合法切法**。调用方（拆分器）拿到 None 必须在**调用拆分模型之前**停下，
        不得退回非法切点。
    """
    if cap is None:
        cap = active_shot_cap()
    cap = float(cap)
    text = str(text or "")
    if not text.strip():
        return None
    prefix, body = _split_speaker_prefix(text)
    _pad = "张" * max(0, int(prefix_allowance_chars))

    def _est(part: str) -> float:
        return _estimate_duration(prefix + _pad + part)

    if _est(body) <= cap + 0.01:
        return None  # 整条装得下：无切分需求
    import math
    need = _est(body)
    min_parts = max(2, int(math.ceil(need / cap - 1e-9)))
    if min_parts > max_parts:
        return None
    # 合法切点：句末标点**串**之后（串内位置不切；末尾位置不算切点）。
    # v6.138（九审边界项）：句末串后紧随的**收尾符**（闭引号/括号/空白）一并由
    # 前段消费——否则切点落在 `！` 与 `”` 之间，下一镜以孤立的闭引号开头。
    bounds = []
    _L = len(body)
    for i, ch in enumerate(body):
        if ch not in _SENT_END_CHARS or i + 1 >= _L \
                or body[i + 1] in _SENT_END_CHARS:
            continue
        _p = i + 1
        while _p < _L and body[_p] in _SENT_CLOSERS:
            _p += 1
        if _p < _L:
            bounds.append(_p)
    bounds = sorted(set(bounds))
    if not bounds:
        return None
    # 完整 DP：pts = [0] + bounds + [L]；D[k][j] = body[0:pts[j]] 切成 k 段
    # （段界都在切点上）的最小「最长段」估时。L 必须在 pts 里——末段是
    # body[最后切点:L]，漏掉 L 会把尾巴丢出估时（六审后自测抓到的真 bug）。
    pts = [0] + bounds + [len(body)]
    m = len(pts)
    L = len(body)
    INF = float("inf")
    _est_cache: dict = {}

    def _est_ij(i: int, j: int) -> float:
        key = (i, j)
        if key not in _est_cache:
            _est_cache[key] = _est(body[pts[i]:pts[j]])
        return _est_cache[key]

    rows = []          # rows[k-1] = k 段的 dp 行（下标 j）
    dp_prev = [0.0] + [INF] * (m - 1)   # k=0：仅位置 0 可达，值 0
    choice: dict = {}
    for k in range(1, max_parts + 1):
        dp_cur = [INF] * m
        for j in range(1, m):
            bv, bi = INF, -1
            for i in range(j):
                if dp_prev[i] == INF:
                    continue
                v = _est_ij(i, j)
                if dp_prev[i] > v:
                    v = dp_prev[i]
                if v < bv:
                    bv, bi = v, i
            dp_cur[j] = bv
            if bi >= 0:
                choice[(k, j)] = bi
        rows.append(dp_cur)
        dp_prev = dp_cur
    # 最少段数优先（k ≥ min_parts——更少段数必有一段超 cap），同段数内 DP 已取
    # 最小「最长段」；该 k 无可行解才进入 k+1。
    # 行语义：rows[r-1][j] = D(r, j) = 前 r 段覆盖 body[0:pts[j]]；总段数 k 的解
    # = D(k-1, j) + 末段 body[pts[j]:L] ⇒ 用 rows[k-2]，回溯重建 k-1 个内部切点。
    for k in range(min_parts, max_parts + 1):
        row = rows[k - 2]
        bv, bj = INF, -1
        for j in range(m - 1):              # 最后切点必须是真句末界（末段非空）
            if row[j] == INF:
                continue
            e_last = _est_ij(j, m - 1)      # 末段 body[pts[j]:L]
            v = e_last if e_last > row[j] else row[j]
            if v <= cap + 0.01 and v < bv:
                bv, bj = v, j
        if bj < 0:
            continue
        cuts: list = []
        cur = bj
        for r in range(k - 1, 1, -1):
            cuts.append(pts[cur])
            cur = choice[(r, cur)]
        cuts.append(pts[cur])               # 第一段的段尾
        cuts.reverse()
        parts = []
        prev = 0
        for c in cuts:
            parts.append(body[prev:c])
            prev = c
        parts.append(body[prev:])
        return [prefix + p for p in parts]
    return None

def check_dialogue_representable(line: str, cap: float | None = None, *,
                                 prefix_allowance_chars: int = 5) -> dict:
    """单句台词**可表示性**（A 判据；与拆分器 B 同源，见 `find_legal_split`）.

    判据：整条（含说话人前缀余量）装进单镜，**或**存在句末合法切分（≤4 段、
    每段含前缀余量 ≤ cap）。**不是**"整条 ≤12s"——多句长台词按句末切分可以保留
    （2026-09-26 用户决策：听香 64 字台词按省略号切两段 ≈5.9s/8.9s，保留）。

    Returns:
        `{"representable": bool, "est_sec": float, "split": list|None, "reason": str}`；
        `split` 是合法切段预览（重选反馈里给规划阶段看"怎么切能装下"）。
    """
    if cap is None:
        cap = active_shot_cap()
    cap = float(cap)
    line = str(line or "")
    # 前缀余量只在台词**还没有**说话人前缀时加（自带前缀的台词不再双算）
    _has_prefix, _ = _split_speaker_prefix(line)
    _allow = 0 if _has_prefix else max(0, int(prefix_allowance_chars))
    est = _estimate_duration("张" * _allow + line)
    if est <= cap + 0.01:
        return {"representable": True, "est_sec": round(est, 2), "split": None,
                "reason": ""}
    split = find_legal_split(line, cap, prefix_allowance_chars=_allow)
    if split:
        return {"representable": True, "est_sec": round(est, 2), "split": split,
                "reason": ""}
    n_end = len([i for i, ch in enumerate(line)
                 if ch in _SENT_END_CHARS and 0 < i + 1 < len(line)])
    if n_end == 0:
        reason = ("估时 %.1fs 超单镜上限 %.0fs，句内**无句末边界**（只有逗号/顿号）"
                  "——无法合法切分，须从原文**重选一句**：要有句末标点、一镜说得完" % (est, cap))
    else:
        reason = ("估时 %.1fs 超单镜上限 %.0fs，有 %d 个句末边界但切不出"
                  "每段 ≤ 上限的合法解" % (est, cap, n_end))
    return {"representable": False, "est_sec": round(est, 2), "split": None,
            "reason": reason}


_CLAUSE_BOUNDARY = "，、；："


def representable_replacements(line: str, novel_text: str, *, limit: int = 6) -> list:
    """从该台词的**原文出处**里列出「句末收尾且可表示」的逐字替代（2026-09-26 用户决定）.

    用途：规划重选的反馈里给出**已验算过**的替代集合，让规划层从这个集合里选，
    而不是自由重抽（实测自由重选会把不可表示的句子原样留下）。

    只收**句末标点（。！？…；）收尾**的片段——逗号截断的片段虽然单镜装得下，
    成片里听起来是没说完的半句，用户明确不采用。因此当出处通篇没有句末标点时
    （章 2 冯检校那句正是），返回空列表；反馈块据此改为要求**按原文顺序重排载体**
    （画面动作 + 由别人在原文里的完整短句承载同一事实），而不是硬拆。

    排序：按长度降序（尽量少丢信息）。
    """
    src = str(novel_text or "")
    anchor = _source_norm(line)
    if not src or not anchor:
        return []
    for m in re.finditer(r"[“「]([^”」]{2,})[”」]", src):
        quote = m.group(1)
        if anchor not in _source_norm(quote):
            continue
        return _quote_span_candidates(quote, anchor, limit=limit)
    return []


def _quote_span_candidates(quote: str, exclude_norm: str, *, limit: int,
                           one_shot: bool = False):
    """一条引语里**句末收尾且可表示**的逐字片段（按保留信息量降序）.

    `one_shot=True` 只留**单镜装得下**（无需切分）的片段——跨出处候选要的是
    「替代某一句」的短句，50-110 字那种虽然可表示（能切段），但换过去只是把
    长度问题搬了个家，当候选只会淹没提示。
    """
    out = []
    for k, ch in enumerate(quote):
        if ch not in _SENT_END_CHARS:
            continue
        cand = quote[:k + 1].strip()
        if len(cand) < 6 or _source_norm(cand) == exclude_norm or cand in out:
            continue
        r = check_dialogue_representable(cand)
        if not r["representable"]:
            continue
        if one_shot and r.get("split"):
            continue
        out.append(cand)
    return sorted(out, key=len, reverse=True)[:limit]


def cross_quote_replacements(line: str, novel_text: str, *, window: int = 3,
                             limit: int = 4) -> list:
    """**跨出处**的可表示替代：同一场景里**相邻引语**中句末收尾的逐字短句.

    为什么需要（2026-09-26 实测）：章 2 冯检校那句的出处通篇只有逗号，本函数之外的
    `representable_replacements` 只能返回空表；反馈因此只说「没有候选，请用 beat_ops
    重排载体」，模型于是去**截断本句**（给出逗号半句），被应用器按「不可单镜表示」拒收。
    用户决定的修法是把**相邻引语**里的合法短句也摆到它面前——例如同场景里安员外那句
    「杨旭死了，咱们的差事算是办砸了，现在该怎么办？」，本来就是承载同一事实的合法载体。

    **不引入语义启发式**：只按**原文位置**取被点名台词所在引语前后各 `window` 条引语，
    枚举其中句末收尾且可表示的片段——「同场景相邻发言」是位置事实，不需要判断相关性。
    排序：先按与出处的距离（近者优先），同距离内长者优先（尽量少丢信息）。

    Returns: `[{"text": …, "from": "相邻第N条引语"}, …]`（空表 = 附近也没有可用短句）。
    """
    src = str(novel_text or "")
    anchor_norm = _source_norm(line)
    if not src or not anchor_norm:
        return []
    quotes = list(re.finditer(r"[\u201c「]([^\u201d」]{2,})[\u201d」]", src))
    _hit = [i for i, m in enumerate(quotes) if anchor_norm in _source_norm(m.group(1))]
    if not _hit:
        return []
    _at = _hit[0]
    out: list = []
    seen: set = set()
    for _d in range(1, window + 1):
        for _j in (_at - _d, _at + _d):
            if not 0 <= _j < len(quotes):
                continue
            q = quotes[_j].group(1)
            for cand in _quote_span_candidates(q, anchor_norm, limit=limit,
                                                one_shot=True):
                if cand in seen:
                    continue
                seen.add(cand)
                out.append({"text": cand, "from": "相邻第%d条引语" % _d})
                if len(out) >= limit:
                    return out
    return out


#: **明确非法**的切点字符（句中停顿）：成片里接下去还有话，切在这里就是半句.
#: 重选提示里那句「以「，」「、」收尾的半句一律非法」由本常量**渲染**，
#: 守卫用例再钉住「提示文本 ↔ 判据函数」一致——2026-09-27 复核：此前提示是手写的
#: 字符表，与应用器只是**当时**一致，不算长期保证。
CUT_ILLEGAL_CHARS = "，,、"


def is_sentence_end_terminated(text: str) -> bool:
    """台词是否以**句末标点**收尾（不是逗号/顿号截断的半句）.

    实测模型两次都把长句换成它自己出处的**逗号截断前缀**。这里是**唯一判据函数**：
    应用器 `apply_replan_ops` 直接调用它；重选提示里的非法字符表由
    `CUT_ILLEGAL_CHARS` 渲染。两者一致**不靠人肉同步**——由
    `test_cut_point_rule_in_prompt_matches_the_criterion` 的守卫用例钉住
    （2026-09-27 复核：提示文本仍是手写规则，不宜把「目前一致」当长期保证）。
    """
    _t = str(text or "").strip()
    while _t and _t[-1] in _CLOSERS_TAIL:
        _t = _t[:-1].rstrip()
    return bool(_t) and _t[-1] in _SENT_END_CHARS


_CLOSERS_TAIL = "”’」』》〉）)]" + chr(34) + chr(39) + " 　"


def _speaker_body(text: str) -> str:
    """去说话人前缀（`张十三道：…` → `…`）——生成层规则0补的，不逐字来自计划.

    v6.138（九审 P1-3）：与 `_split_speaker_prefix` 同一判据——头部含子句/句末
    标点的冒号（"且慢，我告诉你：你先走。"）不是前缀，正文原样保留。
    """
    return _split_speaker_prefix(text)[1]


def _ends_sentence(raw: str) -> bool:
    """镜头旁白是否以句末收尾（剥尾随引号/括号/空白后看末字符）."""
    s = str(raw or "").strip()
    while s and s[-1] in _SENT_CLOSERS:
        s = s[:-1].rstrip()
    return bool(s) and s[-1] in _SENT_END_CHARS


def _match_forms(text: str) -> list:
    """台词/旁白匹配形态：**合规净化后 + 原样**两种归一化（2026-09-26 三审 P1-2 重定）.

    为什么匹配方向是 **sanitize（orig→safe）而不是还原（safe→orig）**：还原对
    "原词与安全词同句"本质歧义——计划"拿春药，再去抓药。"整段还原成
    "拿春药，再去抓春药。"，与稿件"拿药，再去抓药。"两个形态都对不上（三审实锤）。
    而 sanitize 正是产出稿件文本的那个确定性函数（`sanitize_narration`，
    长词优先、幂等）：计划句与稿件**都过同一遍 sanitize** 再比对，替换永远不会错位。
    原样形态保留给"两侧都未被净化"的路径；未命中安全词时 sanitize 是 no-op，
    两形态退化为同一形态。

    （`restore_narration_substitutions` 仍归 `_check_grounding` 使用——5 字滑窗容忍
    其"春药+抓药同句"歧义与单字 safe 误还原，修它属另一工作包。）

    **净化异常必须向外传播**（2026-09-26 四审 P1）：这里**禁止** try/except 退回
    "原样匹配"——回退会让"两侧原样恰好相同"的稿子在净化器故障时照样过门，提交层的
    fail-closed 分支收不到任何异常（硬门在自身故障时静默降级 = 门失效）。调用方
    （候选层 `_CandidateGateError`／终稿层／提交层／BEST-GUARD 快照过滤）各自
    fail-closed 接住。

    **判据边界（四审记录，有意取舍）**：本门契约 =「**净化后文本一致**」。净化后
    同形为同一安全词的原词（如 春药/媚药 → 药）**有意不可区分**——正式产物只含
    净化后文本，两个原词在成片里无差别；门保证的是"成片将呈现的台词与计划一致"，
    不是原词逐字一致。
    """
    t = str(text or "")
    from shared.content_safety import sanitize_narration as _sanitize
    variants = (_sanitize(t), t)
    out: list = []
    for v in variants:
        nf = _source_norm(v)
        if nf and nf not in out:
            out.append(nf)
    return out


def _line_forms(line: str) -> list:
    """一条计划台词的全部归一化形态（原文 / 去说话人前缀 × 净化后 / 原样）."""
    out: list = []
    for f in (line, _speaker_body(line)):
        for nf in _match_forms(f):
            if nf and nf not in out:
                out.append(nf)
    return out


def _line_span_in_units(line: str, units_raw: list, start: int | None = None) -> tuple:
    """台词/旁白在镜头旁白序列上的**落点** → `(hit, joinable, span)`.

    **单一实现**：整稿台词门（`start=None`，任一起点）、事件履约契约的按镜核验
    （`start=k`，只许从声明那一镜起算）、以及「声音条目 → 镜头或连续片段」的落点计算
    （`voice_units`，音轨排版与时长核算用它）共用本函数。抽出来是因为 2026-09-27 复核
    指出的假绿——「shot 1 声明 D1、shot 2 才说 D1」两门都能过：整稿门只证明 D1 **在
    某处**，而契约门当时只核编号范围/顺序。修它必须让两门**同一个判据**，复制一份
    必然漂移；2026-09-30 复核第 7 轮又因为「旁白落点」各写一份，出现「计划判可行、
    终检要求整条在一个镜头里」。

    判据（与 v6.135 硬门逐字一致，未放宽也未收紧）：
    - `hit①`：某形态**整句包含**在某个镜头的旁白形态里 → `span` 只有那一镜；
    - `hit②`：跨镜**连续**分解——起点镜旁白从头对齐且句末完整 → 中间镜整镜消费且句末
      完整 → 末镜可只消费前缀 → `span` 是这一段**连续**镜头下标。
    `joinable` 只用于失败分类（`cross_shot_split` vs `not_found`），不参与命中判定；
    未命中时 `span` 为空表。
    """
    unit_forms_full = [_match_forms(u) for u in units_raw]
    unit_forms_body = [_match_forms(_speaker_body(u)) for u in units_raw]
    # 防空：只有标点/空白的镜头旁白归一化后为空 → `_match_forms` 返回空表。
    # 调用方（`voice_units` / 台词门）本来就不该把空镜头塞进来，但门自身不能炸
    # （准则 7：显式失败可以，静默崩不行）。
    body_plain = [(p[-1] if p else "") for p in unit_forms_body]
    body_sanitized = [(p[0] if p else "") for p in unit_forms_body]
    n = len(units_raw)
    _s0 = None if start is None else int(start)
    _starts = list(range(n)) if _s0 is None else [_s0]
    hit = False
    joinable = False
    span: list = []
    # `start` 给定时，命中必须**锚在该镜**：hit① 只看该镜的旁白形态（不能扫全稿——
    # 那正是「shot 1 声明 D1、shot 2 才说 D1」能溜过的路径），hit② 的起点也钉在该镜。
    _hit1_units = (list(range(n)) if _s0 is None
                   else ([_s0] if 0 <= _s0 < n else []))
    for nf in _line_forms(line):
        if hit:
            break
        # ① 单镜包含
        for _u in _hit1_units:
            _forms = unit_forms_full[_u] + unit_forms_body[_u]
            if any(_f and nf in _f for _f in _forms):
                return True, False, [_u]
        # ② 跨镜整段拼接（`start` 限定起点镜）；净化/原样两套连贯形态都试
        for bodies in (body_sanitized, body_plain):
            if hit:
                break
            if nf and nf in "".join(bodies):
                joinable = True
            for i in _starts:
                if not 0 <= i < n:
                    continue
                u0 = bodies[i]
                if not u0 or not nf.startswith(u0):
                    continue
                if not _ends_sentence(_speaker_body(units_raw[i])):
                    continue  # 首镜句中截断 ⇒ 拼接救不回（09-26 实损形态）
                acc = u0
                j = i + 1
                while j < n:
                    cand = acc + bodies[j]
                    if cand == nf:
                        hit = True
                        span = list(range(i, j + 1))
                        break
                    if nf.startswith(cand):
                        if not _ends_sentence(_speaker_body(units_raw[j])):
                            break  # 中间镜句中截断
                        acc = cand
                        j += 1
                        continue
                    if cand.startswith(nf):
                        hit = True  # 台词在末镜前缀内结束（末镜句首对齐）
                        span = list(range(i, j + 1))
                        break
                    break
                if hit:
                    break
            if hit:
                break
    return hit, joinable, span


def _line_hit_in_units(line: str, units_raw: list, start: int | None = None) -> tuple:
    """`_line_span_in_units` 的命中读数（薄包装：既有调用点不改口径）."""
    _hit, _joinable, _span = _line_span_in_units(line, units_raw, start)
    return _hit, _joinable


def _line_offset_in_units(line: str, units_raw: list, start: int) -> tuple:
    """台词**落点读数** → `(hit, 该镜内的起点偏移)`；存在性与顺序**共用同一次匹配**.

    为什么必须共用（第 17 轮复核 P1-①）：存在性走 `_line_hit_in_units`（容忍标点、
    说话人前缀、合规净化对齐），顺序却用生 `str.find` ——**换标点或换说话前缀**，一个门
    说"在"、另一个说"找不到"，于是正文里 D2 先说、D1 后说的**逆序被静默放过**
    （复核实测：原样文字能拦，换标点/前缀则两个门全过）。

    偏移只用于**同一镜内**的相对先后比较（多形态取最小，各形态间的整体位移是同一常数，
    不改相对序）。
    """
    _hit, _ = _line_hit_in_units(line, units_raw, start=start)
    if not _hit:
        return False, -1
    _i0 = int(start)
    if not 0 <= _i0 < len(units_raw):
        return True, -1
    _seg = str(units_raw[_i0])
    _forms = list(_match_forms(_speaker_body(_seg))) + list(_match_forms(_seg))
    _best = -1
    for _lf in _line_forms(line):
        for _u in _forms:
            _p = _u.find(_lf)
            if _p >= 0 and (_best < 0 or _p < _best):
                _best = _p
    return True, _best


def check_script_dialogue_integrity(script: dict, plan: dict | None) -> tuple:
    """**台词完整性硬门**（纯函数、确定性、零模型调用）：计划选用的台词是否逐字保住.

    判据（唯一事实源，候选层/终稿层/提交层共用）：
    - **命中①（单镜包含）**：`norm(台词)` 出现在**单个**镜头的 `narration_segment`
      里（台词侧与镜头侧都做说话人前缀宽容 + **合规净化对齐**——两侧同过
      `sanitize_narration`，替换永不错位；见 `_match_forms`）。
    - **命中②（跨镜整段拼接，审核约束④收口）**：台词跨多镜时，必须是
      「起点对齐（首镜旁白从头开始且句末完整）→ 中间镜整镜消费且句末完整 →
      末镜可只消费前缀（台词在该镜结束）」的**连续**分解——每个参审镜头的旁白
      都以句末收尾。句中碎片（09-26 实损形态）与偶然跨界拼接都不满足 ⇒ 破损。
      **逗号/顿号结尾的分句跨镜同样判破损**（2026-09-26 二审 M2 固化）：句中碎片
      正是审计 `truncated_text` 的触发形态——宁可显式失败，不做"逗号也算句末"的
      宽容。
    - **破损**：任一句未命中 ⇒ 整份候选不合格（宁显式失败，不做部分接受/阈值放行）。
    - `dialogue_lines` 为空 → 不可评估 → fail-closed 判不合格（同 `evaluate_plan`
      口径）。**调用方例外**：提交层对「无台词计划」（历史模式/旧缓存）不启用门——
      无契约可核 ≠ 有契约被破坏（见 `main._commit_step01_outputs`）。

    为什么不需要 `novel_text`：台词对原文的逐字依据已由
    `check_plan_before_generation` 收口（`_drop_unverified` 就地收窄）；本门核对的是
    「稿件是否保住计划选定的台词」——另一个维度。

    Returns:
        `(ok, broken)`；`broken` 每项 `{"line": 前60字, "reason": "cross_shot_split"|
        "not_found"|"extra_dialogue"}`。`reason=cross_shot_split` 表示整稿拼接能拼回但存在句中碎片
        （审计必然报 truncated_text → 提交门死锁的形态）。`extra_dialogue`（v6.138，
        九审 P2-4）= 带说话人前缀的旁白不含任何计划句（稿件另编了计划外对白）。
    """
    try:
        lines = [e["text"] for e in plan_voice_entries(plan)
                 if e["kind"] == VOICE_KIND_DIALOGUE]
    except Exception as exc:
        # 改编块不可消费时不得退回基底：完整性门直接 fail-closed。
        return False, [{"line": "", "reason": "adaptation_gate_error: %s" % exc}]
    if not lines:
        return False, [{"line": "", "reason": "dialogue_lines 为空（不可评估，fail-closed）"}]
    units_raw: list = []
    unit_shots: list = []
    for s in (script or {}).get("shots") or []:
        if isinstance(s, dict):
            _n = str(s.get("narration_segment") or "").strip()
            if _n:
                units_raw.append(_n)
                unit_shots.append(str(s.get("index")))
    # 镜头侧形态：全文/去说话人前缀 × 未还原/已还原（`_match_forms` 每文本给两形态）
    # 排他性检查（下方 extra_dialogue）要按镜取形态；命中判定本身在 `_line_hit_in_units`
    # 内部自算（判据单一实现，避免两处形态口径分叉）
    unit_forms_full = [_match_forms(u) for u in units_raw]
    unit_forms_body = [_match_forms(_speaker_body(u)) for u in units_raw]
    # A declared, fully realized narration span is a separate planned voice,
    # even when the renderer adds a short label before its text. Reuse the
    # same span resolution as event validation and downstream audio consumption.
    narration_forms_by_shot: dict = {}
    for entry in voice_units(plan, (script or {}).get("shots") or []):
        if entry["kind"] != VOICE_KIND_NARRATION:
            continue
        for sid in entry["shot_indexes"]:
            narration_forms_by_shot.setdefault(str(sid), []).extend(_line_forms(entry["text"]))

    broken: list = []
    all_line_forms: list = []
    for line in lines:
        all_line_forms.extend(_line_forms(line))
        # 整稿门：**任一起点**均可；判据本体与事件门的按镜核验同源
        hit, _joinable = _line_hit_in_units(line, units_raw)
        if not hit:
            broken.append({"line": str(line)[:60],
                           "reason": "cross_shot_split" if _joinable else "not_found"})
    # v6.138（九审 P2-4）：**排他性**——计划句逐字保住还不够，稿件不得**另编**计划外
    # 对白（"5/5 命中 + 1 句自选"此前照样过门，含 BEST-GUARD 历史快照；自选台词 =
    # 生成层重新选材，违反计划权威）。判据收窄到**带说话人前缀**的旁白：其正文与
    # 全部计划句既无包含关系（整句在镜内）、也无被包含关系（跨镜碎片）⇒ 自选对白。
    # 无前缀的旁白（描述/画外音）不按对白拦截（审查明确要求）。
    for _ui, _u in enumerate(units_raw):
        if not _split_speaker_prefix(_u)[0]:
            continue
        _u_forms = [f for f in (unit_forms_full[_ui] + unit_forms_body[_ui]) if f]
        if not _u_forms:
            continue
        _narration_forms = narration_forms_by_shot.get(unit_shots[_ui], [])
        if any(_body and _body in _narration
               for _body in unit_forms_body[_ui] for _narration in _narration_forms):
            continue
        _consumed = False
        for _uf in _u_forms:
            for _lf in all_line_forms:
                if _lf and (_lf in _uf or _uf in _lf):
                    _consumed = True
                    break
            if _consumed:
                break
        if not _consumed:
            broken.append({"line": str(_u)[:60], "reason": "extra_dialogue"})
    return (not broken), broken


class EventContractBrokenError(DialogueIncompleteError):
    """候选层**事件履约契约**：镜头的 plan_refs 声明违反节拍覆盖/顺序/越界.

    v6.138（九审根因 1，2026-09-26）：计划的事件链（`shot_beats` 节拍）此前只作
    参考信息注入 prompt，没有任何可校验的承载声明——节拍「恐吓听香」在规划重选
    删掉承载台词后成片无承载（审计 MISSING_PLOT）、修复补丁把被删的「赶车命令」
    改成视觉手势且无承接（MISSING_ACTION），都只能在提交点靠语义审计发现，而修复
    轮又不许改台词（逐字锁）⇒ 结构性死锁。契约把「哪个镜头承载哪些节拍/台词」
    变成**显式、可确定性核验**的声明（`plan_refs`），在候选入池前核验。

    与 `DialogueIncompleteError` 同性质（内容结论、非检查故障）：fail-closed、
    独立分账（attempt_stats.contract_broken），**不得**混入 dialogue_broken——
    把「节拍无承载」统计成「台词截断」= 五审同型误归因。继承它只为让调用方的
    「显式失败、不重试」分支照常接住。
    """


#: `shot_beats` 条目的**段标签**（`｜` 分隔；顺序即渲染顺序）
BEAT_SEG_BODY = "节拍："
BEAT_SEG_PURPOSE = "本镜信息目的："
BEAT_SEG_EVIDENCE = "原文依据："
BEAT_SEG_CARRIER = "载体："
BEAT_SEG_FACTS = "事实："
#: 已认识的段标签。**未识别**的段（如旧文书的「信息目的：」、未来的新标签）不判错，
#: 按原样保留在记录里、原样渲染回去——解析器不认识 ≠ 可以丢内容。
BEAT_SEG_TAGS = (BEAT_SEG_BODY, BEAT_SEG_PURPOSE, BEAT_SEG_EVIDENCE,
                 BEAT_SEG_CARRIER, BEAT_SEG_FACTS)

#: 节拍载体标注段前缀（`shot_beats` 条目的「｜载体：…」段，v6.138）——历史别名
_BEAT_CARRIER_TAG = BEAT_SEG_CARRIER
_BEAT_CARRIER_DIALOGUE_PREFIX = "台词"


#: 编号里允许的数字：**ASCII `0-9`**。模型写全角/上标数字都不是编号——那属于"写法非法"，
#: 要在**解析边界**返回结构化结果，而不是让 `int()` 的异常一路送出计划层。
_DIGITS = "0123456789"


def safe_decl_int(text):
    """ASCII 数字串 → `int`；空串 / 含非 ASCII 数字 / 超长 ⇒ `None`（**绝不抛异常**）.

    单源（2026-10-01 复核第 30 轮）：承载声明、节拍载体、区间上下界、`plan_refs` 引用编号、
    操作编号**共用它**。旧实现各写各的——`str.isdigit()` 对 `²` 也为真（随后 `int()` 抛
    `ValueError`），4300 位以上的数字串又超出 CPython 的 int 转换上限：两种都会把异常
    送到调用方，连契约诊断都留不下。
    """
    _s = "" if text is None else str(text)
    if not _s or any(_c not in _DIGITS for _c in _s):
        return None
    try:
        return int(_s)
    except ValueError:                         # 超长数字串（CPython 的转换上限）
        return None


def parse_beat_carrier(value) -> tuple:
    """**节拍载体值** → `("visual", None)` / `("dialogue", k)` / `("narration", k)` /
    `("invalid", None)`；**绝不抛异常**.

    单源（2026-10-01 复核第 30 轮）：`BeatRecord.carrier()` 与操作应用器
    （`outline._norm_beat_carrier`）共用它。两处各写一份必然漂移——实测操作侧按"数字前缀"
    截取，模型写的 `new_carrier="台词1-2"` 被**静默**改成 `台词1`，载体关系还被判通过。

    文法（**完整匹配**）：去掉全部空白后恰好是 `画面` / `台词<十进制k>` / `旁白<十进制k>`；
    其余（`台词1-2`、`台词1、台词2`、`歌词1`、`台词²`、超长数字）一律 `invalid`——载体是
    **唯一记录**，含糊值等于没声明。`k` 原样返回（是否越界由各调用方按自己的轴判）。
    """
    _v = "".join(str(value or "").split())
    if _v == "画面":
        return "visual", None
    for _kind, _tag in (("dialogue", "台词"), ("narration", "旁白")):
        if _v.startswith(_tag):
            _k = safe_decl_int(_v[len(_tag):])
            return (_kind, _k) if _k is not None else ("invalid", None)
    return "invalid", None


class BeatRecord:
    """`shot_beats` 单条节拍的**结构化内部记录**（`parse` ⇄ `render` **逐字往返**）.

    2026-09-27 用户决定（合并方案第 2 步）：此前节拍是「一整串靠 `｜` 拼起来的文本」，
    载体/原文依据/事实编号全靠**子串扫描**判断（`_beat_carrier` 找「载体：」、
    `_strip_beat_carrier` 删所有载体段）。这种表示下：

    - **判据依赖拼接形态**：多一个载体段、少一个，只有事后数字符串才发现；
    - **改不了**：想给一条节拍补一段事实编号，只能重新拼整串；
    - **加不了节拍**：操作只能**替换**已有条目，不能插入/拆分——而评审指出章 2 不
      收敛的根因之一正是「节拍操作表达能力不足」（改不了节拍**数量**，可承载关系缺陷
      恰恰需要「把一条节拍拆成画面 + 台词两条」或「补一条承载 D1 的节拍」）。

    故先把条目解析成记录，判据一律读记录字段（`body` / `evidence` / `carrier()` /
    `facts()`），需要落盘时再 `render()` 回现文本格式。

    **往返是逐字等价的**：`segments` 保存每个 `｜` 段的**原始文本**，`render()` 只是
    重新 `｜` 拼接——对**任意**输入 `parse(x).render() == x`（含前导空白、未识别段、
    重复段）。这一点是硬要求：`_plan_core_preserved` 用**去载体后的原字符串**比较节拍，
    解析若顺手做了规范化，就会凭空制造「核心字段被改动」而误拒合法重选。
    """

    __slots__ = ("segments",)

    def __init__(self, segments=None):
        #: `[(tag, raw)]`；`tag` = 识别到的段标签（`None` = 无标签/未识别段），
        #: `raw` = 该段的**完整原文**（含标签本身与任何前导空白）
        self.segments = [(t, r) for t, r in (segments or [])]

    # ---- 解析 / 渲染 ------------------------------------------------------
    @classmethod
    def parse(cls, text) -> "BeatRecord":
        segs = []
        for seg in str(text or "").split("｜"):
            _stripped = seg.lstrip()
            for tag in BEAT_SEG_TAGS:
                if _stripped.startswith(tag):
                    segs.append((tag, seg))
                    break
            else:
                segs.append((None, seg))
        return cls(segs)

    def render(self) -> str:
        return "｜".join(raw for _, raw in self.segments)

    @staticmethod
    def _off(raw: str, tag: str) -> int:
        """标签在段原文里的起始下标（段可能有前导空白，如 `｜ 载体：台词2`）."""
        return len(raw) - len(raw.lstrip())

    # ---- 段级访问器 ------------------------------------------------------
    def has(self, tag: str) -> bool:
        return any(t == tag for t, _ in self.segments)

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.segments if t == tag)

    def get(self, tag: str, default: str = "") -> str:
        for t, raw in self.segments:
            if t == tag:
                return raw[self._off(raw, tag) + len(tag):]
        return default

    def set(self, tag: str, value: str) -> None:
        """改写**第一个**该标签段；不存在则追加到末尾（段序可预测）."""
        for i, (t, raw) in enumerate(self.segments):
            if t == tag:
                off = self._off(raw, tag)
                self.segments[i] = (tag, raw[:off + len(tag)] + value)
                return
        self.segments.append((tag, tag + value))

    def drop(self, tag: str) -> None:
        """删掉**所有**该标签段（`_strip_beat_carrier` 的语义：重复标注一并清掉）."""
        self.segments = [(t, raw) for t, raw in self.segments if t != tag]

    # ---- 字段访问器（判据一律读这里，不再扫字符串）------------------------
    @property
    def body(self) -> str:
        """节拍正文（`节拍：` 段，去首尾空白）."""
        return self.get(BEAT_SEG_BODY).strip()

    @property
    def purpose(self) -> str:
        """本镜信息目的（旧文书写「信息目的：」→ 未识别段，这里为空，不误判）."""
        return self.get(BEAT_SEG_PURPOSE).strip()

    @property
    def evidence(self) -> str:
        """原文依据（含引号，原样）."""
        return self.get(BEAT_SEG_EVIDENCE).strip()

    def carrier(self) -> tuple:
        """载体 → `("dialogue", k)` / `("visual", None)` / `(None, None)`（未声明）.

        **只读第一段**（与 v6.138 的原口径一致）；「写了多段」这件事由
        `count(BEAT_SEG_CARRIER) > 1` 单独判（承载关系必须是**唯一记录**）。
        未声明 = `(None, None)`：**没有契约可核 ≠ 契约被破坏**（与台词门对
        「无台词计划」的口径一致）。`载体：台词k` 的 k 取随后的整数；其余取值按画面。
        """
        if not self.has(BEAT_SEG_CARRIER):
            return None, None
        # 2026-09-30 复核 P2-1：「取前导数字、其余一律按画面」会**静默改意**——`台词1、台词2`
        # 被读成只有台词1、`台词X` 被读成画面。现在只认完整匹配的三种写法，其余
        # `("invalid", None)` 由调用方**显式报错**；「没有登记」仍是 `(None, None)`
        # （无可核 ≠ 被破坏，口径不变）。
        # 2026-10-01 复核第 30 轮：文法与数字转换**单源**在 `parse_beat_carrier`
        # ——它与操作应用器共用（两处各写一份时，操作侧的"数字前缀"截取把
        # `台词1-2` 静默改成了 `台词1`）。
        return parse_beat_carrier(self.get(BEAT_SEG_CARRIER))

    def set_carrier(self, kind, index=None) -> None:
        """按 `carrier()` 的取值口径写回载体段（`dialogue`/`narration` 需给 `index`）."""
        if kind == "dialogue" and index:
            _txt = "台词%d" % int(index)
        elif kind == "narration" and index:
            _txt = "旁白%d" % int(index)
        else:
            _txt = "画面"
        self.set(BEAT_SEG_CARRIER, _txt)

    def facts(self) -> list:
        """「事实：F1、F3」→ `[1, 3]`（无该段 → `[]`，旧计划即此形态）.

        第 3 步（事实拆到可承载粒度）会用这张表建立「事实—节拍」关系；本步只把
        字段**接进记录**（解析/渲染/往返），不新增判据。
        """
        return [int(x) for x in re.findall(r"\d+", self.get(BEAT_SEG_FACTS))]

    def set_facts(self, ids) -> None:
        _ids = [int(i) for i in (ids or [])]
        if _ids:
            self.set(BEAT_SEG_FACTS, "、".join("F%d" % i for i in _ids))
        else:
            self.drop(BEAT_SEG_FACTS)


def _beat_carrier(beat_text: str) -> tuple:
    """（历史入口，语义不变）节拍载体标注 → `("dialogue", k)` / `("visual", None)`.

    实现已迁到 `BeatRecord.carrier`——判据读记录字段，不再扫字符串。
    """
    return BeatRecord.parse(beat_text).carrier()


def _strip_beat_carrier(beat_text: str) -> str:
    """去掉节拍条目的**所有**「｜载体：…」段——规划重选核心字段比较用（只许改载体标注）."""
    _rec = BeatRecord.parse(beat_text)
    _rec.drop(BEAT_SEG_CARRIER)
    return _rec.render()


def replan_criteria_version() -> str:
    """重选**验收判据**的版本指纹（这两个文件承载全部重选规则）.

    2026-09-27 复核：`plan_replan` 记着 `result=infeasible/no_change` 时，后续运行会
    直接沿用（「同一计划版本只重选一次」）。但**判据本身变了**（例如新增承载关系验收、
    收紧半句判据）之后，那条旧结论就不再代表「本判据下也不可行」——沿用等于用旧尺子
    的量数否定新尺子的重试。故把判据版本一并记进 `plan_replan`，不匹配就当作没试过。

    ⚠️ 为什么不复用 `RULE_SOURCE_FINGERPRINT`：那个指纹的构成里**不含**
    `plan_feasibility.py` / `outline.py`，改这两处的规则它不会变。

    ⚠️ **失效宽度是「整个文件」**（改注释、改日志也会换版本）——这是**有意**的保守
    取舍：宁可多跑一轮重选，也不因为「只改了函数体」的乐观假设而沿用旧结论。
    可接受的代价：Step 2 动过这两个文件 ⇒ 章 2 那份旧缓存下次运行会**重跑一轮
    重选**（预期结果，不是故障）。
    """
    import hashlib
    from pathlib import Path
    _d = Path(__file__).resolve().parent
    _h = hashlib.sha256()
    for _name in ("plan_feasibility.py", "outline.py"):
        try:
            _h.update((_d / _name).read_bytes())
        except OSError:
            return ""
    return _h.hexdigest()[:12]


def carrier_relation_records(beats, n_lines: int, n_narration: int = 0) -> list:
    """承载关系验收（**双向闭合**）：返回**结构化记录**（工作包 §2.2）.

    `kind` / `index` / `path` 在遍历节拍与声音条目时**当场给出**——报告层不再从
    `"B3"` / `"D2"` 这类标签串里认编号（旧写法按标签取序号，标签与轴要另判一次）。
    以下为原判据说明：

    2026-09-27 复核：规划层原先只有**单向**检查——「节拍指向已不存在的台词」
    （`beat_carrier_dangling`）。缺的两面是：

      ① **重复载体**：同一个节拍写了多个「｜载体：…」段。`_beat_carrier` 只读第一段，
         后面的被静默忽略——承载关系不是**唯一记录**，实跑出现过
         「｜载体：画面｜载体：画面」（应用器把模型自带的旧标注又追加了一遍）。
      ② **选中台词无人承载**：台词被选中却没有节拍声明承载它。后果不在计划层显形：
         生成契约里没有哪条节拍告诉模型这句放哪儿 → 成片大概率不出这句 →
         **候选层台词门**（每句必须逐字出现）整稿拒收，问题被从计划层挪到候选层。
         实跑形态：重选把 D1 换掉、又把原指向 D1 的节拍改成「载体：画面」。

    调用方：`evaluate_plan`（计划验收）、`apply_replan_ops`（重选应用后**重新验算
    整张承载关系**，不能只验算字数）。`beats` 里**一条载体标注都没有**时不判孤儿
    （旧缓存计划无可核 ≠ 被破坏，与 `beat_carrier_dangling` 同口径）。
    """
    _beats = [str(b).strip() for b in (beats or []) if str(b).strip()]
    _recs = [BeatRecord.parse(b) for b in _beats]
    # 重复载体：同一节拍写了**多个**「｜载体：…」段（承载关系不是唯一记录，
    # `carrier()` 只读第一段、后面的被静默忽略）。按**段**判定——不再数字符串里
    # 出现了几次标签（引文里提到「载体：」不算标注）。
    duplicate = [i for i, r in enumerate(_recs, 1)
                 if r.count(BEAT_SEG_CARRIER) > 1]
    orphan: list = []
    unlabeled: list = []
    if any(r.has(BEAT_SEG_CARRIER) for r in _recs):
        # 计划**带了**标注 ⇒ 承载关系是**唯一且完整**的记录：每条节拍**恰有一个**
        # 载体（2026-09-27 复核：只查「所有台词被别人覆盖」不够——允许部分节拍没有
        # 标注，等于同一份计划里两种口径混用，承载关系不再可判定）。
        # 「非法载体」（`台词1、台词2` / `台词X` / 未知值）与「没标」一样不可核：
        # 两者都必须显式失败，而不是被解析器悄悄改意后放行。
        # 旁白（`载体：旁白k`）是**合法**载体（复核第 6 轮）——它的内容核验在
        # `plan_contract_problems` / `evaluate_plan`（原文逐字 + 条目存在 + 与事实配对）。
        unlabeled = [i for i, r in enumerate(_recs, 1)
                     if r.carrier()[0] not in ("dialogue", "visual", "narration")]
        carried = set()
        carried_n = set()
        for r in _recs:
            kind, k = r.carrier()
            if kind == "dialogue" and k:
                carried.add(k)
            elif kind == "narration" and k:
                carried_n.add(k)
        orphan = [("dialogue", i) for i in range(1, int(n_lines or 0) + 1)
                  if i not in carried]
        # 2026-09-30 复核第 6 轮：旁白是**第二个**可被选中的内容序列，同样必须双向
        # 闭合——选中了旁白却没任何节拍承载它，成片里没人念，而计划层此前全绿。
        orphan += [("narration", i) for i in range(1, int(n_narration or 0) + 1)
                   if i not in carried_n]
    return _carrier_relation_records(duplicate, unlabeled, orphan)


def _carrier_relation_records(duplicate, unlabeled, orphan) -> list:
    """把承载关系判据的**原始序号**收成六字段记录（判据本身在上一函数里）."""
    _b = "承载关系：节拍 ↔ 声音条目**双向闭合**（重复载体/孤儿/未标注）"
    _out = []
    for _i in duplicate:
        _out.append(_problem_record(
            "carrier_duplicate", "beat", _i, "shot_beats[%d].载体" % (_i - 1),
            "B%d 写了多个「载体：」段——承载关系必须是唯一记录" % _i, _b))
    for _i in unlabeled:
        _out.append(_problem_record(
            "carrier_unlabeled", "beat", _i, "shot_beats[%d].载体" % (_i - 1),
            "B%d 的载体标注非法或缺席（计划带了承载契约，就必须每条都有）" % _i, _b))
    for _kind, _i in orphan:
        _axis = "narration_lines" if _kind == "narration" else "dialogue_lines"
        _lab = ("N%d" if _kind == "narration" else "D%d") % _i
        _out.append(_problem_record(
            "orphan_uncarried", _kind, _i, "%s[%d]" % (_axis, _i - 1),
            "%s 被选中却没有任何节拍承载它（成片里没人念）。保留它时须分散到不同的节拍上，各自分配承载节拍；"
            "预算不足或不再选用时可删除该声音条目，并同步撤销/重绑所有引用与钩子，"
            "仍须完整保留冻结事实与事件。一条节拍只能标**一个**声音载体；"
            "保留 k 条声音条目时须有 k 条承载节拍，不要求保留全部候选台词。"
            % _lab, _b))
    return _out


def carrier_relation_problems(beats, n_lines: int, n_narration: int = 0) -> tuple:
    """兼容转发：旧的 `(duplicate, orphan, unlabeled)` 标签清单，由记录渲染."""
    _recs = carrier_relation_records(beats, n_lines, n_narration)

    def _labels(code):
        return [("N%d" if r["kind"] == "narration"
                 else ("D%d" if r["kind"] == "dialogue" else "B%d")) % r["index"]
                for r in _recs if r["code"] == code]
    return _labels("carrier_duplicate"), _labels("orphan_uncarried"), \
        _labels("carrier_unlabeled")


# ---------------------------------------------------------------------------
# 计划契约：必保留事实与钩子的**承载**（2026-09-30 复核裁决）
#
# 复核原话：「另做**不依赖 ops 的计划验收函数**，供首版、重选版和缓存版共用。
# 核对实际节拍正文／信息目的及选用台词，不能靠"原文依据"互抄判事实已表达；
# `画面`声明也须与节拍实际载体一致。」「在生成前统一入口拒绝缺失或无效的承载，
# 单独报告 `plan_contract_invalid`；不要把它送进预算重选，也不要报告"需拆集"。」
#
# 为什么不塞进 `evaluate_plan` 的 `infeasible`：那条路会触发**预算重选**（换掉次要
# 内容再试一轮），而缺承载声明是**计划契约未完成**——取舍还没做完，换内容解决不了；
# 误报「需拆集」更是把「登记信息缺失」说成「故事装不下」。
# ---------------------------------------------------------------------------

#: 计划契约版本。缓存里的计划**必须**带同一版本才会被复用：旧计划没有承载字段，
#: 缺的是**登记信息**、不是内容——备份旧缓存后**重新产出首版计划**，
#: 不能误报「故事不可行」。
#:
#: v3（2026-09-30 复核第 4 轮 P2）：新增 `hook_spoken`——「钩子里哪几句必须由角色
#: 说出口」从**自由文本的猜测**（关键词 + 窗口）改成**显式声明**。旧计划没有这个
#: 字段 ⇒ 按既有机制备份并重新产出首版计划（不是「故事不可行」）。
#:
#: v4（2026-09-30 复核第 6 轮裁决）：新增第三种承载方式**图外旁白**——计划带编号
#: 旁白条目 `narration_lines`（`N1..Nn`，逐字原文叙述 + 出处 + 承载的事实），承载写作
#: `Bn/旁白k`；旁白与对白共用发声时长与预算。旧计划没有这个字段 ⇒ 同样备份重生成。
PLAN_CONTRACT_VERSION = 5

#: 从这一版起，计划走**结构化呈现协议**（`presentation_contract.v1`）——
#: 承载关系读 `presentation.fact_bindings`，不再解析 `｜承载：…` 文案。
PRESENTATION_MIN_VERSION = 6

#: 载体**区间**写法（模型自创）：`旁白1-5` / `台词1-2`——含义唯一，是"这一条节拍承载
#: 旁白1 到 旁白5"的简写。按工作包边界，这属于**表达**：保守规范化把它展开成逐条承载的
#: 多条节拍，并**同步重映射**所有节拍引用；语义错配（声明了承载、节拍其实不承载）留给规划器。
_RANGE_CARRIER_RE = re.compile("^(旁白|台词)([0-9]+)-([0-9]+)$")

#: 承载声明的**唯一文法**：`B 2 / 画面`、`B2/台词1`、`B` 与数字之间换行/夹 Unicode 空白
#: 都算数——容差就是 `str.isspace()` 那一套，与验收解析器过去用的 `decl.split()` **完全同一
#: 集合**。旧实现另列了一份空白名单（空格+制表符+全角空格），于是 `B 2/画面` 验收接受、
#: 重映射漏认：**合法**引用 0 问题 → 1 问题，越界的 `B 3/画面` 反过来被展开洗成 0 问题
#: （第 28 轮复核实测）。名单必然漂移 ⇒ 只留一份扫描器，两边都消费它的结果。
#: 承载声明的**唯一文法**——与旧的验收解析器**逐字相同**：先按原契约去掉**全部空白**
#: （`str.split()` 那一套：空格/制表/换行/全角空格/NBSP…），再按
#: `B<ASCII 数字>/<画面|台词k|旁白k>` 匹配。数字只认 ASCII `0-9`：`B²/画面` 这类一律
#: **不匹配**（第 29 轮复核：上一版扫描器用 `isdigit()` 放它进来，随后 `int()` 抛
#: `ValueError` 一路穿过生成前门，连契约诊断都没留下）。验收、旧域核验、重映射
#: 都消费这里的结果——判据只有一份，位置映射让重映射仍能只改数字那一段。
_DECL_REF_RE = re.compile("B([0-9]+)/(画面|台词([0-9]+)|旁白([0-9]+))")


def _flat_decl_text(text: str) -> tuple:
    """去掉全部空白 + **位置映射**：`(flat, pos)`，`pos[i]` 是 `flat[i]` 在**原文**里的下标."""
    _flat, _pos = [], []
    for _i, _c in enumerate(text):
        if _c.isspace():
            continue
        _flat.append(_c)
        _pos.append(_i)
    return "".join(_flat), _pos


#: **操作协议支持的键**（工作包 §4 C；**只此一处**，与
#: `_text_gen/deepseek_schema.REPLAN_OPS_SCHEMA` 的 properties 一致——有测试钉住漂移）。
#: 语义（每个键"缺席 / 空值 / 非空"三态都要能表达）：
#:
#: | 键 | 缺席 | `null` | 空值（`[]`/`""`） | 非空 |
#: |---|---|---|---|---|
#: | `dialogue_ops` | 不改 | **非法**（必填键） | `[]` = 不改 | 逐条替换台词 |
#: | `beat_ops` | 不改 | **非法**（必填键） | `[]` = 不改 | replace / insert_after / split |
#: | `fact_carriers` | 不改 | **非法**（必填键） | `[]` = 不改（"不改"只能这样表达；<br>整表清空不是合法终态：每条必保留事实都必须有承载） | 逐条声明承载（改台词/节拍时要求**逐项给全**） |
#: | `hook_carrier` | 不改（可省略） | 不改 | `""` = **清除**承载声明 | 设置（`B2/台词1`，多句用「；」） |
#: | `hook_spoken` | 不改（可省略） | 不改 | `[]` = **清除**"必须说出口" | 逐句设置（逐字原文） |
#: | `narration_lines` | 不改（可省略） | 不改 | `[]` = **清空**旁白表 | 整表设置（text/source/fact_refs） |
#:
#: `null` 的合法性**由 `required` 决定**：必填键给 `null` 不是"缺席"而是**违反 schema**
#: （`replan_ops_response_problems` 记 `illegal_response`——复核 2026-10-02 之三 P1）；
#: 可省略的声明键给 `null` 才是"这一项没有"。**缺席**一律读作「不改」（安全默认）。
#: **嵌套字段（条目里）另有一条硬规则**（复核 2026-10-02 之四 C）：那里的 `null` **不是**
#: "不改"、也不许被当默认值读——要"不改"就**省略那个键**；`beat_ops` 的
#: `op`/`new_text`/`new_carrier`/`parts`、旁白条目的 `text`/`source`/`fact_refs`、
#: 对象形态 `hook_carrier` 的 `beat`/`mode` 给 `null` 一律 `illegal_response`。
#: 许可范围由 `REPLAN_NULL_OK_KEYS` + schema 自己声明的 `null` 类型决定（提示同源渲染）。
DECL_OP_KEYS = ("fact_carriers", "hook_carrier", "hook_spoken", "narration_lines")
BASE_OP_KEYS = ("dialogue_ops", "beat_ops")
REPLAN_OP_KEYS = BASE_OP_KEYS + DECL_OP_KEYS


#: 声明键的**三态语义（唯一源）**：`键 → (空值是否算"要改", 空值的**模型面说法**)`。
#: `True` = 出现即"要改"（空值 = **显式清除**）；`False` = 空值只是"没什么可说"（= 不改）。
#:
#: 那句**模型面说法**由判据侧**渲染进提示**（`decl_semantics_clause()`），并且必须
#: **逐字**出现在请求 schema 的对应描述里——两个方向都由守卫用例逐键核对
#: （复核 2026-10-02 P2：提示把四个键的空值统称"清除"，而 `fact_carriers: []` 的裁决
#: 是"不改"：**提示与裁决自相矛盾**，模型照着提示写就会写错）。
#:
#: **不变量（复核 2026-10-02 P1）**：请求 schema 里 `required` 的键，其空值只能是 `False`
#: ——模型"没有话可说"时按 schema 填的缺省填充值必须是**安全**的。`hook_carrier` 曾经
#: 既必填、空串又等于清除：一份**零问题**的计划收到符合当时 schema 形态的"只修事实"
#: 响应，合法钩子承载被静默清掉，新增 `carrier_hook_unlabeled`。要么把键改成可省略
#: （省略 = 不改），要么给"清除"一个**非缺省**的显式记号——不能拿缺省值当清除用。
#: 由守卫用例 `test_plan_phase_c_declaration_ops.py::test_c06_*` / `test_c09_*` 钉住。
DECL_OP_SEMANTICS = {
    "fact_carriers": (False, "空数组 = 本批不改承载"),
    "hook_carrier": (True, "空串 = 清除这个声明"),
    "hook_spoken": (True, "空数组 = 清除这个声明"),
    "narration_lines": (True, "空数组 = 清空本集旁白表"),
}

#: 兼容视图：`{键: 空值是否算"要改"}`（`effective_decl_ops` 直接消费它）。
DECL_OP_EMPTY_MEANS_CHANGE = {k: v[0] for k, v in DECL_OP_SEMANTICS.items()}


def decl_semantics_clause() -> str:
    """声明键的空值语义渲染成**一句话**——提示里那段"三种写法"由它生成（不手抄）."""
    return "；".join("`%s` %s" % (_k, DECL_OP_SEMANTICS[_k][1]) for _k in DECL_OP_KEYS)


def effective_decl_ops(ops) -> list:
    """本批**确实要求改动**的声明键（工作包 §4 C：区分"缺席""没什么可说"与"显式清除"）.

    复核第 45 轮实测的缺口：`apply_replan_ops` 只读台词/节拍两类键，只给声明时直接落
    `no_change`——把"接口表达不了"记成"模型认为无需改动"。这个函数是**判定入口**：
    只要它非空（或有台词/节拍操作），本轮就必须真正走到应用器。

    - `fact_carriers: []` = 没什么可说（本键必填，"不改"只能这样表达）；
    - `hook_carrier: ""` / `hook_spoken: []` / `narration_lines: []` = **显式清除**，算要改；
    - **`null` = 缺席**（不改）：`null` 是模型用来表示"这一项没有"的填充值，与"清除"同形
      却意思相反——**可省略的**声明键一律按"不改"读（同类静默破坏：`{"narration_lines":
      null}` 旧实现会清空整张旁白表）。要清除就写**空串/空数组**。
      必填键（`fact_carriers`）不适用这条：它的 `null` 由 `replan_ops_response_problems`
      在更早一步判成 `illegal_response`（复核 2026-10-02 之三 P1）。
    """
    if not isinstance(ops, dict):
        return []
    _out = []
    for _k in DECL_OP_KEYS:
        if _k not in ops or ops.get(_k) is None:
            continue
        if DECL_OP_EMPTY_MEANS_CHANGE[_k]:
            _out.append(_k)                  # 出现（哪怕空值）就是要改
        elif isinstance(ops.get(_k), list) and ops.get(_k):
            _out.append(_k)                  # 空值 = 没什么可说（不改）
    return _out


#: 操作响应的**请求侧 schema**（唯一源）：本地响应校验按它核对形态，提示里"必填键"那句
#: 也由它渲染——请求 schema / 提示 / 应用器**三处同源**（工作包 §4 C 的裁决）。
from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA as _REPLAN_OPS_SCHEMA

REPLAN_RESPONSE_SCHEMA = _REPLAN_OPS_SCHEMA["schema"]

#: 必填键（顺序同 schema）：**不能给 `null`**——没有要改的就给类型空值。缺席（整个键不出现）
#: 仍按三态语义读作「不改」：那是**安全**的默认（等价于空数组，不丢任何模型说过的话）。
#: 复核 2026-10-02 之三 P1：应用器此前把必填键的 `null` 也当**缺席**读，违反严格 schema 的
#: 响应（`{"dialogue_ops": null}`）被静默"修好"——单独出现报 `no_change`、与别的操作同批
#: 还能应用。`null` 只允许落在**可省略的声明键**上（= 不改）。
REPLAN_REQUIRED_KEYS = tuple(REPLAN_RESPONSE_SCHEMA.get("required") or ())

#: **唯一允许 `null` 的顶层键**：可省略的声明键（三态语义里 `null` = 不改）。必填键、
#: 以及**任何嵌套字段**的 `null` 都不是合法取值（复核 2026-10-02 之四 C：字段**缺席**
#: 才是「不改」，`null` 是模型确实写了一个空值——嵌套上的 `null` 曾被当默认值/忽略照常应用）。
REPLAN_NULL_OK_KEYS = tuple(k for k in REPLAN_OP_KEYS if k not in REPLAN_REQUIRED_KEYS)

#: `hook_carrier` 的**历史对象形态**（`{"beat": 3, "mode": "台词2"}`）：请求 schema 的契约
#: 形态是字符串，对象形态只作兼容；键集合、类型与 `null` 规则跟其它嵌套字段**同一套**
#: （此前只核键集合，`mode: null` 会被归一成空载体，"模型写了个空值"没人报）。
HOOK_CARRIER_OBJ_SPEC = {"type": "object", "additionalProperties": False,
                         "properties": {"beat": {"type": "integer"},
                                        "mode": {"type": "string"}}}

#: 类型 → 中文说法（诊断**指认类型错**，不替模型圆场）。
_TYPE_SAY = {"array": "不是数组", "string": "必须是字符串", "object": "必须是对象",
             "integer": "非整数", "boolean": "必须是布尔值", "number": "不是数字"}

#: 本地响应校验**整块跳过**的字段路径（值由应用器的**安全归一**单源处理）：
#: 编号写法 `2` / `"2"` / `"B2"` 由 `coerce_ref_index` → `_coerce_op_index` 解析，
#: 轴不对 / 不是编号 / 越界各有结构化原因。**不能在形状层先判死**——那是**生产要求**：
#: agent_plan 通道把 json_schema 降级成 `json_object`，模型照着上下文里 `B2` 的写法回话
#: （2026-09-27 复核的现场：`int("B2")` 抛 ValueError ⇒ 整轮修订被记成拒收）。
#: 这些字段里**没有**归一能力的取值（字典/数组）同样由那条通道给出了原因。
SHAPE_SKIP_PATHS = ("beat_ops.[].index", "dialogue_ops.[].index",
                    "fact_carriers.[].index", "fact_carriers.[].beat")


def _type_say(spec) -> str:
    _t = spec.get("type")
    return " / ".join(_TYPE_SAY.get(x, "类型应为 %s" % x)
                      for x in (_t if isinstance(_t, list) else [_t]) if x)


def _type_ok(value, spec) -> bool:
    def _one(x):
        return {"array": lambda: isinstance(value, list),
                "string": lambda: isinstance(value, str),
                "object": lambda: isinstance(value, dict),
                "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
                "boolean": lambda: isinstance(value, bool),
                "number": lambda: isinstance(value, (int, float))
                and not isinstance(value, bool)}.get(x, lambda: True)()
    _t = spec.get("type")
    _ts = [x for x in (_t if isinstance(_t, list) else [_t]) if x]
    return (not _ts) or any(_one(x) for x in _ts)


def _empty_value_say(spec) -> str:
    """该键"没有要改"时的类型空值写法（提示与诊断共用同一句话）."""
    return {"array": "`[]`", "string": "空串", "object": "`{}`"}.get(
        spec.get("type"), "类型空值")


def _null_declared(spec) -> bool:
    """schema **自己**声明了 `null` 才算合法取值（`"type": ["string", "null"]`）.

    当前请求 schema 里没有任何字段声明 `null`；除**显式约定的可选顶层声明键**
    （`REPLAN_NULL_OK_KEYS`）外，`null` 一律不是合法取值。
    """
    _t = spec.get("type")
    return "null" in (_t if isinstance(_t, list) else [_t])


def _shape_problems(value, spec: dict, path: str, *, null_ok: bool = False,
                    trail: tuple = ()) -> list:
    """递归核对一个值是否满足 schema 片段 → 问题清单（`path` 给人看、`trail` 给判据认）.

    判：类型、`enum`、**没定义过的键**（含嵌套条目里的）、**`null` 的位置**。
    **不判"条目内必填键缺失"**：那条留给应用器的**域内**判据——`beat_ops` 的 `op` 有
    既定默认（不写 = `replace`）、`parts` 只在 `split` 时有意义，应用器的报错也更具体
    （"第 N 条节拍：…"）。本地校验的职责是"模型说的话**没有被曲解**"，不是替应用器
    重新定义条目语义。
    **`null` ≠ 缺席**（复核 2026-10-02 之四 C）：字段**缺席**才是「不改」；`null` 是模型
    确实写了一个空值。嵌套字段一律非法（`beat_ops` 的 `op: null` 曾被当默认 `replace`
    照常应用）；`null_ok=True` 只由**已约定的可选顶层声明键**（三态语义）传入，或由
    schema 自己声明 `null` 类型。
    `SHAPE_SKIP_PATHS` 里的字段整块跳过（见那里的说明：它们的取值由**安全归一**单源处理）。
    """
    if ".".join(trail) in SHAPE_SKIP_PATHS:
        return []
    _out: list = []
    if value is None:
        if null_ok or _null_declared(spec):
            return []
        return ["%s 为 null——schema 里它是 %s，`null` 不是合法取值"
                "（要「不改」就**省略这个键**）" % (path, spec.get("type"))]
    if not _type_ok(value, spec):
        _out.append("%s %s（schema 要求 %s；现在是 %s）"
                    % (path, _type_say(spec), spec.get("type"), type(value).__name__))
        return _out                                  # 类型都不对，不再往下认结构
    _enum = spec.get("enum")
    if _enum and value not in _enum:
        _out.append("%s 只能是 %s（现在是 %r）"
                    % (path, " / ".join(str(x) for x in _enum), value))
    if isinstance(value, list) and isinstance(spec.get("items"), dict):
        for _i, _x in enumerate(value, 1):
            _out += _shape_problems(_x, spec["items"], "%s 第 %d 项" % (path, _i),
                                    null_ok=False, trail=trail + ("[]",))
    if isinstance(value, dict) and spec.get("properties"):
        _props = spec["properties"]
        if spec.get("additionalProperties") is False:
            _unk = sorted(k for k in value if k not in _props)
            if _unk:
                # **未知键不许丢**（复核 2026-10-02 之三 P1：旁白条目里的未知键此前被
                # 静默丢掉，模型写的东西没进计划、也没人知道）
                _out.append("%s 里有未定义的键 %s（schema 只允许 %s）"
                            % (path, "、".join(_unk), "、".join(_props)))
        for _k, _v in value.items():
            if _k in _props:
                _out += _shape_problems(_v, _props[_k], "%s 的 `%s`" % (path, _k),
                                        trail=trail + (_k,))
    return _out


def replan_ops_response_problems(ops) -> list:
    """**本地响应校验**（工作包 §4 C）：按请求 schema 核对模型回的操作 → 问题清单.

    判**形态**——有没有没定义过的键（含嵌套条目）、类型对不对、`null` 是不是落在了
    **可省略的声明键**上。语义（编号范围、载体是否合法、内容有没有变）仍由
    `apply_replan_ops` 判；两处职责分开，诊断才指得准。

    **不许替模型圆场**（复核 2026-10-02 之三 P1）：旁白 `text: 123` 旧实现被 `str()`
    成 `"123"`、旁白条目里的未知键被丢掉，整批照样"应用成功"（`stop_reason=ok`）；
    必填键的 `null` 又被当"缺席"读。三种都记问题，由应用器返回 `illegal_response`。

    **缺席 ≠ `null`**：整个键不出现仍按三态语义读作「不改」（安全默认，等价空数组），
    schema 里 `required` 的键只是**不许给 `null`**。**`null` 只允许落在可省略的声明键上**
    （`REPLAN_NULL_OK_KEYS`）——**嵌套字段**（`beat_ops[].op` / `.parts` / `.new_carrier`、
    旁白条目的 `text/source/fact_refs`、对象形态 `hook_carrier` 的 `beat/mode`）给 `null`
    一律记问题（复核 2026-10-02 之四 C：`op: null` 曾被当默认 `replace` 照常应用）。
    """
    if not isinstance(ops, dict):
        return ["响应不是 JSON 对象（现在是 %s）" % type(ops).__name__]
    _props = REPLAN_RESPONSE_SCHEMA.get("properties") or {}
    _out: list = []
    _unk = sorted(k for k in ops if k not in _props)
    if _unk:
        _out.append("未知操作键 %s（只支持 %s）" % ("、".join(_unk), "、".join(REPLAN_OP_KEYS)))
    for _k, _v in ops.items():
        if _k not in _props:
            continue
        if _v is None and _k in REPLAN_REQUIRED_KEYS:
            _out.append("必填键 `%s` 是 null——schema 里它是 %s，`null` 不是合法取值"
                        "（要「不改」就给 %s，或整个键省略）"
                        % (_k, _props[_k].get("type"), _empty_value_say(_props[_k])))
            continue
        if _k == "hook_carrier" and isinstance(_v, dict):
            # 历史兼容形态（对象）：键集合 / 类型 / `null` 与其它嵌套字段同一处判；
            # 取值合法性（编号越界、载体名）仍归声明解析器判。
            _out += _shape_problems(_v, HOOK_CARRIER_OBJ_SPEC, "`hook_carrier`",
                                    trail=(_k, "obj"))
            continue
        # 可省略的声明键的 `null`（= 不改）也走这里判：`null_ok` 是**活的**——
        # 上面不为它们开早退分支，否则"许可范围"就成了两处各判一次的空话。
        _out += _shape_problems(_v, _props[_k], "`%s`" % _k,
                                null_ok=(_k in REPLAN_NULL_OK_KEYS), trail=(_k,))
    return _out


def replan_response_shape_clause() -> str:
    """提示里"响应形态"那句——**由请求 schema 渲染**（不手抄键名与空值写法）.

    `null` 的许可范围与**本地响应校验**同一源（`REPLAN_NULL_OK_KEYS`）：提示教什么，
    校验就按什么判；嵌套字段的 `null` 两边都不许（复核 2026-10-02 之四 C）。
    """
    _req = "、".join("`%s`" % k for k in REPLAN_REQUIRED_KEYS)
    _defs = "、".join("%s→%s" % (k, _empty_value_say(REPLAN_RESPONSE_SCHEMA["properties"][k]))
                     for k in REPLAN_REQUIRED_KEYS)
    _opt = "、".join("`%s`" % k for k in REPLAN_NULL_OK_KEYS)
    return ("**`null` 只允许出现在可省略的声明键上**（%s）：必填键（%s）**不能给 `null`**"
            "（没有要改的就给 %s，或整个键省略 = 不改）；**条目里（含嵌套字段）的 `null` "
            "一律非法**——那些字段要「不改」就**省略那个键**，不要给 `null`。"
            "**没定义过的键一个都不许出现**（含条目里），类型也必须与 schema 一致"
            % (_opt, _req, _defs))


#: 承载表达方式的**种类（唯一源）**：`(解析用的种类名, 提示/schema 里的写法)`。
#: `画面` 无编号；`台词k` / `旁白k` 的 k 必须在范围内（`carrier_mode_problem` 判）。
#: 提示与 schema 描述里"可以写哪些载体"那句**由它渲染**——手抄一份的后果是模型只知道
#: "画面/台词k"，`旁白k` 这条**合法通道**永远用不上（复核 2026-10-02 之二 P2：
#: 应用器支持 `旁白k`，请求侧却没教；桩直传正确操作能过，真实请求不能）。
#: 解析出来的**种类名**（`parse_beat_carrier` 返回 `visual`/`dialogue`/`narration`）
#: → `(中文写法, 提示写法)`。判据、提示渲染与**应用器的载体规范化**都取这一份——
#: 各处手写"画面 / 台词 / 旁白"三件套，加第四种载体时必然漏改一处。
CARRIER_KIND_FORMS = {"visual": ("画面", "画面"),
                      "dialogue": ("台词", "台词k"),
                      "narration": ("旁白", "旁白k")}

#: 提示里"可以写哪些载体"的顺序与写法（由 `CARRIER_KIND_FORMS` 派生，不另抄一份）。
CARRIER_MODE_KINDS = tuple((CARRIER_KIND_FORMS[_k][0], CARRIER_KIND_FORMS[_k][1])
                           for _k in ("visual", "dialogue", "narration"))


def carrier_mode_hint() -> str:
    """提示 / 错误信息里"允许写哪些载体"的一句话（渲染自 `CARRIER_MODE_KINDS`）."""
    return "、".join("`%s`" % _hint for _kind, _hint in CARRIER_MODE_KINDS)


def carrier_mode_problem(mode, n_lines: int, n_narration: int) -> str:
    """承载表达方式的**结构**校验：`画面` / `台词k` / `旁白k`（k 在范围内）；"" = 通过.

    只判结构（种类与编号范围）："这条承载是否真演出了事实""画面与声音是否一致"这类
    **语义**判据归统一契约门（`plan_contract_problems`），不在这里造第二份。
    """
    _m = str(mode or "").strip()
    for _kind, _hint in CARRIER_MODE_KINDS:
        if _kind == "画面":
            continue
        if _m.startswith(_kind):
            _n = n_lines if _kind == "台词" else n_narration
            _k = _m[len(_kind):].strip()
            if not _k.isdigit() or int(_k) < 1:
                return "`%s` 里的编号不是正整数（应为 %s1..%s%d）" % (_m, _hint, _kind, _n)
            if int(_k) > _n:
                return "`%s` 越界（本批只有 %d 条%s）" % (_m, _n, _kind)
            return ""
    if _m == "画面":
        return ""
    return "`%s` 不是合法载体（只能是 %s）" % (_m, carrier_mode_hint())


def iter_decl_refs(text: str) -> list:
    """一段声明文本里的**全部**节拍引用，带**原始跨度**（单源文法）.

    每项是 7 元组 `(start, end, num_start, num_end, beat, kind, idx)`：

      · `start` / `end`：这条引用在**原文**里的跨度；
      · `num_start` / `num_end`：**节拍号数字**在原文里的跨度——重映射只替换这一段，
        `B`、`/` 与模型自己写的空白（含数字内部的空白，如 `B1 2/画面`）原样保留；
      · `beat` / `kind` / `idx`：结构化读数——旧域核验与新域映射**消费同一份**。

    判据 = `_DECL_REF_RE` 匹配**去掉空白后**的文本（与旧验收解析器同一份文法：`B1 2/画面`
    就是 `B12/画面`，`B12/画 面` 就是 `B12/画面`）；位置映射把 flat 里的跨度换回原文下标。
    非法字符（非 ASCII 数字、未知种类、长得不像节拍号的数字串）**不匹配** ⇒ 由调用方报
    "格式非法"，而不是让转换异常逃出去。
    """
    _flat, _pos = _flat_decl_text(text)
    _out = []
    for _m in _DECL_REF_RE.finditer(_flat):
        _beat = safe_decl_int(_m.group(1))
        _raw_idx = _m.group(3) or _m.group(4)
        _idx = safe_decl_int(_raw_idx) if _raw_idx else None
        if _beat is None or (_raw_idx and _idx is None):
            continue                           # 数字不像节拍号 ⇒ 当它没有写
        _kind = ("画面" if _m.group(2) == "画面"
                 else ("旁白" if _m.group(4) else "台词"))
        _a, _b = _m.span(1)
        _out.append((_pos[_m.start()], _pos[_m.end() - 1] + 1,
                     _pos[_a], _pos[_b - 1] + 1, _beat, _kind, _idx))
    return _out


def _decl_ref_problem(beat, n_beats) -> str:
    """一个节拍引用在**旧编号域**里的范围核验（`""` = 在范围内）.

    只核**节拍号**：展开会把后面的节拍整体后移，一个越界的节拍号可能**碰巧落进**合法
    范围（复核第 27 轮实测：2 条节拍 + `B3/画面`，展开后契约从报错变成通过）。台词/旁白
    序号不核——展开不改序号，越界的就是越界，契约门照样报得出来。
    """
    if not 1 <= int(beat) <= int(n_beats):
        return "节拍号 %s 越界（旧计划共 %d 条节拍）" % (beat, n_beats)
    return ""


def normalize_plan_ranges(plan):
    """**保守规范化（只做表达转换）**：载体区间展开 + 声明字段里的节拍引用重映射.

    返回 `(plan, changes)`；`changes` 为空表示**一个字节都不该改**（没有区间写法，
    或任一引用越界 ⇒ 整条计划原样返回）。

    这是一次**引用转换事务**（2026-10-01 复核第 27 轮 P1）：三件事要么一起成立、
    要么一个字都不改——

      ① **只改声明字段**：节拍引用只可能出现在「｜承载：…」段与 `hook_carrier` 里。
         事实的正文与「原文证据」是**事实本体与出处**，逐字保留。旧实现拿整条事实文本
         做替换：正文里字面的 `B3/画面` 会被一起改成 `B4/画面`（复核实测）。
      ② **先在旧编号域核验**：引用节拍号越界 ⇒ 整条计划不动，契约门照旧报它。
         旧实现把越界引用原样留着，展开把编号推高之后它**碰巧落进合法范围**，契约
         从报错变成**通过**（复核实测：2 条节拍 + `B3/画面`，问题 1 → 0）。非法引用
         不得因扩号变合法。区间上界超过条目数（`旁白1-9` 而只有 5 条旁白）同样不动
         ——那一步会替模型造出指向不存在条目的节拍。
      ③ **再映射到新编号域**，**只改数字**、保留模型自己写的空白（旧实现只认紧贴的
         `B2/画面`，含空格的**合法**声明漏改，于是规范化把一份本来通过的计划改成了
         不通过——复核实测）。

    展开规则（含义唯一才动）：
      · `载体：旁白1-5` ⇒ 5 条节拍，各自 `载体：旁白1`…`载体：旁白5`，
        `节拍／本镜信息目的／原文依据` **逐字复制**（只有载体段不同）；
      · 事实的 `承载：Bn/旁白k` 与 `hook_carrier` 里的 `Bn/…` 随编号平移；若原来那条节拍
        被拆开、而声明里的声音条目正在区间内 ⇒ 指向**真正承载它的那一条**；
      · `载体：旁白3-3`（退化区间）也只改写载体段，不拆；
      · **没有区间写法就原样返回**——"展开"不许凭空补出承载（例如 ch2 根本没有区间表达）；
      · 语义错配（`B1/旁白1` 但 B1 标的是 `画面`）**不动**，交规划器。

    幂等：展开后不再有区间写法 ⇒ 再调用一次 `changes` 为空、计划逐字不变。
    """
    if not isinstance(plan, dict):
        return plan, []
    _beats = plan.get("shot_beats")
    if not isinstance(_beats, list) or not _beats:
        return plan, []
    _records = []
    for _b in _beats:
        try:
            _records.append(BeatRecord.parse(_b))
        except Exception:                      # noqa: BLE001
            return plan, []                    # 解析不了就整体不动（宁可不改）
    # 声音条目的条数：区间上界不许超过它，否则这一步会替模型造出指向不存在条目的节拍
    _counts = {"台词": len(plan_dialogue_lines(plan)),
               "旁白": len(plan_narration_entries(plan))}
    _changes, _new_beats, _start = [], [], {}
    for _i, _rec in enumerate(_records):
        _val = "".join(str(_rec.get(BEAT_SEG_CARRIER) or "").split())
        _m = _RANGE_CARRIER_RE.match(_val)
        _start[_i + 1] = len(_new_beats) + 1
        if not _m:
            _new_beats.append(_rec)
            continue
        _kind = _m.group(1)
        _a, _b = safe_decl_int(_m.group(2)), safe_decl_int(_m.group(3))
        if _a is None or _b is None:
            # 数字长得不像编号（超长串）：含义不唯一 ⇒ 不动，交契约门报"标注非法"
            _new_beats.append(_rec)
            continue
        if _b < _a or _a < 1 or _b > _counts.get(_kind, 0):
            _new_beats.append(_rec)            # 倒区间 / 上界越界：含义不唯一 ⇒ 不动，交规划器
            continue
        _parts = []
        for _k in range(_a, _b + 1):
            _part = BeatRecord.parse(_rec.render())   # 逐字复制，只改载体段
            _part.set_carrier("dialogue" if _kind == "台词" else "narration", _k)
            _parts.append(_part)
            _new_beats.append(_part)
        _changes.append({"beat": _i + 1, "range": _val,
                         "parts": list(range(_start[_i + 1], _start[_i + 1] + len(_parts)))})
    if not _changes:
        return plan, []

    # ---- ① 声明站点：节拍引用**只**允许出现在这两个字段里 ----
    _facts = plan.get("required_facts")
    _facts = _facts if isinstance(_facts, list) else []
    _sites = []                                # (事实下标, 段下标, 段文本)
    for _i, _f in enumerate(_facts):
        if not isinstance(_f, str):
            continue
        for _si, _seg in enumerate(_f.split("｜")):
            if _seg.strip().startswith(FACT_CARRIER_SEG):
                _sites.append((_i, _si, _seg))
    _hook = str(plan.get("hook_carrier") or "")

    # ---- ② 旧编号域核验：任何一处节拍号越界 ⇒ **整条计划不动** ----
    for _txt in [_seg for _, _, _seg in _sites] + ([_hook] if _hook else []):
        for _ref in iter_decl_refs(_txt):
            if _decl_ref_problem(_ref[4], len(_records)):
                return plan, []

    def _target(_b: int, _kindw, _idx) -> int:
        """一个 `Bn/…` 引用在展开后应当指向的节拍号（调用前已核验 `_b` 在旧域内）."""
        _s = _start.get(_b)
        if _s is None:
            return _b                          # 兜底：核验之后不该走到这里
        _ch = next((c for c in _changes if c["beat"] == _b), None)
        if _ch is None:
            _shift = sum(len(c["parts"]) - 1 for c in _changes if c["beat"] < _b)
            return _b + _shift                 # 前面的节拍被拆开 ⇒ 编号整体平移
        if _kindw in ("台词", "旁白") and _idx is not None:
            _first = int(_ch["range"].split("-")[0][len(_kindw):])
            _last = int(_ch["range"].split("-")[1])
            if _kindw == _ch["range"][:len(_kindw)] and _first <= _idx <= _last:
                return _ch["parts"][_idx - _first]   # 指向**真正承载它**的那一条
        return _s                              # 画面/不匹配 ⇒ 落在拆开后的第一条，交契约门判

    def _remap(_txt: str) -> str:
        """声明文本里的节拍号 → 新编号域；**只换数字那一段**，其余字符（含空白）原样.

        与旧域核验消费的是**同一份** `iter_decl_refs` 结果——不再是"另一套正则碰巧很像"。
        """
        _refs = iter_decl_refs(_txt)
        if not _refs:
            return _txt
        _buf, _pos = [], 0
        for _s_, _e_, _ns, _ne, _b, _kindw, _idx in _refs:
            _buf.append(_txt[_pos:_ns])
            _buf.append(str(_target(_b, _kindw, _idx)))
            _pos = _ne
        _buf.append(_txt[_pos:])
        return "".join(_buf)

    # ---- ③ 应用：新节拍表 + 声明字段重映射（其余字段一个字节不动） ----
    _out = dict(plan)
    _out["shot_beats"] = [r.render() for r in _new_beats]
    if _sites:
        _new_facts = list(_facts)
        for _i, _si, _seg in _sites:
            _segs = str(_new_facts[_i]).split("｜")
            _segs[_si] = _remap(_seg)
            _new_facts[_i] = "｜".join(_segs)
        _out["required_facts"] = _new_facts
    if _hook:
        _out["hook_carrier"] = _remap(_hook)
    return _out, _changes


#: 事实条目里的承载段（`｜承载：B2/台词1`）。**写进计划本体**而不是只留在 ops 里，
#: 否则 `render_execution_contract` 传不给生成器（2026-09-30 复核必要条件 2）。
FACT_CARRIER_SEG = "承载："

#: 钩子「必须说出口」的**显式契约字段**（2026-09-30 复核第 4 轮 P2）.
#: 值为**逐字台词**数组，必须是 `dialogue_lines` 的子集；没有必须说出口的台词
#: （纯画面钩子 / 引文只是被提到）→ 空数组。代码不再从 `end_hook` 的自由文本里猜。
HOOK_SPOKEN_FIELD = "hook_spoken"


class PlanContractInvalidError(Exception):
    """**计划契约未完成**：必保留事实/钩子缺承载、或承载无效.

    与 `PlanInfeasibleError` 的分工（2026-09-30 复核）：`infeasible` = 「故事装不下」
    ⇒ 触发预算重选；契约未完成 = 「取舍还没做完」⇒ **不**送预算重选、**不**报
    「需拆集」，停在计划层由用户裁决。刻意不继承 `RuntimeError`（同
    `PlanInfeasibleError`）：候选循环的 `except RuntimeError` 是"生成失败→重试"
    语义，契约问题是确定性的，重试没有意义。
    """


#: 诊断产物的**停止层**——写进产物本体，读取方按它归层，**不按文件名猜**
#: （2026-10-01 复核 P2：按文件名归层会把候选层的台词失败记成计划层失败）。
STOP_LAYER_PLAN = "plan"
STOP_LAYER_CANDIDATE = "candidate"
#: 终稿层（候选层之后的 finalize 阶段）——同一条硬门在两个层各跑一次，
#: 停止层要跟着**实际那一层**走（复核 2026-10-01：「候选／终稿诊断……被误归层」）
STOP_LAYER_FINAL = "final"


#: 旧产物（无 `stop_layer` 字段）按文件名的回退映射——**只服务于历史留档**，
#: 新产物一律以产物体内的 `stop_layer` 为准
_LEGACY_LAYER_BY_FILE = {
    "step01_plan_contract_invalid.json": STOP_LAYER_PLAN,
    "step01_plan_infeasible.json": STOP_LAYER_PLAN,
    "step01_dialogue_incomplete.json": STOP_LAYER_CANDIDATE,
    "step01_plan_transform_rejected.json": STOP_LAYER_PLAN,
}


def diagnostic_stop_layer(diag, filename: str = "") -> str:
    """诊断产物 → **真实停止层**（读取方单源）.

    优先取产物体内的 `stop_layer`（新产物都写）；旧产物没有该字段时按**文件名**回退，
    回退表只覆盖已知的三份诊断。两者都判不出 ⇒ 返回 ""（**显式未知**，不猜成某一层
    ——2026-10-01 复核：按文件名猜会把候选层的台词失败记成计划层失败）。
    """
    if isinstance(diag, dict):
        _s = str(diag.get("stop_layer") or "").strip()
        if _s:
            return _s
    return _LEGACY_LAYER_BY_FILE.get(str(filename or "").rsplit("/", 1)[-1], "")


#: 三份诊断的文件名——**统一登记**（跑批工具按它清场，见 `select_diagnostic`）.
#: 2026-10-01 复核第 11 轮：清场只删了前两份，台词诊断的旧文件仍能被当成"本轮结果"，
#: 于是把上一轮的终稿层失败报成本轮结论。
DIAGNOSTIC_FILES = (
    "step01_plan_contract_invalid.json",
    "step01_plan_infeasible.json",
    "step01_dialogue_incomplete.json",
)

#: **附属取证**（工作包 §3：拒收候选的取证包）——它不是"本轮的结论"，是**证据**。
#: 角色分工（复核第 43 轮 P3）：主诊断说结论（问题清单／预算读数），取证包给候选与基线
#: 两份计划、各自指纹与实际跑到的验收输入；但两者走**同一套**认领／归档／清场流程
#: （跑批工具消费 `DIAGNOSTIC_ARTIFACT_FILES`）——否则它不被归档、清场清不掉，
#: 后来的同名写入还会把它覆盖（实测：清场后仍留在章节目录）。
DIAGNOSTIC_EVIDENCE_FILES = (
    "step01_plan_transform_rejected.json",
)
#: 诊断类产物的**全集**（结论 + 取证）：认领／归档／清场用它；
#: 「本轮停在哪个文件」只从 `DIAGNOSTIC_FILES` 里选（主诊断才有结论角色）
DIAGNOSTIC_ARTIFACT_FILES = DIAGNOSTIC_FILES + DIAGNOSTIC_EVIDENCE_FILES

#: `written_at` 的两种已知写法（`_step01_now()` 是 `%Y-%m-%dT%H:%M:%S`，本地时区、秒级）
_DIAG_TIME_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _now_epoch(written_at):
    """诊断自报的 `written_at` → epoch 秒（判不出 → `None`，**不猜**）.

    秒级精度意味着"恰好落在运行起始同一秒之前"的旧产物仍可能通过时间判据，所以
    调用方**必须**先清场——跑批工具在启动前归档并删除 `DIAGNOSTIC_FILES`。
    """
    import time as _time
    _s = str(written_at or "").strip()
    if not _s:
        return None
    for _fmt in _DIAG_TIME_FMTS:
        try:
            return _time.mktime(_time.strptime(_s, _fmt))
        except ValueError:
            continue
    return None


def select_diagnostic(diags, *, since: float = 0.0, expect_run_id: str = "") -> dict:
    """从候选诊断里挑出**本轮**那一份，并把它的身份与停止层一起给出（读取方单源）.

    `diags` 是 `{文件名: 已读到的 dict}` 或 `((文件名, dict), …)`，按**给出顺序**取优先；
    `since` 是本轮运行的起始时刻（epoch 秒；`0` = 不知道运行边界，只按"存在"取）；
    `expect_run_id` 是本轮**请求身份**（跑批驱动先固定，见 `main._step01_run_id`）。

    返回 `{"file", "diag", "stop_layer", "run_id", "written_at", "plan_contract_version",
            "stale_files", "foreign_files", "time_conflicts", "mixed_run_ids", "reason"}`。

    **归属判据**（2026-10-01 复核第 12 轮）：
    - 给了 `expect_run_id` ⇒ **身份优先**：`run_id` 对不上的进 `foreign_files`（不采用）；
      时间**不参与取舍**，只把"身份对得上但时间看着不对"的列进 `time_conflicts` 作辅助检查；
    - 没给身份 ⇒ 退回时间判据（旧行为），判不出的进 `stale_files`。

    2026-10-01 复核第 11 轮的两处修正：
    ① **身份与停止层取自同一份**——旧实现停止层取自台词诊断、身份取自计划层诊断，
       只有台词诊断时汇总就写出"停止层=final、run_id 空、契约版本=None"；
    ② **本轮判据用产物自报的 `written_at`**，不是文件 mtime 的秒级余量——旧实现留
       2 秒余量，启动前 0.5 秒写下的上一轮诊断照样被当成本轮结果。
    `written_at` 取不到/判不出的产物**不算本轮**（身份 fail-closed，显式未知不猜）。
    """
    _items = list(diags.items()) if isinstance(diags, dict) else list(diags or ())
    _present = [(_n, _d) for _n, _d in _items if isinstance(_d, dict) and _d]
    _fresh, _stale, _foreign, _conflict = [], [], [], []
    _floor = float(int(since)) if since else 0.0
    _want = str(expect_run_id or "").strip()
    for _n, _d in _present:
        _ep = _now_epoch(_d.get("written_at"))
        if _want:
            # 身份优先：对不上就是别的轮写的（批内跨轮复用也走这条）
            if str(_d.get("run_id") or "") != _want:
                _foreign.append(_n)
                continue
            # 时间只做辅助检查：身份说"是本轮"，时间却早于本轮起点，值得显式指出
            if since and _ep is not None and _ep < _floor:
                _conflict.append(_n)
            _fresh.append((_n, _d))
            continue
        if since and (_ep is None or _ep < _floor):
            _stale.append(_n)
            continue
        _fresh.append((_n, _d))
    if not _fresh:
        _why = []
        if _foreign:
            _why.append("身份不是本轮：%s" % "、".join(sorted(_foreign)))
        if _stale:
            _why.append("陈旧：%s" % "、".join(sorted(_stale)))
        return {"file": "", "diag": {}, "stop_layer": "", "run_id": "",
                "written_at": "", "plan_contract_version": None,
                "stale_files": sorted(_stale), "foreign_files": sorted(_foreign),
                "time_conflicts": [], "mixed_run_ids": [],
                "reason": ("没有本轮诊断（%s）" % "；".join(_why) if _why
                           else "没有任何诊断产物")}
    _file, _diag = _fresh[0]
    _run_id = str(_diag.get("run_id") or "")
    _mixed = sorted(_n for _n, _d in _fresh
                    if str(_d.get("run_id") or "") and str(_d.get("run_id")) != _run_id)
    _pcv = _diag.get("plan_contract_version")
    if _pcv is None and isinstance(_diag.get("plan"), dict):
        _pcv = _diag["plan"].get("plan_contract_version")
    return {"file": _file, "diag": _diag,
            "stop_layer": diagnostic_stop_layer(_diag, _file),
            "run_id": _run_id, "written_at": str(_diag.get("written_at") or ""),
            "plan_contract_version": _pcv,
            "stale_files": sorted(_stale), "foreign_files": sorted(_foreign),
            "time_conflicts": sorted(_conflict), "mixed_run_ids": _mixed,
            "reason": ("本轮停在 %s" % _file if not _mixed
                       else "同一轮混有不同 run_id 的诊断：%s" % "、".join(_mixed))}


def _dump_diagnostic_json(path, payload, *, what: str) -> bool:
    """诊断落盘的**唯一出口**（准则 7：失败必须看得见）.

    三份诊断共用：写盘异常一律**告警**，不让任何一处再退回静默的 `except: pass`
    （2026-10-01 复核第 11 轮：台词诊断的写盘失败仍是静默的；契约诊断一度因此
    "一个字都没写出来"而没人发现）。返回是否写入成功。
    """
    try:
        from pathlib import Path
        from shared.json_io import atomic_dump_json
        atomic_dump_json(Path(path), payload, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:                      # noqa: BLE001
        try:
            _log().warn("[PLAN] %s 落盘失败（%s: %s）——本次判定没有留档"
                        % (what, type(exc).__name__, exc))
        except Exception:
            pass
        return False


def _diag_identity(stop_layer: str, plan=None) -> dict:
    """诊断产物的公共身份四件套：跑批身份 + 契约版本 + **真实停止层**.

    `run_id` 取不到时留空串（显式空值，不伪造）；读取方可据此判断「这份诊断属于本轮哪一层」。
    """
    _run_id, _now = "", ""
    try:
        from step01_script_gen.main import _step01_now, _step01_run_id
        _run_id = str(_step01_run_id() or "")
        _now = str(_step01_now() or "")
    except Exception as exc:                      # noqa: BLE001
        # 取不到就显式说出来：读取方把"没有 written_at"当作**不属于本轮**，静默留空
        # 会让本轮诊断被悄悄丢弃（准则 7）。
        try:
            _log().warn("[PLAN] 诊断身份取不到（%s: %s）——该诊断没有 run_id/written_at，"
                        "读取方会判它不属于本轮" % (type(exc).__name__, exc))
        except Exception:
            pass
    return {"run_id": _run_id, "written_at": _now, "stop_layer": stop_layer,
            "plan_contract_version": _plan_contract_version(plan)}


def strip_fact_carrier(fact) -> str:
    """去掉事实条目里的承载段（不变量比对用：加承载 ≠ 改了事实本体）."""
    parts = str(fact or "").split("｜")
    return "｜".join(p for p in parts if not p.strip().startswith(FACT_CARRIER_SEG))


def fact_carrier(fact) -> str:
    """取事实条目的承载声明正文（无 → ""）."""
    for p in str(fact or "").split("｜"):
        if p.strip().startswith(FACT_CARRIER_SEG):
            return p.strip()[len(FACT_CARRIER_SEG):].strip()
    return ""


def parse_carrier_decl(decl):
    """`B2/台词1` → `(2, "台词", 1)`；`B3/画面` → `(3, "画面", 0)`；
    `B2/旁白1` → `(2, "旁白", 1)`；非法 → None.

    **与重映射器同一文法**（第 28 轮复核）：整条声明去掉空白后必须**恰好是一个**引用，
    且首尾没有别的内容。判据来自 `iter_decl_refs`——这里不再有第二套正则，两边不可能
    再各说各话（旧实现自带的 `^B(数字)/(...)$` 与重映射器的空白名单漂移：`B 2/画面`
    验收接受、重映射漏认，一份合法计划被改坏）。
    """
    _txt = str(decl or "")
    _refs = iter_decl_refs(_txt)
    if len(_refs) != 1:
        return None
    _a, _b_, _ns, _ne, _beat, _kind, _idx = _refs[0]
    if _txt[:_a].strip() or _txt[_b_:].strip():
        return None
    return _beat, _kind, (0 if _kind == "画面" else _idx)


def norm_carrier_text(s) -> str:
    """承载比对用归一：只留字（去空白与常见中英标点）."""
    _drop = " " + chr(9) + chr(13) + chr(10) + "，。！？、；：“”‘’「」…,.!?;:"
    return "".join(ch for ch in str(s or "") if ch not in _drop)


#: 承载「表达」判定的两个**既有**窗口——与 `shares_run` / `shares_terms` 的调用处同值，
#: 抽成具名常量只为在「逐分句覆盖」里复用同一个数：**不是新阈值**。
CARRIER_RUN = 6
CARRIER_TERMS = 2

#: 事实「原文证据」按**原文自己的标点**切分（判据的最小单位是分句）。
_FACT_CLAUSE_SPLIT = re.compile("[，。；：、！？,.;:!?]+")


def fact_evidence_text(fact) -> str:
    """事实的「原文证据」引文正文（无 → ""）——这条事实的**原文出处**."""
    from shared.fact_metadata import split_claim_evidence
    return split_claim_evidence(fact)[1]


def fact_evidence_clauses(fact) -> list:
    """「原文证据」切成**分句**（归一后保序去重）——判据逐分句过，不看整条的"有没有重合"."""
    _out: list = []
    for _c in _FACT_CLAUSE_SPLIT.split(fact_evidence_text(fact)):
        _n = norm_carrier_text(_c)
        if _n and _n not in _out:
            _out.append(_n)
    return _out


def uncovered_fact_clauses(fact, carrier_text, *, verbatim: bool) -> tuple:
    """载体**没有表达**的「原文证据」分句 → `(缺失分句, 被判分句数)`.

    「表达」用**各载体既有那条关系**，不新造阈值：
      - 逐字类（台词 / 旁白：正文按契约逐字来自原文）⇒ `shares_run`（≥`CARRIER_RUN` 字连续）；
      - 转述类（画面节拍正文）⇒ `shares_terms`（≥`CARRIER_TERMS` 个不同二字要素）。

    比该关系**自身窗口**还短的分句**不判**（逐字类 < `CARRIER_RUN` 字、转述类 < 3 字）：
    它们本来就无法被这条关系判定——跳过是"判不了"，不是"判为没表达"；跳过量由调用方可见。
    """
    _ct = norm_carrier_text(carrier_text)
    _missing: list = []
    _judged = 0
    _floor = CARRIER_RUN if verbatim else 3
    for _c in fact_evidence_clauses(fact):
        if len(_c) < _floor:
            continue
        _judged += 1
        _hit = (shares_run(_c, _ct, CARRIER_RUN) if verbatim
                else shares_terms(_c, _ct, CARRIER_TERMS))
        if not _hit:
            _missing.append(_c)
    return _missing, _judged


def _incomplete_reason(i, what, carrier_text, missing):
    """部分缺失的**统一文案**（三种载体共用，只有「什么载体」不同）.

    ⚠️ **建议的修法必须与接口能力一致**（R3）：原型文案写的是「表达不了就把它**拆成两条
    事实**」，而**没有任何一轮接口能改事实正文**——`ops` 没有事实正文编辑能力，
    `plan_revision.v2` 把 `frozen_facts[].claim` 显式冻结。给出做不到的建议，模型照做就
    必然被拒收（实测：模型按建议拆了事实，下一轮被「核心字段被改动」打回，白费一轮）。
    正确的建议是**拆载体、保留同一 fact_uid**：让另一个载体（另一条台词、旁白，或新模式
    下的画面/卡片）去承接剩下的主张。
    """
    return ("F%d 声明的承载%s只表达了这条事实的**一部分**——它的「原文证据」里这几处"
            "没有任何正文表达：%s（该载体正文：「%s」）——修法是**换/加一个载体去承接"
            "剩下的主张**（另一个台词/旁白，或新模式下的画面/文字卡），"
            "并把 F%d 的承载改指向它；**不要拆事实**（正文主张是冻结的，"
            "本轮接口没有任何一条能改它）" % (i, what, "；".join(missing),
                                              str(carrier_text)[:24], i))


def shares_run(a, b, n: int = 6) -> bool:
    """两段文本是否有 **≥n 字的连续公共子串**——**两侧都是原文原句**时用（事实↔台词）.

    用于「这条事实与**选中的那句台词**是不是同一件事」：台词是逐字摘的原文，事实的
    「原文证据」也是逐字引文，故连续命中是有意义的判据。

    ⚠️ 2026-09-30 复核：**不许有「包含即通过」的捷径**。旧实现先判 `a in b or b in a`
    就返回 True，于是两字台词「杨旭」——它当然是事实文本的子串——能绕过 6 字要求。
    现在统一按「存在长度 ≥ n 的连续公共子串」判，短于 n 的一侧必然不通过。
    """
    _a, _b = norm_carrier_text(a), norm_carrier_text(b)
    if len(_a) < n or len(_b) < n:
        return False
    return any(_a[i:i + n] in _b for i in range(len(_a) - n + 1))


def carrier_terms(text, n: int = 2) -> set:
    """承载比对用的「要素」：**字序列上的 n 字片段**（默认 2 字）."""
    _t = norm_carrier_text(text)
    return {_t[i:i + n] for i in range(len(_t) - n + 1)}


def shares_terms(a, b, need: int = 2) -> bool:
    """两段文本是否共享 **≥need 个不同的二字要素**——**转述 vs 引文**时用（事实↔画面节拍）.

    为什么不用「连续公共子串」：节拍正文是**转述**（"黄昏水湾，杨旭吹箫、听香撑伞立于
    船头"），与原文引文的连续交集实测只有 2-3 字（2026-09-30 归档计划实测：ch1
    3/6 条事实的最长交集 ≤3 字），拿 ≥6 字判会把**合法承载**一起拒收。二字要素
    则稳定：上例共享「杨旭／听香／水湾」三个要素。

    复核反例（必须拒收）仍被拦住：F2「四年前四人奉命离开应天府潜入青州」↔ 只写
    「四人围桌喝茶」的画面节拍，只共享「四人」**一个**要素。
    """
    _a, _b = carrier_terms(a), carrier_terms(b)
    if not _a or not _b:
        return False
    return len(_a & _b) >= int(need)



#: 「原文依据/原文证据/end_hook」里的引文。**提示词**固定要求全角 “ ”，但**模型实际会**
#: 用 ‘ ’、" "、「 」甚至只给一个开引号（2026-09-29 章 2 实测：引文 12 字在原文里
#: 逐字存在（idx 2551），却因为写成 ‘吃的灯草灰，放的轻巧屁！ 被 `findall` 一个都
#: 没抓到 → 判「逐字核不过」→ **整批重选操作丢弃**（含能修好超时的台词替换）→
#: 计划永远不可行）。这里把常见引号风格都认下来，并允许**未闭合**的开引号（截到
#: 「｜」/「|」或行尾）。**证据标准不变**：仍是 ≥8 字连续逐字命中。
_EVIDENCE_QUOTE = re.compile(
    '[“"‘「『]'
    '([^”"’」』]{4,})'
    '[”"’」』]'
    '|'
    '[“"‘「『]'
    '([^”"’」』｜|]{4,})'
)


def evidence_quotes(text) -> list:
    """取出「原文依据/原文证据/end_hook」里的引文正文（成对引号或未闭合开引号）."""
    out: list = []
    for _m in _EVIDENCE_QUOTE.finditer(str(text or "")):
        _q = _m.group(1) or _m.group(2) or ""      # 第1组=成对引号；第2组=未闭合
        if _q:
            out.append(_q)
    return out


def hook_spoken_lines(plan) -> list:
    """`hook_spoken` 字段的规范化读数（非法/空条目一律丢弃，保序去重）.

    字段缺失 → `[]`（钩子没有必须说出口的台词）。字段**存在但形态非法**由
    `plan_contract_problems` 显式报错，这里只做读数（读数不许抛异常）。
    """
    _v = (plan or {}).get(HOOK_SPOKEN_FIELD) if isinstance(plan, dict) else None
    if isinstance(_v, str):          # 单句写成裸字符串：按一句读（schema 要求数组）
        _v = [_v] if _v.strip() else []
    if not isinstance(_v, list):
        return []
    out: list = []
    for _x in _v:
        if isinstance(_x, str) and _x.strip() and _x.strip() not in out:
            out.append(_x.strip())
    return out


def hook_spoken_quotes(plan) -> list:
    """钩子**要求说出口**的台词 = 计划契约字段 `hook_spoken`（2026-09-30 复核第 4 轮）.

    旧实现靠「说话标记关键词 + 引文前 8 字窗口」从 `end_hook` 的自由文本里**猜**哪句
    必须说出口。复核给了三个当场可复现的反例（引导语落在窗口外 / 引文里自带「问」字 /
    两句共用一句引导语），结论是这类判据换多少启发式都还是猜。通用做法是把这件事
    **写成契约字段**：规划层显式声明，代码只核验——
      · 原文：`hook_spoken` 必须是 `dialogue_lines` 的子集，而后者已过逐字核验；
      · 选用台词：整句在 `dialogue_lines` 里（`hook_carried_line`）；
      · 承载关系：`hook_carrier` 必须为它声明承载节拍，且与该节拍自己的载体标注一致。
    """
    return hook_spoken_lines(plan)


def hook_carried_line(plan, quote=None) -> int:
    """`hook_spoken` 里这一句在 `dialogue_lines` 里的编号（**整句相等**）；不在 → 0.

    2026-09-30 复核第 5 轮 P2-3：旧实现用**子串包含**（`_q in _l`），于是声明
    「拿主意」能匹配完整台词「拿主意？拿什么主意？」——与「逐字台词、必须是
    `dialogue_lines` 的子集」这条契约不一致（等于又给了「只说开头几个字」一条生路）。
    现在按**完整台词**比对（只做空白/标点归一）。

    不给 `quote` 时按第一条要求说出口的台词（历史调用点的语义）。
    """
    _q = norm_carrier_text(quote if quote is not None else
                           (hook_spoken_quotes(plan) or [""])[0])
    if not _q:
        return 0
    for _i, _line in enumerate(plan_dialogue_lines(plan), 1):
        if norm_carrier_text(_line) == _q:
            return _i
    return 0


def hook_spoken_quote(plan) -> str:
    """（历史入口）`end_hook` 要求说出口的**第一条**引文；未要求 → ""."""
    _qs = hook_spoken_quotes(plan)
    return _qs[0] if _qs else ""


#: 从这一版起，`hook_spoken` 必须**存在且为字符串数组**（兼容只依据明确版本）
HOOK_SPOKEN_MIN_VERSION = 3


def _plan_contract_version(plan) -> int:
    """计划自带的契约版本（缺失/非法 → 0 = 早于该字段的旧计划）."""
    try:
        return int((plan or {}).get("plan_contract_version") or 0)
    except (TypeError, ValueError):
        return 0


def _hook_spoken_field_records(plan) -> list:
    """`hook_spoken` 字段**自身**的形态核验——**按明确版本**决定口径.

    2026-09-30 复核第 5 轮 P2-3：消费入口不能因为「字段缺失」就退回旧口径。v3 及
    之后的计划必须显式给出该字段（没有必须说出口的台词就给空数组）；裸字符串只对
    **标注了旧版本**的计划兼容。规则演进期的老计划带版本标注（缓存按版本迁移），
    所以「缺字段」在新稿上就是**计划不合格**，而不是「等价于没有钩子要求」。
    """
    _B = "钩子：`hook_spoken` / `hook_carrier` 的声明自身先无条件核验，再核验覆盖"
    if not isinstance(plan, dict):
        return []
    _ver = _plan_contract_version(plan)
    _strict = _ver >= HOOK_SPOKEN_MIN_VERSION
    if HOOK_SPOKEN_FIELD not in plan:
        if _strict:
            return [_problem_record("carrier_hook_spoken_missing", "hook", None, "hook_spoken", "`hook_spoken` 缺失：v%d 计划必须显式声明钩子要求**说出口**的台词"
                        "（没有就填空数组 `[]`），不能靠「没写」表示没有要求" % _ver, _B)]
        return []
    _v = plan.get(HOOK_SPOKEN_FIELD)
    if isinstance(_v, str):
        if _strict:
            return [_problem_record("carrier_hook_spoken_type", "hook", None, "hook_spoken", "`hook_spoken` 必须是字符串数组（v%d 计划不接受裸字符串）：%s"
                        % (_ver, _v[:20]), _B)]
        return []                      # 旧版本标注的计划：兼容读法
    if not isinstance(_v, list):
        return [_problem_record("carrier_hook_spoken_type", "hook", None, "hook_spoken",
                                "`hook_spoken` 必须是字符串数组（现在是 %s）——逐条给钩子要求"
                                "**说出口**的台词原文" % type(_v).__name__, _B)]
    _bad = [x for x in _v if not isinstance(x, str) or not x.strip()]
    if _bad:
        return [_problem_record("carrier_hook_spoken_item", "hook", None, "hook_spoken", "`hook_spoken` 里有空/非法条目（%r）——必须是原文逐字台词"
                        % (str(_bad[0])[:20],), _B)]
    return []




def _problem_record(code, kind, index, path, reason, basis="", fix="",
                    fact_uid="") -> dict:
    """**问题记录**：`code / kind / index / path / reason / basis` + **修法能力** `fix`.

    身份（`kind` 与 `index`）与路径由**生产者**在遍历当前计划时给出——报告层只组合，
    不再从文案里认编号（复核第 33/34 轮：那是"从正文猜编号"）。`detail` 作为兼容别名
    与 `reason` 同值。

    `fix`（复核 2026-10-02 之九 P1）：**这条问题需要哪种修法**——`""` = 操作协议
    （只改已有条目的内容/编号/承载声明）就能表达；`"reselect_dialogue"` = 必须**新增
    或删除台词条目**，操作协议**表达不了**（`dialogue_ops` 只能替换已有编号）。修订
    分流按它决定走操作式还是**受约束的完整计划修订**。没有这个字段，分流只能按 code
    前缀猜（旧写法按"是不是预算问题"分，于是"空台词表"这种内容缺失被送进只能替换的接口）。
    """
    _rec = {"code": code, "kind": kind, "index": index, "path": path,
            "reason": reason, "detail": reason, "basis": basis, "fix": fix}
    # **被修改实体 vs 受影响事实**（复核 2026-10-08 P1）：`kind`/`uid` 说的是**该改谁**
    # （卡片就是卡片、声音单位就是单位），`fact_uid` 另记"这条事实受影响"。
    # 旧写法把空卡片告警写成 `kind=fact, uid=f01`——消费者按 `uid` 找卡片就找错实体
    # （实测：槽位 `slot:card:f01` → 合法回答被判 `repair_slot_target_missing`）。
    if fact_uid:
        _rec["fact_uid"] = str(fact_uid)
    return _rec


def _legacy_problems(records) -> list:
    """兼容转发（工作包 §2.2）：旧接口仍返回**字符串清单**，由结构化记录**渲染**得到；
    反向（把旧字符串解析回记录）不做。
    """
    return [str(r.get("reason") or "") for r in (records or [])]


def _narration_field_records(plan) -> list:
    """`narration_lines` **字段形态**核验（无条件）.

    2026-09-30 复核第 6 轮：旁白可选（空数组 = 本集无旁白），但**形态非法**必须显式
    报错——schema 降级通道（json_schema → json_object）下，模型可能把它写成字符串/
    标量，而读数函数会当"没有旁白"静默放过，等于旁白链在计划层凭空消失。
    """
    if not isinstance(plan, dict) or NARRATION_LINES_FIELD not in plan:
        return []
    _v = plan.get(NARRATION_LINES_FIELD)
    if not isinstance(_v, list):
        return [_problem_record("carrier_narration_field", "plan", None, "narration_lines", "`%s` 必须是对象数组（每项 {text, source, fact_refs}），现在是 %s"
                % (NARRATION_LINES_FIELD, type(_v).__name__), "旁白：`narration_lines` 必须显式给出、形态合规（画外音链不许在计划层凭空消失）")]
    problems: list = []
    _basis = "旁白：`narration_lines` 必须显式给出、形态合规（画外音链不许在计划层凭空消失）"
    def _add(code, kind, index, path, reason):
        problems.append(_problem_record(code, kind, index, path,
                                        reason, _basis))
    for _i, _e in enumerate(_v, 1):
        if not isinstance(_e, dict):
            _add("carrier_narration_item", "narration", _i, "narration_lines[%d]" % (_i - 1), "`%s` 第 %d 项不是对象（%r）——旁白必须写清正文与出处"
                            % (NARRATION_LINES_FIELD, _i, str(_e)[:20]))
            continue
        if not str(_e.get("text") or "").strip():
            _add("carrier_narration_item", "narration", _i, "narration_lines[%d]" % (_i - 1), "`%s` 第 %d 项缺 `text`（旁白正文）" % (NARRATION_LINES_FIELD, _i))
        if not str(_e.get("source") or "").strip():
            _add("carrier_narration_item", "narration", _i, "narration_lines[%d]" % (_i - 1), "`%s` 第 %d 项缺 `source`（出处原文）" % (NARRATION_LINES_FIELD, _i))
        # 2026-09-30 复核第 7 轮 P2：「`fact_refs=[]`、`null`、非法字符串元素都通过；
        # 浮点数和布尔值还会被转成整数引用」。字段形态是契约的一部分——**无条件**核：
        # 必须是数组，元素必须是**真正的整数**（`True`/`2.0`/`"2"` 一律不算）。
        if "fact_refs" not in _e:
            _add("carrier_narration_fact_refs", "narration", _i, "narration_lines[%d].fact_refs" % (_i - 1), "`%s` 第 %d 项缺 `fact_refs`（这条旁白讲出的必保留事实"
                            "序号；没有就写空数组 `[]`）" % (NARRATION_LINES_FIELD, _i))
            continue
        _refs = _e.get("fact_refs")
        if not isinstance(_refs, list):
            _add("carrier_narration_fact_refs", "narration", _i, "narration_lines[%d].fact_refs" % (_i - 1), "`%s` 第 %d 项的 `fact_refs` 必须是**整数数组**（现在是 %s）"
                            "——写 `[]` 表示这条旁白不承载任何事实"
                            % (NARRATION_LINES_FIELD, _i, type(_refs).__name__))
            continue
        _bad = [x for x in _refs if isinstance(x, bool) or not isinstance(x, int)]
        if _bad:
            _add("carrier_narration_fact_refs", "narration", _i, "narration_lines[%d].fact_refs" % (_i - 1), "`%s` 第 %d 项的 `fact_refs` 里有非整数项（%r）——必须写"
                            "整数序号（`2`，不是 `\"2\"`/`2.0`/`true`）"
                            % (NARRATION_LINES_FIELD, _i, str(_bad[0])[:12]))
    return problems


def narration_source_records(entries, novel_text: str) -> list:
    """旁白**出处**核验——`source` 不是「必填文字」（复核第 7 轮 P2）.

    复核的反例：把 `source` 改成"不存在的出处"，契约门仍通过，正文仍被列为已核验旁白。
    两条确定性判据（零模型调用）：

      ① `source` 必须能在原文里**逐字**找到（与台词/旁白正文同一个 `_source_norm` 口径）；
      ② 正文必须**出自该出处**（归一化后 `text ⊆ source`）——只抄另一句原文当出处，
         证明不了这条旁白有依据。

    ⚠️ 判据边界（有意）：这里核的是「出处真实且覆盖正文」，**不是**「出处选得恰当」
    ——同一句原文里哪一段最该当旁白，仍归内容核验（准则 7：不把"没检出"当"没问题"，
    但也不把可判定的部分推给语义层）。
    """
    problems: list = []
    _basis = "旁白条目：`source` 必须逐字来自原文且覆盖正文"
    def _add(code, kind, index, path, reason):
        problems.append(_problem_record(code, kind, index, path,
                                        reason, _basis))
    novel_norm = _source_norm(novel_text)
    if not novel_norm:
        return problems
    for _i, _e in enumerate(entries or [], 1):
        _src = str(_e.get("source") or "").strip()
        if not _src:
            continue                      # 缺出处由形状门报，这里不重复计
        _sn = _source_norm(_src)
        _tn = _source_norm(str(_e.get("text") or ""))
        # 正文本身就不在原文里 ⇒ 归 `evaluate_plan` 的**逐字核验**（`narration_rejected`
        # → `_drop_unverified` 剔除并重映射引用）。契约门在剔除**之前**跑，若在这里
        # 也报一条，就会把「可剔除项」升级成「计划契约未完成」而整章停下——判据越界。
        if _tn and _tn not in novel_norm:
            continue
        if not _sn or _sn not in novel_norm:
            _add("source_narration_not_in_novel", "narration", _i, "narration_lines[%d]" % (_i - 1), "旁白 N%d 的 `source`（出处）在原文里**找不到**——出处必须"
                            "逐字来自原文，不能是任意文字：「%s」" % (_i, _src[:24]))
            continue
        if _tn and _tn not in _sn:
            _add("source_narration_not_in_source", "narration", _i, "narration_lines[%d]" % (_i - 1), "旁白 N%d 的正文**不在这条出处里**（出处另抄了别的原文）："
                            "正文「%s…」/ 出处「%s…」"
                            % (_i, str(_e.get("text"))[:16], _src[:16]))
    return problems


def _hook_carrier_decls(plan, beats, lines) -> tuple:
    """`hook_carrier` 解析 + **无条件**核验（格式/范围/重复/与节拍载体配对）.

    返回 `(decls, problems)`；`decls` 是合法声明 `(节拍号, 方式, 台词号)` 列表，
    供覆盖核验复用（解析与核验同一处，避免两套口径）。
    """
    _raw = [x.strip() for x in
            re.split("[;；,、]", str((plan or {}).get("hook_carrier") or "")) if x.strip()]
    problems: list = []
    _basis = "钩子：`hook_spoken` / `hook_carrier` 的声明自身先无条件核验，再核验覆盖"
    def _add(code, kind, index, path, reason):
        problems.append(_problem_record(code, kind, index, path,
                                        reason, _basis))
    decls: list = []
    seen: set = set()
    for _x in _raw:
        _d = parse_carrier_decl(_x)
        if _d is None:
            _add("carrier_hook_decl_invalid", "hook", None, "hook_carrier", "`hook_carrier` 里的「%s」不是合法声明（只能写 "
                            "`B{n}/台词k` 或 `B{n}/画面`）" % _x[:24])
            continue
        _b, _kind, _k = _d
        if (_b, _kind, _k) in seen:
            _add("carrier_hook_decl_duplicate", "hook", None, "hook_carrier", "`hook_carrier` 里「%s」重复声明" % _x[:24])
            continue
        seen.add((_b, _kind, _k))
        if not 1 <= _b <= len(beats):
            _add("carrier_hook_beat_missing", "beat", _b, "shot_beats[%d].载体" % (_b - 1), "`hook_carrier` 指向不存在的节拍 B%d（共 %d 条）"
                            % (_b, len(beats)))
            continue
        if _kind == "旁白":
            # 复核第 7 轮补口：旁白是**画外音**，承载不了「角色说出口」——
            # 旧实现把它归到「非台词 ⇒ 画面」那一支，报出的消息与实际声明不符。
            _add("carrier_hook_narration_not_spoken", "hook", None, "hook_carrier", 
                "`hook_carrier` 不能声明由**旁白**承载（「%s」）——`hook_spoken` 是"
                "「钩子里必须由**角色说出口**的台词」（必须是 `dialogue_lines` 的子集），"
                "旁白是画外音，不算说出口；请改成承载它的那条 `B{n}/台词k`，或把该句"
                "从 `hook_spoken` 里去掉" % _x[:24])
            continue
        if _kind == "台词" and not 1 <= _k <= len(lines):
            _add("carrier_hook_line_missing", "dialogue", _k, "dialogue_lines[%d]" % (_k - 1), "`hook_carrier` 指向不存在的台词 D%d（选用台词共 %d 句）"
                            % (_k, len(lines)))
            continue
        # 配对：声明必须与**该节拍自己标的载体**一致（声明与标注打架 = 计划自相矛盾）。
        # **2026-10-18（A 窄版）**：`hook_carrier = B{n}/画面` 允许落在**承声节拍**上——
        # 画面承载与声音归属可以同镜；`台词k` 仍必须由标着「台词k」的那条节拍承载。
        _own = BeatRecord.parse(beats[_b - 1]).carrier()
        _want = ("dialogue", _k) if _kind == "台词" else ("visual", None)
        _visual_ok = (_kind == "画面" and _own[0] in ("visual", "dialogue", "narration"))
        if _own != _want and not _visual_ok:
            _own_txt = ("没标载体" if _own[0] is None
                        else ("标注非法" if _own[0] == "invalid"
                              else ("台词%d" % _own[1] if _own[0] == "dialogue"
                                    else ("旁白%d" % _own[1] if _own[0] == "narration"
                                          else "画面"))))
            _want_txt = "画面" if _kind == "画面" else "台词%d" % _k
            _add("carrier_hook_carrier_mismatch", "beat", _b, "shot_beats[%d].载体" % (_b - 1), 
                "`hook_carrier` 声明由 B%d 的「%s」承载，而该节拍自己标的是「%s」"
                "——两处必须一致" % (_b, _want_txt, _own_txt))
            continue
        decls.append(_d)
    return decls, problems


def narration_source_problems(entries, novel_text: str) -> list:
    """兼容转发：旧接口的字符串清单（由 `narration_source_records` 渲染）."""
    return _legacy_problems(narration_source_records(entries, novel_text))


def _narration_field_problems(plan) -> list:
    """兼容转发（旧名）：字符串清单."""
    return _legacy_problems(_narration_field_records(plan))


def _hook_spoken_field_problems(plan) -> list:
    """兼容转发（旧名）：字符串清单."""
    return _legacy_problems(_hook_spoken_field_records(plan))


def plan_contract_problems(plan, *, novel_text: str = "",
                          require_verification: bool = True) -> list:
    """兼容转发：旧接口的**问题文案清单**（由 `plan_contract_records` 渲染）.

    新链（`outline.plan_acceptance_problems`）直接消费 `plan_contract_records`
    的结构化记录；这里保留旧形态给既有调用方与日志。
    """
    return _legacy_problems(plan_contract_records(
        plan, novel_text=novel_text,
        require_verification=require_verification))


def plan_contract_records(plan, *, novel_text: str = "",
                        require_verification: bool = True) -> list:
    """**计划契约**验收：必保留事实与钩子是否有**有效承载**；返回问题清单（空=通过）.

    2026-09-30 复核裁决：承载关系必须是**计划的一部分**，在生成前的统一入口核对——
    首版、重选版、缓存版走**同一道门**（不接受"只有重选受约束"）。

    核对什么（复核原话：核对实际节拍正文／信息目的及选用台词）：

    - **台词承载**：事实与**选中的那句台词**必须有实质交集，且承载节拍标的载体就是它；
    - **画面承载**：事实与承载节拍的**正文或信息目的**必须有实质交集，且该节拍标
      「载体：画面」（一条节拍同时要承载画面与台词时先 `split`）；
    - **不比对节拍的「原文依据」**——把事实引文抄进一个拍别的东西的节拍，正是复核
      给的反例：引文相同只证明"引的是同一句原文"，证明不了观众能从画面得知这条事实。
      「信息目的」是**声明**，单独成立不算画面演出，故与正文**同权**参与比对
      （审计仍是第二道防线）。
    - **钩子**：`end_hook` 明确要求说出口时，那句必须**整句**在 `dialogue_lines` 里，
      并有节拍承载（`hook_carrier`）。
    """
    if not isinstance(plan, dict) or not plan:
        return []            # 计划缺失/字段缺失由 `evaluate_plan` 的 fail-closed 判
    # **按模式分派**（主专题 §2）：v6 的 `screen_hybrid` 读**结构化关系**
    # （`presentation.fact_bindings`），不再解析 `｜承载：…` 文案——旧文案在新模式里
    # 只是显示，不另作权威判据。v5 走下面的旧路径，一个字都不改。
    if _plan_contract_version(plan) >= PRESENTATION_MIN_VERSION:
        return presentation_records(plan, novel_text=novel_text,
                                verification=(plan or {}).get("plan_verification"),
                                require_verification=require_verification)
    from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
    _semantic = LEGACY_ACCEPTANCE_FIELD in plan
    if _semantic and plan[LEGACY_ACCEPTANCE_FIELD] != LEGACY_ACCEPTANCE_VERSION:
        return [_problem_record("planning_acceptance_version_invalid", "plan", None,
                                LEGACY_ACCEPTANCE_FIELD, "未知的计划验收版本") ]
    facts = [str(f) for f in (plan.get("required_facts") or [])]
    if not facts:
        return []            # 必保留事实清单本身为空 = 计划不合格，归 `_validate_outline`
    beats = [str(b) for b in (plan.get("shot_beats") or [])]
    lines = plan_dialogue_lines(plan)
    problems: list = []
    _basis = "计划契约：每条必保留事实与钩子都必须有**有效承载**（事实的「承载：Bn/…」与节拍上的「载体：…」必须互指）"
    def _add(code, kind, index, path, reason, fix=""):
        problems.append(_problem_record(code, kind, index, path,
                                        reason, _basis, fix=fix))
    for i, fact in enumerate(facts, 1):
        _decl = fact_carrier(fact)
        if not _decl:
            _add("carrier_fact_missing", "fact", i, "required_facts[%d].承载" % (i - 1), 
                "必保留事实 F%d 未声明承载（每条事实都要给「｜承载：B{n}/台词k」"
                "「｜承载：B{n}/画面」或「｜承载：B{n}/旁白k」——哪条节拍真的把它"
                "演出来／说出来／讲出来）" % i)
            continue
        _p = parse_carrier_decl(_decl)
        if _p is None:
            _add("carrier_fact_decl_invalid", "fact", i, "required_facts[%d].承载" % (i - 1), "F%d 的承载声明格式非法（须 `B{n}/台词k`、`B{n}/画面` "
                            "或 `B{n}/旁白k`）：%s" % (i, _decl[:24]))
            continue
        _b, _mode, _k = _p
        if not 1 <= _b <= len(beats):
            _add("carrier_fact_beat_missing", "fact", i, "required_facts[%d].承载" % (i - 1), "F%d 声明的承载节拍 B%d 不存在（共 %d 条节拍）"
                            % (i, _b, len(beats)))
            continue
        _rec = BeatRecord.parse(beats[_b - 1])
        _kind, _tag_k = _rec.carrier()
        _claim = strip_fact_carrier(fact)
        if _mode == "台词":
            if not 1 <= _k <= len(lines):
                _add("carrier_fact_line_missing", "fact", i, "required_facts[%d].承载" % (i - 1), "F%d 声明的承载台词 D%d 不存在（共 %d 句）"
                                % (i, _k, len(lines)),
                     # **改动"必须有台词"这一条**：表为空时改承载指向也救不了（计划不能
                     # 一句台词都不选），只能补选内容 ⇒ 操作协议表达不了。表非空时改指到
                     # 已有台词/画面就能闭合 ⇒ 操作式可表达。
                     fix=("reselect_dialogue" if not lines else ""))
                continue
            if (_kind, _tag_k) != ("dialogue", _k):
                if _kind == "invalid":
                    _add("carrier_beat_label_invalid", "beat", _b, "shot_beats[%d].载体" % (_b - 1), 
                        "F%d 声明的承载节拍 B%d 的载体标注**非法**（%s）——只能写 `画面`、"
                        "`台词k` 或 `旁白k`"
                        % (i, _b, _rec.get(BEAT_SEG_CARRIER).strip()[:20]))
                else:
                    _add("carrier_fact_line_mismatch", "fact", i, "required_facts[%d].承载" % (i - 1),
                        "F%d 声明由 B%d 承载「台词%d」，但该节拍的载体标注不是它"
                        "——一条节拍只能标**一个**载体：k 句台词要各配一条标着"
                        "「载体：台词k」的节拍，把它们**分散开**"
                        % (i, _b, _k))
                continue
            _missing, _judged = uncovered_fact_clauses(
                fact, str(lines[_k - 1]), verbatim=True)
            if not _semantic and _missing:
                if len(_missing) == _judged:
                    _add("carrier_fact_line_unrelated", "fact", i,
                         "required_facts[%d].承载" % (i - 1),
                         "F%d 声明的承载台词 D%d 与这条事实**没有交集**（讲的是另一件事）："
                         "「%s」" % (i, _k, str(lines[_k - 1])[:24]))
                else:
                    _add("carrier_fact_line_incomplete", "fact", i,
                         "required_facts[%d].承载" % (i - 1),
                         _incomplete_reason(i, "台词 D%d" % _k, lines[_k - 1], _missing))
        elif _mode == "旁白":
            # 2026-09-30 复核第 6 轮：事实可以由图外旁白讲出来（`｜承载：Bn/旁白k`），
            # 但**旁白必须是计划里编号的条目**（`narration_lines` 第 k 条），且与该节拍
            # 自己的载体标注一致——「编号有效」不等于「事实被表达」，后者交内容核验。
            _narr = plan_narration_entries(plan)
            if not 1 <= _k <= len(_narr):
                _add("carrier_fact_narration_missing", "fact", i, "required_facts[%d].承载" % (i - 1), "F%d 声明的承载旁白 N%d 不存在（计划共 %d 条旁白）——"
                                "旁白必须先写进 `narration_lines` 才能被承载"
                                % (i, _k, len(_narr)))
                continue
            if (_kind, _tag_k) != ("narration", _k):
                if _kind == "invalid":
                    _add("carrier_beat_label_invalid", "beat", _b, "shot_beats[%d].载体" % (_b - 1), 
                        "F%d 声明的承载节拍 B%d 的载体标注**非法**（%s）——只能写 `画面`、"
                        "`台词k` 或 `旁白k`"
                        % (i, _b, _rec.get(BEAT_SEG_CARRIER).strip()[:20]))
                else:
                    _add("carrier_fact_narration_mismatch", "fact", i, "required_facts[%d].承载" % (i - 1),
                        "F%d 声明由 B%d 承载「旁白%d」，但该节拍的载体标注不是它"
                        "——一条节拍只能标**一个**载体：k 条旁白要各配一条标着"
                        "「载体：旁白k」的节拍，把它们**分散开**，不要都挂在同一条上"
                        % (i, _b, _k))
                continue
            # 2026-09-30 复核第 7 轮 P2：「互相登记」此前**单向可绕**——事实侧声明
            # `承载：B1/旁白1`，而 N1 的 `fact_refs` 写成 `[]`／`null`／非法项就整条
            # 免检（旧实现在这里带 `if _refs and`）。两处登记必须**无条件**互指
            # （与 `hook_spoken` ↔ `hook_carrier` 同口径）。
            _refs = _narr[_k - 1].get("fact_refs") or []
            if i not in _refs:
                _add("carrier_narration_fact_unregistered", "narration", _k, "narration_lines[%d].fact_refs" % (_k - 1), 
                    "F%d 声明由旁白 N%d 承载，但该旁白条目的 `fact_refs` 里没有 F%d"
                    "——事实侧与旁白侧必须互相登记（这条旁白要把 F%d 写进自己的 "
                    "`fact_refs`）" % (i, _k, i, i))
            # 复核 2026-10-04 第四轮 P1：**旁白正文也必须真的表达这条事实**。
            # 台词（`shares_run`）与画面（`shares_terms`）都有内容核验，旁白此前只有
            # 「编号有效 + 两处互指」——实测漏口：第三轮真实响应把四条旁白缩成一条
            # 「杨家的主人姓杨名旭，字文轩，今年刚及弱冠。」并登记 `fact_refs=[1,2,3,4]`，
            # 两处引用改对之后**统一验收返回零问题**，而「四年前丧父、十六岁接手家业」
            # 「三年孝期刚过便考中诸生」「听香是前几日买来的青楼女子」三条事实**没有
            # 任何正文表达**（下游要求旁白逐字保留，生成侧补不回来）。
            #
            # 判据沿用**台词那条**关系（`shares_run`）：旁白正文按契约必须逐字来自原文
            # （`text ⊆ source ⊆ 原文`，见 `narration_source_records`），与「选中的台词」
            # 同类；画面节拍是**转述**，才用更松的 `shares_terms`。不新造阈值、不看人物名
            # 重合、不做关键词匹配——与既有两条同一套口径。
            _ntext = str(_narr[_k - 1].get("text") or "")
            # **同一条判据边界**（`narration_source_records` 已有裁决）：正文本身
            # 不在原文 ⇒ 它本来就该由 `evaluate_plan` 的逐字核验剔除并重映射引用；
            # 这里再报一条，可剔除项会被升级成「契约未完成」而在剔除**之前**停下整章。
            _nn = _source_norm(_ntext)
            _novel_norm = _source_norm(novel_text)
            _droppable = bool(_nn) and bool(_novel_norm) and _nn not in _novel_norm
            _missing, _judged = uncovered_fact_clauses(fact, _ntext, verbatim=True)
            if not _semantic and not _droppable and _missing:
                # 身份按**事实侧**给（与同族 `carrier_fact_narration_mismatch` 同形）：
                # 要改的是「F_i 声明由谁承载」，路径落在该事实的承载字段上。
                if len(_missing) == _judged:
                    _add("carrier_fact_narration_unrelated", "fact", i,
                         "required_facts[%d].承载" % (i - 1),
                         "F%d 声明的承载旁白 N%d 的正文与这条事实**没有交集**"
                         "（讲的是另一件事）：「%s」" % (i, _k, _ntext[:24]))
                else:
                    _add("carrier_fact_narration_incomplete", "fact", i,
                         "required_facts[%d].承载" % (i - 1),
                         _incomplete_reason(i, "旁白 N%d" % _k, _ntext, _missing))
        else:
            # **声画共镜（A 窄版，2026-10-18 用户裁决"允许"）**：
            # 事实按 `B<uid>/画面` 声明画面承载，而该节拍自身的标签是 `旁白k`/`台词k` 时，
            # **不再拒绝**——声音归属记在节拍的载体标签上，事实承载记在事实的 `承载` 字段上，
            # 两者是不同字段、往返不丢（已离线验证）。仍拒绝：无标签、非法标签、
            # 标了旁白/台词但编号对不上（那属于声音契约问题，由后面的语音检查处理）。
            if _kind not in ("visual", "narration", "dialogue"):
                _tag = ("该节拍没有标载体" if _kind is None
                        else ("该节拍的载体标注**非法**（%s）——只能写 `画面`、`台词k` "
                              "或 `旁白k`"
                              % _rec.get(BEAT_SEG_CARRIER).strip()[:20]
                              if _kind == "invalid"
                              else "该节拍标的是「载体：%s%s」"
                                   % ("台词" if _kind == "dialogue" else "旁白", _tag_k)))
                # 复核之十二 P2：**生产者只说“要求”，不说“用哪个操作”**——
                # 历史文案曾写「请把它**拆成两条**」，而完整计划轮的核心守卫**禁止**改
                # `shot_beats`（条数与正文逐项比对）⇒ 实测：模型照建议拆了节拍（先后 3 轮）、
                # 每轮都被 `core_field_changed: shot_beats` 拒收。能做什么操作由**接口层**说。
                # **2026-10-18（A 窄版）**：不再需要拆分——画面承载与声音归属可落在同一节拍，
                # 本条只报"该节拍缺少合法载体登记"。
                _add("carrier_fact_visual_mismatch", "fact", i, "required_facts[%d].承载" % (i - 1),
                    "F%d 声明由 B%d 的**画面**承载，但%s——该节拍需要一个**合法载体登记**"
                    "（`画面`、`台词k` 或 `旁白k`）；无登记或登记非法时，无法确认这条节拍"
                    "到底演/说了什么。画面承载与声音归属**可以落在同一节拍**。"
                    % (i, _b, _tag))
                continue
            # 画面承载只认**正文**（镜头真的拍什么）：2026-09-30 复核给的漏口是
            # 「只在『本镜信息目的』里抄一遍事实」——那是**声明**，不是演出。
            _body = _rec.body
            # ⚠️ 画面是**转述**载体：合法形态是"用画面演出"，不是"逐字复述证据"
            # （实测 `F5 …「那个昨夜死掉」/载体「后院马车中躺着杨旭发臭的尸体」` 就是
            # 合法演出）。所以这里保持既有的"筛错指"关系，**不做**逐分句覆盖——
            # 完整承载交内容核验（审计），与 `test_agent_patient_swap_...` 同一条边界。
            if (not _body.strip() if _semantic else not shares_terms(_claim, _body, 2)):
                _add("carrier_fact_visual_unrelated", "fact", i, "required_facts[%d].承载" % (i - 1),
                    "F%d 声明的承载节拍 B%d 的正文／信息目的与这条事实**没有交集**"
                    "（共享要素不足：该节拍讲的是另一件事）：「%s」"
                    % (i, _b, _rec.body[:28]))
    # ---- 旁白条目的镜像自洽：`fact_refs` 指向的事实必须回头声明由它承载 ----
    # 两处登记必须一致（与 `hook_spoken`↔`hook_carrier` 同一口径）：只有一侧写，
    # 另一侧就会在变换/重选后各说各话。
    _narr_all = plan_narration_entries(plan)
    problems.extend(narration_source_records(_narr_all, novel_text))
    for _ni, _ne in enumerate(_narr_all, 1):
        if not _ne.get("source"):
            _add("carrier_narration_source_missing", "narration", _ni, "narration_lines[%d]" % (_ni - 1), "旁白 N%d 缺少 `source`（出处原文）——旁白必须注明它出自"
                            "原文哪一句，不能只写正文" % _ni)
        for _fi in (_ne.get("fact_refs") or []):
            if not 1 <= _fi <= len(facts):
                _add("carrier_narration_fact_missing", "narration", _ni, "narration_lines[%d].fact_refs" % (_ni - 1), "旁白 N%d 的 `fact_refs` 指向不存在的必保留事实 F%d"
                                "（共 %d 条）" % (_ni, _fi, len(facts)))
                continue
            _p2 = parse_carrier_decl(fact_carrier(facts[_fi - 1]))
            if not (_p2 and _p2[1] == "旁白" and _p2[2] == _ni):
                _add("carrier_narration_fact_mismatch", "narration", _ni, "narration_lines[%d]" % (_ni - 1), "旁白 N%d 声明它承载 F%d，但 F%d 的承载声明不是 "
                                "`B…/旁白%d`——事实侧与旁白侧必须互相登记"
                                % (_ni, _fi, _fi, _ni))
    # ---- 钩子：**无条件**核验声明自身，再核验它覆盖了全部要求 ----
    # 2026-09-30 复核第 4 轮 P2：旧实现只在「检出说出口要求」时才看这个字段，且只看
    # 覆盖——`B2/台词2；B3/台词99`（台词越界）、多余但对不上的声明、以及**没有说出口
    # 要求时**的非法写法全都照过。声明自身的格式/范围/重复/配对先无条件核验。
    _hqs = hook_spoken_quotes(plan)
    problems.extend(_hook_spoken_field_records(plan))
    problems.extend(_narration_field_records(plan))
    _decls, _hc_problems = _hook_carrier_decls(plan, beats, lines)
    problems.extend(_hc_problems)
    if _hqs:
        _named = {d[2] for d in _decls if d[1] == "台词"}
        for _q in _hqs:
            _hk = hook_carried_line(plan, _q)
            if not _hk:
                _add("carrier_hook_line_missing", "hook", None, "hook_spoken", 
                    "钩子要求说出口的「%s」不在选用台词里——必须先**整句**进入 "
                    "dialogue_lines（只说开头不算）" % str(_q)[:24],
                    # 修法就是"把那句整句选进台词表" = **新增条目** ⇒ 操作协议表达不了
                    fix="reselect_dialogue")
                continue
            if _hk not in _named:
                _add("carrier_hook_unlabeled", "dialogue", _hk, "dialogue_lines[%d]" % (_hk - 1), "钩子那句（D%d）没有承载节拍——`hook_carrier` 里没有"
                                "它的声明（形如 `B{n}/台词%d`）" % (_hk, _hk))
                continue
            _d = [x for x in _decls if x[1] == "台词" and x[2] == _hk][0]
            if not 1 <= _d[0] <= len(beats):
                _add("carrier_hook_beat_missing", "hook", None, "hook_carrier", "钩子声明的承载节拍 B%d 不存在（共 %d 条节拍）"
                                % (_d[0], len(beats)))
            elif BeatRecord.parse(beats[_d[0] - 1]).carrier() != ("dialogue", _hk):
                _add("carrier_hook_carrier_mismatch", "beat", _d[0], "shot_beats[%d].载体" % (_d[0] - 1), "钩子声明由 B%d 承载 D%d，但该节拍的载体标注不是它"
                                % (_d[0], _hk))
    # 多声明（**无条件**）：`hook_carrier` 里出现的每一句台词都必须在 `hook_spoken` 里。
    # 复核第 4 轮点名：没有说出口要求时，声明 `B1/台词1` 这种「多余但对得上」的条目
    # 此前完全不核（`_hqs` 为空就整段跳过）——声明与要求必须一一对应。
    _required = {hook_carried_line(plan, _q) for _q in _hqs}
    _extra = sorted(d[2] for d in _decls if d[1] == "台词" and d[2] not in _required)
    if _extra:
        _add("carrier_hook_extra_decl", "hook", None, "hook_carrier", 
            "`hook_carrier` 多声明了 D%s——`hook_spoken` 只要求说出口 %s；"
            "钩子没要求说出口的那句不要写进承载声明（纯画面钩子请给空串）"
            % (_extra, sorted(_required) or "（无）"))
    if _semantic:
            problems.extend(_semantic_verification_records(
                plan, novel_text, require_verification=require_verification))
    return problems


def _semantic_verification_records(plan, novel_text,
                                require_verification: bool = True):
    """Both presentation modes share the independent semantic record contract."""
    from shared.presentation_contract import view_from_plan
    from shared.content_verification import verification_problems, verification_context_of
    from step01_script_gen.content_verify import novel_identity
    view, bad = view_from_plan(plan)
    if view is None:
        return bad
    bad, table = verification_context_of(plan, view)
    if bad:
        return bad
    if not require_verification:
        # 应用阶段：**核验上下文**准入已由上面的 `verification_context_of`
        # 判过，这里只跳过"必须已有记录"这一步——记录由统一候选出口在候选
        # 定稿后绑定。完整验收（默认 True）行为不变，仍严格要求记录。
        return []
    return verification_problems(plan.get("plan_verification"), view, plan=plan,
                                 decomposition=table, novel_text=novel_text,
                                 novel_digest=novel_identity(novel_text)["digest"]
                                 if novel_text else None)


def _copy_or_raw(value):
    """逐字段副本的**类型安全**取法：列表复制一份；非列表**原样返回**（如实写出来）.

    非列表时 `list(value)` 会抛 `TypeError`（实测 `required_facts=7`）——留档出口不许
    成为异常源，否则"结构不合格"这件事在落盘这一步就丢了（复核第 43 轮 P1）。
    """
    return list(value) if isinstance(value, list) else value


def dump_plan_contract_invalid(chapter_dir, plan, problems, *, stop_reasons=None,
                               problem_records=None) -> None:
    """把契约问题落成**诊断产物** `step01_plan_contract_invalid.json`（准则 7：失败看得见）.

    `problems` 必须是**唯一完整验收报告**（`outline.plan_acceptance_problems`）的清单——
    落盘的 `plan` 重喂它必得同一批（回放契约；复核第 44 轮 P2：首次契约门一度只落
    `plan_contract_problems` 的**局部**清单，落盘 1 条而回放 2~11 条）。
    `stop_reasons` 记**触发本次停在计划层**的那几条（契约族的真子集）——
    "为什么停"与"完整清单"分成两个字段，读者不必从完整清单里猜。

    与 `step01_plan_infeasible.json` **分开写**：那份是「装不下」的读数，这份是
    「取舍没做完」的清单——混在一起会把登记问题读成内容结论。

    2026-09-30 复核第 5 轮 P2-4：诊断必须能**逐字回放**——旧实现只挑了几个字段落盘
    （漏了 `hook_spoken`），把冻结产物交回同一个函数会得到**不一样的问题清单**
    （实测第二章冻结产物记 5 项、回放得 6 项：多的正是钩子声明那条）。现在落盘
    **整份计划**（`plan`）+ 契约版本 + 运行身份，回放与现场判定必然一致；上面的
    逐字段副本保留，供人工速读与既有消费者。
    """
    if chapter_dir is None:
        return
    import copy
    from pathlib import Path
    from shared.config import duration_band
    # 身份四件套（run_id / written_at / stop_layer / 计划本体的契约版本）由
    # `_diag_identity` 统一提供；落盘走公共出口（准则 7：写盘失败必告警）。
    _dump_diagnostic_json(
        Path(chapter_dir) / "step01_plan_contract_invalid.json",
        dict(_diag_identity(STOP_LAYER_PLAN, plan), **{
            "verdict": "plan_contract_invalid",
            "duration_band": duration_band(),
            "problem_records": copy.deepcopy(problem_records),
            "conclusion": "plan_incomplete",
            "contract_version": PLAN_CONTRACT_VERSION,
            #: **完整验收**的问题清单（回放契约：`plan` 重喂 `plan_acceptance_problems` 必得同一批）
            "problems": list(problems),
            #: 触发"停在计划层"的原因（契约族）——`problems` 的**真子集**
            "stopped_by": (list(stop_reasons) if stop_reasons is not None
                           else list(problems)),
            #: **完整验收输入**（离线回放用；判据函数直接吃它）
            "plan": copy.deepcopy(plan) if isinstance(plan, dict) else plan,
            # 逐字段副本仅供人工速读：**列表原样复制，非列表原样留**——结构不合格时
            # 旧写法 `list(7)` 会在这里抛 `TypeError`，留档出口自己成了异常源
            # （复核第 43 轮 P1 同类：入口/守卫/留档的前置操作都要在统一协议内）
            "required_facts": _copy_or_raw((plan or {}).get("required_facts")),
            "shot_beats": _copy_or_raw((plan or {}).get("shot_beats")),
            "dialogue_lines": list(plan_dialogue_lines(plan)),
            "hook_spoken": list(hook_spoken_lines(plan)),
            "hook_carrier": (plan or {}).get("hook_carrier"),
            "end_hook": (plan or {}).get("end_hook")}),
        what="契约诊断")


def plan_identity(plan) -> str:
    """**计划版本身份**：节拍序列 + 选用台词序列（逐字）的指纹（12 位十六进制）.

    2026-09-27 复核点名的「同号误认」：`plan_refs` 是**对着某一份计划**作的声明
    （`B1..Bn` 按 `shot_beats` 的**位置**编号）。重选一旦改动节拍——尤其
    `insert_after` / `split` 会**重排编号**——旧声明可能**编号仍在范围内**却指向
    完全不同的节拍；只查「在不在范围内」会把它当成本轮计划的声明静默放行。

    身份**只由编号的所指决定**：节拍文本序列 + 台词文本序列。合同无关字段
    （时间预算、人物动机、`plan_replan` 留档等）不进指纹——它们变了不改编号含义。
    """
    import hashlib
    _h = hashlib.sha256()
    for _key in ("shot_beats", "dialogue_lines"):
        _h.update(("\x00" + _key + "\x00").encode("utf-8"))
        for _x in ((plan or {}).get(_key) or []):
            _h.update((str(_x or "") + "\n").encode("utf-8"))
    # 2026-09-30 复核第 6 轮：旁白条目也按**序号**被引用（`旁白k`），它的增删/改写
    # 同样改变编号含义 ⇒ 必须进计划身份（否则旧稿的 `旁白k` 声明会被当成本轮有效）。
    _h.update("\x00narration_lines\x00".encode("utf-8"))
    for _e in plan_narration_entries(plan):
        _h.update((str(_e.get("text") or "") + "\n").encode("utf-8"))
        if "voice_role" in _e:
            _h.update(("\x00voice_role\x00" + _e["voice_role"] + "\x00").encode("utf-8"))
    # 改编块也是编号含义的一部分：同一基底换一套播出文本，旧 plan_refs 不得复用。
    _block = (plan or {}).get("voice_adaptations")
    if _block:
        _entries = _block.get("entries") if isinstance(_block, dict) else _block
        _h.update(("\x00voice_adaptations\x00" + _va.entries_digest(_entries)).encode("utf-8"))
    return _h.hexdigest()[:12]


def plan_identity_mismatch(script, plan) -> str:
    """脚本携带的计划身份与本轮计划**不符**时返回原因；相符 / 无身份 → `""`。

    「不符」是**可证**的过期声明（`plan_refs` 的编号指的不是同一条节拍）；
    「无身份」是**不可核**（早于本字段的历史稿）——两者必须分开处置：前者拒收/排除，
    后者按兼容政策放行但**不得**称其「通过了本轮事件契约」。
    """
    _decl = str((script or {}).get("plan_id") or "").strip()
    if not _decl:
        return ""
    _cur = plan_identity(plan)
    if not _cur or _decl == _cur:
        return ""
    return ("plan_refs 是对**另一版计划**（%s）作的声明，当前计划是 %s——编号即使仍在"
            "范围内也不指向同一条节拍，必须按当前计划重新声明" % (_decl, _cur))


def bind_plan_identity(script: dict, plan) -> str:
    """给**新稿**绑定本轮计划身份；**已有身份一律不覆盖**（返回最终身份）。

    安全关键点就在「不覆盖」：候选稿的 `plan_refs` 是模型**对着本轮计划**写的，
    所以调用方可以替它声明来源；但一份**历史快照**自带旧身份时，覆盖它等于把
    「这份声明是对另一版计划作的」洗成「对当前计划作的」——同号误认正是这么发生的。
    故绑定用 `setdefault` 语义，随后 `check_event_contract(..., require_declaration=True)`
    必须返回 `declared=True` 且通过（绑定 ≠ 免检）。
    """
    _cur = plan_identity(plan)
    _decl = str((script or {}).get("plan_id") or "").strip()
    if _decl:
        return _decl
    if isinstance(script, dict) and _cur:
        script["plan_id"] = _cur
    return _cur


#: `plan_refs` 编号的标签前缀（`"B1"` / `"D2"`——模型常按契约块的 `B序号` 字面填）
_REF_TAGS = {"beats": "B", "lines": "D"}


def coerce_ref_index(value, tag: str):
    """`plan_refs` 的一个编号 → `int`；无法识别 → `None`.

    接受三种形态（**语义完全一致**，差别只在写法）：
    - 整数 `1`（schema 期望的形态）；
    - 纯数字串 `"1"`（`json_object` 降级后被 JSON 当字符串传回来的情形）；
    - 带标签串 `"B1"` / `"b1"` / `"B 1"` / `"B-1"`——契约块写的是
      `{"beats": [B序号…]}`，字面就支持这种读法。

    为什么**归一化**而不是判越界：前缀只是标签，指代无歧义；判越界会烧掉候选预算
    （2026-09-27 02:10 实测 1/3）、最坏整池拒收终止整章——那是**误拒**，不是严谨。
    真正越界（`"B9"` 超出节拍数）照旧由范围判据拒收，判据强度不减。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    _t = str(value or "").strip()
    if not _t:
        return None
    if _t[:1].upper() == str(tag or "").upper():
        _t = _t[1:].strip(" -_:#")
    # 2026-10-01 复核第 30 轮：`str.isdigit()` 对 `²` 也为真 ⇒ `int("²")` 抛 `ValueError`
    # 穿过计划层。数字判据与转换**单源**在 `safe_decl_int`（只认 ASCII `0-9`、超长返回 None）。
    return safe_decl_int(_t)


def plan_has_carrier_contract(plan) -> bool:
    """计划是否带**承载契约**（节拍标了「载体：…」或事实声明了「｜承载：…」）.

    2026-09-30 复核：`require_declaration` 的强制力**挂在契约上**。带承载契约的计划
    （v2：首版提示必产、缓存版本强制）必须逐镜声明 `plan_refs`，否则「先绑 plan_id、
    再整稿不填 refs」就能绕过覆盖/顺序核验还被当成已核验；而没有承载契约的历史计划
    （旧缓存/夹具）沿用「无可核 ≠ 被破坏」的容忍口径——两处同源，不新造第三套语义。
    """
    if not isinstance(plan, dict):
        return False
    for b in (plan.get("shot_beats") or []):
        if BeatRecord.parse(str(b)).has(BEAT_SEG_CARRIER):
            return True
    for f in (plan.get("required_facts") or []):
        if fact_carrier(f):
            return True
    return bool(str(plan.get("hook_carrier") or "").strip())


def check_event_contract(script: dict, plan: dict | None, *,
                         require_declaration: bool = False) -> tuple:
    """**事件履约契约**（纯函数、确定性、零模型调用）：`plan_refs` 声明的覆盖与顺序.

    计划侧单位（编号在 `prompts.render_execution_contract` 的契约块里给出）：
    **B1..Bn** = `shot_beats` 顺序（节拍），**D1..Dm** = `dialogue_lines` 顺序（选用
    台词）。每个镜头声明 `plan_refs = {"beats": [B…], "lines": [D…]}`，判据：

    - **覆盖**：每个节拍至少被一镜声明——缺 = 该节拍在成片没有承载（2026-09-26
      实跑：B5「恐吓隐瞒」无承载 → 审计 MISSING_PLOT；赶车命令被改成视觉手势且
      无承接 → MISSING_ACTION）；
    - **顺序**：按镜头序展开（镜内按声明序）后，B 与 D 序列都必须**非降**——
      后位事件不得在前位事件完成前呈现；
    - **越界**：引用不存在的 B/D → 声明与计划脱节。

    台词**逐字**是否保住不在此核（`check_script_dialogue_integrity` 收口）——
    这里核「声明与计划一致」这另一维度。声明缺失的容忍口径：**整稿**所有镜都
    没有 `plan_refs` → 「契约未声明」，返回 `(True, [], False)` 不判违规（历史
    快照没有声明，没有契约可核 ≠ 契约被破坏，否则跨 run 快照线全灭）；部分缺失
    → 逐镜记 `plan_refs_missing`（新生成稿不该出现混合形态）。

    Returns:
        `(ok, violations, declared)`；violations 每项 `{"code", "shot", "detail"}`，
        `declared=False` 表示整稿未声明（调用方不得据此阻断）。
    """
    beats = [str(x).strip() for x in ((plan or {}).get("shot_beats") or [])
             if str(x).strip()]
    try:
        _voice_entries = plan_voice_entries(plan)
    except Exception as exc:
        return False, [{"code": "adaptation_gate_error", "shot": "",
                        "detail": "改编块不可消费：%s" % exc}], True
    n_lines = len([e for e in _voice_entries if e["kind"] == VOICE_KIND_DIALOGUE])
    shots = [s for s in (script or {}).get("shots") or [] if isinstance(s, dict)]
    # ① 身份（2026-09-27 复核 P0/P0-2）：声明是**对着哪一版计划**作的？
    #    - 可证过期（带身份且不符）→ 拒收；
    #    - 不可核（无身份）→ 不据「不可核」判违规，但调用方要求时必须**显式失败**，
    #      否则新稿会绕过覆盖/顺序核验、还被盖上「已核验」的身份。
    _stale = plan_identity_mismatch(script, plan)
    if _stale:
        return False, [{"code": "plan_refs_stale_plan", "shot": "",
                        "detail": _stale}], True
    _contract_plan = plan_has_carrier_contract(plan)
    if not str((script or {}).get("plan_id") or "").strip():
        if require_declaration and shots and _contract_plan:
            return False, [{"code": "plan_refs_missing", "shot": "", "detail":
                            "整稿没有任何 plan_refs 声明（新生成稿必须逐镜声明"
                            "事件履约契约）"}], False
        return True, [], False
    refs_list: list = []
    missing: list = []
    for s in shots:
        r = s.get("plan_refs")
        if (not isinstance(r, dict)
                or not isinstance(r.get("beats"), list)
                or not isinstance(r.get("lines"), list)):
            missing.append(str(s.get("index")))
            refs_list.append(None)
        else:
            refs_list.append(r)
    if not shots:
        return True, [], False
    if len(missing) == len(shots):
        # 整稿**一条** plan_refs 都没有。历史快照（跨 run 复用）无声明 = 无契约可核；
        # 但**新生成稿**必须逐镜声明——否则「先绑 plan_id、再整稿不填 refs」就能绕过
        # 覆盖/顺序核验，还会被当成已核验（2026-09-30 复核复现的漏放）。
        if require_declaration and _contract_plan:
            return False, [{"code": "plan_refs_missing", "shot": "",
                            "detail": "整稿没有任何镜头声明 plan_refs（新生成稿必须逐镜"
                                      "声明事件履约契约）"}], False
        return True, [], False
    violations: list = []
    for sid in missing:
        violations.append({"code": "plan_refs_missing", "shot": sid,
                           "detail": "镜头缺少 plan_refs 声明（事件履约契约）"})
    if missing:
        # 有镜头缺声明时，覆盖/顺序无法公平评估（缺声明的那镜可能正承载着某节拍）——
        # 先报缺失，让下一轮候选带着完整声明进来再核其余判据。
        return False, violations, True

    covered: set = set()
    beat_seq: list = []
    line_seq: list = []
    for s, r in zip(shots, refs_list):
        sid = str(s.get("index"))
        for _raw in r["beats"]:
            b = coerce_ref_index(_raw, "B")
            if b is None:
                violations.append({
                    "code": "plan_refs_not_a_number", "shot": sid,
                    "detail": "plan_refs.beats 里有非编号项 %r（只填数字，如 1；"
                              "不要带 B 前缀）" % (_raw,)})
                continue
            if b < 1 or b > len(beats):
                violations.append({
                    "code": "plan_refs_out_of_range", "shot": sid,
                    "detail": "B%d 不存在（节拍共 %d 个）" % (b, len(beats))})
                continue
            covered.add(b)
            beat_seq.append((sid, b))
        for _raw in r["lines"]:
            d = coerce_ref_index(_raw, "D")
            if d is None:
                violations.append({
                    "code": "plan_refs_not_a_number", "shot": sid,
                    "detail": "plan_refs.lines 里有非编号项 %r（只填数字，如 1；"
                              "不要带 D 前缀）" % (_raw,)})
                continue
            if d < 1 or d > n_lines:
                violations.append({
                    "code": "plan_refs_out_of_range", "shot": sid,
                    "detail": "D%d 不存在（选用台词共 %d 句）" % (d, n_lines)})
                continue
            line_seq.append((sid, d))
    # 事件/台词顺序 —— **同时承载 vs 串行**在这里定死（第 15 轮复核；与预算侧
    # `beats_floor_basis` 同一份时间语义）：
    #   · **同时承载**：同一镜内声明多条；或同一条（声音）节拍被**连续多镜共同承载**
    #     （`[1,2] → [1,2]`：B1、B2 都由这两个镜头组成的片段承载）——它们是**同时
    #     发生**的，任何顺序都不构成回退；
    #   · **串行**：某条只在**更晚**的镜里首次出现，说明它被排到了前面 ⇒ 才是回退。
    # 旧实现把"每镜的声明"展开成一长串逐对比较，于是 `[1,2] → [1,2]` 被读成
    # 「B2 之后又出现 B1」→ 误报回退（实测：连续两镜共同承载 = 必然误报）。
    # 台词**实际落点**（第 16 轮复核）：声明数组是**有序集合**，光看它看不出"先说了谁"——
    # 正文里先说 D2、再说 D1，只要声明写成 `[1,2]`，旧口径与台词完整性门都会放行。
    # 所以镜内顺序按**台词在旁白文本里的位置**判，不看声明顺序。
    _line_text = [str(e["text"]) for e in _voice_entries
                  if e["kind"] == VOICE_KIND_DIALOGUE]
    _seg_by_sid = {str(s.get("index")): str(s.get("narration_segment") or "") for s in shots}
    # 旁白序列与镜位映射：**存在性判据（②）与顺序判据（①③）共用这一份**——
    # 各建一份就会出现"一个门用宽容匹配、另一个用生字符串"的口径分叉（第 17 轮复核）。
    _units_raw: list = []
    _unit_pos: dict = {}          # 镜头序号 → 在 `_units_raw` 里的位置（无旁白则不登记）
    for _sh in shots:
        _n = str(_sh.get("narration_segment") or "").strip()
        if _n:
            _unit_pos[str(_sh.get("index"))] = len(_units_raw)
            _units_raw.append(_n)
    # 镜位（`shots` 里的序）：承载区间是否连续**按镜位判**，不能按"声明展开后的下标"——
    # `[1,2] → [1,2]` 在下标上会看成 B1 断在 0 与 2 之间，其实它被**连续两镜**承载。
    _shot_ord = {str(s.get("index")): i for i, s in enumerate(shots)}

    def _actual_seq(seq):
        """D 的**实际序列**：按镜序、镜内按台词落点（与存在性**同一次匹配**）.

        两条纪律（第 17 轮复核 P1-①）：
          · 落点用 `_line_offset_in_units`（容忍标点/说话前缀/合规净化），**不是**生
            `str.find`——否则"换标点就两个门全过"；
          · **逐句**定位：某一句定位不到（存在性另有 `plan_refs_line_not_in_shot` 报它）
            不牵连同镜其它已确认的位置——旧实现一句失败就整镜退回声明序，
            连已经看出来的 D2→D1 逆序都被丢掉。
        """
        _by_shot: dict = {}
        for _sid, _v in seq:
            _by_shot.setdefault(_sid, []).append(_v)
        out = []
        for _sid, _vs in _by_shot.items():
            _pos = _unit_pos.get(_sid)
            _at, _miss = [], []
            for _v in _vs:
                _txt = _line_text[_v - 1] if 1 <= _v <= len(_line_text) else ""
                _hit, _off = (False, -1) if (_pos is None or not _txt) else \
                    _line_offset_in_units(_txt, _units_raw, _pos)
                (_at if _hit else _miss).append((_off, _v))
            out.extend((_sid, _v) for _off, _v in sorted(_at, key=lambda x: x[0]))
            out.extend((_sid, _v) for _off, _v in _miss)     # 定位不到的按声明序排在后面
        return out

    for seq, code, tag in ((beat_seq, "beats_order_violation", "B"),
                           (_actual_seq(line_seq), "lines_order_violation", "D")):
        _reported = False
        # ① 同一镜内：B 按声明顺序；D 的序列本身已是**实际落点序**（见 `_actual_seq`）
        for (sid0, v0), (sid1, v1) in zip(seq, seq[1:]):
            if sid0 != sid1 or v1 >= v0:
                continue
            if tag == "D":
                violations.append({
                    "code": code, "shot": str(sid1),
                    "detail": "镜头 %s 的旁白里 %s%d 出现在 %s%d 之前（台词呈现顺序"
                              "不得回退；判据看**正文实际落点**，不看 plan_refs 的声明顺序）"
                              % (sid1, tag, v1, tag, v0)})
            else:
                violations.append({
                    "code": code, "shot": str(sid1),
                    "detail": "同一镜内 %s%d 声明在 %s%d 之后（事件/台词顺序不得回退）"
                              % (tag, v1, tag, v0)})
            _reported = True
            break
        if _reported:
            continue
        # ② 跨镜：只按**首次声明**取序——共同承载造成的复现不参与排序
        _first: dict = {}
        for _i, (_sd, _v) in enumerate(seq):
            _first.setdefault(_v, (_i, _sd))
        _ordered = [(v[1], k) for k, v in sorted(_first.items(), key=lambda kv: kv[1][0])]
        for (sid0, v0), (sid1, v1) in zip(_ordered, _ordered[1:]):
            if v1 < v0:
                violations.append({
                    "code": code, "shot": str(sid1),
                    "detail": "%s%d 在 %s%d 之后首次出现（事件/台词顺序不得回退；"
                              "同一镜内或连续多镜**共同承载**不算回退）"
                              % (tag, v1, tag, v0)})
                _reported = True
                break  # 每类报第一处即够定位
        if _reported:
            continue
        # ③ **承载区间必须连续**（第 16 轮复核）：首次声明序只看"第一次"，`[1]→[2]→[1]→[3]`
        #    的首次序是 0,1,3（非降）会被放行——而 B1 在 B2 之后**再次进入**，叙事回到了
        #    已经结束的节拍。共同承载 = 一段**连续**的镜；隔镜复现 = 回退。
        _where: dict = {}
        for _sd, _v in seq:
            _where.setdefault(_v, []).append(_shot_ord.get(_sd, -1))
        for _v in sorted(_where):
            _ps = sorted(set(_where[_v]))
            if _ps and _ps[-1] - _ps[0] + 1 != len(_ps):
                violations.append({
                    "code": code, "shot": "",
                    "detail": "%s%d 在镜头 %d 与镜头 %d 之间**断开后再次出现**"
                              "（承载片段必须连续：隔镜复现 = 回到已经结束的节拍）"
                              % (tag, _v, _ps[0] + 1, _ps[-1] + 1)})
                _reported = True
                break
    for i, beat in enumerate(beats, 1):
        if i not in covered:
            violations.append({
                "code": "beats_not_carried", "shot": "",
                "detail": "B%d（%s…）没有被任何镜头声明承载" % (i, beat[:36])})
    # ③ **B↔D 配对**（2026-09-30 复核）：同一镜里「声明的节拍承载哪句台词」与
    #    「这一镜声明说哪句台词」必须是同一句。两门各自都能过、合起来仍是假绿：
    #    shot1 声明 B1（承载 D1）却不说话，shot2 声明 B2 却说了 D1 —— 覆盖门与
    #    台词锚定门都不报。
    # 容忍口径与 `carrier_relation_problems` 同源：**计划根本没标载体**时没有 B→D
    # 契约可核（旧计划/历史快照）⇒ 不判配对，避免把「无契约」当成「契约被破坏」。
    _has_carrier_contract = any(
        BeatRecord.parse(b).has(BEAT_SEG_CARRIER) for b in beats)
    _beat_decl_shot: dict = {}      # B 编号 → 按镜序**最早**声明它的镜头序号
    for _sh, _r in (zip(shots, refs_list) if _has_carrier_contract else ()):
        for _raw in _r["beats"]:
            _b = coerce_ref_index(_raw, "B")
            if _b is None or not 1 <= _b <= len(beats):
                continue            # 非编号/越界已在上方报过
            _beat_decl_shot.setdefault(_b, str(_sh.get("index")))
    _shot_beats: dict = {}
    _shot_lines: dict = {}
    for s, r in (zip(shots, refs_list) if _has_carrier_contract else ()):
        sid = str(s.get("index"))
        _shot_beats[sid] = {b for b in (coerce_ref_index(x, "B") for x in r["beats"])
                            if b and 1 <= b <= len(beats)}
        _shot_lines[sid] = {d for d in (coerce_ref_index(x, "D") for x in r["lines"])
                            if d and 1 <= d <= n_lines}
    if _has_carrier_contract:
        # (i) 这一镜**说** Dk ⇒ 本镜声明的节拍里必须有一条承载 Dk（否则说的是别人节拍的台词）
        for sid, _ds in _shot_lines.items():
            _carried = set()
            for b in sorted(_shot_beats.get(sid) or ()):
                _kind, _k = BeatRecord.parse(beats[b - 1]).carrier()
                if _kind == "dialogue" and _k:
                    _carried.add(_k)
            for d in sorted(_ds):
                if d not in _carried:
                    violations.append({
                        "code": "plan_refs_pairing_broken", "shot": sid,
                        "detail": "这一镜声明说 D%d，但本镜声明的节拍里没有一条承载它"
                                  "（承载它的节拍被声明在别的镜头）" % d})
        # (ii) B 承载 Dk ⇒ 声明 B 的**那些镜头里至少有一镜**声明说 Dk。
        #      2026-09-30 复核 P1-2：**一个节拍可以展开 2-4 镜**（建立镜 + 对白镜 +
        #      反应镜），旧实现只认「最早声明 B 的那一镜」，把「第一镜建立场景、
        #      第二镜仍属 B1 并说 D1」判成错配——这是把合法展开误拒。
        _carrier_of: dict = {}
        for b in range(1, len(beats) + 1):
            _kind, _k = BeatRecord.parse(beats[b - 1]).carrier()
            if _kind == "dialogue" and _k:
                _carrier_of[b] = _k
        for b, k in sorted(_carrier_of.items()):
            _holders = [sid for sid, _bs in _shot_beats.items() if b in _bs]
            if not _holders:
                continue          # 未被任何镜头声明：由覆盖门单独报
            if not any(k in (_shot_lines.get(sid) or set()) for sid in _holders):
                violations.append({
                    "code": "plan_refs_pairing_broken", "shot": _holders[0],
                    "detail": "承载 D%d 的 B%d 被 %d 个镜头声明，但这些镜头都没有声明说 "
                              "D%d（一个节拍可展开多镜，但至少有一镜要说这句）"
                              % (k, b, len(_holders), k)})

        # (iii) 声音条目里的**旁白**必须在承载它的**连续镜头片段**里逐字出现。
        #       复核第 6 轮：`旁白k` 只是编号，编号有效不等于旁白在成片里真被念出来；
        #       复核第 7 轮 P1：判据必须与台词侧**同一份**——旧实现要求「整条落在
        #       某一个镜头里」，而计划层的可表示性判据本来就允许按句末切分成 ≤4 段
        #       （`check_dialogue_representable`），两份假设不一致 ⇒ 计划判可行、
        #       终检却报 `plan_refs_narration_missing`。现在与台词共用
        #       `_line_span_in_units`：单镜整句包含，或起点对齐 + 跨镜连续分解。
        if _has_carrier_contract:
            for _v in voice_units(plan, shots):
                if _v["kind"] != VOICE_KIND_NARRATION:
                    continue
                if _v.get("declared_shot") is None:
                    continue          # 没有任何镜头声明承载它的节拍：由覆盖门单独报
                if _v["shot_indexes"]:
                    continue
                violations.append({
                    "code": "plan_refs_narration_missing",
                    "shot": _v["declared_shot"],
                    "detail": "承载旁白 N%d 的节拍由镜头 %s 起声明，但从该镜起**连续**的"
                              "镜头旁白里拼不出这条旁白（判据与台词同源：单镜整句包含，"
                              "或起点对齐 + 按句末跨镜连续分解——**不截句**）："
                              "「%s…」" % (_v["index"], _v["declared_shot"],
                                           str(_v["text"])[:24])})

    # ② **Dk 必须锚在声明它的那一镜**（2026-09-27 复核）
    # 契约块要求「台词 Dk 必须**逐字**落在声明它的那个镜头的 narration_segment 里」，
    # 但本门此前只核编号范围/顺序，整稿台词门只证明 Dk **在某处**——两门各自都过、
    # 合起来是**假绿**：shot 1 声明 D1、shot 2 才说 D1 能一路通过。判据与整稿门同源
    # （`_line_hit_in_units`，只把起点钉在声明那一镜），不新造第二套匹配口径。
    _decl_shots: dict = {}        # D 编号 → 按镜序**最早**声明它的镜头序号
    for _sh, _r in zip(shots, refs_list):
        for _raw in _r["lines"]:
            _d = coerce_ref_index(_raw, "D")
            if _d is None or not 1 <= _d <= n_lines:
                continue          # 非编号/越界已在上方报过，不重复计
            _decl_shots.setdefault(_d, str(_sh.get("index")))
    _plan_lines = _line_text
    for _d, _sid in sorted(_decl_shots.items()):
        _pos = _unit_pos.get(_sid)
        if _pos is None:
            violations.append({
                "code": "plan_refs_line_not_in_shot", "shot": _sid,
                "detail": "镜头 %s 声明承载 D%d，但该镜没有旁白（台词不在本镜）"
                          % (_sid, _d)})
            continue
        _hit, _ = _line_hit_in_units(_plan_lines[_d - 1], _units_raw, start=_pos)
        if not _hit:
            violations.append({
                "code": "plan_refs_line_not_in_shot", "shot": _sid,
                "detail": "镜头 %s 声明承载 D%d，但该镜旁白里找不到这句台词"
                          "（逐字/跨镜连续分解均不成立）：%s…"
                          % (_sid, _d, str(_plan_lines[_d - 1])[:24])})
    return (not violations), violations, True


def dump_dialogue_incomplete(chapter_dir, broken_batches, *, total_lines: int = 0,
                             attempt_stats: dict | None = None,
                             conclusion: str | None = None,
                             layer: str = "候选层",
                             plan=None) -> None:
    """台词完整性门失败的**显式诊断**（决策点 2；五审：结论随实际失败类型区分）.

    `conclusion` 缺省按批次**自动归类**（五审 2026-09-26）：
    - 全部条目的 `reason` 以 `gate_error` 开头（且批次非空）→ `dialogue_gate_error`：
      **检查故障**结论——稿件完整性**未被判定**，不是生成稿完整性结论；
    - 否则（含真实破损；含破损+门异常的**混合失败**）→ `dialogue_incomplete`：
      生成稿完整性结论，混合失败的各类计数留在 `attempt_stats`
      （`{"total", "dialogue_broken", "gate_errors", "other_failed"}`，三审 P2-2
      + 四审 P1 分账口径）。

    消费者不得把任何一种解释成 `replan_incomplete`／触发计划重选（审核
    2026-09-26）。正式三件套不动。`layer` 只影响 note 文案（候选层/终稿层共用）。
    """
    if chapter_dir is None:
        return
    _gate_only = bool(broken_batches) and all(
        str((b or {}).get("reason") or "").startswith("gate_error")
        for batch in (broken_batches or []) for b in batch)
    _conclusion = conclusion or ("dialogue_gate_error" if _gate_only
                                 else "dialogue_incomplete")
    if _conclusion == "dialogue_gate_error":
        _note = (f"{layer}台词完整性门自身异常（检查故障）：稿件完整性**未被判定**，"
                 "不进入选稿/提交门；这不是计划可行性结论，不触发计划重选")
    else:
        _note = (f"{layer}台词完整性硬门：破损稿不进入 BEST-GUARD／提交门；"
                 "这不是计划可行性结论，不触发计划重选")
    # **自己的文件**（2026-10-01 复核 P2）：候选层的台词结论原先写进
    # `step01_plan_infeasible.json` —— 既没有身份，又会**覆盖**真正的预算层诊断，
    # 读取方按文件名归层 ⇒ 候选层失败被记成计划层。
    from pathlib import Path
    _stop_layer = (STOP_LAYER_FINAL if "终稿" in str(layer or "")
                   else STOP_LAYER_CANDIDATE)
    # `plan=` 不能省（复核第 11 轮 P2-①）：缺计划时契约版本落成 0，读取方会以为
    # "这轮不是 v4 计划"；落盘走公共出口（准则 7：写盘失败必告警，不再静默 pass）。
    _dump_diagnostic_json(
        Path(chapter_dir) / "step01_dialogue_incomplete.json",
        dict(_diag_identity(_stop_layer, plan),
             **{"verdict": _conclusion,
                "conclusion": _conclusion,
                "layer": layer,
                "note": _note,
                "total_lines": total_lines,
                "attempt_stats": dict(attempt_stats or {}),
                "broken_batches": list(broken_batches or [])}),
        what="台词诊断")


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
    "dialogue_rejected", "beats_floor_basis", "lower_bound_basis",
    "lower_bound_sec",
    "additive_sum_sec", "budget", "reasons", "suggestions", "note"}`。
    其中 `dialogue_lines`/`dialogue_sec` 口径是**核验通过**的那一组。
    """
    _bmin, _bmax = budget_band()
    bmin = float(budget_min if budget_min is not None else _bmin)
    bmax = float(budget_max if budget_max is not None else _bmax)

    def _stop(reasons, suggestions, *, missing=None, rejected=None,
              problems=None) -> dict:
        return {"verdict": "unevaluable", "feasible": False, "evaluable": False,
                "missing": list(missing or []), "dialogue_lines": 0,
                "dialogue_sec": 0.0, "dialogue_verified": [],
                "dialogue_rejected": list(rejected or []),
                "narration_verified": [], "narration_rejected": [],
                "dialogue_unrepresentable": [],
                "beat_carrier_dangling": [],
                "beats_floor_basis": None, "lower_bound_basis": "voice",
                "lower_bound_sec": None,
                "additive_sum_sec": None, "budget": {"min": bmin, "max": bmax},
                # **停止分支也要产具体记录**（复核 2026-10-02 之八 P1）：旧写法只填
                # `reasons`（一串没有身份的字符串），报告层却去读不存在的单数 `reason`
                # ⇒ 反馈给模型的只有「验收未完成：」四个字，模型拿不到"哪个字段、
                # 为什么、怎么改"。记录带 `path` / `reason` / **修正要求**（`basis`）。
                "problems": list(problems or []),
                "reasons": reasons, "suggestions": suggestions, "note": ""}

    # ① 计划缺失（大纲提取失败 / OUTLINE_ENABLED=0）——生成前无从验算，必须显式停止
    if not plan:
        return _stop(
            ["本集计划缺失（大纲提取失败或 OUTLINE_ENABLED=0）——生成前无法核验内容取舍与预算"],
            ["重跑规划（或打开 OUTLINE_ENABLED=1）后再生成；计划是内容取舍与预算的权威"],
            missing=list(_REQUIRED_PLAN_FIELDS),
            problems=[_problem_record(
                "plan_missing", "plan", None, "plan",
                "本集计划缺失（大纲提取失败或 OUTLINE_ENABLED=0）——生成前无法核验"
                "内容取舍与预算",
                "修正要求：先让规划阶段产出计划（重跑规划 / 打开 OUTLINE_ENABLED=1）"
                "再生成；计划是内容取舍与预算的权威")])

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
            missing=missing,
            problems=[_problem_record(
                "plan_fields_missing", "plan", None,
                "、".join(str(x) for x in missing) or "dialogue_lines/shot_beats",
                "计划缺少可核算字段 %s——无法在生成前验算（fail-closed）" % missing,
                "修正要求：让规划阶段补齐这些字段后重跑；不要带着未验算的计划生成完整稿")])

    # ③ 没有原文就无法核验台词依据（原文是事实与台词的依据）
    if not str(novel_text or "").strip():
        return _stop(["缺少原文文本，无法核验选用台词是否逐字来自原文（fail-closed）"],
                     ["带原文重跑；不要带着未核验的台词生成完整稿"],
                     problems=[_problem_record(
                         "source_text_missing", "plan", None, "novel_text",
                         "缺少原文文本，无法核验选用台词是否逐字来自原文（fail-closed）",
                         "修正要求：带上原文重跑；不要带着未核验的台词生成完整稿")])

    # ③b 改编块前置校验：不可消费时不得退回基底做预算/生成（fail-closed）。
    _adapt = _va.adaptation_problems(plan, novel_text)
    if _adapt:
        return _stop(["改编层不可消费：%s" % str(_adapt[0].get("reason") or _adapt[0].get("code"))],
                     ["修复改编批准/条目/出处，或关闭改编后重跑；不得静默使用基底文本"],
                     problems=_adapt)
    lines = plan_dialogue_lines(plan)
    _kept_ix, rejected = verify_dialogue_lines_indexed(lines, novel_text)
    kept = [x["line"] for x in _kept_ix]
    if not kept:
        # **三个互斥分支，各自完整返回**（复核 2026-10-05 P1：旧写法让 hybrid 分支
        # 落进 legacy 的收尾，于是①合法 hybrid 零对白抛 `UnboundLocalError`
        # （`reasons` 在该分支从未赋值）、②legacy 全部对白不在原文时被判 feasible
        # （`reasons` 只在 `else` 里赋值，混合分支拿到的是别的分支的值）。
        # 一处控制流负责两个语义错误，必须写成互斥的早返回。
        _hybrid = (_plan_contract_version(plan) >= PRESENTATION_MIN_VERSION
                   and isinstance(plan.get("presentation"), dict))
        # ---- 分支 ①：新模式 + 没有任何台词（也没被剔除的台词）⇒ 合法，继续 ----
        if _hybrid and not lines and not rejected:
            kept = []
        # ---- 分支 ②：有台词但**全部**不在原文（新旧模式都拒，逐条反馈）----
        elif rejected:
            _reasons = ["计划选用的 %d 句台词**全部**未在原文中逐字找到%s"
                        % (len(lines),
                           "（新模式仍要求：若有对白，必须逐字来自原文）" if _hybrid
                           else "——生成阶段会自行决定台词，计划就不是内容取舍的权威")]
            _probs = []
            for _r in (rejected or []):
                _ix = _r.get("index")
                _probs.append(_problem_record(
                    "dialogue_line_not_in_novel", "dialogue", _ix,
                    ("dialogue_lines[%d]" % (int(_ix) - 1)) if _ix else "dialogue_lines",
                    "D%s 未在原文里逐字找到（%s）：%s"
                    % (_ix if _ix else "?", str(_r.get("reason") or ""),
                       str(_r.get("line") or "")[:40]),
                    "修正要求：换一句**原文逐字**的台词，或删掉它改由画面/文字卡承载"))
            return _stop(_reasons,
                         ["改用原文逐字台词，或移除后由画面/文字卡承载"],
                         rejected=rejected, problems=_probs)
        # ---- 分支 ③：旧模式没有任何台词 ⇒ 仍拒收（旧模式不放宽）----
        else:
            return _stop(["计划未选用任何逐字台词（`dialogue_lines` 为空）——"
                          "生成阶段会自行决定台词，计划就不是内容取舍的权威"],
                         ["回到规划层按原文**逐字**摘出台词（宁少勿滥）后重跑"],
                         rejected=rejected,
                         problems=[_problem_record(
                             "plan_no_dialogue_lines", "plan", None, "dialogue_lines",
                             "计划未选用任何逐字台词（`dialogue_lines` 为空）——生成阶段"
                             "会自行决定台词，计划就不是内容取舍的权威",
                             "修正要求：回到规划层，按原文**逐字**摘出台词（宁少勿滥）；"
                             "改写/自造的句子不算本集选用的内容",
                             fix="reselect_dialogue")])

    # ④b 旁白（2026-09-30 复核第 6 轮）：与对白同一套核验与预算——
    #     ① 首版采用原文叙述的**完整句子**，故同样要求逐字来自原文；
    #     ② 与对白**共用**发声时长估算，计入整篇下界（画面与旁白同时发生，不额外加镜头）。
    _narr_all = plan_narration_entries(plan)
    _narr_kept: list = []
    _narr_kept_ix: list = []          # 原序号（1 起）——记录身份，报告层不猜
    _narr_rejected: list = []
    if _narr_all:
        _v_kept, _v_rej = verify_dialogue_lines([e["text"] for e in _narr_all], novel_text)
        _pool = list(_v_kept)
        for _ni, _e in enumerate(_narr_all, 1):
            if _e["text"] in _pool:
                _pool.remove(_e["text"])
                _narr_kept.append(_e)
                _narr_kept_ix.append(_ni)
            else:
                _narr_rejected.append(_e["text"])
    _voice_bc = plan_voice_entries(plan, novel_text=novel_text)
    _bc_dlg = [e for e in _voice_bc if e["kind"] == VOICE_KIND_DIALOGUE]
    _bc_narr = [e for e in _voice_bc if e["kind"] == VOICE_KIND_NARRATION]
    _dlg = round(
        dialogue_seconds([_bc_dlg[x["index"] - 1]["text"] for x in _kept_ix])
        + sum(_estimate_duration(_bc_narr[ni - 1]["text"])
              for ni in _narr_kept_ix), 2)
    # 下界 = **发声预算**（对白 + 旁白，逐句估时器算出来的真实量）。节拍**不算时间**：
    # 见 `beats_floor_basis` 的说明（第 16 轮复核：9 镜 × 10 秒 36 节拍的稿被判 108 秒）。
    _basis = beats_floor_basis(plan)
    lower = round(_dlg, 2)
    # ⑤ 单镜**可表示性**（v6.136 A 判据，2026-09-26 用户决策）：逐句检查"整条装进
    # 单镜，或存在句末合法切分"。这是**计划层**的检查——不可表示的台词进入生成后
    # 必然"拆分成功、终稿门失败"（09-26 三次实跑复现），必须前移到规划层。
    # **不是静默剔除**：走 infeasible → 规划重选反馈（点名台词+保留必保留事实/
    # 事件顺序）→ 重选后仍不可行才显式停止交内容取舍。
    _cap = active_shot_cap()
    _unrep = []
    for _x in _kept_ix:
        _line_bc = (_bc_dlg[_x["index"] - 1]["text"]
                    if 0 < _x["index"] <= len(_bc_dlg) else _x["line"])
        _chk = check_dialogue_representable(_line_bc, _cap)
        if not _chk["representable"]:
            _unrep.append({"kind": "dialogue", "index": _x["index"],
                           "line": _line_bc, "est_sec": _chk["est_sec"],
                           "reason": _chk["reason"]})
    # 旁白同样不能截句：一条旁白必须能整条讲完在一个镜头里（与台词同判据）
    # ⚠️ 2026-10-22：旁白**不加说话人前缀余量**。旧写法复用 `check_dialogue_representable`
    # 的默认 5 字余量，把 11.40s 的旁白算成 12.65s 判"不可表示"，而预算下界用不加余量的
    # 估时（11.40s）——同一句话两个读数，模型无论怎么改都过不了。
    for _e, _ni in zip(_narr_kept, _narr_kept_ix):
        _line_bc = (_bc_narr[_ni - 1]["text"]
                    if 0 < _ni <= len(_bc_narr) else _e["text"])
        _chk = check_dialogue_representable(_line_bc, _cap, prefix_allowance_chars=0)
        if not _chk["representable"]:
            _unrep.append({"kind": "narration", "index": _ni,
                           "line": _line_bc, "est_sec": _chk["est_sec"],
                           "reason": "旁白：" + str(_chk["reason"])})
    reasons, suggestions = [], []
    if rejected:
        reasons.append("选用台词 %d 句未在原文中逐字找到，已从计划中剔除" % len(rejected))
    if _narr_rejected:
        reasons.append("旁白 %d 条未在原文中逐字找到（旁白必须是原文叙述的完整句子，"
                       "不得自造/改写）：%s"
                       % (len(_narr_rejected),
                          "；".join("「%s…」" % str(x)[:20] for x in _narr_rejected[:3])))
    if _unrep:
        reasons.append(
            "%d 句选用台词超出单镜上限 %.0fs 且**无句末合法切分**（判据：整条装进单镜，"
            "或按句末边界。！？…；切成 ≤4 段每段 ≤ 上限——与台词完整性门/拆分器同判据）：%s"
            % (len(_unrep), _cap,
               "；".join("「%s…」%s" % (u["line"][:20], u["reason"])
                         for u in _unrep[:3])))
        suggestions.append(
            "规划层**重选**：从原文重选能传达同样必保留事实的替代台词"
            "（**必保留事实与事件顺序不得静默删除**；重选反馈会点名这些台词）")
        suggestions.append(
            "若原文无合适替代 ⇒ 明确报告「规划无法满足当前单镜规则」，内容取舍交用户决定")
    # ⑥ 「无合适替代」标记出口（六审 P1-2）：重选反馈要求规划在原文确实没有更短
    # 替代台词时在 `time_budget` 明写——该标记是**内容取舍状态**：即使其余台词
    # 可表示、预算可行，也不得进入生成（必保留事实被静默丢弃的风险必须由用户
    # 裁决，代码不代决）。标记随计划进缓存 → 后续每轮都在本门显式停止。
    _no_alt = "无合适替代" in str((plan or {}).get("time_budget") or "")
    if _no_alt:
        reasons.append(
            "规划层明确报告：不可表示台词**无合适替代**（`time_budget` 标记）——"
            "须内容取舍（删情节/换场次/拆集），代码不代决")
        suggestions.append(
            "用户决策后修订计划（删除该标记并重选，或手工调整选材）再重跑")
    # ⑦ 节拍**载体悬空**（v6.138，九审根因 1）：节拍声明「载体：台词k」而第 k 句
    # 已不在选用台词里 ⇒ 该节拍在成片注定无台词承载（重选删掉了承载台词却没重新
    # 指定载体）。与 ⑤ 同口径走 infeasible → 重选反馈（允许且必须更新载体标注）
    # → 重选后仍悬空才显式停止。旧缓存计划无载体标注 → 不判悬空（无可核 ≠ 被破坏）。
    _beats_list = [str(x).strip() for x in ((plan or {}).get("shot_beats") or [])
                   if str(x).strip()]
    _dangling = []
    _dangling_ix: list = []          # 与 `_dangling` 同步的 (节拍号, 渲染好的原因)，供记录用
    for _i, _bt in enumerate(_beats_list, 1):
        _kind, _k = _beat_carrier(_bt)
        # 同上：`台词k` 是**计划编号**，与"核验通过条数"无关
        if _kind == "dialogue" and not (1 <= (_k or 0) <= len(lines)):
            _msg = ("B%d 载体「台词%s」不在当前选用台词中（共 %d 句）"
                    % (_i, _k, len(lines)))
            _dangling.append(_msg)
            _dangling_ix.append((_i, _msg))
        # 2026-10-02 之二：**旁白**是第三条合法载体，⑦ 的镜像必须一并核（v4 加了旁白，
        # 悬空检查却没跟着扩）：节拍声明「载体：旁白k」而旁白表里没有第 k 条——典型形态是
        # 同批把 N2 删了、承载它的那条节拍没跟着改。**结果不完整要当场报出来**：否则这份
        # 计划会带着"宣称承载一个不存在的声音"一路走到生成契约。
        elif _kind == "narration" and not (1 <= (_k or 0) <= len(_narr_kept)):
            _msg = ("B%d 载体「旁白%s」不在当前旁白表中（共 %d 条）"
                    % (_i, _k, len(_narr_kept)))
            _dangling.append(_msg)
            _dangling_ix.append((_i, _msg))
    # ⑧ 载体标注**重复**（v6.143，2026-09-27 复核）：同一个节拍声明了多个载体段
    # ⇒ 承载关系不是**唯一记录**，而 `_beat_carrier` 只读第一段、后面的段被静默忽略。
    # 实测形态：重选应用器把模型自带的旧标注又追加了一遍 →「｜载体：画面｜载体：画面」。
    # ⑨ 选中台词**无人承载**（v6.143，同上）：⑦ 只查「节拍指向已不存在的台词」，这是
    # 它的**镜像面**——台词被选中却没有任何节拍声明承载它。后果不在计划层显形：生成
    # 契约里没有哪条节拍告诉模型这句该放哪儿 → 成片大概率不出这句 → **候选层台词门**
    # 判决（每句必须逐字出现）整稿拒收，等于把问题从计划层挪到了候选层。
    # 承载关系必须**双向**闭合。与 ⑦ 同口径：旧缓存计划**根本没有**载体标注时不判
    # （无可核 ≠ 被破坏）。
    # **编号域是计划自己的 D/N 编号**（复核第 41 轮 P2）：这里必须传 `lines` / `_narr_all`
    # 的条数，不能传"核验通过的条数"——`kept` 已经剔掉了原文里没有的台词，用它去解释
    # 节拍里的 `台词k` 会把合法承载判成悬空（D1 非法、D2 合法、B2 承载 D2 时曾误报）。
    # 剔除与重映射之后编号域会变，那时由 `apply_plan_transforms` **重新出报告**。
    _dup_carrier, _orphan, _unlabeled = carrier_relation_problems(
        _beats_list, len(lines), len(_narr_all))
    _no_carrier = "载体缺失：无合适承载" in str((plan or {}).get("time_budget") or "")
    if _unlabeled:
        reasons.append(
            "%d 个节拍**没有载体标注**（计划带了标注就必须每条节拍恰有一个载体）：%s"
            % (len(_unlabeled), "，".join(_unlabeled[:6])))
        suggestions.append(
            "为每条节拍补一个「｜载体：…」段（台词k，或「画面」）——承载关系是唯一记录")
    if _dup_carrier:
        reasons.append(
            "%d 个节拍的载体标注**重复**（同一节拍声明了多个载体，承载关系不是唯一记录）：%s"
            % (len(_dup_carrier), "，".join(_dup_carrier[:4])))
        suggestions.append(
            "每个节拍只保留**一个**「｜载体：…」段（重选/重排时先剥掉旧标注再写新的）")
    if _orphan:
        reasons.append(
            "%d 句**选中的台词没有任何节拍承载**（承载关系必须双向闭合）：%s"
            % (len(_orphan), "，".join(_orphan[:6])))
        suggestions.append(
            "为每句选中台词指定承载节拍（改该节拍的「｜载体：台词k」），或把该句移出本集选材")
    if _dangling:
        reasons.append(
            "%d 个节拍的载体标注指向已移出的台词：%s"
            % (len(_dangling), "；".join(_dangling[:4])))
        suggestions.append(
            "规划层重选时**必须同步更新节拍的载体标注**（重选回原台词，或改标为"
            "保留台词/「载体：画面」）——节拍内容与顺序不得改动，只许改载体段")
    if _no_carrier:
        reasons.append(
            "规划层明确报告：节拍**载体缺失、无合适承载**（`time_budget` 标记）——"
            "须内容取舍，代码不代决")
        suggestions.append(
            "用户决策后修订计划（删除该标记并调整节拍/选材）再重跑")
    if lower > bmax:
        reasons.append(
            "必保留内容的估算下界 %.2fs 已超预算上限 %.0fs（发声：对白 %.2fs / %d 句 + "
            "旁白 %.2fs / %d 条；画面节拍 %d 个**不构成时间下界**——镜数由时长预算决定）"
            % (lower, bmax, dialogue_seconds(kept), len(kept),
               narration_seconds(_narr_kept), len(_narr_kept), _basis["beats"]))
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
    # ---- 本函数**自己**产出的问题记录（工作包 §2.2：生产者自带身份与分类）----
    # 复核第 40 轮 P1：`verdict` 是**多轴合并**的汇总结论；报告层若从它挑原因，
    # 「无合适替代」「载体缺失（time_budget 标记）」「载体悬空」这几类就没有记录，
    # 计划在验收链上表现为**全绿**，控制器直接 ok。这里逐类产记录。
    _bp = "预算：发声下界（对白+旁白，合计）必须装进 `time_budget` 带宽"
    _eval_problems: list = []
    if lower > bmax:
        _eval_problems.append(_problem_record(
            "budget_infeasible", "plan", None, "",
            "发声下界（对白 %.2fs / %d 句 + 旁白 %.2fs / %d 条 = **合计 %.2fs**）"
            "已超预算上限 %.0fs（画面节拍 %d 个**不构成时间下界**）"
            % (dialogue_seconds(kept), len(kept), narration_seconds(_narr_kept),
               len(_narr_kept), lower, bmax, _basis["beats"]),
            # **总量超限只有内容取舍能解**（逐句替换解决不了总量）：这是"必须删/换选
            # 条目"的修法，操作协议表达不了（它只能替换已有编号）
            fix="reselect_dialogue"))
    for _u in _unrep:
        _ui = _u.get("index")
        _uk = "narration" if str(_u.get("kind")) == "narration" else "dialogue"
        _ul = (("N%d" if _uk == "narration" else "D%d") % _ui) if _ui else \
            ("旁白" if _uk == "narration" else "台词")
        _eval_problems.append(_problem_record(
            "budget_unrepresentable", _uk, _ui,
            ("%s_lines[%d]" % (_uk, _ui - 1)) if _ui else ("%s_lines" % _uk),
            "%s 不可单镜表示（估时 %.2fs）：%s" % (_ul, float(_u.get("est_sec") or 0.0),
                                                    str(_u.get("reason") or "")),
            "预算：每句台词/旁白必须能在一镜里讲完"))
    if _no_alt:
        _eval_problems.append(_problem_record(
            "budget_no_alternative", "plan", None, "time_budget",
            "规划层明确报告：不可表示台词**无合适替代**（`time_budget` 标记）"
            "——须内容取舍（删情节/换场次/拆集），代码不代决", _bp,
            fix="reselect_dialogue"))
    if _no_carrier:
        _eval_problems.append(_problem_record(
            "carrier_missing_annotation", "plan", None, "time_budget",
            "规划层明确报告：节拍**载体缺失、无合适承载**（`time_budget` 标记）"
            "——须内容取舍，代码不代决",
            "承载关系：节拍必须标出它承载的画面/台词/旁白"))
    # 记录与 `_dangling` 用**同一句渲染**（旧实现两处各拼一份，条数口径还不一样）。
    for _i, _msg in _dangling_ix:
        _eval_problems.append(_problem_record(
            "carrier_dangling", "beat", _i, "shot_beats[%d].载体" % (_i - 1),
            _msg, "承载关系：节拍指向的声音条目必须存在"))
    return {"verdict": "infeasible" if (lower > bmax or _unrep or _no_alt
                                        or _dangling or _no_carrier
                                        or _dup_carrier or _orphan
                                        or _unlabeled) else "feasible",
            "feasible": (lower <= bmax and not _unrep and not _no_alt
                         and not _dangling and not _no_carrier
                         and not _dup_carrier and not _orphan
                         and not _unlabeled),
            "evaluable": True, "missing": [],
            "dialogue_lines": len(kept), "dialogue_sec": _dlg,
            # 旁白分账（复核第 7 轮）：发声预算 = 对白 + 旁白，重选反馈要看得见两项，
            # 否则「取材」只能砍台词、砍不掉占了大头的旁白。
            "narration_lines": len(_narr_kept),
            "narration_sec": narration_seconds(_narr_kept),
            "narration_orphan": [x for x in _orphan if str(x).startswith("N")],
            "dialogue_verified": kept, "dialogue_rejected": rejected,
            # 旁白（复核第 6 轮）：与对白同样逐字核验；`_drop_unverified` 据这两个
            # 字段把计划里的 `narration_lines` 同步收窄并重映射引用。
            "narration_verified": [e["text"] for e in _narr_kept],
            "narration_rejected": list(_narr_rejected),
            "dialogue_unrepresentable": _unrep,
            "beat_carrier_dangling": _dangling,
            "beat_carrier_duplicate": _dup_carrier,
            "dialogue_orphan": _orphan,
            "beat_carrier_unlabeled": _unlabeled,
            # 节拍的**内容读数**（不是时间下界，见 `beats_floor_basis`）；
            # 下界只取发声预算 ⇒ 用 `lower_bound_basis` 显式声明这条口径
            "beats_floor_basis": dict(_basis),
            "lower_bound_basis": "voice",
            "lower_bound_sec": lower,
            "budget": {"min": bmin, "max": bmax},
            # 生产者记录的停止原因（六字段，工作包 §2.1）——报告层只组合
            "problems": _eval_problems,
            "reasons": reasons, "suggestions": suggestions,
            "note": "下界 = 发声预算（对白 + 旁白）；画面节拍不构成时间下界"
                    "（镜数由时长预算决定，不由节拍数决定）"}


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
        _un = int(_r.get("after_unrepresentable") or 0)
        return ("规划层已重选 %d 轮后仍不可行（对白 %.2fs → %.2fs%s）⇒ 需拆集，"
                "或按 `omittable` 重选后再规划"
                % (int(_r.get("rounds") or 1),
                   float(_r.get("before_dialogue_sec") or 0.0),
                   float(_r.get("after_dialogue_sec") or 0.0),
                   "；不可表示台词 %d 句" % _un if _un else ""), "", False)
    return "", "", False


def _log():
    """复用主流程 logger；独立调用（如单测）时退回标准 logging（不制造 import 环）."""
    try:
        from step01_script_gen.main import _logger as lg
        return lg
    except Exception:
        import logging
        return logging.getLogger("step01.plan")


def _remap_dialogue_refs(plan, old_lines, kept_lines) -> list:
    """台词收窄后**重映射全部引用**（节拍载体 / 事实承载 / 钩子承载）；返回作废说明.

    2026-09-30 复核第 3 组：剔除未核验台词会**压缩 D 编号**，而引用仍按旧编号写——
    `｜载体：台词k`、`｜承载：Bn/台词k`、`hook_carrier` 于是静默指到**另一句**台词上
    （新旧编号都「在范围内」，没有任何判据会报）。这里按位置映射（保留的原序子序列）
    重写编号；承载的那句本身被剔除时，**去掉**该处的承载登记并如实记一条说明——
    由调用方重新过契约门（缺承载会被显式判为契约未完成，不静默留半份引用）。
    """
    if not isinstance(plan, dict):
        return []
    _kept = list(kept_lines or [])
    _map: dict = {}
    _j = 0
    for _i, _line in enumerate(old_lines or [], 1):
        if _j < len(_kept) and _kept[_j] == _line:
            _j += 1
            _map[_i] = _j
    notes: list = []
    _beats = []
    for _i, _b in enumerate(plan.get("shot_beats") or [], 1):
        _rec = BeatRecord.parse(str(_b))
        _kind, _k = _rec.carrier()
        if _kind == "dialogue" and _k:
            _nk = _map.get(_k)
            if _nk:
                _rec.set_carrier("dialogue", _nk)
            else:
                _rec.drop(BEAT_SEG_CARRIER)
                notes.append("B%d 的承载台词（原 D%d）未在原文逐字找到、已剔除 ⇒ 该节拍"
                             "现在没有载体" % (_i, _k))
        _beats.append(_rec.render())
    if _beats:
        plan["shot_beats"] = _beats
    _facts = []
    for _i, _f in enumerate(plan.get("required_facts") or [], 1):
        _p = parse_carrier_decl(fact_carrier(_f))
        if _p and _p[1] == "台词":
            _nk = _map.get(_p[2])
            if _nk:
                _facts.append("%s｜%sB%d/台词%d" % (strip_fact_carrier(_f),
                                                   FACT_CARRIER_SEG, _p[0], _nk))
            else:
                _facts.append(strip_fact_carrier(_f))
                notes.append("F%d 的承载台词（原 D%d）未在原文逐字找到、已剔除 ⇒ 该事实"
                             "现在没有承载" % (_i, _p[2]))
        else:
            _facts.append(str(_f))
    if _facts:
        plan["required_facts"] = _facts
    _hc_out: list = []
    _hc_lost = False
    for _x in re.split("[;；,、]", str(plan.get("hook_carrier") or "")):
        _p = parse_carrier_decl(_x)
        if _p is None:
            if _x.strip():
                _hc_out.append(_x.strip())
            continue
        if _p[1] != "台词":
            _hc_out.append("B%d/画面" % _p[0])
            continue
        _nk = _map.get(_p[2])
        if _nk:
            _hc_out.append("B%d/台词%d" % (_p[0], _nk))
        else:
            _hc_lost = True
    if _hc_lost:
        plan.pop("hook_carrier", None)
        notes.append("钩子承载的台词未在原文逐字找到、已剔除 ⇒ hook_carrier 一并作废")
    elif _hc_out:
        plan["hook_carrier"] = "；".join(_hc_out)
    return notes


def _remap_narration_refs(plan, old_entries, kept_entries) -> list:
    """旁白条目收窄后**重映射全部旁白引用**（节拍载体 `旁白k` / 事实承载 `Bn/旁白k`）.

    2026-09-30 复核第 6 轮：旁白与台词一样按**序号**被引用，删掉一条就会让后面的编号
    整体前移——不重映射就是「同一份计划里两个地方各指一条旁白」。被剔除的那条旁白
    仍被引用时，**去掉该处的旁白登记**并如实记一条说明，由调用方重过契约门（缺承载
    显式失败，不静默留半份引用）。
    """
    if not isinstance(plan, dict):
        return []
    _kept_texts = [str(e.get("text") or "") for e in (kept_entries or [])]
    _map: dict = {}
    _j = 0
    for _i, _e in enumerate(old_entries or [], 1):
        _t = str(_e.get("text") or "")
        if _j < len(_kept_texts) and _kept_texts[_j] == _t:
            _j += 1
            _map[_i] = _j
    notes: list = []
    _beats = []
    for _i, _b in enumerate(plan.get("shot_beats") or [], 1):
        _rec = BeatRecord.parse(str(_b))
        _kind, _k = _rec.carrier()
        if _kind == "narration" and _k:
            _nk = _map.get(_k)
            if _nk:
                _rec.set_carrier("narration", _nk)
            else:
                _rec.drop(BEAT_SEG_CARRIER)
                notes.append("B%d 的承载旁白（原 N%d）未在原文逐字找到、已剔除 ⇒ 该节拍"
                             "现在没有载体" % (_i, _k))
        _beats.append(_rec.render())
    if _beats:
        plan["shot_beats"] = _beats
    _facts = []
    for _i, _f in enumerate(plan.get("required_facts") or [], 1):
        _p = parse_carrier_decl(fact_carrier(_f))
        if _p and _p[1] == "旁白":
            _nk = _map.get(_p[2])
            if _nk:
                _facts.append("%s｜%sB%d/旁白%d" % (strip_fact_carrier(_f),
                                                   FACT_CARRIER_SEG, _p[0], _nk))
            else:
                _facts.append(strip_fact_carrier(_f))
                notes.append("F%d 的承载旁白（原 N%d）未在原文逐字找到、已剔除 ⇒ 该事实"
                             "现在没有承载" % (_i, _p[2]))
        else:
            _facts.append(str(_f))
    if _facts:
        plan["required_facts"] = _facts
    return notes


def _drop_unverified(plan, rep: dict) -> list:
    """把计划里的 `dialogue_lines` 就地收窄为**核验通过**的那一组.

    这是本模块**唯一**改计划的地方，且只删"原文里根本没有的台词"；预算不够走
    `infeasible` 分支、**不动计划**。收窄后生成侧（`outline_to_anchor` 等）只能看到
    核验过的台词——"计划是本集内容取舍的权威"靠这一步落地。

    v6.156（2026-09-30 复核第 3 组）：收窄会压缩 D 编号，故**同步重映射全部引用**
    （见 `_remap_dialogue_refs`），返回"作废的引用"说明；调用方据此**重新过契约门**，
    缺承载显式失败，不留半份引用给下游。
    """
    if not isinstance(plan, dict):
        return []
    raw = plan.get("dialogue_lines")
    if not isinstance(raw, list):
        return []
    kept = list(rep.get("dialogue_verified") or [])
    _notes: list = []
    if raw != kept:
        plan["dialogue_lines"] = kept
        _notes = _remap_dialogue_refs(plan, raw, kept)
    # 旁白同样只保留**原文里逐字找到**的那些（复核第 6 轮）
    _narr_raw = plan_narration_entries(plan)
    _narr_str = [str(x.get("text") or "") for x in (plan.get("narration_lines") or [])
                 if isinstance(x, dict)]
    _narr_kept = [e for e in _narr_raw
                  if e["text"] in set(rep.get("narration_verified") or [])]
    if len(_narr_kept) != len(_narr_str):
        _notes += _remap_narration_refs(plan, _narr_raw, _narr_kept)
        plan["narration_lines"] = _narr_kept
        _nrej = [str(x)[:24] for x in (rep.get("narration_rejected") or [])]
        if _nrej:
            _notes.append("剔除的旁白：%s" % "、".join(_nrej[:3]))
    _rej = [str(d.get("line"))[:24] for d in (rep.get("dialogue_rejected") or [])]
    return (_notes + ["剔除的台词：%s" % "、".join(_rej[:3])]) if _rej else _notes


def _core_snapshot(plan) -> dict:
    """变换**不许动**的核心字段快照（事实本体与出处、核心事件与顺序）.

    「只许改载体段/引用，不许改事实本体、原文证据、事件与因果链」是既定契约
    （见 `_remap_dialogue_refs` / `apply_replan_ops` 的同一口径），候选副本采纳前照此核对。

    **类型安全**（复核第 43 轮 P1）：候选可能被变换改成非数组（实测把 `required_facts`
    写成 `7`），旧写法 `for f in 7` 抛 `TypeError`、冲出事务边界——守卫自己成了异常源，
    统一返回协议被绕过。快照只需要**可比**：非数组原样入快照，于是"把数组改成数字"照样
    判成动了核心字段（fail-closed），而不再抛异常。
    """
    _p = plan if isinstance(plan, dict) else {}
    _raw = _p.get("required_facts")
    _facts = ([strip_fact_carrier(str(f)) for f in _raw] if isinstance(_raw, list) else _raw)

    def _d(_v):
        # `default=repr`：任何类型都能落成稳定的可比串（不因不可序列化再抛异常）
        return json.dumps(_v, ensure_ascii=False, sort_keys=True, default=repr)

    return {"main_events": _d(_p.get("main_events")), "climax": _d(_p.get("climax")),
            "causal_chain": _d(_p.get("causal_chain")), "facts": _d(_facts)}


def _classify_problem_class(problems) -> str:
    """候选问题的**类别**（决定入口用哪种停止语义）：`contract` / `budget` / `none`."""
    _codes = [str((x or {}).get("code") or "") for x in (problems or [])]
    if not _codes:
        return "none"
    if any(c.startswith(("carrier", "orphan", "structure", "source", "acceptance")) for c in _codes):
        return "contract"
    return "budget"


def _tx_operation_error(tx: dict) -> dict:
    """变换事务里的**操作错误**（不是"验收发现的内容问题"；复核第 43 轮 P2）.

    返回 `{"reason": ..., "type": ..., "message": ...}`：`type`/`message` 只在真的捕获到异常时
    才有（`exception` / `reverify_failed`）；守卫拒收（`core_moved` / `axis_mismatch`）没有异常，
    只记拒收理由。
    """
    _err = str(tx.get("error") or "")
    if not _err:
        return {"reason": str(tx.get("reason") or ""), "type": "", "message": ""}
    _t, _, _m = _err.partition(": ")
    return {"reason": str(tx.get("reason") or ""), "type": _t, "message": _m}


def dump_plan_transform_evidence(chapter_dir, tx: dict) -> None:
    """**拒收候选的取证包** `step01_plan_transform_rejected.json`（复核第 42/43 轮）.

    角色与**主诊断**（`DIAGNOSTIC_FILES` = 本轮结论）**分开**：

    - 主诊断说的是**结论**：`step01_plan_contract_invalid.json` 装"已完成验收的问题清单"
      （可逐字回放）、`step01_plan_infeasible.json` 装预算/操作层结论；
    - 本文件是**证据**：候选与基线两份计划 + 各自指纹 + 变换注记 + 操作错误 +
      **实际跑到的**验收问题清单（`acceptance_ran` 为真时才有）。
      它**不参与"本轮停在哪个文件"的选择**，但走**同一套**认领／归档／清场流程
      （`DIAGNOSTIC_ARTIFACT_FILES`），按自带 `run_id` 认领——否则它既不被归档，
      清场也清不掉，后来的同名写入还会把它覆盖（复核第 43 轮 P3 实测）。

    **所有被拒候选统一留证据**：内容拒收、操作失败、只剩预算类的未采纳，都落这一份。
    """
    if chapter_dir is None or not isinstance(tx, dict):
        return
    from pathlib import Path
    from step01_script_gen.plan_revision import revision_fingerprint
    _ran = bool(tx.get("acceptance_ran"))
    _dump_diagnostic_json(
        Path(chapter_dir) / "step01_plan_transform_rejected.json",
        dict(_diag_identity(STOP_LAYER_PLAN, tx.get("candidate")), **{
            "role": "candidate_forensics",
            "verdict": "plan_transform_rejected",
            "conclusion": "candidate_not_accepted",
            "reason": tx.get("reason"),
            "problem_class": _classify_problem_class(tx.get("problems")),
            "notes": list(tx.get("notes") or []),
            "operation_error": _tx_operation_error(tx),
            # **只有真跑完验收**才有问题清单（复核第 43 轮 P2：旧写法把异常文案塞进
            # `problems`，回放必然对不上——那不是验收的结论）
            "acceptance_ran": _ran,
            "problems": list(tx.get("problems") or []) if _ran else [],
            "new_problems": [x.get("code") for x in (tx.get("new_problems") or [])] if _ran else [],
            "candidate_plan": tx.get("candidate"),
            "candidate_fingerprint": revision_fingerprint(tx.get("candidate")),
            "baseline_plan": tx.get("baseline"),
            "baseline_fingerprint": revision_fingerprint(tx.get("baseline")),
            "budget": (tx.get("report") or {}).get("budget")}),
        what="计划变换否决取证")


def dump_plan_transform_failed(chapter_dir, tx: dict, rep: dict) -> None:
    """**操作层**失败（变换/守卫/重验）的结论：`step01_plan_infeasible.json`.

    与**内容结论分开记录**（复核第 43 轮 P2）：`step01_plan_contract_invalid.json` 只承载
    「**已完成验收**的问题清单」，异常文案不属于那里。

    `verdict` 取既有的 `unevaluable`——它的语义正是「**判不了**，fail-closed」，据此**不**下
    "故事装不下／需拆集"的结论；`conclusion` 写 `transform_failed`，`operation_error` 给出
    异常类型与消息。基线读数（对白/预算）照旧落盘：那是**手上这份计划**的真实读数，
    不是候选的结论。
    """
    if chapter_dir is None or not isinstance(rep, dict):
        return
    from step01_script_gen.plan_revision import revision_fingerprint
    _rep = dict(rep)
    _rep["verdict"] = "unevaluable"
    _rep["reasons"] = ["计划变换未采纳（%s）——候选无法在生成前验算：%s"
                       % (tx.get("reason"), "；".join(str(x) for x in (tx.get("notes") or [])[:2]))]
    _rep["suggestions"] = ["修正计划或重跑规划层后重试；不要带着未验算的候选生成完整稿"
                           "（基线计划未被改动、未被下发）"]
    _dump(chapter_dir, _rep, tx.get("baseline"), conclusion="transform_failed",
          extra={"reason": tx.get("reason"),
                 "operation_error": _tx_operation_error(tx),
                 "acceptance_ran": bool(tx.get("acceptance_ran")),
                 "notes": list(tx.get("notes") or []),
                 "candidate_plan": tx.get("candidate"),
                 "candidate_fingerprint": revision_fingerprint(tx.get("candidate")),
                 "baseline_fingerprint": revision_fingerprint(tx.get("baseline"))})


def _unevaluable_rep(reason: str, suggestion: str) -> dict:
    """`verdict=unevaluable` 的最小报告（"判不了"的公共读数：**没有**对白/预算结论）."""
    return {"verdict": "unevaluable", "reasons": [reason], "suggestions": [suggestion],
            "dialogue_lines": 0, "dialogue_sec": 0.0, "dialogue_rejected": [],
            "lower_bound_sec": None, "budget": {"min": 0.0, "max": 0.0}, "note": ""}


def dump_plan_deadline_exhausted(chapter_dir, plan=None, *,
                                 deadline_monotonic=None, detail: str = "") -> None:
    """**时间门**的结论：`step01_plan_infeasible.json`（工作包 §4 D）.

    与"故事不可行"**分账**：截止已到时，计划的内容**根本没有被判定**（`verdict` 记
    `unevaluable`——语义正是"判不了，fail-closed"），`conclusion` 记 `deadline_exhausted`。
    下游据此知道该做的是"重跑／给时间"，而不是"拆集／换内容"。

    内容判据**故意不在这里跑**：它们要花的时间正是这道门要保护的东西；计划未被改动、
    未被缓存，下一次运行会从头重新判定（不会把"没判过"当成"判过了"）。
    """
    if chapter_dir is None:
        return
    from step01_script_gen.plan_revision import revision_fingerprint
    _dump(chapter_dir,
          _unevaluable_rep(detail or "规划截止时刻已到，候选扇出前停止",
                           "重跑本节（预算/截止放宽）或缩小本次规划范围；"
                           "**不要**按「故事装不下」处置——这条结论没有下过"),
          plan, conclusion="deadline_exhausted",
          extra={"deadline_monotonic": deadline_monotonic,
                 "plan_fingerprint": revision_fingerprint(plan)})


def _record_text(rec) -> str:
    """（兼容名）问题记录 → 一行可读文本；实现归一到 `plan_revision.record_text` 一处."""
    from step01_script_gen.plan_revision import record_text
    return record_text(rec)


def dump_plan_unavailable(chapter_dir, *, code: str, detail: str = "", plan=None,
                          baseline_problems=None, failure_problems=None,
                          replan_rejections=None) -> None:
    """**计划没产出**的结论：`step01_plan_infeasible.json`（工作包 §4 D / §7.6）.

    用于"首版都没拿到"的三种情形（`call_failed` / `illegal_response` / `outline_invalid`）
    ——它们都是**"这一轮没跑成"**：`verdict=unevaluable`（判不了）、`conclusion=<失败码>`。
    与"故事不可行"分账：下游据此该看通道/重跑，而不是拆集换内容。

    **这些失败就是主终局**（复核 2026-10-02 之三 P1）：即使手上还留着一份**基线计划**，
    也不能拿基线的内容结论去顶替它——旧写法把调用失败交给生成前内容门，终局变成
    `plan_contract_invalid`（`PlanContractInvalidError`），真实的调用失败只剩在修订历史里，
    归因反了。基线的内容问题用 `baseline_problems` **单独记录**（`extra` 里另存一栏），
    它与"本轮为什么没跑成"是两件事。

    `baseline_problems` **类型稳定**（复核 2026-10-02 之四 D-1）：收列表 / 单条 / 字符串，
    逐条经 `record_text` 归一成字符串——旧写法逐项 `.get()`，兜底项是字符串时抛
    `AttributeError`，整份诊断一次也没写成。

    `failure_problems`（复核 2026-10-02 之五）：**本轮响应为什么被拒**的**具体记录**
    （哪条规则、哪个字段、实际值是什么），由生产者给（`outline_structure_problems` /
    本地响应校验），这里原样落盘——终局诊断只有一句"关键字段缺失/类型错误"时，
    既指不出规则也**回灌不了**（下一次重跑/下一轮请求没有可操作信息）。

    `replan_rejections`（复核 2026-10-02 之五）：修订控制器**逐轮**的拒收记录
    （第几轮、什么类型、具体字段与原因）。`detail` 只留**最后一条**——留档要宽：
    三轮各自为什么被拒收是收敛分析的输入，只留最后一条等于把轨迹丢掉。
    """
    if chapter_dir is None:
        return
    from step01_script_gen.plan_revision import problem_records, record_text, revision_fingerprint
    _out = {"failure_code": str(code), "detail": detail,
            "plan_fingerprint": revision_fingerprint(plan)}
    if baseline_problems is not None:
        _out["baseline_problems"] = [record_text(x)
                                     for x in problem_records(baseline_problems)]
    if failure_problems:
        _out["failure_problems"] = [x for x in problem_records(failure_problems)]
    if replan_rejections:
        _out["replan_rejections"] = [dict(x) if isinstance(x, dict) else {"detail": str(x)}
                                     for x in problem_records(replan_rejections)]
    _dump(chapter_dir,
          _unevaluable_rep("%s：%s" % (code, detail or "计划未产出"),
                           "这是**这一轮没跑成**（不是故事不可行）：核对文本通道/响应后重跑；"
                           "计划未被改动、未被缓存"),
          plan, conclusion=str(code), extra=_out)


#: **调用类**失败码（"这一轮没跑成"，§7.2 三类分账之一）：有基线计划时也**不许**被
#: 生成前内容门的结论顶替——终局就是它，基线的内容问题另列（复核 2026-10-02 之三 P1）。
#:
#: `invalid_target`（复核 2026-10-08 P2 第 5 条）同属这一类：本次诊断**指向的修改实体
#: 不存在/身份写错**是**上游记录契约失效**（生产者写错了身份），不是"这份计划不行"。
#: 不登记在这里的话，它会掉进"拿基线过生成前门"那一支，被 `plan_contract_invalid` 顶替
#: ——终局归因反了（真实的身份失效只剩在修订历史里），与 §7.2 的分账口径相违。
OUTLINE_CALL_FAILURE_CODES = ("call_failed", "illegal_response", "outline_invalid",
                              "invalid_target")


def apply_plan_transforms(plan, rep: dict, *, novel_text: str = "") -> dict:
    """**候选副本上的变换事务**（工作包 §3 / A 步 5）：剔除未核验声音 + 引用重映射.

    流程：深复制候选 → 确定性剔除并**按原序号**重映射全部 D/N/B/F/hook 引用 → 核对候选
    **没有动核心字段**（事实本体与出处、核心事件与顺序）→ **D/N 两轴**收窄结果必须等于
    核验通过集合 → **在候选上跑完整验收** → 通过才**就地把候选写回传入的计划**。

    **可执行资格**（复核第 42 轮纠正）：采纳条件是**完整验收通过**，不是"没有新增问题"。
    结构不合格是**原计划**的问题，但它同样不能被采纳、不能被下发。新旧问题差分
    （`new_problems`）**只用于解释变化**。

    **结果形态（所有分支一致）**：`{plan, adopted, reason, notes, baseline, candidate,
    problems, report, new_problems, acceptance_ran, error}`——异常/守卫/重验异常分支也带全键
    （复核第 42 轮 P1：旧写法异常分支缺 `report`，真实入口直接 `KeyError`；第 43 轮 P1：
    守卫自己抛异常时**整份结果都没有**）。

    - `plan`：调用方该用的计划（采纳=候选；未采纳=**基线**，逐字未改）
    - `reason`：`ok` / `exception` / `core_moved` / `axis_mismatch` / `reverify_failed` / `not_acceptable`
    - `problems`：**候选**的完整验收记录；`acceptance_ran`：**验收是否真的跑完**（没跑完就没有
      问题清单——复核第 43 轮 P2 说的"内容结论与操作错误分开"）
    - `report`：候选的 `evaluate_plan` 报告（候选没通过验收 ⇒ `None`）
    - `error`：捕获到的异常（`类型: 消息`），没有异常时为空串
    """
    _baseline = copy.deepcopy(plan)
    _cand = copy.deepcopy(plan)
    _res = {"plan": plan, "adopted": False, "reason": "exception", "notes": [],
            "baseline": _baseline, "candidate": _cand,
            "problems": [], "report": None, "new_problems": [],
            "acceptance_ran": False, "error": ""}
    try:
        _notes = _drop_unverified(_cand, rep)
    except Exception as _e:                       # noqa: BLE001 —— 变换异常不采纳
        _res["notes"] = ["变换异常，已保留基线计划：%r" % _e]
        _res["error"] = "%s: %s" % (type(_e).__name__, _e)
        return _res
    _res["notes"] = list(_notes or [])
    _res["candidate"] = _cand

    try:
        # 守卫自己不许成为异常源（复核第 43 轮 P1：候选被改成非数组时旧写法抛 `TypeError`，
        # 既没有诊断，也绕过了统一返回协议）
        if _core_snapshot(_cand) != _core_snapshot(_baseline):
            _res["reason"] = "core_moved"
            _res["notes"] = _res["notes"] + [
                "候选改动了核心字段（事实本体/原文证据/事件顺序）⇒ 拒绝候选，保留基线"]
            return _res

        # **D/N 两轴**核对收窄结果（复核第 41 轮 P2：此前只核对白）
        _want_d = [str(x) for x in (rep.get("dialogue_verified") or [])]
        _want_n = [str(x) for x in (rep.get("narration_verified") or [])]
        _got_d = [str(x) for x in (_cand.get("dialogue_lines") or [])]
        _got_n = [str(e.get("text") or "") for e in plan_narration_entries(_cand)]
        if _got_d != _want_d or _got_n != _want_n:
            _res["reason"] = "axis_mismatch"
            _res["notes"] = _res["notes"] + [
                "候选收窄结果与核验通过集合不一致（D/N 两轴；不许多剔也不许改序）⇒ 拒绝候选，保留基线"]
            return _res
    except Exception as _e:                       # noqa: BLE001 —— 守卫异常同样不采纳
        _res["reason"] = "exception"
        _res["notes"] = _res["notes"] + ["候选核对异常，已保留基线计划：%r" % _e]
        _res["error"] = "%s: %s" % (type(_e).__name__, _e)
        return _res

    def _key(_r):
        return (str(_r.get("code")), str(_r.get("kind")), _r.get("index"), str(_r.get("path")))

    try:
        from step01_script_gen.outline import plan_acceptance_problems
        # 基线报告只用来**解释变化**（哪些问题是这次变换新引入的），不决定采纳。
        _base_keys = {_key(_r) for _r in (plan_acceptance_problems(_baseline, novel_text) or [])}
        _problems = list(plan_acceptance_problems(_cand, novel_text) or [])
        # 验收没通过时**不再**去跑 `evaluate_plan`：候选结构已不合法，那只会再抛一次异常；
        # 而"候选不合格"已经有可回放的问题清单（复核第 43 轮 P1：入口与守卫的前置操作
        # 也要落在事务边界内）。
        _cand_rep = None if _problems else evaluate_plan(_cand, novel_text=novel_text)
    except Exception as _e:                       # noqa: BLE001 —— 重验异常同样不采纳
        _res["reason"] = "reverify_failed"
        _res["notes"] = _res["notes"] + ["候选重新验收异常，已保留基线计划：%r" % _e]
        _res["error"] = "%s: %s" % (type(_e).__name__, _e)
        return _res
    _res["acceptance_ran"] = True
    _res["problems"] = _problems
    _res["report"] = _cand_rep
    _res["new_problems"] = [_r for _r in _problems if _key(_r) not in _base_keys]

    if _problems:
        # **完整验收没通过 ⇒ 不采纳**（复核第 42 轮 P1：结构不合格不许被"没有新问题"放行）
        _res["reason"] = "not_acceptable"
        _res["notes"] = _res["notes"] + [
            "候选未通过**完整验收**（%s）⇒ 不采纳，保留基线"
            % "、".join(sorted({str(x.get("code")) for x in _problems})[:4])]
        return _res

    plan.clear()
    plan.update(_cand)                            # **采纳**（就地把候选写回）
    _res.update({"plan": plan, "adopted": True, "reason": "ok",
                 "candidate": copy.deepcopy(plan)})
    return _res


def check_plan_before_generation(plan: dict | None, *, chapter_dir=None,
                                 novel_text: str = "", deadline_monotonic=None) -> dict:
    """**生成前唯一入口**（挂于候选扇出之前）：计划契约 + 原文核验 + 预算核算，只验算一次.

    可行 → 把 `dialogue_lines` 收窄为核验通过的集合，返回报告（调用方据此记录同一组数：
    对白估时、下界、预算）。不可行/不可评估/缺计划 → 写诊断 `step01_plan_infeasible.json`
    并抛 `PlanInfeasibleError`（**不生成完整稿**）。

    2026-09-30 复核裁决：**契约门在预算门之前**。必保留事实/钩子缺承载是「取舍还没
    做完」，不是「故事装不下」——顺序颠倒会把「登记信息缺失」送进预算重选（换内容
    解决不了），并误报「需拆集」。契约未完成 → 写
    `step01_plan_contract_invalid.json` 并抛 `PlanContractInvalidError`。
    """
    # **时间门在最前**（工作包 §4 D / §7.4）：截止已到就停在计划层——这是**时间**用尽，
    # 与"故事装不下／需拆集"是两种结论（诊断按 `deadline_exhausted` 写，错误文本分开）。
    # 放在最前是因为"生成完整稿"这一步本身要花时间：内容判据再准也没有意义；
    # 计划未被改动、未被缓存，下一次运行会重新判定。
    if deadline_monotonic is not None:
        import time as _t
        if _t.monotonic() >= float(deadline_monotonic):
            dump_plan_deadline_exhausted(chapter_dir, plan,
                                         deadline_monotonic=deadline_monotonic,
                     detail="候选扇出前已过规划截止时刻（生成完整稿的时间已不足）")
            _log().warn("[PLAN] 规划截止时刻已到（deadline_exhausted）⇒ 停在计划层："
                        "这是**时间**用尽，不是「故事装不下／需拆集」")
            raise PlanInfeasibleError(
                "规划截止时刻已到（deadline_exhausted）⇒ 不生成完整稿——"
                "这是**时间**用尽，不是「故事装不下／需拆集」")
    # ⓪ **结构判据先跑**（复核第 43 轮 P1）：计划结构不合格时下游（承载/预算/变换）无法
    #    安全遍历——实测 `shot_beats=7` / `required_facts=7` 会抛裸 `TypeError`，既没有
    #    诊断留档，也绕过了统一返回协议。判定与验收链**同一份**纯诊断
    #    （`outline.outline_structure_problems`），结论与记录形态一致。
    #    "计划缺失／空计划"不走这条（那是"无法验算"，见 `evaluate_plan` 的 `unevaluable`）。
    if isinstance(plan, dict) and plan:
        from step01_script_gen.outline import outline_structure_problems
        _struct = outline_structure_problems(plan)
        if _struct:
            _rs = [str(x.get("reason") or x.get("code")) for x in _struct]
            _paths = "；".join(sorted({str(x.get("path") or "（整表）") for x in _struct})[:4])
            dump_plan_contract_invalid(chapter_dir, plan, _rs)
            _log().warn(
                "[PLAN] 计划结构不合格（%s）——**不是**「故事装不下／需拆集」，"
                "也不进预算重选：%s" % (_paths, "；".join(_rs[:3])))
            raise PlanContractInvalidError(
                "计划结构不合格（%s）⇒ 停在计划层，不生成完整稿：%s"
                % (_paths, "；".join(_rs[:6])))
    _adapt = _va.adaptation_problems(plan, novel_text)
    if _adapt:
        raise PlanContractInvalidError(
            "改编层不可消费（%s）⇒ 停在计划层：%s"
            % (_adapt[0].get("code"), _adapt[0].get("reason")))
    rep = evaluate_plan(plan, novel_text=novel_text)
    # 契约门在**预算门之前**（先判"取舍做完了没有"，再判"装不装得下"），但在
    # **缺字段判据之后**：缺 `dialogue_lines` 这类是「无法验算」（`unevaluable`），
    # 与「契约未完成」是两种结论、两份诊断，不能互相顶替（2026-09-30 复核）。
    _contract = ([] if rep["verdict"] == "unevaluable"
                 else plan_contract_problems(plan, novel_text=novel_text))
    if _contract:
        # **诊断取自唯一完整验收报告**（复核第 44 轮 P2）：触发条件仍是契约族（**停止顺序
        # 不变**：契约优先于预算、`unevaluable` 不在此停），但落盘的是完整清单——旧写法落
        # `plan_contract_problems` 的局部清单，回放走完整链，两处对不上（实测落盘 1 条、
        # 回放 2~11 条）。验收链必含契约记录（结构短路已在 ⓪ 步排除），故 `_contract` 是其子集。
        from step01_script_gen.outline import plan_acceptance_problems
        _accept_records = plan_acceptance_problems(plan, novel_text=novel_text) or []
        _accept = [str(x.get("reason") or x.get("code")) for x in _accept_records]
        dump_plan_contract_invalid(chapter_dir, plan, _accept or _contract,
                                   stop_reasons=_contract, problem_records=_accept_records)
        _log().warn(
            "[PLAN] 计划契约未完成（plan_contract_invalid）——**不是**「故事装不下／"
            "需拆集」，也不进预算重选：%s" % "；".join(_contract[:4]))
        raise PlanContractInvalidError(
            "计划契约未完成（必保留事实/钩子的承载缺失或无效）⇒ 停在计划层，"
            "不生成完整稿：%s" % "；".join(_contract[:6]))
    # 重选记录（若有）——把"重选没跑成"与"重选后仍超预算"分开写进诊断与报错（用户 2026-09-25）
    _note, _extra_sug, _no_verdict = _replan_note(plan)
    if _note:
        rep["reasons"] = list(rep["reasons"]) + [_note]
    if _no_verdict:
        rep["suggestions"] = [s for s in rep["suggestions"] if "拆集" not in s]
    if _extra_sug:
        rep["suggestions"] = list(rep["suggestions"]) + [_extra_sug]
    _log().info(
        "[PLAN] 对白 %d 句/估时 %.2fs（逐字核验剔除 %d 句；单镜不可表示 %d 句）；"
        "节拍 %d 个（画面 %d／声音 %d，**均不构成时间下界**：镜数由时长预算决定）；"
        "下界 %.2fs（发声预算）；预算 %.0f–%.0fs（%s）"
        % (rep["dialogue_lines"], rep["dialogue_sec"], len(rep["dialogue_rejected"]),
           len(rep.get("dialogue_unrepresentable") or []),
           int((rep.get("beats_floor_basis") or {}).get("beats") or 0),
           int((rep.get("beats_floor_basis") or {}).get("visual") or 0),
           int((rep.get("beats_floor_basis") or {}).get("voice") or 0),
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
    if rep.get("dialogue_unrepresentable"):
        # 准则 7：不可表示台词必须看得见（A 判据；交规划重选，不静默剔除）
        _log().warn("[PLAN] 下列选用台词超出单镜上限且无句末合法切分"
                    "（交规划重选；必保留事实不得静默删除）："
                    + "；".join("「%s…」（%s）" % (str(u.get("line"))[:40],
                                                   u.get("reason"))
                                for u in rep["dialogue_unrepresentable"]))
    _unadopted = False
    if rep["evaluable"]:
        # A 步 5：候选副本上做剔除与重映射，**完整验收通过**才采纳；否则基线原样保留
        # 并按其问题类别停在计划层（见 `apply_plan_transforms` 的结果协议）。
        _tx = apply_plan_transforms(plan, rep, novel_text=novel_text)
        _notes = _tx["notes"]
        if _tx["adopted"]:
            # 编号域已变 ⇒ 后续判定用**变换后**的报告；重选注记是本次运行的记账，一并并回
            rep = _tx["report"]
            if _note:
                rep["reasons"] = list(rep["reasons"]) + [_note]
            if _no_verdict:
                rep["suggestions"] = [x for x in rep["suggestions"] if "拆集" not in x]
            if _extra_sug:
                rep["suggestions"] = list(rep["suggestions"]) + [_extra_sug]
        else:
            # **未采纳**：基线逐字保留，诊断绑定**候选**。按候选问题的类别分派停止语义：
            #   · 契约/结构/原文类 ⇒ 计划不合格，停在计划层（PlanContractInvalidError）
            #   · 异常 / 守卫拒收 / 重验异常 ⇒ 候选与基线都不可执行，直接停
            #   · 只剩预算类 ⇒ 退回既有 verdict/重选路径（基线报告未变，重选注记仍在）
            _cls = _classify_problem_class(_tx["problems"])
            _log().warn("[PLAN] 计划变换未采纳（reason=%s；%s）⇒ 基线未改"
                        % (_tx["reason"], "；".join(_notes[:2])))
            # **所有拒收候选统一留证据**（复核第 43 轮 P3）：内容拒收、操作失败、只剩预算类
            # 的未采纳都落这一份；它与主诊断同走认领/归档/清场，但**不冒充结论**
            dump_plan_transform_evidence(chapter_dir, _tx)
            _why = "；".join(str(x.get("reason") or x.get("code"))
                             for x in (_tx["problems"] or [])[:3]) or "；".join(_notes[:3])
            if _cls == "contract":
                # **内容结论**：候选**已完成验收**，问题清单可逐字回放（绑候选，不绑基线）
                dump_plan_contract_invalid(
                    chapter_dir, _tx["candidate"],
                    [str(x.get("reason") or x.get("code")) for x in (_tx["problems"] or [])],
                    stop_reasons=[str(x.get("reason") or x.get("code"))
                                  for x in (_tx["problems"] or [])
                                  if _classify_problem_class([x]) == "contract"])
                raise PlanContractInvalidError(
                    "变换后计划未通过完整验收（候选未采纳，基线未改）⇒ 不生成完整稿：%s" % _why)
            if _tx["reason"] in ("exception", "core_moved", "axis_mismatch", "reverify_failed"):
                # **操作错误**（不是验收发现的内容问题）：写"判不了"的结论，不伪造问题清单
                dump_plan_transform_failed(chapter_dir, _tx, rep)
                raise PlanInfeasibleError(
                    "计划变换未采纳（%s，基线未改）⇒ 不生成完整稿：%s" % (_tx["reason"], _why))
            # 预算类：不采纳、也不在这里停——交给下面的 verdict 分支（含重选注记）
            _unadopted = True
        if _notes:
            _after = plan_contract_problems(plan, novel_text=novel_text)
            if _after:
                # 同上：**触发**用契约族（语义不变），**落盘**用完整验收报告（回放一致）
                from step01_script_gen.outline import plan_acceptance_problems
                _accept_after = [str(x.get("reason") or x.get("code"))
                                 for x in (plan_acceptance_problems(plan, novel_text=novel_text) or [])]
                dump_plan_contract_invalid(chapter_dir, plan, _accept_after or _after,
                                           stop_reasons=_after)
                _log().warn(
                    "[PLAN] 剔除未核验台词后承载关系断开（%s）⇒ 停在计划层：%s"
                    % ("；".join(_notes[:2]), "；".join(_after[:3])))
                raise PlanContractInvalidError(
                    "剔除未核验台词后承载关系断开（承载的那句原文里没有，剔除后引用作废）"
                    "⇒ 不生成完整稿：%s" % "；".join(_after[:4]))
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
    if _unadopted:
        # 变换没被采纳（候选只剩预算类问题），而基线自己判了 `feasible` —— 说明**未核验的
        # 声音仍在计划里**：不许拿它下发（复核第 42 轮：未通过候选不得交给生成）。
        # 结论照旧落盘（"判不了"），取证包已在未采纳分支写过。
        _dump(chapter_dir, dict(rep, verdict="unevaluable", reasons=list(rep["reasons"])),
              plan, conclusion="candidate_not_accepted")
        raise PlanInfeasibleError(
            "计划变换未采纳且基线不可下发（候选未通过完整验收）⇒ 不生成完整稿：%s"
            % "；".join(rep["reasons"])[:200])
    return rep


def _dump(chapter_dir, rep: dict, plan=None, *, no_verdict: bool = False,
          conclusion: str = "", extra: dict | None = None) -> None:
    """把判定落成**诊断产物**（正式三件套不动）——准则 7：失败必须看得见.

    `verdict` 记的是**计划本身的读数**（事实：超预算 / 无法验算），`conclusion` 记的是
    **可以据此下的结论**：`teardown`（确认装不下）/ `replan_incomplete`（重选没跑成，
    尚无结论，用户 2026-09-25）。

    `conclusion` 参数可**显式给定**（`dump_plan_transform_failed` 用它写 `transform_failed`：
    操作层失败——判不了，`verdict=unevaluable`）；`extra` 追加该场景特有的字段
    （拒收原因、异常、候选指纹……），不覆盖上面的公共读数。
    """
    if chapter_dir is None:
        return
    plan_replan = (plan or {}).get("plan_replan") if isinstance(plan, dict) else None
    # 身份字段与契约诊断同款：**停止在这一层时，这一层的诊断要能自证是哪一轮、
    # 哪一版契约、哪一层**（复核 2026-10-01「诊断产物身份」）。
    from pathlib import Path
    _payload = dict(_diag_identity(STOP_LAYER_PLAN, plan), **{
            "verdict": rep["verdict"],
            "conclusion": (conclusion or ("replan_incomplete" if no_verdict
                                          else ("teardown" if rep["verdict"] == "infeasible" else ""))),
            "reasons": rep["reasons"],
            "suggestions": rep["suggestions"],
            "dialogue_lines": rep["dialogue_lines"],
            "dialogue_sec": rep["dialogue_sec"],
            # 旁白分账（与契约诊断对齐：发声预算 = 对白 + 旁白）
            "narration_lines": rep.get("narration_lines"),
            "narration_sec": rep.get("narration_sec"),
            "dialogue_rejected": rep["dialogue_rejected"],
            "dialogue_unrepresentable": rep.get("dialogue_unrepresentable") or [],
            "beats_floor_basis": rep.get("beats_floor_basis"),
            "lower_bound_basis": rep.get("lower_bound_basis"),
            "lower_bound_sec": rep["lower_bound_sec"],
            # v6.132：规划层是否已经重选过一轮、重选前后各是多少——
            # 「重选后仍超限」与「压根没重选」是两种不同的处置依据
            "replan": (plan_replan if isinstance(plan_replan, dict) else None),
            "budget": rep["budget"], "note": rep["note"]})
    if extra:
        # 该场景特有的字段（操作层失败的原因/异常/候选指纹）——公共读数不被覆盖
        _payload.update(extra)
    _dump_diagnostic_json(Path(chapter_dir) / "step01_plan_infeasible.json", _payload,
                          what="预算诊断")


# ---------------------------------------------------------------------------
# 版本 6（`screen_hybrid`）：读**结构化关系**的计划契约验收（主专题 §2/§3.3）
# ---------------------------------------------------------------------------
def presentation_records(plan, *, novel_text: str = "", verification=None,
                         require_verification: bool = True) -> list:
    """v6 计划契约验收：**只核形状、引用、出处、时长与证据完整性**.

    语义（"这段正文是否完整表达了这条事实"）**不在这里判**——那是 `shared.content_verification`
    的独立核验（R4 / 主专题 §3.3）。旧模式的逐分句文字重合门是**对"用文字复述"这一形态
    的近似**；新模式允许"画面演出 + 卡片补足"的联合承载，那个近似既会误收（只重合 6 字）
    也会误拒（合法演出不复述引文）。所以这里不叠加它。

    核什么：

    1. `presentation` 块存在、schema/mode 正确；`narration_lines` 必须显式为空（模式合同）；
    2. `beat_uids` 与节拍**一一对应**（数量与稳定性）；
    3. 每条冻结事实**恰好一项** `fact_bindings`，载体非空且引用可解析（悬空即拒收，
       不按相似度猜）；
    4. 每个载体的**实际正文**：声音/卡片必须**逐字来自原文**；画面节拍必须有正文；
    5. 卡片**阅读预算**（"放得进去"不等于"读得完"，两张预算分别判）；
    6. 钩子绑定与 `hook_spoken`/`hook_carrier` 一致；
    7. 该计划摘要**必须有内容核验记录且结论全是 `supported`**（R4：缺记录不得判通过）。
    """
    from shared.presentation_contract import (KIND_CARD, KIND_DIALOGUE, KIND_NARRATION,
                                              KIND_VISUAL, PRESENTATION_SCHEMA,
                                              MODE_HYBRID, parse_view, ref_uid,
                                              view_from_plan, view_problems)
    problems: list = []
    _basis = ("新模式契约：`presentation` 结构化关系是唯一承载事实源；画面/卡片/声音必须"
              "有实际正文，来源逐字可核，卡片阅读预算可满足，且必须有内容核验记录")
    _pres = plan.get("presentation")

    def _add(code, kind, index, path, reason, fix="", uid="", fact_uid=""):
        _record = _problem_record(code, kind, index, path, reason, _basis, fix=fix,
                                  fact_uid=fact_uid)
        if uid:
            _record["uid"] = uid
        problems.append(_record)

    if not isinstance(_pres, dict):
        _add("presentation_block_missing", "plan", None, "presentation",
             "v6 计划必须带 `presentation` 块（schema=%s）——新模式的结构化关系是唯一"
             "承载事实源，不能退回解析 `｜承载：` 文案" % PRESENTATION_SCHEMA)
        return problems
    if str(_pres.get("schema") or "") != PRESENTATION_SCHEMA:
        _add("presentation_schema_unknown", "plan", None, "presentation.schema",
             "`presentation.schema` 必须是 %s（现在是 %r）"
             % (PRESENTATION_SCHEMA, _pres.get("schema")))
        return problems
    if str(_pres.get("mode") or "") != MODE_HYBRID:
        _add("presentation_mode_invalid", "plan", None, "presentation.mode",
             "`presentation.mode` 必须是 %s（现在是 %r）" % (MODE_HYBRID, _pres.get("mode")))
        return problems
    if plan.get("narration_lines"):
        _add("presentation_narration_forbidden", "plan", None, "narration_lines",
             "新模式必须显式 `narration_lines=[]`——图外旁白不再生成独立音轨，必要的"
             "信息要重新分配给画面或文字卡；**不自动清空**、也不静默降级")

    # 解析成视图（引用闭合、uid 唯一、模式合同都在这一处）
    _view, _vp = view_from_plan(plan)
    if _view is None:
        for _x in _vp:                      # 逐条透传，不压成一条"读不成"
            _add(str(_x.get("code") or "presentation_unreadable"),
                 str(_x.get("kind") or "plan"), None,
                 str(_x.get("path") or ""), str(_x.get("reason") or ""),
                 fix=str(_x.get("fix") or ""), uid=str(_x.get("ref") or ""))
        return problems
    # ⚠️ 读取准入现在是**二值**的（`view is None` ⇔ `problems` 非空），所以这里没有
    # "视图读得出来、但读盘同时报了问题"这一档——旧写法考虑过这一档（"模式合同/类型
    # 已被拒"），那是按码名白名单判可读性时的残留。模式合同（新模式带旁白）改由下面的
    # `parse_view` 报 `view_hybrid_narration_forbidden`，一条不漏。
    _pv, _vpp = parse_view(_view, expected_mode=MODE_HYBRID)
    if _pv is None:
        for _x in _vpp:
            _add(str(_x.get("code") or "presentation_relation_invalid"),
                 str(_x.get("kind") or "plan"), None,
                 str(_x.get("path") or ""), str(_x.get("reason") or ""),
                 fix=str(_x.get("fix") or ""), uid=str(_x.get("ref") or ""))
        return problems
    for _x in view_problems(_view):
        _add(str(_x.get("code") or "presentation_relation_invalid"),
             str(_x.get("kind") or "plan"), None,
             str(_x.get("path") or ""), str(_x.get("reason") or ""),
             fix=str(_x.get("fix") or ""), uid=str(_x.get("ref") or ""))

    # `beat_uids` 与节拍一一对应（数量与稳定性）
    _uids = [str(x) for x in (_pres.get("beat_uids") or [])]
    _beats = list(plan.get("shot_beats") or [])
    if len(_uids) != len(_beats):
        _add("presentation_beat_uids_mismatch", "plan", None, "presentation.beat_uids",
             "`beat_uids` 有 %d 项而节拍有 %d 条——节拍 uid 必须与节拍一一对应"
             % (len(_uids), len(_beats)))
    elif len(set(_uids)) != len(_uids):
        _add("presentation_beat_uids_duplicate", "plan", None, "presentation.beat_uids",
             "`beat_uids` 里有重复 uid：%s" % _uids)

    _novel = _source_norm(novel_text)
    _beat_by_uid = {str(b.get("uid")): b for b in _view["beats"]}
    _card_by_uid = {ref_uid(c): c for c in _view["screen_cards"]}
    _unit_by_uid = {}
    for _u in _view["dialogue_units"]:
        _unit_by_uid[str(_u["uid"])] = ("dialogue", _u)
    for _u in _view["narration_units"]:
        _unit_by_uid[str(_u["uid"])] = ("narration", _u)

    _reading_sec = 0.0
    #: **按卡片去重后**累计阅读时间（工作包 3 第 3 条）：一张卡被 N 条事实引用时，
    #: 观众**只读一遍**。原先在绑定循环里累加，于是"一条卡承载三条事实"被计三次，
    #: 预算凭空翻三倍——合法计划被误判成超预算。阅读时间按**实际卡片/分页**算一次。
    _read_cards: set = set()
    _fact_indexes = {f["uid"]: i for i, f in enumerate(_view["frozen_facts"], 1)}
    for _binding_i, _b in enumerate(_pres.get("fact_bindings") or [], 1):
        _fu = _b["fact_uid"]
        _i = _fact_indexes[_fu]
        for _ci, _c in enumerate((_b or {}).get("carriers") or []):
            _kind = str((_c or {}).get("kind") or "")
            _path = "presentation.fact_bindings[%d].carriers[%d]" % (_binding_i - 1, _ci)
            _uid = ref_uid(_c)
            if _kind == KIND_VISUAL:
                _bt = _beat_by_uid.get(_uid)
                if _bt is None:
                    _add("presentation_carrier_dangling", "fact", _i, _path,
                         "%s 指向不存在的节拍 %r" % (_fu, _uid), uid=_fu)
                    continue
                if not str(_bt.get("text") or "").strip():
                    _add("presentation_visual_body_missing", "fact", _i, _path,
                         "%s 声明由节拍 %s 的**画面**承载，但该节拍没有正文（画面里"
                         "什么都没有）" % (_fu, _uid), uid=_fu)
                _ev = str(_bt.get("evidence") or "")
                if not _ev.strip():
                    _add("presentation_beat_evidence_missing", "beat", None,
                         "shot_beats[%s].原文依据" % _uid,
                         "节拍 %s 没有「原文依据」——画面承载必须有原文出处" % _uid, uid=_uid)
                elif _novel and _source_norm(_ev) not in _novel:
                    _add("presentation_beat_evidence_not_in_novel", "beat", None,
                         "shot_beats[%s].原文依据" % _uid,
                         "节拍 %s 的「原文依据」不在原文里（逐字核验未通过）" % _uid, uid=_uid)
            elif _kind == KIND_CARD:
                _card = _card_by_uid.get(_uid)
                if _card is None:
                    _add("presentation_carrier_dangling", "fact", _i, _path,
                         "%s 指向不存在的卡片 %r" % (_fu, _uid), uid=_fu)
                    continue
                _t = str(_card.get("text") or "").strip()
                if not _t:
                    # **实体是卡片**（`uid` = 卡片身份），受影响事实另记（复核 P1）
                    _add("presentation_card_text_empty", "card", None,
                         "presentation.screen_cards[%s].text" % _uid,
                         "卡片 %s 没有正文（它承载事实 %s）" % (_uid, _fu),
                         uid=_uid, fact_uid=_fu)
                elif _novel and _source_norm(_t) not in _novel:
                    _add("presentation_card_not_in_novel", "card", None,
                         "presentation.screen_cards[%s].text" % _uid,
                         "卡片 %s 的正文不是原文逐字片段——卡片正文必须取自原文"
                         % _uid, uid=_uid)
                _reading_sec += card_reading_seconds(_t) if _uid not in _read_cards else 0.0
                _read_cards.add(_uid)
            elif _kind in (KIND_DIALOGUE, KIND_NARRATION):
                _u = _unit_by_uid.get(_uid)
                if _u is None:
                    _add("presentation_carrier_dangling", "fact", _i, _path,
                         "%s 指向不存在的%s单位 %r"
                         % (_fu, "对白" if _kind == KIND_DIALOGUE else "旁白", _uid), uid=_fu)
                    continue
                _t = str(_u[1].get("text") or "").strip()
                if not _t:
                    # 同一条口径：实体是**声音单位**，受影响事实另记
                    _add("presentation_voice_text_empty", _u[0], None,
                         "%s_units[%s]" % ("dialogue" if _kind == KIND_DIALOGUE
                                           else "narration", _uid),
                         "%s 的声音单位 %s 没有正文" % (_fu, _uid),
                         uid=_uid, fact_uid=_fu)
                elif _novel and _source_norm(_t) not in _novel:
                    _add("presentation_voice_not_in_novel", _u[0], None,
                         "%s_units[%s]" % ("dialogue" if _kind == KIND_DIALOGUE
                                           else "narration", _uid),
                         "%s 的正文不在原文里（逐字核验未通过）" % _uid, uid=_uid)
            else:
                _add("presentation_carrier_kind_invalid", "fact", _i, _path,
                     "载体 kind 非法：%r（合法：画面/卡片/对白/旁白）" % _kind, uid=_fu)

    # 声音单位必须由**某条节拍**标着（否则它是孤儿：计划里有、镜头里没有）
    _voiced = {ref_uid(b.get("voice_ref")) for b in _view["beats"]
               if isinstance(b.get("voice_ref"), dict)}
    for _uid, (_k, _u) in _unit_by_uid.items():
        if _uid not in _voiced:
            _add("presentation_voice_orphan", _k, None, "%s_units[%s]" % (_k, _uid),
                 "%s 没有任何节拍标着它（`voice_ref` 指向它）——计划里有这条声音、"
                 "镜头里没人说它" % _uid, uid=_uid)

    # 阅读预算（展示预算，独立于 TTS 估时与模型能力常量）
    _budget = _reading_budget_sec()
    if _reading_sec > _budget:
        _add("presentation_reading_budget_exceeded", "plan", None, "presentation.screen_cards",
             "文字卡合计阅读需求 %.2fs 超过展示预算 %.2fs——保留完整主张，将可演出的信息转交画面/对白，或重选完整表达主张的更短原文片段；单纯拆页不会减少总阅读需求，"
             "**不自动加时/慢放/截字/减少核心事实**" % (_reading_sec, _budget))

    # 钩子：绑定与声明一致
    _hb = _pres.get("hook_bindings") or {}
    _spoken = [str(x) for x in (_hb.get("spoken_uids") or [])]
    _cra = _hb.get("carriers")
    if _cra is None and _hb.get("carrier") is not None:
        _cra = [_hb.get("carrier")]
    if _spoken or _cra:
        _d_texts = [str(u.get("text") or "") for u in _view["dialogue_units"]]
        for _u in _spoken:
            if _u not in [str(x["uid"]) for x in _view["dialogue_units"]]:
                _add("presentation_hook_spoken_dangling", "hook", None,
                     "presentation.hook_bindings.spoken_uids",
                     "钩子要求说出口的对白单位不存在：%r" % _u)
        if plan.get("hook_spoken") and not _d_texts:
            _add("presentation_hook_spoken_lost", "hook", None, "hook_spoken",
                 "`hook_spoken` 有内容但计划里没有对白——钩子要求说出口的台词不能失去")

    # 内容核验：拆成**确定性上下文准入**与**记录准入**两步（工作包②职责拆分）。
    #
    # - 上下文准入（`verification_context_of`）**两模式都跑**，与有没有记录无关：
    #   它判的是"这份候选声明的核验上下文本身是否合法"，属确定性判据。
    # - 记录准入由 `require_verification` 决定：
    #   · True（完整验收，**默认**）：无记录 ⇒ 拒收；**带旧摘要记录** ⇒ 仍走记录检查
    #     （摘要/身份不符照样拒）；
    #   · False（应用阶段）：只跳过"必须已有记录"这一步，不跳过上下文与确定性契约。
    from shared.content_verification import verification_context_of, verification_problems
    _ctx_bad, _tbl = verification_context_of(plan, _view)
    problems.extend(_ctx_bad)
    if not require_verification:
        # 应用阶段：上下文准入已判完，**记录准入整段跳过**——无记录与**旧摘要记录**
        # 一视同仁（旧记录也不能让应用阶段执行最终核验检查）。统一候选出口会在候选
        # 定稿后绑定记录，届时按完整验收再判。
        return problems
    if verification is None:
        _add("verify_record_missing", "verification", None, "plan_verification",
             "新模式计划的**内容核验记录**缺失——确定性判据只核形状/引用/出处/时长，"
             "完整支持关系必须由独立核验给出（缺记录不得判通过）")
    elif not _ctx_bad:
        # 上下文坏 ⇒ **只报上下文问题**（fail-closed）。不要顺手按根义务再判一遍：
        # 那会给一份分量记录报出一串"自造条目"，把真正的原因（上下文不合法）盖住。
        # ⚠️ **最终验收与缓存/控制器用同一份上下文**（复核 r6 §4.2 第四处入口）。
        _novel_dig = None
        if str(novel_text or ""):
            from step01_script_gen.content_verify import novel_identity
            _novel_dig = novel_identity(novel_text)["digest"]
        problems.extend(verification_problems(verification, _view, plan=plan,
                                              decomposition=_tbl,
                                              novel_text=novel_text,
                                              novel_digest=_novel_dig))
    return problems


def _reading_budget_sec() -> float:
    """展示预算（秒）：从**显示 profile** 取生效值，不假装已验."""
    from shared.screen_cards import profile as _profile
    return float(_profile.active_profile().reading_budget_sec)


def planning_resource_context(view=None) -> dict:
    """给规划者的预算事实：参数与逐条估时直接使用验收侧的事实源。

    这是一份请求信息，不是候选自报预算。原文/主张取舍仍由生产者负责；
    不自动截字、删除条目或改变验收上限。初版无 view 时只提供有效参数。
    """
    from shared.screen_cards import profile
    from shared.media_contract import MERGE_FPS
    from shared.presentation_contract import view_admission_problems, PresentationContractError
    if view is not None:
        problems = view_admission_problems(view)
        if problems:
            raise PresentationContractError(problems, where="规划资源请求")
    effective = profile.active_profile()
    minimum, maximum = budget_band()
    cards = [{"uid": card.get("card_id") or card.get("uid"),
              "characters": profile.countable_chars(card["text"]),
              "seconds": card_reading_seconds(card["text"])}
             for card in (view or {}).get("screen_cards", [])]
    voices = []
    for kind, field in (("dialogue", "dialogue_units"), ("narration", "narration_units")):
        for unit in (view or {}).get(field, []):
            voices.append({"uid": unit["uid"], "kind": kind,
                           "seconds": dialogue_seconds([unit["text"]])})
    return {"duration_band_sec": {"min": minimum, "max": maximum},
            "single_shot_cap_sec": active_shot_cap(),
            "card_reading": {"profile_version": effective.version,
                             "budget_sec": effective.reading_budget_sec,
                             "reading_chars_per_sec": effective.reading_chars_per_sec,
                             "settle_sec": effective.settle_sec,
                             "fps": MERGE_FPS,
                             "count_rule": "非空白 Unicode 字符，标点计入",
                             "formula": "ceil((characters/reading_chars_per_sec+settle_sec)*fps)/fps"},
            "card_costs": cards, "voice_costs": voices}


def card_reading_seconds(text) -> float:
    """一张卡的**阅读帧数**换算出的秒数（主专题 §6.1 的固定公式）.

    `ceil((非空白 Unicode 码点数 / reading_chars_per_sec + settle_sec) * MERGE_FPS)`；
    **标点计数、换行不计数**。这是展示假设，必须随清单记录。
    """
    from shared.screen_cards import profile as _profile
    _p = _profile.active_profile()
    _n = _profile.countable_chars(text)
    _frames = _profile.reading_frames(_n, _p)
    from shared.media_contract import MERGE_FPS
    return _frames / float(MERGE_FPS)
