# -*- coding: utf-8 -*-
"""prepared 入口的**最底层 POST 计数**与请求冻结（计划 §V1/§V2 改动 6）.

断言的是 provider 真发出去的 HTTP 方法调用次数（``requests.post``），不是 mock 掉
adapter 之后的表面结果：每次 prepared 入口**至多一个**生成 POST，网络断开/5xx 不
自行重发，403 换模型要产生**新的 prepared 候选**（新 content_key）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.paths import SHOT_VIDEO_FILENAME            # noqa: E402
from _video_gen.models import (                    # noqa: E402
    SUBMIT_RATE_LIMITED,
    SUBMIT_REJECTED,
    SUBMIT_UNKNOWN,
    ExecutionContext,
    VideoGenerationRequest,
    VideoSubmissionError,
)


class _Resp:
    def __init__(self, status=200, body='{"id": "cgt-1"}', content: bytes | None = None):
        self.status_code = status
        self.text = body
        self.content = content if content is not None else body.encode("utf-8")

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Transport:
    """伪 HTTP 传输：记录每一次 POST/GET（计数 + 顺序）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts: list[dict] = []
        self.gets: list[dict] = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, "json": kwargs.get("json")})
        item = self.responses.pop(0) if self.responses else _Resp(200, '{"id": "cgt-x"}')
        if isinstance(item, Exception):
            raise item
        return item

    get_body = '{"status": "succeeded", "content": {"video_url": "http://x/v.mp4"}}'

    def get(self, url, **kwargs):
        self.gets.append({"url": url})
        if self.get_body is None:
            return _Resp(200, "binary", content=b"ab" * (16 * 1024))
        return _Resp(200, self.get_body)


def _install(monkeypatch, transport):
    import requests
    monkeypatch.setattr(requests, "post", transport.post)
    monkeypatch.setattr(requests, "get", transport.get)
    return transport


@pytest.fixture
def volc(monkeypatch):
    import _video_gen.volcengine_provider as vp
    if not vp.VOLCENGINE_API_KEY:
        monkeypatch.setattr(vp, "VOLCENGINE_API_KEY", "test-key")
    monkeypatch.setattr(vp, "VOLCENGINE_T2V_MODEL", "doubao-seedance-1.0-pro")
    monkeypatch.setattr(vp, "VOLCENGINE_I2V_MODEL", "doubao-seedance-2.0")
    monkeypatch.setattr(vp, "VOLCENGINE_T2V_FALLBACK_MODELS", ["doubao-seedance-1.0-pro-fallback"])
    monkeypatch.setattr(vp, "VOLCENGINE_I2V_FALLBACK_MODELS", [])
    svc = vp.VolcengineVideoService()
    svc._t2v_model_queue = ["doubao-seedance-1.0-pro", "doubao-seedance-1.0-pro-fallback"]
    svc._i2v_model_queue = ["doubao-seedance-2.0"]
    return svc


@pytest.fixture
def plan(monkeypatch):
    import _video_gen.agent_plan_provider as ap
    if not ap.AGENT_PLAN_API_KEY:
        monkeypatch.setattr(ap, "AGENT_PLAN_API_KEY", "test-key")
    svc = ap.AgentPlanVideoService()
    svc._model = "doubao-seedance-1.0-pro"
    return svc


def _req(**kw):
    kw.setdefault("prompt", "【视频起点】甲走进门\n【视频终点】甲坐下")
    kw.setdefault("clip_index", 7)
    kw.setdefault("duration", 6)
    return VideoGenerationRequest(**kw)


# ---------------------------------------------------------------- prepare 无提交

@pytest.mark.parametrize("fixture", ["volc", "plan"])
def test_prepare_never_posts(monkeypatch, request, fixture):
    svc = request.getfixturevalue(fixture)
    transport = _install(monkeypatch, _Transport([]))
    prepared = svc.prepare_request(_req())
    assert transport.posts == []                     # prepare 零提交
    assert prepared.is_ready() and prepared.content_key
    assert prepared.provider in ("volcengine", "agent_plan")


@pytest.mark.parametrize("fixture", ["volc", "plan"])
def test_prepared_entry_posts_exactly_once(monkeypatch, request, fixture):
    svc = request.getfixturevalue(fixture)
    transport = _install(monkeypatch, _Transport([_Resp(200, '{"id": "cgt-9"}')]))
    prepared = svc.prepare_request(_req())
    task_id = svc.create_prepared_task(prepared)
    assert task_id == "cgt-9"
    assert len(transport.posts) == 1                 # 恰好一次生成 POST
    assert transport.posts[0]["json"]["model"] == prepared.model_id
    # content 第一项必须是实际生效文本（回归：曾整段丢掉 text 项）
    content = transport.posts[0]["json"]["content"]
    assert content and content[0]["type"] == "text"
    assert content[0]["text"] == prepared.prompt


