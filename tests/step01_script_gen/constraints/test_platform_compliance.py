"""O. PlatformComplianceConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_ai_marker_to_first_shot():
    """index==1 缺 AI 标注 → prompt 追加'（AI生成）'."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭夜雨"),
        make_shot(index=2, prompt="[中景] 杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.platform_compliance import PlatformComplianceConstraint

    repairs = PlatformComplianceConstraint().apply(ctx)

    assert len(repairs) == 1
    assert "（AI生成）" in script["shots"][0]["prompt"]


def test_is_idempotent():
    """已含'AI生成' → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭夜雨（AI生成）"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.platform_compliance import PlatformComplianceConstraint

    c = PlatformComplianceConstraint()
    c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert repairs2 == []


def test_skips_when_first_shot_has_no_index_one():
    """shots[0].index != 1 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # shots[0].index=2 → 不应被改
    script = make_script([
        make_shot(index=2, prompt="[中景] 杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.platform_compliance import PlatformComplianceConstraint

    repairs = PlatformComplianceConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.platform_compliance import PlatformComplianceConstraint
    from step01_script_gen.constraints.types import Severity

    c = PlatformComplianceConstraint()
    assert c.name == "platform_compliance"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "AI生成"
    assert c.skip_hook is True
