# -*- coding: utf-8 -*-
"""Phase 2A offline verification (G1-G4, 0 external calls)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import requests
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _block_net():
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


def _png(path: Path, color=(80, 120, 180)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)
    return path


def test_g1a_multi_char_human_guard_dedupe_no_contradiction():
    from step01_script_gen.prompt_semantic import NO_ANIMAL_FEATURES_GUARD
    from shared.prompt_contradiction import assert_no_contradictions
    from shared.vimax_bridge import compose_frame_prompt

    chars = [
        {"identifier": "角色甲", "static_features": "圆脸，戴折角纱巾", "dynamic_features": "青衫"},
        {"identifier": "角色乙", "static_features": "方脸", "dynamic_features": "直掇，襟角掖在腰带"},
    ]
    shot = {
        "index": 1,
        "location": "作坊",
        "ff_desc": "两人围桌而坐。",
        "frame_layouts": {
            "ff": [
                {"name": "角色甲", "on_screen": True, "position": "画左", "pose": "端坐", "states": []},
                {"name": "角色乙", "on_screen": True, "position": "画右", "pose": "端坐", "states": []},
            ]
        },
    }
    prompt = compose_frame_prompt(shot, chars, "first_frame")
    assert prompt.count(NO_ANIMAL_FEATURES_GUARD) == 1
    assert_no_contradictions(prompt, "multi-char")


def test_g1b_location_deliteralized_and_no_text_constraint():
    from shared.prompt_contradiction import assert_no_contradictions
    from shared.style_prefix import visual_location_hint
    from shared.vimax_bridge import compose_frame_prompt

    assert visual_location_hint("清溪镇水湾") == "水湾"
    assert visual_location_hint("陈府别庄") == "别庄"
    assert visual_location_hint("作坊") == "作坊"

    chars = [{"identifier": "甲", "static_features": "白衣青年", "dynamic_features": "素白长袍"}]
    shot = {
        "index": 5,
        "location": "清溪镇水湾",
        "ff_desc": "甲仰卧船头。",
        "lf_desc": "水面剧烈震荡，甲仰卧不动。",
        "frame_layouts": {"lf": [{"name": "甲", "on_screen": True, "position": "画中", "pose": "仰卧", "states": []}]},
    }
    lf_prompt = compose_frame_prompt(shot, chars, "last_frame")
    assert "清溪镇" not in lf_prompt
    assert "额外地名标题" in lf_prompt
    assert "画面任意区域无任何文字" not in lf_prompt
    assert_no_contradictions(lf_prompt, "b4-replay")


def test_g1c_prop_synonym_fishing_rod_matches(tmp_path, monkeypatch):
    from shared import vimax_bridge as vb

    prop_img = _png(tmp_path / "props_bank" / "钓杆" / "prop.png")
    (tmp_path / "props_bank" / "props_bank.json").write_text(
        json.dumps({"props": {"钓杆": {"category": "渔具", "consistent_terms": ["钓杆"], "image": str(prop_img)}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("shared.paths.props_bank_dir", lambda: tmp_path / "props_bank")
    refs = vb._collect_prop_asset_refs(
        {"index": 3, "ff_desc": "甲左手握钓竿垂湖面。", "location": "水湾"},
        limit=2,
        frame_desc="甲左手握钓竿垂湖面。",
    )
    assert str(prop_img) in refs


def test_g1d_prompt_contradiction_writes_blocked_preflight_ledger(tmp_path, monkeypatch):
    from shared import request_events as re_ev
    from shared import vimax_bridge as vb
    from shared.prompt_contradiction import PromptContradictionError

    events_file = tmp_path / "step02_events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(vb, "compose_frame_prompt", lambda *a, **k: "重复片段X" * 3)
    monkeypatch.setattr(vb, "_build_final_frame_prompt", lambda *a, **k: "dup")

    import shared.prompt_contradiction as pc
    monkeypatch.setattr(pc, "assert_no_contradictions", lambda *a, **k: (_ for _ in ()).throw(PromptContradictionError("dup block")))

    class _AdapterNever:
        async def generate_single_image(self, **kwargs):
            raise AssertionError("must not send POST on PromptContradictionError")

    shot = {"index": 1, "ff_desc": "甲坐着。", "keyframes": [], "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}]}}
    with pytest.raises(PromptContradictionError):
        asyncio.run(vb._gen_first_frame_async(_AdapterNever(), tmp_path / "shots", shot, force=True, characters=[{"identifier": "甲"}]))

    ledgers = [e for e in re_ev.read_events(events_file) if e.get("event") == "decision_ledger"]
    assert len(ledgers) == 1
    assert ledgers[0]["action_type"] == "blocked_preflight"
    assert ledgers[0]["defect_class"] == vb.FAILURE_REQUEST_CONFLICT
    assert ledgers[0]["attempt_index"] == 0


def test_g2a_warning_only_does_not_reject_and_syncs_ledger(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared import request_events as re_ev
    from shared import vimax_bridge as vb

    events_file = tmp_path / "step02_events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(iq, "QC_ENABLED", True)
    monkeypatch.setattr(iq, "QC_WRITE_CRITICAL", True)
    monkeypatch.setattr(vb, "compose_frame_prompt", lambda *a, **k: "安全提示词")
    monkeypatch.setattr(vb, "_build_final_frame_prompt", lambda *a, **k: "安全提示词")

    parsed = iq.parse_verdict(json.dumps({
        "has_hallucination": True,
        "issues": [
            {"category": "prompt_semantics", "description": "w1", "severity": "warning"},
            {"category": "subject_consistency", "description": "w2", "severity": "warning"},
        ],
    }))
    assert parsed.has_hallucination is False

    monkeypatch.setattr(iq, "maybe_qc_sync", lambda *a, **k: iq.QcResult(
        has_hallucination=False,
        issues=[
            iq.QcIssue("prompt_semantics", "w1", "warning"),
            iq.QcIssue("prompt_semantics", "w2", "warning"),
        ],
        ok=True, raw_result="",
    ))

    class _AdapterOk:
        async def generate_single_image(self, **kwargs):
            import os
            from vimax.interfaces import ImageOutput
            raw = os.urandom(128 * 128 * 3)
            return ImageOutput(fmt="pil", ext="png", data=Image.frombytes("RGB", (128, 128), raw))

    shot = {"index": 2, "lf_desc": "甲站立。", "keyframes": [], "frame_layouts": {"lf": [{"name": "甲", "on_screen": True}]}}
    out = asyncio.run(vb._gen_last_frame_async(_AdapterOk(), tmp_path / "shots", shot, force=True, characters=[{"identifier": "甲"}]))
    assert out is not None
    state = iq.qc_state(Path(out))
    assert state["status"] == "qc_passed"
    assert state["data"]["verdict_detail"] == "warning_only_pass"

    ledgers = [e for e in re_ev.read_events(events_file) if e.get("event") == "decision_ledger"]
    assert ledgers and ledgers[-1]["qc_verdict"] == "qc_passed"
    assert ledgers[-1]["detail"] == "warning_only_pass"


def test_g2b_unwanted_text_watermark_is_hard_critical(tmp_path, monkeypatch):
    from shared import image_qc as iq

    monkeypatch.setenv("QC_PROVIDER", "opencode")
    b4_fake = _png(tmp_path / "b4_watermark.png", (100, 120, 140))

    # Case 1: Layer 3 has_unwanted_text=true (even if model marks warning) -> forced critical
    monkeypatch.setattr(iq, "_opencode_vision_chat", lambda p, pr: json.dumps({
        "has_hallucination": False,
        "has_unwanted_text": True,
        "issues": [{"category": "text_watermark", "description": "天空有大字", "severity": "warning"}],
    }))
    monkeypatch.setattr(iq, "_detect_entities", lambda p: [{"label": "person", "desc": "甲", "bbox": [10, 10, 50, 50]}])
    monkeypatch.setattr(iq, "_ask_yes_no", lambda p, qs: {q["q"]: "no" if q.get("category") == "text_watermark" else "yes" for q in qs})

    shot = {"index": 5, "lf_desc": "甲仰卧。", "frame_layouts": {"lf": [{"name": "甲", "on_screen": True}]}}
    res = iq.maybe_qc_sync(b4_fake, shot, "last_frame", [{"identifier": "甲"}])
    assert res.ok is True
    assert res.has_hallucination is True
    assert any(i.category == "text_watermark" and i.severity == "critical" for i in res.issues)


def test_g2c_ssl_session_rebuild_and_recheck_failed_qc(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared import request_events as re_ev
    from step02a_frame_gen import verify_acceptance as va

    img = _png(tmp_path / "frame.png")
    monkeypatch.setenv("AGENT_PLAN_TEXT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("QC_VISION_HTTP_RETRIES", "2")
    monkeypatch.setattr("time.sleep", lambda *_: None)

    created_sessions = []

    class _FakeResp:
        status_code = 200
        headers = {}

        def json(self):
            return {"choices": [{"message": {"content": '{"has_hallucination": false, "issues": []}'}}]}

        def close(self):
            pass

    class _FakeSession:
        def __init__(self):
            self.idx = len(created_sessions)
            self.closed = False
            created_sessions.append(self)

        def post(self, *args, **kwargs):
            if self.idx == 0:
                raise requests.exceptions.SSLError("SSLEOFError(8, UNEXPECTED_EOF_WHILE_READING)")
            return _FakeResp()

        def close(self):
            self.closed = True

    monkeypatch.setattr(requests, "Session", _FakeSession)
    iq._QC_SESSION = None
    iq._QC_SESSION_DIRTY = False

    content = iq._opencode_vision_chat(img, "check")
    assert "has_hallucination" in content
    assert len(created_sessions) == 2
    assert created_sessions[0].closed is True

    # Recheck CLI helper targets ONLY qc_call_failed frames, 0 image posts
    events_file = tmp_path / "events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)

    work = tmp_path / "work"
    s1_ff = _png(work / "shots" / "1" / "first_frame.png")
    s1_lf = _png(work / "shots" / "1" / "last_frame.png")
    script = {
        "characters": [{"identifier": "甲"}],
        "shots": [{"index": 1, "ff_desc": "a", "lf_desc": "b", "keyframes": [],
                   "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}], "lf": [{"name": "甲", "on_screen": True}]}}],
    }
    sp = tmp_path / "script_result.json"
    sp.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    meta_ff = iq.build_qc_frame_meta(script["shots"][0], "first_frame", script["characters"])
    meta_lf = iq.build_qc_frame_meta(script["shots"][0], "last_frame", script["characters"])
    iq.write_sidecar(s1_ff, iq.QcResult(False, [], ok=False, error="ssl fail"), frame_meta=meta_ff)
    iq.write_sidecar(s1_lf, iq.QcResult(False, [], ok=True), frame_meta=meta_lf)

    rechecked_paths = []
    monkeypatch.setattr(iq, "QC_ENABLED", True)
    monkeypatch.setattr(iq, "maybe_qc_sync", lambda p, *a, **k: rechecked_paths.append(Path(p).name) or iq.QcResult(False, [], ok=True))

    rep = va.recheck_failed_qc(sp, work)
    assert rep["image_posts"] == 0
    assert rep["counts"]["checked"] == 1
    assert rep["counts"]["qc_passed"] == 1
    assert rechecked_paths == ["first_frame.png"]
    assert iq.qc_state(s1_ff, expected_frame=meta_ff)["status"] == "qc_passed"
    assert any(e.get("action_type") == "qc_recheck" for e in re_ev.read_events(events_file))


def test_g3_style_anchor_injection_and_pre_shot_asset_gate(tmp_path, monkeypatch):
    from shared import vimax_bridge as vb
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    sbank_dir = tmp_path / "global" / "style_bank"
    sa = _png(sbank_dir / "style_anchor.png", (12, 34, 56))
    (sbank_dir / "style_bank.json").write_text(
        json.dumps({"video_style": "gufeng", "is_locked": True}), encoding="utf-8")
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    monkeypatch.setattr("shared.paths.style_bank_dir", lambda: sbank_dir)
    monkeypatch.setattr("shared.paths.vimax_working_dir", lambda: tmp_path / "work")
    monkeypatch.setattr("shared.paths.characters_bank_dir", lambda: tmp_path / "global" / "characters_bank")
    monkeypatch.setattr(vb, "_collect_scene_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_collect_space_object_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_collect_prop_asset_refs", lambda *a, **k: [])

    chars = [{"identifier": "角色丙"}]
    shot = {
        "index": 1,
        "ff_desc": "角色丙端坐。",
        "frame_layouts": {"ff": [{"name": "角色丙", "on_screen": True}]},
    }
    refs_info = vb._collect_frame_refs_for_shot(shot, chars, for_frame="首帧", return_buckets=True)
    assert refs_info["style_anchor_used"] is True
    assert str(sa.resolve()) in refs_info["buckets"][0][1]

    gate_blocked = check_pre_shot_asset_gate({"characters": chars, "shots": [shot]}, only_indices={1}, frames={"ff"})
    assert gate_blocked["status"] == "blocked"
    assert gate_blocked["missing_portraits"][0]["character"] == "角色丙"

    cb_dir = tmp_path / "global" / "characters_bank"
    cb_dir.mkdir(parents=True, exist_ok=True)
    (cb_dir / "characters_bank.json").write_text(json.dumps({
        "characters": {"角色丙": {
            "identifier": "角色丙", "video_style": "gufeng",
            "portraits": {"base": {
                "path": str((cb_dir / "角色丙" / "base.png").resolve()),
                "video_style": "gufeng"}},
        }},
    }, ensure_ascii=False), encoding="utf-8")
    _png(tmp_path / "global" / "characters_bank" / "角色丙" / "base.png")
    gate_ok = check_pre_shot_asset_gate({"characters": chars, "shots": [shot]}, only_indices={1}, frames={"ff"})
    assert gate_ok["status"] == "ok"


def test_g4_metrics_report_summarizes_events():
    from tools.step02a_metrics_report import summarize_step02a_events

    rep = summarize_step02a_events([
        {"event": "request_sent"},
        {
            "event": "decision_ledger",
            "chapter_scope": "part_01/chapter_001",
            "shot_index": 2,
            "frame_role": "last_frame",
            "model_id": "doubao-seedream-5-0-pro-260628",
            "ref_strategy": "multi_ref_contract",
            "shot_complexity": "two_person",
            "attempt_index": 1,
            "action_type": "generate",
            "defect_class": "none",
            "qc_verdict": "qc_passed",
            "human_usability": "usable",
            "non_target_damage": False,
        },
        {
            "event": "decision_ledger",
            "chapter_scope": "part_01/chapter_002",
            "shot_index": 1,
            "frame_role": "first_frame",
            "model_id": "doubao-seedream-5-0-pro-260628",
            "ref_strategy": "text_only",
            "shot_complexity": "multi_person",
            "attempt_index": 0,
            "action_type": "blocked_preflight",
            "defect_class": "request_conflict",
            "qc_verdict": "not_run",
            "human_usability": "pending",
            "non_target_damage": False,
        },
    ])
    assert rep["decision_ledger_entries"] == 2
    assert rep["buckets"]["doubao-seedream-5-0-pro-260628|multi_ref_contract|two_person"]["requests_per_usable_frame"] == 1.0
    assert rep["buckets"]["doubao-seedream-5-0-pro-260628|text_only|multi_person"]["unresolved_entries"] == 1
