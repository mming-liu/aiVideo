# -*- coding: utf-8 -*-
"""`tools/run_step01_final_audit.py` 的审计输入解析（v6.117，2026-09-25）.

背景：该工具此前用 `script.get("outline") or script.get("plan")` 取大纲，而
`script_result.json` 里**这两个键都不存在**（大纲在 `chapter_outline`）→ 每次审计
都带**空大纲**跑，结论与生产提交轮（`main.py::_commit_step01_outputs` 的 `plan=`
用的是 `chapter_outline`）不可比。同一个 revision 得出的 blocking 数因此没有可比性。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import run_step01_final_audit as rfa  # noqa: E402


def test_plan_comes_from_chapter_outline_not_the_legacy_keys():
    """主键必须是 `chapter_outline`——与生产提交点同一个键."""
    script = {"chapter_outline": {"main_events": ["正解"]},
              "outline": {"main_events": ["历史键，不得优先"]}}
    assert rfa._resolve_plan(script) == {"main_events": ["正解"]}


def test_plan_falls_back_to_legacy_keys_for_old_artifacts():
    assert rfa._resolve_plan({"outline": {"a": 1}}) == {"a": 1}
    assert rfa._resolve_plan({"plan": {"a": 1}}) == {"a": 1}


def test_missing_outline_warns_instead_of_silently_going_empty(capsys):
    """复现原 bug 的输入形状：两个历史键都没有 → 必须显式告警，不能静默用 {}."""
    assert rfa._resolve_plan({"title": "t", "shots": []}) == {}
    assert "无 chapter_outline" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# v6.123：安全诊断模式（指定输入 → 诊断输出，绝不覆盖正式产物）
# ---------------------------------------------------------------------------
def test_diagnostic_run_never_writes_the_official_audit_file(tmp_path, monkeypatch):
    """`--script <冻结稿>` → 只写 `<稿目录>/diag/<稿名>.audit.json`，正式产物一字不动。

    用户 2026-09-25 指出的阻塞：该工具此前只读正式稿、只写 `step01_audit.json`，
    拿它做快照实验会覆盖正式审计产物。

    v6.124.2：默认位置再迁到 `diag/` 子目录——旧默认写在该稿**旁边**，产物名与
    稿件同前缀（`script_result_*`）→ 落进 BEST-GUARD 的候选 glob → 2026-09-25
    19:14 被选为终稿、提交点 BLOCK。本测试同时钉住「不得落进候选 glob」。
    """
    import json as _json

    official_dir = (tmp_path / "output" / "video" / rfa.NOVEL_NAME
                    / f"part_{rfa.BOOK_INDEX:02d}_{rfa.BOOK_NAME}"
                    / f"chapter_{rfa.NOVEL_CHAPTER:03d}")
    official_dir.mkdir(parents=True)
    (official_dir / "script_result.json").write_text(
        _json.dumps({"title": "official", "shots": []}), encoding="utf-8")
    (official_dir / "step01_audit.json").write_text(
        _json.dumps({"sentinel": "do-not-touch"}), encoding="utf-8")

    snap_dir = official_dir / "snapshots"
    snap_dir.mkdir()
    frozen = snap_dir / "script_result_20260925_173311.json"
    frozen.write_text(_json.dumps({"title": "frozen", "shots": [
        {"index": 5, "prompt": "p", "keyframes": [{"desc": "d"}]}]}),
        encoding="utf-8")

    monkeypatch.setattr(rfa, "ROOT", tmp_path)
    monkeypatch.setattr(rfa, "_read_raw_chapter", lambda *a, **k: "原文")
    monkeypatch.setattr(rfa, "_final_audit_focus", lambda *a, **k: [])
    monkeypatch.setattr(rfa, "compute_script_revision", lambda *a, **k: "rev-frozen")

    class _R:
        status = "ok"
        issues: list = []
        error = ""
        raw_text = ""
        usage = {"input_tokens": 1, "output_tokens": 2, "reasoning_tokens": 3}

    monkeypatch.setattr(rfa, "run_same_model_audit_with_status",
                        lambda *a, **k: _R())

    rc = rfa.main(["--script", str(frozen), "--note", "诊断输入（非原始审计输出）"])
    assert rc == 0

    _out = snap_dir / "diag" / "script_result_20260925_173311.audit.json"
    assert _out.exists(), "诊断产物没落在 diag/ 子目录"
    # 候选池红线：诊断产物**不得**出现在 BEST-GUARD 的候选 glob 里
    # （`snapshots/script_result_*.json`，非递归 → 子目录天然隔离）
    assert sorted(p.name for p in snap_dir.glob("script_result_*.json")) == [
        "script_result_20260925_173311.json"], "诊断产物落进了候选池"
    _payload = _json.loads(_out.read_text(encoding="utf-8"))
    assert _payload["diagnostic"] is True
    assert "非原始审计输出" in _payload["note"]
    assert _payload["input_revision"] == "rev-frozen"
    assert _payload["usage"]["output_tokens"] == 2

    assert _json.loads((official_dir / "step01_audit.json").read_text(
        encoding="utf-8")) == {"sentinel": "do-not-touch"}, "正式审计产物被覆盖了"


def test_out_path_policy_table(tmp_path):
    """路径策略：正式稿 → 正式产物；非正式稿 → `diag/` 子目录；显式 `--out` 优先."""
    _official = tmp_path / "script_result.json"
    _audit = tmp_path / "step01_audit.json"
    _frozen = tmp_path / "snapshots" / "script_result_2026.json"

    _base, _paths, _is_off = rfa.resolve_out_paths(
        _official, _official, _audit, None, 1)
    assert _paths == [_audit] and _is_off

    _base, _paths, _is_off = rfa.resolve_out_paths(
        _frozen, _official, _audit, None, 1)
    assert not _is_off
    assert _paths == [tmp_path / "snapshots" / "diag" / "script_result_2026.audit.json"]

    _base, _paths, _is_off = rfa.resolve_out_paths(
        _frozen, _official, _audit, str(tmp_path / "x.json"), 2)
    assert _paths == [tmp_path / "x.r1.json", tmp_path / "x.r2.json"], _paths


@pytest.mark.parametrize("effort,expected", [
    (None, "script_result_2026.audit.json"),
    ("none", "script_result_2026.audit.none.json"),
])
def test_diag_default_output_is_isolated_from_the_candidate_glob(tmp_path, effort,
                                                                 expected):
    """诊断默认输出必须在 `diag/` 下、且**不**匹配候选 glob（用户 2026-09-25 决定）.

    这条不是"目录整洁"问题：候选 glob 是 `snapshots/script_result_*.json`
    （非递归），同名前缀的诊断产物会被当成历史稿参与选稿——实测当天 19:14 由
    此 BLOCK。effort 分档命名同样必须留在 `diag/` 内。
    """
    _frozen = tmp_path / "snapshots" / "script_result_2026.json"
    _base = rfa.diag_out_base(_frozen, effort)
    assert _base == tmp_path / "snapshots" / "diag" / expected
    assert _base.parent.name == "diag", "诊断产物没进 diag/ 子目录"
    # 用真实 glob 语义校验：`pathlib.Path.glob` 不递归，子目录里的文件不会命中
    _frozen.parent.mkdir(parents=True, exist_ok=True)
    _base.parent.mkdir(parents=True, exist_ok=True)
    _base.write_text("{}", encoding="utf-8")
    assert _base not in list(_frozen.parent.glob("script_result_*.json"))


def test_compare_audits_keys_on_shot_and_fact_evidence_not_code_only():
    """同一 `code`、证据不同 → 必须单独列出（只比代码会漏掉这一类）."""
    _a = {"status": "ok", "input_revision": "r", "issues": [
        {"shot_id": "5", "code": "model_audit_KF_ORDER", "severity": "visual_risk",
         "evidence": "灯盏在丢后又出现"},
        {"shot_id": "2", "code": "model_audit_STATE", "severity": "visual_risk",
         "evidence": "状态链断裂"}]}
    _b = {"status": "ok", "input_revision": "r", "issues": [
        {"shot_id": "5", "code": "model_audit_KF_ORDER", "severity": "visual_risk",
         "evidence": "灯盏！在丢后又出现。"},          # 标点差异 → 归一后一致
        {"shot_id": "7", "code": "model_audit_STATE", "severity": "visual_risk",
         "evidence": "状态链断裂"}]}

    _c = rfa.compare_audits(_a, _b)
    assert len(_c["same_shot_evidence"]) == 1, _c["same_shot_evidence"]
    assert _c["code_only"] == ["model_audit_KF_ORDER", "model_audit_STATE"]
    assert _c["only_in_a"] and _c["only_in_b"], "单侧结论必须列出"
    assert any("shot 2" or "2|" in x for x in _c["only_in_a"])
    assert any("7|" in x for x in _c["only_in_b"])

    # 同一 (镜头, code) 但证据不同 → 单列
    _a2 = {"issues": [{"shot_id": "5", "code": "c", "evidence": "甲"}]}
    _b2 = {"issues": [{"shot_id": "5", "code": "c", "evidence": "乙"}]}
    _c2 = rfa.compare_audits(_a2, _b2)
    assert len(_c2["code_matched_but_evidence_differs"]) == 1
    assert _c2["same_shot_evidence"] == []


# ---------------------------------------------------------------------------
# v6.124（验证计划 (c)）：审计专用思考档位
# ---------------------------------------------------------------------------
def test_audit_effort_env_is_honored_and_restored(tmp_path, monkeypatch):
    """`--effort` 只影响本次诊断审计；跑完必须还原环境（不污染后续运行）."""
    import json as _json

    monkeypatch.setattr(rfa, "ROOT", tmp_path)
    monkeypatch.setattr(rfa, "_read_raw_chapter", lambda *a, **k: "原文")
    monkeypatch.setattr(rfa, "_final_audit_focus", lambda *a, **k: [])
    monkeypatch.setattr(rfa, "compute_script_revision", lambda *a, **k: "rev")
    (tmp_path / "frozen.json").write_text(_json.dumps({"shots": []}), encoding="utf-8")

    _seen = {}

    class _R:
        status = "ok"
        issues: list = []
        error = ""
        raw_text = ""
        usage = None

    def _fake_audit(*a, **k):
        import os as _os
        _seen["effort"] = _os.environ.get("AUDIT_REASONING_EFFORT")
        return _R()

    monkeypatch.setattr(rfa, "run_same_model_audit_with_status", _fake_audit)
    monkeypatch.delenv("AUDIT_REASONING_EFFORT", raising=False)

    assert rfa.main(["--script", str(tmp_path / "frozen.json"), "--effort", "none",
                     "--out", str(tmp_path / "out.json")]) == 0
    assert _seen["effort"] == "none", "审计请求期必须看得到该档位"
    import os as _os2
    assert "AUDIT_REASONING_EFFORT" not in _os2.environ, "跑完必须还原环境"

    _payload = _json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert _payload["audit_reasoning_effort"] == "none"
    assert (tmp_path / "frozen.audit.none.json").exists() is False  # 显式 --out 优先
