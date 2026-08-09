"""TTS 服务 — 抽象接口 + 工厂模式.

所有供应商实现 TTSServiceInterface，业务代码只依赖此接口.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import config
from .models import TTSRequest, TTSServiceResult


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------

class TTSServiceInterface(ABC):
    """TTS 语音合成抽象接口.

    各供应商实现此接口，业务代码只调用 synthesize().
    """

    @abstractmethod
    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        """合成语音.

        Args:
            req: 合成请求参数.
            output: 输出文件路径（可选，默认使用配置值）.

        Returns:
            TTSSuccessResult 或 TTSFailureResult.
        """
        ...

    def _save_audio(self, audio_bytes: bytes, output: str) -> str:
        """保存音频到本地."""
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio_bytes)
        return str(out_path)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[TTSServiceInterface]] = {}


def register(provider: str, cls: type[TTSServiceInterface]) -> None:
    """注册供应商实现."""
    _REGISTRY[provider] = cls




def _auto_register() -> None:
    """延迟导入所有供应商实现."""
    if _REGISTRY:
        return
    # DashScope SDK (Python library)
    try:
        from . import dashscope_provider as _ds
        register("dashscope_sdk", _ds.DashScopeSDKProvider)
    except ImportError:
        pass  # dashscope SDK 未安装

    # DashScope HTTP REST
    try:
        from . import dashscope_provider as _ds2
        register("dashscope_http", _ds2.DashScopeHttpProvider)
    except ImportError:
        pass

    # OpenAI 官方 / 第三方兼容 API
    try:
        from . import openai_provider as _oa
        register("openai", _oa.OpenAIProvider)
        register("openai_compat", _oa.OpenAIProvider)
    except ImportError:
        pass

    # Qwen3 TTS (from waoowaoo)
    try:
        from . import dashscope_qwen3 as _q3
        register("dashscope_qwen3", _q3.DashScopeQwen3TTSProvider)
    except ImportError:
        pass

    # Voice design
    try:
        from . import voice_design as _vd
        register("voice_design", _vd.VoiceDesignService)
    except ImportError:
        pass


def create_provider(provider: Optional[str] = None) -> TTSServiceInterface:
    """根据 provider 名称创建服务实例.

    Args:
        provider: 供应商名称. None 则使用 config.PROVIDER.

    Returns:
        TTSServiceInterface 实例.
    """
    _auto_register()
    provider = provider or config.PROVIDER
    if provider not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"未知 TTS 供应商 '{provider}'，可用: {available}\n"
            f"在 .env.content 中设置 TTS_PROVIDER 切换供应商."
        )
    return _REGISTRY[provider]()
