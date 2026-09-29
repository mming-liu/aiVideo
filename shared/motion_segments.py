"""motion_desc 时间分段解析 —— **全仓唯一事实源**（v6.76，2026-09-19 统一）.

## 为什么有这个模块

统一前，同一件事（从 `motion_desc` 抽出时间段）在全仓有 **16 处互不一致的解析点**
（初版审计数出 11 处，收尾时又发现 5 处：下表后 5 行）：

| 位置 | 分隔符集 | 小数 | 「秒」可选 |
|---|---|---|---|
| `timecode._rewrite_motion_time` | `- ~ 至 到` | ✓ | ✓ |
| `timecode._split_motion_desc_at_cut` | `- ~ 至 到` | ✓ | ✓ |
| `timecode._extract_motion_duration` | `- – ~ 至 到` | ✓ | ✓ |
| `validation._KF_TIME_SEG_PATTERN` | `- － ~ ～` | ✓ | 第三支 |
| `validation._split_motion_to_seg_texts`（剥标签正则） | `- － ~ ～` | **✗** | ✗ |
| `split_continuity._rebase_motion_times` | `- ~ 至 到` | ✓ | ✓ |
| `split_continuity._match_segment` | `- ~ 至 到` | ✓ | ✓ |
| `motion_time_bounds._RANGE_RE` | `- ~ 至 到` | ✓ | ✓ |
| `kf_event_order._RANGE_RE` | `- ~ 至 到` | ✓ | ✓ |
| `quality_issues.has_explicit_time` | `- ~ 至 到` | ✓ | ✓ |
| `final_quality_normalizer`（复用 validation） | — | — | — |
| `motion_time_bounds._SINGLE_RE` | `- ~ 至 到`（单点式） | ✓ | 是 |
| `quality_issues` 第二支（提及式，:195） | `- ~ 至 到` | ✓ | 是 |
| `validation._TIME_SEGMENT_RE`（:2331，含 `共/时长N秒`） | `[-–]` | **✗** | 否 |
| `validation._TIME_SEG_RE`（:2355，局部同名 shadow） | `[-–]` | **✗** | 否 |
| `validation._COMBAT_TIMING_PATTERN`（:3038） | `[-–—]` | ✓ | 是 |
| `weapon_state_consistency._SEG_RE` | `- ~ 至 到` | ✓ | 是 |

两组分隔符**互不覆盖**：`至/到` 只有一半解析器认，全角 `－/～` 只有另一半认。
**小数**只有 `_split_motion_to_seg_texts` 的剥标签正则不认——`0-0.378378秒：…`
被它剥成 `.378378秒：…` 残片，再被写进 `keyframes[*].desc`
（实测 chapter_001 index 11.5）。这就是「残缺时间文本」的根因。

## 本模块提供

- `parse_segments()`：解析为 `[(start, end), ...]`（返回值恒满足 `0 <= start < end <= total`）
- `raw_atom_span()`：**唯一时间语法判定点**（前/后/范围三分支都经它）
- `out_of_window_atoms()`：列出完全越界的原子段（丢弃必须可观测）
- `split_segment_texts()`：切分每段正文（剥掉时间标签）
- `strip_time_label()`：剥掉单个原子段的时间标签前缀
- `extract_total_duration()`：从时间段推算总时长
- `rebase_motion_times()`：把整段文本的时间轴平移（拆镜用）
- `has_explicit_time()` / `mentions_duration()`：是否显式提到时段/时长
- `max_mentioned_second()`：文本提到的最大秒数（合并了两处重复实现）
- `build_motion_segments()` / `format_motion_segments()` / `count_effective_segments()`：step02 用的结构化封装

依赖方向：本模块**不 import step01**（此前 `build_motion_segments` 反向 import
`step01_script_gen.validation`，step02 用它等于 step02→step01 反向耦合）。
"""
from __future__ import annotations

import re
from typing import NamedTuple, TypedDict

# ===========================================================================
# 唯一正则事实源
# ===========================================================================
_NUM = r"\d+(?:\.\d+)?"
# 区间分隔符**并集**：半角连字符、全角连字符、en-dash、波浪号、全角波浪号、至、到
_SEP = r"[-－–~～至到]"
# 缺「秒」分支的三重保护（v6.79，2026-09-20）：
#
#   ① **必须落在原子段开头**（`_ATOM_START`）——裸范围没有单位，唯一的消歧依据
#      就是位置。`画面中1-2人向前走3-5步` 里的 `1-2`/`3-5` 都在句子中间，是
#      **人数和步数**，不是秒数；原实现用 `search()` 在整个原子段里找，把它们
#      当成 `(1.0, 2.0)` 写进时段表，`extract_total_duration` 进一步把镜头
#      时长改成 5 秒。（见 docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 4）
#   ② **尾部不得接量词/单位**（`_NOT_TIME_TAIL`）——原子段开头也可能是
#      「3-5人围坐」这类量词短语，位置判据拦不住，还要看后一个字符。
#   ③ **区间必须落在镜头时长内**（`parse_segments` 校验）——即使 ①② 都过，
#      `9-12秒：A` 出现在 4 秒镜头里也不是本镜的事件。
_NOT_TIME_TAIL = (
    r"(?![0-9.\-－–~～:：sS]"
    # 长度/面积/容量/人数/次数/时间单位——裸范围后面接这些词一律不是秒数
    r"|人|名|员|位|个|只|条|张|支|把|步|次|遍|声|阵|招|拳|脚|"
    r"岁|成|倍|层|间|句|字|尺|寸|丈|米|厘|毫|分|刻|天|日|月|年|"
    r"斤|两|钱|石|斗|升|贯|文|枚|颗|株|朵|片|面|艘|匹|头|尾|盏|杯"
    r")"
)
# 裸范围只认原子段开头（行首 / 空白 / 原子分隔符之后）
_ATOM_START = r"(?<![^;；。\s])"

