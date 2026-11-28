# -*- coding: utf-8 -*-
"""S07: `step02_video_gen.submit_poll` 的 qc 职责（2026-11-28 拆分）.

父模块保留提交/轮询主编排与运行状态；本模块在调用点读取父模块当前值，
导入期不回导父模块，保证父模块上的 monkeypatch 仍然生效。
"""
from pathlib import Path
from dataclasses import dataclass, field

_PARENT = "step02_video_gen.submit_poll"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


@dataclass
class _VideoProductQc:
    """产物 QC 的结构化结果（计划 §15「QC证据」）。

    ``measured_pass`` = 真的测过且通过；``soft_allowed`` = 前置探测失败但按旧合同
    允许继续（**不得**满足 required_check 里"要求真实测量 PASS"的项）；
    ``failed`` = 明确检出问题。
    """

    status: str                      # measured_pass | soft_allowed | failed
    reason: str | None = None
    checks: list = field(default_factory=list)
    #: S06/V3：0 元探针的结构化结果与问题（供 reroll_decision 分类裁决）
    media_probe: dict | None = None
    media_issues: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "checks": list(self.checks),
                "media_probe": self.media_probe,
                "media_issues": list(self.media_issues)}

def _qc_video_product(video_path: Path, shot_idx: int, expected_duration: float,
                      shot: dict | None = None,
                      characters: list | None = None) -> bool:
    """视频产物内容质检（A6 修复 08-11）——**旧接口，方向固定**。

    旧语义（§15「QC证据」）：``True`` = 现有门允许继续（**可能是探测失败软通过**）；
    ``False`` = 明确拒收。判据只有一份：委托 :func:`_qc_video_product_result`。
    需要区分"真测过"与"软通过"时用结构化版本。
    """
    return _qc_video_product_result(video_path, shot_idx, expected_duration,
                                    shot=shot, characters=characters).status != "failed"

