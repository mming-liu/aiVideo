"""A. 致命伤持续约束（v6.42 迁移自 _hard_fix_injury_continuity）.

扫所有 shot，找第一个含 anchor 词的 shot 作为 build_idx；
之后同 location shot 若含 victim 但缺 anchor → 同步追加到 prompt + lf_desc.

幂等：anchor 词在 prompt/lf_desc 已含 → 跳过（_already_repaired 检测
_INJURY_ANCHOR_TAIL 标记）.

数据驱动（v6.42+）：可从 ElementMarkers.injuries[victim].continuity_phrase 派生；
当前实现仍用 validation._INJURY_ANCHOR_TAIL 作为兜底（Phase 5 升级）.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import (
    _INJURY_ANCHOR_WORDS,
    _INJURY_ANCHOR_TAIL,
)
from .base import Constraint
from .types import RepairRecord, Severity


# 强致命伤锚词（更严格，避免误判衣服水渍/手指沾血）
_STRONG_INJURY_ANCHORS = (
    "洇痕", "中刀", "中剑", "中枪", "刀伤", "剑伤",
    "血痕", "血斑", "溅血", "中伤", "倒卧", "倒地",
)
_THREAT_KEYWORDS = ("刺客", "神秘人", "黑影", "蒙面", "匪")


def _identify_victim(shot: dict) -> str | None:
    """从 build_idx 镜头推导 victim 角色名（4 来源 fallback）.

    来源顺序：① main_subject 剥离动作词（仅当与 char_layout name 完全匹配）
    ② char_layout role=次 非 threat 角色 ③ prompt/lf_desc 紧邻 anchor 词
    ④ char_layout 兜底.
    """
    cl_names_all = [
        str(item.get("name", "")).strip()
        for item in (shot.get("char_layout") or [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    # 来源①
    main_raw = shot.get("main_subject", "")
    if main_raw and "的" not in main_raw:
        stripped = main_raw
        for prefix in (
            "刺杀", "被刺", "倒地", "躺血", "奄奄一息", "濒死", "受伤", "倒下",
            "刺客", "神秘人", "黑影", "破水", "出水", "破门", "夜袭",
        ):
            stripped = stripped.replace(prefix, "")
        stripped = stripped.strip()
        if stripped and stripped in cl_names_all:
            return stripped
    # 来源②
    for item in (shot.get("char_layout") or []):
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name", "")).strip()
        role = str(item.get("role", "")).strip()
        if not nm or role != "次":
            continue
        if any(t in nm for t in _THREAT_KEYWORDS):
            continue
        return nm
    # 来源③
    text = (shot.get("prompt", "") or "") + (shot.get("lf_desc", "") or "")
    for m in re.finditer(
        r"([一-鿿]{2,3})(?:胸口|心口|心窝|腹部|中刀|中剑|中枪|刀伤|剑伤|洇痕|暗伤|血痕)",
        text,
    ):
        nm = m.group(1)
        if nm not in _THREAT_KEYWORDS:
            return nm
    # 来源④
    for item in (shot.get("char_layout") or []):
        if isinstance(item, dict):
            nm = str(item.get("name", "")).strip()
            if nm and nm not in _THREAT_KEYWORDS:
                return nm
    return None


def _is_victim_in_frame(shot: dict, victim: str) -> bool:
    """victim 出现在 4 字段任一 或 char_layout（背景角色也算）."""
    text = (
        (shot.get("ff_desc") or "")
        + (shot.get("motion_desc") or "")
        + (shot.get("prompt") or "")
        + (shot.get("lf_desc") or "")
    )
    if victim and victim in text:
        return True
    for item in (shot.get("char_layout") or []):
        if isinstance(item, dict) and victim == str(item.get("name", "")).strip():
            return True
    return False


class InjuryContinuityConstraint(Constraint):
    """A. 致命伤持续：build_idx 之后同 location 镜头缺 anchor 则追加.

    severity=WARN: 致命伤可由后续 repair round 修.
    repair_marker=_INJURY_ANCHOR_TAIL: 已含伤处描述视为已修.
    """

    name = "fatal_wound_continuity"
    severity = Severity.WARN
    repair_marker = _INJURY_ANCHOR_TAIL

    def apply(self, ctx) -> list[RepairRecord]:
        # 1. 找 build_idx（第一个含 anchor 词的 shot）
        build_idx = None
        build_loc = None
        for i, s in enumerate(ctx.shots):
            text = (
                (s.get("prompt", "") or "")
                + (s.get("ff_desc", "") or "")
                + (s.get("lf_desc", "") or "")
            )
            if any(w in text for w in _INJURY_ANCHOR_WORDS):
                build_idx = i
                build_loc = s.get("location", "") or s.get("scene", "")
                break
        if build_idx is None:
            return []

        # 2. 推导 victim
        victim = _identify_victim(ctx.shots[build_idx])
        if not victim:
            return []

        # 3. 后续同 location shot 缺 anchor → 追加
        repairs: list[RepairRecord] = []
        for i in range(build_idx + 1, len(ctx.shots)):
            s = ctx.shots[i]
            loc = s.get("location", "") or s.get("scene", "")
            if not build_loc or loc != build_loc:
                continue
            if not _is_victim_in_frame(s, victim):
                continue
            if self._already_repaired(s):
                continue

            prompt = s.get("prompt", "") or ""
            lf = s.get("lf_desc", "") or ""
            prompt_has_anchor = any(w in prompt for w in _STRONG_INJURY_ANCHORS)
            lf_has_anchor = bool(lf) and any(w in lf for w in _STRONG_INJURY_ANCHORS)

            if not prompt_has_anchor:
                before = prompt
                s["prompt"] = prompt.rstrip("。") + f"。{_INJURY_ANCHOR_TAIL}"
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", i),
                        action="append",
                        target="prompt",
                        before=before,
                        after=s["prompt"],
                    )
                )
            if lf and not lf_has_anchor:
                before = lf
                s["lf_desc"] = lf.rstrip("。") + f"。{_INJURY_ANCHOR_TAIL}"
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", i),
                        action="append",
                        target="lf_desc",
                        before=before,
                        after=s["lf_desc"],
                    )
                )
        return repairs
