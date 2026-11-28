# -*- coding: utf-8 -*-
"""视频级 A/B 评测协议（总计划阶段 7 / 工作包 D）.

锁住非 step01 吸收计划 §6.5 的门槛判定与 §5.6 的生成门槛：上游未通过不生成、
未显式批准预算不生成、样本池排除四条已知矛盾镜头、结果矛盾时判 `inconclusive`。

2026-10-03（增强计划 §15「绕过入口」行）起，本工具的 `run`（真实生成）**明确拒绝**：
旧提交路径不冻结请求、不保证"一次确认一次 POST"，且会把结果副本写进正式 `clips/`。
因此下面的用例改为断言"门槛通过也**零调用**"（注入的逐镜生成入口必须一次都没被调到），
`plan`/`score` 的离线行为不受影响。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "video_ab", _PROJECT_ROOT / "tools" / "video_ab_experiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ab():
    return _load_tool()


def _samples(n=6):
    return {"samples": [{"shot_id": str(i)} for i in range(1, n + 1)]}


def _score_set(a_val, b_val, *, target_b=None, fidelity_b=2, usability_b=2):
    out = {}
    for i in range(1, 7):
        out[str(i)] = {
            "A": {m: a_val for m in
                  ("character_identity", "costume_accessory", "scene_consistency",
                   "prop_consistency", "reference_pollution", "action_usability",
                   "story_fidelity")},
            "B": {m: b_val for m in
                  ("character_identity", "costume_accessory", "scene_consistency",
                   "prop_consistency", "action_usability")},
        }
        out[str(i)]["B"]["reference_pollution"] = (
            b_val if target_b is None else target_b)
        out[str(i)]["B"]["story_fidelity"] = fidelity_b
        out[str(i)]["B"]["action_usability"] = usability_b
        out[str(i)]["A"]["action_usability"] = 2
    return out


# ---------------------------------------------------------------------------
# 生成门槛
# ---------------------------------------------------------------------------
def test_generation_refused_without_approval(ab, monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    blockers = ab.generation_gates(approve=False)
    assert any("批准生成预算" in b for b in blockers)


def test_generation_refused_when_mode_is_not_r2v(ab, monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    blockers = ab.generation_gates(approve=True)
    assert any("r2v" in b for b in blockers)


def test_generation_refused_when_upstream_gate_blocks(ab, monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    monkeypatch.setattr(ab, "_upstream_eligible",
                        lambda: (False, None, "audit has 4 blocking"))
    blockers = ab.generation_gates(approve=True)
    assert any("上游 gate 未通过" in b for b in blockers)


def test_run_reports_blocked_without_starting(ab, monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    out = ab.run("exp-1", approve=False)
    assert out["generation_started"] is False
    assert out["status"] in ("blocked", "upstream_blocked")
    assert out["blockers"]


def test_run_dry_run_never_generates(ab, monkeypatch):
    monkeypatch.setattr(ab, "generation_gates", lambda approve: [])
    out = ab.run("exp-1", approve=True, dry_run=True)
    assert out["status"] == "dry_run"
    assert out["generation_started"] is False


# ---------------------------------------------------------------------------
# 样本池
# ---------------------------------------------------------------------------
def test_blocked_shots_never_enter_the_sample_pool(ab):
    script = {"shots": [{"index": str(i)} for i in range(1, 13)]}
    picked, excluded = ab._sample_shots(script)
    assert set(excluded) >= {"1", "2", "5", "7"}
    assert not ({str(s["index"]) for s in picked} & {"1", "2", "5", "7"})


def test_manifest_declares_isolation_and_cost_fields(ab, monkeypatch):
    monkeypatch.setattr(ab, "_upstream_eligible", lambda: (False, None, "blocked"))
    m = ab.build_manifest("exp-2")
    assert m["upstream_gate_status"] == "upstream_blocked"
    assert "experiments" in str(ab.experiment_dir("exp-2"))
    assert set(m["cost"]) == {"api_generated_images", "api_generated_videos",
                              "qc_regenerations", "cache_hits",
                              "local_derived_images"}
    assert m["armed"] is False


# ---------------------------------------------------------------------------
# 判定门槛（§6.5）
# ---------------------------------------------------------------------------
def test_adopt_when_gain_and_target_improvements_met(ab):
    result = ab.evaluate_scores(_samples(), _score_set(1, 2, target_b=2))
    assert result["verdict"] == "adopt"
    assert all(result["checks"].values())


def test_reject_when_gain_below_threshold(ab):
    # 同样分数 → 增益 0
    result = ab.evaluate_scores(_samples(), _score_set(2, 2, target_b=2))
    assert result["verdict"] != "adopt"
    assert result["checks"]["gain_ok"] is False


def test_reject_when_too_few_target_improvements(ab):
    # 总分提高（其他项 +1）但**目标指标一项都没提高**
    scores = _score_set(1, 2, target_b=2)
    for rec in scores.values():
        for m in ab.TARGET_METRICS:
            rec["B"][m] = rec["A"][m]
    result = ab.evaluate_scores(_samples(), scores)
    assert result["checks"]["improved_shots_ok"] is False
    assert result["verdict"] != "adopt"


def test_story_fidelity_zero_blocks_adoption(ab):
    scores = _score_set(1, 2, target_b=2, fidelity_b=0)
    result = ab.evaluate_scores(_samples(), scores)
    assert result["checks"]["no_fidelity_regression"] is False
    assert result["verdict"] != "adopt"


def test_action_usability_drop_blocks_adoption(ab):
    scores = _score_set(1, 2, target_b=2, usability_b=0)
    result = ab.evaluate_scores(_samples(), scores)
    assert result["checks"]["action_usability_not_worse"] is False
    assert result["verdict"] != "adopt"


def test_insufficient_samples_is_inconclusive(ab):
    result = ab.evaluate_scores(_samples(2), _score_set(1, 2, target_b=2))
    assert result["verdict"] == "inconclusive"


def test_missing_scores_is_inconclusive_not_adopt(ab):
    result = ab.evaluate_scores(_samples(), {})
    assert result["verdict"] == "inconclusive"
    assert result["manual_score_status"] == "missing"


def test_contradictory_results_are_inconclusive(ab):
    """单次结果互相矛盾（有升有降且总增益不达标）→ 不直接上线."""
    scores = _score_set(1, 2, target_b=2)
    # 让第 1 镜明显变差
    scores["1"]["B"] = {m: 0 for m in ab.SCORE_METRICS}
    result = ab.evaluate_scores(_samples(), scores)
    assert result["verdict"] in ("inconclusive", "reject")
    assert result["verdict"] != "adopt"


# ---------------------------------------------------------------------------
# v6.111（审核 P1-4）：run 必须**真的生成**，而且必须隔离
# ---------------------------------------------------------------------------
def _fake_generation(tmp_path, ab, monkeypatch, *, record):
    """替换 step02b 逐镜入口；写一个假视频到 shots_dir，并记录调用."""
    async def _fake_gen_one(shot, shots_dir, adapter, script_mtime, script,
                            poll_semaphore=None, force=False):
        record.append({"shot_id": str(shot.get("index")),
                       "shots_dir": str(shots_dir),
                       "policy": __import__("os").environ.get("R2V_REF_POLICY")})
        d = Path(shots_dir) / str(shot.get("index"))
        d.mkdir(parents=True, exist_ok=True)
        (d / "video.mp4").write_bytes(b"x" * (21 * 1024))
        return None                                    # 无需轮询
    monkeypatch.setattr(ab, "_generation_api", lambda: (_fake_gen_one, lambda: object()))

    ch = tmp_path
    (ch / "vimax_working" / "shots" / "1").mkdir(parents=True)
    (ch / "vimax_working" / "shots" / "1" / "video.mp4").write_bytes(b"prod")
    prod_mtime = (ch / "vimax_working" / "shots" / "1" / "video.mp4").stat().st_mtime

    (ch / "script_result.json").write_text(
        '{"shots": [{"index": 1}, {"index": 2}], "hook": null}', encoding="utf-8")
    monkeypatch.setattr(ab, "chapter_path", lambda: ch)
    return prod_mtime


def test_run_refuses_generation_instead_of_writing(tmp_path, ab, monkeypatch):
    """§15「绕过入口」：合法变体也**明确拒绝**——逐镜生成入口零调用、零写入.

    注入的 `_fake_generation` 是判别性所在：旧实现会真的按 A/B 两组各调一次，
    所以"拒绝"若被去掉，这里立刻转红。
    """
    calls = []
    _fake_generation(tmp_path, ab, monkeypatch, record=calls)
    manifest = {"variant": "r2v_inherit_exclude",
                "samples": [{"shot_id": "1"}, {"shot_id": "2"}]}
    out = ab._generate_isolated("ab-test", manifest)

    assert out["generated"] is False
    assert out["code"] == ab.ENHANCEMENT_REFUSAL_CODE
    assert calls == []                               # 一次都没调到生成入口
    assert out["api_generated_videos"] == 0
    # 合法变体的分组映射仍要报出（供人工对照），但不能据此生成
    assert out["groups"] == {"A": "plain", "B": "inherit_exclude"}
    assert out["videos"] == []
    # 实验目录里不得留下任何视频，生产 vimax_working 也不得被写
    assert not (tmp_path / "experiments" / "ab-test" / "shots").exists()
    prod = tmp_path / "vimax_working" / "shots" / "1" / "video.mp4"
    assert prod.read_bytes() == b"prod"


def test_run_refuses_unknown_variant(tmp_path, ab, monkeypatch):
    """未知变体是**独立于**「生成路径未接通」的输入错误，须单独报出."""
    calls = []
    _fake_generation(tmp_path, ab, monkeypatch, record=calls)
    out = ab._generate_isolated("ab-test", {"variant": "nope", "samples": []})
    assert out["generated"] is False
    assert any("未知变体" in f for f in out["failures"])
    assert out["groups"] == {}
    assert calls == [] and out["api_generated_videos"] == 0

    # 合法对照：合法变体不会报"未知变体"，但同样拒绝（两条原因不互相顶替）
    ok = ab._generate_isolated("ab-test",
                               {"variant": "r2v_inherit_exclude", "samples": []})
    assert not any("未知变体" in f for f in ok["failures"])
    assert ok["code"] == ab.ENHANCEMENT_REFUSAL_CODE


def test_run_records_variant_groups(ab):
    assert ab.VARIANT_GROUPS["r2v_inherit_exclude"] == {
        "A": "plain", "B": "inherit_exclude"}


def test_run_refuses_generation_when_gates_pass(tmp_path, ab, monkeypatch):
    """门槛全过后**不是**"真的生成"，而是明确拒绝：零调用、零正式写入、给出替代入口.

    （v6.111 审核 P1-4 要求的"不许只回 ready 就装作跑过"仍然成立——拒绝是显式出口，
    `status` 带具体 code 而不是含糊的 ready。）
    """
    calls = []
    _fake_generation(tmp_path, ab, monkeypatch, record=calls)
    monkeypatch.setattr(ab, "generation_gates", lambda *, approve: [])
    monkeypatch.setattr(ab, "chapter_path", lambda: tmp_path)
    monkeypatch.setattr(ab, "build_manifest",
                        lambda eid, variant="r2v_inherit_exclude": {
                            "variant": variant, "samples": [{"shot_id": "1"}]})
    out = ab.run("ab-test", approve=True)

    assert out["generation_started"] is False
    assert out["status"] == ab.ENHANCEMENT_REFUSAL_CODE
    assert calls == []
    assert "video_reroll" in out["next"] or "step02b_video_gen" in out["next"]
    # 拒绝时**不**写实验 manifest（那是生成成功后才有的记录）
    assert not (tmp_path / "experiments" / "ab-test" / "manifest.json").exists()


def test_run_cli_refuses_with_nonzero_exit_when_enhancement_flags_passed(tmp_path, ab):
    """命令行带候选参数 ⇒ 同一出口（rc=2），不得静默继续或退回旧路径."""
    rc = ab.main(["run", "--experiment-id", "ab-cli",
                  "--candidate-root", str(tmp_path / "out" / "video")])
    assert rc == 2
    assert not (tmp_path / "experiments").exists()


def test_run_does_not_generate_when_gates_block(ab, monkeypatch):
    monkeypatch.setattr(ab, "generation_gates", lambda *, approve: ["未批准预算"])
    out = ab.run("ab-test", approve=False)
    assert out["generation_started"] is False
    assert out["status"] == "blocked"
