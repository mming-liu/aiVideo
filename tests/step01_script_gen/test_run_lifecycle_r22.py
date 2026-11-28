# -*- coding: utf-8 -*-
"""第二十二轮复核两处（P1/P2）：**批次存储协议**（读 / 写 / 恢复 / 计数同处一处）.

复核原始反例（均离线复现，先确认再动手）：
  1. **P1 台账写入失败被忽略，仍报成功**：注入 `PermissionError`（真实故障路径：
     `io.open(..., "a")` 抛 `OSError` ⇒ `_append_attempt` 返回失败），三个驱动**都执行 2 次、
     返回 0**，汇总却是"样本 2/2、尝试 0 次"，台账失败一个字都不报——收尾函数忽略了
     `_append_attempt()` 的返回值。
  2. **P2 续跑没有核验台账与验收表的一致性**：
     · 混入异批台账 ⇒ 本批只执行 1 次却报"尝试 3 次"；
     · 台账断行 ⇒ 静默跳过，仍返回 0；
     · 损坏 `summary.json` ⇒ 被当成空表，重跑已完成槽位并**覆盖旧汇总**；
     · 台账读不动 ⇒ 当空表（报"尝试 0 次"）。
     根因是"读台账"与"恢复批次"把**缺失、读取失败、格式损坏**混作空记录，且没有跨文件对账。

处置（**批次存储协议**，一个模块负责读/写/恢复/计数）：
  · 写前日志顺序：算写回候选（不动表）→ 落尝试台账（写+fsync）→ **落盘成功才确认验收**；
  · 台账记录自带**批次身份**与**执行身份**；
  · 严格读：文件不在 / 读不动 / 断行 / 异批**分开**，后三类显式停止；
  · 续跑对账：验收表每一条都要有台账里 `accepted=True` 且执行身份相符的背书，否则拒绝；
    反向（台账已验收但未落表）是合法崩溃窗口，**报出来**且该槽位重试；
  · 汇总损坏≠空批：读不动/不是列表 ⇒ 拒绝续跑，**不动文件**。
"""
from __future__ import annotations

import pytest

import importlib.util
import json
import os
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
    chd = Path(root) / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": []}, ensure_ascii=False), encoding="utf-8")
    return chd


class _Drv:
    """把驱动指到 tmp（**绝不许落到真实路径**——三个驱动的路径常量名不一样）."""

    def __init__(self, lc, drv, chd, out):
        self.lc, self.drv, self.chd, self.out = lc, drv, chd, out
        self.calls = []

    def bdir(self) -> Path:
        return sorted(self.out.glob("batch_*"))[0]

    def bid(self) -> str:
        return self.bdir().name[len("batch_"):]

    def rows(self) -> list:
        """验收表（**不在就是空**——提交前的批次本来就没有这张表）."""
        _p = self.bdir() / "summary.json"
        return json.loads(_p.read_text(encoding="utf-8")) if _p.exists() else []

    def ledger(self) -> list:
        return self.lc.read_attempts(
            str(self.lc.attempts_path(str(self.bdir()))))["attempts"]


def _drv(monkeypatch, tmp_path, name, mod_name, n_runs, out_name="out", chapters=(1, 2)):
    _l = _lc()
    _d = _load(mod_name, name)
    chd = _mk_chapter(tmp_path)
    out = tmp_path / out_name
    if hasattr(_d, "N_RUNS"):
        _d.N_RUNS = n_runs
    if hasattr(_d, "OUT"):
        _d.OUT = str(out)
    if hasattr(_d, "OUT_ROOT"):
        _d.OUT_ROOT = str(out)
    if hasattr(_d, "CH"):
        _d.CH = str(chd)
    if hasattr(_d, "BASE"):                        # run_verify_2.py：两章都造在 tmp 里
        base = tmp_path / "base"
        for _c in chapters:
            _mk_chapter(base, _c)
        _d.BASE = str(base)
    for _n in ("OUT", "OUT_ROOT"):
        _p = getattr(_d, _n, "")
        if _p:
            assert str(tmp_path) in str(_p), "%s 指到真实路径了：%s" % (_n, _p)
    _stub = _Drv(_l, _d, chd, out)
    monkeypatch.setattr(_l.subprocess, "call",
                        lambda *a, **kw: (_stub.calls.append(1), 0)[1])
    return _stub


