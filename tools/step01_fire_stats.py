# -*- coding: utf-8 -*-
"""Step01 约束 / 问题码触发统计（只读、离线、零模型调用）.

两步用法（先冻结、再统计；统计时只读冻结清单，不扫「最新目录」）::

    # 1) 冻结：显式列出要收录的根目录，按文件名识别产物类型并记录 sha256
    python tools/step01_fire_stats.py freeze --root output/<书>/<部>/chapter_001 \
        --root output/step01_ab --out .tmp/slim/fire_manifest.json

    # 2) 统计：只读清单里的文件，逐个校验 sha256
    python tools/step01_fire_stats.py stats --manifest .tmp/slim/fire_manifest.json \
        --out .tmp/slim/fire_stats.json

产物类型（按文件名）：

- ``*.log``                       → ``[CONSTRAINT]`` / ``[CONSTRAINT-WARN]`` 文本行
- ``step01_quality.json``         → 问题码 × 严重度
- ``step01_audit_rounds.json``    → 问题码 × 严重度（逐轮）
- ``step01_repair_rounds.json``   → 修复轮 reason / accepted / resolved
- ``step01_metrics.json``         → ``constraints`` 节点（逐约束 ran/repair/warn/violation/error）

口径说明（报告里逐条写明，不替读者下结论）：

- 日志里的修复汇总只列前 5 个约束（``constraints/engine.py``），低频约束可能
  **只是没上日志**；这种日志记为 ``truncation_possible``，不能据此判「没触发」。
- ``metrics.constraints`` 是完整计数，但只有带埋点的新运行才有。
- 清单里的文件缺失、sha256 不符、无法解析时**直接失败**（退出码 2），不跳过、
  不当成 0 次触发。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "step01_fire_stats/v1"
KIND_BY_NAME = {
    "step01_quality.json": "quality",
    "step01_audit_rounds.json": "audit_rounds",
    "step01_repair_rounds.json": "repair_rounds",
    "step01_metrics.json": "metrics",
}

_RE_SUMMARY = re.compile(r"\[CONSTRAINT\] 修复 (\d+) 处 \(top: ([^)]*)\)")
_RE_WARN = re.compile(r"\[CONSTRAINT-WARN\] ([a-z0-9_]+)@[^:：（(\s]+(?:（另 (\d+) 处）)?")
_RE_HARD = re.compile(r"\[CONSTRAINT\] HARD 未解决 \d+ 处[^:：]*[:：]\s*(.*)")
_RE_ERROR = re.compile(r"\[CONSTRAINT\] ([a-z0-9_]+) 异常")


class ManifestError(RuntimeError):
    """冻结清单不可用（缺失 / 哈希不符 / 无法解析）."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kind_of(path: Path) -> str | None:
    if path.suffix == ".log":
        return "log"
    return KIND_BY_NAME.get(path.name)


def freeze(roots: list[Path]) -> dict:
    """收录 roots 下所有可识别产物（显式根目录，不按 mtime 选）."""
    items = []
    for root in roots:
        if not root.exists():
            raise ManifestError(f"根目录不存在: {root}")
        for p in sorted(root.rglob("*")):
            if p.is_file() and kind_of(p):
                items.append({"path": p.as_posix(), "kind": kind_of(p), "sha256": _sha256(p)})
    return {"schema": SCHEMA, "roots": [r.as_posix() for r in roots], "artifacts": items}


def _load_verified(manifest: dict, base: Path) -> list[tuple[dict, bytes]]:
    if manifest.get("schema") != SCHEMA:
        raise ManifestError(f"清单 schema 不符: {manifest.get('schema')!r}")
    out = []
    for item in manifest.get("artifacts") or []:
        p = Path(item["path"])
        p = p if p.is_absolute() else base / p
        if not p.is_file():
            raise ManifestError(f"清单文件缺失: {item['path']}")
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != item.get("sha256"):
            raise ManifestError(f"sha256 不符（文件在冻结后被改写）: {item['path']}")
        out.append((item, data))
    return out


def _json(item: dict, data: bytes):
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"无法解析 {item['path']}: {exc}") from exc


def _walk_issues(node, sink: Counter) -> None:
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("code"), str) and isinstance(x.get("severity"), str):
                sink[(x["code"], x["severity"])] += 1
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


