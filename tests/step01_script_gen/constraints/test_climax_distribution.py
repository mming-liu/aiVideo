"""W7. ClimaxDistributionConstraint 单元测试（v6.62 收敛后契约）.

v6.62 起该约束**不再注入节拍词**（旧版把「。冲突，反转，真相，局势骤变」
注入 5 字段——会进字幕、污染 main_subject），改为：
  1. 剥离历史注入残留（幂等，留 RepairRecord）
  2. 高潮镜不足 → record_violation 上报（WARN，不改数据）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.climax_words import (
    CLIMAX_INJECTED_GAP_SUFFIX,
    CLIMAX_INJECTED_TAILS,
)


def _make_constraint():
    from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint
    return ClimaxDistributionConstraint()


class TestStripInjectedResidue:
    """历史注入残留剥离."""

    def test_strips_suffix_from_all_polluted_fields(self):
        """prompt/main_subject/narration_segment 的注入残留都被剥掉."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        polluted = f"画面内容{CLIMAX_INJECTED_GAP_SUFFIX}"
        script = make_script([
            make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
            make_shot(index=2, prompt=polluted, main_subject=f"群演{CLIMAX_INJECTED_GAP_SUFFIX}",
                      narration_segment=polluted, subtitle=polluted),
            make_shot(index=3, prompt="[中景] 场景3", main_subject="杨旭"),
        ])
        ctx = make_ctx(script)
        repairs = _make_constraint().apply(ctx)

        assert len(repairs) >= 1
        s2 = script["shots"][1]
        assert s2["prompt"] == "画面内容"
        assert s2["main_subject"] == "群演"
        assert s2["narration_segment"] == "画面内容"
        assert s2["subtitle"] == "画面内容"

    def test_strip_is_idempotent(self):
        """连跑 2 次：第 2 次无新增修复."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt=f"内容{CLIMAX_INJECTED_GAP_SUFFIX}"),
            make_shot(index=2, prompt="[中景] 场景2"),
            make_shot(index=3, prompt="[中景] 场景3"),
        ])
        ctx = make_ctx(script)
        c = _make_constraint()
        r1 = c.apply(ctx)
        r2 = c.apply(ctx)
        assert len(r1) >= 1
        assert len(r2) == 0
        assert script["shots"][0]["prompt"] == "内容"

    def test_strips_w7_and_punch_tails_too(self):
        """W7 尾缀与爆点密度尾缀（含数字）同样剥离."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt=f"内容{CLIMAX_INJECTED_TAILS[0]}"),
            make_shot(index=2, prompt="末镜。局势骤变，3个爆点叠加"),
            make_shot(index=3, prompt="[中景] 场景3"),
        ])
        ctx = make_ctx(script)
        _make_constraint().apply(ctx)
        assert script["shots"][0]["prompt"] == "内容"
        assert script["shots"][1]["prompt"] == "末镜"

    def test_clean_text_untouched(self):
        """干净文本零操作（含真实含「冲突」的正文不得误删）."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt="[中景] 杨旭与刺客冲突爆发，刀刃相向"),
            make_shot(index=2, prompt="[中景] 场景2"),
            make_shot(index=3, prompt="[中景] 场景3"),
        ])
        ctx = make_ctx(script)
        _make_constraint().apply(ctx)
        assert script["shots"][0]["prompt"] == "[中景] 杨旭与刺客冲突爆发，刀刃相向"


class TestShortageDetection:
    """高潮镜不足 → 只上报不改写."""

    def _shortage_script(self):
        from tests.step01_script_gen.conftest import make_script, make_shot
        return make_script([
            make_shot(index=i, prompt=f"[中景] 场景{i}", main_subject="杨旭")
            for i in range(1, 9)
        ])

    def test_shortage_records_violation_without_mutation(self):
        """全无高潮镜 → 记 Violation，但所有字段保持原样."""
        from tests.step01_script_gen.conftest import make_ctx

        script = self._shortage_script()
        before = repr([(s["prompt"], s["main_subject"]) for s in script["shots"]])
        ctx = make_ctx(script)
        repairs = _make_constraint().apply(ctx)

        violations = ctx.drain_violations()
        assert len(violations) >= 1
        assert all(v.constraint_name == "climax_distribution" for v in violations)
        # 缺口数 = 3（全无 climax）
        assert all("缺 3 个" in v.message for v in violations)
        # 数据零改写
        after = repr([(s["prompt"], s["main_subject"]) for s in script["shots"]])
        assert before == after
        assert repairs == []

    def test_no_violation_when_sufficient(self):
        """3 个高潮镜（GAP 可读字段含标记词）→ 零违例."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt="[远景] 建立", main_subject="杨旭"),
            make_shot(index=2, prompt="[中景] 刺杀", narration_segment="刺客刺杀", core_keywords=["刺杀"]),
            make_shot(index=3, prompt="[中景] 后续", main_subject="杨旭"),
            make_shot(index=4, prompt="[中景] 反转", narration_segment="真相曝光", core_keywords=["反转"]),
            make_shot(index=5, prompt="[中景] 场景5", main_subject="杨旭"),
            make_shot(index=6, prompt="[中景] 冲突", narration_segment="冲突升级", core_keywords=["冲突"]),
            make_shot(index=7, prompt="[中景] 场景7", main_subject="杨旭"),
            make_shot(index=8, prompt="[中景] 场景8", main_subject="杨旭"),
        ])
        ctx = make_ctx(script)
        repairs = _make_constraint().apply(ctx)
        assert repairs == []
        assert ctx.drain_violations() == []


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.types import Severity

    c = _make_constraint()
    assert c.name == "climax_distribution"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True
