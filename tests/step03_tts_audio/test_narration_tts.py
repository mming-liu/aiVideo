# -*- coding: utf-8 -*-
"""step03：图外旁白走**独立 TTS 音轨**（2026-09-30 复核第 7 轮第 3 项 + 10-03 §T2a）.

覆盖：
- 有旁白时**不能**因「模型原生发声」整段跳过 TTS（`resolve_audio_policy` 的旁白维度）；
- 一条旁白一个文件、**固定旁白声线**（不做说话人识别、不进角色音色分配）；
- 旁白在成稿里**没有连续落点** ⇒ 显式失败（复核点名的「声明有旁白但正文缺失」）；
- 旁白比承载它的镜头片段还长 ⇒ `duration_rejected`，**不截句**、**保留基线**；
- **超长旁白不再硬失败**（§T2a 取代「单段 300 字」实现预期）：窗口足够 ⇒ 按 provider
  能力**无损分段**成功，拼回逐字符等于原文；窗口不够 ⇒ `duration_rejected`。

需要真实 mp3 时用 ffmpeg 合成（无模型调用）；缺 ffmpeg 时相关用例跳过。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tts_stub import StubProvider, make_mp3                              # noqa: E402
from shared.audio_policy import resolve_audio_policy                       # noqa: E402
from step03_tts_audio import shot_tts as st                                # noqa: E402
from tts.synthesis_job import DurationRejectedError                        # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")

_H1 = "拿主意？拿什么主意？"
_N1 = "四人围桌喝茶。"
_N2 = "甲起身离席。"
_BEAT = ("节拍：四人围桌喝茶随即甲起身离席｜本镜信息目的：推进｜原文依据：“%s”｜"
         "载体：旁白1" % (_N1 + _N2))


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """音色注册表隔离：候选/测试**绝不**写生产 `global/voice_registry.json`."""
    from tts import voice_registry
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", str(tmp_path / "voice_registry.json"))
    voice_registry.reset_registry_cache()
    yield
    voice_registry.reset_registry_cache()


def _script(*, narr_text=None, shot_texts=(_N1, _N2), dur=2.0):
    plan = {"shot_beats": [_BEAT],
            "narration_lines": [{"text": narr_text or (_N1 + _N2),
                                 "source": _N1 + _N2, "fact_refs": []}]}
    shots = [{"index": i + 1, "narration_segment": t, "duration_sec": dur,
              "plan_refs": {"beats": [1], "lines": []}}
             for i, t in enumerate(shot_texts)]
    return {"chapter_outline": plan, "shots": shots}


def _long_script(n: int = 40, dur: float = 6.0):
    """n 段互不相同的旁白句（拼起来等于整条旁白）——每段一个镜头."""
    chunks = ["第%d段旁白内容。" % i for i in range(1, n + 1)]
    return _script(narr_text="".join(chunks), shot_texts=tuple(chunks), dur=dur)


# ---------------------------------------------------------------------------
# 策略：有旁白就必须出音轨（原生发声不构成跳过理由）
# ---------------------------------------------------------------------------
def test_narration_keeps_native_mode_but_requires_its_own_track(monkeypatch):
    """native 模式**不变**（原声保留），但旁白必须走独立 TTS 轨（不许切 tts_mix 绕过）."""
    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setattr("shared.audio_policy.active_video_supports_audio", lambda: True)
    p = resolve_audio_policy(actual_audio_present=True, narration_present=True)
    assert p.effective_merge_mode == "native" and p.tts_required is False
    assert p.narration_tts_required is True


def test_without_narration_nothing_changes(monkeypatch):
    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setattr("shared.audio_policy.active_video_supports_audio", lambda: True)
    p = resolve_audio_policy(actual_audio_present=True, narration_present=False)
    assert p.narration_tts_required is False and p.tts_required is False


# ---------------------------------------------------------------------------
# 生成：一条旁白一个文件、固定旁白声线
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_each_narration_gets_one_file_with_the_narrator_voice(tmp_path):
    provider = StubProvider(cap=60)
    out = st.gen_narration_tts(_script(), tmp_path / "audio", provider=provider)
    assert [Path(p).name for p in out] == ["narration_01.mp3"]
    assert (tmp_path / "audio" / "narration_01.mp3").is_file()
    assert (tmp_path / "audio" / "narration_01.mp3.identity.json").is_file()
    assert len(provider.calls) == 1
    assert provider.calls[0]["text"] == _N1 + _N2          # 完整旁白正文，逐字
    assert provider.calls[0]["speaker"].startswith("stub-")  # 旁白声线，不查角色音色


@needs_ffmpeg
def test_narration_longer_than_its_shot_run_is_rejected(tmp_path):
    """旁白讲不完（承载窗口只有 2.0s，音频约 5s）⇒ duration_rejected，**不截句**."""
    # 窗口 4.0s（2 镜 × 2.0s）；估时 3.95s 排得进，实测 4.85s 讲不完 ⇒ duration 门
    provider = StubProvider(cap=60, dur_per_char=0.35, max_dur=9.0)
    with pytest.raises(DurationRejectedError) as exc:
        st.gen_narration_tts(_script(dur=2.0), tmp_path / "audio", provider=provider)
    assert "禁止截句" in str(exc.value) or "不截句" in str(exc.value)
    assert exc.value.code == "duration_rejected"


@needs_ffmpeg
def test_duration_rejection_keeps_the_previous_audio_intact(tmp_path):
    """时长拒收必须**保留基线**：盘上旧音轨逐字不动（不是"先删再生成"）."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    baseline = make_mp3(audio_dir / "narration_01.mp3", 1.0)
    before = baseline.read_bytes()
    provider = StubProvider(cap=60, dur_per_char=0.35, max_dur=9.0)
    with pytest.raises(DurationRejectedError):
        st.gen_narration_tts(_script(dur=2.0), audio_dir, provider=provider)
    assert baseline.read_bytes() == before


