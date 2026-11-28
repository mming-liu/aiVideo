# -*- coding: utf-8 -*-
"""第十六轮工具侧两处（复核 P1-③ / P2-④）：**核验最终生效配置** + **逐行核验批次身份**.

  1. **守卫核的配置不等于实际执行配置**：环境采集漏了 `STEP01_TOTAL_BUDGET_SEC` /
     `AUDIT_TIMEOUT_SEC` / `PROMPT_MAX_CHARS`（实测把总预算 1200→60、审计超时 300→5，
     续跑仍被允许）；`env_extra` 在守卫**之后**才覆盖环境 ⇒ 批次登记端点 A、子进程实际
     用 B，照样启动。
  2. **续跑不核记录自身的批次身份**：塞一条 `batch_id=OTHER_BATCH, started=True` 的记录，
     驱动返回 rc=0、子进程调用 0 次、"样本 1/1"。
"""
from __future__ import annotations

import pytest

import importlib.util
import json
import sys
import time
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


def _write_chapter_dir(root: Path, ch: int = 2) -> Path:
    chd = root / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": []}, ensure_ascii=False), encoding="utf-8")
    return chd


def _stub(monkeypatch, lc):
    calls = []

    def _call(*a, **kw):
        calls.append(1)
        return 0

    monkeypatch.setattr(lc.subprocess, "call", _call)
    return calls


# ---------------------------------------------------------------------------
# ① 配置核验：看的是**最终生效**的那份（含 env_extra）
# ---------------------------------------------------------------------------
def test_the_budget_and_timeout_configs_are_collected(tmp_path, monkeypatch):
    """复核反例：`STEP01_TOTAL_BUDGET_SEC` / `AUDIT_TIMEOUT_SEC` 改了必须被发现（P1-③）.

    判别性：退回"只认文件里像 provider/model 的名字 + 一条窄正则" ⇒ 这两个键收不进来。
    """
    _l = _lc()
    if _l is None:
        return
    monkeypatch.setenv("STEP01_TOTAL_BUDGET_SEC", "1200")
    monkeypatch.setenv("AUDIT_TIMEOUT_SEC", "300")
    monkeypatch.setenv("PROMPT_MAX_CHARS", "50000")
    cfg = _l._env_config()
    for _k in ("STEP01_TOTAL_BUDGET_SEC", "AUDIT_TIMEOUT_SEC", "PROMPT_MAX_CHARS"):
        assert _k in cfg, "%s 没进配置采集" % _k

    _b = _l.open_batch(str(tmp_path / "b"), config=_l.batch_config(driver="d", chapters=[2],
                                                                  n_runs=1))
    monkeypatch.setenv("STEP01_TOTAL_BUDGET_SEC", "60")
    monkeypatch.setenv("AUDIT_TIMEOUT_SEC", "5")
    _b2 = _l.open_batch(str(tmp_path / "b"), config=_l.batch_config(driver="d", chapters=[2],
                                                                   n_runs=1),
                        resume=_b["batch_id"])
    assert _b2["ok"] is False, _b2
    assert "STEP01_TOTAL_BUDGET_SEC" in _b2["reason"] and "AUDIT_TIMEOUT_SEC" in _b2["reason"], \
        _b2["reason"]


def test_noise_env_drift_does_not_block_the_resume(tmp_path, monkeypatch):
    """噪声（PATH/TEMP/按章设置的 NOVEL_CHAPTER）变化**不该**拦续跑——否则守卫没法用."""
    _l = _lc()
    if _l is None:
        return
    _cfg = _l.batch_config(driver="d", chapters=[1, 2], n_runs=2)
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    monkeypatch.setenv("PATH", "/somewhere/else")
    monkeypatch.setenv("NOVEL_CHAPTER", "2")          # 两章对照时按章切换
    monkeypatch.setenv("STEP01_RUN_ID", "本轮身份")     # 每轮都不同
    _b2 = _l.open_batch(str(tmp_path / "b"), config=_cfg, resume=_b["batch_id"])
    assert _b2["ok"] is True, _b2["reason"]


