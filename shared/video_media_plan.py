# -*- coding: utf-8 -*-
"""video_media_plan - 视频素材的**最终角色**规划（提示词与请求共用一份）.

问题（2026-10-31 复核 P1）：提示词按"ff 首帧 + lf 尾帧 + 3 张过程关键帧"写参考图清单，
provider 随后按模型能力把素材裁成 2 张（甚至用 kf0/kf2 顶替首尾帧）。于是模型被告知
有 5 张图、实际收到 2 张，编号语义还对不上。

约定：**先按能力定最终素材及 role，再据此生成提示词，再编码/上传同一份素材**。
本模块只做纯计算（不读文件、不碰网络），两条链路都调用它，避免再出现"各裁各的"。

角色取值：`first_frame` / `last_frame` / `reference_image`。

2026-11-01（素材模式治理 S01）：新增显式 `policy`（auto/first_last/first_only/reference）。
- `auto`：保留旧行为（能力位决定 kf 是否抢占 ff/lf）；
- `first_last`：强制 ff/lf，kf 丢弃并记账；缺 ff/lf 或模型不支持尾帧 ⇒ blocked；
- `first_only`：强制单首帧，lf/kf 丢弃并记账；缺 ff ⇒ blocked；
- `reference`：全部作 reference_image（kf 优先，否则 ff/lf）；无素材或模型不支持 ⇒ blocked。

所有分支都返回 `policy` 与 `blocked`；`blocked` 非空时 items 为空，调用方必须转异常/阻断，
不得继续生成空素材请求。
"""
from __future__ import annotations

import os

#: 合法策略值。非法值在解析期抛 ValueError（配置错误，确定性失败，不静默降级）。
VALID_MEDIA_POLICIES = ("auto", "first_last", "first_only", "reference")

POLICY_ENV = "VIDEO_MEDIA_POLICY"

#: 只在这些 provider 的 image 路径上支持显式策略（S01 冲突矩阵）。
_POLICY_PROVIDERS = ("volcengine", "agent_plan")


def active_media_policy() -> str:
    """读 `VIDEO_MEDIA_POLICY`（运行期读取，与 `_reference_mode()` 同风格）."""
    raw = (os.getenv(POLICY_ENV, "auto") or "auto").strip().lower()
    if raw not in VALID_MEDIA_POLICIES:
        raise ValueError(
            f"{POLICY_ENV}={raw!r} 非法；合法值：{', '.join(VALID_MEDIA_POLICIES)}")
    return raw


def _blocked(code: str, message: str, field_path: str = "") -> dict:
    return {"code": code, "message": message, "field_path": field_path}


def _result(*, policy: str, items=None, first_frame=None, last_frame=None,
            reference_images=None, dropped=None, modifications=None,
            mode: str = "none", blocked=None) -> dict:
    return {
        "items": list(items or []),
        "first_frame": first_frame,
        "last_frame": last_frame,
        "reference_images": list(reference_images or []),
        "dropped": list(dropped or []),
        "modifications": list(modifications or []),
        "mode": mode,
        "policy": policy,
        "blocked": blocked,
    }


def validate_media_policy_context(*, policy: str, provider: str,
                                  reference_mode: str) -> dict | None:
    """策略与运行上下文的冲突检查（入口与 provider 双重执行）.

    显式策略只支持 Seedance（volcengine/agent_plan）的 image 路径；
    其余组合 fail-closed，不做静默降级。
    """
    if policy == "auto":
        return None
    provider = (provider or "").strip().lower()
    reference_mode = (reference_mode or "").strip().lower()
    if provider not in _POLICY_PROVIDERS:
        return _blocked(
            "media_policy_conflict",
            f"{POLICY_ENV}={policy} 仅支持 Seedance（volcengine/agent_plan）的 image 路径，"
            f"当前 VIDEO_PROVIDER={provider or '未设置'}",
            POLICY_ENV)
    if reference_mode != "image":
        return _blocked(
            "media_policy_conflict",
            f"{POLICY_ENV}={policy} 仅在 VIDEO_REFERENCE_MODE=image 下生效，"
            f"当前={reference_mode or '未设置'}",
            POLICY_ENV)
    return None


def required_frames(policy: str) -> tuple[str, ...]:
    """该策略在入口必须存在的帧文件（reference 由实际选中集合判定，不在此列）."""
    return {
        "first_last": ("ff", "lf"),
        "first_only": ("ff",),
        "reference": (),
        "auto": ("ff", "lf"),
    }.get(policy, ("ff", "lf"))


