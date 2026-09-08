"""W6. RuntimeExtendConstraint 单元测试.

08-23 用户规则：
  - 正片分镜总时长（不含钩子）≥ 60s，钩子时长另算
  - 单镜时长 ≤ 8s（MAX_SHOT_DURATION，与模型 cap 取严）

测试场景：
  1. 修复：末镜 ≤ 4s / 总时长 < 65s → 拉伸 + timecode 同步
  2. 单镜 > 8s → 夹到 8s
  3. 钩子不计入总时长（shots 48s + hook 20s 仍要拉伸）
  4. 饱和仍 < 60s → 记 HARD Violation（镜头数不够，走 regen）
  5. 幂等：连跑 2 次 = 第 2 次 0 改动
  6. 隔离：已达标（单镜 ≤ 8s 且总时长贴线 ≥ 60s）→ 不改
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_clamp_last_shot_when_too_short():
    """末镜 ≤ 4s → 强制夹到 5s."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=5.0),
        make_shot(index=2, duration_sec=4.0),  # 末镜
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert len(repairs) >= 1
    # 末镜应被夹到 >= 4s
    assert script["shots"][1]["duration_sec"] > 4.0


def test_stretches_total_when_below_65s():
    """总时长 < 65s → 按比例放大每镜 (8 镜 × 5s = 40s → 64s)，单镜不破 8s."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 8 镜 × 5s = 40s，远小于 65s
    shots = [make_shot(index=i + 1, duration_sec=5.0) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    total = sum(s.get("duration_sec", 0) for s in script["shots"])
    assert total >= 60.0  # 顶到 8s/镜 = 64s ≥ 60s 硬下限
    # 单镜硬上限 8s（08-23 用户规则）
    assert all(s.get("duration_sec", 0) <= 8.0 for s in script["shots"])


def test_clamps_single_shot_over_8s():
    """单镜 > 8s → 夹到 8s（08-23 用户规则）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 9 镜：1 镜 10s 超限 + 8 镜 7.5s = 70s（总时长已达标，只测单镜夹取）
    shots = [make_shot(index=1, duration_sec=10.0)]
    shots += [make_shot(index=i + 2, duration_sec=7.5) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert script["shots"][0]["duration_sec"] <= 8.0
    assert any(r.target == "duration_sec" and r.action == "clamp" for r in repairs)


def test_hook_excluded_from_total():
    """钩子时长另算：shots 48s + hook 20s（合计 68s）仍要拉伸正片到 ≥60s."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shots = [make_shot(index=i + 1, duration_sec=6.0) for i in range(8)]  # 48s
    hook = make_shot(index=99, duration_sec=20.0)
    script = make_script(shots, hook=hook)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    shots_total = sum(s.get("duration_sec", 0) for s in script["shots"])
    assert shots_total >= 60.0  # 不含钩子也必须 ≥ 60s
    # 钩子时长另算：不被拉伸/夹取改动
    assert script["hook"]["duration_sec"] == 20.0


def test_violation_when_total_unreachable():
    """镜头数不够（6 镜 × 8s = 48s < 60s）→ 记 HARD Violation 走 regen."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.types import Severity

    shots = [make_shot(index=i + 1, duration_sec=5.0) for i in range(6)]  # 30s
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    # 全部顶到 8s 也只有 48s < 60s → 必须报 HARD violation（加镜头解决）
    shots_total = sum(s.get("duration_sec", 0) for s in script["shots"])
    assert shots_total < 60.0
    violations = ctx.drain_violations()
    assert len(violations) == 1
    assert violations[0].severity == Severity.HARD
    assert violations[0].constraint_name == "runtime_extend"
    assert "60s" in violations[0].message or "60" in violations[0].message


def test_syncs_timecode():
    """不论是否拉伸，timecode 永远和 shot['duration'] 同步."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=5.0),
        make_shot(index=2, duration_sec=3.0),  # 末镜 ≤ 4s 触发拉伸
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    # timecode 应被设置且与 duration 一致
    s0 = script["shots"][0]
    s1 = script["shots"][1]
    assert s0.get("timecode", {}).get("start") == 0
    assert s0.get("timecode", {}).get("end") == round(s0.get("duration_sec", 0), 2)
    # shot 2 start = shot 1 end
    assert s1.get("timecode", {}).get("start") == s0.get("timecode", {}).get("end")
    # total_duration 应被重算
    assert "total_duration" in script


def test_is_idempotent_after_first_run():
    """连跑 2 次 = 第 2 次 0 改动（总时长贴线 64s + 末镜 > 4s + 单镜 ≤ 8s）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 8 镜 × 5s = 40s
    shots = [make_shot(index=i + 1, duration_sec=5.0) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    c = RuntimeExtendConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0  # 幂等


def test_skips_when_already_at_limits():
    """单镜全 8s（合计 64s ≥ 60s 硬下限）且末镜 > 4s → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 8 镜 × 8s = 64s：已达 60s 硬下限（65s 目标贴线，need=1 < 2 不再微调）
    shots = [make_shot(index=i + 1, duration_sec=8.0) for i in range(8)]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    repairs = RuntimeExtendConstraint().apply(ctx)

    assert repairs == []
    assert ctx.drain_violations() == []


def test_action_shots_not_stretched_reaction_absorbs():
    """智能分配：高节奏动作镜不拉伸，反应/情绪镜集中拉满（观感自然）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 3 动作镜（刺杀/疾奔）+ 7 反应/建立镜，全 5.0s = 50s → 拉 15s 到 65s
    shots = [
        make_shot(index=1, duration_sec=5.0, prompt="夕阳下，水湾碧荷连天，小舟静泊"),
        make_shot(index=2, duration_sec=5.0, prompt="刺客破水而出，匕首猛刺杨旭胸口"),
        make_shot(index=3, duration_sec=5.0, prompt="听香跪坐船头，泪流满面，浑身颤抖"),
        make_shot(index=4, duration_sec=5.0, prompt="马车疾奔，扬鞭，马匹四蹄腾空"),
        make_shot(index=5, duration_sec=5.0, prompt="张十三蹲下探鼻息，脸色铁青"),
        make_shot(index=6, duration_sec=5.0, prompt="庄仆们哄笑，摇头转身走远"),
        make_shot(index=7, duration_sec=5.0, prompt="杨旭仰卧船头，胸口白袍暗红洇痕"),
        make_shot(index=8, duration_sec=5.0, prompt="听香掩嘴，惊恐望向水面"),
        make_shot(index=9, duration_sec=5.0, prompt="庄仆身影消失在树影后"),
        make_shot(index=10, duration_sec=5.0, prompt="灯笼光暖黄，夜色渐沉"),
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    # 动作镜（index 2/4）不被拉伸
    assert script["shots"][1]["duration_sec"] == 5.0  # 镜2 刺杀
    assert script["shots"][3]["duration_sec"] == 5.0  # 镜4 马车疾奔
    # 反应/建立镜被拉长
    assert script["shots"][0]["duration_sec"] > 5.0   # 镜1 建立
    assert script["shots"][2]["duration_sec"] > 5.0   # 镜3 听香哭
    # 总时长仍达标
    total = sum(s["duration_sec"] for s in script["shots"])
    assert total >= 60.0


def test_all_action_chapter_falls_back_to_stretch_all():
    """纯打斗章（无反应镜可拉）→ 回退拉伸全部，避免时长落空."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 9 镜全打斗 5.0s = 45s → 无反应镜 → 回退拉全部
    shots = [
        make_shot(index=i + 1, duration_sec=5.0,
                  prompt="激烈打斗，挥刀砍杀，血光四溅")
        for i in range(9)
    ]
    script = make_script(shots)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    RuntimeExtendConstraint().apply(ctx)

    total = sum(s["duration_sec"] for s in script["shots"])
    assert total >= 60.0  # 回退后仍达标
    assert all(s["duration_sec"] <= 8.0 for s in script["shots"])


def test_constraint_attributes():
    """验证 Constraint 类属性正确 (severity=HARD, skip_hook=True)."""
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint
    from step01_script_gen.constraints.types import Severity

    c = RuntimeExtendConstraint()
    assert c.name == "runtime_extend"
    assert c.severity == Severity.HARD
    assert c.skip_hook is True


# ===========================================================================
# v6.62.4（09-08）拉伸比告警 —— warning-only，不触发重生成
#
# 09-08 chapter_001 实测：聚合 1.23x、单镜最高 1.50x（shot10 4.00→6.00），
# 7/11 镜 ≥1.25x，且「高节奏动作镜不拉伸」保护因该章无动作镜而完全没生效
# —— 拉伸全落在静态铺垫镜上。加告警让问题可见，但**不改拉伸行为**。
# ===========================================================================

def test_stretch_ratio_warn_on_large_stretch():
    """大幅拉伸（3s 起底，需拉到 60s）→ 出聚合 + 单镜 warn."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    # 12 镜 × 3s = 36s，拉到 60s 需 1.67x —— 远超两个阈值
    script = make_script([make_shot(index=i, duration_sec=3.0) for i in range(1, 13)])
    ctx = make_ctx(script)
    repairs = RuntimeExtendConstraint().apply(ctx)

    warns = [r for r in repairs if r.action == "warn"]
    assert warns, "大幅拉伸应产生 warn"
    assert any(r.shot_index is None and "整体拉伸" in (r.detail or "") for r in warns)
    assert any(r.shot_index is not None and "单镜拉伸" in (r.detail or "") for r in warns)


def test_stretch_ratio_no_warn_when_already_compliant():
    """已达标（无需拉伸）→ 无 warn."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    # 9 镜 × 7s = 63s ≥ 60s，末镜 7s > 4s → 不拉伸
    script = make_script([make_shot(index=i, duration_sec=7.0) for i in range(1, 10)])
    ctx = make_ctx(script)
    repairs = RuntimeExtendConstraint().apply(ctx)

    warns = [r for r in repairs if r.action == "warn"]
    assert not warns, f"无需拉伸时不应告警，实际: {[r.detail for r in warns]}"


def test_stretch_ratio_warn_produces_no_violation():
    """拉伸比告警是 warning-only：不得产生 Violation（不触发 regen）.

    regen 由 GAP 零高潮检测负责（gap_analysis.analyze_shot_distribution），
    W6 只做可观测性 —— 拉伸比高的根因是内容不足，正确解法是补镜头。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.runtime_extend import RuntimeExtendConstraint

    # 10 镜 × 4s = 40s：能靠拉伸达标（10×8=80 > 60），故不该有 HARD Violation
    script = make_script([make_shot(index=i, duration_sec=4.0) for i in range(1, 11)])
    ctx = make_ctx(script)
    repairs = RuntimeExtendConstraint().apply(ctx)

    assert any(r.action == "warn" for r in repairs)
    assert ctx.drain_violations() == [], "拉伸比告警不得升级为 Violation"


def test_stretch_ratio_thresholds_are_module_constants():
    """阈值走模块常量（便于按实测调整，不散落魔数）."""
    from step01_script_gen.constraints import runtime_extend as re_mod

    assert re_mod._STRETCH_WARN_TOTAL == 1.20
    assert re_mod._STRETCH_WARN_SHOT == 1.35
