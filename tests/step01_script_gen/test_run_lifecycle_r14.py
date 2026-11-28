# -*- coding: utf-8 -*-
"""第十四轮复核两处的离线回归：**批次身份** 与 **正式产物只认 manifest**.

复核点名的两处（全部离线复现，无模型调用）：
  1. **P1 批次续跑把旧样本计入新批**：`.tmp/run10_ch2/summary.json` 里躺着 **09-29 的 10 条**
     旧记录，驱动只按轮号恢复 ⇒ 10 轮全部 SKIP、子进程调用 **0** 次，却报"10 次完成"；
     第一章同样跳过旧的 2 条。
  2. **P2 正式产物仍能绕过 manifest 借 mtime 冒充本轮**：快速两轮反例中第二轮未写任何产物，
     正确拒绝了首轮 commit，却仍把首轮的 `script_result.json` / `step01_quality.json`
     标为本轮 owned 并归档（依据都是 `mtime`）。

共同根因：**请求身份已固定，批次身份和正式产物的证明边界还没完全统一。**
处置：新批用独立 `batch_id` + 独立目录，续跑必须显式指定批次并核对配置与源码指纹；
三份正式产物只凭本轮已核验的 manifest 认领，**取消一切 mtime 兜底**。
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
    return _load("_lc_r14_%d" % int(time.time() * 1000 % 100000), "run_lifecycle.py")


def _write_chapter_dir(root: Path, ch: int = 1) -> Path:
    """一个**真实形态**的章节目录：`chapter_00N` + `script_result.json`（带 chapter_index）."""
    chd = root / ("chapter_%03d" % ch)
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": ch, "shots": [{"index": 1, "narration_segment": "甲"}]},
                   ensure_ascii=False), encoding="utf-8")
    return chd


def _write_committed_manifest(chd: Path, run_id: str) -> dict:
    """写一份**能通过产品侧核验**的 commit manifest（revision + 三份产物 sha1/字节数）."""
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
    _man = {"manifest_version": 1, "status": "COMMITTED", "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "script_revision": compute_script_revision(_script),
            "rule_source_fingerprint": "r", "rule_version": "v", "audit_scope": "full",
            "blocking_count": 0, "blocking": [], "blocking_truncated": False,
            "audit_status": "ok", "detail": "", "artifacts": _arts}
    (chd / "step01_commit.json").write_text(json.dumps(_man, ensure_ascii=False),
                                           encoding="utf-8")
    return _man


def _stub_calls(monkeypatch, lc):
    """桩子进程：不发模型调用，只记账（返回接收到的本轮身份）."""
    calls = []
    monkeypatch.setattr(lc.subprocess, "call",
                        lambda *a, **kw: calls.append((kw.get("env") or {})
                                                       .get("STEP01_RUN_ID")) or 0)
    return calls


def _batch_dirs(root: Path):
    return sorted(root.glob("batch_*"))


# ---------------------------------------------------------------------------
# ① 批次身份：旧批样本不得计入新批
# ---------------------------------------------------------------------------
def test_an_old_root_summary_does_not_start_the_new_batch(tmp_path, monkeypatch):
    """批次根目录里的**旧批 summary** 不得当成本批已完成的轮次（复核 P1-①）.

    判别性：退回"只按轮号恢复"（读 `OUT/summary.json`）⇒ 桩子进程调用 0 次、本用例失败。
    """
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r14", "run10_ch2.py")
    assert _drv is not None
    chd = _write_chapter_dir(tmp_path, 2)
    out = tmp_path / "out"
    out.mkdir()
    # 真实形态的旧批残留：09-29 的 10 条（旧字段名 `run`，没有 batch_id）
    legacy = [{"run": i, "chapter": 2, "rc": 0, "status": "BLOCK", "duration_sec": 800}
              for i in range(1, 11)]
    (out / "summary.json").write_text(json.dumps(legacy, ensure_ascii=False),
                                      encoding="utf-8")
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(out))
    monkeypatch.setattr(_drv, "N_RUNS", 3)
    calls = _stub_calls(monkeypatch, _l)

    assert _drv.main([]) == 0
    assert len(calls) == 3, "旧批 summary 让本轮少跑了：实际调用 %d 次（应为 3）" % len(calls)

    _dirs = _batch_dirs(out)
    assert len(_dirs) == 1, _dirs
    _rows = json.loads((_dirs[0] / "summary.json").read_text(encoding="utf-8"))
    assert [r["run"] for r in _rows] == [1, 2, 3], _rows
    assert len({r["batch_id"] for r in _rows}) == 1, _rows
    assert _rows[0]["batch_id"] == _dirs[0].name[len("batch_"):], _rows[0]
    # 旧批文件**原样没动**（不迁移、不覆盖，只是不再被读）
    assert len(json.loads((out / "summary.json").read_text(encoding="utf-8"))) == 10


def test_a_batch_records_identity_config_and_source_fingerprint(tmp_path):
    """新批落 `batch.json`：批次号 / 配置 / 源码与配置指纹三样都得在（复核 P1-① 建议）."""
    _l = _lc()
    if _l is None:
        return
    _cfg = _l.batch_config(driver="run10_ch2.py", chapters=[2], n_runs=10,
                           chapter_dir="output/.../chapter_002",
                           self_path=str(_ROOT / ".tmp" / "run10_ch2.py"))
    _b = _l.open_batch(str(tmp_path), config=_cfg)
    assert _b["ok"] and _b["resumed"] is False and _b["rows"] == [], _b
    _meta = json.loads((Path(_b["dir"]) / "batch.json").read_text(encoding="utf-8"))
    assert _meta["batch_id"] == _b["batch_id"], _meta
    assert _meta["config"] == _cfg, _meta["config"]
    _files = _meta["source_files"]
    assert _files and _meta["source_fingerprint"] == _l._fingerprint(_files), _meta.keys()
    # 指纹覆盖源码与生效配置，且**不含产物目录**
    assert any(k.startswith("step01_script_gen/") for k in _files), _files.keys()
    assert any(k.startswith("shared/") for k in _files), _files.keys()
    assert not any(k.startswith("output/") or k.startswith(".tmp/run") and k.endswith(".log")
                   for k in _files), [k for k in _files if k.startswith("output/")]


def test_resume_needs_an_explicit_existing_batch(tmp_path, monkeypatch):
    """`--resume` 指向不存在的批次 ⇒ **拒绝并退出 2**，一轮都不跑（fail-closed）."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r14b", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(tmp_path / "out"))
    calls = _stub_calls(monkeypatch, _l)
    assert _drv.main(["--resume", "nosuchbatch"]) == 2
    assert calls == [], calls
    assert not _batch_dirs(tmp_path / "out"), "被拒的续跑不该留下批次目录"


