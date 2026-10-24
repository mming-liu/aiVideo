# -*- coding: utf-8 -*-
"""冻结核验入口的输入身份测试（不调用模型）."""
import hashlib
import json
from pathlib import Path

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from tests.step01_script_gen.test_create_voice import _view_for
from tools import verify_frozen_candidate as v


def _valid_record(view, digest, novel_digest):
    fact = (view.get("frozen_facts") or [{}])[0]
    uid = str(fact.get("uid"))
    claim = str(fact.get("claim") or "")
    carrier = ""
    for _b in view.get("beats") or []:
        if claim and claim in str(_b.get("text") or ""):
            carrier = str(_b.get("uid") or "")
            break
    carriers = [_c for _c in (fact.get("carriers") or [])]
    if not carrier and carriers:
        carrier = str(carriers[0].get("uid") or carriers[0].get("beat_uid") or "")
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": digest, "novel_digest": novel_digest,
        "source_support": [{"fact_uid": uid, "verdict": "supported",
                            "evidence_spans": [claim], "reason": ""}],
        "facts": [{"fact_uid": uid, "verdict": "supported",
                   "support_spans": [claim], "missing": "",
                   "suggested_carriers": [],
                   "obligations": [{
                       "obligation_id": "claim", "text": claim,
                       "verdict": "supported",
                       "evidence": [{"carrier_uid": carrier,
                                     "exhibitor_span": claim}],
                       "missing": "", "reason": ""}]}],
        "voice_units": [
            {"unit_uid": str(_u.get("uid")), "verdict": "supported",
             "source_kind": ("novel_speech" if _k == "dialogue_units"
                             else "novel_narration"),
             "speaker": ("乙" if _k == "dialogue_units" else ""),
             "echo_text": str(_u.get("text") or ""), "reason": ""}
            for _k in ("dialogue_units", "narration_units")
            for _u in (view.get(_k) or [])],
    }


def _correction_fixture(tmp_path):
    manifest_path, manifest, plan, novel = _write_fixture(tmp_path)
    digest = manifest["candidate"]["plan_digest"]
    nd = manifest["novel"]["digest"]
    failed = {"schema": cv.SCHEMA_VERIFICATION, "status": "bad_response",
              "plan_digest": digest, "novel_digest": nd,
              "error": "义务字段不合法",
              "problems": [{"code": "verify_obligation_unknown_field",
                            "path": "facts[0].obligations[0]", "reason": "x"}]}
    _failed = tmp_path / "failed.json"
    _failed.write_text(json.dumps(failed, ensure_ascii=False), encoding="utf-8")
    _prev = tmp_path / "previous.json"
    _prev.write_text(json.dumps({"raw_response_text": "OLD-RESPONSE"},
                                ensure_ascii=False), encoding="utf-8")
    return manifest_path, manifest, plan, novel, _failed, _prev


def _fixture_view():
    v = _view_for(existing=True)
    v["frozen_events"] = ["e01", "e02", "e03"]
    v["beats"].append({
        "uid": "b03", "event_uids": ["e03"], "text": "乙走进屋",
        "purpose": "", "evidence": "乙走进屋", "source": {},
        "voice_ref": {"kind": "dialogue", "uid": "d01"}})
    v["dialogue_units"] = [{"uid": "d01", "text": "乙走进屋。", "source": {}}]
    v["fact_bindings"] = [{"fact_uid": "f01", "carriers": [
        {"kind": "visual", "beat_uid": "b02"}]}]
    return v


def _plan():
    p = pc.plan_from_view(_fixture_view())
    p["main_events"] = ["甲留在门外", "乙进屋", "乙走进屋"]
    return p


def _write_fixture(tmp_path, *, novel_format="sample", novel_override=None):
    novel = novel_override or "甲留在门外。乙走进屋。我进屋了。"
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps({
        "schema": "step01.sample.v1",
        "scenario": "p2",
        "novel": novel,
        "plan": _plan(),
        "expected": {"problem_codes": ["verify_fact_unsupported"]},
    }, ensure_ascii=False), encoding="utf-8")
    plan = _plan()
    cand = tmp_path / "candidate_plan.json"
    cand.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    run = tmp_path / "revision"
    run.mkdir()
    (run / "candidate_plan.json").write_text(json.dumps(plan, ensure_ascii=False),
                                             encoding="utf-8")
    if novel_format == "novel_text":
        novel_path = tmp_path / "novel.txt"
        novel_path.write_text(novel, encoding="utf-8")
    else:
        novel_path = sample_path
    manifest = {
        "schema": v.MANIFEST_SCHEMA,
        "candidate": {"path": str(cand), "plan_digest": cv.plan_digest_of(plan)},
        "novel": {"path": str(novel_path), "format": novel_format,
                  "digest": hashlib.sha256(novel.encode()).hexdigest()},
        "revision_run": str(run),
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                             encoding="utf-8")
    return manifest_path, manifest, plan, novel


