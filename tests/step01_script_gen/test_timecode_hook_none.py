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
