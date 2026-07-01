"""文本生成服务包 — 供应商无关的公共 API."""

from .config import TextGenCfg as TextGenCfg, config as config
from .models import (
    TextGenerationRequest as TextGenerationRequest,
    TextGenerationResult as TextGenerationResult,
)
from .service import (
    TextGenerationService as TextGenerationService,
    create_service as create_service,
)

__all__ = [
    "TextGenCfg",
    "config",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TextGenerationService",
    "create_service",
]
