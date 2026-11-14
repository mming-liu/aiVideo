# -*- coding: utf-8 -*-
"""`shared.prompt_contradiction_lexicon` 的回归（词表外置与可覆盖性）.

这层解决的是「判据只对一本书成立」：原先四组词写死在
`shared/prompt_contradiction.py` 的函数体里，且是从某一本书的真实 prompt
提炼的措辞——换书即失效，而代码看起来还在跑。

本文件钉住三件事：

1. **默认值内容无关**：不含任何具体作品的人名、地名、服饰专名；判据只保留结构；
2. **可覆盖**：增量覆盖文件能加词、能切 `enumerate` 模式，未知键/非法值必须报错
   （配置写错字不许静默失效）；
3. **换题材真的生效**：给一个「都市/科幻」项目的覆盖文件后，该题材的缺陷能被
   抓到；不覆盖时同一段文本不报——证明扩展点是覆盖文件，不是判据代码。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
ROOT = TESTS_DIR.parent
for _p in (str(TESTS_DIR), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import prompt_contradiction_lexicon as lexmod      # noqa: E402
from shared.prompt_contradiction import find_contradictions    # noqa: E402
from _content_binding import ALL_TERMS                          # noqa: E402

#: 默认词表**不得**含具体作品的专名——清单从 `tests/_content_binding.py` 取，
#: 不在这里另抄一份（抄一份本身就是内容耦合，棘轮会正确地把它标出来）。
FORBIDDEN = ALL_TERMS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(lexmod.ENV_VAR, raising=False)
    lexmod.reset_cache()
    yield
    lexmod.reset_cache()


def _overlay(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "lexicon.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _use(monkeypatch, path: Path) -> None:
    monkeypatch.setenv(lexmod.ENV_VAR, str(path))
    lexmod.reset_cache()


# ── 1. 默认值 ────────────────────────────────────────────────────────────

def test_defaults_are_complete():
    doc = lexmod.defaults()
    assert doc["schema"] == lexmod.SCHEMA
    for key in lexmod.LIST_KEYS:
        assert isinstance(doc[key], tuple) and doc[key], key
    assert doc["prohibition_mode"] in ("explicit", "enumerate")


def test_defaults_carry_no_work_specific_terms():
    doc = lexmod.defaults()
    blob = json.dumps({k: list(v) if isinstance(v, tuple) else v
                       for k, v in doc.items()}, ensure_ascii=False)
    hits = [w for w in FORBIDDEN if w in blob]
    assert not hits, "默认词表里出现了具体作品专名：%s" % hits


def test_defaults_do_not_include_static_blood_words():
    """静态血迹色（暗红/血迹/血痕）必须留在表外——它表示"伤一直在"，
    与"尚未发生"不矛盾；收进来会在真实候选上误报（2026-11-14 实测）。"""
    injury = lexmod.DEFAULTS["injury_terms"]
    for word in ("暗红", "血迹", "血痕", "伤处"):
        assert word not in injury, word


def test_load_without_overlay_equals_defaults():
    assert lexmod.load(None) == lexmod.defaults()


# ── 2. 覆盖语义 ──────────────────────────────────────────────────────────

def test_add_extends_and_dedupes(tmp_path):
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "add": {"injury_terms": ["吐血", "渗血", "吐血"]}})
    doc = lexmod.load(path)
    assert doc["injury_terms"].count("渗血") == 1
    assert "吐血" in doc["injury_terms"]
    assert set(lexmod.DEFAULTS["injury_terms"]) <= set(doc["injury_terms"])


def test_set_replaces_scalar(tmp_path):
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "set": {"prohibition_mode": "enumerate"}})
    assert lexmod.load(path)["prohibition_mode"] == "enumerate"


def test_unknown_key_is_rejected(tmp_path):
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "add": {"injury_term": ["吐血"]}})  # 少一个 s
    with pytest.raises(lexmod.LexiconError):
        lexmod.load(path)


def test_illegal_mode_is_rejected(tmp_path):
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "set": {"prohibition_mode": "fuzzy"}})
    with pytest.raises(lexmod.LexiconError):
        lexmod.load(path)


def test_bad_schema_and_missing_file_are_rejected(tmp_path):
    with pytest.raises(lexmod.LexiconError):
        lexmod.load(_overlay(tmp_path, {"schema": "something.else"}))
    with pytest.raises(lexmod.LexiconError):
        lexmod.load(tmp_path / "nope.json")


def test_meta_keys_cannot_be_overridden(tmp_path):
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "set": {"version": "9999"}})
    with pytest.raises(lexmod.LexiconError):
        lexmod.load(path)


# ── 3. 换题材：覆盖文件是唯一扩展点 ──────────────────────────────────────

_URBAN_DEFECT = "甲某蹲在巷口，一团烟尘沿地砖滚过来。禁止出现任何烟尘、垃圾。"


def test_urban_defect_is_silent_without_overlay():
    """默认词表里没有「烟尘」——这不是漏，而是"默认只认通用高频实体"，
    扩展靠覆盖文件（否则默认值会随项目无限膨胀）。"""
    assert "prohibition" not in json.dumps(find_contradictions(_URBAN_DEFECT))


def test_urban_defect_fires_with_project_overlay(monkeypatch, tmp_path):
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "add": {"prohibition_terms": ["烟尘", "垃圾"]}})
    _use(monkeypatch, path)
    assert any("prohibition_conflict" in m for m in find_contradictions(_URBAN_DEFECT))


def test_enumerate_mode_derives_terms_without_listing(monkeypatch, tmp_path):
    """`enumerate` 模式直接从「禁止…」子句切词，不必逐词登记。"""
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "set": {"prohibition_mode": "enumerate"}})
    _use(monkeypatch, path)
    text = "甲某蹲在巷口，一团烟尘沿地砖滚过来。禁止出现任何烟尘、垃圾。"
    assert any("prohibition_conflict" in m for m in find_contradictions(text))


def test_enumerate_mode_ignores_generic_nouns(monkeypatch, tmp_path):
    """切词模式必须把"人物/画面"这类过泛词挡在外面，否则会大面积误报。"""
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "set": {"prohibition_mode": "enumerate"}})
    _use(monkeypatch, path)
    text = "画面整体偏冷，人物立在门内。禁止出现任何其他人物、文字水印。"
    assert find_contradictions(text) == []


def test_scalar_override_changes_window_behaviour(monkeypatch, tmp_path):
    """阈值也是词表的一部分：把间距阈值调到 0，远处两段就不该再报。"""
    far = ("乙某闭目靠在车门上，风从半开的车窗灌进来，吹得帘子贴住门框又松开，"
           "远处传来断续的脚步声，右臂枪伤渗血。")
    assert any("ff_lf_stage_conflict" in m for m in find_contradictions(far))
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "set": {"pre_state_max_distance": 4}})
    _use(monkeypatch, path)
    assert find_contradictions(far) == []


# ── 4. 缓存 ──────────────────────────────────────────────────────────────

def test_cache_follows_env_and_reset(monkeypatch, tmp_path):
    base = lexmod.current()
    assert "overlay_path" not in base
    path = _overlay(tmp_path, {"schema": lexmod.OVERLAY_SCHEMA,
                               "add": {"blade_terms": ["镰刀"]}})
    _use(monkeypatch, path)
    assert "镰刀" in lexmod.current()["blade_terms"]
    lexmod.reset_cache()
    assert "镰刀" in lexmod.current()["blade_terms"]   # 环境变量仍在
    monkeypatch.delenv(lexmod.ENV_VAR)
    lexmod.reset_cache()
    assert "镰刀" not in lexmod.current()["blade_terms"]