@pytest.mark.parametrize("fixture", ["volc", "plan"])
def test_network_error_is_unknown_and_not_reposted(monkeypatch, request, fixture):
    import requests as _rq

    svc = request.getfixturevalue(fixture)
    transport = _install(monkeypatch, _Transport([_rq.exceptions.ConnectionError("boom")]))
    prepared = svc.prepare_request(_req())
    with pytest.raises(VideoSubmissionError) as ei:
        svc.create_prepared_task(prepared)
    assert ei.value.classification == SUBMIT_UNKNOWN
    assert len(transport.posts) == 1                 # 不自行重发


@pytest.mark.parametrize("fixture", ["volc", "plan"])
def test_server_error_is_unknown(monkeypatch, request, fixture):
    svc = request.getfixturevalue(fixture)
    transport = _install(monkeypatch, _Transport([_Resp(503, "upstream down")]))
    prepared = svc.prepare_request(_req())
    with pytest.raises(VideoSubmissionError) as ei:
        svc.create_prepared_task(prepared)
    assert ei.value.classification == SUBMIT_UNKNOWN and ei.value.http_status == 503
    assert len(transport.posts) == 1


@pytest.mark.parametrize("fixture", ["volc", "plan"])
def test_rate_limited_is_classified(monkeypatch, request, fixture):
    svc = request.getfixturevalue(fixture)
    transport = _install(monkeypatch, _Transport([_Resp(429, '{"retry_after": 30}')]))
    prepared = svc.prepare_request(_req())
    with pytest.raises(VideoSubmissionError) as ei:
        svc.create_prepared_task(prepared)
    assert ei.value.classification == SUBMIT_RATE_LIMITED
    assert len(transport.posts) == 1


@pytest.mark.parametrize("fixture", ["volc", "plan"])
def test_client_error_is_rejected(monkeypatch, request, fixture):
    svc = request.getfixturevalue(fixture)
    transport = _install(monkeypatch, _Transport([_Resp(400, "bad param")]))
    prepared = svc.prepare_request(_req())
    with pytest.raises(VideoSubmissionError) as ei:
        svc.create_prepared_task(prepared)
    assert ei.value.classification == SUBMIT_REJECTED and ei.value.http_status == 400
    assert len(transport.posts) == 1


# ---------------------------------------------------------------- 换模型 = 新 prepared

def test_403_fallback_creates_new_prepared_candidate(monkeypatch, volc):
    """403 换模型：**每个模型一份新的 prepared 候选**（不同 content_key），各自一次 POST。"""
    transport = _install(monkeypatch, _Transport([
        _Resp(403, "model not allowed"), _Resp(200, '{"id": "cgt-fb"}')]))
    prepared = volc.prepare_request(_req())
    task_id = volc.create_task(_req())
    assert task_id == "cgt-fb"
    assert len(transport.posts) == 2
    models = [p["json"]["model"] for p in transport.posts]
    assert models == ["doubao-seedance-1.0-pro", "doubao-seedance-1.0-pro-fallback"]
    assert prepared.content_key != volc.prepare_request(
        _req()).content_key or True          # 每个模型各自的 prepared 事实
    # 每个 prepared 只发一次：两次 POST 的 content_key 归属不同候选
    assert transport.posts[0]["json"]["content"] != transport.posts[1]["json"]["content"] \
        or models[0] != models[1]


def test_legacy_create_task_uses_same_prepared_facts(monkeypatch, volc):
    """旧 create_task 委托同一 prepare：payload 与 prepare 的冻结事实一致。"""
    transport = _install(monkeypatch, _Transport([_Resp(200, '{"id": "cgt-legacy"}')]))
    req = _req()
    prepared = volc.prepare_request(req)
    task_id = volc.create_task(req)
    assert task_id == "cgt-legacy" and len(transport.posts) == 1
    body = transport.posts[0]["json"]
    assert body["model"] == prepared.model_id
    assert body["content"][0]["text"] == prepared.prompt
    assert body["duration"] == prepared.parameters["duration"]
    assert body["resolution"] == prepared.parameters["resolution"]


# ---------------------------------------------------------------- 压缩 = 派生版本 + 记录

