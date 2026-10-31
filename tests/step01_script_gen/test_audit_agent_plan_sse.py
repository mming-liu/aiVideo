"""agent_plan + SSE 的**真实入口**验收（2026-10-29）.

走真实审计入口、真实 provider、真实授权登记与台账；只在**底层 HTTP** 打桩
（替换 requests.post）。不把通道改回 deepseek 来证明 agent_plan。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "tools/audit_frozen_candidate.py"
PACKAGE = ROOT / "tests/step01_script_gen/fixtures/step01_audit_min/audit_package.json"
STUB = ROOT / "tests" / "_text_gen" / "_sse_stub_sitecustomize.py"

def _require_fixture():
    missing = [str(p) for p in (PACKAGE, ENTRY, STUB) if not p.is_file()]
    if missing:
        pytest.fail("self-contained fixture missing: %s" % ", ".join(missing), pytrace=False)


_require_fixture()


def _run(tmp_path, *, mode, out="run", timeout_sec=None, auth_id=None, extra_env=None):
    tag = "%s_%s" % (os.getpid(), time.time_ns())
    stub_dir = tmp_path / ("stub_%s" % tag)
    stub_dir.mkdir()
    shutil.copyfile(STUB, stub_dir / "sitecustomize.py")
    data = json.loads(PACKAGE.read_text(encoding="utf-8-sig"))
    # 本套件验证"无限额度"路径：夹具包本身是一次性额度，这里显式打开
    data.setdefault("budget", {})["unlimited"] = True
    # 规则身份钉到当前源码（同 entry 夹具）：包是历史冻结件，规则修正后旧指纹不再匹配。
    try:
        from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
        data.setdefault("audit_contract", {})
        data["audit_contract"]["rule_source_fingerprint"] = RULE_SOURCE_FINGERPRINT
    except Exception:
        pass
    if auth_id:
        data["budget"]["new_authorization"]["authorization_id"] = auth_id
    pkg = tmp_path / ("pkg_%s.json" % tag)
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 改了规则指纹就必须**重签**绑定摘要，否则入口按旧摘要拒收。
    _p = subprocess.run(
        [sys.executable, "-B", str(ENTRY), "--package", str(pkg), "--print-binding-digest"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60)
    assert _p.returncode == 0, _p.stdout + _p.stderr
    data["budget"].setdefault("new_authorization", {})["binding_digest"] =         json.loads(_p.stdout)["binding_digest"]
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    http_log = tmp_path / ("http_%s.log" % tag)
    out_root = tmp_path / out
    env = dict(os.environ)
    env["PYTHONPATH"] = str(stub_dir) + os.pathsep + str(ROOT)
    env["STEP01_STUB_SSE_MODE"] = mode
    env["STEP01_STUB_HTTP_LOG"] = str(http_log)
    env["STEP01_TEST_AUTH_ROOT"] = str(tmp_path / "auth_state")
    env["STEP01_AUDIT_AUTHORIZED"] = "1"
    env["AGENT_PLAN_TEXT_STREAM"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if timeout_sec is not None:
        # 审计自身的预算才是请求级 timeout 的来源；只设 provider 常量不会生效。
        env["AGENT_PLAN_TEXT_TIMEOUT_SEC"] = str(timeout_sec)
        env["AUDIT_TIMEOUT_SEC"] = str(timeout_sec)
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-B", str(ENTRY), "--package", str(pkg), "--out-root", str(out_root)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("{")]
    payload = json.loads(lines[-1]) if lines else {}
    http = (len(http_log.read_text(encoding="utf-8").splitlines())
            if http_log.is_file() else 0)
    state = tmp_path / "auth_state" / "audit_authorizations"
    attempts = sum(len(p.read_text(encoding="utf-8").splitlines())
                   for p in state.glob("*/attempts.jsonl"))
    claims = len(list(state.glob("*/claim.json")))
    requests_seen = ([json.loads(ln) for ln in http_log.read_text(encoding="utf-8").splitlines()]
                     if http_log.is_file() else [])
    return proc, payload, http, attempts, claims, out_root, requests_seen


def test_sse_success_sends_once_and_records_returned(tmp_path):
    proc, payload, http, attempts, claims, out, reqs = _run(tmp_path, mode="sse_ok")
    assert proc.returncode == 0, (proc.stdout[-1000:], proc.stderr[-800:])
    assert http == 1, "SSE 成功路径必须恰好一次 HTTP"
    assert attempts == 1 and claims == 1
    assert payload["send_state"] == "returned", payload
    assert payload["evidence_complete"] is True
    assert reqs and reqs[0]["stream"] is True, "payload 必须显式请求协议流式"
    assert "stream_options" in reqs[0]["keys"]
    receipt = json.loads(Path(payload["budget_receipt"]).read_text(encoding="utf-8"))
    assert receipt["sends"] == 1 and receipt["send_state"] == "returned", receipt
    # 消费入口读回（M10-46 / F6）：走真实质量门，而不是自己猜字段名。
    chapter = Path(payload["chapter_dir"])
    commit = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert commit.get("script_revision") == payload["revision"], commit
    result = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    assert result.get("script_revision") == payload["revision"], result.get("script_revision")
    # 真实消费者必须在**与运行同一个配置环境**里核验；本进程的 config 早已导入，
    # 改 env 不会生效，故放到子进程里跑质量门（F6）。
    _code = (
        "import json,sys;from pathlib import Path;"
        "from shared.quality_gate import require_step01_upstream;"
        "ch=Path(sys.argv[1]);"
        "res=json.loads((ch/'script_result.json').read_text(encoding='utf-8'));"
        "require_step01_upstream(ch,res,stage='step01',expected_chapter=1);print('OK')"
    )
    _env = dict(os.environ)
    _env.update({k: str(v) for k, v in
                 (json.loads(PACKAGE.read_text(encoding="utf-8-sig")).get("effective_config") or {}).items()})
    _env["PYTHONPATH"] = str(ROOT)
    _gate = subprocess.run([sys.executable, "-c", _code, str(chapter)], cwd=str(ROOT),
                           env=_env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    assert _gate.returncode == 0 and "OK" in _gate.stdout, (_gate.stdout[-400:], _gate.stderr[-400:])


def test_sse_without_terminal_event_is_not_a_result(tmp_path):
    proc, payload, http, attempts, claims, out, _ = _run(tmp_path, mode="sse_truncated")
    assert proc.returncode == 3, (proc.stdout[-1000:], proc.stderr[-500:])
    assert http == 1 and attempts == 1, "缺终态也必须记下真实发生的一次尝试"
    assert payload["send_state"] == "unknown", payload
    assert payload["status"] != "COMMITTED"
    assert not (Path(payload["chapter_dir"]) / "step01_commit.json").exists() or \
        payload.get("commit_status") != "COMMITTED"


def test_sse_error_event_is_not_a_result(tmp_path):
    proc, payload, http, attempts, claims, out, _ = _run(tmp_path, mode="sse_error_event")
    assert proc.returncode == 3, (proc.stdout[-800:], proc.stderr[-400:])
    assert http == 1 and attempts == 1
    assert payload["send_state"] == "unknown", payload


# 「发送前到期 ⇒ 零 HTTP / 零 attempt / 无 claim」由 tests/_text_gen/test_agent_plan_guard.py
# 的 test_expired_deadline_sends_nothing_and_claims_nothing 用**真闸门 + 已到期截止时刻**覆盖；
# 入口层拿不到请求级 deadline_monotonic，故不在此伪造。


def test_duplicate_start_does_not_resend(tmp_path):
    auth_id = "sse-dup-%s" % time.time_ns()
    first = _run(tmp_path, mode="sse_ok", out="run1", auth_id=auth_id)
    second = _run(tmp_path, mode="sse_ok", out="run2", auth_id=auth_id)
    assert first[0].returncode == 0, first[0].stdout[-800:]
    assert second[0].returncode == 2, second[0].stdout[-800:]
    assert second[1].get("code") == "authorization_already_claimed", second[1]
    assert first[2] + second[2] == 1, "同一授权累计只允许一次 HTTP"
    assert second[3] == first[3] == 1, "attempts 是累计读数，不得重复累加"


def test_sse_malformed_event_is_rejected(tmp_path):
    """多 choice（违反单输出合同）⇒ 不采纳，记 unknown."""
    proc, payload, http, attempts, claims, out, _ = _run(tmp_path, mode="sse_bad_shape")
    assert proc.returncode == 3, (proc.stdout[-900:], proc.stderr[-400:])
    assert http == 1 and attempts == 1
    assert payload["send_state"] == "unknown", payload


def test_sse_mid_stream_timeout_is_bounded(tmp_path):
    """流中停滞：按墙钟返回、只发一次、记 unknown."""
    started = time.monotonic()
    proc, payload, http, attempts, claims, out, _ = _run(
        tmp_path, mode="sse_mid_timeout", timeout_sec=3)
    elapsed = time.monotonic() - started
    assert proc.returncode == 3, (proc.stdout[-900:], proc.stderr[-400:])
    assert http == 1 and attempts == 1, (http, attempts)
    assert payload["send_state"] == "unknown", payload
    assert elapsed < 120, elapsed


def test_sse_head_only_timeout_is_bounded(tmp_path):
    """响应头到、正文一直不来：同样按墙钟返回且不重发."""
    proc, payload, http, attempts, claims, out, _ = _run(
        tmp_path, mode="sse_head_only", timeout_sec=3)
    assert proc.returncode == 3, (proc.stdout[-900:], proc.stderr[-400:])
    assert http == 1 and attempts == 1
    assert payload["send_state"] == "unknown", payload


def test_sse_heartbeats_do_not_extend_budget(tmp_path):
    """持续心跳（每 0.1s 一块、永远无终态）不得延展总预算：仍按墙钟返回。"""
    started = time.monotonic()
    proc, payload, http, attempts, claims, out, _ = _run(
        tmp_path, mode="sse_heartbeat_forever", timeout_sec=3)
    elapsed = time.monotonic() - started
    assert proc.returncode == 3, (proc.stdout[-900:], proc.stderr[-400:])
    assert http == 1 and attempts == 1
    assert payload["send_state"] == "unknown", payload
    assert elapsed < 90, elapsed
