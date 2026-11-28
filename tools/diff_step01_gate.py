# -*- coding: utf-8 -*-
"""只读差异复核：用**当前**规则重算一章的确定性质量门，与已采纳产物逐条对比.

用途（2026-11-28 O2）：确认规则源码改动不会给已采纳正式章带来 **新增 blocking**，
从而可以安全地在离线阶段重签门禁（`tools/production_context.py --refresh-gate`）。
本工具**不写任何文件**、不发起模型调用、不修改正式目录（重算在临时暂存根内完成）。

判据（`safe_for_offline_refresh`）三条件同时成立：
1. 无**新增的语义 blocking**（按「码 + 严重度 + 定位」判定；只改文案不算新增）；
2. 重算 `status_new == "PASS"`；
3. 原审计 `status == "ok"`（离线重签只允许在审计通过的前提下进行）。

用法::

    python tools/diff_step01_gate.py --receipt output/锦衣夜行/part_01_杀青州/chapter_001/step01_adoption.json
    python tools/diff_step01_gate.py --receipt <...> --json
    python tools/diff_step01_gate.py --receipt <...> --require-safe   # 不安全时退出码 1

退出码：0 = 复核完成；1 = `--require-safe` 且不安全；2 = 输入/身份/读取失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "step01.gate_diff/1"

#: 质量门 payload 里代表 blocking 的两个列表键（与 `_build_step01_quality_gate` 一致）。
BLOCKING_KEYS = ("hard", "visual_risk")
#: 非阻断告警列表键（advisory 同时容纳 advisory / unverified 两种 severity）。
WARNING_KEYS = ("advisory", "video_advisory")


def _collect(payload: dict, keys: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for key in keys:
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                out.append(item)
    return out


def _semantic_key(issue: dict) -> str:
    """问题**语义身份**：码 + 严重度 + 定位（**不含**文案）.

    blocking 是否「新增」只按语义身份判：检查器只改措辞/取词顺序时，已采纳章
    不应被误判成「新增 blocking」；文案变化另记 `*_messages_changed`。
    """
    return "|".join(str(issue.get(field) or "") for field in (
        "code", "severity", "shot_id", "frame_id", "field_path", "entity_id"))


def _sort_key(issue: dict) -> tuple:
    return (str(issue.get("shot_id") or ""), str(issue.get("code") or ""),
            str(issue.get("field_path") or ""), str(issue.get("message") or ""))


def _diff(old: list[dict], new: list[dict]) -> dict[str, Any]:
    """按语义身份对比，返回 added / removed / messages_changed 三组."""
    old_map: dict[str, dict] = {}
    for issue in old:
        old_map.setdefault(_semantic_key(issue), issue)
    new_map: dict[str, dict] = {}
    for issue in new:
        new_map.setdefault(_semantic_key(issue), issue)

    added = sorted((issue for key, issue in new_map.items() if key not in old_map),
                   key=_sort_key)
    removed = sorted((issue for key, issue in old_map.items() if key not in new_map),
                     key=_sort_key)
    changed = sorted(
        ({"code": issue.get("code"), "shot_id": issue.get("shot_id"),
          "field_path": issue.get("field_path"),
          "old_message": old_map[key].get("message"),
          "new_message": issue.get("message")}
         for key, issue in new_map.items()
         if key in old_map
         and str(issue.get("message") or "") != str(old_map[key].get("message") or "")),
        key=lambda x: (str(x.get("shot_id") or ""), str(x.get("code") or "")))
    return {"count": len(added), "issues": added}, \
        {"count": len(removed), "issues": removed}, changed


def build_report(receipt: dict, *, novel_file: str | None = None) -> dict[str, Any]:
    """只读重算并对比；失败抛 `production_context.ContextError`（由调用方转退出码）."""
    from tools import production_context as pc

    ch = Path(receipt["formal_chapter"])
    old_quality = json.loads((ch / "step01_quality.json").read_text(encoding="utf-8"))
    old_audit = json.loads((ch / "step01_audit.json").read_text(encoding="utf-8"))
    # 差异复核必须跳过「规则源指纹必须等于当前源码」这一步（否则永远先被拒），
    # 但摘要 / revision / 章号 / 提交清单仍按磁盘现状核对。
    verified = pc.verify(receipt, allow_rule_source_drift=True)

    new_quality = pc.recompute_step01_quality(receipt, novel_file=novel_file)

    blocking_added, blocking_removed, blocking_changed = _diff(
        _collect(old_quality, BLOCKING_KEYS), _collect(new_quality, BLOCKING_KEYS))
    warnings_added, warnings_removed, warnings_changed = _diff(
        _collect(old_quality, WARNING_KEYS), _collect(new_quality, WARNING_KEYS))

    audit_status = str(old_audit.get("status") or "").strip().lower()
    status_new = str(new_quality.get("status") or "")
    safe = (blocking_added["count"] == 0 and status_new == "PASS" and audit_status == "ok")

    return {
        "schema": SCHEMA,
        "ok": True,
        "formal_chapter": str(ch),
        "script_revision": verified.get("script_revision"),
        "fingerprint_old": old_quality.get("rule_source_fingerprint"),
        "fingerprint_new": new_quality.get("rule_source_fingerprint"),
        "fingerprint_changed": (str(old_quality.get("rule_source_fingerprint") or "")
                                != str(new_quality.get("rule_source_fingerprint") or "")),
        "status_old": old_quality.get("status"),
        "status_new": status_new,
        "blocking_old": bool(old_quality.get("blocking")),
        "blocking_new": bool(new_quality.get("blocking")),
        "blocking_categories_new": list(new_quality.get("blocking_categories") or []),
        "hard_count_new": new_quality.get("hard_count"),
        "visual_risk_count_new": new_quality.get("visual_risk_count"),
        "blocking_issues_added": blocking_added,
        "blocking_issues_removed": blocking_removed,
        "blocking_messages_changed": blocking_changed,
        "warnings_added": warnings_added,
        "warnings_removed": warnings_removed,
        "warning_messages_changed": warnings_changed,
        "audit_status_old": old_audit.get("status"),
        "safe_for_offline_refresh": safe,
    }


def _print_text(report: dict) -> None:
    print(f"# {report['formal_chapter']}")
    print(f"  指纹: {report['fingerprint_old']} -> {report['fingerprint_new']}"
          f"{'（已变化）' if report['fingerprint_changed'] else '（未变）'}")
    print(f"  质量门: {report['status_old']} -> {report['status_new']}"
          f"  blocking: {report['blocking_old']} -> {report['blocking_new']}"
          f"  audit: {report['audit_status_old']}")
    for label, key in (("新增 blocking", "blocking_issues_added"),
                       ("移除 blocking", "blocking_issues_removed"),
                       ("新增告警", "warnings_added"),
                       ("移除告警", "warnings_removed")):
        entry = report[key]
        print(f"  {label}: {entry['count']}")
        for issue in entry["issues"][:10]:
            print(f"    - [{issue.get('code')}] shot={issue.get('shot_id')} "
                  f"{str(issue.get('message'))[:100]}")
    for label, key in (("blocking 文案变化", "blocking_messages_changed"),
                       ("告警文案变化", "warning_messages_changed")):
        changed = report[key]
        print(f"  {label}: {len(changed)}")
        for item in changed[:10]:
            print(f"    - [{item.get('code')}] shot={item.get('shot_id')} "
                  f"{str(item.get('old_message'))[:60]} => {str(item.get('new_message'))[:60]}")
    verdict = "SAFE" if report["safe_for_offline_refresh"] else "NOT SAFE"
    print(f"  => safe_for_offline_refresh: {report['safe_for_offline_refresh']} ({verdict})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="只读差异复核 step01 确定性质量门（零模型调用）")
    ap.add_argument("--receipt", required=True, help="step01_adoption.json 路径")
    ap.add_argument("--novel-file", default=None, help="显式原文路径（默认按凭据 scope 读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--require-safe", action="store_true",
                    help="不安全时返回退出码 1（CI/闸门用）")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    from tools import production_context as pc

    try:
        receipt = pc.load_receipt(Path(args.receipt))
        report = build_report(receipt, novel_file=args.novel_file)
    except pc.ContextError as exc:
        payload = {"schema": SCHEMA, "ok": False, **exc.payload()}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json
              else f"[ERROR] {exc.payload()}")
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 读取正式产物失败: {exc}")
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    if args.require_safe and not report["safe_for_offline_refresh"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
