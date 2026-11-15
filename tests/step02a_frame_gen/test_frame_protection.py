# -*- coding: utf-8 -*-
"""Step D：受保护帧防删 + `fit_refs(dry_run=True)` 零写入的离线单测.

背景：`vimax_bridge` 的 B7 逻辑在「缓存命中但 QC critical」时会删帧+删哈希重抽。
对已人工批准/受保护的帧，这个删除必须被拦住（只留 sidecar 告警）；`--plan-only`
的参考图组合也不能再往正式 shots 目录写 `ref_compose_*.json`/参考表。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from shared import ref_compose
from shared import vimax_bridge


def _png(path: Path, color=(200, 40, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)
    return path


def _big_png(path: Path, color=(200, 40, 40)) -> Path:
    """≥5KB 的帧图（`_validate_frame` 的最小尺寸阈值）."""
    frame = _png(path, color)
    with open(frame, "ab") as f:
        f.write(b"\x00" * 6000)
    return frame


class _Logger:
    def __init__(self):
        self.lines: list[str] = []

    def verbose(self, msg, *a, **k):
        self.lines.append(str(msg))


def _shot(index: int = 2) -> dict:
    return {"index": index}


def test_marker_file_protects_whole_shot(tmp_path):
    frame = _png(tmp_path / "first_frame.png")
    (tmp_path / ".lock_frame").write_text("approved", encoding="utf-8")
    assert vimax_bridge._frame_is_protected(_shot(), frame, "first_frame") is True


def test_marker_file_protects_specific_frame_kind(tmp_path):
    frame = _png(tmp_path / "last_frame.png")
    (tmp_path / ".lock_frame_first_frame").write_text("x", encoding="utf-8")
    assert vimax_bridge._frame_is_protected(_shot(), frame, "last_frame") is False
    (tmp_path / ".lock_frame_last_frame").write_text("x", encoding="utf-8")
    assert vimax_bridge._frame_is_protected(_shot(), frame, "last_frame") is True


def test_env_shot_and_kind_protects(tmp_path, monkeypatch):
    frame = _png(tmp_path / "first_frame.png")
    monkeypatch.setenv("STEP02A_PROTECTED_FRAMES", "2:first_frame")
    assert vimax_bridge._frame_is_protected(_shot(2), frame, "first_frame") is True
    assert vimax_bridge._frame_is_protected(_shot(3), frame, "first_frame") is False
    assert vimax_bridge._frame_is_protected(_shot(2), frame, "last_frame") is False


def test_env_glob_protects(tmp_path, monkeypatch):
    frame = _png(tmp_path / "first_frame.png")
    monkeypatch.setenv("STEP02A_PROTECTED_FRAMES", "*first_frame.png")
    assert vimax_bridge._frame_is_protected(_shot(9), frame, "first_frame") is True


def test_protected_frame_is_not_deleted(tmp_path, monkeypatch):
    frame = _png(tmp_path / "first_frame.png")
    hash_path = tmp_path / "frame_hash.txt"
    hash_path.write_text("abc", encoding="utf-8")
    (tmp_path / ".lock_frame").write_text("x", encoding="utf-8")
    logger = _Logger()
    deleted = vimax_bridge._del_frame_unless_protected(
        _shot(), frame, hash_path, "首帧", 2, logger, "first_frame")
    assert deleted is False
    assert frame.exists() and hash_path.exists(), "受保护帧与哈希都不得被删除"
    assert any("受保护" in line for line in logger.lines)


def test_unprotected_frame_is_deleted(tmp_path, monkeypatch):
    monkeypatch.delenv("STEP02A_PROTECTED_FRAMES", raising=False)
    frame = _png(tmp_path / "first_frame.png")
    hash_path = tmp_path / "frame_hash.txt"
    hash_path.write_text("abc", encoding="utf-8")
    logger = _Logger()
    deleted = vimax_bridge._del_frame_unless_protected(
        _shot(), frame, hash_path, "首帧", 2, logger, "first_frame")
    assert deleted is True
    assert not frame.exists() and not hash_path.exists()


def test_fit_refs_dry_run_writes_nothing(tmp_path):
    src = tmp_path / "src"
    refs = [_png(src / ("%d.png" % i)) for i in range(4)]
    sheet_dir = tmp_path / "sheets"
    buckets = [("scene", refs[:2]), ("chars", refs[2:])]

    predicted = ref_compose.fit_refs(buckets, 2, sheet_dir, "t", dry_run=True)
    assert predicted, "dry-run 仍要给出会返回的路径"
    assert not sheet_dir.exists(), "dry-run 不得建目录/写参考表/写组合报告"

    composed = ref_compose.fit_refs(buckets, 2, sheet_dir, "t")
    assert composed
    assert sheet_dir.exists(), "非 dry-run 仍按旧行为落盘"


def test_plan_frame_state_does_not_write_sheets(tmp_path, monkeypatch):
    """`_plan_frame_state` 对超限参考图只预测路径，不往 shots 目录写任何文件."""
    from step02a_frame_gen import main as step02a_main

    shot_dir = tmp_path / "shots" / "2"
    shot_dir.mkdir(parents=True)
    frame = _png(shot_dir / "first_frame.png")
    (shot_dir / "frame_hash.txt").write_text("stale", encoding="utf-8")
    src = tmp_path / "src"
    refs = [_png(src / ("%d.png" % i)) for i in range(4)]
    monkeypatch.setattr("shared.vimax_bridge._collect_frame_refs_for_shot",
                        lambda *a, **k: {"buckets": [("scene", refs[:2]),
                                                     ("chars", refs[2:])],
                                         "omissions": []})
    monkeypatch.setattr("shared.ref_compose.active_image_ref_limit", lambda: 2)
    # 让哈希必然不匹配 → 返回 generate；关键断言是「不写盘」
    before = {p.name for p in shot_dir.iterdir()}
    state = step02a_main._plan_frame_state(_shot(2), shot_dir, [], "first_frame")
    assert state in ("cached", "generate")
    assert {p.name for p in shot_dir.iterdir()} == before
    assert not list(shot_dir.glob("ref_sheet_*"))
    assert not (shot_dir / "ref_compose_ff.json").exists()
    assert frame.exists()


# ---------------------------------------------------------------------------
# U7 收口（2026-11-15）：锁保帧防覆写——`.lock_frame` 不只拦删除，
# 哈希失配 / --force 的原位覆写也必须被拦住。
# ---------------------------------------------------------------------------

def test_keep_protected_frame_keeps_valid_locked_original(tmp_path):
    frame = _big_png(tmp_path / "first_frame.png")
    (tmp_path / ".lock_frame").write_text("approved", encoding="utf-8")
    logger = _Logger()
    assert vimax_bridge._keep_protected_frame(
        _shot(2), frame, "first_frame", logger, "首帧", 2) is True
    assert any("受保护" in line for line in logger.lines)


def test_keep_protected_frame_not_locked_or_missing(tmp_path):
    logger = _Logger()
    # 未锁：不拦
    frame = _png(tmp_path / "first_frame.png")
    assert vimax_bridge._keep_protected_frame(
        _shot(2), frame, "first_frame", logger, "首帧", 2) is False
    # 锁了但文件不存在：不拦
    (tmp_path / ".lock_frame").write_text("x", encoding="utf-8")
    assert vimax_bridge._keep_protected_frame(
        _shot(2), tmp_path / "first_frame.png", "first_frame", logger, "首帧", 2) is False


def test_keep_protected_frame_corrupt_original_falls_through(tmp_path):
    """锁了但原图损坏/过小（不可读）→ 保护不生效，允许照常重生成."""
    frame = tmp_path / "first_frame.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")  # 8 字节假 PNG，低于 _validate_frame 阈值
    (tmp_path / ".lock_frame").write_text("x", encoding="utf-8")
    logger = _Logger()
    assert vimax_bridge._keep_protected_frame(
        _shot(2), frame, "first_frame", logger, "首帧", 2) is False
    assert any("不可读" in line for line in logger.lines)


def test_plan_frame_state_reports_locked_without_generation(tmp_path):
    """锁定且原图可读 ⇒ plan 报 locked（真实运行零 POST），不计入 generate."""
    from step02a_frame_gen import main as step02a_main

    shot_dir = tmp_path / "shots" / "2"
    shot_dir.mkdir(parents=True)
    _big_png(shot_dir / "first_frame.png")
    (shot_dir / ".lock_frame").write_text("approved", encoding="utf-8")
    # 哈希文件缺失（或写个必然失配的值）都应报 locked 而非 generate
    state = step02a_main._plan_frame_state(_shot(2), shot_dir, [], "first_frame")
    assert state == "locked"
