"""S06 阶段一：把 A/B 结果与冻结预期对齐，输出可读对照表."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / ".pytest_tmp/s06/runs"


def _latest() -> Path:
    dirs = [d for d in RUNS.iterdir() if (d / "results.json").is_file()]
    return sorted(dirs)[-1]


def _codes(issues) -> list:
    return [str(i.get("code") or "") for i in issues]


def _verdict(row: dict) -> str:
    """按**冻结预期**判定这次审计的结论走向（只看 blocking 级）。"""
    exp = row.get("expected") or {}
    blocking = [i for i in row.get("issues") or []
                if str(i.get("severity")) in ("visual_risk", "blocking", "hard")]
    want = exp.get("verdict")
    if want == "legal":
        return "误报" if blocking else "正确放行"
    return "正确检出" if blocking else "漏报"


def main() -> int:
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest()
    data = json.loads((run / "results.json").read_text(encoding="utf-8"))
    print("# S06 审计契约对照  run=%s  model=%s  sends=%d"
          % (run.name, data["meta"]["model"], data["sends"]))
    print("old=%s  new=%s" % (data["meta"]["old_prompt_version"],
                              data["meta"]["new_prompt_version"]))
    print()
    print("| 样本 | 契约 | status | issues | blocking | 判定 | 耗时 | tokens |")
    print("|---|---|---|---|---|---|---|---|")
    for r in data["results"]:
        usage = r.get("usage") or {}
        toks = usage.get("total_tokens") or usage.get("input_tokens")
        print("| %s | %s | %s | %d | %d | %s | %.1fs | %s |"
              % (r["sample"], r["contract"], r.get("status"),
                 len(r.get("issues") or []), r.get("blocking_count", 0),
                 _verdict(r), r.get("elapsed_sec") or 0, toks))
    print()
    for r in data["results"]:
        print("## %s / %s  -> %s" % (r["sample"], r["contract"], _verdict(r)))
        for i in r.get("issues") or []:
            print("   - [%s] %s @shot%s %s | %s"
                  % (i.get("severity"), i.get("code"), i.get("shot_id"),
                     i.get("field_path"), str(i.get("message") or "")[:90]))
        if r.get("status") != "ok":
            print("   ! status=%s error=%s" % (r.get("status"), r.get("error")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
