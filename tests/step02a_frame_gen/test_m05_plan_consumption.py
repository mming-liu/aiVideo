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
from step02a_frame_gen.execution_plan import (
    build_visual_plan,
    verify_plan_changes,
    verify_plan_coverage,
    verify_plan_execution,
)

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
    planned_prompt = "计划改写：船坞全景，阿岚立于船底旁，锤与木屑清晰可见"
    candidate = {"shots": [{"index": 1, "prompt": planned_prompt}]}

    plan = build_visual_plan(script, scope=family["scope"], only_indices={1},
                             candidate=candidate)
    assert plan["shots"][0]["execution"]["unit"]["prompt"] == planned_prompt
    assert verify_plan_execution(plan, characters=script.get("characters") or []) == []
    assert verify_plan_changes(plan, script) == []

    asyncio.run(m.gen_all_frames(script_path, tmp_path / "work",
                                 only_indices={1}, visual_plan=plan,
                                 scope=family["scope"]))

    assert seen, "桩生成器必须被调用"
    assert seen[0]["prompt"] == planned_prompt, "生成侧必须收到计划里的执行参数"
    # 原稿没被计划改写污染
    assert script["shots"][0]["prompt"] != planned_prompt


def test_protected_field_change_is_refused_even_if_plan_is_self_consistent(
        monkeypatch, tmp_path):
    """单元与提示一起改（自洽）也不能绕过上游保护：`ff_desc` 属 Step01 权限."""
    family, script, script_path, seen = _setup(monkeypatch, tmp_path)
    forged = "伪造：把首帧描述也一起改掉"
    plan = build_visual_plan(script, scope=family["scope"], only_indices={1})
    plan["shots"][0]["execution"]["unit"]["ff_desc"] = forged
    # 让计划内部保持自洽：帧提示按改后的单元重算
    from step02a_frame_gen.execution_plan import _frame_prompt
    for frame in plan["shots"][0]["frames"]:
        prompt, _err = _frame_prompt(plan["shots"][0]["execution"]["unit"],
                                     script.get("characters") or [], frame["kind"])
        frame["prompt"] = prompt

    assert verify_plan_execution(plan, characters=script.get("characters") or []) == []
    issues = verify_plan_changes(plan, script)
    # `ff_desc` 属 Step01 语义权限：要么按受保护字段拒、要么按「语义未验证」拒，
    # 两者都是「计划自洽也不能放行」——这里只锁「必须被拒且指向该路径」。
    assert issues, "单元与提示一起改也必须被拒"
    assert {i["path"] for i in issues} == {"shots[0].ff_desc"}, issues
    assert {i["code"] for i in issues} <= {"protected_field_change",
                                           "semantic_change_unverified"}, issues

    with pytest.raises(SystemExit):
        asyncio.run(m.gen_all_frames(script_path, tmp_path / "work",
                                     only_indices={1}, visual_plan=plan,
                                     scope=family["scope"]))
    assert seen == [], "保护字段被改写时不得发起生成"


def test_requested_shot_outside_the_plan_is_refused(monkeypatch, tmp_path):
    """计划只覆盖镜 1，却请求镜 1+2 → 覆盖检查拒收，且零调用（不回退原稿）."""
    family, script, script_path, seen = _setup(monkeypatch, tmp_path)
    plan = build_visual_plan(script, scope=family["scope"], only_indices={1})

    issues = verify_plan_coverage(plan, only_indices={1, 2}, frames=None)
    assert [i["code"] for i in issues] == ["plan_coverage_missing_shot"], issues

    with pytest.raises(SystemExit):
        asyncio.run(m.gen_all_frames(script_path, tmp_path / "work",
                                     only_indices={1, 2}, visual_plan=plan,
                                     scope=family["scope"]))
    assert seen == []


def test_full_run_with_shot_missing_from_plan_is_refused(monkeypatch, tmp_path):
    """全量运行（`only_indices=None`）必须拿完整稿镜头全集比较计划（F1）.

    计划只覆盖镜 1+2，稿件还有镜 3；此前的覆盖检查在 `only_indices=None` 时只
    遍历计划自己的条目，漏镜不报，主入口又把 `only_indices` 收窄为计划镜头，
    于是镜 3 被静默跳过。现在必须在发送前报 `plan_coverage_missing_shot` 且零调用。
    """
    family, script, script_path, seen = _setup(monkeypatch, tmp_path)
    frames = {"ff", "lf"}
    plan = build_visual_plan(script, scope=family["scope"], only_indices={1, 2},
                             frames=frames)

    issues = verify_plan_coverage(plan, only_indices=None, frames=frames, script=script)
    assert [i["code"] for i in issues] == ["plan_coverage_missing_shot"], issues
    assert "镜头 3" in issues[0]["message"], issues

    with pytest.raises(SystemExit):
        asyncio.run(m.gen_all_frames(script_path, tmp_path / "work",
                                     only_indices=None, frames=frames,
                                     visual_plan=plan, scope=family["scope"]))
    assert seen == [], "计划漏镜的全量运行必须零调用拒收"


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


def test_reference_entry_without_path_is_reported(tmp_path):
    """F4：参考条目缺 path 不得静默跳过，必须报 plan_reference_missing."""
    from step02a_frame_gen.execution_plan import _frame_prompt
    unit = {"index": 1, "ff_desc": "x"}
    prompt, _err = _frame_prompt(unit, [], "ff")
    plan = {"shots": [{"shot_index": 1,
                       "execution": {"unit": unit},
                       "frames": [{"frame": "ff", "kind": "ff", "prompt": prompt,
                                   "references": [{"sha256": "0" * 64,
                                                   "role": "scene"}]}]}]}
    issues = verify_plan_execution(plan, check_reference_files=True)
    assert [i["code"] for i in issues] == ["plan_reference_missing"], issues


def test_reference_entry_without_sha_is_reported(tmp_path):
    """F4：参考条目缺 sha256 同样按参考缺失拒收（文件存在也不能跳过）."""
    from step02a_frame_gen.execution_plan import _frame_prompt
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"ref")
    unit = {"index": 1, "ff_desc": "x"}
    prompt, _err = _frame_prompt(unit, [], "ff")
    plan = {"shots": [{"shot_index": 1,
                       "execution": {"unit": unit},
                       "frames": [{"frame": "ff", "kind": "ff", "prompt": prompt,
                                   "references": [{"path": str(ref),
                                                   "role": "scene"}]}]}]}
    issues = verify_plan_execution(plan, check_reference_files=True)
    assert [i["code"] for i in issues] == ["plan_reference_missing"], issues


def test_plan_without_execution_block_is_refused():
    plan = {"shots": [{"shot_index": 1, "frames": []}]}
    issues = verify_plan_execution(plan)
    assert [i["code"] for i in issues] == ["plan_execution_missing"]
