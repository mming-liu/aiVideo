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
# 收敛原则：只保留 PROMPT_TEMPLATE 的 shot 输出 schema 里真实要求、且下游确实消费的字段。
# 曾经误列了 prompt 从未要求、下游也从未消费的 7 个死字段
# （mask_area/content/camera_movement/framing/lighting_color/negative_prompt/hook），
# 导致每次生成都对每镜刷「缺少字段」WARN——LLM 缺的只是提示词没让它输出的字段。
# 视频负向词由 shared/vimax_adapters.py 用适配器内置 NEGATIVE_PROMPT 统一注入，与逐镜 negative_prompt 无关。
_REQUIRED_NEW_FIELDS = [
    "main_subject",
    "core_keywords",
    "fx_effect",
    "audio_cue",
    "color_tone",
    "clip_transition",
    "duration",
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

    # --- 镜头数校验（软护栏：v4.1 起数量交由 LLM 决定，此处只拦极端废输出） ---
    reported_count = script.get("shot_count")
    actual_count = len(shots)
    from shared.config import SHOT_COUNT_MIN, SHOT_COUNT_MAX
    if actual_count < SHOT_COUNT_MIN or actual_count > SHOT_COUNT_MAX:
        print(f"[WARN] 镜头数 {actual_count} 超出 sanity 范围 [{SHOT_COUNT_MIN}, {SHOT_COUNT_MAX}]（仅提示，不阻断）")
    if reported_count is None:
        print(f"[WARN] JSON 中缺少 shot_count 字段，建议 LLM 在输出中包含此字段")
    elif not isinstance(reported_count, int):
        print(f"[WARN] shot_count={reported_count} 非整数，已忽略")
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

    # v6.7: 内容问题汇总——把「幻觉/语义风险/连续空镜」等真实内容缺陷从几十条
    # WARN 中单独高亮，便于在批量生成后一眼定位需人工审核的镜头。
    # （此前这些 [CHECK] 项与大量噪声混在一起，真实问题容易被淹没）
    _print_content_issue_summary(script, shots)


def _print_content_issue_summary(script: dict, shots: list[dict]) -> None:
    """高亮汇总真实内容缺陷（幻觉、语义风险、连续空镜），与噪声 WARN 区分.

    仅汇总不修复——这些需要 LLM 重新生成或人工判断，静默吞掉会让问题流入下游.
    """
    flagged = []

    # 1. 未锚定角色（幻觉）：_check_script_novel_alignment 已检测，这里重扫 prompt 里
    #    出现「一个[人物]」但 narration 无对应说话人的镜头
    defined_ids = frozenset(
        c.get("identifier", "") for c in script.get("characters", []) if c.get("identifier")
    )
    unanchored_pat = re.compile(r"一个[\[【]?[^\]】]{1,6}男女[\]】]?")
    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "") or ""
        m = unanchored_pat.search(prompt)
        if m:
            flagged.append(f"镜头{idx}: 画面出现未锚定角色「{m.group(0)}」，可能为幻觉编造")

    # 2. 语义风险关键词（硬性检查项，需人工确认是否合理）
    for shot in shots:
        idx = shot.get("index", "?")
        search_text = " ".join([
            shot.get("prompt", ""), shot.get("subtitle", ""),
            shot.get("narration_segment", ""),
        ])
        found = [kw for kw in _SEMANTIC_RISK_KEYWORDS if kw in search_text]
        if found:
            flagged.append(f"镜头{idx}: 语义风险关键词 {found}（需人工确认是否保留）")

    # 3. 连续空镜（事件轴分布硬性检查项）
    empty_subjects = {"空镜", "荷塘", "日落", "日出", "山", "水", "湖", "田野", "村庄"}
    run = 0
    for shot in shots:
        if any(s in (shot.get("main_subject", "") or "") for s in empty_subjects):
            run += 1
        else:
            run = 0
        if run == 2:
            flagged.append(f"镜头{shot.get('index', '?')} 起连续空镜，建议合并为 1 个空镜")
            run = 0  # 只报一次

    if flagged:
        print("\n[重点] 内容问题汇总 - 以下为需人工审核的真实缺陷:")
        for item in flagged:
            print(f"  [CHECK] {item}")
        print("[重点] 上述内容问题已标记，建议确认后继续下游\n")
    else:
        print("\n[OK] 内容问题汇总: 未发现幻觉/语义风险/连续空镜缺陷")