# 四个分支（有序，前面的优先）：
#   1. 前N秒 / 后N秒
#   2. N-M秒 / N-Ms（带单位——有单位就允许出现在段中任意位置）
#   3. N-M（缺「秒」→ 只认段首 + 尾部不接量词）
#   4. N秒后（= **从第 N 秒起**；与分支 1 的「后N秒」= **末尾 N 秒**语义相反）
#
# 分支 4 是 2026-09-27 补的（工作包 A）：提示规定的三段式是
# `前X秒：…；X-Y秒：…；Y秒后：…`，而「Y秒后」在统一前**任何解析器都不认**——
# `parse_segments` 直接跳过它、`has_explicit_time` 也判 False，于是分段式 motion
# 的末段对整条时序链（时段表 / 覆盖检查 / 时长推算）完全隐形。
# 与分支 3 一样必须**段首锚定**：正文里的「三秒后他转身」不得冒充段首标签。
TIME_SEG_RE = re.compile(
    r"(?:前|后)\s*(" + _NUM + r")\s*秒"
    r"|(" + _NUM + r")\s*" + _SEP + r"\s*(" + _NUM + r")\s*(?:秒|s)"
    r"|" + _ATOM_START + r"(" + _NUM + r")\s*" + _SEP + r"\s*(" + _NUM + r")\s*" + _NOT_TIME_TAIL
    # 第 4 分支必须**显式 `+`**：上一行以变量（`_NOT_TIME_TAIL`）结尾，直接续一个
    # 字符串字面量会变成「两个表达式并置」→ SyntaxError（隐式拼接只在相邻
    # **字面量**之间成立）。组号仍是 6，前 5 组不变。
    + (r"|" + _ATOM_START + r"(" + _NUM + r")\s*秒\s*后")
)

# 剥时间标签前缀（保留正文）。**必须支持小数**——这是 .378378 残片的根因修复点。
TIME_LABEL_PREFIX_RE = re.compile(
    r"^(?:前|后)\s*" + _NUM + r"\s*秒\s*[:：]?"
    r"|^" + _NUM + r"\s*" + _SEP + r"\s*" + _NUM + r"\s*(?:秒|s)?\s*[:：]?"
    # 2026-09-27：「N秒后」是分支 4 的标签形态，剥离必须同源——
    # 否则 `strip_time_label("4秒后：他收手")` 会把「4秒后：」留在正文里。
    r"|^" + _NUM + r"\s*秒\s*后\s*[:：]?"
)

# 原子段切分（中文分号/句号）
_ATOM_SPLIT_RE = re.compile(r"[;；。]")

# 「前N秒」「第N秒」——**单点式**时间提及（不是区间）。
# `第N秒`（"the Nth second"）是时间点，语义上不属于区间解析器，
# 且实测产物 0 次、prompt 规范 0 次（纯防御性变体）。为不改变既有行为，
# 单独具名导出：`motion_time_bounds` 取「文本提到的最大秒数」时用它。
SINGLE_TIME_RE = re.compile(r"(?:前|第)\s*(" + _NUM + r")\s*秒")

# 最宽的时间提及检测（关键词可省）——`quality_issues` 判「这句话提到时间」用。
TIME_MENTION_RE = re.compile(r"(?:前|第)?\s*(" + _NUM + r")\s*秒")

# 时间标签的最大小数位（`:g` 会把 0.378378… 原样输出成 6 位有效数字，
# 造成「0-0.378378秒」这类残缺文本；统一在这里截到 2 位）
_TIME_DECIMALS = 2


def fmt_time(v: float) -> str:
    """格式化时间标签里的秒数（截到 2 位小数，去掉多余的 .0）."""
    r = round(float(v) + 0.0, _TIME_DECIMALS)
    if r == int(r):
        return str(int(r))
    return f"{r:.{_TIME_DECIMALS}f}".rstrip("0").rstrip(".")


def atoms(motion: str) -> list[str]:
    return [a.strip() for a in _ATOM_SPLIT_RE.split(motion or "") if a.strip()]


# ===========================================================================
# 基础解析
# ===========================================================================
def strip_time_label(atom: str) -> str:
    """剥掉原子段开头的时间标签，返回正文（无标签则原样返回）."""
    return TIME_LABEL_PREFIX_RE.sub("", atom or "").strip("，。；：: ")


def raw_atom_span(atom: str):
    """解析单个原子段 → `(kind, a, b)` 或 None.  kind ∈ {"pre","post","range"}.

    **本函数是全仓唯一的时间语法判定点**——`atom_range` / `parse_segments` /
    `extract_total_duration` / `split_segment_texts` 全部经它，不再各自持有正则
    （原实现 `extract_total_duration` 在同一模块内另维护一条更宽的裸范围正则，
    「统一事实源」实际有两套语法）。见审核发现 4。

    「前N秒」→ `("pre", 0, N)`；「后N秒」→ `("post", 0, N)`（起点由调用方按
    total 求，见 `atom_range`）；「N-M」→ `("range", N, M)`（未排序）；
    「N秒后」→ `("after", N, 0.0)`——**起点 = N、终点由 total 求**，与「后N秒」
    的「末尾 N 秒」是**相反语义**（b 位只作占位，消费者必须经 `atom_range`）。
    """
    m = TIME_SEG_RE.search(atom or "")
    if not m:
        return None
    if m.group(6) is not None:
        return ("after", float(m.group(6)), 0.0)
    if m.group(1) is not None and m.group(2) is None:
        kw = (atom or "")[m.start():m.end()]
        return ("pre" if "前" in kw else "post", 0.0, float(m.group(1)))
    if m.group(2) is not None and m.group(3) is not None:
        return ("range", float(m.group(2)), float(m.group(3)))
    return ("range", float(m.group(4)), float(m.group(5)))


