# -*- coding: utf-8 -*-
"""把 `step01_script_gen/main.py` 按职责原样搬到 `step01_script_gen/orchestrator/`（S01/S02）.

子命令::

    python tools/split_step01_main.py plan    [--modules a,b]   # 盘点：归属、引用、依赖图、冲突
    python tools/split_step01_main.py apply   --modules a,b      # 搬迁指定模块（先写临时目录，校验通过才替换）
    python tools/split_step01_main.py verify  [--base REV]       # AST 等价：搬走的每条顶层语句与原文一致

规则（计划 D1/D4）：
* 只改写**全局引用**：被搬到别的模块的名字写成 ``_<alias>.name``；同模块内引用不动；
  局部变量 / 参数 / 闭包变量一律不动（自带作用域分析，不按名字猜）。
* 原 main.py 顶部 13-21 行的标准库导入在各模块按需重写；其余所有 import（项目内模块）
  留在 ``runtime``，别的模块经 ``_rt.X`` 访问——这样测试对 ``main.X`` 的打桩经门面
  转发到 runtime 后，所有使用方都能看到（计划 D2 的落点，原 ``_deps`` 并入 runtime，
  以保留导入与 RunContext/日志初始化的原始先后顺序）。
* ``global X`` 改写的变量必须与改写它的函数同模块，否则报错退出。
* ``__file__`` 在搬走的代码里一律改为 ``runtime._MAIN_FILE``（原 main.py 路径）。
"""
from __future__ import annotations

import argparse
import ast
import builtins
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "step01_script_gen" / "main.py"
PKG = ROOT / "step01_script_gen" / "orchestrator"
PKG_IMPORT = "step01_script_gen.orchestrator"

#: 模块 → 访问别名（main.py 里任何标识符都不得与之重名，plan 会检查）
ALIASES = {
    "runtime": "_mrt", "scoring": "_msc", "regen": "_mrg", "final_audit": "_mfa",
    "repair_apply": "_mra", "repair_accept": "_mrc", "finalize": "_mfz",
    "repair_targets": "_mtg",
    "repair_round": "_mrr", "persist": "_mps", "generation": "_mgn", "commit": "_mcm",
    "blocked_ledger": "_mbl", "best_guard": "_mbg",
}
#: 搬迁顺序（S05-S08 分组）；依赖必须指向更靠前的模块
ORDER = ["runtime", "scoring", "regen", "repair_apply", "final_audit", "repair_accept",
         "finalize", "repair_targets", "repair_round", "persist", "blocked_ledger", "generation",
         "commit", "best_guard"]

DOCS = {
    "runtime": "导入期副作用、路径与日志、控制台重定向、锁、超时、指标、计时、预算；项目内导入的汇集点",
    "scoring": "告警收集、候选质量摘要与打分、质量门",
    "regen": "退化判定、超限与结构性重生成、拆镜",
    "final_audit": "终审、审计复用、诊断台账、冻结预算续验",
    "repair_apply": "补丁与替换镜头的应用、字段写入审计",
    "repair_accept": "问题身份、已解决判定、修复采纳判断",
    "finalize": "确定性定稿与提交投影（finalize / 折叠重复 / project_for_commit / 钩子开关）",
    "repair_targets": "修复目标定位、联动组、回滚、通道与字节预算",
    "repair_round": "定向修复轮 `_repair_shot_warnings`",
    "persist": "资产提取、运行快照、修复轮落盘",
    "generation": "spy/hook 脚本生成、候选生成",
    "commit": "提交、采纳凭据、finalize、钩子质量门",
    "blocked_ledger": "阻塞修订台账",
    "best_guard": "跨运行 BEST-GUARD 选稿",
}

