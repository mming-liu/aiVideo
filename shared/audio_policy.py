"""Single source of truth for narration/audio source selection.

Priority:
  1. MERGE_AUDIO_MODE=tts_mix explicitly requests TTS.
  2. VIDEO_MODEL_VOICE=0 explicitly requests TTS.
  3. VIDEO_MODEL_VOICE=1 + model capability supports native audio -> native.
  4. VIDEO_MODEL_VOICE=1 but model is silent -> TTS fallback (never skip).

This prevents the historical failure mode where a silent model was configured with
native merge mode, step03 skipped TTS, and step04 produced a silent final video.

**图外旁白（off-screen narration）是正交的一维**（2026-09-30 复核第 7 轮）：
它不走上面这套「对白由谁发声」的选择——视频模型不会念它（旁白是原文叙述句，
既没有引号也没有说话人前缀，step02 的 prompt 不把它当台词注入），所以**无论
native 还是 tts_mix 都必须有自己的 TTS 音轨**，step04 再把它叠在视频原声之上
（角色原声保留）。不许用「给旁白加引号/说话人前缀」或「整体切 tts_mix」绕过。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from shared.model_caps import active_video_model, active_video_supports_audio


@dataclass(frozen=True)
class AudioPolicy:
    native_voice: bool
    tts_required: bool
    effective_merge_mode: str  # native | tts_mix
    reason: str
    video_model: str | None
    model_supports_audio: bool
    #: 本集是否**必须**为图外旁白合成独立音轨（计划里有旁白条目即为 True，
    #: 与 `tts_required`／`effective_merge_mode` 相互独立）
    narration_tts_required: bool = False


def resolve_audio_policy(*, actual_audio_present: bool | None = None,
                         narration_present: bool = False) -> AudioPolicy:
    """`narration_present`：本集计划里有没有图外旁白（有 ⇒ 必须有独立 TTS 音轨）."""
    narration = bool(narration_present)
    voice_pref = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
    merge_mode = os.getenv("MERGE_AUDIO_MODE", "native").strip().lower()
    if merge_mode not in ("native", "tts_mix"):
        merge_mode = "native"
    supports = bool(active_video_supports_audio())
    model = active_video_model()

    if merge_mode == "tts_mix":
        return AudioPolicy(False, True, "tts_mix", "MERGE_AUDIO_MODE=tts_mix",
                           model, supports, narration)
    if not voice_pref:
        return AudioPolicy(False, True, "tts_mix", "VIDEO_MODEL_VOICE=0",
                           model, supports, narration)
    if not supports:
        return AudioPolicy(
            False, True, "tts_mix",
            f"video model {model or 'unknown'} has no native audio",
            model, supports, narration,
        )
    if actual_audio_present is False:
        return AudioPolicy(
            False, True, "tts_mix",
            f"video model {model or 'unknown'} advertises audio but clips have no audio stream",
            model, supports, narration,
        )
    return AudioPolicy(True, False, "native", "model supports native audio",
                       model, supports, narration)


# ---------------------------------------------------------------------------
# 呈现模式（`screen_hybrid`）：新模式的**声音合同**（主专题 §5）
# ---------------------------------------------------------------------------
class ModeAudioConflict(ValueError):
    """新模式出现图外旁白 ⇒ **合同冲突**，交回规划者；不静默降级、不伪造."""


def narration_contract(plan, *, mode: str | None = None) -> dict:
    """按**计划自称的模式**给出声音合同 → `{mode, narration_present, allowed, reason}`.

    §5 的五条行为收在这里，Step03/Step04 **共用这一份**：

    | 输入 | 行为 |
    |---|---|
    | 旧模式有旁白 | 保持独立旁白 TTS 要求 |
    | 旧模式无旁白 | 保持现状 |
    | 新模式合法且无旁白 | 卡片不创建 SpeechUnit、不调旁白 TTS |
    | 新模式出现旁白 | **拒收合同冲突**，返回规划者 |
    | 新模式有人物对白 | 按现有 native/tts 策略处理 |

    「卡片与对白文字相同」**依结构身份分流**，不按文字相同合并——这里不给任何按文本
    去重的口子（主专题 §5 明文）。
    """
    from shared.presentation_contract import MODE_HYBRID, MODE_LEGACY, plan_mode
    _mode = str(mode or plan_mode(plan) or MODE_LEGACY)
    _lines = (plan or {}).get("narration_lines") or []
    _present = bool(_lines)
    if _mode == MODE_HYBRID:
        if _present:
            return {"mode": _mode, "narration_present": True, "allowed": False,
                    "reason": "screen_hybrid 模式不允许图外旁白（narration_lines 有 %d 条）"
                              "——必要信息要重新分配给画面或文字卡；不静默清空、不伪造"
                              % len(_lines)}
        return {"mode": _mode, "narration_present": False, "allowed": True,
                "reason": "screen_hybrid 合法且无旁白：文字卡不创建 SpeechUnit、"
                          "不调旁白 TTS"}
    return {"mode": _mode, "narration_present": _present, "allowed": True,
            "reason": "legacy_audio：旁白按现有独立 TTS 要求" if _present
                      else "legacy_audio：无旁白，保持现状"}


def require_narration_contract(plan, *, mode: str | None = None) -> dict:
    """`narration_contract` + 冲突时抛 `ModeAudioConflict`（Step03/04 的入口判据）."""
    _c = narration_contract(plan, mode=mode)
    if not _c["allowed"]:
        raise ModeAudioConflict(_c["reason"])
    return _c


__all__ = ["AudioPolicy", "resolve_audio_policy", "narration_contract",
           "require_narration_contract", "ModeAudioConflict"]
