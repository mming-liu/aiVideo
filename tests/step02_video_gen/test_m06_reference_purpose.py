# -*- coding: utf-8 -*-
"""M06：参考用途必须与实际下发方式一致（首帧那条尤其不能冒充契约）.

事实依据（代码层已核）：
  · `step02_video_gen/prompt_builder.py` 的 r2v 分支把首帧放进 `reference_paths`，
    与其它参考图一样标 `role="reference_image"`，并记 `role_claims={"first_frame": "reference"}`；
  · `_video_gen/volcengine_provider.py` 的 r2v 分支对每张图都 `_image(ref, "reference_image")`，
    **不使用** `first_frame` 角色。

所以 r2v 下「画面从该静态瞬间开始、与图中完全一致」是**语言引导**，不是已实现的
契约。本用例锁住：r2v 的首帧用途只能声明「可对齐什么」，并显式标成待验证；
图生视频（非 r2v）仍保留起点语义。
"""
from __future__ import annotations

from pathlib import Path

from step02_video_gen.prompt_builder import (
    _build_r2v_purpose_list,
    _classify_reference_purpose,
)

_FF_NAME = "first_frame.png"


def _shot():
    return {"index": 3, "prompt": "[中景] 院中，甲提杖而立",
            "ff_desc": "甲站在院中", "main_subject": "甲"}


def test_r2v_first_frame_does_not_claim_a_locked_first_frame():
    text = _classify_reference_purpose(f"/tmp/shot3/{_FF_NAME}", _shot(), {}, [],
                                       r2v=True)
    assert "reference_image" in text, text
    assert "不锁定" in text and "待验证" in text, text
    # 旧文案里的强承诺必须消失
    assert "完全一致" not in text
    assert "画面从该静态瞬间开始" not in text


def test_image_mode_keeps_the_first_frame_contract():
    """图生视频确实把该图当首帧下发，强承诺在这一侧是成立的."""
    text = _classify_reference_purpose(f"/tmp/shot3/{_FF_NAME}", _shot(), {}, [])
    assert "视频起点构图锚点" in text
    assert "完全一致" in text


def test_r2v_purpose_list_numbers_the_first_frame_with_the_honest_wording(tmp_path):
    ff = tmp_path / _FF_NAME
    ff.write_bytes(b"\x89PNG\r\n\x1a\n")
    text = _build_r2v_purpose_list(_shot(), registry={}, characters=[], ref_paths=[str(ff)])
    assert "图1" in text, text
    assert "reference_image" in text
    assert "不锁定" in text
    assert "完全一致" not in text
