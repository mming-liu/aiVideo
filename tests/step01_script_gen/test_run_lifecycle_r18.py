# -*- coding: utf-8 -*-
"""第十八轮复核两处（P1-① / P2-②）：批次必须**完整定义**「槽位—章号—目录」关系.

复核反例：
  1. 批次登记 `chapters=[2]`，却 `run_once(..., chapter=7)` —— 请求**自身**一致，但
     **不属于该批**，旧守卫只看配置与源码指纹 ⇒ `started=True`，子进程收到第 7 章。
  2. 记录校验只核"选中的那个槽位字段"：`{run:1, chapter:7}` 在第 2 章批次里只核 `run`，
     章号被忽略（0 次调用、直接 SKIP、报"样本 1/1"）；只有 `chapter` 的记录混进按 `run`
     续跑的批次也能过，跑一轮后变"样本 2/1"。

处置：批次元数据登记**槽位键**与允许的章号/轮次/目录；启动（`request_scope_problem`）、
续跑读取与记录写回（`_validate_rows` / `batch_done` / `batch_put`）共用这份关系。
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


def _write_chapter_dir(root: Path, ch: int) -> Path:
    chd = root / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": []}, ensure_ascii=False), encoding="utf-8")
    return chd


def _stub(monkeypatch, lc):
    calls = []
    monkeypatch.setattr(lc.subprocess, "call", lambda *a, **kw: calls.append(1) or 0)
    return calls


# ---------------------------------------------------------------------------
# ① 启动：请求必须**属于**这个批次
# ---------------------------------------------------------------------------
def test_a_request_outside_the_batch_chapters_is_refused(tmp_path, monkeypatch):
    """复核反例 1：批次 `chapters=[2]` + `run_once(chapter=7)` ⇒ 不启动（第 18 轮 P1-①）.

    判别性：守卫只看配置与源码指纹（不核请求的章号属不属于本批）⇒ 本用例子进程被启动。
    """
    _l = _lc()
    if _l is None:
        return
    _ch2 = _write_chapter_dir(tmp_path, 2)
    _ch7 = _write_chapter_dir(tmp_path, 7)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=1, chapter_dir=str(_ch2),
                           slot_key="run")
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    assert _b["ok"], _b
    calls = _stub(monkeypatch, _l)
    row = _l.run_once(str(_ch7), str(tmp_path / "run"), chapter=7, batch=_b, config=_cfg)
    assert calls == [], "请求第 7 章不属于本批（[2]），却启动了子进程"
    assert row["started"] is False, row
    assert "不属于本批" in row["not_started_reason"], row["not_started_reason"]


def test_a_request_inside_the_batch_chapters_still_starts(tmp_path, monkeypatch):
    """对照组：章号属于本批、目录在批次的目录之下 ⇒ 照常启动（别把合法路径打死）."""
    _l = _lc()
    if _l is None:
        return
    _ch2 = _write_chapter_dir(tmp_path, 2)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=1, chapter_dir=str(_ch2),
                           slot_key="run")
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    calls = _stub(monkeypatch, _l)
    row = _l.run_once(str(_ch2), str(tmp_path / "run"), chapter=2, batch=_b, config=_cfg)
    assert len(calls) == 1 and row["started"] is True, row


def test_a_request_outside_the_batch_directory_is_refused(tmp_path, monkeypatch):
    """目录也要落在批次允许的关系内（章号对、目录在别处 ⇒ 拒）."""
    _l = _lc()
    if _l is None:
        return
    _ch2 = _write_chapter_dir(tmp_path, 2)
    _elsewhere = _write_chapter_dir(tmp_path / "other", 2)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=1, chapter_dir=str(_ch2),
                           slot_key="run")
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    calls = _stub(monkeypatch, _l)
    row = _l.run_once(str(_elsewhere), str(tmp_path / "run"), chapter=2, batch=_b,
                      config=_cfg)
    assert calls == [] and row["started"] is False, row
    assert "不在本批" in row["not_started_reason"], row["not_started_reason"]


def test_the_scope_relation_is_registered_in_the_batch_meta(tmp_path):
    """批次元数据要**明确登记**槽位键与允许关系（不能读取时临时猜）."""
    _l = _lc()
    if _l is None:
        return
    _cfg = _l.batch_config(driver="d", chapters=[1, 2], n_runs=2, chapter_dir="/base")
    assert _cfg["slot_key"] == "chapter", _cfg            # 多章 ⇒ 按章续跑
    assert _l.batch_config(driver="d", chapters=[2], n_runs=10)["slot_key"] == "run"
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    _meta = json.loads((Path(_b["dir"]) / "batch.json").read_text(encoding="utf-8"))
    assert _meta["config"]["slot_key"] == "chapter", _meta["config"]
    assert _b["slot_key"] == "chapter", _b
    _scope = _l.batch_scope(_meta["config"])
    assert _scope["slot_key"] == "chapter" and _scope["chapters"] == [1, 2], _scope
    # 作业表登记在批次元数据里：章号 ↔ 目录**成对**
    assert [j["chapter"] for j in _scope["jobs"]] == [1, 2], _scope["jobs"]
    assert len({j["dir"] for j in _scope["jobs"]}) == 2, _scope["jobs"]
    assert _scope["jobs"] == _meta["config"]["jobs"], _scope["jobs"]


# ---------------------------------------------------------------------------
# ② 读取：整表按批次关系校验（章号仍要核；键由批次定义）
# ---------------------------------------------------------------------------
def test_a_row_with_a_foreign_chapter_is_refused_even_when_the_key_is_run(tmp_path,
                                                                         monkeypatch):
    """复核反例 2：第 2 章批次里的 `{run:1, chapter:7}` ⇒ 拒绝（旧行为：只核 run，直接 SKIP）."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r18", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 1)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0 and len(calls) == 1
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    _p = _bdir / "summary.json"
    _rows = json.loads(_p.read_text(encoding="utf-8"))
    _rows[0]["chapter"] = 7                                # 章号被改成别的章
    _p.write_text(json.dumps(_rows, ensure_ascii=False), encoding="utf-8")
    _n = len(calls)
    assert _drv.main(["--resume", _bid]) == 2, "异章记录没被拒"
    assert len(calls) == _n, "被拒的续跑仍然跑了"


