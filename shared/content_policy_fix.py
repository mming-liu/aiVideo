"""Agnes 422 内容违规自动修复模块.

功能:
  - 解析 422 错误信息，提取疑似敏感词
  - 从已知敏感词库查找安全替代词
  - 自动替换后重试提交
  - 记录新发现的敏感词到本地词库

2026-08-09 审计 P0-2：敏感词替换词表统一收敛在 shared/content_safety.py
（style_prefix 先验替换 + 本模块 422 后验修复共用同一权威词表），
删除本模块重复的 _VIOLENT_WORDS/_SEXUAL_WORDS/_GORE_WORDS 三份词表。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 权威替换词表（与 style_prefix.sanitize_prompt 共用，单一来源）
from shared.content_safety import SENSITIVE_SUBSTITUTIONS, sanitize as safety_sanitize

ALL_SENSITIVE_WORDS = dict(SENSITIVE_SUBSTITUTIONS)


# ============================================================================
# 二、错误解析与敏感词提取
# ============================================================================

def parse_422_error(error_text: str) -> list[str]:
    """从 422 错误信息中解析可能的敏感词.

    返回可能的敏感词列表（按匹配位置排序）。
    """
    possible_words = []

    # 1. 尝试提取引号中的词
    quotes = re.findall(r'["""\']([^""\']{2,10})[""\'“”]', error_text)
    for w in quotes:
        if len(w) >= 2:
            possible_words.append(w)

    # 2. 尝试提取 "keyword: xxx" 格式
    keyword_match = re.search(r'keyword[:\s]+([^\s,\]]{2,10})', error_text)
    if keyword_match:
        possible_words.append(keyword_match.group(1))

    # 3. 检查已知敏感词是否出现在错误信息中
    for word in ALL_SENSITIVE_WORDS:
        if word in error_text:
            possible_words.append(word)

    # 去重，保持出现顺序
    seen = set()
    result = []
    for w in possible_words:
        if w not in seen:
            seen.add(w)
            result.append(w)

    return result


def find_substitutes(word: str) -> Optional[str]:
    """查找敏感词的安全替代词."""
    # 精确匹配
    if word in ALL_SENSITIVE_WORDS:
        return ALL_SENSITIVE_WORDS[word]

    # 前缀匹配（更宽泛）
    for sensitive, safe in ALL_SENSITIVE_WORDS.items():
        if word.startswith(sensitive) or sensitive in word:
            return safe

    return None


# ============================================================================
# 三、敏感词库持久化
# ============================================================================

_SENSITIVE_WORD_FILE = _PROJECT_ROOT / ".cache" / "sensitive_words.json"


def _load_word_library() -> dict:
    """加载本地敏感词库."""
    if _SENSITIVE_WORD_FILE.exists():
        try:
            return json.loads(_SENSITIVE_WORD_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_word_library(library: dict) -> None:
    """保存敏感词库到本地."""
    _SENSITIVE_WORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SENSITIVE_WORD_FILE.write_text(
        json.dumps(library, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def record_new_sensitive_word(word: str, context: str = "") -> None:
    """记录新发现的敏感词."""
    library = _load_word_library()
    if word not in library:
        # 尝试自动推断替代词
        substitute = _infer_substitute(word, context)
        library[word] = {
            "substitute": substitute,
            "context": context[:200] if context else "",
            "discovered_at": str(Path.cwd()),
        }
        _save_word_library(library)
        print(f"  [敏感词库] 记录新词: {word} -> {substitute}")


def _infer_substitute(word: str, context: str) -> str:
    """根据上下文推断安全的替代词."""
    # 暴力相关
    if any(c in word for c in ["血", "刺", "杀", "砍", "斩", "割"]):
        if "血" in word:
            return "红" if "血" in word else "剑"
        return "剑光" if "剑" in context else "身影"

    # 性相关
    if any(c in word for c in ["裸", "亲", "抚", "抱"]):
        return "衣" if "裸" in word else "凝望"

    # 恐怖相关
    if any(c in word for c in ["尸", "骨", "内脏", "脑"]):
        return "身影" if "尸" in word else "骨架"

    # 默认
    return "***"


def get_all_sensitive_words() -> dict:
    """获取所有敏感词及替代词."""
    library = _load_word_library()
    merged = {**ALL_SENSITIVE_WORDS}
    for word, info in library.items():
        if isinstance(info, dict) and info.get("substitute"):
            merged[word] = info["substitute"]
        else:
            merged[word] = info if isinstance(info, str) else "***"
    return merged


# ============================================================================
# 四、自动修复入口
# ============================================================================

def auto_fix_prompt(prompt: str, error_text: str, verbose: bool = True) -> Optional[str]:
    """自动修复 prompt 中的敏感词.

    Args:
        prompt: 原始 prompt
        error_text: 422 错误信息
        verbose: 是否打印修复过程

    Returns:
        修复后的 prompt，如果无法修复则返回 None
    """
    # 1. 从错误信息中提取可能的敏感词
    suspected_words = parse_422_error(error_text)

    if not suspected_words:
        if verbose:
            print(f"  [审核修复] 无法从错误信息提取敏感词，执行全量盲替换")
        # 盲替换：把所有已知敏感词都替换掉（适用于 400/不指明敏感词的错误）
        return _blind_replace_all(prompt, verbose=verbose)

    if verbose:
        print(f"  [422修复] 疑似敏感词: {suspected_words}")

    # 2. 尝试修复
    fixed_prompt = prompt
    for word in suspected_words:
        substitute = find_substitutes(word)

        if substitute:
            if word in fixed_prompt:
                fixed_prompt = fixed_prompt.replace(word, substitute)
                if verbose:
                    print(f"  [422修复] 替换 '{word}' -> '{substitute}'")
                # 记录新词
                record_new_sensitive_word(word, prompt)
        else:
            # 尝试用更通用的规则
            for sensitive, safe in ALL_SENSITIVE_WORDS.items():
                if sensitive in fixed_prompt:
                    fixed_prompt = fixed_prompt.replace(sensitive, safe)
                    if verbose:
                        print(f"  [422修复] 替换 '{sensitive}' -> '{safe}'")
                    # 记录
                    record_new_sensitive_word(sensitive, prompt)
                    break

    # 3. 返回修复后的 prompt（如果有变化）
    if fixed_prompt != prompt:
        return fixed_prompt

    return None


def _blind_replace_all(prompt: str, verbose: bool = True) -> Optional[str]:
    """盲替换：将所有已知敏感词按优先级全部替换（适用于 400/不指明敏感词的错误）.

    按词长降序（长词优先），避免短词误伤长词的一部分。
    """
    fixed_prompt = prompt
    replaced_count = 0

    # 用权威词表（shared/content_safety），按词长降序（"刺入" 在 "刺" 之前）
    all_words = dict(ALL_SENSITIVE_WORDS)
    # 本地词库
    local = _load_word_library()
    for word, info in local.items():
        if isinstance(info, dict) and info.get("substitute"):
            all_words[word] = info["substitute"]
        elif isinstance(info, str):
            all_words[word] = info

    sorted_words = sorted(all_words.keys(), key=lambda w: -len(w))

    for word in sorted_words:
        if word in fixed_prompt:
            substitute = all_words[word]
            fixed_prompt = fixed_prompt.replace(word, substitute)
            replaced_count += 1
            if verbose and replaced_count <= 5:
                print(f"  [盲替换] '{word}' -> '{substitute}'")

    if replaced_count > 0:
        if verbose:
            print(f"  [盲替换] 共替换 {replaced_count} 个敏感词")
        return fixed_prompt

    return None