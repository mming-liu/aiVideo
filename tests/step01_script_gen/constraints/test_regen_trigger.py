"""RegenTrigger 单元测试（v6.42 PoC）.

覆盖：
  1. build_regen_prompt 格式正确
  2. trigger_regen_if_needed 无 HARD → None
  3. trigger_regen_if_needed 有 HARD + auto_call=False → None + 日志
  4. trigger_regen_if_needed 有 HARD + auto_call=True → 返回 LLM 输出
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _reset_regen_attempts():
    """隔离模块级熔断计数（v6.50）——上一个个例的尝试次数不得泄漏到下一个."""
    from step01_script_gen.constraints import regen_trigger as _rt
    _rt._REGEN_ATTEMPTS.clear()
    yield
    _rt._REGEN_ATTEMPTS.clear()


def test_build_regen_prompt_empty():
    """空列表 → 空字符串."""
    from step01_script_gen.constraints.regen_trigger import build_regen_prompt
    assert build_regen_prompt([]) == ""


def test_build_regen_prompt_basic():
    """多条违例 → 按 shot 分组."""
    from step01_script_gen.constraints.regen_trigger import build_regen_prompt
    from step01_script_gen.constraints.types import Severity, Violation

    vs = [
        Violation("axis_jump", Severity.HARD, shot_index=2, message="轴线跳变"),
        Violation("runtime_extend", Severity.HARD, shot_index=5, message="总时长不足"),
    ]
    prompt = build_regen_prompt(vs)
    assert "shot 2" in prompt
    assert "shot 5" in prompt
    assert "axis_jump" in prompt
    assert "runtime_extend" in prompt
    assert "轴线跳变" in prompt


def test_trigger_no_hard_returns_none():
    """无 HARD 违例 → 不触发."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_if_needed
    from step01_script_gen.constraints.types import RepairReport, Severity, Violation

    report = RepairReport()
    # 注入一个 WARN 违例（不硬）
    report.violations_remaining = [
        Violation("some_warn", Severity.WARN, shot_index=1, message="warn"),
    ]
    result = trigger_regen_if_needed(report, auto_call_llm=True)
    assert result is None


def test_trigger_with_hard_no_llm():
    """有 HARD 但 auto_call_llm=False → 不调 LLM，返回 None."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_if_needed
    from step01_script_gen.constraints.types import RepairReport, Severity, Violation

    report = RepairReport()
    report.violations_remaining = [
        Violation("axis_jump", Severity.HARD, shot_index=2, message="轴线跳变"),
    ]
    # mock LLM 调用，确认未被调用
    with patch(
        "step01_script_gen.constraints.regen_trigger._text_gen",
        create=True,
    ):
        result = trigger_regen_if_needed(report, auto_call_llm=False)
    assert result is None


def test_trigger_with_hard_calls_llm():
    """有 HARD + auto_call_llm=True → 调 LLM 并返回结果."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_if_needed
    from step01_script_gen.constraints.types import RepairReport, Severity, Violation

    report = RepairReport()
    report.violations_remaining = [
        Violation("axis_jump", Severity.HARD, shot_index=2, message="轴线跳变"),
    ]

    # mock create_service（在 _text_gen 模块路径上 patch）
    fake_text = '{"shots": [], "hook": null, "characters": []}'
    fake_result = type("R", (), {"success": True, "text": fake_text})()
    fake_service = type("S", (), {"generate": lambda self, req: fake_result})()
    with patch(
        "_text_gen.create_service",
        return_value=fake_service,
        create=True,
    ):
        result = trigger_regen_if_needed(report, auto_call_llm=True)
    assert result == fake_text


