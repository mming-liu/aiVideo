"""约束系统的核心数据类型.

三个核心 dataclass + 1 个 Severity enum:
  - Violation: 检测到的违例（不一定被修复）
  - RepairRecord: 实际执行的修复记录（用于日志 + 回归测试断言）
  - RepairReport: 一次 apply_constraints 调用的总结果

Severity 决定违例的"严重度"——HARD 未修复则触发 regen（替代旧的
hard_fail_checks 白名单 line 3882）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(Enum):
    """约束违例严重度.

    INFO  = 仅记录，不上报
    WARN  = 报告给 repair feedback（LLM 下一轮可修复）
    HARD  = 报告 + 未修复则触发 regen
    """

    INFO = 1
    WARN = 2
    HARD = 3


@dataclass(frozen=True)
class Violation:
    """检测到的违例.

    Attributes:
        constraint_name: 哪个约束检测出来的（对应 Constraint.name）
        severity: 严重度
        shot_index: 镜头 index（None = script-level 或 hook）
        field: 违例字段名（'prompt' / 'ff_desc' / 'keyframes[2].desc' / None）
        message: 人/给 LLM 看的描述
        evidence: 触发违例的原文片段（用于日志）
        repaired: v6.62：检测后已确定性修复（违例留档供日志/regen 反馈，
            但不再计入 hard_unresolved——治「已修仍报 HARD」的循环）
    """

    constraint_name: str
    severity: Severity
    shot_index: Optional[int] = None
    field: Optional[str] = None
    message: str = ""
    evidence: str = ""
    repaired: bool = False


@dataclass(frozen=True)
class RepairRecord:
    """实际执行的修复记录.

    Attributes:
        constraint_name: 哪个约束做了修复
        shot_index: 镜头 index（None = script-level 或 hook）
        action: 动作类型（'append' / 'rewrite' / 'set_field' / 'split_shot' / 'clamp'）
        target: 修复目标（'prompt' / 'ff_desc' / 'lf_desc' / 'keyframes[N].desc'）
        before: 修改前文本（便于回归测试断言）
        after: 修改后文本
        detail: 修复说明（2026-09-03 现场压测补：约束代码侧普遍传 detail=... 报
            "__init__() got an unexpected keyword argument 'detail'"；与 violation.detail
            同语义，供回放/日志使用）
    """

    constraint_name: str
    shot_index: Optional[int]
    action: str
    target: str
    before: str = ""
    after: str = ""
    detail: str = ""


@dataclass
class RepairReport:
    """一次 apply_constraints 调用的总结果.

    Attributes:
        repairs: 实际执行的修复列表
        violations_remaining: 检测到但未修复的违例（含修复失败）
        duration_ms: 耗时（毫秒）
    """

    repairs: list[RepairRecord] = field(default_factory=list)
    violations_remaining: list[Violation] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def hard_unresolved(self) -> list[Violation]:
        """HARD severity 且未修复的违例（触发 regen）."""
        return [v for v in self.violations_remaining
                if v.severity == Severity.HARD and not v.repaired]

    @property
    def repairs_by_constraint(self) -> dict[str, int]:
        """每个约束的修复次数（用于日志汇总）."""
        out: dict[str, int] = {}
        for r in self.repairs:
            out[r.constraint_name] = out.get(r.constraint_name, 0) + 1
        return out


# ============================================================================
# 只读的「已授权效果」视图（总计划 §14 P1/P3，2026-10-03）
#
# 约束侧需要知道的**唯一**一件事：本轮修复已经被授权、已经落地地删掉了哪些
# 布局条目，以及那些条目**与在场人物的身份关系**是否明确。
#
# 为什么走 context 而不是让约束自己去查脚本：效果属于**本轮事务**，它不是稿件的
# 属性——写进生产分镜 schema 就变成了跨轮的持久事实（本包明令禁止）。视图因此是
# 冻结的、只读的，缺省为空（没有事务时约束行为**逐字不变**）。
# ============================================================================


@dataclass(frozen=True)
class RepairEffectView:
    """约束侧**只读**的已授权布局效果视图.

    只暴露做判断所需的最小事实：哪个目标、哪个帧、删了哪些条目，以及这些条目的
    人物绑定裁决（生产者显式给出关系时）。约束**不得**改它，也**不得**据此全局
    停用自己——按帧、按条目生效。

    Attributes:
        round_id: 轮次标识（证据用）
        source_revision: 效果所属稿件 revision
        effects: 效果记录（tuple of dict，**已冻结**）
        bindings: `{effect_key: 绑定记录}`（`repair_effects.ActorBinding.as_record()`）
    """

    round_id: str = ""
    source_revision: str = ""
    effects: tuple = ()
    bindings: dict = field(default_factory=dict)

    def _effects_on(self, target: str, frame_locator: str) -> list:
        out = []
        for e in self.effects or ():
            if not isinstance(e, dict):
                continue
            if str(e.get("shot_id")) != str(target):
                continue
            if str(e.get("frame_locator")) != str(frame_locator):
                continue
            out.append(e)
        return out

    @staticmethod
    def binding_key(target: str, frame_locator: str, entry_name: str = "") -> str:
        """绑定键：**目标＋帧＋条目**三者一起才算一个绑定.

        只按帧记一个关系，会让"这一帧上有一个条目关系明确"扩张成"该帧所有删除条目
        都不必重建"（2026-10-03 复核实测：只有「小厮→张十三」明确，独立的「群演」
        也拿到了放行）。
        """
        key = "%s|%s" % (target, frame_locator)
        return "%s|%s" % (key, entry_name) if entry_name else key

    def relation_of_entry(self, target: str, frame_locator: str, entry_name: str) -> str:
        """**这一条目自己**的绑定关系（没有任何绑定 → 空串，不借同帧其他条目）."""
        rec = (self.bindings or {}).get(
            self.binding_key(target, frame_locator, str(entry_name or "")))
        if isinstance(rec, dict) and rec.get("relation"):
            return str(rec["relation"])
        return ""

    def relation_of(self, target: str, frame_locator: str) -> str:
        """该帧上的**帧级**绑定关系（只作诊断；判定"不必重建"必须按条目查）"""
        for e in self._effects_on(target, frame_locator):
            rec = (self.bindings or {}).get("%s|%s" % (e.get("shot_id"),
                                                       e.get("frame_locator")))
            if isinstance(rec, dict) and rec.get("relation"):
                return str(rec["relation"])
        return ""

    def honored_removals(self, target: str, frame_locator: str,
                         remaining: list | None = None,
                         entity_names=None) -> frozenset:
        """该帧上**不得重建**的条目名.

        两个条件同时成立才生效（§P3 第 1 行 + 2026-10-04 复核 P1-6）：
        ① 该条目与已在场人物**明确是同一实体**（类别别名只说明类别）；
        ② **修复后帧里仍有另一个条目承载这个实体**——"重复条目清理"要由**保留的
           那一条**证明。只凭被删条目在基线里认出自己，等于把"独立角色"也当成重复，
           于是把它删掉后连重建都被禁止。

        ``remaining`` / ``entity_names`` 缺省时不启用条件②（旧调用方契约），
        调用方（`group_actor_continuity`）会给两者。
        """
        names = set()
        for e in self._effects_on(target, frame_locator):
            for entry in e.get("removed_entries") or ():
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                name = str(entry["name"])
                rec = (self.bindings or {}).get(
                    self.binding_key(target, frame_locator, name))
                if not isinstance(rec, dict) or rec.get("relation") != "same_entity":
                    continue
                if remaining is not None:
                    carried = set(map(str, remaining)) - {name}
                    aliases: set = set()
                    entity = str(rec.get("entity") or "")
                    if entity:
                        # 绑定里的实体名**本身就是一种写法**：留下条目直接用这个名字
                        # 就已证明承载；别名表只是补充（人物表缺席时也能判）
                        aliases = {entity}
                        if entity_names is not None:
                            aliases |= set(map(str, entity_names(entity)))
                    if not (carried & aliases):
                        continue          # 没有别的条目承载它 ⇒ 这一条不是"重复"
                names.add(name)
        return frozenset(names)

    def has_effect(self, target: str, frame_locator: str) -> bool:
        """该帧上有没有本轮的效果记录（第 6 行：没有记录 ⇒ 一切照旧）."""
        return bool(self._effects_on(target, frame_locator))


def get_repair_effects(ctx):
    """从约束上下文取**只读**效果视图；没有（正常生产/单测直接构造 ctx）返回 `None`.

    走 helper 而不是在 34 个约束里各自 `getattr`：视图字段名只有一处定义，
    缺省语义（无事务 = 逐字不变）也只有一处。
    """
    view = getattr(ctx, "repair_effects", None)
    return view if isinstance(view, RepairEffectView) else None
