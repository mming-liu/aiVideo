# -*- coding: utf-8 -*-
"""shared.execution_semantics - 三个样本族要求的**自动语义检查器**（S02 R2）.

本模块是 ``shared.execution_contract`` 的语义伴生层：执行合同负责字段/权限/摘要，
本模块负责把「合法时间省略 / 画外声音 / 声音时长」这三类主张变成可自动检出的
结构化记录。它只消费**显式结构化字段**与通用语言标记，不绑定任何小说、角色或
章节：

- ``causal_gap``：``chapter_outline.causal_chain`` 有 ``结果`` 角色却缺 ``行动``
  角色——结果没有执行路径；
- ``time_ellipsis_lost``：出现上述因果缺口，且全稿没有任何时间推进标记/过程载体，
  结果状态却已断言完成——这是「删掉过程与时间，只留结果」，不是合法省略；
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
    """该镜是否自带可演的过程（动作分段 ≥2 或有关键帧）——结构判据，不看词表."""
    segments = _MOTION_SEGMENT_RE.findall(_text(shot.get("motion_desc")))
    keyframes = shot.get("keyframes")
    return len(segments) >= 2 or (isinstance(keyframes, list) and bool(keyframes))


#: 关联判定里不承担内容的 2 字组合（避免「的了」「是在」这类把任意两镜连起来）。
_LINK_STOP_BIGRAMS = frozenset({
    "的了", "是在", "在的", "了一", "这个", "那个", "什么", "没有", "一个",
    "之后", "之前", "然后", "接着", "于是", "已经", "正在", "起来", "出来",
})


def _content_bigrams(text: str) -> set[str]:
    """中文内容 2 字组合（去掉空白/标点与停用组合）.

    不用分词器：本模块只做确定性检查，不引入外部依赖；2 字组合对「城门」这类
    实体/地点足够稳，且对措辞漂移（「抵达」↔「站定」）保持一定的鲁棒性。
    """
    cleaned = re.sub(r"[\s\W_]+", "", str(text or ""), flags=re.UNICODE)
    out = set()
    for i in range(len(cleaned) - 1):
        gram = cleaned[i:i + 2]
        if gram not in _LINK_STOP_BIGRAMS:
            out.add(gram)
    return out


def _shares_content(text: str, anchor: str) -> bool:
    """两段文本是否共享至少一个内容 2 字组合."""
    a = _content_bigrams(anchor)
    if not a:
        return False
    return bool(a & _content_bigrams(text))


def _process_carriers(script: dict, *, anchors: list[str] | None = None) -> tuple[list[str], list[str]]:
    """把过程载体分成**与被质疑的因果相关**和**无法建立关联**两组.

    2026-11-12 复核反例（第二例）：原输入 `time_ellipsis_lost / reject`，**只增加一个
    无关镜头**（关键帧画一只鸟），原问题没有任何变化，拒收却消失了——因为旧实现把
    全稿任意关键帧都当成过程证据。过程证据必须绑定到被质疑的事实/事件/镜头上。

    关联规则：载体镜的文本与 `causal_chain` 里**起因/结果**任一条目共享至少一个内容
    2 字组合。建立不了关联的载体**不算证据**，如实记进 `unlinked`（待语义裁决），
    裁决与「完全没有载体」时保持一致。

    Returns:
        `(linked, unlinked)`，元素形如 `shot:3`。
    """
    linked: list[str] = []
    unlinked: list[str] = []
    anchor_list = [a for a in (anchors or []) if _text(a)]
    for shot in _iter_shots(script):
        if not _shot_has_process(shot):
            continue
        label = "shot:%s" % (shot.get("index"),)
        text = _shot_fields(shot)
        if anchor_list and any(_shares_content(text, a) for a in anchor_list):
            linked.append(label)
        else:
            unlinked.append(label)
    return linked, unlinked


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


def _has_temporal_marker(script: dict) -> bool:
    text = "\n".join([_all_shot_text(script),
                      _bridge_text(script),
                      str(_outline(script).get("narration_lines") or ""),
                      str(_outline(script).get("main_events") or "")])
    return any(re.search(pattern, text, re.I) for pattern in DEFAULT_TEMPORAL_MARKERS)


def _has_completion_marker(script: dict) -> bool:
    text = "\n".join([str(_outline(script).get("causal_chain") or ""),
                      str(_outline(script).get("main_events") or ""),
                      _all_shot_text(script)])
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
    # 标签缺失先作为**结构证据**记录（advisory），不再直接拒收：合法省略的镜头
    # 往往有完整过程，只是计划没写「行动」标签。
    #
    # 但过程证据必须**绑定到被质疑的因果**：无关镜头（例如新增一镜鸟的关键帧）
    # 不得把拒收豁免掉。建立不了关联的载体只记进 `unlinked_carriers`，不算证据，
    # 裁决与「完全没有载体」时一致。
    anchors = [text for role, text, _i in roles
               if text and (_role_matches(role, _CAUSE_ROLES)
                            or _role_matches(role, _RESULT_ROLES))]
    linked, unlinked = _process_carriers(script, anchors=anchors)
    findings = [_issue(
        "causal_gap", "chapter_outline.causal_chain",
        "因果链只有起因与结果、没有行动/过程标签：结果缺少显式执行路径（结构记录）",
        family="temporal_ellipsis",
        severity="advisory",
        expected_roles=["起因", "行动", "结果"],
        actual_roles=[role for role, _t, _i in roles],
        carrier_evidence=linked,
        unlinked_carriers=unlinked,
    )]
    # 只有**与被质疑因果相关**的过程载体才能豁免；无关载体不改变裁决。
    if (not linked and not _has_temporal_marker(script)
            and _has_completion_marker(script)):
        findings.append(_issue(
            "time_ellipsis_lost", "chapter_outline.narration_lines",
            "结果状态已断言完成，但全稿既没有时间推进标记、也没有与该结果相关的过程载体；"
            "这是删除时间/过程后只留结果，不是合法的常规省略",
            family="temporal_ellipsis",
            missing=["temporal_marker", "process_carrier"],
            unlinked_carriers=unlinked,
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


def _check_audio(script: dict) -> list[dict]:
    outline = _outline(script)
    units = outline.get("voice_units")
    if not isinstance(units, list):
        return []
    findings: list[dict] = []
    shots = {s.get("index"): s for s in _iter_shots(script)}
    windows = script.get("audio_windows")
    windows = windows if isinstance(windows, dict) else {}
    by_shot: dict[Any, list[dict]] = {}
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
            if actual is not None and window is not None and actual > window + 1e-9:
                findings.append(_issue(
                    "audio_window_overflow",
                    f"chapter_outline.voice_units[{i}].actual_sec",
                    f"独立声音单元 {unit.get('unit_id')!r} 实测 {actual:.2f}s "
                    f"超过单元窗口 {window:.2f}s",
                    family="voice_timing", unit_id=unit.get("unit_id"),
                    shot_index=unit.get("shot_index"),
                    actual_sec=actual, window_sec=window,
                    overflow_sec=round(actual - window, 6),
                ))
            by_shot.setdefault(unit.get("shot_index"), []).append(unit)
    # 逐镜合计：独立 TTS + 旁白共享同一镜头可用窗口。
    for shot_index, items in by_shot.items():
        actual_total = sum(_number(u.get("actual_sec")) or 0.0 for u in items)
        limit = _number(windows.get(str(shot_index))) if windows else None
        if limit is None:
            shot = shots.get(shot_index) or {}
            limit = _number(shot.get("duration_sec"))
        if limit is None or actual_total <= limit + 1e-9:
            continue
        findings.append(_issue(
            "audio_window_overflow", f"shots[{shot_index}].audio_window",
            f"镜头 {shot_index} 的独立声音合计 {actual_total:.2f}s 超过可用窗口 "
            f"{limit:.2f}s（差 {actual_total - limit:.2f}s）",
            family="voice_timing", shot_index=shot_index,
            unit_ids=[u.get("unit_id") for u in items],
            actual_sec=round(actual_total, 6), window_sec=round(limit, 6),
            overflow_sec=round(actual_total - limit, 6),
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
    "DEFAULT_TEMPORAL_MARKERS",
    "DEFAULT_REACTION_CUES",
    "DEFAULT_LIPSYNC_DISABLED_CUES",
    "DEFAULT_COMPLETION_MARKERS",
    "apply_execution_candidate",
    "check_script_semantics",
    "check_execution_semantics",
    "check_candidate_paths",
    "codes_of",
]
