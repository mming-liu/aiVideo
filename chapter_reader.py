"""兼容层：章节提取统一使用 shared/chapter_reader.py.
"""
from shared.chapter_reader import (  # noqa: F401
    extract_section, extract_chapter, find_sections, _filter_irrelevant_content,
)
