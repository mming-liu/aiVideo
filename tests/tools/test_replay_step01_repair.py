# -*- coding: utf-8 -*-
"""`tools/replay_step01_repair.py` 的红线与诊断留痕（v6.123，无网络）.

用户验证计划第 2 步要求在**冻结稿**上回放一次真实修复——回放工具本身必须先证明
「不会碰正式产物」，否则一次诊断实验就能污染提交链的权威产物。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import replay_step01_repair as rsp  # noqa: E402


def test_out_path_guard_rejects_official_artifact_names(tmp_path):
    for _name in ("script_result.json", "step01_audit.json", "step01_quality.json",
                  "step01_commit.json", "step01_repair_rounds.json"):
        with pytest.raises(SystemExit) as _e:
            rsp._guard_out_path(tmp_path / _name)
        assert "不得覆盖正式产物" in str(_e.value)


def test_out_path_guard_rejects_the_chapter_root(tmp_path, monkeypatch):
    _chapter = tmp_path / "chapter_001"
    _chapter.mkdir()
    monkeypatch.setattr(rsp, "_chapter_dir", lambda: _chapter)
    with pytest.raises(SystemExit) as _e:
        rsp._guard_out_path(_chapter / "repaired.json")
    assert "章节目录根部" in str(_e.value)
    rsp._guard_out_path(_chapter / "snapshots" / "repaired.json")   # 合法


def _payload() -> dict:
    return {"issues": [{
        "code": "model_audit_STATE_CONTRADICTION", "severity": "visual_risk",
        "message": "m", "shot_id": "4", "frame_id": "ff", "field_path": "ff_desc",
        "repair_targets": [{"shot_id": "4", "field_path": "ff_desc"},
                           {"shot_id": "5", "field_path": "prompt"}]}]}


def test_replay_writes_only_diagnostic_outputs(tmp_path, monkeypatch):
    """诊断回放：只写 `--out` 与 `<out>.replay.json`；正式产物目录一字不动."""
    import step01_script_gen.main as m

    _chapter = tmp_path / "chapter_001"
    (_chapter / "snapshots").mkdir(parents=True)
    _official = _chapter / "script_result.json"
    _official.write_text(json.dumps({"sentinel": "official"}), encoding="utf-8")
    _audit = _chapter / "step01_audit.json"
    _audit.write_text(json.dumps({"sentinel": "audit"}), encoding="utf-8")

    _frozen = _chapter / "snapshots" / "script_result_20260925_145317.json"
    _frozen.write_text(json.dumps({"shots": [{"index": 4}, {"index": 5}]}),
                       encoding="utf-8")
    _issues = tmp_path / "diag_issues.json"
    _issues.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    _out = _chapter / "snapshots" / "repaired.json"

    monkeypatch.setattr(rsp, "_chapter_dir", lambda: _chapter)
    monkeypatch.setattr(rsp, "_read_raw_chapter", lambda: "原文")
    monkeypatch.setattr(rsp, "compute_script_revision",
                        lambda s: "rev-" + str(len(s.get("shots") or [])))

    _seen = {}

    def _fake_repair(script, novel_text, **kw):
        _seen.update(kw)
        script["shots"].append({"index": 6})
        if kw.get("repair_diag") is not None:
            kw["repair_diag"].append({"targets": ["shot_4", "shot_5"],
                                      "groups": [["shot_4", "shot_5"]],
                                      "accepted": True, "resolved": 1,
                                      "new_blocking_keys": [],
                                      "rolled_back_groups": []})
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", _fake_repair)

    report = rsp.replay(_frozen, _issues, _out, "诊断输入：下标已人工补全",
                        None, tmp_path / "diag.log")

    assert _seen.get("max_rounds") == 1, "回放不得增加修复轮"
    assert len(_seen.get("audit_issues") or []) == 1
    assert json.loads(_out.read_text(encoding="utf-8"))["shots"][-1] == {"index": 6}
    _rep = json.loads(_out.with_suffix(".replay.json").read_text(encoding="utf-8"))
    assert _rep["diagnostic"] is True
    assert "下标已人工补全" in _rep["note"]
    assert _rep["changed"] is True
    assert _rep["repair_diag"][0]["groups"] == [["shot_4", "shot_5"]]

    assert json.loads(_official.read_text(encoding="utf-8")) == {"sentinel": "official"}
    assert json.loads(_audit.read_text(encoding="utf-8")) == {"sentinel": "audit"}
    assert not (_chapter / "step01_repair_rounds.json").exists(), "不得写修复台账"
    assert not (_chapter / "_staged").exists()


def test_replay_refuses_an_empty_issue_set(tmp_path, monkeypatch):
    import step01_script_gen.main as m

    monkeypatch.setattr(rsp, "_read_raw_chapter", lambda: "原文")
    monkeypatch.setattr(m, "_repair_shot_warnings",
                        lambda *a, **k: pytest.fail("不该发起模型调用"))
    _script = tmp_path / "frozen.json"
    _script.write_text(json.dumps({"shots": []}), encoding="utf-8")
    _issues = tmp_path / "empty.json"
    _issues.write_text(json.dumps({"issues": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as _e:
        rsp.replay(_script, _issues, tmp_path / "out.json", "", None, None)
    assert "没有任何 blocking 条目" in str(_e.value)
