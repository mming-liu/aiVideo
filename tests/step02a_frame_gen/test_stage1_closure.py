# -*- coding: utf-8 -*-
"""Stage 1 closure verification: P1-1b, P1-2b, P1-4b, P1-5 stop-loss, and 4.1 ledger schema."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _block_net():
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


def _noisy_img(w=256, h=256, seed=3) -> Image.Image:
    import random
    rng = random.Random(seed)
    return Image.frombytes("RGB", (w, h), bytes(rng.randrange(256) for _ in range(w * h * 3)))


def test_p1_1_execution_plan_image_model_matches_active_config(monkeypatch):
    from shared import config as cfg
    from step02a_frame_gen import execution_plan as ep

    monkeypatch.setattr(cfg, "IMAGE_PROVIDER", "volcengine")
    monkeypatch.setattr(cfg, "VOLCENGINE_T2I_MODEL", "doubao-seedream-5-0-pro-260628")
    assert ep._image_model() == "volcengine:doubao-seedream-5-0-pro-260628"


def test_p1_2_qc_prompt_and_expected_persons_support_keyframes_and_aliases():
    from shared import image_qc as iq

    chars = [
        {"identifier": "角色甲", "aliases": ["掌柜"]},
        {"identifier": "角色乙", "aliases": ["伙计"]},
    ]
    shot = {
        "index": 1,
        "location": "作坊",
        "ff_desc": "掌柜擦拭铜秤。",
        "lf_desc": "伙计提灯离开。",
        "keyframes": [{"desc": "两人对看一眼。", "t": 0.5}],
        "char_layout": [{"name": "角色甲"}, {"name": "角色乙"}],
        "frame_layouts": {
            "ff": [{"name": "掌柜", "on_screen": True}, {"name": "角色乙", "on_screen": False}],
            "lf": [{"name": "角色甲", "on_screen": False}, {"name": "伙计", "on_screen": True}],
            "kfs": [[{"name": "掌柜", "on_screen": True}, {"name": "伙计", "on_screen": True}]],
        },
    }
    assert iq._expected_persons(shot, "first_frame", chars) == 1
    assert iq._expected_persons(shot, "keyframe", chars, kf_index=0) == 2

    ff_qc_prompt = iq.build_qc_prompt(shot, "first_frame", chars)
    kf_qc_prompt = iq.build_qc_prompt(shot, "keyframe", chars, kf_index=0)
    assert "本镜角色名单共 1 人（角色甲）" in ff_qc_prompt
    assert "本镜角色名单共 2 人（角色甲、角色乙）" in kf_qc_prompt


def test_p1_4_acceptance_tracks_locked_human_approved_and_admitted(tmp_path, monkeypatch):
    from shared.image_qc import QcResult, build_qc_frame_meta, write_frame_request_snapshot, write_sidecar
    from step02a_frame_gen import image_prompt_quality as ipq

    work = tmp_path / "working"
    d = work / "shots" / "1"
    d.mkdir(parents=True)
    script = {"shots": [{"index": 1, "ff_desc": "a", "lf_desc": "b", "keyframes": []}]}
    shot = script["shots"][0]
    ff = d / "first_frame.png"
    _noisy_img().save(ff)
    write_frame_request_snapshot(ff, shot=shot, frame_kind="first_frame", prompt="a", refs=[])
    write_sidecar(ff, QcResult(has_hallucination=False, issues=[], ok=True, raw_result=""),
                  frame_meta=build_qc_frame_meta(shot, "first_frame", [], frame_path=ff))
    monkeypatch.setattr(ipq, "chapter_dir", lambda: tmp_path)

    rep_unlocked = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_unlocked["admitted"] is True
    assert rep_unlocked["locked_human_approved"] is False
    assert rep_unlocked["outputs"]["1"]["locked_human_approved"]["all_required"] is False

    (d / ".lock_frame_first_frame").write_text("", encoding="utf-8")
    rep_locked = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_locked["admitted"] is True
    assert rep_locked["locked_human_approved"] is True


def test_p1_5_failure_classification_and_zero_retry_for_non_stochastic(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared import request_events as re_ev
    from shared import vimax_bridge as vb

    events_file = tmp_path / "step02_events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(vb, "compose_frame_prompt", lambda *a, **k: "作坊画面，安全提示词")
    monkeypatch.setattr(vb, "_build_final_frame_prompt", lambda *a, **k: "作坊画面，安全提示词")

    chars = [{"identifier": "角色甲"}]
    shots_dir = tmp_path / "shots"

    # 1. request_conflict (contradictory contract) -> 0 POST
    calls_conflict = []

    class _AdapterConflict:
        async def generate_single_image(self, **kwargs):
            calls_conflict.append(1)
            raise AssertionError("conflict must not invoke generate_single_image")

    bad_shot = {
        "index": 1,
        "ff_desc": "作坊场景。",
        "keyframes": [],
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True, "states": ["未持尺", "手持尺"]}]},
    }
    res_conflict = asyncio.run(vb._gen_first_frame_async(
        _AdapterConflict(), shots_dir, bad_shot, force=True, max_retries=2, characters=chars,
    ))
    assert res_conflict is None
    assert len(calls_conflict) == 0
    assert bad_shot["_last_failure_class"] == vb.FAILURE_REQUEST_CONFLICT

    # 2. missing_asset -> 1 call raises FileNotFoundError -> 0 retry POST (total 1 call, not 3)
    calls_missing = []

    class _AdapterMissing:
        async def generate_single_image(self, **kwargs):
            calls_missing.append(1)
            raise FileNotFoundError("参考图缺失或不可读: missing.png")

    good_shot = {
        "index": 2,
        "ff_desc": "角色甲站在木案旁。",
        "keyframes": [],
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True, "states": []}]},
    }
    res_missing = asyncio.run(vb._gen_first_frame_async(
        _AdapterMissing(), shots_dir, dict(good_shot), force=True, max_retries=2, characters=chars,
    ))
    assert res_missing is None
    assert len(calls_missing) == 1

    # 3. qc_dispute (qc_call_failed) -> keeps image & stops immediately (1 call, 0 retry POST)
    calls_dispute = []

    class _AdapterOk:
        def __init__(self, counter):
            self.counter = counter

        async def generate_single_image(self, **kwargs):
            self.counter.append(1)
            from vimax.interfaces import ImageOutput
            return ImageOutput(fmt="pil", ext="png", data=_noisy_img(seed=len(self.counter) + 10))

    monkeypatch.setattr(iq, "QC_ENABLED", True)
    monkeypatch.setattr(iq, "QC_WRITE_CRITICAL", True)
    monkeypatch.setattr(iq, "maybe_qc_sync", lambda *a, **k: iq.QcResult(
        has_hallucination=False, issues=[], ok=False, raw_result="", error="429 timeout"))
    shot_dispute = dict(good_shot, index=3)
    out_dispute = asyncio.run(vb._gen_first_frame_async(
        _AdapterOk(calls_dispute), shots_dir, shot_dispute, force=True, max_retries=2, characters=chars,
    ))
    assert out_dispute is not None and Path(out_dispute).is_file()
    assert len(calls_dispute) == 1
    assert shot_dispute["_last_failure_class"] == vb.FAILURE_QC_DISPUTE

    # 4. stochastic_miss (qc_rejected critical) -> retries exactly up to QC_MAX_ATTEMPTS=3
    calls_stoch = []
    monkeypatch.setattr(iq, "maybe_qc_sync", lambda *a, **k: iq.QcResult(
        has_hallucination=True,
        issues=[iq.QcIssue(category="extra_person", description="多画一人", severity="critical")],
        ok=True, raw_result=""))
    shot_stoch = dict(good_shot, index=4)
    out_stoch = asyncio.run(vb._gen_first_frame_async(
        _AdapterOk(calls_stoch), shots_dir, shot_stoch, force=True, max_retries=2, characters=chars,
    ))
    assert out_stoch is not None and Path(out_stoch).is_file()
    assert len(calls_stoch) == 3  # QC_MAX_ATTEMPTS=3
    assert shot_stoch["_last_failure_class"] == vb.FAILURE_STOCHASTIC_MISS

    # 4.1 Decision ledger records written for all classifications
    ledgers = [e for e in re_ev.read_events(events_file) if e.get("event") == "decision_ledger"]
    classes = {e.get("defect_class") for e in ledgers}
    assert {
        vb.FAILURE_REQUEST_CONFLICT,
        vb.FAILURE_MISSING_ASSET,
        vb.FAILURE_QC_DISPUTE,
        vb.FAILURE_STOCHASTIC_MISS,
    } <= classes
    sample = ledgers[0]
    for field in ("chapter_scope", "shot_index", "frame_role", "model_id",
                  "prompt_version", "ref_strategy", "attempt_index", "action_type",
                  "defect_class", "human_usability", "non_target_damage"):
        assert field in sample
