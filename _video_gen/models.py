"""标准数据结构：请求 / 响应"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class VideoGenerationRequest:
    """单次视频生成请求.

    尺寸：推荐 9:16 竖屏用于短视频。
    num_frames 约束（<= 441 且 8n+1）仅 Agnes 视频模型有——常量单源
    shared/model_caps.AGNES_VIDEO_MAX_NUM_FRAMES / AGNES_VIDEO_NUM_FRAMES_MOD。

    多关键帧模式支持:
    - 仅 image_path: 普通图生视频 (1帧)
    - image_path + last_frame_image_path: 首尾双锚定 (2帧)
    - extra_keyframe_paths: 多关键帧叙事 (3-4帧)，实现 A->B->C->D 完整动作序列
    - reference_paths: R2V 参考图/视频（最多5个），用于角色/道具/场景一致性
    """

    prompt: str
    clip_index: int
    duration: int = 6
    negative_prompt: str = ""
    size: str = "720x1280"  # 9:16 竖屏
    # 首帧图本地路径（image-to-video）；None 或文件缺失时降级为纯 text-to-视频
    image_path: Optional[str] = None
    # 尾帧图本地路径（KF2V 首尾帧模式专用）；None 时为普通 i2v
    last_frame_image_path: Optional[str] = None
    # 多关键帧路径列表（extra_body.image 数组），支持 3-4 帧完整动作序列
    # 优先级高于 last_frame_image_path（如果两者同时存在，取 extra_keyframe_paths）
    extra_keyframe_paths: list[str] = None
    # R2V 参考图/视频路径列表（最多5个），用于角色/道具/场景一致性保持
    reference_paths: list[str] = None
    # R2V 参考音频路径，用于音色参考
    reference_audio: Optional[str] = None
    # 可选：用于可复现结果（文档支持）
    seed: Optional[int] = None
    # 推理步数（Agnes API 支持）
    num_inference_steps: Optional[int] = None
    # 运镜指令（中文，如"推近"、"跟拍"、"固定机位"），由 step01 camera_movement 字段传入
    # provider 自行决定如何消费：MiniMax 转方括号指令、Seedance/Bailian 提到 prompt 开头
    camera_movement: str = ""
    # 候选执行上下文（计划 §V1 `ExecutionContext`）：非空 ⇒ 本次下载/产物必须落在
    # 候选路径，**绝不**写正式 shots/、clips/。适配器按原样透传本请求对象，provider
    # 通过 `execution_context_from_request(req)` 读取（不新增适配器私有接口）。
    execution_context: Optional["ExecutionContext"] = None
    # 候选产物落盘路径（由 ExecutionContext.assert_candidate 校验后写入）。
    candidate_path: Optional[str] = None

    def __post_init__(self):
        if self.extra_keyframe_paths is None:
            self.extra_keyframe_paths = []
        if self.reference_paths is None:
            self.reference_paths = []


@dataclass
class VideoGenerationResult:
    """单次视频生成结果."""

    clip_index: int
    success: bool
    save_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BatchGenerationResult:
    """批量生成汇总."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"视频生成完毕：总计 {self.total} | 成功 {self.succeeded} | 失败 {self.failed}",
        ]
        for r in self.results:
            status = "OK" if r.success else f"FAIL: {r.error}"
            path = r.save_path or ""
            lines.append(f"  镜头{r.clip_index:>2}  [{status}]  {path}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


# ===========================================================================
# V 包增量接口（计划 §V1/§V2/§V3）：prepare / prepared submit / 观察 / 执行上下文
#
# 旧接口（create_task / query_task / wait_for_completion）**保留兼容**：它们只是
# 新接口的薄包装（create_task = prepare_request + create_prepared_task），签名不变。
# ===========================================================================

SCHEMA_PREPARED_REQUEST = "prepared_video_request.v1"

STATUS_PREPARED_READY = "ready"
STATUS_PREPARED_UNSUPPORTED = "unsupported"

#: 一次生成 POST 的分类（§V2 故障表）。控制器按分类决定状态与后续动作，
#: **不**按异常字符串猜"是否已建任务"。
SUBMIT_OK = "ok"
SUBMIT_REJECTED = "provider_failed"           # 明确拒绝且没有任务（含 403/400/404/422）
SUBMIT_RATE_LIMITED = "rate_limited"          # 429：被限流拒绝（无任务），交上层记录待恢复
SUBMIT_UNKNOWN = "submission_unknown"         # 网络断开/5xx/超时：不知是否已建任务
SUBMIT_UNSUPPORTED = "unsupported"            # provider 未实现 prepared 入口（fail-closed）


class VideoCapabilityError(RuntimeError):
    """provider 能力不足（计划 §V1：不降级写正式文件，fail-closed）。"""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message,
                "field_path": self.fields.get("field_path"), **self.fields}