def bare_range_atoms(motion_desc: str) -> list[str]:
    """列出**没有单位**的裸时间区间原子段（`2-5放下酒壶`）→ `[atom, ...]`.

    裸范围是早期自由文本的遗留写法。没有「秒/s」单位时，唯一的消歧依据是
    「落在原子段开头 **且** 尾部不接量词」（`TIME_SEG_RE` 分支 3）——
    `画面中1-2人向前走3-5步` 里的 `1-2`/`3-5` 因此在原实现里被当成秒数
    （审核发现 4）。

    审核要求「裸范围只作为 legacy warning，只报告迁移，不参与确定性写入」。
    完全拒绝会丢掉真实时段（实测 LLM 仍会产出这种写法），故保留兼容但让它
    **可观测**：本函数列出这些段，由 `validation._check_temporal_plan` 上报
    「请写成 `N-M秒`」，而不是让它们静默成为权威时间。
    """
    out: list[str] = []
    for atom in atoms(motion_desc or ""):
        m = TIME_SEG_RE.search(atom or "")
        # 分支 3 命中：group(4)/(5) 有值而 group(2) 为空 = 无「秒/s」单位
        if m is not None and m.group(4) is not None and m.group(2) is None:
            out.append(atom)
    return out


def atom_range(atom: str, total_sec: float):
    """解析单个原子段 → (start, end) 或 None.

    「前N秒」→ (0, N)；「后N秒」→ (total-N, total)；「N-M」→ (N, M)；
    「N秒后」→ (N, total)。
    `a > b` 时自动交换（`4-2秒` 视为 `2-4秒`）——**本函数保留这一 legacy 宽容**，
    「倒置区间」的报错在 `head_label()`（校验侧要看见倒置，写入侧仍照旧兼容）。
    """
    span = raw_atom_span(atom)
    if span is None:
        return None
    kind, a, b = span
    if kind == "pre":
        return (0.0, b)
    if kind == "post":
        return (max(0.0, total_sec - b), total_sec)
    if kind == "after":
        # 「N秒后」= 从第 N 秒起。终点必须有 total 才能定；无 total（≤0）时
        # **返回 None**，让调用方原样保留该段（`rebase_motion_times` 传 0.0）。
        return (a, total_sec) if total_sec > 0 else None
    if a > b:
        a, b = b, a
    return (a, b)


def parse_segments(
    motion_desc: str,
    total_sec: float,
    *,
    implicit_tail: bool = False,
    clamp: bool = True,
) -> list[tuple[float, float]]:
    """从 motion_desc 解析时段列表 `[(start_sec, end_sec), ...]`.

    Args:
        motion_desc: 动作文本
        total_sec: 镜头时长（「后N秒」与 clamp 需要）
        implicit_tail: 显式时段未覆盖到 `total_sec` 末尾时，是否自动补一个
            `(last_end, total_sec)` 段（间距须 > 0.5s）。
            **默认 False**——只有 KF 时段覆盖检查需要它（`validation`）。
        clamp: 是否把 end 夹到 `total_sec`。

    Returns:
        时段列表；无显式时段 → `[]`（让上层决定是否走全局检查）。

    v6.79（2026-09-20）**区间合法性**：返回值恒满足 `0 <= start < end <= total_sec`。
    原实现只做 `b = min(b, total_sec)` 而完全不看 start，于是
    `parse_segments("9-12秒：A", 4.0)` 返回 `[(9.0, 4.0)]`——一个**反向区间**。
    它随后进入 `derive_temporal_plan()` 成为事件表的一行，而任何 KF 都不可能
    落进去，事件覆盖/排序判断随之失真（审核发现 4）。

    完全越界的原子段（起点已在镜头之外）**丢弃**，由 `out_of_window_atoms()`
    供上层上报，不在此静默改写数值。
    """
    if not motion_desc or total_sec <= 0:
        return []
    out: list[tuple[float, float]] = []
    for atom in atoms(motion_desc):
        rng = atom_range(atom, total_sec)
        if rng is None:
            continue
        a, b = rng
        if clamp:
            if a >= total_sec:
                # 起点已在镜头之外 → 不是本镜的事件，丢弃（上层可上报）
                continue
            b = min(b, total_sec)
        if b <= a:
            # 退化/反向区间一律不进时段表（`4-4秒`、越界夹取后的 `9-4` 都走这里）
            continue
        out.append((a, b))
    if implicit_tail and out:
        last_end = max(b for _, b in out)
        if last_end + 0.5 < total_sec:
            out.append((last_end, total_sec))
    return out


#: 段首标签区的**字符集**判据（不是「含数字」——那会误伤正文）：
#: 标签区**整段只由时间标签合法字符组成**（数字 / 小数点 / 区间分隔符 / 「秒s」/
#: 「前 后 第」/空白），且至少含一个数字或「秒」→ 这一段的**本意就是个时间标签**，
#: 只是没解析成功（残片）。
#:
#: 反例（必须**不**被判成残片）：`画面中1-2人向前走3-5步` 里有数字也有区间，但整段
#: 不是由标签字符组成，是正文——v6.79 用 `_NOT_TIME_TAIL` 专门保下来的场景，
#: 不能在新增解析器上被推翻。
_LABEL_CHARS = r"[0-9.\-－–~～至到秒sS前后第\s]+"
_LABEL_CHARS_RE = re.compile(_LABEL_CHARS)
_LABEL_HAS_NUM_RE = re.compile(r"\d|秒")


