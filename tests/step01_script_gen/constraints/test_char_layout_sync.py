"""N. CharLayoutSyncConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fixes_position_when_prompt_says_opposite():
    """prompt 写'画右'但 char_layout 写'画左' → 改 char_layout."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭画右站立",
            char_layout=[{"name": "杨旭", "role": "主", "position": "画左"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.char_layout_sync import CharLayoutSyncConstraint

    repairs = CharLayoutSyncConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "画右" in script["shots"][0]["char_layout"][0]["position"]


def test_is_idempotent_when_already_consistent():
    """char_layout position 已与 prompt 一致 → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭画右站立",
            char_layout=[{"name": "杨旭", "role": "主", "position": "画右"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.char_layout_sync import CharLayoutSyncConstraint

    repairs = CharLayoutSyncConstraint().apply(ctx)
    assert repairs == []


def test_skips_when_no_direction_word():
    """text 中无'画左/画右' → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭站立",
            char_layout=[{"name": "杨旭", "role": "主", "position": "画中"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.char_layout_sync import CharLayoutSyncConstraint

    repairs = CharLayoutSyncConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.char_layout_sync import CharLayoutSyncConstraint
    from step01_script_gen.constraints.types import Severity

    c = CharLayoutSyncConstraint()
    assert c.name == "char_layout_sync"
    assert c.severity == Severity.WARN
    assert c.repair_marker is None
    assert "prompt" in c.fields
