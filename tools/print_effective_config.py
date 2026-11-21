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


def _module_consts(tree: ast.Module) -> dict[str, str]:
    consts: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
    return consts


def _unwrap_conversion(node):
    """`str(x)` / `int(x)` / `float(x)` 包一层的默认值：取里面的 x（`os.getenv(n, str(default))`）."""
    while (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id in ("str", "int", "float") and len(node.args) == 1
           and not node.keywords):
        node = node.args[0]
    return node


def _callee_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _params(fn) -> list[str]:
    a = fn.args
    return [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]


def _arg_for(call: ast.Call, fn_params: list[str], pos: int):
    """调用里对应包装函数第 pos 个形参的实参节点（位置或关键字）."""
    if pos < len(call.args) and not any(isinstance(a, ast.Starred) for a in call.args[:pos + 1]):
        return call.args[pos]
    for kw in call.keywords:
        if kw.arg == fn_params[pos]:
            return kw.value
    return None


def find_wrappers(trees: dict[str, ast.Module]) -> dict[str, dict]:
    """包装函数：形参直接（或经已知包装函数）流入环境变量读取.

    返回 函数名 → {"params": [...], "name_pos": [形参位置...], "default_pos": {名字位: 默认值位}}。
    按名字匹配（同名函数视为同一包装），迭代到不动点以覆盖「包装的包装」。
    """
    wrappers: dict[str, dict] = {}
    # 预先收集每个函数体里的调用与直接读取（只做一次遍历）
    funcs = []
    for tree in trees.values():
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = _params(fn)
                if not params:
                    continue
                direct, calls = [], []
                for node in ast.walk(fn):
                    hit = _env_call_arg(node)
                    if hit:
                        direct.append(hit)
                    elif isinstance(node, ast.Call):
                        calls.append(node)
                funcs.append((fn.name, params, direct, calls))
    for _ in range(5):                       # 包装层数有限，5 层足够；只增不改，必然收敛
        changed = False
        for fname, params, direct, calls in funcs:
            pairs = list(direct)
            for call in calls:
                callee = _callee_name(call)
                if callee in wrappers and callee != fname:
                    w = wrappers[callee]
                    for p in w["name_pos"]:
                        dpos = w["default_pos"].get(p)
                        pairs.append((_arg_for(call, w["params"], p),
                                      _arg_for(call, w["params"], dpos) if dpos is not None else None))
            found: dict[int, int | None] = {}
            for name_node, default_node in pairs:
                if isinstance(name_node, ast.Name) and name_node.id in params:
                    dpos = None
                    _dn = _unwrap_conversion(default_node)
                    if isinstance(_dn, ast.Name) and _dn.id in params:
                        dpos = params.index(_dn.id)
                    found[params.index(name_node.id)] = dpos
            if not found or fname in wrappers:
                continue                     # 同名函数只登记第一个（按名字匹配的已知近似）
            wrappers[fname] = {"params": params, "name_pos": sorted(found),
                               "default_pos": {k: v for k, v in found.items() if v is not None}}
            changed = True
        if not changed:
            break
    return wrappers


