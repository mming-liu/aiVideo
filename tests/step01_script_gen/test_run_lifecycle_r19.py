# -*- coding: utf-8 -*-
"""第十九轮复核三处（P1-① / P2-②③）：把允许关系登记成**合法作业表**.

复核的三个反例（离线执行级）：
  1. **章号与目录未配对**：两章批次里"请求第 1 章、读取 `chapter_002`"仍能启动；桩子进程把
     本轮诊断写进 `chapter_001`，汇总却去第 2 章读 ⇒ **漏收诊断、归档为空**。
  2. **缺章号的完成记录仍能冒充样本**：`{run:1, started:true}`（无章号）放进第 2 章批次 ⇒
     0 次调用、直接 SKIP、报"样本 1/1"。
  3. **写回没有接入关系校验**：`batch_put` 只收 `key` ⇒ 异批/第 99 轮/第 7 章的记录写回成功
     并计入样本，同一行随后又被读取校验拒绝——**读写两口径**。

处置：批次把允许关系登记成**合法作业表**（槽位—章号—规范目录）；启动请求、续跑记录、
写回候选表三处共用同一份 `batch_jobs()`。
"""
from __future__ import annotations

import pytest

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load(mod_name: str, filename: str, *, register: bool = False):
    _p = _ROOT / ".tmp" / filename
    if not _p.exists():
            # 2026-10：`.tmp/` 下的测试 harness 已丢失且**不会恢复**（用户决定）。
            # 依赖它的用例此前以 AttributeError 报红、淹没真实回归，现改为**显式跳过**；
            # harness 一旦补回，本函数自动转回正常执行，无需改动用例。
            pytest.skip("测试 harness 缺失（不恢复，2026-10 决定）: %s" % _p,
                        allow_module_level=True)
    _spec = importlib.util.spec_from_file_location(mod_name, _p)
    _mod = importlib.util.module_from_spec(_spec)
    if register:
        sys.modules[mod_name] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


def _lc():
    return _load("run_lifecycle", "run_lifecycle.py", register=True)


def _mk_chapter(root: Path, ch: int) -> Path:
    chd = root / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": []}, ensure_ascii=False), encoding="utf-8")
    return chd


# ---------------------------------------------------------------------------
# ① 章号 ↔ 目录必须配对
# ---------------------------------------------------------------------------
def test_the_job_table_pairs_slot_chapter_and_directory(tmp_path):
    """作业表按批次配置登记：按章续跑 ⇒ 每章一项、目录 = 部目录/chapter_NNN（成对）."""
    _l = _lc()
    if _l is None:
        return
    _base = tmp_path / "part_01"
    _cfg = _l.batch_config(driver="d", chapters=[1, 2], n_runs=2, chapter_dir=str(_base))
    _jobs = _l.batch_jobs(_cfg)
    assert [j["slot"] for j in _jobs] == [1, 2], _jobs
    assert [j["chapter"] for j in _jobs] == [1, 2], _jobs
    assert [j["dir"].split("/")[-1] for j in _jobs] == ["chapter_001", "chapter_002"], _jobs
    # 按轮续跑：每轮一项，章号固定、目录是章节目录本身
    _ch2 = _mk_chapter(tmp_path, 2)
    _cfg2 = _l.batch_config(driver="d", chapters=[2], n_runs=3, chapter_dir=str(_ch2))
    _jobs2 = _l.batch_jobs(_cfg2)
    assert [j["slot"] for j in _jobs2] == [1, 2, 3], _jobs2
    assert {j["chapter"] for j in _jobs2} == {2}, _jobs2
    assert len({j["dir"] for j in _jobs2}) == 1, _jobs2


def test_a_request_must_pair_chapter_with_directory(tmp_path, monkeypatch):
    """复核反例 1：请求第 1 章却读 `chapter_002` ⇒ **不启动**（第 19 轮 P1-①）.

    判别性：只要求"目录在共同根之下" ⇒ 本用例启动子进程，诊断写进 chapter_001、汇总去
    chapter_002 读，归档为空。
    """
    _l = _lc()
    if _l is None:
        return
    _base = tmp_path / "part_01"
    _c1 = _mk_chapter(_base, 1)
    _c2 = _mk_chapter(_base, 2)
    _cfg = _l.batch_config(driver="d", chapters=[1, 2], n_runs=2, chapter_dir=str(_base),
                           slot_key="chapter")
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    assert _b["ok"], _b
    calls = []
    monkeypatch.setattr(_l.subprocess, "call", lambda *a, **kw: calls.append(1) or 0)
    row = _l.run_once(str(_c2), str(tmp_path / "run"), chapter=1, batch=_b, config=_cfg)
    assert calls == [], "章号与目录不是同一项，却启动了子进程"
    assert row["started"] is False, row
    assert "配对" in row["not_started_reason"], row["not_started_reason"]
    # 对照组：章号与目录是同一项 ⇒ 启动；换成 chapter_001 也启动（各是各的一项）
    _ok = _l.run_once(str(_c1), str(tmp_path / "run2"), chapter=1, batch=_b, config=_cfg)
    assert _ok["started"] is True, _ok
    _ok2 = _l.run_once(str(_c2), str(tmp_path / "run3"), chapter=2, batch=_b, config=_cfg)
    assert _ok2["started"] is True, _ok2


