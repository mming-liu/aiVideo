# -*- coding: utf-8 -*-
"""S05/M1：逐镜音轨超出视频窗口时的**显式**处理策略（纯函数，无 ffmpeg 依赖）.

背景：`02b → 03 → 04` 的时长倒挂（配音比视频长）此前只有两种出口——静默
`atrim` 截掉人声尾音，或整章失败。本模块把选择变成**显式策略 + 可核对的调整记录**：

- ``fail``（默认）：超窗 > 容差即报 ``audio_overflow_truncated``，**绝不静默截字**；
- ``pad_video_tail``：末帧克隆补齐（≤ ``max_pad_sec``，默认 1.0s），整条人声保留；
- ``gentle_atempo``：轻度加速（≤ ``max_atempo``，默认 1.12x；超过即拒收，不失真加速）；
- ``trim``：旧行为，仅由 ``TTS_MIX_ALLOW_TRIM=1`` 显式开启；仍如实记录被截时长。

容差默认 0.15s：更小的超窗属于帧量化/容器误差，按截断无听感影响处理，但同样记录。
所有判断都是**纯算术**：输入是实测时长（ffprobe），不猜、不调用模型。
"""
from __future__ import annotations

from dataclasses import dataclass, field

POLICY_FAIL = "fail"
POLICY_PAD = "pad_video_tail"
POLICY_ATEMPO = "gentle_atempo"
POLICY_TRIM = "trim"
VALID_POLICIES = (POLICY_FAIL, POLICY_PAD, POLICY_ATEMPO, POLICY_TRIM)

ENV_POLICY = "MERGE_AUDIO_OVERFLOW_POLICY"
ENV_LEGACY_ALLOW_TRIM = "TTS_MIX_ALLOW_TRIM"

DEFAULT_TOLERANCE_SEC = 0.15
DEFAULT_MAX_PAD_SEC = 1.0
DEFAULT_MAX_ATEMPO = 1.12

#: 结构化缺陷码（报告层直接透传，不从文案反推）
CODE_TRUNCATED = "audio_overflow_truncated"
CODE_EXCEEDS_PAD = "audio_overflow_exceeds_pad"
CODE_EXCEEDS_ATEMPO = "audio_overflow_exceeds_atempo"
CODE_INVALID_POLICY = "audio_overflow_policy_invalid"
CODE_TRIM_DISCLOSED = "audio_overflow_trimmed"


def resolve_policy(explicit: str | None = None, *, legacy_allow_trim: bool = False) -> str:
    """解析生效策略（显式值优先；旧开关 ``TTS_MIX_ALLOW_TRIM=1`` 等价于 ``trim``）."""
    raw = str(explicit or "").strip().lower()
    if raw:
        if raw not in VALID_POLICIES:
            raise ValueError("%s=%r 非法；合法值：%s"
                             % (ENV_POLICY, raw, ", ".join(VALID_POLICIES)))
        return raw
    if legacy_allow_trim:
        return POLICY_TRIM
    return POLICY_FAIL


@dataclass
class AudioAdjustment:
    """一次时长倒挂的处理记录（写进合并报告/终检，不静默）.  """

    clip_index: int
    shot_index: int | None
    action: str                  # "none" | "tolerated" | "pad" | "atempo" | "trim"
    clip_sec: float
    audio_sec: float
    overflow_sec: float
    applied_sec: float = 0.0     # pad 秒数 / atempo 比值 / 被截秒数
    code: str = ""

    def to_dict(self) -> dict:
        return {"clip_index": int(self.clip_index), "shot_index": self.shot_index,
                "action": self.action, "clip_sec": round(float(self.clip_sec), 6),
                "audio_sec": round(float(self.audio_sec), 6),
                "overflow_sec": round(float(self.overflow_sec), 6),
                "applied_sec": round(float(self.applied_sec), 6), "code": self.code}


@dataclass
class AudioFitPlan:
    policy: str
    tolerance_sec: float = DEFAULT_TOLERANCE_SEC
    clip_durations: list = field(default_factory=list)   # 调整后的逐镜时长
    pads: dict = field(default_factory=dict)             # {clip_index: pad_sec}
    atempo: dict = field(default_factory=dict)           # {clip_index: ratio}
    adjustments: list = field(default_factory=list)      # [AudioAdjustment]
    blocked: list = field(default_factory=list)          # [{clip_index, code, message, ...}]

    @property
    def ok(self) -> bool:
        return not self.blocked

    def to_dict(self) -> dict:
        return {
            "policy": self.policy,
            "tolerance_sec": self.tolerance_sec,
            "status": "pass" if self.ok else "blocked",
            "pads": {int(k): round(float(v), 6) for k, v in self.pads.items()},
            "atempo": {int(k): round(float(v), 6) for k, v in self.atempo.items()},
            "adjustments": [a.to_dict() for a in self.adjustments],
            "blocked": list(self.blocked),
        }


