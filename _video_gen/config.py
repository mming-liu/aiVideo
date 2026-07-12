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
    _OUTPUT_BASE = os.getenv("OUTPUT_BASE", "./output/video")
    _BOOK_IDX = int(os.getenv("BOOK_INDEX", "1"))
    _CHAPTER_IDX = int(os.getenv("NOVEL_CHAPTER", "1"))
    OUTPUT_DIR: str = os.getenv("VIDEO_OUTPUT_DIR", os.path.join(
        _OUTPUT_BASE,
        f"book_{_BOOK_IDX:02d}",
        f"chapter_{_CHAPTER_IDX:02d}",
        "clips"
    ))
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

    # ---- agnes (OpenAI 兼容协议) ----
    # 视频: POST {base}{endpoint}（提交任务） / GET {base}{endpoint}/{task_id}（轮询）
    AGNES_API_KEY: str = os.getenv("AGNES_API_KEY", "")
    AGNES_API_KEY_2: str = os.getenv("AGNES_API_KEY_2", "")  # 备用 Key
    AGNES_API_KEY_3: str = os.getenv("AGNES_API_KEY_3", "")  # 第三个 Key
    AGNES_API_KEY_4: str = os.getenv("AGNES_API_KEY_4", "")  # 第四个 Key
    AGNES_API_KEY_5: str = os.getenv("AGNES_API_KEY_5", "")  # 第五个 Key
    AGNES_API_KEY_6: str = os.getenv("AGNES_API_KEY_6", "")  # 第六个 Key
    AGNES_API_KEY_7: str = os.getenv("AGNES_API_KEY_7", "")  # 第七个 Key
    AGNES_API_KEY_8: str = os.getenv("AGNES_API_KEY_8", "")  # 第八个 Key
    AGNES_BASE_URL: str = os.getenv("AGNES_BASE_URL", "")
    AGNES_VIDEO_MODEL: str = os.getenv("AGNES_VIDEO_MODEL", "agnes-video-v2.0")
    # 403 回退模型列表（逗号分隔）
    AGNES_VIDEO_FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("AGNES_VIDEO_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    AGNES_VIDEO_ENDPOINT: str = os.getenv("AGNES_VIDEO_ENDPOINT", "/v1/videos")
    # 轮询端点（Agnes 视频使用 /agnesapi?video_id=xxx，非标准的 /v1/videos/{id}）
    AGNES_VIDEO_POLL_ENDPOINT: str = os.getenv("AGNES_VIDEO_POLL_ENDPOINT", "/agnesapi")
    # image-to-video: 首帧图在请求体中的字段名
    AGNES_VIDEO_IMAGE_FIELD: str = os.getenv("AGNES_VIDEO_IMAGE_FIELD", "image")
    # 视频尺寸（宽x高）
    AGNES_VIDEO_WIDTH: int = int(os.getenv("AGNES_VIDEO_WIDTH", "832"))
    AGNES_VIDEO_HEIGHT: int = int(os.getenv("AGNES_VIDEO_HEIGHT", "1088"))
    # 帧率与总帧数（12 fps * 5s = 60 帧，24 fps * 5s = 120 帧）
    AGNES_VIDEO_FRAME_RATE: int = int(os.getenv("AGNES_VIDEO_FRAME_RATE", "24"))
    AGNES_VIDEO_NUM_FRAMES: int = int(os.getenv("AGNES_VIDEO_NUM_FRAMES", "0"))  # 0=自适应
    # 视频模式: async（提交任务+轮询，默认）/ sync（同步返回 video_url）
    AGNES_VIDEO_MODE: str = os.getenv("AGNES_VIDEO_MODE", "async")
    AGNES_VIDEO_POLL_INTERVAL: int = int(os.getenv("AGNES_VIDEO_POLL_INTERVAL", "8"))
    AGNES_VIDEO_MAX_POLL_ATTEMPTS: int = int(os.getenv("AGNES_VIDEO_MAX_POLL_ATTEMPTS", "120"))
    # 提交重试: 5xx/429连接错误时无限重试（只要不是必须停止的错误码），直到成功
    # 429限速错误会等待后重试，不会放弃
    AGNES_VIDEO_SUBMIT_RETRIES: int = int(os.getenv("AGNES_VIDEO_SUBMIT_RETRIES", "99999"))
    AGNES_VIDEO_SUBMIT_RETRY_DELAY: int = int(os.getenv("AGNES_VIDEO_SUBMIT_RETRY_DELAY", "30"))

    @classmethod
    def validate(cls) -> None:
        """验证当前供应商所需配置是否完整."""
        if cls.PROVIDER == "dashscope" and not cls.DASHSCOPE_API_KEY:
            raise RuntimeError("VIDEO_PROVIDER=dashscope 需要 DASHSCOPE_API_KEY")
        if cls.PROVIDER in ("openai", "openai_compat") and not cls.OPENAI_API_KEY:
            raise RuntimeError(f"VIDEO_PROVIDER={cls.PROVIDER} 需要 VIDEO_OPENAI_API_KEY")
        if cls.PROVIDER == "agnes" and not cls.AGNES_API_KEY:
            raise RuntimeError("VIDEO_PROVIDER=agnes 需要 AGNES_API_KEY")

    @classmethod
    def get_rate_limit_info(cls) -> dict:
        """返回免费用户推荐速率限制（基于 docs/faq.md）."""
        return {
            "image_2k_rpm": 10,      # 免费用户 2K: 10 RPM
            "image_1k_rpm": 20,      # 免费用户 1K: 20 RPM
            "image_interval": 6,     # 2K: 6秒间隔
            "video_rpm": 1,          # 免费用户视频: 1 RPM
            "video_interval": 60,    # 视频: 60秒间隔
        }


# 单例
config = VideoGenCfg()
