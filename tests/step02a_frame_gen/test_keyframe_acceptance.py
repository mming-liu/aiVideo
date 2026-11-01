# -*- coding: utf-8 -*-
"""step02a 验收：核心关键帧的**身份**与**序号**（v6.110）.

实测（2026-09-24 整章重跑）：shot 3 缺 `keyframe_0.png`，验收却报
`PASS / missing 0`。两个漏洞叠加：

  ① `shot_dir.glob("keyframe_*.png")` 把二修标注中间产物
     `keyframe_1_doodle.png` 也算成关键帧 → 数量凑够 4；
  ② 只比**数量**不比**序号** → 错位（缺 0 号、多 1 个别的号）也照样通过。

`*_doodle.png` 是区域编辑的**输入**标注图（画了红框），编辑结果覆盖原文件，
它本身不是产物。把它当帧还有第二个后果：step02b 的 `frame_paths` 会把带红框的
图发给视频模型。
"""
from __future__ import annotations

from pathlib import Path

from shared.paths import core_keyframe_files, is_core_keyframe_file, keyframe_files


def _mk(d: Path, names):
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"x")


def test_core_keyframe_predicate_excludes_doodle_artifacts():
    for ok in ("keyframe_0.png", "keyframe_2.png", "keyframe_10.png"):
        assert is_core_keyframe_file(Path(ok)) is True
    for bad in ("keyframe_1_doodle.png", "keyframe_1_doodle_tmp.png",
                "keyframe_.png", "first_frame.png", "keyframe_1.jpg"):
        assert is_core_keyframe_file(Path(bad)) is False


def test_core_keyframe_files_sorts_numerically(tmp_path, monkeypatch):
    import shared.paths as paths

    d = tmp_path / "3"
    _mk(d, ["keyframe_2.png", "keyframe_10.png", "keyframe_1.png",
            "keyframe_1_doodle.png"])
    monkeypatch.setattr(paths, "shot_work_dir", lambda idx, chapter=None: d)
    assert [p.name for p in core_keyframe_files(3)] == [
        "keyframe_1.png", "keyframe_2.png", "keyframe_10.png"]


def test_keyframe_files_membership_excludes_doodle(tmp_path, monkeypatch):
    import shared.paths as paths

    d = tmp_path / "5"
    _mk(d, ["keyframe_0.png", "keyframe_0_doodle.png", "keyframe_1.png"])
    monkeypatch.setattr(paths, "shot_work_dir", lambda idx, chapter=None: d)
    assert [p.name for p in keyframe_files(5)] == ["keyframe_0.png", "keyframe_1.png"]


# ---------------------------------------------------------------------------
# 验收门：缺图必须 BLOCK（按序号核对，不被中间产物凑数）
# ---------------------------------------------------------------------------
def _png(path: Path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path)


def _script(n_kfs=4):
    return {"hook": None, "shots": [{
        "index": 3, "duration_sec": 5.0,
        "keyframes": [{"t_sec": 1.0 * (i + 1), "desc": "d%d" % i} for i in range(n_kfs)],
    }]}


def test_acceptance_blocks_when_a_keyframe_index_is_missing(tmp_path, monkeypatch):
    import step02a_frame_gen.image_prompt_quality as q

    work = tmp_path / "vimax_working"
    d = work / "shots" / "3"
    _png(d / "first_frame.png")
    _png(d / "last_frame.png")
    # 缺 keyframe_0，但多一张 doodle —— 旧实现会因此凑够数量报 PASS
    for n in ("keyframe_1.png", "keyframe_2.png", "keyframe_3.png",
              "keyframe_1_doodle.png"):
        _png(d / n)
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    # 固定「视频模型消费关键帧」，避免断言随 .env 的视频型号漂移
    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: True)
    payload = q.write_image_product_quality(_script(4), work)
    assert payload["status"] == "BLOCK"
    assert any("关键帧" in m and "3" in m for m in payload["missing"])


def test_acceptance_passes_when_all_indices_present(tmp_path, monkeypatch):
    import step02a_frame_gen.image_prompt_quality as q

    work = tmp_path / "vimax_working"
    d = work / "shots" / "3"
    _png(d / "first_frame.png")
    _png(d / "last_frame.png")
    for i in range(4):
        _png(d / ("keyframe_%d.png" % i))
    _png(d / "keyframe_0_doodle.png")          # 中间产物不影响判定
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: True)
    payload = q.write_image_product_quality(_script(4), work)
    assert payload["status"] == "PASS"
    assert payload["missing"] == []
