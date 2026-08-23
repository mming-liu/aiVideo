"""I2. FramingDiversityV2Constraint 单元测试.

测试 3 类场景：
  1. 修复：连续 3+ 镜同景别 → 中间一镜替换
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：< 3 镜同景别 / 跨不同景别 / 全部景别已异 → 不改
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_replaces_mid_shot_in_3_consecutive_same_framing():
    """3+ 镜同景别 → 中间一镜替换景别."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 镜头1"),
        make_shot(index=2, prompt="[中景] 镜头2"),
        make_shot(index=3, prompt="[中景] 镜头3"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_diversity_v2 import FramingDiversityV2Constraint

    repairs = FramingDiversityV2Constraint().apply(ctx)

    assert len(repairs) >= 1
    # shot 2 (mid) 应被替换景别
    assert "[中景]" not in script["shots"][1]["prompt"]


def test_keeps_only_diverse_unchanged():
    """3+ 镜中只有 1 段连续同景别 → 修该段，其他不动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 镜头1"),
        make_shot(index=2, prompt="[远景] 镜头2"),
        make_shot(index=3, prompt="[远景] 镜头3"),
        make_shot(index=4, prompt="[特写] 镜头4"),  # 不同景别
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_diversity_v2 import FramingDiversityV2Constraint

    repairs = FramingDiversityV2Constraint().apply(ctx)

    assert len(repairs) >= 1
    # shot 4 不应被改
    assert "[特写]" in script["shots"][3]["prompt"]


def test_is_idempotent():
    """连跑 2 次 = 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 镜头1"),
        make_shot(index=2, prompt="[中景] 镜头2"),
        make_shot(index=3, prompt="[中景] 镜头3"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_diversity_v2 import FramingDiversityV2Constraint

    c = FramingDiversityV2Constraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0


def test_skips_already_diverse():
    """全片景别已不同 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 镜头1"),
        make_shot(index=2, prompt="[中景] 镜头2"),
        make_shot(index=3, prompt="[特写] 镜头3"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_diversity_v2 import FramingDiversityV2Constraint

    repairs = FramingDiversityV2Constraint().apply(ctx)

    assert repairs == []


def test_skips_short_run():
    """只有 2 镜同景别 → 0 修复（< 3 不算连续）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 镜头1"),
        make_shot(index=2, prompt="[中景] 镜头2"),
        make_shot(index=3, prompt="[特写] 镜头3"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_diversity_v2 import FramingDiversityV2Constraint

    repairs = FramingDiversityV2Constraint().apply(ctx)

    assert repairs == []


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.framing_diversity_v2 import FramingDiversityV2Constraint
    from step01_script_gen.constraints.types import Severity

    c = FramingDiversityV2Constraint()
    assert c.name == "framing_diversity_v2"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True
