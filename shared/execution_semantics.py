# -*- coding: utf-8 -*-
"""shared.execution_semantics - 三个样本族要求的**自动语义检查器**（S02 R2）.

本模块是 ``shared.execution_contract`` 的语义伴生层：执行合同负责字段/权限/摘要，
本模块负责把「合法时间省略 / 画外声音 / 声音时长」这三类主张变成可自动检出的
结构化记录。它只消费**显式结构化字段**与通用语言标记，不绑定任何小说、角色或
章节：

- ``causal_gap``：``chapter_outline.causal_chain`` 有 ``结果`` 角色却缺 ``行动``
  角色——结果没有执行路径；
- ``time_ellipsis_lost``：出现上述因果缺口，且**承载该因果关系的镜头**里没有
  任何时间推进标记/过程载体，结果状态却已断言完成——这是「删掉过程与时间，只留
  结果」，不是合法省略。检查器先按 `causal_chain` 条目里的**显式单位身份**
  （`B2`/`F1`/`事件3`，或与计划单位正文完全相等）解析关系；再按
  `shot.plan_refs` 定位承载镜头，过程与时间证据都限定在该范围。解析失败或结果
  没有承载镜头 → 证据不足（advisory + `needs_semantic_review`），不按全章范围判
  （2026-11-13 S03 目标关系）；
- ``offscreen_speaker_visible``：声音单元标为画外，说话人却出现在可见布局
  （``frame_layouts`` / ``visual_plan.visible_characters``），且没有合法的口型分离
  豁免；
- ``listener_reaction_missing``：存在画外声源时，画内听者没有任何可见反应证据
  （ff/lf 布局变化或动作/描述中的可见反应）；
- ``audio_window_overflow``：独立 TTS/旁白的实测时长超过单元窗口或镜头可用窗口；
- ``native_dialogue_unverified``：原生对白没有 ``verified=true`` 与有效实测时长。

设计约束：

- 返回值是 ``list[dict]``，空列表 = 通过；记录至少含
  ``code`` / ``severity`` / ``path`` / ``message`` / ``family``；
- 只做确定性检查，不读盘、不调模型、不写文件、不改入参；
- 不声称已经证明「语义等价」；它只证明上述结构化主张成立/不成立。真实画面是否
  符合计划仍需视觉 QC 或人工评阅（见 S03 的 ``unverified`` 记录）。
"""
from __future__ import annotations

import copy
import re
from typing import Any

#: 检查器版本（写入 ``binding.checks`` / 候选 manifest）。
CHECKER_VERSION = "execution_semantics.v1"

#: 本模块能产出的全部 code（消费方按此白名单接受 ``expected.codes``）。
KNOWN_CODES = frozenset({
    "causal_gap",
    "time_ellipsis_lost",
    "offscreen_speaker_visible",
    "listener_reaction_missing",
    "audio_window_overflow",
    "audio_window_overflow_planned",
    "native_dialogue_unverified",
})

#: 时间推进标记（通用语言层，不含任何作品/角色名）。
DEFAULT_TEMPORAL_MARKERS = (
    r"三日后", r"三天后", r"两日后", r"两日后", r"次日", r"翌日", r"隔日", r"隔天",
    r"第二天", r"数日后", r"几天后", r"多年后", r"半晌", r"片刻后", r"稍后",
    r"当夜", r"夜里", r"天亮后", r"黄昏", r"清晨", r"after \w+ days?", r"next day",
    r"later", r"meanwhile", r"years later",
)

#: 可见反应标记（听者是否在画面里作出反应）。
DEFAULT_REACTION_CUES = (
    "转头", "回头", "看向", "望向", "凝视", "目光", "抬眼", "抬眸", "侧目",
    "皱眉", "蹙眉", "低头", "抬头", "屏息", "后退", "握紧", "怔", "顿住",
    "吸气", "点头", "摇头", "抿唇", "眨眼", "转身",
)

#: 口型分离/画外低语的合法豁免标记。只有**同一镜**里明确声明「不靠口型」时才
#: 允许画外声源同时可见（例如人物在画内但只以画外低语/内心独白发声）。
DEFAULT_LIPSYNC_DISABLED_CUES = (
    "无口型", "没有口型", "口型不符", "嘴唇闭合", "没有出声", "不出声",
    "内心独白", "画外低语", "voice_over", "voiceover", "no lip", "lip sync disabled",
)

#: 结果状态已完成的通用标记（用于区分「过程被省略」与「过程从未出现」）。
DEFAULT_COMPLETION_MARKERS = (
    "已", "修好", "补好", "做好", "建好", "完成", "结束", "变成", "成为",
    "repaired", "completed", "finished", "done",
)

#: 因果链角色标签。标签来自计划结构，不来自故事内容。
_CAUSE_ROLES = ("起因", "原因", "cause")
_ACTION_ROLES = ("行动", "经过", "过程", "action", "process")
_RESULT_ROLES = ("结果", "后果", "result", "outcome")

_ROLE_SPLIT_RE = re.compile(r"^\s*([^：:]{1,12})\s*[：:]\s*(.*)$", re.S)

#: 动作分段标记（`前2秒：` / `2-4秒：`）。**结构证据**，不依赖任何作品词表。
_MOTION_SEGMENT_RE = re.compile(
    r"(?:前|后)\s*\d+(?:\.\d+)?\s*秒|\d+(?:\.\d+)?\s*[-－~～至]\s*\d+(?:\.\d+)?\s*秒")


def _bridge_text(script: dict) -> str:
    """镜头衔接字段（`context_bridge` 等）——时间推进标记的**统一读取范围**之一.

    2026-11-12 复核：`_has_temporal_marker` 此前只读镜头正文与 `narration_lines` /
    `main_events`，**不读 `context_bridge`**。于是「次日到达」这类合法省略写在衔接
    字段里时被判成「全稿没有时间推进标记」，进而触发 `time_ellipsis_lost`。
    """
    parts = [_text(shot.get("context_bridge")) for shot in _iter_shots(script)]
    outline = _outline(script)
    for key in ("context_bridge", "time_notes", "temporal_notes"):
        value = outline.get(key)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(p for p in parts if p)


def _shot_has_process(shot: dict) -> bool:
    """该镜是否自带可演的过程（动作分段 ≥2 或**关键帧序列**）——结构判据，不看词表.

    2026-11-13（S03 复核）：旧口径把「有关键帧」直接当成过程证据，于是给一镜
    无关的鸟加一张定格关键帧，就能豁免因果缺失。单个关键帧只是静态定格；只有
    ≥2 个时刻/阶段不同的关键帧才构成时间序列（过程）。
    """
    segments = _MOTION_SEGMENT_RE.findall(_text(shot.get("motion_desc")))
    return len(segments) >= 2 or _keyframe_sequence(shot)