def _qc_video_product_result(video_path: Path, shot_idx: int, expected_duration: float,
                             shot: dict | None = None,
                             characters: list | None = None,
                             log_path: Path | None = None) -> _VideoProductQc:
    """视频产物内容质检（结构化版本；判据与旧 bool 版**同一份**）。

    ``log_path`` 非空 ⇒ 日志写该路径（候选模式用它把日志留在 attempt 内，
    不污染正式 ``step02.log``）。

    S06/V3：**先跑 0 元 FFmpeg 硬探针**（黑场/静帧/残缺/首帧偏离），再按配置决定
    是否调昂贵的视觉 QC。探针判 critical 时直接判失败（不花任何模型调用）。
    """
    from shared.tee_logger import get_logger
    from shared.log_utils import resolve_log_file
    logger = get_logger(log_path or resolve_log_file("step02.log"), console_level="INFO")
    checks: list[dict] = []
    try:
        if not video_path.exists() or video_path.stat().st_size <= 20 * 1024:
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：文件缺失或过小")
            return _VideoProductQc("failed", reason="文件缺失或过小",
                                   checks=[{"name": "file_size", "status": "fail"}])
        actual = _pc("_probe_video_duration", video_path)
        if actual <= 0:
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：时长探测失败，跳过时长校验")
            checks.append({"name": "duration", "status": "unmeasured"})
        else:
            exp = expected_duration or 0
            if exp > 0:
                dev_abs = abs(actual - exp)
                dev_ratio = dev_abs / exp if exp else 1.0
                if dev_abs > _pv("VIDEO_DURATION_DEV_SEC") and dev_ratio > _pv("VIDEO_DURATION_DEV_RATIO"):
                    logger.warn(
                        f"  [Shot {shot_idx}] 视频 QC FAIL：实际时长 {actual:.1f}s vs "
                        f"期望 {exp:.1f}s（偏差 {dev_abs:.1f}s / {dev_ratio:.0%}）——"
                        f"视频短截/动作没播完，判定坏视频"
                    )
                    return _VideoProductQc(
                        "failed", reason=f"时长偏差 {dev_abs:.1f}s/{dev_ratio:.0%}",
                        checks=checks + [{"name": "duration", "status": "fail",
                                          "actual": actual, "expected": exp}])
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：时长 {actual:.1f}s 校验通过")
            checks.append({"name": "duration", "status": "pass",
                           "actual": actual, "expected": expected_duration})

        # ---- S06/V3：0 元 FFmpeg 硬探针（任何配置下都跑，零模型调用） ----
        probe_result, media_issues = None, []
        try:
            from step02_video_gen.video_qc import probe_video_media
            probe_result = probe_video_media(
                video_path, expected_duration=expected_duration,
                first_frame_path=_first_frame_of(shot_idx))
            media_issues = list(probe_result.issues or [])
            checks.append({"name": "media_probe", "status": probe_result.status,
                           "measured": probe_result.measured,
                           "issues": [i.get("code") for i in media_issues],
                           "reason": probe_result.reason})
            if probe_result.critical:
                logger.warn(
                    "  [Shot %s] 0 元媒体探针检出确定性问题: %s"
                    % (shot_idx, [i.get("code") for i in probe_result.critical]))
                return _VideoProductQc(
                    "failed", reason="媒体探针: %s" % ",".join(
                        str(i.get("code")) for i in probe_result.critical),
                    checks=checks, media_probe=probe_result.to_dict(),
                    media_issues=media_issues)
        except Exception as probe_exc:  # noqa: BLE001 - 探针异常=未验证，不误杀
            logger.verbose(f"  [Shot {shot_idx}] 0 元媒体探针异常（不阻断）: {probe_exc}")
            checks.append({"name": "media_probe", "status": "unavailable"})

        if _pv("VIDEO_QC_ENABLED"):
            try:
                from step02_video_gen.video_qc import qc_video_file_result
                vis = qc_video_file_result(video_path, shot_idx, shot=shot, characters=characters)
                checks.append({"name": "visual", "status": vis.status,
                               "measured": vis.measured, "reason": vis.reason})
                if vis.status == "critical":
                    logger.warn(f"  [Shot {shot_idx}] 视频 QC 视觉检测发现问题")
                    return _VideoProductQc("failed", reason="视觉 critical", checks=checks,
                                           media_probe=(probe_result.to_dict()
                                                        if probe_result is not None else None),
                                           media_issues=media_issues)
            except Exception as e:
                logger.verbose(f"  [Shot {shot_idx}] 视频视觉 QC 异常（不阻断）: {e}")
                checks.append({"name": "visual", "status": "unavailable"})
        measured = all(c.get("status") in ("pass",) for c in checks if c.get("name") == "duration")
        return _VideoProductQc("measured_pass" if measured else "soft_allowed",
                               reason=None if measured else "时长未测得（软通过）",
                               checks=checks,
                               media_probe=(probe_result.to_dict()
                                            if probe_result is not None else None),
                               media_issues=media_issues)
    except Exception as e:
        logger.verbose(f"  [Shot {shot_idx}] 视频 QC 异常（不阻断）: {e}")
        return _VideoProductQc("soft_allowed", reason=f"QC 异常: {e}", checks=checks)

def _check_keyframe_hallucination(frame_path: str) -> dict:
    """读取某帧的 QC 结论，**只认绑定到当前图片结果**的 sidecar.

    U4：sidecar 必须带 image.sha256 且与当前文件一致，status 才有效。旧 sidecar
    （无摘要）、换图后遗留的旧结论、调用失败与未运行，一律按「未验证」返回：
    不冒充通过，也不凭旧结论删新图。旧字段 has_hallucination/issues 仍兼容读取。

    Returns:
        {"has_critical", "issues", "status", "verified", "reason"}
    """
    if not frame_path:
        return {"has_critical": False, "issues": [], "status": "qc_not_run",
                "verified": False, "reason": "no_frame"}
    frame_path_obj = Path(frame_path)
    from shared.image_qc import qc_state
    state = qc_state(frame_path_obj)
    status = state["status"]
    data = state.get("data") or {}
    if status == "qc_rejected":
        critical = [i for i in data.get("issues", [])
                    if isinstance(i, dict) and i.get("severity") == "critical"]
        return {"has_critical": bool(critical), "issues": critical,
                "status": status, "verified": True, "reason": ""}
    if status == "qc_passed":
        return {"has_critical": False, "issues": [], "status": status,
                "verified": True, "reason": ""}
    return {"has_critical": False, "issues": [], "status": status,
            "verified": False, "reason": state.get("reason") or status}

