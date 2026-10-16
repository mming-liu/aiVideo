# -*- coding: utf-8 -*-
"""shared.screen_cards.render —— 本地**绘制**：透明 PNG 资产（Pillow）.

主专题 §6.1：全画布 RGBA PNG，复用现有 image 叠加；三类确定性模板；显式保留对白字幕区。
绘制**不碰正文**：只画 `layout` 已经切好的页（正文逐字来自原文，模板标题属样式）。

失败语义（§6.1）：缺字体、缺中文字形、布局溢出**明确失败**——不自动无限缩小字体。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .layout import LayoutError, load_font, layout_checked
from .profile import (PLACEMENT_UPPER, DisplayProfile, active_profile, slot_rect)

#: 三类模板（第一版固定）：人物介绍 / 时空说明 / 背景信息。
TEMPLATE_CHARACTER = "character"
TEMPLATE_TIME_PLACE = "time_place"
TEMPLATE_BACKGROUND = "background"
TEMPLATES = (TEMPLATE_CHARACTER, TEMPLATE_TIME_PLACE, TEMPLATE_BACKGROUND)

#: 模板标题只是**样式**（不计入事实正文，也不进阅读计数）。
TEMPLATE_TITLES = {
    TEMPLATE_CHARACTER: "人物",
    TEMPLATE_TIME_PLACE: "时地",
    TEMPLATE_BACKGROUND: "背景",
}


def _problem(code, reason, **extra):
    _p = {"code": code, "kind": "card", "path": "", "reason": reason, "detail": reason}
    _p.update(extra)
    return _p


@dataclass
class CardAsset:
    card_id: str
    page_index: int
    path: str
    sha256: str
    bytes: int
    text_start: int
    text_end: int

    def as_dict(self) -> dict:
        return {"card_id": self.card_id, "page_index": self.page_index,
                "path": self.path, "sha256": self.sha256, "bytes": self.bytes,
                "text_start": self.text_start, "text_end": self.text_end}


def _rounded(draw, box, radius, fill):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill)
    except AttributeError:                                             # 老版本 Pillow
        draw.rectangle(box, fill=fill)


def render_page(text: str, page, template: str, profile: DisplayProfile | None = None,
                placement: str = PLACEMENT_UPPER):
    """画**一页** → `PIL.Image`（RGBA，全画布）. `placement` 决定它占哪个卡位."""
    from PIL import Image, ImageDraw
    _p = profile or active_profile()
    if template not in TEMPLATES:
        raise LayoutError("未知卡片模板：%r（合法：%s）" % (template, list(TEMPLATES)))
    _font = load_font(_p, int(_p.font_size))
    _title_font = load_font(_p, max(int(_p.min_font_size), int(_p.font_size * 0.7)))

    _img = Image.new("RGBA", (int(_p.canvas[0]), int(_p.canvas[1])), (0, 0, 0, 0))
    _d = ImageDraw.Draw(_img)
    _x0, _y0, _x1, _y1 = slot_rect(_p, placement)
    _pad = 34
    _panel = (_x0 - _pad, _y0 - _pad - int(_p.font_size * 1.6),
              _x1 + _pad, min(_y1 + _pad, _p.subtitle_exclusion_rect[1] - 12))
    _rounded(_d, _panel, 28, (_p.bg[0], _p.bg[1], _p.bg[2], int(_p.panel_alpha)))
    # 左侧强调条（样式，不含信息）
    _d.rectangle((_panel[0], _panel[1] + 22, _panel[0] + 8, _panel[3] - 22), fill=_p.accent)
    # 模板标题（样式）
    _d.text((_panel[0] + 30, _panel[1] + 20), TEMPLATE_TITLES[template],
            font=_title_font, fill=_p.accent)
    _ty = _y0
    for _ln in page.lines:
        _d.text((_x0, _ty), text[_ln.start:_ln.end], font=_font, fill=_p.fg)
        _ty += int(float(_p.font_size) * float(_p.line_spacing))
    # **字幕区必须干净**（§6.1）：绘制完成后自检，越界即失败而不是"看起来还行"
    _sx0, _sy0, _sx1, _sy1 = _p.subtitle_exclusion_rect
    _alpha = _img.split()[3]
    _box = _alpha.crop((int(_sx0), int(_sy0), int(_sx1), int(_sy1))).getbbox()
    if _box is not None:
        raise LayoutError("卡片绘制侵入字幕排除区 %s——安全区/字号/行距与字幕区冲突"
                          % (_p.subtitle_exclusion_rect,))
    return _img


def render_card(text: str, template: str, card_id: str, out_dir, *,
                profile: DisplayProfile | None = None,
                placement: str = PLACEMENT_UPPER) -> tuple:
    """画**一张卡的全部页** → `(assets, problems)`；`problems` 非空时 `assets` 不可用.

    分页与绘制都失败即拒收——**不产出半成品资产**。
    """
    from pathlib import Path
    _p = profile or active_profile()
    # **预检失败一律走 (assets, problems) 这一个出口**：`load_font`（缺字体/缺中文字形）
    # 与 `slot_rect`（卡位非法）抛的是 `LayoutError`/`ValueError`，直接冒出去会让调用方
    # 拿到 traceback 而不是"哪里不合法"——本函数的契约就是**不抛**。
    try:
        _rect = slot_rect(_p, placement)
    except ValueError as exc:
        return [], [_problem("card_slot_invalid",
                             "卡位 %r 不可用：%s" % (placement, exc))]
    try:
        _pages, _probs = layout_checked(text, _p, rect=_rect)
    except LayoutError as exc:
        return [], [_problem("card_layout_overflow", str(exc))]
    if _probs:
        return [], _probs
    _out = Path(out_dir)
    try:
        _out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return [], [_problem("card_asset_dir_failed",
                             "卡片资产目录不可创建（%s）：%s" % (_out, exc))]
    _assets: list = []
    for _pg in _pages:
        try:
            _img = render_page(text, _pg, template, _p, placement)
        except LayoutError as exc:
            return [], [_problem("card_render_failed", str(exc))]
        _path = _out / ("%s_%s_p%02d.png" % (card_id, placement, _pg.index))
        try:
            _img.save(str(_path), "PNG")
        except OSError as exc:
            return [], [_problem("card_render_write_failed",
                                 "卡片资产写盘失败（%s）：%s" % (_path, exc))]
        _assets.append(CardAsset(card_id=str(card_id), page_index=int(_pg.index),
                                 path=str(_path),
                                 sha256=_sha256_file(_path),
                                 bytes=int(_path.stat().st_size),
                                 text_start=int(_pg.start), text_end=int(_pg.end)))
    return _assets, []


def _sha256_file(path) -> str:
    _h = hashlib.sha256()
    with open(path, "rb") as _f:
        for _c in iter(lambda: _f.read(1 << 16), b""):
            _h.update(_c)
    return _h.hexdigest()


__all__ = ["TEMPLATES", "TEMPLATE_CHARACTER", "TEMPLATE_TIME_PLACE",
           "TEMPLATE_BACKGROUND", "TEMPLATE_TITLES", "CardAsset",
           "render_page", "render_card"]
