"""文本生成配置 — 从 .env 读取参数.

供应商可切换，所有参数均可通过 .env 覆盖.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class TextGenCfg:
    """文本生成配置（所有参数均可通过 .env 覆盖）."""

    # ---- 供应商选择 ----
    # dashscope → 阿里云百炼 DashScope (Qwen)
    # deepseek  → DeepSeek (OpenAI 兼容接口)
    # agnes     → agnes ai (OpenAI 兼容协议)
    PROVIDER: str = os.getenv("TEXT_PROVIDER", "dashscope")

    # ---- DashScope (百炼) ----
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    DASHSCOPE_BASE_URL: str = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
    )
    DASHSCOPE_TEXT_MODEL: str = os.getenv("DASHSCOPE_TEXT_MODEL", "qwen-plus")

    # ---- DeepSeek ----
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # ---- agnes (OpenAI 兼容) ----
    AGNES_API_KEY: str = os.getenv("AGNES_API_KEY", "")
    AGNES_BASE_URL: str = os.getenv("AGNES_BASE_URL", "")
    AGNES_TEXT_MODEL: str = os.getenv("AGNES_TEXT_MODEL", "agnes-text-1")
    # 403 回退模型列表（逗号分隔）
    AGNES_TEXT_FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("AGNES_TEXT_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    AGNES_TEXT_ENDPOINT: str = os.getenv("AGNES_TEXT_ENDPOINT", "/v1/chat/completions")

    @classmethod
    def validate(cls) -> None:
        """验证当前供应商所需配置是否完整."""
        if cls.PROVIDER == "dashscope" and not cls.DASHSCOPE_API_KEY:
            raise RuntimeError("TEXT_PROVIDER=dashscope 需要 DASHSCOPE_API_KEY")
        if cls.PROVIDER == "deepseek" and not cls.DEEPSEEK_API_KEY:
            raise RuntimeError("TEXT_PROVIDER=deepseek 需要 DEEPSEEK_API_KEY")
        if cls.PROVIDER == "agnes" and not cls.AGNES_API_KEY:
            raise RuntimeError("TEXT_PROVIDER=agnes 需要 AGNES_API_KEY")


# 单例
config = TextGenCfg()
