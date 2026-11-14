# -*- coding: utf-8 -*-
"""内容无关性的棘轮：`tests/` 里还残留多少具体作品词，只许少不许多了.

为什么需要它：AGENTS.md「内容无关性与夹具」要求样本数据与机制分离，但**没有东西
在守**——判据一旦只对某一本小说成立，换书以后回归全绿、线上全错。G05
（`tests/step01_script_gen/test_content_agnostic_guard.py`）管的是**生产代码**，
本文件补的是**测试语料**这半边。

口径
----
* 清单在 `tests/_content_binding.py`（`TERMS` + `STORY_TERMS`），与 G05 同源；
* 扫描 `tests/**` 下的 `.py` / `.json`；
* `fixtures/content_binding_baseline.json` 是**当前快照**——棘轮：
  未列出的文件必须为 0；已列出的文件不得高于基线；基线只允许下调或删条目；
* 「历史真实事故的抓取件」允许保留作品词（它们是独立回归数据，不是默认依赖），
  但必须出现在基线的 `historical` 段里并写明理由，否则一视同仁被拦。

跑法：`python -m pytest tests/test_content_binding_ratchet.py -q`
下调基线：跑 `python tests/test_content_binding_ratchet.py --dump` 打印新快照。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _content_binding import (  # noqa: E402
    ALL_TERMS, TERMS, count_terms, iter_corpus,
)

BASELINE_PATH = TESTS_DIR / "fixtures" / "content_binding_baseline.json"

#: 新增/已改造的受控语料：必须**完全**内容无关（不享受历史抓取件的豁免）。
PARAMETERIZED_CORPORA = (
    "step01_script_gen/fixtures/script_preflight",
    "step01_script_gen/fixtures/repair_loop",
    "step01_script_gen/fixtures/audit_narrative_families",
    "shared/fixtures/prompt_contradiction",
    "shared/fixtures/execution_contract",
    "step01_script_gen/fixtures/step01_audit_min",
)


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _current() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in iter_corpus(TESTS_DIR):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:                                   # pragma: no cover
            continue
        n = count_terms(text)
        if n:
            out[path.relative_to(TESTS_DIR).as_posix()] = n
    return out


def test_no_new_file_gets_bound_to_the_novel():
    base = _baseline()["files"]
    new = sorted(rel for rel in _current() if rel not in base)
    assert not new, (
        "以下文件新增了具体作品词而基线里没有（新夹具必须内容无关）：\n  "
        + "\n  ".join("%s（%d 处）" % (r, _current()[r]) for r in new)
        + "\n确属历史抓取件请在 baseline 的 historical 段申报理由后下调/新增条目。")


def test_no_file_exceeds_its_baseline():
    base = _baseline()["files"]
    cur = _current()
    grew = {rel: (n, cur.get(rel, 0)) for rel, n in base.items() if cur.get(rel, 0) > n}
    assert not grew, "内容耦合增加了（棘轮只许下调）：\n  " + "\n  ".join(
        "%s: 基线 %d → 现在 %d" % (r, old, now) for r, (old, now) in sorted(grew.items()))


def test_baseline_has_no_stale_entries():
    base = _baseline()["files"]
    cur = _current()
    stale = sorted(rel for rel in base if cur.get(rel, 0) == 0)
    assert not stale, (
        "以下条目已经清零，请从基线删掉（下调棘轮）：\n  " + "\n  ".join(stale))


def test_parameterized_corpora_are_fully_content_agnostic():
    """受控语料是「换书可用」的前提，一条都不许有."""
    offenders = []
    for rel in PARAMETERIZED_CORPORA:
        root = TESTS_DIR / rel
        assert root.exists(), "受控语料目录不存在：%s" % rel
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".json", ".md"):
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = [t for t in ALL_TERMS if t in text]
            if hits:
                offenders.append("%s: %s" % (path.relative_to(TESTS_DIR).as_posix(), hits))
    assert not offenders, "\n".join(offenders)


def test_historical_captures_are_declared_with_a_reason():
    """允许保留作品词的只有「历史抓取件」，且必须写明理由（不得静默豁免）."""
    doc = _baseline()
    historical = doc.get("historical") or {}
    base = doc["files"]
    for rel, reason in historical.items():
        assert rel in base, "historical 里的 %s 不在 files 里" % rel
        assert len(str(reason)) >= 12, "historical 条目 %s 的理由太短" % rel


def test_binding_lexicon_covers_g05():
    """G05 保护的词必须都在本模块的清单里（否则 G05 收紧、语料门禁仍放行）."""
    import importlib.util

    guard = TESTS_DIR / "step01_script_gen" / "test_content_agnostic_guard.py"
    spec = importlib.util.spec_from_file_location("_g05_guard", guard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = [t for t in mod.TERMS if t not in ALL_TERMS]
    assert not missing, "本模块 ALL_TERMS 缺少 G05 已保护的词：%s" % missing


def _dump() -> None:
    cur = _current()
    doc = _baseline()
    doc["files"] = dict(sorted(cur.items()))
    doc["generated_at"] = "regenerated"
    print(json.dumps(doc, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--dump" in sys.argv:
        _dump()
    else:
        raise SystemExit("用法：python tests/test_content_binding_ratchet.py --dump")
