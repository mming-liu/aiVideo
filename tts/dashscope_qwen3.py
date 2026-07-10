import base64
import sys
sys.stdout.reconfigure(encoding='utf-8')

from typing import Optional

import requests

from .config import config
from .models import TTSRequest, TTSSuccessResult, TTSFailureResult, TTSServiceResult
from .service import TTSServiceInterface


DASHSCOPE_QWEN3_TTS_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
DASHSCOPE_QWEN3_TTS_MODEL = "qwen3-tts-vd-2026-01-26"
DASHSCOPE_TTS_MAX_CHARS = 600

KNOWN_VOICE_IDS = ["longxiaxiaoxuan", "longwan", "longchen", "longyue", "longyuan"]

_SPLIT_HINT_CHARS = {
    "。", "！", "？", "；", "：", "，", "、",
    ".", "!", "?", ";", ",", ":",
    "\n",
}


def _split_text_by_limit(text, max_chars=600):
    trimmed = text.strip()
    if not trimmed:
        return []
    chars = list(trimmed)
    if len(chars) <= max_chars:
        return [trimmed]
    segments = []
    cursor = 0
    while cursor < len(chars):
        hard_end = min(cursor + max_chars, len(chars))
        if hard_end == len(chars):
            s = "".join(chars[cursor:hard_end]).strip()
            if s:
                segments.append(s)
            break
        split_point = hard_end
        for idx in range(hard_end - 1, cursor, -1):
            if chars[idx] in _SPLIT_HINT_CHARS:
                split_point = idx + 1
                break
        s = "".join(chars[cursor:split_point]).strip()
        if not s:
            raise ValueError("TTS split failed at cursor " + str(cursor))
        segments.append(s)
        cursor = split_point
        while cursor < len(chars) and chars[cursor].isspace():
            cursor += 1
    return segments


def _decode_wav(buffer):
    if len(buffer) < 44:
        raise ValueError("WAV too short")
    if buffer[0:4] != b"RIFF" or buffer[8:12] != b"WAVE":
        raise ValueError("Invalid WAV header")
    fmt = None
    pcm_data = None
    offset = 12
    while offset + 8 <= len(buffer):
        chunk_id = buffer[offset:offset + 4].decode("ascii", errors="replace")
        chunk_size = int.from_bytes(buffer[offset + 4:offset + 8], "little")
        cs = offset + 8
        ce = cs + chunk_size
        if ce > len(buffer):
            raise ValueError("WAV chunk out of range")
        if chunk_id == "fmt ":
            if chunk_size < 16:
                raise ValueError("Invalid WAV fmt chunk")
            fmt = {
                "audio_format": int.from_bytes(buffer[cs:cs + 2], "little"),
                "num_channels": int.from_bytes(buffer[cs + 2:cs + 4], "little"),
                "sample_rate": int.from_bytes(buffer[cs + 4:cs + 8], "little"),
                "byte_rate": int.from_bytes(buffer[cs + 8:cs + 12], "little"),
                "block_align": int.from_bytes(buffer[cs + 12:cs + 14], "little"),
                "bits_per_sample": int.from_bytes(buffer[cs + 14:cs + 16], "little"),
            }
        elif chunk_id == "data":
            pcm_data = buffer[cs:ce]
        offset = ce + (chunk_size % 2)
    if fmt is None or pcm_data is None:
        raise ValueError("WAV missing fmt or data chunk")
    return fmt, pcm_data


def _build_wav(fmt, pcm_data):
    buf = bytearray(44 + len(pcm_data))
    buf[0:4] = b"RIFF"
    buf[4:8] = (36 + len(pcm_data)).to_bytes(4, "little")
    buf[8:12] = b"WAVE"
    buf[12:16] = b"fmt "
    buf[16:20] = (16).to_bytes(4, "little")
    buf[20:22] = fmt["audio_format"].to_bytes(2, "little")
    buf[22:24] = fmt["num_channels"].to_bytes(2, "little")
    buf[24:28] = fmt["sample_rate"].to_bytes(4, "little")
    buf[28:32] = fmt["byte_rate"].to_bytes(4, "little")
    buf[32:34] = fmt["block_align"].to_bytes(2, "little")
    buf[34:36] = fmt["bits_per_sample"].to_bytes(2, "little")
    buf[36:40] = b"data"
    buf[40:44] = len(pcm_data).to_bytes(4, "little")
    buf[44:] = pcm_data
    return bytes(buf)


