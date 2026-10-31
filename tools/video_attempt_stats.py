"""只读统计：从 video_attempts 汇总每镜的提交/下载/拒收/一次通过率.

数据来源（唯一）：<shot_dir>/video_attempts/attempts/<attempt_id>/
  input.json     冻结请求（requested_seed / prepared / request_key）
  events.jsonl   追加事件（submit_post / fetch_started / fetch_result /
                 video_check / rejected_artifact / accepted / poll_failed …）
  lifecycle.json 投影终态（state / task_id / provider / model_id）

口径（重要）：
  - 「提交次数」只数 events 里的 submit_post，不数轮询或下载重试；
  - 「下载尝试」数 fetch_started，与提交分开记；
  - 「一次通过」= 该镜只有 1 个 attempt 且终态 accepted；
  - 终态以 lifecycle.state 为准，缺失则记 missing。

不发网络请求、不改任何文件。用法：
  python tools/video_attempt_stats.py --root output/video/<book>/<part>/chapter_001
  python tools/video_attempt_stats.py --root .tmp/reg_verify3 --json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_events(p: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"kind": "<unparsable>"})
    except OSError:
        pass
    return out


def scan(root: Path) -> dict:
    """扫描 root 下所有 video_attempts 目录（生产布局或测试夹具均可）。"""
    shots: dict[str, dict] = {}
    for va_root in sorted(root.rglob("video_attempts")):
        if not va_root.is_dir():
            continue
        shot_dir = va_root.parent
        key = str(shot_dir)
        rec = shots.setdefault(key, {
            "shot_dir": key, "attempts": 0, "posts": 0, "fetches": 0,
            "rejected": 0, "states": Counter(), "multi_post_attempts": 0,
            "attempt_ids": [],
        })
        attempts_dir = va_root / "attempts"
        if not attempts_dir.is_dir():
            continue
        for att in sorted(p for p in attempts_dir.iterdir() if p.is_dir()):
            rec["attempts"] += 1
            rec["attempt_ids"].append(att.name)
            lc = _read_json(att / "lifecycle.json")
            state = lc.get("state") or "missing"
            rec["states"][state] += 1
            events = _read_events(att / "events.jsonl")
            kinds = Counter(e.get("kind") for e in events)
            posts = kinds.get("submit_post", 0)
            rec["posts"] += posts
            rec["fetches"] += kinds.get("fetch_started", 0)
            rec["rejected"] += kinds.get("rejected_artifact", 0)
            if posts > 1:
                rec["multi_post_attempts"] += 1
    return shots


def summarize(shots: dict) -> dict:
    total_shots = len(shots)
    total_attempts = sum(s["attempts"] for s in shots.values())
    accepted = {k: s for k, s in shots.items() if s["states"].get("accepted")}
    first_pass = sum(
        1 for s in shots.values()
        if s["attempts"] == 1 and s["states"].get("accepted") == 1)
    state_totals: Counter = Counter()
    for s in shots.values():
        state_totals.update(s["states"])
    return {
        "shots_with_attempts": total_shots,
        "attempts_total": total_attempts,
        "submit_posts_total": sum(s["posts"] for s in shots.values()),
        "fetch_attempts_total": sum(s["fetches"] for s in shots.values()),
        "qc_rejected_total": sum(s["rejected"] for s in shots.values()),
        "shots_accepted": len(accepted),
        "shots_first_pass": first_pass,
        "first_pass_rate": round(first_pass / total_shots, 4) if total_shots else None,
        "attempts_per_shot": round(total_attempts / total_shots, 2) if total_shots else None,
        "terminal_states": dict(state_totals),
        "attempts_with_multiple_posts": sum(s["multi_post_attempts"] for s in shots.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="video_attempts 只读统计")
    ap.add_argument("--root", required=True, help="章节目录或含 video_attempts 的任意目录")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON（含每镜明细）")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"目录不存在: {root}")

    shots = scan(root)
    summary = summarize(shots)
    payload = {"root": str(root), "summary": summary,
               "shots": {k: {**v, "states": dict(v["states"])} for k, v in shots.items()}}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not shots:
        print("\n(没有找到任何 video_attempts/attempts 记录)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
