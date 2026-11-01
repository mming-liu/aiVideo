# -*- coding: utf-8 -*-
"""固定机位尾帧 prompt 回归：只约束相机，不锁定人物位置，不做词表硬门."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _isolate_script_file(monkeypatch):
    """compose_frame_prompt 会读 script_file 做场景/道具锚定；测试不依赖现场产物.

    指向一个确定不存在的路径即可让锚定函数走空分支，无需创建临时目录。
    """
    from shared import paths
    missing = Path(__file__).resolve().parent / "__no_script_result_for_camera_test__.json"
    assert not missing.exists(), "测试哨兵文件不应存在：%s" % missing
    monkeypatch.setattr(paths, "script_file", lambda: missing)


def _compose(shot: dict) -> str:
    from shared.vimax_bridge import compose_frame_prompt
    return compose_frame_prompt(shot, [], "last_frame")


def test_fixed_camera_system_text_only_locks_camera():
    """固定机位系统文案只约束相机，不把机位扩大成人物位置锁定/换机位指令."""
    out = _compose({"camera_movement": "固定机位", "lf_desc": "两人在船板上交错走过。"})
    assert "同一机位" in out
    assert "相机不移动" in out
    # 旧系统文案回归：人物位置锁定与无条件重布局都不得再注入
    assert "人物在画面中的相对位置不变" not in out
    assert "相对位置不变" not in out
    assert "构图与首帧保持一致" not in out
    assert "构图需按尾帧描述重新布局" not in out
    assert "可与首帧/参考图的景别、机位、人物位置不同" not in out


def test_fixed_camera_last_frame_allows_negated_relayout_text():
    """“禁止重新布局”是否定指令，不是重布局冲突；不得硬拒."""
    text = "禁止重新布局，保持原机位。"
    out = _compose({"camera_movement": "固定机位", "lf_desc": text})
    assert text in out


def test_fixed_camera_last_frame_allows_consistency_text():
    """“可与首帧一致”是一致性描述，不是换机位指令；不得硬拒."""
    text = "尾帧光线可与首帧一致。"
    out = _compose({"camera_movement": "固定机位", "lf_desc": text})
    assert text in out


def test_fixed_camera_last_frame_allows_subject_movement():
    """固定机位允许人物走动/交换位置；尾帧人物位置不同是合法主体运动."""
    text = "固定机位拍摄两人交错走过，尾帧人物位置不同。"
    out = _compose({"camera_movement": "固定机位", "lf_desc": text})
    assert text in out


def test_fixed_camera_aliases_do_not_inject_relayout_clause():
    """“固定”/“static shot”等固定机位别名也必须只约束相机，不得走重布局分支."""
    for camera in ("固定", "static shot", "fixed"):
        out = _compose({"camera_movement": camera, "lf_desc": "两人交错走过。"})
        assert "相机不移动" in out, camera
        assert "构图需按尾帧描述重新布局" not in out, camera


def test_moving_camera_last_frame_keeps_relayout_clause():
    """非固定机位仍按尾帧重布局；本次修正不得误伤运镜镜头."""
    out = _compose({"camera_movement": "镜头横移", "lf_desc": "尾帧正文。"})
    assert "构图需按尾帧描述重新布局" in out
    assert "可与首帧/参考图的景别、机位、人物位置不同" in out
