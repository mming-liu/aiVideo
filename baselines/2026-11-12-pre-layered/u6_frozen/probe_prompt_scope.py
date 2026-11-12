# -*- coding: utf-8 -*-
"""Check the new candidate's audit prompt still covers all 13 shots (zero-call)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
sys.path.insert(0, str(ROOT))

from step01_script_gen import main as m  # noqa: E402
from step01_script_gen.audit import build_audit_prompt  # noqa: E402

s = json.loads((BASE / "u6_kfs0fix" / "script_projected.json").read_text(encoding="utf-8"))
q = json.loads((BASE / "u6_kfs0fix" / "step01_quality.json").read_text(encoding="utf-8"))
n = (BASE / "u6_inputs" / "novel_chapter_001.txt").read_text(encoding="utf-8")
focus = m._final_audit_focus(s, q)
metadata = m._final_audit_metadata(s, run_id="u6-kfs0fix-scope")
prompt = build_audit_prompt(n, s.get("chapter_outline"), s,
                            audit_focus=focus, metadata=metadata)
indices = sorted({int(x) for x in re.findall(r'"index":\s*(\d+)', prompt)})
out = {
    "revision": s.get("script_revision"),
    "prompt_chars": len(prompt),
    "shot_indices_in_prompt": indices,
    "all_13": indices == list(range(1, 14)),
    "novel_chars": len(n),
    "novel_truncated_at": 12000,
}
(BASE / "u6_kfs0fix" / "prompt_scope.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
