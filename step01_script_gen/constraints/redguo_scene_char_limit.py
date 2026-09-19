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
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return []

        scene_counter: Counter[str] = Counter()
        char_counter: Counter[str] = Counter()
        registered = {
            str(c.get("identifier") or c.get("name") or "").strip()
            for c in (ctx.script.get("characters") or [])
            if isinstance(c, dict) and str(c.get("identifier") or c.get("name") or "").strip()
        }
        for shot in ctx.shots:
            if not isinstance(shot, dict):
                continue
            sc = str(shot.get("scene", "") or shot.get("location", "")).strip()
            if sc:
                scene_counter[sc] += 1
            ms = str(shot.get("main_subject", "")).strip()
            if ms and not _is_non_char_subject(ms):
                for _n in _split_subject_names(ms):
                    if _n in registered:
                        char_counter[_n] += 1
            for item in (shot.get("char_layout") or []):
                if isinstance(item, dict):
                    cn = str(item.get("name", "")).strip()
                    if cn and not _is_non_char_subject(cn):
                        for _n in _split_subject_names(cn):
                            if _n in registered:
                                char_counter[_n] += 1

        # Platform limits are production constraints, not permission to rename
        # authored people or merge distinct scenes.  Report the overflow and
        # let regeneration/simplification change the story with evidence.
        if len(scene_counter) > _MAX_SCENES:
            for scene, count in scene_counter.most_common():
                if len(scene_counter) - _MAX_SCENES == 0:
                    break
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None,
                    field="scene",
                    repaired=False,
                    message=(
                        f"场景「{scene}」共出现 {count} 镜；当前脚本有 {len(scene_counter)} 个场景，"
                        f"超过红果上限 {_MAX_SCENES}。禁止自动把不同地点改名合并，必须精简或重生成。"
                    ),
                    evidence=str(dict(scene_counter.most_common())),
                ))
                break
        if len(char_counter) > _MAX_CHARS:
            for name, count in char_counter.most_common():
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None,
                    field="characters",
                    repaired=False,
                    message=(
                        f"人物「{name}」共出现 {count} 次；当前脚本有 {len(char_counter)} 个主要人物，"
                        f"超过红果上限 {_MAX_CHARS}。禁止自动改名为群演/村人，必须重生成或明确合并身份。"
                    ),
                    evidence=str(dict(char_counter.most_common())),
                ))
                break
        return []


__all__ = ["RedguoSceneCharLimitConstraint"]
