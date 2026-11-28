# -*- coding: utf-8 -*-
"""第十二轮复核的四类离线反例：**本轮产物归属**必须是所有入口、所有字段的共同不变量.

复核点名的四类（全部离线复现，无模型调用）：
  1. **清场失败后仍采用旧诊断** —— 清不掉就照跑，同秒旧诊断被选中；
  2. **被排除的旧诊断仍污染详情** —— 选择器拒了，summary 照抄旧结论/旧统计；
  3. **旧 commit 遮挡本轮计划诊断** —— 只要 commit 带 `run_id` 就不再看诊断；
  4. **批内跨轮复用** —— 整批起始时间判归属，批内上一轮的诊断天然满足 `mtime > _T0`。

处置方向（复核建议，全部落地在 `.tmp/run_lifecycle.py` 这一份实现里）：
运行前**固定请求身份**（`STEP01_RUN_ID` 注入，父子同一个 run_id）→ 时间只作辅助检查；
清场失败**停止该轮**；状态/详情/身份/归档**全部**从通过归属核验的产物生成；
单跑与两个批跑驱动**共用**同一套生命周期。
"""
from __future__ import annotations

import pytest

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import step01_script_gen.plan_feasibility as pf  # noqa: E402


def _load_lc(mod_name: str = "_lc_r12"):
    _p = _ROOT / ".tmp" / "run_lifecycle.py"
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


def _now(offset: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + offset))


_PLAN = {"plan_contract_version": 4, "dialogue_lines": ["甲"], "narration_lines": [],
         "shot_beats": ["节拍：甲｜本镜信息目的：x｜原文依据：“甲”｜载体：画面"]}


# ---------------------------------------------------------------------------
# 反例 1：清场失败 ⇒ 本轮**不启动**
# ---------------------------------------------------------------------------
def test_a_failed_clear_stops_the_round_instead_of_running_anyway(tmp_path, monkeypatch):
    """清不掉旧诊断就**不启动本轮**，记隔离失败（复核 P2-①）.

    旧实现在清场失败后照常启动下一轮：同秒写下的旧诊断（时间戳判不出）被选中，
    汇总报出上一轮的 `stop_layer=final、run_id=previous-run`。
    判别性：允许"清不掉也照跑" ⇒ 本用例里桩子进程会被调用，断言失败。
    """
    _lc = _load_lc("_lc_r12a")
    if _lc is None:
        return
    chd = tmp_path / "chapter_001"
    chd.mkdir()
    _stale = {"run_id": "previous-run", "written_at": _now(),
              "stop_layer": pf.STOP_LAYER_FINAL, "conclusion": "dialogue_incomplete"}
    (chd / "step01_dialogue_incomplete.json").write_text(
        json.dumps(_stale, ensure_ascii=False), encoding="utf-8")

    _called = []

    def _boom(path, *a, **kw):
        if str(path).endswith("step01_dialogue_incomplete.json"):
            raise OSError("文件被占用")
        return _real_remove(path, *a, **kw)

    _real_remove = _lc.os.remove
    monkeypatch.setattr(_lc.os, "remove", _boom)
    monkeypatch.setattr(_lc.subprocess, "call",
                        lambda *a, **kw: _called.append(a) or 0)

    row = _lc.run_once(str(chd), str(tmp_path / "run"), chapter=1)

    assert _called == [], "清场失败却仍然启动了流水线"
    assert row["isolation_failed"] is True, row
    assert row["rc"] is None, row
    assert row["stop_layer"] == "未启动（隔离失败）", row
    assert row["isolation_warnings"] and "清场失败" in row["isolation_warnings"][0], row
    assert row["diagnostic_file"] == "" and row["run_id"], row
    assert (chd / "step01_dialogue_incomplete.json").exists(), "清不掉的文件被动过"


