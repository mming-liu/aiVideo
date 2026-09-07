"""F13. FramingVarietyConstraint 单元测试（v6.62.1，09-06）.

contract:
  - unique 景别 < 5 → 内容适配镜改 framing 补缺（对白镜中景→近景等）
  - unique ≥ 5 → 零操作（幂等）
  - 候选规则未命中（无对白/末镜/空镜/特写语境镜）→ 不乱改
  - 长词优先匹配（「大远景」不得被当「远景」）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make():
    from step01_script_gen.constraints.framing_variety import FramingVarietyConstraint
    return FramingVarietyConstraint()


class TestFramingVariety:

    def test_variety_met_no_op(self):
        """已有 5 种景别 → 零操作."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        framings = ["大远景", "远景", "全景", "中景", "中近景", "中景"]
        script = make_script([
            make_shot(index=i + 1, prompt=f"[{f}] 场景{i}")
            for i, f in enumerate(framings)
        ])
        ctx = make_ctx(script)
        repairs = _make().apply(ctx)
        assert repairs == []

    def test_dialogue_shot_upgraded_to_closeup(self):
        """对白镜 framing=中景 → 近景（补缺）."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt="[远景] 远山落日"),
            make_shot(index=2, prompt="[中远景] 荷丛小舟"),
            make_shot(index=3, prompt="[远景] 湾上炊烟"),
            make_shot(index=4, prompt="[中景] 杨旭坐船头", narration_segment="杨旭：今日风好"),
            make_shot(index=5, prompt="[中景] 听香撑伞"),
            make_shot(index=6, prompt="[中景] 老农牵牛"),
        ])
        ctx = make_ctx(script)
        repairs = _make().apply(ctx)
        assert len(repairs) >= 1
        # shot4 是对白镜+中景 → 近景
        assert script["shots"][3]["prompt"].startswith("[近景]")

    def test_no_candidates_untouched(self):
        """无内容适配候选（无对白/末镜/空镜/特写语境）→ 不乱改."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt="[中景] 场景1"),
            make_shot(index=2, prompt="[中景] 场景2"),
            make_shot(index=3, prompt="[中景] 场景3"),
        ])
        before = [s["prompt"] for s in script["shots"]]
        ctx = make_ctx(script)
        repairs = _make().apply(ctx)
        # 中景无对白、非末镜、非空镜、无特写语境 → 只有末镜规则 b 可用
        # （末镜中景+拉词不在 → 不改）；断言前两镜不动
        assert script["shots"][0]["prompt"] == before[0]
        assert script["shots"][1]["prompt"] == before[1]

    def test_last_shot_to_fullshot(self):
        """末镜含「拉」→ 全景."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt="[远景] 建立"),
            make_shot(index=2, prompt="[中远景] 推进"),
            make_shot(index=3, prompt="[远景] 落日"),
            make_shot(index=4, prompt="[中景] 缓慢拉镜收尾"),
            make_shot(index=5, prompt="[中景] 拉远定格"),
        ])
        ctx = make_ctx(script)
        repairs = _make().apply(ctx)
        assert len(repairs) >= 1
        assert script["shots"][4]["prompt"].startswith("[全景]")

    def test_long_word_not_matched_as_short(self):
        """「大远景」不被当成「远景」（长词优先）."""
        from step01_script_gen.constraints.framing_variety import _extract_framing
        assert _extract_framing("[大远景] 画面") == "大远景"
        assert _extract_framing("[中远景] 画面") == "中远景"
        assert _extract_framing("[远景] 画面") == "远景"

    def test_idempotent(self):
        """补到 5 种后第二次跑零新增."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, prompt="[远景] 远山落日"),
            make_shot(index=2, prompt="[中远景] 荷丛"),
            make_shot(index=3, prompt="[远景] 炊烟"),
            make_shot(index=4, prompt="[中景] 杨旭坐船头", narration_segment="杨旭：风好"),
            make_shot(index=5, prompt="[中景] 听香"),
            make_shot(index=6, prompt="[中景] 老农"),
        ])
        ctx = make_ctx(script)
        c = _make()
        r1 = c.apply(ctx)
        r2 = c.apply(ctx)
        assert len(r1) >= 1
        assert r2 == []
