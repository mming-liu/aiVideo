"""step01_script_gen.validation - 脚本验证模块（v5.0 增强版）

功能：
- _validate_script(): 验证脚本结构与数据完整性（含景别多样性、字幕-画面绑定）
- _check_prompt_subtitle_alignment(): 校验 prompt 与 subtitle 对齐
- _check_subtitle_char_coverage(): 校验 subtitle 字符覆盖率（含 bigram 分析）
- parse_response(): 解析 LLM 返回的原始文本为 JSON
- v5.0 新增：新字段必填校验 + scene_compliance 合规集成
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
from datetime import datetime

from shared.utils.json_repair import safe_parse_json_object, _fix_common_json_errors
import scene_compliance
import storyboard_refs


# 模块级变量：存储当前待验证的小说原文
_novel_text_for_validation = ""


def _set_novel_text(text: str) -> None:
    """设置待验证的小说原文。"""
    global _novel_text_for_validation
    _novel_text_for_validation = text


# v5.0 新增必填字段列表
_REQUIRED_NEW_FIELDS = [
    "main_subject",
    "core_keywords",
    "fx_effect",
    "audio_cue",
    "mask_area",
    "color_tone",
    "clip_transition",
    # v6.4 新增：视频钩子版新字段
    "content",
    "camera_movement",
    "framing",
    "lighting_color",
    "duration",
    "negative_prompt",
    "hook",
    # v6.6 新增：物理地点
    "location",
]


def _validate_script(script: dict) -> None:
    """验证脚本结构与数据完整性.

    v5.0 新增:
      - 校验 main_subject / core_keywords / fx_effect 等新字段
      - 集成 scene_compliance.validate_script() 做合规校验
    """
    if "shots" not in script:
        raise ValueError("缺少 shots 字段")
    shots = script.get("shots", [])

    # --- 镜头数校验 ---
    reported_count = script.get("shot_count")
    actual_count = len(shots)
    if reported_count is None:
        print(f"[WARN] JSON 中缺少 shot_count 字段，建议 LLM 在输出中包含此字段")
    elif not isinstance(reported_count, int) or reported_count < 4 or reported_count > 20:
        print(f"[WARN] shot_count={reported_count} 超出配置范围 [4, 20]")
    elif reported_count != actual_count:
        print(f"[WARN] 声明 shot_count={reported_count} 但实际镜头数为 {actual_count}，已自动修正")
        script["shot_count"] = actual_count

    # --- 全局渲染参数校验 ---
    for field in ("resolution", "ratio", "fps", "render_style"):
        if field not in script:
            print(f"[WARN] JSON 缺少全局渲染参数 {field}")

    # 验证每个 shot 必有字段
    required = {"index", "narration_segment", "subtitle", "prompt"}
    for i, shot in enumerate(shots, 1):
        missing = required - set(shot.keys())
        if missing:
            if "index" in missing:
                shot["index"] = i
            if "narration_segment" in missing:
                shot["narration_segment"] = shot.get("subtitle", "")
            if "subtitle" in missing:
                shot["subtitle"] = shot.get("narration_segment", "")
            if "prompt" in missing:
                shot["prompt"] = ""

    # v5.0: 校验新增必填字段（仅 WARN 不阻断）
    for i, shot in enumerate(shots, 1):
        for field in _REQUIRED_NEW_FIELDS:
            if field not in shot or not shot.get(field):
                print(f"[WARN] 镜头{i}: 缺少字段 {field}")

    # 验证 narration == 所有 subtitle 拼接
    combined = "".join(s.get("subtitle", "") for s in shots)
    expected_narration = script.get("narration", "")
    if combined != expected_narration:
        print(f"[WARN] narration 与 subtitle 拼接不一致")

    # 验证每个 shot 的 subtitle == narration_segment
    # v6.2: 改为 auto-fix 而非崩溃——LLM 不总严格遵守此规则
    for i, shot in enumerate(shots, 1):
        sub = shot.get("subtitle", "")
        narr = shot.get("narration_segment", "")
        if sub != narr:
            # 优先取 narration_segment（来自原文摘取，通常更准确）
            if narr and not sub:
                shot["subtitle"] = narr
                print(f"[WARN] 镜头{i}: subtitle 为空，已用 narration_segment 填充")
            elif sub and not narr:
                shot["narration_segment"] = sub
                print(f"[WARN] 镜头{i}: narration_segment 为空，已用 subtitle 填充")
            else:
                # 两者都有但不一致：以 narration_segment 为准
                print(f"[WARN] 镜头{i}: subtitle 与 narration_segment 不一致，以 narration_segment 为准")
                print(f"       subtitle: {sub[:40]}...")
                print(f"       narration: {narr[:40]}...")
                shot["subtitle"] = narr

    # --- 景别多样性校验 ---
    _check_shot_size_diversity(shots)

    # v6.1: Agnes AI 音频指令校验
    _check_audio_instruction(shots)

    # --- 单镜头校验 ---
    for i, shot in enumerate(shots, 1):
        seg = shot.get("narration_segment", "").strip()
        sub = shot.get("subtitle", "").strip()
        search_text = " ".join([
            shot.get("prompt", ""),
            shot.get("ff_desc", ""),
            shot.get("motion_desc", ""),
        ])

        if seg and sub and seg != sub:
            print(f"[WARN] 镜头{i}: subtitle 与 narration_segment 不一致")
            print(f"       subtitle={sub}")
            print(f"       narration_segment={seg}")

        if sub and search_text:
            _check_prompt_subtitle_alignment(i, sub, search_text)

    # v5.0: scene_compliance 合规校验（宽松模式，仅 WARN）
    compliance_result = scene_compliance.validate_script(script, strict_mode=False)
    if not compliance_result["pass"]:
        print(f"[WARN] 合规校验: {compliance_result['summary']['fail_count']} 个镜头未通过")
        for report in compliance_result["shot_reports"]:
            if not report["pass"]:
                for ck, cv in report["checks"].items():
                    if not cv.get("pass", True):
                        print(f"  镜头{report['index']} {ck}: {cv.get('reason', '')}")
                for w in report["warnings"]:
                    print(f"  镜头{report['index']}: {w}")

    # v6.2: 人物一致性校验（纳入常规流程，强制运行）
    _check_character_consistency(script)

    # v6.6: 逐镜头原文锚点校验（新增 P1 优化）
    # 检查 narration_segment 是否能在原文中找到对应内容，防止 LLM 编造
    _check_grounding_and_warn(script)

    # v6.3: 分镜提示词检查表（8项核心检查）
    checklist_result = _run_prompt_checklist(script)
    if not checklist_result["pass"]:
        print(f"[WARN] 分镜提示词检查表: {len([r for r in checklist_result['results'].values() if not r.get('pass', True)])} 项未通过")


def _check_shot_size_diversity(shots: list[dict]) -> None:
    """校验镜头景别多样性：至少 ceil(N/2) 种不同景别，无相邻同景别."""
    shot_sizes_used = []
    for shot in shots:
        prompt = shot.get("prompt", "")
        found = None
        for sz in storyboard_refs.SHOT_SIZES:
            sz_short = sz.split(" ")[0]
            if sz_short in prompt:
                found = sz_short
                break
        shot_sizes_used.append(found)

    unique_sizes = len(set(s for s in shot_sizes_used if s))
    min_required = max(3, -(len(shots) // -2))  # ceil(N/2)
    if unique_sizes < min_required:
        print(f"[WARN] 景别多样性不足：{unique_sizes} 种（{shot_sizes_used}），要求至少 {min_required} 种")
    for i in range(len(shot_sizes_used) - 1):
        if shot_sizes_used[i] and shot_sizes_used[i] == shot_sizes_used[i + 1]:
            print(f"[WARN] 镜头{i+1}与镜头{i+2}景别重复（{shot_sizes_used[i]}）")


def _check_prompt_subtitle_alignment(shot_idx: int, subtitle: str, search_text: str) -> None:
    """校验 prompt 与 subtitle 的硬性矛盾（场景矛盾 + 字符覆盖率）."""
    prompt_body = search_text.split("高清1080P")[-1] if "高清1080P" in search_text else search_text

    # --- 场景/地点矛盾 ---
    scene_check = scene_compliance.check_scene_contradiction(subtitle, prompt_body)
    if not scene_check["pass"]:
        print(f"[WARN] 镜头{shot_idx}: {scene_check['reason']}")
        return

    # --- 字幕汉字覆盖率 ---
    _check_subtitle_char_coverage(shot_idx, subtitle, prompt_body)


def _check_subtitle_char_coverage(shot_idx: int, subtitle: str, search_text: str) -> None:
    """检测字幕汉字在画面描述中的覆盖率（含单字 + 二字组合命中率）."""
    chars = [c for c in subtitle if '一' <= c <= '鿿']
    if len(chars) < 10:
        return

    found = [c for c in chars if c in search_text]
    char_hit_rate = len(found) / len(chars)

    bigrams_in_sub = set()
    for i in range(len(chars) - 1):
        bigrams_in_sub.add(chars[i] + chars[i + 1])
    bigrams_found = {bg for bg in bigrams_in_sub if bg in search_text}
    bg_hit_rate = len(bigrams_found) / len(bigrams_in_sub) if bigrams_in_sub else 1

    if char_hit_rate < 0.15 and bg_hit_rate == 0:
        missing_bg = list(bigrams_in_sub - bigrams_found)[:6]
        print(f"[WARN] 镜头{shot_idx}: 画面描述与字幕内容严重脱节"
              f"（汉字命中{char_hit_rate:.0%}、二字组合全部未命中）")
        print(f"       未命中的二字组合: {missing_bg}")


def _check_audio_instruction(shots: list[dict]) -> None:
    """检查是否包含 Agnes AI 的声音指令（v6.1 新增）."""
    for shot in shots:
        shot_idx = shot.get("index", "?")
        subtitle = shot.get("subtitle", "")
        prompt = shot.get("prompt", "")

        # 简单判断：如果 subtitle 包含冒号或引号，视为有对话
        if ":" in subtitle or "：" in subtitle or '"' in subtitle or '"' in subtitle or '"' in subtitle:
            if "（用中文说）" not in prompt and "(用中文说)" not in prompt:
                print(f"[WARN] 镜头{shot_idx}: 检测到对话但 Prompt 中缺少 Agnes AI 中文发音指令 '（用中文说）'")


# 模块级变量 _novel_text_for_validation 已在文件顶部（line 29）定义，此处不再重复
# （code-audit 07-31 #5：原 :254 与 :29 重复定义同一模块级变量）


def _check_grounding(shot: dict, novel_text: str) -> bool:
    """简单的原文锚点检查：检查 narration 是否在原文中有对应内容。"""
    narration = shot.get("narration_segment", "").strip()
    if not narration:
        return True

    narration_clean = narration.replace(" ", "").replace("，", "").replace("。", "")
    for i in range(len(narration_clean) - 4):
        chunk = narration_clean[i:i+5]
        if chunk in novel_text:
            return True
    return False


def _check_grounding_and_warn(script: dict) -> None:
    """对脚本中所有镜头进行原文锚点校验并打印警告。"""
    global _novel_text_for_validation
    if not _novel_text_for_validation:
        return

    shots = script.get("shots", [])
    ungrounded = []
    for shot in shots:
        idx = shot.get("index", "?")
        if not _check_grounding(shot, _novel_text_for_validation):
            narration = shot.get("narration_segment", "").strip()
            ungrounded.append((idx, narration[:30] if narration else ""))

    if ungrounded:
        print(f"[WARN] 原文锚点校验: 以下镜头的 narration 在原文中未找到对应内容:")
        for idx, text in ungrounded:
            print(f"  镜头{idx}: \"{text}...\"")
        print(f"  → 请确保所有 narration 直接引用自小说原文")


def _check_character_consistency(script: dict) -> dict:
    """校验人物一致性（v6.2 新增，纳入常规校验流程）.

    检查项：
    1. 角色锚点检查：main_subject 是已知角色时，prompt 必须有 [角色名: ...] 锚点
    2. 跨镜头特征一致性：同一角色在不同镜头中，静态特征（衣着/外貌）不应矛盾
    3. 对话提到角色但画面缺失：narration 中明确提到角色名，但 prompt/fd_desc 无此人
    4. 空镜中出现角色：main_subject 为非人物（如"荷塘""日落"）但 prompt 含人物描述

    Returns:
        dict: {"pass": bool, "issues": [str], "warnings": [str]}
    """
    shots = script.get("shots", [])
    characters = script.get("characters", [])
    defined_ids = {c.get("identifier", "") for c in characters if c.get("identifier")}

    if not defined_ids or not shots:
        return {"pass": True, "issues": [], "warnings": []}

    issues = []
    warnings = []

    # --- 1. 每镜头角色锚点检查 ---
    missing_anchor = []
    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        main_subject = shot.get("main_subject", "").strip()

        if main_subject in defined_ids:
            if not re.search(rf"\[{re.escape(main_subject)}[:：]", prompt):
                missing_anchor.append(idx)
                issues.append(
                    f"镜头{idx}: main_subject=\"{main_subject}\" 是已知角色，"
                    f"但 prompt 中缺少 [{main_subject}: 特征] 角色锚点"
                )

    if missing_anchor:
        print(f"[WARN] 角色锚点缺失: 镜头 {missing_anchor} 的主体是已知角色但 prompt 缺少锚点")

    # --- 2. 跨镜头特征一致性 ---
    # 从 prompt 中提取每个角色的锚点特征，检查同一角色在不同镜头描述是否矛盾
    char_features_by_shot = {}  # {角色名: {shot_idx: features_text}}
    for char_id in defined_ids:
        char_features_by_shot[char_id] = {}
    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        for char_id in defined_ids:
            m = re.search(rf"\[{re.escape(char_id)}[:：]([^\]]+)\]", prompt)
            if m:
                char_features_by_shot[char_id][idx] = m.group(1).strip()

    # 检查同一角色特征是否矛盾（简化为关键词矛盾检测）
    contradictory_pairs = [
        ("黑衣", "白衣"), ("黑发", "白发"), ("长发", "短发"),
        ("长袍", "短衫"), ("官服", "布衣"), ("锦衣", "粗布"),
        ("年轻", "苍老"), ("瘦削", "魁梧"), ("高", "矮"),
        ("佩剑", "空手"), ("持刀", "持剑"),
    ]
    for char_id, shot_features in char_features_by_shot.items():
        if len(shot_features) < 2:
            continue
        shot_idxs = list(shot_features.keys())
        for i in range(len(shot_idxs)):
            for j in range(i + 1, len(shot_idxs)):
                feat_a = shot_features[shot_idxs[i]]
                feat_b = shot_features[shot_idxs[j]]
                for word_a, word_b in contradictory_pairs:
                    if word_a in feat_a and word_b in feat_b:
                        issues.append(
                            f"角色\"{char_id}\" 特征矛盾：镜头{shot_idxs[i]} 含\"{word_a}\"，"
                            f"镜头{shot_idxs[j]} 含\"{word_b}\""
                        )
                        break
                # 同时检查反向
                for word_a, word_b in contradictory_pairs:
                    if word_b in feat_a and word_a in feat_b:
                        issues.append(
                            f"角色\"{char_id}\" 特征矛盾：镜头{shot_idxs[i]} 含\"{word_b}\"，"
                            f"镜头{shot_idxs[j]} 含\"{word_a}\""
                        )
                        break

    # --- 3. 对话/字幕提到角色但画面可能缺失 ---
    for shot in shots:
        idx = shot.get("index", "?")
        narration = shot.get("narration_segment", "")
        prompt = shot.get("prompt", "")
        ff_desc = shot.get("ff_desc", "")
        lf_desc = shot.get("lf_desc", "")
        search_pool = prompt + ff_desc + lf_desc

        for char_id in defined_ids:
            if char_id in narration:
                # 对话提到了这个角色，检查画面中是否出现了该角色
                if char_id not in search_pool:
                    main_sub = shot.get("main_subject", "")
                    # 排除画外音/未见其人的情况
                    is_offscreen = any(kw in narration for kw in ["声", "音", "喊", "说", "道", "闻", "听"])
                    if not is_offscreen or char_id == main_sub:
                        warnings.append(
                            f"镜头{idx}: narration 提到角色\"{char_id}\"，"
                            f"但 prompt/fd_desc/lf_desc 中均未出现该角色名"
                        )

    # --- 4. 空镜中出现人物（main_subject 不匹配） ---
    non_character_subjects = {
        "空镜", "荷塘", "池塘", "日落", "日出", "山", "水", "湖", "河", "田野",
        "田埂", "村庄", "房屋", "门", "窗", "树", "花", "草", "云", "月", "日",
        "雨", "雪", "风", "雾", "路", "桥", "船", "灯", "烛", "书案", "桌",
        "天空", "大地", "庭院", "回廊", "楼阁", "屋檐", "照月湾", "荷塘黄昏全景",
    }
    for shot in shots:
        idx = shot.get("index", "?")
        main_subject = shot.get("main_subject", "").strip()
        prompt = shot.get("prompt", "")
        ff_desc = shot.get("ff_desc", "")
        lf_desc = shot.get("lf_desc", "")

        # 如果 main_subject 是空镜/场景/物体，检查是否不当出现人物
        is_non_char = any(nc in main_subject for nc in non_character_subjects)
        if is_non_char:
            # 检查 prompt 首段（锚点之前）是否有人物描述
            # 如果有 [角色名: 特征] 锚点，说明 LLM 明确加了人物，不算误报
            has_char_anchor = bool(re.search(r"\[[^\]]+[:：][^\]]+\]", prompt))
            if not has_char_anchor:
                # 模糊检测：prompt/ff_Desc/lf_Desc 中出现常见人物词但 main_subject 不匹配
                person_words = ["人物", "角色", "人影", "身影", "男子", "女子", "老人", "少年",
                              "少女", "孩子", "娃娃", "公子", "姑娘", "刺客", "杀手", "官员",
                              "士兵", "侍女", "人", "两人", "三人", "两个人", "三个人"]
                # 合并所有文本进行检查
                all_text = prompt + ff_desc + lf_desc
                found_persons = [pw for pw in person_words if pw in all_text]
                if found_persons:
                    # 检查是否 main_subject 本身就包含这些词（如"刺客出水"）
                    if not any(pw in main_subject for pw in found_persons):
                        warnings.append(
                            f"镜头{idx}: main_subject=\"{main_subject}\" 疑似空镜/场景，"
                            f"但 prompt/ff_Desc/lf_Desc 含有 {found_persons}，可能是空镜中不当出现人物"
                        )

    # --- 汇总 ---
    has_issues = len(issues) > 0 or len(warnings) > 0
    if has_issues:
        for issue in issues:
            print(f"[CHECK] 人物一致性: {issue}")
        for warn in warnings:
            print(f"[CHECK] 人物一致性: [WARN] {warn}")

    return {"pass": not bool(issues), "issues": issues, "warnings": warnings}


# ===========================================================================
# v6.3 新增：分镜提示词检查表（适配用户提供的8项核心检查）
# ===========================================================================

# --- 5. 提示词污染检测：无效关键词列表 ---
_INVALID_PROMPT_KEYWORDS = {
    "8K清晰度", "8k清晰度", "8K画质", "8k画质", "超高清", "HD画质",
    "无AI感", "无AI痕迹", "无AI味道", "不像AI", "AI感",
    "电影感", "电影画质", " cinematic", "电影级",
    "真实感", "逼真", "写实感",
    "专业摄影", "专业摄像", "摄影师级别",
    "顶级画质", "最高画质", "最佳画质",
    "单反", " DSLR", "微单", "摄像机",
    "好莱坞", "大片感",
    "Adobe", "Photoshop", "渲染",
    "CG", "3D渲染", "三维渲染",
    "光线追踪", "Ray Tracing",
}

# --- 7. 语义风险关键词（需拦截） ---
_SEMANTIC_RISK_KEYWORDS = {
    "迷药", "蒙汗药", "春药", "媚药",
    "如何制作", "制作方法", "配方",
    "杀人", "自杀", "轻生",
    "抢劫", "盗窃", "偷盗",
    "毒品", "吸毒", "贩毒",
    "赌博", "作弊",
}

# --- 6. 逆向提示词模板 ---
_COMMON_NEGATIVE_PROMPTS = {
    "人物变形", "面部扭曲", "多余的手指", "畸形",
    "画面抖动", "模糊", "虚焦", "噪点",
    "色差", "色彩失真", "过曝", "欠曝",
    "穿帮", "穿帮镜头", "不自然",
    "闪烁", "跳帧", "卡顿",
    "水印", "logo", "文字",
    "字幕", "文本",
}

# v6.4 新增：视频教程转场关键词库
_TRANSITION_COMBAT = {"冲击转场", "快速跟拍", "动作连贯", "转场时长0.2s", "画面冲击转场", "快速切换"}
_TRANSITION_SCENE_SWITCH = {"淡入淡出转场", "镜头拉远", "自然过渡", "转场时长0.5s", "平滑过渡", "渐变转场"}
_TRANSITION_CLOSEUP = {"模糊转场", "镜头推近", "节奏缓慢", "转场时长0.3s", "渐进转场", "推进转场"}
_TRANSITION_GENERIC = {"转场时长", "镜头运动", "光影变化", "画面衔接", "转场指令", "转场效果"}

# 场景类型判断关键词
_COMBAT_KEYWORDS = {"打斗", "战斗", "格斗", "刺杀", "攻击", "砍杀", "挥刀", "拔剑", "跃出", "冲刺"}
_CLOSEUP_KEYWORDS = {"特写", "近景", "面部", "眼神", "表情", "眼睛", "嘴唇", "手指"}


def _check_prompt_pollution(shots: list[dict]) -> dict:
    """检查提示词污染：堆砌无效关键词（检查项5）."""
    issues = []
    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")

        found_invalid = [kw for kw in _INVALID_PROMPT_KEYWORDS if kw in prompt]
        if found_invalid:
            issues.append(f"镜头{idx}: 包含无效关键词 {found_invalid}，需精简提示词")

    if issues:
        for issue in issues:
            print(f"[CHECK] 提示词污染: {issue}")

    return {"pass": len(issues) == 0, "issues": issues}


def _check_negative_prompt(shots: list[dict]) -> dict:
    """检查逆向提示词是否添加（检查项6）."""
    warnings = []
    # 空镜关键词集合
    non_character_subjects = {"空镜", "荷塘", "池塘", "日落", "日出", "山", "水", "湖", "河", "田野",
                              "田埂", "村庄", "照月湾", "荷塘黄昏全景", "山峦", "空镜"}

    for shot in shots:
        idx = shot.get("index", "?")
        negative_prompt = shot.get("negative_prompt", "").strip()
        main_subject = shot.get("main_subject", "").strip()

        if not negative_prompt:
            warnings.append(f"镜头{idx}: 未添加逆向提示词，需补充")
        elif any(nc in main_subject for nc in non_character_subjects):
            # 空镜必须包含禁止人物的负向提示词
            required_terms = ["no people", "empty scene", "禁止人物", "空镜无人"]
            if not any(term in negative_prompt.lower() for term in [t.lower() for t in required_terms]):
                warnings.append(f"镜头{idx}: main_subject=\"{main_subject}\" 为空镜/场景，但负向提示词缺少禁止人物相关词（如 no people/empty scene/禁止人物）")

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 逆向提示词: [WARN] {warn}")

    return {"pass": len(warnings) == 0, "warnings": warnings}


def _check_prop_consistency(shots: list[dict]) -> dict:
    """检查道具连贯性（检查项2）：上一镜道具的状态、位置与下一镜是否一致."""
    issues = []
    warnings = []

    # 从 shots 提取每个镜头的道具信息
    # 简化实现：检查同一角色在不同镜头的道具是否矛盾
    for i, shot in enumerate(shots):
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        narration = shot.get("narration_segment", "")

        # 常见道具关键词
        props = ["剑", "刀", "枪", "鞭", "扇", "笛", "琴", "棋", "书", "画",
                 "酒", "茶", "杯", "碗", "灯", "烛", "火把", "包袱", "书信",
                 "令牌", "印信", "玉佩", "项链", "戒指", "手镯"]

        # 查找本镜头提到的道具
        mentioned_props = [p for p in props if p in prompt or p in narration]

        # 与下一镜头对比
        if i < len(shots) - 1:
            next_shot = shots[i + 1]
            next_idx = next_shot.get("index", "?")
            next_prompt = next_shot.get("prompt", "")
            next_narration = next_shot.get("narration_segment", "")
            next_mentioned = [p for p in props if p in next_prompt or p in next_narration]

            # 如果本镜有道具，下一镜完全没提，可能不连贯
            for prop in mentioned_props:
                if prop not in next_mentioned and "消失" not in next_prompt:
                    # 排除明显场景切换的情况（如从室内到室外）
                    # 这里简化处理，仅作警告
                    if any(kw in next_prompt for kw in ["继续", "仍然", "依然", "仍是"]):
                        issues.append(
                            f"镜头{idx}→{next_idx}: 道具\"{prop}\"在镜头{idx}出现，"
                            f"但镜头{next_idx}未提及，可能道具连贯性问题"
                        )

    if issues:
        for issue in issues:
            print(f"[CHECK] 道具连贯性: {issue}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


def _check_shot_transition(shots: list[dict]) -> dict:
    """检查镜头衔接（检查项4）：本镜结束画面与下一镜开始画面的衔接."""
    issues = []
    warnings = []

    for i in range(len(shots) - 1):
        curr_shot = shots[i]
        next_shot = shots[i + 1]

        curr_idx = curr_shot.get("index", "?")
        next_idx = next_shot.get("index", "?")

        curr_prompt = curr_shot.get("prompt", "")
        next_prompt = next_shot.get("prompt", "")

        # 检查 context_bridge 是否存在且有效
        curr_bridge = curr_shot.get("context_bridge", "").strip()
        if not curr_bridge:
            warnings.append(f"镜头{curr_idx}: 缺少 context_bridge 衔接描述")

        # 检查关键动作/位置衔接
        # 简化：检查"继续""然后""紧接着"等衔接词
        transition_words = ["继续", "然后", "紧接着", "随后", "随即", "紧接",
                           "瞬间", "刹那", "突然", "猛然", "骤然"]
        has_transition = any(tw in curr_prompt for tw in transition_words)
        has_bridge = curr_bridge and any(tw in curr_bridge for tw in transition_words)

        if not has_transition and not has_bridge:
            # 检查两个镜头之间的 location 是否相同（同一地点需要有衔接）
            curr_loc = curr_shot.get("location", "") or curr_shot.get("scene", "")
            next_loc = next_shot.get("location", "") or next_shot.get("scene", "")

            if curr_loc and next_loc and curr_loc != next_loc:
                # 地点切换时可以没有强衔接词
                pass
            elif curr_loc == next_loc and not has_bridge:
                warnings.append(
                    f"镜头{curr_idx}→{next_idx}: 同一地点({curr_loc})但缺少衔接词，"
                    f"可能画面跳跃"
                )

    if issues:
        for issue in issues:
            print(f"[CHECK] 镜头衔接: {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 镜头衔接: [WARN] {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


def _check_semantic_risk(shots: list[dict]) -> dict:
    """检查语义风险（检查项7）：是否存在隐晦的违规意图."""
    issues = []

    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        subtitle = shot.get("subtitle", "")
        narration = shot.get("narration_segment", "")

        search_text = prompt + subtitle + narration

        found_risks = [kw for kw in _SEMANTIC_RISK_KEYWORDS if kw in search_text]
        if found_risks:
            issues.append(f"镜头{idx}: 存在语义风险关键词 {found_risks}，需修改提示词")

    if issues:
        for issue in issues:
            print(f"[CHECK] 语义风险: {issue}")

    return {"pass": len(issues) == 0, "issues": issues}


def _check_platform_compliance(shots: list[dict]) -> dict:
    """检查平台规则（检查项8）：是否标注AI生成等内容."""
    warnings = []

    # 检查 prompt 是否包含 AI 生成相关标注
    # 注意：这是可选的合规要求，仅作警告
    ai_keywords = ["AI生成", "AI创作", "人工智能", "AIGC"]

    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")

        # 仅在第一个镜头检查（整体标注即可）
        if idx == 1:
            has_ai_mark = any(kw in prompt for kw in ai_keywords)
            if not has_ai_mark:
                warnings.append(
                    "建议在首镜头 prompt 中添加\"AI生成\"等标注，"
                    "以符合平台合规要求"
                )

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 平台规则: [WARN] {warn}")

    return {"pass": True, "warnings": warnings}

# v6.5: 视线方向检查
def _check_gaze_direction(shots):
    issues, warnings = [], []
    wrong_pattern = re.compile(r'[看盯望向朝][^，。,\.]{0,10}')
    correct_dirs = {'画左', '画右', '画上方', '画下方', '画前', '画后'}

    for shot in shots:
        idx = shot.get('index', '?')
        prompt = shot.get('prompt', '')

        wrong = wrong_pattern.findall(prompt)
        filtered = [w for w in wrong if not any(d in w for d in correct_dirs)]
        if filtered:
            warnings.append(f'镜头{idx}: 视线描述 {filtered} 建议用画左/画右等')

        has_correct = any(d in prompt for d in correct_dirs)
        if not has_correct and ('看向' in prompt or '盯着' in prompt):
            warnings.append(f'镜头{idx}: 建议用空间方向词(画左/画右/画上方)')

    for w in warnings:
        print(f'[CHECK] 视线方向: [WARN] {w}')
    return {'pass': True, 'issues': issues, 'warnings': warnings}


# v2.0 新增：正文净化规则检查
_BODY_PURIFICATION_FORBIDDEN = {
    # 2.1 禁止进入正文的词汇
    "画面", "分镜", "参考图", "镜头", "画面变",
    # 2.2 内部说明词
    "参考上一版", "接上文", "保持镜头不变", "本规则用于过滤",
    "为了方便剪辑", "继续完成对话", "尚未结束",
    # 2.3 泛称（除非是真正描述"人物"概念）
    "人物", "角色",
}


def _check_body_purification(shots: list[dict], hook: dict = None) -> dict:
    """检查正文是否包含净化规则禁止的词汇（v2.0 新增）。

    检查项：
    1. prompt 中是否出现"画面"、"分镜"、"参考图"、"镜头"等词
    2. 是否出现内部说明词
    3. 是否使用泛称而非具体角色名
    """
    issues = []
    warnings = []

    def check_shot_prompt(shot, idx):
        prompt = shot.get("prompt", "")
        if not prompt:
            return

        # 检查禁止词汇
        found_forbidden = []
        for word in _BODY_PURIFICATION_FORBIDDEN:
            if word in prompt:
                # 排除合理使用（如"画面中"出现在某些固定表达中）
                if word == "人物" and "人物关系" in prompt:
                    continue  # 允许"人物关系"这种专业术语
                found_forbidden.append(word)

        if found_forbidden:
            # 判断严重程度：如果在开头或连续出现，视为严重
            is_severe = any(prompt.startswith(w) for w in found_forbidden)
            if is_severe:
                issues.append(
                    f"镜头{idx}: prompt 开头包含净化规则禁止词 {found_forbidden}，"
                    f"这些词不应进入【正文】"
                )
            else:
                warnings.append(
                    f"镜头{idx}: prompt 包含净化规则禁止词 {found_forbidden}，"
                    f"建议移至【参考素材】或【制作说明】"
                )

    # 检查所有镜头
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:  # 跳过 hook
            continue
        check_shot_prompt(shot, idx)

    # 检查 hook
    if hook:
        check_shot_prompt(hook, 0)

    if issues:
        for issue in issues:
            print(f"[CHECK] 正文净化: [ISSUE] {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 正文净化: [WARN] {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v2.0 新增：输出结构检查
_OUTPUT_STRUCTURE_KEYWORDS = {
    "【参考素材】", "【正文】", "【制作说明】", "参考素材", "制作说明",
}


def _check_output_structure(script: dict) -> dict:
    """检查输出是否包含固定结构要素（v2.0 新增）。

    注：由于 JSON 结构本身已包含 shots/hook 等字段，此检查主要针对
    prompt 内部是否有结构化注释（可选）。
    """
    warnings = []
    shots = script.get("shots", [])

    # 检查 prompt 中是否包含结构化标记（作为参考）
    has_structure_marker = False
    for shot in shots:
        prompt = shot.get("prompt", "")
        if any(marker in prompt for marker in _OUTPUT_STRUCTURE_KEYWORDS):
            has_structure_marker = True
            break

    # 如果 LLM 主动添加了结构化注释，给出确认
    if has_structure_marker:
        print("[CHECK] 输出结构: 检测到结构化标记（【参考素材】【正文】【制作说明】）")

    # 注意：不强制要求 JSON 输出包含这些标记，因为 JSON 本身已是结构化格式
    # 此检查仅为日志确认
    return {"pass": True, "issues": [], "warnings": warnings}


# v2.0 新增：剧情事件轴分布检查（强化版）
def _check_narrative_arc_distribution(shots: list[dict], script: dict) -> dict:
    """检查剧情事件轴分布是否均匀（v2.0 新增，强化版）。

    检查项：
    1. 镜头是否均匀覆盖前/中/后三段剧情
    2. 高潮镜头是否在后半段
    3. 是否存在连续空镜/环境描写
    """
    issues = []
    warnings = []

    if not shots:
        return {"pass": True, "issues": [], "warnings": []}

    total = len(shots)
    # 按位置分为前/中/后三段
    first_third = total // 3
    last_third = total - total // 3

    # 检测高潮关键词
    climax_keywords = {"刺杀", "战斗", "对决", "反转", "死亡", "诀别", "暴露", "发现", "冲突", "搏杀", "跃出", "挥刀", "拔剑"}

    # 统计各段的事件密度
    events_in_thirds = [0, 0, 0]  # 前/中/后
    climax_in_thirds = [0, 0, 0]

    for i, shot in enumerate(shots):
        idx = shot.get("index", "?")
        if idx == 0:  # 跳过 hook
            continue

        narration = shot.get("narration_segment", "")
        main_subject = shot.get("main_subject", "")

        # 判断属于哪一段
        if i < first_third:
            tier = 0
        elif i < last_third:
            tier = 1
        else:
            tier = 2

        # 有 narration 视为有事件
        if narration and len(narration) > 5:
            events_in_thirds[tier] += 1

        # 检查是否是高潮镜头
        text_to_check = narration + main_subject
        if any(kw in text_to_check for kw in climax_keywords):
            climax_in_thirds[tier] += 1

    # 检查1：前段不能全是事件，后段不能没有事件
    if events_in_thirds[0] > 0 and events_in_thirds[2] == 0:
        warnings.append(
            f"事件分布不均：前段有 {events_in_thirds[0]} 个事件，后段无事件。"
            f"建议后半段增加关键剧情镜头。"
        )

    # 检查2：高潮应集中在后半段
    if climax_in_thirds[0] > 0 and climax_in_thirds[2] == 0:
        warnings.append(
            f"高潮分布不均：检测到 {sum(climax_in_thirds)} 个高潮镜头，全部集中在前中段。"
            f"建议将至少一个高潮点移至后半段（战斗/刺杀/反转等）。"
        )

    # 检查3：连续空镜检测
    empty_subjects = {"空镜", "荷塘", "日落", "日出", "山", "水", "湖", "田野", "村庄"}
    consecutive_empty = 0
    for shot in shots:
        ms = shot.get("main_subject", "")
        if any(e in ms for e in empty_subjects):
            consecutive_empty += 1
            if consecutive_empty >= 2:
                issues.append(
                    f"连续空镜：检测到 {consecutive_empty} 个连续空镜镜头，"
                    f"根据v2.0规则，背景内容应合并为1个空镜"
                )
        else:
            consecutive_empty = 0

    if issues:
        for issue in issues:
            print(f"[CHECK] 事件轴分布: [ISSUE] {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 事件轴分布: [WARN] {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.6 新增：人物船只位置检查（防"人在水面蹦跳"幻觉）
# 检查船只/水面场景中人物是否有明确的位置约束词
def _check_person_boat_position(shots: list[dict]) -> dict:
    """检查船只/水面场景中人物位置约束是否明确（防幻觉核心检查）。

    问题场景：prompt 说"小舟穿过荷叶，杨旭坐船头吹箫"，但 AI 可能理解为
    "人站在水面上跳"——因为缺少"端坐于"、"稳坐于"等强制位置约束词。

    检查逻辑：
    1. 检测涉及船只/水面的场景关键词
    2. 检查人物描述是否有明确的位置约束词（端坐、盘膝、稳坐、跪坐等）
    3. 检查是否包含可能导致误解的动作词（跳、蹦、穿过、跃等）
    4. 如发现问题，建议添加负面提示词

    Returns:
        dict: {"pass": bool, "issues": [str], "warnings": [str]}
    """
    issues = []
    warnings = []

    # 船只/水面场景关键词
    boat_keywords = ["船", "舟", "筏", "艇", "帆", "桨", "水面", "湖面", "河面", "荷塘", "池塘"]

    # 明确的位置约束词（正面清单）
    position_constraint_words = [
        "端坐", "稳坐", "盘膝", "跪坐", "正坐", "斜坐", "侧坐",
        "倚坐", "卧", "仰卧", "俯卧", "躺", "斜躺",
        "立于", "站在", "蹲于", "坐于", "位于",
        "固定于", "静止于", "不动",
    ]

    # 可能导致误解的动作词（需要配合位置约束）
    ambiguous_action_words = [
        "跳", "蹦", "跃", "冲出", "穿过", "跨越", "腾空", "飞起",
        "移动", "滑行", "漂移", "荡", "摇", "晃",
    ]

    # 需要添加的负面提示词模板
    negative_suggestions = [
        "不要在水面上跳", "不要蹦跳", "不要站立在水面",
        "不要在船上走动", "不要跳入水中", "不要从水面跃出",
    ]

    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        narration = shot.get("narration_segment", "")
        ff_desc = shot.get("ff_desc", "")

        # 合并检查文本
        check_text = prompt + ff_desc

        # 1. 检测是否是船只/水面场景
        is_boat_scene = any(kw in check_text for kw in boat_keywords) or any(kw in narration for kw in boat_keywords)

        if not is_boat_scene:
            continue

        # 2. 检测是否有人物描述（有 [角色名: 特征] 锚点）
        has_char_anchor = bool(re.search(r"\[[^\]]+[:：][^\]]+\]", prompt))

        if not has_char_anchor:
            # 没有角色锚点，可能是空镜，跳过
            continue

        # 3. 检查是否有明确的位置约束词
        has_position_constraint = any(
            constraint in check_text
            for constraint in position_constraint_words
        )

        # 4. 检查是否有歧义动作词
        has_ambiguous_action = any(
            action in check_text
            for action in ambiguous_action_words
        )

        # 5. 检查是否有负面提示词
        has_negative_prompt = shot.get("negative_prompt", "").strip()
        has_negative_about_boat = any(
            neg in (has_negative_prompt or "").lower()
            for neg in ["跳", "蹦", "水面", "船"]
        )

        # 判断是否需要警告
        if is_boat_scene and has_char_anchor:
            if has_ambiguous_action and not has_position_constraint:
                warnings.append(
                    f"镜头{idx}: 船只/水面场景检测到歧义动作词但缺少位置约束词，"
                    f"建议添加\"端坐\"、\"稳坐\"、\"盘膝\"等明确位置描述"
                )

            if has_ambiguous_action and not has_negative_about_boat:
                warnings.append(
                    f"镜头{idx}: 船只/水面场景建议添加负面提示词防止幻觉，"
                    f"如：\"不要在水面蹦跳、不要站立在水面\""
                )

    if issues:
        for issue in issues:
            print(f"[CHECK] 船只位置约束: {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 船只位置约束: [WARN] {warn}")

    # 宽松模式：仅警告， 不阻断
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.8 新增：情绪可视化检查（将抽象情绪词转化为具体肢体动作）
def _check_emotion_visualization(shots: list[dict], hook: dict = None) -> dict:
    """检查 prompt 中的抽象情绪词是否已转化为可视化动作。

    检查逻辑：
    1. 检测 prompt 中是否出现抽象情绪词（悲伤、愤怒、迷茫、冷酷等）
    2. 如出现抽象情绪词，必须同时包含对应的可视化动作描述
    3. 提供常见情绪词的可视化动作对照表

    Args:
        shots: 分镜列表
        hook: 钩子镜头（可选）

    Returns:
        dict: {"pass": bool, "issues": [str], "warnings": [str]}
    """
    issues = []
    warnings = []

    # 抽象情绪词列表（禁止直接使用）
    abstract_emotion_words = [
        "悲伤", "伤心", "难过", "痛苦", "哀伤", "悲痛", "哀愁", "忧郁", "忧伤",
        "愤怒", "生气", "恼怒", "怒火", "愤恨", "怒气", "狂怒", "暴怒",
        "迷茫", "困惑", "茫然", "无助", "彷徨", "犹豫", "纠结",
        "冷酷", "冷漠", "无情", "冰冷", "冷淡", "淡漠", "麻木",
        "开心", "高兴", "快乐", "喜悦", "愉快", "欣喜", "欢乐", "欢笑",
        "害怕", "恐惧", "惊恐", "畏惧", "胆怯", "惊慌", "恐慌",
        "惊讶", "震惊", "惊奇", "意外", "错愕", "愕然",
        "温柔", "温柔地", "柔情", "温情", "温馨", "温暖",
        "严肃", "严厉", "严厉地", "庄重", "郑重", "正经",
        "紧张", "紧张地", "焦虑", "焦急", "急迫", "紧迫",
        "害羞", "羞涩", "腼腆", "不好意思", "脸红",
        "镇定", "冷静", "沉稳", "冷静地", "淡定",
    ]

    # 情绪词 → 可视化动作对照表
    emotion_to_action = {
        "悲伤": "低头垂眸、眼眶泛红、双手紧握、肩膀颤抖、泪水滑落、嘴角下垂",
        "愤怒": "咬牙切齿、眉头上挑、额头青筋跳动、猛拍桌面、胸口起伏、呼吸急促",
        "迷茫": "眼神空洞、眉头紧锁、手指无意识摩挲、望向远处、嘴唇微张、叹息",
        "冷酷": "面部肌肉僵硬、嘴角下沉、目光如刀、手指缓慢擦拭兵刃、不带感情",
        "开心": "眉眼弯弯、嘴角上扬、露出牙齿、身体前倾、拍手、跳跃",
        "害怕": "瞳孔放大、脚步后退、双手护胸、身体颤抖、面色苍白、冷汗直流",
        "惊讶": "眼睛瞪大、嘴巴张开、眉毛上扬、身体后仰、双手举起",
        "温柔": "眼神柔和、嘴角含笑、动作轻缓、声音软糯、轻轻抚摸",
        "严肃": "眉头紧锁、表情凝固、目光犀利、声音低沉、双手交叉胸前",
        "紧张": "吞咽口水、频繁眨眼、搓手、踱步、咬唇、肌肉紧绷",
        "害羞": "低头、脸颊泛红、绞手指、眼神躲闪、声音变小、扭捏",
        "镇定": "呼吸平稳、目光坚定、表情平静、站立不动、声音平稳",
    }

    def check_shot_emotions(shot, idx):
        """检查单个镜头的情绪可视化"""
        prompt = shot.get("prompt", "")
        motion_desc = shot.get("motion_desc", "")

        # 合并检查文本
        full_text = prompt + " " + motion_desc

        for emotion in abstract_emotion_words:
            if emotion in full_text:
                # 检查是否同时包含对应的可视化动作
                has_visual_action = False

                if emotion in emotion_to_action:
                    # 检查是否包含该情绪对应的任何可视化动作词
                    action_words = emotion_to_action[emotion].split("、")
                    for action in action_words:
                        if action in full_text:
                            has_visual_action = True
                            break

                if not has_visual_action:
                    # 提供具体的修改建议
                    suggested_actions = emotion_to_action.get(emotion, "具体肢体动作描述")
                    issues.append(
                        f"镜头{idx}: prompt 中出现抽象情绪词 \"{emotion}\"，"
                        f"必须转化为可视化动作！建议添加：{suggested_actions}"
                    )

    # 检查所有镜头
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:  # 跳过 hook
            continue
        check_shot_emotions(shot, idx)

    # 检查 hook
    if hook:
        check_shot_emotions(hook, 0)

    # 输出检查结果
    for issue in issues:
        print(f"[CHECK] 情绪可视化: [ISSUE] {issue}")

    for warn in warnings:
        print(f"[CHECK] 情绪可视化: [WARN] {warn}")

    # 宽松模式：仅警告， 不阻断（因为 LLM 可能用其他方式表达情绪）
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.7 新增：首尾帧描述完整性检查
def _check_ff_lf_desc_completeness(shots: list[dict], hook: dict = None) -> dict:
    """检查 ff_desc 和 lf_desc 的完整性和差异性（防静态视频关键）。

    检查逻辑：
    1. lf_desc 禁止为空
    2. ff_desc 和 lf_desc 必须有明显差异（否则生成的视频只是静态淡入淡出）
    3. hook 镜头（index=0）也必须遵守同样规则

    Args:
        shots: 分镜列表
        hook: 钩子镜头（可选）

    Returns:
        dict: {"pass": bool, "issues": [str], "warnings": [str]}
    """
    issues = []
    warnings = []

    # 检查普通镜头
    for shot in shots:
        idx = shot.get("index", "?")
        ff_desc = shot.get("ff_desc", "").strip()
        lf_desc = shot.get("lf_desc", "").strip()

        # 检查 lf_desc 是否为空
        if not lf_desc:
            issues.append(
                f"镜头{idx}: lf_desc（尾帧描述）为空，生成的视频将只是静态图片过渡！"
                f"必须填写尾帧画面描述（动作结束后的静止状态）"
            )
            continue

        # 检查 ff_desc 是否为空
        if not ff_desc:
            issues.append(
                f"镜头{idx}: ff_desc（首帧描述）为空，生成的视频将无法生成首帧图片！"
            )
            continue

        # 检查首尾帧是否相同或过于相似（去除空格和标点后比较）
        import re
        ff_normalized = re.sub(r'[\s　，。！？、：；""''（）【】《》]', '', ff_desc)
        lf_normalized = re.sub(r'[\s　，。！？、：；""''（）【】《》]', '', lf_desc)

        # 计算相似度
        if ff_normalized == lf_normalized:
            issues.append(
                f"镜头{idx}: ff_desc 和 lf_desc 完全相同，生成的视频将只是静态图片的淡入淡出！"
                f"ff_desc=\"{ff_desc}\", lf_desc=\"{lf_desc}\""
            )
        elif ff_normalized in lf_normalized or lf_normalized in ff_normalized:
            # 检查是否有明显的动作变化词
            action_keywords = ["跃", "跳", "冲", "飞", "跑", "倒下", "倒地", "刺", "劈", "砍",
                             "挥", "甩", "落", "起", "出", "入", "进", "出"]
            has_action_change = any(kw in lf_desc for kw in action_keywords)
            if not has_action_change:
                warnings.append(
                    f"镜头{idx}: ff_desc 和 lf_desc 相似度过高，生成的视频可能缺乏动态效果。"
                    f"建议在 lf_desc 中添加动作结束后的状态描述"
                )

    # 检查 hook 镜头（如果有）
    if hook and isinstance(hook, dict):
        idx = 0
        ff_desc = hook.get("ff_desc", "").strip()
        lf_desc = hook.get("lf_desc", "").strip()

        if not lf_desc:
            issues.append(
                f"钩子镜头(index=0): lf_desc（尾帧描述）为空，生成的视频将只是静态图片过渡！"
            )
        elif not ff_desc:
            issues.append(
                f"钩子镜头(index=0): ff_desc（首帧描述）为空，生成的视频将无法生成首帧图片！"
            )
        else:
            import re
            ff_normalized = re.sub(r'[\s　，。！？、：；""''（）【】《》]', '', ff_desc)
            lf_normalized = re.sub(r'[\s　，。！？、：；""''（）【】《》]', '', lf_desc)
            if ff_normalized == lf_normalized:
                issues.append(
                    f"钩子镜头(index=0): ff_desc 和 lf_desc 完全相同，生成的视频将只是静态图片的淡入淡出！"
                )

        # 检查 hook 的 scene 字段是否包含具体地点
        hook_scene = hook.get("scene", "").strip()
        if not hook_scene:
            issues.append(
                f"钩子镜头(index=0): scene（场景名称）为空，必须填写！"
            )
        elif len(hook_scene) < 4:  # 太短可能是模糊词
            warnings.append(
                f"钩子镜头(index=0): scene=\"{hook_scene}\" 可能过于模糊，建议填写更具体的场景名称"
            )

        # 检查 hook 的 location 字段（新增）
        hook_location = hook.get("location", "").strip()
        if not hook_location:
            issues.append(
                f"钩子镜头(index=0): location（物理地点）为空，必须填写具体地点！"
            )
        elif len(hook_location) < 3:  # 太短
            warnings.append(
                f"钩子镜头(index=0): location=\"{hook_location}\" 可能过于模糊，建议填写更具体的地点如\"照月湾湖面小舟\""
            )

    if issues:
        for issue in issues:
            print(f"[CHECK] 首尾帧完整性: {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 首尾帧完整性: [WARN] {warn}")

    # 硬性检查：lf_desc 不能为空，首尾帧不能完全相同
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.5 新增：分镜与原文比对（防幻觉核心 - 检测编造内容）
# 检查 prompt 中的元素是否都能在 narration_segment 中找到依据
def _check_script_novel_alignment(script: dict) -> dict:
    """检查分镜内容是否超出原文范围（防幻觉核心检查）。

    检查逻辑：
    1. 从 prompt 提取所有实体（人名、物品名、场景名）
    2. 检查这些实体是否都在 narration_segment 中出现
    3. 如果出现 narration 中没有的元素，标记为可能的"幻觉编造"

    Returns:
        dict: {"pass": bool, "issues": [str], "warnings": [str]}
    """
    shots = script.get("shots", [])
    characters = script.get("characters", [])
    if not shots:
        return {"pass": True, "issues": [], "warnings": []}

    # 提取已知角色名
    known_chars = {c.get("identifier", "") for c in characters if c.get("identifier")}

    # 常见实体模式（人名、物体、场景关键词）
    # 简单实现：检测 narration 未提但 prompt 出现的名词
    issues = []
    warnings = []

    for shot in shots:
        idx = shot.get("index", "?")
        narration = shot.get("narration_segment", "")
        prompt = shot.get("prompt", "")
        ff_desc = shot.get("ff_desc", "")
        lf_desc = shot.get("lf_desc", "")

        # 合并检查文本
        check_text = prompt + ff_desc + lf_desc

        # 检查已知角色是否在 narration 中出现
        for char_name in known_chars:
            if char_name in check_text and char_name not in narration:
                # 排除"对话提到但画面未出现"的情况（那是另一种检查）
                # 这里检测的是：prompt 提到但 narration 根本没说
                warnings.append(
                    f"镜头{idx}: prompt 出现角色\"{char_name}\"但 narration 中未提及，"
                    f"可能为幻觉编造"
                )

        # 检查明显的新增物体（简化版：常见道具/场景对比）
        # 启发式检查，不追求完美。定义一次，随后应用到每镜头 prompt/帧描述。
        suspicious_patterns = [
            # 新增人物类（narration 没提人名但 prompt 有人物描述）
            (r'一个[成年青老年男女]', '[人物角色]'),
            # 新增物品类
            (r'手持[刀剑枪戟扇笛]', '[新增道具]'),
        ]

        # 应用：帧描述/提示词里凭空出现、但 narration 未提及的具体人物/道具 → 疑似幻觉，仅 WARN
        for _pat, _label in suspicious_patterns:
            if re.search(_pat, check_text):
                warnings.append(
                    f"镜头{idx}: 画面描述出现未锚定的{_label}（{_pat}），"
                    f"narration 中无对应，可能为幻觉编造"
                )

    if issues:
        for issue in issues:
            print(f"[CHECK] 分镜原文对齐: {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 分镜原文对齐: [WARN] {warn}")

    # 宽松模式：仅警告，不阻断
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


def _check_transition_keywords(shots: list[dict]) -> dict:
    """检查转场关键词（v6.4 新增）★视频教程核心技巧★.

    检查项：
    1. clip_transition 字段是否有效（不为空、不是仅"硬切"）
    2. prompt 中是否包含场景对应的转场关键词
    3. 转场时长是否符合场景类型
    """
    issues = []
    warnings = []

    for i, shot in enumerate(shots):
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        clip_transition = shot.get("clip_transition", "").strip()
        narration = shot.get("narration_segment", "")

        # 1. clip_transition 字段检查
        if not clip_transition:
            warnings.append(f"镜头{idx}: clip_transition 为空，需添加转场方式")
        elif clip_transition in ("硬切", "硬切 cut", "cut"):
            # 硬切是允许的，但如果是战斗/特写场景，应有更丰富的转场描述
            # 仅作为 soft warning
            pass

        # 2. 判断场景类型（location 变化 = 真正的场景切换）
        is_combat = any(kw in narration for kw in _COMBAT_KEYWORDS)
        is_closeup = any(kw in narration for kw in _CLOSEUP_KEYWORDS)
        curr_loc = shot.get("location", "") or shot.get("scene", "")
        is_scene_switch = False
        if i < len(shots) - 1:
            next_loc = shots[i + 1].get("location", "") or shots[i + 1].get("scene", "")
            is_scene_switch = curr_loc != next_loc

        # 3. 转场关键词检查
        all_transition_kws = _TRANSITION_COMBAT | _TRANSITION_SCENE_SWITCH | _TRANSITION_CLOSEUP | _TRANSITION_GENERIC
        found_kws = [kw for kw in all_transition_kws if kw in prompt]

        if is_combat:
            # 打斗场景应该有冲击转场、0.2s 等关键词
            expected_kws = _TRANSITION_COMBAT
            missing_kws = expected_kws - set(found_kws)
            if not found_kws:
                warnings.append(
                    f"镜头{idx}: 检测为打斗场景但 prompt 缺少转场关键词，"
                    f"建议添加冲击转场、快速跟拍、动作连贯、转场时长0.2s"
                )
            elif missing_kws and "转场时长" not in found_kws:
                warnings.append(f"镜头{idx}: 打斗场景建议添加具体转场时长（如0.2s）")

        elif is_closeup:
            # 特写场景应该有模糊转场、0.3s 等关键词
            expected_kws = _TRANSITION_CLOSEUP
            missing_kws = expected_kws - set(found_kws)
            if not found_kws:
                warnings.append(
                    f"镜头{idx}: 检测为特写场景但 prompt 缺少转场关键词，"
                    f"建议添加模糊转场、镜头推近、节奏缓慢、转场时长0.3s"
                )

        elif is_scene_switch:
            # 场景切换应该有淡入淡出、0.5s 等关键词
            expected_kws = _TRANSITION_SCENE_SWITCH
            missing_kws = expected_kws - set(found_kws)
            if not found_kws:
                warnings.append(
                    f"镜头{idx}: 检测为场景切换但 prompt 缺少转场关键词，"
                    f"建议添加淡入淡出、镜头拉远、自然过渡、转场时长0.5s"
                )

    if issues:
        for issue in issues:
            print(f"[CHECK] 转场关键词: {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 转场关键词: [WARN] {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


def _run_prompt_checklist(script: dict) -> dict:
    """运行分镜提示词检查表（v6.3 新增）.

    整合8项核心检查：
    1. 人物连贯性 → _check_character_consistency（已存在）
    2. 道具连贯性 → _check_prop_consistency（新增）
    3. 镜头节奏 → 暂略（需 duration 信息，LLM 决定）
    4. 起点/终点衔接 → _check_shot_transition（新增）
    5. 提示词污染 → _check_prompt_pollution（新增）
    6. 逆向提示词 → _check_negative_prompt（新增）
    7. 语义风险 → _check_semantic_risk（新增）
    8. 平台规则 → _check_platform_compliance（新增）

    Returns:
        dict: {"pass": bool, "results": {每个检查项的结果}}
    """
    shots = script.get("shots", [])
    if not shots:
        return {"pass": True, "results": {}}

    results = {}

    # 1. 人物连贯性（已有）
    results["character_consistency"] = _check_character_consistency(script)

    # 2. 道具连贯性（新增）
    results["prop_consistency"] = _check_prop_consistency(shots)

    # 4. 镜头衔接（新增）
    results["shot_transition"] = _check_shot_transition(shots)

    # 5. 提示词污染（新增）
    results["prompt_pollution"] = _check_prompt_pollution(shots)

    # 6. 逆向提示词（新增）
    results["negative_prompt"] = _check_negative_prompt(shots)

    # 7. 语义风险（新增）
    results["semantic_risk"] = _check_semantic_risk(shots)

    # 8. 平台规则（新增）
    results["platform_compliance"] = _check_platform_compliance(shots)

    # v6.4 新增：转场关键词检查（视频教程核心技巧）
    results["transition_keywords"] = _check_transition_keywords(shots)

    # v6.5 新增：人物视线方向检查（镜内词域原则）
    results["gaze_direction"] = _check_gaze_direction(shots)

    # v6.5 新增：分镜与原文比对（防幻觉核心 - 检测编造内容）
    results["script_novel_alignment"] = _check_script_novel_alignment(script)

    # v6.6 新增：船只位置约束检查（防"人在水面蹦跳"幻觉）
    results["person_boat_position"] = _check_person_boat_position(shots)

    # v6.7 新增：首尾帧描述完整性检查（防静态视频）
    hook = script.get("hook")
    results["ff_lf_completeness"] = _check_ff_lf_desc_completeness(shots, hook)

    # v6.8 新增：情绪可视化检查（将抽象情绪词转化为具体肢体动作）
    results["emotion_visualization"] = _check_emotion_visualization(shots, hook)

    # v2.0 新增：正文净化规则检查（禁止词汇检查）
    results["body_purification"] = _check_body_purification(shots, hook)

    # v2.0 新增：输出结构检查
    results["output_structure"] = _check_output_structure(script)

    # v2.0 新增：剧情事件轴分布检查（强化版）
    results["narrative_arc"] = _check_narrative_arc_distribution(shots, script)

    # 综合判定：任一硬性检查失败则整体不通过
    # v6.5: 增加 script_novel_alignment 到硬性检查
    # v6.7: 增加 ff_lf_completeness 到硬性检查（lf_desc 为空会导致静态视频）
    # v2.0: 增加 body_purification 到硬性检查（正文净化规则禁止词）
    hard_fail_checks = ["prop_consistency", "shot_transition", "semantic_risk", "script_novel_alignment", "ff_lf_completeness", "body_purification"]
    pass_fail = all(
        results.get(check, {}).get("pass", True)
        for check in hard_fail_checks
    )

    return {"pass": pass_fail, "results": results}


_STOP_WORDS = {
    "一个", "没有", "不是", "什么", "怎么", "可以", "这个", "那个",
    "他们", "你们", "我们", "自己", "起来", "出来", "过来", "进去",
    "已经", "还是", "就是", "只是", "但是", "然而", "因为", "所以",
    "如果", "虽然", "而且", "或者", "之后", "之前", "然后", "最后",
    "不过", "那里", "这里",
}


def _strip_punct(text: str) -> str:
    """移除中文标点和空白,仅保留汉字."""
    return re.sub(r'[^一-鿿]', '', text)


def _fill_missing_new_fields(script: dict) -> None:
    """补全视频钩子版本的新字段（如果 LLM 未返回则用默认值填充）."""
    # 补全 shots
    for shot in script.get("shots", []):
        shot.setdefault("content", "")
        shot.setdefault("camera_movement", "")
        shot.setdefault("framing", "")
        shot.setdefault("lighting_color", "")
        shot.setdefault("duration", 1)
        shot.setdefault("negative_prompt", "")
        shot.setdefault("hook", "")
        shot.setdefault("time_stamp", "")
        shot.setdefault("cam_idx", 1)

    # 补全 hook（如果存在）
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        hook.setdefault("content", "")
        hook.setdefault("camera_movement", "")
        hook.setdefault("framing", "")
        hook.setdefault("lighting_color", "")
        hook.setdefault("duration", 1)
        hook.setdefault("negative_prompt", "")
        hook.setdefault("time_stamp", "")

    # 补全顶层字段
    script.setdefault("total_duration", 0)
    script.setdefault("style", "")


def parse_response(raw_text: str, novel_text: str = "") -> dict:
    """解析 LLM 返回的原始文本为 JSON， 含兜底修复。

    Args:
        raw_text: LLM 返回的原始文本
        novel_text: 小说原文（用于原文锚点验证）
    """
    global _novel_text_for_validation
    _novel_text_for_validation = novel_text

    print(f"[INFO] 模型返回文本长度: {len(raw_text)} 字符")
    try:
        script = safe_parse_json_object(raw_text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[WARN] safe_parse_json_object 失败: {e}")
        # 兜底：去掉围栏，取 {…} 区间，然后修复中文标点
        text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"模型未返回有效 JSON。响应:\n{text[:1000]}")
        candidate = text[start:end + 1]
        # 修复常见 JSON 错误（中文标点等）
        candidate = _fix_common_json_errors(candidate)
        candidate = re.sub(r",\s*}", "}", candidate)
        candidate = re.sub(r",\s*]", "]", candidate)
        try:
            script = json.loads(candidate)
            print(f"[INFO] 兜底解析成功（直接 JSON 区间+尾部逗号修复）")
        except json.JSONDecodeError as e2:
            dump_path = f"llm_response_dump_{datetime.now():%Y%m%d_%H%M%S}.txt"
            with open(dump_path, "w", encoding="utf-8") as df:
                df.write(text)
            pos = getattr(e2, "pos", 0)
            snippet = text[max(0, pos - 60):pos + 60]
            raise ValueError(
                f"模型返回的 JSON 无法解析。\n"
                f"错误: {e2}\n"
                f"出错位置上下文 (...{snippet}...)\n"
                f"完整响应已保存至 {dump_path}"
            ) from e2

    # v6.4 新增：补全新添加的字段（如果 LLM 没有返回）
    _fill_missing_new_fields(script)

    _validate_script(script)
    return script
