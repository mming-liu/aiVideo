"""V 包契约测试：任务生命周期（视频）与跨阶段候选解析。"""
import json
import sys
from pathlib import Path

import pytest

from shared import enhancement_io as eio
from shared import video_attempts as va
from shared.candidate_inputs import CandidateInputError, resolve_candidate_inputs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _candidate_fixtures import seal_accepted_candidate  # noqa: E402


# ---------------------------------------------------------------- 生命周期

def _pkg(tmp_path):
    root = tmp_path / "exp" / "video"
    root.mkdir(parents=True)
    return root


def test_lifecycle_forward_chain_and_illegal_jump(tmp_path):
    pkg = _pkg(tmp_path)
    att = va.open_attempt(pkg, "a1", request_key="k1", scope={"chapter": 1},
                          script_revision="rev1", provider="stub", model_id="m1")
    assert va.current_state(att) == va.STATE_DRAFT
    for nxt in (va.STATE_READY, va.STATE_SUBMITTING):
        va.transition(att, nxt)
    va.record_submit(att, task_id="t-1", credential_profile_ref="p1", generation_id="g1")
    assert va.current_state(att) == va.STATE_SUBMITTED
    for nxt in (va.STATE_RUNNING, va.STATE_DOWNLOADED, va.STATE_CHECKED, va.STATE_ACCEPTED):
        va.transition(att, nxt)
    assert va.current_state(att) == va.STATE_ACCEPTED

    att2 = va.open_attempt(pkg, "a2", request_key="k2", scope={}, script_revision="rev1",
                           provider="stub", model_id="m1")
    with pytest.raises(va.LifecycleError) as ei:
        va.transition(att2, va.STATE_ACCEPTED)   # 不能从 draft 直接采纳
    assert ei.value.code == "illegal_transition"


def test_submit_unknown_when_no_task_id(tmp_path):
    pkg = _pkg(tmp_path)
    att = va.open_attempt(pkg, "a1", request_key="k", scope={}, script_revision="r",
                          provider="stub", model_id="m")
    va.transition(att, va.STATE_READY)
    va.transition(att, va.STATE_SUBMITTING)
    va.record_submit(att, task_id=None, credential_profile_ref="p1", generation_id="g1")
    assert va.current_state(att) == va.STATE_SUBMISSION_UNKNOWN
    # unknown 之后不许再次 submit（重新提交要走新的费用确认，见 §V2 表）
    with pytest.raises(va.LifecycleError):
        va.record_submit(att, task_id="t-2", credential_profile_ref="p1", generation_id="g2")


def test_submit_post_count_and_recovery_reuses_generation(tmp_path):
    pkg = _pkg(tmp_path)
    att = va.open_attempt(pkg, "a1", request_key="k", scope={}, script_revision="r",
                          provider="stub", model_id="m", generation_id="g1")
    va.transition(att, va.STATE_READY)
    va.transition(att, va.STATE_SUBMITTING)
    va.record_submit(att, task_id="t-1", credential_profile_ref="p1", generation_id="g1")
    assert va.submit_post_count(pkg) == 1

    resumed = va.open_recovery_attempt(pkg, att, attempt_id="a1-resume")
    assert va.current_state(resumed) == va.STATE_SUBMITTED          # 直接带 task 起步
    assert va.submit_post_count(pkg) == 1                            # 恢复不新增生成
    inputs = eio.read_inputs(resumed)
    assert inputs["parent_attempt_id"] == "a1" and inputs["generation_id"] == "g1"
    gens = va.generations(pkg)
    assert gens["g1"]["submit_posts"] == 1 and set(gens["g1"]["attempts"]) == {"a1", "a1-resume"}
    assert gens["g1"]["task_id"] == "t-1"


# ---------------------------------------------------------------- 候选解析

def _manifest(tmp_path, name, *, package, scope, revision, source, units, accepted=True):
    """造候选：**走生产者真实出口**（seal_manifest + commit_accepted）。

    ``accepted=False`` 只用于反例——验证"没被指针指名的候选不可消费"。
    """
    man, sha = seal_accepted_candidate(
        tmp_path, name, package=package, scope=scope, revision=revision, source=source,
        units=units)
    if not accepted:
        eio.accepted_path(man.parent).unlink()
    return man, sha


