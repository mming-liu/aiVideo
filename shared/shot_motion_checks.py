# -*- coding: utf-8 -*-
"""镜头运镜一致性检查（离线、零模型调用）.

S22-D：step01 出稿时 `camera_movement` 与 `motion_desc` 可能互相矛盾
（例：camera_movement=固定机位，motion_desc 却写"镜头掠过荷丛"）。视频模型
按参数执行，文本里的运镜不会发生；同时首尾帧若几乎没有可观察差异，位移类
动作（船滑出、人物走动）也不会被插值出来。
"""
from __future__ import annotations

import difflib

#: 文本里表示"镜头在动"的词
CAMERA_MOVE_TERMS = (
    "镜头掠过", "镜头横移", "镜头推近", "镜头拉远", "镜头跟拍", "镜头环绕",
    "镜头摇", "镜头升", "镜头降", "横移", "推近", "推镜", "拉远", "拉镜",
    "跟拍", "跟随", "环绕", "摇镜", "摇摄", "升降", "俯冲", "拉升",
)

#: camera_movement 的"固定"取值
FIXED_CAMERA_VALUES = ("固定机位", "固定", "static", "fixed", "locked")

#: 文本里表示"主体发生位移/状态改变"的词
DISPLACE_TERMS = (
    "滑出", "驶出", "走出", "跑出", "进入画面", "离开画面", "穿过", "越过",
    "靠近", "远离", "腾空", "起身", "转身", "回头", "倒下", "站起", "坐下",
)


def motion_warnings(shot: dict) -> list[dict]:
    """返回该镜头的运镜一致性告警（空列表 = 未发现矛盾）."""
    warnings: list[dict] = []
    camera = str(shot.get("camera_movement") or "").strip().lower()
    motion = str(shot.get("motion_desc") or "")
    ff = str(shot.get("ff_desc") or "").strip()
    lf = str(shot.get("lf_desc") or "").strip()

    if any(v in camera for v in FIXED_CAMERA_VALUES):
        hits = [t for t in CAMERA_MOVE_TERMS if t in motion]
        if hits:
            warnings.append({
                "code": "camera_motion_conflict",
                "field_path": "camera_movement/motion_desc",
                "message": "camera_movement=%r 但 motion_desc 写了镜头运动 %r：模型会按固定机位执行，"
                           "文本里的运镜不会发生" % (shot.get("camera_movement"), hits),
                "needed_change": "要么把 camera_movement 改成对应运镜，要么删掉 motion_desc 里的镜头运动描述",
            })

    if ff and lf and any(t in motion for t in DISPLACE_TERMS):
        ratio = difflib.SequenceMatcher(None, ff, lf).ratio()
        if ratio >= 0.85:
            warnings.append({
                "code": "frame_displacement_missing",
                "field_path": "ff_desc/lf_desc",
                "message": "motion_desc 描述了位移/状态改变，但 ff_desc 与 lf_desc 相似度 %.2f，"
                           "首尾帧缺少可观察差异，图生视频不会插值出位移" % ratio,
                "needed_change": "让 lf_desc 写出终态的可见差异（位置、构图、主体状态）",
            })
    return warnings
