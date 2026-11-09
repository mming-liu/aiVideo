# -*- coding: utf-8 -*-
"""按镜头关系选择转场（M8.4，2026-11-09）.

纯函数：只读两个相邻镜头的结构化字段，返回**已规范化**的 kind 与时长，交给
Step04 既有的 `_parse_transition`/timeline 校验链；不直接改脚本、不调用 FFmpeg。

规则（内容无关，按确定性优先级）：

1. 显式 `clip_transition` 永远优先——本模块只在调用方明确要求「按关系选」且
   该边界没有显式转场时使用；
2. 场景/地点切换 → `fade`（0.5s / 0.4s）；
3. 时间跳跃/回忆/转场标记 → `fadeblack`（0.6s，明确的时间断裂）；
4. 打斗/追逐等强动作同场衔接 → `cut`（0s，保持动作连贯）；
5. 同场景同地点普通衔接 → `cut`（0s）；
6. 其他（有场景但地点字段缺失等）→ `cut`，不猜更重的转场。

返回的 `kind` 全部落在 `shared.media_contract.LEGAL_BOUNDARY_TRANSITIONS`。
"""
from __future__ import annotations

from shared.media_contract import LEGAL_BOUNDARY_TRANSITIONS

_TIME_JUMP_MARKERS = (
    "回忆", "闪回", "倒叙", "数日前", "数年前", "次日", "翌日", "第二天",
    "隔日", "多年后", "三日后", "半个月后", "一个月后", "时间流逝", "转场",
)
_ACTION_WORDS = (
    "刺杀", "打斗", "搏杀", "搏斗", "激战", "追逐", "奔逃", "挥拳", "出拳",
    "劈砍", "刺入", "撞上", "踢", "踹", "格挡", "闪避",
)
_SPACE_WORDS = (
    "水下", "水面", "船头", "船尾", "岸边", "庄内", "庄外", "门内", "门外",
    "室内", "室外", "院内", "院外", "舱内", "舱外",
)
_LEGAL = set(LEGAL_BOUNDARY_TRANSITIONS)


def _text(shot: dict) -> str:
    return " ".join(str((shot or {}).get(k) or "")
                    for k in ("ff_desc", "lf_desc", "motion_desc", "prompt",
                              "main_subject"))


def _location_switch(prev: dict, curr: dict) -> bool:
    a = str(prev.get("location") or "")
    b = str(curr.get("location") or "")
    if not a or not b or a == b:
        return False
    words_a = {w for w in _SPACE_WORDS if w in a}
    words_b = {w for w in _SPACE_WORDS if w in b}
    return bool(words_a or words_b) and words_a != words_b


def _choice(kind: str, duration: float, reason: str, code: str) -> dict:
    if kind not in _LEGAL:
        raise ValueError("非法转场 kind: %r" % kind)
    return {
        "kind": kind,
        "duration_sec": float(duration) if kind != "cut" else 0.0,
        "reason": reason,
        "code": code,
    }


def choose_transition(prev_shot: dict, next_shot: dict) -> dict:
    """给相邻镜头的边界选一个合法转场（不读显式 clip_transition，由调用方优先）."""
    prev = prev_shot if isinstance(prev_shot, dict) else {}
    curr = next_shot if isinstance(next_shot, dict) else {}
    curr_text = _text(curr)

    if any(mark in curr_text for mark in _TIME_JUMP_MARKERS):
        return _choice("fadeblack", 0.6, "时间跳跃/回忆：用黑场淡入明确断裂",
                       "time_jump")
    prev_scene = str(prev.get("scene") or "")
    curr_scene = str(curr.get("scene") or "")
    if prev_scene and curr_scene and prev_scene != curr_scene:
        return _choice("fade", 0.5, "场景切换：淡入淡出", "scene_change")
    if _location_switch(prev, curr):
        return _choice("fade", 0.4, "同场景空间切换：短淡入淡出", "space_change")
    if any(w in curr_text for w in _ACTION_WORDS):
        return _choice("cut", 0.0, "强动作同场衔接：硬切保持动作连贯", "action_cut")
    return _choice("cut", 0.0, "同场景连续衔接：硬切", "continuity_cut")


def fill_missing_transitions(shots) -> list[dict | None]:
    """逐镜（含首镜）给出建议；首镜/已有显式转场的边界返回 None（保持原值）.

    返回列表与 `shots` 等长：索引 i 表示「进入第 i 镜」的边界。索引 0 恒为 None。
    """
    items = list(shots or [])
    out: list[dict | None] = []
    for i, shot in enumerate(items):
        if i == 0 or not isinstance(shot, dict) or str(shot.get("clip_transition") or "").strip():
            out.append(None)
            continue
        prev = items[i - 1] if isinstance(items[i - 1], dict) else {}
        out.append(choose_transition(prev, shot))
    return out


__all__ = ["choose_transition", "fill_missing_transitions"]
