# -*- coding: utf-8 -*-
"""第二十五轮复核：两条不变量收口 —— **每次执行独立且可核验；全部历史先验收，再生成最新投影**.

复核原始反例（均离线复现）：
  1. **拒收记录也必须有唯一执行身份**：复制一条**拒收**台账后，实际调用 2 次仍报"尝试 3 次"，
     续跑返回 0（第 24 轮把唯一性放宽到只查 `accepted=True` 的那条口子）。
  2. **完整历史仍没有完整校验**：①把**已被替代**的已验收记录改成第 99 章，续跑仍接受；
     ②汇总里两条**相同**的合法历史行被静默压成一行；③自洽的 `slot=[1]` 能通过读取，
     随后按槽位建索引时抛 `TypeError`。共同根因是**先分类、投影，再只校验最新结果**。
  3. **执行归档仍会覆盖**：三个驱动连续尝试同槽位时，诊断归档与 `prev/` 仍复用文件名，
     第三次之后第一次的完整诊断已找不到（日志本身已独立）。
  4. 备份"相同内容复用"只在同一秒内成立（名字里含时间）。

处置：台账里 **`run_id` = 执行前固定的执行身份（全表唯一）**，结果**声称**的身份另记
`claimed_run_id`（身份冲突是拒收原因，不该让执行记录失去唯一性）；恢复按
**原汇总主键唯一性 → 全部已验收历史的类型与作业关系 → 最新投影** 三步走；
**整个执行归档目录** 按 `run_id` 隔离；备份名只由**内容**决定。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

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
    def __init__(self, lc, drv, out):
        self.lc, self.drv, self.out = lc, drv, out
        self.calls = []

    def bdir(self) -> Path:
        return sorted(self.out.glob("batch_*"))[0]

    def bid(self) -> str:
        return self.bdir().name[len("batch_"):]

    def rows(self) -> list:
        _p = self.bdir() / "summary.json"
        return json.loads(_p.read_text(encoding="utf-8")) if _p.exists() else []

    def lpath(self) -> str:
        return self.lc.attempts_path(str(self.bdir()))

    def ledger(self) -> list:
        return self.lc.read_attempts(self.lpath())["attempts"]

    def write_ledger(self, recs) -> None:
        io.open(self.lpath(), "w", encoding="utf-8").write(
            "".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in recs))


def _drv(monkeypatch, tmp_path, name, mod_name, n_runs, out_name="out"):
    _l = _lc()
    _d = _load(mod_name, name)
    out = tmp_path / out_name
    if hasattr(_d, "N_RUNS"):
        _d.N_RUNS = n_runs
    if hasattr(_d, "OUT"):
        _d.OUT = str(out)
    if hasattr(_d, "CH"):
        _d.CH = str(_mk_chapter(tmp_path))
    if hasattr(_d, "OUT_ROOT"):
        _d.OUT_ROOT = str(out)
    if hasattr(_d, "BASE"):
        base = tmp_path / "base"
        for _c in (1, 2):
            _mk_chapter(base, _c)
        _d.BASE = str(base)
    _s = _Drv(_l, _d, out)
    monkeypatch.setattr(_l.subprocess, "call", lambda *a, **kw: (_s.calls.append(1), 0)[1])
    return _s


class _LaunchBreaker:
    def __init__(self, monkeypatch, lc):
        self._lc, self._real = lc, lc.subprocess.call
        self.on = False
        monkeypatch.setattr(lc.subprocess, "call", self._call)

    def _call(self, *a, **kw):
        if self.on:
            raise FileNotFoundError("注入：子进程拉不起来")
        return self._real(*a, **kw)


def _rejected_record(d: _Drv, victim: str, *, run_id: str = "R-REJ") -> dict:
    """一条**被拒收**的台账记录：执行身份是它自己的，结果**声称**的是别人（被拒收的原因）."""
    return {"batch_id": d.bid(), "key": "run", "slot": 2, "run_id": run_id,
            "seq": 2, "evidence": {"dir": "", "files": {}},
            "claimed_run_id": victim, "chapter": 2, "started": True, "accepted": False,
            "reason": "写回被拒（注入）", "at": "2026-10-01T00:00:00",
            "row": {"run": 2, "chapter": 2, "started": True, "run_id": victim,
                    "exec_run_id": run_id, "batch_id": d.bid()}}


# ---------------------------------------------------------------------------
# ① 执行身份全表唯一（含拒收记录）
# ---------------------------------------------------------------------------
def test_a_copied_rejected_record_is_refused(tmp_path, monkeypatch, capsys):
    """复核反例 1：复制一条**拒收**记录 ⇒ 拒绝（旧：实际调用 2 次仍报"尝试 3 次"、返回 0）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25a", 1, out_name="o1")
    assert _s.drv.main([]) == 0
    _recs = _s.ledger()
    _rej = _rejected_record(_s, _recs[0]["run_id"])
    _s.write_ledger(_recs + [_rej])
    assert _s.drv.main(["--resume", _s.bid()]) == 0        # 一条拒收记录本身是合法的
    _s.write_ledger(_recs + [_rej, _rej])                  # 复制它
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 2, "复制出来的拒收记录被算成了又一次执行"
    assert len(_s.calls) == _n
    _txt = capsys.readouterr().out
    assert "执行身份" in _txt and "重复" in _txt, _txt[-300:]


