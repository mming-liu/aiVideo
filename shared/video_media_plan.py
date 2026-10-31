# -*- coding: utf-8 -*-
"""video_media_plan - 视频素材的**最终角色**规划（提示词与请求共用一份）.

问题（2026-10-31 复核 P1）：提示词按"ff 首帧 + lf 尾帧 + 3 张过程关键帧"写参考图清单，
provider 随后按模型能力把素材裁成 2 张（甚至用 kf0/kf2 顶替首尾帧）。于是模型被告知
有 5 张图、实际收到 2 张，编号语义还对不上。

约定：**先按能力定最终素材及 role，再据此生成提示词，再编码/上传同一份素材**。
本模块只做纯计算（不读文件、不碰网络），两条链路都调用它，避免再出现"各裁各的"。

角色取值：`first_frame` / `last_frame` / `reference_image`。
"""
from __future__ import annotations


def plan_image_media(*, supports_reference_image: bool, image_path=None,
                     last_frame_path=None, keyframe_paths=()) -> dict:
    """图生视频的最终素材计划.

    分支（与 provider 的官方场景一一对应）：

    1. 有关键帧 且 模型支持 `reference_image` ⇒ 关键帧全部作参考图（ff/lf 不下发）；
    2. 否则只要 **有 ff 或 lf** ⇒ 用真正的首/尾帧，关键帧显式丢弃并记账；
       —— 不用 kf0/kf2 顶替：过程关键帧不保证是动作起点/终点；
    3. 既没有 ff/lf 又有关键帧 ⇒ 退化为 kf 首尾两张，并记 `keyframe_substituted`；
    4. 都没有 ⇒ 空计划（text-to-video）。

    Returns:
        `{"items": [{"path","role"}], "first_frame": str|None, "last_frame": str|None,
          "reference_images": [str], "dropped": [{"path","reason"}],
          "modifications": [dict], "mode": "reference"|"first_last"|"keyframe_fallback"|"none"}`
    """
    kfs = [str(p) for p in (keyframe_paths or ()) if p]
    ff = str(image_path) if image_path else None
    lf = str(last_frame_path) if last_frame_path else None
    items: list = []
    dropped: list = []
    modifications: list = []

    if kfs and supports_reference_image:
        items = [{"path": p, "role": "reference_image"} for p in kfs]
        for p in (ff, lf):
            if p:
                dropped.append({"path": p, "reason": "reference_mode_uses_keyframes"})
        if ff or lf:
            modifications.append({"kind": "reference_mode", "reason": "keyframes_take_priority",
                                  "dropped_first_last": bool(ff or lf)})
        return {"items": items, "first_frame": None, "last_frame": None,
                "reference_images": kfs, "dropped": dropped,
                "modifications": modifications, "mode": "reference"}

    if ff or lf:
        if ff:
            items.append({"path": ff, "role": "first_frame"})
        if lf:
            items.append({"path": lf, "role": "last_frame"})
        for p in kfs:
            dropped.append({"path": p, "reason": "model_unsupported_reference_image"})
        if kfs:
            modifications.append({"kind": "keyframe_degrade",
                                  "reason": "model_unsupported_reference_image",
                                  "dropped": len(kfs), "kept": 0,
                                  "first_last_source": "ff/lf"})
        return {"items": items, "first_frame": ff, "last_frame": lf,
                "reference_images": [], "dropped": dropped,
                "modifications": modifications, "mode": "first_last"}

    if kfs:
        first, last = kfs[0], kfs[-1]
        items = [{"path": first, "role": "first_frame"}]
        if last != first:
            items.append({"path": last, "role": "last_frame"})
        for p in kfs[1:-1]:
            dropped.append({"path": p, "reason": "no_first_last_frame"})
        modifications.append({"kind": "keyframe_substituted",
                              "reason": "no_first_last_frame",
                              "used": [first, last], "dropped": len(kfs[1:-1])})
        return {"items": items, "first_frame": first,
                "last_frame": last if last != first else None,
                "reference_images": [], "dropped": dropped,
                "modifications": modifications, "mode": "keyframe_fallback"}

    return {"items": [], "first_frame": None, "last_frame": None,
            "reference_images": [], "dropped": dropped,
            "modifications": modifications, "mode": "none"}


def reference_labels(items) -> list:
    """按最终素材清单生成"参考图N"的语义标签（顺序与请求一致）."""
    out = []
    for i, item in enumerate(items or (), 1):
        role = str(item.get("role") or "")
        if role == "first_frame":
            label = "视频起点画面（首帧），本镜头画面从这里开始"
        elif role == "last_frame":
            label = "视频终点画面（尾帧），动作结束后画面落在该构图"
        elif role == "reference_image":
            label = "参考图，人物/场景/道具以它为准"
        else:
            label = "参考素材"
        out.append("参考图%d：%s" % (i, label))
    return out
