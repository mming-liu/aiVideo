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
