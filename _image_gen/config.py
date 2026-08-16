"""图片生成配置 — 从 .env.content（内容参数）+ .env.keys（API Key）读取参数.

供应商可切换，所有参数均可通过 .env.content 覆盖.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


from shared.paths import images_dir as _images_dir


class ImageGenCfg:
    """图片生成配置（所有参数均可通过 .env.content 覆盖）."""

    # ---- 供应商选择 ----
    # agnes → agnes ai (OpenAI 兼容协议 /v1/images/generations)
    PROVIDER: str = os.getenv("IMAGE_PROVIDER", "agnes")

    # ---- 通用参数 ----
    # 默认输出到当前 book/chapter 的 images/ 目录（统一路径来源: shared/paths.py）
    OUTPUT_DIR: str = os.getenv("IMAGE_OUTPUT_DIR", str(_images_dir()))
    IMAGE_SIZE: str = os.getenv("IMAGE_SIZE", "1024x1792")  # 9:16 竖屏

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
    # #23 修复 2026-08-14：key_scheduler.acquire() 等待上限（秒），防止全部 key
    # 被 429 长冷却时工作线程无限阻塞
    AGNES_IMAGE_KEY_ACQUIRE_TIMEOUT: float = float(
        os.getenv("AGNES_IMAGE_KEY_ACQUIRE_TIMEOUT", "180"))

    @classmethod
    def validate(cls) -> None:  # type: ignore[override]
        """验证当前供应商所需配置是否完整."""
        if cls.PROVIDER == "volcengine":
            import os as _os
            key = _os.getenv("VOLCENGINE_API_KEY") or _os.getenv("ARK_API_KEY", "")
            if not key:
                raise RuntimeError("IMAGE_PROVIDER=volcengine 需要 VOLCENGINE_API_KEY（或 ARK_API_KEY）")
        elif cls.PROVIDER == "minimax":
            import os as _os
            key = _os.getenv("MINIMAX_API_KEY")
            if not key:
                raise RuntimeError("IMAGE_PROVIDER=minimax 需要 MINIMAX_API_KEY")
        elif cls.PROVIDER == "agnes":
            # 动态检测是否配置了任何 AGNES_API_KEY
            has_key = bool(os.environ.get("AGNES_API_KEY"))
            for i in range(2, 21):
                if os.environ.get(f"AGNES_API_KEY_{i}"):
                    has_key = True
                    break
            if not has_key:
                raise RuntimeError("IMAGE_PROVIDER=agnes 需要至少配置一个 AGNES_API_KEY")

    @classmethod
    def get_rate_limit_info(cls) -> dict:
        """返回免费用户推荐速率限制（基于 docs/faq.md）."""
        return {
            "image_2k_rpm": 10,      # 免费用户 2K: 10 RPM
            "image_1k_rpm": 20,      # 免费用户 1K: 20 RPM
            "image_3k_rpm": 1,       # 免费用户 3K/4K: 1 RPM
            "image_interval_2k": 6,  # 2K: 6秒间隔
            "image_interval_1k": 3,  # 1K: 3秒间隔
        }


# 单例
config = ImageGenCfg()
