# -*- coding: utf-8 -*-
"""按 nodeid 逐条对账两份 JUnit XML（离线）.

用法::

    python tools/compare_junit.py BASE.xml NEW.xml [--allow FILE]

判定：
* 两边都有、结果相同 → 一致；
* 只在 NEW 出现且 passed → 「本包新增」，不算差异；
* 其余（结果变化、NEW 缺失、NEW 新增但非 passed）→ 差异，逐条输出；
* ``--allow`` 文件（每行一个 nodeid，``#`` 注释）列出的差异视为已解释。

退出码：存在未解释差异返回 1，否则 0。
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def outcomes(path: Path) -> dict[str, str]:
    """nodeid（classname::name）→ passed / failed / error / skipped."""
    out: dict[str, str] = {}
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
        state = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                state = "failed" if child.tag == "failure" else "error"
                break
            if child.tag == "skipped":
                # xfail 在 junit 里也记为 skipped；用 message 区分
                msg = (child.get("type") or "") + (child.get("message") or "")
                state = "xfailed" if "xfail" in msg.lower() else "skipped"
        if nodeid in out and out[nodeid] != state:
            state = f"{out[nodeid]}+{state}"
        out[nodeid] = state
    return out


def compare(base: dict[str, str], new: dict[str, str], allow: set[str]) -> dict:
    changed, missing, new_bad, added = [], [], [], []
    for nid, st in base.items():
        if nid not in new:
            missing.append((nid, st, None))
        elif new[nid] != st:
            changed.append((nid, st, new[nid]))
    for nid, st in new.items():
        if nid in base:
            continue
        (added if st == "passed" else new_bad).append((nid, None, st))
    unexplained = [d for d in changed + missing + new_bad if d[0] not in allow]
    return {"changed": changed, "missing": missing, "new_not_passed": new_bad,
            "added_passed": added, "unexplained": unexplained}


def _load_allow(path: Path | None) -> set[str]:
    if not path:
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {ln.split("#", 1)[0].strip() for ln in lines if ln.split("#", 1)[0].strip()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--allow", type=Path)
    args = ap.parse_args(argv)
    base, new = outcomes(args.base), outcomes(args.new)
    res = compare(base, new, _load_allow(args.allow))
    print("base:", dict(Counter(base.values())))
    print("new: ", dict(Counter(new.values())))
    print(f"added_passed={len(res['added_passed'])} changed={len(res['changed'])} "
          f"missing={len(res['missing'])} new_not_passed={len(res['new_not_passed'])}")
    for label in ("changed", "missing", "new_not_passed"):
        for nid, a, b in res[label]:
            mark = "  (allowed)" if (nid, a, b) not in res["unexplained"] else ""
            print(f"  {label}: {nid}: {a} -> {b}{mark}")
    print(f"unexplained={len(res['unexplained'])}")
    return 1 if res["unexplained"] else 0


if __name__ == "__main__":
    sys.exit(main())
