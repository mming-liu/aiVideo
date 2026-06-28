"""tts 包 — 公共 API."""

from .config import TTSCfg as TTSCfg, config as config
from .models import (
    TTSRequest as TTSRequest,
    TTSServiceResult as TTSServiceResult,
)
from .service import (
    TTSServiceInterface as TTSServiceInterface,
    create_provider as create_provider,
)

__all__ = [
    "TTSCfg",
    "config",
    "TTSRequest",
    "TTSServiceResult",
    "TTSServiceInterface",
    "create_provider",
]
