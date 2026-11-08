"""一次性授权的定向验收（M07/M09，2026-10-29）.

覆盖：稳定授权身份、排他占用、顺序/并发重复启动、零发送拒收、崩溃后不重发、
状态不可读不自动恢复、绑定不符不得改绑。全部经真实启动入口 + 底层 SDK 桩。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / ".p1a_exp/step01_delivery_20261028/audit_package.json"
ENTRY = ROOT / "tools/audit_frozen_candidate.py"

pytestmark = pytest.mark.skipif(
    not (PACKAGE.is_file() and ENTRY.is_file()),
    reason="external dataset: frozen candidate audit package not present",
)

from tests.step01_script_gen.test_audit_entry_subprocess import (  # noqa: E402
    SITECUSTOMIZE, _binding_digest, _grant, _pin_stub_channel, _run)


@pytest.fixture()
def package():
    data = json.loads(PACKAGE.read_text(encoding="utf-8-sig"))
    # 审计规则模板已更新（锁定台词规则），冻结包必须按当前 prompt 身份重新绑定；
    # 只覆盖版本字段，不改外部数据集本身。此处用子进程的生效版本（父进程的
    # 时长带/配置可能不同，直接调用 audit_prompt_version() 会得到另一组哈希）。
    data.setdefault("audit_contract", {})["audit_prompt_version"] = "698eb56f54fe"
    return _pin_stub_channel(data)


def _key(authorization_id: str) -> str:
    return "auth_" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()[:32]


def _state_dir(state_root: Path, authorization_id: str = "pytest-fixture-auth") -> Path:
    # 测试注入的是 AUDIT_AUTHORIZATION_ROOT；登记目录在它下面的 audit_authorizations/
    return Path(state_root) / "audit_authorizations" / _key(authorization_id)


def _project_revision(candidate_path: Path, tmp_path: Path, package: dict) -> str:
    config = dict(package.get("effective_config") or {})
    env = dict(os.environ)
    env.update({k: str(v) for k, v in config.items()})
    env.update({
        "OUTPUT_BASE": str(tmp_path / "project_output"),
        "STEP01_LOG_PATH": str(tmp_path / "project.log"),
        "TEXT_CALL_LEDGER": str(tmp_path / "project_calls.jsonl"),
        "PYTHONIOENCODING": "utf-8",
    })
    code = r'''
import json, sys
from pathlib import Path
import step01_script_gen.main as m
from shared.quality_gate import compute_script_revision
c = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
n = Path(sys.argv[2]).read_text(encoding="utf-8-sig")
p, e = m.project_for_commit(c, n, label="pytest-auth-guard")
if e:
    raise SystemExit("projection failed: %s" % e)
print(compute_script_revision(p))
'''
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code,
         str(candidate_path), str(ROOT / package["object"]["novel"])],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return next(line for line in reversed(proc.stdout.splitlines()) if line.strip())


def _pre_gate_package(tmp_path: Path, package: dict) -> dict:
    """能通过身份/revision、但提交入口台词硬门会拒收的候选（发送前拒收夹具）."""
    cand = json.loads((ROOT / package["object"]["candidate"]).read_text(encoding="utf-8-sig"))
    shot = next(s for s in cand["shots"] if s["index"] == 17)
    shot["narration_segment"] = ""
    shot["subtitle"] = ""
    broken = tmp_path / "broken_dialogue.json"
    broken.write_text(json.dumps(cand, ensure_ascii=False), encoding="utf-8")
    revision = _project_revision(broken, tmp_path, package)
    data = json.loads(json.dumps(package))
    data["object"]["candidate"] = str(broken)
    data["object"]["candidate_sha256"] = hashlib.sha256(broken.read_bytes()).hexdigest()
    data["object"]["revision"] = revision
    return data


# ---------------------------------------------------------------------------
# S14：顺序重复启动（换运行根、换包文件名、同一授权 ID）
# ---------------------------------------------------------------------------
def test_sequential_same_authorization_sends_at_most_once(tmp_path, package):
    state = tmp_path / "auth_state"
    p1, pay1, sends1, out1, _ = _run(tmp_path, package, "ok",
                                     out_root=tmp_path / "run_first", state_root=state)
    assert p1.returncode == 0, (p1.stdout[-900:], p1.stderr[-500:])
    assert sends1 == 1, sends1
    assert pay1["send_state"] == "returned" and pay1["budget"]["consumed"] == 1, pay1
    claim_path = _state_dir(state) / "claim.json"
    claim_before = claim_path.read_text(encoding="utf-8")
    attempts_before = (_state_dir(state) / "attempts.jsonl").read_text(encoding="utf-8")

    p2, pay2, sends2, out2, _ = _run(tmp_path, package, "ok",
                                     out_root=tmp_path / "run_second", state_root=state)
    assert p2.returncode == 2, (p2.stdout[-900:], p2.stderr[-500:])
    assert pay2["code"] == "authorization_already_claimed", pay2
    assert pay2["send_state"] == "not_sent"
    assert sends2 == 0, "同一授权换运行根后不得再次发送"

    # 第三次：同样的包内容写到另一个文件名（等价于复制审计包），授权 ID 不变
    p3, pay3, sends3, out3, _ = _run(tmp_path, package, "ok",
                                     out_root=tmp_path / "run_third", state_root=state)
    assert p3.returncode == 2 and pay3["code"] == "authorization_already_claimed", pay3
    assert sends1 + sends2 + sends3 == 1, "三次累计最多一次 SDK 调用"
    assert claim_path.read_text(encoding="utf-8") == claim_before, "首次占用记录不得被覆盖"
    assert (_state_dir(state) / "attempts.jsonl").read_text(
        encoding="utf-8") == attempts_before, "首次尝试记录不得被覆盖"
    assert len([p for p in state.rglob("claim.json")]) == 1
    # 没有第二份模型响应
    second_bodies = list(Path(out2).rglob("*.txt"))
    assert second_bodies == [], second_bodies


# ---------------------------------------------------------------------------
# S15：并发启动
# ---------------------------------------------------------------------------
def test_concurrent_same_authorization_sends_at_most_once(tmp_path, package):
    state = tmp_path / "auth_state"
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    data = json.loads(json.dumps(package))
    data["budget"]["remaining"] = 1
    pkg = tmp_path / "pkg.json"
    _grant(data, pkg)

    procs, logs, outs = [], [], []
    for i in (1, 2):
        log = tmp_path / ("sends_%d.log" % i)
        out = tmp_path / ("run_%d" % i)
        env = dict(os.environ)
        env["PYTHONPATH"] = "%s%s%s" % (stub, os.pathsep, ROOT)
        env["STEP01_STUB_MODE"] = "ok"
        env["STEP01_STUB_LOG"] = str(log)
        env["STEP01_STUB_DELAY"] = "0.6"
        env["STEP01_TEST_AUTH_ROOT"] = str(state)
        env["STEP01_AUDIT_AUTHORIZED"] = "1"
        procs.append(subprocess.Popen(
            [sys.executable, "-B", str(ENTRY), "--package", str(pkg), "--out-root", str(out)],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace"))
        logs.append(log)
        outs.append(out)
    results = [p.communicate(timeout=240) for p in procs]

    sends = sum(len(log.read_text(encoding="utf-8").splitlines())
                if log.is_file() else 0 for log in logs)
    assert sends == 1, ("同一授权并发启动累计只能发送一次", sends,
                        [r[0][-500:] for r in results])

    payloads = []
    for stdout, _stderr in results:
        lines = [ln for ln in stdout.strip().splitlines() if ln.startswith("{")]
        payloads.append(json.loads(lines[-1]) if lines else {})
    codes = [p.returncode for p in procs]
    assert sorted(codes) == [0, 2], codes
    winner = payloads[codes.index(0)]
    loser = payloads[codes.index(2)]
    assert loser["code"] in ("authorization_lost", "authorization_already_claimed"), loser
    assert loser["send_state"] == "not_sent", loser
    claim_path = _state_dir(state) / "claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    assert claim["run_id"] == winner["authorization"]["claim_run_id"], (claim, winner)
    assert claim["call_id"] == winner["evidence"]["attempt_call_ids"][0]
    # 占用记录完整：claim + 1 条 attempt + 1 条 outcome，均绑定获胜运行
    attempts = [json.loads(ln) for ln in (_state_dir(state) / "attempts.jsonl").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    outcomes = [json.loads(ln) for ln in (_state_dir(state) / "outcomes.jsonl").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    assert len(attempts) == 1 and len(outcomes) == 1
    assert attempts[0]["run_id"] == winner["authorization"]["claim_run_id"]
    assert outcomes[0]["outcome"] == "returned"


# ---------------------------------------------------------------------------
# S16：零发送拒收（硬门、登记、绑定）
# ---------------------------------------------------------------------------
def test_pre_send_gate_records_zero_sends(tmp_path, package):
    tampered = _pre_gate_package(tmp_path, package)
    state = tmp_path / "auth_state"
    proc, payload, sends, out, _ = _run(
        tmp_path, tampered, "ok", out_root=tmp_path / "run_blocked", state_root=state)
    assert proc.returncode == 3, (proc.stdout[-1200:], proc.stderr[-500:])
    assert payload["status"] == "BLOCK", payload
    assert any(b.get("code") == "dialogue_truncated"
               for b in (payload["summary"].get("blocking") or [])), payload["summary"]
    assert sends == 0, "提交前硬门拒收不得发送审计"
    receipt = json.loads(Path(payload["budget_receipt"]).read_text(encoding="utf-8"))
    assert receipt["sends"] == 0 and receipt["send_state"] == "not_sent", receipt
    assert receipt["sends_known"] is True and receipt["sends_source"] == "attempts", receipt
    assert receipt["authorization"]["claimed"] is False, receipt
    assert payload["budget"]["consumed"] == 0
    assert payload["authorization"]["attempts"] == 0
    assert not (_state_dir(state) / "claim.json").exists(), "拒收不得留下授权占用"


def _granted_package(tmp_path, package, **overrides):
    """按当前包内容登记一次授权，再按需篡改（不再由 _run 重新登记）."""
    data = json.loads(json.dumps(package))
    data["budget"]["remaining"] = 1
    pkg = tmp_path / "pkg_granted.json"
    _grant(data, pkg)
    data["budget"]["new_authorization"].update(overrides)
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def test_tampered_binding_digest_is_refused_before_claim(tmp_path, package):
    data = _granted_package(tmp_path, package, binding_digest="0" * 32)
    proc, payload, sends, out, _ = _run(tmp_path, data, "ok", grant=False)
    assert proc.returncode == 2, proc.stdout[-800:]
    assert payload["code"] == "authorization_binding_mismatch", payload
    assert sends == 0
    assert not list(Path(out).rglob("claim.json"))


def test_max_sends_other_than_one_is_refused(tmp_path, package):
    data = _granted_package(tmp_path, package, max_sends=2)
    proc, payload, sends, out, _ = _run(tmp_path, data, "ok", grant=False)
    assert proc.returncode == 2, proc.stdout[-800:]
    assert payload["code"] == "budget_exhausted" and "max_sends" in payload["detail"], payload
    assert sends == 0


def test_missing_binding_digest_is_refused(tmp_path, package):
    data = _granted_package(tmp_path, package, binding_digest="")
    proc, payload, sends, out, _ = _run(tmp_path, data, "ok", grant=False)
    assert proc.returncode == 2 and payload["code"] == "budget_exhausted", payload
    assert sends == 0


def test_same_id_with_changed_object_is_rejected_not_rebound(tmp_path, package):
    state = tmp_path / "auth_state"
    p1, pay1, sends1, _o1, _ = _run(tmp_path, package, "ok",
                                    out_root=tmp_path / "run_a", state_root=state)
    assert p1.returncode == 0 and sends1 == 1

    # 同一 authorization_id，但**通道变了**（绑定摘要随之变化并已按新包登记）：
    # 已存 claim 的摘要与本次不符 ⇒ 拒绝改绑后重发，而不是悄悄按新绑定发第二次。
    data = json.loads(json.dumps(package))
    data["budget"]["remaining"] = 1
    data["audit_channel"]["model"] = "deepseek-pro"
    pkg = tmp_path / "pkg_rebound.json"
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    data["budget"]["new_authorization"] = {
        "authorization_id": "pytest-fixture-auth",
        "granted_by": "pytest-fixture",
        "granted_at": "2026-10-29T00:00:00+08:00",
        "max_sends": 1,
        "binding_digest": _binding_digest(pkg),
    }
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    p2, pay2, sends2, _o2, _ = _run(tmp_path, data, "ok", grant=False,
                                    out_root=tmp_path / "run_b", state_root=state)
    assert p2.returncode == 2, pay2
    assert pay2["code"] == "authorization_identity_mismatch", pay2
    assert sends2 == 0
    assert pay1["authorization"]["send_state"] == "returned"


# ---------------------------------------------------------------------------
# S17：故障与恢复
# ---------------------------------------------------------------------------
def test_crash_after_send_blocks_restart_without_resend(tmp_path, package):
    state = tmp_path / "auth_state"
    first, _payload, sends1, _out1, _ = _run(
        tmp_path, package, "crash_after_attempt",
        out_root=tmp_path / "run_crash", state_root=state)
    assert first.returncode not in (0, 2, 3), first.returncode
    assert sends1 == 1
    sdir = _state_dir(state)
    assert (sdir / "claim.json").is_file()
    attempts = [ln for ln in (sdir / "attempts.jsonl").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    assert len(attempts) == 1
    assert not (sdir / "outcomes.jsonl").exists(), "崩溃：attempt 之后没有 outcome"

    second, payload, sends2, _out2, _ = _run(
        tmp_path, package, "ok", out_root=tmp_path / "run_after_crash", state_root=state)
    assert second.returncode == 2, (second.stdout[-900:], second.stderr[-500:])
    assert payload["code"] == "authorization_already_claimed", payload
    assert payload["authorization"]["send_state"] == "unknown", payload["authorization"]
    assert payload["authorization"]["attempts"] == 1
    assert sends2 == 0, "发送后异常退出不得自动释放额度或重发"
    # 现场保留：不删除、不改写
    assert (sdir / "claim.json").is_file()
    assert not (sdir / "outcomes.jsonl").exists()


def test_claim_write_failure_sends_nothing(tmp_path, package):
    state_file = tmp_path / "auth_state_is_a_file"
    state_file.write_text("not a directory", encoding="utf-8")
    proc, payload, sends, out, _ = _run(
        tmp_path, package, "ok", out_root=tmp_path / "run_no_claim",
        state_root=state_file)
    assert proc.returncode == 3, (proc.stdout[-1200:], proc.stderr[-500:])
    assert payload["status"] == "BLOCK", payload
    assert sends == 0, "授权占用写入失败必须零发送"
    assert payload["summary"].get("audit_status") in ("request_failed", None), payload["summary"]
    receipt = json.loads(Path(payload["budget_receipt"]).read_text(encoding="utf-8"))
    assert receipt["sends"] == 0, receipt
    assert receipt["authorization"]["claimed"] is False


def test_truncated_claim_is_refused_and_left_in_place(tmp_path, package):
    state = tmp_path / "auth_state"
    sdir = _state_dir(state)
    sdir.mkdir(parents=True)
    claim = sdir / "claim.json"
    claim.write_text('{"authorization_id": "pytest-fixture-a', encoding="utf-8")
    before = claim.read_text(encoding="utf-8")

    proc, payload, sends, out, _ = _run(
        tmp_path, package, "ok", out_root=tmp_path / "run_truncated", state_root=state)
    assert proc.returncode == 2, (proc.stdout[-1000:], proc.stderr[-400:])
    assert payload["code"] == "authorization_state_unreadable", payload
    assert sends == 0
    assert claim.read_text(encoding="utf-8") == before, "不得删除或重建现场"
    assert not (sdir / "attempts.jsonl").exists()


def test_truncated_attempt_record_is_refused(tmp_path, package):
    state = tmp_path / "auth_state"
    sdir = _state_dir(state)
    sdir.mkdir(parents=True)
    (sdir / "claim.json").write_text(json.dumps({
        "schema": "step01.audit_authorization/1",
        "authorization_id": "pytest-fixture-auth",
        "binding_digest": "x" * 32, "binding": {}, "run_id": "r", "claimed_at": "t"}),
        encoding="utf-8")
    (sdir / "attempts.jsonl").write_text('{"attempt_id": "att_1"', encoding="utf-8")
    before = (sdir / "attempts.jsonl").read_text(encoding="utf-8")

    proc, payload, sends, out, _ = _run(
        tmp_path, package, "ok", out_root=tmp_path / "run_trunc", state_root=state)
    assert proc.returncode == 2, (proc.stdout[-1000:], proc.stderr[-400:])
    assert payload["code"] in ("authorization_state_unreadable",
                               "authorization_identity_mismatch",
                               "authorization_already_claimed"), payload
    assert sends == 0
    assert (sdir / "attempts.jsonl").read_text(encoding="utf-8") == before


def test_sent_without_ledger_is_reported_incomplete(tmp_path, package):
    """S12：已发送但台账缺失 ⇒ evidence_complete=false，退出码 3，不重发."""
    proc, payload, sends, out, _ = _run(
        tmp_path, package, "ok", extra_env={"STEP01_STUB_DROP_LEDGER": "1"})
    assert sends == 1
    assert proc.returncode == 3, (proc.stdout[-1000:], proc.stderr[-400:])
    assert payload["status"] == "COMMITTED", payload
    assert payload["send_state"] == "returned"
    assert payload["evidence_complete"] is False, payload["evidence"]
    assert any("台账" in p for p in payload["evidence"]["problems"]), payload["evidence"]
    receipt = json.loads(Path(payload["budget_receipt"]).read_text(encoding="utf-8"))
    assert receipt["sends"] == 1 and receipt["evidence_complete"] is False, receipt
    assert receipt["authorization"]["claimed"] is True


def test_state_dir_is_independent_of_run_root(tmp_path, package):
    """S04：同一授权 ID 在两个运行根下解析到同一个状态位置."""
    state = tmp_path / "auth_state"
    p1, pay1, _s1, out1, _ = _run(tmp_path, package, "ok",
                                  out_root=tmp_path / "root_A", state_root=state)
    assert p1.returncode == 0
    p2, pay2, _s2, out2, _ = _run(tmp_path, package, "ok",
                                  out_root=tmp_path / "root_B", state_root=state,
                                  extra_env={"OUTPUT_BASE": str(tmp_path / "other")})
    assert p2.returncode == 2 and pay2["code"] == "authorization_already_claimed"
    assert Path(pay1["authorization"]["state_dir"]) == Path(pay2["authorization"]["state_dir"])
    assert str(out1) not in pay1["authorization"]["state_dir"]
    assert str(out2) not in pay2["authorization"]["state_dir"]


def test_registry_root_env_var_cannot_redirect_consumption(tmp_path, package):
    """P1 反例：首次消费后把旧登记根变量指向空目录，不得再获得一次发送资格.

    直接运行 Python 入口（不是 PowerShell）：生产解析不读该变量，因此第二次仍命中
    同一个 claim，而不是在一个新目录里"再发一次"。
    """
    state = tmp_path / "auth_state"
    other = tmp_path / "evil_state"
    p1, pay1, sends1, _o1, _ = _run(tmp_path, package, "ok",
                                    out_root=tmp_path / "run_first", state_root=state)
    assert p1.returncode == 0 and sends1 == 1, (p1.stdout[-800:], sends1)

    p2, pay2, sends2, _o2, _ = _run(
        tmp_path, package, "ok", out_root=tmp_path / "run_redirect", state_root=state,
        extra_env={"STEP01_AUDIT_STATE_ROOT": str(other),
                   "STEP01_TEST_PROVE_ENV_INERT": "1"})
    assert p2.returncode == 2, (p2.stdout[-900:], p2.stderr[-400:])
    assert pay2["code"] == "authorization_already_claimed", pay2
    assert sends2 == 0, "改环境变量不得产生第二次 SDK 调用"
    assert Path(pay2["authorization"]["state_dir"]) == Path(pay1["authorization"]["state_dir"])
    assert not list(other.rglob("claim.json")), "空目录里不得出现新的占用记录"
