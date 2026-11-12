# -*- coding: utf-8 -*-
"""S07：候选消费前重验、跨章/错 revision 拒收、影子比较与切换清单门禁."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from shared import enhancement_io as eio
from shared.candidate_inputs import (
    CandidateInputError,
    revalidate_candidate_inputs,
    resolve_candidate_inputs,
)
from tools.shadow_compare_step01_blockers import build_report

ROOT = Path(__file__).resolve().parents[2]


def _manifest(root: Path, *, chapter: int = 1, revision: str = "rev-s07") -> tuple[Path, Path]:
    video = root / "clip_1.mp4"
    video.write_bytes(b"s07-video" * 200)
    payload = {
        "schema_version": eio.SCHEMA_ATTEMPT_MANIFEST,
        "package": "video",
        "scope": {"chapter": chapter},
        "script_revision": revision,
        "source": "s07_fixture",
        "units": [{
            "unit_id": "shot-1", "shot_index": 1, "shot_id": "shot-1",
            "path": str(video), "sha256": eio.sha256_file(video),
            "window": {"start_sec": 0.0, "end_sec": 2.0},
            "media": {"duration_sec": 2.0},
        }],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, video


def test_cross_chapter_and_wrong_revision_rejected_before_consume(tmp_path):
    manifest, _ = _manifest(tmp_path, chapter=1, revision="rev-s07")
    with pytest.raises(CandidateInputError) as exc:
        resolve_candidate_inputs(manifest, expect_scope={"chapter": 2},
                                 expect_script_revision="rev-s07",
                                 require_accepted=False)
    assert exc.value.code == "scope_mismatch"
    with pytest.raises(CandidateInputError) as exc:
        resolve_candidate_inputs(manifest, expect_scope={"chapter": 1},
                                 expect_script_revision="other-rev",
                                 require_accepted=False)
    assert exc.value.code == "script_revision_mismatch"


def test_second_revalidation_rejects_rewritten_artifact(tmp_path):
    manifest, video = _manifest(tmp_path)
    resolved = resolve_candidate_inputs(manifest, expect_scope={"chapter": 1},
                                        expect_script_revision="rev-s07",
                                        require_accepted=False)
    assert revalidate_candidate_inputs(
        resolved, expect_scope={"chapter": 1},
        expect_script_revision="rev-s07") is resolved
    video.write_bytes(b"rewritten-after-resolve")
    with pytest.raises(CandidateInputError) as exc:
        revalidate_candidate_inputs(resolved)
    assert exc.value.code == "candidate_digest_mismatch"


def test_revalidation_manifest_digest_and_missing_file(tmp_path):
    manifest, video = _manifest(tmp_path)
    resolved = resolve_candidate_inputs(manifest, expect_scope={"chapter": 1},
                                        expect_script_revision="rev-s07",
                                        require_accepted=False)
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CandidateInputError) as exc:
        revalidate_candidate_inputs(resolved)
    assert exc.value.code == "manifest_digest_mismatch"
    # 重新解析后删产物：消费前必须报缺失
    resolved = resolve_candidate_inputs(manifest, expect_scope={"chapter": 1},
                                        expect_script_revision="rev-s07",
                                        require_accepted=False)
    video.rename(tmp_path / "clip_1.removed")
    with pytest.raises(CandidateInputError) as exc:
        revalidate_candidate_inputs(resolved)
    assert exc.value.code == "candidate_missing"


def test_concurrent_revalidation_is_stable(tmp_path):
    manifest, _ = _manifest(tmp_path)
    resolved = resolve_candidate_inputs(manifest, expect_scope={"chapter": 1},
                                        expect_script_revision="rev-s07",
                                        require_accepted=False)
    results = []
    errors = []

    def _worker():
        try:
            results.append(revalidate_candidate_inputs(resolved)["script_revision"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results == ["rev-s07"] * 4


def test_shadow_comparison_covers_all_blockers_and_mutations(tmp_path):
    report = build_report(tmp_path / "shadow")
    assert report["summary"]["baseline_all_green"] is True
    assert report["summary"]["mutation_all_detected"] is True
    assert set(report["families"]) == {"temporal_ellipsis", "offscreen_voice", "voice_timing"}
    for family, data in report["families"].items():
        assert data["sides"]["baseline"]["semantic_codes"] == []
        assert data["sides"]["baseline"]["visual_plan_status"] == "planned"
        if family != "voice_timing":
            assert data["sides"]["mutation"]["visual_plan_status"] == "blocked"
    codes = set(report["families"]["voice_timing"]["sides"]["mutation"]["voice_codes"])
    assert codes == {"audio_window_overflow", "native_dialogue_unverified"}
    rows = {row["id"]: row for row in report["blockers"]}
    assert {"D1", "S-01", "S-02", "S-03", "S-04", "S-05", "S-06"} <= set(rows)
    for blocker_id in ("S-01", "S-02", "S-03", "S-04"):
        assert rows[blocker_id]["received"] is True
        assert rows[blocker_id]["final_disposition"] == "downstream_detected_and_routed"
    assert rows["S-05"]["downstream_receipts"][0]["mutation_codes"] == [
        "audio_window_overflow", "native_dialogue_unverified"]
    assert (tmp_path / "shadow" / "shadow_compare.json").is_file()
    assert (tmp_path / "shadow" / "shadow_compare.md").is_file()


def test_switch_checklist_is_checklist_only():
    path = ROOT / "docs" / "step01-layered-execution" / "S07-formal-gate-switch-checklist.md"
    assert path.is_file(), "S07 必须产出正式门禁切换清单"
    text = path.read_text(encoding="utf-8")
    assert "不执行切换" in text
    for blocker_id in ("D1", "S-01", "S-03", "S-05"):
        assert blocker_id in text
