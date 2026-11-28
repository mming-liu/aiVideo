# -*- coding: utf-8 -*-
"""``MergeTimeline v1`` 的 golden 矩阵（计划 §S2/§S3、§15「转场表示/转场合法集合」）.

这里的期望全部来自**计划给定的 golden**，不由现返回值倒推；反例断言具体 code，
并确认没有 clamp / 静默回退这类副作用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _candidate_fixtures import seal_accepted_candidate  # noqa: E402

from shared import enhancement_io as eio  # noqa: E402
from shared.media_contract import MERGE_FPS  # noqa: E402
from step04_merge.timeline import (  # noqa: E402

    BoundaryTransition, ClipEntry, EndpointEffect, MergeTimeline, OverlayEntry,
    TimelineAudioTrack, TimelineError, overlays_from_motion_plan, resolve_merge_candidates,
    stable_shot_id, transitions_from_legacy, validate_timeline, windows_from_legacy,
)

SCOPE = "chapter_001"
REV = "rev-abc"


def _clips(tmp_path, durs, *, kinds=None, indexes=None, names=None) -> tuple:
    entries = []
    kinds = kinds or ["shot"] * len(durs)
    indexes = indexes if indexes is not None else list(range(1, len(durs) + 1))
    for i, dur in enumerate(durs):
        name = (names or [f"clip_{i}.mp4"] * len(durs))[i]
        path = tmp_path / name
        path.write_bytes(b"stub" * 100)
        entries.append(ClipEntry(
            shot_index=int(indexes[i]), kind=str(kinds[i]),
            stable_id=stable_shot_id(SCOPE, REV, str(kinds[i]), int(indexes[i])),
            path=path, duration_sec=0.0, raw_duration_sec=float(dur), frames=0))
    return tuple(entries)


def _timeline(tmp_path, durs, boundaries=(), endpoints=(), *, kinds=None, indexes=None,
              audio_tracks=(), overlays=(), names=None, audio_mode="native"):
    return MergeTimeline(
        scope=SCOPE, script_revision=REV, width=320, height=180, fps=MERGE_FPS,
        clips=_clips(tmp_path, durs, kinds=kinds, indexes=indexes, names=names),
        boundary_transitions=tuple(boundaries), endpoint_effects=tuple(endpoints),
        audio_mode=audio_mode, audio_tracks=tuple(audio_tracks),
        overlays=tuple(overlays))


# ------------------------------------------------------------------ 时长 golden

def test_single_clip_fade_in_golden(tmp_path):
    """golden：单镜 2s 首淡入 0.5 ⇒ 总长 2（端点不减总长）。"""
    tl = validate_timeline(_timeline(tmp_path, [2.0],
                                     endpoints=[EndpointEffect("fade_in", 0.5, 0)]))
    assert tl.total_duration_sec == pytest.approx(2.0)
    assert [(w.start_sec, w.end_sec) for w in tl.windows] == [(0.0, 2.0)]
    assert tl.endpoint_effects[0].kind == "fade_in"
    assert tl.endpoint_effects[0].duration_sec == pytest.approx(0.5)


def test_two_clips_final_fade_out_golden(tmp_path):
    """golden：[2,3] 末淡出 0.5 ⇒ 总长 5（端点不减总长）。"""
    tl = validate_timeline(_timeline(tmp_path, [2.0, 3.0],
                                     endpoints=[EndpointEffect("fade_out", 0.5, 1)]))
    assert tl.total_duration_sec == pytest.approx(5.0)
    assert tl.boundary_transitions[0].kind == "cut"


def test_boundary_fade_golden(tmp_path):
    """golden：边界 fade 0.5（d=[2,3]）⇒ 总长 4.5，窗口 (0,2)/(1.5,4.5)。"""
    tl = validate_timeline(_timeline(tmp_path, [2.0, 3.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.5)]))
    assert tl.total_duration_sec == pytest.approx(4.5)
    assert [(w.start_sec, w.end_sec) for w in tl.windows] == [(0.0, 2.0), (1.5, 4.5)]


def test_total_is_sum_minus_overlaps_for_consecutive_transitions(tmp_path):
    tl = validate_timeline(_timeline(tmp_path, [1.0, 1.0, 1.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.5),
                                                 BoundaryTransition(2, "wipeleft", 0.5)]))
    assert tl.total_duration_sec == pytest.approx(2.0)
    assert [(w.start_sec, w.end_sec) for w in tl.windows] == \
        [(0.0, 1.0), (0.5, 1.5), (1.0, 2.0)]


def test_tdur_equal_to_min_is_legal(tmp_path):
    """d=[0.3,5.0] 且 tdur=0.3 恰好等于 min ⇒ 合法边界（量化后仍 ≤ min）。"""
    tl = validate_timeline(_timeline(tmp_path, [0.3, 5.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.3)]))
    first = tl.clips[0].duration_sec
    assert tl.boundary_transitions[0].duration_sec <= first + 1e-9
    assert tl.total_duration_sec == pytest.approx(
        tl.clips[0].duration_sec + 5.0 - tl.boundary_transitions[0].duration_sec)


def test_tdur_longer_than_min_is_rejected_not_clamped(tmp_path):
    """d=[0.3,5.0], tdur=1.5 ⇒ 拒收（不 max(0) clamp、不按后一镜长度放行）。"""
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [0.3, 5.0],
                                    boundaries=[BoundaryTransition(1, "fade", 1.5)]))
    assert ei.value.code == "invalid_transition_timing"


def test_zero_duration_xfade_is_legal(tmp_path):
    """tdur 是有限非负数：0 合法（编译器按无重叠处理，不 clamp）。"""
    tl = validate_timeline(_timeline(tmp_path, [2.0, 2.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.0)]))
    assert tl.total_duration_sec == pytest.approx(4.0)
    assert tl.boundary_transitions[0].duration_sec == 0.0


def test_clip_shorter_than_one_frame_is_rejected(tmp_path):
    """两端过短：不足 1 帧的素材非法（量化函数单源）。"""
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [0.02, 2.0]))
    assert ei.value.code == "invalid_clip_duration"


def test_missing_boundary_entry_defaults_to_cut(tmp_path):
    """缺项的边界按 cut（文档化默认，与旧入口同义）；编号越界/重复另算错误。"""
    tl = validate_timeline(_timeline(tmp_path, [1.0, 1.0, 1.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.2)]))
    assert [b.kind for b in tl.boundary_transitions] == ["fade", "cut"]
    # 过渡也按帧量化：0.2s → 5 帧（0.2083…），总长 = 3 − 0.2083…
    assert tl.boundary_transitions[0].duration_sec == pytest.approx(5 / 24)
    assert tl.total_duration_sec == pytest.approx(3.0 - 5 / 24)


def test_boundary_index_out_of_range_or_duplicated_is_rejected(tmp_path):
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [1.0, 1.0],
                                    boundaries=[BoundaryTransition(5, "fade", 0.2)]))
    assert ei.value.code == "boundary_index_out_of_range"
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [1.0, 1.0],
                                    boundaries=[BoundaryTransition(1, "fade", 0.2),
                                                BoundaryTransition(1, "cut", 0.0)]))
    assert ei.value.code == "duplicate_boundary_index"


def test_unknown_boundary_kind_is_rejected(tmp_path):
    """合法集合单源在 shared.media_contract：未知 kind 拒收（不静默回退 fade）。"""
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [2.0, 2.0],
                                    boundaries=[BoundaryTransition(1, "wipezigzag", 0.5)]))
    assert ei.value.code == "illegal_transition_kind"


def test_all_legal_xfade_kinds_are_accepted(tmp_path):
    from shared.media_contract import LEGAL_XFADE_TRANSITIONS
    for kind in sorted(LEGAL_XFADE_TRANSITIONS):
        tl = validate_timeline(_timeline(tmp_path, [1.0, 1.0],
                                         boundaries=[BoundaryTransition(1, kind, 0.5)]))
        assert tl.boundary_transitions[0].kind == kind


def test_endpoint_duration_longer_than_clip_is_rejected(tmp_path):
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [1.0, 2.0],
                                    endpoints=[EndpointEffect("fade_in", 1.5, 0)]))
    assert ei.value.code == "endpoint_longer_than_clip"


def test_endpoint_on_the_wrong_clip_is_rejected(tmp_path):
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [1.0, 2.0],
                                    endpoints=[EndpointEffect("fade_in", 0.5, 1)]))
    assert ei.value.code == "endpoint_effect_misplaced"


def test_duplicate_clip_identity_is_rejected(tmp_path):
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [1.0, 1.0], indexes=[1, 1]))
    assert ei.value.code == "duplicate_clip_identity"


def test_missing_clip_file_is_rejected(tmp_path):
    tl = _timeline(tmp_path, [1.0])
    Path(tl.clips[0].path).unlink()
    with pytest.raises(TimelineError) as ei:
        validate_timeline(tl)
    assert ei.value.code == "clip_missing"


# ------------------------------------------------------------------ 旧入口适配层

def test_legacy_first_fade_in_maps_to_endpoint(tmp_path):
    boundaries, endpoints, _notes = transitions_from_legacy(
        [("fade_in", 0.5), ("cut", 0.0)], n_clips=2)
    assert endpoints == (EndpointEffect("fade_in", 0.5, 0),)
    assert boundaries == (BoundaryTransition(1, "cut", 0.0),)


def test_legacy_final_fade_out_maps_to_endpoint_and_cut_boundary(tmp_path):
    """**有意的行为变更**（§15 golden 为准）：旧末端 fade_out 不再同时当边界。"""
    windows, total = windows_from_legacy([2.0, 3.0], [("cut", 0.0), ("fade_out", 0.5)])
    assert windows == [(0.0, 2.0), (2.0, 5.0)]      # 旧实现会给 (1.5, 4.5) / total 4.5
    assert total == pytest.approx(5.0)


def test_legacy_first_item_that_is_not_fade_in_is_cut_with_a_note():
    boundaries, endpoints, notes = transitions_from_legacy(
        [("fade", 0.5), ("cut", 0.0)], n_clips=2)
    assert endpoints == ()
    assert boundaries[0].kind == "cut"
    assert notes and "没有前镜" in notes[0]


def test_legacy_single_item_does_not_guess_both_endpoints():
    _b, endpoints, _n = transitions_from_legacy([("fade_in", 0.4)], n_clips=1)
    assert endpoints == (EndpointEffect("fade_in", 0.4, 0),)
    _b, endpoints, _n = transitions_from_legacy([("fade_out", 0.4)], n_clips=1)
    assert endpoints == (EndpointEffect("fade_out", 0.4, 0),)


def test_legacy_fade_in_at_a_middle_boundary_is_rejected():
    with pytest.raises(TimelineError) as ei:
        transitions_from_legacy([("cut", 0.0), ("fade_in", 0.5), ("cut", 0.0)], n_clips=3)
    assert ei.value.code == "endpoint_effect_misplaced"


def test_legacy_fade_out_at_the_first_position_of_a_multi_clip_is_rejected():
    with pytest.raises(TimelineError) as ei:
        transitions_from_legacy([("fade_out", 0.5), ("cut", 0.0)], n_clips=2)
    assert ei.value.code == "endpoint_effect_misplaced"


# ------------------------------------------------------------------ 音频轨

def test_narration_track_beyond_timeline_is_rejected(tmp_path):
    track = TimelineAudioTrack(kind="narration", stable_id="narration:N1",
                               path=tmp_path / "n1.mp3", start_sec=1.0, end_sec=9.0)
    (tmp_path / "n1.mp3").write_bytes(b"x" * 10)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [2.0, 2.0], audio_tracks=[track]))
    assert ei.value.code == "audio_window_out_of_timeline"


def test_shot_tts_track_must_bind_a_clip(tmp_path):
    (tmp_path / "t.mp3").write_bytes(b"x" * 10)
    track = TimelineAudioTrack(kind="shot_tts", stable_id="t", path=tmp_path / "t.mp3",
                               clip_index=None)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [2.0], audio_tracks=[track]))
    assert ei.value.code == "audio_track_without_clip"


def test_audio_track_missing_file_is_rejected(tmp_path):
    track = TimelineAudioTrack(kind="narration", stable_id="narration:N1",
                               path=tmp_path / "none.mp3", start_sec=0.0, end_sec=1.0)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(_timeline(tmp_path, [2.0], audio_tracks=[track]))
    assert ei.value.code == "audio_track_missing"


# ------------------------------------------------------------------ overlay 身份

def _overlay(clip, *, overlay_id="ov1", start=0.2, end=0.8, kind="image", path=None):
    return OverlayEntry(overlay_id=overlay_id, stable_shot_id=clip.stable_id,
                        shot_index=clip.shot_index, kind=kind,
                        path=path or Path("stub.png"), start_sec=start, end_sec=end)


def test_overlay_binds_to_the_hook_not_the_first_body_shot(tmp_path):
    """hook(index=0) 是独立身份：绑定按稳定 id 走，不落到第一正文镜头。"""
    tl = _timeline(tmp_path, [2.0, 2.0], kinds=["hook", "shot"], indexes=[0, 1])
    hook_clip, body_clip = tl.clips
    png = tmp_path / "ov.png"
    png.write_bytes(b"png")
    good = _overlay(hook_clip, path=png)
    validated = validate_timeline(MergeTimeline(
        scope=tl.scope, script_revision=tl.script_revision, width=tl.width, height=tl.height,
        fps=tl.fps, clips=tl.clips, boundary_transitions=(), overlays=(good,)))
    assert validated.overlays[0].stable_shot_id == hook_clip.stable_id

    # 绑到正文镜头 1（index=1）的身份却写 hook 的 shot_index ⇒ 身份不符即拒收
    wrong = OverlayEntry(overlay_id="ov2", stable_shot_id=body_clip.stable_id, shot_index=0,
                         kind="image", path=png, start_sec=0.2, end_sec=0.8)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(MergeTimeline(
            scope=tl.scope, script_revision=tl.script_revision, width=tl.width,
            height=tl.height, fps=tl.fps, clips=tl.clips, boundary_transitions=(),
            overlays=(good, wrong)))
    assert ei.value.code == "overlay_identity_mismatch"


def test_overlay_from_another_revision_is_rejected(tmp_path):
    tl = _timeline(tmp_path, [2.0])
    png = tmp_path / "ov.png"
    png.write_bytes(b"png")
    stale = OverlayEntry(overlay_id="ov1",
                         stable_shot_id=stable_shot_id(SCOPE, "rev-OLD", "shot", 1),
                         shot_index=1, kind="image", path=png, start_sec=0.2, end_sec=0.8)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(MergeTimeline(
            scope=tl.scope, script_revision=tl.script_revision, width=tl.width,
            height=tl.height, fps=tl.fps, clips=tl.clips, boundary_transitions=(),
            overlays=(stale,)))
    assert ei.value.code == "overlay_identity_mismatch"


def test_overlay_out_of_window_and_overlap_are_rejected(tmp_path):
    tl = _timeline(tmp_path, [2.0, 2.0])
    png = tmp_path / "ov.png"
    png.write_bytes(b"png")
    outside = _overlay(tl.clips[0], start=1.5, end=2.5, path=png)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(MergeTimeline(
            scope=tl.scope, script_revision=tl.script_revision, width=tl.width,
            height=tl.height, fps=tl.fps, clips=tl.clips, boundary_transitions=(),
            overlays=(outside,)))
    assert ei.value.code == "overlay_out_of_window"

    dup = _overlay(tl.clips[0], overlay_id="ovA", start=0.1, end=1.0, path=png)
    dup2 = _overlay(tl.clips[0], overlay_id="ovB", start=0.5, end=1.2, path=png)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(MergeTimeline(
            scope=tl.scope, script_revision=tl.script_revision, width=tl.width,
            height=tl.height, fps=tl.fps, clips=tl.clips, boundary_transitions=(),
            overlays=(dup, dup2)))
    assert ei.value.code == "overlay_overlap"


def test_overlay_unknown_shot_is_rejected(tmp_path):
    tl = _timeline(tmp_path, [2.0])
    png = tmp_path / "ov.png"
    png.write_bytes(b"png")
    unknown = OverlayEntry(overlay_id="ov1", stable_shot_id=stable_shot_id(SCOPE, REV, "shot", 7),
                           shot_index=7, kind="image", path=png, start_sec=0.1, end_sec=0.5)
    with pytest.raises(TimelineError) as ei:
        validate_timeline(MergeTimeline(
            scope=tl.scope, script_revision=tl.script_revision, width=tl.width,
            height=tl.height, fps=tl.fps, clips=tl.clips, boundary_transitions=(),
            overlays=(unknown,)))
    assert ei.value.code == "overlay_shot_not_found"


# ------------------------------------------------------------------ 动效计划（A 包）

def test_motion_plan_maps_local_time_into_the_clip_window(tmp_path):
    tl = validate_timeline(_timeline(tmp_path, [2.0, 3.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.5)]))
    png = tmp_path / "ov.png"
    png.write_bytes(b"png")
    plan = {"schema_version": "motion_overlay_plan.v1", "scope": SCOPE,
            "script_revision": REV, "fps": MERGE_FPS,
            "overlays": [{"overlay_id": "title1", "spec_id": "spec1", "shot_index": 2,
                          "kind": "image", "path": str(png),
                          "start_sec": 0.5, "end_sec": 1.5, "layer": 0}]}
    overlays = overlays_from_motion_plan(plan, scope=SCOPE, script_revision=REV,
                                         clips=tl.clips, windows=tl.windows)
    assert overlays[0].start_sec == pytest.approx(1.5 + 0.5)   # window.start(1.5) + 0.5
    assert overlays[0].end_sec == pytest.approx(1.5 + 1.5)
    assert overlays[0].stable_shot_id == tl.clips[1].stable_id


def test_motion_plan_unknown_schema_or_revision_is_rejected(tmp_path):
    tl = validate_timeline(_timeline(tmp_path, [2.0]))
    with pytest.raises(TimelineError) as ei:
        overlays_from_motion_plan({"schema_version": "nope"}, scope=SCOPE,
                                  script_revision=REV, clips=tl.clips, windows=tl.windows)
    assert ei.value.code == "unsupported_motion_plan_schema"
    with pytest.raises(TimelineError) as ei:
        overlays_from_motion_plan({"schema_version": "motion_overlay_plan.v1", "scope": SCOPE,
                                   "script_revision": "other", "overlays": []},
                                  scope=SCOPE, script_revision=REV, clips=tl.clips,
                                  windows=tl.windows)
    assert ei.value.code == "motion_plan_revision_mismatch"


# ------------------------------------------------------------------ 候选清单（F resolver）

def _manifest(tmp_path, *, package="video", revision=REV, units=None, name="m.json",
              accepted=True):
    """造候选：**走生产者真实出口**（seal_manifest + commit_accepted），不手搓半成品."""
    man, _sha = seal_accepted_candidate(
        tmp_path, (name or "cand").replace(".json", "") or "cand", package=package,
        scope={"chapter": 1}, revision=revision, source="accepted_script", units=units)
    if not accepted:
        eio.accepted_path(man.parent).unlink()
    return man


def _write_identity_media(tmp_path, name="u1.wav", *, unit_id="u1", revision=REV,
                          **overrides):
    """用 T 的单源接口造**真实**媒体 + v2 侧车（不手搓字段清单）."""
    import hashlib

    from shared.audio_identity import build_identity, write_identity

    media = tmp_path / name
    media.write_bytes(b"RIFF" + bytes(200))
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    payload = build_identity(
        unit_id=unit_id, scope={"chapter": 1}, script_revision=revision, text="对白一句。",
        provider="stub", model_id="stub-tts", model_revision=None, voice_profile_id="p1",
        speaker=None, mapping_version="v1", instruction=None, language="zh", rate=None,
        rate_applied=False, volume=None, volume_applied=False, format="wav",
        sample_rate=44100, artifact_path=media, artifact_hash=digest,
        artifact_bytes=media.stat().st_size, request_key="k1")
    payload.update(overrides)
    if not overrides.get("_skip_sidecar"):
        write_identity(media, {k: v for k, v in payload.items() if k != "_skip_sidecar"})
    return media, {k: v for k, v in payload.items() if k != "_skip_sidecar"}


def test_audio_candidates_accept_nullable_identity_fields(tmp_path):
    """判别性①：`model_revision/instruction/rate/volume/speaker` 为 null 是**合法完整身份**
    （「参数未生效 / 声线由 model_id 决定」），不得判成缺字段。"""
    media, identity = _write_identity_media(tmp_path, unit_id="shot-1")
    for field in ("model_revision", "instruction", "rate", "volume", "speaker"):
        assert identity[field] in (None, ""), field
    body = tmp_path / "c.mp4"
    body.write_bytes(b"z" * 10)
    video = _manifest(tmp_path, units=[{"unit_id": "shot-1", "shot_index": 1,
                                        "path": str(body)}])
    audio = _manifest(tmp_path, package="tts", name="audio.json", units=[
        {"unit_id": "shot-1", "kind": "dialogue", "shot_index": 1, "path": str(media),
         "voice_identity": identity}])
    cands = resolve_merge_candidates(video, audio, script_revision=REV,
                                     expect_scope={"chapter": 1},
                                     experiment_root=tmp_path)
    assert cands.dialogue[1] == media
    assert cands.dialogue_identity[1]["unit_id"] == "shot-1"


def test_audio_candidates_reject_missing_required_identity_field(tmp_path):
    """判别性②：真正必需字段缺失（空串/null 且非可空）⇒ identity_incomplete 拒收."""
    media, identity = _write_identity_media(tmp_path, name="u2.wav", unit_id="u2")
    body = tmp_path / "c.mp4"
    body.write_bytes(b"z" * 10)
    video = _manifest(tmp_path, units=[{"unit_id": "shot-1", "shot_index": 1,
                                        "path": str(body)}])

    for field in ("model_id", "provider", "artifact_hash", "text_hash"):
        broken, _ = _write_identity_media(tmp_path, name=f"bad_{field}.wav",
                                          unit_id=f"u-{field}", _skip_sidecar=True)
        broken_identity = dict(identity, **{field: ""})
        audio = _manifest(tmp_path, package="tts", name=f"audio_{field}.json", units=[
            {"unit_id": "shot-1", "kind": "dialogue", "shot_index": 1, "path": str(broken),
             "voice_identity": broken_identity}])
        with pytest.raises(TimelineError) as ei:
            resolve_merge_candidates(video, audio, script_revision=REV,
                                     expect_scope={"chapter": 1},
                                     experiment_root=tmp_path)
        assert ei.value.code == "identity_incomplete", field
    assert media.is_file()


def test_audio_candidates_reject_media_that_does_not_match_the_sidecar(tmp_path):
    """侧车/媒体关系：写侧车后把媒体换掉 ⇒ artifact_hash 对不上，拒收."""
    media, identity = _write_identity_media(tmp_path, name="u3.wav", unit_id="u3")
    media.write_bytes(b"RIFF" + bytes([255]) * 200)      # 换掉媒体字节
    body = tmp_path / "c.mp4"
    body.write_bytes(b"z" * 10)
    video = _manifest(tmp_path, units=[{"unit_id": "shot-1", "shot_index": 1,
                                        "path": str(body)}])
    audio = _manifest(tmp_path, package="tts", name="audio3.json", units=[
        {"unit_id": "shot-1", "kind": "dialogue", "shot_index": 1, "path": str(media),
         "voice_identity": identity}])
    with pytest.raises(TimelineError) as ei:
        resolve_merge_candidates(video, audio, script_revision=REV,
                                 expect_scope={"chapter": 1}, experiment_root=tmp_path)
    assert ei.value.code == "identity_incomplete"


def test_audio_candidates_need_rich_identity(tmp_path):
    """没有 v2 富身份（只有 old signature 那种空壳）⇒ identity_incomplete."""
    media = tmp_path / "d1.wav"
    media.write_bytes(b"x" * 100)
    body = tmp_path / "c.mp4"
    body.write_bytes(b"z" * 10)
    video = _manifest(tmp_path, units=[{"unit_id": "shot-1", "shot_index": 1,
                                        "path": str(body)}])
    audio = _manifest(tmp_path, package="tts", name="audio_empty.json", units=[
        {"unit_id": "shot-1", "kind": "dialogue", "shot_index": 1, "path": str(media),
         "voice_identity": {}}])
    with pytest.raises(TimelineError) as ei:
        resolve_merge_candidates(video, audio, script_revision=REV,
                                 expect_scope={"chapter": 1}, experiment_root=tmp_path)
    assert ei.value.code == "identity_incomplete"


def test_narration_candidate_must_declare_its_index(tmp_path):
    media = tmp_path / "n.wav"
    media.write_bytes(b"x" * 100)
    body = tmp_path / "c.mp4"
    body.write_bytes(b"z" * 10)
    video = _manifest(tmp_path, units=[{"unit_id": "shot-1", "shot_index": 1,
                                        "path": str(body)}])
    media, identity = _write_identity_media(tmp_path, name="n2.wav", unit_id="N1")
    audio = _manifest(tmp_path, package="tts", name="a2.json", units=[
        {"unit_id": "N1", "kind": "narration", "path": str(media),
         "voice_identity": identity}])
    with pytest.raises(TimelineError) as ei:
        resolve_merge_candidates(video, audio, script_revision=REV,
                                 expect_scope={"chapter": 1}, experiment_root=tmp_path)
    assert ei.value.code == "candidate_index_missing"


def test_timeline_to_dict_is_the_frozen_fact_source(tmp_path):
    tl = validate_timeline(_timeline(tmp_path, [2.0, 3.0],
                                     boundaries=[BoundaryTransition(1, "fade", 0.5)]))
    data = tl.to_dict()
    assert data["schema_version"].startswith("merge_timeline.")
    assert data["clips"][0]["frames"] == 48
    assert data["boundary_transitions"][0]["duration_sec"] == pytest.approx(0.5)
    assert json.dumps(data, ensure_ascii=False)  # 可序列化（冻结输入用）


def test_endpoint_fades_from_text_reach_the_timeline(tmp_path):
    """P1-7：首淡入/末淡出经**真实解析**后带正时长，并能过时间线校验（中英文都算）.

    反例：解析器把端点时长清成 0 ⇒ `transitions_from_legacy` 造出 0 时长的
    `EndpointEffect` ⇒ 时间线按"端点时长必须为正"拒收。
    """
    from step04_merge.main import _parse_transition
    from step04_merge.timeline import ENDPOINT_FADE_IN, ENDPOINT_FADE_OUT

    for text, want in (("fade_in", ENDPOINT_FADE_IN), ("fade_out", ENDPOINT_FADE_OUT),
                       ("淡入", ENDPOINT_FADE_IN), ("淡出", ENDPOINT_FADE_OUT)):
        kind, dur = _parse_transition(text)
        assert kind == want, (text, kind)
        assert dur > 0, (text, dur)
        boundaries, endpoints, _notes = transitions_from_legacy(
            [(kind, dur)], n_clips=1)
        assert not boundaries and endpoints and endpoints[0].duration_sec > 0
        validate_timeline(_timeline(tmp_path, [2.0], boundaries=boundaries,
                                    endpoints=endpoints))
    # 长词不被短词截走：中文"淡入淡出"仍是**边界**叠化
    assert _parse_transition("淡入淡出")[0] == "fade"
