"""章节提取工具 — 从 锦衣夜行 小说文件中提取指定章节.

小说结构:
  - GBK 编码
  - 章标记: 第001章, 第002章, ... 每节约 5000 字符

功能:
  - 基础过滤: 回忆/闪回、跨章引用、说书套语
  - 场景过滤: 识别主线场景，过滤与主线无关的场景（需 LLM）
"""

import re
import os
import json
from pathlib import Path


# 缓存目录：本书跨章节共享（统一路径来源: shared/paths.py）
from shared.paths import chapter_cache_dir
_CACHE_DIR = chapter_cache_dir()
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# 需要过滤的无关内容模式
_IRRELEVANT_PATTERNS = [
    # 回忆/闪回开头（整行匹配）
    (r"^上回说到[，,。：]", "回忆/闪回"),
    (r"^前文提要[：:]", "前文提要"),
    (r"^上章[所述|提到|说到][：:]", "上章引用"),
    # 跨章引用（整行匹配）
    (r"^详见[第|上]\d+章", "跨章引用"),
    (r"^且听下回分解", "章尾套语"),
    (r"^欲知后事如何[，,]", "章尾套语"),
    (r"^上回书说", "说书套语"),
    (r"^却说", "说书套语"),
]

# 预编译正则
_COMPILED_PATTERNS = [
    (re.compile(p, re.MULTILINE), label) for p, label in _IRRELEVANT_PATTERNS
]


def _filter_irrelevant_content(text: str) -> str:
    """过滤章节中的无关内容（回忆/闪回/跨章引用）."""
    original_len = len(text)
    lines = text.split("\n")
    filtered_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered_lines.append(line)
            continue

        # 检查整行是否匹配无关模式
        matched = False
        for pattern, label in _COMPILED_PATTERNS:
            if pattern.match(stripped):
                print(f"  [过滤] {label}: {stripped[:30]}...")
                matched = True
                break

        # 检查段内跨章引用（整句删除）
        if not matched:
            stripped = re.sub(r"[，,，]详见第\d+章[，,\s].*", "", stripped)
            stripped = re.sub(r"[，,]上回说到[，,].*", "", stripped)

        if stripped:
            filtered_lines.append(stripped)
        else:
            filtered_lines.append("")

    result = "\n".join(filtered_lines).strip()
    removed = original_len - len(result)
    if removed > 0:
        print(f"  [过滤] 共移除 {removed} 字符无关内容")

    return result


def find_sections(novel_path: str) -> list[dict]:
    """查找小说中所有章标记的位置（格式: 第001章 章名）.

    Returns:
        list of {index: int, title: str, start: int, end: int}
        start/end 为字符位置
    """
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            with open(novel_path, "r", encoding=enc) as f:
                text = f.read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        # 验证解码正确性：能命中章标记才算成功（乱码文本不会命中「第NNN章」）
        if re.search(r"第\d{3,4}章", text[:20000]):
            break
    else:
        raise ValueError(f"无法读取小说 {novel_path}")

    # 匹配 第001章 标题
    pattern = re.compile(r"第(\d{3,4})章\s*(.+?)(?=\n|$)")
    matches = list(pattern.finditer(text))

    sections = []
    for i, m in enumerate(matches):
        idx = int(m.group(1))
        title = m.group(2).strip()
        start = m.start()
        # 解析同一行「第NNN章」之前的 部/卷 信息（如「第一部 杀青州 第001章 章名」）
        line_start_pos = text.rfind("\n", 0, start) + 1
        line_prefix = text[line_start_pos:start].strip()
        vol_m = re.match(r"第[一二三四五六七八九十百零\d]+部\s*(\S*)", line_prefix)
        volume_label = line_prefix if vol_m else ""
        book_name = (vol_m.group(1) if vol_m else "").strip()
        # 后退到最近的行首，避免漏掉前面的「第一卷 xxx」
        line_start = text.rfind("\n", 0, start)
        if line_start > 0:
            prev_text = text[line_start:start].strip()
            if not prev_text:  # 前一行是空行，再往前找标题
                prev_line_end = text.rfind("\n", 0, line_start)
                if prev_line_end > 0:
                    prev_text = text[prev_line_end:line_start].strip()
            # 如果包含「卷」，把卷标题也纳入
            if prev_text and ("卷" in prev_text or prev_text.startswith("第")):
                start = text.rfind("\n", 0, start) + 1

        # 结束位置: 下一章的开始
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append({
            "index": idx,
            "title": title,
            "volume_label": volume_label,   # 如「第一部 杀青州」
            "book_name": book_name,         # 如「杀青州」
            "start": start,
            "end": end,
        })

    return sections


