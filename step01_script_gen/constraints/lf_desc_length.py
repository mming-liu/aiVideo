"""M. lf_desc 字数下限约束（v6.42 迁移自 _hard_fix_lf_desc_length）.

shot lf_desc < 40 字（hook < 60）→ 拼接 char_layout 第一个角色的布局
信息作为收尾。

幂等：append 后字符变长，允许 5 字符内差额；空镜豁免。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_EMPTY_SUBJECTS = (
    "空镜", "空景", "远景", "环境", "山峦", "山色", "夕阳", "月光",
    "荷塘", "夜幕", "雨", "雪", "风", "云", "日落", "日出", "月色",
)


class LfDescLengthConstraint(Constraint):
    """M. lf_desc 字数下限：< 40 (shot) / 60 (hook) → 拼接布局.

    severity=WARN: 字数不足可由后续 repair round 修.
    repair_marker="位于"（拼接布局的开头）.
    """

    name = "lf_desc_length"
    severity = Severity.WARN
    repair_marker = "位于"
    fields = ("lf_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            # 幂等优先：已含 marker → 跳过（防止长 lf_desc 仍被反复 append）
            if self._already_repaired(shot):
                continue
            lf = shot.get("lf_desc", "") or ""
            threshold = 60 if is_hook else 40
            if len(lf) >= threshold:
                continue
            if not is_hook:
                ms = str(shot.get("main_subject", "") or "").strip()
                if any(es in ms for es in _EMPTY_SUBJECTS):
                    continue
            layout = shot.get("char_layout") or []
            role_layout = ""
            if isinstance(layout, list) and layout:
                for item in layout[:3]:
                    if isinstance(item, dict):
                        nm = item.get("name", "").strip()
                        pos = item.get("position", "").strip()
                        facing = item.get("facing", "").strip()
                        if nm and pos:
                            role_layout = f"{nm}位于{pos}"
                            if facing:
                                role_layout += f"、{facing}"
                            role_layout += "。"
                            break
            if not role_layout:
                role_layout = "画面完整，所有角色定锚。"
            before = lf
            shot["lf_desc"] = lf.rstrip("。") + f"。{role_layout}"
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx,
                    action="append",
                    target="lf_desc",
                    before=before,
                    after=shot["lf_desc"],
                )
            )
        return repairs