#: 顶层语句按出现顺序归属：遇到这些名字时切换到对应模块
CLUSTER_STARTS = {
    "_ConsoleToLog": "runtime",
    "_warn_silent_video_model": "generation",
    "_speech_body": "regen",
    "_collect_repair_warnings": "scoring",
    "_step01_run_id": "final_audit",
    "FinalizeError": "commit",
    "_strip_runtime_fields": "scoring",
    "_build_novel_slice_for_picked": "repair_apply",
    "collapse_repetition_fields": "commit",
    "_blocking_record_key": "repair_accept",
    "_shot_by_target_id": "repair_targets",
    "_repair_shot_warnings": "repair_round",
    "_extract_assets_and_update_script": "persist",
    "_pid_is_alive": "runtime",
    "_CandidateDialogueBroken": "generation",
    "_BLOCKED_LEDGER_NAME": "blocked_ledger",
    "_cross_run_best_guard": "best_guard",
    "_fatal_summary": "main",
}
#: 单个名字的归属覆盖（D5：消除回边）
OVERRIDES = {
    "_REAL_STDOUT": "runtime",
    "_step01_run_id": "runtime", "_step01_now": "runtime", "_read_json_quiet": "runtime",
    "STEP01_AUDIT_SCOPE": "runtime",
    "_freeze_budget_exhausted": "final_audit", "_verify_budget_resume": "final_audit",
    "_budget_freeze_root": "final_audit",
    "_blocking_identity": "scoring", "_INTEGRITY_NUM_RE": "scoring",
    # 修复目标层要用的定稿/投影能力下沉到 finalize（原在提交簇里，会形成回边）
    "project_for_commit": "finalize", "FinalizeError": "finalize",
    "_is_hook_enabled": "finalize", "_hook_max_sends": "finalize",
    "_sync_missing_onscreen_layout_entries": "finalize",
    "collapse_repetition_fields": "finalize", "finalize_step01_script": "finalize",
    # 原 172-232 行的导入期选择（prompt builder / 系统提示 / 剖面日志）属于头部初始化
    "_TEXT_PROVIDER": "runtime", "build_prompt": "runtime", "build_hook_prompt": "runtime",
    "_SYSTEM_PROMPT": "runtime", "_CAND_MODELS": "runtime", "_profiles": "runtime",
}
#: main.py 与 runtime 各自定义一份（路径相对各自文件计算）
DUAL_NAMES = {"_PROJECT_ROOT"}
#: 留在 main.py 的定义
MAIN_KEEP = {"_fatal_summary", "_presentation_mode_argv", "main"}
#: 原 main.py 顶部（bootstrap 之前）的标准库导入：各模块按需重写
STDLIB_HEAD_END_LINE = 22
#: main.py 自留的头部语句（以首行文本前缀识别）
MAIN_HEAD_PREFIXES = ("_PROJECT_ROOT = ", "sys.path.insert(0, str(_PROJECT_ROOT))",
                      'if __name__ == "__main__":')
MAIN_FILE_NAME = "_MAIN_FILE"
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                 "__loader__", "__package__", "__builtins__",
                                 "__annotations__", "__debug__"}


class SplitError(RuntimeError):
    pass


# =========================================================================== 作用域分析
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


class Scope:
    def __init__(self, node, parent):
        self.node, self.parent = node, parent
        self.kind = ("module" if parent is None else
                     "class" if isinstance(node, ast.ClassDef) else
                     "comp" if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                                 ast.GeneratorExp)) else "function")
        self.locals: set = set()
        self.globals: set = set()
        self.nonlocals: set = set()


def _target_names(node, out: set) -> None:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            out.add(sub.id)
        elif isinstance(sub, ast.Starred):
            pass


