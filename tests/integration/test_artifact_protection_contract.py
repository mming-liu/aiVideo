# -*- coding: utf-8 -*-
"""产物保护契约（S04 从 chain5 退出受控集恢复的自足子集）.

原文件 `tests_local/integration/test_chain5_artifact_protection.py` 同时依赖
冻结基线、`tests.integration.chain_support` 和真实 output；本文件只保留其通用
机制：**摘要清单 → 逐字节复核 → 缺失/篡改/跨章分别报出 → 恢复后重新变绿**。
真实正式树的回放属于外部数据测试，不在基础测试里依赖。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FORMAL_PRODUCTS = ("script_result.json", "step01_quality.json",
                   "step01_audit.json", "step01_commit.json",
                   "step01_adoption.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for blob in iter(lambda: handle.read(1 << 20), b""):
            digest.update(blob)
    return digest.hexdigest()


def _audit_against_baseline(digests_path: Path, root: Path) -> dict:
    """按冻结摘要逐条复核盘上文件；日志类差异与正式产物差异分开报."""
    mismatched, missing, logs, good = [], [], [], 0
    for line in Path(digests_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        path = root / rel
        rel_name = rel.replace("\\", "/").rsplit("/", 1)[-1]
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256(path) == digest:
            good += 1
            continue
        if rel_name in FORMAL_PRODUCTS:
            mismatched.append(rel)
        else:
            logs.append(rel)
    return {"good": good, "mismatched": mismatched, "missing": missing, "logs": logs}


def _seed_product_group(root: Path, *, rev: str = "revA",
                        chapter_rel: str = "chapter_001") -> Path:
    chapter = root / chapter_rel
    chapter.mkdir(parents=True, exist_ok=True)
    (chapter / "script_result.json").write_text(
        json.dumps({"script_revision": rev, "shots": [{"index": 1}]},
                   ensure_ascii=False), encoding="utf-8")
    (chapter / "step01_quality.json").write_text(
        json.dumps({"status": "PASS"}), encoding="utf-8")
    (chapter / "step01_audit.json").write_text(
        json.dumps({"status": "ok", "input_revision": rev}), encoding="utf-8")
    (chapter / "step01_commit.json").write_text(
        json.dumps({"status": "COMMITTED", "script_revision": rev}), encoding="utf-8")
    (chapter / "step01_adoption.json").write_text(
        json.dumps({"status": "adopted_and_verified", "script_revision": rev}),
        encoding="utf-8")
    return chapter


def _digest_manifest(root: Path, chapter_rel: str = "chapter_001") -> str:
    chapter = root / chapter_rel
    lines = [f"{_sha256(chapter / name)}  {chapter_rel}/{name}"
             for name in sorted(FORMAL_PRODUCTS) if (chapter / name).is_file()]
    return "\n".join(lines) + "\n"


def test_clean_products_pass_the_protection_check(tmp_path):
    root = tmp_path / "out"
    _seed_product_group(root)
    baseline = tmp_path / "artifacts.sha256"
    baseline.write_text(_digest_manifest(root), encoding="utf-8")
    report = _audit_against_baseline(baseline, root)
    assert report["missing"] == [] and report["mismatched"] == []
    assert report["good"] == len(FORMAL_PRODUCTS)


def test_missing_product_is_reported_not_treated_as_unchanged(tmp_path):
    root = tmp_path / "out"
    chapter = _seed_product_group(root)
    baseline = tmp_path / "artifacts.sha256"
    baseline.write_text(_digest_manifest(root), encoding="utf-8")
    (chapter / "step01_adoption.json").unlink()
    report = _audit_against_baseline(baseline, root)
    assert any("step01_adoption.json" in item for item in report["missing"])
    assert report["mismatched"] == []


def test_tampered_product_is_detected_even_with_same_revision(tmp_path):
    root = tmp_path / "out"
    chapter = _seed_product_group(root)
    baseline = tmp_path / "artifacts.sha256"
    baseline.write_text(_digest_manifest(root), encoding="utf-8")
    (chapter / "script_result.json").write_text(
        json.dumps({"script_revision": "revA",
                    "shots": [{"index": 1, "prompt": "tampered"}]},
                   ensure_ascii=False), encoding="utf-8")
    report = _audit_against_baseline(baseline, root)
    assert any("script_result.json" in item for item in report["mismatched"])


def test_cross_chapter_contamination_is_detected(tmp_path):
    root = tmp_path / "out"
    chapter = _seed_product_group(root)
    baseline = tmp_path / "artifacts.sha256"
    baseline.write_text(_digest_manifest(root), encoding="utf-8")
    other = _seed_product_group(tmp_path / "other", rev="revOTHER")
    (chapter / "script_result.json").write_bytes(
        (other / "script_result.json").read_bytes())
    report = _audit_against_baseline(baseline, root)
    assert any("script_result.json" in item for item in report["mismatched"])


def test_restore_then_reaudit_is_clean(tmp_path):
    root = tmp_path / "out"
    chapter = _seed_product_group(root)
    baseline = tmp_path / "artifacts.sha256"
    baseline.write_text(_digest_manifest(root), encoding="utf-8")
    before = (chapter / "script_result.json").read_bytes()
    (chapter / "script_result.json").write_bytes(before + b" ")
    assert _audit_against_baseline(baseline, root)["mismatched"]
    (chapter / "script_result.json").write_bytes(before)
    assert _audit_against_baseline(baseline, root)["mismatched"] == []


def test_non_formal_log_change_is_listed_separately(tmp_path):
    root = tmp_path / "out"
    chapter = _seed_product_group(root)
    (chapter / "step01.log").write_text("v1", encoding="utf-8")
    baseline = tmp_path / "artifacts.sha256"
    lines = _digest_manifest(root).rstrip("\n")
    lines += f"\n{_sha256(chapter / 'step01.log')}  chapter_001/step01.log\n"
    baseline.write_text(lines, encoding="utf-8")
    (chapter / "step01.log").write_text("v2", encoding="utf-8")
    report = _audit_against_baseline(baseline, root)
    assert any("step01.log" in item for item in report["logs"])
    assert report["mismatched"] == []
