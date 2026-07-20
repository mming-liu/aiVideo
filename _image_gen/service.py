"""图片生成服务 — 抽象接口 + 工厂模式.

所有供应商实现 ImageGenerationService，业务代码只依赖此接口.
"""

from abc import ABC, abstractmethod
from typing import Optional

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class ImageGenerationService(ABC):
    """图片生成服务抽象接口.

    各供应商实现此接口，业务代码只调用 generate().
    """

    @abstractmethod
    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片，保存到本地，返回结果."""
        ...


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[ImageGenerationService]] = {}


def register(provider: str, cls: type[ImageGenerationService]) -> None:
    """注册供应商实现."""
    _REGISTRY[provider] = cls


def _auto_register() -> None:
    """延迟导入所有供应商实现."""
    if _REGISTRY:
        return

    # agnes ai (OpenAI 兼容协议)
    try:
        from . import agnes_provider as _ag
        register("agnes", _ag.AgnesImageService)
    except ImportError:
        pass

    # Bailian 百炼 (qwen-image 文生图)
    try:
        from . import bailian_provider as _bl
        register("bailian", _bl.BailianImageService)
    except ImportError:
        pass


def create_service(provider: Optional[str] = None) -> ImageGenerationService:
    """根据 provider 名称创建服务实例.

    Args:
        provider: 供应商名称. None 则使用 config.PROVIDER.

    Returns:
        ImageGenerationService 实例.
    """
    _auto_register()
    provider = provider or config.PROVIDER
    if provider not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"未知图片供应商 '{provider}'，可用: {available}\n"
            f"在 .env 中设置 IMAGE_PROVIDER 切换供应商."
        )
    return _REGISTRY[provider]()
