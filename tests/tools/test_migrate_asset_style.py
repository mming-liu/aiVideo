# -*- coding: utf-8 -*-
"""S03：旧资产画风迁移候选（只出隔离副本，不写正式资产库）."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.character_bank import CharacterBank, StyleBank
from tools import migrate_asset_style as mas


def _png(path: Path, color=(30, 40, 50)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 12), color).save(path)
    return path


def _setup(tmp_path, *, style: str | None = None):
    cb_dir = tmp_path / "source" / "characters_bank"
    sb_dir = tmp_path / "source" / "style_bank"
    front = _png(cb_dir / "甲" / "front.png")
    base = _png(cb_dir / "甲" / "base.png")
    side = _png(cb_dir / "乙" / "side.png")
    anchor = _png(sb_dir / "style_anchor.png")
    entry = {
        "identifier": "甲", "asset_id": "C01",
        "static_features": "青年", "dynamic_features": "沉静",
        "first_chapter": 1, "last_chapter": 2,
        "portraits": {
            "front": {"path": str(front)},
            "base": {"path": str(base)},
        },
    }
    if style:
        entry["video_style"] = style
    cb_data = {"characters": {
        "甲": entry,
        "乙": {"identifier": "乙", "portraits": {"side": {"path": str(side)}}},
    }, "meta": {}}
    (cb_dir / "characters_bank.json").write_text(
        json.dumps(cb_data, ensure_ascii=False), encoding="utf-8")
    sb_data = {"is_locked": True, "style_anchor_image": str(anchor)}
    (sb_dir / "style_bank.json").write_text(
        json.dumps(sb_data, ensure_ascii=False), encoding="utf-8")
    return {
        "cb_dir": cb_dir, "sb_dir": sb_dir, "front": front, "base": base,
        "side": side, "anchor": anchor,
        "cb_sha": mas.sha256_file(cb_dir / "characters_bank.json"),
        "sb_sha": mas.sha256_file(sb_dir / "style_bank.json"),
    }


def _manifest(fixture, *, target="gufeng", views=("front", "base"),
              source_sha=None, sb_sha=None, include_anchor=True,
              operator="tester", basis="人工核对"):
    assets = [{
        "kind": "character", "identifier": "甲", "views": list(views),
        "basis": "甲全部肖像来自同一画风批次",
        "image_sha256": {v: mas.sha256_file(fixture[v]) for v in views},
    }]
    if include_anchor:
        assets.append({
            "kind": "style_anchor",
            "sha256": mas.sha256_file(fixture["anchor"]),
            "basis": "锚点为纯空镜古风",
        })
    return {
        "schema": mas.SCHEMA,
        "operator": operator,
        "basis": basis,
        "target_style": target,
        "source": {
            "characters_bank": str(fixture["cb_dir"] / "characters_bank.json"),
            "characters_bank_sha256": source_sha or fixture["cb_sha"],
            "style_bank": str(fixture["sb_dir"] / "style_bank.json"),
            "style_bank_sha256": sb_sha or fixture["sb_sha"],
        },
        "assets": assets,
    }


def _write_manifest(tmp_path, payload) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_happy_path_generates_loadable_candidate_and_keeps_source(tmp_path):
    fixture = _setup(tmp_path)
    cb_before = (fixture["cb_dir"] / "characters_bank.json").read_bytes()
    manifest = _write_manifest(tmp_path, _manifest(fixture))

    result = mas.apply_migration(manifest, tmp_path / "candidate")
    assert result["status"] == "complete"
    assert result["applied"][0]["identifier"] == "甲"
    assert {row["identifier"] for row in result["pending"]} == {"乙"}

    # 源库与源图片一个字节不改
    assert (fixture["cb_dir"] / "characters_bank.json").read_bytes() == cb_before
    assert mas.sha256_file(fixture["front"]) == _manifest(fixture)["assets"][0][
        "image_sha256"]["front"]

    # 候选经正式 Bank 装载接口可用，且解析放行
    cand_dir = Path(result["candidate_root"]) / "characters_bank"
    bank = CharacterBank(cand_dir)
    bank.load(read_only=True)
    resolved = bank.resolve_portraits("甲", style="gufeng")
    assert resolved["ok"] is True
    assert set(resolved["portraits"]) == {"front", "base"}
    assert resolved["portraits"]["front"]["style_source"] == "explicit_migration"
    assert resolved["portraits"]["front"]["style_basis"]

    # 声明的画风锚点写入候选并可经 StyleBank 解析
    anchor_json = Path(result["candidate_root"]) / "style_bank" / "style_bank.json"
    assert anchor_json.is_file()

    # 回执与差异报告都在候选里
    receipt = json.loads((Path(result["candidate_root"]) /
                          "migration_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "complete"
    assert receipt["target_style"] == "gufeng"
    assert receipt["output_sha256"]


def test_candidate_does_not_stamp_undeclared_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    fixture = _setup(tmp_path)
    manifest = _manifest(fixture, views=("front",))
    result = mas.apply_migration(_write_manifest(tmp_path, manifest),
                                 tmp_path / "candidate")
    cand = CharacterBank(Path(result["candidate_root"]) / "characters_bank")
    cand.load(read_only=True)
    resolved = cand.resolve_portraits("甲")
    assert resolved["ok"] is True
    assert set(resolved["portraits"]) == {"front"}
    assert cand.get_character("甲")["portraits"]["base"].get("video_style") is None


def test_repeated_run_is_idempotent(tmp_path):
    fixture = _setup(tmp_path)
    manifest = _write_manifest(tmp_path, _manifest(fixture))
    first = mas.apply_migration(manifest, tmp_path / "candidate")
    receipt_before = (Path(first["candidate_root"]) /
                      "migration_receipt.json").read_bytes()
    second = mas.apply_migration(manifest, tmp_path / "candidate")
    assert second["status"] == "already_applied"
    assert (Path(first["candidate_root"]) /
            "migration_receipt.json").read_bytes() == receipt_before


def test_source_digest_mismatch_is_rejected_without_candidate(tmp_path):
    fixture = _setup(tmp_path)
    manifest = _write_manifest(
        tmp_path, _manifest(fixture, source_sha="0" * 64))
    with pytest.raises(mas.MigrationError) as excinfo:
        mas.apply_migration(manifest, tmp_path / "candidate")
    assert excinfo.value.reason == "source_digest_mismatch"
    assert not (tmp_path / "candidate").exists()


def test_image_digest_mismatch_is_rejected(tmp_path):
    fixture = _setup(tmp_path)
    payload = _manifest(fixture)
    payload["assets"][0]["image_sha256"]["front"] = "f" * 64
    manifest = _write_manifest(tmp_path, payload)
    with pytest.raises(mas.MigrationError) as excinfo:
        mas.apply_migration(manifest, tmp_path / "candidate")
    assert excinfo.value.reason == "image_digest_mismatch"
    assert not (tmp_path / "candidate").exists()


def test_existing_tag_conflict_is_rejected(tmp_path):
    fixture = _setup(tmp_path, style="anime")
    manifest = _write_manifest(tmp_path, _manifest(fixture))
    with pytest.raises(mas.MigrationError) as excinfo:
        mas.apply_migration(manifest, tmp_path / "candidate")
    assert excinfo.value.reason == "tag_conflict"
    assert not (tmp_path / "candidate").exists()


def test_path_escape_is_rejected(tmp_path):
    fixture = _setup(tmp_path)
    outside = _png(tmp_path / "outside.png")
    cb_data = json.loads((fixture["cb_dir"] / "characters_bank.json")
                         .read_text(encoding="utf-8"))
    cb_data["characters"]["甲"]["portraits"]["front"]["path"] = str(outside)
    (fixture["cb_dir"] / "characters_bank.json").write_text(
        json.dumps(cb_data, ensure_ascii=False), encoding="utf-8")
    payload = _manifest(fixture)
    payload["source"]["characters_bank_sha256"] = mas.sha256_file(
        fixture["cb_dir"] / "characters_bank.json")
    payload["assets"][0]["image_sha256"]["front"] = mas.sha256_file(outside)
    manifest = _write_manifest(tmp_path, payload)
    with pytest.raises(mas.MigrationError) as excinfo:
        mas.apply_migration(manifest, tmp_path / "candidate")
    assert excinfo.value.reason == "path_escape"


def test_candidate_conflict_is_rejected(tmp_path):
    fixture = _setup(tmp_path)
    manifest = _write_manifest(tmp_path, _manifest(fixture))
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "occupied.txt").write_text("x", encoding="utf-8")
    with pytest.raises(mas.MigrationError) as excinfo:
        mas.apply_migration(manifest, candidate)
    assert excinfo.value.reason == "candidate_conflict"


def test_manifest_requires_operator_and_basis(tmp_path):
    fixture = _setup(tmp_path)
    payload = _manifest(fixture, operator="")
    manifest = _write_manifest(tmp_path, payload)
    with pytest.raises(mas.MigrationError) as excinfo:
        mas.apply_migration(manifest, tmp_path / "candidate")
    assert excinfo.value.reason == "invalid_manifest"


def test_cli_json_and_exit_codes(tmp_path, capsys):
    fixture = _setup(tmp_path)
    manifest = _write_manifest(tmp_path, _manifest(fixture))
    assert mas.main(["--manifest", str(manifest),
                     "--candidate-root", str(tmp_path / "candidate"),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "complete"

    bad = _write_manifest(tmp_path, {"schema": mas.SCHEMA})
    assert mas.main(["--manifest", str(bad),
                     "--candidate-root", str(tmp_path / "other"),
                     "--json"]) == 2


def test_style_bank_candidate_is_loadable(tmp_path):
    fixture = _setup(tmp_path)
    result = mas.apply_migration(_write_manifest(tmp_path, _manifest(fixture)),
                                 tmp_path / "candidate")
    sb = StyleBank(Path(result["candidate_root"]) / "style_bank")
    sb.load(read_only=True)
    resolved = sb.get_anchor_for_style("gufeng")
    assert resolved["ok"] is True
    assert resolved["path"]
