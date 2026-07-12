"""Agnes 422 内容违规自动修复模块.

功能:
  - 解析 422 错误信息，提取疑似敏感词
  - 从已知敏感词库查找安全替代词
  - 自动替换后重试提交
  - 记录新发现的敏感词到本地词库
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================================
# 一、已知敏感词库（可扩展）
# ============================================================================

# 暴力动作
_VIOLENT_WORDS = {
    "刺客": "神秘人",
    "刺杀": "突袭",
    "刺进": "剑光掠过",
    "刺穿": "剑光掠过",
    "刺入": "剑光掠过",
    "刺": "剑光",
    "心口": "身前",
    "胸口": "身前",
    "砍断": "剑影交错",
    "斩断": "剑影交错",
    "剁": "斩",
    "劈开": "斩裂",
    "割喉": "剑影闪过",
    "杀死": "击败",
    "击杀": "击败",
    "掐死": "制住",
    "勒死": "制住",
    "毒死": "倒下",
    "血": "红",
    "血腥": "红影",
    "鲜血": "赤色",
    "血迹": "痕迹",
    "血痕": "痕迹",
    "流血": "滴落",
    "飞溅": "洒落",
    "腥甜": "奇怪",
}

# 性暗示
_SEXUAL_WORDS = {
    "裸": "衣",
    "赤裸": "赤身",
    "光腚": "赤脚",
    "酥胸": "衣襟",
    "玉体": "身影",
    "胴体": "身形",
    "亲吻": "凝望",
    "拥抱": "相视",
    "抚摸": "凝视",
    "缠绵": "对立",
    "交缠": "对立",
    "喘息": "呼吸",
    "呻吟": "低吟",
    "挑逗": "示意",
    "诱惑": "示意",
    "勾引": "示意",
}

# 恐怖/恶心
_GORE_WORDS = {
    "尸体": "身影",
    "尸骸": "遗影",
    "尸横遍野": "伤亡遍野",
    "尸体堆积": "伤亡",
    "脑浆": "浊液",
    "内脏": "内部",
    "肠子": "内部",
    "碎肉": "残片",
    "断肢": "伤痛",
    "断臂": "伤痛",
    "断头": "倒下",
    "残肢": "伤痛",
    "白骨": "骨架",
    "骷髅": "骨架",
    "腐肉": "残骸",
    "血肉模糊": "赤影朦胧",
    "鲜血淋漓": "赤色浸染",
    "开膛破肚": "伤裂",
    "脑浆迸裂": "重伤",
}

# 合并所有敏感词
ALL_SENSITIVE_WORDS = {**_VIOLENT_WORDS, **_SEXUAL_WORDS, **_GORE_WORDS}


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
            print(f"  [422修复] 无法从错误信息提取敏感词")
        return None

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