def test_a_row_of_another_slot_key_is_refused_and_not_counted(tmp_path, monkeypatch):
    """复核反例 2 的另一半：按 `run` 续跑的批次里混进只有 `chapter` 的记录 ⇒ 拒绝.

    旧行为：这条记录过了校验、被计入样本，但 `batch_done(key="run")` 看不见它 ⇒
    再跑一轮变成"样本 2/1"。
    """
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r18b", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 2)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0 and len(calls) == 2
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    _p = _bdir / "summary.json"
    _rows = json.loads(_p.read_text(encoding="utf-8"))
    _rows.append({"chapter": 2, "started": True, "batch_id": _bid, "attempt": 1})
    _p.write_text(json.dumps(_rows, ensure_ascii=False), encoding="utf-8")
    _n = len(calls)
    assert _drv.main(["--resume", _bid]) == 2, "混键记录没被拒"
    assert len(calls) == _n, "被拒的续跑仍然跑了"


def test_a_legal_resume_still_passes(tmp_path, monkeypatch):
    """对照组：合法续跑（本批自己的记录）照常工作——只跳过已完成槽位，不新跑."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r18c", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 2)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0 and len(calls) == 2
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    assert _drv.main(["--resume", _bid]) == 0, "合法续跑被拒"
    assert len(calls) == 2, "合法续跑又跑了已完成的两轮：%s" % calls
    _rows = json.loads((_bdir / "summary.json").read_text(encoding="utf-8"))
    assert _l.batch_counts(_rows) == {"samples": 2, "attempts": 2, "rejected": 0, "superseded": 0,
                                      "pending": 0, "not_started": 0, "rows": 2,
                                      "verifiable": -1}, _l.batch_counts(_rows)
