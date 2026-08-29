"""_check_redguo_standard 退化时长保护（v6.47，08-27）单元测试.

LLM 常漏写 duration（兜底 1s），parse 阶段未跑 _compute_timecodes，此时
「红果总时长 Ns」WARN 是误导性（实际会按 motion_desc/对白/4s 旁白重算）。
总时长明显退化（均镜 < 2.5s 且 ≥6 镜）→ 跳过全部时长类子检查。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _check_redguo_standard


def _make_shots(durations):
    return [{"index": i + 1, "duration": d} for i, d in enumerate(durations)]


def test_degenerate_durations_skip_duration_warns():
    """LLM 漏写时长（16 镜 × 1s）→ 不报「红果总时长 16s」误导 WARN."""
    shots = _make_shots([1.0] * 16)
    result = _check_redguo_standard(shots)

    warns = " ".join(result["warnings"])
    assert "红果总时长" not in warns
    assert "末镜" not in warns


def test_sane_durations_still_report():
    """正常时长（8 镜 × 5s = 40s < 60s）→ 仍报总时长不足 WARN."""
    shots = _make_shots([5.0] * 8)
    result = _check_redguo_standard(shots)

    warns = " ".join(result["warnings"])
    assert "红果总时长" in warns


def test_few_shots_short_not_skipped():
    """镜头少（5 镜 × 1s）不触发退化保护（< 6 镜）——保持原检查."""
    shots = _make_shots([1.0] * 5)
    result = _check_redguo_standard(shots)

    warns = " ".join(result["warnings"])
    assert "红果总时长" in warns
