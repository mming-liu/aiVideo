"""Final deterministic normalizer for production-facing script fields."""
from __future__ import annotations

import re

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
        if len(prompt) + len(ff) + len(lf) + len(narration) <= 500:
            return []
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="warn",
            target="prompt",
            before=prompt[-120:],
            after=prompt[-120:],
            detail="prompt/描述总量超过预算；保留原文，禁止自动删除尾句",
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
        warnings = []
        for j, kf in enumerate(kfs):
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, shot)
            if t is None:
                continue
            if any(a < t < b for a, b in segments):
                continue
            warnings.append(RepairRecord(
                constraint_name="final_quality_normalizer",
                shot_index=sid,
                action="warn",
                target=f"keyframes[{j}].t_sec",
                before=str(t),
                after=str(t),
                detail=(
                    "关键帧时间不在任何 motion 严格区间内；保留原时间，"
                    "禁止为通过时间窗检查自动搬到区间中点。"
                ),
            ))
        return warnings

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
                return [RepairRecord(
                    constraint_name="final_quality_normalizer",
                    shot_index=sid,
                    action="warn",
                    target="keyframes[0].desc",
                    before=kf0[-80:],
                    after=kf0[-80:],
                    detail=(
                        "尾帧/关键帧出现血迹但首帧无伤情过渡；"
                        "保留原文，未自动追加“刺入瞬间，此刻初现血迹”。"
                    ),
                )]
        return []

    @staticmethod
    def _normalize_weapon_holder(shot: dict, sid) -> list[RepairRecord]:
        """Normalize only explicit, generic holder syntax.

        Earlier versions hard-coded “神秘人” and turned any standalone “刀尖”
        into a held weapon. That invented a holder whenever the description
        merely mentioned a weapon tip (for example after the weapon had been
        released). Semantic holder changes remain in the evidence-bound weapon
        constraint/audit layer; this normalizer only removes exact duplicated
        syntax and expands the lexical phrase “握刃”.
        """
        repairs = []

        def _normalize(text: str) -> str:
            weapon = "匕首" if "匕首" in text else ("短刃" if "短刃" in text else "刀")
            out = text.replace("握刃", f"握着{weapon}")
            # Collapse only an exact adjacent repetition: “甲握甲握着刀”.
            out = re.sub(r"([\u4e00-\u9fff]{2,4})握\1握", r"\1握", out)
            out = re.sub(r"([\u4e00-\u9fff]{2,4})握握着", r"\1握着", out)
            return out

        for field in ("prompt", "ff_desc", "lf_desc"):
            text = shot.get(field)
            if not isinstance(text, str) or not text:
                continue
            new_text = _normalize(text)
            if new_text != text:
                shot[field] = new_text
                repairs.append(RepairRecord(
                    constraint_name="final_quality_normalizer",
                    shot_index=sid,
                    action="rewrite",
                    target=field,
                    before=text[-80:],
                    after=new_text[-80:],
                    detail="武器 holder 语法归一（不推断持有者）",
                ))
        for j, kf in enumerate(shot.get("keyframes") or []):
            if not isinstance(kf, dict):
                continue
            text = kf.get("desc")
            if not isinstance(text, str) or not text:
                continue
            new_text = _normalize(text)
            if new_text != text:
                kf["desc"] = new_text
                repairs.append(RepairRecord(
                    constraint_name="final_quality_normalizer",
                    shot_index=sid,
                    action="rewrite",
                    target=f"keyframes[{j}].desc",
                    before=text[-80:],
                    after=new_text[-80:],
                    detail="武器 holder 语法归一（不推断持有者）",
                ))
        return repairs

    @staticmethod
    def _trim_ff_desc(shot: dict, sid) -> list[RepairRecord]:
        text = shot.get("ff_desc")
        if not isinstance(text, str) or len(text) <= 120:
            return []
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="warn",
            target="ff_desc",
            before=text[-80:],
            after=text[-80:],
            detail="ff_desc 超长；保留原文，禁止自动删除尾句",
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
        # A moving/airborne action does not prove that the subject is stably
        # standing on the boat deck.  The old fallback appended exactly that
        # claim and contradicted jumps/splashes.  Report the ambiguity instead.
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="warn",
            target="prompt",
            detail=(
                "船只/水面镜头含位移或腾空动作，但缺少明确的承重面或稳定状态；"
                "保留原文，交 audit 判断，禁止自动追加“人物稳定位于船板”。"
            ),
        )]

    @staticmethod
    def _trim_lf_desc(shot: dict, sid) -> list[RepairRecord]:
        text = shot.get("lf_desc")
        if not isinstance(text, str) or len(text) <= 120:
            return []
        return [RepairRecord(
            constraint_name="final_quality_normalizer",
            shot_index=sid,
            action="warn",
            target="lf_desc",
            before=text[-80:],
            after=text[-80:],
            detail="lf_desc 超长；保留原文，禁止自动删除尾句",
        )]
