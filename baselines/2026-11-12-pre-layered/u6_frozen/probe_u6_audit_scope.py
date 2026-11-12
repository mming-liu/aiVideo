# -*- coding: utf-8 -*-
"""U6 零调用补验：审计输入范围 + 增量降级复现 + 发送控制事实.

不调用任何模型；只读冻结稿/旧审计/旧轮次台账，调用生产同源纯函数。
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from step01_script_gen.audit import build_audit_prompt  # noqa: E402
from step01_script_gen import main as m  # noqa: E402
from shared.quality_gate import compute_script_revision  # noqa: E402

BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
CAND = BASE / "candidate_d1a" / "candidate.json"
NOVEL = BASE / "u6_inputs" / "novel_chapter_001.txt"
QUALITY = (BASE / "iso" / "output" / "锦衣夜行" / "part_01_杀青州"
           / "chapter_001" / "step01_quality.json")
FORMAL = (ROOT / "output" / "锦衣夜行" / "part_01_杀青州"
          / "chapter_001")
OLD_AUDIT = FORMAL / "step01_audit.json"
OLD_ROUNDS = FORMAL / "step01_audit_rounds.json"
OLD_SCRIPT = FORMAL / "script_result.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _issue_row(issue: dict) -> dict:
    return {k: issue.get(k) for k in
            ("shot_id", "severity", "code", "field_path",
             "out_of_delta_scope", "_downgraded_locked_only",
             "_downgraded_stage_enum", "repair_targets")}


def main() -> int:
    cand = _load(CAND)
    novel = NOVEL.read_text(encoding="utf-8")
    quality = _load(QUALITY)
    projected, errors = m.project_for_commit(cand, novel, label="u6-scope-probe")
    revision = compute_script_revision(projected)
    focus = m._final_audit_focus(projected, quality)
    metadata = m._final_audit_metadata(projected, run_id="u6-scope-probe")
    prompt = build_audit_prompt(novel, projected.get("chapter_outline"), projected,
                                audit_focus=focus, metadata=metadata)
    idx_in_prompt = sorted({int(x) for x in re.findall(r'"index":\s*(\d+)', prompt)})
    all_idx = sorted(int(s.get("index")) for s in projected.get("shots") or [])

    old_rounds = _load(OLD_ROUNDS)
    last = old_rounds[-1]
    pre = copy.deepcopy(last.get("issues") or [])
    formal = _load(OLD_AUDIT)

    def run_downgrades(scope, script):
        work = copy.deepcopy(pre)
        payload = {"issues": work}
        n1 = m._downgrade_locked_only_audit_issues(payload, contracts_ok=True)
        n2 = m._downgrade_stage_enum_false_positives(payload, script, contracts_ok=True)
        n3 = m._downgrade_out_of_delta_audit_issues(payload, set(scope),
                                                    contracts_ok=True)
        return {"scope": sorted(scope), "locked_only": n1, "stage_enum": n2,
                "out_of_delta": n3,
                "issues": [_issue_row(i) for i in work]}

    old_script = _load(OLD_SCRIPT)
    actual_scope = {"1", "2", "3", "5", "10", "11", "12"}   # 2026-11-09 07:52:59 日志
    u6_scope = {"1", "8"}
    result = {
        "candidate": {
            "path": str(CAND),
            "revision_projected": revision,
            "projection_errors": errors,
            "quality_revision": quality.get("script_revision"),
            "quality_status": quality.get("status"),
            "quality_fingerprint": quality.get("rule_source_fingerprint"),
            "shot_count": len(projected.get("shots") or []),
        },
        "audit_input_scope": {
            "constant": m.STEP01_AUDIT_SCOPE,
            "prompt_chars": len(prompt),
            "shot_indices_in_prompt": idx_in_prompt,
            "all_shot_indices": all_idx,
            "all_shots_present": idx_in_prompt == all_idx,
            "novel_chars": len(novel),
            "novel_truncated_at": 12000,
            "compact_fields": ["index", "duration_sec", "main_subject", "ff_desc",
                               "lf_desc", "motion_desc", "prompt", "narration_segment",
                               "frame_layouts", "keyframes"],
            "audit_focus": focus,
            "metadata": {k: metadata.get(k) for k in
                         ("input_revision", "rule_source_fingerprint",
                          "audit_scope", "audit_prompt_version",
                          "audit_focus_version", "duration_band")},
        },
        "old_audit": {
            "path": str(OLD_AUDIT),
            "status": formal.get("status"),
            "input_revision": formal.get("input_revision"),
            "rule_source_fingerprint": formal.get("rule_source_fingerprint"),
            "audit_prompt_version": formal.get("audit_prompt_version"),
            "audit_focus_version": formal.get("audit_focus_version"),
            "created_at": formal.get("created_at"),
            "stale": (formal.get("rule_source_fingerprint")
                      != quality.get("rule_source_fingerprint")
                      or formal.get("input_revision") != revision),
            "issues_formal": [_issue_row(i) for i in formal.get("issues") or []],
        },
        "old_round_pre_downgrade": {
            "recorded_at": last.get("recorded_at"),
            "run_id": last.get("run_id"),
            "input_revision": last.get("input_revision"),
            "rule_source_fingerprint": last.get("rule_source_fingerprint"),
            "issue_count": last.get("issue_count"),
            "blocking_count": last.get("blocking_count"),
            "issues": [_issue_row(i) for i in pre],
        },
        "downgrade_replay": {
            "actual_2026_11_09_scope": run_downgrades(actual_scope, old_script),
            "u6_adoption_scope": run_downgrades(u6_scope, old_script),
            "note": ("复现 2026-11-09 07:56:57 的生产降级：shot 8 的 visual_risk 因落在"
                     "增量作用域之外被改为 warning；同一份问题在 U6 的 {1,8} 作用域下"
                     "不会被降级。旧报告整体已 stale，不能复用；此复现只证明降级机制。"),
        },
    }
    out = BASE / "u6_audit_scope_and_downgrade.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
