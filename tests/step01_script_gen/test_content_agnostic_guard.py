"""G05：Step01/shared 生产字符串与 Prompt 不得写死当前作品内容."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TERMS = ("杨旭", "听香", "张十三", "肖管事", "照月湾",
         "杨府别庄", "锦衣夜行", "77119")


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _python_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    skip = _docstring_constant_ids(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        for term in TERMS:
            if term in node.value:
                out.append(f"{path}:{node.lineno}: {term}")
                break
    return out


def test_no_content_specific_strings_in_step01_and_shared():
    violations = []
    for root_name in ("step01_script_gen", "shared"):
        for path in (ROOT / root_name).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            violations.extend(_python_violations(path))
    assert not violations, "\n".join(violations)


def test_no_content_specific_terms_in_prompt_templates():
    violations = []
    for path in (ROOT / "step01_script_gen" / "prompts").glob("*.md"):
        text = path.read_text(encoding="utf-8-sig")
        for term in TERMS:
            if term in text:
                violations.append(f"{path}: {term}")
    assert not violations, "\n".join(violations)
