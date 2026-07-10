"""JSON 安全解析工具 — 从 waoowaoo 的 json-repair.ts 适配

用于从 LLM 输出中安全提取并修复 JSON（处理 markdown 围栏、
多余文本包裹、不平衡括号、尾部逗号等常见问题）。
"""

import json
import re
from typing import Any, Optional


def strip_markdown_fence(text: str) -> str:
    """移除 ```json ... ``` 或 ``` ... ``` 围栏。"""
    result = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    result = re.sub(r"^```\s*", "", result, flags=re.MULTILINE)
    result = re.sub(r"\s*```$", "", result, flags=re.MULTILINE)
    return result.strip()


def extract_json_substring(text: str) -> str:
    """从大段文本中提取第一个完整的 JSON 对象或数组。"""
    first_brace = text.find("{")
    first_bracket = text.find("[")

    if first_brace == -1 and first_bracket == -1:
        return text

    is_object = (first_bracket == -1 or (first_brace != -1 and first_brace < first_bracket))
    open_char = "{" if is_object else "["
    close_char = "}" if is_object else "]"
    start = first_brace if is_object else first_bracket

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text


def safe_parse_json(text: str) -> Any:
    """安全解析 LLM 输出的 JSON 文本。"""
    cleaned = strip_markdown_fence(text.strip())

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    extracted = extract_json_substring(cleaned)
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass

    fixed = _fix_common_json_errors(extracted)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    try:
        from jsonrepair import repair_json
        return json.loads(repair_json(extracted))
    except ImportError:
        raise ValueError(
            "无法解析 LLM 输出的 JSON。建议安装 jsonrepair: pip install jsonrepair\n"
            f"文本片段: {text[:300]}"
        )


def safe_parse_json_object(text: str) -> dict[str, Any]:
    """安全解析为 JSON 对象。"""
    result = safe_parse_json(text)
    if isinstance(result, dict):
        return result
    raise ValueError(f"期望 JSON 对象，但得到 {type(result).__name__}")


def safe_parse_json_array(text: str, fallback_key: Optional[str] = None) -> list[dict[str, Any]]:
    """安全解析为 JSON 对象数组，处理 LLM 包装数组在对象中的情况。"""
    result = safe_parse_json(text)

    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]

    if isinstance(result, dict):
        keys_to_try = [fallback_key] if fallback_key else list(result.keys())
        for key in keys_to_try:
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

    raise ValueError(f"期望 JSON 数组，但得到 {type(result).__name__}")


def _fix_common_json_errors(text: str) -> str:
    """修复常见 JSON 格式问题（单引号转双引号）。"""
    result = []
    i = 0
    in_single = False
    in_double = False
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            result.append(ch + text[i + 1])
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single
            result.append('"')
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def parse_json_array(text: str, fallback_key: Optional[str] = None) -> list[dict[str, Any]]:
    """便捷函数：解析 JSON 数组，失败时返回空列表。"""
    try:
        return safe_parse_json_array(text, fallback_key)
    except (ValueError, json.JSONDecodeError):
        return []


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    """便捷函数：解析 JSON 对象，失败时返回 None。"""
    try:
        return safe_parse_json_object(text)
    except (ValueError, json.JSONDecodeError):
        return None
