# -*- coding: utf-8 -*-
"""诊断链的**身份 / 生命周期 / 异常**贯通回归（第十一~十二轮复核）.

第十一轮三处（身份没传到汇总 / 旧诊断冒充本轮 / 写盘失败静默）已修；第十二轮复核对
「正常路径」确认通过后，又指出**归属还不是所有入口与汇总字段共同遵守的不变量**——
驱动侧的回归因此从"某个驱动的内部函数"改指**共用实现** `.tmp/run_lifecycle.py`。
第十二轮的四类离线反例在同目录 `test_run_lifecycle_r12.py`。
"""
from __future__ import annotations

import pytest

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import step01_script_gen.plan_feasibility as pf  # noqa: E402

_PLAN = {"plan_contract_version": 4,
         "dialogue_lines": ["甲"],
         "narration_lines": [],
         "shot_beats": ["节拍：甲｜本镜信息目的：x｜原文依据：“甲”｜载体：画面"]}


def _now_str(offset: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + offset))


def _load(mod_name: str, filename: str):
    """按**文件路径**载入 `.tmp/` 下的工具（它们不是包的一部分）."""
    _p = _ROOT / ".tmp" / filename
    if not _p.exists():
        # 2026-10：`.tmp/` 下的测试 harness 已丢失且**不会恢复**（用户决定）→ 显式跳过，
        # 不再以 AttributeError / FileNotFoundError 报红淹没真实回归；补回即自动恢复。
            pytest.skip("测试 harness 缺失（不恢复，2026-10 决定）: %s" % _p,
                        allow_module_level=True)
    _spec = importlib.util.spec_from_file_location(mod_name, _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


# ---------------------------------------------------------------------------
# ① 身份：诊断自报的计划版本 + 汇总身份取自**选中的那一份**
# ---------------------------------------------------------------------------
def test_the_dialogue_diagnostic_records_the_current_plan_version(tmp_path):
    """台词诊断要带**本轮计划的契约版本**（复核 P2-①：没接计划时落成 0）.

    判别性：`dump_dialogue_incomplete` 不传 `plan=` ⇒ 本用例读到的版本是 0 而非 4。
    """
    pf.dump_dialogue_incomplete(tmp_path, [[{"line": "甲", "reason": "not_found"}]],
                                total_lines=1, plan=_PLAN)
    diag = json.loads((tmp_path / "step01_dialogue_incomplete.json")
                      .read_text(encoding="utf-8"))
    assert diag["plan_contract_version"] == 4, diag
    assert diag["stop_layer"] == pf.STOP_LAYER_CANDIDATE, diag


def test_summary_identity_comes_from_the_selected_diagnostic():
    """**只有台词诊断**时，停止层与身份也要成对给出（复核 P2-①反例）.

    旧实现 `_this_run = contract or infeas` 把台词诊断排除在身份来源之外 ⇒
    汇总写出「停止层=final、run_id 空、契约版本=None」。判别性：去掉 `dialogue`
    这一路，run_id/版本立刻变空。
    """
    _diag = {"run_id": "20261001T090000-4242", "written_at": _now_str(),
             "stop_layer": pf.STOP_LAYER_FINAL, "plan_contract_version": 4,
             "conclusion": "dialogue_incomplete"}
    _sel = pf.select_diagnostic(
        (("step01_plan_contract_invalid.json", {}),
         ("step01_plan_infeasible.json", {}),
         ("step01_dialogue_incomplete.json", _diag)), since=time.time() - 60)
    assert _sel["file"] == "step01_dialogue_incomplete.json", _sel
    assert _sel["stop_layer"] == pf.STOP_LAYER_FINAL, _sel
    assert _sel["run_id"] == "20261001T090000-4242", _sel
    assert _sel["plan_contract_version"] == 4, _sel
    assert _sel["stale_files"] == [] and _sel["mixed_run_ids"] == [], _sel


def test_the_contract_version_may_come_from_the_embedded_plan():
    """老写法（身份在整份计划里）也能取到版本——读取方不必认死一种形态."""
    _diag = {"run_id": "r", "written_at": _now_str(),
             "stop_layer": pf.STOP_LAYER_PLAN, "plan": {"plan_contract_version": 4}}
    _sel = pf.select_diagnostic({"step01_plan_contract_invalid.json": _diag},
                                since=time.time() - 60)
    assert _sel["plan_contract_version"] == 4, _sel


# ---------------------------------------------------------------------------
# ② 旧文件隔离：不在本轮的诊断一律不采用
# ---------------------------------------------------------------------------
def test_a_diagnostic_written_before_this_run_is_not_adopted():
    """`written_at` 早于本轮起点 ⇒ 判为陈旧，**不被采用**（复核 P2-②）.

    判别性：退回 mtime 余量判据（或去掉时间判据）⇒ 这条旧诊断会被当成本轮结果。
    """
    _old = {"run_id": "上一轮", "written_at": _now_str(-30),
            "stop_layer": pf.STOP_LAYER_FINAL, "plan_contract_version": 4}
    _sel = pf.select_diagnostic(
        {"step01_dialogue_incomplete.json": _old}, since=time.time())
    assert _sel["file"] == "" and _sel["stop_layer"] == "", _sel
    assert _sel["stale_files"] == ["step01_dialogue_incomplete.json"], _sel
    assert "没有本轮诊断" in _sel["reason"], _sel


def test_a_diagnostic_without_a_timestamp_is_not_adopted():
    """没有 `written_at`（判不出身份）⇒ **fail-closed 不算本轮**，不猜."""
    _sel = pf.select_diagnostic(
        {"step01_dialogue_incomplete.json": {"run_id": "x", "stop_layer": "final"}},
        since=time.time())
    assert _sel["file"] == "" and _sel["run_id"] == "", _sel
    assert _sel["stale_files"] == ["step01_dialogue_incomplete.json"], _sel


def test_this_run_diagnostic_is_adopted_and_mixed_rounds_are_flagged():
    """本轮诊断被采用；同一轮混进别的 run_id 时要**显式标出**（不静默混用身份）."""
    _t0 = time.time() - 60
    _sel = pf.select_diagnostic(
        (("step01_plan_contract_invalid.json", {"run_id": "本轮", "written_at": _now_str(),
                                                "stop_layer": pf.STOP_LAYER_PLAN}),
         ("step01_dialogue_incomplete.json", {"run_id": "别轮", "written_at": _now_str(),
                                              "stop_layer": pf.STOP_LAYER_FINAL})),
        since=_t0)
    assert _sel["file"] == "step01_plan_contract_invalid.json", _sel
    assert _sel["run_id"] == "本轮", _sel
    assert _sel["mixed_run_ids"] == ["step01_dialogue_incomplete.json"], _sel


# ---------------------------------------------------------------------------
# ③ 共用生命周期（驱动不再各写一份）
# ---------------------------------------------------------------------------
def test_the_shared_lifecycle_clears_every_registered_diagnostic(tmp_path):
    """清场必须覆盖**全部**登记诊断，且先归档再删（复核 P2-②：旧实现漏掉台词诊断）.

    判别性：清场退回手写的两份 ⇒ 台词诊断的旧文件留在原地，本用例失败。
    """
    _lc = _load("_lc_r11a", "run_lifecycle.py")
    if _lc is None:
        return
    chd = tmp_path / "chapter_001"
    chd.mkdir()
    # 第 43 轮：名单是**结论 + 附属取证**（`DIAGNOSTIC_ARTIFACT_FILES`）——取证包此前
    # 不在任何名单里，清场清不掉、也不归档，后来的同名写入会把它覆盖
    for _n in pf.DIAGNOSTIC_ARTIFACT_FILES:
        (chd / _n).write_text("{}", encoding="utf-8")
    _warns = _lc.clear_prev_diagnostics(str(chd), str(tmp_path / "run" / "prev"))
    assert _warns == [], _warns
    for _n in pf.DIAGNOSTIC_ARTIFACT_FILES:
        assert not (chd / _n).exists(), "上一轮诊断没清干净：%s" % _n
        assert (tmp_path / "run" / "prev" / _n).exists(), "清掉的旧诊断没有归档：%s" % _n
    assert set(pf.DIAGNOSTIC_ARTIFACT_FILES) <= set(_lc.ARTIFACTS), _lc.ARTIFACTS
    assert "step01_plan_transform_rejected.json" in pf.DIAGNOSTIC_EVIDENCE_FILES


def test_a_stale_diagnostic_cannot_survive_into_this_round(tmp_path, monkeypatch):
    """执行级反例：上一轮的诊断**放进本章目录**，本轮提前失败也不许被采用.

    旧诊断的 `written_at` 故意写成"看上去属于本轮"（比现在晚 2 秒）——只为证明隔离
    靠的是**启动前清场**，而不是时间判据本身（秒级时间戳有边界，清场没有）。
    判别性：清场漏掉台词诊断 ⇒ 本用例读到 `stop_layer=final` 的上一轮结论。
    """
    _lc = _load("_lc_r11b", "run_lifecycle.py")
    if _lc is None:
        return
    chd = tmp_path / "chapter_001"
    chd.mkdir()
    _stale = {"run_id": "上一轮", "written_at": _now_str(+2),
              "stop_layer": pf.STOP_LAYER_FINAL, "conclusion": "dialogue_incomplete"}
    (chd / "step01_dialogue_incomplete.json").write_text(
        json.dumps(_stale, ensure_ascii=False), encoding="utf-8")
    (chd / "step01_commit.json").write_text(
        json.dumps({"run_id": "上一轮提交"}, ensure_ascii=False), encoding="utf-8")
    _old = time.time() - 60
    for _f in chd.iterdir():
        os.utime(str(_f), (_old, _old))

    monkeypatch.setattr(_lc.subprocess, "call", lambda *a, **kw: 0)   # 不跑流水线
    row = _lc.run_once(str(chd), str(tmp_path / "run"), chapter=1)

    assert row["stop_layer"].startswith("未识别"), row
    assert row["diagnostic_file"] == "" and row["commit_owned"] is False, row
    assert not (chd / "step01_dialogue_incomplete.json").exists(), "旧诊断还在原地"
    assert list((tmp_path / "run").glob(
        "exec-*/prev/step01_dialogue_incomplete.json")), \
        "旧诊断没有归档"


def test_the_driver_reports_identity_for_a_dialogue_only_stop(tmp_path, monkeypatch):
    """执行级贯通：本轮**只写了台词诊断**时，汇总要报出它的层与身份（复核 P2-①）.

    桩子进程扮演"跑到候选层台词门就停"的流水线：它用**驱动注入的 `STEP01_RUN_ID`**
    写诊断（顺带钉住"父子同一个本轮身份"），不写任何计划层诊断、不提交。
    判别性：汇总身份退回「`contract or infeas`」⇒ run_id 变空、契约版本变 None，
    而停止层仍报 candidate/final（正是复核观测到的自相矛盾）。
    """
    _lc = _load("_lc_r11c", "run_lifecycle.py")
    if _lc is None:
        return
    chd = tmp_path / "chapter_001"
    chd.mkdir()
    _seen = {}

    def _fake(argv, stdout=None, stderr=None, env=None):
        _seen["run_id"] = (env or {}).get("STEP01_RUN_ID", "")
        _seen["chapter"] = (env or {}).get("NOVEL_CHAPTER", "")
        # 子进程就是这么拿到本轮身份的：驱动注入 env，产品侧认它
        monkeypatch.setenv("STEP01_RUN_ID", _seen["run_id"])
        _diag = pf._diag_identity(pf.STOP_LAYER_CANDIDATE, _PLAN)
        assert _diag["run_id"] == _seen["run_id"], "产品侧没有认驱动注入的本轮身份"
        _diag.update({"conclusion": "dialogue_incomplete", "layer": "候选层"})
        (chd / "step01_dialogue_incomplete.json").write_text(
            json.dumps(_diag, ensure_ascii=False), encoding="utf-8")
        return 1

    monkeypatch.setattr(_lc.subprocess, "call", _fake)
    row = _lc.run_once(str(chd), str(tmp_path / "run"), chapter=1)

    assert _seen["run_id"], "驱动没有把本轮身份注入子进程"
    assert _seen["chapter"] == "1", _seen
    assert row["run_id"] == _seen["run_id"], row
    assert row["stop_layer"] == pf.STOP_LAYER_CANDIDATE, row
    assert row["diagnostic_file"] == "step01_dialogue_incomplete.json", row
    assert row["plan_contract_version"] == 4, row
    assert row["identity_source"] == "step01_dialogue_incomplete.json", row


# ---------------------------------------------------------------------------
# ④ 写盘失败可见性
# ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.warns = []

    def warn(self, msg):
        self.warns.append(str(msg))

    def info(self, msg):
        pass


def test_a_write_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    """三份诊断的写盘失败都要**告警**（复核 P2-③：台词诊断仍是静默 `pass`）.

    判别性：任一处退回 `except Exception: pass` ⇒ 对应用例读到 0 条告警。
    """
    import shared.json_io as jio

    def _boom(*a, **kw):
        raise OSError("磁盘满")

    monkeypatch.setattr(jio, "atomic_dump_json", _boom)
    _rec = _Recorder()
    monkeypatch.setattr(pf, "_log", lambda: _rec)

    pf.dump_plan_contract_invalid(tmp_path, _PLAN, ["F1 声明……"])
    pf._dump(tmp_path, {"verdict": "infeasible", "reasons": [], "suggestions": [],
                        "dialogue_lines": 0, "dialogue_sec": 0.0,
                        "dialogue_rejected": [], "beats_floor_sec": 0.0,
                        "lower_bound_sec": 0.0, "budget": {"min": 60, "max": 90},
                        "note": ""}, _PLAN)
    pf.dump_dialogue_incomplete(tmp_path, [[{"line": "甲", "reason": "not_found"}]],
                                total_lines=1, plan=_PLAN)

    assert len(_rec.warns) == 3, _rec.warns
    assert any("契约诊断" in w for w in _rec.warns), _rec.warns
    assert any("预算诊断" in w for w in _rec.warns), _rec.warns
    assert any("台词诊断" in w for w in _rec.warns), _rec.warns
    assert not (tmp_path / "step01_dialogue_incomplete.json").exists()


def test_a_successful_write_reports_no_warning(tmp_path, monkeypatch):
    """对照组：正常写盘不产生告警（避免"只要有写就告警"的假通过）."""
    _rec = _Recorder()
    monkeypatch.setattr(pf, "_log", lambda: _rec)
    pf.dump_dialogue_incomplete(tmp_path, [[{"line": "甲", "reason": "not_found"}]],
                                total_lines=1, plan=_PLAN)
    assert _rec.warns == [], _rec.warns
    assert (tmp_path / "step01_dialogue_incomplete.json").exists()
