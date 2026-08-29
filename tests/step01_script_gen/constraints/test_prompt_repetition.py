"""P. PromptRepetitionConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_removes_4char_duplicate_token():
    """'眼睛瞪大' 重复 2 次 → 第 2 次被删."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 眼睛瞪大，瞳孔收缩，嘴巴张开，面部肌肉扭曲，眼睛瞪大，瞳孔收缩",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    repairs = PromptRepetitionConstraint().apply(ctx)

    assert len(repairs) >= 1
    # 重复的 4-char 短语应只出现 1 次
    assert script["shots"][0]["prompt"].count("眼睛瞪大") == 1


def test_is_idempotent():
    """删后无重复 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 已删干净（手动）
    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭夜雨，杨旭站立，听香端茶。"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    c = PromptRepetitionConstraint()
    c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert repairs2 == []


def test_skips_short_fields():
    """字段 < 8 字 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景]"),  # 太短
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    repairs = PromptRepetitionConstraint().apply(ctx)
    assert repairs == []


def test_handles_multiple_fields():
    """多字段（prompt/ff_desc/motion_desc）都被扫描."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="眼睛瞪大，瞳孔收缩，嘴巴张开，面部肌肉扭曲，眼睛瞪大，瞳孔收缩",
            ff_desc="",
            lf_desc="",
            motion_desc="眼睛瞪大，瞳孔收缩，嘴巴张开，面部肌肉扭曲，眼睛瞪大，瞳孔收缩",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    repairs = PromptRepetitionConstraint().apply(ctx)
    # prompt + motion_desc 两个字段被改
    assert len(repairs) == 2


def test_constraint_attributes():
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint
    from step01_script_gen.constraints.types import Severity

    c = PromptRepetitionConstraint()
    assert c.name == "prompt_repetition"
    assert c.severity == Severity.WARN
    assert "prompt" in c.fields
    assert "ff_desc" in c.fields
    assert "lf_desc" in c.fields
    assert "motion_desc" in c.fields


# ============================================================================
# v6.50（08-28）：角色名/短语连跑折叠（滑窗对含逗号重复单元的盲区）
# ============================================================================

def test_collapses_name_run_with_commas():
    """「杨旭，杨旭，杨旭，杨旭」→「杨旭」（08-28 chapter_001 实测损坏）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="听香站立身旁，杨旭，杨旭，杨旭，杨旭，手持油纸伞撑开",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    PromptRepetitionConstraint().apply(ctx)

    assert script["shots"][0]["prompt"] == "听香站立身旁，杨旭，手持油纸伞撑开"


def test_collapses_three_char_name_run():
    """「张十三，张十三，张十三」→「张十三」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=8,
            ff_desc="张十三站在画右岸边，手指向画左水边；张十三，张十三，张十三，转身欲走",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    PromptRepetitionConstraint().apply(ctx)

    assert "张十三，张十三" not in script["shots"][0]["ff_desc"]


def test_normal_text_with_repeated_name_not_collapsed():
    """正常叙述中角色名多次出现（非逗号连跑）→ 不动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    original = "杨旭仰卧船头，听香望向杨旭，张十三看着杨旭"
    script = make_script([make_shot(index=1, prompt=original)])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    PromptRepetitionConstraint().apply(ctx)

    assert script["shots"][0]["prompt"] == original