def test_the_guard_checks_the_environment_the_child_will_actually_get(tmp_path, monkeypatch):
    """复核反例：批次登记端点 A、`env_extra` 把子进程环境改成 B ⇒ 该轮**不启动**（P1-③）.

    判别性：守卫只看 `os.environ`（不看合并后的 `_env`）⇒ 本用例里子进程会被启动。
    """
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 2)
    monkeypatch.setenv("STEP01_TOTAL_BUDGET_SEC", "1200")
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=1, chapter_dir=str(chd))
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    assert _b["ok"], _b
    calls = _stub(monkeypatch, _l)
    _row = _l.run_once(str(chd), str(tmp_path / "run"), chapter=2, batch=_b, config=_cfg,
                       env_extra={"STEP01_TOTAL_BUDGET_SEC": "60"})
    assert calls == [], "子进程环境与批次登记不符，却仍然启动"
    assert _row["started"] is False, _row
    assert "STEP01_TOTAL_BUDGET_SEC" in _row["not_started_reason"], _row["not_started_reason"]


# ---------------------------------------------------------------------------
# ② 续跑逐行核验批次身份
# ---------------------------------------------------------------------------
def test_a_foreign_row_in_the_batch_summary_refuses_the_resume(tmp_path, monkeypatch):
    """复核反例：`batch_id=OTHER_BATCH, started=True` 的记录不许被当成本批已完成（P2-④）.

    判别性：`open_batch` 不过滤记录（接受任意字典）⇒ 驱动会 rc=0、0 次子进程调用、
    报"样本 1/1"。
    """
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r16", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 1)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    assert len(calls) == 1 and _bid

    # 往批次 summary 里塞一条"别批的记录"（身份/槽位都对不上）
    _p = _bdir / "summary.json"
    _rows = json.loads(_p.read_text(encoding="utf-8"))
    _rows.append({"run": 2, "started": True, "batch_id": "OTHER_BATCH"})
    _p.write_text(json.dumps(_rows, ensure_ascii=False), encoding="utf-8")

    _n = len(calls)
    assert _drv.main(["--resume", _bid]) == 2, "异批记录被当成已完成"
    assert len(calls) == _n, "被拒的续跑仍然跑了"
    _b2 = _l.open_batch(str(out), config=_drv.lc.batch_config(
        driver="run10_ch2.py", chapters=[2], n_runs=1, chapter_dir=str(chd),
        self_path=str(_ROOT / ".tmp" / "run10_ch2.py")), resume=_bid)
    assert _b2["ok"] is False and "OTHER_BATCH" in _b2["reason"], _b2


def test_row_validation_checks_identity_slot_and_status_type():
    """逐行核验的三项：批次身份 / 槽位 / 状态类型（`started` 必须是布尔）."""
    _l = _lc()
    if _l is None:
        return
    _scope = _l.batch_scope({"n_runs": 3, "chapters": [1]})
    _ok = {"run": 1, "chapter": 1, "started": True, "batch_id": "B1",
           "run_id": "R1"}
    assert _l._row_problem(_ok, "B1", _scope) == ""
    assert "batch_id" in _l._row_problem({"run": 1, "started": True, "batch_id": "B2", "run_id": "R2"},
                                         "B1", _scope)
    assert "started" in _l._row_problem({"run": 1, "started": "yes", "batch_id": "B1", "run_id": "R1"},
                                        "B1", _scope)
    assert "槽位" in _l._row_problem({"started": True, "batch_id": "B1"}, "B1", _scope)
    assert "run" in _l._row_problem({"run": "1", "started": True, "batch_id": "B1", "run_id": "R1"},
                                    "B1", _scope)
    assert "对象" in _l._row_problem(["x"], "B1", _scope)
    _cfg = {"n_runs": 3, "chapters": [1]}          # 单章 ⇒ 槽位键是 run
    _rows, _bad = _l._validate_rows([_ok, {"run": 2, "started": True, "batch_id": "B2"}],
                                    "B1", _cfg)
    assert _rows == [_ok] and len(_bad) == 1, (_rows, _bad)


def test_a_batch_without_matching_rows_still_runs_every_slot(tmp_path, monkeypatch):
    """对照组：**开批后什么都没跑过**（汇总与台账都不在）时，续跑照常跑完所有槽位.

    第 22 轮起"只删汇总"不再属于这一类：尝试台账有执行记录却没有验收表属于**批次存储不
    一致**，要显式停止（见 `test_run_lifecycle_r22.py`），守卫仍不是一票否决。
    """
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r16b", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 2)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0 and len(calls) == 2, calls
    _bdir = sorted(out.glob("batch_*"))[0]
    (_bdir / "summary.json").unlink()
    (_bdir / _l.ATTEMPTS_NAME).unlink()
    assert _drv.main(["--resume", _bdir.name[len("batch_"):]]) == 0
    assert len(calls) == 4, "summary 缺失后没有重跑：%d" % len(calls)
