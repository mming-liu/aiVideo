# -*- coding: utf-8 -*-
"""S44/P1：Step03 从受身份绑定的候选计划缓存装载 outline."""
from __future__ import annotations

import json

import pytest

from shared.plan_cache import PlanCacheError, load_plan_cache, plan_digest, write_plan_cache
from step03_tts_audio import main as m

SCOPE = {"book_name": "杀青州", "chapter": 1}
REV = "e" * 64


def _cache(tmp_path, *, plan=None, scope=SCOPE, revision=REV):
    path = tmp_path / "chapter_outline.cache.json"
    write_plan_cache(path, plan or {"main_events": ["甲"]}, scope=scope,
                     script_revision=revision)
    return path


def test_plan_cache_loads_bound_outline(tmp_path):
    path = _cache(tmp_path)
    assert load_plan_cache(path, scope=SCOPE, script_revision=REV) == {"main_events": ["甲"]}


@pytest.mark.parametrize("scope,revision,match", [
    ({"book_name": "杀青州", "chapter": 2}, REV, "chapter"),
    ({"book_name": "别部", "chapter": 1}, REV, "book_name"),
    (SCOPE, "f" * 64, "script_revision"),
])
def test_plan_cache_rejects_wrong_identity(tmp_path, scope, revision, match):
    path = _cache(tmp_path)
    with pytest.raises(PlanCacheError, match=match):
        load_plan_cache(path, scope=scope, script_revision=revision)


def test_plan_cache_rejects_digest_mismatch_and_legacy(tmp_path):
    path = _cache(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["outline"] = {"main_events": ["被改"]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PlanCacheError, match="plan_sha256"):
        load_plan_cache(path, scope=SCOPE, script_revision=REV)

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"outline": {"main_events": ["甲"]}}), encoding="utf-8")
    with pytest.raises(PlanCacheError, match="schema"):
        load_plan_cache(legacy, scope=SCOPE, script_revision=REV)


def test_plan_for_script_loads_cache_and_checks_identity(tmp_path, monkeypatch):
    _cache(tmp_path)
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    assert m._plan_for_script({}, scope=SCOPE, script_revision=REV) == {"main_events": ["甲"]}
    with pytest.raises(PlanCacheError, match="chapter"):
        m._plan_for_script({}, scope={"book_name": "杀青州", "chapter": 2},
                           script_revision=REV)


def test_plan_for_script_prefers_script_outline(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    outline = {"main_events": ["乙"]}
    assert m._plan_for_script({"chapter_outline": outline}, scope=SCOPE,
                              script_revision=REV) == outline


def test_audio_context_uses_cached_plan_for_policy(tmp_path, monkeypatch):
    plan = {"narration_lines": [{"text": "旁白%d" % i} for i in range(1, 5)]}
    _cache(tmp_path, plan=plan)
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    got_plan, contract, entries, policy = m._audio_context(
        {"shots": []}, scope=SCOPE, revision=REV, actual_audio_present=False)
    assert got_plan == plan
    assert contract["narration_present"] is True
    assert len(entries) == 4
    assert policy.narration_tts_required is True


def test_plan_digest_is_stable_and_content_sensitive():
    assert plan_digest({"a": 1}) == plan_digest({"a": 1})
    assert plan_digest({"a": 1}) != plan_digest({"a": 2})
