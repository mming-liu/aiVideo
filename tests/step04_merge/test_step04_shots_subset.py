"""S19-3：step04 --shots 子集试合并的离线契约测试.

覆盖：step02b 成品门的子集投影与防穿透、旁白跨镜裁剪判定、CLI 严格解析。
不读写生产 output、不调用任何模型。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _sha1(path: Path) -> str:
    import hashlib
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _report(tmp_path: Path, shots, scope_shots, issues=None):
    from shared.quality_gate import QUALITY_RULE_VERSION, ensure_script_revision
    script = {"shots": [{"index": i} for i in shots], "characters": []}
    ensure_script_revision(script)
    outputs = {}
    for idx in shots:
        video = tmp_path / f"shot_{idx}.mp4"
        video.write_bytes(b"x" * 2048)
        outputs[str(idx)] = {"video": {"path": str(video), "sha1": _sha1(video)},
                             "duration_sec": 5.0, "has_audio": False}
    report = {
        "source": "step02b.product_acceptance",
        "report_type": "product_acceptance",
        "rule_version": QUALITY_RULE_VERSION,
        "script_revision": script["script_revision"],
        "status": "PASS",
        "blocking": False,
        "missing": [],
        "outputs": outputs,
        "issues_by_shot": {str(i): [] for i in shots},
        "scope": {"shots": list(scope_shots)},
    }
    return script, report


def test_subset_report_projects_only_requested_shots(tmp_path):
    from shared.quality_gate import require_gate

    script, report = _report(tmp_path, [1, 2], [1])
    path = tmp_path / "step02b_quality.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    projected = require_gate(path, script, stage="step02b",
                             required_report_type="product_acceptance",
                             verify_outputs=True, output_indices={1})
    assert projected["status"] == "PASS"
    assert sorted(projected["outputs"]) == ["1"]


def test_full_chapter_gate_rejects_subset_report(tmp_path):
    """生产序列：整章 require_gate（不投影）+ scope 守卫必须拒收子集报告."""
    from shared.quality_gate import QualityGateError, require_gate
    from step04_merge.main import _assert_step02b_scope

    script, report = _report(tmp_path, [1, 2], [1])
    path = tmp_path / "step02b_quality.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    full = require_gate(path, script, stage="step02b",
                        required_report_type="product_acceptance",
                        verify_outputs=True, output_indices=None)
    with pytest.raises(QualityGateError):
        _assert_step02b_scope(full, None, script)


def test_scope_guard_rejects_uncovered_subset(tmp_path):
    from shared.quality_gate import QualityGateError
    from step04_merge.main import _assert_step02b_scope

    script = {"shots": [{"index": 1}, {"index": 2}], "hook": {"index": 0}}
    report = {"scope": {"shots": [1]}}
    # 子集请求在覆盖范围内 → 放行
    _assert_step02b_scope(report, {1}, script)
    # 整章请求 → 必须拒收
    with pytest.raises(QualityGateError):
        _assert_step02b_scope(report, None, script)
    # 旧报告无 scope → 视为全章，放行
    _assert_step02b_scope({}, None, script)


def test_narration_subset_decisions():
    from step04_merge.main import _narration_subset_decision

    # 完全落在子集内
    assert _narration_subset_decision([1, 2], {1, 2}) == "keep"
    # 属于其他镜头
    assert _narration_subset_decision([5], {1}) == "skip_other"
    # 跨镜旁白只跑其中一镜 → 跳过（禁止截句）
    assert _narration_subset_decision([1, 2], {1}) == "skip_cross"
    # 整章合并不做裁剪
    assert _narration_subset_decision([1, 2], None) == "keep"
    # 无承载信息
    assert _narration_subset_decision([], {1}) == "skip_other"


def test_cli_shots_parsed_and_typo_rejected():
    from step04_merge import cli

    opts = cli.parse_bootstrap(["--shots", "1"])
    assert opts.shots == "1"
    assert opts.enhancement_requested is False   # 不强制 --candidate-root

    with pytest.raises(SystemExit) as exc:
        cli.parse_bootstrap(["--shot", "1"])      # 拼错不得被前缀缩写吞掉
    assert exc.value.code == 2