def derive_media_mode(roles) -> str:
    """从**最终素材 role 序列**派生素材模式（不信任调用方自报的标签）.

    接受 provider 词汇（reference_image/reference_audio）与冻结契约词汇（reference）。
    """
    norm: list[str] = []
    for r in roles or ():
        r = str(r or "")
        norm.append("reference" if r in ("reference_image", "reference_audio", "reference")
                    else r)
    if not norm:
        return "none"
    if all(r == "reference" for r in norm):
        return "reference"
    has_first = "first_frame" in norm
    has_last = "last_frame" in norm
    if has_first and has_last:
        return "first_last"
    if has_first:
        return "first"
    if has_last:
        return "last"
    if "keyframe" in norm:
        return "keyframe_fallback"
    return "mixed"


def plan_image_media(*, supports_reference_image: bool, image_path=None,
                     last_frame_path=None, keyframe_paths=(),
                     supports_last_frame: bool = True,
                     policy: str = "auto") -> dict:
    """图生视频的最终素材计划.

    `policy="auto"` 分支（与旧实现逐字等价）：

    1. 有关键帧 且 模型支持 `reference_image` ⇒ 关键帧全部作参考图（ff/lf 不下发）；
    2. 否则只要 **有 ff 或 lf** ⇒ 用真正的首/尾帧，关键帧显式丢弃并记账；
       —— 不用 kf0/kf2 顶替：过程关键帧不保证是动作起点/终点；
    3. 既没有 ff/lf 又有关键帧 ⇒ 退化为 kf 首尾两张，并记 `keyframe_substituted`；
    4. 都没有 ⇒ 空计划（text-to-video）。

    Returns:
        `{"items": [{"path","role"}], "first_frame": str|None, "last_frame": str|None,
          "reference_images": [str], "dropped": [{"path","reason"}],
          "modifications": [dict],
          "mode": "reference"|"first_last"|"first"|"keyframe_fallback"|"none",
          "policy": str, "blocked": dict|None}`
    """
    if policy not in VALID_MEDIA_POLICIES:
        raise ValueError(
            f"policy={policy!r} 非法；合法值：{', '.join(VALID_MEDIA_POLICIES)}")

    kfs = [str(p) for p in (keyframe_paths or ()) if p]
    ff = str(image_path) if image_path else None
    lf = str(last_frame_path) if last_frame_path else None

    if policy == "auto":
        return _plan_auto(supports_reference_image=supports_reference_image,
                          supports_last_frame=supports_last_frame,
                          ff=ff, lf=lf, kfs=kfs)
    if policy == "first_last":
        return _plan_first_last(supports_last_frame=supports_last_frame,
                                ff=ff, lf=lf, kfs=kfs)
    if policy == "first_only":
        return _plan_first_only(ff=ff, lf=lf, kfs=kfs)
    return _plan_reference(supports_reference_image=supports_reference_image,
                           ff=ff, lf=lf, kfs=kfs)


def _plan_auto(*, supports_reference_image: bool, supports_last_frame: bool,
               ff, lf, kfs) -> dict:
    items: list = []
    dropped: list = []
    modifications: list = []
    # Fast 等只支持首帧的型号不得携带 last_frame；显式丢弃并记账，
    # 避免把不兼容素材发给 provider，或让提示词声明一张实际没发的图。
    if lf and not supports_last_frame:
        dropped.append({"path": lf, "reason": "model_unsupported_last_frame"})
        modifications.append({"kind": "last_frame_unsupported",
                              "reason": "model_unsupported_last_frame",
                              "dropped": lf})
        lf = None

    if kfs and supports_reference_image:
        items = [{"path": p, "role": "reference_image"} for p in kfs]
        for p in (ff, lf):
            if p:
                dropped.append({"path": p, "reason": "reference_mode_uses_keyframes"})
        if ff or lf:
            modifications.append({"kind": "reference_mode", "reason": "keyframes_take_priority",
                                  "dropped_first_last": bool(ff or lf)})
        return _result(policy="auto", items=items, reference_images=kfs,
                       dropped=dropped, modifications=modifications, mode="reference")

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
        return _result(policy="auto", items=items, first_frame=ff, last_frame=lf,
                       dropped=dropped, modifications=modifications,
                       mode="first_last" if (ff and lf) else "first")

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
        return _result(policy="auto", items=items, first_frame=first,
                       last_frame=last if last != first else None,
                       dropped=dropped, modifications=modifications,
                       mode="keyframe_fallback")

    return _result(policy="auto")


