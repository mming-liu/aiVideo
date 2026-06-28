"""视频生成配置 — 从 .env 读取参数.

供应商可切换，所有参数均可通过 .env 覆盖.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class VideoGenCfg:
    """视频生成配置（所有参数均可通过 .env 覆盖）."""

    # ---- 供应商选择 ----
    # dashscope  → 阿里云百炼 DashScope (通义万相 wan2.7-t2v)
    # 后续可扩展: openai (Sora), runway, kling, pika, luma, minimax
    PROVIDER: str = os.getenv("VIDEO_PROVIDER", "dashscope")

    # ---- 通用参数 ----
    OUTPUT_DIR: str = os.getenv("VIDEO_OUTPUT_DIR", "./output/clips")
    VIDEO_SIZE: str = os.getenv("VIDEO_SIZE", "960x1792")       # 9:16 竖屏
    VIDEO_DURATION: int = int(os.getenv("VIDEO_DURATION", "6"))  # 默认单镜头时长(秒)

    # ---- DashScope (通义万相) ----
    # 官方文档: https://help.aliyun.com/zh/model-studio/text-to-video
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    DASHSCOPE_BASE_URL: str = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2video/generation",
    )
    DASHSCOPE_VIDEO_MODEL: str = os.getenv("DASHSCOPE_VIDEO_MODEL", "wan2.7-t2v")
    # 403 回退模型列表（逗号分隔）
    DASHSCOPE_VIDEO_FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("DASHSCOPE_VIDEO_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    DASHSCOPE_VIDEO_DURATION: int = int(os.getenv("DASHSCOPE_VIDEO_DURATION", "0"))  # 0=不传duration
    DASHSCOPE_POLL_INTERVAL: int = int(os.getenv("DASHSCOPE_POLL_INTERVAL", "8"))
    DASHSCOPE_MAX_POLL_ATTEMPTS: int = int(os.getenv("DASHSCOPE_MAX_POLL_ATTEMPTS", "120"))

    # ---- OpenAI Sora / 第三方兼容 API ----
    # OpenAI 官方: https://api.openai.com/v1/video
    # 第三方兼容: 待后续扩展
    OPENAI_API_KEY: str = os.getenv("VIDEO_OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv(
        "VIDEO_OPENAI_BASE_URL",
        "https://api.openai.com/v1",
    )
    OPENAI_VIDEO_MODEL: str = os.getenv("VIDEO_OPENAI_MODEL", "sora")

    @classmethod
    def validate(cls) -> None:
        """验证当前供应商所需配置是否完整."""
        if cls.PROVIDER == "dashscope" and not cls.DASHSCOPE_API_KEY:
            raise RuntimeError("VIDEO_PROVIDER=dashscope 需要 DASHSCOPE_API_KEY")
        if cls.PROVIDER in ("openai", "openai_compat") and not cls.OPENAI_API_KEY:
            raise RuntimeError(f"VIDEO_PROVIDER={cls.PROVIDER} 需要 VIDEO_OPENAI_API_KEY")


# 单例
config = VideoGenCfg()