def _block_ledger(monkeypatch, lc, blocked: Path):
    """**真实故障注入**：把台账路径指到一个"父路径是文件"的位置 ⇒ `io.open` 抛 `OSError`."""
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("占位文件（不是目录）", encoding="utf-8")
    monkeypatch.setattr(lc, "attempts_path",
                        lambda d: os.path.join(str(d), blocked.name, lc.ATTEMPTS_NAME))


# ---------------------------------------------------------------------------
# ① 写前日志顺序：台账没落盘就不算已验收，驱动非零退出
# ---------------------------------------------------------------------------
def test_a_ledger_write_failure_is_not_reported_as_success(tmp_path, monkeypatch, capsys):
    """复核反例 1：台账落盘失败 ⇒ 非零退出、报出台账错误、**本轮不进验收表**（旧：报 0/样本 2/2）."""
    for _name, _mod, _out in (("run10_ch2.py", "_d22a", "o1"),
                              ("run20_ch1.py", "_d22b", "o2")):
        _s = _drv(monkeypatch, tmp_path, _name, _mod, 2, out_name=_out)
        _block_ledger(monkeypatch, _s.lc, _s.out / "blocked")
        assert _s.drv.main([]) == 2, "%s 台账落盘失败却报成功退出" % _name
        assert len(_s.calls) == 1, "台账落盘失败后仍在往下跑：%s" % _s.calls
        _out_txt = capsys.readouterr().out
        assert "台账落盘失败" in _out_txt and "BATCH ABORT" in _out_txt, _out_txt[-400:]
        # **落盘成功才确认验收**：这一轮不能进验收表（第 23 轮起连提交都不做）
        assert _s.rows() == [], _s.rows()
        # 失败尝试仍要留档（按执行身份命名）
        _rej = sorted((_s.bdir() / "rejected").glob("*.json"))
        assert _rej, "台账失败的尝试没有留档"
        assert "台账" in json.loads(_rej[0].read_text(encoding="utf-8"))["reason"]


def test_the_verify_driver_also_stops_on_a_ledger_write_failure(tmp_path, monkeypatch, capsys):
    """同上，两章验证驱动：第一处台账失败即停，第二章不再跑（旧：两章都跑完还返回 0）."""
    _s = _drv(monkeypatch, tmp_path, "run_verify_2.py", "_d22c", 2, out_name="o3")
    _block_ledger(monkeypatch, _s.lc, _s.out / "blocked")
    assert _s.drv.main([]) == 2
    assert len(_s.calls) == 1, _s.calls
    assert "台账落盘失败" in capsys.readouterr().out


def test_a_summary_write_failure_aborts_the_batch(tmp_path, monkeypatch, capsys):
    """验收表落盘失败 ⇒ 驱动非零退出（旧：裸 `json.dump`、不看返回值，照样报"样本 N/N"）.

    第 23 轮起**注入点下沉到原子写本身**（`shared.json_io.atomic_dump_json`，就是"磁盘满"
    的真实故障点），判据也换成"盘上那一份"——见 `test_run_lifecycle_r23.py`。
    """
    import shared.json_io as _jio
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d22h", 2, out_name="o6")

    _real = _jio.atomic_dump_json

    def _boom(path, obj, **kw):
        if str(path).endswith("summary.json"):     # 只拦验收表（batch.json 走同一个原子写）
            raise OSError("注入：磁盘满")
        return _real(path, obj, **kw)
    monkeypatch.setattr(_jio, "atomic_dump_json", _boom)
    assert _s.drv.main([]) == 2
    assert len(_s.calls) == 1, "验收表落盘失败后仍在往下跑：%s" % _s.calls
    assert "验收表提交失败" in capsys.readouterr().out