def test_resume_refuses_a_config_mismatch(tmp_path, monkeypatch):
    """同一个批次号、换了轮数 ⇒ 拒绝续跑（配置核验），批内记录不受影响（复核建议）."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r14c", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(tmp_path / "out"))
    monkeypatch.setattr(_drv, "N_RUNS", 2)
    calls = _stub_calls(monkeypatch, _l)
    assert _drv.main([]) == 0
    _dirs = _batch_dirs(tmp_path / "out")
    _bid = _dirs[0].name[len("batch_"):]
    _n = len(calls)

    monkeypatch.setattr(_drv, "N_RUNS", 5)          # 换了轮数 = 换了批
    assert _drv.main(["--resume", _bid]) == 2
    assert len(calls) == _n, "被拒的续跑仍然跑了：%d" % (len(calls) - _n)
    _rows = json.loads((_dirs[0] / "summary.json").read_text(encoding="utf-8"))
    assert len(_rows) == 2, _rows


def test_resume_refuses_when_the_source_changed(tmp_path):
    """续跑时源码/配置已改 ⇒ 拒绝，并**指出改了哪个文件**（同批必须同源）."""
    _l = _lc()
    if _l is None:
        return
    _src = tmp_path / "probe_src.py"
    _src.write_text("X = 1\n", encoding="utf-8")
    _cfg = _l.batch_config(driver="d", chapters=[1], n_runs=1)
    _b = _l.open_batch(str(tmp_path / "batches"), config=_cfg, extra_files=[str(_src)])
    assert _b["ok"], _b
    _src.write_text("X = 2\n", encoding="utf-8")     # 批跑中途改源码
    _b2 = _l.open_batch(str(tmp_path / "batches"), config=_cfg, resume=_b["batch_id"],
                        extra_files=[str(_src)])
    assert _b2["ok"] is False, _b2
    assert "probe_src.py" in _b2["reason"] and "源码" in _b2["reason"], _b2["reason"]


def test_resume_skips_only_the_rounds_already_in_that_batch(tmp_path, monkeypatch):
    """续跑只跳过**本批**已有记录的轮次，且不新开批次目录."""
    _l = _lc()
    if _l is None:
        return
    _drv = _load("_drv10_r14d", "run10_ch2.py")
    chd = _write_chapter_dir(tmp_path, 2)
    monkeypatch.setattr(_drv, "CH", str(chd))
    monkeypatch.setattr(_drv, "OUT", str(tmp_path / "out"))
    monkeypatch.setattr(_drv, "N_RUNS", 2)
    calls = _stub_calls(monkeypatch, _l)
    assert _drv.main([]) == 0
    _dirs = _batch_dirs(tmp_path / "out")
    _bid = _dirs[0].name[len("batch_"):]
    assert len(calls) == 2, calls

    assert _drv.main(["--resume", _bid]) == 0       # 全部已完成 ⇒ 不再跑
    assert len(calls) == 2, "续跑又跑了已完成的两轮：%s" % calls
    assert len(_batch_dirs(tmp_path / "out")) == 1, "续跑新开了批次目录"


# ---------------------------------------------------------------------------
# ② 正式产物只认已核验的 manifest
# ---------------------------------------------------------------------------
def test_formal_artifacts_are_only_owned_through_the_verified_manifest(tmp_path):
    """三份正式产物：manifest 未核验通过 ⇒ **一份都不属于本轮**；核验通过 ⇒ 按 manifest 归属.

    判别性：留着 mtime 兜底 ⇒ 第一段断言就失败（刚写下的文件被 mtime 认领）。
    """
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 1)
    for _n in _l.FORMAL_ARTIFACTS:
        (chd / _n).write_text(json.dumps({"run_id": "上一轮"}), encoding="utf-8")
    _now = time.time()
    _sel = pf.select_diagnostic({}, since=_now, expect_run_id="本轮")

    for _how, _commit in ((0, {"owned": True, "verified": False, "registered": []}),
                          (0, {})):
        _owned = _l.owned_artifacts(str(chd), run_id="本轮", t0=_now - 1.0, sel=_sel,
                                    commit=_commit)
        assert not (set(_owned) & set(_l.FORMAL_ARTIFACTS)), _owned
        assert "mtime" not in {v["how"] for v in _owned.values()}, _owned
    # 单份判据本身也不给 mtime 留口子
    for _n in _l.FORMAL_ARTIFACTS:
        assert _l.artifact_ownership(_n, str(chd / _n), run_id="本轮",
                                     t0=_now - 1.0) == "", _n

    _owned = _l.owned_artifacts(
        str(chd), run_id="本轮", t0=_now - 1.0, sel=_sel,
        commit={"owned": True, "verified": True, "registered": list(_l.FORMAL_ARTIFACTS)})
    for _n in _l.FORMAL_ARTIFACTS:
        assert _owned[_n]["how"] == "manifest", _owned.get(_n)
        assert _owned[_n]["run_id"] == "上一轮", _owned[_n]      # 来源身份保留



def _archived(run_dir: Path, name: str) -> list:
    """按执行身份隔离的归档目录里的产物（第 25 轮复核：`run_dir/exec-<run_id>/`）."""
    return sorted(run_dir.glob("exec-*/%s" % name))

def test_the_second_round_archives_nothing_when_it_writes_nothing(tmp_path, monkeypatch):
    """快速两轮：第二轮什么都没写 ⇒ 归档里**不许有**首轮的正式产物（复核 P2-② 反例）.

    两轮间隔只有几毫秒，小于文件系统刻度（NTFS ~15.6ms）＋余量 ⇒ 旧的 mtime 判据会把
    首轮的文件认成第二轮的（正是复核观测到的现象）。判别性：把 mtime 兜底加回去 ⇒ 本用例失败。
    """
    _l = _lc()
    if _l is None:
        return
    chd = _write_chapter_dir(tmp_path, 1)
    _round = {"n": 0}

    def _fake(argv, stdout=None, stderr=None, env=None):
        _round["n"] += 1
        if _round["n"] == 1:
            _write_committed_manifest(chd, (env or {}).get("STEP01_RUN_ID", ""))
            return 0
        return 1                                   # 第二轮提前失败，什么都不写

    monkeypatch.setattr(_l.subprocess, "call", _fake)
    _run1 = tmp_path / "run1"
    _run2 = tmp_path / "run2"
    _r1 = _l.run_once(str(chd), str(_run1), chapter=1)
    _r2 = _l.run_once(str(chd), str(_run2), chapter=1)

    assert _r1["commit_owned"] and _r1["commit_verified"], _r1
    for _n in _l.FORMAL_ARTIFACTS:
        assert _r1["artifact_sources"][_n]["how"] == "manifest", _r1["artifact_sources"]
        assert _archived(_run1, _n), "首轮的正式产物没有归档：%s" % _n

    assert _r2["commit_owned"] is False and _r2["status"] is None, _r2
    assert not (set(_r2["owned_artifacts"]) & set(_l.FORMAL_ARTIFACTS)), _r2["owned_artifacts"]
    for _n in _l.FORMAL_ARTIFACTS:
        assert not _archived(_run2, _n), "第二轮归档了首轮的正式产物：%s" % _n
    # 认不出来历的（首轮刚写下的那些）**显式列出来**，不静默丢
    assert set(_r2["suspicious_recent"]) <= set(_l.ARTIFACTS), _r2["suspicious_recent"]


def test_a_recent_artifact_without_identity_is_reported_not_silently_dropped(tmp_path):
    """没有身份判据的产物：**不算本轮**，但要出现在 `suspicious_recent` 里（不静默丢）.

    顺带钉住修复轮台账的归属：它是**列表**，身份在行里（行内 `run_id`），不看 mtime。
    """
    _l = _lc()
    if _l is None:
        return
    chd = tmp_path / "chapter_001"
    chd.mkdir()
    _p = chd / "step01_repair_rounds.json"
    _p.write_text(json.dumps([{"recorded_at": "x", "run_id": "上一轮"}], ensure_ascii=False),
                  encoding="utf-8")
    _now = time.time()
    _sel = pf.select_diagnostic({}, since=_now, expect_run_id="本轮")

    assert _l.artifact_ownership("step01_repair_rounds.json", str(_p), run_id="本轮",
                                 t0=_now - 1.0) == ""
    _owned = _l.owned_artifacts(str(chd), run_id="本轮", t0=_now - 1.0, sel=_sel)
    assert "step01_repair_rounds.json" not in _owned, _owned
    _susp = _l.recent_unowned(str(chd), _owned, t0=_now - 1.0)
    assert _susp == ["step01_repair_rounds.json"], _susp

    _p.write_text(json.dumps([{"recorded_at": "x", "run_id": "上一轮"},
                              {"recorded_at": "y", "run_id": "本轮"}], ensure_ascii=False),
                  encoding="utf-8")
    _owned2 = _l.owned_artifacts(str(chd), run_id="本轮", t0=_now - 1.0, sel=_sel)
    assert _owned2["step01_repair_rounds.json"]["how"] == "run_id", _owned2
    assert _l.recent_unowned(str(chd), _owned2, t0=_now - 1.0) == []