def parse_log(text: str, names: set[str]) -> dict:
    """单份日志 → {constraint: Counter(repair/warn/hard/error)}, truncation_possible."""
    per: dict[str, Counter] = defaultdict(Counter)
    truncated = False
    for line in text.splitlines():
        m = _RE_SUMMARY.search(line)
        if m:
            total = int(m.group(1))
            listed = 0
            for part in m.group(2).split(","):
                name, _, n = part.strip().partition("=")
                if name in names and n.isdigit():
                    per[name]["repair"] += int(n)
                    listed += int(n)
            if listed < total:
                truncated = True
            continue
        m = _RE_WARN.search(line)
        if m and m.group(1) in names:
            per[m.group(1)]["warn"] += 1 + int(m.group(2) or 0)
            continue
        m = _RE_HARD.search(line)
        if m:
            for ref in m.group(1).split(","):
                name = ref.strip().split("@", 1)[0]
                if name in names:
                    per[name]["hard"] += 1
            continue
        m = _RE_ERROR.search(line)
        if m and m.group(1) in names:
            per[m.group(1)]["error"] += 1
    return {"per": per, "truncation_possible": truncated}


def stats(manifest: dict, *, base: Path = ROOT, names: set[str] | None = None) -> dict:
    if names is None:
        from step01_script_gen.constraints import ALL_CONSTRAINTS
        names = {c.name for c in ALL_CONSTRAINTS}
    loaded = _load_verified(manifest, base)
    log_counts: dict[str, Counter] = defaultdict(Counter)
    metric_counts: dict[str, Counter] = defaultdict(Counter)
    codes: Counter = Counter()
    reasons: Counter = Counter()
    rounds = {"decisions": 0, "accepted": 0, "resolved": 0}
    seen_kinds: Counter = Counter()
    logs_with_constraints = logs_truncated = 0
    metrics_with_node = 0
    for item, data in loaded:
        kind = item["kind"]
        seen_kinds[kind] += 1
        if kind == "log":
            text = data.decode("utf-8", errors="replace")
            if "[CONSTRAINT" not in text:
                continue
            logs_with_constraints += 1
            parsed = parse_log(text, names)
            logs_truncated += int(parsed["truncation_possible"])
            for n, c in parsed["per"].items():
                log_counts[n].update(c)
        elif kind in ("quality", "audit_rounds"):
            _walk_issues(_json(item, data), codes)
        elif kind == "repair_rounds":
            doc = _json(item, data)
            entries = doc if isinstance(doc, list) else (doc.get("rounds") or [])
            for d in entries:
                if isinstance(d, dict) and "accepted" in d and "groups" in d:
                    rounds["decisions"] += 1
                    rounds["accepted"] += int(bool(d.get("accepted")))
                    rounds["resolved"] += int(d.get("resolved") or 0)
                    reasons[str(d.get("origin_reason") or d.get("reason") or "")] += 1
        elif kind == "metrics":
            node = (_json(item, data) or {}).get("constraints")
            if isinstance(node, dict):
                metrics_with_node += 1
                for n, c in node.items():
                    if isinstance(c, dict):
                        metric_counts[n].update({k: int(v) for k, v in c.items()
                                                 if isinstance(v, int)})
    per_constraint = {}
    for n in sorted(names):
        lc, mc = dict(log_counts.get(n, {})), dict(metric_counts.get(n, {}))
        fired = any(lc.values()) or any(mc.get(k, 0) for k in ("repair", "warn", "violation"))
        per_constraint[n] = {
            "fired": fired,
            "log": lc,
            "metrics": mc,
            # None = 没有任何带埋点的运行，无法区分「跑了没命中」和「根本没跑」
            "observed_ran": (mc.get("ran", 0) > 0) if metrics_with_node else None,
        }
    unknown_in_logs = sorted(set(log_counts) - names)
    return {
        "schema": SCHEMA,
        "inputs": {"artifacts": len(loaded), "by_kind": dict(seen_kinds),
                   "logs_with_constraint_lines": logs_with_constraints,
                   "logs_truncation_possible": logs_truncated,
                   "metrics_with_constraints_node": metrics_with_node},
        "constraints": per_constraint,
        "never_fired": sorted(n for n, v in per_constraint.items() if not v["fired"]),
        "unknown_in_logs": unknown_in_logs,
        "issue_codes": [{"code": c, "severity": s, "count": k}
                        for (c, s), k in codes.most_common()],
        "repair_rounds": {**rounds, "reasons": dict(reasons)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    fz = sub.add_parser("freeze")
    fz.add_argument("--root", action="append", type=Path, required=True)
    fz.add_argument("--out", type=Path, required=True)
    st = sub.add_parser("stats")
    st.add_argument("--manifest", type=Path, required=True)
    st.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "freeze":
            doc = freeze(args.root)
            print(f"frozen {len(doc['artifacts'])} artifacts")
        else:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            doc = stats(manifest)
            print(f"constraints={len(doc['constraints'])} never_fired={len(doc['never_fired'])} "
                  f"logs_truncation_possible={doc['inputs']['logs_truncation_possible']}")
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
