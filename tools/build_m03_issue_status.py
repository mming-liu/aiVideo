"""M03：用 S01 的人工裁决 + 冻结终局审计，生成「已确认问题状态台账」.

输入（全部冻结、只读）：
  tests/step01_script_gen/fixtures/u6_confirmed_issues.json   —— 人工裁决（身份 + 处置）
  baselines/.../s06_ab/phase2_20261112T072011/final_audit.json —— 终局审计（本次观察）
输出：
  docs/step01-layered-execution/artifacts/m03/issue_status.{json,md}

它回答的是：**同一份未变稿件**上，7 条确认缺陷里有几条被再次报出、几条没被报出。
「没被报出」只记 `not_detected_this_round`，不关闭——关闭需要修订证据或重新裁决。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIRMED = ROOT / "tests/step01_script_gen/fixtures/u6_confirmed_issues.json"
OBSERVED = (ROOT / "baselines/2026-11-12-pre-layered/s06_ab"
            / "phase2_20261112T072011/final_audit.json")
OUT = ROOT / "docs/step01-layered-execution/artifacts/m03"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirmed", default=str(CONFIRMED))
    ap.add_argument("--observed", default=str(OBSERVED))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from step01_script_gen.issue_ledger import build_issue_ledger, render_markdown

    confirmed_path = Path(args.confirmed)
    observed_path = Path(args.observed)
    for path in (confirmed_path, observed_path):
        if not path.exists():
            raise SystemExit("输入缺失（已声明外部数据集）：%s" % path)

    fixture = json.loads(confirmed_path.read_text(encoding="utf-8"))
    observed = json.loads(observed_path.read_text(encoding="utf-8"))

    ledger = build_issue_ledger(
        fixture.get("issues") or [],
        observed.get("issues") or [],
        candidate_revision=str(observed.get("input_revision") or
                               (fixture.get("candidate") or {}).get("revision") or ""),
    )
    ledger["inputs"] = {
        "confirmed": str(confirmed_path.relative_to(ROOT)).replace("\\", "/"),
        "observed": str(observed_path.relative_to(ROOT)).replace("\\", "/"),
        "audit_prompt_version": observed.get("audit_prompt_version"),
        "observed_status": observed.get("status"),
        "candidate": fixture.get("candidate"),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "issue_status.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "issue_status.md").write_text(render_markdown(ledger), encoding="utf-8")
    print(json.dumps(ledger["summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