def test_narration_without_a_landing_fails_before_any_call(tmp_path):
    """复核点名形态：**声明有旁白、正文里却没有**（承载节拍的镜头讲不出这条旁白）.

    必须在调用 TTS 之前就失败（否则会生成一段在成片里没地方放的音频）。
    """
    provider = StubProvider()
    script = _script(shot_texts=("甲端起茶盏。", "丙把茶盏放下。"))
    with pytest.raises(RuntimeError) as exc:
        st.gen_narration_tts(script, tmp_path / "audio", provider=provider)
    assert "没有连续落点" in str(exc.value), exc.value
    assert provider.calls == []


@needs_ffmpeg
def test_over_long_narration_is_segmented_losslessly_when_the_window_allows(tmp_path):
    """§T2a：超长旁白**不再**硬失败——窗口足够时按 provider 能力无损分段成功."""
    script = _long_script(n=40, dur=6.0)
    text = script["chapter_outline"]["narration_lines"][0]["text"]
    assert len(text) > 300          # 旧实现会在 provider 之前直接拒绝
    provider = StubProvider(cap=40)
    out = st.gen_narration_tts(script, tmp_path / "audio", provider=provider)
    assert [Path(p).name for p in out] == ["narration_01.mp3"]
    assert len(provider.calls) > 1
    assert all(len(c["text"]) <= 40 for c in provider.calls)
    assert "".join(c["text"] for c in provider.calls) == text      # 逐字符等于完整旁白
    from shared.audio_identity import read_identity
    ident = read_identity(out[0])
    assert ident["segmentation"]["lossless"] is True
    assert ident["text"] == text


@needs_ffmpeg
def test_over_long_narration_is_rejected_when_the_window_is_too_small(tmp_path):
    """窗口不够时只出 `duration_rejected`（不截音、不加速、不因拆段宣布解决）."""
    script = _long_script(n=40, dur=0.6)         # 承载窗口 24s < 估算/实测需要
    text = script["chapter_outline"]["narration_lines"][0]["text"]
    provider = StubProvider(cap=40, max_dur=60.0)
    audio_dir = tmp_path / "audio"
    with pytest.raises(DurationRejectedError) as exc:
        st.gen_narration_tts(script, audio_dir, provider=provider)
    assert exc.value.code == "duration_rejected"
    assert not (audio_dir / "narration_01.mp3").exists()


def test_no_narration_entries_is_a_noop(tmp_path):
    assert st.gen_narration_tts({"chapter_outline": {}, "shots": []},
                                tmp_path / "audio") == []


# ---------------------------------------------------------------------------
# 第 8 轮（2026-09-30 复核）：TTS 必须消费**同一份**对白／旁白分流
# ---------------------------------------------------------------------------
def _split_plan():
    return {
        "shot_beats": [
            "节拍：张十三把话说了｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _H1,
            "节拍：四人围桌喝茶｜本镜信息目的：交代｜原文依据：“%s”｜载体：旁白1" % _N1,
        ],
        "dialogue_lines": [_H1],
        "narration_lines": [{"text": _N1, "source": _N1, "fact_refs": []}],
    }


def _shot(seg, refs):
    return {"index": 1, "narration_segment": seg, "duration_sec": 4.0,
            "plan_refs": refs, "main_subject": "张十三"}


def test_async_tts_skips_a_pure_narration_shot(tmp_path, monkeypatch):
    """复核第 8 轮 P1 的原反例：**异步**路径此前没有对白筛选.

    纯旁白镜（结构化分流后对白那一份为空）不得用**角色音色**发起合成——
    否则独立旁白轨会再合成一次，tts_mix 还会消费那个逐镜文件。
    """
    import asyncio
    provider = StubProvider()
    plan = _split_plan()
    shot = _shot("四人围桌喝茶。", {"beats": [2], "lines": []})
    out = asyncio.run(st._gen_shot_tts_async(shot, tmp_path, provider, None, [],
                                             plan=plan))
    assert out is None and provider.calls == []


