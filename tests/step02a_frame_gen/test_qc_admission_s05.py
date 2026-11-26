# -*- coding: utf-8 -*-
"""S05 verification: digest/contract-bound QC, retry skip fix, product acceptance, --verify-only, and step02b gate."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _png(path: Path, color=(80, 120, 180)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)
    return path


def _write_qc(frame_path: Path, shot: dict, frame_kind: str, characters=None, *,
              has_hallucination: bool = False, ok: bool = True, kf_index: int | None = None,
              write_request: bool = True):
    from shared.image_qc import (
        QcIssue, QcResult, build_qc_frame_meta, write_frame_request_snapshot, write_sidecar,
    )

    if write_request:
        write_frame_request_snapshot(
            frame_path, shot=shot, frame_kind=frame_kind,
            prompt=str(shot.get("ff_desc") or shot.get("lf_desc") or "test"),
            refs=[], kf_index=kf_index,
        )
    issues = ([QcIssue(category="extra_person", description="人数不符", severity="critical")]
              if has_hallucination else [])
    res = QcResult(has_hallucination=has_hallucination, issues=issues, ok=ok, raw_result="",
                   error="" if ok else "qc_error")
    meta = build_qc_frame_meta(
        shot, frame_kind, characters or [], kf_index=kf_index,
        frame_path=(frame_path if write_request else None),
    )
    write_sidecar(frame_path, res, frame_meta=meta)


def _base_script():
    return {
        "title": "t",
        "book_name": "book",
        "book_index": 1,
        "chapter_index": 1,
        "script_revision": "a" * 64,
        "total_duration": 5.0,
        "characters": [{"identifier": "角色甲"}],
        "chapter_outline": {"narration_lines": []},
        "hook": None,
        "shots": [{
            "index": 1,
            "duration_sec": 5.0,
            "main_subject": "角色甲",
            "ff_desc": "角色甲在木案前擦拭铜秤。",
            "lf_desc": "角色甲放下铜秤转身。",
            "motion_desc": "角色甲转身",
            "prompt": "[中景] 角色甲转身",
            "narration_segment": "",
            "scene": "作坊",
            "location": "作坊",
            "clip_transition": "硬切",
            "context_bridge": "",
            "keyframes": [],
            "frame_layouts": {
                "ff": [{"name": "角色甲", "on_screen": True, "states": []}],
                "lf": [{"name": "角色甲", "on_screen": True, "states": []}],
            },
        }],
    }


def test_product_acceptance_blocks_qc_rejected_call_failed_stale_and_unrun(tmp_path, monkeypatch):
    from step02a_frame_gen import image_prompt_quality as q

    script = _base_script()
    shot = script["shots"][0]
    work = tmp_path / "working"
    d = work / "shots" / "1"
    ff = _png(d / "first_frame.png", (10, 20, 30))
    lf = _png(d / "last_frame.png", (30, 40, 50))
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: False)

    # 1. qc_not_run (no sidecar) -> file_ok=True, qc_ok=False, BLOCK
    rep_unrun = q.build_image_product_quality(script, work, media_policy="first_last")
    assert rep_unrun["file_ok"] is True
    assert rep_unrun["qc_ok"] is False
    assert rep_unrun["status"] == "BLOCK"
    assert any("qc=qc_not_run" in m for m in rep_unrun["missing"])

    # 2. qc_rejected on lf -> BLOCK even though PNGs exist & decode
    _write_qc(ff, shot, "first_frame", script["characters"], has_hallucination=False)
    _write_qc(lf, shot, "last_frame", script["characters"], has_hallucination=True)
    rep_rej = q.build_image_product_quality(script, work, media_policy="first_last")
    assert rep_rej["status"] == "BLOCK"
    assert rep_rej["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_passed"
    assert rep_rej["outputs"]["1"]["qc_states"]["lf"]["status"] == "qc_rejected"

    # 3. qc_call_failed -> BLOCK
    _write_qc(lf, shot, "last_frame", script["characters"], ok=False)
    rep_fail = q.build_image_product_quality(script, work, media_policy="first_last")
    assert rep_fail["status"] == "BLOCK"
    assert rep_fail["outputs"]["1"]["qc_states"]["lf"]["status"] == "qc_call_failed"

    # 4. Both qc_passed, then contract/image mutation -> qc_stale -> BLOCK
    _write_qc(lf, shot, "last_frame", script["characters"], has_hallucination=False)
    rep_ok = q.build_image_product_quality(script, work, media_policy="first_last")
    assert rep_ok["status"] == "PASS"
    assert rep_ok["schema_version"] == "step02a.product_acceptance/2"

    # Mutate image content -> image_changed -> qc_stale
    _png(lf, (250, 10, 10))
    rep_img_stale = q.build_image_product_quality(script, work, media_policy="first_last")
    assert rep_img_stale["status"] == "BLOCK"
    assert rep_img_stale["outputs"]["1"]["qc_states"]["lf"]["status"] == "qc_stale"

    # Restore lf qc_passed, then mutate frame_layouts contract -> qc_stale
    _write_qc(lf, shot, "last_frame", script["characters"], has_hallucination=False)
    shot["frame_layouts"]["lf"][0]["states"] = ["心口暗红"]
    rep_contract_stale = q.build_image_product_quality(script, work, media_policy="first_last")
    assert rep_contract_stale["status"] == "BLOCK"
    assert rep_contract_stale["outputs"]["1"]["qc_states"]["lf"]["status"] == "qc_stale"

    # first_only ignores lf even when lf is rejected/stale
    shot["frame_layouts"]["lf"][0]["states"] = []
    _write_qc(lf, shot, "last_frame", script["characters"], has_hallucination=True)
    rep_fo = q.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_fo["status"] == "PASS"


def test_require_gate_rejects_legacy_and_stale_step02a_reports(tmp_path, monkeypatch):
    from shared.quality_gate import QualityGateError, ensure_script_revision, require_gate
    from step02a_frame_gen import image_prompt_quality as q

    script = _base_script()
    ensure_script_revision(script)
    shot = script["shots"][0]
    work = tmp_path / "working"
    ff = _png(work / "shots" / "1" / "first_frame.png")
    _write_qc(ff, shot, "first_frame", script["characters"], has_hallucination=False)
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: False)

    rep = q.write_image_product_quality(script, work, media_policy="first_only")
    assert rep["status"] == "PASS"
    report_path = tmp_path / "step02a_quality.json"

    # Valid bound report passes require_gate
    require_gate(report_path, script, stage="step02a", required_report_type="product_acceptance",
                 verify_outputs=True, output_indices={1})

    # Swap sidecar on disk to qc_rejected after PASS report was written -> blocked!
    _write_qc(ff, shot, "first_frame", script["characters"], has_hallucination=True)
    with pytest.raises(QualityGateError):
        require_gate(report_path, script, stage="step02a", required_report_type="product_acceptance",
                     verify_outputs=True, output_indices={1})

    # Legacy v1 report without schema_version/qc_states -> blocked!
    legacy = json.loads(report_path.read_text(encoding="utf-8"))
    legacy.pop("schema_version", None)
    legacy["outputs"]["1"].pop("qc_states", None)
    report_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(QualityGateError):
        require_gate(report_path, script, stage="step02a", required_report_type="product_acceptance",
                     verify_outputs=True, output_indices={1})


def test_maybe_qc_frame_skipped_settled_rejected_returns_critical(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared import vimax_bridge as vb

    script = _base_script()
    shot = script["shots"][0]
    ff = _png(tmp_path / "first_frame.png")
    _write_qc(ff, shot, "first_frame", script["characters"], has_hallucination=True)

    events = []
    monkeypatch.setattr("shared.request_events.emit", lambda status, **kw: events.append((status, kw)))
    monkeypatch.setattr(iq, "QC_ENABLED", True)

    is_critical = asyncio.run(
        vb._maybe_qc_frame(shot, str(ff), "first_frame", script["characters"], False)
    )
    assert is_critical is True
    assert events and events[0][0] == "qc_rejected"
