# -*- coding: utf-8 -*-
"""tools.inspect_video - B 包 CLI：连续时间技术检查与切点定位（计划 §B1/§B3）.

```text
python tools/inspect_video.py --input FILE --spec FILE --out-dir DIR
python tools/inspect_video.py plan --input FILE --source-manifest FILE --out-dir DIR
```

- ``plan``：**只读产出 spec**，不执行任何检查、不拉工具进程；
- 默认（执行检查）：读 spec → 核输入身份 → ffmpeg 检测 + cuts 隔离 worker →
  ``media_inspection_report.v1``。

只报告：不接生产拒收、不触发重生成、不修改输入媒体与正式产物。
报告按 §F2 协议落盘（attempt + events + 不可变 manifest）；``report_status=complete``
时才移动 ``accepted.json`` 指针——**旧 complete 结果不会被 partial/unverified 覆盖**。

退出码（§F3）：0 报告完整；2 输入/身份非法；3 工具失败或不可用（报告仍写出）；
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
from shared.enhancement_io import (  # noqa: E402
    EXIT_INVALID_INPUT,
    EXIT_OK,
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

PACKAGE = "inspect"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspect_video",
        description="连续时间技术检查（黑场/静止/静音/切点）——只报告，不改片")
    parser.add_argument("command", nargs="?", choices=("plan", "inspect"), default="inspect")
    parser.add_argument("--input", required=True, help="输入媒体（shot 或 final）")
    parser.add_argument("--spec", help="media_inspection.v1 spec（inspect 必给）")
    parser.add_argument("--source-manifest", help="来源 manifest（plan 必给）")
    parser.add_argument("--out-dir", help="包 root（省略时由 --experiment-id 推导）")
    parser.add_argument("--experiment-id", help="实验 id（推导 out-dir；同时用于隔离工作区）")
    parser.add_argument("--chapter", type=int, default=None, help="章节序号（配合 --experiment-id）")
    parser.add_argument("--attempt-id", default=None, help="显式 attempt id（默认自动生成）")
    parser.add_argument("--input-kind", choices=list(mi.INPUT_KINDS), default=None,
                        help="plan 用：覆盖 manifest 声明的输入类型")
    parser.add_argument("--checks", default=None,
                        help=f"plan 用：逗号分隔的检查项（合法: {','.join(mi.ALL_CHECKS)}）")
    parser.add_argument("--no-evidence-frames", action="store_true",
                        help="不抽取事件帧证据（默认抽，用于人工复核）")
    parser.add_argument("--max-evidence-frames", type=int, default=mi.DEFAULT_MAX_EVIDENCE_FRAMES)
    parser.add_argument("--timeout", type=float, default=mi.DEFAULT_TIMEOUT_SEC,
                        help="单项 ffmpeg 检测超时（秒）")
    parser.add_argument("--cuts-timeout", type=float, default=mi.DEFAULT_CUTS_TIMEOUT_SEC,
                        help="cuts（PySceneDetect 隔离 worker）超时（秒）")
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


def cmd_plan(args) -> int:
    out_dir = _resolve_out_dir(args)
    if not args.source_manifest:
        raise EnhancementError("source_manifest_missing", "cli", "plan 需要 --source-manifest",
                               exit_code=EXIT_INVALID_INPUT, field_path="source_manifest")
    checks = None
    if args.checks:
        checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    spec = mi.plan_inspection(args.input, args.source_manifest, out_dir,
                              input_kind=args.input_kind, checks=checks)
    _emit({"ok": True, "command": "plan", "spec_path": spec.get("spec_path"),
           "input_sha256": spec.get("input_sha256"), "input_kind": spec.get("input_kind"),
           "checks": spec.get("checks"), "allowed_interval_count": len(spec["allowed_intervals"]),
           "checks_executed": 0})
    return EXIT_OK


def cmd_inspect(args) -> int:
    if not args.spec:
        raise EnhancementError("spec_missing", "cli", "执行检查需要 --spec",
                               exit_code=EXIT_INVALID_INPUT, field_path="spec")
    out_dir = _resolve_out_dir(args)
    experiment_id = args.experiment_id
    attempt_id = assert_attempt_id(args.attempt_id or _new_attempt_id())
    if experiment_id:
        attempt = attempt_dir(PACKAGE, experiment_id, attempt_id, args.chapter)
    else:
        attempt = out_dir / "attempts" / attempt_id
    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        raise EnhancementError("input_missing", "cli", f"输入媒体不存在: {input_path}",
                               exit_code=EXIT_INVALID_INPUT, field_path="input")

    spec = mi.load_spec(args.spec)
    lock = AttemptLock(attempt / ".lock", purpose=f"{PACKAGE}:{attempt_id}")
    with lock:
        attempt.mkdir(parents=True, exist_ok=True)
        freeze_inputs(attempt, {
            "package": PACKAGE,
            "attempt_id": attempt_id,
            "input": {"path": str(input_path), "sha256": sha256_file(input_path)},
            "spec": {"path": str(Path(args.spec).resolve()),
                     "sha256": sha256_file(Path(args.spec))},
            "spec_content": spec,
        })
        append_event(attempt, "inspect_started", before={"state": "draft"},
                     after={"state": "running"}, input_path=str(input_path))

        report = mi.inspect(spec, input_path=input_path, work_dir=attempt,
                            evidence_frames=not args.no_evidence_frames,
                            max_evidence_frames=args.max_evidence_frames,
                            timeout_sec=args.timeout, cuts_timeout_sec=args.cuts_timeout)

        outputs = attempt / "outputs"
        assert_candidate_path(outputs / "media_inspection_report.json",
                              experiment_root=out_dir, inputs=(input_path, Path(args.spec)),
                              formal=())
        report_path = outputs / "media_inspection_report.json"
        mi.write_json(report_path, report)
        artifacts = [artifact_record(report_path, role="report")]

        evidence = _decode_evidence_from(report)
        if evidence is not None:
            evidence_path = outputs / "media_decode_evidence.json"
            mi.write_json(evidence_path, evidence)
            artifacts.append(artifact_record(evidence_path, role="decode_evidence"))

        for event in report.get("events") or []:
            for frame in event.get("evidence_frames") or []:
                if frame.get("status") == "completed" and Path(frame["path"]).is_file():
                    artifacts.append(artifact_record(Path(frame["path"]), role="evidence_frame"))

        checks_manifest = [{"name": c.get("name"), "status": c.get("status"),
                            "error": c.get("error")} for c in report.get("checks") or []]
        checks_manifest.append({
            "name": "media_inspection_report",
            "status": "pass" if report["report_status"] == mi.REPORT_COMPLETE else "unknown",
            "error": None,
        })
        manifest = seal_manifest(attempt, {
            "package": PACKAGE,
            "status": report["report_status"],
            "input_key": _input_key(report),
            "source": report["source"],
            "report_status": report["report_status"],
            "artifacts": artifacts,
            "checks": checks_manifest,
            "tool_versions": report.get("tool_versions"),
            "parameters": report.get("parameters"),
            "coverage": {"checks": len(checks_manifest) - 1,
                         "events": len(report.get("events") or []),
                         "suspicious": sum(1 for e in report.get("events") or []
                                           if e.get("classification") == mi.CLASS_SUSPICIOUS)},
        })
        append_event(attempt, "report_sealed", before={"state": "running"},
                     after={"state": "checked"}, report_status=report["report_status"],
                     manifest_sha256=manifest.get("manifest_sha256"))

        committed = False
        if report["report_status"] == mi.REPORT_COMPLETE:
            commit_accepted(out_dir, attempt_id=attempt_id,
                            manifest_path=attempt / "manifest.json",
                            manifest_sha256=manifest["manifest_sha256"],
                            input_key=manifest["input_key"])
            committed = True
            append_event(attempt, "accepted", before={"state": "checked"},
                         after={"state": "accepted"})
        else:
            append_event(attempt, "not_accepted", before={"state": "checked"},
                         after={"state": report["report_status"]},
                         reason="report_status != complete：保留旧 accepted，不覆盖成 PASS")

    _emit({
        "ok": report["report_status"] == mi.REPORT_COMPLETE,
        "command": "inspect",
        "attempt_id": attempt_id,
        "attempt_dir": str(attempt),
        "report_path": str(report_path),
        "report_status": report["report_status"],
        "exit_code": mi.exit_code_for(report),
        "checks": [{"name": c.get("name"), "status": c.get("status"),
                    "error": (c.get("error") or {}).get("code"),
                    "events": c.get("event_count"), "problems": c.get("problem_count")}
                   for c in report.get("checks") or []],
        "event_classification": _count_by(report.get("events") or [], "classification"),
        "event_kinds": _count_by(report.get("events") or [], "kind"),
        "accepted_committed": committed,
    })
    return mi.exit_code_for(report)


def _input_key(report: dict) -> str:
    from shared.enhancement_io import canonical_json_digest
    return canonical_json_digest({
        "input_sha256": report["source"].get("input_sha256"),
        "source_identity": report["source"].get("source_identity"),
        "source_revision": report["source"].get("source_revision"),
        "input_kind": report["source"].get("input_kind"),
    })


def _decode_evidence_from(report: dict):
    """把本次独立 decode 的结果写成 ``media_decode_evidence.v1``（供 S/后续消费者复用）.

    形状**与 S 包 ``build_decode_evidence`` 逐字一致**：恰好六字段、``status="decoded"``、
    ``ffmpeg_build`` 用短版本号（``ffmpeg -version`` 首行第 3 段）——两边产出的证据可互换。
    """
    for check in report.get("checks") or []:
        if check.get("name") != mi.CHECK_DECODE:
            continue
        if check.get("status") != mi.STATUS_COMPLETED or check.get("evidence_source") != "independent":
            return None
        tokens = mi.ffmpeg_build_tokens({"version_line": check.get("ffmpeg_build")})
        return {
            "input_hash": report["source"].get("input_sha256"),
            "ffmpeg_build": (tokens[-1] if len(tokens) > 1 else (tokens[0] if tokens else None)),
            "argv": check.get("argv"),
            "returncode": check.get("returncode"),
            "stderr_digest": check.get("stderr_digest"),
            "status": "decoded",
        }
    return None


def _count_by(events: list, key: str) -> dict:
    out: dict = {}
    for event in events:
        value = str(event.get(key))
        out[value] = out.get(value, 0) + 1
    return out


def _emit(payload: dict) -> None:
    # ASCII-safe：控制台可能是 GBK，JSON 里的中文用 \uXXXX 转义（机器可读优先）
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            return cmd_plan(args)
        return cmd_inspect(args)
    except EnhancementError as exc:
        payload = exc.payload()
        sys.stderr.write(json.dumps(payload, ensure_ascii=True) + "\n")
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
