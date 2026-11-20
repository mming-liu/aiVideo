# -*- coding: utf-8 -*-
"""S09：Step 01 编排拆分的静态守卫（离线）.

1. orchestrator 内只允许 `from step01_script_gen.orchestrator import <模块> as <别名>`，
   不得 from-import 迁出的符号（绑定名字会让 `main.X` 打桩失效）；
2. 模块依赖只能指向 `MODULE_ORDER` 中排在前面的模块（无环）；
3. orchestrator 只允许由 `main.py`、orchestrator 自身与测试导入（导入期副作用必须
   排在运行范围 bootstrap 之后）；
4. 全部模块就位后 `main.py` 不超过 1500 行；
5. orchestrator + main.py 的 F401/F811 总数不超过拆分前 main.py（15 条）。

每条守卫另有一个注入违规的对照，证明判据真的会拦。
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.orchestrator import MODULE_ORDER  # noqa: E402

_PKG = "step01_script_gen.orchestrator"
_ORCH = _ROOT / "step01_script_gen" / "orchestrator"
_MAIN = _ROOT / "step01_script_gen" / "main.py"
_MAIN_MAX_LINES = 1500
_PRE_SPLIT_UNUSED = 15        # 拆分前 main.py 的 F401 + F811（tools/ruff_baseline.json @ 75dbdc5）
_ALLOWED_IMPORTERS_PREFIX = ("step01_script_gen/orchestrator/", "tests/")
_ALLOWED_IMPORTERS = {"step01_script_gen/main.py", "tools/split_step01_main.py"}
_SCAN_DIRS = ("step01_script_gen", "shared", "step02a_frame_gen", "step02b_video_gen",
              "step02_video_gen", "step03_tts_audio", "step04_merge", "_text_gen",
              "_image_gen", "_video_gen", "tts", "tools")


def _present_modules() -> list[str]:
    return [m for m in MODULE_ORDER if (_ORCH / f"{m}.py").exists()]


# ----------------------------------------------------------------------------- 判据
def import_violations(module: str, source: str) -> list[str]:
    """守卫 1 + 2：编排模块内的导入形式与依赖方向."""
    out = []
    pos = MODULE_ORDER.index(module) if module in MODULE_ORDER else len(MODULE_ORDER)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            target = ("." * node.level) + (node.module or "")
            if node.level:
                out.append(f"{module}: 相对导入 `{target}`（应写 from {_PKG} import x as _mx）")
                continue
            if target.startswith(_PKG + "."):
                out.append(f"{module}: from-import 迁出符号 `{target}`")
                continue
            if target == _PKG:
                for al in node.names:
                    if al.name not in MODULE_ORDER and al.name != "_facade":
                        out.append(f"{module}: 从包导入非模块名 `{al.name}`")
                    elif al.name in MODULE_ORDER and MODULE_ORDER.index(al.name) >= pos:
                        out.append(f"{module} → {al.name}：依赖未排在前面的模块")
        elif isinstance(node, ast.Import):
            for al in node.names:
                if al.name.startswith(_PKG + "."):
                    out.append(f"{module}: import `{al.name}`（应写 from {_PKG} import x as _mx）")
    return out


def importer_violations(files: dict[str, str]) -> list[str]:
    """守卫 3：谁在导入 orchestrator（files: 相对路径 → 源码）."""
    out = []
    for rel, source in files.items():
        if rel in _ALLOWED_IMPORTERS or rel.startswith(_ALLOWED_IMPORTERS_PREFIX):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n == _PKG or n.startswith(_PKG + ".") for n in names):
                out.append(f"{rel}:{node.lineno} 导入了 {_PKG}")
    return out


# ----------------------------------------------------------------------------- 真实代码
def test_orchestrator_modules_only_import_earlier_modules_by_alias():
    problems = []
    for m in _present_modules():
        problems += import_violations(m, (_ORCH / f"{m}.py").read_text(encoding="utf-8-sig"))
    assert not problems, "\n".join(problems)


def test_only_main_and_tests_import_the_orchestrator():
    files = {}
    for top in _SCAN_DIRS:
        for p in (_ROOT / top).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            files[p.relative_to(_ROOT).as_posix()] = p.read_text(encoding="utf-8-sig",
                                                                 errors="replace")
    problems = importer_violations(files)
    assert not problems, "\n".join(problems)


def test_main_py_is_slim_once_every_module_is_present():
    if len(_present_modules()) != len(MODULE_ORDER):
        pytest.skip("拆分未完成：%s 尚未就位" % sorted(set(MODULE_ORDER) - set(_present_modules())))
    n = len(_MAIN.read_text(encoding="utf-8-sig").splitlines())
    assert n <= _MAIN_MAX_LINES, f"main.py {n} 行 > {_MAIN_MAX_LINES}"


def test_module_order_matches_split_tool():
    sys.path.insert(0, str(_ROOT / "tools"))
    try:
        import split_step01_main as tool
    finally:
        sys.path.pop(0)
    assert tuple(tool.ORDER) == MODULE_ORDER
    assert set(tool.ALIASES) == set(MODULE_ORDER)


def test_unused_import_count_not_above_pre_split_main():
    exe = shutil.which("ruff")
    cmd = [exe] if exe else [sys.executable, "-m", "ruff"]
    try:
        proc = subprocess.run([*cmd, "check", "--no-cache", "--select", "F401,F811",
                               "--output-format", "json", "step01_script_gen/main.py",
                               "step01_script_gen/orchestrator"],
                              cwd=_ROOT, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        pytest.skip("未安装 ruff（pip install -r requirements-dev.txt）")
    if proc.returncode not in (0, 1) or not proc.stdout.strip().startswith("["):
        pytest.skip("ruff 不可用：%s" % proc.stderr[-300:])
    n = len(json.loads(proc.stdout))
    assert n <= _PRE_SPLIT_UNUSED, f"orchestrator+main 的 F401/F811 {n} 条 > 拆分前 {_PRE_SPLIT_UNUSED}"


# ----------------------------------------------------------------------------- 注入对照
def test_guard_rejects_from_import_of_moved_symbol():
    bad = f"from {_PKG}.runtime import CHAPTER_DIR\n"
    assert import_violations("scoring", bad)


def test_guard_rejects_dependency_on_later_module():
    bad = f"from {_PKG} import commit as _mcm\n"
    assert import_violations("scoring", bad)
    ok = f"from {_PKG} import runtime as _mrt\n"
    assert not import_violations("scoring", ok)


def test_guard_rejects_outside_importer():
    assert importer_violations({"shared/x.py": f"import {_PKG}.runtime\n"})
    assert importer_violations({"tools/y.py": f"from {_PKG} import runtime\n"})
    assert not importer_violations({"step01_script_gen/main.py": f"from {_PKG} import runtime\n"})
