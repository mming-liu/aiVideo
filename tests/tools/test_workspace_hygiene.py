# -*- coding: utf-8 -*-
"""仓库卫生：LF 行尾规范与磁盘水位巡检（2026-11-28 O13）.

- `.gitattributes` 必须给代码/文档声明 `eol=lf`，脚本保留 `eol=crlf`；
- 跟踪的 `.py` **不得**有 CRLF 残留（显式 `-text` 钉住的证据文件豁免）；
- `workspace_size_audit.py --check-thresholds` 只读巡检：干净根退出 0，
  有越界项退出 1 并给清理建议，且**绝不写文件**。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import workspace_size_audit as audit

GIT = shutil.which("git")


def _git(args, *, cwd=_ROOT, input_bytes=None):
    return subprocess.run([GIT, "-C", str(cwd)] + args, input=input_bytes,
                          capture_output=True, check=True)


# ---------------------------------------------------------------------------
# 1) .gitattributes 规范
# ---------------------------------------------------------------------------
def test_gitattributes_declares_lf_for_code_and_docs():
    text = (_ROOT / ".gitattributes").read_text(encoding="utf-8")
    rules = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "text" and parts[2].startswith("eol="):
            rules[parts[0]] = parts[2]
    for pattern in ("*.py", "*.md", "*.json", "*.yml", "*.yaml"):
        assert rules.get(pattern) == "eol=lf", "%s 必须声明 eol=lf" % pattern
    for pattern in ("*.ps1", "*.bat"):
        assert rules.get(pattern) == "eol=crlf", "%s 必须保留 eol=crlf" % pattern


# ---------------------------------------------------------------------------
# 2) 跟踪的 .py 无 CRLF 残留
# ---------------------------------------------------------------------------
def _text_pinned(files: list[str]) -> set:
    """显式 `-text` 钉住的文件（baselines/ 证据等）按原始字节保留，豁免."""
    payload = ("\0".join(files) + "\0").encode("utf-8")
    out = _git(["check-attr", "--stdin", "-z", "text"], input_bytes=payload).stdout
    parts = out.decode("utf-8", "replace").split("\0")
    pinned = set()
    for index in range(0, len(parts) - 2, 3):
        path, _attr, value = parts[index], parts[index + 1], parts[index + 2]
        if value == "unset":
            pinned.add(path)
    return pinned


@pytest.mark.skipif(GIT is None, reason="需要 git")
def test_tracked_python_files_have_no_crlf():
    files = [p for p in _git(["ls-files", "*.py"]).stdout.decode("utf-8").split()
             if p]
    pinned = _text_pinned(files)
    offenders = []
    for rel in files:
        if rel in pinned:
            continue
        path = _ROOT / rel
        if path.is_file() and b"\r\n" in path.read_bytes():
            offenders.append(rel)
    assert offenders == [], "以下跟踪的 .py 还有 CRLF 残留：%s" % offenders[:10]


# ---------------------------------------------------------------------------
# 3) 只读水位巡检
# ---------------------------------------------------------------------------
def _make_root(tmp_path: Path, *, stale: bool = False) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "shared").mkdir()
    (root / "shared" / "mod.py").write_bytes(b"x" * 256)
    (root / "tools").mkdir()
    (root / "tools" / "tool.py").write_bytes(b"y" * 128)
    (root / ".tmp").mkdir()
    (root / ".tmp" / "fresh").mkdir()
    (root / ".tmp" / "fresh" / "junit.xml").write_text("<x/>", encoding="utf-8")
    if stale:
        old = root / ".tmp" / "old_run"
        old.mkdir()
        (old / "big.bin").write_bytes(b"z" * 2048)
        import os
        import time

        ancient = time.time() - 40 * 86400
        os.utime(old / "big.bin", (ancient, ancient))
        # 两个同内容大文件（阈值放到 1KB，避免测试里造 16MB）
        for name in ("dup_a.bin", "dup_b.bin"):
            (root / name).write_bytes(b"q" * 4096)
    return root


def test_clean_root_passes_and_writes_nothing(tmp_path):
    root = _make_root(tmp_path)
    before = sorted(p.name for p in root.iterdir())
    report = audit.check_thresholds(root, stale_days=14, dup_min_bytes=1024)
    assert report["schema"] == "workspace.thresholds/1"
    assert report["ok"] is True
    assert report["violations"] == []
    assert report["suggestions"] == []
    assert report["writes"] == []
    assert sorted(p.name for p in root.iterdir()) == before, "巡检不得写文件"


def test_stale_and_duplicate_roots_report_suggestions(tmp_path):
    root = _make_root(tmp_path, stale=True)
    report = audit.check_thresholds(root, stale_days=14, dup_min_bytes=1024)
    assert report["ok"] is False
    assert set(report["violations"]) >= {"tmp_stale_dirs", "duplicate_large_files"}
    stale = next(c for c in report["checks"] if c["id"] == "tmp_stale_dirs")
    assert stale["count"] == 1 and stale["items"][0]["path"] == "old_run"
    dups = next(c for c in report["checks"] if c["id"] == "duplicate_large_files")
    assert dups["count"] == 1
    assert sorted(dups["items"][0]["paths"]) == ["dup_a.bin", "dup_b.bin"]
    assert len(report["suggestions"]) == 2
    assert all("old_run" in s or "dup_a.bin" in s for s in report["suggestions"])


def test_core_red_line_is_enforced(tmp_path):
    root = _make_root(tmp_path)
    report = audit.check_thresholds(root, core_limit_bytes=64)
    core = next(c for c in report["checks"] if c["id"] == "core_code_size")
    assert core["ok"] is False and core["bytes"] > 64
    assert report["ok"] is False


def test_cli_check_thresholds_exit_codes(tmp_path, capsys):
    clean = _make_root(tmp_path / "clean")
    assert audit.main(["--root", str(clean), "--check-thresholds",
                       "--stale-days", "14"]) == 0
    payload = json.loads(capsys.readouterr().out.split("[建议]")[0])
    assert payload["ok"] is True

    dirty = _make_root(tmp_path / "dirty", stale=True)
    # 用极小阈值与陈旧目录造出越界项
    import os
    import time

    ancient = time.time() - 40 * 86400
    os.utime(dirty / ".tmp" / "old_run", (ancient, ancient))
    assert audit.main(["--root", str(dirty), "--check-thresholds",
                       "--stale-days", "14", "--core-limit-mb", "0.00001"]) == 1
    out = capsys.readouterr().out
    assert "[建议]" in out