def _collect_bindings(scope: Scope) -> None:
    """收集 scope 自身绑定的名字（不进入嵌套作用域；嵌套 def/class 的名字算本层）."""
    node = scope.node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = node.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
            if arg is not None:
                scope.locals.add(arg.arg)
        body = node.body if isinstance(node.body, list) else [node.body]
    elif isinstance(node, ast.ClassDef):
        body = node.body
    elif scope.kind == "comp":
        for gen in node.generators:
            _target_names(gen.target, scope.locals)
        body = []
    else:
        body = node.body

    stack = list(body)
    if scope.kind == "comp":
        stack = []
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.locals.add(n.name)
            stack.extend(n.decorator_list)
            if not isinstance(n, ast.ClassDef):
                stack.extend(d for d in n.args.defaults if d is not None)
                stack.extend(d for d in n.args.kw_defaults if d is not None)
            else:
                stack.extend(n.bases)
                stack.extend(k.value for k in n.keywords)
            continue
        if isinstance(n, ast.Lambda):
            stack.extend(n.args.defaults)
            stack.extend(d for d in n.args.kw_defaults if d is not None)
            continue
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            stack.append(n.generators[0].iter)
            # 推导式里的海象运算符绑定到外层函数
            for sub in ast.walk(n):
                if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                    scope.locals.add(sub.target.id)
            continue
        if isinstance(n, ast.Global):
            scope.globals.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            scope.nonlocals.update(n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            scope.locals.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                if al.name != "*":
                    scope.locals.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            scope.locals.add(n.name)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and getattr(n, "name", None):
            scope.locals.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            scope.locals.add(n.rest)
        stack.extend(ast.iter_child_nodes(n))
    scope.locals -= scope.globals


def classify_names(tree: ast.Module):
    """遍历全树：返回 [(Name 节点, 是否全局引用, 所在 scope)]."""
    out = []
    module_scope = Scope(tree, None)

    def walk(node, scope):
        if isinstance(node, _SCOPE_NODES):
            enter(node, scope)
            return
        if isinstance(node, ast.Name):
            out.append((node, resolve(node.id, scope), scope))
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    def enter(node, scope):
        # 装饰器 / 默认值 / 注解 / 基类 / 首个迭代器在外层作用域求值
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            outer = [*getattr(node, "decorator_list", []), *node.args.defaults,
                     *[x for x in node.args.kw_defaults if x is not None]]
            if not isinstance(node, ast.Lambda):
                outer += _annotations(node)
        elif isinstance(node, ast.ClassDef):
            outer = [*node.decorator_list, *node.bases, *[k.value for k in node.keywords]]
        else:
            outer = [node.generators[0].iter]
        for expr in outer:
            walk(expr, scope)
        inner = Scope(node, scope)
        _collect_bindings(inner)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for stmt in node.body:
                walk(stmt, inner)
        elif isinstance(node, ast.Lambda):
            walk(node.body, inner)
        else:  # comprehension
            for i, gen in enumerate(node.generators):
                walk(gen.target, inner)
                if i > 0:
                    walk(gen.iter, inner)
                for cond in gen.ifs:
                    walk(cond, inner)
            for part in ([node.key, node.value] if isinstance(node, ast.DictComp)
                         else [node.elt]):
                walk(part, inner)

    def resolve(name, scope) -> bool:
        s = scope
        first = True
        while s is not None:
            if s.kind == "module":
                return True
            if name in s.globals:
                return True
            if (first or s.kind != "class") and (name in s.locals or name in s.nonlocals):
                return False
            first = False
            s = s.parent
        return True

    for stmt in tree.body:
        walk(stmt, module_scope)
    return out


def _annotations(fn):
    a = fn.args
    anns = [x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs,
                                   a.vararg, a.kwarg] if x is not None and x.annotation]
    if fn.returns is not None:
        anns.append(fn.returns)
    return anns


# =========================================================================== 顶层语句归属
def _module_level_bindings(stmt) -> set:
    """顶层语句在**模块命名空间**里绑定的名字（含 if/try/with/for 体内，不进 def/class 体）."""
    out: set = set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    stack = [stmt]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue
        if isinstance(n, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                          ast.GeneratorExp)):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        stack.extend(ast.iter_child_nodes(n))
    return out


class Stmt:
    def __init__(self, node, start, end, owner, names):
        self.node, self.start, self.end, self.owner, self.names = node, start, end, owner, names

    @property
    def is_import(self):
        return isinstance(self.node, (ast.Import, ast.ImportFrom))


