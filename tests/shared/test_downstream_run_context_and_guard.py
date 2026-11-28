# -*- coding: utf-8 -*-
"""下游 RunContext 注入与统一出站预算（2026-11-28 O3）.

覆盖：
1. `RunContext.from_env(env=...)` 纯函数读映射；`apply_overrides()` 作用域覆盖后逐键还原，
   同进程两套上下文零串扰（画幅 / 声音合同这类**调用时**读 env 的消费者）；
2. `read_only=True` 的物理写保护（章节目录内 JSON 写直接拒；受保护日志落 devnull；
   目录零变化），章外路径不受影响；
3. `OutboundGuard` 双层账本：逻辑 / 尝试上限为 0 时**在发送前** fail-closed
   （transport 一次都不调用）；生图与视觉 QC 两个真实下游通道按接线实测；
   视频 / TTS 的发送边界以接线断言锁住（其 provider 构造需要真实 key/模型队列）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import outbound_guard  # noqa: E402
from shared.run_context import RunContext, ReadOnlyViolation, guard_log_path  # noqa: E402

# ---------------------------------------------------------------------------
# 1) RunContext：纯函数构造 + 作用域覆盖 + 零串扰
# ---------------------------------------------------------------------------
def test_from_env_reads_an_explicit_mapping_without_touching_os_environ():
    mapping = {"NOVEL_CHAPTER": "3", "OUTPUT_BASE": "/tmp/out", "VIDEO_SIZE": "2560*1440",
               "RENDER_FPS": "30", "DURATION_MIN_SEC": "45", "DURATION_MAX_SEC": "80",
               "VIDEO_STYLE": "anime", "STEP01_READ_ONLY": "1"}
    before = dict(os.environ)
    ctx = RunContext.from_env(env=mapping)
    assert ctx.chapter == 3 and ctx.output_base == "/tmp/out"
    assert ctx.video_size == "2560*1440" and ctx.merge_fps == 30
    assert (ctx.duration_min_sec, ctx.duration_max_sec) == (45.0, 80.0)
    assert ctx.video_style == "anime" and ctx.read_only is True
    assert dict(os.environ) == before, "from_env 不得写进程环境"


def test_apply_overrides_restores_keys_and_does_not_cross_talk(monkeypatch):
    from shared.audio_policy import resolve_audio_policy
    from shared.style_prefix import frame_canvas_ratio

    monkeypatch.setenv("VIDEO_SIZE", "1440*2560")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "0")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "tts_mix")
    assert frame_canvas_ratio() == "9:16"

    with RunContext(video_size="2560*1440", read_only=True).apply_overrides():
        assert frame_canvas_ratio() == "16:9"
        with RunContext(video_size="1280*1280").apply_overrides():
            assert frame_canvas_ratio() == "1:1"
        assert frame_canvas_ratio() == "16:9", "嵌套退出后应回到外层上下文"

    # 退出后逐键还原：画幅与声音合同都回到环境原值
    assert frame_canvas_ratio() == "9:16"
    assert resolve_audio_policy().effective_merge_mode == "tts_mix"
    with RunContext(video_size="2560*1440", video_style="anime").apply_overrides():
        policy = resolve_audio_policy()
        assert policy.effective_merge_mode == "tts_mix"     # 声音合同不随画幅变
    assert frame_canvas_ratio() == "9:16"


def test_apply_env_is_idempotent_and_projects_only_set_fields():
    keys = ("VIDEO_SIZE", "VIDEO_STYLE", "NOVEL_CHAPTER", "RENDER_FPS")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        ctx = RunContext(chapter=2, video_size="2560*1440", video_style="oil_painting")
        first = ctx.apply_env()
        second = ctx.apply_env()
        assert first == second
        assert os.environ["VIDEO_SIZE"] == "2560*1440"
        assert os.environ["VIDEO_STYLE"] == "oil_painting"
        assert os.environ["NOVEL_CHAPTER"] == "2"
        # 未设置 merge_fps 时不覆盖 RENDER_FPS（留空 = 不投影）
        assert "RENDER_FPS" not in first
    finally:
        # apply_env 是**进程级**投影；用例必须自己还原，否则会污染后续用例的 env
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_chapter_dir_override_wins(tmp_path):
    ch = tmp_path / "chapter_001"
    ch.mkdir()
    ctx = RunContext(chapter_dir_override=str(ch))
    assert ctx.chapter_dir() == ch


# ---------------------------------------------------------------------------
# 2) read_only 物理写保护
# ---------------------------------------------------------------------------
def test_read_only_blocks_writes_inside_chapter_and_silences_log(tmp_path):
    from shared.json_io import atomic_dump_json

    ch = tmp_path / "chapter_001"
    ch.mkdir()
    ctx = RunContext(chapter_dir_override=str(ch), read_only=True)
    before = (sorted(p.name for p in ch.iterdir()), ch.stat().st_mtime_ns)
    with ctx.activate():
        with pytest.raises(ReadOnlyViolation):
            atomic_dump_json(ch / "probe.json", {"x": 1})
        assert guard_log_path(ch / "step01.log") == Path(os.devnull)
        # 章目录之外的路径不受只读保护（工具自身的工作区）
        atomic_dump_json(tmp_path / "outside.json", {"ok": True})
    after = (sorted(p.name for p in ch.iterdir()), ch.stat().st_mtime_ns)
    assert after == before
    assert (tmp_path / "outside.json").is_file()


# ---------------------------------------------------------------------------
# 3) 统一出站预算
# ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return {"status_code": 200}


def test_guard_blocks_network_attempt_before_transport():
    transport = _Recorder()
    guard = outbound_guard.OutboundGuard(transport=transport, phase_attempts={"x": 0})
    with pytest.raises(outbound_guard.SendBlocked):
        guard("http://example.invalid", phase="x")
    assert transport.calls == [], "触顶必须在发出字节之前拦截"
    assert guard.snapshot()["http_attempts"] == {}


def test_guard_blocks_logical_call_at_zero_phase_cap():
    guard = outbound_guard.OutboundGuard(phase_logical={"image_gen": 0})
    with pytest.raises(outbound_guard.SendBlocked):
        guard.begin("image_gen")


def test_total_zero_means_unlimited_by_contract():
    """既有合同：`total_logical=0` / `total_attempts=0` 表示**不限**（不是「禁止」）.

    要「一次都不许发」必须用阶段上限 0（`phase_logical={"x": 0}`）。
    """
    guard = outbound_guard.OutboundGuard(total_logical=0, total_attempts=0)
    assert guard.begin("image_gen") == "image_gen"
    transport = _Recorder()
    guard = outbound_guard.OutboundGuard(transport=transport, total_attempts=0)
    guard("http://example.invalid", phase="x")
    assert len(transport.calls) == 1


def test_module_level_helpers_are_noops_without_guard():
    """未安装守卫 = 零行为变化（既有调用点不受影响）."""
    outbound_guard.install(None)
    assert outbound_guard.begin_phase("tts") == ""
    assert outbound_guard.charge_attempt("tts") == 0


def test_image_generation_fails_closed_before_http(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_API_KEY", "fake-key")
    from _image_gen.models import ImageGenerationRequest
    from _image_gen.volcengine_provider import VolcengineImageService

    service = VolcengineImageService()
    req = ImageGenerationRequest(prompt="a frame", size="1440x2560", shot_index=1)
    guard = outbound_guard.OutboundGuard(phase_logical={"image_gen": 0})
    outbound_guard.install(guard)
    try:
        with pytest.raises(outbound_guard.SendBlocked):
            service.generate(req)
    finally:
        outbound_guard.install(None)
    assert guard.snapshot()["http_attempts"] == {}


def _tiny_png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (16, 16), (120, 90, 60)).save(path)
    return path


def test_vision_qc_fails_closed_before_http(tmp_path, monkeypatch):
    from shared import image_qc

    monkeypatch.setenv("AGENT_PLAN_TEXT_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("AGENT_PLAN_API_KEY", "fake-key")
    frame = _tiny_png(tmp_path / "frame.png")
    guard = outbound_guard.OutboundGuard(phase_logical={"vision_qc": 0})
    outbound_guard.install(guard)
    attempts_before = image_qc.get_qc_http_attempts()
    try:
        with pytest.raises(outbound_guard.SendBlocked):
            image_qc._opencode_vision_chat(frame, "描述这张图")
    finally:
        outbound_guard.install(None)
    assert image_qc.get_qc_http_attempts() == attempts_before, "未发出即不应记尝试"
    assert guard.snapshot()["http_attempts"] == {}


@pytest.mark.parametrize("relative,phase,marker", [
    ("_video_gen/volcengine_provider.py", "video_gen", 'charge_attempt("video_gen")'),
    ("_video_gen/agent_plan_provider.py", "video_gen", 'charge_attempt("video_gen")'),
    ("tts/synthesis_job.py", "tts", 'charge_attempt("tts")'),
    ("shared/image_qc.py", "vision_qc", 'charge_attempt("vision_qc")'),
    ("_image_gen/volcengine_provider.py", "image_gen", 'charge_attempt("image_gen")'),
])
def test_send_boundaries_are_wired_to_the_guard(relative, phase, marker):
    """接线断言：这些 provider 直接调 SDK/requests，必须在自己的发送边界记账."""
    text = (_ROOT / relative).read_text(encoding="utf-8")
    assert marker in text, f"{relative} 未接 {phase} 的出站账本"
    assert f'begin_phase("{phase}")' in text, f"{relative} 未登记逻辑调用（{phase}）"


# ---------------------------------------------------------------------------
# 4) 下游入口接受 ctx（不破坏既有调用）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("relative", [
    "step02a_frame_gen/main.py", "step02b_video_gen/main.py",
    "step03_tts_audio/main.py", "step04_merge/main.py",
])
def test_stage_main_accepts_optional_ctx(relative):
    """下游入口签名接受可选 `ctx`（静态检查，不 import——避免 import 期日志副作用）."""
    import ast

    tree = ast.parse((_ROOT / relative).read_text(encoding="utf-8"))
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    args = [arg.arg for arg in main.args.args]
    assert "ctx" in args, f"{relative} 的 main() 未接受 ctx"
    defaults = dict(zip(args[len(args) - len(main.args.defaults):],
                        main.args.defaults))
    assert isinstance(defaults.get("ctx"), ast.Constant)
    assert defaults["ctx"].value is None