class VideoSubmissionError(RuntimeError):
    """**一次**生成 POST 的结果分类（§V2 改动 1/6：绝不在此自行重 POST）。"""

    def __init__(self, classification: str, message: str, *, http_status: int | None = None,
                 retry_after: float | None = None, task_id: str | None = None, **fields):
        super().__init__(message)
        self.classification = classification
        self.http_status = http_status
        self.retry_after = retry_after
        self.task_id = task_id
        self.fields = fields

    @property
    def created_task(self) -> bool:
        return bool(self.task_id)

    def payload(self) -> dict:
        return {"classification": self.classification, "http_status": self.http_status,
                "retry_after": self.retry_after, "task_id": self.task_id, **self.fields}


_DEFAULT_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


@dataclass
class MaterialRef:
    """已冻结的**有序**素材（进入内容 key 的是**字节摘要 + 角色 + 序号**）。

    上传 URL / 临时签名 / 凭据 / 本地输出路径**不进入** key（§V1 第 3 段）。
    """

    role: str
    kind: str                      # image | audio | video
    source_path: Optional[str] = None
    sha256: Optional[str] = None
    bytes: Optional[int] = None
    ref_id: str = ""               # 稳定序号（r1/r2…）

    def key_facts(self) -> dict:
        return {"ref_id": self.ref_id, "role": self.role, "kind": self.kind,
                "sha256": self.sha256}

    def to_dict(self) -> dict:
        return {"ref_id": self.ref_id, "role": self.role, "kind": self.kind,
                "source_path": self.source_path, "sha256": self.sha256,
                "bytes": self.bytes}

    @staticmethod
    def from_dict(d: dict) -> "MaterialRef":
        return MaterialRef(role=d.get("role", ""), kind=d.get("kind", ""),
                           source_path=d.get("source_path"),
                           sha256=d.get("sha256"), bytes=d.get("bytes"),
                           ref_id=d.get("ref_id", ""))


