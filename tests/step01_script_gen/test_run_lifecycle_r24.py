# -*- coding: utf-8 -*-
"""第二十四轮复核三组缺口：**台账成为恢复来源后的可信性 / 历史关系 / 证据不可覆盖**.

复核原始反例（均离线复现，先确认再动手）：
  1. **P1 同槽位的崩溃恢复仍被拒绝**："首次启动失败 → 续跑成功 → 台账落盘 → 汇总原子写失败"
     之后，盘上留着的是这个槽位**上一次**那次执行的合法记录；旧实现只与**最新**台账记录比，
     直接判"背书不一致"，三个驱动都无法继续恢复（`rc=[2,2,2]`）。
  2. **P1 恢复源尚未经过完整校验**：
     · 复制一条已有台账记录 ⇒ 续跑返回 0，**实际执行 2 次却统计尝试 3 次**；
     · 台账恢复出的第 99 轮记录被**先写进汇总**，再因越界拒绝续跑（"先写后验"）。
  3. **P2 历史证据仍会被覆盖**：三个驱动重试同槽位复用 `stdout.log`，旧尝试行里的日志路径
     指向**新执行**的内容，旧日志一个字都没留下；同一秒内两次损坏汇总复用同一个备份名，
     第二份损坏内容没保存。

处置（仍由 `run_lifecycle` 统一负责）：**完整台账校验 → 区分合法历史行与被改行 → 恢复最新投影**；
每次执行的日志按 `run_id` 独立保存；损坏备份带**内容摘要**、唯一命名。
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

    def ledger_path(self) -> str:
        return self.lc.attempts_path(str(self.bdir()))

    def ledger(self) -> list:
        return self.lc.read_attempts(self.ledger_path())["attempts"]

    def write_ledger(self, recs) -> None:
        io.open(self.ledger_path(), "w", encoding="utf-8").write(
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
    """让子进程**拉不起来**（`FileNotFoundError`）——日志文件已建、进程没起来."""

    def __init__(self, monkeypatch, lc):
        self._lc, self._real = lc, lc.subprocess.call
        self.on = False
        monkeypatch.setattr(lc.subprocess, "call", self._call)

    def _call(self, *a, **kw):
        if self.on:
            raise FileNotFoundError("注入：子进程拉不起来")
        return self._real(*a, **kw)


class _SummaryBreaker:
    """让**验收表**写不进去（`batch.json` 走同一个原子写，所以只拦 summary.json）."""

    def __init__(self, monkeypatch):
        import shared.json_io as _jio
        self._real = _jio.atomic_dump_json
        self.on = False
        monkeypatch.setattr(_jio, "atomic_dump_json", self._call)

    def _call(self, path, obj, **kw):
        if self.on and str(path).endswith("summary.json"):
            raise OSError("注入：验收表写不进去")
        return self._real(path, obj, **kw)


# ---------------------------------------------------------------------------
# ① 同槽位的崩溃恢复：合法历史行 ⇒ 补成最新，不是篡改
# ---------------------------------------------------------------------------
def test_a_legal_history_row_is_recovered_after_a_failed_commit(tmp_path, monkeypatch,
                                                                capsys):
    """复核反例 1：启动失败 → 续跑成功 → 提交失败 ⇒ 再续跑要**恢复**（旧：判不一致、拒续跑）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24a", 1, out_name="o1")
    _launch = _LaunchBreaker(monkeypatch, _s.lc)
    _brk = _SummaryBreaker(monkeypatch)
    _launch.on = True
    assert _s.drv.main([]) == 2                    # 首次：启动失败
    _launch.on = False
    _brk.on = True
    assert _s.drv.main(["--resume", _s.bid()]) == 2   # 续跑成功，但验收表写不进去
    _brk.on = False
    assert len(_s.ledger()) == 2, _s.ledger()
    assert len(_s.rows()) == 1 and _s.rows()[0]["started"] is False, _s.rows()
    _n = len(_s.calls)
    # 第三次：盘上那一行是**合法历史行**（上一次执行的），要补成最新而不是判篡改
    assert _s.drv.main(["--resume", _s.bid()]) == 0, "同槽位的崩溃恢复被拒了"
    assert len(_s.calls) == _n, "恢复时重跑了"
    _rows = _s.rows()
    assert len(_rows) == 1 and _rows[0]["started"] is True, _rows
    assert _rows[0]["run_id"] == _s.ledger()[1]["run_id"], _rows
    _txt = capsys.readouterr().out
    assert "补回 1 条已完成但未落表的结果" in _txt and "已替代 1" in _txt, _txt[-400:]


