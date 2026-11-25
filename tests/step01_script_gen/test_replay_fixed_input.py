# -*- coding: utf-8 -*-
"""固定输入修复入口（P03）：清单校验、隔离根、dry-run、发送上限.

这些用例全部在独立子进程里跑 CLI，避免项目配置在测试进程里被先导入后失效
（`CHAPTER_DIR` 在导入期由环境变量决定）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _write_novel(path: Path) -> None:
    path.write_text(
        "第一部 测试卷 第001章 测试章\n\n"
        "甲把门关上。乙问为什么。甲说外面有人。\n\n"
        "第一部 测试卷 第002章 下一章\n\n"
        "无关内容。\n",
        encoding="utf-8")


def _plan() -> dict:
    return {
        "main_events": ["甲关门", "乙发问", "甲回答"],
        "climax": "甲回答",
        "causal_chain": ["关门→发问→回答"],
        "time_budget": "画面为主",
        "characters": ["甲", "乙"],
        "emotional_arc": "平→紧",
        "end_hook": "外面有人",
        "shot_beats": [
            "节拍：甲把门关上｜本镜信息目的：推进｜原文依据：“甲把门关上。”｜载体：台词1",
            "节拍：乙发问｜本镜信息目的：推进｜原文依据：“乙问为什么。”｜载体：台词2",
            "节拍：甲回答｜本镜信息目的：收束｜原文依据：“甲说外面有人。”｜载体：画面",
        ],
        "dialogue_lines": ["甲把门关上。", "乙问为什么。"],
        "narration_lines": [],
        "required_facts": [
            "甲把门关上｜原文证据：“甲把门关上。”｜承载：B1/台词1",
            "乙发问｜原文证据：“乙问为什么。”｜承载：B2/台词2",
            "甲说外面有人｜原文证据：“甲说外面有人。”｜承载：B3/画面",
        ],
        "hook_spoken": [],
        "hook_carrier": "",
        "plan_contract_version": 5,
        "planning_acceptance_version": "legacy_acceptance.v1",
    }


def _script() -> dict:
    return {"title": "测试", "shots": [
        {"index": 1, "narration_segment": "甲把门关上。",
         "plan_refs": {"beats": [1], "lines": [1]}},
        {"index": 2, "narration_segment": "乙问为什么。",
         "plan_refs": {"beats": [2], "lines": [2]}},
    ]}


def _manifest(tmp_path: Path, *, output_base: Path | None = None) -> Path:
    novel = tmp_path / "novel.txt"
    _write_novel(novel)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(), ensure_ascii=False), encoding="utf-8")
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(_script(), ensure_ascii=False), encoding="utf-8")
    issues_path = tmp_path / "issues.json"
    issues_path.write_text(json.dumps({"issues": []}, ensure_ascii=False),
                           encoding="utf-8")
    manifest = {
        "novel": {"path": str(novel)},
        "scope": {"novel_name": "测试书", "book_index": 1,
                  "book_name": "测试卷", "chapter": 1},
        "script": {"path": str(script_path)},
        "plan": {"path": str(plan_path)},
        "issues": {"path": str(issues_path)},
        "budget": {"max_sends": 4, "repair_rounds": 2, "audit_rounds": 2},
        # 通道必须与 `.env.content` 的生效值一致（配置层有 shell/.env 冲突守卫）
        "channel": {"provider": "agent_plan", "model": "deepseek-v4.1-flash"},
        "output_base": str(output_base or (tmp_path / "iso")),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def _run_cli(*args) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "tools.replay_step01_repair", *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, env=env)


def test_dry_run_validates_and_writes_report_without_network(tmp_path):
    manifest = _manifest(tmp_path)
    proc = _run_cli("--input-manifest", str(manifest), "--dry-run")
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads((tmp_path / "iso" / "dry_run.json").read_text(encoding="utf-8"))
    assert report["stage"] == "dry_run"
    assert isinstance(report["plan_acceptance_codes"], list)
    assert report["budget"]["max_sends"] == 4
    assert report["dry_run"]["network_blocked_calls"] == 0
    # 隔离根在导入后仍然生效：章节目录落在 output_base 内
    assert str(tmp_path / "iso") in report["chapter_dir"]


def test_missing_novel_is_a_clear_error(tmp_path):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["novel"]["path"] = str(tmp_path / "nope.txt")
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = _run_cli("--input-manifest", str(manifest), "--dry-run")
    assert proc.returncode != 0
    assert "[ABORT]" in (proc.stderr + proc.stdout)
    assert "nope.txt" in (proc.stderr + proc.stdout)


def test_hash_mismatch_is_rejected(tmp_path):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["plan"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = _run_cli("--input-manifest", str(manifest), "--dry-run")
    assert proc.returncode != 0
    assert "[ABORT]" in (proc.stderr + proc.stdout)
    assert "0000000000000000" in (proc.stderr + proc.stdout)


def test_output_base_inside_production_output_is_rejected(tmp_path):
    manifest = _manifest(tmp_path, output_base=ROOT / "output" / "step01_replay_probe")
    proc = _run_cli("--input-manifest", str(manifest), "--dry-run")
    assert proc.returncode != 0
    assert "[ABORT]" in (proc.stderr + proc.stdout)
    assert "output" in (proc.stderr + proc.stdout)


def test_manifest_duration_band_is_applied_before_import(tmp_path):
    """修复回放必须用 manifest 的时长带，不能沿用安装目录的旧默认."""
    import os
    from tools import replay_step01_repair as tool

    keys = ("DURATION_MIN_SEC", "DURATION_MAX_SEC",
            "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC")
    old = {k: os.environ.get(k) for k in keys}
    try:
        checked = {
            "scope": {"novel_name": "测试书", "book_index": 1,
                      "book_name": "测试卷", "chapter": 1},
            "budget": {"min": 60, "max": 120, "target_min": 85, "target_max": 110},
            "channel": {},
        }
        tool._apply_manifest_env({}, checked, tmp_path / "iso")
        assert os.environ["DURATION_MIN_SEC"] == "60"
        assert os.environ["DURATION_MAX_SEC"] == "120"
        assert os.environ["DURATION_TARGET_MIN_SEC"] == "85"
        assert os.environ["DURATION_TARGET_MAX_SEC"] == "110"
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_send_counter_enforces_the_boundary_cap(monkeypatch):
    import requests
    from tools import replay_step01_repair as tool

    calls = {"n": 0}

    def _fake_post(*args, **kwargs):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(requests, "post", _fake_post)
    state = tool._install_send_counter(2)
    try:
        assert requests.post("u") is not None
        assert requests.post("u") is not None
        with pytest.raises(tool.SendBudgetExceeded):
            requests.post("u")
        assert state["used"] == 2
        assert state["refused"] == 1
        assert calls["n"] == 2          # 被拒的发送没有到达真实边界
    finally:
        state["restore"]()
