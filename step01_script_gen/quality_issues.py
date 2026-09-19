"""Unified structured quality issues for Step01.

Rule version is intentionally explicit: reports produced by older rules must be
re-checked even when the script revision is unchanged.
"""
from __future__ import annotations

import contextlib
import io
import re
from dataclasses import dataclass, field
from typing import Iterable

RULE_VERSION = "2026-09-19.16"
RULE_IMPLEMENTATION = "evidence-gated-writes-v1"


def _compute_rule_source_fingerprint() -> str:
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parent
    project = root.parent
    files = [
        root / "quality_issues.py",
        root / "state_updates.py",
        root / "validation.py",
        root / "timecode.py",
        root / "split_visuals.py",
        project / "shared" / "style_prefix.py",
        project / "shared" / "vimax_bridge.py",
        *sorted((root / "constraints").glob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in files:
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:16]


RULE_SOURCE_FINGERPRINT = _compute_rule_source_fingerprint()


@dataclass
class Issue:
    code: str
    severity: str  # hard | visual_risk | advisory | video_advisory | unverified
    message: str
    shot_id: object = None
    frame_id: str = ""
    entity_id: str = ""
    field_path: str = ""
    evidence: str = ""
    related_shot_ids: list = field(default_factory=list)
    repairable: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "shot_id": self.shot_id,
            "frame_id": self.frame_id,
            "entity_id": self.entity_id,
            "field_path": self.field_path,
            "evidence": self.evidence,
            "related_shot_ids": self.related_shot_ids,
            "repairable": self.repairable,
        }


def parse_shot_ref(message: str):
    """Extract (shot_id, related_ids) from checklist messages.

    Supports integers, half-integer split shots (13.5), hooks, and cross-shot
    messages such as 镜头13→14.  Structured callers should prefer Issue.shot_id,
    but legacy checklist text must still never collapse 13.5 into 13.
    """
    text = str(message or "")
    if "钩子镜头" in text or "镜头hook" in text or "钩子" in text:
        return 0, []
    m = re.search(r"镜头\s*(\d+(?:\.\d+)?)\s*(?:→|->)\s*(\d+(?:\.\d+)?)", text)
    if m:
        return m.group(1), [m.group(2)]
    m = re.search(r"镜头\s*(\d+(?:\.\d+)?)", text)
    if m:
        return m.group(1), []
    return None, []


def _frame_texts(shot: dict) -> list[tuple[str, str]]:
    out = [("ff", str(shot.get("ff_desc") or ""))]
    kfs = shot.get("keyframes") or []
    kfs = [k for k in kfs if isinstance(k, dict)]
    kfs.sort(key=lambda k: float(k.get("t_sec") or k.get("time_ratio") or 0))
    for i, kf in enumerate(kfs):
        out.append((f"kf{i}", str(kf.get("desc") or "")))
    out.append(("lf", str(shot.get("lf_desc") or "")))
    return out


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[，。；！？]", text or "") if c.strip()]


def _char_names(shot: dict) -> list[str]:
    names: list[str] = []
    for layout_key in ("frame_layouts", "char_layout"):
        layout = shot.get(layout_key)
        if isinstance(layout, dict):
            buckets = []
            for key in ("ff", "lf"):
                buckets.extend(layout.get(key) or [])
            layout = buckets
        if isinstance(layout, list):
            for item in layout:
                if not isinstance(item, dict):
                    continue
                n = str(item.get("name") or "").strip()
                if n and n not in names:
                    names.append(n)
    return names


