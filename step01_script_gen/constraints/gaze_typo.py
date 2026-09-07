"""F14. 目光错别字归一（v6.62.1，09-06）.

09-06 hook 实测：motion_desc 落盘就带着错别字「目光倒倒盯住听香」——修正
此前只存在于 shared/vimax_bridge.fix_lf_desc_gaze（step02b 组装时才修），
step01 的 script_result.json / step02a 读到的都是错字。LLM 叠字错写
（倒倒/倒到）是系统性输出噪声，应在 step01 层就归一。

修法：错别字映射表从 bridge 单源 import（_LF_GAZE_TYPO），扫
motion_desc/ff_desc/lf_desc/prompt + keyframes[*].desc 替换。只做**错字
归一**（倒倒→紧紧），不做 bridge 的「目光语义改写」（那需要 ff/lf 上下文，
属 step02a/02b 组装期职责）。

幂等：替换后不再含错字形态。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

_FIELDS = ("prompt", "ff_desc", "lf_desc", "motion_desc")


def _typo_table() -> tuple[tuple[str, str], ...]:
    """错别字表单源 shared.vimax_bridge._LF_GAZE_TYPO（import 失败用内置兜底）."""
    try:
        from shared.vimax_bridge import _LF_GAZE_TYPO
        return tuple(_LF_GAZE_TYPO)
    except Exception:
        return (("倒倒", "紧紧"), ("倒到", "紧紧"), ("望望", "望着"), ("看看", "看着"))


class GazeTypoConstraint(Constraint):
    """F14. 目光叠字错别字归一（motion/ff/lf/prompt/kf.desc）."""

    name = "gaze_typo"
    severity = Severity.WARN
    fields = _FIELDS

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        table = _typo_table()
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            for field in _FIELDS:
                text = shot.get(field)
                if not isinstance(text, str) or not text:
                    continue
                fixed = text
                for old, new in table:
                    fixed = fixed.replace(old, new)
                if fixed != text:
                    shot[field] = fixed
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="rewrite",
                            target=field,
                            before=text[:60],
                            after=fixed[:60],
                        )
                    )
            for _i, _kf in enumerate(shot.get("keyframes") or []):
                if not isinstance(_kf, dict):
                    continue
                _text = _kf.get("desc")
                if not isinstance(_text, str) or not _text:
                    continue
                _fixed = _text
                for old, new in table:
                    _fixed = _fixed.replace(old, new)
                if _fixed != _text:
                    _kf["desc"] = _fixed
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="rewrite",
                            target=f"keyframes[{_i}].desc",
                            before=_text[:60],
                            after=_fixed[:60],
                        )
                    )
        return repairs
