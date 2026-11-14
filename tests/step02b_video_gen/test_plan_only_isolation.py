# -*- coding: utf-8 -*-
"""F2：plan-only 的 r2v 拼表落候选根，且正式产物前后逐字节不变.

`--plan-only` 此前只把计划写到候选 `plans/`，没有 production snapshot 守卫；
r2v 参考超限时 `fit_refs` 又会把参考表写进生产 `shots/`。本用例用桩收集器
模拟「超限 → 拼表」写路径，断言参考表只落候选根、正式目录 hash 前后一致。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from shared import enhancement_io as eio
from step02_video_gen import prompt_builder as pb
from step02b_video_gen import cli, main


def _production_tree(tmp_path: Path) -> Path:
    prod = tmp_path / "production"
    shot_dir = prod / "shots" / "1"
    shot_dir.mkdir(parents=True)
    (shot_dir / "first_frame.png").write_bytes(b"production-ff")
    (prod / "clips").mkdir()
    (prod / "clips" / "clip_1.mp4").write_bytes(b"production-clip")
    return prod


def _snapshot_of(root: Path):
    def _snapshot(chapter=None):
        return {str(p): p.read_bytes().hex()
                for p in sorted(root.rglob("*")) if p.is_file()}
    return _snapshot


def _script() -> dict:
    return {
        "shots": [{
            "index": 1, "duration_sec": 5,
            "ff_desc": "甲站在船头", "lf_desc": "甲转身离开",
            "motion_desc": "甲转身", "prompt": "甲转身离开",
        }],
        "characters": [],
    }


def test_plan_only_r2v_compose_never_writes_production(tmp_path, monkeypatch):
    prod = _production_tree(tmp_path)
    monkeypatch.setattr(main, "production_snapshot", _snapshot_of(prod))

    pkg = tmp_path / "candidate_pkg"
    monkeypatch.setattr(cli, "resolve_experiment_root",
                        lambda root, chapter=None: "exp")
    monkeypatch.setattr(eio, "package_root",
                        lambda package, experiment_id, chapter=None: pkg)

    src = tmp_path / "portrait.png"
    src.write_bytes(b"portrait")
    seen: list[tuple[Path | None, bool]] = []

    def _collect(shot, characters=None, registry=None, limit=5,
                 sheet_dir=None, materialize=True):
        seen.append((Path(sheet_dir) if sheet_dir is not None else None, materialize))
        if not materialize:
            return [str(src)]
        d = Path(sheet_dir)
        d.mkdir(parents=True, exist_ok=True)
        sheet = d / "ref_sheet_stub.png"
        sheet.write_bytes(b"composed-sheet")
        return [str(sheet)]

    monkeypatch.setattr(pb, "_collect_r2v_references", _collect)
    monkeypatch.setattr(pb, "_load_character_registry", lambda: {})
    monkeypatch.setattr(pb, "_REFERENCE_MODE", "r2v", raising=False)
    monkeypatch.setattr(pb, "_USE_TEXT_TO_VIDEO", False, raising=False)
    monkeypatch.setattr(pb, "_MODEL_VOICE", False, raising=False)
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setattr("_video_gen.create_service", lambda provider=None: None)

    before = _snapshot_of(prod)()
    options = SimpleNamespace(
        candidate_root=str(tmp_path / "candidate" / "exp" / "video"), shots="1")
    rc = main._plan_only(options, _script(), tmp_path / "shots", 1)
    after = _snapshot_of(prod)()

    assert rc == 0
    assert before == after, "plan-only 不得修改正式产物"
    materialized = [d for d, mat in seen if mat and d is not None]
    assert materialized, "r2v 拼表必须经过 ref_sheet_dir"
    assert all(pkg in d.parents for d in materialized), materialized
    assert not list((prod / "shots" / "1").glob("ref_sheet_*"))
    assert (pkg / "plans" / "refs" / "1" / "ref_sheet_stub.png").is_file()


def test_plan_only_reports_production_write_detected(tmp_path, monkeypatch):
    prod = _production_tree(tmp_path)
    monkeypatch.setattr(main, "production_snapshot", _snapshot_of(prod))

    pkg = tmp_path / "candidate_pkg"
    monkeypatch.setattr(cli, "resolve_experiment_root",
                        lambda root, chapter=None: "exp")
    monkeypatch.setattr(eio, "package_root",
                        lambda package, experiment_id, chapter=None: pkg)

    def _bad_build_plan(*args, **kwargs):
        # 模拟拼表误落生产 shots/
        (prod / "shots" / "1" / "ref_sheet_bad.png").write_bytes(b"leak")
        return {"shots": [], "ready": 0, "blocked": 0,
                "estimated_generations": 0, "network_calls": 0}

    monkeypatch.setattr(main, "build_plan", _bad_build_plan)
    options = SimpleNamespace(
        candidate_root=str(tmp_path / "candidate" / "exp" / "video"), shots="1")

    rc = main._plan_only(options, _script(), tmp_path / "shots", 1)

    assert rc == eio.EXIT_TOOL_FAIL


def test_plan_only_bridge_log_never_lands_in_production(tmp_path, monkeypatch):
    """U6-FIN WP1b：plan-only 期间桥接日志不得落正式章目录（step02.log → exit 3）.

    2026-11-14 R6 S05 实测：`--plan-only --candidate-root` 只写候选 plans/，
    但 `vimax_bridge._bridge_log` 硬编码 `chapter_dir()/step02.log`，一碰哈希计算
    就在正式章目录建了 step02.log，被 production snapshot 判成
    `production_write_detected`（exit 3）。这里把章目录与快照指到同一棵树，
    保证"日志落点"和"隔离判据"看的是同一个位置，测试才有判别力。
    """
    from shared import log_utils
    from shared import paths as shared_paths
    from shared import vimax_bridge

    prod = _production_tree(tmp_path)
    monkeypatch.setattr(shared_paths, "chapter_dir", lambda *a, **k: prod)
    monkeypatch.setattr(main, "production_snapshot", _snapshot_of(prod))

    pkg = tmp_path / "candidate_pkg"
    monkeypatch.setattr(cli, "resolve_experiment_root",
                        lambda root, chapter=None: "exp")
    monkeypatch.setattr(eio, "package_root",
                        lambda package, experiment_id, chapter=None: pkg)

    def _build_plan_needs_log(*args, **kwargs):
        # 真实 plan 计算里 _get_shot_hash/_get_frame_content_hash 会走这条日志
        vimax_bridge._bridge_log("  [HASH] shot_1 首帧内容哈希计算")
        return {"shots": [], "ready": 1, "blocked": 0,
                "estimated_generations": 1, "network_calls": 0}

    monkeypatch.setattr(main, "build_plan", _build_plan_needs_log)

    # 候选模式的日志上下文（main() 在进入 _plan_only 前就已设置）
    log_utils.set_log_context(pkg / "logs")
    try:
        options = SimpleNamespace(
            candidate_root=str(tmp_path / "candidate" / "exp" / "video"), shots="1")
        rc = main._plan_only(options, _script(), tmp_path / "shots", 1)
    finally:
        log_utils.set_log_context(None)

    assert rc == 0, "plan-only 必须 exit 0（日志外溢会把隔离判据打成 exit 3）"
    assert not (prod / "step02.log").exists(), "桥接日志不得写进正式章目录"
    assert (pkg / "logs" / "step02.log").is_file(), "日志应落候选实验根"
