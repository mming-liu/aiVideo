# -*- coding: utf-8 -*-
"""T1 / M2.3：PAUSED 暂停态（2026-11-18）.

要证明的：
  1. 超时/预算不足可以把「最近完成的 CP」写成暂停态，字段完整；
  2. 暂停态**单独落盘**（`snapshots/_paused.json`），不污染四态 `latest()`；
  3. 缺失 = 没有暂停；存在但损坏/自相矛盾 = `CheckpointError`（fail-closed）；
  4. 写入失败只告警，不打断超时结论。
"""
from __future__ import annotations

import json

import pytest

from shared import step01_checkpoint as ck


@pytest.fixture(autouse=True)
def _zero_call_guard():
    """零调用证据：本文件任何用例都不得产生逻辑调用/HTTP 尝试/响应."""
    from shared import outbound_guard

    guard = outbound_guard.OutboundGuard(
        transport=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unexpected provider POST")))
    prev = outbound_guard.install(guard)
    try:
        yield guard
    finally:
        outbound_guard.install(prev)
    snap = guard.snapshot()
    assert snap["logical_total"] == 0
    assert snap["http_attempts_total"] == 0
    assert snap["responses_total"] == 0


def _script(marker: str = "A", n: int = 2) -> dict:
    return {"title": "t", "characters": [], "hook": None,
            "shots": [{"index": i, "duration_sec": 5.0, "main_subject": marker,
                       "ff_desc": marker, "lf_desc": marker, "motion_desc": marker,
                       "prompt": marker, "narration_segment": "", "scene": "院内",
                       "location": "院中", "clip_transition": "硬切",
                       "context_bridge": "", "frame_layouts": {}, "keyframes": []}
                      for i in range(1, n + 1)]}


def test_write_paused_roundtrip_and_fields(tmp_path):
    script = _script()
    rec = ck.write_paused(tmp_path, ck.CP3_INITIAL_AUDIT_DONE,
                          run_id="run-paused", reason="budget_exceeded",
                          script=script,
                          extra={"budget_sec": 1200, "elapsed_sec": 1201.5})
    assert rec["written"] is True and rec["error"] == ""
    assert rec["stage"] == ck.PAUSED_AT_CP3_INITIAL_AUDIT_DONE
    assert rec["checkpoint_stage"] == ck.CP3_INITIAL_AUDIT_DONE
    assert rec["resume_from"] == "CP3"
    assert rec["run_id"] == "run-paused"
    assert rec["revision"]
    assert rec["written_at"]

    path = tmp_path / "snapshots" / "_paused.json"
    assert path.is_file()
    back = ck.read_paused(tmp_path)
    assert back is not None
    assert back["written"] is True
    assert back["path"] == str(path)
    assert back["stage"] == ck.PAUSED_AT_CP3_INITIAL_AUDIT_DONE
    assert back["revision"] == rec["revision"]
    assert back["extra"]["budget_sec"] == 1200
    assert back["extra"]["elapsed_sec"] == 1201.5


def test_paused_does_not_pollute_latest(tmp_path):
    cp1 = ck.write(tmp_path, ck.CP1_PLAN_VERIFIED, script=_script("A"))
    assert ck.write_paused(tmp_path, ck.CP1_PLAN_VERIFIED,
                           run_id="run-x", reason="budget_exceeded",
                           script=_script("A"))["written"] is True
    got = ck.latest(tmp_path)
    assert got["key"] == ck.CP1_PLAN_VERIFIED
    assert got["path"] == cp1["path"]

    # 后续正常四态仍按 written_at 成为 latest，暂停态不参与竞争。
    cp3 = ck.write(tmp_path, ck.CP3_INITIAL_AUDIT_DONE, script=_script("B"))
    assert ck.latest(tmp_path)["path"] == cp3["path"]


def test_missing_paused_returns_none(tmp_path):
    assert ck.read_paused(tmp_path) is None


def test_corrupt_paused_fail_closed(tmp_path):
    path = tmp_path / "snapshots" / "_paused.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ck.CheckpointError):
        ck.read_paused(tmp_path)

    path.write_text(json.dumps({"paused": True}), encoding="utf-8")
    with pytest.raises(ck.CheckpointError):
        ck.read_paused(tmp_path)

    # stage 与 checkpoint_stage 自相矛盾
    path.write_text(json.dumps({
        "paused": True,
        "stage": ck.PAUSED_AT_CP3_INITIAL_AUDIT_DONE,
        "checkpoint_stage": ck.CP2_CANDIDATE_GENERATED,
        "resume_from": "CP3",
        "revision": "r", "written_at": "2026-11-18T00:00:00.000000",
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ck.CheckpointError):
        ck.read_paused(tmp_path)


def test_write_paused_requires_revision(tmp_path):
    rec = ck.write_paused(tmp_path, ck.CP2_CANDIDATE_GENERATED,
                          run_id="run-x", reason="budget_exceeded")
    assert rec["written"] is False
    assert "revision" in rec["error"]
    assert not (tmp_path / "snapshots" / "_paused.json").exists()


def test_write_paused_failure_does_not_raise(tmp_path, monkeypatch):
    import shared.json_io as json_io

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(json_io, "atomic_dump_json", _boom)
    rec = ck.write_paused(tmp_path, ck.CP1_PLAN_VERIFIED,
                          run_id="run-x", reason="budget_exceeded",
                          script=_script())
    assert rec["written"] is False
    assert "disk full" in rec["error"]


def test_paused_stage_mapping_accepts_cp4_round_and_paused_names(tmp_path):
    assert ck.paused_stage_for("CP4_REPAIR_ROUND_K_DONE_r02") == \
        ck.PAUSED_AT_CP4_REPAIR_ROUND_K_DONE
    assert ck.paused_stage_for(ck.PAUSED_AT_CP2_CANDIDATE_GENERATED) == \
        ck.PAUSED_AT_CP2_CANDIDATE_GENERATED
    with pytest.raises(ck.CheckpointError):
        ck.paused_stage_for("CP9_UNKNOWN")
