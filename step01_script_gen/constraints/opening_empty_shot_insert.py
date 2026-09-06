"""Insert an opening environment shot when the first scene lacks one."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_EMPTY_HINTS = ("空镜", "环境", "远景", "特写", "水波", "树影")


class OpeningEmptyShotInsertConstraint(Constraint):
    """Keep an establishing shot in the opening window and compensate duration."""

    name = "opening_empty_shot_insert"
    severity = Severity.WARN
    fields = ("main_subject",)
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if len(shots) < 3:
            return repairs
        window = shots[:3]
        if not all(isinstance(shot, dict) for shot in window):
            return repairs
        # v6.62（09-06）幂等加固：空镜判定不能只看 main_subject——插入后
        # 后置约束（redguo alias / role_anchor）会把「空镜·水波/树影/风」
        # 改写成「群演」等（实测每轮重插一个空镜，13→14→15 镜）。
        # ff_desc「环境空镜，无人无角色」是模板自身字段，不随 alias 变。
        if any(
            any(hint in str(shot.get("main_subject") or "") for hint in _EMPTY_HINTS)
            or "环境空镜" in str(shot.get("ff_desc") or "")
            or "无人无角色" in str(shot.get("lf_desc") or "")
            for shot in window
        ):
            return repairs

        first = window[0]
        template = {
            "main_subject": "空镜·水波/树影/风",
            "prompt": "中景，固定机位，暖金光位，画面稳定不闪烁。",
            "ff_desc": "环境空镜，无人无角色，暖金阳光斜照。",
            "lf_desc": "环境空镜，无人无角色，仍空无一人。",
            "motion_desc": "全程静景，仅光线微移。",
            "core_keywords": ["空镜", "环境", "氛围"],
            "narration_segment": "",
            "subtitle": "",
            "audio_cue": "环境音",
            "color_tone": "暖金",
            "duration_sec": 2,
            "variation_type": "small",
            "frame_layouts": {"ff": [], "lf": [], "kfs": [[]]},
            # 空镜无关键帧（kfs=[[]] 与 keyframes=[] 数量协议：单 kf spec 占位）
            "keyframes": [],
            "characters": [],
            "scene": copy.deepcopy(first.get("scene")),
        }
        shots.insert(0, template)
        for position, shot in enumerate(shots, 1):
            if isinstance(shot, dict):
                shot["index"] = position

        last = shots[-1]
        if isinstance(last, dict):
            old_duration = float(last.get("duration_sec") or 5.0)
            last["duration_sec"] = max(1.0, old_duration - 2.0)

        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=1,
                action="insert",
                target="shots[0]",
                before="",
                after="环境空镜 + 末镜时长扣减2秒",
            )
        )
        return repairs
