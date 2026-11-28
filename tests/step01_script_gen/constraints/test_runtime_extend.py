"""W6. RuntimeExtendConstraint 单元测试（v6.76 起为只报告语义）.

08-23 用户规则：
  - 正片分镜总时长（不含钩子）≥ 60s，钩子时长另算
  - 单镜时长 ≤ 8s（MAX_SHOT_DURATION，与模型 cap 取严）

v6.76（2026-09-19）语义变更：
  - **保留** 单镜 > 8s 的夹取（减时长，防 provider 截断内容）
  - **移出** 末镜夹取 + 按比例拉伸 → 改为只报告
理由见 constraints/runtime_extend.py 模块 docstring 与
docs/step01-temporal-writers-audit-2026-09-19.md。

测试场景：
  1. 单镜 > 8s → 夹到 8s（保留的行为）
  2. 总时长 < 60s → 上报 + HARD Violation，但 duration_sec 全部不变
  3. 末镜 ≤ 4s → 上报，但不再夹到 5s
  4. 钩子不计入总时长，且不被改动
  5. 幂等：连跑 2 次结果一致，字段不变
  6. 隔离：已达标 → 0 上报 0 Violation
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _per_shot_cap() -> float:
    """生效单镜上限 = min(视频模型 cap, 项目上限)——不在测试里写死。"""
    from step01_script_gen.constraints.runtime_extend import _MAX_SHOT_SEC, _video_duration_cap

    return min(_video_duration_cap(), _MAX_SHOT_SEC)


def test_reports_but_never_clamps_single_shot_over_cap():
    """单镜 > cap → **只报 HARD，不夹取**（v6.83，二次审核发现 1）.

    原实现把超 cap 的时长直接缩短，理由「减时长是安全的」。但它位于
    `kf_time_window_alignment` / `kf_t_sec_bounds` / `kf_monotonic` **之后**：
    缩短时长会改变所有时间坐标的合法域——实测 12s 夹成 8s 后 `t_sec=10` 的 KF
    留在最终对象里。cap 夹取属时长派生，只能发生在 KF 生成/检查之前
    （`timecode._compute_timecodes` 已在权威派生点按 min(模型 cap, 项目上限) 夹取）。

    v6.93：超限镜按**生效 cap** 构造（`cap + 2`），不再写死 10s——
    2026-09-22 单镜上限 8 → 15 后，10s 已不超限，写死会让这条测试失去意义。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    over = _per_shot_cap() + 2.0
    # 1 镜超限 + 8 镜 7.5s（总时长已达标 ≥60s，只测单镜上限）
    shots = [make_shot(index=1, duration_sec=over)]
    shots += [make_shot(index=i + 2, duration_sec=7.5) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert script["shots"][0]["duration_sec"] == over, "链尾不得改写时长"
    assert not any(r.action == "clamp" for r in repairs)
    violations = ctx.drain_violations()
    assert any(v.severity.name == "HARD" for v in violations), violations
    assert any(r.target == "duration_sec" and r.action == "warn" for r in repairs)


def test_no_longer_stretches_total_but_reports():
    """8 镜 × 5s = 40s < 60s → 上报 + HARD Violation，但**时长一个都不改**."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.types import Severity

    shots = [make_shot(index=i + 1, duration_sec=5.0) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    total = sum(s["duration_sec"] for s in script["shots"])
    assert total == 40.0, f"v6.76 起不再拉伸，总时长应保持 40s，实际 {total}"
    assert any(r.action == "warn" for r in repairs)
    violations = ctx.drain_violations()
    assert len(violations) == 1, "总时长不足应只记一条 HARD（不再重复记两条）"
    assert violations[0].severity == Severity.HARD
    assert "60" in violations[0].message


def test_reports_short_last_shot_without_clamping():
    """末镜 ≤ 4s → 上报，但不再夹到 5s（v6.76：不凭空加无内容时长）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=5.0),
        make_shot(index=2, duration_sec=4.0),  # 末镜，处于下限
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert any(r.shot_index == 2 and r.action == "warn" for r in repairs), "末镜应被上报"
    assert script["shots"][1]["duration_sec"] == 4.0, "末镜时长必须保持原值"


