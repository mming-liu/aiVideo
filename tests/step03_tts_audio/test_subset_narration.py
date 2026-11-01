# -*- coding: utf-8 -*-
"""step03 --shots：旁白子集裁剪（S18-2 缺口）.

全章旁白不能在子集运行里被"当成没有落点"而报错；也不能跨镜截断。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _plan(beats, narrations):
    return {"shot_beats": beats,
            "dialogue_lines": [],
            "narration_lines": narrations}


def _shots():
    return [
        {"index": 1, "narration_segment": "甲", "duration_sec": 4.0,
         "plan_refs": {"beats": [1], "lines": []}},
        {"index": 2, "narration_segment": "乙", "duration_sec": 4.0,
         "plan_refs": {"beats": [2], "lines": []}},
    ]


def test_subset_keeps_only_narrations_fully_inside_selected_shots():
    from step03_tts_audio.shot_tts import narration_subset_entries

    plan = _plan(
        ["节拍：甲｜本镜信息目的：推进｜原文依据：“甲”｜载体：旁白1",
         "节拍：乙｜本镜信息目的：推进｜原文依据：“乙”｜载体：旁白2"],
        [{"text": "甲", "source": "甲", "fact_refs": []},
         {"text": "乙", "source": "乙", "fact_refs": []}])
    kept = narration_subset_entries(plan, _shots(), {1})
    assert [i for i, _e in kept] == [1], "他镜旁白没有被裁掉"


def test_cross_shot_narration_is_skipped_not_truncated():
    from step03_tts_audio.shot_tts import narration_subset_entries

    plan = _plan(
        ["节拍：甲乙｜本镜信息目的：推进｜原文依据：“甲乙”｜载体：旁白1"],
        [{"text": "甲乙", "source": "甲乙", "fact_refs": []}])
    shots = _shots()
    # 同一条旁白由两个镜头的节拍共同承载
    shots[0]["plan_refs"] = {"beats": [1], "lines": []}
    shots[1]["plan_refs"] = {"beats": [1], "lines": []}
    kept = narration_subset_entries(plan, shots, {1})
    assert kept == [], "跨镜旁白被截断进了子集"


def test_full_run_keeps_every_narration():
    from step03_tts_audio.shot_tts import narration_subset_entries

    plan = _plan(
        ["节拍：甲｜本镜信息目的：推进｜原文依据：“甲”｜载体：旁白1",
         "节拍：乙｜本镜信息目的：推进｜原文依据：“乙”｜载体：旁白2"],
        [{"text": "甲", "source": "甲", "fact_refs": []},
         {"text": "乙", "source": "乙", "fact_refs": []}])
    kept = narration_subset_entries(plan, _shots(), None)
    assert [i for i, _e in kept] == [1, 2]
