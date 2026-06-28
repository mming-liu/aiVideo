"""视频生成服务包 — 供应商无关的公共 API."""

from .config import VideoGenCfg as VideoGenCfg, config as config
from .models import (
    VideoGenerationRequest as VideoGenerationRequest,
    VideoGenerationResult as VideoGenerationResult,
    BatchGenerationResult as BatchGenerationResult,
)
from .service import (
    VideoGenerationService as VideoGenerationService,
    create_service as create_service,
)

__all__ = [
    "VideoGenCfg",
    "config",
    "VideoGenerationRequest",
    "VideoGenerationResult",
    "BatchGenerationResult",
    "VideoGenerationService",
    "create_service",
]