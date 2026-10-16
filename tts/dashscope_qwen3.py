"""tts.dashscope_qwen3 - DashScope Qwen3 TTS 适配（2026-10-03 计划 §T1/§T2a）.

本适配层在 T 包里承担四件事（逐条对应 §T2a 的执行项）：

1. **拆 model / speaker**：`req.model` 只是模型 ID，`req.effective_speaker()` 才是声线。
   旧实现把 `voice_registry` 分配的 **Sambert 音色 ID 当模型名**发给 Qwen 端点
   （`req.model` = `voice_config["id"]`），并把未知音色**静默回退**成
   `longxiaxiaoxuan`——两件事都在这里明确拒绝。
2. **600 字分段只保留一个负责位置**：provider 不再自己切分/拼接（旧实现按标点切段、
   各段 `strip()` 后拼回，**拼回不等于原文**）。单次上限作为**能力事实**声明
   （`max_chars_per_call`），切分由 `tts.synthesis_job.segment_text` 独占负责；
   直接喂超长正文 ⇒ 显式失败 `text_over_provider_limit`。
3. **rate/volume/instruction 返回能力事实**：本端点的请求体只有 `text/voice/
   language_type`——rate/volume 不做假装生效（结果里 `applied` 记 False）；
   `instruction` 会被明确拒绝（不拼进发声文本）。
4. **实际 WAV save_path 接结果**：端点返回 WAV，落到真实 `.wav` 路径并把它写进
   `save_path`，由 `synthesis_job` 读**这个路径**（不 stat 假定的原后缀）后再转消费格式。
"""
import base64
import sys
from pathlib import Path
from typing import Optional

import requests

from .config import config
from .models import (ProviderCapabilities, TTSFailureResult, TTSRequest,
                     TTSSuccessResult, TTSServiceResult)
from .service import TTSServiceInterface

# 端点/模型 ID 单源 tts/config.py（可 env 覆盖 DASHSCOPE_QWEN3_TTS_MODEL / _URL）
DASHSCOPE_QWEN3_TTS_URL = config.QWEN3_TTS_URL
DASHSCOPE_QWEN3_TTS_MODEL = config.QWEN3_TTS_MODEL
#: 单次请求正文字符上限（**能力事实**：切分由 synthesis_job 独占负责，这里只拒收超限）
DASHSCOPE_TTS_MAX_CHARS = 600

#: 合法声线白名单（单源；新旧调用点都从这里取，不再各处复制一份）
KNOWN_VOICE_IDS = ["longxiaxiaoxuan", "longwan", "longchen", "longyue", "longyuan"]

#: 语言代码 → 端点语言名（只做映射，不猜语种）
_LANGUAGE_NAMES = {"zh": "Chinese", "zh-cn": "Chinese", "cn": "Chinese",
                   "en": "English", "en-us": "English"}

PROVIDER_NAME = "dashscope_qwen3"


def _ensure_utf8_stdout() -> None:
    """#20 修复 2026-08-14：原模块导入即 `sys.stdout.reconfigure`，是进程级副作用
    （影响同进程其它模块/重定向管道）。改为惰性 + 幂等 + 失败静默：
    仅当 stdout 可重配且当前编码非 utf-8 时才执行。"""
    try:
        if hasattr(sys.stdout, "reconfigure") and (sys.stdout.encoding or "").lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _build_model_queue(req_model):
    base = req_model or DASHSCOPE_QWEN3_TTS_MODEL
    queue = [base]
    for m in config.FALLBACK_MODELS:
        if m != base:
            queue.append(m)
    return queue


def _looks_like_foreign_voice_id(model: str) -> bool:
    """是否是**别的 provider 的音色 ID 被当成了模型名**（§T1 点名的串号形态）.

    Sambert 系音色 ID 一律以 `sambert-` 开头（`tts/voice_registry.AVAILABLE_VOICES`）；
    把这种 ID 当 Qwen 模型名发出去，端点要么 404、要么被当成别的模型——与其等到
    远端报错，不如在本地按命名事实明确拒绝。
    """
    return str(model or "").strip().lower().startswith("sambert-")


class _TTSModelFallback(Exception):
    def __init__(self, model):
        super().__init__("Model " + model + " 403 fallback")
        self.model = model


def declared_capabilities() -> ProviderCapabilities:
    """本 provider 的能力事实（**静态声明**：不实例化、不读密钥，供规划与 fail-closed 登记用）."""
    return ProviderCapabilities(
        provider=PROVIDER_NAME,
        max_chars_per_call=DASHSCOPE_TTS_MAX_CHARS,
        supports_instruction=False,
        supports_rate=False,
        supports_volume=False,
        supports_speaker=True,
        legal_speakers=tuple(KNOWN_VOICE_IDS),
        output_formats=("wav",),
        sample_rates=(),
        model_id=DASHSCOPE_QWEN3_TTS_MODEL,
        model_revision=str(getattr(config, "MODEL_REVISION", "") or ""),
        real_model=True,
        evidence="DashScope multimodal-generation TTS：请求体 input={text, voice, "
                 "language_type}——无 rate/volume/instruction 字段（因此声明不支持，"
                 "不假装生效）；单次 600 字符上限取自现有实现常量 DASHSCOPE_TTS_MAX_CHARS；"
                 "合法声线见 KNOWN_VOICE_IDS",
    )


