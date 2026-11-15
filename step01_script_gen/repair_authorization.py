# -*- coding: utf-8 -*-
"""M02：**同一份结构化授权记录**驱动请求说明、答卷示例与应用校验.

修复前的 `compact=True` 示例有三个确定性缺陷（2026-11-15 只读核对 + 复现）：

1. 把精确授权路径 `frame_layouts.kfs[0][0].pose` 截成顶层 `frame_layouts`，
   等于请求模型提交一个**本轮未授权**的父字段；
2. 给 `frame_layouts` 的占位值是**列表**，而实际字段是 `{ff, lf, kfs}` 对象，
   `apply_scoped_field_updates.compatible()` 会直接判类型不匹配；
3. 只要出现任意 `frame_layouts.kfs*` 子路径就展示 `keyframe_additions`，
   而真正的补帧授权只认整表级（`_whole_kfs_authorized`）。

此外 `apply_scoped_field_updates` 明确拒绝含 `*` 的路径：授权集合里的
`keyframes.*.desc` 一类通配条目必须按**当前稿件结构**展开成具体下标路径，
否则示例本身就是一个会被拒收的答卷。

本模块只生成「可写路径 / 只读关联字段 / 补帧许可 / 类型正确的占位值」，
不扩大也不缩小授权面：`allowed` 原集合仍是应用校验的唯一权威来源。
"""
from __future__ import annotations

import re
from typing import Any

from step01_script_gen.repair_fields import (
    _norm_keep_index, _whole_kfs_authorized, path_allowed)

_MISSING = object()
_BRACKET = re.compile(r"\[(\d+)\]")

# 与补帧表达互斥：`keyframes` / `frame_layouts.kfs` 整表只由 keyframe_additions
# 成对表达，不在 updates 示例里诱导整表重写（run e / h / j 都栽在这里）。
# `frame_layouts` 整表本身仍是本轮合法表达（run j 的 shot_4 即如此），保留并
# 给正确的 dict 骨架占位，不跳过、也不提升。
_FRAME_TABLES = {"keyframes", "frame_layouts.kfs"}

# 必须读取用于对齐、但不得改写的关联字段（除非本轮显式授权）。
_RELATED_READONLY = (
    "keyframes", "frame_layouts.kfs", "duration", "prompt",
    "narration_segment", "subtitle", "temporal_plan",
)

_PLACEHOLDER_TEXT = "<按该问题改写后的完整字段文本>"


def _segments(path: str) -> list:
    p = _BRACKET.sub(r".\1", str(path or "").strip())
    return [s for s in p.split(".") if s]


def _walk(node: Any, segs: list, prefix: str = ""):
    """沿路径段下钻，只产出**当前稿上真实存在**的完整具体路径."""
    if not segs:
        if prefix:
            yield prefix
        return
    seg, rest = segs[0], segs[1:]
    if isinstance(node, dict):
        if seg == "*":
            for k, v in node.items():
                yield from _walk(v, rest, "%s.%s" % (prefix, k) if prefix else str(k))
        elif seg in node:
            yield from _walk(node[seg], rest,
                             "%s.%s" % (prefix, seg) if prefix else seg)
    elif isinstance(node, list):
        if seg == "*":
            for i, v in enumerate(node):
                yield from _walk(v, rest, "%s.%d" % (prefix, i) if prefix else str(i))
        elif seg.isdigit() and int(seg) < len(node):
            i = int(seg)
            yield from _walk(node[i], rest,
                             "%s.%d" % (prefix, i) if prefix else str(i))


def _get_at(node: Any, segs: list):
    for seg in segs:
        if isinstance(node, dict):
            if seg not in node:
                return _MISSING
            node = node[seg]
        elif isinstance(node, list):
            if not seg.isdigit() or int(seg) >= len(node):
                return _MISSING
            node = node[int(seg)]
        else:
            return _MISSING
    return node


