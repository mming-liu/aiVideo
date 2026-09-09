from pathlib import Path
import contextlib
import io
import json

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.chapter_reader import extract_section
from tools.bench_text_provider_ab import (
    _apply_final_hardfixes,
    _strip_fences,
    process_trial,
)
from tools.bench_reasoning import composite, _DEFAULT_WEIGHTS
from step01_script_gen.climax_words import climax_words_for_gap
from step01_script_gen.constraints import ALL_CONSTRAINTS
from step01_script_gen.constraints.climax_distribution import ClimaxDistributionConstraint
from step01_script_gen.validation import parse_response

ROOT = Path.cwd()
novel = extract_section(str(ROOT / "锦衣夜行_77119小说.txt"), 1)
for idx in range(1, 4):
    path = ROOT / "tools" / "dump" / f"agent_plan__ab__t{idx}.json"
    raw = _strip_fences(path.read_text(encoding="utf-8"))
    with contextlib.redirect_stdout(io.StringIO()):
        result = process_trial(
            "agent_plan",
            {"success": True, "text": raw, "error": None, "usage": {}, "elapsed": 0.0},
            novel,
            trial=idx,
            dry_run=True,
        )
        # Reparse through the same final path used by process_trial.
        script = parse_response(raw, novel_text=novel)
        script, _ = _apply_final_hardfixes(script)
    words = climax_words_for_gap(script)
    climax_shots = sum(
        ClimaxDistributionConstraint._is_climax(shot, words)
        for shot in script.get("shots", [])
    )
    forbidden = "_DOUBAO_FIX_" in raw
    names = [c.name for c in ALL_CONSTRAINTS]
    print(json.dumps({
        "trial": idx,
        "parse": result["metrics"]["parse"],
        "gap": result["metrics"]["gap"],
        "composite": round(composite([result["metrics"]], _DEFAULT_WEIGHTS) * 100, 1),
        "final_gap_pass": result["final_gap_pass"],
        "hard_unresolved": result["hard_unresolved_count"],
        "climax_shots": climax_shots,
        "raw_json_len": len(raw),
        "shot_count": len(script.get("shots", [])),
        "forbidden_marker": forbidden,
        "doubao_constraints": [n for n in names if n.startswith("doubao_")],
    }, ensure_ascii=False))
