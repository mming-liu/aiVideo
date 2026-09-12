"""DuplicateShotMergeConstraint / CharSideContinuityConstraint 单元测试（2026-09-12）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _ctx(shots: list[dict], characters: list[dict] | None = None):
    from tests.step01_script_gen.conftest import make_ctx, make_script
    return make_ctx(make_script(shots, characters=characters or []))


# ---------------------------------------------------------------------------
# DuplicateShotMergeConstraint：相邻镜重复视觉 HARD 合并
# ---------------------------------------------------------------------------

def test_duplicate_visuals_merged():
    """镜12/12.5 实战形态：ff/lf/motion 逐字重复 → 后镜并入前镜."""
    from step01_script_gen.constraints.duplicate_shot_merge import (
        DuplicateShotMergeConstraint,
    )
    common_ff = "碧绿荷叶漂浮水面，涟漪扩散，水下黑影向画左移动"
    common_lf = "水面涟漪平息，黑影消失不见，荷丛幽暗静谧"
    common_motion = "前2秒：涟漪扩散；2-5秒：黑影隐入荷丛"
    shots = [
        {"index": 12, "duration_sec": 6.5, "location": "照月湾水面",
         "ff_desc": common_ff, "lf_desc": common_lf, "motion_desc": common_motion,
         "narration_segment": "听香：不…不知道，那人…那人一下子从水里跳出来，就…",
         "subtitle": "听香：不…不知道，那人…那人一下子从水里跳出来，就…"},
        {"index": 12.5, "duration_sec": 5.875, "location": "照月湾水面",
         "ff_desc": common_ff, "lf_desc": common_lf, "motion_desc": common_motion,
         "narration_segment": "听香：杀了公子，然后又…又跳进水里不见了…",
         "subtitle": "听香：杀了公子，然后又…又跳进水里不见了…"},
    ]
    ctx = _ctx(shots)
    repairs = DuplicateShotMergeConstraint().apply(ctx)
    assert len(shots) == 1, "重复视觉的后镜应被并入"
    assert shots[0]["duration_sec"] == 12.375
    assert "从水里跳出来" in shots[0]["narration_segment"]
    assert "跳进水里不见了" in shots[0]["narration_segment"]  # 两半台词都保留
    assert repairs and repairs[0].action == "merge"


def test_distinct_visuals_not_merged():
    """视觉不同的相邻镜不合并."""
    from step01_script_gen.constraints.duplicate_shot_merge import (
        DuplicateShotMergeConstraint,
    )
    shots = [
        {"index": 1, "duration_sec": 4.0, "location": "小舟",
         "ff_desc": "杨旭盘膝坐船头吹箫", "lf_desc": "杨旭放下玉箫",
         "motion_desc": "吹箫后放下"},
        {"index": 2, "duration_sec": 4.0, "location": "小舟",
         "ff_desc": "老汉牵牛走在田埂上", "lf_desc": "老汉驻足望向水湾",
         "motion_desc": "老汉停步"},
    ]
    ctx = _ctx(shots)
    repairs = DuplicateShotMergeConstraint().apply(ctx)
    assert repairs == [] and len(shots) == 2


def test_different_speakers_not_merged():
    """不同说话人的对白镜不合并（防把两人的对白缝进同一镜）."""
    from step01_script_gen.constraints.duplicate_shot_merge import (
        DuplicateShotMergeConstraint,
    )
    common_ff = "两人在船头对峙，气氛紧张，刀光剑影，短兵相接，一触即发"
    shots = [
        {"index": 1, "duration_sec": 4.0, "location": "小舟",
         "ff_desc": common_ff, "lf_desc": common_ff, "motion_desc": "对峙",
         "narration_segment": "张十三：住嘴！"},
        {"index": 2, "duration_sec": 4.0, "location": "小舟",
         "ff_desc": common_ff, "lf_desc": common_ff, "motion_desc": "对峙",
         "narration_segment": "听香：救命啊！"},
    ]
    ctx = _ctx(shots)
    repairs = DuplicateShotMergeConstraint().apply(ctx)
    assert repairs == [] and len(shots) == 2


# ---------------------------------------------------------------------------
# CharSideContinuityConstraint：同地点角色侧翻转 → 后镜交换画左/画右
# ---------------------------------------------------------------------------

def test_side_flip_swapped_in_later_shot():
    """镜10 听香画左 → 镜11 听香画右：后镜整体交换."""
    from step01_script_gen.constraints.char_side_continuity import (
        CharSideContinuityConstraint,
    )
    shots = [
        {"index": 10, "duration_sec": 4.0, "location": "小舟船中",
         "ff_desc": "张十三落在船梢，听香在画左抓船舷",
         "lf_desc": "张十三直起身走向画左船头"},
        {"index": 11, "duration_sec": 4.0, "location": "小舟船中",
         "ff_desc": "张十三站在船头，面朝画右听香，听香跪坐画右满脸泪痕",
         "lf_desc": "张十三怒目瞪着画右的听香，听香手指向画左水面",
         "prompt": "中景，听香跪坐画右，张十三面朝画右"},
    ]
    ctx = _ctx(shots, characters=[{"identifier": "张十三"}, {"identifier": "听香"}])
    repairs = CharSideContinuityConstraint().apply(ctx)
    assert repairs and repairs[0].action == "swap_sides"
    assert "画左听香" in shots[1]["ff_desc"] and "听香跪坐画左" in shots[1]["ff_desc"]
    assert "画右" not in shots[1]["ff_desc"]
    assert "听香跪坐画左" in shots[1]["prompt"]


def test_consistent_sides_untouched():
    """侧向一致（听香两镜都在画左）→ 不触发交换."""
    from step01_script_gen.constraints.char_side_continuity import (
        CharSideContinuityConstraint,
    )
    shots = [
        {"index": 1, "duration_sec": 4.0, "location": "小舟船中",
         "ff_desc": "听香在画左抓船舷"},
        {"index": 2, "duration_sec": 4.0, "location": "小舟船中",
         "ff_desc": "听香跪坐画左，泪痕满面"},
    ]
    ctx = _ctx(shots, characters=[{"identifier": "听香"}])
    repairs = CharSideContinuityConstraint().apply(ctx)
    assert repairs == []
