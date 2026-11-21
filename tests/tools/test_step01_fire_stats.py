# -*- coding: utf-8 -*-
"""tools/step01_fire_stats.py：冻结清单 → 逐约束 / 问题码触发统计（离线自造夹具）."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import step01_fire_stats as fs  # noqa: E402

NAMES = {"alpha_rule", "beta_rule", "gamma_rule", "delta_rule"}


def _write(root: Path, rel: str, content) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    p.write_text(text, encoding="utf-8")
    return p


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    _write(run, "logs/step01.log", "\n".join([
        "[HARD_FIX] 本轮路径: Constraint 框架",
        "[CONSTRAINT] 修复 7 处 (top: alpha_rule=3, beta_rule=1)",
        "[CONSTRAINT-WARN] beta_rule@shot2（另 2 处）: 样例",
        "[CONSTRAINT-WARN] 合计 3 条告警 / 1 类约束",
        "[CONSTRAINT] HARD 未解决 1 处 (建议 regen): gamma_rule@shot4",
        "[CONSTRAINT] alpha_rule 异常: boom",
    ]))
    _write(run, "chapter_x/step01_quality.json", {
        "hard": [{"code": "code_a", "severity": "hard", "message": "m"}],
        "advisory": [{"code": "code_b", "severity": "advisory", "message": "m"},
                     {"code": "code_b", "severity": "advisory", "message": "m"}],
    })
    _write(run, "chapter_x/step01_repair_rounds.json", [
        {"kind": "call_ok", "repair_round": 1},
        {"repair_round": 1, "groups": [[1]], "accepted": True, "resolved": 2,
         "reason": "", "origin_reason": ""},
        {"repair_round": 2, "groups": [[3]], "accepted": False, "resolved": 0,
         "reason": "new_blocking", "origin_reason": "new_blocking"},
    ])
    return run


def test_freeze_then_stats_counts_each_source(tmp_path):
    run = _run_dir(tmp_path)
    manifest = fs.freeze([run])
    assert {a["kind"] for a in manifest["artifacts"]} == {"log", "quality", "repair_rounds"}
    doc = fs.stats(manifest, base=tmp_path, names=NAMES)
    c = doc["constraints"]
    assert c["alpha_rule"]["log"] == {"repair": 3, "error": 1}
    assert c["beta_rule"]["log"] == {"repair": 1, "warn": 3}
    assert c["gamma_rule"]["log"] == {"hard": 1}
    assert doc["never_fired"] == ["delta_rule"]
    # 7 处里只列出 4 处 → 低频约束可能没上日志
    assert doc["inputs"]["logs_truncation_possible"] == 1
    # 没有带埋点的 metrics：无法区分「没跑」与「跑了没命中」
    assert c["delta_rule"]["observed_ran"] is None
    codes = {(i["code"], i["severity"]): i["count"] for i in doc["issue_codes"]}
    assert codes == {("code_a", "hard"): 1, ("code_b", "advisory"): 2}
    assert doc["repair_rounds"]["decisions"] == 2
    assert doc["repair_rounds"]["accepted"] == 1
    assert doc["repair_rounds"]["resolved"] == 2
    assert doc["repair_rounds"]["reasons"] == {"": 1, "new_blocking": 1}


def test_metrics_node_distinguishes_ran_from_fired(tmp_path):
    run = tmp_path / "run"
    _write(run, "step01_metrics.json", {"constraints": {
        "alpha_rule": {"ran": 2, "repair": 0, "warn": 0, "violation": 0, "error": 0},
        "beta_rule": {"ran": 2, "repair": 0, "warn": 1, "violation": 0, "error": 0},
    }})
    doc = fs.stats(fs.freeze([run]), base=tmp_path, names=NAMES)
    c = doc["constraints"]
    assert c["alpha_rule"]["fired"] is False and c["alpha_rule"]["observed_ran"] is True
    assert c["beta_rule"]["fired"] is True
    assert c["gamma_rule"]["observed_ran"] is False
    assert doc["inputs"]["metrics_with_constraints_node"] == 1


def test_rewritten_file_after_freeze_fails(tmp_path):
    run = _run_dir(tmp_path)
    manifest = fs.freeze([run])
    (run / "logs/step01.log").write_text("changed", encoding="utf-8")
    with pytest.raises(fs.ManifestError, match="sha256"):
        fs.stats(manifest, base=tmp_path, names=NAMES)


def test_missing_file_fails_instead_of_counting_zero(tmp_path):
    run = _run_dir(tmp_path)
    manifest = fs.freeze([run])
    (run / "chapter_x/step01_quality.json").unlink()
    with pytest.raises(fs.ManifestError, match="缺失"):
        fs.stats(manifest, base=tmp_path, names=NAMES)


def test_unparseable_json_fails(tmp_path):
    run = tmp_path / "run"
    _write(run, "step01_quality.json", "{not json")
    with pytest.raises(fs.ManifestError, match="无法解析"):
        fs.stats(fs.freeze([run]), base=tmp_path, names=NAMES)


def test_freeze_rejects_missing_root(tmp_path):
    with pytest.raises(fs.ManifestError, match="根目录不存在"):
        fs.freeze([tmp_path / "nope"])


def test_cli_exit_code_on_bad_manifest(tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text(json.dumps({"schema": "other", "artifacts": []}), encoding="utf-8")
    assert fs.main(["stats", "--manifest", str(bad), "--out", str(tmp_path / "o.json")]) == 2
    assert not (tmp_path / "o.json").exists()
