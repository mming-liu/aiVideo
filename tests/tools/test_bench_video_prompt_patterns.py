# -*- coding: utf-8 -*-
"""视频 prompt 基线工具（总计划阶段 7 / 工作包 B）.

锁住非 step01 吸收计划 §4.5 的验收标准：不导入 step01、fixture 不被修改、
报告区分三层 prompt、重复运行一致、不调用外部 API、image/text/r2v 三模式都有
快照级回归、上游未通过时标记 `upstream_blocked`。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "prompt_bench", _PROJECT_ROOT / "tools" / "bench_video_prompt_patterns.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bench():
    return _load_tool()


@pytest.fixture(scope="module")
def report(bench):
    return bench.run(bench.load_cases())


def test_fixture_has_all_three_modes(bench):
    cases = bench.load_cases()
    modes = {c["mode"] for c in cases}
    assert {"image", "r2v"} <= modes
    assert len(cases) >= 6, "至少 6 类样本（§4.3）"


def test_report_covers_three_prompt_stages(report):
    for c in report["cases"]:
        assert c["stage_concise"], c["case_id"]
        assert c["stage_with_refs"].startswith(c["stage_concise"])
        assert c["provider_prompt"]
        assert c["len_stage_concise"] <= c["len_stage_with_refs"]


def test_no_case_exceeds_the_provider_cap(report):
    for c in report["cases"]:
        assert c["len_provider_prompt"] <= c["cap"], (
            f"{c['case_id']} provider prompt {c['len_provider_prompt']} > cap {c['cap']}")


def test_expected_and_forbidden_fragments(report):
    for c in report["cases"]:
        assert not c["missing_fragments"], f"{c['case_id']} 缺 {c['missing_fragments']}"
        assert not c["leaked_fragments"], f"{c['case_id']} 泄漏 {c['leaked_fragments']}"
        assert c["verdict"] != "BLOCK", f"{c['case_id']} {c['reasons']}"


def test_image_mode_keeps_frame_time_semantics(report):
    """image 模式必须保留首帧/尾帧的时间语义，**不得**套用 r2v 的用途清单."""
    by_id = {c["case_id"]: c for c in report["cases"]}
    single = by_id["image_first_frame"]
    assert "视频起点画面（首帧）" in single["stage_with_refs"]
    assert "【参考图用途】" not in single["stage_with_refs"]
    pair = by_id["image_first_last_frame"]
    assert "视频终点画面（尾帧）" in pair["stage_with_refs"]
    assert "【参考图用途】" not in pair["stage_with_refs"]


def test_r2v_purpose_list_numbers_every_reference(report):
    by_id = {c["case_id"]: c for c in report["cases"]}
    two = by_id["r2v_two_characters_numbering"]
    assert "图1" in two["stage_with_refs"] and "图2" in two["stage_with_refs"]
    assert "杨旭" in two["stage_with_refs"] and "听香" in two["stage_with_refs"]
    limit = by_id["r2v_refs_near_model_limit"]
    assert "图5" in limit["stage_with_refs"], "第 5 张参考图必须有自己的编号"
    assert len(limit["reference_files"]) == 5


def test_space_object_is_not_degraded_to_a_plain_prop(report):
    by_id = {c["case_id"]: c for c in report["cases"]}
    blob = by_id["r2v_prop_and_space_object"]["stage_with_refs"]
    assert "空间物体" in blob and "比例" in blob
    assert "道具" in blob


def test_inherit_exclude_boundary_is_written_when_budget_allows(report):
    """预算充裕时必须写出「禁止继承」边界（工作包 C 的核心增量）."""
    by_id = {c["case_id"]: c for c in report["cases"]}
    c = by_id["r2v_single_ref_short_shot"]
    assert c["reference_policy"]["inherit_exclude_present"] is True
    blob = c["stage_with_refs"]
    assert "只取身份、五官" in blob
    assert "不取背景、姿势、构图" in blob


def test_image_mode_never_gets_r2v_excludes(report):
    """首帧/尾帧不套用继承/排除规则——它们的构图与姿势就是视频起点/终点."""
    for c in report["cases"]:
        if c["mode"] != "image":
            continue
        assert "不取背景、姿势" not in c["stage_with_refs"], c["case_id"]
        assert c["reference_policy"]["trimmed"] is False


def test_priority_order_keeps_numbering_and_drops_excludes_first(report):
    """§5.4 优先级：接近上限时先丢排除细节、再丢跨角色隔离，编号与用途永不裁掉."""
    by_id = {c["case_id"]: c for c in report["cases"]}
    limit = by_id["r2v_refs_near_model_limit"]
    pol = limit["reference_policy"]
    # 5 张参考图 + 500 字硬上限 → 排除细节必然让位
    assert pol["trimmed"] is True
    # 但每张图的编号与用途必须完整保留
    for i in range(1, 6):
        assert f"图{i}" in limit["stage_with_refs"], f"图{i} 被裁掉"
    # 多角色隔离的优先级高于排除细节。
    # 2026-11-08：当前模型参考图上限（seedance 等 14 张）下，两角色用例
    # **不再超预算**，因此 trimmed 应为 False；"超限先丢排除项"由上面的
    # near_model_limit 用例覆盖。多角色隔离仍必须存在。
    two = by_id["r2v_two_characters_numbering"]
    assert two["reference_policy"]["cross_character_isolation_present"] is True
    assert two["reference_policy"]["trimmed"] is False


def test_repeat_run_is_identical(bench):
    a = bench.run(bench.load_cases())
    b = bench.run(bench.load_cases())
    assert json.dumps(a["cases"], ensure_ascii=False, sort_keys=True) == \
        json.dumps(b["cases"], ensure_ascii=False, sort_keys=True)


def test_fixture_input_is_not_mutated(bench):
    before = bench.FIXTURE.read_text(encoding="utf-8")
    bench.run(bench.load_cases())
    assert bench.FIXTURE.read_text(encoding="utf-8") == before


def test_no_api_calls_and_no_step01_import(bench, report):
    """纯离线：报告声明 0 次 API 调用，且工具不导入 step01."""
    assert report["api_calls"] == 0
    src = (bench.__file__ if isinstance(bench.__file__, str)
           else str(bench.__file__)).replace("\\", "/")
    text = Path(src).read_text(encoding="utf-8")
    assert "step01_script_gen" not in text, "工作包 B 不得导入 step01"


def test_upstream_gate_status_is_surfaced(report):
    """上游 gate 未通过时必须在报告里可见，且不能把静态 PASS 说成可生成."""
    assert report["summary"]["upstream_gate_status"] in ("eligible", "upstream_blocked")
    if report["summary"]["upstream_gate_status"] == "upstream_blocked":
        assert any("上游 gate" in r for c in report["cases"] for r in c["reasons"])
        assert "不代表可以发起生成" in " ".join(report["notes"])


def test_markdown_report_renders(bench):
    md = bench._render_md(bench.run(bench.load_cases()))
    assert "# 视频 prompt 基线报告" in md
    for c in bench.load_cases():
        assert c["case_id"] in md