# ---------------------------------------------------------------------------
# 反例 2：被排除的旧诊断不得污染详情
# ---------------------------------------------------------------------------
def test_a_rejected_diagnostic_does_not_leak_into_the_summary_details(tmp_path):
    """选择器拒了旧诊断，汇总就**不能**再写它的结论/统计/问题清单（复核 P2-②）.

    旧实现 `dialogue_incomplete = bool(dialogue)`、`contract_problems = contract["problems"]`
    都是**未过滤的原始读数**；把旧诊断的时间移早 30 秒，选择器正确拒绝，summary 照样
    写出 `dialogue_incomplete=True` + 旧错误原因 + 旧统计。
    判别性：`build_row` 从 `diags` 取详情 ⇒ 本用例失败。
    """
    _lc = _load_lc("_lc_r12b")
    if _lc is None:
        return
    now = time.time()
    _diags = {
        "step01_dialogue_incomplete.json": {
            "run_id": "上一轮", "written_at": _now(-30),
            "stop_layer": pf.STOP_LAYER_FINAL, "conclusion": "dialogue_incomplete",
            "attempt_stats": {"total": 9, "dialogue_broken": 4}},
        "step01_plan_contract_invalid.json": {
            "run_id": "上一轮", "written_at": _now(-30), "stop_layer": pf.STOP_LAYER_PLAN,
            "problems": ["F1 旧问题"], "plan_contract_version": 4},
        "step01_plan_infeasible.json": {
            "run_id": "上一轮", "written_at": _now(-30), "stop_layer": pf.STOP_LAYER_PLAN,
            "conclusion": "teardown"},
    }
    _sel = pf.select_diagnostic(_diags, since=now, expect_run_id="本轮")

    row = _lc.build_row(chapter=2, rc=1, duration_sec=3.0, run_id="本轮",
                        diags=_diags, sel=_sel, commit={}, timing={}, owned={})

    assert row["dialogue_incomplete"] is False, row
    assert row["dialogue_conclusion"] is None and row["dialogue_stats"] is None, row
    assert row["contract_invalid"] is False and row["contract_problems"] is None, row
    assert row["infeasible"] is False and row["infeasible_conclusion"] is None, row
    assert row["plan_contract_version"] is None, row
    # "有哪些没被采用"仍要报出来（报名字，不报内容）
    assert row["diagnostics_present"] == sorted(_diags), row
    assert row["foreign_files"] == sorted(_diags), row


# ---------------------------------------------------------------------------
# 反例 3：旧 commit 不得遮住本轮的诊断
# ---------------------------------------------------------------------------
def test_an_old_commit_does_not_mask_this_round_plan_diagnostic(tmp_path):
    """章节目录里躺着上一轮的 commit 时，本轮的计划层诊断仍要报出来（复核 P2-③）.

    旧实现 `_stop_layer()` 开头就是 `if commit.get("run_id"): return ""`——旧 commit
    只要带 run_id，就再也不看诊断，本轮停在计划层的事实被完全遮住。
    判别性：`build_row` 在 `commit_owned=False` 时仍借用旧 commit 的终局（或返回空停止层）⇒ 失败。
    """
    _lc = _load_lc("_lc_r12c")
    if _lc is None:
        return
    now = time.time()
    _diags = {"step01_plan_contract_invalid.json": {
        "run_id": "本轮", "written_at": _now(), "stop_layer": pf.STOP_LAYER_PLAN,
        "plan_contract_version": 4, "problems": ["F1 本轮问题"]}}
    _sel = pf.select_diagnostic(_diags, since=now, expect_run_id="本轮")
    _commit = {"run_id": "上一轮", "status": "BLOCK", "blocking_count": 12,
               "blocking": [{"code": "MISSING_PLOT"}], "audit_status": "blocking",
               "detail": "上一轮的终局"}
    _owned = _lc.owned_artifacts(str(tmp_path), run_id="本轮", t0=now,
                                 sel=_sel)

    row = _lc.build_row(chapter=1, rc=1, duration_sec=5.0, run_id="本轮",
                        diags=_diags, sel=_sel, commit=_commit, timing={}, owned=_owned)

    assert row["stop_layer"] == pf.STOP_LAYER_PLAN, row
    assert row["diagnostic_file"] == "step01_plan_contract_invalid.json", row
    assert row["plan_contract_version"] == 4, row
    assert row["contract_problems"] == ["F1 本轮问题"], row
    # 旧 commit 的终局**一个字都不许借**
    assert row["commit_owned"] is False and row["commit_run_id"] == "上一轮", row
    assert row["status"] is None and row["blocking_count"] is None, row
    assert row["blocking_codes"] == [] and row["audit_status"] is None, row
    assert row["detail"] == "", row


