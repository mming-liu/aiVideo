# -*- coding: utf-8 -*-
"""旁白字幕不做视觉覆盖；人物对白与普通字幕的覆盖判据保持不变."""
from __future__ import annotations

from scene_compliance import check_subtitle_coverage

SPEAKERS = frozenset({"角色甲", "角色乙"})


def test_narration_subtitle_is_voice_over_not_visual_coverage():
    cov = check_subtitle_coverage(
        "旁白：角色甲三年前离开故乡，如今已是另一番光景。",
        "黄昏的渡口空无一人，水面泛着微光。", SPEAKERS)
    assert cov["pass"] is True
    assert cov.get("voice_over") is True
    assert cov["missing"] == []


def test_character_dialogue_requires_speaker_on_screen():
    ok = check_subtitle_coverage("角色甲道：走吧。", "角色甲站在渡口。", SPEAKERS)
    assert ok["pass"] is True
    bad = check_subtitle_coverage("角色甲道：走吧。", "渡口空无一人。", SPEAKERS)
    assert bad["pass"] is False and bad["missing"] == ["角色甲"]


def test_plain_subtitle_still_uses_noun_coverage():
    cov = check_subtitle_coverage("渡口 木船 灯笼", "渡口边停着一条木船。", SPEAKERS)
    assert cov["pass"] is True
    cov2 = check_subtitle_coverage("渡口 木船 灯笼 雨伞 铜钱",
                                   "山间小路。", SPEAKERS)
    assert cov2["pass"] is False and cov2["missing"]
