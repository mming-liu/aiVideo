# -*- coding: utf-8 -*-
"""G4/F7 read-only metrics aggregator for Step02a HTTP attempt & decision_ledger events."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.request_events import read_events  # noqa: E402

_KF_ROLE_RE = re.compile(r"^keyframe(?:_(\d+))?$")


def _norm_usability(raw) -> str:
    val = str(raw or "").strip().lower()
    if val in ("usable", "yes"):
        return "usable"
    if val in ("rework", "no"):
        return "rework"
    return "unknown"


def _frame_key(row: dict) -> tuple[str, str, str, int | str | None, str, int | None]:
    raw_role = str(row.get("frame_role") or "").strip()
    raw_kfi = row.get("kf_index")
    kfi: int | None = int(raw_kfi) if isinstance(raw_kfi, int) or str(raw_kfi or "").isdigit() else None
    m = _KF_ROLE_RE.match(raw_role)
    if m:
        base_role = "keyframe"
        if kfi is None and m.group(1) is not None:
            kfi = int(m.group(1))
    else:
        base_role = raw_role
    return (
        str(row.get("work_id") or ""),
        str(row.get("chapter_scope") or ""),
        str(row.get("script_revision") or ""),
        row.get("shot_index"),
        base_role,
        kfi,
    )


def _bucket_key(row: dict) -> str:
    model = str(row.get("model_id") or "unknown")
    ref_strategy = str(row.get("ref_strategy") or "unknown")
    complexity = str(row.get("shot_complexity") or "unknown")
    if not row.get("shot_complexity"):
        complexity = "unknown"
    ptv = str(row.get("prompt_template_version") or "").strip()
    if not ptv:
        pv = str(row.get("prompt_version") or "").strip()
        if pv and not pv.startswith("sha256:"):
            ptv = pv
    if ptv:
        return f"{model}|{ref_strategy}|{complexity}|{ptv}"
    return f"{model}|{ref_strategy}|{complexity}"


def _is_unresolved_terminal(row: dict) -> bool:
    qv = str(row.get("qc_verdict") or "").strip()
    dc = str(row.get("defect_class") or "").strip()
    if qv in ("qc_rejected", "qc_call_failed", "qc_incomplete", "qc_stale"):
        return True
    if dc in ("request_conflict", "missing_asset", "qc_dispute", "stochastic_miss"):
        return qv not in ("qc_passed",)
    return False


def summarize_step02a_events(events: Iterable[dict]) -> dict:
    """Aggregate correlated HTTP attempts & terminal frame states by bucket."""
    ev_list = list(events)
    ledgers = [e for e in ev_list if e.get("event") == "decision_ledger"]
    raw_http_attempts = [e for e in ev_list if e.get("event") == "http_attempt"]
    raw_request_sent = [
        e for e in ev_list
        if e.get("event") == "request_sent" and not e.get("pre_send_failed")
    ]
    review_events = [e for e in ev_list if e.get("event") == "human_review"]

    def _dedup_ev(src: list[dict]) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for ev in src:
            rid = str(ev.get("request_id") or "").strip()
            if rid:
                if rid in seen:
                    continue
                seen.add(rid)
            out.append(ev)
        return out

    dedup_attempt = _dedup_ev(raw_http_attempts)
    dedup_sent = _dedup_ev(raw_request_sent)

    attempt_by_op: dict[str, list[dict]] = defaultdict(list)
    sent_by_op: dict[str, list[dict]] = defaultdict(list)
    unlinked_attempt = 0
    unlinked_sent = 0
    for ev in dedup_attempt:
        op = str(ev.get("operation_id") or "").strip()
        if op:
            attempt_by_op[op].append(ev)
        else:
            unlinked_attempt += 1
    for ev in dedup_sent:
        op = str(ev.get("operation_id") or "").strip()
        if op:
            sent_by_op[op].append(ev)
        else:
            unlinked_sent += 1

    # Prefer explicit http_attempt over legacy request_sent (never double-count by request_id).
    seen_all_rids: set[str] = set()
    preferred_total_events = 0
    for ev in dedup_attempt:
        rid = str(ev.get("request_id") or "").strip()
        if rid:
            seen_all_rids.add(rid)
        preferred_total_events += 1
    for ev in dedup_sent:
        rid = str(ev.get("request_id") or "").strip()
        if rid and rid in seen_all_rids:
            continue
        op = str(ev.get("operation_id") or "").strip()
        if op and op in attempt_by_op:
            continue
        preferred_total_events += 1

    # Index explicit human reviews bound to full frame identity + image_sha256.
    bound_reviews: dict[tuple, str] = {}
    for rv in review_events:
        fk = _frame_key(rv)
        sha = str(rv.get("image_sha256") or "").strip()
        if sha:
            bound_reviews[(fk, sha)] = _norm_usability(rv.get("human_usability"))

    # Group ALL ledger rows per unique 6-part frame key in chronological order.
    frames_history: dict[tuple, list[dict]] = defaultdict(list)
    for idx, row in enumerate(ledgers):
        r_copy = dict(row)
        r_copy["_seq"] = idx
        frames_history[_frame_key(row)].append(r_copy)

    # Sort each frame's timeline deterministically.
    for fk, rows in frames_history.items():
        rows.sort(key=lambda r: (
            int(r.get("seq")) if str(r.get("seq") or "").isdigit() else r["_seq"],
            int(r.get("attempt_index") or 0),
            r["_seq"],
        ))

    # Assign each unique frame to the bucket of its terminal ledger state.
    bucket_frames: dict[str, list[tuple[tuple, list[dict]]]] = defaultdict(list)
    bucket_all_rows: dict[str, list[dict]] = defaultdict(list)
    for row in ledgers:
        bucket_all_rows[_bucket_key(row)].append(row)
    for fk, rows in sorted(frames_history.items(), key=lambda it: str(it[0])):
        term_row = rows[-1]
        bucket_frames[_bucket_key(term_row)].append((fk, rows))

    bucket_stats: dict[str, dict] = {}
    all_bucket_keys = sorted(set(bucket_all_rows) | set(bucket_frames))
    for key in all_bucket_keys:
        rows_all = bucket_all_rows.get(key, [])
        frame_items = bucket_frames.get(key, [])

        # Correlate HTTP attempts: prefer actual HTTP dispatches (`http_attempt`) per operation_id;
        # fall back to `request_sent` only when an operation has no `http_attempt` events.
        op_ids = {str(r.get("operation_id") or "").strip() for r in rows_all if str(r.get("operation_id") or "").strip()}
        for _, hist in frame_items:
            for r in hist:
                op = str(r.get("operation_id") or "").strip()
                if op:
                    op_ids.add(op)

        correlated_attempt = sum(len(attempt_by_op[op]) for op in op_ids if op in attempt_by_op)
        correlated_sent_only = sum(
            len(sent_by_op[op]) for op in op_ids
            if op not in attempt_by_op and op in sent_by_op
        )
        correlated_http = correlated_attempt + correlated_sent_only
        attempts_total: int | str
        attempts_source: str
        if correlated_http > 0:
            attempts_total = correlated_http
            attempts_source = (
                "http_attempt_by_operation"
                if correlated_sent_only == 0
                else ("http_events_mixed" if correlated_attempt > 0 else "http_events_by_operation")
            )
        elif (unlinked_attempt > 0 or unlinked_sent > 0) and not op_ids and len(all_bucket_keys) <= 2 and any(
            str(r.get("action_type") or "") in ("generate", "local_edit") and int(r.get("attempt_index") or 0) > 0
            for r in rows_all
        ):
            # Legacy fixture fallback: only when operation_id is absent AND raw HTTP events exist.
            attempts_total = unlinked_attempt if unlinked_attempt > 0 else unlinked_sent
            attempts_source = "http_events_unlinked"
        elif any(str(r.get("action_type") or "") in ("generate", "local_edit") and int(r.get("attempt_index") or 0) > 0 for r in rows_all) or any(
            str(r.get("action_type") or "") in ("generate", "local_edit") and int(r.get("attempt_index") or 0) > 0
            for _, hist in frame_items for r in hist
        ):
            attempts_total = "unknown"
            attempts_source = "missing_http_events"
        else:
            attempts_total = 0
            attempts_source = "no_generation_attempts"

        verdict_counts: dict[str, int] = defaultdict(int)
        defect_counts: dict[str, int] = defaultdict(int)
        usability_counts: dict[str, int] = defaultdict(int)
        ever_reworked_frames = 0
        any_reviewed_frames = 0
        final_rework_frames = 0
        usable_frames = 0
        unresolved_frames = 0
        non_target_known = 0
        non_target_damage = 0
        cost_known_all = bool(frame_items)
        cost_sum = 0.0

        for fk, hist in frame_items:
            term = hist[-1]
            verdict_counts[str(term.get("qc_verdict") or "unknown")] += 1
            defect_counts[str(term.get("defect_class") or "none")] += 1

            term_sha = str(term.get("image_sha256") or "").strip()
            # Resolve ever_reworked and final usability with image_sha256 binding when present.
            ever_rework_here = False
            any_review_here = False
            for r in hist:
                r_sha = str(r.get("image_sha256") or "").strip()
                u_val = bound_reviews.get((fk, r_sha)) if r_sha and (fk, r_sha) in bound_reviews else _norm_usability(r.get("human_usability"))
                if u_val in ("usable", "rework"):
                    any_review_here = True
                if u_val == "rework":
                    ever_rework_here = True
            for (rfk, rsha), rval in bound_reviews.items():
                if rfk == fk:
                    any_review_here = True
                    if rval == "rework":
                        ever_rework_here = True

            if term_sha and (fk, term_sha) in bound_reviews:
                final_u = bound_reviews[(fk, term_sha)]
            elif term_sha:
                # Only trust terminal row usability if bound to this image_sha256 (or no cross-sha confusion).
                final_u = _norm_usability(term.get("human_usability"))
            else:
                final_u = _norm_usability(term.get("human_usability"))

            usability_counts[final_u] += 1
            if any_review_here or final_u in ("usable", "rework"):
                any_reviewed_frames += 1
            if ever_rework_here:
                ever_reworked_frames += 1
            if final_u == "usable":
                usable_frames += 1
            elif final_u == "rework":
                final_rework_frames += 1
            if _is_unresolved_terminal(term):
                unresolved_frames += 1

            ntd = term.get("non_target_damage")
            if isinstance(ntd, bool):
                non_target_known += 1
                if ntd:
                    non_target_damage += 1

            cost = term.get("estimated_cost_cny")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                cost_sum += float(cost)
            else:
                cost_known_all = False

        unique_n = len(frame_items)
        reviewed_n = usable_frames + final_rework_frames
        ever_rev_denom = any_reviewed_frames if any_reviewed_frames > 0 else ever_reworked_frames
        req_per_usable = (
            round(attempts_total / usable_frames, 3)
            if isinstance(attempts_total, int) and usable_frames > 0
            else None
        )
        ever_rework_rate = (
            round(ever_reworked_frames / ever_rev_denom, 3)
            if ever_rev_denom > 0
            else "unknown"
        )
        final_rework_rate = (
            round(final_rework_frames / reviewed_n, 3)
            if reviewed_n > 0
            else "unknown"
        )
        unresolved_rate = round(unresolved_frames / unique_n, 3) if unique_n > 0 else None
        non_target_damage_rate = (
            round(non_target_damage / non_target_known, 3)
            if non_target_known > 0
            else "unknown"
        )

        # Keep template version & sample request digests cleanly separated in bucket diagnostics.
        ptv_vals = sorted({
            str(r.get("prompt_template_version") or "").strip()
            for r in rows_all
            if str(r.get("prompt_template_version") or "").strip()
        })
        req_digests = sorted({
            str(r.get("request_digest") or "").strip()
            for r in rows_all
            if str(r.get("request_digest") or "").strip()
        })

        bucket_stats[key] = {
            "ledger_entries": len(rows_all),
            "unique_frames": unique_n,
            "terminal_selection_rule": "last_by_(seq,attempt_index,record_order)",
            "attempts_total": attempts_total,
            "attempts_source": attempts_source,
            "posts_estimated": attempts_total,
            "prompt_template_versions": ptv_vals or ["unknown"],
            "distinct_request_digests": len(req_digests),
            "verdicts": dict(sorted(verdict_counts.items())),
            "defect_classes": dict(sorted(defect_counts.items())),
            "human_usability": dict(sorted(usability_counts.items())),
            "reviewed_frames": reviewed_n,
            "any_reviewed_frames": any_reviewed_frames,
            "unreviewed_frames": unique_n - reviewed_n,
            "ever_reworked_frames": ever_reworked_frames,
            "ever_reworked_rate": ever_rework_rate,
            "final_rework_frames": final_rework_frames,
            "final_rework_rate": final_rework_rate,
            "manual_rework_rate": final_rework_rate if isinstance(final_rework_rate, float) else None,
            "non_target_damage_count": non_target_damage if non_target_known > 0 else "unknown",
            "non_target_damage_rate": non_target_damage_rate,
            "estimated_cost_cny": round(cost_sum, 4) if cost_known_all else None,
            "cost_status": "known" if cost_known_all else "unknown",
            "requests_per_usable_frame": req_per_usable,
            "unresolved_frames": unresolved_frames,
            "unresolved_entries": unresolved_frames,
            "unresolved_rate": unresolved_rate,
            "denominators": {
                "requests_per_usable_frame": usable_frames,
                "ever_reworked_rate": ever_rev_denom,
                "final_rework_rate": reviewed_n,
                "unresolved_rate": unique_n,
                "non_target_damage_rate": non_target_known,
            },
        }

    return {
        "schema": "step02a.metrics_report/2",
        "total_events": len(ev_list),
        "request_sent_events": preferred_total_events,
        "http_attempt_events": len(dedup_attempt),
        "decision_ledger_entries": len(ledgers),
        "unique_frames_total": len(frames_history),
        "buckets": bucket_stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate Step02a decision_ledger & HTTP attempt metrics (read-only)")
    parser.add_argument("paths", nargs="+", help="Path(s) to step02_events.jsonl files")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(argv)

    all_events: list[dict] = []
    for p in args.paths:
        all_events.extend(read_events(Path(p)))
    report = summarize_step02a_events(all_events)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"events={report['total_events']} http_attempts={report['request_sent_events']} "
            f"ledger_entries={report['decision_ledger_entries']} unique_frames={report['unique_frames_total']}"
        )
        for key, st in report["buckets"].items():
            print(
                f"  [{key}] frames={st['unique_frames']} attempts={st['attempts_total']} "
                f"verdicts={st['verdicts']} ever_rework={st['ever_reworked_rate']} "
                f"final_rework={st['final_rework_rate']} unresolved={st['unresolved_frames']} "
                f"req_per_usable={st['requests_per_usable_frame']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
