"""M. LfDescLengthConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_layout_when_lf_too_short():
    """lf_desc < 40 字 → 拼接 char_layout 第一个角色布局."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭坐船头",
            lf_desc="杨旭仰卧",  # 4 字
            char_layout=[{"name": "杨旭", "role": "次", "position": "画左", "facing": "听香"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_desc_length import LfDescLengthConstraint

    repairs = LfDescLengthConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "杨旭位于画左" in script["shots"][0]["lf_desc"]


def test_is_idempotent():
    """连跑 2 次不重复追加（repair_marker='位于'）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            lf_desc="杨旭仰卧",  # 4 字
            char_layout=[{"name": "杨旭", "role": "次", "position": "画左"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_desc_length import LfDescLengthConstraint

    c = LfDescLengthConstraint()
    c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert repairs2 == []


def test_skips_when_lf_already_long():
    """lf_desc ≥ 40 字 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    long_lf = "杨旭仰卧船头，白袍心口一片暗红洇痕，背景江面波光粼粼，月色清凉，画面静止，所有角色定锚完成。"  # 42 字
    script = make_script([
        make_shot(index=1, lf_desc=long_lf),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_desc_length import LfDescLengthConstraint

    repairs = LfDescLengthConstraint().apply(ctx)
    assert repairs == []


def test_skips_empty_subject():
    """空镜主语豁免（main_subject 含'空镜'）→ 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            lf_desc="远山",  # 2 字
            main_subject="空镜远山",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_desc_length import LfDescLengthConstraint

    repairs = LfDescLengthConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.lf_desc_length import LfDescLengthConstraint
    from step01_script_gen.constraints.types import Severity

    c = LfDescLengthConstraint()
    assert c.name == "lf_desc_length"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "位于"
    assert "lf_desc" in c.fields
