"""G05：Step01/shared 生产字符串与 Prompt 不得写死当前作品内容.

2026-11-06（G05-Phase2）扩展：
- 扫描范围加入 `.aiVideo/skills/**/*.md`——这些技能会被 `prompts.py` 拼进 Step01 提示，
  之前完全不在门禁内（story-bible 曾把整本《锦衣夜行》设定注入每一次生成）；
- 除 8 个作品名外，再禁**第一章专属剧情短语**（水下黑影/半身探水/刀尖微露/钓杆/凶手是谁…），
  防止"换掉人名、剧情仍是同一场刺杀"的形态绕过。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TERMS = ("杨旭", "听香", "张十三", "肖管事", "照月湾",
         "杨府别庄", "锦衣夜行", "77119")

#: 第一章专属**剧情短语**（不是人名/地名）：换掉人名后仍会把同一场"荷塘破水刺杀"
#: 带进任何一本书的提示里。只对**提示面**（prompt 模板 / 技能 md / 提示装配器）生效，
#: 不扫全仓 Python——代码注释里的历史复盘不算提示内容。
STORY_TERMS = ("夏浔", "纪纲", "冯西辉", "水下黑影", "半身探水", "倒纵入水",
               "刀尖微露", "刀尖朝下", "钓杆", "平定四方巾", "凶手是谁",
               "酒壶倾倒在船板")

#: 技能目录（会被 prompts.py 注入）。
SKILLS = ROOT / ".aiVideo" / "skills"


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


def _python_violations(path: Path, terms=TERMS) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    skip = _docstring_constant_ids(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        for term in terms:
            if term in node.value:
                out.append(f"{path}:{node.lineno}: {term}")
                break
    return out


def _markdown_violations(path: Path, terms) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    return [f"{path}: {term}" for term in terms if term in text]


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
        violations.extend(_markdown_violations(path, TERMS + STORY_TERMS))
    assert not violations, "\n".join(violations)


def test_no_content_specific_terms_in_skill_files():
    """`.aiVideo/skills` 会被注入提示，必须与 Prompt 模板同一门禁."""
    if not SKILLS.is_dir():
        return
    violations = []
    for path in SKILLS.rglob("*.md"):
        violations.extend(_markdown_violations(path, TERMS + STORY_TERMS))
    assert not violations, "\n".join(violations)


def test_no_story_terms_in_prompt_assembler():
    """提示装配器（prompts.py）与自检提示（self_review.py）同样不得写死剧情."""
    violations = []
    for rel in ("step01_script_gen/prompts.py",
                "step01_script_gen/constraints/self_review.py"):
        path = ROOT / rel
        if path.is_file():
            violations.extend(_python_violations(path, STORY_TERMS))
    assert not violations, "\n".join(violations)
