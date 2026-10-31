"""子进程验收：审计包启动入口的契约（M07/M08/M09，2026-10-28 / 2026-10-29）.

桩只替换**最底层 SDK**（sitecustomize 注入一个假的 openai 模块），因此
DeepSeekTextService 的参数构造、发送守卫（一次性授权占用 + attempt 记账）、
_LedgeredService 的 call_id/台账、真实投影、真实提交点与真实产物写入全部执行。
不在 generate() 层打桩：那一层在守卫之外，打成功能测试就等于绕开被验收的机制。
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
PACKAGE = ROOT / "tests/step01_script_gen/fixtures/step01_audit_min/audit_package.json"
ENTRY = ROOT / "tools/audit_frozen_candidate.py"

# R01（2026-10-30）：基础用例改用**仓库内自足夹具**，不再依赖 .p1a_exp 现场包。
# 夹具缺失属**依赖错误**，必须明确失败而不是 skip。
def _require_fixture():
    missing = [str(p) for p in (PACKAGE, PACKAGE.parent / "candidate.json",
                                PACKAGE.parent / "novel.txt") if not p.is_file()]
    if missing:
        pytest.fail("self-contained fixture missing: %s" % ", ".join(missing), pytrace=False)


_require_fixture()

SITECUSTOMIZE = r'''
import json
import os
import sys
import time
import types

# 测试不得依赖真实凭据：config.validate() 只要非空即可（请求不会真的发出去）。
os.environ.setdefault("DEEPSEEK_API_KEY", "stub-key")

# 授权登记根隔离：**只在测试进程内替换路径事实源**（P1，2026-10-29）。
# 生产代码不读任何环境变量——否则换一个空目录就能绕过已消费记录。
_TEST_AUTH_ROOT = os.environ.get("STEP01_TEST_AUTH_ROOT")
if _TEST_AUTH_ROOT:
    from pathlib import Path as _Path

    import shared.paths as _shared_paths

    _shared_paths.AUDIT_AUTHORIZATION_ROOT = _Path(_TEST_AUTH_ROOT)

# 旧变量必须**完全无效**（反例测试：首次消费后改它仍只能一次发送）
if os.environ.get("STEP01_TEST_PROVE_ENV_INERT") == "1":
    os.environ["STEP01_AUDIT_STATE_ROOT"] = os.environ["STEP01_TEST_AUTH_ROOT"] + "-evil"

_LOG = os.environ.get("STEP01_STUB_LOG")
_MODE = os.environ.get("STEP01_STUB_MODE") or ""


def _log(endpoint, params):
    if not _LOG:
        return
    keep = {k: v for k, v in params.items()
            if k in ("model", "stream", "reasoning", "max_output_tokens", "text",
                     "temperature", "top_p")}
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"endpoint": endpoint, "params": keep},
                            ensure_ascii=False, default=str) + "\n")


def _text():
    if _MODE == "blocking":
        return json.dumps({"issues": [{
            "shot_id": "shot_1", "severity": "blocking", "code": "stub_block",
            "message": "stub blocking", "evidence": "stub"}]})
    if _MODE == "garbage":
        return "not a json audit response"
    return json.dumps({"issues": []})


def _maybe_die_or_raise():
    delay = os.environ.get("STEP01_STUB_DELAY")
    if delay:
        time.sleep(float(delay))
    if _MODE == "crash_after_attempt":
        os._exit(9)
    if _MODE == "fail":
        raise ConnectionError("stub connect reset")


class _Usage:
    input_tokens = 120
    output_tokens = 40
    total_tokens = 160
    output_tokens_details = None


class _Block:
    type = "output_text"

    def __init__(self, text):
        self.text = text


class _Item:
    type = "message"

    def __init__(self, text):
        self.content = [_Block(text)]


class _Response:
    def __init__(self, text):
        self.status = "completed"
        self.output = [_Item(text)] if text else []
        self.usage = _Usage()
        self.error = None
        self.incomplete_details = None


class _Stream:
    def __init__(self, final):
        self._events = [types.SimpleNamespace(type="response.completed", response=final)]

    def __iter__(self):
        return iter(self._events)

    def close(self):
        pass


class _Responses:
    def create(self, **params):
        _log("responses.create", params)
        _maybe_die_or_raise()
        text = _text()
        final = _Response(text)
        if params.get("stream"):
            return _Stream(final)
        return final


class _ChatCompletions:
    def create(self, **params):
        _log("chat.completions.create", params)
        _maybe_die_or_raise()
        message = types.SimpleNamespace(content=_text(), reasoning_content=None)
        choice = types.SimpleNamespace(message=message, finish_reason="stop")
        return types.SimpleNamespace(choices=[choice], usage=_Usage())


class _Chat:
    completions = _ChatCompletions()


class OpenAI:
    def __init__(self, **kwargs):
        self.responses = _Responses()
        self.chat = _Chat()


_mod = types.ModuleType("openai")
_mod.OpenAI = OpenAI
sys.modules["openai"] = _mod

# 台账写失败注入（S12：已发送但证据缺失 ⇒ 不得冒充完整）
if os.environ.get("STEP01_STUB_DROP_LEDGER") == "1":
    import importlib.abc
    import importlib.machinery

    class _Loader(importlib.abc.Loader):
        def __init__(self, real):
            self.real = real

        def create_module(self, spec):
            return self.real.create_module(spec)

        def exec_module(self, module):
            self.real.exec_module(module)
            module._append_row = lambda *a, **k: False

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name != "shared.call_ledger":
                return None
            spec = importlib.machinery.PathFinder.find_spec(name, path)
            if spec is None or spec.loader is None:
                return spec
            spec.loader = _Loader(spec.loader)
            return spec

    sys.meta_path.insert(0, _Finder())

# 目录准备失败注入（S16：发送前拒收）
if os.environ.get("STEP01_STUB_PREPFAIL") == "1":
    from pathlib import Path

    _real_mkdir = Path.mkdir

    def _mkdir(self, *args, **kwargs):
        if self.name == "chapter_001":
            raise OSError("stub: directory denied before send")
        return _real_mkdir(self, *args, **kwargs)

    Path.mkdir = _mkdir
'''


def _pin_stub_channel(data: dict) -> dict:
    """把包副本钉回 **DeepSeek 通道 + 一次性额度**.

    2026-10-29 起真实包改为 agent_plan / 无限额度（用户决定）；本套件用 sitecustomize
    注入假 openai 模块验证发送边界，照抄真包会绕过该桩、真的打到 opencode 网关。
    因此这里只改**副本**：通道钉回被桩覆盖的那条，额度钉回一次性。
    """
    channel = dict(data.get("audit_channel") or {})
    if str(channel.get("provider")) != "deepseek":
        # 只在**不是** deepseek 时改回：用例自己要换 model（同一 provider）的反例
        # 必须保持原样，否则绑定摘要会被夹具抹平。
        channel.update({"provider": "deepseek", "model": "deepseek-flash"})
        data["audit_channel"] = channel
    data["budget"].pop("unlimited", None)
    # 规则身份也要钉到**当前源码**：包是历史冻结件（旧规则指纹），而入口会拿
    # 包内声明与当前源码比对。夹具若不钉，任何规则修正都会让整套入口用例
    # 以 rule_identity_mismatch 全军覆没——那不是"修法错了"。
    # 只钉**规则源码指纹**（静态）；提示版本依赖生效配置，必须由入口自己算，
    # 在测试进程里算会与子进程不一致。读取失败要**明确报错**，不能静默放过。
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
    data.setdefault("audit_contract", {})
    data["audit_contract"]["rule_source_fingerprint"] = RULE_SOURCE_FINGERPRINT
    return data


@pytest.fixture()
def package():
    return _pin_stub_channel(json.loads(PACKAGE.read_text(encoding="utf-8-sig")))


def _binding_digest(pkg_path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-B", str(ENTRY), "--package", str(pkg_path),
         "--print-binding-digest"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)["binding_digest"]


def _grant(data: dict, pkg: Path) -> None:
    """按**当前包内容**登记一次性授权（绑定摘要由入口自己算，测试不手填）."""
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    digest = _binding_digest(pkg)
    data["budget"]["new_authorization"] = {
        "authorization_id": "pytest-fixture-auth",
        "granted_by": "pytest-fixture",
        "granted_at": "2026-10-29T00:00:00+08:00",
        "max_sends": 1,
        "binding_digest": digest,
    }
    pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _run(tmp_path, package_obj, mode="ok", *, authorized=True, mutate=None,
         out_root=None, extra_env=None, budget_remaining=1, prepfail=False,
         dry_run=False, state_root=None, grant=True):
    tag = "%s_%s" % (os.getpid(), time.time_ns())
    stub = tmp_path / ("stub_%s" % tag)
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    data = _pin_stub_channel(json.loads(json.dumps(package_obj)))
    data["budget"]["remaining"] = budget_remaining
    if mutate:
        mutate(data)
    pkg = tmp_path / ("pkg_%s.json" % tag)
    if budget_remaining > 0 and grant:
        data["budget"]["new_authorization"] = {
            "authorization_id": "", "granted_by": "", "granted_at": "",
            "max_sends": 0, "binding_digest": ""}
        _grant(data, pkg)
    else:
        # grant=False：调用方已自行构造授权/篡改字段，不能再覆盖（否则反例被抹平）。
        pkg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log = tmp_path / ("sends_%s.log" % tag)
    reqlog = tmp_path / ("reqs_%s.jsonl" % tag)
    out = Path(out_root) if out_root else (tmp_path / ("run_%s" % tag))
    env = dict(os.environ)
    env["PYTHONPATH"] = "%s%s%s" % (stub, os.pathsep, ROOT)
    env["STEP01_STUB_MODE"] = mode
    env["STEP01_STUB_LOG"] = str(log)
    env["STEP01_STUB_REQ"] = str(reqlog)
    env["STEP01_TEST_AUTH_ROOT"] = str(
        Path(state_root) if state_root else (tmp_path / "auth_state"))
    # 子进程 stdout 必须是 UTF-8：Windows 默认 GBK 会让父进程按 UTF-8 解出替换字符，
    # 拒收原因/证据问题这类中文文本就不可读了（不影响 JSON 结构，但影响取证）。
    env["PYTHONIOENCODING"] = "utf-8"
    if prepfail:
        env["STEP01_STUB_PREPFAIL"] = "1"
    env.pop("STEP01_AUDIT_AUTHORIZED", None)
    if authorized:
        env["STEP01_AUDIT_AUTHORIZED"] = "1"
    env.update(extra_env or {})
    cmd = [sys.executable, "-B", str(ENTRY), "--package", str(pkg),
           "--out-root", str(out)]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    log_lines = (log.read_text(encoding="utf-8").splitlines() if log.is_file() else [])
    sends = len(log_lines)
    reqs = [json.loads(ln) for ln in log_lines if ln.strip()]
    lines = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.startswith("{")]
    payload = json.loads(lines[-1]) if lines else {}
    return proc, payload, sends, out, reqs


def _ledger(payload):
    path = Path(payload["call_ledger"])
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()] if path.is_file() else []


def test_success_commits_with_exactly_one_send_and_one_ledger_row(tmp_path, package):
    proc, payload, sends, out, reqs = _run(tmp_path, package, "ok")
    assert proc.returncode == 0, (proc.stdout[-800:], proc.stderr[-800:])
    assert payload["status"] == "COMMITTED", payload
    assert payload["revision"] == package["object"]["revision"], payload
    assert sends == 1, "必须恰好一次发送"
    assert payload["send_state"] == "returned", payload
    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        assert payload["artifacts"][name]["exists"], (name, payload["artifacts"])
    assert str(payload["chapter_dir"]).startswith(str(out)), "章节目录必须落在隔离根内"
    assert reqs, "桩必须收到请求"
    # 真实 payload（不只是环境变量）：Responses 路径 + 单次发送开关生效
    assert reqs[0]["endpoint"] == "responses.create", reqs
    assert reqs[0]["params"]["model"] == "deepseek-flash", reqs

    rows = _ledger(payload)
    assert len(rows) == 1, rows
    rec = rows[0]
    assert payload["call_rows"] == 1
    assert rec["provider"] == "deepseek" and rec["model"] == "deepseek-flash"
    assert rec["call_id"] and rec["call_id"] in payload["evidence"]["call_ids"]
    # S07：授权 → attempt → call_id 可关联（不是从日志文本猜）
    assert payload["evidence"]["attempt_call_ids"] == [rec["call_id"]], payload["evidence"]
    assert payload["authorization"]["attempts"] == 1
    assert payload["authorization"]["claim_run_id"] == payload["authorization"]["claim_run_id"]
    body = Path(rec["response_path"])
    assert body.is_file()
    assert hashlib.sha1(body.read_bytes()).hexdigest() == rec["response_sha1"]
    assert payload["evidence"]["complete"] is True, payload["evidence"]
    assert payload["evidence"]["bodies_verified"] == 1
    rounds_path = Path(payload["chapter_dir"]) / "step01_audit_rounds.json"
    rounds = json.loads(rounds_path.read_text(encoding="utf-8"))
    assert rounds and payload["evidence"]["audit_rounds"] >= 1
    assert set(rounds[0]["run_id"].split()) & set(rec["run_id"].split())
    assert any(r.get("input_revision") == payload["revision"] for r in rounds)
    # 收据：发送数来自发送边界的 attempt 记录，不是硬编码 1
    receipt = json.loads(Path(payload["budget_receipt"]).read_text(encoding="utf-8"))
    assert receipt["sends"] == 1 and receipt["sends_known"] is True, receipt
    assert receipt["send_state"] == "returned", receipt
    assert receipt["remaining_after"] == 0
    assert receipt["revision"] == payload["revision"]
    assert receipt["authorization"]["claimed"] is True
    assert receipt["attempt_ids"] == payload["evidence"]["attempt_ids"]


def test_streaming_path_is_also_guarded(tmp_path, package):
    proc, payload, sends, out, reqs = _run(
        tmp_path, package, "ok", extra_env={"DEEPSEEK_STREAM": "1"})
    assert proc.returncode == 0, (proc.stdout[-900:], proc.stderr[-500:])
    assert sends == 1, "流式分支同样只能一次发送"
    assert reqs[0]["params"].get("stream") is True, reqs
    assert payload["send_state"] == "returned", payload
    assert payload["evidence"]["complete"] is True, payload["evidence"]


def test_blocking_audit_stops_without_repair_and_keeps_evidence(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "blocking")
    assert proc.returncode == 3, (proc.stdout[-800:], proc.stderr[-800:])
    assert payload["status"] == "BLOCK", payload
    assert payload["summary"]["repair_outcome"] == "audit_only", payload
    assert sends == 1, "blocking 后不得再发第二次审计"
    assert not payload["artifacts"]["script_result.json"]["exists"], "BLOCK 不得写正式产物"
    assert payload["call_rows"] == 1 and payload["evidence"]["complete"] is True


def test_audit_call_failure_blocks_without_resend(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "fail")
    assert proc.returncode == 3, (proc.stdout[-800:], proc.stderr[-800:])
    assert payload["status"] == "BLOCK", payload
    assert sends == 1, "调用失败不得重发"
    assert payload["send_state"] == "failed", payload
    assert not payload["artifacts"]["script_result.json"]["exists"]
    assert payload["call_rows"] == 1, "失败调用也要有一条可读记录"
    assert payload["evidence"]["complete"] is True
    # 授权不自动释放：同一授权第二次启动必须被拒
    proc2, payload2, sends2, _out2, _ = _run(tmp_path, package, "ok")
    assert sends2 == 0
    assert proc2.returncode == 2 and payload2["code"] == "authorization_already_claimed", payload2


def test_unparseable_response_is_recorded_once(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "garbage")
    assert proc.returncode == 3, (proc.stdout[-800:], proc.stderr[-800:])
    assert payload["status"] == "BLOCK"
    assert sends == 1, "解析失败不得重发"
    assert payload["call_rows"] == 1 and payload["evidence"]["complete"] is True


def test_preparation_failure_is_zero_send(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", prepfail=True)
    assert proc.returncode == 2, (proc.stdout[-900:], proc.stderr[-400:])
    assert payload["code"] == "preparation_failed", payload
    assert sends == 0, "目录准备失败必须零发送"
    assert payload.get("send_state") == "not_sent"
    assert not list(Path(out).rglob("claim.json")), "零发送不得留下授权占用"


def test_budget_zero_refuses_execute_without_sending(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", budget_remaining=0)
    assert proc.returncode == 2, (proc.stdout[-900:], proc.stderr[-400:])
    assert payload["code"] == "budget_exhausted", payload
    assert sends == 0, "剩余额度为 0 时 -Execute 也不得发送"
    assert not out.exists(), "额度拒收不得创建运行根"


def test_dry_run_allows_zero_budget_and_sends_nothing(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", dry_run=True,
                                        budget_remaining=0)
    assert proc.returncode == 0, (proc.stdout[-800:], proc.stderr[-800:])
    assert payload["status"] == "VALIDATED" and payload["would_send"] == 0
    assert sends == 0
    assert payload["authorization"]["send_state"] == "unregistered", payload["authorization"]
    chapter_dirs = [p for p in out.rglob("chapter_001") if p.is_dir()]
    assert chapter_dirs == [], "dry-run 不创建章节目录"
    assert not list(out.rglob("step01_calls.jsonl"))


def test_dry_run_never_claims_authorization(tmp_path, package):
    state = tmp_path / "auth_state"
    proc, payload, sends, _out, _ = _run(tmp_path, package, "ok", dry_run=True,
                                         state_root=state)
    assert proc.returncode == 0, proc.stdout[-600:]
    assert sends == 0
    assert not list(Path(state).rglob("claim.json")), "dry-run 不得占用授权"


def test_rule_identity_mismatch_is_refused_before_send(tmp_path, package):
    def corrupt(data):
        data["audit_contract"]["audit_prompt_version"] = "0" * 12
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=corrupt)
    assert proc.returncode == 2, (proc.stdout[-900:], proc.stderr[-400:])
    assert payload["code"] == "rule_identity_mismatch", payload
    assert sends == 0, "规则身份不符必须零发送"


def test_digest_mismatch_is_refused_before_any_send(tmp_path, package):
    def corrupt(data):
        data["object"]["candidate_sha256"] = "0" * 64
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=corrupt)
    assert proc.returncode == 2, (proc.stdout[-600:], proc.stderr[-600:])
    assert payload["code"] == "candidate_identity", payload
    assert sends == 0, "身份不符必须零发送"
    assert not out.exists(), "拒收不得创建运行根"


def test_missing_authorization_is_refused_before_any_send(tmp_path, package):
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", authorized=False)
    assert proc.returncode == 2, (proc.stdout[-600:], proc.stderr[-600:])
    assert payload["code"] == "authorization_required"
    assert sends == 0
    assert not out.exists()


@pytest.mark.parametrize("field", ["candidate_sha256", "novel_sha256", "quality_sha256"])
@pytest.mark.parametrize("bad", ["", "   ", "abc", 12345, None])
def test_invalid_digest_is_refused(tmp_path, package, field, bad):
    def corrupt(data):
        data["object"][field] = bad
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=corrupt)
    assert proc.returncode == 2, (field, bad, proc.stdout[-400:])
    assert payload["code"].endswith("_identity"), payload
    assert sends == 0 and not out.exists()


@pytest.mark.parametrize("key,bad", [("TEXT_TRANSPORT_RETRIES", "1"),
                                     ("AUDIT_PARSE_RETRIES", " "),
                                     ("REPAIR_ROUNDS_ENABLED", "yes"),
                                     ("STEP01_AUDIT_ONLY", "0"),
                                     ("TEXT_TRANSPORT_CONNECT_RETRIES", 0)])
def test_invalid_send_control_is_refused(tmp_path, package, key, bad):
    def corrupt(data):
        data["single_send_controls"][key] = bad
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=corrupt)
    assert proc.returncode == 2, (key, bad, proc.stdout[-400:])
    assert payload["code"] == "send_controls_invalid", payload
    assert sends == 0 and not out.exists()


def test_unsupported_channel_is_refused_without_send(tmp_path, package):
    def other_channel(data):
        data["audit_channel"]["provider"] = "dashscope"
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=other_channel)
    assert proc.returncode == 2, proc.stdout[-600:]
    assert payload["code"] == "audit_channel_unsupported", payload
    assert sends == 0, "未接守卫的通道必须零发送拒收（不得静默绕开授权）"
    assert not out.exists()


def test_existing_run_root_is_refused(tmp_path, package):
    out = tmp_path / "already"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    proc, payload, sends, _, _ = _run(tmp_path, package, "ok", out_root=out)
    assert proc.returncode == 2 and payload["code"] == "out_root_invalid", payload
    assert sends == 0
    assert (out / "keep.txt").read_text(encoding="utf-8") == "x", "不得改动已有目录"


def test_production_output_root_is_refused(tmp_path, package):
    proc, payload, sends, _, _ = _run(tmp_path, package, "ok",
                                      out_root=ROOT / "output" / "audit_should_not_write")
    assert proc.returncode == 2 and payload["code"] == "out_root_invalid", payload
    assert sends == 0
    assert not (ROOT / "output" / "audit_should_not_write").exists()


def test_scope_chapter_is_honoured(tmp_path, package):
    def chapter_two(data):
        data["scope"]["chapter"] = 2
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=chapter_two)
    assert proc.returncode == 0, (proc.stdout[-800:], proc.stderr[-800:])
    assert str(payload["chapter_dir"]).endswith("chapter_002"), payload["chapter_dir"]
    assert package["scope"]["chapter"] == 1, "原包范围不得被测试反例污染"


def test_foreign_output_env_is_overridden(tmp_path, package):
    proc, payload, sends, out, _ = _run(
        tmp_path, package, "ok",
        extra_env={"OUTPUT_BASE": str(ROOT / "output"),
                   "STEP01_LOG_PATH": str(ROOT / "output" / "x.log"),
                   "TEXT_CALL_LEDGER": str(ROOT / "output" / "x.jsonl")})
    assert proc.returncode == 0, (proc.stdout[-800:], proc.stderr[-800:])
    assert str(payload["chapter_dir"]).startswith(str(out)), payload["chapter_dir"]
    assert str(ROOT / "output") not in payload["call_ledger"], payload["call_ledger"]


def test_missing_plan_is_refused(tmp_path, package):
    cand = json.loads((ROOT / package["object"]["candidate"]).read_text(encoding="utf-8-sig"))
    cand.pop("chapter_outline", None)
    stripped = tmp_path / "no_plan.json"
    stripped.write_text(json.dumps(cand, ensure_ascii=False), encoding="utf-8")
    sha = hashlib.sha256(stripped.read_bytes()).hexdigest()

    def mutate(data):
        data["object"]["candidate"] = str(stripped)
        data["object"]["candidate_sha256"] = sha
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=mutate)
    assert proc.returncode == 2 and payload["code"] == "plan_missing", payload
    assert sends == 0


def test_tampered_candidate_is_refused_before_any_send(tmp_path, package):
    """改过内容（删第 1 镜对白/描述）⇒ revision 变化 ⇒ 审计前拒收（0 发送）."""
    cand_path = ROOT / package["object"]["candidate"]
    cand = json.loads(cand_path.read_text(encoding="utf-8-sig"))
    target = cand["shots"][0]
    target["narration_segment"] = "临时改动：这一句改变投影"
    target["subtitle"] = "临时改动：这一句改变投影"
    broken = tmp_path / "broken_dialogue.json"
    broken.write_text(json.dumps(cand, ensure_ascii=False), encoding="utf-8")
    sha = hashlib.sha256(broken.read_bytes()).hexdigest()

    def mutate(data):
        data["object"]["candidate"] = str(broken)
        data["object"]["candidate_sha256"] = sha
    proc, payload, sends, out, _ = _run(tmp_path, package, "ok", mutate=mutate)
    assert proc.returncode == 2, (proc.stdout[-900:], proc.stderr[-400:])
    assert payload["code"] == "revision_mismatch", payload
    assert sends == 0, "对象变了就必须零发送"


def test_stale_rule_fingerprint_is_refused(tmp_path, package):
    """旧规则指纹（源码已变）必须在发送前被拒——与"错提示版本"是两条独立负例."""
    def _stale(data):
        data["audit_contract"]["rule_source_fingerprint"] = "0" * 16
    proc, payload, sends, _out, _ = _run(tmp_path, package, "ok", mutate=_stale)
    assert proc.returncode == 2, (proc.stdout[-600:], proc.stderr[-300:])
    assert payload["code"] == "rule_identity_mismatch", payload
    assert sends == 0


def test_tampered_binding_digest_is_refused(tmp_path, package):
    """篡改绑定摘要（不改对象）必须在发送前被拒，且不得自动校正."""
    def _tamper(data):
        data["budget"]["new_authorization"] = {
            "authorization_id": "tamper-fixture", "granted_by": "pytest",
            "granted_at": "2026-10-30T00:00:00+08:00", "max_sends": 1,
            "binding_digest": "0" * 32,
        }
    proc, payload, sends, _out, _ = _run(tmp_path, package, "ok", mutate=_tamper, grant=False)
    assert proc.returncode == 2, (proc.stdout[-600:], proc.stderr[-300:])
    assert payload["code"] == "authorization_binding_mismatch", payload
    assert sends == 0
