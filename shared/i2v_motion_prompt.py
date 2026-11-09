# -*- coding: utf-8 -*-
"""I2V 首尾帧模式的精简运动 prompt（M8.1，2026-11-09）.

首尾帧模式已经把**外观/构图/终态**交给两张图，文字不再重复人物长相与场景
细节，只描述「从首帧到末帧发生了什么」：动作顺序、相机运动、连续性约束。

输出长度控制在 ``[MIN_CHARS, MAX_CHARS]``（150–220 字）：
- 太短 → 补运动连续性/无字幕/首尾一致等通用约束（不引入剧本内容）；
- 太长 → 按优先级丢弃低权重分句（动作 > 运镜 > 首尾锚点 > 通用约束），
  只在分句边界处理，不把句子截半。

纯函数、零模型调用；不写死任何小说/角色/章节内容。
"""
from __future__ import annotations

MIN_CHARS = 150
MAX_CHARS = 220

_HEAD = "首帧到尾帧的运动"
_TAIL_CANDIDATES = (
    "动作连续推进，不跳切、不瞬移，人物身份、服装与场景在运动过程中保持不变",
    "镜头运动平稳，起点与首帧构图一致，终点与末帧终态一致",
    "只表现动作与运镜，不重复描述人物长相、服饰细节与静态环境",
    "画面无字幕、无文字、无水印",
    "动作节奏按镜头时长均匀展开，结尾自然停在末帧状态",
)
_ANCHOR_TAIL = "从首帧的起始姿态开始，连续演变到末帧的终态"


def _clauses(text: str) -> list[str]:
    import re
    parts = re.split(r"[，。；！？\n]+", str(text or ""))
    return [p.strip(" ，。；！？\t\r\n") for p in parts if p.strip(" ，。；！？\t\r\n")]


def _strip_time_labels(clauses: list[str]) -> list[str]:
    import re
    out = []
    for clause in clauses:
        clause = re.sub(r"^(?:前|后|第)?\s*\d+(?:\.\d+)?\s*(?:秒|s)\s*[:：]\s*",
                        "", clause, flags=re.IGNORECASE).strip()
        if clause:
            out.append(clause)
    return out


def _fit_text(text: str, limit: int) -> str:
    """按分句边界收窄到 limit；单句超限时才在标点/空格附近截断."""
    if len(text) <= limit:
        return text
    out = ""
    for clause in _clauses(text):
        candidate = clause if not out else out + "，" + clause
        if len(candidate) + 1 > limit:
            break
        out = candidate
    if out:
        return out
    cut = max(text.rfind("，", 0, limit - 1), text.rfind("；", 0, limit - 1),
              text.rfind("、", 0, limit - 1))
    if cut <= 0:
        cut = limit - 1
    return text[:cut].rstrip("，；、") + "…"


def build_motion_prompt(shot: dict, *, min_chars: int = MIN_CHARS,
                        max_chars: int = MAX_CHARS) -> str:
    """构建首尾帧模式专用精简运动 prompt（150–220 字，确定性）."""
    if max_chars <= min_chars:
        raise ValueError("max_chars 必须大于 min_chars")
    shot = shot if isinstance(shot, dict) else {}
    motion = str(shot.get("motion_desc") or shot.get("prompt") or "")
    camera = str(shot.get("camera_movement") or "").strip()
    # 动作分句优先级最高；限制在剩余预算内，避免一开始就挤掉通用约束。
    motion_text = _fit_text("，".join(_strip_time_labels(_clauses(motion))),
                            max_chars - len(_HEAD) - 2)
    parts: list[str] = [_HEAD]
    if motion_text:
        parts.append(motion_text)
    if camera:
        parts.append("运镜：" + _fit_text(camera, 40))
    parts.append(_ANCHOR_TAIL)

    def _render() -> str:
        return "。".join(p for p in parts if p) + "。"

    # 太短：按通用约束顺序补足（不引入剧情内容）。
    for tail in _TAIL_CANDIDATES:
        if len(_render()) >= min_chars:
            break
        if tail not in parts:
            parts.append(tail)
    # 仍不足（极短镜头）：再补一条首尾一致说明（通用、不含具体内容）。
    if len(_render()) < min_chars:
        extra = "保持同一人物与同一空间的连续关系，运动结束后停在末帧画面"
        if extra not in parts:
            parts.append(extra)

    # 太长：从最低优先级（末尾通用约束）开始丢，保留动作/运镜/锚点。
    while len(_render()) > max_chars and len(parts) > 3:
        parts.pop()
    if len(_render()) > max_chars:
        # 只剩三段仍然超长：收窄运动正文。
        excess = len(_render()) - max_chars
        motion_text = _fit_text(parts[1], max(20, len(parts[1]) - excess - 1))
        parts[1] = motion_text
        while len(_render()) > max_chars and len(parts) > 2:
            parts.pop()
    text = _render()
    if len(text) > max_chars:
        text = text[:max_chars - 1].rstrip("，；、。") + "。"
    return text


__all__ = ["MAX_CHARS", "MIN_CHARS", "build_motion_prompt"]
