"""V. BoatCropConstraint 单元测试.

测试 4 类场景：
  1. 修复：船体裁切短语 → 剥离
  2. 幂等：已剥离（无命中短语）→ 跳过
  3. 隔离：无裁切短语 → 0 改动
  4. 多字段都应被扫描
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_strips_boat_extension_out_of_frame():
    """「船身向画面右方延伸出画外」→ 剥离该短语."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 小舟漂在湖面，船身向画面右方延伸出画外（只露出船身一部分）",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    repairs = BoatCropConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "延伸出画外" not in script["shots"][0]["prompt"]
    assert "只露出船身一部分" not in script["shots"][0]["prompt"]


def test_strips_partial_visibility_phrase():
    """「只露出船身一部分」→ 剥离."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜色中小舟静泊，只露出船身一部分",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    repairs = BoatCropConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "只露出船身一部分" not in script["shots"][0]["prompt"]


def test_strips_boat_end_out_of_frame():
    """「船尾出画外」→ 剥离."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭在船头，船尾出画外",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    repairs = BoatCropConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "船尾出画外" not in script["shots"][0]["prompt"]


def test_is_idempotent_after_first_apply():
    """剥离后再调一次 → 0 改动（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 小舟漂在湖面，船身向右延伸出画外",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    c = BoatCropConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) >= 1
    assert len(repairs2) == 0


def test_no_change_when_no_crop_phrase():
    """无船体裁切短语 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭坐在船头听香撑油纸伞",
            ff_desc="",
            lf_desc="",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    repairs = BoatCropConstraint().apply(ctx)
    assert repairs == []


def test_handles_multiple_fields():
    """prompt/ff_desc/lf_desc 都被扫描."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="船身向右延伸出画外",
            ff_desc="只露出船身一部分",
            lf_desc="船尾出画外",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    repairs = BoatCropConstraint().apply(ctx)

    assert len(repairs) == 3
    assert "延伸出画外" not in script["shots"][0]["prompt"]
    assert "只露出船身一部分" not in script["shots"][0]["ff_desc"]
    assert "船尾出画外" not in script["shots"][0]["lf_desc"]


def test_processes_hook():
    """hook 分镜也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, prompt="[中景] 场景", ff_desc="", lf_desc="")],
        hook={
            "prompt": "[特写] 船身向右延伸出画外",
            "ff_desc": "",
            "lf_desc": "",
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint

    BoatCropConstraint().apply(ctx)

    assert "延伸出画外" not in script["hook"]["prompt"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.boat_crop import BoatCropConstraint
    from step01_script_gen.constraints.types import Severity

    c = BoatCropConstraint()
    assert c.name == "boat_crop_strip"
    assert c.severity == Severity.WARN
    assert "ff_desc" in c.fields
    assert "lf_desc" in c.fields
    assert "prompt" in c.fields
