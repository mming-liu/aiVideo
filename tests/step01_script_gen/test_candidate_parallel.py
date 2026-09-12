# -*- coding: utf-8 -*-
"""v6.65 多候选并行生成（_generate_script_candidates）单元测试."""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _setup(monkeypatch, scores, fail_idx=frozenset(), delay=0.0):
    """mock gen/parse/score；scores[i] = 第 idx 个候选的分数."""
    import step01_script_gen.main as m
    calls = {"idx": [], "start": [], "end": []}

    def fake_gen(novel_text, bank=None, objects=None, outline=None, temperature=0.2, model=None):
        idx = len(calls["idx"])
        calls["idx"].append(idx)
        calls["start"].append(time.monotonic())
        if delay:
            time.sleep(delay)
        calls["end"].append(time.monotonic())
        if idx in fail_idx:
            raise RuntimeError("mock LLM 失败")
        return f"raw-{idx}"

    def fake_parse(raw, novel_text=""):
        return {"shots": [], "mock_from": raw}

    def fake_score(script, novel_text):
        idx = int(script["mock_from"].split("-")[1])
        return {"total": scores[idx] if idx < len(scores) else 0, "detail": f"mock{idx}"}

    monkeypatch.setattr(m, "gen_spy_script", fake_gen)
    monkeypatch.setattr(m, "parse_response", fake_parse)
    monkeypatch.setattr(m, "_score_script", fake_score)
    return calls


def test_parallel_topk3_all_success(monkeypatch):
    """TOPK=3 → 3 候选并行完成，按分数降序返回."""
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)
    calls = _setup(monkeypatch, scores=[50, 80, 65], delay=0.2)
    t0 = time.monotonic()
    cands = m._generate_script_candidates("小说")
    elapsed = time.monotonic() - t0

    assert [c[0]["total"] for c in cands] == [80, 65, 50]  # 分数降序
    assert sorted(calls["idx"]) == [0, 1, 2]
    # 并行验证：总耗时远小于串行（3×0.2s 串行=0.6s+，并行≈0.2s+）
    assert elapsed < 0.55


def test_parallel_retry_on_failure(monkeypatch):
    """第 1 个候选失败 → 自动补发，最终凑满 3 个."""
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)
    # 第 0 个失败；第 1/2/3 号成功
    calls = _setup(monkeypatch, scores=[0, 60, 70, 80], fail_idx={0})
    cands = m._generate_script_candidates("小说")
    assert len(cands) == 3
    assert sorted(calls["idx"]) == [0, 1, 2, 3]  # 3 成功 + 1 次失败重试
    assert [c[0]["total"] for c in cands] == [80, 70, 60]


def test_serial_topk1_zero_change(monkeypatch):
    """TOPK=1 → 只调 1 次，串行语义."""
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 1)
    calls = _setup(monkeypatch, scores=[90])
    cands = m._generate_script_candidates("小说")
    assert len(cands) == 1
    assert calls["idx"] == [0]


def test_all_fail_raises(monkeypatch):
    """全部失败 → RuntimeError（max_attempts 用尽）."""
    import step01_script_gen.main as m
    import shared.config as cfg
    import pytest
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)
    from shared.constants import SCRIPT_PARSE_RETRIES
    all_idx = frozenset(range(SCRIPT_PARSE_RETRIES * 3))
    calls = _setup(monkeypatch, scores=[], fail_idx=all_idx)
    with pytest.raises(RuntimeError):
        m._generate_script_candidates("小说")