class HeadLabel(NamedTuple):
    """段首锚定的时间标签解析结果（`head_label()` 的返回值）.

    Attributes:
        atom: 原始原子段（未改动）
        label: 段首时间标签原文（无标签 → `""`）
        span: 标签在 `atom` 中的 `[start, end)` 下标（无标签 → `(-1, -1)`）
        kind: `"pre"`（前N秒）| `"post"`（后N秒，末尾 N 秒）|
            `"after"`（N秒后，从 N 秒起）| `"range"`（N-M[秒]）| `"none"`
        start / end: 解析出的秒数区间（`"after"` 的 end = total_sec；
            无标签或不可解析 → None）
        error: `""` | `"unparseable"`（**整段只由标签字符组成**但解析不了，
            即残片；正文里的数字不算）|
            `"reversed"`（区间倒置）| `"zero_length"`（起止相等，不是时段；
            **任何标签类型**都判，`前0秒`/`后0秒`/`2-2秒` 一视同仁）|
            `"negative"` | `"out_of_range"`
            （**整段**落在镜头时长之外；部分越界是既有 clamp 语义，不算错）
    """

    atom: str
    label: str
    span: tuple
    kind: str
    start: "float | None"
    end: "float | None"
    error: str


def head_label(atom: str, total_sec: float = 0.0) -> HeadLabel:
    """解析**段首锚定**的时间标签 → `HeadLabel`（`head_label` 的单一入口）.

    为什么需要它（2026-09-27 工作包 A）：`motion_time_integrity` 此前自带一条
    **只认区间**的正则，于是提示规定的 `前X秒：…` 被判成「时间前缀非法或损坏」，
    而且因为提示强制中段写 `X-Y秒`，那条件恒真——**每一镜必被误报**。判据必须
    与本模块同一套语法（`TIME_SEG_RE`），不再各持一份。

    与 `raw_atom_span()` 的两点区别：
      - **段首锚定**：只用 `.match()`（等价于要求标签落在原子段开头），正文中的
        时间词不冒充标签；
      - **返回解析错误**（`error`）：倒置区间在这里**报错**而不是像 `atom_range`
        那样静默交换——校验要看得出倒置，写入仍走 `atom_range` 的 legacy 宽容。
    """
    raw = atom or ""
    head = raw.lstrip()
    off = len(raw) - len(head)
    m = TIME_SEG_RE.match(head) if head else None
    if m is None:
        # 无段首标签：形状像标签（标签区含数字/「秒」）→ 残片；否则是纯正文。
        _zone = head.split("：", 1)[0].split(":", 1)[0]
        _is_label_shape = (bool(_zone)
                           and bool(_LABEL_CHARS_RE.fullmatch(_zone))
                           and bool(_LABEL_HAS_NUM_RE.search(_zone)))
        err = "unparseable" if _is_label_shape else ""
        return HeadLabel(raw, "", (-1, -1), "none", None, None, err)
    label = m.group(0)
    span = (off + m.start(), off + m.end())

    if m.group(1) is not None and m.group(2) is None:
        n = float(m.group(1))
        if "前" in label:
            kind, start, end = "pre", 0.0, n
        else:  # 「后N秒」= 末尾 N 秒
            kind = "post"
            start, end = ((max(0.0, total_sec - n), total_sec)
                          if total_sec > 0 else (None, None))
    elif m.group(6) is not None:  # 「N秒后」= 从第 N 秒起
        kind = "after"
        n = float(m.group(6))
        start, end = (n, total_sec) if total_sec > 0 else (n, None)
    else:
        kind = "range"
        start = float(m.group(2) if m.group(2) is not None else m.group(4))
        end = float(m.group(3) if m.group(3) is not None else m.group(5))
        if start > end:
            return HeadLabel(raw, label, span, kind, start, end, "reversed")

    # 零长度（起止相等）**不是合法时段**：`parse_segments` / `kf_coverage.segments`
    # 都会丢弃它（`b <= a` → 不进时段表），故段首校验必须同样判失败——否则同一份
    # 稿会出现「标签合法但段不存在」的分叉。复核 2-1 先只覆盖了区间写法（`2-2秒`）；
    # 复核三轮 2 指出 `前0秒：` 仍返回无错误、`后0秒` 还错报越界，故判定移到
    # **所有类型算完 start/end 之后**统一做（顺序：reversed → zero_length →
    # negative → out_of_range）。
    if start is not None and end is not None and start == end:
        return HeadLabel(raw, label, span, kind, start, end, "zero_length")

    if start is not None and start < 0:
        return HeadLabel(raw, label, span, kind, start, end, "negative")
    if total_sec > 0 and start is not None and start >= total_sec:
        # **整段**在镜头之外（部分越界是既有 clamp 语义，不算错）
        return HeadLabel(raw, label, span, kind, start, end, "out_of_range")
    return HeadLabel(raw, label, span, kind, start, end, "")


def overlapping_labels(labels, *, tol: float = 0.05) -> list:
    """列出**严格重叠**的标签对 → `[(HeadLabel, HeadLabel), ...]`.

    首尾相接（`(2,4)` 与 `(4,6)`）不算重叠；只比较可解析且未报错的标签。
    """
    ok = [l for l in (labels or [])
          if l.kind != "none" and not l.error
          and l.start is not None and l.end is not None and l.end > l.start]
    out: list = []
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            a, b = ok[i], ok[j]
            if a.start < b.end - tol and b.start < a.end - tol:
                out.append((a, b))
    return out


def out_of_window_atoms(motion_desc: str, total_sec: float) -> list[tuple[str, float, float]]:
    """列出**完全落在镜头时长之外**的原子段 → `[(atom, start, end), ...]`.

    `parse_segments` 会丢弃这些段（它们不是本镜的事件），但丢弃必须可观测：
    校验层用本函数把它们报成 `motion_time` / `temporal_plan` 问题，而不是让
    时长被静默扩写或事件表被静默污染。仅 `start >= total_sec` 的段算「完全越界」；
    部分越界（`3-9秒` 落在 4 秒镜里）是正常的 clamp 场景，不在此列。
    """
    if not motion_desc or total_sec <= 0:
        return []
    out: list[tuple[str, float, float]] = []
    for atom in atoms(motion_desc):
        rng = atom_range(atom, total_sec)
        if rng is None:
            continue
        a, b = rng
        if a >= total_sec:
            out.append((atom, a, b))
    return out


