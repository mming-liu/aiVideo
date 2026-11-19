# -*- coding: utf-8 -*-
"""R7.1：voice_spans 跨镜延续的权威绑定与拒收（2026-11-19）."""
from __future__ import annotations

import pytest

from step01_script_gen.plan_feasibility import voice_units


def _script(spans):
    shots = [
        {"index": 1, "narration_segment": "", "plan_refs": {"beats": [1], "lines": []}},
        {"index": 2, "narration_segment": "", "plan_refs": {"beats": [2], "lines": []}},
        {"index": 3, "narration_segment": "旁白：若非如此，前几日往泰州府去时",
         "plan_refs": {"beats": [3], "lines": []}},
        {"index": 4, "narration_segment": "旁白：杨文轩又参加府学",
         "plan_refs": {"beats": [4], "lines": []}},
    ]
    plan = {
        "shot_beats": [
            "节拍：A｜载体：画面",
            "节拍：B｜载体：画面",
            "节拍：C｜载体：旁白1",
            "节拍：D｜载体：旁白2",
        ],
        "narration_lines": [
            {"text": "若非如此，前几日往泰州府去时", "source": "", "fact_refs": []},
            {"text": "杨文轩又参加府学", "source": "", "fact_refs": []},
        ],
        "voice_spans": spans,
    }
    return {"shots": shots, "chapter_outline": plan}


def test_cross_shot_span_is_authoritative():
    recs = voice_units(_script({"N1": ["3", "4"]})["chapter_outline"],
                       _script({})["shots"])
    n1 = next(r for r in recs if r["id"] == "N1")
    assert n1["shot_indexes"] == ["3", "4"]


def test_non_contiguous_span_is_rejected():
    with pytest.raises(ValueError):
        voice_units(_script({"N1": ["3", "5"]})["chapter_outline"],
                    _script({})["shots"])


def test_first_shot_must_register_full_text():
    with pytest.raises(ValueError):
        voice_units(_script({"N1": ["4", "3"]})["chapter_outline"],
                    _script({})["shots"])
