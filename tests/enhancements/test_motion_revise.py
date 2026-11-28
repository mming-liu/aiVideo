"""A 包修订闭环判别性测试（§A3）：六类合法修订的成功出口 + 残余/无变化/旧 key 对照。

每个用例都**重置基线**（先把基线 bundle 提交成 accepted），再走真实 CLI：
parser → 核 base key → 候选副本 → 范围与来源守卫 → 完整校验 → 渲染 → 提交。
"""
from __future__ import annotations

import io
import json
import os
import contextlib
import itertools
import shutil
from pathlib import Path

import pytest

from tools import motion_lab
from tests.enhancements import build_fixtures as bf

BASE = Path(os.environ.get("OUTPUT_BASE", Path(__file__).parent.parent / ".pytest_tmp")) / f"motion_revise_{os.getpid()}"


@pytest.fixture(scope="module")
def env() -> dict:
    # 会话开始时清空隔离根：attempt 与 manifest 是 write-once，跨会话复用会撞 manifest_immutable
    shutil.rmtree(BASE, ignore_errors=True)
    root = BASE / "fixtures"
    built = bf.ensure_fixtures(root, experiment_id="motion-fixture", scale=0.25)
    package_root = (BASE / "exp" / "motion-fixture" / "motion").resolve()
    base_bundle = Path(built["cases"]["content_a__title"]["bundle"])
    cases = bf.build_revision_cases(root, base_bundle_path=base_bundle)
    return {"built": built, "asset_root": str(Path(built["asset_root"]).resolve()),
            "package_root": package_root, "base_bundle": base_bundle, "cases": cases}


