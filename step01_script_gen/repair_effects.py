# -*- coding: utf-8 -*-
"""修复事务内的**布局效果账本**与人物绑定裁决（总计划 §14 P 包，2026-10-03）.

## 本模块回答什么

`repair_fields` 回答「这次改动**有没有得到授权**」。本模块回答它**落地之后**的三件事：

1. **这次授权改动是什么**（`build_layout_effects`）——把「被逐字点名的帧子列表上的
   增删」登记成**请求应用事实**：删了哪条、加了哪条、留下了哪条、原授权目标是谁。
   ⚠️ 效果记录**不证明删除在故事中正确**——它只证明「这条改动被授权、被合并层应用了」。
2. **它有没有被确定性后处理抵消**（`RepairEffectLedger` 的四个冻结节点）——原候选 /
   授权合并后 / 每个后处理 writer 完成后 / 最终重验后。抵消意味着**两个机制打架**，
   而不是「模型没修好」。
3. **责任该判给谁**（`classify_effect_outcome`）——四类归因必须可区分：
   `postprocess_effect_reverted`（附责任 writer）/ `actor_presence_conflict` /
   `actor_binding_ambiguous` / `repair_capability_missing`；没有效果或没有抵消时
   **返回空 reason**，把结论原样交回既有判据（`no_change` / 协议拒收）。

## 为什么需要（固化的事实）

批次 `20261003T182636-d26992-001-564d9b`（第 21 轮）ch2：审计说「`小厮` 是张十三的
重复实体，删掉」→ 模型在 `frame_layouts.kfs[0]/[1]` 上**删对了**（授权与合并都通过）
→ 同一轮的 `group_actor_continuity` 按「文本提到群体」把它**加回来** → 验收看到
`frame_layouts` 与基线相同 ⇒ `字段触达=0` ⇒ `no_target_resolved` ⇒ 整轮回退 ⇒ BLOCK。

⇒ 完整因果链里没有一步是「模型没改」；整条链的失败却被记成了模型的问题。
本模块把这条链的**每一节**变成可读记录，并让**共同的写入机制**按人物关系裁决。

## 边界（不能越的线）

- 不建全书实体知识库；**不批量改旧人物 schema**；不靠名字或句式猜「同一人」。
- 人物关系属于**语义判断**：生产者给出关系与原文依据，代码只核证据定位、引用完整与
  冲突，并**只派生编号**。生产者没给关系时返回 `ambiguous`，把缺的字段反馈回去，
  而不是替它猜。
- 类别别名（`CHARACTER_ALIASES[name] == "群演"`）只说明**类别**，既不能证明它与已在场
  人物不同，也不能证明它相同。
- 效果记录**不得**混进生产分镜 schema，也不得成为全局永久删除黑名单——它只活在
  本轮事务的账本里。

## 四类归因的**承载层**（每个 code 的记录位置 / 字段路径 / 消费者 / 断言点）

| code | 记录位置（字段路径） | 消费者 | 断言点 |
|---|---|---|---|
| `postprocess_effect_reverted` | `_decision["reason"]` + `_decision["effect_conflict"].{mechanism,writer,first_revert}` → `step01_repair_rounds.json` 的 `effect_conflict` 字段 | 提交点的人读日志、离线回放、`retry_state["layout_conflict"]` | `test_frozen_revert_is_attributed_to_the_writer_not_to_the_model` / `test_commit_entry_a_*` |
| `actor_presence_conflict` | 同上（`reason` 取该类） | 同上 | `test_explicit_other_entity_is_presence_conflict` / `test_verdict_changes_when_the_relationship_changes` |
| `actor_binding_ambiguous` | 同上 | 同上 | `test_relation_field_present_but_empty_is_binding_ambiguous` |
| `repair_capability_missing` | 同上 + `outcome["needed_field"]`（`characters[].aliases`） | 同上；**补关系字段**的请求写给原生产者（见下） | `test_missing_relation_field_is_capability_missing` / `test_frozen_conflict_is_carried_into_the_next_request` |
| （既有）`no_change` / 协议拒收 | 本模块返回 `reason=""`，`_decision["reason"]` **原样保留** | 原路径不变 | `test_no_effect_returns_empty_reason_so_existing_verdicts_stand` |

**「返回原生产者」的出口**：本包内可达的是**修复通道**——冲突随
`_feedback_json["conflicts"]` 与 `_repair_turn` 的冲突段进入下一修复请求
（`render_conflict_feedback` 渲染，含 source_revision / 目标 / 绑定证据 /
请求→应用→撤销阶段 / 允许修改范围五项）。把 `characters[].aliases` 这条**窄协议**
写进审计输出 schema（`step01_script_gen/audit.py` 的 `repair_targets` 段与 Issue）
不在本包文件范围内，见交付报告的「跨文件需求」。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from shared.alias_map import CHARACTER_ALIASES

from .repair_fields import (
    _canon_entry,
    _entry_name,
    _frame_target_spelled,
    _layout_frame_lists,
    frame_layout_structural_violations,
    shot_by_target,
)


# ============================================================================
# 1. 常量与四类归因
# ============================================================================

# 四个冻结节点（§P1）。`writer` 在约束框架里是**每个约束一个节点**
# （engine 的 `on_constraint_done` 回调），其余后处理各自一个节点。
NODE_CANDIDATE = "candidate"        # 原候选
NODE_MERGED = "merged"              # 授权合并后（效果的基线）
NODE_WRITER = "writer"              # 每个后处理 writer 完成后
NODE_REVERIFY = "reverify"          # 最终重验后

# 修复事务里**全部**确定性后处理 writer 的显式清单（§P1「每个后处理 writer 完成后」）.
#
# 为什么必须枚举：只挂 `on_constraint_done` 覆盖不到——同一事务里还有四类直写：
# `style_prefix.inject_global_config_into_script`（注入全局渲染参数）、
# `write_timeline_from_durations`（写 duration_sec/timecode）、
# `validation.post_fix_shot_prompts`（写 prompt/motion_desc/narration_segment/
# subtitle/core_keywords）、伤处兜底（直写 `shot["prompt"]`）。少挂一个，抵消就会被
# 归到后一个 writer 头上（责任 writer 指错比不指更坏）。
# `constraints.run_hard_fix` 另按**每个约束**一个节点（engine 的 `on_constraint_done`），
# 因为抵消责任的粒度止于那一个约束。
WRITER_GLOBAL_STYLE = "style_prefix.inject_global_config_into_script"
WRITER_TIMELINE = "write_timeline_from_durations"
WRITER_POST_FIX = "post_fix_shot_prompts"
WRITER_HARD_FIX = "constraints.run_hard_fix"
WRITER_INJURY_ANCHOR = "injury_anchor_fallback"
WRITER_RESTORE_UNTARGETED = "restore_untargeted"
WRITER_FINALIZE_HARD_FIX = "finalize.step01_script"

REPAIR_TX_WRITERS: tuple = (
    WRITER_GLOBAL_STYLE,
    WRITER_TIMELINE,
    WRITER_POST_FIX,
    WRITER_HARD_FIX,
    WRITER_INJURY_ANCHOR,
    WRITER_RESTORE_UNTARGETED,
)

# 四类归因（§P3 表）。`REASON_NONE` = 本模块不裁决，沿用既有 reason
# （`no_change` / 协议拒收——两者与这四类必须可区分）。
REASON_REVERTED = "postprocess_effect_reverted"
REASON_PRESENCE_CONFLICT = "actor_presence_conflict"
REASON_BINDING_AMBIGUOUS = "actor_binding_ambiguous"
REASON_CAPABILITY_MISSING = "repair_capability_missing"
REASON_NONE = ""

# 归因优先级：语义层先于机制层——「这条修复本身就不该做」比「谁把它抵消了」更靠前。
REASON_PRECEDENCE: tuple = (
    REASON_PRESENCE_CONFLICT,
    REASON_CAPABILITY_MISSING,
    REASON_BINDING_AMBIGUOUS,
    REASON_REVERTED,
)

# 人物绑定三态
RELATION_SAME = "same_entity"
RELATION_OTHER = "other_entity"
RELATION_AMBIGUOUS = "ambiguous"

# 关系来源（决定「协议缺字段」还是「字段在但生产者没填」）
SOURCE_CHARACTERS = "characters"
SOURCE_PLAN = "plan"
SOURCE_DECLARED = "declared"
SOURCE_FIELD_EMPTY = "relation_field_empty"      # 有该字段但没给出本条关系
SOURCE_NO_FIELD = "relation_field_absent"        # 协议里根本没有该字段

# 缺的关系字段名（反馈给生产者；只**读**，不批量改旧人物 schema）
NEEDED_RELATION_FIELD = "characters[].aliases"

# 群体（类别）合规形态。与 `group_actor_continuity` 的 `_group_aliases()` **同源**
# ——这里定义，约束侧 import，避免两处各写一份别名口径。
GROUP_CANONICAL = "群演"
_GROUP_EXTRA_NAMES: tuple = ("群演", "佃仆")
# 人物记录里可能**显式声明**身份关系的字段名（容忍读取；不存在就不当作有关系）
_RELATION_FIELD_NAMES: tuple = (
    "aliases", "identity_aliases", "alias", "also_known_as", "aka",
)
# 显式声明「这是另一个实体」的字段名
_DISTINCT_FIELD_NAMES: tuple = ("distinct_from", "separate_from")


def group_alias_names() -> set:
    """群体（类别）别名全集——`group_actor_continuity` 的唯一来源."""
    out = set(_GROUP_EXTRA_NAMES)
    out.update(alias for alias, canonical in CHARACTER_ALIASES.items()
               if canonical == GROUP_CANONICAL)
    return out


def is_group_name(name: str) -> bool:
    """`name` 是否是群体（类别）条目——别名表与合规形态两种写法都算."""
    n = str(name or "")
    if not n:
        return False
    return n in group_alias_names() or CHARACTER_ALIASES.get(n) == GROUP_CANONICAL


def is_category_alias(name: str) -> bool:
    """`name` 是否是**类别别名**写法（「小厮」这类只说明类别的表层词）.

    类别别名只说明类别——它既不能证明与已在场人物不同，也不能证明相同。
    """
    n = str(name or "")
    return bool(n) and CHARACTER_ALIASES.get(n) == GROUP_CANONICAL


# ============================================================================
# 2. 帧快照与差异（只按**名字**匹配，与 `repair_fields._pure_entry_edits` 同口径）
# ============================================================================

def _canon_list(entries) -> str:
    if not isinstance(entries, list):
        return ""
    return json.dumps([_canon_entry(e) for e in entries], ensure_ascii=False)


def list_digest(entries) -> str:
    """帧条目列表的**逐字**摘要（含条目字段归一的顺序）."""
    if not isinstance(entries, list):
        return ""
    return hashlib.sha1(_canon_list(entries).encode("utf-8")).hexdigest()[:16]


def entry_signature(entries) -> str:
    """只按**名字与顺序**的签名（判断「保留了哪些、顺序变没变」）."""
    return "|".join(_entry_name(e) for e in entries or [])


def entry_delta(before: list, after: list) -> tuple:
    """`(removed, added)`——按**名字**成对消解，与守卫的「纯增删」同口径.

    ⚠️ 与 `_pure_entry_edits` 一样：改名在这里表现为「删一条 + 加一条」，
    本函数**报不出来**（按名字匹配时 `before - after` 与 `after - before` 相交为空）。
    那条边界由守卫负责，不在这里重开。
    """
    pool: dict = {}
    for e in after or []:
        pool.setdefault(_entry_name(e), []).append(e)
    removed: list = []
    for e in before or []:
        bucket = pool.get(_entry_name(e))
        if bucket:
            bucket.pop(0)
        else:
            removed.append(e)
    added = [e for bucket in pool.values() for e in bucket]
    return removed, added


def entries_at(script, target: str, locator: str):
    """取 `<脚本>.<目标>.<帧 locator>` 的条目列表；目标/帧不存在返回 `None`."""
    shot = shot_by_target(script, target)
    if not isinstance(shot, dict):
        return None
    return _layout_frame_lists(shot).get(locator)


def _effect_key(effect: dict) -> tuple:
    return (str(effect.get("shot_id") or ""), str(effect.get("frame_locator") or ""))


def _effect_id(effect: dict) -> str:
    return "%s|%s" % _effect_key(effect)


# ============================================================================
# 3. `build_layout_effects`：只登记**精确点名帧列表**上、通过现有结构守卫的增删
# ============================================================================

def _violation_names_frame(message: str, target: str, locator: str) -> bool:
    """违例消息是否点到**这一个帧**（`"<目标>: <帧> …"` 形状，见 repair_fields）."""
    m = str(message or "")
    if not m.startswith(target + ": "):
        return False
    rest = m[len(target) + 2:]
    if not rest.startswith(locator):
        return False
    nxt = rest[len(locator):len(locator) + 1]
    # 后一位是数字说明点到的是更长的下标（`kfs[1]` 不该吃掉 `kfs[10]`）
    return not nxt.isdigit()


def blocked_frames(before, merged, allowed_by_target: dict, targets: list) -> set:
    """跑**现有结构守卫**，返回「不得登记效果」的 `(目标, 帧)` 集合.

    归属不出来的违例 → **整个目标**的帧都不登记（fail-closed：宁可少记一条效果，
    也不把一条没通过守卫的改动记成「已授权效果」）。
    """
    violations = frame_layout_structural_violations(
        before, merged, allowed_by_target, targets)
    blocked: set = set()
    unattributable: set = set()
    for tid in targets:
        locators = list(_layout_frame_lists(
            shot_by_target(before, tid) or {}).keys())
        for v in violations:
            if not str(v).startswith(tid + ": "):
                continue
            hit = next((p for p in sorted(locators, key=len, reverse=True)
                        if _violation_names_frame(v, tid, p)), None)
            if hit is None:
                unattributable.add(tid)
            else:
                blocked.add((tid, hit))
    for tid in unattributable:
        blocked.add((tid, None))     # 该目标**全部**帧
    return blocked


def build_layout_effects(before, merged, allowed_by_target, source_revision,
                         *, round_id: str = "") -> list:
    """登记本轮**已授权且已应用**的帧子列表增删（**请求应用事实**）.

    Args:
        before: 本轮开始前的完整 script（与 `_apply_shot_replacements` 同源口径）
        merged: 授权合并后的完整 script
        allowed_by_target: `{目标名: 授权路径集合}`（与 `_apply_shot_replacements` 同一份）
        source_revision: 本次效果所属的**本轮基线稿** revision。
            ⚠️ **不是** `_commit_step01_outputs(source_revision=...)` 那个——后者是
            BEST-GUARD **选中快照自身**的 revision（`main.py:2878`），两者在
            BEST-GUARD 路径上**必然不同**。传错会把效果的版本身份绑到另一份稿子上，
            于是 `remap_layout_effects` 的核对全部失效。用
            `shared.quality_gate.compute_script_revision(<本轮基线稿>)` 取本参数。
        round_id: 轮次标识（台账/回放用）

    Returns:
        效果记录列表。每条含 round_id / source_revision / shot_id / frame_locator /
        before 摘要 / merged 摘要 / removed_entries / added_entries / 保留条目摘要 /
        原授权目标；另附 `merged_entries`（证据，供撤销差异逐字比对）。

    **只登记**：
      · 被**逐字点名**成「该帧子列表」的目标（`_frame_target_spelled`——条目叶子、
        通配、整张 `frame_layouts.kfs` 都不算「点名某一帧」）；
      · 通过了**现有结构守卫**的增删（未过守卫的帧不登记，见 `blocked_frames`）；
      · 确有增删的帧（内容没变不产生效果）。

    **不登记**整镜替换、字段改写或任何非帧子列表的改动——那些没有「条目被抵消」
    这种失败形态。
    """
    out: list = []
    targets = [str(t) for t in (allowed_by_target or {})]
    if not targets:
        return out
    blocked = blocked_frames(before, merged, allowed_by_target, targets)

    for tid in targets:
        if (tid, None) in blocked:
            continue
        allowed = [str(a) for a in (allowed_by_target.get(tid) or ())]
        if not allowed:
            continue
        shot_before = shot_by_target(before, tid)
        shot_merged = shot_by_target(merged, tid)
        if not isinstance(shot_before, dict) or not isinstance(shot_merged, dict):
            continue
        listed_before = _layout_frame_lists(shot_before)
        listed_merged = _layout_frame_lists(shot_merged)
        for locator, entries in listed_before.items():
            if not _frame_target_spelled(locator, allowed):
                continue
            if (tid, locator) in blocked:
                continue
            new = listed_merged.get(locator)
            if not isinstance(new, list):
                continue
            if _canon_list(entries) == _canon_list(new):
                continue
            removed, added = entry_delta(entries, new)
            if not removed and not added:
                continue
            kept_names = {_entry_name(e) for e in new}
            out.append({
                "round_id": str(round_id or ""),
                "source_revision": str(source_revision or ""),
                "shot_id": tid,
                "frame_locator": locator,
                "before_digest": list_digest(entries),
                "merged_digest": list_digest(new),
                "before_signature": entry_signature(entries),
                "merged_signature": entry_signature(new),
                "removed_entries": [dict(e) if isinstance(e, dict) else e
                                    for e in removed],
                "added_entries": [dict(e) if isinstance(e, dict) else e
                                  for e in added],
                "kept_entries": [
                    {"name": _entry_name(e), "entry_digest": list_digest([e])}
                    for e in entries if _entry_name(e) in kept_names
                ],
                # 原授权目标：只有**被逐字点名的帧**才是这次效果的授权来源
                "authorized_targets": [a for a in allowed
                                       if _frame_target_spelled(locator, [a])],
                "merged_entries": [dict(e) if isinstance(e, dict) else e
                                   for e in new],
            })
    return out


def remap_layout_effects(effects, shot_mapping, *, to_revision: str = ""):
    """版本变更（拆镜/重排）后把旧效果搬到新编号域；**无唯一映射的拒收**.

    Args:
        effects: `build_layout_effects` 的记录
        shot_mapping: `{旧 target 名: 新 target 名}`——值必须是**单个**字符串。
            拆镜（一旧对多新）= 无唯一映射 ⇒ 该效果**拒收**，不适用旧效果。
        to_revision: 新 revision（给了就写进搬过去的效果）

    Returns:
        `(applicable, rejected)`——拒收项带 `reject` 原因，留在证据里而不是被丢掉。

    列表下标属于**该 revision**：跨拆镜或重排复用下标会把「删掉 kfs[1] 的某条目」
    搬到另一帧上，所以只认**显式**映射，且只认**唯一**的。

    ⚠️ **仓库里没有可核验的一对一映射载体**：`timecode.py` 拆镜时写的
    `_split_from` / `_split_to` 会被 `constraints/shot_index_renumber.py` 在
    重编号时**主动 pop 掉**（该模块的清理分支），因此拆镜关系过了那一关就不在了。
    所以本函数是 **fail-closed** 的：调用方拿不出可核验的一对一映射（`shot_mapping`
    缺省 `None`、缺该目标、或一旧对多新）时，旧效果**一律失效**
    （进 `rejected`），绝不按下标“猜”着搬过去。
    """
    applicable: list = []
    rejected: list = []
    for eff in effects or []:
        old = str(eff.get("shot_id") or "")
        new = (shot_mapping or {}).get(old)
        if isinstance(new, (list, tuple, set)):
            rejected.append({**eff, "reject": "版本变更后无唯一映射（拆镜/重排）：%s"
                                            % sorted(map(str, new))})
            continue
        if not isinstance(new, str) or not new:
            rejected.append({**eff, "reject": "版本变更后没有该目标的映射"})
            continue
        moved = {**eff, "shot_id": new, "remapped_from": old}
        if to_revision:
            moved["source_revision"] = str(to_revision)
        applicable.append(moved)
    return applicable, rejected


# ============================================================================
# 4. 撤销检测与四个冻结节点
# ============================================================================

def _revert_detail(effect: dict, now_entries) -> Optional[dict]:
    """该效果此刻是否**被抵消**；是则给出「前后差异」的逐条事实."""
    merged_entries = effect.get("merged_entries")
    if not isinstance(merged_entries, list):
        return None
    if now_entries is None:
        return {
            "shot_id": effect.get("shot_id"),
            "frame_locator": effect.get("frame_locator"),
            "authorized_targets": list(effect.get("authorized_targets") or []),
            "before_digest": effect.get("before_digest"),
            "merged_digest": effect.get("merged_digest"),
            "now_digest": None,
            "merged_signature": effect.get("merged_signature"),
            "now_signature": None,
            "restored_names": [],
            "lost_names": [],
            "removed_by_repair": [_entry_name(e)
                                  for e in (effect.get("removed_entries") or [])],
            "added_by_repair": [_entry_name(e)
                                for e in (effect.get("added_entries") or [])],
            "note": "整帧不再存在（目标镜头被删/被改编号/帧被移除）",
        }
    if list_digest(now_entries) == effect.get("merged_digest"):
        return None
    # `entry_delta(合并后, 现在)` ⇒ removed = 合并后有、现在没了的；added = 现在多出来的
    dropped, readded = entry_delta(merged_entries, now_entries)
    return {
        "shot_id": effect.get("shot_id"),
        "frame_locator": effect.get("frame_locator"),
        "authorized_targets": list(effect.get("authorized_targets") or []),
        "before_digest": effect.get("before_digest"),
        "merged_digest": effect.get("merged_digest"),
        "now_digest": list_digest(now_entries),
        "merged_signature": effect.get("merged_signature"),
        "now_signature": entry_signature(now_entries),
        # 后又**出现**的条目（= 本轮删掉的又被加回来 / 新增的被去掉）——
        # 这正是「已授权效果被抵消」的实体
        "restored_names": [_entry_name(e) for e in readded],
        # 合并后本来在、现在**不在**的条目（被后处理删掉——另一种抵消形态）
        "lost_names": [_entry_name(e) for e in dropped],
        "removed_by_repair": [_entry_name(e)
                              for e in (effect.get("removed_entries") or [])],
        "added_by_repair": [_entry_name(e)
                            for e in (effect.get("added_entries") or [])],
    }


def detect_reverted_layout_effects(effects, script) -> list:
    """`script` 相对**合并后**状态被抵消的已授权效果（逐条给出前后差异）."""
    out: list = []
    for eff in effects or []:
        now = entries_at(script, str(eff.get("shot_id") or ""),
                         str(eff.get("frame_locator") or ""))
        detail = _revert_detail(eff, now)
        if detail is not None:
            out.append(detail)
    return out


@dataclass
class RepairEffectLedger:
    """一轮修复事务的效果账本：四个冻结节点 + 逐 writer 观测.

    **只记事实**。本类不下「删除在故事中是否正确」的结论——那是
    `resolve_frame_actor_binding` 与 `classify_effect_outcome` 的事。
    """

    round_id: str = ""
    source_revision: str = ""
    effects: list = field(default_factory=list)
    nodes: list = field(default_factory=list)

    # ---- 四个冻结节点 ----------------------------------------------------

    def freeze_candidate(self, script, revision: str = "") -> dict:
        """节点①：原候选."""
        return self._freeze(NODE_CANDIDATE, "", script, revision)

    def freeze_merged(self, script, effects=None, revision: str = "") -> dict:
        """节点②：授权合并后（效果的**基线**）.

        基线只能在这里取——它只含 LLM 的写入，不含管线自己的确定性归一化
        （与 `_mask_violations_after_patch` / `_kf_struct_after_patch` 同源）。
        """
        if effects is not None:
            self.effects = list(effects)
        if revision:
            self.source_revision = str(revision)
        return self._freeze(NODE_MERGED, "", script, "")

    def observe_writer(self, writer: str, script) -> dict:
        """节点③：每个后处理 writer 完成后（约束框架里 = 每个约束一个节点）."""
        return self._freeze(NODE_WRITER, str(writer or "?"), script, "")

    def freeze_reverify(self, script, revision: str = "") -> dict:
        """节点④：最终重验后."""
        return self._freeze(NODE_REVERIFY, "", script, revision)

    # ---- 查询 ------------------------------------------------------------

    def reached_postprocess(self) -> bool:
        """本轮是否**真的走到了**后处理.

        未走到 ⇒ 没有 writer 节点 ⇒ 后面的失败**不能**冒称「约束撤销」——它只能
        是「没应用」或协议拒收。这条判据把两者分开，不靠调用点自觉。
        """
        return any(n.get("node") == NODE_MERGED for n in self.nodes) and \
            any(n.get("node") == NODE_WRITER for n in self.nodes)

    def reverted(self) -> list:
        """已授权效果被抵消的**首次**记录（没有抵消/没走到后处理 → 空表）."""
        if not self.reached_postprocess():
            return []
        for node in self.nodes:
            if node.get("node") == NODE_WRITER and node.get("reverted"):
                return list(node["reverted"])
        return []

    def first_revert_writer(self) -> str:
        """首次抵消的**责任 writer**（没有则空串）."""
        if not self.reached_postprocess():
            return ""
        for node in self.nodes:
            if node.get("node") == NODE_WRITER and node.get("reverted"):
                return str(node.get("writer") or "")
        return ""

    def surviving_keys(self, script) -> set:
        """最终重验后仍然成立的效果键（`shot|locator`）."""
        gone = {_effect_id(r) for r in
                detect_reverted_layout_effects(self.effects, script)}
        return {_effect_id(e) for e in self.effects if _effect_id(e) not in gone}

    def reverted_keys(self, script) -> set:
        return {_effect_id(r) for r in
                detect_reverted_layout_effects(self.effects, script)}

    def as_records(self) -> list:
        """台账用的扁平记录（跨轮只作证据；新轮重新依据新基线生成）."""
        return [{k: v for k, v in n.items()} for n in self.nodes]

    # ---- 内部 ------------------------------------------------------------

    def _digests(self, script) -> dict:
        return {_effect_id(e): list_digest(entries_at(
            script, *_effect_key(e))) for e in self.effects}

    def _freeze(self, kind: str, writer: str, script, revision: str) -> dict:
        prev = self.nodes[-1] if self.nodes else None
        # ⚠️ 本方法会作为 `on_constraint_done` 回调挂在**每个约束**之后，而
        # `apply_constraints` 用 `try/except` 把回调异常**吞成一条 warn**——账本自己
        # 抛异常就会变成「观测静默失效」，正是本包要消灭的形态。所以这里自己兜：
        # 出错时把错误写进节点（**可见**），而不是抛出去被吞掉。
        node = {
            "node": kind,
            "writer": writer,
            "round_id": self.round_id,
            "source_revision": str(revision or self.source_revision or ""),
            "effects": len(self.effects),
            "reverted": [],
            "changed_frames": [],
            "digests": {},
        }
        try:
            if self.effects:
                node["reverted"] = detect_reverted_layout_effects(self.effects,
                                                                  script)
            node["changed_frames"] = self._changed(prev, script)
            node["digests"] = self._digests(script)
        except Exception as exc:                      # noqa: BLE001 - 见上
            node["error"] = "%s: %s" % (type(exc).__name__, exc)
        self.nodes.append(node)
        return node

    def _changed(self, prev: Optional[dict], script) -> list:
        if prev is None:
            return []
        now = self._digests(script)
        before = prev.get("digests") or {}
        out: list = []
        for key in sorted(now):
            if key in before and before[key] == now[key]:
                continue
            out.append({"effect": key, "from": before.get(key, ""),
                        "to": now[key]})
        return out


# ============================================================================
# 5. 只读人物解析：`resolve_frame_actor_binding`
# ============================================================================

@dataclass(frozen=True)
class ActorBinding:
    """一帧里**待裁决条目**与在场人物的身份关系（只读，不猜）.

    Attributes:
        relation: `same_entity` / `other_entity` / `ambiguous`
        entry_name: 被裁决的条目名（类别别名等表层写法）
        entity: `same_entity`/`other_entity` 时的角色 identifier（代码只派生编号）
        source: 关系从哪里来（`characters` / `plan` / `declared` /
            `relation_field_empty` / `relation_field_absent`）
        evidence: **证据 locator**（结构化位置 + 原文/记录摘要），不是推测
        needed_field: `ambiguous` 时反馈给生产者**缺哪个字段**
        on_screen: 该实体此刻是否已在场（用于「已在场人物的角色称谓」判定）
    """

    relation: str = RELATION_AMBIGUOUS
    entry_name: str = ""
    entity: str = ""
    source: str = SOURCE_NO_FIELD
    evidence: dict = field(default_factory=dict)
    needed_field: str = ""
    on_screen: bool = False

    def as_record(self) -> dict:
        return {
            "relation": self.relation,
            "entry_name": self.entry_name,
            "entity": self.entity,
            "source": self.source,
            "evidence": dict(self.evidence),
            "needed_field": self.needed_field,
            "on_screen": bool(self.on_screen),
        }

    @property
    def capability_missing(self) -> bool:
        """现协议**根本没给**关系字段（而不是给了但没填）."""
        return self.source == SOURCE_NO_FIELD


def _declared_surfaces(record: dict) -> list:
    """人物记录里**显式声明**的表层写法（含 identifier/name 本身）."""
    if not isinstance(record, dict):
        return []
    out: list = []
    for key in ("identifier", "name"):
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            out.append((key, v.strip()))
    for key in _RELATION_FIELD_NAMES:
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            out.append((key, v.strip()))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, str) and item.strip():
                    out.append(("%s[%d]" % (key, i), item.strip()))
    return out


def _distinct_surfaces(record: dict) -> list:
    if not isinstance(record, dict):
        return []
    out: list = []
    for key in _DISTINCT_FIELD_NAMES:
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            out.append(("%s" % key, v.strip()))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, str) and item.strip():
                    out.append(("%s[%d]" % (key, i), item.strip()))
    return out


def _has_relation_field(records: list) -> bool:
    """这些记录里**有没有**可承载身份关系的字段（决定「缺字段」还是「没填」）."""
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        if any(k in rec for k in _RELATION_FIELD_NAMES + _DISTINCT_FIELD_NAMES):
            return True
    return False


def _character_records(characters) -> list:
    if isinstance(characters, dict):
        return [v for v in characters.values() if isinstance(v, dict)]
    if isinstance(characters, list):
        return [v for v in characters if isinstance(v, dict)]
    return []


def _plan_records(plan) -> list:
    """计划侧的人物记录（容忍 `{"characters": [...]}` / 裸列表 / `{"roles": [...]}`）."""
    if isinstance(plan, dict):
        for key in ("characters", "roles", "cast"):
            if isinstance(plan.get(key), list):
                return [v for v in plan[key] if isinstance(v, (dict, str))]
    if isinstance(plan, list):
        return [v for v in plan if isinstance(v, (dict, str))]
    return []


def _on_screen_names(shot: dict, frame_locator: str) -> list:
    """该帧此刻的条目名（用于「已在场」判定；帧不存在返回空表）."""
    entries = _layout_frame_lists(shot or {}).get(str(frame_locator or ""))
    if not isinstance(entries, list):
        return []
    return [_entry_name(e) for e in entries if _entry_name(e)]


def frame_entry_names(shot: dict, frame_locator: str) -> list:
    """该帧**修复后**的条目名（"重复条目清理"必须由这里的另一条目证明）."""
    return _on_screen_names(shot or {}, frame_locator)


def entity_surface_names(entity: str, characters, plan) -> set[str]:
    """该实体在本稿人物表里的全部表层写法（identifier / name / aliases）.

    用来判断"帧里**还有别的条目**承载同一实体"。被删条目在基线里认出**自己**
    证明不了"这是重复条目"（2026-10-04 复核 P1-6）。
    """
    want = str(entity or "").strip()
    if not want:
        return set()
    out: set[str] = set()
    records = _character_records(characters) + [
        r for r in _plan_records(plan) if isinstance(r, dict)]
    for rec in records:
        ident = str(rec.get("identifier") or "").strip()
        name = str(rec.get("name") or "").strip()
        if want not in (ident, name):
            continue
        for value in (want, ident, name):
            if value:
                out.add(value)
        for alias in (rec.get("aliases") or []):
            if isinstance(alias, str) and alias.strip():
                out.add(alias.strip())
    return out


def _candidate_entry_names(shot: dict, frame_locator: str, entry_name) -> list:
    """待裁决的条目名：显式给了就用它，否则取该帧里的**类别别名**条目."""
    if entry_name:
        return [str(entry_name)]
    entries = _layout_frame_lists(shot or {}).get(str(frame_locator or "")) or []
    return [n for n in (_entry_name(e) for e in entries) if is_category_alias(n)]


def resolve_frame_actor_binding(shot, frame_locator, characters, plan,
                                novel_text: str = "", *, entry_name=None,
                                declared_relations=None) -> ActorBinding:
    """**只读**解析：该帧里某个条目与在场人物的身份关系.

    Args:
        shot: 镜头 dict（含 `frame_layouts`）
        frame_locator: `frame_layouts.ff` / `.lf` / `.kfs[N]`
        characters: 当前 script 的人物列表（或 `{identifier: 记录}`）
        plan: 计划侧人物。**来源**：修复路径取 `script["chapter_outline"]`
            （`main.py:9871` 在缓存命中分支写入；正常路径由钩子/大纲阶段写入），
            约束路径取 `ctx.script["chapter_outline"]`。可以是
            `{"characters": [...]}` / 裸列表 / `None`。
        characters: 当前**稿件**的人物列表——`script["characters"]`，
            **不是** `characters_bank`。理由：身份关系属于「这一稿的这条布局条目」，
            事实源必须与布局同稿；bank 是跨章持久化的另一套事实（它连
            `is_visible`/`is_new` 都没有），拿它判本稿在场性会引入第二个口径。
            `constraints` 侧同理走 `ctx.script["characters"]`。
        novel_text: 小说原文（**只作证据定位**，不用于猜关系）
        entry_name: 显式指定被裁决的条目名；缺省取该帧里的类别别名条目
        declared_relations: 生产者**显式声明**的关系映射
            `{表层写法: identifier}`（兼容入口；没有就不猜）

    Returns:
        `ActorBinding`。**优先**当前人物/计划中的显式身份关系：
          · 表层写法命中某角色记录的 `identifier`/`name`/**aliases** ⇒ `same_entity`
            （`characters` 优先于 `plan`；证据 locator 指到那一条字段）；
          · 命中某记录的 `distinct_from` ⇒ `other_entity`；
          · 计划里**独立点名**了该写法且不在场人物里 ⇒ `other_entity`；
          · 都没有 ⇒ `ambiguous`：有可承载关系的字段则 `relation_field_empty`，
            没有则为 `relation_field_absent` + `needed_field`。

    **不做**：不建全书实体知识库、不批量改旧人物 schema、不靠名字或句式猜同一人。
    类别别名（`小厮`→`群演`）**只说明类别**，不构成「不同」也不构成「相同」的证据。
    """
    records = _character_records(characters) + [
        r for r in _plan_records(plan) if isinstance(r, dict)]
    on_screen = _on_screen_names(shot, frame_locator)
    names = _candidate_entry_names(shot, frame_locator, entry_name)
    if not names:
        return ActorBinding(entry_name="", source=SOURCE_NO_FIELD,
                            needed_field=NEEDED_RELATION_FIELD)
    target = names[0]
    evidence_base = {"shot_index": (shot or {}).get("index"),
                     "frame_locator": str(frame_locator or ""),
                     "entry_name": target,
                     "on_screen": list(on_screen)}

    # ① 生产者显式声明（兼容入口，最高优先——它是**关系**本身，不是推断）
    if isinstance(declared_relations, dict) and declared_relations.get(target):
        ident = str(declared_relations[target])
        return ActorBinding(
            relation=RELATION_SAME, entry_name=target, entity=ident,
            source=SOURCE_DECLARED,
            evidence={**evidence_base, "declared": {target: ident}},
            on_screen=ident in on_screen)

    # ② 当前人物记录里的显式身份关系
    for idx, rec in enumerate(records):
        for key, surface in _declared_surfaces(rec):
            if surface != target:
                continue
            ident = str(rec.get("identifier") or rec.get("name") or "")
            return ActorBinding(
                relation=RELATION_SAME, entry_name=target, entity=ident,
                source=SOURCE_CHARACTERS,
                evidence={**evidence_base,
                          "locator": "characters[%d].%s" % (idx, key),
                          "record": ident},
                on_screen=ident in on_screen)
        for key, surface in _distinct_surfaces(rec):
            if surface != target:
                continue
            ident = str(rec.get("identifier") or rec.get("name") or "")
            return ActorBinding(
                relation=RELATION_OTHER, entry_name=target, entity=ident,
                source=SOURCE_CHARACTERS,
                evidence={**evidence_base,
                          "locator": "characters[%d].%s" % (idx, key),
                          "record": ident},
                on_screen=ident in on_screen)

    # ③ 计划里**独立点名**：计划把该写法当自己的角色 ⇒ 明确是另一个实体
    #    （在场角色名单里没有它时才成立——否则那条记录已在 ② 命中）
    for idx, rec in enumerate(_plan_records(plan)):
        surface = rec.get("identifier") if isinstance(rec, dict) else rec
        if not isinstance(surface, str) or surface.strip() != target:
            continue
        return ActorBinding(
            relation=RELATION_OTHER, entry_name=target, entity=target,
            source=SOURCE_PLAN,
            evidence={**evidence_base, "locator": "plan.characters[%d]" % idx},
            on_screen=False)

    # ④ 没有现成关系 ⇒ 不猜。
    has_field = _has_relation_field(records)
    source = SOURCE_FIELD_EMPTY if has_field else SOURCE_NO_FIELD
    return ActorBinding(
        relation=RELATION_AMBIGUOUS, entry_name=target,
        source=source,
        evidence={**evidence_base,
                  "relation_field_present": bool(has_field),
                  "novel_text_hit": _novel_hit(novel_text, target)},
        needed_field="" if has_field else NEEDED_RELATION_FIELD,
        on_screen=False)


def _novel_hit(novel_text: str, name: str) -> dict:
    """原文里该写法出现的**位置**（证据定位；不解读语义）."""
    text = str(novel_text or "")
    if not text or not name:
        return {}
    pos = text.find(name)
    if pos < 0:
        return {}
    return {"first_pos": pos, "context": text[max(0, pos - 24):pos + len(name) + 24]}


# ============================================================================
# 6. 归因分层：四类可区分，其余交回既有判据
# ============================================================================

def classify_effect_outcome(ledger: "RepairEffectLedger", script,
                            *, bindings: Optional[dict] = None,
                            actors: Optional[dict] = None) -> dict:
    """把「已授权效果被抵消」判成**可区分**的责任类.

    Args:
        ledger: 本轮账本（已冻结节点）
        script: 最终重验后的 script
        bindings: `{"<shot_id>|<frame_locator>": ActorBinding}`
        actors: 兼容别名（同 `bindings`）

    Returns:
        ```
        {
          "reason": <四类之一 或 "">,      # "" = 本模块不裁决，沿用既有 reason
          "mechanism": <REASON_REVERTED 或 "">,   # 机制层归因（始终如实给出）
          "writer": <责任 writer>,
          "targets": [...],                # 受影响的目标（镜头）
          "first_revert": {...} 或 None,   # 首次撤销的前后差异
          "bindings": {...},
          "detail": "<人读一句话>",
        }
        ```

    **判据**：
      · 没有登记效果 → `reason=""`（既有的 `no_change` / 协议拒收原样保留）；
      · 效果登记了但**没被抵消** → `reason=""`（本轮不存在这个冲突）；
      · 被抵消了 →
          - 条目是**类别别名**且关系**明确是另一实体** ⇒ `actor_presence_conflict`
            （这条修复本身不该做；后处理加回来是对的）；
          - 条目是类别别名且**协议没有关系字段** ⇒ `repair_capability_missing`
            （缺字段，附 `needed_field`）；
          - 条目是类别别名且**有关系字段但生产者没填** ⇒ `actor_binding_ambiguous`；
          - 其余（非人物条目 / 关系明确同一实体却被抵消）⇒
            `postprocess_effect_reverted` + 责任 writer。

    **没走到后处理的失败不会到这里**——`ledger.reverted()` 在
    `reached_postprocess()` 为假时恒为空表，因此不会把「没应用」冒称成「被撤销」。
    """
    as_map = bindings if bindings is not None else (actors or {})
    reverted = ledger.reverted()
    base = {
        "reason": REASON_NONE,
        "mechanism": REASON_NONE,
        "writer": "",
        "targets": [],
        "first_revert": None,
        "bindings": {},
        "detail": "",
        "capability_missing": False,
        "needed_field": "",
    }
    if not ledger.effects:
        base["detail"] = "本轮没有登记到授权了的帧子列表增删（交回既有判据）"
        return base
    if not reverted:
        base["detail"] = ("已授权的帧子列表增删没有被后处理抵消"
                          if ledger.reached_postprocess()
                          else "本轮未走到后处理（无 writer 节点）——不构成撤销")
        return base

    base["mechanism"] = REASON_REVERTED
    base["writer"] = ledger.first_revert_writer()
    base["targets"] = sorted({str(r.get("shot_id") or "") for r in reverted})
    base["first_revert"] = reverted[0]

    # 抵消的条目里有没有**类别别名**——没有就走机制层归因
    hits: list = []
    for r in reverted:
        # 绑定键是「目标＋帧＋**条目**」（P1-9）：帧上有多个条目时，"这一帧有关系"
        # 说明不了"这一条关系明确"。先按被回填的条目名查，再退回帧级键
        # （`classify_effect_outcome` 的旧契约，供只给帧级键的调用方）。
        # **逐条**收集（2026-10-04 复核 P2-10）：旧实现只取第一个命中的条目，
        # 同一帧恢复两条（一条关系缺失、一条明确是另一实体）时交换顺序就会改归因。
        _found: list = []
        for _name in (r.get("restored_names") or []):
            _name = str(_name or "")
            if not _name:
                continue
            _b = (as_map or {}).get("%s|%s|%s" % (r.get("shot_id"),
                                                  r.get("frame_locator"), _name))
            if _b is not None:
                _found.append(_b)
        if not _found:
            _b = (as_map or {}).get(_effect_id(r)) or (as_map or {}).get(
                "%s|%s" % (r.get("shot_id"), r.get("frame_locator")))
            if _b is not None:
                _found.append(_b)
        for _b in _found:
            record = _b.as_record() if hasattr(_b, "as_record") else dict(_b)
            base["bindings"]["%s|%s|%s" % (r.get("shot_id"), r.get("frame_locator"),
                                           record.get("entry_name"))] = record
            if not (is_category_alias(record.get("entry_name"))
                    or is_group_name(record.get("entry_name"))):
                continue
            hits.append((r, record))

    if hits:
        relations = [rec.get("relation") for _r, rec in hits]
        # 明确「另一实体」：这条删除本身不该做，后处理加回来是对的（§P3 第 2 行）
        if RELATION_OTHER in relations:
            base["reason"] = REASON_PRESENCE_CONFLICT
            base["detail"] = (
                "被抵消的条目是**明确属于另一实体**的群体（证据见 bindings）——"
                "后处理把它加回来是对的，本轮这条删除不该做；"
                "应改人物绑定/描述而不是删条目")
            return base
        # 协议**根本没有**承载该关系的字段 ⇒ 能力缺口（§P2：先补窄协议）
        if any(rec.get("relation") == RELATION_AMBIGUOUS
               and rec.get("source") == SOURCE_NO_FIELD for _r, rec in hits):
            base["reason"] = REASON_CAPABILITY_MISSING
            base["capability_missing"] = True
            base["needed_field"] = NEEDED_RELATION_FIELD
            base["detail"] = (
                "被抵消的条目是类别别名（只说明类别），而现有协议**没有**任何字段能"
                "表达它与已在场人物的「同一实体/另一实体」关系——"
                "缺少 `%s`，无法裁决删除是否正确；交回原生产者补关系，不自动采纳删除"
                % NEEDED_RELATION_FIELD)
            return base
        # 有关系字段但生产者没给（或互相矛盾）⇒ 歧义，反馈原生产者（§P3 第 3 行）
        if RELATION_AMBIGUOUS in relations:
            base["reason"] = REASON_BINDING_AMBIGUOUS
            base["needed_field"] = NEEDED_RELATION_FIELD
            base["detail"] = (
                "被抵消的条目是类别别名，人物关系字段存在但**没有给出本条关系**"
                "（或互相矛盾）——不自动采纳删除，也不继续无信息的相同请求")
            return base
        # 关系**明确是已在场人物**（或生产者显式声明了同一实体）⇒ 删除合法，
        # 是后处理把它加了回来 ⇒ 机制层归因 + 责任 writer（§P3 第 1 行）
        base["reason"] = REASON_REVERTED
        base["detail"] = (
            "被抵消的条目与已在场人物**明确是同一实体**（证据见 bindings），"
            "删除合法，但被确定性后处理按类别别名重建为第二个实体"
            "（责任 writer=%s）——须修共同写入机制，不是改本轮修复"
            % (base["writer"] or "?"))
        return base

    base["reason"] = REASON_REVERTED
    base["detail"] = (
        "已授权的帧子列表改动被**确定性后处理**抵消（责任 writer=%s）——"
        "这不是「模型没改」，也不是 no_target_resolved" % (base["writer"] or "?"))
    return base


def conflict_signature(outcome: dict) -> str:
    """冲突的**请求信息**签名——相同签名 + 相同请求 ⇒ 停止盲修.

    只取「会改变下一轮请求内容」的部分：原因、责任 writer、目标、被抵消的条目名。
    不含摘要/时间戳这类每轮都变的字段（否则永远判不出「无变化」）。
    """
    if not outcome or not outcome.get("reason"):
        return ""
    parts = [str(outcome.get("reason")), str(outcome.get("writer") or "")]
    parts.append(",".join(str(t) for t in (outcome.get("targets") or [])))
    first = outcome.get("first_revert") or {}
    parts.append(",".join(str(n) for n in (first.get("restored_names") or [])))
    parts.append(str(first.get("shot_id") or ""))
    parts.append(str(first.get("frame_locator") or ""))
    return "|".join(parts)


def render_conflict_feedback(outcome: dict) -> str:
    """把冲突渲染成**下一修复请求**里的一段结构化反馈（§P3）.

    要求的四项：source_revision、目标、原文/绑定证据、请求→应用→撤销阶段、
    允许修改范围。
    """
    if not outcome or not outcome.get("reason"):
        return ""
    first = outcome.get("first_revert") or {}
    lines = [
        "\n**上一轮修复被后处理抵消（相同冲突不会重发）**：",
        "- 归因：`%s`（责任 writer=`%s`）" % (outcome.get("reason"),
                                              outcome.get("writer") or "?"),
        "- 目标：%s" % (sorted(outcome.get("targets") or []) or "（无）"),
        "- 阶段：请求 → 应用（已通过授权与合并）→ **被后处理撤销**",
    ]
    if first:
        lines.append(
            "- 首次撤销：%s 的 %s —— 本轮删掉的 %s 被加回来，"
            "授权目标 %s" % (first.get("shot_id"), first.get("frame_locator"),
                             first.get("restored_names") or "（无）",
                             first.get("authorized_targets") or "（无）"))
    for key, rec in (outcome.get("bindings") or {}).items():
        lines.append(
            "- 绑定证据 %s：关系=%s 来源=%s 证据=%s"
            % (key, rec.get("relation"), rec.get("source"),
               json.dumps(rec.get("evidence") or {}, ensure_ascii=False)))
    if outcome.get("capability_missing"):
        lines.append(
            "- **能力缺口**：现有协议没有字段能表达这条身份关系，"
            "缺 `%s`；请先补关系，不要重复发同一条删除" % outcome.get("needed_field"))
        lines.append("- 允许修改范围：**本条不再发**（无依据授权删除），"
                     "其余目标照常")
    else:
        lines.append("- 允许修改范围：仅本轮 `allowed_fields`，"
                     "且改动必须能穿过同一轮的后处理（不得只改布局条目）")
    return "\n".join(lines) + "\n"


__all__ = [
    "NODE_CANDIDATE", "NODE_MERGED", "NODE_WRITER", "NODE_REVERIFY",
    "WRITER_GLOBAL_STYLE", "WRITER_TIMELINE", "WRITER_POST_FIX",
    "WRITER_HARD_FIX", "WRITER_INJURY_ANCHOR", "WRITER_RESTORE_UNTARGETED",
    "WRITER_FINALIZE_HARD_FIX", "REPAIR_TX_WRITERS",
    "REASON_REVERTED", "REASON_PRESENCE_CONFLICT", "REASON_BINDING_AMBIGUOUS",
    "REASON_CAPABILITY_MISSING", "REASON_NONE", "REASON_PRECEDENCE",
    "RELATION_SAME", "RELATION_OTHER", "RELATION_AMBIGUOUS",
    "SOURCE_CHARACTERS", "SOURCE_PLAN", "SOURCE_DECLARED",
    "SOURCE_FIELD_EMPTY", "SOURCE_NO_FIELD", "NEEDED_RELATION_FIELD",
    "ActorBinding", "RepairEffectLedger", "build_layout_effects",
    "blocked_frames", "remap_layout_effects", "detect_reverted_layout_effects",
    "resolve_frame_actor_binding", "classify_effect_outcome",
    "conflict_signature", "render_conflict_feedback",
    "group_alias_names", "is_group_name", "is_category_alias",
    "entry_delta", "entry_signature", "list_digest", "entries_at",
]
