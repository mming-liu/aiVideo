# -*- coding: utf-8 -*-
"""M05：画面计划必须成为**实际生成参数的唯一来源**（复核 P2）.

此前 `gen_all_frames` 只做 `validate_plan_identity`，随后仍从原稿 `script` 取 `shot`
交给 `gen_shot_frames`——计划验收通过、发送内容却没有采用计划，属于假贯通。
本用例锁住两条：

1. 计划里的执行参数（`shots[].execution.unit`）被**桩生成器实际收到**；
2. 计划与执行参数/参考摘要不一致时，发送前拒收。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

import shared.vimax_bridge as vb
from step02a_frame_gen import main as m
from step02a_frame_gen.execution_plan import build_visual_plan, verify_plan_execution

FIXTURES = Path(__file__).resolve().parents[1] / "shared" / "fixtures" / "execution_contract"


def _family(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _setup(monkeypatch, tmp_path):
    family = _family("temporal_ellipsis")
    script = copy.deepcopy(family["baseline"]["script"])
    script_path = tmp_path / "script_result.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "chapter_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(m, "ImageGeneratorAiVideoAdapter", lambda *a, **k: object())

    seen: list[dict] = []

    async def _stub(shot, shots_dir, image_adapter, script_mtime, **kwargs):
        seen.append(copy.deepcopy(shot))
        return {"ff_path": None, "lf_path": None, "keyframes_paths": []}

    monkeypatch.setattr(vb, "gen_shot_frames", _stub)
    return family, script, script_path, seen


def test_generation_consumes_the_planned_execution_unit(monkeypatch, tmp_path):
    family, script, script_path, seen = _setup(monkeypatch, tmp_path)
    planned_ff = "计划改写：阿岚立于船坞，右手握锤，船底裂缝清晰可见"
    candidate = {"shots": [{"index": 1, "ff_desc": planned_ff}]}

    plan = build_visual_plan(script, scope=family["scope"], only_indices={1},
                             candidate=candidate)
    assert plan["shots"][0]["execution"]["unit"]["ff_desc"] == planned_ff
    assert verify_plan_execution(plan, characters=script.get("characters") or []) == []

    asyncio.run(m.gen_all_frames(script_path, tmp_path / "work",
                                 only_indices={1}, visual_plan=plan,
                                 scope=family["scope"]))

    assert seen, "桩生成器必须被调用"
    assert seen[0]["ff_desc"] == planned_ff, "生成侧必须收到计划里的执行参数"
    # 原稿没被计划改写污染
    assert script["shots"][0]["ff_desc"] != planned_ff


def test_plan_prompt_drift_is_refused_before_send(monkeypatch, tmp_path):
    family, script, script_path, seen = _setup(monkeypatch, tmp_path)
    plan = build_visual_plan(script, scope=family["scope"], only_indices={1})
    plan["shots"][0]["frames"][0]["prompt"] = "被改过的提示"

    issues = verify_plan_execution(plan, characters=script.get("characters") or [])
    assert [i["code"] for i in issues] == ["plan_prompt_drift"], issues

    with pytest.raises(SystemExit):
        asyncio.run(m.gen_all_frames(script_path, tmp_path / "work",
                                     only_indices={1}, visual_plan=plan,
                                     scope=family["scope"]))
    assert seen == [], "计划与执行参数不一致时不得发起生成"


def test_missing_reference_file_is_reported(tmp_path):
    """参考文件在计划之后消失/被替换 → 发送前就能看见（不依赖真实素材库）."""
    missing = tmp_path / "nope.png"
    plan = {"shots": [{"shot_index": 1,
                       "execution": {"unit": {"index": 1, "ff_desc": "x"}},
                       "frames": [{"frame": "ff", "kind": "ff", "prompt": "",
                                   "references": [{"path": str(missing),
                                                   "sha256": "0" * 64,
                                                   "role": "scene"}]}]}]}
    issues = verify_plan_execution(plan, check_reference_files=True)
    codes = [i["code"] for i in issues]
    assert "plan_reference_missing" in codes, issues


def test_plan_without_execution_block_is_refused():
    plan = {"shots": [{"shot_index": 1, "frames": []}]}
    issues = verify_plan_execution(plan)
    assert [i["code"] for i in issues] == ["plan_execution_missing"]
