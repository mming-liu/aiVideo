"""F9. ShortShotMergeConstraint 单元测试（v6.49, 08-28）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_short_shot_stretched_to_min():
    """duration < 3.5s → 拉到 3.5s."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="x"),
        make_shot(index=2, duration_sec=3.0, prompt="y"),  # 短镜
        make_shot(index=3, duration_sec=5.0, prompt="z"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.short_shot_merge import ShortShotMergeConstraint

    repairs = ShortShotMergeConstraint().apply(ctx)

    assert len(repairs) == 1
    assert script["shots"][1]["duration_sec"] == 3.5


def test_no_short_shot_no_repair():
    """全部 ≥ 3.5s → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=4.0, prompt="x"),
        make_shot(index=2, duration_sec=4.5, prompt="y"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.short_shot_merge import ShortShotMergeConstraint

    repairs = ShortShotMergeConstraint().apply(ctx)

    assert len(repairs) == 0


def test_last_shot_not_stretched_by_min():
    """末镜已由 W6 强制 ≥ 4s；本约束不动末镜."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="x"),
        make_shot(index=2, duration_sec=3.0, prompt="y"),  # 末镜 3.0s
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.short_shot_merge import ShortShotMergeConstraint

    repairs = ShortShotMergeConstraint().apply(ctx)

    # 末镜跳过了，但 shots 只有 2 个，最后 1 镜就是末镜 → 跳过
    assert len(repairs) == 0


def test_constraint_attributes():
    from step01_script_gen.constraints.short_shot_merge import ShortShotMergeConstraint
    from step01_script_gen.constraints.types import Severity
    c = ShortShotMergeConstraint()
    assert c.name == "short_shot_merge"
    assert c.severity == Severity.HARD
