#!/usr/bin/env python3
"""上一版结果快照的比对工具。

用途：合并后的代码覆盖 Step01 原判定后，用来回答「修复效果是变好还是变差」。
本脚本只读，不调用任何模型 / 媒体 / 网络，也不修改仓库文件。

用法：
  # 1) 备份完整性自检
  python compare.py --verify

  # 2) 比对两份判定结果的 findings 差异（旧判定 vs 当前代码跑出的判定）
  python compare.py --old step01_outputs/chapter_001/step01_audit.json --new <当前产出的 json>

  # 3) 用当前代码的语义检查器重跑三族夹具，与上一版声明的预期缺陷码对照
  python compare.py --fixtures

  # 4) 查看上一版回归读数，并与当前读数做数值对比
  python compare.py --regression --current <当前读数 json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "regression_baseline.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify() -> int:
    manifest_path = HERE / "manifest.json"
    if not manifest_path.is_file():
        print("manifest.json 缺失，无法自检")
        return 2
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad = []
    for row in m.get("files", []):
        p = HERE / row["path"]
        if not p.is_file():
            bad.append((row["path"], "missing"))
        elif sha256(p) != row["sha256"]:
            bad.append((row["path"], "digest mismatch"))
    total = len(m.get("files", []))
    print("manifest files: %d, mismatches: %d" % (total, len(bad)))
    for path, why in bad[:20]:
        print("  BAD", why, path)
    return 1 if bad else 0


def extract_findings(obj, prefix: str = "") -> dict:
    """把任意结构的判定 json 归一成 {key: record}。

    key = (code, path, shot)；这样同一缺陷在不同字段名下也能对齐。
    只收集含字符串 code 的 dict，其余原样跳过。
    """
    out: dict[tuple, dict] = {}
    seen_ids = set()

    def walk(node, loc):
        if id(node) in seen_ids:
            return
        if isinstance(node, dict):
            seen_ids.add(id(node))
            code = node.get("code")
            if isinstance(code, str) and code:
                path = str(node.get("path") or node.get("field") or loc or "")
                shot = str(node.get("shot_index") or node.get("shot") or "")
                key = (code, path, shot)
                rec = {
                    "code": code,
                    "severity": str(node.get("severity") or node.get("level") or ""),
                    "path": path,
                    "shot": shot,
                    "message": str(node.get("message") or "")[:160],
                }
                old = out.get(key)
                if old is None or (not old["severity"] and rec["severity"]):
                    out[key] = rec
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, loc + "/" + str(k))
        elif isinstance(node, list):
            seen_ids.add(id(node))
            for i, v in enumerate(node):
                if isinstance(v, (dict, list)):
                    walk(v, loc + "[%d]" % i)

    walk(obj, prefix)
    return out


def compare(old_path: Path, new_path: Path) -> int:
    for p in (old_path, new_path):
        if not p.is_file():
            print("文件不存在:", p)
            return 2
    old = extract_findings(json.loads(old_path.read_text(encoding="utf-8")))
    new = extract_findings(json.loads(new_path.read_text(encoding="utf-8")))
    ok, onk = set(old), set(new)
    newly = sorted(onk - ok)
    lost = sorted(ok - onk)
    changed = []
    for k in sorted(ok & onk):
        if old[k]["severity"] != new[k]["severity"]:
            changed.append((k, old[k]["severity"], new[k]["severity"]))

    def fmt(k):
        return "%s | path=%s | shot=%s" % (k[0], k[1] or "-", k[2] or "-")

    print("OLD:", old_path, " findings=%d" % len(ok))
    print("NEW:", new_path, " findings=%d" % len(onk))
    print("\n-- 新版新增检出 (%d) --" % len(newly))
    print("\n".join("  + " + fmt(k) for k in newly) or "  (none)")
    print("\n-- 新版不再检出 (%d) --" % len(lost))
    print("\n".join("  - " + fmt(k) for k in lost) or "  (none)")
    print("\n-- 严重度变化 (%d) --" % len(changed))
    print("\n".join("  ~ %s : %s -> %s" % (fmt(k), a, b) for k, a, b in changed) or "  (none)")
    print("\n-- 判读规则（不自动下结论）--")
    print("  新增检出：可能是漏检减少（变好），也可能是误报增加（变差），需逐项裁定。")
    print("  不再检出：可能是误拒减少（变好），也可能是漏检增加（变差），需逐项裁定。")
    print("  合法时间省略、画外声音、听者反应是正例，不应出现在任何一版的检出里。")
    print("  请结合 regression_baseline.json 的 s01 裁决表与 S06 A/B 结论人工评阅。")
    return 0


def fixtures() -> int:
    """用当前代码的检查器跑三族夹具，与上一版声明的 expected codes 对照。"""
    repo = HERE.parents[1]  # baselines/<date>/ -> 仓库根
    sys.path.insert(0, str(repo))
    try:
        from shared.execution_semantics import check_execution_semantics, codes_of
    except ImportError as exc:
        print("无法导入当前代码的语义检查器:", exc)
        return 2
    root = repo / "tests" / "shared" / "fixtures" / "execution_contract"
    if not root.is_dir():
        print("三族夹具目录不存在:", root)
        return 2
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))["fixture_expected_codes"]
    all_ok = True
    for name, exp in expected.items():
        f = root / (name + ".json")
        fam = json.loads(f.read_text(encoding="utf-8"))
        for side in ("baseline", "mutation"):
            item = fam[side]
            cand = item["execution"]["candidate"]
            got = codes_of(check_execution_semantics(item["script"], cand))
            want = sorted(exp[side])
            ok = got == want
            all_ok &= ok
            print("%-20s %-9s expected=%-58s got=%-58s %s" % (
                name, side, ",".join(want) or "(none)", ",".join(got) or "(none)",
                "OK" if ok else "DIFF"))
    print("\nALL_FIXTURE_CODES_MATCH:", all_ok)
    print("上一版（bc5c50e / f7d4cf1）对这些夹具没有语义检查器，基线为「全部无检出」；")
    print("六码全检出说明确定性覆盖增加，但不能单独证明真实内容质量变好。")
    return 0 if all_ok else 1


def regression(current: Path | None) -> int:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    print("baseline commit:", data["baseline_commit"])
    print("\n-- 上一版回归读数 --")
    for k, v in data["readings"].items():
        print("  %-38s %s" % (k, json.dumps(v, ensure_ascii=False)))
    if current is not None:
        if not current.is_file():
            print("当前读数文件不存在:", current)
            return 2
        cur = json.loads(current.read_text(encoding="utf-8"))
        print("\n-- 与当前读数对比 --")
        for k, v in cur.items():
            old = data["readings"].get(k)
            if old is None:
                print("  %-38s NEW %s" % (k, json.dumps(v, ensure_ascii=False)))
            else:
                delta = ""
                if isinstance(v, dict) and isinstance(old, dict) and "passed" in v and "passed" in old:
                    d = v["passed"] - old["passed"]
                    delta = " passed %+d" % d
                print("  %-38s old=%s new=%s%s" % (
                    k, json.dumps(old, ensure_ascii=False),
                    json.dumps(v, ensure_ascii=False), delta))
    print("\n注意：本机 safe-delete 确认门会打断单进程全量回归，读数须按同口径分组比较。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="上一版 Step01 结果快照的比对工具")
    ap.add_argument("--verify", action="store_true", help="校验 manifest 中所有 sha256")
    ap.add_argument("--old", type=Path, help="旧判定结果 json（通常取本快照内文件）")
    ap.add_argument("--new", type=Path, help="当前代码跑出的判定结果 json")
    ap.add_argument("--fixtures", action="store_true", help="用当前检查器重跑三族夹具")
    ap.add_argument("--regression", action="store_true", help="打印上一版回归读数")
    ap.add_argument("--current", type=Path, help="配合 --regression 的当前读数 json")
    a = ap.parse_args()
    if a.verify:
        return verify()
    if a.fixtures:
        return fixtures()
    if a.old and a.new:
        return compare(a.old, a.new)
    if a.regression:
        return regression(a.current)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
