# -*- coding: utf-8 -*-
"""tests.enhancements.video_stub_provider - 视频 prepared 入口的**桩 provider**.

用途（计划 §V4「桩 provider 生成有效可解码测试视频，distinct task/颜色/测试音用于
区分身份，不仅返回 success=True」）：

* 走**真实** prepared 链路（``prepare_request`` → 冻结 → ``create_prepared_task``），
  记录**最底层 POST 次数**与每次 POST 的语义 payload（断言"每次 prepared 入口至多
  一个生成 POST"，而不是只 mock adapter）；
* ``create_prepared_task`` 产出的"远端任务"是本地 FFmpeg 现造的**可解码** mp4，
  颜色/频率随 task_id 变化（身份可区分）；
* 故障注入：``fail_mode`` ∈ {network, http_500, http_429, http_403, late_id,
  download_fail, none}，用于提交/恢复矩阵；
* 两把 key 的凭据映射（``credential_profile_ref`` 由配置侧环境变量给出，不暴露密钥）。

本模块只在测试与离线工具（``tools/video_reroll.py --provider stub``）里使用，
不注册进 ``_video_gen`` 的生产注册表。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from _video_gen.models import (
    SUBMIT_RATE_LIMITED,
    VideoSubmissionError,
    SUBMIT_REJECTED,
    SUBMIT_UNKNOWN,
    OBS_FAILED,
    OBS_SUCCEEDED,
    MaterialRef,
    PreparedVideoRequest,
    TaskObservation,
    VideoCapabilityError,
    VideoGenerationResult,
    new_request_id,
)
from _video_gen.service import VideoGenerationService

_COLORS = ("red", "blue", "green", "yellow", "purple", "orange")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("桩 provider 需要本机 ffmpeg 造可解码素材（不下载任何资源）")
    return exe


def make_video(path: Path, *, color: str = "blue", seconds: float = 1.0,
               tone_hz: int = 440, with_audio: bool = True) -> Path:
    """现造一段**可解码**测试视频（带正弦测试音，便于区分身份）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
            f"color=c={color}:s=160x120:r=10:d={seconds}"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=16000:duration={seconds}",
                 "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.run(args, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("造桩视频失败: %s" % proc.stderr.decode("utf-8", "replace")[-800:])
    return path


