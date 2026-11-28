# -*- coding: utf-8 -*-
"""S07: `step02_video_gen.submit_poll` 的 recovery 职责（2026-11-28 拆分）.

父模块保留提交/轮询主编排与运行状态；本模块在调用点读取父模块当前值，
导入期不回导父模块，保证父模块上的 monkeypatch 仍然生效。
"""
from pathlib import Path
import os
from dataclasses import dataclass, field

from step02_video_gen.parts.qc import (
    _qc_video_product_result, _record_candidate_qc, accept_candidate_video,
)

_PARENT = "step02_video_gen.submit_poll"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


@dataclass
class _ResumeContext:
    """恢复尝试的轻量候选上下文（不重造 CandidateContext，避免路径推导分叉）。"""

    attempt_dir: Path
    package_root: Path
    scope: dict = field(default_factory=dict)
    #: 来源身份随恢复链一起带（§F4）：只带 scope 会让恢复采纳出空 script_revision 的
    #: manifest——下游据此判不了"这是哪一稿"，等于把候选做成不可消费的半成品。
    script_revision: str = ""
    source: str = "resume"

    @property
    def outputs_dir(self) -> Path:
        return self.attempt_dir / "outputs"

    def assert_path(self, path, *, field_path: str = "candidate_path", **kwargs) -> Path:
        from shared.enhancement_io import assert_candidate_path
        return assert_candidate_path(path, experiment_root=self.attempt_dir,
                                     field_path=field_path, **kwargs)

def attempt_task_id(attempt: Path) -> dict:
    """从事件历史读回任务身份（含**晚到 task_id**，见 ``_create_prepared_task_once``）。

    返回 ``{"task_id", "generation_id", "submit_posts", "classification", "state"}``。
    只读事件（权威历史），不信任内存计数。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va

    attempt = Path(attempt)
    classification = None
    posts = 0
    for e in eio.read_events_strict(attempt)[0]:
        if e.get("kind") == "submit_post":
            posts += 1
            classification = e.get("classification") or classification
    # 身份从**全部**记录过它的事件回溯，并沿可验证父子链找——恢复出来的子尝试把
    # task_id 写在 opened/late_task_id/resume_query，只认 submit_post 会误判成
    # submission_unknown 并退出码 7（2026-10-03 复核实测）。
    found = va.task_id_of_attempt(attempt)
    return {"task_id": found["task_id"],
            "generation_id": found["generation_id"],
            "source_event": found["source_event"], "identity_attempt": found["attempt"],
            "submit_posts": posts, "classification": classification,
            "state": va.current_state(attempt)}

def find_recoverable_attempt(
    package_root: Path,
    *,
    shot_index: int,
    request_key: str = "",
    scope: dict | None = None,
    script_revision: str = "",
    provider: str = "",
) -> dict | None:
    """Inspect existing attempt logs/lifecycles for a shot and return recovery or block action.

    Reuses ``shared.enhancement_io`` + ``shared.video_attempts`` as the single state source:
    - Terminal closed generations (``accepted``, ``provider_failed``, ``rejected``, ``draft``,
      ``ready``) return ``None`` so legitimate re-runs proceed with a new attempt.
    - Non-terminal or IO/unknown attempts holding a ``task_id`` (including ``late_task_id``)
      return ``recover_same_task`` to attach a recovery attempt without sending a new POST.
    - ``submitting`` or ``submission_unknown`` without a ``task_id`` return
      ``submission_unknown_no_id`` to fail closed without guessing or resubmitting.
    - ``commit_unconfirmed`` returns ``commit_unconfirmed`` without resubmitting.
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va

    attempts_dir = Path(package_root) / eio.ATTEMPTS_DIRNAME
    if not attempts_dir.is_dir():
        return None

    matched: list[tuple[Path, dict]] = []
    for att in sorted(p for p in attempts_dir.iterdir() if p.is_dir()):
        inputs = eio.read_inputs(att)
        if not isinstance(inputs, dict):
            continue
        raw_idx = inputs.get("shot_index")
        if raw_idx is None:
            continue
        try:
            if int(raw_idx) != int(shot_index):
                continue
        except (TypeError, ValueError):
            continue
        if request_key and str(inputs.get("request_key") or "") != str(request_key):
            continue
        if provider and inputs.get("provider") and str(inputs.get("provider")) != str(provider):
            continue
        if script_revision and inputs.get("script_revision") and str(inputs.get("script_revision")) != str(script_revision):
            continue
        att_scope = inputs.get("scope") if isinstance(inputs.get("scope"), dict) else None
        if not _scope_matches(att_scope, scope):
            continue
        matched.append((att, inputs))

    if not matched:
        return None

    groups: dict[str, list[tuple[Path, dict]]] = {}
    for att, inputs in matched:
        gid = str(inputs.get("generation_id") or f"att:{att.name}")
        groups.setdefault(gid, []).append((att, inputs))

    leaves: list[tuple[Path, dict]] = []
    for nodes in groups.values():
        parents = {str(inp.get("parent_attempt_id") or "") for _, inp in nodes if inp.get("parent_attempt_id")}
        leaf_cands = [(a, inp) for a, inp in nodes if a.name not in parents] or nodes
        leaf_cands.sort(key=lambda item: (str(item[1].get("opened_at") or ""), item[0].name))
        leaves.append(leaf_cands[-1])

    leaves.sort(key=lambda item: (str(item[1].get("opened_at") or ""), item[0].name), reverse=True)
    finished_states = {
        va.STATE_ACCEPTED,
        va.STATE_PROVIDER_FAILED,
        va.STATE_REJECTED,
        va.STATE_DRAFT,
        va.STATE_READY,
    }
    for leaf, leaf_inputs in leaves:
        state = va.current_state(leaf)
        if state in finished_states:
            continue
        info = attempt_task_id(leaf)
        task_id = info.get("task_id")
        gen_id = str(info.get("generation_id") or leaf_inputs.get("generation_id") or "")
        if state == va.STATE_COMMIT_UNCONFIRMED:
            return {
                "action": "commit_unconfirmed",
                "attempt": leaf,
                "state": state,
                "task_id": str(task_id) if task_id else None,
                "generation_id": gen_id,
                "inputs": leaf_inputs,
            }
        if task_id:
            return {
                "action": "recover_same_task",
                "attempt": leaf,
                "state": state,
                "task_id": str(task_id),
                "task_id_source": info.get("source_event"),
                "generation_id": gen_id,
                "inputs": leaf_inputs,
            }
        if state in (
            va.STATE_SUBMITTING,
            va.STATE_SUBMISSION_UNKNOWN,
            va.STATE_SUBMITTED,
            va.STATE_RUNNING,
            va.STATE_DOWNLOADED,
            va.STATE_CHECKED,
            va.STATE_IO_FAILED,
            va.STATE_UNVERIFIED,
        ):
            return {
                "action": "submission_unknown_no_id",
                "attempt": leaf,
                "state": state,
                "task_id": None,
                "generation_id": gen_id,
                "inputs": leaf_inputs,
            }
    return None