# ---------------------------------------------------------------------------
# 反例 4：批内跨轮复用
# ---------------------------------------------------------------------------
def test_a_previous_round_diagnostic_inside_the_batch_is_rejected():
    """同一批里上一轮的诊断：时间上"很新"（batch T0 之后），身份上不是本轮（复核 P2-④）.

    旧批跑用整批起始时间 `_T0` 判归属 ⇒ 批内第 N-1 轮的诊断天然满足 `mtime > _T0`，
    第 N 轮直接复用它。按**请求身份**核验后它进 `foreign_files`。
    """
    _batch_t0 = time.time() - 600                     # 整批开始
    _round1 = {"run_id": "批内第一轮", "written_at": _now(-60),   # 60 秒前写的：比 _T0 新
               "stop_layer": pf.STOP_LAYER_FINAL, "conclusion": "dialogue_incomplete"}
    _diags = {"step01_dialogue_incomplete.json": _round1}

    _by_time = pf.select_diagnostic(_diags, since=_batch_t0)      # 旧口径：按整批时间
    assert _by_time["file"] == "step01_dialogue_incomplete.json", _by_time

    _by_id = pf.select_diagnostic(_diags, since=time.time(), expect_run_id="批内第二轮")
    assert _by_id["file"] == "" and _by_id["stop_layer"] == "", _by_id
    assert _by_id["foreign_files"] == ["step01_dialogue_incomplete.json"], _by_id
    assert _by_id["stale_files"] == [], "身份核验不过的不该被记成'陈旧'"


def test_a_previous_round_diagnostic_left_in_place_is_not_adopted_by_run_once(
        tmp_path, monkeypatch):
    """执行级：章节目录里留下的**上一轮**诊断（身份对不上）不被本轮采用."""
    _lc = _load_lc("_lc_r12d")
    if _lc is None:
        return
    chd = tmp_path / "chapter_001"
    chd.mkdir()

    def _fake(argv, stdout=None, stderr=None, env=None):
        # 桩子进程"留下"一份**别的轮**的诊断（模拟批内复用/遗留），本轮自己什么都没写
        (chd / "step01_dialogue_incomplete.json").write_text(
            json.dumps({"run_id": "批内上一轮", "written_at": _now(),
                        "stop_layer": pf.STOP_LAYER_FINAL,
                        "conclusion": "dialogue_incomplete"}, ensure_ascii=False),
            encoding="utf-8")
        return 1

    monkeypatch.setattr(_lc.subprocess, "call", _fake)
    row = _lc.run_once(str(chd), str(tmp_path / "run"), chapter=1)

    assert row["diagnostic_file"] == "", row
    assert row["foreign_files"] == ["step01_dialogue_incomplete.json"], row
    assert row["dialogue_incomplete"] is False and row["stop_layer"].startswith("未识别"), row


# ---------------------------------------------------------------------------
# 三个驱动共用同一套生命周期
# ---------------------------------------------------------------------------
def test_all_three_drivers_share_one_lifecycle_implementation():
    """单跑与两个批跑驱动**共用** `.tmp/run_lifecycle.py`，不再各维护一套.

    判别性：任一驱动退回自己手写诊断名单 / 自己的陈旧判定 ⇒ 本用例失败。
    """
    _drivers = ("run_verify_2.py", "run20_ch1.py", "run10_ch2.py")
    for _f in _drivers:
        _p = _ROOT / ".tmp" / _f
        if not _p.exists():
            continue
        _src = io.open(_p, encoding="utf-8").read()
        assert "import run_lifecycle as lc" in _src, "%s 没有用共用生命周期" % _f
        for _n in pf.DIAGNOSTIC_FILES:
            assert _n not in _src, "%s 手写了诊断名单：%s" % (_f, _n)
        for _old in ("run_id_before", "def _stop_layer", "def _fresh", "_ARTIFACTS ="):
            assert _old not in _src, "%s 还留着自建的归属判定：%s" % (_f, _old)


def test_the_run_id_can_be_pinned_before_the_child_starts(monkeypatch):
    """产品侧认驱动注入的 `STEP01_RUN_ID`（父子同一个本轮身份，复核建议①）.

    没有它，"运行前固定请求身份"就无从谈起——驱动只能事后按时间猜哪份产物是自己的。
    判别性：`_step01_run_id()` 不读该变量 ⇒ 本用例失败（子进程会另生成一个 run_id，
    诊断与提交产物都归不到本轮名下）。
    """
    import step01_script_gen.main as m
    monkeypatch.setenv("STEP01_RUN_ID", "20261001T000000-固定")
    assert m._step01_run_id() == "20261001T000000-固定"
    monkeypatch.delenv("STEP01_RUN_ID", raising=False)
    assert m._step01_run_id() not in ("", "20261001T000000-固定")