def _check_shot_size_diversity(shots: list[dict]) -> None:
    """校验镜头景别多样性：至少覆盖词表半数景别，无相邻同景别.

    注意：词表 storyboard_refs.SHOT_SIZES 实际只有 9 种可用景别，
    旧逻辑要求 ceil(N/2) 种（32 镜要 16 种）超出词表上限，结构性永远
    无法达标，造成大量误报刷屏。改为「覆盖词表半数」（5 种）即可。
    """
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
    # 词表上限 9 种，取半数（ceil(9/2)=5）作为多样性要求，不随镜头数线性增长
    min_required = min(5, max(3, -(len(storyboard_refs.SHOT_SIZES) // -2)))
    if unique_sizes < min_required:
        print(f"[WARN] 景别多样性不足：{unique_sizes} 种（{shot_sizes_used}），要求至少 {min_required} 种")
    # 相邻同景别：聚合连续 run，避免一屏刷 N 行相同警告（对话/打斗连续镜同景别是正常剪辑）
    run_start = None
    run_sz = None
    for i in range(len(shot_sizes_used) - 1):
        if shot_sizes_used[i] and shot_sizes_used[i] == shot_sizes_used[i + 1]:
            if run_sz is None:
                run_start, run_sz = i + 1, shot_sizes_used[i]
            # 检查是否同一连续 run 结束
            if i + 2 >= len(shot_sizes_used) or shot_sizes_used[i + 1] != shot_sizes_used[i + 2]:
                run_end = i + 2
                if run_end - run_start + 1 >= 3:  # 3 镜以上连续同景别才提示
                    print(f"[WARN] 镜头{run_start}~{run_end}连续{run_end - run_start + 1}镜景别相同（{run_sz}），建议插入景别变化")
                run_start, run_sz = None, None
        else:
            run_start, run_sz = None, None


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
    # 单字场景词（水/湖/船/灯/门等）作为子串会误伤人物/道具动作主体
    # （如「步摇落水」「张十三跃上船」「张十三举灯」）→ 用双保险：
    # 主体含已知角色名、或含道具名词时一律视为动作镜，不做空镜判定。
    non_character_subjects = {
        "空镜", "荷塘", "池塘", "日落", "日出", "田野", "田埂", "村庄",
        "天空", "大地", "庭院", "回廊", "楼阁", "屋檐", "照月湾", "荷塘黄昏全景", "山峦",
    }
    prop_hints = {
        "步摇", "剑", "刀", "枪", "扇", "笛", "琴", "棋", "书", "箭", "鞭",
        "玉佩", "令牌", "酒杯", "钓杆", "油纸伞", "发簪", "珠钗", "灯笼", "烛火",
    }
    for shot in shots:
        idx = shot.get("index", "?")
        main_subject = shot.get("main_subject", "").strip()
        prompt = shot.get("prompt", "")
        ff_desc = shot.get("ff_desc", "")
        lf_desc = shot.get("lf_desc", "")

        # 主体命中已知角色名或道具动词 → 是人物/道具动作镜，非空镜
        if any(cid and cid in main_subject for cid in defined_ids):
            continue
        if any(p in main_subject for p in prop_hints):
            continue

        # 只有主体是纯场景词才做空镜判定
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
# v6.10 新增：莞夏10种电影级转场关键词
_TRANSITION_CINEMATIC = {
    "空镜匹配", "过肩遮挡", "甩镜", "推近匹配", "拉远揭示",
    "光线闪白", "物体擦拭", "动作匹配", "焦点虚化", "声画延续",
}

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
    """检查逆向提示词是否添加（检查项6）.

    注意：PROMPT_TEMPLATE 的 shot 输出 schema 里没有逐镜 negative_prompt 字段，
    视频负向词由 shared/vimax_adapters.py 用适配器内置 NEGATIVE_PROMPT 统一注入。
    因此这里不检查"每镜有没有填 negative_prompt"（必然为空=误报），只对**真正纯空镜**
    校验 prompt 是否含禁止人物标记（空镜出人是唯一能在脚本层拦下的缺陷）。
    """
    warnings = []
    non_character_subjects = {"空镜", "荷塘", "池塘", "日落", "日出", "山峦", "田野", "村庄", "照月湾", "荷塘黄昏全景"}
    no_people_markers = {
        "无人物", "无人", "空无一人", "无一人", "没有人物", "禁止人物",
        "no people", "empty scene", "空镜无人", "画面无人物", "无人烟",
    }

    for shot in shots:
        idx = shot.get("index", "?")
        main_subject = shot.get("main_subject", "").strip()
        prompt = shot.get("prompt", "").strip()

        if any(nc in main_subject for nc in non_character_subjects):
            low = prompt.lower()
            if not any(m in low for m in no_people_markers):
                warnings.append(
                    f"镜头{idx}: main_subject=\"{main_subject}\" 为空镜/场景，"
                    f"但 prompt 缺少禁止人物标记（如：无人物/空无一人/no people）"
                )

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
    # 连续同地点缺衔接词的 run 聚合（同一场景连续对话/打斗缺衔接词是正常剪辑，
    # 逐镜刷屏无增量信息；聚合为一条，注明连续镜范围）
    same_loc_runs = []  # (start_idx, end_idx, loc)

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
            elif curr_loc and curr_loc == next_loc and not has_bridge:
                # 聚合连续同地点 run：同一 run 只在边界汇总，避免刷屏
                if same_loc_runs and same_loc_runs[-1][1] == curr_idx and same_loc_runs[-1][2] == curr_loc:
                    same_loc_runs[-1][1] = next_idx
                else:
                    same_loc_runs.append([curr_idx, next_idx, curr_loc])

    # 连续同地点缺衔接词的 run 只报一条（≥3 镜才提示；1-2 镜是正常剪辑节奏）
    for start_idx, end_idx, loc in same_loc_runs:
        if isinstance(start_idx, int) and isinstance(end_idx, int) and end_idx - start_idx >= 2:
            warnings.append(
                f"镜头{start_idx}→{end_idx}: 连续{end_idx - start_idx + 1}镜同一地点({loc})"
                f"但缺少衔接词，可能画面跳跃"
            )

    if issues:
        for issue in issues:
            print(f"[CHECK] 镜头衔接: {issue}")
    if warnings:
        for warn in warnings:
            print(f"[CHECK] 镜头衔接: [WARN] {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


def _check_action_overlap(shots: list[dict]) -> dict:
    """检查动作重叠法（v6.10 新增，莞夏/阿凯衔接方案核心）.

    同一地点连续动作的相邻镜头，镜头N+1 的开头动作必须承接镜头N 的结尾动作，
    否则视频拼接处动作断层（"上一镜收剑 → 下一镜直接拔剑"）产生割裂感。

    判定：连续同地点镜头，检查相邻镜头是否有重叠动作线索
      - 镜头N lf_desc 尾部的动作动词/主体，是否出现在镜头N+1 prompt/ff_desc 开头
      - 出现明确衔接词（继续/仍/还/接着/顺势/随/继而）或转场词 → 视为已衔接
    只 WARN 不阻断（动作承接由 LLM 决定，此处仅提示）。
    """
    warnings = []
    # 动作/持械/姿态类线索词——用于检测「上一镜结尾动作」是否延续到下一镜
    _ACTION_LINKS = {
        "剑", "刀", "笛", "箫", "琴", "扇", "袖", "手", "目光", "视线",
        "跪", "坐", "站", "立", "握", "持", "提", "举", "抬", "伸",
        "低", "垂", "转", "回", "退", "进", "入", "出", "落", "起",
    }
    _BRIDGE_WORDS = {"继续", "仍", "还", "接着", "顺势", "继而", "紧接",
                     "随即", "转场", "动作承接", "延续"}

    for i in range(len(shots) - 1):
        curr_shot, next_shot = shots[i], shots[i + 1]
        curr_idx = curr_shot.get("index", "?")
        next_idx = next_shot.get("index", "?")
        if curr_idx == 0 or next_idx == 0:
            continue

        curr_loc = curr_shot.get("location", "") or curr_shot.get("scene", "")
        next_loc = next_shot.get("location", "") or next_shot.get("scene", "")
        if not curr_loc or curr_loc != next_loc:
            continue  # 仅同地点连续动作需要动作承接（场景切换靠转场）

        curr_lf = curr_shot.get("lf_desc", "") or ""
        next_text = (next_shot.get("prompt", "") or "") + (next_shot.get("ff_desc", "") or "")
        if not curr_lf or not next_text:
            continue

        # 已有明确衔接词 → 跳过
        if any(w in next_text for w in _BRIDGE_WORDS):
            continue
        if "转场" in (next_shot.get("clip_transition", "") or ""):
            continue

        # 检查上一镜结尾动作线索是否延续到下一镜开头
        tail = curr_lf[-18:]  # 上一镜结尾末尾
        found = [w for w in _ACTION_LINKS if w in tail and w in next_text]
        if not found:
            warnings.append(
                f"镜头{curr_idx}→{next_idx}: 同地点连续动作，下一镜开头未承接"
                f"上一镜结尾动作（上一镜结尾「{tail}」），建议用动作重叠法"
                f"让下一镜从上一镜结束的动作继续"
            )

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 动作重叠: [WARN] {warn}")
    return {"pass": True, "issues": [], "warnings": warnings}


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
def _check_gaze_direction(shots, hook: dict = None) -> dict:
    """检查人物视线方向（镜内词域原则），并覆盖钩子镜头.

    v6.9: 传入 hook，对钩子镜头做同样的视线方向检查
    （此前 hook 视线零校验——钩子若只写「仰头尖叫」「双眼惊愕」
    这类无视线归属的表情，多人惊恐就没了来由）.
    v6.9: 误报过滤——「向」后紧跟纯方位词（向上/向后/向一侧）是运动/动作
    用法而非视线，不再提示；正则终止于分号/括号，避免跨句截断.
    v6.7: 触发词聚焦真正的视线动词。「向/朝」多为运动/站位/说话对象介词
    （朝杨旭站立、向船梢、向听香张嘴质问），被当成视线造成大量误报，
    移出触发集，只保留 看/盯/望/视/瞧/瞄 等视线动词.
    """
    issues, warnings = [], []
    # 终止于中文标点，避免跨句捕获；触发词只含真正的视线动词
    # v6.10: 「视」会被前缀「影视剧」/机位术语「平视 微仰视 俯视 仰视」误触发
    # （28 镜逐镜报 2 条、刷屏 56 行）——匹配前先剔除这些非视线词，真实视线(看/盯/望)不受影响。
    wrong_pattern = re.compile(r'[看盯望瞧瞄注视][^，。,\.；;：:（()）【】]{0,10}')
    correct_dirs = {'画左', '画右', '画上方', '画下方', '画前', '画后'}
    # 「向」+ 纯方位/动作词 = 运动用法，不是视线（如向上冲出/抓向船舷/偏向一侧）
    motion_after = re.compile(r'^向[上下左右前后内外旁中一侧过身]')

    def _check_one(shot: dict, label: str):
        nonlocal issues, warnings
        idx = shot.get('index', '?')
        prompt = shot.get('prompt', '')
        ff_desc = shot.get('ff_desc', '')
        text = f"{prompt} {ff_desc}"

        wrong = []
        # 先剔除机位/前缀术语，避免其内部「视」被误当视线（影视剧/平视/微仰视/俯视/仰视）
        clean_text = re.sub(r'影视剧|平视|微仰视|俯视|仰视', '', text)
        for m in wrong_pattern.finditer(clean_text):
            w = m.group()
            # 过滤运动用法（「向」后接方位词）+ 已含画左/画右的画内锚点
            if motion_after.match(w) or any(d in w for d in correct_dirs):
                continue
            wrong.append(w)
        if wrong:
            warnings.append(f'{label}{idx}: 视线描述 {wrong} 建议用画左/画右等')

        has_correct = any(d in text for d in correct_dirs)
        if not has_correct and ('看向' in text or '盯着' in text):
            warnings.append(f'{label}{idx}: 建议用空间方向词(画左/画右/画上方)')

    for shot in shots:
        _check_one(shot, '镜头')

    # 钩子镜头（index=0）同样纳入视线方向检查
    if hook and isinstance(hook, dict):
        _check_one(hook, '钩子镜头(index=0)')

    for w in warnings:
        print(f'[CHECK] 视线方向: [WARN] {w}')
    return {'pass': True, 'issues': issues, 'warnings': warnings}


# v2.0 新增：正文净化规则检查
_BODY_PURIFICATION_FORBIDDEN = {
    # 2.1 禁止进入正文的词汇
    # 注意：「画面」「镜头」已从禁止列表移除——SYSTEM_PROMPT 规则5 强制 prompt 以
    # 「景别/机位/运镜」开头，必然包含「镜头」；「画面」也是合理的视觉描述词。
    # 保留真正属于内部说明/泛称的禁止词。
    "分镜", "参考图", "画面变",
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
    # 2026-08-04 扩充：吸收 face-expressions.md（50种表情库），补全表情库涉及的抽象情绪词
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
        # face-expressions 扩充（2026-08-04）
        "委屈", "哭泣", "失落", "绝望", "心碎", "隐忍", "沮丧",
        "震惊", "难以置信", "石化", "惊喜", "错愕",
        "不满", "厌恶", "气呼呼",
        "高冷", "慵懒", "疏离", "困倦", "疲惫", "撒娇", "傲娇", "好奇",
        "释然", "得意", "苦笑", "假笑", "冷笑",
    ]

    # 情绪词 → 可视化动作对照表
    # 2026-08-04 扩充：与 _EMOTION_ACTION_MAP（自动修复）同源，保证校验建议与自动修复一致
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
        # face-expressions 扩充（2026-08-04）
        "震惊": "眉峰上扬、眼睛微睁、嘴角微张、眼神意外",
        "错愕": "嘴巴微张、眼神停滞、眉头微蹙、头微偏",
        "难以置信": "眉头微皱、眼睛半睁半疑、嘴巴微撇、头微偏",
        "石化": "表情完全僵住、眼睛瞪大、嘴巴半张、面部如雕塑静止",
        "惊喜": "眼睛睁大、眼角带笑、眉毛上扬、脸颊泛红",
        "委屈": "嘴角下撇、眼眶泛红、眼神湿润、头微低",
        "哭泣": "眼泪滑落、眼眶泛红、嘴角微撇、眉头紧锁",
        "失落": "眼神低垂、嘴角平直、面部无表情",
        "绝望": "眼神空洞、嘴角下撇、面部肌肉僵硬",
        "难过": "嘴角下撇、眼神湿润、眉毛微蹙、头微低",
        "沮丧": "眼神低垂、嘴角下撇、面部无精打采",
        "心碎": "眼神黯淡、嘴角下撇、面部肌肉松弛",
        "隐忍": "眼眶泛红、嘴角紧闭、眼神坚定、头微仰",
        "生气": "眉头微蹙、眼神锐利、嘴角下撇、头微抬",
        "暴怒": "眼睛瞪大、瞳孔收缩、嘴巴大张、面部肌肉扭曲",
        "不满": "眉头微蹙、眼神不满、嘴角下撇、头微偏",
        "厌恶": "眉头微皱、眼神厌恶、嘴角下撇、头微偏",
        "气呼呼": "脸颊鼓起、眉头微蹙、眼神不满、嘴巴微嘟",
        "高冷": "嘴角平直、眉头舒展、眼神淡漠、下颌线清晰",
        "慵懒": "眼皮半耷拉、眼神慵懒、嘴角平直、头微歪",
        "冰冷": "眼神冰冷、嘴角平直、眉头微蹙、下颌微抬",
        "疏离": "脸侧角度、眼神疏离、嘴角平直、冷硬光影",
        "困倦": "眼皮耷拉、眼神惺忪、眼角泛红、头微仰",
        "疲惫": "眼皮下垂、眼神疲惫、眼袋明显、嘴角下撇",
        "撒娇": "嘴巴嘟起、脸颊鼓起、眼神委屈带期待、头微歪",
        "傲娇": "头微扭、嘴角下撇、眼神傲娇、眉峰微挑",
        "好奇": "头微歪、眼睛睁大、眼神好奇、嘴角微扬",
        "冷漠": "眼神平静无波、眉形平直、嘴角没有弧度、直视镜头",
        "释然": "眼神柔和稳定、眉头完全舒展、嘴角浮现极浅笑意",
        "得意": "嘴角上扬、眼神明亮、眉梢微挑、头微仰",
        "苦笑": "嘴角扯出僵硬弧度、眉头微蹙、眼神无奈、眼底带疲惫",
        "假笑": "嘴角上扬无笑意、眼神空洞、苹果肌僵硬、眉峰平直",
        "冷笑": "嘴角单侧上扬、眉峰微挑、眼神轻蔑、下颌微抬",
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


# ============================================================================
# v6.14 新增：ff_desc / motion_desc 时间线一致性检查（防动作时序矛盾）
# ============================================================================

# 完成态/进行态动词：表示动作「已发生/正在进行」——首帧必须是「动作开始前」的
# 定格，若首帧出现这些完成态动词，说明 LLM 把定格选在了动作结果时刻，与
# motion_desc 描述的动作推进（如"刺入"）矛盾：t=0 已刺中，motion 却又在刺。
_FF_COMPLETED_VERBS = (
    "已刺中", "已刺入", "刺中", "刺入", "扎进", "已刺", "刺进", "已中",
    "倒地", "倒下", "已倒", "已摔", "翻落", "坠地", "已落水", "栽倒",
    "已跃上", "已冲出", "已破水", "已没入", "已潜入", "已入水", "已反身",
    "已拔出", "已抽出", "已抽出刀", "已砍", "已劈", "已挥出", "已斩",
    "已断裂", "已折断", "已碎裂", "已炸开", "已开", "已抓", "已抓住",
    "已夺", "已夺过", "已夺下", "已扑倒", "已扑上", "已擒住", "已制服",
)


def _check_ff_motion_timeline(shots: list[dict], hook: dict = None) -> dict:
    """检查 ff_desc 与 motion_desc 的时间线一致性（防视频模型脑补假动作）.

    背景（code-audit 08-05 钩子视频）：hook ff_desc 写成「刀尖已刺中杨旭心口，
    杨旭仰面后倒」（动作结果时刻），而 motion_desc 写「前2秒：刺客破水而出、
    寒光刺中」（动作进行时）。t=0 已经刺中，0-2秒又要"破水而出、刺中"——
    动作刺了两遍，视频模型无法调和起点与动作，只能脑补一套假动作
    （站在水里→假跳→下刺→落回水里）。

    检查规则：ff_desc 出现「完成态动词」（已刺中/倒地/已跃上等）时，视为首帧
    定格选在动作结果时刻。若同时满足以下任一，判定为时序矛盾：
      a) motion_desc 包含同一动作的「进行态」动词（刺/跃/冲/扑/砍/劈/挥/落水等）
      b) motion_desc 含时间分段（"前N秒/2-4秒"），即动作确实在视频中推进

    纯静态镜头（无动作词）不受影响。此检查为 warning 级（不阻断），提示 LLM
    把 ff_desc 定格前移到「动作开始前/进行中」时刻。
    """
    import re
    issues = []
    warnings = []

    # 进行态动词：动作推进中（与完成态对应）
    _MOTION_PROGRESSIVE = (
        "刺", "跃", "冲", "扑", "砍", "劈", "挥", "斩", "落", "没入", "潜入",
        "反身", "出水", "入水", "破水", "倒纵", "拔", "抽", "抓", "夺",
        "撞", "击", "挥出", "跃起", "跃上",
    )
    _TIME_SEGMENT_RE = re.compile(r"前\d+秒|后\d+秒|\d+\s*[-–]\s*\d+\s*秒|共\d+秒|时长\d+秒")

    def _check_one(shot: dict, label: str):
        ff = shot.get("ff_desc", "").strip() or ""
        motion = shot.get("motion_desc", "").strip() or shot.get("prompt", "") or ""
        if not ff:
            return
        # 完成态动词检测
        completed = [v for v in _FF_COMPLETED_VERBS if v in ff]
        if not completed:
            return
        # motion 是否有进行态动作
        has_progressive = any(v in motion for v in _MOTION_PROGRESSIVE)
        has_time_seg = bool(_TIME_SEGMENT_RE.search(motion))
        if not (has_progressive or has_time_seg):
            return
        # 更严格：完成态动作与 motion 里同一动作词族冲突才告警（减少误报）。
        # 例：ff"已刺中" + motion"刺中/刺入" → 冲突；ff"已没入" + motion"落水" → 冲突。
        verb_families = {
            "刺": ("刺", "扎", "捅"),
            "跃": ("跃", "纵", "跳"),
            "冲": ("冲", "窜", "破水"),
            "没入": ("没入", "潜入", "入水", "落水"),
            "倒": ("倒", "仰", "后倒"),
            "挥": ("挥", "劈", "砍", "斩"),
        }
        conflict = False
        for comp in completed:
            fam = None
            for verb, members in verb_families.items():
                if comp.endswith(verb) or verb in comp:
                    fam = members
                    break
            if fam and any(m in motion for m in fam):
                conflict = True
                break
        if not conflict:
            return

        warnings.append(
            f"{label}: ff_desc 含完成态动词「{'、'.join(completed)}」（首帧定格在动作结果时刻），"
            f"而 motion_desc 又描述同一动作推进（{motion[:40]}...）。"
            f"t=0 已发生，motion 却又在发生 → 视频模型无法调和，会脑补假动作。"
            f"建议 ff_desc 定格在动作开始前/进行中时刻（如'刺客半身破水、刀高举未落下'）。"
        )

    for shot in shots:
        _check_one(shot, f"镜头{shot.get('index', '?')}")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 首帧时序: [WARN] {warn}")

    return {"pass": True, "issues": issues, "warnings": warnings}


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

    # v4.1 起 narration_segment 只放人物对白（「说话人道：台词」），单镜的 narration
    # 只含本镜说话人，不含同镜出场的其他角色。若仍按"逐镜 narration 必须提及 prompt 里
    # 每个角色"校验，几乎所有有群众角色的镜头都会误报"幻觉编造"（本镜叙事镜头 1-9 的
    # 杨旭/听香/刺客全部命中）。
    #
    # 正确逻辑：`known_chars` 来自 `script["characters"]`（由 extract_characters 从小说
    # 原文提取），这些角色是**已确立的**，prompt 中出现它们不算幻觉。只有 prompt 里出现
    # 但 `known_chars` 里根本没有的角色才是可疑的。
    issues = []
    warnings = []

    # 收集所有 prompt 里出现的角色名
    all_prompt_text = "".join(
        s.get("prompt", "") + s.get("ff_desc", "") + s.get("lf_desc", "")
        for s in shots
    )
    # 只检查 known_chars 之外的角色（已知角色不算幻觉）
    # 简化：不逐镜检查，只检查全章 prompt 里是否有 unknown 角色
    # （因为 known_chars 已经是从小说提取的，prompt 里出现的 unknown 角色才是幻觉）
    # 实际上这个检查很难做（需要 NER），所以暂时只保留 suspicious_patterns 检查

    # 检查明显的新增物体（简化版：常见道具/场景对比）
    # 启发式检查，不追求完美。定义一次，随后应用到每镜头 prompt/帧描述。
    suspicious_patterns = [
        # 新增人物类（narration 没提人名但 prompt 有人物描述）
        (r'一个[成年青老年男女]', '[人物角色]'),
        # 新增物品类
        (r'手持[刀剑枪戟扇笛]', '[新增道具]'),
    ]

    # 应用：帧描述/提示词里凭空出现、但 narration 未提及的具体人物/道具 → 疑似幻觉，仅 WARN
    for shot in shots:
        idx = shot.get("index", "?")
        check_text = (shot.get("prompt", "") + shot.get("ff_desc", "")
                      + shot.get("lf_desc", ""))
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


# 时长-内容密度校验（seedance2-skill 避坑第3条「内容过载」吸收）
# 过载场景示例：4-5秒镜头塞太多场景/动作。校验按时长分级：
#   特写/空镜 4s+，中景 5s+，远景/动作 6s+ 时检查动作密度与场景密度。
# 只 WARN 不阻断（时长与动作密度由 LLM 决定，此处仅提示 LLM 拆分）。
_ACTION_VERB_HINTS = (
    "跃出", "冲出", "扑向", "扑上", "刺向", "刺入", "劈砍", "挥刀", "拔剑",
    "转身", "回头", "抬头", "低头", "起身", "站起", "坐下", "跪倒", "摔倒",
    "倒地", "倒下", "跳入", "落水", "上岸", "下船", "上船", "奔跑", "疾奔",
    "追赶", "逃窜", "击倒", "撞开", "推开", "抓起", "丢出", "掷出", "扬起",
    "吹响", "吹箫", "弹琴", "挥袖", "拂袖", "撑伞", "掀起", "扯下", "扯开",
    "按", "抽", "甩", "挥", "刺", "砍", "劈", "踢", "蹬", "窜", "跃", "扑",
)
_SCENE_CHANGE_HINTS = (
    "转场", "淡入", "淡出", "黑场", "切至", "画面切", "切换到", "地点变化",
    "来到了", "走向", "赶往", "走出", "走进", "离开", "驶向", "驶出",
)

_DENSITY_MAX_ACTIONS = {
    # (max_duration, max_action_clauses) — 时长阈值 → 动作短语上限
    2: 1,   # ≤2s：1 个动作
    3: 1,   # ≤3s：1 个动作
    4: 2,   # ≤4s：2 个动作
    5: 2,   # ≤5s：2 个动作
}
_DENSITY_MIN_LEN = 8   # prompt 至少多少字才参与密度判定（过滤过短描述）
_DENSITY_WARN_THRESHOLD = 1.5   # 动作密度 = 命中数 / 期望上限，超过该倍则提示


def _check_content_density(shots: list[dict]) -> dict:
    """检查时长-内容密度是否过载（seedance2-skill 避坑第3条吸收）.

    问题场景：4-5秒镜头塞进多个场景切换或连续动作，视频模型难以在短时长内
    生成连贯画面（与规则 3.5「单镜头动作上限」、3.6「动作拆分」一致，此处
    从时长维度量化密度）。

    判定：
    1. 按 duration 分档：≤2s/≤3s 上限 1 个动作；≤4s/≤5s 上限 2 个动作
    2. prompt 中逗号分隔的动作短语数（含动作动词命中）超过档位上限 → 提示
    3. prompt 中出现多个场景切换词（转场/走向/切至）→ 提示可能塞了多地点
    """
    warnings = []
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:  # 跳过 hook（hook 独立校验）
            continue
        duration = shot.get("duration_sec") or shot.get("duration")
        try:
            duration = float(duration) if duration is not None else 5.0
        except (TypeError, ValueError):
            duration = 5.0
        prompt = shot.get("prompt", "") or ""
        if len(prompt) < _DENSITY_MIN_LEN:
            continue

        # 动作短语计数：逗号/分号分隔的短句，含动作动词的算 1 个
        clauses = [c.strip() for c in re.split(r"[，。；、,;]", prompt) if c.strip()]
        action_clauses = [
            c for c in clauses
            if any(v in c for v in _ACTION_VERB_HINTS)
        ]

        # 找档位：取 ≥duration 的最小档位；超过 5s 不再拦截（长镜头允许多动作）
        cap = None
        for dur, cap_n in sorted(_DENSITY_MAX_ACTIONS.items()):
            if duration <= dur:
                cap = cap_n
                break
        if cap is None:
            continue

        density = len(action_clauses) / cap if cap else 0
        if density >= _DENSITY_WARN_THRESHOLD:
            warnings.append(
                f"镜头{idx}: 时长 {duration:.1f}s 内动作短语 {len(action_clauses)} 个"
                f"（建议 ≤{cap}），可能内容过载——短镜头塞太多动作，"
                f"建议拆分镜头或精简动作（seedance2-skill 避坑3）"
            )

        # 场景切换词：短镜头内出现 ≥2 个地点变化词 → 疑似多场景堆叠
        scene_hits = [w for w in _SCENE_CHANGE_HINTS if w in prompt]
        if len(scene_hits) >= 2 and duration <= 5:
            warnings.append(
                f"镜头{idx}: 时长 {duration:.1f}s 内出现多个场景切换词 {scene_hits}，"
                f"短镜头疑似堆叠多地点，建议拆分为独立镜头"
            )

    if warnings:
        for w in warnings:
            print(f"[CHECK] 内容密度: [WARN] {w}")

    return {"pass": True, "issues": [], "warnings": warnings}


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
        all_transition_kws = (_TRANSITION_COMBAT | _TRANSITION_SCENE_SWITCH
                              | _TRANSITION_CLOSEUP | _TRANSITION_GENERIC
                              | _TRANSITION_CINEMATIC)
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


# v6.10 新增：情景剧运镜检查（抖音@莞夏 18种运镜吸收）
# 18种运镜按作用域分三类：
#   - 机位/角度（prompt 头部固定描述，可与运镜共存）：仰拍/俯拍/过肩/正反打/手持
#   - 运镜动作（一镜一运镜，堆叠会分散注意力）：推/拉/跟/摇/移/甩/环绕/变焦
#   - 速度/格式修饰（可叠加在单个运镜上）：慢动作/快动作/长镜头
_MOVEMENT_TYPE_TERMS = {
    "推镜": ("推近", "前推", "推镜", "推进", "推至"),
    "拉镜": ("拉远", "后拉", "拉镜", "拉出", "拉至"),
    "跟镜": ("跟拍", "跟随", "跟镜", "跟踪"),
    "摇镜": ("摇镜", "横摇", "纵摇", "摇摄", "上摇", "下摇"),
    "移镜": ("横移", "移镜", "侧移", "平移"),
    "甩镜": ("甩镜", "甩动", "甩向", "甩拍"),
    "环绕镜": ("环绕", "环拍", "绕行", "围绕", "360°", "360度", "180°"),
    "变焦镜": ("变焦", "推拉变焦", "急推", "急拉", "急速推近", "突然放大", "突然缩小"),
}
_ANGLE_TECHNIQUES = ("仰拍", "俯拍", "仰视", "俯视", "平视", "过肩", "正反打", "手持", "侧拍", "正对镜头")
_SPEED_TECHNIQUES = ("慢动作", "快动作", "长镜头")
_STATIC_MARKERS = ("固定", "静止", "不动", "静态", "锁机位", "定格")


def _check_camera_movement(shots: list[dict]) -> dict:
    """检查运镜使用（情景剧18种运镜吸收，v6.10 新增）.

    对应抖音@莞夏「18种情景剧运镜技巧」的三条落地规则：
    1. 运镜服务剧情：每个镜头必须有运镜/机位描述（或明确固定机位）
    2. 避免过度运镜：单镜只承载 1 种主导运镜，禁止堆叠多种运镜
    3. 运镜类型多样性：全片运镜类型不宜单一（关键场景用运镜强化，普通场景固定）

    机位/角度（仰拍/俯拍/过肩/手持等）属于 prompt 头部固定描述，可与运镜共存，
    不参与"堆叠"计数；速度修饰（慢动作/快动作/长镜头）可叠加在单个运镜上。
    """
    warnings = []
    used_techniques = set()  # 全片运镜类型集合（含机位/角度，用于多样性统计）

    for shot in shots:
        idx = shot.get("index", "?")
        text = " ".join([
            shot.get("prompt", ""),
            shot.get("ff_desc", ""),
            shot.get("motion_desc", ""),
        ])

        # 1. 命中运镜动作
        matched = {name for name, terms in _MOVEMENT_TYPE_TERMS.items() if any(t in text for t in terms)}
        used_techniques |= matched

        # 2. 命中机位/角度 + 速度修饰
        angle_hits = [a for a in _ANGLE_TECHNIQUES if a in text]
        speed_hits = [s for s in _SPEED_TECHNIQUES if s in text]
        used_techniques |= set(angle_hits) | set(speed_hits)

        # 3. 完全没有运镜/机位/固定描述 → 缺镜头语言
        if not matched and not angle_hits and not any(m in text for m in _STATIC_MARKERS) and "空镜" not in text:
            warnings.append(
                f"镜头{idx}: 缺少运镜/机位描述（如 推/拉/跟/环绕/仰拍/过肩，或注明「固定机位」）"
            )

        # 4. 单镜堆叠多种运镜 → 避免过度运镜（机位/速度修饰不计数；跟拍可与其他运镜
        #    复合——「跟随+环绕/推进/变焦」是跟拍镜的合法变体，不算堆叠）
        if len(matched) >= 2 and "跟镜" not in matched:
            warnings.append(
                f"镜头{idx}: 单镜堆叠多种运镜 {sorted(matched)}，"
                f"建议一镜一运镜（变化交给相邻镜头的 clip_transition）"
            )

    # 5. 全片运镜类型多样性（软检查：≥6镜才提示）
    if len(shots) >= 6 and len(used_techniques) < 3:
        warnings.append(
            f"全片运镜类型单一（仅 {sorted(used_techniques) or ['无']}），"
            f"建议关键场景用运镜强化（推/拉/环绕/甩等），普通场景固定机位"
        )

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 运镜检查: [WARN] {warn}")

    return {"pass": True, "issues": [], "warnings": warnings}


# v6.12 新增：高级运镜升级关键词库（抖音@AI开机 运镜提示词升级教程）
# 基础运镜词 → 对应的高级运镜提示词（高张力镜头命中时给出升级建议）。
# 与 v6.10 _check_camera_movement（一镜一运镜/多样性）互补：一个管"堆叠/缺失"，
# 一个管"基础词→特效词升级"。
_CAMERA_UPGRADE_MAP = {
    # 推
    "推镜头": "侵入式推进+轻微变焦扭曲",
    "缓慢前推": "侵入式推进+轻微变焦扭曲",
    "快速推近": "侵入式推进+轻微变焦扭曲",
    "镜头推进": "侵入式推进+轻微变焦扭曲",
    # 拉
    "拉镜头": "反向变焦后拉+视差空间剥离",
    "缓慢后拉": "反向变焦后拉+视差空间剥离",
    "快速拉出": "反向变焦后拉+视差空间剥离",
    "镜头拉远": "反向变焦后拉+视差空间剥离",
    # 摇 / 甩
    "横摇": "极速鞭甩+动态残影捕捉",
    "摇镜头": "极速鞭甩+动态残影捕捉",
    "甩镜头": "暴力轴向撕裂+画面畸变拉伸",
    "甩镜": "暴力轴向撕裂+画面畸变拉伸",
    "快速甩动": "暴力轴向撕裂+画面畸变拉伸",
    # 移
    "镜头横移": "斯坦尼康横移+前景遮挡",
    "横移": "斯坦尼康横移+前景遮挡",
    # 跟
    "镜头跟随": "POV视角+动态构图重构",
    "跟拍": "POV视角+动态构图重构",
    "跟踪拍摄": "POV视角+动态构图重构",
}
_CAMERA_UPGRADE_ADVANCED = {
    "侵入式推进", "变焦扭曲", "反向变焦", "视差", "空间剥离",
    "极速鞭甩", "动态残影", "斯坦尼康横移", "前景遮挡",
    "POV", "第一人称视角", "动态构图", "轴向撕裂", "画面畸变", "畸变拉伸",
}
# 高张力场景触发词：仅此类镜头提示升级（避免对对话/特写/空镜等低张力镜头产生噪声）
_CAMERA_UPGRADE_TENSION = _COMBAT_KEYWORDS | {
    "对峙", "逼近", "压迫", "杀意", "追击", "追杀", "奔跑",
    "疾驰", "逃跑", "潜入", "窃听", "埋伏", "陷阱", "危机",
    "离别", "孤独", "回忆", "凝望", "转身离去",
}


def _check_camera_upgrade(shots: list[dict]) -> dict:
    """检查基础运镜是否升级为高级运镜词（v6.12 新增）★抖音@AI开机 运镜提示词升级教程★.

    基础运镜描述（推/拉/摇/移/跟/甩）升级为带特效细节的高级提示词
    （侵入式推进/反向变焦后拉/极速鞭甩/斯坦尼康横移/POV视角/暴力轴向撕裂），
    画面张力与电影感直接提升。仅对高张力镜头（打斗/对峙/追逐/离别等）提示，
    低张力镜头不打扰；命中高级词则跳过。
    """
    issues = []
    warnings = []

    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "") or ""
        narration = shot.get("narration_segment", "") or ""

        # 仅高张力镜头才检查（避免规则5强制「[运镜]推/拉/摇/移/跟」前缀导致的噪声）
        is_tension = any(kw in narration for kw in _CAMERA_UPGRADE_TENSION)
        if not is_tension:
            continue

        if any(kw in prompt for kw in _CAMERA_UPGRADE_ADVANCED):
            continue

        found_basic = [basic for basic in _CAMERA_UPGRADE_MAP if basic in prompt]
        if found_basic:
            basic = found_basic[0]
            advanced = _CAMERA_UPGRADE_MAP[basic]
            warnings.append(
                f"镜头{idx}: 高张力镜头运镜仍为基础词「{basic}」，"
                f"建议升级为「{advanced}」（可补充 4K高清、电影感、动态模糊）"
            )

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 运镜升级: [WARN] {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# 8种AI人像专业光位（抖音「方块格子」教程吸收）
# 人像镜头的光影描述必须落到具体灯位（光源相对人物/镜头的位置+软硬光+阴影形状），
# 禁止"自然光""柔和光线"这种无位置、无来源的模糊光。
_LIGHT_POSITION_KEYWORDS = {
    "正面顺光", "顺光", "前侧光", "侧顺光", "蝴蝶光", "伦勃朗光", "伦勃朗",
    "侧光", "侧面光", "逆光", "背光", "轮廓光", "顶光", "顶光直射", "底光", "鬼光",
    "侧逆光", "轮廓光勾勒", "三分区", "明暗交界线", "三角亮区", "蝴蝶形阴影",
    "高光", "眼神光", "剪影光", "分区照明", "边缘光", "主光源",
}
# 具体光源动机词——即便不是人像光位，也是可落地的光源（不算模糊光，不提示）
_LIGHT_SOURCE_WORDS = {
    "烛火", "烛光", "灯笼", "篝火", "火把", "油灯", "火盆",
    "月光", "日光", "天光", "窗光", "夕阳", "旭日", "晨光",
}
# 无位置、无来源的模糊光描述——出现即提示补充具体灯位
_VAGUE_LIGHT_WORDS = {"自然光", "柔和光", "柔和光线", "柔光", "自然光线", "柔和光照"}
# 情绪光位映射：命中情绪词时建议的光位（供提示文案引用）
_EMOTION_TO_LIGHT_HINT = {
    "浪漫": "逆光", "神秘": "伦勃朗光", "深邃": "伦勃朗光", "恐怖": "底光",
    "诡异": "底光", "危险": "底光", "压抑": "顶光", "权威": "顶光",
    "威慑": "顶光", "硬朗": "侧光", "冲突": "侧光", "力量": "侧光",
    "温馨": "正面顺光", "亲和": "正面顺光", "清新": "前侧光", "精致": "蝴蝶光",
    "优雅": "蝴蝶光", "高级": "蝴蝶光", "氛围": "逆光", "剪影": "逆光",
}
# 人像判定标记（命中其一视为镜头里有人的脸/人物）
_PERSON_PATTERN = re.compile(
    r"\[[^\[\]]+[:：][^\[\]]+\]"               # 角色锚点 [名: 特征]
    r"|[男女][子士]"                            # 男子/女子/男士/女士
    r"|公子|姑娘|少年|少女|刺客|杀手|侠客|官吏|士兵|侍女|老翁|老妪|书生"
    r"|面孔|面部|脸颊|脸庞|面容|五官|眉"
)
# 纯场景主体（空镜/风景）——无人物时跳过光位检查（风景光位逻辑不同）
_EMPTY_SCENE_SUBJECTS = {
    "空镜", "荷塘", "池塘", "日落", "日出", "田野", "田埂", "村庄",
    "天空", "大地", "庭院", "回廊", "楼阁", "屋檐", "照月湾", "山峦",
    "水面", "湖面", "荷塘黄昏全景", "夕阳", "薄雾",
}
# 场景主体里的动作/道具词——main_subject 含这些但非人物名时，也视为有人（动作镜）
_ACTIVE_SCENE_HINTS = ("跃", "冲", "刺", "追", "杀", "对峙", "交锋", "挥手", "举", "抱")


def _check_lighting_position(shots: list[dict]) -> dict:
    """检查人像光位是否落到具体灯位（8种AI人像专业光位教程吸收）.

    问题场景：人像/特写镜头 prompt 写"自然光""柔和光线"，AI 无法确定光源
    从哪个方向来、在脸上形成什么阴影 → 光影平庸无故事感。

    判定（只 WARN 不阻断）：
    1. 有人的镜头出现模糊光词（自然光/柔和光线），且既无具体光位词、也无具体
       光源动机词（烛火/月光/窗光等）→ 提示补充灯位
    2. 出现情绪词（浪漫/诡异/权威…）但画面未用任何光位/光源 → 提示用光位强化情绪

    Args:
        shots: 分镜列表

    Returns:
        dict: {"pass": True, "issues": [], "warnings": [str]}
    """
    warnings = []
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:  # hook 由独立逻辑校验，此处跳过
            continue
        prompt = shot.get("prompt", "") or ""
        main_subject = shot.get("main_subject", "") or ""
        search_text = prompt + (shot.get("ff_desc", "") or "")

        has_person = bool(_PERSON_PATTERN.search(search_text))
        is_empty_subject = any(kw in main_subject for kw in _EMPTY_SCENE_SUBJECTS)
        if is_empty_subject and not has_person:
            continue  # 纯空镜/风景，无人 → 光位检查不适用

        # 具体灯位 vs 具体光源动机：灯位决定"光从哪来+阴影形状"（本检查核心），
        # 光源动机（烛火/月光等）只说明光源是什么，不说明位置——情绪配对仍需要灯位
        has_position = any(kw in prompt for kw in _LIGHT_POSITION_KEYWORDS)
        has_source = any(kw in prompt for kw in _LIGHT_SOURCE_WORDS)

        # 1. 模糊光词检测（仅人像镜头：非空镜、或空镜中有人）
        #    无灯位、无光源动机的"自然光/柔和光线" → 提示补充具体灯位
        vague_hits = [w for w in _VAGUE_LIGHT_WORDS if w in prompt]
        if vague_hits and not has_position and not has_source and has_person:
            warnings.append(
                f"镜头{idx}: 人像镜头使用模糊光描述 {vague_hits} 且无具体光位，"
                f"建议改为具体灯位（如：正面顺光/前侧光/蝴蝶光/伦勃朗光/侧光/"
                f"逆光/顶光/底光），灯位+画面特征+情绪效果三要素齐备"
            )

        # 2. 情绪词 → 推荐光位（人物/动作镜头，出现情绪但画面未用具体灯位时提示）
        if not has_position:
            emotion_hits = [e for e, _ in _EMOTION_TO_LIGHT_HINT.items() if e in search_text]
            for emotion in emotion_hits:
                suggested = _EMOTION_TO_LIGHT_HINT[emotion]
                warnings.append(
                    f"镜头{idx}: 画面含情绪词「{emotion}」，建议配 {suggested} 强化氛围"
                    f"（光位决定光影基调，情绪词决定色彩氛围）"
                )

    if warnings:
        for warn in warnings:
            print(f"[CHECK] 光位: [WARN] {warn}")

    return {"pass": True, "issues": [], "warnings": warnings}


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

    # v6.10 新增：动作重叠法检查（同地点连续动作的镜头衔接）
    results["action_overlap"] = _check_action_overlap(shots)

    # v6.10 新增：情景剧运镜检查（18种运镜，一镜一运镜 + 类型多样性）
    results["camera_movement"] = _check_camera_movement(shots)

    # v6.12 新增：高级运镜升级检查（基础运镜→特效词，高张力镜头才提示）
    results["camera_upgrade"] = _check_camera_upgrade(shots)

    # v6.11 新增：人像光位检查（8种AI人像专业光位，防模糊光）
    results["lighting_position"] = _check_lighting_position(shots)

    # v6.5 新增：人物视线方向检查（镜内词域原则，覆盖 hook）
    results["gaze_direction"] = _check_gaze_direction(shots, script.get("hook"))

    # v6.5 新增：分镜与原文比对（防幻觉核心 - 检测编造内容）
    results["script_novel_alignment"] = _check_script_novel_alignment(script)

    # v6.6 新增：船只位置约束检查（防"人在水面蹦跳"幻觉）
    results["person_boat_position"] = _check_person_boat_position(shots)

    # v6.7 新增：首尾帧描述完整性检查（防静态视频）
    hook = script.get("hook")
    results["ff_lf_completeness"] = _check_ff_lf_desc_completeness(shots, hook)

    # v6.14 新增：ff_desc / motion_desc 时间线一致性检查（防钩子动作时序矛盾）
    results["ff_motion_timeline"] = _check_ff_motion_timeline(shots, hook)

    # v6.8 新增：情绪可视化检查（将抽象情绪词转化为具体肢体动作）
    results["emotion_visualization"] = _check_emotion_visualization(shots, hook)

    # seedance2-skill 吸收：时长-内容密度检查（短镜头塞太多动作/场景）
    results["content_density"] = _check_content_density(shots)

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
        # duration_sec：视频时长权威来源，缺省时与 duration 同值（timecode 后续会覆盖）
        shot.setdefault("duration_sec", shot.get("duration", 1))
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
        hook.setdefault("duration_sec", hook.get("duration", 1))
        hook.setdefault("negative_prompt", "")
        hook.setdefault("time_stamp", "")

    # 补全顶层字段
    script.setdefault("total_duration", 0)
    script.setdefault("style", "")


# 说话人动作词（用于从「说话人道：台词」中提取说话人，及后续入画校验）
_SPEAKER_TAG_RE = re.compile(r"^([一-鿿]{2,8}?)(?:道|说|喊|喝|叫|问|答|曰|声道|声说)[:：]")
# 尾部说话动词剥离：正则可能把「喊道/扬声道/颤声道」的动词部分误纳入说话人名
_SPEAKER_TRAIL_VERB_RE = re.compile(r'(?:扬|怒|颤|冷|漫|惊|厉|高|低|轻|重|急|缓)?声?(?:道|说|喊|喝|叫|问|答|叹|曰)$')


def _extract_speaker(narration: str, defined_ids: frozenset | None = None) -> str:
    """从「说话人道：台词」格式中提取说话人。

    优先用角色库里的精确名（避免把「惊叫/颤声」等动词修饰语误当说话人），
    找不到再退回正则提取，并剥离尾部说话动词（「庄丁喊」→「庄丁」）。
    """
    n = (narration or "").strip()
    if not n:
        return ""
    if defined_ids:
        for cid in sorted(defined_ids, key=len, reverse=True):
            if n.startswith(cid):
                return cid
    m = _SPEAKER_TAG_RE.match(n)
    if not m:
        return ""
    speaker = m.group(1)
    return _SPEAKER_TRAIL_VERB_RE.sub("", speaker)


def post_fix_shot_prompts(script: dict) -> dict:
    """解析后、校验前的自动修复（v6.9 新增）。

    修复 LLM 最容易漏掉的、且能确定性修掉的点：
      1. 对白镜 prompt 末尾补「（用中文说）」声音指令（校验项 _check_audio_instruction 依赖）
      2. 对白镜若画面描述没写说话人，补一句开口动作 → 同时解决
         「说话人没入画」「实词覆盖率」两处误报
      3. 抽象情绪词 → 追加可视化动作（校验项 _check_emotion_visualization 依赖）
      3.5 模糊光词 → 具体光位（校验项 _check_lighting_position 依赖；
          8种AI人像专业光位教程吸收）
      4. 场景切换镜补转场关键词（校验项 _check_transition_keywords 依赖）
      5. 正文净化：从 prompt 主体中剥离「画面/镜头」等禁止词
    只追加不删除（净化除外），安全幂等。hook 由独立逻辑处理，不在此处。
    """
    defined_ids = frozenset(
        c.get("identifier", "") for c in script.get("characters", []) if c.get("identifier")
    )
    shots = script.get("shots", [])
    for i, shot in enumerate(shots):
        prompt = shot.get("prompt", "") or ""
        motion_desc = shot.get("motion_desc", "") or ""
        narration = shot.get("narration_segment", "") or ""
        subtitle = shot.get("subtitle", "") or ""
        is_dialogue = ("：" in narration) or (":" in narration) or ("：" in subtitle) or (":" in subtitle)

        # 1. 声音指令（跳过 hook）
        if shot.get("index") != 0 and is_dialogue:
            if "（用中文说）" not in prompt and "(用中文说)" not in prompt:
                prompt = prompt.rstrip() + "（用中文说）"

        # 2. 说话人入画（跳过 hook）
        if shot.get("index") != 0 and is_dialogue:
            speaker = _extract_speaker(narration, defined_ids) or _extract_speaker(subtitle, defined_ids)
            if speaker and speaker not in prompt:
                prompt = prompt.rstrip() + f"。画面中说话人「{speaker}」正开口说话，口型与台词同步"

        # 3. 光位量化：模糊光词（自然光/柔和光线）→ 具体灯位。先于情绪可视化，
        #    让「浪漫/诡异」等情绪词在 _fix_emotion_visualization 替换前仍可被
        #    _fix_lighting_quantization 用来挑选对应光位（校验项 _check_lighting_position 依赖；
        #    8种AI人像专业光位教程吸收）
        prompt = _fix_lighting_quantization(prompt)

        # 3.5 情绪可视化：抽象情绪词 → 追加可视化动作（prompt + motion_desc）
        prompt = _fix_emotion_visualization(prompt)
        motion_desc = _fix_emotion_visualization(motion_desc)

        # 4. 转场关键词：场景切换镜补淡入淡出（跳过 hook）
        if shot.get("index") != 0 and i < len(shots) - 1:
            curr_loc = shot.get("location", "") or shot.get("scene", "")
            next_loc = shots[i + 1].get("location", "") or shots[i + 1].get("scene", "")
            if curr_loc and next_loc and curr_loc != next_loc:
                if not any(kw in prompt for kw in ("淡入淡出", "转场", "自然过渡", "叠化")):
                    prompt = prompt.rstrip() + "。淡入淡出转场，转场时长0.5s"

        shot["prompt"] = prompt
        shot["motion_desc"] = motion_desc

    # hook 也需要光位量化 + 情绪可视化修复（光位先，让情绪词先驱动光位选择）
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        hook["prompt"] = _fix_lighting_quantization(hook.get("prompt", "") or "")
        hook["prompt"] = _fix_emotion_visualization(hook.get("prompt", "") or "")
        hook["motion_desc"] = _fix_emotion_visualization(hook.get("motion_desc", "") or "")

    return script


# 情绪词 → 可视化动作（与 _check_emotion_visualization 的 emotion_to_action 同源）
# 2026-08-04 扩充：吸收 face-expressions.md（50种动漫表情提示词库），把更多抽象情绪词
# 映射到面部表情模板（眉眼/嘴巴/脸颊/头位），使表情库在自动修复路径上真正生效。
# 值与 face-expressions.md 的表情模板保持一致（取面部核心，不含光影/色调——光影并入镜头层）。
_EMOTION_ACTION_MAP = {
    "悲伤": "低头垂眸，眼眶泛红，双手紧握",
    "愤怒": "咬牙切齿，眉头上挑，额头青筋跳动",
    "迷茫": "眼神空洞，眉头紧锁，望向远处",
    "冷酷": "面部肌肉僵硬，嘴角下沉，目光不带感情",
    "开心": "眉眼弯弯，嘴角上扬，身体前倾",
    "害怕": "瞳孔放大，脚步后退，身体颤抖",
    "惊恐": "眼睛瞪大，瞳孔收缩，嘴巴张开，面部肌肉扭曲",
    "惊讶": "眼睛瞪大，瞳孔收缩，眉毛上扬，嘴巴大张",
    "温柔": "眼神柔和，嘴角含笑，动作轻缓",
    "严肃": "眉头紧锁，表情凝固，目光犀利",
    "紧张": "眼神闪躲，眉头浅皱，嘴唇抿紧，鼻翼轻张",
    "害羞": "低头，脸颊泛红，耳朵发红，眼神低垂",
    "镇定": "呼吸平稳，目光坚定，表情平静",
    # ── face-expressions 扩充 ──
    "震惊": "眉峰上扬，眼睛微睁，嘴角微张，眼神意外",
    "错愕": "嘴巴微张，眼神停滞，眉头微蹙，头微偏",
    "难以置信": "眉头微皱，眼睛半睁半疑，嘴巴微撇，头微偏",
    "石化": "表情完全僵住，眼睛瞪大，嘴巴半张，面部如雕塑静止",
    "惊喜": "眼睛睁大，眼角带笑，眉毛上扬，脸颊泛红",
    "委屈": "嘴角下撇，眼眶泛红，眼神湿润，头微低",
    "哭泣": "眼泪滑落，眼眶泛红，嘴角微撇，眉头紧锁",
    "失落": "眼神低垂，嘴角平直，面部无表情",
    "绝望": "眼神空洞，嘴角下撇，面部肌肉僵硬",
    "难过": "嘴角下撇，眼神湿润，眉毛微蹙，头微低",
    "沮丧": "眼神低垂，嘴角下撇，面部无精打采",
    "心碎": "眼神黯淡，嘴角下撇，面部肌肉松弛",
    "隐忍": "眼眶泛红，嘴角紧闭，眼神坚定，头微仰",
    "生气": "眉头微蹙，眼神锐利，嘴角下撇，头微抬",
    "暴怒": "眼睛瞪大，瞳孔收缩，嘴巴大张，面部肌肉扭曲",
    "不满": "眉头微蹙，眼神不满，嘴角下撇，头微偏",
    "厌恶": "眉头微皱，眼神厌恶，嘴角下撇，头微偏",
    "气呼呼": "脸颊鼓起，眉头微蹙，眼神不满，嘴巴微嘟",
    "高冷": "嘴角平直，眉头舒展，眼神淡漠，下颌线清晰",
    "慵懒": "眼皮半耷拉，眼神慵懒，嘴角平直，头微歪",
    "冰冷": "眼神冰冷，嘴角平直，眉头微蹙，下颌微抬",
    "疏离": "脸侧角度，眼神疏离，嘴角平直，冷硬光影",
    "困倦": "眼皮耷拉，眼神惺忪，眼角泛红，头微仰",
    "疲惫": "眼皮下垂，眼神疲惫，眼袋明显，嘴角下撇",
    "撒娇": "嘴巴嘟起，脸颊鼓起，眼神委屈带期待，头微歪",
    "傲娇": "头微扭，嘴角下撇，眼神傲娇，眉峰微挑",
    "好奇": "头微歪，眼睛睁大，眼神好奇，嘴角微扬",
    "冷漠": "眼神平静无波，眉形平直，嘴角没有弧度，直视镜头",
    "释然": "眼神柔和稳定，眉头完全舒展，嘴角浮现极浅笑意",
    "得意": "嘴角上扬，眼神明亮，眉梢微挑，头微仰",
    "苦笑": "嘴角扯出僵硬弧度，眉头微蹙，眼神无奈，眼底带疲惫",
    "假笑": "嘴角上扬无笑意，眼神空洞，苹果肌僵硬，眉峰平直",
    "冷笑": "嘴角单侧上扬，眉峰微挑，眼神轻蔑，下颌微抬",
}


def _fix_emotion_visualization(prompt: str) -> str:
    """将抽象情绪词替换为可视化动作描述。"""
    for emotion, action in _EMOTION_ACTION_MAP.items():
        if emotion in prompt:
            prompt = prompt.replace(emotion, action)
    return prompt


# 8种光位可直接复制描述（与 portrait-lighting SKILL.md 一致，供自动修复拼接）。
# 注意：描述文字避免出现 _VAGUE_LIGHT_WORDS（自然光/柔和光/柔光等），防止 replace 二次命中。
_LIGHT_POSITION_DESC = {
    "正面顺光": "主光位于镜头正后方，光轴与镜头方向基本重合，接近人物眼睛高度，均匀照亮人物正面，面部阴影极少，左右亮度接近一致",
    "前侧光": "主光位于人物左前方约30°，略高于人物眼睛20-30°，中等面积软光，靠近光源侧额头颧骨明亮，鼻梁在另一侧形成短而柔和的阴影，暗侧眼睛保留细节",
    "蝴蝶光": "主光位于人物正前上方，水平角度0°，高于人物眼睛约35°-45°，额头鼻梁双侧颧骨对称受光，鼻尖正下方形成短小居中的蝴蝶形阴影，面颊下颌收紧",
    "伦勃朗光": "主光位于人物左前上方，水平方向约45°，高于人物眼睛30°，硬光，暗侧脸颊形成三角形亮区，明暗对比强，暗部保留细节",
    "侧光": "主光位于人物正侧方，与人物成90°角，硬光，面部一半明亮一半处于阴影，明暗交界线清晰，轮廓硬朗",
    "逆光": "主光位于人物正后方略高于头顶，硬光，人物正面处于阴影中，头发肩膀身体边缘形成明亮轮廓光，背景明亮人物与背景分离",
    "顶光": "主光位于人物正上方垂直向下照射，硬光，眼窝鼻梁下方下颌形成明显阴影，面部轮廓深邃",
    "底光": "主光位于人物正下方垂直向上照射，硬光，面部下方明亮上方处于阴影，阴影反常向上生长",
}


def _fix_lighting_quantization(prompt: str) -> str:
    """将模糊光描述替换为具体光位（8种AI人像专业光位教程吸收）.

    与 _check_lighting_position 的判定同源（_VAGUE_LIGHT_WORDS /
    _LIGHT_POSITION_KEYWORDS / _LIGHT_SOURCE_WORDS / _EMOTION_TO_LIGHT_HINT）：
    - 出现模糊光词（自然光/柔和光线）且无具体灯位、无光源动机 → 替换为具体光位
    - 命中情绪词时按其映射光位（浪漫→逆光、诡异→底光…），无情绪词默认前侧光
    只替换不删改其他内容，幂等（替换后 prompt 已含具体灯位词，二次调用跳过）。
    """
    vague_hits = [w for w in _VAGUE_LIGHT_WORDS if w in prompt]
    if not vague_hits:
        return prompt
    if any(kw in prompt for kw in _LIGHT_POSITION_KEYWORDS):
        return prompt
    if any(kw in prompt for kw in _LIGHT_SOURCE_WORDS):
        return prompt

    position = "前侧光"
    for emotion, suggested in _EMOTION_TO_LIGHT_HINT.items():
        if emotion in prompt:
            position = suggested
            break
    desc = _LIGHT_POSITION_DESC.get(position, _LIGHT_POSITION_DESC["前侧光"])
    replacement = f"{position}：{desc}"
    for vague in vague_hits:
        # count=1：仅替换首个命中，避免 desc 内文字被后续 vague 词二次命中
        prompt = prompt.replace(vague, replacement, 1)
    return prompt


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

    # v6.9 新增：自动修复（声音指令 + 说话人入画），必须在校验前执行，
    # 否则校验输出的「缺少（用中文说）」「说话人未入画」WARN 无法被消除
    post_fix_shot_prompts(script)

    _validate_script(script)
    return script