def _first_frame_of(shot_idx: int) -> Path | None:
    """该镜的输入首帧路径（有才给 probe 比首帧；没有就不测相似度）."""
    try:
        from shared.paths import FIRST_FRAME_FILENAME, vimax_shots_dir
        path = vimax_shots_dir() / str(int(shot_idx)) / FIRST_FRAME_FILENAME
        return path if path.is_file() else None
    except Exception:
        return None

def _report_to_dict(report) -> dict:
    return {"media_status": report.media_status, "semantic_status": report.semantic_status,
            "coverage": dict(report.coverage or {}),
            "issues": [{"issue_id": i.issue_id, "kind": i.kind, "time_range": i.time_range,
                        "entity_refs": list(i.entity_refs), "expected": i.expected,
                        "observed": i.observed, "confidence": i.confidence,
                        "required_change": i.required_change,
                        "field_paths": list(i.field_paths),
                        "evidence_hashes": list(i.evidence_hashes)} for i in report.issues],
            "tool_versions": dict(report.tool_versions or {}),
            "source": dict(report.source or {}), "request": dict(report.request or {}),
            "attempt": dict(report.attempt or {})}

def _qc_coverage(qc, media_status: str) -> tuple:
    """把产物 QC 的结构化结果翻成（媒体覆盖, 语义覆盖, 语义状态）。

    ``not_applicable`` = 这一轮**没有**要求语义检查（不是"测过且通过"）——
    接受的判据仍要求媒体轴**真测过**（§V4「好片/坏片/工具不可用」三态可分）。
    """
    from step02_video_gen import reroll_decision as rd

    checks = {str(c.get("name")): str(c.get("status")) for c in (qc.checks or [])}
    media_coverage = ["bytes"]
    if checks.get("file_size") in ("pass", "fail"):
        media_coverage.append("file_size")
    if checks.get("duration") in ("pass", "fail"):
        media_coverage.append("duration")
    # S06/V3：0 元硬探针真跑过 ⇒ media 轴覆盖扩展到 black/freeze/decode/首帧
    if checks.get("media_probe") in ("clean", "warned", "critical"):
        media_coverage.extend(["media_probe", "black", "freeze", "decode"])
        if any((i or {}).get("kind") == "frame_binding"
               for i in (getattr(qc, "media_issues", None) or [])):
            media_coverage.append("first_frame")
    if "visual" not in checks:
        return media_coverage, [], "not_applicable"
    visual = checks["visual"]
    if visual in ("clean", "pass"):
        return media_coverage, ["three_frames"], rd.STATUS_PASS
    if visual in ("critical", "fail"):
        return media_coverage, ["three_frames"], rd.STATUS_FAIL
    return media_coverage, ["three_frames"], rd.STATUS_UNKNOWN