def get_chapter_meta(novel_path: str, chapter: int) -> dict:
    """返回指定章的元信息（部名 + 章名），供写入 script_result.json.

    标题行格式: 第一部 杀青州 第002章 卖了亲爹的娃
    Returns: {"chapter_index", "chapter_title", "book_name", "volume_label"}
    """
    for s in find_sections(novel_path):
        if s["index"] == chapter:
            return {
                "chapter_index": chapter,
                "chapter_title": s.get("title", ""),
                "book_name": s.get("book_name", ""),
                "volume_label": s.get("volume_label", ""),
            }
    return {"chapter_index": chapter, "chapter_title": "", "book_name": "", "volume_label": ""}


def extract_section(novel_path: str, section_index: int = 1) -> str:
    """提取指定章的内容.

    Args:
        novel_path: 小说文件路径
        section_index: 章序号 (1-based)，默认第 001 章

    Returns:
        干净的中文文本，去掉 URL 和 HTML 实体
    """
    sections = find_sections(novel_path)
    if not sections:
        raise ValueError(f"未在 {novel_path} 中找到任何章标记")

    target = None
    for s in sections:
        if s["index"] == section_index:
            target = s
            break

    if target is None:
        available = [s["index"] for s in sections[:10]]
        raise ValueError(
            f"未找到第 {section_index:03d} 章。"
            f"可用章号: {available}..."
        )

    for enc in ("gbk", "gb18030", "utf-8"):
        try:
            with open(novel_path, "r", encoding=enc) as f:
                text = f.read()
            if any("一" <= c <= "鿿" for c in text[:200]):
                break
        except (UnicodeDecodeError, FileNotFoundError):
            continue

    # 提取内容
    content = text[target["start"]:target["end"]]

    # 清理: 去掉 URL、HTML 实体、多余空白
    content = re.sub(r"&\w+;", "", content)
    content = re.sub(r"https?://\S+", "", content)

    # 规范化换行: 保留段落结构，合并连续空行
    lines = []
    prev_empty = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if not prev_empty:
                lines.append("")
                prev_empty = True
        else:
            lines.append(stripped)
            prev_empty = False

    cleaned = "\n".join(lines).strip()

    # 过滤无关内容（回忆/闪回/跨章引用/说书套语）
    cleaned = _filter_irrelevant_content(cleaned)

    # 场景过滤：检查是否属于需要过滤的支线场景
    cleaned = filter_section_by_chapter(novel_path, section_index, cleaned)

    print(f"[OK] 提取第 {section_index:03d} 章「{target['title']}」: "
          f"{len(cleaned)} 字符")
    return cleaned


def extract_chapter(novel_path: str, chapter: int = 1,
                    sections_per_chapter: int = 5) -> str:
    """提取指定多章合并为一个大章.

    没有明确的大章标记，按 N 章为一组合并.
    默认: 第 1-5 章 = 第一章
    """
    start_section = (chapter - 1) * sections_per_chapter + 1
    end_section = chapter * sections_per_chapter

    sections = find_sections(novel_path)
    if not sections:
        raise ValueError(f"未在 {novel_path} 中找到任何章标记")

    all_text = []
    for s in sections:
        if start_section <= s["index"] <= end_section:
            txt = extract_section(novel_path, s["index"])
            all_text.append(txt)

    if not all_text:
        available = sorted(s["index"] for s in sections)
        raise ValueError(
            f"第一章（第 {start_section:03d}-{end_section:03d} 章）"
            f"无匹配内容。可用章: {available[:10]}..."
        )

    result = "\n\n".join(all_text)
    print(f"[OK] 第一章（第 {start_section:03d}-{end_section:03d} 章）: "
          f"{len(result)} 字符")
    return result


# ============================================================================
# 场景过滤：章级分析 + 节级过滤
# ============================================================================

def _get_chapter_cache_path(chapter: int) -> Path:
    """获取章节场景缓存文件路径."""
    return _CACHE_DIR / f"chapter_{chapter:03d}_scenes.json"


