# -*- coding: utf-8 -*-
"""S44/P1：Step03 从候选计划缓存装载 outline."""
from __future__ import annotations

import json

from step03_tts_audio import main as m


def test_plan_for_script_loads_candidate_cache(tmp_path, monkeypatch):
    outline = {"main_events": ["甲"], "voice_plan": []}
    (tmp_path / "chapter_outline.cache.json").write_text(
        json.dumps({"outline": outline}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    assert m._plan_for_script({"shots": []}) == outline


def test_plan_for_script_prefers_script_outline(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    outline = {"main_events": ["乙"]}
    assert m._plan_for_script({"chapter_outline": outline}) == outline