def _scope_matches(a: dict | None, b: dict | None) -> bool:
    """Compare two scope dicts strictly across any overlapping non-empty keys."""
    if not a or not b:
        return True
    for key in ("work_id", "book_index", "chapter_scope", "chapter"):
        va_val, vb_val = a.get(key), b.get(key)
        if va_val not in (None, "") and vb_val not in (None, ""):
            if str(va_val) != str(vb_val):
                return False
    return True

def _chapter_scope_str(scope: dict | None) -> str:
    sc = dict(scope or {})
    if sc.get("chapter_scope"):
        return str(sc["chapter_scope"])
    if sc.get("chapter") is not None:
        return f"part_{int(sc.get('book_index') or 1):02d}/chapter_{int(sc['chapter']):03d}"
    return ""

def _work_id_str(scope: dict | None) -> str:
    sc = dict(scope or {})
    return str(sc.get("work_id") or os.getenv("NOVEL_NAME", "") or "")

def _request_from_frozen(frozen: dict, clip_index: int):
    """按冻结事实重建请求对象（恢复时**不得**重新组装 prompt/参考）。"""
    from _video_gen.models import VideoGenerationRequest

    params = dict((frozen or {}).get("parameters") or {})
    first = last = None
    keyframes: list = []
    references: list = []
    for ref in ((frozen or {}).get("references") or []):
        path = ref.get("path")
        if not path:
            continue
        role = ref.get("role")
        if role == "first_frame" and first is None:
            first = path
        elif role == "last_frame" and last is None:
            last = path
        elif role == "keyframe":
            keyframes.append(path)
        elif role == "reference":
            references.append(path)
    return VideoGenerationRequest(
        prompt=str((frozen or {}).get("prompt") or ""), clip_index=int(clip_index),
        duration=int(params.get("duration") or 6),
        negative_prompt=str(params.get("negative_prompt") or ""),
        size=str(params.get("size") or "720x1280"),
        image_path=first, last_frame_image_path=last,
        extra_keyframe_paths=keyframes, reference_paths=references,
        seed=params.get("seed"),
        camera_movement=str(params.get("camera_movement") or ""))

