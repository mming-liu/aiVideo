# -*- coding: utf-8 -*-
"""G4 read-only metrics aggregator for Step02a decision_ledger events."""
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


def summarize_step02a_events(events: Iterable[dict]) -> dict:
    """Aggregate decision_ledger & request/qc counts bucketed by model_id x ref_strategy x complexity."""
    ev_list = list(events)
    ledgers = [e for e in ev_list if e.get("event") == "decision_ledger"]
    req_sent = sum(1 for e in ev_list if e.get("event") == "request_sent")

    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in ledgers:
        model = str(row.get("model_id") or "unknown")
        ref_strategy = str(row.get("ref_strategy") or "unknown")
        complexity = str(row.get("shot_complexity") or "unspecified")
        key = f"{model}|{ref_strategy}|{complexity}"
        buckets[key].append(row)

    bucket_stats: dict[str, dict] = {}
    for key, rows in sorted(buckets.items()):
        posts = sum(
            1 for r in rows
            if str(r.get("action_type") or "") in ("generate", "local_edit")
            and int(r.get("attempt_index") or 0) > 0
        )
        verdict_counts: dict[str, int] = defaultdict(int)
        defect_counts: dict[str, int] = defaultdict(int)
        usability_counts: dict[str, int] = defaultdict(int)
        non_target_damage = 0
        for r in rows:
            verdict_counts[str(r.get("qc_verdict") or "unknown")] += 1
            defect_counts[str(r.get("defect_class") or "none")] += 1
            usability_counts[str(r.get("human_usability") or "pending")] += 1
            if r.get("non_target_damage"):
                non_target_damage += 1

        # Unique frame slots (chapter, shot, frame_role)
        slots = {
            (r.get("chapter_scope"), r.get("shot_index"), r.get("frame_role"))
            for r in rows
        }
        usable = usability_counts.get("usable", 0) + usability_counts.get("yes", 0)
        rework = usability_counts.get("rework", 0) + usability_counts.get("no", 0)
        unresolved = (
            verdict_counts.get("qc_rejected", 0)
            + verdict_counts.get("qc_call_failed", 0)
            + defect_counts.get("request_conflict", 0)
            + defect_counts.get("missing_asset", 0)
        )
        bucket_stats[key] = {
            "ledger_entries": len(rows),
            "unique_frames": len(slots),
            "posts_estimated": posts,
            "verdicts": dict(sorted(verdict_counts.items())),
            "defect_classes": dict(sorted(defect_counts.items())),
            "human_usability": dict(sorted(usability_counts.items())),
            "non_target_damage_count": non_target_damage,
            "requests_per_usable_frame": round(posts / usable, 3) if usable > 0 else None,
            "manual_rework_rate": round(rework / len(slots), 3) if slots else None,
            "unresolved_entries": unresolved,
        }

    return {
        "schema": "step02a.metrics_report/1",
        "total_events": len(ev_list),
        "request_sent_events": req_sent,
        "decision_ledger_entries": len(ledgers),
        "buckets": bucket_stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate Step02a decision_ledger metrics (read-only)")
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
            f"events={report['total_events']} request_sent={report['request_sent_events']} "
            f"ledger_entries={report['decision_ledger_entries']}"
        )
        for key, st in report["buckets"].items():
            print(
                f"  [{key}] frames={st['unique_frames']} posts={st['posts_estimated']} "
                f"verdicts={st['verdicts']} defects={st['defect_classes']} "
                f"req_per_usable={st['requests_per_usable_frame']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
