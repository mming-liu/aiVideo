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
            if ms:
                char_counter[ms] += 1
            for item in (shot.get("char_layout") or []):
                if isinstance(item, dict):
                    cn = str(item.get("name", "")).strip()
                    if cn:
                        char_counter[cn] += 1

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
        if char_drop:
            for shot in ctx.shots:
                if not isinstance(shot, dict):
                    continue
                alias = _GENERIC_ALIASES[_scene_kind(shot.get("location", ""))]
                # main_subject
                ms = str(shot.get("main_subject", "")).strip()
                if ms in char_drop:
                    shot["main_subject"] = alias
                # char_layout
                for item in (shot.get("char_layout") or []):
                    if isinstance(item, dict):
                        cn = str(item.get("name", "")).strip()
                        if cn in char_drop:
                            item["name"] = alias
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot.get("index"),
                        action="alias",
                        target="characters",
                        before=",".join(char_drop),
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
                            message=(
                                f"人物「{s}」（{c} 镜）超出红果 {_MAX_CHARS} 人物硬上限——"
                                f"已 alias 为群演/村人/船夫等。"
                            ),
                        )
                    )
                    break

        return repairs
