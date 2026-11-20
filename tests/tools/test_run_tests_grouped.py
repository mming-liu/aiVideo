# -*- coding: utf-8 -*-
"""tools/run_tests_grouped：旧报告不得冒充本轮结果（2026-11-19 复核 P2-4，离线、不跑 pytest）."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import run_tests_grouped as rtg  # noqa: E402

_GOOD_XML = ('<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1">'
             '<testcase classname="a" name="t"/></testsuite></testsuites>')


def _runner(code, write_xml=True):
    def _run(cmd, **kw):
        xml = next(c.split("=", 1)[1] for c in cmd if c.startswith("--junitxml="))
        if write_xml:
            Path(xml).write_text(_GOOD_XML, encoding="utf-8")
        return SimpleNamespace(returncode=code, stdout="", stderr="")
    return _run


def test_exit_1_without_new_xml_is_incomplete_even_if_old_xml_existed(tmp_path):
    stale = tmp_path / "g.xml"
    stale.write_text(_GOOD_XML, encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    rc = rtg.run_all(["g"], {"g": ("x",)}, tmp_path, runner=_runner(1, write_xml=False))
    assert rc == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["complete"] is False
    assert summary["groups"][0]["report_problem"] == "未生成 JUnit XML"
    assert not stale.exists(), "旧报告必须在本组启动前删除"


def test_fresh_xml_with_test_failures_is_complete(tmp_path):
    rc = rtg.run_all(["g"], {"g": ("x",)}, tmp_path, runner=_runner(1))
    assert rc == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["complete"] is True
    assert (tmp_path / "merged.xml").exists()


def test_unparseable_xml_is_incomplete(tmp_path):
    def _run(cmd, **kw):
        xml = next(c.split("=", 1)[1] for c in cmd if c.startswith("--junitxml="))
        Path(xml).write_text("<testsuites><broken", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    assert rtg.run_all(["g"], {"g": ("x",)}, tmp_path, runner=_run) == 2


def test_validate_report_rejects_file_older_than_start(tmp_path):
    p = tmp_path / "g.xml"
    p.write_text(_GOOD_XML, encoding="utf-8")
    assert rtg.validate_report(p, time.time() + 60) is not None
    assert rtg.validate_report(p, time.time() - 60) is None


def test_main_refuses_non_empty_output_dir(tmp_path):
    (tmp_path / "leftover.xml").write_text("x", encoding="utf-8")
    assert rtg.main(["--out", str(tmp_path), "--only", "rest"]) == 2
