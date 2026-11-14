"""Reparse an existing final audit response without another model call.

默认沿用历史行为（找第一个 `output/video/*/part_01_*/chapter_001`），但**必须
能指定目标**：原实现把路径写死成"遍历真实产物目录"，于是它既无法回归
（跑一次就改一次真实 `step01_audit.json`），也无法在离线夹具上验证。
现在 `--path` 指向具体审计文件，`--chapter-dir` 指向章目录（取其中的
`step01_audit.json`）；两者都不给才走历史默认。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.json_io import atomic_dump_json
from step01_script_gen.audit import _parse_issues_with_status
from step01_script_gen.quality_issues import Issue


def resolve_target(path: str = "", chapter_dir: str = "") -> Path:
    if path:
        return Path(path)
    if chapter_dir:
        return Path(chapter_dir) / "step01_audit.json"
    chapter = next((ROOT / "output" / "video").glob("*/part_01_*/chapter_001"))
    return chapter / "step01_audit.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="", help="step01_audit.json 路径")
    ap.add_argument("--chapter-dir", default="", help="章目录（取其中 step01_audit.json）")
    args = ap.parse_args(argv)

    path = resolve_target(args.path, args.chapter_dir)
    if not path.is_file():
        raise SystemExit("审计文件不存在: %s" % path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    parsed, status = _parse_issues_with_status(payload.get("raw_response", ""))
    out = []
    for item in parsed:
        severity_raw = str(item.get("severity") or "").strip().lower()
        impact = str(item.get("impact") or "")
        severity = "visual_risk" if severity_raw == "blocking" or impact.lower().startswith("blocking") else "advisory"
        shot_id = item.get("shot_id")
        out.append(Issue(
            code=f"model_audit_{item.get('code') or 'issue'}",
            severity=severity,
            message=f"{impact}；建议：{item.get('minimal_fix') or ''}",
            shot_id=str(shot_id) if shot_id is not None else None,
            frame_id=str(item.get("frame_id") or ""),
            entity_id=str(item.get("entity_id") or ""),
            field_path=str(item.get("field_path") or ""),
            evidence=str(item.get("evidence") or ""),
            repairable=True,
        ).to_dict())
    payload["status"] = status
    payload["issues"] = out
    if status == "ok":
        payload["error"] = ""
    atomic_dump_json(path, payload, ensure_ascii=False, indent=2)
    print(status, len(out))
    for item in out:
        print(item["severity"], item["shot_id"], item["frame_id"], item["message"])
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