def _record_candidate_qc(shot_dir: Path, candidate_context, *, qc, shot_idx, shot_hash,
                         frozen_request, video_path: Path) -> str:
    """把产物 QC 结果换成**结构化报告 + 裁决**（§V3：bool 不再控制重生成）。

    返回 ``{"kind", "coverage", "checks", "checks_report", "decision", …}``；
    分支状态与事件在同一处落盘，报告层不猜身份。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va
    from step02_video_gen import reroll_decision as rd

    attempt = _pc("_candidate_attempt", shot_dir, candidate_context)
    request_key = getattr(frozen_request, "request_key", "") if frozen_request else ""

    if qc.status == "failed":
        media_status = rd.STATUS_FAIL
        issues = [rd.VideoIssue(
            issue_id=f"shot{shot_idx}-qc", kind="timing_window",
            observed=qc.reason or "产物 QC 未通过",
            required_change="按修订接口回到输入层/原生产者（重生成属新费用，需人工确认）",
            field_paths=["parameters.duration"], evidence_paths=[str(video_path)])]
    elif qc.status == "measured_pass":
        media_status = rd.STATUS_PASS
        issues = []
    else:
        # 前置探测失败 ⇒ **未验证**（不得伪报 PASS，也不因此判生成错误）
        media_status = rd.STATUS_UNKNOWN
        issues = []

    # S06/V3：0 元探针检出的问题按**证据类型**分类进入裁决（不在这里判重抽）
    probe = getattr(qc, "media_probe", None) or {}
    for i, issue in enumerate(getattr(qc, "media_issues", None) or []):
        kind = str(issue.get("kind") or "decode")
        time_range = issue.get("time_range")
        issues.append(rd.VideoIssue(
            issue_id=f"shot{shot_idx}-media-{i}",
            kind=kind,
            time_range=(tuple(time_range) if isinstance(time_range, (list, tuple))
                        and len(time_range) == 2 else None),
            expected=None, observed=str(issue.get("message") or ""),
            evidence_paths=[str(video_path)],
            required_change=("回输入层修正（帧绑定/时长/内容）" if issue.get("severity")
                             == "critical" else "后处理可缓解，需人工确认"),
            field_paths=list((issue.get("evidence") or {}).keys())))
        if issue.get("severity") == "critical":
            media_status = rd.STATUS_FAIL

    # 语义轴：**没跑**（未启用/未要求）与 **跑了但测不到** 是两件事（§V3 覆盖率）：
    # - 没跑 ⇒ not_applicable（不阻断接受，但 manifest 的 check 记 not_run，不写 pass）；
    # - 跑了但工具不可用/证据不足 ⇒ unknown ⇒ 裁决只能是 unverified。
    media_coverage, semantic_coverage, semantic_status = _qc_coverage(qc, media_status)

    report = rd.VideoCheckReport(
        media_status=media_status, semantic_status=semantic_status,
        coverage={"media": media_coverage, "semantic": semantic_coverage},
        issues=issues, tool_versions={"qc": "step02._qc_video_product_result.v2",
                                      "media_probe": (probe.get("tool_versions") or {})},
        request={"request_key": request_key},
        attempt={"attempt_id": attempt.name})
    decision = rd.decide(report, {"request_key": request_key})
    eio.append_event(attempt, "video_check", report=_report_to_dict(report),
                     checks=list(qc.checks or []), qc_status=qc.status)
    eio.append_event(attempt, "decision", decision=decision.to_dict())
    if decision.kind == rd.DECISION_ACCEPT and qc.status != "measured_pass":
        # 语义仍 unknown ⇒ 只能是 unverified（裁决已按 unknown 处理，这里再次核对）
        decision = rd.Decision(rd.DECISION_UNVERIFIED,
                               reasons=["语义轴未验证，不得声明内容 PASS"])
    # 裁决 → 生命周期终局（§V2 表：QC 内容问题=rejected 且**保留产物**；
    # 工具不可用/证据不足=unverified；只有 accept 才进 checked→accepted）
    if decision.kind == rd.DECISION_ACCEPT:
        target = va.STATE_CHECKED
    elif decision.kind in (rd.DECISION_REVISE_INPUTS, rd.DECISION_POSTPROCESS,
                           rd.DECISION_STOP):
        target = va.STATE_REJECTED
    else:
        target = va.STATE_UNVERIFIED
    va.transition(attempt, target, decision=decision.kind, issue_ids=decision.issue_ids)
    return {"kind": decision.kind, "coverage": report.coverage,
            "media_status": report.media_status, "semantic_status": report.semantic_status,
            "checks": list(qc.checks or []),
            "media_probe": probe or None,
            "checks_report": [{"name": "media", "status": report.media_status},
                              {"name": "semantic", "status": report.semantic_status}],
            "decision": decision.to_dict()}

def accept_candidate_video(candidate_context, *, video_path: Path, frozen_request,
                           checks: list, clip_copy: Path | None = None,
                           coverage: dict | None = None,
                           tool_versions: dict | None = None,
                           accepted_root: Path | None = None) -> dict:
    """候选合格：不可变 manifest → 锁内换 accepted → **读回完整关系**（§V2/§F3）。

    只在候选上下文内工作；生产 `clips/` 一个字节都不动。返回 accepted 关系；
    提交后读回失败 ⇒ 抛 ``CommitUnconfirmedError``（退出码 6），不声称旧值仍在。

    ``accepted_root`` 默认 = attempt 目录：视频包**一个 task 一个 attempt**，
    指针落在 attempt 内才不会多镜互相覆盖（attempts/ 仍在包 root 下，见 §F2）。
    """
    from shared import enhancement_io as eio

    attempt = Path(getattr(candidate_context, "attempt_dir"))
    ctx_scope = dict(getattr(candidate_context, "scope", None) or {})
    ctx_revision = str(getattr(candidate_context, "script_revision", "") or "")
    ctx_source = str(getattr(candidate_context, "source", "") or "")
    missing = [name for name, value in (("scope", ctx_scope),
                                        ("script_revision", ctx_revision),
                                        ("source", ctx_source)) if not value]
    if missing:
        raise eio.EnhancementError(
            "candidate_identity_missing", "accept",
            f"候选上下文缺少来源身份 {missing}：采纳 manifest 必须能回答"
            "「哪一章、哪一稿、谁产生的」——缺一律拒收，不写空身份、不用 fixture 兜底",
            exit_code=eio.EXIT_REJECTED, attempt_id=attempt.name, field_path="source")

    # 消费方按**稳定编号**取素材（`resolve_candidate_inputs` 不按数组位置猜身份）：
    # 没有 shot_index/shot_id 的 manifest 会让下游直接 `unit_locator_missing`。
    frozen_dict = frozen_request.to_dict() if frozen_request is not None else {}
    fz_source = dict(frozen_dict.get("source") or {})
    shot_index = fz_source.get("shot_index")
    shot_id = fz_source.get("shot_id")
    if shot_index is None and not shot_id:
        raise eio.EnhancementError(
            "candidate_unit_locator_missing", "accept",
            "冻结请求没有 shot_index/shot_id：无法给出稳定的单元编号，拒绝产出"
            "下游读不出身份的候选 manifest",
            exit_code=eio.EXIT_REJECTED, attempt_id=attempt.name, field_path="unit_id")
    dur = (frozen_dict.get("parameters") or {}).get("duration")
    locator = str(shot_id) if shot_id else "shots[%s]" % shot_index
    video_sha = eio.sha256_file(Path(video_path))
    units = [{
        "unit_id": f"video-{locator}",
        "kind": "video",
        "locator": locator,
        "shot_index": shot_index,
        "shot_id": shot_id,
        "path": str(Path(video_path).resolve()),
        "sha256": video_sha,
        "window": ({"start_sec": 0.0, "end_sec": float(dur)} if dur else {}),
        "media": ({"duration_sec": float(dur)} if dur else {}),
    }]

    pointer_root = Path(accepted_root) if accepted_root is not None else attempt
    artifacts = [eio.artifact_record(Path(video_path), role="candidate_video")]
    if clip_copy and Path(clip_copy).is_file():
        artifacts.append(eio.artifact_record(Path(clip_copy), role="candidate_clip_copy"))
    inputs = attempt / eio.INPUT_FILENAME
    if inputs.is_file():
        artifacts.append(eio.artifact_record(inputs, role="frozen_request"))
    input_key = str(getattr(frozen_request, "request_key", "") or "")
    manifest = eio.seal_manifest(attempt, {
        "package": "video",
        "source": ctx_source,
        "scope": ctx_scope,
        "script_revision": ctx_revision,
        "request_key": input_key,
        "units": units,
        "request": frozen_dict,
        "status": "validated",
        "tool_versions": dict(tool_versions or {"qc": "step02._qc_video_product_result.v1"}),
        "artifacts": artifacts,
        "checks": list(checks),
        "coverage": dict(coverage or {}),
        "errors": [],
    })
    accepted = eio.commit_accepted(
        pointer_root, attempt_id=attempt.name,
        manifest_path=attempt / eio.MANIFEST_FILENAME,
        manifest_sha256=manifest["manifest_sha256"], input_key=input_key)
    return {"pointer": accepted, "manifest": manifest,
            "accepted_path": str(eio.accepted_path(pointer_root))}