@dataclass
class PreparedVideoRequest:
    """provider **实际生效**的请求事实（§V1）：一次组装，之后不再压缩/选参考。

    - ``prompt`` 是实际会发出去的文本（prepare 内的压缩/合规改写产生派生版本，
      并把每一次改写记进 ``modifications``，供原文守卫与批准范围复核）；
    - ``parameters`` 是实际下发的模型参数（resolution/ratio/duration/audio/seed…）；
    - ``materials`` 是**有序**素材，key 只看字节摘要与角色，不看路径/URL；
    - ``content_key`` 是上述事实的规范摘要；``request_key`` 可绑定 FrozenVideoRequest。
    """

    request_id: str
    provider: str
    model_id: str
    mode: str
    prompt: str
    parameters: dict = field(default_factory=dict)
    materials: list = field(default_factory=list)          # list[MaterialRef]
    modifications: list = field(default_factory=list)      # 派生版本记录
    credential_profile_ref: Optional[str] = None
    key_slot: Optional[int] = None
    binding_version: int = 0
    capability_revision: Optional[str] = None
    request_key: Optional[str] = None
    content_key: str = ""
    status: str = STATUS_PREPARED_READY
    unsupported_reason: Optional[str] = None
    schema_version: str = SCHEMA_PREPARED_REQUEST
    #: 素材的**有序**落盘计划（materialize 时按它编码；含本地路径与拼接组，
    #: 因此**不进入** content_key）。文本项即 ``prompt`` 的实际生效版本。
    content_plan: list = field(default_factory=list)
    #: 请求来源身份（scope/shot_id/shot_index/script_revision）；不进入内容 key。
    source: dict = field(default_factory=dict)

    # -- 事实摘要 --
    def key_facts(self) -> dict:
        return {
            "schema": self.schema_version,
            "provider": self.provider,
            "model_id": self.model_id,
            "mode": self.mode,
            "prompt": self.prompt,
            "parameters": {k: self.parameters.get(k) for k in sorted(self.parameters or {})},
            "materials": [m.key_facts() for m in self.materials],
        }

    def compute_content_key(self) -> str:
        import hashlib
        import json
        blob = json.dumps(self.key_facts(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        self.content_key = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return self.content_key

    def is_ready(self) -> bool:
        return self.status == STATUS_PREPARED_READY

    def material_paths(self) -> list:
        return [m.source_path for m in self.materials if m.source_path]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "status": self.status,
            "unsupported_reason": self.unsupported_reason,
            "request_id": self.request_id, "provider": self.provider,
            "model_id": self.model_id, "mode": self.mode, "prompt": self.prompt,
            "parameters": dict(self.parameters or {}),
            "materials": [m.to_dict() for m in self.materials],
            "modifications": list(self.modifications or []),
            "credential_profile_ref": self.credential_profile_ref,
            "key_slot": self.key_slot, "binding_version": self.binding_version,
            "capability_revision": self.capability_revision,
            "request_key": self.request_key, "content_key": self.content_key,
            "content_plan": list(self.content_plan or []),
            "source": dict(self.source or {}),
        }

    @staticmethod
    def unsupported(provider: str, reason: str, *, request_id: str = "") -> "PreparedVideoRequest":
        return PreparedVideoRequest(
            request_id=request_id or f"req-{provider}-unsupported",
            provider=provider, model_id="", mode="", prompt="",
            status=STATUS_PREPARED_UNSUPPORTED, unsupported_reason=reason)

    @staticmethod
    def from_dict(d: dict) -> "PreparedVideoRequest":
        return PreparedVideoRequest(
            request_id=d.get("request_id", ""), provider=d.get("provider", ""),
            model_id=d.get("model_id", ""), mode=d.get("mode", ""),
            prompt=d.get("prompt", ""), parameters=dict(d.get("parameters") or {}),
            materials=[MaterialRef.from_dict(m) for m in (d.get("materials") or [])],
            modifications=list(d.get("modifications") or []),
            credential_profile_ref=d.get("credential_profile_ref"),
            key_slot=d.get("key_slot"), binding_version=int(d.get("binding_version") or 0),
            capability_revision=d.get("capability_revision"),
            request_key=d.get("request_key"), content_key=d.get("content_key", ""),
            status=d.get("status", STATUS_PREPARED_READY),
            unsupported_reason=d.get("unsupported_reason"),
            schema_version=d.get("schema_version", SCHEMA_PREPARED_REQUEST),
            content_plan=list(d.get("content_plan") or []),
            source=dict(d.get("source") or {}))


def new_request_id(prefix: str = "req") -> str:
    """一次请求的身份（与 request_key/content_key 分离：内容相同≠同一个请求）。"""
    import os
    import time
    return f"{prefix}-{os.getpid()}-{time.time_ns()}"


# ---------------------------------------------------------------- 远端任务观察

OBS_QUEUED = "queued"
OBS_RUNNING = "running"
OBS_SUCCEEDED = "succeeded"
OBS_FAILED = "failed"
OBS_EXPIRED = "expired"
OBS_UNKNOWN = "unknown"

_OBS_SUCCEEDED = ("succeeded", "success", "done", "completed")
_OBS_FAILED = ("failed", "cancelled", "canceled", "error")
_OBS_EXPIRED = ("expired",)
_OBS_RUNNING = ("running", "processing", "in_progress")
_OBS_QUEUED = ("queued", "pending", "created", "submitted", "waiting")


@dataclass
class TaskObservation:
    """**规范化**的远端任务观察（§V1：query 规范化状态与结果引用，不把日志当协议）。"""

    task_id: str
    status: str = OBS_UNKNOWN
    raw_status: str = ""
    result: Optional[dict] = None       # {"video_url": …} / {"candidate_path": …}
    error: Optional[str] = None
    http_status: Optional[int] = None
    usage: Optional[dict] = None

    def is_terminal(self) -> bool:
        return self.status in (OBS_SUCCEEDED, OBS_FAILED, OBS_EXPIRED)

    def is_success(self) -> bool:
        return self.status == OBS_SUCCEEDED

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "status": self.status, "raw_status": self.raw_status,
                "result": self.result, "error": self.error,
                "http_status": self.http_status, "usage": self.usage}

    @staticmethod
    def normalize_status(raw: str | None) -> str:
        s = str(raw or "").strip().lower()
        if s in _OBS_SUCCEEDED:
            return OBS_SUCCEEDED
        if s in _OBS_FAILED:
            return OBS_FAILED
        if s in _OBS_EXPIRED:
            return OBS_EXPIRED
        if s in _OBS_RUNNING:
            return OBS_RUNNING
        if s in _OBS_QUEUED:
            return OBS_QUEUED
        return OBS_UNKNOWN

    @staticmethod
    def from_raw(task_id: str, raw: dict) -> "TaskObservation":
        """把 provider 原始响应规范化（不解析日志文本）。"""
        raw = raw if isinstance(raw, dict) else {}
        status_raw = str(raw.get("status") or "")
        status = TaskObservation.normalize_status(status_raw)
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        result = None
        if status == OBS_SUCCEEDED:
            url = content.get("video_url") or raw.get("video_url")
            if url:
                result = {"video_url": str(url)}
        err = raw.get("error")
        if isinstance(err, dict):
            code = err.get("code") or ""
            msg = err.get("message") or ""
            error = f"{code} - {msg}".strip(" -") or None
        elif err:
            error = str(err)
        else:
            error = None
        return TaskObservation(task_id=str(task_id), status=status, raw_status=status_raw,
                               result=result, error=error,
                               usage=raw.get("usage") if isinstance(raw.get("usage"), dict) else None)


