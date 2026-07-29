"""TTS 配置 — 从 .env 读取参数."""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


from shared.paths import audio_dir as _audio_dir


class TTSCfg:
    """TTS 配置（所有参数均可通过 .env 覆盖）."""

    # ---- 供应商选择 ----
    # dashscope_sdk   → dashscope.audio.tts (WebSocket SDK)
    # dashscope_http  → DashScope HTTP REST 端点
    # openai          → OpenAI 官方 /v1/audio/speech
    # openai_compat   → 任何兼容 OpenAI 格式的第三方 API
    # azure           → Azure Speech Service
    PROVIDER: str = os.getenv("TTS_PROVIDER", "dashscope_sdk")

    # ---- 通用参数 ----
    API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    MODEL: str = os.getenv("DASHSCOPE_TTS_MODEL", "sambert-zhide-v1")
    # 403 回退模型列表（逗号分隔）
    FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("DASHSCOPE_TTS_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    RATE: float = float(os.getenv("DASHSCOPE_TTS_RATE", "0.92"))
    VOLUME: int = int(os.getenv("DASHSCOPE_TTS_VOLUME", "50"))
    FORMAT: str = os.getenv("DASHSCOPE_TTS_FORMAT", "mp3")
    SAMPLE_RATE: int = int(os.getenv("DASHSCOPE_TTS_SAMPLE_RATE", "16000"))
    # 默认输出到当前 book/chapter 的 audio/ 目录（统一路径来源: shared/paths.py）
    OUTPUT_PATH: str = os.getenv("TTS_OUTPUT_PATH", str(_audio_dir() / "narration.mp3"))

    # ---- OpenAI / 第三方 REST API ----
    # OpenAI 官方: https://api.openai.com/v1/audio/speech
    # SiliconFlow:  https://api.siliconflow.cn/v1/audio/speech
    # DashScope 兼容: https://dashscope.aliyuncs.com/api/v1/services/audio/tts/speech-synthesis
    OPENAI_BASE_URL: str = os.getenv(
        "TTS_OPENAI_BASE_URL",
        "https://api.openai.com/v1/audio/speech",
    )
    OPENAI_VOICE: str = os.getenv("TTS_OPENAI_VOICE", "alloy")
    OPENAI_MODEL: str = os.getenv("TTS_OPENAI_MODEL", "tts-1")
    OPENAI_API_KEY: str = os.getenv("TTS_OPENAI_API_KEY", "")

    # ---- Azure ----
    AZURE_KEY: str = os.getenv("TTS_AZURE_KEY", "")
    AZURE_REGION: str = os.getenv("TTS_AZURE_REGION", "eastus")
    AZURE_VOICE: str = os.getenv("TTS_AZURE_VOICE", "zh-CN-YunxiNeural")

    @classmethod
    def validate(cls) -> None:
        if cls.PROVIDER == "dashscope_sdk" and not cls.API_KEY:
            raise RuntimeError("TTS_PROVIDER=dashscope_sdk 需要 DASHSCOPE_API_KEY")
        if cls.PROVIDER in ("openai", "openai_compat") and not cls.OPENAI_API_KEY:
            raise RuntimeError(f"TTS_PROVIDER={cls.PROVIDER} 需要 TTS_OPENAI_API_KEY")
        if cls.PROVIDER == "azure" and not cls.AZURE_KEY:
            raise RuntimeError("TTS_PROVIDER=azure 需要 TTS_AZURE_KEY")


# 单例
config = TTSCfg()
