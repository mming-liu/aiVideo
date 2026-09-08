"""B. GazeDirectionConstraint 单元测试.

测试 3 类场景：
  1. 修复：「看/望+人名」无空间方位 → 改写为「目光紧盯X」
  2. 幂等：字段已含「目光/凝望/仰望/俯视」→ 跳过
  3. 隔离：未命中任何 gaze rule → 不改写
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_rewrites_gaze_with_person_name():
    """「看<角色>」无空间方位 → 应改写为「目光紧盯<角色>」.
    通用化（v6.62）：测试用通用角色名「那女子」（COMMON_PERSON_NAMES 兜底词），
    旧版用「杨旭」——现 COMMON_PERSON_NAMES 已删除小说特化角色名。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    target = "那女子"
    script = make_script([
        make_shot(
            index=1,
            prompt=f"[中景] 他看{target}",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    repairs = GazeDirectionConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert f"目光紧盯{target}" in script["shots"][0]["prompt"]


def test_is_idempotent_when_already_repaired():
    """字段已含「目光」→ 跳过（幂等）.
    通用化（v6.62）：同样改为「那女子」避免小说特化。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    target = "那女子"
    script = make_script([
        make_shot(
            index=1,
            prompt=f"[中景] 他目光紧盯{target}",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    c = GazeDirectionConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    # 第一次调用：已含"目光" → 跳过
    assert len(repairs1) == 0
    assert len(repairs2) == 0


def test_no_change_when_no_gaze_pattern():
    """无 gaze 模板命中 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭坐在船头",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    repairs = GazeDirectionConstraint().apply(ctx)
    assert repairs == []


def test_rewrites_multiple_fields():
    """多字段（prompt/ff_desc/lf_desc/motion_desc）都应被扫描.

    已知 v6.32 旧实现限制：lf_desc 字段可能因前面字段被改写后命中"目光"
    而被 skip（幂等保护）。本测试用 3 个独立字段验证基本功能.
    通用化（v6.62）：用 COMMON_PERSON_NAMES 兜底词（那女子/那男子/公子）。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 3 字段各自写一个会被 _GAZE_FALLBACK_RULES 命中的「望+人称」模板
    script = make_script([
        make_shot(
            index=1,
            prompt="他望那女子",          # 望+人称 → 目光紧盯（rule 1）
            ff_desc="望他",              # 望+他 → 目光凝视（rule 2, 无"向"）
            lf_desc="",
            motion_desc="刺客望那男子",  # 望+人称 → 目光紧盯（rule 1）
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    repairs = GazeDirectionConstraint().apply(ctx)

    # 3 字段命中（prompt / ff_desc / motion_desc）；lf_desc 故意空
    assert len(repairs) == 3
    assert "目光紧盯那女子" in script["shots"][0]["prompt"]
    assert "目光凝视他" in script["shots"][0]["ff_desc"]
    assert "目光紧盯那男子" in script["shots"][0]["motion_desc"]


def test_processes_hook():
    """hook 分镜也应被处理（除非 skip_hook=True）.
    通用化（v6.62）：用通用人称（那女子）。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, prompt="[中景] 场景", ff_desc="", lf_desc="")],
        hook={
            "prompt": "[特写] 她看那女子",
            "ff_desc": "",
            "lf_desc": "",
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    repairs = GazeDirectionConstraint().apply(ctx)

    assert "目光紧盯那女子" in script["hook"]["prompt"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint
    from step01_script_gen.constraints.types import Severity

    c = GazeDirectionConstraint()
    assert c.name == "gaze_direction"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "目光"
    assert "prompt" in c.fields
    assert "ff_desc" in c.fields
    assert "lf_desc" in c.fields
    assert "motion_desc" in c.fields


def test_rewrites_gaze_with_stare_at_sky():
    """「望天」→ 「仰望画上方天空」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 他望天",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    repairs = GazeDirectionConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "仰望画上方天空" in script["shots"][0]["prompt"]


# === v6.49 扩展测试（08-28 跑批实测 11/11 视线方向 WARN） ===

def test_rewrites_gaze_with_water_v6_49():
    """v6.49 扩展：「望向前方水面」/「望向水湾」/「望水面垂钓」→ 「俯视画下方水面」.

    旧规则「望水」太严，含中间修饰词（"向前方"/"水湾"/"垂钓"）时不匹配
    → 11/11 镜刷屏 WARN。v6.49 改 regex 允许中间 ≤8 字修饰。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    cases = [
        ("望向前方水面", "俯视画下方水面"),
        ("望向水湾 箫", "俯视画下方水面"),
        ("望水面垂钓", "俯视画下方水面"),
    ]
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint
    c = GazeDirectionConstraint()
    for src, expected in cases:
        script = make_script([
            make_shot(index=1, prompt=src, ff_desc="", lf_desc=""),
        ])
        ctx = make_ctx(script)
        c.apply(ctx)
        result = script["shots"][0]["prompt"]
        assert expected in result, f"src={src!r} expected={expected!r} got={result!r}"


def test_rewrites_gaze_simple_water_still_works():
    """v6.49 改动后旧 case「望水」仍生效（向后兼容）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 他望水", ff_desc="", lf_desc=""),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.gaze_direction import GazeDirectionConstraint

    GazeDirectionConstraint().apply(ctx)

    assert "俯视画下方水面" in script["shots"][0]["prompt"]