def load_source(path: Path = SRC):
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    return text, bom, crlf


def assign_statements(tree: ast.Module, lines: list[str]) -> list[Stmt]:
    """每条顶层语句（连同其前面的注释/空行）→ 归属模块."""
    stmts: list[Stmt] = []
    current = None
    prev_end = 0
    head_done = False
    stdlib_phase = True        # 文件开头连续的导入（docstring 之后、首条非导入语句之前）
    for node in tree.body:
        start = node.lineno
        if getattr(node, "decorator_list", None):
            start = min(d.lineno for d in node.decorator_list)
        names = _module_level_bindings(node)
        first_line = lines[node.lineno - 1].strip()
        name = getattr(node, "name", None)
        if name in CLUSTER_STARTS:
            current = CLUSTER_STARTS[name]
            head_done = True
        for nm in names:
            if nm in CLUSTER_STARTS:
                current = CLUSTER_STARTS[nm]
                head_done = True
        is_import = isinstance(node, (ast.Import, ast.ImportFrom))
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        is_docstring = (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                        and node is tree.body[0])
        if not (is_import or is_docstring):
            stdlib_phase = False
        if not head_done:
            if stdlib_phase and is_import and not is_future:
                owner = "stdlib_head"
            elif isinstance(node, ast.Expr) and node.lineno == 1:
                owner = "main"
            elif first_line.startswith(MAIN_HEAD_PREFIXES):
                owner = "main"
            elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
                owner = "future"
            else:
                owner = "runtime"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            owner = "runtime"
        elif isinstance(node, ast.If) and first_line.startswith('if __name__ == "__main__":'):
            owner = "main"
        else:
            owner = current
            if names & set(OVERRIDES):
                owners = {OVERRIDES[n] for n in names if n in OVERRIDES}
                if len(owners) != 1:
                    raise SplitError(f"语句 {node.lineno} 绑定多个覆盖名，归属不唯一: {names}")
                owner = owners.pop()
            if names & MAIN_KEEP:
                owner = "main"
        stmts.append(Stmt(node, prev_end + 1, node.end_lineno, owner, names))
        prev_end = node.end_lineno
    return stmts


def name_owners(stmts: list[Stmt]) -> dict:
    """模块级名字 → 归属模块。stdlib 头部导入记为 ``stdlib``."""
    owners: dict = {}
    for s in stmts:
        if s.owner == "future":
            continue
        for nm in s.names:
            own = "stdlib" if s.owner == "stdlib_head" else s.owner
            prev = owners.get(nm)
            if prev and prev != own:
                # 导入后又在别处重新赋值（如 build_prompt）：以赋值方为准
                if prev == "stdlib" or (s.is_import is False):
                    owners[nm] = own
                continue
            owners[nm] = own
    return owners


# =========================================================================== 改写
GEN_BEGIN = "# <split:generated>"
GEN_END = "# </split:generated>"
VERBATIM_MARK = "# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----"


