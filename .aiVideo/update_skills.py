"""aiVideo 技能自进化入口.

读取 script_result.json 的 git diff，识别人类编辑模式，
自动更新伴生技能文件（受 write surface 安全保护）.

用法:
  python .aiVideo/update_skills.py            # 查看变更摘要（干跑）
  python .aiVideo/update_skills.py --apply   # 应用变更到伴生文件
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from skills_loader import (
    SKILLS_DIR,
    load_skill,
    has_local_skill,
    assert_write_surface,
    WriteSurfaceViolation,
)

SCRIPT_FILE = _PROJECT_ROOT / "script_result.json"
LOCAL_SKILL = SKILLS_DIR / "generate-script-local" / "SKILL.md"

# 学习目标字段（有视觉偏好的描述性字段）
_LEARNING_FIELDS = {
    "shots[].ff_desc",
    "shots[].lf_desc",
    "shots[].motion_desc",
    "shots[].context_bridge",
    "character_profile",
    "shots[].cam_idx",
    "shots[].variation_type",
}


# ---------------------------------------------------------------------------
# 信号采集
# ---------------------------------------------------------------------------

def _get_git_diff(file_path: Path) -> str:
    """获取指定文件的 git diff（unstaged 变更）."""
    try:
        result = subprocess.run(
            ["git", "diff", str(file_path)],
            cwd=_PROJECT_ROOT,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        return result.stdout
    except Exception as e:
        print(f"[WARN] git diff 失败: {e}")
        return ""


def _parse_diff(diff_text: str) -> list[dict]:
    """解析 git diff，提取被修改的 JSON 字段.

    返回 [{shot_index, field, old_value, new_value}, ...]
    """
    changes: list[dict] = []
    # 匹配 diff 中的行: + 或 - 开头，后面跟 JSON 内容
    lines = diff_text.split("\n")
    added_lines: list[str] = []
    removed_lines: list[str] = []

    for line in lines:
        stripped = line[1:] if len(line) > 1 and line[0] in "+-" else ""
        if not stripped.strip().startswith('"'):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(stripped)
        elif line.startswith("-") and not line.startswith("---"):
            removed_lines.append(stripped)

    # 尝试匹配 JSON 键值对
    _kv_pattern = re.compile(r'"(ff_desc|lf_desc|motion_desc|context_bridge|character_profile|cam_idx|variation_type)"\s*:\s*(.+)')

    for old_l, new_l in zip(removed_lines, added_lines):
        m_old = _kv_pattern.search(old_l)
        m_new = _kv_pattern.search(new_l)
        if m_old and m_new and m_old.group(1) == m_new.group(1):
            field = m_old.group(1)
            old_val = m_old.group(2).strip().strip('",')
            new_val = m_new.group(2).strip().strip('",')
            if old_val != new_val:
                changes.append({
                    "field": field,
                    "old": old_val[:80],
                    "new": new_val[:80],
                })

    return changes


# ---------------------------------------------------------------------------
# 模式识别
# ---------------------------------------------------------------------------

def _analyze_patterns(changes: list[dict]) -> list[dict]:
    """从变更列表中识别重复模式.

    返回 [{pattern_type, evidence_count, examples, suggestion}, ...]
    """
    patterns: dict[str, dict] = {}

    for c in changes:
        field = c["field"]
        new = c["new"]
        old = c["old"]

        # ---- 模式 1: ff_desc/lf_desc 长度变化 ----
        if field in ("ff_desc", "lf_desc"):
            key = f"{field}_length"
            if len(new) > len(old) + 20:
                patterns.setdefault(key, {
                    "type": "叙事节奏", "field": field,
                    "count": 0, "examples": [],
                })
                patterns[key]["count"] += 1
                patterns[key]["examples"].append(new[:40])
                patterns[key]["suggestion"] = (
                    f"{field} 建议 {len(new)}-{len(new)+20} 字 "
                    f"（从 {len(old)} 字扩展）"
                )
            elif len(new) < len(old) - 20:
                patterns.setdefault(key, {
                    "type": "叙事节奏", "field": field,
                    "count": 0, "examples": [],
                })
                patterns[key]["count"] += 1
                patterns[key]["examples"].append(new[:40])
                patterns[key]["suggestion"] = (
                    f"{field} 建议 {len(old)-20}-{len(old)} 字 "
                    f"（从 {len(old)} 字精简）"
                )

        # ---- 模式 2: context_bridge 特定表达 ----
        if field == "context_bridge":
            phrases = _extract_temporal_phrases(new)
            for phrase in phrases:
                key = f"context_bridge_phrase_{phrase}"
                patterns.setdefault(key, {
                    "type": "衔接描述偏好", "field": field,
                    "count": 0, "examples": [],
                })
                patterns[key]["count"] += 1
                patterns[key]["examples"].append(new[:60])
                patterns[key]["suggestion"] = (
                    f"context_bridge 建议包含过渡短语模式: {phrase}"
                )

        # ---- 模式 3: cam_idx 分布变化 ----
        if field == "cam_idx":
            key = "cam_distribution"
            patterns.setdefault(key, {
                "type": "摄像机偏好", "field": field,
                "count": 0, "examples": [],
            })
            patterns[key]["count"] += 1
            patterns[key]["examples"].append(f"{old} -> {new}")
            patterns[key]["suggestion"] = (
                f"摄像机分组偏好变更（从 cam={old} 改为 cam={new}）"
            )

    # 只保留出现 ≥2 次的模式
    return [p for p in patterns.values() if p["count"] >= 2]


def _extract_temporal_phrases(text: str) -> list[str]:
    """从 context_bridge 中提取时间/空间过渡短语."""
    patterns = [
        r"光影从\S+转为\S+",
        r"镜头从\S+切[换入]",
        r"时间从\S+推进[到至]",
        r"空间从\S+移[动至]",
        r"从\S+过渡到\S+",
    ]
    phrases: list[str] = []
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            phrases.append(m.group())
    return phrases


# ---------------------------------------------------------------------------
# 应用更新
# ---------------------------------------------------------------------------

def _update_local_skill(patterns: list[dict], dry_run: bool = True) -> list[str]:
    """将识别出的模式写入伴生技能文件.

    Args:
        patterns: _analyze_patterns 的输出
        dry_run: True = 只打印不写入

    Returns:
        写入的文件路径列表
    """
    written: list[str] = []

    for p in patterns:
        ptype = p["type"]
        suggestion = p.get("suggestion", "")
        count = p.get("count", 0)
        examples = p.get("examples", [])

        print(f"\n  [{ptype}] {suggestion}")
        print(f"    证据: {count} 次, 示例: {examples[:2]}")

        if not dry_run:
            _append_to_local_skill(ptype, suggestion, examples)

        written.append(str(LOCAL_SKILL))

    return written


def _append_to_local_skill(category: str, suggestion: str, examples: list[str]) -> None:
    """向伴生技能文件追加新的启发式规则."""
    if not LOCAL_SKILL.exists():
        print(f"  [WARN] 伴生文件不存在: {LOCAL_SKILL}")
        return

    content = LOCAL_SKILL.read_text(encoding="utf-8")

    # 在对应类别下追加
    category_map = {
        "叙事节奏": "## 叙事节奏偏好",
        "衔接描述偏好": "## 叙事节奏偏好",
        "摄像机偏好": "## 镜头构图启发式",
    }
    section = category_map.get(category, "## 镜头构图启发式")

    new_line = f"\n- [auto] {suggestion}（证据: {len(examples)} 次人类编辑）"

    # 找到对应 section，在其末尾追加
    if section in content:
        # 找到下一个 ## 或文件末尾
        idx = content.index(section)
        next_section = re.search(r"\n##\s", content[idx + len(section):])
        if next_section:
            insert_at = idx + len(section) + next_section.start()
            content = content[:insert_at] + new_line + content[insert_at:]
        else:
            content = content + "\n" + new_line
    else:
        # section 不存在则追加到文件末尾
        content = content + f"\n{section}\n{new_line}\n"

    LOCAL_SKILL.write_text(content, encoding="utf-8")
    print(f"  [WRITE] {LOCAL_SKILL}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(apply: bool = False) -> int:
    """自进化主流程.

    Args:
        apply: True = 实际写入伴生文件; False = 干跑（只打印）
    Returns:
        退出码: 0 = 成功/无变更, 1 = 错误
    """
    dry_run = not apply

    print("=" * 60)
    print("  aiVideo 技能自进化")
    print("=" * 60)

    # 1. 检查 script_result.json 是否存在
    if not SCRIPT_FILE.exists():
        print(f"[ERROR] script_result.json 不存在: {SCRIPT_FILE}")
        return 1

    # 2. 获取 git diff
    diff = _get_git_diff(SCRIPT_FILE)
    if not diff:
        print("[INFO] script_result.json 无未提交变更，无需进化")
        return 0

    # 3. 解析 diff → 变更列表
    changes = _parse_diff(diff)
    print(f"\n[INFO] 检测到 {len(changes)} 处字段变更")
    if not changes:
        print("[INFO] 无可学习的字段变更（仅监控描述性字段）")
        return 0

    for c in changes[:10]:
        print(f"  {c['field']}: {c['old'][:40]} -> {c['new'][:40]}")

    # 4. 识别模式
    patterns = _analyze_patterns(changes)
    if not patterns:
        print("\n[INFO] 未检测到足够的学习信号（需 ≥2 次相同模式）")
        return 0

    print(f"\n[PLAN] 检测到 {len(patterns)} 个可学习模式:")

    # 5. 应用更新
    written_files = _update_local_skill(patterns, dry_run=dry_run)

    # 6. Write surface 校验
    ALLOWED = [".aiVideo/skills/generate-script-local/"]
    try:
        assert_write_surface(written_files, ALLOWED, "update-script")
    except WriteSurfaceViolation as e:
        print(f"\n[FAIL] {e}")
        return 1

    if dry_run:
        print(f"\n[INFO] 干跑完成。使用 --apply 实际写入。")
    else:
        print(f"\n[DONE] 已写入 {len(written_files)} 个文件")

    return 0


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    sys.exit(main(apply=apply_flag))
