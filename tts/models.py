"""TTS 数据模型 — 供应商无关."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TTSRequest:
    """语音合成请求（与供应商无关）."""

    text: str             # 待转换的文本
    model: str = ""       # 供应商侧的模型名称
    rate: float = 0.92    # 语速系数，范围因提供商而异
    volume: int = 50      # 音量，范围因提供商而异
    format: str = "mp3"   # 输出格式：mp3 / wav / pcm
    sample_rate: int = 16000  # 采样率


@dataclass
class TTSSuccessResult:
    """合成成功."""

    success: bool = True
    audio_bytes: bytes = b""
    save_path: Optional[str] = None


@dataclass
class TTSFailureResult:
    """合成失败."""

    success: bool = False
    error: str = ""


# Union type
TTSServiceResult = TTSSuccessResult | TTSFailureResult