def _placeholder(value: Any, depth: int = 0):
    """按当前值的类型/结构生成占位，保证 `compatible()` 不会因为类型拒收."""
    if value is _MISSING or value is None:
        return _PLACEHOLDER_TEXT
    if isinstance(value, str):
        return _PLACEHOLDER_TEXT
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        if depth >= 2:
            return {}
        return {str(k): _placeholder(v, depth + 1)
                for k, v in list(value.items())[:8]}
    if isinstance(value, list):
        if depth >= 2 or not value:
            return []
        return [_placeholder(value[0], depth + 1)]
    return _PLACEHOLDER_TEXT


def repair_authorization_spec(shot: dict, allowed) -> dict:
    """把本轮授权集合展开成**可执行**的请求规格.

    返回：
      `writable_paths`：当前稿上真实存在、不含通配符、且经 `path_allowed`
      复核的具体路径（可直接作为 `updates.field_path`）。
      `placeholders`：同路径上类型正确的占位值。
      `keyframe_additions_allowed`：复用守卫的权威补帧授权判断。
      `read_only_related`：必须读取用于对齐、但本轮不得修改的字段。
      `raw_allowed`：原授权集合（应用校验仍以它为准）。
    """
    raw = [str(a).strip() for a in (allowed or ()) if str(a or "").strip()]
    shot = shot if isinstance(shot, dict) else {}
    kf_ok = _whole_kfs_authorized(raw)
    writable: list = []
    seen = set()
    for a in raw:
        norm = _norm_keep_index(a)
        if kf_ok and norm in _FRAME_TABLES:
            # 补帧授权开时，整表容器只走 keyframe_additions（成对形状）。
            continue
        for p in _walk(shot, _segments(a)):
            if "*" in p or p in seen:
                continue
            if not path_allowed(p, raw):
                continue
            seen.add(p)
            writable.append(p)
    # 同一目标上父路径与子路径同时提交会被 `apply_scoped_field_updates` 判
    # 「重复或重叠字段补丁路径」。示例只保留最具体的那一层，保证逐条可应用。
    writable = [p for p in writable
                if not any(q.startswith(p + ".") for q in writable if q != p)]
    writable.sort()

    placeholders = {p: _placeholder(_get_at(shot, _segments(p)))
                    for p in writable}
    read_only = [p for p in _RELATED_READONLY
                 if p not in seen and _get_at(shot, _segments(p)) is not _MISSING]
    return {
        "raw_allowed": sorted(raw),
        "writable_paths": writable,
        "placeholders": placeholders,
        "keyframe_additions_allowed": bool(kf_ok),
        "read_only_related": read_only,
    }


def render_authorization_guidance(spec_by_target: dict) -> str:
    """两处修复请求共用的说明：**允许修改** 与 **必须读取** 分开讲清楚."""
    has_kf = any((v or {}).get("keyframe_additions_allowed")
                 for v in (spec_by_target or {}).values())
    lines = [
        "**字段分两类**：`allowed_fields` 是本轮**允许修改**的路径；`updates.field_path`",
        "只能取其中在示例里给出的**具体路径**（不得写成父字段、不得含 `*` 通配、",
        "不得整表重写未点名的容器）。`keyframes` / `frame_layouts.kfs` / `duration` /",
        "`prompt` / `narration_segment` / `subtitle` 等是**必须读取用于对齐、但不得改写**",
        "的关联字段，除非它们同时出现在 `allowed_fields` 中。示例里的占位值只表示",
        "字段类型与结构，不是要照抄的内容。",
    ]
    if has_kf:
        lines.append("本轮**允许补帧**：缺帧/缺段只能用 `keyframe_additions` 的"
                     "`frame` + `layout` 成对形状，禁止用 `updates` 整表重写 kfs。")
    else:
        lines.append("本轮**未授权补帧**：不要提交 `keyframe_additions`，"
                     "也不要改动关键帧数量。")
    return "".join(lines) + "\n"
