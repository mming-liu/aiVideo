# -*- coding: utf-8 -*-
"""Summarize the S03 audit result (BLOCK) for the U6 gate report."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
RUN = BASE / "u6_package" / "run_s02_20261111"
CH = RUN / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"

audit = json.loads((CH / "step01_audit.blocked.json").read_text(encoding="utf-8"))
receipt = json.loads((RUN / "budget_receipt.json").read_text(encoding="utf-8"))
commit = json.loads((CH / "step01_commit.json").read_text(encoding="utf-8"))
rounds = json.loads((CH / "step01_audit_rounds.json").read_text(encoding="utf-8"))
calls = [json.loads(line) for line in (CH / "step01_calls.jsonl").read_text(
    encoding="utf-8").splitlines() if line.strip()]

issues = []
for i in audit.get("issues") or []:
    issues.append({k: i.get(k) for k in
                   ("shot_id", "severity", "code", "field_path", "message",
                    "repair_targets")})
blocking = [i for i in issues if i.get("severity") in ("hard", "visual_risk", "blocking")]
advisory = [i for i in issues if i.get("severity") == "advisory"]

row = {
    "schema": "u6.s03_result/1",
    "authorization_id": receipt.get("authorization", {}).get("authorization_id"),
    "run_id": receipt.get("authorization", {}).get("claim_run_id"),
    "out_root": str(RUN),
    "revision": receipt.get("revision"),
    "sends": receipt.get("sends"),
    "send_state": receipt.get("send_state"),
    "audit_status": audit.get("status"),
    "blocking_count": len(blocking),
    "advisory_count": len(advisory),
    "verdict": commit.get("status"),
    "repair_outcome": "audit_only",
    "audit_repair_rounds": 0,
    "usage": audit.get("usage"),
    "channel": [audit.get("channel_provider"), audit.get("model")],
    "rule_source_fingerprint": audit.get("rule_source_fingerprint"),
    "audit_prompt_version": audit.get("audit_prompt_version"),
    "audit_focus_version": audit.get("audit_focus_version"),
    "audit_scope": audit.get("audit_scope"),
    "blocking": blocking,
    "advisory": advisory,
    "call": {k: calls[0].get(k) for k in
             ("call_id", "ok", "truncated", "error", "elapsed_ms",
              "prompt_chars", "response_chars", "response_path")} if calls else {},
    "evidence": {
        "audit_payload": str(CH / "step01_audit.blocked.json"),
        "audit_rounds": str(CH / "step01_audit_rounds.json"),
        "commit_manifest": str(CH / "step01_commit.json"),
        "call_ledger": str(CH / "step01_calls.jsonl"),
        "budget_receipt": str(RUN / "budget_receipt.json"),
    },
    "gate": ("S03 判定 BLOCK（blocking_count=1）；按用户门禁停在 S03，"
             "未执行 S04/S05/S06/S07，未重发、未修复。"),
}
out = BASE / "u6_s03_result.json"
out.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: row[k] for k in
                  ("authorization_id", "run_id", "sends", "send_state",
                   "audit_status", "blocking_count", "advisory_count", "verdict",
                   "usage", "blocking")}, ensure_ascii=False, indent=1))
print(str(out))
