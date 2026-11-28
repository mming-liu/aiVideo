# -*- coding: utf-8 -*-
"""Step02b 候选入口接线与早期解析（计划 §3 表 step02b 行 / §15「bootstrap 与源码守卫」）.

覆盖：
* ``_process_one_shot_skip_frames``（Step02b 的**实际主入口**）在候选模式下
  把 shot_dir/video_path/哈希/快照全部落在 attempt 内，提交走 prepared 入口；
* 命中缓存必须核**冻结请求关系**（同输入才复用，输入变了要重新生成）；
* ``cli`` 的早期解析无副作用、非法参数组合显式退出码 2；
* 真实子进程：候选参数与 scope 一起解析（缺 step01 产物时报清楚、且不烧额度）。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio          # noqa: E402
from shared import video_attempts as va           # noqa: E402
from shared.paths import (  # noqa: E402
    SHOT_VIDEO_FILENAME, audio_dir, chapter_dir, clips_dir, vimax_shots_dir,
)
from step02_video_gen import submit_poll as sp    # noqa: E402
from tests.enhancements.video_stub_provider import StubVideoAdapter, StubVideoService  # noqa: E402

SHOT_INDEX = 5
_UID = {"n": 0}


def _script(duration: float = 6.0) -> dict:
    return {"characters": [], "shots": [
        {"index": SHOT_INDEX, "prompt": "[中景] 沈砚抬手", "ff_desc": "抬手",
         "lf_desc": "放下", "motion_desc": "0-3秒：抬手；3-6秒：放下",
         "subtitle": "抬手", "narration_segment": "", "keyframes": [],
         "duration_sec": duration}]}


def _ctx(attempt_id: str = "shot05-a") -> eio.CandidateContext:
    _UID["n"] += 1
    # 来源身份与真实入口一致（§F4）：采纳 manifest 必须答得出"哪一章、哪一稿"，
    # 缺了会被采纳出口拒收——夹具不再省掉这三个字段。
    ctx = eio.CandidateContext(experiment_id=f"sb{os.getpid()}{_UID['n']:03d}",
                               package="video", attempt_id=attempt_id, chapter=1,
                               scope={"chapter": 1}, script_revision="rev-test",
                               source="step02b_candidate")
    ctx.prepare()
    return ctx


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "text")     # 无需帧文件
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-sb")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    for name, value in (("INITIAL_POLL_DELAY", 0), ("POLL_BASE_INTERVAL", 0),
                        ("POLL_MAX_INTERVAL", 0), ("POLL_JITTER", 0)):
        monkeypatch.setattr(sp, name, value)
    sp._failed_videos.clear()
    yield


def _digest(root: Path) -> dict:
    root = Path(root)
    if not root.is_dir():
        return {}
    return {str(p): eio.sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _run_shot(ctx, service, script, *, force=False):
    from step02b_video_gen import main as m
    adapter = StubVideoAdapter(service)

    async def _go():
        task = await m._process_one_shot_skip_frames(
            script["shots"][0], vimax_shots_dir(), adapter, None, script,
            asyncio.Semaphore(1), force=force, candidate_context=ctx)
        if task is None:
            return None
        try:
            return await task
        except Exception as exc:  # noqa: BLE001
            return exc

    return asyncio.run(_go())


# ---------------------------------------------------------------- 候选接线

def test_skip_frames_entry_writes_candidate_inside_attempt():
    ctx = _ctx()
    script = _script()
    service = StubVideoService()
    clips_before = _digest(clips_dir())
    shots_before = _digest(vimax_shots_dir())

    assert _run_shot(ctx, service, script) is True
    assert len(service.posts) == 1
    # 产物落在 attempt 内
    candidate = ctx.shot_dir(SHOT_INDEX) / SHOT_VIDEO_FILENAME
    assert candidate.is_file() and candidate.stat().st_size > 20 * 1024
    assert (ctx.shot_dir(SHOT_INDEX) / "prompt_hash.txt").is_file()
    assert sp.read_frozen_snapshot(ctx.shot_dir(SHOT_INDEX))["request_key"]
    assert eio.read_accepted(ctx.attempt_dir)
    assert va.current_state(ctx.attempt_dir) == va.STATE_ACCEPTED
    # 正式产物一个字节没动；正式 shots 目录没被创建
    assert _digest(clips_dir()) == clips_before
    assert _digest(vimax_shots_dir()) == shots_before
    # 候选运行**不写**正式 shots/<idx>/video.mp4（目录可能因其它测试存在，判据是产物）
    assert not (vimax_shots_dir() / str(SHOT_INDEX) / SHOT_VIDEO_FILENAME).exists()
    # 日志也隔离在 attempt 内
    assert (ctx.log_dir / "step02b.log").is_file()


def test_cache_hit_skips_second_generation():
    ctx = _ctx()
    script = _script()
    service = StubVideoService()
    assert _run_shot(ctx, service, script) is True
    assert len(service.posts) == 1

    # 同输入再跑：冻结请求关系一致 ⇒ 直接复用，零新 POST
    assert _run_shot(ctx, service, script) is None
    assert len(service.posts) == 1

    # 输入变了（正文变化）⇒ 必须重新生成；新一轮用**新的 attempt_id**（§F2 不复用）
    script2 = _script()
    script2["shots"][0]["ff_desc"] = "改了首帧描述"
    ctx2 = _ctx(attempt_id="shot05-b")
    assert _run_shot(ctx2, service, script2) is not None
    assert len(service.posts) == 2
    assert (ctx2.attempt_dir / eio.ACCEPTED_FILENAME).is_file()

    # 复用一个**终局** attempt 重新生成 ⇒ 明确拒收，不覆盖既有证据
    assert _run_shot(ctx, service, script2) is None
    assert len(service.posts) == 2
    kinds = [e["kind"] for e in eio.read_events_strict(ctx.attempt_dir)[0]]
    assert "attempt_reuse_rejected" in kinds
    assert eio.read_accepted(ctx.attempt_dir)["attempt_id"] == ctx.attempt_id


def test_force_skips_cache_but_does_not_bypass_authorization():
    """force 在候选入口只跳过可复用缓存，不隐式允许额外 POST（新候选仍是一次）。"""
    ctx = _ctx()
    script = _script()
    service = StubVideoService()
    _run_shot(ctx, service, script)
    assert len(service.posts) == 1
    ctx2 = _ctx(attempt_id="shot05-force")
    assert _run_shot(ctx2, service, script, force=True) is not None
    assert len(service.posts) == 2          # 明确要求的新候选 ⇒ 恰好新增一次


# ---------------------------------------------------------------- 早期解析

def test_parse_is_side_effect_free(monkeypatch, tmp_path):
    from step02b_video_gen import cli as vcli

    monkeypatch.delenv("NOVEL_CHAPTER", raising=False)
    options = vcli.parse_args(["--chapter", "4", "--shots", "5", "--plan-only",
                               "--candidate-root", str(tmp_path / "e" / "video")])
    assert options.chapter == 4 and options.plan_only and options.shots == "5"
    assert "NOVEL_CHAPTER" not in os.environ        # 解析不写 env
    assert not (tmp_path / "e").exists()            # 解析不建目录


def test_bootstrap_applies_scope_before_config_reads(monkeypatch):
    from step02b_video_gen import cli as vcli

    monkeypatch.setattr(vcli, "_PARSED", None)
    options = vcli.bootstrap(["--chapter", "6", "--shots", "5"])
    assert os.environ["NOVEL_CHAPTER"] == "6"
    assert vcli.cached_options() is options
    from shared.paths import chapter_dir
    assert chapter_dir().name == "chapter_006"
    monkeypatch.setattr(vcli, "_PARSED", None)


@pytest.mark.parametrize("argv", [
    ["--plan-only", "--resume", "DIR"],
    ["--resume", "DIR", "--candidate-root", "ROOT/video"],
    ["--resume", "DIR", "--force"],
    ["--candidate-root", "ROOT/video"],                       # 未给 shots / --all-shots
])
def test_illegal_option_combinations_fail_explicitly(argv):
    from step02b_video_gen import cli as vcli

    with pytest.raises(ValueError):
        vcli.validate_options(vcli.parse_args(argv))


def test_candidate_root_must_be_single_source(tmp_path):
    from step02b_video_gen import cli as vcli

    ctx = _ctx()
    assert vcli.resolve_experiment_root(str(ctx.package_root), chapter=1) == ctx.experiment_id
    with pytest.raises(ValueError):
        vcli.resolve_experiment_root(str(tmp_path / "whatever" / "video"), chapter=1)


# ---------------------------------------------------------------- 真实子进程

def _run(args, tmp_output: Path, timeout: int = 240):
    env = dict(os.environ)
    env["OUTPUT_BASE"] = str(tmp_output)
    env["PYTHONIOENCODING"] = "utf-8"
    for key in ("NOVEL_CHAPTER", "BOOK_INDEX", "BOOK_NAME", "STEP01_LOG_PATH"):
        env.pop(key, None)
    return subprocess.run([sys.executable, *args], cwd=str(_ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def test_subprocess_candidate_flags_parse_early(tmp_path):
    """真实子进程：候选参数与 scope 一起解析；缺 step01 产物时 rc=2 且不烧额度。"""
    proc = _run(["-m", "step02b_video_gen.main", "--chapter", "2", "--shots", "5",
                 "--plan-only", "--candidate-root", str(tmp_path / "out" / "video")],
                tmp_path / "out")
    assert proc.returncode == 2
    merged = proc.stdout + proc.stderr
    assert "chapter_002" in merged and "step01_script_gen/main.py --chapter 2" in merged
    assert not (tmp_path / "out" / "video").exists()      # 解析阶段不建候选目录


def test_subprocess_illegal_combination_exits_2(tmp_path):
    proc = _run(["-m", "step02b_video_gen.main", "--plan-only", "--resume",
                 str(tmp_path / "attempt")], tmp_path / "out")
    assert proc.returncode == 2
    assert "--plan-only 与 --resume 互斥" in (proc.stdout + proc.stderr)


def test_subprocess_candidate_without_shots_is_refused(tmp_path):
    proc = _run(["-m", "step02b_video_gen.main", "--candidate-root",
                 str(tmp_path / "out" / "video")], tmp_path / "out")
    assert proc.returncode == 2
    assert "--shots" in (proc.stdout + proc.stderr)


def test_gen_all_videos_candidate_mode_isolates_every_shot(monkeypatch):
    """批次入口（gen_all_videos + candidate_factory）：每镜一个 attempt，
    产物/日志全隔离，正式 clips 与 shots 一个字节不变。"""
    from step02b_video_gen import main as m

    script = {"characters": [], "shots": [
        dict(_script()["shots"][0], index=5),
        dict(_script()["shots"][0], index=6, prompt="[中景] 沈砚转身")]}
    service = StubVideoService()
    adapter = StubVideoAdapter(service)
    monkeypatch.setattr(m, "VideoGeneratorAiVideoAdapter", lambda *a, **k: adapter)

    made: list = []

    def _factory(idx: int):
        ctx = _ctx(attempt_id=f"batch-{idx}")
        made.append(ctx)
        return ctx

    clips_before = _digest(clips_dir())
    shots_before = _digest(vimax_shots_dir())
    failed = asyncio.run(m.gen_all_videos(
        script, vimax_shots_dir(), vimax_shots_dir() / "script.json",
        only_indices={5, 6}, candidate_factory=_factory))

    assert failed == 0 and len(service.posts) == 2
    assert [c.attempt_id for c in made] == ["batch-5", "batch-6"]
    for ctx in made:
        assert eio.read_accepted(ctx.attempt_dir), ctx.attempt_id
        assert va.current_state(ctx.attempt_dir) == va.STATE_ACCEPTED
        assert (ctx.log_dir / "step02b.log").is_file()
    assert _digest(clips_dir()) == clips_before
    assert _digest(vimax_shots_dir()) == shots_before


# ---------------------------------------------------------------- 隔离守卫（真实入口）

def test_candidate_entry_leaves_production_snapshot_bytewise_unchanged():
    """真实入口 ``_process_one_shot_skip_frames``（候选路径）：实验前后
    ``clips/``、``shots/``、``audio/`` 与章级日志/报告**逐文件 SHA-256 不变**。

    注意证据边界：入口是真实产品入口，provider 是桩（离线、零费用）——"不碰正式
    产物"这条判据与 provider 无关，只取决于落盘路径。
    """
    from step02b_video_gen import main as m

    clips = clips_dir()
    clips.mkdir(parents=True, exist_ok=True)
    (clips / "clip_5.mp4").write_bytes(b"formal-clip-5" * 200)
    (clips / "clip_6.mp4").write_bytes(b"formal-clip-6" * 200)
    adir = audio_dir(1)
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "shot_5.mp3").write_bytes(b"formal-audio" * 40)
    shots = vimax_shots_dir()
    (shots / "5").mkdir(parents=True, exist_ok=True)
    (shots / "5" / "first_frame.png").write_bytes(b"formal-frame" * 60)
    (chapter_dir() / "step02b_quality.json").write_text('{"status": "PASS"}', encoding="utf-8")

    ctx = _ctx(attempt_id="guard-1")
    service = StubVideoService()
    before = m.production_snapshot(1)
    script = _script()
    assert _run_shot(ctx, service, script) is True
    after = m.production_snapshot(1)
    changes = m.snapshot_changes(before, after)
    assert changes == {"added": [], "removed": [], "modified": []}, changes
    # 只提交了确认过的那一镜（无未授权扩量）
    assert len(service.posts) == 1
    # clips/ 逐文件摘要与跑前快照逐一相符（不是"两边都空"造成的假绿）
    assert before[str(clips / "clip_5.mp4")] == eio.sha256_file(clips / "clip_5.mp4")
    assert before[str(clips / "clip_6.mp4")] == eio.sha256_file(clips / "clip_6.mp4")
    assert set(before) >= {str(clips / "clip_5.mp4"), str(clips / "clip_6.mp4"),
                           str(adir / "shot_5.mp3"),
                           str(shots / "5" / "first_frame.png")}


def test_production_guard_detects_injected_write(monkeypatch):
    """判别性：真发生正式写入时守卫必须报出该路径（不是"永远空 diff"）。"""
    from step02b_video_gen import main as m

    clips = clips_dir()
    clips.mkdir(parents=True, exist_ok=True)
    injected = clips / "clip_5.mp4"
    injected.write_bytes(b"formal-clip-5" * 200)

    service = StubVideoService()
    real = service.wait_for_completion

    def _writing_wait(req, task_id, timeout=None):
        result = real(req, task_id, timeout)
        injected.write_bytes(b"OVERWRITTEN-BY-EXPERIMENT" * 200)   # 注入越界覆盖
        return result

    monkeypatch.setattr(service, "wait_for_completion", _writing_wait)

    ctx = _ctx(attempt_id="guard-2")
    before = m.production_snapshot(1)
    _run_shot(ctx, service, _script())
    changes = m.snapshot_changes(before, m.production_snapshot(1))
    assert str(injected) in changes["modified"], changes


def test_ab_experiment_refuses_enhanced_generation(tmp_path, monkeypatch):
    """§15「绕过入口」：旧 A/B 工具**明确拒绝**真实生成（零 POST、零正式写入）。"""
    import importlib.util as _u

    spec = _u.spec_from_file_location("video_ab_experiment", str(_ROOT / "tools" / "video_ab_experiment.py"))
    ab = _u.module_from_spec(spec)
    spec.loader.exec_module(ab)

    posted = []
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: posted.append(a) or None)

    clips = clips_dir()
    clips.mkdir(parents=True, exist_ok=True)
    (clips / "clip_5.mp4").write_bytes(b"formal" * 100)
    before = m_snapshot()
    result = ab._generate_isolated("ab-x", {"variant": "A", "samples": [{"shot_id": "5"}]})
    assert result["generated"] is False
    assert result["code"] == ab.ENHANCEMENT_REFUSAL_CODE
    assert result["api_generated_videos"] == 0 and posted == []
    assert m_snapshot() == before
    # 命令行带候选参数 ⇒ 同一出口（rc=2），不静默继续
    assert ab.main(["run", "--experiment-id", "ab-x",
                    "--candidate-root", str(tmp_path / "out" / "video")]) == 2
    assert posted == []


def m_snapshot() -> dict:
    from step02b_video_gen import main as m
    return m.production_snapshot(1)


def test_candidate_failure_classes_reach_the_cli_exit_code():
    """P2-8：失败类别必须**传到退出码**（未知提交 7 / 提交未确认 6 / 候选被拒 4 / 其余 3）.

    只用"正式产物没变"判成功，会让 unknown 与 commit_unconfirmed 这类需要人工处置的
    结局被报成通过。
    """
    from step02b_video_gen.main import candidate_exit_code

    assert candidate_exit_code(0, []) == 0
    assert candidate_exit_code(1, ["submission_unknown"]) == eio.EXIT_SUBMISSION_UNKNOWN
    assert candidate_exit_code(1, ["commit_unconfirmed"]) == eio.EXIT_COMMIT_UNCONFIRMED
    assert candidate_exit_code(1, ["prepared_unsupported"]) == eio.EXIT_REJECTED
    assert candidate_exit_code(2, ["commit_unconfirmed", "submission_unknown"]) == \
        eio.EXIT_SUBMISSION_UNKNOWN          # 优先序：未知提交最高
    assert candidate_exit_code(1, ["io_failed"]) == eio.EXIT_TOOL_FAIL


def test_submit_failure_carries_its_classification_into_the_batch_record():
    """P2-8：提交异常的**真实分类表**直接决定失败记录的 code（批处理据此定退出码）."""
    from _video_gen.models import SUBMIT_RATE_LIMITED, SUBMIT_REJECTED, SUBMIT_UNKNOWN
    from step02_video_gen.submit_poll import submit_failure_code

    assert submit_failure_code(SUBMIT_UNKNOWN) == "submission_unknown"
    assert submit_failure_code(SUBMIT_RATE_LIMITED) == "rate_limited"
    assert submit_failure_code(SUBMIT_REJECTED) == "provider_failed"
    assert submit_failure_code("") == "candidate_submit_failed"
    # 与退出码表对得上：unknown 最终到 7、被拒到 4
    from step02b_video_gen.main import candidate_exit_code
    assert candidate_exit_code(1, [submit_failure_code(SUBMIT_UNKNOWN)]) ==         eio.EXIT_SUBMISSION_UNKNOWN


def test_resume_counts_as_candidate_run():
    """P2-9：`--resume` 也是候选路径（否则它的日志落到正式章目录）."""
    import argparse
    from step02b_video_gen.main import is_candidate_run

    def _o(**kw):
        return argparse.Namespace(**{"candidate_root": None, "resume": None, **kw})

    assert is_candidate_run(_o()) is False
    assert is_candidate_run(_o(resume="/x/attempts/a1")) is True
    assert is_candidate_run(_o(candidate_root="/x/video")) is True