def split_segment_texts(motion: str, segs: list, dur: float) -> list[str]:
    """按 `segs` 顺序切分 motion_desc 的段正文（剥掉时间标签）.

    与 `parse_segments` 同用一套正则，故标签必能被剥净——不存在
    「解析器认小数、剥标签不认小数」的错配。
    """
    if not motion or not segs:
        return []
    out: list[str | None] = [None] * len(segs)
    for atom in atoms(motion):
        rng = atom_range(atom, dur)
        if rng is None:
            continue
        a, b = rng
        seg_idx = -1
        for i, (sa, sb) in enumerate(segs):
            if abs(sa - a) < 0.01 and abs(sb - min(b, dur) if dur > 0 else sb - b) < 0.01:
                seg_idx = i
                break
        if seg_idx < 0:
            continue
        body = strip_time_label(atom)
        if body and out[seg_idx] is None:
            out[seg_idx] = body
    return [x or "" for x in out]


def extract_total_duration(motion_desc: str) -> float | None:
    """从 motion_desc 推算总时长（无时段标记 → None）.

    并集法取最大值，避免单格式正则漏段：
      - 「前N秒」+「后M秒」求和
      - 「N-M秒」取最大终点
      - 「N秒后」取起点（末段开口，只能给出**下界**）
      - 「共N秒 / 时长N秒 / 总N秒」显式声明
    """
    if not motion_desc:
        return None
    # v6.79（09-20）：改走 `raw_atom_span` —— **唯一时间语法判定点**。
    #
    # 原实现另维护一条更宽的裸范围正则（`(?:秒|s)?` 可选 + 无段首/量词保护），
    # 于是 `extract_total_duration("画面中1-2人向前走3-5步")` 返回 5.0——把步数
    # 当成了秒数，并直接改写镜头 duration（`_compute_timecodes` /
    # `duration_budget` 都消费本函数）。「统一事实源」此前实际有两套语法。
    pre_sum = 0.0
    post_sum = 0.0
    range_max = 0.0
    for atom in atoms(motion_desc):
        span = raw_atom_span(atom)
        if span is None:
            continue
        kind, a, b = span
        if kind == "pre":
            pre_sum += b
        elif kind == "post":
            post_sum += b
        elif kind == "after":
            range_max = max(range_max, a)
        elif b > a:
            range_max = max(range_max, b)

    m = TOTAL_DURATION_RE.search(motion_desc)
    explicit = float(m.group("v")) if m else 0.0

    positives = [c for c in (pre_sum + post_sum, range_max, explicit) if c > 0]
    return max(positives) if positives else None


def has_explicit_time(text: str) -> bool:
    """文本是否含显式时段标记（「N-M秒」或「前/后/第N秒」）."""
    return bool(TIME_SEG_RE.search(text or ""))


def rebase_motion_times(text: str, offset: float) -> str:
    """把整段文本的时间轴平移 -offset（拆镜把全局时刻换成本地时刻用）.

    offset ≤ 0 或无时段 → 原样返回。跨 offset 的段不处理（由调用方切分）。
    """
    if not text or offset <= 0:
        return text
    out: list[str] = []
    for atom in atoms(text):
        rng = atom_range(atom, 0.0)
        if rng is None:
            out.append(atom)
            continue
        a, b = rng
        if a < offset - 0.01:
            out.append(atom)
            continue
        body = strip_time_label(atom)
        out.append(f"{fmt_time(max(0.0, a - offset))}-{fmt_time(max(0.1, b - offset))}秒：{body}")
    return "；".join(out)



def max_mentioned_second(text: str) -> float | None:
    """文本中提到的**最大秒数**（区间终点 ∪ 单点式「前/第N秒」）.

    用于「动作最晚提到第几秒」判定：
      - `motion_time_bounds`：超过镜头时长 → 延长镜头防生成端截断
      - `quality_issues._action_time_issues`：超过镜头时长 → 报 visual_risk

    原先这两处各写了一遍完全相同的提取逻辑（两套正则各自维护）。
    """
    vals: list[float] = []
    for m in TIME_SEG_RE.finditer(text or ""):
        if m.group(2) is not None:
            vals.append(float(m.group(3)))
        elif m.group(4) is not None:
            vals.append(float(m.group(5)))
        elif m.group(6) is not None:
            # 「N秒后」的 N 同样是「提到了第几秒」——不认它会让
            # motion_time_bounds 看不见末段标签，超出镜头时长也不延长。
            vals.append(float(m.group(6)))
    for m in SINGLE_TIME_RE.finditer(text or ""):
        vals.append(float(m.group(1)))
    return max(vals) if vals else None



# 「共N秒 / 时长N秒 / 总N秒」——显式总时长**声明**（不是区间标签，但同属"提到了时长"）
TOTAL_DURATION_RE = re.compile(r"(?:共|时长|总)\s*(?P<v>" + _NUM + r")\s*秒")


def mentions_duration(text: str) -> bool:
    """文本是否提到时长（区间标签 或 「共N秒」式总时长声明）.

    比 `has_explicit_time` 宽：后者只认区间/前N秒标签，本函数额外认总时长声明。
    `_check_ff_motion_timeline` 用它判「动作是否带时间分段」。
    """
    t = text or ""
    return bool(TIME_SEG_RE.search(t) or TOTAL_DURATION_RE.search(t))


# 「N-M[秒]：正文」整体结构（split_continuity 配对使用）
SEGMENT_RE = re.compile(
    r"(?P<range>" + _NUM + r"\s*" + _SEP + r"\s*" + _NUM + r"\s*秒?)"
    r"\s*[:：]\s*(?P<body>.*)"
)


