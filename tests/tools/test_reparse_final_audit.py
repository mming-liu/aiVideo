# -*- coding: utf-8 -*-
"""`tools/reparse_final_audit.py` 的回归（夹具驱动，零调用）.

这个脚本把已经落盘的审计原始响应**重新解析**一遍并回写 `step01_audit.json`：
审计结论的 `severity` 映射（`blocking` → `visual_risk`，其余 `advisory`）与
`status`（`ok` / `parse_failed`）就是由它盖章的。解析口径错一格，历史审计的
阻塞数会被静默改写——所以它自己必须有用例。

原实现把目标路径写死成"遍历真实 `output/video/*/part_01_*/chapter_001`"，
跑一次就改一次真实产物，无法回归。修复后 `--path` / `--chapter-dir` 可指定目标，
夹具是一份最小的审计响应（协议字段，不含作品内容）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reparse_final_audit as tool                       # noqa: E402


def _payload(issues) -> dict:
    return {"schema": "step01_audit.v1", "status": "pending",
            "raw_response": json.dumps({"issues": issues}, ensure_ascii=False),
            "issues": [], "error": ""}


def _write(path: Path, issues) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_payload(issues), ensure_ascii=False), encoding="utf-8")
    return path


ISSUES = [
    {"code": "AUDIO_CONTINUITY", "severity": "blocking", "shot_id": 3, "frame_id": "ff",
     "impact": "blocking：音效与画面乐器不一致", "minimal_fix": "把笛声改成箫声",
     "evidence": "音效写笛声，画面持箫"},
    {"code": "PROP_CHAIN", "severity": "warning", "shot_id": 5, "frame_id": "kf2",
     "impact": "道具在中途消失", "minimal_fix": "补回道具", "evidence": "kf1 有、kf2 无"},
]


def test_parses_and_rewrites_in_place(tmp_path):
    path = _write(tmp_path / "step01_audit.json", ISSUES)
    assert tool.main(["--path", str(path)]) == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert len(payload["issues"]) == 2
    first = payload["issues"][0]
    assert first["severity"] == "visual_risk"
    assert first["code"] == "model_audit_AUDIO_CONTINUITY"
    assert first["shot_id"] == "3"
    assert first["repairable"] is True


def test_warning_maps_to_advisory(tmp_path, capsys):
    path = _write(tmp_path / "step01_audit.json", ISSUES)
    tool.main(["--path", str(path)])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["issues"][1]["severity"] == "advisory"


def test_impact_prefix_promotes_to_visual_risk(tmp_path):
    """`severity` 写 warning、但 impact 以 blocking 开头 —— 仍按阻塞算。"""
    issues = [{"code": "X", "severity": "warning", "shot_id": 1,
               "impact": "Blocking：结构性缺陷", "minimal_fix": "-", "evidence": "-"}]
    path = _write(tmp_path / "step01_audit.json", issues)
    assert tool.main(["--path", str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["issues"][0]["severity"] == "visual_risk"


def test_chapter_dir_resolves_the_standard_filename(tmp_path):
    path = _write(tmp_path / "chapter_001" / "step01_audit.json", ISSUES)
    assert tool.main(["--chapter-dir", str(path.parent)]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "ok"


def test_parse_failure_exits_2_and_clears_issues(tmp_path):
    """坏响应不能被当成「审计通过」：status=parse_failed + exit 2。"""
    path = tmp_path / "step01_audit.json"
    path.write_text(json.dumps({"raw_response": "这不是 JSON"}), encoding="utf-8")
    assert tool.main(["--path", str(path)]) == 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "parse_failed"
    assert payload["issues"] == []


def test_shape_violation_is_parse_failed(tmp_path):
    """`severity` 不在 blocking/warning/info 里 ⇒ 整份拒收（不是"零问题"）。"""
    path = tmp_path / "step01_audit.json"
    path.write_text(json.dumps({
        "raw_response": json.dumps({"issues": [{"code": "X", "severity": "fatal",
                                               "impact": "?"}]})}), encoding="utf-8")
    assert tool.main(["--path", str(path)]) == 2


def test_empty_issue_list_is_ok_not_failure(tmp_path):
    path = _write(tmp_path / "step01_audit.json", [])
    assert tool.main(["--path", str(path)]) == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok" and payload["issues"] == []


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        tool.main(["--path", str(tmp_path / "nope.json")])


def test_resolve_target_prefers_explicit_path(tmp_path):
    explicit = tmp_path / "a.json"
    assert tool.resolve_target(str(explicit), str(tmp_path)) == explicit
    assert tool.resolve_target("", str(tmp_path)) == tmp_path / "step01_audit.json"
