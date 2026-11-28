# -*- coding: utf-8 -*-
"""第十三轮复核四处（含两处 P1）的离线回归：**真实入口 + 真实产物形态 + 合法复用**.

复核指出上一版的三条教训，正是这轮测试要盖住的：
- 现有"三个驱动共用实现"用例只是**源码字符串检查**，所以没抓到 `main()` 启动即崩；
- 汇总是拿**臆想的产物形态**在测（`phases` 写成 dict），真实 timing 是**列表**；
- 归属判据没考虑**合法复用**（第二次提交沿用第一次的审计报告）。

四处问题（全部离线复现，无模型调用）：
  1. **P1 两个批跑入口启动即崩**：`done = {{r.get("run") for r in rows}}`（模板转义残留，
     集合里装集合）⇒ `TypeError`，`run_once` 调用次数 0；
  2. **P1 正常 timing 产物让汇总崩溃**：`phases` 是列表，`dict(phases)` ⇒ `ValueError`；
  3. **P2 请求身份仍会重复**：秒级时间 + 父 PID，同进程同秒两轮同号 ⇒ 第二轮把首轮
     结果报成本轮 COMMITTED；
  4. **P2 合法复用的审计被归档器排除**：第二次提交沿用第一次的审计（manifest 属于 B、
     审计内 `run_id` 仍是 A）⇒ 归属检查拒绝，漏掉本轮 commit 明确登记的证据。
"""
from __future__ import annotations

import pytest

import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import step01_script_gen.plan_feasibility as pf  # noqa: E402


def _load(mod_name: str, filename: str):
    _p = _ROOT / ".tmp" / filename
    if not _p.exists():
            # 2026-10：`.tmp/` 下的测试 harness 已丢失且**不会恢复**（用户决定）。
            # 依赖它的用例此前以 AttributeError 报红、淹没真实回归，现改为**显式跳过**；
            # harness 一旦补回，本函数自动转回正常执行，无需改动用例。
            pytest.skip("测试 harness 缺失（不恢复，2026-10 决定）: %s" % _p,
                        allow_module_level=True)
    _spec = importlib.util.spec_from_file_location(mod_name, _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


def _lc():
    return _load("_lc_r13_%d" % int(time.time() * 1000 % 100000), "run_lifecycle.py")


def _write_chapter_dir(root: Path, ch: int = 1) -> Path:
    """一个**真实形态**的章节目录：`chapter_00N` + `script_result.json`（带 chapter_index）."""
    chd = root / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": [{"index": 1, "narration_segment": "甲"}]},
                   ensure_ascii=False), encoding="utf-8")
    return chd


def _write_committed_manifest(chd: Path, run_id: str, *, audit_run_id: str = "") -> dict:
    """写一份**能通过产品侧核验**的 commit manifest（revision + 三份产物 sha1/字节数）.

    `audit_run_id` 用来模拟**合法复用**：manifest 属于本轮，审计文件内的 `run_id` 仍是
    上一轮（第二次提交沿用第一次的审计报告）。
    """
    from shared.quality_gate import (STEP01_ARTIFACT_FILES, compute_script_revision,
                                     sha1_file)
    _script = json.loads((chd / "script_result.json").read_text(encoding="utf-8"))
    _arts = {}
    for _n in STEP01_ARTIFACT_FILES:
        _p = chd / _n
        if not _p.exists():
            _p.write_text(json.dumps({"report_type": _n}, ensure_ascii=False),
                          encoding="utf-8")
        _arts[_n] = {"sha1": sha1_file(_p), "bytes": _p.stat().st_size}
    if audit_run_id:
        _a = json.loads((chd / "step01_audit.json").read_text(encoding="utf-8"))
        _a["run_id"] = audit_run_id
        (chd / "step01_audit.json").write_text(json.dumps(_a, ensure_ascii=False),
                                              encoding="utf-8")
        _arts["step01_audit.json"] = {"sha1": sha1_file(chd / "step01_audit.json"),
                                      "bytes": (chd / "step01_audit.json").stat().st_size}
    _man = {"manifest_version": 1, "status": "COMMITTED", "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "script_revision": compute_script_revision(_script),
            "rule_source_fingerprint": "r", "rule_version": "v", "audit_scope": "full",
            "blocking_count": 0, "blocking": [], "blocking_truncated": False,
            "audit_status": "ok", "detail": "", "artifacts": _arts}
    (chd / "step01_commit.json").write_text(json.dumps(_man, ensure_ascii=False),
                                           encoding="utf-8")
    return _man