class Layout:
    """一次搬迁的完整计算结果（不落盘）."""

    def __init__(self, moved: list[str], src_path: Path = SRC):
        unknown = set(moved) - set(ORDER)
        if unknown:
            raise SplitError(f"未知模块: {sorted(unknown)}")
        self.moved = [m for m in ORDER if m in moved]
        self.text, self.bom, self.crlf = load_source(src_path)
        self.lines = self.text.split("\n")
        self.tree = ast.parse(self.text)
        self.stmts = assign_statements(self.tree, self.lines)
        self.owners_raw = name_owners(self.stmts)
        self.errors: list[str] = []
        self.deps_call = defaultdict(set)    # 模块 → 依赖模块（函数体内引用）
        self.deps_import = defaultdict(set)  # 模块 → 依赖模块（导入期求值）
        self.stdlib_used = defaultdict(set)
        self.replacements = defaultdict(list)  # 行号 → [(col0, col1, text)]
        self._check_aliases()
        self._rewrite()

    def eff(self, owner: str) -> str:
        """未搬迁模块的语句仍留在 main.py."""
        if owner in ("stdlib", "stdlib_head", "future"):
            return owner
        return owner if owner in self.moved else "main"

    def stmt_module(self, s: Stmt) -> str:
        return self.eff(s.owner)

    def owner_of(self, name: str, from_module: str):
        if name in DUAL_NAMES:
            return from_module if from_module in ("main", "runtime") else self.eff("runtime")
        own = self.owners_raw.get(name)
        if own is None:
            return None
        return self.eff(own)

    def _check_aliases(self):
        idents = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        idents |= {n.arg for n in ast.walk(self.tree) if isinstance(n, ast.arg)}
        idents |= {n.attr for n in ast.walk(self.tree) if isinstance(n, ast.Attribute)}
        for alias in (*ALIASES.values(), MAIN_FILE_NAME, "_facade"):
            if alias in idents:
                self.errors.append(f"别名 {alias} 与 main.py 现有标识符冲突")

    def _rewrite(self):
        stmt_of_line = {}
        for s in self.stmts:
            for ln in range(s.node.lineno, s.node.end_lineno + 1):
                stmt_of_line[ln] = s
        for node, is_global, scope in classify_names(self.tree):
            if not is_global:
                continue
            s = stmt_of_line.get(node.lineno)
            if s is None:
                continue
            mod = self.stmt_module(s)
            if mod in ("stdlib_head", "future"):
                continue
            name = node.id
            in_function = False
            sc = scope
            while sc is not None:
                if sc.kind == "function":
                    in_function = True
                    break
                sc = sc.parent
            if name == "__file__":
                if mod == "main":
                    continue
                new = MAIN_FILE_NAME if mod == "runtime" else f"{ALIASES['runtime']}.{MAIN_FILE_NAME}"
                if mod != "runtime":
                    (self.deps_call if in_function else self.deps_import)[mod].add("runtime")
                self._replace(node, new)
                continue
            own = self.owner_of(name, mod)
            if own is None:
                if name not in BUILTINS:
                    self.errors.append(f"{mod}: 第 {node.lineno} 行未解析的名字 {name}")
                continue
            if own == "stdlib":
                self.stdlib_used[mod].add(name)
                continue
            if own == mod:
                continue
            if own == "main":
                self.errors.append(f"{mod}: 第 {node.lineno} 行引用仍留在 main.py 的 {name}")
                continue
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.errors.append(f"{mod}: 第 {node.lineno} 行跨模块写全局 {name}（属 {own}）")
                continue
            (self.deps_call if in_function else self.deps_import)[mod].add(own)
            self._replace(node, f"{ALIASES[own]}.{name}")

    def _replace(self, node, new_text):
        if node.lineno != node.end_lineno:
            raise SplitError(f"名字跨行: {node.id}@{node.lineno}")
        self.replacements[node.lineno].append((node.col_offset, node.end_col_offset, new_text))

    def rewritten_line(self, lineno: int) -> str:
        line = self.lines[lineno - 1]
        reps = self.replacements.get(lineno)
        if not reps:
            return line
        raw = line.encode("utf-8")
        for c0, c1, new in sorted(reps, reverse=True):
            raw = raw[:c0] + new.encode("utf-8") + raw[c1:]
        return raw.decode("utf-8")

    def chunk(self, s: Stmt) -> str:
        return "\n".join(self.rewritten_line(i) for i in range(s.start, s.end + 1))


    # ------------------------------------------------------------------ 生成
    def _stdlib_import_lines(self, mod: str) -> list[str]:
        used = self.stdlib_used.get(mod, set())
        if mod == "runtime":
            used = used | {"Path"}          # _MAIN_FILE / _PROJECT_ROOT 需要
        out = []
        for s in self.stmts:
            if s.owner != "stdlib_head":
                continue
            node = s.node
            keep = [a for a in node.names if (a.asname or a.name).split(".")[0] in used]
            if not keep:
                continue
            if isinstance(node, ast.Import):
                out.append(ast.unparse(ast.Import(names=keep)))
            else:
                out.append(ast.unparse(ast.ImportFrom(module=node.module, names=keep,
                                                      level=node.level)))
        return out

    def _alias_import_lines(self, mod: str) -> list[str]:
        deps = (self.deps_call[mod] | self.deps_import[mod]) - {mod}
        return [f"from {PKG_IMPORT} import {m} as {ALIASES[m]}"
                for m in ORDER if m in deps]

    def module_text(self, mod: str) -> str:
        body = [s for s in self.stmts if self.stmt_module(s) == mod]
        head = ["# -*- coding: utf-8 -*-",
                GEN_BEGIN,
                f'"""step01_script_gen.orchestrator.{mod} —— {DOCS[mod]}.',
                "",
                "顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。",
                "跨模块引用一律经模块属性访问（如 `_rt.X`），测试对",
                "`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。",
                '"""',
                "from __future__ import annotations",
                ""]
        head += self._stdlib_import_lines(mod)
        aliases = self._alias_import_lines(mod)
        if aliases:
            head += [""] + aliases
        if mod == "runtime":
            head += ["",
                     "#: 原 `main.py` 的路径：迁出代码里的 `__file__` 一律改用它（行为不变）",
                     f'{MAIN_FILE_NAME} = str(Path(__file__).resolve().parent.parent / "main.py")',
                     "_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent"]
        head += [GEN_END, "", VERBATIM_MARK]
        chunks = [self.chunk(s) for s in body]
        text = "\n".join(head) + "\n" + "\n".join(chunks).lstrip("\n") + "\n"
        if mod == "runtime":
            reexport = sorted(self._runtime_reexports())
            if reexport:
                text += ("\n" + GEN_BEGIN + "\n"
                         "# 项目内导入的名字由其他编排模块经 `_rt.X` 使用（门面打桩的落点）。\n"
                         "__all__ = [\n" + "".join(f'    "{n}",\n' for n in reexport) + "]\n"
                         + GEN_END + "\n")
        return text

    def _runtime_reexports(self) -> set:
        imported = set()
        for s in self.stmts:
            if self.stmt_module(s) == "runtime" and s.is_import:
                imported |= s.names
        used_elsewhere = set()
        for mod in [*self.moved, "main"]:
            if mod == "runtime":
                continue
            for ln, reps in self.replacements.items():
                for _c0, _c1, new in reps:
                    if new.startswith(ALIASES["runtime"] + "."):
                        used_elsewhere.add(new.split(".", 1)[1])
        return imported & used_elsewhere

    def main_text(self) -> str:
        body = [s for s in self.stmts if self.stmt_module(s) == "main"]
        doc = [s for s in body if isinstance(s.node, ast.Expr) and s.node.lineno == 1]
        head_main = [s for s in body if s.node.lineno < 120 and s not in doc]
        rest = [s for s in body if s not in doc and s not in head_main]
        out = [self.chunk(s) for s in doc]
        out += [GEN_BEGIN, "from __future__ import annotations"]
        out += self._stdlib_import_lines("main") or []
        if "sys" not in self.stdlib_used.get("main", set()):
            out.append("import sys")
        if "Path" not in self.stdlib_used.get("main", set()):
            out.append("from pathlib import Path")
        out.append(GEN_END)
        out += [self.chunk(s) for s in head_main]
        out += ["", GEN_BEGIN,
                "# 编排实现已按职责拆到 step01_script_gen/orchestrator/；本文件保留入口 main()。",
                "# 门面：对本模块的读写（含 mock.patch / monkeypatch）转发到实现所在模块。",
                f"from {PKG_IMPORT} import _facade"]
        out += [f"from {PKG_IMPORT} import {m} as {ALIASES[m]}" for m in self.moved]
        out += [f"_facade.install(sys.modules[__name__], [{', '.join(ALIASES[m] for m in self.moved)}])",
                GEN_END, ""]
        out += [self.chunk(s) for s in rest]
        return "\n".join(out) + "\n"

    def dependency_cycles(self) -> list[list[str]]:
        graph = {m: (self.deps_call[m] | self.deps_import[m]) & set(self.moved)
                 for m in self.moved}
        cycles, color, stack = [], {}, []

        def dfs(u):
            color[u] = 1
            stack.append(u)
            for v in sorted(graph[u]):
                if color.get(v) == 1:
                    cycles.append(stack[stack.index(v):] + [v])
                elif not color.get(v):
                    dfs(v)
            stack.pop()
            color[u] = 2

        for m in self.moved:
            if not color.get(m):
                dfs(m)
        return cycles

    def order_violations(self) -> list[str]:
        out = []
        for m in self.moved:
            for d in self.deps_call[m] | self.deps_import[m]:
                if d in ORDER and ORDER.index(d) > ORDER.index(m):
                    out.append(f"{m} → {d}（依赖排在后面的模块）")
        return out


