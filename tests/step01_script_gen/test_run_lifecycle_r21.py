# -*- coding: utf-8 -*-
"""第二十一轮复核两处（P2）：**尝试台账**与**共用收尾的失败状态**.

  1. **续跑会覆盖首次拒收证据，尝试计数也漏算**：同槽位连续两次拒收（两个不同 `run_id`），
     只剩一个 `rejected/round_2.json`（按**槽位**命名 ⇒ 后一次覆盖前一次）；首次实际启动
     2 次，汇总仍报"尝试 1 次"（尝试数只从**验收表**算）。
  2. **启动失败仍被驱动报成成功退出**：注入 `FileNotFoundError`，三个驱动均返回 0；两个批跑
     打印 `BATCH ABORT` 却不改退出码，验证驱动还继续跑下一章（共用收尾只判"写回是否通过"）。

处置：每次尝试按固定 `run_id` **独立留档**（`rejected/<run_id>.json`）+ 追加式尝试台账
（`attempts.jsonl`）；尝试数从台账算、样本数从验收表算；**中止与退出码由共用收尾统一决定**，
覆盖"未启动"与"写回被拒"。
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


def _driver(monkeypatch, tmp_path, name, mod_name, n_runs, out_name="out"):
    _l = _lc()
    _drv = _load(mod_name, name)
    chd = _mk_chapter(tmp_path)
    out = tmp_path / out_name
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", n_runs)
    return _l, _drv, out


def _ok_calls(monkeypatch, lc):
    calls = []
    monkeypatch.setattr(lc.subprocess, "call", lambda *a, **kw: calls.append(1) or 0)
    return calls


# ---------------------------------------------------------------------------
# ① 每次尝试按执行身份独立留档 + 尝试数从台账算
# ---------------------------------------------------------------------------
def test_two_consecutive_rejections_are_both_archived(tmp_path):
    """复核反例 1a：同槽位两次拒收（两个 `run_id`）⇒ 两份留档都在（旧行为：后一次覆盖）."""
    _l = _lc()
    if _l is None:
        return
    chd = _mk_chapter(tmp_path)
    _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=3, chapter_dir=str(chd))
    _b = _l.open_batch(str(tmp_path / "b"), config=_cfg)
    _rej = tmp_path / "rejected"
    _rows = []
    # 同一槽位（越界的 run=99）连续两次被拒，两次的 `run_id` 不同
    _base = {"run": 99, "chapter": 2, "started": True, "batch_id": _b["batch_id"]}
    # 第 26 轮起：身份由**请求对象**给，结果自称的身份只用来核对
    _out = [_l.settle_round(_rows, {**_base, "run_id": "R-a"}, batch=_b,
                            request={"run_id": "R-a"}, rejected_dir=str(_rej)),
            _l.settle_round(_rows, {**_base, "run_id": "R-b"}, batch=_b,
                            request={"run_id": "R-b"}, rejected_dir=str(_rej))]
    assert [r["ok"] for r in _out] == [False, False], _out
    _files = sorted(p.name for p in _rej.glob("*.json"))
    assert _files == ["R-a.json", "R-b.json"], _files        # 按执行身份命名，互不覆盖
    assert _rows == []


def test_the_attempt_ledger_counts_every_execution(tmp_path, monkeypatch, capsys):
    """复核反例 1b：实际启动 2 次 + 一次被拒 ⇒ 尝试台账 3 条，汇总报"尝试 3 次（拒收 1）"."""
    _l, _drv, out = _driver(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r21", 3)
    _real = _l.settle_round
    _state = {"n": 0}

    def _sabotage(rows, row, **kw):
        _state["n"] += 1
        if _state["n"] == 3:                       # 第三轮：喂一条重复身份的行 ⇒ 写回被拒
            _bad = dict(row)
            _bad["run_id"] = rows[0]["run_id"]
            return _real(rows, _bad, **kw)
        return _real(rows, row, **kw)

    monkeypatch.setattr(_l, "settle_round", _sabotage)
    calls = _ok_calls(monkeypatch, _l)
    assert _drv.main([]) == 2
    assert len(calls) == 3, calls                  # 三轮都真的启动了
    _bdir = sorted(out.glob("batch_*"))[0]
    _led = _l.read_attempts(str(_bdir / _l.ATTEMPTS_NAME))["attempts"]
    assert len(_led) == 3, _led
    assert [a["accepted"] for a in _led] == [True, True, False], _led
    assert _led[2]["run_id"] == _led[2]["run_id"] and _led[2]["slot"] == 3, _led[2]
    _txt = capsys.readouterr().out
    assert "尝试 3 次（其中拒收 1）" in _txt, _txt[-300:]
    assert "批内样本 2/3" in _txt, _txt[-300:]


def test_the_ledger_records_a_not_started_attempt(tmp_path, monkeypatch):
    """未启动的尝试也要进台账（它是**一次尝试**，只是没启动；样本另算）."""
    _l, _drv, out = _driver(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r21b", 1,
                            out_name="out2")
    _real_clear = _l.clear_prev_diagnostics
    _state = {"n": 0}

    def _clear(chapter_dir, prev_dir):
        _state["n"] += 1
        if _state["n"] == 1:
            return ["陈旧诊断 x 清场失败（注入）"]
        return _real_clear(chapter_dir, prev_dir)

    monkeypatch.setattr(_l, "clear_prev_diagnostics", _clear)
    _ok_calls(monkeypatch, _l)
    assert _drv.main([]) == 2
    _bdir = sorted(out.glob("batch_*"))[0]
    _led = _l.read_attempts(str(_bdir / _l.ATTEMPTS_NAME))["attempts"]
    # 未启动的尝试进台账：`started=False`；它的记录仍被表接收（好让续跑重试它），
    # 但**不算样本**（样本按 `started=True` 计）
    assert len(_led) == 1 and _led[0]["started"] is False, _led
    _rows = json.loads((_bdir / "summary.json").read_text(encoding="utf-8"))
    _c = _l.batch_counts(_rows, attempts=_led)
    assert _c["samples"] == 0 and _c["attempts"] == 1 and _c["not_started"] == 1, _c


# ---------------------------------------------------------------------------
# ② 启动失败：三个驱动都要非零退出、验证驱动不再继续后续章节
# ---------------------------------------------------------------------------
def _raise_fnf(monkeypatch, lc):
    _n = []

    def _boom(*a, **kw):
        _n.append(1)
        raise FileNotFoundError("注入：子进程拉不起来")

    monkeypatch.setattr(lc.subprocess, "call", _boom)
    return _n


def test_a_launch_failure_makes_batch_drivers_exit_nonzero(tmp_path, monkeypatch, capsys):
    """复核反例 2：`FileNotFoundError` ⇒ 批跑驱动**返回非零**（旧行为：打印 ABORT 却返回 0）."""
    for _name, _mod, _out in (("run10_ch2.py", "_drv10_r21c", "o1"),
                              ("run20_ch1.py", "_drv20_r21", "o2")):
        _l, _drv, out = _driver(monkeypatch, tmp_path, _name, _mod, 2, out_name=_out)
        _n = _raise_fnf(monkeypatch, _l)
        assert _drv.main([]) == 2, "%s 启动失败却报成功退出" % _name
        assert len(_n) == 1, "启动失败后仍然继续尝试：%s" % _n
        _txt = capsys.readouterr().out
        assert "BATCH ABORT" in _txt and "启动失败" in _txt, _txt[-300:]
        _rows = json.loads((sorted(out.glob("batch_*"))[0] / "summary.json")
                           .read_text(encoding="utf-8"))
        assert _rows[0]["started"] is False, _rows[0]


def test_a_launch_failure_stops_the_verify_driver(tmp_path, monkeypatch, capsys):
    """复核反例 2 的另一半：两章验证驱动在第一处启动失败后**不再跑第二章**，且非零退出."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv_verify_r21", "run_verify_2.py")
    _base = tmp_path / "part"
    _mk_chapter(_base, 1)
    _mk_chapter(_base, 2)
    monkeypatch.setattr(_drv, "BASE", str(_base))
    monkeypatch.setattr(_drv, "OUT_ROOT", str(tmp_path / "verify"))
    _n = _raise_fnf(monkeypatch, _l)
    assert _drv.main([]) == 2, "验证驱动启动失败却报成功退出"
    assert len(_n) == 1, "第一处启动失败后仍去跑后续章节：%s" % _n
    _txt = capsys.readouterr().out
    assert "BATCH ABORT" in _txt, _txt[-300:]


def test_a_clean_batch_exits_zero_and_the_ledger_matches(tmp_path, monkeypatch):
    """对照组：正常批次返回 0，台账条数 = 实际执行次数（不把新口径做成一律非零）."""
    _l, _drv, out = _driver(monkeypatch, tmp_path, "run10_ch2.py", "_drv10_r21d", 2,
                            out_name="o3")
    calls = _ok_calls(monkeypatch, _l)
    assert _drv.main([]) == 0
    assert len(calls) == 2
    _bdir = sorted(out.glob("batch_*"))[0]
    _led = _l.read_attempts(str(_bdir / _l.ATTEMPTS_NAME))["attempts"]
    assert len(_led) == 2 and all(a["accepted"] and a["started"] for a in _led), _led
    _rows = json.loads((_bdir / "summary.json").read_text(encoding="utf-8"))
    _c = _l.batch_counts(_rows, attempts=_led)
    assert _c["samples"] == 2 and _c["attempts"] == 2 and _c["rejected"] == 0, _c
