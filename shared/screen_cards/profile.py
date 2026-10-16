# -*- coding: utf-8 -*-
"""shared.screen_cards.profile —— **显示 profile**：字体、画布、字号、安全区、阅读预算.

主专题 §6.1 要求 profile **包含具体的字体文件/摘要、字号与最小字号、行距、卡片安全区、
字幕排除区、每页最大行数、每秒阅读字符数、入场静置秒数；缺值不得假装预算已验**。所以
这里的每一项都是**实测值或显式缺省结论**，没有"算了先跑"的占位。

阅读帧数的公式是**固定**的（§6.1）：

    ceil((非空白 Unicode 码点数 / reading_chars_per_sec + settle_sec) * MERGE_FPS)

标点计数、换行不计数。参数是**展示假设**，随资产清单一起记录；参数变化使预算/布局缓存
失效，但**不改变语义支持判据**（那是 `shared.content_verification` 的事）。

字体是**外部依赖**：找不到合适字体时 profile 自己带 `font_missing` 原因，渲染侧**明确失败**
（不自动无限缩小字号、不静默用西文字体画中文——那会画出一片方框还声称成功）。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace

from shared.media_contract import MERGE_FPS

#: profile 自身的版本（进资产身份；改样式参数就要改它）。
PROFILE_VERSION = "screen_card_profile.v1"

#: 画布：与合并成片同尺寸。生产值由 `shared/paths`/配置给，这里给**默认画布**。
DEFAULT_CANVAS = (1080, 1920)

#: 候选字体（按序找第一个存在的）。路径是**运行环境的输入**，不是产品语义——
#: 找不到就明确失败，不猜、不降级。
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",          # 微软雅黑（Windows 默认中文）
    r"C:\Windows\Fonts\simhei.ttf",        # 黑体
    r"C:\Windows\Fonts\simsun.ttc",        # 宋体
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

#: 不参与阅读计数的字符：空白与标点（§6.1「标点计数、换行不计数」）。
_PUNCT = set(
    " \t\r\n\u3000"
    "，。、；：？！“”‘’（）【】《》〈〉「」『』…—～·,"
    ".!?;:\"'()[]{}<>-—/|\\`~@#$%^&*_+="
)


#: 第一版的**卡位**（同镜并显时每张卡占一个卡位；两张卡同卡位同时可见 ⇒ 拒收）.
PLACEMENT_UPPER = "info_panel"
PLACEMENT_LOWER = "info_panel_lower"
PLACEMENTS = (PLACEMENT_UPPER, PLACEMENT_LOWER)


def slot_rect(profile, placement: str) -> tuple:
    """卡位 → 该卡可用的矩形 `(x0, y0, x1, y1)`.

    卡位把"安全区上沿 → 字幕排除区上沿"这条带子**等分**：`info_panel` 占上半、
    `info_panel_lower` 占下半。两个卡位都整体位于字幕排除区之上——字幕区永远是干净的。
    """
    if placement not in PLACEMENTS:
        raise ValueError("未知卡位：%r（合法：%s）" % (placement, list(PLACEMENTS)))
    _x0, _y0, _x1, _y1 = profile.safe_rect
    _bottom = min(int(_y1), int(profile.subtitle_exclusion_rect[1]) - 24)
    _top = int(_y0)
    if _bottom - _top < 2:
        raise ValueError("安全区到字幕区之间放不下卡位（%d..%d）" % (_top, _bottom))
    _mid = _top + (_bottom - _top) // 2
    if placement == PLACEMENT_UPPER:
        return (int(_x0), _top, int(_x1), _mid - 8)
    return (int(_x0), _mid + 8, int(_x1), _bottom)


def countable_chars(text) -> int:
    """**阅读计数**用的码点数：**非空白字符，标点计入**（工作包 3 第 4 条）.

    ⚠️ 复核 2026-10-05 P2：原先这里把标点**排除**在外，于是「一句 20 字带 5 个标点」
    只按 15 字计阅读时间，实际读者要过的就是那 5 个标点——预算被系统性低估约 15~25%，
    真到成片上就是"来不及读"。既定口径是**非空白字符**（标点计数、换行不计数），
    换行属于布局（§6.1「换行只改布局」），标点不属于。
    """
    return sum(1 for _c in str(text or "") if not _c.isspace())


@dataclass(frozen=True)
class DisplayProfile:
    """一版**冻结**的显示参数（进资产身份；改一个字段就是新一版）."""

    version: str = PROFILE_VERSION
    canvas: tuple = DEFAULT_CANVAS
    font_path: str = ""
    font_sha256: str = ""
    font_missing: str = ""
    font_size: int = 46
    min_font_size: int = 34
    line_spacing: float = 1.45
    #: 卡片安全区（画布内边距后的可用矩形）：`(x0, y0, x1, y1)`
    safe_rect: tuple = (90, 240, 990, 1480)
    #: **字幕排除区**：卡片不得侵入（§6.1「显式保留对白字幕区」）
    subtitle_exclusion_rect: tuple = (60, 1500, 1020, 1800)
    max_lines_per_page: int = 6
    #: 每秒阅读字符数（展示假设）
    reading_chars_per_sec: float = 6.0
    #: 入场静置秒数
    settle_sec: float = 0.6
    #: **展示预算**：整章文字卡合计允许占用的阅读秒数（独立于 TTS 估时与模型能力常量）
    reading_budget_sec: float = 30.0
    fg: tuple = (255, 255, 255, 255)
    bg: tuple = (12, 14, 18, 255)
    accent: tuple = (214, 176, 96, 255)
    panel_alpha: int = 188
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        _d = {k: (list(v) if isinstance(v, tuple) else v)
              for k, v in self.__dict__.items()}
        return _d

    def digest(self) -> str:
        import json
        _t = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), default=str)
        return hashlib.sha256(_t.encode("utf-8")).hexdigest()


def find_font() -> str:
    """按 `SCREEN_CARD_FONT` → 候选表找字体；找不到返回空串（**由调用方明确失败**）."""
    _env = (os.getenv("SCREEN_CARD_FONT") or "").strip()
    if _env:
        return _env if os.path.isfile(_env) else ""
    for _p in _FONT_CANDIDATES:
        if os.path.isfile(_p):
            return _p
    return ""


def sha256_file(path) -> str:
    try:
        _h = hashlib.sha256()
        with open(path, "rb") as _f:
            for _chunk in iter(lambda: _f.read(1 << 16), b""):
                _h.update(_chunk)
        return _h.hexdigest()
    except OSError:
        return ""


def _float_env(name, default):
    _v = (os.getenv(name) or "").strip()
    if not _v:
        return float(default)
    try:
        return float(_v)
    except ValueError:
        return float(default)


_ACTIVE: DisplayProfile | None = None


def active_profile(*, refresh: bool = False) -> DisplayProfile:
    """生效的显示 profile（进程内缓存一份；`refresh=True` 重读环境）.

    **缺字体不抛异常**：`font_missing` 记录原因，渲染/布局侧据此明确失败。这样"计划层
    的阅读预算"仍可用（它只数码点），而"真的能不能画出来"由资产侧给出结论。
    """
    global _ACTIVE
    if _ACTIVE is not None and not refresh:
        return _ACTIVE
    _font = find_font()
    _p = DisplayProfile(
        font_path=_font,
        font_sha256=(sha256_file(_font) if _font else ""),
        font_missing=("" if _font else
                      "找不到中文字体（设 SCREEN_CARD_FONT 指定，或安装候选字体之一）"),
        reading_chars_per_sec=_float_env("SCREEN_CARD_READING_CHARS_PER_SEC", 6.0),
        settle_sec=_float_env("SCREEN_CARD_SETTLE_SEC", 0.6),
        reading_budget_sec=_float_env("SCREEN_CARD_READING_BUDGET_SEC", 30.0),
    )
    _ACTIVE = _p
    return _p


def reading_frames(n_chars: int, profile: DisplayProfile | None = None) -> int:
    """阅读**帧数**（§6.1 的固定公式）；至少 1 帧."""
    _p = profile or active_profile()
    import math
    _sec = (int(n_chars) / float(_p.reading_chars_per_sec)) + float(_p.settle_sec)
    return max(1, int(math.ceil(_sec * MERGE_FPS)))


def reading_seconds(n_chars: int, profile: DisplayProfile | None = None) -> float:
    return reading_frames(n_chars, profile) / float(MERGE_FPS)


def profile_manifest(profile: DisplayProfile | None = None) -> dict:
    """清单里记的那一份（含生效值与字体身份）——**缺字体要说出来**."""
    _p = profile or active_profile()
    _m = {"version": _p.version, "canvas": list(_p.canvas),
          "font_path": _p.font_path, "font_sha256": _p.font_sha256,
          "font_size": _p.font_size, "min_font_size": _p.min_font_size,
          "line_spacing": _p.line_spacing, "safe_rect": list(_p.safe_rect),
          "subtitle_exclusion_rect": list(_p.subtitle_exclusion_rect),
          "max_lines_per_page": _p.max_lines_per_page,
          "reading_chars_per_sec": _p.reading_chars_per_sec,
          "settle_sec": _p.settle_sec, "reading_budget_sec": _p.reading_budget_sec,
          "fps": MERGE_FPS, "profile_digest": _p.digest()}
    if _p.font_missing:
        _m["font_missing"] = _p.font_missing
    return _m


def with_overrides(**kw) -> DisplayProfile:
    """派生一份改了若干字段的 profile（测试与本地预览用；不改全局）."""
    return replace(active_profile(), **kw)


__all__ = ["PROFILE_VERSION", "DEFAULT_CANVAS", "PLACEMENTS", "PLACEMENT_UPPER",
           "PLACEMENT_LOWER", "slot_rect",
           "DisplayProfile", "active_profile",
           "with_overrides", "find_font", "sha256_file", "countable_chars",
           "reading_frames", "reading_seconds", "profile_manifest"]
