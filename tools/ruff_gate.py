# -*- coding: utf-8 -*-
"""ruff 棘轮门禁：F821/F401/F811 只许少、不许多（离线）.

用法::

    python tools/ruff_gate.py            # 对比 tools/ruff_baseline.json，有新增返回 1
    python tools/ruff_gate.py --update   # 用当前读数重写基线（只允许下降）

比较口径：按 ``(路径, code, 名字)`` 的多重集合，不看行号（行号随编辑漂移）。
名字取 ruff message 里第一个反引号内的内容。F821（未定义名字）基线必须为空。

ruff 版本固定在 `requirements-dev.txt`；未安装时返回 2 并提示安装命令。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "ruff_baseline.json"
SELECT = "F821,F401,F811"
_NAME_RE = re.compile(r"`([^`]+)`")

Key = tuple  # (path, code, name)


def _ruff_cmd() -> list[str] | None:
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    try:
        import ruff  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "ruff"]


def run_ruff() -> list[dict]:
    cmd = _ruff_cmd()
    if cmd is None:
        raise FileNotFoundError("未安装 ruff：pip install -r requirements-dev.txt")
    proc = subprocess.run([*cmd, "check", "--no-cache", "--select", SELECT,
                           "--output-format", "json", "."],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ruff 执行失败 rc={proc.returncode}: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout or "[]")


def to_counter(diagnostics: list[dict], root: Path = ROOT) -> Counter:
    out: Counter = Counter()
    for d in diagnostics:
        path = Path(d.get("filename", ""))
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()
        m = _NAME_RE.search(d.get("message", "") or "")
        out[(rel, d.get("code", ""), m.group(1) if m else "")] += 1
    return out


def load_baseline(path: Path | None = None) -> Counter:
    path = BASELINE if path is None else path
    if not path.exists():
        return Counter()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return Counter({(r["path"], r["code"], r["name"]): int(r["count"]) for r in rows})


def dump_baseline(counter: Counter, path: Path | None = None) -> None:
    path = BASELINE if path is None else path
    rows = [{"path": p, "code": c, "name": n, "count": k}
            for (p, c, n), k in sorted(counter.items()) if k > 0]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def compare(current: Counter, baseline: Counter) -> tuple[list, list]:
    """返回 (新增, 减少)，每项为 (key, 差值)."""
    added = [(k, current[k] - baseline.get(k, 0)) for k in sorted(current)
             if current[k] > baseline.get(k, 0)]
    removed = [(k, baseline[k] - current.get(k, 0)) for k in sorted(baseline)
               if baseline[k] > current.get(k, 0)]
    return added, removed


def offset_moves(added: list, removed: list, moved_from: str, moved_to: str):
    """代码搬迁抵消：`moved_to` 前缀下新增、`moved_from` 下减少的同 (code, 名字) 条目互相抵消.

    返回 (剩余新增, 抵消明细)。抵消明细逐条列出，写进交付记录。
    """
    pool: dict = {}
    for (path, code, name), k in removed:
        if path == moved_from:
            pool[(code, name)] = pool.get((code, name), 0) + k
    rest, offsets = [], []
    for (path, code, name), k in added:
        if path.startswith(moved_to) and pool.get((code, name), 0) > 0:
            take = min(k, pool[(code, name)])
            pool[(code, name)] -= take
            offsets.append((code, name, moved_from, path, take))
            if k - take:
                rest.append(((path, code, name), k - take))
        else:
            rest.append(((path, code, name), k))
    return rest, offsets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--init", action="store_true", help="基线文件不存在时写入首份基线")
    ap.add_argument("--moved-from", help="代码搬迁的源文件（相对路径），与 --moved-to 一起用")
    ap.add_argument("--moved-to", help="代码搬迁的目标路径前缀")
    args = ap.parse_args(argv)
    try:
        current = to_counter(run_ruff())
    except FileNotFoundError as exc:
        print(exc)
        return 2
    if args.init:
        if BASELINE.exists():
            print(f"基线已存在：{BASELINE}（只能用 --update 下调）")
            return 1
        if any(c == "F821" for (_, c, _), _k in current.items()):
            print("F821（未定义名字）必须先清零，不能写进基线")
            return 1
        dump_baseline(current)
        print(f"已写入首份基线：{sum(current.values())} 条")
        return 0
    baseline = load_baseline()
    added, removed = compare(current, baseline)
    if args.moved_from and args.moved_to:
        added, offsets = offset_moves(added, removed, args.moved_from, args.moved_to)
        for code, name, src, dst, k in offsets:
            print(f"  ~ 搬迁抵消 {code} `{name}` x{k}: {src} → {dst}")
    totals = Counter()
    for (_, code, _), k in current.items():
        totals[code] += k
    print("current:", dict(sorted(totals.items())))
    if args.update:
        if added:
            print("拒绝更新：存在新增条目（基线只允许下降）")
            for (p, c, n), k in added:
                print(f"  + {c} {p} `{n}` x{k}")
            return 1
        dump_baseline(current)
        print(f"基线已更新（减少 {sum(k for _, k in removed)} 条）")
        return 0
    for (p, c, n), k in added:
        print(f"  + {c} {p} `{n}` x{k}")
    if removed:
        print(f"比基线少 {sum(k for _, k in removed)} 条，可运行 --update 下调基线")
    if any(c == "F821" for (_, c, _), _k in current.items()):
        print("F821（未定义名字）必须为 0")
        return 1
    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main())