def _emit_video_ledger(
    *,
    scope: dict | None,
    script_revision: str,
    shot_index: int | None,
    provider: str = "",
    model_id: str = "",
    media_policy: str = "",
    request_key: str = "",
    content_key: str = "",
    generation_id: str = "",
    task_id: str = "",
    attempt_id: str = "",
    action_type: str = "generate",
    outcome: str = "",
    error_code: str = "",
    recovered: bool = False,
    submit_posts: int | None = None,
    context=None,
    request_id: str = "",
    detail: str = "",
) -> None:
    try:
        from shared.request_events import emit_video_decision_ledger
        emit_video_decision_ledger(
            work_id=_work_id_str(scope),
            chapter_scope=_chapter_scope_str(scope),
            script_revision=str(script_revision or ""),
            shot_index=shot_index,
            provider=str(provider or ""),
            model_id=str(model_id or ""),
            media_policy=str(media_policy or ""),
            request_key=str(request_key or ""),
            content_key=str(content_key or ""),
            operation_id=str(generation_id or request_id or ""),
            generation_id=str(generation_id or ""),
            task_id=str(task_id or ""),
            attempt_id=str(attempt_id or ""),
            action_type=action_type,
            outcome=outcome,
            error_code=error_code,
            recovered=recovered,
            submit_posts=submit_posts,
            context=context,
            request_id=request_id,
            detail=detail,
        )
    except Exception:
        return

