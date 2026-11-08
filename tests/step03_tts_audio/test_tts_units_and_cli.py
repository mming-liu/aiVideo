# -*- coding: utf-8 -*-
"""step03 接线：声音单元身份 / 说话人识别用 raw / 候选隔离 / 入口早期解析（§T1、§T4、§15）.

判别性要求：把「说话人识别用 raw_for_speaker」换成 text、或让候选运行落到生产目录，
对应用例必须转红。
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tts_stub import StubProvider                                       # noqa: E402
from shared.paths import chapter_dir, global_dir                         # noqa: E402
from step03_tts_audio import cli as stage_cli                            # noqa: E402
from step03_tts_audio import shot_tts as st                              # noqa: E402
from tts import voice_registry as vreg                                   # noqa: E402
from tts.service import register_capabilities                            # noqa: E402
from tts.synthesis_job import SynthesisError, TtsCandidateContext        # noqa: E402

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")

_H1 = "拿主意？拿什么主意？"
_N1 = "四人围桌喝茶。"


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", str(tmp_path / "voice_registry.json"))
    register_capabilities(StubProvider.PROVIDER_NAME, StubProvider().capabilities())
    vreg.reset_registry_cache()
    yield
    vreg.reset_registry_cache()


@pytest.fixture(autouse=True)
def _restore_scope_env(monkeypatch):
    """还原 `cli.bootstrap()` 直接写进 `os.environ` 的章号.

    bootstrap 按设计调 run_scope.apply 写 env，本文件又显式断言这个副作用；
    不还原就会把 `NOVEL_CHAPTER=2/4` 泄漏给后续用例。2026-11-08 全量跑实证：
    step04_merge 在 import 期按泄漏值缓存 `CHAPTER_INDEX=2`，章号一致性门把
    合法的 chapter_001 副本判成错章。用 monkeypatch 登记原值，teardown 恢复。
    """
    import os

    for key, default in (("NOVEL_CHAPTER", "1"), ("BOOK_INDEX", "1")):
        monkeypatch.setenv(key, os.environ.get(key, default))
    yield


def _script(seg: str, *, duration: float = 8.0) -> dict:
    plan = {
        "shot_beats": [
            "节拍：张十三把话说了｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _H1,
            "节拍：四人围桌喝茶｜本镜信息目的：交代｜原文依据：“%s”｜载体：旁白1" % _N1,
        ],
        "dialogue_lines": [_H1],
        "narration_lines": [{"text": _N1, "source": _N1, "fact_refs": []}],
    }
    shots = [{"index": 1, "narration_segment": seg, "duration_sec": duration,
              "main_subject": "张十三", "plan_refs": {"beats": [1, 2], "lines": [1]}}]
    return {"chapter_outline": plan, "shots": shots,
            "characters": [{"identifier": "张十三", "gender": "male", "age_range": "young"}]}


# ---------------------------------------------------------------- 声线归属

def test_speaker_is_detected_from_the_raw_text_not_the_stripped_line(tmp_path):
    """§15：说话人识别用 `raw_for_speaker`——用正文会让 voice 分叉到旁白声线."""
    script = _script('张十三道：“%s”' % _H1)
    units = st.build_speech_units(script, scope={"chapter": 1}, script_revision="rev-1",
                                  provider_name=StubProvider.PROVIDER_NAME)
    dlg = [u for u in units if u.kind == "dialogue"][0]
    from shared.paths import novel_name
    reg = vreg.get_registry()
    work = novel_name() or "default"
    profile = reg.profiles[work]["张十三"]
    assert dlg.speaker == profile["speaker"]
    assert dlg.voice_profile_id == profile["voice_profile_id"]
    assert dlg.text == _H1                        # 合成文本仍是剥掉前缀的台词
    # 旁白有自己的**另一个**稳定声线（不静默回退统一默认声线）
    narr = [u for u in units if u.kind == "narration"][0]
    assert narr.speaker == reg.profiles[work]["Narrator"]["speaker"]
    assert narr.speaker != dlg.speaker


def test_new_profile_keeps_model_and_voice_ids_separate(tmp_path):
    script = _script('张十三道：“%s”' % _H1)
    units = st.build_speech_units(script, scope={"chapter": 1}, script_revision="rev-1",
                                  provider_name=StubProvider.PROVIDER_NAME)
    dlg = [u for u in units if u.kind == "dialogue"][0]
    assert dlg.model_id == "stub-model"           # 模型 ID
    assert dlg.speaker.startswith("stub-")        # 音色 ID（分开）
    assert dlg.model_id != dlg.speaker


def test_legacy_provider_keeps_the_sambert_pool_as_model_id(tmp_path):
    """旧 provider（无独立 speaker 概念）⇒ 沿用旧池，模型 ID = 音色 ID（它的请求语义）."""
    script = _script('张十三道：“%s”' % _H1)
    units = st.build_speech_units(script, scope={"chapter": 1}, script_revision="rev-1",
                                  provider_name="dashscope_sdk")
    dlg = [u for u in units if u.kind == "dialogue"][0]
    assert dlg.model_id.startswith("sambert-")
    assert dlg.speaker == ""                      # 旧 provider 没有独立 speaker
    assert dlg.params_strict is True               # 规划默认严格


# ---------------------------------------------------------------- 候选隔离

def test_candidate_run_writes_only_under_the_candidate_root(tmp_path):
    script = _script('张十三道：“%s”' % _H1)
    cand = tmp_path / "tts"
    provider = StubProvider()
    with TtsCandidateContext.create(str(cand), attempt_id="a-1", scope={"chapter": 1},
                                    script_revision="rev-1", source="fixture") as ctx:
        units = st.build_speech_units(script, scope={"chapter": 1},
                                      script_revision="rev-1",
                                      provider_name=StubProvider.PROVIDER_NAME)
        out = st.gen_shot_tts(script["shots"][0], tmp_path / "unused", provider=provider,
                              voice_registry=vreg.get_registry(),
                              characters=script["characters"],
                              plan=script["chapter_outline"], scope={"chapter": 1},
                              script_revision="rev-1", candidate_context=ctx)
    assert out is not None and str(cand) in out
    from shared.audio_identity import read_identity
    assert read_identity(out) is not None          # 新路径写 v2 身份侧车
    # 生产目录**没有**被写（日志/音频都不在正式 chapter 下）
    assert not chapter_dir(1).joinpath("step03.log").exists()
    assert not (tmp_path / "unused").exists()
    # 注册表隔离：用的是隔离路径（不是生产 global/voice_registry.json）
    assert vreg.get_registry().registry_path == tmp_path / "voice_registry.json"
    prod = global_dir() / "voice_registry.json"
    if prod.is_file():
        assert "profiles" not in json.loads(prod.read_text(encoding="utf-8"))


def test_candidate_mode_is_strict_about_unsupported_instruction(tmp_path):
    """候选（新路径）严格：provider 不支持的能力必须被拒绝，而不是"记一笔继续"."""
    from tts.models import SpeechUnit
    unit = SpeechUnit(unit_id="shot-1", kind="dialogue", text="短句。",
                      scope={"chapter": 1}, script_revision="rev-1",
                      voice_profile_id="vp", provider=StubProvider.PROVIDER_NAME,
                      model_id="stub-model", speaker="stub-role-a", instruction="愤怒",
                      mapping_version="v1", params_strict=True)
    with pytest.raises(SynthesisError) as ei:
        st._unit_artifact(unit, StubProvider(), None, tmp_path / "x.wav")
    assert ei.value.code == "unsupported_parameter" and ei.value.field_path == "instruction"


# ---------------------------------------------------------------- gen_single_tts（旧入口不再截断）

def test_gen_single_tts_no_longer_truncates(tmp_path):
    """§T2：移除 300 字截断——长文本按 provider 能力**无损**分段."""
    text = "".join("第%d句话在这里。" % i for i in range(1, 41))
    assert len(text) > 300
    provider = StubProvider(cap=40)
    out = st.gen_single_tts(text, tmp_path / "legacy.mp3", provider=provider,
                            voice_config={"id": "stub-role-a", "rate": "1.0"})
    assert Path(out).is_file()
    assert len(provider.calls) > 1
    assert "".join(c["text"] for c in provider.calls) == text
    from shared.audio_identity import read_identity
    assert read_identity(out)["text"] == text


def test_legacy_provider_path_still_writes_the_voice_sidecar(tmp_path):
    """旧 provider 正常对照：`.voice` 边车 + 旧签名口径**逐字不变**（现有 step04 消费者）.

    step04（9-30 版）对旁白轨「缺边车即失败」：迁移期不许把这条兼容线断掉。
    """
    from shared.audio_identity import (identity_path, read_signature, speech_signature,
                                       verify_identity)
    script = _script('张十三道：“%s”' % _H1)
    provider = StubProvider(supports_speaker=False)
    out = st.gen_shot_tts(script["shots"][0], tmp_path / "audio", provider=provider,
                          characters=script["characters"],
                          plan=script["chapter_outline"], scope={"chapter": 1},
                          script_revision="rev-1")
    assert out is not None
    assert Path(out).with_suffix(".voice").is_file()          # 旧边车仍在
    assert identity_path(out).is_file()                       # 新身份同时写
    assert verify_identity(out) == ""
    voice_id = provider.calls[0]["model"]
    assert voice_id.startswith("sambert-")                    # 旧池：模型 ID = 音色 ID
    assert read_signature(out) == speech_signature(_H1, {"id": voice_id})
    assert provider.calls[0]["text"] == _H1


# ---------------------------------------------------------------- CLI 早期解析

def test_cli_bootstrap_registers_scope_and_candidate_isolation(tmp_path, monkeypatch):
    root = tmp_path / "cand"
    opts = stage_cli.bootstrap("t", ["--chapter", "4", "--plan-only",
                                     "--candidate-root", str(root),
                                     "--video-candidates", str(tmp_path / "v.json")])
    assert opts.chapter == 4 and opts.plan_only is True
    import os as _os
    assert _os.environ["NOVEL_CHAPTER"] == "4"
    assert stage_cli.candidate_root_from_env() == str(root)
    assert stage_cli.plan_only_from_env() is True
    assert opts.attempt_id and stage_cli.attempt_id_from_env() == opts.attempt_id
    import os
    assert os.environ["TTS_VOICE_REGISTRY_PATH"] == str(root / "voice_registry.json")


def test_cli_without_candidate_args_keeps_the_old_entry(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", "keep-me")
    opts = stage_cli.bootstrap("t", ["--chapter", "2"])
    assert opts.candidate_root is None and opts.plan_only is False
    assert stage_cli.candidate_root_from_env() is None
    assert stage_cli.plan_only_from_env() is False
    assert os.environ.get("TTS_VOICE_REGISTRY_PATH") == "keep-me"


def test_cli_unknown_argument_fails_loudly():
    with pytest.raises(SystemExit) as ei:
        stage_cli.parse_argv(["--chapter", "2", "--shot", "3"])
    assert ei.value.code == 2


# ---------------------------------------------------------------- 入口：--plan-only

def test_main_plan_only_writes_units_and_calls_no_provider(tmp_path, monkeypatch):
    """真实入口（main）的 `--plan-only` 分支：只产 SpeechUnits，不碰 provider/生产目录."""
    import os
    script = _script('张十三道：“%s”' % _H1)
    script_path = tmp_path / "script_result.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    cand = tmp_path / "cand"
    monkeypatch.setenv("STEP03_CANDIDATE_ROOT", str(cand))
    monkeypatch.setenv("STEP03_ATTEMPT_ID", "a-plan")
    monkeypatch.setenv("TTS_VOICE_REGISTRY_PATH", str(cand / "voice_registry.json"))
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    vreg.reset_registry_cache()

    import shared.quality_gate as qg
    monkeypatch.setattr(qg, "require_gate", lambda *a, **k: None)
    monkeypatch.setattr(qg, "verify_step01_commit_manifest", lambda *a, **k: None)
    import step03_tts_audio.main as stage3
    stage3 = importlib.reload(stage3)
    monkeypatch.setattr(stage3, "SCRIPT_FILE", script_path)
    monkeypatch.setattr(st, "create_provider",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("plan-only 不得调用 provider")))

    stage3.main(stage_cli.StageOptions(candidate_root=str(cand), plan_only=True,
                                       scope={"chapter": 1}))
    plan_file = cand / "plan" / "speech_units.json"
    assert plan_file.is_file()
    payload = json.loads(plan_file.read_text(encoding="utf-8"))
    assert payload["n_units"] == 2
    # 只规划：不落媒体、不封 manifest（日志目录不算产物）
    attempt = cand / "attempts" / "a-plan"
    assert not (attempt / "manifest.json").exists()
    assert not (attempt / "outputs").exists()
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").rglob("*.mp3"))
