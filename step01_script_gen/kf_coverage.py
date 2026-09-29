"""关键帧 ↔ motion 时段覆盖的**唯一判据**（2026-09-27，用户裁决）.

## 口径

1. **每个明确动作时段必须有独立关键帧**；帧数 **≥ 时段数**（`>=`，不是「恰好相等」）
   —— 额外帧只要有叙事价值就允许。CLAUDE.md v6.33 的源头规则本来就是这一条
   （`kf 个数 ≥ 时段数`），`validation._check_temporal_plan` 的 docstring 也已写明
   「只要求 kf 个数 >= 时段数，没要求逐个时段恰好一帧」；只有 `kf_event_order`
   的计数判据写成了 `!=`。
2. **一帧只归属一个明确事件**：边界帧（t 落在两段交界）**不得**同时充当相邻两段的
   证据。归属规则：在时间窗命中的段中取**离段中点最近**的（同距取靠前的段）——
   与 `quality_issues` 的 `kf_event_window_mismatch` 既有约定一致，确定性、可复核。

## 为什么要有这个模块

同一件事此前有**两份口径不同**的实现，于是同一稿可以「一个说缺段、一个说已覆盖」：

  · `constraints/kf_event_order`：`len(kfs) != len(segments)`（恰好相等）+ 一帧一段归属；
  · `validation._check_kf_time_window_alignment`：`covered_segments` 里**边界帧同时
    覆盖相邻两段**（原注释：「允许段边界帧同时覆盖相邻两段，避免 t_sec=1.0 这种
    合法边界被误报成'缺段'」）。

2026-09-27 冻结稿（`snapshots/script_result_20260927_022149.json`）实测：shot 6/7 的
2-4s 段，约束侧报「缺少可证明对应的关键帧」、校验侧判「已覆盖」——两边都有理，因为
口径不同。用户裁决「保留每段有帧、一帧只归属一个事件、帧数 ≥ 段数」后，抽出本模块，
两个检查器 + 质量门的错位判据共用，不再各自维护。

## 帧时刻的"时间"与"内容"是两件事（不得混报）

旧 `kf_event_order` 把「最佳匹配段不在窗口内」报成「关键帧时间 t 不在任何 motion 时段内」
——文案说的是**时间**问题，实际是**内容**错位（帧文案描写的是别的时段的事件）。
本模块把两者分开：`in_window`（时间）与 `mismatched`（内容），各报各的。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from shared import motion_segments as _ms
from shared.kf_time import get_kf_t_sec

#: 时间窗容差（与既有实现一致）
_WINDOW_TOL = 0.01

#: 内容错位判据（与 `quality_issues.kf_event_window_mismatch` 既有阈值**逐字相同**，
#: 避免统一判据时顺带改严重度）
MISMATCH_MIN_SCORE = 0.45
MISMATCH_MARGIN = 0.12

#: 「内容对应已核实」的下限——**沿用既有的 0.45**（不新造数字）。它只用于
#: `KfSlot.content_ok` / `KfCoverage.content_unverified` 的**记录**；内容维度的
#: 阻塞范围待判别样例定案（2026-09-27 复核 2-3），本轮不改门。
CONTENT_MIN_SCORE = MISMATCH_MIN_SCORE


def segments(motion: str, total: float) -> list[tuple[float, float, str]]:
    """解析 motion 为 `[(start, end, body), ...]`（body 已剥时间标签）."""
    out: list[tuple[float, float, str]] = []
    for atom in _ms.atoms(motion or ""):
        rng = _ms.atom_range(atom, total)
        if rng is None:
            continue
        a, b = rng
        a = max(0.0, a)
        if total > 0:
            b = min(max(0.0, b), total)
        if b <= a:
            # 零长度/退化区间**与 `parse_segments` 同口径丢弃**（都不进时段表）：
            # 否则同一份稿会出现「时段表里没有这一段、覆盖检查里却有这么一段」
            # （2026-09-27 复核 2-1）。标签本身是否合法由 `head_label` 判并上报。
            continue
        out.append((a, b, _ms.strip_time_label(atom) or atom))
    return out


def event_text(desc: str) -> str:
    text = str(desc or "")
    text = text.split("【角色锚定】", 1)[0]
    text = re.sub(r"（本帧[^）]*）", "", text)
    return text.strip()


def clean(s: str) -> str:
    text = re.sub(r"[（(][^）)]*[）)]", "", str(s or ""))
    return re.sub(r"[，。；！？、：:（）()【】\s]", "", text)


def event_overlap(desc: str, body: str) -> float:
    d = clean(event_text(desc))
    b = clean(body)
    if not d or not b:
        return 0.0
    ngrams = {b[i:i + 2] for i in range(max(0, len(b) - 1))}
    if not ngrams:
        return 0.0
    return sum(1 for ng in ngrams if ng in d) / len(ngrams)


def match_score(desc: str, body: str) -> float:
    """帧文案与某段正文的相似度（0-1）."""
    return max(
        difflib.SequenceMatcher(None, clean(event_text(desc)), clean(body)).ratio(),
        event_overlap(desc, body),
    )


def in_window(t, a: float, b: float) -> bool:
    """双端闭区间（±tol）——只用于「是否提到该区间」这类粗判，**不用于归属**."""
    return t is not None and a - _WINDOW_TOL <= t <= b + _WINDOW_TOL


def own_segment(t, segs) -> "int | None":
    """`t` 落在哪个时段 → 段索引；不在任何时段内 → `None`.

    **半开区间 `[start, end)`、末段闭合终点**——与事件表的事实源
    `shared.motion_segments.event_of()` **同一规则**（其冻结用例已规定
    `0-2秒` / `2-4秒` 的 `t=2.0` 归**后**段）。2026-09-27 复核 2-2：本模块此前用
    「双端闭区间 + 离中点最近」，同一稿会在事件表与覆盖检查里得到**相反**归属。

    `_WINDOW_TOL` 只在严格窗口全部落空时作浮点误差兜底，不参与「相邻段谁优先」。
    """
    if t is None or not segs:
        return None
    last = len(segs) - 1
    for i, (a, b, _body) in enumerate(segs):
        # 2026-09-27（复核三轮 1）：末段**两端都判**——原写成 `t <= b`，于是段间
        # 空档（`0-2s、4-6s` 的 `t=3s`）会被归给末段，凭空消掉一次「无归属」。
        if a <= t < b or (i == last and a <= t <= b):
            return i
    for i, (a, b, _body) in enumerate(segs):
        if a - _WINDOW_TOL <= t <= b + _WINDOW_TOL:
            return i
    return None


def closeness(t, a: float, b: float) -> float:
    if t is None:
        return 0.0
    length = max(b - a, 0.1)
    return max(0.0, 1.0 - abs(t - (a + b) / 2.0) / length)


@dataclass(frozen=True)
class KfSlot:
    """一帧的归属分析."""

    index: int
    desc: str
    t_sec: "float | None"
    own: "int | None" = None        # **时间**归属段（半开区间，与 event_of 同规则）
    best: "int | None" = None       # 文案最匹配的段
    scores: list = field(default_factory=list)
    duplicate_of: "int | None" = None   # 若 own 已被更早的帧占住 → 那一帧的下标
    content_ok: bool = False        # 归属段的**内容对应**是否已核实（见 CONTENT_MIN_SCORE）

    @property
    def out_of_window(self) -> bool:
        return self.t_sec is not None and self.own is None

    @property
    def mismatched(self) -> bool:
        """内容更匹配另一段（时间在窗口内也照样算）——「时间」与「内容」分开报."""
        if self.own is None or self.best is None:
            return False
        if self.best == self.own or not self.scores:
            return False
        s_best = self.scores[self.best]
        s_own = self.scores[self.own]
        return s_best >= MISMATCH_MIN_SCORE and s_best >= s_own + MISMATCH_MARGIN


@dataclass(frozen=True)
class KfCoverage:
    segments: list          # [(a, b, body)]
    required: list          # 需要独立帧的段索引（body 非空）
    slots: list             # [KfSlot]
    assigned: dict          # seg_i -> kf_i（一帧一段；重复的不占）
    missing: list           # required 中没有任何帧**时间归属**的段索引
    shortfall: bool         # len(kfs) < len(required)
    # 「时间占位」与「内容对应」**分开记录**（复核 2-3）：缺段为零只证明时间上
    # 每段都有帧，不证明帧文案描写了该段动作。
    time_covered: list = field(default_factory=list)     # = sorted(assigned)
    content_unverified: list = field(default_factory=list)  # 有帧但内容对不上的段


def analyze(shot: dict, *, scene_prefix: str = "") -> KfCoverage:
    """分析一个镜头的 kf ↔ 时段覆盖 → `KfCoverage`.

    Args:
        shot: 镜头 dict（含 `motion_desc` / `keyframes` / `duration_sec`）
        scene_prefix: 可选，帧文案前置的场景前缀（如「照月湾，」）。给出时匹配前先
            剥掉——这是 `quality_issues` 侧的既有做法；约束侧不传（保持其原行为）。
    """
    motion = str(shot.get("motion_desc") or "")
    kfs = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
    try:
        total = float(shot.get("duration_sec") or shot.get("duration") or 0)
    except (TypeError, ValueError):
        total = 0.0
    segs = segments(motion, total)
    # 「是否需要独立帧」看**剥标签后的正文**是否为空：`segments()` 的 body 在剥完
    # 为空时会回退成整段（便于匹配），但纯标签段（`2-4秒：`）不该要求独立帧——
    # 与 `validation._split_motion_to_seg_texts` 同口径，避免两处再次分叉。
    required = []
    for _i, (_a, _b, _body) in enumerate(segs):
        if str(_ms.strip_time_label(_body) if _body else "").strip():
            required.append(_i)

    slots: list[KfSlot] = []
    for kf_i, kf in enumerate(kfs):
        desc = str(kf.get("desc") or "")
        t = get_kf_t_sec(kf, shot)
        desc_for_match = desc
        if scene_prefix and desc_for_match.startswith(scene_prefix):
            desc_for_match = desc_for_match[len(scene_prefix):]
        own = own_segment(t, segs)
        scores = [match_score(desc_for_match, body) for _a, _b, body in segs]
        best = None
        if scores:
            best = max(range(len(scores)), key=lambda i: (scores[i], -i))
        _content_ok = (own is not None and best == own
                       and scores[own] >= CONTENT_MIN_SCORE)
        slots.append(KfSlot(index=kf_i, desc=desc, t_sec=t, own=own, best=best,
                            scores=scores, content_ok=_content_ok))

    # 一帧一段：own 先到先得；后来者记 duplicate_of（仍**不**改任何字段）
    assigned: dict[int, int] = {}
    dup: dict[int, int] = {}
    for slot in slots:
        if slot.own is None:
            continue
        if slot.own in assigned:
            dup[slot.index] = assigned[slot.own]
            continue
        assigned[slot.own] = slot.index
    slots = [KfSlot(index=s.index, desc=s.desc, t_sec=s.t_sec,
                    own=s.own, best=s.best, scores=s.scores,
                    content_ok=s.content_ok,
                    duplicate_of=dup.get(s.index)) for s in slots]

    missing = [i for i in required if i not in assigned]
    # **按段聚合该段的全部帧**（含被判为重复的帧）：只要其中任意一帧的内容
    # 对得上，就算这一段的「内容对应已核实」。2026-09-27 复核：原实现用
    # `{s.own: s ...}` 且**滤掉了 duplicate_of 的帧**，于是「同段第二帧才是写对
    # 的那一帧」被漏掉——02:34 选稿 shot 3 的 1-3s 段（kf1 相似度 0.556）、
    # shot 9 的 1-3s 段（kf1 相似度 1.000）都被误记为「未核实」。
    _by_seg: dict = {}
    for _s in slots:
        if _s.own is not None:
            _by_seg.setdefault(_s.own, []).append(_s)
    content_unverified = [i for i in required
                          if i in _by_seg
                          and not any(_s.content_ok for _s in _by_seg[i])]
    return KfCoverage(
        segments=segs,
        required=required,
        slots=slots,
        assigned=assigned,
        missing=missing,
        shortfall=len(kfs) < len(required),
        time_covered=sorted(assigned),
        content_unverified=content_unverified,
    )


__all__ = [
    "KfSlot",
    "KfCoverage",
    "analyze",
    "segments",
    "match_score",
    "event_text",
    "clean",
    "event_overlap",
    "in_window",
    "own_segment",
    "closeness",
    "CONTENT_MIN_SCORE",
    "MISMATCH_MIN_SCORE",
    "MISMATCH_MARGIN",
]