def resume_candidate_attempt(attempt: Path, *, service=None, adapter=None,
                             poll_interval: float = 0.5, timeout_sec: float = 60.0) -> dict:
    """恢复**指定已有任务**：查询原 task_id → 下载 → 验收采纳；**不新生成**。

    没有可恢复 task_id（submission_unknown 且无晚到结果）⇒ 抛
    ``EnhancementError(code="submission_unknown", exit_code=7)``——「无 ID 不许新生成」。
    凭据引用不可解析 ⇒ ``credential_binding_unavailable``（退出码 7）。
    """
    import time as _time

    from shared import enhancement_io as eio
    from shared import video_attempts as va

    attempt = Path(attempt)
    inputs = eio.read_inputs(attempt) or {}
    info = attempt_task_id(attempt)
    if not info["task_id"]:
        raise eio.EnhancementError(
            "submission_unknown", "resume",
            "该 attempt 没有可恢复的 task_id（不知是否已建任务）；禁止在没有 ID 的情况下新生成",
            exit_code=eio.EXIT_SUBMISSION_UNKNOWN, attempt_id=attempt.name)

    if service is None:
        service = _pc("_video_service_for", adapter) if adapter is not None else None
    if service is None:
        from _video_gen.service import create_service
        service = create_service(str(inputs.get("provider") or "") or None)

    binding = {"status": "ok", "provider": inputs.get("provider"),
               "key_slot": inputs.get("key_slot"),
               "credential_profile_ref": inputs.get("credential_profile_ref"),
               "binding_version": 1}
    if not service.restore_task_binding(info["task_id"], binding):
        raise eio.EnhancementError(
            "credential_binding_unavailable", "resume",
            "该任务提交时的凭据引用不可解析（配置变更/凭据已移除）：不得用当前 key 猜历史任务",
            exit_code=eio.EXIT_SUBMISSION_UNKNOWN, attempt_id=attempt.name,
            request_id=info["task_id"])

    package_root = attempt.parent.parent
    resumed = va.open_recovery_attempt(package_root, attempt)
    if info["task_id"] and va.current_state(resumed) != va.STATE_SUBMITTED:
        # 晚到 task_id（不是 submit_post 事件带来的）⇒ 恢复尝试直接以 submitted 起步，
        # 并**补一条合法事件**再改投影（投影落后可按合法事件补，§F2）
        lock = eio.AttemptLock(resumed / eio.LOCK_DIRNAME, purpose="resume adopt task")
        with lock:
            before_state = va.current_state(resumed)
            event = eio.append_event(resumed, "late_task_id", task_id=info["task_id"],
                                     generation_id=info["generation_id"],
                                     before={"state": before_state},
                                     after={"state": va.STATE_SUBMITTED})
            proj = eio.read_lifecycle(resumed) or {}
            proj.update({"state": va.STATE_SUBMITTED, "task_id": info["task_id"],
                         "projected_from_event_seq": event["seq"]})
            eio.write_lifecycle(resumed, proj)
    ctx = _ResumeContext(attempt_dir=resumed, package_root=package_root,
                         scope=dict(inputs.get("scope") or {}),
                         script_revision=str(inputs.get("script_revision") or ""),
                         source="video_candidate_resume")
    clipped = ctx.outputs_dir / "candidate.mp4"
    clipped.parent.mkdir(parents=True, exist_ok=True)

    frozen = dict(inputs.get("frozen_request") or {})
    if not frozen:
        # 没有冻结请求就没有"本次要什么"的事实：不许用默认值拼一个出来（冻结的
        # 意义就是时长/声音/参考角色等按原样重放）。
        raise eio.EnhancementError(
            "frozen_request_missing", "resume",
            "该 attempt 没有冻结请求（input.json 缺 frozen_request）：无法按原事实重放，"
            "拒绝用默认参数重建请求",
            exit_code=eio.EXIT_REJECTED, attempt_id=attempt.name)
    shot_index = int(inputs.get("shot_index") or (frozen.get("source") or {}).get("shot_index") or 0)
    req = _request_from_frozen(frozen, shot_index)
    from _video_gen.models import ExecutionContext
    req.candidate_path = str(clipped)
    req.execution_context = ExecutionContext(
        experiment_root=str(resumed), attempt_id=resumed.name,
        candidate_path=str(clipped), scope=ctx.scope, package="video")

    if va.current_state(resumed) == va.STATE_SUBMITTED:
        va.transition(resumed, va.STATE_RUNNING, kind="resume_query",
                      task_id=info["task_id"])

    deadline = _time.monotonic() + max(0.0, float(timeout_sec))
    obs = None
    while True:
        obs = service.query_observation(info["task_id"])
        if obs.is_terminal():
            break
        if _time.monotonic() >= deadline:
            break
        _time.sleep(max(0.05, float(poll_interval)))

    if obs is None or not obs.is_success():
        state = (va.STATE_PROVIDER_FAILED if obs is not None and obs.status in ("failed", "expired")
                 else va.STATE_UNVERIFIED)
        va.transition(resumed, state, kind="resume_terminal",
                      status=getattr(obs, "status", "unknown"),
                      error=getattr(obs, "error", None))
        return {"status": "failed", "attempt_dir": str(resumed),
                "task_id": info["task_id"], "observation": obs.to_dict() if obs else None,
                "submit_posts": va.submit_post_count(package_root)}

    remaining = max(1.0, deadline - _time.monotonic())
    result = service.wait_for_completion(req, info["task_id"], timeout=int(remaining))
    if not result.success or not result.save_path:
        va.transition(resumed, va.STATE_IO_FAILED, kind="resume_download_failed",
                      error=str(getattr(result, "error", "download failed"))[:400])
        return {"status": "io_failed", "attempt_dir": str(resumed),
                "task_id": info["task_id"], "error": getattr(result, "error", None),
                "submit_posts": va.submit_post_count(package_root)}

    va.transition(resumed, va.STATE_DOWNLOADED, kind="downloaded", task_id=info["task_id"])
    frozen_obj = None
    try:
        from step02_video_gen import request_plan as rp
        frozen_obj = rp.FrozenVideoRequest(**{
            **frozen, "references": [rp.ReferenceBinding(**r) for r in (frozen.get("references") or [])]})
    except Exception:  # noqa: BLE001 - 冻结快照只用于身份绑定，坏了不阻断恢复
        frozen_obj = None
    _qc = _qc_video_product_result(Path(result.save_path), shot_index,
                                   float(getattr(req, "duration", 0) or 0))
    verdict = _record_candidate_qc(Path(resumed), ctx, qc=_qc, shot_idx=shot_index,
                                   shot_hash="", frozen_request=frozen_obj,
                                   video_path=Path(result.save_path))
    if verdict["kind"] != "accept":
        return {"status": verdict["kind"], "attempt_dir": str(resumed),
                "task_id": info["task_id"], "checks": _qc.checks,
                "media_status": verdict["media_status"],
                "semantic_status": verdict["semantic_status"],
                "submit_posts": va.submit_post_count(package_root)}

    accepted = accept_candidate_video(
        ctx, video_path=Path(result.save_path), frozen_request=frozen_obj,
        checks=(list(_qc.checks or []) + verdict["checks_report"]),
        coverage=verdict["coverage"])
    va.transition(resumed, va.STATE_ACCEPTED, kind="accepted",
                  manifest_sha256=accepted["manifest"]["manifest_sha256"])
    return {"status": "accepted", "attempt_dir": str(resumed), "task_id": info["task_id"],
            "manifest_sha256": accepted["manifest"]["manifest_sha256"],
            "submit_posts": va.submit_post_count(package_root)}

def _record_content_policy_revision(attempt: Path, prompt: str, error: str, eio) -> None:
    """content-policy 修订只产出**新的候选计划**建议（不就地改 prompt 重发）。"""
    try:
        from shared.content_policy_fix import auto_fix_prompt
        fixed = auto_fix_prompt(prompt, error, verbose=False)
    except Exception:
        fixed = None
    if fixed and fixed != prompt:
        eio.append_event(attempt, "proposed_revision", operation="replace_prompt_text",
                         reason="content_policy", original_error=str(error)[:300],
                         replacement_prompt=fixed)