def test_sample_input_uses_only_sample_novel_and_writes_identity(tmp_path):
    manifest_path, manifest, plan, novel = _write_fixture(tmp_path)
    out = v.run(manifest_path, root=tmp_path / "runs", dry=True)
    assert out["sends"] == 0
    assert out["candidate_digest"] == manifest["candidate"]["plan_digest"]
    text = (Path(out["run_dir"]) / "extracted_novel.txt").read_text(encoding="utf-8")
    assert text == novel
    assert "expected" not in text and "problem_codes" not in text
    ident = json.loads((Path(out["run_dir"]) / "input_identity.json").read_text(
        encoding="utf-8"))
    assert ident["novel_format"] == "sample"
    assert ident["novel_digest"] == manifest["novel"]["digest"]


def test_novel_text_input_is_read_as_plain_body(tmp_path):
    manifest_path, manifest, _, novel = _write_fixture(
        tmp_path, novel_format="novel_text")
    info = v.preflight(manifest_path)
    assert info["novel_text"] == novel
    assert info["novel_format"] == "novel_text"


def test_whole_sample_file_digest_is_rejected_before_service(tmp_path, monkeypatch):
    manifest_path, manifest, _, _ = _write_fixture(tmp_path)
    # 复现本次错误：把样本包文件摘要当成 novel 摘要。
    manifest["novel"]["digest"] = hashlib.sha256(
        Path(manifest["novel"]["path"]).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                             encoding="utf-8")
    _called = []
    monkeypatch.setattr(o, "create_service", lambda: _called.append(1))
    with pytest.raises(v.InputIdentityError):
        v.run(manifest_path, root=tmp_path / "runs")
    assert _called == []
    assert not (tmp_path / "runs").exists() or not any(
        (tmp_path / "runs").glob("verify_*"))


def test_candidate_digest_mismatch_is_rejected_before_service(tmp_path, monkeypatch):
    manifest_path, manifest, _, _ = _write_fixture(tmp_path)
    manifest["candidate"]["plan_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                             encoding="utf-8")
    _called = []
    monkeypatch.setattr(o, "create_service", lambda: _called.append(1))
    with pytest.raises(v.InputIdentityError):
        v.run(manifest_path, root=tmp_path / "runs")
    assert _called == []


def test_revision_run_identity_mismatch_is_rejected(tmp_path):
    manifest_path, manifest, _, _ = _write_fixture(tmp_path)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"main_events": ["different"]}), encoding="utf-8")
    (tmp_path / "revision" / "candidate_plan.json").write_text(
        json.dumps({"main_events": ["different"]}), encoding="utf-8")
    with pytest.raises(v.InputIdentityError):
        v.preflight(manifest_path)


def _patch_provider(monkeypatch):
    try:
        from _text_gen.config import config
        monkeypatch.setattr(config, "PROVIDER", "agent_plan", raising=False)
    except Exception:
        pass


def test_correction_replays_old_response_and_accepts_complete_new_record(
        tmp_path, monkeypatch):
    _patch_provider(monkeypatch)
    manifest_path, manifest, plan, novel, failed, previous = _correction_fixture(tmp_path)
    calls = []

    def _fake(view, digest, *, novel_text, novel_digest, correction=None):
        calls.append({"novel_text": novel_text, "novel_digest": novel_digest,
                      "correction": correction})
        return _valid_record(view, digest, novel_digest)

    out = v.run(manifest_path, root=tmp_path / "runs",
                correction_record=failed, previous_response=previous,
                service_verify=_fake)
    assert len(calls) == 1, calls
    assert calls[0]["novel_text"] == novel
    assert calls[0]["correction"]["previous_response"] == "OLD-RESPONSE"
    assert calls[0]["correction"]["problems"][0]["code"] == \
        "verify_obligation_unknown_field"
    assert out["record_status"] == "ok", out
    assert out["acceptance_codes"] == [], out
    assert out["corrections"] == 1, out


def test_correction_bad_response_is_not_retried(tmp_path, monkeypatch):
    _patch_provider(monkeypatch)
    manifest_path, manifest, plan, novel, failed, previous = _correction_fixture(tmp_path)
    calls = []

    def _fake(view, digest, *, novel_text, novel_digest, correction=None):
        calls.append(correction)
        return {"schema": cv.SCHEMA_VERIFICATION, "status": "bad_response",
                "facts": [], "source_support": [], "voice_units": []}

    out = v.run(manifest_path, root=tmp_path / "runs2",
                correction_record=failed, previous_response=previous,
                service_verify=_fake)
    assert len(calls) == 1, calls
    assert out["record_status"] == "bad_response", out
    _rec = json.loads((Path(out["run_dir"]) / "record.json").read_text(
        encoding="utf-8"))
    assert _rec.get("code") == "verify_bad_response", _rec


def test_correction_dry_mode_does_not_send(tmp_path):
    manifest_path, manifest, plan, novel, failed, previous = _correction_fixture(tmp_path)
    out = v.run(manifest_path, root=tmp_path / "runs3", dry=True,
                correction_record=failed, previous_response=previous)
    assert out["dry"] is True and out["sends"] == 0
    assert (Path(out["run_dir"]) / "correction_input.json").is_file()