def _keyframe_sequence(shot: dict) -> bool:
    """关键帧是否构成**时间序列**（≥2 帧且时刻/阶段不同）."""
    frames = shot.get("keyframes")
    if not isinstance(frames, list):
        return False
    marks: set[str] = set()
    for frame in frames:
        if not isinstance(frame, dict) or not _text(frame.get("desc")):
            continue
        moment = frame.get("t_sec")
        if moment is None:
            moment = _text(frame.get("stage")) or _text(frame.get("moment"))
        marks.add(str(moment))
    return len(marks) >= 2


#: `plan_refs` 里表示「本镜承载了哪些计划条目」的键，以及各自在
#: `chapter_outline` 里的目标列表（编号按 1 起算）。目标列表是**本稿自带的计划
#: 结构**，不读盘、不调模型；缺失 = 引用不可核验。
_PLAN_REF_TARGETS = (
    ("beats", "shot_beats", "节拍"),
    ("lines", "dialogue_lines", "台词"),
    ("facts", "fact_uids", "事实"),
    ("events", "main_events", "事件"),
)


def _coerce_ref_number(value: Any) -> int | None:
    """`plan_refs` 的编号 → int；只认整数与 ASCII 数字串.

    带前缀的 `"B1"` / 浮点 / bool 都不是有效引用（生成契约要求只填数字；带前缀
    由 `plan_feasibility.check_event_contract` 报 `plan_refs_not_a_number`）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text.isascii() and text.isdigit():
            return int(text)
    return None


def _ref_target_count(outline: dict, target_key: str) -> int | None:
    """计划目标列表的条目数；列表缺失/不可读返回 None（引用不可核验）."""
    value = outline.get(target_key)
    if isinstance(value, list):
        return len(value)
    return None


def _resolve_plan_refs(shot: dict, outline: dict) -> tuple[dict, list[dict]]:
    """把 `plan_refs` 解析到**本稿计划的实际条目**上.

    Returns:
        `(resolved, invalid)`：
        - `resolved`：`{kind: [number, ...]}`，只含目标确实存在的引用；
        - `invalid`：`[{"kind", "value", "reason"}]`，目标缺失/越界/类型非法。

    「非空引用」不等于绑定：`beats=[999]` 在没有第 999 条节拍时不能证明本镜
    承载了任何计划条目。只有解析成功的引用才进入承载关系（2026-11-13 S03）。
    """
    refs = shot.get("plan_refs")
    if not isinstance(refs, dict):
        return {}, []
    resolved: dict[str, list[int]] = {}
    invalid: list[dict] = []
    for kind, target_key, _label in _PLAN_REF_TARGETS:
        if kind not in refs:
            continue
        raw = refs.get(kind)
        if not isinstance(raw, list):
            invalid.append({"kind": kind, "value": raw, "reason": "not_a_list"})
            continue
        count = _ref_target_count(outline, target_key)
        for value in raw:
            number = _coerce_ref_number(value)
            if number is None:
                invalid.append({"kind": kind, "value": value,
                                "reason": "not_a_number"})
                continue
            if count is None:
                invalid.append({"kind": kind, "value": value,
                                "reason": "target_missing"})
                continue
            if number < 1 or number > count:
                invalid.append({"kind": kind, "value": value,
                                "reason": "out_of_range"})
                continue
            resolved.setdefault(kind, []).append(number)
    return resolved, invalid


def _process_carriers(script: dict) -> tuple[list[str], list[str], list[str], list[dict]]:
    """按**解析成功的显式 `plan_refs`** 划分过程载体.

    Returns:
        `(bound_with_process, bound_without_process, unbound, invalid_refs)`，
        镜头标签形如 `shot:3`；`invalid_refs` 逐条给出
        `{"shot", "kind", "value", "reason"}`。

    - `bound_*`：至少一条引用**解析到真实计划条目**——计划明确说「本镜承载
      这些节拍/台词」；
    - `unbound`：没有可解析的引用（没声明，或声明的目标不存在）。**它既不能当
      豁免证据，也不能据此认定因果缺失**，裁决交给语义评阅
      （`needs_semantic_review`）；
    - `invalid_refs`：声明了但目标缺失/越界/类型非法的引用，单独记录。无效引用
      **不改变裁决**——2026-11-13 S03 复核：`beats=[999]` 曾被当成绑定，给一镜
      无关的鸟加关键帧就抹掉了 `time_ellipsis_lost`。
    """
    outline = _outline(script)
    bound_with: list[str] = []
    bound_without: list[str] = []
    unbound: list[str] = []
    invalid_refs: list[dict] = []
    for shot in _iter_shots(script):
        label = "shot:%s" % (shot.get("index"),)
        resolved, invalid = _resolve_plan_refs(shot, outline)
        for item in invalid:
            invalid_refs.append({"shot": label, **item})
        if not resolved:
            unbound.append(label)
        elif _shot_has_process(shot):
            bound_with.append(label)
        else:
            bound_without.append(label)
    return bound_with, bound_without, unbound, invalid_refs


def _issue(code: str, path: str, message: str, *, family: str, **fields) -> dict:
    if code not in KNOWN_CODES:  # 开发期自检：不允许悄悄新增未登记的 code
        raise ValueError(f"未登记的语义检查 code: {code}")
    record = {"code": code, "severity": "reject", "path": path,
              "message": message, "family": family}
    record.update(fields)
    return record


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _iter_shots(script: dict) -> list[dict]:
    shots = script.get("shots")
    if not isinstance(shots, list):
        return []
    return [s for s in shots if isinstance(s, dict)]


def _outline(script: dict) -> dict:
    outline = script.get("chapter_outline")
    return outline if isinstance(outline, dict) else {}


def _shot_fields(shot: dict) -> str:
    parts = []
    for key in ("prompt", "ff_desc", "lf_desc", "motion_desc",
                "narration_segment", "audio_cue", "main_subject"):
        value = shot.get(key)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _all_shot_text(script: dict) -> str:
    return "\n".join(_shot_fields(s) for s in _iter_shots(script))


def _causal_roles(outline: dict) -> list[tuple[str, str, int]]:
    """把 ``causal_chain`` 拆成 ``[(role, text, index)]``；无标签条目 role=''."""
    chain = outline.get("causal_chain")
    if not isinstance(chain, list):
        return []
    out: list[tuple[str, str, int]] = []
    for i, entry in enumerate(chain):
        text = _text(entry) if isinstance(entry, str) else ""
        role = ""
        match = _ROLE_SPLIT_RE.match(text)
        if match:
            role = match.group(1).strip().lower()
            text = match.group(2).strip()
        out.append((role, text, i))
    return out


def _role_matches(role: str, candidates: tuple) -> bool:
    return bool(role) and any(role == c or role.startswith(c) for c in candidates)


#: 因果链条目里的**显式单位引用**：`B2` / `节拍2` / `F1` / `事实1` / `事件3` / `台词1`。
#: 只认这些显式编号；**不按文本相似度猜关系**（2026-11-12 第三/四轮教训：
#: 「城门上方一只鸟飞过」会与结果共享地点而被误判成相关过程）。
_CAUSAL_REF_PATTERNS = (
    ("beats", re.compile(r"(?:节拍|(?<![A-Za-z])B)\s*[（(]?\s*(\d+)")),
    ("facts", re.compile(r"(?:事实|(?<![A-Za-z])F)\s*[（(]?\s*(\d+)")),
    ("events", re.compile(r"事件\s*[（(]?\s*(\d+)")),
    ("lines", re.compile(r"(?:台词|(?<![A-Za-z])D)\s*[（(]?\s*(\d+)")),
)


def _identity_text(value: Any) -> str:
    """身份比较用的归一化：去空白/标点，只留内容字符.

    只用于**完全相等**的身份匹配（因果链条目正文 == 某个计划单位正文），
    不做相似度、不做子串包含——那正是被复核否决的「猜关系」。
    """
    return re.sub(r"[\s，。、；：:（）()\[\]【】“”\"'｜|→\-—]+", "", str(value or ""))


def _beat_body(beat: Any) -> str:
    """节拍正文（去掉 `节拍：` 前缀与 `｜` 段）."""
    text = str(beat or "").strip()
    if text.startswith("节拍："):
        text = text[len("节拍："):]
    return text.split("｜")[0].strip()


def _fact_claim(fact: Any) -> str:
    """事实正文（去掉 `｜原文证据/承载` 段）."""
    return str(fact or "").split("｜")[0].strip()


def _beat_fact_refs(beat: Any) -> set[int]:
    """节拍 `事实：F1、F2` 段里的显式事实编号."""
    for part in str(beat or "").split("｜"):
        part = part.strip()
        if part.startswith("事实："):
            return {int(x) for x in re.findall(r"\d+", part)}
    return set()


def _relation_units(outline: dict, roles: list[tuple[str, str, int]]
                    ) -> tuple[dict[str, set[int]], list[str],
                               dict[str, set[int]], dict]:
    """把因果链条目解析到**计划单位身份**（节拍/事实/事件/台词）.

    Returns:
        `(units, unresolved, result_units, diagnostics)`：
        - `units = {kind: {编号}}`：整条关系（起因/行动/结果）的单位（1 起算）；
        - `unresolved`：无法建立**唯一**身份的条目正文（显式失效/歧义/冲突/
          无身份四类都算）；
        - `result_units`：**结果条目**解析到的单位——结果没有承载镜头时，关系
          在成片里没有被定位，必须按证据不足处理；
        - `diagnostics`：`{"explicit_unresolved": [...], "ambiguous": [...],
          "conflicts": [...], "no_identity": [...]}`，供 `validate_causal_identity`
          区分状态（歧义/冲突/旧稿）。

    身份来源只有两种，都是显式的：
      ① 条目里的编号引用（`B2` / `节拍2` / `F1` / `事实1` / `事件3` / `台词1`）；
      ② 条目正文与某个计划单位正文**完全相等**（归一化标点后）。

    2026-11-14 复核（跨阶段保留歧义）：
      - 条目里已有显式失效标记 `未落节拍` ⇒ 直接按 unresolved，**不再**按正文
        重新解析（否则重映射识别的歧义会在下一阶段被"重新解决"）；
      - 正文匹配到**同类多个候选**（如同一正文的两条节拍）⇒ 歧义，不得把候选
        全部塞进身份集合；
      - 内容身份与编号引用冲突 ⇒ 记 `conflicts`（状态 invalid），不是普通
        unresolved。
    """
    limits = {
        "beats": _ref_target_count(outline, "shot_beats") or 0,
        "facts": _ref_target_count(outline, "fact_uids") or 0,
        "events": _ref_target_count(outline, "main_events") or 0,
        "lines": _ref_target_count(outline, "dialogue_lines") or 0,
    }
    units: dict[str, set[int]] = {}
    result_units: dict[str, set[int]] = {}
    unresolved: list[str] = []
    diagnostics: dict = {"explicit_unresolved": [], "ambiguous": [],
                         "conflicts": [], "no_identity": []}
    for role, text, _i in roles:
        is_result = _role_matches(role, _RESULT_ROLES)
        # ⓪ 显式失效标记优先：重映射已判定「没有唯一对应节拍」，不得按正文
        #    重新解析成 resolved（2026-11-14 复核反例）。
        if CAUSAL_UNRESOLVED_MARK in str(text or ""):
            diagnostics["explicit_unresolved"].append(text)
            unresolved.append(text)
            continue
        # ① 正文完全相等 → 内容身份（最稳：节拍插入/重编号不影响）。
        #    比较前先去掉条目里的 `（Bk）` 引用，否则带引用的条目永远无法与
        #    节拍正文完全相等。
        exact: dict[str, set[int]] = {}
        body = _identity_text(_strip_causal_refs(text))
        if body:
            for kind, values, extractor in (
                    ("beats", outline.get("shot_beats") or [], _beat_body),
                    ("facts", outline.get("required_facts") or [], _fact_claim),
                    ("events", outline.get("main_events") or [], str)):
                for number, value in enumerate(values, 1):
                    if body == _identity_text(extractor(value)):
                        exact.setdefault(kind, set()).add(number)
        # ①b 同类多候选 ⇒ 歧义：不把候选全部纳入，也不选边站。
        ambiguous_kinds = {kind: sorted(numbers)
                           for kind, numbers in exact.items() if len(numbers) > 1}
        if ambiguous_kinds:
            diagnostics["ambiguous"].append({"text": text,
                                             "candidates": ambiguous_kinds})
            unresolved.append(text)
            continue
        # ② 条目里的显式编号引用。
        refs: dict[str, set[int]] = {}
        for kind, pattern in _CAUSAL_REF_PATTERNS:
            for match in pattern.finditer(text):
                number = int(match.group(1))
                if 1 <= number <= limits[kind]:
                    refs.setdefault(kind, set()).add(number)
        # 内容身份与编号引用冲突 ⇒ 身份互相矛盾（invalid，不是普通 unresolved）。
        # 只在**同一类单位**上比较：正文同时等于某节拍与某事件是正常的
        # （main_events 常与节拍正文同文），不是冲突。
        if exact and refs and any(
                kind in refs and exact[kind] != refs[kind] for kind in exact):
            diagnostics["conflicts"].append({
                "text": text,
                "exact": {k: sorted(v) for k, v in exact.items()},
                "refs": {k: sorted(v) for k, v in refs.items()},
            })
            unresolved.append(text)
            continue
        chosen = exact or refs
        if not chosen:
            diagnostics["no_identity"].append(text)
            unresolved.append(text)
            continue
        for kind, numbers in chosen.items():
            units.setdefault(kind, set()).update(numbers)
            if is_result:
                result_units.setdefault(kind, set()).update(numbers)
    return units, unresolved, result_units, diagnostics


def _shot_by_index(script: dict, index: int) -> dict:
    for shot in _iter_shots(script):
        try:
            if int(shot.get("index")) == index:
                return shot
        except (TypeError, ValueError):
            continue
    return {}


#: 因果链条目里「没有对应节拍」的显式标记（生成端在无节拍可指时写它）。
CAUSAL_UNRESOLVED_MARK = "未落节拍"


def _causal_ref_spans(text: str) -> list[tuple[int, int, str, int]]:
    """因果链条目里的 `(start, end, kind, number)` 引用片段（按位置排序）."""
    spans: list[tuple[int, int, str, int]] = []
    for kind, pattern in _CAUSAL_REF_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            spans.append((match.start(), match.end(), kind, int(match.group(1))))
    spans.sort()
    return spans


def _causal_limits(outline: dict) -> dict[str, int]:
    return {
        "beats": _ref_target_count(outline, "shot_beats") or 0,
        "facts": _ref_target_count(outline, "fact_uids") or 0,
        "events": _ref_target_count(outline, "main_events") or 0,
        "lines": _ref_target_count(outline, "dialogue_lines") or 0,
    }


def _strip_causal_refs(text: str) -> str:
    """去掉因果条目里的编号引用（含残留空括号与「未落节拍」标记）.

    内容身份比较必须用**去掉引用后的正文**：`结果：乙到达（B2）` 的正文是
    「乙到达」，否则带引用的条目永远无法与节拍正文完全相等。
    """
    raw = str(text or "")
    spans = _causal_ref_spans(raw)
    if not spans:
        body = raw
    else:
        buf: list[str] = []
        pos = 0
        for start, end, _kind, _number in spans:
            buf.append(raw[pos:start])
            pos = end
        buf.append(raw[pos:])
        body = "".join(buf)
    # 先去掉「未落节拍」标记，再清掉因此留下的空括号。
    body = body.replace(CAUSAL_UNRESOLVED_MARK, "")
    body = body.replace("（）", "").replace("()", "")
    return body


def validate_causal_identity(outline: dict) -> dict:
    """校验因果链的单位身份（计划生成后的第一道核验）.

    Returns:
        `{"status": "resolved" | "unresolved" | "invalid", "units", "unresolved",
          "problems", "ambiguous", "conflicts", "explicit_unresolved"}`：
        - `resolved`：每条因果条目都解析到**唯一**的计划单位；
        - `unresolved`：有条目没有显式身份（旧稿 / 生成端未标注）、正文匹配到
          多个候选（歧义），或带显式失效标记「未落节拍」——**不是**内容缺陷，
          是「该项未完成语义判定」，准入影响走影子差异；
        - `invalid`：有条目引用了不存在的单位编号（越界），或同一条目里
          内容身份与编号引用冲突（身份互相矛盾）。
    """
    roles = _causal_roles(outline)
    units, unresolved, _result_units, diagnostics = _relation_units(outline, roles)
    limits = _causal_limits(outline)
    problems: list[dict] = []
    for role, text, index in roles:
        for _start, _end, kind, number in _causal_ref_spans(text):
            if not 1 <= number <= limits[kind]:
                problems.append({
                    "code": "causal_ref_out_of_range",
                    "path": f"chapter_outline.causal_chain[{index}]",
                    "message": ("因果链条目引用了不存在的单位 %s%d（共 %d 条）"
                                % (kind, number, limits[kind])),
                })
    conflicts = diagnostics.get("conflicts") or []
    status = ("invalid" if (problems or conflicts)
              else ("unresolved" if unresolved else "resolved"))
    return {
        "status": status,
        "units": {kind: sorted(values) for kind, values in units.items()},
        "unresolved": unresolved,
        "problems": problems,
        "ambiguous": diagnostics.get("ambiguous") or [],
        "conflicts": conflicts,
        "explicit_unresolved": diagnostics.get("explicit_unresolved") or [],
    }


def remap_causal_refs(plan: dict, old_beats: list, new_beats: list) -> list[str]:
    """节拍表变更后重映射 `causal_chain` 里的 `（Bk）` 编号；返回作废说明.

    修订会插入/删除/重排/拆分节拍，而 `causal_chain` 里的编号是**按位置**写的。
    这里按**正文完全相等**把旧编号映射到新编号（插入/删除/重排都适用）：

    - 旧节拍仍存在（可能换了位置）→ 改成新编号；
    - 旧节拍被删除、改写或拆分（正文不再唯一存在）→ 该引用改成
      `未落节拍`，并记一条说明，交计划层重新标注；
    - 重复正文无法唯一映射 → 同样未落节拍（不猜）。

    不按文本相似度猜关系；只认完全相等的正文身份。
    """
    if not isinstance(plan, dict):
        return []
    old_bodies = [_identity_text(_beat_body(b)) for b in (old_beats or [])]
    new_bodies = [_identity_text(_beat_body(b)) for b in (new_beats or [])]
    counts: dict[str, int] = {}
    for body in new_bodies:
        if body:
            counts[body] = counts.get(body, 0) + 1
    new_index = {body: i for i, body in enumerate(new_bodies, 1)
                 if body and counts.get(body) == 1}
    chain = plan.get("causal_chain")
    if not isinstance(chain, list):
        return []
    notes: list[str] = []
    out: list = []
    for entry_index, entry in enumerate(chain, 1):
        text = str(entry or "")
        spans = [s for s in _causal_ref_spans(text) if s[2] == "beats"]
        if not spans:
            out.append(entry)
            continue
        buf: list[str] = []
        pos = 0
        for start, end, _kind, number in spans:
            buf.append(text[pos:start])
            body = (old_bodies[number - 1]
                    if 1 <= number <= len(old_bodies) else "")
            new_number = new_index.get(body) if body else None
            if new_number:
                buf.append("B%d" % new_number)
            else:
                buf.append(CAUSAL_UNRESOLVED_MARK)
                notes.append(
                    "causal_chain[%d] 的 B%d 在节拍变更后找不到唯一对应节拍 ⇒ 未落节拍"
                    % (entry_index, number))
            pos = end
        buf.append(text[pos:])
        out.append("".join(buf))
    plan["causal_chain"] = out
    return notes


def _unit_carrier_shots(script: dict, units: dict[str, set[int]]) -> set[int]:
    """计划单位身份 → 承载它的镜头序号集合（只认显式 `plan_refs` / 节拍事实段）."""
    outline = _outline(script)
    fact_beats: set[int] = set()
    for number, beat in enumerate(outline.get("shot_beats") or [], 1):
        if _beat_fact_refs(beat) & units.get("facts", set()):
            fact_beats.add(number)
    carriers: set[int] = set()
    for shot in _iter_shots(script):
        refs, _invalid = _resolve_plan_refs(shot, outline)
        hit = (
            bool(set(refs.get("beats", [])) & (units.get("beats", set()) | fact_beats))
            or bool(set(refs.get("facts", [])) & units.get("facts", set()))
            or bool(set(refs.get("events", [])) & units.get("events", set()))
            or bool(set(refs.get("lines", [])) & units.get("lines", set()))
        )
        if hit:
            try:
                carriers.add(int(shot.get("index")))
            except (TypeError, ValueError):
                continue
    return carriers


def _relation_time_texts(outline: dict, units: dict[str, set[int]]) -> list[str]:
    """与被质疑关系**显式绑定**的章节级时间文本（不读无关条目）.

    - 关系解析到的事件正文（仅这些事件，不是全部 `main_events`）；
    - `fact_refs` 指向关系事实的旁白正文（实际播出文本）。
    节拍/事实正文不算：它们是计划声明，不是成片里的时间证据。
    """
    texts: list[str] = []
    events = outline.get("main_events") or []
    for number in sorted(units.get("events", set())):
        if 1 <= number <= len(events):
            texts.append(str(events[number - 1]))
    for line in outline.get("narration_lines") or []:
        if not isinstance(line, dict):
            continue
        refs = {int(x) for x in (line.get("fact_refs") or [])
                if isinstance(x, int) or (isinstance(x, str) and x.isdigit())}
        if refs & units.get("facts", set()):
            texts.append(str(line.get("text") or ""))
    return [t for t in texts if t]


def _has_temporal_marker(script: dict, *, shot_indices: set | None = None,
                         extra_texts: list[str] | None = None) -> bool:
    """时间推进标记是否出现在**指定范围**内.

    2026-11-12 复核（第三轮）：时间标记要按镜头范围核对。
    2026-11-13 复核（S03 目标关系）：章节级文本也**只计入显式绑定到被质疑关系**
    的那些（`extra_texts`）；不再把整章 `main_events` / `narration_lines` 拼进来
    ——「仅在 main_events 增加『次日，远方的渔民出海』」不应影响另一个因果关系的裁决。
    """
    shot_texts = []
    for shot in _iter_shots(script):
        if shot_indices is not None:
            try:
                if int(shot.get("index", -1)) not in shot_indices:
                    continue
            except (TypeError, ValueError):
                continue
        shot_texts.append(_shot_fields(shot))
        shot_texts.append(_text(shot.get("context_bridge")))
    text = "\n".join(shot_texts + list(extra_texts or []))
    return any(re.search(pattern, text, re.I) for pattern in DEFAULT_TEMPORAL_MARKERS)


def _has_completion_marker(script: dict, *, shot_indices: set | None = None,
                           extra_texts: list[str] | None = None) -> bool:
    """结果状态是否已断言完成（默认全稿；给定范围时只看该范围）."""
    if shot_indices is None and extra_texts is None:
        text = "\n".join([str(_outline(script).get("causal_chain") or ""),
                          str(_outline(script).get("main_events") or ""),
                          _all_shot_text(script)])
        return any(marker in text for marker in DEFAULT_COMPLETION_MARKERS)
    parts: list[str] = []
    for shot in _iter_shots(script):
        if shot_indices is not None:
            try:
                if int(shot.get("index", -1)) not in shot_indices:
                    continue
            except (TypeError, ValueError):
                continue
        parts.append(_shot_fields(shot))
    parts.extend(extra_texts or [])
    text = "\n".join(parts)
    return any(marker in text for marker in DEFAULT_COMPLETION_MARKERS)


# ---------------------------------------------------------------------------
# 候选合并
# ---------------------------------------------------------------------------

def apply_execution_candidate(script: dict, candidate: dict | None) -> dict:
    """把执行候选合并进稿件副本，供检查器消费（不修改入参）.

    - ``shots``：按 ``index`` 合并字段；
    - ``voice_units``：按 ``unit_id`` 合并字段；
    - ``visual_plan`` / ``audio_windows``：替换章节级字段；
    - 其它顶层字段（如 ``speaker``）按 key 覆盖到每个同号镜头。
    """
    merged = copy.deepcopy(script)
    if not isinstance(candidate, dict):
        return merged
    if isinstance(candidate.get("shots"), list):
        by_index = {int(s.get("index", -1)): s for s in _iter_shots(merged)
                    if isinstance(s.get("index"), int)}
        for patch in candidate["shots"]:
            if not isinstance(patch, dict):
                continue
            idx = patch.get("index")
            if not isinstance(idx, int) or idx not in by_index:
                continue
            target = by_index[idx]
            for key, value in patch.items():
                if key == "index":
                    continue
                if key == "frame_layouts" and isinstance(value, dict):
                    layouts = target.get("frame_layouts")
                    layouts = layouts if isinstance(layouts, dict) else {}
                    merged_layouts = dict(layouts)
                    merged_layouts.update(copy.deepcopy(value))
                    target["frame_layouts"] = merged_layouts
                else:
                    target[key] = copy.deepcopy(value)
    outline = merged.get("chapter_outline")
    if isinstance(outline, dict):
        if isinstance(candidate.get("voice_units"), list):
            units = outline.get("voice_units")
            units = units if isinstance(units, list) else []
            by_uid = {str(u.get("unit_id") or ""): u for u in units if isinstance(u, dict)}
            for patch in candidate["voice_units"]:
                if not isinstance(patch, dict):
                    continue
                uid = str(patch.get("unit_id") or "")
                target = by_uid.get(uid)
                if target is None:
                    target = {"unit_id": uid}
                    units.append(target)
                    by_uid[uid] = target
                target.update(copy.deepcopy(patch))
            outline["voice_units"] = units
    for key in ("visual_plan", "audio_windows"):
        if key in candidate:
            merged[key] = copy.deepcopy(candidate[key])
    for key, value in candidate.items():
        if key in ("shots", "voice_units", "visual_plan", "audio_windows"):
            continue
        if key in ("speaker", "camera_movement", "prompt"):
            for shot in _iter_shots(merged):
                shot[key] = copy.deepcopy(value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


# ---------------------------------------------------------------------------
# 单条检查
# ---------------------------------------------------------------------------

def _check_causal_and_time(script: dict) -> list[dict]:
    outline = _outline(script)
    roles = _causal_roles(outline)
    if not roles:
        return []
    has_result = any(_role_matches(role, _RESULT_ROLES) for role, _t, _i in roles)
    has_action = any(_role_matches(role, _ACTION_ROLES) for role, _t, _i in roles)
    has_cause = any(_role_matches(role, _CAUSE_ROLES) for role, _t, _i in roles)
    if not (has_result and has_cause and not has_action):
        return []
    # 标签缺失先作为**结构证据**记录（advisory），不直接拒收。
    #
    # 承载关系**只认显式绑定**（`shot.plan_refs`）：文本相似度不足以证明某镜承载了
    # 被质疑的因果——「城门上方一只鸟飞过」会与结果共享地点而被误判成相关过程。
    # 2026-11-13（S03）：绑定前先把引用**解析到真实计划条目**；解析失败的引用不参与
    # 承载关系，逐条记入 `invalid_refs`，不得用它豁免。
    bound_with, bound_without, unbound, invalid_refs = _process_carriers(script)
    binding_status = "bound" if (bound_with or bound_without) else "unbound"

    # 2026-11-13（S03 目标关系）：先取得被质疑关系对应的计划单位身份（节拍/事实/
    # 事件/台词），再把过程与时间证据**限定到这些单位的承载镜头**。拿不到唯一身份
    # （或结果在成片里没有承载镜头）时明确输出「证据不足」，不按全章范围判，也不
    # 静默豁免——准入处理沿用既定裁决边界（只出 advisory + needs_semantic_review）。
    identity = validate_causal_identity(outline)
    units, unresolved, result_units, _diagnostics = _relation_units(outline, roles)
    carrier_indices = _unit_carrier_shots(script, units)
    result_carriers = _unit_carrier_shots(script, result_units)
    if not units or unresolved:
        relation_status = "unresolved"
    elif not result_carriers:
        relation_status = "no_carrier"
    else:
        relation_status = "bound"
    relation_scope = {kind: sorted(values) for kind, values in units.items()}
    relation_process = [
        "shot:%s" % index for index in sorted(carrier_indices)
        if _shot_has_process(_shot_by_index(script, index))
    ]
    relation_time_texts = _relation_time_texts(outline, units)
    # 「结果状态已断言完成」只看**结果条目自身**（+ 关系绑定文本），不看全章——
    # 否则无关镜头里的「已完成」也会替这条关系作证。
    result_texts = [text for role, text, _i in roles
                    if _role_matches(role, _RESULT_ROLES)]
    findings = [_issue(
        "causal_gap", "chapter_outline.causal_chain",
        "因果链只有起因与结果、没有行动/过程标签：结果缺少显式执行路径（结构记录）",
        family="temporal_ellipsis",
        severity="advisory",
        expected_roles=["起因", "行动", "结果"],
        actual_roles=[role for role, _t, _i in roles],
        binding_status=binding_status,
        relation_status=relation_status,
        relation_note=("该项未完成语义判定（因果链条目没有显式单位身份）"
                       if relation_status == "unresolved" else ""),
        relation_units=relation_scope,
        unresolved_relations=unresolved,
        identity_status=identity.get("status"),
        identity_problems=identity.get("problems") or [],
        identity_ambiguous=identity.get("ambiguous") or [],
        identity_conflicts=identity.get("conflicts") or [],
        relation_carriers=["shot:%s" % i for i in sorted(carrier_indices)],
        result_carriers=["shot:%s" % i for i in sorted(result_carriers)],
        carrier_evidence=relation_process,
        bound_without_process=bound_without,
        unbound_shots=unbound,
        invalid_refs=invalid_refs,
        needs_semantic_review=(
            binding_status == "unbound" or bool(invalid_refs)
            or relation_status != "bound"),
    )]
    if relation_status != "bound":
        # 关系无法唯一解析 / 结果没有承载镜头：既不豁免（不能证明过程存在），
        # 也不认定缺失（不能拿无法解析当证据）。证据不足交语义评阅。
        return findings
    # 只有**关系承载镜头**里的过程载体才能豁免；时间标记也只在关系范围内核对
    # （章节级只计入显式绑定到该关系的事件/旁白，见 `_relation_time_texts`）。
    if (not relation_process
            and not _has_temporal_marker(script, shot_indices=carrier_indices,
                                         extra_texts=relation_time_texts)
            and _has_completion_marker(script, shot_indices=carrier_indices,
                                       extra_texts=result_texts + relation_time_texts)):
        findings.append(_issue(
            "time_ellipsis_lost", "chapter_outline.narration_lines",
            "结果状态已断言完成，但**承载该因果关系的镜头**里既没有过程载体、"
            "也没有时间推进标记；这是删除时间/过程后只留结果，不是合法的常规省略",
            family="temporal_ellipsis",
            missing=["temporal_marker", "process_carrier"],
            relation_units=relation_scope,
            bound_shots=sorted(carrier_indices),
            process_carriers=relation_process,
            unbound_shots=unbound,
            invalid_refs=invalid_refs,
        ))
    return findings


def _unit_is_offscreen(unit: dict, shot: dict | None) -> bool:
    kind = _text(unit.get("kind")).lower()
    if "offscreen" in kind or kind in ("voice_over", "narration"):
        if kind == "narration":
            return False
        return True
    if unit.get("offscreen") is True:
        return True
    if unit.get("on_screen") is False:
        return True
    # 声音单元没有显式标记时，看所属镜头的画外标记
    text = _shot_fields(shot or {})
    return "（画外）" in text or "(画外)" in text or "画外人声" in text


def _visible_names(script: dict, shot: dict | None) -> set[str]:
    names: set[str] = set()
    for frame in ("ff", "lf", "kfs"):
        entries = ((shot or {}).get("frame_layouts") or {}).get(frame)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("on_screen") is False:
                continue
            name = _text(entry.get("name") or entry.get("character")
                         or entry.get("identifier"))
            if name:
                names.add(name)
    plan = script.get("visual_plan")
    if isinstance(plan, dict):
        for key in ("visible_characters", "visible_props"):
            values = plan.get(key)
            if isinstance(values, list):
                for value in values:
                    if key == "visible_props":
                        continue
                    name = _text(value)
                    if name:
                        names.add(name)
    return names


def _lipsync_disabled(shot: dict) -> bool:
    text = _shot_fields(shot).lower()
    return any(cue.lower() in text for cue in DEFAULT_LIPSYNC_DISABLED_CUES)


def _check_offscreen(script: dict) -> list[dict]:
    outline = _outline(script)
    units = outline.get("voice_units")
    if not isinstance(units, list):
        return []
    shots = {s.get("index"): s for s in _iter_shots(script)}
    findings: list[dict] = []
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        speaker = _text(unit.get("speaker"))
        if not speaker:
            continue
        shot = shots.get(unit.get("shot_index"))
        if not _unit_is_offscreen(unit, shot):
            continue
        visible = _visible_names(script, shot)
        if speaker not in visible:
            continue
        # 显式 on_screen=true 是「声音标画外、人却出镜」的直接证据，不给豁免。
        if unit.get("on_screen") is True:
            findings.append(_issue(
                "offscreen_speaker_visible",
                f"chapter_outline.voice_units[{i}].speaker",
                f"画外声音的说话人 {speaker!r} 被显式标为出镜（on_screen=true）",
                family="offscreen_voice", speaker=speaker,
                shot_index=unit.get("shot_index"), visible=sorted(visible),
            ))
            continue
        if _lipsync_disabled(shot or {}):
            continue
        findings.append(_issue(
            "offscreen_speaker_visible",
            f"shots[{unit.get('shot_index')}].frame_layouts",
            f"画外声音的说话人 {speaker!r} 出现在可见布局中，且本镜没有口型分离豁免",
            family="offscreen_voice", speaker=speaker,
            shot_index=unit.get("shot_index"), visible=sorted(visible),
        ))

    # 听者反应：对每个有画外声源的镜头，画内听者必须有可见反应证据。
    findings.extend(_check_listener_reactions(script, units, shots))
    return findings


def _reaction_evidence(shot: dict, listener: str) -> str:
    has_cue = any(cue in _shot_fields(shot) for cue in DEFAULT_REACTION_CUES)
    layouts = shot.get("frame_layouts") if isinstance(shot.get("frame_layouts"), dict) else {}
    ff = [e for e in (layouts.get("ff") or []) if isinstance(e, dict)
          and _text(e.get("name")) == listener]
    lf = [e for e in (layouts.get("lf") or []) if isinstance(e, dict)
          and _text(e.get("name")) == listener]
    if ff and lf:
        def key(entry):
            return tuple(_text(entry.get(k)) for k in ("position", "pose", "gaze_target"))
        if key(ff[0]) != key(lf[0]):
            return "frame_layout_change"
        if any(cue in " ".join(str(v) for v in lf[0].values()) for cue in DEFAULT_REACTION_CUES):
            return "layout_pose"
    if has_cue and listener in _shot_fields(shot):
        return "shot_text"
    return ""


def _check_listener_reactions(script: dict, units: list, shots: dict) -> list[dict]:
    findings: list[dict] = []
    per_shot: dict[Any, list[dict]] = {}
    for unit in units:
        if not isinstance(unit, dict):
            continue
        shot = shots.get(unit.get("shot_index"))
        if not _unit_is_offscreen(unit, shot):
            continue
        per_shot.setdefault(unit.get("shot_index"), []).append(unit)
    for shot_index, items in per_shot.items():
        shot = shots.get(shot_index) or {}
        visible = _visible_names(script, shot)
        speakers = {_text(u.get("speaker")) for u in items}
        listeners = [name for name in sorted(visible)
                     if name and name not in speakers]
        if not listeners:
            # 说话人自己在画内、且以口型分离方式发声：不要求第三方听者反应。
            continue
        reacted = [name for name in listeners if _reaction_evidence(shot, name)]
        if reacted:
            continue
        findings.append(_issue(
            "listener_reaction_missing", f"shots[{shot_index}].motion_desc",
            f"镜头 {shot_index} 有画外声源，但画内听者 {listeners} 没有任何可见反应",
            family="offscreen_voice", listeners=listeners,
            speakers=sorted(speakers), shot_index=shot_index,
        ))
    return findings


def _unit_timing(unit: dict) -> tuple[float | None, str]:
    """声音单元的时长与状态：``(seconds, state)``.

    2026-11-14 S06 G6 裁决：**准备态/确认态分离**。

    - ``confirmed``：``verified is True`` 且 ``actual_sec`` 是有效数字——用实测时长；
    - ``prepared``：尚未实测（或未核实）——用计划声明时长（``declared_sec`` /
      ``required_playback_sec`` / 兼容旧数据的 ``actual_sec``）做**规划**判断。

    准备态超窗只报规划问题（``audio_window_overflow_planned``，advisory），
    不按实测拒收；确认态超窗才是 ``audio_window_overflow``（reject）。
    """
    verified = unit.get("verified") is True
    actual = _number(unit.get("actual_sec"))
    declared = _number(unit.get("declared_sec") or unit.get("required_playback_sec"))
    if verified and actual is not None:
        return actual, "confirmed"
    fallback = declared if declared is not None else actual
    return fallback, "prepared"


def _check_audio(script: dict) -> list[dict]:
    outline = _outline(script)
    units = outline.get("voice_units")
    if not isinstance(units, list):
        return []
    findings: list[dict] = []
    shots = {s.get("index"): s for s in _iter_shots(script)}
    windows = script.get("audio_windows")
    windows = windows if isinstance(windows, dict) else {}
    by_shot: dict[Any, list[tuple[dict, float | None, str]]] = {}
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        kind = _text(unit.get("kind")).lower()
        actual = _number(unit.get("actual_sec"))
        window = _number(unit.get("window_sec"))
        if kind == "native_dialogue":
            if unit.get("verified") is not True or actual is None or actual <= 0:
                findings.append(_issue(
                    "native_dialogue_unverified",
                    f"chapter_outline.voice_units[{i}].verified",
                    f"原生对白 {unit.get('unit_id')!r} 没有 verified=true 与有效实测时长，"
                    "不能仅凭音轨存在判通过",
                    family="voice_timing", unit_id=unit.get("unit_id"),
                    verified=unit.get("verified"), actual_sec=unit.get("actual_sec"),
                ))
            continue
        if kind in ("tts_dialogue", "narration"):
            secs, state = _unit_timing(unit)
            if secs is not None and window is not None and secs > window + 1e-9:
                if state == "confirmed":
                    findings.append(_issue(
                        "audio_window_overflow",
                        f"chapter_outline.voice_units[{i}].actual_sec",
                        f"独立声音单元 {unit.get('unit_id')!r} 实测 {secs:.2f}s "
                        f"超过单元窗口 {window:.2f}s",
                        family="voice_timing", unit_id=unit.get("unit_id"),
                        shot_index=unit.get("shot_index"), state=state,
                        actual_sec=secs, window_sec=window,
                        overflow_sec=round(secs - window, 6),
                    ))
                else:
                    findings.append(_issue(
                        "audio_window_overflow_planned",
                        f"chapter_outline.voice_units[{i}].declared_sec",
                        f"独立声音单元 {unit.get('unit_id')!r} 计划时长 {secs:.2f}s "
                        f"超过单元窗口 {window:.2f}s（尚未实测，按规划问题上报）",
                        family="voice_timing", severity="advisory", unit_id=unit.get("unit_id"),
                        shot_index=unit.get("shot_index"), state=state,
                        declared_sec=secs, window_sec=window,
                        overflow_sec=round(secs - window, 6),
                    ))
            by_shot.setdefault(unit.get("shot_index"), []).append((unit, secs, state))
    # 逐镜合计：独立 TTS + 旁白共享同一镜头可用窗口。
    for shot_index, items in by_shot.items():
        confirmed_total = sum(sec for _u, sec, state in items
                              if state == "confirmed" and sec is not None)
        planned_total = sum(sec for _u, sec, state in items
                            if state == "prepared" and sec is not None)
        total = confirmed_total + planned_total
        limit = _number(windows.get(str(shot_index))) if windows else None
        if limit is None:
            shot = shots.get(shot_index) or {}
            limit = _number(shot.get("duration_sec"))
        if limit is None or total <= limit + 1e-9:
            continue
        if confirmed_total > limit + 1e-9:
            findings.append(_issue(
                "audio_window_overflow", f"shots[{shot_index}].audio_window",
                f"镜头 {shot_index} 的独立声音实测合计 {confirmed_total:.2f}s 超过可用窗口 "
                f"{limit:.2f}s（差 {confirmed_total - limit:.2f}s）",
                family="voice_timing", shot_index=shot_index, state="confirmed",
                unit_ids=[u.get("unit_id") for u, _s, st in items if st == "confirmed"],
                actual_sec=round(confirmed_total, 6), window_sec=round(limit, 6),
                overflow_sec=round(confirmed_total - limit, 6),
            ))
        else:
            findings.append(_issue(
                "audio_window_overflow_planned", f"shots[{shot_index}].audio_window",
                f"镜头 {shot_index} 的独立声音计划合计 {total:.2f}s 超过可用窗口 "
                f"{limit:.2f}s（尚未实测，按规划问题上报）",
                family="voice_timing", severity="advisory", shot_index=shot_index,
                state="prepared",
                unit_ids=[u.get("unit_id") for u, _s, _st in items],
                declared_sec=round(total, 6), window_sec=round(limit, 6),
                overflow_sec=round(total - limit, 6),
            ))
    return findings


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def check_script_semantics(script: dict) -> list[dict]:
    """对一份（可含执行候选合并结果的）稿件运行全部语义检查."""
    if not isinstance(script, dict):
        return [_issue("causal_gap", "script", "稿件不是对象，无法执行语义检查",
                       family="contract")]
    findings: list[dict] = []
    findings.extend(_check_causal_and_time(script))
    findings.extend(_check_offscreen(script))
    findings.extend(_check_audio(script))
    return _dedupe(findings)


def check_execution_semantics(script: dict, candidate: dict | None = None) -> list[dict]:
    """检查稿件本身；给定候选时检查「稿件 + 候选」合并后的执行结果."""
    return check_script_semantics(apply_execution_candidate(script, candidate))


def _dedupe(findings: list[dict]) -> list[dict]:
    seen = set()
    out: list[dict] = []
    for item in findings:
        key = (item.get("code"), item.get("path"), item.get("message"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def codes_of(findings: list[dict]) -> list[str]:
    """稳定排序后的 code 列表（供报告/夹具对照）."""
    return sorted({str(f.get("code")) for f in findings if f.get("code")})


def check_candidate_paths(script: dict, before: dict, after: dict) -> list[dict]:
    """对执行候选的 before/after 变化做语义差分检查.

    返回的 path 指向**候选内**的实际字段（``visual_plan.visible_characters``、
    ``shots[k].prompt`` 等），供 ``shared.execution_contract`` 的反馈路由使用。
    """
    before_merged = apply_execution_candidate(script, before)
    after_merged = apply_execution_candidate(script, after)
    before_codes = {(f["code"], f["path"]) for f in check_script_semantics(before_merged)}
    out: list[dict] = []
    for finding in check_script_semantics(after_merged):
        if (finding["code"], finding["path"]) in before_codes:
            continue
        out.append(finding)
    return _dedupe(out)


__all__ = [
    "CHECKER_VERSION",
    "KNOWN_CODES",
    "CAUSAL_UNRESOLVED_MARK",
    "DEFAULT_TEMPORAL_MARKERS",
    "DEFAULT_REACTION_CUES",
    "DEFAULT_LIPSYNC_DISABLED_CUES",
    "DEFAULT_COMPLETION_MARKERS",
    "apply_execution_candidate",
    "check_script_semantics",
    "check_execution_semantics",
    "check_candidate_paths",
    "codes_of",
    "remap_causal_refs",
    "validate_causal_identity",
]
