"""v6.62.2 通用兜底 4 个新约束的 smoke tests（覆盖最常见的真实 bug 模式）.

不依赖具体小说角色——全部用「那女子/那男子/公子」等通用名。换小说照样过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _ctx(script):
    from step01_script_gen.constraints.context import ConstraintContext
    return ConstraintContext(script=script, shots=script.get("shots", []), hook=script.get("hook"))


def test_opening_character_presence_warns_when_no_character_in_first_2():
    """前 2 镜均无角色名 → 警告."""
    from step01_script_gen.constraints.opening_character_presence import OpeningCharacterPresenceConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="空镜·水波", ff_desc="", lf_desc=""),
        make_shot(index=2, main_subject="群演", ff_desc="", lf_desc=""),
        make_shot(index=3, main_subject="那女子", ff_desc="", lf_desc=""),
    ])
    # script.characters 含 "那女子" 但前 2 镜 main_subject 不含
    script["characters"] = [{"identifier": "那女子"}]
    reps = OpeningCharacterPresenceConstraint().apply(_ctx(script))
    assert len(reps) == 1
    assert "前 2 镜均无角色" in reps[0].detail


def test_opening_character_presence_passes_when_first_shot_has_character():
    """前 2 镜任 1 个含角色名 → 通过."""
    from step01_script_gen.constraints.opening_character_presence import OpeningCharacterPresenceConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="那女子端坐", ff_desc="", lf_desc=""),
        make_shot(index=2, main_subject="空镜", ff_desc="", lf_desc=""),
    ])
    script["characters"] = [{"identifier": "那女子"}]
    reps = OpeningCharacterPresenceConstraint().apply(_ctx(script))
    assert reps == []


def test_dialogue_density_warns_when_below_30_percent():
    """对白镜 25% < 30% → 警告."""
    from step01_script_gen.constraints.dialogue_density import DialogueDensityConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    # 4 镜：1 镜对白 + 3 镜无对白 = 25% < 30%
    script = make_script([
        make_shot(index=1, narration_segment="空镜远景，烈日当空", ff_desc="", lf_desc=""),
        make_shot(index=2, narration_segment="他道：住嘴！", ff_desc="", lf_desc=""),
        make_shot(index=3, narration_segment="水面微澜", ff_desc="", lf_desc=""),
        make_shot(index=4, narration_segment="远山沉默", ff_desc="", lf_desc=""),
    ])
    reps = DialogueDensityConstraint().apply(_ctx(script))
    assert len(reps) == 1
    assert "0%" in reps[0].detail or "25%" in reps[0].detail or "对白镜仅" in reps[0].detail


def test_dialogue_density_passes_when_above_30_percent():
    """对白镜 50% ≥ 30% → 通过."""
    from step01_script_gen.constraints.dialogue_density import DialogueDensityConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    # 4 镜：2 镜对白 = 50% ≥ 30%
    script = make_script([
        make_shot(index=1, narration_segment="他道：来者何人", ff_desc="", lf_desc=""),
        make_shot(index=2, narration_segment="她问：从何来", ff_desc="", lf_desc=""),
        make_shot(index=3, narration_segment="水面微澜", ff_desc="", lf_desc=""),
        make_shot(index=4, narration_segment="远山沉默", ff_desc="", lf_desc=""),
    ])
    reps = DialogueDensityConstraint().apply(_ctx(script))
    assert reps == []


def test_frame_layouts_required_warns_when_missing():
    """frame_layouts 缺失 → 警告（ff/lf 必填）."""
    from step01_script_gen.constraints.frame_layouts_required import FrameLayoutsRequiredConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    script = make_script([
        make_shot(index=1, ff_desc="ff", lf_desc="lf"),  # 无 frame_layouts 字段
    ])
    reps = FrameLayoutsRequiredConstraint().apply(_ctx(script))
    # 默认 frame_layouts 是空 dict → ff/lf 为空触发 warning
    assert len(reps) >= 1
    assert any("frame_layouts.ff 为空" in r.detail or "frame_layouts.lf 为空" in r.detail for r in reps)


def test_shot_count_bounds_warns_when_scenes_zero():
    """场景数 0 → 警告（场景提取失败典型症状）.

    v6.62.3：容器键是 script["scene_assets"]（dict，_extract_assets_and_update_script
    写入），不是无写入方的 "scenes"。键**存在但为空** = 提取失败 → 报；
    键**完全缺失** = 提取还没跑（修复轮内）→ 不报，见下一个用例。
    """
    from step01_script_gen.constraints.shot_count_bounds import ShotCountBoundsConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    script = make_script([
        make_shot(index=1, ff_desc="", lf_desc=""),
        make_shot(index=2, ff_desc="", lf_desc=""),
    ])
    script["scene_assets"] = {}          # 提取跑过但零结果
    reps = ShotCountBoundsConstraint().apply(_ctx(script))
    assert any("场景数" in r.detail for r in reps)


def test_shot_count_bounds_silent_when_scene_key_absent():
    """场景键缺失（资产提取尚未执行）→ 不报，避免修复轮每轮刷假告警."""
    from step01_script_gen.constraints.shot_count_bounds import ShotCountBoundsConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    script = make_script([make_shot(index=1), make_shot(index=2)])
    script.pop("scene_assets", None)
    script.pop("scenes", None)
    reps = ShotCountBoundsConstraint().apply(_ctx(script))
    assert not any("场景数" in r.detail for r in reps)


def test_shot_count_bounds_counts_scene_assets_dict():
    """scene_assets 是 dict（键=identifier）→ 按条数计，超上限报警."""
    from step01_script_gen.constraints.shot_count_bounds import ShotCountBoundsConstraint
    from tests.step01_script_gen.conftest import make_script, make_shot

    script = make_script([make_shot(index=i) for i in range(1, 4)])
    script["scene_assets"] = {f"场景{i}": {"wide_desc": ""} for i in range(8)}
    reps = ShotCountBoundsConstraint().apply(_ctx(script))
    assert any("场景数 8 >" in r.detail for r in reps)


def test_dialogue_density_counts_bare_speaker_label():
    """v6.62.3：「人名：台词」无对话动词也算对白（09-08 实测 2/11 曾漏判成 0/11）."""
    from step01_script_gen.constraints.dialogue_density import _has_dialogue

    assert _has_dialogue("那男子：抱歉的很，我家少爷已订亲") is True
    assert _has_dialogue("那女子低声道：别出声") is True


def test_dialogue_density_ignores_metadata_labels():
    """结构性元数据标签（时间/地点/音效…）不是说话人，不计对白."""
    from step01_script_gen.constraints.dialogue_density import _has_dialogue

    for txt in ("时间：黄昏。远景空镜", "地点：前厅", "音效：水声渐起", "景别：近景"):
        assert _has_dialogue(txt) is False, txt
