# -*- coding: utf-8 -*-
"""I 包贯通链共用的夹具与入口（只读被验收包，不改它们）.

原则：链上的每一条都由**真实入口**驱动（真实 CLI / 真实 parser / 真实 FFmpeg /
真实事务），桩只出现在"外部付费模型"这一层，且桩自己也要能被计数与区分身份。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = None  # 由各测试模块自行 pytestmark

SHOT_INDEX = 7
DURATION = 6.0
_UID = {"n": 0}


def new_experiment_id(prefix: str) -> str:
    _UID["n"] += 1
    return f"{prefix}{os.getpid()}{_UID['n']:03d}"


def script(shot_index: int = SHOT_INDEX, *, subtitle: str = "雨停之前",
           duration: float = DURATION) -> dict:
    return {"characters": [], "shots": [
        {"index": shot_index, "prompt": f"[中景] 沈砚停在门口（{subtitle}）",
         "ff_desc": "沈砚站在门口", "lf_desc": "沈砚转身离开",
         "motion_desc": "0-3秒：停下；3-6秒：转身",
         "subtitle": subtitle, "narration_segment": "", "keyframes": [],
         "duration_sec": duration}]}


def shot_of(doc: dict, index: int = SHOT_INDEX) -> dict:
    for s in doc["shots"]:
        if s["index"] == index:
            return s
    raise KeyError(index)


def frames(root: Path, index: int = SHOT_INDEX, *, ff: bytes = b"ff-1",
           lf: bytes = b"lf-1") -> list:
    d = Path(root) / str(index)
    d.mkdir(parents=True, exist_ok=True)
    (d / "first_frame.png").write_bytes(ff + b"\0" * 64)
    (d / "last_frame.png").write_bytes(lf + b"\0" * 64)
    return [str(d / "first_frame.png"), str(d / "last_frame.png")]


def video_ctx(experiment_id: str, *, attempt_id: str = "a-1", chapter: int = 1):
    from shared import enhancement_io as eio
    # 来源身份与真实入口一致（§F4）：采纳 manifest 必须答得出"哪一章、哪一稿"，
    # 缺了会被采纳出口拒收（2026-10-03 复核 P1-5）——夹具不再省这三个字段。
    ctx = eio.CandidateContext(experiment_id=experiment_id, package="video",
                               attempt_id=attempt_id, chapter=chapter,
                               scope={"chapter": chapter}, script_revision="rev-i-chain",
                               source="video_candidates")
    ctx.prepare()
    return ctx


def run_candidate(monkeypatch, ctx, *, doc: dict, service, frame_paths,
                  attempt_id: str | None = None):
    """跑真实候选入口 ``_submit_video_candidate`` 并等它走完（轮询间隔归零，判据不变）."""
    from step02_video_gen import prompt_builder as pb
    from step02_video_gen import submit_poll as sp
    from tests.enhancements.video_stub_provider import StubVideoAdapter
    from shared.paths import SHOT_VIDEO_FILENAME

    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)
    shot = shot_of(doc)
    idx = shot["index"]
    shot_dir = ctx.shot_dir(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    video_path = shot_dir / SHOT_VIDEO_FILENAME
    adapter = StubVideoAdapter(service)

    async def _go():
        task = await sp._submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=video_path,
            frame_paths=frame_paths, shot_hash="h-i-chain", video_adapter=adapter,
            poll_semaphore=asyncio.Semaphore(1), script=doc, idx=idx,
            prompt=str(shot.get("prompt") or ""), characters=[],
            continuity_ref_image=None, mode="A", candidate_context=ctx,
            logger=None, make_request=pb._make_request)
        if task is None:
            return None
        try:
            return await task
        except Exception as exc:  # noqa: BLE001 - 异常本身是被测行为
            return exc

    return asyncio.run(_go())


def run_cli(module_path: Path, argv: list, *, env: dict | None = None,
            cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """真实子进程跑工具（用于"命令行合同"级证据）."""
    full = dict(os.environ)
    if env:
        full.update(env)
    return subprocess.run([__import__("sys").executable, str(module_path), *map(str, argv)],
                          cwd=str(cwd or REPO), env=full, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def digest_tree(root: Path) -> dict:
    from shared import enhancement_io as eio
    out = {}
    root = Path(root)
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[str(p)] = eio.sha256_file(p)
    return out


def probe_duration(path: Path) -> float:
    proc = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries",
                           "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, timeout=120)
    return float((proc.stdout or "0").strip() or 0)
