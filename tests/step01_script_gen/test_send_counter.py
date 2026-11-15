# -*- coding: utf-8 -*-
"""发送边界计数（C03 复核）：总尝试 / 响应 / 钩子分类尝试 / 两层上限."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.send_cap import (  # noqa: E402
    CallCounter, SendBudgetExceeded, is_hook_request,
)
from tools.run_step01_from_verified_plan import _summarize_ledger  # noqa: E402


class _Resp:
    status_code = 200


def _body(messages):
    return {"data": json.dumps({"messages": messages}, ensure_ascii=False)}


def _hook_kw():
    return _body([{"role": "system", "content": "你是短视频脚本大师，擅长制造悬念。"},
                  {"role": "user", "content": "生成钩子分镜"}])


def _other_kw():
    return _body([{"role": "system", "content": "你是短视频编剧。"},
                  {"role": "user", "content": "生成分镜脚本"}])


def test_counts_attempts_and_responses_separately(tmp_path):
    seq = [200, "ssl", "ssl", "ssl", "ssl", 200]
    state = {"i": 0}

    def transport(url, **kw):
        kind = seq[state["i"]]
        state["i"] += 1
        if kind == "ssl":
            raise ConnectionError("SSLError: EOF")
        return _Resp()

    c = CallCounter(transport, cap=10, log_dir=tmp_path)
    for _ in seq:
        try:
            c(("https://example.test",), **_other_kw())
        except ConnectionError:
            pass
    assert c.attempts == 6
    assert c.responses_received == 2
    assert c.hook_attempts == 0
    assert len(c.calls) == 6
    assert sum(1 for x in c.calls if not x["response_received"]) == 4


def test_hook_classification_by_prompt_marker():
    assert is_hook_request(_hook_kw()) is True
    assert is_hook_request(_other_kw()) is False
    # json= 形态同样可识别
    assert is_hook_request({"json": {"messages": [
        {"role": "system", "content": "你是短视频脚本大师。"}]}}) is True


def test_hook_cap_counts_retries_and_blocks_before_send(tmp_path):
    sent = {"n": 0}

    def transport(url, **kw):
        sent["n"] += 1
        return _Resp()

    c = CallCounter(transport, cap=10, log_dir=tmp_path, hook_cap=2)
    c("u", **_hook_kw())
    c("u", **_hook_kw())
    with pytest.raises(SendBudgetExceeded):
        c("u", **_hook_kw())
    assert sent["n"] == 2, "超限请求不得再发出"
    assert c.hook_attempts == 2 and c.hook_blocked == 1
    assert c.attempts == 2


def test_total_cap_blocks_before_send(tmp_path):
    sent = {"n": 0}

    def transport(url, **kw):
        sent["n"] += 1
        return _Resp()

    c = CallCounter(transport, cap=1, log_dir=tmp_path)
    c("u", **_other_kw())
    with pytest.raises(SendBudgetExceeded):
        c("u", **_other_kw())
    assert sent["n"] == 1
    assert c.attempts == 1 and c.blocked == 1


def test_hook_attempts_and_responses_are_counted_separately(tmp_path):
    def transport(url, **kw):
        return _Resp()

    c = CallCounter(transport, cap=10, log_dir=tmp_path, hook_cap=4)
    c("u", **_hook_kw())
    c("u", **_other_kw())
    c("u", **_hook_kw())
    assert c.attempts == 3 and c.responses_received == 3
    assert c.hook_attempts == 2 and c.hook_responses == 2


def test_ledger_summary_classifies_stages(tmp_path):
    p = tmp_path / "step01_calls.jsonl"
    rows = [
        {"schema": "storyboard_script", "ok": True, "call_id": "c1"},
        {"schema": "hook_shot", "ok": False, "call_id": "c2"},
        {"schema": "script_audit", "ok": True, "call_id": "c3"},
        {"schema": "repair_shot_replacements", "ok": True, "call_id": "c4"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = _summarize_ledger(p)
    assert [x["stage"] for x in out] == ["body_generation", "hook", "audit", "repair"]
    assert [x["ok"] for x in out] == [True, False, True, True]
    assert _summarize_ledger(tmp_path / "missing.jsonl") == []
