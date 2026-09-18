"""Final deterministic normalizer for production-facing script fields."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from shared.utils.text import text_integrity_errors
from .base import Constraint
from .types import RepairRecord, Severity

_MOVEMENT_PHRASES = (
    "环绕一周", "镜头缓推", "缓慢推近", "镜头推近", "快速跟拍",
    "环绕镜", "跟拍", "拉镜", "推镜", "固定机位", "固定镜头",
)
_BLOOD_WORDS = ("洇痕", "暗红", "血迹", "血痕", "血污", "渗血")
_BLOOD_TRANSITION_WORDS = ("刚被刺", "刺入瞬间", "此刻", "正对", "刺入")


class FinalQualityNormalizerConstraint(Constraint):
    """Normalize final prompt budget, camera movement, and kf windows."""

    name = "final_quality_normalizer"
    severity = Severity.WARN
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            sid = None if is_hook else shot.get("index", idx)
            repairs.extend(self._single_camera(shot, sid))
            repairs.extend(self._trim_prompt_budget(shot, sid))
            repairs.extend(self._align_kf_windows(shot, sid))
            repairs.extend(self._early_blood_transition(shot, sid))
            repairs.extend(self._normalize_weapon_holder(shot, sid))
            repairs.extend(self._trim_ff_desc(shot, sid))
            repairs.extend(self._trim_lf_desc(shot, sid))
            repairs.extend(self._fix_boat_position(shot, sid))
        return repairs

    @staticmethod
    def _single_camera(shot: dict, sid) -> list[RepairRecord]:
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return []
        found = []
        for phrase in _MOVEMENT_PHRASES:
            start = prompt.find(phrase)
            if start >= 0:
                found.append((start, phrase))
        if len(found) < 2:
            return []
        keep_at = max(pos for pos, _ in found)
        new_prompt = prompt
        removed = []
        for pos, phrase in sorted(found, reverse=True):
            if pos == keep_at:
                continue
            new_prompt = new_prompt.replace(phrase, "", 1)
            removed.append(phrase)
        if new_prompt == prompt:
            return []
        import re
        new_prompt = re.sub(r"[，,。]{2,}", "。", new_prompt).strip("，,。 ")
        if new_prompt and not new_prompt.endswith(("。", "！", "？")):
            new_prompt += "。"
        shot["prompt"] = new_prompt
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="rewrite",
            target="prompt",
            before=prompt[:80],
            after=new_prompt[:80],
            detail=f"单镜运镜归一，移除 {removed}",
        )]

    @staticmethod
    def _trim_prompt_budget(shot: dict, sid) -> list[RepairRecord]:
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return []
        ff = shot.get("ff_desc") if isinstance(shot.get("ff_desc"), str) else ""
        lf = shot.get("lf_desc") if isinstance(shot.get("lf_desc"), str) else ""
        narration = shot.get("narration_segment") if isinstance(shot.get("narration_segment"), str) else ""
        fixed_len = len(ff) + len(lf) + len(narration)
        if len(prompt) + fixed_len <= 500:
            return []
        original = prompt
        current = prompt
        removed = []
        while len(current) + fixed_len > 500:
            stripped = current.rstrip("。")
            if "。" not in stripped:
                break
            head, tail = stripped.rsplit("。", 1)
            if not tail.strip():
                break
            removed.append(tail.strip() + "。")
            current = head + "。"
        if current == original:
            return []
        shot["prompt"] = current
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="rewrite",
            target="prompt",
            before=original[-80:],
            after=current[-80:],
            detail=f"prompt 预算裁剪，移除 {len(removed)} 个尾句",
        )]

    @staticmethod
    def _align_kf_windows(shot: dict, sid) -> list[RepairRecord]:
        motion = str(shot.get("motion_desc") or "").strip()
        kfs = shot.get("keyframes") or []
        if not motion or not isinstance(kfs, list) or not kfs:
            return []
        try:
            dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            return []
        from step01_script_gen.validation import _parse_motion_time_segments
        segments = _parse_motion_time_segments(motion, dur)
        if not segments:
            return []
        repairs = []
        for j, kf in enumerate(kfs):
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, shot)
            if t is None:
                continue
            if any(a - 0.01 <= t <= b + 0.01 for a, b in segments):
                continue
            existing = [
                x for k in kfs if isinstance(k, dict)
                for x in [get_kf_t_sec(k, shot)] if x is not None
            ]
            ranked = sorted(segments, key=lambda ab: abs((ab[0] + ab[1]) / 2 - t))
            best = ranked[0]
            for cand in ranked:
                load = sum(1 for x in existing if cand[0] - 0.01 <= x <= cand[1] + 0.01)
                if load < 1:
                    best = cand
                    break
            mid = round((best[0] + best[1]) / 2, 3)
            before = kf.get("t_sec")
            kf["t_sec"] = mid
            kf.pop("time_ratio", None)
            repairs.append(RepairRecord(
                constraint_name="final_quality_normalizer",
                shot_index=sid,
                action="clamp",
                target=f"keyframes[{j}].t_sec",
                before=str(before),
                after=str(mid),
                detail="kf 时间窗回中",
            ))
        return repairs

    @staticmethod
    def _early_blood_transition(shot: dict, sid) -> list[RepairRecord]:
        kfs = shot.get("keyframes") or []
        if not isinstance(kfs, list) or not kfs or not isinstance(kfs[0], dict):
            return []
        ff = str(shot.get("ff_desc") or "")
        kf0 = str(kfs[0].get("desc") or "")
        if not any(w in ff for w in _BLOOD_WORDS):
            if any(w in kf0 for w in _BLOOD_WORDS) and not any(
                w in kf0 for w in _BLOOD_TRANSITION_WORDS
            ):
                before = kf0
                kfs[0]["desc"] = kf0.rstrip("。.，,;；") + "。刺入瞬间，此刻初现血迹。"
                return [RepairRecord(
                    constraint_name="final_quality_normalizer",
                    shot_index=sid,
                    action="append",
                    target="keyframes[0].desc",
                    before=before[-80:],
                    after=kfs[0]["desc"][-80:],
                    detail="补早期血迹过渡",
                )]
        return []

    @staticmethod
    def _normalize_weapon_holder(shot: dict, sid) -> list[RepairRecord]:
        repairs = []
        for field in ("prompt", "ff_desc", "lf_desc"):
            text = shot.get(field)
            if not isinstance(text, str) or ("握刃" not in text and "刀尖" not in text):
                continue
            weapon = "匕首" if "匕首" in text else ("短刃" if "短刃" in text else "刀")
            new_text = text.replace("握刃", f"握着{weapon}")
            new_text = new_text.replace("着匕首的手", "神秘人握着匕首的手")
            new_text = new_text.replace("匕首刀尖抵近", "神秘人握着匕首，刀尖抵近")
            new_text = new_text.replace("匕首刺中", "神秘人握着匕首，刺中")
            if ("握刃" not in text and "刀尖" in new_text
                    and "握着" not in new_text and "神秘人" in new_text):
                # Only repair when the holder already exists in this shot's
                # evidence.  Inventing “画外伸入一只握着刀的手” created plot
                # facts that were absent from the script and source.
                new_text = new_text.replace("刀尖", "神秘人握着刀，刀尖", 1)
            # Collapse stacked holder prefixes produced by older passes:
            # 神秘人握神秘人握着刀 / 神秘人握握着刀 -> 神秘人握着刀
            new_text = new_text.replace("神秘人握神秘人", "神秘人")
            new_text = new_text.replace("神秘人握握着", "神秘人握着")
            if new_text != text:
                shot[field] = new_text
                repairs.append(RepairRecord(
                    constraint_name="final_quality_normalizer",
                    shot_index=sid,
                    action="rewrite",
                    target=field,
                    before=text[-80:],
                    after=new_text[-80:],
                    detail="武器 holder 归一",
                ))
        for j, kf in enumerate(shot.get("keyframes") or []):
            if not isinstance(kf, dict):
                continue
            text = kf.get("desc")
            if not isinstance(text, str) or ("握刃" not in text and "刀尖" not in text):
                continue
            weapon = "匕首" if "匕首" in text else ("短刃" if "短刃" in text else "刀")
            new_text = text.replace("握刃", f"握着{weapon}")
            new_text = new_text.replace("着匕首的手", "神秘人握着匕首的手")
            new_text = new_text.replace("匕首刀尖抵近", "神秘人握着匕首，刀尖抵近")
            new_text = new_text.replace("匕首刺中", "神秘人握着匕首，刺中")
            if ("握刃" not in text and "刀尖" in new_text
                    and "握着" not in new_text and "神秘人" in new_text):
                # Only repair when the holder already exists in this shot's
                # evidence.  Inventing “画外伸入一只握着刀的手” created plot
                # facts that were absent from the script and source.
                new_text = new_text.replace("刀尖", "神秘人握着刀，刀尖", 1)
            # Collapse stacked holder prefixes produced by older passes:
            # 神秘人握神秘人握着刀 / 神秘人握握着刀 -> 神秘人握着刀
            new_text = new_text.replace("神秘人握神秘人", "神秘人")
            new_text = new_text.replace("神秘人握握着", "神秘人握着")
            if new_text != text:
                kf["desc"] = new_text
                repairs.append(RepairRecord(
                    constraint_name="final_quality_normalizer",
                    shot_index=sid,
                    action="rewrite",
                    target=f"keyframes[{j}].desc",
                    before=text[-80:],
                    after=new_text[-80:],
                    detail="武器 holder 归一",
                ))
        return repairs

    @staticmethod
    def _trim_ff_desc(shot: dict, sid) -> list[RepairRecord]:
        text = shot.get("ff_desc")
        if not isinstance(text, str) or len(text) <= 120:
            return []
        original = text
        current = text
        while len(current) > 120 and "。" in current:
            stripped = current.rstrip("。")
            if "。" not in stripped:
                break
            head, tail = stripped.rsplit("。", 1)
            if not tail.strip():
                break
            candidate = head + "。"
            # Never hard-cut at an arbitrary character. A broken parenthesis
            # or dangling size token is worse than an over-budget description:
            # the final quality gate can reject the latter, while downstream
            # image/video prompts cannot reliably recover from the former.
            if text_integrity_errors(candidate):
                break
            current = candidate
        if current == original:
            return []
        shot["ff_desc"] = current
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="rewrite",
            target="ff_desc",
            before=original[-80:],
            after=current[-80:],
            detail="ff_desc 长度裁剪",
        )]

    @staticmethod
    def _fix_boat_position(shot: dict, sid) -> list[RepairRecord]:
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return []
        probe = prompt + str(shot.get("ff_desc") or "")
        boat_words = ("船", "舟", "筏", "水面", "湖面", "河面", "荷塘", "池塘")
        ambiguous = ("跳", "蹦", "跃", "冲出", "穿过", "跨越", "腾空", "飞起", "荡", "摇", "晃")
        positions = ("端坐", "稳坐", "盘膝", "位于", "不动", "固定于", "静止于")
        if not any(w in probe for w in boat_words):
            return []
        if not any(w in probe for w in ambiguous):
            return []
        if any(w in probe for w in positions):
            return []
        new_prompt = prompt.rstrip("。") + "。人物稳定位于船板，身体不动。"
        shot["prompt"] = new_prompt
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="append",
            target="prompt",
            before=prompt[-80:],
            after=new_prompt[-80:],
            detail="船只承重面位置补锚",
        )]

    @staticmethod
    def _trim_lf_desc(shot: dict, sid) -> list[RepairRecord]:
        text = shot.get("lf_desc")
        if not isinstance(text, str) or len(text) <= 120:
            return []
        original = text
        current = text
        while len(current) > 120 and "。" in current:
            stripped = current.rstrip("。")
            if "。" not in stripped:
                break
            head, tail = stripped.rsplit("。", 1)
            if not tail.strip():
                break
            candidate = head + "。"
            if text_integrity_errors(candidate):
                break
            current = candidate
        if current == original:
            return []
        shot["lf_desc"] = current
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="rewrite",
            target="lf_desc",
            before=original[-80:],
            after=current[-80:],
            detail="lf_desc 长度裁剪",
        )]
