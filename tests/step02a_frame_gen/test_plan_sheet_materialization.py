# -*- coding: utf-8 -*-
"""U7 收口（2026-11-15）：视觉计划的参考拼表「执行期物化」豁免回归.

现场（Step F 13 镜 --plan-only 实测）：shot 7 的 LF 计划引用
``shots/7/ref_sheet_lf_props_*.png``——该拼表由 ``fit_refs`` 在**真实生成期**
按 buckets 确定性拼出，计划构建于生成之前，必然不在盘上。旧判据把它按
``prepared: false`` 判 ``reference_unprepared`` → 镜头 ``status=blocked`` →
02b ``execution_requirements`` 读到 ``visual_plan_blocked``，在真正生成前就
把请求拦了（用户复核指出的第 2 类问题）。

判据修订：
  * 预测拼表（``ref_sheet_*``）→ ``prepared=None`` + ``materialized_at_execution``，
    **可见但不阻断**（记入 ``pending_materialization``）；
  * 真实源资产（肖像/bank/帧图）缺失 → ``prepared=False`` 仍 fail-closed 拒收。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from step02a_frame_gen import execution_plan as ep                     # noqa: E402


def test_ref_entries_sheet_is_materialized_at_execution(tmp_path):
    """预测拼表：prepared=None + materialized 标记，不再是 False."""
    sheet = tmp_path / "ref_sheet_lf_props_deadbeef.png"      # 故意不落盘
    portrait = tmp_path / "portrait_front.png"
    portrait.write_bytes(b"\x89PNG" + b"\x00" * 6000)
    entries = ep._ref_entries([str(sheet), str(portrait)])
    by_path = {e["path"]: e for e in entries}
    sheet_entry = by_path[str(sheet)]
    assert sheet_entry["prepared"] is None
    assert sheet_entry["materialized_at_execution"] is True
    # 真实源资产仍在盘 → prepared=True
    assert by_path[str(portrait)]["prepared"] is True


def test_frame_entry_missing_source_blocks(monkeypatch):
    """真实源资产缺失仍 fail-closed：blocked + reference_unprepared 证据."""
    refs = [
        # 拼表：不在盘，但不阻断
        {"role": "reference", "path": "x/ref_sheet_lf_props_deadbeef.png",
         "purpose": "参考", "sha256": None, "prepared": None,
         "materialized_at_execution": True},
        # 真实源资产：缺失 → 阻断
        {"role": "design_reference", "path": "x/missing_portrait.png",
         "purpose": "身份锚点", "sha256": None, "prepared": False},
    ]
    monkeypatch.setattr(ep, "_frame_refs", lambda *a, **k: list(refs))
    monkeypatch.setattr(ep, "_frame_prompt", lambda *a, **k: ("prompt", None))
    unit = {"index": 7, "ff_desc": "甲立于船头", "lf_desc": "甲转身回望",
            "main_subject": "甲", "prompt": "江面。"}
    entry = ep._frame_entry(unit, [], "lf", "test-model")
    assert entry["status"] == "blocked", "缺失源资产必须拒收"
    assert any(u["code"] == "reference_unprepared" for u in entry["unresolved"])
    assert entry["unresolved"][0]["path"] == "x/missing_portrait.png"
    # 拼表仍只出现在 pending，不进 unresolved
    assert entry["pending_materialization"]
    assert not any("ref_sheet" in str(u.get("path")) for u in entry["unresolved"])


def test_frame_entry_semantics_via_ref_entries(tmp_path, monkeypatch):
    """端到端：_frame_entry 走 _frame_refs 时，预测拼表进 pending 而非 unresolved."""
    sheet_dir = tmp_path / "shots" / "7"
    sheet_dir.mkdir(parents=True)
    # buckets 超限 → fit_refs(dry_run=True) 预测一张拼表（不落盘）
    src = tmp_path / "src"
    src.mkdir()
    srcs = []
    from PIL import Image
    for i in range(4):
        p = src / ("%d.png" % i)
        Image.new("RGB", (8, 8), (10 * i, 20, 30)).save(p)
        srcs.append(str(p))
    monkeypatch.setattr("shared.vimax_bridge._collect_frame_refs_for_shot",
                        lambda *a, **k: {"buckets": [("chars", srcs[:2]),
                                                      ("props", srcs[2:])],
                                         "omissions": []})
    monkeypatch.setattr("shared.ref_compose.active_image_ref_limit", lambda: 1)
    monkeypatch.setattr("shared.vimax_bridge.shot_work_dir", lambda idx: sheet_dir)
    unit = {"index": 7, "ff_desc": "甲立于船头", "lf_desc": "甲转身回望",
            "main_subject": "甲", "prompt": "江面。", "location": "江面"}
    entry = ep._frame_entry(unit, [{"identifier": "甲"}], "lf", "test-model")
    assert entry["status"] == "planned", (
        "预测拼表不得把镜头判 blocked（unresolved=%s）" % entry["unresolved"])
    assert entry["pending_materialization"], "拼表必须显式可见（不得静默消失）"
    assert all(e["code"] == "reference_materialized_at_execution"
               for e in entry["pending_materialization"])
    assert json.dumps(entry, ensure_ascii=False)  # 可序列化
