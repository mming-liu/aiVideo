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


# v5.0 新增必填字段列表
_REQUIRED_NEW_FIELDS = [
    "main_subject",
    "core_keywords",
    "fx_effect",
    "audio_cue",
    "mask_area",
    "color_tone",
    "clip_transition",
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


def _check_character_consistency(script: dict) -> dict:
    """校验人物一致性（v6.2 新增，纳入常规校验流程）.

    检查项：
    1. 角色锚点检查：main_subject 是已知角色时，prompt 必须有 [角色名: ...] 锚点
    2. 跨镜头特征一致性：同一角色在不同镜头中，静态特征（衣着/外貌）不应矛盾
    3. 旁白提到角色但画面缺失：narration 中明确提到角色名，但 prompt/fd_desc 无此人
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

    # --- 3. 旁白/字幕提到角色但画面可能缺失 ---
    for shot in shots:
        idx = shot.get("index", "?")
        narration = shot.get("narration_segment", "")
        prompt = shot.get("prompt", "")
        ff_desc = shot.get("ff_desc", "")
        lf_desc = shot.get("lf_desc", "")
        search_pool = prompt + ff_desc + lf_desc

        for char_id in defined_ids:
            if char_id in narration:
                # 旁白提到了这个角色，检查画面中是否出现了该角色
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
        "天空", "大地", "庭院", "回廊", "楼阁", "屋檐",
    }
    for shot in shots:
        idx = shot.get("index", "?")
        main_subject = shot.get("main_subject", "").strip()
        prompt = shot.get("prompt", "")
        ff_desc = shot.get("ff_desc", "")

        # 如果 main_subject 是空镜/场景/物体，检查是否不当出现人物
        is_non_char = any(nc in main_subject for nc in non_character_subjects)
        if is_non_char:
            # 检查 prompt 首段（锚点之前）是否有人物描述
            # 如果有 [角色名: 特征] 锚点，说明 LLM 明确加了人物，不算误报
            has_char_anchor = bool(re.search(r"\[[^\]]+[:：][^\]]+\]", prompt))
            if not has_char_anchor:
                # 模糊检测：prompt 中出现常见人物词但 main_subject 不匹配
                person_words = ["人物", "角色", "人影", "身影", "男子", "女子", "老人", "少年",
                              "公子", "姑娘", "刺客", "杀手", "官员", "士兵", "侍女"]
                found_persons = [pw for pw in person_words if pw in prompt]
                if found_persons:
                    # 检查是否 main_subject 本身就包含这些词（如"刺客出水"）
                    if not any(pw in main_subject for pw in found_persons):
                        warnings.append(
                            f"镜头{idx}: main_subject=\"{main_subject}\" 疑似空镜/场景，"
                            f"但 prompt 含有 {found_persons}，可能是空镜中不当出现人物"
                        )

    # --- 汇总 ---
    has_issues = len(issues) > 0 or len(warnings) > 0
    if has_issues:
        for issue in issues:
            print(f"[CHECK] 人物一致性: {issue}")
        for warn in warnings:
            print(f"[CHECK] 人物一致性: [WARN] {warn}")

    return {"pass": not bool(issues), "issues": issues, "warnings": warnings}


_STOP_WORDS = {
    "一个", "没有", "不是", "什么", "怎么", "可以", "这个", "那个",
    "他们", "你们", "我们", "自己", "起来", "出来", "过来", "进去",
    "已经", "还是", "就是", "只是", "但是", "然而", "因为", "所以",
    "如果", "虽然", "而且", "或者", "之后", "之前", "然后", "最后",
    "不过", "那里", "这里",
}


def _strip_punct(text: str) -> str:
    """移除中文标点和空白，仅保留汉字."""
    return re.sub(r'[^一-鿿]', '', text)


def parse_response(raw_text: str) -> dict:
    """解析 LLM 返回的原始文本为 JSON，含兜底修复."""
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
    _validate_script(script)
    return script
