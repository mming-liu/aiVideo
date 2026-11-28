"""role_anchor：只在人物**出镜**时补外观段（2026-10-28 M06）.

匿名角色名（阿青/阿白）来自本测试输入，不依赖当前小说。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

BANK = {"阿青": {"static_features": "青衣少女，圆脸，杏眼"},
        "阿白": {"static_features": "白衣青年，方脸，剑眉"}}


def _layout(*names):
    return {
        "ff": [{"name": n, "on_screen": True, "position": "画中", "pose": "站立",
                "gaze_target": "画左", "props": []} for n in names],
        "lf": [{"name": n, "on_screen": True, "position": "画中", "pose": "站立",
                "gaze_target": "画左", "props": []} for n in names],
        "kfs": [],
    }


def _shot(prompt, narration="", subject="阿青", layout=()):
    from tests.step01_script_gen.conftest import make_shot
    extra = {"frame_layouts": _layout(*layout)} if layout else {}
    return make_shot(index=1, prompt=prompt, main_subject=subject,
                     narration_segment=narration, **extra)


def test_off_screen_mention_does_not_add_appearance_block():
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint

    shot = _shot("中景，阿青低头拨弄炉火", narration="阿青是阿白前几日买来的女子",
                 layout=("阿青",))
    script = make_script([shot])
    RoleAnchorConstraint().apply(make_ctx(script, characters_bank=BANK))

    prompt = script["shots"][0]["prompt"]
    assert "[阿白:" not in prompt, "画外被提到的人物不得自动加入画面"
    assert "[阿青:" in prompt, "出镜人物仍需身份锚点"


def test_on_screen_actor_with_anchor_is_not_duplicated():
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint

    shot = _shot("[阿青: 青衣少女，圆脸，杏眼] 中景，阿青低头拨弄炉火",
                 narration="阿青是阿白买来的女子", layout=("阿青",))
    script = make_script([shot])
    before = script["shots"][0]["prompt"]
    RoleAnchorConstraint().apply(make_ctx(script, characters_bank=BANK))
    assert script["shots"][0]["prompt"] == before
    assert script["shots"][0]["prompt"].count("[阿青:") == 1


def test_without_layout_legacy_behavior_is_preserved():
    """没有布局数据时不能凭空判定画外：保留旧行为（按文本补锚点）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint

    shot = _shot("中景，炉火跳动", narration="他是阿白买来的", subject="炉火", layout=())
    script = make_script([shot])
    RoleAnchorConstraint().apply(make_ctx(script, characters_bank=BANK))
    assert "[阿白:" in script["shots"][0]["prompt"]


def test_main_subject_counts_as_on_screen():
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint

    shot = _shot("中景，炉火跳动", narration="阿白站在门外", subject="阿白",
                 layout=("阿青",))
    script = make_script([shot])
    RoleAnchorConstraint().apply(make_ctx(script, characters_bank=BANK))
    assert "[阿白:" in script["shots"][0]["prompt"]


def test_repeated_application_is_idempotent():
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint

    shot = _shot("中景，阿青低头拨弄炉火", narration="阿青是阿白买来的女子",
                 layout=("阿青",))
    script = make_script([shot])
    c = RoleAnchorConstraint()
    c.apply(make_ctx(script, characters_bank=BANK))
    once = script["shots"][0]["prompt"]
    c.apply(make_ctx(script, characters_bank=BANK))
    assert script["shots"][0]["prompt"] == once