"""Shared stage quality-gate contract.

Every stage report carries the upstream ``script_revision`` it validated.  A
downstream stage may only consume a report when both conditions hold:

* report revision == current script revision (stale reports are rejected);
* report is not blocking.

The revision is intentionally stored on the script and copied downstream rather
than recomputed after every local metadata write.  That makes the report an
identity token for the story/visual plan, not a hash of transient file paths.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class QualityGateError(RuntimeError):
    pass


def _stable_payload(script: dict) -> dict:
    keys = (
        "title", "chapter_index", "chapter_title", "book_name",
        "characters", "outline", "shots", "hook", "narration",
        "negative_prompt", "scene_objects", "prop_assets", "scene_assets",
        "resolution", "ratio", "fps", "render_style",
    )
    return {k: script.get(k) for k in keys if k in script}


def compute_script_revision(script: dict) -> str:
    raw = json.dumps(_stable_payload(script), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_script_revision(script: dict, *, force: bool = False) -> str:
    """Recompute the content revision; stored values are never trusted.

    A stored ``script_revision`` is only a cache hint.  Recomputing on every
    gate check catches manual edits to story/visual fields that previously
    slipped through with an old revision.
    """
    revision = compute_script_revision(script)
    if force or str(script.get("script_revision") or "").strip() != revision:
        script["script_revision"] = revision
    return revision


def attach_gate_metadata(payload: dict, script: dict, *, stage: str) -> dict:
    revision = ensure_script_revision(script)
    payload.setdefault("source", stage)
    payload["script_revision"] = revision
    return payload


def load_gate(report_path: Path) -> dict:
    if not report_path.exists():
        raise QualityGateError(f"quality gate report missing: {report_path}")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise QualityGateError(f"quality gate report unreadable: {report_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise QualityGateError(f"quality gate report is not an object: {report_path}")
    return data


def require_gate(report_path: Path, script: dict, *, stage: str,
                 allow_blocking: bool = False) -> dict:
    report = load_gate(report_path)
    revision = ensure_script_revision(script)
    report_revision = str(report.get("script_revision") or "").strip()
    if not report_revision or report_revision != revision:
        raise QualityGateError(
            f"{stage} quality gate is stale: report={report_revision or 'missing'} "
            f"current={revision}. Re-run the upstream stage."
        )
    if report.get("blocking") and not allow_blocking:
        raise QualityGateError(
            f"{stage} quality gate is blocking: "
            f"hard={report.get('hard_count')} visual={report.get('visual_risk_count')} "
            f"categories={report.get('blocking_categories')}"
        )
    return report


__all__ = [
    "QualityGateError",
    "compute_script_revision",
    "ensure_script_revision",
    "attach_gate_metadata",
    "load_gate",
    "require_gate",
]
