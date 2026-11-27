# -*- coding: utf-8 -*-
"""S01: Baseline freeze, self-contained fixtures, and formal artifact protection."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from shared.quality_gate import verify_step01_commit_manifest
from tests._content_binding import assert_clean
from tests.integration.downstream_fixtures import (
    assert_tree_unchanged,
    build_anonymous_story_pack,
    install_committed_chapter,
    snapshot_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("variant", ["compact", "multi_speaker", "narration_and_dialogue"])
def test_anonymous_packs_commit_and_verify_independently(tmp_path, variant):
    pack = build_anonymous_story_pack(variant, chapter=2, book_name="测试卷B", book_index=2)
    ch_dir = tmp_path / "output" / pack["script"]["novel_name"] / "part_02_测试卷B" / "chapter_002"
    installed = install_committed_chapter(ch_dir, pack)
    manifest = verify_step01_commit_manifest(ch_dir, installed["script"], expected_chapter=2)
    assert manifest["status"] == "COMMITTED"
    assert manifest["script_revision"] == installed["revision"]


def test_downstream_entrypoints_fail_cleanly_without_writing_formal_outputs(tmp_path):
    """Missing Step01 inputs on an isolated chapter must fail without creating formal outputs."""
    out_base = tmp_path / "formal_out"
    ch_dir = out_base / "匿名作品" / "part_01_匿名测试卷" / "chapter_003"
    ch_dir.mkdir(parents=True, exist_ok=True)
    seed_file = ch_dir / "keep_me.txt"
    seed_file.write_text("do-not-touch", encoding="utf-8")
    before = snapshot_tree(out_base, ignore_parts=("_enhancement",))

    env = {
        "OUTPUT_BASE": str(out_base),
        "NOVEL_NAME": "匿名作品",
        "BOOK_INDEX": "1",
        "BOOK_NAME": "匿名测试卷",
        "NOVEL_CHAPTER": "3",
    }
    import os
    run_env = dict(os.environ)
    run_env.update(env)

    # Step02b --plan-only without script_result.json must exit non-zero and not touch formal files.
    cand_root = ch_dir / "_enhancement" / "exp_s01" / "video"
    proc_02b = subprocess.run(
        [sys.executable, "-m", "step02b_video_gen.main", "--chapter", "3",
         "--shots", "1", "--plan-only", "--candidate-root", str(cand_root)],
        cwd=str(REPO_ROOT), env=run_env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc_02b.returncode == 2
    assert_tree_unchanged(
        out_base, before, "formal output after step02b missing-script", ignore_parts=("_enhancement",),
    )

    # Step03 --plan-only without script_result.json must exit non-zero and not touch formal files.
    tts_root = ch_dir / "_enhancement" / "exp_s01" / "tts"
    proc_03 = subprocess.run(
        [sys.executable, "-m", "step03_tts_audio.main", "--chapter", "3",
         "--plan-only", "--candidate-root", str(tts_root)],
        cwd=str(REPO_ROOT), env=run_env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc_03.returncode != 0
    assert_tree_unchanged(
        out_base, before, "formal output after step03 missing-script", ignore_parts=("_enhancement",),
    )

    # Step04 --dry-run without script_result.json must exit non-zero and not touch formal files.
    merge_root = ch_dir / "_enhancement" / "exp_s01" / "merge"
    proc_04 = subprocess.run(
        [sys.executable, "-m", "step04_merge.main", "--chapter", "3",
         "--dry-run", "--candidate-root", str(merge_root)],
        cwd=str(REPO_ROOT), env=run_env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc_04.returncode != 0
    assert_tree_unchanged(
        out_base, before, "formal output after step04 missing-script", ignore_parts=("_enhancement",),
    )
    for rel in ("clips", "shots", "audio", "script_result.json", "final_video.mp4", "subtitles.srt"):
        assert not (ch_dir / rel).exists(), f"formal path leaked: {rel}"


def test_s01_files_have_zero_novel_binding_terms():
    for rel in (
        "tests/integration/downstream_fixtures.py",
        "tests/integration/test_s01_isolation_baseline.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert_clean(text, rel)
