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

RULE_VERSION = "2026-09-18.1"


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
        for clause in _clauses(text):
            has_open = any(w in clause for w in open_words)
            has_closed = any(w in clause for w in closed_words)
            if not (has_open and has_closed):
                continue
            matched = [n for n in names if n in clause]
            if len(matched) == 1:
                issues.append(Issue(
                    code="eye_state_conflict", severity="visual_risk",
                    message=f"角色{matched[0]}同句同时出现睁眼/闭眼",
                    shot_id=shot_id, entity_id=matched[0], field_path=field_name,
                    evidence=clause, repairable=True,
                ))
            elif not matched:
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
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(\d+(?:\.\d+)?)\s*秒", motion):
        try:
            ends.append(float(m.group(2)))
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
    frames = _frame_texts(shot)
    gone_seen = False
    for frame_id, text in frames:
        if any(w in text for w in ("已没入水中", "已退场", "已消失", "已离开")):
            gone_seen = True
            continue
        if gone_seen and any(w in text for w in ("跃出", "跃起", "冲出", "登船", "跃上", "窜出")):
            return [Issue(
                code="state_order_replay", severity="visual_risk",
                message="角色已退场/入水后，后续帧又重复执行入场动作",
                shot_id=shot_id, frame_id=frame_id, field_path=frame_id,
                evidence=text, repairable=True,
            )]
    return []


def _pose_conflicts(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    layouts = shot.get("frame_layouts") or {}
    if not isinstance(layouts, dict):
        return issues
    sitting = ("坐", "跪", "盘膝", "踞蹲", "蹲")
    lying = ("仰卧", "躺", "卧", "倒") 
    for frame_key in ("ff", "lf"):
        for item in layouts.get(frame_key) or []:
            if not isinstance(item, dict):
                continue
            pose = str(item.get("pose") or "")
            if any(s in pose for s in sitting) and any(l in pose for l in lying):
                issues.append(Issue(
                    code="pose_conflict", severity="visual_risk",
                    message=f"{item.get('name','角色')}同帧姿态同时包含坐/跪和躺卧",
                    shot_id=shot_id, frame_id=frame_key,
                    entity_id=str(item.get("name") or ""), field_path=f"frame_layouts.{frame_key}.pose",
                    evidence=pose, repairable=True,
                ))
    return issues


def _surface_conflicts(shot: dict, shot_id) -> list[Issue]:
    surfaces = ("车厢内", "院内地面", "船板", "船头", "船尾", "马背", "轿内", "厅内地面")
    text = " ".join(str(shot.get(k) or "") for k in ("ff_desc", "lf_desc", "motion_desc"))
    found = [s for s in surfaces if s in text]
    if len(set(found)) >= 2:
        # Only flag mutually exclusive vehicle/ground pairs, not broad scene words.
        if ("车厢内" in found and any(s.endswith("地面") for s in found)) or ("马背" in found and "院内地面" in found):
            return [Issue(
                code="surface_conflict", severity="visual_risk",
                message=f"同一镜头出现互斥承载面: {found}",
                shot_id=shot_id, field_path="ff_desc/lf_desc", evidence=text,
                repairable=True,
            )]
    return []


def _onscreen_prop_issues(shot: dict, shot_id) -> list[Issue]:
    layout = shot.get("frame_layouts")
    if not isinstance(layout, dict):
        return []
    text = " ".join(str(shot.get(k) or "") for k in ("ff_desc", "lf_desc", "motion_desc"))
    issues = []
    for item in (layout.get("ff") or []):
        if not isinstance(item, dict) or item.get("on_screen") is not False:
            continue
        name = str(item.get("name") or "")
        if name and name in text and any(w in text for w in ("攻击", "持刀", "持匕首", "刺出", "劈砍")):
            issues.append(Issue(
                code="offscreen_character_action", severity="visual_risk",
                message=f"{name}标记离场但文本仍要求其执行动作",
                shot_id=shot_id, frame_id="ff", entity_id=name,
                field_path="frame_layouts.ff.on_screen", evidence=text,
                repairable=True,
            ))
    if "刀尖" in text and not any(w in text for w in ("握着", "手持", "持刀", "神秘人", "刺客")):
        issues.append(Issue(
            code="weapon_holder_missing", severity="visual_risk",
            message="出现刀尖但镜头内没有明确持刀者",
            shot_id=shot_id, field_path="prompt/ff_desc", evidence=text,
            repairable=False,
        ))
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


__all__ = ["Issue", "RULE_VERSION", "collect_structured_issues", "is_blocking", "parse_shot_ref"]
