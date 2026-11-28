# -*- coding: utf-8 -*-
"""跨章连续性工具（2026-11-28 O9，只读/零调用）.

两种用法：

    # 1) 从已提交剧本提取章末状态（供下一章规划参考）
    python tools/chapter_continuity.py --script <chapter_dir>/script_result.json \
        --chapter 1 --out <chapter_dir>/chapter_end_state.json

    # 2) 用前章终态核对新章开场（advisory，不阻断）
    python tools/chapter_continuity.py --prev <chapter_001>/chapter_end_state.json \
        --script <chapter_002>/script_result.json --chapter 2 --json

退出码：0 = 无告警；1 = 有 advisory 告警（仍属正常，便于 CI 区分）；2 = 输入错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.chapter_continuity import (compare_continuity, extract_end_state,
                                       load_end_state, write_end_state)


def _load_script(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("shots"):
        raise ValueError("剧本缺少 shots: %s" % path)
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨章连续性：终态提取与承接告警（只读）")
    ap.add_argument("--script", required=True, help="本章 script_result.json")
    ap.add_argument("--chapter", type=int, default=None, help="本章章号")
    ap.add_argument("--out", default="", help="提取模式：章末状态输出路径")
    ap.add_argument("--prev", default="", help="核对模式：前章 chapter_end_state.json")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        script = _load_script(Path(args.script))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("[ERROR] 读取剧本失败: %s" % exc)
        return 2

    if args.prev:
        try:
            prev = load_end_state(Path(args.prev))
        except ValueError as exc:
            print("[ERROR] %s" % exc)
            return 2
        problems = compare_continuity(prev, script)
        payload = {"previous": str(args.prev), "script": str(args.script),
                   "problem_count": len(problems), "problems": problems}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if not problems:
                print("[OK] 未发现跨章断裂告警")
            for item in problems:
                print("[WARN] %-24s %s" % (item["code"], item["message"]))
        return 1 if problems else 0

    state = extract_end_state(script, chapter=args.chapter)
    if args.out:
        write_end_state(script, Path(args.out), chapter=args.chapter)
        print("[OK] 章末状态已写入: %s" % args.out)
    if args.json or not args.out:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
