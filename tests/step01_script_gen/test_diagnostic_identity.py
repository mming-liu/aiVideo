# -*- coding: utf-8 -*-
"""**停止在哪一层，就应从该层本轮诊断取身份**（2026-10-01 复核 P2 的诊断侧）.

两份诊断都是「本轮运行停在某一层」时唯一的产物：
- `step01_plan_contract_invalid.json`（契约层）
- `step01_plan_infeasible.json`（预算层）

读取方（跑批汇总、离线回放）必须能从**这一层**拿到身份，否则只能退回去读缓存 / 提交产物
——那正是复核指出的「混用不同来源的身份」。所以两份都要带 `run_id` /
`written_at` / `plan_contract_version`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import plan_feasibility as pf                       # noqa: E402

_ID_KEYS = ("run_id", "written_at", "plan_contract_version")


def _rep():
    return {"verdict": "infeasible", "reasons": ["超出预算上限"],
            "suggestions": ["重选"], "dialogue_lines": 3, "dialogue_sec": 40.0,
            "dialogue_rejected": [], "dialogue_unrepresentable": [],
            "beats_floor_sec": 24.0, "lower_bound_sec": 120.0,
            "narration_lines": 2, "narration_sec": 30.0,
            "budget": {"min": 60, "max": 90}, "note": "max(对白, 节拍下限)"}


def test_the_infeasible_diagnostic_carries_its_own_identity(tmp_path):
    plan = {"plan_contract_version": 4, "shot_beats": ["x"], "dialogue_lines": []}
    pf._dump(tmp_path, _rep(), plan)
    got = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    for _k in _ID_KEYS:
        assert _k in got, (sorted(got), _k)
    assert got["plan_contract_version"] == 4, got
    assert isinstance(got["run_id"], str)
    # 旁白分账也要在（与契约诊断对齐）
    assert got["narration_lines"] == 2 and got["narration_sec"] == 30.0, got


def test_the_contract_diagnostic_carries_its_own_identity(tmp_path):
    plan = {"plan_contract_version": 4,
            "required_facts": ['甲｜原文证据：“甲”｜承载：B1/画面'],
            "shot_beats": ["节拍：甲｜本镜信息目的：x｜原文依据：“甲”｜载体：画面"],
            "dialogue_lines": [], "narration_lines": []}
    pf.dump_plan_contract_invalid(tmp_path, plan, ["F1 声明……"])
    got = json.loads((tmp_path / "step01_plan_contract_invalid.json")
                     .read_text(encoding="utf-8"))
    for _k in _ID_KEYS:
        assert _k in got, (sorted(got), _k)
    assert got["plan_contract_version"] == 4, got
    # 冻结回放要求整份计划都在（复核第 5 轮 P2-4）
    assert isinstance(got.get("plan"), dict) and got["plan"], got
