"""B.4 OvershoulderConsistencyConstraint 单元测试（3 用例）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_inserts_overshoulder_in_middle_shot():
    """3 镜同景同 cam 同对白 -> 中间镜改过肩."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="杨旭质问", narration_segment="你为何背叛", subtitle="你为何背叛",
                  location="小舟", camera_movement="固定镜头"),
        make_shot(index=2, main_subject="刺客答", narration_segment="我是被迫", subtitle="我是被迫",
                  location="小舟", camera_movement="固定镜头"),
        make_shot(index=3, main_subject="杨旭追问", narration_segment="为何不说", subtitle="为何不说",
                  location="小舟", camera_movement="固定镜头"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import OvershoulderConsistencyConstraint
    repairs = OvershoulderConsistencyConstraint().apply(ctx)
    assert len(repairs) == 1
    mid = script["shots"][1]
    assert "过肩" in mid["camera_movement"]


def test_skips_when_locations_differ():
    """3 镜 location 不同 -> 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="杨旭质问", narration_segment="a", subtitle="a",
                  location="小舟", camera_movement="固定"),
        make_shot(index=2, main_subject="刺客答", narration_segment="b", subtitle="b",
                  location="岸边", camera_movement="固定"),
        make_shot(index=3, main_subject="杨旭追问", narration_segment="c", subtitle="c",
                  location="船舱", camera_movement="固定"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import OvershoulderConsistencyConstraint
    repairs = OvershoulderConsistencyConstraint().apply(ctx)
    assert repairs == []


def test_skips_when_camera_movements_differ():
    """3 镜 camera_movement 不同 -> 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="杨旭质问", narration_segment="a", subtitle="a",
                  location="小舟", camera_movement="固定"),
        make_shot(index=2, main_subject="刺客答", narration_segment="b", subtitle="b",
                  location="小舟", camera_movement="推镜"),
        make_shot(index=3, main_subject="杨旭追问", narration_segment="c", subtitle="c",
                  location="小舟", camera_movement="拉镜"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import OvershoulderConsistencyConstraint
    repairs = OvershoulderConsistencyConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.doubao_hardfix import OvershoulderConsistencyConstraint
    from step01_script_gen.constraints.types import Severity
    c = OvershoulderConsistencyConstraint()
    assert c.name == "doubao_overshoulder_consistency"
    assert c.severity == Severity.WARN