class StubVideoService(VideoGenerationService):
    """确定性桩：真实的 prepared 组装 + 一次 POST + 可解码结果。"""

    name = "stub"

    def __init__(self, *, video_source: str | None = None, fail_mode: str = "none",
                 key_slot: int = 0, submit_delay: float = 0.0,
                 query_failures: int = 0, accepted_checks: list | None = None):
        self.video_source = Path(video_source) if video_source else None
        self.fail_mode = fail_mode
        self.key_slot = int(key_slot)
        self.submit_delay = float(submit_delay)
        self.query_failures = int(query_failures)
        self.accepted_checks = list(accepted_checks or [])
        self._lock = threading.Lock()
        self.posts: list[dict] = []       # 每次**最底层** POST 的语义记录
        self.tasks: dict[str, dict] = {}
        self.queries: list[str] = []
        self.bindings: dict[str, dict] = {}
        self._n = 0

    # -- 凭据身份（两槽；profile 取自配置侧非敏感环境变量） --
    def _profile_ref(self) -> str | None:
        pid = os.getenv("VIDEO_CREDENTIAL_PROFILE_ID", "").strip()
        rev = os.getenv("VIDEO_CREDENTIAL_REVISION", "").strip()
        return f"{pid}@{rev or 'r0'}" if pid else None

    def get_task_binding(self, task_id: str) -> dict:
        ref = self._profile_ref()
        rec = self.bindings.get(str(task_id))
        if ref is None or rec is None:
            return {"status": "binding_unavailable", "provider": "stub", "key_slot": None,
                    "credential_profile_ref": ref, "binding_version": 1}
        return {"status": "ok", "provider": "stub", "key_slot": rec["key_slot"],
                "credential_profile_ref": ref, "binding_version": 1}

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        if not isinstance(binding, dict) or binding.get("status") != "ok":
            return False
        ref = self._profile_ref()
        if ref is None or binding.get("credential_profile_ref") != ref:
            return False
        rec = self.bindings.get(str(task_id))
        if rec is None:
            return False
        self.key_slot = int(rec["key_slot"])
        return True

    # -- prepare（**无提交**） --
    def prepare_request(self, req, context=None) -> PreparedVideoRequest:
        from shared.enhancement_io import sha256_file

        prompt = str(getattr(req, "prompt", "") or "")
        materials: list[MaterialRef] = []
        plan: list[dict] = [{"type": "text", "text": prompt}]
        roles = [("first_frame", getattr(req, "image_path", None)),
                 ("last_frame", getattr(req, "last_frame_image_path", None))]
        roles += [("keyframe", p) for p in (getattr(req, "extra_keyframe_paths", None) or [])]
        roles += [("reference", p) for p in (getattr(req, "reference_paths", None) or [])]
        for role, path in roles:
            if not path:
                continue
            p = Path(path)
            if not p.is_file():
                raise VideoCapabilityError("material_missing", f"素材不存在: {p}",
                                           field_path=f"materials[{role}]")
            materials.append(MaterialRef(role=role, kind="image", source_path=str(p),
                                         sha256=sha256_file(p), bytes=p.stat().st_size,
                                         ref_id=f"r{len(materials) + 1}"))
            plan.append({"type": "image", "kind": "image", "role": role,
                         "source_path": str(p)})
        mode = ("r2v" if getattr(req, "reference_paths", None)
                else ("image" if (getattr(req, "image_path", None)
                                  or getattr(req, "last_frame_image_path", None)) else "text"))
        prepared = PreparedVideoRequest(
            request_id=new_request_id("stub"), provider="stub",
            model_id=str(os.getenv("STUB_VIDEO_MODEL", "stub-model-1")), mode=mode,
            prompt=prompt,
            parameters={"duration": int(getattr(req, "duration", 6) or 6),
                        "size": str(getattr(req, "size", "720x1280")),
                        "seed": getattr(req, "seed", None)},
            materials=materials, credential_profile_ref=self._profile_ref(),
            key_slot=self.key_slot, binding_version=1, capability_revision="stub-caps-1")
        prepared.content_plan = plan
        prepared.source = {"shot_index": req.clip_index}
        prepared.compute_content_key()
        return prepared

    # -- 一次 POST --
    def create_prepared_task(self, prepared: PreparedVideoRequest, context=None) -> str:
        if not prepared.is_ready():
            raise VideoCapabilityError("unsupported", prepared.unsupported_reason or "not ready")
        mode = self.fail_mode
        with self._lock:
            self._n += 1
            n = self._n
            self.posts.append({"n": n, "model_id": prepared.model_id,
                               "content_key": prepared.content_key,
                               "prompt": prepared.prompt,
                               "materials": [m.key_facts() for m in prepared.materials],
                               "parameters": dict(prepared.parameters or {})})
        if mode == "network":
            raise VideoSubmissionError(SUBMIT_UNKNOWN, "桩：连接断开（不知是否建任务）")
        if mode == "http_500":
            raise VideoSubmissionError(SUBMIT_UNKNOWN, "桩：服务端 500", http_status=500)
        if mode == "http_429":
            raise VideoSubmissionError(SUBMIT_RATE_LIMITED, "桩：429 限流", http_status=429,
                                       retry_after=1.0)
        if mode == "http_403":
            raise VideoSubmissionError(SUBMIT_REJECTED, "桩：403 模型无权限", http_status=403)
        if mode == "http_400_content_policy":
            raise VideoSubmissionError(SUBMIT_REJECTED, "桩：400 content_policy 违规",
                                       http_status=400)
        # 请求**已经发出**（计入底层 POST 计数）之后才模拟慢/失败的响应
        if self.submit_delay:
            time.sleep(self.submit_delay)
        task_id = f"stub-task-{n:04d}"
        color = _COLORS[(n - 1) % len(_COLORS)]
        with self._lock:
            self.tasks[task_id] = {"status": "running", "color": color, "n": n,
                                   "content_key": prepared.content_key}
            self.bindings[task_id] = {"key_slot": self.key_slot}
        # mode == "late_id"：配合 submit_delay > 提交超时使用——任务**已建**，
        # 主协程先超时（unknown），完成回调随后把 task_id 落盘成 late_task_id。
        return task_id

    def create_task(self, req) -> str:
        return self.create_prepared_task(self.prepare_request(req))

    # -- 查询 / 下载 --
    def query_task(self, task_id: str) -> dict:
        obs = self.query_observation(task_id)
        return {"status": obs.status, "content": (obs.result or {}),
                "error": {"code": "stub", "message": obs.error} if obs.error else None}

    def query_observation(self, task_id: str) -> TaskObservation:
        with self._lock:
            self.queries.append(str(task_id))
            rec = dict(self.tasks.get(str(task_id)) or {})
        if not rec:
            return TaskObservation(task_id=str(task_id), status=OBS_FAILED,
                                   error="桩：未知 task_id")
        if self.query_failures > 0:
            self.query_failures -= 1
            return TaskObservation(task_id=str(task_id), status="unknown",
                                   error="桩：查询暂时失败（可恢复）")
        if rec["status"] == "running":
            rec["status"] = "succeeded"
        if rec["status"] == "failed":
            return TaskObservation(task_id=str(task_id), status=OBS_FAILED, error="桩：生成失败")
        return TaskObservation(task_id=str(task_id), status=OBS_SUCCEEDED,
                               raw_status="succeeded",
                               result={"video_url": f"stub://{task_id}"})

    def wait_for_completion(self, req, task_id: str, timeout: int | None = None):
        """把桩任务的"远端结果"落到请求指定的路径（候选 ⇒ candidate_path）。"""
        from _video_gen.service import resolve_download_target
        from shared.paths import shot_video_file

        target = resolve_download_target(req, shot_video_file(req.clip_index))
        rec = self.tasks.get(str(task_id)) or {}
        if self.fail_mode == "download_fail":
            return VideoGenerationResult(clip_index=req.clip_index, success=False,
                                         error="桩：下载失败")
        try:
            if self.video_source and Path(self.video_source).is_file():
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.video_source, target)
            else:
                make_video(Path(target), color=str(rec.get("color") or "blue"),
                           seconds=float(getattr(req, "duration", 1) or 1))
        except Exception as exc:  # noqa: BLE001
            return VideoGenerationResult(clip_index=req.clip_index, success=False,
                                         error=f"桩下载异常: {exc}")
        return VideoGenerationResult(clip_index=req.clip_index, success=True,
                                     save_path=str(target))


class StubVideoAdapter:
    """把桩 provider 适配成 submit_poll / 工具需要的 adapter 面（不改生产适配器）. """

    def __init__(self, service: StubVideoService):
        self.service = service
        self._svc = service

    def get_task_binding(self, task_id: str) -> dict:
        return self.service.get_task_binding(task_id)

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        return self.service.restore_task_binding(task_id, binding)

    def get_key_index(self) -> int:
        return int(self.service.key_slot)

    async def fetch_result(self, req, task_id: str):
        return self.service.wait_for_completion(req, task_id)