@needs_ffmpeg
def test_async_tts_synthesizes_only_the_dialogue_part(tmp_path):
    """混合镜：只合成**对白那一份**（旧实现把整段 narration_segment 当对白念）."""
    import asyncio
    provider = StubProvider()
    plan = _split_plan()
    shot = _shot('张十三道：“%s”%s' % (_H1, _N1), {"beats": [1, 2], "lines": [1]})
    # 说话人解析要求角色表（空表一律 characters_missing），这里给最小角色表
    asyncio.run(st._gen_shot_tts_async(shot, tmp_path, provider, None,
                                       [{"identifier": "张十三"}], plan=plan))
    assert [c["text"] for c in provider.calls] == [_H1]


def test_serial_tts_and_async_tts_agree_on_the_split(tmp_path):
    """串行与异步**同一判据**（分流单源）：同一个纯旁白镜都不出声."""
    import asyncio
    plan = _split_plan()
    shot = _shot("四人围桌喝茶。", {"beats": [2], "lines": []})
    provider = StubProvider()
    assert st.gen_shot_tts(shot, tmp_path, provider, None, [], plan=plan) is None
    assert asyncio.run(st._gen_shot_tts_async(shot, tmp_path, provider, None,
                                              [], plan=plan)) is None
    assert provider.calls == []


def test_without_a_voice_contract_the_legacy_heuristic_still_applies(tmp_path):
    """无声音契约（旧稿）⇒ 回退历史口径：纯描述镜仍跳过、带对话标记的仍合成."""
    provider = StubProvider()
    plain = {"index": 1, "narration_segment": "四人围桌喝茶。", "duration_sec": 4.0}
    assert st.gen_shot_tts(plain, tmp_path, provider, None, [], plan=None) is None
    assert provider.calls == []


@pytest.mark.parametrize("seg", [
    '张十三道：“%s”' % _H1,
    '%s' % _H1,
    '画面：%s' % _H1,                       # 冒号前缀：两个剥离函数行为不同的那一个
    '张十三道：“%s”%s' % (_H1, _N1),        # 混合镜
])
def test_the_text_we_sign_is_the_text_the_consumer_expects(seg):
    """**不变量**：生成侧签名的文本与 step04 消费侧期望的文本必须逐字一致.

    否则「身份核对」会把一条**正确**的音轨判成「与当前文本不符」，tts_mix 直接失败
    （实测反例：`strip_speaker_prefix` 与 `_split_speaker_prefix` 对「画面：…」这类
    冒号前缀行为不同，签名会落在带前缀的串上）。
    """
    from step01_script_gen.plan_feasibility import shot_voice_parts
    plan = _split_plan()
    refs = ({"beats": [1, 2], "lines": [1]} if _N1 in seg else {"beats": [1], "lines": [1]})
    shot = _shot(seg, refs)
    _raw, signed = st._dialogue_speech(shot, plan, None)
    assert signed is not None
    assert signed == shot_voice_parts(shot, plan)["dialogue"].strip(), seg


def test_narration_is_refused_when_the_dialogue_track_would_speak_it_too(tmp_path):
    """独立复核确认的形态：分流**失败**且旁白正文落在对白轨那一段里 ⇒ 拒绝生成.

    形态：旁白条目**包含**对白句（两段在正文里重叠 ⇒ 分不干净）⇒ 整段归对白；此时独立
    旁白轨再合成一次，合并时就是同一句话叠着播。宁可显式失败，也不产出会被念两遍的音轨。
    """
    provider = StubProvider()
    _line = "小弟这就回去复命。"
    _narr = "大哥不必多虑，小弟这就回去复命。"      # 旁白条目**包含**对白句
    plan = {
        "shot_beats": [
            "节拍：张十三把话说了｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _line,
            "节拍：低声交代｜本镜信息目的：交代｜原文依据：“%s”｜载体：旁白1" % _narr,
        ],
        "dialogue_lines": [_line],
        "narration_lines": [{"text": _narr, "source": _narr, "fact_refs": []}],
    }
    shot = {"index": 1, "narration_segment": _narr, "duration_sec": 4.0,
            "plan_refs": {"beats": [1, 2], "lines": [1]}}
    with pytest.raises(RuntimeError) as exc:
        st.gen_narration_tts({"chapter_outline": plan, "shots": [shot]},
                             tmp_path / "audio", provider=provider)
    assert "重复发声" in str(exc.value), exc.value
    assert provider.calls == []
