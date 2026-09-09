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


def _manual_fix(text: str) -> str:
    """手动修复中文标点和引号 — 仅转换 JSON 结构位置的字符，绝不改动字符串值内容。

    LLM 常见两种输出：
      A. 结构用中文引号/标点（如 {"title"： "xx"， "shots"：[...]}）—— 需转 ASCII
      B. 字符串值内含中文引号（对话「张十三道：“凶手...”」）—— 这是合法 Unicode 内容，
         必须原样保留

    旧实现把「“ ” → "」全局替换，导致 B 类内容里的对话引号被转成未转义的 ASCII 引号，
    字符串被截断 → JSON 解析崩溃（本流水线 DeepSeek 的输出即为此类）。此处用状态机
    区分「结构位置」与「字符串内部」：只有结构位置的中文标点才转换。
    """
    out: list[str] = []
    in_string = False
    opened_by_real_quote = False  # True=当前字符串由 ASCII " 开启（内部中文引号全是内容）
    escaped = False
    cjk_quotes = '“”「」『』'  # “ ” 「 」 『 』

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if escaped:
            out.append(ch)
            escaped = False
            i += 1
            continue
        if ch == '\\':
            out.append(ch)
            escaped = True
            i += 1
            continue

        if not in_string:
            # ---- 结构位置 ----
            if ch == '"':
                in_string = True
                opened_by_real_quote = True
                out.append(ch)
            elif ch in cjk_quotes:
                # 中文引号在结构位置 = 当作 ASCII 双引号（开/闭字符串）
                in_string = True
                opened_by_real_quote = False
                out.append('"')
            elif ch == '，':      # ，
                out.append(',')
            elif ch == '：':      # ：
                out.append(':')
            elif ch == '（':      # （
                out.append('(')
            elif ch == '）':      # ）
                out.append(')')
            elif ch == '【':      # 【
                out.append('[')
            elif ch == '】':      # 】
                out.append(']')
            else:
                out.append(ch)
            i += 1
            continue

        # ---- 字符串内部 ----
        if opened_by_real_quote:
            if ch == '"':
                # 未转义的 ASCII 引号关闭字符串（这是 LLM 真实的结构错误，交给 _fix_at_error）
                in_string = False
                out.append(ch)
            else:
                # 中文引号/标点在此全是内容，原样保留
                out.append(ch)
        else:
            # 由中文引号开启的字符串：中文闭合引号关闭，其余内容保留
            if ch in cjk_quotes:
                in_string = False
                out.append('"')
            else:
                out.append(ch)
        i += 1

    return ''.join(out)


def _fix_at_error(text: str, error: json.JSONDecodeError) -> str:
    """在错误位置修复 JSON。

    策略：从错误位置向前找冒号（跳过字符串内的冒号），
    找到所属字符串值，用 json.dumps 重新转义。

    关键：判断字符串结束引号时检查引号后第一个非空白字符
    是否为 JSON 结构定界符（, } ]），而非简单配对下一个 " 。
    """
    pos = error.pos
    n = len(text)

    # 防御：错误位置可能在 EOF（pos == len(text)），此时退到最后一个字符再回溯，
    # 避免 text[pos] 越界抛 IndexError。若修复无果会返回 None，由上层降级处理。
    if pos >= n:
        pos = n - 1
    if pos < 0:
        return None

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
    # 先手动修复中文标点（状态机：仅转换结构位置，保留字符串值内中文引号）
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


