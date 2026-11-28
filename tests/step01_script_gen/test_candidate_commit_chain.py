"""Candidate -> real commit entry: success, rejection, write failure, recovery.

The candidate is built by `candidate_edit.build_candidate` from an anonymous
sample, finalized by the production finalizer, projected through
`_strip_runtime_fields`, reloaded from disk exactly as the chapter cache does,
and then handed to the single commit point `_commit_step01_outputs`.  Only the
text-model audit call is stubbed (tests never call a model); the quality gate,
staged self-check, manifest and read-back verification are the real ones.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_NOVEL = "甲说：走。乙离开。"


def _shot(index, narration, beats, duration=5.0, lines=None):
    return {
        "index": index, "duration_sec": duration, "duration": duration,
        "prompt": "[中景] 匿名测试镜%d，画面各异" % index,
        "ff_desc": "第%d镜起始画面，人物立于画左" % index,
        "lf_desc": "第%d镜结束画面，人物立于画右" % index,
        "motion_desc": "0-2秒：动作甲；2-5秒：动作乙",
        "narration_segment": narration, "subtitle": narration,
        "main_subject": "甲", "scene": "院中", "location": "院中",
        "keyframes": [{"desc": "过程画面", "stage": "approach", "t_sec": 2.5}],
        "frame_layouts": {
            "ff": [{"name": "甲", "on_screen": True, "position": "画左",
                    "pose": "站立", "gaze_target": "画右", "props": []}],
            "lf": [{"name": "甲", "on_screen": True, "position": "画右",
                    "pose": "站立", "gaze_target": "画左", "props": []}],
            "kfs": [[{"name": "甲", "on_screen": True, "position": "画中",
                      "pose": "站立", "gaze_target": "画前", "props": []}]],
        },
        "plan_refs": {"beats": beats, "lines": lines or []},
    }


def _plan():
    return {"main_events": ["甲说走", "乙离开"],
            "required_facts": [],
            "shot_beats": ["节拍：甲说走｜载体：台词1", "节拍：乙离开｜载体：旁白1"],
            "dialogue_lines": ["走。"],
            "narration_lines": [{"text": "乙离开。", "source": "乙离开。",
                                 "fact_refs": [], "voice_role": "乙"}],
            "hook_spoken": []}


def _source():
    return {"characters": [{"identifier": "甲"}, {"identifier": "乙"}],
            "shots": [
        {"index": 41, "motion_desc": "甲开口。", "duration_sec": 5.0,
         "plan_refs": {"beats": [1], "lines": [1]}},
        {"index": 83, "motion_desc": "乙转身离开。", "duration_sec": 5.0,
         "plan_refs": {"beats": [2], "lines": []}}]}


def _candidate():
    from step01_script_gen.candidate_edit import build_candidate, object_digest
    plan, source = _plan(), _source()
    target = _shot(1, "", [1, 2])
    target["narration_segment"] = "甲说：“走。”乙离开。"
    target["subtitle"] = target["narration_segment"]
    target = dict(target, index=1, duration_sec=10.0, duration=10.0,
                  plan_refs={"beats": [1, 2], "lines": [1]})
    spec = {"schema": "candidate_edit.v1", "plan_sha256": object_digest(plan),
            "source_sha256": object_digest(source),
            "shots": [{"edit_id": "merge-2", "reason": "同一空间连续事件",
                       "sources": [{"index": s["index"], "sha256": object_digest(s)}
                                   for s in source["shots"]],
                       "target": target}]}
    candidate, problems = build_candidate(plan, source, spec)
    assert not problems, problems
    return candidate


def _finalize(candidate):
    import step01_script_gen.main as m
    from step01_script_gen import plan_feasibility as pf
    pf.bind_plan_identity(candidate, candidate["chapter_outline"])
    script, errors = m.finalize_step01_script(
        copy.deepcopy(candidate), novel_text=_NOVEL, label="test")
    assert not errors, errors
    return json.loads(json.dumps(m._strip_runtime_fields(script), ensure_ascii=False))


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_VERSION
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "collect_structured_issues",
                        lambda *a, **k: ([], False, RULE_VERSION))
    monkeypatch.setattr(m, "_repair_shot_warnings",
                        lambda script, novel_text, **kw: script)
    monkeypatch.setattr(m, "_snapshot_run_artifacts", lambda script=None: None)
    return tmp_path


def _fake_audit(monkeypatch):
    import step01_script_gen.audit as audit_mod
    import step01_script_gen.main as m
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.audit import audit_prompt_version
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = {"n": 0}

    def fake(script_arg_novel, plan, script, *, model=None, timeout_sec=180,
             audit_focus=None, metadata=None, **kwargs):
        calls["n"] += 1
        result = audit_mod.AuditResult("ok", [], "")
        result.issues = []
        result.raw_text = ""
        result.payload = {
            "status": "ok", "model": "fake-model", "error": "", "issues": [],
            "input_revision": compute_script_revision(script),
            "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
            "audit_scope": m.STEP01_AUDIT_SCOPE,
            "audit_prompt_version": audit_prompt_version(),
            "audit_focus_version": m._audit_focus_version(),
            "created_at": "2026-10-27T00:00:00", "run_id": "test-run",
        }
        return result

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status", fake)
    return calls


def test_candidate_commits_and_reads_back(chapter, monkeypatch):
    import step01_script_gen.main as m
    _fake_audit(monkeypatch)
    final = _finalize(_candidate())
    committed, summary = m._commit_step01_outputs(
        copy.deepcopy(final), _NOVEL, plan=copy.deepcopy(final["chapter_outline"]))
    assert summary["status"] == "COMMITTED", summary["detail"]
    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        assert (chapter / name).exists(), name
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "COMMITTED"
    assert manifest["script_revision"] == summary["revision"]
    assert manifest["artifacts"], "manifest 必须登记三份产物摘要"
    # 读回的声音归属必须还在（提交不是只写画面字段）
    assert on_disk["chapter_outline"]["narration_lines"][0]["voice_role"] == "乙"
    assert on_disk["shots"][0]["narration_segment"] == final["shots"][0]["narration_segment"]


def test_broken_dialogue_blocks_and_keeps_baseline(chapter, monkeypatch):
    import step01_script_gen.main as m
    _fake_audit(monkeypatch)
    baseline = {"shots": [{"index": 1, "narration_segment": "旧基线"}], "marker": "baseline"}
    (chapter / "script_result.json").write_text(
        json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    before = (chapter / "script_result.json").read_bytes()
    broken = _candidate()
    broken["shots"][0]["narration_segment"] = "甲说：“停。”乙离开。"
    broken["shots"][0]["subtitle"] = broken["shots"][0]["narration_segment"]
    _committed, summary = m._commit_step01_outputs(
        copy.deepcopy(broken), _NOVEL, plan=copy.deepcopy(broken["chapter_outline"]))
    assert summary["status"] == "BLOCK"
    assert summary["repair_outcome"] == "dialogue_truncated"
    assert (chapter / "script_result.json").read_bytes() == before


def test_write_failure_blocks_and_keeps_baseline(chapter, monkeypatch):
    import shared.json_io as json_io
    import step01_script_gen.main as m
    _fake_audit(monkeypatch)
    final = _finalize(_candidate())
    baseline = (chapter / "script_result.json")
    baseline.write_text(json.dumps({"marker": "baseline-2"}, ensure_ascii=False),
                        encoding="utf-8")
    before = baseline.read_bytes()
    real = json_io.atomic_dump_json

    def boom(path, payload, **kwargs):
        if Path(path).name == "script_result.json":
            raise OSError("simulated disk failure")
        return real(path, payload, **kwargs)

    monkeypatch.setattr(json_io, "atomic_dump_json", boom)
    _committed, summary = m._commit_step01_outputs(
        copy.deepcopy(final), _NOVEL, plan=copy.deepcopy(final["chapter_outline"]))
    assert summary["status"] == "BLOCK"
    assert "staged" in summary["detail"] or "替换失败" in summary["detail"]
    # 故障发生在写正式文件之前/之中：基线字节不变，且没有写出半份提交清单
    assert baseline.read_bytes() == before
    manifest = chapter / "step01_commit.json"
    if manifest.exists():
        assert json.loads(manifest.read_text(encoding="utf-8"))["status"] != "COMMITTED"


def test_recovery_after_failure_commits_same_revision(chapter, monkeypatch):
    import step01_script_gen.main as m
    _fake_audit(monkeypatch)
    final = _finalize(_candidate())
    broken = copy.deepcopy(final)
    broken["shots"][0]["narration_segment"] = "甲说：“停。”乙离开。"
    _c, blocked = m._commit_step01_outputs(
        broken, _NOVEL, plan=copy.deepcopy(broken["chapter_outline"]))
    assert blocked["status"] == "BLOCK"
    committed, summary = m._commit_step01_outputs(
        copy.deepcopy(final), _NOVEL, plan=copy.deepcopy(final["chapter_outline"]))
    assert summary["status"] == "COMMITTED", summary["detail"]
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    from shared.quality_gate import compute_script_revision
    assert compute_script_revision(on_disk) == summary["revision"]
