# -*- coding: utf-8 -*-
"""S05/A2：Step03 实测配音时长审计 → Step02b 生视频前超窗拦截（0 provider 调用）."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.paths import audio_file
from shared.voice_preparation import build_duration_audit
from step02b_video_gen import cli, main as step02b_main
from tests.integration.downstream_fixtures import (
    build_anonymous_story_pack,
    install_committed_chapter,
    install_step02a_acceptance,
    make_test_wav,
)


def _units_fixture(*, actual: float | None, window: float = 5.0,
                   declared: float = 4.6, unit_id: str = "shot-1",
                   shot_index: int = 1) -> list[dict]:
    return [{
        "unit_id": unit_id,
        "kind": "tts_dialogue",
        "shot_index": shot_index,
        "speaker": "甲",
        "text": "这是一句用于时长对账的匿名台词。",
        "declared_sec": declared,
        "actual_sec": actual,
        "available_window_sec": window,
        "state": "confirmed" if actual is not None else "prepared",
        "measurement": "ffprobe" if actual is not None else "audio_missing",
        "audio_path": "",
    }]


def _audit_for(script: dict, units: list[dict], *, chapter: int) -> dict:
    """构造**身份完整**的审计（scope + script_revision 与本次稿一致）."""
    return build_duration_audit(
        units, script=script,
        scope={"chapter": chapter, "book_index": 1, "book_name": "匿名测试卷"},
        script_revision=script.get("script_revision") or "",
        audio_mode="tts_mix")


def _script(shot_sec: float = 5.0) -> dict:
    return {
        "characters": [{"identifier": "甲", "gender": "male", "age_range": "young"}],
        "shots": [{"index": 1, "duration_sec": shot_sec, "narration_segment": "甲道：“好。”"}],
        "hook": None,
    }


def test_duration_audit_projects_measured_overflow():
    audit = build_duration_audit(_units_fixture(actual=6.2), script=_script(5.0),
                                 script_revision="rev", audio_mode="tts_mix")
    assert audit["schema"] == "step03_duration_audit.v1"
    shot = audit["shots"]["1"]
    assert shot["measured"] is True
    assert abs(shot["audio_need_sec"] - 6.2) < 1e-6
    assert abs(shot["overflow_sec"] - 1.2) < 1e-6      # 计划 A2 验收读数
    assert abs(shot["recommended_shot_dur"] - 6.2) < 1e-6
    unit = audit["units"][0]
    assert unit["state"] == "confirmed" and unit["measurement"] == "ffprobe"
    assert audit["summary"]["measured_units"] == 1
    assert audit["summary"]["overflow_shot_count"] == 1
    assert abs(audit["summary"]["max_overflow_sec"] - 1.2) < 1e-6
    assert audit["provider_calls"] == 0


def test_unmeasured_unit_is_not_overflow_evidence():
    audit = build_duration_audit(_units_fixture(actual=None), script=_script(5.0))
    shot = audit["shots"]["1"]
    assert shot["audio_need_sec"] is None
    assert shot["overflow_sec"] is None
    assert shot["measured"] is False
    assert audit["summary"]["measured_units"] == 0
    assert audit["summary"]["overflow_shot_count"] == 0


def _chapter(tmp_path: Path, monkeypatch, *, chapter: int = 12) -> dict:
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_NAME", "匿名样本作品")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "匿名测试卷")
    monkeypatch.setenv("NOVEL_CHAPTER", str(chapter))
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")
    monkeypatch.setenv("VOLCENGINE_T2V_MODEL", "doubao-seedance-1-0-pro-250528")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    monkeypatch.setenv("VOLCENGINE_API_KEY", "test-volc-key")
    pack = build_anonymous_story_pack("compact", chapter=chapter)
    ch_dir = (tmp_path / "out" / "匿名样本作品" / "part_01_匿名测试卷"
              / ("chapter_%03d" % chapter))
    installed = install_committed_chapter(ch_dir, pack)
    return {"ch_dir": ch_dir, "installed": installed, "outline": pack["outline"]}


def pack_outline(ctx: dict) -> dict:
    return ctx["outline"]


def test_step03_entry_writes_measured_duration_audit(tmp_path, monkeypatch):
    """真实入口 `_write_duration_audit`：ffprobe 实测 WAV → 审计写盘、超窗可读."""
    from step03_tts_audio.main import _write_duration_audit

    ctx = _chapter(tmp_path, monkeypatch, chapter=13)
    ch_dir = ctx["ch_dir"]
    script = ctx["installed"]["script"]
    shot = script["shots"][0]
    target_audio = audio_file(int(shot["index"]))
    staging = make_test_wav(target_audio.with_suffix(".staging.wav"), 6.2)
    target_audio.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(target_audio)
    assert target_audio.is_file()
    unit = SimpleNamespace(unit_id="shot-%d" % int(shot["index"]), kind="dialogue",
                           source_index=int(shot["index"]),
                           text="甲道：“好。”", planned_window=None, voice_role="甲")
    audit = _write_duration_audit(
        script=script, gen_script=script, plan=pack_outline(ctx),
        scope={"chapter": 13, "book_index": 1, "book_name": "匿名测试卷"},
        revision=script.get("script_revision") or "rev", provider="dashscope_sdk",
        target_dir=ch_dir, audio_mode="tts_mix", units=[unit])
    written = json.loads((ch_dir / "step03_duration_audit.json").read_text(encoding="utf-8"))
    assert written["schema"] == "step03_duration_audit.v1"
    row = written["units"][0]
    assert row["state"] == "confirmed" and row["measurement"] == "ffprobe"
    assert abs(row["actual_wav_sec"] - 6.2) < 0.25
    shot_row = written["shots"][str(shot["index"])]
    assert shot_row["measured"] is True
    assert shot_row["overflow_sec"] > 0.5            # 6.2s 对白 > 该镜规划时长
    assert audit["summary"]["measured_units"] >= 1


def test_step02b_plan_blocks_audio_overflow_with_recommended_duration(tmp_path, monkeypatch):
    ctx = _chapter(tmp_path, monkeypatch, chapter=14)
    ch_dir, installed = ctx["ch_dir"], ctx["installed"]
    script = installed["script"]
    audit = _audit_for(script, _units_fixture(actual=6.2, window=5.0), chapter=14)
    shots_dir = ch_dir / "vimax_working" / "shots"
    plan = step02b_main.build_plan(
        script, shots_dir, only_indices={1}, chapter=14, provider="volcengine",
        model_id="doubao-seedance-1-0-pro-250528",
        scope={"chapter": 14, "book_index": 1, "book_name": "匿名测试卷"},
        duration_audit=audit)
    entry = plan["shots"][0]
    assert entry["verdict"] == "blocked" and entry["code"] == "audio_overflow"
    assert entry["audio_audit"]["audio_need_sec"] == pytest.approx(6.2, abs=1e-6)
    assert entry["recommended_shot_dur"] == pytest.approx(6.2, abs=1e-6)
    assert plan["audio_audit"]["source"] == "explicit"
    assert plan["audio_audit"]["overflow"] == ["1"]
    assert plan["network_calls"] == 0


def test_step02b_plan_rejects_foreign_audit_identity(tmp_path, monkeypatch):
    """旧章/旧稿的审计不得当本次实测：身份不符 ⇒ 拒绝，不产生超窗裁决."""
    ctx = _chapter(tmp_path, monkeypatch, chapter=17)
    ch_dir, installed = ctx["ch_dir"], ctx["installed"]
    script = installed["script"]
    stale = _audit_for(script, _units_fixture(actual=6.2, window=5.0), chapter=999)
    stale["script_revision"] = "stale-revision"
    plan = step02b_main.build_plan(
        script, ch_dir / "vimax_working" / "shots", only_indices={1}, chapter=17,
        provider="volcengine", model_id="doubao-seedance-1-0-pro-250528",
        scope={"chapter": 17, "book_index": 1, "book_name": "匿名测试卷"},
        duration_audit=stale)
    codes = [i.get("code") for i in plan["audio_audit"]["rejected"]]
    assert "audit_scope_mismatch" in codes
    assert "audit_revision_mismatch" in codes
    assert plan["audio_audit"]["source"] == "explicit"
    assert all(entry.get("code") != "audio_overflow" for entry in plan["shots"])


def test_step02b_plan_allows_within_tolerance(tmp_path, monkeypatch):
    ctx = _chapter(tmp_path, monkeypatch, chapter=15)
    ch_dir, installed = ctx["ch_dir"], ctx["installed"]
    script = installed["script"]
    planned = float(script["shots"][0].get("duration_sec") or 5.0)
    audit = _audit_for(script, _units_fixture(actual=planned + 0.1, window=planned),
                       chapter=15)
    plan = step02b_main.build_plan(
        script, ch_dir / "vimax_working" / "shots", only_indices={1}, chapter=15,
        provider="volcengine", model_id="doubao-seedance-1-0-pro-250528",
        scope={"chapter": 15, "book_index": 1, "book_name": "匿名测试卷"},
        duration_audit=audit)
    assert plan["shots"][0].get("code") != "audio_overflow"   # 容差内不因超窗阻断
    assert plan["audio_audit"]["overflow"] == []


def test_narration_uses_carrier_shots_not_index_number():
    """旁白 N2 承载在 5、6 镜：审计按承载窗口对账，不把 N2 当镜头 2."""
    unit = {
        "unit_id": "narration-N2", "kind": "narration", "shot_index": 5,
        "shot_indexes": [5, 6], "source_index": 2, "speaker": "Narrator",
        "text": "这是一条跨两镜的旁白。", "declared_sec": 8.0, "actual_sec": 9.5,
        "available_window_sec": 10.0, "state": "confirmed", "measurement": "ffprobe",
        "audio_path": "", "audio_sha256": "",
    }
    script = {"shots": [{"index": 5, "duration_sec": 5.0},
                        {"index": 6, "duration_sec": 5.0}]}
    audit = build_duration_audit([unit], script=script, scope={"chapter": 9},
                                 script_revision="rev")
    assert audit["shots"] == {}                       # 不折进任何单镜的 audio_need
    entry = audit["narrations"]["narration-N2"]
    assert entry["shot_indexes"] == [5, 6]
    assert entry["carrier_window_sec"] == 10.0
    assert entry["overflow_sec"] is None or entry["overflow_sec"] < 0
    row = audit["units"][0]
    assert row["shot_index"] is None          # 旁白不是"某镜的对白"
    assert row["shot_indexes"] == [5, 6]


def test_candidate_audit_reads_artifact_paths(tmp_path, monkeypatch):
    """候选审计按**本次产物**路径实测（不假定正式章目录的 audio_file）."""
    from step03_tts_audio.main import _write_duration_audit
    from tests.integration.downstream_fixtures import make_test_wav

    ctx = _chapter(tmp_path, monkeypatch, chapter=18)
    ch_dir = ctx["ch_dir"]
    script = ctx["installed"]["script"]
    shot = script["shots"][0]
    artifact = make_test_wav(tmp_path / "attempt" / "seg_01.wav", 3.0)
    unit = SimpleNamespace(unit_id="shot-%d" % int(shot["index"]), kind="dialogue",
                           source_index=int(shot["index"]), text="甲道：“好。”",
                           planned_window=None, voice_role="甲")
    audit = _write_duration_audit(
        script=script, gen_script=script, plan=pack_outline(ctx),
        scope={"chapter": 18, "book_index": 1, "book_name": "匿名测试卷"},
        revision=script.get("script_revision") or "", provider="dashscope_sdk",
        target_dir=tmp_path / "attempt", units=[unit],
        audio_paths={unit.unit_id: str(artifact)})
    row = audit["units"][0]
    assert row["state"] == "confirmed"
    assert row["audio_path"] == str(artifact)
    assert row["actual_wav_sec"] == pytest.approx(3.0, abs=0.2)


def test_step02b_plan_blocks_narration_overflow(tmp_path, monkeypatch):
    """跨镜旁白放不下 ⇒ plan 以 audio_overflow 阻断（独立于逐镜 need）."""
    ctx = _chapter(tmp_path, monkeypatch, chapter=19)
    ch_dir, installed = ctx["ch_dir"], ctx["installed"]
    script = installed["script"]
    unit = {"unit_id": "narration-N1", "kind": "narration", "shot_index": 1,
            "shot_indexes": [1, 2], "source_index": 1, "text": "旁白。",
            "actual_sec": 12.0, "available_window_sec": 10.0,
            "state": "confirmed", "measurement": "ffprobe", "audio_path": ""}
    audit = build_duration_audit([unit], script=script,
                                 scope={"chapter": 19, "book_index": 1,
                                        "book_name": "匿名测试卷"},
                                 script_revision=script.get("script_revision") or "")
    plan = step02b_main.build_plan(
        script, ch_dir / "vimax_working" / "shots", only_indices={1, 2},
        chapter=19, provider="volcengine",
        model_id="doubao-seedance-1-0-pro-250528",
        scope={"chapter": 19, "book_index": 1, "book_name": "匿名测试卷"},
        duration_audit=audit)
    narration_blocks = [e for e in plan["shots"] if e.get("code") == "audio_overflow"
                        and e.get("narration")]
    assert narration_blocks, plan["shots"]
    assert narration_blocks[0]["narration"]["unit_id"] == "narration-N1"
    assert narration_blocks[0]["narration"]["shot_indexes"] == [1, 2]


def test_step02b_generation_entry_rejects_before_any_post(tmp_path, monkeypatch):
    """真实入口：审计超窗 ⇒ 生视频前退出码 2（不进入任何提交）。"""
    ctx = _chapter(tmp_path, monkeypatch, chapter=16)
    ch_dir, installed = ctx["ch_dir"], ctx["installed"]
    script = installed["script"]
    install_step02a_acceptance(ch_dir, script, shot_policies={1: "auto", 2: "first_only"})
    planned = float(script["shots"][0].get("duration_sec") or 5.0)
    audit = _audit_for(script, _units_fixture(actual=planned + 1.2, window=planned,
                                              shot_index=1), chapter=16)
    (ch_dir / "step03_duration_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    cand_root = ch_dir / "_enhancement" / "exp_s05" / "video"
    opts = cli.parse_args([
        "--chapter", "16", "--shots", "1", "--candidate-root", str(cand_root),
    ])
    with pytest.raises(SystemExit) as exc_info:
        step02b_main.main(opts)
    assert exc_info.value.code == 2
    assert not any(cand_root.rglob("*.mp4"))       # 没有任何生成产物
