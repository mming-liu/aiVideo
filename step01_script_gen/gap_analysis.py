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

from step01_script_gen.climax_words import KEYWORD_SYNONYMS, climax_words_for_gap


# ========================================================================
# 1. Core Keywords Coverage Calculation
# ========================================================================

def normalize_core_keywords(value) -> list[str]:
    """Normalize core_keywords from either schema array or legacy comma string."""
    if isinstance(value, (list, tuple)):
        return [str(kw).strip() for kw in value if str(kw).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[，,、;；]+", value) if part.strip()]
    return []

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
    """增强版同义词映射（v6.1 新增中文同义词；词表单源 climax_words.KEYWORD_SYNONYMS）."""
    return KEYWORD_SYNONYMS.get(keyword, [])


def calculate_keyword_coverage(shot: dict, novel_text: str) -> dict:
    """Calculate keyword coverage for a single shot (for debug report + gap feedback).

    C1 修复（08-11）：旧实现只查 core_keywords 是否在 prompt 中命中（且不返回
    missing 列表），`validate_script_gap` 因此恒返回空 `low_coverage_shots`，
    声称的覆盖检查从未真正执行（todolist C 档死代码）。现在：
    - 命中源扩大：prompt + ff_desc + lf_desc + motion_desc（关键词可能只出现在
      帧描述里，单查 prompt 会漏报）
    - 返回 missing 列表：供 `_load_gap_analysis_context` 反馈给 LLM 具体缺了哪些词
    """
    core_keywords = normalize_core_keywords(shot.get("core_keywords", []))
    prompt = shot.get("prompt", "")
    # 命中源：画面描述全字段（关键词可能只出现在帧描述里，单查 prompt 会漏报）
    search_pool = " ".join(filter(None, [
        prompt,
        shot.get("ff_desc", "") or "",
        shot.get("lf_desc", "") or "",
        shot.get("motion_desc", "") or "",
        shot.get("subtitle", "") or "",
    ]))

    if not core_keywords:
        return {"total": 0, "covered": 0, "coverage_rate": 0.0, "missing": []}

    missing = [kw for kw in core_keywords if not _keyword_covered(kw, search_pool)]
    covered = len(core_keywords) - len(missing)
    total = len(core_keywords)
    coverage_rate = covered / total if total > 0 else 0.0

    return {
        "total": total,
        "covered": covered,
        "coverage_rate": round(coverage_rate, 3),
        "missing": missing,
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

    # 1. 高潮关键词库（单源 climax_words；通用半跨小说 + 特化词经
    #    script["meta"]["climax_keywords"] 注入，未提供时走锦衣夜行第一章兜底）
    climax_keywords = climax_words_for_gap(script)

    # 2. 检查高潮覆盖情况
    has_climax_coverage = False
    climax_shots_count = 0

    for shot in shots:
        # 将对话、字幕和核心关键词拼接起来进行检测
        segment_text = shot.get("narration_segment", "") + " " + \
                       shot.get("subtitle", "") + " " + \
                       " ".join(normalize_core_keywords(shot.get("core_keywords", [])))

        if any(kw in segment_text for kw in climax_keywords):
            has_climax_coverage = True
            climax_shots_count += 1
            continue

        # v6.62.4（09-08）扩检测面：上面 3 字段在实测中近乎全盲——09-08
        # chapter_001 有 7/11 镜 narration_segment 为空，高潮若只写在
        # main_subject/motion_desc 里就完全检测不到。补扫这两个「语义动作」
        # 字段。**不扫 prompt**：prompt 带风格前缀（无重影/无伪影）与
        # [角色: 特征] 锚点，是噪音源，实测相比 motion_desc 零增益。
        # 单字词（杀/死/跑）在新字段易假命中（实测 shot1 的 prompt 因单字
        # 「跑」假命中），故新字段只认 ≥2 字关键词，单字词仍只在上面 3 字段生效。
        action_text = str(shot.get("main_subject", "") or "") + " " + \
                      str(shot.get("motion_desc", "") or "")
        if any(kw in action_text for kw in climax_keywords if len(kw) >= 2):
            has_climax_coverage = True
            climax_shots_count += 1

    # 3. 校验“蒙太奇拆分”规则（核心优化点）
    # 规则：如果检测到了高潮剧情，那么相关的镜头数量必须 >= 3
    montage_check_passed = True
    montage_message = ""

    # v6.62.4（09-08）修零边界漏洞：旧实现写 `if has_climax_coverage and count < 3`
    # —— count==0 时 has_climax_coverage 为 False，整个检查被**跳过**，于是
    # 「1-2 个高潮镜」FAIL 而「0 个高潮镜」PASS，还打印「✅ 质量优秀」。
    # 09-08 chapter_001 实测：正片 11 镜零高潮（刺客/刺杀/匕首/心口/破水/
    # 尖叫/死 全 0 次，张十三 0 次出镜），GAP 报 status=pass、反馈为空，
    # LLM 永远收不到「补高潮」指令。0 是比 1-2 更严重的缺陷，必须独立分支。
    if climax_shots_count == 0:
        montage_check_passed = False
        montage_message = (
            "⚠️ 严重：正片镜头**零高潮覆盖**——未检测到任何强事件词"
            "（战斗/刺杀/反转/冲突/追逐/暴露等）。正片只拍了铺垫，"
            "原文的核心冲突/高潮整段缺失。请重写：后半段至少 3 镜覆盖原文的"
            "冲突/反转/动作高潮，且这些镜头必须有具体动作描述与台词。"
        )
    elif climax_shots_count < 3:
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

    # 返回完整结构（供 _load_gap_analysis_Context 使用）
    return {
        "status": overall_status,
        "valid": overall_status == "pass",  # 兼容旧代码
        "has_climax_coverage": has_climax_coverage,  # 新增：供差距反馈使用
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
        Validation result dict with structure matching _load_gap_analysis_context expectations:
        {
            "low_coverage_shots": [],
            "distribution": {
                "status": str,  # "pass", "warning", "fail"
                "has_climax_coverage": bool,
                "valid": bool,
                "messages": [str],
            },
            "overall_pass": bool,
        }
    """
    dist = analyze_shot_distribution(script, novel_text)

    # 将 analyze_shot_distribution 的输出转换为 _load_gap_analysis_context 期望的格式
    status = dist.get("status", "pass")

    # 检查高潮覆盖
    has_climax_coverage = dist.get("climax_shots", 0) > 0

    # C1 修复（08-11）：真正计算低覆盖率镜头——旧实现硬编码返回空列表，
    # `_load_gap_analysis_context` 的「关键词覆盖率问题」反馈永不触发，覆盖
    # 检查形同虚设。现逐镜算覆盖率，低于阈值（<0.5）且缺词的镜头进反馈。
    low_coverage_shots = []
    _COVERAGE_THRESHOLD = 0.5
    for shot in script.get("shots", []):
        cov = calculate_keyword_coverage(shot, novel_text)
        if cov["total"] > 0 and cov["coverage_rate"] < _COVERAGE_THRESHOLD:
            low_coverage_shots.append({
                "index": shot.get("index", "?"),
                "coverage_rate": cov["coverage_rate"],
                "missing": cov.get("missing", [])[:5],
            })

    # 检查是否有失败或警告消息
    has_messages = bool(dist.get("messages")) and any(
        "⚠️" in m or "警告" in m for m in dist.get("messages", [])
    )

    return {
        "low_coverage_shots": low_coverage_shots,  # C1：真正计算（此前恒为空死代码）
        "distribution": {
            "status": status,
            "has_climax_coverage": has_climax_coverage,
            "valid": status == "pass",  # pass 状态表示通过
            "messages": dist.get("messages", []),
            "climax_shots": dist.get("climax_shots", 0),
            "total_shots": dist.get("total_shots", 0),
        },
        "overall_pass": status == "pass" and not has_messages,
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