def test_a_row_matching_no_ledger_history_is_still_refused(tmp_path, monkeypatch, capsys):
    """同一段代码的另一半：表里那行**对不上任何一条历史**（既非最新也非旧记录）⇒ 仍拒绝."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24b", 1, out_name="o2")
    assert _s.drv.main([]) == 0
    _rows = _s.rows()
    _rows[0]["rc"] = 12345                          # 改一个字段 ⇒ 对不上任何台账记录
    (_s.bdir() / "summary.json").write_text(json.dumps(_rows, ensure_ascii=False),
                                            encoding="utf-8")
    assert _s.drv.main(["--resume", _s.bid()]) == 2, "被改过的行仍被当成合法历史"
    assert "既不是最新结果、也不是任何一条历史记录" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ② 台账自身的可信性 + 先校验再写盘
# ---------------------------------------------------------------------------
def test_a_duplicated_ledger_record_is_refused(tmp_path, monkeypatch, capsys):
    """复核反例 2a：复制一条台账记录 ⇒ 拒绝（旧：实际执行 2 次却报"尝试 3 次"并返回 0）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24c", 2, out_name="o3")
    assert _s.drv.main([]) == 0
    _recs = _s.ledger()
    _s.write_ledger(_recs + [_recs[0]])             # 复制第一行
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 2, "重复的执行身份被当成了两次执行"
    assert len(_s.calls) == _n
    _txt = capsys.readouterr().out
    assert "执行身份" in _txt and "重复" in _txt, _txt[-400:]


def test_a_ledger_record_inconsistent_with_its_result_is_refused(tmp_path, monkeypatch,
                                                                  capsys):
    """复核反例 2a 的另一半：头部与**完整结果**自相矛盾（说没启动、结果说启动过）⇒ 拒绝."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24d", 1, out_name="o4")
    _launch = _LaunchBreaker(monkeypatch, _s.lc)
    _launch.on = True
    assert _s.drv.main([]) == 2
    _launch.on = False
    _recs = _s.ledger()
    _recs[0]["started"] = True                      # 头部改成"启动过"，完整结果仍是 False
    _s.write_ledger(_recs)
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 2, "自相矛盾的台账记录被拿来恢复了"
    assert len(_s.calls) == _n
    assert "执行状态与完整结果不一致" in capsys.readouterr().out


def test_a_slot_mismatch_inside_a_record_is_refused(tmp_path, monkeypatch, capsys):
    """同上：头部槽位与完整结果里的槽位不一致 ⇒ 拒绝（不能拿它恢复）。"""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24e", 1, out_name="o5")
    assert _s.drv.main([]) == 0
    _recs = _s.ledger()
    _recs[0]["slot"] = 2                            # 头部说槽位 2，结果里是 1
    _s.write_ledger(_recs)
    assert _s.drv.main(["--resume", _s.bid()]) == 2
    assert "槽位与完整结果不一致" in capsys.readouterr().out


def test_an_out_of_range_recovered_row_never_reaches_the_disk(tmp_path, monkeypatch,
                                                              capsys):
    """复核反例 2b：恢复出的越界记录 ⇒ **先校验、再拒绝**，一个字节都不落盘（旧：先写后验）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24f", 1, out_name="o6")
    assert _s.drv.main([]) == 0
    _recs = _s.ledger()
    _recs[0]["slot"] = 99                           # 合法作业表里只有 run=1
    _recs[0]["row"]["run"] = 99
    _s.write_ledger(_recs)
    (_s.bdir() / "summary.json").unlink()
    assert _s.drv.main(["--resume", _s.bid()]) == 2
    assert not (_s.bdir() / "summary.json").exists(), "越界的恢复记录被先写进了汇总"
    _txt = capsys.readouterr().out
    assert "合法作业表" in _txt and "拒绝恢复" in _txt, _txt[-400:]


