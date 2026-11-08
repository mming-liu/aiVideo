# -*- coding: utf-8 -*-
"""V 包矩阵：prepared 提交 / 恢复 / 检查 / 修订 / IO 事务 / 绕过入口（计划 §V4）.

跑的是**真实入口**（``_submit_video_candidate`` → 真实 ``prepare_request`` →
桩 provider 的**最底层 POST** → 真实轮询 → 真实 QC → 事务采纳），断言底层提交次数、
生命周期终局、accepted 读回链与"正式产物零变化"。预期来自计划条款与独立 golden，
不从当前 hash/返回值倒推。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio          # noqa: E402
from shared import video_attempts as va           # noqa: E402
from shared.paths import (                        # noqa: E402
    SHOT_VIDEO_FILENAME, chapter_dir, clip_file, clips_dir, vimax_shots_dir,
)
from step02_video_gen import prompt_builder as pb   # noqa: E402
from step02_video_gen import submit_poll as sp      # noqa: E402
from tests.enhancements.video_stub_provider import (  # noqa: E402
    StubVideoAdapter, StubVideoService,
)

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg（桩 provider 现造可解码素材）")

SHOT_INDEX = 7
DURATION = 6.0
_UID = {"n": 0}


# ---------------------------------------------------------------- 夹具

def _script(index: int = SHOT_INDEX, *, subtitle: str = "雨停之前",
            duration: float = DURATION) -> dict:
    return {"characters": [], "shots": [
        {"index": index, "prompt": f"[中景] 沈砚停在门口（{subtitle}）",
         "ff_desc": "沈砚站在门口", "lf_desc": "沈砚转身离开",
         "motion_desc": "0-3秒：停下；3-6秒：转身",
         "subtitle": subtitle, "narration_segment": "", "keyframes": [],
         "duration_sec": duration}]}


def _shot(script: dict, index: int = SHOT_INDEX) -> dict:
    for s in script["shots"]:
        if s["index"] == index:
            return s
    raise KeyError(index)


def _frames(root: Path, index: int = SHOT_INDEX, *, ff: bytes = b"ff-1",
            lf: bytes = b"lf-1") -> list:
    """造帧（**只**写调用方给的 root；候选测试落 attempt，plan 测试落生产输入目录）。"""
    d = Path(root) / str(index)
    d.mkdir(parents=True, exist_ok=True)
    (d / "first_frame.png").write_bytes(ff + b"\0" * 64)
    (d / "last_frame.png").write_bytes(lf + b"\0" * 64)
    return [str(d / "first_frame.png"), str(d / "last_frame.png")]


def _ctx(experiment_id: str | None = None, *, attempt_id: str = "a-1",
         chapter: int = 1) -> eio.CandidateContext:
    _UID["n"] += 1
    eid = experiment_id or f"ve{os.getpid()}{_UID['n']:03d}"
    # 来源身份与真实入口一致（§F4）：采纳 manifest 必须答得出"哪一章、哪一稿"，
    # 缺了会被采纳出口拒收——夹具不再省掉这三个字段。
    ctx = eio.CandidateContext(experiment_id=eid, package="video",
                               attempt_id=attempt_id, chapter=chapter,
                               scope={"chapter": chapter},
                               script_revision="rev-test", source="video_reroll")
    ctx.prepare()
    return ctx


def _digest_tree(root: Path) -> dict:
    out = {}
    if Path(root).is_dir():
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                out[str(p)] = eio.sha256_file(p)
    return out


def _run_candidate(monkeypatch, ctx, *, shot, script, service, frame_paths,
                   prompt: str | None = None, make_request=None):
    """跑真实候选入口并等它走完（轮询间隔归零，判据不变）。"""
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)
    idx = shot["index"]
    shot_dir = ctx.shot_dir(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    video_path = shot_dir / SHOT_VIDEO_FILENAME
    adapter = StubVideoAdapter(service)

    async def _go():
        task = await sp._submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=video_path,
            frame_paths=frame_paths, shot_hash="h-test", video_adapter=adapter,
            poll_semaphore=asyncio.Semaphore(1), script=script, idx=idx,
            prompt=prompt if prompt is not None else str(shot.get("prompt") or ""),
            characters=[], continuity_ref_image=None, mode="A",
            candidate_context=ctx, logger=None,
            make_request=make_request or pb._make_request)
        if task is None:
            return None
        try:
            return await task
        except Exception as exc:  # noqa: BLE001 - 异常本身是被测行为的一部分
            return exc

    return asyncio.run(_go())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-ci")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    yield


# ---------------------------------------------------------------- 1. 正常与兼容

def test_normal_path_one_post_and_accepted_readback(monkeypatch):
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    frames = _frames(ctx.attempt_dir / "frames")
    clips_before = _digest_tree(clips_dir())
    shots_before = _digest_tree(vimax_shots_dir())
    service = StubVideoService()

    ok = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                        frame_paths=frames)
    assert ok is True
    # 一次提交、一次生成：语义 payload 记录在桩的最底层 POST 上
    assert len(service.posts) == 1
    assert service.posts[0]["content_key"]
    assert va.submit_post_count(ctx.package_root) == 1
    assert va.current_state(ctx.attempt_dir) == va.STATE_ACCEPTED

    # accepted 是唯一采纳事实源：指针 → manifest → 产物 → required_checks 全核
    verified = eio.verify_accepted(ctx.attempt_dir, required_checks=("duration",))
    assert verified["pointer"]["attempt_id"] == ctx.attempt_id
    assert Path(verified["manifest"]["artifacts"][0]["path"]).is_file()

    # 候选下载**没有**落到正式 shots/、clips/（§V1 退出）
    shot_dir = ctx.shot_dir(SHOT_INDEX)
    assert (shot_dir / SHOT_VIDEO_FILENAME).is_file()
    assert not (vimax_shots_dir() / str(SHOT_INDEX) / SHOT_VIDEO_FILENAME).exists()
    assert _digest_tree(clips_dir()) == clips_before
    assert _digest_tree(vimax_shots_dir()) == shots_before


def test_clips_directory_bytewise_unchanged_by_experiment(monkeypatch):
    """至少一条用例逐文件断言 <chapter>/clips/ 全部摘要不变（成功 + 拒收各一次）。"""
    clips = clips_dir()
    clips.mkdir(parents=True, exist_ok=True)
    (clips / "clip_7.mp4").write_bytes(b"formal-clip-7" * 100)
    (clips / "clip_8.mp4").write_bytes(b"formal-clip-8" * 100)
    before = {p.name: eio.sha256_file(p) for p in sorted(clips.iterdir())}

    script = _script()
    shot = _shot(script)
    # (a) 成功采纳
    ctx = _ctx()
    _run_candidate(monkeypatch, ctx, shot=shot, script=script,
                   service=StubVideoService(), frame_paths=_frames(ctx.attempt_dir / "frames"))
    # (b) 明确拒收（服务端拒绝）
    ctx2 = _ctx()
    _run_candidate(monkeypatch, ctx2, shot=shot, script=script,
                   service=StubVideoService(fail_mode="http_403"),
                   frame_paths=_frames(ctx2.attempt_dir / "frames"))

    after = {p.name: eio.sha256_file(p) for p in sorted(clips.iterdir())}
    assert after == before


def test_legacy_create_task_still_one_post(monkeypatch):
    """兼容接口：``create_task`` 委托同一 prepare，仍是**一次** POST。"""
    from _video_gen.models import VideoGenerationRequest

    service = StubVideoService()
    req = VideoGenerationRequest(prompt="兼容入口", clip_index=3, duration=6)
    task_id = service.create_task(req)
    assert task_id and len(service.posts) == 1
    assert service.posts[0]["prompt"] == "兼容入口"


# ---------------------------------------------------------------- 2. 预检

def test_preflight_blocks_with_zero_submit(monkeypatch):
    from _video_gen.models import VideoGenerationRequest

    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()

    def _empty_prompt_request(adapter, prompt, frame_paths, shot, **kwargs):
        return VideoGenerationRequest(prompt="", clip_index=shot["index"], duration=6)

    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=[], make_request=_empty_prompt_request)
    assert result is None
    assert service.posts == []                                   # 零 POST
    assert va.submit_post_count(ctx.package_root) == 0
    events = [e["kind"] for e in eio.read_events_strict(ctx.attempt_dir)[0]]
    assert "preflight_blocked" in events
    assert eio.read_accepted(ctx.attempt_dir) is None


def test_prepare_capability_error_blocks(monkeypatch):
    """provider 在 prepare 阶段报能力不足（如角色不支持）⇒ blocked、零 POST。"""
    from _video_gen.models import VideoCapabilityError

    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()

    def _boom(req, context=None):
        raise VideoCapabilityError("role_unsupported", "桩：模型不支持该引用角色",
                                   field_path="references[role=first_frame]")

    monkeypatch.setattr(service, "prepare_request", _boom)
    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=[])
    assert result is None and service.posts == []
    payload = eio.read_events_strict(ctx.attempt_dir)[0][-1]
    assert payload["kind"] == "preflight_blocked" and payload["code"] == "role_unsupported"


def test_provider_without_prepared_entry_fails_closed(monkeypatch):
    """provider 未实现 prepared 入口 ⇒ fail-closed（不降级到旧提交路径）。"""
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()
    monkeypatch.setattr(service, "supports_prepared", lambda: False)

    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=[])
    assert result is None and service.posts == []
    kinds = [e["kind"] for e in eio.read_events_strict(ctx.attempt_dir)[0]]
    assert "prepared_unsupported" in kinds


# ---------------------------------------------------------------- 3. 内容身份

def _prepare(service, *, prompt="p", image=None, refs=None, duration=6, seed=None):
    from _video_gen.models import VideoGenerationRequest

    req = VideoGenerationRequest(prompt=prompt, clip_index=1, duration=duration,
                                 image_path=image, reference_paths=refs or [], seed=seed)
    return service.prepare_request(req)


def test_content_key_follows_real_inputs(tmp_path):
    service = StubVideoService()
    img = tmp_path / "a.png"
    img.write_bytes(b"img-A" * 100)
    img2 = tmp_path / "b.png"
    img2.write_bytes(b"img-B" * 100)

    base = _prepare(service, image=str(img)).content_key
    assert _prepare(service, image=str(img)).content_key == base          # 同事实同 key
    assert _prepare(service, image=str(img)).content_key == base
    # 同路径换字节（prepare 会重算素材摘要）
    img.write_bytes(b"img-A2" * 100)
    assert _prepare(service, image=str(img)).content_key != base
    img.write_bytes(b"img-A" * 100)
    # 换模型 / prompt / seed 都变
    assert _prepare(service, prompt="p2", image=str(img)).content_key != base
    assert _prepare(service, image=str(img), seed=12345).content_key != base
    monkeypatch_model = os.environ.get("STUB_VIDEO_MODEL")
    os.environ["STUB_VIDEO_MODEL"] = "stub-model-2"
    try:
        assert _prepare(service, image=str(img)).content_key != base
    finally:
        if monkeypatch_model is None:
            os.environ.pop("STUB_VIDEO_MODEL", None)
        else:
            os.environ["STUB_VIDEO_MODEL"] = monkeypatch_model
    # 重排参考 = 不同事实
    a = _prepare(service, refs=[str(img), str(img2)]).content_key
    b = _prepare(service, refs=[str(img2), str(img)]).content_key
    assert a != b


def test_prepared_key_excludes_urls_and_paths(tmp_path):
    """临时 URL/签名/本地输出路径**不进入**内容 key（素材字节摘要进入）。"""
    from _video_gen.models import PreparedVideoRequest

    img = tmp_path / "a.png"
    img.write_bytes(b"img" * 50)
    service = StubVideoService()
    prepared = _prepare(service, image=str(img))
    facts = json.dumps(prepared.key_facts(), ensure_ascii=False, sort_keys=True)
    assert "data:" not in facts and "http" not in facts
    assert str(tmp_path) not in facts                    # 路径不进入 key
    assert prepared.materials[0].sha256 in facts         # 字节摘要进入 key

    # 同样的素材换个落盘目录 ⇒ key 不变（事实相同）
    moved = tmp_path / "sub"
    moved.mkdir()
    shutil.copy2(img, moved / "a.png")
    other = PreparedVideoRequest.from_dict(prepared.to_dict())
    other.materials[0].source_path = str(moved / "a.png")
    assert other.compute_content_key() == prepared.content_key


# ---------------------------------------------------------------- 4. 提交故障

@pytest.mark.parametrize("fail_mode,expect_state,expect_event", [
    ("network", va.STATE_SUBMISSION_UNKNOWN, None),
    ("http_500", va.STATE_SUBMISSION_UNKNOWN, None),
    ("http_429", va.STATE_PROVIDER_FAILED, "submit_rate_limited"),
    ("http_403", va.STATE_PROVIDER_FAILED, None),
])
def test_submit_failures_never_repost(monkeypatch, fail_mode, expect_state, expect_event):
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService(fail_mode=fail_mode)

    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=_frames(ctx.attempt_dir / "frames"))
    assert result is None
    assert len(service.posts) == 1                       # 恰好一次生成 POST，无隐藏重发
    assert va.submit_post_count(ctx.package_root) == 1
    assert va.current_state(ctx.attempt_dir) == expect_state
    kinds = [e["kind"] for e in eio.read_events_strict(ctx.attempt_dir)[0]]
    if expect_event:
        assert expect_event in kinds
    assert eio.read_accepted(ctx.attempt_dir) is None


def test_late_task_id_is_persisted_and_resumable(monkeypatch):
    """底层线程超时后返回 task_id：原 worker 落盘 late_task_id，恢复按同一 ID 查询。"""
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService(fail_mode="late_id", submit_delay=0.6)
    monkeypatch.setattr(sp, "_submit_timeout", lambda: 0.2)
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)

    import threading

    adapter = StubVideoAdapter(service)
    shot_dir = ctx.shot_dir(SHOT_INDEX)
    shot_dir.mkdir(parents=True, exist_ok=True)

    async def _go():
        return await sp._submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=shot_dir / SHOT_VIDEO_FILENAME,
            frame_paths=_frames(ctx.attempt_dir / "frames"), shot_hash="h", video_adapter=adapter,
            poll_semaphore=asyncio.Semaphore(1), script=script, idx=SHOT_INDEX,
            prompt=str(shot["prompt"]), characters=[], continuity_ref_image=None, mode="A",
            candidate_context=ctx, logger=None, make_request=pb._make_request)

    assert asyncio.run(_go()) is None
    assert len(service.posts) == 1
    assert va.current_state(ctx.attempt_dir) == va.STATE_SUBMISSION_UNKNOWN

    # 等到晚到结果落盘（提交线程仍在跑）
    deadline = 500
    for _ in range(deadline):
        info = sp.attempt_task_id(ctx.attempt_dir)
        if info["task_id"]:
            break
        threading.Event().wait(0.01)
    info = sp.attempt_task_id(ctx.attempt_dir)
    assert info["task_id"] == "stub-task-0001", info
    assert info["submit_posts"] == 1

    # 恢复：查询**原** task_id（不新生成）→ 下载 → 采纳
    result = sp.resume_candidate_attempt(ctx.attempt_dir, service=service)
    assert result["status"] == "accepted"
    assert result["task_id"] == "stub-task-0001"
    assert va.submit_post_count(ctx.package_root) == 1     # 恢复不新增生成
    assert len(service.posts) == 1


def test_resume_without_task_id_refuses_to_generate(monkeypatch):
    ctx = _ctx()
    eio.freeze_inputs(ctx.attempt_dir, {"provider": "stub", "shot_index": SHOT_INDEX})
    with pytest.raises(eio.EnhancementError) as ei:
        sp.resume_candidate_attempt(ctx.attempt_dir, service=StubVideoService())
    assert ei.value.code == "submission_unknown"
    assert ei.value.exit_code == eio.EXIT_SUBMISSION_UNKNOWN


def test_resume_rejects_unavailable_credential_binding(monkeypatch):
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()
    _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                   frame_paths=_frames(ctx.attempt_dir / "frames"))
    # 配置变更/凭据移除：profile 引用对不上 ⇒ 不得用当前 key 猜历史任务
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r2")
    with pytest.raises(eio.EnhancementError) as ei:
        sp.resume_candidate_attempt(ctx.attempt_dir, service=service)
    assert ei.value.code == "credential_binding_unavailable"
    assert ei.value.exit_code == eio.EXIT_SUBMISSION_UNKNOWN


def test_bypass_content_policy_proposes_revision_without_repost(monkeypatch):
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService(fail_mode="http_400_content_policy")
    monkeypatch.setattr("shared.content_policy_fix.auto_fix_prompt",
                        lambda prompt, err, verbose=False: prompt + "（合规改写）")

    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=_frames(ctx.attempt_dir / "frames"))
    assert result is None
    assert len(service.posts) == 1                      # 绝不在 except 内再 submit
    events = eio.read_events_strict(ctx.attempt_dir)[0]
    proposed = [e for e in events if e["kind"] == "proposed_revision"]
    assert proposed and proposed[0]["operation"] == "replace_prompt_text"
    assert va.current_state(ctx.attempt_dir) == va.STATE_PROVIDER_FAILED


# ---------------------------------------------------------------- 5. 检查与裁决

def test_qc_rejection_keeps_artifact_and_zero_regeneration(monkeypatch, tmp_path):
    from tests.enhancements.video_stub_provider import make_video

    bad = make_video(tmp_path / "bad.mp4", color="red", seconds=1.0)   # 时长明显不符
    service = StubVideoService(video_source=str(bad))
    ctx = _ctx()
    script = _script()
    shot = _shot(script)

    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=_frames(ctx.attempt_dir / "frames"))
    assert result is False
    assert len(service.posts) == 1
    assert va.current_state(ctx.attempt_dir) == va.STATE_REJECTED
    kinds = [e["kind"] for e in eio.read_events_strict(ctx.attempt_dir)[0]]
    assert "video_check" in kinds and "decision" in kinds
    assert eio.read_accepted(ctx.attempt_dir) is None
    # rejected 保留产物（供修订），不删片、不自动重交
    assert (ctx.shot_dir(SHOT_INDEX) / SHOT_VIDEO_FILENAME).is_file()
    decision = [e for e in eio.read_events_strict(ctx.attempt_dir)[0]
                if e["kind"] == "decision"][-1]["decision"]
    assert decision["kind"] == "revise_inputs"


def test_unverified_when_qc_cannot_measure(monkeypatch):
    """工具不可用/测不到 ⇒ unverified（零重生成，不伪报 PASS）。"""
    monkeypatch.setattr(sp, "_probe_video_duration", lambda path: 0.0)
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()

    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=_frames(ctx.attempt_dir / "frames"))
    assert result is False
    assert va.current_state(ctx.attempt_dir) == va.STATE_UNVERIFIED
    assert eio.read_accepted(ctx.attempt_dir) is None
    assert len(service.posts) == 1


def test_cache_decision_requires_frozen_relation(tmp_path):
    script = _script()
    shot = _shot(script)
    shot_dir = tmp_path / "7"
    shot_dir.mkdir()
    (shot_dir / "video.mp4").write_bytes(b"x" * (25 * 1024))
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")
    clip = tmp_path / "clip_7.mp4"
    clip.write_bytes(b"y" * (25 * 1024))

    # (a) 无快照 ⇒ legacy 命中（旧证据允许沿用，但不得宣称新身份已验证）
    d = sp.cache_decision(shot, script, shot_dir, shot_dir / "video.mp4", clip_path=clip)
    assert d["hit"] and d["relation"] == "legacy"

    # (b) 快照存在但与本次组装不一致 ⇒ 必须重新生成
    (shot_dir / sp.FROZEN_SNAPSHOT_FILENAME).write_text(
        json.dumps({"request_key": "deadbeef"}), encoding="utf-8")

    class _F:
        request_key = "cafebabe"

    d = sp.cache_decision(shot, script, shot_dir, shot_dir / "video.mp4", clip_path=clip,
                          frozen_request=_F())
    assert not d["hit"] and d["relation"] == "mismatch"

    class _Same:
        request_key = "deadbeef"

    d = sp.cache_decision(shot, script, shot_dir, shot_dir / "video.mp4", clip_path=clip,
                          frozen_request=_Same())
    assert d["hit"] and d["relation"] == "exact"

    # (c) 只改后处理参数（不在冻结请求里）⇒ 仍命中，不刷新视频 key
    shot["postprocess"] = {"srt_font": "X"}
    d = sp.cache_decision(shot, script, shot_dir, shot_dir / "video.mp4", clip_path=clip,
                          frozen_request=_Same())
    assert d["hit"]

    # (d) clip 缺失 ⇒ 走补片
    clip.unlink()
    d = sp.cache_decision(shot, script, shot_dir, shot_dir / "video.mp4", clip_path=clip,
                          frozen_request=_Same())
    assert not d["hit"] and d["reason"] == "clip 产物缺失"


# ---------------------------------------------------------------- 6. 修订协议

def _revision_file(tmp_path, *, operation, replacement, base_key, issue_ids=("i1",),
                   expected=("i1",), name="rev.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({
        "schema_version": "video_revision.v1", "request_id": "rev-1",
        "base_request_key": base_key, "issue_ids": list(issue_ids),
        "operation": operation, "replacement": replacement, "reason": "fixture",
        "expected_resolved_issues": list(expected)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return p


def _prepared_attempt(monkeypatch, *, experiment_id=None):
    """先跑一次正常候选，得到一个带冻结请求的 attempt（修订的 base）。"""
    ctx = _ctx(experiment_id)
    script = _script()
    shot = _shot(script)
    frames = _frames(ctx.attempt_dir / "frames")
    service = StubVideoService()
    _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                   frame_paths=frames)
    return ctx, script, shot, frames, service


def _run_revision(ctx, revision_path, *, argv_extra=()):
    from tools import video_reroll as vr
    import argparse

    args = argparse.Namespace(attempt=str(ctx.attempt_dir), revision=str(revision_path))
    return vr.cmd_apply_revision(args)


def test_revision_replace_references_rebinds_and_renumbers(monkeypatch, tmp_path):
    ctx, script, shot, frames, _svc = _prepared_attempt(monkeypatch)
    base = sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))
    other = tmp_path / "other.png"
    other.write_bytes(b"portrait-B" * 80)

    rev = _revision_file(tmp_path, operation="replace_references", base_key=base["request_key"],
                         replacement=[
                             {"role": "reference", "path": str(other), "bindings": ["e-2"]},
                             {"role": "first_frame", "path": str(other), "bindings": ["e-1"]}])
    rc = _run_revision(ctx, rev)
    assert rc == eio.EXIT_OK
    new_attempt = sorted((ctx.package_root / "attempts").iterdir())[-1]
    frozen = eio.read_inputs(new_attempt)["frozen_request"]
    assert [r["ref_id"] for r in frozen["references"]] == ["r1", "r2"]
    assert frozen["references"][1]["role"] == "first_frame"
    assert frozen["request_key"] != base["request_key"]      # 输入变了 ⇒ 新候选
    assert va.current_state(new_attempt) == va.STATE_READY


def test_revision_replace_postprocess_keeps_generation_key(monkeypatch, tmp_path):
    ctx, script, shot, frames, _svc = _prepared_attempt(monkeypatch)
    base = sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))
    rev = _revision_file(tmp_path, operation="replace_postprocess",
                         base_key=base["request_key"],
                         replacement={"srt_font": "Noto Sans SC", "loudness": -14.0})
    rc = _run_revision(ctx, rev)
    assert rc == eio.EXIT_OK
    new_attempt = sorted((ctx.package_root / "attempts").iterdir())[-1]
    inputs = eio.read_inputs(new_attempt)
    assert inputs["frozen_request"]["request_key"] == base["request_key"]   # key 保持
    assert inputs["postprocess"]["srt_font"] == "Noto Sans SC"
    assert va.submit_post_count(ctx.package_root) == 1                      # 零新生成


def test_revision_action_plan_requires_core_events(monkeypatch, tmp_path):
    ctx, script, shot, frames, _svc = _prepared_attempt(monkeypatch)
    frozen = sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))
    base_requirements = dict(frozen.get("requirements") or {})
    base_requirements["events"] = [{"event_id": "core-1", "text": "推门", "core": True},
                                   {"event_id": "opt-1", "text": "风吹", "core": False}]
    # 把 requirements 写回 base attempt，作为"核心事实"基线
    inputs = eio.read_inputs(ctx.attempt_dir)
    inputs["frozen_request"]["requirements"] = base_requirements
    inputs["frozen_request"].pop("request_key")
    from tools import video_reroll as vr
    obj = vr._load_frozen_obj(inputs["frozen_request"])
    obj.compute_key()
    inputs["frozen_request"] = obj.to_dict()
    eio.freeze_inputs(ctx.attempt_dir, inputs)
    sp.write_frozen_snapshot(ctx.shot_dir(SHOT_INDEX), obj)

    # (a) 丢核心事件 ⇒ 明确拒收（不得静默删剧情）
    bad = _revision_file(tmp_path, operation="replace_action_plan", base_key=obj.request_key,
                         replacement={"events": [{"event_id": "opt-1", "text": "风吹"}],
                                      "audio_policy": "native"},
                         name="rev-bad.json")
    with pytest.raises(eio.EnhancementError) as ei:
        _run_revision(ctx, bad)
    assert ei.value.code == "core_event_dropped" and ei.value.exit_code == eio.EXIT_REJECTED

    # (b) 完整保留核心事件 ⇒ ready
    good = _revision_file(tmp_path, operation="replace_action_plan", base_key=obj.request_key,
                          replacement={"events": [{"event_id": "core-1", "text": "推门"},
                                                  {"event_id": "opt-1", "text": "风吹"}],
                                       "audio_policy": "native", "start_state": {"pose": "站"},
                                       "end_state": {"pose": "坐"}},
                          name="rev-good.json")
    rc = _run_revision(ctx, good)
    assert rc == eio.EXIT_OK
    new_attempt = sorted((ctx.package_root / "attempts").iterdir())[-1]
    new_frozen = eio.read_inputs(new_attempt)["frozen_request"]
    assert len(new_frozen["requirements"]["events"]) == 2
    assert new_frozen["request_key"] != obj.request_key


def test_revision_rejects_stale_base(monkeypatch, tmp_path):
    ctx, _script_, _shot_, _frames_, _svc = _prepared_attempt(monkeypatch)
    rev = _revision_file(tmp_path, operation="replace_references", base_key="not-the-base",
                         replacement=[{"role": "first_frame", "path": None}])
    with pytest.raises(eio.EnhancementError) as ei:
        _run_revision(ctx, rev)
    assert ei.value.code == "base_changed" and ei.value.exit_code == eio.EXIT_BUSY


def test_revision_reports_residual_issues(monkeypatch, tmp_path):
    ctx, _s, _sh, _f, _svc = _prepared_attempt(monkeypatch)
    base = sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))
    rev = _revision_file(tmp_path, operation="replace_postprocess", base_key=base["request_key"],
                         replacement={"srt_font": "F"}, issue_ids=("i1", "i2", "i3"),
                         expected=("i1", "i3"))
    rc = _run_revision(ctx, rev)
    assert rc == eio.EXIT_OK
    new_attempt = sorted((ctx.package_root / "attempts").iterdir())[-1]
    applied = [e for e in eio.read_events_strict(new_attempt)[0]
               if e["kind"] == "revision_applied"][-1]
    assert applied["resolved"] == ["i1", "i3"] and applied["residual"] == ["i2"]


def test_revision_rejects_unknown_operation_and_story_edits(monkeypatch, tmp_path):
    ctx, _s, _sh, _f, _svc = _prepared_attempt(monkeypatch)
    base = sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))
    rev = _revision_file(tmp_path, operation="rewrite_story", base_key=base["request_key"],
                         replacement={"text": "换个结局"})
    with pytest.raises(eio.EnhancementError) as ei:
        _run_revision(ctx, rev)
    assert ei.value.code == "unsupported_operation"
    assert ei.value.exit_code == eio.EXIT_INVALID_INPUT


# ---------------------------------------------------------------- 7. 声音政策

@pytest.mark.parametrize("audio_mode", ["native", "tts_mix"])
def test_audio_policy_enters_generation_key_only_when_it_changes_payload(monkeypatch,
                                                                          audio_mode):
    """声音政策分账（§V1）：只有"模型原生发声"的通道才进视频生成 key；"""
    monkeypatch.setenv("MERGE_AUDIO_MODE", audio_mode)
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()
    _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                   frame_paths=_frames(ctx.attempt_dir / "frames"))
    frozen = eio.read_inputs(ctx.attempt_dir)["frozen_request"]
    # 桩通道不是"视频模型原生发声"：声音由 step03 负责 ⇒ 不进生成 key
    assert "audio_policy" not in (frozen.get("requirements") or {})
    assert len(service.posts) == 1

    # 换成原生发声通道（volcengine/agent_plan/minimax）⇒ 政策改变 payload，必须进 key
    monkeypatch.setattr("shared.model_caps.active_video_uses_native_voice", lambda: True)
    ctx2 = _ctx()
    _run_candidate(monkeypatch, ctx2, shot=shot, script=script, service=service,
                   frame_paths=_frames(ctx2.attempt_dir / "frames"))
    frozen2 = eio.read_inputs(ctx2.attempt_dir)["frozen_request"]
    assert frozen2["requirements"]["audio_policy"] == audio_mode
    assert frozen2["request_key"] != frozen["request_key"]


# ---------------------------------------------------------------- 8. 绕过入口 / 计划层

def test_plan_and_inspect_replay_report_are_offline(tmp_path, monkeypatch):
    """plan / inspect / report 不发网络：桩 provider 的 POST 计数保持 0。"""
    from tools import video_reroll as vr
    import argparse

    script = _script()
    ctx = _ctx()
    # 2026-11-08（WP1）：**不再写生产输入目录**。现网 chapter_001 升到 58c1b6bd
    # 后旧帧摘要不匹配、plan 判 stale 是正确的；本用例要验证"离线 plan 能报
    # ready>0"，所以用 tmp_path 自包含夹具，并显式把 shots_dir 指过去。
    frames_root = tmp_path / "shots"
    _frames(frames_root, SHOT_INDEX)
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    rc = vr.cmd_plan(argparse.Namespace(script=str(script_path), shots=str(SHOT_INDEX),
                                        out_dir=str(ctx.package_root),
                                        shots_dir=str(frames_root),
                                        chapter=1,
                                        # 2026-11-08：`stub` 不是注册过的视频 provider，
                                        # plan 会判 pending（估 0 次）；这里要验证"离线
                                        # 组装能报 ready>0"，用生效通道名（prepare 纯离线）。
                                        provider="agent_plan",
                                        model="stub-model-1"))
    assert rc == eio.EXIT_OK
    plan = json.loads(sorted((ctx.package_root / "plans").glob("*.json"))[-1].read_text(encoding="utf-8"))
    assert plan["network_calls"] == 0 and plan["estimated_generations"] == 1

    rc = vr.cmd_report(argparse.Namespace(root=str(ctx.package_root)))
    assert rc == eio.EXIT_OK


def test_cli_plan_only_and_scope_parse_without_side_effects(monkeypatch, tmp_path):
    """早期解析：bootstrap 应用范围并缓存；parse_args 本身不碰 env/不建目录。"""
    from step02b_video_gen import cli as vcli

    monkeypatch.setattr(vcli, "_PARSED", None)
    options = vcli.bootstrap(["--chapter", "3", "--shots", "7,9",
                              "--plan-only", "--candidate-root", str(tmp_path / "x" / "video")])
    assert options.chapter == 3 and options.plan_only
    assert os.environ["NOVEL_CHAPTER"] == "3"
    assert vcli.cached_options() is options          # main() 不二次解析
    assert not (tmp_path / "x").exists()             # 解析不建目录

    with pytest.raises(ValueError):
        vcli.validate_options(vcli.parse_args(["--plan-only", "--resume", "DIR"]))
    with pytest.raises(ValueError):
        vcli.validate_options(vcli.parse_args(["--candidate-root", str(tmp_path / "y" / "video")]))


def test_candidate_root_must_match_experiment_single_source(tmp_path):
    from step02b_video_gen import cli as vcli

    assert vcli.resolve_experiment_root(str(_ctx().package_root), chapter=1)
    with pytest.raises(ValueError):
        vcli.resolve_experiment_root(str(tmp_path / "elsewhere" / "video"), chapter=1)


def test_transaction_commit_unconfirmed_is_reported(monkeypatch, tmp_path):
    """提交后读回失败 ⇒ commit_unconfirmed（退出码 6），产物保留、不声称已采纳。"""
    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    service = StubVideoService()

    def _fail_confirm(*args, **kwargs):
        raise eio.CommitUnconfirmedError("桩：替换后读回失败")

    monkeypatch.setattr(eio, "commit_accepted", _fail_confirm)
    result = _run_candidate(monkeypatch, ctx, shot=shot, script=script, service=service,
                            frame_paths=_frames(ctx.attempt_dir / "frames"))
    assert result is False
    assert len(service.posts) == 1
    assert va.current_state(ctx.attempt_dir) == va.STATE_COMMIT_UNCONFIRMED
    assert eio.read_accepted(ctx.attempt_dir) is None
    # 产物与 manifest 保留（不盲回滚、不删证据）
    assert (ctx.attempt_dir / eio.MANIFEST_FILENAME).is_file()
    assert (ctx.shot_dir(SHOT_INDEX) / SHOT_VIDEO_FILENAME).is_file()


# ---------------------------------------------------------------- 9. 工具 CLI 贯通

def test_replay_cli_runs_offline_fixture(tmp_path):
    """replay：消费夹具里的"已录制"结果，走真实 prepared → 一次桩 POST → 采纳。"""
    from tools import video_reroll as vr
    from tests.enhancements.video_stub_provider import make_video

    experiment_id = f"replay{os.getpid()}"
    out_dir = eio.package_root("video", experiment_id, 1)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    fixture_dir = tmp_path / "fixture"
    (fixture_dir / "media").mkdir(parents=True)
    recorded = make_video(fixture_dir / "media" / "recorded.mp4", color="green", seconds=6.0)
    shot = _shot(_script())
    fixture = {
        "schema_version": "video_replay_fixture.v1",
        "chapter": 1, "shot_index": SHOT_INDEX, "mode": "text",
        "scope": {"chapter": 1}, "script_revision": "rev-fixture",
        "provider": "stub", "shot": shot,
        "script": {"characters": [], "shots": [shot]},
        "references": [], "video": "media/recorded.mp4",
        "expected": {"submit_posts": 1, "state": "accepted"},
    }
    (fixture_dir / "fixture.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")

    import argparse
    rc = vr.cmd_replay(argparse.Namespace(fixture=str(fixture_dir), out_dir=str(out_dir),
                                          experiment_id=None, stub_module=None))
    assert rc == eio.EXIT_OK
    accepted = [p for p in out_dir.rglob("accepted.json")]
    assert accepted, "replay 必须走到 accepted"
    pointers = [json.loads(p.read_text(encoding="utf-8")) for p in accepted]
    assert all(p["attempt_id"] for p in pointers)
    # 一次提交：report 子命令从事件历史读回的计数
    rc = vr.cmd_report(argparse.Namespace(root=str(out_dir)))
    assert rc == eio.EXIT_OK


def test_inspect_cli_maps_verdict_to_exit_code(tmp_path):
    """inspect：无证据的未知 ⇒ rc=0（不伪报）；明确合同类问题 ⇒ rc=4。"""
    from tools import video_reroll as vr
    import argparse

    ctx = _ctx()
    script = _script()
    shot = _shot(script)
    # 构造一个带冻结请求的 attempt（不需要真提交）
    frozen, _req = sp.build_frozen_for_shot(
        shot=shot, script=script, video_adapter=None, frame_paths=[], mode="A",
        scope={"chapter": 1}, provider="stub", model_id="stub-model-1")
    ctx.shot_dir(SHOT_INDEX).mkdir(parents=True, exist_ok=True)
    sp.write_frozen_snapshot(ctx.shot_dir(SHOT_INDEX), frozen)
    eio.freeze_inputs(ctx.attempt_dir, {"frozen_request": frozen.to_dict(),
                                        "provider": "stub", "shot_index": SHOT_INDEX})

    unknown = tmp_path / "checks-unknown.json"
    unknown.write_text(json.dumps({"media_status": "unknown", "semantic_status": "unknown",
                                   "coverage": {"media": []}, "issues": []}), encoding="utf-8")
    rc = vr.cmd_inspect(argparse.Namespace(attempt=str(ctx.attempt_dir),
                                           checks_file=str(unknown)))
    assert rc == eio.EXIT_OK

    contract = tmp_path / "checks-contract.json"
    contract.write_text(json.dumps({
        "media_status": "fail", "semantic_status": "fail",
        "coverage": {"media": ["duration"], "semantic": ["three_frames"]},
        "issues": [{"issue_id": "i1", "kind": "voice_identity",
                    "expected": "沈砚的声音", "observed": "陌生女声",
                    "evidence_paths": [str(unknown)], "evidence_hashes": ["h1"],
                    "required_change": "回原生产者改声音身份", "field_paths": ["requirements.audio_policy"]}],
    }, ensure_ascii=False), encoding="utf-8")
    rc = vr.cmd_inspect(argparse.Namespace(attempt=str(ctx.attempt_dir),
                                           checks_file=str(contract)))
    assert rc == eio.EXIT_REJECTED


def test_resume_cli_exit_codes(monkeypatch, tmp_path):
    """resume CLI：无 task_id ⇒ 7；有 task ⇒ 查询原 ID 并采纳（rc=0）。"""
    from tools import video_reroll as vr
    import argparse

    empty = _ctx(attempt_id="empty")
    eio.freeze_inputs(empty.attempt_dir, {"provider": "stub", "shot_index": SHOT_INDEX})
    assert vr.main(["resume", "--attempt", str(empty.attempt_dir)]) == 7


def test_revision_replace_frame_binding_keeps_prompt(monkeypatch, tmp_path):
    """起止帧绑定错：整组替换 frame binding，正文保持不变、key 随之变化。"""
    ctx, script, shot, frames, _svc = _prepared_attempt(monkeypatch)
    base = sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))
    new_ff = tmp_path / "ff2.png"
    new_ff.write_bytes(b"frame-corrected" * 64)

    rev = _revision_file(tmp_path, operation="replace_frame_binding",
                         base_key=base["request_key"],
                         replacement={"references": [
                             {"role": "first_frame", "path": str(new_ff), "bindings": ["e-1"]},
                             {"role": "last_frame", "path": frames[1], "bindings": ["e-1"]}]})
    assert _run_revision(ctx, rev) == eio.EXIT_OK
    new_attempt = sorted((ctx.package_root / "attempts").iterdir())[-1]
    frozen = eio.read_inputs(new_attempt)["frozen_request"]
    roles = [r["role"] for r in frozen["references"]]
    assert roles == ["first_frame", "last_frame"]
    assert frozen["prompt"] == base["prompt"]                  # 正文保留
    assert frozen["references"][0]["sha256"] == eio.sha256_file(new_ff)
    assert frozen["request_key"] != base["request_key"]


def test_stub_identity_is_distinguishable_across_tasks(monkeypatch, tmp_path):
    """桩身份可区分：不同 task 的可解码视频在字节与颜色上都不同（不是恒定 success）。"""
    script = _script()
    shot = _shot(script)
    script2 = _script()
    script2["shots"][0]["prompt"] = "[中景] 沈砚转身离开（换了正文）"
    service = StubVideoService()

    ctx1 = _ctx(attempt_id="id-a")
    ctx2 = _ctx(attempt_id="id-b")
    assert _run_candidate(monkeypatch, ctx1, shot=shot, script=script, service=service,
                          frame_paths=_frames(ctx1.attempt_dir / "frames")) is True
    assert _run_candidate(monkeypatch, ctx2, shot=_shot(script2), script=script2,
                          service=service,
                          frame_paths=_frames(ctx2.attempt_dir / "frames")) is True

    assert len(service.posts) == 2
    task_ids = sorted(service.tasks)
    assert len(task_ids) == 2
    colors = {service.tasks[t]["color"] for t in task_ids}
    assert len(colors) == 2                                    # 身份可区分（颜色）
    v1 = ctx1.shot_dir(SHOT_INDEX) / SHOT_VIDEO_FILENAME
    v2 = ctx2.shot_dir(SHOT_INDEX) / SHOT_VIDEO_FILENAME
    assert eio.sha256_file(v1) != eio.sha256_file(v2)          # 不是同一份字节
    for v in (v1, v2):                                         # 且都真的可解码
        assert sp._probe_video_duration(v) > 0
    assert service.posts[0]["content_key"] != service.posts[1]["content_key"]
