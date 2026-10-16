"""shared.motion_render.validate - MotionBundle 的**纯校验**（计划 §A2/§A3/§15）。

职责边界
--------
- 本模块只**读**输入、只**返回**结构化问题（``ValidationReport.problems``），
  不抛裸异常、不写盘、不渲染（渲染在 ``render.py``，事务/提交在 ``tools/motion_lab.py``）。
- 结构（未知字段/类型/常量/范围）在 ``schema.py``；这里做语义：来源、引用完整性、
  字符索引、时间窗、版式溢出、字体与字形覆盖。

**各轴独立触发**：某一轴失败不会吞掉其它轴的问题——组合故障必须能逐条断言
（自测章「组合故障逐条断言对应 code、轴、编号、路径」）。

关键判据（逐条对应 §A2/§A3/§15）
-------------------------------
1. **来源**：fixture 冻结文档 / accepted_script 走 ``shared.quality_gate.require_step01_upstream``；
   ``source_sha256`` 由代码按冻结字节重算；fixture 单独标记，**不进生产准入**。
2. **source_locator**：固定 RFC6901 文法，只允许指向本包 ``DECLARED_TEXT_LEAF_SHAPES``
   声明字段的**字符串叶**（map 用 ``DECLARED_RELATION_LEAF_SHAPES`` 的关系对象叶）；
   数组下标必须是当前 revision 的规范非负整数；不复用审计模块白名单。
3. **segments**：索引是 **Unicode code point**；``text == source_text[char_start:char_end]``；
   按序、无重叠、无缺失覆盖正文（标点也不丢）；时间半开且在**量化时长**内。
4. **overlays**：spec 引用悬空为 0；``shot_index`` 属于 ``source.script_revision``，
   hook(index=0) 是**独立 hook 身份**，不能当第一正文镜头；越界/重复/跨 revision 拒收；
   每条 overlay 时长 == spec 的**量化时长**（容差 1 帧）。
5. **版式**：按显式 rect 排版；溢出返回 ``layout_overflow``，**不裁字、不缩字号**。
6. **字体**：缺字体文件/缺所需字形在渲染前拒收，不静默系统 fallback。

测量口径：本模块的宽度测量是**保守估计**（``estimate_char_width_em``），只用于渲染前
尽早发现溢出；**权威测量**发生在渲染时的浏览器内（``render.py``）。行切分只有一份
Python 实现（``layout_spec``），浏览器只按其结果做"这个字体下放不放得下"的测量，
不重新决定断行。
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from shared.media_contract import MERGE_FPS, quantize_frame_count, quantized_duration_sec

from .schema import (
    ASSET_KIND_FONT,
    DECLARED_RELATION_LEAF_SHAPES,
    DECLARED_TEXT_LEAF_SHAPES,
    SOURCE_KIND_ACCEPTED_SCRIPT,
    SOURCE_KIND_FIXTURE,
    TEMPLATE_LETTER,
    TEMPLATE_MAP,
    TEMPLATE_TITLE,
    canonical_text_sha256,
    check_motion_bundle,
    parse_pointer,
    pointer_child,
    pointer_join,
    problem,
    resolve_pointer,
    split_index_token,
)

#: 轴（axis）——报告层按轴分类，不用文案猜类别。
AXIS_SCHEMA = "schema"
AXIS_SOURCE = "source"
AXIS_ASSETS = "assets"
AXIS_LOCATOR = "locator"
AXIS_SEGMENTS = "segments"
AXIS_TIME = "time"
AXIS_LAYOUT = "layout"
AXIS_FONTS = "fonts"
AXIS_REFS = "references"
AXIS_OVERLAYS = "overlays"
AXIS_CANVAS = "canvas"
AXIS_MAP = "map"

#: overlay 时长与 spec 量化时长的容差（帧）。
OVERLAY_DURATION_TOLERANCE_FRAMES = 1


# ---------------------------------------------------------------- 结果容器

@dataclass
class ValidationReport:
    """结构化校验结果：问题列表 + 派生事实（交给 manifest/渲染消费）。"""

    problems: list[dict] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def codes(self) -> list[str]:
        return [p["code"] for p in self.problems]

    def by_axis(self, axis: str) -> list[dict]:
        return [p for p in self.problems if p.get("axis") == axis]

    def axes(self) -> list[str]:
        seen: list[str] = []
        for p in self.problems:
            if p.get("axis") not in seen:
                seen.append(p.get("axis"))
        return seen

    def has_code(self, code: str) -> bool:
        return any(p.get("code") == code for p in self.problems)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "problems": list(self.problems), "facts": dict(self.facts)}


def _merge(report: ValidationReport, recs: Iterable[dict]) -> None:
    report.problems.extend(recs)


# ---------------------------------------------------------------- 字体：cmap 缺字检测

class FontError(ValueError):
    pass


def _read_u16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def _read_u32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def font_codepoint_set(path: Path) -> frozenset[int]:
    """读取 TrueType/OpenType 的 cmap，返回字体可渲染的 code point 集合。

    只实现检测所需的最小解析（cmap format 0/4/6/12）；遇到不支持的表格式直接抛
    ``FontError`` —— **fail-closed**：字形覆盖无法证明时不允许渲染通过。
    """
    data = Path(path).read_bytes()
    if len(data) < 12:
        raise FontError(f"字体文件过短，无法解析: {path}")
    if data[:4] == b"ttcf":
        raise FontError(f"不支持 TTC 集合字体（需要单字体 TTF/OTF）: {path}")
    num_tables = _read_u16(data, 4)
    cmap_off = None
    for i in range(num_tables):
        rec = 12 + i * 16
        if rec + 16 > len(data):
            break
        if data[rec:rec + 4] == b"cmap":
            cmap_off = _read_u32(data, rec + 8)
            break
    if cmap_off is None:
        raise FontError(f"字体缺少 cmap 表: {path}")
    n_sub = _read_u16(data, cmap_off + 2)
    priority_map = {(3, 10): 0, (0, 4): 1, (0, 6): 1, (3, 1): 2, (0, 3): 3,
                    (0, 2): 3, (0, 1): 4, (0, 0): 5}
    best: tuple[int, int] | None = None
    for i in range(n_sub):
        rec = cmap_off + 4 + i * 8
        if rec + 8 > len(data):
            break
        plat, enc = _read_u16(data, rec), _read_u16(data, rec + 2)
        off = cmap_off + _read_u32(data, rec + 4)
        pri = priority_map.get((plat, enc))
        if pri is None:
            continue
        if best is None or pri < best[0]:
            best = (pri, off)
    if best is None:
        raise FontError(f"字体 cmap 无受支持的编码子表: {path}")
    sub = best[1]
    fmt = _read_u16(data, sub)
    if fmt == 4:
        return _cmap4(data, sub)
    if fmt == 12:
        return _cmap12(data, sub)
    if fmt == 6:
        return _cmap6(data, sub)
    if fmt == 0:
        return frozenset(range(256))
    raise FontError(f"不支持的 cmap 格式 {fmt}: {path}")


def _cmap4(data: bytes, off: int) -> frozenset[int]:
    seg_x2 = _read_u16(data, off + 6)
    seg = seg_x2 // 2
    ends = [_read_u16(data, off + 14 + i * 2) for i in range(seg)]
    starts = [_read_u16(data, off + 16 + seg_x2 + i * 2) for i in range(seg)]
    deltas = [struct.unpack_from(">h", data, off + 16 + seg_x2 * 2 + i * 2)[0] for i in range(seg)]
    range_off_base = off + 16 + seg_x2 * 3
    offsets = [_read_u16(data, range_off_base + i * 2) for i in range(seg)]
    out: set[int] = set()
    for i in range(seg):
        for cp in range(starts[i], min(ends[i], 0xFFFF) + 1):
            if cp == 0xFFFF:
                continue
            if offsets[i] == 0:
                gid = (cp + deltas[i]) & 0xFFFF
            else:
                addr = range_off_base + i * 2 + offsets[i] + (cp - starts[i]) * 2
                if addr + 2 > len(data):
                    continue
                gid = _read_u16(data, addr)
                if gid:
                    gid = (gid + deltas[i]) & 0xFFFF
            if gid:
                out.add(cp)
    return frozenset(out)


def _cmap12(data: bytes, off: int) -> frozenset[int]:
    n_groups = _read_u32(data, off + 12)
    out: set[int] = set()
    for i in range(n_groups):
        rec = off + 16 + i * 12
        if rec + 12 > len(data):
            break
        start, end, gid = (_read_u32(data, rec), _read_u32(data, rec + 4),
                           _read_u32(data, rec + 8))
        if gid == 0 or end < start or end - start > 0x20000:
            continue
        out.update(range(start, end + 1))
    return frozenset(out)


def _cmap6(data: bytes, off: int) -> frozenset[int]:
    first = _read_u16(data, off + 6)
    count = _read_u16(data, off + 8)
    out = set()
    for i in range(count):
        if _read_u16(data, off + 10 + i * 2):
            out.add(first + i)
    return frozenset(out)


def glyph_check_available(font_path: Path) -> bool:
    try:
        font_codepoint_set(Path(font_path))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 版式

#: 保守宽度估计（em 倍数）。只用于渲染前提前发现溢出；权威测量在浏览器内。
_WIDE_RANGES = (
    (0x1100, 0x115F), (0x2E80, 0xA4CF), (0xAC00, 0xD7A3), (0xF900, 0xFAFF),
    (0xFE30, 0xFE6F), (0xFF00, 0xFF60), (0xFFE0, 0xFFE6),
    (0x1F300, 0x1FAFF), (0x20000, 0x3FFFD),
)


def estimate_char_width_em(ch: str) -> float:
    """单字符宽度估计（em）：宽字符 1.0，ASCII 半角 0.6，其余 0.8（保守偏大）。"""
    cp = ord(ch)
    if any(a <= cp <= b for a, b in _WIDE_RANGES):
        return 1.0
    if cp < 0x0080:
        return 0.6
    return 0.8


@dataclass
class LayoutLine:
    text: str
    width_px: float
    #: 本行在该 segment 文本内的 **code point** 半开区间（与 Python 切片同源，
    #: 交给浏览器做逐字显现；浏览器只按它裁剪，不自行断行）。
    cp_start: int = 0
    cp_end: int = 0


@dataclass
class SegmentLayout:
    segment_id: str
    lines: list[LayoutLine]
    x: float
    y: float
    block_height: float


@dataclass
class SpecLayout:
    """一个 spec 的排版结果（Python 侧**唯一**行切分实现）。"""

    spec_id: str
    template: str
    rect: tuple[int, int, int, int]
    font_size_px: int
    line_height_px: float
    segments: list[SegmentLayout] = field(default_factory=list)
    node_points: dict[str, tuple[float, float]] = field(default_factory=dict)
    label_lines: dict[str, list[str]] = field(default_factory=dict)
    problems: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "template": self.template,
            "rect": list(self.rect),
            "font_size_px": self.font_size_px,
            "line_height_px": self.line_height_px,
            "segments": [
                {"segment_id": s.segment_id, "x": s.x, "y": s.y,
                 "block_height": s.block_height,
                 "lines": [{"text": ln.text, "width_px": ln.width_px,
                            "cp_start": ln.cp_start, "cp_end": ln.cp_end} for ln in s.lines]}
                for s in self.segments
            ],
            "node_points": {k: list(v) for k, v in self.node_points.items()},
            "label_lines": {k: list(v) for k, v in self.label_lines.items()},
        }


LINE_HEIGHT_RATIO = 1.25


def wrap_text_ranges(text: str, font_size_px: int, max_width_px: float,
                     measure=None) -> list[tuple[str, int, int]]:
    """按 code point 贪心切行，返回 ``(行文本, cp_start, cp_end)``；**不丢字符**。

    换行符在此**按硬换行**处理（不进入任何行文本）；``\\n`` 出现在正文里会由
    ``_check_segments`` 以 ``newline_in_text`` 拒收——分段才是换行的表达方式，
    这样 code point 计账不含"被吃掉"的字符。
    """
    measure = measure or (lambda s: sum(estimate_char_width_em(c) for c in s) * font_size_px)
    out: list[tuple[str, int, int]] = []
    cur = ""
    cp = 0
    start = 0
    for ch in text:
        cp += 1
        if ch == "\n":
            out.append((cur, start, cp - 1))
            cur = ""
            start = cp
            continue
        cand = cur + ch
        if cur and measure(cand) > max_width_px:
            out.append((cur, start, cp - 1))
            cur = ch
            start = cp - 1
        else:
            cur = cand
    if cur or not out:
        out.append((cur, start, cp))
    return out


def wrap_text(text: str, font_size_px: int, max_width_px: float,
              measure=None) -> list[str]:
    """按 code point 贪心切行；**不丢字符**（标点也不删）——``wrap_text_ranges`` 的薄封装。"""
    return [t for t, _s, _e in wrap_text_ranges(text, font_size_px, max_width_px, measure)]


def _measure_line(line: str, font_size_px: int, measure=None) -> float:
    if measure is not None:
        return float(measure(line))
    return sum(estimate_char_width_em(c) for c in line) * font_size_px


def layout_spec(spec: Mapping[str, Any], canvas: Mapping[str, Any], *,
                measure=None, axis_path: str = "") -> SpecLayout:
    """按显式 rect 排版一个 spec（title/letter 文本块；map 节点坐标与标签）。"""
    rect = tuple(int(v) for v in spec["rect"])
    x, y, w, h = rect
    size = int(spec["font_size_px"])
    line_h = size * LINE_HEIGHT_RATIO
    out = SpecLayout(spec_id=str(spec.get("spec_id")), template=str(spec.get("template")),
                     rect=rect, font_size_px=size, line_height_px=line_h)
    tmpl = spec.get("template")

    if tmpl == TEMPLATE_MAP:
        for node in spec.get("nodes") or []:
            out.node_points[str(node["node_id"])] = (
                x + float(node["x"]) * w, y + float(node["y"]) * h)
        for node in spec.get("nodes") or []:
            label = str(node["label"])
            lines = wrap_text(label, size, w, measure=measure)
            out.label_lines[str(node["node_id"])] = lines
            width = max((_measure_line(ln, size, measure) for ln in lines), default=0.0)
            if width > w:
                out.problems.append(problem(
                    "layout_overflow", AXIS_LAYOUT,
                    pointer_child(axis_path, "nodes") if axis_path else "",
                    f"map 节点标签 {label!r} 在 rect 宽 {w}px 内放不下（需 {width:.1f}px）",
                    spec_id=out.spec_id, node_id=str(node["node_id"]),
                    needed_px=round(width, 2), available_px=w))
        if (spec.get("nodes") or []) and h < line_h:
            out.problems.append(problem(
                "layout_overflow", AXIS_LAYOUT,
                pointer_child(axis_path, "rect") if axis_path else "",
                f"map rect 高度 {h}px 小于一行行高 {line_h:.1f}px",
                spec_id=out.spec_id, needed_px=round(line_h, 2), available_px=h))
        return out

    cursor_y = float(y)
    for seg in spec.get("segments") or []:
        text = str(seg.get("text") or "")
        ranges = wrap_text_ranges(text, size, w, measure=measure)
        widths = [_measure_line(t, size, measure) for t, _s, _e in ranges]
        max_w = max(widths) if widths else 0.0
        block_h = line_h * len(ranges)
        if max_w > w:
            out.problems.append(problem(
                "layout_overflow", AXIS_LAYOUT,
                pointer_child(axis_path, "segments") if axis_path else "",
                f"段落 {seg.get('segment_id')!r} 单字符即超出 rect 宽 {w}px"
                f"（需 {max_w:.1f}px，字号 {size}px）",
                spec_id=out.spec_id, segment_id=str(seg.get("segment_id")),
                needed_px=round(max_w, 2), available_px=w))
        out.segments.append(SegmentLayout(
            segment_id=str(seg.get("segment_id")),
            lines=[LayoutLine(t, wi, cs, ce)
                   for (t, cs, ce), wi in zip(ranges, widths)],
            x=float(x), y=cursor_y, block_height=block_h))
        cursor_y += block_h
    total_h = cursor_y - float(y)
    if total_h > h:
        out.problems.append(problem(
            "layout_overflow", AXIS_LAYOUT,
            pointer_child(axis_path, "rect") if axis_path else "",
            f"文本块总高 {total_h:.1f}px 超出 rect 高 {h}px（不裁字、不缩字号）",
            spec_id=out.spec_id, needed_px=round(total_h, 2), available_px=h))
    return out


# ---------------------------------------------------------------- 定位策略

def leaf_shapes_for(template: str) -> tuple[str, ...]:
    """按模板给出允许的**本包声明**叶形状。"""
    return DECLARED_RELATION_LEAF_SHAPES if template == TEMPLATE_MAP else DECLARED_TEXT_LEAF_SHAPES


def match_declared_shape(tokens: Sequence[str], shapes: Sequence[str]) -> bool:
    """把具体 token 序列匹配到声明形状（``<i>`` 位必须是规范非负整数下标）。"""
    for shape in shapes:
        want = [t for t in shape.strip("/").split("/") if t]
        if len(want) != len(tokens):
            continue
        ok = True
        for wtok, got in zip(want, tokens):
            if wtok == "<i>":
                if split_index_token(got) is None:
                    ok = False
                    break
            elif wtok != got:
                ok = False
                break
        if ok:
            return True
    return False


def resolve_locator(locator: Mapping[str, Any], *, source_kind: str, source_doc: Any,
                    template: str, shots_len: int | None, path: str) -> tuple[bool, Any, list[dict]]:
    """解析 ``source_locator``：RFC6901 文法 + 本包声明叶白名单 + 数组下标范围。

    Returns: ``(是否命中, 值, 问题列表)``。
    """
    problems: list[dict] = []
    pointer = locator.get("field_path")
    if pointer is None:
        pointer = "/text"
    try:
        tokens = parse_pointer(str(pointer))
    except ValueError as exc:
        problems.append(problem("invalid_pointer", AXIS_LOCATOR,
                                pointer_child(path, "field_path"), str(exc), locator=str(pointer)))
        return False, None, problems
    shapes = leaf_shapes_for(template)
    if not match_declared_shape(tokens, shapes):
        problems.append(problem(
            "undeclared_leaf", AXIS_LOCATOR, pointer_child(path, "field_path"),
            f"指针 {pointer!r} 不在本包声明字段内（{list(shapes)}）",
            locator=str(pointer), declared=list(shapes)))
        return False, None, problems
    for i, tok in enumerate(tokens):
        if tok != "shots":
            continue
        idx_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        idx = split_index_token(str(idx_tok)) if idx_tok is not None else None
        if idx is not None and shots_len is not None and idx >= shots_len:
            problems.append(problem(
                "locator_index_out_of_range", AXIS_LOCATOR, pointer_child(path, "field_path"),
                f"shots 下标 {idx} 超出当前 revision 的镜头数 {shots_len}",
                locator=str(pointer), index=idx, shots_len=shots_len))
            return False, None, problems
    found, value = resolve_pointer(source_doc, tokens)
    if not found:
        problems.append(problem("locator_unresolved", AXIS_LOCATOR, pointer_child(path, "field_path"),
                                f"指针 {pointer!r} 在来源文档中不存在（source_kind={source_kind}）",
                                locator=str(pointer), source_kind=source_kind))
        return False, None, problems
    return True, value, problems


# ---------------------------------------------------------------- 镜头表

def shot_table(source_doc: Any) -> dict:
    """由来源文档派生镜头表（**编号由代码派生**，不手抄）。

    - ``indexes``：正文镜头号（取 ``shots[i].index``，缺失时按 1..N）；
    - ``has_hook``：来源是否显式声明 hook（``hook`` 是对象）；
    - ``max_index``：合法 ``shot_index`` 上界；hook 的独立身份是 index=0。
    """
    shots = source_doc.get("shots") if isinstance(source_doc, Mapping) else None
    indexes: list[int] = []
    if isinstance(shots, list):
        for i, sh in enumerate(shots):
            raw = sh.get("index") if isinstance(sh, Mapping) else None
            try:
                indexes.append(int(raw)) if raw is not None else indexes.append(i + 1)
            except (TypeError, ValueError):
                indexes.append(i + 1)
    has_hook = bool(isinstance(source_doc, Mapping) and isinstance(source_doc.get("hook"), dict))
    return {
        "count": len(indexes),
        "indexes": indexes,
        "has_hook": has_hook,
        "max_index": max(indexes) if indexes else (0 if has_hook else -1),
        "revision": str((source_doc or {}).get("script_revision") or "")
        if isinstance(source_doc, Mapping) else "",
    }


# ---------------------------------------------------------------- 来源加载

def fixture_doc_sha256(doc: Any) -> str:
    """fixture 冻结文档的摘要口径（规范 JSON、UTF-8）——与校验侧同一函数。"""
    return hashlib.sha256(
        json.dumps(doc, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def load_fixture_doc(asset_root: Path | str, fixture_id: str) -> Any:
    """冻结 fixture 文档：``<asset_root>/fixtures/<fixture_id>.json``（严格 JSON）。"""
    from .schema import load_json_file_strict
    p = Path(asset_root) / "fixtures" / f"{fixture_id}.json"
    if not p.is_file():
        raise FileNotFoundError(f"fixture 文档不存在: {p}")
    return load_json_file_strict(p)


def load_bundle_with_sources(bundle: Mapping[str, Any], *, asset_root: Path | str,
                             source_dir: Path | str | None = None
                             ) -> tuple[Any, str | None, list[dict]]:
    """按 ``source.kind`` 加载来源文档并做**生产准入**判据。

    accepted_script 必须过 ``shared.quality_gate.require_step01_upstream``（语义门 +
    commit manifest 两道），且 ``script_revision`` 与 bundle 声明一致——真实来源
    不能只靠"文件存在"。
    """
    problems: list[dict] = []
    source = bundle.get("source") or {}
    kind = str(source.get("kind"))
    if kind == SOURCE_KIND_FIXTURE:
        try:
            doc = load_fixture_doc(asset_root, str(source["fixture_id"]))
        except Exception as exc:
            problems.append(problem("fixture_load_failed", AXIS_SOURCE, "/source/fixture_id",
                                    f"冻结 fixture 文档读取失败: {exc}",
                                    fixture_id=str(source.get("fixture_id"))))
            return None, None, problems
        return doc, (str(doc.get("script_revision") or "") or None), problems

    if kind != SOURCE_KIND_ACCEPTED_SCRIPT:
        problems.append(problem("unknown_source_kind", AXIS_SOURCE, "/source/kind",
                                f"未知 source.kind: {kind!r}"))
        return None, None, problems

    ch = Path(source_dir) if source_dir is not None else Path(asset_root) / "source"
    script_path = ch / "script_result.json"
    if not script_path.is_file():
        problems.append(problem("script_missing", AXIS_SOURCE, "/source",
                                f"accepted_script 来源目录缺 script_result.json: {script_path}",
                                source_dir=str(ch)))
        return None, None, problems
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except Exception as exc:
        problems.append(problem("script_unreadable", AXIS_SOURCE, "/source",
                                f"script_result.json 读取失败: {exc}", source_dir=str(ch)))
        return None, None, problems
    if isinstance(script, dict):
        script["__raw_bytes_sha256__"] = hashlib.sha256(script_path.read_bytes()).hexdigest()
    try:
        from shared.quality_gate import QualityGateError, require_step01_upstream
    except Exception as exc:  # import 失败与"门不过"分开记账
        problems.append(problem("quality_gate_unavailable", AXIS_SOURCE, "/source",
                                f"quality_gate 不可用: {exc}"))
        return None, None, problems
    try:
        require_step01_upstream(ch, script, stage="motion", expected_chapter=source.get("chapter"))
    except QualityGateError as exc:
        problems.append(problem("step01_upstream_rejected", AXIS_SOURCE, "/source",
                                f"Step01 提交/质量门拒绝: {exc}", source_dir=str(ch)))
        return None, None, problems
    except Exception as exc:
        problems.append(problem("step01_upstream_error", AXIS_SOURCE, "/source",
                                f"Step01 准入检查异常: {exc}", source_dir=str(ch)))
        return None, None, problems
    revision = str(script.get("script_revision") or "")
    if revision != str(source.get("script_revision")):
        problems.append(problem(
            "source_revision_mismatch", AXIS_SOURCE, "/source/script_revision",
            "bundle 声明的 script_revision 与采纳稿不符（跨版本不得自动沿用）",
            expected=str(source.get("script_revision")), actual=revision))
    if str(source.get("book") or "") and str(script.get("book_name") or "") \
            and str(source["book"]) not in str(script.get("book_name")):
        problems.append(problem(
            "source_book_mismatch", AXIS_SOURCE, "/source/book",
            "bundle 声明的 book 与来源脚本不符",
            expected=str(source.get("book")), actual=str(script.get("book_name"))))
    return script, (revision or None), problems


def source_doc_sha256(source_doc: Any, kind: str) -> str:
    """来源摘要口径（写入侧与校验侧同源）。"""
    if kind == SOURCE_KIND_ACCEPTED_SCRIPT:
        if isinstance(source_doc, Mapping) and "__raw_bytes_sha256__" in source_doc:
            return str(source_doc["__raw_bytes_sha256__"])
        return fixture_doc_sha256(source_doc)
    return fixture_doc_sha256(source_doc)


# ---------------------------------------------------------------- 主入口

def validate_motion_bundle(bundle: Any, *, asset_root: Path | str | None = None,
                           source_doc: Any = None, measure=None,
                           check_assets: bool = True) -> ValidationReport:
    """完整校验：结构 + 语义。结构失败不吞掉其它轴的语义问题。"""
    report = ValidationReport()
    _merge(report, check_motion_bundle(bundle))
    if not isinstance(bundle, Mapping):
        return report

    canvas = bundle.get("canvas")
    source = bundle.get("source")
    specs = bundle.get("specs")
    overlays = bundle.get("overlays")
    kind = str(source.get("kind")) if isinstance(source, Mapping) else ""
    table = shot_table(source_doc) if source_doc is not None else {
        "count": None, "indexes": [], "has_hook": False, "max_index": None, "revision": ""}

    if isinstance(canvas, Mapping):
        _check_canvas(report, canvas)
    _check_source_shape(report, source, source_doc, kind)
    _check_assets(report, bundle, asset_root, check_assets=check_assets)

    fonts = _font_assets(bundle)
    coverage: list[dict] = []
    layouts: dict[str, SpecLayout] = {}
    spec_ids: list[str] = []
    specs_by_id: dict[str, Mapping[str, Any]] = {}
    if isinstance(specs, list):
        for i, spec in enumerate(specs):
            path = pointer_join("specs", i)
            if not isinstance(spec, Mapping):
                continue  # 结构性 wrong_type 已由 schema 报出
            sid = str(spec.get("spec_id"))
            if sid in spec_ids:
                report.problems.append(problem("duplicate_id", AXIS_REFS,
                                               pointer_child(path, "spec_id"),
                                               f"spec_id 重复: {sid!r}", spec_id=sid))
            spec_ids.append(sid)
            specs_by_id[sid] = spec
            if not isinstance(canvas, Mapping):
                continue
            _check_spec(report, spec, canvas, bundle, source_doc=source_doc,
                        shots_len=table["count"], fonts=fonts, path=path,
                        measure=measure, layouts=layouts, asset_root=asset_root,
                        coverage=coverage)

    overlay_ids: list[str] = []
    if isinstance(overlays, list):
        for i, ov in enumerate(overlays):
            path = pointer_join("overlays", i)
            if not isinstance(ov, Mapping):
                continue
            oid = str(ov.get("overlay_id"))
            if oid in overlay_ids:
                report.problems.append(problem("duplicate_id", AXIS_OVERLAYS,
                                               pointer_child(path, "overlay_id"),
                                               f"overlay_id 重复: {oid!r}", overlay_id=oid))
            overlay_ids.append(oid)
            _check_overlay(report, ov, specs_by_id=specs_by_id, table=table, path=path,
                           source_kind=kind)

    _check_group_coverage(report, coverage)
    bound = {str(o.get("spec_id")) for o in overlays if isinstance(o, Mapping)} \
        if isinstance(overlays, list) else set()
    report.facts.update({
        "source_kind": kind,
        "spec_ids": spec_ids,
        "spec_count": len(spec_ids),
        "overlay_ids": overlay_ids,
        "bound_spec_ids": sorted(bound),
        "unbound_spec_ids": sorted(set(spec_ids) - bound),
        "template_by_spec": {str(s.get("spec_id")): str(s.get("template"))
                             for s in (specs or []) if isinstance(s, Mapping)},
        "duration_by_spec": {str(s.get("spec_id")): float(s.get("duration_sec", 0.0))
                             for s in (specs or []) if isinstance(s, Mapping)},
        "frame_count_by_spec": {},
        "layouts": {k: v.to_dict() for k, v in layouts.items()},
        "shot_table": table,
        "production_admission": kind == SOURCE_KIND_ACCEPTED_SCRIPT,
    })
    fps = int(canvas.get("fps", MERGE_FPS)) if isinstance(canvas, Mapping) else MERGE_FPS
    for spec in (specs or []):
        if not isinstance(spec, Mapping):
            continue
        try:
            report.facts["frame_count_by_spec"][str(spec.get("spec_id"))] = quantize_frame_count(
                float(spec.get("duration_sec", 0.0)), fps)
        except Exception:
            continue
    return report


# ---------------------------------------------------------------- 子检查

def _rect_like(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(int(v) for v in value)
    return None


def _check_canvas(report: ValidationReport, canvas: Mapping[str, Any]) -> None:
    try:
        w, h = int(canvas["width"]), int(canvas["height"])
    except Exception:
        return  # 结构问题已由 schema 报出
    fps = canvas.get("fps")
    if fps != MERGE_FPS:
        report.problems.append(problem(
            "fps_mismatch", AXIS_CANVAS, "/canvas/fps",
            f"fps 必须等于 MERGE_FPS={MERGE_FPS}（不得抄历史配置）", actual=fps, expected=MERGE_FPS))
    for key in ("safe_rect", "subtitle_exclusion_rect"):
        rect = _rect_like(canvas.get(key))
        if rect is None:
            continue
        x, y, rw, rh = rect
        if rw <= 0 or rh <= 0:
            report.problems.append(problem("empty_rect", AXIS_CANVAS, pointer_join("canvas", key),
                                           f"{key} 宽高必须为正，实际 {rw}x{rh}", actual=[rw, rh]))
        if x + rw > w or y + rh > h:
            report.problems.append(problem(
                "rect_outside_canvas", AXIS_CANVAS, pointer_join("canvas", key),
                f"{key} 超出画幅 {w}x{h}: x+w={x + rw}, y+h={y + rh}", actual=[x, y, rw, rh]))
    sr, se = _rect_like(canvas.get("safe_rect")), _rect_like(canvas.get("subtitle_exclusion_rect"))
    if sr and se and _rect_overlap(sr, se):
        report.problems.append(problem(
            "safe_rect_overlaps_subtitle_exclusion", AXIS_CANVAS, "/canvas/safe_rect",
            "safe_rect 与 subtitle_exclusion_rect 重叠", safe_rect=list(sr), subtitle=list(se)))


def _rect_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _check_source_shape(report: ValidationReport, source: Any, source_doc: Any, kind: str) -> None:
    if not isinstance(source, Mapping):
        return
    if kind == SOURCE_KIND_FIXTURE and not str(source.get("fixture_id") or ""):
        report.problems.append(problem("missing_field", AXIS_SOURCE, "/source/fixture_id",
                                       "fixture 来源必须带 fixture_id"))
    if source_doc is None:
        if kind == SOURCE_KIND_FIXTURE:
            report.problems.append(problem(
                "fixture_doc_missing", AXIS_SOURCE, "/source",
                "fixture 来源文档未加载（调用方需给出冻结 fixture 文档）"))
        return
    actual = source_doc_sha256(source_doc, kind)
    if actual != str(source.get("source_sha256")):
        report.problems.append(problem(
            "source_digest_mismatch", AXIS_SOURCE, "/source/source_sha256",
            "source_sha256 与冻结来源不符（来源可能被替换）",
            expected=str(source.get("source_sha256")), actual=actual))


def _check_assets(report: ValidationReport, bundle: Mapping[str, Any],
                  asset_root: Path | str | None, *, check_assets: bool) -> None:
    assets = bundle.get("assets")
    if not isinstance(assets, Mapping):
        return
    has_font = False
    root = Path(asset_root) if asset_root is not None else None
    for aid, asset in assets.items():
        path = pointer_join("assets", aid)
        if not isinstance(asset, Mapping):
            continue
        if str(asset.get("kind")) == ASSET_KIND_FONT:
            has_font = True
        rel = str(asset.get("path") or "")
        if root is None or not check_assets:
            continue
        if rel.startswith(("/", "\\")) or ".." in Path(rel).parts \
                or (len(rel) > 1 and rel[1] == ":"):
            report.problems.append(problem(
                "asset_path_escape", AXIS_ASSETS, pointer_child(path, "path"),
                f"asset path 必须相对只读素材根且不得逃逸: {rel!r}", actual=rel))
            continue
        target = root / rel
        if not target.is_file():
            report.problems.append(problem(
                "asset_missing", AXIS_ASSETS, pointer_child(path, "path"),
                f"asset 文件不存在: {target}", actual=str(target)))
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != str(asset.get("sha256")):
            report.problems.append(problem(
                "asset_digest_mismatch", AXIS_ASSETS, pointer_child(path, "sha256"),
                "asset 摘要与文件不符（素材被替换）",
                expected=str(asset.get("sha256")), actual=digest, path_hint=str(target)))
    if not has_font:
        report.problems.append(problem("no_font_asset", AXIS_ASSETS, "/assets",
                                       "assets 必须至少有一个 kind=font 的字体"))


def _font_assets(bundle: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(k): v for k, v in (bundle.get("assets") or {}).items()
            if isinstance(v, Mapping) and str(v.get("kind")) == ASSET_KIND_FONT}


def _locator_key(locator: Mapping[str, Any]) -> str:
    return json.dumps({str(k): locator[k] for k in sorted(locator)}, ensure_ascii=False)


def _check_group_coverage(report: ValidationReport, coverage: Sequence[Mapping[str, Any]]) -> None:
    """同一来源定位叶的所有 spec 必须**合起来覆盖整片叶**（不得只取半句）。

    §A2「source核心文字分段前后必须完整相同」「标点也不能删」：把一段文字拆到多个
    spec 是合法的，但拼回去必须等于原文；重复显示由修订守卫（旧绑定）另行判。
    """
    groups: dict[str, dict] = {}
    for item in coverage:
        g = groups.setdefault(item["locator_key"], {"leaf": item["leaf"], "covered": set(),
                                                    "specs": []})
        g["specs"].append(item["spec_id"])
        for cs, ce in item["ranges"]:
            g["covered"].update(range(item["offset"] + cs, item["offset"] + ce))
    for key, g in groups.items():
        missing = [i for i in range(len(g["leaf"])) if i not in g["covered"]]
        if missing:
            preview = "".join(g["leaf"][i] for i in missing[:20])
            report.problems.append(problem(
                "source_text_incomplete", AXIS_SEGMENTS, "/specs",
                f"来源叶未被完整覆盖（缺 {len(missing)} 个字，例如 {preview!r}）；"
                "拆分必须拼回全文，标点也不能删",
                locator=key, specs=sorted(g["specs"]), missing_total=len(missing),
                missing_indexes=missing[:40]))


def _check_spec(report: ValidationReport, spec: Mapping[str, Any], canvas: Mapping[str, Any],
                bundle: Mapping[str, Any], *, source_doc: Any, shots_len: int | None,
                fonts: Mapping[str, Mapping[str, Any]], path: str, measure,
                layouts: dict, asset_root: Path | str | None,
                coverage: list) -> None:
    tmpl = str(spec.get("template"))
    sid = str(spec.get("spec_id"))
    w, h = int(canvas["width"]), int(canvas["height"])
    rect = _rect_like(spec.get("rect"))
    if rect is not None:
        x, y, rw, rh = rect
        if x + rw > w or y + rh > h:
            report.problems.append(problem(
                "rect_outside_canvas", AXIS_CANVAS, pointer_child(path, "rect"),
                f"rect 超出画幅 {w}x{h}: x+w={x + rw}, y+h={y + rh}", spec_id=sid))
        sr = _rect_like(canvas.get("safe_rect"))
        if sr and not (sr[0] <= x and sr[1] <= y and x + rw <= sr[0] + sr[2]
                       and y + rh <= sr[1] + sr[3]):
            report.problems.append(problem(
                "rect_outside_safe_rect", AXIS_CANVAS, pointer_child(path, "rect"),
                "spec.rect 必须在 canvas.safe_rect 内", spec_id=sid, safe_rect=list(sr)))
        se = _rect_like(canvas.get("subtitle_exclusion_rect"))
        if se and _rect_overlap(rect, se):
            report.problems.append(problem(
                "rect_overlaps_subtitle_exclusion", AXIS_CANVAS, pointer_child(path, "rect"),
                "spec.rect 与 subtitle_exclusion_rect 重叠（绘制区必须避开字幕区）",
                spec_id=sid, subtitle_exclusion_rect=list(se)))

    faid = str(spec.get("font_asset_id"))
    if faid not in fonts:
        report.problems.append(problem(
            "font_asset_unknown", AXIS_FONTS, pointer_child(path, "font_asset_id"),
            f"font_asset_id {faid!r} 不是已声明的 kind=font 资源", spec_id=sid,
            declared=sorted(fonts)))

    try:
        dur = float(spec.get("duration_sec"))
        qdur = quantized_duration_sec(dur, int(canvas["fps"]))
        quantize_frame_count(dur, int(canvas["fps"]))
    except Exception as exc:
        report.problems.append(problem("bad_duration", AXIS_TIME, pointer_child(path, "duration_sec"),
                                       f"duration_sec 无法量化: {exc}", spec_id=sid))
        return

    locator = spec.get("source_locator")
    if source_doc is not None and isinstance(locator, Mapping):
        resolved, value, loc_problems = resolve_locator(
            locator, source_kind=str((bundle.get("source") or {}).get("kind")),
            source_doc=source_doc, template=tmpl, shots_len=shots_len,
            path=pointer_child(path, "source_locator"))
        _merge(report, loc_problems)
        if resolved:
            _check_locator_value(report, spec, locator, value, tmpl, path, sid)
            if isinstance(value, str) and tmpl != TEMPLATE_MAP:
                offset = leaf_offset(value, str(spec.get("source_text") or ""))
                if offset is not None:
                    coverage.append({
                        "spec_id": sid,
                        "locator_key": _locator_key(locator),
                        "leaf": value,
                        "offset": offset,
                        "ranges": [(int(seg["char_start"]), int(seg["char_end"]))
                                   for seg in (spec.get("segments") or [])
                                   if isinstance(seg, Mapping)],
                    })

    if tmpl in (TEMPLATE_TITLE, TEMPLATE_LETTER):
        _check_segments(report, spec, qdur, path, sid, tmpl)
    elif tmpl == TEMPLATE_MAP:
        relation = None
        if source_doc is not None and isinstance(locator, Mapping):
            _ok, value, _lp = resolve_locator(
                locator, source_kind=str((bundle.get("source") or {}).get("kind")),
                source_doc=source_doc, template=tmpl, shots_len=shots_len,
                path=pointer_child(path, "source_locator"))
            relation = value if _ok else None
        _check_map(report, spec, qdur, path, sid, relation)

    layout = layout_spec(spec, canvas, measure=measure, axis_path=path)
    layouts[sid] = layout
    _merge(report, layout.problems)
    _check_glyphs(report, spec, fonts.get(faid), path, sid, asset_root)


def _check_locator_value(report: ValidationReport, spec: Mapping[str, Any],
                         locator: Mapping[str, Any], value: Any, tmpl: str,
                         path: str, sid: str) -> None:
    leaf_path = pointer_child(path, "source_locator", "field_path")
    text = str(spec.get("source_text") or "")
    if tmpl == TEMPLATE_MAP:
        if not isinstance(value, Mapping):
            report.problems.append(problem(
                "map_relation_not_object", AXIS_MAP, leaf_path,
                f"map 的关系来源叶必须是对象（含 nodes/edges），实际 {type(value).__name__}",
                spec_id=sid))
            return
        if text != "":
            report.problems.append(problem(
                "map_source_text_not_empty", AXIS_MAP, pointer_child(path, "source_text"),
                "map 的 source_text 必须为空串（正文由 nodes/edges 表达）", spec_id=sid))
        declared = str(locator.get("sha256") or "")
        if declared:
            digest = _canonical_digest(value)
            if digest != declared:
                report.problems.append(problem(
                    "locator_digest_mismatch", AXIS_LOCATOR,
                    pointer_child(path, "source_locator", "sha256"),
                    "locator.sha256 与关系叶内容不符", spec_id=sid,
                    expected=declared, actual=digest))
        return
    if not isinstance(value, str):
        report.problems.append(problem(
            "locator_not_string_leaf", AXIS_LOCATOR, leaf_path,
            f"定位叶必须是字符串叶，实际 {type(value).__name__}", spec_id=sid))
        return
    offset = leaf_offset(value, text)
    if offset is None:
        report.problems.append(problem(
            "source_text_not_in_leaf", AXIS_LOCATOR, pointer_child(path, "source_text"),
            "source_text 必须是定位叶的连续子串（拆分只能取切片，不得改写或新造原文）",
            spec_id=sid, leaf_digest=canonical_text_sha256(value),
            text_digest=canonical_text_sha256(text)))
    declared = str(locator.get("sha256") or "")
    if declared and declared != canonical_text_sha256(value):
        report.problems.append(problem(
            "locator_digest_mismatch", AXIS_LOCATOR,
            pointer_child(path, "source_locator", "sha256"),
            "locator.sha256 与定位叶原文不符（原样 UTF-8 字节摘要）", spec_id=sid,
            expected=declared, actual=canonical_text_sha256(value)))


def leaf_offset(leaf: str, text: str) -> int | None:
    """``text`` 在 ``leaf`` 中的**唯一**起点；不是连续子串或不唯一 ⇒ None。

    唯一性就是「无唯一对应拒收」：同一段文字在叶里出现两次时，无法证明引用的是哪一处。
    """
    if leaf == text:
        return 0
    if not text:
        return None
    first = leaf.find(text)
    if first < 0 or leaf.find(text, first + 1) != -1:
        return None
    return first


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _check_segments(report: ValidationReport, spec: Mapping[str, Any], qdur: float,
                    path: str, sid: str, tmpl: str) -> None:
    segs = list(spec.get("segments") or [])
    text = str(spec.get("source_text") or "")
    if spec.get("nodes") or spec.get("edges"):
        report.problems.append(problem("map_fields_on_text_spec", AXIS_SCHEMA, path,
                                       "title/letter 的 nodes/edges 必须为空表（不适用字段为空表）",
                                       spec_id=sid))
    if not text:
        report.problems.append(problem("empty_source_text", AXIS_SEGMENTS,
                                       pointer_child(path, "source_text"),
                                       "title/letter 的 source_text 不能为空", spec_id=sid))
        return
    if not segs:
        report.problems.append(problem("no_segments", AXIS_SEGMENTS, pointer_child(path, "segments"),
                                       "title/letter 必须至少有一个 segment", spec_id=sid))
        return
    cursor = 0
    prev_end: float | None = None
    for i, seg in enumerate(segs):
        spath = pointer_child(path, "segments", i)
        if not isinstance(seg, Mapping):
            continue
        try:
            cs, ce = int(seg["char_start"]), int(seg["char_end"])
        except Exception:
            continue
        if not (0 <= cs < ce <= len(text)):
            report.problems.append(problem(
                "bad_char_range", AXIS_SEGMENTS, pointer_child(spath, "char_start"),
                f"字符区间 [{cs}, {ce}) 越界（source_text 长度 {len(text)}）", spec_id=sid))
            continue
        if str(seg["text"]) != text[cs:ce]:
            report.problems.append(problem(
                "segment_text_mismatch", AXIS_SEGMENTS, pointer_child(spath, "text"),
                "segment.text 必须等于 source_text[char_start:char_end]（code point 索引）",
                spec_id=sid, expected=text[cs:ce], actual=str(seg["text"])))
        if "\n" in str(seg["text"]) or "\r" in str(seg["text"]):
            report.problems.append(problem(
                "newline_in_text", AXIS_SEGMENTS, pointer_child(spath, "text"),
                "正文不得含换行符：换行用 segments 表达，否则 code point 计账会吃掉字符",
                spec_id=sid))
        if cs != cursor:
            report.problems.append(problem(
                "segment_coverage_gap" if cs > cursor else "segment_overlap",
                AXIS_SEGMENTS, pointer_child(spath, "char_start"),
                f"分段必须按序无重叠无缺失覆盖正文：期望起点 {cursor}，实际 {cs}", spec_id=sid))
        cursor = ce
        st, en = float(seg["start_sec"]), float(seg["end_sec"])
        if not (0.0 <= st < en <= qdur + 1e-9):
            report.problems.append(problem(
                "segment_time_out_of_range", AXIS_TIME, pointer_child(spath, "start_sec"),
                f"segment 时间窗 [{st}, {en}) 必须半开且落在量化时长 {qdur}s 内", spec_id=sid))
        if tmpl == TEMPLATE_LETTER and prev_end is not None and st < prev_end - 1e-9:
            report.problems.append(problem(
                "segment_time_overlap", AXIS_TIME, pointer_child(spath, "start_sec"),
                f"letter 逐段显现要求时间窗不重叠：上一段结束 {prev_end} > 本段开始 {st}",
                spec_id=sid))
        prev_end = en
    if cursor != len(text):
        report.problems.append(problem(
            "segment_coverage_incomplete", AXIS_SEGMENTS, pointer_child(path, "segments"),
            f"分段未覆盖正文尾部：覆盖到 {cursor}，正文长度 {len(text)}（标点也不能删）",
            spec_id=sid, covered=cursor, total=len(text)))


def _check_map(report: ValidationReport, spec: Mapping[str, Any], qdur: float,
               path: str, sid: str, relation: Any) -> None:
    nodes = [n for n in (spec.get("nodes") or []) if isinstance(n, Mapping)]
    edges = [e for e in (spec.get("edges") or []) if isinstance(e, Mapping)]
    if not nodes:
        report.problems.append(problem("no_nodes", AXIS_MAP, pointer_child(path, "nodes"),
                                       "map 必须至少有一个节点", spec_id=sid))
    if not edges:
        report.problems.append(problem("no_edges", AXIS_MAP, pointer_child(path, "edges"),
                                       "map 必须至少有一条边（首版直线方向动画）", spec_id=sid))
    if spec.get("segments"):
        report.problems.append(problem("map_fields_on_text_spec", AXIS_SCHEMA, path,
                                       "map 的 segments 必须为空表", spec_id=sid))
    ids: list[str] = []
    labels: list[str] = []
    for i, node in enumerate(nodes):
        nid = str(node.get("node_id"))
        if nid in ids:
            report.problems.append(problem("duplicate_id", AXIS_MAP,
                                           pointer_child(path, "nodes", i, "node_id"),
                                           f"node_id 重复: {nid!r}", spec_id=sid))
        ids.append(nid)
        labels.append(str(node.get("label")))
    for i, edge in enumerate(edges):
        epath = pointer_child(path, "edges", i)
        for key in ("from_node", "to_node"):
            if str(edge.get(key)) not in ids:
                report.problems.append(problem(
                    "dangling_reference", AXIS_REFS, pointer_child(epath, key),
                    f"edge.{key} 指向未知节点 {edge.get(key)!r}", spec_id=sid))
        try:
            st, en = float(edge["start_sec"]), float(edge["end_sec"])
        except Exception:
            continue
        if not (0.0 <= st < en <= qdur + 1e-9):
            report.problems.append(problem(
                "edge_time_out_of_range", AXIS_TIME, pointer_child(epath, "start_sec"),
                f"edge 时间窗 [{st}, {en}) 必须半开且落在量化时长 {qdur}s 内", spec_id=sid))
    if not isinstance(relation, Mapping):
        report.problems.append(problem(
            "map_relation_source_missing", AXIS_MAP, pointer_child(path, "source_locator"),
            "map 必须有明确的关系来源（显式 fixture / 人工关系对象），不得由地名猜测",
            spec_id=sid))
        return
    rel_labels = {str(n.get("label")) for n in (relation.get("nodes") or [])
                  if isinstance(n, Mapping)}
    rel_pairs = {_pair(e) for e in (relation.get("edges") or []) if isinstance(e, Mapping)}
    mystery = [lb for lb in labels if lb not in rel_labels]
    if mystery:
        report.problems.append(problem(
            "map_label_not_in_relation_source", AXIS_MAP, pointer_child(path, "nodes"),
            f"节点标签不在关系来源中（不得自动映射真实地图）: {mystery}",
            spec_id=sid, unknown=mystery))
    extra = {_pair(e) for e in edges} - rel_pairs
    if extra:
        report.problems.append(problem(
            "map_edge_not_in_relation_source", AXIS_MAP, pointer_child(path, "edges"),
            f"edges 含关系来源中不存在的连线: {sorted(extra)}", spec_id=sid,
            unknown=sorted("->".join(p) for p in extra)))


def _pair(edge: Mapping[str, Any]) -> tuple[str, str]:
    return tuple(sorted((str(edge.get("from_node")), str(edge.get("to_node")))))


def _check_glyphs(report: ValidationReport, spec: Mapping[str, Any],
                  font: Mapping[str, Any] | None, path: str, sid: str,
                  asset_root: Path | str | None) -> None:
    if font is None or asset_root is None:
        return
    chars = set(str(spec.get("source_text") or ""))
    for node in spec.get("nodes") or []:
        if isinstance(node, Mapping):
            chars.update(str(node.get("label") or ""))
    if not chars:
        return
    target = Path(asset_root) / str(font.get("path"))
    if not target.is_file():
        return  # asset_missing 已报
    try:
        covered = font_codepoint_set(target)
    except FontError as exc:
        report.problems.append(problem(
            "font_unreadable", AXIS_FONTS, pointer_child(path, "font_asset_id"),
            f"字体无法解析，字形覆盖无法证明（fail-closed）: {exc}",
            spec_id=sid, font_path=str(target)))
        return
    missing = sorted(cp for cp in (ord(c) for c in chars) if cp not in covered)
    if missing:
        report.problems.append(problem(
            "missing_glyph", AXIS_FONTS, pointer_child(path, "font_asset_id"),
            "字体缺少所需字形（禁止静默 fallback）: " + "".join(chr(cp) for cp in missing[:20]),
            spec_id=sid, missing_codepoints=[f"U+{cp:04X}" for cp in missing[:20]],
            missing_total=len(missing)))


def _check_overlay(report: ValidationReport, ov: Mapping[str, Any], *,
                   specs_by_id: Mapping[str, Mapping[str, Any]], table: Mapping[str, Any],
                   path: str, source_kind: str) -> None:
    sid = str(ov.get("spec_id"))
    spec = specs_by_id.get(sid)
    if spec is None:
        report.problems.append(problem(
            "dangling_reference", AXIS_REFS, pointer_child(path, "spec_id"),
            f"overlay 指向不存在的 spec: {sid!r}", spec_id=sid, declared=sorted(specs_by_id)))
        return
    if str(spec.get("template")) == TEMPLATE_MAP:
        report.problems.append(problem(
            "map_not_overlayable", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
            "map 模板只能导出隔离单片，禁止加入 Step04 overlays", spec_id=sid))
    try:
        shot_index = int(ov["shot_index"])
    except Exception:
        return
    max_index = table.get("max_index")
    if max_index is None:
        pass
    elif shot_index == 0:
        if not table.get("has_hook"):
            report.problems.append(problem(
                "hook_identity_missing", AXIS_OVERLAYS, pointer_child(path, "shot_index"),
                "shot_index=0 是 hook 的独立身份；当前来源未声明 hook，不能当第一正文镜头",
                shot_index=0, has_hook=False))
    elif shot_index > max_index:
        report.problems.append(problem(
            "shot_index_out_of_range", AXIS_OVERLAYS, pointer_child(path, "shot_index"),
            f"shot_index {shot_index} 超出当前 revision 的镜头范围 0..{max_index}",
            shot_index=shot_index, max_index=max_index,
            shot_indexes=list(table.get("indexes") or [])))
    try:
        st, en = float(ov["start_sec"]), float(ov["end_sec"])
    except Exception:
        return
    if not (0.0 <= st < en):
        report.problems.append(problem(
            "overlay_time_invalid", AXIS_TIME, pointer_child(path, "start_sec"),
            f"overlay 时间窗必须半开且非负: [{st}, {en})", overlay_id=str(ov.get("overlay_id"))))
        return
    try:
        expected = quantized_duration_sec(float(spec["duration_sec"]), MERGE_FPS)
    except Exception:
        return
    tol = OVERLAY_DURATION_TOLERANCE_FRAMES / float(MERGE_FPS)
    if abs((en - st) - expected) > tol + 1e-9:
        report.problems.append(problem(
            "overlay_duration_mismatch", AXIS_OVERLAYS, path,
            f"overlay 时长 {en - st:.4f}s 必须等于 spec 量化时长 {expected:.4f}s"
            f"（容差 {OVERLAY_DURATION_TOLERANCE_FRAMES} 帧）",
            overlay_id=str(ov.get("overlay_id")), spec_id=sid,
            actual_sec=round(en - st, 6), expected_sec=round(expected, 6)))


__all__ = [
    "ValidationReport", "validate_motion_bundle", "layout_spec", "wrap_text",
    "resolve_locator", "match_declared_shape", "leaf_shapes_for", "shot_table",
    "font_codepoint_set", "FontError", "glyph_check_available",
    "load_fixture_doc", "load_bundle_with_sources", "source_doc_sha256", "fixture_doc_sha256",
    "leaf_offset",
    "SpecLayout", "SegmentLayout", "LayoutLine",
    "estimate_char_width_em", "OVERLAY_DURATION_TOLERANCE_FRAMES",
    "AXIS_SCHEMA", "AXIS_SOURCE", "AXIS_ASSETS", "AXIS_LOCATOR", "AXIS_SEGMENTS",
    "AXIS_TIME", "AXIS_LAYOUT", "AXIS_FONTS", "AXIS_REFS", "AXIS_OVERLAYS",
    "AXIS_CANVAS", "AXIS_MAP",
]
