# -*- coding: utf-8 -*-
"""C 包验收：已知文本字幕句界候选（计划 §C1/§C2/§C3/§15）.

判别性要求：每类失败断言**具体 code 与作用范围**（整请求拒收 / 单镜 fallback），
合法对照（identity 映射、前缀映射、长停顿包络、完整覆盖）同时验证；
音频用本机 ffmpeg 现造的真实 WAV，富身份用 audio_identity.v2 侧车夹具。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixture_media import (  # noqa: E402
    build_speech_wav,
    make_request_dict,
    sha256_file,
    write_audio_identity,
    write_tts_manifest,
    write_json,
)

from shared import media_inspect as mi  # noqa: E402
from shared import subtitle_alignment as sa  # noqa: E402
from shared.enhancement_io import EXIT_INVALID_INPUT, EXIT_OK, EXIT_REJECTED, EnhancementError, verify_accepted  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="C 包夹具需要本机 ffmpeg（不下载工具）")

ALIGN_CLI = _HERE.parent.parent / "tools" / "align_subtitles.py"
SPOKEN = "李明说：我们三点出发，带二十公斤行李。"
FIXTURE_SCOPE = {"novel": "fixture-novel", "book_index": 1, "chapter": 1}
COMMA = SPOKEN.index("，") + 1


@pytest.fixture(scope="module")
def audio_template(tmp_path_factory):
    return build_speech_wav(tmp_path_factory.mktemp("c_audio"), seconds=3.0)


def char_units(text, *, start=0.2, step=0.09, dur=0.08, skip=()):
    units = []
    for i, ch in enumerate(text):
        if ch.isspace() or i in skip:
            continue
        units.append({"char_start": i, "char_end": i + 1,
                      "start_sec": round(start + i * step, 3),
                      "end_sec": round(start + i * step + dur, 3)})
    return units


def segments_of(text, split_at=None):
    split = COMMA if split_at is None else split_at
    return [{"segment_id": "s1", "char_start": 0, "char_end": split},
            {"segment_id": "s2", "char_start": split, "char_end": len(text)}]


class Case:
    """一个完整的合法夹具：真实 WAV + v2 侧车 + 候选 manifest + 请求 + 候选对齐."""

    def __init__(self, tmp_path, name="case", *, spoken=SPOKEN, subtitle=None, mapping=None,
                 segments=None, units=None, unit_id="u-001", script_revision="rev-1",
                 audio_template=None, sidecar_text=None, sidecar_schema="audio_identity.v2",
                 write_sidecar=True, manifest_text=None, voice_signature="fixture-voice|abcd"):
        self.dir = Path(tmp_path) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.spoken = spoken
        self.subtitle = spoken if subtitle is None else subtitle
        self.audio = self.dir / "shot3_u-001.wav"
        shutil.copy(audio_template, self.audio)
        self.unit_id = unit_id
        self.script_revision = script_revision

        if write_sidecar:
            self.identity = write_audio_identity(
                self.audio, unit_id=unit_id, schema=sidecar_schema,
                text=(spoken if sidecar_text is None else sidecar_text),
                script_revision=script_revision, scope=FIXTURE_SCOPE)
        else:
            self.identity = None
        manifest_identity = self.identity or {}
        if manifest_text is not None:
            manifest_identity = dict(manifest_identity, text=manifest_text,
                                     text_hash=sa.sha256_text(manifest_text))
        self.manifest = write_tts_manifest(self.dir / "tts_units.json", unit_id=unit_id,
                                           audio_path=self.audio, identity=manifest_identity)
        self.request = make_request_dict(
            audio_path=self.audio, unit_id=unit_id, spoken_text=spoken,
            subtitle_text=self.subtitle,
            mapping=([] if mapping is None else mapping),
            segments=(segments_of(self.subtitle) if segments is None else segments),
            script_revision=script_revision, audio_manifest_path=self.manifest,
            voice_signature=voice_signature, scope=FIXTURE_SCOPE)
        structural = sa.validate_request_structure(self.request)
        if structural:
            raise EnhancementError("request_invalid", "request",
                                   f"请求结构不合法（{len(structural)} 项）",
                                   exit_code=EXIT_REJECTED, field_path="request",
                                   residual_issues=structural)
        self.units = char_units(spoken) if units is None else units
        self.alignment, self.coverage = sa.build_alignment(
            self.request, self.units, backend="external", backend_version="fixture-1",
            model_revision=None, diagnostics={"fixture": True})
        self.duration = float(mi.probe_media(self.audio)["duration_sec"])

    def write(self) -> tuple:
        req = write_json(self.dir / "request.json", self.request)
        aln = write_json(self.dir / "alignment.json", self.alignment)
        return req, aln

    def problems(self, alignment=None):
        return sa.validate_alignment(self.request, alignment or self.alignment,
                                     audio_duration_sec=self.duration)


# ---------------------------------------------------------------- 合法对照

def test_identity_mapping_full_coverage_is_adopted(tmp_path, audio_template):
    case = Case(tmp_path, "ok", audio_template=audio_template)
    assert case.problems() == []
    assert [s["segment_id"] for s in case.coverage["adoptable_segments"]] == ["s1", "s2"]
    assert case.coverage["uncovered_ranges"] == []
    assert case.coverage["units"][0]["char_start"] == 0          # 索引按发声正文码点
    assert case.coverage["coordinates"]["units"] == sa.COORD_SPOKEN
    timings = sa.to_validated_timings(case.coverage)
    assert [t["segment_id"] for t in timings] == ["s1", "s2"]
    assert timings[0]["start_sec"] == pytest.approx(0.2, abs=0.02)
    assert timings[1]["end_sec"] <= case.duration
    shifted = sa.shift_timings(timings, 10.0)
    assert shifted[0]["start_sec"] == pytest.approx(timings[0]["start_sec"] + 10.0)
    assert shifted[1]["end_sec"] == pytest.approx(timings[1]["end_sec"] + 10.0)


def test_identity_mapping_may_omit_mapping_blocks(tmp_path, audio_template):
    case = Case(tmp_path, "identity", audio_template=audio_template, mapping=[])
    assert case.problems() == []
    assert sa.mapping_blocks(case.request) == [{"spoken_start": 0, "spoken_end": len(SPOKEN),
                                                "subtitle_start": 0, "subtitle_end": len(SPOKEN)}]


def test_prefix_mapping_offsets_are_adopted(tmp_path, audio_template):
    spoken = "我们三点出发"
    subtitle = "『我们三点出发』"
    mapping = [{"spoken_start": 0, "spoken_end": len(spoken),
                "subtitle_start": 1, "subtitle_end": 1 + len(spoken)}]
    case = Case(tmp_path, "prefix", audio_template=audio_template, spoken=spoken,
                subtitle=subtitle, mapping=mapping,
                segments=[{"segment_id": "s1", "char_start": 1, "char_end": 1 + len(spoken)}])
    assert case.problems() == []
    assert [s["segment_id"] for s in case.coverage["adoptable_segments"]] == ["s1"]
    assert case.coverage["segments"][0]["spoken_ranges"] == [[0, len(spoken)]]
    timings = sa.to_validated_timings(case.coverage)
    assert timings[0]["char_start"] == 1 and timings[0]["char_end"] == 1 + len(spoken)


def test_long_pause_is_covered_by_envelope_not_invented(tmp_path, audio_template):
    spoken = "好……走。"
    units = [{"char_start": 0, "char_end": 1, "start_sec": 0.2, "end_sec": 0.4},
             {"char_start": 1, "char_end": 3, "start_sec": 0.4, "end_sec": 1.6},   # 长停顿
             {"char_start": 3, "char_end": 4, "start_sec": 1.6, "end_sec": 1.8},
             {"char_start": 4, "char_end": 5, "start_sec": 1.8, "end_sec": 2.0}]
    case = Case(tmp_path, "pause", audio_template=audio_template, spoken=spoken, units=units,
                segments=[{"segment_id": "s1", "char_start": 0, "char_end": 3},
                          {"segment_id": "s2", "char_start": 3, "char_end": 5}])
    assert case.problems() == []
    s1, s2 = case.coverage["segments"]
    assert (s1["start_sec"], s1["end_sec"]) == (0.2, 1.6)     # 首尾包络，含停顿
    assert (s2["start_sec"], s2["end_sec"]) == (1.6, 2.0)
    assert case.coverage["uncovered_ranges"] == []            # 标点也必须有映射


# ---------------------------------------------------------------- 映射缺失/非法

def test_missing_mapping_for_different_text_is_rejected(tmp_path, audio_template):
    with pytest.raises(EnhancementError) as ei:
        Case(tmp_path, "nomap", audio_template=audio_template, spoken="我们三点出发",
             subtitle="『我们三点出发』", mapping=[])
    assert ei.value.code == "request_invalid"
    assert "mapping_missing" in {p["code"] for p in ei.value.fields["residual_issues"]}
    with pytest.raises(EnhancementError) as ei:
        sa.mapping_blocks({"spoken_text": "abc", "subtitle_text": "xabc", "mapping": []})
    assert ei.value.code == "mapping_missing"


def test_non_monotonic_mapping_is_rejected(tmp_path, audio_template):
    mapping = [{"spoken_start": 2, "spoken_end": 4, "subtitle_start": 2, "subtitle_end": 4},
               {"spoken_start": 0, "spoken_end": 2, "subtitle_start": 0, "subtitle_end": 2}]
    with pytest.raises(EnhancementError) as ei:
        Case(tmp_path, "order", audio_template=audio_template, mapping=mapping)
    codes = {p["code"] for p in ei.value.fields["residual_issues"]}
    assert "mapping_not_monotonic" in codes


def test_unequal_block_cut_by_segment_boundary_falls_back(tmp_path, audio_template):
    spoken = "你好世界"
    subtitle = "嗨你好世界~"
    mapping = [{"spoken_start": 0, "spoken_end": 2, "subtitle_start": 1, "subtitle_end": 2},
               {"spoken_start": 2, "spoken_end": 4, "subtitle_start": 2, "subtitle_end": 5}]
    case = Case(tmp_path, "unequal", audio_template=audio_template, spoken=spoken,
                subtitle=subtitle, mapping=mapping,
                segments=[{"segment_id": "s1", "char_start": 0, "char_end": 3},
                          {"segment_id": "s2", "char_start": 3, "char_end": 6}],
                units=char_units(spoken))
    s1, s2 = case.coverage["segments"]
    assert s1["adoptable"] is False
    assert s1["reason"] == "segment_boundary_unmappable"
    assert s1["problems"][0]["code"] == "segment_boundary_unmappable"
    assert s2["adoptable"] is False and s2["reason"] == "segment_boundary_unmappable"


# ---------------------------------------------------------------- 覆盖与时间

def test_partial_coverage_falls_back_per_segment(tmp_path, audio_template):
    skip = set(range(COMMA, len(SPOKEN)))                 # 第二句完全没有 unit
    case = Case(tmp_path, "partial", audio_template=audio_template,
                units=char_units(SPOKEN, skip=skip))
    assert case.problems() == []
    assert [s["segment_id"] for s in case.coverage["adoptable_segments"]] == ["s1"]
    fallback = case.coverage["fallback_segments"]
    assert [s["segment_id"] for s in fallback] == ["s2"]
    assert fallback[0]["reason"] == "char_uncovered"
    uncovered = case.coverage["uncovered_ranges"]
    assert uncovered and all(u["reason"] == "char_uncovered" for u in uncovered)
    assert uncovered[0]["char_start"] >= COMMA
    assert any(u["char_end"] == len(SPOKEN) for u in uncovered)
    # 只有可采纳的 segment 拿到时间；fallback 不给任何时间（不拼半新半旧）
    assert [t["segment_id"] for t in sa.to_validated_timings(case.coverage)] == ["s1"]


def test_all_blank_segment_has_no_time_evidence(tmp_path, audio_template):
    spoken = "走。"
    case = Case(tmp_path, "blank_seg", audio_template=audio_template, spoken=spoken,
                segments=[{"segment_id": "s1", "char_start": 0, "char_end": 2}],
                units=[{"char_start": 0, "char_end": 1, "start_sec": 0.2, "end_sec": 0.4}])
    seg = case.coverage["segments"][0]
    assert seg["adoptable"] is False and seg["reason"] == "char_uncovered"
    assert sa.to_validated_timings(case.coverage) == []


def test_unit_without_time_is_rejected(tmp_path, audio_template):
    case = Case(tmp_path, "notime", audio_template=audio_template)
    units = list(case.alignment["units"])
    units[3] = {**units[3], "start_sec": None}
    bad = {**case.alignment, "units": units}
    assert "unit_time_null" in {p["code"] for p in case.problems(bad)}

    # 生产侧构造入口也不许抛裸 TypeError：畸形 unit ⇒ 结构化错误
    with pytest.raises(EnhancementError) as ei:
        sa.build_alignment(case.request, units, backend="external", backend_version="1",
                           model_revision=None)
    assert ei.value.code == "alignment_build_invalid"
    assert "unit_field_invalid" in {p["code"] for p in ei.value.fields["residual_issues"]}


def test_out_of_bounds_and_overlapping_units_are_rejected(tmp_path, audio_template):
    case = Case(tmp_path, "bounds", audio_template=audio_template)

    bad = {**case.alignment, "units": [{**case.alignment["units"][0], "start_sec": -0.4}]}
    assert "unit_time_range" in {p["code"] for p in case.problems(bad)}

    bad = {**case.alignment,
           "units": [{**case.alignment["units"][-1], "end_sec": case.duration + 1.5}]}
    assert "unit_time_oob" in {p["code"] for p in case.problems(bad)}

    units = case.alignment["units"]
    bad = {**case.alignment, "units": [units[0], {**units[1], "start_sec": units[0]["start_sec"]}]}
    assert "unit_time_overlap" in {p["code"] for p in case.problems(bad)}


def test_overlapping_or_out_of_range_chars_are_rejected(tmp_path, audio_template):
    case = Case(tmp_path, "chars", audio_template=audio_template)
    units = case.alignment["units"]

    bad = {**case.alignment, "units": [units[0], {**units[1], "char_start": 0}]}
    assert "unit_char_overlap" in {p["code"] for p in case.problems(bad)}

    bad = {**case.alignment, "units": [units[0], {**units[1], "char_end": len(SPOKEN) + 3}]}
    assert "unit_char_oob" in {p["code"] for p in case.problems(bad)}

    bad = {**case.alignment, "units": [{**units[0], "char_end": 0}]}
    assert "unit_char_range" in {p["code"] for p in case.problems(bad)}


def test_alignment_identity_fields_are_required(tmp_path, audio_template):
    case = Case(tmp_path, "ident", audio_template=audio_template)

    bad = {**case.alignment, "request_sha256": "0" * 64}
    assert "request_sha256_mismatch" in {p["code"] for p in case.problems(bad)}

    bad = {k: v for k, v in case.alignment.items() if k != "model_revision"}
    assert "model_revision_missing" in {p["code"] for p in case.problems(bad)}

    bad = {**case.alignment, "backend_version": ""}
    assert "backend_version_missing" in {p["code"] for p in case.problems(bad)}

    bad = {**case.alignment, "units": "not-a-list"}
    assert "units_missing" in {p["code"] for p in case.problems(bad)}


def test_audio_duration_unknown_refuses_to_adopt(tmp_path, audio_template):
    case = Case(tmp_path, "nodur", audio_template=audio_template)
    problems = sa.validate_alignment(case.request, case.alignment, audio_duration_sec=None)
    assert [p["code"] for p in problems] == ["audio_bounds_unverified"]


# ---------------------------------------------------------------- 富身份

def test_rich_identity_verified_against_manifest_and_sidecar(tmp_path, audio_template):
    case = Case(tmp_path, "rich", audio_template=audio_template)
    evidence = sa.identity_gate(case.request, audio_candidates_path=case.manifest)
    assert evidence["rich_identity"] == "v2"
    assert evidence["audio_sha256"] == sha256_file(case.audio)
    assert Path(evidence["candidates"]["path"]) == case.manifest
    assert evidence["unit"]["sha256"] == sha256_file(case.audio)


def test_wrong_audio_hash_is_rejected(tmp_path, audio_template):
    case = Case(tmp_path, "badhash", audio_template=audio_template)
    case.request["audio_sha256"] = "b" * 64
    result = sa.verify_audio_identity(case.request)
    assert {p["code"] for p in result["problems"]} == {"audio_sha256_mismatch"}


def test_mismatched_text_in_sidecar_or_manifest_is_rejected(tmp_path, audio_template):
    sidecar_bad = Case(tmp_path, "badside", audio_template=audio_template,
                       sidecar_text="另一段完全不同的台词")
    codes = {p["code"] for p in sa.verify_audio_identity(sidecar_bad.request)["problems"]}
    assert "identity_text_mismatch" in codes

    manifest_bad = Case(tmp_path, "badmani", audio_template=audio_template,
                        manifest_text="另一段台词")
    codes = {p["code"] for p in sa.verify_audio_identity(manifest_bad.request)["problems"]}
    assert "unit_text_mismatch" in codes


def test_manifest_hash_and_path_mismatch_are_rejected(tmp_path, audio_template):
    case = Case(tmp_path, "badmani2", audio_template=audio_template)
    tampered = dict(case.request, audio_manifest_hash="c" * 64)
    codes = {p["code"] for p in sa.verify_audio_identity(tampered)["problems"]}
    assert "audio_manifest_hash_mismatch" in codes

    other = write_tts_manifest(tmp_path / "other_manifest.json", unit_id="u-001",
                               audio_path=case.audio, identity={})
    codes = {p["code"] for p in sa.verify_audio_identity(
        case.request, audio_candidates_path=other)["problems"]}
    assert "audio_manifest_path_mismatch" in codes


def test_unit_not_in_manifest_is_rejected(tmp_path, audio_template):
    case = Case(tmp_path, "nounit", audio_template=audio_template)
    codes = {p["code"] for p in sa.verify_audio_identity(
        dict(case.request, unit_id="u-999"))["problems"]}
    assert "unit_not_in_manifest" in codes


def test_manifest_expected_voice_relation_mismatch_is_rejected(tmp_path, audio_template):
    """manifest 期望身份与侧车记录的**声音关系**不符：只有 T 包 verify_identity 能发现."""
    case = Case(tmp_path, "voice_rel", audio_template=audio_template)
    entry = json.loads(case.manifest.read_text(encoding="utf-8"))
    entry["units"][0]["identity"] = dict(entry["units"][0]["identity"],
                                          voice_profile_id="vp-other")
    write_json(case.manifest, entry)
    case.request["audio_manifest_hash"] = sha256_file(case.manifest)
    codes = {p["code"] for p in sa.verify_audio_identity(case.request)["problems"]}
    assert codes == {"identity_relation_mismatch"}


def test_identity_falls_back_to_local_reader_without_audio_identity_module(
        tmp_path, audio_template, monkeypatch):
    """T 模块不可用时本模块仍能独立核身份（同一份侧车，不静默放行）."""
    case = Case(tmp_path, "fb", audio_template=audio_template)
    monkeypatch.setattr(sa, "_audio_identity_api", lambda: None)
    evidence = sa.identity_gate(case.request, audio_candidates_path=case.manifest)
    assert evidence["rich_identity"] == "v2" and evidence["identity_source"] == "local"

    case.request["spoken_text"] = "完全不同的台词"
    codes = {p["code"] for p in sa.verify_audio_identity(case.request)["problems"]}
    assert "identity_text_mismatch" in codes


def test_missing_or_legacy_sidecar_is_identity_incomplete(tmp_path, audio_template):
    missing = Case(tmp_path, "noside", audio_template=audio_template, write_sidecar=False)
    codes = {p["code"] for p in sa.verify_audio_identity(missing.request)["problems"]}
    assert "identity_incomplete" in codes

    legacy = Case(tmp_path, "v1side", audio_template=audio_template,
                  sidecar_schema="audio_identity.v1")
    codes = {p["code"] for p in sa.verify_audio_identity(legacy.request)["problems"]}
    assert "identity_incomplete" in codes


def test_voice_signature_alone_is_not_rich_identity(tmp_path, audio_template):
    case = Case(tmp_path, "sig_only", audio_template=audio_template, write_sidecar=False)
    # 请求自带 voice_signature 且与 .voice 边车一致，也**不能**替代富身份
    case.audio.with_suffix(".voice").write_text(case.request["voice_signature"],
                                                encoding="utf-8")
    codes = {p["code"] for p in sa.verify_audio_identity(case.request)["problems"]}
    assert "identity_incomplete" in codes and "voice_signature_mismatch" not in codes


def test_legacy_voice_file_mismatch_is_reported(tmp_path, audio_template):
    case = Case(tmp_path, "sig_bad", audio_template=audio_template)
    case.audio.with_suffix(".voice").write_text("another-voice|deadbeef", encoding="utf-8")
    codes = {p["code"] for p in sa.verify_audio_identity(case.request)["problems"]}
    assert codes == {"voice_signature_mismatch"}


def test_native_and_non_tts_policies_are_unsupported(tmp_path, audio_template):
    case = Case(tmp_path, "native", audio_template=audio_template)
    codes = {p["code"] for p in sa.verify_audio_identity(
        dict(case.request, source_policy="native"))["problems"]}
    assert codes == {"unsupported_source_policy"}


def test_blank_spoken_text_is_rejected(tmp_path, audio_template):
    """无声（噪声/空台词）不是"覆盖 0 个字符所以通过"：没有发声字符就没有对齐对象."""
    case = Case(tmp_path, "blank", audio_template=audio_template, spoken="走",
                segments=[{"segment_id": "s1", "char_start": 0, "char_end": 1}])
    request = dict(case.request, spoken_text="   ", subtitle_text="   ",
                   segments=[{"segment_id": "s1", "char_start": 0, "char_end": 3}])
    problems = sa.validate_request_structure(request)
    assert "no_speech_text" in {p["code"] for p in problems}
    with pytest.raises(EnhancementError) as ei:
        sa.load_request(write_json(case.dir / "blank_request.json", request))
    assert ei.value.code == "request_invalid"


# ---------------------------------------------------------------- CLI 贯通

def _cli(*args, cwd=None):
    proc = subprocess.run([sys.executable, str(ALIGN_CLI), *[str(a) for a in args]],
                          shell=False, capture_output=True, timeout=300, cwd=cwd,
                          encoding="utf-8", errors="replace")
    return proc


def test_cli_adopts_alignment_and_writes_timings(tmp_path, audio_template):
    case = Case(tmp_path, "cli_ok", audio_template=audio_template)
    req, aln = case.write()
    out = (tmp_path / "cli_ok" / "out").resolve()
    proc = _cli("--input", req, "--audio-candidates", case.manifest, "--alignment", aln,
                "--out-dir", out)
    assert proc.returncode == EXIT_OK, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["adoptable_segments"] == ["s1", "s2"] and payload["fallback_segments"] == []
    assert payload["accepted_committed"] is True

    timings = json.loads(Path(payload["validated_timings_path"]).read_text(encoding="utf-8"))
    assert timings["time_base"] == "shot_local_seconds"
    assert timings["source_audio"]["sha256"] == sha256_file(case.audio)
    assert [t["segment_id"] for t in timings["timings"]] == ["s1", "s2"]
    assert timings["timings"][0]["start_sec"] < timings["timings"][1]["start_sec"]

    verified = verify_accepted(out, required_checks=("rich_identity", "alignment_valid"))
    assert verified["manifest"]["coverage"]["adoptable_segments"] == 2
    assert sha256_file(case.audio) == case.request["audio_sha256"]   # 音频只读


def test_cli_rejected_candidate_keeps_old_accepted_pointer(tmp_path, audio_template):
    case = Case(tmp_path, "cli_bad", audio_template=audio_template)
    req, aln = case.write()
    out = (tmp_path / "cli_bad" / "out").resolve()
    assert _cli("--input", req, "--audio-candidates", case.manifest, "--alignment", aln,
                "--out-dir", out).returncode == EXIT_OK
    accepted_before = json.loads((out / "accepted.json").read_text(encoding="utf-8"))

    bad = dict(case.alignment)
    bad["units"] = [{**case.alignment["units"][0], "start_sec": case.duration + 5.0,
                     "end_sec": case.duration + 5.5}]
    bad_path = write_json(case.dir / "bad_alignment.json", bad)
    proc = _cli("--input", req, "--audio-candidates", case.manifest, "--alignment", bad_path,
                "--out-dir", out)
    assert proc.returncode == EXIT_REJECTED
    payload = json.loads(proc.stdout)
    assert payload["problem_codes"] == ["unit_time_oob"]
    assert payload["accepted_committed"] is False
    assert json.loads((out / "accepted.json").read_text(encoding="utf-8")) == accepted_before
    attempt = Path(payload["attempt_dir"])
    assert (attempt / "outputs" / "alignment_problems.json").is_file()
    assert not (attempt / "outputs" / "subtitle_alignment.json").exists()
    assert not (attempt / "outputs" / "validated_timings.json").exists()


def test_cli_wrong_audio_hash_exit_4(tmp_path, audio_template):
    case = Case(tmp_path, "cli_hash", audio_template=audio_template)
    case.request["audio_sha256"] = "d" * 64
    req = write_json(case.dir / "request_bad.json", case.request)
    _, aln = case.write()
    out = (tmp_path / "cli_hash" / "out").resolve()
    proc = _cli("--input", req, "--audio-candidates", case.manifest, "--alignment", aln,
                "--out-dir", out)
    assert proc.returncode == EXIT_REJECTED
    assert json.loads(proc.stderr)["code"] == "identity_rejected"
    assert not (out / "accepted.json").exists()
    attempt = next((out / "attempts").iterdir())
    assert (attempt / "input.json").is_file()          # 拒收也留证据


def test_cli_whisperx_without_local_weights_is_unavailable(tmp_path, audio_template):
    case = Case(tmp_path, "cli_asr", audio_template=audio_template)
    req, _ = case.write()
    out = (tmp_path / "cli_asr" / "out").resolve()
    empty_model_dir = tmp_path / "cli_asr" / "model"
    empty_model_dir.mkdir(parents=True, exist_ok=True)
    proc = _cli("--input", req, "--audio-candidates", case.manifest,
                "--backend", "whisperx", "--model-dir", empty_model_dir, "--out-dir", out)
    assert proc.returncode == 3
    assert json.loads(proc.stderr)["code"] == "weights_missing"     # 禁止自动下载
    assert not (out / "accepted.json").exists()
    assert not list(out.rglob("subtitle_alignment.json"))


def test_cli_conflicting_or_missing_backend_args(tmp_path, audio_template):
    case = Case(tmp_path, "cli_args", audio_template=audio_template)
    req, aln = case.write()
    out = (tmp_path / "cli_args" / "out").resolve()
    proc = _cli("--input", req, "--audio-candidates", case.manifest, "--alignment", aln,
                "--backend", "whisperx", "--out-dir", out)
    assert proc.returncode == EXIT_INVALID_INPUT
    assert json.loads(proc.stderr)["code"] == "backend_conflict"

    proc = _cli("--input", req, "--audio-candidates", case.manifest, "--out-dir", out)
    assert proc.returncode == EXIT_INVALID_INPUT
    assert json.loads(proc.stderr)["code"] == "backend_conflict"


def test_cli_rejects_audio_candidates_not_matching_request(tmp_path, audio_template):
    case = Case(tmp_path, "cli_man", audio_template=audio_template)
    req, aln = case.write()
    other = write_tts_manifest(case.dir / "other_units.json", unit_id="u-001",
                               audio_path=case.audio, identity={})
    proc = _cli("--input", req, "--audio-candidates", other, "--alignment", aln,
                "--out-dir", (case.dir / "out").resolve())
    assert proc.returncode == EXIT_REJECTED
    assert "audio_manifest_path_mismatch" in json.loads(proc.stderr)["residual_issues"][0]["code"]
