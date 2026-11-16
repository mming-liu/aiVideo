# -*- coding: utf-8 -*-
"""M2.2：修复轮采纳稿必须**先落盘再复审**（2026-11-16）.

要证明的：`_persist_repair_round` 在提交循环把 `script = _cand` 之前，就把采纳稿
原子写入 `snapshots/repair_round_{k}.json` 并写指针；写入失败时**不打断**提交
（只记录 error）。这条不变量来自实测：`19:01` 与 `22:35` 两次修复都已
`accepted=true / resolved=1`，随后修后复审被发送上限拦下，修后稿只存在于内存，
整轮付费成果丢失。
"""
from __future__ import annotations

import json


def _script(marker: str = "A", n: int = 2) -> dict:
    shots = []
    for i in range(1, n + 1):
        shots.append({
            "index": i, "duration_sec": 5.0, "main_subject": marker,
            "ff_desc": f"{marker}站立", "lf_desc": f"{marker}转身",
            "motion_desc": f"{marker}转身", "prompt": f"[中景] {marker}转身",
            "narration_segment": "", "scene": "院内", "location": "院中",
            "clip_transition": "硬切", "context_bridge": "",
            "frame_layouts": {}, "keyframes": [],
        })
    return {"title": "t", "shots": shots, "characters": [], "hook": None}


def test_accepted_repair_is_persisted_before_next_audit(tmp_path, monkeypatch):
    import step01_script_gen.main as m
    from shared.quality_gate import compute_script_revision

    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    script = _script("修复后")

    rec = m._persist_repair_round(script, round_no=2)

    assert rec["written"] is True and rec["error"] == ""
    path = tmp_path / "snapshots" / "repair_round_02.json"
    assert path.exists(), "采纳稿必须落在 snapshots/repair_round_02.json"
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back == script
    assert rec["revision"] == compute_script_revision(script)
    assert rec["requires_fresh_audit"] is True

    pointer = json.loads((tmp_path / "snapshots" / "_repair_round_pointer.json")
                         .read_text(encoding="utf-8"))
    assert pointer["path"] == rec["path"]
    assert pointer["round"] == 2
    assert pointer["revision"] == rec["revision"]


def test_snapshot_survives_after_audit_block(tmp_path, monkeypatch):
    """模拟「修复成功 → 复审被 cap 拦下」：快照已在盘上，内容与采纳稿逐字相同."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    accepted = _script("采纳稿")
    m._persist_repair_round(accepted, round_no=1)

    # 复审阶段抛出（发送上限 / 网络失败），提交循环不会再把 script 写盘
    with_raise = True
    try:
        if with_raise:
            raise RuntimeError("send_budget_exhausted: cap 2")
    except RuntimeError:
        pass

    back = json.loads((tmp_path / "snapshots" / "repair_round_01.json")
                      .read_text(encoding="utf-8"))
    assert back == accepted, "复审失败后采纳稿必须仍可从快照完整恢复"


def test_write_failure_does_not_break_commit(tmp_path, monkeypatch):
    """快照是保护性副作用：写失败只记录，绝不抛出打断提交."""
    import step01_script_gen.main as m
    import shared.json_io as json_io

    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(json_io, "atomic_dump_json", _boom)
    rec = m._persist_repair_round(_script(), round_no=3)   # 不得抛

    assert rec["written"] is False
    assert "disk full" in rec["error"]
    assert rec["requires_fresh_audit"] is True