def test_a_rejected_record_keeps_its_own_execution_identity(tmp_path, monkeypatch):
    """复核给的口径：**身份冲突是拒收原因**，不能让执行记录失去唯一性——台账两列分开记."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25b", 2, out_name="o2")
    _real = _s.lc.settle_round
    _st = {"n": 0}

    def _sabotage(rows, row, **kw):
        _st["n"] += 1
        if _st["n"] == 2:                                  # 第二轮：结果冒充第一轮的身份
            _bad = dict(row)
            _bad["run_id"] = rows[0]["run_id"]
            return _real(rows, _bad, **kw)
        return _real(rows, row, **kw)

    monkeypatch.setattr(_s.lc, "settle_round", _sabotage)
    assert _s.drv.main([]) == 2 and len(_s.calls) == 2
    _recs = _s.ledger()
    assert [a["accepted"] for a in _recs] == [True, False], _recs
    _rej = _recs[1]
    assert _rej["run_id"] not in [a["run_id"] for a in _recs[:1]], _rej
    assert _rej["claimed_run_id"] == _recs[0]["run_id"], _rej   # 声称的是别人的身份
    _c = _s.lc.batch_counts(_s.rows(), attempts=_recs)
    assert _c["attempts"] == 2 and _c["rejected"] == 1 and _c["samples"] == 1, _c


# ---------------------------------------------------------------------------
# ② 全部历史先验收，再生成最新投影
# ---------------------------------------------------------------------------
def test_a_superseded_history_record_is_validated_too(tmp_path, monkeypatch, capsys):
    """复核反例 2①：把**已被替代**的记录改成越界章号 ⇒ 拒绝（旧：只校验投影后的那一条）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25c", 1, out_name="o3")
    _launch = _LaunchBreaker(monkeypatch, _s.lc)
    _launch.on = True
    assert _s.drv.main([]) == 2
    _launch.on = False
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    _recs = _s.ledger()
    assert len(_recs) == 2, _recs
    _recs[0]["chapter"] = 99                               # 改的是**旧的**那条（两边一致）
    _recs[0]["row"]["chapter"] = 99
    _s.write_ledger(_recs)
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 2, "被替代的历史记录没有参与校验"
    assert len(_s.calls) == _n
    assert "历史记录" in capsys.readouterr().out


def test_a_malformed_slot_is_refused_at_read_time(tmp_path, monkeypatch):
    """复核反例 2③：自洽的 `slot=[1]` ⇒ 读台账时就拒绝（旧：通过读取，随后抛 `TypeError`）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25d", 1, out_name="o4")
    assert _s.drv.main([]) == 0
    _recs = _s.ledger()
    _recs[0]["slot"] = [1]
    _recs[0]["row"]["run"] = [1]
    _s.write_ledger(_recs)
    _lr = _s.lc.read_attempts(_s.lpath())                  # 不许抛异常
    assert _lr["ok"] is False and "槽位类型非法" in _lr["problem"], _lr
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 2
    assert len(_s.calls) == _n


def test_a_table_with_duplicate_primary_keys_is_refused(tmp_path, monkeypatch, capsys):
    """复核反例 2②：汇总里两条**相同**的合法历史行 ⇒ 拒绝且**不改动原文件**（旧：静默压成一行）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25e", 1, out_name="o5")
    assert _s.drv.main([]) == 0
    _p = _s.bdir() / "summary.json"
    _raw = json.dumps([_s.rows()[0], _s.rows()[0]], ensure_ascii=False)
    _p.write_text(_raw, encoding="utf-8")
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 2, "重复主键的行被静默压成了一行"
    assert len(_s.calls) == _n
    assert _p.read_text(encoding="utf-8") == _raw, "被拒的续跑改动了验收表"
    assert "重复行" in capsys.readouterr().out