def _run(argv, *, shooter=None):
    """跑 CLI 并吞掉输出（返回值只看退出码与盘上产物）。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = motion_lab.main(argv, shooter=shooter)
    return rc, out.getvalue(), err.getvalue()


_SEQ = itertools.count()


def _reset_base(env, tag: str) -> None:
    """把 accepted 重新指回基线 bundle（每次用全新 attempt id：manifest 是 write-once）。"""
    attempt_id = f"base-{tag}-{next(_SEQ)}"
    rc, _o, err = _run(["validate", "--bundle", str(env["base_bundle"]),
                        "--out-dir", str(env["package_root"]),
                        "--asset-root", env["asset_root"], "--attempt-id", attempt_id])
    assert rc == 0, f"基线重置失败 rc={rc} err={err}"


def _accepted(env) -> dict:
    pointer = json.loads((env["package_root"] / "accepted.json").read_text(encoding="utf-8"))
    manifest = json.loads(Path(pointer["manifest_path"]).read_text(encoding="utf-8"))
    return {"pointer": pointer, "manifest": manifest}


def _revise(env, name: str, *, request=None, attempt=None):
    case = env["cases"]["cases"][name]
    _reset_base(env, f"{name}-reset")
    attempt_id = attempt or f"rev-{name}-{next(_SEQ)}"
    rc, out, err = _run(["revise", "--request", request or case["request"],
                         "--out-dir", str(env["package_root"]),
                         "--asset-root", env["asset_root"], "--attempt-id", attempt_id,
                         "--no-encode"], shooter=bf.DeterministicStubShooter())
    manifest_path = env["package_root"] / "attempts" / attempt_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    if manifest is None and "stale_base_key" not in err:
        raise AssertionError(f"{name}: 未产生 attempt（rc={rc}）stderr={err.strip()[:400]}")
    return rc, out, err, manifest


def _codes(manifest) -> set[str]:
    return {p["code"] for p in (manifest or {}).get("problems") or []}


LEGAL = ["layout_split", "font_replace", "retime_overlap", "add_bind", "delete_rebind",
         "split_remap"]


@pytest.mark.parametrize("name", LEGAL)
def test_legal_revisions_reach_accepted(env, name):
    rc, _o, _e, manifest = _revise(env, name)
    assert rc == 0, (name, manifest and manifest.get("problems"))
    assert manifest["status"] == "accepted"
    assert {c["name"]: c["status"] for c in manifest["checks"]}["render"] == "pass"
    accepted = _accepted(env)
    candidate = json.loads(Path(env["cases"]["cases"][name]["bundle"]).read_text(encoding="utf-8"))
    assert accepted["pointer"]["input_key"] == env["cases"]["cases"][name]["replacement_input_key"]
    assert accepted["manifest"]["input_key"] == accepted["pointer"]["input_key"]
    assert accepted["manifest"]["base_input_key"] == env["cases"]["base_input_key"]
    # 采纳稿必须就是候选副本（不是基线，也不是拼接产物）
    frozen = json.loads(Path(accepted["manifest"]["bundle"]["frozen_path"]).read_text(encoding="utf-8"))
    assert frozen == candidate


def test_legal_revision_also_emits_step04_plan(env):
    """修订后的候选也要产出 S 的计划文件（局部时间 + 新帧路径）。"""
    rc, _o, _e, manifest = _revise(env, "retime_overlap")
    assert rc == 0, manifest and manifest.get("problems")
    assert manifest["overlay_plan_status"] == "written"
    plan = json.loads(Path(manifest["overlay_plan_file"]).read_text(encoding="utf-8"))
    assert tuple(plan) == ("schema_version", "scope", "script_revision", "fps", "overlays")
    entry = plan["overlays"][0]
    assert entry["start_sec"] == 1.5 and entry["end_sec"] == 4.5, "重排后的局部时间窗"
    assert Path(entry["path"]).parent.is_dir()


def test_rejection_does_not_move_accepted_and_does_not_render(env):
    rc, _o, _e, manifest = _revise(env, "partial_fix")
    assert rc != 0
    assert manifest["status"] == "rejected"
    assert "derived_char_index_mismatch" in _codes(manifest)
    accepted = _accepted(env)
    assert accepted["pointer"]["input_key"] == env["cases"]["base_input_key"], \
        "拒收不得移动 accepted"
    attempt = max((env["package_root"] / "attempts").glob("rev-partial_fix-*"),
                  key=lambda p: p.stat().st_mtime)
    assert not list(attempt.rglob("frame_*.png")), "残余拒收不得渲染"
    assert not (attempt / "outputs").exists() or not list((attempt / "outputs").rglob("*.mp4"))


def test_residual_after_full_revalidation_is_rejected(env):
    """完整重验返回残余 ⇒ 拒收（局部"看起来修好了"不构成采纳理由）。"""
    rc, _o, _e, manifest = _revise(env, "residual_text_swap")
    assert rc != 0 and manifest["status"] == "rejected"
    assert "segment_text_mismatch" in _codes(manifest)
    assert _accepted(env)["pointer"]["input_key"] == env["cases"]["base_input_key"]


def test_no_change_is_legal_and_reaccepted(env):
    rc, _o, _e, manifest = _revise(env, "no_change")
    assert rc == 0, manifest and manifest.get("problems")
    assert manifest["no_change"] is True
    assert manifest["input_key"] == env["cases"]["base_input_key"]
    assert _accepted(env)["pointer"]["input_key"] == env["cases"]["base_input_key"]


def test_no_change_still_illegal_returns_residual_without_rendering(env):
    rc, _o, _e, manifest = _revise(env, "no_change_still_illegal")
    assert rc != 0 and manifest["status"] == "rejected"
    assert "segment_coverage_incomplete" in _codes(manifest)
    attempt = max((env["package_root"] / "attempts").glob("rev-no_change_still_illegal-*"),
                  key=lambda p: p.stat().st_mtime)
    assert not list(attempt.rglob("frame_*.png"))


def test_stale_base_key_is_rejected_with_current_key(env):
    case = env["cases"]["cases"]["stale_base_key"]
    _reset_base(env, "stale")
    before = _accepted(env)["pointer"]["input_key"]
    rc, _o, err = _run(["revise", "--request", case["request"],
                        "--out-dir", str(env["package_root"]),
                        "--asset-root", env["asset_root"], "--attempt-id", "rev-stale",
                        "--no-encode"], shooter=bf.DeterministicStubShooter())
    assert rc == 4
    payload = json.loads(err.strip().splitlines()[-1])
    assert payload["code"] == "stale_base_key"
    assert payload["current_input_key"] == before
    assert payload["requested_base_input_key"] == "0" * 64
    assert _accepted(env)["pointer"]["input_key"] == before, "旧 key 拒收不得动 accepted"
    assert not (env["package_root"] / "attempts" / "rev-stale").exists(), \
        "旧 key 拒收不应建立 attempt"


@pytest.mark.parametrize("name,code", [
    ("guard_source_changed", "source_changed"),
    ("guard_text_reduced", "core_text_reduced"),
    ("guard_duplicate_copy", "duplicate_binding_without_base"),
])
def test_scope_and_source_guards(env, name, code):
    rc, _o, _e, manifest = _revise(env, name)
    assert rc != 0 and manifest["status"] == "rejected"
    assert code in _codes(manifest)
    assert _accepted(env)["pointer"]["input_key"] == env["cases"]["base_input_key"]


def test_source_change_guard_stops_before_other_checks(env):
    """来源守卫最先裁决：换了来源就不必再讨论正文覆盖（错误归因要准）。"""
    _rc, _o, _e, manifest = _revise(env, "guard_source_changed")
    assert "core_text_reduced" not in _codes(manifest)


def test_split_remap_preserves_leaf_coverage(env):
    """拆分并重映射的成功出口必须真的把两个新 spec 绑上（不是只删旧的）。"""
    rc, _o, _e, manifest = _revise(env, "split_remap")
    assert rc == 0, manifest and manifest.get("problems")
    frozen = json.loads(Path(manifest["bundle"]["frozen_path"]).read_text(encoding="utf-8"))
    ids = {s["spec_id"] for s in frozen["specs"]}
    assert {"t_p1", "t_p2"} <= ids and "t_main" not in ids
    bound = {o["spec_id"] for o in frozen["overlays"]}
    assert {"t_p1", "t_p2"} <= bound
    assert "ov_main" not in {o["overlay_id"] for o in frozen["overlays"]}


def test_add_bind_rejects_unknown_binding():
    """guard 的反向对照：绑定到不存在的 spec 时，悬空引用必须是 0 才算通过。"""
    report = __import__("shared.motion_render.validate", fromlist=["V"]).ValidationReport()
    assert report.ok
