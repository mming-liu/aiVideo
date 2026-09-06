"""B.1 DialogNarrationEmptyConstraint 单元测试（3 用例）.

测试 3 类场景:
  1. 修复: 主含对话关键词 + 双空 -> 兜底填 narration_segment + subtitle
  2. 豁免: 空镜主 -> 跳过
  3. 幂等: 已修过 -> 第 2 次 0 改动
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fills_dialogue_shot_with_double_empty():
    """主含对话关键词 + narr/subtitle 双空 -> 兜底填."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="纣王怒喝", narration_segment="", subtitle="",
                  ff_desc="纣王端坐龙椅质问群臣"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import DialogNarrationEmptyConstraint
    repairs = DialogNarrationEmptyConstraint().apply(ctx)
    assert len(repairs) == 1
    shot = script["shots"][0]
    assert "某" in shot["narration_segment"]
    assert shot["subtitle"]
    assert "_DOUBAO_FIX_DLG:" in shot["narration_segment"]


def test_skips_empty_shot():
    """空镜主 (含「空镜/远景/特写」) -> 跳过对白兜底."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="空镜·水波", narration_segment="", subtitle=""),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import DialogNarrationEmptyConstraint
    repairs = DialogNarrationEmptyConstraint().apply(ctx)
    assert repairs == []
    assert script["shots"][0]["narration_segment"] == ""


def test_is_idempotent():
    """连跑 2 次 -> 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="纣王怒喝", narration_segment="", subtitle="",
                  ff_desc="纣王端坐"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import DialogNarrationEmptyConstraint
    c = DialogNarrationEmptyConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    assert len(r1) == 1
    assert len(r2) == 0


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.doubao_hardfix import DialogNarrationEmptyConstraint
    from step01_script_gen.constraints.types import Severity
    c = DialogNarrationEmptyConstraint()
    assert c.name == "doubao_dialogue_narration_empty"
    assert c.severity == Severity.WARN
    assert "_DOUBAO_FIX_DLG:" in c.repair_marker