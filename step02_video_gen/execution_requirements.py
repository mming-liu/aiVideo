# -*- coding: utf-8 -*-
"""视频执行要求（S04）：语义镜头事实、片段映射、声音策略与反馈路由.

冻结请求 ``FrozenVideoRequest`` 已经记录 provider 实际生效的 prompt/参数/引用。
本模块在它之上补齐**执行层语义事实**，供 plan-only 与实际 prepare 共用：

- ``semantic_shot``：稳定镜头身份、起止状态、必要事件与动作顺序、计划时长；
- ``production_fragments``：可选的制作片段拆分及其到语义镜头的映射与事件覆盖；
- ``references``：参考模式、实际使用素材的 role/sha256、能力未验证项；
- ``audio``：声音策略、需保留的声音身份、图外旁白；
- ``source``：已用画面计划/音频产物摘要与 script revision。

同时提供 ``classify_failure``：把失败分成「视频执行准备 / 候选失败+证据 /
回 Step01 / 沿用同任务恢复」四类，路由不是猜测，而是按结构化 code 映射。

本模块不读网络、不写盘、不提交任务。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "video_execution_requirements.v1"

#: 片段事件覆盖/动作顺序类问题：候选失败并附证据（不是 Step01 语义问题）。
CANDIDATE_FAILURE_CODES = frozenset({
    "action_missing", "event_order_mismatch", "fragment_event_loss",
    "start_end_state_mismatch", "required_event_missing", "reference_identity_mismatch",
    "visual_plan_missing", "visual_plan_stale", "audio_identity_missing",
})

#: 参数/参考能力/时长能力不支持：回视频执行准备（换 provider/重排参数）。
PREPARE_CODES = frozenset({
    "role_unsupported", "reference_capacity", "bad_duration", "model_missing",
    "prompt_empty", "duration_insufficient", "provider_capability",
    "prepared_pending_verification", "prepared_unsupported",
})

#: 结构无法容纳必要内容：回 Step01 并附冲突，不允许在下游删内容凑。
STEP01_CODES = frozenset({
    "structural_infeasible", "content_conflict", "fragment_capacity_exceeded",
    "semantic_change_unverified", "offscreen_speaker_visible",
    "listener_reaction_missing", "causal_gap", "time_ellipsis_lost",
})

#: 传输/下载/任务未知：沿用同一任务恢复（不重新生成）。
RESUME_CODES = frozenset({
    "submission_unknown", "network_error", "timeout", "download_failed",
    "poll_failed", "task_unknown", "rate_limited", "transport_error",
})

_TASK_RESUME_PREFIXES = ("prepared_pending", "task_", "poll_", "download_", "transport_")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        digest = hashlib.sha256()
        with open(p, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _shot_events(shot: dict, script: dict) -> list[dict]:
    """从 motion_desc / shot_beats / plan_refs 提取可核对的动作顺序."""
    events: list[dict] = []
    motion = _text(shot.get("motion_desc") or shot.get("prompt"))
    for i, part in enumerate(p for p in motion.replace("；", ";").split(";") if p.strip()):
        text = part.strip()
        if "：" in text or ":" in text:
            # “前两秒：动作”式时间前缀：只保留动作部分，但保留原文。
            head, _, tail = text.partition("：") if "：" in text else text.partition(":")
            if tail.strip():
                text = tail.strip()
        events.append({"order": i + 1, "event": text, "source": "motion_desc"})
    # 结构化分镜节拍：只取与本镜 index 对应的条目（不按文本相似度猜）。
    outline = script.get("chapter_outline") or {}
    beats = outline.get("shot_beats") if isinstance(outline, dict) else None
    index = shot.get("index")
    if isinstance(beats, list) and isinstance(index, int) and 0 < index <= len(beats):
        beat = beats[index - 1]
        if isinstance(beat, str) and beat.strip():
            body = beat.split("｜")[0]
            body = body.split("：", 1)[1] if "：" in body else body
            events.append({"order": len(events) + 1, "event": body.strip(),
                           "source": "shot_beats"})
    return events


def _start_end_state(shot: dict) -> dict:
    layouts = shot.get("frame_layouts") if isinstance(shot.get("frame_layouts"), dict) else {}
    def _layout_state(frame: str) -> list[dict]:
        out = []
        for entry in layouts.get(frame) or []:
            if not isinstance(entry, dict):
                continue
            out.append({k: entry.get(k) for k in ("name", "on_screen", "position",
                                                  "pose", "gaze_target", "props")
                        if entry.get(k) is not None})
        return out
    return {
        "start": {"desc": _text(shot.get("ff_desc")), "layout": _layout_state("ff")},
        "end": {"desc": _text(shot.get("lf_desc")), "layout": _layout_state("lf")},
    }


def _reference_facts(frame_paths: list, reference_mode: str) -> dict:
    materials = []
    for i, path in enumerate(frame_paths or []):
        name = Path(str(path)).name.lower()
        role = "first_frame" if "first_frame" in name else (
            "last_frame" if "last_frame" in name else (
                "keyframe" if "keyframe" in name else "reference"))
        materials.append({"role": role, "path": str(path),
                          "sha256": _sha256_file(str(path)),
                          "used": Path(str(path)).is_file()})
    unsupported: list[str] = []
    try:
        from shared.model_caps import active_video_model
        from _video_gen import create_service
        provider = os.getenv("VIDEO_PROVIDER", "")
        svc = create_service(provider) if provider else None
        caps = getattr(svc, "capabilities", None) if svc is not None else None
        supported = getattr(caps, "supported_roles", None) if caps is not None else None
        if isinstance(supported, (set, frozenset, list, tuple)):
            for material in materials:
                if material["role"] not in supported:
                    unsupported.append(material["role"])
    except Exception:
        unsupported = []
    return {
        "mode": reference_mode,
        "materials": materials,
        "unsupported_roles": sorted(set(unsupported)),
        "unverified": [] if materials else ["no_reference_material_used"],
    }


def _audio_facts(script: dict, shot: dict) -> dict:
    outline = script.get("chapter_outline") or {}
    units = outline.get("voice_units") if isinstance(outline, dict) else None
    units = units if isinstance(units, list) else []
    index = shot.get("index")
    relevant = [u for u in units if isinstance(u, dict) and u.get("shot_index") == index]
    try:
        from shared.audio_policy import resolve_audio_policy
        narration_present = bool(outline.get("narration_lines")) if isinstance(outline, dict) else False
        policy = resolve_audio_policy(narration_present=narration_present)
        policy_facts = {
            "native_voice": policy.native_voice,
            "tts_required": policy.tts_required,
            "effective_merge_mode": policy.effective_merge_mode,
            "narration_tts_required": policy.narration_tts_required,
            "reason": policy.reason,
        }
    except Exception as exc:  # noqa: BLE001
        policy_facts = {"unverified": f"audio_policy_unavailable: {exc}"}
    retained = [{
        "unit_id": u.get("unit_id"), "kind": u.get("kind"),
        "speaker": u.get("speaker"), "text": u.get("text"),
        "verified": u.get("verified"), "actual_sec": u.get("actual_sec"),
        "window_sec": u.get("window_sec"),
    } for u in relevant]
    return {
        "policy": policy_facts,
        "has_dialogue": any(_text(u.get("kind")).lower().endswith("dialogue") for u in relevant),
        "has_narration": any(_text(u.get("kind")).lower() == "narration" for u in relevant),
        "retained_voice_identity": retained,
        "native_voice_requires_verification": any(
            _text(u.get("kind")).lower() == "native_dialogue" for u in relevant),
    }


def build_requirements(shot: dict, script: dict, *, visual_plan: dict | None = None,
                       frame_paths: list | None = None,
                       reference_mode: str | None = None,
                       model_id: str | None = None) -> dict:
    """构建执行要求（纯函数）."""
    mode = reference_mode or os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    index = shot.get("index")
    plan_entries = [s for s in (visual_plan or {}).get("shots") or []
                    if isinstance(s, dict) and s.get("shot_index") == index]
    plan_entry = plan_entries[0] if plan_entries else None
    plan_digest = _sha256_text(_canonical(visual_plan)) if visual_plan else ""
    events = _shot_events(shot, script)
    issues: list[dict] = []
    if visual_plan is not None and plan_entry is None:
        issues.append({"code": "visual_plan_missing", "path": f"shots[{index}]",
                       "message": "画面计划没有本镜条目"})
    if plan_entry is not None and plan_entry.get("status") == "blocked":
        issues.append({"code": "visual_plan_blocked", "path": f"shots[{index}]",
                       "message": "画面计划本镜被阻塞"})
    start_end = _start_end_state(shot)
    if not start_end["start"]["desc"] or not start_end["end"]["desc"]:
        issues.append({"code": "start_end_state_mismatch", "path": f"shots[{index}]",
                       "message": "首帧或尾帧状态描述缺失，无法核对动作起止"})
    fragments = []
    raw_fragments = shot.get("production_fragments") or shot.get("fragments") or []
    if isinstance(raw_fragments, list):
        for i, fragment in enumerate(raw_fragments):
            if not isinstance(fragment, dict):
                continue
            fragments.append({
                "fragment_id": str(fragment.get("fragment_id") or f"frag-{index}-{i + 1}"),
                "semantic_shot_id": str(shot.get("shot_id") or shot.get("id")
                                        or f"shot-{index}"),
                "shot_index": index,
                "start_state": _text(fragment.get("start_state") or fragment.get("ff_desc")),
                "end_state": _text(fragment.get("end_state") or fragment.get("lf_desc")),
                "events": [str(e) for e in (fragment.get("events") or [])],
                "duration_sec": fragment.get("duration_sec"),
            })
    if fragments:
        covered = " ".join(e for f in fragments for e in f["events"])
        missing = [e["event"] for e in events if e["event"] not in covered
                   and not any(e["event"] in f["events"] for f in fragments)]
        # 片段拆分必须覆盖必要事件，并保持顺序：按片段出现顺序拼接后检查子序列。
        sequence = [e for f in fragments for e in f["events"]]
        positions = []
        for event in [e["event"] for e in events]:
            pos = next((i for i, s in enumerate(sequence) if event in s or s in event), None)
            positions.append(pos)
        ordered = all(p is not None for p in positions) and positions == sorted(positions)
        if missing:
            issues.append({"code": "fragment_event_loss", "path": f"shots[{index}].fragments",
                           "message": "片段拆分丢失必要事件", "missing": missing})
        if not ordered:
            issues.append({"code": "event_order_mismatch", "path": f"shots[{index}].fragments",
                           "message": "片段事件顺序与语义镜头动作顺序不一致",
                           "events": [e["event"] for e in events]})
    try:
        from shared.model_caps import model_duration_range
        lo, hi = model_duration_range(model_id)
    except Exception:
        lo, hi = (None, None)
    planned = shot.get("duration_sec") or shot.get("duration")
    duration = {
        "planned_sec": planned,
        "model_range": [lo, hi],
        "unverified": [] if isinstance(planned, (int, float)) else ["planned_duration_missing"],
    }
    if (isinstance(planned, (int, float)) and lo is not None and hi is not None
            and float(hi) > 0):
        if not (float(lo) <= float(planned) <= float(hi)):
            issues.append({"code": "bad_duration", "path": f"shots[{index}].duration_sec",
                           "message": f"计划时长 {planned}s 超出模型区间 {lo}-{hi}s"})
    refs = _reference_facts(list(frame_paths or []), mode)
    for role in refs.get("unsupported_roles") or []:
        issues.append({"code": "role_unsupported", "path": f"shots[{index}].references",
                       "message": f"当前 provider 不支持参考角色 {role}"})
    audio = _audio_facts(script, shot)
    audio["continuity"] = {
        "semantic_shot_id": str(shot.get("shot_id") or shot.get("id") or f"shot-{index}"),
        "fragment_ids": [f["fragment_id"] for f in fragments],
        "same_semantic_shot": True,
        "voice_units": [u.get("unit_id") for u in audio.get("retained_voice_identity") or []],
        "note": "片段拆分只重排同一语义镜头内的制作粒度；声音单元仍绑同一 shot_index，"
                "不跨镜换说话人/改正文",
    }
    audio_units = audio.get("retained_voice_identity") or []
    audio_digest = _sha256_text(_canonical(audio_units)) if audio_units else ""
    req = {
        "schema": SCHEMA,
        "semantic_shot": {
            "shot_id": str(shot.get("shot_id") or shot.get("id") or f"shot-{index}"),
            "shot_index": index,
            "main_subject": _text(shot.get("main_subject")),
            "source_spans": shot.get("source_spans") or [],
            "plan_refs": shot.get("plan_refs") or {},
            "start_end_state": start_end,
            "required_events": events,
            "action_order": [e["event"] for e in events],
        },
        "production_fragments": fragments,
        "references": refs,
        "audio": audio,
        "duration": duration,
        "visual_plan": {
            "digest": plan_digest,
            "frames": [f.get("frame_id") for f in (plan_entry or {}).get("frames") or []],
            "visible_characters": (plan_entry or {}).get("visible_characters") or [],
            "offscreen_speakers": (plan_entry or {}).get("offscreen_speakers") or [],
        },
        "audio_artifacts": {"digest": audio_digest, "units": audio_units},
        "theoretical_shot_index": index,
        "issues": issues,
        "unverified": [] if visual_plan is not None else ["visual_plan_not_supplied"],
    }
    return req


def merge_into_frozen(frozen, requirements: dict) -> dict:
    """把执行要求并入冻结请求（requirements/source），并重算 request_key."""
    existing = dict(getattr(frozen, "requirements", None) or {})
    existing["execution"] = requirements
    frozen.requirements = existing
    source = dict(getattr(frozen, "source", None) or {})
    source.update({
        "semantic_shot": requirements.get("semantic_shot") or {},
        "production_fragments": requirements.get("production_fragments") or [],
        "visual_plan_digest": (requirements.get("visual_plan") or {}).get("digest"),
        "audio_digest": (requirements.get("audio_artifacts") or {}).get("digest"),
        "execution_requirements_schema": requirements.get("schema"),
    })
    frozen.source = source
    frozen.compute_key()
    return frozen


def validate_requirements(frozen, requirements: dict) -> list[dict]:
    """结构核对：冻结请求必须带上语义事实，且与计划摘要一致."""
    problems: list[dict] = []
    if not isinstance(requirements, dict) or requirements.get("schema") != SCHEMA:
        return [{"code": "requirements_schema", "field_path": "requirements.execution",
                 "message": "执行要求缺失或 schema 不符"}]
    problems.extend(dict(i) for i in requirements.get("issues") or [])
    source = getattr(frozen, "source", None) or {}
    if source.get("visual_plan_digest") != (requirements.get("visual_plan") or {}).get("digest"):
        problems.append({"code": "visual_plan_stale", "field_path": "source.visual_plan_digest",
                         "message": "冻结请求与执行要求的画面计划摘要不一致"})
    if not (requirements.get("semantic_shot") or {}).get("required_events"):
        problems.append({"code": "required_event_missing", "field_path": "requirements.execution",
                         "message": "语义镜头没有可核对的必要事件"})
    return problems


def classify_failure(code: str, *, message: str = "",
                     affected: list | None = None,
                     evidence: dict | None = None) -> dict:
    """把失败 code 路由到四类责任位置（不按错误文案猜语义）."""
    code = str(code or "").strip()
    affected = list(affected or [])
    if code in STEP01_CODES:
        route, action = "step01", "revise_semantics_or_content"
    elif code in CANDIDATE_FAILURE_CODES:
        route, action = "candidate_failed", "retry_candidate_with_evidence"
    elif code in RESUME_CODES or any(code.startswith(p) for p in _TASK_RESUME_PREFIXES):
        route, action = "task_resume", "resume_same_task"
    elif code in PREPARE_CODES:
        route, action = "video_execution_prepare", "adjust_request_or_provider"
    else:
        # 未知 code 不静默当作可恢复：默认回执行准备并要求提供证据。
        route, action = "video_execution_prepare", "classify_before_retry"
    return {
        "code": code, "route": route, "action": action, "message": message,
        "affected": affected, "evidence": dict(evidence or {}),
        "requires_new_post": route in ("video_execution_prepare", "candidate_failed"),
        "resume_same_task": route == "task_resume",
    }


__all__ = [
    "SCHEMA", "CANDIDATE_FAILURE_CODES", "PREPARE_CODES", "STEP01_CODES",
    "RESUME_CODES", "build_requirements", "merge_into_frozen",
    "validate_requirements", "classify_failure",
]
