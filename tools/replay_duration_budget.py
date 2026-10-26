"""Offline Step01 budget replay against an explicit, hash-frozen dataset.

Manifest keys: plan, novel, plan_sha256, novel_sha256; optional script and
script_sha256. Paths are relative to the manifest. This is an external-data
replay, not a clean-checkout test fixture. No model or transport is permitted.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(manifest_path: Path, output_base: Path) -> Path:
    manifest_path = manifest_path.resolve()
    dataset = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    inputs = {}
    hashes = {}
    for name in ("plan", "novel", "script"):
        if name == "script" and name not in dataset:
            continue
        path = (manifest_path.parent / dataset[name]).resolve()
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != dataset[name + "_sha256"]:
            raise ValueError(f"{name} digest mismatch: {path}")
        hashes[name] = {"path": str(path), "sha256": actual}
        # 与生产装载器一致：universal newlines（CRLF→LF）；哈希仍按原始字节核对。
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        inputs[name] = text if name == "novel" else json.loads(text)
    output_base = output_base.resolve()
    if output_base.exists() and any(output_base.iterdir()):
        raise ValueError(f"output_base must be a fresh empty directory: {output_base}")
    if any(output_base == Path(x["path"]) or output_base in Path(x["path"]).parents
           for x in hashes.values()):
        raise ValueError("output_base must not contain frozen inputs")
    os.environ["OUTPUT_BASE"] = str(output_base)
    # All path/config-bound imports follow isolation setup.
    from shared.config import duration_band, VOICE_ADAPTATION_ENABLED
    from shared.paths import enhancement_experiment_dir
    from tools.probe_isolation import block_external_transport

    dest = enhancement_experiment_dir("duration-budget-replay")
    dest.mkdir(parents=True, exist_ok=False)
    os.environ["STEP01_LOG_PATH"] = str(dest / "step01.log")
    with block_external_transport():
        from shared.content_verification import plan_digest_of
        from step01_script_gen import outline, plan_feasibility as pf
        plan = inputs["plan"]
        original = copy.deepcopy(plan)
        novel = inputs["novel"]
        report = {"kind": "offline_external_dataset", "model_calls": 0,
                  "inputs": hashes, "duration_band": duration_band(),
                  "voice_adaptation_enabled": VOICE_ADAPTATION_ENABLED,
                  "plan_digest": plan_digest_of(plan),
                  "novel_digest": hashlib.sha256(novel.encode("utf-8")).hexdigest()}
        report["evaluate"] = pf.evaluate_plan(copy.deepcopy(plan), novel_text=novel)
        report["acceptance"] = outline.plan_acceptance_problems(copy.deepcopy(plan), novel_text=novel)
        candidate = copy.deepcopy(plan)
        try:
            result = pf.check_plan_before_generation(candidate, chapter_dir=dest, novel_text=novel)
        except (pf.PlanContractInvalidError, pf.PlanInfeasibleError) as exc:
            report["before_generation"] = {"exception": type(exc).__name__, "message": str(exc)}
        else:
            report["before_generation"] = {"exception": None, "report": result}
        report["before_generation"]["input_unchanged"] = candidate == original
        diagnostics = {}
        for path in sorted(dest.glob("step01_plan_*.json")):
            diagnostics[path.name] = json.loads(path.read_text(encoding="utf-8"))
        report["diagnostics"] = diagnostics
        if "script" in inputs:
            from step01_script_gen.constraints import apply_constraints
            from step01_script_gen.constraints.context import ConstraintContext
            from step01_script_gen.constraints.duration_budget import DurationBudgetConstraint
            from step01_script_gen.main import _build_step01_quality_gate
            script = copy.deepcopy(inputs["script"])
            ctx = ConstraintContext.from_script(script, novel_text=novel)
            constraints = apply_constraints(ctx, only=(DurationBudgetConstraint,))
            quality = _build_step01_quality_gate(script, novel)
            issues = quality["hard"] + quality["visual_risk"] + quality["advisory"]
            report["script"] = {
                "total_sec": sum(float(s.get("duration_sec") or 0) for s in script["shots"]),
                "duration_violations": [v.message for v in constraints.violations_remaining],
                "quality_duration_issues": [i for i in issues if i["code"] == "duration_budget"],
                "other_quality_codes": sorted({i["code"] for i in issues if i["code"] != "duration_budget"}),
                "checker_failed": quality["checker_failed"], "rule_version": quality["rule_version"],
                "input_unchanged": script == inputs["script"],
            }
        for name, item in hashes.items():
            if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError(f"frozen {name} changed during replay")
        report["frozen_inputs_unchanged"] = True
        result_path = dest / "duration_replay.json"
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-base", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.manifest, args.output_base))
