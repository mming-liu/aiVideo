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
    """提示装配器（prompts.py）同样不得写死剧情（自检提示 self_review.py 已于 2026-11-21 删除）."""
    violations = []
    for rel in ("step01_script_gen/prompts.py",):
        path = ROOT / rel
        if path.is_file():
            violations.extend(_python_violations(path, STORY_TERMS))
    assert not violations, "\n".join(violations)


#: 生产与工具代码的根（2026-11-23 起全部纳入，含剧情短语）。测试语料由
#: `tests/test_content_binding_ratchet.py` 管；`baselines/` 是冻结快照，不参与。
_CODE_ROOTS = ("step01_script_gen", "shared", "_text_gen", "_image_gen", "_video_gen", "tts",
               "step02a_frame_gen", "step02b_video_gen", "step02_video_gen", "step03_tts_audio",
               "step04_merge", "vimax", "tools")


#: 在代码里作为**通用道具名**保留的词（只出现在道具词表/手持物语法修正这类通用表里；
#: 按具体镜头写死的改写与落点已删除）。提示面门禁仍禁它。
_CODE_GENERIC_NOUNS = ("钓杆",)


def _tracked_code_files():
    """受版本管理的代码文件；不在 git 仓库里（如解包的源码包）时退回目录遍历."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", *_CODE_ROOTS],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        files = [ROOT / x for x in out.splitlines() if x.endswith(".py")]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for r in _CODE_ROOTS if (ROOT / r).is_dir()
            for p in (ROOT / r).rglob("*.py") if "__pycache__" not in p.parts]


def test_no_work_terms_or_story_phrases_in_any_code_string():
    """作品名、人名与第一章剧情短语都不得出现在任何生产/工具代码的字符串常量里.

    docstring 与注释里的历史复盘不算（不进入运行时）；原文路径、书名、部名一律从配置取。
    """
    terms = TERMS + tuple(t for t in STORY_TERMS if t not in _CODE_GENERIC_NOUNS)
    violations = []
    for path in _tracked_code_files():
        if path.is_file():
            violations.extend(_python_violations(path, terms))
    assert not violations, "\n".join(violations)
