# -*- coding: utf-8 -*-
"""S01a 后验：逐镜身份对比 + L1/L3 复核（零调用）."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(r"C:/myFloders/git/aiVideo")
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
ISO_CH = BASE / "iso" / "output"
os.environ["OUTPUT_BASE"] = str(ISO_CH)
os.environ["HOOK_ENABLED"] = "0"
for _k, _v in (("DURATION_MIN_SEC", "60"), ("DURATION_MAX_SEC", "90"),
               ("DURATION_TARGET_MIN_SEC", "70"), ("DURATION_TARGET_MAX_SEC", "80")):
    os.environ.setdefault(_k, _v)
sys.path.insert(0, str(ROOT))

from step01_script_gen.candidate_edit import object_digest       # noqa: E402

NEW = BASE / "candidate_d1a" / "candidate.json"
OLD = BASE / "candidate_d1a" / "candidate.pre_s01a.json"
NOVEL = BASE / "u6_inputs" / "novel_chapter_001.txt"

new = json.loads(NEW.read_text(encoding="utf-8"))
old = json.loads(OLD.read_text(encoding="utf-8"))
novel = NOVEL.read_text(encoding="utf-8")

shot_deltas = []
for a, b in zip(old["shots"], new["shots"]):
    if object_digest(a) != object_digest(b):
        shot_deltas.append({"index": a.get("index"),
                            "changed_keys": sorted(k for k in set(a) | set(b)
                                                   if a.get(k) != b.get(k))})

# ---- L1 结构 ------------------------------------------------------------
l1 = {}
try:
    import scene_compliance
    res = scene_compliance.validate_script(new)
    summary = res.get("summary") or {}
    l1 = {"pass": bool(res.get("pass")),
          "fail_count": summary.get("fail_count"),
          "warn_count": summary.get("warn_count"),
          "fails": [{"index": f.get("index"), "checks": f.get("checks")}
                    for f in (res.get("failures") or [])][:12]}
except Exception as exc:                                            # noqa: BLE001
    l1 = {"error": f"{type(exc).__name__}: {exc}"}

# ---- L3 跨字段 ----------------------------------------------------------
l3 = {}
try:
    from step01_script_gen.cross_field_checks import cross_field_issues
    issues, failures = cross_field_issues(new)
    from collections import Counter
    l3 = {"issue_count": len(issues),
          "failure_count": len(failures),
          "by_code": dict(Counter(i.get("code") for i in issues)),
          "failures": [{"code": f.get("code"), "shot_id": f.get("shot_id"),
                        "message": str(f.get("message"))[:160]} for f in failures[:12]],
          "shot8_issues": [{"code": i.get("code"), "message": str(i.get("message"))[:200]}
                           for i in issues if str(i.get("shot_id")) == "8"]}
except Exception as exc:                                            # noqa: BLE001
    l3 = {"error": f"{type(exc).__name__}: {exc}"}

report = {
    "schema": "s01a_verify.v1",
    "shot_count": {"old": len(old["shots"]), "new": len(new["shots"])},
    "shot_object_digest_deltas": shot_deltas,
    "unchanged_shots": len(new["shots"]) - len(shot_deltas),
    "revision_new": str(new.get("script_revision") or "")[:16],
    "L1_structure": l1,
    "L3_cross_field": l3,
    "shot2_identity_unchanged": not any(d["index"] == 2 for d in shot_deltas),
}
out = BASE / "s01a" / "s01a_verify.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=1))