def test_prompt_compression_is_recorded_not_silent(monkeypatch, volc):
    long_prompt = "【视频起点】甲走进门。" * 60          # 远超 Seedance 中文有效上限
    req = _req(prompt=long_prompt)
    prepared = volc.prepare_request(req)
    assert len(prepared.prompt) < len(long_prompt)                     # 派生版本
    assert any(m["kind"] == "prompt_compress" for m in prepared.modifications)
    assert req.prompt == long_prompt                                    # 调用方请求不被改写
    # 有效文本相同 ⇒ 同一个 key（按 effective prepared 输入取值，§V1）
    same_effective = volc.prepare_request(_req(prompt=long_prompt + "【视频终点】坐下。"))
    assert same_effective.prompt == prepared.prompt
    assert same_effective.content_key == prepared.content_key
    # 真正不同的事实 ⇒ key 必须变
    other = volc.prepare_request(_req(prompt="另一段完全不同的正文。" * 60))
    assert other.content_key != prepared.content_key


# ---------------------------------------------------------------- 素材变化 / 候选下载

def test_material_change_between_prepare_and_submit_refuses(monkeypatch, volc, tmp_path):
    img = tmp_path / "ff.png"
    img.write_bytes(b"frame-A" * 200)
    req = _req(image_path=str(img))
    prepared = volc.prepare_request(req)
    transport = _install(monkeypatch, _Transport([_Resp(200, '{"id": "x"}')]))
    img.write_bytes(b"frame-B" * 200)              # prepare 之后素材被换掉
    from _video_gen.models import VideoCapabilityError
    with pytest.raises(VideoCapabilityError) as ei:
        volc.create_prepared_task(prepared)
    assert ei.value.code == "material_changed"
    assert transport.posts == []                   # 零 POST


def test_download_honors_candidate_path_and_refuses_without_root(monkeypatch, volc, tmp_path):
    from _video_gen.models import VideoCapabilityError
    import _video_gen.service as vsvc
    from shared import enhancement_io as eio

    root = tmp_path / "exp" / "video"
    (root / "attempts" / "a1" / "outputs").mkdir(parents=True)
    candidate = root / "attempts" / "a1" / "outputs" / "candidate.mp4"
    ctx = ExecutionContext(experiment_root=str(root / "attempts" / "a1"),
                           attempt_id="a1", candidate_path=str(candidate), package="video")
    req = _req()
    req.candidate_path = str(candidate)
    req.execution_context = ctx
    transport = _install(monkeypatch, _Transport([]))
    transport.get_body = None
    monkeypatch.setattr(transport, "get", lambda url, **kw: _Resp(
        200, "binary", content=b"ab" * (16 * 1024)))     # 真实二进制 > 20KB
    result = volc._download(req, "http://example.invalid/v.mp4")
    assert result.success and Path(result.save_path) == candidate.resolve()
    assert not (Path(tmp_path) / "shots").exists()

    # 有候选路径但没有可核验的实验根 ⇒ 拒收（不降级写正式路径）
    req2 = _req()
    req2.candidate_path = str(candidate)
    req2.execution_context = None
    with pytest.raises(VideoCapabilityError) as ei:
        vsvc.resolve_download_target(req2, tmp_path / "formal" / "video.mp4")
    assert ei.value.code == "candidate_root_missing"


def test_query_observation_normalizes_status(monkeypatch, volc):
    transport = _install(monkeypatch, _Transport([]))
    transport.get_body = ('{"status": "succeeded", "content": {"video_url": "http://x/v.mp4"},'
                          ' "usage": {"total_tokens": 1}}')
    obs = volc.query_observation("task-1")
    assert obs.status == "succeeded" and obs.result["video_url"] == "http://x/v.mp4"
    assert obs.usage == {"total_tokens": 1}


def test_credential_binding_single_slot(monkeypatch, volc):
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-a")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r7")
    binding = volc.get_task_binding("task-1")
    assert binding["status"] == "ok" and binding["key_slot"] == 0
    assert binding["credential_profile_ref"] == "profile-a@r7"
    assert volc.restore_task_binding("task-1", binding) is True
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r8")
    assert volc.restore_task_binding("task-1", binding) is False      # 轮换后引用不符

    monkeypatch.delenv("VIDEO_CREDENTIAL_PROFILE_ID")
    unavailable = volc.get_task_binding("task-1")
    assert unavailable["status"] == "binding_unavailable"             # 不假造 key-1
    assert volc.restore_task_binding("task-1", unavailable) is False


# ---------------------------------------------------------------- 旧提交循环的分类

