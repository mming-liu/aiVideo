"""frame_layouts_required 键名回归测试（v6.62.7，09-09）.

中间帧布局的规范键是 `kfs`（shared/frame_spec.py:57、timecode.py:233、
frame_spec_repair.py:131、frame_spec_consistency.py:161 全用 `kfs`）。
frame_layouts_required 曾是全仓唯一读 `keyframes` 的地方——读了个永不存在的键，
于是「长度=0 与 shot.keyframes 不一致」对每个有关键帧的镜头**恒定误报**：
09-08 实测 chapter_001 一轮 18 条 warn 里 10 条是这个假警报，把真警报
（连续 3 镜同机位 / 开篇无角色 / 对白密度 10%）挤出日志。

覆盖：
  1. kfs 与 shot.keyframes 对齐 → 不告警（旧实现必误报）
  2. kfs 长度不齐 → 仍然告警（真缺陷不能被这次修复放过）
  3. 告警文案点名 `kfs`（不再写 `keyframes` 误导排查）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _run(shot: dict):
    from step01_script_gen.constraints.context import ConstraintContext
    from step01_script_gen.constraints.element_markers import ElementMarkers
    from step01_script_gen.constraints.frame_layouts_required import (
        FrameLayoutsRequiredConstraint,
    )
    ctx = ConstraintContext.from_script({"shots": [shot]}, markers=ElementMarkers())
    return FrameLayoutsRequiredConstraint().apply(ctx)


_PERSON = [{"name": "杨旭", "on_screen": True, "position": "画左船头",
            "pose": "盘膝坐,手持箫", "gaze_target": "画下方水面", "props": ["箫"]}]


def test_aligned_kfs_produce_no_warning():
    """kfs 与 shot.keyframes 1:1 对齐 → 零告警（旧实现读 keyframes 必误报）."""
    shot = {
        "index": 4,
        "keyframes": [{"desc": "a"}, {"desc": "b"}, {"desc": "c"}],
        "frame_layouts": {
            "ff": list(_PERSON), "lf": list(_PERSON),
            "kfs": [list(_PERSON), list(_PERSON), list(_PERSON)],
        },
    }
    assert _run(shot) == []


def test_misaligned_kfs_still_warn():
    """kfs 短于 shot.keyframes → 仍告警（真缺陷不能被放过）."""
    shot = {
        "index": 4,
        "keyframes": [{"desc": "a"}, {"desc": "b"}, {"desc": "c"}],
        "frame_layouts": {
            "ff": list(_PERSON), "lf": list(_PERSON),
            "kfs": [list(_PERSON)],
        },
    }
    warns = _run(shot)
    assert len(warns) == 1
    assert warns[0].target == "frame_layouts.kfs"
    assert "长度=1" in warns[0].detail and "长度=3" in warns[0].detail


def test_warning_text_names_kfs_not_keyframes():
    """文案必须点名 kfs——写 keyframes 会把排查引到不存在的键上."""
    shot = {
        "index": 4,
        "keyframes": [{"desc": "a"}],
        "frame_layouts": {"ff": list(_PERSON), "lf": list(_PERSON), "kfs": []},
    }
    warns = _run(shot)
    assert len(warns) == 1
    assert "frame_layouts.kfs" in warns[0].detail
    assert "frame_layouts.keyframes" not in warns[0].detail
