# -*- coding: utf-8 -*-
"""M02 复核第三轮：后处理不得撤销**已授权**的修复.

两处已确认的实现问题（2026-11-12 复核）：

1. `validation._shot_size_level` 按词表顺序取**第一个子串命中**——`远景` 先于
   `大远景`、`特写` 先于 `大特写`，于是「大远景」被判成 `远景`；`framing_jump`
   随后 `replace("远景","特写")`，作用在「大远景」上恰好还原成「大特写」，
   把审计刚修好的 prompt 改回原值。
2. `v6_43_prompt_enforcement._fix_vague_anchor` 无条件追加「（X是独立物体…）」，
   模型删掉重复后又被加回去，重复数量随轮次增长。

修法：景别最长匹配；追加前先看紧邻文本是否已有说明；修复轮内，被授权改写的镜头
不再被 `framing_jump` 自动改写（该约束仍由下一轮审计按 `shot_size_jump` 报 warning）。
"""
from __future__ import annotations

from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.framing_jump import FramingJumpConstraint
from step01_script_gen.constraints.v6_43_prompt_enforcement import _fix_vague_anchor
from step01_script_gen.validation import _shot_size_level

_ANCHOR_TAIL = "（寒光是独立物体，不是从角色身上长出来的）"


# ---------------------------------------------------------------------------
# 1. 景别识别：最长匹配
# ---------------------------------------------------------------------------
def test_shot_size_prefers_the_longest_framing_word():
    assert _shot_size_level({"prompt": "[大远景] 荷塘"}) == "大远景"
    assert _shot_size_level({"prompt": "黄昏荷塘大远景，夕阳半沉"}) == "大远景"
    assert _shot_size_level({"prompt": "[大特写] 指尖"}) == "大特写"
    assert _shot_size_level({"prompt": "[中近景] 船头"}) == "中近景"
    assert _shot_size_level({"prompt": "[鸟瞰远景] 全城"}) == "鸟瞰远景"


def _pair_script(prev_prompt: str, curr_prompt: str) -> dict:
    return {"shots": [
        {"index": 1, "scene": "荷塘", "location": "荷塘",
         "prompt": prev_prompt, "ff_desc": "空镜", "lf_desc": "空镜",
         "motion_desc": "镜头缓慢推近"},
        {"index": 2, "scene": "荷塘", "location": "荷塘",
         "prompt": curr_prompt, "ff_desc": "人物", "lf_desc": "人物",
         "motion_desc": "前2秒：人物入画；后2秒：人物站定"},
    ], "characters": []}


def test_framing_jump_does_not_rewrite_a_shot_under_repair():
    """本轮被授权改写的镜头：`framing_jump` 不得把它改回去."""
    script = _pair_script("黄昏荷塘大远景，空无一人", "[中近景] 船头，人物立")
    ctx = ConstraintContext.from_script(
        script, is_in_repair_loop=True, repair_shot_indices={1})
    repairs = FramingJumpConstraint().apply(ctx)
    assert repairs == []
    assert script["shots"][0]["prompt"] == "黄昏荷塘大远景，空无一人"


def test_framing_jump_still_rewrites_shots_outside_the_round():
    """不在本轮修复范围内的镜头，行为保持原样（规则本身没有被取消）."""
    script = _pair_script("黄昏荷塘大远景，空无一人", "[中近景] 船头，人物立")
    ctx = ConstraintContext.from_script(
        script, is_in_repair_loop=True, repair_shot_indices={2})
    repairs = FramingJumpConstraint().apply(ctx)
    assert repairs, "非本轮目标的景别跨度仍应被处理"
    assert "大远景" not in script["shots"][0]["prompt"]


def test_framing_jump_never_rewrites_on_the_final_pass():
    """落盘前（`is_final_pass`）只报不改：投影不得撤销已接受的修复."""
    script = _pair_script("黄昏荷塘大远景，空无一人", "[中近景] 船头，人物立")
    ctx = ConstraintContext.from_script(script, is_final_pass=True)
    assert FramingJumpConstraint().apply(ctx) == []
    assert script["shots"][0]["prompt"] == "黄昏荷塘大远景，空无一人"


# ---------------------------------------------------------------------------
# 2. 锚点说明：幂等
# ---------------------------------------------------------------------------
def test_vague_anchor_note_is_not_appended_twice():
    shot = {"prompt": f"一道寒光{_ANCHOR_TAIL}，刃尖指向画左", "ff_desc": ""}
    _fix_vague_anchor(shot)
    assert shot["prompt"].count("独立物体") == 1, shot["prompt"]


def test_vague_anchor_note_is_added_once_when_missing():
    shot = {"prompt": "一道寒光，刃尖指向画左", "ff_desc": ""}
    _fix_vague_anchor(shot)
    assert shot["prompt"].count("独立物体") == 1, shot["prompt"]
    # 再跑一次不得增长
    _fix_vague_anchor(shot)
    assert shot["prompt"].count("独立物体") == 1, shot["prompt"]