# ---------------------------------------------------------------------------
# ③ 证据不可覆盖：日志按执行身份、备份按内容
# ---------------------------------------------------------------------------
def test_each_execution_keeps_its_own_log(tmp_path, monkeypatch):
    """复核反例 3a：重试同槽位 ⇒ 日志路径**按 `run_id` 各自独立**，旧日志内容留下来."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24g", 1, out_name="o7")
    _launch = _LaunchBreaker(monkeypatch, _s.lc)
    _launch.on = True
    assert _s.drv.main([]) == 2
    _launch.on = False
    _first = _s.rows()[0]
    assert _first["log"], "本轮没有记日志路径"
    Path(_first["log"]).write_bytes(b"FIRST_EXECUTION")
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    _second = _s.rows()[0]
    assert _second["run_id"] != _first["run_id"], "没有产生新的执行身份"
    assert _second["log"] != _first["log"], "重试复用了同一份日志（旧执行的内容被覆盖）"
    assert Path(_first["log"]).read_bytes() == b"FIRST_EXECUTION", "旧执行的日志没了"
    assert Path(_second["log"]).exists(), "新执行的日志不在"


def test_corrupt_backups_are_unique_per_content(tmp_path, monkeypatch, capsys):
    """复核反例 3b：同一秒内两次损坏 ⇒ 备份名按**内容摘要**区分，两份都留下."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24h", 1, out_name="o8")
    assert _s.drv.main([]) == 0
    for _tag in ("first_corruption", "second_corruption"):
        (_s.bdir() / "summary.json").write_text(_tag, encoding="utf-8")
        assert _s.drv.main(["--resume", _s.bid()]) == 0
    _baks = sorted((_s.bdir()).glob("summary.corrupt-*.json"))
    _kept = sorted(_p.read_text(encoding="utf-8") for _p in _baks)
    assert _kept == ["first_corruption", "second_corruption"], _kept


def test_the_same_corruption_is_not_backed_up_twice(tmp_path, monkeypatch):
    """对照组：同样的损坏内容重复出现 ⇒ 复用同一份备份（不无限堆积）."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24i", 1, out_name="o9")
    assert _s.drv.main([]) == 0
    _p = _s.bdir() / "summary.json"
    _p.write_text("broken", encoding="utf-8")
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    _p.write_text("broken", encoding="utf-8")
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    assert len(list(_s.bdir().glob("summary.corrupt-*.json"))) == 1


def test_a_clean_batch_and_its_resume_agree(tmp_path, monkeypatch, capsys):
    """对照组：干净批次 + 续跑都 0 退出、0 次新执行、分类全空."""
    _s = _drv(monkeypatch, tmp_path, "run10_ch2.py", "_d24j", 2, out_name="o10")
    assert _s.drv.main([]) == 0
    _logs = [r["log"] for r in _s.rows()]
    assert len(set(_logs)) == 2 and all(_logs), _logs
    _st = _s.lc.load_batch(str(_s.bdir()), batch_id=_s.bid(), key="run")
    assert _st["ok"] and _st["pending"] == [] and _st["superseded"] == [], _st
    _n = len(_s.calls)
    assert _s.drv.main(["--resume", _s.bid()]) == 0
    assert len(_s.calls) == _n
    assert "本次实跑 0 次" in capsys.readouterr().out


def test_a_recovered_record_must_satisfy_the_job_table():
    """单元：`load_batch(config=…)` 用**同一份**合法作业表校验恢复出来的行."""
    _l = _lc()
    if _l is None:
        pytest.skip("harness 不在")
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        _d = Path(_td) / "batch_x"
        _d.mkdir()
        _cfg = _l.batch_config(driver="d", chapters=[2], n_runs=1, chapter_dir=_td)
        _bid = "X"
        _rec = {"batch_id": _bid, "key": "run", "slot": 7, "run_id": "R7", "seq": 1,
                "claimed_run_id": "R7", "chapter": 2, "evidence": {"dir": "", "files": {}},
                "started": True, "accepted": True, "reason": "", "at": "2026-10-01T00:00:00",
                "row": {"run": 7, "chapter": 2, "started": True, "run_id": "R7",
                        "exec_run_id": "R7", "batch_id": _bid}}
        assert _l.commit_rows(str(_d), [_rec["row"]])["ok"] is True
        io.open(_l.attempts_path(str(_d)), "w", encoding="utf-8").write(
            json.dumps(_rec, ensure_ascii=False) + chr(10))
        _st = _l.load_batch(str(_d), batch_id=_bid, key="run", config=_cfg)
        assert _st["ok"] is False and "作业表" in _st["problem"], _st
