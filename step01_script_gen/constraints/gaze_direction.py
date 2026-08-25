"""B. 视线方向约束（v6.42 迁移自 _hard_fix_gaze_direction）.

「看/望+人名/对象」但没带空间方位词时，按规则改写为「画X」或「目光紧盯X」.

幂等：替换前检测，字段已含「目光/凝望/仰望/俯视」时直接跳过该字段.
（v6.32 修复：Python `re` `(?<!目光)` 多字符负向预查定长回溯导致反复累积）.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import (
    _GAZE_FALLBACK_RULES, _build_char_gaze_pattern, _build_char_gaze_loose_pattern,
)
from step01_script_gen.prompt_semantic import COMMON_PERSON_NAMES
from .base import Constraint
from .types import RepairRecord, Severity


class GazeDirectionConstraint(Constraint):
    """B. 视线方向：「看/望+人名」→ 「画X」或「目光紧盯X」.

    severity=WARN: 视线方向可由后续 repair round 修.
    repair_marker="目光": 已含「目光/凝望/仰望/俯视」任一视为已修.

    v6.43 重构：角色名优先从 char_layout 抽，fallback 用
    prompt_semantic.COMMON_PERSON_NAMES（不再写死 11 个名字到本文件）.
    """

    name = "gaze_direction"
    severity = Severity.WARN
    repair_marker = "目光"
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def _collect_char_names(self, shot: dict) -> tuple[str, ...]:
        """从 shot.char_layout 抽角色名（去重保序）.

        无 char_layout → fallback 到 prompt_semantic.COMMON_PERSON_NAMES.
        """
        layout = shot.get("char_layout") or []
        names: list[str] = []
        for item in layout:
            if not isinstance(item, dict):
                continue
            n = str(item.get("name", "")).strip()
            if n and n not in names:
                names.append(n)
        if not names:
            return COMMON_PERSON_NAMES
        return tuple(names)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            char_names = self._collect_char_names(shot)
            char_pat = _build_char_gaze_pattern(char_names)
            char_pat_loose = _build_char_gaze_loose_pattern(char_names)
            for fld in self.fields:
                v = shot.get(fld)
                if not isinstance(v, str) or not v:
                    continue
                # 替换前已含合法空间词 → 视为已修（幂等）
                if any(w in v for w in ("目光", "凝望", "仰望", "俯视")):
                    continue
                new_v = v
                # 1) 通用 fallback rules（看天/看水/看她/他等）
                for pat, repl in _GAZE_FALLBACK_RULES:
                    if pat.search(new_v):
                        new_v = pat.sub(repl, new_v)
                # 2) char_layout 角色名 regex（动态）
                if char_pat.search(new_v):
                    new_v = char_pat.sub(r"目光紧盯\1", new_v)
                if char_pat_loose.search(new_v):
                    new_v = char_pat_loose.sub(r"目光凝视\1", new_v)
                if new_v != v:
                    shot[fld] = new_v
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=fld,
                            before=v,
                            after=new_v,
                        )
                    )
        return repairs
