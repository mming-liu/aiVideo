"""Reparse an existing final audit response without another model call."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.json_io import atomic_dump_json
from step01_script_gen.audit import _parse_issues_with_status
from step01_script_gen.quality_issues import Issue


def main() -> int:
    chapter_dir = next((ROOT / "output" / "video").glob("*/part_01_*/chapter_001"))
    path = chapter_dir / "step01_audit.json"
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
