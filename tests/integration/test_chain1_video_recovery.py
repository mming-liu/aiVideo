# -*- coding: utf-8 -*-
"""I2 链 1：视频 prepared → **一次 POST** → 保存 task → 注入故障 → 同 task 恢复 → QC → accepted.

判据（计划 §I2 链 1 / §I3「视频恢复」行）：
* 每次尝试**至多一个**生成 POST；计的是**最底层** POST（桩的 create_prepared_task），
  不是 mock adapter；
* 恢复必须**沿用原 task_id**、不得新增 POST；没有可恢复 ID 时**拒绝隐式 create**；
* 采纳走真实事务（不可变 manifest + accepted 指针读回），QC 是结构化的。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from shared import enhancement_io as eio
from shared import video_attempts as va
from shared.paths import enhancement_package_dir
from tests.enhancements.video_stub_provider import make_video
from tests.integration import chain_support as cs
from tests.integration.video_chain_stub import STORE_ENV, FileBackedStubVideoService

pytestmark = pytest.mark.skipif(cs.FFMPEG is None, reason="需要 ffmpeg（桩 provider 现造可解码素材）")

VIDEO_REROLL = cs.REPO / "tools" / "video_reroll.py"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-i-chain")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    yield


def _accepted_readback(attempt: Path) -> dict:
    """采纳读回：指针 + 不可变 manifest 的关系（不看内存状态）."""
    pointer = eio.read_accepted(attempt)
    assert pointer, "accepted 指针缺失"
    manifest = json.loads((attempt / eio.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return {"pointer": pointer, "manifest": manifest}


# --------------------------------------------------------------------- 1a 正常链

def test_chain1_cli_replay_one_post_and_accepted_readback(monkeypatch, tmp_path):
    """入口 = tools/video_reroll.py replay（§11 命令行合同），全程离线."""
    exp = cs.new_experiment_id("i1replay")
    fixture_dir = tmp_path / "fixture"
    (fixture_dir / "media").mkdir(parents=True)
    make_video(fixture_dir / "media" / "recorded.mp4", color="blue", seconds=cs.DURATION)
    doc = cs.script()
    (fixture_dir / "fixture.json").write_text(json.dumps({
        "schema_version": "video_replay_fixture.v1", "chapter": 1,
        "shot_index": cs.SHOT_INDEX, "mode": "text", "provider": "stub",
        "script": doc, "shot": cs.shot_of(doc), "references": [],
        "video": "media/recorded.mp4", "script_revision": "rev-i-chain",
        "expected": {"submit_posts": 1, "state": "accepted"},
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("REPLAY_ATTEMPT_ID", "a-replay-1")
    from tools import video_reroll as vr
    out_dir = enhancement_package_dir("video", exp, 1)
    rc = vr.main(["replay", "--fixture", str(fixture_dir), "--out-dir", str(out_dir)])

    assert rc == eio.EXIT_OK
    assert va.submit_post_count(out_dir) == 1, "最底层 POST 计数必须恒为 1"
    attempt = out_dir / "attempts" / "a-replay-1"
    read = _accepted_readback(attempt)
    assert read["manifest"]["status"] == "validated"
    assert read["manifest"]["checks"], "结构化 QC 结果必须进 manifest"
    assert va.current_state(attempt) == "accepted"
    events = [e["kind"] for e in eio.read_events_strict(attempt)[0]]
    assert "attempt_reuse_rejected" not in events


def test_chain1_reroll_fixture_assertion_is_discriminating(monkeypatch, tmp_path):
    """反例：期望值与实际 POST 数不符时 replay 必须报错（证明该计数真的在判）."""
    exp = cs.new_experiment_id("i1neg")
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    doc = cs.script()
    (fixture_dir / "fixture.json").write_text(json.dumps({
        "chapter": 1, "shot_index": cs.SHOT_INDEX, "mode": "text", "provider": "stub",
        "script": doc, "shot": cs.shot_of(doc), "references": [],
        "script_revision": "rev-i-neg", "expected": {"submit_posts": 3},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("REPLAY_ATTEMPT_ID", "a-replay-neg")
    from tools import video_reroll as vr
    out_dir = enhancement_package_dir("video", exp, 1)
    rc = vr.main(["replay", "--fixture", str(fixture_dir), "--out-dir", str(out_dir)])
    assert rc == eio.EXIT_REJECTED
    assert va.submit_post_count(out_dir) == 1


# ------------------------------------------------------- 1b 下载故障 → 同 task 恢复

def test_chain1_download_fault_then_same_task_recovery(monkeypatch):
    """注入下载故障 → 同 task 恢复 → accepted；全程只发生一次 POST."""
    from step02_video_gen import submit_poll as sp

    exp = cs.new_experiment_id("i1dl")
    doc = cs.script()
    ctx = cs.video_ctx(exp, attempt_id="a-dl-1")
    frames = cs.frames(ctx.attempt_dir / "frames")
    service = FileBackedStubVideoService(fail_mode="download_fail")

    out = cs.run_candidate(monkeypatch, ctx, doc=doc, service=service, frame_paths=frames)
    assert out is not None, "提交阶段不该被拒"
    assert len(service.posts) == 1, "故障前必须已经真实 POST 过一次"
    assert eio.read_accepted(ctx.attempt_dir) is None, "下载失败不得采纳"
    info = sp.attempt_task_id(ctx.attempt_dir)
    assert info["task_id"], "失败也必须留下可恢复的 task_id"

    service.fail_mode = "none"          # 传输恢复（远端任务原样还在）
    resumed = sp.resume_candidate_attempt(ctx.attempt_dir, service=service)
    assert resumed["status"] == "accepted", resumed
    assert resumed["task_id"] == info["task_id"], "必须沿用同一个 task"
    assert len(service.posts) == 1, "恢复不得新增 POST"
    assert va.submit_post_count(ctx.package_root) == 1

    child = Path(resumed["attempt_dir"])
    read = _accepted_readback(child)
    assert read["manifest"]["checks"]
    assert va.current_state(child) == "accepted"
    assert child.name != ctx.attempt_dir.name, "恢复应开新的 attempt（原尝试保留证据）"


# ------------------------------------- 1c 重启（新进程/新实例）+ 晚到 task_id 恢复

def test_chain1_restart_with_late_task_id_only_one_post(monkeypatch, tmp_path):
    """晚到 task_id 落盘 → 本机重启（远端存储重连、本地内存全丢）→ 恢复成活."""
    from step02_video_gen import submit_poll as sp

    store = tmp_path / "remote-store.json"
    monkeypatch.setenv(STORE_ENV, str(store))
    monkeypatch.setattr(sp, "_submit_timeout", lambda: 0.2)

    exp = cs.new_experiment_id("i1late")
    doc = cs.script()
    ctx = cs.video_ctx(exp, attempt_id="a-late-1")
    frames = cs.frames(ctx.attempt_dir / "frames")
    # 提交在 0.2s 内不返回 ⇒ unknown，但 POST 已经发出、远端任务随后建好
    service = FileBackedStubVideoService(fail_mode="none", submit_delay=1.2)

    cs.run_candidate(monkeypatch, ctx, doc=doc, service=service, frame_paths=frames)
    assert len(service.posts) == 1

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not sp.attempt_task_id(ctx.attempt_dir)["task_id"]:
        time.sleep(0.2)                       # 等提交线程把晚到 task_id 落盘
    info = sp.attempt_task_id(ctx.attempt_dir)
    assert info["task_id"], "晚到 task_id 必须落盘（否则恢复无从谈起）"
    assert info["submit_posts"] == 1

    # ---- 本机重启：新实例、只从远端存储重载，本地内存里的 service 全部丢弃 ----
    restarted = FileBackedStubVideoService()
    assert restarted.tasks, "重启后必须仍能看见远端任务"
    assert len(restarted.posts) == 1, "重启不得把 POST 计数清零"

    resumed = sp.resume_candidate_attempt(ctx.attempt_dir, service=restarted)
    assert resumed["status"] == "accepted", resumed
    assert resumed["task_id"] == info["task_id"]
    assert len(restarted.posts) == 1, "恢复路径不得新增 POST"
    assert va.submit_post_count(ctx.package_root) == 1
    assert va.current_state(Path(resumed["attempt_dir"])) == "accepted"


# ------------------------------------------------ 1d unknown 不得隐式新生成

def test_chain1_unknown_submission_refuses_to_create(monkeypatch):
    """连接断开（不知道有没有建任务）⇒ 恢复必须拒收，且**不得**借机新生成."""
    from step02_video_gen import submit_poll as sp

    exp = cs.new_experiment_id("i1unk")
    doc = cs.script()
    ctx = cs.video_ctx(exp, attempt_id="a-unk-1")
    frames = cs.frames(ctx.attempt_dir / "frames")
    service = FileBackedStubVideoService(fail_mode="network")

    cs.run_candidate(monkeypatch, ctx, doc=doc, service=service, frame_paths=frames)
    assert len(service.posts) == 1
    info = sp.attempt_task_id(ctx.attempt_dir)
    assert info["task_id"] is None
    assert info["state"] == "submission_unknown"
    assert eio.read_accepted(ctx.attempt_dir) is None

    with pytest.raises(eio.EnhancementError) as exc:
        sp.resume_candidate_attempt(ctx.attempt_dir, service=service)
    assert exc.value.code == "submission_unknown"
    assert exc.value.exit_code == eio.EXIT_SUBMISSION_UNKNOWN
    assert len(service.posts) == 1, "拒收路径一个 POST 都不许新增"
    assert va.submit_post_count(ctx.package_root) == 1


# -------------------------------------------- 1e 终局 attempt 不得被覆盖复用

def test_chain1_terminal_attempt_rejects_reuse(monkeypatch):
    """同一 attempt 已终局后再提交必须被拒（防止覆盖既有采纳证据）."""
    exp = cs.new_experiment_id("i1reuse")
    doc = cs.script()
    ctx = cs.video_ctx(exp, attempt_id="a-reuse-1")
    frames = cs.frames(ctx.attempt_dir / "frames")
    service = FileBackedStubVideoService()
    cs.run_candidate(monkeypatch, ctx, doc=doc, service=service, frame_paths=frames)
    assert va.current_state(ctx.attempt_dir) == "accepted"

    service2 = FileBackedStubVideoService()
    out = cs.run_candidate(monkeypatch, ctx, doc=doc, service=service2, frame_paths=frames)
    assert out is None, "终局 attempt 复用必须被拒（返回 None）"
    assert len(service2.posts) == 0, "被拒的复用不得产生 POST"
    events = eio.read_events_strict(ctx.attempt_dir)[0]
    assert any(e.get("kind") == "attempt_reuse_rejected" for e in events)
    assert any(e.get("code") == "attempt_identity_conflict" for e in events)
