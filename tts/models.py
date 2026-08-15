"""TTS 数据模型 — 供应商无关。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TTSRequest:
    """语音合成请求（与供应商无关）。"""

    text: str = ""              # 待转换的文本
    model: str = ""             # 供应商晶牌的模型名
    rate: float = 0.92          # 语速系数，范围因供应商而异
    volume: int = 50            # 音量，范围因供应商而异
    format: str = "mp3"         # 输出格式：mp3 / wav / pcm
    sample_rate: int = 16000    # 采样率
    voice_id: str = ""          # 音色 ID（#20 修复 2026-08-14：voice_registry 分配的音色
                                # 借此字段传到 provider；此前 TTSRequest 无此字段，qwen3
                                # provider 的 getattr(req, "voice_id", None) 恒为 None）


@dataclass
class TTSSuccessResult:
    """合成成功。"""

    success: bool = True
    audio_bytes: bytes = b""
    save_path: Optional[str] = None


@dataclass
class TTSFailureResult:
    """合成失败。"""

    success: bool = False
    error: str = ""


# Union type
TTSServiceResult = TTSSuccessResult | TTSFailureResult