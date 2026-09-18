"""step01_script_gen.hash_cache - 缓存管理模块

功能：
- _hash_ource(): 计算源内容指纹（用于缓存比对）
- _prompt_hanged(): 检测 prompt 是否变更
"""
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import hashlib
import json


def _hash_source(build_py_path: str, novel_preview: str, chapter: int,
                 objects_bank_data: str = "", rule_version: str = "") -> str:
    """计算源内容指纹（用于缓存比对）.

    objects_bank_data: 场景物体库序列化内容——场景物体锚点已注入 prompt，
    物体尺寸/容量/结构变化时应视为源变更、重新生成脚本（v6.6）.

    v6.43（08-23 审计）：v6.39 模板从 prompts.py 拆到 prompts/ 目录下的
    hook.md / main.md / system.md，但只哈希 prompts.py 会漏掉「只改
    .md 不动 .py」的变更。补对同目录 .md 的递归哈希（按文件相对路径 +
    mtime + 内容，保证顺序稳定）。
    """
    h = hashlib.sha256()
    build_py = Path(build_py_path)
    if build_py.exists():
        with open(build_py, "rb") as f:
            h.update(f.read())
    # 哈希 prompts/ 目录下的所有 .md（v6.39 起模板在此）
    prompts_dir = build_py.parent / "prompts"
    if prompts_dir.is_dir():
        for md_path in sorted(prompts_dir.rglob("*.md")):
            try:
                rel = md_path.relative_to(prompts_dir).as_posix()
                h.update(rel.encode())
                stat = md_path.stat()
                h.update(str(stat.st_mtime_ns).encode())
                with open(md_path, "rb") as f:
                    h.update(f.read())
            except OSError:
                continue
    h.update(novel_preview.encode())
    h.update(str(chapter).encode())
    h.update(objects_bank_data.encode())
    h.update(str(rule_version or "").encode())
    return h.hexdigest()[:16]


def _prompt_changed(build_py: Path, script_json: Path, novel_preview: str, chapter: int,
                    objects_bank_data: str = "", rule_version: str = "") -> bool:
    """检测 prompt 是否变更（通过源文件 hash 比对）."""
    if not script_json.exists():
        return True

    try:
        with open(script_json, "r", encoding="utf-8") as f:
            old_script = json.load(f)
        old_hash = old_script.get("_source_hash", "")
        new_hash = _hash_source(str(build_py), novel_preview, chapter,
                                objects_bank_data, rule_version=rule_version)
        return new_hash != old_hash
    except Exception:
        return True