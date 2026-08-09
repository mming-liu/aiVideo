"""视频生成服务 — 抽象接口 + 工厂模式.

所有供应商实现 VideoGenerationService，业务代码只依赖此接口.
"""

from abc import ABC, abstractmethod
from typing import Optional

from .config import config
from .models import VideoGenerationRequest, VideoGenerationResult


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class VideoGenerationService(ABC):
    """视频生成服务抽象接口.

    各供应商实现此接口，业务代码只调用 create_task() + wait_for_completion().
    """

    @abstractmethod
    def create_task(self, req: VideoGenerationRequest) -> str:
        """提交任务，返回 task_id."""
        ...

    @abstractmethod
    def query_task(self, task_id: str) -> dict:
        """查询任务状态，返回原始响应 dict."""
        ...

    @abstractmethod
    def wait_for_completion(self, req: VideoGenerationRequest, task_id: str) -> VideoGenerationResult:
        """轮询等待任务完成，下载视频，返回结果."""
        ...


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[VideoGenerationService]] = {}


def register(provider: str, cls: type[VideoGenerationService]) -> None:
    """注册供应商实现."""
    _REGISTRY[provider] = cls


def _auto_register() -> None:
    """延迟导入所有供应商实现."""
    if _REGISTRY:
        return

    # DashScope (通义万相)
    try:
        from . import dashscope_provider as _ds
        register("dashscope", _ds.DashScopeVideoService)
    except ImportError:
        pass  # dashscope 相关依赖未安装

    # DashScope HTTP (KF2V 首尾帧支持, from waoowaoo)
    try:
        from . import http_dashscope_provider as _hdp
        register("dashscope_http", _hdp.DashScopeHttpVideoService)
    except ImportError:
        pass

    # agnes ai (OpenAI 兼容协议, image-to-video + 异步轮询)
    try:
        from . import agnes_provider as _ag
        register("agnes", _ag.AgnesVideoService)
    except ImportError:
        pass

    # Bailian 百炼 (wan2.7 图生视频 + 文生视频)
    try:
        from . import bailian_provider as _bl
        register("bailian", _bl.BailianVideoService)
    except ImportError:
        pass

    # OpenAI Sora / 第三方兼容 (待后续扩展)
    # try:
    #     from . import openai_provider as _oa
    #     register("openai", _oa.OpenAIVideoService)
    #     register("openai_compat", _oa.OpenAIVideoService)
    # except ImportError:
    #     pass


def create_service(provider: Optional[str] = None) -> VideoGenerationService:
    """根据 provider 名称创建服务实例.

    Args:
        provider: 供应商名称. None 则使用 config.PROVIDER.

    Returns:
        VideoGenerationService 实例.
    """
    _auto_register()
    provider = provider or config.PROVIDER
    if provider not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"未知视频供应商 '{provider}'，可用: {available}\n"
            f"在 .env.content 中设置 VIDEO_PROVIDER 切换供应商."
        )
    return _REGISTRY[provider]()