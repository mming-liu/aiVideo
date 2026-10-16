#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/video_reroll.py - 视频候选的**隔离** CLI（计划 §3 表 / §11 命令 / §V3）.

六个子命令（命令形态与计划 §11 一致）::

    python tools/video_reroll.py plan --script FILE --shots 7 --out-dir ROOT/video
    python tools/video_reroll.py inspect --attempt DIR --checks-file FILE
    python tools/video_reroll.py apply-revision --attempt DIR --revision FILE
    python tools/video_reroll.py resume --attempt DIR
    python tools/video_reroll.py replay --fixture DIR --out-dir ROOT/video
    python tools/video_reroll.py report --root ROOT/video

纪律：

* ``plan`` / ``inspect`` / ``apply-revision`` / ``replay`` / ``report`` **默认零网络**
  （不调用任何模型；``replay`` 用桩 provider 消费夹具里的"已录制"结果）；
* ``resume`` **只**恢复夹具/attempt 里**已有**的 task_id：没有 ID 直接
  ``submission_unknown``（退出码 7），**绝不**新生成；
* 所有产物落在调用方给出的隔离 root 内，守卫由 ``shared.enhancement_io`` 统一提供；
* 退出码用 §F3 那套：0 成功（≠内容通过）/2 输入非法/3 工具或 IO 失败/4 明确拒收/
  5 锁忙或 base 变化/6 commit_unconfirmed/7 submission_unknown 或恢复不可用。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio  # noqa: E402

EXIT_OK = eio.EXIT_OK
EXIT_INVALID_INPUT = eio.EXIT_INVALID_INPUT
EXIT_TOOL_FAIL = eio.EXIT_TOOL_FAIL
EXIT_REJECTED = eio.EXIT_REJECTED
EXIT_BUSY = eio.EXIT_BUSY
EXIT_COMMIT_UNCONFIRMED = eio.EXIT_COMMIT_UNCONFIRMED
EXIT_SUBMISSION_UNKNOWN = eio.EXIT_SUBMISSION_UNKNOWN

#: 只在这些操作下允许"视频生成 key 保持不变"（后处理类）
POSTPROCESS_ONLY_OPS = ("replace_postprocess",)
REVISION_OPS = ("replace_references", "replace_action_plan", "replace_frame_binding",
                "replace_postprocess")

DEFAULT_STUB_MODULE = "tests.enhancements.video_stub_provider:StubVideoService"


# ---------------------------------------------------------------- 公共工具