# ---------------------------------------------------------------------------
# ② 记录必须完整对应作业表的一项（含章号）
# ---------------------------------------------------------------------------
def test_a_record_without_a_chapter_cannot_impersonate_a_sample(tmp_path, monkeypatch):
    """复核反例 2：第 2 章批次里 `{run:1}`（无章号）⇒ 拒绝（旧行为：SKIP、报"样本 1/1"）."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r19", "run10_ch2.py")
    chd = _mk_chapter(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 1)
    calls = []
    monkeypatch.setattr(_l.subprocess, "call", lambda *a, **kw: calls.append(1) or 0)
    assert _drv.main([]) == 0 and len(calls) == 1
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    _p = _bdir / "summary.json"
    _rows = json.loads(_p.read_text(encoding="utf-8"))
    _rows[0].pop("chapter", None)                       # 章号被抹掉
    _p.write_text(json.dumps(_rows, ensure_ascii=False), encoding="utf-8")
    _n = len(calls)
    assert _drv.main(["--resume", _bid]) == 2, "缺章号的记录没被拒"
    assert len(calls) == _n, "被拒的续跑仍然跑了"


def test_row_must_map_onto_one_job_entry(tmp_path):
    """单元：槽位在表里 + 章号与该项一致；缺章号/章号不符/槽位越界都要拒."""
    _l = _lc()
    if _l is None:
        return
    _ch2 = _mk_chapter(tmp_path, 2)
    _cfg = {"n_runs": 2, "chapters": [2], "chapter_dir": str(_ch2)}
    _scope = _l.batch_scope(_cfg)
    assert _l._row_problem({"run": 1, "chapter": 2, "started": True, "batch_id": "B", "run_id": "R1"},
                           "B", _scope) == ""
    assert "缺章号" in _l._row_problem({"run": 1, "started": True, "batch_id": "B", "run_id": "R1"},
                                      "B", _scope)
    assert "与作业表不符" in _l._row_problem({"run": 1, "chapter": 7, "started": True,
                                             "batch_id": "B", "run_id": "R1"}, "B", _scope)
    assert "合法作业表" in _l._row_problem({"run": 99, "chapter": 2, "started": True,
                                           "batch_id": "B", "run_id": "R1"}, "B", _scope)


# ---------------------------------------------------------------------------
# ③ 写回与读取同一口径
# ---------------------------------------------------------------------------
def test_batch_put_refuses_a_row_the_reader_would_reject(tmp_path):
    """复核反例 3：写回**先校验候选整表**，不合格就不写、并返回原因（不再读写两口径）."""
    _l = _lc()
    if _l is None:
        return
    _ch2 = _mk_chapter(tmp_path, 2)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=3, chapter_dir=str(_ch2),
                           slot_key="run")
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    _rows = []
    _bad = {"run": 99, "chapter": 2, "started": True, "batch_id": _b["batch_id"],
            "run_id": "R99"}
    _why = _l.batch_put(_rows, _bad, key="run", batch=_b)
    assert _why and "写回被拒" in _why, _why
    assert _rows == [], "被拒的记录仍写进了表"
    # 合法记录照常写回，并被计入样本
    _ok = {"run": 1, "chapter": 2, "started": True, "batch_id": _b["batch_id"],
           "run_id": "R1"}
    assert _l.batch_put(_rows, _ok, key="run", batch=_b) == "", _rows
    assert [r["run"] for r in _rows] == [1] and _l.batch_counts(_rows)["samples"] == 1


def test_batch_put_refuses_a_row_of_another_batch_or_chapter(tmp_path):
    """异批 / 异章的记录同样被写回拒掉（读侧会拒的东西，写侧不许放进来）."""
    _l = _lc()
    if _l is None:
        return
    _ch2 = _mk_chapter(tmp_path, 2)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=2, chapter_dir=str(_ch2),
                           slot_key="run")
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    for _row in ({"run": 1, "chapter": 2, "started": True, "batch_id": "OTHER", "run_id": "R1"},
                 {"run": 1, "chapter": 7, "started": True, "batch_id": _b["batch_id"]}):
        _rows = []
        assert _l.batch_put(_rows, dict(_row), key="run", batch=_b), _row
        assert _rows == [], _row


def test_the_driver_aborts_when_write_back_is_refused(tmp_path, monkeypatch):
    """入口级：写回被拒 ⇒ 该批中止（打印 BATCH ABORT），不会"写进去又被读拒绝"."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r19b", "run10_ch2.py")
    chd = _mk_chapter(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 3)          # 第 2 轮写回被拒 ⇒ 该批应中止（不跑第 3 轮）
    # 注入点=**写回候选表**（第 22 轮起收尾顺序是"算候选（不动表）→ 落台账 → 提交"，
    # `batch_put` 已不在收尾路径上；候选被拒与真实校验失败走同一条分支）
    _real_cand = _l._candidate_rows
    _state = {"n": 0}

    def _sabotage(rows, row, **kw):                    # 第二轮起：写回一律被拒
        _state["n"] += 1
        if _state["n"] > 1:
            return [], "写回被拒（记录与批次作业表不符）：测试注入"
        return _real_cand(rows, row, **kw)

    monkeypatch.setattr(_l, "_candidate_rows", _sabotage)
    _calls = []
    monkeypatch.setattr(_l.subprocess, "call", lambda *a, **kw: _calls.append(1) or 0)
    # 第 20 轮起：写回被拒 ⇒ 驱动**返回非零**（失败状态要留住）
    assert _drv.main([]) == 2
    assert len(_calls) == 2, "写回被拒后没有中止本批：%s" % _calls
    _rows = json.loads((sorted(out.glob("batch_*"))[0] / "summary.json")
                       .read_text(encoding="utf-8"))
    assert [r["run"] for r in _rows] == [1], _rows      # 只留下写回成功的那一轮
