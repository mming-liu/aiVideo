"""F5. 红果场景/人物硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 2 处红果超限：
  - 场景 6 > 3（红果硬上限）
  - 人物 5 > 4（红果硬上限）

LLM 即使收到 system.md D/E 规则 + main.md v6.49 红果提示，仍会因
「同一物理地点细分」+「配角各自独立名」超出硬上限。

修法（确定性兜底，不依赖 LLM）：
  (1) 场景合并：scene_assets bank 已有 6 个 scene，bank alias 匹配同地点
      → 把第 4+ 个场景的 shot.scene / shot.location / shot.prompt 中的别名
      替换为前 3 个的"代表 scene"
  (2) 人物 alias：挑主角（main_subject 出现频次最高）+ 主要对手 + 1-2 配角
      = ≤4 个，其他角色名 alias 为「群演」/「村人」/「船夫」（按场景类型）

severity=HARD: 60-90s 红果标准是硬约束（structural）.
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import Counter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 场景上限 / 人物上限（v6.49 与 system.md D/E 同步）
_MAX_SCENES = 3
_MAX_CHARS = 4

# 场景类型 → 群演别名（按 location 选）
_GENERIC_ALIASES = {
    "水": "船夫",
    "岸": "村人",
    "庄": "庄仆",
    "家": "家人",
    "车": "车夫",
    "default": "群演",
}

# v6.62（09-06）：非人物 main_subject 值——空镜/场景标记不参与人物计数，
# 也不能被 alias（实测「空镜·水波/树影/风」被当人物 alias 成「群演」后，
# opening_empty_shot_insert 判定失效 → 每轮重复插入空镜）。
_NON_CHAR_SUBJECTS = (
    "空镜", "环境", "无人物", "no people",
)


def _is_non_char_subject(name: str) -> bool:
    """main_subject 值是否为空镜/场景标记（非人物）."""
    n = (name or "").strip()
    return any(w in n for w in _NON_CHAR_SUBJECTS)


def _split_subject_names(name: str) -> list[str]:
    """把 main_subject 按「，」拆成单人物名.

    v6.62（09-06）：role_anchor 会把「群演」改写为「杨旭，群演」复合名，
    整串计数会让「杨旭，群演」挤进 top4（成了第 4 个"独立人物"），真实
    配角被挤掉 → 每轮 char_counter>4 → HARD 永不收敛。拆分后按真实人物
    计数；句号后缀（「杨旭，群演。冲突，反转」）在第一个「。」截断。
    """
    n = (name or "").strip().split("。", 1)[0]
    parts = [p.strip() for p in n.split("，") if p.strip()]
    return parts if parts else ([name.strip()] if name.strip() else [])


def _scene_kind(location: str) -> str:
    """从 location 推断场景类型（决定群演别名）."""
    if not location:
        return "default"
    for k in _GENERIC_ALIASES:
        if k != "default" and k in location:
            return k
    return "default"


class RedguoSceneCharLimitConstraint(Constraint):
    """F5. 红果场景/人物上限硬修复：场景≤3 + 人物≤4.

    severity=HARD: 违反触发 regen 反馈.
    """

    name = "redguo_scene_char_limit"
    severity = Severity.HARD

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return repairs

        # ---- 1. 统计 scene / character 实际使用频次 ----
        scene_counter: Counter[str] = Counter()
        char_counter: Counter[str] = Counter()
        for shot in ctx.shots:
            if not isinstance(shot, dict):
                continue
            sc = str(shot.get("scene", "") or shot.get("location", "")).strip()
            if sc:
                scene_counter[sc] += 1
            ms = str(shot.get("main_subject", "")).strip()
            # v6.62：空镜/场景标记不是人物，跳过计数（「空镜·水波/树影/风」
            # 此前被当人物计数 + alias，导致空镜插入约束每轮重复触发）
            if ms and not _is_non_char_subject(ms):
                # 复合名「杨旭，群演」按单人物拆分计数（治 top4 被复合名挤占）
                for _n in _split_subject_names(ms):
                    char_counter[_n] += 1
            for item in (shot.get("char_layout") or []):
                if isinstance(item, dict):
                    cn = str(item.get("name", "")).strip()
                    if cn and not _is_non_char_subject(cn):
                        for _n in _split_subject_names(cn):
                            char_counter[_n] += 1

        # ---- 2. 场景合并：保留前 _MAX_SCENES 个高频，余下 alias 到最近一个 ----
        scene_keep = [s for s, _ in scene_counter.most_common(_MAX_SCENES)]
        scene_drop = [s for s in scene_counter if s not in scene_keep]
        if scene_drop:
            # 按主场景索引循环分配（drop 的用 keep[0]）
            scene_alias: dict[str, str] = {s: scene_keep[0] for s in scene_drop}
            for shot in ctx.shots:
                if not isinstance(shot, dict):
                    continue
                sc = str(shot.get("scene", "") or shot.get("location", "")).strip()
                if sc in scene_alias:
                    new_sc = scene_alias[sc]
                    shot["scene"] = new_sc
                    shot["location"] = new_sc
                    # prompt 中的别名也替换
                    for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
                        v = shot.get(fld)
                        if isinstance(v, str) and sc in v:
                            shot[fld] = v.replace(sc, new_sc)
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index"),
                            action="set_field",
                            target="scene",
                            before=sc,
                            after=new_sc,
                        )
                    )

        # ---- 3. 人物 alias：保留前 _MAX_CHARS，余下 alias 为群演 ----
        # 优先保留 main_subject 出现频次最高的（主角）
        char_keep = [c for c, _ in char_counter.most_common(_MAX_CHARS)]
        char_drop = [c for c in char_counter if c not in char_keep]
        # v6.62（09-06）：只在实际发生改名时记录 repair（此前每轮无条件 append
        # repair 记录——「第 N 镜即使无 drop 人物也记 alias」——导致本约束
        # 不幂等，`repairs` 恒非空，第 4 步 HARD 上报被永久抑制/或每轮重报）。
        aliased_names: set[str] = set()
        if char_drop:
            drop_set = set(char_drop)
            for shot in ctx.shots:
                if not isinstance(shot, dict):
                    continue
                alias = _GENERIC_ALIASES[_scene_kind(shot.get("location", ""))]
                # main_subject：复合名「杨旭，群演」逐段判定——只把 drop 段换成
                # alias，keep 段（杨旭）保留（整串替换会把主角名也抹掉）
                ms = str(shot.get("main_subject", "")).strip()
                ms_parts = _split_subject_names(ms)
                if ms and any(p in drop_set for p in ms_parts):
                    head, _, tailnote = ms.partition("。")
                    new_parts = [
                        alias if p in drop_set else p
                        for p in _split_subject_names(head)
                    ]
                    # 去重保持顺序（多段 alias 同名合并）
                    seen: list[str] = []
                    for p in new_parts:
                        if p not in seen:
                            seen.append(p)
                    shot["main_subject"] = "，".join(seen) + (f"。{tailnote}" if tailnote else "")
                    aliased_names.update(p for p in ms_parts if p in drop_set)
                # char_layout
                for item in (shot.get("char_layout") or []):
                    if isinstance(item, dict):
                        cn = str(item.get("name", "")).strip()
                        cn_parts = _split_subject_names(cn)
                        if cn and any(p in drop_set for p in cn_parts):
                            new_cn = "，".join(
                                dict.fromkeys(
                                    alias if p in drop_set else p for p in cn_parts
                                )
                            )
                            item["name"] = new_cn
                            aliased_names.update(p for p in cn_parts if p in drop_set)
            if aliased_names:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=None,
                        action="alias",
                        target="characters",
                        before=",".join(sorted(aliased_names)),
                        after=alias,
                    )
                )

        # ---- 4. HARD 违例上报（如果 LLM 生成时就已超）----
        # v6.49 fix：只有当 repairs 为空（即 LLM 仍未合并且硬修复失败）才报 HARD。
        # 实际修复后 scene 已 alias 到 top 3，violation 会让 regen 误以为未修，触发 regen 循环。
        if scene_counter and len(scene_counter) > _MAX_SCENES and not repairs:
            for s, c in scene_counter.most_common(_MAX_SCENES + 1):
                if s in scene_drop:
                    ctx.record_violation(
                        Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=None,
                            field="scene",
                            # 已 alias 合并 → repaired=True（不再触发 regen 循环；
                            # 实测 09-05/09-06 每轮跑完都剩本违例挂着）
                            repaired=True,
                            message=(
                                f"场景「{s}」（{c} 镜）超出红果 {_MAX_SCENES} 场景硬上限——"
                                f"已 alias 合并到「{scene_keep[0] if scene_keep else '?'}」。"
                                f"原文若需独立场景，prompt 应明确合并到主场景。"
                            ),
                        )
                    )
                    break  # 只报一个
        if char_counter and len(char_counter) > _MAX_CHARS:
            for s, c in char_counter.most_common(_MAX_CHARS + 1):
                if s in char_drop:
                    ctx.record_violation(
                        Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=None,
                            field="characters",
                            repaired=True,
                            message=(
                                f"人物「{s}」（{c} 镜）超出红果 {_MAX_CHARS} 人物硬上限——"
                                f"已 alias 为群演/村人/船夫等。"
                            ),
                        )
                    )
                    break

        return repairs