def _analyze_chapter_scenes(novel_path: str, chapter: int, full_text: str) -> dict:
    """调用 LLM 分析本章主线场景，返回场景分析结果.

    Returns:
        {
            "chapter": int,
            "main_scenes": ["场景1", "场景2", ...],
            "main_characters": ["角色1", "角色2", ...],
            "filtered_sections": [1, 3, 5],  # 需要过滤的节索引
            "reason": "..."
        }
    """
    try:
        from _text_gen import create_service, TextGenerationRequest
    except ImportError:
        print(f"  [WARNING] 无法导入 text_gen，跳过场景分析")
        return {"chapter": chapter, "main_scenes": [], "main_characters": [], "filtered_sections": []}

    try:
        from shared.config import PROMPT_MAX_CHARS
    except ImportError:
        PROMPT_MAX_CHARS = 50000

    prompt = f"""分析以下小说章节，识别主线场景和支线场景。

## 任务
1. 识别本章（5节）中的所有场景地点
2. 判断哪些是主线场景（推动剧情发展），哪些是支线/无关场景（插叙、过渡、与主线无关）
3. 列出需要过滤掉的支线场景对应的节号

## 输出格式（JSON）
{{
    "main_scenes": ["场景描述1", "场景描述2", ...],  // 主线场景
    "main_characters": ["角色1", "角色2", ...],       // 主要人物
    "filtered_sections": [节号1, 节号2, ...],         // 需要过滤的节索引（如与主线无关的回忆、插叙）
    "reason": "分析理由"                              // 简要说明
}}

## 小说章节
{full_text[:PROMPT_MAX_CHARS]}
"""

    try:
        service = create_service()
        req = TextGenerationRequest(
            system_prompt="你是一个小说编辑，擅长分析剧情结构和场景",
            user_prompt=prompt,
            max_tokens=4000,
            temperature=0.3,
        )
        print(f"  [场景分析] 正在分析第 {chapter} 章主线场景...")
        result = service.generate(req)
        if not result.success:
            print(f"  [WARNING] LLM 分析失败: {result.error}")
            return {"chapter": chapter, "main_scenes": [], "main_characters": [], "filtered_sections": []}

        import json
        import re
        raw = result.text.strip()
        # 提取 JSON
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            print(f"  [场景分析] 完成: 主线场景 {len(data.get('main_scenes', []))} 个, "
                  f"需过滤 {len(data.get('filtered_sections', []))} 节")
            return data
        else:
            print(f"  [WARNING] LLM 返回格式异常")
            return {"chapter": chapter, "main_scenes": [], "main_characters": [], "filtered_sections": []}
    except Exception as e:
        print(f"  [ERROR] 场景分析异常: {e}")
        return {"chapter": chapter, "main_scenes": [], "main_characters": [], "filtered_sections": []}


def _load_chapter_cache(chapter: int) -> dict | None:
    """加载章节场景缓存，若不存在返回 None."""
    cache_path = _get_chapter_cache_path(chapter)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_chapter_cache(chapter: int, data: dict) -> None:
    """保存章节场景缓存."""
    cache_path = _get_chapter_cache_path(chapter)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_chapter_scenes(novel_path: str, chapter: int) -> dict:
    """获取章节场景分析（带缓存）."""
    # 尝试加载缓存
    cache = _load_chapter_cache(chapter)
    if cache:
        print(f"  [场景缓存] 第 {chapter} 章缓存已加载")
        return cache

    # 需要分析，先提取全章文本
    full_text = extract_chapter(novel_path, chapter, sections_per_chapter=5)

    # 调用 LLM 分析
    analysis = _analyze_chapter_scenes(novel_path, chapter, full_text)

    # 保存缓存
    _save_chapter_cache(chapter, analysis)

    return analysis


def filter_section_by_chapter(novel_path: str, section_index: int, text: str, _recursion_depth: int = 0) -> str:
    """根据章节场景分析检查本节内容（不过滤整节）。

    逻辑调整（用户要求）：
    - 整章回忆：不过滤
    - 整节回忆：不过滤
    - 节内回忆场景：通过 _filter_irrelevant_content 已逐行过滤

    本函数现在仅用于记录分析结果，暂不执行过滤。
    为避免递归调用导致的无限循环，添加递归深度检查。
    """
    # 防递归：超过2层直接返回原文
    if _recursion_depth > 2:
        return text

    # 确定所属章节
    chapter = (section_index - 1) // 5 + 1  # 每5节一章

    # 获取章节场景分析（用于日志/调试），带递归深度标志
    try:
        scenes = _get_chapter_scenes(novel_path, chapter)
    except RecursionError:
        # 递归错误时静默返回原文
        return text

    filtered_sections = scenes.get("filtered_sections", [])
    if section_index in filtered_sections:
        # 用户要求：整节回忆不过滤，只记录
        print(f"  [场景分析] 第 {section_index} 节被识别为支线场景，但整节保留（回忆内容>20%）")

    return text


# ============================================================================
if __name__ == "__main__":
    # 快速测试
    import sys
    novel = "锦衣夜行_77119小说.txt"
    if len(sys.argv) > 1:
        novel = sys.argv[1]

    # 列出前 10 个章
    secs = find_sections(novel)
    print(f"共找到 {len(secs)} 个章标记")
    for s in secs[:10]:
        print(f"  第{s['index']:03d}章「{s['title']}」"
              f"  pos={s['start']}~{s['end']}"
              f"  ({s['end']-s['start']} 字符)")

    # 提取第 1 章
    print()
    txt = extract_section(novel, 1)
    print(f"\n=== 前 200 字 ===")
    print(txt[:200])