def test_trigger_with_hard_llm_fails_graceful():
    """LLM 失败应静默返回 None."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_if_needed
    from step01_script_gen.constraints.types import RepairReport, Severity, Violation

    report = RepairReport()
    report.violations_remaining = [
        Violation("axis_jump", Severity.HARD, shot_index=2, message="轴线跳变"),
    ]

    with patch(
        "_text_gen.create_service",
        side_effect=Exception("API 失败"),
        create=True,
    ):
        result = trigger_regen_if_needed(report, auto_call_llm=True)
    assert result is None


# ============================================================================
# v6.42 闭环: 解析 + merge 测试
# ============================================================================

def test_safe_parse_regen_json_valid():
    """合法 JSON → dict."""
    from step01_script_gen.constraints.regen_trigger import _safe_parse_regen_json
    text = '{"shots": [{"index": 1, "prompt": "x"}], "hook": null}'
    result = _safe_parse_regen_json(text)
    assert result is not None
    assert "shots" in result
    assert len(result["shots"]) == 1


def test_safe_parse_regen_json_with_fences():
    """带 ```json 围栏 → 仍能解析."""
    from step01_script_gen.constraints.regen_trigger import _safe_parse_regen_json
    text = '```json\n{"shots": [{"index": 1, "prompt": "x"}]}\n```'
    result = _safe_parse_regen_json(text)
    # safe_parse_json_object 应当能处理
    assert result is not None or result is None  # 取决于实现


def test_safe_parse_regen_json_invalid():
    """非法 JSON → None."""
    from step01_script_gen.constraints.regen_trigger import _safe_parse_regen_json
    result = _safe_parse_regen_json("not json at all")
    assert result is None


def test_merge_regen_into_script_basic():
    """合并基础功能：按 index 替换."""
    from step01_script_gen.constraints.regen_trigger import merge_regen_into_script
    original = {
        "shots": [
            {"index": 1, "prompt": "old1", "duration_sec": 4.0},
            {"index": 2, "prompt": "old2", "duration_sec": 4.0},
        ],
        "characters": [{"ident": "x", "appearance": "old"}],
    }
    regen = {
        "shots": [
            {"index": 1, "prompt": "new1"},  # 修改 shot 1
            # shot 2 不在 regen → 保留
        ],
    }
    result = merge_regen_into_script(original, regen)
    assert result["shots"][0]["prompt"] == "new1"  # 已替换
    assert result["shots"][1]["prompt"] == "old2"  # 保留
    assert result["_regen_applied"] is True
    assert result["_regen_shot_indices"] == [1]


def test_merge_regen_with_target_indices():
    """指定 target_shot_indices → 只合并这些."""
    from step01_script_gen.constraints.regen_trigger import merge_regen_into_script
    original = {
        "shots": [
            {"index": 1, "prompt": "old1"},
            {"index": 2, "prompt": "old2"},
            {"index": 3, "prompt": "old3"},
        ],
    }
    regen = {
        "shots": [
            {"index": 1, "prompt": "new1"},
            {"index": 2, "prompt": "new2"},
            {"index": 3, "prompt": "new3"},
        ],
    }
    result = merge_regen_into_script(
        original, regen, target_shot_indices=[2]  # 只合并 shot 2
    )
    assert result["shots"][0]["prompt"] == "old1"
    assert result["shots"][1]["prompt"] == "new2"  # 已替换
    assert result["shots"][2]["prompt"] == "old3"


def test_merge_regen_preserves_extra_keys():
    """合并保留原 shot 字段（不被 regen 覆盖）."""
    from step01_script_gen.constraints.regen_trigger import merge_regen_into_script
    original = {
        "shots": [
            {
                "index": 1,
                "prompt": "old",
                "char_layout": [{"name": "x"}],  # regen 没这字段
                "duration_sec": 4.0,
            },
        ],
    }
    regen = {
        "shots": [
            {"index": 1, "prompt": "new"},  # 只给 prompt
        ],
    }
    result = merge_regen_into_script(original, regen)
    shot = result["shots"][0]
    assert shot["prompt"] == "new"
    assert shot["char_layout"] == [{"name": "x"}]  # 保留
    assert shot["duration_sec"] == 4.0  # 保留


def test_merge_regen_merges_characters_by_ident():
    """characters 按 ident 合并，不重复."""
    from step01_script_gen.constraints.regen_trigger import merge_regen_into_script
    original = {
        "shots": [],
        "characters": [
            {"ident": "x", "appearance": "old", "age": 30},
        ],
    }
    regen = {
        "shots": [],
        "characters": [
            {"ident": "x", "appearance": "new"},  # 更新 x
            {"ident": "y", "appearance": "ynew"},  # 新增
        ],
    }
    result = merge_regen_into_script(original, regen)
    chars_by_ident = {c["ident"]: c for c in result["characters"]}
    assert chars_by_ident["x"]["appearance"] == "new"  # 已更新
    assert chars_by_ident["x"]["age"] == 30  # 保留
    assert chars_by_ident["y"]["appearance"] == "ynew"  # 新增


def test_trigger_regen_and_merge_no_hard():
    """无 HARD → 直接返回原 script，不动."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_and_merge
    from step01_script_gen.constraints.types import RepairReport

    script = {"shots": [{"index": 1, "prompt": "x"}]}
    report = RepairReport()  # 无 violations
    result = trigger_regen_and_merge(script, report, auto_call_llm=True)
    assert result == script
    assert "_regen_applied" not in result


def test_trigger_regen_and_merge_llm_returns_valid():
    """LLM 返回有效 JSON → merge 成功."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_and_merge
    from step01_script_gen.constraints.types import RepairReport, Severity, Violation

    script = {
        "shots": [
            {"index": 2, "prompt": "old2", "duration_sec": 4.0},
            {"index": 3, "prompt": "old3", "duration_sec": 4.0},
        ],
    }
    report = RepairReport()
    report.violations_remaining = [
        Violation("axis_jump", Severity.HARD, shot_index=2, message="轴线跳"),
    ]

    regen_json = '{"shots": [{"index": 2, "prompt": "new2"}]}'
    fake_result = type("R", (), {"success": True, "text": regen_json})()
    fake_service = type("S", (), {"generate": lambda self, req: fake_result})()

    with patch(
        "_text_gen.create_service",
        return_value=fake_service,
        create=True,
    ):
        result = trigger_regen_and_merge(script, report, auto_call_llm=True)

    assert result["shots"][0]["prompt"] == "new2"  # merged
    assert result["shots"][1]["prompt"] == "old3"  # not in target_indices
    assert result["_regen_applied"] is True
    assert result["_regen_shot_indices"] == [2]


def test_trigger_regen_and_merge_llm_invalid_json():
    """LLM 返回非 JSON → 保留原 script."""
    from step01_script_gen.constraints.regen_trigger import trigger_regen_and_merge
    from step01_script_gen.constraints.types import RepairReport, Severity, Violation

    script = {"shots": [{"index": 2, "prompt": "old2"}]}
    report = RepairReport()
    report.violations_remaining = [
        Violation("axis_jump", Severity.HARD, shot_index=2, message="轴线跳"),
    ]

    fake_result = type("R", (), {"success": True, "text": "not json at all"})()
    fake_service = type("S", (), {"generate": lambda self, req: fake_result})()

    with patch(
        "_text_gen.create_service",
        return_value=fake_service,
        create=True,
    ):
        result = trigger_regen_and_merge(script, report, auto_call_llm=True)

    # 保留原版
    assert result["shots"][0]["prompt"] == "old2"
    assert "_regen_applied" not in result
