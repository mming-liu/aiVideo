# -*- coding: utf-8 -*-
"""测试语料的「内容无关性」单点定义（AGENTS.md §内容无关性与夹具）.

本模块只做两件事：

1. **定义什么算「具体作品词」**——`TERMS` 是唯一清单，构造器、门禁、棘轮都引用它，
   避免每个夹具各自维护一份越来越不一致的黑名单；
2. **提供可复用的断言**——`assert_clean()` 给新建夹具用，`count_terms()` 给棘轮用。

口径（与 `tests/step01_script_gen/test_content_agnostic_guard.py` 的 G05 一致）：
生产代码里的作品词由 G05 管（只扫 `.py` 的**代码常量**，docstring 里的历史复盘不算）。
本模块管的是**测试语料**：`tests/**` 下的 `.py` / `.json` 中出现的作品名、专属地名、
专属剧情短语——它们会让「换一本小说」时的回归失去意义（判据只对那一本书成立）。

历史真实事故的抓取件（`fixtures/chapter001_16th` 等）**允许**保留作品词：它们是独立
回归数据，用同一套机制读取，不是默认必需依赖。这些文件在
`fixtures/content_binding_baseline.json` 里逐条声明并写明理由，**只减不增**。
"""
from __future__ import annotations

import json
from pathlib import Path

#: 具体作品词：作品名、主要人物、专属地名、专属编号。
TERMS = (
    "锦衣夜行", "杨旭", "听香", "张十三", "肖管事",
    "照月湾", "杨府别庄", "77119", "杀青州",
)

#: 第一章专属**剧情短语**（换掉人名后仍是同一场戏）。只对提示面与夹具生效。
STORY_TERMS = (
    "夏浔", "纪纲", "冯西辉", "水下黑影", "半身探水", "倒纵入水",
    "刀尖微露", "刀尖朝下", "平定四方巾", "凶手是谁", "酒壶倾倒在船板",
)

ALL_TERMS = TERMS + STORY_TERMS

#: **声明黑名单本身的**文件：它们必须写出这些词，不构成「内容耦合」。
#: 只放精确路径（不用通配），避免拿一个同名文件把内容藏进去。
DECLARATION_FILES = frozenset({
    "tests/_content_binding.py",
    "tests/step01_script_gen/test_content_agnostic_guard.py",
    "tests/step01_script_gen/narrative_family_builder.py",
})


class ContentBindingError(AssertionError):
    """夹具里出现了具体作品内容。"""


def count_terms(text: str, terms=TERMS) -> int:
    return sum(text.count(t) for t in terms)


def assert_clean(text: str, label: str, terms=ALL_TERMS) -> None:
    hits = [t for t in terms if t in text]
    if hits:
        raise ContentBindingError("%s 含具体作品词 %s" % (label, hits))


def assert_obj_clean(obj, label: str, terms=ALL_TERMS) -> None:
    assert_clean(json.dumps(obj, ensure_ascii=False), label, terms)


def iter_corpus(root: Path):
    """遍历参与门禁的语料文件（`tests/**` 下的 .py/.json，跳过缓存、基线清单与声明文件）。"""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".json"):
            continue
        if "__pycache__" in path.parts:
            continue
        if path.name == "content_binding_baseline.json":
            continue
        try:
            rel = "tests/" + path.relative_to(root).as_posix()
        except ValueError:                                # pragma: no cover
            rel = path.as_posix()
        if rel in DECLARATION_FILES:
            continue
        yield path


def load_baseline(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc
