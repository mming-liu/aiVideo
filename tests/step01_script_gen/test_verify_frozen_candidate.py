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


def _plan():
    return pc.plan_from_view(_view_for(existing=True))


def _write_fixture(tmp_path, *, novel_format="sample", novel_override=None):
    novel = novel_override or "甲留在门外。乙说：“我进屋了。”"
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
