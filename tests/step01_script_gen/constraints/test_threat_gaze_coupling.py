"""G. ThreatGazeCouplingConstraint 单元测试.

测试 4 类场景：
  1. 修复：发现者「望向画X」+ 威胁源 position 是另一方向 → 改写
  2. 幂等：威胁源 position 已含视线方向 → 跳过
  3. 隔离：无发现者视线 / 无威胁源 / 威胁源已是该方向 → 0 改动
  4. 多 shot 处理
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_rewrites_threat_position_to_match_gaze():
    """发现者 lf_desc「望向画左」+ 威胁源 position=画右 → 改写为画左."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香惊恐望向画左",
            lf_desc="听香望向画左，看到刺客",
            char_layout=[
                {"name": "听香", "position": "画中"},
                {"name": "刺客", "position": "画右"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )

    repairs = ThreatGazeCouplingConstraint().apply(ctx)

    assert len(repairs) >= 1
    # 刺客 position 应被改写为画左
    layout = script["shots"][0]["char_layout"]
    threat = next(it for it in layout if "刺客" in it["name"])
    assert "画左" in threat["position"]


def test_is_idempotent_when_position_already_matches():
    """威胁源 position 已含视线方向 → 跳过（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香望向画左",
            lf_desc="听香望向画左，看到刺客",
            char_layout=[
                {"name": "听香", "position": "画中"},
                {"name": "刺客", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )

    c = ThreatGazeCouplingConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 0
    assert len(repairs2) == 0


def test_no_change_when_no_threat_in_layout():
    """char_layout 无威胁源角色 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香望向画左",
            lf_desc="听香望向画左看杨旭",
            char_layout=[
                {"name": "听香", "position": "画中"},
                {"name": "杨旭", "position": "画右"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )

    repairs = ThreatGazeCouplingConstraint().apply(ctx)
    assert repairs == []


def test_no_change_when_no_gaze_direction_in_fields():
    """4 字段无"看/望+画X"模式 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香撑油纸伞",
            lf_desc="听香低头看伞面",
            char_layout=[
                {"name": "听香", "position": "画中"},
                {"name": "刺客", "position": "画右"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )

    repairs = ThreatGazeCouplingConstraint().apply(ctx)
    assert repairs == []


def test_rewrites_threat_with_complex_position():
    """威胁源 position="画中偏右" → 替换为"画左偏右"（含旧画X 词）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香惊恐望向画左",
            lf_desc="听香望向画左，看到刺客",
            char_layout=[
                {"name": "刺客", "position": "画右偏中"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )

    repairs = ThreatGazeCouplingConstraint().apply(ctx)

    assert len(repairs) >= 1
    layout = script["shots"][0]["char_layout"]
    threat = next(it for it in layout if "刺客" in it["name"])
    # 旧"画右"被替换为"画左"
    assert "画左" in threat["position"]
    assert "画右" not in threat["position"]


def test_handles_hook():
    """hook 分镜也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, prompt="[中景] 场景", ff_desc="", lf_desc="")],
        hook={
            "prompt": "[特写] 听香望向画左",
            "lf_desc": "听香望向画左",
            "char_layout": [
                {"name": "听香", "position": "画中"},
                {"name": "刺客", "position": "画右"},
            ],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )

    repairs = ThreatGazeCouplingConstraint().apply(ctx)

    assert len(repairs) >= 1
    threat = next(
        it for it in script["hook"]["char_layout"] if "刺客" in it["name"]
    )
    assert "画左" in threat["position"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.threat_gaze_coupling import (
        ThreatGazeCouplingConstraint,
    )
    from step01_script_gen.constraints.types import Severity

    c = ThreatGazeCouplingConstraint()
    assert c.name == "threat_gaze_coupling"
    assert c.severity == Severity.WARN
    assert "prompt" in c.fields
    assert "lf_desc" in c.fields
