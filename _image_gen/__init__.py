"""图片生成服务包 — 供应商无关的公共 API."""

from .config import ImageGenCfg as ImageGenCfg, config as config
from .models import (
    ImageGenerationRequest as ImageGenerationRequest,
    ImageGenerationResult as ImageGenerationResult,
)
from .service import (
    ImageGenerationService as ImageGenerationService,
    create_service as create_service,
)

__all__ = [
    "ImageGenCfg",
    "config",
    "ImageGenerationRequest",
    "ImageGenerationResult",
    "ImageGenerationService",
    "create_service",
]