def match_segment(text: str):
    """匹配「N-M[秒]：正文」→ re.Match（命名组 `range` / `body`），否则 None."""
    return SEGMENT_RE.match(text or "")



# ===========================================================================
# v6.77：结构化事件表（temporal_plan 的**派生**形态）
# ===========================================================================
class TemporalEvent(TypedDict):
    """单个事件（时间窗口 + 描述 + 原文出处）."""
    event_id: str          # "<shot_key>_e<n>"，从 1 起
    start_sec: float
    end_sec: float
    description: str       # 段正文（已剥时间标签）
    source_quote: str | None   # 原文出处；确定性派生拿不到 → None



# ===========================================================================
# 事件语义一致性（二次审核发现 5）
# ===========================================================================
# 运镜/景别词：段正文里这些词不承载「剧情内容」，比较前先剥掉——否则
# 「镜头缓缓推近至面部特写」与「张十三举刀」会被判成无共同内容（假阳性）。
_CAMERA_WORDS = (
    "大远景", "远景", "全景", "中近景", "中景", "近景", "特写", "大特写",
    "镜头", "推近", "推镜", "拉远", "摇镜", "平移", "跟拍", "固定镜头",
    "俯拍", "仰拍", "过肩", "缓推", "快推", "切换", "特写镜头", "空镜",
    "画面", "构图", "视角", "景别",
)
# 程度/方式副词：同样不承载剧情内容。剥掉它们是为了让「镜头缓缓推近」这类
# **纯镜头语言**的段正文在比较前归零——否则残余的「缓缓」会被当成内容，
# 把正常的运镜段误判成「与描述无共同内容」。
_MANNER_WORDS = (
    "缓缓", "慢慢", "轻轻", "渐渐", "微微", "稍稍", "徐徐", "缓缓地", "逐渐",
    "越来越", "始终", "一直", "最后", "随后", "继而", "然后",
)
# 静态方位/身体部位名词：是**场景事实**但不是**事件内容**。它们出现在段正文里
# 不构成「这段在讲一个动作」，因此在「有没有共同内容」的判定里同样归零。
_STATIC_NOUNS = (
    "面部", "脸上", "身上", "身后", "身前", "画左", "画右", "画中", "前方",
    "后方", "上方", "下方", "旁边", "周围", "面前", "背影", "轮廓", "方向",
    # 「没有动作在发生」的标记：段正文只写这些时，它本身不构成一个事件，
    # 不能拿它去证明描述「虚构」（例如 `镜头固定，水面平静`）。
    "固定", "静止", "平稳", "稳定", "平静", "静谧", "无声", "不动", "毫无",
    "保持一致", "持续", "保持", "空镜", "环境",
)
# 功能字（介词/助词/连词/副词）：不承载内容，逐字剥掉。
_FUNCTION_CHARS_RE = re.compile(r"[的地得了着至向朝往从在与和及并而就都也仍还其之]")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def _content_bigrams(text: str) -> set:
    """取**内容**二元组：剥掉运镜词后，每个 CJK 连续段的相邻两字组合."""
    t = str(text or "")
    for _w in _CAMERA_WORDS + _MANNER_WORDS + _STATIC_NOUNS:
        t = t.replace(_w, "")
    t = _FUNCTION_CHARS_RE.sub("", t)
    out = set()
    for run in _CJK_RUN_RE.findall(t):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def descriptions_conflict_with_body(description: str, body: str) -> bool:
    """事件描述与该段正文是否**完全没有共同内容** → 疑似虚构事件.

    这是一条**必要判据**（不是充分判据）：只用字符重合做「无共同内容」的判定，
    不做剧情蕴含推断。刻意保守——只有两段在剥掉运镜词之后连一个共同二元组都没有
    才判冲突，因此不会因为改写/同义表述误报，但能抓住「motion 写举刀收刀、
    plan 写凭空倒地/突然起火」这类凭空事件（审核发现 5 的最小复现）。

    任一侧内容不足 2 个 CJK 字（如「A」「收势」）时无法判定，返回 False。
    """
    a, b = _content_bigrams(description), _content_bigrams(body)
    if not a or not b:
        return False
    return not (a & b)