def _merge_wav_segments(buffers):
    if not buffers:
        raise ValueError("No WAV segments to merge")
    if len(buffers) == 1:
        return buffers[0]
    decoded = [_decode_wav(buf) for buf in buffers]
    ref_fmt = decoded[0][0]
    for fmt, _ in decoded[1:]:
        if fmt != ref_fmt:
            raise ValueError("WAV segment format mismatch")
    merged = b"".join(pcm for _, pcm in decoded)
    return _build_wav(ref_fmt, merged)


def _build_model_queue(req_model):
    base = req_model or DASHSCOPE_QWEN3_TTS_MODEL
    queue = [base]
    for m in config.FALLBACK_MODELS:
        if m != base:
            queue.append(m)
    return queue


class _TTSModelFallback(Exception):
    def __init__(self, model):
        super().__init__("Model " + model + " 403 fallback")
        self.model = model


class DashScopeQwen3TTSProvider(TTSServiceInterface):
    """DashScope Qwen3 TTS via multimodal-generation/generation endpoint.

    Supports voice_id, long text splitting (>600 chars), WAV merging.
    403 auto-fallback through FALLBACK_MODELS.
    """

    def __init__(self):
        config.validate()

    def synthesize(self, req, output=None):
        model_queue = _build_model_queue(req.model or DASHSCOPE_QWEN3_TTS_MODEL)
        last_error = ""
        text_segments = _split_text_by_limit(req.text, DASHSCOPE_TTS_MAX_CHARS)

        for idx, model in enumerate(model_queue):
            try:
                wav_buffers = []
                for seg in text_segments:
                    voice_id = getattr(req, "voice_id", None) or "longxiaxiaoxuan"
                    payload = {
                        "model": model,
                        "input": {
                            "text": seg,
                            "voice": voice_id,
                            "language_type": "Chinese",
                        },
                    }
                    res = requests.post(
                        DASHSCOPE_QWEN3_TTS_URL,
                        json=payload,
                        headers={
                            "Authorization": "Bearer " + config.API_KEY,
                            "Content-Type": "application/json",
                        },
                        timeout=120,
                    )

                    if res.status_code == 403:
                        raise _TTSModelFallback(model)
                    if res.status_code != 200:
                        last_error = "Qwen3 TTS failed (" + str(res.status_code) + "): " + res.text[:200]
                        break

                    data = res.json()
                    output_audio = data.get("output", {}).get("audio", {})
                    audio_b64 = output_audio.get("data", "")
                    audio_url = output_audio.get("url", "")

                    if audio_b64:
                        wav_buffers.append(base64.b64decode(audio_b64))
                    elif audio_url:
                        audio_res = requests.get(audio_url, timeout=60)
                        if audio_res.status_code == 200:
                            wav_buffers.append(audio_res.content)
                        else:
                            last_error = "Audio download failed: " + str(audio_res.status_code)
                            break
                    else:
                        last_error = "Qwen3 TTS response missing audio data"
                        break

                if last_error:
                    continue

                merged_audio = _merge_wav_segments(wav_buffers)
                out = output or config.OUTPUT_PATH
                if not out.lower().endswith(".wav"):
                    out = out.rsplit(".", 1)[0] + ".wav"
                self._save_audio(merged_audio, out)

                if idx > 0:
                    print("  [INFO] Qwen3 TTS fallback to", model)
                return TTSSuccessResult(audio_bytes=merged_audio, save_path=out)

            except _TTSModelFallback:
                next_model = model_queue[idx + 1] if idx + 1 < len(model_queue) else None
                if next_model:
                    print("  [!] Qwen3 TTS", model, "403, switching to", next_model)
                    continue
                return TTSFailureResult(error="All models 403: " + ", ".join(model_queue))
            except Exception as e:
                last_error = "Qwen3 TTS error: " + str(e)
                break

        return TTSFailureResult(error=last_error)
