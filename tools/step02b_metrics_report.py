# -*- coding: utf-8 -*-
"""S02/V4 read-only metrics aggregator for Step02b HTTP attempts & decision_ledger events."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.request_events import read_events  # noqa: E402


def _shot_key(row: dict) -> tuple[str, str, str, int | str | None]:
    """Canonical 4-field shot identity key: (work_id, chapter_scope, script_revision, shot_index)."""
    return (
        str(row.get("work_id") or ""),
        str(row.get("chapter_scope") or ""),
        str(row.get("script_revision") or ""),
        row.get("shot_index"),
    )


def _bucket_key(row: dict) -> str:
    model = str(row.get("model_id") or "unknown")
    policy = str(row.get("media_policy") or "unknown")
    return f"{model}|{policy}"


def summarize_step02b_events(events: Iterable[dict]) -> dict:
    """Aggregate Step02b HTTP attempts, usable clips, recovery rate, and explicit failures/unfinished."""
    ev_list = list(events)
    raw_attempts = [e for e in ev_list if e.get("event") == "http_attempt"]
    ledgers = [e for e in ev_list if e.get("event") == "decision_ledger"]

    dedup_attempts: list[dict] = []
    seen_rids: set[str] = set()
    for ev in raw_attempts:
        rid = str(ev.get("request_id") or "").strip()
        if rid:
            if rid in seen_rids:
                continue
            seen_rids.add(rid)
        dedup_attempts.append(ev)

    shots_history: dict[tuple, list[dict]] = defaultdict(list)
    for row in ledgers:
        shots_history[_shot_key(row)].append(row)

    attempts_by_bucket: dict[str, list[dict]] = defaultdict(list)
    for ev in dedup_attempts:
        attempts_by_bucket[_bucket_key(ev)].append(ev)

    ledger_by_bucket: dict[str, dict[tuple, list[dict]]] = defaultdict(dict)
    for sk, hist in shots_history.items():
        bk = _bucket_key(hist[-1])
        ledger_by_bucket[bk][sk] = hist

    all_buckets = sorted(set(attempts_by_bucket) | set(ledger_by_bucket))
    bucket_stats: dict[str, dict] = {}
    all_failed_shots: list[dict] = []
    all_unfinished_shots: list[dict] = []
    total_usable = 0
    total_recovery_shots = 0
    total_recovered_usable = 0

    for bk in all_buckets:
        shot_map = ledger_by_bucket.get(bk, {})
        bk_attempts = len(attempts_by_bucket.get(bk, []))
        usable = 0
        recovered_shots = 0
        recovered_usable = 0
        failed_shots: list[dict] = []
        unfinished_shots: list[dict] = []
        outcomes: dict[str, int] = defaultdict(int)

        for sk, hist in shot_map.items():
            term = hist[-1]
            outcome = str(term.get("outcome") or "unknown")
            outcomes[outcome] += 1
            was_recovered = any(bool(r.get("recovered")) or r.get("action_type") == "recover" for r in hist)
            if was_recovered:
                recovered_shots += 1
            if outcome == "accepted":
                usable += 1
                if was_recovered:
                    recovered_usable += 1
            elif outcome in ("provider_failed", "rejected", "submit_blocked"):
                item = {
                    "shot_key": list(sk),
                    "outcome": outcome,
                    "error_code": term.get("error_code") or outcome,
                    "attempt_id": term.get("attempt_id"),
                }
                failed_shots.append(item)
                all_failed_shots.append(item)
            else:
                item = {
                    "shot_key": list(sk),
                    "outcome": outcome,
                    "error_code": term.get("error_code") or outcome,
                    "attempt_id": term.get("attempt_id"),
                }
                unfinished_shots.append(item)
                all_unfinished_shots.append(item)

        total_usable += usable
        total_recovery_shots += recovered_shots
        total_recovered_usable += recovered_usable
        ratio = round(bk_attempts / usable, 3) if usable > 0 else None
        rec_rate = round(recovered_usable / recovered_shots, 3) if recovered_shots > 0 else None
        bucket_stats[bk] = {
            "unique_shots": len(shot_map),
            "http_attempts": bk_attempts,
            "usable_clips": usable,
            "http_attempts_per_usable_clip": ratio,
            "recovered_shots": recovered_shots,
            "recovered_usable_clips": recovered_usable,
            "task_recovery_rate": rec_rate,
            "failed_count": len(failed_shots),
            "unfinished_count": len(unfinished_shots),
            "failed_shots": failed_shots,
            "unfinished_shots": unfinished_shots,
            "outcomes": dict(sorted(outcomes.items())),
        }

    total_http = len(dedup_attempts)
    return {
        "schema": "step02b.metrics_report/1",
        "total_events": len(ev_list),
        "http_attempts_total": total_http,
        "decision_ledger_entries": len(ledgers),
        "unique_shots_total": len(shots_history),
        "usable_clips_total": total_usable,
        "http_attempts_per_usable_clip": (round(total_http / total_usable, 3) if total_usable > 0 else None),
        "recovered_shots_total": total_recovery_shots,
        "task_recovery_rate": (round(total_recovered_usable / total_recovery_shots, 3) if total_recovery_shots > 0 else None),
        "failed_count": len(all_failed_shots),
        "unfinished_count": len(all_unfinished_shots),
        "failed_shots": all_failed_shots,
        "unfinished_shots": all_unfinished_shots,
        "buckets": bucket_stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate Step02b HTTP attempt & decision_ledger metrics (read-only)")
    parser.add_argument("paths", nargs="+", help="Path(s) to step02b_events.jsonl files")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(argv)

    events: list[dict] = []
    for p in args.paths:
        events.extend(read_events(Path(p)))
    report = summarize_step02b_events(events)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"events={report['total_events']} http_attempts={report['http_attempts_total']} "
            f"usable_clips={report['usable_clips_total']} failed={report['failed_count']} "
            f"unfinished={report['unfinished_count']} attempts_per_usable={report['http_attempts_per_usable_clip']}"
        )
        for bk, st in report["buckets"].items():
            print(
                f"  [{bk}] shots={st['unique_shots']} attempts={st['http_attempts']} "
                f"usable={st['usable_clips']} failed={st['failed_count']} "
                f"unfinished={st['unfinished_count']} ratio={st['http_attempts_per_usable_clip']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
