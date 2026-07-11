"""章节提取工具 — 从 锦衣夜行 小说文件中提取指定章节.

小说结构:
  - GBK 编码
  - 章标记: 第001章, 第002章, ... 每节约 5000 字符
"""

import re


def find_sections(novel_path: str) -> list[dict]:
    """查找小说中所有章标记的位置（格式: 第001章 章名）.

    Returns:
        list of {index: int, title: str, start: int, end: int}
        start/end 为字符位置
    """
    for enc in ("gbk", "gb18030", "utf-8", "utf-8-sig"):
        try:
            with open(novel_path, "r", encoding=enc) as f:
                text = f.read()
            # 验证编码
            if any("一" <= c <= "鿿" for c in text[:200]):
                break
        except (UnicodeDecodeError, FileNotFoundError):
            continue
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
            "start": start,
            "end": end,
        })

    return sections


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


# ---------------------------------------------------------------------------
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

