# -*- coding: utf-8 -*-
"""P4 观察工具的**停止守卫 / 冻结一致性**离线验收（0 次网络、0 次真实调用）.

覆盖 `tools/p4_stability_observe.py` 通过 `run()` 实际执行的六类场景：

1. 首次协议失败 ⇒ 只观察 1 次，无第二次发送；
2. 第二次协议失败 ⇒ 只观察 2 次，无第三次发送；
3. 三次均准入 ⇒ 恰好 3 次，无第四次；
4. 准入记录含 `partial` ⇒ 继续观察（语义判负不触发协议止损）；
5. 调用异常 / 观察异常 / 证据写入失败 ⇒ 停止、保留已有证据、不继续发送；
6. 输入或实际请求摘要不符 ⇒ 在受影响请求发送前拒绝（含 0 次发送）。

桩打在**实际 HTTP 边界** `AgentPlanTextService._send_once` 上（先于工具自己的
发送守卫安装），因此端点守卫、提示摘要核对、payload 冻结与预算计数都被真实执行；
桩的命中次数就是物理发送次数。样本取 `tools/samples/p3_split_entry_a.json`（匿名、
受控），落成临时文件后交给 `run()`，不读 `output/` 现场产物。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                                # noqa: E402
from shared import presentation_contract as pc                               # noqa: E402
from step01_script_gen import content_verify as V                            # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen.source_references import catalog_for                   # noqa: E402
from tests.step01_script_gen import p3_entry_fixtures as fx                   # noqa: E402

_TOOL_PATH = _ROOT / "tools" / "p4_stability_observe.py"
_spec = importlib.util.spec_from_file_location("p4_stability_observe_under_test",
                                              _TOOL_PATH)
tool = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tool
_spec.loader.exec_module(tool)

BASE_URL = "https://opencode.ai/inference/go/openai/v1"


# ---------------------------------------------------------------------------
# 受控样本 / 冻结输入
# ---------------------------------------------------------------------------
_ENV_KEYS = ("OUTPUT_BASE", "STEP01_LOG_PATH", "TEXT_TRANSPORT_RETRIES",
             "TEXT_TRANSPORT_CONNECT_RETRIES", "CONTENT_VERIFY_MAX_TOKENS")


@pytest.fixture()
def workdir():
    """独立临时根（**不用 `tmp_path`**：该 fixture 以 0700 建目录，本机沙箱下
    子文件写入被拒；也不放仓库目录，避免测试产物留在工作树）。结束后恢复 `run()`
    改写的进程环境，并尽力删除本用例产物（日志句柄可能仍被进程持有）。"""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    base = Path(tempfile.gettempdir()) / ("p4_guard_" + uuid.uuid4().hex[:8])
    base.mkdir(parents=True, exist_ok=False)
    try:
        yield base
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(base, ignore_errors=True)


def _sha256(text) -> str:
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _inputs(workdir) -> dict:
    sample = fx.load_sample("a")
    plan = o.normalize_plan_ranges(copy.deepcopy(sample["plan"]))[0]
    plan.pop("plan_verification", None)
    novel = str(sample["novel"])
    plan_path = workdir / "candidate_content.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    novel_path = workdir / "novel.txt"
    novel_path.write_text(novel, encoding="utf-8")
    view, vp = pc.view_from_plan(plan)
    assert vp == [], vp
    catalog = catalog_for(novel)
    prompt = V.build_verification_prompt(
        view, novel_text=novel, novel_digest=_sha256(novel),
        novel_version="", source_catalog=catalog)
    return {"plan_path": plan_path, "novel_path": novel_path,
            "exp_root": workdir / "exp", "view": view,
            "digest": cv.plan_digest_of(plan), "novel": novel,
            "novel_digest": _sha256(novel), "prompt_sha256": _sha256(prompt),
            "ref": next(iter(catalog)),
            "parent": str(sample["split"]["parent_uid"])}


# ---------------------------------------------------------------------------
# 桩：响应体 / 配置 / 物理发送边界
# ---------------------------------------------------------------------------
class _FakeResponse:
    status_code = 200
    headers: dict = {}

    def __init__(self, content: str):
        self._content = content

    def json(self) -> dict:
        return {"id": "p4-offline-stub", "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": self._content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}

    @property
    def text(self) -> str:
        return json.dumps(self.json(), ensure_ascii=False)


def _raw_ok(inputs: dict, *, partial=()) -> str:
    """生产同源记录（`record_for`）包成模型原始响应；来源片段用本次原文目录编号."""
    record = fx.record_for(inputs["view"], inputs["digest"], partial=partial,
                           novel=inputs["novel"])
    for row in record.get("source_support") or []:
        row["evidence_spans"] = [inputs["ref"]]
    return json.dumps(record, ensure_ascii=False)


def _raw_bad() -> str:
    return json.dumps({"facts": [], "source_support": [], "voice_units": [],
                       "coherence": {}}, ensure_ascii=False)


def _patch_channel(monkeypatch) -> None:
    from _text_gen.config import config
    monkeypatch.setattr(config, "PROVIDER", "agent_plan", raising=False)
    monkeypatch.setattr(config, "AGENT_PLAN_TEXT_BASE_URL", BASE_URL, raising=False)
    monkeypatch.setattr(config, "AGENT_PLAN_BASE_URL", BASE_URL, raising=False)
    monkeypatch.setattr(config, "AGENT_PLAN_API_KEY", "p4-offline-stub-key",
                        raising=False)
    monkeypatch.setattr(config, "AGENT_PLAN_TEXT_MODEL", "deepseek-v4.1-flash",
                        raising=False)
    monkeypatch.setattr(config, "AGENT_PLAN_TEXT_RPM", 10 ** 6, raising=False)
    monkeypatch.setattr(config, "AGENT_PLAN_SESSION_ID", "p4-offline-session",
                        raising=False)


def _install_stub(monkeypatch, contents: list) -> list:
    from _text_gen.agent_plan_provider import AgentPlanTextService
    calls: list = []

    def _send_once(self, url, payload, timeout, trace, body=None):
        calls.append({"url": str(url), "payload": payload, "body": body})
        item = contents[min(len(calls) - 1, len(contents) - 1)]
        if isinstance(item, BaseException):
            raise item
        return _FakeResponse(str(item))

    monkeypatch.setattr(AgentPlanTextService, "_send_once", _send_once)
    return calls


def _invoke(inputs: dict, *, runs=3, expect=None, exp_root=None):
    expect = expect or {}
    return tool.run(
        plan_path=inputs["plan_path"], novel_path=inputs["novel_path"],
        runs=runs, root=exp_root or inputs["exp_root"],
        expect_plan_digest=expect.get("plan_digest", inputs["digest"]),
        expect_novel_digest=expect.get("novel_digest", inputs["novel_digest"]),
        expect_prompt_sha256=expect.get("prompt_sha256", inputs["prompt_sha256"]))


def _stability(res: dict) -> dict:
    return json.loads((Path(res["run_dir"]) / "stability.json")
                      .read_text(encoding="utf-8"))


def _assert_hits(calls: list, expected: int, note: str) -> None:
    """桩命中次数 = 物理发送次数；`-s` 运行时留一份可核对的命中记录."""
    print("[stub-hits] %-18s %d/%d" % (note, len(calls), expected))
    assert len(calls) == expected, note


# ---------------------------------------------------------------------------
# 1–2：协议失败即停
# ---------------------------------------------------------------------------
def test_first_protocol_failure_stops_before_any_second_send(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    # 第二、三条是合法响应：若循环没有停，它们会被发出去。
    calls = _install_stub(monkeypatch, [_raw_bad(), _raw_ok(inputs), _raw_ok(inputs)])

    res = _invoke(inputs)

    _assert_hits(calls, 1, "首次协议失败")
    assert res["sends"] == 1 and res["observed_runs"] == 1
    assert res["planned_runs"] == 3 and res["stopped_early"] is True
    assert res["stop_reason"] == "protocol_failure"
    assert res["runs"][0]["status"] == "bad_response"
    run_dir = Path(res["run_dir"])
    assert not (run_dir / "sends" / "send_02_request.json").exists()
    assert _stability(res)["stop_reason"] == "protocol_failure"
    assert "stopped（protocol_failure）" in _stability(res)["conclusion"]


def test_second_protocol_failure_stops_before_third_send(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs), _raw_bad(), _raw_ok(inputs)])

    res = _invoke(inputs)

    _assert_hits(calls, 2, "第二次协议失败")
    assert res["sends"] == 2 and res["observed_runs"] == 2
    assert [r["status"] for r in res["runs"]] == ["ok", "bad_response"]
    assert res["stop_reason"] == "protocol_failure"
    assert not (Path(res["run_dir"]) / "sends" / "send_03_request.json").exists()


# ---------------------------------------------------------------------------
# 3–4：全部准入才继续；语义判负不算协议失败
# ---------------------------------------------------------------------------
def test_three_admitted_observations_stop_at_planned_runs(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 4)

    res = _invoke(inputs)

    _assert_hits(calls, 3, "三次均准入")
    assert res["sends"] == 3 and res["observed_runs"] == 3
    assert res["stop_reason"] == "completed" and res["stopped_early"] is False
    assert [r["status"] for r in res["runs"]] == ["ok", "ok", "ok"]
    assert res["verdict_differences"] == []
    assert not (Path(res["run_dir"]) / "sends" / "send_04_request.json").exists()


def test_partial_verdicts_do_not_trigger_protocol_stop(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    partial = _raw_ok(inputs, partial=(inputs["parent"],))
    calls = _install_stub(monkeypatch, [partial] * 4)

    res = _invoke(inputs)

    _assert_hits(calls, 3, "partial 继续")
    assert res["stop_reason"] == "completed"
    assert all(r["status"] == "ok" for r in res["runs"])
    assert res["runs"][0]["axes"]["facts"][inputs["parent"]]["verdict"] == "partial"


# ---------------------------------------------------------------------------
# 5：调用异常 / 观察异常 / 证据写入失败
# ---------------------------------------------------------------------------
def test_call_failure_stops_and_keeps_prior_evidence(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch,
                          [_raw_ok(inputs), RuntimeError("stub transport down")])

    res = _invoke(inputs)

    _assert_hits(calls, 2, "调用异常")
    assert res["runs"][1]["status"] == "call_failed"
    assert res["stop_reason"] == "call_failure"
    assert res["sends"] == 2
    run_dir = Path(res["run_dir"])
    assert (run_dir / "runs" / "run_1" / "record.json").is_file()
    assert not (run_dir / "sends" / "send_03_request.json").exists()


def test_observation_exception_stops_and_keeps_prior_evidence(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 3)
    _orig_observe = tool._observe

    def _observe_once_then_boom(info, index, run_dir):
        if index >= 2:
            raise RuntimeError("stub observation crash")
        return _orig_observe(info, index, run_dir)

    monkeypatch.setattr(tool, "_observe", _observe_once_then_boom)

    res = _invoke(inputs)

    _assert_hits(calls, 1, "观察异常")
    assert res["runs"][1]["status"] == "exception"
    assert res["stop_reason"] == "exception"
    run_dir = Path(res["run_dir"])
    assert (run_dir / "runs" / "run_2" / "exception.json").is_file()
    assert not (run_dir / "sends" / "send_02_request.json").exists()


def test_record_write_failure_stops_without_further_sends(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 3)
    _orig_write = tool._write

    def _write(path, value):
        if path.name == "record.json" and path.parent.name == "run_2":
            raise OSError("stub disk full")
        return _orig_write(path, value)

    monkeypatch.setattr(tool, "_write", _write)

    res = _invoke(inputs)

    _assert_hits(calls, 2, "证据写入失败")
    assert res["stop_reason"] == "evidence_write_failure"
    run_dir = Path(res["run_dir"])
    assert (run_dir / "runs" / "run_1" / "record.json").is_file()
    assert (run_dir / "runs" / "run_2" / "exception.json").is_file()
    assert not (run_dir / "sends" / "send_03_request.json").exists()


# ---------------------------------------------------------------------------
# 6：冻结摘要不符 ⇒ 发送前拒绝
# ---------------------------------------------------------------------------
def test_input_digest_mismatch_exits_with_zero_sends(monkeypatch, workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 3)

    with pytest.raises(SystemExit) as excinfo:
        _invoke(inputs, expect={"plan_digest": "0" * 64})

    assert "冻结摘要不符" in str(excinfo.value)
    _assert_hits(calls, 0, "输入摘要不符")
    run_dirs = list((workdir / "exp").glob("p4_stability_*"))
    assert len(run_dirs) == 1, "预检目录已建，但不得有任何发送证据"
    assert not (run_dirs[0] / "sends").exists()
    assert not (run_dirs[0] / "stability.json").exists()


def test_prompt_digest_drift_blocks_every_send(monkeypatch, workdir):
    inputs = _inputs(workdir)                       # 期望摘要按打桩前的提示算
    _patch_channel(monkeypatch)
    _orig_prompt = V.build_verification_prompt

    def _drifted(view, **kwargs):
        return _orig_prompt(view, **kwargs) + "\n<!-- p4-drift -->"

    monkeypatch.setattr(V, "build_verification_prompt", _drifted)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 3)

    with pytest.raises(SystemExit) as excinfo:
        _invoke(inputs)

    assert "prompt_sha256" in str(excinfo.value)
    _assert_hits(calls, 0, "提示摘要漂移")


def test_payload_drift_is_refused_before_the_second_http(monkeypatch, workdir):
    from _text_gen.agent_plan_provider import AgentPlanTextService
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    _orig_build = AgentPlanTextService._build_payload
    counter = {"n": 0}

    def _build(self, req):
        body = _orig_build(self, req)
        body["p4_probe"] = str(counter["n"])
        counter["n"] += 1
        return body

    monkeypatch.setattr(AgentPlanTextService, "_build_payload", _build)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 3)

    res = _invoke(inputs)

    _assert_hits(calls, 1, "payload 漂移")
    assert res["sends"] == 1
    assert res["observed_runs"] == 2 and res["attempted_runs"] == 2
    assert res["runs"][1]["status"] == "call_failed"
    assert (Path(res["run_dir"]) / "sends" / "send_blocked_payload_mismatch.json").is_file()
    assert res["stop_reason"] == "payload_mismatch"
    run_dir = Path(res["run_dir"])
    assert (run_dir / "frozen_payload.json").is_file()
    assert not (run_dir / "sends" / "send_02_request.json").exists()


# ---------------------------------------------------------------------------
# 同进程重入：第二组不继承第一组的预算/证据目录
# ---------------------------------------------------------------------------
def test_second_run_in_same_process_has_own_budget_and_directory(monkeypatch,
                                                                 workdir):
    inputs = _inputs(workdir)
    _patch_channel(monkeypatch)
    calls = _install_stub(monkeypatch, [_raw_ok(inputs)] * 3)

    first = _invoke(inputs, exp_root=workdir / "exp1")
    second = _invoke(inputs, exp_root=workdir / "exp2")

    _assert_hits(calls, 6, "同进程两组各 3 次")
    assert first["run_dir"] != second["run_dir"]
    assert first["sends"] == 3 and second["sends"] == 3
    assert second["stop_reason"] == "completed"
    second_sends = sorted(p.name for p in
                          (Path(second["run_dir"]) / "sends").glob("*_request.json"))
    assert second_sends == ["send_01_request.json", "send_02_request.json",
                            "send_03_request.json"]
