"""K. OpeningHookConstraint：评阅提示，不改写 prompt（2026-10-28 修订）."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

SERENE = "黄昏余晖似雾，满湾碧荷，一叶小舟行于荷丛间，船头公子持箫吹奏，暖橙逆光"


def _script(first_prompt=SERENE):
    from tests.step01_script_gen.conftest import make_script, make_shot
    return make_script([
        make_shot(index=1, prompt=first_prompt, main_subject="公子"),
        make_shot(index=2, prompt="[中景] 公子抬眼", main_subject="公子"),
        make_shot(index=3, prompt="[中景] 侍女端茶", main_subject="侍女"),
    ])


def test_serene_scene_is_not_rewritten():
    """宁静场景：prompt 逐字保留，只产生非阻塞提示（不注入固定紧张措辞）."""
    from tests.step01_script_gen.conftest import make_ctx
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint

    script = _script()
    before = copy.deepcopy(script)
    ctx = make_ctx(script)
    repairs = OpeningHookConstraint().apply(ctx)

    assert script == before, "约束不得改写稿件"
    assert script["shots"][0]["prompt"] == SERENE
    assert "危机一触即发" not in script["shots"][0]["prompt"]
    assert "惊觉" not in script["shots"][0]["prompt"]
    assert len(repairs) == 1
    assert repairs[0].action == "warn"
    assert repairs[0].before == repairs[0].after == SERENE
    violations = ctx.drain_violations()
    assert len(violations) == 1
    assert violations[0].constraint_name == "opening_hook"
    assert violations[0].severity.name == "WARN"


def test_tense_scene_is_preserved_and_not_warned():
    """原本紧张的场景：用户措辞原样保留，0 修复/0 提示（不再扫描删除）."""
    from tests.step01_script_gen.conftest import make_ctx
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint

    tense = "夜色荷湾，一道黑影持匕首逼近，刺客骤然跃出"
    script = _script(tense)
    before = copy.deepcopy(script)
    ctx = make_ctx(script)
    repairs = OpeningHookConstraint().apply(ctx)

    assert script == before
    assert script["shots"][0]["prompt"] == tense
    assert repairs == []
    assert ctx.drain_violations() == []


def test_repeated_application_never_appends_tail():
    """重复执行（等价于重复 finalize）：不累积文字、不会把尾句加回来."""
    from tests.step01_script_gen.conftest import make_ctx
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint

    script = _script()
    c = OpeningHookConstraint()
    for _ in range(3):
        c.apply(make_ctx(script))
        assert script["shots"][0]["prompt"] == SERENE


def test_engine_consumes_warning_without_mutation():
    """经真实约束引擎：warn 记录可被日志消费，稿件字节不变、无 HARD."""
    from tests.step01_script_gen.conftest import make_ctx
    from step01_script_gen.constraints import apply_constraints
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint
    from step01_script_gen.constraints.types import Severity

    script = _script()
    before = copy.deepcopy(script)
    report = apply_constraints(make_ctx(script), only=(OpeningHookConstraint,))

    assert script == before
    assert [r.action for r in report.repairs] == ["warn"]
    assert report.hard_unresolved == []
    assert all(v.severity is not Severity.HARD for v in report.violations_remaining)


def test_constraint_attributes():
    from step01_script_gen.constraints.opening_hook import OpeningHookConstraint
    from step01_script_gen.constraints.types import Severity

    c = OpeningHookConstraint()
    assert c.name == "opening_hook"
    assert c.severity == Severity.WARN
    assert c.repair_marker is None
    assert "prompt" in c.fields