# ---------------------------------------------------------------- 执行上下文

@dataclass
class ExecutionContext:
    """候选执行的路径与身份上下文（§V1；**不混入模型参数**）。

    所有候选产物必须落在 ``candidate_path`` / ``attempt_dir`` 内；任何试图写正式
    路径（clips/、shots/、final、script、日志）的调用会被 ``shared.enhancement_io``
    的守卫拦下。
    """

    experiment_root: Optional[str] = None
    attempt_id: Optional[str] = None
    candidate_path: Optional[str] = None
    scope: dict = field(default_factory=dict)
    package: str = "video"
    chapter: Optional[int] = None

    def to_dict(self) -> dict:
        return {"experiment_root": self.experiment_root, "attempt_id": self.attempt_id,
                "candidate_path": self.candidate_path, "scope": dict(self.scope or {}),
                "package": self.package, "chapter": self.chapter}

    @staticmethod
    def from_candidate_context(ctx, *, candidate_path=None) -> "ExecutionContext":
        """从 ``shared.enhancement_io.CandidateContext`` 适配（鸭子类型，不反向依赖）。"""
        if ctx is None:
            return ExecutionContext(candidate_path=str(candidate_path) if candidate_path else None)
        path = candidate_path if candidate_path is not None else getattr(ctx, "candidate_path", None)
        return ExecutionContext(
            experiment_root=str(getattr(ctx, "attempt_dir", "") or "") or None,
            attempt_id=getattr(ctx, "attempt_id", None),
            candidate_path=str(path) if path else None,
            scope=dict(getattr(ctx, "scope", None) or {}),
            package=str(getattr(ctx, "package", "video") or "video"),
            chapter=getattr(ctx, "chapter", None))

    def assert_candidate(self, path=None):
        """把候选路径交给 ``shared.enhancement_io`` 守卫核验（绝对/同卷/非正式/无逃逸）。"""
        from shared.enhancement_io import assert_candidate_path
        target = Path(path or self.candidate_path or "")
        if not self.experiment_root:
            raise VideoCapabilityError(
                "candidate_root_missing",
                "候选执行上下文缺少 experiment_root，拒绝写入（不降级到正式路径）")
        return assert_candidate_path(target, experiment_root=self.experiment_root,
                                     field_path="candidate_path")


def execution_context_from_request(req) -> Optional[ExecutionContext]:
    """从请求对象读执行上下文（适配器透传请求对象，无需改适配器）。"""
    ctx = getattr(req, "execution_context", None)
    if isinstance(ctx, ExecutionContext):
        return ctx
    if isinstance(ctx, dict):
        return ExecutionContext(**ctx)
    return None


def candidate_path_for_request(req) -> Optional[str]:
    """本次请求的候选落盘路径（无上下文时 None ⇒ provider 走正式路径）。"""
    explicit = getattr(req, "candidate_path", None)
    if explicit:
        return str(explicit)
    ctx = execution_context_from_request(req)
    if ctx is not None and ctx.candidate_path:
        return str(ctx.candidate_path)
    return None