def validate_events(events, total_sec: float, *, tol: float = 0.05,
                    motion_desc: str | None = None) -> str:
    """校验 LLM 直出的事件表；返回空串=通过，否则返回拒绝原因.

    严格迁移口径（文档 §1）：「只允许从格式严格、无歧义的显式时间段迁移；
    解析失败就报 regen，**不能猜**」。所以这里**只判定合格与否**，
    不做补齐/排序/裁剪——不合格就整体回退到派生，并把原因暴露给检查器。

    v6.79（2026-09-20）**补全 schema 自己声明的不变量**。此前注释与
    `_TEMPORAL_PLAN` 的 description 都要求事件「按时间升序、首尾相接、覆盖
    `(0, duration]`」，实现却只查了「可转数值 / description 非空 / 单段不越界 /
    `end > start` / 不重叠倒序」。于是最小复现里，10 秒镜头的真实 motion 是
    `0-5 A；5-10 B`，LLM 只给 `2-3秒：编造事件, source_quote=""` ——
    覆盖率 10%、无出处的表**照样通过**，并被 `derive_temporal_plan()` 标成
    `source="llm"` 交给拆镜用（审核发现 10）。现在补三类硬判据：

      1. **整镜覆盖**：首段必须从 0 起、末段必须到 `total_sec`、相邻边界相接无空洞；
      2. **证据充分**：每个 `source_quote` 非空（迁移期不接受无出处事件）；
      3. **与 motion 一致**（传入 `motion_desc` 时）：段数与逐段边界必须对得上。

    Args:
        events: LLM 直出的 `events[]`
        total_sec: 镜头时长
        tol: 边界容差（秒）
        motion_desc: 可选。传入时额外校验事件表与 motion_desc 分段一致。
    """
    if not isinstance(events, list) or not events:
        return "events 为空或不是数组"
    if total_sec <= 0:
        return "缺少 duration_sec，无法校验事件窗口"
    prev_end = None
    bounds: list[tuple[float, float]] = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            return f"events[{i}] 不是对象"
        try:
            a = float(ev.get("start_sec"))
            b = float(ev.get("end_sec"))
        except (TypeError, ValueError):
            return f"events[{i}] 起止秒数非数值"
        if not str(ev.get("description") or "").strip():
            return f"events[{i}].description 为空"
        if not str(ev.get("source_quote") or "").strip():
            return f"events[{i}].source_quote 为空（迁移期不接受无原文出处的事件）"
        if a < -tol or b > total_sec + tol:
            return f"events[{i}] 窗口 [{a}, {b}] 越出镜长 {total_sec}"
        if b <= a:
            return f"events[{i}] 窗口非法（end <= start）"
        if prev_end is not None and a < prev_end - tol:
            return f"events[{i}] 与前一事件重叠或倒序（start {a} < 上一 end {prev_end}）"
        prev_end = b
        bounds.append((a, b))

    # ① 整镜覆盖：首段起 0、末段到 duration、中间无空洞
    if abs(bounds[0][0]) > tol:
        return f"事件表未从 0 开始（首段 start={bounds[0][0]:g}）"
    if abs(bounds[-1][1] - total_sec) > tol:
        return f"事件表未覆盖到镜末（末段 end={bounds[-1][1]:g} ≠ duration {total_sec:g}）"
    for i in range(1, len(bounds)):
        gap = bounds[i][0] - bounds[i - 1][1]
        if abs(gap) > tol:
            return (f"事件表在 events[{i - 1}]→events[{i}] 之间有空洞"
                    f"（{bounds[i - 1][1]:g} → {bounds[i][0]:g}）")

    # ② 与 motion_desc 一致：段数 + 逐段边界 + **逐段语义**
    if motion_desc:
        segs = parse_segments(motion_desc, total_sec, implicit_tail=True, clamp=True)
        if len(segs) != len(bounds):
            return (f"事件段数 {len(bounds)} 与 motion_desc 分段数 {len(segs)} 不一致"
                    "（两者必须一一对应）")
        for i, ((sa, sb), (ea, eb)) in enumerate(zip(segs, bounds)):
            if abs(sa - ea) > tol or abs(sb - eb) > tol:
                return (f"events[{i}] 边界 [{ea:g},{eb:g}] 与 motion_desc 第 {i + 1} 段"
                        f" [{sa:g},{sb:g}] 不一致")
        # v6.83（二次审核发现 5）：只比段数与边界不够——「边界相同但结果相反」的表
        # 照样通过。逐段比**内容**：描述与该段正文没有任何共同内容 = 疑似虚构事件。
        bodies = split_segment_texts(motion_desc, segs, total_sec)
        for i, ev in enumerate(events):
            body = bodies[i] if i < len(bodies) else ""
            desc = str(ev.get("description") or "")
            if descriptions_conflict_with_body(desc, body):
                return (f"events[{i}].description 与 motion_desc 第 {i + 1} 段正文"
                        f"**无任何共同内容**（疑似虚构事件）：{desc!r} vs {body!r}")
    return ""

def derive_temporal_plan(shot: dict) -> dict | None:
    """从 `motion_desc` **派生**结构化事件表.

    这是 `docs/step01-temporal-writers-audit-2026-09-19.md` 第二阶段
    「唯一时间事实源」的**派生形态**：不持久化、不写 shot，纯函数按需算。
    好处是立刻具备「事件身份 + 事件边界」的结构，供不变式校验使用；
    局限是它仍以 `motion_desc` 为准（LLM 直出 `temporal_plan` 需改 schema/prompt，
    属待决策项，见该文档「待确认事项」）。

    Returns:
        {"source": "derived", "events": [TemporalEvent, ...]}
        无时段标记 / 无 duration → None
    """
    motion = (shot.get("motion_desc") or "").strip()
    if not motion:
        return None
    dur = _get_duration(shot)
    if dur <= 0:
        return None
    key = shot.get("index")
    if key is None:
        key = "s"

    # 1) 优先采用 **LLM 直出**的事件表（须通过严格校验；不合格不修补，
    #    直接回退派生——「不能猜」）。
    #
    #    v6.79：校验必须带上 `motion_desc`——「事件表与 motion 分段一一对应」
    #    是 `_TEMPORAL_PLAN` 自己声明的契约，只在派生侧检查等于没检查：
    #    一段覆盖率 10%、无出处的 LLM 表曾据此被判合格并优先于完整 motion。
    llm_plan = shot.get("temporal_plan")
    if isinstance(llm_plan, dict):
        evs = llm_plan.get("events")
        if not validate_events(evs, dur, motion_desc=motion):
            return {
                "source": "llm",
                "events": [
                    {
                        "event_id": "%s_e%d" % (key, i),
                        "start_sec": float(ev["start_sec"]),
                        "end_sec": float(ev["end_sec"]),
                        "description": str(ev.get("description") or ""),
                        "source_quote": (str(ev.get("source_quote") or "") or None),
                    }
                    for i, ev in enumerate(evs, 1)
                ],
            }

    # 2) 回退：从 motion_desc **确定性派生**
    segs = parse_segments(motion, dur, implicit_tail=True, clamp=True)
    if not segs:
        return None
    texts = split_segment_texts(motion, segs, dur)
    return {
        "source": "derived",
        "events": [
            {
                "event_id": "%s_e%d" % (key, i),
                "start_sec": float(a),
                "end_sec": float(b),
                "description": (texts[i - 1] if i - 1 < len(texts) else "") or "",
                "source_quote": None,
            }
            for i, (a, b) in enumerate(segs, 1)
        ],
    }


