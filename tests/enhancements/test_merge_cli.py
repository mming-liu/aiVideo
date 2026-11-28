# -*- coding: utf-8 -*-
"""Step04 早期 bootstrap 与候选隔离（计划 §A4、§15「bootstrap与源码守卫」）.

判别点：
- 增强参数缺 ``--candidate-root`` / 目录形状不对 ⇒ 解析期干净失败（rc=2，不建目录）；
- ``main(options=None)`` 在未解析 argv 时**沿用现有环境**（不 NameError、不解析 pytest 的 argv）；
- 候选模式下 SRT/TEMP/log/final_tmp/候选 mp4 **全部**落在本 attempt 内；
- 真实子进程里 ``--chapter`` 与候选参数同时生效（scope 生效 + attempt 建立）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step04_merge.cli import (  # noqa: E402
    BootstrapOptions, ENHANCEMENT_FLAGS, create_attempt_context, parse_bootstrap,
    resolve_experiment_id,
)

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(autouse=True)
def _restore_scope_env():
    """``apply_scope`` 直接写 os.environ（产品行为）：用例结束后恢复，免得污染别的用例."""
    keys = ("NOVEL_CHAPTER", "BOOK_INDEX", "BOOK_NAME")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _package_root(experiment: str = "exp1", chapter: int | None = None) -> Path:
    """包 root 走 ``shared.paths`` 单源（测试不自己拼目录层级）."""
    from shared.paths import enhancement_package_dir
    return enhancement_package_dir("merge", experiment, chapter)


# ------------------------------------------------------------------ parse_bootstrap

def test_enhancement_flag_without_candidate_root_fails_cleanly(tmp_path):
    with pytest.raises(SystemExit) as ei:
        parse_bootstrap(["--subtitle-timings", str(tmp_path / "t.json")])
    assert ei.value.code == 2


def test_candidate_root_must_use_the_shared_paths_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    with pytest.raises(SystemExit) as ei:
        parse_bootstrap(["--motion-plan", str(tmp_path / "m.json"),
                         "--candidate-root", str(tmp_path / "not_a_package_root")])
    assert ei.value.code == 2
    # 合法形状：<chapter_dir>/_enhancement/<实验id>/merge
    root = _package_root()
    options = parse_bootstrap(["--candidate-root", str(root)])
    assert options.candidate_root == root
    # 给了实验根就是增强模式（产物隔离到该 attempt）；单给 root 也要走候选分支
    assert options.enhancement_requested
    assert options.require_candidate_root() == root
    assert resolve_experiment_id(root) == "exp1"


def test_parse_bootstrap_applies_scope_and_keeps_the_new_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    root = _package_root("exp1", 2)          # 章号在包 root 路径里（paths 单源）
    options = parse_bootstrap(["--chapter", "2", "--candidate-root", str(root),
                               "--subtitle-timings", str(tmp_path / "t.json"),
                               "--candidate-out", str(root / "attempts" / "a1" /
                                                      "outputs" / "mine.mp4")])
    assert os.environ["NOVEL_CHAPTER"] == "2"
    assert options.scope["chapter"] == 2
    assert set(options.enhancement_flags) == {"candidate_root", "subtitle_timings",
                                              "candidate_out"}
    assert "chapter_002" in options.scope["summary"]


def test_parse_bootstrap_without_enhancement_flags_keeps_the_old_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    options = parse_bootstrap([])
    assert options.enhancement_requested is False
    assert options.candidate_root is None
    with pytest.raises(ValueError):
        options.require_candidate_root()


# ------------------------------------------------------------------ attempt 上下文

def test_attempt_context_routes_srt_temp_and_log_into_the_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    options = BootstrapOptions(candidate_root=_package_root())
    ctx = create_attempt_context(options, attempt_id="a-test-1")
    assert ctx.root == _package_root() / "attempts" / "a-test-1"
    assert ctx.outputs_dir.is_dir() and ctx.work_dir.is_dir() and ctx.log_dir.is_dir()
    assert ctx.candidate_out == ctx.outputs_dir / "candidate.mp4"
    assert ctx.srt_path == ctx.outputs_dir / "subtitles.srt"
    assert ctx.final_tmp.parent == ctx.work_dir
    from shared.paths import execution_log_file
    assert execution_log_file("step04", ctx) == ctx.log_dir / "step04.log"


def test_candidate_out_outside_the_attempt_is_rejected(tmp_path, monkeypatch):
    from shared.enhancement_io import EnhancementError

    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    options = BootstrapOptions(candidate_root=_package_root(),
                               candidate_out=tmp_path / "elsewhere.mp4")
    with pytest.raises(EnhancementError) as ei:
        create_attempt_context(options, attempt_id="a-test-2")
    assert ei.value.code == "candidate_out_outside_attempt"


def test_candidate_out_inside_outputs_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    root = _package_root()
    target = root / "attempts" / "a-test-3" / "outputs" / "mine.mp4"
    options = BootstrapOptions(candidate_root=root, candidate_out=target)
    ctx = create_attempt_context(options, attempt_id="a-test-3")
    assert ctx.candidate_out == target.resolve()


def test_main_without_options_does_not_parse_argv(tmp_path, monkeypatch):
    """`main(options=None)` 且模块顶未解析时：沿用现有环境，不 NameError、不二次解析。"""
    import step04_merge.main as m

    monkeypatch.setattr(sys, "argv", ["pytest", "--weird-flag"])
    assert m._ensure_attempt(None) is None
    assert m._ensure_attempt(BootstrapOptions()) is None      # 无增强参数 ⇒ 无 attempt
    # 有增强参数的（程序化）options 也能建立上下文；chapter 用**入口自己的**常量
    # （模块顶部读取的 CHAPTER_INDEX），不是别的用例通过 apply_scope 留下的 env 值
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    ctx = m._ensure_attempt(BootstrapOptions(candidate_root=_package_root("exp1", m.CHAPTER_INDEX)))
    assert ctx is not None and ctx.root.parent.name == "attempts"


# ------------------------------------------------------------------ 真实子进程

def test_real_subprocess_bootstrap_beats_config_and_builds_the_attempt(tmp_path, monkeypatch):
    """真实子进程：``--chapter 2`` + ``--candidate-root`` 同时生效（scope + 候选上下文）."""
    output_base = tmp_path / "out"
    monkeypatch.setenv("OUTPUT_BASE", str(output_base))
    monkeypatch.delenv("NOVEL_CHAPTER", raising=False)
    monkeypatch.delenv("STEP01_LOG_PATH", raising=False)
    from shared.paths import chapter_dir
    probe_root = _package_root("exp9", 2)
    production_log = chapter_dir(2) / "step04.log"
    env = dict(os.environ)
    env["OUTPUT_BASE"] = str(output_base)
    env["PYTHONIOENCODING"] = "utf-8"
    for key in ("NOVEL_CHAPTER", "STEP01_LOG_PATH", "CLIP_SOURCE"):
        env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "tests" / "shared" / "_stage_head_probe.py"),
         str(_ROOT / "step04_merge" / "main.py"),
         "--chapter", "2", "--candidate-root", str(probe_root)],
        cwd=str(_ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300, check=False)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data.get("NOVEL_CHAPTER", data.get("CHAPTER_INDEX")) == 2, data
    assert data["CHAPTER_DIR"] == "chapter_002", data
    # 候选上下文真的建立了（attempt 目录 + 日志都在第 2 章的实验根下）
    attempts = probe_root / "attempts"
    assert attempts.is_dir(), f"没有建立 attempts 目录: {attempts}"
    attempt_dirs = [d for d in attempts.iterdir() if d.is_dir()]
    assert len(attempt_dirs) == 1
    # 日志路径由 execution_log_file(context) 单源给出（文件按需创建，故只断言目录）
    from shared.paths import execution_log_file
    assert execution_log_file("step04", None, 2) == production_log
    assert (attempt_dirs[0] / "logs").is_dir()
    assert (attempt_dirs[0] / "outputs").is_dir() and (attempt_dirs[0] / "work").is_dir()
    # 生产日志没有被这次（只到 head 的）运行创建
    assert not production_log.exists()


def test_unknown_argument_is_rejected_by_the_early_parser(tmp_path):
    env = dict(os.environ)
    env["OUTPUT_BASE"] = str(tmp_path / "out")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, str(_ROOT / "step04_merge" / "main.py"),
                           "--nope"], cwd=str(_ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          check=False)
    assert proc.returncode == 2
    assert "--nope" in (proc.stdout + proc.stderr)


# ------------------------------------------------------------------ 候选提交链

def _render_candidate(tmp_path, ctx):
    """真实渲染 + 提交链回到 accepted（F 契约）."""
    from shared import enhancement_io as eio
    from shared.media_contract import MERGE_FPS
    from step04_merge.backends import ffmpeg as be
    from step04_merge.main import _commit_candidate
    from step04_merge.timeline import (ClipEntry, MergeTimeline, stable_shot_id,
                                       validate_timeline)

    clip = tmp_path / "clip_1.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=navy:s=128x72:d=0.5:r={MERGE_FPS}",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(clip)], check=True)
    from dataclasses import replace as _replace
    timeline = validate_timeline(MergeTimeline(
        scope=ctx.scope, script_revision="rev1", width=128, height=72, fps=MERGE_FPS,
        clips=(ClipEntry(shot_index=1, kind="shot",
                         stable_id=stable_shot_id(ctx.scope, "rev1", "shot", 1),
                         path=clip, duration_sec=0.0, raw_duration_sec=0.5, frames=0,
                         has_audio=True),),
        boundary_transitions=(), source={"test": "merge_cli"}))
    from step04_merge.main import _source_declarations
    timeline = _replace(timeline, source={
        **timeline.source,
        "source_declarations": _source_declarations(timeline, {}, [])})
    result = be.render(timeline, ctx.candidate_out, ctx.work_dir,
                       settings=be.EncodeSettings(width=128, height=72, crf="28",
                                                  preset="ultrafast", loudnorm=False),
                       evidence_dir=ctx.outputs_dir)
    accepted = _commit_candidate(ctx, timeline, result, BootstrapOptions(),
                                 timeline.subtitle_plan, ctx.srt_path)
    verified = eio.verify_accepted(ctx.package_root, required_checks=("timeline_validated",))
    return accepted, verified, result


@pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")
def test_candidate_commit_and_readback(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    ctx = create_attempt_context(BootstrapOptions(candidate_root=_package_root()),
                                 attempt_id="a-commit-1")
    accepted, verified, result = _render_candidate(tmp_path, ctx)
    assert accepted["attempt_id"] == "a-commit-1"
    assert verified["pointer"]["attempt_id"] == "a-commit-1"
    assert Path(verified["pointer"]["manifest_path"]).name == "manifest.json"
    roles = {a["role"] for a in verified["manifest"]["artifacts"]}
    assert "candidate_video" in roles
    assert verified["manifest"]["input_key"] == accepted["input_key"]
    assert verified["manifest"]["ffmpeg_version"] == result.ffmpeg_version
    # B/C 共用 schema 的解码证据：**恰好**六个字段，且登记进 manifest
    evidence = json.loads((ctx.outputs_dir / "media_decode_evidence.json").read_text("utf-8"))
    assert set(evidence) == {"input_hash", "ffmpeg_build", "argv", "returncode",
                             "stderr_digest", "status"}
    assert evidence["status"] == "decoded" and evidence["returncode"] == 0
    assert evidence["input_hash"] == result.sha256
    assert "media_decode_evidence" in roles
    # 来源声明（B 逐字复制，不倒推）：本产物声明的区间/内部切点/禁用类目
    manifest = verified["manifest"]
    assert isinstance(manifest["allowed_intervals"], list)
    assert isinstance(manifest["declared_internal_cuts"], list)
    assert manifest["prohibited_kinds"] == []
    # 给 B 的检测器参数：显式给出且远低于 ffmpeg 默认的 2.0s（否则端点淡出检不出）
    assert manifest["detector_parameters"]["black"]["min_duration_sec"] < 2.0
    # 失败留档不移动 accepted（旧 accepted 仍可读回同一 attempt）
    from shared import enhancement_io as eio
    from step04_merge.main import _record_failure

    class _Boom(Exception):
        code = "render_failed"
        stage = "encode"

    _record_failure(ctx, _Boom("炸了"))
    events, tail = eio.read_events_strict(ctx.root)
    assert tail is None
    assert events[-1]["kind"] == "failed" and events[-1]["code"] == "render_failed"
    assert eio.read_accepted(ctx.package_root)["attempt_id"] == "a-commit-1"


def test_missing_candidate_media_is_rejected_before_any_write(tmp_path, monkeypatch):
    """候选路径守卫：与正式产物/输入冲突或不存在时拒收（不写盘）."""
    from shared import enhancement_io as eio

    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    ctx = create_attempt_context(BootstrapOptions(candidate_root=_package_root()),
                                 attempt_id="a-guard-1")
    with pytest.raises(eio.EnhancementError) as ei:
        eio.assert_candidate_path(ctx.candidate_out, experiment_root=ctx.package_root,
                                  formal=eio.production_paths(1), must_exist=True)
    assert ei.value.code == "candidate_path_missing"


# ------------------------------------------------------------------ 来源声明（交给 B）

def test_source_declarations_are_copied_from_the_timeline_not_inferred(tmp_path):
    """端点淡入/淡出 → ``kind="black"`` 区间；边界过渡 → ``kind="transition"``；
    镜内切点来自 step01 的 ``temporal_plan``；未声明的禁用类目为空表。"""
    from shared.media_contract import MERGE_FPS
    from step04_merge.main import _source_declarations
    from step04_merge.timeline import (BoundaryTransition, ClipEntry, EndpointEffect,
                                       MergeTimeline, stable_shot_id, validate_timeline)

    for index in (1, 2):
        (tmp_path / f"c{index}.mp4").write_bytes(b"x" * 32)
    timeline = validate_timeline(MergeTimeline(
        scope="chapter_001", script_revision="rev1", width=128, height=72, fps=MERGE_FPS,
        clips=tuple(ClipEntry(shot_index=i, kind="shot",
                              stable_id=stable_shot_id("chapter_001", "rev1", "shot", i),
                              path=tmp_path / f"c{i}.mp4", duration_sec=0.0,
                              raw_duration_sec=dur, frames=0, has_audio=True)
                    for i, dur in ((1, 2.0), (2, 3.0))),
        boundary_transitions=(BoundaryTransition(1, "fade", 0.5),),
        endpoint_effects=(EndpointEffect("fade_in", 0.5, 0),
                          EndpointEffect("fade_out", 0.5, 1))))
    shots = [{"index": 2, "temporal_plan": {"events": [
        {"event_id": "e1", "start_sec": 1.0, "end_sec": 2.0, "description": "内部事件"}]}}]
    declarations = _source_declarations(timeline, {}, shots)
    kinds = [(d["kind"], round(d["start_sec"], 3), round(d["end_sec"], 3))
             for d in declarations["allowed_intervals"]]
    assert ("black", 0.0, 0.5) in kinds                     # 首淡入的黑场段
    assert ("black", 4.0, 4.5) in kinds                     # 末淡出的黑场段（总长 4.5）
    assert ("transition", 1.5, 2.0) in kinds                # 边界过渡重叠段
    assert all(d["source_locator"].startswith("/") for d in declarations["allowed_intervals"])
    cuts = declarations["declared_internal_cuts"]
    assert len(cuts) == 1 and cuts[0]["kind"] == "cut"
    assert cuts[0]["start_sec"] == pytest.approx(1.5 + 1.0)  # 窗口起点 + 镜内局部时间
    assert cuts[0]["source_locator"] == "/shots/0/temporal_plan/events/0"
    assert declarations["prohibited_kinds"] == []            # 来源没声明就不猜


def test_detector_parameters_follow_the_shortest_declared_fade(tmp_path):
    """端点淡出 0.5s ⇒ black.min_duration_sec=0.25（默认 2.0s 会整段检不出）。"""
    from shared.media_contract import MERGE_FPS
    from step04_merge.main import _detector_parameters
    from step04_merge.timeline import (BoundaryTransition, ClipEntry, EndpointEffect,
                                       MergeTimeline, stable_shot_id, validate_timeline)

    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x" * 32)
    timeline = validate_timeline(MergeTimeline(
        scope="chapter_001", script_revision="rev1", width=128, height=72, fps=MERGE_FPS,
        clips=(ClipEntry(shot_index=1, kind="shot",
                         stable_id=stable_shot_id("chapter_001", "rev1", "shot", 1),
                         path=clip, duration_sec=0.0, raw_duration_sec=2.0, frames=0,
                         has_audio=True),),
        boundary_transitions=(), endpoint_effects=(EndpointEffect("fade_in", 0.5, 0),)))
    assert _detector_parameters(timeline)["black"]["min_duration_sec"] == pytest.approx(0.25)