def _eye_conflicts(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    open_words = ("双目圆睁", "双眼圆睁", "圆睁", "睁眼", "双目大睁", "双眼大睁")
    closed_words = ("双目紧闭", "双眼紧闭", "闭眼")
    names = _char_names(shot)
    for field_name in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
        text = str(shot.get(field_name) or "")
        if not text:
            continue
        # Subject inheritance works within a sentence, not across sentences.
        for sentence in re.split(r"[。；！？]", text):
            current_names: list[str] = []
            states: dict[str, set[str]] = {}
            has_explicit_time = bool(
                re.search(r"\d+(?:\.\d+)?\s*(?:-|~|至|到)\s*\d+(?:\.\d+)?\s*秒", sentence)
                or re.search(r"(?:前|第)?\s*\d+(?:\.\d+)?\s*秒", sentence)
            )
            for clause in _clauses(sentence):
                if has_explicit_time:
                    states = {}
                mentioned = [n for n in names if n in clause]
                if mentioned:
                    current_names = mentioned
                has_open = any(w in clause for w in open_words)
                has_closed = any(w in clause for w in closed_words)
                if not (has_open or has_closed):
                    continue
                if len(current_names) == 1:
                    ent = current_names[0]
                    bucket = states.setdefault(ent, set())
                    if has_open:
                        bucket.add("open")
                    if has_closed:
                        bucket.add("closed")
                    if {"open", "closed"} <= bucket:
                        issues.append(Issue(
                            code="eye_state_conflict", severity="visual_risk",
                            message=f"角色{ent}同段同时出现睁眼/闭眼",
                            shot_id=shot_id, entity_id=ent, field_path=field_name,
                            evidence=sentence, repairable=True,
                        ))
                        states[ent] = set()
                elif has_open and has_closed:
                    issues.append(Issue(
                        code="eye_state_conflict_unverified", severity="unverified",
                        message="同句出现睁眼/闭眼但无法确认主语",
                        shot_id=shot_id, field_path=field_name, evidence=clause,
                        repairable=False,
                    ))
    return issues


def _action_time_issues(shot: dict, shot_id) -> list[Issue]:
    try:
        duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
    except (TypeError, ValueError):
        return []
    motion = str(shot.get("motion_desc") or "")
    if duration <= 0 or not motion:
        return []
    ends = []
    # Support 4-5秒 and legacy/compact 4-5低头往前赶.
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?", motion):
        try:
            ends.append(float(m.group(2)))
        except ValueError:
            pass
    # Single-end expressions such as 前2秒 / 第3秒.
    for m in re.finditer(r"(?:前|第)?\s*(\d+(?:\.\d+)?)\s*秒", motion):
        try:
            ends.append(float(m.group(1)))
        except ValueError:
            pass
    if ends and max(ends) > duration + 0.01:
        return [Issue(
            code="action_time_out_of_range", severity="visual_risk",
            message=f"动作分段到第{max(ends):g}秒，超过镜头时长{duration:g}秒",
            shot_id=shot_id, field_path="motion_desc", evidence=motion,
            repairable=True,
        )]
    return []


def _state_order_issues(shot: dict, shot_id) -> list[Issue]:
    names = _char_names(shot)
    if not names:
        return []
    gone: set[str] = set()
    for frame_id, text in _frame_texts(shot):
        for clause in _clauses(text):
            for name in names:
                if name not in clause:
                    continue
                if any(w in clause for w in ("已没入水中", "已退场", "已消失", "已离开")):
                    gone.add(name)
                    continue
                if name in gone and any(w in clause for w in ("跃出", "跃起", "冲出", "登船", "跃上", "窜出")):
                    return [Issue(
                        code="state_order_replay", severity="visual_risk",
                        message=f"{name}已退场/入水后，后续帧又重复执行入场动作",
                        shot_id=shot_id, frame_id=frame_id, entity_id=name,
                        field_path=frame_id, evidence=clause, repairable=True,
                    )]
    return []


def _pose_conflicts(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    layouts = shot.get("frame_layouts") or {}
    if not isinstance(layouts, dict):
        return issues
    sitting = ("坐", "跪", "盘膝", "踞蹲", "蹲")
    lying = ("仰卧", "躺", "卧", "倒")
    buckets: list[tuple[str, list]] = []
    for key in ("ff", "lf"):
        if isinstance(layouts.get(key), list):
            buckets.append((key, layouts.get(key)))
    kfs = layouts.get("kfs")
    if isinstance(kfs, list):
        for i, frame in enumerate(kfs):
            if isinstance(frame, list):
                buckets.append((f"kf{i}", frame))
    for frame_id, frames in buckets:
        for item in frames:
            if not isinstance(item, dict):
                continue
            pose = str(item.get("pose") or "")
            if any(s in pose for s in sitting) and any(l in pose for l in lying):
                issues.append(Issue(
                    code="pose_conflict", severity="visual_risk",
                    message=f"{item.get('name','角色')}同帧姿态同时包含坐/跪和躺卧",
                    shot_id=shot_id, frame_id=frame_id,
                    entity_id=str(item.get("name") or ""), field_path=f"frame_layouts.{frame_id}.pose",
                    evidence=pose, repairable=True,
                ))
    return issues


def _surface_conflicts(shot: dict, shot_id) -> list[Issue]:
    # Only clearly incompatible carrier pairs.  “船头/船板” may describe the
    # same physical surface and must not be treated as a conflict.
    incompatible = (
        ("车厢内", "院内地面"), ("车厢内", "地面"),
        ("马背", "院内地面"), ("轿内", "院内地面"),
        ("船板", "院内地面"), ("船头", "院内地面"), ("船尾", "院内地面"),
    )
    names = _char_names(shot)
    issues: list[Issue] = []
    for frame_id, text in _frame_texts(shot):
        for clause in _clauses(text):
            found_pair = None
            for a, b in incompatible:
                if a in clause and b in clause:
                    found_pair = (a, b)
                    break
            if not found_pair:
                continue
            mentioned = [n for n in names if n in clause]
            if len(mentioned) == 1:
                issues.append(Issue(
                    code="surface_conflict", severity="visual_risk",
                    message=f"{mentioned[0]}同帧出现互斥承载面: {list(found_pair)}",
                    shot_id=shot_id, frame_id=frame_id, entity_id=mentioned[0],
                    field_path="ff_desc/lf_desc", evidence=clause, repairable=True,
                ))
    return issues


def _onscreen_prop_issues(shot: dict, shot_id) -> list[Issue]:
    layouts = shot.get("frame_layouts")
    if not isinstance(layouts, dict):
        return []
    issues: list[Issue] = []
    from shared.content_safety import WEAPON_WORDS as _WEAPON_WORDS
    _HOLD = ("握着", "手持", "持刀", "持匕首", "紧握", "右手持", "左手持")
    _RELEASE = ("已脱手", "脱手", "滑落", "掉落", "坠入", "没入", "沉入")
    frame_map = dict(_frame_texts(shot))
    for frame_id, text in frame_map.items():
        frames = layouts.get(frame_id)
        if frame_id.startswith("kf"):
            try:
                frames = (layouts.get("kfs") or [])[int(frame_id[2:])]
            except Exception:
                frames = []
        for item in frames or []:
            if not isinstance(item, dict) or item.get("on_screen") is not False:
                continue
            name = str(item.get("name") or "")
            for clause in _clauses(text):
                if name in clause and any(w in clause for w in ("攻击", "持刀", "持匕首", "刺出", "劈砍")):
                    issues.append(Issue(
                        code="offscreen_character_action", severity="visual_risk",
                        message=f"{name}标记离场但同帧仍要求其执行动作",
                        shot_id=shot_id, frame_id=frame_id, entity_id=name,
                        field_path=f"frame_layouts.{frame_id}.on_screen", evidence=clause,
                        repairable=True,
                    ))
        # Same-frame weapon state contradiction: explicit release + explicit hold.
        _weapon_hit = next((w for w in _WEAPON_WORDS if w in text), "")
        if _weapon_hit and any(w in text for w in _HOLD) and any(w in text for w in _RELEASE):
            issues.append(Issue(
                code="weapon_state_conflict", severity="visual_risk",
                message=f"同帧武器「{_weapon_hit}」既标记脱手/没入又标记持握，武器状态矛盾",
                shot_id=shot_id, frame_id=frame_id, entity_id="",
                field_path="prompt/ff_desc/lf_desc", evidence=text[-180:],
                repairable=True,
            ))
        # Holder evidence can appear in another clause of the same frame.
        if "刀尖" in text and not any(
            w in text for w in ("握着", "手持", "持刀", "神秘人", "刺客")
        ):
            issues.append(Issue(
                code="weapon_holder_missing", severity="visual_risk",
                message="同帧出现刀尖但没有明确持刀者",
                shot_id=shot_id, frame_id=frame_id, field_path="prompt/ff_desc",
                evidence=text[-160:], repairable=False,
            ))
    return issues


def _kf_clean_text(text: str) -> str:
    text = str(text or "")
    text = text.split("【角色锚定】", 1)[0]
    text = re.sub(r"（本帧[^）]*）", "", text)
    text = re.sub(r"（画面延续[^）]*）", "", text)
    text = re.sub(r"画面延续[，,。；;]*保持该状态", "", text)
    return re.sub(r"[，。；！？、：:（）()【】\s]", "", text)


def _kf_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    aa = _kf_clean_text(a)
    bb = _kf_clean_text(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def _pose_categories(text: str) -> set[str]:
    cats: set[str] = set()
    if any(w in text for w in ("坐", "跪", "盘膝", "踞蹲", "蹲")):
        cats.add("sit")
    if any(w in text for w in ("仰卧", "躺", "卧倒", "倒地", "倒伏", "平躺")):
        cats.add("lie")
    return cats


def _prop_release_owner_issues(shot: dict, shot_id) -> list[Issue]:
    from shared.content_safety import WEAPON_WORDS

    layouts = shot.get("frame_layouts")
    if not isinstance(layouts, dict):
        return []
    names = _char_names(shot)
    release_words = ("丢", "脱手", "滑落", "掉落", "坠入", "放下", "松开")
    issues: list[Issue] = []
    for frame_id, text in _frame_texts(shot):
        frames = layouts.get(frame_id)
        if frame_id.startswith("kf"):
            try:
                frames = (layouts.get("kfs") or [])[int(frame_id[2:])]
            except Exception:
                frames = []
        for clause in _clauses(text):
            if not any(w in clause for w in release_words):
                continue
            weapon = next((w for w in WEAPON_WORDS if w in clause), "")
            if not weapon:
                continue
            if any(name in clause for name in names):
                continue
            owners = []
            for item in frames or []:
                if not isinstance(item, dict):
                    continue
                props = item.get("props") or []
                if any(weapon in str(p) or str(p) in weapon for p in props):
                    owner = str(item.get("name") or "").strip()
                    if owner and owner not in owners:
                        owners.append(owner)
            if len(owners) > 1:
                issues.append(Issue(
                    code="prop_release_owner_ambiguous", severity="visual_risk",
                    message=f"{'、'.join(owners)} 同持「{weapon}」，但“{clause}”未说明释放者，不能猜测归属",
                    shot_id=shot_id, frame_id=frame_id,
                    field_path=f"frame_layouts.{frame_id}.props",
                    evidence=clause, repairable=False,
                ))
    return issues


def _keyframe_timeline_issues(shot: dict, shot_id) -> list[Issue]:
    """Validate the final keyframe contract as one unit.

    Count alone is not enough: frames must be strictly ordered, no two frames
    may repeat the same event, and each frame layout must describe the same
    character state as its description.
    """
    from shared.kf_time import get_kf_t_sec

    kfs = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
    if not kfs:
        return []
    issues: list[Issue] = []

    timed: list[tuple[int, dict, float | None]] = []
    for i, kf in enumerate(kfs):
        timed.append((i, kf, get_kf_t_sec(kf, shot)))

    # Strict timeline; same/near-same moments are ambiguous for image/video
    # generation and must not be repaired by merely re-spreading identical text.
    prev_t: float | None = None
    prev_i = -1
    for i, _kf, t in timed:
        if t is None:
            issues.append(Issue(
                code="kf_time_missing", severity="visual_risk",
                message=f"第{i}个关键帧缺少可解析的 t_sec/time_ratio",
                shot_id=shot_id, frame_id=f"kf{i}", field_path="keyframes.t_sec",
                repairable=True,
            ))
            continue
        if prev_t is not None:
            if t <= prev_t + 1e-6:
                issues.append(Issue(
                    code="kf_time_not_strict", severity="visual_risk",
                    message=f"关键帧时间未严格递增：kf{prev_i}={prev_t:.3f}s，kf{i}={t:.3f}s",
                    shot_id=shot_id, frame_id=f"kf{prev_i}->kf{i}",
                    field_path="keyframes.t_sec", evidence=f"{prev_t:.3f} -> {t:.3f}",
                    repairable=True,
                ))
            elif t - prev_t < 0.08:
                issues.append(Issue(
                    code="kf_time_too_close", severity="visual_risk",
                    message=f"关键帧间隔过小：kf{prev_i}->kf{i} 仅 {t - prev_t:.3f}s，无法表达两个独立状态",
                    shot_id=shot_id, frame_id=f"kf{prev_i}->kf{i}",
                    field_path="keyframes.t_sec", repairable=True,
                ))
        prev_t, prev_i = t, i

    for i in range(len(timed)):
        ii, kf_i, ti = timed[i]
        for j in range(i + 1, len(timed)):
            jj, kf_j, tj = timed[j]
            if ti is None or tj is None or abs(tj - ti) > 0.30:
                continue
            similarity = _kf_similarity(
                str(kf_i.get("desc") or ""), str(kf_j.get("desc") or "")
            )
            if similarity >= 0.90:
                issues.append(Issue(
                    code="kf_event_duplicate", severity="visual_risk",
                    message=f"kf{ii} 与 kf{jj} 在 {abs(tj - ti):.3f}s 内重复表达同一事件（相似度 {similarity:.0%}）",
                    shot_id=shot_id, frame_id=f"kf{ii}->kf{jj}",
                    field_path="keyframes.desc", repairable=True,
                ))
                break

    layouts = shot.get("frame_layouts")
    layout_kfs = layouts.get("kfs") if isinstance(layouts, dict) else None
    if isinstance(layout_kfs, list) and len(layout_kfs) != len(kfs):
        issues.append(Issue(
            code="kf_layout_count_mismatch", severity="visual_risk",
            message=f"keyframes={len(kfs)} 与 frame_layouts.kfs={len(layout_kfs)} 数量不一致",
            shot_id=shot_id, field_path="frame_layouts.kfs", repairable=True,
        ))

    # Per-frame description/layout pose consistency.
    if isinstance(layout_kfs, list):
        for i, kf in enumerate(kfs):
            desc = str(kf.get("desc") or "")
            if not desc:
                continue
            frame = layout_kfs[i] if i < len(layout_kfs) and isinstance(layout_kfs[i], list) else []
            for item in frame:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or name not in desc:
                    continue
                clauses = [c.strip() for c in re.split(r"[，。；！？、]", desc) if name in c]
                if not clauses:
                    continue
                if any(w in "".join(clauses) for w in ("从", "转为", "随后", "继而", "起身", "坐起", "躺下", "倒下", "翻倒")):
                    continue
                desc_cats = set().union(*(_pose_categories(c) for c in clauses))
                layout_cats = _pose_categories(str(item.get("pose") or ""))
                if ({"sit", "lie"} <= desc_cats) or (
                    "sit" in desc_cats and "lie" in layout_cats
                ) or (
                    "lie" in desc_cats and "sit" in layout_cats
                ):
                    issues.append(Issue(
                        code="kf_desc_layout_pose_conflict", severity="visual_risk",
                        message=f"{name} 的关键帧描述与 frame_layouts.kfs[{i}] 姿态互斥",
                        shot_id=shot_id, frame_id=f"kf{i}", entity_id=name,
                        field_path=f"frame_layouts.kfs[{i}].pose",
                        evidence=f"desc={desc[-120:]} layout_pose={item.get('pose')}",
                        repairable=True,
                    ))

    # Event text must match the motion segment it occupies.  This catches a
    # frame that was only moved by timestamp while its content still belongs to
    # an earlier or later event.
    motion = str(shot.get("motion_desc") or "").strip()
    if motion and len(timed) >= 2:
        try:
            from step01_script_gen.constraints.kf_event_order import _match_score, _segments
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0.0
            segs = _segments(motion, dur)
            scene_prefix = str(shot.get("ff_desc") or "").split("，", 1)[0].strip()
            if scene_prefix:
                scene_prefix += "，"
            if len(segs) >= 2:
                for i, kf, t in timed:
                    if t is None:
                        continue
                    own = None
                    scores = []
                    in_windows: list[int] = []
                    desc_for_match = str(kf.get("desc") or "")
                    if scene_prefix and desc_for_match.startswith(scene_prefix):
                        desc_for_match = desc_for_match[len(scene_prefix):]
                    for si, (a, b, body) in enumerate(segs):
                        score = _match_score(desc_for_match, body)
                        scores.append(score)
                        if a - 0.01 <= t <= b + 0.01:
                            in_windows.append(si)
                    if in_windows:
                        own = min(
                            in_windows,
                            key=lambda si: abs(t - (segs[si][0] + segs[si][1]) / 2.0),
                        )
                    if own is None or not scores:
                        continue
                    best = max(range(len(scores)), key=lambda x: scores[x])
                    if best != own and scores[best] >= 0.45 and scores[best] >= scores[own] + 0.12:
                        issues.append(Issue(
                            code="kf_event_window_mismatch", severity="visual_risk",
                            message=f"kf{i} 位于 {segs[own][0]:g}-{segs[own][1]:g}s，但内容更匹配 {segs[best][0]:g}-{segs[best][1]:g}s 的事件",
                            shot_id=shot_id, frame_id=f"kf{i}", field_path="keyframes.desc",
                            evidence=str(kf.get("desc") or "")[-120:], repairable=True,
                        ))
        except Exception:
            pass

    return issues


def _split_boundary_issues(script: dict) -> list[Issue]:
    """Reject half-shots that replay the previous shot's completed action."""
    from difflib import SequenceMatcher
    from step01_script_gen.state_updates import (
        extract_states, has_role_state_regression,
    )

    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    by_index = {str(s.get("index")): s for s in shots}
    issues: list[Issue] = []

    for shot in shots:
        try:
            idx = float(shot.get("index"))
        except (TypeError, ValueError):
            continue
        if abs(idx - round(idx)) < 1e-9:
            continue
        prev = by_index.get(str(round(idx - 0.5, 4))) or by_index.get(str(int(idx)))
        if not isinstance(prev, dict):
            continue
        prev_lf = str(prev.get("lf_desc") or "").strip()
        motion = str(shot.get("motion_desc") or "").strip()
        if not prev_lf or not motion:
            continue
        prev_clauses = [c.strip() for c in re.split(r"[，。；、]", prev_lf) if c.strip()]
        names = _char_names(shot)

        def _intermediate_change(earlier: list[str], candidate: str) -> bool:
            previous = extract_states(prev_lf, names)
            candidate_states = extract_states(candidate, names)
            earlier_states = [extract_states(body, names) for body in earlier]
            return has_role_state_regression(earlier_states, previous, candidate_states)

        segments = [x.strip() for x in re.split(r"[;；]", motion) if x.strip()]
        earlier_bodies: list[str] = []
        for seg_i, atom in enumerate(segments):
            m = re.match(
                r"(?P<range>\d+(?:\.\d+)?\s*(?:-|~|至|到)\s*\d+(?:\.\d+)?\s*秒?)"
                r"\s*[：:]\s*(?P<body>.*)",
                atom,
            )
            body = m.group("body").strip() if m else ""
            if seg_i == 0:
                if body:
                    earlier_bodies.append(body)
                continue
            if not m or not body:
                continue
            score = max(
                (SequenceMatcher(None, body, c).ratio() for c in prev_clauses),
                default=0.0,
            )
            if score >= 0.62 and not _intermediate_change(earlier_bodies, body):
                issues.append(Issue(
                    code="split_boundary_replay", severity="visual_risk",
                    message=f"镜头{shot.get('index')} {m.group('range')} 重复前半镜尾帧已完成的动作（相似度 {score:.0%}）",
                    shot_id=shot.get("index"),
                    field_path="motion_desc",
                    evidence=body,
                    repairable=True,
                ))
                break
            earlier_bodies.append(body)
    return issues


def _timeline_issues(script: dict) -> list[Issue]:
    if not script.get("timeline"):
        return []
    try:
        from step01_script_gen.timecode import timeline_consistency_errors
        errors = timeline_consistency_errors(script)
    except Exception as exc:
        return [Issue("CHECK_FAILED", "hard", f"时间轴检查异常: {exc}", repairable=False)]
    return [Issue("timeline_consistency", "hard", err,
                  field_path="timeline", repairable=False) for err in errors]


def _duration_budget_issues(script: dict) -> list[Issue]:
    """Make the project's 60-90s acceptance range a blocking invariant.

    The prompt plans for 70-80s and the old redguo checker only emitted
    advisory warnings.  Without this gate, a 100s script could pass while
    still being outside the product target.
    """
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        return []
    durations = []
    for shot in shots:
        try:
            durations.append(float(shot.get("duration_sec") or shot.get("duration") or 0))
        except (TypeError, ValueError):
            return []
    total = sum(durations)
    if 60.0 - 0.01 <= total <= 90.0 + 0.01:
        return []
    if total > 90.0:
        msg = (f"正片总时长 {total:.1f}s > 90s，违反 60-90s 验收区间；"
               "需精简剧情/动作或合并镜头，不能静默截断台词或动作")
    else:
        msg = (f"正片总时长 {total:.1f}s < 60s，低于 60-90s 验收区间；"
               "需补充有效镜头或重新分配时长")
    return [Issue("duration_budget", "visual_risk", msg,
                  field_path="total_duration", repairable=False)]


def _text_integrity_issues(script: dict) -> list[Issue]:
    from shared.utils.text import text_integrity_errors
    issues: list[Issue] = []
    targets = [("hook", script.get("hook"))] if isinstance(script.get("hook"), dict) else []
    targets += [(s.get("index", i + 1), s) for i, s in enumerate(script.get("shots") or [])]
    for shot_id, shot in targets:
        if not isinstance(shot, dict):
            continue
        for field_name in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            text = str(shot.get(field_name) or "")
            for err in text_integrity_errors(text):
                issues.append(Issue(
                    code="text_integrity", severity="visual_risk",
                    message=err, shot_id=shot_id, field_path=field_name,
                    evidence=text[-120:], repairable=False,
                ))
    return issues


def collect_structured_issues(script: dict, blocking_categories: set[str],
                              video_only_categories: set[str],
                              checklist_runner=None) -> tuple[list[Issue], bool, str]:
    """Return (issues, checker_failed, rule_version)."""
    if checklist_runner is None:
        from step01_script_gen.validation import _run_prompt_checklist as checklist_runner
    issues: list[Issue] = []
    checker_failed = False
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            results = checklist_runner(script).get("results", {})
        for category, result in results.items():
            if not isinstance(result, dict):
                continue
            for msg in list(result.get("issues") or []):
                sid, related = parse_shot_ref(str(msg))
                issues.append(Issue(category, "hard", str(msg), sid,
                                    related_shot_ids=related, field_path=category,
                                    repairable=True))
            for msg in list(result.get("warnings") or []):
                sid, related = parse_shot_ref(str(msg))
                if category in video_only_categories:
                    sev = "video_advisory"
                elif category in blocking_categories:
                    sev = "visual_risk"
                else:
                    sev = "advisory"
                issues.append(Issue(category, sev, str(msg), sid,
                                    related_shot_ids=related, field_path=category,
                                    repairable=sev == "visual_risk"))
    except Exception as exc:
        checker_failed = True
        issues.append(Issue("CHECK_FAILED", "hard", f"质量检查器异常: {exc}",
                            repairable=False))
    issues.extend(_text_integrity_issues(script))
    issues.extend(_timeline_issues(script))
    issues.extend(_duration_budget_issues(script))
    issues.extend(_split_boundary_issues(script))
    targets = [("hook", script.get("hook"))] if isinstance(script.get("hook"), dict) else []
    targets += [(s.get("index", i + 1), s) for i, s in enumerate(script.get("shots") or [])]
    for shot_id, shot in targets:
        if not isinstance(shot, dict):
            continue
        issues.extend(_eye_conflicts(shot, shot_id))
        issues.extend(_action_time_issues(shot, shot_id))
        issues.extend(_state_order_issues(shot, shot_id))
        issues.extend(_pose_conflicts(shot, shot_id))
        issues.extend(_surface_conflicts(shot, shot_id))
        issues.extend(_onscreen_prop_issues(shot, shot_id))
        issues.extend(_prop_release_owner_issues(shot, shot_id))
        issues.extend(_keyframe_timeline_issues(shot, shot_id))
    # Stable dedupe.
    seen = set()
    out = []
    for issue in issues:
        key = (issue.code, issue.message, str(issue.shot_id), issue.frame_id, issue.entity_id, issue.field_path)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out, checker_failed, RULE_VERSION


def is_blocking(issue: Issue) -> bool:
    return issue.severity in ("hard", "visual_risk")


__all__ = ["Issue", "RULE_VERSION", "RULE_IMPLEMENTATION", "RULE_SOURCE_FINGERPRINT", "collect_structured_issues", "is_blocking", "parse_shot_ref"]
