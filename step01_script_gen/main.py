"""step01_script_gen - 分镜脚本生成入口（v6 增强版）

功能：
  - 从小说提取章节内容
  - 调用 LLM 生成分镜脚本
  - 生成钩子分镜（v6新增）用于吸引观众
  - 验证音画同步 + 合规校验 + 敏感词校验（v6增强）
  - 注入全局渲染参数（style_prefix.inject_global_config_into_script）
  - 计算时间轴（含运镜量化参数）
  - 角色提取与持久化

输出：
  - script_result.json: 分镜脚本（位于 OUTPUT_BASE/book_N/chapter_N/）
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
import json
from contextlib import redirect_stdout

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 统一配置（从 shared.config 读取 .env）
from shared.config import (
    NOVEL_CHAPTER, BOOK_INDEX,
    CHARACTER_EXTRACTION_ENABLED,
    SCENE_OBJECT_EXTRACTION_ENABLED,
    SCENE_ASSET_EXTRACTION_ENABLED,
    PROP_ASSET_EXTRACTION_ENABLED,
    COMPLIANCE_STRICT, COMPLIANCE_AUTO_FIX,
    HOOK_ENABLED,
    GAP_ANALYSIS_ENABLED, GAP_MAX_REGEN_ATTEMPTS,
    SHOT_COUNT_REGEN_ENABLED,
    REPAIR_ROUNDS_ENABLED,
)

# v6.23：大纲两阶段开关（ShortVideoDirector director-outline 吸收，默认开启）
OUTLINE_ENABLED = os.getenv("OUTLINE_ENABLED", "1").strip() == "1"

# 输出目录基于 book/chapter 计算（统一路径来源: shared/paths.py）
from shared.paths import chapter_dir, script_file
CHAPTER_DIR = chapter_dir()
OUTPUT_FILE = script_file()
SCRIPT_FILE = OUTPUT_FILE

# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(CHAPTER_DIR / "step01.log", console_level="INFO")


# ============================================================================
# 控制台静默：把运行期间的 print() 全部重定向到 step01.log
# 控制台只保留关键进度提示（_logger.console）和最终摘要
# ============================================================================
class _ConsoleToLog:
    """把 print() 输出重定向到 log 文件的对象（配合 contextlib.redirect_stdout）."""

    def write(self, s: str):
        if s and s.strip():
            _logger.verbose(s.rstrip("\n"))

    def flush(self):
        pass


def _redirect_to_log() -> _ConsoleToLog:
    """返回一个 file-like 对象，print 写进去会落到 step01.log 而非控制台."""
    return _ConsoleToLog()


def _console(msg: str) -> None:
    """直接写原始 stdout（绕过重定向），用于控制台进度提示与最终摘要.

    重定向期间 _logger.info() 也会被吞进 log，故关键控制台输出走这里.
    """
    try:
        print(msg, file=_REAL_STDOUT)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"), file=_REAL_STDOUT)


def _warn_silent_video_model() -> None:
    """VIDEO_MODEL_VOICE=1（对话交给视频模型）但当前视频模型无声时告警.

    适配 2026-08 新接入模型：Seedance 1.0 系列（generate_audio=False）、
    MiniMax v1 Hailuo 均不生成语音；此时若仍按「对话交给视频模型」架构运行，
    成片会无对白/背景音。提示换有声模型或设 VIDEO_MODEL_VOICE=0 走 step03 TTS。
    """
    from shared.model_caps import active_video_supports_audio
    voice_enabled = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
    if voice_enabled and not active_video_supports_audio():
        _console(
            "[!] 当前视频模型不生成语音（VIDEO_MODEL_VOICE=1 默认把对话/背景音交给"
            "视频模型，但该模型无声）。\n"
            "    成片将无对白/背景音。建议：换有声模型（Seedance 2.0+/wan2.7-r2v），"
            "或设 VIDEO_MODEL_VOICE=0 走 step03 TTS。"
        )


# 保存真实 stdout 引用（redirect 前赋值）
_REAL_STDOUT = sys.stdout

# ============================================================================
# 导入子模块
# ============================================================================
from shared.chapter_reader import extract_section, get_chapter_meta
from _text_gen import create_service, TextGenerationRequest
from shared.character_bank import CharacterBank
from shared.scene_objects_bank import SceneObjectsBank
from shared.assets_bank import SceneAssetsBank, PropAssetsBank
from step01_script_gen.prompts import (
    build_prompt, SYSTEM_PROMPT, build_hook_prompt, parse_hook_response,
    _load_gap_analysis_context, _build_character_anchors,
    _load_knowledge_skills, _load_local_skill_context,
)
from step01_script_gen.character_extractor import extract_characters
from step01_script_gen.object_extractor import extract_scene_objects
from step01_script_gen.asset_extractor import extract_scene_assets, extract_props
from step01_script_gen.validation import parse_response, build_regen_feedback, _set_novel_text, _run_prompt_checklist
from step01_script_gen.timecode import _compute_timecodes
from step01_script_gen.hash_cache import _prompt_changed, _hash_source
from step01_script_gen.gap_analysis import validate_script_gap, generate_gap_report, print_gap_report

import style_prefix
import scene_compliance
import shared.content_safety as content_safety


# ============================================================================
# 1. 从小说提取指定章节
# ============================================================================

def read_chapter(path: str = "锦衣夜行_77119小说.txt", chapter: int = 1) -> str:
    """提取小说指定章节（合并多个小节），返回完整内容."""
    full_text = extract_section(path, chapter)
    _logger.info(f"[INFO] 选取第 {chapter} 章全文 {len(full_text)} 字符作为素材")
    return full_text


def _check_fps_consistency(script: dict) -> None:
    """C5 修复（08-11）：接线 validate_fps_consistency（此前定义后无调用点）.

    在 _compute_timecodes 之后调用——timeline 此时才存在。分镜时长 × fps 若
    不对齐整数帧，视频生成会多/少一帧导致卡顿。只 WARN 不阻断。
    """
    try:
        from style_prefix import validate_fps_consistency
        validate_fps_consistency(script)
    except Exception:
        pass


# ============================================================================
# 2. 文本生成
# ============================================================================

def gen_spy_script(novel_text: str, bank=None, objects_bank=None) -> str:
    """调用 LLM 生成脚本，返回原始文本."""
    from _text_gen.deepseek_schema import SCRIPT_SCHEMA
    # v6.23：大纲两阶段（ShortVideoDirector director-outline 吸收）——先生成章节
    # 大纲作分镜锚点，保证冲突/高潮分布。失败不阻断（空锚点）。
    outline_anchor = ""
    try:
        from step01_script_gen.outline import generate_outline, outline_to_anchor
        if OUTLINE_ENABLED:
            outline = generate_outline(novel_text)
            if outline:
                outline_anchor = outline_to_anchor(outline)
                _logger.info("[INFO] 章节大纲已生成，注入分镜 prompt")
    except Exception as e:
        _logger.warn(f"[WARN] 大纲生成失败（不阻断）: {e}")
    prompt = build_prompt(novel_text, bank, objects_bank, outline_anchor)
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        max_tokens=40000,
        temperature=0.2,  # v6.5: 降低温度防止幻觉
        json_schema=SCRIPT_SCHEMA,
    )
    _logger.info("[INFO] 正在调用 LLM 生成分镜脚本...")
    _console("  [生成] 正在调用 LLM 生成分镜脚本（18 镜，约需 3-6 分钟）...")
    result = service.generate(req)
    if not result.success:
        raise RuntimeError(f"LLM 生成失败: {result.error}")
    import re
    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return raw


# ============================================================================
# 3. 后处理：注入全局配置 + 合规过滤 + 敏感词校验（v6增强）
# ============================================================================

def _post_process(script: dict) -> dict:
    """增强：注入全局渲染配置 + 合规过滤 + 敏感词校验."""
    # 注入全局渲染参数（若 LLM 未输出则补上）
    script = style_prefix.inject_global_config_into_script(script)

    # 自动合规过滤
    if COMPLIANCE_AUTO_FIX:
        script = style_prefix.sanitize_script_shots(script, verbose=True)
        script = scene_compliance.auto_sanitize_shot_prompts(script, verbose=True)
        # v6.32（08-12）：台词（narration/subtitle）做「台词级」敏感词替换——只替换
        # 语义风险词表里的词（如「杀人」→「出人命」），不套用画面 prompt 的全表
        # （「死了→倒了」「杀了→击了」会把对话语义改碎，step01.log 镜头16/18 实测）。
        # 让「杀人」类风险词不再触发 semantic_risk 硬检查，同时保留对话原意。
        for _shot in script.get("shots", []):
            for _f in ("narration_segment", "subtitle"):
                if _shot.get(_f):
                    _shot[_f] = content_safety.sanitize_narration(_shot[_f])

    # v6 新增：敏感词校验（强制级）
    validation_result = style_prefix.validate_sensitive_words(script, verbose=True)
    if validation_result["has_issues"]:
        _logger.warn("[WARN] 检测到残留敏感词，再次进行合规过滤...")
        # 再次过滤确保干净
        script = style_prefix.sanitize_script_shots(script, verbose=True)
        # 再次校验
        validation_result = style_prefix.validate_sensitive_words(script, verbose=True)
        if validation_result["has_issues"]:
            _logger.error("[ERROR] 仍有残留敏感词，请检查脚本!")

    return script


# ============================================================================
# 4. 钩子分镜生成（v6新增）
# ============================================================================

def gen_hook_script(novel_text: str, bank=None, objects_bank=None,
                    forbidden_lines: list[str] | None = None,
                    dup_note: str | None = None) -> dict | None:
    """调用 LLM 生成钩子分镜，返回 hook dict 或 None.

    Args:
        novel_text: 小说原文
        bank: CharacterBank 实例，用于注入角色锚点（prompt 中用角色名而非模糊称呼）
        objects_bank: SceneObjectsBank 实例，用于注入场景物体尺寸锚点（prompt 中写出锚定尺寸）
        forbidden_lines: v6.32（08-12）主分镜各镜头台词体列表（_speech_body），
            注入钩子 prompt 防「钩子台词与主分镜重复」倒叙穿帮。
        dup_note: v6.32（08-12）重生成时的去重反馈——上一版钩子台词与主分镜镜头
            重复，把具体重复台词 + 换台词/改纯画面开场的选择喂给 LLM，引导它换一句。
    """
    if not HOOK_ENABLED:
        return None

    prompt = build_hook_prompt(novel_text, bank=bank, objects_bank=objects_bank,
                               forbidden_lines=forbidden_lines)
    if dup_note:
        prompt = prompt + f"\n\n# 钩子台词去重反馈（必须遵守）\n{dup_note}"
    service = create_service()
    from _text_gen.deepseek_schema import HOOK_SCHEMA
    req = TextGenerationRequest(
        system_prompt="你是短视频脚本大师，擅长在视频开头制造悬念和吸引力。禁止血腥暴力细节。",
        user_prompt=prompt,
        max_tokens=8000,
        temperature=0.2,  # v6.5: 降低温度防止幻觉
        json_schema=HOOK_SCHEMA,
    )
    _logger.info("[INFO] 正在调用 LLM 生成钩子分镜...")
    _console("  [生成] 正在生成钩子分镜（约 30 秒）...")
    result = service.generate(req)
    if not result.success:
        _logger.warn(f"[WARN] 钩子分镜生成失败: {result.error}，跳过钩子")
        return None

    import re
    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

    try:
        hook_data = parse_hook_response(raw)
        hook = hook_data.get("hook")
        if hook:
            # 钩子也需合规过滤
            hook["prompt"] = style_prefix.sanitize_prompt(hook.get("prompt", ""), verbose=False)
            # 钩子镜头也需要做敏感词校验
            for field in ("ff_desc", "lf_desc", "motion_desc"):
                if hook.get(field):
                    hook[field] = style_prefix.sanitize_prompt(hook[field], verbose=False)
            # v6.32：钩子台词（narration/subtitle）做「台词级」敏感词替换——只替换
            # 语义风险词表里的词（如「杀人」→「出人命」），不套用画面 prompt 的全表
            # （「死了→倒了」会把对话改得语义破碎）。见 shared.content_safety.sanitize_narration。
            for field in ("narration_segment", "subtitle"):
                if hook.get(field):
                    hook[field] = content_safety.sanitize_narration(hook[field])
        return hook
    except Exception as e:
        _logger.warn(f"[WARN] 钩子分镜解析失败: {e}，跳过钩子")
        return None


# ============================================================================
# 5. 镜头数超限收敛重生成（v6.7 新增）
# ============================================================================

def _speech_body(narration: str) -> str:
    """从「说话人道：台词」中提取台词体（剥说话人前缀，去标点）.

    C4 修复（08-11）用：钩子台词与主分镜台词比对去重。台词比对前先剥
    「说话人道：」前缀（不同说话人说同一句也判重）并去空白标点。
    """
    if not narration:
        return ""
    import re as _re
    body = _re.split(r"[:：]", narration, maxsplit=1)[-1]
    return _re.sub(r"[\s　，。！？、；：""''（）【】《》…—-]", "", body)

def _regen_over_limit(script: dict, novel_text: str, bank=None, objects_bank=None,
                      max_attempts: int = 2) -> dict:
    """镜头数严重超限时，反馈给 LLM 合并冗余镜头，重新生成一次（软收敛）.

    与 gap_analysis 相互独立：这里只看「镜头数/总时长」超限，不校验内容分布。
    仍超限或 JSON 解析失败时保留原版，不中断流水线（下游有 TTS rate / merge
    truncation / stretch cap 兜底，v4.1 哲学是镜头数交 LLM，此处只收敛废输出）。

    Returns:
        重新生成且通过校验的 script；否则返回原 script。

    v6.43（08-23 用户规则）镜头数不限：SHOT_COUNT_MAX<=0 时整个收敛+截断逻辑
    全部跳过（不再 regen、不再 force-truncate），镜头数完全由 LLM 决定。
    """
    from shared.config import SHOT_COUNT_MAX

    # v6.43 镜头数不限：SHOT_COUNT_MAX<=0 时跳过整个函数（保留 LLM 原始生成）
    if SHOT_COUNT_MAX <= 0:
        return script

    shots = script.get("shots", [])
    total_duration = float(script.get("total_duration", 0) or 0)
    over_max = len(shots) > SHOT_COUNT_MAX

    for attempt in range(1, 1 + max_attempts):
        if not over_max:
            break

        current = len(script.get("shots", []))
        _logger.warn(
            f"[COUNT] 镜头数 {current} 超上限 {SHOT_COUNT_MAX}，"
            f"反馈 LLM 合并冗余镜头（尝试 {attempt}/{max_attempts}）..."
        )
        _console(f"  [收敛] 镜头数 {current} 超上限 {SHOT_COUNT_MAX}，重新生成精简版（{attempt}/{max_attempts}）...")

        # 反馈上下文：镜头数与总时长双重提示，要求合并同场景/冗余过渡镜头
        # （08-12 用户决策：60s/110s 总时长目标「先不改」，只收敛镜头数超限的废输出）
        # v6.40 08-22：升级为"硬约束"措辞 + 强调"本轮拒收"，LLM 软话不收敛。
        from shared.config import SHOT_COUNT_MAX as _max
        feedback = (
            f"\n\n# ★ 镜头数硬约束（v6.40 08-22 升级，本轮拒收）\n"
            f"你上一轮出了 {current} 镜，超上限 {_max} {current - _max} 镜。\n"
            f"**本轮拒收 ≤ {_max} 镜的输出——必须严格 ≤ {_max} 镜**。\n\n"
            f"严格合并规则：\n"
            f"1. 同一主体 + 同一地点 + 连续同一动作 = 1 镜（用动作重叠法或 clip_transition=硬切合并）\n"
            f"2. 相机角度微调 / 同对话正反打 = 1 镜（不要拆）\n"
            f"3. 只有主体变 / 地点变 / 时空跳跃 / 旁白主题切换 = 拆镜\n"
            f"4. 删减冗余过渡镜头与重复表达的空镜\n"
            f"5. 保留全部关键剧情节点（开篇冲突 / 刺杀 / 高潮 / 收尾），不删剧情只合并冗余\n"
            f"6. **不要把多镜拆成单镜的细碎画面**——一镜只承载 1 个核心动作/情绪转折\n\n"
            f"v6.38 红果硬约束：3-8 镜、正片总时长 60-90s（钩子另算）、单镜 3-8s（单镜硬上限 8s）。当前 14 镜是历史包袱，本轮必须压到 ≤ {_max} 镜。\n"
        )
        # v6.33（08-12）：附加 checklist 聚合反馈——镜头数收敛之外，把内容密度/景别
        # 跳跃/致命伤等真实缺陷也喂给 LLM（此前只喂镜头数/覆盖率，LLM 重生成还是老问题）
        _checklist_fb = build_regen_feedback(script, novel_text)
        if _checklist_fb:
            feedback = feedback + _checklist_fb
        prompt = build_prompt(novel_text, bank, objects_bank)
        prompt = prompt + feedback

        service = create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        req = TextGenerationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=50000,  # 08-15：全量分析反馈进 prompt 后重写镜头更多，输出=完整脚本留余量（修复轮已用 50000 实测成功）
            temperature=0.2,
            json_schema=SCRIPT_SCHEMA,
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _logger.warn(f"[WARN] 收敛重生成失败: {e}，保留原版")
            break
        if not result.success:
            _logger.warn(f"[WARN] 收敛重生成失败: {result.error}，保留原版")
            break

        import re
        raw = result.text.strip()
        raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

        try:
            new_script = parse_response(raw, novel_text=novel_text)
        except (ValueError, json.JSONDecodeError) as e:
            _logger.warn(f"[WARN] 收敛重生成 JSON 解析失败，保留原版: {e}")
            break

        new_shots = new_script.get("shots", [])
        if not new_shots:
            _logger.warn("[WARN] 收敛重生成返回空 shots，保留原版")
            break

        # 收敛有效：替换并重算时间轴
        script = new_script
        _compute_timecodes(script)
        total_duration = float(script.get("total_duration", 0) or 0)
        if len(new_shots) <= SHOT_COUNT_MAX:
            _logger.info(
                f"[COUNT] 收敛成功: {current} → {len(new_shots)} 镜, "
                f"总时长 {total_duration:.0f}s"
            )
        over_max = len(new_shots) > SHOT_COUNT_MAX

    if over_max:
        # v6.40 08-22：regen 全部失败后不再"保留原版"——按 v6.38 红果硬约束**强制截断**
        # 到 SHOT_COUNT_MAX 镜。LLM 软话不收敛时这是最后兜底，优先保留前 N 镜（开篇+冲突+高潮）
        # 截断后再跑一次 _compute_timecodes 校正 timecode。
        from shared.config import SHOT_COUNT_MAX
        _console(
            f"  [COUNT] 重试 {max_attempts} 次后仍 {len(script.get('shots', []))} 镜，"
            f"按 v6.38 红果硬约束强制截断到 {SHOT_COUNT_MAX} 镜"
        )
        _logger.warn(
            f"[COUNT] 强制截断: {len(script.get('shots', []))} → {SHOT_COUNT_MAX} 镜（v6.38 兜底）"
        )
        shots = script.get("shots", [])
        script["shots"] = shots[:SHOT_COUNT_MAX]
        # 同步处理 hook：若 hook 之前被算入 shots 之外的，截断不影响 hook
        _compute_timecodes(script)
    return script


# 定向修复轮：GAP 通过后仍存在的高价值告警（开篇冲突/爆点密度/威胁源朝向/
# 关键帧质量/内容密度），只把「告警镜头 + 告警文本」发给 LLM 做单镜修复，
# 比全脚本重生成便宜、且不破坏已通过的镜头。P18（08-15）核心闭环。
# 08-15（v6.36）收紧：只留「字段级局部修复」类别——重写被限制在具体字段
# （ff/lf 锚、道具方位、强情绪链、ff/motion 时序），引入新噪音的风险低。
# 剔除：opening_hook/punch_density（全局重构，改前1/3段必引入噪音）、content_density
# （08-15 实测 1→9 重写越改越糟）、threat_gaze_coupling（B2 确定性注入已覆盖）。
# 08-15（PR-2）：keyframes_quality 退出修复轮——聚合告警（「N 个中间关键帧」无镜头号）
# 本就归 global 被过滤，且 post_fix 已确定性注入角色姿态/位置骨架，无需 LLM 修。
# 定向修复轮：GAP 通过后仍存在的高价值告警，只把「告警镜头 + 告警文本」发给 LLM 做单镜修复。
# 08-20（v6.37）扩展：9 → 33 类，覆盖所有字段级可手术修复的检查；结构性/全局/确定性已覆盖类仍排除。
# 排除（合并改写必破坏其它镜头 / 代码已兜底）：
#   - opening_hook / punch_density / content_density / combat_arc（全局或整段）
#   - keyframes_quality / threat_gaze_coupling / combat_arc_coda（确定性已注入）
#   - narrative_arc_distribution / platform_compliance / semantic_risk（结构性/合规另路处理）
_REPAIR_PRIORITY_CATEGORIES = (
    # === 锚点/位置（4） ===
    "ff_desc_anchors", "prop_spatial_anchor", "pose_continuity", "multi_person_staging",
    # === 情绪/表情（3） ===
    "strong_emotion_chain", "restrained_emotion", "emotion_visualization",
    # === 时序/节奏（5） ===
    "ff_motion_timeline", "kf_temporal_consistency", "kf_time_window_alignment",
    "shot_transition", "action_overlap",
    # === 景别/运镜（3） ===
    "shot_size_jump", "camera_movement", "camera_upgrade", "focal_switch",
    # === 服装/角色（2） ===
    "character_consistency", "character_entrance",
    # === 对白/口型（2） ===
    "dialogue_length", "voice_punctuation",
    # === 静态/时长（2） ===
    "dwell_time", "prompt_length_budget",
    # === 画面质量（8） ===
    "forbidden_abstract", "lighting_position", "color_tone_transition", "hand_lock",
    "over_shoulder_lock", "occlusion_layering", "gaze_direction", "gaze_convergence",
    # === 画面描述（4） ===
    "ff_lf_completeness", "ff_desc_length", "ff_multi_action", "frame_weapon_legality",
    # === 打斗/致命（2） ===
    "combat_action", "fatal_wound_continuity",
    # === 空镜/字幕/合规（2） ===
    "negative_prompt", "subtitle_leak",
    # === 原文锚点 + 高潮优先（2） ===
    "grounding", "climax_priority",
)
# 08-15（v6.36）：2 轮 → 1 轮——实测修复轮越改越糟（38→55），降为单轮 top-6
# 聚焦最严重，杜绝「第2轮全量」把没问题的镜头也重写一遍。
_REPAIR_MAX_ROUNDS = 1


def _collect_repair_warnings(script: dict, novel_text: str) -> dict:
    """重跑 checklist，收集高价值告警，按镜头聚合.

    Returns: {shot_index_or_"hook": [(category_label, warning_text), ...]}
    """
    try:
        if novel_text:
            _set_novel_text(novel_text)
        import io as _io
        import contextlib as _ctx
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf):
            results = _run_prompt_checklist(script).get("results", {})
    except Exception as _e:
        import traceback
        _logger.warn(f"[WARN] 定向修复收集告警异常: {_e}")
        traceback.print_exc()
        return {}
    from step01_script_gen.validation import _REGEN_FEEDBACK_LABELS
    per_shot: dict = {}
    for key, res in results.items():
        if key not in _REPAIR_PRIORITY_CATEGORIES or not isinstance(res, dict):
            continue
        warns = list(res.get("warnings") or []) + list(res.get("issues") or [])
        if not warns:
            continue
        label = _REGEN_FEEDBACK_LABELS.get(key, key)
        import re as _re
        for w in warns:
            m = _re.match(r"镜头([\d]+)(?:→(\d+))?", w)
            if m:
                _tid = f"shot_{m.group(1)}"
                per_shot.setdefault(_tid, []).append(f"[{label}] {w.strip()}")
                # 08-15（PR-3）：跨镜告警（镜头衔接「镜头N→M」/动作重叠「镜头N→M」）
                # 涉及的**两镜**都归入修复——动作重叠/衔接的修复动作常落在 M 的
                # ff/prompt（下一镜承接上一镜结尾动作），M 不在 picked 则 merge 门闩
                # 丢弃其改写，永远修不干净。只加告警句点名的对镜，不把整串 run 全加。
                if m.group(2):
                    per_shot.setdefault(f"shot_{m.group(2)}",
                                         []).append(f"[{label}] {w.strip()}")
            else:
                _tid = "hook" if "钩子" in w else "global"
                per_shot.setdefault(_tid, []).append(f"[{label}] {w.strip()}")
    return per_shot


def _locate_narration_in_novel(narration: str, novel_text: str) -> tuple:
    """在 novel_text 里找 narration 的最长公共子串（LCS 简化：滑动窗口）。

    返回 (start, end) 字符偏移；找不到返回 (-1, -1)。
    - 完整 narration 命中：直接返回
    - 标点归一化（：→"，" → "等）：再尝试一次
    - 按标点/空白切 narration 为多个 8-16 字短语（短句优先匹配）
    - 每个短语在 novel_text 里找首次出现位置
    - 取**最靠后**的短语位置作 end，往前推 64 字作 start（保证覆盖整句）
    """
    if not narration or not novel_text:
        return (-1, -1)
    import re as _re_loc
    # 标点归一化：把中文引号/弯引号统一，全角冒号 → 半角冒号
    def _normalize(s: str) -> str:
        s = s.replace("：", ":")  # 全角冒号 → 半角
        s = s.replace('"', '"').replace('"', '"')  # 中文弯引号 → ASCII
        s = s.replace(''', "'").replace(''', "'")  # 中文单引号 → ASCII
        s = s.replace("…", "...")  # 横向省略号 → 三点
        s = s.replace("。", ".")  # 句号归一
        s = s.replace("，", ",")  # 逗号归一
        return s
    narr_norm = _normalize(narration)
    novel_norm = _normalize(novel_text)
    # 1) 完整 narration 命中（归一化后）：直接返回
    if narr_norm in novel_norm:
        idx = novel_norm.find(narr_norm)
        return (idx, idx + len(narr_norm))
    # 2) 切短语（按标点 / 「道:」切）—— 阈值从 6 放宽到 3，避免「啊…啊…救命啊」整段被切碎
    chunks = _re_loc.split(r'[,.!?;:\n]', narr_norm)
    chunks = [c.strip() for c in chunks if c and len(c.strip()) >= 3]
    if not chunks:
        return (-1, -1)
    # 3) 找「人名 + 动词/对象」短语作锚（剥离「道:」前缀）
    # 优先尝试最长的 chunk，再依次退到短的
    chunks.sort(key=len, reverse=True)
    for chunk in chunks[:5]:  # 试 5 个最长的
        pos = novel_norm.find(chunk)
        if pos >= 0:
            end = pos + len(chunk)
            start = max(0, pos - 64)
            return (start, end)
    return (-1, -1)


def _build_novel_slice_for_picked(picked: dict, shots_by_id: dict,
                                   novel_text: str, ctx: int = 200) -> str:
    """08-20 mode B：按 picked 镜的 narration_segment 切原文片段，前后各扩 ctx 字。

    返回拼接好的字符串（带 shot_xx 标头），多个 picked 镜的切片按时间序合并（重叠自动合并）。
    """
    if not picked or not novel_text:
        return ""

    # 1) 收集 picked 镜的 (shot_id, narration) 列表
    items = []
    for tid in picked:
        if tid == "global":
            continue
        s = shots_by_id.get(tid)
        if not s:
            continue
        narr = (s.get("narration_segment") or s.get("narration") or "").strip()
        if not narr:
            continue
        items.append((tid, narr, s.get("index")))

    if not items:
        return ""

    # 2) 每个 narration 在 novel 里定位 → (start, end) → 扩 ctx
    slices = []
    for tid, narr, idx in items:
        s, e = _locate_narration_in_novel(narr, novel_text)
        if s < 0:
            continue
        s2 = max(0, s - ctx)
        e2 = min(len(novel_text), e + ctx)
        slices.append((s2, e2, tid, idx))

    if not slices:
        return ""

    # 3) 按 start 排序 + 重叠合并（间隔 < 100 字视为连续）
    slices.sort(key=lambda x: x[0])
    merged = [slices[0]]
    for cur in slices[1:]:
        last = merged[-1]
        if cur[0] <= last[1] + 100:  # 重叠或相邻
            new_end = max(last[1], cur[1])
            new_ids = f"{last[2]},{cur[3]}"
            merged[-1] = (last[0], new_end, last[2] + "+" + cur[2], None)
        else:
            merged.append(cur)

    # 4) 输出：每个合并段打 shot_ids 标头 + 原文片段
    out_lines = []
    for s, e, ids, _ in merged:
        seg = novel_text[s:e].replace("\n", " ").strip()
        if len(seg) > 1500:  # 单段过长截断
            seg = seg[:1500] + "..."
        out_lines.append(f"### 原文片段（{ids}）\n{seg}\n")

    total_len = sum(len(l) for l in out_lines)
    # 防止 prompt 爆炸：超 6k 字截断（理论上 picked 6 镜 × ~400 字 = 2.4k）
    if total_len > 6000:
        return out_lines[0] + "\n...（后续切片省略，控制总长 6k 字以内）..."
    return "\n".join(out_lines)


def _repair_shot_warnings(script: dict, novel_text: str,
                          bank=None, objects_bank=None) -> dict:
    """定向修复轮（v6.35，P18 闭环核心）。

    GAP 通过后仍有高价值告警 → 只重写告警镜头的相关字段，重新校验，收敛即停。
    全脚本重生成会把没问题的镜头也重画一遍（白烧额度 + 引入新随机）；
    单镜修复只动有问题的地方，且每次轮次只挑「最值得修」的镜头（防 prompt 爆炸）。

    Returns:
        修复后的 script（任一轮失败/无告警则原样返回）。
    """
    for _round in range(1, _REPAIR_MAX_ROUNDS + 1):
        per_shot = _collect_repair_warnings(script, novel_text)
        # 过滤结构性全局告警（无法单镜修复的），只留镜头级
        shot_items = {k: v for k, v in per_shot.items() if k.startswith("shot_") or k == "hook"}
        if not shot_items:
            return script
        # 第1轮：只挑告警最多的前 6 镜，聚焦修最严重的（防 prompt 膨胀）；
        # 第2轮：剩余告警镜头**全部**返给 LLM 一次修完（08-15 用户要求——低告警镜头
        # 此前可能两轮都进不了 top-6 而永不修复）。每轮 _collect_repair_warnings 都
        # 重新校验当前脚本：第1轮已修的告警消失、未修的保留，故第2轮"全部"天然覆盖
        # 第1轮遗留 + 第2轮新增。
        import itertools
        if _round == 1:
            picked = dict(itertools.islice(
                sorted(shot_items.items(), key=lambda kv: -len(kv[1])), 6))
        else:
            picked = dict(shot_items)
        if not picked:
            return script
        # 08-15（PR-3）：跨镜告警对镜补选——top-6 按告警数排序时，被选镜告警里的
        # 「镜头N→M」对镜可能掉出前 6（两镜告警数相同会一起进，但若 N 另有别的
        # 告警挤掉 M 就散了）。补选保证 N 入选时 M 必然一起，merge 门闩才能采纳
        # M 的承接改写。只补告警句点名的对镜，不把整串 run 全加（12→22 不加中间）。
        import re as _pr3re
        for _tid in list(picked):
            for _w in picked[_tid]:
                _pm = _pr3re.search(r"镜头\d+→(\d+)", _w)
                if _pm:
                    _partner = f"shot_{_pm.group(1)}"
                    if _partner in shot_items and _partner not in picked:
                        picked[_partner] = shot_items[_partner]

        # 构建单镜修复 prompt：只带被选镜头原文 + 告警
        shots_by_id = {f"shot_{s.get('index')}": s for s in script.get("shots", [])}
        if "hook" in picked and script.get("hook"):
            shots_by_id["hook"] = script["hook"]

        _console(f"  [修复轮{_round}] {len(picked)} 镜待修（{', '.join(picked)}）...")
        # 08-18 方案 C：先统计全片各 label 的 WARN 总数（按分镜聚合之前算一次），
        # 后面拼 parts 时每条 WARN 末尾追加"（同类全片共 N 处）"——让 LLM 改 shot_4
        # 时知道"ff_desc_anchors 错误全片 25 处"而不是孤例 1 处，全局视图促动认真修。
        # 08-21 mode 合并：_label_total 总是计算（v1 mode A 不算 → v2 算轻量版 → v3 复评
        # 决定全量注入全片错误分布 + top-3 hint + 全片分布三件套同时给 LLM）
        _label_total: dict = {}
        from collections import Counter as _label_counter
        _label_total = _label_counter()
        for _tid, _ws in per_shot.items():
            for _w in _ws:
                if _w.startswith("["):
                    _end = _w.find("]")
                    if _end > 0:
                        _label_total[_w[1:_end]] += 1
        # top-3 高频 hint（轻量版全片视图，无分布表，只取前 3 类）
        # ——给 LLM 一个"本章主要矛盾"信号，不带成本又比纯 warn 列表多了全局感
        _top3_hint = ""
        if _label_total:
            _top3 = _label_total.most_common(3)
            if _top3:
                _top3_hint = (
                    "\n**全片高频问题（top-3，仅作参考）**：" +
                    "、".join(f"[{l}] {n}处" for l, n in _top3) + "\n"
                )
        parts = []
        # 08-15（v6.36 P18 修复）：删除「全片结构问题」前言注入。v6.36 已把开篇冲突/
        # 爆点密度/内容密度 剔除出修复类别（全局重构必引入噪音），此时 per_shot["global"]
        # 只剩 keyframes 聚合告警（以「N 个中间关键帧」开头、无镜头号、被 shot_items
        # 过滤）——把它当结构问题灌给 LLM 只会诱导重排前1/3段镜头，正是开篇冲突/爆点
        # 密度回归的来源。keyframes 改走确定性 skeleton（PR-2），不进修复轮。
        for tid, warns in picked.items():
            if tid == "global":
                continue
            s = shots_by_id.get(tid)
            if not s:
                continue
            # 08-15 邻镜上下文（用户要求）：镜头衔接/动作重叠是**跨镜**类告警，
            # 第1轮 top-6 时邻镜不在 prompt 里，LLM 看不到邻镜结尾/开头就修不了
            # 成对的衔接问题——把「前镜 lf_desc + 后镜 ff_desc」一并给 LLM。
            # 第2轮全量时邻镜字段已在各自块里，此处冗余无害。
            prev_s = shots_by_id.get(f"shot_{s.get('index', 0) - 1}")
            next_s = shots_by_id.get(
                f"shot_1" if tid == "hook" else f"shot_{s.get('index', 0) + 1}")
            ctx_lines = []
            if prev_s:
                ctx_lines.append(f"前镜({prev_s.get('index')}) lf_desc: {prev_s.get('lf_desc', '')}")
            if next_s:
                ctx_lines.append(f"后镜({next_s.get('index')}) ff_desc: {next_s.get('ff_desc', '')}")
            ctx = ("\n" + "\n".join(ctx_lines) + "\n") if ctx_lines else ""
            # 08-18：每条 WARN 末尾追加"（同类全片共 N 处）"
            # 08-21 mode 合并：总是追加（让 LLM 知道同类问题全片共 N 处）
            _warn_lines = []
            for w in warns:
                if w.startswith("[") and w.find("]") > 0:
                    _lbl = w[1:w.find("]")]
                    _total = _label_total.get(_lbl, 1)
                    _warn_lines.append(f"  - {w}（同类全片共 {_total} 处）")
                else:
                    _warn_lines.append(f"  - {w}")
            _warns_str = "\n".join(_warn_lines)
            parts.append(f"### 待修镜头 {tid}\n"
                         f"当前 ff_desc: {s.get('ff_desc', '')}\n"
                         f"当前 lf_desc: {s.get('lf_desc', '')}\n"
                         f"当前 prompt: {s.get('prompt', '')}\n"
                         f"当前 color_tone: {s.get('color_tone', '')}\n"
                         f"{ctx}"
                         f"问题:\n{_warns_str}")
        if not parts:
            return script

        # 08-18：在 prompt 开头加"全片错误分布"段
        # 08-21 mode 合并：全片错误分布总是注入（v1 mode A 删掉，v2 复评发现有用）
        # ——给 LLM 全局视图，决策树 + 正反例才能精准定位
        _global_block = (
            "\n**全片错误分布（同类问题总数，按类别聚合）**：\n"
            + "\n".join(f"  - [{l}] 全片共 {n} 处"
                        for l, n in _label_total.most_common())
            + "\n"
        )

        # v6.37：把修复轮 prompt 拆成结构化多轮输入
        # - system_prompt：完整规则（不变）
        # - input[0]：INPUT（当前分镜 + 角色锚点 + skills）——**不含 novel，防 LLM 重生成**
        # - input[1]：REPAIR FEEDBACK（结构化 JSON）+ TASK（修复指令）——纯修复内容
        # 关键：移除 novel text，避免 LLM 看到原文后重新拆镜（违反"手术刀原则"）
        import json as _json
        _current_script_json = _json.dumps(
            script, ensure_ascii=False, indent=1, default=str
        )

        # 角色锚点（仅含服装色等查询字段，不含规则）——修 服装色 必备
        _char_anchors_str = ""
        if bank:
            try:
                _ca = _build_character_anchors(bank)
                if _ca and _ca != "无已知角色":
                    _char_anchors_str = (
                        "## INPUT 2：角色锚点（仅供字段查询，不重生成）\n"
                        "以下角色名+服装色等锁定字段，修复时服装色必查本表：\n"
                        f"{_ca}"
                    )
            except Exception:
                _char_anchors_str = ""

        # skills（运镜/光线/招式词库）——修运镜/光位/打斗反馈必备
        try:
            _skills_str = _load_knowledge_skills()
        except Exception:
            _skills_str = ""

        # Turn 0：INPUT（当前分镜 + 角色锚点 + skills，不含 novel）
        _input_turn = (
            "## INPUT 1：当前分镜（待修复）\n"
            "```json\n"
            f"{_current_script_json}\n"
            "```\n\n"
            f"{_char_anchors_str}"
        )
        if _skills_str:
            _input_turn += (
                "\n\n## INPUT 3：写作风格参考（skills 词库）\n"
                "运镜/光线/招式/打斗等词汇从这里取（不要重新发挥）：\n"
                f"{_skills_str}"
            )

        # 08-21 mode 合并：总是注入原文切片（原 mode B 特性，对 grounding 类问题很有用，
        # 合并后无副作用：_input_turn 的 narration_segment 锚定校验本来就该有原文对照）
        # - 按 narration_segment 在 novel_text 里做最长公共子串定位，前后各扩 200 字
        # - 多个 picked 镜共享同一段原文时自动合并去重
        # - 仅供「ground 修复」用，禁止触发 LLM 重新拆镜（依然保留手术刀原则）
        if novel_text:
            try:
                _novel_slice = _build_novel_slice_for_picked(picked, shots_by_id, novel_text, ctx=200)
                if _novel_slice:
                    _input_turn += (
                        "\n\n## INPUT 4：原文切片（仅供 ground 待修问题，禁改动 narration_segment）\n"
                        "以下是从原文中按 picked 镜的 narration_segment 定位切出的片段，"
                        "用于 ground「动作时刻」「致命伤 anchor」「kf 时段」等问题：\n"
                        "**只用来查时间/动作/伤情对应关系，禁止用于重新拆镜、合并镜头、"
                        "改动 narration_segment 文本。**\n"
                        f"{_novel_slice}"
                    )
            except Exception as _e:
                _logger.warn(f"[WARN] 原文切片构建失败，回退到无原文切片: {_e}")

        # Turn 2：REPAIR FEEDBACK + TASK
        # 警告转为结构化 JSON（按 shot_xxx 分组），避免 freeform 文本混排字段
        # 08-21 mode 合并：总是带全片错误分布
        _feedback_json = {
            "mode": "unified",
            "global_error_distribution": [
                {"label": l, "total": n} for l, n in _label_total.most_common()
            ],
            "shots": {},
        }
        for _tid, _ws in picked.items():
            if _tid == "global":
                continue
            _s = shots_by_id.get(_tid) or {}
            _feedback_json["shots"][_tid] = {
                "current": {
                    "ff_desc": _s.get("ff_desc", ""),
                    "lf_desc": _s.get("lf_desc", ""),
                    "prompt": _s.get("prompt", ""),
                    "color_tone": _s.get("color_tone", ""),
                },
                "warnings": _ws,
            }

        # 08-21 mode 合并：单一 unified prompt 替代原 A/B/C 三套
        # 含：全片错误分布（_input_turn）+ top-3 hint + 4 类约束 + 决策树 + 3 组正反例
        # + 同角色跨镜一致性 + 5 条修复要点 + 同镜内 anchor 保留
        _repair_turn = (
            "## REPAIR FEEDBACK（结构化 JSON，按 shot 分组）\n"
            "```json\n"
            f"{_json.dumps(_feedback_json, ensure_ascii=False, indent=1)}\n"
            "```\n\n"
            + _top3_hint +
            "## TASK（精简修复）\n"
            "请**逐条**修复下方列出的问题，每条 warn 改一处字段即可。\n"
            "输出完整修正后的分镜 JSON（保留全部镜头结构与未提及字段）。\n\n"
            "**手术刀原则（最高优先级）**：\n"
            "1. 一条 warn 改一处字段——不要批量重写整镜\n"
            "2. 禁止新增任何短语（动作/情绪/运镜/光位/转场/视角/装饰性方位），"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但**禁止写成「望着她」「看杨旭」这类无空间方位词**\n"
            "3. 禁止改景别/时长/台词/剧情/narration_segment 文本\n"
            "4. 禁止增删镜头、改 shot.index\n"
            "5. 修完输出整本 JSON，未列问题的镜头**逐字保留**\n"
            "6. **lf_desc 至少 40 字，含本镜所有出镜角色的完整静态布局**（姿态/位置/服装色/视线）"
            "——过短（如「已没入水下，杨旭仰卧船头」单句）会丢角色状态、产生内部矛盾\n\n"
            "**关键字段保留白名单（最高优先级，违反 = 致命伤/漂色/连续性破缺）**：\n"
            "即使该字段不在 warn 列表中，下述字段也**禁止删/改/移位**——\n"
            "C 模式实验教训：改服装色 warn 连带删掉致命伤 anchor（shot14/24/25）。\n"
            "  - **致命伤 anchor**：心口/洇痕/暗红/中刀/中剑/伤口/血痕/腹部/胸口/心窝 等\n"
            "  - **服装色**（每个出镜角色）：素白轻袍/一袭春衫/青衣小帽/玄色劲装 等（取自角色库）\n"
            "  - **角色当前状态**（坐/站/仰卧/跪坐/持物 等已建立姿态）\n"
            "  - **锚点连贯子句**（前镜 lf_desc 已锚定的姿态/位置/朝向，跨镜承接必须保留）\n"
            "  - **color_tone**（场景色调基底词，如「冷蓝星光」/「暖金烛光」——跨镜共享）\n\n"
            "**Mask 字段清单（本轮禁止修改）**：\n"
            "  - narration_segment（台词，原文锚定，改则 grounding 校验必挂）\n"
            "  - index / duration_sec（结构性字段，动了会改镜头排序或时长）\n"
            "  - subtitle（与 narration_segment 逐字相同，规则 1 锁定）\n"
            "  - variation_type（small/large 变体类型，动了会改 keyframe 数量）\n"
            "  - scene / location（场景字段，规则 7.5 锁定）\n\n"
            "**同镜内 warn 改字段时必须保留同字段 anchor（最高优先级）**：\n"
            "若一条 warn 改 X 字段（ff_desc/lf_desc/prompt），该字段内已存在的\n"
            "  致命伤 anchor / 服装色 / 角色状态 / 锚点连贯子句 必须**逐字保留**。\n"
            "例：ff_desc 报「缺服装色」→ 仅追加缺色角色的服装色，**禁止**顺手删\n"
            "  已有的「心口暗红洇痕」；lf_desc 报「缺服装色」→ 同样只补服装色，\n"
            "  已有的「仰卧/血痕/听香捂嘴」等不删。\n\n"
            "**修复决策树（最高优先级，不知道改哪里时按表走）**：\n"
            "- ff_desc/lf_desc 报「缺服装色」→ 仅在对应字段末尾追加缺色角色服装色，\n"
            "  已有 anchor 全保留（见上「同镜内 anchor」原则）\n"
            "- ff_desc/lf_desc/prompt 报「视线无空间方位」→ 把「看/盯/望+人名」\n"
            "  改写为「目光紧盯人名（画X）」；无空间方位词 = 必挂\n"
            "- 报「致命伤/洇痕缺失」→ 在 prompt + lf_desc 补「心口处白袍暗红洇痕持续可见」\n"
            "- 报「kf 时序矛盾」→ 改 kf.desc（不要改 prompt），按 pre-stab/stab/post-stab 递进写\n"
            "- 报「景别跳跃」→ 后一镜景别词改为中间级，同步改 prompt 开头 + ff_desc 构图\n"
            "- 报「光位模糊」→ 「柔和光线」改为「45°侧光/顶光/逆光」等具体光位\n"
            "- 报「kf 时段缺段」→ 按 motion_desc 时段表补 1 帧定格（time_ratio = 段中点/duration_sec）\n\n"
            "**3 组正反例（最高优先级）**——C 模式实验的具体教训：\n"
            "1. **致命伤保护**：\n"
            "   - ❌ 反例：报「缺服装色」→ 整段重写 ff_desc（顺手删心口暗红洇痕）\n"
            "   - ✅ 正例：报「缺服装色」→ 仅追加「刺客玄色劲装」5 字到 ff_desc 末尾，\n"
            "     已有的「心口暗红洇痕/仰卧/听香跪坐」一字不动\n"
            "2. **视线方位**：\n"
            "   - ❌ 反例：报「视线无方位」→ 删整句「杨旭望一眼星空」\n"
            "   - ✅ 正例：报「视线无方位」→ 改写为「杨旭仰望画上方星空」\n"
            "     （加方位词 + 保留原意，**这是规则 22.5a 硬性要求，禁砍**）\n"
            "3. **lf_desc 完整性（v6.37 实测坑）**：\n"
            "   - ❌ 反例：报「服装色遗漏」→ 整句改写「杨旭仰卧」为「杨旭仰卧船头」\n"
            "     （丢原 anchor 短语 + lf_desc 砍到 21 字 / 产生内部矛盾\n"
            "     「已没入水下」+「举刀正对」单句并存）\n"
            "   - ✅ 正例：报「服装色遗漏」→ 仅追加「素白轻袍」到 lf_desc 末尾，\n"
            "     原 lf_desc 完整保留 + ≥40 字下限（防 lf_desc 整体被砍短）\n\n"
            "**同角色跨镜一致性（最高优先级）**：\n"
            "同一角色在多镜出现时，**服装色 / 当前状态（坐/站/仰卧）/ kf 顺序** 必须跨镜一致：\n"
            "- 服装色：shot_1 杨旭素白轻袍 → shot_5 杨旭也必须素白轻袍，禁中途换色\n"
            "- 当前状态：shot_3 杨旭盘坐 → shot_4 杨旭仍盘坐，禁无动机跳变（站起/躺下需承接）\n"
            "- 致命伤持续：被刺后所有镜头都需带「心口暗红洇痕」（不只是伤镜）\n\n"
            "修复要点：\n"
            "1. ff/lf_desc 锚点：威胁源写朝向/目标、出镜角色重述服装色\n"
            "2. keyframes desc：补角色姿态/位置锚定\n"
            "3. 保持原文锚定（narration_segment 逐字来自原文）、服装色、致命伤连续性不变\n"
            "4. 镜头衔接/动作重叠（跨镜）：看 REPAIR FEEDBACK 中「current」字段"
            "——相邻镜头同地点时在结尾补衔接词（自然过渡/淡入淡出等），后一镜"
            "开头承接前一镜结尾的动作，禁止无承接硬切\n"
            "5. 景别跳跃（无夹心的真跳跃）：把后一镜的景别改为中间级（如全景→中景），"
            "同步改该镜 prompt 开头的景别词与 ff_desc 构图——禁止只改景别词不改"
            "构图（图视不一致），也禁止增删镜头"
        )

        # user_inputs：两条 user 消息
        _user_inputs = [
            {"role": "user", "content": _input_turn},
            {"role": "user", "content": _repair_turn},
        ]

        # 向后兼容：旧 user_prompt 也保留（其他 provider 走 chat 时仍拼一段）
        # 旧实现：prompt = build_prompt(novel_text, bank, objects_bank) + repair_prompt
        # 新实现：user_inputs 拆两条；user_prompt 用作"未支持多轮的 provider"的回退
        prompt = build_prompt(novel_text, bank, objects_bank)

        # 08-21 mode 合并：单一 unified fallback（与 _repair_turn 同源，压缩为单行 + 决策树/正反例/跨镜一致性）
        prompt = prompt + (
            "\n\n# 定向修复\n"
            "以下是分镜问题清单。请**逐条**修复，每条 warn 改一处字段即可。"
            "输出完整修正后的分镜 JSON（保留全部镜头结构与未提及字段）。"
            f"{_global_block}"
            + _top3_hint +
            "**手术刀原则（最高优先级）**：一条 warn 改一处——不要批量重写整镜；"
            "禁止新增任何短语（动作/情绪/运镜/光位/转场/视角/装饰性方位），"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但禁止写成无空间方位词；"
            "禁止改景别/时长/台词/剧情/narration_segment 文本；禁止增删镜头、改 shot.index；"
            "**lf_desc 至少 40 字**，含本镜所有出镜角色完整静态布局——过短会产生内部矛盾。\n"
            "**关键字段保留白名单（最高优先级）**：致命伤 anchor（心口/洇痕/暗红/中刀/中剑/伤口/血痕 等）"
            "+ 服装色（每个出镜角色，取自角色库）+ 角色当前状态（坐/站/仰卧/跪坐/持物 等）"
            "+ 锚点连贯子句（前镜 lf_desc 已锚定的姿态/位置/朝向，跨镜承接必须保留）"
            "+ color_tone（场景色调基底词）——即使不在 warn 列表中也**禁止删/改/移位**"
            "（C 模式实验教训：改服装色 warn 连带删掉致命伤 anchor）。\n"
            "**Mask 字段清单（本轮禁止修改）**：narration_segment / index / duration_sec /"
            " subtitle / variation_type / scene / location——结构性字段，改则破坏排序/时长/原文锚定。\n"
            "**同镜内 warn 改字段时必须保留同字段 anchor（最高优先级）**：改 ff_desc/lf_desc/prompt"
            " 的某条 warn 时，**禁止**顺手删该字段已有的 anchor 子句（致命伤/服装色/角色状态）。\n"
            "**修复决策树**（精简版）：缺服装色→追加；视线无方位→加画X/目光；致命伤缺→补洇痕；"
            "kf矛盾→改kf.desc；景别跳→改中间级；光位模糊→具体光位。\n"
            "**3 组正反例（按例做）**：致命伤保护=追加5字不删anchor；视线方位=加方位词不砍原意；"
            "lf_desc完整性=追加不砍到<40字（防「已没入水下」+「举刀正对」矛盾）。\n"
            "**同角色跨镜一致性**：服装色/状态/kf顺序跨镜一致（杨旭素白轻袍不中途换色，致命伤持续到所有伤后镜）。"
            + "\n".join(parts)
        )

        service = create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        req = TextGenerationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            user_inputs=_user_inputs,  # v6.37 结构化多轮
            max_tokens=50000,  # 08-15：第2轮全量告警重写镜头更多，输出=完整脚本，留出截断余量
            temperature=0.2,
            json_schema=SCRIPT_SCHEMA,
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _logger.warn(f"[WARN] 定向修复失败（保留原版）: {e}")
            break
        if not result.success:
            _logger.warn(f"[WARN] 定向修复失败（保留原版）: {result.error}")
            break

        import re as _re
        raw = result.text.strip()
        raw = _re.sub(r"^```json\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"^```\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"```\s*$", "", raw, flags=_re.MULTILINE)
        try:
            new_script = parse_response(raw, novel_text=novel_text)
        except (ValueError, json.JSONDecodeError) as e:
            _logger.warn(f"[WARN] 定向修复 JSON 解析失败（保留原版）: {e}")
            break

        if not new_script.get("shots"):
            _logger.warn("[WARN] 定向修复返回空 shots，保留原版")
            break

        # 只采纳修复过的镜头，其余镜头保持原样（合并回 script）
        # 08-15（v6.36 P18 修复）：merge 强制以 picked 为门闩——此前只按 index 匹配就
        # 全量采纳修复 LLM 的整本重写（25/25 镜），非选中镜头（23/25）的改写也一并落地，
        # 光位×4/开篇冲突/爆点密度/内容密度 等新告警全由「全量采纳」引入。注释写「只
        # 采纳修复过的镜头」但代码从没查 picked——v6.36 回归根因。现在 picked 镜内仍受
        # 「手术刀原则」约束；跨镜告警（衔接/越轴）的对镜纳入 picked 属 PR-3。
        merged = dict(script)
        merged["shots"] = []
        for old_s in script.get("shots", []):
            idx = old_s.get("index")
            new_s = next((n for n in new_script.get("shots", [])
                          if n.get("index") == idx), None)
            if new_s and f"shot_{idx}" in picked:
                merged["shots"].append(new_s)
            else:
                merged["shots"].append(old_s)
        if "hook" in picked and new_script.get("hook"):
            merged["hook"] = new_script["hook"]
        # 修复后需重新算时间轴/注入全局渲染参数
        # 08-15 修复：正确函数在 style_prefix（_post_process:186 同款），原 import 自
        # shared.vimax_bridge 不存在 → 修复轮合并后 ImportError 崩溃，修复结果从未落地
        merged = style_prefix.inject_global_config_into_script(merged)
        _compute_timecodes(merged)
        script = merged
        _logger.info(f"[REPAIR] 第 {_round} 轮定向修复完成，重新校验...")
    # 08-15：修复轮合并后强制再跑一遍 post_fix_shot_prompts（幂等）——merge 保留的
    # 旧镜头可能带着改版前就有的机械残留（如「迅速@lf」，parse_response 只修过
    # prompt/motion），确定性清掉；也防修复 LLM 引入新的机械噪音。下一轮
    # _collect_repair_warnings 校验的就是已 post_fix 的文本，少报一条废告警。
    from step01_script_gen.validation import post_fix_shot_prompts as _pfix
    try:
        script = _pfix(script)
    except Exception as _pfx:
        _logger.warn(f"[WARN] 修复后 post_fix 失败（保留原版）: {_pfx}")
    # 08-19：C 模式默认后，修复轮 LLM 可能删掉致命伤锚定（A→C 实测 2 个镜头"
    # 心口暗红洇痕"被删，违反规则 8 最高优先级）。修复完成后跑 hard_fix 致命伤
    # 兜底 + kf 时段对齐，把 LLM 弄丢的确定性补回来（幂等，不伤已有正确内容）。
    # v6.42：改用新 Constraint 框架（29 条），保留 HARD_FIX_USE_OLD=1 回退开关
    from step01_script_gen.constraints import run_hard_fix as _hfix
    try:
        script = _hfix(script, is_in_repair_loop=True, is_final_pass=False)
    except Exception as _hfx:
        _logger.warn(f"[WARN] 修复后 hard_fix 兜底失败（保留原版）: {_hfx}")
    # 08-19：hard_fix_injury_continuity 按 location 精确匹配 + victim 提取不准，
    # 会漏补"杨旭在船上仰卧但镜头 location 写岸边"这类情况。再加一道轻量兜底：
    # 扫所有镜头，**刺杀发生后**（build_idx 之后）的镜头里，ff_desc/lf_desc/prompt
    # 有"杨旭仰卧/倒下/躺"但没 anchor 词 → prompt 末尾追加伤处锚。
    # 只在修复轮后调用，范围小。用 build_idx 作分界避免把"夜宴仰卧喝酒"也补成带伤。
    _injury_lay_words = ("仰卧", "倒下", "仰倒", "躺于", "躺倒", "倒地", "瘫倒")
    from step01_script_gen.validation import _INJURY_ANCHOR_WORDS as _anchor_words
    # 找 build_idx（第一个有 anchor 词的镜头 = 致命伤建立点）
    _build_idx = None
    for _i, _s in enumerate(script.get("shots", [])):
        _txt = _s.get("prompt", "") + _s.get("ff_desc", "")
        if any(w in _txt for w in _anchor_words):
            _build_idx = _i
            break
    _inj_fixed = 0
    if _build_idx is not None:
        for _i, _s in enumerate(script.get("shots", [])):
            if _i <= _build_idx:
                continue  # 刺杀发生前的镜头不补（夜宴仰卧喝酒不算伤）
            _text = _s.get("prompt", "") + _s.get("ff_desc", "") + _s.get("lf_desc", "")
            if not any(w in _text for w in _injury_lay_words):
                continue  # 杨旭没倒下，不用补
            if any(w in _text for w in _anchor_words):
                continue  # 已有伤处锚，幂等跳过
            if "杨旭" not in _text:
                continue  # 倒下的不是杨旭，不补
            _s["prompt"] = (_s.get("prompt", "") or "").rstrip("。") + "。伤处白袍暗红洇痕持续可见"
            _inj_fixed += 1
    if _inj_fixed:
        _logger.info(f"[REPAIR] 致命伤兜底补锚: {_inj_fixed} 镜（C 模式修复轮后丢失的伤处锚）")
    return script


# ============================================================================
# 6. 场景/道具资产提取（v7 新增）
# ============================================================================

def _extract_assets_and_update_script(script: dict) -> None:
    """从分镜脚本提取场景资产 + 道具资产，注册入库并写回 script.

    依赖 script 的 shots[].scene/location/prompt（脚本生成/收敛之后才能调用）。
    两个失败点（LLM 提取失败 / 注册失败）都只 WARN 不阻断流水线：
    下游 step02a 资产生成与 step02b r2v 参考在 bank 为空时自动跳过。

    Args:
        script: script_result.json dict（原地修改，写入 scene_assets/prop_assets）
    """
    # -- 场景资产提取（大场景/近景功能场景描述，供资产图生成与 r2v 场景参考） --
    scene_assets_bank = SceneAssetsBank()
    scene_assets_bank.load()
    if SCENE_ASSET_EXTRACTION_ENABLED:
        _logger.info("[INFO] 提取本章场景资产（大场景/近景描述）...")
        try:
            _scene_assets = extract_scene_assets(script, scene_assets_bank)
            for _sa in _scene_assets:
                scene_assets_bank.register_scene(_sa, NOVEL_CHAPTER)
            scene_assets_bank.save()
            _logger.info(f"[INFO] 已提取 {len(_scene_assets)} 个场景资产（已保存到场景资产库）")
        except Exception as e:
            _logger.warn(f"[WARN] 提取场景资产失败: {e}，继续流水线...")

    # -- 道具资产提取（核心道具 + 外观描述，供道具资产图生成与 r2v 道具参考） --
    prop_assets_bank = PropAssetsBank()
    prop_assets_bank.load()
    if PROP_ASSET_EXTRACTION_ENABLED:
        _logger.info("[INFO] 提取本章核心道具（外观描述）...")
        try:
            _prop_assets = extract_props(script, prop_assets_bank)
            for _pa in _prop_assets:
                prop_assets_bank.register_prop(_pa, NOVEL_CHAPTER)
            prop_assets_bank.save()
            _logger.info(f"[INFO] 已提取 {len(_prop_assets)} 个道具资产（已保存到道具资产库）")
        except Exception as e:
            _logger.warn(f"[WARN] 提取道具资产失败: {e}，继续流水线...")

    # 场景/道具资产描述写回 script，供 step02a 资产生成与 step02b r2v 参考引用
    _scene_assets_out = {}
    for _sid in scene_assets_bank.get_all_identifiers():
        _sa = scene_assets_bank.get_scene(_sid)
        if _sa:
            _scene_assets_out[_sid] = {
                k: _sa[k] for k in ("location", "time_of_day", "weather", "color_tone",
                                    "wide_desc", "near_desc", "consistent_terms")
                if k in _sa
            }
    script["scene_assets"] = _scene_assets_out

    _prop_assets_out = {}
    # v6.40 08-22：prop_assets 字段 = 本次 LLM 提的 ∪ bank 中 last_chapter >= NOVEL_CHAPTER - 1 的
    # 且 ∉ audit_orphan_props.hallucinated_now 集合
    # 之前 L1148 遍历 bank 全部 → 把 LLM 幻觉的历史累积条目（如活虾/嫩藕/肥鸡）
    # 也写进 script_result.json → 下游 step02a/r2v 看到这些幻觉条目。
    # 改为并集：本次 LLM 提的（保证新提的真用道具）+ bank 中"本章或上章用过且非幻觉"的
    # （避免 LLM 不稳定漏提"舴艋舟"等已 stable 的道具，同时排除"活虾"等历史幻觉）。
    # audit_orphan_props 用 alias + 分镜全文反查识别幻觉，弥补"last_chapter 区分不了
    # 真用 vs 幻觉"的盲区。
    from step01_script_gen.asset_extractor import audit_orphan_props
    _audit = audit_orphan_props(
        prop_assets_bank, current_chapter=NOVEL_CHAPTER, current_script=script,
        orphan_window=3,
    )
    _hallucinated = set(_audit.get("hallucinated_now", []))
    if _hallucinated:
        _logger.info(
            f"[INFO] audit 拦截 bank 幻觉 prop: {len(_hallucinated)} 个 → {sorted(_hallucinated)}"
        )
    # 1) 本次 LLM 提的（优先，含新提的真用道具）
    for _pa in _prop_assets:
        _pid = _pa.get("identifier", "").strip()
        if not _pid:
            continue
        _prop_assets_out[_pid] = {
            k: _pa[k] for k in ("category", "appearance", "consistent_terms")
            if k in _pa
        }
    # 2) bank 中本章或上章用到且非幻觉的（LLM 漏提兜底）
    _cutoff = NOVEL_CHAPTER - 1
    for _pid in prop_assets_bank.get_all_identifiers():
        if _pid in _prop_assets_out or _pid in _hallucinated:
            continue
        _pa = prop_assets_bank.get_prop(_pid)
        if not _pa:
            continue
        _last = _pa.get("last_chapter")
        if _last is None or _last < _cutoff:
            continue
        _prop_assets_out[_pid] = {
            k: _pa[k] for k in ("category", "appearance", "consistent_terms")
            if k in _pa
        }
    script["prop_assets"] = _prop_assets_out
    if _scene_assets_out or _prop_assets_out:
        _logger.info(
            f"[INFO] 场景资产 {len(_scene_assets_out)} 个 / 道具资产 {len(_prop_assets_out)} 个 已写入 script_result.json"
        )


# ============================================================================
# 6. 主流程
# ============================================================================

# 08-21：mode 合并（v1-v4 渐进后正式落地）
# 删除了 08-20 重构的 A/B/C 三套反馈聚合模式（mode A 精简、mode B +原文、mode C 全片分布）。
# 原因：v3 同步 4 类约束后，三种 mode 90% 内容相同；v4 加决策树/正反例/跨镜一致性后，
# 差异化消失；用户几乎不切换 mode（A 实验后 C 胜出成为默认）。保留 3 套代码只增维护成本。
# 合并后 unified 模式：4 类约束 + 决策树 + 3 组正反例 + 跨镜一致性 + 全片错误分布 + 原文切片
# （原 mode B 的原文切片对 grounding 类问题很有用，无副作用全量注入）。


def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存.

    控制台静默策略：运行期间所有 print()（校验 WARN、LLM 推理过程等）
    重定向到 step01.log，控制台只显示关键进度和最终摘要。
    """

    _logger.separator()
    _logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _logger.info("  新增: 钩子分镜 + 敏感词校验")
    _logger.separator()

    # 无声视频模型告警：VIDEO_MODEL_VOICE=1（对话交给视频模型）但当前模型不生成语音时，
    # 成片会无对白/背景音。Seedance 1.0 系列 / MiniMax v1 Hailuo 均无声（08-16 适配）。
    _warn_silent_video_model()

    # 创建输出目录
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

    # 运行期间 print() 全部重定向到 step01.log，控制台静默
    # 最终摘要用 _console() 写 _REAL_STDOUT（模块级保存的真实 stdout）
    # 同时让 logger 控制台输出静默，避免 logger.print 经重定向回流日志造成双写
    sys.stdout = _redirect_to_log()
    _logger.console_silent = True

    # 读取章节
    novel_text = read_chapter("锦衣夜行_77119小说.txt", chapter=NOVEL_CHAPTER)
    novel_preview = novel_text[:1000]

    # 加载角色库（v6.1 新增）
    character_bank = CharacterBank()
    character_bank.load()

    # 预提取本章角色信息，辅助分镜生成（v6.1 新增）
    # 这样 LLM 生成脚本时能看到本章新角色的特征描述
    # 预提取结果存到 pre_extracted_chars，主流程后段（角色提取）直接复用，避免二次 LLM 调用
    pre_extracted_chars = None
    if CHARACTER_EXTRACTION_ENABLED:
        _logger.info("[INFO] 预提取本章角色信息以辅助分镜生成...")
        try:
            chars = extract_characters(novel_text, character_bank)
            pre_extracted_chars = chars
            # 注册新角色到 bank（暂存本章角色，供生成时使用）
            for c in chars:
                ident = c.get("identifier", "")
                if not ident:
                    continue
                if c.get("is_new", True):
                    # 新角色：首次注册
                    character_bank.register_character(
                        ident,
                        c.get("static_features", ""),
                        c.get("dynamic_features", ""),
                        NOVEL_CHAPTER,
                    )
                elif c.get("dynamic_features"):
                    # 已知角色：如果 LLM 返回了 dynamic_features，则更新衣着
                    # 这解决了"衣着与本章内容不一致"的问题
                    character_bank.update_dynamic_features(
                        ident,
                        c.get("dynamic_features", ""),
                    )
                    character_bank.update_character_chapter(ident, NOVEL_CHAPTER)
            # v6.1 优化：立即保存，确保即使脚本生成失败，角色库也已更新
            character_bank.save()
            _logger.info(f"[INFO] 预提取到 {len(chars)} 个角色特征（已保存到角色库）")
        except Exception as e:
            _logger.warn(f"[WARN] 预提取角色失败: {e}，继续生成脚本...")

    # 预提取本章场景物体尺寸，辅助分镜生成（v6.6 新增）
    # 从原文上下文推导船/车/厅堂等空间物体的合理尺寸/容量/结构，
    # 替代硬编码规则，防止画面比例漂移（图片模型把船画成只能装2人的小舢板）
    scene_objects_bank = SceneObjectsBank()
    scene_objects_bank.load()
    if SCENE_OBJECT_EXTRACTION_ENABLED:
        _logger.info("[INFO] 预提取本章场景物体尺寸以辅助分镜生成...")
        try:
            objs = extract_scene_objects(novel_text, scene_objects_bank)
            for o in objs:
                scene_objects_bank.register_object(o, NOVEL_CHAPTER)
            scene_objects_bank.save()
            _logger.info(f"[INFO] 预提取到 {len(objs)} 个场景物体（已保存到场景物体库）")
        except Exception as e:
            _logger.warn(f"[WARN] 预提取场景物体失败: {e}，继续生成脚本...")

    # 检查缓存（场景物体库内容变化也视为源变更，v6.6）
    objects_bank_data = json.dumps(
        scene_objects_bank._data, ensure_ascii=False, sort_keys=True
    )
    prompt_changed = _prompt_changed(
        Path(__file__).parent / "prompts.py", SCRIPT_FILE, novel_preview, NOVEL_CHAPTER,
        objects_bank_data,
    )

    if not prompt_changed:
        _logger.info(f"[CACHE] 脚本已缓存: {SCRIPT_FILE}")
        with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
            script = json.load(f)
        # 补写章节元信息（部名/章名，老缓存可能缺失）
        if not script.get("chapter_title"):
            script.update(get_chapter_meta("锦衣夜行_77119小说.txt", NOVEL_CHAPTER))
        # 重新计算时间轴（使用增强算法）
        _compute_timecodes(script)
        # C5 修复（08-11）：fps 一致性校验（timeline 此时已存在）
        _check_fps_consistency(script)
        # v7: 缓存命中时也要跑场景/道具资产提取（老缓存无 scene_assets/prop_assets），
        # 结果写回 script 再落盘，保证 step02a 资产生成能拿到数据
        _extract_assets_and_update_script(script)
        with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        _logger.info("时间轴已重新计算")
        # 缓存命中：恢复 stdout 并显示最终摘要
        sys.stdout = _REAL_STDOUT
        _logger.console_silent = False
        shots = script.get("shots", [])
        _console("")
        _console("=" * 56)
        _console("  [步骤1] 分镜脚本摘要（缓存命中，未重新生成）")
        _console("=" * 56)
        _console(f"  输出: {SCRIPT_FILE}")
        _console(f"  镜头数: {len(shots)}" + ("  + 钩子" if script.get("hook") else ""))
        _console(f"  总时长: {script.get('total_duration', 0):.1f}s")
        _sa = script.get("scene_assets", {})
        _pa = script.get("prop_assets", {})
        if _sa or _pa:
            _console(f"  资产: 场景{len(_sa)} / 道具{len(_pa)}")
        _console("=" * 56)
        return

    # 生成脚本（含 JSON 解析重试）
    # LLM 偶发输出损坏的 JSON（DeepSeek 尤甚：内部中文引号未转义/缺逗号等）。
    # 解析失败时重新调用 LLM 重试，而不是让整个流水线崩溃（每次约 3-6 分钟）。
    script = None
    last_err = None
    for attempt in range(1, 1 + 3):
        try:
            raw = gen_spy_script(novel_text, character_bank, scene_objects_bank)
        except RuntimeError as e:
            # 生成失败（如 DeepSeek 返回空文本）：短暂等待后重试，而不是中断流水线
            last_err = e
            _logger.warn(f"[WARN] LLM 生成失败（尝试 {attempt}/3）: {e}")
            _console(f"  [重试] LLM 生成失败，重新调用（{attempt}/3）...")
            import time as _t
            _t.sleep(5)
            continue
        try:
            script = parse_response(raw, novel_text=novel_text)
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            _logger.warn(f"[WARN] 分镜 JSON 解析失败（尝试 {attempt}/3）: {e}")
            _console(f"  [重试] JSON 解析失败，重新调用 LLM（{attempt}/3）...")
    if script is None:
        raise RuntimeError(f"分镜 JSON 连续 3 次解析失败: {last_err}")

    # ========== 差距分析（v5.0 新增）==========
    # 若差距过大，重新生成（最多2次）

    if GAP_ANALYSIS_ENABLED:
        for attempt in range(GAP_MAX_REGEN_ATTEMPTS):
            # 快速校验
            validation = validate_script_gap(script, novel_text)

            if validation["overall_pass"]:
                _logger.info(f"[GAP] 差距分析通过 (尝试 {attempt + 1}/{GAP_MAX_REGEN_ATTEMPTS})")
                # 保存脚本并生成完整报告
                partial_path = CHAPTER_DIR / "script_result.partial.json"
                with open(partial_path, "w", encoding="utf-8") as _f:
                    json.dump(script, _f, ensure_ascii=False, indent=2)
                report = generate_gap_report(
                    str(partial_path),
                    "锦衣夜行_77119小说.txt",
                    NOVEL_CHAPTER
                )
                # 打印报告摘要
                print_gap_report(report)
                # 清理临时文件
                if partial_path.exists():
                    partial_path.unlink()
                break

            # 发现问题，反馈给 LLM 重新生成
            _logger.warn(f"[GAP] 发现差距问题， 重新生成 (尝试 {attempt + 1}/{GAP_MAX_REGEN_ATTEMPTS})...")

            # 获取差距分析上下文并追加到 prompt（函数已在模块级导入）
            gap_context = _load_gap_analysis_context(validation)
            # v6.33（08-12）：附加 checklist 聚合反馈——差距分析之外，把内容密度/景别
            # 跳跃/致命伤等真实缺陷也喂给重生成 LLM（此前只喂覆盖率/分布，重生成还是老问题）
            _checklist_fb = build_regen_feedback(script, novel_text)
            if _checklist_fb:
                gap_context = gap_context + _checklist_fb

            # 追加差距反馈到 prompt，重新生成
            prompt = build_prompt(novel_text, character_bank, scene_objects_bank)
            prompt = prompt + gap_context

            service = create_service()
            from _text_gen.deepseek_schema import SCRIPT_SCHEMA
            req = TextGenerationRequest(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=40000,
                temperature=0.2,  # v6.5: 降低温度防止幻觉
                json_schema=SCRIPT_SCHEMA,
            )
            result = service.generate(req)
            if not result.success:
                raise RuntimeError(f"LLM 重新生成失败: {result.error}")

            import re
            raw = result.text.strip()
            raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

            try:
                script = parse_response(raw, novel_text=novel_text)
            except (ValueError, json.JSONDecodeError) as e:
                # 重生成结果 JSON 损坏 → 保留上一版，不中断流水线
                _logger.warn(f"[WARN] 重生成 JSON 解析失败，保留上一版: {e}")
                break
            # 不在循环内 post_process：循环退出后统一在 line 334 做一次
            # （code-audit 07-31 #7：原 :328 与 :334 重复调用 _post_process）
        else:
            # 达到最大尝试次数仍有问题，记录但不中断
            _logger.warn("[GAP] 达到最大重试次数，仍有差距问题，将继续处理")

    # v6.7: 镜头数严重超限时收敛重生成（独立于 gap_analysis，默认开启）
    if SHOT_COUNT_REGEN_ENABLED:
        script = _regen_over_limit(
            script, novel_text,
            bank=character_bank, objects_bank=scene_objects_bank,
        )

    # v6.35（08-15，P18 闭环）：GAP 通过后仍存在的高价值告警（开篇冲突/
    # 爆点密度/威胁源朝向/关键帧质量/内容密度等）→ 定向修复轮，只重写
    # 告警镜头（此前这些 WARN 纯打印落盘，无人修，带伤进 step02）。
    if REPAIR_ROUNDS_ENABLED:
        script = _repair_shot_warnings(
            script, novel_text,
            bank=character_bank, objects_bank=scene_objects_bank,
        )

    # 后处理：注入全局配置 + 合规过滤 + 敏感词校验
    script = _post_process(script)

    # 脚本已更新，删除旧的音频和视频文件。
    # 2026-08-09 用户需求：若新旧脚本只是分镜数量差异且总时长≤3min，逐镜对比后
    # 保留未变镜头的帧/视频产物，只清理新增/改动/删除的镜头（避免重跑 step02 全量
    # 重新生成白烧额度）。
    # 2026-08-09 修复：原实现把此段放在 script 生成之前，引用未赋值变量 script
    # 触发 UnboundLocalError，逐镜保留从未生效（每次都全量清理）。现在移到
    # _post_process 之后——新分镜已定稿（含收敛/合规过滤），旧 script_result.json
    # 仍在磁盘可读，对比才有意义；且 LLM 生成失败时旧产物也不会被提前删掉。
    _preserve_shots: list[int] = []
    try:
        from shared.shot_diff import compute_shot_diff, should_preserve_artifacts
        _old_script = json.load(open(SCRIPT_FILE, encoding="utf-8")) if SCRIPT_FILE.exists() else None
        if _old_script and should_preserve_artifacts(_old_script, script):
            _diff = compute_shot_diff(_old_script, script)
            _preserve_shots = _diff.get("unchanged", [])
            _logger.info(
                f"[PRESERVE] 逐镜对比放行：未变 {len(_preserve_shots)} 镜"
                f"（保留旧产物），新增/改动 {len(_diff.get('added', [])) + len(_diff.get('changed', []))} 镜重新生成"
            )
            # 只清理 changed/removed 镜头的帧/视频（未变镜头保留）
            for _sub in ("vimax_working", "stretched"):
                _sub_path = CHAPTER_DIR / _sub
                if _sub_path.exists():
                    _shots_root = _sub_path / "shots"
                    if _shots_root.exists():
                        for _sid in _diff.get("changed", []) + _diff.get("removed", []):
                            _shot_dir = _shots_root / str(_sid)
                            if _shot_dir.exists():
                                import shutil as _sh
                                _sh.rmtree(_shot_dir)
                                _logger.verbose(f"  [已清理] {_sub}/shots/{_sid}/（内容变更）")
    except Exception as _preserve_err:
        _logger.warn(f"[PRESERVE] 逐镜对比失败，全量清理: {_preserve_err}")

    if not _preserve_shots:
        _logger.info(f"[INFO] 分镜脚本已更新，清理旧的音视频文件...")
        for subdir in ("audio", "vimax_working", "stretched"):
            subdir_path = CHAPTER_DIR / subdir
            if subdir_path.exists():
                import shutil
                shutil.rmtree(subdir_path)
                _logger.verbose(f"  [已删除] {subdir}/")
    # 删除最终视频和字幕（脚本结构变了，最终产物必须重做）
    for f in ("final.mp4", "subtitles.srt"):
        fpath = CHAPTER_DIR / f
        if fpath.exists():
            fpath.unlink()
            _logger.verbose(f"  [已删除] {f}")

    # ========================================================================
    # 钩子分镜生成（v6 沿用 + v6.39 调用顺序硬约束）
    # ========================================================================
    # 严格 3 步调用顺序（C4 修复 08-11 + v6.32 强化 08-12），禁止任何步骤乱序：
    #
    #   ┌─────────────────────────────────────────────────────────────┐
    #   │  ① 提取 forbidden_lines  ← 必须在主分镜 shots 落盘后做         │
    #   │     (剥说话人前缀的台词体列表，注入钩子 prompt 防重复穿帮)      │
    #   │                          ↓                                  │
    #   │  ② 调用 hook = gen_hook_script(forbidden_lines=...)          │
    #   │     (钩子 LLM 在 prompt 看到禁止清单，从源头不写重复台词)       │
    #   │                          ↓                                  │
    #   │  ③ 合并 + 去重 retry + 兜底清空                               │
    #   │     (挂载后比对台词，仍重复则重试 2 次；再重复则清空台词字段)     │
    #   └─────────────────────────────────────────────────────────────┘
    #
    # 关键 invariant（破坏任意一条会引发「钩子台词=主分镜台词」倒叙穿帮）：
    #   - 步骤① forbidden_lines 必须在 shots 落盘后才能采集（空列表=无效）
    #   - 步骤② gen_hook_script 调用时 forbidden_lines 必须非 None
    #   - 步骤③ _hook_dup_hits 比对时 shots 列表未变（避免 race）
    # ========================================================================
    if HOOK_ENABLED:
        # ===== ① 提取 forbidden_lines（assert 保护调用顺序）=====
        _shots_for_hook = script.get("shots", [])
        assert _shots_for_hook, (
            "HOOK pipeline 顺序保护：步骤②前必须先生成主分镜 shots。"
            "当前 script['shots'] 为空——主分镜 LLM 调用可能失败或被跳过。"
        )
        _forbidden_lines: list[str] = []
        for _s in _shots_for_hook:
            _body = _speech_body(_s.get("narration_segment", ""))
            if _body:
                _forbidden_lines.append(_body)
        _logger.info(f"[INFO] 钩子禁止台词清单已就绪（{len(_forbidden_lines)} 条）")

        # ===== ② 调用 hook LLM（注入 forbidden_lines）=====
        _logger.info("\n[INFO] 开始生成钩子分镜...")
        hook = gen_hook_script(
            novel_text,
            bank=character_bank,
            objects_bank=scene_objects_bank,
            forbidden_lines=_forbidden_lines,  # 必传参数，v6.32 强约束
        )

        if hook:
            # ===== ③ 合并 + 去重 retry + 兜底清空 =====
            script["hook"] = hook
            _logger.info(f"[OK] 钩子分镜已生成: {hook.get('narration_segment', '')[:30]}...")

            # 闭包：引用外层 _shots_for_hook（步骤③ shots 列表不变 invariant）
            def _hook_dup_hits() -> list:
                """钩子台词与主分镜任一镜头台词重复的镜头 index 列表（空=无重复）."""
                _hl = _speech_body(hook.get("narration_segment", ""))
                _hits = []
                if not _hl:
                    return _hits
                for _s in _shots_for_hook:  # ← 闭包用 _shots_for_hook 而非 script.get，避免 race
                    _sb = _speech_body(_s.get("narration_segment", ""))
                    if _sb and (_hl == _sb or _hl in _sb or _sb in _hl):
                        _hits.append(_s.get("index", "?"))
                return _hits

            # C4 修复（08-11）+ v6.32 强化（08-12）：挂载后立即比对台词去重，
            # 重复则自动重新生成钩子（换一句台词），最多再试 2 次；仍重复才 WARN
            # 提示人工处理。钩子是「最精彩瞬间」的倒叙，与主分镜台词重复会直接
            # 穿帮（观众刚在钩子听到、又在下个镜头原样听到）。
            _dup_hits = _hook_dup_hits()
            _regen_attempt = 0
            while _dup_hits and _regen_attempt < 2:
                _regen_attempt += 1
                _hook_line_prev = _speech_body(hook.get("narration_segment", ""))
                _logger.warn(
                    f"[WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复（C4 修复），"
                    f"自动重新生成钩子换台词（尝试 {_regen_attempt}/2）..."
                )
                _console(f"  [WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复，重新生成钩子（{_regen_attempt}/2）...")
                _dup_note = (
                    f"上一版钩子台词「{_hook_line_prev[:30]}」与主分镜镜头 {_dup_hits} 的台词重复"
                    f"（观众刚在钩子听到、又在正片听到，倒叙穿帮）。请严格换一句完全不同的台词：\n"
                    f"1. 优先选主分镜其它镜头未用过的对话（如张十三的质问/密谋、杨旭的台词、肖管事的试探）；\n"
                    f"2. 若所有人物对话都与主分镜重复，则把 narration_segment 和 subtitle 设为空字符串\"\"，"
                    f"钩子改纯画面动作开场（刺客破水而出、匕首出鞘的瞬间），靠视觉冲击不靠台词。"
                )
                _hook_retry = gen_hook_script(
                    novel_text, bank=character_bank, objects_bank=scene_objects_bank,
                    forbidden_lines=_forbidden_lines, dup_note=_dup_note,
                )
                if not _hook_retry:
                    break  # 重生成失败，保留原钩子
                hook = _hook_retry
                script["hook"] = hook
                _logger.info(f"[OK] 钩子分镜已重新生成: {hook.get('narration_segment', '')[:30]}...")
                _dup_hits = _hook_dup_hits()
            if _dup_hits:
                # 确定性兜底（08-12）：重试仍重复则清空钩子台词，改纯画面开场——
                # 钩子画面（刺客破水+匕首下刺）本身是强开场，宁可无台词也不倒叙穿帮。
                hook["narration_segment"] = ""
                hook["subtitle"] = ""
                _logger.warn(
                    f"[WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复且重生成仍未解决，"
                    f"已强制改为纯画面开场（narration_segment 清空）——无台词优于倒叙穿帮。"
                )
                _console(f"  [WARN] 钩子台词仍重复，已改为纯画面开场")
            # v6.18：钩子在 parse_response 之后才挂载（_validate_script 已跑完），
            # 必须补自动修复 + 校验，否则钩子 ff_desc 零校验零修复（多动作堆叠
            # 直接落盘，导致首帧图画不出三个同时态动作）。
            try:
                from step01_script_gen.validation import post_fix_shot_prompts, validate_hook
                post_fix_shot_prompts(script)  # 幂等：shots 已修复过，主要触发 hook 分支
                validate_hook(script["hook"], script.get("characters", []))
            except Exception as _hook_fix_err:
                _logger.warn(f"[WARN] 钩子修复/校验异常（不阻断）: {_hook_fix_err}")
        else:
            _logger.info("[INFO] 跳过钩子分镜")

    # 提取角色（跨章节一致性）
    if CHARACTER_EXTRACTION_ENABLED:
        if pre_extracted_chars is not None:
            # 复用预提取结果，避免二次 LLM 调用（角色库已在预提取阶段保存）
            chars = pre_extracted_chars
            _logger.info(f"[INFO] 复用预提取的 {len(chars)} 个角色")
        else:
            # 预提取阶段失败时才重新提取
            _logger.info("[INFO] 提取角色信息...")
            # #26 修复 2026-08-14：LLM 提取失败不应中断整条流水线——
            # 回退到脚本解析阶段已收集的 characters（可能为空），仅 WARN。
            try:
                chars = extract_characters(novel_text, character_bank)
            except Exception as _char_err:
                _logger.warn(
                    f"[WARN] 角色提取失败（不阻断）: {_char_err}，"
                    f"回退使用脚本内已有 characters")
                chars = script.get("characters", []) or []
        script["characters"] = chars
        # 注意：角色库已在预提取阶段保存，无需重复保存
        _logger.info(f"[INFO] 已提取 {len(chars)} 个角色")

    # 写入场景物体锚定（v6.6）：供下游 step02/validation 引用统一尺寸
    _scene_objects_out = {}
    for _oid in scene_objects_bank.get_all_identifiers():
        _obj = scene_objects_bank.get_object(_oid)
        if _obj:
            _scene_objects_out[_oid] = {
                k: _obj[k] for k in ("category", "subtype", "length_m", "width_m",
                                     "height_m", "capacity", "structure",
                                     "consistent_terms")
                if k in _obj
            }
    script["scene_objects"] = _scene_objects_out

    # v7: 场景/道具资产提取（依赖 script 的 shots[].scene/location/prompt，
    # 必须在脚本生成/收敛之后执行；缓存命中路径也调用同一函数）
    _extract_assets_and_update_script(script)

    # 计算时间轴（v5.0 运镜量化参数）
    _compute_timecodes(script)
    # 08-23 v6.41 P19 闭环：硬修复层必须在 _compute_timecodes 之后调用——
    # 此前 hard_fix 在 REPAIR 内部调用，但 line 1659 _compute_timecodes 会按
    # motion_desc 重算 duration_sec，覆盖 W6 时长拉伸的值。改为：每次落盘前
    # 跑一次 hard_fix_shot_prompts（含 W6 时长拉伸），保证最终值持久化。
    # v6.42：改用新 Constraint 框架（is_final_pass=True 让 RuntimeExtendConstraint 拉伸时长）
    from step01_script_gen.constraints import run_hard_fix as _hfix_final
    try:
        script = _hfix_final(script, is_in_repair_loop=False, is_final_pass=True)
    except Exception as _hfe:
        _logger.warn(f"[WARN] 落盘前 hard_fix 失败（保留原版）: {_hfe}")
    # C5 修复（08-11）：fps 一致性校验（timeline 此时已存在）
    _check_fps_consistency(script)

    # 章节元信息（部名/章名，来自小说标题行「第一部 杀青州 第NNN章 章名」）
    script.update(get_chapter_meta("锦衣夜行_77119小说.txt", NOVEL_CHAPTER))

    # 写入 hash
    script["_source_hash"] = _hash_source(
        str(Path(__file__).parent / "prompts.py"), novel_preview, NOVEL_CHAPTER,
        objects_bank_data,
    )

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    _logger.info(f"[OK] 分镜脚本已保存至 {OUTPUT_FILE}")

    # ---- 最终摘要：写真实 stdout（控制台），绕过重定向 ----
    shots = script.get("shots", [])
    _console("")
    _console("=" * 56)
    _console("  [步骤1] 完成 —— 分镜脚本摘要")
    _console("=" * 56)
    _console(f"  输出: {OUTPUT_FILE}")
    _console(f"  镜头数: {len(shots)}" + ("  + 钩子" if script.get("hook") else ""))
    _console(f"  总时长: {script.get('total_duration', 0):.1f}s")
    _scene_objs = script.get("scene_objects", {})
    if _scene_objs:
        _console(f"  场景物体: {', '.join(_scene_objs.keys())}")
    _sa = script.get("scene_assets", {})
    _pa = script.get("prop_assets", {})
    if _sa or _pa:
        _console(f"  资产: 场景{len(_sa)}个 {', '.join(_sa.keys())} / 道具{len(_pa)}个 {', '.join(_pa.keys())}")
    for k in ("resolution", "ratio", "fps", "render_style"):
        if k in script:
            _console(f"  {k}: {script[k]}")
    _console("  前 3 镜:")
    for shot in shots[:3]:
        idx = shot.get("index", "?")
        sub = (shot.get('subtitle', '') or '').strip()
        _console(f"    - 镜{idx}: {sub[:42] if sub else '(空镜/纯画面)'}")
    _console("=" * 56)
    _console("  ⚠️ 详细校验 WARN 已写入 step01.log，控制台不再重复输出")
    _console("=" * 56)

    # 恢复 stdout
    sys.stdout = _REAL_STDOUT
    _logger.console_silent = False



if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        # 完整 traceback 写入 step01.log（verbose 仅文件，不依赖 stderr）
        for line in tb.splitlines():
            _logger.verbose(line)
        # 恢复真实 stdout，控制台显示一行错误摘要
        sys.stdout = _REAL_STDOUT
        _logger.console_silent = False
        last = tb.strip().splitlines()[-1]
        _logger.error(f"[FATAL] {last}")
        sys.exit(1)