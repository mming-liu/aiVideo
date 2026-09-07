"""F14. GazeTypoConstraint 单元测试（v6.62.1，09-06）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make():
    from step01_script_gen.constraints.gaze_typo import GazeTypoConstraint
    return GazeTypoConstraint()


def test_fixes_doubled_char_typo():
    """「目光倒倒盯住」→「目光紧紧盯住」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, motion_desc="杨旭直起身，目光倒倒盯住听香"),
    ])
    ctx = make_ctx(script)
    repairs = _make().apply(ctx)
    assert len(repairs) == 1
    assert "目光紧紧盯住" in script["shots"][0]["motion_desc"]


def test_fixes_kf_desc_too():
    """keyframes[].desc 同样归一."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, keyframes=[
            {"stage": "react", "t_sec": 1.5, "desc": "张十三目光倒倒盯住听香"},
        ]),
    ])
    ctx = make_ctx(script)
    repairs = _make().apply(ctx)
    assert len(repairs) == 1
    assert "目光紧紧" in script["shots"][0]["keyframes"][0]["desc"]


def test_clean_text_untouched():
    """无错别字零操作."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, motion_desc="目光紧紧盯住画左，脚步未停"),
    ])
    ctx = make_ctx(script)
    assert _make().apply(ctx) == []


def test_idempotent():
    """连跑 2 次第 2 次零新增."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, motion_desc="目光倒倒盯住听香"),
    ])
    ctx = make_ctx(script)
    c = _make()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    assert len(r1) == 1
    assert r2 == []
