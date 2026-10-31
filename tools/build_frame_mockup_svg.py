# -*- coding: utf-8 -*-
"""build_frame_mockup_svg - 把帧计划画成 SVG 示意图（0 次模型调用）.

在真正生图之前，用**结构化示意图**核对"这一批帧要拍什么"：几个人、谁在前谁在后、
什么姿态、手里拿什么、首尾状态、位置是不是计划里明确写了。它不是模型输出，也不
伪装成效果图——画面上的人形是**按姿态关键字画的占位剪影**，只能用来核对构图关系，
不能用来判断脸、画风、光影和演技。

设计口径（对应 2026-10-31 的评阅意见）：

- **场景清单独立于镜头选择**：默认取整份 script 的全部场景（含 hook），不因为这次
  只画 shot 8/13 就只剩一个场景；
- **姿态与道具进入画面**：仰卧、跪坐、踞蹲、俯身、侍立各自画出不同剪影，短匕、钓杆、
  酒壶、灯笼、伞等按关键字画成小图形，不再只写在标签里；
- **位置必须标明来源**：`frame_layouts` 里写了画左/画右就按它；没写但帧描述里写了
  "跪坐画左"就按描述并在标签注明"（描述）"；两者都没有就画**虚线剪影**并标
  "位置未指定"，不冒充计划里的构图；
- **每镜一页**，图注放大到可读字号，不再挤在一张长图底部；
- 角色颜色按 `characters` 顺序固定分配，同一人跨页同色，不同人不同色。

用法::

    python tools/build_frame_mockup_svg.py \
        --script output/<小说>/part_01_<部>/chapter_001/script_result.json \
        --out-dir output/<...>/_production/preview/batch1 --shots 8,13

产物：`01_assets.svg`（角色 + 场景）、`02_shot008.svg`… 每镜一页、`index.html` 索引。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]

INK = "#0a1018"
NIGHT_TOP = "#111c2e"
NIGHT_BOT = "#060a12"
MOON = "#a9d6f5"
LAMP = "#ffb45c"
PAPER = "#e9eef6"
MUTED = "#8ea0b8"
BODY = "#93a7c0"

FONT = "Microsoft YaHei, Noto Sans CJK SC, Source Han Sans SC, sans-serif"
#: 角色配色：按 characters 顺序分配，保证杨旭与听香不会撞色
CHAR_COLORS = ("#7fb2e5", "#ef9fb6", "#9fd8a0", "#c9a7e5", "#e5c98a", "#8ad4cd")

_INVALID_XML = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd]")


def esc(text) -> str:
    return escape(_INVALID_XML.sub("", str(text or "")))


def text_width(text: str, size: float) -> float:
    return sum(size if ord(ch) > 0x2E7F else size * 0.55 for ch in str(text or ""))


def wrap_cn(text: str, size: float, max_width: float, max_lines: int = 0) -> list:
    lines: list = []
    for para in str(text or "").replace("\r", "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            if not cur or text_width(cur + ch, size) <= max_width:
                cur += ch
                continue
            lines.append(cur)
            cur = ch
        if cur:
            lines.append(cur)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and text_width(tail + "…", size) > max_width:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    return lines


class Sheet:
    def __init__(self, width: int = 1600, title: str = "", subtitle: str = ""):
        self.width = width
        self.parts: list = []
        self.y = 0.0
        self.title = title
        self.subtitle = subtitle

    def add(self, raw: str) -> None:
        self.parts.append(raw)

    def text(self, x, y, s, *, size=14, fill=PAPER, weight="normal", anchor="start",
             opacity=1.0) -> None:
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
                 f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}" '
                 f'opacity="{opacity}">{esc(s)}</text>')

    def rect(self, x, y, w, h, **kw) -> None:
        attrs = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in kw.items())
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" {attrs}/>')

    def render(self) -> str:
        h = int(self.y + 46)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{h}" '
            f'viewBox="0 0 {self.width} {h}" font-family="{FONT}">'
            f'<defs>'
            f'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{NIGHT_TOP}"/><stop offset="100%" stop-color="{NIGHT_BOT}"/>'
            f'</linearGradient>'
            f'<linearGradient id="page" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="#0d1522"/><stop offset="100%" stop-color="#070b12"/>'
            f'</linearGradient>'
            f'<radialGradient id="glow" cx="50%" cy="30%" r="62%">'
            f'<stop offset="0%" stop-color="{MOON}" stop-opacity="0.18"/>'
            f'<stop offset="100%" stop-color="{MOON}" stop-opacity="0"/>'
            f'</radialGradient>'
            f'</defs>'
            f'<rect x="0" y="0" width="100%" height="100%" fill="url(#page)"/>'
            + "".join(self.parts) + "</svg>"
        )


# ---------------------------------------------------------------------------
# 姿态 / 道具
# ---------------------------------------------------------------------------
_POSTURES = (
    ("lying", ("仰卧", "平躺", "躺卧", "横卧", "卧于", "伏卧", "倒卧")),
    ("kneel", ("跪坐", "跪", "踞坐")),
    ("crouch", ("踞蹲", "蹲", "踞于", "踞在", "踞")),
    ("sit", ("盘坐", "斜倚", "倚坐", "坐")),
    ("lean", ("俯身", "倾身", "前倾", "俯下", "凑近", "探身", "俯")),
)


def classify_pose(text: str) -> str:
    for name, keys in _POSTURES:
        if any(k in str(text or "") for k in keys):
            return name
    return "stand"


def _body_stand(color: str, dashed: bool) -> str:
    stroke = f' stroke="{color}" stroke-width="2" stroke-dasharray="6 5"' if dashed else ""
    fill = f'{color}' if not dashed else "none"
    return (
        f'<circle cx="0" cy="-96" r="12" fill="{fill}"{stroke}/>'
        f'<path d="M-19,-82 Q0,-98 19,-82 L23,-34 L-23,-34 Z" fill="{fill}"{stroke}/>'
        f'<path d="M-11,-34 L-14,0 M11,-34 L14,0" stroke="{color}" stroke-width="6" '
        f'stroke-linecap="round" opacity="{0.45 if dashed else 0.95}"/>'
    )


def human(pose: str, color: str, *, dashed=False) -> str:
    """按姿态画的占位剪影（原点在脚下，身高约 110）."""
    base = _body_stand(color, dashed)
    if pose == "lying":
        return f'<g transform="translate(0,-26) rotate(-76)">{base}</g>'
    if pose == "kneel":
        return (
            f'<g transform="translate(0,-16)">'
            f'<circle cx="0" cy="-62" r="11" fill="{color}"/>'
            f'<path d="M-17,-50 Q0,-64 17,-50 L20,-14 L-20,-14 Z" fill="{color}"/>'
            f'<ellipse cx="0" cy="-8" rx="26" ry="8" fill="{color}" opacity="0.75"/>'
            f'</g>'
        )
    if pose == "sit":
        return (
            f'<g transform="translate(0,-18)">'
            f'<circle cx="0" cy="-68" r="11" fill="{color}"/>'
            f'<path d="M-17,-56 Q0,-70 17,-56 L19,-20 L-19,-20 Z" fill="{color}"/>'
            f'<path d="M-19,-18 L26,-6" stroke="{color}" stroke-width="7" stroke-linecap="round"/>'
            f'</g>'
        )
    if pose == "crouch":
        return (
            f'<g transform="translate(0,-30)">'
            f'<circle cx="0" cy="-58" r="11" fill="{color}"/>'
            f'<path d="M-18,-46 Q0,-60 18,-46 L20,-12 L-20,-12 Z" fill="{color}"/>'
            f'<path d="M-14,-12 L-26,4 M14,-12 L26,4" stroke="{color}" stroke-width="6" '
            f'stroke-linecap="round"/>'
            f'</g>'
        )
    if pose == "lean":
        return f'<g transform="rotate(16)">{base}</g>'
    return base


_PROP_RULES = (
    ("dagger", ("匕首", "短匕", "刀", "剑")),
    ("rod", ("钓杆", "鱼杆", "竿", "箫")),
    ("pot", ("酒壶", "酒囊", "壶")),
    ("lantern", ("灯笼", "灯")),
    ("umbrella", ("伞",)),
    ("fork", ("叉", "铲")),
    ("horse", ("马", "车")),
)


def prop_kind(name: str) -> str:
    for kind, keys in _PROP_RULES:
        if any(k in str(name or "") for k in keys):
            return kind
    return "other"


def prop_svg(kind: str, x: float, y: float, color: str, label: str = "") -> str:
    """在 (x,y) 处画一个小道具图形；认不出的画成方块并标字."""
    if kind == "dagger":
        return (f'<g transform="translate({x:.1f},{y:.1f}) rotate(-24)">'
                f'<rect x="-2" y="-16" width="4" height="18" fill="{color}"/>'
                f'<path d="M-4,-16 L0,-30 L4,-16 Z" fill="{color}"/></g>')
    if kind == "rod":
        return (f'<g transform="translate({x:.1f},{y:.1f}) rotate(28)">'
                f'<rect x="-1.5" y="-58" width="3" height="58" fill="{color}" opacity="0.9"/></g>')
    if kind == "pot":
        return (f'<g transform="translate({x:.1f},{y:.1f})">'
                f'<rect x="-9" y="-14" width="18" height="14" rx="4" fill="{color}"/>'
                f'<rect x="-3" y="-19" width="6" height="6" fill="{color}"/></g>')
    if kind == "lantern":
        return (f'<g transform="translate({x:.1f},{y:.1f})">'
                f'<circle cx="0" cy="0" r="9" fill="{LAMP}" opacity="0.85"/>'
                f'<path d="M0,-9 L0,-16" stroke="{color}" stroke-width="2"/></g>')
    if kind == "umbrella":
        return (f'<g transform="translate({x:.1f},{y:.1f})">'
                f'<path d="M-20,-6 Q0,-24 20,-6 Z" fill="{color}" opacity="0.85"/>'
                f'<path d="M0,-6 L0,16" stroke="{color}" stroke-width="2"/></g>')
    if kind == "fork":
        return (f'<g transform="translate({x:.1f},{y:.1f})">'
                f'<path d="M0,14 L0,-12 M-6,-12 L-6,-20 M0,-12 L0,-21 M6,-12 L6,-20" '
                f'stroke="{color}" stroke-width="2.4"/></g>')
    if kind == "horse":
        return (f'<g transform="translate({x:.1f},{y:.1f})">'
                f'<rect x="-16" y="-10" width="32" height="12" rx="3" fill="{color}"/>'
                f'<circle cx="-9" cy="6" r="5" fill="none" stroke="{color}" stroke-width="2"/>'
                f'<circle cx="9" cy="6" r="5" fill="none" stroke="{color}" stroke-width="2"/></g>')
    ch = (str(label or "？")[:1]) or "？"
    return (f'<g transform="translate({x:.1f},{y:.1f})">'
            f'<rect x="-7" y="-7" width="14" height="14" rx="3" fill="none" '
            f'stroke="{color}" stroke-width="1.4"/>'
            f'<text x="0" y="4.5" font-size="10" fill="{color}" text-anchor="middle" '
            f'font-family="{FONT}">{esc(ch)}</text></g>')


# ---------------------------------------------------------------------------
# 位置：layout > 描述 > 未指定
# ---------------------------------------------------------------------------
_DEPTH = {"前景": (1.00, 0.86), "中景": (0.82, 0.80), "远景": (0.55, 0.70),
          "画右远景": (0.55, 0.70), "画左远景": (0.55, 0.70),
          "画右水下": (0.72, 0.96), "画左水下": (0.72, 0.96), "水下": (0.72, 0.96)}

_HINT = (("画左", 0.27), ("左侧", 0.27), ("左边", 0.30),
         ("画右", 0.73), ("右侧", 0.73), ("右边", 0.70),
         ("画中", 0.50), ("居中", 0.50))


def resolve_position(item: dict, desc: str) -> tuple:
    """返回 (cx, cy, scale, side_source, side_label)。side_source ∈ layout/desc/none."""
    pos = str(item.get("position") or "")
    scale, cy = 0.82, 0.80
    for key, value in _DEPTH.items():
        if key in pos:
            scale, cy = value
            break
    for key, cx in _HINT:
        if key in pos:
            return cx, cy, scale, "layout", key
    name = str(item.get("name") or "")
    if name and desc:
        window = ""
        idx = desc.find(name)
        while idx >= 0:
            window += desc[max(0, idx - 10):idx] + "|" + desc[idx + len(name):idx + len(name) + 12] + "|"
            idx = desc.find(name, idx + 1)
        for key, cx in _HINT:
            if key in window:
                return cx, cy, scale, "desc", key
    return None, cy, scale, "none", ""


_DEFAULT_CX = (0.36, 0.64, 0.50, 0.78, 0.22)


def scene_panel(sheet: Sheet, x, y, w, h, *, scene_name, index_text, warm=False):
    sheet.rect(x, y, w, h, rx="12", fill="url(#sky)", stroke="#22314a", stroke_width="1.4")
    sheet.rect(x, y, w, h, rx="12", fill="url(#glow)")
    horizon = y + h * 0.62
    sheet.add(f'<path d="M{x:.1f},{horizon:.1f} L{x + w:.1f},{horizon:.1f}" '
              f'stroke="{MOON}" stroke-opacity="0.32" stroke-width="1"/>')
    seed = sum(ord(c) for c in str(scene_name)) % 7
    for i in range(5):
        lx = x + w * (0.12 + 0.18 * i) + seed
        ly = horizon + h * (0.05 + 0.045 * ((i + seed) % 3))
        sheet.add(f'<ellipse cx="{lx:.1f}" cy="{ly:.1f}" rx="{w * 0.07:.1f}" '
                  f'ry="{h * 0.02:.1f}" fill="#1d3a2f" fill-opacity="0.85"/>')
    if warm:
        sheet.add(f'<circle cx="{x + w * 0.80:.1f}" cy="{y + h * 0.44:.1f}" r="{w * 0.11:.1f}" '
                  f'fill="{LAMP}" fill-opacity="0.18"/>')
    sheet.text(x + w - 14, y + 26, index_text, size=15, fill=MUTED, anchor="end", weight="bold")


def draw_layout(sheet: Sheet, x, y, w, h, layouts, desc, colors, *, boat=True) -> None:
    visible = [it for it in (layouts or [])
               if isinstance(it, dict) and it.get("on_screen", True)]
    for i, item in enumerate(visible):
        name = str(item.get("name") or "?")
        cx, cy, scale, source, hint = resolve_position(item, desc)
        dashed = cx is None
        if dashed:
            cx = _DEFAULT_CX[i % len(_DEFAULT_CX)]
        color = colors.get(name, BODY)
        px, py = x + w * cx, y + h * cy
        pose = classify_pose(item.get("pose") or "")
        if pose == "stand":
            pose = classify_pose(desc if name in desc else "")
        sheet.add(f'<g transform="translate({px:.1f},{py:.1f}) scale({scale:.2f})">'
                  f'{human(pose, color, dashed=dashed)}</g>')
        for k, prop in enumerate([p for p in (item.get("props") or []) if p][:3]):
            ax = px + (18 + 4 * k) * scale
            ay = py - (34 + 10 * k) * scale
            sheet.add(prop_svg(prop_kind(prop), ax, ay, LAMP, prop))
        # 标签：名字 + 位置来源 + 姿态 + 道具
        label_y = py + 22
        sheet.text(px, label_y, name, size=15, fill=color, anchor="middle", weight="bold")
        side = {"layout": "位置：%s" % hint,
                "desc": "位置：%s（描述）" % hint,
                "none": "位置未指定"}[source]
        sheet.text(px, label_y + 18, side, size=12,
                   fill=(MUTED if source != "none" else LAMP), anchor="middle")
        pose_text = str(item.get("pose") or "")
        if pose_text:
            sheet.text(px, label_y + 34, pose_text, size=12, fill=MUTED, anchor="middle")
        props = [p for p in (item.get("props") or []) if p]
        if props:
            sheet.text(px, label_y + 50, "／".join(props), size=12, fill=LAMP, anchor="middle")
    if boat:
        hull_y = y + h * 0.93
        sheet.add(f'<path d="M{x + w * 0.16:.1f},{hull_y:.1f} '
                  f'Q{x + w * 0.50:.1f},{hull_y + h * 0.06:.1f} {x + w * 0.84:.1f},{hull_y:.1f} '
                  f'Q{x + w * 0.50:.1f},{hull_y + h * 0.015:.1f} {x + w * 0.16:.1f},{hull_y:.1f} Z" '
                  f'fill="#1a2534" stroke="#31465f" stroke-width="1.2"/>')


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
def _header(sheet: Sheet, kicker: str, title: str, meta: str) -> None:
    m = 52
    sheet.y = 46
    sheet.text(m, sheet.y + 16, kicker, size=15, fill=LAMP, weight="bold")
    sheet.text(m, sheet.y + 52, title, size=32, weight="bold")
    for i, line in enumerate(wrap_cn(meta, 14, sheet.width - 2 * m, max_lines=2)):
        sheet.text(m, sheet.y + 80 + i * 20, line, size=14, fill=MUTED)
    sheet.y += 118
    sheet.add(f'<path d="M{m},{sheet.y:.1f} L{sheet.width - m},{sheet.y:.1f}" '
              f'stroke="#22314a" stroke-width="1"/>')
    sheet.y += 40


def _caption(sheet: Sheet, x, y, w, title, desc, *, size=14) -> float:
    sheet.text(x, y, title, size=17, fill=PAPER, weight="bold")
    y += 24
    for line in wrap_cn(desc, size, w, max_lines=6):
        sheet.text(x, y, line, size=size, fill="#c7d3e2")
        y += size * 1.55
    return y


def build_assets_page(script: dict, chars: list, scenes: list, *, colors: dict) -> str:
    m, gap = 52, 26
    sheet = Sheet(title="assets")
    _header(sheet, "A / B · 资产",
            "角色身份参考与场景资产",
            "剪影按外观锚点占位，只用于核对人数、朝向与场景光线；不代表长相与画风。"
            "场景清单取自整份脚本，与本次选了哪些镜头无关。")
    if chars:
        sheet.text(m, sheet.y, "角色身份参考（3:4）", size=19, weight="bold", fill=MOON)
        sheet.text(sheet.width - m, sheet.y, "%d 张" % len(chars), size=14, fill=MUTED, anchor="end")
        sheet.y += 22
        cw = (sheet.width - 2 * m - gap * (len(chars) - 1)) / len(chars)
        chh = cw * 4 / 3
        for i, ch in enumerate(chars):
            x = m + i * (cw + gap)
            name = ch.get("identifier", "?")
            scene_panel(sheet, x, sheet.y, cw, chh, scene_name=name, index_text=name,
                        warm=("府" in name))
            sheet.add(f'<g transform="translate({x + cw * 0.5:.1f},{sheet.y + chh * 0.86:.1f}) '
                      f'scale(1.15)">{human("stand", colors.get(name, BODY))}</g>')
        cap_y = sheet.y + chh + 30
        for i, ch in enumerate(chars):
            x = m + i * (cw + gap)
            sheet.text(x, cap_y, ch.get("identifier", ""), size=17, weight="bold",
                       fill=colors.get(ch.get("identifier"), PAPER))
            for j, line in enumerate(wrap_cn(ch.get("static_features", ""), 13, cw, max_lines=6)):
                sheet.text(x, cap_y + 22 + j * 20, line, size=13, fill="#c7d3e2")
        sheet.y = cap_y + 22 + 6 * 20 + 40
    if scenes:
        sheet.text(m, sheet.y, "场景资产（横版）", size=19, weight="bold", fill=MOON)
        sheet.text(sheet.width - m, sheet.y, "%d 张" % len(scenes), size=14, fill=MUTED, anchor="end")
        sheet.y += 22
        cw = (sheet.width - 2 * m - gap * (len(scenes) - 1)) / len(scenes)
        chh = cw * 9 / 16
        for i, sc in enumerate(scenes):
            x = m + i * (cw + gap)
            scene_panel(sheet, x, sheet.y, cw, chh, scene_name=sc["name"], index_text=sc["name"],
                        warm=sc.get("warm", False))
        cap_y = sheet.y + chh + 30
        for i, sc in enumerate(scenes):
            x = m + i * (cw + gap)
            sheet.text(x, cap_y, sc["name"], size=17, weight="bold")
            for j, line in enumerate(wrap_cn(sc["desc"], 13, cw, max_lines=4)):
                sheet.text(x, cap_y + 22 + j * 20, line, size=13, fill="#c7d3e2")
        sheet.y = cap_y + 22 + 4 * 20 + 30
    return sheet.render()


def build_shot_page(script: dict, shot: dict, frames: list, *, colors: dict) -> str:
    m, gap = 52, 26
    sheet = Sheet(title="shot")
    idx = shot.get("index")
    tc = shot.get("timecode") or {}
    _header(sheet, "C · 镜头 %s" % idx,
            "shot %s · %s · %ss" % (idx, shot.get("scene", ""), shot.get("duration_sec")),
            "时间窗 %s–%s s｜机位：%s｜主体：%s｜转场：%s"
            % (tc.get("start"), tc.get("end"), shot.get("camera_movement", ""),
               shot.get("main_subject", ""), shot.get("clip_transition", "")))
    layouts = shot.get("frame_layouts") or {}
    kfs = shot.get("keyframes") or []
    panels = []
    for frame in frames:
        if frame == "ff":
            panels.append(("首帧 · 动作起点", shot.get("ff_desc", ""), layouts.get("ff") or []))
        elif frame == "lf":
            panels.append(("尾帧 · 动作终点", shot.get("lf_desc", ""), layouts.get("lf") or []))
        else:
            mm = re.fullmatch(r"kf(\d+)", frame)
            if mm and int(mm.group(1)) < len(kfs):
                kf = kfs[int(mm.group(1))]
                t = kf.get("t_sec")
                title = "关键帧 · %s%s" % (kf.get("stage", ""),
                                          " · %.2fs" % float(t) if t is not None else "")
                panels.append((title, kf.get("desc", ""),
                               (layouts.get("kfs") or [])[int(mm.group(1))] or []))
    cols = 3 if len(panels) > 3 else len(panels)
    pw = (sheet.width - 2 * m - gap * (cols - 1)) / cols
    ph = pw * 16 / 9
    for row_start in range(0, len(panels), cols):
        row = panels[row_start:row_start + cols]
        for i, (title, desc, lays) in enumerate(row):
            x = m + i * (pw + gap)
            y = sheet.y
            scene_panel(sheet, x, y, pw, ph, scene_name=shot.get("scene", ""),
                        index_text="%d/%d" % (row_start + i + 1, len(panels)))
            draw_layout(sheet, x, y, pw, ph, lays, desc, colors,
                        boat=("船" in json.dumps(lays, ensure_ascii=False)))
            _caption(sheet, x, y + ph + 34, pw, title, desc, size=14)
        sheet.y += ph + 34 + 24 + 6 * 14 * 1.55 + 44
    sheet.text(m, sheet.y, "提示词（计划文本，非最终请求）：", size=14, fill=LAMP, weight="bold")
    sheet.y += 22
    for line in wrap_cn(shot.get("prompt", ""), 13, sheet.width - 2 * m, max_lines=10):
        sheet.text(m, sheet.y, line, size=13, fill="#9fb0c6")
        sheet.y += 20
    return sheet.render()


def collect_scenes(script: dict) -> list:
    """整份脚本里的场景（hook 也算），按第一次出现的顺序；描述取该场景第一镜的首帧."""
    units = []
    if isinstance(script.get("hook"), dict):
        units.append(dict(script["hook"], index=0))
    units.extend(script.get("shots") or [])
    out, seen = [], set()
    for u in units:
        name = str(u.get("scene") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "desc": str(u.get("ff_desc") or ""),
                    "warm": any(k in name for k in ("府", "庄", "宅", "厅", "屋"))})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把帧计划画成 SVG 示意图（0 次模型调用）")
    ap.add_argument("--script", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shots", default="", help="镜头号，逗号分隔；空=全部")
    ap.add_argument("--frames", default="ff,lf,kf0,kf1,kf2")
    ap.add_argument("--no-assets", action="store_true", help="不生成角色/场景页")
    ap.add_argument("--scenes", default="", help="只画这些场景（逗号分隔）；空=整份脚本的场景")
    args = ap.parse_args(argv)

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    chars = list(script.get("characters") or [])
    colors = {c.get("identifier", "?"): CHAR_COLORS[i % len(CHAR_COLORS)]
              for i, c in enumerate(chars)}
    scenes = collect_scenes(script)
    if args.scenes:
        wanted = {s.strip() for s in args.scenes.split(",") if s.strip()}
        scenes = [s for s in scenes if s["name"] in wanted]
    shots_all = list(script.get("shots") or [])
    if script.get("hook"):
        shots_all = [dict(script["hook"], index=0)] + shots_all
    wanted_idx = [int(x) for x in re.split(r"[,\s]+", args.shots.strip()) if x.strip()]
    shots = [s for s in shots_all if int(s.get("index", -1)) in wanted_idx] if wanted_idx else shots_all
    frames = [f.strip() for f in args.frames.split(",") if f.strip()]

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    if not args.no_assets:
        p = out / "01_assets.svg"
        p.write_text(build_assets_page(script, chars, scenes, colors=colors), encoding="utf-8")
        files.append(p)
    for n, shot in enumerate(shots, start=2):
        p = out / ("%02d_shot%03d.svg" % (n, int(shot.get("index", 0))))
        p.write_text(build_shot_page(script, shot, frames, colors=colors), encoding="utf-8")
        files.append(p)
    index = out / "index.html"
    links = "".join('<li><a href="%s">%s</a></li>' % (f.name, f.name) for f in files)
    index.write_text(
        '<!doctype html><meta charset="utf-8"><title>分镜示意图</title>'
        '<body style="background:#0b1220;color:#e9eef6;font-family:sans-serif;padding:24px">'
        '<h1>分镜示意图</h1><ul>' + links + "</ul></body>", encoding="utf-8")
    print(json.dumps({"out_dir": str(out), "files": [f.name for f in files],
                      "scenes": [s["name"] for s in scenes],
                      "shots": [s.get("index") for s in shots]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