def test_resolve_candidate_inputs_ok(tmp_path):
    scope = {"novel": "n", "chapter": 1}
    v_man, v_sha = _manifest(tmp_path, "v1", package="video", scope=scope, revision="rev1",
                             source="accepted_script",
                             units=[{"unit_id": "shot-1", "shot_index": 1,
                                     "window": {"start_sec": 0.0, "end_sec": 5.0}}])
    a_man, a_sha = _manifest(tmp_path, "t1", package="tts", scope=scope, revision="rev1",
                             source="accepted_script",
                             units=[{"unit_id": "shot-1", "kind": "dialogue",
                                     "shot_index": 1,
                                     "text_sha256": "abc", "voice_identity": {"speaker": "s1"}}])
    got = resolve_candidate_inputs(v_man, a_man, context=None, experiment_root=tmp_path / "exp",
                                   expect_scope=scope, expect_script_revision="rev1",
                                   video_manifest_sha256=v_sha, audio_manifest_sha256=a_sha,
                                   require_audio=True)
    assert got["script_revision"] == "rev1"
    u = got["units"]["shot-1"]
    assert Path(u["video_path"]).is_file() and Path(u["audio_path"]).is_file()
    assert u["voice_identity"] == {"speaker": "s1"} and u["window"]["end_sec"] == 5.0


def test_resolve_rejects_scope_and_revision_mismatch(tmp_path):
    v_man, _ = _manifest(tmp_path, "v1", package="video", scope={"chapter": 1},
                         revision="rev1", source="fixture", units=[{"unit_id": "u1"}])
    a_man, _ = _manifest(tmp_path, "t1", package="tts", scope={"chapter": 2},
                         revision="rev1", source="fixture", units=[{"unit_id": "u1"}])
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man, a_man, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1})
    assert ei.value.code == "video_audio_identity_mismatch"

    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1},
                                 expect_script_revision="rev2")
    assert ei.value.code == "script_revision_mismatch"


def test_resolve_rejects_digest_mismatch_and_missing(tmp_path):
    v_man, v_sha = _manifest(tmp_path, "v1", package="video", scope={"chapter": 1},
                             revision="r", source="accepted_script",
                             units=[{"unit_id": "u1", "shot_index": 1}])
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1},
                                 video_manifest_sha256="0" * 64)
    assert ei.value.code == "manifest_digest_mismatch"

    data = json.loads(Path(v_man).read_text(encoding="utf-8"))
    Path(data["units"][0]["path"]).unlink()
    # 产物被删：准入链先发现（accepted 逐条核产物），比按路径读更早也更准
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1})
    assert ei.value.code == "candidate_accepted_invalid"
    # 未开准入的路径仍按路径核（保留原来的 candidate_missing 覆盖）
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1}, require_accepted=False)
    assert ei.value.code == "candidate_missing"

    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(tmp_path / "nope.json", experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1})
    assert ei.value.code == "manifest_missing"


def test_resolve_requires_accepted_pointer_and_expected_scope(tmp_path):
    """P1-2 反例：没有 accepted 指针的 manifest 不可消费；不声明期望身份也不可."""
    scope = {"chapter": 1}
    v_man, _ = _manifest(tmp_path, "v9", package="video", scope=scope, revision="rev1",
                         source="accepted_script",
                         units=[{"unit_id": "u1", "shot_index": 1}], accepted=False)
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp", expect_scope=scope)
    assert ei.value.code == "candidate_not_accepted"

    ok_man, _ = _manifest(tmp_path, "v10", package="video", scope=scope, revision="rev1",
                          source="accepted_script",
                          units=[{"unit_id": "u1", "shot_index": 1}])
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(ok_man, experiment_root=tmp_path / "exp")
    assert ei.value.code == "expect_scope_required"
    # 合法对照：被指名 + 身份一致 ⇒ 放行
    got = resolve_candidate_inputs(ok_man, experiment_root=tmp_path / "exp", expect_scope=scope)
    assert "u1" in got["units"]


