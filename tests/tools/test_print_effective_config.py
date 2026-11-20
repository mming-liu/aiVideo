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


# --------------------------------------------------------------------------- 2026-11-19 复核 P2-2/P2-3
def test_wrapper_reads_and_cross_module_constants_are_resolved():
    """经包装函数 / 跨模块常量读取的配置必须被解析出来（不能静默缺席）."""
    import ast
    trees = {
        "pkg/config.py": ast.parse(
            "import os\n"
            "def _setting(name, default):\n    return float(os.getenv(name, str(default)))\n"
            "D_MIN = _setting('D_MIN_SEC', 60)\n"),
        "pkg/channel.py": ast.parse(
            "import os\n"
            "def resolve(provider_env, model_env):\n"
            "    return os.getenv(provider_env), os.getenv(model_env)\n"),
        "pkg/audit.py": ast.parse(
            "P_ENV = 'AUDIT_P'\nM_ENV = 'AUDIT_M'\n"
            "def ch():\n    from pkg.channel import resolve\n    return resolve(P_ENV, M_ENV)\n"),
        "pkg/other.py": ast.parse(
            "from pkg.audit import P_ENV\nimport os\n"
            "def f(k):\n    return os.getenv(k)\n"
            "def g():\n    return f(P_ENV), os.getenv(unknown_name())\n"),
    }
    wrappers = pec.find_wrappers(trees)
    assert {"_setting", "resolve", "f"} <= set(wrappers)
    consts = {}
    for t in trees.values():
        for k, v in pec._module_consts(t).items():
            consts.setdefault(k, set()).add(v)
    names, dyn = set(), []
    for rel, t in trees.items():
        st, dy = pec.scan_source(ast.unparse(t), rel, wrappers=wrappers, global_consts=consts)
        names |= {s["name"] for s in st}
        dyn += dy
    assert {"D_MIN_SEC", "AUDIT_P", "AUDIT_M"} <= names
    assert any("unknown_name" in d["expr"] for d in dyn), "无法求值的读取必须列为动态读取"
    assert not any(d["expr"] in ("name", "provider_env", "k") for d in dyn), \
        "包装函数内部转发形参不应被误报为动态读取"


def test_real_repo_reports_audit_repair_and_duration_keys():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "print_effective_config.py"),
         "--stage", "step01", "--json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout)
    by_name = {r["name"]: r for r in report["variables"]}
    for key in ("AUDIT_TEXT_PROVIDER", "REPAIR_TEXT_PROVIDER",
                "DURATION_MIN_SEC", "DURATION_MAX_SEC"):
        assert key in by_name, key
        assert by_name[key]["scanned"], f"{key} 的读取点没有被源码扫描解析到"
    assert report["required_unscanned"] == []


def test_required_keys_are_listed_even_when_not_scanned():
    res = pec.resolve_sources(["ONLY_REQUIRED"], {}, {}, {}, {}, {})
    assert res["ONLY_REQUIRED"] == {"value": None, "source": "unset"}
    assert "AUDIT_TEXT_PROVIDER" in pec.REQUIRED_KEYS["step01"]


def test_interpolation_follows_production_load_semantics(tmp_path):
    """shell 的 BASE 覆盖文件里的 BASE：插值后 URL 取 production（与 load_dotenv 一致）."""
    (tmp_path / ".env.content").write_text(
        "BASE=sandbox\nURL=https://${BASE}/v1\n", encoding="utf-8")
    script = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
        "import print_effective_config as pec\n"
        "from pathlib import Path\n"
        f"root = Path({str(tmp_path)!r})\n"
        "shell = dict(os.environ)\n"
        "content = pec._dotenv(root / '.env.content')\n"
        "actual = pec.production_load(root, import_config=False)\n"
        "res = pec.resolve_sources(['BASE', 'URL'], shell, content, {}, {}, {}, actual=actual)\n"
        "print(json.dumps(res))\n")
    env = dict(__import__("os").environ, BASE="production")
    env.pop("URL", None)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    assert res["BASE"] == {"value": "production", "source": "shell"}
    assert res["URL"] == {"value": "https://production/v1", "source": ".env.content"}