class DashScopeQwen3TTSProvider(TTSServiceInterface):
    """DashScope Qwen3 TTS via multimodal-generation/generation endpoint.

    Supports voice_id (= speaker), single-shot text up to ``DASHSCOPE_TTS_MAX_CHARS``,
    403 auto-fallback through FALLBACK_MODELS. 长文本切分**不在这里**做。
    """

    PROVIDER_NAME = PROVIDER_NAME

    def __init__(self):
        config.validate()
        _ensure_utf8_stdout()

    # -- 能力事实（官方依据见 evidence；未声明的能力一律按不支持处理） --
    def capabilities(self) -> ProviderCapabilities:
        return declared_capabilities()

    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        text = str(getattr(req, "text", "") or "")
        instruction = str(getattr(req, "instruction", "") or "").strip()
        if instruction:
            return TTSFailureResult(
                error="qwen3 端点不支持 instruction，且不会把它拼进发声文本——"
                      "请改用支持 instruction 的 provider（如本地 CustomVoice）",
                code="unsupported_parameter", field_path="instruction")
        if len(text) > DASHSCOPE_TTS_MAX_CHARS:
            return TTSFailureResult(
                error="正文 %d 字超过单次上限 %d 字——分段由 synthesis_job 独占负责，"
                      "provider 不自己切分（切了就拼不回原文）"
                      % (len(text), DASHSCOPE_TTS_MAX_CHARS),
                code="text_over_provider_limit", field_path="text")
        if not text.strip():
            return TTSFailureResult(error="空正文不能合成", code="empty_text",
                                    field_path="text")

        model_queue = _build_model_queue(req.model or DASHSCOPE_QWEN3_TTS_MODEL)
        for model in model_queue:
            if _looks_like_foreign_voice_id(model):
                return TTSFailureResult(
                    error="模型名 %r 是 Sambert 音色 ID，不是 Qwen 模型 ID——"
                          "音色请放 speaker，模型用 %r 一类 ID"
                          % (model, DASHSCOPE_QWEN3_TTS_MODEL),
                    code="model_not_supported", field_path="model")

        # 声线：**不做静默回退**。空 ⇒ 拒绝（要求显式 speaker），非法 ⇒ 拒绝并列白名单。
        speaker = str(getattr(req, "effective_speaker", lambda: "")() or "").strip()
        if not speaker:
            return TTSFailureResult(
                error="未给 speaker（qwen3 不做静默默认声线；合法: %s）"
                      % ", ".join(KNOWN_VOICE_IDS),
                code="speaker_not_legal", field_path="speaker")
        if speaker not in KNOWN_VOICE_IDS:
            return TTSFailureResult(
                error="非法 speaker %r（合法: %s）——拒绝静默回退默认音色"
                      % (speaker, ", ".join(KNOWN_VOICE_IDS)),
                code="speaker_not_legal", field_path="speaker")

        language = str(getattr(req, "language", "") or "zh").strip().lower()
        language_name = _LANGUAGE_NAMES.get(language, str(getattr(req, "language", "") or "Chinese"))
        # rate/volume：能力事实（不支持）。不写进 payload，并在结果里如实标注。
        applied = {"rate": False, "volume": False, "instruction": False,
                   "requested_rate": getattr(req, "rate", None),
                   "requested_volume": getattr(req, "volume", None)}
        last_error = ""
        for idx, model in enumerate(model_queue):
            try:
                payload = {
                    "model": model,
                    "input": {"text": text, "voice": speaker,
                              "language_type": language_name},
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
                    return TTSFailureResult(
                        error="Qwen3 TTS failed (%s): %s" % (res.status_code, res.text[:500]),
                        code="http_error", field_path="provider")

                data = res.json()
                output_audio = (data.get("output") or {}).get("audio") or {}
                audio_b64 = output_audio.get("data", "")
                audio_url = output_audio.get("url", "")
                if audio_b64:
                    audio_bytes = base64.b64decode(audio_b64)
                elif audio_url:
                    audio_res = requests.get(audio_url, timeout=60)
                    if audio_res.status_code != 200:
                        return TTSFailureResult(
                            error="Audio download failed: %s" % audio_res.status_code,
                            code="download_failed", field_path="provider")
                    audio_bytes = audio_res.content
                else:
                    return TTSFailureResult(error="Qwen3 TTS response missing audio data",
                                            code="empty_audio", field_path="provider")
                if not audio_bytes:
                    return TTSFailureResult(error="模型 %s 返回音频为空" % model,
                                            code="empty_audio", field_path="provider")

                # 实际写出路径：端点是 WAV ⇒ 后缀必须与**实际格式**一致，
                # save_path 就是这个真实路径（消费侧读它，不 stat 假定后缀）。
                out = _actual_wav_path(output)
                self._save_audio(audio_bytes, out)
                if idx > 0:
                    print("  [INFO] Qwen3 TTS fallback to", model)
                return TTSSuccessResult(
                    audio_bytes=audio_bytes, save_path=out, actual_format="wav",
                    actual_sample_rate=int(output_audio.get("sample_rate") or 0),
                    applied=applied)

            except _TTSModelFallback:
                next_model = model_queue[idx + 1] if idx + 1 < len(model_queue) else None
                if next_model:
                    print("  [!] Qwen3 TTS", model, "403, switching to", next_model)
                    continue
                return TTSFailureResult(
                    error="All models 403: " + ", ".join(model_queue),
                    code="quota_exhausted", field_path="model")
            except requests.RequestException as exc:
                last_error = "Qwen3 TTS 网络异常: %s" % exc
                break
            except Exception as exc:
                last_error = "Qwen3 TTS error: %s" % exc
                break

        return TTSFailureResult(error=last_error, code="provider_failed")


def _actual_wav_path(output) -> str:
    """结果 WAV 的**实际**落盘路径（无 output ⇒ 配置默认，后缀强制 .wav）."""
    out = Path(output or config.OUTPUT_PATH)
    return str(out.with_suffix(".wav"))