# ---------------------------------------------------------------------------
# ① 三个驱动的**真实 main()** 能启动、能落汇总
# ---------------------------------------------------------------------------
def test_both_batch_drivers_start_and_write_a_summary(tmp_path, monkeypatch):
    """两个批跑入口的 `main()` 必须真的跑起来（复核 P1-①）.

    上一版是模板转义残留（`{{...}}` ⇒ 集合里装集合），`main()` 一进门就 `TypeError`，
    `run_once` 调用 0 次——而"三个驱动共用实现"的旧用例只做源码字符串检查，看不见。
    判别性：把 `{{` 还回去 ⇒ 本用例抛 `TypeError`。
    """
    _l = _lc()
    if _l is None:
        return
    _calls = []
    monkeypatch.setattr(_l.subprocess, "call",
                        lambda *a, **kw: _calls.append((kw.get("env") or {})
                                                       .get("STEP01_RUN_ID")) or 0)
    for _name, _mod_name in (("run20_ch1.py", "_drv20_r13"), ("run10_ch2.py", "_drv10_r13")):
        _drv = _load(_mod_name, _name)
        assert _drv is not None, _name
        _chd = _write_chapter_dir(tmp_path / _mod_name)
        monkeypatch.setattr(_drv, "CH", str(_chd))
        monkeypatch.setattr(_drv, "OUT", str(tmp_path / (_mod_name + "_out")))
        monkeypatch.setattr(_drv, "N_RUNS", 2)
        _calls.clear()
        assert _drv.main() == 0, "%s.main() 没有正常返回" % _name
        assert len(_calls) == 2, "%s: run_once 调用 %d 次（应为 2）" % (_name, len(_calls))
        # 第 14 轮起 summary 落在**批次目录**里（`batch_<batch_id>/summary.json`），
        # 批次根只放各批次的目录——旧批残留不再被读到
        _bdirs = sorted((tmp_path / (_mod_name + "_out")).glob("batch_*"))
        assert len(_bdirs) == 1, _bdirs
        assert (_bdirs[0] / "batch.json").exists(), _bdirs
        _rows = json.loads((_bdirs[0] / "summary.json").read_text(encoding="utf-8"))
        assert len(_rows) == 2, _rows
        assert {r["run"] for r in _rows} == {1, 2}, _rows
        assert len({r["run_id"] for r in _rows}) == 2, "两轮身份相同：%s" % _rows


def test_the_single_run_driver_main_starts(tmp_path, monkeypatch):
    """单跑入口的 `main()` 也要真的跑起来（两章各一轮）."""
    _l = _lc()
    if _l is None:
        return
    _calls = []
    monkeypatch.setattr(_l.subprocess, "call", lambda *a, **kw: _calls.append(1) or 0)
    _drv = _load("_drv_verify_r13", "run_verify_2.py")
    assert _drv is not None
    _write_chapter_dir(tmp_path, 1)
    _write_chapter_dir(tmp_path, 2)
    monkeypatch.setattr(_drv, "BASE", str(tmp_path))
    monkeypatch.setattr(_drv, "OUT_ROOT", str(tmp_path / "verify"))
    assert _drv.main() == 0
    assert len(_calls) == 2, len(_calls)


# ---------------------------------------------------------------------------
# ② 真实产物形态：phases 是列表
# ---------------------------------------------------------------------------
def test_the_summary_keeps_the_real_list_shaped_phases(tmp_path):
    """`phases` 的真实形态是**列表**，汇总不许 `dict()` 它（复核 P1-②）.

    实测真实 `step01_timing.json`：`phases = [{"phase": "_total", "status": "ok",
    "phase_duration_sec": 17.5, …}]`。判别性：退回 `dict(phases)` ⇒ 本用例 `ValueError`。
    """
    _l = _lc()
    if _l is None:
        return
    _phases = [{"phase": "_total", "status": "ok", "phase_duration_sec": 17.5,
                "elapsed_since_start_sec": 17.5, "elapsed_sec": 17.5}]
    _timing = {"run_started": time.time(), "phases": _phases}
    _sel = pf.select_diagnostic({}, since=time.time(), expect_run_id="本轮")
    _row = _l.build_row(chapter=1, rc=0, duration_sec=17.5, run_id="本轮", diags={},
                        sel=_sel, commit={}, timing=_timing,
                        owned={"step01_timing.json": {"path": "x", "how": "run_started",
                                                      "run_id": ""}})
    assert _row["phases"] == _phases, _row["phases"]
    # 没有本轮 timing 时不许凭空造一个
    _row2 = _l.build_row(chapter=1, rc=0, duration_sec=1.0, run_id="本轮", diags={},
                         sel=_sel, commit={}, timing=_timing, owned={})
    assert _row2["phases"] is None, _row2["phases"]


