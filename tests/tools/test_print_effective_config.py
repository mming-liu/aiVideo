# -*- coding: utf-8 -*-
"""tools/print_effective_config：扫描、来源优先级、脱敏（离线、零网络）."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import print_effective_config as pec  # noqa: E402


def test_scan_static_constant_alias_and_dynamic():
    src = (
        "import os\nimport os as _os\n"
        "NAME_ENV = 'FROM_CONST'\n"
        "a = os.getenv('PLAIN', '1')\n"
        "b = _os.getenv('ALIASED')\n"
        "c = os.environ.get(NAME_ENV, 'x')\n"
        "d = os.environ['SUBSCRIPT']\n"
        "e = os.getenv(f'DYN_{a}')\n"
        "os.environ['WRITTEN'] = '1'\n"
    )
    static, dynamic = pec.scan_source(src, "m.py")
    got = {(s["name"], s["default"]) for s in static}
    assert got == {("PLAIN", "1"), ("ALIASED", None), ("FROM_CONST", "x"), ("SUBSCRIPT", None)}
    assert len(dynamic) == 1 and dynamic[0]["line"] == 8


def test_scan_collects_default_conflicts(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("import os\nos.getenv('X', '1')\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("import os\nos.getenv('X', '2')\n", encoding="utf-8")
    names, _ = pec.scan(tmp_path, ("pkg",))
    assert names["X"]["defaults"] == ["1", "2"]
    assert len(names["X"]["reads"]) == 2


def test_resolve_source_priority():
    names = ["A", "B", "C", "D", "E", "F", "G"]
    res = pec.resolve_sources(
        names,
        shell={"A": "shell", "B": "shell"},
        content={"B": "content", "C": "content", "D": "content"},
        keys={"D": "keys", "E": "keys"},
        post_env={"F": "receipt"},
        cli={"A": "cli"},
        defaults={"G": ["dflt"]},
    )
    assert {k: v["source"] for k, v in res.items()} == {
        "A": "cli", "B": "shell", "C": ".env.content", "D": ".env.content",
        "E": ".env.keys", "F": "adoption_receipt", "G": "code_default"}
    assert res["D"]["value"] == "content"
    assert pec.resolve_sources(["Z"], {}, {}, {}, {}, {})["Z"] == {"value": None, "source": "unset"}


def test_mask_secrets():
    assert pec.mask("DASHSCOPE_API_KEY", "abc") == "<已设置，长度 3>"
    assert pec.mask("SOME_URL", "sk-1234567890abcd").startswith("<已设置")
    assert pec.mask("NOVEL_CHAPTER", "2") == "2"
    assert pec.mask("X_TOKEN", "") == "<空>"
    assert pec.mask("X", None) is None


def test_stage_filter_dirs():
    assert pec.STAGES["step01"] == ("step01_script_gen", "_text_gen", "shared")
    assert set(pec.STAGES["all"]) >= {"step04_merge", "tts", "_video_gen"}


def test_real_repo_json_smoke_without_plain_secrets():
    """真实仓库冒烟：只读运行；干净检出缺 .env.keys 也必须能跑。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "print_effective_config.py"),
         "--stage", "step01", "--json", "--chapter", "3"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert not re.search(r"sk-[A-Za-z0-9]{8,}", proc.stdout)
    report = json.loads(proc.stdout)
    by_name = {r["name"]: r for r in report["variables"]}
    assert by_name["NOVEL_CHAPTER"]["source"] == "cli"
    assert by_name["NOVEL_CHAPTER"]["value"] == "3"