def _plan_first_last(*, supports_last_frame: bool, ff, lf, kfs) -> dict:
    if not supports_last_frame:
        return _result(policy="first_last", blocked=_blocked(
            "policy_unsupported_last_frame",
            "VIDEO_MEDIA_POLICY=first_last 需要模型支持尾帧，当前模型不支持",
            "references[role=last_frame]"))
    missing = [name for name, value in (("first_frame", ff), ("last_frame", lf)) if not value]
    if missing:
        return _result(policy="first_last", blocked=_blocked(
            "media_policy_missing_frame",
            f"VIDEO_MEDIA_POLICY=first_last 需要 {'/'.join(missing)}，当前缺失",
            "references"))
    items = [{"path": ff, "role": "first_frame"},
             {"path": lf, "role": "last_frame"}]
    dropped = [{"path": p, "reason": "policy_dropped_keyframes"} for p in kfs]
    modifications = [{"kind": "media_policy", "policy": "first_last",
                      "dropped_keyframes": len(kfs)}]
    return _result(policy="first_last", items=items, first_frame=ff, last_frame=lf,
                   dropped=dropped, modifications=modifications, mode="first_last")


def _plan_first_only(*, ff, lf, kfs) -> dict:
    if not ff:
        return _result(policy="first_only", blocked=_blocked(
            "media_policy_missing_frame",
            "VIDEO_MEDIA_POLICY=first_only 需要 first_frame，当前缺失",
            "references[role=first_frame]"))
    items = [{"path": ff, "role": "first_frame"}]
    dropped: list = []
    if lf:
        dropped.append({"path": lf, "reason": "policy_dropped_last_frame"})
    dropped.extend({"path": p, "reason": "policy_dropped_keyframes"} for p in kfs)
    modifications = [{"kind": "media_policy", "policy": "first_only",
                      "dropped_last_frame": bool(lf),
                      "dropped_keyframes": len(kfs)}]
    return _result(policy="first_only", items=items, first_frame=ff,
                   dropped=dropped, modifications=modifications, mode="first")


def _plan_reference(*, supports_reference_image: bool, ff, lf, kfs) -> dict:
    if not supports_reference_image:
        return _result(policy="reference", blocked=_blocked(
            "media_policy_unsupported",
            "VIDEO_MEDIA_POLICY=reference 需要模型支持 reference_image，当前模型不支持",
            "references[role=reference]"))
    dropped: list = []
    if kfs:
        selected = list(kfs)
        for p in (ff, lf):
            if p:
                dropped.append({"path": p, "reason": "policy_dropped_first_last"})
    else:
        selected = [p for p in (ff, lf) if p]
    if not selected:
        return _result(policy="reference", blocked=_blocked(
            "media_policy_no_material",
            "VIDEO_MEDIA_POLICY=reference 需要至少一张参考素材（关键帧或首尾帧），当前没有",
            "references"))
    items = [{"path": p, "role": "reference_image"} for p in selected]
    modifications = [{"kind": "media_policy", "policy": "reference",
                      "selected": len(selected), "dropped_first_last": len(dropped)}]
    return _result(policy="reference", items=items, reference_images=selected,
                   dropped=dropped, modifications=modifications, mode="reference")


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


def video_consumes_intermediate_frames() -> bool:
    """当前激活的视频模型是否真的消费中间关键帧（决定 step02a 要不要生成）.

    只有**注册表里明确**支持 reference_image 的 Seedance 型号才消费关键帧；
    supports_reference_image=False 的型号（1.0 pro / 1.5 pro）由本模块的
    plan_image_media 全部丢弃。未注册的 Seedance 型号按「会消费」处理——
    省图费不能以静默丢帧为代价（与 _keyframes_consumed_by_video 的旧注释一致）。
    非 Seedance 供应商走各自参考图路径，一律按会消费处理。
    """
    from shared.model_caps import (SEEDANCE_CAPS, active_video_is_seedance,
                                   active_video_model, get_seedance_cap,
                                   normalize_model_name)
    if not active_video_is_seedance():
        return True
    model = active_video_model()
    normalized = normalize_model_name(model or "")
    if not any(pat in normalized for pat, _ in SEEDANCE_CAPS):
        return True     # 未注册型号：保守按会消费处理
    return bool(get_seedance_cap(model).supports_reference_image)


def default_frame_selection(policy: str | None = None) -> set[str]:
    """Step02a 默认生成哪些帧；显式策略优先于能力位推断."""
    policy = policy or active_media_policy()
    if policy == "first_last":
        return {"ff", "lf"}
    if policy == "first_only":
        return {"ff"}
    if policy == "reference":
        return {"ff", "lf", "kf"}
    return {"ff", "lf", "kf"} if video_consumes_intermediate_frames() else {"ff", "lf"}
