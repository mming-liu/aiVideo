# -*- coding: utf-8 -*-
"""按显式声明生成旧资产画风迁移**候选**（S03；不写正式资产库）.

用法：
    python tools/migrate_asset_style.py \
        --manifest migration.json \
        --candidate-root .tmp/style_migration/gufeng

声明（`asset_style_migration/1`）必须逐资产写清来源与依据：

```json
{
  "schema": "asset_style_migration/1",
  "operator": "张三",
  "basis": "2026-11-28 人工核对原画风为古风实景",
  "target_style": "gufeng",
  "source": {
    "characters_bank": "output/<书>/global/characters_bank/characters_bank.json",
    "characters_bank_sha256": "<64 hex>",
    "style_bank": "output/<书>/global/style_bank/style_bank.json",
    "style_bank_sha256": "<64 hex>"
  },
  "assets": [
    {
      "kind": "character",
      "identifier": "甲",
      "views": ["front", "base"],
      "basis": "该角色全部肖像均来自 gufeng 批次",
      "image_sha256": {"front": "<64 hex>", "base": "<64 hex>"}
    },
    {
      "kind": "style_anchor",
      "sha256": "<64 hex>",
      "basis": "第一章锁定锚点为 gufeng 空镜"
    }
  ]
}
```

工具只生成隔离的库副本、差异报告和迁移回执；源库/图片只读且必须与声明摘要
一致。已有标签冲突、路径越界、候选目录冲突或摘要不符一律拒绝；没有画风依据
的资产只进入 `pending`，不会被盖章。候选目录布局与正式 Bank 一致：

    <candidate-root>/characters_bank/characters_bank.json
    <candidate-root>/style_bank/style_bank.json
    <candidate-root>/migration_receipt.json
    <candidate-root>/diff_report.json

重复执行同一声明且候选未被改动时返回 `already_applied`（退出码 0）。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.character_bank import (  # noqa: E402
    ASSET_CONTRACT_VERSION,
    BankLoadError,
    CharacterBank,
    StyleBank,
    normalize_video_style,
)
from shared.json_io import atomic_dump_json  # noqa: E402

SCHEMA = "asset_style_migration/1"
_VIEWS = ("front", "base", "side", "back", "face")
_HEX_LEN = 64
_OPS_TS = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731


class MigrationError(Exception):
    """迁移声明或候选生成被拒绝（`reason` 可机读）."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = str(reason)
        self.detail = str(detail)
        super().__init__(f"{self.reason}: {self.detail}" if self.detail else self.reason)

    def to_dict(self) -> dict:
        return {"ok": False, "reason": self.reason, "detail": self.detail}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex(value) -> bool:
    if not isinstance(value, str) or len(value) != _HEX_LEN:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _require_text(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MigrationError("invalid_manifest", f"{field} 不能为空")
    return text


def _resolve_repo_path(value, field: str) -> Path:
    text = _require_text(value, field)
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


def load_manifest(path: str | Path) -> dict:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MigrationError("manifest_not_found", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise MigrationError("invalid_manifest", f"JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationError("invalid_manifest", "顶层必须是对象")
    return payload


def validate_manifest(manifest: dict) -> dict:
    """校验并归一化声明；返回带解析后路径的副本."""
    if manifest.get("schema") != SCHEMA:
        raise MigrationError("invalid_manifest", f"schema 必须是 {SCHEMA}")
    operator = _require_text(manifest.get("operator"), "operator")
    basis = _require_text(manifest.get("basis"), "basis")
    target = normalize_video_style(manifest.get("target_style"))
    if not target:
        raise MigrationError("invalid_manifest", "target_style 不能为空")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise MigrationError("invalid_manifest", "source 必须是对象")
    cb_path = _resolve_repo_path(source.get("characters_bank"),
                                 "source.characters_bank")
    cb_sha = str(source.get("characters_bank_sha256") or "")
    if not _is_hex(cb_sha):
        raise MigrationError("invalid_manifest",
                             "source.characters_bank_sha256 必须是 64 位十六进制")
    sb_path = None
    sb_sha = ""
    if source.get("style_bank"):
        sb_path = _resolve_repo_path(source.get("style_bank"), "source.style_bank")
        sb_sha = str(source.get("style_bank_sha256") or "")
        if not _is_hex(sb_sha):
            raise MigrationError("invalid_manifest",
                                 "source.style_bank_sha256 必须是 64 位十六进制")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise MigrationError("invalid_manifest", "assets 必须是非空数组")
    seen_characters: set[str] = set()
    declared: list[dict] = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise MigrationError("invalid_manifest", f"assets[{index}] 必须是对象")
        kind = str(asset.get("kind") or "").strip()
        asset_basis = _require_text(asset.get("basis"), f"assets[{index}].basis")
        if kind == "character":
            identifier = _require_text(asset.get("identifier"),
                                       f"assets[{index}].identifier")
            if identifier in seen_characters:
                raise MigrationError("invalid_manifest", f"角色 {identifier} 重复声明")
            seen_characters.add(identifier)
            views = asset.get("views")
            if not isinstance(views, list) or not views:
                raise MigrationError("invalid_manifest",
                                     f"assets[{index}].views 必须是非空数组")
            normalized_views = []
            for view in views:
                view_name = str(view or "").strip()
                if view_name not in _VIEWS:
                    raise MigrationError("invalid_manifest",
                                         f"未知视图 {view_name!r}")
                if view_name in normalized_views:
                    raise MigrationError("invalid_manifest",
                                         f"视图 {view_name} 重复声明")
                normalized_views.append(view_name)
            digests = asset.get("image_sha256")
            if not isinstance(digests, dict):
                raise MigrationError("invalid_manifest",
                                     f"assets[{index}].image_sha256 必须是对象")
            image_digests = {}
            for view in normalized_views:
                digest = str(digests.get(view) or "")
                if not _is_hex(digest):
                    raise MigrationError(
                        "invalid_manifest",
                        f"assets[{index}].image_sha256.{view} 必须是 64 位十六进制")
                image_digests[view] = digest
            declared.append({"kind": "character", "identifier": identifier,
                             "views": normalized_views, "image_sha256": image_digests,
                             "basis": asset_basis})
        elif kind == "style_anchor":
            digest = str(asset.get("sha256") or "")
            if not _is_hex(digest):
                raise MigrationError("invalid_manifest",
                                     f"assets[{index}].sha256 必须是 64 位十六进制")
            declared.append({"kind": "style_anchor", "sha256": digest,
                             "basis": asset_basis})
        else:
            raise MigrationError("invalid_manifest", f"未知资产类型 {kind!r}")
    return {"schema": SCHEMA, "operator": operator, "basis": basis,
            "target_style": target,
            "source": {"characters_bank": str(cb_path),
                       "characters_bank_sha256": cb_sha,
                       "style_bank": str(sb_path) if sb_path else "",
                       "style_bank_sha256": sb_sha},
            "assets": declared}


def _manifest_digest(manifest: dict) -> str:
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _inside(child: Path, root: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _verify_source_file(path: Path, expected_sha: str, label: str) -> None:
    if not path.is_file():
        raise MigrationError("source_missing", f"{label} 不存在: {path}")
    actual = sha256_file(path)
    if actual != expected_sha:
        raise MigrationError(
            "source_digest_mismatch",
            f"{label} 摘要不符（声明 {expected_sha[:12]}…，实际 {actual[:12]}…）")


def _resolve_anchor_path(style_dir: Path, data: dict) -> Path | None:
    candidate = str(data.get("style_anchor_image") or "").strip()
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = style_dir / path
        if path.is_file():
            return path.resolve()
    default = style_dir / "style_anchor.png"
    return default.resolve() if default.is_file() else None


def _load_source_banks(source: dict) -> tuple[dict, dict | None, Path, Path | None]:
    cb_path = Path(source["characters_bank"])
    _verify_source_file(cb_path, source["characters_bank_sha256"], "characters_bank")
    cb = CharacterBank(cb_path.parent)
    try:
        cb_data = cb.load(read_only=True)
    except BankLoadError as exc:
        raise MigrationError("invalid_source_bank", str(exc)) from exc
    sb_data = None
    sb_dir = None
    if source.get("style_bank"):
        sb_path = Path(source["style_bank"])
        _verify_source_file(sb_path, source["style_bank_sha256"], "style_bank")
        sb_dir = sb_path.parent
        sb = StyleBank(sb_dir)
        try:
            sb_data = sb.load(read_only=True)
        except BankLoadError as exc:
            raise MigrationError("invalid_source_bank", str(exc)) from exc
    return copy.deepcopy(cb_data), copy.deepcopy(sb_data), cb_path.parent, sb_dir


def _assert_candidate_root(candidate_root: Path, cb_dir: Path, sb_dir: Path | None) -> None:
    if _inside(candidate_root, cb_dir) or (sb_dir is not None
                                           and _inside(candidate_root, sb_dir)):
        raise MigrationError("path_escape", "候选目录不得位于源资产库内")
    output_root = (_REPO_ROOT / "output").resolve()
    if _inside(candidate_root, output_root):
        raise MigrationError("path_escape",
                             "候选目录不得位于正式 output/ 内（本工具只出隔离候选）")


def _apply_character_assets(cb_data: dict, assets: list[dict], *, target: str,
                            operator: str, basis: str, cb_dir: Path,
                            stamp: str) -> tuple[list[dict], list[dict]]:
    applied: list[dict] = []
    pending: list[dict] = []
    characters = cb_data.get("characters") if isinstance(cb_data, dict) else {}
    if not isinstance(characters, dict):
        raise MigrationError("invalid_source_bank", "characters 必须是对象")
    declared = {a["identifier"]: a for a in assets if a["kind"] == "character"}
    for identifier, asset in declared.items():
        entry = characters.get(identifier)
        if not isinstance(entry, dict):
            raise MigrationError("unknown_asset", f"角色不存在: {identifier}")
        entry_style = normalize_video_style(entry.get("video_style"))
        if entry_style and entry_style != target:
            raise MigrationError(
                "tag_conflict",
                f"角色 {identifier} 已有角色级标签 {entry_style}，与目标 {target} 冲突")
        portraits = entry.get("portraits")
        if not isinstance(portraits, dict):
            raise MigrationError("unknown_asset", f"角色 {identifier} 没有肖像字典")
        for view in asset["views"]:
            record = portraits.get(view)
            if not isinstance(record, dict):
                raise MigrationError("unknown_view",
                                     f"角色 {identifier} 缺少视图 {view}")
            path_text = str(record.get("path") or "").strip()
            if not path_text:
                raise MigrationError("unknown_view",
                                     f"角色 {identifier} 的 {view} 没有路径")
            image_path = Path(path_text)
            if not image_path.is_absolute():
                image_path = cb_dir / image_path
            image_path = image_path.resolve()
            if not _inside(image_path, cb_dir):
                raise MigrationError(
                    "path_escape",
                    f"角色 {identifier} 的 {view} 图片不在资产库内: {image_path}")
            if not image_path.is_file():
                raise MigrationError("image_missing", str(image_path))
            actual = sha256_file(image_path)
            expected = asset["image_sha256"][view]
            if actual != expected:
                raise MigrationError(
                    "image_digest_mismatch",
                    f"角色 {identifier}/{view} 摘要不符"
                    f"（声明 {expected[:12]}…，实际 {actual[:12]}…）")
            existing = normalize_video_style(record.get("video_style"))
            if existing and existing != target:
                raise MigrationError(
                    "tag_conflict",
                    f"角色 {identifier}/{view} 已有标签 {existing}，与目标 {target} 冲突")
            record["video_style"] = target
            record["asset_contract_version"] = ASSET_CONTRACT_VERSION
            record["style_source"] = "explicit_migration"
            record["style_basis"] = asset["basis"] or basis
            record["style_declared_by"] = operator
            record["style_declared_at"] = stamp
            applied.append({"kind": "character", "identifier": identifier,
                            "view": view, "image_sha256": actual,
                            "previous_style": existing or "",
                            "target_style": target})
        for view, record in portraits.items():
            if view in asset["views"]:
                continue
            pending.append({"kind": "character", "identifier": identifier,
                            "view": str(view), "reason": "no_style_declaration",
                            "video_style": normalize_video_style(
                                (record or {}).get("video_style")
                                if isinstance(record, dict) else "")})
    for identifier, entry in characters.items():
        if identifier in declared:
            continue
        pending.append({"kind": "character", "identifier": str(identifier),
                        "view": "", "reason": "no_style_declaration",
                        "video_style": normalize_video_style(
                            (entry or {}).get("video_style")
                            if isinstance(entry, dict) else "")})
    return applied, pending


def _apply_anchor_asset(sb_data: dict, sb_dir: Path, asset: dict, *, target: str,
                        operator: str, basis: str, stamp: str) -> dict:
    anchor = _resolve_anchor_path(sb_dir, sb_data)
    if anchor is None:
        raise MigrationError("anchor_missing", "声明了画风锚点，但找不到 style_anchor.png")
    if not _inside(anchor, sb_dir):
        raise MigrationError("path_escape", f"锚点不在风格库内: {anchor}")
    actual = sha256_file(anchor)
    if actual != asset["sha256"]:
        raise MigrationError(
            "image_digest_mismatch",
            f"画风锚点摘要不符（声明 {asset['sha256'][:12]}…，实际 {actual[:12]}…）")
    existing = normalize_video_style(sb_data.get("video_style"))
    if existing and existing != target:
        raise MigrationError(
            "tag_conflict", f"画风锚点已有标签 {existing}，与目标 {target} 冲突")
    sb_data["video_style"] = target
    sb_data["asset_contract_version"] = ASSET_CONTRACT_VERSION
    sb_data["style_source"] = "explicit_migration"
    sb_data["style_basis"] = asset["basis"] or basis
    sb_data["style_declared_by"] = operator
    sb_data["style_declared_at"] = stamp
    return {"kind": "style_anchor", "path": str(anchor), "image_sha256": actual,
            "previous_style": existing or "", "target_style": target}


def _candidate_digest(candidate_root: Path) -> dict:
    result = {}
    for rel in ("characters_bank/characters_bank.json",
                "style_bank/style_bank.json"):
        path = candidate_root / rel
        if path.is_file():
            result[rel] = sha256_file(path)
    return result


def _already_applied(candidate_root: Path, digest: str) -> dict | None:
    receipt_path = candidate_root / "migration_receipt.json"
    if not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(receipt, dict):
        return None
    if receipt.get("manifest_digest") != digest:
        return None
    if receipt.get("status") != "complete":
        return None
    recorded = receipt.get("output_sha256")
    if recorded != _candidate_digest(candidate_root):
        return None
    return {"ok": True, "status": "already_applied",
            "candidate_root": str(candidate_root),
            "manifest_digest": digest,
            "receipt": str(receipt_path)}


def apply_migration(manifest_path: str | Path, candidate_root: str | Path,
                    *, dry_run: bool = False) -> dict:
    """生成迁移候选；源库/图片始终只读."""
    raw = load_manifest(manifest_path)
    manifest = validate_manifest(raw)
    digest = _manifest_digest(manifest)
    candidate = Path(candidate_root).expanduser()
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    candidate = candidate.resolve()
    cb_data, sb_data, cb_dir, sb_dir = _load_source_banks(manifest["source"])
    _assert_candidate_root(candidate, cb_dir, sb_dir)
    if candidate.exists():
        existing = _already_applied(candidate, digest)
        if existing is not None:
            return existing
        raise MigrationError("candidate_conflict",
                             f"候选目录已存在且不是同一份完整候选: {candidate}")

    stamp = _OPS_TS()
    assets = manifest["assets"]
    applied: list[dict] = []
    pending: list[dict] = []
    char_applied, char_pending = _apply_character_assets(
        cb_data, assets, target=manifest["target_style"],
        operator=manifest["operator"], basis=manifest["basis"],
        cb_dir=cb_dir, stamp=stamp)
    applied = char_applied
    pending = char_pending
    anchor_applied = None
    for asset in assets:
        if asset["kind"] != "style_anchor":
            continue
        if sb_data is None or sb_dir is None:
            raise MigrationError("invalid_manifest",
                                 "声明了 style_anchor 但 source.style_bank 为空")
        anchor_applied = _apply_anchor_asset(
            sb_data, sb_dir, asset, target=manifest["target_style"],
            operator=manifest["operator"], basis=manifest["basis"], stamp=stamp)
        applied.append(anchor_applied)

    receipt = {
        "schema": "asset_style_migration_receipt/1",
        "status": "complete" if not dry_run else "dry_run",
        "generated_at": stamp,
        "operator": manifest["operator"],
        "basis": manifest["basis"],
        "target_style": manifest["target_style"],
        "manifest_digest": digest,
        "source": manifest["source"],
        "applied": applied,
        "pending": pending,
    }
    diff_report = {
        "schema": "asset_style_migration_diff/1",
        "generated_at": stamp,
        "target_style": manifest["target_style"],
        "applied": applied,
        "pending": pending,
        "summary": {
            "applied_views": sum(1 for row in applied if row.get("view")),
            "applied_anchor": 1 if anchor_applied else 0,
            "pending_views": sum(1 for row in pending if row.get("view")),
            "pending_characters": sum(1 for row in pending if not row.get("view")),
        },
    }
    if dry_run:
        return {"ok": True, "status": "dry_run", "manifest_digest": digest,
                "applied": applied, "pending": pending,
                "diff_report": diff_report}

    tmp_dir = candidate.with_name(candidate.name + f".tmp-{os.getpid()}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        (tmp_dir / "characters_bank").mkdir(parents=True)
        atomic_dump_json(tmp_dir / "characters_bank" / "characters_bank.json",
                         cb_data, ensure_ascii=False, indent=2)
        if anchor_applied is not None and sb_data is not None:
            (tmp_dir / "style_bank").mkdir(parents=True)
            atomic_dump_json(tmp_dir / "style_bank" / "style_bank.json",
                             sb_data, ensure_ascii=False, indent=2)
        output_sha = {
            rel: sha256_file(tmp_dir / rel)
            for rel in ("characters_bank/characters_bank.json",
                        "style_bank/style_bank.json")
            if (tmp_dir / rel).is_file()
        }
        receipt["output_sha256"] = output_sha
        receipt["candidate_root"] = str(candidate)
        atomic_dump_json(tmp_dir / "migration_receipt.json", receipt,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(tmp_dir / "diff_report.json", diff_report,
                         ensure_ascii=False, indent=2)
        os.replace(tmp_dir, candidate)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError("candidate_write_failed", str(exc)) from exc

    # 正式 Bank 装载接口必须能读候选（不含风格判定，只证明格式可用）。
    probe = CharacterBank(candidate / "characters_bank")
    try:
        probe.load(read_only=True)
    except BankLoadError as exc:
        shutil.rmtree(candidate, ignore_errors=True)
        raise MigrationError("invalid_candidate", str(exc)) from exc
    return {"ok": True, "status": "complete", "candidate_root": str(candidate),
            "manifest_digest": digest, "applied": applied, "pending": pending,
            "output_sha256": output_sha}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按显式声明生成旧资产画风迁移候选（不写正式资产库）")
    parser.add_argument("--manifest", required=True, help="迁移声明 JSON 路径")
    parser.add_argument("--candidate-root", required=True,
                        help="隔离候选根（不得位于源资产库或 output/ 内）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验并打印差异，不写候选")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = apply_migration(args.manifest, args.candidate_root,
                                 dry_run=args.dry_run)
    except MigrationError as exc:
        payload = exc.to_dict()
        print(json.dumps(payload, ensure_ascii=False) if args.json
              else f"[migrate_asset_style] 拒绝：{exc}")
        return 2 if exc.reason in ("invalid_manifest", "manifest_not_found") else 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[migrate_asset_style] {result['status']} -> "
              f"{result.get('candidate_root', '')}")
        for row in result.get("applied", []):
            where = f"{row.get('identifier')}/{row['view']}" if row.get("view") \
                else row.get("kind")
            print(f"  applied {where} -> {row['target_style']}")
        for row in result.get("pending", []):
            print(f"  pending {row.get('identifier')}/{row.get('view') or '-'}"
                  f"（{row.get('reason')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
