# -*- coding: utf-8 -*-
"""P4.6 跨 run 保优护栏（_cross_run_best_guard）单元测试."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _script_with_dialogue(n_dialogue: int, total: int = 10) -> dict:
    """构造对白镜数量可控的最小 script（时长/镜数都给 10 分标准值）.

    prompt 按镜号变化（2026-09-12）：本测试测的是跨 run 护栏契约，不是打分
    机制——相邻重复惩罚上线后，逐镜相同的 prompt 会被判凑镜重复扣分，
    破坏「恢复版 ≥60」的契约基线。非退化夹具 = 每镜视觉可区分。
    """
    shots = []
    for i in range(1, total + 1):
        narr = "杨旭道：这一句是测试对白" if i <= n_dialogue else ""
        shots.append({
            "index": i,
            "prompt": f"[中景] 测试镜头{i}：画面内容各不相同{i}号机位",
            "ff_desc": "", "lf_desc": "", "motion_desc": "",
            "duration_sec": 6.0,
            "narration_segment": narr,
            "subtitle": narr,
            "main_subject": "杨旭" if narr else "群演",
        })
    return {"shots": shots, "total_duration": 60.0}


def _write_snapshot(snap_dir: Path, ts: str, script: dict) -> Path:
    p = snap_dir / f"script_result_{ts}.json"
    p.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return p


def test_new_score_higher_no_action(tmp_path, monkeypatch):
    """新终稿更优 → 不动作."""
    import step01_script_gen.main as m
    out = tmp_path / "script_result.json"
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_FILE", out)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(1))
    new = _script_with_dialogue(8)
    m._cross_run_best_guard(new, "")
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_snapshot_best_restored(tmp_path, monkeypatch):
    """快照最优显著更高 + KEEP_BEST_SNAPSHOT=1 → 自动恢复."""
    import step01_script_gen.main as m
    out = tmp_path / "script_result.json"
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_FILE", out)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))
    # 最新一份 = 本次 run 的快照（护栏按时间戳排除它）
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))
    new = _script_with_dialogue(1)
    m._cross_run_best_guard(new, "")
    restored = json.loads(out.read_text(encoding="utf-8"))
    # 护栏契约：恢复版经补跑 hard_fix 后分数不低于快照最优（60 分）
    # （补跑可能插入开场镜/注入对白 → 结构可变，按分数断言）
    import step01_script_gen.main as _m
    assert _m._score_script(restored, "")["total"] >= 60


def test_keep_best_disabled_warn_only(tmp_path, monkeypatch):
    """KEEP_BEST_SNAPSHOT=0 → 仅告警，不覆盖新终稿."""
    import step01_script_gen.main as m
    monkeypatch.setenv("KEEP_BEST_SNAPSHOT", "0")
    out = tmp_path / "script_result.json"
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_FILE", out)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))
    new = _script_with_dialogue(1)
    m._cross_run_best_guard(new, "")
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_no_snapshots_no_action(tmp_path, monkeypatch):
    """无快照（首次运行）→ 不动作."""
    import step01_script_gen.main as m
    out = tmp_path / "script_result.json"
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_FILE", out)
    (tmp_path / "snapshots").mkdir()
    m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert json.loads(out.read_text(encoding="utf-8")) == {}
