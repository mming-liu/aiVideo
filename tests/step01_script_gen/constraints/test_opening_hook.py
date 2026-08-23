"""K. OpeningHookConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_tail_when_no_punch_word_in_first_third():
    """前 1/3 镜都缺爆点词 → 第一镜 prompt 追加'危机一触即发'组合."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭夜雨", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 杨旭抬眼", main_subject="杨旭"),
        make_shot(index=3, prompt="[中景] 听香端茶", main_subject="听香"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint

    repairs = OpeningHookConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "危机一触即发" in script["shots"][0]["prompt"]
    assert "惊觉" in script["shots"][0]["prompt"]


def test_is_idempotent_when_already_repaired():
    """已含'惊觉'或'对峙' → 跳过；连跑 2 次 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭夜雨", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 杨旭抬眼", main_subject="杨旭"),
        make_shot(index=3, prompt="[中景] 听香端茶", main_subject="听香"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint

    c = OpeningHookConstraint()
    c.apply(ctx)
    # 第 2 次：已含'惊觉'，repair_marker 命中 → 跳过
    repairs2 = c.apply(ctx)
    assert repairs2 == []


def test_skips_when_punch_word_present():
    """前 1/3 段含 _PUNCH_WORDS 任一词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 刺客刺杀杨旭", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 杨旭抬眼", main_subject="杨旭"),
        make_shot(index=3, prompt="[中景] 听香端茶", main_subject="听香"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint

    repairs = OpeningHookConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint
    from step01_script_gen.constraints.types import Severity

    c = OpeningHookConstraint()
    assert c.name == "opening_hook"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "惊觉"
    assert "prompt" in c.fields
