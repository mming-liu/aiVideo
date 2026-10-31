"""Freeze the evidence bundle for one offline delivery round.

Binds, in a single file: the git HEAD and dirty set, the SHA-256 of every
source/test/input file the round depends on (including untracked and ignored
files), the commands that produced the readings, the readings themselves, and
an independent read of the model-call ledgers.  It writes a new file and never
overwrites an existing one, so a round cannot silently rewrite its own evidence.

Usage:
  python tools/freeze_step01_evidence.py --out <new.json> \
      --source <path> [--source ...] --input <path> [--input ...] \
      --ledger <calls.jsonl> [--ledger ...] --command "<cmd>" [--command ...] \
      --reading <name=json> ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blob in iter(lambda: fh.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


def _git(root: Path, *args) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                             text=True, timeout=60)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return "git_error: %s" % exc


def _ledger(path: Path) -> dict:
    rows = []
    bad = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"path": str(path), "readable": False, "error": str(exc)}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    return {"path": str(path), "readable": True, "rows": len(rows), "bad_lines": bad,
            "ok": sum(1 for r in rows if r.get("ok")),
            "failed": sum(1 for r in rows if not r.get("ok")),
            "run_ids": sorted({str(r.get("run_id") or "") for r in rows}),
            "first_ts": min((str(r.get("ts") or "") for r in rows), default=""),
            "last_ts": max((str(r.get("ts") or "") for r in rows), default=""),
            "call_ids": [str(r.get("call_id") or "") for r in rows]}


def build(*, root: Path, sources, inputs, ledgers, commands, readings) -> dict:
    files = {}
    missing = []
    for group, paths in (("source", sources), ("input", inputs)):
        for raw in paths:
            p = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
            key = str(p.relative_to(root)).replace("\\", "/") if p.is_relative_to(root) else str(p)
            if not p.is_file():
                missing.append(key)
                continue
            files[key] = {"sha256": _sha256(p), "bytes": p.stat().st_size, "group": group}
    status = _git(root, "status", "--porcelain=v1")
    dirty = [line for line in status.splitlines() if line.strip()]

    def _path_of(line: str) -> str:
        """porcelain v1: `XY` status columns then the path; drop the two columns and
        any separator, so staged-only ("M "), worktree-only (" M") and untracked
        ("??") entries all yield the path without clipping a character."""
        return line[2:].lstrip()
    try:
        import pytest
        pytest_version = pytest.__version__
    except Exception:
        pytest_version = ""
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "head": _git(root, "rev-parse", "HEAD"),
        "dirty_entries": dirty,
        "untracked_or_modified": [_path_of(d) for d in dirty],
        "python": platform.python_version(),
        "pytest": pytest_version,
        "os": platform.platform(),
        "files": files,
        "missing": sorted(missing),
        "commands": list(commands),
        "readings": readings,
        "ledgers": [_ledger((root / p).resolve() if not Path(p).is_absolute() else Path(p))
                    for p in ledgers],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--input", action="append", default=[])
    ap.add_argument("--ledger", action="append", default=[])
    ap.add_argument("--command", action="append", default=[])
    ap.add_argument("--reading", action="append", default=[],
                    help="name=json (value parsed as JSON when possible)")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    out = args.out if args.out.is_absolute() else (root / args.out)
    if out.exists():
        print(json.dumps({"status": "REFUSED", "reason": "output exists: %s" % out}))
        return 3
    readings = {}
    for item in args.reading:
        name, _, raw = item.partition("=")
        try:
            readings[name] = json.loads(raw)
        except ValueError:
            readings[name] = raw
    payload = build(root=root, sources=args.source, inputs=args.input,
                    ledgers=args.ledger, commands=args.command, readings=readings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "FROZEN", "out": str(out),
                      "files": len(payload["files"]), "missing": payload["missing"],
                      "ledger_rows": [x.get("rows") for x in payload["ledgers"]]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())