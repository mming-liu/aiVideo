# -*- coding: utf-8 -*-
"""M-A3：审计请求体归档（2026-11-17）.

要证明的：`script_audit` 调用会把 **user_prompt 逐字**落盘到
`<call_id>.prompt.txt` 并在台账记 `prompt_sha256`/`prompt_bytes`。这样下次身份
失配可以直接 diff 出第一处差异，而不是像 19:00 那次只能量出"+1,043 字符"。
其它 schema 不归档请求体（避免每次生成都存几十 KB）。
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from shared import call_ledger


def _req(prompt: str, schema: str) -> SimpleNamespace:
    return SimpleNamespace(model="m-test", user_prompt=prompt, system_prompt="sys",
                           json_schema={"name": schema} if schema else None,
                           temperature=0.0, max_tokens=100)


def _res(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, success=True, truncated=False, error="",
                           finish_reason="stop", usage={})


def test_audit_prompt_is_archived(tmp_path):
    ledger = tmp_path / "step01_calls.jsonl"
    old = call_ledger.set_ledger_path(ledger)
    try:
        call_ledger.record_call("agent_plan", _req("PROMPT-BODY", "script_audit"),
                                _res("RESP"), elapsed_ms=1, call_id="cid-audit")
    finally:
        call_ledger.set_ledger_path(old)

    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    path = tmp_path / "step01_call_bodies" / "cid-audit.prompt.txt"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "PROMPT-BODY"
    assert row["prompt_sha256"] == hashlib.sha256(b"PROMPT-BODY").hexdigest()
    assert row["prompt_bytes"] == len("PROMPT-BODY".encode("utf-8"))
    assert row["prompt_archive_error"] == ""


def test_non_audit_prompt_is_not_archived(tmp_path):
    ledger = tmp_path / "step01_calls.jsonl"
    old = call_ledger.set_ledger_path(ledger)
    try:
        call_ledger.record_call("agent_plan", _req("BIG-PROMPT", "storyboard_script"),
                                _res("RESP"), elapsed_ms=1, call_id="cid-gen")
    finally:
        call_ledger.set_ledger_path(old)

    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert not (tmp_path / "step01_call_bodies" / "cid-gen.prompt.txt").exists()
    # `prompt_sha256` 是既有 `_describe` 字段（所有调用都有），这里核的是
    # **没有请求体文件**、也没有 `prompt_path`
    assert not row.get("prompt_path")
    # 响应体仍照旧归档（既有契约不变）
    assert (tmp_path / "step01_call_bodies" / "cid-gen.txt").is_file()
