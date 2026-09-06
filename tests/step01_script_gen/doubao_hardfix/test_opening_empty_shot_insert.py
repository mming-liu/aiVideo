"""B.3.2 OpeningEmptyShotInsertConstraint 单元测试（3 用例）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_inserts_empty_shot_when_opening_has_none():
    """前 3 镜全是人物 -> 插入空镜到 index=1, 末镜 -2s."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    shots = [
        make_shot(index=1, main_subject="杨旭站立", duration_sec=5.0, scene="小舟"),
        make_shot(index=2, main_subject="刺客潜行", duration_sec=5.0),
        make_shot(index=3, main_subject="纣王质问", duration_sec=5.0),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import OpeningEmptyShotInsertConstraint
    repairs = OpeningEmptyShotInsertConstraint().apply(ctx)
    assert len(repairs) == 1
    assert len(script["shots"]) == 4
    new0 = script["shots"][0]
    assert "空镜" in new0["main_subject"]
    assert new0["duration_sec"] == 2
    assert script["shots"][-1]["duration_sec"] == 3.0


def test_skips_when_first_shot_is_empty():
    """shots[0] 已是空镜 -> 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="空镜·远山"),
        make_shot(index=2, main_subject="杨旭"),
        make_shot(index=3, main_subject="刺客"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import OpeningEmptyShotInsertConstraint
    repairs = OpeningEmptyShotInsertConstraint().apply(ctx)
    assert repairs == []


def test_skips_when_too_few_shots():
    """shots < 3 -> 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="杨旭"),
        make_shot(index=2, main_subject="刺客"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import OpeningEmptyShotInsertConstraint
    repairs = OpeningEmptyShotInsertConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.doubao_hardfix import OpeningEmptyShotInsertConstraint
    from step01_script_gen.constraints.types import Severity
    c = OpeningEmptyShotInsertConstraint()
    assert c.name == "doubao_opening_empty_shot_insert"
    assert c.severity == Severity.WARN