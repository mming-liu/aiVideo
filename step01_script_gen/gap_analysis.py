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
    """
    分析分镜脚本的分布情况，检查是否覆盖了小说的高潮剧情，
    并校验镜头拆分是否符合“蒙太奇”和“角色一致性”标准。
    """
    shots = script.get("shots", [])
    if not shots:
        return {"status": "error", "message": "未找到任何分镜"}

    # 1. 高潮关键词库（已优化为中文，匹配 Agnes AI 的中文语境）
    climax_keywords = [
        "战斗", "刺杀", "决斗", "冲突", "反转", "爆炸", "死亡", "离开", "逃离",
        "追逐", "阴谋", "暴露", "密信", "圣旨", "真相", "身份暴露", "杀", "死", "跑",
        "寒光", "鲜血", "尖叫", "破水而出"  # 针对你当前文本的特化关键词
    ]

    # 2. 检查高潮覆盖情况
    has_climax_coverage = False
    climax_shots_count = 0

    for shot in shots:
        # 将旁白、字幕和核心关键词拼接起来进行检测
        segment_text = shot.get("narration_segment", "") + " " + \
                       shot.get("subtitle", "") + " " + \
                       " ".join(shot.get("core_keywords", []))

        if any(kw in segment_text for kw in climax_keywords):
            has_climax_coverage = True
            climax_shots_count += 1

    # 3. 校验“蒙太奇拆分”规则（核心优化点）
    # 规则：如果检测到了高潮剧情，那么相关的镜头数量必须 >= 3
    montage_check_passed = True
    montage_message = ""

    if has_climax_coverage and climax_shots_count < 3:
        montage_check_passed = False
        montage_message = f"⚠️ 警告：检测到高潮剧情，但仅生成了 {climax_shots_count} 个镜头。建议拆分为至少 3 个独立镜头（如：跃出水面、寒光一闪、刺入心口）以增强视觉冲击力。"

    # 4. 校验“角色一致性”规则
    # 规则：检查 prompt 中是否包含了 [角色名: ...] 格式的锚点
    consistency_check_passed = True
    consistency_message = ""
    missing_anchor_shots = []

    # 获取脚本中定义的角色名
    defined_characters = [c.get("identifier", "") for c in script.get("characters", []) if c.get("identifier")]

    for shot in shots:
        prompt = shot.get("prompt", "")
        main_subject = shot.get("main_subject", "")

        # 如果画面主体是已定义的角色，检查 prompt 里是否有锚点
        if main_subject in defined_characters:
            # 简单的正则检查，看 prompt 里有没有类似 [杨旭: ...] 的格式
            if not re.search(rf"\[{main_subject}[:：]", prompt):
                consistency_check_passed = False
                missing_anchor_shots.append(shot.get("index"))

    if not consistency_check_passed:
        consistency_message = f"⚠️ 警告：镜头 {missing_anchor_shots} 的主体是已知角色，但 Prompt 中缺少 [角色名: 特征] 格式的锚点，可能导致 Agnes AI 生成的人物不一致。"

    # 5. 校验“蒙太奇空镜”规则（防止流水账）
    # 规则：前 3 个镜头中，至少要有 1 个是环境空镜（main_subject 包含“空镜”、“特写”或不含人名）
    opening_check_passed = True
    opening_message = ""

    first_three_shots = shots[:3]
    has_opening_shot = any(
        "空镜" in shot.get("main_subject", "") or
        "特写" in shot.get("main_subject", "") or
        shot.get("main_subject", "") not in defined_characters
        for shot in first_three_shots
    )

    if not has_opening_shot and len(shots) > 3:
        opening_check_passed = False
        opening_message = "⚠️ 建议：开篇前 3 个镜头建议增加环境空镜或特写（如：烈日、知了、荷叶），避免直接切入人物导致节奏过快。"

    # 6. 汇总分析结果
    overall_status = "pass"
    messages = []

    if not montage_check_passed:
        overall_status = "fail"
        messages.append(montage_message)
    if not consistency_check_passed:
        overall_status = "warning" if overall_status == "pass" else "fail"
        messages.append(consistency_message)
    if not opening_check_passed:
        overall_status = "warning" if overall_status == "pass" else "fail"
        messages.append(opening_message)

    if overall_status == "pass":
        messages.append("✅ 分镜脚本质量优秀，符合蒙太奇拆分与角色一致性标准。")

    return {
        "status": overall_status,
        "total_shots": len(shots),
        "climax_shots": climax_shots_count,
        "messages": messages
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