"""C. 道具空间方位约束（v6.42 迁移自 _hard_fix_prop_position）.

14 类常见道具（炭炉/酒壶/酒杯/钓杆/油纸伞/步摇/灯盏/灯笼/竹箫/马车/舴艋舟/
刺客匕首/船舷/农具/叉子粪铲）出现但紧邻 8 字符内无任何空间方位词 → 追加
「（画X）」标注.

位置 hint 改用 scene_anchors.get_prop_position_hint()，
按 shot.location 选横向场景专属（船舷/甲板/船头/水面）或通用位置（画中/发间）.

v6.43 重构：_MANAGED_PROPS 改用 prompt_semantic.PROP_REGISTRY 自动派生
（新增 prop 只改 registry，不动本文件）.

幂等：紧邻已含方位词 / 20 字内已有「（画X）」标记 → 跳过.

v6.50 修（08-28 审计）：
1. 单字道具（剑/箫/舟）改「前后不邻汉字」边界匹配——治「剑（画中）眉星目」
   「唇贴箫（画中）孔」词中打洞。
2. 匹配倒序处理——治正序插入坐标右移导致的错误位置插入。
3. 幂等检查扩为「20 字内已有（画X）」——治跨 Size 锚的二次追加
   「（画中）（宽1.4米，可容3人）（画中）」双标记。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.scene_anchors import get_prop_position_hint
from step01_script_gen.prompt_semantic import PROP_REGISTRY
from .base import Constraint
from .types import RepairRecord, Severity, Violation


def _get_managed_props() -> tuple[str, ...]:
    """从 prompt_semantic.PROP_REGISTRY 派生受管道具列表.

    包含 PROP_REGISTRY 所有别名（不仅是 spec_key 第一个名）。
    新增 prop 只改 registry.py 一行。
    """
    names: list[str] = []
    for spec in PROP_REGISTRY.values():
        for name in spec.names:
            names.append(name)
    # 额外兼容：scene_anchors 的 _PROP_DEFAULT_POSITIONS 含"船舷"/"农具"/"叉子粪铲"
    # 等 prompt_semantic 暂未登记的旧 prop
    extra = ("船舷", "农具", "叉子粪铲")
    for n in extra:
        if n not in names:
            names.append(n)
    return tuple(names)


# 受管道具列表（运行时派生，模块级缓存以提升性能）
_MANAGED_PROPS: tuple[str, ...] = _get_managed_props()

# 方位词黑名单（前后 12/5 字符内含这些词则跳过追加；抽自 scene_anchors BASE_POS_WORDS）
# 注：前景/中景/后景 是景别词（"[中景]" 前缀），不是位置词——不放进黑名单避免误判
_POS_WORDS = (
    "画左", "画右", "画中", "画上", "画下",
    "船头", "船中", "船尾", "船舷", "甲板", "水面", "岸边", "桥头", "桥尾", "街口", "巷尾",
    "地面", "手边", "身侧", "身后", "胸前", "腰间", "背后", "发间",
    "垂入", "倒在", "微倾", "洒落", "岸上", "水里", "插在", "挂在", "立于",
)

# 单字道具（剑/箫/舟 等）必须独立成词才能标注，否则会在复合作义词中间打洞：
# 实测 08-28「剑（画中）眉星目」「唇贴箫（画中）孔」。前后紧邻汉字即视为
# 复合词的一部分（剑眉/箫孔/舴艋舟 的「舟」），跳过不标。
_SINGLE_CHAR_PROP_RE_CACHE: dict[str, "re.Pattern"] = {}


def _prop_match_pattern(prop: str) -> "re.Pattern":
    """单字道具用「前后不邻汉字」边界匹配；多字道具用原样匹配."""
    if len(prop) > 1:
        return re.compile(re.escape(prop))
    pat = _SINGLE_CHAR_PROP_RE_CACHE.get(prop)
    if pat is None:
        pat = re.compile(r"(?<![一-龥])" + re.escape(prop) + r"(?![一-龥])")
        _SINGLE_CHAR_PROP_RE_CACHE[prop] = pat
    return pat


class PropPositionConstraint(Constraint):
    """C. 道具空间方位：14 类常见道具缺方位 → 追加「（画X）」.

    severity=WARN: 道具方位可由后续 repair round 修.
    幂等通过「紧邻已含方位词则跳过」+「防重复追加」保证.
    """

    name = "prop_spatial_anchor"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if is_hook:
                continue
            location = str(shot.get("location", "") or shot.get("scene", "") or "")
            warned: set[tuple[str, str]] = set()
            for fld in self.fields:
                value = shot.get(fld)
                if not isinstance(value, str) or not value:
                    continue
                for prop in _MANAGED_PROPS:
                    for match in _prop_match_pattern(prop).finditer(value):
                        after = value[match.end():match.end() + 20]
                        before = value[max(0, match.start() - 5):match.start()]
                        if any(w in after for w in _POS_WORDS):
                            continue
                        if "（画" in after:
                            continue
                        if any(w in before for w in _POS_WORDS):
                            continue
                        key = (fld, prop)
                        if key in warned:
                            continue
                        warned.add(key)
                        hint = get_prop_position_hint(prop, location)
                        ctx.record_violation(Violation(
                            constraint_name=self.name,
                            severity=Severity.WARN,
                            shot_index=shot.get("index", idx),
                            field=fld,
                            message=(
                                f"道具「{prop}」缺少可证的空间方位；保留原文，"
                                f"未自动插入默认位置「{hint}」。"
                            ),
                            evidence=value[max(0, match.start() - 20):match.end() + 40],
                            repaired=False,
                        ))
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index", idx),
                            action="warn",
                            target=fld,
                            before=value[max(0, match.start() - 20):match.end() + 40],
                            after=value[max(0, match.start() - 20):match.end() + 40],
                            detail=f"道具 {prop} 方位缺失，禁止自动注入默认方位",
                        ))
        return repairs


__all__ = ["PropPositionConstraint"]
