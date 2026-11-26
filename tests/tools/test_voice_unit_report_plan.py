# -*- coding: utf-8 -*-
"""S44/P2：声音报告复用 Step03 的 v2 计划缓存校验."""
from __future__ import annotations

import json

import pytest

from shared.plan_cache import PlanCacheError, write_plan_cache
from shared.quality_gate import compute_script_revision
from tools.voice_unit_report import load_report_plan


def _script(chapter=1, book="杀青州"):
    return {"book_name": book, "chapter_index": chapter,
            "shots": [{"index": 1, "motion_desc": "甲走"}]}


def test_load_report_plan_uses_v2_cache(tmp_path):
    script = _script()
    revision = compute_script_revision(script)
    write_plan_cache(tmp_path / "chapter_outline.cache.json",
                     {"narration_lines": [{"text": "旁白"}]},
                     scope={"book_name": "杀青州", "chapter": 1},
                     script_revision=revision)
    got = load_report_plan(tmp_path, script)
    assert got["narration_lines"][0]["text"] == "旁白"


def test_load_report_plan_rejects_wrong_chapter_and_digest(tmp_path):
    script = _script()
    revision = compute_script_revision(script)
    path = tmp_path / "chapter_outline.cache.json"
    write_plan_cache(path, {"narration_lines": []},
                     scope={"book_name": "杀青州", "chapter": 1},
                     script_revision=revision)
    with pytest.raises(PlanCacheError, match="chapter"):
        load_report_plan(tmp_path, _script(chapter=2))

    data = json.loads(path.read_text(encoding="utf-8"))
    data["outline"] = {"narration_lines": [{"text": "改"}]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PlanCacheError, match="plan_sha256"):
        load_report_plan(tmp_path, script)