def plan_audio_fit(tts_secs, clip_secs, *, shot_indexes=None,
                   policy: str = POLICY_FAIL,
                   tolerance_sec: float = DEFAULT_TOLERANCE_SEC,
                   max_pad_sec: float = DEFAULT_MAX_PAD_SEC,
                   max_atempo: float = DEFAULT_MAX_ATEMPO) -> AudioFitPlan:
    """按策略计算逐镜调整方案（纯算术）.

    Args:
        tts_secs: 与 clip 等长的实测配音时长；``None``/0 表示该镜没有独立对白轨。
        clip_secs: 逐镜实测视频时长。
        shot_indexes: 与 clip 等长的分镜号（只用于报告定位，可缺省）。
        policy: :data:`VALID_POLICIES` 之一。
    """
    norm = str(policy or "").strip().lower()
    if norm not in VALID_POLICIES:
        raise ValueError("%s=%r 非法；合法值：%s"
                         % (ENV_POLICY, policy, ", ".join(VALID_POLICIES)))
    clip_list = [max(0.0, float(c or 0.0)) for c in (clip_secs or [])]
    tts_list = list(tts_secs or [])
    if len(tts_list) < len(clip_list):
        tts_list.extend([None] * (len(clip_list) - len(tts_list)))
    plan = AudioFitPlan(policy=norm, tolerance_sec=float(tolerance_sec),
                        clip_durations=list(clip_list))
    tol = max(0.0, float(tolerance_sec))
    for i, clip_sec in enumerate(clip_list):
        shot_index = (int(shot_indexes[i]) if shot_indexes is not None
                      and i < len(shot_indexes) else None)
        raw_audio = tts_list[i]
        try:
            audio_sec = float(raw_audio) if raw_audio is not None else 0.0
        except (TypeError, ValueError):
            audio_sec = 0.0
        if audio_sec <= 0 or clip_sec <= 0:
            continue
        overflow = audio_sec - clip_sec
        if overflow <= tol:
            if overflow > 0:
                plan.adjustments.append(AudioAdjustment(
                    i, shot_index, "tolerated", clip_sec, audio_sec, overflow,
                    applied_sec=overflow, code=CODE_TRIM_DISCLOSED))
            continue
        if norm == POLICY_FAIL:
            plan.blocked.append({
                "clip_index": i, "shot_index": shot_index, "code": CODE_TRUNCATED,
                "clip_sec": round(clip_sec, 6), "audio_sec": round(audio_sec, 6),
                "overflow_sec": round(overflow, 6),
                "message": ("镜头 %s 配音实测 %.2fs 超过视频 %.2fs（+%.2fs）——默认策略"
                            "禁止 atrim 静默截字。请改台词/拆镜，或显式设 %s=%s/%s"
                            % (shot_index if shot_index is not None else i, audio_sec,
                               clip_sec, overflow, ENV_POLICY, POLICY_PAD, POLICY_ATEMPO)),
            })
            continue
        if norm == POLICY_PAD:
            if overflow <= float(max_pad_sec) + 1e-9:
                plan.pads[i] = overflow
                plan.clip_durations[i] = round(clip_sec + overflow, 6)
                plan.adjustments.append(AudioAdjustment(
                    i, shot_index, "pad", clip_sec, audio_sec, overflow,
                    applied_sec=overflow))
            else:
                plan.blocked.append({
                    "clip_index": i, "shot_index": shot_index, "code": CODE_EXCEEDS_PAD,
                    "clip_sec": round(clip_sec, 6), "audio_sec": round(audio_sec, 6),
                    "overflow_sec": round(overflow, 6),
                    "max_pad_sec": float(max_pad_sec),
                    "message": ("镜头 %s 配音超窗 %.2fs 超过末帧补齐上限 %.2fs——"
                                "不允许把视频尾部拉长掩盖超长台词"
                                % (shot_index if shot_index is not None else i,
                                   overflow, float(max_pad_sec))),
                })
            continue
        if norm == POLICY_ATEMPO:
            ratio = audio_sec / clip_sec
            if ratio <= float(max_atempo) + 1e-9:
                plan.atempo[i] = round(ratio, 6)
                plan.adjustments.append(AudioAdjustment(
                    i, shot_index, "atempo", clip_sec, audio_sec, overflow,
                    applied_sec=ratio))
            else:
                plan.blocked.append({
                    "clip_index": i, "shot_index": shot_index, "code": CODE_EXCEEDS_ATEMPO,
                    "clip_sec": round(clip_sec, 6), "audio_sec": round(audio_sec, 6),
                    "overflow_sec": round(overflow, 6),
                    "required_ratio": round(ratio, 6), "max_atempo": float(max_atempo),
                    "message": ("镜头 %s 需要 %.3fx 加速才能塞进 %.2fs，超过不失真上限"
                                " %.2fx——回内容修订/拆镜，不做加速朗读"
                                % (shot_index if shot_index is not None else i, ratio,
                                   clip_sec, float(max_atempo))),
                })
            continue
        # trim：旧行为显式开启，仍如实记录被截时长
        plan.adjustments.append(AudioAdjustment(
            i, shot_index, "trim", clip_sec, audio_sec, overflow,
            applied_sec=overflow, code=CODE_TRIM_DISCLOSED))
    return plan


__all__ = [
    "CODE_EXCEEDS_ATEMPO", "CODE_EXCEEDS_PAD", "CODE_INVALID_POLICY",
    "CODE_TRIM_DISCLOSED", "CODE_TRUNCATED", "DEFAULT_MAX_ATEMPO",
    "DEFAULT_MAX_PAD_SEC", "DEFAULT_TOLERANCE_SEC", "ENV_LEGACY_ALLOW_TRIM",
    "ENV_POLICY", "AudioAdjustment", "AudioFitPlan", "POLICY_ATEMPO",
    "POLICY_FAIL", "POLICY_PAD", "POLICY_TRIM", "VALID_POLICIES",
    "plan_audio_fit", "resolve_policy",
]
