"""S06 阶段二：U6 候选 1 次受限修订 + 1 次终局审计（真实调用，隔离输出）.

- 输入：U6 冻结候选（revision 84ff4112…）+ 冻结原文 + 冻结审计（10 条）
- 修订：`_repair_shot_warnings(max_rounds=1)`，只修审计点名的字段（授权面由既有逻辑推导）
- 终局：`_run_final_semantic_audit`（新契约，1 次发送）
- 输出：`.pytest_tmp/s06/phase2/<ts>/`（OUTPUT_BASE 隔离），**不触碰生产 output/**
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
S06 = ROOT / ".pytest_tmp/s06"
U6 = ROOT / ".pytest_tmp/u1_u5_closeout_20261110"
CAND = U6 / "candidate_d1a/candidate.json"
BASE = U6 / "u6_package/run_s02_20261111/output/锦衣夜行/part_01_杀青州/chapter_001"
NOVEL = BASE / "_resume/novel_aba1adc22826.txt"
AUDIT = BASE / "step01_audit.blocked.json"

TS = time.strftime("%Y%m%dT%H%M%S")
OUT_ROOT = S06 / "phase2" / TS
os.environ["OUTPUT_BASE"] = str(OUT_ROOT / "output")
os.environ.setdefault("STEP01_LOG_PATH", str(OUT_ROOT / "step01.log"))
sys.path.insert(0, str(ROOT))
OUT_ROOT.mkdir(parents=True, exist_ok=True)

import step01_script_gen.main as m  # noqa: E402  必须在 OUTPUT_BASE 设定之后

candidate = json.loads(CAND.read_text(encoding="utf-8"))
novel_text = NOVEL.read_text(encoding="utf-8")
audit_payload = json.loads(AUDIT.read_text(encoding="utf-8"))

# S01 语义裁决为「确认缺陷」的 7 条（按 code + shot + field 精确定位）：
#   #2 酒壶回手 / #4 首帧结果泄漏 / #5 大特写 / #6 重复文本 /
#   #8 掌掴前披头散发 / #9 酒壶凭空提起 / #10 刺客握刃
CONFIRMED = {
    ("model_audit_PROP_CONTINUITY", "7", "lf_desc"),
    ("model_audit_FF_RESULT_LEAK", "11", "ff_desc"),
    ("model_audit_FIELD_CONFLICT", "1", "prompt"),
    ("model_audit_TEXT_REPETITION", "6", "prompt"),
    ("model_audit_STATE_CHAIN_CONFLICT", "10", "ff_desc"),
    ("model_audit_PROP_CONTINUITY", "4", "ff_desc"),
    ("model_audit_PROP_CONTINUITY", "7", "ff_desc"),
}
_selected = [r for r in (audit_payload.get("issues") or [])
             if (str(r.get("code")), str(r.get("shot_id")),
                 str(r.get("field_path"))) in CONFIRMED]
# 以「确认缺陷」身份进修订：severity 升到 visual_risk 只影响本轮修复的取舍，
# 不改写审计原始产物（原始 payload 原样保留在 u6_package 下）。
issues = m._audit_payload_to_issues({"issues": [dict(r, severity="visual_risk")
                                                for r in _selected]})

print(json.dumps({
    "chapter_dir": str(m.CHAPTER_DIR),
    "issues": len(issues),
    "selected": [[r.get("code"), r.get("shot_id"), r.get("field_path")]
                 for r in _selected],
    "candidate_revision": audit_payload.get("input_revision"),
    "dry": bool(os.getenv("S06_DRY")),
}, ensure_ascii=False, indent=1))
if os.getenv("S06_DRY"):
    raise SystemExit(0)

diag: list = []
block: list = []
t0 = time.time()
repaired = m._repair_shot_warnings(candidate, novel_text, audit_issues=issues,
                                   max_rounds=1, repair_diag=diag,
                                   block_reasons=block)
repair_sec = round(time.time() - t0, 1)
(OUT_ROOT / "repair_diag.json").write_text(
    json.dumps({"diag": diag, "block": block}, ensure_ascii=False, indent=2),
    encoding="utf-8")
(OUT_ROOT / "repaired_script.json").write_text(
    json.dumps(repaired, ensure_ascii=False, indent=2), encoding="utf-8")
print("[S06] repair done in %ss, diag rows=%d" % (repair_sec, len(diag)))

t1 = time.time()
focus = m._final_audit_focus(repaired, None)
final = m._run_final_semantic_audit(repaired, novel_text, None,
                                    model=None, focus=focus, run_id="s06-phase2")
audit_sec = round(time.time() - t1, 1)
(OUT_ROOT / "final_audit.json").write_text(
    json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
summary = {
    "run_dir": str(OUT_ROOT),
    "candidate_revision": audit_payload.get("input_revision"),
    "repaired_revision": final.get("input_revision"),
    "repair_sec": repair_sec,
    "audit_sec": audit_sec,
    "final_status": final.get("status"),
    "final_issues": len(final.get("issues") or []),
    "final_blocking": sum(1 for i in (final.get("issues") or [])
                          if str(i.get("severity")) in ("visual_risk", "blocking", "hard")),
    "usage": final.get("usage"),
    "audit_prompt_version": final.get("audit_prompt_version"),
}
(OUT_ROOT / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
