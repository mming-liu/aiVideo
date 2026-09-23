"""阶段屏障：时间字段写入跟踪与审计段守卫（v6.80，2026-09-20）.

对应 `docs/step01-temporal-writers-implementation-review-2026-09-19.md`
**发现 1（P0）** 的实施方案 #1 / #2 / #6。

## 发现 1 在说什么

约束链是「按注册顺序边检查边改写」的单遍流水线。五个 KF 时间检查注册在
序号 8-11，而当时四个时长写入者（`short_shot_merge` / `motion_time_bounds` /
`duplicate_shot_merge` / `duration_budget`）在序号 57-80。于是**链后写入能让
链前的检查失效**——「审计通过」不等于「最终时间轴通过同一组检查」，单镜 cap
夹取也可以被后续写入撤销。

这四个写入者已全部降为只报告。但**结构性缺口还在**：只要链上还存在任何
时间字段写入者，同类问题就会复发。本模块把这条不变量变成**可执行的守卫**，
而不是一条靠人工维持的注册顺序约定。

## 契约

约束链分三段（`AUDIT_SEGMENT_START` 是第 2/3 段的分界）：

1. **结构变更段**（审计段之前）：可以改镜头集合、字段结构，也可以在改写
   `motion_desc` 之后**同步重定基** `keyframes[*].t_sec`——那是坐标变换，不是
   语义改写（典型：`split_continuity` 把子镜的相对时间平移回父镜时间轴）。
2. **一次性时间派生段**：`timecode._compute_timecodes` /
   `write_timeline_from_durations` 是 duration 的**唯一权威推导点**；
3. **审计段**（`AUDIT_SEGMENT_START` 起）：**禁止再写任何时间字段**。

`SANCTIONED_TIME_WRITERS` 是允许在**审计段**写时间字段的白名单——目前只有
`runtime_extend` 的「单镜 > cap → 夹到 cap」（这是**减**时长、防 provider
截断内容，不是造时长）。其余任何约束在审计段写 `duration` / `duration_sec` /
`keyframes[*].t_sec` / `motion_desc` 都属越权。

**为什么要按位置而不是按名字**：v6.79 的第一版白名单只有 `runtime_extend`,
把「结构段的结构性重定基」和「审计段的语义改写」混成同一类——`split_continuity`
一旦真的重定基就会被判越权，而它做的是结构段该做的事。分界线必须由**链序**给出：
`observers` 在遇到 `AUDIT_SEGMENT_START` 之后才开始把写入记为审计段写入。

## 用法

```python
from shared.semantic_barrier import TimeFieldWriteTracker

tracker = TimeFieldWriteTracker()
run_hard_fix(script, on_constraint_done=tracker.observe)
assert tracker.unauthorized_writes() == []
```
"""
from __future__ import annotations

from typing import Any

from shared.quality_gate import (
    diff_semantic_payload,
    diff_semantic_structure,
    semantic_payload_digest,
)

# 参与守卫的时间字段（shot 级）
SHOT_TIME_FIELDS = ("duration", "duration_sec", "motion_desc")
# 允许在**审计段**写时间字段的白名单。
#
# v6.83（二次审核发现 1）：**已清空**。原先唯一成员 `runtime_extend`
# （「单镜 > cap → 夹到 cap」，理由是「减时长是安全的」）被证明不安全——
# 该约束位于 KF 时间检查**之后**，而缩短时长会改变所有时间坐标的合法域：
# 实测 12s 镜头被夹成 8s 后 `t_sec=10` 的 KF 留在最终对象中。cap 夹取属
# **时长派生**，只能发生在 KF 生成与检查之前（`timecode._compute_timecodes`
# 已在权威派生点按 `min(模型 cap, 8s)` 夹取）；链尾再夹一律越权。
#
# 保留这个空集合而不是删除它，是为了让「审计段允许哪些写入者」仍是一个
# 可审查的显式声明——将来若确有必要，必须连同「为什么可以在审计段写」的
# 证据一起加进来。
SANCTIONED_TIME_WRITERS: frozenset[str] = frozenset()
# 「审计段」起点：`ALL_CONSTRAINTS` 里第一个 KF 时间检查约束的名字。
# 它之前的写入属结构变更段（允许，另见 `structural_writes()`），
# 它及之后的写入属审计段（除白名单外一律越权）。
AUDIT_SEGMENT_START = "kf_time_window_alignment"
# **受豁免的结构写入者**：允许在**审计段**改动镜头集合（增删/重排）的约束。
#
# v6.99（2026-09-23 二次审核 P0-B）：**恢复为空集**。
#
# v6.98 曾把 `opening_empty_shot_remove`（开篇空镜删除）登记进来，理由是「那是
# 用户定的产品行为，先行为零变化 + 留证」。二次审核证明这条路同时踩两个坑：
#   1. **只豁免了结构检查，没豁免时间屏障**——删首镜让后续镜头按**列表位置**前移，
#      而时间快照正是按位置比较 → 同一动作既得 advisory、又得
#      `time_field_audit_write` HARD（实测：删 1 镜 → 20 条未授权时间写入）；
#   2. 就算把时间检查也一起豁免，**语义删除仍然无法证明等价**——被删镜的
#      prompt / ff / lf / motion / KF / 布局 / 道具随 `del` 一起消失，而判据只是
#      一条窄文本启发式。
# 用户决定（选项 B）：**两个空镜约束全部改为 report-only，不扩大任何白名单**。
# 因此这里恢复空集——「审计段允许哪些结构写入者」仍是一个可审查的显式声明，
# 将来若要加，必须连同「为什么这样删/改是等价的」证据一起加。
STRUCTURAL_SANCTIONED_WRITERS: frozenset[str] = frozenset()


