# -*- coding: utf-8 -*-
"""M2.3：阶段级检查点 CP1–CP4（2026-11-16）.

要证明的：
  1. 每个阶段边界都能原子落一份检查点，并带 revision 读回校验；
  2. 索引 `_checkpoints.json` 是指针式的（不靠 mtime/最新目录猜）；
  3. 写失败**不抛**（检查点是保护性副作用）；
  4. CP4 按轮次分文件（`cp_CP4_REPAIR_ROUND_K_DONE_r02.json`）。
"""
from __future__ import annotations

import json

from shared import step01_checkpoint as ck


def _script(marker: str = "A", n: int = 2) -> dict:
    return {"title": "t", "characters": [], "hook": None,
            "shots": [{"index": i, "duration_sec": 5.0, "main_subject": marker,
                       "ff_desc": marker, "lf_desc": marker, "motion_desc": marker,
                       "prompt": marker, "narration_segment": "", "scene": "院内",
                       "location": "院中", "clip_transition": "硬切",
                       "context_bridge": "", "frame_layouts": {}, "keyframes": []}
                      for i in range(1, n + 1)]}


def test_write_read_and_index(tmp_path):
    rec = ck.write(tmp_path, ck.CP2_CANDIDATE_GENERATED, script=_script())
    assert rec["written"] is True and rec["error"] == ""
    path = tmp_path / "snapshots" / "cp_CP2_CANDIDATE_GENERATED.json"
    assert path.exists()
    back = ck.read(tmp_path, ck.CP2_CANDIDATE_GENERATED)
    assert back["stage"] == ck.CP2_CANDIDATE_GENERATED
    assert back["script"] == _script()

    idx = json.loads((tmp_path / "snapshots" / "_checkpoints.json")
                     .read_text(encoding="utf-8"))
    assert ck.CP2_CANDIDATE_GENERATED in idx
    assert idx[ck.CP2_CANDIDATE_GENERATED]["path"] == rec["path"]


def test_cp4_is_per_round(tmp_path):
    ck.write(tmp_path, ck.CP4_REPAIR_ROUND_K_DONE, script=_script("r1"), round_no=1)
    ck.write(tmp_path, ck.CP4_REPAIR_ROUND_K_DONE, script=_script("r2"), round_no=2)
    p1 = tmp_path / "snapshots" / "cp_CP4_REPAIR_ROUND_K_DONE_r01.json"
    p2 = tmp_path / "snapshots" / "cp_CP4_REPAIR_ROUND_K_DONE_r02.json"
    assert p1.exists() and p2.exists()
    assert ck.read(tmp_path, ck.CP4_REPAIR_ROUND_K_DONE, round_no=2)["script"] != \
        ck.read(tmp_path, ck.CP4_REPAIR_ROUND_K_DONE, round_no=1)["script"]


def test_latest_reads_index_not_mtime(tmp_path):
    ck.write(tmp_path, ck.CP1_PLAN_VERIFIED)
    last = ck.write(tmp_path, ck.CP3_INITIAL_AUDIT_DONE, script=_script())
    got = ck.latest(tmp_path)
    assert got["path"] == last["path"]


def test_missing_checkpoint_returns_none(tmp_path):
    assert ck.read(tmp_path, ck.CP1_PLAN_VERIFIED) is None
    assert ck.latest(tmp_path) is None


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    import shared.json_io as json_io

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(json_io, "atomic_dump_json", _boom)
    rec = ck.write(tmp_path, ck.CP1_PLAN_VERIFIED)
    assert rec["written"] is False and "disk full" in rec["error"]


def test_audit_is_recorded_on_cp3(tmp_path):
    audit = {"status": "ok", "input_revision": "rev", "issues": [{"code": "X"}]}
    ck.write(tmp_path, ck.CP3_INITIAL_AUDIT_DONE, script=_script(), audit=audit)
    assert ck.read(tmp_path, ck.CP3_INITIAL_AUDIT_DONE)["audit"] == audit