def test_legacy_units_freeze_stable_id_and_require_map(tmp_path):
    v_man, _ = _manifest(tmp_path, "v1", package="video", scope={"chapter": 1}, revision="rev1",
                         source="accepted_script", units=[{"shot_index": 3}])
    got = resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp",
                                   expect_scope={"chapter": 1})
    uid = next(iter(got["units"]))
    assert uid.startswith("unit-")
    map_file = tmp_path / "exp" / "contracts" / "unit_id_map.json"
    assert map_file.is_file() and json.loads(map_file.read_text(encoding="utf-8"))["shots[3]"] == uid

    # 同一 locator 再次解析 ⇒ 幂等（ID 不变）
    again = resolve_candidate_inputs(v_man, experiment_root=tmp_path / "exp",
                                     expect_scope={"chapter": 1})
    assert next(iter(again["units"])) == uid

    # 场景 B：同一 locator 上**显式改绑** unit_id —— 没有旧→新映射必须拒收
    v_man2, _ = _manifest(tmp_path, "v2", package="video", scope={"chapter": 1}, revision="rev1",
                          source="accepted_script",
                          units=[{"shot_index": 3, "unit_id": "unit-renamed"}])
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man2, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1})
    assert ei.value.code == "unit_id_rebound_without_map"
    ok = resolve_candidate_inputs(v_man2, experiment_root=tmp_path / "exp",
                                  expect_scope={"chapter": 1},
                                  id_map={uid: "unit-renamed"})
    assert "unit-renamed" in ok["units"]

    # 场景 C：locator 相同但来源 revision 变了 ⇒ 派生 ID 与冻结值不符，同样要映射
    v_man3, _ = _manifest(tmp_path, "v3", package="video", scope={"chapter": 1}, revision="rev2",
                          source="accepted_script", units=[{"shot_index": 3}])
    with pytest.raises(CandidateInputError) as ei:
        resolve_candidate_inputs(v_man3, experiment_root=tmp_path / "exp",
                                 expect_scope={"chapter": 1})
    assert ei.value.code == "unit_id_rebound_without_map"


def test_parent_chain_identity_is_verified(tmp_path):
    """P1-5：父子链身份不符 ⇒ 拒收，不得据此取得**别人的**任务；多级恢复仍成立."""
    pkg = _pkg(tmp_path)
    a = va.open_attempt(pkg, "a1", request_key="k-A", scope={"chapter": 1},
                        script_revision="r", provider="p", model_id="m",
                        generation_id="gA")
    for nxt in (va.STATE_READY, va.STATE_SUBMITTING):
        va.transition(a, nxt)
    va.record_submit(a, task_id="T-A", credential_profile_ref=None, generation_id="gA")

    b = va.open_attempt(pkg, "b1", request_key="k-B", scope={"chapter": 1},
                        script_revision="r", provider="p", model_id="m",
                        generation_id="gB")
    for nxt in (va.STATE_READY, va.STATE_SUBMITTING):
        va.transition(b, nxt)
    va.record_submit(b, task_id="T-B", credential_profile_ref=None, generation_id="gB")

    # 子尝试属于请求 A，却把 parent 指到 B 的目录
    child = pkg / eio.ATTEMPTS_DIRNAME / "c1"
    child.mkdir(parents=True, exist_ok=True)
    eio.freeze_inputs(child, {"attempt_id": "c1", "parent_attempt_id": "b1",
                              "generation_id": "gA", "request_key": "k-A",
                              "scope": {"chapter": 1}, "script_revision": "r"})
    with pytest.raises(va.LifecycleError) as ei:
        va.task_id_of_attempt(child)
    assert ei.value.code in ("parent_chain_generation_mismatch",
                             "parent_chain_request_mismatch")

    # 合法对照：正常多级恢复（两次）仍拿得到**同一个**任务
    r1 = va.open_recovery_attempt(pkg, a, attempt_id="a1-r1")
    r2 = va.open_recovery_attempt(pkg, r1, attempt_id="a1-r2")
    assert va.task_id_of_attempt(r2)["task_id"] == "T-A"
