# -*- coding: utf-8 -*-
"""tools.align_subtitles - C 包 CLI：字幕句界候选（计划 §C1/§C2/§C3/§15）.

```text
python tools/align_subtitles.py --input FILE --audio-candidates FILE --alignment FILE --out-dir DIR
python tools/align_subtitles.py --input FILE --audio-candidates FILE --backend whisperx \
       --model-dir DIR --out-dir DIR
```

- ``--alignment``：消费外部已得的合法候选（本工具只校验/采纳，不重新算时间）；
- ``--backend whisperx``：可选真实分支，走 §F5 的 Python 3.12 子进程 worker，
  **禁止自动下载**、无权重/无环境返回 ``unavailable``（退出码 3）；
- 两条路都**先核富身份**（候选 manifest + ``<audio>.identity.json`` 的
  ``audio_identity.v2``）与 scope/revision，失败即拒收（退出码 4），
  不靠 ``voice_signature`` 反推。

退出码（§F3）：0 候选可采纳；2 输入非法；3 工具/后端不可用；4 明确拒收；
5 锁忙；6 提交后未确认。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import media_inspect as mi  # noqa: E402
from shared import subtitle_alignment as sa  # noqa: E402
from shared.enhancement_io import (  # noqa: E402
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_REJECTED,
    EXIT_TOOL_FAIL,
    EnhancementError,
    AttemptLock,
    append_event,
    artifact_record,
    assert_attempt_id,
    assert_candidate_path,
    attempt_dir,
    commit_accepted,
    freeze_inputs,
    seal_manifest,
    sha256_file,
)
from shared.paths import enhancement_package_dir  # noqa: E402

PACKAGE = "alignment"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="align_subtitles",
        description="已知文本的字幕句界候选（不 ASR、不重写文字、不自动下载权重）")
    parser.add_argument("--input", required=True, help="subtitle_alignment_request.v1")
    parser.add_argument("--audio-candidates", required=True, help="音频候选 manifest（富身份入口）")
    parser.add_argument("--alignment", help="外部已得的 subtitle_alignment.v1 候选")
    parser.add_argument("--backend", default=None, choices=("whisperx",),
                        help="后端（缺省=只消费 --alignment）")
    parser.add_argument("--model-dir", help="本地 WhisperX 权重目录（禁止自动下载）")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--out-dir", help="包 root（省略时由 --experiment-id 推导）")
    parser.add_argument("--experiment-id", help="实验 id")
    parser.add_argument("--chapter", type=int, default=None)
    parser.add_argument("--attempt-id", default=None)
    parser.add_argument("--timeout", type=float, default=1800.0, help="worker 超时（秒）")
    return parser


def _resolve_out_dir(args) -> Path:
    if args.out_dir:
        return Path(args.out_dir).resolve()
    if args.experiment_id:
        return enhancement_package_dir(PACKAGE, args.experiment_id, args.chapter).resolve()
    raise EnhancementError("out_dir_missing", "cli",
                           "必须给 --out-dir，或给 --experiment-id 让它推导包 root",
                           exit_code=EXIT_INVALID_INPUT, field_path="out_dir")


def _new_attempt_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{secrets.token_hex(3)}"


def _audio_duration(audio_path: Path) -> float:
    probe = mi.probe_media(audio_path)
    if probe.get("status") != "completed" or probe.get("duration_sec") is None:
        raise EnhancementError(
            "audio_probe_unavailable", "probe",
            "ffprobe 拿不到音轨时长 ⇒ 无法证明对齐时间落在音轨内（不跳过该判据）",
            exit_code=EXIT_TOOL_FAIL, field_path="audio_path",
            residual_issues=[(probe.get("error") or {"code": "probe_incomplete"})])
    return float(probe["duration_sec"])


def _run_backend(request: dict, args, work_dir: Path) -> tuple:
    """跑 whisperx worker 并把结果转成 ``subtitle_alignment.v1``；不可用即抛（exit 3）."""
    if not args.model_dir:
        raise EnhancementError("model_dir_missing", "backend",
                               "whisperx 分支需要 --model-dir（本包禁止自动下载权重）",
                               exit_code=EXIT_TOOL_FAIL, field_path="model_dir")
    try:
        from tools.enhancement_workers import protocol as worker_protocol
    except Exception as exc:  # noqa: BLE001
        raise EnhancementError("worker_infra_missing", "backend",
                               f"worker 协议模块不可用: {exc}", exit_code=EXIT_TOOL_FAIL) from exc
    payload = {"audio_path": str(Path(request["audio_path"]).resolve()),
               "model_dir": str(Path(args.model_dir).resolve()),
               "spoken_text": request["spoken_text"],
               "language": args.language}
    outcome = worker_protocol.run_worker("whisperx_align", payload,
                                         work_dir=work_dir / "worker",
                                         timeout_sec=float(args.timeout))
    if outcome.status != worker_protocol.HOST_OK:
        err = outcome.error or {}
        raise EnhancementError(
            err.get("code") or "backend_failed", "backend",
            err.get("message") or "whisperx worker 未产出可用结果",
            exit_code=EXIT_TOOL_FAIL,
            residual_issues=[{"worker_status": outcome.status,
                              "tool_exit_code": outcome.tool_exit_code,
                              "runtime": outcome.runtime,
                              "stderr_tail": outcome.stderr_tail}])
    result = outcome.result or {}
    alignment, coverage = sa.build_alignment(
        request, result.get("units") or [],
        backend=result.get("backend") or "whisperx",
        backend_version=result.get("backend_version"),
        model_revision=result.get("model_revision"),
        diagnostics={**(result.get("diagnostics") or {}),
                     "worker": {"runtime": outcome.runtime,
                                "tool_exit_code": outcome.tool_exit_code,
                                "request_sha256": outcome.request_sha256,
                                "duration_sec": outcome.duration_sec}})
    return alignment, coverage


def _run(args) -> int:
    if bool(args.alignment) == bool(args.backend):
        raise EnhancementError("backend_conflict", "cli",
                               "必须**二选一**：--alignment（消费外部候选）或 --backend whisperx",
                               exit_code=EXIT_INVALID_INPUT, field_path="backend")
    request = sa.load_request(args.input)
    out_dir = _resolve_out_dir(args)
    attempt_id = assert_attempt_id(args.attempt_id or _new_attempt_id())
    attempt = (attempt_dir(PACKAGE, args.experiment_id, attempt_id, args.chapter)
               if args.experiment_id else out_dir / "attempts" / attempt_id)
    audio_path = Path(request["audio_path"]).resolve()
    request["audio_path"] = str(audio_path)

    lock = AttemptLock(attempt / ".lock", purpose=f"{PACKAGE}:{attempt_id}")
    with lock:
        attempt.mkdir(parents=True, exist_ok=True)
        freeze_inputs(attempt, {
            "package": PACKAGE,
            "attempt_id": attempt_id,
            "request": {"path": str(Path(args.input).resolve()),
                        "sha256": sha256_file(Path(args.input))},
            "request_content": request,
            "audio_candidates": {"path": str(Path(args.audio_candidates).resolve()),
                                 "sha256": sha256_file(Path(args.audio_candidates))},
        })
        append_event(attempt, "align_started", before={"state": "draft"},
                     after={"state": "running"}, unit_id=request.get("unit_id"),
                     backend=args.backend or "external")

        # 1) 富身份先行（§15）：身份不过就不进入对齐（拒收，退出码 4）
        identity = sa.identity_gate(request, audio_candidates_path=args.audio_candidates)
        append_event(attempt, "identity_verified", before={"state": "running"},
                     after={"state": "running"}, rich_identity=identity.get("rich_identity"))

        # 2) 音轨时长（时间边界的唯一依据；拿不到即工具失败，不跳过判据）
        duration = _audio_duration(audio_path)

        # 3) 候选来源
        if args.alignment:
            try:
                alignment = json.loads(Path(args.alignment).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise EnhancementError("alignment_unparsable", "alignment",
                                       f"读不到/解析不了候选: {exc}",
                                       exit_code=EXIT_INVALID_INPUT,
                                       field_path="alignment") from exc
            coverage = sa.compute_coverage(request, alignment)
        else:
            alignment, coverage = _run_backend(request, args, attempt)
        problems = sa.validate_alignment(request, alignment, audio_duration_sec=duration)
        exit_code = _finish(args, attempt, out_dir, request, alignment, coverage, identity,
                            duration, problems)
    return exit_code


def _finish(args, attempt: Path, out_dir: Path, request: dict, alignment: dict,
            coverage: dict, identity: dict, duration: float, problems: list) -> int:
    summary = {
        "schema_version": "subtitle_alignment_report.v1",
        "request_sha256": sa.request_sha256(request),
        "unit_id": request.get("unit_id"),
        "shot_index": request.get("shot_index"),
        "script_revision": request.get("script_revision"),
        "audio": {"path": str(Path(request["audio_path"]).resolve()),
                  "sha256": request.get("audio_sha256"), "duration_sec": duration},
        "identity": identity,
        "backend": alignment.get("backend") if isinstance(alignment, dict) else None,
        "problems": problems,
        "coverage": {"adoptable": [s["segment_id"] for s in coverage["adoptable_segments"]],
                     "fallback": [{"segment_id": s["segment_id"], "reason": s["reason"],
                                   "uncovered_chars": s["uncovered_chars"],
                                   "problems": s["problems"]}
                                  for s in coverage["fallback_segments"]],
                     "segments": coverage["segments"],
                     "uncovered_ranges": coverage["uncovered_ranges"]},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    outputs = attempt / "outputs"
    assert_candidate_path(outputs / "subtitle_alignment.json", experiment_root=out_dir,
                          inputs=(Path(request["audio_path"]), Path(args.input)))
    artifacts = []
    timings_path = None
    if not problems:
        alignment_path = outputs / "subtitle_alignment.json"
        mi.write_json(alignment_path, alignment)
        artifacts.append(artifact_record(alignment_path, role="alignment"))
        timings_path = outputs / "validated_timings.json"
        mi.write_json(timings_path, {
            "schema_version": "subtitle_validated_timings.v1",
            "shot_index": request.get("shot_index"),
            "script_revision": request.get("script_revision"),
            "unit_id": request.get("unit_id"),
            "source_audio": {"path": request["audio_path"], "sha256": request.get("audio_sha256")},
            "time_base": "shot_local_seconds",
            "timings": sa.to_validated_timings(coverage),
            "fallback_segments": [s["segment_id"] for s in coverage["fallback_segments"]],
        })
        artifacts.append(artifact_record(timings_path, role="validated_timings"))
    else:
        problems_path = outputs / "alignment_problems.json"
        mi.write_json(problems_path, {"problems": problems, "alignment": alignment})
        artifacts.append(artifact_record(problems_path, role="rejected_alignment"))
    report_path = outputs / "subtitle_alignment_report.json"
    mi.write_json(report_path, summary)
    artifacts.append(artifact_record(report_path, role="report"))

    all_adoptable = bool(coverage["segments"]) and not coverage["fallback_segments"]
    checks = [
        {"name": "rich_identity", "status": "pass"},
        {"name": "alignment_valid", "status": "pass" if not problems else "failed"},
        {"name": "segment_coverage", "status": "pass" if all_adoptable else "partial"},
        {"name": "backend_available", "status": "pass"},
    ]
    manifest = seal_manifest(attempt, {
        "package": PACKAGE,
        "status": "accepted" if not problems else "rejected",
        "input_key": sa.request_sha256(request),
        "source": {"script_revision": request.get("script_revision"),
                   "shot_index": request.get("shot_index"),
                   "unit_id": request.get("unit_id"),
                   "audio_sha256": request.get("audio_sha256")},
        "artifacts": artifacts,
        "checks": checks,
        "coverage": {"adoptable_segments": len(coverage["adoptable_segments"]),
                     "fallback_segments": len(coverage["fallback_segments"]),
                     "uncovered_ranges": len(coverage["uncovered_ranges"])},
        "tool_versions": {"host_python": mi.python_version()},
    })
    append_event(attempt, "alignment_sealed", before={"state": "running"},
                 after={"state": "checked" if not problems else "rejected"},
                 problems=len(problems), manifest_sha256=manifest.get("manifest_sha256"))

    committed = False
    exit_code = EXIT_REJECTED if problems else EXIT_OK
    if not problems:
        commit_accepted(out_dir, attempt_id=attempt.name,
                        manifest_path=attempt / "manifest.json",
                        manifest_sha256=manifest["manifest_sha256"],
                        input_key=manifest["input_key"])
        committed = True
        append_event(attempt, "accepted", before={"state": "checked"}, after={"state": "accepted"})
    else:
        append_event(attempt, "not_accepted", before={"state": "checked"},
                     after={"state": "rejected"},
                     reason="候选校验未通过：不移动 accepted 指针")
    sys.stdout.write(json.dumps({
        "ok": not problems,
        "attempt_id": attempt.name,
        "attempt_dir": str(attempt),
        "exit_code": exit_code,
        "adoptable_segments": [s["segment_id"] for s in coverage["adoptable_segments"]],
        "fallback_segments": [{"segment_id": s["segment_id"], "reason": s["reason"]}
                              for s in coverage["fallback_segments"]],
        "problem_codes": sorted({p["code"] for p in problems}),
        "accepted_committed": committed,
        "validated_timings_path": (str(timings_path) if timings_path else None),
    }, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return exit_code


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except EnhancementError as exc:
        sys.stderr.write(json.dumps(exc.payload(), ensure_ascii=True) + "\n")
        return int(exc.exit_code)
    except KeyboardInterrupt:  # pragma: no cover
        return EXIT_TOOL_FAIL
    except Exception as exc:  # noqa: BLE001 - 不静默：结构化错误 + 原始 traceback 都留
        import traceback
        traceback.print_exc()
        sys.stderr.write(json.dumps({
            "code": "unhandled_exception", "stage": "cli",
            "message": f"{type(exc).__name__}: {exc}", "package": PACKAGE,
            "residual_issues": [], "replacement_performed": False, "confirmed": False,
        }, ensure_ascii=True) + "\n")
        return EXIT_TOOL_FAIL


if __name__ == "__main__":
    sys.exit(main())
