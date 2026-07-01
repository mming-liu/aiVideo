"""文本生成服务 — 抽象接口 + 工厂模式.

所有供应商实现 TextGenerationService，业务代码只依赖此接口.
"""

from abc import ABC, abstractmethod
from typing import Optional

from .config import config
from .models import TextGenerationRequest, TextGenerationResult


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class TextGenerationService(ABC):
    """文本生成服务抽象接口.

    各供应商实现此接口，业务代码只调用 generate().
    """

    @abstractmethod
    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        """生成文本，返回结果."""
        ...


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[TextGenerationService]] = {}


def register(provider: str, cls: type[TextGenerationService]) -> None:
    """注册供应商实现."""
    _REGISTRY[provider] = cls


def _auto_register() -> None:
    """延迟导入所有供应商实现."""
    if _REGISTRY:
        return

    # DashScope (阿里百炼 Qwen)
    try:
        from . import dashscope_provider as _ds
        register("dashscope", _ds.DashScopeTextService)
    except ImportError:
        pass  # dashscope 相关依赖未安装

    # DeepSeek (OpenAI 兼容接口)
    try:
        from . import deepseek_provider as _dp
        register("deepseek", _dp.DeepSeekTextService)
    except ImportError:
        pass  # openai 库未安装

    # agnes ai (OpenAI 兼容协议)
    try:
        from . import agnes_provider as _ag
        register("agnes", _ag.AgnesTextService)
    except ImportError:
        pass


def create_service(provider: Optional[str] = None) -> TextGenerationService:
    """根据 provider 名称创建服务实例.

    Args:
        provider: 供应商名称. None 则使用 config.PROVIDER.

    Returns:
        TextGenerationService 实例.
    """
    _auto_register()
    provider = provider or config.PROVIDER
    if provider not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"未知文本供应商 '{provider}'，可用: {available}\n"
            f"在 .env 中设置 TEXT_PROVIDER 切换供应商."
        )
    return _REGISTRY[provider]()
