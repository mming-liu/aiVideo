"""Offline explicit candidate replay. No audit, submission or model generation.

Manifest input paths are relative to the manifest, and each file has a raw
SHA-256. Edit specs use canonical object hashes, as documented in candidate_edit.
Outputs must use a new isolated directory; historical results are never replaced.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONFIG_KEYS = {"NOVEL_NAME", "BOOK_INDEX", "BOOK_NAME", "NOVEL_CHAPTER",
               "VOICE_ADAPTATION_ENABLED", "DURATION_MAX_SEC", "DURATION_MIN_SEC",
               "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC",
               "STEP01_PRESENTATION_MODE",
               # 2026-10-28（M07）：钩子是否属于这一版交付必须**显式声明**——
               # 生效值决定提交入口会不会丢掉 4 s 钩子并重排时间码；不声明就只能
               # 继承机器默认值，报告与实际落盘对象会静默分叉。
               "HOOK_ENABLED"}


def read_input(manifest_path, item):
    path = (manifest_path.parent / item["path"]).resolve()
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != item["sha256"]:
        raise ValueError(f"input_digest_mismatch: {path.name}")
    return raw.decode("utf-8-sig"), actual


def replay(manifest_path: Path, work_root: Path) -> dict:
    from tools.probe_isolation import isolated_probe
    from step01_script_gen.candidate_edit import (
        build_candidate, finalize_candidate, object_digest, reading_markdown)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    config = manifest.get("config", {})
    if set(config) - CONFIG_KEYS or any(not isinstance(v, str) for v in config.values()):
        raise ValueError("invalid_config: unsupported key or non-string value")
    loaded, digests = {}, {}
    for name in ("plan", "source", "novel", "edit_spec"):
        text, digests[name] = read_input(manifest_path, manifest["inputs"][name])
        loaded[name] = text if name == "novel" else json.loads(text)
    candidate, problems = build_candidate(loaded["plan"], loaded["source"], loaded["edit_spec"])
    if problems:
        raise ValueError(json.dumps(problems, ensure_ascii=False))
    # New outputs only, including logs and evidence. No force/overwrite option.
    work_root.mkdir(parents=True, exist_ok=False)
    log = io.StringIO()
    saved = {k: os.environ.get(k) for k in config}
    report = None
    try:
        with isolated_probe(label="candidate-delivery", root=work_root / "runtime") as probe:
            os.environ.update(config)
            with contextlib.redirect_stdout(log):
                from step01_script_gen import main as m
                from step01_script_gen import plan_feasibility as pf
                from shared.quality_gate import compute_script_revision
                pf.bind_plan_identity(candidate, loaded["plan"])
                expected = [(s.get("narration_segment"), s.get("subtitle")) for s in candidate["shots"]]
                final = finalize_candidate(
                    candidate,
                    lambda s: m.finalize_step01_script(s, novel_text=loaded["novel"], label="delivery"),
                    m._strip_runtime_fields)
                actual = [(s.get("narration_segment"), s.get("subtitle")) for s in final["shots"]]
                if actual != expected:
                    raise ValueError("broadcast_drift: finalizer changed explicit broadcast text")
                # 报告必须描述**盘上那份**候选：先按发布格式序列化、写盘、再读回，
                # 之后所有派生（提交投影 revision、质量门、契约）都吃读回的对象。
                # （内存对象与 JSON 往返未必逐字相同；用内存对象算投影会得到提交入口
                # 永远不会产生的 revision——2026-10-28 实测 44ef1c5a vs 8c7ab915。）
                published_text = json.dumps(final, ensure_ascii=False, indent=2)
                (work_root / "candidate.json").write_text(published_text, encoding="utf-8")
                published = json.loads(published_text)
                # **不在此进程预测提交 revision**（2026-10-28 实测）：同一个
                # `project_for_commit` 在"跑过两次 finalize 的进程"里给出 44ef1c5a…，
                # 而新进程对同一份文件给出 8c7ab915…——finalize 的输出依赖本进程此前
                # 调用过的输入（非纯函数，见 M09 待查）。唯一的权威是提交入口自己
                # 返回的 `summary["revision"]`，审计就在那一次调用里对同一对象进行。
                projection_revision = ""
                projection_errors = []
                hook_included = None
                quality = m._build_step01_quality_gate(published, loaded["novel"])
                ok, violations, declared = pf.check_event_contract(published, loaded["plan"], require_declaration=True)
                dialogue_ok, broken = pf.check_script_dialogue_integrity(published, loaded["plan"])
                revision = compute_script_revision(published)
            probe.assert_isolated()
            report = {
                "script_revision": revision, "input_sha256": digests,
                "config": config, "edit_object_sha256": object_digest(loaded["edit_spec"]),
                "finalizer_stable": True, "broadcast_drift": False,
                "contract_ok": ok, "contract_violations": violations, "declared": declared,
                "dialogue_ok": dialogue_ok, "dialogue_broken": broken,
                "quality_status": quality.get("status"), "blocking": quality.get("blocking"),
                "status": ("BLOCK" if (not ok or not dialogue_ok or quality.get("blocking")
                                        or projection_errors) else "OFFLINE_READY"),
                # 提交投影不由本工具预测（见上面的说明）；这里只记录配置事实。
                "commit_projection_revision": projection_revision,
                "commit_projection_errors": list(projection_errors),
                "commit_projection_computed_by": "commit_entry",
                "hook_enabled_in_effect": bool(getattr(m, "HOOK_ENABLED", False)),
                "shot_count": len(final["shots"]),
                "main_duration_sec": sum(float(s["duration_sec"]) for s in final["shots"]),
                "semantic_review": "not_verified", "voice_ownership": "not_verified",
                "model_calls": 0, "audit": "not_run", "commit": "not_run",
                "protected_paths": probe.protected,
            }
            (work_root / "quality.json").write_text(
                json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
            (work_root / "storyboard.md").write_text(
                reading_markdown(published, revision=revision), encoding="utf-8")
        # Only a successful context exit proves production protection.
        report["production_unchanged"] = True
        (work_root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        (work_root / "rejected.json").write_text(json.dumps({
            "status": "REJECTED", "error": str(exc), "input_sha256": digests,
            "audit": "not_run", "commit": "not_run",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        (work_root / "replay.log").write_text(log.getvalue(), encoding="utf-8")
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = replay(args.manifest.resolve(), args.work_root.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps({k: report[k] for k in ("status", "shot_count", "main_duration_sec",
                                           "contract_ok", "finalizer_stable", "production_unchanged")}, ensure_ascii=False))
    return 0 if report["status"] == "OFFLINE_READY" else 4


if __name__ == "__main__":
    raise SystemExit(main())
