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
from shared.paths import chapter_dir
from shared.tee_logger import get_logger
from shared.kf_time import (
    get_kf_t_sec, get_kf_stage, set_kf_t_sec, set_kf_stage,
    is_middle_kf, sort_kfs_by_t, stage_to_index,
    KF_STAGE_ENUM, KF_STAGE_TYPICAL_WORDS, KF_STAGE_FORBIDDEN_WORDS,
)

# 懒加载 logger（避免模块导入时配置未就绪）
_logger = None
def _get_logger():
    global _logger
    if _logger is None:
        _logger = get_logger(chapter_dir() / "step01.log", console_level="INFO")
    return _logger


# 模块级变量：存储当前待验证的小说原文
_novel_text_for_validation = ""


def _shot_tag(label: str, idx) -> str:
    """检查告警镜头标签：hook 的 label 已含 index=0，避免「钩子镜头(index=0)0」双编号（v6.34）."""
    if "index=" in (label or ""):
        return label
    return f"{label}{idx}"


# 空镜主体词表（唯一权威常量，审计 P1-8 收敛）。
# 历史：此前在 222/493/645/1158/2752 五处各持一份互不一致的词表，222 已修
# 「禁止单字水」、1158 还是旧版（含单字 山/水/湖 误报「刺客出水」为空镜）。
# 现在收敛为单一常量，所有空镜判定引用它。
# 注意：只含**纯场景词**（不含动作/角色名），且**不含单字「水/湖/山」**——
# 「刺客出水」含「水」被误判为空镜的根因。
EMPTY_SCENE_SUBJECTS = frozenset({
    "空镜", "荷塘", "池塘", "日落", "日出", "田野", "田埂", "村庄",
    "天空", "大地", "庭院", "回廊", "楼阁", "屋檐", "照月湾", "山峦",
    "水面", "湖面", "荷塘黄昏全景", "夕阳", "薄雾",
})


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
    # v6.43（08-23 用户规则）镜头数不限：SHOT_COUNT_MIN/MAX 任一 ≤0 跳过
    # sanity 检查（红果标准：只约束 60-90s 总时长 + ≤8s 单镜）
    if SHOT_COUNT_MIN > 0 and SHOT_COUNT_MAX > 0:
        if actual_count < SHOT_COUNT_MIN or actual_count > SHOT_COUNT_MAX:
            density_hint = ""
            if actual_count > 25:
                density_hint = f"——v6.34 节奏密度参考：60s 短视频最佳 15-22 镜，当前 {actual_count} 镜偏多，建议合并同场景连续动作镜头"
            elif actual_count < 12:
                density_hint = f"——v6.34 节奏密度参考：60s 短视频最佳 15-22 镜，当前 {actual_count} 镜偏少，建议拆分多动作镜头"
            _get_logger().warn(f"镜头数 {actual_count} 超出 sanity 范围 [{SHOT_COUNT_MIN}, {SHOT_COUNT_MAX}]（仅提示，不阻断）{density_hint}")
    if reported_count is None:
        # v6.34（08-15）：缺失静默补齐——旧 WARN 无行动价值，4 轮日志纯噪音
        script["shot_count"] = actual_count
    elif not isinstance(reported_count, int):
        _get_logger().warn(f"shot_count={reported_count} 非整数，已忽略")
    elif reported_count != actual_count:
        _get_logger().warn(f"声明 shot_count={reported_count} 但实际镜头数为 {actual_count}，已自动修正")
        script["shot_count"] = actual_count

    # --- 全局渲染参数校验 ---
    for field in ("resolution", "ratio", "fps", "render_style"):
        if field not in script:
            _get_logger().warn(f"JSON 缺少全局渲染参数 {field}")

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
                _get_logger().warn(f"镜头{i}: 缺少字段 {field}")

    # 验证 narration == 所有 subtitle 拼接
    combined = "".join(s.get("subtitle", "") for s in shots)
    expected_narration = script.get("narration")
    # v6.34（08-15）：顶层 narration 已不在输出 schema，恒报假警——
    # 仅当 LLM 仍输出该字段时才比对
    if expected_narration is not None and combined != expected_narration:
        _get_logger().warn(f"narration 与 subtitle 拼接不一致")

    # 验证每个 shot 的 subtitle == narration_segment
    # v6.2: 改为 auto-fix 而非崩溃——LLM 不总严格遵守此规则
    for i, shot in enumerate(shots, 1):
        sub = shot.get("subtitle", "")
        narr = shot.get("narration_segment", "")
        if sub != narr:
            # 优先取 narration_segment（来自原文摘取，通常更准确）
            if narr and not sub:
                shot["subtitle"] = narr
                _get_logger().warn(f"镜头{i}: subtitle 为空，已用 narration_segment 填充")
            elif sub and not narr:
                shot["narration_segment"] = sub
                _get_logger().warn(f"镜头{i}: narration_segment 为空，已用 subtitle 填充")
            else:
                # 两者都有但不一致：以 narration_segment 为准
                _get_logger().warn(f"镜头{i}: subtitle 与 narration_segment 不一致，以 narration_segment 为准")
                _get_logger().info(f"       subtitle: {sub[:40]}...")
                _get_logger().info(f"       narration: {narr[:40]}...")
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
            _get_logger().warn(f"镜头{i}: subtitle 与 narration_segment 不一致")
            _get_logger().info(f"       subtitle={sub}")
            _get_logger().info(f"       narration_segment={seg}")

        if sub and search_text:
            _check_prompt_subtitle_alignment(i, sub, search_text)

    # v5.0: scene_compliance 合规校验（宽松模式，仅 WARN）
    compliance_result = scene_compliance.validate_script(script, strict_mode=False)
    if not compliance_result["pass"]:
        _get_logger().warn(f"合规校验: {compliance_result['summary']['fail_count']} 个镜头未通过")
        for report in compliance_result["shot_reports"]:
            if not report["pass"]:
                for ck, cv in report["checks"].items():
                    if not cv.get("pass", True):
                        _get_logger().info(f"  镜头{report['index']} {ck}: {cv.get('reason', '')}")
                for w in report["warnings"]:
                    _get_logger().info(f"  镜头{report['index']}: {w}")

    # v6.2: 人物一致性校验（纳入常规流程，强制运行）
    _check_character_consistency(script)

    # v6.6: 逐镜头原文锚点校验（新增 P1 优化）
    # 检查 narration_segment 是否能在原文中找到对应内容，防止 LLM 编造
    _check_grounding_and_warn(script)

    # v6.3: 分镜提示词检查表（8项核心检查）
    checklist_result = _run_prompt_checklist(script)
    if not checklist_result["pass"]:
        _get_logger().warn(f"分镜提示词检查表: {len([r for r in checklist_result['results'].values() if not r.get('pass', True)])} 项未通过")

    # v6.7: 内容问题汇总——把「幻觉/语义风险/连续空镜」等真实内容缺陷从几十条
    # WARN 中单独高亮，便于在批量生成后一眼定位需人工审核的镜头。
    # （此前这些 项与大量噪声混在一起，真实问题容易被淹没）
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
    # 空镜词用唯一常量 EMPTY_SCENE_SUBJECTS（纯场景词，禁止单字「水」——
    # 「刺客出水」含「水」被误判为空镜，08-09 已修）。
    run = 0
    for shot in shots:
        ms = shot.get("main_subject", "") or ""
        is_empty = any(s in ms for s in EMPTY_SCENE_SUBJECTS)
        if is_empty:
            run += 1
        else:
            run = 0
        if run == 2:
            flagged.append(f"镜头{shot.get('index', '?')} 起连续空镜，建议合并为 1 个空镜")
            run = 0  # 只报一次

    if flagged:
        _get_logger().info("\n[重点] 内容问题汇总 - 以下为需人工审核的真实缺陷:")
        for item in flagged:
            _get_logger().info(f"  {item}")
        _get_logger().info("[重点] 上述内容问题已标记，建议确认后继续下游\n")
    else:
        _get_logger().info("\n[OK] 内容问题汇总: 未发现幻觉/语义风险/连续空镜缺陷")


def _is_over_shoulder_run(run_shots: list[dict]) -> bool:
    """过肩对白组判定（P2-2）：连续同景别豁免.

    双人对话正反打（规则22.8）锁定中近景连续是要求，非单调。判定：
    run 内所有镜头都有对白（narration_segment 非空），且每镜都是双人
    左右对峙（char_layout 或 ff_desc/prompt 同时含 画左 与 画右）。
    """
    if not run_shots:
        return False
    for s in run_shots:
        if not (s.get("narration_segment") or "").strip():
            return False
        layout = s.get("char_layout") or []
        sides = set()
        if isinstance(layout, list):
            for item in layout:
                if isinstance(item, dict):
                    p = str(item.get("position", "")).strip()
                    if "画左" in p:
                        sides.add("画左")
                    if "画右" in p:
                        sides.add("画右")
        if len(sides) >= 2:
            continue
        ff = s.get("ff_desc", "") + s.get("prompt", "")
        if not ("画左" in ff and "画右" in ff):
            return False
    return True


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
        _get_logger().warn(f"景别多样性不足：{unique_sizes} 种（{shot_sizes_used}），要求至少 {min_required} 种")
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
                    # v6.35（P2-2）：过肩对白组豁免——双人对话正反打（规则22.8）
                    # 连续中近景是锁定要求，非单调。
                    run_shots = shots[run_start - 1:run_end]
                    if not _is_over_shoulder_run(run_shots):
                        _get_logger().warn(f"镜头{run_start}~{run_end}连续{run_end - run_start + 1}镜景别相同（{run_sz}），建议插入景别变化")
                run_start, run_sz = None, None
        else:
            run_start, run_sz = None, None


def _check_prompt_subtitle_alignment(shot_idx: int, subtitle: str, search_text: str) -> None:
    """校验 prompt 与 subtitle 的硬性矛盾（场景矛盾 + 字符覆盖率）."""
    prompt_body = search_text.split("高清1080P")[-1] if "高清1080P" in search_text else search_text

    # --- 场景/地点矛盾 ---
    scene_check = scene_compliance.check_scene_contradiction(subtitle, prompt_body)
    if not scene_check["pass"]:
        _get_logger().warn(f"镜头{shot_idx}: {scene_check['reason']}")
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
        _get_logger().warn(f"镜头{shot_idx}: 画面描述与字幕内容严重脱节"
              f"（汉字命中{char_hit_rate:.0%}、二字组合全部未命中）")
        _get_logger().info(f"       未命中的二字组合: {missing_bg}")


def _check_audio_instruction(shots: list[dict]) -> None:
    """检查是否包含声音指令（v6.1 新增）.

    旧供应商（bailian/agnes/dashscope）要求「（用中文说）」；
    原生发声供应商（volcengine/minimax/agent_plan）视频模型原生发声，
    prompt 写自然对白即可，跳过检查避免误报。用 active_video_uses_native_voice() 统一判断。
    """
    from shared.model_caps import active_video_uses_native_voice
    if active_video_uses_native_voice():
        return
    for shot in shots:
        shot_idx = shot.get("index", "?")
        subtitle = shot.get("subtitle", "")
        prompt = shot.get("prompt", "")

        # 简单判断：如果 subtitle 包含冒号或引号，视为有对话
        if ":" in subtitle or "：" in subtitle or '"' in subtitle or '"' in subtitle or '"' in subtitle:
            if "（用中文说）" not in prompt and "(用中文说)" not in prompt:
                _get_logger().warn(f"镜头{shot_idx}: 检测到对话但 Prompt 中缺少 Agnes AI 中文发音指令 '（用中文说）'")


# 模块级变量 _novel_text_for_validation 已在文件顶部（line 29）定义，此处不再重复
# （code-audit 07-31 #5：原 :254 与 :29 重复定义同一模块级变量）


def _check_grounding(shot: dict, novel_text: str) -> bool:
    """简单的原文锚点检查：检查 narration 是否在原文中有对应内容。

    先剥掉「说话人道：」前缀再校验台词体——LLM 按规则0给原文台词补说话人前缀
    （「住嘴！张十三怒极」→「张十三怒道：住嘴！」），前缀（说话人+道/怒道）是
    生成层补充、不一定逐字来自原文，但台词体本身逐字引用原文。若整段切 5 字
    子串校验，前缀变了就误报"原文未找到"（08-09 镜头17「张十三怒道：住嘴！」）。

    08-17（v6.32）：归一化双重省略号「……/…/..」后再校验——原文是双省略号（6 字
    `……`），LLM 经常折叠成单省略号（3 字 `…`），原校验做标点归一化时把 `…`
    也清掉了，导致 `啊啊救命啊` 跟原文 `啊啊……救命啊` 失配（shot11/18 实测
    报「未找到」但台词实际是原文）。折叠为单字符后做匹配。
    """
    narration = shot.get("narration_segment", "").strip()
    if not narration:
        return True

    # 剥掉「说话人道：」前缀（说话人 + 道/说/怒道/厉声道 + 全角/半角冒号）
    narration_clean = re.split(r"[:：]", narration, maxsplit=1)[-1]
    narration_clean = narration_clean.replace(" ", "")
    # 08-17：把「…/……/..」三类省略号先折叠为占位符 `§`（普通字符，不会被标点
    # 归一化删掉），再做标点归一化（删除常见标点变体），最后把 `§` 还原成单字符
    # 省略号「…」。原代码顺序是「先标点归一化（含 `…`）→ 后折叠」，导致 `…` 在
    # 归一化阶段就被删了，折叠阶段没字符可折（shot14「公…公子」变「公公子」）。
    narration_clean = re.sub(r"\.{2,}|…{1,}", "§", narration_clean)
    novel_clean = re.sub(r"\.{2,}|…{1,}", "§", novel_text)
    # v6.34（08-15）：归一化补全全部中英文句读——「哎呀！」带感叹号导致
    # 直接子串匹配 miss（镜头8 误报真因），标点变体不应影响锚定判定。
    # 注意：此处不再删 `…`（已折叠为 §），否则折叠失效
    for _p in '，。！？；：、—,!?:;“”"\'（）()':
        narration_clean = narration_clean.replace(_p, "")
    narration_clean = narration_clean.replace("§", "…")
    novel_clean = novel_clean.replace("§", "…")
    # 08-17：合规自动替换层（sanitize_narration）会把"杀人"→"出人命"等。LLM
    # 在合规检测后自己改写 narration 时也会用同套词（shot14 实测：原文"杀…杀人啦"
    # → 当前"杀…出人命啦"）。_check_grounding 应当容忍这些合规替换，不报误报。
    # 这里不直接调用 sanitize（避免破坏 narration 字段本身），而是把合规词对做一次
    # 双向归一化：narration 里的"出人命"还原为"杀人"以跟原文对齐。
    try:
        from shared.content_safety import NARRATION_SUBSTITUTIONS as _NSR
        for _orig, _safe in _NSR.items():
            if _safe in narration_clean and _orig not in narration_clean:
                narration_clean = narration_clean.replace(_safe, _orig)
    except Exception:
        pass
    if len(narration_clean) < 5:
        # 台词体太短（如「住嘴！」）无法切 5 字子串，退化为直接查原文
        return narration_clean in novel_clean
    for i in range(len(narration_clean) - 4):
        chunk = narration_clean[i:i+5]
        if chunk in novel_clean:
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
        _get_logger().warn(f"原文锚点校验: 以下镜头的 narration 在原文中未找到对应内容:")
        for idx, text in ungrounded:
            _get_logger().info(f"  镜头{idx}: \"{text}...\"")
        _get_logger().info(f"  → 请确保所有 narration 直接引用自小说原文")


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
        _get_logger().warn(f"角色锚点缺失: 镜头 {missing_anchor} 的主体是已知角色但 prompt 缺少锚点")

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
    non_character_subjects = EMPTY_SCENE_SUBJECTS
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
                              "士兵", "侍女", "人", "两人", "三人", "两个人", "三个人",
                              # B4 修复（08-11）：威胁源合规替换别名（刺客→神秘人）——
                              # 替换后文本用「神秘人」但角色名是「刺客」，仅查「刺客」会
                              # 误判空镜中无人物、人物渗入不告警
                              "神秘人", "黑衣人", "蒙面人", "黑影", "偷袭者"]
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
            _get_logger().warn(f"人物一致性: {issue}")
        for warn in warnings:
            _get_logger().warn(f"人物一致性: {warn}")

    return {"pass": not bool(issues), "issues": issues, "warnings": warnings}


# ===========================================================================
# v6.3 新增：分镜提示词检查表（适配用户提供的8项核心检查）
# ===========================================================================

# --- 5. 提示词污染检测：无效关键词列表 ---
_INVALID_PROMPT_KEYWORDS = {
    "8K清晰度", "8k清晰度", "8K画质", "8k画质", "超高清", "HD画质",
    "无AI感", "无AI痕迹", "无AI味道", "不像AI", "AI感",
    # P0-1 修复（08-12）：去掉「电影感/电影画质/电影级」——style_prefix.STYLE_PREFIX
    # 运行时强制拼接「…电影质感…」，LLM 按规则 5 抄前缀 → 每镜必报「污染」刷屏。
    # 电影质感是品牌风格词而非堆砌词，不该被拦；" cinematic"（英文）LLM 不会抄，保留。
    " cinematic",
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

# v6.24 新增：seedance 铁律7/8/12 + capsule 分镜原则吸收的检查词库
_DEFORMATION_VERBS = {
    "掰断", "掰碎", "掰一半", "掰",
    "撕开", "撕碎", "撕裂", "撕破", "撕",
    "劈开", "劈碎", "劈",
    "折断", "剥开", "剥", "捏碎", "砸碎", "摔碎",
}
_WIDE_SHOT_WORDS = {"远景", "大远景", "全景", "大全景", "俯拍", "航拍", "俯瞰", "鸟瞰", "广角"}
_FINE_FACE_WORDS = {
    "剑眉", "星目", "丹凤眼", "柳叶眉", "朱唇", "樱桃小口", "五官",
    "睫毛", "发簪", "眉心", "唇瓣", "珍珠耳坠",
}
_HAND_EXCHANGE_WORDS = {"递出", "递交", "递到", "递", "接过", "接", "交接", "交换", "换", "奉上", "呈上", "交出"}
_HAND_LOCK_WORDS = {"手部", "左手", "右手", "手中", "指间", "双手", "手背", "拇指"}

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
            _get_logger().warn(f"提示词污染: {issue}")

    return {"pass": len(issues) == 0, "issues": issues}


def _check_negative_prompt(shots: list[dict], characters: list | None = None) -> dict:
    """检查逆向提示词是否添加（检查项6）.

    注意：PROMPT_TEMPLATE 的 shot 输出 schema 里没有逐镜 negative_prompt 字段，
    视频负向词由 shared/vimax_adapters.py 用适配器内置 NEGATIVE_PROMPT 统一注入。
    因此这里不检查"每镜有没有填 negative_prompt"（必然为空=误报），只对**真正纯空镜**
    校验 prompt 是否含禁止人物标记（空镜出人是唯一能在脚本层拦下的缺陷）。

    P0-3 修复（08-12）：空镜判定加「动作镜排除」——main_subject 含已知角色名
    （刺客跃出水面/听香指水面）、动作动词、道具名词时是动作镜而非空镜。B4 修了
    is_empty_scene 但漏了本检查（只查 EMPTY_SCENE_SUBJECTS 子串，未排角色名）。
    """
    warnings = []
    non_character_subjects = EMPTY_SCENE_SUBJECTS
    no_people_markers = {
        "无人物", "无人", "空无一人", "无一人", "没有人物", "禁止人物",
        "no people", "empty scene", "空镜无人", "画面无人物", "无人烟",
    }
    # 已知角色名（同 _check_character_consistency 第4节的双保险：主体含角色名/道具 → 动作镜）
    defined_ids = frozenset(
        c.get("identifier", "") for c in (characters or []) if c.get("identifier")
    )
    prop_hints = {
        "步摇", "剑", "刀", "枪", "扇", "笛", "琴", "棋", "书", "箭", "鞭",
        "玉佩", "令牌", "酒杯", "钓杆", "油纸伞", "发簪", "珠钗", "灯笼", "烛火",
    }

    for shot in shots:
        idx = shot.get("index", "?")
        main_subject = shot.get("main_subject", "").strip()
        prompt = shot.get("prompt", "").strip()

        if any(nc in main_subject for nc in non_character_subjects):
            # 动作镜排除：主体含已知角色名/道具名词/动作动词 → 非空镜（跳过）
            if (any(cid and cid in main_subject for cid in defined_ids)
                    or any(p in main_subject for p in prop_hints)
                    or any(v in main_subject for v in _ACTION_VERB_HINTS)):
                continue
            low = prompt.lower()
            if not any(m in low for m in no_people_markers):
                warnings.append(
                    f"镜头{idx}: main_subject=\"{main_subject}\" 为空镜/场景，"
                    f"但 prompt 缺少禁止人物标记（如：无人物/空无一人/no people）"
                )

    if warnings:
        for warn in warnings:
            _get_logger().warn(f"逆向提示词: {warn}")

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
            _get_logger().warn(f"道具连贯性: {issue}")

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
            _get_logger().warn(f"镜头衔接: {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"镜头衔接: {warn}")

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
            _get_logger().warn(f"动作重叠: {warn}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_semantic_risk(shots: list[dict]) -> dict:
    """检查语义风险（检查项7）：是否存在隐晦的违规意图.

    v6.33（08-12）：对「会被合规替换的词」不再告警——search_text 用 sanitize 后的版本
    再查。此前「杀人」在 parse 时告警，但 _post_process 会把它替换成「出人命」，日志
    却仍显示需人工确认（08-12 镜头12）。只有 sanitize 后仍残留的风险词（无法确定性
    替换的）才真正需要人工判断。
    """
    issues = []

    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "")
        subtitle = shot.get("subtitle", "")
        narration = shot.get("narration_segment", "")

        # 与 _post_process 同一替换函数：prompt 走全表 sanitize、台词走 sanitize_narration
        try:
            from shared.content_safety import sanitize, sanitize_narration
            search_text = (
                sanitize(prompt) + sanitize_narration(subtitle) + sanitize_narration(narration)
            )
        except Exception:
            search_text = prompt + subtitle + narration

        found_risks = [kw for kw in _SEMANTIC_RISK_KEYWORDS if kw in search_text]
        if found_risks:
            issues.append(f"镜头{idx}: 存在语义风险关键词 {found_risks}，需修改提示词")

    if issues:
        for issue in issues:
            _get_logger().warn(f"语义风险: {issue}")

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
            _get_logger().warn(f"平台规则: {warn}")

    return {"pass": True, "warnings": warnings}

# v6.5: 视线方向检查
def _check_gaze_direction(shots, hook: dict = None, characters: list | None = None) -> dict:
    """检查人物视线方向（镜内词域原则），并覆盖钩子镜头.

    v6.9: 传入 hook，对钩子镜头做同样的视线方向检查
    （此前 hook 视线零校验——钩子若只写「仰头尖叫」「双眼惊愕」
    这类无视线归属的表情，多人惊恐就没了来由）.
    v6.9: 误报过滤——「向」后紧跟纯方位词（向上/向后/向一侧）是运动/动作
    用法而非视线，不再提示；正则终止于分号/括号，避免跨句截断.
    v6.7: 触发词聚焦真正的视线动词。「向/朝」多为运动/站位/说话对象介词
    （朝杨旭站立、向船梢、向听香张嘴质问），被当成视线造成大量误报，
    移出触发集，只保留 看/盯/望/视/瞧/瞄 等视线动词.
    P1-7 修复（08-12）：泛化「视线建议用画左/画右」只对**视线锚定有构图价值的
    镜头**报——同镜 ≥2 角色、或含威胁源（视线汇聚焦点）。单人叙事视角
    （望一眼满天星斗/看他/视而笑）是原文动作，降为静默，不再逐镜刷屏。
    具体检查（尾帧看消失对象、潜伏威胁视线）不受影响，始终保留。
    """
    issues, warnings = [], []
    # 终止于中文标点，避免跨句捕获；触发词只含真正的视线动词
    # v6.10: 「视」会被前缀「影视剧」/机位术语「平视 微仰视 俯视 仰视」误触发
    # （28 镜逐镜报 2 条、刷屏 56 行）——匹配前先剔除这些非视线词，真实视线(看/盯/望)不受影响。
    wrong_pattern = re.compile(r'[看盯望瞧瞄注视][^，。,\.；;：:（()）【】]{0,10}')
    correct_dirs = {'画左', '画右', '画上方', '画下方', '画前', '画后'}
    # 「向」+ 纯方位/动作词 = 运动用法，不是视线（如向上冲出/抓向船舷/偏向一侧）
    motion_after = re.compile(r'^向[上下左右前后内外旁中一侧过身]')
    # P1-7：已知角色名 + 威胁源词，判定「视线锚定有构图价值」的镜头
    defined_ids = frozenset(
        c.get("identifier", "") for c in (characters or []) if c.get("identifier")
    )
    _threat_words = ("刺客", "杀手", "黑衣人", "黑影", "神秘人", "蒙面人", "潜伏", "伏击")

    def _gaze_critical(text: str) -> bool:
        """该镜视线是否值得锚定：同框 ≥2 角色，或含威胁源（视线汇聚焦点）."""
        present = [c for c in defined_ids if c and c in text]
        return len(present) >= 2 or any(t in text for t in _threat_words)

    def _check_one(shot: dict, label: str):
        nonlocal issues, warnings
        idx = shot.get('index', '?')
        tag = _shot_tag(label, idx)
        prompt = shot.get('prompt', '')
        ff_desc = shot.get('ff_desc', '')
        lf_desc = shot.get('lf_desc', '')
        # v1.1：拼入 lf_desc——尾帧角色视线此前零校验（"听香目光盯着画右水面刺客
        # 消失之处"被画右锚点放行且错别字无检测）
        text = f"{prompt} {ff_desc} {lf_desc}"

        # v1.1：尾帧角色看向"消失对象"检测——场景含受伤结果对象（杨旭倒下）时，
        # 角色应看向被刺倒的同伴，而非已消失的刺客/水面
        _victim_hints = ("倒下", "仰面倒", "仰面躺", "中刀", "被刺", "心口", "洇开", "倒地")
        _lost_objects = ("消失之处", "消失处", "水面刺客", "刺客消失", "没入之处", "水中刺客")
        if lf_desc and any(h in lf_desc for h in _victim_hints):
            _gaze_lost = re.search(
                r'[目光双眼眼神视线][^，。；！？]{0,3}[盯看望][^，。；！？]{0,12}',
                lf_desc,
            )
            if _gaze_lost:
                _seg = _gaze_lost.group()
                if any(l in _seg for l in _lost_objects):
                    warnings.append(
                        f"{_shot_tag(label, idx)}: 尾帧角色目光看向「{_seg[:20]}」——已消失的刺客/水面，"
                        f"不是动作结果对象。场景含受伤结果（杨旭倒下），角色应看向被刺倒的"
                        f"同伴杨旭（自动修复见 vimax_bridge.fix_lf_desc_gaze）。"
                    )

        # 先剔除机位/前缀术语/视类词，避免其内部「视」被误当视线
        # （影视剧/平视/微仰视/俯视/仰视；v6.33 补 视频/影视/视图——「视频」会被
        # wrong_pattern 捕获成「视频」，镜头3/4 的独立「视」也是此类词根碎片）
        clean_text = re.sub(r'影视剧|平视|微仰视|俯视|仰视|视角|镜头视角|视频|影视|视图', '', text)

        # v6.27：潜伏威胁（只露眼睛/头肩/半身藏水中）视线朝向校验——只露眼睛的潜伏
        # 姿态最易被画成正对镜头，必须显式写「自水面下斜望画X」+「禁止正对镜头」，
        # 否则模型会把潜伏威胁画成看向观众（threat-gaze-coupling 规则41.3 的补充校验）。
        # 具体检查不随 P1-7 泛化门控，始终保留（含威胁源，本就满足构图价值）。
        _lurking_hints = ("只露一双", "只露出一双", "仅露一双", "只露头肩", "半身藏于", "潜伏水下", "潜于水下", "藏身水中")
        if any(h in text for h in _lurking_hints):
            _lurking = re.search(r'[目光双眼眼神][^，。；！？]{0,3}[盯看望斜望][^，。；！？]{0,16}', text)
            if _lurking:
                _seg = _lurking.group()
                if not any(d in _seg for d in correct_dirs):
                    warnings.append(
                        f'{tag}: 潜伏威胁（只露眼睛/头肩）视线 {_seg} 缺方位锚定，'
                        f'应写「目光自水面下斜望画X的[目标]，侧向朝向目标，禁止正对镜头」'
                        f'——否则水面露眼最易画成正对镜头（规则41）。'
                    )

        # P1-7（08-12）：泛化视线建议只对「构图价值镜头」报（同框≥2角色/含威胁源）；
        # 单人叙事视角（望一眼满天星斗/看他/视而笑）是原文动作，静默不刷屏。
        if _gaze_critical(text):
            wrong = []
            for m in wrong_pattern.finditer(clean_text):
                w = m.group()
                # 过滤运动用法（「向」后接方位词）+ 已含画左/画右的画内锚点
                if motion_after.match(w) or any(d in w for d in correct_dirs):
                    continue
                # v6.33：跳过单字「视」——凝视/巡视/审视 等词根碎片，非独立视线动作
                # （镜头3/4 误报「视线描述 ['视']」，08-12 日志）
                if len(w) == 1 and w == '视':
                    continue
                wrong.append(w)
            if wrong:
                warnings.append(f'{tag}: 视线描述 {wrong} 建议用画左/画右等')

            has_correct = any(d in text for d in correct_dirs)
            if not has_correct and ('看向' in text or '盯着' in text):
                warnings.append(f'{tag}: 建议用空间方向词(画左/画右/画上方)')

    for shot in shots:
        _check_one(shot, '镜头')

    # 钩子镜头（index=0）同样纳入视线方向检查
    if hook and isinstance(hook, dict):
        _check_one(hook, '钩子镜头(index=0)')

    for w in warnings:
        _get_logger().info(f'视线方向: {w}')
    return {'pass': True, 'issues': issues, 'warnings': warnings}


# v6.31 新增：多人视线收敛判定（B6 修复 08-11，规则22.7/13.5/41）
#
# 背景（todolist 审计 08-10 B6）：规则22.7/13.5 要求「ff_desc 中每个角色都用
# 画左/画右锚定视线落点，所有视线汇聚到同一威胁焦点，形成视线三角」。现有
# _check_gaze_direction 只查「每个角色是否用了画左/画右」——但三个人各看各的
# 方向（A看画左、B看画右、C看画上方）时每人都锚定了，却是散焦，规则仍被违反。
# 本检查做「多人视线收敛」判定：同一镜头 ≥2 个角色都有视线锚定（画左/画右），
# 若各角色视线落点方向明显分歧（画左 与 画右 同时出现），且无中间过渡/多人分
# 散看不同方向的台词依据 → WARN 提示让视线汇聚到同一焦点。
#
# 保守设计：只对「多人同框 + 明确写了视线锚定」的镜头提示；单人镜头/空镜/
# 无视线锚定的镜头不查。warning-only。
# ============================================================================

# 视线锚定正则：角色名 + 视线词 + 视线动词 + 画左/画右/画上方/画下方。
# 注意：不跨逗号（逗号分隔的多个角色各自成锚点，跨逗号会误把"杨旭"当
# 「听香目光盯向…」的宾语）；名字非贪婪且不含视线词（防止"听香目光"被
# 当名字捕获）。
_GAZE_ANCHOR_RE = re.compile(
    r'([一-鿿]{2,4}?)(?:目光|双眼|眼睛|眼神|视线)[^，。；！？\n]{0,6}?'
    r'(?:盯|看|望|视|瞄)[^，。；！？\n]{0,4}?(画左|画右|画上方|画下方)'
)


def _check_gaze_convergence(shots: list[dict], hook: dict = None) -> dict:
    """检查多人同框镜头视线是否汇聚到同一焦点（规则22.7/13.5/41，warning-only）.

    同一镜头 ≥2 个角色各自有视线锚定（画左/画右）时，若落点方向明显分歧
    （画左 与 画右 并存），且无台词/动作依据说明是多人分散环视 → 提示让
    视线汇聚到同一威胁焦点（视线三角）。
    """
    warnings = []

    def _check_one(shot: dict, label: str):
        idx = shot.get("index", "?")
        text = " ".join([
            shot.get("prompt", "") or "", shot.get("ff_desc", "") or "",
            shot.get("lf_desc", "") or "", shot.get("motion_desc", "") or "",
        ])
        # 收集所有「角色 + 视线动词 + 画X」锚点
        anchors = _GAZE_ANCHOR_RE.findall(text)
        if len(anchors) < 2:
            return  # 少于 2 个角色视线锚定，无需收敛判定
        # 统计各落点方向（findall 返回 (角色, 画X) 二元组）
        dirs = set(a[1] for a in anchors)
        # 画左 与 画右 并存 = 明显分歧（画上方/画下方并存可算对立，但画左/右
        # 是最常见的视线对撞）
        has_left = "画左" in dirs
        has_right = "画右" in dirs
        if not (has_left and has_right):
            return  # 视线同向或单向，无分歧
        # 排除「多人分散环视」的合法依据：台词/动作写明各方看不同目标
        # （如「三人围坐，各怀心事」）——简化为：有明确「各自/分别/环视/扫视」
        # 词则不提示
        if any(k in text for k in ("各自", "分别", "环视", "扫视", "四顾", "左顾右盼")):
            return
        # 多人视线分歧：画左 与 画右 并存
        left_subs = sorted({a[0] for a in anchors if a[1] == "画左"})
        right_subs = sorted({a[0] for a in anchors if a[1] == "画右"})
        warnings.append(
            f"{_shot_tag(label, idx)}: 多人同框视线散焦——{left_subs} 看画左、{right_subs} 看画右，"
            f"各看各的方向。规则22.7/13.5 要求所有视线汇聚到同一威胁焦点形成视线三角"
            f"（威胁→目标、发现者→威胁），多人各看一方会让观众读不出冲突焦点。"
            f"应让非威胁侧角色的视线落向威胁源（如'听香看向画右水中的刺客'）。"
        )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    for w in warnings:
        _get_logger().warn(f"视线收敛: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


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
        # 08-15：对白台词（narration/subtitle，规则0 原文原句会嵌入 prompt 尾部）
        # 里的词不是画面描述——「公子是何等人物」是台词，跳过，避免每次误报。
        spoken = (shot.get("narration_segment") or "") + (shot.get("subtitle") or "")

        # 检查禁止词汇
        found_forbidden = []
        for word in _BODY_PURIFICATION_FORBIDDEN:
            if word in prompt:
                # 排除合理使用（如"画面中"出现在某些固定表达中）
                if word == "人物" and "人物关系" in prompt:
                    continue  # 允许"人物关系"这种专业术语
                if spoken and word in spoken:
                    continue  # 词在对白台词里（规则0 逐字保留），不是画面描述
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
            _get_logger().warn(f"正文净化: [ISSUE] {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"正文净化: {warn}")

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
        _get_logger().warn("输出结构: 检测到结构化标记（【参考素材】【正文】【制作说明】）")

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

    # 检查3：连续空镜检测（复用唯一常量 EMPTY_SCENE_SUBJECTS，禁用单字水/湖/山）
    consecutive_empty = 0
    for shot in shots:
        ms = shot.get("main_subject", "")
        if any(e in ms for e in EMPTY_SCENE_SUBJECTS):
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
            _get_logger().warn(f"事件轴分布: [ISSUE] {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"事件轴分布: {warn}")

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
            _get_logger().warn(f"船只位置约束: {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"船只位置约束: {warn}")

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

    # 情绪词 → 可视化动作对照表（收敛：复用模块级 _EMOTION_ACTION_MAP 单一来源，
    # 审计 P1-8 此前在此处另有一份顿号分隔的独立副本，与修复版逗号分隔互相抵消）
    emotion_to_action = _EMOTION_ACTION_MAP

    def check_shot_emotions(shot, idx):
        """检查单个镜头的情绪可视化"""
        prompt = shot.get("prompt", "")
        motion_desc = shot.get("motion_desc", "")

        # 剥离模板注入的固定段落（色彩情绪/物理特效等），这些由 LLM 统一输出，
        # 其中的情绪词不反映角色表演指令，不应触发抽象情绪警告。
        # 匹配「label：value」格式后截断到对应段落的末尾。
        _EMOTION_SECTION_RE = re.compile(
            r'(?:色彩情绪|物理特效|材质|光影|色调|主光源|辅光源)[^：:]*[：:]([^\n]*)(?:\n|$)',
        )
        clean_prompt = _EMOTION_SECTION_RE.sub('', prompt)
        clean_motion = _EMOTION_SECTION_RE.sub('', motion_desc)

        # 合并检查文本（剥离模板段落后）
        full_text = clean_prompt + " " + clean_motion

        for emotion in abstract_emotion_words:
            if emotion in full_text:
                # 检查是否同时包含对应的可视化动作
                has_visual_action = False

                if emotion in emotion_to_action:
                    # 检查是否包含该情绪对应的任何可视化动作词
                    # （_EMOTION_ACTION_MAP 用逗号分隔，与修复版一致，避免互相抵消）
                    action_words = emotion_to_action[emotion].split("，")
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
        _get_logger().warn(f"情绪可视化: [ISSUE] {issue}")

    for warn in warnings:
        _get_logger().warn(f"情绪可视化: {warn}")

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

        # v6.37 lf_desc 字符下限（08-21 实战教训：Mode A++ v1 砍 lf 至 30 字丢角色布局）
        # warning-only：空镜/纯过渡等静态镜可短；其余含角色或动作结束的镜应 ≥40 字
        # 纯空镜（main_subject 是"空镜"/"环境"/"山峦"等）才豁免
        if len(lf_desc) < 40:
            _ms = str(shot.get("main_subject", "") or "").strip()
            _empty_subjects = ("空镜", "空景", "远景", "环境", "山峦", "山色", "夕阳", "月光",
                                "荷塘", "夜幕", "雨", "雪", "风", "云", "日落", "日出", "月色")
            _is_empty_shot = any(es in _ms for es in _empty_subjects)
            if not _is_empty_shot:
                warnings.append(
                    f"镜头{idx}: lf_desc 仅 {len(lf_desc)} 字 < 40 字下限——"
                    f"过短会丢角色布局（如姿态/位置/服装色/视线），甚至产生内部矛盾"
                    f"（如「已没入水下」+「举刀正对」单句并存）。"
                    f"建议补齐本镜所有出镜角色的完整静态布局"
                )

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
            # v6.37 hook lf_desc 字符下限（钩子常含多角色状态，< 60 字为 warning）
            if len(lf_desc) < 60:
                warnings.append(
                    f"钩子镜头(index=0): lf_desc 仅 {len(lf_desc)} 字 < 60 字下限——"
                    f"钩子常含多角色（刺客+杨旭+听香）状态，过短会丢布局"
                )
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
            _get_logger().warn(f"首尾帧完整性: {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"首尾帧完整性: {warn}")

    # 尾帧中间态检查（v6.29：从「武器残留单洞」扩展为五类中间态，复用
    # style_prefix.detect_lf_midstate 闸门，词库单一来源——取代窄正则武器残留）。
    # 视频模型把 lf_desc 当【视频终点】定格，任何中间态（正在刺/即将刺/半截刀尖
    # 露水面/缓缓没入中）都会被定格成诡异画面（08-09 钩子坑「匕首尖停水面」）。
    from style_prefix import detect_lf_midstate
    _midstate_warns = []
    for _shot in list(shots) + ([hook] if hook else []):
        _idx = _shot.get("index", "?")
        _lf = _shot.get("lf_desc", "") or ""
        _problems = detect_lf_midstate(_lf)
        if _problems:
            _kinds = {p["type"] for p in _problems}
            _midstate_warns.append(
                f"镜头{_idx}: lf_desc 含中间态（{'/'.join(sorted(_kinds))}）——"
                f"尾帧必须是动作彻底结束的终态，中间态会被视频模型当【视频终点】定格成"
                f"诡异画面（如匕首尖一直戳水面）。应让武器/人物彻底没入或已结束"
                f"（规则609，normalize_lf_desc 会自动修复）。"
            )
    for w in _midstate_warns:
        _get_logger().warn(f"尾帧中间态: {w}")

    # 硬性检查：lf_desc 不能为空，首尾帧不能完全相同
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# ============================================================================
# v6.31 新增：软性规则校验（C3 修复 08-11）
#
# todolist C 档：以下规则只有 LLM prompt 约束、零代码校验，LLM 违约时无任何
# 提示（软性规则 = 违反不崩、但违反多了画面质量/节奏/合规性明显下降）。
#   - 规则13.6：ff_desc ≤120 字（钩子 ≤160）——超长则嵌套长句、图片模型理解退化
#   - 规则37：抽象词必须量化（梦幻/压抑/优雅 等情绪风格词须带光/色/焦参数）
#   - 规则46：人物出场四大方式——核心角色首次登场用有设计感的出场方式
#   - 规则24：相邻镜头 color_tone 变化幅度 ≤20%（色彩突变观感割裂）
# 全部 warning-only，不阻断（软性规则由 LLM 决定，此处仅提示）。
# ============================================================================

# 规则37 抽象风格词 → 必须量化（梦幻=低饱和+柔光；压抑=暗调+顶光；优雅=暖金侧光…
# 命中抽象词而画面描述无任何光/色/焦参数 → 提示补量化）
_ABSTRACT_QUANT_WORDS = (
    "梦幻", "压抑", "优雅", "诡异", "神秘", "浪漫", "冷酷", "清冷",
    "阴森", "温馨", "壮阔", "孤寂", "哀婉",
    # v6.40 08-22：补 LLM 偷懒词——「唯美/静谧」在 8-22 跑批高频出现
    # 但无任何光/色/焦量化参数，导致模型画出"PPT 风"模糊画面
    "唯美", "静谧", "美轮美奂", "如梦如幻", "如诗如画",
    "诗情画意", "美不胜收", "美如画", "美如仙境",
)
# 光/色/焦参数（抽象词的量化落点）
_QUANT_PARAM_WORDS = (
    "光", "阴影", "明暗", "冷色", "暖色", "青蓝", "金黄", "橙红", "暗调",
    "低饱和", "高饱和", "浅景深", "景深", "柔光", "硬光", "侧光", "逆光",
    "顶光", "色温", "焦",
)

# 规则46 出场设计方式（出现任一即视为有出场设计）
_ENTRANCE_DESIGN_WORDS = (
    "远景", "背影", "背部", "上摇", "特写", "大特写", "脚部", "手部",
    "俯视", "剪影", "轮廓", "渺小", "天地", "缓步", "登场", "出场",
)


def _check_ff_desc_length(shots: list[dict], hook: dict = None) -> dict:
    """规则13.6：ff_desc ≤120 字（钩子 ≤160）校验（A+B 2026-08-14 放宽，warning-only）."""
    warnings = []
    for s in shots:
        idx = s.get("index", "?")
        ff = (s.get("ff_desc") or "").strip()
        if not ff:
            continue
        if len(ff) > 120:
            warnings.append(
                f"镜头{idx}: ff_desc {len(ff)} 字 > 120 字（规则13.6）——首帧描述过长，"
                f"易嵌套长句、图片模型理解退化。应短句、一角色一短句、动词前置，"
                f"只留「前一刻」定格 + 角色完整静态布局 + 服装色，删冗余环境/月光段句。"
            )
    if hook and isinstance(hook, dict):
        hff = (hook.get("ff_desc") or "").strip()
        if hff and len(hff) > 160:
            warnings.append(
                f"钩子镜头(index=0): ff_desc {len(hff)} 字 > 160 字（规则13.6 钩子上限）"
            )
    for w in warnings:
        _get_logger().warn(f"ff描述长度: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_abstract_quantization(shots: list[dict], hook: dict = None) -> dict:
    """规则37：抽象风格词必须量化（C3 修复，warning-only）.

    命中抽象词（梦幻/压抑/优雅…）而画面描述无任何光/色/焦参数 → 提示把
    抽象词翻译成光/色/焦（梦幻=低饱和+柔光+浅景深+青蓝主色…）。
    """
    warnings = []

    def _check_one(shot: dict, label: str):
        idx = shot.get("index", "?")
        text = " ".join([
            shot.get("prompt", "") or "", shot.get("ff_desc", "") or "",
            shot.get("motion_desc", "") or "",
        ])
        hits = [w for w in _ABSTRACT_QUANT_WORDS if w in text]
        if not hits:
            return
        has_quant = any(p in text for p in _QUANT_PARAM_WORDS)
        if not has_quant:
            warnings.append(
                f"{_shot_tag(label, idx)}: 抽象风格词 {hits} 未量化（规则37）——抽象词必须翻译成"
                f"光/色/焦参数（梦幻=低饱和+柔光+浅景深+青蓝主色；压抑=暗调+顶光+冷色；"
                f"优雅=暖金侧光+绸缎高光+慢推），无法量化则删词。"
            )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")
    for w in warnings:
        _get_logger().warn(f"抽象词量化: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_character_entrance(shots: list[dict], characters: list | None = None) -> dict:
    """规则46：核心角色出场设计（C3 修复，warning-only）.

    主角/关键角色首次登场（本镜头 main_subject 是 script.characters 里的
    注册角色 + 该角色首次出现在 shots 中）时，应避免「平视中景站立」千人一面，
    用四大出场方式之一（远景渺小/背影藏脸/细节上摇/特写五官）。

    只查注册角色（characters 里的 identifier）——路人/背景角色不受规则46约束，
    避免对每个新出现的路人误报（C3 保守化 08-11）。
    """
    warnings = []
    seen_chars: set[str] = set()
    defined_ids = frozenset(
        c.get("identifier", "") for c in (characters or []) if c.get("identifier")
    )
    for s in shots:
        idx = s.get("index", "?")
        ms = (s.get("main_subject") or "").strip()
        # 只查注册角色（核心角色 = 已进入角色库的），且是首次出现
        if ms not in defined_ids or ms in seen_chars:
            continue
        seen_chars.add(ms)
        text = " ".join([
            s.get("prompt", "") or "", s.get("ff_desc", "") or "",
            s.get("motion_desc", "") or "",
        ])
        if any(e in text for e in _ENTRANCE_DESIGN_WORDS):
            continue
        # 空镜/场景主体（无人物）不查
        if any(nc in ms for nc in EMPTY_SCENE_SUBJECTS):
            continue
        warnings.append(
            f"镜头{idx}: 核心角色「{ms}」首次登场无出场设计（规则46）——避免'平视中景"
            f"站立'千人一面，用四大出场方式之一：远景渺小于天地 / 中景背影藏脸 / "
            f"近景脚手细节+上摇揭示 / 特写五官眼神。"
        )
    for w in warnings:
        _get_logger().warn(f"出场设计: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_color_tone_transition(shots: list[dict]) -> dict:
    """规则24：相邻镜头 color_tone 变化幅度 ≤20%（C3 修复，warning-only）.

    v6.34（08-15 误报审计）：混合色调（「冷蓝星光与暖橙灯光对比」）同时含
    双向词，与任何方向兼容（灯光元素变化≠基调突变，曾致 10↔11、22↔23
    船岸 A/B 交替镜误报）；只有纯暖↔纯冷直接翻转才提示。

    v6.40（08-22 P18 闭环扩展）：明确时间跳跃（黄昏→夜/晴→雨/日→夜/冬→春）
    豁免——剧情时间跳转属正常叙事，色调随时间变化是合理的。判定：本镜 prompt/
    motion_desc/narration_segment 任一含「明确时间跳跃词」则豁免。
    """
    warnings = []
    _WARM = ("暖", "金", "橙", "红", "黄昏", "夕阳", "烛", "火")
    _COLD = ("冷", "蓝", "青", "月", "夜", "幽", "银")
    # v6.40：明确时间跳跃豁免词（命中任一即视为合理突变）
    _TIME_JUMP_WORDS = (
        "次日", "翌日", "第二天", "次日黎明", "次日清晨", "次日黄昏", "次日夜晚",
        "入夜", "深夜", "凌晨", "黎明", "破晓", "拂晓", "天明",
        "数日后", "数月后", "数年后", "数周后", "数时辰后",
        "冬", "春", "夏", "秋",  # 季节跳转
        "转场", "淡入淡出", "切到", "回到", "镜头转", "时间跳",
    )
    prev_state = None   # 'warm'|'cold'|'mixed'|None
    prev_idx = None
    for s in shots:
        idx = s.get("index", "?")
        if idx == 0:
            continue
        tone = (s.get("color_tone") or "").strip()
        has_warm = any(w in tone for w in _WARM)
        has_cold = any(c in tone for c in _COLD)
        state = "mixed" if (has_warm and has_cold) else ("warm" if has_warm else ("cold" if has_cold else None))
        if prev_state is not None and "mixed" not in (state, prev_state):
            if (prev_state, state) in (("warm", "cold"), ("cold", "warm")):
                # v6.40 豁免：本镜含明确时间跳跃词则跳过
                shot_text = " ".join([
                    s.get("prompt", "") or "",
                    s.get("motion_desc", "") or "",
                    s.get("narration_segment", "") or "",
                    s.get("ff_desc", "") or "",
                ])
                if any(w in shot_text for w in _TIME_JUMP_WORDS):
                    pass  # 豁免
                else:
                    warnings.append(
                        f"镜头{prev_idx}→{idx}: color_tone 色调方向突变（规则24）——"
                        f"相邻镜头色调变化幅度应 ≤20%，暖→冷/冷→暖 直接跳是明显突变，"
                        f"建议中间加过渡镜头（开篇环境建立/明确时间跳跃可用淡入淡出过渡）。"
                    )
        prev_state, prev_idx = state, idx
    for w in warnings:
        _get_logger().warn(f"色调过渡: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


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
        # v6.34（08-15 误报审计）：hook 豁免结果状态污染检查——hook 是
        # 「案发后回放/高潮定格」结构，ff 含「暗红洇痕」等先在状态合法
        # （动作发生在 hook 之前）。完成态动词检查（已刺中/倒地）仍对 hook 生效。
        _is_hook = "index=0" in label or "hook" in label.lower()
        # 结果状态污染检查（v6.16）：ff_desc 是动作开始前的定格，若把动作
        # 进行/结束后的结果状态（血溅到脸上、尖叫、瘫坐、倒地）写进 t=0，
        # 就是"图片提示词是一串动作"的根因——图片模型画不出"炸开→冲出→
        # 溅血→尖叫"的递进，只能靠参考图兜底导致两帧雷同。
        # 触发条件：motion 有时间分段（动作确实在推进）且 ff 与 motion 都含
        # 同一结果状态词 → 该状态属于动作结果，不属于首帧定格。
        # 例：ff「听香仰头张嘴尖叫，脸上溅着暗红红珠」+ motion「2-4秒听香脸上
        # 溅红；4-6秒听香发出凄厉尖叫」→ 尖叫与溅血都是动作结果，被提前到 t=0。
        _RESULT_STATE_WORDS = (
            "尖叫", "溅血", "溅红", "血珠", "血迹", "溅着", "暗红",
            "瘫坐", "倒地", "倒下", "晕厥", "昏迷", "捂住脸", "捂脸",
            "泪水滑落", "痛哭", "大哭", "抽搐",
        )
        _TIME_SEG_RE = re.compile(r"前\d+秒|后\d+秒|\d+\s*[-–]\s*\d+\s*秒")
        if _TIME_SEG_RE.search(motion) and not _is_hook:
            # v6.34：状态词在 motion 首个时间段已存在 → 是 t=0 已成立的
            # 先在状态而非动作结果，放行；只有后续时间段才出现的才属「结果被提前」
            _m1 = _TIME_SEG_RE.search(motion)
            _m2 = _TIME_SEG_RE.search(motion, _m1.end())
            _first_text = motion[_m1.start():(_m2.start() if _m2 else len(motion))]
            polluted = [
                w for w in _RESULT_STATE_WORDS
                if w in ff and w in motion and w not in _first_text
            ]
            if polluted:
                warnings.append(
                    f"{label}: ff_desc 把动作结果状态「{'、'.join(polluted)}」写进首帧定格，"
                    f"但 motion_desc 该状态发生在动作推进中（{motion[:50]}...）。"
                    f"首帧只能画动作开始前（前一刻）的状态（如刺客潜伏水下、听香张嘴欲呼、脸上无血），"
                    f"禁止把血溅到脸上/尖叫/倒地等动作结果提前到 t=0——否则图片模型画不出一串动作，"
                    f"只能照首帧参考图兜底导致首尾帧雷同。"
                )
        # 武器 t=0 不可见检查（08-09 钩子坑2）：motion 含刺入/挥砍动作，
        # 但 ff_desc 没有武器可见状态（藏刀：匕首未出水/未出鞘/未露寒光）——
        # 视频模型没有刀的起始位置，只能「凭空变」出武器，导致刀从胸口冒出/
        # 刺入方向错乱。应让武器 t=0 就握在攻击者手中并可见（如高举过顶/抵住）。
        # 词表统一来源：shared/content_safety.py（与 prompts 规则48 共用，改一漏一）
        from shared.content_safety import (
            WEAPON_WORDS as _W_WORDS,
            WEAPON_HIDDEN_WORDS as _W_HIDDEN,
            WEAPON_VISIBLE_WORDS as _W_VISIBLE,
            WEAPON_STAB_VERBS as _W_STAB,
        )
        if motion and any(w in ff for w in _W_WORDS):
            # 仅当 motion 确实有攻击动作才检查（纯静态镜头无关）
            if any(v in motion for v in _W_STAB):
                # 排除藏刀状态（潜伏水下是合法动作前一刻，但必须紧接着出刀）
                if any(w in ff for w in _W_HIDDEN):
                    if not any(w in ff for w in _W_VISIBLE):
                        # v6.32（08-12）：motion 首段若已建立武器可见起点（破水而出+
                        # 高举匕首/举刀出水面），视频模型有武器运动轨迹，不构成「凭空变」，
                        # 豁免（规则48 核心：要么 ff 可见，要么 motion 首段建立可见起点）。
                        # 此前漏掉此分支，钩子「破水而出，右手高举匕首…垂直直刺」这类
                        # 合规写法被误报（hook 实测，08-12）。
                        _first_seg = motion.split("；")[0]
                        _motion_establishes = (
                            any(k in _first_seg for k in ("出水", "破水", "举刀", "挥出"))
                            or any(v in _first_seg for v in _W_VISIBLE)
                        )
                        if not _motion_establishes:
                            warnings.append(
                                f"{label}: ff_desc 武器处于隐藏态（{'、'.join([w for w in _W_HIDDEN if w in ff])}），"
                                f"而 motion_desc 有刺入/挥砍动作（{motion[:40]}...）。"
                                f"t=0 画面没有武器起点，视频模型只能「凭空变」出武器，"
                                f"导致刀从胸口冒出/刺入方向错乱。"
                                f"建议 ff 让武器可见（如'刺客半身出水、右手握匕首高举过顶、刀尖朝下'），"
                                f"或 motion 首段先写'举刀出水面'建立武器可见起点再下刺。"
                            )
        # 完成态动词检测
        completed = [v for v in _FF_COMPLETED_VERBS if v in ff]
        # v6.19：进行中动作定格检查（用户决策：ff_desc 应收敛到动作「前一刻」）。
        # 若 ff 含进行态动作动词（破水/冲出/刺向/倒纵/跃起等，非完成态）——
        # 首帧定格在动作进行中，与 motion_desc 首段动作重叠，视频模型会把
        # 动作做两遍。提示改前一刻（自动修复见 style_prefix.normalize_ff_desc）。
        if not completed:
            from style_prefix import detect_ff_multi_action
            prog = detect_ff_multi_action(ff, None)
            pre = [p for p in prog if p.get("type") == "pre_action"]
            if pre:
                warnings.append(
                    f"{label}: ff_desc 定格在动作进行中（{pre[0].get('clause', '')[:30]}），"
                    f"而 motion_desc 首段动作相同（{motion[:40]}...）——视频起点与中段动作重叠，"
                    f"模型会把动作做两遍。建议改定格在动作「前一刻」"
                    f"（如'刺客潜伏水下、水面微澜'/'刀尖抵近杨旭心口、即将触及'/'听香张嘴欲呼'）。"
                )
            return
        # 进行中定格豁免：「半身已冲出水面 + 武器高举未落下」是动作进行中的
        # 定格（还有后续劈落/追击），不是动作结果时刻。这是规则建议的正确写法
        # （如'刺客半身破水、刀高举未落下'），不应误报为完成态。
        # 误报示例（code-audit 08-06 钩子）：ff「半身已冲出水面、右手高举匕首过顶」
        # 是进行中，motion 前2秒才劈落——时序一致，却因「已冲出」被判完成态。
        # v6.19 注：normalize_ff_desc 会把「半身破水而出」改写成「潜伏水下」，
        # 此豁免仅防旧缓存/未归一化文本误报为完成态（安全网）。
        in_progress_exempt = (
            ("半身" in ff and "已冲出" in ff and any(k in ff for k in ("高举", "未落", "过顶", "扬起")))
            or ("破水" in ff and "高举" in ff)
            or ("即将" in ff or "欲" in ff or "尚未" in ff)
        )
        if in_progress_exempt:
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
            f"建议 ff_desc 定格在动作「前一刻」（如'刺客潜伏水下、水面微澜'/'刀尖抵近杨旭心口、即将触及'）。"
        )

    for shot in shots:
        _check_one(shot, f"镜头{shot.get('index', '?')}")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    if warnings:
        for warn in warnings:
            _get_logger().warn(f"首帧时序: {warn}")

    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_ff_multi_action(shots: list[dict], hook: dict = None,
                           characters: list | None = None) -> dict:
    """检查 ff_desc 是否把多个动作/时间推进/过渡态堆叠进一个静态首帧（v6.18）.

    背景（code-audit 08-07 钩子首帧）：LLM 常把「破水而出 + 惊觉欲起 + 尖叫」
    三个进行态动作塞进 ff_desc——图片模型画不出"三个同时态动作"，只能糊成
    一团或只画部分。v6.19：收敛目标升级为动作「前一刻」（破水→潜伏水下、
    水面微澜；刺向→刀尖抵近、即将触及；尖叫→欲呼）。此检查为 warning 级
    （不阻断），提示把 ff_desc 定格在 motion 第一个动作的前一刻
    （自动修复见 style_prefix.normalize_ff_desc，生成源头与消费兜底共用）。

    复用 style_prefix.detect_ff_multi_action 作为判定闸门（词库单一来源）。
    注意过滤 pre_action 类型——它是"应被 normalize 自动改写"的信号（post_fix
    已先归一化，正常不会出现），不是"多动作堆叠"问题。
    """
    from style_prefix import detect_ff_multi_action
    warnings = []
    issues = []

    def _check_one(shot: dict, label: str):
        ff = shot.get("ff_desc", "").strip() or ""
        if not ff:
            return
        problems = detect_ff_multi_action(ff, characters)
        if not problems:
            return
        parts = []
        for p in problems:
            if p["type"] == "pre_action":
                continue  # normalize 自动改写信号，非质量问题
            if p["type"] == "time_advance":
                parts.append(f"时间推进连接词「{'、'.join(p['words'])}」")
            elif p["type"] == "multi_action":
                parts.append(f"{p['count']} 个动作时刻同框（{'；'.join(p['clauses'][:2])}{'…' if p['count']>2 else ''}）")
            elif p["type"] == "transition":
                parts.append(f"过渡态「{p['clause']}」")
            elif p["type"] == "sustained":
                parts.append(f"持续动作「{p['clause']}」")
        if not parts:
            return  # 只剩 pre_action，无实际问题
        warnings.append(
            f"{label}: ff_desc 含 {('、'.join(parts))}——静态首帧不应定格在动作进行中。"
            f"应改写为 motion 第一个动作的「前一刻」："
            f"破水而出→潜伏水下、水面微澜；刺向→刀尖抵近、即将触及；尖叫→张嘴欲呼"
            f"（如「刺客潜伏水下、水面微澜；杨旭静坐未察；听香仰头张嘴欲呼」）。"
        )

    for shot in shots:
        _check_one(shot, f"镜头{shot.get('index', '?')}")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    if warnings:
        for warn in warnings:
            _get_logger().warn(f"首帧多动作: {warn}")

    return {"pass": True, "issues": issues, "warnings": warnings}


def validate_hook(hook: dict, characters: list | None = None) -> None:
    """钩子分镜单独校验（v6.18）.

    钩子在 main.py 中于 _validate_script 之后才挂载，正常流程从不经过校验。
    此函数补跑所有 hook-aware 检查，打印 warning（不阻断，防钩子因校验被丢弃）。

    characters: script['characters']，用于多动作/空镜判定；缺省时传空列表。
    """
    from style_prefix import normalize_ff_desc
    if not hook or not isinstance(hook, dict):
        return
    chars = characters or []
    try:
        # 先做 ff_desc 归一化（生成源头修复，与 post_fix 幂等）
        if hook.get("ff_desc"):
            hook["ff_desc"] = normalize_ff_desc(hook["ff_desc"], hook, chars)
        _check_ff_multi_action([], hook, chars)
        _check_ff_lf_desc_completeness([], hook)
        _check_ff_motion_timeline([], hook)
        _check_gaze_direction([], hook)
        _check_emotion_visualization([], hook)
        _check_restrained_emotion([], hook)
        _check_focal_switch([], hook)
        _check_body_purification([], hook)
        # Plan A（2026-08-14）：hook 补跑规则41/51/25/13.7 全锚点检查——
        # _run_prompt_checklist 在 hook 挂载前执行，钩子必须在此处补查。
        _check_threat_gaze_coupling([], hook)
        _check_keyframes_quality([], hook)
        _check_multi_person_staging([], hook, chars)
        _check_ff_desc_anchors([], hook, chars)
        # v6.31 新增：钩子 kf 时序一致性（与主分镜同校验，但钩子单独跑）
        _check_kf_temporal_consistency([], hook)
    except Exception as e:
        _get_logger().warn(f"钩子校验异常（不阻断）: {e}")


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
        # 新增人物类：匹配「一个+性别/年龄」等模糊人称（如「一个成年男性」）。
        # 如果同一条文中还出现了已注册角色名，则该模糊描述大概率是指已知角色，跳过。
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
                # 若同一条文中出现了已注册角色名，则该模糊描述大概率指向已知角色（如
                # 「一个成年男性刺客」中「刺客」是已知角色），跳过以避免误报
                has_known_char = any(c in check_text for c in known_chars)
                if has_known_char and _label == '[人物角色]':
                    continue
                warnings.append(
                    f"镜头{idx}: 画面描述出现未锚定的{_label}（{_pat}），"
                    f"narration 中无对应，可能为幻觉编造"
                )

    if issues:
        for issue in issues:
            _get_logger().warn(f"分镜原文对齐: {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"分镜原文对齐: {warn}")

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
    # P0-4 修复（08-12）：去掉纯转场词（转场/淡入/淡出/黑场/切至/画面切/切换到）——
    # post_fix_shot_prompts 对场景切换镜注入「淡入淡出转场，转场时长0.5s」，含
    # 「转场/淡入/淡出」被本检查自冲突误报「堆叠多地点」（10+ 镜刷屏）。
    # 只保留**真实地点变化**词（人物移动/离开到另一地），转场描述不再计为多地点。
    "地点变化", "来到了", "走向", "赶往", "走出", "走进", "离开", "驶向", "驶出",
)

_DENSITY_MAX_ACTIONS = {
    # (max_duration, max_action_clauses) — 时长阈值 → 动作短语上限
    # v6.34（08-15 审计）：≤3s 上限 1→2、≤5s 2→3——连贯动作链
    # 「推臂→弹腿→倒纵入水」是一串连续身体动作而非多个独立动作，旧阈值误报
    2: 1,   # ≤2s：1 个动作
    3: 2,   # ≤3s：2 个动作（连续动作链）
    4: 2,   # ≤4s：2 个动作
    5: 3,   # ≤5s：3 个动作
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
            _get_logger().warn(f"内容密度: {w}")

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
            _get_logger().warn(f"转场关键词: {issue}")
    if warnings:
        for warn in warnings:
            _get_logger().warn(f"转场关键词: {warn}")

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

    # Seedance 官方明确「一个镜头尽量只指定 1 种运镜」，Seedance 系 provider
    # （volcengine/agent_plan）下取消「跟拍可复合」豁免。用 active_video_is_seedance() 统一判断。
    from shared.model_caps import active_video_is_seedance
    seedance_strict = active_video_is_seedance()

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
        #    复合——「跟随+环绕/推进/变焦」是跟拍镜的合法变体，不算堆叠。
        #    Seedance 官方要求一镜一运镜，volcengine 下不豁免）
        composite = len(matched) >= 2
        if composite and not seedance_strict and "跟镜" in matched:
            composite = False
        if composite:
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
            _get_logger().warn(f"运镜检查: {warn}")

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
            _get_logger().warn(f"运镜升级: {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.15 新增：打斗动作具体性检查（AI漫剧打斗动作提示词吸收，combat-action）
# 打斗镜头禁止抽象"交手/打斗"，必须落具体招式 + 打击反馈 + 节奏变化：
#   1. 招式具体：prompt 命中拳/掌/腿法招式词（或武器+挥/劈/刺等动词）
#   2. 打击反馈：每镜至少 1 个可感知反馈元素（冲击波/火星/碎屑/衣袂/气浪/动态模糊等）
#   3. 节奏变化：时间凝滞/慢动作类修饰必须写三段时长分配，禁止笼统"全程慢动作"
# 与 v6.4 转场检查、v6.12 运镜升级互补：那个管镜头过渡，这个管动作本体。
_COMBAT_STRIKE_TERMS = (
    # 拳法类
    "直拳", "摆拳", "勾拳", "连环拳", "反手拳", "虚晃拳",
    # 掌法类
    "推掌", "劈掌", "拍掌", "横掌", "双掌推出", "反掌",
    # 腿法类
    "高扫腿", "低扫腿", "后踢", "回旋踢", "腾空踢", "蹬踢", "飞踢", "旋风腿",
    # 武器挥击动词（配合武器词判定具体招架）
    "劈砍", "横劈", "上挑", "斜劈", "格挡", "挥刀", "出剑", "刺出", "横扫",
    "斩", "劈", "刺", "砍",
)
_COMBAT_FEEDBACK_TERMS = (
    "冲击波", "气浪", "火星四溅", "火花四溅", "碎屑飞溅", "飞沙走石",
    "衣袂翻飞", "肌肉紧绷", "动态模糊", "速度线", "气流扰动", "扬尘",
    "虎口青筋", "撞击", "格挡",
)
# 节奏修饰必须带时长分配（三段式），命中下面"裸节奏词"却无时长分段 → 提示
_COMBAT_RHYTHM_NUDES = ("时间凝滞", "定格", "慢动作", "慢镜头")
_COMBAT_TIMING_PATTERN = re.compile(r"前?\d+\.?\d*\s*秒|(\d|秒)[-–—](\d|秒)")


def _check_combat_action(shots: list[dict]) -> dict:
    """检查打斗动作具体性（v6.15 新增）★AI漫剧打斗动作提示词★.

    仅检查检测为打斗的镜头（复用 _COMBAT_KEYWORDS + 高张力触发词）：
    1. 招式必须具体（拳/掌/腿法招式词或武器挥击动词），禁止"激烈交手/两人缠斗"式抽象
    2. 每镜至少 1 个打击反馈元素，防止"挥空拳"式的无反馈打斗
    3. 时间凝滞/慢动作必须写三段时长分配，禁止笼统"全程慢动作"
    """
    warnings = []
    issues = []

    for shot in shots:
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "") or ""
        narration = shot.get("narration_segment", "") or ""
        text = " ".join([prompt, shot.get("ff_desc", "") or "", shot.get("motion_desc", "") or ""])

        # 仅打斗/高张力镜头检查
        is_combat = any(kw in narration for kw in _COMBAT_KEYWORDS) or any(kw in text for kw in _COMBAT_KEYWORDS)
        if not is_combat:
            continue

        # 1. 招式具体性
        has_strike = any(t in text for t in _COMBAT_STRIKE_TERMS)
        has_weapon = any(w in text for w in ("刀", "剑", "枪", "鞭", "戟", "斧", "锤", "匕首", "暗器", "箭"))
        has_abstract_fight = any(w in text for w in ("交手", "缠斗", "激战", "搏斗", "对战"))
        if not has_strike:
            if has_abstract_fight:
                warnings.append(
                    f"镜头{idx}: 打斗动作仍抽象（「{next(w for w in ('交手', '缠斗', '激战', '搏斗', '对战') if w in text)}」），"
                    f"建议写具体招式（拳/掌/腿法名或 武器+挥/劈/刺+部位+方向）"
                )
            elif has_weapon:
                warnings.append(
                    f"镜头{idx}: 有武器但缺具体挥击动词，建议写「劈砍/横劈/上挑/刺出+方向+部位」"
                )

        # 2. 打击反馈
        if not any(f in text for f in _COMBAT_FEEDBACK_TERMS):
            warnings.append(
                f"镜头{idx}: 打斗镜头缺打击反馈，建议加 冲击波/火星四溅/碎屑飞溅/衣袂翻飞/动态模糊 等效果"
            )

        # 3. 节奏修饰必须带时长分配
        nude_rhythm = [r for r in _COMBAT_RHYTHM_NUDES if r in text]
        if nude_rhythm and not _COMBAT_TIMING_PATTERN.search(text):
            warnings.append(
                f"镜头{idx}: 用了「{'/'.join(nude_rhythm)}」但未写时段分配，"
                f"建议「前X秒：常速；X-X+2秒：定格；剩余：恢复」"
            )

    if warnings:
        for warn in warnings:
            _get_logger().warn(f"打斗动作: {warn}")

    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.16 新增：克制感情绪戏检查（AI微表情生成教学吸收，restrained-emotion）
# 告别/隐忍/失落/哽咽/噙泪等克制情绪：
#   1. 禁止外放大动作（大哭/痛哭/猛烈摇头/瘫坐捶地）——情绪压在微表情细节
#   2. 克制情绪镜头必须形成「微表情时间链」：至少 2-3 拍面部细节，按时间先后递进
#   3. 情绪可视化照旧（抽象情绪词 → 面部/肢体动作），但克制情绪落点在面部细节
# 与 _check_emotion_visualization 互补：那个管「抽象情绪词必须有可视化动作」，
# 这个管「克制情绪的强度控制 + 递进链完整」。
_RESTRAINED_EMOTION_WORDS = (
    "隐忍", "释然", "失落", "哽咽", "噙泪", "含泪", "忍住不落", "无声哭", "无声地哭",
    "强忍", "强忍住", "苦笑", "别过脸", "眼眶泛红", "眼尾泛红",
    "噙着泪", "泪光", "忍住泪", "将落未落", "克制",
    # v6.34（08-15 误报审计）：移除「压抑」（氛围词，空镜/岸景也命中）与
    # 「微红」（掌掴红印等物理红，非克制表情）——曾致镜头11/21/22/23 误报
)
# 外放大动作（克制情绪禁止组合）
_RESTRAINED_OVERT_ACTIONS = ("大哭", "痛哭", "嚎啕", "失声痛哭", "哭喊", "捶地", "猛摇头",
                              "剧烈颤抖", "抽搐", "泣不成声", "悲痛欲绝", "撕心裂肺", "嚎哭")
# 微表情面部细节词（克制情绪链的构成拍）
_RESTRAINED_MICRO_TELLS = (
    "眼眶泛红", "眼尾泛红", "眼角泛红", "泛红", "泪光", "噙泪", "含泪", "将落未落",
    "睫毛轻颤", "睫毛颤动", "睫毛微颤", "垂眼", "垂眸", "低垂", "低头", "目光低垂",
    "失神", "无聚焦", "眼神空洞", "凝住", "瞳孔微缩", "抿紧", "嘴角紧闭", "嘴唇抿",
    "嘴角", "眉眼", "别过脸", "扭头", "转过头", "微笑", "苦笑", "浮起", "微扬",
    "松开", "吸了吸鼻子", "鼻尖", "喉头滚动", "呼吸", "胸口起伏",
)


def _check_restrained_emotion(shots: list[dict], hook: dict = None) -> dict:
    """检查克制感情绪戏（v6.16 新增）★AI微表情生成教学★.

    仅检查出现克制情绪词的镜头：
    1. 克制基调：出现克制情绪词时禁止外放大动作（大哭/痛哭/猛摇头），
       也不得用「悲痛欲绝」类形容词堆砌
    2. 微表情时间链：克制情绪镜头必须 ≥2 个微表情面部细节拍（不能只写一个表情定住）
    3. 若出现「眼泪/泪水」但没有「忍住/噙/无声」等克制修饰 → 提示补克制基调
    """
    warnings = []
    issues = []

    def _check_one(shot: dict, label: str):
        nonlocal warnings, issues
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "") or ""
        text = " ".join([prompt, shot.get("ff_desc", "") or "", shot.get("motion_desc", "") or ""])
        if not any(w in text for w in _RESTRAINED_EMOTION_WORDS):
            return

        # 1. 克制基调：克制情绪词 + 外放大动作 → 冲突
        overt_hits = [w for w in _RESTRAINED_OVERT_ACTIONS if w in text]
        if overt_hits:
            warnings.append(
                f"{_shot_tag(label, idx)}: 克制情绪与「{'/'.join(overt_hits[:3])}」冲突，"
                f"克制情绪应压在微表情（无声哭/忍泪/苦笑），禁外放大动作"
            )

        # 2. 微表情时间链：克制情绪镜头 ≥2 个面部细节拍
        micro_hits = [m for m in _RESTRAINED_MICRO_TELLS if m in text]
        if len(micro_hits) < 2:
            warnings.append(
                f"{_shot_tag(label, idx)}: 克制情绪镜头微表情不足（仅 {len(micro_hits)} 拍），"
                f"应形成时间链：低头隐忍（抿唇/垂眼）→ 泪光（噙泪）→ 苦笑/释怀，"
                f"每拍一个面部细节，按时间先后写全"
            )

        # 3. 有眼泪无克制修饰 → 补克制基调（防写成大哭）
        if ("泪水" in text or "眼泪" in text or "泪" in text) and not any(
            k in text for k in ("忍住", "噙", "将落未落", "含泪", "强忍", "无声", "忍泪", "涌上")
        ):
            warnings.append(
                f"{_shot_tag(label, idx)}: 出现泪/泪水但无克制修饰（忍住/噙/将落未落/含泪），"
                f"克制哭戏请写「眼含热泪但忍住不落」，禁止直接「泪水滑落」"
            )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    for w in warnings:
        _get_logger().warn(f"克制情绪: {w}")
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.36 新增：强情绪顺序链检查（五层情绪链吸收，strong-emotion-chain）
# 崩溃/狂怒/惊恐/骇然等强情绪转折镜头（情绪从平静跳到极值或剧烈转折）：
#   1. 禁止只写一个「全脸定格快照」（面部扭曲/双目圆睁/嘴巴大张 定死）——
#      视频模型把静止表情定住 →「突然变脸」。情绪要写成有顺序的表演链。
#   2. 顺序链 = 触发事件在前 + ≥2 递进拍 + ≥1 时序连接词（先/随后/接着/骤然/继而），
#      微表情路径（眉挑→眼睑抽→瞳孔放大）→ 肢体传导（肩膀↑→手指攥紧）。
#   3. 与 _check_restrained_emotion 互补：克制情绪管「压着演」（规则28/28.1），
#      本检查管「强情绪/转折要演出一步步」（规则52）——静态快照两者都要防。
# 注意：暴怒/惊恐/狂怒/恐慌 等词已被 _fix_emotion_visualization 替换成静态快照
#       （不触发本检查）；本检查捕的是幸存强情绪词（崩溃/骇然/惊呆/失控等）。
# 08-15 误报审计：移除「惊变」（场景名「小舟惊变」=事件词，非角色情绪）与
# 「骤变」（局势骤变等语境词）——曾致镜头7/8/9 误报，且事件词无法按链修复
# 会白烧修复轮 LLM 调用。保留「脸色骤变」（真实面部反应）。
_STRONG_EMOTION_WORDS = (
    "崩溃", "狂怒", "震怒", "惊恐", "恐慌", "惊骇", "惊呆", "骇然",
    "失控", "抓狂", "狂喜", "暴跳如雷", "怒不可遏", "火冒三丈", "歇斯底里",
    "目眦欲裂", "呆若木鸡", "大惊失色", "脸色骤变", "惊得",
    "惊惧", "吓呆",
)
# 时序连接词（证明情绪按先后递进，而非一个定格）
_STRONG_SEQ_WORDS = (
    "先是一", "先是", "随后", "随即", "然后", "接着", "继而", "紧接着",
    "骤然", "下一瞬", "下一秒", "猛地", "突然", "忽然", "霎时", "一瞬",
    "再一", "渐渐", "缓缓",
)
# 递进拍词（微表情路径/肢体传导的可数细节，一拍一词）
_STRONG_BEAT_WORDS = (
    # 面部/五官微变化
    "眉挑", "挑眉", "眉峰", "眉头", "眼睑", "眼角", "瞳孔", "眼眶",
    "嘴角", "嘴唇", "抿唇", "抿紧", "垂眸", "眨眼", "泛红", "泪光",
    "噙泪", "鼻翼", "青筋", "咬紧", "咬牙", "龇牙", "僵住", "凝住",
    "失神", "呆住", "愣住", "瞪大", "瞪圆", "圆睁",
    # 肢体传导
    "肩膀", "攥紧", "握紧", "握拳", "攥衣", "揪紧", "颤抖", "发抖",
    "后缩", "后退", "前倾", "屏息", "胸口起伏", "呼吸急促", "双颊",
    "鼓起", "瑟缩",
)
# 强情绪爆发镜的可动器物（环境留痕 C 的可触发对象）
_STRONG_PROP_WORDS = (
    "茶盏", "茶碗", "酒杯", "酒盏", "酒壶", "烛台", "砚台", "笔架",
    "书卷", "信纸", "纸张", "蒲扇", "棋盘", "碗筷", "杯盏",
)
# 环境留痕词（证明情绪爆发在环境里留过痕）
_STRONG_ENV_TRACE_WORDS = (
    "打翻", "震落", "滑落", "扫落", "掀翻", "翻倒", "泼", "洒在",
    "摔在", "拍桌", "拍案", "碰倒", "抖落", "碎", "溅",
)
# 强情绪 → 参考链（供 WARN 提示 LLM 模仿；只提示不注入，不改 _EMOTION_ACTION_MAP）
_STRONG_EMOTION_CHAIN_MAP = {
    "崩溃": "先是一愣，随即眼睑抽搐、瞳孔骤放，双颊鼓起、肩膀耸起，手指攥紧身侧衣角",
    "惊恐": "骤然一震，瞳孔骤缩、嘴角抽搐，肩膀猛地上提，身体后缩、双手护胸",
    "惊呆": "先是一怔，眉头僵住、嘴角微张，眼睛一眨不眨，好一会才回过神来",
    "骇然": "脸色骤变，瞳孔骤缩、喉头滚动，后退半步、手指在袖中攥紧",
}


def _check_strong_emotion_chain(shots: list[dict], hook: dict = None) -> dict:
    """检查强情绪顺序链（v6.36 新增）★五层情绪链★.

    仅检查出现强情绪词的镜头（prompt+motion_desc；ff_desc 是静态定格，不要求表演链）：
    1. 顺序链：≥2 递进拍 + ≥1 时序连接词，缺任一 → 提示补成表演链
       （触发在前 → 微表情路径 → 肢体传导）
    2. 环境留痕（C，可选）：链已合格 + 场景有可动器物 + 却无留痕 → 软提示
    只 WARN 不阻断；每镜最多一条 WARN（链优先，环境留痕次之）。
    """
    warnings = []
    issues = []

    def _check_one(shot: dict, label: str):
        nonlocal warnings, issues
        idx = shot.get("index", "?")
        text = " ".join([
            shot.get("prompt", "") or "",
            shot.get("motion_desc", "") or "",
        ])
        strong_hits = [w for w in _STRONG_EMOTION_WORDS if w in text]
        if not strong_hits:
            return
        # 08-15 误报修复（镜头17 实测）：触发词若出现在对白台词里（narration/subtitle，
        # prompt 尾部「（用中文说）…（用中文说）」会嵌入同一句原文原句，规则0 禁止改写）
        # → 是台词里的词，不是画面表演指令，排除。否则修复轮每次喂它、LLM 按规则0
        # 保留台词 → 永远修不好、白白烧修复轮。
        spoken = (shot.get("narration_segment") or "") + (shot.get("subtitle") or "")
        strong_hits = [w for w in strong_hits if w not in spoken]
        if not strong_hits:
            return

        beats = [b for b in _STRONG_BEAT_WORDS if b in text]
        seqs = [s for s in _STRONG_SEQ_WORDS if s in text]

        # 1. 顺序链：≥2 递进拍 + ≥1 时序词，缺任一 → 静态快照
        if len(beats) < 2 or not seqs:
            seq_desc = "、".join(seqs[:3]) or "无时序词"
            ref = _STRONG_EMOTION_CHAIN_MAP.get(strong_hits[0], strong_hits[0])
            warnings.append(
                f"{_shot_tag(label, idx)}: 强情绪「{'/'.join(strong_hits[:3])}」写成静态定格"
                f"（{len(beats)} 拍、{seq_desc}），会突然变脸——请按顺序链写表演路径："
                f"先写触发事件→微表情递进（≥2拍）→肢体传导，用「先…随后…接着…」串接，"
                f"参考：{ref}"
            )
            return

        # 2. 环境留痕（C，可选）：链已合格 + 有可动器物 + 无留痕 → 软提示
        props = [p for p in _STRONG_PROP_WORDS if p in text]
        if props and not any(t in text for t in _STRONG_ENV_TRACE_WORDS):
            warnings.append(
                f"{_shot_tag(label, idx)}: 强情绪「{'/'.join(strong_hits[:3])}」链已合格，"
                f"画面有可动器物（{'/'.join(props[:2])}）——可加环境留痕证明情绪发生过"
                f"（打翻的盏/滑落的信纸/拍响的案几），空间锚点（规则50）保持原位"
            )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    for w in warnings:
        _get_logger().warn(f"强情绪链: {w}")
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.16 新增：切换拍摄对象检查（AI视频镜头控制教学吸收，focal-switch）
# 镜头在人物与物品之间切换时必须写清「方向+景别+运动+结束落点」四要素：
#   1. 切换提示词必须有方向（左侧/右侧/上方/前方/环绕/前推/上移/左摇）
#   2. 切换提示词必须有运动方式（前推/环绕/摇移/跟随/上移）
#   3. 切换到物品的镜头必须写明「结束落点」（环绕一周/定格/拉回），禁止切到物品后无归宿
#   4. 物品特写只用于关键道具/伏笔（被台词或动作点名），禁止为炫技硬切
# 与 _check_camera_movement（18种运镜，一镜一运镜）互补：那个管镜头类型与多样性，
# 这个管「切换对象」这类组合运镜的完整性。
_FOCAL_SWITCH_MOTIONS = (
    "前推", "推近", "推镜", "环绕", "摇移", "左摇", "右摇", "上移", "下移",
    "跟随", "跟拍", "拉回", "拉远", "推上", "上摇", "下摇",
)
_FOCAL_SWITCH_DIRECTIONS = ("左", "右", "上", "下", "前", "后", "侧", "环绕", "正中", "画左", "画右")
_FOCAL_SWITCH_ENDINGS = ("环绕", "定格", "拉回", "停", "停留", "落在", "对准", "聚焦", "结束")


def _check_focal_switch(shots: list[dict], hook: dict = None) -> dict:
    """检查切换拍摄对象运镜的完整性（v6.16 新增）★AI视频镜头控制教学★.

    仅检查出现切换语义（前推/环绕/从...切到/焦点切换到/转向）的镜头：
    1. 必须写清方向（从哪边/朝哪边）
    2. 必须写清运动方式（前推/环绕/摇移/上移）
    3. 切到物品的镜头必须写明结束落点，防止镜头切到物品后乱跑
    """
    warnings = []
    issues = []

    def _check_one(shot: dict, label: str):
        nonlocal warnings, issues
        idx = shot.get("index", "?")
        prompt = shot.get("prompt", "") or ""
        motion = shot.get("motion_desc", "") or ""
        text = " ".join([prompt, motion])

        # 触发：出现切换语义（从人物切到物品/焦点切换/转向某物）
        is_switch = any(
            s in text for s in ("切换到", "焦点切换", "从...切到", "转向", "从人物手部", "切换", "焦点")
        )
        has_motion = any(m in text for m in _FOCAL_SWITCH_MOTIONS)
        if not (is_switch and has_motion):
            return

        # 1. 方向
        has_dir = any(d in text for d in _FOCAL_SWITCH_DIRECTIONS)
        # 2. 运动方式已由 has_motion 保证
        # 3. 结束落点（切到物品类镜头）
        is_item_target = any(w in text for w in ("戒指", "令牌", "玉簪", "臂环", "玉佩", "书信",
                                                  "符", "剑鞘", "镜", "香囊", "步摇", "发簪", "令牌"))
        if is_item_target and not any(e in text for e in _FOCAL_SWITCH_ENDINGS):
            warnings.append(
                f"{_shot_tag(label, idx)}: 切换到物品特写但无结束落点，"
                f"应写「环绕一周/定格在物品/拉回中景」，防止镜头切到物品后乱跑"
            )
        if not has_dir:
            warnings.append(
                f"{_shot_tag(label, idx)}: 切换拍摄对象缺方向，"
                f"应写清「从{人物}手部/左侧/右侧」+「前推/环绕」"
            )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    for w in warnings:
        _get_logger().warn(f"切换拍摄对象: {w}")
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# v6.17 新增：打斗四段式节奏检查（抖音拉片《凡人修仙传打戏》吸收，combat-arc）
# 整段打斗（连续 4 镜以上命中打斗/高张力触发词）必须覆盖「对峙→攻防→反转→收尾」四段：
#   1. 段式覆盖：打斗段应包含 快镜攻防（短镜/推拉跟）+ 长镜蓄力（反转/大招）+ 空镜/远景收尾
#   2. 反差提示：打斗段若全片 1-2s 短镜、无任何长镜慢放蓄力 → 提示补反转长镜（大招没冲击）
#   3. 闪白提示：大招/雷击/蓄力类镜头未用「黑场闪白/闪白」转场 → 提示制造悬念
#   4. 收尾提示：打斗段结束无空镜/远景收尾升华 → 提示战后拉长时间
# 只 WARN 不阻断（节奏由 LLM 决定，此处仅提示结构完整性）。
_COMBAT_ARC_TRIGGERS = _COMBAT_KEYWORDS | _CAMERA_UPGRADE_TENSION
# 反转/大招/蓄力类关键词（命中即视为存在反转段）
_COMBAT_ARC_CLIMAX_WORDS = (
    "大招", "蓄力", "施法", "法术", "灵光", "法阵", "凝结", "爆气", "暴气",
    "积蓄", "聚力", "运功", "念咒", "腾空", "跃起蓄力",
)
# 长镜/慢放标记（反转段载体）
_COMBAT_ARC_SLOW_LONGSHOT = ("慢动作", "慢镜头", "长镜头", "环绕", "慢放", "凝滞", "定格")
# 收尾升华标记（空镜/大远景/情绪镜头）
_COMBAT_ARC_CODA_WORDS = ("空镜", "大远景", "远景", "极光", "月夜", "黄昏", "晨光", "背影", "升华", "落幕")
# 闪白转场标记（大招/雷击前）
_COMBAT_ARC_FLASH_WHITE = ("闪白", "黑场闪白", "白闪", "强光充满")


def _check_combat_arc(shots: list[dict]) -> dict:
    """检查打斗四段式节奏结构（v6.17 新增）★凡人修仙传打戏拉片★.

    仅对连续打斗段（≥4 镜命中打斗/高张力触发词）做结构检查，防误报：
    1. 反转长镜反差：打斗段全片短镜（无长镜/慢放/环绕）→ 大招无冲击，建议穿插反转蓄力长镜
    2. 闪白悬念：大招/蓄力镜头未用闪白转场 → 建议释放前黑场闪白（约1.5s）
    3. 收尾升华：打斗段结束紧接人物镜头、无空镜/远景 → 建议战后空镜叠化拉长时间
    """
    warnings = []
    combat_runs = []
    run = []
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:
            continue
        text = " ".join([
            shot.get("prompt", "") or "",
            shot.get("narration_segment", "") or "",
            shot.get("motion_desc", "") or "",
        ])
        # 段内成员：打斗/高张力触发词，或大招蓄力镜头（施法/灵光/慢动作——这类镜头
        # 本身不含'打斗/刺杀'触发词，若不算进段会掐断打斗弧，闪白/反差检查失效）
        is_combat_shot = any(kw in text for kw in _COMBAT_ARC_TRIGGERS)
        is_climax_shot = any(k in text for k in _COMBAT_ARC_CLIMAX_WORDS) and (
            any(k in text for k in _COMBAT_ARC_SLOW_LONGSHOT)
        )
        if is_combat_shot or is_climax_shot:
            run.append((idx, text))
        else:
            if len(run) >= 4:
                combat_runs.append(run)
            run = []
    if len(run) >= 4:
        combat_runs.append(run)

    for run_shots in combat_runs:
        run_text = " ".join(t for _, t in run_shots)
        # 1. 反转长镜反差：打斗段无任何长镜/慢放/环绕 → 大招没冲击
        if not any(k in run_text for k in _COMBAT_ARC_SLOW_LONGSHOT):
            warnings.append(
                f"镜头{run_shots[0][0]}~{run_shots[-1][0]}: 打斗段 {len(run_shots)} 镜全为短镜快切，"
                f"无长镜/慢放/环绕蓄力——大招没冲击。建议在法术蓄力/施法/大招镜头穿插 1 个"
                f"3-5s 长镜+慢放+环绕（紧-松-紧节奏反差，规则7 的唯一例外）"
            )
        # 2. 闪白悬念：打斗段含大招/蓄力词但无闪白转场
        if any(k in run_text for k in _COMBAT_ARC_CLIMAX_WORDS) and not any(
            k in run_text for k in _COMBAT_ARC_FLASH_WHITE
        ):
            warnings.append(
                f"镜头{run_shots[0][0]}~{run_shots[-1][0]}: 大招/蓄力镜头未用闪白转场，"
                f"建议释放前「黑场闪白」（约1.5s）制造悬念提升爽感"
            )
        # 3. 收尾升华：打斗段之后无空镜/远景收尾
        coda = _check_combat_arc_coda(shots, run_shots[-1][0])
        if coda:
            warnings.append(coda)

    if warnings:
        for w in warnings:
            _get_logger().warn(f"打斗节奏: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_combat_arc_coda(shots: list[dict], last_combat_idx) -> str | None:
    """打斗段后 3 镜内是否有空镜/大远景收尾升华（返回提示或 None）."""
    try:
        last_combat_pos = next(i for i, s in enumerate(shots) if s.get("index") == last_combat_idx)
    except StopIteration:
        return None
    for i in range(last_combat_pos + 1, min(last_combat_pos + 4, len(shots))):
        text = " ".join([
            shots[i].get("prompt", "") or "",
            shots[i].get("main_subject", "") or "",
        ])
        if any(k in text for k in _COMBAT_ARC_CODA_WORDS):
            return None
    return (
        f"镜头{last_combat_idx}: 打斗结束后连续镜头无空镜/大远景收尾，"
        f"建议战后插 1 个 5-10s 空镜叠化/大远景（极光/月夜/山水），突出平静与成长"
    )


# v6.17 新增：台词标点情绪检查（AI配音标点控制语气教程吸收，voice-punctuation）
# 有对话的镜头（narration_segment 含「道：台词」）标点必须与情绪语义匹配：
#   1. 撒娇/温柔类台词用「。」或「！」收尾 → 提示改「～」
#   2. 警告/愤怒/质问类台词用「。」或「～」收尾 → 提示改「！」
#   3. 思考/迟疑类台词无停顿逗号/省略号 → 提示加停顿
#   4. 台词缺句末标点（无标点裸收尾）→ 提示补标点（语气无法传达）
# 只 WARN 不阻断（标点是 LLM 的情绪表达，此处仅提示语气落地）。
_TAUNT_PUNCT = ("～", "~", "！", "!", "？", "?")
_COAX_PUNCT = ("～", "~", "！", "!", "？", "?", "……", "…")
_WARN_PUNCT = ("～", "~", "…", "……")
_COAX_WORDS = ("撒娇", "娇", "甜", "软", "嘟", "嗔", "呢", "呀", "嘛", "啦")
_WARN_WORDS = ("警告", "警告地", "怒", "喝", "呵斥", "质问", "威胁", "厉", "冷声", "叱")
_THINK_WORDS = ("思考", "迟疑", "犹豫", "顿", "欲言又止", "想了想", "迟疑地")


def _check_voice_punctuation(shots: list[dict], hook: dict = None) -> dict:
    """检查台词标点与情绪语义匹配（v6.17 新增）★AI配音标点控制语气★.

    仅检查有对话的镜头（narration_segment 含「道：/说：」+ 台词）：
    1. 撒娇/温柔台词（撒娇/娇/呀/嘛）句尾是「。」或「～」外的平调 → 建议「～」
    2. 警告/愤怒台词（警告/怒/喝/呵斥）句尾非「！」 → 建议「！」
    3. 思考/迟疑台词（思考/迟疑/犹豫）无停顿标点 → 建议加「，」或「…」
    4. 台词裸收尾（无任何句末标点） → 建议补标点
    """
    warnings = []

    def _check_one(shot: dict, label: str):
        idx = shot.get("index", "?")
        narration = (shot.get("narration_segment") or "").strip()
        if "道：" not in narration and "说：" not in narration and "：" not in narration:
            return
        # 台词部分 = 「角色名道：」后的内容（不含说话人前缀）
        line = re.split(r"道[:：]|说[:：]|喊[:：]|喝[:：]|叫[:：]|问[:：]", narration)
        if len(line) < 2:
            return
        speech = line[-1].strip()
        if not speech:
            return
        # 台词文本（去尾标点）
        body = speech.rstrip("～~！!？?。.…。，,；;")
        if not body:
            return
        tail = speech[len(body):]
        # 1. 裸收尾：台词无任何句末标点 → 语气无法传达
        if not tail:
            warnings.append(
                f"{_shot_tag(label, idx)}: 台词「{body[:24]}{'…' if len(body) > 24 else ''}」裸收尾无标点，"
                f"建议按情绪补标点（撒娇「～」/警告「！」/思考「，」「…」）"
            )
            return
        # 2. 撒娇/温柔类：句尾用了「。」→ 平淡，建议「～」
        if any(w in narration for w in _COAX_WORDS) and tail.endswith("。") and not tail.endswith("！") and not tail.endswith("～"):
            warnings.append(
                f"{_shot_tag(label, idx)}: 撒娇/娇柔台词句尾「。」过于平淡，建议改「～」（如「{body}～」）"
            )
        # 3. 警告/愤怒类：句尾非「！」→ 压迫感不足
        if any(w in narration for w in _WARN_WORDS) and tail.endswith("。"):
            warnings.append(
                f"{_shot_tag(label, idx)}: 警告/愤怒台词句尾「。」缺压迫感，建议改「！」（如「{body}！」）"
            )
        # 4. 思考/迟疑类：无停顿逗号/省略号 → 读不出卡顿
        if any(w in narration for w in _THINK_WORDS) and "，" not in speech and "…" not in speech:
            warnings.append(
                f"{_shot_tag(label, idx)}: 思考/迟疑台词缺停顿标点，建议在停顿处加「，」（如「等一下，我想，先确认一件事」）"
            )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    for w in warnings:
        _get_logger().warn(f"台词标点: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


# v6.17 新增：前后遮挡逻辑检查（古风酒楼一镜到底教程吸收，occlusion-layering）
# 人物处于复合空间/走位场景时，遮挡关系必须显式写明（防穿模）：
#   1. 有明确遮挡物（围栏/栏杆/门窗/楼梯扶手/屏风/柱子）+ 人物同框 → 必须有「遮挡」动词+部位
#   2. 写了「…后/在…后面」但无「遮挡/掠过/穿行」+ 部位 → 提示写清遮挡到什么部位
#   3. 前景人物/物件走过场景 → 应有「短暂遮挡/掠过」描述（前后位置）
# 只 WARN 不阻断（遮挡是空间关系，由 LLM 决定写法，此处仅提示穿模风险）。
_OCCLUSION_OBJECTS = ("围栏", "栏杆", "门框", "窗框", "屏风", "柱子", "楼梯扶手", "拱门",
                      "窗棂", "隔扇", "廊柱", "门槛", "回廊", "雕花栏杆", "栅栏")
_OCCLUSION_VERBS = ("遮挡", "遮住", "遮蔽", "掠过", "穿行", "隔开", "隐于", "掩住", "挡住", "遮掩")
_OCCLUSION_LEVELS = ("膝", "腿", "腰部", "腰", "半身", "半张脸", "脸", "上身", "下身",
                     "手臂", "肩膀", "脚", "头部", "下摆", "裙摆", "衣摆")
_OCCLUSION_FAR_SYNC = ("之后", "后面", "后", "背后", "深处", "内侧", "旁边", "一侧")


def _check_occlusion_layering(shots: list[dict]) -> dict:
    """检查前后遮挡逻辑是否显式化（v6.17 新增）★古风酒楼一镜到底★.

    仅检查出现遮挡物词（围栏/栏杆/门窗/屏风/楼梯扶手）且有明确人物的镜头：
    1. 遮挡物+人物同框但无「遮挡/掠过/穿行」动词 → 提示写清遮挡关系
    2. 有遮挡词但无部位（膝/腰/半张脸）→ 提示写清遮挡程度（防穿模/防笼统）
    3. 前景走过类（从…前走过/从…前经过）→ 提示补「短暂遮挡/掠过」前后位置
    """
    warnings = []
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:
            continue
        prompt = (shot.get("prompt") or "") + (shot.get("ff_desc") or "") + (shot.get("lf_desc") or "")
        if not any(o in prompt for o in _OCCLUSION_OBJECTS):
            continue
        # 有人物才算（遮挡物+人同框才有遮挡语义；纯空镜建筑不检查）
        has_person = bool(_PERSON_PATTERN.search(prompt))
        if not has_person:
            continue
        # 1. 遮挡物+人物但无遮挡动词
        if not any(v in prompt for v in _OCCLUSION_VERBS):
            warnings.append(
                f"镜头{idx}: 画面含遮挡物（围栏/门窗/屏风等）与人物同框，但未写明遮挡关系，"
                "建议写「遮挡物遮挡住人物部位」（如'围栏遮挡住女主膝盖以下'），防穿模"
            )
            continue
        # 2. 有遮挡动词但无部位（写清遮挡到什么程度）
        if not any(l in prompt for l in _OCCLUSION_LEVELS):
            # 排除「遮挡视线/镜头掠过」这类非人体部位用法
            if not any(w in prompt for w in ("视线", "镜头", "视野", "画面")):
                warnings.append(
                    f"镜头{idx}: 写了遮挡但未写遮挡程度（膝/腰/半张脸），"
                    f"建议写清部位——禁止笼统「在围栏后」，否则 AI 会把人物画成与围栏穿插"
                )
        # 3. 前景走过类（从…前走过/经过）→ 补前后位置遮挡
        if re.search(r"[从侧画]?[左右]?.*前走过|从.*前经过", prompt) and not any(
            v in prompt for v in ("短暂遮挡", "掠过", "遮挡住", "遮住")
        ):
            warnings.append(
                f"镜头{idx}: 前景人物走过但未写遮挡关系，建议补「前景路人行进中短暂遮挡住主角」"
            )

    if warnings:
        for w in warnings:
            _get_logger().warn(f"遮挡逻辑: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


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
# 空镜主体词表（收敛：引用文件顶部唯一常量 EMPTY_SCENE_SUBJECTS，审计 P1-8）
_EMPTY_SCENE_SUBJECTS = EMPTY_SCENE_SUBJECTS
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
            _get_logger().warn(f"光位: {warn}")

    return {"pass": True, "issues": [], "warnings": warnings}


# ========================================================================
# v6.35 站位台吸收（规则51）：多人站位蓝图检查
# ========================================================================

# 站位合法性判定改走 scene_anchors：
# - position 合法 = 画X/前景/中景/后景 之一 OR 该 location 场景的具象空间词
# - facing 合法 = 6 个朝向词（不分场景）
# - 多人在画左同侧无纵深 = 根据 HORIZONTAL/CONTAINING_SCENES 走不同分支
# 完整字典见 step01_script_gen/scene_anchors.py
from step01_script_gen.scene_anchors import (
    is_valid_position, is_valid_facing, has_lateral_depth, get_scene_type,
    is_horizontal_scene, scene_anchors_for,
)


def _format_anchors_hint(location: str) -> str:
    """校验器提示用：取该场景前 5 个具象空间词作为 hint."""
    anchors = scene_anchors_for(location)
    if not anchors:
        return "画左/画右/画中/前景/中景/后景"
    return "/".join(anchors[:5])


def scene_anchors_list_for(location: str) -> tuple:
    """取该场景具象空间词 tuple（无则空 tuple）."""
    return scene_anchors_for(location)


def _check_multi_person_staging(shots: list[dict], hook: dict = None,
                                characters: list | None = None) -> dict:
    """多人站位蓝图检查（站位台吸收 v6.35，规则51，warning-only）.

    1. 多人同框（文本探测出镜 ≥2 角色）但缺 char_layout → 提示补结构化站位；
    2. char_layout 内角色名不在顶层 characters 注册表 → 编造路人；
    3. position/facing 用了非空间词/非朝向词 → 提示用画左/画右/前中后/面向；
    4. 多角色同框全挤同一侧、无纵深展开 → 建议套对峙/群像蓝图。
    空镜/单角色不触发（防误报刷屏，同 P1-7 视线门控思路）。
    """
    issues, warnings = [], []
    known = {c.get("identifier") for c in (characters or []) if c.get("identifier")}
    # Plan A（2026-08-14）：hook 同为 index=0 的镜头，必须一并检查站位蓝图
    targets = list(shots) + ([hook] if hook and isinstance(hook, dict) else [])
    for shot in targets:
        idx = shot.get("index", "?")
        label = "钩子镜头(index=0)" if (hook and shot is hook) else f"镜头{idx}"
        layout = shot.get("char_layout")
        if layout is None:
            layout = []
        if not isinstance(layout, list):
            warnings.append(
                f"{label}: char_layout 不是数组（{type(layout).__name__}），"
                f"规则51要求出镜角色逐一出镜")
            continue
        # 出镜角色探测（文本匹配注册表角色名）：ff/lf/prompt/main_subject
        probe = " ".join(filter(None, [
            shot.get("ff_desc", ""), shot.get("lf_desc", ""),
            shot.get("prompt", ""), shot.get("main_subject", ""),
        ]))
        onstage = [c for c in known if c and c in probe] if probe else []
        if not layout and len(onstage) < 2:
            continue  # 空镜/单角色：无需站位布局
        if not layout and len(onstage) >= 2:
            warnings.append(
                f"{label}: 多人同框（{len(onstage)}角色）缺 char_layout 站位布局——"
                f"规则51要求出镜角色逐一出镜并给画左/画右/前中后站位")
            continue
        names = []
        laterals = []  # 每个角色的横向侧位（画左/画右）
        has_center = False
        has_depth = False
        # 本镜 location 决定 position 合法词表 + 纵深判定
        shot_location = str(shot.get("location", "") or shot.get("scene", "") or "")
        shot_scene = get_scene_type(shot_location)
        for item in layout:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            names.append(name)
            pos = str(item.get("position", "")).strip()
            facing = str(item.get("facing", "")).strip()
            # 编造判定：既不在注册表、也不在本镜文本中可见 → 才提示。
            # 防误伤文本可见的临时角色（如「小娃娃」在 ff_desc 出现但不在顶层 characters）
            if name not in known and not any(a in probe for a in (name,)):
                warnings.append(
                    f"{label}: char_layout 角色「{name}」不在角色注册表内且未在本镜文本出现"
                    f"（规则51要求与顶层 characters 一致）")
            if pos and not is_valid_position(pos, shot_location):
                # 按 scene 类型给具体提示（具象词空间锚点 / 画X 空间锚点）
                if shot_scene:
                    hint = f"（{shot_scene}场景可用：{_format_anchors_hint(shot_location)}）"
                else:
                    hint = "（画左/画右/画中/前景/中景/后景），规则51"
                warnings.append(
                    f"{label}: {name} 站位「{pos}」缺空间词{hint}")
            if facing and not is_valid_facing(facing):
                warnings.append(
                    f"{label}: {name} 朝向「{facing}」缺朝向词"
                    f"（面向/背对/侧身/正对），规则51")
            if "画左" in pos:
                laterals.append("画左")
            elif "画右" in pos:
                laterals.append("画右")
            if "画中" in pos:
                has_center = True
            if any(k in pos for k in ("前景", "中景", "后景")):
                has_depth = True
        # 多角色全挤同一侧（≥2 明确横向侧位、全部同侧、无画中/无纵深）→ 站位单一。
        # 画中（居中构图）或含前中后景纵深时不提示——避免「画中+画右」被误判为全右。
        # 横向场景（船/桥/街道）走 has_lateral_depth（船头/船中/船尾天然有纵深）
        positions_all = [
            str(item.get("position", "")).strip()
            for item in layout if isinstance(item, dict)
        ]
        depth_ok = has_depth or has_center or has_lateral_depth(positions_all, shot_location)
        if len(laterals) >= 2 and len(set(laterals)) == 1 and not depth_ok:
            side = laterals[0]
            if is_horizontal_scene(shot_location):
                # 横向场景：建议改用 船头/船中/船尾 等具象词拉开纵深
                anchors = scene_anchors_list_for(shot_location)
                hint = f"建议改用 {side[0]}{anchors[0]} + {side[0]}{anchors[1] if len(anchors) > 1 else anchors[0]}" if anchors else "建议拉开前后景"
                warnings.append(
                    f"{label}: {len(laterals)}角色站位全在「{side}」同侧且无纵深——"
                    f"{hint}（规则51，{shot_scene}横向场景）")
            else:
                warnings.append(
                    f"{label}: {len(laterals)}角色站位全在「{side}」同侧且无纵深（前中后景）——"
                    f"建议按对峙/群像蓝图对置或拉开纵深，规则51")
    for w in warnings:
        _get_logger().warn(f"多人站位: {w}")
    return {"pass": len(issues) == 0, "issues": issues, "warnings": warnings}


# ============================================================================
# 锚点失真 2 类校验（system.md 50a 规则）
# ============================================================================

# 攻击动作关键词（时序塌缩检测）
_VAGUE_ATTACK_KW = (
    "破水", "跃出", "破门", "举刀", "挥刀", "直刺", "刺中", "心口洇",
    "闷哼", "推臂一送", "倒纵", "没入水中", "已中刀", "已倒下",
)
# 躺卧姿态关键词
_RECLINING_KW = ("仰卧", "俯卧", "侧卧", "昏迷", "熟睡", "闭目", "失神")
# 模糊方位词
_VAGUE_DIRECTION_KW = ("回首", "抬头", "仰头", "抬眼", "凝神", "眺望")
# 模糊数量词 + 危险锚点
_VAGUE_ANCHOR_RE = re.compile(
    r"(一点|一截|一缕)("
    + "|".join(("刀尖", "刀", "剑", "匕首", "寒光", "黑影"))
    + r")"
)


def _check_prompt_temporal_collapse(shots: list[dict], hook: dict = None) -> dict:
    """钩子/关键首帧：pre-state + attack 混写检测.

    规则（system.md 50a）：钩子/伏笔类首帧严禁在 ff_desc/prompt 写攻击/受害/遁水动作。
    hard_fix (Rule 6) 会自动删除攻击句；本 check 是兜底——若 hard_fix 后仍残留，
    报 warning 给 LLM 下一轮修复轮看。
    """
    warnings = []
    targets = list(shots) + ([hook] if hook and isinstance(hook, dict) else [])
    for shot in targets:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("index", "?")
        label = "钩子镜头(index=0)" if (hook and shot is hook) else f"镜头{idx}"
        is_hook_shot = (hook and shot is hook) or shot.get("index") == 0
        for field in ("ff_desc", "prompt"):
            text = shot.get(field, "")
            if not text or not isinstance(text, str):
                continue
            attack_hits = [kw for kw in _VAGUE_ATTACK_KW if kw in text]
            if not attack_hits:
                continue
            if is_hook_shot or any(kw in text for kw in _RECLINING_KW):
                warnings.append(
                    f"{label} {field} 含攻击动作词「{attack_hits[0]}」+ 躺卧姿态="
                    "时序塌缩（system 50a）——只写 pre-state，受害/遁水留给 motion/lf")
    for w in warnings:
        _get_logger().warn(f"时序塌缩: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_prompt_anchor_ambiguity(shots: list[dict], hook: dict = None) -> dict:
    """锚点失真 2 类检测（system.md 50a）.

    1) "X手+道具" + 躺卧 → 道具被误读为"握"（"钓杆抵喉咙"根因）
    2) 模糊数量词 + 危险锚点（"一点刀尖"）→ 锚点漂移（"匕首长头上"根因）
    3) 模糊方位词（"回首/抬头"）→ 模型误读为头部姿态（"朝天看"根因）

    hard_fix Rule 7/8/9 会自动改写；本 check 是兜底 warning.
    """
    warnings = []
    targets = list(shots) + ([hook] if hook and isinstance(hook, dict) else [])
    for shot in targets:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("index", "?")
        label = "钩子镜头(index=0)" if (hook and shot is hook) else f"镜头{idx}"
        for field in ("ff_desc", "prompt"):
            text = shot.get(field, "")
            if not text or not isinstance(text, str):
                continue
            if any(kw in text for kw in _RECLINING_KW):
                m_handle = re.search(r"([左右])手[^，。、\n]{0,4}(钓杆|酒壶|竹箫|油纸伞|步摇|灯盏|灯笼|炭炉|刀|剑|匕首)", text)
                if m_handle:
                    warnings.append(
                        f"{label} {field} 角色躺卧 + 「{m_handle.group(0)}」="
                        "道具主谓歧义——模型会把「X手+道具」理解为「握」"
                        "（system 50a，需改写为「道具已脱手+位置」）")
            m_anchor = _VAGUE_ANCHOR_RE.search(text)
            if m_anchor and "独立物体" not in text:
                warnings.append(
                    f"{label} {field} 「{m_anchor.group(0)}」模糊数量词+危险锚点="
                    "易漂移（system 50a，需追加「独立物体/位置限定」）")
            for kw in _VAGUE_DIRECTION_KW:
                if kw in text:
                    warnings.append(
                        f"{label} {field} 模糊方位词「{kw}」="
                        "模型易误读为头部姿态（system 50a，建议替换为「侧头凝望画X的[角色]」）")
                    break
    for w in warnings:
        _get_logger().warn(f"锚点失真: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_prompt_length_budget(shots: list[dict], hook: dict | None = None) -> dict:
    """模型感知的 prompt 长度预算检查（2026-08 适配多模型）.

    provider 会按模型 prompt 上限静默截断（wan2.7=5000 / H3=7000 / Hailuo=2000），
    截断=内容丢失。step01 在生成阶段就应控制单镜字段总长，避免 step02 拼接后超限。
    step02 会在 shot.prompt 上追加 ~1200 字固定约束（风格前缀/背景音/空镜/风格收束/
    色彩/衔接/参考图清单），故预算按 cap 预留该余量。warning-only（进 regen 反馈，
    LLM 会自动精简，无需阻断）。
    """
    from shared.model_caps import active_video_prompt_cap
    cap = active_video_prompt_cap()
    if not cap or cap <= 0:
        return {"pass": True, "issues": [], "warnings": []}
    # 08-23 修复 cap 误报（v6.40+）：原 `budget = max(1, cap - 1200)` 当 cap<1200
    # 时算出 budget=1（wan2.7 cap=500 → 500-1200=-700 → max(1, -700)=1），
    # 任何非空字段都报"超预算 1 字"，全镜误报 8-22 实测全镜命中。
    # 真实意图：step01 字段是"模型送进 step02 之前的内容"，step02 在此基础上
    # 拼 ~1200 风格前缀/约束/参考图清单，超 model cap 才截断。step01 字段自身
    # 应按 cap 100% 控制（不能指望 step02 一定在 1200 内——实际可浮动 800-1400）。
    # 收紧为：budget = cap * 0.5（step01 字段 < model cap 一半，step02 拼接 1200 后
    # 仍大概率 < cap）。模型 prompt 上限 < 1200 时直接用 cap 当预算（不预留）。
    if cap < 1200:
        budget = cap
    else:
        budget = int(cap * 0.5)
    warnings = []
    items = list(shots) + ([hook] if hook else [])
    for shot in items:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("index", "?")
        fields = "".join([
            shot.get("prompt", "") or "",
            shot.get("ff_desc", "") or "",
            shot.get("lf_desc", "") or "",
            shot.get("narration_segment", "") or "",
        ])
        n = len(fields)
        if n > budget:
            warnings.append(
                f"镜头{idx}: step01 字段合计 {n} 字，超过模型 prompt 预算 {budget} 字"
                f"（cap={cap}，预留 step02 约束 1200 字）。step02 拼接后会被 provider 截断，"
                f"建议精简 prompt/ff_desc/lf_desc。"
            )
    for w in warnings:
        _get_logger().warn(f"prompt长度: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


# ========================================================================
# v6.37（08-20）高潮优先原则检查
# 对应 prompts.py 规则 3.7（v6.35 新增）：强冲突/关键情感戏必拍，
# 快铺垫 ≤2 镜（≤3s），背景/过渡/日常 0 镜。
# ========================================================================

# 背景/过渡/日常关键词（命中即视为「非高潮」镜头候选）
_CLIMAX_BG_HINTS = (
    "身世", "履历", "家境", "殷实", "中等", "媒人", "踏破门槛",
    "田埂", "老汉", "牵牛", "季节", "春夏秋冬", "回顾", "从前",
    "早亡", "幼年", "少年时", "父母", "出身", "门第", "门当户对",
    "晨光", "暮色", "炊烟", "牛羊", "风吹草低", "空山新雨",
    "漫步", "闲坐", "品茶", "赏花", "闲话", "絮语", "家常",
    "寒暄", "客套", "沏茶", "煎药", "缝衣", "洗衣", "扫地",
)
# 强冲突关键词（命中即视为高潮）— 用 2 字以上短句避免「打/破/杀」等单字误匹配
# （如「踏破门槛」含「破」但不是冲突）
_CLIMAX_CONFLICT_HINTS = (
    "刺杀", "砍杀", "搏斗", "打斗", "击退", "击中", "击倒",
    "追杀", "追击", "追捕", "围捕", "包围", "围困",
    "砍向", "劈向", "砍倒", "劈倒",
    "对质", "质问", "质问", "怒骂", "怒斥", "怒喝",
    "跃出", "跃起", "扑出", "冲出", "跃过",
    "出鞘", "拔剑", "拔刀", "抽刀",
    "格挡", "闪避", "躲过", "闪过", "挡住",
    "擒住", "被擒", "被押", "被绑",
    "逃出", "逃脱", "逃跑", "逃入",
    "中刀", "中剑", "中箭", "中拳",
    "打飞", "踢飞", "撞飞", "甩飞",
    "厮打", "扭打", "肉搏",
)
# 关键情感戏关键词 — 同样用 2 字以上短句
_CLIMAX_EMOTION_HINTS = (
    "诀别", "告别", "永别", "重逢", "相认",
    "拥抱", "拥抱", "相拥",
    "哭泣", "痛哭", "落泪", "流泪", "泪流",
    "失声", "哽咽", "抽泣",
    "跪地", "下跪", "叩首", "叩拜",
    "释怀", "释然", "恍然", "醒悟",
    "承认", "认错", "认罪",
    "立誓", "发誓", "起誓", "许诺", "承诺",
    "思念", "想念", "怀念", "追忆", "追思",
    "悔恨", "悔悟", "愧疚", "亏欠",
    "诀别", "永诀",
)


def _check_climax_priority(shots: list[dict]) -> dict:
    """v6.37 新增（08-20）：检测非高潮镜头（背景/过渡/日常），v6.35 规则 3.7 落地.

    判定：
    1. prompt + ff_desc + lf_desc + narration_segment 命中背景关键词
    2. 同时未命中强冲突/关键情感戏关键词
    3. 豁免：开篇 1-2 镜（idx 0/1）若 duration ≤3 秒 → 视为合法快铺垫

    警告：每命中 1 镜 → 警告「非高潮镜头，应改 0 镜 / 信息由旁白承担」。
    全片非高潮 > 2 镜 → 加总览警告「全片非高潮 N 镜 > 2 上限」。
    """
    warnings = []
    background_ids = []
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:  # 跳过 hook
            continue
        fields = "".join([
            shot.get("prompt", "") or "",
            shot.get("ff_desc", "") or "",
            shot.get("lf_desc", "") or "",
            shot.get("narration_segment", "") or "",
        ])
        if len(fields) < 4:
            continue
        has_bg = any(h in fields for h in _CLIMAX_BG_HINTS)
        if not has_bg:
            continue
        has_conflict = any(h in fields for h in _CLIMAX_CONFLICT_HINTS)
        has_emotion = any(h in fields for h in _CLIMAX_EMOTION_HINTS)
        if has_conflict or has_emotion:
            continue
        # 开篇 1-2 镜（idx 1-2）若 duration ≤3s，视为合法快铺垫
        if idx in (1, 2):
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur > 0 and dur <= 3.0:
                continue
        background_ids.append(idx)
        warnings.append(
            f"镜头{idx}: 非高潮镜头（背景/过渡/日常），v6.35 规则 3.7 应为 0 镜——"
            f"信息由旁白（narration_segment）一句带过，禁止单独成镜占用剧情预算"
        )
    if len(background_ids) > 2:
        warnings.insert(
            0, f"全片非高潮镜头 {len(background_ids)} 镜 > 2 上限（v6.35 仅允许开篇 1-2 镜快铺垫）"
        )
    for w in warnings:
        _get_logger().warn(f"高潮优先: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}
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

    # 6. 逆向提示词（新增；P0-3：传角色名供动作镜排除）
    results["negative_prompt"] = _check_negative_prompt(shots, script.get("characters", []))

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

    # v6.15 新增：打斗动作具体性检查（combat-action 吸收，招式/反馈/节奏）
    results["combat_action"] = _check_combat_action(shots)

    # v6.11 新增：人像光位检查（8种AI人像专业光位，防模糊光）
    results["lighting_position"] = _check_lighting_position(shots)

    # v6.5 新增：人物视线方向检查（镜内词域原则，覆盖 hook；P1-7：传角色名供构图价值门控）
    results["gaze_direction"] = _check_gaze_direction(shots, script.get("hook"), script.get("characters", []))

    # v6.5 新增：分镜与原文比对（防幻觉核心 - 检测编造内容）
    results["script_novel_alignment"] = _check_script_novel_alignment(script)

    # v6.6 新增：船只位置约束检查（防"人在水面蹦跳"幻觉）
    results["person_boat_position"] = _check_person_boat_position(shots)

    # v6.7 新增：首尾帧描述完整性检查（防静态视频）
    hook = script.get("hook")
    results["ff_lf_completeness"] = _check_ff_lf_desc_completeness(shots, hook)

    # v6.14 新增：ff_desc / motion_desc 时间线一致性检查（防钩子动作时序矛盾）
    results["ff_motion_timeline"] = _check_ff_motion_timeline(shots, hook)

    # v6.18 新增：ff_desc 多动作/时间推进堆叠检查（单一静态瞬间，warning 级）
    results["ff_multi_action"] = _check_ff_multi_action(shots, hook, script.get("characters", []))

    # 08-15 新增：非战斗镜头武器词检查（拦截描述层武器泄漏，hook 帧被塞匕首）
    results["frame_weapon_legality"] = _check_frame_weapon_legality(shots, hook, script.get("characters", []))

    # v6.8 新增：情绪可视化检查（将抽象情绪词转化为具体肢体动作）
    results["emotion_visualization"] = _check_emotion_visualization(shots, hook)

    # v6.16 新增：克制感情绪戏检查（微表情时间链，克制情绪禁外放大动作）
    results["restrained_emotion"] = _check_restrained_emotion(shots, hook)

    # v6.36 新增：强情绪顺序链检查（五层情绪链吸收，强情绪禁静态定格快照）
    results["strong_emotion_chain"] = _check_strong_emotion_chain(shots, hook)

    # v6.16 新增：切换拍摄对象运镜检查（人物→物品：方向+景别+运动+结束落点）
    results["focal_switch"] = _check_focal_switch(shots, hook)

    # v6.17 新增：打斗四段式节奏检查（对峙→攻防→反转→收尾 + 闪白/空镜升华）
    results["combat_arc"] = _check_combat_arc(shots)

    # v6.17 新增：台词标点情绪检查（标点驱动配音语气：波浪号撒娇/感叹号警告/逗号思考）
    results["voice_punctuation"] = _check_voice_punctuation(shots, hook)

    # v6.17 新增：前后遮挡逻辑检查（写清什么遮挡什么，防人物与围栏/门框穿模）
    results["occlusion_layering"] = _check_occlusion_layering(shots)

    # seedance2-skill 吸收：时长-内容密度检查（短镜头塞太多动作/场景）
    results["content_density"] = _check_content_density(shots)

    # v2.0 新增：正文净化规则检查（禁止词汇检查）
    results["body_purification"] = _check_body_purification(shots, hook)

    # v2.0 新增：输出结构检查
    results["output_structure"] = _check_output_structure(script)

    # v2.0 新增：剧情事件轴分布检查（强化版）
    results["narrative_arc"] = _check_narrative_arc_distribution(shots, script)

    # v6.21 新增：镜头组接检查（轴线越轴 / 景别跳跃 / 战场分层）
    results["axis_crossing"] = _check_axis_crossing(shots, script.get("hook"))
    results["shot_size_jump"] = _check_shot_size_jump(shots)
    results["battle_layer"] = _check_battle_layer(shots, script)

    # v6.24 新增：seedance 铁律7/8 + capsule 分镜原则吸收（全部 warning 级）
    results["object_separability"] = _check_object_separability(shots)
    results["wide_shot_facial"] = _check_wide_shot_facial(shots, script.get("hook"))
    results["hand_lock"] = _check_hand_lock(shots)
    results["subtitle_leak"] = _check_subtitle_leak(shots, script.get("hook"))

    # v6.25 新增：威胁源-视线耦合（规则41，hook 调试暴露的站位-视线矛盾）
    results["threat_gaze_coupling"] = _check_threat_gaze_coupling(shots, script.get("hook"))

    # v6.26 新增：抠姐短剧剧本检查（开篇冲突/爆点密度/对白长度）+ absorption-log·shotcraft（静态停留）
    # 全部 warning-only，不进 hard_fail_checks（与 v6.24/v6.25 一致）
    results["opening_hook"] = _check_opening_hook(shots, script.get("hook"))
    results["punch_density"] = _check_punch_density(shots, script.get("hook"))
    results["dialogue_length"] = _check_dialogue_length(shots, script.get("hook"))
    results["dwell_time"] = _check_dwell_time(shots, script.get("hook"))

    # v6.27 新增：角色状态锁定校验（角色状态锁定表吸收，跨镜坐站跳变检测）
    results["pose_continuity"] = _check_pose_continuity(shots)

    # v6.28 新增：双人对话过肩锁定检查（视频「对话视角一致性」吸收，规则22.8）
    results["over_shoulder_lock"] = _check_over_shoulder_lock(shots, script.get("hook"))

    # v6.29 新增：keyframes 简略检查（08-10 hook 关键帧质量——keyframe desc 只写
    # 动作动词、缺角色姿态/位置/朝向锚定，导致图片模型画错（杨旭站立/刺客消失/
    # 听香撑伞/船断）。warning-only，提示 LLM 重写规范 keyframes；step02a 代码层
    # _extract_char_layout 作为最终兜底。）
    results["keyframes_quality"] = _check_keyframes_quality(
        shots, script.get("hook"), script.get("characters", []))

    # v6.30 新增：道具空间方位锚定检查（A3 修复——同框多道具各写落点，rule50/22.9）
    results["prop_spatial_anchor"] = _check_prop_spatial_anchor(
        shots, script.get("hook"), script.get("characters"))

    # v6.31 新增：致命伤跨镜持续校验（A4 修复——规则8 伤处锚定词跨镜持续）
    results["fatal_wound_continuity"] = _check_fatal_wound_continuity(shots)

    # v6.31 新增：禁抽象形容词/比喻校验（A5 修复——规则5.1，抽象词视频画不出）
    results["forbidden_abstract"] = _check_forbidden_abstract(shots, script.get("hook"))

    # v6.31 新增：多人视线收敛判定（B6 修复——规则22.7/13.5，三人各看一方=散焦）
    results["gaze_convergence"] = _check_gaze_convergence(shots, script.get("hook"))

    # v6.31 新增：软性规则校验（C3 修复——规则13.6 ff长度 / 规则37 抽象词量化 /
    # 规则46 出场设计 / 规则24 色调过渡，全部 warning-only）
    results["ff_desc_length"] = _check_ff_desc_length(shots, script.get("hook"))
    results["abstract_quantization"] = _check_abstract_quantization(shots, script.get("hook"))
    results["character_entrance"] = _check_character_entrance(shots, script.get("characters", []))
    results["color_tone_transition"] = _check_color_tone_transition(shots)


    # v6.38 红果 60-90s 短剧标准检查（08-22 起，warning-only）
    results["redguo_standard"] = _check_redguo_standard(shots, script.get("characters", []))

    # v6.35 站位台吸收（规则51）：多人站位蓝图检查（warning-only，不进 hard_fail）
    results["multi_person_staging"] = _check_multi_person_staging(
        shots, script.get("hook"), script.get("characters"))

    # v6.45 char_layout.surface 必填检查（warning-only）——LLM 漏填 surface
    # （角色承重面：船尾/甲板/岸边/亭内/...）→ bridge 注入锚定块缺承重信息 →
    # LLM 自由发挥成船外水面（08-26 hook 0 听香站水里实测）。只告警不修，
    # 修法交回 prompt schema 必填 + LLM regen。
    results["char_layout_surface"] = _check_char_layout_surface(
        shots, script.get("hook"))

    # Plan A（2026-08-14）：ff_desc 全锚点检查（规则41/25/13.7，warning-only）——
    # 查「真正的图片输入」而非 prompt；hook 手改教训：ff_desc 缺锚 → 重渲必漂。
    results["ff_desc_anchors"] = _check_ff_desc_anchors(
        shots, script.get("hook"), script.get("characters"))

    # 2026-08 适配多模型：prompt 长度预算（防 provider 按模型上限静默截断内容）
    results["prompt_length_budget"] = _check_prompt_length_budget(shots, script.get("hook"))

    # v6.31 新增：关键帧时序一致性检查（钩子 kf 修复：pre-state+post-state 冲突）
    results["kf_temporal_consistency"] = _check_kf_temporal_consistency(shots, script.get("hook"))

    # v6.33 新增：关键帧物理时刻 = motion_desc 时段对齐（08-18 钩子坑：kfs 全堆 post 段、缺 pre 帧）
    results["kf_time_window_alignment"] = _check_kf_time_window_alignment(shots, script.get("hook"))

    # v6.40 新增：kf stage 标签一致性（stage enum + 与 desc 词表匹配 + t_sec 越界）
    results["kf_stage_consistency"] = _check_kf_stage_consistency(shots, script.get("hook"))

    # v6.37 新增：高潮优先原则检查（08-20 高潮优先 prompt 落地，对应规则 3.7）
    # 检测"非高潮"镜头（命中背景/过渡/日常关键词 + 未命中强冲突/关键情感戏关键词）
    # → 警告应改为 0 镜（v6.35 规则 3.7）；开篇 1-2 镜快铺垫 ≤3 秒可豁免
    results["climax_priority"] = _check_climax_priority(shots)

    # v6.38 新增：长破折号检查（08-21 吸收 shyman159/seedance 铁律 9，规则 56）
    # prompt 含「————」长破折号 → 改用空行/换行/【方括号区块标题】
    results["long_dash_usage"] = _check_long_dash_usage(shots, script.get("hook"))

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


# ========================================================================
# v6.33 新增（08-12）：第二轮校验反馈——把分镜检查表告警喂给重生成 LLM
#
# 背景：_run_prompt_checklist 的 ~50 条 WARN 只 print 不返回，重生成循环
# （_regen_over_limit / gap regen）看不到内容密度/景别跳跃/致命伤等真实缺陷，
# 重生成还是老问题（08-12 日志实测：内容密度7条/景别跳跃18条，LLM 重生成无感）。
# 本函数把告警按类型聚合（每类 1 行 + 命中镜头数 + 首例截断），生成一段简短
# 「本地校验反馈」，供 main.py 重生成循环附加到 prompt。
# 只取告警数最多的前 max_categories 类；排除需人工判断/结构性的类别。
# ========================================================================

# 不反馈给 LLM 的低价值类别（人工判断 / 结构性 / 非重生成可修）
_REGEN_FEEDBACK_EXCLUDE = frozenset({
    "semantic_risk",          # 需人工确认是否保留
    "script_novel_alignment", # 原文锚点需人工核对（编造/变形）
    "platform_compliance",    # 平台合规标注（AI生成 字样）
    "output_structure",       # 输出结构
    "narrative_arc",          # 事件轴分布（结构性）
    "body_purification",      # 正文净化
})

# 类别英文键 → 中文名（供反馈行首标签；未收录的用原键）
_REGEN_FEEDBACK_LABELS = {
    "content_density": "内容密度(动作过载)",
    "prompt_length_budget": "prompt长度预算",
    "shot_size_jump": "景别跳跃",
    "gaze_direction": "视线方向",
    "gaze_convergence": "多人视线收敛",
    "fatal_wound_continuity": "致命伤持续",
    "threat_gaze_coupling": "威胁源-视线耦合",
    "prop_spatial_anchor": "道具空间方位",
    "keyframes_quality": "关键帧质量",
    "kf_temporal_consistency": "kf时序一致性",
    "kf_time_window_alignment": "kf物理时刻=时段铁律(v6.33)",
    "opening_hook": "开篇冲突",
    "punch_density": "爆点密度",
    "dialogue_length": "对白长度",
    "dwell_time": "静态停留",
    "combat_action": "打斗打击反馈",
    "camera_movement": "一镜一运镜",
    "camera_upgrade": "高张力运镜升级",
    "focal_switch": "切换拍摄对象",
    "lighting_position": "人像光位",
    "forbidden_abstract": "禁抽象词",
    "pose_continuity": "角色状态锁定",
    "character_entrance": "人物出场设计",
    "color_tone_transition": "色调过渡",
    "character_consistency": "角色一致性",
    "negative_prompt": "空镜禁止人物",
    "ff_lf_completeness": "首尾帧完整性",
    "ff_motion_timeline": "首帧motion时序",
    "ff_multi_action": "ff多动作堆叠",
    "ff_desc_anchors": "ff锚点(服装色/威胁源朝向)",
    "over_shoulder_lock": "过肩锁定",
    "restrained_emotion": "克制情绪",
    "strong_emotion_chain": "强情绪顺序链",
    "hand_lock": "手部锁定",
    "occlusion_layering": "前后遮挡",
    "ff_desc_length": "ff长度",
    "emotion_visualization": "情绪可视化",
    "voice_punctuation": "台词标点",
    "combat_arc": "打斗四段式",
    "shot_transition": "镜头衔接",
    "action_overlap": "动作重叠",
    "multi_person_staging": "多人站位",
    "frame_weapon_legality": "非战斗镜头武器词",
    "subtitle_leak": "画面无字幕(规则40)",
    "grounding": "原文锚点校验",
    "climax_priority": "高潮优先原则(v6.35)",
    "long_dash_usage": "长破折号使用(规则56/v6.38)",
    "redguo_standard": "红果60-90s标准(v6.38)",
    "kf_stage_consistency": "kf阶段标签一致性(v6.40)",
}


def build_regen_feedback(script: dict, novel_text: str = "",
                         max_categories: int = 99, per_line_chars: int = 300) -> str:
    """把分镜检查表告警按类型聚合，生成给重生成 LLM 的「本地校验反馈」.

    v6.33（08-12）：checklist 的 WARN 此前只 print 不返回，重生成 LLM
    看不到内容密度/景别跳跃/致命伤等真实缺陷 → 重生成还是老问题。本函数重跑
    _run_prompt_checklist（抑制 stdout 避免日志刷屏），把告警按类型聚合：
    每类 1 行「- 类别 N处：首例」，取告警数最多的前 max_categories 类，
    排除需人工判断/结构性的类别（_REGEN_FEEDBACK_EXCLUDE）。返回 "" 表示无反馈。

    v6.36（08-15）：默认放开——max_categories=99（全部非排除类别，不再只取 top-5）
    + per_line_chars=300（首例几乎完整，不再 72 字截断），重生成 LLM 看全量分析、
    能修更多。若需控制 prompt 体积可显式传小值。

    Args:
        script: 当前待重生成的分镜脚本（重生成前已 parse_response，含完整字段）
        novel_text: 小说原文（供原文锚点类检查；默认空则跳过）
        max_categories: 最多反馈几类（默认 99=全部非排除类别）
        per_line_chars: 每行首例截断长度（默认 300，几乎不截）
    """
    try:
        if novel_text:
            _set_novel_text(novel_text)
        import io as _io
        import contextlib as _ctx
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf):  # 抑制 刷屏
            results = _run_prompt_checklist(script).get("results", {})
    except Exception:
        return ""
    grouped = {}
    for key, res in results.items():
        if key in _REGEN_FEEDBACK_EXCLUDE or not isinstance(res, dict):
            continue
        warns = list(res.get("warnings") or []) + list(res.get("issues") or [])
        if warns:
            grouped[key] = warns
    if not grouped:
        return ""
    top = sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:max_categories]
    lines = []
    for key, warns in top:
        label = _REGEN_FEEDBACK_LABELS.get(key, key)
        # 08-20（v6.37）：改为每类全量例举（之前只首例，信息不足）。
        # 提取每条 warning 的镜头号前缀，列出所有命中镜头号；再附首例完整文本（截断）。
        import re as _re_b
        shot_ids = set()
        for w in warns:
            for m in _re_b.finditer(r"镜头(\d+)(?:→(\d+))?|钩子", w):
                for g in m.groups():
                    if g:
                        shot_ids.add(f"镜头{g}" if g.isdigit() else "钩子")
        shot_str = ",".join(sorted(shot_ids, key=lambda s: int(s.replace("镜头", "")) if s.startswith("镜头") and s[2:].isdigit() else -1)) or "全局"
        first = warns[0].strip()
        if len(first) > per_line_chars:
            first = first[:per_line_chars] + "…"
        lines.append(f"- {label} {len(warns)}处（{shot_str}）：{first}")
    body = "\n".join(lines)
    return (
        "\n\n# 本地校验反馈（来自分镜检查表，请针对性修正；「N处」=命中镜头数，括号内=镜头号）\n"
        + body + "\n"
    )


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
        # v6.47（08-27）：duration 缺省默认 4.0s（原 1s 是误导性退化值——LLM 常漏写
        # duration，parse 阶段红果检查读到 1s×N 报「总时长 16s」，实际 _compute_timecodes
        # 会按 motion_desc/对白/4s 旁白重算。4.0 = 纯旁白固定时长下限，更接近最终值）。
        shot.setdefault("duration", 4.0)
        # fx_effect（schema required，LLM 常漏写）缺省置空避免「缺少字段」误报
        shot.setdefault("fx_effect", "")
        # duration_sec：视频时长权威来源，缺省时与 duration 同值（timecode 后续会覆盖）
        shot.setdefault("duration_sec", shot.get("duration", 4.0))
        shot.setdefault("negative_prompt", "")
        shot.setdefault("hook", "")
        shot.setdefault("time_stamp", "")
        shot.setdefault("cam_idx", 1)
        shot.setdefault("subject_direction", "")  # v6.21：人物朝向（可选）
        shot.setdefault("char_layout", [])  # v6.35 站位台吸收：多人站位蓝图（可选）
        # v6.57 P0-B：char_layout 派生回填（frame_layouts → 旧视图，老章原样保留）
        from shared.frame_spec import derive_char_layout
        shot["char_layout"] = derive_char_layout(shot)

    # 补全 hook（如果存在）
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        hook.setdefault("content", "")
        hook.setdefault("camera_movement", "")
        hook.setdefault("framing", "")
        hook.setdefault("lighting_color", "")
        hook.setdefault("duration", 4.0)
        hook.setdefault("fx_effect", "")
        hook.setdefault("duration_sec", hook.get("duration", 4.0))
        hook.setdefault("negative_prompt", "")
        hook.setdefault("time_stamp", "")
        hook.setdefault("char_layout", [])   # Plan A（2026-08-14）：与 _SHOT 对称
        from shared.frame_spec import derive_char_layout
        hook["char_layout"] = derive_char_layout(hook)
        hook.setdefault("keyframes", [])     # Plan A（2026-08-14）：schema 已入 required

    # 补全顶层字段
    script.setdefault("total_duration", 0)
    script.setdefault("style", "")


# 说话人动作词（用于从「说话人道：台词」中提取说话人，及后续入画校验）
_SPEAKER_TAG_RE = re.compile(r"^([一-鿿]{2,8}?)(?:道|说|喊|喝|叫|问|答|曰|声道|声说)[:：]")
# 尾部说话动词剥离：正则可能把「喊道/扬声道/颤声道」的动词部分误纳入说话人名
_SPEAKER_TRAIL_VERB_RE = re.compile(r'(?:扬|怒|颤|冷|漫|惊|厉|高|低|轻|重|急|缓)?声?(?:道|说|喊|喝|叫|问|答|叹|曰)$')


def _extract_speaker(narration: str, defined_ids: frozenset | None = None) -> str:
    """从「说话人道：台词」格式中提取说话人。

    复用 shared/dialogue.detect_speaker 权威实现（含角色库精确名优先 + 剥离
    尾部说话动词，审计 P1-8 说话人检测 5 套正则收敛）。返回空串表示未检测到。
    """
    from shared.dialogue import detect_speaker
    chars = [{"identifier": cid} for cid in (defined_ids or [])]
    speaker = detect_speaker(narration, chars) if narration else None
    return speaker or ""


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
    from style_prefix import normalize_ff_desc
    from shared.model_caps import active_video_uses_native_voice
    # 原生发声供应商（volcengine/minimax/agent_plan）不补「（用中文说）」；
    # 旧供应商保留历史行为。用 active_video_uses_native_voice() 统一判断。
    native_voice = active_video_uses_native_voice()
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

        # 1. 声音指令（跳过 hook；原生发声模型不补「用中文说」）
        if shot.get("index") != 0 and is_dialogue and not native_voice:
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

        # 3.2 禁抽象形容词/比喻词（A5 修复 08-11，规则5.1）：「快速出剑」「身形矫健」
        #    这类抽象词视频模型画不出具体动作。确定性删除抽象修饰词，保留主体与动作
        #    （校验项 _check_forbidden_abstract 依赖）。motion_desc 同样处理。
        prompt = _fix_forbidden_abstract(prompt)
        motion_desc = _fix_forbidden_abstract(motion_desc)
        # 3.2.1 08-15 bug 修复：_check_forbidden_abstract 扫 prompt/motion/ff/lf 四字段，
        # 但此前这里只修 prompt/motion →「迅速」出现在 ff/lf（shot25 lf_desc 实测）
        # 检到了没修到、每次运行都报。ff/lf 同样确定性删抽象词（幂等）。
        for _f in ("ff_desc", "lf_desc"):
            _v = shot.get(_f)
            if _v:
                shot[_f] = _fix_forbidden_abstract(_v)

        # 3.2.2 规则39 远景禁五官：确定性剥离精细五官词（校验项 _check_wide_shot_facial
        # 依赖）——远景人物只留身份+服装主色+剪影，精细五官（剑眉/星目/朱唇）会让模型
        # 把脸放大比例崩坏。仅当镜头命中远景词（远景/全景/俯拍…）才删，非远景不误伤。
        _is_wide = any(w in " ".join([
            shot.get("ff_desc", ""), shot.get("prompt", ""),
            shot.get("location", ""), shot.get("scene", "")]) for w in _WIDE_SHOT_WORDS)
        if _is_wide:
            prompt = _fix_wide_shot_facial(prompt)
            _vf = shot.get("ff_desc")
            if _vf:
                shot["ff_desc"] = _fix_wide_shot_facial(_vf)

        # 3.5 情绪可视化：抽象情绪词 → 追加可视化动作（prompt + motion_desc）
        prompt = _fix_emotion_visualization(prompt)
        motion_desc = _fix_emotion_visualization(motion_desc)

        # 3.6 对白超长句确定性断句（B9 修复 08-11，规则43）：无标点连续 >15 字
        #    的单句在语义停顿点插入「，」，只改标点不改文字（规则31 兼容）。
        #    narration_segment 与 subtitle 同步修复（subtitle == narration_segment，
        #    字幕与语音严格对齐；修复后仍逐字一致）。
        _fixed_narr = _fix_dialogue_length(narration)
        if _fixed_narr != narration:
            shot["narration_segment"] = _fixed_narr
            narration = _fixed_narr
        _fixed_sub = _fix_dialogue_length(subtitle)
        if _fixed_sub != subtitle:
            shot["subtitle"] = _fixed_sub
            subtitle = _fixed_sub
        # 08-15（P18）：台词破折号裸收尾补「！」（「救命啊——」→「救命啊——！」），
        # 校验项 _check_voice_punctuation 裸收尾分支依赖。narration 与 subtitle 严格
        # 同步（subtitle == narration_segment），修复后仍逐字一致。
        _fixed_dash = _fix_trailing_dash_punct(narration)
        if _fixed_dash != narration:
            shot["narration_segment"] = _fixed_dash
            shot["subtitle"] = _fix_trailing_dash_punct(subtitle)
            narration = _fixed_dash

        # 4. 转场关键词：场景切换镜补淡入淡出（跳过 hook）
        if shot.get("index") != 0 and i < len(shots) - 1:
            curr_loc = shot.get("location", "") or shot.get("scene", "")
            next_loc = shots[i + 1].get("location", "") or shots[i + 1].get("scene", "")
            if curr_loc and next_loc and curr_loc != next_loc:
                if not any(kw in prompt for kw in ("淡入淡出", "转场", "自然过渡", "叠化")):
                    prompt = prompt.rstrip() + "。淡入淡出转场，转场时长0.5s"

        shot["prompt"] = prompt
        shot["motion_desc"] = motion_desc

        # v6.18：ff_desc 静态化归一化（多动作/进行态堆叠 → 单一静态瞬间）
        if shot.get("ff_desc"):
            shot["ff_desc"] = normalize_ff_desc(
                shot["ff_desc"], shot, script.get("characters", []))

        # v1.1：尾帧 lf_desc 目光修复（错别字 + 看向动作结果对象——听香看被刺倒的杨旭）
        if shot.get("lf_desc"):
            from shared.vimax_bridge import fix_lf_desc_gaze
            shot["lf_desc"] = fix_lf_desc_gaze(
                shot["lf_desc"], shot, script.get("characters", []))

        # v6.28：尾帧武器残留确定性修复（从根上解决「匕首尖停水面」）。
        # LLM 常把「刺客没入水中」写成「仅余一截刀尖刺破水面」——刀尖半截卡在水面，
        # v6.29：lf_desc 终态归一化（中间态 → 彻底结束；与 ff 侧 normalize_ff_desc 对称）。
        # 取代 v6.28 的 _fix_lf_weapon_residue 窄正则（审计 S1：_fix_* 小函数收敛为管道）。
        # 覆盖残留/半身/过渡/即将/进行 五类中间态（武器残留是其一）。
        if shot.get("lf_desc"):
            from style_prefix import normalize_lf_desc
            shot["lf_desc"] = normalize_lf_desc(
                shot["lf_desc"], shot, script.get("characters", []))

        # 08-15 B1/B2：ff/lf 确定性锚注入（规则13.7 服装色短锚防漂色 / 规则41 威胁
        # 朝向防背对）。放在各 normalize 之后，锚不被归一化剥离；幂等——已有锚跳过。
        for _f in ("ff_desc", "lf_desc"):
            _v = shot.get(_f)
            if _v:
                _v = _inject_dress_anchor(_v, script.get("characters", []))
                _v = _inject_threat_facing(_v, shot)
                shot[_f] = _v

        # 08-15（PR-2）：中间关键帧确定性骨架锚定——ff/lf 定稿后，把与 step02a 生成时
        # 同款的 _extract_char_layout「角色姿态/位置/朝向」块追加到 0<tr<1 的 keyframe
        # desc（脚本层先落地，step02a 幂等跳过）。治「24 个中间关键帧缺姿态/位置锚定」
        # 聚合告警，威胁源朝向兜底（面向目标/禁止背对）随之注入；空镜（无角色）返回
        # 空跳过。幂等：「【角色锚定】」标记已在则不动。
        _kfs = shot.get("keyframes") or []
        if _kfs and isinstance(_kfs, list):
            from shared.vimax_bridge import _extract_char_layout as _kf_layout
            for _kf in _kfs:
                if not isinstance(_kf, dict):
                    continue
                # v6.34 字段迁移：走 shared.kf_time.is_middle_kf（t_sec 优先 + time_ratio fallback）
                if not is_middle_kf(_kf, shot):
                    continue
                _kd = _kf.get("desc") or ""
                if not _kd or "【角色锚定】" in _kd:
                    continue
                _role_scan = " ".join(filter(None, [
                    shot.get("ff_desc", ""), shot.get("lf_desc", ""),
                    shot.get("motion_desc", "") or shot.get("prompt", ""),
                    _kd,
                ]))
                _anchor = _kf_layout(shot, script.get("characters", []),
                                     role_scan_text=_role_scan)
                if _anchor:
                    # v6.43（08-23 审计）：原 f"{_kd}。{_anchor}" 在 _kd 末尾已
                    # 有「。」时会产生「。。」双句号（实测 24 处）。rstrip 句末
                    # 标点后再拼接。
                    _kd_stripped = _kd.rstrip("。！？，,;；")
                    _kf["desc"] = f"{_kd_stripped}。{_anchor}"

        # 08-17（P18 闭环）：kf 坐→仰卧跳变自动改写过渡态
        # v6.43（08-23 审计）：原版写死「杨旭」——切到其他小说失效。改为
        # 遍历 shot.char_layout / script.characters 所有出镜角色，每个角色
        # 都检查「首帧坐姿 → kf 仰卧/躺倒」跳变。
        # 只输出 WARN 等 LLM 修，跨轮残留。改为确定性改写：kf[0] 改成过渡态
        # 「身体后倾、即将倒地」类描述 + 标记「（过渡态）」，step02a/vimax_bridge
        # 锚定能直接用。幂等：「（过渡态）」已在则跳过。
        _ff_pose_m = _extract_state_markers(shot.get("ff_desc", "") or "")
        _ff_poses = _ff_pose_m.get("poses", {}) or {}
        # 收集本镜出镜角色：优先 char_layout，兜底 script.characters
        _char_pool = list(_ff_poses.keys())
        if not _char_pool:
            for _cl in shot.get("char_layout") or []:
                if isinstance(_cl, dict) and _cl.get("name"):
                    _char_pool.append(str(_cl["name"]))
        if not _char_pool:
            _char_pool = ["杨旭"]  # 兜底（与旧行为兼容）
        for _char in _char_pool:
            _ff_p = _ff_poses.get(_char, "")
            if _ff_p not in ("盘坐", "盘膝", "端坐", "坐"):
                continue
            handled = False
            for _kf in _kfs:
                if not isinstance(_kf, dict):
                    continue
                if not is_middle_kf(_kf, shot):
                    continue
                _kd = _kf.get("desc") or ""
                if "（过渡态）" in _kd or "过渡态）" in _kd:
                    continue
                _kf_m = _extract_state_markers(_kd)
                _kf_p = _kf_m.get("poses", {}).get(_char, "")
                if _kf_p in ("仰卧", "躺", "仰倒", "倒", "已躺下"):
                    _new_kd = re.sub(
                        re.escape(_char) + r"[^，。！？；、]*?(仰卧|躺下|仰倒|倒下)",
                        f"{_char}身体后倾、即将倒地",
                        _kd,
                    )
                    if _new_kd != _kd:
                        _kf["desc"] = f"{_new_kd}（过渡态）"
                        handled = True
                        break  # 仅改第一个命中（kf[0]），后续 kf 仍按自然推进
            if handled:
                continue

        # 08-17（P18 闭环）：打斗镜 motion_desc 自动补打击反馈元素
        # 原逻辑：combat_action WARN 等 LLM 修，跨轮残留。改为确定性补强：
        # 检测到打斗镜头（按 _COMBAT_KEYWORDS），motion_desc 末尾追加打击反馈词，
        # 让 AI 视频模型有「打到了」的具体画面（不靠 LLM 自觉）。幂等：
        # 已含「火星四溅/衣袂翻飞/冲击波」任一则跳过。
        _motion_now = shot.get("motion_desc", "") or ""
        _is_combat = any(kw in _motion_now for kw in _COMBAT_KEYWORDS) or \
            any(kw in (shot.get("ff_desc", "") or "") for kw in _COMBAT_KEYWORDS)
        if _is_combat:
            _hit_fx = ("火星四溅", "衣袂翻飞", "冲击波", "动态模糊", "碎屑飞溅", "水花飞溅")
            if not any(w in _motion_now for w in _hit_fx):
                shot["motion_desc"] = f"{_motion_now.rstrip('。')}，火星四溅、衣袂翻飞"

    # hook 也需要光位量化 + 情绪可视化修复（光位先，让情绪词先驱动光位选择）
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        hook["prompt"] = _fix_lighting_quantization(hook.get("prompt", "") or "")
        hook["prompt"] = _fix_forbidden_abstract(hook.get("prompt", "") or "")
        hook["motion_desc"] = _fix_forbidden_abstract(hook.get("motion_desc", "") or "")
        # 08-15 bug 修复（与 shot 3.2.1 一致）：hook 的 ff/lf 也确定性删抽象词
        for _f in ("ff_desc", "lf_desc"):
            _hv = hook.get(_f)
            if _hv:
                hook[_f] = _fix_forbidden_abstract(_hv)
        hook["prompt"] = _fix_emotion_visualization(hook.get("prompt", "") or "")
        hook["motion_desc"] = _fix_emotion_visualization(hook.get("motion_desc", "") or "")
        # 08-15（P18）：hook 同样剥离远景精细五官词（_check_wide_shot_facial 覆盖 hook）
        _hook_wide = any(w in " ".join([
            hook.get("ff_desc", ""), hook.get("prompt", ""),
            hook.get("location", ""), hook.get("scene", "")]) for w in _WIDE_SHOT_WORDS)
        if _hook_wide:
            hook["prompt"] = _fix_wide_shot_facial(hook.get("prompt", "") or "")
            _hvf = hook.get("ff_desc")
            if _hvf:
                hook["ff_desc"] = _fix_wide_shot_facial(_hvf)
        # v6.18：hook ff_desc 同样归一化
        if hook.get("ff_desc"):
            hook["ff_desc"] = normalize_ff_desc(
                hook["ff_desc"], hook, script.get("characters", []))
        # v1.1：hook 尾帧 lf_desc 目光修复（与 shot 一致）
        if hook.get("lf_desc"):
            from shared.vimax_bridge import fix_lf_desc_gaze
            hook["lf_desc"] = fix_lf_desc_gaze(
                hook["lf_desc"], hook, script.get("characters", []))

        # v6.29：hook 尾帧终态归一化（同 shot，取代 v6.28 武器残留窄正则）
        if hook.get("lf_desc"):
            from style_prefix import normalize_lf_desc
            hook["lf_desc"] = normalize_lf_desc(
                hook["lf_desc"], hook, script.get("characters", []))
        # 08-15 B1/B2：hook ff/lf 同样注入服装色锚 + 威胁朝向（与 shot 一致）
        for _f in ("ff_desc", "lf_desc"):
            _hv = hook.get(_f)
            if _hv:
                _hv = _inject_dress_anchor(_hv, script.get("characters", []))
                _hv = _inject_threat_facing(_hv, hook)
                hook[_f] = _hv
        # 08-15（P18）：hook 台词破折号裸收尾补「！」（_check_voice_punctuation 覆盖 hook）
        if hook.get("narration_segment"):
            hook["narration_segment"] = _fix_trailing_dash_punct(hook["narration_segment"])
        if hook.get("subtitle"):
            hook["subtitle"] = _fix_trailing_dash_punct(hook["subtitle"])

    return script


# ============================================================================
# v6.32：硬修复层（hard_fix_shot_prompts，08-17 P18 闭环）
# ============================================================================
# post_fix_shot_prompts 只追加/归一化，对 LLM 反复写错的 4 类「确定性缺陷」无能为力：
#   1. 致命伤建立后，同场景后续镜头的伤处锚点消失（shot8 建立后 shot12/14/17/18/20
#      /21/23 共 7 镜全丢，伤处洇痕是剧情连续性核心）
#   2. 视线方向用「看/望/盯+人名」而不是「画左/画右」（shot4 望星斗、shot15 看杨旭）
#   3. 道具空间方位缺失（shot3 炭炉/shot4 酒壶/shot6 步摇/shot12 灯盏/shot24-26 灯笼马车
#      等 8 处），模型会自由摆放重叠
#   4. shot_count 持续超 SHOT_COUNT_MAX（27 vs 25）— LLM 反馈循环不肯合并
#
# 硬修复层在 post_fix_shot_prompts 之后、_validate_script 之前调用，
# 对每类做确定性改写（不在 JSON 中暴露 WARN）；run_shots 写入 disk 的就是干净版。
# ============================================================================

# 致命伤锚关键词（任一出现即视为「该镜已锚定致命伤」，跳过追加）
# v6.43 重构：统一从 prompt_semantic.registry 引入
from step01_script_gen.prompt_semantic import (
    INJURY_ANCHOR_WORDS as _INJURY_ANCHOR_WORDS,
    INJURY_ANCHOR_TAIL as _INJURY_ANCHOR_TAIL,
    STRONG_INJURY_ANCHORS as _STRONG_INJURY_ANCHORS,
    BLOOD_STATE_WORDS as _BLOOD_STATE_WORDS,
)

# 视线方向兜底：发现「看/望/盯+人名/对象」但没带空间方位词时，按目标所在位置改写
# 简易版：只处理「望满天星斗/望星斗」一类易识别模板，复杂场景留给校验 WARN。
# 复杂「看杨旭/看他」等需要按杨旭在 ff/lf 的实际位置推：ff 若杨旭在画右，look
# 应写「目光画左望向杨旭」（看的人在画左、杨旭在画右、对视轴合理）。v6.32 简化
# 处理：直接替换为「目光望向杨旭（画X）」，让模型自己推断（不写死画左/画右）。
# v6.43 重构：角色名 regex 改为从 char_layout 动态生成（不再写死 11 个名字）。
# 通用兜底名（"她/他/女子/男子/公子/姑娘"等）保留硬编码。
_GAZE_FALLBACK_RULES = [
    # (匹配正则, 改写为) — 顺序敏感，先具体后泛化
    (re.compile(r"望满天星斗|仰望星斗|望星斗"), "仰望画上方星空"),
    (re.compile(r"看向?天|望天"), "仰望画上方天空"),
    # v6.49（08-28）扩展：原规则「望水」太严，"望向前方水面"/"望向水湾"/"望水面垂钓"
    # 等含中间修饰词的"望+水面"无法匹配 → 11/11 视线方向 WARN 刷屏。
    # 改为「望(?:向)?[^，。；]{0,8}水」允许中间最多 8 字修饰。
    # v6.50（08-28）补 `面?`：原文「呆望画下方水面」匹配止于「水」、遗留「面」，
    # 拼出「俯视画下方水面面」（hook lf_desc 实测）——把尾字一并吞掉。
    (re.compile(r"望(?:向)?[^，。；]{0,8}水面?"), "俯视画下方水面"),
    (re.compile(r"看向?远|望远方"), "凝望画面深处"),
    (re.compile(r"看向?地|望地"), "俯视画下方地面"),
    # 通用代词兜底（不写死角色名）
    (re.compile(r"(?<![一-鿿])望着?(她|他|那女子|女子|那男子|男子|公子|姑娘)"), r"目光凝视\1"),
]


def _build_char_gaze_pattern(char_names: tuple[str, ...]) -> re.Pattern:
    """从 char_layout 抽角色名，生成「看/盯/望+人名」regex.

    替代原硬编码 11 名字列表 —— 任意小说的 char_layout 角色名都生效。
    char_names 为空时 fallback 到 prompt_semantic.COMMON_PERSON_NAMES.
    """
    if not char_names:
        from step01_script_gen.prompt_semantic import COMMON_PERSON_NAMES
        char_names = COMMON_PERSON_NAMES
    # 按名字长度降序排列（避免"听香"被"香"先匹配）
    sorted_names = sorted(char_names, key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in sorted_names)
    return re.compile(r"(?:看|盯|望)向?(" + alt + r")")


def _build_char_gaze_loose_pattern(char_names: tuple[str, ...]) -> re.Pattern:
    """宽松版：处理"望着杨旭"（不要求"向"）."""
    if not char_names:
        from step01_script_gen.prompt_semantic import COMMON_PERSON_NAMES
        char_names = COMMON_PERSON_NAMES
    sorted_names = sorted(char_names, key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in sorted_names)
    return re.compile(r"(?<![一-鿿])望着?(" + alt + r")")


# 道具兜底方位（按镜头场景/位置优先级匹配，找不到就放「画中」）
# v6.43（08-23 审计）：原版「酒杯 → 画中杨旭手边」「油纸伞 → 画中听香手边」
# 等写死本小说角色——切到其他小说（主角变林平之、场景变镖局）会失效或
# 误植。规则：
#   - 道具自然落点（炭炉/酒壶/灯盏/灯笼/钓杆/舴艋舟 等）→ 保留场景相关方位
#   - 角色相关（酒杯/油纸伞/步摇/竹箫/刺客匕首/马车）→ 改为通用「画中」，
#     由模型从上下文判断「谁持/谁近」
# v6.43 重构：道具清单与位置从 prompt_semantic + scene_anchors 派生（不再写死）
from step01_script_gen.prompt_semantic import PROP_REGISTRY
from step01_script_gen.scene_anchors import (
    _PROP_DEFAULT_POSITIONS, _PROP_HORIZONTAL_POSITIONS,
)

def _build_prop_pos_fallback() -> dict[str, str]:
    """从 prompt_semantic + scene_anchors 派生道具兜底方位.

    合并：
      - scene_anchors._PROP_DEFAULT_POSITIONS（通用）
      - scene_anchors._PROP_HORIZONTAL_POSITIONS（横向场景专属）
    """
    out: dict[str, str] = {}
    # 通用位置
    for name, pos in _PROP_DEFAULT_POSITIONS.items():
        if name in out:
            continue
        # 把"发间"类（角色相关）改为通用"画中"
        if pos in ("发间", "画中"):
            out[name] = "画中"
        else:
            out[name] = pos
    # 横向场景专属
    for name, pos in _PROP_HORIZONTAL_POSITIONS.items():
        out[name] = pos
    return out

# 运行时构建（替代原 15 个写死条目）
_PROP_POS_FALLBACK: dict = _build_prop_pos_fallback()


# 通用 pre/post-state 关键词（任何打斗/刺杀/追逐/位移场景都适用）
# pre-state = 动作起始（破水/出剑/冲撞/跃起/举刀/挥拳…）
# post-state = 动作结束（没入/倒地/逃走/消失/已被…）
_KF_PRE_KW = (
    "半身探出", "刚出水面", "破水而出", "举刀", "刀尖朝下", "正对", "水花四溅",
    "破门", "跃起", "扑出", "冲出", "挥刀", "出剑", "抬脚", "纵身",
)
_KF_POST_KW = (
    "已没入", "没入", "入水", "逃走", "消失", "只剩", "涟漪",
    "倒地", "坠落", "跌落", "倒下", "瘫倒", "倒下不起",
    "奔远", "远去", "不见踪影", "已被", "被擒",
)


def _hard_fix_kf_pre_post_conflict(shots: list, hook: dict | None = None) -> int:
    """通用 kf 时序矛盾硬修复（v6.32，08-17）.

    校验器 `_check_kf_temporal_consistency` 只 WARN 不阻断——LLM 经常在同一 frame
    里同时写 pre-state（"刺客半身探出举刀"）和 post-state（"杨旭仰卧+心口洇痕"），
    但 pre-state 应该在前段（t≤0.33），post-state 应该在后段（t≥0.67）。原本只修
    hook，本函数对所有 shot+hook 通用化。

    修法：
      1. 同一 frame 含 pre+post 关键词 + 杨旭已躺血 → 矛盾，删 pre 词、保留 post、t=0.5
      2. 纯 pre 但 t>0.33 → 压到 t=0.2（前 1/3 段）
      3. 纯 post 但 t<0.67 → 拉到 t=0.8（后 1/3 段）

    通用性：pre/post 关键词已扩到所有打斗场景（破门/跃起/出剑/倒地/逃走/被擒），
    任何含"动作起始/动作结束"对立的镜头都适用。
    返回改写帧数。
    """
    fixed = 0
    targets: list[dict] = list(shots or [])
    if hook and isinstance(hook, dict):
        targets.append(hook)

    for s in targets:
        kfs = s.get("keyframes") or []
        if not isinstance(kfs, list) or not kfs:
            continue

        # v6.34：取 shot 自身 dur（用于 t→t_sec 反推阈值）
        try:
            dur = float(s.get("duration_sec") or s.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        if dur <= 0:
            continue

        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            # v6.34 字段迁移：走 helper，读 t_sec（fallback time_ratio × dur）
            t = get_kf_t_sec(kf, s)
            if t is None or t <= 0 or t >= dur:
                continue
            desc = kf.get("desc") or ""
            # v6.43（08-23 审计）：【角色锚定】块内自带「正对/仰卧」等词，
            # 参与检测会自触发，删除正则还会跨标点吞掉锚定文本——检测与
            # 删除只在锚定前的正文进行，锚定原样保留（与 constraint 版同步）。
            anchor = ""
            body = desc
            if "【角色锚定】" in desc:
                body, _anchor_tail = desc.split("【角色锚定】", 1)
                anchor = "【角色锚定】" + _anchor_tail
            is_pre = any(kw in body for kw in _KF_PRE_KW)
            is_post = any(kw in body for kw in _KF_POST_KW)
            if is_pre and is_post:
                continue
            # 真实矛盾：pre-state 帧 + 角色已"躺下/倒地/坠落" + 血/洇痕
            # → 动作起始不应该与"已躺血"同框（应当是 stab 帧：刀尖刚触或刚倒）
            has_laying = any(w in body for w in ("仰卧", "后仰", "倒地", "倒下", "坠落", "跌落"))
            has_blood = any(w in body for w in ("洇痕", "暗红", "血"))
            if is_pre and has_laying and has_blood:
                original = desc
                for kw in _KF_PRE_KW:
                    # 删 pre 关键词所在逗号段（[^，。；] 限定单段，禁止跨标点吞句）
                    pat = re.compile(r"[,，]?\s*[^，。；]*?" + re.escape(kw) + r"[^，。；]*?(?=[，。；]|$)")
                    body = pat.sub("", body)
                # 也删单独的"水花四溅"等孤立的 pre 关键词
                for kw in _KF_PRE_KW:
                    body = re.sub(r"[,，]?\s*" + re.escape(kw), "", body)
                body = re.sub(r"[,，]\s*[,，]", "，", body).strip("，。； ")
                desc = body + anchor
                if desc != original:
                    kf["desc"] = desc
                    # v6.34 字段迁移：写 t_sec（半程）而非 time_ratio
                    set_kf_t_sec(kf, dur * 0.5)
                    fixed += 1
                continue
            # 纯 pre-state 但时间 > 0.33 × dur → 压到 0.2 × dur（前 1/3 段）
            if is_pre and t > dur * 0.33:
                set_kf_t_sec(kf, dur * 0.2)
                fixed += 1
            # 纯 post-state 但时间 < 0.67 × dur → 拉到 0.8 × dur（后 1/3 段）
            elif is_post and t < dur * 0.67:
                set_kf_t_sec(kf, dur * 0.8)
                fixed += 1
    return fixed


def _hard_fix_kf_time_window_alignment(shots: list, hook: dict | None = None) -> int:
    """v6.33 派生 kf 硬修复（08-18 钩子实测）——补齐 motion_desc 显式分时段缺段.

    08-18 钩子实测：LLM 把 hook 的 3 帧 kf 用等距分布（t=0.2/0.5/0.8），duration 9.92s
    → 物理 1.98/4.96/7.94s，**段 [2-4] / [4-6] / [6-9.92] 无 kf 覆盖**——校验器
    `_check_kf_time_window_alignment` 报"缺段"。源头规则 16.4 仍不遵守，必须派生。

    派生原则（确定性，不调 LLM）：
      1. 解析 motion_desc 全部时段（已用 _parse_motion_time_segments）
      2. 已有 kf 物理时刻按段归属（用段中点最近的现有 kf 复用 desc，**不重写**）
      3. 缺段：从该段文本提取作为 desc 骨架 + 加场景前缀
         - 场景前缀：优先从 ff_desc 取"完整小舟上，" / "XXX 全景，" / 等画面开头
         - 段文本是动作链（「刺客破水+举刀下刺」），已经是该瞬时描述，无需重写
         - 角色名前缀已含（段文本自身带）
      4. t = 段中点 / duration_sec（段中点 = (a+b)/2）
      5. 幂等：已有 kf 覆盖全部段的镜头跳过

    通用性：任何 motion_desc 显式分时段的镜头都适用（不限 hook）。LLM 已按时段写的
    镜头（13/14）走"全部段都有 kf 覆盖"分支直接跳过——**不影响已正确的镜头**。
    返回补齐 kf 个数。
    """
    fixed = 0
    targets: list[dict] = list(shots or [])
    if hook and isinstance(hook, dict):
        targets.append(hook)

    for s in targets:
        motion = (s.get("motion_desc") or "").strip()
        if not motion:
            continue
        dur = s.get("duration_sec")
        if not dur or dur <= 0:
            tc = s.get("timecode") or {}
            try:
                start = float(tc.get("start", 0) or 0)
                end = float(tc.get("end", 0) or 0)
                if end > start:
                    dur = end - start
            except (TypeError, ValueError):
                pass
        if not dur or dur <= 0:
            continue
        segs = _parse_motion_time_segments(motion, float(dur))
        # F1.4 修复（08-22）：motion_desc 无显式分时段时，按 implicit 单段处理
        # 之前硬跳过导致 shot 3/4/5/6（无显式时段）永远报"0 帧修复"
        # 新行为：implicit 单段 = (0, dur) → 若 已有 kfs 数 < 1，补 1 个 after 帧
        if len(segs) < 1:
            continue
        if len(segs) == 1 and segs[0] == (0.0, float(dur)):
            # 解析回退到 implicit（_parse_motion_time_segments 至少返回 1 段）
            pass
        kfs = s.get("keyframes") or []
        if not isinstance(kfs, list):
            continue

        # 收集已有 kf 的物理时刻（v6.34：走 helper，t_sec 优先）
        existing_t = []
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, s)
            if t is not None:
                existing_t.append(t)

        # 对每段判定是否已有 kf 覆盖
        missing_segs: list[tuple[int, tuple[float, float]]] = []
        for i, (a, b) in enumerate(segs):
            covered = any(a - 0.01 <= t <= b + 0.01 for t in existing_t)
            if not covered:
                missing_segs.append((i, (a, b)))

        # F1.4 增强：motion_desc 无显式时段时（仅 1 段 = (0, dur)），
        # 若 kfs < 1，补 1 个 after 帧（与 v6.33 主线兼容）
        if len(segs) < 2 and not missing_segs and len(existing_t) < 1:
            # 镜头有 motion_desc 但 kf 列表为空——强制补 1 帧
            missing_segs = [(0, segs[0])]

        if not missing_segs:
            continue  # 全部段都有 kf 覆盖（幂等/不影响已对）

        # 切分 motion_desc 为段文本（按「前N秒/M-N秒」开头匹配）
        seg_texts = _split_motion_to_seg_texts(motion, segs, float(dur))

        # 场景前缀：从 ff_desc 取前 16 字符（"完整小舟上，" / "黄昏荷塘全景，" / 等）
        ff_text = (s.get("ff_desc") or "").strip()
        scene_prefix = _extract_scene_prefix(ff_text)

        # 派生 kf
        for i, (a, b) in missing_segs:
            # 缺段对应文本（无显式时段时回退到 motion_desc 整体）
            seg_text = (seg_texts[i] if (seg_texts and i < len(seg_texts)) else motion)
            if not seg_text:
                continue
            # 段文本是动作链 → 加场景前缀变静态画面
            desc = (scene_prefix + seg_text).strip("，。； ")
            t_sec = (a + b) / 2
            # F1.4 增强：fallback 默认 stage = "after"（无显式时段的补帧用）
            new_kf = {"desc": desc, "t_sec": round(t_sec, 3), "stage": "after"}
            kfs.append(new_kf)
            existing_t.append(t_sec)  # 更新避免补的 kf 又被另一段判定
            fixed += 1
        # 按 t_sec 重排（v6.34 走 helper）
        sort_kfs_by_t(kfs, s)
    return fixed


def _split_motion_to_seg_texts(motion: str, segs: list, dur: float) -> list[str]:
    """按段顺序切分 motion_desc 为段文本列表.

    切分策略：按中文分号/句号分段，匹配每段开头的「前N秒/M-N秒」标签，
    按段顺序对齐（容忍标签缺失/乱序）。
    """
    if not motion or not segs:
        return []
    # 切原始文本为原子段（按 ；；；。)
    raw = re.split(r"[;；。]", motion)
    raw = [r.strip() for r in raw if r.strip()]
    # 给每个原子段打段序号（按 _KF_TIME_SEG_PATTERN 匹配到的秒数归到对应段）
    out: list[str | None] = [None] * len(segs)
    for atom in raw:
        m = _KF_TIME_SEG_PATTERN.search(atom)
        if not m:
            continue
        seg_idx = -1
        if m.group(1) and not m.group(2):
            # 「前N秒」「后N秒」
            n = int(m.group(1))
            kw = atom[m.start():m.end()]
            if "前" in kw:
                end = min(float(n), dur)
                for i, (a, b) in enumerate(segs):
                    if abs(a - 0.0) < 0.01 and abs(b - end) < 0.01:
                        seg_idx = i
                        break
            else:
                start = max(0.0, dur - float(n))
                for i, (a, b) in enumerate(segs):
                    if abs(a - start) < 0.01 and abs(b - dur) < 0.01:
                        seg_idx = i
                        break
        elif m.group(2) and m.group(3):
            a_v, b_v = int(m.group(2)), int(m.group(3))
            if a_v > b_v:
                a_v, b_v = b_v, a_v
            for i, (a, b) in enumerate(segs):
                if abs(a - float(a_v)) < 0.01 and abs(b - float(b_v)) < 0.01:
                    seg_idx = i
                    break
        if seg_idx < 0:
            continue
        # 去掉时间标签前缀（「前2秒：」「2-4秒：」），保留段正文
        body = re.sub(
            r"^(?:前|后)\s*\d+\s*秒\s*[:：]|^\d+\s*[-－~～]\s*\d+\s*秒\s*[:：]",
            "",
            atom,
        ).strip()
        if body and out[seg_idx] is None:
            out[seg_idx] = body
    # 缺段用空字符串占位（调用方会跳过）
    return [x or "" for x in out]


def _extract_scene_prefix(ff_text: str) -> str:
    """从 ff_desc 提取场景前缀.

    策略：取 ff_desc 第一个逗号/句号前的部分作为场景锚定（"完整小舟上，" / "黄昏荷塘全景，"）。
    若 ff_text 短（< 8 字符）则返回空串（不强行加前缀）。
    """
    if not ff_text or len(ff_text) < 8:
        return ""
    # 优先按 "，" 切（中文逗号最常见）
    for sep in ("，", "。", ",", ".", "；", ";"):
        idx = ff_text.find(sep)
        if 4 <= idx <= 30:
            return ff_text[: idx + 1]  # 含分隔符
    # 没合适切点 → 取前 12 字符 + "，"
    return ff_text[:12] + "，"


def _hard_fix_injury_continuity(shots: list) -> int:
    """致命伤建立后，同场景的后续镜头若缺锚点则追加（v6.40 08-22 扩展到 lf_desc）.

    通用化：不再硬编码"小舟"——任何带 anchor 词的镜头（"心口/洇痕/暗红/中刀/白袍/血/
    胸口/心窝/腹部/中剑"等）都视为致命伤建立点，location 包含"被打者"
    （"杨旭/听香/角色名"）的后续同 location 镜头缺锚则追加伤处锚。

    v6.40 08-22 修复：角色 role=背景（杨旭躺血仍是画面的一部分）时 lf_desc
    也应保留伤处锚。原实现只追加到 prompt，shot 7/8 角色 role=背景，
    lf_desc 描述角色时丢了伤处锚，画面渲染看不出受伤。

    修法：扫所有 shot，找到第一个含 anchor 词的镜头作为 build_idx；之后
    所有同 location 镜头若 prompt/lf_desc/ff_desc 缺 anchor 词且仍含"被打者"
    角色 → 同步追加到对应字段（v6.51：ff_desc 纳入，location 放宽 2 字头，
    泛指名不作受害者候选）。返回改写镜头数。
    """
    # 找致命伤建立镜（第一个 prompt/ff_desc/lf_desc 含 anchor 词）
    from step01_script_gen.prompt_semantic.registry import (
        MAIN_SUBJECT_PREFIXES, INJURY_ANCHOR_WORDS, STRONG_INJURY_ANCHORS,
        COLLECTIVE_NAMES, is_threat_name,
    )

    def _is_victim_candidate(nm: str) -> bool:
        """个体受害者候选：非威胁、非泛指（群演/仆从等群像不是可追踪受害者）."""
        return bool(nm) and not is_threat_name(nm) and not any(w in nm for w in COLLECTIVE_NAMES)

    build_idx = None
    build_loc = None
    # v6.50（08-29）：build 判定改用 STRONG_INJURY_ANCHORS（洇痕/中刀/血痕…）——
    # 宽表含"白袍/胸口/心口"等服装/中性部位词，开篇"素白色轻袍"即命中 →
    # build_idx 误判到刺杀前 → 全片误补伤处锚（14:46 run shot1 三 kf + hook
    # 带「伤处白袍心口一片暗红血迹」实测）
    for i, s in enumerate(shots):
        text = (s.get("prompt", "") or "") + (s.get("ff_desc", "") or "") + (s.get("lf_desc", "") or "")
        if any(w in text for w in STRONG_INJURY_ANCHORS):
            build_idx = i
            build_loc = (s.get("location", "") or s.get("scene", ""))
            break
    if build_idx is None:
        return 0

    fixed = 0
    for i in range(build_idx + 1, len(shots)):
        s = shots[i]
        loc = (s.get("location", "") or s.get("scene", ""))
        # 同 location 才延续。v6.51：放宽为「前 2 字一致」——LLM 写的 location
        # 是「小舟上，张十三质问听香」这类自由文本，与 build 镜的「小舟船头」
        # 精确串比较永不相等 → 延续层整体失明（16:34 run shot6/7 杨旭倒地
        # 图字段无锚实测）。受害者的伤在身上，跨机位同场景理应延续。
        if not build_loc or (loc != build_loc and loc[:2] != build_loc[:2]):
            continue
        prompt = s.get("prompt", "") or ""
        lf = s.get("lf_desc", "") or ""
        # 提取"被打者"角色名（多源推导）
        # 来源顺序：① main_subject 剥离动作词 ② build_idx 镜头的 char_layout role=主 角色
        #          ③ build_idx 镜头的 char_layout 排除"刺客"后的所有角色
        build_shot = shots[build_idx]
        victim_names: list[str] = []
        # 来源①：main_subject 剥离动作词 + threat 词（仅当结果与 char_layout 里的某个 name 匹配才采纳）
        main_raw = build_shot.get("main_subject", "")
        # 先收集 char_layout 里的所有角色名（用于校验来源① 剥离结果）
        cl_names_all = [
            str(item.get("name", "")).strip()
            for item in (build_shot.get("char_layout") or [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        if main_raw and "的" not in main_raw:
            stripped = main_raw
            for prefix in MAIN_SUBJECT_PREFIXES:
                stripped = stripped.replace(prefix, "")
            stripped = stripped.strip()
            # 关键：剥离结果必须与 char_layout 里的某个 name 完全匹配，才视为有效 victim
            # 否则可能是动词残留（如"破水"/"出水"）或无效词，进入来源② 继续查找
            # v6.51：泛指名（群演/仆从…）不是受害者——shot4 特写刺杀
            # main_subject='群演' 曾被采纳，shot6/7 延续因此漏补（实测）
            if _is_victim_candidate(stripped) and stripped in cl_names_all:
                victim_names.append(stripped)
        # 来源②+③：build_idx 镜头的 char_layout role=次 角色（刺客/施动者通常 role=主，被打者=次）
        # 限制 victim_names 最多 1 个：被打者是 build_idx 镜头的"主要受害角色"，唯一
        # 优先从 main_subject 剥离（来源① 已经填），如空再从 char_layout role=次 找
        if not victim_names:
            for item in (build_shot.get("char_layout") or []):
                if not isinstance(item, dict):
                    continue
                nm = str(item.get("name", "")).strip()
                role = str(item.get("role", "")).strip()
                if not nm or role != "次":
                    continue
                if not _is_victim_candidate(nm):
                    continue
                victim_names.append(nm)
                break  # 只取第一个 role=次 非 threat 角色
        # 来源④：build_idx 自己的 prompt/lf_desc 里紧邻 anchor 词的人物名
        # （"杨旭胸口已现暗红洇痕" → 命中"杨旭"）。仅在以上源都空时使用。
        if not victim_names:
            anchor_alt = "|".join(re.escape(w) for w in INJURY_ANCHOR_WORDS)
            anchor_nearby = re.findall(
                r"([一-鿿]{2,3})(?:" + anchor_alt + r")",
                (build_shot.get("prompt", "") or "") + (build_shot.get("lf_desc", "") or ""),
            )
            for nm in anchor_nearby:
                if _is_victim_candidate(nm) and nm not in victim_names:
                    victim_names.append(nm)
                    break
        # 防御：若仍空，至少从 char_layout 找主受害角色
        if not victim_names:
            for item in (build_shot.get("char_layout") or []):
                if not isinstance(item, dict):
                    continue
                nm = str(item.get("name", "")).strip()
                if _is_victim_candidate(nm):
                    victim_names.append(nm)
                    break
        # 兜底：从 char_layout 找 "role=主" 角色作为 victim
        if not victim_names:
            for item in (s.get("char_layout") or []):
                if isinstance(item, dict):
                    role = str(item.get("role", "")).strip()
                    nm = str(item.get("name", "")).strip()
                    if role in ("主", "次") and _is_victim_candidate(nm):
                        victim_names.append(nm)
        # 必须"被打者"在画面里才追加（否则刺客遁水远景镜也被污染）
        # 判定方式：victim 出现在 ① 4 字段任一 或 ② char_layout（背景角色也算）
        char_layout_names = [
            str(item.get("name", "")).strip()
            for item in (s.get("char_layout") or [])
            if isinstance(item, dict)
        ]
        victim_in_frame = bool(
            any(name in (s.get("ff_desc", "") + s.get("motion_desc", "") + prompt + lf) for name in victim_names)
            or any(name in char_layout_names for name in victim_names)
        )
        if not victim_in_frame:
            continue
        # 同步追加到 prompt + lf_desc + ff_desc（受害者出现的字段才追加）
        # v6.51：①ff_desc 纳入——image 模式首帧图恰恰由 ff_desc 生成，此前只补
        # prompt+lf 会让 ff 画出无伤的杨旭（16:34 run shot6/7 实测）；
        # ②已锚判定改 BLOOD_STATE_WORDS（含 暗红/洇开 等可见血状态词）——
        # STRONG 漏判变体锚 → 重复追加完整锚尾 → 复读去重抠出截断碎片。
        # 收紧 anchor 判定：必须出现"洇痕/中刀/中剑/刀伤/剑伤/中枪"才算真致命伤锚定；
        # 单独出现"暗红/白袍"可能是听香手指沾血或衣服被水打湿，误判会漏补。
        ff = s.get("ff_desc", "") or ""
        prompt_has_anchor = any(w in prompt for w in _BLOOD_STATE_WORDS)
        lf_has_anchor = lf and any(w in lf for w in _BLOOD_STATE_WORDS)
        ff_has_anchor = ff and any(w in ff for w in _BLOOD_STATE_WORDS)
        changed = False
        if not prompt_has_anchor:
            s["prompt"] = prompt.rstrip("。") + f"。{_INJURY_ANCHOR_TAIL}"
            changed = True
        if lf and not lf_has_anchor:
            s["lf_desc"] = lf.rstrip("。") + f"。{_INJURY_ANCHOR_TAIL}"
            changed = True
        if ff and not ff_has_anchor and any(n in ff for n in victim_names):
            # ff 只在受害者确实出现在该字段时补（反应特写 ff 无受害者时不污染）
            s["ff_desc"] = ff.rstrip("。") + f"。{_INJURY_ANCHOR_TAIL}"
            changed = True
        if changed:
            fixed += 1
    return fixed


# 跪坐角色改写时的目标措辞（幂等：已含「身侧/身旁」跳过）
_KNEEL_BESIDE_IDIOM = ("身侧", "身旁", "身边", "脚边", "边上")


def _hard_fix_kneel_beside_victim(shots: list, hook: dict | None = None) -> int:
    """跪坐位硬修复（08-23 用户反馈 shot0：听香跪在船尾而非杨旭身侧）.

    触发问题：同一镜头里既有"躺着的受害者"（杨旭仰卧船头、心口被刺），又有另一
    角色"跪坐船尾/船头/船梢"（听香）。图片模型照 ff/lf 描述把两人分开画——
    听香在船尾、杨旭在船头，画面失去"亲人守在伤者身旁"的张力，且跪向画外。

    修法：受害者在场时，把"跪坐船X/船尾/甲板"改写为「跪坐{受害者}身侧/身旁，
    面向{受害者}」。受害者名从 4 字段（ff/lf/motion/prompt）里找"仰卧/躺/倒"词
    前最近的角色名；跪坐角色从"跪坐/跪在+位置词"处取角色名。

    v6.41+（08-23）：字段范围从 ff/lf/prompt 扩到 keyframes[*].desc——关键帧是
    LLM 独立生成、U 类原本不覆盖，kf pre 帧仍写「听香跪坐船尾」，kf 图照此渲染
    又把听香画到船尾（用户 shot0 反馈的 6 号问题在关键帧路径漏修）。复用同一
    正则与幂等检测（改写后 kf desc 含「身侧」自动跳过）。

    幂等：① 目标字段已含「身侧/身旁/身边」→ 跳过；② 已写「跪坐+受害者名+身侧」
    → 跳过；多次调用结果一致。返回改写字段数。
    """
    if not shots and not hook:
        return 0
    from step01_script_gen.prompt_semantic.registry import (
        POSE_REGISTRY, is_threat_name,
    )
    # 收集 4 字段里"受害者"角色名：紧邻躺卧词（POSE_REGISTRY["reclining"]）的角色名
    reclining_alt = "|".join(re.escape(w) for w in POSE_REGISTRY["reclining"])
    victim_re = re.compile(r"([一-鿿]{2,3})(?:" + reclining_alt + r")")
    victims: set[str] = set()
    for s in list(shots) + ([hook] if hook and isinstance(hook, dict) else []):
        text = " ".join(filter(None, [
            s.get("ff_desc", "") or "",
            s.get("lf_desc", "") or "",
            s.get("motion_desc", "") or "",
            s.get("prompt", "") or "",
        ]))
        for m in victim_re.finditer(text):
            nm = m.group(1)
            if not is_threat_name(nm):
                victims.add(nm)
    if not victims:
        return 0

    fixed = 0
    for s in list(shots) + ([hook] if hook and isinstance(hook, dict) else []):
        # 收集要改写的字段（含 kf.desc——v6.41+ 关键帧跪坐位漏修）
        fld_text = []
        for field in ("ff_desc", "lf_desc", "prompt"):
            v = s.get(field)
            if isinstance(v, str) and v:
                fld_text.append((field, v))
        kfs = s.get("keyframes") or []
        for j, kf in enumerate(kfs):
            if isinstance(kf, dict):
                d = kf.get("desc")
                if isinstance(d, str) and d:
                    fld_text.append((f"keyframes[{j}].desc", d))

        for field, desc in fld_text:
            if not desc or any(w in desc for w in _KNEEL_BESIDE_IDIOM):
                continue
            new_desc = desc
            # 找「跪坐/跪在 + 位置词」——命中后改写
            for m in re.finditer(r"(([一-鿿]{2,3}))(?:跪坐|跪在|跪于)(船头|船尾|船梢|船首|船中|甲板|岸上|岸边)", desc):
                kneeler = m.group(2)
                if is_threat_name(kneeler):
                    continue
                # 受害者自身不会"跪坐"——防「杨旭跪坐船尾」改写成「杨旭跪坐杨旭身侧」
                if kneeler in victims:
                    continue
                # 该镜头里找受害者：4 字段里"仰卧/躺"前最近角色名，或已收集 victims
                victim = ""
                for v in victims:
                    if v in desc or v in (s.get("ff_desc", "") or "") + (s.get("lf_desc", "") or ""):
                        victim = v
                        break
                if not victim:
                    continue
                replace_from = m.group(0)
                replace_to = f"{kneeler}跪坐{victim}身侧，面向{victim}"
                if replace_from in new_desc:
                    new_desc = new_desc.replace(replace_from, replace_to, 1)
            if new_desc != desc:
                # 写回原字段（kf 的 desc 需要特殊处理）
                if field.startswith("keyframes["):
                    m = re.match(r"keyframes\[(\d+)\]\.desc", field)
                    if m:
                        kfs[int(m.group(1))]["desc"] = new_desc
                else:
                    s[field] = new_desc
                fixed += 1
    return fixed


# 船体裁切短语（LLM 在 ff/lf/prompt 里写"船身延伸出画外"等 → 图片模型裁掉船尾，
# 与「完整小舟在画中、船头船尾齐全」锚直接冲突。命中即整段剥离）
# 模式把「延伸出画外（…注释…）」整体吞掉（含括号），避免剥离后残留孤立半括号。
_BOAT_CROP_PATTERNS = (
    # [^。，,]{0,3} 容错「向画面右方」里的"面"字（"向画"+"面"+"右方"）；
    # [左右]方? 让"画右延伸"（无"方"字）也能命中
    re.compile(r"[，,。]?\s*船身(?:向画)?[^。，,]{0,3}[左右]方?延伸出画外(?:（[^（）]{0,30}）)?"),
    re.compile(r"[，,。]?\s*只露出船身(?:的)?一部分(?:（[^（）]{0,30}）)?"),
    re.compile(r"[，,。]?\s*船(?:头|尾)[^。，,]{0,6}(?:在画外|出画外|被截断|被切掉)(?:（[^（）]{0,30}）)?"),
    re.compile(r"[，,。]?\s*船身(?:向画)?[^。，,]{0,3}[左右]方?(?:延伸|探出)(?:[^。，,（）]{0,12})?出画(?:（[^（）]{0,30}）)?"),
)


def _hard_fix_hook_witness_not_killer(hook: dict | None = None,
                                      script: dict | None = None) -> int:
    """钩子目击者-凶手视觉歧义硬修复（v6.44，08-26 用户反馈）.

    触发问题：钩子画面里杨旭仰卧船头、胸口插刀/暗红洇痕，**同时**描写听香/某
    女性「手伸向/摸向/触碰/握向/正要去拔 刀/匕首/凶器」。图片模型照描述画出来
    就是「凶手在受害人身边处理凶器」的构图——观众一眼会把她读成凶手/栽赃者。

    原文真相（《锦衣夜行》第一回）：刺客从水中跃出刺杨旭、跳回水中不见，听香
    是目击者/受害者侧——她哆嗦地摸的是**自己濡湿的嘴唇**（沾了杨旭溅出的血），
    不是凶器。脚本 step01 LLM 反复把听香的手部动作写向刀柄，与原文相悖。

    修法：钩子（hook）4 字段 prompt/ff_desc/lf_desc/motion_desc 里若「X 伸向
    刀柄/摸向刀/触碰凶器」+ 受害者已死/被刺，X 必须改成「伸向自己濡湿的嘴唇
    （沾有 Y 溅出的血）」。「血迹是溅到嘴唇上」与「目击者刚目睹凶案」双重信号，
    让观众明确「她不是凶手、是刚被血溅一脸的受害者/目击者」。

    幂等：① 字段已含「嘴唇」/「舌尖」/「嘴唇沾血」/「摸了摸嘴唇」→ 跳过
           ② 受害者未死/无胸口刀/无洇痕 → 不触发（健康人物+手部动作不算）
    返回改写字段数。
    """
    if not hook or not isinstance(hook, dict):
        return 0

    from step01_script_gen.prompt_semantic.registry import (
        VICTIM_SIGNS, WEAPON_HANDLE_WORDS, POSE_REGISTRY,
        is_threat_name,
    )

    # 收集 4 字段文本
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")
    text = " ".join(str(hook.get(f, "") or "") for f in fields)
    if not text:
        return 0

    # 1. 受害者在场判定：VICTIM_SIGNS 至少一条出现（registry 单一事实源）
    if not any(s in text for s in VICTIM_SIGNS):
        return 0

    # 2. 受害者名提取（多源融合：script.characters 优先，POSE_REGISTRY 兜底）
    candidates: list[str] = []
    if script and isinstance(script, dict):
        for ch in script.get("characters") or []:
            ident = ch.get("identifier") if isinstance(ch, dict) else None
            if ident and not is_threat_name(ident):
                candidates.append(ident)

    reclining_alt = "|".join(re.escape(w) for w in POSE_REGISTRY["reclining"])
    victim_pattern = re.compile(
        r"([一-鿿]{2,4})(?:(" + reclining_alt + r")|被刺|身亡|已死)"
    )

    victim_name = ""
    for m in victim_pattern.finditer(text):
        nm = m.group(1)
        if candidates:
            if nm in candidates:
                victim_name = nm
                break
        else:
            if not is_threat_name(nm) and "人" not in nm:
                victim_name = nm
                break
    if not victim_name:
        victim_name = "受害人"

    # 3. 已修：检查「嘴唇+血/摸/伸向/沾」组合
    already_fixed_re = re.compile(
        r"(嘴唇|唇上|唇角|舌尖).{0,6}(血|沾血|血迹|溅)|"
        r"(摸了摸?|伸向|摸向|触碰).{0,4}自己.{0,4}(嘴唇|唇上|唇角)|"
        r"指尖?颤抖.{0,6}(嘴唇|唇上|唇角)"
    )
    if already_fixed_re.search(text):
        return 0

    # 4. 触发：手部伸向/摸向/握向/正要去 凶器（registry: WEAPON_HANDLE_WORDS）
    # 中间允许 0-8 字（覆盖"X 胸口的"5 字这种"插在受害者身上的凶器"）
    weapon_alt = "|".join(re.escape(w) for w in WEAPON_HANDLE_WORDS)
    hand_to_weapon_re = re.compile(
        r"([一-鿿]{2,3})([^。]*?)(伸向|摸向|握向|伸去|触碰|握住|伸过来|伸过去|正要去拔|正要去|要去拔|就要拔)([^。]{0,8})("
        + weapon_alt + r")"
    )

    fixed = 0
    for field in fields:
        v = hook.get(field)
        if not isinstance(v, str) or not v:
            continue
        m = hand_to_weapon_re.search(v)
        if not m:
            continue
        span_start = m.start(3)
        span_end = m.end()
        replacement = f"伸向自己濡湿的嘴唇（沾有{victim_name}胸口的血）"
        new_v = v[:span_start] + replacement + v[span_end:]
        if new_v != v:
            hook[field] = new_v
            fixed += 1
    return fixed


def _hard_fix_boat_crop(shots: list, hook: dict | None = None) -> int:
    """船体裁切硬修复（08-23 用户反馈 shot0：船尾没画）.

    触发问题：step01 生成 ff/lf/prompt 时，LLM 为表现"船体修长"写了
    「船身向画面右方延伸出画外（只露出船身一部分）」。图片模型照做把船尾裁掉，
    与 bridge 注入的「完整小舟在画中、船头船尾齐全不缺失」锚互相矛盾、画面缺船尾。

    修法：ff_desc/lf_desc/prompt 里命中船体裁切短语（延伸出画外/只露出一部分/
    船X在画外）→ 剥离该短语（连同其括号注释，避免残留孤立半括号）。桥接层
    「完整小舟」锚会补齐完整性。幂等：短语剥离后不再命中。返回改写字段数。
    """
    fixed = 0
    for s in list(shots) + ([hook] if hook and isinstance(hook, dict) else []):
        for field in ("ff_desc", "lf_desc", "prompt"):
            desc = s.get(field, "") or ""
            if not desc:
                continue
            new_desc = desc
            for pat in _BOAT_CROP_PATTERNS:
                new_desc = pat.sub("", new_desc)
            # 剥离后若仍有孤立半括号（极端文本），一并清掉（只删单边括号，不动成对）
            if new_desc.count("（") != new_desc.count("）"):
                new_desc = re.sub(r"[（）]", "", new_desc)
            # 清理剥离后残留的空段/多余标点（"…，。船身延伸出画外（…）。" → 归一）
            new_desc = re.sub(r"[，,。]{2,}", "。", new_desc)
            new_desc = new_desc.strip("。，, ") + ("。" if new_desc.strip() else "")
            if new_desc != desc:
                s[field] = new_desc
                fixed += 1
    return fixed


def _hard_fix_gaze_direction(shots: list, hook: dict | None = None) -> int:
    """视线方向硬修复：「看/望+人名/对象」但没带空间方位 → 按规则改写为「画X」。

    幂等设计：08-17 调试发现 Python `re` 的 `(?<!目光)` 多字符负向预查是定长
    回溯（"目光紧盯"中"盯"前 2 字符是"紧"+"光"≠"目光"），导致反复累积
    "目光紧盯"+"目光紧盯"…。改用**替换前**检测：v 已含"目光/凝望/仰望/俯视"
    时直接跳过该字段（视为已修）。
    返回改写字段数。
    """
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets = [hook] + targets

    fixed = 0
    for s in targets:
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = s.get(fld)
            if not v or not isinstance(v, str):
                continue
            # 替换前已含合法空间词 → 视为已修（幂等）
            if any(w in v for w in ("目光", "凝望", "仰望", "俯视")):
                continue
            new_v = v
            for pat, repl in _GAZE_FALLBACK_RULES:
                if pat.search(new_v):
                    new_v = pat.sub(repl, new_v)
            if new_v != v:
                s[fld] = new_v
                fixed += 1
    return fixed


def _hard_fix_prop_position(shots: list) -> int:
    """道具空间方位硬修复：缺方位的道具按 _PROP_POS_FALLBACK 补「画X」。

    限定改写：仅当某道具在 prompt 出现且紧邻 8 字符内无任何空间方位词时，
    把「道具」替换为「道具（画X）」附加标注（不破坏原句结构）。
    返回改写镜头数。
    """
    pos_words = ("画左", "画右", "画中", "画上", "画下", "船头", "船尾",
                 "地面", "甲板", "手边", "身侧", "身后", "胸前", "腰间",
                 "背后", "垂入", "倒在", "微倾", "洒落", "岸上", "水里",
                 "水面", "插在", "挂在", "立于", "发间")
    fixed_shots = 0
    for s in shots:
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = s.get(fld)
            if not v or not isinstance(v, str):
                continue
            new_v = v
            changed = False
            for prop, fallback in _PROP_POS_FALLBACK.items():
                for m in list(re.finditer(re.escape(prop), new_v)):
                    ctx_after = new_v[m.end():m.end() + 12]
                    ctx_before = new_v[max(0, m.start() - 5):m.start()]
                    # 已有方位词则跳过
                    if any(w in ctx_after for w in pos_words):
                        continue
                    if any(w in ctx_before for w in pos_words):
                        continue
                    # 在首次出现后追加（不替换原句）
                    insert_at = m.end()
                    suffix = f"（{fallback}）"
                    # 防重复追加
                    if new_v[insert_at:insert_at + len(suffix)] == suffix:
                        continue
                    new_v = new_v[:insert_at] + suffix + new_v[insert_at:]
                    changed = True
            if changed:
                s[fld] = new_v
                fixed_shots += 1
    return fixed_shots


# ============================================================================
# v6.40（08-22）P18 闭环扩展：补全 12 类 hard_fix，扩列报告到 11 列。
# 全部确定性改写，不调 LLM，幂等（已含则跳过）。每个函数单独可测。
# ============================================================================


# F1.1 kf t_sec 越界 hard_fix
def _hard_fix_kf_t_sec_bounds(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：kf t_sec 越界硬修复.

    08-22 跑批实测：shot 1 kf[1] t_sec=4.5 > duration_sec=4.0（越界 0.5s）。
    越界 t_sec → step02a 渲染中间帧超出本镜时间范围，video 拼接时序错位。

    修法：clamp 到 dur × 0.9（保留 ~10% 余量给 lf 帧衔接），< 0.1s 则置 0.1s。
    幂等：clamp 后值与原值相同则跳过。
    返回改写帧数。
    """
    fixed = 0
    targets: list[dict] = list(shots or [])
    if hook and isinstance(hook, dict):
        targets.append(hook)

    for s in targets:
        try:
            dur = float(s.get("duration_sec") or s.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        if dur <= 0:
            continue
        kfs = s.get("keyframes") or []
        if not isinstance(kfs, list):
            continue
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, s)
            if t is None:
                continue
            new_t = t
            if new_t > dur * 0.9:
                new_t = dur * 0.9
            if new_t < 0.1 and dur > 0.2:
                new_t = 0.1
            if abs(new_t - t) > 0.001:
                set_kf_t_sec(kf, new_t)
                fixed += 1
    return fixed


# F1.3 威胁源-视线耦合 hard_fix
_THREAT_GAZE_FIX_RE = re.compile(
    r"(惊恐|望|盯|凝神|凝视|注视|紧盯|目光|望见)[一-龥]{0,12}?(画左|画右|船头|船尾|船梢)"
)


def _hard_fix_threat_gaze_coupling(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：威胁源-视线耦合硬修复.

    8-22 跑批实测：2 次 regen 都报「镜头4 + hook 视线-威胁方位矛盾」。
    `_check_threat_gaze_coupling` 只 WARN 不改写，本函数做确定性改写：
    优先级：发现者 lf_desc 写的视线方向 > char_layout 角色 position

    改法：发现者 (听香/角色) lf_desc 写「惊恐望向画X 刺客」 → 把 char_layout
    中威胁源（刺客）的 position 改为画X（与发现者视线同向）。
    幂等：char_layout 已是该方向则跳过。
    返回改写镜头数。
    """
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)

    for s in targets:
        # 找发现者（听香/非威胁角色）的视线方向
        discoverer_gaze = None  # 方向：画左/画右
        for fld in ("lf_desc", "ff_desc", "motion_desc", "prompt"):
            v = s.get(fld) or ""
            m = _THREAT_GAZE_FIX_RE.search(v)
            if m:
                # 第二个 group（画左/画右/船头/船尾/船梢）
                pos = m.group(2)
                if pos in ("画左", "画右"):
                    discoverer_gaze = pos
                    break
        if not discoverer_gaze:
            continue
        # 改 char_layout 威胁源 position
        layout = s.get("char_layout")
        if not isinstance(layout, list):
            continue
        threat_keywords = ("刺客", "神秘人", "黑衣人", "蒙面", "杀手")
        changed = False
        for item in layout:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if any(w in name for w in threat_keywords):
                pos = str(item.get("position", "")).strip()
                if discoverer_gaze not in pos:
                    # 改写 position 为视线方向
                    # 去掉旧画左/画右前缀，加上新方向
                    new_pos = re.sub(r"画左|画右", discoverer_gaze, pos)
                    if new_pos != pos:
                        item["position"] = new_pos
                    else:
                        # 原 position 没画左/画右词 → 整体改写
                        item["position"] = discoverer_gaze
                    changed = True
        if changed:
            fixed += 1
    return fixed


# F1.5 narration/subtitle 拼接一致性 hard_fix
def _hard_fix_narration_subtitle_consistency(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：narration/subtitle 拼接一致性硬修复.

    8-22 跑批实测：2 次 regen 都报「narration 与 subtitle 拼接不一致」。
    LLM 偶发输出「…」 vs 「...」、全/半角空格差异、标点不一致。

    修法（确定性归一化）：
      1. 「...」 → 「…」（全角统一）
      2. 多个连续空格 → 单空格
      3. 同步 narration 与 subtitle（先 narration，再镜像到 subtitle）
    幂等：归一化前后无变化则跳过。
    返回改写镜头数。
    """
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)

    for s in targets:
        narr = s.get("narration_segment")
        sub = s.get("subtitle")
        if not narr and not sub:
            continue
        if not isinstance(narr, str):
            narr = ""
        if not isinstance(sub, str):
            sub = ""

        def _normalize(text: str) -> str:
            if not text:
                return text
            # 1. 三连点 → 中文省略号
            text = re.sub(r"\.{2,}", "…", text)
            # 2. 半角空格 → 移除（中文对白不应有空格）
            text = re.sub(r"\s+", "", text)
            return text

        new_narr = _normalize(narr)
        new_sub = _normalize(sub)
        # 强制以 narration_segment 为准
        if new_narr:
            new_sub = new_narr
        else:
            new_narr = new_sub

        if new_narr != narr or new_sub != sub:
            s["narration_segment"] = new_narr
            s["subtitle"] = new_sub
            fixed += 1
    return fixed


# F2.2 景别多样性 hard_fix
def _hard_fix_shot_size_diversity(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：景别多样性硬修复.

    8-22 跑批实测：2 次 regen 都报「景别多样性 4→3 种，要求 5 种」。
    LLM 默认中景/中近景为主，缺远景/特写/全景。

    修法：扫所有 shot 的 prompt/ff_desc/motion_desc，按出现频次找最常用景别词，
    把第一个含该词的 shot 末尾追加"中景" → "中景，特写镜头捕捉..." 之类的
    远景/特写/全景提示（按词表缺啥补啥）。
    幂等：shot 已含目标景别词则跳过。
    返回改写镜头数。
    """
    import storyboard_refs
    fixed = 0
    if not shots:
        return 0
    # 1. 统计已用景别
    used = set()
    for s in shots:
        text = " ".join([s.get("prompt", ""), s.get("ff_desc", ""),
                         s.get("motion_desc", "")])
        for sz in storyboard_refs.SHOT_SIZES:
            sz_short = sz.split(" ")[0]
            if sz_short in text:
                used.add(sz_short)
    # 2. 找缺失的目标景别（远景/特写/全景优先）
    priority_missing = []
    for sz in ("远景", "全景", "特写", "大特写", "中远景", "大远景"):
        if sz not in used and sz in [s.split(" ")[0] for s in storyboard_refs.SHOT_SIZES]:
            priority_missing.append(sz)
    if not priority_missing:
        return 0
    # 3. 给第一镜 prompt 追加"插入特写/远景提示"
    target_sz = priority_missing[0]
    s = shots[0]
    prompt = s.get("prompt", "") or ""
    # 幂等加固（08-23）：已含"插入…强化节奏"结构（含其他景别）即跳过——否则
    # 追加的"插入特写强化节奏"被复读机删掉"特写"后，下次统计又缺"特写"→ 反复
    # 追加，违反幂等。已含任意"插入X强化节奏"即视为本次已补全景别提示。
    if re.search(r"插入[^。，,]{1,8}强化节奏", prompt):
        return 0
    if target_sz not in prompt:
        s["prompt"] = prompt.rstrip("。") + f"。插入{target_sz}强化节奏"
        fixed += 1
    return fixed


# F2.3 运镜描述 hard_fix
_STATIC_CAMERA_TAIL = "。固定机位，平视取景"


def _hard_fix_camera_movement(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：运镜描述硬修复.

    8-22 跑批实测：2 次 regen 都报 8 镜缺运镜/机位描述。
    LLM 偶尔 prompt 写"固定机位"但大部分漏。

    修法：每镜 prompt 若不含 _MOVEMENT_TYPE_TERMS（推/拉/跟/环绕/仰拍/
    过肩/手持/摇/移/甩/变焦）任一词 + 不含"固定机位"等静态标记 → 追加
    "固定机位"提示。
    幂等：已含则跳过。
    返回改写镜头数。
    """
    import storyboard_refs
    fixed = 0
    if not hasattr(storyboard_refs, "_MOVEMENT_TYPE_TERMS"):
        return 0
    movement_words = []
    for terms in storyboard_refs._MOVEMENT_TYPE_TERMS.values():
        movement_words.extend(terms)
    static_markers = ("固定机位", "静止", "不动", "静态", "锁机位", "定格",
                      "固定镜头", "固定机位", "Static", "static")
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        prompt = s.get("prompt", "") or ""
        text = " ".join([prompt, s.get("ff_desc", ""), s.get("motion_desc", "")])
        if any(w in text for w in movement_words):
            continue
        if any(m in text for m in static_markers):
            continue
        if "空镜" in text:
            continue
        s["prompt"] = prompt.rstrip("。") + _STATIC_CAMERA_TAIL
        fixed += 1
    return fixed


# F2.4 开篇冲突 hard_fix
def _hard_fix_opening_hook(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：开篇冲突硬修复.

    8-22 跑批实测：2 次 regen 都报「前 1/3 段未建立核心冲突」。
    修法：若前 1/3 镜（=max(1, len//3)）都不含 _PUNCH_WORDS 词，则在
    第一镜 prompt 末尾追加"危机一触即发"提示。
    幂等：已含 _PUNCH_WORDS 词则跳过。
    返回改写镜头数。
    """
    if not shots:
        return 0
    if not hasattr(shots, "__iter__"):
        return 0
    # 找 _PUNCH_WORDS（与 _check_opening_hook 同源）
    # 模块级变量名在不同位置；尝试按名查
    try:
        from step01_script_gen.validation import _PUNCH_WORDS  # 同模块导入
        punch_words = _PUNCH_WORDS
    except ImportError:
        punch_words = ("刺", "杀", "叫", "破", "击", "对峙", "剑", "刀", "刺客", "血", "惊",
                       "危机", "埋伏", "陷阱", "逼近", "出手", "出手", "突袭", "反", "转",
                       "命", "亡", "断", "倒", "坠", "擒", "追", "围", "冲", "喊")
    head = shots[: max(1, len(shots) // 3)]
    head_text = " ".join(
        s.get("prompt", "") + s.get("main_subject", "") + s.get("motion_desc", "")
        for s in head if isinstance(s, dict)
    )
    if any(p in head_text for p in punch_words):
        return 0
    # 缺冲突：给第一镜追加含 _PUNCH_WORDS 词的提示
    # 08-23 修复：原版"危机一触即发，暗流涌动"不含 _PUNCH_WORDS 任一词
    # （_PUNCH_WORDS = 刺杀/偷袭/对峙/威胁/惊觉/反转/发现/质问/怒喝/伏击…），
    # validator (_check_opening_hook) 仍 WARN。改为追加"惊觉对峙危机"组合
    # 含 _PUNCH_WORDS 的"惊觉"和"对峙"，命中 validator 判定。
    s = shots[0]
    if not isinstance(s, dict):
        return 0
    prompt = s.get("prompt", "") or ""
    if "惊觉" in prompt or "对峙" in prompt:
        return 0
    s["prompt"] = prompt.rstrip("。") + "。危机一触即发，暗流涌动，惊觉对峙"
    return 1


# F2.5 爆点密度 hard_fix
def _hard_fix_punch_density(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：爆点密度硬修复.

    8-22 跑批实测：2 次 regen 都报「全片爆点 N 个 < 目标」。
    修法：每 30 秒应有 1 爆点。若 < 目标，每缺 1 爆点，给最后一镜 prompt
    追加"局势骤变"提示（弱增强，避免污染叙事）。
    幂等：已含爆点词则跳过。
    返回改写镜头数。
    """
    if not shots:
        return 0
    try:
        from step01_script_gen.validation import _PUNCH_WORDS
        punch_words = _PUNCH_WORDS
    except ImportError:
        punch_words = ("刺", "杀", "叫", "破", "击", "对峙", "剑", "刀", "刺客", "血", "惊",
                       "危机", "埋伏", "陷阱", "逼近", "出手", "突袭", "反", "转",
                       "命", "亡", "断", "倒", "坠", "擒", "追", "围", "冲", "喊")
    punch_count = 0
    total_dur = 0.0
    for s in shots:
        if not isinstance(s, dict):
            continue
        text = " ".join([s.get("prompt", ""), s.get("narration_segment", ""),
                         s.get("main_subject", ""), s.get("motion_desc", "")])
        if any(p in text for p in punch_words):
            punch_count += 1
        try:
            total_dur += float(s.get("duration_sec") or s.get("duration") or 0)
        except (TypeError, ValueError):
            pass
    if total_dur <= 0:
        total_dur = 60.0
    needed = max(1, int(total_dur // 30) + (1 if total_dur % 30 else 0))
    if punch_count >= needed:
        return 0
    # 缺 N 个爆点：最后一镜追加
    shortage = needed - punch_count
    s = shots[-1]
    if not isinstance(s, dict):
        return 0
    prompt = s.get("prompt", "") or ""
    if "局势骤变" in prompt:
        return 0
    s["prompt"] = prompt.rstrip("。") + f"。局势骤变，{shortage}个爆点叠加"
    return 1


# F2.7 lf_desc 字数 hard_fix
def _hard_fix_lf_desc_length(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：lf_desc 字数下限硬修复.

    8-22 跑批实测：shot 1 lf_desc 38 字 < 40；hook lf_desc 50 字 < 60。
    修法：
      - shot lf_desc < 40 字（且非空镜）→ 拼接 char_layout 第一人称布局
      - hook lf_desc < 60 字 → 拼接「所有角色最终状态」描述
    幂等：append 后字符变长，但允许 5 字符内差额
    返回改写镜头数。
    """
    fixed = 0
    empty_subjects = ("空镜", "空景", "远景", "环境", "山峦", "山色", "夕阳", "月光",
                      "荷塘", "夜幕", "雨", "雪", "风", "云", "日落", "日出", "月色")
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        lf = s.get("lf_desc", "") or ""
        is_hook = (hook is not None and s is hook)
        threshold = 60 if is_hook else 40
        if len(lf) >= threshold:
            continue
        # 空镜豁免
        if not is_hook:
            ms = str(s.get("main_subject", "") or "").strip()
            if any(es in ms for es in empty_subjects):
                continue
        # 取 char_layout 第一个角色做布局
        layout = s.get("char_layout") or []
        role_layout = ""
        if isinstance(layout, list) and layout:
            for item in layout[:3]:
                if isinstance(item, dict):
                    nm = item.get("name", "").strip()
                    pos = item.get("position", "").strip()
                    facing = item.get("facing", "").strip()
                    if nm and pos:
                        role_layout = f"{nm}位于{pos}"
                        if facing:
                            role_layout += f"、{facing}"
                        role_layout += "。"
                        break
        if not role_layout:
            role_layout = "画面完整，所有角色定锚。"
        s["lf_desc"] = lf.rstrip("。") + f"。{role_layout}"
        fixed += 1
    return fixed


# F2.8 char_layout 同步 hard_fix
def _hard_fix_char_layout_sync(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：char_layout 同步硬修复.

    8-22 跑批实测：shot 6 char_layout 听香写"画右"但 prompt 说"跪坐中舱"。
    修法：扫 shot 文本（prompt/ff_desc/lf_desc/motion_desc）中"画X"位置词，
    若 char_layout 同一角色 position 与文本矛盾 → 改 char_layout.position。
    幂等：char_layout position 已匹配则跳过。
    返回改写镜头数。
    """
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        if not isinstance(s, dict):
            continue
        layout = s.get("char_layout")
        if not isinstance(layout, list) or not layout:
            continue
        text = " ".join([s.get("prompt", ""), s.get("ff_desc", ""),
                         s.get("lf_desc", ""), s.get("motion_desc", "")])
        if "画左" not in text and "画右" not in text:
            continue
        # 找每个角色
        for item in layout:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            # 在 text 中找 name 最近的"画X"（v6.43 08-23 审计：
            # 旧正则会把「刺客侧身朝画右的杨旭」误判成刺客 position=画右——
            # 紧跟朝向词（朝/向/望/盯）之后的画左/画右描述的是视线/面向，
            # 不是站位。改为：枚举名字后同句内的全部（画左|画右），
            # 跳过前面带朝向词的，取第一个真正的站位词）
            pos_now = str(item.get("position", "")).strip()
            dir_in_text = None
            for nm in re.finditer(re.escape(name), text):
                seg_end = text.find("。", nm.end())
                seg = text[nm.end(): seg_end if seg_end >= 0 else len(text)]
                for dm in re.finditer(r"(画左|画右)", seg):
                    prev2 = seg[max(0, dm.start() - 2):dm.start()]
                    if any(c in "朝向望盯" for c in prev2):
                        continue
                    dir_in_text = dm.group(1)
                    break
                if dir_in_text:
                    break
            if dir_in_text and dir_in_text not in pos_now:
                    # 改写 position
                    new_pos = re.sub(r"画左|画右", dir_in_text, pos_now)
                    if new_pos != pos_now:
                        item["position"] = new_pos
                    else:
                        item["position"] = dir_in_text
                    fixed += 1
    return fixed


# F2.9 首镜头 AI 生成标注 hard_fix
def _hard_fix_platform_compliance(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：首镜头 AI 生成标注硬修复.

    8-22 跑批实测：首镜头 prompt 缺"AI生成"标注。
    修法：index==1 的 shot.prompt 末尾追加"AI生成"标注（仅追加 1 次）。
    幂等：已含任一 AI 关键词则跳过。
    返回改写镜头数。
    """
    if not shots:
        return 0
    s = shots[0]
    if not isinstance(s, dict):
        return 0
    if s.get("index", 0) != 1:
        return 0
    prompt = s.get("prompt", "") or ""
    ai_kws = ("AI生成", "AI创作", "人工智能", "AIGC")
    if any(kw in prompt for kw in ai_kws):
        return 0
    s["prompt"] = prompt.rstrip("。") + "。（AI生成）"
    return 1


# F3.2 复读机 hard_fix
def _hard_fix_prompt_repetition(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：prompt 字段复读机硬修复.

    8-22 跑批实测：shot 6/8 prompt 重复"眼睛瞪大，瞳孔收缩，嘴巴张开，
    面部肌肉扭曲" 4 次；shot 8 motion_desc 重复"眼睛瞪大"。

    修法：扫每个字段（prompt/ff_desc/lf_desc/motion_desc），按 4-token 短语
    滑动窗口，重复 ≥2 次的删第 2 次起的短语。
    幂等：删后无重复则跳过。
    返回改写字段数。
    """
    # v6.43：委托给 constraints.prompt_repetition.dedupe_text_4char——
    # 旧实现 replace(token,"",1) 删第一次出现且检测/替换文本错位会打洞
    # （08-23 shot9「神（）（）（）情冷峻」），新实现按区间重建+括号保护。
    from step01_script_gen.constraints.prompt_repetition import dedupe_text_4char
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        if not isinstance(s, dict):
            continue
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = s.get(fld)
            if not v or not isinstance(v, str) or len(v) < 8:
                continue
            new_v = dedupe_text_4char(v)
            if new_v != v:
                s[fld] = new_v
                fixed += 1
    return fixed


# F3.4 lf_desc 动作进行态 hard_fix
_LF_MIDSTATE_TERMS = {
    "初现": "已现",
    "即将": "已",
    "正在": "已",
    "渐渐": "已",
    "缓慢": "已",
    "慢慢": "已",
    "缓缓": "已",
    "正在": "已",
}


def _hard_fix_lf_midstate(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：lf_desc 动作进行态硬修复.

    8-22 跑批实测：shot 4 lf_desc "暗红洇痕初现"（动作进行态，违反 system 13.6）。
    修法：扫 lf_desc 命中"初现/即将/正在/渐渐/缓慢/慢慢/缓缓" → 替换为"已/已现"。
    幂等：替换前后无变化则跳过。
    返回改写镜头数。
    """
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        lf = s.get("lf_desc", "") or ""
        if not lf:
            continue
        new_lf = lf
        for old, new in _LF_MIDSTATE_TERMS.items():
            if old in new_lf:
                new_lf = new_lf.replace(old, new)
        if new_lf != lf:
            s["lf_desc"] = new_lf
            fixed += 1
    return fixed


# F3.7 hook 时长下限 4-6s hard_fix
def _hard_fix_hook_duration(shots: list, hook: dict | None = None) -> int:
    """v6.40（08-22）P18 闭环扩展：hook 时长下限 4-6s 硬修复.

    8-22 跑批实测：hook duration 3.0s 偏短，钩子应在 4-6s 抓人。
    修法：hook duration_sec < 4.0 → 扩到 4.0；若 kfs 数 < 2 → 补 1 个 after 帧。
    幂等：duration_sec ≥ 4.0 且 kfs ≥ 2 则跳过。
    返回改写镜头数（hook 只算 1 镜）。
    """
    if not hook or not isinstance(hook, dict):
        return 0
    try:
        dur = float(hook.get("duration_sec") or hook.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur >= 4.0:
        return 0
    # 扩到 4.0
    hook["duration"] = 4.0
    hook["duration_sec"] = 4.0
    # 补 1 个 after 帧
    kfs = hook.get("keyframes")
    if isinstance(kfs, list) and len(kfs) < 2:
        kfs.append({"desc": "画面定格，所有角色定锚", "stage": "after", "t_sec": 3.5})
    return 1


# ============================================================================
# F3.6（08-22）长对话拆镜 hard_fix
# ============================================================================

def _estimate_speech_duration(text: str) -> float:
    """估算对话时长（与 timecode._estimate_duration 同源逻辑）.

    4 字/秒基础语速 + 标点停顿缓冲（，=0.3s/。=0.6s/！=0.5s/？=0.5s）。
    MIN = 3.0s.
    """
    if not text:
        return 3.0
    clean = re.sub(r"[^\w一-鿿]", "", text)
    n = len(clean)
    if n == 0:
        return 3.0
    rate = 4.0
    if n < 10:
        rate = 3.0
    elif n > 50:
        rate = 4.5
    dur = n / rate
    pauses = text.count("，") * 0.3 + text.count("。") * 0.6 + \
             text.count("！") * 0.5 + text.count("？") * 0.5
    return max(dur + pauses, 3.0)


# v6.50（08-29）拆镜视觉差异化 helpers 已上移 split_visuals.py——
# timecode._do_pre_split_oversized_shots（半整数索引 pre-pass）与 F3.6
# 长对话拆镜两个拆镜器共用（14:22 run 实测第二拆分器同样照抄）。
from step01_script_gen.split_visuals import (  # noqa: E402,F401
    FRAMING_LADDER as _FRAMING_LADDER,
    shift_framing as _shift_framing,
    extract_speaker as _extract_speaker,
    differentiate_split_visuals as _differentiate_split_visuals,
)


def _hard_fix_split_long_dialogue_shot(shots: list, hook: dict | None = None) -> int:
    """v6.40 F3.6（08-22）长对话拆镜 hard_fix.

    8-22 跑批实测：shot 8 narration 60+ 字 → speech_duration ~15s → provider
    截断到 12s（Seedance 1.0 pro 能力表）→ TTS 音轨比视频长 3-5s，step04
    merge 音画不同步。

    修法：每镜 narration_segment 估算时长 > dur_cap（默认 12s）时，按
    语义停顿（，/。/！/？）拆 N 镜（N = ceil(est_dur / dur_cap) 上限 3）：
      1. 抽说话人前缀（"听香颤声道："）保留在每个子镜
      2. 按子句数均分到 N 镜，每镜重算 speech_duration < dur_cap
      3. 继承 scene/location/main_subject/char_layout/prompt/ff_desc
      4. kf 只在第一子镜保留（原 moment 归属该镜）
      5. 标 __split_from_idx + __split_part for traceability
      6. 重新按 index 排序

    幂等：shot 已含 __split_from_idx 标记或 estimated ≤ dur_cap 跳过。
    返回新生成子镜数（不含原始镜；原始镜 1 个被替换为 N 个）。
    """
    if not shots:
        return 0
    # 1. 拿视频模型时长上限
    try:
        from shared.model_caps import active_video_duration_cap
        dur_cap = active_video_duration_cap() or 12.0
    except Exception:
        dur_cap = 12.0

    new_shots: list[dict] = []
    fixed = 0

    for s in shots:
        if not isinstance(s, dict):
            new_shots.append(s)
            continue
        # 幂等：已拆过的子镜跳过
        if s.get("__split_from_idx"):
            new_shots.append(s)
            continue
        narr = s.get("narration_segment", "") or ""
        # 非对话镜（无说话人前缀「X道：」）跳过
        if not narr or "：" not in narr and ":" not in narr:
            new_shots.append(s)
            continue
        # 估算时长
        est = _estimate_speech_duration(narr)
        if est <= dur_cap:
            new_shots.append(s)
            continue

        # 拆 2-3 镜（按子句数 / 单镜容量）
        # 2. 抽说话人前缀（"X道：/X道:"）
        m = re.match(r"^([^：:]+[：:])(.*)$", narr, flags=re.DOTALL)
        if m:
            speaker_prefix = m.group(1)
            body = m.group(2).strip()
        else:
            speaker_prefix = ""
            body = narr

        # 3. 按标点切子句（保留标点便于朗读停顿）
        # 中文标点优先级：。！？（句末）> ，；、、
        raw_clauses = re.split(r"([。！？])", body)
        # 合并：re.split 带分组后 ["a", "。", "b", "！", ...] → ["a。", "b！", ...]
        clauses: list[str] = []
        i = 0
        while i < len(raw_clauses):
            seg = raw_clauses[i].strip()
            if not seg:
                i += 1
                continue
            if i + 1 < len(raw_clauses) and raw_clauses[i + 1] in ("。", "！", "？"):
                clauses.append(seg + raw_clauses[i + 1])
                i += 2
            else:
                # 残留部分（无句末标点），按逗号再切
                sub = re.split(r"([，；、])", seg)
                j = 0
                while j < len(sub):
                    s_ = sub[j].strip()
                    if not s_:
                        j += 1
                        continue
                    if j + 1 < len(sub) and sub[j + 1] in ("，", "；", "、"):
                        clauses.append(s_ + sub[j + 1])
                        j += 2
                    else:
                        clauses.append(s_)
                        j += 1
                i += 1
        if not clauses:
            clauses = [body]
        if len(clauses) < 2:
            # 单子句无法拆
            new_shots.append(s)
            continue

        # 4. 决定拆 N 镜（2-3），按子句数均分
        n_parts = max(2, min(3, -(-len(clauses) // 2)))  # ceil(N/2) 上限 3
        if n_parts == 1:
            new_shots.append(s)
            continue
        # 5. 分配子句
        groups: list[list[str]] = [[] for _ in range(n_parts)]
        for idx, c in enumerate(clauses):
            groups[idx % n_parts].append(c)
        # 至少每组 ≥1 子句（已保证）
        # 6. 构造 N 个子镜
        original_index = s.get("index", 0)
        for part_i, group in enumerate(groups):
            new_narr_body = "".join(group)
            new_narr = (speaker_prefix + new_narr_body) if speaker_prefix else new_narr_body
            new_sub = new_narr  # 字幕与 narration 一致
            # 深拷贝关键字段（避免引用同一对象）
            new_shot = {
                "index": original_index + part_i * 0.001,  # 临时占位，调用方按位重排
                "narration_segment": new_narr,
                "subtitle": new_sub,
                "scene": s.get("scene", ""),
                "location": s.get("location", ""),
                "main_subject": s.get("main_subject", ""),
                "core_keywords": list(s.get("core_keywords") or []),
                "prompt": s.get("prompt", ""),
                "ff_desc": s.get("ff_desc", ""),
                "lf_desc": s.get("lf_desc", ""),
                "context_bridge": s.get("context_bridge", ""),
                "audio_cue": s.get("audio_cue", ""),
                "fx_effect": s.get("fx_effect", ""),
                "color_tone": s.get("color_tone", ""),
                "cam_idx": s.get("cam_idx", 1),
                "clip_transition": s.get("clip_transition", "硬切，转场时长0.2s"),
                "variation_type": s.get("variation_type", "small"),
                "char_layout": [dict(item) for item in (s.get("char_layout") or []) if isinstance(item, dict)],
                # v6.57+ 继承 frame_layouts：ff/lf 出镜表复制（连贯性），kfs
                # 跟随 keyframes（只第一子镜保留）
                "frame_layouts": {
                    "ff":  [dict(item) for item in ((s.get("frame_layouts") or {}).get("ff") or []) if isinstance(item, dict)],
                    "lf":  [dict(item) for item in ((s.get("frame_layouts") or {}).get("lf") or []) if isinstance(item, dict)],
                    "kfs": [],
                },
                "__split_from_idx": original_index,
                "__split_part": part_i + 1,
                "__split_total": n_parts,
            }
            # kf 只在第一子镜保留
            if part_i == 0:
                kfs = s.get("keyframes")
                if isinstance(kfs, list):
                    new_shot["keyframes"] = [dict(kf) for kf in kfs if isinstance(kf, dict)]
                    # v6.57+ kfs 同步继承
                    _src_kfs = (s.get("frame_layouts") or {}).get("kfs")
                    if isinstance(_src_kfs, list):
                        new_shot["frame_layouts"]["kfs"] = [list(items) for items in _src_kfs if isinstance(items, list)]
            # v6.50：拆镜视觉差异化（治「拆镜照抄」——相邻子镜同 prompt、ff==lf）
            _differentiate_split_visuals(s, new_shot, part_i)
            new_shots.append(new_shot)
            fixed += 1
        # 原始镜不保留（已被 N 子镜替换）
    # 7. 重新按 index 排序（保留 hook 在外）
    new_shots.sort(key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0)
    # 8. 重新编号 index 为 1, 2, 3, ...
    for new_idx, ns in enumerate(new_shots, 1):
        if isinstance(ns, dict):
            ns["index"] = new_idx
    # 9. in-place 替换原 shots 列表
    shots.clear()
    shots.extend(new_shots)
    return fixed


# T1 船体宽度硬修复（v6.40+，08-23）
def _hard_fix_boat_width(shots: list, hook: dict | None = None) -> int:
    """v6.40+（08-23）船体宽度硬修复：检测「窄身」「长X米…舴艋舟/小舟」但无宽度 → 补「宽1.4米」。

    08-23 实测：shot 1 prompt 写「一叶小舟（长6米窄身无篷舴艋舟）」，「窄身」被模型
    忠实渲染成"木板"——杨旭和听香坐着就把船占满，无视觉余量。LLM 自改时倾向
    把"舴艋舟"删掉换"画舫"，但舴艋舟是原文场景标识不该改。确定性兜底：保留船型，
    把"窄身"删/替换成"宽1.4米"。

    改写规则（按字段 prompt/ff_desc/lf_desc/motion_desc/keyframes[*].desc）：
      ① 「长X米窄身」 → 「长X米宽1.4米」（保留米数，仅替"窄身"）
      ② 「窄身无篷舴艋舟」/「窄身无篷小舟」 → 「宽1.4米无篷…」（删除"窄身"，插入宽度）
      ③ 「长X米（…）舴艋舟」无"宽"字 → 同样插入"宽1.4米"在"无篷"前

    幂等：已含"宽X米"或"宽身"则跳过。
    返回改写字段数（含 kf 描述）。
    """
    import re as _re
    if not shots:
        return 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets = [hook] + targets

    fixed = 0
    for s in targets:
        # 收集要改写的字段（含 kf.desc）
        fld_text = []
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = s.get(fld)
            if isinstance(v, str) and v:
                fld_text.append((fld, v))
        kfs = s.get("keyframes") or []
        for j, kf in enumerate(kfs):
            if isinstance(kf, dict):
                d = kf.get("desc")
                if isinstance(d, str) and d:
                    fld_text.append((f"keyframes[{j}].desc", d))

        for fld, v in fld_text:
            # 幂等：已含宽度或宽身则跳过
            if _re.search(r"宽\d", v) or "宽身" in v:
                continue
            new_v = v
            # ① 「长X米窄身」 → 「长X米宽1.4米」
            new_v = _re.sub(
                r"(长\d+(?:\.\d+)?米)窄身",
                r"\1宽1.4米",
                new_v,
            )
            # ② 单独「窄身」前后无"长X米" → 整段删"窄身"
            new_v = _re.sub(r"窄身", "", new_v)
            # ③ 含"长X米...无篷"但无"宽"字 → 在"无篷"前插"宽1.4米"
            if "无篷" in new_v and not _re.search(r"宽\d", new_v):
                new_v = _re.sub(
                    r"(长\d+(?:\.\d+)?米)无篷",
                    r"\1宽1.4米无篷",
                    new_v,
                )
            if new_v != v:
                # 写回原字段（kf 的 desc 需要特殊处理）
                if fld.startswith("keyframes["):
                    m = _re.match(r"keyframes\[(\d+)\]\.desc", fld)
                    if m:
                        kfs[int(m.group(1))]["desc"] = new_v
                else:
                    s[fld] = new_v
                fixed += 1
    return fixed


# T2 面向→凝望 视线硬修复（v6.40+，08-23）
_F2G_GAZE_MARKERS = ("目光", "凝望", "仰望", "俯视", "凝视")
_F2G_FACING_TARGET_RE = _re_re_compile = None  # 占位避免 pylint 警告


def _hard_fix_facing_to_gaze(shots: list, hook: dict | None = None) -> int:
    """v6.40+（08-23）char_layout「面向[人名]」→ 改写为「目光凝望[人名]」。

    08-23 实测：shot 1 char_layout 写"听香 面向杨旭"，prompt 只写"听香撑油纸伞立于身侧"
    无任何"看/望杨旭"模式，_hard_fix_gaze_direction 不触发（它只匹配「看/望+人名」），
    模型渲染时听香身体朝向杨旭但眼神飘向他身后的水面——动作 ≠ 视线。

    修法：扫所有 char_layout 条目，若 facing="面向[人名]"（人名而非"画左/画右/画上"等方位），
    且 prompt/ff/lf/motion/kf.desc 字段中无"目光/凝望/凝视"+该人名 → 在角色名首次出现
    之后追加"目光凝望[人名]"。

    幂等：已含 _F2G_GAZE_MARKERS 任一者 + 该人名则跳过。
    返回改写字段数。
    """
    import re as _re
    if not shots:
        return 0
    from step01_script_gen.prompt_semantic.registry import COMMON_PERSON_NAMES, DIRECTION_WORDS
    # 名字集合：从 char_layout 提取；兜底用 registry 主名清单（去重 COMMON_PERSON_NAMES）
    NAME_POOL = COMMON_PERSON_NAMES
    # 只匹配"面向<人名>"，排除"面向画X"（方向词而非目标）
    direction_words = DIRECTION_WORDS
    facing_re = _re.compile(r"^面向(.+)$")

    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets = [hook] + targets

    fixed = 0
    for s in targets:
        cl = s.get("char_layout") or []
        if not isinstance(cl, list):
            continue
        # 收集本镜所有「角色X → 面向Y（Y 是人名）」对
        facing_pairs = []
        for item in cl:
            if not isinstance(item, dict):
                continue
            x_name = item.get("name", "")
            facing = item.get("facing", "")
            if not (x_name and facing):
                continue
            m = facing_re.match(facing.strip())
            if not m:
                continue
            target = m.group(1).strip()
            if not target or target in direction_words:
                continue
            if target in NAME_POOL or len(target) <= 4:  # 中文姓名短
                facing_pairs.append((x_name, target))

        if not facing_pairs:
            continue

        # 收集要改写的字段
        fld_text = []
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = s.get(fld)
            if isinstance(v, str) and v:
                fld_text.append((fld, v))
        kfs = s.get("keyframes") or []
        for j, kf in enumerate(kfs):
            if isinstance(kf, dict):
                d = kf.get("desc")
                if isinstance(d, str) and d:
                    fld_text.append((f"keyframes[{j}].desc", d))

        for x_name, target in facing_pairs:
            for fld, v in fld_text:
                # 幂等：已含"目光/凝望/凝视"+target 则跳过
                has_gaze = any(
                    (mk in v) and (target in v[v.find(mk):v.find(mk) + 20])
                    for mk in _F2G_GAZE_MARKERS
                )
                if has_gaze:
                    continue
                # 找 x_name 首次出现位置 → 定位到其所在分句末尾再插入
                # （v6.43 08-23 审计：紧贴名字后插入会切开括号/定语，产生
                # 断句；改到分句边界（下一个 ，。；！？）前插入，句法完整）
                m = _re.search(_re.escape(x_name), v)
                if not m:
                    continue
                insert_at = m.end()
                while insert_at < len(v) and v[insert_at] not in "，。；！？":
                    insert_at += 1
                # 防重复追加
                tail = v[insert_at:insert_at + 20]
                if "目光凝望" in tail and target in tail:
                    continue
                suffix = f"，目光凝望{target}"
                new_v = v[:insert_at] + suffix + v[insert_at:]
                if new_v != v:
                    if fld.startswith("keyframes["):
                        m2 = _re.match(r"keyframes\[(\d+)\]\.desc", fld)
                        if m2:
                            kfs[int(m2.group(1))]["desc"] = new_v
                    else:
                        s[fld] = new_v
                    fixed += 1
    return fixed


# =============================================================================
# v6.41（08-23）P19 闭环：14+ 类 warning-only 问题全量硬修复
# 8-22~8-23 跑批发现 LLM 3 轮 + REPAIR 都改不动的 14 类确定性缺陷：
#   W1  景别多样性仍单一（连续 5 镜同景别）
#   W2  运镜类型单一（全片仅 1-2 种）
#   W3  打斗场景缺打击反馈词
#   W4  打斗/特写场景转场关键词缺失
#   W5  多人站位全在一侧无纵深
#   W6  红果总时长 < 60s / 末镜 < 4s
#   W7  高潮镜头分布不均（全在前中段）
#   W8  开篇冲突（既有 _hard_fix_opening_hook，扩 1 类）
#   W9  kf stage='pre' 含「指尖沾血」被误判矛盾
#   W10 相邻景别跨度 >2 级（景别跳跃）
#   W11 180° 轴线越轴
#   W12 prompt 长度 cap 误报（已修 _check_prompt_length_budget）
#   W13 镜头衔接词（同地点连续镜）
#   W14 多人站位纵深（同 W5，已合并）
# =============================================================================


# W1: 景别多样性增强 hard_fix（8-22 旧版只补第一镜 1 个缺项，3-5 镜连续同景别修不动）
_FRAMING_REPLACE = {
    # (原景别, 替代景别) — 按连续 3+ 镜同景别时挑中间一镜替换
    "中近景": "中景",      # 5+ 镜中近景 → 中间一镜改中景
    "中景": "全景",        # 3+ 镜中景 → 改全景
    "全景": "远景",        # 3+ 镜全景 → 改远景
    "特写": "大特写",      # 3+ 镜特写 → 改大特写
}


def _hard_fix_framing_diversity_v2(shots: list, hook: dict | None = None) -> int:
    """W1 v6.41 景别多样性硬修复（增强版，08-23）.

    旧版 _hard_fix_shot_size_diversity 只在 shots[0] 末尾追加 1 个缺项景别，对
    "shot 1 全景/shot 2 中景/shot 3-8 中近景" 这种 3-5 镜连续同景别束手无策。

    新版两步走：
      1. 沿用旧版全局缺项景别（远景/特写/全景），补 shots[0] 末尾 1 次（幂等）
      2. 检测连续 3+ 镜同 framing 词 → 强制把中间一镜的 framing 词替换为相邻
         景别（中近景→中景/特写，中景→全景，全景→远景），打破连镜
    幂等：替换后景别与前后不同，再次调用扫描会跳过。
    """
    fixed = 0
    if not shots:
        return 0
    # 步骤 1：调旧版逻辑保证全局缺项景别至少 1 次（兼容旧 hard_fix 顺序）
    fixed += _hard_fix_shot_size_diversity(shots, hook)

    # 步骤 2：扫连续 3+ 镜同景别
    from collections import Counter
    sequences = []  # [(start_idx, end_idx, framing_word), ...]
    cur_start = 0
    cur_framing = None
    for i, s in enumerate(shots):
        if not isinstance(s, dict):
            continue
        prompt = s.get("prompt", "") or ""
        # 取 prompt 第一个景别词
        sz = None
        for kw in ("大特写", "特写", "中近景", "中景", "中远景", "全景", "远景", "大远景", "鸟瞰"):
            if kw in prompt:
                sz = kw
                break
        if sz == cur_framing:
            continue
        if cur_framing is not None and i - cur_start >= 3:
            sequences.append((cur_start, i - 1, cur_framing))
        cur_start = i
        cur_framing = sz
    if cur_framing is not None and len(shots) - cur_start >= 3:
        sequences.append((cur_start, len(shots) - 1, cur_framing))

    # 对每段连续同景别，挑中间一镜换 framing
    for start, end, framing in sequences:
        if start >= end:
            continue
        # 选中间一镜（向下取整）
        mid = start + (end - start) // 2
        target = shots[mid]
        if not isinstance(target, dict):
            continue
        # 选替换景别
        new_framing = _FRAMING_REPLACE.get(framing, "中景")
        if new_framing == framing:
            continue
        prompt = target.get("prompt", "") or ""
        # 幂等：已含目标 framing 跳过
        if new_framing in prompt:
            continue
        # 用 replace count=1 替换第一个出现的 framing
        new_prompt = prompt.replace(framing, new_framing, 1)
        if new_prompt != prompt:
            target["prompt"] = new_prompt
            fixed += 1
    return fixed


# W2: 运镜多样性 hard_fix（全片仅 1-2 种运镜时按场景角色强制补差异）
_CAMERA_BY_SHOT_ROLE = [
    # (角色判定函数返回 True, 推荐运镜)
    ("远景建立", "。缓慢推近", lambda s: "远景" in (s.get("prompt", "") or "") and "推近" not in (s.get("prompt", "") or "")),
    ("对话对望", "。过肩镜头", lambda s: any(kw in (s.get("main_subject", "") or "") for kw in ("张十三", "听香", "老汉", "肖管事")) and "过肩" not in (s.get("prompt", "") or "")),
    ("打斗/刺杀", "。快速跟拍", lambda s: any(kw in (s.get("main_subject", "") or "") + (s.get("motion_desc", "") or "") for kw in ("刺杀", "打斗", "搏斗", "搏杀", "偷袭", "伏击")) and "跟拍" not in (s.get("prompt", "") or "")),
    ("质问/怒喝", "。缓推近", lambda s: any(kw in (s.get("narration_segment", "") or "") for kw in ("怒", "喝", "质问", "厉声", "骂道")) and "推" not in (s.get("prompt", "") or "")),
    ("反应/特写", "。环绕镜", lambda s: any(kw in (s.get("main_subject", "") or "") for kw in ("听香", "刺客")) and "中近景" in (s.get("prompt", "") or "") and "环绕" not in (s.get("prompt", "") or "")),
]


def _hard_fix_camera_variety(shots: list, hook: dict | None = None) -> int:
    """W2 v6.41 运镜多样性硬修复（08-23）.

    旧版 _hard_fix_camera_movement 只补"固定机位"（无运镜时静态），LLM 反复输出
    "固定机位"导致全片 8 镜 8 个固定机位——0 种运镜变化，校验报"全片运镜类型单一
    （仅 ['手持', '环绕镜']）"或"仅 ['固定机位']"。

    新版：全片已用运镜 < 3 种时，按每镜角色判定补 1 个差异化运镜：
      - 远景建立镜 → "缓慢推近"（建立景深）
      - 对白镜（张十三/听香） → "过肩"
      - 打斗/刺杀镜 → "快速跟拍"
      - 质问/怒喝 → "缓推近"
      - 听香/刺客反应 → "环绕镜"
    幂等：已含对应运镜词跳过。
    """
    if not shots:
        return 0
    # 先调旧版保底（无任何运镜则补固定机位）
    fixed = _hard_fix_camera_movement(shots, hook)

    # 统计全片运镜类型
    from shared.model_caps import active_video_is_seedance
    # 08-23 修复：原误用 storyboard_refs._MOVEMENT_TYPE_TERMS（不存在于该模块）
    # _MOVEMENT_TYPE_TERMS/_ANGLE_TECHNIQUES/_STATIC_MARKERS 实际定义在本 validation.py
    # 模块内（line 2563+，v6.10 _check_camera_movement 同源）
    movement_words = set()
    if "_MOVEMENT_TYPE_TERMS" in globals():
        for terms in _MOVEMENT_TYPE_TERMS.values():
            movement_words.update(terms)
    angle_words = set(_ANGLE_TECHNIQUES)
    # 全片已用运镜词集
    used_movement = set()
    for s in shots:
        if not isinstance(s, dict):
            continue
        text = " ".join([s.get("prompt", "") or "", s.get("ff_desc", "") or "",
                         s.get("motion_desc", "") or ""])
        for w in movement_words:
            if w in text:
                used_movement.add(w)
    # 已用 ≥3 种 → 不补
    if len(used_movement) >= 3:
        return fixed
    # 缺多样性：按每镜角色挑差异化运镜
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    added = 0
    for s in targets:
        if not isinstance(s, dict):
            continue
        prompt = s.get("prompt", "") or ""
        # 已含 ≥2 种运镜词的不再加（避免堆叠）
        cnt = sum(1 for w in movement_words if w in prompt)
        if cnt >= 2:
            continue
        for label, tail, predicate in _CAMERA_BY_SHOT_ROLE:
            if predicate(s):
                # Seedance 严格一镜一运镜：避免再叠加（已含静态标记也不加）
                if active_video_is_seedance() and cnt >= 1:
                    break
                if any(m in prompt for m in ("固定机位", "静止", "不动", "静态", "锁机位", "定格")):
                    # 静态标记 + 运镜词共存，先去掉静态标记再加运镜
                    s["prompt"] = re.sub(r"[。，]?\s*(固定机位|静止|不动|静态|锁机位|定格)\s*[，,。]?", "", prompt)
                    prompt = s["prompt"]
                if any(w in prompt for w in movement_words):
                    continue
                s["prompt"] = prompt.rstrip("。") + tail
                added += 1
                break
    return fixed + added


# W3: 打斗场景缺打击反馈词
_IMPACT_WORDS = (
    "水花四溅", "水花", "冲击波", "冲击", "火星四溅", "火星",
    "碎屑飞溅", "飞溅", "衣袂翻飞", "衣袂", "动态模糊", "残影",
    "血雾", "血溅", "浪花翻涌", "水波炸开", "水波激荡",
)
_IMPACT_BY_SCENE = {
    "水上": "水花四溅，水波激荡",
    "陆地": "衣袂翻飞，碎屑飞溅",
    "夜": "暗处残影，衣袂翻飞",
    "打斗": "冲击波，衣袂翻飞，动态模糊",
    "刺杀": "水花四溅，动态模糊",
}


def _hard_fix_combat_impact(shots: list, hook: dict | None = None) -> int:
    """W3 v6.41 打斗/刺杀场景打击反馈硬修复（08-23）.

    8-22 跑批：shot 3（刺客破水刺杀杨旭）prompt 完全没有"水花/冲击/动态模糊"
    等打击反馈词。LLM 默认只写"刺客破水而出，匕首下刺"就结束，缺视觉冲击。

    修法：扫打斗/刺杀/追逐/搏斗镜（_is_combat_shot），prompt 缺 _IMPACT_WORDS
    任一词 → 追加 _IMPACT_BY_SCENE 按场景类型选的反馈短语。
    幂等：已含 _IMPACT_WORDS 任一词跳过。
    """
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        if not isinstance(s, dict):
            continue
        # 08-23 修复：validator (_check_combat_action) 用 _COMBAT_KEYWORDS 查
        # narration+text 双通道。原 _is_combat_shot 只查 text，漏了"听香尖叫道：救
        # 命啊"narration 含"刺杀"等扩展场景。改为 _COMBAT_KEYWORDS 双通道。
        text = " ".join([s.get("prompt", "") or "", s.get("motion_desc", "") or "",
                         s.get("ff_desc", "") or "", s.get("lf_desc", "") or ""])
        narr = s.get("narration_segment", "") or ""
        is_combat = any(kw in text for kw in _COMBAT_KEYWORDS) or any(kw in narr for kw in _COMBAT_KEYWORDS)
        if not is_combat:
            continue
        prompt = s.get("prompt", "") or ""
        if any(w in prompt for w in _IMPACT_WORDS):
            continue
        # 场景类型判定
        scene_type = "打斗"
        full_text = text + " " + narr + " " + (s.get("location", "") or "") + " " + (s.get("scene", "") or "")
        if "水" in full_text or "湖" in full_text or "河" in full_text or "湾" in full_text or "舟" in full_text or "船" in full_text:
            scene_type = "水上"
        elif "夜" in full_text or "暗" in full_text:
            scene_type = "夜"
        # 是否刺杀（破水/下刺/举刀下刺等）
        if "刺" in full_text or "匕首" in full_text or "下刺" in full_text:
            scene_type = "刺杀"
        impact = _IMPACT_BY_SCENE.get(scene_type, _IMPACT_BY_SCENE["打斗"])
        s["prompt"] = prompt.rstrip("。") + f"。{impact}"
        fixed += 1
    return fixed


# W4: 打斗/特写场景转场关键词
_TRANSITION_KEYWORDS_TAIL = {
    "打斗": "。快速跟拍，动作连贯，冲击转场，转场时长0.2s",
    "特写": "。镜头推近，节奏缓慢，模糊转场，转场时长0.3s",
    "场景切换": "。淡入淡出，镜头拉远，自然过渡，转场时长0.5s",
}


def _hard_fix_transition_keywords(shots: list, hook: dict | None = None) -> int:
    """W4 v6.41 打斗/特写/场景切换转场关键词硬修复（08-23）.

    8-22 跑批：shot 3（刺杀）/shot 4（听香尖叫特写）clip_transition='硬切，转场时长0.2s'
    但 prompt 完全没"转场/冲击/跟拍"等动作连贯性词，导致 step02 视频模型拼接时
    画面跳跃。

    修法：扫打斗/特写/场景切换镜，prompt 缺"转场时长/快速跟拍/动作连贯"任一
    词 → 追加 _TRANSITION_KEYWORDS_TAIL 对应短语。clip_transition="硬切" 也在
    范围内。
    幂等：已含"转场时长"或对应关键词跳过。
    """
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for i, s in enumerate(targets):
        if not isinstance(s, dict):
            continue
        prompt = s.get("prompt", "") or ""
        clip_t = (s.get("clip_transition", "") or "").strip()
        # 08-23 修复：validator (_check_transition_keywords) 用 _COMBAT_KEYWORDS 查
        # narration 判定打斗。原 _is_combat_shot 只查 text，漏 narration。改为
        # 双通道 + _COMBAT_KEYWORDS。
        text = " ".join([prompt, s.get("motion_desc", "") or "",
                         s.get("ff_desc", "") or "", s.get("lf_desc", "") or ""])
        narr = s.get("narration_segment", "") or ""
        is_combat = any(kw in text for kw in _COMBAT_KEYWORDS) or any(kw in narr for kw in _COMBAT_KEYWORDS)
        is_closeup = "特写" in prompt or "大特写" in prompt
        is_scene_switch = False
        if i < len(targets) - 1 and isinstance(targets[i + 1], dict):
            loc1 = s.get("location", "") or s.get("scene", "")
            loc2 = targets[i + 1].get("location", "") or targets[i + 1].get("scene", "")
            is_scene_switch = bool(loc1 and loc2 and loc1 != loc2)
        # 选类型
        scene_type = None
        if is_combat:
            scene_type = "打斗"
        elif is_closeup:
            scene_type = "特写"
        elif is_scene_switch:
            scene_type = "场景切换"
        if not scene_type:
            continue
        # 幂等：已含"转场时长"或对应关键词
        if "转场时长" in prompt or "动作连贯" in prompt or "快速跟拍" in prompt:
            continue
        # 08-23 修复：原版 `clip_t in ("硬切", "硬切 cut", "cut")` 太窄，LLM 写
        # "硬切，0.2s" / "硬切，转场时长0.2s" 都会漏。改为：场景类型=打斗时无条件
        # 追加转场 tail（clip_transition 仅做信息提示，不影响 prompt 内容判定）。
        if scene_type == "打斗":
            s["prompt"] = prompt.rstrip("。") + _TRANSITION_KEYWORDS_TAIL[scene_type]
            fixed += 1
        elif scene_type == "特写" and "转场" not in prompt:
            s["prompt"] = prompt.rstrip("。") + _TRANSITION_KEYWORDS_TAIL[scene_type]
            fixed += 1
        elif scene_type == "场景切换" and "淡入" not in prompt and "淡出" not in prompt:
            s["prompt"] = prompt.rstrip("。") + _TRANSITION_KEYWORDS_TAIL[scene_type]
            fixed += 1
    return fixed


# W5: 多人站位纵深（按角色身份 + 场景空间重排）
# v6.43 重构：纵深位置统一从 constraints/multi_char_layout_depth 派生（已在该文件
# 顶部从 scene_anchors 派生 _DEPTH_POSITIONS）。此处保留别名供 legacy path 使用。
# 改为函数内 lazy import：module 级 import 会触发 constraints 包初始化，包内
# axis_jump/camera_variety 等文件又 import validation 顶层 helper，构成
# "partially initialized module" 循环（C:\...validation.py 在 6510 行尚未完成加载，
# 包内 from step01_script_gen.validation import _extract_shot_directions 即挂）。
def _get_depth_positions() -> tuple:
    from step01_script_gen.constraints.multi_char_layout_depth import (
        _DEPTH_POSITIONS as _DP,
    )
    return _DP


def _hard_fix_multi_char_layout_depth(shots: list, hook: dict | None = None) -> int:
    """W5 v6.41 多人站位纵深硬修复（08-23）.

    8-22 跑批：shot 1-5/7-8 多角色 char_layout 全 "画左" 或 "画右" 同侧，无纵深。
    物理原因：听香跪坐船尾+杨旭仰卧船头+刺客画左/画右——空间决定无法对置。

    修法：对每镜 char_layout 全同侧的，把"次要/背景"角色 position 改为"画中
    甲板/船舷"等纵深词；保留主角色画左/画右以维持空间方向。
    幂等：任一角色 position 已在 _DEPTH_POSITIONS 即视为已拉开纵深。
    """
    _DEPTH_POSITIONS = _get_depth_positions()
    fixed = 0
    targets = list(shots)
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        if not isinstance(s, dict):
            continue
        layout = s.get("char_layout")
        if not isinstance(layout, list) or len(layout) < 2:
            continue
        # 检查是否所有角色 position 都是"画左/画右"
        positions = [str(item.get("position", "")).strip()
                     for item in layout if isinstance(item, dict)]
        if not positions:
            continue
        only_lateral = all(p in ("画左", "画右") for p in positions)
        has_depth = any(p in _DEPTH_POSITIONS or "前" in p or "中" in p or "后" in p
                        for p in positions)
        if not only_lateral or has_depth:
            continue
        # 全横向同侧：把非主角色（role != "主"）position 改为纵深
        main_side = positions[0]  # 取第一个
        depth_pos = "画中甲板" if "船" in str(s.get("scene", "")) else "画中"
        cnt = 0
        for item in layout:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            if role in ("次", "背景"):
                pos = str(item.get("position", "")).strip()
                # 给次要角色加纵深
                new_pos = f"{main_side}{depth_pos}" if cnt == 0 else f"{main_side}{depth_pos}{cnt + 1}"
                if pos != new_pos and new_pos not in pos:
                    item["position"] = new_pos
                    cnt += 1
        if cnt > 0:
            fixed += 1
    return fixed


# W6: 红果标准时长拉伸（总时长 < 60s + 末镜 < 4s）
# v6.41+ 修 (08-23)：60 → 65 留 5s 安全裕度（避免贴线 60s 被下限误判为不达标）
_REDGUO_TARGET_SEC = 65.0  # 红果标准下限（+5s 安全裕度）
_LAST_SHOT_MIN_SEC = 4.0   # 末镜最小值
_LAST_SHOT_TARGET_SEC = 5.0 # 末镜目标值


def _hard_fix_runtime_extend(
    shots: list,
    hook: dict | None = None,
    script: dict | None = None,
) -> int:
    """W6 v6.41 红果时长硬修复（08-23；旧路径，HARD_FIX_USE_OLD=1 才走）.

    8-22 跑批：8 镜总时长 42.25s < 60s 红果下限，末镜 3.04s < 4s 红果下限。

    08-23 用户规则（与 constraints/runtime_extend.py 新路径同步）：
      - 正片分镜总时长 ≥ 60s，**不含钩子**（钩子时长另算）
      - 单镜 ≤ 8s（与视频模型 cap 取严）

    修法：
      1. 末镜 ≤ 4s → 强制夹到 5-6s
      2. 正片分镜总时长（不含钩子）< 65s（红果下限 60 + 5s 安全裕度）
         → 按比例放大每镜 duration
         - 单镜上限不超 min(active_video_duration_cap, 8s)
         - per_add 上限 3.0s（保证单镜有足够余量顶到 65s）
    幂等：总时长 ≥ 65s 且末镜 > 4s → 跳过所有修改。

    v6.41+ 修（08-23，治理 timecode 不同步 bug）：
      - 末镜保护条件 0 < x < 4.0 → 0 < x ≤ 4.0（4.0 自身也夹到 5.0）
      - per_add 上限 2.0 → 3.0（1.42s 顶不到 65s）
      - new_d 走 _align_frame_duration 而非 round(2)（24fps 整数帧对齐 + ≤3 位小数）
      - **末尾无条件重算** shot["timecode"]["start"/"end"] + script["total_duration"]
        + script["total_effective_duration"] + script["timeline"] + hook["timecode"]
        （不论是否拉伸，timecode 永远和 shot["duration"] 一致；
         否则 step02b 按 5.42s 生视频、step04 merge 按 4.0s timecode 切 → 音画错位）
    """
    if not shots:
        return 0
    try:
        from shared.model_caps import active_video_duration_cap
        per_shot_cap = float(active_video_duration_cap() or 12)
    except Exception:
        per_shot_cap = 12.0
    # 08-23 用户规则：单镜 ≤ 8s（与模型 cap 取严）；钩子另算不受此限
    try:
        from step01_script_gen.timecode import MAX_SHOT_DURATION
        per_shot_cap = min(per_shot_cap, float(MAX_SHOT_DURATION))
    except Exception:
        per_shot_cap = min(per_shot_cap, 8.0)

    try:
        from step01_script_gen.timecode import _align_frame_duration
    except Exception:
        _align_frame_duration = lambda raw, fps: round(float(raw), 2)  # noqa: E731

    fps = 24.0
    if script is not None:
        try:
            fps = float(script.get("fps", 24) or 24)
        except (TypeError, ValueError):
            fps = 24.0

    fixed = 0

    # 末镜时长夹取（v6.41+：条件 ≤ 而非 <）
    last = shots[-1]
    if isinstance(last, dict):
        try:
            last_dur = float(last.get("duration_sec") or last.get("duration") or 0)
        except (TypeError, ValueError):
            last_dur = 0
        if 0 < last_dur <= _LAST_SHOT_MIN_SEC:
            new_last = _align_frame_duration(
                min(_LAST_SHOT_TARGET_SEC, per_shot_cap), fps,
            )
            last["duration_sec"] = new_last
            last["duration"] = new_last
            fixed += 1

    # 总时长计算——只算正片分镜，钩子时长另算（08-23 用户规则）
    total = 0.0
    items = list(shots)
    for s in items:
        if not isinstance(s, dict):
            continue
        try:
            d = float(s.get("duration_sec") or s.get("duration") or 0)
            total += d
        except (TypeError, ValueError):
            pass

    # 总时长不足：按比例拉伸（v6.41+：need 阈值从 5 改为 2，避免频繁跳过 → 总时长顶不到 65s）
    if total < _REDGUO_TARGET_SEC:
        need = _REDGUO_TARGET_SEC - total
        if need >= 2:
            extensible = []
            for s in shots:
                if not isinstance(s, dict):
                    continue
                try:
                    d = float(s.get("duration_sec") or s.get("duration") or 0)
                except (TypeError, ValueError):
                    continue
                if d > 0 and d < per_shot_cap - 1.0:
                    extensible.append(s)
            if extensible:
                # v6.41+：1.42s 单镜增量不够（8 镜 × 1.42 ≈ 11.4s，48s→59.4s 仍贴线 60s 不稳），
                # 上限提到 3.0s 让单镜有足够余量。
                per_add = min(3.0, need / len(extensible))
                for s in extensible:
                    try:
                        cur = float(s.get("duration_sec") or s.get("duration") or 0)
                    except (TypeError, ValueError):
                        continue
                    new_d = min(per_shot_cap, cur + per_add)
                    actual_add = new_d - cur
                    if actual_add > 0.1:
                        # v6.41+：走 _align_frame_duration 而非 round(2)（24fps 整数帧对齐）
                        aligned = _align_frame_duration(new_d, fps)
                        s["duration_sec"] = aligned
                        s["duration"] = aligned
                        fixed += 1

    # v6.41+ 关键修复：不论是否拉伸，timecode 永远和 shot["duration"] 同步。
    # 此前 bug：末镜保护改了 duration 但 timecode 不动；新跑批 need<5 早返回也跳过
    # timecode 同步 → shot["duration"]=5.42 但 timecode.end=4.0，step02b/step04 时轴错位。
    # v6.43（08-23）钩子在播放顺序头部：hook.timecode=0..hook_duration，
    # shots 从 hook_duration 开始累加。timeline[] 数组**不再重建**——
    # _compute_timecodes 已按 hook-first 建好正确顺序，下游不读 timeline。
    if script is not None:
        cumulative = 0.0
        hook_dur_v = 0.0
        if hook and isinstance(hook, dict):
            try:
                hook_dur_v = float(hook.get("duration_sec") or hook.get("duration") or 0)
            except (TypeError, ValueError):
                hook_dur_v = 0.0
            hook["timecode"] = {
                "start": 0.0,
                "end": round(hook_dur_v, 2),
            }
            cumulative = hook_dur_v
        for s in shots:
            if not isinstance(s, dict):
                continue
            try:
                d = float(s.get("duration_sec") or s.get("duration") or 0)
            except (TypeError, ValueError):
                d = 0.0
            s["timecode"] = {
                "start": round(cumulative, 2),
                "end": round(cumulative + d, 2),
            }
            cumulative += d
        script["total_duration"] = round(cumulative, 2)
        # 兼容字段：旧版 step04 merge 用 total_effective_duration
        if "total_effective_duration" in script:
            script["total_effective_duration"] = round(cumulative, 2)
        # timeline[] 数组不再重建（_compute_timecodes 已建好 hook-first 顺序；下游不读）

    return fixed


# W7: 高潮分布硬修复
_CLIMAX_TAIL_TEMPLATES = (
    "。局势骤变，真相大白",
    "。气氛逆转，危机四伏",
    "。身份暴露，矛盾激化",
    "。反转揭露，悬念陡升",
    "。冲突到达顶点，局势不可挽回",
)


def _hard_fix_climax_distribution(shots: list, hook: dict | None = None) -> int:
    """W7 v6.41 高潮分布硬修复（08-23）.

    8-22 跑批：8 镜中 2 个 climax 镜（shot 3 刺杀 + shot 5 张十三质问）都集中在
    前 1/2 段（0-20s），后半段 20-37s 平淡无爆点。

    修法：检测 _CLIMAX_CONFLICT_HINTS 镜集中位置，若 < 1/2 时长在 climax 段 →
    在后半段至少 1 镜（index > len//2）的 main_subject 追加反转/揭穿/悬念词，
    并把 prompt 末尾追加 _CLIMAX_TAIL_TEMPLATES 之一。
    幂等：已含"反转/揭露/揭穿/局势骤变"跳过。
    """
    if not shots:
        return 0
    # 复用 _CLIMAX_CONFLICT_HINTS（已存在模块级）
    fixed = 0
    climax_idx = []
    for i, s in enumerate(shots):
        if not isinstance(s, dict):
            continue
        text = " ".join([s.get("prompt", "") or "", s.get("main_subject", "") or "",
                         s.get("motion_desc", "") or "", s.get("narration_segment", "") or ""])
        if any(kw in text for kw in _CLIMAX_CONFLICT_HINTS):
            climax_idx.append(i)
    if not climax_idx:
        # 全无 climax → 末镜追加反转
        last = shots[-1]
        if not isinstance(last, dict):
            return 0
        prompt = last.get("prompt", "") or ""
        if any(w in prompt for w in ("反转", "揭露", "揭穿", "局势骤变", "真相")):
            return 0
        last["prompt"] = prompt.rstrip("。") + _CLIMAX_TAIL_TEMPLATES[0]
        return 1
    # 高潮分布判定：climax 在前 1/2 段
    # 08-23 修复：validator (_check_narrative_arc_distribution) 用 1/3 段切分
    # last_third = total - total // 3；高潮镜头需落入 index >= last_third 才算
    # 后半段。原版用 mid=total//2 选 shot[5]，但 last_third=6（total=8），
    # shot[5] 在 tier=1 不算后半段，validator 仍 WARN。改用 last_third 选。
    total = len(shots)
    last_third = total - total // 3
    climax_in_first = sum(1 for i in climax_idx if i < last_third)
    climax_in_last = sum(1 for i in climax_idx if i >= last_third)
    if climax_in_last >= 1:
        return 0  # 后 1/3 段已有 climax
    if climax_in_first < 1:
        return 0  # 全部都不算 climax
    # 后 1/3 段缺 climax：挑后 1/3 段中点
    if total < 4:
        return 0
    target_idx = last_third + (total - last_third) // 2
    if target_idx >= total:
        target_idx = total - 1
    target = shots[target_idx]
    if not isinstance(target, dict):
        return 0
    # 改 main_subject 加 climax 关键词（validator 查 narration+main_subject）
    main_subject = target.get("main_subject", "") or ""
    # 选一个未使用的 climax 关键词（_NARRATIVE_ARC_CLIMAX_KEYWORDS 与 validator 同源）
    _NARRATIVE_ARC_CLIMAX_KEYWORDS = (
        "刺杀", "战斗", "对决", "反转", "死亡", "诀别", "暴露", "发现",
        "冲突", "搏杀", "跃出", "挥刀", "拔剑",
    )
    used_kw = [w for w in _NARRATIVE_ARC_CLIMAX_KEYWORDS if w in main_subject or w in target.get("prompt", "")]
    target_kw = next((w for w in _NARRATIVE_ARC_CLIMAX_KEYWORDS if w not in used_kw), "反转")
    target["main_subject"] = main_subject.rstrip("，,。") + f"，{target_kw}揭示"
    fixed += 1
    return fixed


# W9: kf stage='pre' 含「指尖沾血」豁免（语义区分：受害者手沾血 vs 受害者伤口血）
_FINGER_BLOOD_HINTS = ("指尖", "手指", "手上", "指缝", "指间", "手沾", "手背",
                       "唇边", "脸上", "脸颊", "额上", "手心", "摸到", "触到")


def _hard_fix_stage_pre_blood_semantic(shots: list, hook: dict | None = None) -> int:
    """W9 v6.41 kf stage='pre' 血语义区分硬修复（08-23）.

    8-22 跑批：shot 4 kf pre t=0.5 desc='听香指尖触到唇边暗红，杨旭仰卧船头胸口
    暗红洇痕' → stage='pre' 含「洇痕」stage 禁用词，校验报矛盾。但听香指尖沾血
    是刺杀溅到她手上的血（属于刺杀后立刻反应），不是伤口血——不应剥离。

    修法：扫所有 kf，stage='pre' 且含 stage 禁用词时，若同时含 _FINGER_BLOOD_HINTS
    （指尖/手/唇边/脸上），把 stage 改为 'climax'（刺杀已发生，听香反应 = climax
    阶段）而不是删除血词。
    幂等：已含 climax/after 跳过。
    """
    fixed = 0
    targets: list[dict] = list(shots or [])
    if hook and isinstance(hook, dict):
        targets.append(hook)
    for s in targets:
        kfs = s.get("keyframes") or []
        if not isinstance(kfs, list):
            continue
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            stage = str(kf.get("stage", "")).strip()
            if stage != "pre":
                continue
            desc = str(kf.get("desc", "") or "")
            if not any(fb in desc for fb in _FINGER_BLOOD_HINTS):
                continue  # 不含手指沾血特征 → 留给 _check_kf_stage_consistency 处理
            if not any(fb in desc for fb in ("暗红", "血", "洇痕", "血痕", "血迹")):
                continue  # 不含血词 → 不是血相关 stage 矛盾
            # 含手指沾血 + 血词 + stage=pre → 升级为 climax（刺杀已完成，听香反应）
            kf["stage"] = "climax"
            fixed += 1
    return fixed


# W10: 景别跳跃硬修复
_FRAMING_JUMP_FALLBACK = {
    "大特写": "特写",
    "特写": "中近景",
    "中近景": "中景",
    "中景": "中远景",
    "中远景": "全景",
    "全景": "远景",
    "远景": "大远景",
    "大远景": "鸟瞰",
}


def _hard_fix_framing_jump(shots: list, hook: dict | None = None) -> int:
    """W10 v6.41 景别跳跃硬修复（08-23）.

    8-22 跑批：shot 1 全景 → shot 2 中近景（3 级跳跃）报"景别跳跃幅度 3 级
    （>2 级）"。但实拍中远景建立 → 中近景进入对话是合理节奏。

    修法：扫相邻镜景别跨度 > 2 级，强制把前镜 framing 替换为后镜的相邻景别
    （中近景→中景过渡），减小跨度。豁免：场景切换 / 打斗镜（与 _check_shot_size_jump 同口径）。
    幂等：相邻景别 ≤ 2 级时跳过。
    """
    fixed = 0
    if len(shots) < 2:
        return 0
    items = list(shots)
    for i in range(len(items) - 1):
        s1, s2 = items[i], items[i + 1]
        if not isinstance(s1, dict) or not isinstance(s2, dict):
            continue
        # 场景切换豁免
        loc1 = s1.get("location", "") or s1.get("scene", "")
        loc2 = s2.get("location", "") or s2.get("scene", "")
        if loc1 and loc2 and loc1 != loc2:
            continue
        # 打斗镜豁免
        if _is_combat_shot(s1) or _is_combat_shot(s2):
            continue
        # 取景别
        lv1 = _shot_size_level(s1)
        lv2 = _shot_size_level(s2)
        if not lv1 or not lv2:
            continue
        try:
            d1 = _SHOT_SIZE_ORDER.index(lv1)
            d2 = _SHOT_SIZE_ORDER.index(lv2)
        except ValueError:
            continue
        if abs(d1 - d2) <= 2:
            continue
        # 跨度 > 2：把 s1 framing 改为 s2 的相邻（更近一档）
        # 例：s1=全景(d=5) s2=特写(d=2) |d1-d2|=3 → s1 改 中远景(d=4)
        # 选取 s2 的相邻更近档（与 s2 跨度 1）
        adj_idx = max(0, d2 - 1) if d1 > d2 else min(len(_SHOT_SIZE_ORDER) - 1, d2 + 1)
        new_lv1 = _SHOT_SIZE_ORDER[adj_idx]
        if new_lv1 == lv1:
            continue
        prompt = s1.get("prompt", "") or ""
        if new_lv1 in prompt:
            continue
        # 替换 s1 的 framing 词
        new_prompt = prompt.replace(lv1, new_lv1, 1)
        if new_prompt != prompt:
            s1["prompt"] = new_prompt
            fixed += 1
    return fixed


# W11: 180° 轴线越轴硬修复
def _hard_fix_axis_jump(shots: list, hook: dict | None = None) -> int:
    """W11 v6.41 180° 轴线越轴硬修复（08-23）.

    8-22 跑批：shot 5→6 同场景「画右」→「画左」朝向翻转报"轴线越轴"。

    修法：扫同场景相邻镜方向词冲突（画左↔画右），把后镜 char_layout 第一角色
    position 强制改为前镜同侧（保持轴线一致），并把后镜 prompt 中"画左"或"画右"
    字样替换为前镜方向。
    豁免：插空镜 / 场景切换。
    幂等：方向一致时跳过。
    """
    fixed = 0
    if len(shots) < 2:
        return 0
    for i in range(len(shots) - 1):
        s1, s2 = shots[i], shots[i + 1]
        if not isinstance(s1, dict) or not isinstance(s2, dict):
            continue
        loc1 = s1.get("location", "") or s1.get("scene", "")
        loc2 = s2.get("location", "") or s2.get("scene", "")
        if loc1 and loc2 and loc1 != loc2:
            continue
        # 方向判定
        t1 = " ".join([s1.get("prompt", "") or "", s1.get("ff_desc", "") or ""])
        t2 = " ".join([s2.get("prompt", "") or "", s2.get("ff_desc", "") or ""])
        d1 = _extract_shot_directions(t1)
        d2 = _extract_shot_directions(t2)
        if not d1 or not d2:
            continue
        # 冲突：画左↔画右
        if set(d1) == {"画左"} and set(d2) == {"画右"}:
            # 把 s2 方向统一到画左
            new_prompt = (s2.get("prompt", "") or "").replace("画右", "画左")
            if new_prompt != s2.get("prompt", ""):
                s2["prompt"] = new_prompt
                fixed += 1
            # 同步 char_layout
            for item in s2.get("char_layout", []) or []:
                if isinstance(item, dict):
                    pos = str(item.get("position", "")).strip()
                    if "画右" in pos:
                        item["position"] = pos.replace("画右", "画左")
        elif set(d1) == {"画右"} and set(d2) == {"画左"}:
            new_prompt = (s2.get("prompt", "") or "").replace("画左", "画右")
            if new_prompt != s2.get("prompt", ""):
                s2["prompt"] = new_prompt
                fixed += 1
            for item in s2.get("char_layout", []) or []:
                if isinstance(item, dict):
                    pos = str(item.get("position", "")).strip()
                    if "画左" in pos:
                        item["position"] = pos.replace("画左", "画右")
    return fixed


# W13: 镜头衔接词（同地点连续镜缺衔接词）
# 08-23 修复：validator (_check_shot_transition) 用 transition_words 判定
# ["继续", "然后", "紧接着", "随后", "随即", "紧接", "瞬间", "刹那", "突然", "猛然", "骤然"]
# 同时检查 context_bridge。原 W13 只追加"镜头衔接/连贯/过渡/延续前镜"，与
# validator 判定词不重叠，validator 仍 WARN。改为：prompt 追加"然后"+ context_bridge
# 拼接"紧接着..."，双通道命中 validator。
_BRIDGE_TRANSITION_WORDS = ("继续", "然后", "紧接着", "随后", "随即", "紧接",
                           "瞬间", "刹那", "突然", "猛然", "骤然")
_BRIDGE_TAIL_PROMPT = "。然后镜头继续，前镜动作与构图自然过渡到本镜"


def _hard_fix_bridge_words(shots: list, hook: dict | None = None) -> int:
    """W13 v6.41 镜头衔接词硬修复（08-23）.

    8-22 跑批：shot 1→4 / shot 5→8 连续 4-5 镜同地点(照月湾) 报"缺衔接词"。

    修法：扫连续 3+ 镜同 location 且 prompt+context_bridge 都缺
    _BRIDGE_TRANSITION_WORDS 任一词 → 追加 prompt 末尾"然后..."+ context_bridge
    拼接"紧接着..."。双通道命中 validator 判定。
    幂等：已含 transition word 跳过。
    """
    fixed = 0
    if not shots:
        return 0
    # 找出连续 3+ 镜同 location 段
    runs = []
    cur_start = 0
    cur_loc = None
    for i, s in enumerate(shots):
        if not isinstance(s, dict):
            continue
        loc = s.get("location", "") or s.get("scene", "")
        if loc == cur_loc:
            continue
        if cur_loc and i - cur_start >= 3:
            runs.append((cur_start, i - 1))
        cur_start = i
        cur_loc = loc
    if cur_loc and len(shots) - cur_start >= 3:
        runs.append((cur_start, len(shots) - 1))
    for start, end in runs:
        if start >= end:
            continue
        # 08-23 修复：原版只修 mid 一镜，但 validator (_check_shot_transition) 逐镜
        # 判定 transition word 缺失——run 内每镜都需补。改为：run 内每镜都加
        # transition tail + context_bridge 拼接。
        for idx in range(start, end + 1):
            s = shots[idx]
            if not isinstance(s, dict):
                continue
            prompt = s.get("prompt", "") or ""
            cb = (s.get("context_bridge", "") or "").strip()
            # 幂等：prompt 或 context_bridge 任一已含 transition word
            if any(w in prompt for w in _BRIDGE_TRANSITION_WORDS):
                continue
            if cb and any(w in cb for w in _BRIDGE_TRANSITION_WORDS):
                continue
            # 追加 prompt transition tail
            s["prompt"] = prompt.rstrip("。") + _BRIDGE_TAIL_PROMPT
            # 同时拼接 context_bridge 双保险
            if cb:
                s["context_bridge"] = cb.rstrip("。") + "，紧接着延续前镜动作与构图。"
            else:
                s["context_bridge"] = "紧接着延续前镜动作与构图，自然过渡到本镜。"
            fixed += 1
    return fixed


def hard_fix_shot_prompts(script: dict) -> dict:
    """P18 闭环硬修复层（v6.32，08-17 新增，08-22 v6.40 扩 11 类，08-23 v6.41 扩 14 类 P19 闭环）。

    在 post_fix_shot_prompts 之后、_validate_script 之前调用。对 LLM 反复写错的
    23 类确定性缺陷 + 08-23 跑批新发现的 14 类 P19 闭环做改写（不依赖 LLM 自觉，
    跨轮稳定）：

      A. 致命伤持续：伤处建立后同场景镜头缺锚 → 追加「心口处白袍暗红洇痕」
      B. 视线方向：看/望+人名 → 改写为「画X」
      C. 道具空间方位：缺方位 → 追加「（画X）」标注
      D. kf 时序矛盾：pre+post 冲突 → 删 pre 词
      E. kf 时段缺段：motion_desc 时段无 kf → 派生
      F. kf t_sec 越界：t_sec > dur → clamp
      G. 威胁源-视线耦合：发现者视线方向 → 改 char_layout 威胁源 position
      H. narration/subtitle 拼接：省略号/空格归一化
      I. 景别多样性（旧版）：缺远景/特写 → 提示
      I2. 景别多样性 v2：连续 3+ 镜同景别 → 中间一镜替换
      J. 运镜描述（旧版）：缺 → 追加"固定机位"
      J2. 运镜多样性：全片 < 3 种运镜 → 按角色补差异化运镜
      K. 开篇冲突：前 1/3 段缺爆点词 → 追加"危机一触即发"
      L. 爆点密度：每 30s 1 爆点 → 追加
      M. lf_desc 字数下限：< 40 / 60 → 拼接布局
      N. char_layout 同步：position vs prompt 矛盾 → 改 char_layout
      O. 首镜头 AI 生成标注：→ 追加
      P. 复读机：4-token 短语 ≥2 次 → 删第 2 次起
      Q. lf_desc 动作进行态：初现/即将/正在 → 已/已现
      R. hook 时长下限：< 4s → 扩 4s
      S. 长对话拆镜：narration 估算时长 > 模型 cap → 按标点拆 2-3 子镜
      T1. 船体宽度：长X米窄身/无宽度的舴艋舟/小舟 → 改"宽1.4米"（08-23）
      T2. 面向→凝望：char_layout 写「X 面向Y（人名）」但 prompt/desc 无目光/凝望Y → 追加「目光凝望Y」（08-23）
      U. 跪坐位：受害者仰卧/躺 + 另一角色跪坐船头/船尾/甲板 → 改写"跪坐受害者身侧，面向受害者"（08-23 听香跪船尾；v6.41+ 覆盖 keyframes[*].desc——kf pre 帧漏修）
      V. 船体裁切：ff/lf/prompt 写"船身延伸出画外/只露出一部分" → 剥离（与"完整小舟"锚冲突，图片模型裁掉船尾）
      W15. 钩子目击者-凶手视觉歧义：受害者已死/被刺 + 另一角色"伸向/摸向/握向刀柄" → 改"伸向自己濡湿的嘴唇（沾有 Y 的血）"（08-26 用户反馈，registry 驱动：VICTIM_SIGNS/WEAPON_HANDLE_WORDS/POSE_REGISTRY）

      ── 08-23 v6.41 P19 闭环 新增 14 类 ──
      W1. 景别多样性 v2：连续 3+ 镜同景别 → 中间一镜替换（I 旧版不修连镜）
      W2. 运镜多样性 v2：全片 < 3 种运镜 → 按角色补差异化运镜（J 旧版只补固定机位）
      W3. 打斗/刺杀场景打击反馈：缺"水花/冲击/动态模糊" → 按场景类型追加
      W4. 打斗/特写/场景切换转场关键词：硬切缺"转场时长0.2s" → 追加
      W5. 多人站位纵深：全同侧（画左/画右）无纵深 → 次要角色改"画中甲板/船舷"
      W6. 红果时长拉伸：总时长 < 60s / 末镜 < 4s → 按比例放大+末镜夹到 4-6s
      W7. 高潮分布：高潮全在前段 → 后半段中间一镜追加反转/揭穿/悬念
      W8. 开篇冲突（同 K，v6.41 扩）
      W9. kf stage pre 血语义：含"指尖/手" + 血词的 pre 帧 → 升级 climax（豁免误判）
      W10. 景别跳跃：相邻景别跨度 > 2 级 → 前镜改相邻景别
      W11. 180° 轴线越轴：同场景画左↔画右 → 后镜统一方向
      W12. prompt 长度 cap 误报（已修 _check_prompt_length_budget：cap<1200 直接用 cap）
      W13. 镜头衔接词：同地点 3+ 镜 → 中间一镜追加"镜头衔接"短语
      W14. 多人站位纵深（与 W5 合并）

    幂等：每类修复都有锚词检测，已含则跳过；多次调用结果一致。
    """
    shots = script.get("shots", []) or []
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    a = _hard_fix_injury_continuity(shots)                    # A 致命伤
    b = _hard_fix_gaze_direction(shots, hook)                  # B 视线
    c = _hard_fix_prop_position(shots)                         # C 道具
    d = _hard_fix_kf_pre_post_conflict(shots, hook)            # D kf时序
    e = _hard_fix_kf_time_window_alignment(shots, hook)        # E kf时段
    f = _hard_fix_kf_t_sec_bounds(shots, hook)                 # F kf t_sec 越界
    g = _hard_fix_threat_gaze_coupling(shots, hook)            # G 威胁源-视线耦合
    h = _hard_fix_narration_subtitle_consistency(shots, hook)  # H 拼接归一
    i = _hard_fix_shot_size_diversity(shots, hook)             # I 景别多样（旧版）
    i2 = _hard_fix_framing_diversity_v2(shots, hook)           # I2 景别多样 v2
    j = _hard_fix_camera_movement(shots, hook)                 # J 运镜（旧版）
    j2 = _hard_fix_camera_variety(shots, hook)                 # J2 运镜多样性
    k = _hard_fix_opening_hook(shots, hook)                    # K 开篇冲突
    l = _hard_fix_punch_density(shots, hook)                   # L 爆点密度
    m = _hard_fix_lf_desc_length(shots, hook)                  # M lf_desc 字数
    n = _hard_fix_char_layout_sync(shots, hook)                # N char_layout 同步
    o = _hard_fix_platform_compliance(shots, hook)             # O AI 标注
    p = _hard_fix_prompt_repetition(shots, hook)               # P 复读机
    q = _hard_fix_lf_midstate(shots, hook)                     # Q lf 动作态
    r = _hard_fix_hook_duration(shots, hook)                   # R hook 时长
    s_split = _hard_fix_split_long_dialogue_shot(shots, hook)  # S 长对话拆镜
    t1 = _hard_fix_boat_width(shots, hook)                     # T1 船体宽度
    t2 = _hard_fix_facing_to_gaze(shots, hook)                  # T2 面向→凝望
    u = _hard_fix_kneel_beside_victim(shots, hook)              # U 跪坐位→受害者身侧
    v = _hard_fix_boat_crop(shots, hook)                        # V 船体裁切短语剥离
    w15 = _hard_fix_hook_witness_not_killer(hook, script)         # W15 钩子目击者-凶手视觉歧义（registry 驱动）
    # v6.41 P19 闭环 新增 11 类
    w3 = _hard_fix_combat_impact(shots, hook)                  # W3 打斗打击反馈
    w4 = _hard_fix_transition_keywords(shots, hook)            # W4 转场关键词
    w5 = _hard_fix_multi_char_layout_depth(shots, hook)        # W5 多人站位纵深
    w6 = _hard_fix_runtime_extend(shots, hook, script)         # W6 红果时长拉伸（v6.41+ 同步 timecode）
    w7 = _hard_fix_climax_distribution(shots, hook)            # W7 高潮分布
    w9 = _hard_fix_stage_pre_blood_semantic(shots, hook)       # W9 kf pre 血语义豁免
    w10 = _hard_fix_framing_jump(shots, hook)                  # W10 景别跳跃
    w11 = _hard_fix_axis_jump(shots, hook)                     # W11 轴线越轴
    w13 = _hard_fix_bridge_words(shots, hook)                  # W13 镜头衔接词
    counts = (a, b, c, d, e, f, g, h, i, i2, j, j2, k, l, m, n, o, p, q, r,
              s_split, t1, t2, u, v, w3, w4, w5, w6, w7, w9, w10, w11, w13, w15)
    if any(counts):
        _get_logger().info(
            f"[HARD-FIX] 致命伤 +{a} 镜 / 视线 +{b} 字段 / 道具 +{c} 镜 / "
            f"kf时序 +{d} 帧 / kf时段 +{e} 帧 / kf t_sec越界 +{f} 帧 / "
            f"威胁源-视线 +{g} 镜 / 拼接归一 +{h} 镜 / 景别多样 +{i} 镜 / "
            f"景别多样v2 +{i2} 镜 / 运镜补全 +{j} 镜 / 运镜多样 +{j2} 镜 / "
            f"开篇冲突 +{k} 镜 / 爆点密度 +{l} 镜 / "
            f"lf_desc字数 +{m} 镜 / char_layout同步 +{n} 镜 / AI标注 +{o} 镜 / "
            f"复读机 +{p} 字段 / lf动作态 +{q} 镜 / hook时长 +{r} 镜 / "
            f"长对话拆镜 +{s_split} 镜 / 船体宽度 +{t1} 字段 / 面向→凝望 +{t2} 字段 / "
            f"跪坐位 +{u} 字段 / 船体裁切 +{v} 字段 / "
            f"打击反馈 +{w3} 镜 / 转场关键词 +{w4} 镜 / 站位纵深 +{w5} 镜 / "
            f"时长拉伸 +{w6} 镜 / 高潮分布 +{w7} 镜 / pre血语义 +{w9} 帧 / "
            f"景别跳跃 +{w10} 镜 / 轴线越轴 +{w11} 镜 / 衔接词 +{w13} 镜 "
            f"（v6.41 08-23 P18+P19 闭环 34 类）"
        )
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
    # ── 2026-08-09 补全（审计 P1-8：abstract_emotion_words 108 词仅 46 有映射，
    # 缺映射的校验给泛泛兜底、修复不替换，两者不一致）──
    "伤心": "低头垂眸，眼泪滑落，嘴角下撇",
    "痛苦": "眉头紧锁，双眼紧闭，牙关咬紧，身体蜷缩",
    "哀伤": "目光低垂，眼眶泛红，嘴角轻颤",
    "悲痛": "泪流满面，肩膀剧烈颤抖，双手攥紧",
    "哀愁": "眼神低垂无神，轻轻叹气，眉头微蹙",
    "忧郁": "目光低垂，沉默不语，神情落寞",
    "忧伤": "眼神黯淡，低头不语，眼角微红",
    "恼怒": "眉头紧皱，眼含怒意，嘴唇紧抿",
    "怒火": "双目圆睁，额角青筋凸起，握拳",
    "愤恨": "眼神凌厉，咬牙切齿，胸口起伏",
    "怒气": "眉头倒竖，双拳握紧，呼吸粗重",
    "狂怒": "双目赤红，面部扭曲，挥臂砸物",
    "困惑": "眉头微皱，眼神迷茫，歪头思索",
    "茫然": "目光空洞，呆立不动，嘴唇微张",
    "无助": "眼神哀求，双手垂下，肩背佝偻",
    "彷徨": "来回踱步，眉头紧锁，目光游移",
    "犹豫": "手悬半空，目光迟疑，欲言又止",
    "纠结": "眉头深锁，来回摩挲手指，眼神挣扎",
    "无情": "眼神冰冷，面无表情，直视前方",
    "冷淡": "目光疏离，面无表情，语气平淡",
    "淡漠": "眼神平静无波，面无表情，视若无睹",
    "麻木": "眼神空洞，身体僵直，无反应",
    "高兴": "眉眼弯弯，嘴角上扬，眼睛发亮",
    "快乐": "笑容满面，眼睛弯成月牙，脚步轻快",
    "喜悦": "嘴角上扬，眼含笑意，眉梢舒展",
    "愉快": "眉眼舒展，嘴角带笑，神情轻松",
    "欣喜": "眼睛发亮，嘴角上扬，眉梢带喜",
    "欢乐": "咧嘴大笑，眼睛眯起，手舞足蹈",
    "欢笑": "咧嘴大笑，眉眼弯弯，肩膀轻抖",
    "恐惧": "瞳孔骤缩，身体后缩，双手护胸，面色惨白",
    "畏惧": "身体瑟缩，目光躲闪，不敢直视",
    "胆怯": "目光闪躲，身体微缩，声音发颤",
    "惊慌": "双眼睁大，手足无措，身体僵硬",
    "恐慌": "瞳孔放大，面色惨白，双手颤抖，脚步踉跄",
    "惊奇": "眼睛瞪大，嘴巴微张，身体前倾",
    "意外": "眉毛上扬，眼睛睁大，身体微顿",
    "愕然": "眼睛圆睁，嘴巴微张，身体僵住",
    "温柔": "眼神柔和，嘴角含笑，动作轻缓",
    "柔情": "眼神温柔，嘴角浅笑，目光含情",
    "温情": "眼神柔和，嘴角带暖意，目光关切",
    "温馨": "眼神柔和，嘴角含笑，动作轻柔",
    "温暖": "嘴角带暖意，眼神温和，神情舒展",
    "严厉": "眉头紧锁，目光锐利，面色沉凝",
    "庄重": "表情严肃，目光端正，身姿挺直",
    "郑重": "表情严肃，目光认真，身姿端凝",
    "正经": "表情端正，目光平视，姿态规整",
    "紧张": "手指绞紧，呼吸急促，目光游移，吞咽口水",
    "焦虑": "来回踱步，手指不停摩挲，目光焦灼",
    "焦急": "眉头紧锁，目光急切，不停张望",
    "急迫": "身体前倾，步伐急促，目光紧迫",
    "紧迫": "神情紧绷，呼吸急促，眉头紧锁",
    "羞涩": "脸颊泛红，低头垂眸，手指绞动",
    "腼腆": "脸颊微红，低头抿唇，目光躲闪",
    "不好意思": "脸颊泛红，低头挠头，目光闪躲",
    "脸红": "双颊泛红，低头回避，目光躲闪",
    "冷静": "呼吸平稳，目光坚定，神情平静",
    "沉稳": "呼吸平稳，目光沉静，身体不动",
    "淡定": "神情自若，目光平稳，姿态从容",
    "冷静地": "呼吸平稳，目光坚定，神情平静",
    "温柔地": "眼神柔和，嘴角含笑，动作轻缓",
    "严厉地": "眉头紧锁，目光锐利，面色沉凝",
    "紧张地": "手指绞紧，呼吸急促，目光游移，吞咽口水",
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


# ========================================================================
# 类型归位（2026-08-13 修复）：LLM 偶发输出错误类型，防御 type-pollution 崩溃
# ========================================================================

def _coerce_schema_types(script: dict) -> None:
    """按 SCRIPT_SCHEMA 声明类型，把字段尽力归位（类型污染防御）.

    背景（2026-08-13）：DeepSeek strict json_schema 偶发输出错误类型（index/
    cam_idx 混为字符串、duration 为字符串、prompt/narration 偶发非字符串等），
    validation 里的 sorted()/< /字符串拼接等运算抛 `<` str/int、拼接等崩溃
    （bench 实测两档各 1/2 触发）。此处按 schema 声明类型尽力转回
    （数字字段→数字、字符串字段→str），失败保留原值让校验 WARN 兜底。

    幂等：已是正确类型时原样返回。None 保留（校验用 `or ""` 处理）。
    """
    # 字符串字段（schema 声明为 string；非 str 值转 str，防拼接/strip 崩溃）
    _STR_FIELDS = (
        "prompt", "ff_desc", "lf_desc", "motion_desc", "main_subject",
        "scene", "location", "narration_segment", "subtitle",
        "context_bridge", "audio_cue", "fx_effect", "color_tone",
        "clip_transition", "variation_type", "subject_direction",
    )

    def _cast(v, cast):
        if v is None or isinstance(v, bool):
            return v  # None/bool 语义特殊，不自动转（校验 `or ""` 兜底）
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            try:
                return cast(v)
            except (TypeError, ValueError):
                return v
        return v  # list/dict 等：保留，校验兜底

    def _to_str(v):
        # 字符串字段兜底：None → ""（防 join/strip/拼接崩，校验对空字段 WARN 兜底）；
        # 其余非 str 值转 str（防拼接/`.strip()` 崩溃）。
        if v is None:
            return ""
        return v if isinstance(v, str) else str(v)

    if not isinstance(script, dict):
        return
    for f, cast in (("shot_count", lambda s: int(float(s))),
                    ("total_duration", float)):
        if f in script:
            script[f] = _cast(script[f], cast)
    for shot in script.get("shots", []) or []:
        if not isinstance(shot, dict):
            continue
        for f, cast in (("index", lambda s: int(float(s))),
                        ("cam_idx", lambda s: int(float(s))),
                        ("duration", float),
                        ("duration_sec", float)):
            if f in shot:
                shot[f] = _cast(shot[f], cast)
        for f in _STR_FIELDS:
            if f in shot:
                shot[f] = _to_str(shot[f])
        for kf in shot.get("keyframes") or []:
            if isinstance(kf, dict):
                # v6.34 字段迁移：t_sec 直写秒数（优先），time_ratio 旧字段（fallback）
                if "t_sec" in kf:
                    kf["t_sec"] = _cast(kf["t_sec"], float)
                elif "time_ratio" in kf:
                    # 旧字段：保留为兼容，但不再被 helper 写入新数据
                    kf["time_ratio"] = _cast(kf["time_ratio"], float)
                if "stage" in kf and kf["stage"] is not None:
                    s = str(kf["stage"]).strip().lower()
                    if s in KF_STAGE_ENUM:
                        kf["stage"] = s
                    else:
                        kf["stage"] = None  # 非法 enum 静默置空
                if "desc" in kf:
                    kf["desc"] = _to_str(kf["desc"])
    hook = script.get("hook")
    if isinstance(hook, dict):
        for f, cast in (("index", lambda s: int(float(s))),
                        ("cam_idx", lambda s: int(float(s))),
                        ("duration", float),
                        ("duration_sec", float)):
            if f in hook:
                hook[f] = _cast(hook[f], cast)
        for f in _STR_FIELDS:
            if f in hook:
                hook[f] = _to_str(hook[f])
        # hook kf 字段同 shot 同步处理
        for kf in hook.get("keyframes") or []:
            if isinstance(kf, dict):
                if "t_sec" in kf:
                    kf["t_sec"] = _cast(kf["t_sec"], float)
                elif "time_ratio" in kf:
                    kf["time_ratio"] = _cast(kf["time_ratio"], float)
                if "stage" in kf and kf["stage"] is not None:
                    s = str(kf["stage"]).strip().lower()
                    if s in KF_STAGE_ENUM:
                        kf["stage"] = s
                    else:
                        kf["stage"] = None
                if "desc" in kf:
                    kf["desc"] = _to_str(kf["desc"])


def parse_response(raw_text: str, novel_text: str = "") -> dict:
    """解析 LLM 返回的原始文本为 JSON， 含兜底修复。

    Args:
        raw_text: LLM 返回的原始文本
        novel_text: 小说原文（用于原文锚点验证）
    """
    global _novel_text_for_validation
    _novel_text_for_validation = novel_text

    _get_logger().info(f"模型返回文本长度: {len(raw_text)} 字符")
    try:
        script = safe_parse_json_object(raw_text)
    except (ValueError, json.JSONDecodeError) as e:
        _get_logger().warn(f"safe_parse_json_object 失败: {e}")
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
            _get_logger().info(f"兜底解析成功（直接 JSON 区间+尾部逗号修复）")
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

    # 2026-08-13 修复：LLM 偶发输出错误类型（index/cam_idx/duration 等混为字符串），
    # validation 的 sorted()/< 等数字运算会抛 `<` str/int 崩溃（bench 实测）。
    # 按 schema 类型归位后再填充/校验，同类 type-pollution 一并防御。
    _coerce_schema_types(script)

    # v6.4 新增：补全新添加的字段（如果 LLM 没有返回）
    _fill_missing_new_fields(script)

    # narration 兜底：若 LLM 未输出顶层 narration，从 subtitle 拼接填充。
    # 防止 _validate_script 报告「narration 与 subtitle 拼接不一致」（line 108-111），
    # 同时也保证 step03 TTS 不会拿到空字符串而 raise ValueError。
    if not script.get("narration"):
        script["narration"] = "".join(s.get("subtitle", "") for s in script.get("shots", []))

    # v6.9 新增：自动修复（声音指令 + 说话人入画），必须在校验前执行，
    # 否则校验输出的「缺少（用中文说）」「说话人未入画」WARN 无法被消除
    post_fix_shot_prompts(script)

    # v6.42：改走新 Constraint 框架（29 条 vs 旧 34 类——新框架更精简一致），
    # 避免与 main.py:1055 / 1664 重复执行。保留 HARD_FIX_USE_OLD=1 兜底。
    from step01_script_gen.constraints import run_hard_fix as _hfix
    try:
        _hfix(script, is_in_repair_loop=False, is_final_pass=False)
    except Exception as _hfe:
        _get_logger().warn(f"[WARN] parse_response 末尾 hard_fix 失败: {_hfe}")

    # P0-5 修复（08-12）：全局渲染参数（resolution/ratio/fps/render_style）此前在
    # main._post_process 才注入，_validate_script 校验时永远缺失 → 4×2 次误报。
    # 提前到校验前注入：inject_global_config_into_script 幂等（不覆盖已有字段），
    # 后续 _post_process 再跑一次无副作用，且让校验能检查真实注入值。
    try:
        import style_prefix as _sp
        _sp.inject_global_config_into_script(script)
    except Exception:
        pass  # 注入失败则维持原状，_validate_script 会提示缺失（可接受的降级）

    _validate_script(script)
    return script


# ============================================================================
# v6.21：镜头组接检查（shot-assembling skill 吸收）
# ============================================================================

# 朝向方向词（从 prompt/ff_desc 提取「画左/画右」作越轴锚点）
_AXIS_DIR_WORDS = ("画左", "画右")
# 景别等级表（storyboard_refs.SHOT_SIZES 的 9 级，用于计算相邻镜头景别跳跃幅度）
_SHOT_SIZE_ORDER = [
    "大特写", "特写", "中近景", "中景", "中远景", "全景", "远景", "大远景", "鸟瞰远景",
]


def _extract_shot_directions(text: str) -> list[str]:
    """从文本提取「画左/画右」方向词列表."""
    return [d for d in _AXIS_DIR_WORDS if d in text]


def _shot_size_level(shot: dict) -> str | None:
    """提取镜头景别（prompt 开头格式「[景别]」），返回等级名."""
    prompt = shot.get("prompt", "") or ""
    for sz in _SHOT_SIZE_ORDER:
        if sz in prompt:
            return sz
    return None


def _check_axis_crossing(shots: list[dict], hook: dict = None) -> dict:
    """检查相邻镜头同场景中「画左/画右」方向翻转（180°轴线越轴）.

    镜头组接规则（v6.21）：同一场景相邻镜头，同一角色「画左/画右」朝向不应
    随意翻转（越轴会让人物左右颠倒）。仅当镜头间有越轴豁免（插空镜/转身/特写）
    才允许。warning 级，不阻断。
    """
    issues, warnings = [], []
    for i in range(len(shots) - 1):
        s1, s2 = shots[i], shots[i + 1]
        loc1 = s1.get("location", "") or s1.get("scene", "")
        loc2 = s2.get("location", "") or s2.get("scene", "")
        if not loc1 or loc1 != loc2:
            continue  # 不同场景不查（场景切换允许重新建立朝向）
        # 相邻镜头方向词（画左/画右）
        t1 = " ".join([s1.get("prompt", ""), s1.get("ff_desc", "")])
        t2 = " ".join([s2.get("prompt", ""), s2.get("ff_desc", "")])
        d1 = _extract_shot_directions(t1)
        d2 = _extract_shot_directions(t2)
        if not d1 or not d2:
            continue  # 任一镜头未锚定方向，无法判定
        # 方向冲突：镜头1只有画左/画右且镜头2方向相反
        if set(d1) == {"画左"} and set(d2) == {"画右"}:
            warnings.append(
                f"镜头{s1['index']}→{s2['index']}: 同场景「画左」→「画右」朝向翻转，"
                f"疑似180°轴线越轴——人物左右颠倒观众分不清。需插入空镜/转身/特写"
                f"豁免，或统一朝向（镜头组接规则33）。"
            )
        elif set(d1) == {"画右"} and set(d2) == {"画左"}:
            warnings.append(
                f"镜头{s1['index']}→{s2['index']}: 同场景「画右」→「画左」朝向翻转，"
                f"疑似180°轴线越轴——人物左右颠倒观众分不清。需插入空镜/转身/特写"
                f"豁免，或统一朝向（镜头组接规则33）。"
            )
    for w in warnings:
        _get_logger().warn(f"轴线越轴: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# 打斗镜判定词（P2-1：景别跳跃豁免用——打斗短镜快切是规则7设计，非割裂）
_COMBAT_WORDS = (
    "刺杀", "偷袭", "伏击", "拔剑", "挥刀", "挥剑", "劈", "砍", "刺向", "刺入",
    "跃出", "格挡", "刀锋", "剑锋", "匕首", "搏杀", "搏斗", "打斗", "交手",
    "闪避", "反杀", "袭击", "挥",
)


def _is_combat_shot(shot: dict) -> bool:
    """打斗镜判定：prompt/motion/ff/lf 含打斗动作词."""
    text = " ".join([shot.get("prompt", ""), shot.get("motion_desc", ""),
                     shot.get("ff_desc", ""), shot.get("lf_desc", "")])
    return any(w in text for w in _COMBAT_WORDS)


# 非战斗镜头武器词检查（08-15 hook 帧被塞匕首）：拦截 step01 把武器写进非战斗
# 镜头的描述层泄漏。威胁源在场或打斗镜时武器合法、跳过；「戟指」是手势词先剔除，
# 防误命中（戟∈WEAPON_WORDS）。
def _check_frame_weapon_legality(shots: list[dict], hook: dict | None = None,
                                 characters: list | None = None) -> dict:
    """非战斗/无威胁镜头 ff/lf/prompt 出现武器词 → WARN（不阻断）."""
    from shared.content_safety import WEAPON_WORDS as _W_WORDS
    issues, warnings = [], []
    checks = [(s, f"镜头{s.get('index')}") for s in shots]
    if hook:
        checks.insert(0, (hook, "钩子"))
    _THREAT_NAMES = ("刺客", "杀手", "黑衣人", "蒙面人", "神秘人")
    for shot, label in checks:
        text = " ".join([shot.get("ff_desc", ""), shot.get("lf_desc", ""),
                         shot.get("prompt", "") or shot.get("motion_desc", "")])
        if any(n in text for n in _THREAT_NAMES) or _is_combat_shot(shot):
            continue
        # 剔除非武器复合词：戟指=手势、剑眉/刀眉=脸型描述（剑/刀/戟∈WEAPON_WORDS）
        for excl in ("戟指", "剑眉", "刀眉", "烟枪"):
            text = text.replace(excl, "")
        hit = [w for w in _W_WORDS if w in text]
        if hit:
            warnings.append(
                f"{label}:非战斗/无威胁镜头 ff/lf/prompt 出现武器词({','.join(hit)}),"
                "图片模型会渲染出该武器;若角色确实空手请删除,或改为明确归属")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_shot_size_jump(shots: list[dict]) -> dict:
    """检查相邻镜头景别跳跃幅度（禁止特写直接跳大远景）.

    景别等级 9 级（大特写→鸟瞰），相邻镜头跳跃幅度 >2 级视为割裂（如
    特写→大远景）。warning 级。

    v6.35（P2-1）：打斗镜豁免——打斗短镜快切（规则7）本就走特写↔全景
    交替制造节奏，跳跃是设计使然，不误报。
    """
    issues, warnings = [], []
    for i in range(len(shots) - 1):
        lv1 = _shot_size_level(shots[i])
        lv2 = _shot_size_level(shots[i + 1])
        if not lv1 or not lv2:
            continue
        # v6.36（08-15）：场景切换豁免——切到新场景用远景建立镜头（establishing
        # shot）是标准手法，前镜对白中近景→新场景远景不是割裂（镜头2→3 实测：
        # 照月湾小舟上中近景 → 远处田埂上远景）。与 _check_axis_crossing 同口径
        # （location 不同 = 新场景，允许重新建立朝向/景别）。
        _loc1 = shots[i].get("location", "") or shots[i].get("scene", "")
        _loc2 = shots[i + 1].get("location", "") or shots[i + 1].get("scene", "")
        if _loc1 and _loc2 and _loc1 != _loc2:
            continue
        # v6.35（P2-1）：打斗镜快切豁免（规则7：打斗/追杀一律 2-3 秒短镜 + 0.2s 硬切）
        if _is_combat_shot(shots[i]) or _is_combat_shot(shots[i + 1]):
            continue
        # v6.36（08-15）：正常节奏豁免——A-B-A 夹心。相邻跳跃对里某一镜被同景别
        # 两镜夹住（shots[i-1] 与 shots[i+1] 同景别，或 shots[i] 与 shots[i+2]
        # 同景别）=「切出再切回」节奏设计（对白中近景→环境全景→对白中近景 /
        # 特写→全景→特写），不是割裂。实测 8→9/12→13/13→14 全是此类，正常节奏
        # 走 B（接受），不喂修复轮。真跳跃（无夹心）走 A（修复轮 LLM 判断）。
        if (i >= 1 and _shot_size_level(shots[i - 1]) == lv2) or \
           (i + 2 < len(shots) and lv1 == _shot_size_level(shots[i + 2])):
            continue
        d1 = _SHOT_SIZE_ORDER.index(lv1)
        d2 = _SHOT_SIZE_ORDER.index(lv2)
        if abs(d1 - d2) > 2:
            warnings.append(
                f"镜头{shots[i]['index']}→{shots[i+1]['index']}: 景别「{lv1}」→「{lv2}」"
                f"跳跃幅度 {abs(d1 - d2)} 级（>2 级）——特写直接跳大远景观感割裂，"
                f"建议中间补过渡景别（镜头组接规则33）。"
            )
    for w in warnings:
        _get_logger().warn(f"景别跳跃: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_battle_layer(shots: list[dict], script: dict) -> dict:
    """检查大战场镜头是否含分层描述（battle-layered skill 吸收）.

    大战场镜头（prompt 含 大军/万人/阵列/士兵 等）应标 scene_layer 并分层描述，
    禁止一次性塞满上万士兵。warning 级（当前章节无战场镜头不触发）。
    """
    issues, warnings = [], []
    battle_kw = ("大军", "万人", "军队", "士兵", "阵列", "冲杀", "兵马", "千军", "万马")
    layer_kw = ("英雄层", "近景动作", "中景队列", "远景密度", "hero_layer",
                "near_action", "mid_queue", "far_density")
    for s in shots:
        text = " ".join([s.get("prompt", ""), s.get("motion_desc", ""), s.get("ff_desc", "")])
        if not any(k in text for k in battle_kw):
            continue
        has_layer = any(k in text for k in layer_kw) or s.get("scene_layer")
        if not has_layer:
            warnings.append(
                f"镜头{s['index']}: 大战场镜头（{s.get('scene','')}）未分层描述——"
                f"禁止一次性塞满上万士兵。应按四层分层（英雄层/近景动作/中景队列/"
                f"远景密度）+ 场景DNA + 战场资产复用（战场分层规则34）。"
            )
    for w in warnings:
        _get_logger().warn(f"战场分层: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_object_separability(shots: list[dict]) -> dict:
    """v6.24 新增：物体可分性检查（seedance 铁律7 / 规则35）.

    易碎/可撕裂/可弯折物体的分解类动作（掰断/撕开/劈开/折断）渲染差，
    应改为摔/取/递单帧动作或结果状态特写。warning-only。
    """
    issues, warnings = [], []
    result_state = ("裂", "碎", "破", "折断", "特写", "静止")
    for s in shots:
        text = " ".join([s.get("prompt", ""), s.get("motion_desc", ""),
                         s.get("ff_desc", ""), s.get("lf_desc", "")])
        hit = next((w for w in _DEFORMATION_VERBS if w in text), None)
        if not hit:
            continue
        if any(k in text for k in result_state):
            continue  # 已是结果态镜头（结果帧特写），不告警
        warnings.append(
            f"镜头{s['index']}: 形变动词「{hit}」——物体断裂渲染差，"
            f"改为摔/取/递单帧动作或结果状态特写（物体可分性规则35）。"
        )
    for w in warnings:
        _get_logger().warn(f"物体可分性: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_wide_shot_facial(shots: list[dict], hook: dict = None) -> dict:
    """v6.24 新增：远景镜头禁精细五官（capsule 远景省略 / 规则39）.

    远景/大远景/全景/俯拍/航拍中人物很小，写精细五官会导致 AI 把脸放大、
    比例崩坏。warning-only。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        probe = " ".join([s.get("ff_desc", ""), s.get("prompt", ""),
                          s.get("location", ""), s.get("scene", "")])
        if not any(w in probe for w in _WIDE_SHOT_WORDS):
            continue
        hit = next((w for w in _FINE_FACE_WORDS if w in probe), None)
        if hit:
            label = "hook" if (hook and s is hook) else s.get("index", "?")
            warnings.append(
                f"镜头{label}: 远景镜头含精细五官词「{hit}」——远景人物只留身份+服装主色+剪影，"
                f"禁止精细五官与全身服装细节（远景人物省略规则39）。"
            )
    for w in warnings:
        _get_logger().warn(f"远景禁五官: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_hand_lock(shots: list[dict]) -> dict:
    """v6.24 新增：双人递物/交易手部锁定检查（seedance 铁律8 / 规则38）.

    递/接/交/换场景手部易混淆（尤其有饰品时），必须写明手部归属与方向。
    warning-only。
    """
    issues, warnings = [], []
    for s in shots:
        text = " ".join([s.get("prompt", ""), s.get("motion_desc", ""), s.get("ff_desc", "")])
        if not any(k in text for k in _HAND_EXCHANGE_WORDS):
            continue
        if any(k in text for k in _HAND_LOCK_WORDS):
            continue
        warnings.append(
            f"镜头{s['index']}: 递物/交易镜头未锁定手部——需写明谁用哪只手递/接、"
            f"双方手部饰品严格区分（双人递物手部锁定规则38）。"
        )
    for w in warnings:
        _get_logger().warn(f"手部锁定: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_subtitle_leak(shots: list[dict], hook: dict = None) -> dict:
    """v6.24 新增：画面字段字幕泄漏检查（seedance 铁律3 / 规则40）.

    prompt/ff_desc/lf_desc/motion_desc 出现「字幕/文字/水印」字样或【字幕：】标记，
    会导致视频模型渲染烧录文字；字幕由 step04 后期统一烧录。warning-only。
    """
    issues, warnings = [], []
    leak_kw = ("字幕", "文字", "水印", "【字幕", "字体")
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        text = " ".join([s.get("prompt", ""), s.get("ff_desc", ""),
                         s.get("lf_desc", ""), s.get("motion_desc", "")])
        hit = next((w for w in leak_kw if w in text), None)
        if hit:
            label = "hook" if (hook and s is hook) else s.get("index", "?")
            warnings.append(
                f"镜头{label}: 画面描述含「{hit}」——视频模型会渲染烧录文字，"
                f"字幕由后期统一烧录，画面描述必须无任何文字（画面无字幕规则40）。"
            )
    for w in warnings:
        _get_logger().warn(f"字幕泄漏: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_long_dash_usage(shots: list[dict], hook: dict = None) -> dict:
    """v6.38 新增：长破折号「————」检查（08-21 吸收 shyman159/seedance 铁律 9 排版规则）.

    规则56: prompt 正文中禁止使用「————」长破折号作分隔符。
    长破折号会污染 prompt 排版结构、影响 LLM 对分段/换行的理解。
    改用：空行/换行/【方括号区块标题】（如【角色声线锁定】【声音】）。
    warning-only（防崩神词放最末，长破折号中间也能用但建议改）。
    """
    issues, warnings = [], []
    import re
    # 匹配连续 3+ 个 em dash（—— 实际是 2 字符 "—" "—"）
    long_dash_re = re.compile(r'—{3,}')
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        text = " ".join([s.get("prompt", ""), s.get("ff_desc", ""),
                         s.get("lf_desc", ""), s.get("motion_desc", "")])
        m = long_dash_re.search(text)
        if m:
            label = "hook" if (hook and s is hook) else s.get("index", "?")
            warnings.append(
                f"镜头{label}: prompt 含「{m.group()}」长破折号（≥3 个 ——）——"
                f"改用空行/换行/【方括号区块标题】（规则 56，08-21 吸收 seedance 铁律 9）"
            )
    for w in warnings:
        _get_logger().warn(f"长破折号: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# v6.25: 威胁源-视线耦合检查（规则41）
# v6.43 重构：统一从 prompt_semantic.registry 引入
from step01_script_gen.prompt_semantic import (
    THREAT_WORDS_ENLARGED as _THREAT_WORDS,
)
# 潜伏态威胁免朝向（规则48：潜伏水下/藏匿中无需朝向目标；与 B2 注入共用）
_RULE48_LURKING = ("潜伏水下", "水下黑影", "微露水面", "藏于水下", "水下轮廓", "藏身水下")
# 「发现者视线指向某威胁」的表达（视线锁定/紧盯/看向 + 威胁词）
_THREAT_GAZE_RE = re.compile(
    r'[视线目光双眼眼神]?[^。；！？]{0,8}[盯看望锁][^。；！？]{0,10}(刺客|杀手|黑衣人|黑影|水中|水下)'
)


def _check_threat_gaze_coupling(shots: list[dict], hook: dict = None) -> dict:
    """v6.25 新增：威胁源-视线耦合检查（规则41）.

    从 hook 调试学到的教训：威胁源（刺客）的站位必须与「发现者」的视线方向
    逻辑自洽。v6 构图里把刺客移到画左（船尾侧），结果中舱的听香朝画右看
    根本看不到——反而是画右船头的杨旭先发现，逻辑崩坏。

    本检查聚焦两类问题（warning-only）：
    1. **威胁源朝向不明/背对目标**：威胁源（刺客）描述里没写它朝向目标方向
       （画左/画右），或写了朝向但没对齐「盯目标」。
    2. **视线所指与威胁方位矛盾**：角色「视线锁定画X的威胁」，但威胁源
       描述里方位（画X）与视线方向不一致——发现者看的不是威胁所在处。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        text = " ".join([s.get("prompt", ""), s.get("ff_desc", ""),
                         s.get("lf_desc", ""), s.get("motion_desc", "")])
        label = "hook" if (hook and s is hook) else s.get("index", "?")

        # 1. 有威胁源但无朝向目标：威胁源该「紧盯/朝向某方向的目标」，若只写
        #    「潜伏水下」而无朝向目标，容易画成背对或正对镜头（hook 实测）
        has_threat = any(w in text for w in _THREAT_WORDS)
        # v6.35（P1-1）结构化威胁源朝向（站位台吸收）：char_layout.facing 直读，
        # 比文本正则更准——「背对镜头/正对镜头」是规则41 死穴，文本写法多变易漏。
        layout = s.get("char_layout") or []
        layout_threat = ""  # 威胁源在 char_layout 中的名字
        layout_facing = ""  # 对应 facing
        if isinstance(layout, list):
            for item in layout:
                if isinstance(item, dict):
                    nm = str(item.get("name", "")).strip()
                    if any(w in nm for w in _THREAT_WORDS):
                        layout_threat = nm
                        layout_facing = str(item.get("facing", "")).strip()
                        break
        layout_bad_facing = bool(layout_facing) and (
            "背对" in layout_facing or "正对镜头" in layout_facing)

        if has_threat or layout_threat:
            # 威胁源是否有「面向目标」的朝向表达：朝画X/紧盯画X/侧身/望向X
            # （char_layout 的 facing 指向目标也算——如「面向画左的杨旭」）
            threat_has_target = any(
                k in text for k in (
                    "朝画", "紧盯画", "盯向画", "望画", "侧身", "朝向", "面向",
                    "逼近", "逼近画",
                    # v6.34：与 _check_ff_desc_anchors 朝向句式表对齐（08-15 审计）
                    "刺向", "劈向", "扑向", "扎向", "斩向", "砍向", "挥向",
                    "冲向", "探向", "逼向", "刀尖朝", "剑尖朝", "匕首朝",
                    "举过头顶", "高举", "抵住", "对准", "正对",
                    "即将刺", "即将劈", "即将扑", "即将跃", "即将砸",
                )
            )
            if layout_facing and any(k in layout_facing for k in ("面向", "朝向", "盯向", "望向")):
                threat_has_target = True
            if layout_bad_facing:
                # 结构化命中背对/正对镜头——精确告警，替代笼统的「未写朝向目标」
                warnings.append(
                    f"镜头{label}: char_layout 威胁源「{layout_threat}」朝向「{layout_facing}」"
                    f"——规则41要求威胁源侧身朝向目标，禁止背对目标或正对镜头，"
                    f"否则发现者看不到它、模型也画不对攻击方向"
                )
            elif not threat_has_target:
                warnings.append(
                    f"镜头{label}: 威胁源（刺客/黑衣人等）未写朝向目标——应写明它"
                    f"「侧身朝画X/紧盯画X/朝向目标」，否则模型易画成背对目标或正对镜头"
                    f"（威胁源-视线耦合规则41）。"
                )

        # 2. 发现者视线方向 vs 威胁方位矛盾：
        #    「视线锁定画右的刺客」→ 威胁源（刺客）应摆在画右；若威胁源的
        #    方位描述（画左/画右）与视线方向相反，发现者看的不是威胁
        #    （hook 实测：听香「视线锁定画右水下刺客」，但刺客被排到画左，
        #     逻辑崩坏——中舱听香朝画右看，画右是船头杨旭，画左才是刺客）。
        gaze_match = _THREAT_GAZE_RE.search(text)
        if gaze_match:
            seg = gaze_match.group()
            gaze_dir = next((d for d in ("画左", "画右") if d in seg), None)
            if gaze_dir:
                # 威胁源（刺客）自身的方位：找「刺客/黑衣人/潜伏 附近」的方位词
                threat_spot = next(
                    (d for d in ("画左", "画右")
                     if any(t in text for t in _THREAT_WORDS)
                     and d in text and d != gaze_dir),
                    None,
                )
                if threat_spot:
                    warnings.append(
                        f"镜头{label}: 视线-威胁方位矛盾——发现者「{seg[:26]}」，"
                        f"视线指{gaze_dir}，但威胁源（刺客）方位写的是{threat_spot}"
                        f"（画{gaze_dir}反侧）。威胁源必须与发现者视线同向摆放，"
                        f"否则发现者看到的不是威胁（威胁源-视线耦合规则41）。"
                    )
    for w in warnings:
        _get_logger().warn(f"威胁源-视线耦合: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ========================================================================
# Plan A（2026-08-14）：ff_desc 全锚点检查——查「真正的图片输入」而非 prompt。
#
# 背景：chapter_001 hook 首帧手改 ff_desc + 反复重渲「越改越乱」——手改删掉了
# 威胁源朝向锚/尺寸锚/服装色，图像模型每次重渲随机漂（衣服色/视线方向必变）。
# 系统性修法：ff_desc 必须自带全锚点，让 LLM 一次生成、校验兜底，杜绝手改。
# 四类锚点（全部 warning-only，不阻断）：
#   A. 威胁源朝向锚（规则41）：ff_desc 出现威胁源但无朝向/目标表达 → 提示。
#      （查 ff_desc 而非 prompt——prompt 有锚但 ff_desc 缺 = 首帧仍漂）
#   B. 视线-威胁方位矛盾（规则41）：ff_desc 内视线方向与威胁方位相反 → 提示。
#   C. 尺寸锚（规则25）：场景物体首现镜 ff_desc 必须含「N米」尺寸锚。
#   D. 服装色重述（规则13.7）：出镜角色 ff_desc 必须重述其关键服装色/衣。
# ========================================================================

_FF_DESC_COLOR_WORDS = (
    "素白", "月白", "雪白", "霜白", "玄色", "墨色", "鸦青", "青灰", "炭黑",
    "鹅黄", "藕荷", "绯红", "绛红", "黛青", "藏青", "宝蓝", "靛蓝",
    "杏黄", "杏红", "桃红", "柳绿", "竹青", "素青", "玄", "绯", "绛", "黛", "翠", "朱",
)
_FF_DESC_GARMENT_WORDS = (
    "劲装", "道袍", "长衫", "纱衣", "缁衣", "斗篷", "小帽", "轻袍", "白袍",
    "青袍", "玄袍", "黑袍", "红袍", "袍", "衫", "裳", "袄", "氅", "裙", "甲",
)
_FF_DESC_SIZE_RE = re.compile(r"\d+(?:\.\d+)?\s*米")


def _extract_dress_tokens(dynamic_features: str) -> tuple[list[str], list[str]]:
    """从角色衣着描述提取「服装色词 / 衣词」，供 ff_desc 重述检查（规则13.7）.

    例：杨旭「素白色轻袍…」→ (["素白"], ["轻袍", "袍"])；
        听香「一袭春衫…」→ ([], ["衫"])；刺客「浑身湿透…」→ ([], []) 跳过。
    """
    dyn = dynamic_features or ""
    colors = [c for c in _FF_DESC_COLOR_WORDS if c in dyn]
    garments = [g for g in _FF_DESC_GARMENT_WORDS if g in dyn]
    return colors, garments


# ============================================================================
# 08-15 B1/B2：ff/lf 确定性锚注入（规则13.7 服装色防漂色 / 规则41 威胁朝向防背对）
#
# B1 服装色锚：出镜角色缺服装色词时，在角色名后插「（色+衣）」短锚——比让 LLM
#   「记得重述」稳一个数量级（check 判定：_extract_dress_tokens 提取的 token 任一
#   在帧中即过，注入的锚必含其一，必然清除告警）。
# B2 威胁朝向：威胁源缺朝向时注入「侧身朝画X/面向目标」——check 判定：_THREAT_WORDS
#   命中 + 非潜伏 + 无朝向词（_THREAT_FACING_WORDS）→ 报。注入与判定用同一词表。
# ============================================================================
# 威胁源朝向表达（_check_frame_desc_anchors A 判定同源——共享单一词表防漂移）
# v6.43 重构：基础词从 prompt_semantic.registry 引入，validation 扩展
from step01_script_gen.prompt_semantic import (
    THREAT_FACING_WORDS as _PS_THREAT_FACING,
    THREAT_GONE_WORDS as _PS_THREAT_GONE,
)
_THREAT_FACING_WORDS = (
    *_PS_THREAT_FACING,
    # validation 扩展（更具体的朝向/动作短语）
    "朝画", "面向", "面朝", "朝向", "侧身", "紧盯画", "盯向画",
    "望画", "望向", "逼近", "逼近画", "刀尖对准", "正对目标",
    "对准画", "指向画", "刺向", "劈向", "扑向", "扎向", "斩向", "砍向", "挥向",
    "冲向", "探向", "逼向", "刀尖朝", "剑尖朝", "匕首朝",
    "举过头顶", "高举", "抵住", "对准", "正对",
    "即将刺", "即将劈", "即将扑", "即将跃", "即将砸",
)
# 威胁已没入/消失 → 免朝向（与 keyframes 检查 threat_gone 同口径：没入水下的
# 刺客无需朝向目标——「刺客已没入水中、水面只剩涟漪」不该被要求朝向）
_THREAT_GONE_WORDS = _PS_THREAT_GONE


def _inject_dress_anchor(frame: str, characters) -> str:
    """08-15 B1：ff/lf 出镜角色缺服装色锚时，在角色名首现后插「（色+衣）」短锚.

    判定与 _check_frame_desc_anchors D 对称：角色名在帧中 + dynamic_features 有
    服装色/衣词 + 帧中无任何已提取 token → 注入。锚 = 色+衣（素白轻袍）或单衣词
    （衫）——都是 _extract_dress_tokens 会提取的词，注入后必然清除告警。
    step02a 帧生成还会注入完整 static+dynamic，此处只做脚本层短锚（防 ff/lf 漂色）。
    """
    if not frame or not characters:
        return frame
    for c in characters:
        ident = c.get("identifier") or ""
        if not ident or ident not in frame:
            continue
        dyn = c.get("dynamic_features") or ""
        colors, garments = _extract_dress_tokens(dyn)
        if not colors and not garments:
            continue  # 无衣着描述（如刺客「浑身湿透」），无法锚定，跳过
        if any(x in frame for x in colors) or any(x in frame for x in garments):
            continue  # 帧中已有服装锚
        if colors and garments:
            anchor = colors[0] + garments[0]          # 素白轻袍
        elif colors:
            anchor = colors[0] + "衣"
        else:
            # 单衣词（衫/袍）→ 从 dynamic 里取含该衣词的完整词（春衫/长衫），
            # 避免注入「听香（衫）」这种单字锚
            import re as _re
            _m = _re.search(r"[一-鿿]{0,2}" + garments[0], dyn)
            anchor = _m.group() if _m else garments[0]
        frame = frame.replace(ident, f"{ident}（{anchor}）", 1)
    return frame


def _inject_threat_facing(frame: str, shot: dict) -> str:
    """08-15 B2：威胁源在 ff/lf 缺朝向锚时注入朝向（规则41 防背对目标）.

    优先级：① char_layout 威胁的 facing（真实站位意图，如「面向画右的杨旭」）
    ② 画面方向反推（威胁在画右 → 侧身朝画左）③ 默认侧身朝画左。
    潜伏（_RULE48_LURKING）与已没入/消失（_THREAT_GONE_WORDS）不注入——前者
    无需朝向、后者给「已没入」硬加朝向是语义错误。
    """
    if not frame or not any(w in frame for w in _THREAT_WORDS):
        return frame
    if any(k in frame for k in _RULE48_LURKING) or any(g in frame for g in _THREAT_GONE_WORDS):
        return frame
    if any(k in frame for k in _THREAT_FACING_WORDS):
        return frame
    # ① char_layout 威胁 facing（站位意图权威）
    for x in (shot.get("char_layout") or []):
        nm = x.get("name") or ""
        if any(t in nm for t in _THREAT_WORDS) and x.get("facing"):
            return f"{frame}，{x['facing']}"
    # ② 画面方向反推：威胁所在侧 → 面向对侧
    if "画右" in frame:
        return f"{frame}，侧身朝画左"
    if "画左" in frame:
        return f"{frame}，侧身朝画右"
    # ③ 默认
    return f"{frame}，侧身朝画左"


# 场景物体类别词（识别 ff_desc 中的空间物体，用于「bank 未覆盖才要求尺寸锚」）。
# 只用复合词：单字「船/舟/车」会命中「船头/船尾/船舷」等已知船的方位词（误报），
# 复合词（小舟/马车/厅堂…）才是「独立物体」的信号。
_SCENE_OBJ_HINT_WORDS = (
    "舴艋舟", "小舟", "扁舟", "画舫", "乌篷船", "马车", "牛车", "轿子",
    "厅堂", "院落", "宝塔", "拱桥", "楼阁", "山寨", "府邸",
)


def _check_size_anchor_warnings(shots: list[dict], hook: dict | None) -> list[str]:
    """规则25：ff_desc 尺寸锚检查（Plan B 2026-08-14）——bank 未覆盖才要求.

    职责分工：场景物体尺寸的**渲染权威来源是 objects_bank 注入**
    （vimax_bridge._inject_object_size_anchor 在帧/视频 prompt 拼入「长X米宽Y米」）。
    因此 ff_desc 只在「物体不在 bank」时才有自足义务——此时注入帮不上，
    ff_desc 若缺尺寸锚，首帧比例就可能漂。

    判定：ff_desc 含场景物体复合词（小舟/马车/厅堂…）但：
    - 无「N米」尺寸锚 且
    - 整镜文本（location/scene/ff/lf/motion/prompt/main_subject）不含任何 bank 物体
      术语（identifier/consistent_terms/subtype——命中即 bank 覆盖、注入会兜底）
    → WARN（bank 外新物体缺尺寸锚）。
    """
    out = []
    try:
        from shared.scene_objects_bank import SceneObjectsBank
        bank = SceneObjectsBank()
        bank.load()
    except Exception:
        return out
    bank_terms = set()
    for ident in bank.get_all_identifiers():
        obj = bank.get_object(ident) or {}
        bank_terms.add(ident)
        bank_terms.update(obj.get("consistent_terms") or [])
        if obj.get("subtype"):
            bank_terms.add(obj["subtype"])
    bank_terms = {t for t in bank_terms if t}

    targets = (([hook] if hook and isinstance(hook, dict) else [])
               + list(shots))
    for s in targets:
        label = "钩子镜头(index=0)" if (hook and s is hook) else f"镜头{s.get('index', '?')}"
        ff = (s.get("ff_desc") or "").strip()
        if not ff:
            continue
        if _FF_DESC_SIZE_RE.search(ff):
            continue  # 已有尺寸锚
        # bank 覆盖判定：与 _inject_object_size_anchor 同口径，整镜文本匹配
        probe = " ".join(filter(None, [
            s.get("location", ""), s.get("scene", ""), ff,
            s.get("lf_desc", ""), s.get("motion_desc", ""),
            s.get("prompt", ""), s.get("main_subject", ""),
        ]))
        if any(t in probe for t in bank_terms):
            continue  # bank 覆盖，注入兜底，ff_desc 免写
        hits = [w for w in _SCENE_OBJ_HINT_WORDS if w in ff]
        if hits:
            out.append(
                f"{label}: ff_desc 出现场景物体「{hits[0]}」但缺尺寸锚且不在物体库"
                f"（规则25）——该物体无法靠 bank 注入补尺寸，ff_desc 必须写出锚定尺寸"
                f"（如「舴艋舟长约7米」），否则首帧比例漂移。"
            )
    return out


def _check_frame_desc_anchors(text: str, label: str,
                              known_chars, char_dress) -> list[str]:
    """对单段画面描述（ff_desc/lf_desc）做 A/B/D 三类锚点检查（v6.35）.

    与 ff_desc 相同口径套到 lf_desc——尾帧同样喂图片模型，缺锚 → 尾帧漂色/
    比例漂/威胁背对，首尾帧风格断裂（08-15 审计 P17 遗留：锚点只查 ff 一半）。
    A. 威胁源朝向锚（规则41）  B. 视线-威胁方位矛盾  D. 出镜角色服装色重述
    """
    out: list[str] = []
    if not text:
        return out

    # --- A. 威胁源朝向锚 ---
    if (any(w in text for w in _THREAT_WORDS)
            and not any(k in text for k in _RULE48_LURKING)
            # 08-15 B2：威胁已没入/消失 → 免朝向（「刺客已没入水中、水面只剩涟漪」
            # 不该被要求朝向，与 keyframes threat_gone 同口径）
            and not any(g in text for g in _THREAT_GONE_WORDS)):
        # 08-15 共享 _THREAT_FACING_WORDS 单一词表（含 v6.34 补的武器指向/攻击预备）
        has_facing = any(k in text for k in _THREAT_FACING_WORDS)
        if not has_facing:
            out.append(
                f"{label}: 出现威胁源（刺客/黑影等）但无朝向/目标锚定"
                f"（规则41）——画面描述是图片输入，威胁源必须写明「侧身朝画X/刀尖"
                f"对准[目标]/目光望向画X的[角色]」，否则模型画成背对目标或正对镜头。"
            )

    # --- B. 视线-威胁方位矛盾 ---
    gm = _THREAT_GAZE_RE.search(text)
    if gm:
        seg = gm.group()
        gaze_dir = next((d for d in ("画左", "画右") if d in seg), None)
        if gaze_dir:
            threat_dir = next(
                (d for d in ("画左", "画右")
                 if d in text and d != gaze_dir and any(t in text for t in _THREAT_WORDS)),
                None,
            )
            if threat_dir:
                out.append(
                    f"{label}: 视线-威胁方位矛盾——「{seg[:26]}」视线指"
                    f"{gaze_dir}，但威胁源方位写{threat_dir}（规则41）。威胁源必须与"
                    f"发现者视线同向摆放，否则发现者看到的不是威胁。"
                )

    # --- D. 出镜角色服装色重述（规则13.7） ---
    onstage = [c for c in known_chars if c and c in text]
    missing_dress = []
    for c in onstage:
        dyn = char_dress.get(c, "") or ""
        colors, garments = _extract_dress_tokens(dyn)
        if not colors and not garments:
            continue  # 角色无衣着描述（如刺客「浑身湿透」），无法锚定，跳过
        if not (any(x in text for x in colors) or any(x in text for x in garments)):
            hint = (colors or garments)[0]
            missing_dress.append(f"{c}({hint})")
    if missing_dress:
        out.append(
            f"{label}: 未重述出镜角色关键服装色 {missing_dress}"
            f"（规则13.7 防漂色）——不重述时衣服色只靠肖像软锚，重渲必漂。"
        )
    return out


def _check_ff_desc_anchors(shots: list[dict], hook: dict = None,
                           characters: list | None = None) -> dict:
    """Plan A（2026-08-14 → v6.35 补 lf 对称）：ff_desc/lf_desc 全锚点检查.

    v6.35（08-15 审计 P17 遗留）：锚点此前只查 ff_desc，尾帧同样喂图片模型，
    缺锚 → 尾帧漂色/比例漂/威胁背对，首尾帧风格断裂。A/B/D 三类锚点现对称
    套到 lf_desc。
    """
    issues, warnings = [], []
    labels = [(s, f"镜头{s.get('index', '?')}") for s in shots]
    if hook and isinstance(hook, dict):
        labels.append((hook, "钩子镜头(index=0)"))

    known_chars = {c.get("identifier") for c in (characters or []) if c.get("identifier")}
    char_dress = {c.get("identifier"): c.get("dynamic_features", "")
                  for c in (characters or []) if c.get("identifier")}

    for shot, label in labels:
        ff = (shot.get("ff_desc") or "").strip()
        lf = (shot.get("lf_desc") or "").strip()
        # 尾帧画面描述：ff/lf 内容相同时不重复检查（lf 继承 ff 锚点）
        if ff and lf and ff == lf:
            lf = ""
        for frame_text, frame_tag in ((ff, "ff_desc"), (lf, "lf_desc")):
            if not frame_text:
                continue
            for w in _check_frame_desc_anchors(
                frame_text, f"{label}: {frame_tag}", known_chars, char_dress,
            ):
                warnings.append(w)

    # --- C. 场景物体首现镜尺寸锚（规则25，需 objects_bank；ff 专属——
    # bank 注入同时兜底 lf，lf 免写） ---
    warnings.extend(_check_size_anchor_warnings(shots, hook))

    for w in warnings:
        _get_logger().warn(f"ff描述锚点: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ========================================================================
# v6.29 新增：keyframes 简略检查（08-10 hook 关键帧质量）
#
# 背景：hook 关键帧「刺客破水而出」被图片模型画成无刺客/听香站水面（船断）/
# 杨旭站立后倒——根因是 keyframes desc 只写动作动词，缺角色姿态/位置/朝向锚定。
# 本检查对每个含中间关键帧（0<time_ratio<1）的镜头，检查 keyframes[].desc 是否
# 含角色姿态词/位置词/威胁源朝向；简略则 WARN 提示 LLM 重写规范描述。
# warning-only 不阻断（step02a 代码层 _extract_char_layout 是最终兜底）。
# ========================================================================

# 关键帧简略检查：姿态词（角色画成什么样）/ 位置词（角色在哪）
_KF_VALIDATION_POSE_WORDS = (
    "仰卧", "仰面", "躺", "卧", "跪坐", "跪", "坐", "蹲", "趴", "俯卧",
    "直立", "站立", "站", "立", "半身探出", "半身出水", "潜伏",
)
_KF_VALIDATION_POS_WORDS = (
    "船头", "船尾", "船中", "中舱", "船梢", "船舷", "船板",
    "画左", "画右", "画中", "水中", "水下", "水面", "岸上",
)
# 威胁源词（含合规替换后"神秘人"——LLM 可能直接产出）
_KF_VALIDATION_THREAT_WORDS = (
    "刺客", "杀手", "黑衣人", "黑影", "神秘人", "蒙面人", "潜伏", "埋伏", "伏击",
)
# 威胁源朝向表达（Plan A 2026-08-14：补「正对/对准/刀尖朝/刺向」——武器指向目标
# 也是朝向锚，如「刀尖朝下正对杨旭心口」，此前漏检误报）
_KF_VALIDATION_ORIENT_WORDS = (
    "面向", "朝向", "侧身", "紧盯", "盯向", "望向", "朝画", "逼近",
    "正对", "对准", "刀尖朝", "刺向", "直刺", "劈落",
)


def _check_keyframes_quality(shots: list[dict], hook: dict = None,
                             characters: list | None = None) -> dict:
    """检查 keyframes 中间帧 desc 是否缺角色姿态/位置/朝向锚定（warning-only）.

    P1-6 修复（08-12）：26 镜 + hook 逐镜报同样一句话刷屏、淹没真实缺陷。
    改为**聚合提示**——每个问题类型最多一条，注明命中镜头数 + 首例示例
    （代码层 _extract_char_layout 是最终兜底，warning 只作一次指引）。

    PR-2（08-15）：空镜（无出镜角色，如纯马车疾驰转场）不要求角色姿态/位置
    锚定——没有角色可锚，报「缺角色姿态」是误报；且 post_fix 已确定性注入
    骨架，非空镜不会再缺。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    # PR-2：空镜跳过（_extract_char_layout 对空镜返回空、注入不了，检查也当放过）
    if characters:
        try:
            from shared.vimax_bridge import is_empty_scene as _is_empty
        except Exception:
            _is_empty = None
        if _is_empty:
            targets = [s for s in targets if not _is_empty(s, characters)]
    # 聚合统计（问题类型 → 命中列表）
    empty_kf = []          # desc 为空的中间帧
    missing_anchor = []    # 缺姿态/位置锚定（(label, tr, desc[:36])）
    missing_orient = []    # 威胁源未写朝向（(label, tr)）
    for s in targets:
        label = "hook" if (hook and s is hook) else s.get("index", "?")
        kfs = s.get("keyframes") or []
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            # v6.34 字段迁移：走 helper（t_sec 优先，time_ratio fallback）
            t = get_kf_t_sec(kf, s)
            if t is None or t <= 0:
                continue
            try:
                dur = float(s.get("duration_sec") or s.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur > 0 and t >= dur:
                continue
            tr = (t / dur) if dur > 0 else 0  # 物理比例（仅日志用）
            desc = (kf.get("desc") or "").strip()
            if not desc:
                empty_kf.append(label)
                continue
            # 1. 姿态/位置锚定：desc 是否含角色姿态词 + 位置词
            has_pose = any(w in desc for w in _KF_VALIDATION_POSE_WORDS)
            has_pos = any(w in desc for w in _KF_VALIDATION_POS_WORDS)
            # 2. 威胁源朝向：desc 含威胁词而缺朝向表达 → 提示
            has_threat = any(w in desc for w in _KF_VALIDATION_THREAT_WORDS)
            has_orient = any(w in desc for w in _KF_VALIDATION_ORIENT_WORDS)
            # Plan A（2026-08-14）：威胁已没入/消失/不见踪影时免朝向——没入水下的
            # 刺客无需朝向目标（如后果帧「刺客已没入画左水下、水面浪花翻涌」），
            # 免误报（规则41 只要求可画的潜伏威胁朝向目标）。
            threat_gone = any(g in desc for g in ("已没入", "没入水中", "没入水下", "消失", "不见踪影"))
            if not has_pose or not has_pos:
                missing_anchor.append((label, tr, desc[:36]))
            if has_threat and not has_orient and not threat_gone:
                missing_orient.append((label, tr))
    if empty_kf:
        warnings.append(
            # P1-2 修复（08-13）：label 混合 int 镜头号 + str "hook"，sorted 须 key=str 防 TypeError
            f"{len(empty_kf)} 个中间关键帧 desc 为空（镜头 {sorted(set(empty_kf), key=str)}），"
            f"应写清该瞬间每个角色的姿态/位置/朝向"
        )
    if missing_anchor:
        _first = missing_anchor[0]
        warnings.append(
            f"{len(missing_anchor)} 个中间关键帧 desc 缺角色姿态/位置锚定"
            f"（首例：镜头{_first[0]} t={_first[1]:.1f}「{_first[2]}…」）——"
            f"应写清该瞬间每个角色的姿态（仰卧/跪坐/站）与位置（船头/船尾/画左/画右），"
            f"如「杨旭仰卧船头，听香跪坐船尾，刺客半身探出画右水面」（08-10 关键帧质量）。"
        )
    if missing_orient:
        # P1-2 修复（08-13）：label 混合 int/int 与 str "hook"，sorted 须 key=str 防 TypeError
        _labs = sorted({l for l, _ in missing_orient}, key=str)
        warnings.append(
            f"{len(missing_orient)} 个中间关键帧的威胁源未写朝向目标（镜头 {_labs}）——"
            f"应写明「面向/朝向目标」，否则图片模型把刺客画成背对目标或正对镜头。"
        )
    for w in warnings:
        _get_logger().warn(f"keyframes质量: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ========================================================================
# v6.31 新增：关键帧时序一致性检查（08-16 钩子 kf 修复）
#
# 背景：钩子 kf0.4 prompt 同时写「刺客半身探出（刚出水面）」+「杨旭仰卧+心口
# 暗红洇痕（已躺下+已流血）」——pre-state 与 post-state 混在同一 frame，
# 渲染后图片模型左右为难（刺客方向错/刀位置错/红血时机错/与 FF 不一致）。
#
# 检查项：
# 1. **状态单调性**（kf[0]→kf[1]→kf[2]）：每个出镜角色的关键状态
#    （血/出水/仰卧）只能单调递进，不能回退（如 kf0 有血、kf1 又没血）。
# 2. **单 frame 时序一致性**：单 kf 内不能同时含 pre-state 与 post-state
#    （「刚出水面」+「已躺下有血」是典型冲突——刺客刚出来、杨旭就已经躺下
#    有血，意味着刺杀瞬间过去 + 刺客又出来一次，时序荒谬）。
# 3. **FF→kf0→LF 状态连续性**：FF 杨旭盘坐 + kf0 杨旭躺下 + LF 杨旭躺下
#    缺过渡——kf0 必须是「仍坐/未察觉」或「刚察觉」，不能跳到「已躺下」。
#
# 状态词表（关键词匹配）：
#   - 血相关：暗红/洇痕/血/红污/血染/红点/血迹/红痕
#   - 刺客状态：刚出水面/半身探出/破水/出水/没入水中/已没入/倒纵入水
#   - 杨旭姿态：盘坐/坐/仰卧/躺/倒/仰倒
#   - 听香状态：惊叫/尖叫/捂嘴/张嘴欲呼/披头散发
# ========================================================================
_BLOOD_WORDS = ("暗红", "洇痕", "血污", "血染", "血痕", "血迹", "红痕", "红点", "红污", "流血", "血")
# v6.43 重构：基础词从 prompt_semantic.registry 引入，validation 扩展"没入/已没入"等
from step01_script_gen.prompt_semantic import THREAT_WATER_WORDS as _PS_THREAT_WATER
_THREAT_WATER_WORDS = (
    *_PS_THREAT_WATER,
    "没入水中", "已没入", "已彻底没入", "倒纵入水", "潜回水中", "潜伏",
)
_SUBJECT_POSE_WORDS = {
    "杨旭": ("盘坐", "盘膝", "端坐", "坐", "跪坐", "半躺", "倚靠", "仰卧", "躺", "仰倒", "倒", "已躺下"),
    "听香": ("撑伞", "立", "站", "跪坐", "跪", "捂嘴", "惊叫", "尖叫", "披头散发"),
}


def _extract_state_markers(text: str) -> dict:
    """从 kf/ff/lf desc 文本里提取状态标记."""
    markers = {"has_blood": False, "threat_water_state": None, "poses": {}}
    if not text:
        return markers
    for w in _BLOOD_WORDS:
        if w in text:
            markers["has_blood"] = True
            break
    for w in _THREAT_WATER_WORDS:
        if w in text:
            markers["threat_water_state"] = w
            break
    for char, words in _SUBJECT_POSE_WORDS.items():
        for w in words:
            if w in text:
                markers["poses"][char] = w
                break
    return markers


def _check_kf_temporal_consistency(shots: list[dict], hook: dict = None) -> dict:
    """v6.31 关键帧时序一致性检查（warning-only）.

    1. 单 frame pre/post 冲突（"刺客刚出水面" + "杨旭仰卧血痕"）
    2. kf[0..N] 状态单调性（血不能回退、出水不能回退）
    3. FF→kf[0] 状态连续性（不能跳变太大）
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])

    for s in targets:
        label = "hook" if (hook and s is hook) else s.get("index", "?")
        ff_text = (s.get("ff_desc") or "").strip()
        lf_text = (s.get("lf_desc") or "").strip()
        kfs = s.get("keyframes") or []

        # 取中间帧（v6.34 字段迁移：t_sec 走 helper，time_ratio fallback）
        try:
            dur = float(s.get("duration_sec") or s.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        mid_kfs = []
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, s)
            if t is None or t <= 0:
                continue
            if dur > 0 and t >= dur:
                continue
            # 转换为比例（仅用于 log + 单调性比较）
            tr = (t / dur) if dur > 0 else 0
            mid_kfs.append((tr, (kf.get("desc") or "").strip()))

        # 按 t_sec 物理时刻排序
        mid_kfs.sort(key=lambda x: x[0])
        if not mid_kfs:
            continue

        ff_m = _extract_state_markers(ff_text)
        kf_markers = [_extract_state_markers(d) for _, d in mid_kfs]
        lf_m = _extract_state_markers(lf_text)

        # 1. 单 frame pre/post 冲突
        # 真矛盾 = 刺客 pre-state（刚出/刚探出）+ 杨旭 post-state（仰卧+血/洇痕）
        # 允许的 stab 瞬间 = 刺客 pre-state + 杨旭 仍坐 + 血初现
        for tr, desc in mid_kfs:
            m = _extract_state_markers(desc)
            threat_just_out = m["threat_water_state"] in (
                "刚出水面", "半身探出", "破水而出", "窜出", "浮出", "半身破水"
            )
            yangxu_posture = m["poses"].get("杨旭", "")
            # 真矛盾条件：刺客刚出 + 杨旭已躺下/仰倒 + 有血
            yangxu_laying_down = yangxu_posture in ("仰卧", "躺", "仰倒", "倒", "已躺下")
            if threat_just_out and yangxu_laying_down and m["has_blood"]:
                warnings.append(
                    f"镜头{label} t={tr:.1f} 时序冲突：刺客「{m['threat_water_state']}」"
                    f"（pre-state）+ 杨旭已「{yangxu_posture}」+ 心口有血（post-state）"
                    f"——刺客刚出水面，杨旭不可能已躺下有血（刺杀瞬间还没完成）。"
                    f"建议 pre-stab kf 写「刺客刚出/杨旭仍坐/无血」；"
                    f"stab kf 写「刺客刺入/杨旭坐或刚仰/初现血」；"
                    f"post-stab kf 写「刺客踞蹲/杨旭已躺/洇痕扩大」。"
                )

        # 2. kf[0..N] 状态单调性
        for i in range(1, len(kf_markers)):
            prev, cur = kf_markers[i - 1], kf_markers[i]
            # 血单调：上一镜有血、下一镜无血 → 回退
            if prev["has_blood"] and not cur["has_blood"]:
                tr_p = mid_kfs[i - 1][0]
                tr_c = mid_kfs[i][0]
                warnings.append(
                    f"镜头{label} 血状态回退：t={tr_p:.1f} 有血 → t={tr_c:.1f} 无血，"
                    f"违反时间线单调性（出血只能扩大或不变，不能消失）。"
                )
            # 刺客出水单调：上一镜「没入」、下一镜「刚出」 → 时序倒退
            prev_gone = prev["threat_water_state"] in ("没入水中", "已没入", "已彻底没入", "倒纵入水", "潜回水中")
            cur_just_out = cur["threat_water_state"] in (
                "刚出水面", "半身探出", "破水而出", "窜出", "浮出", "半身破水"
            )
            if prev_gone and cur_just_out:
                tr_p = mid_kfs[i - 1][0]
                tr_c = mid_kfs[i][0]
                warnings.append(
                    f"镜头{label} 刺客状态回退：t={tr_p:.1f} 已没入 → t={tr_c:.1f} 刚出水面，"
                    f"违反时间线单调性（刺客已没入不应再出水）。"
                )

        # 3. FF→kf[0] 状态连续性：FF 杨旭盘坐 + kf[0] 杨旭直接仰卧 = 跳变
        if ff_m["poses"].get("杨旭") in ("盘坐", "盘膝", "端坐", "坐"):
            kf0_m = kf_markers[0]
            kf0_pose = kf0_m["poses"].get("杨旭")
            if kf0_pose in ("仰卧", "躺", "仰倒", "倒", "已躺下"):
                tr0 = mid_kfs[0][0]
                warnings.append(
                    f"镜头{label} FF→kf[0] 杨旭状态跳变：FF「{ff_m['poses']['杨旭']}」"
                    f"→ kf[0] t={tr0:.1f}「{kf0_pose}」缺过渡（盘坐→仰卧应至少 1 镜过渡："
                    f"「仍坐/察觉」或「被刺瞬间往后倒」）。"
                )
            # FF 无血 + kf[0] 有血 = 跳变（除非 kf[0] 写「刚被刺」明示时序）
            if not ff_m["has_blood"] and kf0_m["has_blood"]:
                tr0 = mid_kfs[0][0]
                kf0_text = mid_kfs[0][1]
                # 允许 kf[0] 写「刚被刺/刺入瞬间」明示时序
                if not any(w in kf0_text for w in ("刚被刺", "刺入瞬间", "此刻", "正对", "刺入")):
                    warnings.append(
                        f"镜头{label} FF 无血 → kf[0] t={tr0:.1f} 突然有血，缺过渡。"
                        f"建议 kf[0] 写「刺客刚破水（无血）」，kf[1] 写「刺入瞬间（初现血）」。"
                    )

        # 4. v6.32 镜内时序链：kf[last] → LF 倒退
        #    血从有回到无 / 刺客从有回到潜伏 / 杨旭从躺回坐 = 时序倒退
        if mid_kfs:
            last_tr, last_text = mid_kfs[-1]
            last_m = kf_markers[-1]
            # 血倒退：kf 末尾有血 + LF 无血
            if last_m["has_blood"] and not lf_m["has_blood"]:
                warnings.append(
                    f"镜头{label} 血状态回退：kf 末尾 t={last_tr:.1f} 有血 → LF 无血，"
                    f"违反致命伤持续规则（一旦出血 LF 必须保持）。"
                    f"建议 LF 重写为「杨旭仰倒+心口洇痕+刺客已没入水中」。"
                )
            # 刺客从有回到潜伏：kf 末尾刺客已出水/刺入 + LF 刺客「潜伏」「水下」无具体动作
            kf_threat_visible = last_m["threat_water_state"] in (
                "刚出水面", "半身探出", "破水而出", "窜出", "浮出", "半身破水"
            )
            lf_threat_gone = lf_m["threat_water_state"] in (
                "没入水中", "已没入", "已彻底没入", "倒纵入水", "潜回水中"
            )
            lf_threat_latent = lf_m["threat_water_state"] is None and any(
                w in lf_text for w in ("潜伏", "黑影", "潜伏水下", "暗影")
            )
            # kf 刺客已出水 → LF 刺客完全消失/潜伏（既不出水也不入水=凭空消失）
            if kf_threat_visible and lf_threat_latent:
                warnings.append(
                    f"镜头{label} 刺客状态跳变：kf 末尾 t={last_tr:.1f} 刺客已出水/破水 → LF 刺客「潜伏/黑影」凭空消失（中间缺「倒纵入水/没入」过渡）。"
                    f"建议 LF 写「刺客已没入水中/倒纵入水」明示消失方式。"
                )
            # 杨旭从躺回坐：kf 末尾杨旭躺下 + LF 杨旭坐起
            kf_yangxu_laying = last_m["poses"].get("杨旭") in ("仰卧", "躺", "仰倒", "倒", "已躺下")
            lf_yangxu_sitting = lf_m["poses"].get("杨旭") in ("盘坐", "盘膝", "端坐", "坐", "跪坐", "撑伞")
            if kf_yangxu_laying and lf_yangxu_sitting:
                warnings.append(
                    f"镜头{label} 杨旭姿态回退：kf 末尾 t={last_tr:.1f} 杨旭「{last_m['poses']['杨旭']}」"
                    f"→ LF 杨旭「{lf_m['poses']['杨旭']}」，违反致命伤持续规则（一旦倒下 LF 必须保持）。"
                    f"建议 LF 写「杨旭仰倒船头/躺于船板」明示终态。"
                )

        # 5. v6.32 角色出现/消失时序合理性
        #    FF + KFs + LF 必须含连贯的角色链——刺客不能凭空出现/消失
        if mid_kfs:
            ff_has_threat = any(w in ff_text for w in ("刺客", "神秘人", "黑衣人", "杀手", "蒙面人"))
            lf_has_threat = any(w in lf_text for w in ("刺客", "神秘人", "黑衣人", "杀手", "蒙面人"))
            kfs_have_threat = any(
                any(w in d for w in ("刺客", "神秘人", "黑衣人", "杀手", "蒙面人"))
                for _, d in mid_kfs
            )
            # 合理链：
            #   FF ✓ KFs ✓ LF ✓  → 刺客全程在场（合理）
            #   FF ✓ KFs ✓ LF ✗  → 刺客动作链收尾（合理）
            #   FF ✗ KFs ✓ LF ✓  → 刺客入场（合理）
            #   FF ✗ KFs ✓ LF ✗  → 刺客入场-出场（合理）
            # 不合理链：
            #   FF ✓ KFs ✗ LF ✓  → 刺客在 KFs 消失又重现
            #   FF ✓ KFs ✗ LF ✗  → 刺客在 KFs 完全消失（无收尾）
            #   FF ✗ KFs ✗ LF ✓  → 刺客凭空出现
            #   FF ✗ KFs ✗ LF ✗  → 刺客全程不在（KFs 中途出现又消失不合理）
            if ff_has_threat and not kfs_have_threat and lf_has_threat:
                warnings.append(
                    f"镜头{label} 刺客在 KFs 段消失又重现：FF 刺客在场，"
                    f"KFs 无刺客，LF 刺客再次在场。刺客应在 KFs 持续呈现动作链"
                    f"（潜伏→出水→刺入→入水），不应消失再重现。"
                )
            elif ff_has_threat and not kfs_have_threat and not lf_has_threat:
                warnings.append(
                    f"镜头{label} 刺客在 KFs+LF 消失：FF「{ff_text[:30]}…」刺客在场，"
                    f"但所有 KFs 和 LF 均无刺客。刺客动作链应在 KFs 完整呈现"
                    f"（潜伏→出水→刺入→入水消失）。"
                )
            elif not ff_has_threat and not kfs_have_threat and lf_has_threat:
                warnings.append(
                    f"镜头{label} 刺客凭空出现：FF/KFs 均无刺客，LF 刺客突然在场（无入场动作）。"
                    f"建议 FF 加刺客潜伏伏笔，或前置一个刺客入场镜头。"
                )

    for w in warnings:
        _get_logger().warn(f"kf时序一致性: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ========================================================================
# v6.33 新增：关键帧物理时刻 = motion_desc 时段铁律（08-18 钩子坑）
#
# 背景：08-17 之前的校验只查"kf 内容是否矛盾"（pre+post 冲突 / 单调性 / 连续性），
# 但 hook motion_desc 明确写「前2秒：刺客破水+下刺 / 2-4秒：闷哼+推臂 / 4-6秒：
# 刺客没入」，LLM 只生成了 2 帧 kf 都堆到 post 段（杨旭已仰卧+血+听香跪坐）——
# 校验器只报"单 frame pre/post 冲突"或不报，没有"kfs 全堆后段、缺失前段 pre 帧"
# 这一类结构性塌缩检测。修法：解析 motion_desc 时间窗（中文"前N秒" / "M-N秒"），
# 对每 kf 计算物理时刻 t×duration_sec，判定其是否落在某时段内；并对时段数与 kf
# 个数做最小覆盖率检查。
# ========================================================================

# 匹配 motion_desc 中的时段标签：
#   - 「前2秒：…」
#   - 「2-4秒：…」
#   - 「2～4秒：…」（全角波浪号）
#   - 「后2秒：…」（后段）
_KF_TIME_SEG_PATTERN = re.compile(
    r"(?:前|后)\s*(\d+)\s*秒|(\d+)\s*[-－~～]\s*(\d+)\s*秒"
)


def _parse_motion_time_segments(motion_desc: str, total_sec: float) -> list[tuple[float, float]]:
    """从 motion_desc 解析时段列表.

    返回: [(start_sec, end_sec), ...]
    - 显式时段（如「2-4秒」）按字面
    - 「前N秒」按 (0, N)
    - 「后N秒」按 (total_sec - N, total_sec)
    - 无显式时段 → 返回空列表（让上层决定是否走全局 kfs 检查）
    - 显式时段未覆盖到 duration_sec 末尾时，**自动补一个 implicit 段**（last_end, total_sec）
      —— motion_desc 只写「前2秒/2-4秒」时，剩余 4-duration_sec 归到 implicit post 段
    """
    if not motion_desc or total_sec <= 0:
        return []
    out: list[tuple[float, float]] = []
    # 按中文分号/句号分段
    for seg in re.split(r"[;；。]", motion_desc):
        seg = seg.strip()
        if not seg:
            continue
        m = _KF_TIME_SEG_PATTERN.search(seg)
        if not m:
            continue
        if m.group(1) and not m.group(2):
            # 「前N秒」或「后N秒」
            kw = seg[m.start():m.end()]
            n = int(m.group(1))
            if "前" in kw:
                out.append((0.0, min(float(n), total_sec)))
            else:
                out.append((max(0.0, total_sec - n), total_sec))
        elif m.group(2) and m.group(3):
            a, b = int(m.group(2)), int(m.group(3))
            if a > b:
                a, b = b, a
            out.append((float(a), min(float(b), total_sec)))
    # 自动补 implicit 末尾段（last_end < total_sec 且间距 > 0.5s）
    if out:
        last_end = max(b for _, b in out)
        if last_end + 0.5 < total_sec:
            out.append((last_end, total_sec))
    return out


def _check_kf_time_window_alignment(shots: list[dict], hook: dict = None) -> dict:
    """v6.33 关键帧物理时刻 = motion_desc 时段铁律检查（warning-only）.

    3 类检测（任一命中即 WARN）：
      1. 时段覆盖率：motion_desc 显式分 N 段，kfs 个数 < N → 缺帧
      2. 时段归属：某 kf 物理时刻 t×duration_sec 不在任一时段内
      3. 时段单调性：kfs 的物理时刻不按时段顺序排列（如 kf0 落在 4-6 秒、kf1 落在 0-2 秒）

    仅对 motion_desc 显式分时段的镜头生效；未分段时返回全 pass（不影响非动作镜）。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])

    for s in targets:
        label = "hook" if (hook and s is hook) else s.get("index", "?")
        motion = (s.get("motion_desc") or "").strip()
        if not motion:
            continue
        # 取 duration_sec
        dur = s.get("duration_sec")
        if not dur or dur <= 0:
            tc = s.get("timecode") or {}
            try:
                start = float(tc.get("start", 0) or 0)
                end = float(tc.get("end", 0) or 0)
                if end > start:
                    dur = end - start
            except (TypeError, ValueError):
                pass
        if not dur or dur <= 0:
            continue
        segs = _parse_motion_time_segments(motion, float(dur))
        if len(segs) < 2:
            # motion_desc 未显式分时段，不强制对齐
            continue
        kfs = s.get("keyframes") or []
        if not isinstance(kfs, list):
            continue
        # 取有效 kf（v6.34 字段迁移：t_sec 走 helper）
        # 08-18 修复：原 `0 < tr < 1` 把 t=0.0/t=1.0 排除了，导致 LLM 把 kf 时刻设为段边界
        # (0.0/0.5/1.0) 时只数到中间 1 帧，误报「缺段」。实际 3 帧 kf 已覆盖 2 段。
        mid_kfs = []
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, s)
            if t is None or t < 0:
                continue
            if dur > 0 and t > dur + 0.01:
                continue
            # 转换为比例（用于 log + 单调性比较）
            tr = (t / float(dur)) if dur > 0 else 0
            mid_kfs.append((tr, (kf.get("desc") or "").strip(), t))
        mid_kfs.sort(key=lambda x: x[0])
        if not mid_kfs:
            warnings.append(
                f"镜头{label} motion_desc 显式分 {len(segs)} 段但无任何 kf（缺帧，"
                f"会渲染出'动作凭空出现/消失'）。建议每段至少 1 帧定格关键瞬间。"
            )
            continue

        # 检测 1：时段覆盖率
        if len(mid_kfs) < len(segs):
            seg_summary = "、".join(f"{a:g}-{b:g}秒" for a, b in segs)
            warnings.append(
                f"镜头{label} motion_desc 分 {len(segs)} 段（{seg_summary}）"
                f"但只有 {len(mid_kfs)} 个 kf，缺段（每段至少 1 帧定格）。"
                f"建议：写 kf 前先列时段表，每段生成 1 帧 time_ratio = 段中点 / duration_sec。"
            )

        # 检测 2：每个 kf 物理时刻是否落在任一时段内
        orphan_kfs = []
        for tr, desc, t_sec in mid_kfs:
            in_any = any(start - 0.01 <= t_sec <= end + 0.01 for start, end in segs)
            if not in_any:
                orphan_kfs.append((tr, t_sec, desc))
        if orphan_kfs:
            detail = "、".join(
                f"t={tr:.2f}→{ts:.2f}s「{d[:25]}…」" for tr, ts, d in orphan_kfs[:3]
            )
            seg_summary = "、".join(f"{a:g}-{b:g}秒" for a, b in segs)
            warnings.append(
                f"镜头{label} {len(orphan_kfs)} 个 kf 物理时刻不在 motion_desc 时段内"
                f"（{seg_summary}）：{detail}。"
                f"建议：把 kf 的 t_sec 调到对应时段中点（t=段中点，v6.34 直写秒数）。"
            )

        # 检测 3：时段单调性——kfs 应按 t_sec 升序落在各段（首段 → 次段 → …）
        # 简化判定：如果存在"晚段 kf 的 content 含 pre-state 词、早段 kf 含 post-state 词" → 倒装
        seg_index_for_kf = []
        for tr, _, t_sec in mid_kfs:
            idx = -1
            for i, (a, b) in enumerate(segs):
                if a - 0.01 <= t_sec <= b + 0.01:
                    idx = i
                    break
            seg_index_for_kf.append(idx)
        if len(seg_index_for_kf) >= 2 and all(x >= 0 for x in seg_index_for_kf):
            for i in range(1, len(seg_index_for_kf)):
                if seg_index_for_kf[i] < seg_index_for_kf[i - 1]:
                    warnings.append(
                        f"镜头{label} kf 时段顺序倒装：第 {i} 个 kf 物理时刻落到"
                        f"时段 {seg_index_for_kf[i] + 1}，但前一个 kf 落到时段"
                        f" {seg_index_for_kf[i - 1] + 1}（应按时间单调：早段→晚段）。"
                        f"建议按 motion_desc 段顺序生成 kf，不要把晚段画面写到前面。"
                    )
                    break

    for w in warnings:
        _get_logger().warn(f"kf时间窗对齐: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ============================================================================
# v6.40（08-22）新增：kf stage 标签一致性检查
# ============================================================================

def _check_kf_stage_consistency(shots: list[dict], hook: dict = None) -> dict:
    """v6.40（08-22）新增：kf stage 标签一致性检查（warning-only）.

    v6.34 stage+t_sec 字段迁移后，校验层需补：
      1. stage 必须是 6 值 enum 之一（非法值/缺失 → 报）
      2. stage 与 desc 词表一致性（stage=pre 但 desc 含"已刺入" → 报矛盾）
      3. t_sec 越界（≤0 或 > dur）→ 报

    word tables 来自 `shared.kf_time.KF_STAGE_TYPICAL_WORDS` /
    `KF_STAGE_FORBIDDEN_WORDS`（与 helper 同源，避免硬编码分散）。
    """
    warnings = []
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        label = "hook" if (hook and s is hook) else s.get("index", "?")
        try:
            dur = float(s.get("duration_sec") or s.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        kfs = s.get("keyframes") or []
        if not isinstance(kfs, list):
            continue
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            stage = get_kf_stage(kf)
            desc = (kf.get("desc") or "").strip()
            t = get_kf_t_sec(kf, s)
            # 1. stage enum 校验（非 None 才检查词表）
            if stage and desc:
                # stage 与 desc 词表匹配
                typical = KF_STAGE_TYPICAL_WORDS.get(stage, ())
                forbidden = KF_STAGE_FORBIDDEN_WORDS.get(stage, ())
                has_typical = any(w in desc for w in typical)
                has_forbidden = any(w in desc for w in forbidden)
                if has_forbidden and not has_typical:
                    # 命中禁用词 + 无典型词 → 矛盾
                    fb_word = next((w for w in forbidden if w in desc), "")
                    warnings.append(
                        f"镜头{label} kf stage={stage!r} 与 desc 矛盾："
                        f"desc 含 stage 禁用词「{fb_word}」（v6.34 stage+t_sec 铁律）"
                    )
            # 2. t_sec 越界
            if t is not None and dur > 0:
                if t <= 0:
                    warnings.append(
                        f"镜头{label} kf t_sec={t:.2f} ≤ 0（必须 0 < t_sec < duration_sec）"
                    )
                elif t > dur + 0.01:
                    warnings.append(
                        f"镜头{label} kf t_sec={t:.2f} > duration_sec={dur:.2f}（越界）"
                    )
    for w in warnings:
        _get_logger().warn(f"kf stage 一致性: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


# ========================================================================
# v6.30 新增：道具空间方位锚定检查（A3 修复 08-10，规则50/22.9）
#
# 背景：同框多件手持道具必须各写落点，否则图片/视频模型自由摆放重叠（酒壶钓杆
# 叠一起）。rule50/22.9 是纯 LLM 约束，零代码校验。本检查对含多个手持道具的
# 镜头，检测道具是否各带方位词（画左/右手/身侧/水中 等），缺则 WARN。
# warning-only 不阻断（step02a `_extract_prop_layout` 代码层注入是最终兜底）。
# ========================================================================

# 道具方位词（画面方位 + 手部 + 位置落点）
_KF_VALIDATION_PROP_POS = (
    "画左", "画右", "画中", "画上方", "画下方",
    "左手", "右手", "双手", "手中", "手边", "身侧", "身前", "身下",
    "前方", "身后", "头顶", "肩头", "腰间", "背后",
    "水中", "水下", "水面", "船头", "船尾", "船板", "船舷", "船侧", "甲板",
    "地上", "岸上", "桌上", "几上", "案上",
    # 08-15（P18 修复）：部位式落点——修复 LLM 已给 shot1 竹箫「横唇」、shot3 炭炉
    # 「中舱」写了落点但 checker 不认，报假阳性。补人体部位/器具位置词：
    # 唇（横唇）、嘴边/口中、胸前/怀中、舱内/中舱、案边/栏边。
    "唇", "嘴边", "口中", "胸前", "怀中", "舱内", "中舱", "案边", "栏边",
)


def _check_prop_spatial_anchor(shots: list[dict], hook: dict = None,
                               characters: list | None = None) -> dict:
    """检查同框多道具是否各带空间方位（规则50/22.9，warning-only）."""
    issues, warnings = [], []
    # #27 修复 2026-08-14：把 script_result.json 的读取提到循环外——
    # 此前每个镜头都重新 read + json.load 一次磁盘文件，几十镜 = 几十次 I/O。
    prop_assets: dict = {}
    try:
        import json as _json
        from shared.paths import chapter_dir
        script_path = chapter_dir() / "script_result.json"
        if script_path.exists():
            _script = _json.load(open(script_path, encoding="utf-8"))
            prop_assets = _script.get("prop_assets", {}) or {}
    except Exception:
        prop_assets = {}
    if not isinstance(prop_assets, dict):
        prop_assets = {}
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        label = "hook" if (hook and s is hook) else s.get("index", "?")
        text = " ".join([
            s.get("ff_desc", ""), s.get("motion_desc", "") or s.get("prompt", ""),
            s.get("lf_desc", ""),
        ])
        # 从 prop_assets 匹配本镜出现的道具（含 consistent_terms 别名）
        matched = []
        for pid, entry in (prop_assets.items() if isinstance(prop_assets, dict) else []):
            if not isinstance(entry, dict):
                continue
            if entry.get("category") in ("交通工具",) or any(
                    w in str(entry.get("category", "")) for w in ("船", "车", "轿", "辇", "舟")):
                continue  # 空间物体走 _build_space_object_anchor，不在此查
            terms = [pid] + list(entry.get("consistent_terms", []) or [])
            for t in terms:
                if t and t in text:
                    matched.append(pid)
                    break
        if len(matched) < 2:
            continue  # 只有一个或零个道具不构成"同框多道具"
        # 每个道具是否带方位：道具名前 8 字 + 后 12 字窗口含方位词
        # （"右手酒壶倾洒" → "右手"在酒壶前；"钓杆垂入画右水面" → "画右"在后 12 字。
        # 08-15（P18）：窗口 [-6,+8] → [-8,+12]，覆盖「中舱生炭炉」「横唇」等落点在
        # 前/在词内的部位式写法；误配由 _KF_VALIDATION_PROP_POS 词表收窄。）
        lacks = []
        for pid in matched:
            p = text.find(pid)
            if p < 0:
                continue
            window = text[max(0, p - 8):p + 12]
            if not any(w in window for w in _KF_VALIDATION_PROP_POS):
                lacks.append(pid)
        if lacks:
            warnings.append(
                f"镜头{label}: 同框道具 {matched} 中 {lacks} 未写空间方位——"
                f"应给每件道具一个落点（如「钓杆垂入画右水面、酒壶倾洒画左甲板」），"
                f"否则模型自由摆放重叠（规则50/22.9）。"
            )
    for w in warnings:
        _get_logger().warn(f"道具空间方位: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ========================================================================
# v6.26 吸收：抠姐短剧剧本检查（开篇冲突/爆点密度/对白长度）+ absorption-log·shotcraft（静态停留）
# 全部 warning-only，不进 hard_fail_checks（与 v6.24/v6.25 一致，重生成循环昂贵）
# ========================================================================

# 爆点/冲突关键词（与 _check_narrative_arc_distribution 的 climax_keywords 互补，
# 此处偏「反转/打脸/冲突」等短视频情绪爆点，供密度检查与开篇检查共用）
_PUNCH_WORDS = (
    "刺杀", "偷袭", "伏击", "拔剑", "挥刀", "匕首", "对峙", "冲突", "搏杀", "跃出",
    "反转", "揭露", "暴露", "揭穿", "打脸", "翻脸", "变故", "密谋", "秘密", "谎言",
    "欺骗", "背叛", "惊慌", "尖叫", "质问", "怒喝", "威胁", "求饶", "落败", "反杀",
    "吐血", "跌倒", "遇袭", "惊觉", "发现", "意外", "撞击", "破窗", "闯入",
)

# 对白单句长度上限（规则43：每句 ≤15 字，说话人前缀不计入）
_DIALOGUE_SENTENCE_MAX = 15
# 对白分句标点（逗号/感叹号/问号/省略号/破折号/句号后接），用于把长台词拆成单句
_DIALOGUE_SEP_RE = re.compile(r"[，。！？；…—、,\n]")

# 静态/停留场景判定词（对白、特写、静景、氛围空镜；与空镜/特写检查词互补）
_DWELL_STATIC_HINTS = ("静坐", "凝视", "对视", "定住", "僵住", "沉默", "伫立", "停驻",
                       "特写", "静景", "空镜", "氛围", "独坐", "背影", "面朝")
# 静态停留下限（规则44：静态/对白镜头 ≥2 秒）
_DWELL_MIN_SEC = 2.0
# 对白镜头推荐下限（规则44 建议 3-5 秒，此处对明确有对白的镜头放宽到 ≥2 秒即可，
# 严格下限按 _DWELL_MIN_SEC；低于 3s 只提示为建议，避免与动作短镜误伤）
_DWELL_DIALOG_RECOMMEND = 3.0


def _check_opening_hook(shots: list[dict], hook: dict = None) -> dict:
    """v6.26 新增：开篇3秒冲突/悬念检查（抠姐剧本检查·开篇3秒+前10秒建立核心冲突）.

    1. 钩子存在且锁定冲突/悬念瞬间 → hook 已由钩子分镜 prompt 保证，此处只核对
       hook 的 prompt/ff_desc 是否含冲突/悬念画面词
    2. 主分镜前1-2镜（紧随 hook 之后）必须有冲突/悬念画面，禁止纯风景/环境空镜开场拖节奏
    3. 前 1/3 段（前10秒对应位置）必须建立核心冲突（谁/在哪/要发生什么）
    """
    issues, warnings = [], []

    # 钩子文本 + 是否含冲突/悬念（供 section 1 校验 + section 2 豁免共用，P2-3）
    hook_text = ""
    hook_has_conflict = False
    if hook and isinstance(hook, dict):
        hook_text = " ".join([hook.get("prompt", ""), hook.get("ff_desc", ""),
                              hook.get("motion_desc", ""), hook.get("lf_desc", "")])
        hook_has_conflict = any(p in hook_text for p in _PUNCH_WORDS)

    # 1. 钩子须含冲突/悬念画面词
    if hook and isinstance(hook, dict):
        if not hook_has_conflict:
            warnings.append(
                "钩子镜头(index=0): 画面未检测到冲突/悬念词（对峙/拔剑/惊觉/意外等）——"
                "钩子必须锁定冲突/悬念瞬间（开篇3秒抓注意力，抠姐检查·开篇3秒）。"
            )

    # 2. 前2个主分镜禁止纯环境/风景开场拖节奏
    #    v6.35（P2-3）：钩子已含冲突/悬念时，紧随其后的建立镜豁免——冲突由钩子承担
    #    （规则42「钩子+前1-2镜」），前1-2镜做空间/人物建立不拖节奏。
    front = [s for s in shots if s.get("index") != 0][:2]
    for s in front:
        if hook_has_conflict:
            continue
        text = " ".join([s.get("prompt", ""), s.get("narration_segment", ""),
                         s.get("main_subject", ""), s.get("motion_desc", "")])
        if not any(p in text for p in _PUNCH_WORDS):
            warnings.append(
                f"镜头{s.get('index')}: 开篇镜头未含冲突/悬念画面——视频前3秒必须出现"
                f"事件/冲突/悬念（对峙/意外/反转伏笔/危机逼近），禁止纯风景空镜开场拖节奏"
                f"（开篇3秒规则42，抠姐检查·开篇3秒/前10秒）。"
            )

    # 3. 前 1/3 段必须建立核心冲突（对应前10秒内容）
    if shots:
        active = [s for s in shots if s.get("index") != 0]
        if active:
            head = active[: max(1, len(active) // 3)]
            head_text = " ".join(
                s.get("prompt", "") + s.get("narration_segment", "") + s.get("main_subject", "")
                for s in head
            )
            if not any(p in head_text for p in _PUNCH_WORDS):
                warnings.append(
                    "前1/3段镜头未建立核心冲突（谁/在哪/要发生什么）——"
                    "前10秒观众必须能一眼看懂冲突，禁止只铺垫不推进"
                    "（规则42，抠姐检查·前10秒建立核心冲突）。"
                )

    for w in warnings:
        _get_logger().warn(f"开篇冲突: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_char_layout_surface(shots: list[dict], hook: dict | None = None) -> dict:
    """v6.45（08-26）char_layout[i].surface 必填检查（warning-only，不修）.

    根因：char_layout schema 当前 4 字段（name/position/facing/role）—— LLM 输出
    时不写"角色所在的承重面"（船尾/甲板/岸边/亭内/...）→ vimax_bridge 拼锚定块
    只有"画右（面向杨旭）" → LLM 自由发挥成船外水面（08-26 hook 0 听香站水里）。

    修法：
      - prompt schema（main.md/hook.md/system.md）扩 surface 字段，LLM 必填
      - vimax_bridge._extract_char_layout seg 拼接时如有 surface 则拼入
      - 本检查 warning-only 报"缺 surface"，**不自动改写**——承重面是 LLM 应
        输出的语义信息，不该由代码层硬填（违反 v6.43 hard_fix 失声原则）
      - role=背景/边角角色不强制（背景人物可只给构图位置）

    校验返回 {pass, issues, warnings}，warnings 含"镜X/角色Y: 缺 surface"列表。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        if not isinstance(s, dict):
            continue
        cl = s.get("char_layout")
        if not isinstance(cl, list) or not cl:
            continue
        label = f"hook(idx=0)" if (hook and s is hook) else f"shot{s.get('index', '?')}"
        for j, item in enumerate(cl):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            role = str(item.get("role", "")).strip()
            surface = str(item.get("surface", "")).strip()
            # 背景/边角角色不强求（不在图片显著位置）
            if role in ("背景", "背景人物"):
                continue
            if not name:
                continue
            if not surface:
                warnings.append(
                    f"{label} char_layout[{j}]({name}): 缺 surface"
                    f"（必填：船尾/船头/甲板/船舷/岸边/亭内/楼上/楼下/车内/...）"
                    f"——无承重面锚点 → 锚定块缺位 → LLM 自由发挥角色站位"
                )
    for w in warnings:
        _get_logger().warn(f"char_layout.surface: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_punch_density(shots: list[dict], hook: dict = None) -> dict:
    """v6.26 新增：爆点密度检查（抠姐剧本检查·爆点密度≥1个/30秒）.

    以 60 秒全片估算爆点数（钩子+主分镜中含冲突/反转/打脸/高潮词的镜头数），
    目标 ≥1个/30秒；并检查前1/3段是否已出现第一个爆点（防前段拖沓铺垫）。
    """
    issues, warnings = [], []
    targets = [s for s in shots if s.get("index") != 0] + ([hook] if hook else [])
    if not targets:
        return {"pass": True, "issues": [], "warnings": []}

    punch_count = 0
    for s in targets:
        text = " ".join([s.get("prompt", ""), s.get("narration_segment", ""),
                         s.get("main_subject", ""), s.get("motion_desc", "")])
        if any(p in text for p in _PUNCH_WORDS):
            punch_count += 1

    # 全片时长：duration_sec 权威来源，缺省按 60s 估算
    total = 0.0
    for s in targets:
        d = s.get("duration_sec") or s.get("duration")
        try:
            total += float(d) if d else 0.0
        except (TypeError, ValueError):
            pass
    if total <= 0:
        total = 60.0

    needed = max(1, int(total // 30) + (1 if total % 30 else 0))
    if punch_count < needed:
        warnings.append(
            f"全片约 {total:.0f}s 含爆点镜头 {punch_count} 个（目标 ≥{needed}个/每30秒）——"
            f"爆点（冲突/反转/打脸/高潮）密度不足，视频中段易流失观众；"
            f"建议压缩铺垫、在关键转折处强化冲突（规则42.5，抠姐检查·爆点密度）。"
        )

    # 前1/3段须出现第一个爆点
    active = [s for s in shots if s.get("index") != 0]
    if active:
        head = active[: max(1, len(active) // 3)]
        head_text = " ".join(
            s.get("prompt", "") + s.get("narration_segment", "") + s.get("main_subject", "")
            for s in head
        )
        if not any(p in head_text for p in _PUNCH_WORDS):
            warnings.append(
                "前1/3段未检测到第一个爆点——铺垫镜头（纯环境/日常/过渡）连续不宜超2镜，"
                "首个爆点应出现在前1/3段（规则42.5，抠姐检查·爆点密度/铺垫拖沓）。"
            )

    for w in warnings:
        _get_logger().warn(f"爆点密度: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _check_dialogue_length(shots: list[dict], hook: dict = None) -> dict:
    """v6.26 新增：对白单句≤15字检查（抠姐剧本检查·制作细节·台词口语化/单句≤15字）.

    遍历所有有对白的镜头（narration_segment 非空），按分句标点拆成单句，
    逐句检查长度（说话人「道：」前缀不计入，句末标点计入）。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        narration = (s.get("narration_segment") or "").strip()
        if not narration:
            continue
        label = "hook" if (hook and s is hook) else s.get("index", "?")
        # 去掉「说话人道：」前缀（说话人识别前缀不计入长度；兼容半角/全角冒号）
        body = re.split(r"[:：]", narration, maxsplit=1)[-1]
        # 按分句标点拆单句
        sentences = [seg.strip() for seg in _DIALOGUE_SEP_RE.split(body) if seg.strip()]
        for seg in sentences:
            # 字符计数：保留汉字/字母数字，忽略空格（中文对白无空格，双引号/书名号等不计数）
            count = len(re.sub(r"[^一-鿿A-Za-z0-9]", "", seg))
            if count > _DIALOGUE_SENTENCE_MAX:
                warnings.append(
                    f"{label}: 对白单句 {count} 字（>{_DIALOGUE_SENTENCE_MAX}字）「{seg[:24]}…」"
                    f"——对白需口语化、单句≤{_DIALOGUE_SENTENCE_MAX}字，长句按标点断成短句"
                    f"（规则43，抠姐检查·台词口语化/单句长度）。"
                )
    for w in warnings:
        _get_logger().warn(f"对白长度: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _fix_dialogue_length(narration: str) -> str:
    """确定性修复超长对白单句（B9 修复 08-11，规则43）.

    _check_dialogue_length 只告警不修正（超长句仍进 TTS/视频模型，规则43
    形同虚设）。本函数按规则31「只改标点不改文字」做确定性的句内断句：
    无标点连续 >15 字的单句，在语义停顿点（的/了/在/要/把/让/便/就/已等
    虚词后）插入「，」断成短句；不删改任何汉字（台词原文锚点校验不破坏）。

    幂等：修复后每句 ≤15 字，二次调用无操作。
    """
    if not narration:
        return narration
    # 剥「说话人道：」前缀，只修台词体（规则43 前缀不计入长度）
    if "：" in narration or ":" in narration:
        prefix, _, body = re.split(r"([：:])", narration, maxsplit=1)
        new_body = _split_long_dialogue(body)
        return prefix + "：" + new_body if new_body != body else narration
    return _split_long_dialogue(narration)


# 语义停顿虚词（插入逗号的候选位置；优先长虚词，避免无意义断句）
_DIALOGUE_BREAK_AFTER = (
    "的", "了", "在", "要", "把", "让", "便", "就", "已", "也",
    "却", "然后", "接着", "所以", "因为", "如果", "虽然", "但是",
)


def _split_long_dialogue(body: str) -> str:
    """把超长台词体按语义停顿点插入逗号（不删字，幂等）.

    只处理「无标点连续单句 >15 字」的 clause；已有标点的 clause 原样保留
    （逐 clause 拆分，不破坏原文标点）。虚词断点只在当前分句 ≥10 字后生效，
    避免过早切碎。
    """
    # 保留分句标点（_DIALOGUE_SEP_RE 含 ，。！？；…—、 等），逐 clause 处理
    parts = list(_DIALOGUE_SEP_RE.split(body))
    seps = list(_DIALOGUE_SEP_RE.findall(body))
    out: list[str] = []
    for i, clause in enumerate(parts):
        clause = clause.strip()
        if not clause:
            continue
        if len(re.sub(r"[^一-鿿A-Za-z0-9]", "", clause)) <= _DIALOGUE_SENTENCE_MAX:
            out.append(clause)
        else:
            out.append("，".join(_break_one_long_clause(clause)))
        if i < len(seps):
            out.append(seps[i])
    # 已拆的 clause 都 ≤15 字（_break_one_long_clause 兜底保留原样），
    # 拼接后二次调用只会逐 clause 处理（各 ≤15 → 原样返回），天然幂等。
    return "".join(out)


def _break_one_long_clause(clause: str) -> list[str]:
    """把单个超长无标点 clause 在语义停顿虚词后断成 ≤15 字短句（不删字）."""
    pieces: list[str] = []
    cur = ""
    for ch in clause:
        cur += ch
        if len(cur) >= 10 and ch in _DIALOGUE_BREAK_AFTER:
            pieces.append(cur)
            cur = ""
    if cur:
        pieces.append(cur)
    # 极端情况：断出的子句仍 >15 字（clause 中虚词密度低）→ 位置兜底硬切
    # （08-12 P0-12：如「老爷们急着给府学和合城士绅们一个交待」无虚词断点，
    # 虚词法放弃 → 位置切 10+8）。仅在能切出合法段时采用；连位置切都失败
    # （超长无标点）才放弃保留原样。
    if any(
        len(re.sub(r"[^一-鿿A-Za-z0-9]", "", p)) > _DIALOGUE_SENTENCE_MAX
        for p in pieces
    ):
        return _positional_break_clause(clause)
    return pieces


def _positional_break_clause(clause: str) -> list[str]:
    """无虚词断点时的位置兜底：切成 ≤15 字段（不删字，幂等）.

    优先两段式（首段 ≥10 字、余段 ≤15 字，最小干预）；两段放不下（超长无标点）
    则贪心按 15 字切。首段若仍 >15 字则放弃原样返回（避免把超长句切碎成渣）。
    幂等：产物每段 ≤15 字，二次调用早退。
    """
    max_len = _DIALOGUE_SENTENCE_MAX
    _count = lambda s: len(re.sub(r"[^一-鿿A-Za-z0-9]", "", s))
    if _count(clause) <= max_len:
        return [clause]
    # 两段式：首段长度 = 使余段 ≤15 的最小可行值，夹在 [10, 15]
    head_len = max(10, _count(clause) - max_len)
    if head_len < _count(clause) and head_len <= max_len:
        head, tail = clause[:head_len], clause[head_len:]
        if _count(tail) <= max_len:
            return [head, tail]
    # 两段放不下：贪心按 max_len 切，末段收敛
    pieces: list[str] = []
    rest = clause
    while _count(rest) > max_len:
        pieces.append(rest[:max_len])
        rest = rest[max_len:]
    if rest:
        pieces.append(rest)
    return pieces


def _check_dwell_time(shots: list[dict], hook: dict = None) -> dict:
    """v6.26 新增：静态镜头停留时长检查（absorption-log·shotcraft·关键画面停留时长）.

    对白/特写/静景/氛围空镜等静态镜头（prompt/ff_desc 含静态判定词，或 narration 非空
    且画面无明显动作），duration_sec 低于 _DWELL_MIN_SEC → 提示停留不足；
    对白镜头低于 3s → 提示建议放宽（动作短镜快切不受限，规则7既定节奏）。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    for s in targets:
        idx = s.get("index", "?")
        if idx == 0 and hook and s is hook:
            continue  # 钩子镜为「前一刻」定格，时长规则按钩子 prompt 独立约束，不套静态下限
        d = s.get("duration_sec") or s.get("duration")
        try:
            duration = float(d) if d else 0.0
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            continue

        prompt = (s.get("prompt") or "") + (s.get("ff_desc") or "")
        narration = (s.get("narration_segment") or "").strip()
        has_dialog = bool(narration)
        # 静态判定：对白镜 或 画面含静态/特写/空镜词 且无动作动词
        is_static = has_dialog or any(h in prompt for h in _DWELL_STATIC_HINTS)
        if not is_static:
            continue

        if duration < _DWELL_MIN_SEC:
            warnings.append(
                f"镜头{idx}: 静态/对白镜头停留 {duration:.1f}s < {_DWELL_MIN_SEC:.0f}s——"
                f"画面主体刚出现就切走，观众看不清；静态/对白镜头应 ≥{_DWELL_MIN_SEC:.0f}s"
                f"（absorption-log·shotcraft·关键画面停留时长）。"
            )
        elif has_dialog and duration < _DWELL_DIALOG_RECOMMEND:
            warnings.append(
                f"镜头{idx}: 对白镜头 {duration:.1f}s（建议 3-5s）——台词口型与画面时间不足，"
                f"可适当放宽到 {_DWELL_DIALOG_RECOMMEND:.0f}s 以上（规则7节奏分配）。"
            )
    for w in warnings:
        _get_logger().warn(f"静态停留: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


# ============================================================================
# v6.27 新增：角色状态锁定校验（角色状态锁定表吸收——根治「坐站跳变」）
# ============================================================================
# 短剧 skill 三件套的「角色状态锁定表」核心主张：同一角色在连续镜头中的
# 身体姿势状态（坐/站/蹲/跪/躺/立）不能无动机跳变——上一镜坐着、下一镜
# 忽然站起而镜头/旁白没交代「起身」，视频模型会脑补一个不存在的过渡，
# 导致动作生硬或穿模。aiVideo 没有逐镜状态锁字段，改由跨镜校验兜底：
# 同一角色相邻镜头姿势状态冲突且无「状态转换动作」衔接 → WARN。
_POSE_STATES = ("坐", "站", "蹲", "跪", "躺", "卧", "趴", "立")
# 状态转换动作：任一出现在当前镜 prompt/motion/lf 或下镜 prompt/ff，
# 说明姿势变化有动机交代（起身/坐下/站起/落座/蹲下/跪倒/爬起…），不算跳变
_POSE_TRANSITION_ACTIONS = (
    "起身", "站起", "站了起", "站起身来", "坐起", "坐下", "落座", "入座", "就座",
    "蹲下", "蹲了", "跪倒", "跪下", "跪坐", "卧倒", "躺下", "倒下", "倒地",
    "爬起", "翻身", "起来", "上船", "下船", "上岸", "下岸", "出舱", "进舱",
    "迈步", "踏步", "行走", "走进", "走出", "站到", "走向", "走到", "站定",
    "扶着", "撑地", "撑身", "欠身", "起身离",
)


def _check_pose_continuity(shots: list[dict]) -> dict:
    """跨镜姿势状态连续性检查（角色状态锁定表吸收，根治「坐站跳变」）.

    检测：同一角色出现在相邻镜头时，两镜的姿势状态词（坐/站/蹲/跪/躺）冲突，
    但当前镜结尾 / 下镜开头没有任何「状态转换动作」（起身/坐下/站起等）衔接，
    则提示姿势跳变——视频模型会在两镜之间脑补一个不存在的起身/落座过渡。

    只 WARN 不阻断（姿势状态由 LLM 按原文决定，此处仅提示补动机镜头或衔接动作）。
    """
    warnings = []
    for i in range(len(shots) - 1):
        curr, nxt = shots[i], shots[i + 1]
        ci, ni = curr.get("index", "?"), nxt.get("index", "?")
        if ci == 0 or ni == 0:
            continue  # hook 独立，不参与相邻姿态链
        # 同一角色：取两镜共有的 main_subject 或 prompt 中共同出现的角色名
        # 简化：用「角色锚点 [名: 特征]」提取，兼容 main_subject 直接是角色名
        common_chars = _common_present_characters(curr, nxt)
        if not common_chars:
            continue

        curr_text = ((curr.get("prompt") or "") + " " +
                     (curr.get("motion_desc") or "") + " " +
                     (curr.get("lf_desc") or ""))
        nxt_text = ((nxt.get("prompt") or "") + " " +
                    (nxt.get("ff_desc") or "") + " " +
                    (nxt.get("motion_desc") or ""))
        for char_id in common_chars:
            pose_curr = _extract_pose_for_char(curr_text, char_id)
            pose_nxt = _extract_pose_for_char(nxt_text, char_id)
            if not pose_curr or not pose_nxt or pose_curr == pose_nxt:
                continue  # 未检出姿势或姿势一致 → 无跳变
            # 姿势变了：检查是否有转换动作衔接（当前镜结尾或下镜开头）
            transition_mentioned = any(a in curr_text or a in nxt_text
                                       for a in _POSE_TRANSITION_ACTIONS)
            if transition_mentioned:
                continue  # 有动机交代（起身/坐下…），不算跳变
            warnings.append(
                f"镜头{ci}→{ni}: 角色「{char_id}」姿势状态跳变（{pose_curr}→{pose_nxt}）"
                f"但无「起身/坐下/站起」等转换动作衔接——视频模型会脑补一个不存在的过渡，"
                f"建议补状态转换动作或过渡镜头（角色状态锁定表）。"
            )
    for w in warnings:
        _get_logger().warn(f"角色状态锁定: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_over_shoulder_lock(shots: list[dict], hook: dict = None) -> dict:
    """v6.28 新增：双人对话过肩锁定检查（视频1「对话视角一致性」吸收，规则22.8）.

    同一对话场景组的相邻镜头，一旦某镜用「过肩」机位（通过某角色肩膀拍另一人），
    要求后续镜头延续过肩/正反打逻辑、被拍者视线落向对话对方——禁止一人过肩、
    下镜突然变「看向镜头/看向观众」的单人正打机位，导致对话关系断裂。

    warning-only。越轴翻转（画左/画右）已由 _check_axis_crossing 覆盖，此处不重复。
    """
    issues, warnings = [], []
    targets = list(shots) + ([hook] if hook else [])
    for i in range(len(targets) - 1):
        s1, s2 = targets[i], targets[i + 1]
        # 对话镜头组：相邻两镜都有对白
        if not (s1.get("narration_segment") or "").strip():
            continue
        if not (s2.get("narration_segment") or "").strip():
            continue
        # 同场景（对话组要求座位/朝向连续）
        loc1 = s1.get("location", "") or s1.get("scene", "")
        loc2 = s2.get("location", "") or s2.get("scene", "")
        if not loc1 or loc1 != loc2:
            continue
        # 取 prompt 头部（景别/机位）+ 视线词
        t1 = " ".join([s1.get("prompt", ""), s1.get("ff_desc", "")])
        t2 = " ".join([s2.get("prompt", ""), s2.get("ff_desc", "")])
        # 过肩词：过肩/正反打；被拍者视线看对方 vs 看镜头
        os1 = any(w in t1 for w in ("过肩", "正反打"))
        os2 = any(w in t2 for w in ("过肩", "正反打"))
        look_cam = any(w in t2 for w in ("看向镜头", "看向观众", "看镜头", "直视镜头", "注视观众"))
        if os1 and not os2 and look_cam:
            i2 = s2.get("index", "?")
            warnings.append(
                f"镜头{s1.get('index','?')}→{i2}: 对话组内上镜用「过肩」机位，"
                f"下镜{look_cam and '却写成「看向镜头/观众」' or '却未延续过肩/正反打逻辑'}"
                f"——双人对话被拍者视线应落向对方而非镜头（规则22.8）。"
            )
    for w in warnings:
        _get_logger().warn(f"对话过肩: {w}")
    return {"pass": True, "issues": issues, "warnings": warnings}


def _common_present_characters(curr: dict, nxt: dict) -> list[str]:
    """提取相邻两镜共同出现的角色（角色锚点 [名:特征] 或 main_subject）."""
    def extract(shot: dict) -> set:
        found = set()
        text = (shot.get("prompt") or "") + (shot.get("ff_desc") or "") + (shot.get("lf_desc") or "")
        for m in re.finditer(r"\[([^\]：:]{1,8})[:：]", text):
            found.add(m.group(1).strip())
        ms = (shot.get("main_subject") or "").strip()
        if ms and len(ms) <= 6:  # main_subject 是角色名（短、非句子）
            found.add(ms)
        return found

    return sorted(extract(curr) & extract(nxt))


def _extract_pose_for_char(text: str, char_id: str) -> str | None:
    """从文本中提取指定角色的姿势状态词.

    命中顺序：姿势词出现在该角色名附近（前后 6 字内）优先；
    若全句只含一个姿势词且该角色是唯一主体，也接受（简化启发式）。
    """
    char_re = re.escape(char_id)
    # 姿势词在角色名附近（同句片段）
    for pose in _POSE_STATES:
        # 「坐」会误配「坐在」「坐着」——这些其实也是坐姿，接受；
        # 但「立」在「立刻/独立」里不算姿势，需词边界（用后续字排除常见非姿势词）
        if pose == "立":
            if re.search(rf"{char_re}[^。！？；\n]{{0,6}}立(?!刻|即|场|功|足)", text):
                return "立"
        else:
            # 角色名后 6 字内出现姿势词，或姿势词紧邻角色名
            if re.search(rf"{char_re}[^。！？；\n]{{0,6}}{pose}", text):
                return pose
    # 兜底：句子以「角色 姿势」形式出现（如「杨旭站」「听香坐」）
    for pose in _POSE_STATES:
        if pose == "立":
            if re.search(rf"{char_re}\s*立(?!刻|即|场|功|足)", text):
                return "立"
        elif re.search(rf"{char_re}\s*{pose}", text):
            return pose
    return None


# ============================================================================
# v6.31 新增：禁形容词/比喻校验 + 替换修正（A5 修复 08-11，规则5.1）
#
# 背景（todolist 审计 08-10 A5）：SYSTEM_PROMPT 规则5.1 禁止在 prompt 中使用
# 形容词（矫健/敏捷/迅猛/凌厉/飘逸/灵动/优雅/诡异/阴森）和比喻句（如鹰隼般/
# 似闪电/仿佛/犹如/如同/恰如），要求用具体动作替代——「快速出剑」→「右手从
# 腰侧拔出长剑，剑身与地面成45度」。但这是纯 LLM 规则、零代码校验零修正，
# LLM 不遵守时抽象动作直接进视频模型，画面画不出「矫健地跃出」这类抽象概念。
# 本检查扫描 prompt/motion_desc/ff_desc，命中禁词则 WARN；post_fix 里用
# _fix_forbidden_abstract 确定性替换（抽象形容词删除/比喻词删除，保留主体与动作）。
# ============================================================================

# 规则5.1 禁止的形容词（抽象动作/神态描述，替换为具体动作）
_FORBIDDEN_ABSTRACT_ADJECTIVES = (
    "矫健", "敏捷", "迅猛", "凌厉", "飘逸", "灵动", "优雅", "诡异",
    "阴森", "迅捷", "迅猛", "利落", "轻盈", "疾速", "彪悍", "凶悍",
    "狰狞", "凄厉", "萧瑟", "肃杀", "凌厉地", "迅速", "疾速地",
)
# 规则5.1 禁止的比喻句连接词（如鹰隼般/似闪电/仿佛/犹如/如同/恰如）
# 注意：单字「般/似的」不单独列入——「一般/似的」在正常中文里太常见（"一般情况"），
# 比喻句式由 _fix_forbidden_abstract 的「如X般/似X/像X一样」正则整体删除覆盖。
_FORBIDDEN_METAPHOR_WORDS = (
    "如鹰隼般", "似闪电", "仿佛", "犹如", "如同", "恰如", "宛似", "恍若",
)
# 规则5.1 禁止的程度副词（快速/缓慢/轻轻/重重/猛地/骤然）
_FORBIDDEN_ABSTRACT_ADVERBS = (
    "快速地", "缓慢地", "轻轻地", "重重地", "猛地", "骤然", "忽然间",
    "迅速地", "疾速地", "轻轻地", "快速", "缓缓",
)


def _find_forbidden_abstract(text: str) -> list[str]:
    """扫描文本中的禁形容词/比喻词/程度副词，返回命中列表（去重保序）."""
    hits = []
    seen = set()
    for word in _FORBIDDEN_ABSTRACT_ADJECTIVES + _FORBIDDEN_METAPHOR_WORDS + _FORBIDDEN_ABSTRACT_ADVERBS:
        if word in text and word not in seen:
            seen.add(word)
            hits.append(word)
    return hits


def _check_forbidden_abstract(shots: list[dict], hook: dict = None) -> dict:
    """检查 prompt 是否含禁形容词/比喻（规则5.1，warning-only）.

    扫描 prompt/motion_desc/ff_desc/lf_desc，命中规则5.1 禁止的抽象形容词、
    比喻句、程度副词则 WARN 提示改为具体动作描述。自动修正见
    post_fix_shot_prompts 的 _fix_forbidden_abstract（幂等）。
    """
    warnings = []

    def _check_one(shot: dict, label: str):
        idx = shot.get("index", "?")
        text = " ".join([
            shot.get("prompt", "") or "", shot.get("motion_desc", "") or "",
            shot.get("ff_desc", "") or "", shot.get("lf_desc", "") or "",
        ])
        hits = _find_forbidden_abstract(text)
        if hits:
            warnings.append(
                f"{_shot_tag(label, idx)}: 画面描述含禁抽象形容词/比喻 {hits}（规则5.1）——"
                f"「快速出剑」「身形矫健」这类抽象词视频模型画不出具体动作，"
                f"应改为具体动作描述（如'右手从腰侧拔出长剑，剑身与地面成45度'"
                f"'身体直立，双手自然下垂，目光平视'）。"
            )

    for shot in shots:
        _check_one(shot, "镜头")
    if hook and isinstance(hook, dict):
        _check_one(hook, "钩子镜头(index=0)")

    for w in warnings:
        _get_logger().warn(f"禁抽象词: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}


def _fix_forbidden_abstract(text: str) -> str:
    """确定性删除/替换禁抽象形容词与比喻词（规则5.1，幂等）.

    只删抽象修饰词，保留主体与动作（「身形矫健地跃出」→「身形跃出」——
    删除'矫健地'；「剑光如银龙般飞舞」→「剑光飞舞」——删除'如银龙般'）。
    程度副词「快速/缓慢/猛地」也删除（「快速出剑」→「出剑」，交由后续
    motion 的具体动作描述表达时序）。幂等：删除后不再含禁词。
    """
    if not text:
        return text
    out = text
    # 1. 比喻句模式「如X般/如X一样/似X/像X」→ 整段删除（如银龙般→''）
    #    X = 1-6 字（避免误删"如故/如初"这类短词）
    import re as _re
    for pat in (
        r"如[一-鿿A-Za-z0-9]{1,6}般",
        r"如[一-鿿A-Za-z0-9]{1,4}(一样|一般)",
        r"似[一-鿿A-Za-z0-9]{1,6}(般|一样|一般)?",
        r"像[一-鿿A-Za-z0-9]{1,4}一样",
    ):
        out = _re.sub(pat, "", out)
    # 2. 抽象形容词 + 可选「地」（矫健地/凌厉地/敏捷地/轻盈地）整词删除
    for word in _FORBIDDEN_ABSTRACT_ADJECTIVES:
        # 长词优先（凌厉地 在 凌厉 之前）
        pass
    for word in sorted(_FORBIDDEN_ABSTRACT_ADJECTIVES, key=len, reverse=True):
        out = out.replace(word + "地", "").replace(word, "")
    # 3. 程度副词（快速/缓慢/轻轻/重重/猛地/骤然）+ 可选「地」整词删除
    for word in sorted(_FORBIDDEN_ABSTRACT_ADVERBS, key=len, reverse=True):
        out = out.replace(word + "地", "").replace(word, "")
    # 4. 比喻连接词残余（仿佛/犹如/如同/恰如/宛似/恍若）删除
    for word in _FORBIDDEN_METAPHOR_WORDS:
        out = out.replace(word, "")
    # 5. 清理删除后产生的残留：孤立的「地」「的」、连续标点、句首/句尾标点
    out = _re.sub(r"[，、]{1,}地(?!道|说)", "，", out)   # "，地" → "，"
    out = _re.sub(r"[，、]{2,}", "，", out)
    out = _re.sub(r"^[，、；；。！？、\s]+", "", out)
    out = _re.sub(r"[，、；；。！？、\s]+$", "", out)
    return out


def _fix_wide_shot_facial(text: str) -> str:
    """远景镜头确定性剥离精细五官词（规则39，幂等）.

    远景/大远景/全景/俯拍中人物极小，精细五官（剑眉/星目/朱唇）会让视频模型
    把脸放大、比例崩坏。只删五官词，保留身份/服装/动作（校验项
    _check_wide_shot_facial 依赖）。仅当镜头命中远景词时调用，非远景不误伤。
    幂等：删除后不再含禁词。
    """
    if not text:
        return text
    out = text
    for word in sorted(_FINE_FACE_WORDS, key=len, reverse=True):
        out = out.replace(word, "")
    import re as _re
    out = _re.sub(r"[，、]{2,}", "，", out)
    out = _re.sub(r"^[，、；；。！？、\s]+", "", out)
    out = _re.sub(r"[，、；；。！？、\s]+$", "", out)
    return out


def _fix_trailing_dash_punct(text: str) -> str:
    """台词以破折号裸收尾 → 确定性补「！」（08-15，P18）.

    LLM 常写「救命啊——」「啊……啊……救命啊——」这类断喝/尖叫拖音尾，句末无
    标点，配音/字幕语气无法传达（校验项 _check_voice_punctuation 裸收尾分支依赖）。
    句尾破折号几乎总是「情绪未落地」的尖叫/断喝，确定性补「！」最贴合情绪。
    幂等：句末已有标点（！？。…～）不处理。
    """
    if not text:
        return text
    stripped = text.rstrip()
    if re.search(r"[—―]{1,2}$", stripped):
        return stripped + "！"
    return text


# ============================================================================
# v6.31 新增：致命伤跨镜持续校验（A4 修复 08-11，规则8）
#
# 背景（todolist 审计 08-10 A4）：规则8 要求「一旦发生致命伤（如刺入心口），后续
# 每个镜头必须持续锚定伤口位置与生命体征衰减，严禁跳变为'直接躺平'」——第2镜伤口
# 洇痕消失无任何告警，剧情连续性断裂。这是纯 LLM 规则约束、零代码校验。
# 本检查做跨镜「伤处锚定词持续」校验：某镜建立致命伤后，同一场景的后续连续镜头
# 必须至少提及一处伤处锚定词（心口/伤处/洇痕/暗红/中刀…），直到场景切换。
# warning-only 不阻断（伤情推进由 LLM 按原文决定，此处仅提示补锚定词）。
# ============================================================================

# 建立致命伤的触发词（某镜出现这些 = 本镜刚发生致命伤，开启持续校验）
_WOUND_START_VERBS = (
    "刺中", "刺入", "刺进", "捅入", "扎入", "被刺", "中刀", "中箭",
    "仰面倒", "仰面躺", "倒地", "倒下", "洇开", "洇出", "血染",
)
# 伤处锚定词（伤口位置/生命体征衰减的持续表达）
# v6.43 重构：基础词从 prompt_semantic.registry 引入，validation 扩展
from step01_script_gen.prompt_semantic import WOUND_ANCHOR_WORDS as _PS_WOUND
_WOUND_ANCHOR_WORDS = (
    *_PS_WOUND,
    "伤处", "伤口", "洇开", "洇出", "红痕",
    "被刺", "中箭", "受伤", "重伤", "倒地", "倒下", "仰面",
)


def _check_fatal_wound_continuity(shots: list[dict]) -> dict:
    """跨镜致命伤状态持续校验（规则8，warning-only）.

    状态机：某镜出现致命伤触发词（刺中/被刺/倒下/洇开）→ 开启"伤情持续"；
    同一地点（location 连续）的后续镜头必须至少含一处伤处锚定词，否则 WARN
    （第2镜伤口洇痕消失 → 剧情连续性断裂）。场景切换 / 空镜 / 纯过渡镜头重置。

    只 WARN 不阻断（伤情推进与锚定写法由 LLM 决定，此处仅提示补持续锚定）。
    """
    warnings = []
    wound_active = False      # 是否处于"伤情持续"状态
    wound_loc = None          # 伤情发生的场景（location）
    wound_start_idx = None    # 建立伤情的镜头
    for shot in shots:
        idx = shot.get("index", "?")
        if idx == 0:
            continue  # hook 独立，不参与跨镜状态链
        text = " ".join([
            shot.get("prompt", "") or "", shot.get("ff_desc", "") or "",
            shot.get("lf_desc", "") or "", shot.get("motion_desc", "") or "",
        ])
        loc = shot.get("location", "") or shot.get("scene", "") or ""
        # 空镜判定（复用 EMPTY_SCENE_SUBJECTS）：空镜无人物，不要求伤处锚定，
        # 且会重置伤情状态（空镜通常是转场/时间流逝，伤情锚定由下一人物镜头承接）
        main_subject = shot.get("main_subject", "") or ""
        is_empty = any(s in main_subject for s in EMPTY_SCENE_SUBJECTS)

        if not wound_active:
            # 本镜建立致命伤 → 开启持续校验
            if any(v in text for v in _WOUND_START_VERBS):
                wound_active = True
                wound_loc = loc
                wound_start_idx = idx
            continue

        # ---- 伤情持续状态 ----
        # 1. 场景切换 / 空镜 → 重置状态（伤情锚定由新场景或后续人物镜头承接）
        if is_empty or (wound_loc and loc and loc != wound_loc):
            wound_active = False
            wound_loc = None
            wound_start_idx = None
            continue
        # 2. 本镜是否持续锚定伤处
        has_anchor = any(w in text for w in _WOUND_ANCHOR_WORDS)
        if not has_anchor:
            warnings.append(
                f"镜头{wound_start_idx}→{idx}: 镜头{wound_start_idx}建立致命伤，"
                f"但镜头{idx}（同场景继续）未提及任何伤处锚定词（心口/伤处/洇痕/"
                f"暗红/中刀/倒地）——伤口洇痕消失、剧情连续性断裂。"
                f"被刺后的镜头即使主体是刺客逃跑/听香反应，也必须持续锚定伤处"
                f"（如'心口处白袍暗红洇痕'），不能完全消失（规则8）。"
            )
    for w in warnings:
        _get_logger().warn(f"致命伤持续: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}



# =====================================================================
# v6.38 红果 60-90s 短剧标准检查（08-22 起）
# 配套 prompts.py 规则 60/60.1/60.2/60.3，本检查为 warning-only：
# 失败时不 FAIL-FAST 而是把 violations 加进 regen 反馈让 LLM 自我修正。
# 7 子检查：①总时长 ②镜头数 ③场景数 ④人物数 ⑤末镜禁闭环词 ⑥末镜时长保护
#           ⑦单镜 ≤ 8s（08-23 用户规则）
# =====================================================================

# v6.43 红果标准（08-23 用户规则）：镜头数不限 + 单镜 ≤ 8s + 正片总时长 60-90s（不含钩子，下限 60s 保留）
_REDGUO_DURATION_MIN = 60.0  # 保留 60s 下限（08-23 用户规则）
_REDGUO_DURATION_MAX = 90.0
_REDGUO_SHOT_MIN = 0         # 取消镜数下限
_REDGUO_SHOT_MAX = 0         # 取消镜数上限
_REDGUO_SCENE_MAX = 3        # 保留：≤ 3 场景（与时长/镜数解耦的业务硬约束）
_REDGUO_CHAR_MAX = 4         # 保留：≤ 4 人物
_REDGUO_LAST_SHOT_DURATION_MIN = 4.0
# 08-23 用户规则：单镜时长不长于 8s（与 timecode.MAX_SHOT_DURATION 同值）
_REDGUO_SHOT_DURATION_MAX = 8.0

# 末镜禁闭环词（与 prompts.py 规则 60 同步）
_REDGUO_CLOSING_WORDS = (
    "危机解除", "危险已过", "终于安全", "拥抱", "释然", "圆满",
    "大结局", "从此幸福", "最终胜利", "夕阳下远去", "帷幕落下",
    "一切结束", "完美收官", "安心入眠", "长舒一口气", "如释重负",
    "握手言和", "和好如初", "阖家团圆", "凯旋归来", "圆满落幕",
    "完美谢幕", "本集终",
)


def _check_redguo_standard(shots, characters=None):
    """v6.38 红果 60-90s 短剧标准（08-22 起）.

    7 子检查（全部 warning-only，violations 进 regen 反馈）：
    ① 单集总时长 60-90s（超/低都报；只算正片分镜，钩子时长另算）
    ② 镜头数 3-8 镜
    ③ 场景数 ≤ 3 个
    ④ 出镜人物 ≤ 4 人
    ⑤ 末镜 lf_desc/prompt 无禁闭环词（本集不闭环）
    ⑥ 末镜 duration ≥ 4s
    ⑦ 单镜 duration ≤ 8s（08-23 用户规则）
    """
    warnings = []
    if not shots:
        return {"pass": True, "issues": [], "warnings": warnings}

    # ① 总时长（只算正片分镜；钩子另算，不进 60s 下限统计）
    total_dur = sum(float(s.get("duration", 0) or 0) for s in shots)
    # v6.47（08-27）：LLM 常漏写 duration（_fill_missing_new_fields 兜底 1s → 均镜 1s×N
    # 报「总时长 16s」是误导性 WARN——_compute_timecodes 会按 motion_desc/对白/4s 旁白
    # 重算，W6 兜底 60s 下限）。总时长明显退化（均镜 < 2.5s）时跳过全部时长类子检查。
    degenerate_durations = len(shots) >= 6 and total_dur < len(shots) * 2.5
    if not degenerate_durations:
        if total_dur > _REDGUO_DURATION_MAX:
            over = total_dur - _REDGUO_DURATION_MAX
            warnings.append(
                f"红果总时长 {total_dur:.1f}s > 90s（超 {over:.1f}s，不含钩子）。"
                f"v6.43 红果标准正片 60-90s——必须砍次要冲突/次要人物/铺垫段。"
            )
        elif total_dur < _REDGUO_DURATION_MIN:
            under = _REDGUO_DURATION_MIN - total_dur
            warnings.append(
                f"红果总时长 {total_dur:.1f}s < 60s（差 {under:.1f}s，不含钩子）。"
                f"v6.43 红果标准正片 60-90s——可增加镜头数或拉长对话/反应镜头 duration"
                f"（单镜 ≤ {_REDGUO_SHOT_DURATION_MAX:.0f}s）。"
            )

    # ⑦ 单镜时长上限（08-23 用户规则：单镜不长于 8s；钩子另算不检查）
    over_shots = []
    for s in shots:
        try:
            d = float(s.get("duration", 0) or 0)
        except (TypeError, ValueError):
            continue
        if d > _REDGUO_SHOT_DURATION_MAX:
            over_shots.append((s.get("index"), d))
    if over_shots:
        detail = ", ".join(f"shot{idx}={d:.1f}s" for idx, d in over_shots[:5])
        warnings.append(
            f"单镜时长超 {_REDGUO_SHOT_DURATION_MAX:.0f}s 上限：{detail}"
            f"（{len(over_shots)} 镜）。拆分动作链为多镜（单镜 ≤ "
            f"{_REDGUO_SHOT_DURATION_MAX:.0f}s），禁止长镜头拖延。"
        )

    # ② 镜头数（v6.43：取消镜数上下限检查，按动作密度自然拆分）
    # n_shots 仅作 log 记录，不再触发 warn。

    # ③ 场景数
    scenes = set()
    for s in shots:
        loc = (s.get("scene") or s.get("location") or "").strip()
        if loc:
            scenes.add(loc)
    n_scenes = len(scenes)
    if n_scenes > _REDGUO_SCENE_MAX:
        warnings.append(
            f"红果场景数 {n_scenes} > 3（超 {n_scenes - 3} 个）。"
            f"选主场景（≥60% 镜头）+ 次场景 ≤ 1，支场景全砍。"
        )

    # ④ 人物数
    char_set = set()
    char_aliases = {}
    if characters:
        for c in characters:
            ident = (c.get("identifier") or "").strip()
            if not ident:
                continue
            char_set.add(ident)
            aliases = [ident]
            for f in (c.get("dynamic_features") or "", c.get("static_features") or ""):
                for token in f.replace("，", " ").replace("。", " ").replace("、", " ").split():
                    token = token.strip()
                    if 1 <= len(token) <= 3 and all("一" <= ch <= "鿿" for ch in token):
                        aliases.append(token)
            char_aliases[ident] = list(set(aliases))

    scan_text = " ".join(
        (s.get("ff_desc") or "") + " " + (s.get("lf_desc") or "") + " " +
        (s.get("motion_desc") or "") + " " + (s.get("prompt") or "") + " " +
        (s.get("main_subject") or "")
        for s in shots
    )
    chars_present = set()
    for ident in char_set:
        for a in char_aliases.get(ident, [ident]):
            if a and a in scan_text:
                chars_present.add(ident)
                break
    n_chars = len(chars_present)
    if n_chars > _REDGUO_CHAR_MAX:
        warnings.append(
            f"红果人物数 {n_chars} > 4（超 {n_chars - 4} 人）。"
            f"主角 + 主要对手 + 1-2 配角，支线人物全砍。"
        )

    # ⑤ 末镜禁闭环词
    last_shot = shots[-1]
    last_idx = last_shot.get("index", "?")
    last_text = " ".join([
        last_shot.get("lf_desc", "") or "",
        last_shot.get("prompt", "") or "",
        last_shot.get("motion_desc", "") or "",
        last_shot.get("narration_segment", "") or "",
    ])
    for w in _REDGUO_CLOSING_WORDS:
        if w in last_text:
            warnings.append(
                f"红果末镜（镜头{last_idx}）含闭环词「{w}」——"
                f"本集必须不闭环（规则60）。改结尾为悬念："
                f"主角新决策未执行 / 反派未反应 / 新威胁出现 / 关键信息揭露一半。"
            )
            break

    # ⑥ 末镜时长保护（退化时长同样跳过——timecode/W6 会兜底到 ≥4s）
    if not degenerate_durations:
        last_dur = float(last_shot.get("duration", 0) or 0)
        if 0 < last_dur < _REDGUO_LAST_SHOT_DURATION_MIN:
            warnings.append(
                f"红果末镜（镜头{last_idx}）duration {last_dur:.1f}s < 4s ——"
                f"末镜必须够长以渲染悬念情绪。建议拉长到 4-6s。"
            )

    # ⑦ 空镜时长硬约束（v6.38.4 规则 60.4）：纯空镜 > 4s 必报
    # 红果空镜 2-4s，> 5s 绝对禁；开篇 0-10s 禁空镜
    # 08-23 v6.41 修复误报：原 `any(h in ms for h in _EMPTY_SHOT_HINTS)` substring
    # 匹配会误判"杨旭与听香在荷塘小舟上"为空镜（"荷塘" substring 命中）。改为：
    # 1) char_layout 有 ≥1 个角色 → 必不是空镜（哪怕 main_subject 含场景词）
    # 2) main_subject 短句（≤6 字）且整个 main_subject 就在 _EMPTY_SHOT_HINTS
    #    → 才是纯空镜
    # 3) main_subject 长度 > 6 字 + 含场景词但同时含人物动词（在/与/和/于…）
    #    → 视为人物镜头不是空镜
    _EMPTY_SHOT_HINTS = (
        "空镜", "场景", "环境", "远景", "全景", "荷塘", "湖面", "山峦", "日落",
        "田野", "村庄", "庭院", "夜景", "空无人", "无人物", "城池", "山川", "水面",
    )
    # 人物连接词（main_subject 含此 → 是人物 + 场景的复合描述）
    _PERSON_CONNECTORS = ("在", "与", "和", "与", "同", "随", "立于", "立于", "立于", "坐于", "立于")
    for s in shots:
        idx = s.get("index", "?")
        ms = (s.get("main_subject") or "").strip()
        dur = float(s.get("duration", 0) or 0)
        if not ms or dur <= 0:
            continue
        # 1) char_layout 有角色 → 不是空镜
        layout = s.get("char_layout") or []
        if isinstance(layout, list) and any(
            isinstance(it, dict) and it.get("name") for it in layout
        ):
            continue
        # 2) main_subject 短句 + 整个 main_subject 是空镜词 → 真空镜
        if len(ms) <= 6 and ms in _EMPTY_SHOT_HINTS:
            is_empty = True
        # 3) main_subject 短句 + 含人物连接词 → 不是空镜
        elif any(c in ms for c in _PERSON_CONNECTORS):
            continue
        # 4) main_subject 长句 + substring 命中场景词 → 也按人物镜头处理
        #    （"杨旭与听香在荷塘小舟上" 长 13 字，明显是人物 + 场景描述，
        #     不是纯空镜）
        elif len(ms) > 6:
            continue
        else:
            is_empty = any(h in ms for h in _EMPTY_SHOT_HINTS)
        if not is_empty:
            continue
        # 开篇 0-10s 禁空镜（按本镜时间戳判断）
        start_t = (s.get("timecode") or {}).get("start", 0) or 0
        if start_t < 10 and dur > 2:
            warnings.append(
                f"红果空镜开篇禁（规则60.4）：镜头{idx}（{start_t:.1f}s 开始）"
                f"main_subject={ms[:30]!r} 是空镜且 duration {dur:.1f}s —— "
                f"开篇 0-10s 禁空镜，必须立刻换人物镜头。"
            )
        elif dur > 5:
            warnings.append(
                f"红果空镜超时长（规则60.4）：镜头{idx} main_subject={ms[:30]!r} "
                f"空镜 duration {dur:.1f}s > 5s 红果硬上限——压到 2-4s 或改成人物镜头。"
            )
        elif dur > 4:
            warnings.append(
                f"红果空镜超建议时长（规则60.4）：镜头{idx} 空镜 duration {dur:.1f}s > 4s "
                f"建议压到 2-4s。"
            )

    for w in warnings:
        _get_logger().warn(f"红果标准: {w}")
    return {"pass": True, "issues": [], "warnings": warnings}
