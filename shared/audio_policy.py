"""Single source of truth for narration/audio source selection.

Priority:
  1. MERGE_AUDIO_MODE=tts_mix explicitly requests TTS.
  2. VIDEO_MODEL_VOICE=0 explicitly requests TTS.
  3. VIDEO_MODEL_VOICE=1 + model capability supports native audio -> native.
  4. VIDEO_MODEL_VOICE=1 but model is silent -> TTS fallback (never skip).

This prevents the historical failure mode where a silent model was configured with
native merge mode, step03 skipped TTS, and step04 produced a silent final video.
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


def resolve_audio_policy(*, actual_audio_present: bool | None = None) -> AudioPolicy:
    voice_pref = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
    merge_mode = os.getenv("MERGE_AUDIO_MODE", "native").strip().lower()
    if merge_mode not in ("native", "tts_mix"):
        merge_mode = "native"
    supports = bool(active_video_supports_audio())
    model = active_video_model()

    if merge_mode == "tts_mix":
        return AudioPolicy(False, True, "tts_mix", "MERGE_AUDIO_MODE=tts_mix", model, supports)
    if not voice_pref:
        return AudioPolicy(False, True, "tts_mix", "VIDEO_MODEL_VOICE=0", model, supports)
    if not supports:
        return AudioPolicy(
            False, True, "tts_mix",
            f"video model {model or 'unknown'} has no native audio",
            model, supports,
        )
    if actual_audio_present is False:
        return AudioPolicy(
            False, True, "tts_mix",
            f"video model {model or 'unknown'} advertises audio but clips have no audio stream",
            model, supports,
        )
    return AudioPolicy(True, False, "native", "model supports native audio", model, supports)


__all__ = ["AudioPolicy", "resolve_audio_policy"]
