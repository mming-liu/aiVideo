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

    async def generate_async(self, req: TextGenerationRequest) -> TextGenerationResult:
        """Await one local worker without blocking the event loop.

        Uses the same generate/ledger/deadline path. Cancellation stops awaiting,
        not the remote request; callers must not automatically resubmit it.
        """
        import asyncio
        return await asyncio.to_thread(self.generate, req)

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

    # 火山方舟 Agent Plan（doubao-seed-2-1-turbo 默认，2026-09-03 接入）
    try:
        from . import agent_plan_provider as _ap
        register("agent_plan", _ap.AgentPlanTextService)
    except ImportError:
        pass


class _LedgeredService(TextGenerationService):
    """**调用台账代理**：把每次 `generate()` 记一条带 `run_id` 的记录（2026-10-02 复核）.

    为什么放在工厂出口：四家 provider 共用这一个入口，新接一家自动带上；若在每个
    provider 里各写一遍，必然漏一家（与「调用记录全空」是同一类问题）。代理只转发
    `generate()`——全仓只有这一个方法被调用（已 grep 核对），不代理其它属性，
    免得把 provider 私有面变成新契约。
    """

    def __init__(self, inner: TextGenerationService, provider: str):
        self._inner = inner
        self._provider = provider

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        import time as _t
        # `call_id` 在**调用前**生成（复核之七）：这一次调用的唯一标识——完整正文按它
        # 归档、修订轮按它关联"这一轮用的是哪份响应"，同一轮多次/并发调用各自定位。
        from shared.call_ledger import new_call_id
        _cid = new_call_id()
        _t0 = _t.monotonic()
        _res = None
        try:
            _res = self._inner.generate(req)
            return _res
        except BaseException as _exc:
            # provider 抛异常时，**调用身份不能丢**（复核之十一 P2）：异常路径上的调用方
            # （规划修订的三个通道）也要能报出"这一次调用是哪一个"——台账里那一行就是按
            # 它落的。挂在异常对象上，不改变抛出物本身。
            try:
                _exc.ai_video_call_id = _cid
            except Exception:
                pass
            raise
        finally:
            try:                       # 台账**绝不影响生成**（写失败只丢记录）
                from shared.call_ledger import record_call
                if _res is None:       # provider 抛异常也算一次失败调用
                    _res = TextGenerationResult(success=False, error="exception")
                # 调用方（修订轮/提取器）从结果上取它来关联证据；幂等赋值，不改语义
                try:
                    _res.call_id = _cid
                except Exception:
                    pass
                # `model` 记**实际生效**的那一个：调用点常常不传（提取类走 provider
                # 默认模型），只记 `req.model` 会留下空字段，事后照样查不出用的哪个模型。
                record_call(self._provider, req, _res,
                            elapsed_ms=(_t.monotonic() - _t0) * 1000.0,
                            model_default=str(getattr(self._inner, "_model", "") or ""),
                            call_id=_cid)
            except Exception:
                pass


def create_service(provider: Optional[str] = None) -> TextGenerationService:
    """根据 provider 名称创建服务实例（外面包一层**调用台账代理**）.

    Args:
        provider: 供应商名称. None 则使用 config.PROVIDER.

    Returns:
        TextGenerationService 实例；未设台账路径时记录退化成 no-op.
    """
    _auto_register()
    provider = provider or config.PROVIDER
    if provider not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"未知文本供应商 '{provider}'，可用: {available}\n"
            f"在 .env.content 中设置 TEXT_PROVIDER 切换供应商."
        )
    return _LedgeredService(_REGISTRY[provider](), provider)
