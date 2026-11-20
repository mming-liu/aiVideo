# -*- coding: utf-8 -*-
"""打印环境变量的**最终生效值**与来源（只读、离线、零模型调用）.

来源按优先级判定（与 `shared/config.py` 的加载顺序一致）：

1. ``cli``      —— 本工具的 ``--chapter/--book/--book-name``（经 `shared.run_scope.apply_scope`）
2. ``shell``    —— 运行本工具时进程环境里已有的值
3. ``.env.content``
4. ``.env.keys``
   （`shared/config.py` 两次 ``load_dotenv`` 都是 ``override=False``，所以先加载的
   ``.env.content`` 胜过 ``.env.keys``，二者都让位于进程环境）
5. ``adoption_receipt`` —— 导入 `shared.config` 后才出现的值（`DURATION_*` 由本章
   ``step01_adoption.json`` 继承）
6. ``code_default`` —— 代码里 ``os.getenv(NAME, 默认值)`` 的字面默认值
7. ``unset``    —— 以上都没有

变量清单用 AST 从源码收集（``os.getenv`` / ``os.environ.get`` / ``os.environ[...]`` /
``os.environ.setdefault`` 的第一个参数；同模块顶层字符串常量会被解析）。参数为
f-string 等表达式时记为「动态读取」单列。

用法::

    python tools/print_effective_config.py --stage step01
    python tools/print_effective_config.py --stage all --json
    python tools/print_effective_config.py --stage step02a --chapter 2

阶段归属按目录近似：每个阶段 = 阶段目录 + 对应 provider 目录 + ``shared``。
密钥类变量（名字含 KEY/TOKEN/SECRET/PASSWORD，或值形如 ``sk-...``）只输出「已设置（长度 N）」。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STAGES: dict[str, tuple[str, ...]] = {
    "step01": ("step01_script_gen", "_text_gen", "shared"),
    "step02a": ("step02a_frame_gen", "_image_gen", "shared"),
    "step02b": ("step02b_video_gen", "step02_video_gen", "_video_gen", "shared"),
    "step03": ("step03_tts_audio", "tts", "shared"),
    "step04": ("step04_merge", "shared"),
}
STAGES["all"] = tuple(dict.fromkeys(d for dirs in STAGES.values() for d in dirs))

_SECRET_NAME_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.I)
_SECRET_VALUE_RE = re.compile(r"^(sk-|ak-|Bearer\s)", re.I)

SOURCE_ORDER = ("cli", "shell", ".env.content", ".env.keys", "adoption_receipt",
                "code_default", "unset")


# --------------------------------------------------------------------------- 扫描
def _env_call_arg(node: ast.AST):
    """若 node 是读环境变量的表达式，返回 (名字节点, 默认值节点或 None)."""
    if isinstance(node, ast.Call) and node.args:
        fn = ast.unparse(node.func)
        # 覆盖 `os.getenv` 与 `import os as _os` 后的 `_os.getenv` 等别名写法
        if (fn == "getenv" or fn.endswith(".getenv")
                or fn.endswith("environ.get") or fn.endswith("environ.setdefault")):
            default = node.args[1] if len(node.args) > 1 else None
            return node.args[0], default
    if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
            and ast.unparse(node.value).endswith("environ")):
        return node.slice, None
    return None


def scan_source(source: str, rel: str) -> tuple[list[dict], list[dict]]:
    """返回 (静态读取, 动态读取)。静态项：{name, path, line, default}."""
    tree = ast.parse(source)
    consts: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
    static, dynamic = [], []
    for node in ast.walk(tree):
        hit = _env_call_arg(node)
        if not hit:
            continue
        name_node, default_node = hit
        name = None
        if isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
            name = name_node.value
        elif isinstance(name_node, ast.Name) and name_node.id in consts:
            name = consts[name_node.id]
        default = None
        if isinstance(default_node, ast.Constant):
            default = None if default_node.value is None else str(default_node.value)
        if name:
            static.append({"name": name, "path": rel, "line": node.lineno,
                           "default": default})
        else:
            dynamic.append({"path": rel, "line": node.lineno,
                            "expr": ast.unparse(name_node)[:80]})
    return static, dynamic


def scan(root: Path, dirs: tuple[str, ...]) -> tuple[dict[str, dict], list[dict]]:
    names: dict[str, dict] = {}
    dynamic: list[dict] = []
    for top in dirs:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                st, dy = scan_source(path.read_text(encoding="utf-8-sig"), rel)
            except (SyntaxError, UnicodeDecodeError) as exc:
                dynamic.append({"path": rel, "line": 0, "expr": f"<无法解析: {exc}>"})
                continue
            dynamic.extend(dy)
            for item in st:
                entry = names.setdefault(item["name"], {"reads": [], "defaults": []})
                entry["reads"].append(f"{item['path']}:{item['line']}")
                if item["default"] is not None and item["default"] not in entry["defaults"]:
                    entry["defaults"].append(item["default"])
    return names, dynamic


# --------------------------------------------------------------------------- 取值
def resolve_sources(names, shell: dict, content: dict, keys: dict, post_env: dict,
                    cli: dict, defaults: dict | None = None) -> dict[str, dict]:
    """纯函数：每个名字 → {value, source}；`defaults` 为 名字 → 默认值列表."""
    defaults = defaults or {}
    out: dict[str, dict] = {}
    for name in names:
        if name in cli:
            value, source = cli[name], "cli"
        elif name in shell:
            value, source = shell[name], "shell"
        elif content.get(name) is not None:
            value, source = content[name], ".env.content"
        elif keys.get(name) is not None:
            value, source = keys[name], ".env.keys"
        elif name in post_env:
            value, source = post_env[name], "adoption_receipt"
        elif defaults.get(name):
            vals = defaults[name]
            value, source = vals[0], "code_default"
        else:
            value, source = None, "unset"
        out[name] = {"value": None if value is None else str(value), "source": source}
    return out


def mask(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if _SECRET_NAME_RE.search(name) or _SECRET_VALUE_RE.search(value):
        return f"<已设置，长度 {len(value)}>" if value else "<空>"
    return value


def _dotenv(path: Path) -> dict:
    if not path.exists():
        return {}
    from dotenv import dotenv_values
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


class _Silent:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def collect(stage: str, *, root: Path = ROOT, chapter=None, book=None,
            book_name=None) -> dict:
    names, dynamic = scan(root, STAGES[stage])
    shell = dict(os.environ)
    content = _dotenv(root / ".env.content")
    keys = _dotenv(root / ".env.keys")
    cli: dict = {}
    if chapter is not None or book is not None or book_name is not None:
        from shared.run_scope import apply_scope
        resolved = apply_scope(chapter, book, book_name, logger=_Silent(), max_echo=0)
        cli = {k: str(v[1]) for k, v in resolved["overrides"].items()}
    import shared.config  # noqa: F401  触发 dotenv 加载与采纳收据继承
    post_env = dict(os.environ)
    # dotenv 加载出的值来源已由 content/keys 判定；post_env 只用于「导入后新增」的键
    post_only = {k: v for k, v in post_env.items()
                 if k not in shell and k not in content and k not in keys}
    defaults = {n: e["defaults"] for n, e in names.items()}
    resolved = resolve_sources(sorted(names), shell, content, keys, post_only, cli, defaults)
    rows = []
    for name in sorted(names):
        entry = names[name]
        r = resolved[name]
        rows.append({
            "name": name, "value": mask(name, r["value"]), "source": r["source"],
            "code_defaults": [mask(name, d) for d in entry["defaults"]],
            "default_conflict": len(entry["defaults"]) > 1,
            "read_sites": len(entry["reads"]), "reads": entry["reads"][:5],
        })
    configured = set(content) | set(keys)
    return {
        "stage": stage, "dirs": list(STAGES[stage]), "variables": rows,
        "dynamic_reads": dynamic,
        "configured_but_not_read": sorted(n for n in configured if n not in names
                                          and n in content),
        "code_default_only": sum(1 for r in rows if r["source"] == "code_default"),
        "note": "阶段归属按目录近似；shared 计入每个阶段。",
    }


def _print_text(report: dict) -> None:
    print(f"# stage={report['stage']}  dirs={' '.join(report['dirs'])}")
    print(f"# {report['note']}")
    width = max((len(r["name"]) for r in report["variables"]), default=10)
    for r in report["variables"]:
        flag = "  [默认值冲突]" if r["default_conflict"] else ""
        print(f"{r['name']:<{width}}  {r['source']:<16} {r['value']!s}{flag}")
    print(f"# 变量 {len(report['variables'])} 个；仅靠代码默认值 {report['code_default_only']} 个；"
          f"动态读取 {len(report['dynamic_reads'])} 处")
    if report["configured_but_not_read"]:
        print("# .env.content 设置了但本阶段代码未直接读取（可能经其他阶段或间接读取）：")
        print("#   " + " ".join(report["configured_but_not_read"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打印环境变量最终生效值与来源（只读）")
    ap.add_argument("--stage", choices=sorted(STAGES), default="all")
    ap.add_argument("--chapter")
    ap.add_argument("--book")
    ap.add_argument("--book-name")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    # Windows 管道下默认按本地代码页输出，中文值会乱码；统一 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if str(args.root.resolve()) not in sys.path:
        sys.path.insert(0, str(args.root.resolve()))
    report = collect(args.stage, root=args.root.resolve(), chapter=args.chapter,
                     book=args.book, book_name=args.book_name)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
