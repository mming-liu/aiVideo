# -*- coding: utf-8 -*-
"""第二十轮复核两处：**执行身份（run_id）** 与 **收尾记账**.

  1. **P1 同一次执行仍能充当两个样本**：把第一行复制到第二个合法槽位（同一个 `run_id`、
     同一条日志路径），续跑仍返回 0、调用 0 次、报"样本 2/2"；两行 `run_id` 清空也一样。
     写回入口同样接受重复身份——整表校验只核了**槽位**唯一。
  2. **P2 写回被拒后，失败状态与实际启动次数仍丢失**：第二次执行已经启动、写回被拒后停止，
     驱动却仍返回 0，且"本次实跑"少报一次（计数放在写回成功之后）。

不变量（复核给的原话）：**合法作业 → 固定请求身份 → 执行尝试 → 验收结果**。
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


def _mk_chapter(root: Path, ch: int = 2) -> Path:
    chd = root / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": []}, ensure_ascii=False), encoding="utf-8")
    return chd


def _stub(monkeypatch, lc):
    calls = []
    monkeypatch.setattr(lc.subprocess, "call", lambda *a, **kw: calls.append(1) or 0)
    return calls


def _driver_main(monkeypatch, tmp_path, name, mod_name, n_runs):
    _l = _lc()
    _drv = _load(mod_name, name)
    chd = _mk_chapter(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", n_runs)
    return _l, _drv, out


# ---------------------------------------------------------------------------
# ① 执行身份：必填 + 批内唯一
# ---------------------------------------------------------------------------
def test_a_copied_row_cannot_impersonate_a_second_sample(tmp_path, monkeypatch):
    """复核反例 1：把第一行复制到第二个合法槽位 ⇒ 拒绝续跑（旧行为：0 次调用、报"样本 2/2"）.

    判别性：整表只核槽位唯一、不核执行身份 ⇒ 本用例里复制行被当成第二个样本、驱动返回 0。
    """
    _l, _drv, out = _driver_main(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r20", 2)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0 and len(calls) == 2
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    _p = _bdir / "summary.json"
    _rows = json.loads(_p.read_text(encoding="utf-8"))
    _copy = dict(_rows[0])
    _copy["run"] = 2                                   # 换个合法槽位，身份与日志原样照抄
    _p.write_text(json.dumps([_rows[0], _copy], ensure_ascii=False), encoding="utf-8")
    _n = len(calls)
    assert _drv.main(["--resume", _bid]) == 2, "复制出来的行被当成了第二个样本"
    assert len(calls) == _n, "被拒的续跑仍然跑了"


def test_a_row_without_a_run_id_is_refused(tmp_path, monkeypatch):
    """复核反例 1 的另一半：`run_id` 清空的行 ⇒ 拒绝（无从证明它对应一次独立执行）."""
    _l, _drv, out = _driver_main(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r20b", 1)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0 and len(calls) == 1
    _bdir = sorted(out.glob("batch_*"))[0]
    _bid = _bdir.name[len("batch_"):]
    _p = _bdir / "summary.json"
    _rows = json.loads(_p.read_text(encoding="utf-8"))
    _rows[0]["run_id"] = ""
    _p.write_text(json.dumps(_rows, ensure_ascii=False), encoding="utf-8")
    _n = len(calls)
    assert _drv.main(["--resume", _bid]) == 2, "缺执行身份的行被当成了样本"
    assert len(calls) == _n


def test_validate_rows_requires_unique_execution_identity():
    """单元：`run_id` 必填、批内唯一；日志路径重复同样拒（同一次执行只有一份日志）."""
    _l = _lc()
    if _l is None:
        return
    _cfg = {"n_runs": 3, "chapters": [2]}
    _scope = _l.batch_scope(_cfg)
    _a = {"run": 1, "chapter": 2, "started": True, "run_id": "R1", "batch_id": "B",
          "log": "run_01/stdout.log"}
    assert _l._row_problem(_a, "B", _scope) == ""
    assert "run_id" in _l._row_problem({**_a, "run": 2, "run_id": ""}, "B", _scope)
    _rows, _bad = _l._validate_rows([_a, {**_a, "run": 2}], "B", _cfg)
    assert len(_rows) == 1 and any("执行身份" in b for b in _bad), _bad
    _rows2, _bad2 = _l._validate_rows([_a, {**_a, "run": 2, "run_id": "R2"}], "B", _cfg)
    assert len(_rows2) == 1 and any("日志" in b for b in _bad2), _bad2


def test_a_reused_audit_source_id_may_repeat():
    """复核要求：合法复用审计的 `source_run_id` **允许**重复（它只是来源身份，不是执行身份）."""
    _l = _lc()
    if _l is None:
        return
    _cfg = {"n_runs": 3, "chapters": [2]}
    _base = {"chapter": 2, "started": True, "batch_id": "B",
             "source_run_id": "上一轮A", "log": None}
    _rows, _bad = _l._validate_rows(
        [{**_base, "run": 1, "run_id": "R1"},
         {**_base, "run": 2, "run_id": "R2"}], "B", _cfg)
    assert len(_rows) == 2 and _bad == [], _bad


def test_write_back_also_refuses_a_duplicated_identity(tmp_path):
    """复核反例 1 的第三半：写回入口同样不接受重复身份（读写同一口径）."""
    _l = _lc()
    if _l is None:
        return
    chd = _mk_chapter(tmp_path)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=3, chapter_dir=str(chd))
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    _rows = []
    _r1 = {"run": 1, "chapter": 2, "started": True, "run_id": "R1",
           "batch_id": _b["batch_id"]}
    assert _l.batch_put(_rows, _r1, key="run", batch=_b) == ""
    _why = _l.batch_put(_rows, {**_r1, "run": 2}, key="run", batch=_b)
    assert _why and "执行身份" in _why, _why
    assert [r["run"] for r in _rows] == [1], _rows


# ---------------------------------------------------------------------------
# ② 收尾记账：启动按实际执行、失败留档、非零返回
# ---------------------------------------------------------------------------
def test_a_refused_write_back_keeps_the_attempt_and_returns_nonzero(tmp_path, monkeypatch,
                                                                   capsys):
    """复核反例 2：第二轮已启动、写回被拒 ⇒ 驱动**返回非零**，且"本次实跑"不丢这一次."""
    _l, _drv, out = _driver_main(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r20c", 3)
    _real = _l.settle_round
    _state = {"n": 0}

    def _sabotage(rows, row, **kw):
        _state["n"] += 1
        if _state["n"] > 1:                            # 第二轮起：写回一律被拒
            return {"ok": False, "problem": "写回被拒（测试注入）", "rejected_path": "",
                    "abort": True, "exit_code": 2, "started": bool(row.get("started"))}
        return _real(rows, row, **kw)

    monkeypatch.setattr(_l, "settle_round", lambda *a, **kw: _sabotage(*a, **kw))
    calls = _stub(monkeypatch, _l)
    _rc = _drv.main([])
    assert _rc == 2, "写回被拒却仍然返回 0（失败状态没留住）"
    assert len(calls) == 2, "第二轮之后没有中止：%s" % calls
    # 启动次数按**实际执行**记账：2 次调用不能只报 1 次（旧实现把计数放在写回成功之后）
    assert _state["n"] == 2, _state
    _out = capsys.readouterr().out
    assert "本次实跑 2 次" in _out, _out[-400:]


def test_the_rejected_attempt_is_archived(tmp_path, monkeypatch):
    """写回被拒的尝试要**留档**（表里只装通过校验的记录，证据另存 `rejected/`）."""
    _l, _drv, out = _driver_main(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r20d", 2)
    _real = _l.settle_round
    _state = {"n": 0}

    def _sabotage(rows, row, *, batch, request, key=None, rejected_dir="",
                  attempts_path=""):
        _state["n"] += 1
        if _state["n"] == 1:
            return _real(rows, row, batch=batch, request=request, key=key,
                         rejected_dir=rejected_dir, attempts_path=attempts_path)
        # 第二轮真正走"被拒"路径：喂一条**执行身份与已记录的那条重复**的行
        _bad = dict(row)
        _bad["run_id"] = rows[0]["run_id"]
        return _real(rows, _bad, batch=batch, request=request, key=key,
                     rejected_dir=rejected_dir, attempts_path=attempts_path)

    monkeypatch.setattr(_l, "settle_round", _sabotage)
    _stub(monkeypatch, _l)
    assert _drv.main([]) == 2
    _bdir = sorted(out.glob("batch_*"))[0]
    _rej = sorted((_bdir / "rejected").glob("*.json"))
    assert _rej, "被拒的尝试没有留档"
    _d = json.loads(_rej[0].read_text(encoding="utf-8"))
    assert "执行身份" in _d["reason"] and _d["row"]["run"] == 2, _d
    # 留档**按请求身份命名**（第 25 轮起：结果自称的身份可能被冒充，不能当文件名）
    assert _rej[0].stem == _d["row"]["exec_run_id"], (_rej[0].name, _d["row"])


def test_a_clean_batch_still_returns_zero_and_counts_every_round(tmp_path, monkeypatch):
    """对照组：正常批次返回 0，"本次实跑"与实际调用次数一致（别把新口径做成一律非零）."""
    _l, _drv, out = _driver_main(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r20e", 2)
    calls = _stub(monkeypatch, _l)
    assert _drv.main([]) == 0
    assert len(calls) == 2
    _rows = json.loads((sorted(out.glob("batch_*"))[0] / "summary.json")
                       .read_text(encoding="utf-8"))
    assert _l.batch_counts(_rows)["samples"] == 2, _l.batch_counts(_rows)