def scan_source(source: str, rel: str, *, wrappers: dict | None = None,
                global_consts: dict | None = None) -> tuple[list[dict], list[dict]]:
    """返回 (静态读取, 动态读取)。静态项：{name, path, line, default, via}.

    `wrappers` 见 `find_wrappers`；`global_consts` 为跨模块常量 名字 → {值...}
    （只在唯一时使用，如 `AUDIT_PROVIDER_ENV = "AUDIT_TEXT_PROVIDER"`）。
    无法求值的读取进入「动态读取」，**不得**静默缺席。
    """
    tree = ast.parse(source)
    consts = _module_consts(tree)
    wrappers = wrappers or {}
    global_consts = global_consts or {}

    def resolve(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        key = (node.id if isinstance(node, ast.Name)
               else node.attr if isinstance(node, ast.Attribute) else None)
        if key is None:
            return None
        if isinstance(node, ast.Name) and key in consts:
            return consts[key]
        vals = global_consts.get(key) or set()
        return next(iter(vals)) if len(vals) == 1 else None

    wrapper_params: dict[int, set] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in wrappers:
            for sub in ast.walk(fn):
                wrapper_params[id(sub)] = set(wrappers[fn.name]["params"])

    static, dynamic = [], []
    for node in ast.walk(tree):
        pairs = []
        hit = _env_call_arg(node)
        if hit:
            pairs.append((hit[0], hit[1], "direct"))
        elif isinstance(node, ast.Call) and _callee_name(node) in wrappers:
            w = wrappers[_callee_name(node)]
            for p in w["name_pos"]:
                dpos = w["default_pos"].get(p)
                pairs.append((_arg_for(node, w["params"], p),
                              _arg_for(node, w["params"], dpos) if dpos is not None else None,
                              _callee_name(node)))
        for name_node, default_node, via in pairs:
            if name_node is None:
                continue
            # 包装函数自身把形参传下去：那是包装，不是一次读取
            if (isinstance(name_node, ast.Name)
                    and name_node.id in wrapper_params.get(id(node), ())):
                continue
            name = resolve(name_node)
            default = None
            _dn = _unwrap_conversion(default_node)
            if isinstance(_dn, ast.Constant):
                default = None if _dn.value is None else str(_dn.value)
            if name:
                static.append({"name": name, "path": rel, "line": node.lineno,
                               "default": default, "via": via})
            else:
                dynamic.append({"path": rel, "line": node.lineno, "via": via,
                                "expr": ast.unparse(name_node)[:80]})
    return static, dynamic


def scan(root: Path, dirs: tuple[str, ...]) -> tuple[dict[str, dict], list[dict]]:
    names: dict[str, dict] = {}
    dynamic: list[dict] = []
    trees: dict[str, ast.Module] = {}
    sources: dict[str, str] = {}
    # 包装函数与跨模块常量按**全部**项目目录收集（包装可能定义在别的阶段目录）
    for top in dict.fromkeys((*STAGES["all"], *dirs)):
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                src = path.read_text(encoding="utf-8-sig")
                trees[rel] = ast.parse(src)
                sources[rel] = src
            except (SyntaxError, UnicodeDecodeError) as exc:
                if rel.split("/", 1)[0] in dirs:
                    dynamic.append({"path": rel, "line": 0, "expr": f"<无法解析: {exc}>"})
    wrappers = find_wrappers(trees)
    global_consts: dict[str, set] = {}
    for tree in trees.values():
        for k, v in _module_consts(tree).items():
            global_consts.setdefault(k, set()).add(v)
    for rel, src in sources.items():
        if rel.split("/", 1)[0] not in dirs:
            continue
        st, dy = scan_source(src, rel, wrappers=wrappers, global_consts=global_consts)
        dynamic.extend(dy)
        for item in st:
            entry = names.setdefault(item["name"], {"reads": [], "defaults": []})
            entry["reads"].append(f"{item['path']}:{item['line']}")
            if item["default"] is not None and item["default"] not in entry["defaults"]:
                entry["defaults"].append(item["default"])
    return names, dynamic


# --------------------------------------------------------------------------- 取值
def resolve_sources(names, shell: dict, content: dict, keys: dict, post_env: dict,
                    cli: dict, defaults: dict | None = None,
                    actual: dict | None = None) -> dict[str, dict]:
    """纯函数：每个名字 → {value, source}；`defaults` 为 名字 → 默认值列表.

    `content` / `keys` 只用于判定**来源**（键是否在该文件里）。`actual` 为生产加载
    语义（`load_dotenv(override=False)` 之后的 `os.environ`）下的实际值：给出时，
    所有非代码默认来源的**值**一律取它——dotenv 插值（`${BASE}`）引用的变量会被
    shell 覆盖，单独解析文件得到的值与实际加载值可能不同（2026-11-19 复核 P2-3）。
    """
    defaults = defaults or {}
    actual = actual if actual is not None else {}
    out: dict[str, dict] = {}
    for name in names:
        if name in cli:
            value, source = cli[name], "cli"
        elif name in shell:
            value, source = shell[name], "shell"
        elif name in content:
            value, source = content[name], ".env.content"
        elif name in keys:
            value, source = keys[name], ".env.keys"
        elif name in post_env:
            value, source = post_env[name], "adoption_receipt"
        elif defaults.get(name):
            vals = defaults[name]
            value, source = vals[0], "code_default"
        else:
            value, source = None, "unset"
        if source not in ("code_default", "unset") and name in actual:
            value = actual[name]
        out[name] = {"value": None if value is None else str(value), "source": source}
    return out


def mask(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if _SECRET_NAME_RE.search(name) or _SECRET_VALUE_RE.search(value):
        return f"<已设置，长度 {len(value)}>" if value else "<空>"
    return value


def _dotenv(path: Path) -> dict:
    """配置文件里**出现了哪些键**（不插值；值只作无实际加载值时的兜底）."""
    if not path.exists():
        return {}
    from dotenv import dotenv_values
    return {k: v for k, v in dotenv_values(path, interpolate=False).items() if v is not None}


#: 运行前必须核对的配置（AGENTS.md「授权与费用」/ 时长合同）。即使源码扫描没解析到
#: 读取点也要出现在报告里，并标明 `scanned=False`——不能直接缺席。
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "step01": ("TEXT_PROVIDER", "AGENT_PLAN_TEXT_MODEL", "AGENT_PLAN_TEXT_BASE_URL",
               "AUDIT_TEXT_PROVIDER", "AUDIT_TEXT_MODEL",
               "REPAIR_TEXT_PROVIDER", "REPAIR_TEXT_MODEL",
               "DURATION_MIN_SEC", "DURATION_MAX_SEC",
               "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC"),
    "step02a": ("IMAGE_PROVIDER", "QC_PROVIDER", "QC_MAX_ATTEMPTS"),
    "step02b": ("VIDEO_PROVIDER", "VIDEO_REFERENCE_MODE"),
    "step03": ("TTS_PROVIDER",),
    "step04": ("MERGE_AUDIO_MODE",),
}
REQUIRED_KEYS["all"] = tuple(dict.fromkeys(k for v in REQUIRED_KEYS.values() for k in v))


def production_load(root: Path, *, import_config: bool = True) -> dict:
    """按生产语义加载配置，返回加载后的 `os.environ` 快照.

    与 `shared/config.py` 顶部完全一致：先 `.env.content`、再 `.env.keys`，都是
    `load_dotenv(override=False)`——已有的进程环境不被覆盖，插值 `${X}` 也按这一
    语义取值。随后导入 `shared.config`，触发采纳收据对 `DURATION_*` 的继承。
    """
    from dotenv import load_dotenv
    load_dotenv(root / ".env.content")
    load_dotenv(root / ".env.keys")
    if import_config:
        import shared.config  # noqa: F401
    return dict(os.environ)


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
    post_env = production_load(root)
    # 来源由 content/keys 的键判定；post_only 只用于「导入后新增」的键（采纳收据）
    post_only = {k: v for k, v in post_env.items()
                 if k not in shell and k not in content and k not in keys}
    defaults = {n: e["defaults"] for n, e in names.items()}
    required = REQUIRED_KEYS.get(stage, ())
    all_names = sorted(set(names) | set(required))
    resolved = resolve_sources(all_names, shell, content, keys, post_only, cli, defaults,
                               actual=post_env)
    rows = []
    for name in all_names:
        entry = names.get(name, {"reads": [], "defaults": []})
        r = resolved[name]
        rows.append({
            "name": name, "value": mask(name, r["value"]), "source": r["source"],
            "code_defaults": [mask(name, d) for d in entry["defaults"]],
            "default_conflict": len(entry["defaults"]) > 1,
            "read_sites": len(entry["reads"]), "reads": entry["reads"][:5],
            "required": name in required, "scanned": name in names,
        })
    configured = set(content) | set(keys)
    return {
        "stage": stage, "dirs": list(STAGES[stage]), "variables": rows,
        "dynamic_reads": dynamic,
        "configured_but_not_read": sorted(n for n in configured if n not in names
                                          and n in content),
        "code_default_only": sum(1 for r in rows if r["source"] == "code_default"),
        "required_unscanned": [r["name"] for r in rows if r["required"] and not r["scanned"]],
        "note": "阶段归属按目录近似；shared 计入每个阶段。",
    }


def _print_text(report: dict) -> None:
    print(f"# stage={report['stage']}  dirs={' '.join(report['dirs'])}")
    print(f"# {report['note']}")
    width = max((len(r["name"]) for r in report["variables"]), default=10)
    for r in report["variables"]:
        flag = "  [默认值冲突]" if r["default_conflict"] else ""
        if r["required"]:
            flag += "  [必核]" if r["scanned"] else "  [必核·源码未解析到读取点]"
        print(f"{r['name']:<{width}}  {r['source']:<16} {r['value']!s}{flag}")
    if report["required_unscanned"]:
        print("# ⚠ 必核配置未在源码中解析到读取点（值按环境/配置文件报告，读取路径需人工核对）："
              + " ".join(report["required_unscanned"]))
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
    ap.add_argument("--out", type=Path, help="把报告写到文件（UTF-8），不依赖 shell 重定向编码")
    args = ap.parse_args(argv)
    # Windows 管道下默认按本地代码页输出，中文值会乱码；统一 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if str(args.root.resolve()) not in sys.path:
        sys.path.insert(0, str(args.root.resolve()))
    report = collect(args.stage, root=args.root.resolve(), chapter=args.chapter,
                     book=args.book, book_name=args.book_name)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入 {args.out}")
    elif args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
