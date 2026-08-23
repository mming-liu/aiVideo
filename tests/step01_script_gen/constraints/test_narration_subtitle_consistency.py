"""H. NarrationSubtitleConsistencyConstraint 单元测试.

测试 4 类场景：
  1. 修复：「...」→「…」、全/半角空格差异、narration/subtitle 同步
  2. 幂等：narration/subtitle 已一致 → 跳过
  3. 隔离：无 narration/subtitle → 0 改动
  4. 多 shot + hook 处理
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_normalizes_three_dots_to_ellipsis():
    """「...」→「…」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            narration_segment="听香颤声道：他来了...",
            subtitle="听香颤声道：他来了...",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    repairs = NarrationSubtitleConsistencyConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert script["shots"][0]["narration_segment"] == "听香颤声道：他来了…"
    assert script["shots"][0]["subtitle"] == "听香颤声道：他来了…"


def test_removes_spaces_in_narration():
    """半角空格 → 移除."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            narration_segment="听香说: 你 来了",
            subtitle="听香说: 你 来了",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    repairs = NarrationSubtitleConsistencyConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert " " not in script["shots"][0]["narration_segment"]
    assert " " not in script["shots"][0]["subtitle"]


def test_syncs_subtitle_to_narration():
    """narration 优先，subtitle 同步."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            narration_segment="听香说：他来了",
            subtitle="听香说：她来了",  # 不一致
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    NarrationSubtitleConsistencyConstraint().apply(ctx)

    # subtitle 同步到 narration
    assert script["shots"][0]["subtitle"] == "听香说：他来了"


def test_is_idempotent_when_already_consistent():
    """narration/subtitle 已一致 → 0 改动（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            narration_segment="听香颤声道：他来了…",
            subtitle="听香颤声道：他来了…",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    c = NarrationSubtitleConsistencyConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 0
    assert len(repairs2) == 0


def test_no_change_when_both_empty():
    """narration/subtitle 都空 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            narration_segment="",
            subtitle="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    repairs = NarrationSubtitleConsistencyConstraint().apply(ctx)
    assert repairs == []


def test_uses_subtitle_when_narration_empty():
    """narration 空但 subtitle 有值 → 用 subtitle 填充 narration."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            narration_segment="",
            subtitle="听香说：他来了...",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    repairs = NarrationSubtitleConsistencyConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert script["shots"][0]["narration_segment"] == "听香说：他来了…"


def test_handles_hook():
    """hook 分镜也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, narration_segment="", subtitle="")],
        hook={
            "narration_segment": "他来了...",
            "subtitle": "他来了...",
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )

    NarrationSubtitleConsistencyConstraint().apply(ctx)

    assert script["hook"]["narration_segment"] == "他来了…"


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.narration_subtitle_consistency import (
        NarrationSubtitleConsistencyConstraint,
    )
    from step01_script_gen.constraints.types import Severity

    c = NarrationSubtitleConsistencyConstraint()
    assert c.name == "narration_subtitle_consistency"
    assert c.severity == Severity.WARN
    assert "narration_segment" in c.fields
    assert "subtitle" in c.fields
