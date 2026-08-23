"""C. 道具空间方位约束（v6.42 迁移自 _hard_fix_prop_position）.

14 类常见道具（炭炉/酒壶/酒杯/钓杆/油纸伞/步摇/灯盏/灯笼/竹箫/马车/舴艋舟/
刺客匕首/船舷/农具/叉子粪铲）出现但紧邻 8 字符内无任何空间方位词 → 追加
「（画X）」标注.

幂等：紧邻已含方位词 / 已追加过（防重复）→ 跳过.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _PROP_POS_FALLBACK
from .base import Constraint
from .types import RepairRecord, Severity


_POS_WORDS = (
    "画左", "画右", "画中", "画上", "画下", "船头", "船尾",
    "地面", "甲板", "手边", "身侧", "身后", "胸前", "腰间",
    "背后", "垂入", "倒在", "微倾", "洒落", "岸上", "水里",
    "水面", "插在", "挂在", "立于", "发间",
)


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
                continue  # 旧实现不处理 hook
            for fld in self.fields:
                v = shot.get(fld)
                if not isinstance(v, str) or not v:
                    continue
                new_v = v
                changed = False
                for prop, fallback in _PROP_POS_FALLBACK.items():
                    for m in list(re.finditer(re.escape(prop), new_v)):
                        ctx_after = new_v[m.end():m.end() + 12]
                        ctx_before = new_v[max(0, m.start() - 5):m.start()]
                        if any(w in ctx_after for w in _POS_WORDS):
                            continue
                        if any(w in ctx_before for w in _POS_WORDS):
                            continue
                        insert_at = m.end()
                        suffix = f"（{fallback}）"
                        # 防重复追加
                        if new_v[insert_at:insert_at + len(suffix)] == suffix:
                            continue
                        new_v = new_v[:insert_at] + suffix + new_v[insert_at:]
                        changed = True
                if changed:
                    shot[fld] = new_v
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="append",
                            target=fld,
                            before=v,
                            after=new_v,
                        )
                    )
        return repairs
