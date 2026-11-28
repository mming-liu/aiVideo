"""_compute_timecodes 对 hook=None 的回归测试."""
from __future__ import annotations


def test_compute_timecodes_allows_null_hook():
    import step01_script_gen.timecode as timecode

    class _Logger:
        def info(self, message):
            pass

        def warn(self, message):
            pass

    timecode._log = lambda: _Logger()

    script = {
        "shots": [{"index": 1, "narration_segment": "", "motion_desc": ""}],
        "hook": None,
    }
    timecode._compute_timecodes(script)
    assert script["hook"] is None
    assert script["shots"][0]["duration"] >= 3.0


def test_final_timeline_rebuild_uses_duration_sec_as_single_source():
    from step01_script_gen.timecode import (
        timeline_consistency_errors, write_timeline_from_durations,
    )

    script = {
        "hook": {"duration_sec": 3.0, "duration": 2.0},
        "shots": [
            {"index": 1, "duration_sec": 4.0, "duration": 9.0,
             "variation_type": "small"},
            {"index": 2, "duration_sec": 5.5, "duration": 1.0},
        ],
        "timeline": [
            {"index": 0, "start": 0, "end": 2, "duration": 2},
            {"index": 1, "start": 2, "end": 9, "duration": 7},
            {"index": 2, "start": 9, "end": 11, "duration": 2},
        ],
        "total_duration": 11.0,
        "total_effective_duration": 11.0,
    }
    write_timeline_from_durations(script)
    assert script["total_duration"] == 12.5
    assert script["timeline"][-1]["end"] == 12.5
    assert script["shots"][0]["timecode"] == {"start": 3.0, "end": 7.0}
    assert script["shots"][1]["timecode"] == {"start": 7.0, "end": 12.5}
    assert timeline_consistency_errors(script) == []
