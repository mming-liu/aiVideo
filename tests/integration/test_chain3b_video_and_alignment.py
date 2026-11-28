# -*- coding: utf-8 -*-
"""I2 链 3 的另外两段：**VideoRevision** 与 **alignment** 的真实 parser/守卫/终检.

* 视频：合法 ``replace_references`` → 新 READY attempt（零 POST）；残余由**消费端**
  （``inspect`` 裁决）拒收；错误 base key / 未知角色一律拒收。
* 对齐：合法完整 ``subtitle_alignment.v1`` → validated_timings → S 的 cue 单源；
  身份/摘要不符整请求拒收，旧 accepted 逐字节不动。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import enhancement_io as eio
from shared import video_attempts as va
from tests.integration import chain_support as cs

pytestmark = pytest.mark.skipif(cs.FFMPEG is None, reason="需要 ffmpeg")

VIDEO_REROLL = cs.REPO / "tools" / "video_reroll.py"
ALIGN_CLI = cs.REPO / "tools" / "align_subtitles.py"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-i-chain")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    yield


def _base_attempt(monkeypatch, exp: str, attempt_id: str = "a-rev-base"):
    """先跑一次真实候选，得到带 frozen_request 的 base attempt."""
    from tests.integration.video_chain_stub import FileBackedStubVideoService
    doc = cs.script()
    ctx = cs.video_ctx(exp, attempt_id=attempt_id)
    frames = cs.frames(ctx.attempt_dir / "frames")
    service = FileBackedStubVideoService()
    out = cs.run_candidate(monkeypatch, ctx, doc=doc, service=service, frame_paths=frames)
    assert out is not None
    frozen = json.loads((ctx.attempt_dir / eio.INPUT_FILENAME).read_text(encoding="utf-8"))
    return ctx, frames, frozen["frozen_request"]


# --------------------------------------------------------- 视频：合法修订

def test_chain3_video_revision_applies_without_new_post(monkeypatch, tmp_path):
    """合法 VideoRevision → 新 READY attempt、key 变化、**零 POST**（修订不等于重新生成）."""
    from tools import video_reroll as vr

    exp = cs.new_experiment_id("i3vid")
    ctx, frames, frozen = _base_attempt(monkeypatch, exp)
    package_root = ctx.package_root
    posts_before = va.submit_post_count(package_root)

    new_frame = tmp_path / "replacement_first_frame.png"
    new_frame.write_bytes(b"new-first-frame-i3" + b"\0" * 64)
    revision = tmp_path / "revision.json"
    revision.write_text(json.dumps({
        "schema_version": "video_revision.v1", "request_id": "rv-1",
        "base_request_key": frozen["request_key"],
        "operation": "replace_references",
        "issue_ids": ["issue-A", "issue-B"],
        "expected_resolved_issues": ["issue-A"],
        "reason": "换掉参考帧绑定（本操作只覆盖引用层）",
        "replacement": [
            {"role": "first_frame", "path": str(new_frame),
             "sha256": eio.sha256_file(new_frame)},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    rc = vr.main(["apply-revision", "--attempt", str(ctx.attempt_dir),
                  "--revision", str(revision)])
    assert rc == eio.EXIT_OK
    assert va.submit_post_count(package_root) == posts_before, "修订不得产生任何生成 POST"

    rev_attempts = [p for p in (package_root / "attempts").iterdir()
                    if p.name.startswith("rev-")]
    assert len(rev_attempts) == 1
    new = rev_attempts[0]
    assert va.current_state(new) == "ready"
    payload = json.loads((new / eio.INPUT_FILENAME).read_text(encoding="utf-8"))
    assert payload["parent_attempt_id"] == ctx.attempt_dir.name
    assert payload["frozen_request"]["request_key"] != frozen["request_key"], \
        "换引用必须换 key（不许藏在旧 key 下）"
    assert payload["frozen_request"]["references"][0]["role"] == "first_frame"
    # 残余必须如实留在记录里（不静默销账）
    assert payload["revision"]["issue_ids"] == ["issue-A", "issue-B"]


def test_chain3_video_residual_is_refused_by_consumer(monkeypatch, tmp_path):
    """局部修完仍残余 ⇒ 消费端（inspect 裁决）拒收，accepted 不动."""
    from tools import video_reroll as vr

    exp = cs.new_experiment_id("i3res")
    ctx, _frames, frozen = _base_attempt(monkeypatch, exp)
    new_frame = tmp_path / "f2.png"
    new_frame.write_bytes(b"f2" + b"\0" * 64)
    revision = tmp_path / "revision.json"
    revision.write_text(json.dumps({
        "request_id": "rv-2", "base_request_key": frozen["request_key"],
        "operation": "replace_references",
        "issue_ids": ["issue-A", "issue-B"], "expected_resolved_issues": ["issue-A"],
        "reason": "只覆盖引用层", "replacement": [{"role": "first_frame",
                                                 "path": str(new_frame)}],
    }, ensure_ascii=False), encoding="utf-8")
    assert vr.main(["apply-revision", "--attempt", str(ctx.attempt_dir),
                    "--revision", str(revision)]) == eio.EXIT_OK
    new = [p for p in (ctx.package_root / "attempts").iterdir() if p.name.startswith("rev-")][0]
    accepted_before = eio.read_accepted(new)

    # 消费端证据：残余问题仍在（合同类 ⇒ 必须回生产者，不许在后处理掩盖）
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({
        "media_status": "pass", "semantic_status": "fail",
        "coverage": {"shots": ["7"]},
        "issues": [{"issue_id": "issue-B", "kind": "frame_binding",
                    "time_range": [0.0, 1.0], "entity_refs": ["沈砚"],
                    "expected": "首帧为沈砚进门",
                    "observed": "首帧仍是旧绑定",
                    "evidence_hashes": ["h-issue-b"],
                    "required_change": "重绑首帧", "field_paths": ["references[0]"]}],
    }, ensure_ascii=False), encoding="utf-8")
    rc = vr.main(["inspect", "--attempt", str(new), "--checks-file", str(checks)])
    assert rc == eio.EXIT_REJECTED, "残余合同类问题必须拒收"
    assert eio.read_accepted(new) == accepted_before, "拒收不得移动 accepted"


def test_chain3_video_revision_rejects_stale_base_and_unknown_role(monkeypatch, tmp_path):
    """守卫反例：base key 已变 ⇒ 拒绝在旧事实上替换；未知角色 ⇒ 拒收."""
    from tools import video_reroll as vr

    exp = cs.new_experiment_id("i3guard")
    ctx, _frames, frozen = _base_attempt(monkeypatch, exp)

    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({
        "request_id": "rv-3", "base_request_key": "0" * 64,
        "operation": "replace_references", "replacement": [],
    }, ensure_ascii=False), encoding="utf-8")
    rc = vr.main(["apply-revision", "--attempt", str(ctx.attempt_dir),
                  "--revision", str(stale)])
    assert rc == eio.EXIT_BUSY

    bad_role = tmp_path / "bad_role.json"
    bad_role.write_text(json.dumps({
        "request_id": "rv-4", "base_request_key": frozen["request_key"],
        "operation": "replace_references",
        "replacement": [{"role": "不存在的角色"}],
    }, ensure_ascii=False), encoding="utf-8")
    rc = vr.main(["apply-revision", "--attempt", str(ctx.attempt_dir),
                  "--revision", str(bad_role)])
    assert rc == eio.EXIT_INVALID_INPUT

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(json.dumps({
        "request_id": "rv-5", "base_request_key": frozen["request_key"],
        "operation": "改剧情", "replacement": {},
    }, ensure_ascii=False), encoding="utf-8")
    rc = vr.main(["apply-revision", "--attempt", str(ctx.attempt_dir),
                  "--revision", str(unsupported)])
    assert rc == eio.EXIT_INVALID_INPUT, "故事取舍必须交回生产者，本包不接"


# --------------------------------------------------------- 对齐：合法与拒收

def _alignment_fixture(root: Path):
    from shared import subtitle_alignment as sa
    from tests.enhancements import fixture_media as fm

    root.mkdir(parents=True, exist_ok=True)
    audio = fm.build_speech_wav(root, seconds=3.0, name="shot7.wav")
    audio = audio.resolve()
    text = "雨停之前，他就走了。"
    identity = fm.write_audio_identity(audio, unit_id="u-shot7", text=text,
                                       script_revision="rev-al", scope={"chapter": 1})
    manifest = fm.write_tts_manifest(root / "audio_manifest.json", unit_id="u-shot7",
                                     audio_path=audio, identity=identity)
    segments = [{"segment_id": "s1", "char_start": 0, "char_end": 4},
                {"segment_id": "s2", "char_start": 4, "char_end": 10}]
    request = fm.make_request_dict(
        audio_path=audio, unit_id="u-shot7", spoken_text=text, subtitle_text=text,
        mapping=[], segments=segments, script_revision="rev-al", shot_index=7,
        audio_manifest_path=manifest, scope={"chapter": 1})
    units = [{"char_start": 0, "char_end": 4, "start_sec": 0.20, "end_sec": 1.00},
             {"char_start": 4, "char_end": 10, "start_sec": 1.10, "end_sec": 2.60}]
    alignment, coverage = sa.build_alignment(request, units, backend="external",
                                             backend_version="fixture-1",
                                             model_revision=None,
                                             diagnostics={"fixture": True})
    req_path = fm.write_json(root / "request.json", request)
    align_path = fm.write_json(root / "alignment.json", alignment)
    return {"root": root, "request": req_path, "manifest": manifest,
            "alignment": align_path, "alignment_obj": alignment, "coverage": coverage,
            "text": text, "segments": segments}


def test_chain3_alignment_legal_reaches_cues_and_confirms(tmp_path):
    """合法完整 alignment → 真实 CLI → validated_timings → S 的 cue 单源."""
    from tools import align_subtitles as align_cli
    from shared.subtitle_contract import build_subtitle_plan

    fx = _alignment_fixture(tmp_path / "align")
    out_dir = tmp_path / "alignment_out"
    rc = align_cli.main(["--input", str(fx["request"]),
                         "--audio-candidates", str(fx["manifest"]),
                         "--alignment", str(fx["alignment"]),
                         "--out-dir", str(out_dir), "--attempt-id", "al-1"])
    assert rc == eio.EXIT_OK

    attempt = out_dir / "attempts" / "al-1"
    timings_path = attempt / "outputs" / "validated_timings.json"
    assert timings_path.is_file()
    timings = json.loads(timings_path.read_text(encoding="utf-8"))
    assert timings["time_base"] == "shot_local_seconds"
    assert timings["shot_index"] == 7
    assert eio.read_accepted(out_dir), "合法对齐必须提交 accepted 指针"

    # C 的产物 → S 的 cue（真实适配器，不重算时窗）
    shots = [{"index": 7, "subtitle": '他道：“雨停之前。”'}]
    plan = build_subtitle_plan(shots, [3.0], [(0.0, 3.0)], validated_timings=timings,
                               script_revision="rev-al")
    assert plan.source == "alignment"
    assert plan.cues, "合法对齐必须产出 cue"
    assert plan.cues[0].source == "alignment"


def test_chain3_alignment_rejects_wrong_identity_without_moving_accepted(tmp_path):
    """错音频摘要 ⇒ 整请求拒收，且旧 accepted 逐字节不动（不降级掩盖错绑定）."""
    from tools import align_subtitles as align_cli

    fx = _alignment_fixture(tmp_path / "align2")
    out_dir = tmp_path / "alignment_out2"
    assert align_cli.main(["--input", str(fx["request"]),
                           "--audio-candidates", str(fx["manifest"]),
                           "--alignment", str(fx["alignment"]),
                           "--out-dir", str(out_dir), "--attempt-id", "al-ok"]) == eio.EXIT_OK
    good = (out_dir / "accepted.json").read_bytes()

    bad = json.loads(fx["alignment"].read_text(encoding="utf-8"))
    bad["request_sha256"] = "f" * 64
    bad_path = tmp_path / "align_bad.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    rc = align_cli.main(["--input", str(fx["request"]),
                         "--audio-candidates", str(fx["manifest"]),
                         "--alignment", str(bad_path),
                         "--out-dir", str(out_dir), "--attempt-id", "al-bad"])
    assert rc == eio.EXIT_REJECTED
    assert (out_dir / "accepted.json").read_bytes() == good, "拒收不得移动 accepted"

    # 换一份被改过的音频（摘要不符）⇒ 身份门拒收
    from tests.enhancements import fixture_media as fm
    other = fm.build_speech_wav(tmp_path, seconds=2.0, name="other.wav").resolve()
    req2 = json.loads(fx["request"].read_text(encoding="utf-8"))
    req2["audio_path"] = str(other)
    req2["audio_sha256"] = fm.sha256_file(other)
    req2_path = tmp_path / "request2.json"
    req2_path.write_text(json.dumps(req2, ensure_ascii=False), encoding="utf-8")
    rc = align_cli.main(["--input", str(req2_path),
                         "--audio-candidates", str(fx["manifest"]),
                         "--alignment", str(fx["alignment"]),
                         "--out-dir", str(out_dir), "--attempt-id", "al-mismatch"])
    assert rc == eio.EXIT_REJECTED, "音频身份不符必须拒收"
    assert (out_dir / "accepted.json").read_bytes() == good