# =========================================================================== 校验（AST 等价）
class _Normalize(ast.NodeTransformer):
    """`_rt.X` → `X`；`_MAIN_FILE` / `_rt._MAIN_FILE` → `__file__`."""
    _aliases = set(ALIASES.values())

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id in self._aliases:
            name = "__file__" if node.attr == MAIN_FILE_NAME else node.attr
            return ast.copy_location(ast.Name(id=name, ctx=node.ctx), node)
        return node

    def visit_Name(self, node):
        if node.id == MAIN_FILE_NAME:
            return ast.copy_location(ast.Name(id="__file__", ctx=node.ctx), node)
        return node


def _dump(node) -> str:
    return ast.dump(_Normalize().visit(node), include_attributes=False)


def _verbatim_statements(text: str) -> list:
    """生成文件里不在 <split:generated> 块内的顶层语句."""
    lines = text.replace("\r\n", "\n").split("\n")
    generated = set()
    inside = False
    for i, ln in enumerate(lines, 1):
        if ln.strip() == GEN_BEGIN:
            inside = True
        if inside:
            generated.add(i)
        if ln.strip() == GEN_END:
            inside = False
    tree = ast.parse(text)
    return [n for n in tree.body if n.lineno not in generated]


def verify(original_text: str, files: dict[str, str], moved: list[str]) -> list[str]:
    """逐条比对原 main.py 顶层语句与新文件中的对应语句；返回差异列表（空 = 等价）."""
    tmp = Path(tempfile.mkdtemp()) / "main.py"
    tmp.write_text(original_text, encoding="utf-8")
    lay = Layout(moved, tmp)
    problems = []
    for mod, text in files.items():
        expected = [s.node for s in lay.stmts if lay.stmt_module(s) == mod]
        got = _verbatim_statements(text)
        if len(expected) != len(got):
            problems.append(f"{mod}: 语句数 原 {len(expected)} / 新 {len(got)}")
            continue
        for a, b in zip(expected, got):
            if _dump(a) != _dump(b):
                label = getattr(a, "name", None) or ast.unparse(a)[:60].replace("\n", " ")
                problems.append(f"{mod}: 第 {a.lineno} 行 `{label}` AST 不一致")
    accounted = sum(1 for s in lay.stmts if lay.stmt_module(s) in files)
    total = sum(1 for s in lay.stmts if s.owner not in ("stdlib_head", "future"))
    if accounted != total:
        problems.append(f"语句覆盖不全：{accounted}/{total}")
    return problems