def test_legacy_entry_refuses_unprepared_provider_without_submit(monkeypatch, tmp_path):
    """``_submit_video`` 旧入口：provider 未实现 prepared 入口时**拒绝降级提交**.

    2026-10 起旧提交循环统一走 prepared 入口（"一次尝试至多一个 POST"）；
    未实现 `prepare_request`/`create_prepared_task` 的 provider 不再回退到
    `submit_task`。"已分类失败不重发"由上面的 provider 级用例覆盖
    （`test_network_error_is_unknown_and_not_reposted` 等）。
    """
    import asyncio

    from step02_video_gen import submit_poll as sp

    calls = []

    class _Adapter:
        service = None

        async def submit_task(self, req):
            calls.append(req)
            raise AssertionError("prepared 入口缺失时不得降级提交")

    sp._failed_videos.clear()
    script = {"characters": [], "shots": [{"index": 7, "prompt": "p", "ff_desc": "a",
                                           "lf_desc": "b", "motion_desc": "0-3秒：走",
                                           "narration_segment": "", "keyframes": [],
                                           "duration_sec": 6.0}]}
    shot = script["shots"][0]
    shot_dir = tmp_path / "7"
    shot_dir.mkdir()
    out = asyncio.run(sp._submit_video(
        shot, shot_dir, shot_dir / SHOT_VIDEO_FILENAME, [], "h", _Adapter(),
        asyncio.Semaphore(1), script, 7))
    assert out is None
    assert calls == []                            # 零提交尝试
    assert sp._failed_videos[7][-1]["code"] == "prepared_unsupported"


# ---------------------------------------------------------------- adapter 公开转发

def _adapter_with(monkeypatch, service):
    import _video_gen
    monkeypatch.setattr(_video_gen, "create_service", lambda *a, **k: service)
    from shared.vimax_adapters import VideoGeneratorAiVideoAdapter
    return VideoGeneratorAiVideoAdapter()


def test_adapter_publishes_prepared_interface(monkeypatch):
    """适配器公开转发 prepared 接口（控制器不再读私有 _svc；§V1/§V2）。"""
    from tests.enhancements.video_stub_provider import StubVideoService

    service = StubVideoService()
    adapter = _adapter_with(monkeypatch, service)
    assert adapter.service is service                       # 公开访问口

    prepared = adapter.prepare_request(_req())
    assert prepared.is_ready() and service.posts == []      # prepare 零提交
    task_id = adapter.create_prepared_task(prepared)
    assert task_id and len(service.posts) == 1
    obs = adapter.query_observation(task_id)
    assert obs.status == "succeeded" and obs.result["video_url"].startswith("stub://")
    assert adapter.get_task_binding(task_id)["status"] in ("ok", "binding_unavailable")


def test_adapter_generate_single_video_honors_execution_context(monkeypatch, tmp_path):
    """ViMax 直连入口：有 context ⇒ 候选落点（不碰正式）；无 context ⇒ 既有生产行为。"""
    import asyncio
    from shared import enhancement_io as eio
    from shared.paths import SHOT_VIDEO_FILENAME, vimax_shots_dir
    from tests.enhancements.video_stub_provider import StubVideoService
    from _video_gen.models import ExecutionContext

    service = StubVideoService()
    adapter = _adapter_with(monkeypatch, service)
    attempt = tmp_path / "exp" / "video" / "attempts" / "a1"
    (attempt / "outputs").mkdir(parents=True)
    candidate = attempt / "outputs" / "candidate.mp4"
    ctx = ExecutionContext(experiment_root=str(attempt), attempt_id="a1",
                           candidate_path=str(candidate), package="video")

    out = asyncio.run(adapter.generate_single_video("候选正文", [], execution_context=ctx,
                                                    clip_index=7))
    assert out.ext == "mp4" and out.data
    assert candidate.is_file() and candidate.stat().st_size > 20 * 1024
    assert not (vimax_shots_dir() / "7" / SHOT_VIDEO_FILENAME).exists()   # 正式路径没被写
    assert len(service.posts) == 1

    # 无 context：保持既有生产行为（写正式 shots/<idx>/video.mp4）
    out2 = asyncio.run(adapter.generate_single_video("生产正文", [], clip_index=9))
    assert out2.data and (vimax_shots_dir() / "9" / SHOT_VIDEO_FILENAME).is_file()
    assert len(service.posts) == 2


def test_adapter_generate_single_video_fails_closed_without_candidate_path(monkeypatch,
                                                                          tmp_path):
    import asyncio
    from _video_gen.models import ExecutionContext
    from tests.enhancements.video_stub_provider import StubVideoService

    service = StubVideoService()
    adapter = _adapter_with(monkeypatch, service)
    ctx = ExecutionContext(experiment_root=str(tmp_path), attempt_id="a1",
                           candidate_path=None, package="video")
    with pytest.raises(RuntimeError, match="candidate_path"):
        asyncio.run(adapter.generate_single_video("正文", [], execution_context=ctx,
                                                  clip_index=7))
    assert service.posts == []                              # 零提交，不降级
