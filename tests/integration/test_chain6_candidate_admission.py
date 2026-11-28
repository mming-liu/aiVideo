# -*- coding: utf-8 -*-
"""连通性验收（2026-10-03 复核 P1-2 / P1-5）：**生产者输出直接交给消费入口**。

与夹具式用例的区别：这里不手写 manifest，也不替生产者补字段——候选由真实的
``accept_candidate_video`` 产出，然后**原样**喂给 ``resolve_candidate_inputs``。
反例（未采纳 / 状态被拒 / 章号不符 / 输入 key 不符）与合法对照各断言具体 code。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio  # noqa: E402
from shared.candidate_inputs import CandidateInputError, resolve_candidate_inputs  # noqa: E402

SCOPE = {"chapter": 1}
REV = "rev-chain"


def _produce(tmp_path, *, attempt_id="a1", experiment_id="chain-e1",
             shot_id="shot-1", shot_index=1, checks=None, status=None):
    """走**真实的采纳出口**产出一个候选；返回 (manifest_path, accepted_root)。

    实验号带 pid：``.pytest_tmp`` 在多次运行之间**不清理**，固定 experiment_id 会撞上
    上一轮留下的 attempt（manifest 是 write-once，会直接拒写）。
    """
    experiment_id = "%s-%d" % (experiment_id, os.getpid())
    from step02_video_gen.request_plan import FrozenVideoRequest
    from step02_video_gen.submit_poll import accept_candidate_video

    ctx = eio.CandidateContext(experiment_id=experiment_id, package="video",
                               attempt_id=attempt_id, chapter=1, scope=dict(SCOPE),
                               script_revision=REV, source="step02b_candidate")
    ctx.prepare()
    video = ctx.outputs_dir / "candidate.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"v" * 4096)
    frozen = FrozenVideoRequest(
        request_id="req-1", provider="stub", model_id="stub-1", mode="text",
        prompt="[中景] 沈砚抬手", parameters={"duration": 10},
        source={"scope": dict(SCOPE), "shot_id": shot_id, "shot_index": shot_index,
                "script_revision": REV})
    frozen.compute_key()
    accepted = accept_candidate_video(
        ctx, video_path=video, frozen_request=frozen,
        checks=list(checks if checks is not None
                    else [{"name": "media", "status": "pass"}]))
    manifest = ctx.attempt_dir / eio.MANIFEST_FILENAME
    if status is not None:                       # 只用于反例：改状态后重算指针摘要
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["status"] = status
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        eio.commit_accepted(ctx.attempt_dir, attempt_id=attempt_id,
                            manifest_path=manifest,
                            manifest_sha256=eio.sha256_file(manifest),
                            input_key=str(data.get("request_key") or ""))
    return manifest, ctx.attempt_dir, ctx.package_root


def _resolve(manifest, **kw):
    return resolve_candidate_inputs(manifest, expect_scope=dict(SCOPE), **kw)


def test_producer_output_is_consumable_without_fixture_help(tmp_path):
    """合法对照：生产者的输出**原样**可被消费，且有稳定编号（不再 unit_locator_missing）."""
    manifest, _attempt, root = _produce(tmp_path)
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    # 身份不是空壳：来源/章/稿都写进了 manifest，消费方据此判断"是不是这一份"
    assert data["source"] == "step02b_candidate"
    assert data["scope"] == SCOPE and data["script_revision"] == REV
    assert data["units"] and data["units"][0].get("shot_index") == 1

    got = _resolve(manifest, experiment_root=root, required_checks=("media",))
    unit = got["units"]["video-shot-1"]
    assert unit["shot_index"] == 1
    assert Path(unit["video_path"]).is_file()
    assert got["scope"] == SCOPE and got["script_revision"] == REV


def test_unaccepted_candidate_is_refused(tmp_path):
    """反例：删掉 accepted 指针 ⇒ 未采纳的候选不得消费."""
    manifest, attempt, root = _produce(tmp_path, experiment_id="chain-e2")
    eio.accepted_path(attempt).unlink()
    with pytest.raises(CandidateInputError) as ei:
        _resolve(manifest, experiment_root=root)
    assert ei.value.code == "candidate_not_accepted"


def test_rejected_manifest_is_refused(tmp_path):
    """反例：manifest 自报 rejected（指针还在）⇒ 拒收，不因"有指针"就放行."""
    manifest, _attempt, root = _produce(tmp_path, experiment_id="chain-e3",
                                        status="rejected")
    with pytest.raises(CandidateInputError) as ei:
        _resolve(manifest, experiment_root=root)
    assert ei.value.code == "candidate_accepted_invalid"
    assert "accepted_manifest_rejected" in ei.value.message


def test_wrong_chapter_candidate_is_refused(tmp_path):
    """反例：另一章的候选配到本章 ⇒ 章号不符拒收（不许继承候选自己的 scope）."""
    manifest, _attempt, root = _produce(tmp_path, experiment_id="chain-e4")
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(manifest, expect_scope={"chapter": 9},
                                 experiment_root=root)
    assert ei.value.code == "scope_mismatch"


def test_input_key_must_bind_manifest_and_pointer(tmp_path):
    """反例：指针的 input_key 与 manifest 记录的请求身份不符 ⇒ 拒收."""
    manifest, attempt, root = _produce(tmp_path, experiment_id="chain-e5")
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    eio.commit_accepted(attempt, attempt_id=attempt.name, manifest_path=manifest,
                        manifest_sha256=eio.sha256_file(manifest),
                        input_key="另一个请求的 key")
    assert data.get("request_key")
    with pytest.raises(CandidateInputError) as ei:
        _resolve(manifest, experiment_root=root)
    assert ei.value.code == "candidate_accepted_invalid"
    assert "accepted_input_key_mismatch" in ei.value.message


def test_failed_media_check_is_refused(tmp_path):
    """反例：媒体检查未通过 ⇒ 消费方按 required_checks 拒收（空表不等于通过）."""
    manifest, _attempt, root = _produce(
        tmp_path, experiment_id="chain-e6",
        checks=[{"name": "media", "status": "fail"}])
    # **默认调用**就必须拒收：消费侧必需检查按包固定（video→media），
    # 不传不等于不检查（2026-10-04 复核 P1-2）
    with pytest.raises(CandidateInputError) as ei:
        _resolve(manifest, experiment_root=root)
    assert ei.value.code == "candidate_accepted_invalid"
    assert "required_checks_unmet" in ei.value.message

    # 合法对照：同一生产者出口产出的**通过**候选，默认调用照常可读
    ok_manifest, _a, ok_root = _produce(tmp_path, experiment_id="chain-e7")
    assert _resolve(ok_manifest, experiment_root=ok_root)["units"]