# =========================================================================== CLI
def _write_outputs(lay: Layout, target_root: Path) -> dict[str, str]:
    files = {"main": lay.main_text()}
    for m in lay.moved:
        files[m] = lay.module_text(m)
    pkg = target_root / "step01_script_gen" / "orchestrator"
    pkg.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = (target_root / "step01_script_gen" / "main.py" if name == "main"
                else pkg / f"{name}.py")
        data = text.replace("\n", "\r\n") if lay.crlf else text
        path.write_bytes((b"\xef\xbb\xbf" if lay.bom and name == "main" else b"")
                         + data.encode("utf-8"))
    return files


def cmd_plan(args) -> int:
    lay = Layout(args.modules, _base_source(args.base))
    by_mod = defaultdict(lambda: [0, 0])
    for s in lay.stmts:
        m = lay.stmt_module(s)
        by_mod[m][0] += 1
        by_mod[m][1] += s.end - s.start + 1
    for m, (n, ln) in sorted(by_mod.items(), key=lambda kv: -kv[1][1]):
        print(f"{m:16} stmts={n:4} lines={ln:6}")
    for m in lay.moved:
        print(f"deps {m:16} call={sorted(lay.deps_call[m] - {m})} import={sorted(lay.deps_import[m] - {m})}")
    print("cycles:", lay.dependency_cycles())
    print("order_violations:", lay.order_violations())
    print("errors:", len(lay.errors))
    for e in lay.errors[:60]:
        print("  ", e)
    return 1 if lay.errors or lay.dependency_cycles() else 0


