#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/verify_enhancement_transaction.py - F 阶段退出的**唯一**事务验收入口（计划 §F退出/§15）.

    python tools/verify_enhancement_transaction.py --package video --fixture DIR --out-dir ROOT/video
    python tools/verify_enhancement_transaction.py --package tts   --fixture DIR --out-dir ROOT/tts
    python tools/verify_enhancement_transaction.py --package merge --fixture DIR --out-dir ROOT/merge

对三类**有效协议 fixture** 走**同一个事务入口**（``shared.enhancement_io``）完成
``input → manifest → accepted → 完整读回``，随后覆盖故障点：

* mkdir（目录准备失败不启动外部生成）；
* write（manifest 目标被占位 ⇒ 写入失败且不产生半成品）；
* replace + confirm（替换后读回不一致 ⇒ ``commit_unconfirmed``，退出码 6，
  既不声称旧值仍在、也不盲目回滚）；
* 并发 writer（第二个 writer 拿不到锁 ⇒ ``lock_busy``，退出码 5；确认 owner 退出后
  可恢复）；
* 恢复（lifecycle 投影落后按合法事件补投影；投影领先/中间断链拒绝自动续跑；
  尾部不完整事件保留原始证据并停在最后确认点）。

纪律：所有故障都在**临时副本**上注入（``tempfile.mkdtemp`` + 调用方给的 out-dir），
不回退共享脏树；只读源 fixture；`video` 包的 ``lifecycle.json`` **不进入**产物
manifest 投影（删掉 lifecycle 后 accepted 读回仍成立即为证据：事务层读的是
指针→manifest→产物→check 这条链，而不是任务状态投影）。

这些是**事务 fixture**：只验证事务入口，不宣称生成器/声音/合成已实跑
（生成/声音/合成贯通分别在 V/T/S/I 验收）。退出码用 §F3 那套。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio  # noqa: E402

PACKAGES = ("video", "tts", "merge")
SCHEMA_FIXTURE = "enhancement_transaction_fixture.v1"

EXIT_OK = eio.EXIT_OK
EXIT_INVALID_INPUT = eio.EXIT_INVALID_INPUT
EXIT_TOOL_FAIL = eio.EXIT_TOOL_FAIL
EXIT_REJECTED = eio.EXIT_REJECTED
EXIT_BUSY = eio.EXIT_BUSY
EXIT_COMMIT_UNCONFIRMED = eio.EXIT_COMMIT_UNCONFIRMED


