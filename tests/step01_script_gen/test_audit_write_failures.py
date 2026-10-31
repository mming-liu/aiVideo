# -*- coding: utf-8 -*-
"""R05/F4：**证据写失败**的入口级验收（2026-10-30）.

原则（与 shared/audit_authorization 的顺序一致）：

* 发送**前**写失败（claim / attempt）⇒ 零 HTTP、零发送；
* 发送**后**写失败（outcome / result / receipt / 台账）⇒ 保留已发生的结果，
  证据降级、退出码不是"完整成功"，**不重发**。

故障注入只在测试侧：sitecustomize 按**具体文件名**失败（不是让所有 open 失效），
并记录命中次数，证明被判的写入确实是目标文件。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_audit_entry_harness", _HERE / "test_audit_entry_subprocess.py")
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)

_FAULT_BLOCK = '''
# ---- 故障注入（仅测试）：按**具体文件名**命中，不全局禁写 ----
_FAULT_NAME = os.environ.get("STEP01_FAULT_FILE") or ""
_FAULT_AFTER = int(os.environ.get("STEP01_FAULT_AFTER") or "0")
_FAULT_LOG = os.environ.get("STEP01_FAULT_LOG")
_FAULT_HITS = {"n": 0}


def _fault_hit(path):
    name = os.path.basename(str(path))
    if not _FAULT_NAME or name != _FAULT_NAME:
        return False
    _FAULT_HITS["n"] += 1
    if _FAULT_LOG:
        with open(_FAULT_LOG, "a", encoding="utf-8") as fh:
            fh.write(name + "\\n")
    return _FAULT_HITS["n"] > _FAULT_AFTER


import builtins as _bi
import io as _io

_real_open = _bi.open


def _open(path, *a, **k):
    if _fault_hit(path):
        raise OSError("stub fault: denied " + os.path.basename(str(path)))
    return _real_open(path, *a, **k)


_bi.open = _open
_io.open = _open

_real_os_open = os.open


def _os_open(path, *a, **k):
    if _fault_hit(path):
        raise OSError("stub fault: denied " + os.path.basename(str(path)))
    return _real_os_open(path, *a, **k)


os.open = _os_open
'''


@pytest.fixture()
def package():
    return H._pin_stub_channel(json.loads(H.PACKAGE.read_text(encoding="utf-8-sig")))


def _run_fault(tmp_path, package_obj, monkeypatch, fault, *, after=0, **kwargs):
    monkeypatch.setattr(H, "SITECUSTOMIZE", H.SITECUSTOMIZE + _FAULT_BLOCK)
    log = tmp_path / ("fault_%s.log" % fault)
    env = dict(kwargs.pop("extra_env", {}) or {})
    env.update({"STEP01_FAULT_FILE": fault, "STEP01_FAULT_LOG": str(log),
                "STEP01_FAULT_AFTER": str(after)})
    proc, payload, sends, out, reqs = H._run(tmp_path, package_obj, "ok",
                                             extra_env=env, **kwargs)
    hits = len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0
    return proc, payload, sends, out, reqs, hits


def _problems(payload) -> str:
    return " / ".join(str(x) for x in (payload.get("evidence") or {}).get("problems") or [])


def test_claim_write_failure_sends_nothing(tmp_path, package, monkeypatch):
    """claim 写失败 ⇒ 零 HTTP、零 attempt，占用现场不被伪造."""
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "claim.json")
    assert hits >= 1, "注入没有命中目标文件"
    assert sends == 0 and not reqs, reqs
    assert proc.returncode == 3, (proc.returncode, proc.stdout[-600:])
    assert payload["sends"] == 0
    assert payload["evidence"]["attempts"] == 0
    assert payload["authorization"]["claimed"] is False


def test_attempt_write_failure_sends_nothing_and_keeps_reservation(tmp_path, package,
                                                                 monkeypatch):
    """attempt 写失败 ⇒ 零 HTTP；已占用的授权**不自动释放**（不删登记、不还额度）."""
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "attempts.jsonl")
    assert hits >= 1
    assert sends == 0 and not reqs, reqs
    assert proc.returncode == 3
    assert payload["send_state"] == "reserved", payload
    assert payload["authorization"]["claimed"] is True
    assert payload["evidence"]["attempts"] == 0


def test_outcome_write_failure_keeps_the_send_but_reports_incomplete(tmp_path, package,
                                                                    monkeypatch):
    """发送成功后 outcomes 写失败：恰好一次发送、证据降级、退出码不是完整成功."""
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "outcomes.jsonl")
    assert hits >= 1
    assert sends == 1, "发送必须真的发生过一次"
    assert proc.returncode == 3, (proc.returncode, proc.stdout[-600:])
    assert payload["evidence_complete"] is False
    assert "留档失败" in _problems(payload) or "写入失败" in _problems(payload), payload
    assert payload["sends"] == 1


def test_result_write_failure_keeps_the_audit_result(tmp_path, package, monkeypatch):
    """终局记录写失败：结果不被吞掉，但整体不标完整."""
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "result.json.tmp")
    assert hits >= 1
    assert proc.returncode == 3
    assert payload["evidence_complete"] is False
    assert "留档写入失败" in _problems(payload), _problems(payload)
    # 已经产生的审计结论仍在报告里（不因留档失败就丢掉）
    assert payload.get("summary", {}).get("status") == "COMMITTED", payload.get("summary")


def test_receipt_write_failure_is_reported(tmp_path, package, monkeypatch):
    """额度收据写失败：报不完整，且不承诺在坏介质上还有收据."""
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "budget_receipt.json")
    assert hits >= 1
    assert proc.returncode == 3
    assert payload["evidence_complete"] is False
    assert "收据写入失败" in _problems(payload), _problems(payload)
    assert payload.get("budget_receipt") == ""


def test_missing_ledger_after_send_is_incomplete(tmp_path, package, monkeypatch):
    """"已发送但没有调用台账"必须判不完整；不是"没有正文所以正常"."""
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "step01_calls.jsonl")
    assert hits >= 1
    assert sends == 1
    assert proc.returncode == 3
    assert payload["evidence_complete"] is False
    assert "调用台账" in _problems(payload), _problems(payload)


def test_no_send_needs_no_body(tmp_path, package):
    """对照：明确**没有发送**时，没有响应正文是正常状态，不得伪造"归档失败"."""
    import tools.audit_frozen_candidate as afc
    chapter = tmp_path / "chapter"
    chapter.mkdir()
    evidence = afc._collect_evidence(chapter, tmp_path / "no_calls.jsonl", "rev-x",
                                     send_state="not_sent", attempts=[], run_id="run-x")
    assert evidence["complete"] is True, evidence["problems"]
    assert evidence["sent"] is False
    assert evidence["response_expected"] is False
    assert not [p for p in evidence["problems"] if "正文" in p], evidence["problems"]


def test_recovery_does_not_resend_after_outcome_failure(tmp_path, package, monkeypatch):
    """故障现场再次启动：不重发、不覆盖原记录；撤除注入后用**新授权**才成功."""
    state_root = tmp_path / "auth_state"
    proc, payload, sends, out, reqs, hits = _run_fault(
        tmp_path, package, monkeypatch, "outcomes.jsonl", state_root=state_root)
    assert hits >= 1 and sends == 1 and proc.returncode == 3
    out_root = out
    claim_path = next(Path(state_root).rglob("claim.json"))
    attempts_path = claim_path.with_name("attempts.jsonl")
    claim_before = json.loads(claim_path.read_text(encoding="utf-8"))
    attempts_before = attempts_path.read_text(encoding="utf-8")

    # 再启动一次（不注入）：授权已占用 ⇒ 零发送，原现场保留
    monkeypatch.setattr(H, "SITECUSTOMIZE", H.SITECUSTOMIZE)
    proc2, payload2, sends2, out2, reqs2 = H._run(
        tmp_path, package, "ok", state_root=state_root, out_root=out_root)
    assert sends2 == 0, "恢复运行不得补发"
    assert proc2.returncode in (2, 3), (proc2.returncode, proc2.stdout[-400:])
    claim_after = json.loads(claim_path.read_text(encoding="utf-8"))
    attempts_after = attempts_path.read_text(encoding="utf-8")
    assert claim_after == claim_before, "占用现场不得被覆盖"
    assert attempts_after == attempts_before, "attempt 记录不得被重写"

    # 撤除注入 + 新授权（新 out_root）：合法成功
    proc3, payload3, sends3, out3, reqs3 = H._run(
        tmp_path, package, "ok", state_root=tmp_path / "auth_state_new")
    assert sends3 == 1, (proc3.returncode, proc3.stdout[-600:])
    assert proc3.returncode == 0
    assert payload3["evidence_complete"] is True


# ---------------------------------------------------------------------------
# R05/F5：身份关联在**真实入口**上的表现（等数量、错身份不能判完整）
# ---------------------------------------------------------------------------
def _auth_dir_name() -> str:
    from shared.paths import step01_audit_authorization_dir
    return step01_audit_authorization_dir("pytest-fixture-auth").name


def test_a_healthy_run_reports_an_intact_chain(tmp_path, package):
    """真实运行的授权链：claim → attempt → outcome → 台账 全部对上."""
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok")
    assert proc.returncode == 0, (proc.stdout[-600:], proc.stderr[-300:])
    chain = payload["evidence"]["authorization_chain"]
    assert chain["ok"] is True and chain["problems"] == [], chain
    receipt = json.loads(Path(payload["budget_receipt"]).read_text(encoding="utf-8"))
    assert receipt["evidence_dimensions"]["authorization_chain"] is True, receipt
    assert receipt["evidence_dimensions"]["chain_problems"] == []


def test_a_tampered_state_with_the_same_counts_cannot_be_reused(tmp_path, package):
    """数量不变、身份被换（attempt.run_id 改成别的运行）⇒ 不得再次发送."""
    state_root = tmp_path / "auth_state"
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok",
                                            state_root=state_root)
    assert proc.returncode == 0 and sends == 1
    claim_path = next(Path(state_root).rglob("claim.json"))
    attempts_path = claim_path.with_name("attempts.jsonl")
    rows = [json.loads(x) for x in attempts_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    rows[0]["run_id"] = "run-other"
    attempts_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                             encoding="utf-8")
    claim_before = claim_path.read_text(encoding="utf-8")
    proc2, payload2, sends2, out2, reqs2 = H._run(tmp_path, package, "ok",
                                             state_root=state_root)
    assert sends2 == 0, "身份被改的现场不得再次发送"
    assert proc2.returncode == 2, (proc2.returncode, proc2.stdout[-400:])
    assert "authorization" in proc2.stdout or "授权" in proc2.stdout, proc2.stdout[-400:]
    assert claim_path.read_text(encoding="utf-8") == claim_before


def test_attempts_without_a_claim_are_refused_as_uncertain(tmp_path, package):
    """有尝试、没占用凭据 ⇒ 按不确定状态拒收（不猜"没发过"，也不自动释放）."""
    state_root = tmp_path / "auth_state"
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok",
                                            state_root=state_root)
    assert proc.returncode == 0 and sends == 1
    # 丢掉占用凭据、保留发送尝试：现场变成"有尝试、没占用"
    claim_path = next(Path(state_root).rglob("claim.json"))
    claim_path.unlink()
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok",
                                            state_root=state_root)
    assert sends == 0 and not reqs
    assert proc.returncode == 2, (proc.returncode, proc.stdout[-400:])
    assert "authorization_state_corrupt" in proc.stdout, proc.stdout[-400:]


def test_missing_or_tampered_body_is_incomplete(tmp_path, package):
    """已发送但正文缺失/摘要不符 ⇒ 判不完整；恢复原文件后又成立（同一个函数，真实产物）."""
    import tools.audit_frozen_candidate as afc
    from shared import call_ledger
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok")
    assert proc.returncode == 0 and sends == 1
    chapter = Path(payload["chapter_dir"])
    ledger = Path(payload["call_ledger"])
    # 正文核验按**本进程配置的台账路径**找文件（生产入口在准备阶段设过），
    # 进程内直接调同一函数时必须先设同一份台账，否则校验的是别的目录。
    _prev = call_ledger.ledger_path()
    call_ledger.set_ledger_path(ledger)
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    body = Path(rows[0]["response_path"])
    original = body.read_bytes()

    def _evidence():
        return afc._collect_evidence(chapter, ledger, payload["revision"],
                                     send_state="returned", attempts=[],
                                     run_id=rows[0].get("run_id") or "")

    body.unlink()
    missing = _evidence()
    assert missing["complete"] is False
    assert any("正文" in p for p in missing["problems"]), missing["problems"]
    assert missing["bodies_verified"] == 0

    body.write_bytes(original + b" ")
    tampered = _evidence()
    assert tampered["complete"] is False
    assert any("正文" in p for p in tampered["problems"]), tampered["problems"]

    body.write_bytes(original)
    restored = _evidence()
    assert restored["complete"] is True, restored["problems"]
    assert restored["bodies_verified"] == 1
    call_ledger.set_ledger_path(_prev)
