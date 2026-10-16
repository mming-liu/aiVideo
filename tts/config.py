"""TTS 配置 — 从 .env.content（内容参数）+ .env.keys（API Key）读取."""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


from shared.paths import audio_dir as _audio_dir


class TTSCfg:
    """TTS 配置（所有参数均可通过 .env.content 覆盖）."""

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

    # ---- DashScope Qwen3 TTS / 音色设计（端点与模型 ID 均可 env 覆盖） ----
    QWEN3_TTS_MODEL: str = os.getenv("DASHSCOPE_QWEN3_TTS_MODEL", "qwen3-tts-vd-2026-01-26")
    QWEN3_TTS_URL: str = os.getenv(
        "DASHSCOPE_QWEN3_TTS_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    )
    VOICE_DESIGN_URL: str = os.getenv(
        "TTS_VOICE_DESIGN_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization",
    )

    # ---- 2026-10-03 §T1/T2a：身份与候选隔离（全部非敏感；不写死任何 key） ----
    #: 音色映射版本（进 SpeechUnit 身份与 v2 侧车；换映射口径时**递增**，
    #: 使旧音轨的 request_key 自动失配 ⇒ 新 attempt，而不是静默复用）
    MAPPING_VERSION: str = os.getenv("TTS_MAPPING_VERSION", "v1")
    #: 模型修订号（provider 不返回修订号时的显式声明；空 = 未声明）
    MODEL_REVISION: str = os.getenv("TTS_MODEL_REVISION", "")
    #: 候选运行使用的音色注册表路径（不设 = 生产注册表；候选入口必须设它，
    #: 否则 get_registry() 会把实验分配写进生产 `global/voice_registry.json`）
    VOICE_REGISTRY_PATH: str = os.getenv("TTS_VOICE_REGISTRY_PATH", "")
    #: 消费格式（step04 现有消费者合同决定；provider 写什么格式由它自己决定）
    CONSUMER_FORMAT: str = os.getenv("TTS_CONSUMER_FORMAT", "mp3")
    CONSUMER_SAMPLE_RATE: int = int(os.getenv("TTS_CONSUMER_SAMPLE_RATE", "0")) or 0
    #: 时长窗口容差（与 shot_tts._validate_tts_duration 同一口径）
    WINDOW_TOLERANCE_MIN_SEC: float = float(os.getenv("TTS_WINDOW_TOLERANCE_MIN", "0.05"))
    WINDOW_TOLERANCE_RATIO: float = float(os.getenv("TTS_WINDOW_TOLERANCE_RATIO", "0.02"))

    # ---- Qwen3-TTS-12Hz-1.7B-CustomVoice 本地隔离客户端（§T1/§T3） ----
    #: 本地权重目录（**不自动下载**；不存在 ⇒ 工具报 not_run，不当成成功）
    QWEN3_LOCAL_MODEL_DIR: str = os.getenv("QWEN3_LOCAL_MODEL_DIR", "")
    #: 本地服务地址（独立进程/服务；主项目不装 CUDA/PyTorch 栈）
    QWEN3_LOCAL_SERVICE_URL: str = os.getenv("QWEN3_LOCAL_SERVICE_URL",
                                             "http://127.0.0.1:8848")
    #: 真实调用总开关：**默认 0**（零真实调用）；=1 且权重要在且已有运行授权才放行
    QWEN3_LOCAL_ENABLE: str = os.getenv("QWEN3_LOCAL_ENABLE", "0")
    #: 本地客户端请求超时（秒）
    QWEN3_LOCAL_TIMEOUT_SEC: float = float(os.getenv("QWEN3_LOCAL_TIMEOUT_SEC", "600"))

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
