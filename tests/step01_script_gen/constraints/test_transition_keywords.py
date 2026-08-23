"""W4. TransitionKeywordsConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_combat_tail_to_combat_shot():
    """打斗镜（narration 含'刺杀'）缺转场词 → 追加打斗 tail."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客举刀",
            narration_segment="刺客刺杀杨旭",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.transition_keywords import TransitionKeywordsConstraint

    repairs = TransitionKeywordsConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "转场时长" in script["shots"][0]["prompt"]


def test_appends_closeup_tail():
    """特写镜缺转场词 → 追加特写 tail."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[大特写] 杨旭眼睛特写",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.transition_keywords import TransitionKeywordsConstraint

    repairs = TransitionKeywordsConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "转场时长" in script["shots"][0]["prompt"]


def test_is_idempotent_when_already_repaired():
    """已含'转场时长' → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客刺杀杨旭，转场时长0.2s",
            narration_segment="刺客刺杀杨旭",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.transition_keywords import TransitionKeywordsConstraint

    c = TransitionKeywordsConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert len(repairs1) == 0
    assert len(repairs2) == 0


def test_skips_normal_shot():
    """非打斗/特写/场景切换镜 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭在亭中看书",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.transition_keywords import TransitionKeywordsConstraint

    repairs = TransitionKeywordsConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.transition_keywords import TransitionKeywordsConstraint
    from step01_script_gen.constraints.types import Severity

    c = TransitionKeywordsConstraint()
    assert c.name == "transition_keywords"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "转场时长"
    assert "prompt" in c.fields
