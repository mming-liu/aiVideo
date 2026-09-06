"""B.5 FrameSpecReconstructConstraint 单元测试（3 用例）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fills_missing_pose_and_on_screen():
    """frame_layouts ff/lf dict 缺 pose/on_screen -> 兜底填."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭",
            keyframes=[],
            frame_layouts={"ff": [{"name": "杨旭"}], "lf": [{"name": "杨旭"}], "kfs": []},
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import FrameSpecReconstructConstraint
    repairs = FrameSpecReconstructConstraint().apply(ctx)
    assert len(repairs) >= 2
    fl = script["shots"][0]["frame_layouts"]
    assert fl["ff"][0]["pose"] == "杨旭"
    assert fl["ff"][0]["on_screen"] is True
    assert fl["lf"][0]["pose"] == "杨旭"
    assert fl["lf"][0]["on_screen"] is True


def test_aligns_kfs_length_to_keyframes():
    """keyframes 3 帧, kfs 1 帧 -> 补齐到 3."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭",
            keyframes=[{"t_sec": 1}, {"t_sec": 2}, {"t_sec": 3}],
            frame_layouts={"ff": [{"name": "杨旭", "pose": "立", "on_screen": True}],
                           "lf": [{"name": "杨旭", "pose": "立", "on_screen": True}],
                           "kfs": [[{"name": "杨旭", "pose": "立", "on_screen": True}]]},
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import FrameSpecReconstructConstraint
    repairs = FrameSpecReconstructConstraint().apply(ctx)
    assert len(repairs) >= 2
    fl = script["shots"][0]["frame_layouts"]
    assert len(fl["kfs"]) == 3


def test_idempotent_after_fill():
    """连跑 2 次 -> 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭",
            keyframes=[{"t_sec": 1}],
            frame_layouts={"ff": [{"name": "杨旭"}], "lf": [], "kfs": []},
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import FrameSpecReconstructConstraint
    c = FrameSpecReconstructConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    assert len(r1) >= 1
    assert len(r2) == 0


def test_constraint_attributes():
    from step01_script_gen.doubao_hardfix import FrameSpecReconstructConstraint
    from step01_script_gen.constraints.types import Severity
    c = FrameSpecReconstructConstraint()
    assert c.name == "doubao_frame_spec_reconstruct"
    assert c.severity == Severity.WARN