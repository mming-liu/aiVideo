# -*- coding: utf-8 -*-
"""Offline tests for Phase 2b vision QC HTTP budget separation and ref head-crop parity."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image


def _png(path: Path, color=(80, 90, 100), size=(64, 64)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return str(path.resolve())


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload

    def close(self):
        pass


def test_qc_retries_zero_caps_http_attempts_and_separates_metrics(tmp_path, monkeypatch):
    from shared import image_qc, request_events
    from tools.step02a_metrics_report import summarize_step02a_events

    frame = Path(_png(tmp_path / "shot_001" / "first_frame.png"))
    ev_file = tmp_path / "step02_events.jsonl"

    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setenv("QC_VISION_HTTP_RETRIES", "0")
    monkeypatch.setenv("QC_VISION_MAX_HTTP_ATTEMPTS", "0")
    monkeypatch.setenv("AGENT_PLAN_TEXT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("POST_EVENTS_FILE", str(ev_file))

    calls: list[dict] = []

    class _FakeSession429:
        def post(self, url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": dict(headers or {}), "prompt": json["messages"][0]["content"][1]["text"]})
            return _FakeResp(429, text="rate limited")

    monkeypatch.setattr(image_qc, "_get_qc_session", lambda: _FakeSession429())
    image_qc.reset_qc_http_attempts()

    with pytest.raises(RuntimeError, match="429"):
        image_qc._opencode_vision_chat(frame, "test prompt")
    assert len(calls) == 1
    assert image_qc.get_qc_http_attempts() == 1

    # Full 3-layer QC with QC_VISION_HTTP_RETRIES=0 must take at most 3 HTTP attempts per image.
    calls.clear()
    image_qc.reset_qc_http_attempts()
    monkeypatch.setattr(image_qc, "write_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(image_qc, "should_run_qc", lambda *a, **k: True)
    monkeypatch.setattr(image_qc, "read_frame_request_snapshot", lambda *a, **k: None)

    responses = [
        json.dumps({"has_hallucination": False, "issues": []}),
        json.dumps([{"label": "person", "desc": "char A sitting on chair", "bbox": [200, 200, 600, 800]}]),
        json.dumps({"answers": [{"index": 1, "answer": "yes", "reason": "sitting on chair"}]}),
    ]

    class _FakeSession3Calls:
        def post(self, url, headers=None, json=None, timeout=None):
            idx = len(calls)
            calls.append({"idx": idx})
            return _FakeResp(200, {"choices": [{"message": {"content": responses[idx]}}]})

    monkeypatch.setattr(image_qc, "_get_qc_session", lambda: _FakeSession3Calls())

    shot = {
        "index": 1,
        "ff_desc": "甲端坐于木椅上",
        "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}]},
        "claims": [{"id": "c1", "q": "甲正端坐在木椅上吗？", "expect": "yes", "key": True}],
    }
    res = image_qc.maybe_qc_sync(
        str(frame),
        shot=shot,
        frame_kind="first_frame",
        characters=[{"identifier": "甲"}],
        prompt="甲端坐于木椅上",
    )
    assert res.ok is True
    assert len(calls) == 3
    assert image_qc.get_qc_http_attempts() == 3

    # Mix in a synthetic image generation http_attempt and assert metrics keep counts separate.
    request_events.emit("http_attempt", request_id="img-1", operation_id="op-img-1", kind="image_gen")
    events = request_events.read_events(ev_file)
    report = summarize_step02a_events(events)
    assert report["request_sent_events"] == 1
    assert report["http_attempt_events"] == 1
    assert report["vision_qc_http_attempts"] == 4  # 1 (429) + 3 (QC layers)


def test_qc_max_http_attempts_blocks_before_send(tmp_path, monkeypatch):
    from shared import image_qc

    frame = Path(_png(tmp_path / "shot_001" / "first_frame.png"))
    monkeypatch.setenv("QC_VISION_HTTP_RETRIES", "0")
    monkeypatch.setenv("QC_VISION_MAX_HTTP_ATTEMPTS", "2")
    monkeypatch.setenv("AGENT_PLAN_TEXT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("POST_EVENTS_FILE", str(tmp_path / "events.jsonl"))

    sent = []

    class _FakeSessionOk:
        def post(self, url, headers=None, json=None, timeout=None):
            sent.append(url)
            return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(image_qc, "_get_qc_session", lambda: _FakeSessionOk())
    image_qc.reset_qc_http_attempts()

    assert image_qc._opencode_vision_chat(frame, "p1") == "ok"
    assert image_qc._opencode_vision_chat(frame, "p2") == "ok"
    with pytest.raises(RuntimeError, match="HTTP 发送上限"):
        image_qc._opencode_vision_chat(frame, "p3")
    assert len(sent) == 2
    assert image_qc.get_qc_http_attempts() == 2


def test_last_frame_head_crop_parity_and_readonly_plan_state(tmp_path, monkeypatch):
    import shared.vimax_bridge as vb

    char_dir = tmp_path / "global" / "characters_bank"
    a_base = _png(char_dir / "甲" / "base.png")
    a_face = _png(char_dir / "甲" / "face.png")
    b_base = _png(char_dir / "乙" / "base.png")
    sa = _png(tmp_path / "global" / "style_bank" / "style_anchor.png")
    shot_dir = tmp_path / "shots" / "1"
    ff_img = _png(shot_dir / "first_frame.png")

    bank = {
        "甲": {"base": a_base, "face": a_face, "approved": True},
        "乙": {"base": b_base, "approved": True},
    }
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / "characters_bank.json").write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
    (work_dir / "character_portraits_registry.json").write_text(
        json.dumps({
            "甲": {"base": {"path": a_base, "approved": True}, "face": {"path": a_face, "approved": True}},
            "乙": {"base": {"path": b_base, "approved": True}},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("shared.paths.characters_bank_dir", lambda *a, **k: char_dir)
    monkeypatch.setattr("shared.paths.scenes_bank_dir", lambda *a, **k: tmp_path / "global" / "scenes_bank")
    monkeypatch.setattr("shared.paths.props_bank_dir", lambda *a, **k: tmp_path / "global" / "props_bank")
    monkeypatch.setattr("shared.paths.style_bank_dir", lambda *a, **k: tmp_path / "global" / "style_bank")
    monkeypatch.setattr("shared.paths.vimax_working_dir", lambda *a, **k: work_dir)
    monkeypatch.setattr(vb, "_resolve_style_anchor_path", lambda *a, **k: sa)

    chars = [{"identifier": "甲"}, {"identifier": "乙"}]
    shot = {
        "index": 1,
        "ff_desc": "甲端坐着",
        "lf_desc": "甲微微抬手",
        "motion_desc": "甲微微抬手",
        "frame_layouts": {
            "ff": [{"name": "甲", "on_screen": True}],
            "lf": [{"name": "甲", "on_screen": True}],
        },
    }

    # First frame uses face.png via default crop=True
    ff_refs = vb._collect_frame_refs_for_shot(shot, chars, limit=5, for_frame="首帧")
    assert a_face in ff_refs and a_base not in ff_refs

    # Readonly dry-run with apply_head_crop=True, create_head_crop=False picks face.png for 甲 and writes no portrait_crops for 乙
    ro_shot = {
        "index": 2,
        "ff_desc": "甲与乙同桌而坐",
        "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}, {"name": "乙", "on_screen": True}]},
    }
    ro_info = vb._collect_frame_refs_for_shot(
        ro_shot, chars, limit=5, for_frame="首帧",
        return_buckets=True, apply_head_crop=True, create_head_crop=False,
    )
    char_bucket = dict(ro_info["buckets"])["chars"]
    assert a_face in char_bucket
    assert b_base in char_bucket
    assert not (tmp_path / "work" / "portrait_crops").exists()

    # Live gen_shot_frames must request last_frame with apply_head_crop=True (face.png parity!)
    captured_lf_refs: list[str] = []

    async def _fake_ff(*a, **k):
        return ff_img

    async def _fake_lf(*a, reference_paths=None, **k):
        captured_lf_refs.extend(list(reference_paths or []))
        return _png(shot_dir / "last_frame.png")

    monkeypatch.setattr(vb, "shot_work_dir", lambda idx: shot_dir)
    monkeypatch.setattr(vb, "_gen_first_frame_async", _fake_ff)
    monkeypatch.setattr(vb, "_gen_last_frame_async", _fake_lf)

    res = asyncio.run(vb.gen_shot_frames(
        shot, tmp_path / "shots", object(), None, characters=chars, frames={"ff", "lf"},
    ))
    assert res["lf_path"]
    assert a_face in captured_lf_refs
    assert a_base not in captured_lf_refs


def test_support_chair_patch_removes_tabletop_conflict_from_prompt_and_qc():
    from shared import image_qc, vimax_bridge as vb

    chars = [{"identifier": "角色甲", "appearance": "青衣青年"}]
    bad_shot = {
        "index": 1,
        "shot_size": "中全景",
        "location": "室内大厅",
        "ff_desc": "室内大厅，一桌两椅。两人围桌端坐交谈。",
        "lf_desc": "室内大厅，角色甲站起。",
        "motion_desc": "两人围桌端坐交谈。",
        "frame_layouts": {
            "ff": [
                {
                    "name": "角色甲",
                    "on_screen": True,
                    "pose": "坐在桌北椅上，身体前倾",
                    "support": {"id": "table_1", "kind": "圆木桌", "surface": "桌面"},
                }
            ],
            "lf": [
                {
                    "name": "角色甲",
                    "on_screen": True,
                    "pose": "站于椅前",
                    "support": {"id": "floor_indoor", "kind": "室内地面", "surface": "地面"},
                }
            ],
        },
        "claims": [],
    }
    bad_prompt = vb.compose_frame_prompt(bad_shot, chars, "first_frame", desc=bad_shot["ff_desc"])
    bad_q = " ".join(str(q.get("q") or "") for q in image_qc.build_targeted_questions(bad_shot, "first_frame"))
    assert "位于圆木桌的桌面" in bad_prompt
    assert "圆木桌的桌面上" in bad_q

    fixed_shot = {
        **bad_shot,
        "frame_layouts": {
            **bad_shot["frame_layouts"],
            "ff": [
                {
                    "name": "角色甲",
                    "on_screen": True,
                    "pose": "坐在桌北椅上，身体前倾",
                    "support": {"id": "chair_north", "kind": "木桌旁木椅", "surface": "椅面"},
                }
            ],
        },
    }
    fixed_prompt = vb.compose_frame_prompt(fixed_shot, chars, "first_frame", desc=fixed_shot["ff_desc"])
    fixed_q = " ".join(str(q.get("q") or "") for q in image_qc.build_targeted_questions(fixed_shot, "first_frame"))
    assert "圆木桌的桌面" not in fixed_prompt
    assert "桌面上" not in fixed_q
    assert "木桌旁木椅" in fixed_prompt
    assert "木桌旁木椅的椅面上" in fixed_q


def test_semantic_qc_wraps_generation_prompt_and_disk_snapshot_with_json_contract(tmp_path, monkeypatch):
    from shared import image_qc

    frame = Path(_png(tmp_path / "shots" / "1" / "first_frame.png"))
    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setattr(image_qc, "should_run_qc", lambda *a, **k: True)
    monkeypatch.setattr(image_qc, "_detect_entities", lambda p: [{"label": "person", "desc": "char", "bbox": [0, 0, 50, 50]}])
    monkeypatch.setattr(image_qc, "build_targeted_questions", lambda *a, **k: [])

    captured: list[str] = []

    def _fake_chat(path, prompt):
        captured.append(prompt)
        return json.dumps({"has_hallucination": False, "has_unwanted_text": False, "issues": []})

    monkeypatch.setattr(image_qc, "_opencode_vision_chat", _fake_chat)

    shot = {
        "index": 1,
        "scene": "室内大厅",
        "ff_desc": "角色甲端坐木椅",
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True}]},
    }
    raw_gen = "高清二次元画风，角色甲端坐木椅，温暖光线，9:16竖屏"

    # Case 1: explicit raw generation prompt passed into maybe_qc_sync
    res1 = image_qc.maybe_qc_sync(
        str(frame), shot, "first_frame", [{"identifier": "角色甲"}], False, prompt=raw_gen,
    )
    assert res1.ok is True
    assert len(captured) == 1
    assert "你是专业图片质检员" in captured[0]
    assert '{"has_hallucination":' in captured[0]
    assert raw_gen in captured[0]

    # Case 2: prompt=None with persisted disk request snapshot containing raw generation prompt
    image_qc.write_frame_request_snapshot(
        frame, shot=shot, frame_kind="first_frame", prompt="SNAPSHOT-GEN-PROMPT-CONTENT", refs=[],
    )
    captured.clear()
    res2 = image_qc.maybe_qc_sync(
        str(frame), shot, "first_frame", [{"identifier": "角色甲"}], False, prompt=None,
    )
    assert res2.ok is True
    assert len(captured) == 1
    assert "你是专业图片质检员" in captured[0]
    assert '{"has_hallucination":' in captured[0]
    assert "SNAPSHOT-GEN-PROMPT-CONTENT" in captured[0]


@pytest.mark.parametrize(
    "raw_response",
    [
        "",
        "   \n",
        "{broken json",
        "画面清晰正常，没有看到任何问题。",
        "图中存在轻微漂浮与融合现象，但不符合 JSON。",
        "如需调整画面细节，我可以帮您重新生成。",
        "{\"foo\": \"bar\"}",
        "{\"has_hallucination\": \"no\", \"issues\": []}",
        "{\"has_hallucination\": false, \"issues\": \"none\"}",
        "{\"has_hallucination\": false, \"issues\": [\"bad_string_item\"]}",
        "{\"issues\": [{}]}",
        "{\"issues\": [{\"category\": \"face_consistency\"}]}",
        "{\"issues\": [{\"description\": \"五官模糊\"}]}",
        "{\"issues\": [{\"category\": \"   \", \"description\": \"五官模糊\"}]}",
        "{\"issues\": [{\"category\": \"face_consistency\", \"description\": \"   \"}]}",
        "{\"issues\": [{\"category\": \"face_consistency\", \"description\": \"模糊\", \"severity\": 123}]}",
        "{\"has_hallucination\": false, \"issues\": [{\"foo\": \"bar\"}]}",
    ],
)
def test_parse_verdict_and_sidecar_fail_closed_on_malformed_or_non_json(tmp_path, raw_response):
    from shared import image_qc

    parsed = image_qc.parse_verdict(raw_response)
    assert parsed.ok is False, f"Expected ok=False for {raw_response!r}, got {parsed}"
    assert parsed.has_hallucination is False

    frame = Path(_png(tmp_path / "f.png"))
    shot = {"index": 1, "ff_desc": "甲端坐", "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}]}}
    image_qc.write_frame_request_snapshot(frame, shot=shot, frame_kind="first_frame", prompt="甲端坐", refs=[])
    meta = image_qc.build_qc_frame_meta(shot, "first_frame", [{"identifier": "甲"}], frame_path=frame)
    image_qc.write_sidecar(frame, parsed, frame_meta=meta)
    state = image_qc.qc_state(frame, expected_frame=meta, require_request_snapshot=True)
    assert state["status"] == "qc_call_failed"


def test_parse_verdict_accepts_valid_and_fenced_json_verdicts():
    from shared import image_qc

    clean = image_qc.parse_verdict('```json\n{"has_hallucination": false, "has_unwanted_text": false, "issues": []}\n```')
    assert clean.ok is True
    assert clean.has_hallucination is False

    # Legal optional null/omitted fields remain accepted
    null_issues = image_qc.parse_verdict('{"has_hallucination": false, "issues": null}')
    assert null_issues.ok is True
    assert null_issues.has_hallucination is False
    assert null_issues.issues == []

    opt_sev = image_qc.parse_verdict(json.dumps({
        "has_hallucination": False,
        "issues": [{"category": "prompt_semantics", "description": "minor tint shift", "severity": None}],
    }))
    assert opt_sev.ok is True
    assert opt_sev.has_hallucination is False
    assert len(opt_sev.issues) == 1 and opt_sev.issues[0].severity == "warning"

    omitted_top = image_qc.parse_verdict(json.dumps({
        "issues": [{"category": "text_watermark", "description": "sky text"}],
    }))
    assert omitted_top.ok is True
    assert omitted_top.has_hallucination is True
    assert omitted_top.issues[0].severity == "critical"

    warn_only = image_qc.parse_verdict(json.dumps({
        "has_hallucination": False,
        "issues": [{"category": "prompt_semantics", "description": "slight color shift", "severity": "warning"}],
    }))
    assert warn_only.ok is True
    assert warn_only.has_hallucination is False
    assert len(warn_only.issues) == 1 and warn_only.issues[0].severity == "warning"

    text_crit = image_qc.parse_verdict(json.dumps({
        "has_hallucination": False,
        "has_unwanted_text": True,
        "issues": [],
    }))
    assert text_crit.ok is True
    assert text_crit.has_hallucination is True
    assert any(i.category == "text_watermark" and i.severity == "critical" for i in text_crit.issues)


def test_qc_rule_version_bump_invalidates_prior_version_and_reuses_current(tmp_path, monkeypatch):
    from shared import image_qc

    frame = Path(_png(tmp_path / "shots" / "1" / "first_frame.png"))
    shot = {"index": 1, "ff_desc": "甲端坐木椅", "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}]}}
    chars = [{"identifier": "甲"}]
    image_qc.write_frame_request_snapshot(frame, shot=shot, frame_kind="first_frame", prompt="甲端坐木椅", refs=[])

    # 1. Historical sidecar created under old 2026-11-26.f1 prompt version
    monkeypatch.setattr(image_qc, "QC_PROMPT_VERSION", "2026-11-26.f1")
    old_meta = image_qc.build_qc_frame_meta(shot, "first_frame", chars, frame_path=frame)
    image_qc.write_sidecar(frame, image_qc.QcResult(has_hallucination=False, issues=[], ok=True), frame_meta=old_meta)
    assert image_qc.should_run_qc(frame, expected_frame=old_meta) is False

    # Restore current bumped version: old sidecar must become qc_stale and require re-run
    monkeypatch.undo()
    cur_meta = image_qc.build_qc_frame_meta(shot, "first_frame", chars, frame_path=frame)
    assert cur_meta["qc_rule_version"] != old_meta["qc_rule_version"]
    st_stale = image_qc.qc_state(frame, expected_frame=cur_meta, require_request_snapshot=True)
    assert st_stale["status"] == "qc_stale"
    assert st_stale["reason"] == "frame_qc_rule_version_mismatch"
    assert image_qc.should_run_qc(frame, expected_frame=cur_meta) is True

    # 2. Entry point runs new QC, rejects invalid issue {"issues":[{}]}, writes qc_call_failed (never PASS)
    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setattr(image_qc, "_detect_entities", lambda p: [{"label": "person", "desc": "甲", "bbox": [0, 0, 50, 50]}])
    monkeypatch.setattr(image_qc, "build_targeted_questions", lambda *a, **k: [])

    calls: list[str] = []

    def _chat_bad_issue(path, prompt):
        calls.append(prompt)
        return '{"issues": [{}]}'

    monkeypatch.setattr(image_qc, "_opencode_vision_chat", _chat_bad_issue)
    res_fail = image_qc.maybe_qc_sync(frame, shot, "first_frame", chars, False, prompt="甲端坐木椅")
    assert res_fail.ok is False
    assert len(calls) == 1
    assert "你是专业图片质检员" in calls[0]
    image_qc.write_sidecar(frame, res_fail, frame_meta=cur_meta)
    assert image_qc.qc_state(frame, expected_frame=cur_meta, require_request_snapshot=True)["status"] == "qc_call_failed"
    assert image_qc.should_run_qc(frame, expected_frame=cur_meta) is True

    # 3. Entry point gets valid current JSON verdict -> qc_passed and subsequent call reuses cache (0 chat calls)
    calls.clear()
    monkeypatch.setattr(
        image_qc,
        "_opencode_vision_chat",
        lambda path, prompt: calls.append(prompt) or '{"has_hallucination": false, "has_unwanted_text": false, "issues": []}',
    )
    res_ok = image_qc.maybe_qc_sync(frame, shot, "first_frame", chars, False, prompt="甲端坐木椅")
    assert res_ok.ok is True
    assert res_ok.has_hallucination is False
    assert len(calls) == 1
    image_qc.write_sidecar(frame, res_ok, frame_meta=cur_meta)
    assert image_qc.qc_state(frame, expected_frame=cur_meta, require_request_snapshot=True)["status"] == "qc_passed"
    assert image_qc.should_run_qc(frame, expected_frame=cur_meta) is False

    calls.clear()
    res_cached = image_qc.maybe_qc_sync(frame, shot, "first_frame", chars, False, prompt="甲端坐木椅")
    assert res_cached.error == "skipped"
    assert len(calls) == 0

