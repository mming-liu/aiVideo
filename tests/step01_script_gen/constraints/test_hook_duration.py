"""R. HookDurationConstraint 单元测试（v6.76 起为只报告语义）.

v6.76（2026-09-19）语义变更：
  - 时长 < 4.0 **不再扩到 4.0**（造时长）
  - kfs < 2 **不再补建** 硬编码 `t_sec=3.5` 的定格帧
两项检查解耦，各自独立上报，字段一律保持原值。
理由见 constraints/hook_duration.py 与
docs/step01-temporal-writers-audit-2026-09-19.md。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_reports_short_duration_without_extending():
    """hook duration 3.0 → 上报，但**不改成 4.0**（v6.76 起不造时长）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={"prompt": "[特写] 刺客出剑", "duration_sec": 3.0, "keyframes": []},
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)

    # 时长不足 + 关键帧不足（len=0）→ 两条 warn
    assert len(repairs) == 2
    assert all(r.action == "warn" for r in repairs)
    assert {r.target for r in repairs} == {"hook.duration_sec", "hook.keyframes"}
    # 关键：字段原封不动（原实现会同时写入 duration / duration_sec 两个键）
    assert script["hook"]["duration_sec"] == 3.0
    assert script["hook"].get("duration") is None, "不再写入 duration 镜像字段"
    assert script["hook"]["keyframes"] == []


def test_reports_when_kfs_less_than_2_without_appending():
    """kfs < 2 → 上报，但不再补建固定 3.5s 定格帧；时长也不再扩展."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={
            "prompt": "[特写] 刺客出剑",
            "duration_sec": 3.0,
            "keyframes": [{"desc": "ff", "stage": "before", "t_sec": 0.5}],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)

    assert len(repairs) == 2
    assert all(r.action == "warn" for r in repairs)
    # 时长不再扩展
    assert script["hook"]["duration_sec"] == 3.0
    # 关键：不再补帧
    assert len(script["hook"]["keyframes"]) == 1
    assert script["hook"]["keyframes"][0]["t_sec"] == 0.5


def test_reports_kf_shortage_even_when_duration_ok():
    """两项检查已解耦：时长达标但 kfs 不足也要上报（原实现会提前 return 漏掉）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={
            "prompt": "[特写] 刺客出剑",
            "duration_sec": 5.0,  # 时长达标
            "keyframes": [{"desc": "ff", "stage": "before", "t_sec": 0.5}],  # 但只有 1 个
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)

    assert len(repairs) == 1
    assert repairs[0].target == "hook.keyframes"
    assert repairs[0].action == "warn"


def test_no_report_when_already_ok():
    """duration_sec ≥ 4.0 且 kfs ≥ 2 → 0 上报."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={
            "prompt": "[特写] 刺客出剑",
            "duration_sec": 5.0,
            "keyframes": [
                {"desc": "ff", "stage": "before", "t_sec": 0.5},
                {"desc": "lf", "stage": "after", "t_sec": 4.5},
            ],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)
    assert repairs == []


def test_report_only_is_idempotent():
    """只报告语义幂等：连跑 2 次结果一致，字段不变."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={"prompt": "[特写] 刺客出剑", "duration_sec": 3.0, "keyframes": []},
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    c = HookDurationConstraint()
    before = {"dur": script["hook"]["duration_sec"], "kfs": list(script["hook"]["keyframes"])}
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    after = {"dur": script["hook"]["duration_sec"], "kfs": list(script["hook"]["keyframes"])}

    assert len(r1) == len(r2) >= 1
    assert before == after


def test_no_hook_no_repair():
    """无 hook → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景]", duration_sec=3.0),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint
    from step01_script_gen.constraints.types import Severity

    c = HookDurationConstraint()
    assert c.name == "hook_duration"
    assert c.severity == Severity.WARN
    assert c.fields == ()
