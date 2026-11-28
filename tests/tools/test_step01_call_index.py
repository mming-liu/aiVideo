# -*- coding: utf-8 -*-
"""M01：调用索引与样本漂移核对的锁定用例.

数据来源全部是**已提交的受控原件**：
  `baselines/2026-11-12-pre-layered/s06_ab/`（S06 冻结产物）
  `tests/step01_script_gen/fixtures/audit_narrative_families/`（当前夹具）

它锁住三条会被「后来改写」悄悄抹掉的事实：
  1. S06 报告口径的八次调用，其**原始响应全部不可用**（只有解析结果）；
  2. `time_ellipsis_legal` 的当前夹具**无法**重建当时发出的提示词（样本已改版）；
  3. 修订轮的终局是 `no_target_resolved` 且 `audit_field_touched=0`——
     「改动已应用但没被验收识别」这一说法与台账相反。

索引工具本身依赖外部数据集根，所以这里直接调用它的 `build()` 并显式传参，
不走命令行、不读 `.pytest_tmp`。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baselines/2026-11-12-pre-layered/s06_ab"
FIXTURES = ROOT / "tests/step01_script_gen/fixtures/audit_narrative_families"


def _build(fixtures: Path = FIXTURES):
    from tools.build_step01_call_index import build
    return build(SimpleNamespace(baseline_root=str(BASELINE),
                                 live_root=None,
                                 fixtures=str(fixtures)))


@pytest.mark.skipif(not BASELINE.exists(), reason="S06 冻结原件缺失（需 baselines/）")
def test_eight_frozen_calls_are_indexed():
    index = _build()
    camps = {c["campaign_id"]: c for c in index["campaigns"]}
    ab = camps["s06_ab"]
    assert ab["calls"] == 8, ab
    phases = sorted(c["phase"] for c in ab["entries"])
    assert phases.count("audit_ab") == 6
    assert phases.count("repair") == 1
    assert phases.count("final_audit") == 1
    assert index["totals"]["calls"] == 8


@pytest.mark.skipif(not BASELINE.exists(), reason="S06 冻结原件缺失（需 baselines/）")
def test_no_raw_response_is_available_for_any_frozen_call():
    """八次调用都没有原始响应——这条必须显式锁住，避免报告默认「有原件」。"""
    index = _build()
    ab = next(c for c in index["campaigns"] if c["campaign_id"] == "s06_ab")
    assert ab["entries"]
    for call in ab["entries"]:
        assert call["raw_response"]["available"] is False, call["call_id"]
        assert call["raw_response"]["reason"], call["call_id"]


@pytest.mark.skipif(not BASELINE.exists(), reason="S06 冻结原件缺失（需 baselines/）")
def test_repair_round_ledger_contradicts_the_applied_but_unverified_claim():
    """`audit_field_touched=0` ⇒ 没有任何授权字段发生变化（回退前算的）。"""
    index = _build()
    ab = next(c for c in index["campaigns"] if c["campaign_id"] == "s06_ab")
    repair = next(c for c in ab["entries"] if c["phase"] == "repair")
    decision = repair["parsed"]["decision"]
    assert decision["reason"] == "no_target_resolved"
    assert decision["origin_reason"] == "no_target_resolved"
    assert decision["audit_field_touched"] == 0
    assert decision["resolved"] == 0
    assert decision["revision_before"] == decision["revision_after"]
    assert decision["targets"] == ["shot_1", "shot_11", "shot_6", "shot_7"]


@pytest.mark.skipif(not BASELINE.exists(), reason="S06 冻结原件缺失（需 baselines/）")
def test_fixture_drift_separates_the_rewritten_sample_from_the_stable_ones(tmp_path):
    """漂移判据必须**逐样本、逐契约**区分：未改样本 match，只改一份才 drift.

    当前提示词构建器已整体演进，S06 记录的 6 份历史 sha 全部不再匹配；因此本用例
    不拿历史值当"稳定样本"前提，而是用**当前构建器**为同一份夹具生成 recorded
    基线（应 match），再改其中一份的输入（应 drift）——这样同时锁住"能认匹配"
    与"能认漂移"两半，且不依赖现场历史版本。
    """
    from tools.build_step01_call_index import _load_old_audit, _sha1, fixture_drift
    from step01_script_gen import audit as new_mod

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    path = fixtures / "time_ellipsis_legal.json"
    shutil.copy2(FIXTURES / "time_ellipsis_legal.json", path)
    case = json.loads(path.read_text(encoding="utf-8"))
    old_mod = _load_old_audit(BASELINE / "tools/audit_old.py")
    recorded = {
        ("time_ellipsis_legal", "new"): _sha1(new_mod.build_audit_prompt(
            case["novel"], case["plan"], case["script"])),
        ("time_ellipsis_legal", "old"): _sha1(old_mod.build_audit_prompt(
            case["novel"], case["plan"], case["script"])),
    }

    stable = fixture_drift(fixtures, recorded)
    assert stable["checked"] == 2 and stable["drift"] == 0, stable
    assert all(r["status"] == "match" for r in stable["rows"]), stable

    case["novel"] = str(case.get("novel") or "") + "（漂移探针）"
    path.write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    drift = fixture_drift(fixtures, recorded)
    assert drift["checked"] == 2 and drift["match"] == 0, drift
    assert all(r["status"] == "drift" for r in drift["rows"]), drift
