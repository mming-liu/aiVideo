# -*- coding: utf-8 -*-
"""脚本级确定性预检 **CLI**（判据实现见 `shared/script_preflight.py`）.

2026-11-14 Step C：判据已迁到 `shared/` 并被 Step 01 确定性质量门直接消费；
本文件只保留命令行入口与输出格式，避免「预检」和「主门」各有一份规则。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.script_preflight import (                      # noqa: E402,F401
    EXIT_CLEAN,
    EXIT_FINDINGS,
    check_duplicate_injection,
    check_entity_class_conflict,
    check_image_prompt,
    check_layout_presence,
    check_location_drift,
    check_prop_chain,
    check_rules,
    check_stage_progress,
    run,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="分级脚本预检（确定性，零调用）")
    parser.add_argument("script")
    parser.add_argument("--json", default=None, help="把完整报告写到该路径")
    parser.add_argument("--include-rules", action="store_true",
                        help="同时跑 quality_issues 的 9 个单镜规则族")
    parser.add_argument("--fail-on", choices=("hard", "advisory", "none"), default="hard")
    parser.add_argument("--show-info", action="store_true",
                        help="同时列出 info 级（如「这一镜只是没再用某道具」）")
    args = parser.parse_args()

    path = Path(args.script)
    script = json.loads(path.read_text(encoding="utf-8-sig"))
    issues = run(script, include_rules=args.include_rules)

    hard = [i for i in issues if i.get("severity") == "hard"]
    advis = [i for i in issues if i.get("severity") == "advisory"]
    infos = [i for i in issues if i.get("severity") not in ("hard", "advisory")]
    report = {
        "schema": "step01_preflight/1",
        "script": str(path),
        "shot_count": len(script.get("shots") or []),
        "hard_count": len(hard),
        "advisory_count": len(advis),
        "info_count": len(infos),
        "by_code": {},
        "issues": issues,
    }
    for i in issues:
        report["by_code"][i["code"]] = report["by_code"].get(i["code"], 0) + 1

    print("脚本：%s（%d 镜）" % (path, report["shot_count"]))
    print("hard %d / advisory %d / info %d" %
          (report["hard_count"], report["advisory_count"], report["info_count"]))
    for i in hard:
        print("  [HARD] shot %s %s  %s" % (i.get("shot"), i.get("code"), i.get("message")))
    for i in advis:
        print(("  [adv ] shot %s %s  %s"
               % (i.get("shot"), i.get("code"), i.get("message")))[:200])
    if args.show_info:
        for i in infos:
            print(("  [info] shot %s %s  %s"
                   % (i.get("shot"), i.get("code"), i.get("message")))[:200])

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告已写出：%s" % args.json)

    if args.fail_on == "hard" and hard:
        return EXIT_FINDINGS
    if args.fail_on == "advisory" and issues:
        return EXIT_FINDINGS
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
