"""F8. ClimaxPriorityConstraint 单元测试（v6.49, 08-28）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_non_climax_no_dialogue_marks_merge_candidate():
    """非高潮+无台词 → 写运行时元数据 _merge_candidate（v6.62.1 起不注 narration）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            prompt="[远景] 照月湾荷塘日落，背景远山",
            subtitle="",
            narration_segment="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_priority import ClimaxPriorityConstraint

    repairs = ClimaxPriorityConstraint().apply(ctx)

    assert len(repairs) == 1
    assert script["shots"][0]["_merge_candidate"] is True
    # narration 不被污染（v6.62.1：step04 无消费代码，注 narration 只污染字幕）
    assert script["shots"][0]["narration_segment"] == ""


def test_idempotent_second_pass():
    """连跑 2 次：第 2 次零新增."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            prompt="[远景] 照月湾荷塘日落，背景远山",
            subtitle="",
            narration_segment="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_priority import ClimaxPriorityConstraint

    c = ClimaxPriorityConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    assert len(r1) == 1
    assert r2 == []


def test_with_dialogue_skipped():
    """有台词 → 不合并."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            prompt="[远景] 照月湾日落",
            subtitle="杨旭：好美的夕阳",
            narration_segment="杨旭：好美的夕阳",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_priority import ClimaxPriorityConstraint

    repairs = ClimaxPriorityConstraint().apply(ctx)

    assert len(repairs) == 0


def test_climax_event_skipped():
    """有核心事件 → 不合并."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="刺客破水刺入杨旭心口", subtitle="", narration_segment=""),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_priority import ClimaxPriorityConstraint

    repairs = ClimaxPriorityConstraint().apply(ctx)

    assert len(repairs) == 0


def test_constraint_attributes():
    from step01_script_gen.constraints.climax_priority import ClimaxPriorityConstraint
    from step01_script_gen.constraints.types import Severity
    c = ClimaxPriorityConstraint()
    assert c.name == "climax_priority"
    assert c.severity == Severity.WARN
