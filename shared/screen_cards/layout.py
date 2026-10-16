# -*- coding: utf-8 -*-
"""shared.screen_cards.layout —— **布局预检**：测量实际字体、分页、溢出判定.

主专题 §6.1 的硬要求，逐条落实：

- **先预检、后渲染**：测量用的是**真实字体文件**（Pillow `ImageFont.truetype`），不是
  按字符数估的行宽；
- **按源码正文的连续范围分页**，每页保存 `[text_start, text_end)`（**Unicode 码点**偏移）；
- **按序拼回必须等于正文**——这是分页正确性的唯一判据，不靠"看起来差不多"；
- **页码偏移用于身份**；排版不得拆开字形组合（按码点切，不按字节）；
- 分页**优先在句末边界**（`。！？…`）落刀，避免把一个人名/一个身份拆到两页；
  一句话本身装不下一页时才按行容量落刀，并在页记录里标 `mid_sentence=True`；
- **空间预算**（能不能放进矩形）与**阅读预算**（读不读得完）**分别判**——
  能放进矩形不等于读得完；
- 无法正常分页返回 `layout_overflow`，**不自动无限缩小字体**。

本模块**只测量与排版**，不画图、不写盘。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shared.media_contract import MERGE_FPS

from .profile import (DisplayProfile, active_profile, countable_chars,
                      reading_frames)

#: 句末边界：分页优先在这些字符**之后**落刀。
SENTENCE_END = "。！？…；!?;"
#: 行内可断点：这些字符之后可以换行（避免把标点甩到下一行行首）。
_BREAK_AFTER = SENTENCE_END + "，、：,:）】》」』”’"


def _problem(code, reason, path="", **extra):
    _p = {"code": code, "kind": "card", "path": path, "reason": reason,
          "detail": reason}
    _p.update(extra)
    return _p


def load_font(profile: DisplayProfile, size: int):
    """按 profile 载入字体；**缺字体/缺字形明确失败**（不静默用西文字体画中文）."""
    from PIL import ImageFont
    if profile.font_missing or not profile.font_path:
        raise LayoutError(profile.font_missing or "profile 未指定字体文件")
    try:
        _f = ImageFont.truetype(profile.font_path, size)
    except OSError as exc:
        raise LayoutError("字体载入失败（%s）：%s" % (profile.font_path, exc)) from exc
    # **中文字形自检**：没有汉字的字体画出来是一片方框，必须在预检阶段就失败
    try:
        _missing = [c for c in "汉字测试一二三" if _f.getmask(c).getbbox() is None]
    except Exception:                                                  # noqa: BLE001
        _missing = []
    if len(_missing) == 7:
        raise LayoutError("字体 %s 没有中文字形——换一个带中文的字体"
                          % profile.font_path)
    return _f


class LayoutError(ValueError):
    """布局不可满足（缺字体、放不下、拼不回原文）——调用方按拒收处理."""


@dataclass
class Line:
    start: int
    end: int
    width: float = 0.0

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "width": round(self.width, 2)}


@dataclass
class Page:
    """一页：源码正文的**半开码点区间** `[start, end)` + 版式结果."""
    index: int
    start: int
    end: int
    lines: list = field(default_factory=list)
    mid_sentence: bool = False
    chars: int = 0
    frames: int = 0

    def as_dict(self) -> dict:
        return {"index": self.index, "text_start": self.start, "text_end": self.end,
                "lines": [x.as_dict() for x in self.lines],
                "mid_sentence": bool(self.mid_sentence),
                "countable_chars": self.chars, "reading_frames": self.frames}


def _line_width(font, text) -> float:
    try:
        return float(font.getlength(text))
    except AttributeError:                                             # 老版本 Pillow
        return float(font.getsize(text)[0])


def wrap_lines(text: str, font, max_width: float, *, start: int = 0) -> list:
    """把 `text` 折成多行，返回**码点区间**（相对整段正文）.

    逐字符推进（中文没有词边界），遇到行宽超限就在**最近的允许断点**回退；
    一行里没有任何允许断点时才硬断（这是排版，不改正文——拼回去仍逐字等于原文）。
    """
    _lines: list = []
    _n = len(text)
    _i = 0
    while _i < _n:
        _j = _i
        _last_ok = -1
        _w = 0.0
        while _j < _n:
            _w = _line_width(font, text[_i:_j + 1])
            if _w > max_width:
                break
            if text[_j] in _BREAK_AFTER or _j == _n - 1:
                _last_ok = _j
            _j += 1
        if _j >= _n:                       # 剩下的全部装得下
            _lines.append(Line(start + _i, start + _n,
                               _line_width(font, text[_i:_n])))
            break
        _cut = (_last_ok + 1) if _last_ok >= _i else _j
        if _cut <= _i:                     # 单字都超宽 ⇒ 布局不可满足
            raise LayoutError("单个字符宽度超过可用行宽（%.1fpx）——字号或安全区不匹配"
                              % max_width)
        _lines.append(Line(start + _i, start + _cut,
                           _line_width(font, text[_i:_cut])))
        _i = _cut
    return _lines


def _next_sentence_end(text: str, lo: int, hi: int) -> int:
    """`[lo, hi]` 内最后一个句末边界之后的位置（没有则 -1）."""
    for _k in range(hi, lo - 1, -1):
        if _k > 0 and text[_k - 1] in SENTENCE_END:
            return _k
    return -1


def paginate(text: str, profile: DisplayProfile | None = None, *, rect=None) -> tuple:
    """分页 → `(pages, problems)`；`problems` 非空时页表**不可用**（拒收）.

    `rect`：本卡**卡位**的可用矩形；不给就用 `safe_rect`（单卡场景）。

    先按**最小字号**（`min_font_size`）试排：连最小字号都排不下，才是 `layout_overflow`；
    排得下就用 profile 的字号排（正常情况）。
    """
    _p = profile or active_profile()
    _probs: list = []
    _text = str(text or "")
    if not _text.strip():
        return [], [_problem("card_layout_empty", "卡片正文为空——空卡不生成资产")]
    _x0, _y0, _x1, _y1 = (rect if rect is not None else _p.safe_rect)
    _max_w = float(_x1 - _x0)
    _line_h = float(_p.font_size) * float(_p.line_spacing)
    _max_lines = int((_y1 - _y0) // int(_line_h)) if _line_h > 0 else 0
    _max_lines = min(_max_lines, int(_p.max_lines_per_page))
    if _max_lines < 1:
        return [], [_problem("card_layout_overflow",
                             "安全区高度放不下一行（安全区 %.0fpx / 行高 %.1fpx）"
                             % (float(_y1 - _y0), _line_h))]

    # ① 先用**最小字号**证明"这版 profile 排得出来"：字号再小也排不出，就是空间不可满足
    _min_font = load_font(_p, int(_p.min_font_size))
    try:
        wrap_lines(_text, _min_font, _max_w)
    except LayoutError as exc:
        return [], [_problem("card_layout_overflow",
                             "按最小字号 %dpx 仍排不下：%s" % (_p.min_font_size, exc))]

    _font = load_font(_p, int(_p.font_size))
    try:
        _lines = wrap_lines(_text, _font, _max_w)
    except LayoutError as exc:
        return [], [_problem("card_layout_overflow", str(exc))]

    _pages: list = []
    # ⚠️ **游标是码点，不是行下标**（2026-10-06 复核第十二轮 ⑤ 的样片暴露的缺陷）：
    # 落刀点优先取句末（`_sb`），而句末**可能落在某一行中间**。旧写法下一页从
    # `_lines[_li + len(_chunk)].start`（**行边界**）续排，于是 `[最后一个保住的行尾, _sb)`
    # 这段正文**被两页各算一次**——拼回比原文长，`card_layout_not_reassemblable`，
    # 整张卡（进而整条卡片链路）失败。行数超过 `max_lines_per_page` 的**每一张**卡都中招。
    # 现在每一轮**从游标重新折行**，`_hi` 就是下一页的 `_pos`：区间首尾相接、不重不漏。
    _pos = 0
    while _pos < len(_text):
        # `wrap_lines` 的 `start` 是**切片在正文里的偏移**，所以传的是切片本体：
        # 传整段 + `start` 会把偏移算两遍（行尾越出正文长度）。
        _lines = wrap_lines(_text[_pos:], _font, _max_w, start=_pos)
        _cap = min(_max_lines, len(_lines))
        _hi = _lines[_cap - 1].end
        _mid = False
        if _cap < len(_lines):
            # 还有下一行 ⇒ 这一页要在**允许的边界**落刀：优先句末
            _sb = _next_sentence_end(_text, _pos, _hi)
            if _sb > _pos:
                _hi = _sb          # 落在句末：可能在某一行中间，下一页就从这里续排
            else:
                _mid = True        # 页内没有句末边界 ⇒ 切在行边界上，如实标
        _chunk = [x for x in _lines[:_cap] if x.end <= _hi]
        if not _chunk:
            # 句末边界落在**第一行中间** ⇒ 那一刀只能切在行边界上，
            # 于是这一页**确实**切在句子中间（`mid_sentence=True`，如实标）。
            _chunk = _lines[:1]
            _hi = _chunk[-1].end
            _mid = True
        _seg = _text[_pos:_hi]
        _pages.append(Page(index=len(_pages), start=_pos, end=_hi, lines=_chunk,
                           mid_sentence=_mid, chars=countable_chars(_seg),
                           frames=reading_frames(countable_chars(_seg), _p)))
        _pos = _hi

    # ② **拼回必须逐字等于正文**（分页正确性的唯一判据）
    if "".join(_text[x.start:x.end] for x in _pages) != _text:
        return [], [_problem("card_layout_not_reassemblable",
                             "分页结果按序拼回不等于原文——分页只许切分、不许改动正文")]
    return _pages, _probs


def layout_checked(text: str, profile: DisplayProfile | None = None, *,
                   rect=None) -> tuple:
    """**空间预算 + 阅读预算分别判** → `(pages, problems)`.

    - 空间：`paginate` 的溢出/拼不回。

    ⚠️ **本函数不核展示预算**：`reading_budget_sec` 是**整章所有卡合计**的预算，在
    单卡这一层核它等于拿章级预算当卡级判据（一张 40s 的卡会被说"超预算"，而全章只有
    这一张卡、根本没问题）。合计那一道在**知道全部卡**的地方判——计划层
    （`plan_feasibility.presentation_records`）与排布层（`screen_cards.plan.build_card_plan`），
    两处读同一个 `profile.reading_budget_sec`。单卡只交自己的**阅读秒数**。
    """
    _p = profile or active_profile()
    return paginate(text, _p, rect=rect)


def reading_seconds_for(text: str, profile: DisplayProfile | None = None, *,
                        rect=None) -> float:
    """这张卡（或这几页）需要的**阅读秒数**（不可见间隙不算，只计实际可见帧）."""
    _p = profile or active_profile()
    _pages, _probs = paginate(text, _p, rect=rect)
    if _probs:
        return -1.0
    return sum(int(x.frames) for x in _pages) / float(MERGE_FPS)


__all__ = ["LayoutError", "Line", "Page", "wrap_lines", "paginate", "layout_checked",
           "reading_seconds_for", "load_font", "SENTENCE_END"]