def test_hook_excluded_and_untouched():
    """钩子时长另算：不计入 60s 统计，也不被本约束改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shots = [make_shot(index=i + 1, duration_sec=6.0) for i in range(8)]  # 48s
    hook = make_shot(index=99, duration_sec=20.0)
    script = make_script(shots, hook=hook)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    shots_total = sum(s["duration_sec"] for s in script["shots"])
    assert shots_total == 48.0, "钩子不得被算进正片总时长，正片也不该被拉长"
    assert script["hook"]["duration_sec"] == 20.0, "钩子时长不得被改动"
    # 48s < 60s → 应上报
    assert len(ctx.drain_violations()) == 1


def test_syncs_timecode():
    """不论是否修改，timecode 永远和 shot['duration_sec'] 同步."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=5.0),
        make_shot(index=2, duration_sec=3.0),  # 末镜 ≤ 4s（只上报）
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    s0 = script["shots"][0]
    s1 = script["shots"][1]
    assert s0.get("timecode", {}).get("start") == 0
    assert s0.get("timecode", {}).get("end") == round(s0.get("duration_sec", 0), 2)
    assert s1.get("timecode", {}).get("start") == s0.get("timecode", {}).get("end")
    assert "total_duration" in script


def test_report_only_is_idempotent():
    """只报告语义幂等：连跑 2 次结果一致，duration_sec 全程不变."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shots = [make_shot(index=i + 1, duration_sec=5.0) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    c = RuntimeExtendConstraint()
    before = [s["duration_sec"] for s in script["shots"]]
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    after = [s["duration_sec"] for s in script["shots"]]

    assert len(r1) == len(r2) >= 1
    assert before == after


def test_skips_when_already_at_limits():
    """8 镜 × 8s = 64s ≥ 60s 且末镜 8s > 4s → 0 修复 0 Violation."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shots = [make_shot(index=i + 1, duration_sec=8.0) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert repairs == []
    assert ctx.drain_violations() == []


def test_violation_when_total_unreachable():
    """镜头数不够（即使每镜都顶到 cap 也 <60s）→ HARD Violation 要求加镜头.

    v6.93：fixture 6 镜 → **4 镜**。该约束按 `镜头数 × cap >= 60` 分流成两支提示
    （「补内容」/「增加镜头数」）；cap 8 → 15 后 6 镜的上限从 48s 变成 72s，
    走的是「补内容」那支，测不到本条要测的分支。4 镜在 cap 8/12/15 下都 <60s。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.types import Severity

    shots = [make_shot(index=i + 1, duration_sec=5.0) for i in range(4)]  # 20s
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    shots_total = sum(s["duration_sec"] for s in script["shots"])
    assert shots_total == 20.0, "v6.76 起不再拉伸"
    violations = ctx.drain_violations()
    assert len(violations) == 1
    assert violations[0].severity == Severity.HARD
    assert violations[0].constraint_name == "runtime_extend"
    assert "60" in violations[0].message
    assert "增加镜头数" in violations[0].message


def test_content_insufficiency_report_is_hard():
    """内容不足的可观测信号升级为 HARD（原拉伸比告警是 warn-only 且已随拉伸移除）.

    v6.76：拉伸被移除后「拉伸比」指标失去意义。内容不足直接以
    总时长不足 HARD Violation 表达，并明确指向「补镜头」而非「拉时长」。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.types import Severity

    # 10 镜 × 4.5s = 45s：镜头数够（10×8=80 ≥ 60）、末镜 4.5s > 4s（不触发末镜上报），
    # 只有「总时长不足」这一条
    script = make_script([make_shot(index=i, duration_sec=4.5) for i in range(1, 11)])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert any(r.action == "warn" for r in repairs)
    violations = ctx.drain_violations()
    assert len(violations) == 1
    assert violations[0].severity == Severity.HARD
    # 提示应指向补镜头（可达）而非加镜头数（不可达）
    assert "补镜头" in violations[0].message or "补内容" in violations[0].message


def test_constraint_attributes():
    """验证 Constraint 类属性正确 (severity=HARD, skip_hook=True)."""
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint
    from step01_script_gen.constraints.types import Severity

    c = RuntimeExtendConstraint()
    assert c.name == "runtime_extend"
    assert c.severity == Severity.HARD
    assert c.skip_hook is True