def _read_json(path: Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise eio.EnhancementError("fixture_missing", "input", f"fixture 文件不存在: {p}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="--fixture")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise eio.EnhancementError("fixture_invalid", "input", f"{p} JSON 解析失败: {exc}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="--fixture") from exc


def load_fixture(fixture_dir: Path, package: str) -> dict:
    """读并校验协议 fixture（包名必须与 --package 一致；产物必须真实存在）。"""
    fixture_dir = Path(fixture_dir).resolve()
    data = _read_json(fixture_dir / "fixture.json")
    if data.get("schema_version") != SCHEMA_FIXTURE:
        raise eio.EnhancementError("fixture_schema", "input",
                                   f"fixture schema 不符: {data.get('schema_version')!r}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="schema_version")
    if data.get("package") != package:
        raise eio.EnhancementError(
            "fixture_package_mismatch", "input",
            f"fixture 是 {data.get('package')!r} 包，不给 {package!r} 包使用"
            f"（禁止用 video 夹具冒充音频/合并验收）",
            exit_code=EXIT_INVALID_INPUT, field_path="package")
    artifacts = []
    for rec in data.get("artifacts") or []:
        p = Path(rec["path"])
        p = p if p.is_absolute() else (fixture_dir / p).resolve()
        if not p.is_file():
            raise eio.EnhancementError("fixture_media_missing", "input",
                                       f"fixture 产物缺失: {p}",
                                       exit_code=EXIT_INVALID_INPUT, field_path="artifacts")
        artifacts.append({"role": rec.get("role"), "path": str(p),
                          "sha256": eio.sha256_file(p)})
    if not artifacts:
        raise eio.EnhancementError("fixture_no_artifacts", "input",
                                   "fixture 未登记任何产物", exit_code=EXIT_INVALID_INPUT)
    data["artifacts"] = artifacts
    data["fixture_dir"] = str(fixture_dir)
    return data


# ---------------------------------------------------------------- 成功出口

def run_transaction(package: str, fixture: dict, work: Path) -> dict:
    """同一事务入口：input → manifest → accepted → **完整读回**。"""
    package_root = Path(work) / package
    package_root.mkdir(parents=True, exist_ok=True)
    # 每次运行一个新 attempt（不复用/不改写旧证据；同一 root 可重复验收）
    import os as _os
    import time as _time
    attempt_id = eio.assert_attempt_id(
        f"txn-ok-{_os.getpid()}-{_time.time_ns() % 1_000_000:06d}")
    attempt = package_root / eio.ATTEMPTS_DIRNAME / attempt_id
    attempt.mkdir(parents=True, exist_ok=True)

    # 1) input：冻结输入（原子写）
    eio.freeze_inputs(attempt, {
        "package": package, "scope": fixture.get("scope") or {},
        "script_revision": fixture.get("script_revision") or "rev-fixture",
        "source": fixture.get("source") or "fixture",
        "fixture_dir": fixture["fixture_dir"],
    })

    # 2) 产物落 attempt（复制而不是引用源 fixture：源只读）
    artifacts = []
    outputs = attempt / "outputs"
    outputs.mkdir(exist_ok=True)
    for rec in fixture["artifacts"]:
        dst = outputs / Path(rec["path"]).name
        shutil.copy2(rec["path"], dst)
        if eio.sha256_file(dst) != rec["sha256"]:
            raise eio.EnhancementError("copy_digest_mismatch", "io",
                                       f"复制后摘要不符: {dst}", exit_code=EXIT_TOOL_FAIL)
    artifacts = [eio.artifact_record(outputs / Path(r["path"]).name, role=r.get("role"))
                 for r in fixture["artifacts"]]
    artifacts.append(eio.artifact_record(attempt / eio.INPUT_FILENAME, role="input"))

    # 3) 任务状态投影（video）：**不进入**产物 manifest
    if package == "video":
        from shared import video_attempts as va
        va.open_attempt(package_root, attempt_id, request_key=fixture.get("request_key") or "rk-txn",
                        scope=fixture.get("scope") or {},
                        script_revision=fixture.get("script_revision") or "rev-fixture",
                        provider="fixture", model_id="fixture-model",
                        generation_id="gen-fixture")
        va.transition(attempt, va.STATE_READY)

    # 4) manifest（一次写成，不可改写）
    manifest = eio.seal_manifest(attempt, {
        "package": package,
        "source": fixture.get("source") or "fixture",
        "scope": fixture.get("scope") or {},
        "script_revision": fixture.get("script_revision") or "rev-fixture",
        "request_key": fixture.get("request_key") or "rk-txn",
        "status": "validated",
        "tool_versions": {"transaction": "enhancement_io.v1"},
        "artifacts": artifacts,
        "checks": fixture.get("checks") or [{"name": "decode", "status": "pass"}],
        "coverage": fixture.get("coverage") or {},
        "errors": [],
    })

    # 5) accepted 两阶段替换 + 读回
    accepted = eio.commit_accepted(package_root, attempt_id=attempt_id,
                                   manifest_path=attempt / eio.MANIFEST_FILENAME,
                                   manifest_sha256=manifest["manifest_sha256"],
                                   input_key=fixture.get("request_key") or "rk-txn")

    # 6) 读者侧完整核验（指针 → manifest → 产物 → 来源 → required_checks）
    required = tuple(c["name"] for c in (fixture.get("checks") or [])
                     if c.get("status") in ("pass", "completed", "ok"))
    verified = eio.verify_accepted(package_root, required_checks=required)

    # 7) video：删掉 lifecycle 后读回仍成立 ⇒ 任务状态不在产物链上
    lifecycle_isolated = None
    if package == "video":
        lp = attempt / eio.LIFECYCLE_FILENAME
        lp.unlink(missing_ok=True)
        verified_again = eio.verify_accepted(package_root, required_checks=required)
        manifest_keys = set(verified_again["manifest"].keys())
        lifecycle_isolated = bool(verified_again) and not (
            {"lifecycle", "state", "task_id"} & manifest_keys)

    return {"name": "success_path", "ok": True, "attempt": str(attempt),
            "manifest_sha256": verified["manifest_sha256"],
            "accepted_attempt": accepted["attempt_id"],
            "lifecycle_outside_manifest": lifecycle_isolated,
            "artifacts": len(artifacts)}


# ---------------------------------------------------------------- 故障矩阵

def fault_mkdir(package: str, fixture: dict, work: Path) -> dict:
    """mkdir 失败：目标被同名文件占位 ⇒ 准备失败，且不产生半成品 attempt。"""
    root = Path(work) / f"{package}-mkdir"
    root.mkdir(parents=True, exist_ok=True)
    blocker = root / eio.ATTEMPTS_DIRNAME
    blocker.write_text("blocker", encoding="utf-8")          # 不是目录
    attempt = blocker / "a1"
    try:
        eio.freeze_inputs(attempt, {"package": package})
        return {"name": "mkdir_failure", "ok": False,
                "detail": "父路径被文件占用时写入仍然成功了（判据失效）"}
    except OSError as exc:
        return {"name": "mkdir_failure", "ok": True, "detail": type(exc).__name__,
                "exit_code": EXIT_TOOL_FAIL, "no_partial": not attempt.exists()}


def _safe_read_manifest(attempt: Path) -> object:
    try:
        return eio.read_manifest(attempt)
    except Exception as exc:  # noqa: BLE001 - 读回失败本身也是证据
        return {"read_error": type(exc).__name__}


def fault_write(package: str, fixture: dict, work: Path) -> dict:
    """write 失败：manifest 目标已是**目录** ⇒ 写入失败，读回不到半成品。"""
    pkg_root = Path(work) / f"{package}-write"
    attempt = pkg_root / eio.ATTEMPTS_DIRNAME / "a2"
    (attempt / eio.MANIFEST_FILENAME).mkdir(parents=True, exist_ok=True)
    try:
        eio.seal_manifest(attempt, {"package": package, "artifacts": []})
        return {"name": "write_failure", "ok": False,
                "detail": "manifest 目标被目录占位却写入成功（判据失效）"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "write_failure", "ok": True, "detail": type(exc).__name__,
                "exit_code": EXIT_TOOL_FAIL,
                "read_back": _safe_read_manifest(attempt)}


def fault_replace_confirm(package: str, fixture: dict, work: Path) -> dict:
    """replace 成功但读回不一致 ⇒ commit_unconfirmed（退出码 6），且不改称旧值仍在。"""
    pkg_root = Path(work) / f"{package}-confirm"
    attempt = pkg_root / eio.ATTEMPTS_DIRNAME / "a3"
    attempt.mkdir(parents=True, exist_ok=True)
    man = eio.seal_manifest(attempt, {"package": package, "artifacts": []})
    # 先建一个"旧" accepted 指针，证明失败时旧值原样保留
    old = eio.commit_accepted(pkg_root, attempt_id="a3",
                              manifest_path=attempt / eio.MANIFEST_FILENAME,
                              manifest_sha256=man["manifest_sha256"], input_key="old")
    # 破坏 manifest 的字节 ⇒ 替换后的读回摘要不符
    man_path = attempt / eio.MANIFEST_FILENAME
    payload = json.loads(man_path.read_text(encoding="utf-8"))
    payload["status"] = "tampered"
    man_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        eio.commit_accepted(pkg_root, attempt_id="a3", manifest_path=man_path,
                            manifest_sha256=man["manifest_sha256"], input_key="new")
        return {"name": "commit_unconfirmed", "ok": False,
                "detail": "读回摘要不符时仍报成功（判据失效）"}
    except eio.CommitUnconfirmedError as exc:
        payload = exc.payload()
        return {"name": "commit_unconfirmed", "ok": True,
                "exit_code": exc.exit_code,
                "replacement_performed": payload["replacement_performed"],
                "confirmed": payload["confirmed"],
                # 替换**已发生**：盘上指针是新指针（不声称旧值仍在，也不盲回滚）
                "pointer_after_failure_input_key": (eio.read_accepted(pkg_root) or {}).get("input_key"),
                "first_commit_input_key": old["input_key"]}
    except Exception as exc:  # noqa: BLE001
        return {"name": "commit_unconfirmed", "ok": False,
                "detail": f"未分类异常 {type(exc).__name__}: {exc}"}


def fault_concurrent(package: str, fixture: dict, work: Path) -> dict:
    """并发 writer：第二个 writer 拿不到锁（退出码 5）；owner 退出后可恢复。"""
    pkg_root = Path(work) / f"{package}-lock"
    attempt = pkg_root / eio.ATTEMPTS_DIRNAME / "a4"
    attempt.mkdir(parents=True, exist_ok=True)
    first = eio.AttemptLock(attempt / eio.LOCK_DIRNAME, purpose="first")
    first.acquire()
    try:
        second = eio.AttemptLock(attempt / eio.LOCK_DIRNAME, purpose="second")
        try:
            second.acquire()
            return {"name": "concurrent_writer", "ok": False,
                    "detail": "第二个 writer 拿到了锁（互斥失效）"}
        except eio.BusyError as exc:
            busy = {"exit_code": exc.exit_code, "code": exc.code}
    finally:
        first.release()
    # owner 退出后恢复：新 writer 能拿锁
    third = eio.AttemptLock(attempt / eio.LOCK_DIRNAME, purpose="third")
    with third:
        eio.append_event(attempt, "recovered", after={"state": "ok"})
    return {"name": "concurrent_writer", "ok": busy["exit_code"] == EXIT_BUSY,
            "busy": busy, "recovered_after_owner_exit": True}


#: 本工具自造 attempt 用的状态链（只为核验事务层，不代表任何真实包）。
_RECOVERY_TRANSITIONS = {"draft": set(), "ready": {"draft"}}


def fault_recovery(package: str, fixture: dict, work: Path) -> dict:
    """恢复：投影落后可补；投影领先/断链拒绝；尾部不完整事件保留证据并停点。"""
    out = {"name": "recovery", "ok": True, "details": []}

    # (a) 投影落后 ⇒ 按合法事件补投影
    a = Path(work) / f"{package}-rec-lag" / eio.ATTEMPTS_DIRNAME / "a5"
    a.mkdir(parents=True, exist_ok=True)
    eio.append_event(a, "opened", after={"state": "draft"})
    eio.append_event(a, "state:ready", after={"state": "ready"})
    eio.write_lifecycle(a, {"state": "draft", "projected_from_event_seq": 1})
    proj = eio.reconcile_lifecycle(a, transitions=_RECOVERY_TRANSITIONS)
    out["details"].append({"case": "projection_lagging", "ok": proj["state"] == "ready",
                           "state": proj["state"]})
    out["ok"] &= proj["state"] == "ready"

    # (b) 投影领先 ⇒ 拒绝自动续跑
    b = Path(work) / f"{package}-rec-ahead" / eio.ATTEMPTS_DIRNAME / "a6"
    b.mkdir(parents=True, exist_ok=True)
    eio.append_event(b, "opened", after={"state": "draft"})
    eio.write_lifecycle(b, {"state": "accepted", "projected_from_event_seq": 9})
    try:
        eio.reconcile_lifecycle(b, transitions=_RECOVERY_TRANSITIONS)
        out["details"].append({"case": "projection_ahead", "ok": False,
                               "detail": "投影领先却续跑了"})
        out["ok"] = False
    except eio.EnhancementError as exc:
        out["details"].append({"case": "projection_ahead", "ok": exc.code == "projection_ahead",
                               "code": exc.code})
        out["ok"] &= exc.code == "projection_ahead"

    # (c) 尾部不完整事件 ⇒ 保留原始证据并停在最后确认点
    c = Path(work) / f"{package}-rec-tail" / eio.ATTEMPTS_DIRNAME / "a7"
    c.mkdir(parents=True, exist_ok=True)
    eio.append_event(c, "opened", after={"state": "draft"})
    # 追加残片前先确认"残片状态下禁止追加"（否则会粘成一行、新事件读不回来）
    with open(c / eio.EVENTS_FILENAME, "a", encoding="utf-8") as fh:
        fh.write('{"schema_version": "enhancement_event.v1", "seq": 2, "kind": "TAILFRAG")'[:-2])
    try:
        eio.append_event(c, "later", after={"state": "ready"})
        refused = None
    except eio.EnhancementError as exc:
        refused = exc.code
    proj = eio.reconcile_lifecycle(c, transitions=_RECOVERY_TRANSITIONS)
    raw = (c / eio.EVENTS_FILENAME).read_text(encoding="utf-8")
    evidence = proj.get("tail_repaired", {}).get("evidence_path") or ""
    tail_ok = (refused == "events_tail_incomplete"
               and proj.get("state") == "draft"
               and proj["projected_from_event_seq"] == 1
               and "TAILFRAG" not in raw                  # 残片已从事件文件截走
               and evidence and "TAILFRAG" in Path(evidence).read_text(encoding="utf-8"))
    out["details"].append({"case": "tail_incomplete", "ok": bool(tail_ok),
                           "state": proj.get("state"), "append_refused": refused,
                           "evidence_kept": bool(evidence and "TAILFRAG" in Path(evidence).read_text(encoding="utf-8"))})
    out["ok"] &= bool(tail_ok)
    return out


def fault_tamper_detected(package: str, fixture: dict, work: Path) -> dict:
    """产物被改写 ⇒ 读者侧必须检出（判据可判别：不是"永远 pass"的核验）。"""
    pkg_root = Path(work) / f"{package}-tamper"
    attempt = pkg_root / eio.ATTEMPTS_DIRNAME / "a8"
    attempt.mkdir(parents=True, exist_ok=True)
    outputs = attempt / "outputs"
    outputs.mkdir(exist_ok=True)
    target = outputs / Path(fixture["artifacts"][0]["path"]).name
    shutil.copy2(fixture["artifacts"][0]["path"], target)
    man = eio.seal_manifest(attempt, {
        "package": package, "artifacts": [eio.artifact_record(target, role="fixture")],
        # 请求身份与指针一致：缺了会先撞 `accepted_input_key_missing`，
        # 就测不到本来要测的摘要判据了（2026-10-04 复核 P1-2 之二）
        "request_key": "tamper",
        "checks": [{"name": "decode", "status": "pass"}]})
    eio.commit_accepted(pkg_root, attempt_id="a8", manifest_path=attempt / eio.MANIFEST_FILENAME,
                        manifest_sha256=man["manifest_sha256"], input_key="tamper")
    before = eio.verify_accepted(pkg_root, required_checks=("decode",))
    target.write_bytes(target.read_bytes() + b"tampered")
    try:
        eio.verify_accepted(pkg_root, required_checks=("decode",))
        return {"name": "tamper_detected", "ok": False,
                "detail": "产物被改写后读者侧仍然采纳（判据失效）"}
    except eio.EnhancementError as exc:
        return {"name": "tamper_detected", "ok": exc.code == "accepted_artifact_digest_mismatch",
                "code": exc.code, "verified_before_tamper": bool(before)}


FAULTS = (fault_mkdir, fault_write, fault_replace_confirm, fault_concurrent,
          fault_tamper_detected, fault_recovery)


# ---------------------------------------------------------------- 正式目录摘要

def snapshot_production() -> dict:
    """正式产物的只读摘要（不递归 vimax_working：那是帧/视频工作目录）。"""
    from shared.paths import chapter_dir, clips_dir, audio_dir, script_file
    from shared.paths import step01_log_file

    out: dict[str, str] = {}
    for p in (script_file(), chapter_dir() / "step01_quality.json",
              chapter_dir() / "step02b_quality.json", step01_log_file()):
        try:
            if p.is_file():
                out[str(p)] = eio.sha256_file(p)
        except OSError:
            continue
    for d in (clips_dir(), audio_dir()):
        try:
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file():
                    out[str(f)] = eio.sha256_file(f)
        except OSError:
            continue
    return out


def run_package(package: str, fixture_dir: Path, out_dir: Path) -> dict:
    fixture = load_fixture(fixture_dir, package)
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_absolute():
        raise eio.EnhancementError("invalid_out_dir", "paths",
                                   f"--out-dir 必须是绝对路径: {out_dir}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="--out-dir")
    if out_dir.name != package:
        raise eio.EnhancementError(
            "out_dir_package_mismatch", "paths",
            f"--out-dir 必须是 <ROOT>/{package}（收到 {out_dir}）",
            exit_code=EXIT_INVALID_INPUT, field_path="--out-dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    before = snapshot_production()
    work = Path(tempfile.mkdtemp(prefix=f"txn-{package}-", dir=str(out_dir.parent)))
    checks = [run_transaction(package, fixture, work)]
    for fn in FAULTS:
        try:
            checks.append(fn(package, fixture, work))
        except Exception as exc:  # noqa: BLE001 - 故障注入本身失败也算该项不通过
            checks.append({"name": fn.__name__, "ok": False,
                           "detail": f"{type(exc).__name__}: {exc}"})
    after = snapshot_production()
    unchanged = before == after
    return {"package": package, "fixture": str(Path(fixture_dir).resolve()),
            "work_dir": str(work), "checks": checks,
            "production_unchanged": unchanged,
            "changed_paths": sorted(set(before) ^ set(after)) if not unchanged else [],
            "ok": all(c.get("ok") for c in checks) and unchanged}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/verify_enhancement_transaction.py",
        description="F 阶段事务入口验收（input→manifest→accepted→读回 + 故障矩阵）")
    parser.add_argument("--package", required=True, choices=PACKAGES)
    parser.add_argument("--fixture", required=True, help="有效协议 fixture 目录")
    parser.add_argument("--out-dir", required=True, help="隔离 root：<ROOT>/<package>")
    parser.add_argument("--clean-work", action="store_true",
                        help="验收通过后清理临时工作目录（默认保留，便于取证）")
    args = parser.parse_args(argv)
    try:
        report = run_package(args.package, Path(args.fixture), Path(args.out_dir))
    except eio.EnhancementError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, indent=2))
        return exc.exit_code
    if args.clean_work and report["ok"]:
        shutil.rmtree(report["work_dir"], ignore_errors=True)
        report["work_dir"] = None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK if report["ok"] else EXIT_REJECTED


if __name__ == "__main__":
    raise SystemExit(main())