def test_a_clean_batch_persists_both_ledger_and_table(tmp_path, monkeypatch):
    """对照组：正常批次台账与验收表**逐条对应**（不把新口径做成一律失败）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d22d", 2, out_name="o4")
    assert _s.drv.main([]) == 0
    _rows, _led = _s.rows(), _s.ledger()
    assert len(_rows) == 2 and len(_led) == 2, (_rows, _led)
    assert all(a["accepted"] and a["batch_id"] == _s.bid() for a in _led), _led
    assert [a["run_id"] for a in _led] == [r["run_id"] for r in _rows], (_led, _rows)
    _st = _s.lc.load_batch(str(_s.bdir()), batch_id=_s.bid(), key="run")
    assert _st["ok"] and _st["rows"] == _rows and _st["pending"] == [], _st
    assert _st["superseded"] == [] and _st["rebuilt"] is False, _st
    # 台账每行都留**完整结果**（对账要比整行内容，含执行状态）
    assert all(a.get("row") and a["row"]["run_id"] == a["run_id"] for a in _led), _led


# ---------------------------------------------------------------------------
# ② 续跑：台账身份 / 损坏 / 读不动 / 汇总损坏，一律显式停止
# ---------------------------------------------------------------------------
def _two_batches(monkeypatch, tmp_path):
    _a = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d22e", 1, out_name="fa")
    assert _a.drv.main([]) == 0
    _b = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d22f", 2, out_name="fb")
    assert _b.drv.main([]) == 0
    return _a, _b


def test_a_foreign_ledger_is_refused_on_resume(tmp_path, monkeypatch, capsys):
    """复核反例 2a：异批台账混入 ⇒ 拒绝续跑（旧：拿别批的条数当本批"尝试 3 次"，返回 0）."""
    _a, _b = _two_batches(monkeypatch, tmp_path)
    _dst = _a.bdir() / _a.lc.ATTEMPTS_NAME
    _dst.write_bytes((_b.bdir() / _b.lc.ATTEMPTS_NAME).read_bytes())
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 2, "异批台账被当成了本批读数"
    assert len(_a.calls) == _n, "拒绝续跑之后仍然跑了"
    _txt = capsys.readouterr().out
    assert "别的批次" in _txt and "BATCH REFUSED" in _txt, _txt[-400:]


def test_a_corrupt_ledger_line_is_refused_on_resume(tmp_path, monkeypatch, capsys):
    """复核反例 2b：台账断行 ⇒ 拒绝续跑并报行号（旧：静默跳过，返回 0）."""
    _a, _ = _two_batches(monkeypatch, tmp_path)
    with open(_a.bdir() / _a.lc.ATTEMPTS_NAME, "a", encoding="utf-8") as f:
        f.write("{ 这不是 JSON\n")
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 2
    assert len(_a.calls) == _n
    _txt = capsys.readouterr().out
    assert "不是合法 JSON" in _txt and "第 2 行" in _txt, _txt[-400:]


def test_an_unreadable_ledger_is_refused_on_resume(tmp_path, monkeypatch, capsys):
    """复核反例 2d：台账读不动（`OSError`）⇒ 拒绝续跑（旧：当空表，报"尝试 0 次"还返回 0）."""
    _a, _ = _two_batches(monkeypatch, tmp_path)
    _p = _a.bdir() / _a.lc.ATTEMPTS_NAME
    _p.unlink()
    _p.mkdir()                                     # 同名目录 ⇒ io.open 抛 OSError
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 2
    assert len(_a.calls) == _n
    assert "读不动" in capsys.readouterr().out


def test_a_corrupt_summary_is_rebuilt_from_the_ledger(tmp_path, monkeypatch, capsys):
    """复核反例 2c：汇总损坏 ⇒ **按台账重建**、0 次新执行、损坏文件另存备份.

    （第 22 轮这里是"拒绝续跑且不动文件"；第 23 轮复核定为"表能重建就重建、原文件先备份"
    ——拒绝续跑会让一次启动失败的批次再也续不上。）
    """
    _a, _ = _two_batches(monkeypatch, tmp_path)
    _p = _a.bdir() / "summary.json"
    _bad = '{"chapter": 2, "rc"'                   # 截断的 JSON
    _p.write_text(_bad, encoding="utf-8")
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 0, "损坏的汇总没有按台账重建"
    assert len(_a.calls) == _n, "损坏的汇总导致重跑已完成槽位"
    assert len(_a.rows()) == 1, _a.rows()
    _bak = [x for x in _a.bdir().glob("summary.corrupt-*.json")]
    assert _bak and _bak[0].read_text(encoding="utf-8") == _bad, "损坏的原文件没有备份"
    assert "已按尝试台账重建" in capsys.readouterr().out


def test_a_missing_summary_is_rebuilt_without_rerunning(tmp_path, monkeypatch, capsys):
    """汇总不在**但台账有执行记录** ⇒ 从台账**恢复**（旧：拒绝，或更早的版本当空批重跑）.

    只有"汇总与台账都不在"才是"开批后没跑过"的合法空批（见 r16 的对照组）。
    """
    _a, _ = _two_batches(monkeypatch, tmp_path)
    (_a.bdir() / "summary.json").unlink()
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 0
    assert len(_a.calls) == _n, "验收表缺失却重跑了"
    assert len(_a.rows()) == 1, _a.rows()
    assert "补回 1 条已完成但未落表的结果" in capsys.readouterr().out


def test_a_summary_row_without_ledger_backing_is_refused(tmp_path, monkeypatch, capsys):
    """对账：验收表里的行**必须有台账背书**（台账被删 ⇒ 拒绝续跑；旧：照样返回 0）."""
    _a, _ = _two_batches(monkeypatch, tmp_path)
    (_a.bdir() / _a.lc.ATTEMPTS_NAME).unlink()
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 2
    assert len(_a.calls) == _n
    assert "没有已验收记录" in capsys.readouterr().out


def test_an_accepted_attempt_without_a_row_is_reported_not_dropped(tmp_path, monkeypatch,
                                                                   capsys):
    """反向：台账已验收、验收表还没落盘 ⇒ **补表**（恢复已落盘结果），**不重跑**."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d22g", 2, out_name="o5")
    assert _s.drv.main([]) == 0
    _bdir, _bid = str(_s.bdir()), _s.bid()
    # 造出"台账写成、表还没落盘"：表里只留槽位 1，台账两条都在
    _rows = _s.rows()
    (_s.bdir() / "summary.json").write_text(
        json.dumps(_rows[:1], ensure_ascii=False), encoding="utf-8")
    _st = _s.lc.load_batch(_bdir, batch_id=_bid, key="run")
    assert _st["ok"] is True and _st["pending"] == [_rows[1]["run_id"]], _st
    _c = _s.lc.batch_counts(_rows[:1], attempts=_st["attempts"])
    assert _c["pending"] == 1 and _c["attempts"] == 2 and _c["samples"] == 1, _c
    # 未落表的那个槽位**不算已完成** ⇒ 续跑会重试它
    assert _s.lc.batch_done(_rows[:1], key="run") == {1}, _s.lc.batch_done(_rows[:1])
    # 第 23 轮复核：**恢复已落盘结果，再补验收表** —— 不新增任何执行
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _bid]) == 0
    assert len(_s.calls) == _n, "补表不该重跑任何槽位：%s" % _s.calls
    assert len(_s.rows()) == 2, _s.rows()
    assert "不新增执行" in capsys.readouterr().out


def test_a_clean_batch_resumes_without_new_executions(tmp_path, monkeypatch, capsys):
    """对照组：干净批次续跑 0 次新执行、读数一致、返回 0."""
    _a, _ = _two_batches(monkeypatch, tmp_path)
    _n = len(_a.calls)
    assert _a.drv.main(["--resume", _a.bid()]) == 0
    assert len(_a.calls) == _n, "续跑不该重跑已完成的槽位"
    _txt = capsys.readouterr().out
    assert "本次实跑 0 次" in _txt and "尝试 1 次" in _txt, _txt[-400:]
