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
    """增强版同义词映射（v6.1 新增中文同义词）."""
    synonyms_map = {
        # 刺杀类
        "刺杀": ["刺", "杀", "寒光", "匕首", "袭击", "突袭", "遇害"],
        "刺": ["刺杀", "戳", "穿", "扎入"],
        "杀": ["刺杀", "击杀", "斩", "劈"],
        "寒光": ["剑光", "刀光", "银光", "闪光"],
        "匕首": ["短剑", "利刃", "兵刃"],
        "袭击": ["突袭", "攻击", "冲来", "杀来"],
        # 冲突类
        "冲突": ["对峙", "争吵", "打斗", "交锋", "矛盾"],
        "对峙": ["冲突", "相持", "怒视", "对视"],
        "打斗": ["交手", "过招", "搏斗", "争斗"],
        "交锋": ["对决", "比拼", "争锋"],
        # 逃离类
        "逃离": ["疾驰", "逃跑", "离开", "马车", "离去", "奔逃", "遁走"],
        "逃跑": ["逃离", "奔逃", "逃窜", "逃走"],
        "疾驰": ["飞奔", "疾奔", "奔跑", "策马"],
        "马车": ["车", "马车", "轿子", "车马"],
        # 死亡类
        "死亡": ["死", "亡", "断气", "瞳孔散大", "冰冷", "倒下", "毙命"],
        "死": ["死亡", "亡", "断气", "毙命"],
        "亡": ["死亡", "死", "灭亡"],
        "倒下": ["倒地", "跪倒", "瘫倒"],
        # 太阳/天气类
        "sun": ["sun", "sunlight", "day star"],
        "太阳": ["烈日", "骄阳", "火球", "日头"],
        "earth": ["ground", "field", "soil", "land"],
        "大地": ["地面", "田野", "土地", "田地"],
        "dry": ["drought", "parched", "scorched"],
        "干旱": ["干裂", "燥热", "炙烤"],
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

        # Check for climax keywords (v6.1: 支持中英文 + core_keywords 匹配)
        climax_keywords_en = [
            "battle", "assassinate", "duel", "conflict", "reversal", "explosion",
            "death", "leave", "escape", "chase", "plot", "expose",
            "secret letter", "edict", "truth", "plot exposed", "identity exposed"
        ]
        climax_keywords_cn = [
            "战斗", "刺杀", "决斗", "冲突", "反转", "爆炸", "死亡", "离开", "逃离",
            "追逐", "阴谋", "暴露", "密信", "圣旨", "真相", "身份暴露", "杀", "死",
            "跑", "砍", "剑", "血", "下手", "动手", "突围", "拼杀"
        ]
        climax_keywords = climax_keywords_en + climax_keywords_cn

        # 检查后段镜头是否覆盖高潮（检查 narration_segment + core_keywords）
        has_climax_coverage = False
        for shot in back_shots:
            segment_text = shot.get("narration_segment", "")
            core_kws = shot.get("core_keywords", [])
            search_text = segment_text + " " + " ".join(core_kws)
            if any(kw in search_text for kw in climax_keywords):
                has_climax_coverage = True
                break

        has_climax_coverage = has_climax_coverage

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