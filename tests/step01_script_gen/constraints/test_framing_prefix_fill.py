"""F11. framing_prefix_fill 单测（v6.62, 09-06）."""
import pytest

from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.framing_prefix_fill import (
    FramingPrefixFillConstraint,
    _infer_framing,
    _FRAMING_WORDS,
)


def _ctx(shots: list[dict], hook: dict | None = None) -> ConstraintContext:
    script: dict = {"shots": shots}
    if hook is not None:
        script["hook"] = hook
    return ConstraintContext.from_script(script)


def test_fills_missing_framing_prefix():
    """prompt 无景别词 → 前置「[景别]」."""
    shot = {"index": 1, "main_subject": "杨旭", "prompt": "杨旭盘坐船头持箫，听香执伞立于身旁"}
    c = FramingPrefixFillConstraint()
    repairs = c.apply(_ctx([shot]))
    assert len(repairs) == 1
    assert shot["prompt"].startswith("[")
    assert any(w in shot["prompt"] for w in _FRAMING_WORDS)


def test_idempotent_when_framing_present():
    """prompt 已含景别词 → 跳过（幂等）."""
    shot = {"index": 1, "main_subject": "杨旭",
            "prompt": "[中景]杨旭盘坐船头持箫"}
    c = FramingPrefixFillConstraint()
    repairs = c.apply(_ctx([shot]))
    assert repairs == []
    assert shot["prompt"] == "[中景]杨旭盘坐船头持箫"


def test_infer_empty_scene_far_shot():
    """无角色 + 环境词 → 远景."""
    assert _infer_framing("月光洒在水面，涟漪扩散", has_chars=False) == "远景"


def test_infer_action_over_environment():
    """有角色 + 动作词优先于环境词（shot4 教训：月光洒+潜伏 不应判远景）."""
    text = "月光洒在水面，一个身影潜伏倒悬，只露出双眼"
    assert _infer_framing(text, has_chars=True) == "特写"


def test_infer_dialogue_medium_close():
    """对话词 → 中近景."""
    assert _infer_framing("张十三问道：凶手是谁", has_chars=True) == "中近景"


def test_infer_action_medium():
    """动作/打斗词 → 中景."""
    assert _infer_framing("神秘人纵身跃上船舷，右手持匕首刺向杨旭", has_chars=True) == "中景"


def test_infer_fallback_medium():
    """无特征词兜底 → 中景."""
    assert _infer_framing("画面平淡无奇的一镜", has_chars=True) == "中景"


def test_empty_shot_template_not_filled():
    """空镜插入模板（prompt=中景，固定机位）已含「中景」→ 跳过."""
    shot = {"index": 1, "main_subject": "空镜·水波/树影/风",
            "prompt": "中景，固定机位，暖金光位，画面稳定不闪烁。"}
    c = FramingPrefixFillConstraint()
    assert c.apply(_ctx([shot])) == []


def test_hook_also_filled():
    """hook prompt 同样回填."""
    hook = {"index": 0, "main_subject": "听香与张十三",
            "prompt": "冷夜船头，披发女子半跪指向水面"}
    c = FramingPrefixFillConstraint()
    repairs = c.apply(_ctx([], hook=hook))
    assert len(repairs) == 1
    assert hook["prompt"].startswith("[")
