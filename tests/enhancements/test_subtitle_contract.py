# -*- coding: utf-8 -*-
"""字幕 cue 唯一源（计划 §15「字幕cue唯一源」、§C2）——判别性测试.

覆盖：旧口径适配器 / 对齐候选适配器（含逐镜回退）/ 导出与滤镜共用同一列表 /
显式字体与字形覆盖在编码前拒收。反例都断言**具体 code 与副作用**，不是"抛了异常"。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import subtitle_contract as sc  # noqa: E402

_WINDOWS_FONTS = Path("C:/Windows/Fonts")


def _first_existing(*candidates) -> Path | None:
    for path in candidates:
        p = Path(path)
        if p.is_file():
            return p
    return None


def _cjk_font_path() -> Path | None:
    return _first_existing(_WINDOWS_FONTS / "simhei.ttf", _WINDOWS_FONTS / "msyh.ttc",
                           "/usr/share/fonts/truetype/arphic/uming.ttc",
                           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def _latin_font_path() -> Path | None:
    return _first_existing(_WINDOWS_FONTS / "arial.ttf", _WINDOWS_FONTS / "Deng.ttf",
                           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def parse_srt(text: str) -> list[dict]:
    """独立 golden 解析（不复用被测实现）：SRT 文本 → cue 记录。"""
    blocks = [b for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    cues = []
    for block in blocks:
        lines = block.split("\n")
        m = re.match(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)",
                     lines[1])
        assert m, lines
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        cues.append({"index": int(lines[0]), "text": "\n".join(lines[2:]).rstrip("\n"),
                     "start": start, "end": end})
    return cues


_SHOTS = [
    {"index": 1, "subtitle": "张十三：第一句话，很短。"},
    {"index": 2, "subtitle": "听香：第二句话更长一些，需要按标点拆分才好看。"},
]
_DURATIONS = [5.0, 5.0]
_WINDOWS = [(0.0, 5.0), (4.5, 9.5)]


# ------------------------------------------------------------------ 旧口径适配器

def test_legacy_cues_are_the_single_time_source(tmp_path):
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                                  speech_durations=[2.0, 3.0])
    assert [c.cue_id for c in plan.cues] == ["s1:1", "s2:1"]
    assert plan.cues[0].start_sec == 0.0 and plan.cues[0].end_sec == 2.0
    assert plan.cues[1].start_sec == 4.5 and plan.cues[1].end_sec == 7.5

    target = tmp_path / "sub.srt"
    sc.write_srt(plan, target)
    # 落盘文本与列表渲染逐字一致（BOM 之外）
    assert target.read_text(encoding="utf-8-sig") == sc.render_srt(plan.cues)
    # 独立解析 SRT 与 cue 列表同源
    parsed = parse_srt(target.read_text(encoding="utf-8-sig"))
    assert [(p["text"], p["start"], p["end"]) for p in parsed] == \
           [(c.text, c.start_sec, c.end_sec) for c in plan.cues]


def test_slicing_by_punctuation_is_preserved():
    text = "甲：先说话，然后转身离开门前，最后停在台阶下，抬头看了看天，又低头叹了一声。"
    assert len(text) > sc.MAX_CHARS_PER_LINE
    plan = sc.build_subtitle_plan(
        [{"index": 1, "subtitle": text}], [10.0],
        clip_windows=[(0.0, 10.0)], speech_durations=[9.0])
    assert len(plan.cues) > 1
    assert "".join(c.text for c in plan.cues) == text
    assert [c.cue_id for c in plan.cues] == [f"s1:{i}" for i in range(1, len(plan.cues) + 1)]


# ------------------------------------------------------------------ 两侧同一列表

def _style(font=None):
    return sc.SubtitleStyle(font=font, font_size=28)


def test_filter_consumes_the_same_cue_list(tmp_path):
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS)
    srt = sc.write_srt(plan, tmp_path / "sub.srt")
    filt = sc.ffmpeg_subtitle_filter(srt, plan, _style())
    assert filt.startswith("subtitles='")
    assert "force_style='" in filt
    # 滤镜引用的是**同一个**文件；文件内容与 cue 列表逐字一致
    assert Path(srt).read_text(encoding="utf-8-sig") == sc.render_srt(plan.cues)


def test_filter_rejects_a_file_that_is_not_from_these_cues(tmp_path):
    """判别性：盘上 SRT 与 cue 列表不一致 ⇒ 拒收（禁止两侧各自重算）。"""
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS)
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n别人写的字幕\n\n", encoding="utf-8-sig")

    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.ffmpeg_subtitle_filter(srt, plan, _style())
    assert ei.value.code == "srt_cues_mismatch"


def test_generate_srt_keeps_the_old_signature(tmp_path):
    from step04_merge.srt_gen import generate_srt

    out = tmp_path / "legacy.srt"
    generate_srt(_SHOTS, _DURATIONS, out, _WINDOWS)          # 旧位置参数
    text = out.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 --> 00:00:05,000" in text
    assert "00:00:04,500 --> 00:00:09,500" in text


# ------------------------------------------------------------------ 对齐候选

def _aligned_entry(shot_index, text, char_ranges):
    return {"shot_index": shot_index,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "segments": [{"segment_id": f"seg{i}", "char_start": a, "char_end": b,
                          "start_sec": s, "end_sec": e}
                         for i, (a, b, s, e) in enumerate(char_ranges)]}


def test_alignment_uses_window_start_plus_local_time():
    text = _SHOTS[0]["subtitle"]
    timings = {"schema_version": sc.ALIGNMENT_SCHEMA, "script_revision": "rev1",
               "shots": [_aligned_entry(1, text, [(0, len(text), 0.5, 1.5)])]}
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                                  validated_timings=timings, script_revision="rev1")
    aligned = [c for c in plan.cues if c.source == "alignment"]
    assert len(aligned) == 1
    assert aligned[0].start_sec == pytest.approx(0.5)       # window.start(0.0) + 0.5
    assert aligned[0].end_sec == pytest.approx(1.5)
    assert plan.cues[0].text == text
    # 没有对齐结果的镜头整镜沿用旧口径（不是半句新半句旧）
    legacy = [c for c in plan.cues if c.shot_index == 2]
    assert legacy and all(c.source == "legacy" for c in legacy)
    assert legacy[0].start_sec == pytest.approx(4.5)
    assert [f["reason"] for f in plan.fallbacks if f["shot_index"] == 2] == ["no_alignment"]
    assert plan.source == "alignment+fallback"


def test_alignment_partial_coverage_falls_back_whole_shot():
    text = _SHOTS[1]["subtitle"]
    cut = text.index("，") + 1
    timings = {"schema_version": sc.ALIGNMENT_SCHEMA, "script_revision": "rev1",
               "shots": [_aligned_entry(2, text, [(0, cut, 0.1, 0.6)])]}
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                                  speech_durations=[2.0, 3.0],
                                  validated_timings=timings, script_revision="rev1")
    shot2 = [c for c in plan.cues if c.shot_index == 2]
    assert shot2 and all(c.source == "legacy" for c in shot2)
    assert shot2[0].start_sec == pytest.approx(4.5) and shot2[0].end_sec == pytest.approx(7.5)
    reasons = [f["reason"] for f in plan.fallbacks if f["shot_index"] == 2]
    assert reasons == ["incomplete_coverage"]


def test_alignment_segment_out_of_window_does_not_fabricate_time():
    text = _SHOTS[0]["subtitle"]
    timings = {"schema_version": sc.ALIGNMENT_SCHEMA, "script_revision": "rev1",
               "shots": [_aligned_entry(1, text, [(0, len(text), 0.5, 9.0)])]}
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                                  validated_timings=timings, script_revision="rev1")
    assert [f["reason"] for f in plan.fallbacks if f["shot_index"] == 1] == \
        ["segment_out_of_window"]
    assert all(c.source == "legacy" for c in plan.cues)
    assert plan.cues[0].end_sec == pytest.approx(5.0)   # 旧口径铺满窗口，不造 9 秒


def test_alignment_rejects_wrong_schema_and_revision():
    text = _SHOTS[0]["subtitle"]
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                               validated_timings={"schema_version": "nope"},
                               script_revision="rev1")
    assert ei.value.code == "unsupported_alignment_schema"

    payload = {"schema_version": sc.ALIGNMENT_SCHEMA, "script_revision": "rev0",
               "shots": [_aligned_entry(1, text, [(0, len(text), 0.1, 0.5)])]}
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                               validated_timings=payload, script_revision="rev1")
    assert ei.value.code == "alignment_revision_mismatch"


def test_alignment_rejects_text_and_audio_identity_mismatch():
    text = _SHOTS[0]["subtitle"]
    bad_text = dict(_aligned_entry(1, text, [(0, len(text), 0.1, 0.5)]),
                    text_sha256="0" * 64)
    payload = {"schema_version": sc.ALIGNMENT_SCHEMA, "script_revision": "rev1",
               "shots": [bad_text]}
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                               validated_timings=payload, script_revision="rev1")
    assert ei.value.code == "alignment_text_mismatch"

    entry = dict(_aligned_entry(1, text, [(0, len(text), 0.1, 0.5)]), audio_sha256="1" * 64)
    payload = {"schema_version": sc.ALIGNMENT_SCHEMA, "script_revision": "rev1",
               "shots": [entry]}
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                               validated_timings=payload, script_revision="rev1",
                               shot_audio_sha256={"1": "2" * 64})
    assert ei.value.code == "alignment_audio_mismatch"


# ------------------------------------------------------------------ 字体与字形

def test_explicit_font_is_hashed_and_named():
    path = _cjk_font_path()
    if path is None:
        pytest.skip("本机没有可用的中文字体")
    font = sc.load_font_spec(path)
    assert font.path.is_file() and len(font.sha256) == 64 and font.family
    assert sc.missing_glyphs(font, "你好世界，AI 2026!") == []
    # 摘要不符即拒收（显式路径 + 摘要是同一个事实）
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.load_font_spec(path, "0" * 64)
    assert ei.value.code == "font_digest_mismatch"


def test_missing_font_file_is_rejected(tmp_path):
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.load_font_spec(tmp_path / "nope.ttf")
    assert ei.value.code == "font_missing"


def test_missing_glyphs_rejected_before_encoding():
    latin = _latin_font_path()
    if latin is None:
        pytest.skip("本机没有仅有拉丁字形的字体")
    font = sc.load_font_spec(latin)
    missing = sc.missing_glyphs(font, "中文台词")
    if not missing:
        pytest.skip("该字体覆盖了中文字形，测不了缺字形分支")
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.require_glyph_coverage(font, ["中文台词"])
    assert ei.value.code == "missing_glyphs"
    assert set(ei.value.fields["glyphs"]) <= set("中文台词")


def test_style_falls_back_to_family_name_only_when_no_font_given(tmp_path):
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS)
    srt = sc.write_srt(plan, tmp_path / "sub.srt")
    filt = sc.ffmpeg_subtitle_filter(srt, plan, sc.SubtitleStyle(font=None, font_size=20))
    assert "fontsdir=" not in filt
    assert "FontName=Microsoft YaHei" in filt

    path = _cjk_font_path()
    if path is None:
        pytest.skip("本机没有可用的中文字体")
    font = sc.load_font_spec(path)
    filt = sc.ffmpeg_subtitle_filter(srt, plan, sc.SubtitleStyle(font=font, font_size=20))
    assert "fontsdir=" in filt and f"FontName={font.family}" in filt


def test_accepts_c_side_validated_timings_file_shape():
    """C 的 `tools/align_subtitles.py` 实际产物形状：单镜、镜内局部秒、timings[]."""
    text = _SHOTS[1]["subtitle"]
    payload = {
        "schema_version": "subtitle_validated_timings.v1",
        "shot_index": 2, "script_revision": "rev1", "unit_id": "u2",
        "source_audio": {"path": "x.mp3", "sha256": "a" * 64},
        "time_base": "shot_local_seconds",
        "timings": [{"segment_id": "seg1", "char_start": 0, "char_end": len(text),
                     "start_sec": 0.2, "end_sec": 0.9}],
        "fallback_segments": [],
    }
    plan = sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                                  validated_timings=payload, script_revision="rev1")
    aligned = [c for c in plan.cues if c.source == "alignment"]
    assert len(aligned) == 1
    # 平移：window.start(4.5) + 局部 0.2 → 4.7（Step04 侧负责平移，C 只给局部时间）
    assert aligned[0].start_sec == pytest.approx(4.7)
    assert aligned[0].end_sec == pytest.approx(5.4)
    assert aligned[0].text == text


def test_rejects_a_non_shot_local_time_base():
    payload = {"schema_version": "subtitle_validated_timings.v1", "shot_index": 1,
               "script_revision": "rev1", "time_base": "absolute_seconds",
               "timings": []}
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                               validated_timings=payload, script_revision="rev1")
    assert ei.value.code == "unsupported_alignment_time_base"


def test_alignment_audio_identity_uses_c_source_audio_sha():
    text = _SHOTS[0]["subtitle"]
    payload = {"schema_version": "subtitle_validated_timings.v1", "shot_index": 1,
               "script_revision": "rev1", "time_base": "shot_local_seconds",
               "source_audio": {"path": "x.mp3", "sha256": "9" * 64},
               "timings": [{"segment_id": "s", "char_start": 0, "char_end": len(text),
                            "start_sec": 0.1, "end_sec": 0.5}]}
    with pytest.raises(sc.SubtitleContractError) as ei:
        sc.build_subtitle_plan(_SHOTS, _DURATIONS, clip_windows=_WINDOWS,
                               validated_timings=payload, script_revision="rev1",
                               shot_audio_sha256={"1": "8" * 64})
    assert ei.value.code == "alignment_audio_mismatch"


# ---------------------------------------------------------------------------
# 时间轴推进（2026-10 复核 3.2）：多段字幕分支必须推进到**本镜真实结束**
# ---------------------------------------------------------------------------
def _multi_part_text() -> str:
    """构造必然切成 ≥2 段的对白正文（带全角冒号 = `has_dialogue` 判据，且远超 30 字）。"""
    text = "张十三：" + "山河破碎风飘絮，身世浮沉雨打萍。" * 3
    assert sc._is_dialogue_cue(text), "夹具前置条件：必须被判为对白 cue"
    assert len(sc.split_subtitle_by_punctuation(text, sc.MAX_CHARS_PER_LINE)) >= 2
    return text


def test_legacy_multi_part_advances_to_the_shot_true_end():
    """多段分支的时间轴推进 = 本镜真实结束，而不是最后一段字幕的结束.

    修复前该分支 `return cues, part_end`（`part_end` 只累加
    `subtitle_duration = min(dur, speech_duration)`），而单段/无字幕分支都返回 `end`。
    一旦说话短于镜头，后续每个镜头的 cue 起点都被整体前移（**累积漂移**）。
    判别性：第 2 镜首 cue 起点必须是 10.0（第 1 镜真实结束），修复前是 2.0。
    """
    text = _multi_part_text()
    shots = [{"index": 1, "subtitle": text, "narration_segment": text},
             {"index": 2, "subtitle": "听香：第二镜台词。", "narration_segment": "听香：第二镜台词。"}]
    cues = sc.cues_from_shots(shots, [10.0, 4.0], speech_durations=[2.0, None])

    shot2 = [c for c in cues if c.shot_index == 2]
    assert shot2, "第 2 镜应当有 cue"
    assert shot2[0].start_sec == pytest.approx(10.0), (
        "多段分支必须推进到本镜真实结束；停在最后一段字幕结束会让后续镜头整体前移")

    # 非目标行为：cue 自身时窗仍按说话时长，不得顺手拉满整镜
    shot1 = [c for c in cues if c.shot_index == 1]
    assert len(shot1) >= 2, "夹具前置条件：第 1 镜确实走了多段分支"
    assert shot1[-1].end_sec == pytest.approx(2.0), "cue 时窗仍按 speech_duration"


def test_single_part_and_no_cue_branches_still_advance_to_the_shot_end():
    """对照：单段与无字幕分支本来就推进到本镜结束（本次修复不得改变它们）."""
    shots = [{"index": 1, "subtitle": "张十三：短台词。", "narration_segment": "张十三：短台词。"},
             {"index": 2, "subtitle": "", "narration_segment": ""},
             {"index": 3, "subtitle": "听香：第三句。", "narration_segment": "听香：第三句。"}]
    cues = sc.cues_from_shots(shots, [7.0, 5.0, 3.0], speech_durations=[1.0, None, None])

    s3 = [c for c in cues if c.shot_index == 3]
    assert s3 and s3[0].start_sec == pytest.approx(12.0), (
        "第 2 镜无字幕也必须占到 5s（7.0 + 5.0），否则第 3 镜会前移")
    s1 = [c for c in cues if c.shot_index == 1]
    assert s1 and s1[0].start_sec == pytest.approx(0.0)
    assert s1[0].end_sec == pytest.approx(1.0), "单段 cue 时窗按 speech_duration"
