# -*- coding: utf-8 -*-
"""**规划不可行的贯通基线**（2026-09-25 第一步：只固定读数，不改生产行为）.

用户 2026-09-25 的统一方案（原文摘要）：

> 护栏多数有价值，但它们被迫在下游补一个上游没有完成的决定——**本集究竟选哪些内容、
> 能否在 90 秒内拍完**。
> 证据链：大纲的 `time_budget` 只是概略文字，没有核算选用对白；本轮新稿的对白镜已占
> **91.03 秒**。拆镜把初稿约 85 秒推到约 120 秒后，重生验收又要求保留初稿的全部台词，
> 次要对白无法在规划层重新取舍。跨镜道具问题则被送进字段级修复，最终形成超过 6 镜的
> 原子组。
> 落地顺序是**先用现有失败稿写贯通回归**，再做「计划＋生成前可行性」，随后收口拆镜和
> 审计分流，最后替换选稿兜底。**每一步分别验收，不把多项改动混成一次重跑。**

所以本文件是那**第一步**：用**冻结的失败稿**把今天的读数钉住（characterization）。
后续每一步要**显式改掉其中某一条**，并在这里同步断言——不静默漂移。

夹具（均已冻结进 `fixtures/`，与运行时现场文件无关；理由见
`docs/step01-audit-payload-fixture-and-identity-rerun-2026-09-25.md`）：

  - `draft_failed_20260925_184302.json` / `..._175311.json`：两轮的 pre-BEST-GUARD 新稿
  - `draft_selected_snapshot_20260924_215530.json`：被选中的历史快照（= 可拍完的那份）
  - `outline_20260925.json`：本轮实际使用的大纲（"本集计划"的现状）
  - `audit_blocked_20260925_prop_release.json`：本轮 BLOCK 的审计响应
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_FIX = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    """读夹具；**缺失即失败**（用户 2026-09-25：不允许静默跳过）.

    这些夹具与本研究文件**随仓库交付**（`.gitignore` 里有 09-25 的定向例外），
    缺失说明检出损坏，而不是"本机没数据"——所以这里直接断言，不用 skipif。
    """
    p = _FIX / name
    assert p.exists(), (
        "夹具缺失：%s —— 它随仓库交付（见 .gitignore 的 09-25 例外）；"
        "缺失属检出损坏，必须显式失败，不得跳过" % p)
    return json.loads(p.read_text(encoding="utf-8"))


_FAILED = {"latest": _fixture("draft_failed_20260925_184302.json"),
           "previous": _fixture("draft_failed_20260925_175311.json")}
_SELECTED = _fixture("draft_selected_snapshot_20260924_215530.json")
_OUTLINE = _fixture("outline_20260925.json")
_PAYLOAD = _fixture("audit_blocked_20260925_prop_release.json")


def _dialogue_stats(script: dict) -> dict:
    from step01_script_gen.timecode import _estimate_duration
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    lines = [s for s in shots if str(s.get("narration_segment") or "").strip()]
    return {
        "shots": len(shots),
        "dialogue_shots": len(lines),
        "dialogue_duration_sec": round(
            sum(float(s.get("duration_sec") or 0) for s in lines), 2),
        "dialogue_estimate_sec": round(
            sum(_estimate_duration(str(s.get("narration_segment") or ""))
                for s in lines), 2),
        "total_duration_sec": round(
            sum(float(s.get("duration_sec") or 0) for s in shots), 2),
    }


# ---------------------------------------------------------------------------
# 1) 预算：失败稿把整段预算花在对白上；被选中的旧稿是"能拍完"的那份
# ---------------------------------------------------------------------------
def test_failed_draft_spends_its_whole_budget_on_dialogue():
    """读数固定：对白镜已占 **91.03s**（本轮）/ **93.91s**（上一轮），全片 127.03/120.91s.

    用户给的数字 91.03s 与本次独立复算**一致**（对白镜 `duration_sec` 之和）。

    ⚠️ **只证明到这里**：这两份稿**按当前预算不可行**（对白一项就吃掉整段预算，
    全片 120.91/127.03s 都超出 60–90s 区间）。**不能**据此断言"所有选稿差异都只由
    预算造成"——被选中的旧快照在结构档／完整性档／风险数上也都更好
    （独立复算排名 (0,0,17,-100.0) vs 新稿 (1,1,26,-97.0)），预算只是其中**已被证实**
    的一条因素。用户 2026-09-25 明确要求不要过度归因。
    """
    _latest = _dialogue_stats(_FAILED["latest"])
    _previous = _dialogue_stats(_FAILED["previous"])
    _pick = _dialogue_stats(_SELECTED)
    assert _latest["dialogue_duration_sec"] == pytest.approx(91.03, abs=0.02), _latest
    assert _previous["dialogue_duration_sec"] == pytest.approx(93.91, abs=0.02), _previous
    assert _pick["dialogue_duration_sec"] == pytest.approx(52.02, abs=0.02), _pick
    # 两份失败稿都超出 60–90s 验收区间，旧稿在内
    assert _latest["total_duration_sec"] > 90 and _previous["total_duration_sec"] > 90
    assert _pick["total_duration_sec"] <= 90, _pick
    # 单看对白就已经吃掉整段预算（>60s 下限）
    assert _latest["dialogue_duration_sec"] > 60


# ---------------------------------------------------------------------------
# 2) 计划：现在的"本集计划"没有任何可核算的数字
# ---------------------------------------------------------------------------
def test_the_plan_now_carries_what_the_gate_needs():
    """**第二步已改掉这条基线**：计划必须带上可核算字段，且由代码算时长.

    改之前（第一步基线读数）：`time_budget` 只是"对白约30秒；动作约40秒；反应约30秒"
    这类自由文本，顶层**没有任何数值字段**，生成前无从验算。
    改之后：schema 强制 `dialogue_lines` / `omittable`；可行性门用代码估算器
    （`timecode._estimate_duration`）逐句算，不采信自报秒数。详见
    `tests/step01_script_gen/test_plan_feasibility.py` 与
    `docs/step01-plan-feasibility-gate-2026-09-25.md`。
    """
    from _text_gen.deepseek_schema import OUTLINE_SCHEMA
    from step01_script_gen import plan_feasibility as pf

    _props = OUTLINE_SCHEMA["schema"]["properties"]
    _req = OUTLINE_SCHEMA["schema"]["required"]
    for f in ("dialogue_lines", "omittable"):
        assert f in _props and f in _req, f
    # 门与计划字段对得上：缺字段即不可评估（fail-closed），而不是"没数据就放行"
    assert pf.evaluate_plan({})["evaluable"] is False
    # 历史读数留档：本轮实际用过的那份大纲（第二步之前生成）确实没有任何数值字段
    assert not [k for k, v in _OUTLINE.items() if isinstance(v, (int, float))], (
        "冻结的历史大纲不该有数值字段")
    assert "秒" in str(_OUTLINE.get("time_budget"))


# ---------------------------------------------------------------------------
# 3) 拆镜/重生的验收基线是**第一份失败稿**，不是已验证计划
# ---------------------------------------------------------------------------
def test_regen_acceptance_uses_the_first_draft_as_its_baseline(monkeypatch):
    """去掉一句次要对白 → 判「台词丢失」 ⇒ 验收基线取自初稿，规划层无法重新取舍.

    ⇒ 第三步要改成：保护**已验证计划中的**对白与事实，而不是初稿碰巧写下的每一句。
    """
    import step01_script_gen.main as m

    _before = json.loads(json.dumps(_FAILED["latest"]))
    _after = json.loads(json.dumps(_FAILED["latest"]))
    _lines = [s for s in _after["shots"]
              if str(s.get("narration_segment") or "").strip()]
    assert len(_lines) >= 2, "夹具里对白镜太少，用例失去判别性"
    _lines[-1]["narration_segment"] = ""
    _lines[-1]["subtitle"] = ""

    # 台词判据只读稿件本身（不需要原文）；grounding 判据在无原文时会走
    # fail-closed 的"检查器异常"分支，多出的理由不影响本断言。
    _reasons = m._content_degradation(_before, _after, "")
    assert any("台词丢失" in r for r in _reasons), (
        "删掉一句对白没有被判为退化——验收基线可能已经换了，请同步更新本断言：%s"
        % _reasons)
    # 验收输入是**稿件**，没有任何计划参数（计划在今天不参与验收）
    _params = list(inspect.signature(m._content_degradation).parameters)
    assert _params[:2] == ["before", "after"], _params
    assert not [p for p in _params if "plan" in p or "outline" in p], _params
    assert "plan" not in inspect.signature(m._content_inventory).parameters


# ---------------------------------------------------------------------------
# 4) 跨镜道具欠账形成**超限原子组**：字段级修复结构上做不到
# ---------------------------------------------------------------------------
def test_cross_shot_prop_debt_forms_an_oversized_atomic_group():
    """本轮 BLOCK 的那条道具记录：16 个目标跨 **8 个镜头**（3–10）> 组上限 6.

    ⇒ 这属于"需重新规划／生成"，**不靠抬高组上限、也不靠批量删 `props`**（用户口径）。
    这里固定组容量仍是 6，防止有人为解决它偷偷放宽。
    """
    import step01_script_gen.main as m

    assert _PAYLOAD, "审计响应夹具不在本机"
    _rec = next((i for i in _PAYLOAD.get("issues") or []
                 if str(i.get("code") or "").startswith("model_audit_PROP")), None)
    assert _rec, "夹具里没有道具类记录"
    _targets = _rec.get("repair_targets") or []
    _shots = sorted({str(t.get("shot_id")) for t in _targets}, key=int)
    assert _shots == ["3", "4", "5", "6", "7", "8", "9", "10"], _shots
    assert len(_shots) == 8 > m._REPAIR_MAX_SHOTS_PER_GROUP == 6, (
        "组容量被改动了——用户明确要求不要抬高上限")
    # 目标只有 props/描述类字段：路径存在 ≠ 剧情已补（不补原文那次释放）
    assert len({t.get("field_path") for t in _targets}) == 8
    assert all(str(t.get("field_path") or "").endswith("props")
               or str(t.get("field_path") or "").endswith("_desc")
               for t in _targets), [t.get("field_path") for t in _targets]
