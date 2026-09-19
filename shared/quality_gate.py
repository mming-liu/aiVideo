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


QUALITY_RULE_VERSION = "2026-09-19.16"


class QualityGateError(RuntimeError):
    pass


_SEMANTIC_SHOT_FIELDS = (
    "index", "main_subject", "prompt", "ff_desc", "lf_desc", "motion_desc",
    "narration_segment", "subtitle", "frame_layouts", "char_layout",
    "keyframes", "camera_movement", "clip_transition", "context_bridge",
    "core_keywords", "duration_sec", "scene", "location", "time_of_day",
    "color_tone", "shot_type", "variation_type",
)


def _semantic_shot(shot) -> dict:
    if not isinstance(shot, dict):
        return {}
    out = {k: shot.get(k) for k in _SEMANTIC_SHOT_FIELDS if k in shot}
    kfs = out.get("keyframes")
    if isinstance(kfs, list):
        out["keyframes"] = [
            {k: v for k, v in kf.items() if k in ("desc", "stage", "t_sec", "time_ratio")}
            for kf in kfs if isinstance(kf, dict)
        ]
    return out


def _semantic_character(char) -> dict:
    if not isinstance(char, dict):
        return {}
    keep = ("identifier", "gender", "age_range", "static_features",
            "dynamic_features", "is_visible", "is_new")
    return {k: char.get(k) for k in keep if k in char}


def _stable_payload(script: dict) -> dict:
    keys = (
        "title", "chapter_index", "chapter_title", "book_name",
        "outline", "characters", "shots", "hook", "narration",
        "negative_prompt", "scene_objects", "resolution", "ratio", "fps",
        "render_style",
    )
    payload = {k: script.get(k) for k in keys if k in script}
    if "shots" in payload:
        payload["shots"] = [_semantic_shot(s) for s in (payload.get("shots") or [])]
    if "hook" in payload:
        payload["hook"] = _semantic_shot(payload.get("hook"))
    if "characters" in payload:
        payload["characters"] = [_semantic_character(c) for c in (payload.get("characters") or [])]
    return payload


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
    payload.setdefault("report_type", "quality_gate")
    payload.setdefault("rule_version", QUALITY_RULE_VERSION)
    if "blocking" in payload and "status" not in payload:
        payload["status"] = "BLOCK" if payload.get("blocking") else "PASS"
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



def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_output_files(obj):
    if isinstance(obj, dict):
        if "path" in obj and "sha1" in obj:
            yield obj
            return
        for value in obj.values():
            yield from _iter_output_files(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_output_files(value)


def verify_report_outputs(report: dict) -> list[str]:
    errors = []
    items = list(_iter_output_files(report.get("outputs") or {}))
    if not items:
        return ["report contains no verifiable outputs"]
    for item in items:
        p = Path(str(item.get("path") or ""))
        if not p.exists():
            errors.append(f"missing output: {p}")
            continue
        actual = _sha1_file(p)
        if actual != str(item.get("sha1") or ""):
            errors.append(f"output changed: {p}")
    return errors



def require_semantic_gate(quality_path: Path, audit_path: Path, script: dict,
                          *, stage: str = "step01") -> dict:
    """Require both structural PASS and a non-blocking final audit."""
    quality = require_gate(
        quality_path, script, stage=stage, required_report_type="quality_gate"
    )
    audit = load_gate(audit_path)
    revision = ensure_script_revision(script)
    if str(audit.get("status") or "").strip().lower() != "ok":
        raise QualityGateError(
            f"{stage} semantic audit is not ok: status={audit.get('status') or 'missing'}"
        )
    if str(audit.get("input_revision") or "").strip() != revision:
        raise QualityGateError(
            f"{stage} semantic audit is stale: audit={audit.get('input_revision') or 'missing'} current={revision}"
        )
    if str(audit.get("rule_source_fingerprint") or "").strip() != str(
        quality.get("rule_source_fingerprint") or ""
    ).strip():
        raise QualityGateError(
            f"{stage} semantic audit fingerprint mismatch: "
            f"audit={audit.get('rule_source_fingerprint') or 'missing'} "
            f"quality={quality.get('rule_source_fingerprint') or 'missing'}"
        )
    if stage == "step01":
        try:
            from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
        except Exception:
            RULE_SOURCE_FINGERPRINT = ""
        report_fp = str(quality.get("rule_source_fingerprint") or "").strip()
        if RULE_SOURCE_FINGERPRINT and report_fp != RULE_SOURCE_FINGERPRINT:
            raise QualityGateError(
                f"{stage} quality gate was produced by different rule sources: "
                f"report={report_fp or 'missing'} current={RULE_SOURCE_FINGERPRINT}"
            )
    if not str(audit.get("audit_scope") or "").strip():
        raise QualityGateError(f"{stage} semantic audit scope is missing")
    blocking = [
        issue for issue in (audit.get("issues") or [])
        if str(issue.get("severity") or "").strip() in ("hard", "visual_risk", "blocking")
    ]
    if blocking:
        first = blocking[0]
        raise QualityGateError(
            f"{stage} semantic audit contains {len(blocking)} blocking issue(s): "
            f"shot={first.get('shot_id')} frame={first.get('frame_id')} "
            f"code={first.get('code')} message={first.get('message')}"
        )
    return {"quality": quality, "audit": audit}


def require_gate(report_path: Path, script: dict, *, stage: str,
                 allow_blocking: bool = False,
                 required_report_type: str | None = None,
                 verify_outputs: bool = False) -> dict:
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
    report_status = str(report.get("status") or "").strip().upper()
    if report_status != "PASS":
        raise QualityGateError(
            f"{stage} quality gate has no explicit PASS status "
            f"(status={report_status or 'missing'}). Re-run the upstream stage."
        )
    report_rule = str(report.get("rule_version") or "").strip()
    if report_rule != QUALITY_RULE_VERSION:
        raise QualityGateError(
            f"{stage} quality gate rule_version mismatch: "
            f"report={report_rule or 'missing'} expected={QUALITY_RULE_VERSION}; re-run the upstream stage"
        )
    if required_report_type and report.get("report_type") != required_report_type:
        raise QualityGateError(
            f"{stage} quality gate report_type mismatch: "
            f"report={report.get('report_type') or 'missing'} expected={required_report_type}"
        )
    if verify_outputs:
        _out_errors = verify_report_outputs(report)
        if _out_errors:
            raise QualityGateError(
                f"{stage} product outputs failed verification: " + "; ".join(_out_errors[:5])
            )
    return report


__all__ = [
    "QualityGateError",
    "compute_script_revision",
    "ensure_script_revision",
    "attach_gate_metadata",
    "load_gate",
    "require_gate",
    "require_semantic_gate",
    "verify_report_outputs",
]
