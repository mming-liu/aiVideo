# -*- coding: utf-8 -*-
"""S03 (V1): Per-shot mixed media policy threading without global env mutation."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _video_gen import agent_plan_provider, volcengine_provider
from _video_gen.models import VideoGenerationResult
from shared.quality_gate import QualityGateError
from shared.video_media_plan import (
    VALID_MEDIA_POLICIES,
    active_media_policy,
    resolve_shot_media_policy,
)
from step02_video_gen import submit_poll
from step02b_video_gen import cli, main as step02b_main
from tests._content_binding import assert_clean
from tests.integration.downstream_fixtures import (
    build_anonymous_story_pack,
    install_committed_chapter,
    install_step02a_acceptance,
    make_test_mp4,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _setup_mixed_chapter(tmp_path: Path, monkeypatch, *, chapter: int = 8, policies: dict | None = None):
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
    monkeypatch.setattr(volcengine_provider, "VOLCENGINE_API_KEY", "test-volc-key")
    monkeypatch.setattr(volcengine_provider.config, "validate", lambda: True)
    monkeypatch.setattr(agent_plan_provider, "AGENT_PLAN_API_KEY", "test-ap-key")
    monkeypatch.setattr(agent_plan_provider.config, "validate", lambda: True)

    pack = build_anonymous_story_pack("compact", chapter=chapter)
    ch_dir = tmp_path / "out" / "匿名样本作品" / "part_01_匿名测试卷" / f"chapter_{chapter:03d}"
    installed = install_committed_chapter(ch_dir, pack)

    qa = None
    if policies is not None:
        qa_path = install_step02a_acceptance(ch_dir, installed["script"], shot_policies=policies)
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    return ch_dir, installed, qa


def test_s03_policy_source_keeps_valid_policies_and_fails_closed():
    assert "step02a" not in VALID_MEDIA_POLICIES
    assert "from_step02a" not in VALID_MEDIA_POLICIES

    with pytest.raises(ValueError, match="step02a_quality.json"):
        resolve_shot_media_policy(1, qa_report=None, policy_source="step02a")

    with pytest.raises(ValueError, match="缺少 shot 2"):
        resolve_shot_media_policy(
            2,
            qa_report={"scope": {"policies": {"1": "auto"}}},
            policy_source="step02a",
        )

    with pytest.raises(ValueError, match="非法"):
        resolve_shot_media_policy(
            1,
            qa_report={"scope": {"policies": {"1": "from_step02a"}}},
            policy_source="step02a",
        )

    assert resolve_shot_media_policy(
        1, qa_report={"scope": {"policies": {"1": "auto", "2": "first_only"}}}, policy_source="step02a",
    ) == "auto"
    assert resolve_shot_media_policy(
        2, qa_report={"scope": {"policies": {"1": "auto", "2": "first_only"}}}, policy_source="step02a",
    ) == "first_only"


def test_s03_build_plan_single_command_mixed_policies(tmp_path, monkeypatch):
    ch_dir, installed, qa = _setup_mixed_chapter(
        tmp_path, monkeypatch, chapter=8, policies={1: "auto", 2: "first_only"},
    )
    script = installed["script"]
    shots_dir = ch_dir / "vimax_working" / "shots"
    plan = step02b_main.build_plan(
        script,
        shots_dir,
        only_indices={1, 2},
        chapter=8,
        provider="volcengine",
        model_id="doubao-seedance-1-0-pro-250528",
        scope={"chapter": 8, "book_index": 1, "book_name": "匿名测试卷"},
        policy_source="step02a",
        qa_report=qa,
    )
    assert plan["network_calls"] == 0
    assert plan["ready"] == 2
    assert plan["blocked"] == 0
    by_idx = {int(s["shot_index"]): s for s in plan["shots"]}
    assert by_idx[1]["media_policy"] == "auto"
    assert by_idx[2]["media_policy"] == "first_only"
    roles_1 = [r["role"] for r in by_idx[1]["request"]["references"]]
    roles_2 = [r["role"] for r in by_idx[2]["request"]["references"]]
    assert roles_1 == ["first_frame", "last_frame"]
    assert roles_2 == ["first_frame"]
    assert os.getenv("VIDEO_MEDIA_POLICY") == "auto"

    # Changing Shot 1's policy (auto -> first_only) without changing frame bytes MUST change request_key.
    plan_fo = step02b_main.build_plan(
        script,
        shots_dir,
        only_indices={1},
        chapter=8,
        provider="volcengine",
        model_id="doubao-seedance-1-0-pro-250528",
        scope={"chapter": 8, "book_index": 1, "book_name": "匿名测试卷"},
        policy_source="step02a",
        qa_report={"scope": {"policies": {"1": "first_only"}}},
    )
    assert plan_fo["ready"] == 1
    assert plan_fo["shots"][0]["request_key"] != by_idx[1]["request_key"]


def test_s03_cli_plan_only_and_candidate_run_with_mixed_policies(tmp_path, monkeypatch):
    ch_dir, installed, _ = _setup_mixed_chapter(
        tmp_path, monkeypatch, chapter=9, policies={1: "auto", 2: "first_only"},
    )
    monkeypatch.setenv("VIDEO_QC_ENABLED", "0")
    monkeypatch.setattr(submit_poll, "INITIAL_POLL_DELAY", 0.0)
    monkeypatch.setattr(submit_poll, "POLL_BASE_INTERVAL", 0.01)

    cand_root = ch_dir / "_enhancement" / "exp_s03" / "video"
    opts_plan = cli.parse_args([
        "--chapter", "9", "--shots", "1,2", "--plan-only",
        "--candidate-root", str(cand_root), "--media-policy-source", "step02a",
    ])
    with pytest.raises(SystemExit) as exc_info:
        step02b_main.main(opts_plan)
    assert exc_info.value.code == 0
    plans = sorted((cand_root / "plans").glob("plan_*.json"))
    assert len(plans) == 1
    plan_data = json.loads(plans[0].read_text(encoding="utf-8"))
    assert plan_data["ready"] == 2
    assert [s["media_policy"] for s in plan_data["shots"]] == ["auto", "first_only"]

    # Now drive a real single-command candidate run with stub adapter recording submitted materials per shot.
    recorded: dict[int, dict] = {}
    good_mp4 = make_test_mp4(tmp_path / "good_s03.mp4", 5.0)

    class _PolicyCapturingSvc(volcengine_provider.VolcengineVideoService):
        def create_prepared_task(self, prepared, context=None):
            idx = int((prepared.source or {}).get("shot_index") or 0)
            recorded[idx] = {
                "media_mode": prepared.media_mode,
                "media_policy": (prepared.source or {}).get("media_policy"),
                "roles": [m.role for m in prepared.materials],
                "request_key": prepared.request_key,
                "env_policy_during_submit": active_media_policy(),
            }
            return f"task_s03_{idx}"

        def restore_task_binding(self, task_id, binding):
            return True

    class _Adapter:
        def __init__(self):
            self._svc = _PolicyCapturingSvc()
            self.service = self._svc

        def get_key_index(self):
            return 0

        def get_task_binding(self, task_id):
            return {"status": "ok", "key_slot": 0, "provider": "volcengine"}

        async def fetch_result(self, req, task_id):
            out = Path(req.candidate_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(good_mp4.read_bytes())
            return VideoGenerationResult(clip_index=req.clip_index, success=True, save_path=str(out))

    monkeypatch.setattr(step02b_main, "VideoGeneratorAiVideoAdapter", _Adapter)

    opts_run = cli.parse_args([
        "--chapter", "9", "--shots", "1,2",
        "--candidate-root", str(cand_root), "--media-policy-source", "step02a",
    ])
    with pytest.raises(SystemExit) as run_exc:
        step02b_main.main(opts_run)
    assert run_exc.value.code == 0
    assert recorded[1]["roles"] == ["first_frame", "last_frame"]
    assert recorded[1]["media_mode"] == "first_last"
    assert recorded[2]["roles"] == ["first_frame"]
    assert recorded[2]["media_mode"] == "first"
    assert recorded[1]["media_policy"] == "auto"
    assert recorded[2]["media_policy"] == "first_only"
    assert recorded[1]["env_policy_during_submit"] == "auto"
    assert recorded[2]["env_policy_during_submit"] == "auto"


def test_s03_rejects_missing_or_mismatched_step02a_report(tmp_path, monkeypatch):
    ch_dir, installed, qa = _setup_mixed_chapter(
        tmp_path, monkeypatch, chapter=10, policies={1: "auto"},
    )
    cand_root = ch_dir / "_enhancement" / "exp_s03_bad" / "video"
    opts_plan = cli.parse_args([
        "--chapter", "10", "--shots", "1,2", "--plan-only",
        "--candidate-root", str(cand_root), "--media-policy-source", "step02a",
    ])
    with pytest.raises(SystemExit) as exc_info:
        step02b_main.main(opts_plan)
    assert exc_info.value.code != 0

    with pytest.raises(QualityGateError):
        step02b_main._assert_step02a_scope(qa, {1, 2}, installed["script"], policy_source="step02a")

    (ch_dir / "step02a_quality.json").unlink()
    with pytest.raises(SystemExit) as exc_missing:
        step02b_main.main(opts_plan)
    assert exc_missing.value.code != 0


def test_s03_rejects_step02a_policy_source_outside_image_mode(tmp_path, monkeypatch):
    """逐镜策略只来自 step02a 帧验收报告 ⇒ 非 image 通道显式要求它必须启动即拒。"""
    ch_dir, _installed, _qa = _setup_mixed_chapter(
        tmp_path, monkeypatch, chapter=11, policies={1: "auto"},
    )
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    cand_root = ch_dir / "_enhancement" / "exp_s03_r2v" / "video"
    opts = cli.parse_args([
        "--chapter", "11", "--plan-only", "--candidate-root", str(cand_root),
        "--media-policy-source", "step02a",
    ])
    with pytest.raises(SystemExit) as exc_info:
        step02b_main.main(opts)
    assert exc_info.value.code == 2


def test_s03_files_content_agnostic():
    rel = "tests/step02b_video_gen/test_s03_mixed_shot_policies.py"
    assert_clean((REPO_ROOT / rel).read_text(encoding="utf-8"), rel)
