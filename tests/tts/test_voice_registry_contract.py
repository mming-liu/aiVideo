# -*- coding: utf-8 -*-
"""音色注册表与说话人解析的契约用例（离线，0 次合成）.

对应 2026-10-31 复核结论：

1. 同一个模块曾定义两个同名 VoiceRegistry，生效类没有 get_voice，Sambert 通道静默
   退回旁白音色——现在只有一个注册表类（旧池算法作为混入），且"能力缺失"必须显式失败；
2. 说话人解析以**结构化条目**为准，不再用 main_subject 长度兜底，未解析就报错。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tts.voice_registry import (CHARACTER_VOICES, NARRATOR_VOICE, VoiceRegistry,
                                IllegalSpeakerError)
from step03_tts_audio import shot_tts


def _caps(supports_speaker: bool):
    return SimpleNamespace(supports_speaker=supports_speaker, model_id="m-1",
                           legal_speakers=(), provider="dashscope_sdk")


def test_legacy_pool_assigns_by_gender_and_never_narrator(tmp_path):
    reg = VoiceRegistry(tmp_path / "voice_registry.json")
    female = reg.get_voice("听香", "female", "young")
    male = reg.get_voice("张十三", "male", "young")

    assert female["id"] != NARRATOR_VOICE["id"]
    assert female["gender"] == "female"
    assert male["id"] != NARRATOR_VOICE["id"]
    assert male["gender"] == "male"
    assert {v["id"] for v in CHARACTER_VOICES} >= {female["id"], male["id"]}


def test_assignment_is_stable_after_reload(tmp_path):
    path = tmp_path / "voice_registry.json"
    first = VoiceRegistry(path).get_voice("听香", "female", "young")
    again = VoiceRegistry(path).get_voice("听香", "female", "young")
    assert first["id"] == again["id"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mapping"]["听香"]["id"] == first["id"]


def test_profile_contract_still_holds(tmp_path):
    reg = VoiceRegistry(tmp_path / "voice_registry.json")
    legal = ("spk-a", "spk-b")
    rec = reg.profile_for("张十三", provider="qwen-tts", legal_speakers=legal,
                          mapping_version="v1")
    assert rec["speaker"] in legal
    again = reg.profile_for("张十三", provider="qwen-tts", legal_speakers=legal,
                            mapping_version="v1")
    assert again["voice_profile_id"] == rec["voice_profile_id"]
    with pytest.raises(IllegalSpeakerError):
        reg.profile_for("李四", provider="qwen-tts", legal_speakers=legal,
                        mapping_version="v1", speaker="spk-x")


def test_missing_get_voice_fails_loudly(tmp_path):
    """注册表能力缺失必须报错，不能静默拿旁白音色顶替."""
    from tts.synthesis_job import SynthesisError
    broken = object()          # 有 mapping 没有 get_voice
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_voice(broken, role_id="听香", provider_name="dashscope_sdk",
                               caps=_caps(False), gender="female", age_range="young")
    assert exc.value.code == "voice_registry_unsupported"


def test_narrator_keeps_narrator_voice(tmp_path):
    reg = VoiceRegistry(tmp_path / "voice_registry.json")
    voice = shot_tts.resolve_voice(reg, role_id=shot_tts.NARRATOR_ROLE,
                                   provider_name="dashscope_sdk", caps=_caps(False))
    assert voice["model_id"] == NARRATOR_VOICE["id"]
    assert "听香" not in reg.mapping          # 旁白不占用角色池


# ---------------------------------------------------------------- 说话人解析边界
# 契约（2026-10-31 复核 P1）：
#   多个对白条目 ⇒ 拒收；唯一条目 ⇒ 用条目身份（成员校验/冲突校验）；
#   无条目 ⇒ 允许旧稿前缀；什么都没有 ⇒ 未解析报错。
# 用匿名角色，避免把某一章的写法固化成通用契约。

def _entries(monkeypatch, entries):
    monkeypatch.setattr(
        "step01_script_gen.plan_feasibility.shot_dialogue_entries",
        lambda shot, plan: list(entries))


def _chars(*names):
    return [{"identifier": n} for n in names]


def test_single_entry_speaker_wins(monkeypatch):
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"}])
    got = shot_tts.resolve_shot_speaker({"index": 1}, "一句台词", _chars("甲", "乙"),
                                        {"dummy": True})
    assert got == {"role": "甲", "source": "entry"}


def test_multiple_entries_are_rejected_even_with_recognizable_prefix(monkeypatch):
    """多条对白 + 开头有可识别名字，仍然拒收——不能整体套给那个人."""
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"},
                           {"kind": "dialogue", "index": 2, "speaker": "乙"}])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker({"index": 1}, "甲道：一人一半", _chars("甲", "乙"),
                                      {"dummy": True})
    assert exc.value.code == "speaker_ambiguous"


def test_empty_character_table_is_a_dependency_error(monkeypatch):
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"}])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker({"index": 1}, "一句台词", [],
                                      {"dummy": True})
    assert exc.value.code == "characters_missing"


def test_unknown_structured_speaker_is_rejected(monkeypatch):
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "丙"}])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker({"index": 1}, "一句台词", _chars("甲", "乙"),
                                      {"dummy": True})
    assert exc.value.code == "speaker_unknown"


def test_prefix_conflict_with_structured_speaker_is_rejected(monkeypatch):
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"}])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker({"index": 1}, "乙道：一句台词", _chars("甲", "乙"),
                                      {"dummy": True})
    assert exc.value.code == "speaker_conflict"


def test_legacy_without_entries_uses_explicit_prefix(monkeypatch):
    _entries(monkeypatch, [])
    got = shot_tts.resolve_shot_speaker({"index": 1}, "甲道：一句台词", _chars("甲", "乙"),
                                        {"dummy": True})
    assert got == {"role": "甲", "source": "prefix"}


def test_legacy_without_prefix_or_entry_is_unresolved(monkeypatch):
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker({"index": 1}, "没有前缀的台词", _chars("甲"),
                                      {"dummy": True})
    assert exc.value.code == "speaker_unresolved"


def test_entry_without_speaker_falls_back_to_prefix(monkeypatch):
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1}])
    got = shot_tts.resolve_shot_speaker({"index": 1}, "甲道：一句台词", _chars("甲"),
                                        {"dummy": True})
    assert got == {"role": "甲", "source": "prefix"}


# ---------------------------------------------------------------- 真实入口（build_speech_units）

def _legacy_script(text):
    """最小脚本：一个镜头、一条对白、一个节拍（无 voice_adaptations ⇒ 无结构化 speaker）."""
    return {
        "chapter_index": 1,
        "chapter_outline": {
            "shot_beats": ["节拍：某人说话｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % text],
            "dialogue_lines": [text],
            "narration_lines": [],
        },
        "shots": [{"index": 1, "narration_segment": text, "duration_sec": 4.0,
                   "plan_refs": {"beats": [1], "lines": [1]}}],
        "characters": [{"identifier": "甲", "gender": "male", "age_range": "young"}],
    }


def test_build_speech_units_resolves_legacy_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    from tts import voice_registry as vreg
    vreg.reset_registry_cache()
    units = shot_tts.build_speech_units(_legacy_script("甲道：一句台词"), scope={"chapter": 1},
                                        script_revision="rev-1")
    assert len(units) == 1
    assert units[0].model_id and units[0].model_id != NARRATOR_VOICE["id"]
    vreg.reset_registry_cache()


def test_build_speech_units_rejects_ambiguous_entries(monkeypatch, tmp_path):
    from tts.synthesis_job import SynthesisError
    from tts import voice_registry as vreg
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    vreg.reset_registry_cache()
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"},
                           {"kind": "dialogue", "index": 2, "speaker": "乙"}])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.build_speech_units(_legacy_script("甲道：一句台词"), scope={"chapter": 1},
                                    script_revision="rev-1")
    assert exc.value.code == "speaker_ambiguous"
    vreg.reset_registry_cache()

# ---------------------------------------------------------------- 严格前缀证据（2026-10-31 晚）

def test_name_at_start_without_speech_verb_is_not_a_prefix():
    """'甲站在门边……' 不是发言——detect_speaker 会认，严格前缀不认."""
    from shared.dialogue import speaker_prefix
    chars = [{"identifier": "甲"}]
    assert speaker_prefix("甲站在门边，望着江面。", chars) is None
    assert speaker_prefix("甲道：走吧。", chars) == "甲"
    assert speaker_prefix("甲低声道：走吧。", chars) == "甲"


def test_structured_speaker_not_conflicted_by_mere_mention(monkeypatch):
    """有结构化 speaker 时，普通叙述里的名字不算冲突证据."""
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"}])
    got = shot_tts.resolve_shot_speaker(
        {"index": 1}, "甲站在门边，望着江面。", _chars("甲", "乙"), {"dummy": True})
    assert got == {"role": "甲", "source": "entry"}


def test_structured_speaker_conflicts_with_real_prefix(monkeypatch):
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [{"kind": "dialogue", "index": 1, "speaker": "甲"}])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker(
            {"index": 1}, "乙道：这句是我说的", _chars("甲", "乙"), {"dummy": True})
    assert exc.value.code == "speaker_conflict"


def test_legacy_mention_without_prefix_is_unresolved(monkeypatch):
    from tts.synthesis_job import SynthesisError
    _entries(monkeypatch, [])
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker(
            {"index": 1}, "甲站在门边，望着江面。", _chars("甲"), {"dummy": True})
    assert exc.value.code == "speaker_unresolved"

# ---------------------------------------------------------------- 完整身份匹配（2026-10-31 晚三）

def test_short_and_long_names_do_not_cross_match():
    """角色表只有"王"时，"老王道："不能被认成王；反之亦然——不做后缀猜测."""
    from shared.dialogue import speaker_prefix
    assert speaker_prefix("老王道：走吧。", [{"identifier": "王"}]) is None
    assert speaker_prefix("王道：走吧。", [{"identifier": "老王"}]) is None
    assert speaker_prefix("王道：走吧。", [{"identifier": "王"}]) == "王"
    assert speaker_prefix("老王道：走吧。", [{"identifier": "老王"}]) == "老王"


def test_full_name_with_modifier_is_recognized():
    from shared.dialogue import speaker_prefix
    chars = [{"identifier": "张十三"}]
    assert speaker_prefix("张十三道：走。", chars) == "张十三"
    assert speaker_prefix("张十三低声道：走。", chars) == "张十三"
    assert speaker_prefix("张十三说道：走。", chars) == "张十三"
    assert speaker_prefix("张十三站在门边。", chars) is None


def test_empty_character_table_is_rejected_at_entry():
    """空角色表：前缀函数不猜名字，身份入口明确拒收."""
    from shared.dialogue import speaker_prefix
    from tts.synthesis_job import SynthesisError
    assert speaker_prefix("甲道：走吧。", []) is None
    with pytest.raises(SynthesisError) as exc:
        shot_tts.resolve_shot_speaker({"index": 1}, "甲道：走吧。", [], None)
    assert exc.value.code == "characters_missing"
