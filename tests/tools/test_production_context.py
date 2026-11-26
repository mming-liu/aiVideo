# -*- coding: utf-8 -*-
"""production_context：凭据 -> 环境 -> 核验 的最小离线用例.

只测本模块自己的判据（身份、摘要、revision、章目录），不依赖生产 output 目录：
夹具在 tmp_path 里现搭一个"已提交"的 step01 章目录，三份产物 + commit manifest
由正式构造器 build_step01_commit_manifest 生成，所以摘要与真实提交同构。

注意 revision 必须由 ensure_script_revision() 算出：质量门核的是**语义载荷摘要**，
不是手填的字符串（手填会在 require_gate 处被判 stale）。
"""
from __future__ import annotations

import json
import os

import pytest

from tools import production_context as pc


@pytest.fixture()
def clean_env(monkeypatch):
    """把本模块会写的键先清空，用例结束后自动还原（apply_context 直接改 os.environ）。"""
    for key in pc.CONTEXT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _write_chapter(root, *, chapter=1):
    from shared import config, quality_gate
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    ch = root / "output" / "novel" / "part_01_book" / ("chapter_%03d" % chapter)
    ch.mkdir(parents=True)
    script = {"shots": [{"index": 1}], "timeline": [{"index": 1}],
              "hook": {"index": 0, "prompt": "x"}, "chapter_index": chapter,
              "total_duration": 60.0}
    revision = quality_gate.ensure_script_revision(script)
    (ch / "script_result.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    band = config.duration_band()
    quality = {"status": "PASS", "report_type": "quality_gate", "script_revision": revision,
               "rule_version": quality_gate.QUALITY_RULE_VERSION,
               "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT, "duration_band": band}
    (ch / "step01_quality.json").write_text(json.dumps(quality, ensure_ascii=False), encoding="utf-8")
    audit = {"status": "ok", "input_revision": revision,
             "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
             "audit_scope": "final_after_deterministic_fixes", "duration_band": band,
             "issues": []}
    (ch / "step01_audit.json").write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    manifest = quality_gate.build_step01_commit_manifest(
        run_id="run-test", created_at="2026-10-31T00:00:00+08:00", revision=revision,
        rule_source_fingerprint=RULE_SOURCE_FINGERPRINT,
        rule_version=quality_gate.QUALITY_RULE_VERSION,
        audit_scope="final_after_deterministic_fixes", chapter_dir=ch, status="COMMITTED")
    quality_gate.write_step01_commit_manifest(ch, manifest)
    return ch, script


def _receipt(root, ch, *, revision=None, sha=None, chapter=1):
    import hashlib
    script_text = (ch / "script_result.json").read_bytes()
    digest = sha or hashlib.sha256(script_text).hexdigest()
    if revision is None:
        revision = json.loads(script_text.decode("utf-8")).get("script_revision")
    return {
        "schema": pc.RECEIPT_SCHEMA,
        "status": "adopted_and_verified",
        "formal_chapter": str(ch),
        "output_base_override": str(ch.parents[2]),
        "script_revision": revision,
        "scope": {"novel": "novel", "book_index": 1, "book_name": "book", "chapter": chapter},
        "formal_sha256": {"script_result.json": digest},
        "effective_config": {},
    }


def test_verify_passes_and_resolves_formal_chapter(tmp_path, clean_env):
    ch, _script = _write_chapter(tmp_path)
    receipt = _receipt(tmp_path, ch)
    result = pc.verify(receipt)
    assert result["chapter_dir"].lower() == str(ch).lower()
    assert result["script_revision"] == receipt["script_revision"]
    assert result["commit_status"] == "COMMITTED"
    assert result["hook_present"] is True


def test_context_env_carries_scope_and_pins_duration_band(tmp_path, clean_env):
    ch, _script = _write_chapter(tmp_path)
    receipt = _receipt(tmp_path, ch)
    receipt["effective_config"] = {"DURATION_MAX_SEC": "120", "HOOK_ENABLED": "1"}
    env = pc.context_env(receipt)
    assert env["NOVEL_CHAPTER"] == "1"
    assert env["BOOK_NAME"] == "book"
    assert env["OUTPUT_BASE"] == str(ch.parents[2])
    assert env["DURATION_MAX_SEC"] == "120"
    assert env["HOOK_ENABLED"] == "1"


def test_tampered_script_is_rejected_before_gate(tmp_path, clean_env):
    ch, _script = _write_chapter(tmp_path)
    receipt = _receipt(tmp_path, ch)
    payload = json.loads((ch / "script_result.json").read_text(encoding="utf-8"))
    payload["title"] = "改动过的正文"
    (ch / "script_result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pc.ContextError) as exc:
        pc.verify(receipt)
    assert exc.value.code == "script_digest_mismatch"


def test_revision_mismatch_is_reported(tmp_path, clean_env):
    ch, _script = _write_chapter(tmp_path)
    receipt = _receipt(tmp_path, ch)
    receipt["script_revision"] = "other-revision"
    with pytest.raises(pc.ContextError) as exc:
        pc.verify(receipt)
    assert exc.value.code == "revision_mismatch"


def test_chapter_dir_mismatch_is_reported(tmp_path, clean_env):
    ch, _script = _write_chapter(tmp_path)
    _write_chapter(tmp_path, chapter=2)
    receipt = _receipt(tmp_path, ch)
    # 指向另一章的输出根：formal_chapter 仍是 ch，但 paths 会解析到 chapter_002
    receipt["scope"]["chapter"] = 2
    with pytest.raises(pc.ContextError) as exc:
        pc.verify(receipt)
    assert exc.value.code == "chapter_dir_mismatch"


def test_receipt_schema_is_enforced(tmp_path):
    ch, _script = _write_chapter(tmp_path)
    bad = _receipt(tmp_path, ch)
    bad["schema"] = "something/else"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pc.ContextError) as exc:
        pc.load_receipt(path)
    assert exc.value.code == "receipt_schema_mismatch"


# ---------------------------------------------------------------------------
# 结构校验与读盘分离（2026-11-14）
# ---------------------------------------------------------------------------
def test_validate_receipt_checks_a_dict_without_a_file(tmp_path):
    """`validate_receipt` 只吃字典：写凭据的一方要在**落盘前**就能验结构."""
    ch, _script = _write_chapter(tmp_path)
    good = _receipt(tmp_path, ch)
    assert pc.validate_receipt(good) is good
    with pytest.raises(pc.ContextError) as exc:
        pc.validate_receipt(dict(good, status="pending"))
    assert exc.value.code == "receipt_not_adopted"
    with pytest.raises(pc.ContextError) as exc:
        pc.validate_receipt(["not", "a", "receipt"])
    assert exc.value.code == "receipt_not_object"


def test_validate_receipt_does_not_need_the_file_to_exist(tmp_path):
    """不落盘校验：目录里没有任何文件也能判结构与缺字段（错误里带不出路径也不影响）."""
    receipt = {"schema": pc.RECEIPT_SCHEMA, "status": "adopted_and_verified",
               "formal_chapter": str(tmp_path)}
    assert tmp_path.is_dir() and not list(tmp_path.iterdir())
    with pytest.raises(pc.ContextError) as exc:
        pc.validate_receipt(receipt)
    assert exc.value.code == "receipt_field_missing"
    assert exc.value.fields.get("field") == "output_base_override"
    assert not list(tmp_path.iterdir()), "校验不得产生任何文件"


def test_load_receipt_still_annotates_path_and_digest(tmp_path):
    """拆出 `validate_receipt` 之后，`load_receipt` 的对外行为不变."""
    ch, _script = _write_chapter(tmp_path)
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(_receipt(tmp_path, ch), ensure_ascii=False), encoding="utf-8")
    loaded = pc.load_receipt(path)
    assert loaded["_receipt_path"] == str(path)
    assert len(loaded["_receipt_sha256"]) == 64


def test_apply_refreshed_gate_is_transactional_and_creates_previous_zip(tmp_path, clean_env):
    from shared.paths import step01_previous_zip_for
    from shared.publish_txn import verify_previous_backup

    ch, script = _write_chapter(tmp_path)
    receipt = _receipt(tmp_path, ch)
    receipt_path = ch / "step01_adoption.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    loaded = pc.load_receipt(receipt_path)

    # 注入半途失败（无效 JSON）：必须全组回滚且不留下半套刷新
    bad_files = {
        "step01_quality.json": json.dumps({"status": "PASS", "script_revision": "tampered"}),
        "step01_audit.json": "{bad-json",
        "step01_commit.json": "{}",
        "step01_adoption.json": "{}",
    }
    old_quality = (ch / "step01_quality.json").read_text(encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        pc.apply_refreshed_gate(loaded, bad_files)
    assert (ch / "step01_quality.json").read_text(encoding="utf-8") == old_quality
    assert not step01_previous_zip_for(ch).exists()

    # 合法刷新：提交后生成 snapshots/step01_previous.zip
    good_files = {name: (ch / name).read_text(encoding="utf-8") for name in pc.REFRESH_ARTIFACTS}
    written = pc.apply_refreshed_gate(loaded, good_files, script_payload=script)
    assert "script_result.json" in written
    manifest = verify_previous_backup(step01_previous_zip_for(ch))
    assert manifest["previous_revision"] == receipt["script_revision"]


def test_verify_is_readonly_and_rejects_snapshots_or_stale_adoption_on_disk(tmp_path, clean_env):
    from shared.quality_gate import QualityGateError, require_step01_upstream

    ch, script = _write_chapter(tmp_path)
    receipt = _receipt(tmp_path, ch)
    receipt_path = ch / "step01_adoption.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    before_bytes = receipt_path.read_bytes()

    # 正常只读校验不得修改 step01_adoption.json
    loaded = pc.load_receipt(receipt_path)
    verified = pc.verify(loaded)
    assert verified["script_revision"] == receipt["script_revision"]
    assert receipt_path.read_bytes() == before_bytes

    # 磁盘上的 step01_adoption.json revision 被篡改/过期时，下游统一入口也必须拒收
    stale = dict(receipt, script_revision="stale-rev")
    receipt_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(QualityGateError) as qexc:
        require_step01_upstream(ch, script, stage="step03", expected_chapter=1)
    assert "adoption receipt revision mismatch" in str(qexc.value)

    # 凭据路径若指向 snapshots/ 或 .zip 必须拒收
    bad = dict(receipt, formal_chapter=str(ch / "snapshots" / "step01_previous.zip"))
    with pytest.raises(pc.ContextError):
        pc.verify(bad)


def test_build_refreshed_gate_preserves_plan_cache_required(tmp_path, clean_env, monkeypatch):
    from shared import quality_gate
    from shared.plan_cache import FILENAME as PC_NAME, write_plan_cache
    import step01_script_gen.main as m

    ch, script = _write_chapter(tmp_path)
    rev = script["script_revision"]
    write_plan_cache(ch / PC_NAME, {"dialogue_lines": []},
                     scope={"book_name": "book", "chapter": 1}, script_revision=rev)
    man = quality_gate.build_step01_commit_manifest(
        run_id="r1", created_at="2026-11-01T00:00:00+08:00", revision=rev,
        rule_source_fingerprint=json.loads((ch / "step01_quality.json").read_text("utf-8"))["rule_source_fingerprint"],
        rule_version=quality_gate.QUALITY_RULE_VERSION,
        audit_scope="final_after_deterministic_fixes", chapter_dir=ch, status="COMMITTED")
    quality_gate.write_step01_commit_manifest(ch, man)
    receipt = _receipt(tmp_path, ch)
    (ch / "step01_adoption.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    loaded = pc.load_receipt(ch / "step01_adoption.json")

    monkeypatch.setenv("PRODUCTION_CONTEXT_STAGING", str(tmp_path / "stage"))
    monkeypatch.setattr(pc, "_read_novel_text", lambda *a, **k: "匿名原文")
    monkeypatch.setattr(m, "_build_step01_quality_gate", lambda s, n: {
        "status": "PASS", "report_type": "quality_gate", "script_revision": rev,
        "rule_version": quality_gate.QUALITY_RULE_VERSION,
        "rule_source_fingerprint": "fp-new", "duration_band": s.get("duration_band") or {"min": 60.0, "max": 120.0, "target_min": 85.0, "target_max": 110.0},
        "blocking": [], "hard_count": 0, "visual_risk_count": 0,
    })
    files = pc.build_refreshed_gate(loaded)
    commit_out = json.loads(files["step01_commit.json"])
    assert commit_out["plan_cache_required"] is True
    assert PC_NAME in commit_out["artifacts"]