def _truncate_fix(text: str) -> str:
    """截断恢复：LLM 输出可能被 API 中途截断（如 lf_desc 字符串写到一半戛然而止）。

    这种输入的特征：结尾停在未闭合字符串内，结构括号未收束。

    恢复策略：
      0. 先正向扫描确认"以未闭合字符串结尾"（否则是完整 JSON，直接原样返回）
      1. 反向定位未闭合字符串的**起始引号** start_quote
      2. 丢弃 start_quote 起的半个字段值；再往前回退到该字段 key 开头，连同字段一起丢弃
      3. 去掉尾部逗号，收束所有未闭合 } ]

    有损恢复：丢弃未完成镜头末尾的字段（如 lf_desc），保留前面完整字段。
    对完整 JSON 必须是无操作。
    """
    # 0. 正向扫描：是否以未闭合字符串结尾？且是否存在一个未闭合的结构？
    in_str = False
    esc = False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if not in_str:
            if ch == '"':
                in_str = True
        else:
            if ch == '"':
                in_str = False
    if not in_str:
        return text  # 完整 JSON，无操作

    # 1. 反向定位未闭合字符串的起始引号
    #    从末尾往前扫描；未闭合字符串的起始引号 = 反向遇到的第一个引号，
    #    但要注意它可能是已闭合字符串的结束引号。可靠做法：反向走时用栈匹配。
    #    简化但可靠：从末尾找到未闭合串起点 —— 从末尾反向，跳过已闭合成对引号。
    n = len(text)
    start_quote = -1
    i = n - 1
    esc = False
    while i >= 0:
        ch = text[i]
        if esc:
            esc = False
            i -= 1
            continue
        if ch == '\\':
            esc = True
            i -= 1
            continue
        if ch == '"':
            # 反向遇到的引号：由于正向已确认末尾在字符串内，
            # 从末尾反向遇到的第一个引号就是未闭合串的起始引号
            # （其左边要么是冒号+key，要么是数组/对象起始）
            start_quote = i
            break
        i -= 1
    if start_quote < 0:
        return text

    # 2. 从 start_quote 往前找该字段 key 的冒号，把整个字段丢弃
    colon = text.rfind('":', 0, start_quote)
    if colon < 0:
        head = text[:start_quote].rstrip()
    else:
        key_open = text.rfind('"', 0, colon)
        if key_open < 0:
            head = text[:start_quote].rstrip()
        else:
            head = text[:key_open].rstrip()

    # 3. 去掉尾部逗号
    while head.endswith(','):
        head = head[:-1].rstrip()

    # 4. 收束未闭合括号
    open_stack = []
    in_str2 = False
    esc2 = False
    for ch in head:
        if esc2:
            esc2 = False
            continue
        if ch == '\\':
            esc2 = True
            continue
        if not in_str2:
            if ch == '"':
                in_str2 = True
            elif ch == '{':
                open_stack.append('}')
            elif ch == '[':
                open_stack.append(']')
            elif ch == '}':
                if open_stack and open_stack[-1] == '}':
                    open_stack.pop()
            elif ch == ']':
                if open_stack and open_stack[-1] == ']':
                    open_stack.pop()
        else:
            if ch == '"':
                in_str2 = False

    closed = ''.join(reversed(open_stack))
    return head + closed


def _truncate_to_last_element(text: str) -> str:
    """截断恢复的第二策略：回退到**最后一个完整闭合的元素**（v6.62.6，09-09）.

    `_truncate_fix` 只处理「截断落在字符串内部」这一种形态。实测另外三种同样常见
    的形态它是**无操作**（前向扫描判定 in_str=False 直接原样返回），于是整份解析
    失败、列表型提取拿到 0 项：
      - 截在数值中间： {"objects": [{"id": "a"}, {"id": "b", "length_m": 3.2
      - 截在字面量中间：{"objects": [{"id": "a"}, {"id": "b", "ok": tr
      - 截在分隔符之后：{"objects": [{"id": "a"},

    策略：正向扫描（跳过字符串内内容），记录最后一次「把嵌套深度降回 ≥1」的
    闭合符位置——即最后一个**完整的列表元素/子对象**的末尾；从那里切断，去掉
    尾随逗号，再收束外层括号。丢弃的只有本就不完整的尾部。

    对完整 JSON 无操作（返回原文）。找不到可切点时也返回原文，交给上层报错。
    """
    n = len(text)
    in_str = False
    esc = False
    depth = 0
    cut = -1  # 最后一个「深度降回 ≥1」的闭合符下标（含）
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if in_str:
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
            # depth>=1 表示闭合的是**内层**元素，外层容器还开着 → 可切点
            if depth >= 1:
                cut = i
    if depth <= 0 and not in_str:
        return text  # 结构已收束（完整 JSON），无操作
    if cut < 0:
        return text  # 一个完整元素都没有，无从抢救

    head = text[:cut + 1]
    while head.endswith((',', ' ', '\n', '\r', '\t')):
        head = head[:-1]

    # 收束剩余未闭合括号（此时 head 保证不在字符串内）
    open_stack: list[str] = []
    in_str2 = False
    esc2 = False
    for ch in head:
        if esc2:
            esc2 = False
            continue
        if ch == '\\':
            esc2 = True
            continue
        if in_str2:
            if ch == '"':
                in_str2 = False
            continue
        if ch == '"':
            in_str2 = True
        elif ch == '{':
            open_stack.append('}')
        elif ch == '[':
            open_stack.append(']')
        elif ch in '}]' and open_stack:
            open_stack.pop()
    return head + ''.join(reversed(open_stack))


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

    # 截断恢复（最后兜底）：LLM 输出被 API 中途截断时，丢弃未完成镜头，
    # 保留前面已完整序列化的部分。有损但比整体重试省时。
    truncated = _truncate_fix(extracted)
    if truncated != extracted:
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    # 截断恢复第二策略（v6.62.6）：截在数值/字面量/分隔符处时上面那步是无操作，
    # 回退到最后一个完整闭合的元素。放在最后是因为它比 _truncate_fix 更有损
    # （整个尾对象丢掉，而 _truncate_fix 能保住尾对象里已写完的字段）。
    trimmed = _truncate_to_last_element(extracted)
    if trimmed != extracted:
        try:
            return json.loads(trimmed)
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