def test_a_valid_superseded_row_is_still_accepted(tmp_path, monkeypatch):
    """对照组：合法的历史行（启动失败 → 续跑成功，且都合法）⇒ 照常恢复，不误伤."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25f", 1, out_name="o6")
    _launch = _LaunchBreaker(monkeypatch, _s.lc)
    _launch.on = True
    assert _s.drv.main([]) == 2
    _launch.on = False
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 0, "合法的历史记录被误拒"
    assert len(_s.calls) == _n
    _st = _s.lc.load_batch(str(_s.bdir()), batch_id=_s.bid(), key="run")
    assert _st["ok"] and _st["pending"] == [] and len(_st["superseded"]) == 1, _st


# ---------------------------------------------------------------------------
# ③ 执行归档按 run_id 隔离
# ---------------------------------------------------------------------------
def test_each_execution_gets_its_own_archive_directory(tmp_path, monkeypatch):
    """复核反例 3：同一槽位连续三次尝试 ⇒ **三个执行归档目录**，第一次的内容仍在."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25g", 1, out_name="o7")
    _launch = _LaunchBreaker(monkeypatch, _s.lc)
    _launch.on = True
    assert _s.drv.main([]) == 2
    assert _s.drv.main(["--resume", _s.bid()]) == 2          # 第二次仍然失败
    _launch.on = False
    assert _s.drv.main(["--resume", _s.bid()]) == 0         # 第三次成功
    _dirs = sorted((_s.bdir() / "run_01").glob("exec-*"))
    assert len(_dirs) == 3, [x.name for x in _dirs]
    assert all((x / "stdout.log").exists() for x in _dirs), "执行归档里没有日志"
    assert len({x.name for x in _dirs}) == 3, "归档目录没有按执行身份区分"
    # 第一次执行的归档内容必须还在（旧实现按槽位目录复用文件名 ⇒ 被后来的覆盖）
    _first = _dirs[0] / "stdout.log"
    _first.write_bytes(b"FIRST_EXECUTION")
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    assert _first.read_bytes() == b"FIRST_EXECUTION", "旧执行的归档被覆盖了"


# ---------------------------------------------------------------------------
# ④ 备份按内容去重（跨秒也成立）
# ---------------------------------------------------------------------------
def test_the_same_corruption_is_backed_up_once_across_seconds(tmp_path, monkeypatch):
    """复核反例 4：**同一份损坏内容**跨秒出现也只留一份（旧：名字含时间 ⇒ 两份）."""
    _l = _lc()
    if _l is None:
        pytest.skip("harness 不在")
    _d = tmp_path / "b"
    _d.mkdir()
    _p = Path(_l.summary_path(str(_d)))
    _ticks = iter(["20260101T000001", "20260101T000002", "20260101T000003"])
    monkeypatch.setattr(_l.time, "strftime", lambda *a, **k: next(_ticks, "…T000009"))
    _names = []
    for _ in range(2):
        _p.write_text("broken", encoding="utf-8")
        _names.append(_l._backup_corrupt_summary(str(_d)))
    assert _names[0] == _names[1], _names
    assert len(list(_d.glob("summary.corrupt-*.json"))) == 1


def test_different_corruptions_are_backed_up_separately(tmp_path, monkeypatch):
    """对照组：内容不同 ⇒ 两份都在（跨秒、同一秒都成立）."""
    _l = _lc()
    if _l is None:
        pytest.skip("harness 不在")
    _d = tmp_path / "b"
    _d.mkdir()
    _p = Path(_l.summary_path(str(_d)))
    for _tag in ("first", "second"):
        _p.write_text(_tag, encoding="utf-8")
        _l._backup_corrupt_summary(str(_d))
    _kept = sorted(x.read_text(encoding="utf-8")
                   for x in _d.glob("summary.corrupt-*.json"))
    assert _kept == ["first", "second"], _kept


def test_a_clean_batch_and_its_resume_agree(tmp_path, monkeypatch, capsys):
    """对照组：干净批次 + 续跑 0 退出、0 次新执行，且**尝试数 = 实际执行数**."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d25h", 3, out_name="o8")
    assert _s.drv.main([]) == 0
    assert len(_s.calls) == 3
    _st = _s.lc.load_batch(str(_s.bdir()), batch_id=_s.bid(), key="run")
    _c = _s.lc.batch_counts(_st["rows"], attempts=_st["attempts"])
    assert _c["attempts"] == len(_s.calls) == 3, _c
    assert _c["samples"] == 3 and _c["rejected"] == 0 and _c["superseded"] == 0, _c
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    assert len(_s.calls) == _n
    assert "本次实跑 0 次" in capsys.readouterr().out
