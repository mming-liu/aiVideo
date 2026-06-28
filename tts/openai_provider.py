"""OpenAI 兼容 TTS 供应商实现.

兼容所有遵循 OpenAI /v1/audio/speech 格式的 API:
  - OpenAI 官方: https://api.openai.com/v1/audio/speech
  - SiliconFlow:  https://api.siliconflow.cn/v1/audio/speech
  - 其他第三方 OpenAI 格式代理
"""

from typing import Optional

import requests

from .config import config
from .models import TTSRequest, TTSSuccessResult, TTSFailureResult, TTSServiceResult
from .service import TTSServiceInterface


class OpenAIProvider(TTSServiceInterface):
    """OpenAI / OpenAI-compatible TTS (HTTP REST)."""

    def __init__(self) -> None:
        config.validate()
        self._base_url = config.OPENAI_BASE_URL.rstrip("/").removesuffix("/audio/speech") + "/audio/speech"

    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        # OpenAI TTS 参数: model, input, voice, response_format, speed
        payload = {
            "model": req.model or config.OPENAI_MODEL,
            "input": req.text,
            "voice": config.OPENAI_VOICE,
            "response_format": req.format,
            "speed": req.rate,  # OpenAI speed: 0.25~4.0 (默认 1.0)
        }

        try:
            res = requests.post(
                self._base_url,
                json=payload,
                headers=headers,
                timeout=120,
            )

            if res.status_code != 200:
                return TTSFailureResult(
                    error=f"OpenAI TTS 失败 ({res.status_code}): {res.text[:300]}"
                )

            audio = res.content
            if not audio:
                return TTSFailureResult(error="OpenAI TTS 返回音频为空")

            out = output or config.OUTPUT_PATH
            self._save_audio(audio, out)
            return TTSSuccessResult(audio_bytes=audio, save_path=out)

        except Exception as e:
            return TTSFailureResult(error=f"OpenAI TTS 异常: {e}")
