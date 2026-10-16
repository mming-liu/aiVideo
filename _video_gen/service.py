"""视频生成服务 — 抽象接口 + 工厂模式.

所有供应商实现 VideoGenerationService，业务代码只依赖此接口.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import config
from .models import (
    PreparedVideoRequest,
    STATUS_PREPARED_UNSUPPORTED,
    TaskObservation,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)


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

    # -- 公开任务绑定接口（计划 §V2）：默认"不可解析"，具体 provider 覆写 --
    def get_task_binding(self, task_id: str) -> dict:
        """该 task 的凭据绑定；未实现/不可解析时返回 ``binding_unavailable``.

        控制器**不得**读 provider/adapter 的私有字段猜 key（旧写法恒回退 key-1，
        多 key 通道会用错 key 查不到任务）。
        """
        return {"status": "binding_unavailable", "provider": getattr(self, "name", ""),
                "key_slot": None, "credential_profile_ref": None, "binding_version": 0}

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        """恢复前校验配置引用；返回 False 表示不得用当前 key 猜历史任务."""
        return False

    # ------------------------------------------------------------------
    # V 包增量接口（计划 §V1/§V2）：prepare（**无提交**）+ 冻结事实提交（**一次** POST）
    #
    # 旧 ``create_task`` 保留兼容，但必须委托同一 prepare 逻辑；未实现本接口的
    # provider 返回 ``unsupported``（fail-closed），**不**降级为"直接写正式文件"。
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        """provider 名称（binding/错误报告用；默认取类名小写前缀）。"""
        return type(self).__name__.replace("VideoService", "").lower()

    def prepare_request(self, req: VideoGenerationRequest, context=None) -> PreparedVideoRequest:
        """组装 provider **实际生效**的请求事实——**不发任何生成请求**。

        默认实现：本 provider 未适配 ⇒ ``unsupported``（调用方不得据此提交）。
        """
        return PreparedVideoRequest.unsupported(
            self.name, f"{type(self).__name__} 未实现 prepare_request（prepared 入口不可用）")

    def create_prepared_task(self, prepared: PreparedVideoRequest, context=None) -> str:
        """materialize + 提交**已冻结**事实：每次调用**至多一个**生成 POST。

        不支持的 provider 一律拒绝（不静默回落到旧 create_task 的自动重试）。
        """
        raise VideoCapabilityError(
            "unsupported",
            f"{type(self).__name__} 未实现 create_prepared_task，拒绝提交（fail-closed）")

    def query_observation(self, task_id: str) -> TaskObservation:
        """规范化任务状态与结果引用（默认 unknown —— 不把日志当返回协议）。"""
        try:
            raw = self.query_task(task_id)
        except Exception as exc:  # noqa: BLE001 - 观察失败如实标注 unknown
            return TaskObservation(task_id=str(task_id), error=str(exc)[:300])
        return TaskObservation.from_raw(task_id, raw)

    def supports_prepared(self) -> bool:
        """本 provider 是否可用 prepared 入口（candidate 模式 fail-closed 判据）。"""
        return type(self).prepare_request is not VideoGenerationService.prepare_request


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[VideoGenerationService]] = {}


def classify_post_response(resp, *, provider: str = "") -> str:
    """把**一次**生成 POST 的响应分类；成功返回 task_id，失败抛 ``VideoSubmissionError``。

    分类（§V2 故障表；调用方据此记状态，**不**按异常字符串猜"是否已建任务"）：

    - 200 且带 id        ⇒ 返回 task_id（``submitted``）；
    - 200 无 id          ⇒ ``provider_failed``（响应违约，没有任务）；
    - 429                ⇒ ``rate_limited``（被限流拒绝，无任务；交上层记录待恢复）；
    - 5xx                ⇒ ``submission_unknown``（服务端可能已建任务）；
    - 其它 4xx（400/403/404/422 …）⇒ ``provider_failed``（明确拒绝，无任务）。
    """
    from .models import (
        SUBMIT_RATE_LIMITED,
        SUBMIT_REJECTED,
        SUBMIT_UNKNOWN,
        VideoSubmissionError,
    )
    import re as _re

    status = int(getattr(resp, "status_code", 0) or 0)
    body = ""
    try:
        body = str(getattr(resp, "text", "") or "")[:500]
    except Exception:  # noqa: BLE001 - 取不到正文不影响分类
        body = ""
    if status == 200:
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise VideoSubmissionError(SUBMIT_REJECTED,
                                       f"{provider} 响应 JSON 解析失败: {exc} ({body[:200]})",
                                       http_status=status) from exc
        task_id = (data or {}).get("id") or (data or {}).get("task_id")
        if not task_id:
            raise VideoSubmissionError(SUBMIT_REJECTED,
                                       f"{provider} 未返回 task_id: {body[:300]}",
                                       http_status=status)
        return str(task_id)
    if status == 429:
        wait = None
        m = _re.search(r"retry[-_]after[\"':=\s]+(\d+(?:\.\d+)?)", body, _re.IGNORECASE)
        if m:
            wait = float(m.group(1))
        raise VideoSubmissionError(SUBMIT_RATE_LIMITED,
                                   f"{provider} 429 限流（未建任务，交上层待恢复）: {body[:300]}",
                                   http_status=status, retry_after=wait)
    if 500 <= status < 600:
        raise VideoSubmissionError(SUBMIT_UNKNOWN,
                                   f"{provider} 服务端 {status}（不知是否已建任务，禁止自行重发）: {body[:300]}",
                                   http_status=status)
    raise VideoSubmissionError(SUBMIT_REJECTED, f"{provider} 拒绝 [{status}]: {body[:300]}",
                               http_status=status)


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

    # Volcengine 火山方舟 (Seedance 视频生成)
    try:
        from . import volcengine_provider as _ve
        register("volcengine", _ve.VolcengineVideoService)
    except ImportError:
        pass

    # MiniMax (Hailuo v1 旧模型 + MiniMax-H3 v2 新模型)
    try:
        from . import minimax_provider as _mm
        register("minimax", _mm.MiniMaxVideoService)
    except ImportError:
        pass

    # Agent Plan (/api/plan/v3) — 2026-08-30 新增（专属 key 通道）
    try:
        from . import agent_plan_provider as _ap
        register("agent_plan", _ap.AgentPlanVideoService)
    except ImportError:
        pass

    # OpenAI Sora / 第三方兼容 (待后续扩展)
    # try:
    #     from . import openai_provider as _oa
    #     register("openai", _oa.OpenAIVideoService)
    #     register("openai_compat", _oa.OpenAIVideoService)
    # except ImportError:
    #     pass


def resolve_download_target(req, production_path):
    """本次结果应落盘的绝对路径（§V1：候选下载**不触及**正式路径）。

    - 请求带候选路径 ⇒ 必须同时带可核验的执行上下文根；守卫通过才返回
      （绝对、在实验根内、非正式产物、无 .. 逃逸）；
    - 没有候选路径 ⇒ 生产路径（旧行为不变），**不**在此处做正式/候选判定以外的改写。
    """
    from .models import VideoCapabilityError, candidate_path_for_request, execution_context_from_request

    cand = candidate_path_for_request(req)
    if not cand:
        return Path(production_path)
    ctx = execution_context_from_request(req)
    if ctx is None or not ctx.experiment_root:
        raise VideoCapabilityError(
            "candidate_root_missing",
            f"请求带候选路径 {cand} 但缺少可核验的执行上下文根，拒绝写入（不降级到正式路径）")
    return ctx.assert_candidate(cand)


def http_download_video(video_url: str, target, *, clip_index: int = 0,
                        provider: str = "", timeout: int = 180,
                        min_bytes: int = 20 * 1024):
    """下载远端结果到 ``target``（临时文件 → 大小校验 → 搬移）；失败走结构化结果。

    两个 Seedance provider 共用这一份实现，避免"下载到哪条路径"的判据分叉。
    """
    import shutil
    import tempfile

    from .models import VideoGenerationResult

    save_path = Path(target)
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name
        import requests
        resp = requests.get(video_url, timeout=timeout)
        resp.raise_for_status()
        video_bin = resp.content
        if len(video_bin) < min_bytes:
            raise RuntimeError(f"下载内容过小 ({len(video_bin)} bytes)，可能是错误页面")
        with open(tmp_path, "wb") as f:
            f.write(video_bin)
        shutil.copy2(tmp_path, str(save_path))
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
        return VideoGenerationResult(clip_index=clip_index, success=True,
                                     save_path=str(save_path))
    except Exception as e:  # noqa: BLE001 - 下载失败按结构化结果返回（调用方决定恢复）
        return VideoGenerationResult(clip_index=clip_index, success=False,
                                     error=f"下载失败: {e}")


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