def test_the_row_survives_a_real_shaped_timing_artifact(tmp_path, monkeypatch):
    """执行级：真的放一份**列表型 phases** 的 timing 到章节目录，跑一轮不许崩."""
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 1)
    _phases = [{"phase": "gate", "status": "ok", "phase_duration_sec": 1.0}]

    def _fake(argv, stdout=None, stderr=None, env=None):
        (chd / "step01_timing.json").write_text(
            json.dumps({"run_started": time.time(), "phases": _phases},
                       ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setattr(_l.subprocess, "call", _fake)
    row = _l.run_once(str(chd), str(tmp_path / "run"), chapter=1)
    assert row["phases"] == _phases, row["phases"]


# ---------------------------------------------------------------------------
# ③ 请求身份唯一 + 跨轮不误认
# ---------------------------------------------------------------------------
def test_two_rounds_in_the_same_second_get_distinct_identities():
    """同一进程同一秒的两轮必须拿到**不同**身份（复核 P2-③）.

    判别性：`new_run_id()` 退回"秒级时间 + PID"⇒ 本用例立刻失败。
    """
    _l = _lc()
    if _l is None:
        return
    _ids = [_l.new_run_id() for _ in range(200)]
    assert len(set(_ids)) == len(_ids), "同秒多轮出现重号：%s" % _ids[:5]


def test_a_later_round_does_not_inherit_the_previous_round_commit(tmp_path, monkeypatch):
    """第二轮**没写 commit** 时，不许把首轮的 COMMITTED 报成本轮终局（复核 P2-③ 反例）.

    执行级复现：首轮桩子写入 COMMITTED manifest（轮内身份），第二轮桩子什么都不写；
    若两轮身份相同，第二轮会把首轮 manifest 认成自己的 ⇒ 报出 COMMITTED。
    """
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 1)
    _round = {"n": 0, "ids": []}

    def _fake(argv, stdout=None, stderr=None, env=None):
        _rid = (env or {}).get("STEP01_RUN_ID", "")
        _round["n"] += 1
        _round["ids"].append(_rid)
        if _round["n"] == 1:
            _write_committed_manifest(chd, _rid)
            return 0
        return 1                                   # 第二轮提前失败，什么都不写

    monkeypatch.setattr(_l.subprocess, "call", _fake)
    _r1 = _l.run_once(str(chd), str(tmp_path / "run1"), chapter=1)
    _r2 = _l.run_once(str(chd), str(tmp_path / "run2"), chapter=1)

    assert _r1["commit_owned"] is True and _r1["status"] == "COMMITTED", _r1
    assert _r2["commit_owned"] is False, "第二轮认领了首轮的提交产物：%s" % _r2
    assert _r2["status"] is None and _r2["blocking_count"] is None, _r2
    assert _r2["run_id"] != _r1["run_id"], "两轮身份相同"


# ---------------------------------------------------------------------------
# ④ 合法复用：审计按本轮 manifest 的摘要归属
# ---------------------------------------------------------------------------

def _archived(run_dir: Path, name: str) -> list:
    """按执行身份隔离的归档目录里的产物（第 25 轮复核：`run_dir/exec-<run_id>/`）."""
    return sorted(run_dir.glob("exec-*/%s" % name))

def test_a_reused_audit_is_owned_and_archived_with_its_source_identity(tmp_path, monkeypatch):
    """第二次提交**沿用**第一次的审计报告时，归档器不得把它排除（复核 P2-④）.

    真实形态：manifest 属于本轮（B），而审计文件内部的 `run_id` 仍是上一轮（A）。
    归属按**本轮 manifest 的 revision + 摘要**判定 ⇒ 它属于本轮，且 `source_run_id`
    保留为 A（来源身份）。判别性：审计退回"只要产物自带 run_id"判归属 ⇒ 本用例失败。
    """
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 1)

    def _fake(argv, stdout=None, stderr=None, env=None):
        _write_committed_manifest(chd, (env or {}).get("STEP01_RUN_ID", ""),
                                  audit_run_id="上一轮A")
        return 0

    monkeypatch.setattr(_l.subprocess, "call", _fake)
    _run_dir = tmp_path / "run"
    row = _l.run_once(str(chd), str(_run_dir), chapter=1)

    assert row["commit_owned"] is True and row["commit_verified"] is True, row
    assert "step01_audit.json" in row["owned_artifacts"], row["owned_artifacts"]
    _src = row["artifact_sources"]["step01_audit.json"]
    assert _src["how"] == "manifest", _src
    assert _src["run_id"] == "上一轮A", _src          # 来源身份保留
    assert _archived(_run_dir, "step01_audit.json"), "复用来的审计没有归档"


def test_a_manifest_that_fails_verification_is_reported_not_silently_trusted(
        tmp_path, monkeypatch):
    """提交产物过不了 manifest 核验时：**照报终局**，但把核验失败显式写出来.

    反向保护：不能因为"核验没过"就把本轮的 BLOCK/COMMITTED 终局一并丢掉（批跑读数的
    主要用途就是读终局），也不能默默当它核验通过。
    """
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 1)

    def _fake(argv, stdout=None, stderr=None, env=None):
        _write_committed_manifest(chd, (env or {}).get("STEP01_RUN_ID", ""))
        # 提交后有人动了正式产物 ⇒ 摘要对不上
        (chd / "step01_audit.json").write_text('{"changed": true}', encoding="utf-8")
        return 0

    monkeypatch.setattr(_l.subprocess, "call", _fake)
    row = _l.run_once(str(chd), str(tmp_path / "run"), chapter=1)

    assert row["commit_owned"] is True and row["status"] == "COMMITTED", row
    assert row["commit_verified"] is False, row
    assert "changed after commit" in row["commit_verify_error"], row["commit_verify_error"]
    # 核验没过 ⇒ **不许**拿 manifest 当归属证明（该审计只能靠其它判据各自成立）
    _how = {_n: _v["how"] for _n, _v in row["artifact_sources"].items()}
    assert _how.get("step01_audit.json") != "manifest", _how
