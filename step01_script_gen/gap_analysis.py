# -*- coding: utf-8 -*-
"""gap_analysis - Script-Novel Gap Analysis (v5.0)

Function:
  - Validate shot distribution covers latter-half climax (battle/assassination/reversal)
  - Generate complete gap report for debugging

Note: Keyword coverage detection is handled by validation.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
import json
import re
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ========================================================================
# 1. Core Keywords Coverage Calculation
# ========================================================================

def _keyword_covered(keyword: str, prompt: str) -> bool:
    """Check if keyword has visual match in prompt."""
    if keyword in prompt:
        return True
    # Synonym matching
    synonyms = _get_synonyms(keyword)
    for syn in synonyms:
        if syn in prompt:
            return True
    return False


def _get_synonyms(keyword: str) -> list:
    """Get keyword synonym mapping."""
    synonyms_map = {
        "sun": ["sun", "sunlight", "day star"],
        "earth": ["ground", "field", "soil", "land"],
        "dry": ["drought", "parched", "scorched"],
    }
    return synonyms_map.get(keyword, [])


def calculate_keyword_coverage(shot: dict, novel_text: str) -> dict:
    """Calculate keyword coverage for a single shot (simplified, for debug report).

    Note: Keyword coverage detection is handled by validation.py.
    """
    core_keywords = shot.get("core_keywords", [])
    prompt = shot.get("prompt", "")

    if not core_keywords:
        return {"total": 0, "covered": 0, "coverage_rate": 0.0}

    covered = sum(1 for kw in core_keywords if _keyword_covered(kw, prompt))
    total = len(core_keywords)
    coverage_rate = covered / total if total > 0 else 0.0

    return {
        "total": total,
        "covered": covered,
        "coverage_rate": round(coverage_rate, 3),
    }


# ========================================================================
# 2. Shot Distribution Validation
# ========================================================================

def analyze_shot_distribution(script: dict, novel_text: str) -> dict:
    """Analyze shot distribution for full chapter arc coverage (including climax).

    Args:
        script: Script data
        novel_text: Novel chapter text

    Returns:
        Distribution analysis result
    """
    shots = script.get("shots", [])
    if not shots or not novel_text:
        return {"valid": False, "reason": "no_shots_or_novel"}

    # Check 1: Enough shots to cover novel
    novel_len = len(novel_text)
    min_shots_for_coverage = max(6, novel_len // 600)
    shot_count = len(shots)

    coverage_check = {
        "novel_length": novel_len,
        "shot_count": shot_count,
        "min_required": min_shots_for_coverage,
        "sufficient": shot_count >= min_shots_for_coverage,
    }

    # Check 2: Back 1/3 shots cover latter-half (climax)
    total_narration = "".join(s.get("narration_segment", "") for s in shots)
    total_narration_len = len(total_narration)

    if total_narration_len > 0:
        front_shots = shots[:shot_count // 3] if shot_count >= 3 else []
        middle_shots = shots[shot_count // 3: (shot_count * 2) // 3] if shot_count >= 3 else []
        back_shots = shots[(shot_count * 2) // 3:] if shot_count >= 3 else shots

        front_narration = "".join(s.get("narration_segment", "") for s in front_shots)
        back_narration = "".join(s.get("narration_segment", "") for s in back_shots)

        # Check for climax keywords
        climax_keywords = [
            "battle", "assassinate", "duel", "conflict", "reversal", "explosion",
            "death", "leave", "escape", "chase", "plot", "expose",
            "secret letter", "edict", "truth", "plot exposed", "identity exposed"
        ]

        has_climax_coverage = any(kw in back_narration for kw in climax_keywords)

        arc_coverage = {
            "front_shots": len(front_shots),
            "middle_shots": len(middle_shots),
            "back_shots": len(back_shots),
            "front_narration_len": len(front_narration),
            "back_narration_len": len(back_narration),
            "has_climax_coverage": has_climax_coverage,
        }

        # Back section must have shots and cover climax
        valid = (
            len(back_shots) > 0 and
            arc_coverage["back_narration_len"] >= novel_len * 0.15 and
            has_climax_coverage
        )
    else:
        arc_coverage = {"error": "no_narration_content"}
        valid = False

    return {
        "valid": valid,
        "coverage": coverage_check,
        "arc_coverage": arc_coverage,
    }


# ========================================================================
# 3. Complete Gap Report
# ========================================================================

def generate_gap_report(script_path: str, novel_path: str, chapter: int = 1) -> dict:
    """Generate complete gap analysis report.

    Args:
        script_path: script_result.json path
        novel_path: Novel file path
        chapter: Chapter number

    Returns:
        Complete gap analysis report
    """
    from shared.chapter_reader import extract_section

    # Read script
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    # Read novel chapter
    novel_text = extract_section(novel_path, chapter)

    report = {
        "meta": {
            "script_path": script_path,
            "novel_path": novel_path,
            "chapter": chapter,
            "novel_length": len(novel_text),
            "shot_count": len(script.get("shots", [])),
        },
        "shot_coverage": [],
        "distribution": None,
        "summary": {},
    }

    # 1. Calculate keyword coverage per shot
    shots = script.get("shots", [])
    for shot in shots:
        idx = shot.get("index", "?")
        coverage = calculate_keyword_coverage(shot, novel_text)
        report["shot_coverage"].append({
            "shot_index": idx,
            "coverage_rate": coverage["coverage_rate"],
        })

    # 2. Validate distribution
    report["distribution"] = analyze_shot_distribution(script, novel_text)

    # 3. Generate summary
    report["summary"] = {
        "arc_valid": report["distribution"]["valid"],
        "issues": [],
    }

    if not report["distribution"]["valid"]:
        report["summary"]["issues"].append("Shot distribution does not cover latter-half climax")

    return report


def print_gap_report(report: dict) -> None:
    """Print gap report (human readable format)."""
    print("\n" + "=" * 60)
    print("Script-Novel Gap Analysis Report")
    print("=" * 60)

    meta = report["meta"]
    print(f"\n[Meta]")
    print(f"  Chapter: {meta['chapter']}")
    print(f"  Novel length: {meta['novel_length']} chars")
    print(f"  Shot count: {meta['shot_count']}")

    print(f"\n[Shot Distribution]")
    dist = report["distribution"]
    if dist.get("valid", False):
        print("  OK: Shots cover full chapter arc")
    else:
        print("  Issue: Shots do not cover full arc")
        for issue in report["summary"].get("issues", []):
            print(f"    - {issue}")

    print("\n" + "=" * 60)


# ========================================================================
# 4. Validation Function (for main.py)
# ========================================================================

def validate_script_gap(script: dict, novel_text: str) -> dict:
    """Quick validation of script-novel gap.

    Only checks shot distribution (latter-half climax coverage).
    Keyword coverage is handled by validation.py.

    Returns:
        Validation result dict
    """
    dist = analyze_shot_distribution(script, novel_text)

    return {
        "low_coverage_shots": [],  # Handled by validation.py
        "distribution": dist,
        "overall_pass": dist["valid"],
    }


# ========================================================================
# 5. CLI Entry
# ========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Script-Novel Gap Analysis")
    parser.add_argument("--script", "-s", required=True, help="script_result.json path")
    parser.add_argument("--novel", "-n", default="锦衣夜行_77119小说.txt", help="Novel file path")
    parser.add_argument("--chapter", "-c", type=int, default=1, help="Chapter number")
    parser.add_argument("--output", "-o", help="Output report path (JSON)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Generate report
    report = generate_gap_report(args.script, args.novel, args.chapter)

    if args.verbose:
        print_gap_report(report)
    else:
        # Summary output
        summary = report["summary"]
        print(f"Arc valid: {'PASS' if summary.get('arc_valid') else 'FAIL'}")
        for issue in summary.get("issues", []):
            print(f"  Issue: {issue}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nReport saved to: {args.output}")