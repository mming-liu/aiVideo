# -*- coding: utf-8 -*-
"""tools/ruff_gate：棘轮比较逻辑（只测纯函数，不依赖 ruff 本身）."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import ruff_gate  # noqa: E402


def _diag(path, code, name):
    return {"filename": str(ROOT / path), "code": code,
            "message": f"`{name}` imported but unused" if code == "F401"
            else f"Undefined name `{name}`"}


def test_counter_ignores_line_numbers_and_uses_relative_paths():
    c = ruff_gate.to_counter([_diag("a/b.py", "F401", "os"), _diag("a/b.py", "F401", "os")])
    assert c == Counter({("a/b.py", "F401", "os"): 2})


def test_new_entry_is_reported_and_removed_entry_is_not_a_failure():
    base = Counter({("a.py", "F401", "os"): 1, ("b.py", "F401", "re"): 1})
    cur = Counter({("a.py", "F401", "os"): 2})
    added, removed = ruff_gate.compare(cur, base)
    assert added == [(("a.py", "F401", "os"), 1)]
    assert removed == [(("b.py", "F401", "re"), 1)]


def test_baseline_roundtrip(tmp_path):
    c = Counter({("x.py", "F811", "f"): 1, ("y.py", "F401", "z"): 3})
    p = tmp_path / "b.json"
    ruff_gate.dump_baseline(c, p)
    assert ruff_gate.load_baseline(p) == c


def test_main_fails_on_new_f821_and_new_f401(tmp_path, monkeypatch):
    monkeypatch.setattr(ruff_gate, "BASELINE", tmp_path / "b.json")
    ruff_gate.dump_baseline(Counter(), tmp_path / "b.json")
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [_diag("m.py", "F401", "os")])
    assert ruff_gate.main([]) == 1
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [_diag("m.py", "F821", "nope")])
    assert ruff_gate.main([]) == 1
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [])
    assert ruff_gate.main([]) == 0


def test_update_refuses_increase(tmp_path, monkeypatch):
    p = tmp_path / "b.json"
    monkeypatch.setattr(ruff_gate, "BASELINE", p)
    ruff_gate.dump_baseline(Counter(), p)
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [_diag("m.py", "F401", "os")])
    assert ruff_gate.main(["--update"]) == 1
    assert ruff_gate.load_baseline(p) == Counter()


def test_init_writes_once_and_rejects_f821(tmp_path, monkeypatch):
    p = tmp_path / "b.json"
    monkeypatch.setattr(ruff_gate, "BASELINE", p)
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [_diag("m.py", "F821", "nope")])
    assert ruff_gate.main(["--init"]) == 1
    assert not p.exists()
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [_diag("m.py", "F401", "os")])
    assert ruff_gate.main(["--init"]) == 0
    assert ruff_gate.load_baseline(p) == Counter({("m.py", "F401", "os"): 1})
    assert ruff_gate.main(["--init"]) == 1
    assert ruff_gate.main([]) == 0


def test_offset_moves_only_matches_same_code_and_name():
    added = [(("pkg/new/a.py", "F401", "os"), 1), (("pkg/new/a.py", "F401", "re"), 1),
             (("other.py", "F401", "os"), 1)]
    removed = [(("pkg/main.py", "F401", "os"), 1)]
    rest, offsets = ruff_gate.offset_moves(added, removed, "pkg/main.py", "pkg/new/")
    assert offsets == [("F401", "os", "pkg/main.py", "pkg/new/a.py", 1)]
    assert rest == [(("pkg/new/a.py", "F401", "re"), 1), (("other.py", "F401", "os"), 1)]


def test_update_with_moves_accepts_moved_entries(tmp_path, monkeypatch):
    p = tmp_path / "b.json"
    monkeypatch.setattr(ruff_gate, "BASELINE", p)
    ruff_gate.dump_baseline(Counter({("pkg/main.py", "F401", "os"): 1}), p)
    monkeypatch.setattr(ruff_gate, "run_ruff", lambda: [_diag("pkg/new/a.py", "F401", "os")])
    assert ruff_gate.main([]) == 1
    args = ["--moved-from", "pkg/main.py", "--moved-to", "pkg/new/"]
    assert ruff_gate.main(args) == 0
    assert ruff_gate.main(["--update", *args]) == 0
    assert ruff_gate.load_baseline(p) == Counter({("pkg/new/a.py", "F401", "os"): 1})
