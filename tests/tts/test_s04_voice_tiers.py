# -*- coding: utf-8 -*-
"""S04/A1：角色分级与龙套共享音色池（0 provider 调用）。

覆盖：
- 分级来源：输入声明 > 历史记录 > 本次发言/出镜结构（无帧布局不猜龙套）；
- 独占角色满池**显式失败**（不静默降级、不撞旁白）；
- 龙套共享不撞独占角色/旁白，同镜两个龙套不拿同一条声线；
- 共享桶跨章稳定（后续独占角色不得抢走已共享的音色）；
- `voice_unit_report` 在 7+ 角色的匿名单章上 `voice_uniqueness.ok=true`。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from shared.voice_fit import binding_issues
from step03_tts_audio import shot_tts as st
from tts import voice_registry as vreg
from tts.synthesis_job import SynthesisError

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """每个用例独立注册表 + 独立输出根（不碰生产 global/voice_registry.json）."""
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_NAME", "匿名分级样本")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", str(tmp_path / "voice_registry.json"))
    monkeypatch.delenv("VOICE_PRIMARY_MIN_UNITS", raising=False)
    vreg.reset_registry_cache()
    yield
    vreg.reset_registry_cache()


def _char(name: str, *, gender="male", age="young", **extra) -> dict:
    return {"identifier": name, "gender": gender, "age_range": age,
            "static_features": "匿名外观", "dynamic_features": "", **extra}


def _pack(spec: list[dict]) -> dict:
    """`spec` = [{"index", "speaker", "line", "visible": [..]}] → 匿名整章稿件.

    每镜**至多一条对白**（本包不做多人同镜对白拆分），`visible` 进帧布局。
    """
    shots = []
    beats = []
    lines = []
    for item in spec:
        idx = int(item["index"])
        speaker = item["speaker"]
        line = item["line"]
        visible = list(item.get("visible") or ([speaker] if speaker else []))
        entries = [{"name": name, "on_screen": True, "pose": "站立"}
                   for name in visible] or [{"name": "空镜", "on_screen": True}]
        beat = "节拍：匿名动作｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (
            line or "匿名画面", "台词" if speaker else "画面")
        beats.append(beat)
        if speaker:
            lines.append(line)
            seg = "%s道：“%s”" % (speaker, line)
            refs = {"beats": [len(beats)], "lines": [len(lines)]}
        else:
            seg = "画面推进。"
            refs = {"beats": [len(beats)]}
        shots.append({
            "index": idx, "duration_sec": 5.0, "narration_segment": seg,
            "main_subject": "匿名主体", "plan_refs": refs,
            "frame_layouts": {"ff": entries, "lf": entries},
        })
    names = []
    for item in spec:
        for name in [item.get("speaker")] + list(item.get("visible") or []):
            if name and name not in names:
                names.append(name)
    plan = {"shot_beats": beats, "dialogue_lines": lines, "narration_lines": []}
    return {"chapter_outline": plan, "shots": shots,
            "characters": [_char(n) for n in names]}


def _build(script: dict, *, provider="dashscope_sdk"):
    return st.build_speech_units(script, scope={"chapter": 1, "book_index": 1,
                                                "book_name": ""},
                                 script_revision="rev-s04", provider_name=provider)


def test_tiers_come_from_speech_and_visibility_not_guesses():
    script = _pack([
        {"index": 1, "speaker": "甲", "line": "先走一步。", "visible": ["甲", "乙"]},
        {"index": 2, "speaker": "甲", "line": "跟上。", "visible": ["甲", "乙"]},
        {"index": 3, "speaker": "乙", "line": "好。", "visible": ["甲", "乙"]},
        {"index": 4, "speaker": "路人", "line": "借过。", "visible": ["路人"]},
    ])
    tiers = st.derive_voice_tiers(script, script["chapter_outline"])
    assert tiers["甲"]["tier"] == "primary"
    assert tiers["乙"]["tier"] == "primary"          # 只 1 句但出镜 3 镜
    assert tiers["路人"]["tier"] == "extra"          # 1 句 + 1 镜 → 一次性龙套
    assert tiers["Narrator"]["tier"] == "primary"

    # 没有帧布局 ⇒ 不猜龙套（保持独占分配）
    no_layout = _pack([{"index": 1, "speaker": "甲", "line": "先走一步。",
                        "visible": []}])
    for shot in no_layout["shots"]:
        shot.pop("frame_layouts", None)
    assert st.derive_voice_tiers(no_layout, no_layout["chapter_outline"])["甲"]["tier"] == "primary"


def test_declared_tier_wins_and_is_recorded():
    script = _pack([
        {"index": 1, "speaker": "甲", "line": "先走。", "visible": ["甲", "乙"]},
        {"index": 2, "speaker": "甲", "line": "再走。", "visible": ["甲", "乙"]},
        {"index": 3, "speaker": "乙", "line": "嗯。", "visible": ["乙"]},
    ])
    for char in script["characters"]:
        if char["identifier"] == "乙":
            char["voice_tier"] = "extra"
    tiers = st.derive_voice_tiers(script, script["chapter_outline"])
    assert tiers["乙"] == {**tiers["乙"], "tier": "extra", "source": "declared"}

    # 记录后跨章稳定（历史优先于结构派生）
    reg = vreg.get_registry()
    reg.record_role_tier("乙", "extra", reason="输入声明", source="declared")
    again = st.derive_voice_tiers(script, script["chapter_outline"])
    assert again["乙"]["tier"] == "extra"
    assert again["乙"]["source"] == "declared"


def test_primary_pool_exhaustion_fails_closed():
    """8 个独占角色 > 旧池 7 条角色音色（旁白音色不在角色池内）⇒ 显式失败."""
    spec = [{"index": i, "speaker": "角色%d" % i, "line": "第%d句。" % i,
             "visible": ["角色%d" % i, "角色%d" % (i % 8 + 1)]} for i in range(1, 9)]
    script = _pack(spec)
    for char in script["characters"]:
        char["voice_tier"] = "primary"
    with pytest.raises(SynthesisError) as exc:
        st.plan_role_voices(script, script["chapter_outline"],
                            registry=vreg.get_registry(),
                            provider_name="dashscope_sdk")
    assert exc.value.code == "voice_pool_exhausted"
    assert "角色" in str(exc.value)


def test_extras_share_without_colliding_primary_or_narrator():
    # 3 个独占（各出镜多镜）+ 6 个只在一镜说一句的龙套
    spec = [
        {"index": 1, "speaker": "甲", "line": "开局。", "visible": ["甲", "乙"]},
        {"index": 2, "speaker": "甲", "line": "再说。", "visible": ["甲", "乙"]},
        {"index": 3, "speaker": "乙", "line": "嗯。", "visible": ["甲", "乙", "丙"]},
        {"index": 4, "speaker": "丙", "line": "到。", "visible": ["甲", "丙"]},
        {"index": 5, "speaker": "龙套1", "line": "报！", "visible": ["龙套1"]},
        {"index": 6, "speaker": "龙套2", "line": "让路！", "visible": ["龙套2"]},
        {"index": 7, "speaker": "龙套3", "line": "借过！", "visible": ["龙套3"]},
        {"index": 8, "speaker": "龙套4", "line": "收摊了！", "visible": ["龙套4"]},
        {"index": 9, "speaker": "龙套5", "line": "小心火烛！", "visible": ["龙套5"]},
        {"index": 10, "speaker": "龙套6", "line": "关门！", "visible": ["龙套6"]},
    ]
    script = _pack(spec)
    units = _build(script)
    tiers = {u.voice_role: u.voice_tier for u in units}
    assert tiers["甲"] == "primary" and tiers["乙"] == "primary"
    assert tiers["丙"] == "primary"
    for name in ("龙套1", "龙套2", "龙套3", "龙套4", "龙套5", "龙套6"):
        assert tiers[name] == "extra"

    uniqueness = st.check_voice_uniqueness(units, None, script["characters"])
    assert uniqueness["ok"], uniqueness["conflicts"]
    assert set(uniqueness["shared_roles"]) == {
        "龙套1", "龙套2", "龙套3", "龙套4", "龙套5", "龙套6"}

    # 龙套之间允许共用声线，但绝不撞独占角色/旁白
    reg = vreg.get_registry()
    work = reg._work_of(None)
    assert work in reg.role_tiers                     # 分级已落盘（跨章稳定）
    unique_voices = {reg.mapping[r]["id"] for r in ("甲", "乙", "丙")}
    unique_voices.add(vreg.NARRATOR_VOICE["id"])
    extra_voices = {reg.mapping[r]["id"]
                    for r in ("龙套1", "龙套2", "龙套3", "龙套4", "龙套5", "龙套6")}
    assert not (unique_voices & extra_voices)
    assert len(extra_voices) < 6           # 确实发生了共享（池只有 7 条）
    # 共享登记落盘（跨章稳定：后续独占角色不得拿走这些音色）
    assert reg.shared_buckets.get(work)
    assert vreg.get_registry().shared_buckets == reg.shared_buckets
    assert not binding_issues(units)       # 共享等级不触发 speaker 复用冲突


def test_shared_bucket_avoid_set_forces_a_distinct_voice():
    """同镜/同场会被同时听见的龙套用 `avoid` 避开对方声线；无空余则显式失败."""
    reg = vreg.get_registry()
    first = reg.get_shared_voice("龙套甲", gender="male", age_range="young")
    second = reg.get_shared_voice("龙套乙", gender="male", age_range="young",
                                  avoid=(first["id"],))
    assert second["id"] != first["id"]

    # 把剩下的角色音色全部占满 → 同场龙套没有可区分声线 ⇒ 显式失败（不静默撞音）
    from tts.voice_registry import CHARACTER_VOICES, VoicePoolExhaustedError
    busy = []
    for i, voice in enumerate(CHARACTER_VOICES):
        role = "占位%d" % i
        reg.mapping[role] = dict(voice)
        reg.used_voice_ids.add(voice["id"])
        busy.append(voice["id"])
    with pytest.raises(VoicePoolExhaustedError):
        reg.get_shared_voice("龙套丙", gender="male", age_range="young",
                             avoid=tuple(busy))


def test_shared_voice_is_stable_across_chapters(tmp_path):
    """第一章分到的龙套桶音色，第二章新增独占角色不能抢走."""
    reg = vreg.get_registry()
    reg.record_role_tier("龙套", "extra", source="declared")
    voice1 = reg.get_shared_voice("龙套", gender="male", age_range="young")
    # 第二章：同一桶里再来一个龙套（同性别/年龄段）→ 复用同一条
    reg.record_role_tier("龙套二", "extra", source="declared")
    voice2 = reg.get_shared_voice("龙套二", gender="male", age_range="young")
    assert voice2["id"] == voice1["id"]

    # 独占角色（不同桶）不得占用已被共享的音色
    for name in ("主角一", "主角二", "主角三"):
        reg.profile_for(name, provider="stub", legal_speakers=())
    assert voice1["id"] in reg.shared_voice_ids
    for role, prof in reg.profiles.get(reg._work_of(None), {}).items():
        if role.startswith("主角"):
            assert prof.get("speaker") != voice1["id"]


def test_voice_pool_expansion_channel_is_explicit_and_validated(monkeypatch):
    """扩容通道只认显式配置：合法条目扩大池容量，非法写法显式报错."""
    from tts.voice_registry import (character_voice_pool, configured_extra_speakers,
                                    configured_extra_voices, legal_speakers_for)

    base = len(character_voice_pool())
    monkeypatch.setenv("TTS_EXTRA_VOICES", "custom-male-young|male|young,"
                                           "custom-female-old|female|old")
    voices = configured_extra_voices()
    assert [v["id"] for v in voices] == ["custom-male-young", "custom-female-old"]
    assert len(character_voice_pool()) == base + 2

    monkeypatch.setenv("TTS_EXTRA_VOICES", "custom-x|unknown|young")
    with pytest.raises(ValueError, match="gender/age_range"):
        configured_extra_voices()
    monkeypatch.setenv("TTS_EXTRA_VOICES", "only-id")
    with pytest.raises(ValueError, match="非法"):
        configured_extra_voices()

    class _Caps:
        legal_speakers = ("builtin-a",)

    monkeypatch.setenv("TTS_EXTRA_SPEAKERS", "designed-x, designed-y,builtin-a")
    assert configured_extra_speakers() == ["builtin-a", "designed-x", "designed-y"]
    assert legal_speakers_for(_Caps()) == ("builtin-a", "designed-x", "designed-y")


def test_expanded_pool_lifts_the_seven_voice_ceiling():
    """8 个独占角色：内置池满 ⇒ 失败；显式扩容一条后同一输入可以通过."""
    spec = [{"index": i, "speaker": "角色%d" % i, "line": "第%d句。" % i,
             "visible": ["角色%d" % i, "角色%d" % (i % 8 + 1)]} for i in range(1, 9)]
    script = _pack(spec)
    for char in script["characters"]:
        char["voice_tier"] = "primary"
    with pytest.raises(SynthesisError):
        st.plan_role_voices(script, script["chapter_outline"],
                            registry=vreg.get_registry(), provider_name="dashscope_sdk")

    vreg.reset_registry_cache()
    os.environ["TTS_EXTRA_VOICES"] = "扩容男声|male|young"
    try:
        planned = st.plan_role_voices(script, script["chapter_outline"],
                                      registry=vreg.get_registry(),
                                      provider_name="dashscope_sdk")
        assert len(planned) == 8
        assert len({v["model_id"] for v in planned.values()}) == 8
    finally:
        os.environ.pop("TTS_EXTRA_VOICES", None)
        vreg.reset_registry_cache()


def test_voice_unit_report_ok_for_seven_plus_roles(tmp_path):
    """7+ 角色匿名单章：报告零报错、voice_uniqueness.ok=true、分级可见."""
    from tools.voice_unit_report import build_report

    spec = [
        {"index": 1, "speaker": "甲", "line": "开局。", "visible": ["甲", "乙"]},
        {"index": 2, "speaker": "甲", "line": "再说。", "visible": ["甲", "乙"]},
        {"index": 3, "speaker": "乙", "line": "嗯。", "visible": ["甲", "乙"]},
        {"index": 4, "speaker": "丙", "line": "到。", "visible": ["甲", "丙"]},
        {"index": 5, "speaker": "丁", "line": "来了。", "visible": ["丁"]},
        {"index": 6, "speaker": "戊", "line": "见过。", "visible": ["戊"]},
        {"index": 7, "speaker": "己", "line": "告退。", "visible": ["己"]},
        {"index": 8, "speaker": "庚", "line": "退下。", "visible": ["庚"]},
        {"index": 9, "speaker": "辛", "line": "领命。", "visible": ["辛"]},
        {"index": 10, "speaker": "壬", "line": "是。", "visible": ["壬"]},
    ]
    script = _pack(spec)
    report = build_report(script, plan=script["chapter_outline"])
    assert report["unit_count"] == len(spec)
    assert report["voice_uniqueness"]["ok"], report["voice_uniqueness"]["conflicts"]
    assert report["provider_calls"] == 0
    assert len({u["voice_role"] for u in report["units"]}) >= 7
    assert {u["voice_tier"] for u in report["units"]} <= {"primary", "recurring",
                                                          "minor", "extra"}
    assert json.loads(json.dumps(report))  # 报告可序列化（落盘消费方）
