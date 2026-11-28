# -*- coding: utf-8 -*-
"""T 包核心：`tts.synthesis_job` —— 无损分段 / 能力事实 / PCM 拼接 / 时长守卫 / 候选事务.

判别性要求：撤掉任一条关键判据（无损拼接、分段归属、artifact 摘要、失败不提交、
request_key 参与复用判定）时相应用例必须转红。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio
from shared.audio_identity import read_identity, verify_identity
from shared.paths import chapter_dir
from tts.models import (ProviderCapabilities, SpeechUnit, TTSFailureResult,
                        TTSSuccessResult)
from tts.synthesis_job import (DurationRejectedError, ProviderCallError,
                               SegmentationError, SpeechUnit as _SU, SynthesisError,
                               TtsCandidateContext, assert_lossless, merge_pcm_segments,
                               normalize_unit, probe_duration, publish_attempt,
                               request_key_for, resolve_applied, reuse_accepted,
                               segment_text, synthesize_unit, synthesize_units,
                               wav_sample_count, write_speech_unit_plan)

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")


# ---------------------------------------------------------------- 桩 provider

class StubProvider:
    """离线桩：写真实 WAV（不同文本不同频率），可按段注入失败."""

    PROVIDER_NAME = "tts_stub_test"

    def __init__(self, *, cap: int | None = 20, sample_rate: int = 24000,
                 fail_at_call: int | None = None, supports_instruction: bool = False,
                 supports_rate: bool = False, supports_volume: bool = False,
                 legal_speakers=("voice-a",), return_wav_suffix: bool = True):
        self.cap = cap
        self.sample_rate = sample_rate
        self.fail_at_call = fail_at_call
        self.calls: list = []
        self._caps = ProviderCapabilities(
            provider=self.PROVIDER_NAME, max_chars_per_call=cap,
            supports_instruction=supports_instruction, supports_rate=supports_rate,
            supports_volume=supports_volume, supports_speaker=True,
            legal_speakers=tuple(legal_speakers), output_formats=("wav",),
            sample_rates=(sample_rate,), model_id="stub-model", model_revision="r9",
            real_model=False, evidence="单元测试桩")
        self.return_wav_suffix = return_wav_suffix

    def capabilities(self):
        return self._caps

    def synthesize(self, req, output=None):
        self.calls.append({"text": req.text, "speaker": req.effective_speaker(),
                           "instruction": req.instruction, "rate": req.rate,
                           "volume": req.volume})
        if self.fail_at_call is not None and len(self.calls) == self.fail_at_call:
            return TTSFailureResult(error="桩注入的失败", code="injected_failure")
        out = Path(output)
        if self.return_wav_suffix:
            out = out.with_suffix(".wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        dur = 0.10 * len(req.text) + 0.10
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=%d:duration=%.3f:sample_rate=%d"
                        % (400 + 20 * (len(req.text) % 7), dur, self.sample_rate),
                        "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        return TTSSuccessResult(save_path=str(out), actual_format="wav",
                                actual_sample_rate=self.sample_rate,
                                applied={"instruction": bool(req.instruction),
                                         "rate": False, "volume": False})


def make_unit(text: str, *, kind: str = "dialogue", unit_id: str = "shot-1",
              window=None, instruction: str = "", rate: float = 1.0,
              volume=None, speaker: str = "voice-a", mapping_version: str = "v1",
              voice_profile_id: str = "vp-1", strict: bool = True,
              provider: str = "tts_stub_test", format: str = "wav",
              sample_rate: int = 24000) -> SpeechUnit:
    return SpeechUnit(
        unit_id=unit_id, kind=kind, text=text, scope={"chapter": 2},
        script_revision="rev-1", source_locator="shots[1]", planned_window=window,
        voice_profile_id=voice_profile_id, provider=provider, model_id="stub-model",
        model_revision="r9", speaker=speaker, instruction=instruction,
        language="zh", rate=rate, volume=volume, format=format,
        sample_rate=sample_rate, mapping_version=mapping_version,
        params_strict=strict)


def make_ctx(root: Path, *, attempt_id: str = "a-001", scope=None,
             revision: str = "rev-1") -> TtsCandidateContext:
    return TtsCandidateContext.create(str(root), attempt_id=attempt_id,
                                      experiment_id="exp-t",
                                      scope=scope or {"chapter": 2},
                                      script_revision=revision, source="fixture")


# ---------------------------------------------------------------- 分段

def test_segmentation_is_lossless_and_spans_are_exact():
    text = "第一句。第二句，第三句！" * 6
    segs = segment_text(text, 10)
    assert "".join(s.text for s in segs) == text
    assert all(s.end - s.start == len(s.text) for s in segs)
    assert [s.start for s in segs[1:]] == [s.end for s in segs[:-1]]
    assert segs[-1].end == len(text)
    assert all(len(s.text) <= 10 for s in segs)


def test_segmentation_keeps_whitespace_and_punctuation_verbatim():
    """切点只落在标点处，**不 strip**——撤掉"不 strip"这条判据即转红."""
    text = "甲  说：  hello world, 你好吗？   乙说：好。"
    segs = segment_text(text, 12)
    assert "".join(s.text for s in segs) == text
    assert any(s.text.endswith(" ") for s in segs)     # 空白被保留，而不是被吃掉


def test_segmentation_without_cap_is_a_single_segment():
    text = "短句。"
    segs = segment_text(text, None)
    assert len(segs) == 1 and segs[0].text == text


def test_assert_lossless_rejects_dropped_or_shuffled_characters():
    text = "甲乙丙丁戊己庚辛"
    segs = segment_text(text, 3)
    assert_lossless(text, segs)
    broken = list(segs)
    broken[0] = type(segs[0])(1, segs[0].text[:-1], segs[0].start, segs[0].end)
    with pytest.raises(SegmentationError):
        assert_lossless(text, broken)
    with pytest.raises(SegmentationError):
        assert_lossless(text + "壬", segs)


# ---------------------------------------------------------------- 能力事实

def test_unsupported_instruction_is_refused_in_strict_mode():
    unit = make_unit("短句。", instruction="用愤怒的语气", strict=True)
    with pytest.raises(SynthesisError) as ei:
        resolve_applied(unit, StubProvider().capabilities())
    assert ei.value.code == "unsupported_parameter"
    assert ei.value.field_path == "instruction"


def test_legacy_mode_records_capability_facts_instead_of_pretending():
    unit = make_unit("短句。", instruction="用愤怒的语气", rate=0.85, volume=55,
                     strict=False)
    applied = resolve_applied(unit, StubProvider().capabilities())
    assert applied["instruction"] == "" and applied["instruction_applied"] is False
    assert applied["rate"] is None and applied["rate_applied"] is False
    assert applied["volume"] is None and applied["volume_applied"] is False


def test_rate_and_instruction_are_honoured_when_provider_supports_them():
    caps = StubProvider(supports_instruction=True, supports_rate=True,
                        supports_volume=True).capabilities()
    applied = resolve_applied(make_unit("短句。", instruction="愤怒", rate=0.8,
                                        volume=40), caps)
    assert applied == {"instruction": "愤怒", "rate": 0.8, "volume": 40,
                       "instruction_applied": True, "rate_applied": True,
                       "volume_applied": True}


def test_neutral_rate_is_not_treated_as_an_unsupported_request():
    """rate=1.0（中性）不算"要求了不支持的参数"——否则 qwen3 每个单元都会挂."""
    applied = resolve_applied(make_unit("短句。", rate=1.0), StubProvider().capabilities())
    assert applied["rate"] is None and applied["rate_applied"] is False


def test_illegal_speaker_is_refused_not_silently_replaced():
    unit = make_unit("短句。", speaker="别的声线")
    with pytest.raises(SynthesisError) as ei:
        synthesize_unit(unit, None, provider=StubProvider(),
                        output_path=Path("unused.wav"))
    assert ei.value.code == "speaker_not_legal"


def test_missing_identity_fields_report_field_path():
    unit = make_unit("短句。")
    unit.script_revision = ""
    with pytest.raises(SynthesisError) as ei:
        normalize_unit(unit)
    assert ei.value.code == "identity_incomplete" and ei.value.field_path == "script_revision"


# ---------------------------------------------------------------- 合成与 PCM

def test_long_text_is_synthesized_in_segments_and_reassembles(tmp_path):
    text = "风吹过山岗。他说，走吧！" * 8
    unit = make_unit(text, format="wav")
    provider = StubProvider(cap=25, sample_rate=24000)
    artifact = synthesize_unit(unit, None, provider=provider,
                               output_path=tmp_path / "out.wav",
                               work_dir=tmp_path / "work")
    assert len(provider.calls) > 1
    assert all(len(c["text"]) <= 25 for c in provider.calls)
    assert "".join(c["text"] for c in provider.calls) == text     # 逐字符等于完整 text
    assert artifact.identity["segmentation"] == {"cap": 25, "n_segments": len(provider.calls),
                                                 "lossless": True}
    spans = [(s["start"], s["end"]) for s in artifact.segments]
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert artifact.total_samples == sum(s["samples"] for s in artifact.segments)
    assert artifact.identity["total_samples"] == artifact.total_samples
    assert probe_duration(artifact.path) > 0
    assert read_identity(artifact.path)["unit_id"] == unit.unit_id


def test_one_failed_segment_blocks_the_whole_unit(tmp_path):
    text = "第一段很长很长很长。第二段很长很长很长。第三段很长很长很长。"
    unit = make_unit(text)
    provider = StubProvider(cap=12, fail_at_call=2)
    out = tmp_path / "out.wav"
    with pytest.raises(ProviderCallError) as ei:
        synthesize_unit(unit, None, provider=provider, output_path=out,
                        work_dir=tmp_path / "work")
    assert ei.value.code == "injected_failure"
    assert ei.value.fields["segment_index"] == 2
    assert not out.exists()                       # 任一段失败 ⇒ 整 unit 不提交
    assert not out.with_name(out.name + ".identity.json").exists()


def test_actual_save_path_is_used_not_an_assumed_suffix(tmp_path):
    """provider 实际写 WAV（与请求后缀不同）时，结果必须读它而不是 stat 假定后缀."""
    unit = make_unit("短句。", format="mp3")
    provider = StubProvider()
    artifact = synthesize_unit(unit, None, provider=provider,
                               output_path=tmp_path / "out.mp3",
                               work_dir=tmp_path / "work")
    assert artifact.path.suffix == ".mp3"         # 消费格式仍是既定格式
    assert artifact.segments[0]["input_path"].endswith(".wav")


def test_segment_sum_mismatch_is_detected(tmp_path):
    """拼接后总采样数 ≠ 各段之和 ⇒ 拒绝（撤掉这条对账即转红）."""
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    for p, dur in ((a, 0.2), (b, 0.3)):
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=500:duration=%.2f:sample_rate=24000" % dur,
                        "-ac", "1", "-c:a", "pcm_s16le", str(p)], check=True,
                       capture_output=True)
    res = merge_pcm_segments([a, b], tmp_path / "out.wav", work_dir=tmp_path / "w",
                             sample_rate=24000, channels=1, out_format="wav")
    assert res.total_samples == sum(res.per_segment_samples)
    assert res.total_samples == wav_sample_count(res.path)


def test_duration_over_the_window_is_rejected_without_writing(tmp_path):
    unit = make_unit("这是一句很长的话。" * 3, window=(0.0, 0.5))
    out = tmp_path / "out.wav"
    with pytest.raises(DurationRejectedError) as ei:
        synthesize_unit(unit, None, provider=StubProvider(), output_path=out,
                        work_dir=tmp_path / "work")
    assert ei.value.code == "duration_rejected"
    assert ei.value.fields["available_sec"] == 0.5
    assert not out.exists() and not out.with_name(out.name + ".identity.json").exists()


def test_duration_within_the_window_passes(tmp_path):
    unit = make_unit("短。", window=(0.0, 30.0))
    art = synthesize_unit(unit, None, provider=StubProvider(),
                          output_path=tmp_path / "ok.wav", work_dir=tmp_path / "w")
    assert art.duration_sec > 0


# ---------------------------------------------------------------- 请求键

def test_request_key_follows_text_voice_and_applied_params():
    base = make_unit("同一句话。")
    caps = StubProvider().capabilities()
    key0 = request_key_for(base, applied=resolve_applied(base, caps))
    same = request_key_for(make_unit("同一句话。"), applied=resolve_applied(base, caps))
    assert key0 == same
    other_text = make_unit("另一句话。")
    assert request_key_for(other_text, applied=resolve_applied(other_text, caps)) != key0
    other_voice = make_unit("同一句话。", speaker="voice-b")
    assert request_key_for(other_voice,
                           applied=resolve_applied(other_voice, caps)) != key0
    other_map = make_unit("同一句话。", mapping_version="v2")
    assert request_key_for(other_map, applied=resolve_applied(other_map, caps)) != key0


# ---------------------------------------------------------------- 候选事务

def _publish(ctx, units, provider):
    artifacts = synthesize_units(units, ctx, provider=provider)
    return publish_attempt(ctx, artifacts)


def test_candidate_transaction_commits_manifest_and_accepted(tmp_path):
    ctx = make_ctx(tmp_path / "tts", attempt_id="a-100")
    with ctx:
        result = _publish(ctx, [make_unit("第一句。"), make_unit("第二句。", unit_id="shot-2")],
                          StubProvider(cap=50))
    assert result["accepted"]["attempt_id"] == "a-100"
    verified = eio.verify_accepted(ctx.package_root)
    units = verified["manifest"]["units"]
    assert {u["unit_id"] for u in units} == {"shot-1", "shot-2"}
    for rec in units:
        assert Path(rec["path"]).is_file() and len(rec["sha256"]) == 64
        assert rec["voice_identity"]["schema_version"] == "audio_identity.v2"
        assert verify_identity(rec["path"], rec["voice_identity"]) == ""
        assert rec["text_sha256"] == rec["voice_identity"]["text_hash"]
    assert verified["manifest"]["package"] == "tts"
    assert verified["manifest"]["scope"] == {"chapter": 2}
    assert verified["manifest"]["script_revision"] == "rev-1"
    assert verified["manifest"]["source"] == "fixture"


def test_accepted_is_not_replaced_when_a_later_run_fails(tmp_path):
    ctx = make_ctx(tmp_path / "tts", attempt_id="a-200")
    with ctx:
        _publish(ctx, [make_unit("好的句子。")], StubProvider(cap=50))
    baseline = eio.read_accepted(ctx.package_root)
    ctx2 = make_ctx(tmp_path / "tts", attempt_id="a-201")
    with ctx2:
        with pytest.raises(ProviderCallError):
            _publish(ctx2, [make_unit("坏的句子。")], StubProvider(cap=50, fail_at_call=1))
    assert eio.read_accepted(ctx.package_root) == baseline      # 旧 accepted 逐字未变
    assert eio.verify_accepted(ctx.package_root)


def test_reuse_validates_request_key_and_identity(tmp_path):
    ctx = make_ctx(tmp_path / "tts", attempt_id="a-300")
    provider = StubProvider(cap=50)
    with ctx:
        _publish(ctx, [make_unit("同一句话。")], provider)
    calls_before = len(provider.calls)
    unit = make_unit("同一句话。")
    caps = provider.capabilities()
    key = request_key_for(unit, applied=resolve_applied(unit, caps))
    reused = reuse_accepted(ctx, unit, request_key=key)
    assert reused is not None and reused.reused is True
    assert reused.path.is_file()
    # request_key 变了（文本变了）⇒ 不复用
    other = make_unit("换了一句话。")
    key2 = request_key_for(other, applied=resolve_applied(other, caps))
    assert reuse_accepted(ctx, other, request_key=key2) is None
    # 身份侧车与盘上媒体不符 ⇒ 不复用
    rec = eio.read_manifest(ctx.attempt)["units"][0]
    Path(rec["path"]).write_bytes(b"tampered")
    assert reuse_accepted(ctx, unit, request_key=key) is None
    assert len(provider.calls) == calls_before


def test_second_run_reuses_without_extra_provider_calls(tmp_path):
    root = tmp_path / "tts"
    units = [make_unit("稳定的一句话。")]
    provider = StubProvider(cap=50)
    ctx = make_ctx(root, attempt_id="a-400")
    with ctx:
        _publish(ctx, units, provider)
    calls = len(provider.calls)
    ctx2 = make_ctx(root, attempt_id="a-401")
    with ctx2:
        artifacts = synthesize_units(units, ctx2, provider=provider)
    assert len(provider.calls) == calls              # 有效缓存 ⇒ 不重合成
    assert artifacts[0].reused is True


def test_manifest_is_consumable_by_candidate_inputs(tmp_path):
    """独立消费者（`shared.candidate_inputs`）能按 unit_id 读到媒体 + 富身份."""
    from shared.candidate_inputs import resolve_candidate_inputs
    # 视频侧走**生产者真实出口**（seal_manifest + commit_accepted）：消费侧要求候选
    # 被 accepted 指针指名，手搓一份 manifest 测的是生产上不存在的输入形态。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _candidate_fixtures import seal_accepted_candidate

    v_manifest, _v_sha = seal_accepted_candidate(
        tmp_path, "v-1", package="video", scope={"chapter": 2}, revision="rev-1",
        source="accepted_script",
        units=[{"unit_id": "shot-1", "shot_index": 1, "_bytes": b"v" * 2048,
                "window": {"start_sec": 0.0, "end_sec": 3.0}}])

    ctx = make_ctx(tmp_path / "tts", attempt_id="a-500")
    with ctx:
        _publish(ctx, [make_unit("能对上的一句话。")], StubProvider(cap=50))
    a_manifest = ctx.attempt / "manifest.json"

    resolved = resolve_candidate_inputs(str(v_manifest), str(a_manifest),
                                        experiment_root=tmp_path,
                                        expect_scope={"chapter": 2},
                                        expect_script_revision="rev-1")
    unit = resolved["units"]["shot-1"]
    clip = Path(json.loads(Path(v_manifest).read_text(encoding="utf-8"))["units"][0]["path"])
    assert unit["video_path"] == str(clip.resolve())
    assert Path(unit["audio_path"]).is_file()
    assert unit["voice_identity"]["schema_version"] == "audio_identity.v2"
    assert unit["text_sha256"] == unit["voice_identity"]["text_hash"]
    assert unit["kind"] == "dialogue"


def test_manifest_units_expose_the_rich_identity_for_c_consumers(tmp_path):
    """C 包消费入口（load_audio_candidates → expectation_for → verify_identity）可用.

    2026-10-03 复核（B+C 线）：C 按 `path`/`artifact_path` + `identity` 子对象读，
    并复核富身份字段；这里断言那两个别名确实在，且合并后的期望身份**逐字段可用**。
    """
    from shared.audio_identity import expectation_for, load_audio_candidates
    ctx = make_ctx(tmp_path / "tts", attempt_id="a-700")
    with ctx:
        _publish(ctx, [make_unit("给对齐用的一句话。")], StubProvider(cap=50))
    manifest_path = ctx.attempt / "manifest.json"
    candidates = load_audio_candidates(manifest_path)
    expected = expectation_for("shot-1", candidates)
    for key in ("artifact_path", "path", "sha256", "text", "text_hash", "request_key",
                "provider", "speaker", "voice_profile_id", "mapping_version", "scope",
                "script_revision", "artifact_hash"):
        assert expected.get(key) not in (None, ""), key
    assert expected["artifact_path"] == expected["path"]
    assert verify_identity(Path(expected["path"]), expected=expected, require_media=True) == ""


#: S 包 `step04_merge.timeline.AUDIO_IDENTITY_V2_FIELDS` 的形状（2026-10-03 交付版）——
#: 这里抄一份是为了让 T 的用例不依赖 S 模块能否导入；字段口径变了两边都要动。
_S_IDENTITY_FIELDS = (
    "unit_id", "scope", "script_revision", "text_sha256", "provider", "model_id",
    "model_revision", "voice_profile_id", "speaker", "mapping_version", "instruction",
    "language", "rate", "volume", "format", "sample_rate", "artifact_hash", "request_key",
)


def test_manifest_units_satisfy_the_step04_consumer_contract(tmp_path):
    """S 包消费硬要求：18 项富身份 + 生产者自报编号（不按数组位置猜身份）."""
    ctx = make_ctx(tmp_path / "tts", attempt_id="a-800")
    dialogue = make_unit("对白一句。", unit_id="shot-3", kind="dialogue")
    narration = make_unit("旁白一句。", unit_id="narration-N2", kind="narration")
    dialogue.source_index, narration.source_index = 3, 2
    with ctx:
        _publish(ctx, [dialogue, narration], StubProvider(cap=50))
    units = {u["unit_id"]: u for u in eio.read_manifest(ctx.attempt)["units"]}

    for rec in units.values():
        identity = rec["voice_identity"]
        missing = [f for f in _S_IDENTITY_FIELDS if identity.get(f) in (None, "")]
        # model_revision/instruction/rate/volume 允许 null（"未生效"是合法事实）
        missing = [f for f in missing
                   if f not in ("model_revision", "instruction", "rate", "volume")]
        assert missing == [], (rec["unit_id"], missing)
        assert identity["text_sha256"] == identity["text_hash"]

    assert units["shot-3"]["shot_index"] == 3
    assert units["shot-3"]["kind"] == "dialogue"
    assert units["narration-N2"]["narration_index"] == 2
    assert units["narration-N2"]["kind"] == "narration"
    # 对白单元不得带 narration_index，旁白单元不得带 shot_index（编号不许串轴）
    assert "narration_index" not in units["shot-3"]
    assert "shot_index" not in units["narration-N2"]


def test_plan_only_writes_units_without_media(tmp_path):
    from tts.service import register_capabilities
    register_capabilities("tts_stub_test", StubProvider().capabilities())
    units = [make_unit("规划用的一句话。" * 6, unit_id="shot-7")]
    target = write_speech_unit_plan(tmp_path / "plan", units, scope={"chapter": 2},
                                    script_revision="rev-1", provider="tts_stub_test")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["n_units"] == 1 and data["units"][0]["unit_id"] == "shot-7"
    entry = data["units"][0]
    assert entry["segments"] and entry["segments_lossless"] is True
    assert "".join(entry["text"]) != "" and entry["provider_cap"] == 20
    assert not (tmp_path / "plan" / "attempts").exists()


def test_production_paths_guard_refuses_candidate_outside_root(tmp_path):
    ctx = make_ctx(tmp_path / "tts", attempt_id="a-600")
    with pytest.raises(eio.EnhancementError):
        ctx.guard(tmp_path / "elsewhere" / "x.wav")
