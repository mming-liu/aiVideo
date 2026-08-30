"""一次性扫描 script_result.json 里的锚块混入 — 只统计，不修改."""
from __future__ import annotations
import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("output/video/锦衣夜行/part_01_杀青州/chapter_001/script_result.json",
          "r", encoding="utf-8") as f:
    data = json.load(f)

PATTERNS = [
    (r"【角色锚定】[^【\n]*", "【角色锚定】"),
    (r"【道具锚定】[^【\n]*", "【道具锚定】"),
    (r"【武器归属】[^【\n]*", "【武器归属】"),
    (r"【画面人数硬锚】[^【\n]*", "【画面人数硬锚】"),
    (r"该[^\n。]*无篷[^\n。]*舴艋[^\n。]*", "船尾句-舴艋"),
    (r"该[^\n。]*窄身[^\n。]*舴艋[^\n。]*", "船尾句-窄身"),
    (r"该[^\n。]*无篷[^\n。]*", "船尾句-无篷"),
]

counts = {label: 0 for _, label in PATTERNS}
samples = {label: [] for _, label in PATTERNS}


def scan(obj, path=""):
    for pat, label in PATTERNS:
        text = obj if isinstance(obj, str) else None
        if text is None:
            continue
        for m in re.finditer(pat, text):
            counts[label] += 1
            if len(samples[label]) < 3:
                samples[label].append(f"  {path}: {m.group()[:80]}")


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        scan(obj, path)


walk(data)

print("=== script_result.json 锚块/船尾句混入统计 ===")
total = 0
for label, n in counts.items():
    print(f"  {label}: {n}")
    total += n
    for s in samples[label]:
        print(s)
print(f"\n总命中: {total}")
