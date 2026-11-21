# -*- coding: utf-8 -*-
"""S12：媒体清理只在提交 **COMMITTED** 之后执行（2026-11-21）.

旧实现把音频 / `vimax` / `stretched` / 成片的删除放在唯一提交点**之前**：提交门一旦
BLOCK，正式稿还是旧版，配套媒体却已被删——下游 step02 要付费重做。现在删除收口到
`_cleanup_stale_media_after_commit(status, …)`，由提交状态决定动作。
"""
from __future__ import annotations

import inspect

import step01_script_gen.main as m


def _media_root(tmp_path):
    return {
        "audio": tmp_path / "audio",
        "vimax": tmp_path / "vimax",
        "vimax_shots": tmp_path / "vimax" / "shots",
        "stretched": tmp_path / "stretched",
        "stretched_shots": tmp_path / "stretched" / "shots",
        "final": tmp_path / "final.mp4",
        "subtitles": tmp_path / "subtitles.srt",
    }


def _patch_paths(monkeypatch, root):
    monkeypatch.setattr(m._mrt, "audio_dir", lambda: root["audio"])
    monkeypatch.setattr(m._mrt, "vimax_working_dir", lambda: root["vimax"])
    monkeypatch.setattr(m._mrt, "vimax_shots_dir", lambda: root["vimax_shots"])
    monkeypatch.setattr(m._mrt, "stretched_dir", lambda: root["stretched"])
    monkeypatch.setattr(m._mrt, "final_file", lambda: root["final"])
    monkeypatch.setattr(m._mrt, "subtitles_file", lambda: root["subtitles"])


def _seed(root, shot_ids=(1, 2, 3, 4)):
    for name in ("audio", "vimax", "stretched"):
        root[name].mkdir(parents=True, exist_ok=True)
        (root[name] / "old.bin").write_text("x", encoding="utf-8")
    for base in ("vimax_shots", "stretched_shots"):
        for sid in shot_ids:
            d = root[base] / str(sid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "clip.mp4").write_text("x", encoding="utf-8")
    root["final"].write_text("x", encoding="utf-8")
    root["subtitles"].write_text("x", encoding="utf-8")


def test_block_keeps_every_media_artifact(tmp_path, monkeypatch):
    root = _media_root(tmp_path)
    _seed(root)
    _patch_paths(monkeypatch, root)
    m._cleanup_stale_media_after_commit("BLOCK")
    for name in ("audio", "vimax", "stretched"):
        assert root[name].is_dir(), "BLOCK 时删了 %s/" % name
        assert (root[name] / "old.bin").is_file()
    for sid in (1, 2, 3, 4):
        assert (root["vimax_shots"] / str(sid)).is_dir()
    assert root["final"].is_file() and root["subtitles"].is_file()


def test_committed_without_preserve_cleans_all_media(tmp_path, monkeypatch):
    root = _media_root(tmp_path)
    _seed(root)
    _patch_paths(monkeypatch, root)
    m._cleanup_stale_media_after_commit("COMMITTED")
    for name in ("audio", "vimax", "stretched"):
        assert not root[name].exists(), "COMMITTED 后未清理 %s/" % name
    assert not root["final"].exists() and not root["subtitles"].exists()


def test_committed_with_preserve_keeps_unchanged_shots_only(tmp_path, monkeypatch):
    root = _media_root(tmp_path)
    _seed(root, shot_ids=(1, 2, 3, 4))
    _patch_paths(monkeypatch, root)
    m._cleanup_stale_media_after_commit(
        "COMMITTED", preserve_shots=[1, 2], changed_shots=[3], removed_shots=[4])
    for base in ("vimax_shots", "stretched_shots"):
        assert (root[base] / "1").is_dir(), "未变镜头被删了"
        assert (root[base] / "2").is_dir(), "未变镜头被删了"
        assert not (root[base] / "3").exists(), "changed 镜头的旧产物未清理"
        assert not (root[base] / "4").exists(), "removed 镜头的旧产物未清理"
    # 逐镜保留模式下不做全量清理（音频等仍可能被未变镜头复用）
    assert root["audio"].is_dir()


def test_main_calls_cleanup_only_via_the_post_commit_decision_point():
    """接线核对：主生成路径在提交点之后调用清理助手（不是提交前直接删）."""
    src = inspect.getsource(m.main)
    commit_at = src.rindex("_commit_step01_outputs(")
    # 用**调用形态**定位（注释里也提到助手名，不能用裸函数名）
    cleanup_at = src.index("_cleanup_stale_media_after_commit(\n            "
                          "_commit_summary.get(\"status\")")
    assert cleanup_at > commit_at, "媒体清理仍在提交点之前"
    assert "preserve_shots=_preserve_shots" in src
