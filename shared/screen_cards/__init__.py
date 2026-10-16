# -*- coding: utf-8 -*-
"""shared.screen_cards —— 画面文字卡：**绘制、布局、清单、叠加**的单一责任包.

主专题 `docs/aiVideo-screen-hybrid-plan-2026-10-04.md` §6。

| 子模块 | 责任 |
|---|---|
| `profile` | 显示 profile：字体/摘要、画布、字号、安全区、字幕排除区、阅读参数、卡位 |
| `layout` | 布局**预检**：真实字体测量、按码点分页、拼回判定、空间/阅读预算分别判 |
| `render` | 本地绘制 RGBA PNG（Pillow）；字幕排除区自检；缺字体明确失败 |
| `plan` | `screen_card_plan.v1` 清单、可用窗口（排除淡入淡出/转场重叠）、全时间线分段合成、叠加条目导出 |

对外只暴露这三件事（§6.1）：**检查某画布/时窗能否展示**、**渲染资产并生成摘要清单**、
**转换为现有 OverlayEntry**。包装不下时不自动缩字号、不截字、不慢放。
"""
from __future__ import annotations

from . import layout, plan, profile, render                           # noqa: F401

__all__ = ["profile", "layout", "render", "plan"]
