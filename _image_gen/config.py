"""图片生成配置 — 从 .env 读取参数.

供应商可切换，所有参数均可通过 .env 覆盖.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class ImageGenCfg:
    """图片生成配置（所有参数均可通过 .env 覆盖）."""

    # ---- 供应商选择 ----
    # agnes → agnes ai (OpenAI 兼容协议 /v1/images/generations)
    PROVIDER: str = os.getenv("IMAGE_PROVIDER", "agnes")

    # ---- 通用参数 ----
    OUTPUT_DIR: str = os.getenv("IMAGE_OUTPUT_DIR", "./output/images")
    IMAGE_SIZE: str = os.getenv("IMAGE_SIZE", "1024x1792")  # 9:16 竖屏

    # ---- agnes (OpenAI 兼容) ----
    AGNES_API_KEY: str = os.getenv("AGNES_API_KEY", "")
    AGNES_BASE_URL: str = os.getenv("AGNES_BASE_URL", "")
    AGNES_IMAGE_MODEL: str = os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")
    # 403 回退模型列表（逗号分隔）
    AGNES_IMAGE_FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("AGNES_IMAGE_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    AGNES_IMAGE_ENDPOINT: str = os.getenv("AGNES_IMAGE_ENDPOINT", "/v1/images/generations")
    # 提交重试: 5xx/连接错误时的最大重试次数与间隔（秒）
    AGNES_IMAGE_SUBMIT_RETRIES: int = int(os.getenv("AGNES_IMAGE_SUBMIT_RETRIES", "3"))
    AGNES_IMAGE_SUBMIT_RETRY_DELAY: int = int(os.getenv("AGNES_IMAGE_SUBMIT_RETRY_DELAY", "30"))

    @classmethod
    def validate(cls) -> None:
        """验证当前供应商所需配置是否完整."""
        if cls.PROVIDER == "agnes" and not cls.AGNES_API_KEY:
            raise RuntimeError("IMAGE_PROVIDER=agnes 需要 AGNES_API_KEY")


# 单例
config = ImageGenCfg()
