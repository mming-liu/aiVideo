# -*- coding: utf-8 -*-
"""S03：帧资产逐项来源表（只读，2026-11-17）.

每行一个帧资产：所属镜头/角色、路径、字节数、sha256、Step02a 验收记录里的
sha1 与 revision、锁保标记、QC 日志条目、Step02b 消费预期、状态。

QC 关联规则（用户复核要求）：
- 验收记录里有该帧 sha1 且与当前 sha256 对应 ⇒ ``qc_digest_linked=True``；
- 只有时间（QC 日志 ts >= 文件 mtime）⇒ ``qc_temporal_only=True``，不冒充
  「末次结论适用于当前字节」；
- 两者都没有 ⇒ ``qc_unlinked``。

只读：不写正式章目录；输出路径必须由调用方显式给出。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.enhancement_io import sha256_file
from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME, is_core_keyframe_file

ROLE_FF = "first_frame"
ROLE_LF = "last_frame"
ROLE_KF = "keyframe"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _file_sha1(path: Path) -> str | None:
    import hashlib
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _qc_entries(path: Path) -> list[dict]:
    data = _load_json(path)
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({"ts": str(item.get("ts") or ""),
                    "provider": str(item.get("provider") or ""),
                    "has_hallucination": item.get("has_hallucination"),
                    "person_count": item.get("person_count"),
                    "issues": len(item.get("issues") or [])})
    return out


def _ts_epoch(text: str) -> float | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _frame_rows(shot_dir: Path, idx: int, acceptance: dict | None) -> list[dict]:
    outputs = ((acceptance or {}).get("outputs") or {}).get(str(idx)) or {}
    scope = (acceptance or {}).get("scope") or {}
    revision = (acceptance or {}).get("script_revision")
    policy = scope.get("media_policy")
    need = ((scope.get("requirements") or {}).get("frames") or [])
    rows = []
    candidates = []
    ff = shot_dir / FIRST_FRAME_FILENAME
    lf = shot_dir / LAST_FRAME_FILENAME
    if ff.exists():
        candidates.append((ROLE_FF, ff))
    if lf.exists():
        candidates.append((ROLE_LF, lf))
    for p in sorted(shot_dir.glob("keyframe_*.png"), key=lambda x: int(x.stem.split("_")[1])):
        if is_core_keyframe_file(p):
            candidates.append((ROLE_KF, p))

    for role, path in candidates:
        key = {"first_frame": "ff", "last_frame": "lf"}.get(role, "keyframes")
        recorded = None
        if role in (ROLE_FF, ROLE_LF):
            recorded = (outputs.get(key) or {}).get("sha1")
        else:
            recorded = ((outputs.get("keyframes") or {}).get(path.name) or {}).get("sha1")
        current_sha1 = _file_sha1(path)
        qc_name = {"first_frame": "first_frame_qc_log.json",
                   "last_frame": "last_frame_qc_log.json"}.get(
                       role, path.stem.replace(".png", "") + "_qc_log.json")
        entries = _qc_entries(shot_dir / qc_name)
        mtime = path.stat().st_mtime
        temporal = []
        for entry in entries:
            ts = _ts_epoch(entry["ts"])
            if ts is not None and ts >= mtime:
                temporal.append(entry)
        linked = bool(recorded and current_sha1 and recorded == current_sha1)
        rows.append({
            "shot": idx,
            "role": role,
            "file": path.name,
            "path": str(path),
            "exists": True,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "sha1": current_sha1,
            "acceptance_sha1": recorded,
            "acceptance_revision": revision,
            "acceptance_policy": policy,
            "locked": (shot_dir / (".lock_frame_" + {
                "first_frame": "first_frame", "last_frame": "last_frame"}.get(role, ""))).exists()
                      or (shot_dir / ".lock_frame").exists(),
            "qc_log": qc_name if (shot_dir / qc_name).is_file() else None,
            "qc_entries": entries,
            "qc_applies_temporal": temporal,
            "qc_digest_linked": linked,
            "qc_state": ("digest_linked" if linked
                         else ("temporal_only" if temporal else "unlinked")),
            "expected_by_step02b": (role == ROLE_FF and "ff" in need)
                                   or (role == ROLE_LF and "lf" in need)
                                   or (role == ROLE_KF and bool(scope.get("keyframes_required"))),
            "status": "present",
        })

    present_roles = {r["role"] for r in rows}
    for role, needed, fname in ((ROLE_FF, "ff" in need, FIRST_FRAME_FILENAME),
                                (ROLE_LF, "lf" in need, LAST_FRAME_FILENAME)):
        if needed and role not in present_roles:
            rows.append({
                "shot": idx, "role": role, "file": fname,
                "path": str(shot_dir / fname), "exists": False,
                "bytes": 0, "sha256": None, "sha1": None,
                "acceptance_sha1": ((outputs.get({"first_frame": "ff",
                                                  "last_frame": "lf"}[role]) or {}).get("sha1")),
                "acceptance_revision": revision, "acceptance_policy": policy,
                "locked": False, "qc_log": None, "qc_entries": [],
                "qc_applies_temporal": [], "qc_digest_linked": False,
                "qc_state": "unlinked", "expected_by_step02b": True,
                "status": "missing",
            })
    return rows


def _acceptance_for(idx: int, *reports: dict | None) -> dict | None:
    """按 scope.shots 选覆盖本镜的验收记录；额外的隔离报告优先。"""
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        scoped = {int(i) for i in ((rep.get("scope") or {}).get("shots") or [])}
        if idx in scoped and str(idx) in (rep.get("outputs") or {}):
            return rep
    # 没有报告覆盖本镜时用**主报告**（不是额外报告）判需求，避免把 S03 的
    # shot2 first_only scope 误套到其它镜头。
    return next((r for r in reversed(reports) if isinstance(r, dict)), None)


def build_table(chapter_dir: Path, shots: list[int],
                extra_acceptance: dict | None = None) -> dict:
    acceptance = _load_json(Path(chapter_dir) / "step02a_quality.json")
    shots_dir = Path(chapter_dir) / "vimax_working" / "shots"
    rows = []
    for idx in shots:
        shot_dir = shots_dir / str(idx)
        if not shot_dir.is_dir():
            rows.append({"shot": idx, "role": None, "file": None, "path": str(shot_dir),
                         "exists": False, "bytes": 0, "sha256": None, "sha1": None,
                         "acceptance_sha1": None, "acceptance_revision": None,
                         "acceptance_policy": None, "locked": False, "qc_log": None,
                         "qc_entries": [], "qc_applies_temporal": [],
                         "qc_digest_linked": False, "qc_state": "unlinked",
                         "expected_by_step02b": False, "status": "shot_dir_missing"})
            continue
        rows.extend(_frame_rows(shot_dir, idx,
                                _acceptance_for(idx, extra_acceptance, acceptance)))
    summary = {
        "shots": len(shots),
        "present": sum(1 for r in rows if r["status"] == "present"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
        "shot_dir_missing": sum(1 for r in rows if r["status"] == "shot_dir_missing"),
        "qc_digest_linked": sum(1 for r in rows if r["qc_digest_linked"]),
        "qc_temporal_only": sum(1 for r in rows if r["qc_state"] == "temporal_only"),
    }
    return {
        "schema": "frame_source_table.v1",
        "chapter_dir": str(chapter_dir),
        "acceptance_report": str(Path(chapter_dir) / "step02a_quality.json"),
        "acceptance_scope": (acceptance or {}).get("scope"),
        "acceptance_revision": (acceptance or {}).get("script_revision"),
        "rows": rows,
        "summary": summary,
    }


def _md(table: dict) -> str:
    lines = ["# 帧资产来源表", "",
             "| 镜 | 角色 | 文件 | 字节 | sha256(前12) | 验收 sha1 | 锁 | QC 状态 | 预期消费 | 状态 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in table["rows"]:
        lines.append("| {shot} | {role} | {file} | {bytes} | {sha} | {acc} | {lock} | {qc} | {exp} | {st} |".format(
            shot=r.get("shot"), role=r.get("role") or "-", file=r.get("file") or "-",
            bytes=r.get("bytes") or 0,
            sha=(r.get("sha256") or "-")[:12],
            acc=(r.get("acceptance_sha1") or "-")[:12],
            lock="🔒" if r.get("locked") else "",
            qc=r.get("qc_state") or "-",
            exp="是" if r.get("expected_by_step02b") else "",
            st=r.get("status")))
    s = table["summary"]
    lines += ["", "汇总：{shots} 镜；present={present} / missing={missing} / "
                  "shot_dir_missing={shot_dir_missing}；QC digest_linked={qc_digest_linked}，"
                  "temporal_only={qc_temporal_only}。".format(**s)]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="帧资产来源表（只读）")
    parser.add_argument("--chapter-dir", default=None)
    parser.add_argument("--shots", default=None, help="如 1-13 或 1,2,5")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    parser.add_argument("--acceptance", default=None,
                        help="额外的隔离验收报告（如 S03 shot2 first_only）")
    args = parser.parse_args()

    from shared.shot_selector import parse_shot_selector
    chapter_dir = Path(args.chapter_dir) if args.chapter_dir else None
    if chapter_dir is None:
        from shared.paths import chapter_dir as _chapter_dir
        chapter_dir = _chapter_dir()
    shots = sorted(parse_shot_selector(args.shots)) if args.shots else list(range(1, 14))
    extra = _load_json(Path(args.acceptance)) if args.acceptance else None
    table = build_table(chapter_dir, shots, extra_acceptance=extra)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_md(table), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(out_json),
                      "summary": table["summary"], "model_sends": 0},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
