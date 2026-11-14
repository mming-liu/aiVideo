# -*- coding: utf-8 -*-
"""`tools/diagnose_video_gaps.py` 的回归（夹具驱动，零网络、零副作用）.

这个脚本是「这条镜头为什么没有成片」的唯一离线裁判：它读
`shots/<idx>/video.mp4`、`clips/clip_<idx>.mp4` 与 `step02.log`，把缺片归因到
提交/终局/远端/下载四类环节。判据错一格，运维就会去重跑一条本来只需补拷的镜头，
或者把远端失败当成"没提交"重烧一次额度。

夹具是**协议字符串**（`[Shot N] Task submitted` / `Processing` 等日志格式）+ 假
mp4 文件（按 20KB 阈值判定存在性），不含任何具体作品内容；换书/换章不影响本文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import diagnose_video_gaps as tool                       # noqa: E402

BIG = 25 * 1024      # > 20KB 阈值：判「在」
SMALL = 1024         # < 20KB：判「不在」

#: `[Shot N]` 日志行 → 期望分类。每行的文案就是生产里 `_submit_video`/`_poll_video` 写的。
LOG = "\n".join([
    "[Shot 4] Task submitted id=abc",
    "[Shot 4] 生成失败：远端返回错误",
    "[Shot 5] Task submitted id=def",
    "[Shot 5] Processing 40%",
    "[Shot 5] Processing 80%",
    "[Shot 6] Task submitted id=ghi",
    "[Shot 8] Task submitted id=jkl",
    "[Shot 8] Processing 10%",
    "[Shot 8] 下载失败：连接重置",
]) + "\n"


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


@pytest.fixture()
def chapter(tmp_path: Path) -> Path:
    ch = tmp_path / "chapter_001"
    (ch / "vimax_working" / "shots").mkdir(parents=True)
    _write(ch / "step02.log", 0)
    (ch / "step02.log").write_text(LOG, encoding="utf-8")

    _write(ch / "vimax_working" / "shots" / "1" / "video.mp4", BIG)   # 本体+副本
    _write(ch / "clips" / "clip_1.mp4", BIG)

    _write(ch / "vimax_working" / "shots" / "2" / "video.mp4", BIG)   # 只缺副本

    _write(ch / "clips" / "clip_3.mp4", BIG)                          # 只缺本体

    _write(ch / "vimax_working" / "shots" / "4" / "video.mp4", SMALL)  # 本体过小
    _write(ch / "clips" / "clip_4.mp4", SMALL)

    for idx in (5, 6, 8):
        _write(ch / "vimax_working" / "shots" / str(idx) / "video.mp4", SMALL)
        _write(ch / "clips" / f"clip_{idx}.mp4", SMALL)
    return ch


def _classify(chapter: Path, idx: int) -> dict:
    return tool.classify(chapter, idx, tool._read_log(chapter))


@pytest.mark.parametrize("idx,expected", [
    (1, "clip_ok"),
    (2, "only_clip_missing"),
    (3, "video_missing_clip_ok"),
    (4, "remote_failed"),
    (5, "submitted_no_terminal"),
    (6, "submitted_no_poll"),
    (7, "not_submitted"),
    (8, "download_failed"),
])
def test_classification_matrix(chapter, idx, expected):
    assert _classify(chapter, idx)["classification"] == expected


def test_clip_ok_wins_over_log_evidence(chapter):
    """本体与副本都在时，日志里哪怕有失败行也不该报失败——文件是最终事实。"""
    _write(chapter / "vimax_working" / "shots" / "4" / "video.mp4", BIG)
    _write(chapter / "clips" / "clip_4.mp4", BIG)
    assert _classify(chapter, 4)["classification"] == "clip_ok"


def test_size_threshold_is_inclusive_of_the_boundary(chapter):
    """正好 20KB 不算「在」（判据是 `> 20 * 1024`），否则半截文件会被当成成片。"""
    _write(chapter / "vimax_working" / "shots" / "9" / "video.mp4", 20 * 1024)
    _write(chapter / "clips" / "clip_9.mp4", 20 * 1024)
    row = _classify(chapter, 9)
    assert row["video_present"] is False and row["clip_present"] is False
    assert row["classification"] == "not_submitted"


def test_poll_count_and_log_entries_are_reported(chapter):
    row = _classify(chapter, 5)
    assert row["poll_count"] == 2
    assert row["submitted"] is True
    # `log_entries` 是**该镜**的行数（提交 + 2 条 Processing），不是整份日志行数。
    assert row["log_entries"] == 3
    assert row["last_log_line"].startswith("[Shot 5] Processing 80%")


def test_shot_prefixes_do_not_cross_match(chapter):
    """`[Shot 1]` 不能命中 `[Shot 10]`——前缀匹配会让 1 继承 10 的日志。"""
    (chapter / "step02.log").write_text("[Shot 10] Task submitted id=x\n", encoding="utf-8")
    assert _classify(chapter, 1)["submitted"] is False


def test_main_infers_shots_from_clips_and_log(chapter, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["diagnose_video_gaps.py", "--chapter-dir", str(chapter)])
    assert tool.main() == 0
    payload = json.loads(capsys.readouterr().out)
    idxs = [row["shot"] for row in payload["shots"]]
    assert idxs == [1, 3, 4, 5, 6, 8]


def test_main_honours_explicit_shots(chapter, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["diagnose_video_gaps.py", "--chapter-dir",
                                      str(chapter), "--shots", "2,7"])
    assert tool.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["classification"] for row in payload["shots"]] == [
        "only_clip_missing", "not_submitted"]


def test_main_refuses_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["diagnose_video_gaps.py", "--chapter-dir",
                                      str(tmp_path / "nope")])
    with pytest.raises(SystemExit):
        tool.main()