def snapshot_shot_identity(script: dict, keepalive: list | None = None) -> list:
    """镜头**对象身份**序列（按列表位置）——结构红线的判定依据（v6.99，P1-A）.

    为什么不用 semantic digest 判结构：digest 会把「逐镜文本改写」（审计段硬修复层
    的设计行为）和「删一镜 + 加一镜」变成同一种现象——两者都让 digest 集合改变。
    v6.98 只靠 `count` 与 `set/order` 判定，于是**等数量替换完全不可见**
    （2026-09-23 二次审核 P1-A）。

    对象身份没有这个歧义：
      - 同一对象、字段被原地改写 → **content write**（允许）；
      - 对象消失 / 新增 / 被替换 → **structural write**（审计段禁止）；
      - 同一多重集、顺序不同 → **reorder**。

    `keepalive` 用来持有对象强引用：镜头被从列表里删掉后若被 GC，`id()` 可能被
    新插入的 dict 复用，从而把「删+增」误判成「原地改写」。持引用即可杜绝。
    """
    out: list = []
    for sh in (script.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        if keepalive is not None:
            keepalive.append(sh)
        out.append(id(sh))
    return out


def diff_shot_structure(ids_before: list, ids_after: list,
                        digests_before: list, digests_after: list,
                        *, max_items: int = 20) -> list[str]:
    """**结构红线**：结合「对象身份」与「内容多重集」判定镜头集合/顺序是否被动过.

    两个信号缺一不可（2026-09-23 二次审核 P1-A 的根因修法）：

    | 现象 | 身份 | 内容多重集 | 判定 |
    |---|---|---|---|
    | 同一对象原地改写字段 | 相同 | 不同 | content（允许，审计段硬修复层就是这么工作的） |
    | 删一镜 + 加一镜（总数不变） | 不同 | 不同 | **structural** ✅ 只有加身份才看得见 |
    | 纯重排 | 多重集相同、顺序不同 | 多重集相同、顺序不同 | **structural** |
    | 深拷贝重建（内容逐字相同） | 不同 | **相同** | content（语义没变，不报） |
    | 净数量变化 | — | — | **structural** |

    只看内容（v6.98 的做法）漏掉第 2 行——等数量替换与「逐镜改写」在 digest 层面
    长得一样；只看身份（v6.99 第一版）会把整份 deepcopy 当成全量替换。两条一起看
    才既分得开、又不误报。
    """
    out: list[str] = []
    if len(ids_before) != len(ids_after):
        out.append("镜头数 %d -> %d" % (len(ids_before), len(ids_after)))
    _same_ids = sorted(ids_before) == sorted(ids_after)
    _same_digests = sorted(digests_before) == sorted(digests_after)
    if _same_ids:
        if ids_before != ids_after:
            out.append("镜头顺序变化（对象相同、顺序不同）")
        # 顺序也没变 → 只是字段被改写 = content，不报
    elif not _same_digests:
        out.append("镜头对象增删/替换：消失 %d 个、新增 %d 个" % (
            len(set(ids_before) - set(ids_after)),
            len(set(ids_after) - set(ids_before))))
    return out[:max_items]


def _shot_key(shot: dict, fallback: int) -> Any:
    """镜头在快照里的标识 = **列表位置**（不是 `index`）.

    v6.84：`index` 会变——阶段 1 的 `shot_index_renumber` 把模型自造的半整数
    index 归一成连续整数。若用 `index` 当键，一次重编号会让每个镜头都显示成
    「新镜头」，于是每个字段都算「被写过」——屏障会满屏假越权。屏障关心的是
    「时间字段有没有被改」，位置才是同一镜的身份；`index` 只用于可读消息。
    """
    return fallback


def snapshot_time_fields(script: dict) -> dict:
    """快照全部时间字段（shot 级 + 每帧 t_sec）→ 可逐字段比较的 dict.

    Returns:
        {shot_key: {"duration":…, "duration_sec":…, "motion_desc":…, "kf_t": [...]}}
        另含 `"__hook__"` 键（若有 hook）。
    """
    out: dict = {}

    def _one(shot: dict, key: Any) -> None:
        if not isinstance(shot, dict):
            return
        kfs = shot.get("keyframes")
        out[key] = {
            # `_idx` 只用于消息可读性，**不参与比较**（见 `diff_time_fields`）
            "_idx": shot.get("index"),
            "duration": shot.get("duration"),
            "duration_sec": shot.get("duration_sec"),
            "motion_desc": shot.get("motion_desc"),
            "kf_t": ([k.get("t_sec") for k in kfs if isinstance(k, dict)]
                     if isinstance(kfs, list) else None),
        }

    for i, s in enumerate(script.get("shots") or []):
        _one(s, _shot_key(s, i))
    hook = script.get("hook")
    if isinstance(hook, dict):
        _one(hook, "__hook__")
    return out


def diff_time_fields(before: dict, after: dict, *, max_items: int = 20) -> list[str]:
    """比较两个 `snapshot_time_fields`，返回**实际发生**的时间字段写入（人可读）."""
    out: list[str] = []
    for key in sorted(set(before) | set(after), key=str):
        b, a = before.get(key) or {}, after.get(key) or {}
        for fld in ("duration", "duration_sec", "motion_desc", "kf_t"):
            if b.get(fld) != a.get(fld):
                _idx = a.get("_idx", b.get("_idx"))
                out.append(f"shot#{key}(index={_idx}).{fld}: "
                           f"{b.get(fld)!r} -> {a.get(fld)!r}")
                if len(out) >= max_items:
                    return out
    return out


class TimeFieldWriteTracker:
    """逐约束记录时间字段写入；用于断言「审计段无越权写入」.

    配合 `constraints.engine.apply_constraints(..., on_constraint_done=tracker.observe)`
    使用（`run_hard_fix` 会把该参数透传下去）。
    """

    def __init__(self, script: dict | None = None, *,
                 audit_start: str | None = AUDIT_SEGMENT_START) -> None:
        """
        Args:
            script: 起始脚本（给定后，第一次 `observe` 就能算出 diff）
            audit_start: 审计段起点的约束名。`None` = 整链都算审计段
                （旧行为，等价于把结构段写入也视为越权）。
        """
        self._snap: dict | None = snapshot_time_fields(script) if script else None
        self._audit_start = audit_start
        self._in_audit = audit_start is None
        # [(约束名, diff, 是否发生在审计段)]
        self.writes: list[tuple[str, list[str], bool]] = []
        # v6.98（审核 P0-3）：**结构**写入的独立记录。时间字段之外的
        # 「镜头增删 / 重排 / hook / characters」此前完全没有守卫。
        #
        # 只收**结构**红线，不收逐镜文本改写：审计段之后的硬修复层（71 个约束）
        # 本来就是靠写 `prompt`/`ff_desc`/`lf_desc` 工作的，把逐镜语义也算越权
        # 会让每次运行必然 BLOCK（那就是「自己造 blocking」）。逐镜改动进
        # `content_writes`，只做诊断。
        self._sem_snap: dict | None = (
            semantic_payload_digest(script) if script else None)
        # v6.99（P1-A）：结构判定用**对象身份**，不是 digest——digest 分不开
        # 「逐镜改写」与「等数量替换」。`_keepalive` 持引用防 `id()` 复用。
        self._keepalive: list = []
        self._ids_snap: list | None = (
            snapshot_shot_identity(script, self._keepalive) if script else None)
        self.semantic_writes: list[tuple[str, list[str], bool]] = []
        self.content_writes: list[tuple[str, list[str], bool]] = []

    def observe(self, constraint_name: str, script: dict) -> None:
        """每个约束执行后调用一次，记录它与上一状态之间的时间字段 diff."""
        # 起点判定在 diff **之前**：barrier 约束自身的写入也算审计段写入。
        if constraint_name == self._audit_start:
            self._in_audit = True
        cur = snapshot_time_fields(script)
        if self._snap is not None:
            d = diff_time_fields(self._snap, cur)
            if d:
                self.writes.append((constraint_name, d, self._in_audit))
        self._snap = cur
        # 语义/结构载荷（v6.98，审核 P0-3）：与时间字段**同一时点**取样，
        # 判定同一件事——「这个约束在审计段里改了不该改的东西」。
        _sem = semantic_payload_digest(script)
        if self._sem_snap is not None:
            # 结构红线：**对象身份**（等数量删+增/替换也看得见）+ 粗粒度身份
            # （hook 有无 / 角色集合）——后者 digest 层面才可判。
            _sd = diff_shot_structure(
                self._ids_snap or [],
                snapshot_shot_identity(script, self._keepalive),
                (self._sem_snap or {}).get("shots") or [],
                _sem.get("shots") or [])
            _sd += [x for x in diff_semantic_structure(self._sem_snap, _sem)
                    if x.startswith("hook") or x.startswith("角色集合")]
            if _sd:
                self.semantic_writes.append((constraint_name, _sd, self._in_audit))
            _cd = diff_semantic_payload(self._sem_snap, _sem)
            if _cd:
                self.content_writes.append((constraint_name, _cd, self._in_audit))
        self._sem_snap = _sem
        self._ids_snap = snapshot_shot_identity(script, self._keepalive)

    def writers(self) -> list[str]:
        """实际写过时间字段的约束名（去重，保序）——含结构段写入."""
        seen: list[str] = []
        for name, _, _ in self.writes:
            if name not in seen:
                seen.append(name)
        return seen

    def structural_writes(self) -> list[tuple[str, list[str]]]:
        """结构变更段的时间写入（允许，但必须可见——它们仍是「时间字段写入者」）."""
        return [(n, d) for n, d, in_audit in self.writes if not in_audit]

    def unauthorized_writes(self) -> list[tuple[str, list[str]]]:
        """越权写入：**审计段**内不在 `SANCTIONED_TIME_WRITERS` 的写入记录."""
        return [(n, d) for n, d, in_audit in self.writes
                if in_audit and n not in SANCTIONED_TIME_WRITERS]

    def audit_writes(self) -> list[tuple[str, list[str]]]:
        """审计段内的**全部**时间写入（含白名单）——单镜 cap 等最终不变量的依据."""
        return [(n, d) for n, d, in_audit in self.writes if in_audit]

    def semantic_structural_writes(self) -> list[tuple[str, list[str]]]:
        """审计段内的**语义/结构**写入（含受豁免者）——先于越权判定，用于留证."""
        return [(n, d) for n, d, in_audit in self.semantic_writes if in_audit]

    def content_writes_in_audit(self) -> list[tuple[str, list[str]]]:
        """审计段内的**逐镜文本改写**（含时间字段之外的语义改写）——**诊断，不判违规**.

        这些是硬修复层的设计行为（补景别、重建锚点、合规替换…），只用于回答
        「审计段到底还有多少写入者」。
        """
        return [(n, d) for n, d, in_audit in self.content_writes if in_audit]

    def unauthorized_semantic_writes(self) -> list[tuple[str, list[str]]]:
        """审计段内**未受豁免**的结构写入：镜头增删、重排、hook / characters 变更
        ——审核 P0-3 之前这类写入**没有任何守卫**."""
        return [(n, d) for n, d, in_audit in self.semantic_writes
                if in_audit and n not in STRUCTURAL_SANCTIONED_WRITERS]

    def sanctioned_semantic_writes(self) -> list[tuple[str, list[str]]]:
        """审计段内**受豁免**的语义/结构写入（如开篇空镜删除）——不阻塞，但必须留证."""
        return [(n, d) for n, d, in_audit in self.semantic_writes
                if in_audit and n in STRUCTURAL_SANCTIONED_WRITERS]


__all__ = [
    "SHOT_TIME_FIELDS",
    "SANCTIONED_TIME_WRITERS",
    "STRUCTURAL_SANCTIONED_WRITERS",
    "diff_semantic_payload",
    "diff_shot_structure",
    "snapshot_shot_identity",
    "diff_semantic_structure",
    "semantic_payload_digest",
    "AUDIT_SEGMENT_START",
    "snapshot_time_fields",
    "diff_time_fields",
    "TimeFieldWriteTracker",
]
