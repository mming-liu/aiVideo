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
    # deepseek  → DeepSeek (OpenAI 兼容接口) [默认]
    # agnes     → agnes ai (OpenAI 兼容协议)
    PROVIDER: str = os.getenv("TEXT_PROVIDER", "deepseek")

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
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    # 推理模式 (reasoning_effort): low/medium/high，适用于 deepseek-reasoner 系列
    DEEPSEEK_REASONING_EFFORT: str = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")

    # ---- agnes (OpenAI 兼容) ----
    AGNES_API_KEY: str = os.getenv("AGNES_API_KEY", "")
    AGNES_API_KEY_2: str = os.getenv("AGNES_API_KEY_2", "")  # 备用 Key
    AGNES_API_KEY_3: str = os.getenv("AGNES_API_KEY_3", "")  # 第三个 Key
    AGNES_API_KEY_4: str = os.getenv("AGNES_API_KEY_4", "")  # 第四个 Key
    AGNES_API_KEY_5: str = os.getenv("AGNES_API_KEY_5", "")  # 第五个 Key
    AGNES_API_KEY_6: str = os.getenv("AGNES_API_KEY_6", "")  # 第六个 Key
    AGNES_API_KEY_7: str = os.getenv("AGNES_API_KEY_7", "")  # 第七个 Key
    AGNES_API_KEY_8: str = os.getenv("AGNES_API_KEY_8", "")  # 第八个 Key
    AGNES_BASE_URL: str = os.getenv("AGNES_BASE_URL", "")
    AGNES_TEXT_MODEL: str = os.getenv("AGNES_TEXT_MODEL", "agnes-2.0-flash")
    # 403 回退模型列表（逗号分隔）
    AGNES_TEXT_FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("AGNES_TEXT_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    AGNES_TEXT_ENDPOINT: str = os.getenv("AGNES_TEXT_ENDPOINT", "/v1/chat/completions")
    AGNES_TEXT_ENABLE_THINKING: bool = os.getenv("AGNES_TEXT_ENABLE_THINKING", "1") == "1"
    # 提交重试: 5xx/连接错误时的最大重试次数与间隔（秒）
    AGNES_TEXT_SUBMIT_RETRIES: int = int(os.getenv("AGNES_TEXT_SUBMIT_RETRIES", "3"))
    AGNES_TEXT_SUBMIT_RETRY_DELAY: int = int(os.getenv("AGNES_TEXT_SUBMIT_RETRY_DELAY", "30"))

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
