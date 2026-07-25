"""JSON 安全解析工具 — 从 waoowaoo 的 json-repair. ts 适配
用于从 LLM 输出中安全提取并修复 JSON。"""

import json
import re
from typing import Any, Optional


def strip_markdown_fence(text: str) -> str:
    result = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    result = re.sub(r"^```\s*", "", result, flags=re.MULTILINE)
    result = re.sub(r"\s*```$", "", result, flags=re.MULTILINE)
    return result.strip()


def extract_json_substring(text: str) -> str:
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


def _is_cjk(c: str) -> bool:
    o = ord(c)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


def _has_cjk(text: str) -> bool:
    for c in text:
        if _is_cjk(c):
            return True
    return False


def _manual_fix(text: str) -> str:
    """手动修复中文标点和引号。"""
    # 中文引号转标准引号
    text = text.replace(chr(0x201C), '"').replace(chr(0x201D), '"')
    text = text.replace(chr(0x300E), '"').replace(chr(0x300F), '"')
    text = text.replace(chr(0x300C), '"').replace(chr(0x300D), '"')
    # 中文标点
    text = text.replace('，', ',').replace('：', ':')
    text = text.replace('（', '(').replace('）', ')')
    text = text.replace('【', '[').replace('】', ']')
    # 转义两 CJK 字符之间的 ASCII 双引号（如 清脆的"咚"声 → 清脆的\"咚\"声）
    text = _escape_cjk_adjacent_quotes(text)
    return text


def _escape_cjk_adjacent_quotes(text: str) -> str:
    """将两 CJK 字符之间的 ASCII 双引号转义。

    LLM 输出中，中文拟声词常用 ASCII 双引号包裹（如 发出清脆的"咚"声），
    这些引号会被 JSON 解析器误认为字符串结束符，需要转义。
    """
    result = []
    for i, ch in enumerate(text):
        if ch == '"' and i > 0 and i < len(text) - 1:
            if _is_cjk(text[i - 1]) and _is_cjk(text[i + 1]):
                result.append('\\"')
                continue
        result.append(ch)
    return ''.join(result)


def _fix_at_error(text: str, error: json.JSONDecodeError) -> str:
    """在错误位置修复 JSON。

    策略：从错误位置向前找冒号（跳过字符串内的冒号），
    找到所属字符串值，用 json.dumps 重新转义。

    关键：判断字符串结束引号时检查引号后第一个非空白字符
    是否为 JSON 结构定界符（, } ]），而非简单配对下一个 " 。
    """
    pos = error.pos
    n = len(text)

    # 向前找冒号，跳过字符串内的冒号
    colon_pos = -1
    scan = pos
    in_str = False
    while scan >= 0:
        ch = text[scan]
        if ch == '"' and (scan == 0 or text[scan - 1] != '\\'):
            in_str = not in_str
        elif ch == ':' and not in_str:
            colon_pos = scan
            break
        scan -= 1

    if colon_pos < 0:
        return None

    # 从冒号后找字符串开始引号
    quote_pos = -1
    for i in range(colon_pos + 1, min(colon_pos + 500, n)):
        if text[i] == '"':
            quote_pos = i
            break

    if quote_pos < 0:
        return None

    # 找字符串结束引号：引号后第一个非空白字符必须是 JSON 定界符
    close_pos = -1
    i = quote_pos + 1
    while i < n:
        ch = text[i]
        if ch == '\\' and i + 1 < n:
            i += 2
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] in ' \t\n\r':
                j += 1
            if j >= n or text[j] in ',}:]':
                close_pos = i
                break
        i += 1

    if close_pos < 0:
        return None

    raw_value = text[quote_pos + 1:close_pos]

    if '"' in raw_value:
        escaped = json.dumps(raw_value, ensure_ascii=False)
        result = text[:quote_pos] + escaped + text[close_pos + 1:]
        return result

    return None


def _fix_common_errors(text: str) -> str:
    # 先手动修复中文标点
    text = _manual_fix(text)

    # 尝试直接解析
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError as e:
        # 尝试在错误位置修复
        fixed = _fix_at_error(text, e)
        if fixed:
            try:
                json.loads(fixed)
                return fixed
            except:
                pass

    # 单引号转双引号
    result = []
    in_single = False
    in_double = False
    escaped = False

    for ch in text:
        if escaped:
            escaped = False
            result.append(ch)
        elif ch == '\\':
            escaped = True
            result.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single
            result.append('"')
        else:
            result.append(ch)

    return ''.join(result)


def safe_parse_json(text: str) -> Any:
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

    fixed = _fix_common_errors(extracted)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 修复尾部逗号
    no_trailing = re.sub(r",\s*}", "}", extracted)
    no_trailing = re.sub(r",\s*]", "]", no_trailing)
    if no_trailing != extracted:
        try:
            return json.loads(no_trailing)
        except json.JSONDecodeError:
            pass
        fixed2 = _fix_common_errors(no_trailing)
        try:
            return json.loads(fixed2)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法解析 JSON。文本片段:\n{text[:300]}")


def safe_parse_json_object(text: str) -> dict[str, Any]:
    result = safe_parse_json(text)
    if isinstance(result, dict):
        return result
    raise ValueError(f"期望 JSON 对象，得到 {type(result).__name__}")


def safe_parse_json_array(text: str, fallback_key: Optional[str] = None) -> list[dict[str, Any]]:
    result = safe_parse_json(text)
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        keys = [fallback_key] if fallback_key else list(result.keys())
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"期望 JSON 数组，得到 {type(result).__name__}")


def parse_json_array(text: str, fallback_key: Optional[str] = None) -> list[dict[str, Any]]:
    try:
        return safe_parse_json_array(text, fallback_key)
    except (ValueError, json.JSONDecodeError):
        return []


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    try:
        return safe_parse_json_object(text)
    except (ValueError, json.JSONDecodeError):
        return None


# 向后兼容别名
_fix_common_json_errors = _fix_common_errors