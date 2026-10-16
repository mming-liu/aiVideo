"""TTS 服务 — 抽象接口 + 工厂模式.

所有供应商实现 TTSServiceInterface，业务代码只依赖此接口.

2026-10-03 计划 §T1：「能力由 provider 注册并核官方依据；不支持 instruction 或参数时
明确拒绝，兼容旧 provider 按原请求含义适配，不假装参数生效」。

能力表就在这里**集中登记**（provider 类也可以自己实现 `capabilities()` 覆盖它）：
未登记 = fail-closed（按 :data:`tts.models.UNKNOWN_CAPABILITIES` 处理，不声明支持
任何可选参数）。`tts/dashscope_provider.py` / `openai_provider.py` 不在 T 包允许修改
的文件内，它们的官方上限（Sambert 300 字/次、OpenAI 4096 字/次）因此记在下面这张表里，
而不是散进调用点。
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import config
from .models import (UNKNOWN_CAPABILITIES, ProviderCapabilities, TTSRequest,
                     TTSServiceResult)


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

    def capabilities(self) -> ProviderCapabilities:
        """本 provider 的能力事实（默认查集中登记表；未登记即 fail-closed）."""
        return provider_capabilities(self)

    def _save_audio(self, audio_bytes: bytes, output: str) -> str:
        """保存音频到本地."""
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio_bytes)
        return str(out_path)


# ---------------------------------------------------------------------------
# 能力登记表（官方依据写在 evidence 里；不把上限复制进 .env 当配置）
# ---------------------------------------------------------------------------

#: Sambert / DashScope 通用 TTS：请求级参数齐全，但没有独立 instruction 与 speaker
#: （Sambert 用 `model` 同时当模型 ID 与音色 ID——所以这里 supports_speaker=False：
#: 把 sambert 的模型名当 speaker 传给别的 provider 正是 §T1 点名的串号形态）。
_SAMBERT_LIKE = dict(
    max_chars_per_call=300,
    supports_instruction=False,
    supports_rate=True,
    supports_volume=True,
    supports_speaker=False,
    output_formats=("mp3", "wav", "pcm"),
    sample_rates=(8000, 16000, 24000, 48000),
    evidence="DashScope 语音合成（Sambert）官方限制：单次合成 ≤300 字符；"
             "rate/volume/format/sample_rate 为请求参数（tts/dashscope_provider.py 的 payload）",
)

#: OpenAI /v1/audio/speech 兼容端点：有 speed(=rate)，无 volume，无 instruction，
#: voice 由 config 固定（请求里的 speaker 并不会改变发声声线 ⇒ 如实声明不支持）。
_OPENAI_LIKE = dict(
    max_chars_per_call=4096,
    supports_instruction=False,
    supports_rate=True,
    supports_volume=False,
    supports_speaker=False,
    output_formats=("mp3", "opus", "aac", "flac", "wav", "pcm"),
    sample_rates=(),
    evidence="OpenAI /v1/audio/speech：input ≤4096 字符；speed 为请求参数；"
             "无 volume 参数；voice 取 tts/config.py 的 OPENAI_VOICE（请求声线不下发）",
)

_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "dashscope_sdk": ProviderCapabilities(provider="dashscope_sdk", **_SAMBERT_LIKE),
    "dashscope_http": ProviderCapabilities(provider="dashscope_http", **_SAMBERT_LIKE),
    "openai": ProviderCapabilities(provider="openai", **_OPENAI_LIKE),
    "openai_compat": ProviderCapabilities(provider="openai_compat", **_OPENAI_LIKE),
}


def register_capabilities(provider: str, caps: ProviderCapabilities) -> None:
    """登记/覆盖某 provider 的能力事实（provider 名与 create_provider 同名）."""
    _CAPABILITIES[str(provider)] = caps


def declared_capabilities(provider: str) -> ProviderCapabilities | None:
    """查集中登记表（未登记 → None）."""
    return _CAPABILITIES.get(str(provider))


def provider_capabilities(provider, *, name: str | None = None) -> ProviderCapabilities:
    """能力读数：**实例自报优先** → 登记表 → fail-closed 的 UNKNOWN_CAPABILITIES.

    `provider` 可以是实例、类或 provider 名。给名字时会先尝试装载 provider 模块
    （`_auto_register`），因此登记在模块里的能力也能按名字查到——规划/`--plan-only`
    不必先实例化 provider（不触发任何网络/密钥校验）。
    """
    if isinstance(provider, str):
        _auto_register()
        declared = _CAPABILITIES.get(provider)
        return declared if declared is not None else UNKNOWN_CAPABILITIES
    own = getattr(provider, "capabilities", None)
    # 基类默认实现就是"查这张表"，直接调它会递归；只认子类自己覆盖的实现
    if callable(own) and not isinstance(provider, type) \
            and getattr(type(provider), "capabilities", None) is not TTSServiceInterface.capabilities:
        try:
            caps = own()
        except Exception:  # 能力声明本身出错不得被当成"支持"
            caps = None
        if isinstance(caps, ProviderCapabilities):
            return caps
    for candidate in (name, getattr(provider, "PROVIDER_NAME", None),
                      type(provider).__name__):
        if candidate and str(candidate) in _CAPABILITIES:
            return _CAPABILITIES[str(candidate)]
    return UNKNOWN_CAPABILITIES


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[TTSServiceInterface]] = {}


def register(provider: str, cls: type[TTSServiceInterface]) -> None:
    """注册供应商实现."""
    _REGISTRY[provider] = cls


def registered_providers() -> list[str]:
    _auto_register()
    return sorted(_REGISTRY)


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
        register_capabilities("dashscope_qwen3", _q3.declared_capabilities())
    except ImportError:
        pass

    # Qwen3-TTS-12Hz-1.7B-CustomVoice 本地隔离客户端（默认零真实调用）
    try:
        from . import qwen3_local as _ql
        register(_ql.PROVIDER_NAME, _ql.Qwen3LocalCustomVoiceProvider)
        register_capabilities(_ql.PROVIDER_NAME, _ql.declared_capabilities())
    except ImportError:
        pass

    # Voice design
    try:
        from . import voice_design as _vd
        register("voice_design", _vd.VoiceDesignService)
    except ImportError:
        pass


def create_provider(provider: Optional[str] = None, **kwargs) -> TTSServiceInterface:
    """根据 provider 名称创建服务实例.

    Args:
        provider: 供应商名称. None 则使用 config.PROVIDER.
        kwargs: 传给 provider 构造函数的附加参数（如本地客户端 endpoint）.

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
    cls = _REGISTRY[provider]
    return cls(**kwargs) if kwargs else cls()