def _emit(payload, *, pretty: bool = True) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def _validate_isolation_root(root: Path, *, must_exist: bool) -> Path:
    """隔离 root 必须是 ``…/<experiment_id>/video`` 形态（不新造第二套路径推导）。"""
    root = Path(root).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root)
    root = root.resolve()
    if root.name != "video":
        raise eio.EnhancementError(
            "invalid_experiment_root", "paths",
            f"隔离 root 必须是包根 …/<experiment_id>/video：{root}",
            exit_code=EXIT_INVALID_INPUT, field_path="--out-dir")
    if must_exist and not root.is_dir():
        raise eio.EnhancementError("experiment_root_missing", "paths",
                                   f"隔离 root 不存在：{root}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="--out-dir")
    return root


def _read_json(path: Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise eio.EnhancementError("file_missing", "input", f"文件不存在: {p}",
                                   exit_code=EXIT_INVALID_INPUT, field_path=str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise eio.EnhancementError("invalid_json", "input", f"JSON 解析失败 {p}: {exc}",
                                   exit_code=EXIT_INVALID_INPUT, field_path=str(p)) from exc
    if not isinstance(data, dict):
        raise eio.EnhancementError("invalid_json", "input", f"顶层必须是对象: {p}",
                                   exit_code=EXIT_INVALID_INPUT, field_path=str(p))
    return data


def _resolve_service(provider: str, stub_module: str):
    """解析 provider 服务：``stub`` 走显式给出的桩模块（离线工具默认路径）。"""
    from _video_gen import service as _service

    if provider == "stub":
        module_name, _, cls_name = stub_module.partition(":")
        try:
            import importlib
            mod = importlib.import_module(module_name)
            cls = getattr(mod, cls_name or "StubVideoService")
        except Exception as exc:  # noqa: BLE001
            raise eio.EnhancementError(
                "stub_provider_unavailable", "provider",
                f"无法加载桩 provider {stub_module}: {exc}", exit_code=EXIT_TOOL_FAIL) from exc
        _service.register("stub", cls)
        return cls(), getattr(mod, "StubVideoAdapter", None)
    try:
        return _service.create_service(provider or None), None
    except Exception as exc:  # noqa: BLE001
        raise eio.EnhancementError("provider_unavailable", "provider",
                                   f"无法创建 provider {provider!r}: {exc}",
                                   exit_code=EXIT_TOOL_FAIL) from exc


def _attempt_frozen(attempt: Path) -> dict:
    inputs = eio.read_inputs(Path(attempt)) or {}
    frozen = inputs.get("frozen_request")
    if not isinstance(frozen, dict):
        raise eio.EnhancementError("frozen_request_missing", "input",
                                   f"attempt 没有冻结请求快照: {attempt}",
                                   exit_code=EXIT_INVALID_INPUT, attempt_id=Path(attempt).name)
    return frozen


def _load_frozen_obj(frozen: dict):
    from step02_video_gen import request_plan as rp
    refs = [rp.ReferenceBinding(**r) for r in (frozen.get("references") or [])]
    data = dict(frozen)
    data["references"] = refs
    return rp.FrozenVideoRequest(**data)


# ---------------------------------------------------------------- plan

def cmd_plan(args) -> int:
    """只组装冻结请求 + 结构预检（零网络、零生成 POST）。"""
    from step02b_video_gen.main import build_plan
    from shared.paths import vimax_shots_dir

    out_root = _validate_isolation_root(Path(args.out_dir), must_exist=False)
    script = _read_json(Path(args.script))
    shots_dir = Path(args.shots_dir) if args.shots_dir else vimax_shots_dir()
    from shared.shot_selector import parse_shot_selector
    only = parse_shot_selector(args.shots)
    chapter = args.chapter if args.chapter is not None else 1
    plan = build_plan(script, shots_dir, only_indices=only, chapter=chapter,
                      provider=args.provider or "", model_id=args.model)
    plan["script"] = str(Path(args.script).resolve())
    plans_dir = out_root / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    seq = len(list(plans_dir.glob("plan_*.json"))) + 1
    target = plans_dir / f"plan_{seq:04d}.json"
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit({"plan": str(target), "ready": plan["ready"], "blocked": plan["blocked"],
           "estimated_generations": plan["estimated_generations"], "network_calls": 0})
    return EXIT_OK


# ---------------------------------------------------------------- inspect

def _report_from_checks(checks: dict, *, attempt: Path, frozen: dict):
    from step02_video_gen import reroll_decision as rd

    issues = []
    for i in checks.get("issues") or []:
        issues.append(rd.VideoIssue(
            issue_id=str(i.get("issue_id") or f"issue-{len(issues) + 1}"),
            kind=str(i.get("kind") or "unknown"),
            time_range=tuple(i["time_range"]) if i.get("time_range") else None,
            entity_refs=list(i.get("entity_refs") or []),
            relation=i.get("relation"), expected=i.get("expected"),
            observed=i.get("observed") if i.get("evidence_paths") else None,
            evidence_paths=list(i.get("evidence_paths") or []),
            evidence_hashes=list(i.get("evidence_hashes") or []),
            confidence=i.get("confidence"), required_change=i.get("required_change"),
            field_paths=list(i.get("field_paths") or [])))
    return rd.VideoCheckReport(
        media_status=str(checks.get("media_status") or rd.STATUS_UNKNOWN),
        semantic_status=str(checks.get("semantic_status") or rd.STATUS_UNKNOWN),
        coverage=dict(checks.get("coverage") or {}), issues=issues,
        tool_versions=dict(checks.get("tool_versions") or {}),
        source=dict(frozen.get("source") or {}),
        request={"request_key": frozen.get("request_key")},
        attempt={"attempt_id": Path(attempt).name})


def cmd_inspect(args) -> int:
    """读 attempt + 检查报告 → 纯函数裁决（只读，零网络、零副作用）。"""
    from step02_video_gen import reroll_decision as rd

    attempt = Path(args.attempt).resolve()
    if not attempt.is_dir():
        raise eio.EnhancementError("attempt_missing", "input", f"attempt 不存在: {attempt}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="--attempt")
    frozen = _attempt_frozen(attempt)
    checks = _read_json(Path(args.checks_file))
    report = _report_from_checks(checks, attempt=attempt, frozen=frozen)
    history = []
    for event in eio.read_events_strict(attempt)[0]:
        if event.get("kind") == "decision":
            history.append({"request_key": frozen.get("request_key"), "fingerprints": []})
    decision = rd.decide(report, {"request_key": frozen.get("request_key")}, history)
    state = _read_state(attempt)
    _emit({"attempt": str(attempt), "state": state,
           "request_key": frozen.get("request_key"),
           "media_status": report.media_status, "semantic_status": report.semantic_status,
           "coverage": report.coverage, "decision": decision.to_dict()})
    if decision.kind in (rd.DECISION_REVISE_INPUTS, rd.DECISION_POSTPROCESS, rd.DECISION_STOP):
        return EXIT_REJECTED
    return EXIT_OK


def _read_state(attempt: Path) -> str:
    from shared import video_attempts as va
    try:
        return va.current_state(Path(attempt))
    except Exception:  # noqa: BLE001 - 只读展示，失败标 unknown
        return "unknown"


# ---------------------------------------------------------------- apply-revision

def _sha256(path: Path) -> str:
    return eio.sha256_file(path)


def _rebuild_references(frozen: dict, replacement: list, *, base_dir: Path) -> list[dict]:
    """按整表替换重建引用（**确定性重编号** r1..rN，不让模型猜编号迁移）。"""
    from step02_video_gen import request_plan as rp

    out: list[dict] = []
    for item in replacement or []:
        if not isinstance(item, dict):
            raise eio.EnhancementError("invalid_revision", "revision",
                                       f"references 条目必须是对象: {item!r}",
                                       exit_code=EXIT_INVALID_INPUT, field_path="replacement")
        role = str(item.get("role") or "")
        if role not in rp.KNOWN_ROLES:
            raise eio.EnhancementError("unknown_role", "revision",
                                       f"未知引用角色 {role!r}",
                                       exit_code=EXIT_INVALID_INPUT,
                                       field_path="replacement.references")
        path = item.get("path")
        resolved = None
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            if not p.is_file():
                raise eio.EnhancementError(
                    "reference_missing", "revision",
                    f"替换后的引用素材不存在: {p}（不得引用未落盘的图）",
                    exit_code=EXIT_INVALID_INPUT, field_path="replacement.references")
            resolved = str(p)
            digest = _sha256(p)
            claimed = item.get("sha256")
            if claimed and claimed != digest:
                raise eio.EnhancementError(
                    "reference_digest_mismatch", "revision",
                    f"声明的素材摘要与实际字节不符: {p}",
                    exit_code=EXIT_INVALID_INPUT, field_path="replacement.references")
        out.append({"ref_id": f"r{len(out) + 1}", "role": role, "path": resolved,
                    "sha256": (_sha256(Path(resolved)) if resolved else None),
                    "bindings": list(item.get("bindings") or []),
                    "inherit": list(item.get("inherit") or []),
                    "exclude": list(item.get("exclude") or [])})
    return out


def cmd_apply_revision(args) -> int:
    """VideoRevision v1 应用：检查 base → 隔离副本替换 → 重编号 → 预检 → 新 ready。"""
    from step02_video_gen import request_plan as rp
    from shared import video_attempts as va

    attempt = Path(args.attempt).resolve()
    base = _attempt_frozen(attempt)
    revision = _read_json(Path(args.revision))

    op = str(revision.get("operation") or "")
    if op not in REVISION_OPS:
        raise eio.EnhancementError(
            "unsupported_operation", "revision",
            f"本包只接受 {REVISION_OPS}；故事内容与声音取舍必须交回原生产者",
            exit_code=EXIT_INVALID_INPUT, field_path="operation")
    base_key = str(revision.get("base_request_key") or "")
    if base_key != str(base.get("request_key") or ""):
        raise eio.EnhancementError(
            "base_changed", "revision",
            "base_request_key 与 attempt 的冻结请求不一致（base 已变，拒绝在旧事实上替换）",
            exit_code=EXIT_BUSY, field_path="base_request_key")

    new = json.loads(json.dumps(base))          # 隔离副本：只在副本上完整替换
    replacement = revision.get("replacement")
    postprocess = None

    if op == "replace_action_plan":
        if not isinstance(replacement, dict):
            raise eio.EnhancementError("invalid_revision", "revision",
                                       "replace_action_plan 需要完整的 requirements 对象",
                                       exit_code=EXIT_INVALID_INPUT, field_path="replacement")
        base_req = dict(base.get("requirements") or {})
        core_missing = _missing_core_events(base_req, replacement)
        if core_missing:
            raise eio.EnhancementError(
                "core_event_dropped", "revision",
                f"替代计划丢失核心事件: {core_missing}（不得静默删剧情）",
                exit_code=EXIT_REJECTED, field_path="replacement.events",
                residual_issues=core_missing)
        new["requirements"] = replacement
    elif op == "replace_postprocess":
        # 后处理**不改变**视频生成事实：key 必须保持不变（§V3 表「字幕/编码」行）
        postprocess = replacement if isinstance(replacement, dict) else {"value": replacement}
    elif op == "replace_references":
        if not isinstance(replacement, list):
            raise eio.EnhancementError("invalid_revision", "revision",
                                       "replace_references 需要**整表**引用对象数组",
                                       exit_code=EXIT_INVALID_INPUT, field_path="replacement")
        new["references"] = _rebuild_references(
            base, replacement, base_dir=Path(args.revision).resolve().parent)
    elif op == "replace_frame_binding":
        frames = (replacement or {}).get("references", (replacement or {}).get("frames"))
        if frames is None:
            raise eio.EnhancementError("invalid_revision", "revision",
                                       "replace_frame_binding 需要整组 references/frames",
                                       exit_code=EXIT_INVALID_INPUT, field_path="replacement")
        kept = [r for r in (base.get("references") or [])
                if r.get("role") not in ("rp_role_first", "first_frame", "last_frame")]
        rebuilt = _rebuild_references(base, frames, base_dir=Path(args.revision).resolve().parent)
        new["references"] = _rebuild_references(
            base, rebuilt + kept, base_dir=Path(args.revision).resolve().parent)

    frozen_obj = _load_frozen_obj(new)
    frozen_obj.compute_key()
    if op == "replace_postprocess" and frozen_obj.request_key != base.get("request_key"):
        raise eio.EnhancementError(
            "postprocess_changed_generation_key", "revision",
            "replace_postprocess 改变了视频生成 key（后处理不得进入生成事实）",
            exit_code=EXIT_REJECTED, field_path="replacement")
    problems = rp.validate_frozen_request(frozen_obj)

    # 新 attempt（parent 指向 base）；准备与预检都在隔离副本里完成
    root = attempt.parent.parent
    new_attempt_id = va.new_attempt_id("rev")
    # 来源身份随上下文一起给（§F4）：采纳 manifest 必须答得出"哪一章、哪一稿"，
    # 缺了会被采纳出口拒收——这里从 base 的冻结来源照抄，不自造。
    _base_source = dict(base.get("source") or {})
    ctx = eio.CandidateContext(experiment_id=root.parent.name, package="video",
                              attempt_id=new_attempt_id,
                              chapter=(_scope_chapter(base) or None),
                              scope=dict(_base_source.get("scope") or {}),
                              script_revision=str(_base_source.get("script_revision") or ""),
                              source="video_reroll")
    ctx.prepare()
    inputs = dict(eio.read_inputs(attempt) or {})
    inputs.update({"frozen_request": frozen_obj.to_dict(),
                   "shot_index": inputs.get("shot_index") or (base.get("source") or {}).get("shot_index"),
                   "revision": {k: revision.get(k) for k in
                                ("request_id", "base_request_key", "issue_ids", "operation",
                                 "reason", "expected_resolved_issues")},
                   "postprocess": postprocess,
                   "parent_attempt_id": attempt.name})
    va.open_attempt(root, new_attempt_id, request_key=frozen_obj.request_key,
                    scope=dict(base.get("source", {}).get("scope") or {}),
                    script_revision=str(base.get("source", {}).get("script_revision") or ""),
                    provider=str(base.get("provider") or ""), model_id=str(base.get("model_id") or ""),
                    parent_attempt_id=attempt.name)
    eio.freeze_inputs(ctx.attempt_dir, inputs)

    resolved, residual = _issue_disposition(revision, op, postprocess)
    state = va.STATE_READY if not problems else va.STATE_DRAFT
    if not problems:
        va.transition(ctx.attempt_dir, va.STATE_READY, kind="revision_applied",
                      operation=op, resolved=resolved, residual=residual)
    else:
        eio.append_event(ctx.attempt_dir, "revision_blocked", problems=problems)
    _emit({"attempt": str(ctx.attempt_dir), "operation": op, "state": _read_state(ctx.attempt_dir),
           "request_key": frozen_obj.request_key, "base_request_key": base.get("request_key"),
           "key_changed": frozen_obj.request_key != base.get("request_key"),
           "resolved_issues": resolved, "residual_issues": residual,
           "problems": problems, "submit_count": 0})
    return EXIT_OK if not problems else EXIT_REJECTED


def _scope_chapter(frozen: dict):
    try:
        return int(((frozen.get("source") or {}).get("scope") or {}).get("chapter"))
    except (TypeError, ValueError):
        return None


def _missing_core_events(base_req: dict, replacement: dict) -> list:
    """核心事实保留判据：base 里标了 core 的事件必须在替代计划里仍在（按 id/文本）。"""
    def _keys(events):
        out = set()
        for e in events or []:
            if isinstance(e, dict):
                out.add(str(e.get("event_id") or e.get("id") or e.get("text")
                            or json.dumps(e, ensure_ascii=False, sort_keys=True)))
            else:
                out.add(str(e))
        return out

    missing = []
    new_keys = _keys(replacement.get("events"))
    for e in base_req.get("events") or []:
        if isinstance(e, dict) and not e.get("core", True):
            continue
        key = (str(e.get("event_id") or e.get("id") or e.get("text")
                   or json.dumps(e, ensure_ascii=False, sort_keys=True))
               if isinstance(e, dict) else str(e))
        if key not in new_keys:
            missing.append(key)
    return missing


def _issue_disposition(revision: dict, op: str, postprocess) -> tuple:
    """resolved/residual 计算：**只**在替换真正覆盖到的层上销账，不做乐观销账。"""
    issue_ids = [str(i) for i in (revision.get("issue_ids") or [])]
    expected = [str(i) for i in (revision.get("expected_resolved_issues") or [])]
    coverable = set(issue_ids)
    resolved = [i for i in expected if i in coverable]
    residual = [i for i in issue_ids if i not in resolved]
    return resolved, residual


# ---------------------------------------------------------------- resume

def cmd_resume(args) -> int:
    """只恢复**已有** task（无 ID ⇒ 退出码 7，绝不新生成）。"""
    from step02_video_gen.submit_poll import resume_candidate_attempt

    attempt = Path(args.attempt).resolve()
    if not attempt.is_dir():
        raise eio.EnhancementError("attempt_missing", "input", f"attempt 不存在: {attempt}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="--attempt")
    result = resume_candidate_attempt(attempt)
    _emit(result)
    status = result.get("status")
    if status == "accepted":
        return EXIT_OK
    if status in ("failed", "rejected"):
        return EXIT_REJECTED
    if status == "io_failed":
        return EXIT_TOOL_FAIL
    if status in ("submission_unknown", "credential_binding_unavailable"):
        return EXIT_SUBMISSION_UNKNOWN
    return EXIT_OK


# ---------------------------------------------------------------- replay

class _StubAdapter:
    """把桩 provider 适配成 submit_poll 需要的 adapter 面（不引入生产适配器改动）。"""

    def __init__(self, service):
        self.service = service
        self._svc = service

    def get_task_binding(self, task_id: str) -> dict:
        return self.service.get_task_binding(task_id)

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        return self.service.restore_task_binding(task_id, binding)

    async def fetch_result(self, req, task_id: str):
        return self.service.wait_for_completion(req, task_id)


def _fixture_path(fixture_dir: Path, value) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (fixture_dir / p).resolve()


def cmd_replay(args) -> int:
    """消费夹具里的"已录制"结果：prepared → **一次**桩 POST → 下载 → QC → 采纳。"""
    from step02_video_gen import submit_poll as sp

    fixture_dir = Path(args.fixture).resolve()
    out_root = _validate_isolation_root(Path(args.out_dir), must_exist=False)
    fixture = _read_json(fixture_dir / "fixture.json")

    # 回放不等待真实轮询间隔（否则每个夹具要等 3 分钟）；真实入口的判据不变
    sp.INITIAL_POLL_DELAY = 0
    sp.POLL_BASE_INTERVAL = 0
    sp.POLL_MAX_INTERVAL = 0
    sp.POLL_JITTER = 0

    script = fixture.get("script") or {"characters": [], "shots": [fixture.get("shot") or {}]}
    shot = fixture.get("shot") or {"index": fixture.get("shot_index", 1)}
    shot = dict(shot)
    shot.setdefault("index", fixture.get("shot_index", 1))
    mode = str(fixture.get("mode") or "text")
    import os
    os.environ["VIDEO_REFERENCE_MODE"] = mode
    if fixture.get("provider"):
        os.environ["VIDEO_PROVIDER"] = str(fixture["provider"])

    frame_paths = []
    for ref in fixture.get("references") or []:
        path = _fixture_path(fixture_dir, ref["path"])
        if not path.is_file():
            raise eio.EnhancementError("fixture_media_missing", "input",
                                       f"夹具素材缺失: {path}",
                                       exit_code=EXIT_INVALID_INPUT, field_path=ref["path"])
        frame_paths.append(str(path))

    service, adapter_cls = _resolve_service(str(fixture.get("provider") or "stub"),
                                            args.stub_module or DEFAULT_STUB_MODULE)
    video_source = fixture.get("video")
    if video_source:
        service.video_source = _fixture_path(fixture_dir, video_source)
    adapter = (adapter_cls or _StubAdapter)(service)

    chapter = int(fixture.get("chapter") or 1)
    experiment_id = args.experiment_id or out_root.parent.name
    attempt_id = os.environ.get("REPLAY_ATTEMPT_ID")
    if not attempt_id:
        from shared.video_attempts import new_attempt_id as _new_attempt_id
        attempt_id = _new_attempt_id("replay")
    ctx = eio.CandidateContext(
        experiment_id=experiment_id, package="video", attempt_id=attempt_id, chapter=chapter,
        scope=dict(fixture.get("scope") or {"chapter": chapter}),
        script_revision=str(fixture.get("script_revision") or ""),
        source="video_reroll_replay")
    ctx.prepare()
    if ctx.package_root.resolve() != out_root:
        raise eio.EnhancementError(
            "out_dir_not_single_source", "paths",
            f"--out-dir {out_root} 与实验根单源不一致（按 shared.paths 推导为 {ctx.package_root}）",
            exit_code=EXIT_INVALID_INPUT, field_path="--out-dir")

    shot_dir = ctx.shot_dir(shot["index"])
    shot_dir.mkdir(parents=True, exist_ok=True)
    video_path = shot_dir / "video.mp4"

    from step02_video_gen.prompt_builder import _make_request

    async def _flow() -> bool:
        """提交与轮询必须在**同一个事件循环**里（轮询任务是 async task）。"""
        task = await sp._submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=video_path, frame_paths=frame_paths,
            shot_hash="replay", video_adapter=adapter, poll_semaphore=asyncio.Semaphore(1),
            script=script, idx=shot["index"], prompt=str(shot.get("prompt") or ""),
            characters=script.get("characters", []), continuity_ref_image=None, mode="A",
            candidate_context=ctx, logger=None, make_request=_make_request)
        if task is None:
            return False
        try:
            return bool(await task)
        except Exception:  # noqa: BLE001 - 轮询异常在 _failed_videos 里有记录
            return False

    ok = asyncio.run(_flow())
    state = _read_state(ctx.attempt_dir)
    accepted = eio.read_accepted(ctx.attempt_dir)
    expected = fixture.get("expected") or {}
    report = {"attempt": str(ctx.attempt_dir), "state": state, "submit_posts": len(service.posts),
              "task_ids": sorted(service.tasks), "poll_ok": ok,
              "accepted": bool(accepted)}
    if expected:
        report["expected"] = expected
        report["matches_expected"] = (
            int(expected.get("submit_posts", len(service.posts))) == len(service.posts)
            and (expected.get("state") is None or expected["state"] == state))
    _emit(report)
    if expected and not report["matches_expected"]:
        return EXIT_REJECTED
    return EXIT_OK if accepted else EXIT_REJECTED


# ---------------------------------------------------------------- report

def cmd_report(args) -> int:
    """只读汇总：attempts/ 下每个尝试的状态、task、提交次数与采纳情况。"""
    from shared import video_attempts as va

    root = _validate_isolation_root(Path(args.root), must_exist=True)
    attempts_root = root / eio.ATTEMPTS_DIRNAME
    rows = []
    for att in sorted(p for p in attempts_root.iterdir() if p.is_dir()) if attempts_root.is_dir() else []:
        inputs = eio.read_inputs(att) or {}
        from step02_video_gen.submit_poll import attempt_task_id
        info = attempt_task_id(att)
        accepted = eio.read_accepted(att)
        rows.append({
            "attempt_id": att.name,
            "state": info["state"],
            "request_key": inputs.get("request_key"),
            "task_id": info["task_id"],
            "submit_posts": info["submit_posts"],
            "parent_attempt_id": inputs.get("parent_attempt_id"),
            "generation_id": inputs.get("generation_id"),
            "accepted": bool(accepted),
            "accepted_manifest": (accepted or {}).get("manifest_path"),
        })
    summary = {
        "root": str(root),
        "attempts": len(rows),
        "submit_posts": va.submit_post_count(root),
        "tasks": sorted({r["task_id"] for r in rows if r["task_id"]}),
        "generations": {g: {"attempts": rec["attempts"], "submit_posts": rec["submit_posts"]}
                        for g, rec in va.generations(root).items()},
        "accepted": sum(1 for r in rows if r["accepted"]),
        "rows": rows,
    }
    _emit(summary)
    return EXIT_OK


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python tools/video_reroll.py",
                                     description="视频候选隔离 CLI（plan/inspect/apply-revision/resume/replay/report）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="只组装冻结请求 + 预检（零网络）")
    p.add_argument("--script", required=True)
    p.add_argument("--shots", "-s", default=None)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--shots-dir", default=None)
    p.add_argument("--chapter", type=int, default=None)
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None,
                   help="显式模型 ID（缺省取能力注册表的 active 模型；桩 provider 需显式给）")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("inspect", help="读 attempt + 检查报告做裁决（只读）")
    p.add_argument("--attempt", required=True)
    p.add_argument("--checks-file", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("apply-revision", help="应用 VideoRevision v1（隔离副本）")
    p.add_argument("--attempt", required=True)
    p.add_argument("--revision", required=True)
    p.set_defaults(func=cmd_apply_revision)

    p = sub.add_parser("resume", help="恢复已有 task（无 ID 不新生成）")
    p.add_argument("--attempt", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("replay", help="离线回放夹具（桩 provider）")
    p.add_argument("--fixture", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--experiment-id", default=None)
    p.add_argument("--stub-module", default=None)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("report", help="汇总隔离 root 下的尝试与采纳")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except eio.EnhancementError as exc:
        _emit(exc.payload())
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - 未分类失败：结构化 + 退出码 3，不伪报成功
        _emit({"code": "tool_failed", "stage": getattr(args, "command", "?"),
               "message": f"{type(exc).__name__}: {exc}"})
        return EXIT_TOOL_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
