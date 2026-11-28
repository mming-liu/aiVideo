# -*- coding: utf-8 -*-
"""发音词典：只改送 TTS 的文本，不改展示文本（2026-11-28 O7）.

全部离线：自定义词典写在 tmp_path，请求 key 与分段用纯函数验证，不调用 provider。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tts.pronunciation_lexicon import (LexiconError, PronunciationLexicon,
                                       apply_tts_text, cached_lexicon,
                                       load_lexicon, reset_lexicon_cache)
from tts.synthesis_job import SpeechUnit, request_key_for

# ---------------------------------------------------------------------------
# 装载与改写
# ---------------------------------------------------------------------------
def test_builtin_lexicon_rewrites_tts_text_only(monkeypatch):
    monkeypatch.delenv("TTS_PRONUNCIATION_LEXICON", raising=False)
    reset_lexicon_cache()
    text = "可汗与单于在参差的山脊上立誓"
    tts_text = apply_tts_text(text)
    assert text == "可汗与单于在参差的山脊上立誓"     # 展示文本原样
    assert tts_text == "克寒与蝉于在岑差的山脊上立誓"
    assert len(tts_text) == len(text), "同音改写必须保持字数（字幕/时长不变）"


def test_lexicon_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TTS_PRONUNCIATION_LEXICON", "off")
    reset_lexicon_cache()
    assert apply_tts_text("可汗") == "可汗"
    assert cached_lexicon().enabled is False


def test_custom_json_lexicon_is_loaded_and_merges_builtins(tmp_path, monkeypatch):
    path = tmp_path / "lex.json"
    path.write_text(json.dumps({"entries": {"青鸾": "青鸾儿", "可汗": "珂寒"}},
                               ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("TTS_PRONUNCIATION_LEXICON", str(path))
    reset_lexicon_cache()
    lex = cached_lexicon()
    assert lex.apply("可汗") == "珂寒", "文件条目优先于内置"
    assert lex.apply("单于") == "蝉于", "未覆盖的内置条目仍生效"
    assert lex.apply("青鸾") == "青鸾儿"


def test_custom_yaml_lexicon_is_supported(tmp_path, monkeypatch):
    path = tmp_path / "lex.yaml"
    path.write_text("entries:\n  青鸾: 青鸾儿\n", encoding="utf-8")
    monkeypatch.setenv("TTS_PRONUNCIATION_LEXICON", str(path))
    reset_lexicon_cache()
    assert cached_lexicon().apply("青鸾鸣") == "青鸾儿鸣"


def test_longest_match_wins_and_no_reentry(tmp_path):
    lex = PronunciationLexicon({"长": "X", "长安": "Y", "安": "Z"})
    assert lex.apply("长安") == "Y", "最长匹配优先"
    assert lex.apply("长安安") == "YZ"
    assert lex.apply("长") == "X"


def test_disabled_lexicon_returns_input_unchanged():
    lex = PronunciationLexicon({})
    assert lex.enabled is False
    assert lex.apply("任意文本  保留 空白") == "任意文本  保留 空白"


@pytest.mark.parametrize("payload", [
    "not-a-dict",                                    # 顶层不是对象
    json.dumps({"entries": ["a", "b"]}),             # entries 不是对象
    json.dumps({"entries": {"k": 1}}),               # 值不是字符串
])
def test_invalid_lexicon_fails_closed(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(LexiconError):
        load_lexicon(path)


def test_missing_lexicon_file_fails_closed(tmp_path):
    with pytest.raises(LexiconError):
        load_lexicon(tmp_path / "nope.json")


def test_digest_is_stable_and_sensitive():
    a = PronunciationLexicon({"可汗": "克寒"})
    b = PronunciationLexicon({"可汗": "克寒"})
    c = PronunciationLexicon({"可汗": "珂寒"})
    assert a.digest() == b.digest()
    assert a.digest() != c.digest()


# ---------------------------------------------------------------------------
# 身份：request key 绑定 tts_text
# ---------------------------------------------------------------------------
def _unit(text: str, tts_text: str = "") -> SpeechUnit:
    return SpeechUnit(unit_id="u1", kind="dialogue", text=text, tts_text=tts_text,
                      scope={"chapter": 1}, script_revision="r1",
                      voice_profile_id="vp-1", provider="dashscope_sdk",
                      model_id="m1", speaker="s1", sample_rate=24000,
                      mapping_version="mv1")


def test_request_key_changes_only_when_tts_text_changes():
    base = request_key_for(_unit("可汗"))
    assert request_key_for(_unit("可汗", "")) == base, "空 tts_text 与展示文本同义"
    assert request_key_for(_unit("可汗", "可汗")) == base, "相同 tts_text 不改 key"
    assert request_key_for(_unit("可汗", "克寒")) != base, "读音改写必须换 key（缓存失效）"
    # 展示文本不同、tts_text 相同 ⇒ key 仍然不同（展示文本也进 key）
    assert request_key_for(_unit("单于", "克寒")) != base


def test_lexicon_update_invalidates_only_matched_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("TTS_PRONUNCIATION_LEXICON", raising=False)
    reset_lexicon_cache()
    before_hit = request_key_for(_unit("可汗立誓", apply_tts_text("可汗立誓")))
    before_miss = request_key_for(_unit("他抬起头", apply_tts_text("他抬起头")))

    path = tmp_path / "lex.json"
    path.write_text(json.dumps({"entries": {"可汗": "珂寒"}}), encoding="utf-8")
    monkeypatch.setenv("TTS_PRONUNCIATION_LEXICON", str(path))
    reset_lexicon_cache()

    after_hit = request_key_for(_unit("可汗立誓", apply_tts_text("可汗立誓")))
    after_miss = request_key_for(_unit("他抬起头", apply_tts_text("他抬起头")))
    assert after_hit != before_hit, "命中句必须失效重算"
    assert after_miss == before_miss, "未命中句必须继续复用缓存"
