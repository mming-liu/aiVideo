"""B.3.3 ClimaxBackInjectConstraint 单元测试（3 用例）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_injects_climax_in_back_half():
    """后半无高潮词 -> 中点镜追加高潮尾部."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="开场人物", motion_desc="缓缓移动"),
        make_shot(index=2, main_subject="人物对话", motion_desc="说话"),
        make_shot(index=3, main_subject="打斗开始", motion_desc="开始冲突"),
        make_shot(index=4, main_subject="打斗中段", motion_desc="继续冲突"),
        make_shot(index=5, main_subject="人物收尾", motion_desc="收拾"),
        make_shot(index=6, main_subject="人物离去", motion_desc="离开"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import ClimaxBackInjectConstraint
    repairs = ClimaxBackInjectConstraint().apply(ctx)
    assert len(repairs) == 3
    # GAP 规则要求后半至少 3 个 climax shot。
    for shot in script["shots"][3:6]:
        assert "冲突" in shot["main_subject"] or "冲突到达顶点" in shot["prompt"]
        assert "悬念陡升" in shot["core_keywords"]


def test_skips_when_back_half_has_climax():
    """后半已有高潮词 -> 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="开场"),
        make_shot(index=2, main_subject="对话"),
        make_shot(index=3, main_subject="中段"),
        make_shot(index=4, main_subject="反转揭露", motion_desc="反转揭露悬念陡升"),
        make_shot(index=5, main_subject="收尾"),
        make_shot(index=6, main_subject="结局"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import ClimaxBackInjectConstraint
    repairs = ClimaxBackInjectConstraint().apply(ctx)
    assert repairs == []


def test_preserves_existing_core_keywords():
    """core_keywords 原有词不丢失，高潮词追加到 GAP 读取字段。"""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="开场"),
        make_shot(index=2, main_subject="对话"),
        make_shot(index=3, main_subject="中段"),
        make_shot(index=4, main_subject="收尾", core_keywords="水湾,小舟"),
        make_shot(index=5, main_subject="结局"),
        make_shot(index=6, main_subject="远景"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import ClimaxBackInjectConstraint
    repairs = ClimaxBackInjectConstraint().apply(ctx)
    assert len(repairs) == 3
    assert script["shots"][3]["core_keywords"] == "水湾,小舟,冲突,顶点,悬念陡升"


def test_skips_when_too_few_shots():
    """shots < 4 -> 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1),
        make_shot(index=2),
        make_shot(index=3),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import ClimaxBackInjectConstraint
    repairs = ClimaxBackInjectConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.doubao_hardfix import ClimaxBackInjectConstraint
    from step01_script_gen.constraints.types import Severity
    c = ClimaxBackInjectConstraint()
    assert c.name == "doubao_climax_back_inject"
    assert c.severity == Severity.WARN
