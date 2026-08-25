"""G. 威胁源-视线耦合约束（v6.42 迁移自 _hard_fix_threat_gaze_coupling）.

8-22 跑批实测：2 次 regen 都报「镜头4 + hook 视线-威胁方位矛盾」。
`_check_threat_gaze_coupling` 只 WARN 不改写，本约束做确定性改写：
发现者 lf_desc/ff_desc/motion_desc/prompt 写的视线方向 > char_layout 角色 position

改法：发现者 lf_desc 写「惊恐望向画X 刺客」 → 把 char_layout 中威胁源
（刺客）的 position 改为画X（与发现者视线同向）。

视线正则不再写死「画左/画右/船头/船尾/船梢」5 词，
改为从 scene_anchors 动态构建（BASE_POS_WORDS + 所有 SCENE_ANCHORS）——
桥/街道/院落等新场景自动跟随。

幂等：char_layout 已是该方向则跳过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.scene_anchors import BASE_POS_WORDS, SCENE_ANCHORS
from .base import Constraint
from .types import RepairRecord, Severity


def _build_gaze_directions() -> tuple[str, ...]:
    """从 scene_anchors 动态构建视线方向词.

    组成：
      - BASE_POS_WORDS（画X/前景/中景/后景）
      - 所有 SCENE_ANCHORS 值（船头/船尾/桥头/桥尾/街口/巷尾/正厅/院门/窗前/殿前...）
    """
    seen: set[str] = set(BASE_POS_WORDS)
    for anchors in SCENE_ANCHORS.values():
        seen.update(anchors)
    return tuple(seen)


# 视线方向（动态构建 + 「船梢」兼容别名）
_GAZE_DIRECTIONS = _build_gaze_directions() + ("船梢", "船首", "船头甲板", "船尾甲板")

# 模块级动态正则（按所有方向词构建一次）
_THREAT_GAZE_FIX_RE = re.compile(
    r"(惊恐|望|盯|凝神|凝视|注视|紧盯|目光|望见)"
    r"[一-龥]{0,12}?("
    + "|".join(re.escape(d) for d in _GAZE_DIRECTIONS)
    + r")"
)

# 威胁源角色名关键词（统一从 prompt_semantic.registry 引入，单一事实源）
from step01_script_gen.prompt_semantic import THREAT_KEYWORDS as _THREAT_KEYWORDS


def _find_discoverer_gaze(shot: dict) -> str | None:
    """从 4 字段里抽"看/望+方向"的方向（画X 或 场景 anchor，如 船头/船尾/桥头）."""
    for fld in ("lf_desc", "ff_desc", "motion_desc", "prompt"):
        v = shot.get(fld) or ""
        if not isinstance(v, str):
            continue
        m = _THREAT_GAZE_FIX_RE.search(v)
        if m:
            return m.group(2)  # 直接返回方向词（已被 _THREAT_GAZE_FIX_RE 验证过）
    return None


def _is_threat_name(name: str) -> bool:
    return any(w in name for w in _THREAT_KEYWORDS)


class ThreatGazeCouplingConstraint(Constraint):
    """G. 威胁源-视线耦合：发现者视线方向 → 同步威胁源 char_layout position.

    severity=WARN: 改写是确定性，不触发 regen。
    """

    name = "threat_gaze_coupling"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            discoverer_gaze = _find_discoverer_gaze(shot)
            if not discoverer_gaze:
                continue
            layout = shot.get("char_layout")
            if not isinstance(layout, list):
                continue
            changed = False
            for item in layout:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not _is_threat_name(name):
                    continue
                pos = str(item.get("position", "")).strip()
                if discoverer_gaze in pos:
                    continue
                new_pos = re.sub(r"画左|画右", discoverer_gaze, pos)
                if new_pos == pos:
                    new_pos = discoverer_gaze
                item["position"] = new_pos
                changed = True
            if changed:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="set_field",
                        target="char_layout",
                        before="",
                        after="",
                    )
                )
        return repairs