def event_of(plan: dict | None, t_sec: float, *, tol: float = 0.01) -> dict | None:
    """返回 `t_sec` 落在的那个事件；不在任何窗口内 → None.

    v6.79（2026-09-20）**归属规则由「首位命中」改为半开区间 `[start, end)`**，
    最后一个事件闭合终点。

    原实现在双端闭区间上再加 `±tol` 容差，于是相邻事件 `[0,2]` 与 `[2,4]` 在
    `t=2.0` 处**同时命中**，函数按列表先后把边界时刻静默归给**前**一个事件。
    `derive_temporal_plan()` 派生的隐式尾段与前一段正是这种相接关系，实测把
    「收刀」KF 从自己所属的事件挪到了没有正文的空白尾段（审核发现 2/5）。

    半开区间下 `t=2.0` 归 `[2,4]`（2.0 是它的事件起点），符合直觉且与列表
    顺序无关。`tol` 只在所有严格窗口都落空时作为浮点误差兜底，不再参与
    「相邻窗口谁优先」的判定。
    """
    events = (plan or {}).get("events") or []
    if not events:
        return None
    last_i = len(events) - 1
    for i, ev in enumerate(events):
        try:
            a, b = float(ev["start_sec"]), float(ev["end_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        # 2026-09-27（复核三轮 1）：末段必须**两端都判**。原写成 `t <= b`，
        # 于是时段/事件之间有空档时（`0-2s、4-6s` 的 `t=3s`）会被归给末段。
        if a <= t_sec < b or (i == last_i and a <= t_sec <= b):
            return ev
    for ev in events:
        try:
            a, b = float(ev["start_sec"]), float(ev["end_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if a - tol <= t_sec <= b + tol:
            return ev
    return None

# ===========================================================================
# step02 用的结构化封装
# ===========================================================================
class MotionSegment(TypedDict):
    """单段时段结构."""
    start: float      # 起始秒数
    end: float        # 结束秒数
    text: str         # 该段动作描述（已剥离「前N秒：」标签）


def _get_duration(shot: dict) -> float:
    """从 shot 提取 duration_sec（兼容 timecode 字段）."""
    dur = shot.get("duration_sec")
    try:
        if dur and float(dur) > 0:
            return float(dur)
    except (TypeError, ValueError):
        pass
    tc = shot.get("timecode") or {}
    try:
        start = float(tc.get("start", 0) or 0)
        end = float(tc.get("end", 0) or 0)
        if end > start:
            return end - start
    except (TypeError, ValueError):
        pass
    return 0.0


def build_motion_segments(shot: dict) -> list[MotionSegment]:
    """从 shot.motion_desc 解析时间分段.

    返回:
      - []：motion_desc 为空 / 无时段标记 / duration 缺失
      - 1 段：仅 1 个显式时段（如「前2秒：拔剑」→ (0, 2)）
      - 2+ 段：显式分段时间表，自动补 implicit 末尾段

    幂等：纯函数，无副作用。
    """
    motion = (shot.get("motion_desc") or "").strip()
    if not motion:
        return []
    dur = _get_duration(shot)
    if dur <= 0:
        return []

    segs = parse_segments(motion, dur, implicit_tail=True)
    if not segs:
        return []
    texts = split_segment_texts(motion, segs, dur)
    return [
        {"start": a, "end": b, "text": (text or "").strip()}
        for (a, b), text in zip(segs, texts)
    ]


def format_motion_segments(segments: list[MotionSegment], style: str = "zh") -> str:
    """把分段列表格式化为可拼到 prompt 的字符串.

    Args:
        segments: build_motion_segments 的输出
        style:
          - "zh"：中文时段语法「0-2秒：拔剑；2-4秒：挥剑」 (默认，兼容 step01 原生格式)
          - "en_s"：Seedance 2.0 官方推荐「0-2s: 拔剑; 2-4s: 挥剑」 (含 's')

    Returns:
        多段拼接字符串（无前导标签），调用方决定加什么前缀。
        空段（text 为空）自动跳过。
    """
    if not segments:
        return ""
    parts: list[str] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        a, b = fmt_time(seg["start"]), fmt_time(seg["end"])
        if style == "en_s":
            parts.append(f"{a}-{b}s: {text}")
        else:  # zh / 默认
            parts.append(f"{a}-{b}秒：{text}")
    return "；".join(parts)


def count_effective_segments(segments: list[MotionSegment]) -> int:
    """统计有效段数（text 非空的段）.

    比 len(segments) 更准：implicit 补的段 text 为空，应被排除。
    """
    return sum(1 for s in segments if (s.get("text") or "").strip())


__all__ = [
    "MotionSegment",
    "TIME_SEG_RE",
    "TIME_LABEL_PREFIX_RE",
    "parse_segments",
    "split_segment_texts",
    "strip_time_label",
    "extract_total_duration",
    "has_explicit_time",
    "rebase_motion_times",
    "fmt_time",
    "atoms",
    "atom_range",
    "raw_atom_span",
    "HeadLabel",
    "head_label",
    "overlapping_labels",
    "out_of_window_atoms",
    "bare_range_atoms",
    "descriptions_conflict_with_body",
    "SINGLE_TIME_RE",
    "TIME_MENTION_RE",
    "SEGMENT_RE",
    "match_segment",
    "max_mentioned_second",
    "TemporalEvent",
    "derive_temporal_plan",
    "validate_events",
    "event_of",
    "TOTAL_DURATION_RE",
    "mentions_duration",
    "build_motion_segments",
    "format_motion_segments",
    "count_effective_segments",
]
