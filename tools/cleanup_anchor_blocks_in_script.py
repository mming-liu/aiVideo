"""一次性清理 script_result.json 里的锚块混入（v6.32.1，2026-08-29）.

已混入的组装期产物（不应在数据文件里）：
- 【角色锚定】…整段（含「⚠️ 完整小舟在画中」船尾句）
- 未来若有其他组装期产物也加这里

用法：
    python tools/cleanup_anchor_blocks_in_script.py [chapter_dir]

默认清理 output/video/锦衣夜行/part_01_杀青州/chapter_001/script_result.json
会先备份到 script_result.json.bak.<timestamp>，再写回 cleaned 版本。
"""
from __future__ import annotations

import io
import json
import re
import sys
import shutil
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


# ── 待清理的注入块模式 ──────────────────────────────────────────────
# 顺序敏感：先匹配最长的（含 ⚠️ 船尾句），再匹配短的（只有锚块）。
# 每个模式 1 个 capture group（要删的子串）。
_CLEANUP_PATTERNS: list[tuple[str, str]] = [
    # 【角色锚定】整段 + ⚠️ 船尾句（最常见）。
    # 策略：从「⚠️ 完整小舟在画中」开始，吃到下一条独立规则（`**...**` 强调
    # 块或「禁止/必须/严禁」等）前的逗号/句号（连同标点一起吃，避免双标点残留）。
    (
        r"【角色锚定】[^【\n]*?⚠️\s*完整小舟在画中[^【\n]*?[，。（](?=\*\*|禁止|必须|严禁)",
        "【角色锚定】+⚠️船尾句",
    ),
    # 【角色锚定】整段（不含船尾句）
    (
        r"【角色锚定】[^【\n]*?[，。]?(?=\*\*|禁止|必须|严禁|$)",
        "【角色锚定】",
    ),
    # 单独 ⚠️ 船尾句
    (
        r"⚠️\s*完整小舟在画中[^【\n]*?[，。（](?=\*\*|禁止|必须|严禁)",
        "⚠️船尾句",
    ),
]


def cleanup_text(s: str) -> tuple[str, int]:
    """剥除一段文本里的所有注入块，返回 (cleaned, hit_count)."""
    total = 0
    for pat, label in _CLEANUP_PATTERNS:
        new_s, n = re.subn(pat, "", s)
        if n > 0:
            total += n
            new_s = re.sub(r"[ \t]+", " ", new_s)
            new_s = re.sub(r"。+", "。", new_s)
            new_s = new_s.strip()
            s = new_s
    return s, total


def cleanup_obj_inplace(obj) -> tuple[int, list[str]]:
    """原地清理 dict/list 里的所有 str 字段（直接修改对象）."""
    hits = 0
    paths = []

    def walk(o, path=""):
        nonlocal hits
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if isinstance(v, str):
                    new_s, n = cleanup_text(v)
                    if n > 0:
                        o[k] = new_s
                        hits += n
                        paths.append(f"{path}.{k} (×{n})")
                else:
                    walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, str):
                    new_s, n = cleanup_text(v)
                    if n > 0:
                        o[i] = new_s
                        hits += n
                        paths.append(f"{path}[{i}] (×{n})")
                else:
                    walk(v, f"{path}[{i}]")

    walk(obj)
    return hits, paths


def main():
    import argparse
    ap = argparse.ArgumentParser(description="清理 script_result.json 里的锚块混入")
    ap.add_argument("path", nargs="?",
                    default="output/video/锦衣夜行/part_01_杀青州/chapter_001/script_result.json",
                    help="script_result.json 路径（默认：锦衣夜行 chapter_001）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写回")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        print(f"❌ 找不到文件: {p}", file=sys.stderr)
        sys.exit(1)

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    if args.dry_run:
        total = 0
        def count_walk(o):
            nonlocal total
            if isinstance(o, dict):
                for v in o.values(): count_walk(v)
            elif isinstance(o, list):
                for v in o: count_walk(v)
            elif isinstance(o, str):
                _, n = cleanup_text(o)
                total += n
        count_walk(data)
        print(f"[DRY-RUN] 预计清理 {total} 处")
        return

    hits, paths = cleanup_obj_inplace(data)
    if hits == 0:
        print("✓ 无需清理（未发现锚块混入）")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = p.with_suffix(f".json.bak.{ts}")
    shutil.copy2(p, backup)
    print(f"📦 备份: {backup}")

    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✓ 清理 {hits} 处锚块，写回 {p}")
    if len(paths) <= 10:
        for path in paths:
            print(f"    - {path}")
    else:
        for path in paths[:5]:
            print(f"    - {path}")
        print(f"    ... 及其他 {len(paths) - 5} 处")


if __name__ == "__main__":
    main()
