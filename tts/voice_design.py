"""语音设计服务 — 从 waoowaoo 的 voice-design.ts 适配

DashScope 语音设计 API，用于创建自定义 TTS 音色。
端点: POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
"""

import base64
from dataclasses import dataclass, field
from typing import Optional

import requests

from .config import config


@dataclass
class VoiceDesignInput:
    """语音设计输入参数。"""
    voice_prompt: str          # 音色描述，如"温柔知性的年轻女性声音，略带书卷气"
    preview_text: str          # 预览文本（5-200 字符）
    preferred_name: str = "custom_voice"  # 自定义音色名称
    language: str = "zh"       # 语言: zh / en


@dataclass
class VoiceDesignResult:
    """语音设计结果。"""
    success: bool = False
    voice_id: Optional[str] = None      # 创建成功的音色 ID
    target_model: Optional[str] = None  # 目标模型（如 qwen3-tts-vd-2026-01-26）
    audio_base64: Optional[str] = None  # 预览音频（base64 WAV）
    sample_rate: int = 0
    usage_count: int = 0
    request_id: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


def validate_voice_prompt(voice_prompt: str) -> tuple[bool, Optional[str]]:
    """验证音色提示词。"""
    if not voice_prompt or not voice_prompt.strip():
        return False, "音色提示词不能为空"
    if len(voice_prompt) > 500:
        return False, "音色提示词不能超过 500 个字符"
    return True, None


def validate_preview_text(preview_text: str) -> tuple[bool, Optional[str]]:
    """验证预览文本。"""
    if not preview_text or not preview_text.strip():
        return False, "预览文本不能为空"
    if len(preview_text) < 5:
        return False, "预览文本至少需要 5 个字符"
    if len(preview_text) > 200:
        return False, "预览文本不能超过 200 个字符"
    return True, None


VOICE_DESIGN_ENDPOINT = config.VOICE_DESIGN_URL  # 端点单源 tts/config.py


def create_voice_design(
    input_data: VoiceDesignInput,
    api_key: Optional[str] = None,
) -> VoiceDesignResult:
    """创建自定义音色设计。

    Args:
        input_data: 音色设计输入
        api_key: DashScope API Key（默认使用配置）

    Returns:
        VoiceDesignResult
    """
    key = api_key or config.API_KEY
    if not key:
        return VoiceDesignResult(success=False, error="请配置 DashScope API Key")

    # 验证输入
    valid, err = validate_voice_prompt(input_data.voice_prompt)
    if not valid:
        return VoiceDesignResult(success=False, error=err)
    valid, err = validate_preview_text(input_data.preview_text)
    if not valid:
        return VoiceDesignResult(success=False, error=err)

    payload = {
        "model": "qwen-voice-design",
        "input": {
            "action": "create",
            "target_model": config.QWEN3_TTS_MODEL,  # 单源 tts/config.py（QWEN3_TTS_MODEL）
            "voice_prompt": input_data.voice_prompt.strip(),
            "preview_text": input_data.preview_text.strip(),
            "preferred_name": input_data.preferred_name,
            "language": input_data.language,
        },
        "parameters": {
            "sample_rate": 24000,
            "response_format": "wav",
        },
    }

    try:
        res = requests.post(
            VOICE_DESIGN_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )

        # #18 修复 2026-08-14：网关可能返回 HTML 错误页，res.json() 抛
        # JSONDecodeError（非 RequestException）→ 之前是未捕获的真实崩溃路径。
        try:
            data = res.json()
        except ValueError:
            return VoiceDesignResult(
                success=False,
                error=(f"语音设计 API 返回非 JSON（HTTP {res.status_code}）: "
                       f"{res.text[:200]}"),
            )

        if res.ok and data.get("output"):
            output = data["output"]
            preview_audio = output.get("preview_audio", {})
            return VoiceDesignResult(
                success=True,
                voice_id=output.get("voice"),
                target_model=output.get("target_model"),
                audio_base64=preview_audio.get("data"),
                sample_rate=preview_audio.get("sample_rate", 0),
                usage_count=data.get("usage", {}).get("count", 0),
                request_id=data.get("request_id"),
            )

        return VoiceDesignResult(
            success=False,
            error=data.get("message", "语音设计 API 调用失败"),
            error_code=data.get("code"),
            request_id=data.get("request_id"),
        )

    except requests.RequestException as e:
        return VoiceDesignResult(success=False, error=f"网络请求失败: {e}")


# ===================================================================
# 语音设计服务（适配 TTSServiceInterface）
# ===================================================================
from .service import TTSServiceInterface
from .models import TTSRequest, TTSServiceResult, TTSFailureResult


class VoiceDesignService(TTSServiceInterface):
    """语音设计服务（非标准 TTS，用于创建自定义音色）。"""

    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        """使用语音设计 API 创建音色并通过预览文本生成试听音频。

        注意：req.text 作为 preview_text，req.model 作为 preferred_name。
        """
        voice_prompt = getattr(req, "voice_id", "") or "默认中性声音"
        result = create_voice_design(
            VoiceDesignInput(
                voice_prompt=voice_prompt,
                preview_text=req.text,
                preferred_name=req.model or "custom_voice",
            )
        )

        if not result.success:
            return TTSFailureResult(error=result.error or "语音设计失败")

        if result.audio_base64:
            audio_bytes = base64.b64decode(result.audio_base64)
            out = output or config.OUTPUT_PATH
            if not out.lower().endswith(".wav"):
                out = out.rsplit(".", 1)[0] + ".wav"
            self._save_audio(audio_bytes, out)
            from .models import TTSSuccessResult
            return TTSSuccessResult(audio_bytes=audio_bytes, save_path=out)

        return TTSFailureResult(error="语音设计成功但无预览音频")