def _base_source(base: str) -> Path:
    """拆分前的原 main.py（固定基线 revision）写到临时文件；每次搬迁都从它重新计算."""
    raw = subprocess.run(["git", "show", f"{base}:step01_script_gen/main.py"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    path = ROOT / ".tmp" / "split" / "main_base.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def cmd_apply(args) -> int:
    base_path = _base_source(args.base)
    lay = Layout(args.modules, base_path)
    if lay.errors or lay.dependency_cycles() or lay.order_violations():
        print("拒绝搬迁：先运行 plan 解决 errors / cycles / order_violations")
        return 1
    original = base_path.read_bytes().decode("utf-8-sig")
    # 行尾跟随工作区现有 main.py（autocrlf 下工作区为 CRLF，git 基线为 LF）
    lay.crlf = b"\r\n" in SRC.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        files = _write_outputs(lay, Path(tmp))
        problems = verify(original, files, lay.moved)
        for rel in ["step01_script_gen/main.py",
                    *[f"step01_script_gen/orchestrator/{m}.py" for m in lay.moved]]:
            compile((Path(tmp) / rel).read_bytes().decode("utf-8-sig"), rel, "exec")
        if problems:
            print("校验失败，未替换任何文件：")
            for p in problems:
                print("  ", p)
            return 1
        backup = ROOT / ".tmp" / "split" / "main_before_apply.py"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, backup)
        _write_outputs(lay, ROOT)
    print(f"已搬迁 {lay.moved}；main.py {len(lay.main_text().splitlines())} 行；AST 等价校验通过")
    return 0


def cmd_verify(args) -> int:
    original = subprocess.run(["git", "show", f"{args.base}:step01_script_gen/main.py"],
                              cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8-sig")
    moved = [m for m in ORDER if (PKG / f"{m}.py").exists()]
    files = {"main": SRC.read_bytes().decode("utf-8-sig")}
    for m in moved:
        files[m] = (PKG / f"{m}.py").read_text(encoding="utf-8-sig")
    problems = verify(original, files, moved)
    for p in problems:
        print("  ", p)
    n = sum(len(_verbatim_statements(t)) for t in files.values())
    print(f"moved={moved} verbatim_statements={n} problems={len(problems)}")
    return 1 if problems else 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="拆分 step01_script_gen/main.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--modules", type=lambda s: [x for x in s.split(",") if x],
                       default=list(ORDER))
        p.add_argument("--base", default="refactor/step01-split-base")
    v = sub.add_parser("verify")
    v.add_argument("--base", default="refactor/step01-split-base")
    args = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "apply": cmd_apply, "verify": cmd_verify}[args.cmd](args)
    except SplitError as exc:
        print("SplitError:", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
