"""aiVideo 技能加载与自进化支持库.

从 oz-for-oss 的 core/skills.py + oz/repo_local.py 适配而来。
提供:
  - load_skill(name) → {name, specializes, body}
  - has_local_skill(core_name) → bool
  - read_local_skill(core_name) → body | None
  - assert_write_surface(changed_files, allowed_prefixes, loop_name)
  - WriteSurfaceViolation 异常类
  - list_all_skills() → 打印所有技能状态
"""

import re
from pathlib import Path
from typing import Optional

# YAML frontmatter 模式
_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


# ---------------------------------------------------------------------------
# 技能加载
# ---------------------------------------------------------------------------

def load_skill(name: str) -> dict | None:
    """加载技能文件，返回 {name, specializes?, description?, path, body}.

    name 不加 "-local" 后缀（要读伴生技能时传完整名如 "generate-script-local"）.
    """
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    m = _FRONTMATTER_PATTERN.match(text)
    if m:
        for line in m.group(1).split("\n"):
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():].strip()
    else:
        body = text
    return {"name": meta.get("name", name),
            "specializes": meta.get("specializes"),
            "description": meta.get("description", ""),
            "path": str(path),
            "body": body}


def has_local_skill(core_name: str) -> bool:
    """检查是否存在伴生技能（必须有非空 body，仅 frontmatter 视为不存在）."""
    skill = load_skill(f"{core_name}-local")
    return bool(skill and skill["body"])


def read_local_skill(core_name: str) -> Optional[str]:
    """读取伴生技能的正文（用于注入 prompt 上下文）."""
    skill = load_skill(f"{core_name}-local")
    return skill["body"] if skill else None


def read_skill(name: str) -> Optional[str]:
    """读取任意 skill 的正文（不含 -local 后缀）."""
    skill = load_skill(name)
    return skill["body"] if skill else None


# Skill 引用模式：[skill:xxx]
_SKILL_REF_PATTERN = re.compile(r"\[skill:([a-z0-9_-]+)\]")


def resolve_skill_refs(text: str) -> str:
    """解析文本中的 [skill:xxx] 引用，替换为对应 skill 内容.

    支持链式引用（skill 内容中再次引用其他 skill），最多递归 3 层。
    """
    max_depth = 3
    resolved = text

    for _ in range(max_depth):
        # 查找所有 skill 引用
        refs = set(_SKILL_REF_PATTERN.findall(resolved))
        if not refs:
            break

        # 替换每个引用
        for ref_name in refs:
            skill_body = read_skill(ref_name)
            if skill_body:
                # 替换引用（保留一个换行防止粘连）
                resolved = resolved.replace(f"[skill:{ref_name}]", f"\n{skill_body}\n")

    return resolved


# ---------------------------------------------------------------------------
# 写表面安全
# ---------------------------------------------------------------------------

class WriteSurfaceViolation(Exception):
    """自进化循环尝试修改受保护的文件."""
    pass


def assert_write_surface(
    changed_files: list[str],
    allowed_prefixes: list[str],
    loop_name: str,
) -> None:
    """校验所有改动文件都在允许的前缀内.

    Args:
        changed_files: git diff 中变更的文件路径列表
        allowed_prefixes: 允许写入的前缀列表
        loop_name: 本次自进化循环的名称（用于报错）

    Raises:
        WriteSurfaceViolation: 如果有文件不在允许范围内
    """
    normalized = [f.replace("\\", "/") for f in changed_files if f.strip()]
    for f in normalized:
        if not any(f.startswith(p) for p in allowed_prefixes):
            raise WriteSurfaceViolation(
                f"[{loop_name}] 禁止写入 {f}。"
                f"允许范围: {allowed_prefixes}"
            )
    print(f"  [OK] write surface 校验通过: {len(normalized)} 个文件均在允许范围内")


# ---------------------------------------------------------------------------
# 技能列表
# ---------------------------------------------------------------------------

def list_all_skills() -> None:
    """打印所有技能状态（核心、伴生、元技能）."""
    if not SKILLS_DIR.exists():
        print("[INFO] 技能目录不存在")
        return

    core_skills = []
    local_skills = []
    meta_skills = []

    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill = load_skill(d.name)
        if not skill:
            continue
        if d.name.startswith("update-"):
            meta_skills.append(d.name)
        elif d.name.endswith("-local"):
            local_skills.append(d.name)
        else:
            core_skills.append(d.name)

    print(f"\n{'='*60}")
    print(f"  aiVideo 技能状态")
    print(f"{'='*60}")

    print(f"\n核心技能 ({len(core_skills)}):")
    for name in core_skills:
        skill = load_skill(name)
        has_local = has_local_skill(name)
        local_tag = " [+local]" if has_local else ""
        print(f"  {name}{local_tag} — {skill['description'][:50]}")

    print(f"\n伴生技能 ({len(local_skills)}):")
    for name in local_skills:
        skill = load_skill(name)
        specializes = skill.get("specializes", "?")
        body_len = len(skill["body"]) if skill["body"] else 0
        print(f"  {name} -> {specializes} ({body_len} 字)")

    print(f"\n自进化元技能 ({len(meta_skills)}):")
    for name in meta_skills:
        skill = load_skill(name)
        body = skill["body"] if skill else ""
        # 提取 write surface
        m = re.search(r"仅允许写入\s+(.+)", body)
        ws = m.group(1) if m else "未声明"
        print(f"  {name} — write surface: {ws}")

    print()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    list_all_skills()
