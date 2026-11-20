# -*- coding: utf-8 -*-
"""扫描「import 失败被静默吞掉」的位点（只读、离线）.

口径：``try`` 的第一条语句是 import，且某个 handler 是裸 ``except`` 或
``except Exception/BaseException``，handler 体只有 ``pass``/``continue``。

用法::

    python tools/scan_import_swallow.py            # 文本清单
    python tools/scan_import_swallow.py --json     # JSON

每条带 ``group``：A = 不在 Step01 规则指纹文件清单内；B = 在清单内（改动会让
`RULE_SOURCE_FINGERPRINT` 变化）。`shared/style_prefix.py` / `shared/vimax_bridge.py`
只有部分符号进指纹，按 A 记，是否影响指纹以改后实测为准。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN_ROOTS = ("step01_script_gen", "shared", "step02a_frame_gen", "step02b_video_gen",
              "step02_video_gen", "step03_tts_audio", "step04_merge", "_text_gen",
              "_image_gen", "_video_gen", "tts")

#: 与 `step01_script_gen/quality_issues._compute_rule_source_fingerprint` 的文件清单同步。
_RULE_FILES = {
    "step01_script_gen/" + n for n in (
        "quality_issues.py", "kf_coverage.py", "state_updates.py", "validation.py",
        "timecode.py", "split_visuals.py", "audit.py", "cross_field_checks.py",
        "continuity_semantics.py", "kf_structural_guard.py", "subject_attribution.py",
        "weapon_states.py", "rules_index.py")
} | {
    "shared/" + n for n in (
        "motion_segments.py", "quality_gate.py", "semantic_barrier.py",
        "script_preflight.py", "prompt_contradiction.py", "prompt_contradiction_lexicon.py")
} | {"_text_gen/deepseek_schema.py"}
_RULE_DIRS = ("step01_script_gen/constraints/", "step01_script_gen/prompt_semantic/")


def rule_group(rel: str) -> str:
    return "B" if rel in _RULE_FILES or rel.startswith(_RULE_DIRS) else "A"


def _is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return all(isinstance(b, (ast.Pass, ast.Continue)) for b in handler.body)


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """Try 节点 id → 最内层函数名（模块顶层为 ``<module>``）."""
    out: dict[int, str] = {}

    def walk(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Try):
                out[id(child)] = fn
            walk(child, name)

    walk(tree, "<module>")
    return out


def scan_source(source: str, rel: str) -> list[dict]:
    tree = ast.parse(source)
    funcs = _enclosing_functions(tree)
    sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Try) and node.body
                and isinstance(node.body[0], (ast.Import, ast.ImportFrom))):
            continue
        if not any(_is_broad(h) and _is_silent(h) for h in node.handlers):
            continue
        sites.append({
            "path": rel, "line": node.lineno, "function": funcs.get(id(node), "<module>"),
            "import": ast.unparse(node.body[0]), "extra_stmts": len(node.body) - 1,
            "group": rule_group(rel),
        })
    return sites


def scan(root: Path = ROOT) -> list[dict]:
    out = []
    for top in SCAN_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            out.extend(scan_source(path.read_text(encoding="utf-8-sig"), rel))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    sites = scan()
    if args.json:
        print(json.dumps(sites, ensure_ascii=False, indent=1))
    else:
        for s in sites:
            print(f"{s['group']} {s['path']}:{s['line']} {s['function']} "
                  f"+{s['extra_stmts']} {s['import'][:80]}")
        a = sum(1 for s in sites if s["group"] == "A")
        print(f"total={len(sites)} A={a} B={len(sites) - a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
