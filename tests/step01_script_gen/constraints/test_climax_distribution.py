"""W7. ClimaxDistributionConstraint 单元测试.

测试 3 类场景：
  1. 修复：高潮全在前段 → 后半段中间一镜追加反转/揭穿/悬念
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：高潮已在后段 / 全无 climax（追加末镜）→ 合理
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_climax_to_back_when_all_in_front():
    """高潮全在前段 → 后半段中间一镜 main_subject 应被改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 8 镜：shot 2 / shot 4 都是高潮镜，前半段已有 2 个。
    script = make_script([
        make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 刺杀", main_subject="刺客刺杀"),
        make_shot(index=3, prompt="[中景] 后续", main_subject="杨旭"),
        make_shot(index=4, prompt="[中景] 刺杀2", main_subject="刺客刺杀"),
        make_shot(index=5, prompt="[中景] 场景5", main_subject="杨旭"),
        make_shot(index=6, prompt="[中景] 场景6", main_subject="杨旭"),
        make_shot(index=7, prompt="[中景] 场景7", main_subject="杨旭"),
        make_shot(index=8, prompt="[中景] 场景8", main_subject="杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint

    repairs = ClimaxDistributionConstraint().apply(ctx)

    assert len(repairs) >= 1
    repaired = [
        s for s in script["shots"]
        if "冲突" in s.get("core_keywords", []) and "真相" in s.get("core_keywords", [])
    ]
    assert len(repaired) >= 3
    assert sum(s in script["shots"][len(script["shots"]) // 2:] for s in repaired) >= 1


def test_is_idempotent_after_climax_appended():
    """连跑 2 次 = 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 刺杀", main_subject="刺客刺杀"),
        make_shot(index=3, prompt="[中景] 后续", main_subject="杨旭"),
        make_shot(index=4, prompt="[中景] 刺杀2", main_subject="刺客刺杀"),
        make_shot(index=5, prompt="[中景] 场景5", main_subject="杨旭"),
        make_shot(index=6, prompt="[中景] 场景6", main_subject="杨旭"),
        make_shot(index=7, prompt="[中景] 场景7", main_subject="杨旭"),
        make_shot(index=8, prompt="[中景] 场景8", main_subject="杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint

    c = ClimaxDistributionConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0


def test_injects_remaining_when_one_gap_readable_climax_exists():
    """GAP 可读高潮镜不足 3 个时，已有一个也继续补齐."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 8 镜：shot 7 是 climax（刺杀）→ index 7 >= 6
    script = make_script([
        make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 场景2", main_subject="杨旭"),
        make_shot(index=3, prompt="[中景] 场景3", main_subject="杨旭"),
        make_shot(index=4, prompt="[中景] 场景4", main_subject="杨旭"),
        make_shot(index=5, prompt="[中景] 场景5", main_subject="杨旭"),
        make_shot(index=6, prompt="[中景] 场景6", main_subject="杨旭"),
        make_shot(index=7, prompt="[中景] 刺杀", main_subject="刺客刺杀",
                  core_keywords=["刺杀"]),
        make_shot(index=8, prompt="[中景] 场景8", main_subject="杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint

    repairs = ClimaxDistributionConstraint().apply(ctx)

    assert len(repairs) == 2
    assert all(
        "冲突" in s.get("core_keywords", []) and "真相" in s.get("core_keywords", [])
        for s in script["shots"][4:6]
    )


def test_ignores_semantic_climax_that_gap_cannot_read():
    """main_subject 里的高潮词不能替代 GAP 可读字段."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 对话", main_subject="杨旭"),
        make_shot(index=3, prompt="[中景] 铺垫", main_subject="杨旭"),
        make_shot(index=4, prompt="[中景] 顶点", main_subject="冲突到达顶点"),
        make_shot(index=5, prompt="[中景] 结局", main_subject="杨旭"),
        make_shot(index=6, prompt="[中景] 远景", main_subject="杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint

    repairs = ClimaxDistributionConstraint().apply(ctx)

    assert len(repairs) == 3
    assert all(
        "冲突" in s.get("core_keywords", []) and "真相" in s.get("core_keywords", [])
        for s in script["shots"][3:]
    )


def test_appends_reversal_to_last_when_no_climax_at_all():
    """全无 climax → 末镜应被追加反转."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
        make_shot(index=2, prompt="[中景] 喝酒", main_subject="杨旭"),
        make_shot(index=3, prompt="[中景] 谈话", main_subject="杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint

    repairs = ClimaxDistributionConstraint().apply(ctx)

    assert len(repairs) >= 1
    # 末镜应被追加反转相关词
    last_prompt = script["shots"][-1]["prompt"]
    assert "反转" in last_prompt or "局势" in last_prompt


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint
    from step01_script_gen.constraints.types import Severity

    c = ClimaxDistributionConstraint()
    assert c.name == "climax_distribution"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True
