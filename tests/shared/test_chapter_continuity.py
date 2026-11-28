# -*- coding: utf-8 -*-
"""跨章连续性：终态提取与承接告警（2026-11-28 O9）.

匿名两章夹具验证：提取确定性、字段完整度、跨章突变（换装/伤愈/道具消失/场景
硬跳/时间跳变）都能给出 advisory 告警，且**不阻断**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import chapter_continuity as cc


def _shot(index: int, *, scene: str = "山道", prompt: str = "",
          lf_desc: str = "", names=(), props=(), time_hint: str = "",
          subject: str = "") -> dict:
    layouts = {"ff": [{"name": name, "on_screen": True, "props": list(props)}
                      for name in names]}
    return {
        "index": index,
        "scene": scene,
        "location": scene,
        "main_subject": subject or (names[0] if names else ""),
        "prompt": prompt or ("[%s: 青衣，束袖]" % names[0] if names else ""),
        "lf_desc": lf_desc or time_hint,
        "color_tone": "冷蓝",
        "frame_layouts": layouts,
    }


def _script(*shots, chapter: int = 1) -> dict:
    return {"book_name": "匿名书", "chapter_index": chapter,
            "script_revision": "rev-%d" % chapter, "shots": list(shots)}


# ---------------------------------------------------------------------------
# 提取
# ---------------------------------------------------------------------------
def test_extract_is_deterministic_and_complete():
    script = _script(
        _shot(1, scene="山道", names=["甲"], props=["剑"]),
        _shot(2, scene="古庙", names=["甲", "乙"], props=["剑", "灯"],
              prompt="[甲: 青衣染血，束袖][乙: 白衣，披发]",
              lf_desc="夜色里古庙门缓缓合上", subject="甲"),
    )
    first = cc.extract_end_state(script, chapter=2)
    second = cc.extract_end_state(script, chapter=2)
    assert first == second, "同一份剧本两次提取必须逐字段相同"
    assert first["schema"] == cc.SCHEMA
    assert first["chapter"] == 2
    assert first["last_shot_index"] == 2
    assert first["scene"] == "古庙"
    assert first["time_of_day"] == "夜"
    assert first["final_frame"] == "夜色里古庙门缓缓合上"
    assert [c["name"] for c in first["characters_on_screen"]] == ["甲", "乙"]
    assert first["characters_on_screen"][0]["appearance"] == "青衣染血，束袖"
    assert first["props"] == ["剑", "灯"]
    assert first["source"]["shot_count"] == 2


def test_extract_empty_script_degrades():
    state = cc.extract_end_state({"shots": []})
    assert state["last_shot_index"] is None
    assert state["characters_on_screen"] == []
    assert state["source"]["shot_count"] == 0


def test_write_and_load_roundtrip(tmp_path):
    script = _script(_shot(1, names=["甲"]))
    path = tmp_path / "chapter_end_state.json"
    cc.write_end_state(script, path, chapter=3)
    loaded = cc.load_end_state(path)
    assert loaded["chapter"] == 3
    assert loaded == cc.extract_end_state(script, chapter=3)


def test_load_rejects_wrong_schema(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "other/1"}), encoding="utf-8")
    with pytest.raises(ValueError):
        cc.load_end_state(path)
    with pytest.raises(ValueError):
        cc.load_end_state(tmp_path / "missing.json")


# ---------------------------------------------------------------------------
# 告警
# ---------------------------------------------------------------------------
def _prev_state(**overrides) -> dict:
    state = cc.extract_end_state(_script(
        _shot(9, scene="古庙", names=["甲", "乙"], props=["剑", "灯"],
              prompt="[甲: 青衣染血][乙: 白衣]", lf_desc="夜色，庙门合上")), chapter=1)
    state.update(overrides)
    return state


def test_clean_continuation_has_no_warnings():
    prev = _prev_state()
    nxt = _script(_shot(1, scene="古庙", names=["甲", "乙"], props=["剑", "灯"],
                        prompt="[甲: 青衣染血][乙: 白衣]", lf_desc="夜色未散"))
    assert cc.compare_continuity(prev, nxt) == []


def test_appearance_change_is_reported():
    prev = _prev_state()
    nxt = _script(_shot(1, scene="古庙", names=["甲", "乙"], props=["剑", "灯"],
                        prompt="[甲: 崭新锦袍][乙: 白衣]", lf_desc="夜色"))
    codes = [p["code"] for p in cc.compare_continuity(prev, nxt)]
    assert "appearance_changed" in codes
    problem = next(p for p in cc.compare_continuity(prev, nxt)
                   if p["code"] == "appearance_changed")
    assert problem["character"] == "甲"
    assert problem["severity"] == "advisory"


def test_absent_character_prop_scene_and_time_are_reported():
    prev = _prev_state()
    nxt = _script(_shot(1, scene="城南官道", names=["乙"], props=[],
                        prompt="[乙: 白衣]", lf_desc="清晨，官道无人"))
    codes = {p["code"] for p in cc.compare_continuity(prev, nxt)}
    assert {"character_absent_at_start", "scene_hard_jump", "time_of_day_jump",
            "props_missing_at_start"} <= codes


def test_compare_rejects_unknown_schema():
    with pytest.raises(ValueError):
        cc.compare_continuity({"schema": "nope"}, _script(_shot(1)))


def test_cli_extract_and_check(tmp_path, capsys):
    from tools import chapter_continuity as cli

    chapter1 = tmp_path / "chapter_001"
    chapter2 = tmp_path / "chapter_002"
    chapter1.mkdir()
    chapter2.mkdir()
    script1 = _script(_shot(9, scene="古庙", names=["甲"], props=["剑"],
                            prompt="[甲: 青衣]", lf_desc="夜色"), chapter=1)
    script2 = _script(_shot(1, scene="古庙", names=["甲"], props=["剑"],
                            prompt="[甲: 青衣]", lf_desc="夜色"), chapter=2)
    (chapter1 / "script_result.json").write_text(
        json.dumps(script1, ensure_ascii=False), encoding="utf-8")
    (chapter2 / "script_result.json").write_text(
        json.dumps(script2, ensure_ascii=False), encoding="utf-8")
    state_path = chapter1 / "chapter_end_state.json"

    assert cli.main(["--script", str(chapter1 / "script_result.json"),
                     "--chapter", "1", "--out", str(state_path)]) == 0
    assert state_path.is_file()

    assert cli.main(["--prev", str(state_path),
                     "--script", str(chapter2 / "script_result.json"),
                     "--chapter", "2"]) == 0, "整洁承接应无告警"

    broken = _script(_shot(1, scene="别处", names=[], props=[], lf_desc="正午"),
                     chapter=2)
    (chapter2 / "script_result.json").write_text(
        json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["--prev", str(state_path),
                     "--script", str(chapter2 / "script_result.json")]) == 1
    assert "WARN" in capsys.readouterr().out
