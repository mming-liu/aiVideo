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
                # v6.47（08-27）：追加**全部**角色站位（原只取第一个 break），
                # 否则超短 lf_desc 补 1 个角色仍 <40 字（镜3/5/10 实测）
                parts = []
                for item in layout[:4]:
                    if not isinstance(item, dict):
                        continue
                    nm = str(item.get("name", "") or "").strip()
                    pos = str(item.get("position", "") or "").strip()
                    facing = str(item.get("facing", "") or "").strip()
                    if nm and pos:
                        seg = f"{nm}位于{pos}"
                        if facing:
                            seg += f"、{facing}"
                        parts.append(seg + "。")
                if parts:
                    role_layout = "".join(parts)
            if not role_layout:
                role_layout = "画面完整，所有角色定锚。"
            before = lf
            new_lf = lf.rstrip("。") + f"。{role_layout}"
            # 仍不足阈值（base 太短 + 角色少）→ 补一句占位兜底，保证达标
            if len(new_lf) < threshold and role_layout != "画面完整，所有角色定锚。":
                new_lf += "画面完整，所有角色定锚。"
            shot["lf_desc"] = new_lf
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
