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
import re
from collections import Counter as _Counter
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
    SCRIPT_CANDIDATE_TOPK,
)

# v6.23：大纲两阶段开关（ShortVideoDirector director-outline 吸收，默认开启）
OUTLINE_ENABLED = os.getenv("OUTLINE_ENABLED", "1").strip() == "1"

# 输出目录基于 book/chapter 计算（统一路径来源: shared/paths.py）
from shared.paths import (
    chapter_dir,
    script_file,
    audio_dir,
    vimax_working_dir,
    vimax_shots_dir,
    stretched_dir,
    final_file,
    subtitles_file,
)
from shared.constants import SCRIPT_PARSE_RETRIES, SCRIPT_PARSE_RETRY_SLEEP_SEC
from shared.utils.text import text_integrity_errors
from shared.quality_gate import ensure_script_revision, attach_gate_metadata
from step01_script_gen.quality_issues import (
    Issue, collect_structured_issues, is_blocking, RULE_VERSION, RULE_SOURCE_FINGERPRINT,
)
CHAPTER_DIR = chapter_dir()
OUTPUT_FILE = script_file()
SCRIPT_FILE = OUTPUT_FILE

# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(CHAPTER_DIR / "step01.log", console_level="INFO")

# v6.59 (2026-09-04)：检测 shell 环境变量是否覆盖 .env.content 的 PROVIDER 设置。
# 不阻断，仅 WARN 提示。详细设计见 shared/env_guard.py 顶部。
# 切到 agent_plan/doubao 调试时建议 raise_if_override=True 强约束，避免误以为 .env.content 生效。
from shared.env_guard import warn_shell_overrides as _warn_shell_overrides
_warn_shell_overrides(
    ["TEXT_PROVIDER", "IMAGE_PROVIDER", "VIDEO_PROVIDER", "TTS_PROVIDER"],
    logger=_logger,
)


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
    build_prompt, build_hook_prompt, parse_hook_response,
    _load_gap_analysis_context, _build_regen_iron_rules,
    _build_character_anchors,
    _load_knowledge_skills, _load_local_skill_context,
    auto_select_prompt_builder, auto_select_system_prompt,
    resolve_model_prompt_profile,
)

# v2026-09-03: 按 TEXT_PROVIDER 自动选择 prompt builder。
# agent_plan（doubao-seed-2-1-turbo）→ doubao 专属（XML schema + few-shot + workflow）
# 其他 provider → 沿用 deepseek 调优的 build_prompt / build_hook_prompt
from _text_gen.config import TEXT_PROVIDER_DEFAULT
_TEXT_PROVIDER = (os.getenv("TEXT_PROVIDER") or TEXT_PROVIDER_DEFAULT).strip().lower()
build_prompt, build_hook_prompt = auto_select_prompt_builder(_TEXT_PROVIDER)
_SYSTEM_PROMPT = auto_select_system_prompt(_TEXT_PROVIDER)
if _TEXT_PROVIDER == "agent_plan":
    _logger.info(f"[INFO] 使用 doubao 专属 prompt (provider={_TEXT_PROVIDER})")
from step01_script_gen.character_extractor import extract_characters
from step01_script_gen.object_extractor import extract_scene_objects
from step01_script_gen.asset_extractor import extract_scene_assets, extract_props
from step01_script_gen.validation import parse_response, build_regen_feedback, _set_novel_text, _run_prompt_checklist
from step01_script_gen.timecode import (
    _compute_timecodes,
    write_timeline_from_durations,
    timeline_consistency_errors,
)
from step01_script_gen.hash_cache import _prompt_changed, _hash_source
from step01_script_gen.gap_analysis import (
    gap_needs_full_regen,
    generate_gap_report,
    print_gap_report,
    validate_script_gap,
)


from shared import style_prefix
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
        from shared.style_prefix import validate_fps_consistency
        validate_fps_consistency(script)
    except Exception:
        pass


# ============================================================================
# 2. 文本生成
# ============================================================================

def gen_spy_script(novel_text: str, bank=None, objects_bank=None,
                   outline: dict | None = None,
                   temperature: float = 0.2,
                   model: str | None = None,
                   timeout_sec: int | None = None) -> str:
    """调用 LLM 生成脚本，返回原始文本.

    Args:
        outline: 预提取的章节大纲（v6.43 08-25 改为 main() 入口并发预提取，
            避免本函数再独立调一次 LLM 浪费 ~6s）。
        timeout_sec: 请求级客户端超时（v6.70.3 分级——候选 300s，防 600s 慢请求
            拖死整轮；None=provider 默认）。
    """
    from _text_gen.deepseek_schema import SCRIPT_SCHEMA
    # v6.23：大纲两阶段（ShortVideoDirector director-outline 吸收）——大纲作分镜
    # 锚点，保证冲突/高潮分布。失败不阻断（空锚点）。
    outline_anchor = ""
    if outline:
        try:
            from step01_script_gen.outline import outline_to_anchor
            outline_anchor = outline_to_anchor(outline)
            _logger.info("[INFO] 章节大纲已预提取（并发），注入分镜 prompt")
        except Exception as e:
            _logger.warn(f"[WARN] 大纲转 anchor 失败: {e}，继续无大纲生成")
    # v6.68: prompt/system 跟着实际模型走——deepseek-v4-flash → deepseek 剖面；
    # doubao → doubao 剖面。两者都保留完整 novel，由 PROMPT_MAX_CHARS 统一控上限。
    # 此前 agent_plan 下所有模型共用 doubao prompt，是候选结构性零高潮的根因。
    _main_builder, _system = resolve_model_prompt_profile(model)
    prompt = _main_builder(novel_text, bank, objects_bank, outline_anchor)
    _metrics_inc("calls.script_generation")
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=_system,
        user_prompt=prompt,
        temperature=temperature,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
        json_schema=SCRIPT_SCHEMA,
        model=model,  # v6.66: 跨模型候选池——per-request model 覆盖（None=用 provider 默认）
        timeout_sec=timeout_sec,  # v6.70.3: 候选 300s 分级超时
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
                    dup_note: str | None = None,
                    model: str | None = None) -> dict | None:
    """调用 LLM 生成钩子分镜，返回 hook dict 或 None.

    Args:
        novel_text: 小说原文
        bank: CharacterBank 实例，用于注入角色锚点（prompt 中用角色名而非模糊称呼）
        objects_bank: SceneObjectsBank 实例，用于注入场景物体尺寸锚点（prompt 中写出锚定尺寸）
        forbidden_lines: v6.32（08-12）主分镜各镜头台词体列表（_speech_body），
            注入钩子 prompt 防「钩子台词与主分镜重复」倒叙穿帮。
        dup_note: v6.32（08-12）重生成时的去重反馈——上一版钩子台词与主分镜镜头
            重复，把具体重复台词 + 换台词/改纯画面开场的选择喂给 LLM，引导它换一句。
        model: v6.70.1 钩子后置校验轮——互补模型重生钩子（None=provider 默认，
            零行为变化）
    """
    if not HOOK_ENABLED:
        return None

    prompt = build_hook_prompt(novel_text, bank=bank, objects_bank=objects_bank,
                               forbidden_lines=forbidden_lines)
    if dup_note:
        prompt = prompt + f"\n\n# 钩子台词去重反馈（必须遵守）\n{dup_note}"
    _metrics_inc("calls.hook_generation")
    service = create_service()
    from _text_gen.deepseek_schema import HOOK_SCHEMA
    req = TextGenerationRequest(
        system_prompt="你是短视频脚本大师，擅长在视频开头制造悬念和吸引力。禁止血腥暴力细节。",
        user_prompt=prompt,
        temperature=0.2,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
        json_schema=HOOK_SCHEMA,
        model=model,  # v6.70.1 钩子后置校验轮：互补模型重生（None=provider 默认）
        timeout_sec=_repair_timeout_sec(),  # v6.70.3: hook 180s（09-13 实测 600s 超时已让重生白等）
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
            # W16/W17 兜底：标记 is_hook=True，让 step02a vimax_bridge 在生成层
            # 追加泳池/室内禁词（即使 W17 因 hook ff_desc 缺水面词未触发）。
            hook["is_hook"] = True
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

def _complementary_repair_model(winner_model: str | None) -> str | None:
    """Resolve the repair model.

    Multi-model mode keeps the historical complementary-model behavior.  In
    single-model mode we explicitly keep planning/generation/audit/repair on
    the same model instead of falling through to the provider default model.
    """
    from shared.config import SCRIPT_CANDIDATE_MODELS
    models = list(SCRIPT_CANDIDATE_MODELS or [])
    if len(models) == 1:
        return winner_model or models[0]
    if not winner_model or len(models) < 2:
        return None
    if winner_model not in models:
        return None
    for _m in models:
        if _m != winner_model:
            return _m
    return None


def _actual_shots_total(script: dict) -> float:
    """正片**实际** `Σduration_sec`（v6.86 用户决定 B1：时长收敛的判定口径）.

    与 `project_post_split_metrics()` 的区别：那个是**拆镜后投影**（会把超 cap 的
    镜头按 cap 折算并计入拆镜次数），生产路径的拆镜 pre-pass 已停用
    （`timecode.py` 的 `allow_split=False`），所以投影描述的是**不会发生的剧本**。
    投影口径保留给**候选打分**（那里确实要在拆镜前预估），时长收敛与质量门统一用实际值。
    """
    return sum(float(s.get("duration_sec") or s.get("duration") or 0)
               for s in (script.get("shots") or []) if isinstance(s, dict))


# v6.98（2026-09-22 审核 P1-2 复核确认）：完整性身份 = **与镜头序号无关**的稳定指纹。
#
# 只比数量的漏洞（确定性复现）：旧稿有问题 A、新稿修掉 A 而引入问题 B，数量仍是 1
# → 判「未退化」。对状态回退 / 角色 / 武器归属 / 事件结果这类问题，**身份**比数量重要。
#
# 为什么**不含** `shot_id`：整脚本重生成会重排镜头序号（合并 15 镜 → 13 镜），
# 带序号的身份会让**每个**问题都变成「新身份」→ 采纳分支永不通过（这正是本会话
# 反复修的那类「永不收敛」缺口）。序号剔除后，语义是「**同一类问题换了镜头**不算
# 新问题，**换了问题**才算」——正是要判的那件事。
_INTEGRITY_NUM_RE = re.compile(r"[0-9]+(?:[.][0-9]+)?")


def _blocking_identity(issue) -> str:
    """blocking 问题身份：code + 帧 + 实体 + 字段 + **去数字后的消息**.

    消息里的数字（镜头号、秒数、字数）全部归一成 `#`——它们随重排/收敛而变，
    不属于「问题身份」。

    v6.99（2026-09-23 二次审核 P0-A）：**所有 blocking 都走这一个身份函数**。
    原实现只给 9 个 `integrity_codes` 建身份，于是「统一验收」名不副实：
    `kf_time_out_of_shot` / `kf_layout_count_mismatch` / `kf_event_window_mismatch`
    等不在那 9 个码里的 blocking，**新稿引入它们照样被采纳**（确定性反例见审核
    文档第八节 P0-A：候选只多一个 `t_sec=9.0` 的 KF，`_accept_regen_candidate`
    返回 `(True, [])`，而其中 `kf_time_out_of_shot` 是 hard）。
    """
    msg = str(getattr(issue, "message", "") or "")
    msg = _INTEGRITY_NUM_RE.sub("#", msg)
    parts = (
        str(getattr(issue, "code", "") or ""),
        str(getattr(issue, "frame_id", "") or ""),
        str(getattr(issue, "entity_id", "") or ""),
        str(getattr(issue, "field_path", "") or ""),
        msg,
    )
    return "|".join(parts)


def _blocking_not_degraded(old_q: dict, new_q: dict) -> tuple:
    """**全部 blocking**（hard + visual_risk）相对旧稿是否未退化；返回 (是否通过, 原因).

    v6.99（二次审核 P0-A）：「统一候选验收」此前只比较手工列出的 9 个
    `integrity_codes`，其余 blocking 一概不看——候选可以在解决结构超载的同时
    引入新的 KF 越界类阻塞并被采纳（第十一次重跑「超载归零、同时新出现 3 条 KF
    越界」就是这一型）。

    判据用 **Counter 多重集**，不是集合也不是总数：
      - 用集合会漏掉「同一问题从 1 次涨到 3 次」；
      - 用总数会漏掉「修掉 A、引入 B」（数量不变）。
    要求 `Counter(新) - Counter(旧) == 空`：目标问题允许消失，**不允许出现新身份，
    也不允许既有身份次数增加**。

    身份与镜头序号无关（见 `_blocking_identity`），所以整脚本重生造成的
    镜头重排不会把每个问题都算成「新身份」。
    """
    _new_ids = list(new_q.get("blocking_ids") or [])
    _old_ids = list(old_q.get("blocking_ids") or [])
    if not _new_ids:
        return True, ""
    if not _old_ids:
        # 旧稿干净而新稿不干净 → 只要新稿有 blocking 就是退化
        return False, "新稿出现 %d 条 blocking（旧稿 0 条）：%s" % (
            len(_new_ids), _new_ids[0][:80])
    _extra = _Counter(_new_ids) - _Counter(_old_ids)
    if _extra:
        _items = list(_extra.items())
        # 报「**净增**次数」而不是原文重述：`Counter` 差集给的是增量，
        # 写成「×N」会被读成「总次数」，日志里判不出是新增还是变多。
        _desc = "; ".join("%s（净增 %d 次）" % (k[:70], v) for k, v in _items[:3])
        return False, "新 blocking 身份 %d 类: %s" % (len(_items), _desc)
    return True, ""


def _integrity_not_degraded(old_q: dict, new_q: dict) -> tuple:
    """`new_q` 的完整性相对 `old_q` 是否**未退化**；返回 (是否通过, 原因).

    v6.87（2026-09-21 23:17 实测）：原判据是 `new_q["integrity_ok"] is False` →
    拒绝。**这是错的**：`integrity_ok` 是布尔，原稿本身有完整性异常时任何新稿
    也必然为 False，于是「新稿是否更差」根本无从判断，采纳分支永远不会通过
    ——结构收敛轮 5 → 4 的改善稿被拒，等于没有收敛轮。

    用户 2026-09-21 文档里的用词本来就是「完整性**不退化**」。所以：

      - `checker_failed`（计数为 None）→ **未知不得当成干净**：新稿未知则拒绝，
        旧稿未知则退回布尔口径（只有新稿明确干净才放行）；
      - 两边都可比 → 异常条数不得增加，**且不得出现新身份**（v6.98，审核 P1-2）。
    """
    _o = old_q.get("integrity_count")
    _n = new_q.get("integrity_count")
    if _n is None:
        return False, "新稿完整性检查器未通过（无法验证）"
    if _o is None:
        # 旧稿检查器也没通过 → 没有基线可比；只有新稿**明确干净**（0 条）才放行
        return (_n == 0), "旧稿无完整性基线（仅当新稿 0 条时放行）"
    if _n > _o:
        return False, f"完整性异常 {_o} → {_n} 条"
    # v6.98（审核 P1-2）：数量不增加还不够——**新身份**也不许出现。
    # 「修掉 A、引入 B」在数量口径下是 1 → 1，身份口径下 `new_ids - old_ids` 非空。
    _new_ids = set(new_q.get("integrity_ids") or [])
    if _new_ids:
        _extra = sorted(_new_ids - set(old_q.get("integrity_ids") or []))
        if _extra:
            return False, (f"新完整性身份 {len(_extra)} 类: "
                           + "; ".join(x[:80] for x in _extra[:3]))
    return True, ""


# ---------------------------------------------------------------------------
# v6.98（2026-09-22 审核 P0-2 复核确认）：内容不退化判据的**分母必须来自旧稿**。
#
# 原实现的分母由**候选自己**提供，确定性复现出 3 种逃逸（`_verify_review.py`）：
#   A 候选删光 `core_keywords` → 候选 total=0 → 覆盖率 None → 比较被跳过；
#   B 候选删光 narration     → 候选分母 0      → 比较被跳过；
#   C 10/10 grounded → 1/1 grounded（按比例删 9 镜）→ 比率都是 100%。
# 外加两条 fail-open：检查器抛异常 → `except: pass` → 等于「没有退化」。
#
# 修法（**不**建完整 ContentManifest，理由见审核文档第六节：需要的是
# 「候选不能提供自己的分母」这条性质，不是原文 span/事件 id 那一层——
# 后者在合法合并下粒度会变，反而把合并判成退化）：
#   1. **固定清单**：从**已接受的旧稿**导出「已覆盖的 core_keywords」与
#      「台词锚（去标点后的整句）」。候选只能证明自己覆盖了这份清单。
#   2. **fail-closed**：任一步异常 / None 一律算退化（拒绝候选 = 保留旧稿，
#      不会损坏数据，只会在日志里说明为什么没采纳）。
#   3. grounding 保留**比率**口径——合法合并会减少「有台词镜数」，计数口径
#      会把合并误伤（这正是本函数上一版改成比率的原因）；「删台词」由第 1 条
#      的台词清单兜住，两条合起来才既不放水也不误伤。
# ---------------------------------------------------------------------------
_INVENTORY_DROP = set(
    "，。！？；：、—,.!?;:…“”‘’（）()《》〈〉【】[] "
    + chr(34) + chr(39) + chr(9) + chr(10) + chr(13)
)


def _strip_punct(text) -> str:
    """去掉标点与空白——台词锚的比较口径（合并后子串仍在，不误判丢失）."""
    return "".join(ch for ch in str(text or "") if ch not in _INVENTORY_DROP)


def _covered_keywords(script: dict, novel_text: str) -> set:
    """脚本里**已覆盖**的 `core_keywords` 集合（命中源 = 画面描述全字段）."""
    from step01_script_gen.gap_analysis import (
        calculate_keyword_coverage, normalize_core_keywords,
    )

    covered: set = set()
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        c = calculate_keyword_coverage(s, novel_text)
        missing = {str(x) for x in (c.get("missing") or [])}
        for kw in normalize_core_keywords(s.get("core_keywords", [])):
            if kw not in missing:
                covered.add(kw)
    return covered


def _narration_anchors(script: dict) -> list:
    """台词锚清单：每个有台词的镜头贡献一句（去标点后的整句）."""
    out: list = []
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        a = _strip_punct(s.get("narration_segment"))
        if a:
            out.append(a)
    return out


def _grounding_rate(script: dict, novel_text: str):
    """有台词镜里能在原文定位的比例；无有台词镜 → None（**不等于**通过）."""
    from step01_script_gen.validation import _check_grounding

    total = ok = 0
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        if not str(s.get("narration_segment") or "").strip():
            continue
        total += 1
        if _check_grounding(s, novel_text):
            ok += 1
    return (ok / total) if total else None


def _content_inventory(script: dict, novel_text: str) -> dict:
    """**固定内容清单**（从旧稿导出，候选无权提供分母）.

    这是「不退化」判据的基线，也是审核 P0-2 的根因修法：候选只能证明自己
    覆盖了这份清单，不能自己决定比什么。
    """
    return {
        "keywords": _covered_keywords(script, novel_text),
        "narration": _narration_anchors(script),
        "grounding": _grounding_rate(script, novel_text),
    }


def _content_degradation(before: dict, after: dict, novel_text: str) -> list:
    """`after` 相对 `before` 的**内容退化**清单（空列表 = 不退化）.

    v6.86（用户决定 B2）：时长收敛采纳新稿的前提除「严格改善」与内容完整性外，
    还要求内容不退化。缩短时长最省力的做法就是**删内容**——四条判据正是防它：
    结构非法镜头数、台词清单、已覆盖关键词清单、grounding 率。

    v6.98（2026-09-22 审核 P0-2）：四条判据的分母/清单**全部取自 `before`**，
    且任何检查器异常一律按**退化**处理（fail-closed）。
    """
    reasons: list = []
    # 1) 结构非法镜头数不增加（合并会减少镜头数，但不会让单镜重新超载）
    try:
        _b = len(_structural_shot_issues(before))
        _a = len(_structural_shot_issues(after))
        if _a > _b:
            reasons.append(f"结构非法镜头 {_b} → {_a}")
    except Exception as e:
        reasons.append(f"结构非法镜头检查器异常，按退化处理: {e}")
    # 2) 台词清单：旧稿说过的每一句都必须仍在候选里（合并 = 子串仍在）
    try:
        _blob = "".join(_narration_anchors(after))
        _lost = [a for a in _narration_anchors(before) if a not in _blob]
        if _lost:
            reasons.append(f"台词丢失 {len(_lost)} 句: {[x[:14] for x in _lost[:3]]}")
    except Exception as e:
        reasons.append(f"台词清单检查器异常，按退化处理: {e}")
    # 3) 关键词清单：旧稿**已覆盖**的关键词不得失去覆盖
    try:
        _lost_kw = sorted(_covered_keywords(before, novel_text)
                          - _covered_keywords(after, novel_text))
        if _lost_kw:
            reasons.append(f"已覆盖关键词丢失 {len(_lost_kw)} 个: {_lost_kw[:6]}")
    except Exception as e:
        reasons.append(f"关键词清单检查器异常，按退化处理: {e}")
    # 4) grounding 率：只在**有台词的镜头**里算（无台词镜 `_check_grounding` 恒 True，
    #    放进分母只会稀释比率）。旧稿能算而候选**算不出**（已无有台词镜）= 退化，
    #    不再像旧实现那样「None 就跳过」。
    try:
        _b = _grounding_rate(before, novel_text)
        _a = _grounding_rate(after, novel_text)
        if _b is not None:
            if _a is None:
                reasons.append("grounding 无法验证（候选中已无有台词镜，而旧稿有）")
            elif _a < _b - 1e-9:
                reasons.append(f"grounding 率 {_b:.0%} → {_a:.0%}（有台词镜）")
    except Exception as e:
        reasons.append(f"grounding 检查器异常，按退化处理: {e}")
    return reasons


def _accept_regen_candidate(before: dict, candidate: dict, novel_text: str) -> tuple:
    """**统一候选验收入口**：任何「重生成稿 → 是否采纳」都必须从这一个出口过.

    v6.98（2026-09-22 审核 P0-1）：结构收敛轮的采纳逻辑被**复制成两条分支**——
    「新稿结构非法数 = 0」走一条直接 `return new_script` 的快速返回，完整性比较
    只在 `0 < len(new_issues) < len(issues)` 那条分支里。于是**修得最好的那次
    结构重生恰好拥有最弱的验收**：删掉关键事件、改坏角色状态、引入新的阻塞身份，
    只要「结构干净」就被采纳。

    根因修法（审核建议）：**不为「完全修好」保留快速返回**——目标指标为 0 只是
    目标指标的最佳值，不代表其他维度安全。所有 `N → M`（含 `M=0`）都只能从这里出去。

    判据（全部 fail-closed）：
      0. **全部 blocking 不退化**（无新身份、既有身份次数不增，见
         `_blocking_not_degraded`——v6.99 起是这道门的**主体**；只比 integrity
         子集会让「新引入 KF 类阻塞」的候选照样通过）；
      1. 完整性**不退化**（计数不增 + 无新身份，见 `_integrity_not_degraded`）；
      2. 内容**不退化**（结构非法数 / 台词清单 / 已覆盖关键词清单 / grounding 率，
         见 `_content_degradation`——清单取自 `before`，候选不能提供自己的分母）。

    Returns:
        `(是否采纳, 拒绝原因列表)`；原因列表为空即采纳。
    """
    try:
        old_q = _candidate_quality_summary(before, novel_text)
    except Exception as e:
        return False, [f"原稿完整性摘要检查器异常，按拒绝处理: {e}"]
    try:
        new_q = _candidate_quality_summary(candidate, novel_text)
    except Exception as e:
        return False, [f"候选完整性摘要检查器异常，按拒绝处理: {e}"]
    reasons: list = []
    # v6.99（二次审核 P0-A）：**第一道门就是「全部 blocking 不退化」**。
    # 它比下面的 integrity 子集更宽（9 个码 → 全部 hard/visual_risk），
    # 因为「统一验收」必须覆盖候选能引入的**所有**阻塞。
    _ok, _why = _blocking_not_degraded(old_q, new_q)
    if not _ok:
        reasons.append("blocking 退化：" + str(_why))
    _ok_i, _why_i = _integrity_not_degraded(old_q, new_q)
    if not _ok_i:
        reasons.append("完整性退化：" + str(_why_i))
    reasons.extend(_content_degradation(before, candidate, novel_text))
    return (not reasons), reasons


def _regen_over_limit(script: dict, novel_text: str, bank=None, objects_bank=None,
                      max_attempts: int | None = None,
                      model: str | None = None) -> dict:
    """镜头数/正片时长超限时，反馈给 LLM 合并精简，重新生成一次（软收敛）.

    与 gap_analysis 相互独立：这里只看「镜头数/总时长」超限，不校验内容分布。
    仍超限或 JSON 解析失败时保留原版，不中断流水线（下游有 TTS rate / merge
    truncation / stretch cap 兜底，v4.1 哲学是镜头数交 LLM，此处只收敛废输出）。

    v6.86（**用户决定 B1 + B2**，2026-09-21）：

      - **B1 判定口径 = 实际 `Σduration_sec`**（`_actual_shots_total`）。原实现量的是
        `project_post_split_metrics()` 的**拆镜后投影**，而质量门量的是实际值——两者
        不是同一个量，且拆镜 pre-pass 生产已停用，投影是「不会发生的剧本」。
      - **B2 采纳判据 = 严格改善 + 完整性不退化 + 内容不退化**。原实现
        `script = new_script` 解析成功即无条件替换、零判据，是时长维度唯一的环节
        且不设防；实测把 103.0s 改成 111.38s 也照单全收。「内容不退化」由
        `_content_degradation()` 给出：结构非法镜头数不增加、原文覆盖不退化、
        grounding 不退化——**缩短时长不得用「删内容」换**。
      - 修复预算**暂时保持 1 次**（用户决定：先看提示词容量自检的效果再定是否提高）。

    Returns:
        重新生成且**通过 B2 判据**的 script；否则返回原 script。

    v6.43（08-23 用户规则）镜头数不限：SHOT_COUNT_MAX<=0 时整个收敛+截断逻辑
    全部跳过（不再 regen、不再 force-truncate），镜头数完全由 LLM 决定。
    """
    from shared.config import SHOT_COUNT_MAX

    # 重试次数单源 shared.config.GAP_MAX_REGEN_ATTEMPTS（签名默认 None=不覆盖）
    if max_attempts is None:
        max_attempts = GAP_MAX_REGEN_ATTEMPTS

    shots = script.get("shots", [])
    # v6.86（**用户决定 B1**，2026-09-21）：判定口径改用**实际** `Σduration_sec`。
    #
    # 原实现量的是 `project_post_split_metrics()` 的**拆镜后投影**（实测触发时
    # 103.0s），而质量门 `duration_budget` 量的是实际值（93.3s）。投影会把超 cap 的
    # 镜头按 cap 折算并**计入拆镜次数**，但拆镜 pre-pass 在生产路径**已停用**——
    # 投影描述的是一个**不会发生的剧本**。触发阈值与验收阈值因此不是同一个量：
    # 投影 < 90 而实际 > 90 时**该收敛的不触发**；投影 ≫ 实际时反复重生成，且要求
    # 模型压的是投影而不是门禁量的那个数。投影口径保留给候选打分。
    shots_total = _actual_shots_total(script)
    duration_over = shots_total > 90.0 + 0.01
    over_max = SHOT_COUNT_MAX > 0 and len(shots) > SHOT_COUNT_MAX

    # 镜头数不限时仍需保留时长硬约束：一次显式压缩重生成优于让 100s
    # 脚本静默通过，晚到生图/合成阶段才返工。
    if SHOT_COUNT_MAX <= 0 and not duration_over:
        return script

    for attempt in range(1, 1 + max_attempts):
        if not over_max and not duration_over:
            break

        current = len(script.get("shots", []))
        if over_max:
            _logger.warn(
                f"[COUNT] 镜头数 {current} 超上限 {SHOT_COUNT_MAX}，"
                f"反馈 LLM 合并冗余镜头（尝试 {attempt}/{max_attempts}）..."
            )
            _console(f"  [收敛] 镜头数 {current} 超上限 {SHOT_COUNT_MAX}，重新生成精简版（{attempt}/{max_attempts}）...")
        if duration_over:
            _logger.warn(
                f"[DURATION] 正片 {shots_total:.1f}s > 90s，"
                f"反馈 LLM 精简/合并到 70-80s（尝试 {attempt}/{max_attempts}）..."
            )
            _console(f"  [时长] 正片 {shots_total:.1f}s 超 90s，重新规划为 70-80s（{attempt}/{max_attempts}）...")

        feedback = ""
        if over_max:
            from shared.config import SHOT_COUNT_MAX as _max
            feedback += (
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
                f"v6.38 红果硬约束：正片总时长 60-90s（钩子另算）、单镜 3.5-{_shot_cap():.0f}s（不得超过 {_shot_cap():.0f}s）。\n"
            )
        if duration_over:
            feedback += (
                f"\n\n# ★ 正片时长硬约束（本轮拒收，最高优先级）\n"
                f"当前正片分镜合计 {shots_total:.1f}s，超过 90s 上限。\n"
                f"**本轮必须把正片分镜 duration_sec 之和压到 70-80s，最多 90s**。\n"
                f"禁止删掉关键剧情（开篇冲突/刺杀/高潮/收尾）、致命伤、武器归属或台词原文依据。\n"
                f"允许的压缩方式：\n"
                f"1. 合并同地点、同主体、连续动作或重复信息镜头；\n"
                f"2. 删除纯重复反应、无信息过渡、重复空镜；\n"
                f"3. 精简 motion_desc 的冗余动作，不为凑时长保留重复动作；\n"
                f"4. 按台词实际语速和动作分段重新分配时长，禁止靠加速朗读压缩。\n"
            )
        # v6.33（08-12）：附加 checklist 聚合反馈——镜头数收敛之外，把内容密度/景别
        # 跳跃/致命伤等真实缺陷也喂给 LLM（此前只喂镜头数/覆盖率，LLM 重生成还是老问题）
        _checklist_fb = build_regen_feedback(script, novel_text)
        if _checklist_fb:
            feedback = feedback + _checklist_fb
        # v6.68: prompt/system 跟着实际模型走（接力模型可能是另一家模型）
        _rl_builder, _rl_system = resolve_model_prompt_profile(model)
        prompt = _rl_builder(novel_text, bank, objects_bank)
        prompt = prompt + feedback

        _metrics_inc("calls.regen_over_limit")
        service = create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        req = TextGenerationRequest(
            system_prompt=_rl_system,
            user_prompt=prompt,
            json_schema=SCRIPT_SCHEMA,
            model=model,  # v6.67 接力修复：None=provider 默认（零行为变化）
            timeout_sec=_repair_timeout_sec(),  # v6.70.3: 超限收敛 180s
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

        # v6.86（**用户决定 B2**，2026-09-21）：采纳判据——**严格改善 + 内容完整性**。
        #
        # 原实现是 `script = new_script`：解析成功即**无条件替换**，没有任何质量判据。
        # 也就是说一次坏重生成不会被拒——而这是时长维度**唯一**的一道环节
        # （候选选择有排序、结构轮有采纳判据，唯独这里没有）。本轮实测：触发时
        # 投影 103.0s、最终投影 111.38s，路径结束时仍超限且更差，无环节能拦住。
        #
        # 判据：
        #   1. **严格改善**：实际正片时长必须比原稿更短（容差 0.01s，对齐定点）；
        #   2. **内容完整性**：不得引入完整性异常（text_integrity / 状态回退 /
        #      姿态互斥 / 时长预算等硬正确性问题，见 `_candidate_quality_summary`
        #      的 integrity 档）。
        # 两者任一不满足 → 保留原稿并结束（不做「再多试一次看看」）。
        _new_total = _actual_shots_total(new_script)
        if _new_total >= shots_total - 0.01:
            _logger.warn(
                f"[DURATION] 重生成未改善实际时长（{shots_total:.1f}s → "
                f"{_new_total:.1f}s），保留原版"
            )
            break
        # v6.98（审核 P0-1）：走**统一候选验收入口**（完整性不退化 + 内容不退化）。
        # v6.87 的判据是「**不退化**」而不是「必须干净」——原稿不干净时布尔判据会让
        # 采纳分支永不通过，时长收敛轮等于没有。
        _ok, _why = _accept_regen_candidate(script, new_script, novel_text)
        if not _ok:
            _logger.warn(
                f"[DURATION] 重生成缩短了时长（{shots_total:.1f}s → {_new_total:.1f}s）"
                f"但未通过不退化验收，保留原版：{_why}"
            )
            break

        # 收敛有效：替换并重算时间轴
        _logger.info(
            f"[DURATION] 实际时长改善：{shots_total:.1f}s → {_new_total:.1f}s，采纳新稿"
        )
        script = new_script
        _compute_timecodes(script)
        total_duration = float(script.get("total_duration", 0) or 0)
        shots_total = _new_total
        duration_over = shots_total > 90.0 + 0.01
        if over_max:
            if len(new_shots) <= SHOT_COUNT_MAX:
                _logger.info(
                    f"[COUNT] 收敛成功: {current} → {len(new_shots)} 镜, "
                    f"总时长 {total_duration:.0f}s"
                )
            over_max = len(new_shots) > SHOT_COUNT_MAX
        if not duration_over:
            _logger.info(
                f"[DURATION] 时长收敛成功: 正片 {shots_total:.1f}s（目标 70-80s）"
            )

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


def _structural_shot_issues(script: dict) -> list:
    """结构收敛轮的目标集合：内容需求超 cap / 时长低于下限 / **开篇空镜过多**.

    v6.101（2026-09-23）：空镜问题也进这里。两个空镜约束 v6.99 起是 report-only
    （用户决定 B：代码不再删镜头），审核与用户都要求把删除诉求**交结构 regen**——
    但当时没有落地：收敛轮只读 `_shot_duration_issues`，看不见空镜问题。
    现在把 `opening_empty_shot_issues()`（advisory）纳入目标集合，
    于是「重做这几镜」会和超载/过短一样被点名、并受统一候选验收约束。
    """
    from step01_script_gen.quality_issues import (
        STRUCTURAL_SHOT_CODES, _shot_duration_issues, opening_empty_shot_issues,
    )
    out = [i for i in _shot_duration_issues(script)
           if i.code in STRUCTURAL_SHOT_CODES]
    out.extend(opening_empty_shot_issues(script))
    return out


def _structural_feedback(issues: list) -> str:
    """把结构问题写成**可执行的重做 shot-plan 指令**."""
    lines = []
    for i in issues:
        lines.append(f"  - 镜头{i.shot_id}: {i.message}")
    return (
        "\n\n# ★ 单镜承载能力硬约束（本轮拒收，最高优先级）\n"
        "上一稿有下列镜头在**结构上**不合法——这不是字段级问题，改文本没用：\n"
        + "\n".join(lines) + "\n\n"
        "**本轮必须重做 shot-plan（重新规划镜头切分），不是在已有镜头上做字段拼接**：\n"
        "1. **内容需求超单镜上限**：把该段剧情拆成**多个完整镜头**——每个子镜头自带\n"
        "   narration_segment / motion_desc / ff_desc / lf_desc / keyframes / frame_layouts\n"
        "   与自己的 duration_sec；台词按原文**连续切分**，禁止改写、禁止增删字，\n"
        f"   每个子镜的台词估时都必须 ≤ {_shot_cap():.0f}s（留余量，目标 4-7s）。\n"
        "2. **时长低于下限**：把该镜合并进相邻的同地点连续剧情，或补足动作/台词——\n"
        "   禁止靠事后加秒数凑时长。\n"
        f"3. 单镜 duration_sec 必须落在 3.5-{_shot_cap():.0f}s；正片合计 60-90s（钩子另算）。\n"
        "4. 禁止复制字段制造 ff==lf 的空壳子镜；每个子镜都要有独立的事件与画面。\n"
        "5. **开篇空镜过多**：抖音黄金 3 秒必须有主体入场——把多余的开篇空镜"
        "**合并或替换成带主体的建立镜**（人物入画、手持道具、明确动作），"
        "**不是**让你删镜头；每个替代镜仍要自带全部字段。\n"
    )


# ===========================================================================
# v6.89（发现 14 选项 2）：**单镜定向拆分**
#
# 为什么需要它：单镜内容需求 > cap（实测 9.8 / 12.5 / 14.7 / 20.2s）是唯一一条
# 没有任何路径能消掉的 blocking。prompt 侧的「单条台词也可以切开」显式许可
# **已被证伪**（2026-09-22 07:11：三个候选行为逐字不变），所以改为对**单个**
# 被点名的超载镜头做一次定向拆分请求。
#
# 与当年被禁止的「复制字段拆镜」的区别：那时是**代码复制**父镜字段造子镜，会产出
# ff==lf 的空壳；这里是**模型重新生成**每个子镜的完整对象。`SPLIT_SHOT_SCHEMA`
# 直接复用 `_SHOT`（全部字段必填），子镜在 schema 层就不可能是空壳。
# ===========================================================================
_STRUCT_SPLIT_MAX_SHOTS = 6      # 每次运行最多为几个超载镜头发拆分请求
# v6.89 实测（09-22 07:11 脚本）：一次运行典型有 **5 个**超载镜头（8/10/11/13/14）。
# 上限若只有 3，剩下 2 个必然残留为 hard，且「拆分是否有效」也无从判断——
# 拆一次是 ~2-4k token 的小调用，6 次仍远低于整脚本 regen 的 26k。

_NARRATION_STRIP_PUNCT = "，。！？；：、—,!?:;“”'（）()"


def _narration_body(narration: str) -> str:
    """旁白/台词的**可比对正文**：剥「说话人道：」前缀 + 省略号折叠 + 去标点.

    与 `validation._check_grounding` 的归一化同口径（那边逐字比对**原文**，这里
    校验「切开后拼回来是否逐字相等」）。一致性由回归锁定，见
    `tests/step01_script_gen/test_scoped_shot_split.py`。
    """
    text = str(narration or "")
    if not text:
        return ""
    body = re.split(r"[:：]", text, maxsplit=1)[-1]
    body = body.replace(" ", "")
    body = re.sub(r"\.{2,}|…{1,}", "§", body)
    for ch in _NARRATION_STRIP_PUNCT:
        body = body.replace(ch, "")
    return body.replace("§", "…")


def _narration_continuous(parent: str, subs: list) -> tuple:
    """子镜台词拼回来与父镜**逐字相等**？返回 (是否通过, 原因)."""
    target = _narration_body(parent)
    if not target:
        return True, ""
    joined = "".join(_narration_body(s.get("narration_segment")) for s in subs)
    if joined == target:
        return True, ""
    if len(joined) != len(target):
        return False, "台词非逐字连续（原文 %d 字 → 拼回 %d 字）" % (len(target), len(joined))
    return False, "台词顺序或文字与原文不一致"


def _shot_cap() -> float:
    """单镜硬上限 = `min(视频模型能力, 项目上限)`——委托给唯一源（v6.97）."""
    from step01_script_gen.timecode import active_shot_cap

    return active_shot_cap()


def _content_need(shot: dict) -> float:
    """单镜内容最低需求（与质量门 `shot_content_over_cap` 同公式）.

    只用于**给子镜分配时长**；「这个拆分能不能接受」的最终判据走质量门本体
    （`_split_within_cap`），两者一致性由回归锁定。
    """
    from step01_script_gen.timecode import (
        MIN_SHOT_DURATION, NARRATION_FIXED_DURATION, _estimate_duration,
        _extract_motion_duration,
    )
    from shared.dialogue import has_dialogue

    narration = str(shot.get("narration_segment") or "")
    motion_min = _extract_motion_duration(str(shot.get("motion_desc") or "")) or 0.0
    speech = 0.0
    if narration:
        speech = _estimate_duration(narration) if has_dialogue(narration) \
            else NARRATION_FIXED_DURATION
    return max(motion_min, speech, MIN_SHOT_DURATION)


def _split_within_cap(sub_shots: list, cap: float | None = None) -> tuple:
    """子镜是否全部满足容量与下限——**直接问质量门本体**，不复制判据.

    探针末尾追加一个占位镜，使每个子镜都按「非末镜」口径受 >=3.5s 检查（否则末位
    子镜会绕过下限）。只认结构类 code（`shot_content_over_cap` /
    `shot_duration_below_min`），其余门禁不在此处裁决。
    """
    from step01_script_gen.quality_issues import (
        STRUCTURAL_SHOT_CODES, _shot_duration_issues,
    )

    if cap is None:
        cap = _shot_cap()
    # v6.95（②）：KF 时刻也必须落在单镜上限内——否则时长（= max(内容需求, KF)）
    # 无法在不超 cap 的前提下覆盖它。先于时长赋值检查，直接给出可执行的反馈。
    from step01_script_gen.timecode import max_kf_t_sec as _max_kf

    for s in sub_shots:
        _km = _max_kf(s)
        if _km > 0 and _km > cap + 0.01:
            return False, (
                "子镜(index=%s) 的 KF 时刻 %.2fs 超出单镜上限 %.1fs——"
                "请把关键帧写在镜长之内（或减少帧数）" % (s.get("index"), _km, cap)
            )

    idxs = {s.get("index") for s in sub_shots}
    probe = {"shots": list(sub_shots) + [
        {"index": "__probe__", "duration": 5.0, "duration_sec": 5.0,
         "narration_segment": "", "keyframes": [],
         "frame_layouts": {"ff": [], "lf": [], "kfs": []}},
    ]}
    for i in _shot_duration_issues(probe):
        if i.shot_id not in idxs or i.code not in STRUCTURAL_SHOT_CODES:
            continue
        return False, str(i.message)[:100]
    return True, ""


def _split_has_duplicate_pair(sub_shots: list) -> tuple:
    """子镜之间是否**相邻视觉重复**？返回 (是否通过, 原因).

    v6.89 续（2026-09-22 07:40 实测）：拆分**第一次真实生效**就在最终门带出 1 条
    `adjacent_duplicate_visuals`——「镜头11/12 相邻且视觉重复（相似度 89%）」。
    两条子镜来自同一父镜，模型把同一个动作写了两遍；拆分 prompt 里已经写了
    「禁止把同一个动作在两个子镜里各演一遍」，模型不遵守（与发现 14 同一类：
    **prompt 说了、模型不做**）。所以做成**确定性采纳判据**。

    判据复用全仓唯一判定 `find_adjacent_duplicate_pairs`（阈值与条件都不复制）。
    注意它**会跳过拆镜子镜**（半整数 index / `_split_from`），而子镜此刻正是
    半整数——所以先用临时整数 index 复制一份再判定。
    """
    from step01_script_gen.constraints.duplicate_shot_merge import (
        find_adjacent_duplicate_pairs,
    )

    probe = []
    for k, s in enumerate(sub_shots):
        c = dict(s)
        c["index"] = k + 1          # 临时整数，让判定器不再当它是拆镜子镜
        c.pop("_split_from", None)
        c.pop("_split_to", None)
        probe.append(c)
    for i, j, sim in find_adjacent_duplicate_pairs(probe):
        return False, (
            "子镜%d/%d 相邻视觉重复（相似度 %.0f%%）——同一个动作被拆成两镜各演一遍"
            % (i + 1, j + 1, sim * 100)
        )
    return True, ""


_CUT_DELIMS = "，。！？…；、)）】」』"


def _narration_parts(narr: str) -> tuple:
    """把 `narration_segment` 拆成 (说话人前缀, 台词正文)（正文保留原标点）."""
    text = str(narr or "")
    m = re.split(r"[:：]", text, maxsplit=1)
    if len(m) == 2:
        return m[0] + "：", m[1]
    return "", text


def _cut_bounds(body: str) -> list:
    """可切位置 = 标点**之后**（切成半句话不可读，也让原文 5 字块校验更易失配）."""
    return [i + 1 for i, ch in enumerate(body)
            if ch in _CUT_DELIMS and 0 < i + 1 < len(body)]


def _segments_at(body: str, cuts: list) -> list:
    """按切点把正文切成 `len(cuts)+1` 段（保留原标点）."""
    out, prev = [], 0
    for c in cuts:
        out.append(body[prev:c])
        prev = c
    out.append(body[prev:])
    return out


def _deterministic_narration_cuts(narr: str, cap: float, max_parts: int = 4):
    """按标点边界把一条台词确定性地切成 N 段，使**每段估时 ≤ cap**.

    返回分段后的 `narration_segment` 字符串列表（各带原说话人前缀）；
    **需求段数 > `max_parts`** → None（交整脚本 regen）。

    无标点可切时退化为**等字长切**（刻意保留的兜底）：切在词中间读起来略生硬，
    但拒绝切分 = 该镜保持超载 = 一条必然的 hard 阻塞。中文长句极少完全无标点。

    v6.92：切点不交给模型。见模块注释：切在哪是 `(台词文本, 上限)` 的纯函数，
    交给模型只会得到「不均衡 / 只回一段 / 补字」三种失败（实测采纳率 1/4-2/4）。
    """
    import itertools
    import math

    from step01_script_gen.timecode import _estimate_duration

    prefix, body = _narration_parts(narr)
    if not body:
        return None
    need = _estimate_duration(str(narr or ""))
    min_parts = max(2, int(math.ceil(need / cap - 1e-9)))
    bounds = _cut_bounds(body)

    def _ok(cuts) -> list:
        segs = _segments_at(body, list(cuts))
        texts = [prefix + s for s in segs if s]
        if len(texts) < 2:
            return []
        ests = [_estimate_duration(t) for t in texts]
        if all(e <= cap + 0.01 for e in ests):
            return ests
        return []

    for n in range(min_parts, max_parts + 1):
        if n - 1 == 0:
            continue
        if len(bounds) < n - 1:
            break                      # 标点不够切这么多段
        # v6.92 修：**不能只试「均衡」切点**。
        #
        # 实测（2026-09-22 21:08，20.2s 那条台词）：可用标点位置不均匀，均衡解恰好
        # 把中间一段撑到 8.7s（切点 [27,57] → 27/30/27 字），于是判定「无法切分」
        # 而交回整脚本 regen——**非均衡解本来是合格的**。
        # 改为在标点位置里**穷举**，以「先全部 ≤ 上限、再最小化最长段」为序选解。
        cand = bounds
        if len(cand) > 16:             # 标点很多时按理想位置取窗口，避免组合爆炸
            ideal = [len(body) * k / n for k in range(1, n)]
            near = set()
            for t in ideal:
                near.update(sorted(cand, key=lambda b: abs(b - t))[:5])
            cand = sorted(near)
        best = None                    # (最长段估时, 切点)
        for cuts in itertools.combinations(cand, n - 1):
            ests = _ok(cuts)
            if not ests:
                continue
            m_ = max(ests)
            if best is None or m_ < best[0]:
                best = (m_, list(cuts))
        if best is not None:
            return [prefix + s for s in _segments_at(body, best[1])]
    # 标点解穷举不出合格切分 → **等字长兜底**（刻意保留）。
    # 切在词中间读起来略生硬，但拒绝切分 = 该镜保持超载 = 一条必然的 hard 阻塞；
    # 中文长句极少完全无标点，所以这条兜底实际很少触发。
    for n in range(min_parts, max_parts + 1):
        cuts = [int(round(len(body) * k / n)) for k in range(1, n)]
        cuts = sorted(set(c for c in cuts if 0 < c < len(body)))
        if len(cuts) != n - 1:
            continue
        ests = _ok(cuts)
        if ests:
            return [prefix + s for s in _segments_at(body, cuts)]
    return None


def _split_source_window(shot: dict, novel_text: str, span: int = 400) -> str:
    """该镜台词在原文中的位置 ±span 字（供模型挑切点参照）；定位不到返回空串."""
    body = _narration_body(shot.get("narration_segment"))
    novel = str(novel_text or "")
    if not body or not novel:
        return ""
    pos = novel.find(body[:8])
    if pos < 0:
        return ""
    return novel[max(0, pos - span):pos + len(body) + span]


def _build_split_prompt(shot: dict, novel_text: str, cap: float,
                        segments: list, feedback: str = "") -> str:
    """单镜定向拆分的用户 prompt（分段由 `_deterministic_narration_cuts` 给定）."""
    import json as _json

    need = _content_need(shot)
    idx = shot.get("index")
    n_parts = len(segments)
    clean = {k: v for k, v in shot.items() if not str(k).startswith("_")}
    window = _split_source_window(shot, novel_text)
    parts = [
        "# ★ 单镜定向拆分（结构必需，不是改写台词）\n\n",
        "下面这**一个**镜头内容装不下：按中文字速 ≈4 字/秒估，它的台词需要 "
        "**%.1fs**，而单镜硬上限是 **%.1fs**。整条留在一镜 = 整稿拒收，"
        "所以必须把它拆成 **2-3 个完整子镜**。\n\n" % (need, cap),
        "## 硬要求\n",
        "1. **台词分段已经替你切好了，逐字照用，不许改动**。见下方"
        "「必须使用的台词分段」——第 k 个子镜的 `narration_segment` 必须**恰好**是"
        "第 k 段，一个字都不能增删改。\n"
        "   **这不是改写台词**——「台词原句」约束的是文字不得改写/增删，"
        "**不是**「一句引号只能给一个镜头」；切开后的每一段仍然是原句。\n",
        "2. **每个子镜必须是完整镜头对象**：与首次生成同构的全部字段都要写"
        "（prompt / ff_desc / lf_desc / motion_desc / keyframes / frame_layouts / "
        "duration / core_keywords / narration_segment / subtitle …），"
        "禁止只给变化字段、禁止留空壳、禁止 ff_desc 与 lf_desc 相同。\n",
        "3. **子镜个数必须是 %d 个**（不多不少），每个子镜对应下方的一段台词；"
        "每段的估时已由切分保证 ≤ %.1fs。\n" % (n_parts, cap),
        "4. **保持前后连贯**：scene / location / 人物 / 服装 / 道具 / 伤处锚点 / 朝向与"
        "站位事实全部不变；第 1 个子镜接着**上一个镜头**的尾帧状态，最后一个子镜结束在"
        "原本这一镜的结束状态。\n",
        # v6.94（2026-09-22 21:26 实测）：拆分的**唯一**剩余失败原因是「相邻子镜
        # 视觉重复」（两次都是相似度 100%）——模型给同一句台词的两半写了完全相同的
        # 画面。所以这里必须把「怎么才算不同」讲具体。
        "5. **每个子镜必须换机位或景别**（这是硬要求，不是风格建议）：台词被拆成多镜后"
        "**说话人仍在说话**，所以他必须**保持在画面内**（不能切到听者反应镜——"
        "视频模型会没有嘴型对象）；但相邻子镜必须换一个不同的机位/景别，例如"
        "「子镜1 近景正面 → 子镜2 中景侧后 / 过肩」「子镜1 平视 → 子镜2 俯拍」。\n"
        "   相邻子镜的 `motion_desc` / `prompt` / `lf_desc` 会被算**动作文本相似度**，"
        "**相似 ≥85% 即判相邻视觉重复并拒绝整条拆分**——照抄上一子镜的写法必然被拒。\n",
        "\n## 必须使用的台词分段（**逐字照用，不得改动**）\n",
        "\n".join("  子镜%d 的 narration_segment = `%s`" % (k + 1, s)
                   for k, s in enumerate(segments)),
        "\n\n## 被点名的镜头（index=%s，需要 %.1fs）\n```json\n" % (idx, need),
        _json.dumps(clean, ensure_ascii=False, indent=1),
        "\n```\n",
    ]
    if window:
        parts.append("\n## 原文窗口（挑切点参照，切点必须是原文里真实存在的停顿处）\n")
        parts.append(window)
        parts.append("\n")
    if feedback:
        # v6.94（③）：上一次尝试被拒的**具体原因**回喂——第九次实测 prompt 里的
        # 「必须换机位/景别」被模型忽略，但把「你的子镜1/2 相似度 98%」摆出来
        # 是更具体的信号（与「反馈格式教了模型一个错误写法」是同一类经验：
        # 泛泛的要求不如**具体到某两个子镜**的失败事实）。
        parts.append(
            "\n## ⚠️ 上一次尝试被拒，必须修正\n"
            "上一次你给出的子镜被判为**相邻视觉重复**：%s\n"
            "本次必须让相邻子镜的 **motion_desc / prompt / lf_desc 互相不同**：\n"
            "  · 换**机位或景别**（例：子镜1 近景正面 → 子镜2 中景侧后 / 过肩；"
            "或平视 → 俯拍）；\n"
            "  · 说话人**仍要留在画面内**（台词在继续，不能切到听者反应镜）；\n"
            "  · 相邻子镜的动作文本相似度必须 **< 85%%**。\n" % feedback
        )
    parts.append('\n只输出 JSON：{"sub_shots": [ …**恰好 %d 个**完整镜头对象… ]}\n' % n_parts)
    return "".join(parts)


_SPLIT_MAX_ATTEMPTS = 2      # 首次 + 一次带反馈重试（仅针对「视觉重复」这类写歪）


def _scoped_split_one(shot: dict, novel_text: str, *, cap: float, fps: float,
                      model=None):
    """为一个超载镜头请求定向拆分；返回子镜列表，或 None（失败/被拒）.

    被拒的每一种原因都打日志——只有「为什么拒」可见，这条路径才可运维。

    v6.94（③）：**视觉重复**被拒时带反馈**重试一次**。prompt 里已写明「相邻子镜必须
    换机位/景别、说话人保持在画面内」，模型仍照抄（第九次实测相似度 98%）——与发现 14
    同型。只对这一类重试；结构类失败（段数不符 / 台词不逐字）是没照**给定输入**做，
    重试同样不划算。
    """
    import json as _json

    from step01_script_gen.constraints.short_shot_merge import MIN_SHOT_DURATION_SEC
    from step01_script_gen.timecode import _align_frame_duration
    from _text_gen.deepseek_schema import SPLIT_SHOT_SCHEMA

    idx = shot.get("index")
    # v6.92：**切点由代码确定性计算**，模型只撰写视觉字段。
    segments = _deterministic_narration_cuts(shot.get("narration_segment"), cap)
    if not segments:
        _logger.warn(
            "[SPLIT] 镜头%s 无法确定性切分（标点不足或需求段数超过 4）——交整脚本 regen"
            % idx
        )
        return None

    try:
        base = float(shot.get("index"))
    except (TypeError, ValueError):
        base = None

    _sp_builder, _sp_system = resolve_model_prompt_profile(model)
    service = create_service()
    feedback = ""

    for attempt in range(1, _SPLIT_MAX_ATTEMPTS + 1):
        _metrics_inc("calls.split_shot")
        prompt = _build_split_prompt(shot, novel_text, cap, segments,
                                     feedback=feedback)
        req = TextGenerationRequest(
            system_prompt=_sp_system,
            user_prompt=prompt,
            json_schema=SPLIT_SHOT_SCHEMA,
            model=model,
            timeout_sec=_repair_timeout_sec(),
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _logger.warn("[SPLIT] 镜头%s 拆分请求失败: %s" % (idx, e))
            return None
        if not result.success:
            _logger.warn("[SPLIT] 镜头%s 拆分请求失败: %s" % (idx, result.error))
            return None

        raw = result.text.strip()
        raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        try:
            data = _json.loads(raw)
        except Exception as e:
            _logger.warn("[SPLIT] 镜头%s 拆分 JSON 解析失败: %s" % (idx, e))
            return None
        subs = data.get("sub_shots") if isinstance(data, dict) else None
        if not isinstance(subs, list) or not all(isinstance(s, dict) for s in subs):
            _logger.warn("[SPLIT] 镜头%s 未返回 sub_shots 数组" % idx)
            return None
        if len(subs) != len(segments):
            _logger.warn(
                "[SPLIT] 镜头%s 子镜数 %d 与指定段数 %d 不一致"
                % (idx, len(subs), len(segments))
            )
            return None

        ok, why = _narration_continuous(shot.get("narration_segment"), subs)
        if not ok:
            _logger.warn("[SPLIT] 镜头%s 拆分被拒：%s" % (idx, why))
            return None
        # v6.92：逐段逐字校验——切点是**我们给的**，所以这里能做精确比对：
        # 「补字 / 只回一段 / 段落错位」全部在此挡住。
        _bad_seg = None
        for k, (sub, seg) in enumerate(zip(subs, segments), 1):
            if _narration_body(sub.get("narration_segment")) != _narration_body(seg):
                _bad_seg = (
                    "[SPLIT] 镜头%s 拆分被拒：子镜%d 的台词与指定分段不一致"
                    "（指定 %d 字 → 实得 %d 字）"
                    % (idx, k, len(_narration_body(seg)),
                       len(_narration_body(sub.get("narration_segment"))))
                )
                break
        if _bad_seg:
            _logger.warn(_bad_seg)
            return None

        # 子镜时长由**内容需求**派生（不照抄模型给的 duration）：容器必须装得下内容。
        # 对齐到整数帧，下限 3.5s（非末镜口径）。
        from step01_script_gen.timecode import max_kf_t_sec as _max_kf
        _fps = float(fps or 24)
        out = []
        for k, sub in enumerate(subs):
            if base is not None:
                sub["index"] = round(base + (k + 1) / (len(subs) + 1), 3)
            # v6.95（②）：时长由**内容需求**派生，但**必须同时覆盖该子镜自己的最大
            # KF 时刻**——帧写在哪个时刻，镜头就得长到能拍到它（同发现 18 的钩子）。
            # 只按内容需求派生的后果：模型写的 KF 落在镜长之外 → `kf_time_out_of_shot`。
            _kf_max = _max_kf(sub)
            dur = min(max(_content_need(sub), MIN_SHOT_DURATION_SEC, _kf_max), cap)
            dur = max(_align_frame_duration(dur, _fps), MIN_SHOT_DURATION_SEC)
            if _kf_max and dur < _kf_max:
                # 帧对齐可能取整到**略小于** KF 时刻（发现 18 同类）→ 覆盖优先
                dur = round(dur + 1.0 / _fps, 6)
            sub["duration"] = dur
            sub["duration_sec"] = dur
            out.append(sub)

        ok, why = _split_within_cap(out, cap)
        if not ok:
            # v6.95（②）：容量/KF 覆盖不合格也是**可修**的一类（模型写歪），带反馈重试
            if attempt < _SPLIT_MAX_ATTEMPTS:
                _logger.warn("[SPLIT] 镜头%s 子镜仍不合法，带反馈重试（%d/%d）：%s"
                             % (idx, attempt + 1, _SPLIT_MAX_ATTEMPTS, why))
                feedback = why
                continue
            _logger.warn("[SPLIT] 镜头%s 拆分被拒（重试后仍不合法）：%s" % (idx, why))
            return None

        ok, why = _split_has_duplicate_pair(out)
        if ok:
            if attempt > 1:
                _logger.info("[SPLIT] 镜头%s 带反馈重试后通过（机位/景别已区分）" % idx)
            return out
        if attempt < _SPLIT_MAX_ATTEMPTS:
            _logger.warn(
                "[SPLIT] 镜头%s 视觉重复，带反馈重试（%d/%d）：%s"
                % (idx, attempt + 1, _SPLIT_MAX_ATTEMPTS, why)
            )
            feedback = why
            continue
        _logger.warn("[SPLIT] 镜头%s 拆分被拒（重试后仍重复）：%s" % (idx, why))
        return None
    return None


def _scoped_split_over_cap_shots(script: dict, novel_text: str, *, model=None) -> dict:
    """把超载镜头逐个定向拆分（最多 `_STRUCT_SPLIT_MAX_SHOTS` 个）；原地改 `shots`."""
    over = [i for i in _structural_shot_issues(script)
            if i.code == "shot_content_over_cap"]
    if not over:
        return script
    cap = _shot_cap()
    shots = script.get("shots") or []
    fps = script.get("fps", 24)
    done = 0
    for issue in over:
        if done >= _STRUCT_SPLIT_MAX_SHOTS:
            _logger.info(
                "[SPLIT] 已达单轮拆分上限 %d，其余超载镜头交整脚本 regen"
                % _STRUCT_SPLIT_MAX_SHOTS
            )
            break
        pos = next((n for n, s in enumerate(shots)
                    if isinstance(s, dict) and str(s.get("index")) == str(issue.shot_id)),
                   None)
        if pos is None:
            continue
        parent = shots[pos]
        _logger.info(
            "[SPLIT] 镜头%s 需要 %.1fs > %.1fs，请求定向拆分…"
            % (issue.shot_id, _content_need(parent), cap)
        )
        subs = _scoped_split_one(parent, novel_text, cap=cap, fps=fps, model=model)
        if not subs:
            continue
        shots[pos:pos + 1] = subs
        done += 1
        _logger.info(
            "[SPLIT] 镜头%s 已拆为 %d 镜：%s（台词逐字连续 ✓）"
            % (issue.shot_id, len(subs),
               [round(float(s.get("duration") or 0), 2) for s in subs])
        )
        _console("  [拆分] 镜头%s → %d 镜（超载已消）" % (issue.shot_id, len(subs)))
    if done:
        try:
            write_timeline_from_durations(script)
        except Exception as e:
            _logger.warn("[SPLIT] 拆分后 timeline 序列化失败: %s" % e)
    return script


def _regen_structural_over_limit(script: dict, novel_text: str, bank=None,
                                 objects_bank=None, max_attempts: int | None = None,
                                 model: str | None = None) -> dict:
    """**结构收敛轮**：单镜超 cap / 低于下限 → 重做 shot-plan（整脚本重生成）.

    二次审核发现 1（P0）：系统取消了「先复制字段、后拆镜」这一错误做法，却没有补上
    它的替代者——流程仍是「一次生成完整镜头对象 → 发现结构不合法 → 只报告」。
    本次实测镜头 10/11/13 的台词最低需求 13.7 / 14.4 / 20.0s 都超过 8s 上限，镜头 5
    只有 3.0s，**没有任何阶段负责把它们收敛掉**，必然带着 BLOCK 落盘。

    这里补上可执行的收敛路径：把结构问题点名反馈给 LLM，要求**重做 shot-plan**
    （超限段落拆成多个完整子镜；过短镜头合并相邻剧情片段）。采纳判据必须是
    **结构非法数严格变少**——不能用 `_quality_rank` 当门槛（它把内容分与全部
    blocking 计数混进同一元组，会否决「结构确实变少、内容项略多」的稿子，
    实测把 7 → 5 判成「未改善」），唯一附加守卫是不许引入完整性异常。

    Returns:
        收敛后的 script；无结构问题、无预算、或新稿不改善时原样返回。
    """
    from shared.config import GAP_MAX_REGEN_ATTEMPTS

    if max_attempts is None:
        max_attempts = GAP_MAX_REGEN_ATTEMPTS

    # v6.89（发现 14 选项 2）：**先做单镜定向拆分**。
    #
    # 它比整脚本重做 shot-plan 便宜一个数量级（单镜 ~2-4k token vs 全脚本 26k），
    # 且只动被点名的那一镜——已经验证过的其它镜头一个字节都不碰。整脚本 regen
    # 退居兜底（「过短镜头」只能靠它合并，拆不了）。
    script = _scoped_split_over_cap_shots(script, novel_text, model=model)

    for attempt in range(1, 1 + max_attempts):
        issues = _structural_shot_issues(script)
        if not issues:
            return script
        _logger.warn(
            f"[STRUCT] {len(issues)} 个镜头结构不合法（内容超 cap / 低于下限）："
            f"{[str(i.shot_id) for i in issues]}，重做 shot-plan（尝试 {attempt}/{max_attempts}）"
        )
        _console(f"  [结构] {len(issues)} 个镜头超载/过短，重做分镜规划（{attempt}/{max_attempts}）...")

        _sb_builder, _sb_system = resolve_model_prompt_profile(model)
        prompt = _sb_builder(novel_text, bank, objects_bank)
        prompt = prompt + _structural_feedback(issues)
        try:
            _checklist_fb = build_regen_feedback(script, novel_text)
            if _checklist_fb:
                prompt += _checklist_fb
        except Exception:
            pass

        _metrics_inc("calls.regen_structural")
        service = create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        # v6.101（2026-09-23 第十四次重跑实测）：结构收敛轮是**全脚本级**请求
        # （输入整章原文 + 反馈，输出 ~26k token），却一直用**修复轮**的 180s 超时。
        # 第十四次实测直接 `Agent Plan 客户端超时 (180s)` → 连候选都没拿到，
        # 「2 个超载镜重做 shot-plan」这条路整轮作废（第七次重跑时它能产出 → 属延迟波动）。
        # 改走与 GAP 重生同档的 `_full_script_timeout_sec()`（默认 300s）。
        req = TextGenerationRequest(
            system_prompt=_sb_system,
            user_prompt=prompt,
            json_schema=SCRIPT_SCHEMA,
            model=model,
            timeout_sec=_full_script_timeout_sec(),
        )
        try:
            result = service.generate(req)
        except Exception as e:
            _logger.warn(f"[WARN] 结构收敛重生成失败: {e}，保留原版")
            return script
        if not result.success:
            _logger.warn(f"[WARN] 结构收敛重生成失败: {result.error}，保留原版")
            return script

        import re as _sb_re
        raw = result.text.strip()
        raw = _sb_re.sub(r"^```json\s*", "", raw, flags=_sb_re.MULTILINE)
        raw = _sb_re.sub(r"^```\s*", "", raw, flags=_sb_re.MULTILINE)
        raw = _sb_re.sub(r"```\s*$", "", raw, flags=_sb_re.MULTILINE)
        try:
            new_script = parse_response(raw, novel_text=novel_text)
        except (ValueError, json.JSONDecodeError) as e:
            _logger.warn(f"[WARN] 结构收敛 JSON 解析失败，保留原版: {e}")
            return script
        if not new_script.get("shots"):
            _logger.warn("[WARN] 结构收敛返回空 shots，保留原版")
            return script

        _compute_timecodes(new_script)
        new_issues = _structural_shot_issues(new_script)

        # 采纳判据：**结构非法数严格变少**由本轮的收敛目标主导。
        #
        # 原判据是「结构变少 **且** `_quality_rank` 不劣化」。实测（2026-09-21
        # 22:20:04）一份把非法镜头 **7 → 5** 的新稿被判成「未改善」丢弃：
        # `_quality_rank` 把内容分与**全部** blocking 计数混进同一个元组，新稿
        # 结构更好但内容项略多，元组就变大。结构轮存在的唯一目的就是收敛结构，
        # 判据却不由结构主导——等于没有收敛轮，本轮真实运行因此带着 7 个非法
        # 镜头落盘（`shot_content_over_cap` × 5 直接进 hard）。
        #
        # 现在：结构严格变少即采纳，唯一附加守卫是**不得引入完整性异常**
        # （text_integrity / 状态回退 / 姿态互斥 / 时长预算等硬正确性问题，
        # 见 `_candidate_quality_summary` 的 integrity 档）——那是内容崩坏，
        # 与「内容分低一点」不是一回事；内容项由随后的定向修复轮处理。
        # v6.98（审核 P0-1）：**目标指标为 0 不再走快速返回**。
        #
        # 原实现把「新稿结构非法数 = 0」当成 `return new_script` 的直接理由，于是
        # 完整性门（以及后来的内容门）**只在 `0 < M < N` 时才跑**——修得最干净的那
        # 一次重生反而拥有最弱的验收：删关键事件、改坏角色状态、引入新阻塞身份，
        # 只要「结构干净」就被采纳。目标指标的最佳值 ≠ 其他维度安全。
        #
        # 现在统一为：**先严格改善 → 再过统一验收 → 才提交**；`M = 0` 只是让它
        # 不需要再迭代（提交后直接结束），而不是绕过验收。
        if len(new_issues) >= len(issues):
            _logger.warn(
                f"[STRUCT] 新稿未改善（{len(issues)} → {len(new_issues)} 个非法镜头），保留原版"
            )
            return script
        _ok, _why = _accept_regen_candidate(script, new_script, novel_text)
        if not _ok:
            # v6.87：判据是「**不退化**」，不是「必须干净」——原稿不干净时
            # 布尔判据会让采纳分支永不通过（实测 5 → 4 被拒）。
            _logger.warn(
                f"[STRUCT] 新稿结构改善（{len(issues)} → {len(new_issues)}）"
                f"但未通过不退化验收，保留原版：{_why}"
            )
            return script
        _logger.info(
            f"[STRUCT] 结构改善：{len(issues)} → {len(new_issues)} 个非法镜头，采纳新稿"
        )
        script = new_script
        if not new_issues:
            _logger.info(
                f"[STRUCT] 结构收敛成功：{len(issues)} 个非法镜头 → 0；"
                f"共 {len(script.get('shots', []))} 镜（已通过统一验收）"
            )
            _console("  [结构] 收敛成功：无超载/过短镜头")
            return script
        continue

    remaining = _structural_shot_issues(script)
    if remaining:
        # 无预算/未收敛：**明确结束为 BLOCK**，不再「再试一次看看」
        _logger.warn(
            f"[STRUCT] 结构收敛用尽 {max_attempts} 次尝试，仍有 "
            f"{len(remaining)} 个镜头超载/过短——交质量门判 BLOCK，需人工介入"
        )
        _console(f"  [结构] 收敛失败：仍有 {len(remaining)} 个镜头超载/过短（BLOCK）")
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
    # v6.84 续（2026-09-21 实测）：`kf_event_window_mismatch` 是**最大阻塞桶**
    # （18 → 9 条，占本轮 visual_risk 的一半），却既不在本白名单、也不是
    # `STRUCTURAL_SHOT_CODES` → **没有任何收敛路径**，只能一路报告到 BLOCK。
    # 它的 issue 构造本来就带 `shot_id` / `field_path="keyframes.desc"` /
    # `repairable=True`，与同族的 `kf_time_window_alignment` 是同一个字段、
    # 同一类缺陷、同一套修法——一个接线了一个没接，属漏接。
    "kf_event_window_mismatch",
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
    # weapon_holder 不进修复轮（09-13 决策：无误报观察数据，先跑一章统计命中/误报；
    # issue 分级保留——仍经 feedback.build_regen_feedback 聚合进 GAP/regen 反馈文本。
    # 钩子的无主武器由 main.py「钩子后置校验轮」兜底，不经本清单）
    # === 打斗/致命（1） ===
    # v6.43 08-25：fatal_wound_continuity 移除——hard_fix 阶段
    # InjuryContinuityConstraint 已确定性补 anchor（prompt/lf_desc 追加
    # _INJURY_ANCHOR_TAIL = "伤处白袍心口一片暗红血迹洇开"），剩余 WARN 是
    # hard_fix 修复痕迹的镜像。如果保留进 LLM 修复轮，LLM 会把 hard_fix
    # 加的 anchor 当成"问题"误删（实测 08-25 跑批 LLM 修复 round1 又删了
    # 已补的 fatal_wound_continuity 6 处）。改为由 hard_fix 唯一负责，LLM
    # 修复轮不再过问。
    "combat_action",
    # === 空镜/字幕/合规（2） ===
    "negative_prompt", "subtitle_leak",
    # === 原文锚点 + 高潮优先（2） ===
    "grounding", "climax_priority",
)
# 08-15（v6.36）：2 轮 → 1 轮——实测修复轮越改越糟（38→55），降为单轮 top-6
# 聚焦最严重，杜绝「第2轮全量」把没问题的镜头也重写一遍。
# v6.101（2026-09-23，用户决定「提高修复预算」）：1 → 3。
#
# 第十四次重跑：22 条阻塞分布在 14 个镜头上，而一轮只动 3 镜、只解掉 1 条
# （`before=25 after=25`）。KF 类那 17 条**全在修复轮白名单里**，只是排不进
# 那 3 个名额——真正的瓶颈是预算，不是任何单个检查器。
_REPAIR_MAX_ROUNDS = 3


def _collect_repair_warnings(script: dict, novel_text: str) -> dict:
    """Return structured blocking issues for targeted repair.

    Candidate ranking, repair selection and the final gate all consume the same
    ``collect_structured_issues`` result.  Legacy callers still receive the
    historical ``{_has_hard, shots}`` shape.
    """
    if novel_text:
        _set_novel_text(novel_text)
    issues, checker_failed, rule_version = collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_run_prompt_checklist,
    )
    per_shot: dict = {}
    for issue in issues:
        if not is_blocking(issue):
            continue
        ids = []
        if issue.shot_id is not None:
            ids.append(issue.shot_id)
        ids.extend(issue.related_shot_ids or [])
        if not ids:
            per_shot.setdefault("global", []).append(issue.message)
            continue
        for sid in ids:
            if str(sid) in ("0", "hook"):
                per_shot.setdefault("hook", []).append(issue.message)
            else:
                per_shot.setdefault(f"shot_{sid}", []).append(issue.message)
    return {
        "_has_hard": any(is_blocking(i) for i in issues),
        "shots": per_shot,
        "checker_failed": checker_failed,
        "rule_version": rule_version,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "issues": [i.to_dict() for i in issues],
    }


# hook 质量门只消费高价值 warning（避免普通告警触发重生循环）。
# gaze_state_conflict 是 09-13 实证会烧穿 QC 的确定性矛盾：闭眼角色 gaze 非无。
_HOOK_QC_WARNING_CATEGORIES = {"gaze_state_conflict"}


def _collect_hook_issues(script: dict) -> list[str]:
    """Return all blocking issues attached to the hook shot."""
    issues, checker_failed, _ = collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_run_prompt_checklist,
    )
    out: list[str] = []
    if checker_failed:
        out.append("[CHECK_FAILED] 质量检查器异常")
    for issue in issues:
        if str(issue.shot_id) not in ("0", "hook"):
            continue
        if is_blocking(issue):
            out.append(f"[{issue.code}] {issue.message}")
    return out


_QUALITY_BLOCKING_CATEGORIES = {
    "gaze_state_conflict",
    "weapon_holder",
    "kf_temporal_consistency",
    "kf_time_window_alignment",
    "kf_frame_redundancy",
    "frame_layouts_required",
    "prompt_length_budget",
    "person_boat_position",
    "multi_person_staging",
    "ff_lf_redundancy",
    "cross_shot_continuity",
    "ff_desc_length",
    "text_integrity",
}


_VIDEO_ONLY_CATEGORIES = {
    "camera_movement",
    "camera_upgrade",
    "transition_keywords",
    "focal_switch",
    "prompt_length_budget",
}


def _collect_text_integrity_issues(script: dict) -> list[str]:
    """Deterministic structural integrity scan for model-visible fields."""
    issues: list[str] = []
    shots = list(script.get("shots") or [])
    targets = [(f"镜头{shot.get('index', i + 1)}", shot) for i, shot in enumerate(shots)]
    hook = script.get("hook")
    if isinstance(hook, dict):
        targets.append(("钩子镜头(index=0)", hook))
    _fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")
    _injury_words = (
        "刺中", "刺入", "中刀", "中剑", "心口", "胸口", "暗红", "洇痕",
        "血痕", "血迹", "血染", "伤口", "断气", "身亡", "尸体", "昏迷",
    )
    _open_eye_words = (
        "双目圆睁", "双眼圆睁", "双目大睁", "双眼大睁", "双目暴睁", "双眼暴睁",
    )
    for label, shot in targets:
        if not isinstance(shot, dict):
            continue
        # Context is shot-level.  A body-state phrase in ff_desc is valid when
        # an adjacent field such as lf_desc already records the wound.
        shot_text = " ".join(
            str(shot.get(f) or "") for f in (
                *_fields, "main_subject", "narration_segment", "subtitle"
            )
        )
        for field in _fields:
            text = shot.get(field)
            if not isinstance(text, str) or not text.strip():
                continue
            for err in text_integrity_errors(text):
                issues.append(f"{label}.{field}: {err}")
            if (("双目紧闭" in text or "头颈僵直" in text)
                    and any(w in text for w in _open_eye_words)):
                issues.append(f"{label}.{field}: 闭眼/睁眼状态矛盾")
            if "仰面朝上双目紧闭" in text and not any(
                w in shot_text for w in _injury_words
            ):
                issues.append(f"{label}.{field}: 无伤情上下文却写死亡头态")
    return issues


def _temporal_plan_stats(script: dict) -> dict:
    """结构化事件表的**迁移期统计**：present / valid / grounded（二次审核发现 6）.

    决定 6：`temporal_plan` 继续保持**只读**，不为了覆盖率强制启用结构写入。
    但要先积累真实样本——统计每个候选里有多少镜头带了事件表、其中多少通过严格
    校验（整镜覆盖 / 连续性 / 与 motion 一致）、多少条的 `source_quote` 能**在本镜
    文本内**找到。达到稳定阈值后再决定是否把 schema 改成 required 或让 shot-plan
    消费它。

    `grounded` 是**本镜文本内**的定位代理（候选期通常拿不到章节原文）；它比
    `validation._check_temporal_plan` 的「本镜原文窗口」检查弱一档，只用于统计
    趋势，不参与任何判定——所以不会因为「统计值低」而改变流水线行为。
    """
    from shared.motion_segments import validate_events

    stats = {"shots": 0, "present": 0, "valid": 0, "grounded": 0}
    units = [script.get("hook")] if isinstance(script.get("hook"), dict) else []
    units += [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    for shot in units:
        stats["shots"] += 1
        plan = shot.get("temporal_plan")
        if not isinstance(plan, dict) or not plan.get("events"):
            continue
        stats["present"] += 1
        try:
            dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        motion = str(shot.get("motion_desc") or "")
        if validate_events(plan.get("events"), dur, motion_desc=motion or None):
            continue
        stats["valid"] += 1
        _own_text = "".join(str(shot.get(f) or "") for f in
                            ("narration_segment", "subtitle", "motion_desc",
                             "ff_desc", "lf_desc", "prompt"))
        _quotes = [str(ev.get("source_quote") or "").strip()
                   for ev in plan["events"] if isinstance(ev, dict)]
        if _quotes and all(q and q in _own_text for q in _quotes):
            stats["grounded"] += 1
    return stats


def _candidate_quality_summary(script: dict, novel_text: str = "") -> dict:
    """Candidate risk summary from the same structured issue collector."""
    if novel_text:
        _set_novel_text(novel_text)
    issues, checker_failed, rule_version = collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_run_prompt_checklist,
    )
    blocking = [i for i in issues if is_blocking(i)]
    integrity_codes = {
        "text_integrity", "eye_state_conflict",
        "action_time_out_of_range", "state_order_replay",
        "pose_conflict", "surface_conflict",
        "offscreen_character_action", "weapon_holder_missing",
        "duration_budget",
    }
    integrity_issues = [i for i in blocking if i.code in integrity_codes]
    # v6.84（二次审核发现 1）：**结构收敛类**单独成一档——它们比一般 blocking
    # 更硬：字段级修复轮修不了，必须重做 shot-plan。候选选择据此拒收。
    from step01_script_gen.quality_issues import STRUCTURAL_SHOT_CODES as _SSC
    structural_issues = [i for i in blocking if i.code in _SSC]
    return {
        "structural_ok": not checker_failed and not structural_issues,
        "structural_issues": [i.message for i in structural_issues],
        # v6.102：结构问题的 **code**（消息文本无法可靠判类型，候选采纳要按类型分流）
        "structural_codes": [i.code for i in structural_issues],
        # v6.87：**完整性异常计数**。布尔 `integrity_ok` 不能用来判「新稿是否劣化」——
        # 原稿本身就不干净时，任何新稿也必然 False，采纳判据会变成永远不通过
        # （2026-09-21 23:17 实测：结构轮 5 → 4 被拒，两个候选摘要都标「完整性异常」）。
        # 收口判据要的是「**不退化**」，所以必须能比较数量。checker 失败时给 None
        # ——「未知」不得被当成「干净」。
        "integrity_count": None if checker_failed else len(integrity_issues),
        # v6.98（审核 P1-2）：身份集合——数量只用于日志与排序，安全门看身份差。
        "integrity_ids": ([] if checker_failed
                          else sorted(_blocking_identity(i) for i in integrity_issues)),
        # v6.99（二次审核 P0-A）：**全部 blocking** 的身份（含重复次数）。
        # 统一验收消费的是这一份，不再是上面那个手工子集——名称与行为必须同义。
        "blocking_ids": ([] if checker_failed
                         else [_blocking_identity(i) for i in blocking]),
        "temporal_plan_stats": _temporal_plan_stats(script),
        "quality_checked": not checker_failed,
        "blocking_risk_count": None if checker_failed else len(blocking),
        "integrity_ok": not checker_failed and not integrity_issues,
        "integrity_issues": [i.message for i in integrity_issues],
        "rule_version": rule_version,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "checker_failed": checker_failed,
        "structured_issues": [i.to_dict() for i in issues if is_blocking(i)],
    }


def _candidate_only_splittable(score: dict) -> bool:
    """候选的**唯一**结构问题是不是「内容超载」（= 有确定性修复路径）.

    v6.102（第十六次修复项 #2）：实测第十二～十五次每一轮都一样——同两条长台词
    （原文 50 / 59 字）让候选 1/2/3 全部结构不合法，每次白烧 3 个候选（各 ~26k token），
    而第三个候选手上已经是 100 分。`shot_content_over_cap` **有确定性修复路径**：
    后面的定向拆分（`_scoped_split_over_cap_shots`）会按时长上限把它切开、拆完照样
    过统一验收。既然一定能修掉，就没必要为了「结构干净」再抽两次卡。

    只放行这一类：`shot_duration_below_min`（过短）**没有**确定性路径
    （只能靠整脚本 regen 合并），所以仍然拒收重取。
    """
    codes = set(score.get("structural_codes") or [])
    return bool(codes) and codes <= {"shot_content_over_cap"}


def _candidate_sort_key(item: tuple[dict, dict]) -> tuple:
    """Sort clean candidates ahead of dirty high-score candidates."""
    score = item[0] if isinstance(item, tuple) and item else {}
    if score.get("quality_checked") is False:
        return (2, 10**6, -float(score.get("total", 0) or 0))
    integrity_bad = 0
    if score.get("quality_checked") and score.get("integrity_ok") is not True:
        integrity_bad = 1
    # v6.84（发现 1）：结构非法（超 cap / 低于下限）比内容分更优先——它决定
    # 这份稿子**能不能落地**，而不是好不好看。
    structural_bad = 0
    if score.get("quality_checked") and score.get("structural_ok") is not True:
        structural_bad = 1
    return (
        structural_bad,
        integrity_bad,
        int(score.get("blocking_risk_count", 0) or 0),
        -float(score.get("final_score", score.get("total", 0)) or 0),
    )


def _quality_rank(score: float, quality: dict | None) -> tuple:
    """Lower is better: integrity first, then blocking risks, then raw score."""
    quality = quality or {}
    integrity_ok = quality.get("integrity_ok")
    if integrity_ok is True:
        integrity_bad = 0
    elif integrity_ok is False:
        integrity_bad = 1
    else:
        integrity_bad = 2  # unknown must never outrank a verified clean version
    risks = quality.get("blocking_risk_count")
    risks = 10**6 if risks is None else int(risks or 0)
    structural_ok = quality.get("structural_ok")
    if structural_ok is True:
        structural_bad = 0
    elif structural_ok is False:
        structural_bad = 1
    else:
        structural_bad = 2
    if quality.get("quality_checked") is False:
        integrity_bad = max(integrity_bad, 2)
        structural_bad = max(structural_bad, 2)
        risks = max(risks, 10**6)
    return (structural_bad, integrity_bad, risks, -float(score or 0))


def _write_step01_quality_gate(script: dict, novel_text: str) -> dict:
    """Write the final structured quality-gate report."""
    if novel_text:
        _set_novel_text(novel_text)
    issues, checker_failed, rule_version = collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_run_prompt_checklist,
    )
    hard = [i.to_dict() for i in issues if i.severity == "hard"]
    visual = [i.to_dict() for i in issues if i.severity == "visual_risk"]
    video_advisory = [i.to_dict() for i in issues if i.severity == "video_advisory"]
    advisory = [i.to_dict() for i in issues if i.severity in ("advisory", "unverified")]
    payload = {
        "hard_count": len(hard),
        "visual_risk_count": len(visual),
        "advisory_count": len(advisory),
        "video_advisory_count": len(video_advisory),
        "hard": hard,
        "visual_risk": visual,
        "advisory": advisory,
        "video_advisory": video_advisory,
        "blocking": bool(hard or visual or checker_failed),
        "blocking_categories": sorted({x["code"] for x in hard + visual}),
        "checker_failed": checker_failed,
        "rule_version": rule_version,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "status": "BLOCK" if (hard or visual or checker_failed) else "PASS",
    }
    attach_gate_metadata(payload, script, stage="step01.quality_gate")
    try:
        from shared.json_io import atomic_dump_json
        # attach_gate_metadata recomputes script_revision from final content.
        # Persist that revision back to script_result.json in the same step so
        # BEST-GUARD / downstream gates can never observe report != script.
        atomic_dump_json(OUTPUT_FILE, _strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
        atomic_dump_json(CHAPTER_DIR / "step01_quality.json", payload, ensure_ascii=False, indent=2)
    except Exception as exc:
        _logger.warn(f"[QUALITY-GATE] write failed: {exc}")
    return payload


def _prepare_hook_for_qc(script: dict) -> None:
    _metrics_inc("hook.prepare")
    """Deterministically normalize a hook before the quality gate.

    Hook generation happens after the main hard-fix pass.  Without this, a hook
    with complete ``frame_layouts`` but missing legacy ``char_layout`` is sent
    for a full LLM regeneration, which is slow and random.  These constraints
    only synchronize already-known structure; they do not invent plot content.
    """
    from step01_script_gen.validation import post_fix_shot_prompts
    from step01_script_gen.constraints import ConstraintContext
    from step01_script_gen.constraints.element_markers import ElementMarkers
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint)
    from step01_script_gen.constraints.char_layout_sync import (
        CharLayoutSyncConstraint)
    from step01_script_gen.constraints.surface_backfill import (
        SurfaceBackfillConstraint)
    from step01_script_gen.constraints.gaze_state_repair import (
        GazeStateRepairConstraint)

    post_fix_shot_prompts(script)
    ctx = ConstraintContext.from_script(script, markers=ElementMarkers())
    for constraint in (
        FrameSpecConsistencyConstraint(),
        CharLayoutSyncConstraint(),
        SurfaceBackfillConstraint(),
        GazeStateRepairConstraint(),
    ):
        constraint.apply(ctx)


def _ensure_hook_quality(script: dict, *, novel_text: str, bank=None,
                         objects_bank=None, forbidden_lines=None,
                         producer_model=None) -> list[str]:
    """钩子质量门（v6.70.1，09-13；09-14 抽出供 BEST-GUARD 恢复路径共用）.

    hook 先过确定性结构补齐，再进 checklist；仍有 issue 才换互补模型重生 1 次。
    正式输出有两条来源（本次 run / BEST-GUARD 恢复的快照+补修复），门必须对
    两者同等生效——09-14 实测：恢复路径绕过本门，旧快照的病句/无主刀尖经
    非幂等补修复污染（刀尖句翻倍 + 对象库碎片）直接落盘。重生后补跑检查 7
    （FrameSpec 出镜覆盖）——重生钩子的 spec 同样可能漏人。返回残余 issues
    （空 = 过门）。
    """
    try:
        _prepare_hook_for_qc(script)
    except Exception as _prep_err:
        _logger.warn(f"[WARN] 钩子质量门前结构补齐失败（继续检查）: {_prep_err}")
    # v6.84（二次审核发现 5）：**先把钩子的时长/时间轴派生出来，再收集 issue**。
    #
    # 原顺序是「收集 issue → …」，而钩子质量门跑在 `_compute_timecodes` **之前**
    # ——`duration_sec` 还没派生，`final_time_invariant_issues()` 里 `dur<=0` 会
    # 整段跳过 KF 检查。本次实测钩子首帧 `t_sec=0.0` 就是这样漏过质量门的
    # （`hook.initial_issues=0` / `hook.final_issues=0`），直到落盘前的最终门才
    # 报出来，那时已经没有 regen 机会。现在门内先做与 `_compute_timecodes`
    # **同一份**时长推导，再用**同一套**最终不变量检查。
    try:
        from step01_script_gen.timecode import sync_hook_timeline
        sync_hook_timeline(script)
    except Exception as _sync_err:
        _logger.warn(f"[WARN] 钩子质量门时长派生失败（继续检查）: {_sync_err}")
    issues = _collect_hook_issues(script)
    _metrics_set("hook.initial_issues", len(issues))
    if not issues:
        _metrics_set("hook.final_issues", 0)
        return []
    _logger.warn(
        f"[HOOK-QC] 钩子检出 {len(issues)} 条 issue，互补模型重生："
        + " | ".join(issues[:3]))
    _console(f"  [钩子校验] 检出 {len(issues)} 条 issue → 互补模型重生钩子…")
    _metrics_inc("hook.regenerations")
    _hook_regen = gen_hook_script(
        novel_text, bank=bank, objects_bank=objects_bank,
        forbidden_lines=forbidden_lines,
        model=_complementary_repair_model(producer_model),
    )
    if not _hook_regen:
        _logger.warn("[HOOK-QC] ⚠️ 钩子重生失败，带 issue 落盘（人工过目）：\n  - "
                     + "\n  - ".join(issues))
        _console("  [钩子校验] ⚠️ 重生失败，钩子带 issue 落盘")
        return issues
    script["hook"] = _hook_regen
    try:
        from step01_script_gen.validation import validate_hook
        _prepare_hook_for_qc(script)
        validate_hook(script["hook"], script.get("characters", []))
    except Exception as _e:
        _logger.warn(f"[WARN] 重生钩子修复/校验异常（不阻断）: {_e}")
    try:
        from step01_script_gen.timecode import sync_hook_timeline as _sync_hk
        _sync_hk(script)
    except Exception:
        pass
    remaining = _collect_hook_issues(script)
    _metrics_set("hook.final_issues", len(remaining))
    # v6.84（09-21 重跑发现）：钩子时长可能在门内被改（`sync_hook_timeline` 按
    # motion/台词重算，或换成了重生版钩子）。**必须重建时间轴**，否则 hook 的
    # `duration_sec` 与 `timeline[0].end` 不一致 → `timeline_consistency` BLOCK
    # （实测：timeline 写的是 4.0s，钩子被重算成 3.5s，total 78.14 vs sum 74.14）。
    try:
        write_timeline_from_durations(script)
    except Exception as _tl_e:
        _logger.warn(f"[WARN] 钩子质量门后重建时间轴失败: {_tl_e}")
    if remaining:
        _logger.warn("[HOOK-QC] ⚠️ 重生后钩子仍有 issue（保留落盘，人工过目）：\n  - "
                     + "\n  - ".join(remaining))
        _console("  [钩子校验] ⚠️ 重生后仍有 issue，已按原样落盘（见日志）")
    else:
        _logger.info("[OK] 钩子后置校验通过（互补模型重生版）")
        _console("  [钩子校验] 重生钩子通过校验")
    return remaining


# v6.63.1：_locate_narration_in_novel 下沉至 gap_analysis（constraints 层复用，
# 反向 import main 有重依赖 + 循环导入风险 + 触发 style_prefix 弃用 shim）。
# 此处保留同名兼容导出，行为零变化。
from step01_script_gen.gap_analysis import _locate_narration_in_novel  # noqa: F401,E402


def _strip_runtime_fields(obj):
    """递归剥掉 dict 下划线开头的 key（运行时数据，不入盘）.

    与 step02a_frame_gen.main._strip_runtime_fields 同约定：下划线开头键 =
    「不入盘」信号（_run_metadata/_merge_candidate/_qc_last 等只留在内存）。
    v6.63.1：step01 落盘时统一剥——此前只有 step02a 回写时才剥，
    _run_metadata 会泄进 script_result.json。
    """
    if isinstance(obj, dict):
        return {k: _strip_runtime_fields(v) for k, v in obj.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(obj, list):
        return [_strip_runtime_fields(v) for v in obj]
    return obj

# ============================================================================
# GAP 重试回退保护（v6.47，08-27）——重试版质量退化时保留上一版
# ============================================================================

def _retry_raw_duration(script: dict) -> float:
    """重试版原始总时长（不含钩子）——parse_response 后、hard_fix 拉伸前的值.

    W6 会把不足 60s 的脚本事后拉伸，所以只看**原始** duration_sec 之和才能
    反映 LLM 是否真的响应了总时长规则（08-27 重试版原始 11s → 被拉到 68s 的
    事故就是只看拉伸后总值看不出来）。
    """
    return sum(float(sh.get("duration_sec") or sh.get("duration") or 0)
               for sh in script.get("shots", []) or [])


def _count_ungrounded_narrations(script: dict, novel_text: str) -> int:
    """统计台词未在原文找到的镜头数（原文锚定失败 = LLM 编造对白）."""
    if not novel_text:
        return 0
    cnt = 0
    for sh in script.get("shots", []) or []:
        narr = str(sh.get("narration_segment") or sh.get("narration") or "").strip()
        if not narr:
            continue
        s, e = _locate_narration_in_novel(narr, novel_text)
        if s < 0:
            cnt += 1
    return cnt


# v6.50（08-28）：文本截断检测——LLM 响应被截断后 json_repair 捞回的残句
# （实测「镜头推近至」「惊觉对」ff_desc 尾部悬空「庄仆」直接落盘）。
# 正常 prompt/ff/lf/motion 都应以终结标点收尾；台词 narration 允许逗号结尾
# （跨镜拆分对白），故只查这 4 个字段。
#
# v6.62.6（09-09）真实截断残句的判据：结尾停在**不能收句的虚词**上。
#
# 为什么换判据：旧实现用「不以终结标点收尾」当截断信号（原 _TERMINAL_TAIL，
# 本次已随判据一并删除），09-08 实测在真实数据上命中 40/44 = 91%（上一版
# 40/40 = 100%）——而命中的全是**完整**描述，只是 doubao 写描述性字段不习惯
# 加句号（「…孩童嬉玩可见」「…昏昏欲睡」「…转场时长0.5s」）。
# 于是 `new_trunc >= 2 and new_trunc > prev_trunc` 被行文风格的随机波动稳定触发，
# **把几乎每一次 GAP 重试都判成退化回退掉**，其中包括 v6.62.4 刚加的零高潮专项
# 重试（23:10 授予重试 → 23:13 以「48 > 39 个残句」回退 → 终稿仍零高潮）。
# 两个修复互相抵消，且旧文案从不打印真正命中的判据，所以一直没被看见。
#
# 选词原则：只收**几乎不作名词结尾**的虚词，并排掉本域高频名词/形容词。
# 古风权谋语料里以下字常作名词尾，**故意不收**：
#   地(土地/空地) 把(刀把) 被(棉被) 由(理由) 跟(脚跟) 朝(王朝) 沿(河沿)
#   将(大将) 向(方向) 和(柔和/温和) 得(取得/心得)
_DANGLING_TAIL = tuple("的与及或而且并从往于让给至对")
# _DANGLING_TAIL 里 于/对/至 三字仍有名词化/固定搭配风险（「甚至」「面对」是
# 完整句的合法收尾，不是残句），按二字组合排除。
# 不变式：**每一项都必须以 _DANGLING_TAIL 中的字结尾**，否则它是永不生效的死条目
# ——由 tests/step01_script_gen/test_main_regen_guard.py 断言守住。
_DANGLING_EXCEPT = (
    "面对", "一对", "相对", "绝对", "对于", "至于", "甚至", "以至",
    "由于", "在于", "属于", "等于", "终于", "过于",
)


def _count_mid_clause_tails(script: dict) -> int:
    """统计 4 文本字段以**悬挂虚词**收尾的数量（截断 salvage 的强信号）.

    判据见 `_DANGLING_TAIL`：09-08 实测把「缺句末标点」换成「悬挂虚词收尾」后，
    真实数据命中率 91% → 0%，而 docstring 里的真实残句样例（「镜头推近至」
    「惊觉对」「抬手指向」）仍全部命中。
    """
    cnt = 0
    for sh in script.get("shots", []) or []:
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = str(sh.get(fld) or "").strip()
            if len(v) < 10:
                continue
            if v.endswith(_DANGLING_EXCEPT):
                continue
            if v.endswith(_DANGLING_TAIL):
                cnt += 1
    return cnt


def _climax_count(script: dict, novel_text: str) -> int:
    """该脚本被硬回退护栏判定为「高潮镜」的镜头数.

    回退护栏用更严格的语义字段判定：只看叙述/字幕/主体/动作，不把
    core_keywords 自身算进匹配池，也不让单字高潮词（杀/死/跑）虚增计数。
    """
    try:
        from step01_script_gen.climax_words import climax_words_for_gap
        words = [w for w in climax_words_for_gap(script) if len(w) >= 2]
        semantic_fields = (
            "narration_segment",
            "subtitle",
            "main_subject",
            "motion_desc",
        )
        count = 0
        for shot in script.get("shots", []):
            semantic_text = " ".join(
                str(shot.get(field) or "") for field in semantic_fields
            )
            if any(word in semantic_text for word in words):
                count += 1
        return count
    except Exception:
        return 0



def _score_script(script: dict, novel_text: str) -> dict:
    """通用脚本质量打分（v6.64，P4.5 多路径择优）。

    纯量化指标，零硬编码，换小说自动跟随。用于多候选择优：分数越高越好。

    返回 {
        "total": float,        # 总分 0-100
        "climax_count": int,   # 高潮镜数
        "climax_complete": bool,  # 高潮是否有结果
        "dialogue_ratio": float,  # 对白镜占比 0-1
        "ungrounded_count": int,  # 失锚台词数
        "duration_sec": float,    # 原始总时长
        "shot_count": int,        # 镜头数
        "detail": str,            # 人类可读摘要
    }
    """
    shots = script.get("shots", []) or []
    n = len(shots)
    score = 0.0
    detail_parts = []

    # 1) 高潮镜数（满分 25 分）：0=0, 1=10, 2=20, 3+=25
    climax_n = _climax_count(script, novel_text)
    if climax_n >= 3:
        score += 25
    elif climax_n == 2:
        score += 20
    elif climax_n == 1:
        score += 10
    detail_parts.append(f"高潮{climax_n}镜")

    # 2) 高潮完整性（满分 15 分）：有结果态 vs 只有起势
    try:
        from step01_script_gen.constraints.climax_completeness import (
            _get_climax_shots, _has_result,
        )
        cidx = _get_climax_shots(shots)
        climax_complete = bool(cidx) and any(
            _has_result(shots[i]) for i in cidx if i < n
        )
    except Exception:
        climax_complete = False
    if climax_complete:
        score += 15
    elif climax_n == 0:
        pass  # 零高潮，本条不加分也不减分（由高潮镜数项负责）
    else:
        score += 5  # 有高潮但不完整，给部分分
    detail_parts.append("高潮完整" if climax_complete else "高潮不完整")

    # 3) 对白密度（满分 20 分）：≥40%=20, ≥30%=15, ≥20%=10, ≥10%=5, 0=0
    from step01_script_gen.constraints.dialogue_density import _has_dialogue
    dlg_n = sum(1 for s in shots if _has_dialogue(s.get("narration_segment", "")))
    dlg_ratio = dlg_n / n if n else 0
    if dlg_ratio >= 0.40:
        score += 20
    elif dlg_ratio >= 0.30:
        score += 15
    elif dlg_ratio >= 0.20:
        score += 10
    elif dlg_ratio >= 0.10:
        score += 5
    detail_parts.append(f"对白{dlg_n}/{n}={int(dlg_ratio*100)}%")

    # 4) 台词锚定率（满分 20 分）：0 失锚=20, 1=15, 2=10, 3+=5
    ung = _count_ungrounded_narrations(script, novel_text) if novel_text else 0
    if ung == 0:
        score += 20
    elif ung == 1:
        score += 15
    elif ung == 2:
        score += 10
    else:
        score += 5
    detail_parts.append(f"失锚{ung}条")

    # 5) 时长合规（满分 10 分）：按 timecode 拆镜后的有效时长评分。
    # LLM 原始 duration_sec 常低估长台词；若按原值打分会把拆镜后超 90s
    # 的候选误判为满分（09-16：原始 17镜/75.5s → 拆后 21镜/97.6s）。
    dur = _retry_raw_duration(script)
    from step01_script_gen.timecode import project_post_split_metrics
    _projected = project_post_split_metrics(shots, fps=script.get("fps", 24))
    eff_dur = float(_projected.get("duration_sec", dur) or dur)
    eff_n = int(_projected.get("shot_count", n) or n)
    _split_n = int(_projected.get("split_count", 0) or 0)
    if 60 <= eff_dur <= 90:
        score += 10
    elif 50 <= eff_dur < 60 or 90 < eff_dur <= 120:
        score += 7
    else:
        score += 3
    if abs(eff_dur - dur) >= 0.5:
        detail_parts.append(f"时长{dur:.0f}s→{eff_dur:.0f}s")
    else:
        detail_parts.append(f"时长{dur:.0f}s")

    # 6) 镜数合规（满分 10 分）：按拆镜后的有效镜数评分
    if 8 <= eff_n <= 20:
        score += 10
    elif 5 <= eff_n < 8 or eff_n > 20:
        score += 5
    if eff_n != n:
        detail_parts.append(f"镜数{n}→{eff_n}(拆{_split_n})")
    else:
        detail_parts.append(f"镜数{n}")

    # 7) 相邻镜重复视觉惩罚（2026-09-12 打分盲区复盘：镜12/12.5 文本逐字重复
    # 曾拿 95 分）。相邻镜动作文本 Jaccard ≥0.85 视为凑镜重复，每对扣 8 分。
    try:
        from shared.split_similarity import jaccard_bigram, shot_action_text
        _dup_pairs = sum(
            1 for _i in range(n - 1)
            if jaccard_bigram(shot_action_text(shots[_i]),
                              shot_action_text(shots[_i + 1])) >= 0.85
        )
        if _dup_pairs:
            score = max(0.0, score - 8.0 * _dup_pairs)
            detail_parts.append(f"重复镜-{8 * _dup_pairs}")
    except Exception:
        _dup_pairs = 0

    detail = ", ".join(detail_parts) + f" → {score:.0f}分"

    return {
        "total": round(score, 1),
        "climax_count": climax_n,
        "climax_complete": climax_complete,
        "dialogue_ratio": round(dlg_ratio, 3),
        "ungrounded_count": ung,
        "duration_sec": round(dur, 1),
        "shot_count": n,
        "projected_duration_sec": round(eff_dur, 1),
        "projected_shot_count": eff_n,
        "projected_split_count": _split_n,
        "detail": detail,
    }

def _retry_degrade_reason(prev: dict, new: dict, novel_text: str,
                          *, climax_rescue: bool = False) -> str | None:
    """返回重试版退化的**具体原因**，健康则返回 None（v6.62.6，09-09）.

    与 `_is_retry_degraded` 同判据，只是把命中的那一条讲清楚。拆出来的原因：
    09-08 实测日志打出「重试版质量退化（原始总时长 77s vs 上一版 66s，台词失锚
    0 条），回退保留上一版」——两个被打印的指标**都不是**触发项（77s 未塌缩、
    失锚 0 条），真正命中的是第 3 条「文本中途截断」，而它从不出现在文案里。
    结果是运维读日志会得出「护栏把更好的版本判退化了」的错误结论。

    Args:
        climax_rescue: True = 上一版**零高潮**而重试版补上了高潮镜（见调用点）。
            此时判据 2/3 降级为告警不再触发回退，判据 1/1.5（时长塌缩/高潮塌缩）仍硬拦截——
            理由是价值序：判据 2/3 拦的是**措辞瑕疵**（台词没逐字对上原文、
            句子收尾不干净），判据 1/1.5 拦的是**不可挽回的结构性损坏**
            （时长塌缩后 W6 拉伸把 1s 镜拉成 8s 慢动作；整章高潮没拍直接等于废片）。
    """
    # 1) 时长塌缩
    prev_dur = _retry_raw_duration(prev)
    new_dur = _retry_raw_duration(new)
    if prev_dur >= 40 and new_dur < 40:
        return (f"时长塌缩（原始总时长 {new_dur:.0f}s < 40s，上一版 {prev_dur:.0f}s）"
                f"——事后 W6 拉伸会把短镜拉成慢动作")
    # 1.6) 镜数/时长膨胀（v6.70.3，09-14）——塌缩的镜像判据。09-14 实测：100 分
    # 候选 17 镜 84s 被采纳后，GAP 重生版 21 镜 108s **静默替换**（三条旧判据只看
    # 「变差」方向：塌缩查 <40s、失锚查变多、截断查变多——膨胀一个都不命中），
    # 终稿 20 镜 123s。上一版健康（≥60s ≤90s 合法带内）而新版膨胀出带（>90s）或
    # 镜数多 3+ → 判退化回退。救援态不豁免（救援补高潮不靠膨胀镜头数达成）。
    _prev_n = len(prev.get("shots", []) or [])
    _new_n = len(new.get("shots", []) or [])
    if (prev_dur >= 60 and new_dur > 90) or (_new_n - _prev_n >= 3):
        return (f"镜数/时长膨胀（新版 {_new_n} 镜 {new_dur:.0f}s，上一版 {_prev_n} 镜 "
                f"{prev_dur:.0f}s）——重生版把镜头预算炸出合法带（60-90s），"
                f"越修越多的镜头会把 60s 短剧拖成 2 分钟流水账")
    # 1.5) 高潮塌缩（v6.62.8，09-09）——与「零高潮救援」对称的反向护栏。
    # 09-10 run3 实测：retry1 把高潮镜从 0 补到 1（救援采纳），retry2 又打回 0，
    # 而三条判据都不看高潮，于是 retry2 被静默接受，终稿高潮镜 0、台词镜 0。
    # 「零高潮」正是当初特批 bonus 重试要解决的那个缺陷——绝不能让后一次重试
    # 把它悄悄还原。**不受 climax_rescue 豁免**（救援态下 prev 恒为 0，本条
    # 天然不会命中，放在豁免之前只是为了「结构性缺陷永远优先」这个次序清晰）。
    if novel_text:
        prev_climax = _climax_count(prev, novel_text)
        if prev_climax > 0 and _climax_count(new, novel_text) == 0:
            return (f"高潮塌缩（上一版 {prev_climax} 个高潮镜 → 新版 0 个）"
                    f"——原文核心冲突整段又没拍，退回零高潮态")
    if climax_rescue:
        return None
    # 2) 台词失锚变多
    if novel_text:
        prev_ung = _count_ungrounded_narrations(prev, novel_text)
        new_ung = _count_ungrounded_narrations(new, novel_text)
        if new_ung > prev_ung and new_ung >= 2:
            return (f"台词失锚变多（新版 {new_ung} 条未在原文找到，"
                    f"上一版 {prev_ung} 条）——疑似原文幻觉")
    # 3) 文本中途截断变多
    new_trunc = _count_mid_clause_tails(new)
    prev_trunc = _count_mid_clause_tails(prev)
    if new_trunc >= 2 and new_trunc > prev_trunc:
        return (f"文本中途截断变多（新版 {new_trunc} 个字段以悬挂虚词收尾，"
                f"上一版 {prev_trunc} 个）——LLM 响应被截断后捞回了残句")
    return None


def _is_retry_degraded(prev: dict, new: dict, novel_text: str,
                       *, climax_rescue: bool = False) -> bool:
    """判定重试版是否比上一版退化（回退保护的四个强信号）.

    1) **时长塌缩**：上一版健康（≥40s）而新版原始总时长 < 40s——LLM 明显没
       响应总时长规则（08-27 重试版 11s 事故），事后拉伸会把 1s 镜头拉到 8s。
    1.5) **高潮塌缩**：上一版有高潮镜而新版归零——整章核心冲突整段没拍，
       属于不可挽回的结构性缺陷，必须硬拦截。
    1.6) **镜数/时长膨胀**（v6.70.3，09-14）：上一版在合法带内（≥60s）而新版
       膨胀出带（>90s）或镜数多 3+——「越修越多」与「越修越塌」同样是退化。
       09-14 实测：100 分 17 镜候选被 GAP 重生 21 镜版静默替换，终稿 123s。
    2) **台词失锚变多**：新版未在原文找到的台词数 > 上一版（原文幻觉，如
       「凶手是谁？如何击杀了公子」）。≥2 条才判退化，防短语匹配误报。
    3) **文本中途截断**（v6.50，08-28；判据 v6.62.6 重写）：新版 ≥2 个文本字段
       以**悬挂虚词**（`_DANGLING_TAIL`：的/与/至/对/于…）收尾且比上一版多——
       LLM 响应被截断、json_repair 捞回残句（实测「镜头推近至」「惊觉对」
       直接落盘）。**不再**用「缺句末标点」当信号，那条在真实数据上 91% 是误报。

    判据实现在 `_retry_degrade_reason`（单一事实源，附带命中原因文案）。
    `climax_rescue=True` 时只保留判据 1 和 1.5，语义见该函数 docstring。

    Returns:
        True = 重试版退化，应回退保留 prev。
    """
    return _retry_degrade_reason(
        prev, new, novel_text, climax_rescue=climax_rescue
    ) is not None


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


_PATCH_ALLOWED_RE = re.compile(
    r"^(prompt|ff_desc|lf_desc|motion_desc|color_tone)$"
    r"|^keyframes\[\d+\]\.(desc|t_sec|stage)$"
    r"|^keyframes\.append$"
    r"|^frame_layouts\.(ff|lf)\[\d+\]\.(pose|gaze_target|position|on_screen|surface|props)$"
    r"|^frame_layouts\.kfs\[\d+\]\[\d+\]\.(pose|gaze_target|position|on_screen|surface|props)$"
    r"|^frame_layouts\.kfs\.append$"
    r"|^char_layout\[\d+\]\.position$"
)


def _patch_tokens(field_path: str) -> list:
    tokens = []
    for part in str(field_path or "").split("."):
        m = re.match(r"^([A-Za-z_]+)((?:\[\d+\])*)$", part)
        if not m:
            return []
        tokens.append(m.group(1))
        tokens.extend(int(x) for x in re.findall(r"\[(\d+)\]", m.group(2)))
    return tokens


def _locked_signature_diff(before_sig: str, after_sig: str, *, limit: int = 8) -> list:
    """两个 `_locked_repair_signature` JSON 之间的**字段级**差异.

    v6.94：`locked_changed` 是第九次重跑后修复轮**唯一**的绑定闸门，而它的判据是
    「7 个字段的整串 JSON 变了没有」——不看变了什么、也不看本轮授权了什么。
    这里给出 `镜头.字段: 旧 → 新`，与 `_shot_field_diff` 同一做法：**先拿到字段名，
    再决定修法**，不猜根因。
    """
    import json as _json

    try:
        b = {str(x.get("id")): x for x in _json.loads(before_sig)}
        a = {str(x.get("id")): x for x in _json.loads(after_sig)}
    except Exception:
        return []
    out: list = []
    for sid in sorted(set(b) | set(a), key=str):
        sb, sa = b.get(sid), a.get(sid)
        if not isinstance(sb, dict) or not isinstance(sa, dict):
            out.append("%s: 新增/删除" % sid)
            if len(out) >= limit:
                return out
            continue
        for f in sorted(set(sb) | set(sa)):
            if f == "id":
                continue
            if sb.get(f) != sa.get(f):
                out.append("%s.%s: %r → %r" % (sid, f, sb.get(f), sa.get(f)))
                if len(out) >= limit:
                    return out
    return out


def _locked_repair_signature(script: dict) -> str:
    import json as _json
    out = []
    units = []
    if isinstance(script.get("hook"), dict):
        units.append(("hook", script["hook"]))
    units.extend((str(s.get("index")), s) for s in (script.get("shots") or []))
    for sid, shot in units:
        if not isinstance(shot, dict):
            continue
        out.append({
            "id": sid,
            "index": shot.get("index"),
            "duration_sec": shot.get("duration_sec"),
            "narration_segment": shot.get("narration_segment"),
            "subtitle": shot.get("subtitle"),
            "scene": shot.get("scene"),
            "location": shot.get("location"),
            "variation_type": shot.get("variation_type"),
        })
    return _json.dumps(out, ensure_ascii=False, sort_keys=True)


def _apply_repair_patches(script: dict, patches: list, picked: set[str]) -> tuple[dict, int]:
    """Apply narrow field patches only to picked shots; reject unknown paths.

    ⚠️ **已停用，禁止在运行时自动切回**（决定 2，2026-09-20）。

    修复轮自 v6.78 起改为 `_apply_shot_replacements`（完整镜头事务）。
    `shot_replacements[]` 是**唯一继续演进**的修复契约；模型输出不合格时保留
    修复前镜头、记录拒绝原因并进入完整镜头 regen——**不得**自动回退到本函数。
    旧字段补丁恰好重新引入本轮要消除的字段不一致与"审计后语义写入"，
    不能作为生产降级方案（见 implementation-review 决定 2）。

    保留本函数仅因还有单测引用，跑批验证通过后应连同
    `_PATCH_ALLOWED_RE` / `REPAIR_PATCH_SCHEMA` / test_repair_patches.py 一并删除。
    """
    import copy as _copy
    merged = _copy.deepcopy(script)
    applied = 0
    for patch in patches or []:
        if not isinstance(patch, dict):
            continue
        sid = str(patch.get("shot_id") or "").strip()
        field_path = str(patch.get("field_path") or "").strip()
        value = patch.get("value")
        if not sid or not field_path or not _PATCH_ALLOWED_RE.match(field_path):
            continue
        target = None
        if sid in ("0", "hook"):
            if "hook" in picked:
                target = merged.get("hook")
        else:
            # Patches are scoped to explicitly picked shots.  A valid field on
            # an unpicked shot must still be rejected.
            if f"shot_{sid}" not in picked:
                continue
            for shot in merged.get("shots", []):
                if str(shot.get("index")) == sid:
                    target = shot
                    break
        if not isinstance(target, dict):
            continue
        import json as _patch_json_value
        if field_path == "keyframes.append":
            try:
                item = value if isinstance(value, dict) else _patch_json_value.loads(str(value))
            except Exception:
                continue
            if not isinstance(item, dict) or not str(item.get("desc") or "").strip():
                continue
            try:
                t_sec = float(item.get("t_sec"))
            except (TypeError, ValueError):
                continue
            try:
                duration = float(target.get("duration_sec") or target.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if t_sec < 0 or (duration > 0 and t_sec > duration + 0.01):
                continue
            item = dict(item)
            item["t_sec"] = round(t_sec, 3)
            kfs = target.setdefault("keyframes", [])
            if not isinstance(kfs, list):
                continue
            kfs.append(item)
            kfs.sort(key=lambda k: float(k.get("t_sec") or k.get("time_ratio") or 0)
                     if isinstance(k, dict) else 0)
            applied += 1
            continue
        if field_path == "frame_layouts.kfs.append":
            try:
                item = value if isinstance(value, list) else _patch_json_value.loads(str(value))
            except Exception:
                continue
            if not isinstance(item, list) or not item:
                continue
            if not all(isinstance(x, dict) and str(x.get("name") or "").strip()
                       for x in item):
                continue
            layout = target.setdefault("frame_layouts", {})
            if not isinstance(layout, dict):
                continue
            layout.setdefault("kfs", []).append(item)
            applied += 1
            continue
        tokens = _patch_tokens(field_path)
        if not tokens:
            continue
        cur = target
        ok = True
        for tok in tokens[:-1]:
            try:
                cur = cur[tok]
            except Exception:
                ok = False
                break
        if not ok:
            continue
        last = tokens[-1]
        if not isinstance(cur, dict) or last not in cur:
            continue
        if last == "on_screen":
            raw_value = str(value).strip().lower()
            if raw_value not in ("1", "0", "true", "false", "yes", "no", "是", "否"):
                continue
            value = raw_value in ("1", "true", "yes", "是")
        elif last == "props":
            try:
                value = value if isinstance(value, list) else _patch_json_value.loads(str(value))
            except Exception:
                value = [x.strip() for x in str(value).split(",") if x.strip()]
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                continue
            value = [x.strip() for x in value if x.strip()]
        elif last == "t_sec":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            try:
                duration = float(target.get("duration_sec") or target.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if value < 0 or (duration > 0 and value > duration + 0.01):
                continue
            value = round(value, 3)
        elif last in ("desc", "stage", "pose", "gaze_target", "position", "surface",
                      "prompt", "ff_desc", "lf_desc", "motion_desc", "color_tone"):
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value or len(value) > 2000:
                continue
            if last == "stage":
                from shared.kf_time import KF_STAGE_ENUM
                if value not in KF_STAGE_ENUM:
                    continue
            if last == "position":
                from step01_script_gen.scene_anchors import is_valid_position
                if not is_valid_position(
                    value, str(target.get("location") or target.get("scene") or "")
                ):
                    continue
        else:
            continue
        cur[last] = value
        applied += 1
    return merged, applied


# ===========================================================================
# v6.77（第四阶段）：修复轮「完整镜头事务」
#
# 旧路径（`_apply_repair_patches`）收字段级补丁，结构字段之间可以互不一致
# （补了 motion 没补 keyframes、补了 lf 没补 frame_layouts.kfs），且之后还要跑
# post_fix/hard_fix/timecode 去兜——接受判定比较的是「补丁 + 全部后处理副作用」，
# 后处理引入的新阻断会把本该通过的局部修复整轮拖回滚。
#
# 新路径收**完整镜头对象**，在副本里只校验该镜与其相邻边界，通过后原子替换；
# 未点名的镜头逐字节不动（见 `_repair_acceptance_decision` 的
# `non_target_changed` 判据）。本阶段同时**不再**在修复轮里跑全局拆镜
# （`_compute_timecodes(..., allow_split=False)`）。
# ===========================================================================
_REPLACEMENT_REQUIRED_KEYS: tuple = (
    "index", "narration_segment", "subtitle", "prompt", "motion_desc",
    "ff_desc", "lf_desc", "duration", "keyframes", "frame_layouts",
)
# 替换镜头的旁白允许比原镜长出的比例上限（「原文引用未越界」的代理判据；
# 完整原文锚定仍由 validation._check_grounding 在下游把关）
_REPLACEMENT_NARR_GROWTH = 1.5


def _validate_replacement_shot(orig: dict | None, new: dict, *,
                                is_hook: bool = False,
                                allowed: set | None = None) -> str:
    """校验一条「完整镜头替换」；返回空串=通过，否则返回拒绝原因（给日志）.

    `is_hook=True` 时按 `_HOOK` 的字段要求校验（hook 无 scene/location）。

    `allowed` 是本轮**授权修改的字段路径集合**（由该 target 的 warning/audit
    issue 推导，见 `repair_fields.allowed_repair_fields`）。给定后，替换镜头与
    原镜做 canonical 路径 diff，除授权路径与纯派生字段外任何差异都拒绝——
    二次审核发现 2：只锁一份**固定** mask 挡不住「修 prompt 时顺带改 motion/
    `temporal_plan`/KF」，那些改动虽被 revision 哈希记录，却没有得到授权。
    """
    from shared.kf_time import KF_STAGE_ENUM

    if not isinstance(new, dict):
        return "shot 不是对象"
    _req = _REPLACEMENT_REQUIRED_KEYS
    if is_hook:
        # hook 用 _HOOK：无 scene/location（那是普通镜头字段）
        _req = tuple(k for k in _req if k not in ("scene", "location"))
    missing = [k for k in _req if k not in new]
    if missing:
        return f"结构字段缺失（必须成套出现）: {missing}"
    if orig is not None and str(new.get("index")) != str(orig.get("index")):
        return f"index 被改动（{orig.get('index')} -> {new.get('index')}）"

    try:
        dur = float(new.get("duration_sec") or new.get("duration") or 0)
    except (TypeError, ValueError):
        return "duration 非数值"
    if dur <= 0:
        return "duration <= 0"
    if dur > 30.0:
        return f"duration {dur:.1f}s 异常偏大"

    # v6.80（发现 9 #2）：`duration_sec` 由**唯一纯派生函数**
    # （timecode.write_timeline_from_durations）从 `duration` 生成，
    # 替换镜头不得自带一个与之不一致的 `duration_sec`——两种口径并存会让
    # 「prompt 禁止改 duration_sec」与「schema 只强制 duration」互相矛盾。
    if "duration_sec" in new:
        try:
            ds = float(new.get("duration_sec"))
        except (TypeError, ValueError):
            return "duration_sec 非数值"
        try:
            dd = float(new.get("duration"))
        except (TypeError, ValueError):
            return "duration 非数值"
        if abs(ds - dd) > 0.011:
            return (f"duration_sec({ds:g}) 与 duration({dd:g}) 不一致——"
                    "duration_sec 由时间轴序列化函数统一派生，不得自带第二种口径")

    kfs = new.get("keyframes")
    if not isinstance(kfs, list):
        return "keyframes 不是数组"
    for j, kf in enumerate(kfs):
        if not isinstance(kf, dict):
            return f"keyframes[{j}] 不是对象"
        if not str(kf.get("desc") or "").strip():
            return f"keyframes[{j}].desc 为空"
        if str(kf.get("stage") or "") not in KF_STAGE_ENUM:
            return f"keyframes[{j}].stage 非法: {kf.get('stage')!r}"
        try:
            t = float(kf.get("t_sec"))
        except (TypeError, ValueError):
            return f"keyframes[{j}].t_sec 非数值"
        if t <= 0 or t > dur + 0.01:
            return f"keyframes[{j}].t_sec={t} 越出镜长 {dur}"

    layouts = new.get("frame_layouts")
    if not isinstance(layouts, dict):
        return "frame_layouts 不是对象"
    lkfs = layouts.get("kfs")
    if not isinstance(lkfs, list):
        return "frame_layouts.kfs 不是数组"
    if len(lkfs) != len(kfs):
        return f"frame_layouts.kfs 长度 {len(lkfs)} ≠ keyframes 长度 {len(kfs)}"

    if orig is not None:
        for fld in ("narration_segment", "subtitle"):
            old_len = len(str(orig.get(fld) or ""))
            new_len = len(str(new.get(fld) or ""))
            if old_len and new_len > old_len * _REPLACEMENT_NARR_GROWTH + 10:
                return (f"{fld} 长度 {old_len} -> {new_len}，超出 "
                        f"{_REPLACEMENT_NARR_GROWTH}x 增长上限（原文引用可能越界）")
        # v6.83（二次审核发现 2）：**按 issue 授权**——只有被问题点名的字段可改。
        if allowed is not None:
            from step01_script_gen.repair_fields import check_replacement_authorized
            reason = check_replacement_authorized(orig, new, allowed)
            if reason:
                return reason
    return ""


def _norm_target_id(raw) -> str:
    """把 target id 归一到 applier 的裸 id 口径（`shot_10` → `10`，`hook` 原样）.

    v6.84：修复轮的 REPAIR FEEDBACK 是按 `shot_<id>` 分组的（`_feedback_json["shots"]`
    的键），模型很自然地照抄这个格式回填 `shot_id`——而 applier 只认裸 `10`。实测
    2026-09-21 重跑：模型返回 `{"shot_id": "shot_10"}`，被 `_find()` 判成「未被点名
    或不存在」，整批（含合法的 hook 替换）一起回滚，`resolved=0`。

    **反馈格式在教模型一个 applier 不认的写法**——修 applier 一侧（宽容两种写法），
    而不是只改 prompt 措辞（模型仍会照抄分组键）。
    """
    sid = str(raw or "").strip()
    if sid.startswith("shot_"):
        sid = sid[5:]
    return sid


from step01_script_gen.repair_fields import merge_authorized as _merge_authorized


def _apply_shot_replacements(script: dict, replacements: list,
                             picked: set[str],
                             hook_replacement: dict | None = None,
                             allowed_by_target: dict | None = None,
                             expected_targets: set | None = None) -> tuple[dict, int, list]:
    """把「完整镜头替换」**逐目标**应用到被点名的镜头（v6.86 用户决定 A1）.

    语义（v6.86 起）：

      - **逐目标提交**：每个通过单镜校验（字段授权 / 越权路径 diff / 锁定字段 /
        结构校验）的目标**独立生效**；某个目标缺项、重复或越权只影响它自己，
        不再否决同批其他合法目标。缺项作为**如实回报**进入 `rejected`。
      - **hook 独立承载**：hook 用 `_HOOK` 而非 `_SHOT`，字段要求不同，由
        `hook_replacement` 单独传入；塞进 `shot_replacements` 即拒。
      - 只替换被点名的镜头；未点名镜头逐字节不动。
      - 批次级安全网（全局完整性 / 非目标 revision / 阻塞退化 / 锁定字段签名）
        仍在**修复轮调用侧**，本函数不负责。

    v6.83 的「整批全有或全无」已被 A1 取代：实测 2026-09-21 22:21:22，模型漏回
    一个目标导致含合法替换的整批回滚、修复轮产出 0。

    Returns:
        (merged_script, 成功替换数, 拒绝原因列表)；
        一个都没提交时返回**原脚本**且计数为 0
    """
    import copy as _copy
    merged = _copy.deepcopy(script)
    rejected: list[str] = []
    staged: list[tuple[dict, dict, str]] = []
    _seen_ids: set = set()

    def _find(sid: str):
        if sid in ("0", "hook"):
            return merged.get("hook") if "hook" in picked else None
        if f"shot_{sid}" not in picked:
            return None
        for sh in merged.get("shots", []):
            if isinstance(sh, dict) and str(sh.get("index")) == sid:
                return sh
        return None

    def _stage(item: dict, *, force_hook: bool = False) -> None:
        if not isinstance(item, dict):
            rejected.append("条目不是对象")
            return
        sid = _norm_target_id(item.get("shot_id"))
        new_shot = item.get("shot")
        if not sid:
            rejected.append("shot_id 为空")
            return
        if sid in ("0", "hook") and not force_hook:
            # v6.83（二次审核发现 3）：hook 的字段要求与普通镜头不同（无
            # scene/location），必须由 `hook_replacement` 承载；塞进
            # `shot_replacements` 会按 `_SHOT` 校验出一个字段语义不符的对象。
            rejected.append("hook 替换必须用 hook_replacement 承载，不得放进 shot_replacements")
            return
        if force_hook:
            sid = "hook"
        target = _find(sid)
        if target is None:
            rejected.append(f"shot {sid} 未被点名或不存在")
            return
        if sid in _seen_ids:
            rejected.append(f"shot {sid} 重复出现（每个目标恰好一次，后者丢弃）")
            return
        # v6.86（用户决定 A1）：`expected_targets` 的成员校验下沉到**单条目**——
        # 「不在本轮点名集合内」是这一条的问题，不该否决别的目标。
        if expected_targets is not None and sid not in {str(t) for t in expected_targets}:
            rejected.append(f"shot {sid} 不在本轮点名集合内（该条丢弃）")
            return
        allowed = None
        if isinstance(allowed_by_target, dict):
            allowed = allowed_by_target.get(f"shot_{sid}", allowed_by_target.get(sid))
            if allowed is None and not force_hook:
                # 授权集合缺失 = 该目标没有明确字段路径 → 不得走定向修复
                rejected.append(f"shot {sid}: 本轮未授权任何字段（问题未点名字段路径）")
                return
            allowed = allowed or set()
        # v6.91（用户决定，2026-09-22）：提交前把**未授权路径从原镜强制回填**。
        #
        # 原实现直接判越权 → 整条拒绝。实测 07:39 3/3 目标因此被拒：模型必须回填
        # 全部 24 个字段，而授权只覆盖少数几个路径，措辞略异即判越权。回填后
        # 「未授权路径 = 原样」由**构造保证**（差异只在授权路径内），
        # `check_replacement_authorized` 保留为纵深防御。
        if allowed is not None:
            new_shot, _echoed = _merge_authorized(target, new_shot, allowed)
            if _echoed:
                _logger.info(
                    f"[REPAIR] shot {sid}: 未授权路径已回填 {len(_echoed)} 处: "
                    f"{sorted(set(_echoed))[:5]}"
                )
        reason = _validate_replacement_shot(target, new_shot, is_hook=force_hook,
                                            allowed=allowed)
        if reason:
            rejected.append(f"shot {sid}: {reason}")
            return
        _seen_ids.add(sid)
        staged.append((target, new_shot, sid))

    for _item in replacements or []:
        _stage(_item)
    if isinstance(hook_replacement, dict):
        _stage(hook_replacement, force_hook=True)

    # v6.86（**用户决定 A1**，2026-09-21）：**逐目标提交**，取代 v6.83 的
    # 「整批全有或全无」。
    #
    # 实测（2026-09-21 22:21:22）：
    #   `[REPAIR] 替换被拒 1 条: ["目标集合不完整：缺少 ['16']（本轮点名
    #    ['16','4','hook']）"]`
    # 模型回了镜头 4 与 hook 的替换、漏了镜头 16 → 整批回滚，`resolved=0`。
    # 而镜头 4 与 hook 的替换**本身已通过授权校验**，一并作废——修复轮产出 0。
    #
    # 为什么逐目标提交是安全的：`expected_targets` 要防的是「只提交一部分却当
    # 整批成功」，那是**报告失真**，不是**数据损坏**。数据侧的安全保证全部是
    # **单镜作用域**（`check_replacement_authorized` 字段授权 / `canonical_path_diff`
    # 越权路径 / `_final_field_mask_violations` 锁定字段 / 逐镜 revision gate /
    # 每镜时间不变量），而 replacement 是「完整镜头对象」、**不写任何跨镜字段**，
    # 所以不存在「只有跟别镜一起改才成立」的情况。
    #
    # 保留的批次级安全网（都在修复轮调用侧，不在此函数内）：
    #   - 全局完整性（checker 失败 → 整轮回滚）
    #   - **非目标镜头 revision**（`changed_shot_keys` 逐镜比对，动到未点名镜头即拒）
    #   - **阻塞退化**（本轮 blocking 身份新增即拒）
    #   - 锁定字段签名（`_locked_repair_signature`）
    # 缺项如今只作为**如实回报**出现在 `rejected` 里，不再否决已提交的目标。
    if not staged:
        return script, 0, rejected or ["未提供任何替换条目"]

    for _target, _new, _sid in staged:
        _target.clear()
        _target.update(_new)
    committed = len(staged)

    if expected_targets is not None:
        got = {_sid for _, _, _sid in staged}
        missing = sorted({str(t) for t in expected_targets} - got)
        if missing:
            rejected.append(
                f"目标 {missing} 本轮未返回替换（其余 {committed} 个目标已提交）"
            )
    return merged, committed, rejected



# 最终字段 mask：被点名镜头里 **LLM 不得改动**的字段（v6.80，发现 9 #4）。
# 注意**不含 `duration_sec`**——它由 `timecode.write_timeline_from_durations`
# 从 `duration` 统一派生（唯一口径），模型只负责 `duration`。
# v6.83（二次审核发现 2）：补上 `duration`。修复 prompt 一直把「index / duration」
# 声明为 Mask 字段，运行时却只锁 index——「duration 不可改」这条契约在代码里没有
# 对应实现，LLM 顺带改时长不会被任何判据拦下。
_FINAL_LOCKED_FIELDS = (
    "index", "narration_segment", "subtitle", "scene", "location", "variation_type",
    "duration",
)
# 数值型 Mask 字段的比较容差：`write_timeline_from_durations` 会把 duration 定点到
# 2 位小数（`_align_frame_duration`），定点差不该被当成「改了时长」。
_LOCKED_NUMERIC_TOL = 0.011


def _final_field_mask_violations(before: dict, after: dict, picked: set[str],
                                 allowed_by_target: dict | None = None) -> list[str]:
    """找出被点名镜头里被改动的「锁定字段」中**本轮没有授权**的那些.

    发现 9 #4：上一版只在 patch 之后、后处理**之前**取一次 locked 快照，于是
    `post_fix` / `hard_fix` / 伤情兜底对锁定字段的写入逃过比较。

    v6.99（2026-09-23 第十四次重跑实测）：本闸门与**字段授权层**互相矛盾，
    把**授权过**的改动判成越权，整轮回退掉了 10 条已解决的阻塞：

    ```text
    [REPAIR] 接受条件: before=25+audit1 after=15 resolved=10 audit_resolved=1 new=0
    [REPAIR] LLM 改动了锁定字段 2 处: ['shot 4.duration: 4.0 -> 6.0', 'shot 7.duration: 4.0 -> 5.0']
    [REPAIR] 修复未满足接受条件（reason=locked_field_changed, ...），回退本轮修复
    ```

    而 `duration` **正是**时长类 issue 授权改的字段（`repair_fields._REPAIR_CODE_FIELDS`
    把 `shot_duration → ("duration",)`；消息里的「时长」也会被 `_fields_from_text`
    解析成 `duration`）。旁证：日志里 shot 7 的「未授权路径已回填」**不含** duration
    ——说明它确实在授权集合内、被 `merge_authorized` 有意放行。

    判据改为：**锁定字段中本轮未授权的那些**才算越权。授权层是**按 issue** 推导的
    具体判断，`_FINAL_LOCKED_FIELDS` 是粗粒度默认值——具体优先于默认，而默认仍
    兜住「没被授权的锁定字段」（这才是纵深防御的本意）。
    """
    out: list[str] = []

    def _by_key(script: dict) -> dict:
        d = {"hook": script.get("hook")} if isinstance(script.get("hook"), dict) else {}
        for i, sh in enumerate(script.get("shots") or []):
            if isinstance(sh, dict):
                d[str(sh.get("index", i))] = sh
        return d

    b, a = _by_key(before), _by_key(after)
    targets = {str(x)[5:] for x in picked if str(x).startswith("shot_")}
    if "hook" in picked:
        targets.add("hook")
    for key in sorted(targets):
        sb, sa = b.get(key), a.get(key)
        if not isinstance(sb, dict) or not isinstance(sa, dict):
            continue
        # v6.99：本轮**授权过**的字段不算越权（授权按 issue 推导，比默认值具体）
        _allowed = set()
        if allowed_by_target:
            for _p in (allowed_by_target.get("shot_%s" % key)
                       or allowed_by_target.get(key) or ()):
                _allowed.add(str(_p))
        for f in _FINAL_LOCKED_FIELDS:
            if f in _allowed:
                continue
            vb, va = sb.get(f), sa.get(f)
            if isinstance(vb, (int, float)) and isinstance(va, (int, float)) \
                    and not isinstance(vb, bool) and not isinstance(va, bool):
                if abs(float(vb) - float(va)) <= _LOCKED_NUMERIC_TOL:
                    continue
            if vb != va:
                out.append(f"shot {key}.{f}: {vb!r} -> {va!r}")
    return out


# ===========================================================================
# v6.83（二次审核发现 1 实施 #3）：落盘前的**唯一时间收口**
# ===========================================================================
def _record_time_field_write_audit(script: dict, tracker, *, label: str) -> list:
    """把阶段屏障的实测结果写进脚本（运行时键，不入盘）并告警；返回越权记录.

    `quality_issues._time_field_audit_issues` 会把它转成 blocking Issue——
    发现 1 指出「只写日志」是 fail-open：越权写入照样重建 timeline、算 revision、
    落盘，正式结果里看不出异常。
    """
    unauthorized = tracker.unauthorized_writes()
    structural = tracker.structural_writes()
    if unauthorized or structural:
        # 结构段写入也记录（**没有越权 ≠ 没有写入者**）——它们是「时间字段到底有
        # 几个写入者」的直接证据，正是本审计最初要回答的问题。
        script["_time_field_write_audit"] = {
            "unauthorized": [{"constraint": n, "diff": d} for n, d in unauthorized],
            "structural": [{"constraint": n, "diff": d} for n, d in structural],
        }
    if unauthorized:
        _logger.warn(
            "[WARN] 审计段越权写入时间字段（%d 个约束）：%s —— 链前 KF/时长检查的"
            "结论对最终对象不成立；已升级为 blocking issue（见 %s quality gate）"
            % (len(unauthorized), ", ".join(n for n, _ in unauthorized), label)
        )
    return unauthorized


def _record_semantic_write_audit(script: dict, tracker, *, label: str) -> list:
    """把阶段屏障的**语义/结构**写入结果写进脚本（运行时键）并告警；返回未受豁免记录.

    v6.98（2026-09-22 审核 P0-3）：屏障此前只快照时间字段，审计段里
    「删掉一整个镜头对象」这类写入零报告——`opening_empty_shot_remove` 在审计
    边界之后 `shots.pop(0)`，被删镜的 prompt / ff / lf / motion / KF / 布局 /
    姿态 / 道具随 `del` 一起消失，而「审计段只读」这条契约**只在注释里成立**。

    现在语义/结构写入分两类落盘（`quality_issues._semantic_write_audit_issues` 消费）：
      - `unauthorized` → **blocking**（`semantic_audit_write`）；
      - `sanctioned`（`STRUCTURAL_SANCTIONED_WRITERS`）→ 不阻塞，转 advisory 留证。
    """
    unauthorized = tracker.unauthorized_semantic_writes()
    sanctioned = tracker.sanctioned_semantic_writes()
    if unauthorized or sanctioned:
        script["_semantic_write_audit"] = {
            "unauthorized": [{"constraint": n, "diff": d} for n, d in unauthorized],
            "sanctioned": [{"constraint": n, "diff": d} for n, d in sanctioned],
        }
    if unauthorized:
        _logger.warn(
            "[WARN] 审计段越权写入语义/结构（%d 个约束）：%s —— 镜头增删/重排/语义改写"
            "会让链前结论对最终对象不成立；已升级为 blocking issue（见 %s quality gate）"
            % (len(unauthorized), ", ".join(n for n, _ in unauthorized), label)
        )
    if sanctioned:
        _logger.info(
            "[AUDIT] 审计段受豁免的结构写入：%s（不阻塞，已留 advisory 证据）"
            % ", ".join("%s(%d 处)" % (n, len(d)) for n, d in sanctioned)
        )
    return unauthorized


def finalize_step01_script(script: dict, *, novel_text: str = "",
                           label: str = "落盘前") -> tuple[dict, list[str]]:
    """时间收口：hard-fix（含阶段屏障）→ timeline 序列化 → 最终时间不变量.

    三条路径（正常生成 / 缓存命中 / BEST-GUARD 恢复）此前各写一遍
    「hard-fix → write_timeline_from_durations → 一致性检查」，顺序与守卫各不
    相同（缓存命中路径曾长期漏跑 hard-fix）。这里收成一个入口，任何入口都不能
    绕过阶段屏障与最终不变量检查。

    **顺序是契约的一部分**：最终时间不变量必须在**最后一个**时间写入者之后判定
    ——即 hard-fix 与 `write_timeline_from_durations`（`duration_sec` /
    `timecode` 的派生点）都跑完之后。

    Returns:
        (script, errors)：errors 为空表示时间轴自洽；非空由调用方决定是告警
        （正常 / 缓存路径）还是放弃（BEST-GUARD 恢复）。
    """
    from step01_script_gen.constraints import run_hard_fix as _hfix_final
    from shared.semantic_barrier import TimeFieldWriteTracker
    from step01_script_gen.quality_issues import final_time_invariant_issues

    tracker = TimeFieldWriteTracker(script)
    try:
        script = _hfix_final(script, is_in_repair_loop=False, is_final_pass=True,
                             novel_text=novel_text,
                             on_constraint_done=tracker.observe)
    except Exception as _hfe:
        _logger.warn(f"[WARN] {label} hard_fix 失败（保留原版）: {_hfe}")
    _record_time_field_write_audit(script, tracker, label=label)
    # v6.98（审核 P0-3）：时间字段之外，审计段的**语义/结构**写入同样要收口。
    _record_semantic_write_audit(script, tracker, label=label)

    errors: list[str] = []
    try:
        write_timeline_from_durations(script)
        _tl_errs = timeline_consistency_errors(script)
        if _tl_errs:
            errors.extend(str(e) for e in _tl_errs)
    except Exception as _tl_e:
        errors.append(f"timeline 序列化失败: {_tl_e}")

    # 最后一个时间写入者之后：查「帧是否落在镜内」「时长是否单一口径」
    _inv = final_time_invariant_issues(script)
    if _inv:
        errors.extend(f"{i.code}@shot{i.shot_id}: {i.message}" for i in _inv)
        _logger.warn(
            f"[WARN] {label} 最终时间不变量不成立（{len(_inv)} 条，已交 quality gate "
            f"判 blocking）：{[i.code for i in _inv][:5]}"
        )
    if errors and not _inv:
        _logger.warn(f"[WARN] {label} 最终时间轴归一失败: {'; '.join(errors[:3])}")
    return script, errors


def _blocking_record_key(rec: dict) -> tuple:
    """Identity used to compare before/after repair outcomes.

    The audit contract is intentionally locator-based: code + shot + frame +
    entity + field.  Message wording is not identity and must never make an
    unchanged issue look like a newly introduced blocker.
    """
    return (
        str(rec.get("code") or ""),
        str(rec.get("shot_id") if rec.get("shot_id") is not None else ""),
        str(rec.get("frame_id") or ""),
        str(rec.get("entity_id") or ""),
        str(rec.get("field_path") or ""),
    )


def _blocking_record_keys(state: dict | None) -> set[tuple]:
    keys: set[tuple] = set()
    for rec in (state or {}).get("issues") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("severity") or "") not in ("hard", "visual_risk"):
            continue
        keys.add(_blocking_record_key(rec))
    return keys


def _audit_issue_newly_resolved(script_after: dict, script_before: dict | None,
                                issue: Issue) -> bool:
    """该 audit issue 是否**在本轮内新解决**（v6.102）.

    `_audit_issue_resolved()` 问的是「evidence **现在**还在不在稿子里」——它是
    **幂等**的：第 1 轮清掉之后永远为真。第十六次修复前的实测（第十五次）：

    ```text
    第 2 轮: before=10+audit2 after=10 resolved=0 audit_resolved=2
    第 3 轮: before=10+audit2 after=10 resolved=0 audit_resolved=2   ← 数字一模一样
    ```

    两轮 `resolved=0`，却都靠这个**陈旧的 2** 通过验收（接受条件里
    `audit_resolved > 0` 即可），于是同一批 4 镜被反复改写两次、白烧 2 次 LLM 调用
    而没有任何可测量的收益。

    加一个基线口径：**基线里已经「解决」的不算本轮功绩**——只有基线里 evidence
    仍在、本轮之后才消失的，才算新解决。
    """
    if script_before is None:
        return _audit_issue_resolved(script_after, issue)
    return (not _audit_issue_resolved(script_before, issue)
            and _audit_issue_resolved(script_after, issue))


def _audit_issue_resolved(script: dict, issue: Issue) -> bool:
    """Best-effort verification for an audit-only issue without a second LLM call.

    An audit issue is considered resolved only when its original evidence is no
    longer present in the targeted text.  This is deliberately conservative:
    unverifiable audit issues cannot justify keeping a patch.
    """
    evidence = str(getattr(issue, "evidence", "") or "").strip()
    if len(evidence) < 8:
        return False
    sid = str(getattr(issue, "shot_id", "") or "")
    shot = script.get("hook") if sid in ("0", "hook") else None
    if shot is None:
        for item in script.get("shots") or []:
            if str(item.get("index")) == sid:
                shot = item
                break
    if not isinstance(shot, dict):
        return True  # target shot was removed by a structural patch

    texts: list[str] = []
    field_path = str(getattr(issue, "field_path", "") or "")
    if field_path in ("prompt", "ff_desc", "lf_desc", "motion_desc", "color_tone"):
        texts.append(str(shot.get(field_path) or ""))
    else:
        for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            texts.append(str(shot.get(field) or ""))
        for kf in shot.get("keyframes") or []:
            if isinstance(kf, dict):
                texts.append(str(kf.get("desc") or ""))
    return not any(evidence in text for text in texts)


def _repair_acceptance_decision(before_state: dict | None,
                                after_state: dict | None,
                                audit_issues: list[Issue] | None,
                                script_after: dict,
                                locked_changed: bool,
                                script_before: dict | None = None,
                                non_target_changed: list | None = None,
                                locked_by_llm_changed: list | None = None) -> dict:
    """Pure acceptance decision for one targeted-repair round.

    Required conditions:
      * both checker runs completed successfully;
      * no new blocking issue identity appears;
      * locked fields are unchanged;
      * **未被点名的镜头语义 revision 完全不变**（v6.77 第四阶段新增）——
        修复轮是「镜头事务」，只许动被点名的镜头；
      * **LLM 未改动被点名镜头的锁定字段**（v6.87：基线取「合并后、后处理前」，
        只含 LLM 的改动；后处理是管线自己的确定性归一化，不算越权）；
      * at least one target issue (deterministic or audit) is verifiably gone.
    """
    before_keys = _blocking_record_keys(before_state)
    after_keys = _blocking_record_keys(after_state)
    resolved = before_keys - after_keys
    new_blocking = after_keys - before_keys
    checker_failed = bool((before_state or {}).get("checker_failed")
                          or (after_state or {}).get("checker_failed"))
    audit_targets = [i for i in (audit_issues or []) if is_blocking(i)]
    # v6.102：audit 判据必须是「**本轮新**解决」——`_audit_issue_resolved` 本身幂等，
    # 拿它当轮内功绩会让空转轮次靠陈旧计数通过验收。
    audit_resolved = sum(
        1 for i in audit_targets
        if _audit_issue_newly_resolved(script_after, script_before, i))
    has_targets = bool(before_keys or audit_targets)
    _non_target = list(non_target_changed or [])
    # v6.87：LLM 对锁定字段的写入与「非目标镜头被改动」是**两个不同的闸门**，
    # 分开命名——v6.83 起两者被并在一个列表里、统一报 `non_target_changed`，
    # 结果日志永远指不清是哪一道拦下的（三次运行都是这个 reason，无从定位）。
    _locked_by_llm = list(locked_by_llm_changed or [])
    accepted = (
        not checker_failed
        and not new_blocking
        and not locked_changed
        and not _non_target
        and not _locked_by_llm
        and (not has_targets or bool(resolved) or audit_resolved > 0)
    )
    reason = ""
    if checker_failed:
        reason = "checker_failed"
    elif new_blocking:
        reason = "new_blocking"
    elif locked_changed:
        reason = "locked_changed"
    elif _locked_by_llm:
        reason = "locked_field_changed"
    elif _non_target:
        reason = "non_target_changed"
    elif has_targets and not resolved and audit_resolved <= 0:
        reason = "no_target_resolved"
    return {
        "accepted": accepted,
        "reason": reason,
        "before": len(before_keys),
        "after": len(after_keys),
        "resolved": len(resolved),
        "new_blocking": len(new_blocking),
        "audit_targets": len(audit_targets),
        "audit_resolved": audit_resolved,
        "checker_failed": checker_failed,
        "locked_changed": bool(locked_changed),
        "non_target_changed": _non_target,
        "locked_by_llm_changed": _locked_by_llm,
    }


# v6.84（二次审核发现 3 实施 #4）：每轮定向修复的镜头数。
# 6 个完整镜头对象 + 上下文会顶到 8000 token 上限（本次实测 `finish_reason=length`
# 整份丢弃）；按 3 镜分批，靠**批数**而不是靠抬高 token 上限来解决上下文体积。
# 每轮名下的镜头数（v6.101：3 → 4）。配套把 `REPAIR_MAX_OUTPUT_TOKENS`
# 从 8000 抬到 16000——4 个完整镜头对象 + 上下文按实测约 5-7k token，
# 16k 有 2 倍余量；**不靠抬 token 掩盖上下文体积**这条原则仍然成立，
# 所以批量只加到 4，不是 6/8。
_REPAIR_MAX_SHOTS_PER_ROUND = 4


def _shot_field_diff(before: dict, after: dict, *, limit: int = 8) -> list:
    """两个脚本之间**语义字段**发生变化的 `镜头.字段` 列表（最多 `limit` 条）.

    v6.92：诊断细化的第二步。原先回退日志只说「哪些镜头变了」——
    2026-09-22 20:54 实测 `非目标镜头被改动 3 处: [12, 12.333, 18]`，要定位到底是
    哪个字段被动过只能重新复现一次运行（且未必复现）。这里直接给出字段名。

    只比语义白名单（`shared.quality_gate._SEMANTIC_SHOT_FIELDS`，与逐镜 revision
    同一份定义）——派生字段 `timecode` 不在其中，不会误报。
    """
    from shared.quality_gate import _SEMANTIC_SHOT_FIELDS

    def _by_key(script: dict) -> dict:
        out: dict = {}
        for s in (script.get("shots") or []):
            if isinstance(s, dict):
                out[str(s.get("index"))] = s
        if isinstance(script.get("hook"), dict):
            out["hook"] = script["hook"]
        return out

    b, a = _by_key(before), _by_key(after)
    out: list = []
    for k in sorted(set(b) | set(a), key=str):
        sb, sa = b.get(k), a.get(k)
        if not isinstance(sb, dict) or not isinstance(sa, dict):
            out.append(f"{k}:新增/删除")
            if len(out) >= limit:
                return out
            continue
        for f in _SEMANTIC_SHOT_FIELDS:
            if sb.get(f) != sa.get(f):
                out.append(f"{k}.{f}")
                if len(out) >= limit:
                    return out
    return out


def _snapshot_untargeted(script: dict, picked: set) -> dict:
    """快照**未被点名**的镜头（含 hook）→ `{位置: deepcopy}`（含 `"hook"` 键）.

    v6.94：键从 `shot_{index}` 改为**列表位置**。理由是 v6.84 在
    `shared/semantic_barrier` 上踩过的同一个坑：轮内可能发生索引重编号
    （`shot_index_renumber` 把半整数子镜归一成整数），按 index 为键会让还原
    落到错的镜头上（实测：`非目标镜头被改动 9 处` 命中的正是拆分子镜）。
    """
    import copy as _c
    out: dict = {}
    if "hook" not in picked and isinstance(script.get("hook"), dict):
        out["hook"] = _c.deepcopy(script["hook"])
    for pos, sh in enumerate(script.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        if f"shot_{sh.get('index')}" not in picked:
            out[pos] = _c.deepcopy(sh)
    return out


def _restore_untargeted(script: dict, snapshot: dict) -> int:
    """把快照**逐字节**写回原 dict（保持对象身份）；返回还原的镜头数.

    用 `clear()+update()` 而不是替换引用——脚本里可能有别处持有的同一 dict。
    """
    n = 0
    hook = script.get("hook")
    if "hook" in snapshot and isinstance(hook, dict):
        hook.clear()
        hook.update(snapshot["hook"])
        n += 1
    for pos, sh in enumerate(script.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        snap = snapshot.get(pos)
        if snap is not None:
            # 按位置还原文案，但**保留当前 index**：重编号（phase 1 的
            # `shot_index_renumber`）是受认可的归一化，还回去会造成 index 重复。
            keep_index = sh.get("index")
            sh.clear()
            sh.update(snap)
            if keep_index is not None:
                sh["index"] = keep_index
            n += 1
    return n


def _repair_shot_warnings(script: dict, novel_text: str,
                          bank=None, objects_bank=None,
                          model: str | None = None,
                          audit_issues: list[Issue] | None = None) -> dict:
    """定向修复轮（v6.35，P18 闭环核心）。

    GAP 通过后仍有高价值告警 → 只重写告警镜头的相关字段，重新校验，收敛即停。
    全脚本重生成会把没问题的镜头也重画一遍（白烧额度 + 引入新随机）；
    单镜修复只动有问题的地方，且每次轮次只挑「最值得修」的镜头（防 prompt 爆炸）。

    v6.43 08-25 HARD-only 策略：
    - 无 HARD 违例时（_has_hard=False）**整轮跳过**，节省 60s LLM 调用
    - WARN/soft-suggestion 全部由 hard_fix 兜底（runtime_extend / 12 个 constraint）
    - 实测：精简 prompt + hard_fix 增强后，HARD 违例基本为 0，**修复轮几乎不再触发**

    Returns:
        修复后的 script（任一轮失败/无告警则原样返回）。
    """
    # v6.87：**合并后立刻**抓的「LLM 写的锁定字段」快照（循环内赋值）。在这里
    # 预置，避免「某轮提交了替换却没走到赋值」时尾部读到未定义名——v6.83 的
    # `allowed_targets` 未定义名事故就是这个形态（被外层 except 吞掉、整段接受
    # 判定从未生效）。
    _mask_violations_after_patch: list = []

    # v6.94（2026-09-22 实测）：④ 的根因**不在这里**——是轮尾 `run_hard_fix` 跑到
    # `shot_index_renumber`（它没有 repair-loop 守卫），把结构收敛轮拆出的半整数
    # 子镜归一成整数，其后所有 index 平移；而 `_snapshot_untargeted` 与本轮点名的
    # `picked` / audit `shot_id` 都按 index 标识镜头 → 还原到错镜 + 找不到目标
    # → `非目标镜头被改动 9 处: [13, 13.333, 14, 14.25, 14.5]` 这种**假拒绝**。
    #
    # 修法在 `shot_index_renumber` 一侧（加 `ctx.is_in_repair_loop` 守卫，轮内不
    # 重编号），本函数不再需要「轮首先跑到幂等」那种绕法——实测它反而会把 fixture
    # 的 `index=7` 改成 `1`，导致 audit issue 的 `shot_id` 失配、修复轮提前退出。

    for _round in range(1, _REPAIR_MAX_ROUNDS + 1):
        # v6.101：轮数从 1 放到 3 之后，**每轮开始前**再查一次总预算。
        # 调用点（`main` 里 `REPAIR_ROUNDS_ENABLED and not _budget_exceeded()`）
        # 只保证「开始修的时候没超预算」，挡不住多轮叠加把单次 run 拖到 20 分钟
        # （第十四次重跑已经 12 分钟）。已完成的轮次保留，只是不再发下一批。
        if _round > 1 and _budget_exceeded():
            _logger.warn("[BUDGET] 总预算超时，停止后续定向修复轮（已通过的轮次保留）")
            return script
        per_shot_dict = _collect_repair_warnings(script, novel_text)
        import copy as _copy_before_accept
        _det_before_state = _copy_before_accept.deepcopy(per_shot_dict)
        # Merge same-model audit findings into the first repair round.  Audit is
        # auxiliary input; deterministic checks still decide final acceptance.
        if _round == 1 and audit_issues:
            audited = per_shot_dict.setdefault("shots", {})
            for _issue in audit_issues:
                if not is_blocking(_issue):
                    continue
                per_shot_dict["_has_hard"] = True
                _sid = _issue.shot_id
                _detail = (
                    f"[model_audit] code={_issue.code} frame={_issue.frame_id or '-'} "
                    f"entity={_issue.entity_id or '-'} field={_issue.field_path or '-'} "
                    f"evidence={_issue.evidence or '-'} :: {_issue.message}"
                )
                if str(_sid) in ("0", "hook"):
                    audited.setdefault("hook", []).append(_detail)
                elif _sid is not None:
                    audited.setdefault(f"shot_{_sid}", []).append(_detail)
        # v6.43 08-25：HARD-only 触发——没 hard violation 就不调 LLM
        if not per_shot_dict.get("_has_hard", False):
            if _round == 1:
                _logger.info(
                    "[REPAIR] 跳过：无 HARD 违例（HARD-only 策略），"
                    "WARN 已由 hard_fix 兜底"
                )
            return script
        per_shot = per_shot_dict.get("shots", {})
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
        # v6.101：**每一轮都按告警数取前 K**。
        #
        # 原实现只有第 1 轮限量，第 2 轮起 `picked = dict(shot_items)`（全部带告警的
        # 镜头）——08-15 的用意是「低告警镜头别永远轮不到修补」，但那条路径在
        # `_REPAIR_MAX_ROUNDS = 1` 下**从未执行过**。现在轮数放开到 3，若照原样
        # 第 2 轮会一次性发 10+ 个完整镜头对象，必然 `finish_reason=length`
        # 整份丢弃（v6.84 定分批的原始原因）。改为**每轮都限量**：
        # 按告警数排序取前 K，多轮自然覆盖「第 1 轮遗留 + 新增」。
        picked = dict(itertools.islice(
            sorted(shot_items.items(), key=lambda kv: -len(kv[1])),
            _REPAIR_MAX_SHOTS_PER_ROUND))
        if not picked:
            return script
        import copy as _copy_repair
        _round_before_script = _copy_repair.deepcopy(script)
        _locked_before = _locked_repair_signature(script)
        _locked_after_patch = _locked_before
        _det_before_count = sum(
            len(v) for k, v in per_shot_dict.get("shots", {}).items()
            if k.startswith("shot_") or k == "hook"
        )
        _audit_before_count = (
            sum(1 for _i in (audit_issues or []) if is_blocking(_i))
            if _round == 1 else 0
        )
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
        _repair_context = {
            "hook": shots_by_id.get("hook") if "hook" in picked else None,
            "shots": {tid: shots_by_id[tid] for tid in picked if tid in shots_by_id},
        }
        _current_script_json = _json.dumps(
            _repair_context, ensure_ascii=False, indent=1, default=str
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
        _audit_by_shot = {}
        for _ai in (audit_issues or []):
            _aid = "hook" if str(_ai.shot_id) in ("0", "hook") else f"shot_{_ai.shot_id}"
            _audit_by_shot.setdefault(_aid, []).append({
                "code": _ai.code,
                "frame_id": _ai.frame_id,
                "entity_id": _ai.entity_id,
                "field_path": _ai.field_path,
                "evidence": _ai.evidence,
                "message": _ai.message,
            })
        # v6.83（二次审核发现 2）：**按 issue 授权**——每个 target 只允许改它自己的
        # 问题点名的字段。允许集合由 deterministic warning + audit issue 推导
        # （`repair_fields.allowed_repair_fields`）；**推不出字段路径的目标不进本轮
        # 定向修复**（那类交整镜 regen），因为没有任何依据限制它可以改什么。
        from step01_script_gen.repair_fields import allowed_repair_fields
        _allowed_by_target: dict = {}
        _unauthorized_targets: list = []
        for _tid, _ws in picked.items():
            if _tid == "global":
                continue
            _issues_for_target = [
                _ai for _ai in (audit_issues or [])
                if ("hook" if str(_ai.shot_id) in ("0", "hook")
                    else f"shot_{_ai.shot_id}") == _tid
            ]
            _allowed = allowed_repair_fields(_ws, _issues_for_target)
            if not _allowed:
                _unauthorized_targets.append(_tid)
                continue
            _allowed_by_target[_tid] = _allowed
        if _unauthorized_targets:
            _logger.warn(
                "[REPAIR] %d 个目标的问题未点名字段路径，本轮不走定向修复"
                "（交整镜 regen）：%s" % (len(_unauthorized_targets),
                                          _unauthorized_targets[:6])
            )
        if not _allowed_by_target:
            _logger.warn("[REPAIR] 无任何目标具备明确字段授权，跳过本轮定向修复")
            return script

        _feedback_json = {
            "mode": "unified",
            "global_error_distribution": [
                {"label": l, "total": n} for l, n in _label_total.most_common()
            ],
            "shots": {},
        }
        for _tid, _ws in picked.items():
            if _tid == "global" or _tid not in _allowed_by_target:
                continue
            _s = shots_by_id.get(_tid) or {}
            _feedback_json["shots"][_tid] = {
                "audit_issues": _audit_by_shot.get(_tid, []),
                # **只允许改这些字段**；其余字段逐字照抄原镜
                "allowed_fields": sorted(_allowed_by_target[_tid]),
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
            "输出**被点名镜头的完整替换版本**（不是字段补丁）。格式："
            "{\"shot_replacements\":[{\"shot_id\":\"4\",\"shot\":{...完整镜头对象...}}],"
            "\"hook_replacement\":{\"shot_id\":\"hook\",\"shot\":{...}}}\n"
            "**hook 必须放在 `hook_replacement` 里**（它与普通镜头字段不同，无 scene/location），"
            "普通镜头放 `shot_replacements`；两个键都可为空数组/省略。\n"
            "shot 必须与首次生成**同构**——index/narration_segment/subtitle/prompt/motion_desc/"
            "ff_desc/lf_desc/duration/keyframes/frame_layouts 等**成套出现**，缺一即被拒。\n"
            "**未点名的镜头绝不允许出现在 shot_replacements 里**；每个被点名镜头**恰好出现一次**"
            "（重复、遗漏、多出都会让整批回滚）。\n"
            "`shot_id` 用**裸镜号**（例如 \"10\"，**不是** \"shot_10\"；"
            "REPAIR FEEDBACK 里的 `shot_10` 只是分组键）。\n"
            "**字段授权（硬约束）**：REPAIR FEEDBACK 里每个镜头的 `allowed_fields` 列出该镜"
            "**本轮允许修改的字段路径**；未列入的字段必须**逐字照抄原镜**，"
            "改一个字节都会让整批回滚（motion_desc / temporal_plan / keyframes / duration "
            "默认不在授权内，除非问题明确点名）。\n"
            "结构字段必须彼此自洽：keyframes 与 frame_layouts.kfs 等长；"
            "kf.t_sec 落在 (0, duration] 内；stage 取 pre/approach/climax/react/exit/after。\n\n"
            "**手术刀原则（最高优先级）**：\n"
            "1. 一条 warn 只改**对应字段的值**；镜头内其余字段**逐字保留**——"
            "输出完整镜头不等于重写整镜\n"
            "2. 禁止新增任何短语（动作/情绪/运镜/光位/转场/视角/装饰性方位），"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但**禁止写成「望着她」「看杨旭」这类无空间方位词**\n"
            "3. 禁止改景别/时长/台词/剧情/narration_segment 文本\n"
            "4. 禁止增删镜头、改 shot.index\n"
            "5. 只输出 shot_replacements，不输出整本脚本；未列问题的镜头绝不进入；"
            "替换镜头内**未被 warn 点到的字段逐字保留**（可照抄原镜文本）\n"
            "patch 未覆盖的字段**逐字保留**\n"
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
            "  - index（结构性字段，动了会改镜头排序）\n"
            "  - duration_sec（由时间轴序列化函数从 duration 统一派生；**需要改时长时改 duration**，且仅限本轮 warn 明确授权的镜头）\n"
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
        # v6.68: prompt/system 跟着实际模型走（修复 prompt 本身 provider 中立）
        _rep_builder, _rep_system = resolve_model_prompt_profile(model)
        prompt = _rep_builder(novel_text, bank, objects_bank)

        # 08-21 mode 合并：单一 unified fallback（与 _repair_turn 同源，压缩为单行 + 决策树/正反例/跨镜一致性）
        prompt = prompt + (
            "\n\n# 定向修复\n"
            "以下是分镜问题清单。请**逐条**修复，每条 warn 改一处字段即可。"
            "输出**被点名镜头的完整替换版本**。格式："
            "{\"shot_replacements\":[{\"shot_id\":\"4\",\"shot\":{...与首次生成同构的完整镜头...}}],"
            "\"hook_replacement\":{\"shot_id\":\"hook\",\"shot\":{...}}}\n"
            "hook 放 hook_replacement，普通镜头放 shot_replacements，两者都可为空；"
            "shot_id 用裸镜号（\"10\"，不是 \"shot_10\"）；"
            "每个被点名镜头恰好出现一次（重复/遗漏/多出则整批回滚）。"
            "未点名镜头绝不出现；镜头内**未被 warn 授权的字段逐字保留**"
            "（见 REPAIR FEEDBACK 的 allowed_fields）。"
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

        _metrics_inc("calls.repair_round")
        service = create_service()
        from _text_gen.deepseek_schema import REPAIR_SHOT_REPLACEMENT_SCHEMA
        req = TextGenerationRequest(
            system_prompt=_rep_system,
            user_prompt=prompt,
            user_inputs=_user_inputs,  # v6.37 结构化多轮
            # v6.101：8000 → 16000（每批 4 个完整镜头对象 + 上下文）。
            # agent_plan 的全局上限是 32768，16k 不触顶，留了 2 倍余量。
            max_tokens=int(os.getenv("REPAIR_MAX_OUTPUT_TOKENS", "16000") or "16000"),
            temperature=0.1,
            json_schema=REPAIR_SHOT_REPLACEMENT_SCHEMA,
            model=model,  # v6.67 接力修复：None=provider 默认（零行为变化）
            timeout_sec=_repair_timeout_sec(),  # v6.70.3: 修复轮 180s——超时保留原版，不再盲等 600s
        )
        # v6.84（二次审核发现 3）：模型调用失败/截断**立即返回**，不进提交尾部。
        #
        # 原实现用 `break` 跳出循环，然后仍会执行循环后的全局 `post_fix_shot_prompts()`
        # 与 `run_hard_fix()`——模型根本没产出任何合法 replacement，流程却对整本脚本
        # 做后处理，再把自己造成的非目标镜头变化判成 `non_target_changed` 整轮回滚。
        # 本次实测就是这个形态：8000 token 截断 → JSON 丢弃 → 仍跑全局后处理 →
        # 未点名镜头 7 被改 → `resolved=0` 回滚。
        try:
            result = service.generate(req)
        except Exception as e:
            _logger.warn(f"[WARN] 定向修复失败（保留原版，未做任何后处理）: {e}")
            return script
        if not result.success:
            _logger.warn(f"[WARN] 定向修复失败（保留原版，未做任何后处理）: {result.error}")
            return script

        import re as _re, json as _patch_json
        raw = result.text.strip()
        raw = _re.sub(r"^```json\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"^```\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"```\s*$", "", raw, flags=_re.MULTILINE)
        try:
            _patch_data = _patch_json.loads(raw)
        except Exception as e:
            _logger.warn(f"[WARN] 定向修复补丁 JSON 解析失败（保留原版，未做任何后处理）: {e}")
            return script
        # v6.77（第四阶段）：修复轮收**完整镜头替换**，不再是字段级补丁。
        # 结构字段成套出现由 schema（复用 _SHOT）保证；这里再做一遍语义校验，
        # 任一镜不通过则**整条丢弃**（不做部分应用），然后原子替换。
        # v6.83（二次审核发现 3）：envelope 的两个数组**都允许为空**。
        # 只点名 hook 的一轮，符合新 schema 的自然响应是
        # `{"shot_replacements": [], "hook_replacement": {...}}`；旧实现要求
        # `shot_replacements` 非空，在调用 applier **之前**就把它拒掉——hook-only
        # 修复因此在主调用路径上不可达（只有直接调 helper 才走得通）。
        _repls = _patch_data.get("shot_replacements") if isinstance(_patch_data, dict) else None
        _hook_repl = _patch_data.get("hook_replacement") if isinstance(_patch_data, dict) else None
        if _repls is None:
            _repls = []
        if not isinstance(_repls, list):
            _logger.warn("[WARN] 定向修复 shot_replacements 不是数组，保留原版"
                         "（未做任何后处理）")
            return script
        if not _repls and not isinstance(_hook_repl, dict):
            _logger.warn("[WARN] 定向修复未返回任何替换镜头"
                         "（shot_replacements 与 hook_replacement 皆空），保留原版"
                         "（未做任何后处理）")
            return script
        # v6.80（发现 9 #1/#2）：hook 走独立的 `_HOOK` 承载；整批校验通过才提交。
        # v6.83（发现 4）：`expected_targets` 要求每个 picked 目标**恰好出现一次**——
        # 「全有或全无」必须覆盖「本轮请求的事务」，而不只是「返回的条目」。
        # `picked` 的键是 `shot_<id>` / `hook`；envelope 里的 `shot_id` 是裸 id。
        # 目标集合比较必须用同一口径，否则每个 picked 目标都会被判「缺失」。
        _expected_ids = {str(t)[5:] if str(t).startswith("shot_") else str(t)
                         for t in picked}
        merged, _n_applied, _rejected = _apply_shot_replacements(
            script, _repls, set(picked), hook_replacement=_hook_repl,
            allowed_by_target=_allowed_by_target,
            expected_targets=_expected_ids)
        if _rejected:
            # v6.87：`_rejected` 里既可能是真被拒的条目，也可能是「某目标未返回替换
            # （其余已提交）」这类**信息性**回报（A1 逐目标提交后新出现）。分开打。
            _info_only = [r for r in _rejected if "本轮未返回替换" in r]
            _hard_rej = [r for r in _rejected if "本轮未返回替换" not in r]
            if _hard_rej:
                _logger.info(f"[REPAIR] 替换被拒 {len(_hard_rej)} 条: {_hard_rej[:3]}")
            if _info_only:
                _logger.info(f"[REPAIR] 部分提交说明: {_info_only[:2]}")
        if _n_applied <= 0:
            _logger.warn("[WARN] 定向修复替换镜头均未通过校验，保留原版（未做任何后处理）")
            return script
        _logger.info(f"[REPAIR] 应用完整镜头替换 {_n_applied} 镜")
        # v6.87（2026-09-21 23:19 实测）：**锁定字段权威快照必须在合并后立刻取**。
        #
        # `_FINAL_LOCKED_FIELDS` 要回答的问题是「**LLM** 有没有改动锁定字段」。
        # 但在事务末尾才与「本轮开始前」比较时，中间隔着 `post_fix_shot_prompts` /
        # `run_hard_fix` / `write_timeline_from_durations` 这一串**管线自己的确定性
        # 归一化**——它们对被点名镜头的写入会被算成 LLM 越权。
        #
        # 实测（本轮 `resolved=11` 却整轮回退，reason=non_target_changed）：B.1
        # `narration_subtitle_consistency` 把被点名镜头 subtitle 末尾多余的「。」去掉了，
        # 而 `subtitle` 在锁定字段表里 → `_final_field_mask_violations` 报违例 →
        # 11 条已解决的阻塞一起丢。**这不是 LLM 越权，是管线在收拾 LLM 的稿子。**
        #
        # 正确基线 = 合并后、后处理前的状态：它只含 LLM 的改动。LLM 若真动了锁定
        # 字段，这里立刻就能看见（提交前另有 `check_replacement_authorized` 拦截）。
        # v6.98（2026-09-22 审核 P2-1 复核确认）：比较对象必须是 **merged**。
        #
        # 原实现先算再赋值，而此刻 `script` 仍是**旧稿**（该区间唯一的 `script =`
        # 就在下面一行）——于是这里比的是「旧稿 vs 旧稿」，`_mask_violations_after_patch`
        # **恒为空**：LLM 真的改了锁定字段也看不见（`locked_by_llm_changed` 成死代码）。
        # 实测确认：该区间内 `script` 未被就地改写，唯一赋值点即 `script = merged`。
        # 现在改成对 `merged` 比较——它正是「合并后、后处理前」，只含 LLM 的写入。
        _mask_violations_after_patch = _final_field_mask_violations(
            _round_before_script, merged, set(picked),
            allowed_by_target=_allowed_by_target)
        script = merged
        # v6.95（①，2026-09-22 实测）：**锁定字段基线必须在合并后立刻取**。
        #
        # 原实现在**尾部**（归一化与还原之后）才赋值 `_locked_after_patch`，于是它
        # 比的是「本轮前 vs 全流程之后」，把**管线自己的确定性清洗**算成 LLM 越权——
        # 实测第十次：拆分子镜台词末尾的「，」被归一化清掉 → `locked_changed` → 整轮
        # 假拒绝（`new_blocking=0`、`非目标镜头被改动`=0、`resolved=5` 全被这一个闸门否掉）。
        # 这与 v6.87 修 mask 基线是**同一原则**：闸门只管 **LLM 的**写入。
        _locked_after_patch = _locked_repair_signature(script)
        # 修复后需重新算时间轴/注入全局渲染参数
        # 08-15 修复：正确函数在 style_prefix（_post_process:186 同款），原 import 自
        # shared.vimax_bridge 不存在 → 修复轮合并后 ImportError 崩溃，修复结果从未落地
        merged = style_prefix.inject_global_config_into_script(merged)
        # v6.79（09-20）：修复轮只**序列化** timeline，不再调 `_compute_timecodes`。
        #
        # 原实现用 `_compute_timecodes(merged, allow_split=False)`，注释称它「只重建
        # timeline」——但 `allow_split=False` **只关闭了拆镜 pre-pass**，后续完整估时
        # 逻辑照跑，仍会在 `duration_sec` 上覆盖每个镜头（实测：输入 `duration_sec=4`、
        # `motion_desc="0-6秒：动作"` 的镜头被改成 6 秒）。也就是说修复轮依旧带着一个
        # 「重估时长」的副作用，而 `duration_sec` 是 Mask 字段。
        #
        # `write_timeline_from_durations()` 是纯序列化（不重估、不拆镜、不增删镜头），
        # 正是修复轮需要的唯一职责。
        # 见 docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 3。
        try:
            write_timeline_from_durations(merged)
        except Exception as _tl_e:
            _logger.warn(f"[WARN] 修复轮 timeline 序列化失败（保留其自身时间轴）: {_tl_e}")
        script = merged
        _logger.info(f"[REPAIR] 第 {_round} 轮定向修复完成，重新校验...")
    # 08-15：修复轮合并后强制再跑一遍 post_fix_shot_prompts（幂等）——merge 保留的
    # 旧镜头可能带着改版前就有的机械残留（如「迅速@lf」，parse_response 只修过
    # prompt/motion），确定性清掉；也防修复 LLM 引入新的机械噪音。下一轮
    # _collect_repair_warnings 校验的就是已 post_fix 的文本，少报一条废告警。
    # v6.84（二次审核发现 3 实施 #3）：事务内的确定性后处理**只作用于被点名镜头**。
    #
    # 全局 `post_fix_shot_prompts` / `run_hard_fix` 会改没被点名的镜头（跨镜约束尤其
    # 如此），于是「LLM 合法修复 + 后处理顺手动了别镜」被 `non_target_changed` 判为
    # 违规、整轮回滚，`resolved=0`——合法修复白做。这里先把未点名镜头逐字节快照，
    # 后处理跑完再还原：后处理只负责收拾本轮改动可能引入的问题。
        _frozen = _snapshot_untargeted(script, set(picked))
        from step01_script_gen.validation import post_fix_shot_prompts as _pfix
        try:
            script = _pfix(script)
        except Exception as _pfx:
            _logger.warn(f"[WARN] 修复后 post_fix 失败（保留原版）: {_pfx}")
        # 08-19：C 模式默认后，修复轮 LLM 可能删掉致命伤锚定（A→C 实测 2 个镜头"
        # 心口暗红洇痕"被删，违反规则 8 最高优先级）。修复完成后跑 hard_fix 致命伤
        # 兜底 + kf 时段对齐，把 LLM 弄丢的确定性补回来（幂等，不伤已有正确内容）。
        # v6.42：改用新 Constraint 框架；HARD_FIX_USE_OLD 回退开关已删除（2026-09-12）
        from step01_script_gen.constraints import run_hard_fix as _hfix
        try:
            script = _hfix(script, is_in_repair_loop=True, is_final_pass=False, novel_text=novel_text)
        except Exception as _hfx:
            _logger.warn(f"[WARN] 修复后 hard_fix 兜底失败（保留原版）: {_hfx}")
        # 08-19：hard_fix_injury_continuity 按 location 精确匹配 + victim 提取不准，
        # 会漏补"杨旭在船上仰卧但镜头 location 写岸边"这类情况。再加一道轻量兜底：
        # 扫所有镜头，**刺杀发生后**（build_idx 之后）的镜头里，ff_desc/lf_desc/prompt
        # 有"杨旭仰卧/倒下/躺"但没 anchor 词 → prompt 末尾追加伤处锚。
        # 只在修复轮后调用，范围小。用 build_idx 作分界避免把"夜宴仰卧喝酒"也补成带伤。
        _injury_lay_words = ("仰卧", "倒下", "仰倒", "躺于", "躺倒", "倒地", "瘫倒")
        # v6.50（08-29）：build 判定改用 STRONG 词表——"白袍"等服装词会让
        # 刺杀前镜头（开篇白袍吹箫）被当致命伤建立点
        from step01_script_gen.prompt_semantic.registry import STRONG_INJURY_ANCHORS as _anchor_words
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

        # v6.84：还原未点名镜头（`_frozen` 快照）——后处理不得改动事务外的镜头。
        _restored_n = _restore_untargeted(script, _frozen)
        if _restored_n:
            _logger.info(f"[REPAIR] 事务内后处理已限定范围：还原 {_restored_n} 个未点名镜头")

        _round_ok = False   # v6.101：本轮事务是否被接受
        _no_gain = False    # v6.102：本轮是否「通过验收但零改善」
        # Acceptance is identity-based, not message-count based.  The same
        # structured collector runs before and after; audit-only findings are
        # verified by evidence disappearance.  Any checker failure or new blocking
        # identity rolls the round back.
        try:
            _after_state = _collect_repair_warnings(script, novel_text)
            # v6.79（09-20）：Mask 字段签名在**全部后处理结束后**才取。
            #
            # 原先在 `_apply_shot_replacements` 之后立刻取值，比较的是「LLM patch 直接
            # 造成的变化」；随后跑的 timeline 序列化 / post_fix_shot_prompts / hard_fix
            # 对 `duration_sec`、`narration_segment`、`subtitle` 等 Mask 字段的副作用
            # **不进比较**，于是它们随整轮一起被接受——「duration_sec 是 Mask 字段」
            # 这条 prompt 契约在运行时并不成立（审核发现 3）。
            #
            # Mask 字段清单与修复 prompt 里给 LLM 的清单同源
            # （narration_segment / index / duration_sec / subtitle / variation_type /
            #  scene / location），见 `_locked_repair_signature`。未点名镜头的变化另由
            # 下面的逐镜 revision gate 兜住。
            # v6.95：`_locked_after_patch` 已在**合并后立刻**取（见循环内），这里不再重取
            # ——重取会把归一化/还原的写入算进来，正是上面那个假拒绝的成因。
            _locked_changed = _locked_before != _locked_after_patch
            # v6.77（第四阶段）：修复轮是「镜头事务」——只许动被点名的镜头。
            # 用逐镜语义 revision 快照判定：不在 picked 里的镜头若发生任何语义变化，
            # 本轮即拒绝（原判据只看聚合的 blocking 身份，看不见"顺手动别镜"）。
            from shared.quality_gate import per_shot_revisions, changed_shot_keys
            _picked_keys = {str(_p)[5:] for _p in picked if str(_p).startswith("shot_")}
            if "hook" in picked:
                _picked_keys.add("hook")
            _non_target = [
                _k for _k in changed_shot_keys(
                    per_shot_revisions(_round_before_script), per_shot_revisions(script))
                if str(_k) not in _picked_keys
            ]
            # v6.80（发现 9 #4）想抓的是「后处理对锁定字段的写入逃过比较」。
            # v6.87 修正了它的方向：后处理是**管线自己的确定性归一化**，不算越权；
            # 真正要抓的是 **LLM** 的写入，而那个基线只能在合并后立刻取
            # （`_mask_violations_after_patch`，见上）。两者分开命名，日志才指得清。
            _locked_by_llm = list(_mask_violations_after_patch or [])
            # v6.83：`allowed_targets` 此前是**未定义名**（HEAD 里传的是全部
            # `audit_issues`，工作树改成这个名字时漏了定义）——整段接受判定被外层
            # `except` 吞掉并**回退整轮修复**，于是 v6.79～v6.82 加的
            # non-target gate / 最终字段 mask 在生产里从未真正跑过。
            # 这里按名字的本义补齐：只把**本轮点名目标**的 audit issue 当作可解目标
            # ——非点名镜头的问题本轮修不了，把它们算进 `has_targets` 只会让
            # `no_target_resolved` 误判。
            _allowed_targets = [
                _ai for _ai in (audit_issues or [])
                if ("hook" if str(_ai.shot_id) in ("0", "hook")
                    else f"shot_{_ai.shot_id}") in picked
            ]
            _decision = _repair_acceptance_decision(
                _det_before_state, _after_state, _allowed_targets, script, _locked_changed,
                non_target_changed=_non_target,
                locked_by_llm_changed=_locked_by_llm,
                script_before=_round_before_script)
            _logger.info(
                "[REPAIR] 接受条件: "
                f"before={_decision['before']}+audit{_decision['audit_targets']} "
                f"after={_decision['after']} resolved={_decision['resolved']} "
                f"audit_resolved={_decision['audit_resolved']} "
                f"new={_decision['new_blocking']}"
            )
            _metrics_inc("repair.decisions")
            _metrics_update("repair.last_decision", _decision)
            if _decision["accepted"]:
                _metrics_inc("repair.accepted")
                _round_ok = True
            else:
                _metrics_inc("repair.rejected")
            # v6.102（第十六次修复项 #1）：**通过验收但零改善**也是空转。
            #
            # 实测（第十五次）：第 2/3 轮的 `before/after/resolved` **完全相同**
            # （`resolved=0`），却仍被判「接受」——靠的是 `audit_resolved=2`，
            # 而这个数**第 2 轮起是陈旧的**：调用点把 `_audit_before_count` 写成
            # `if _round == 1 else 0`，而 audit 判据问的是「evidence 是否还在稿子里」，
            # 第 1 轮清掉后它**永远为真**。于是同一批 4 镜被反复改写两次、
            # 白烧 2 次 LLM 调用，没有任何可测量的收益。
            #
            # 判据：被接受 **且** `resolved == 0` **且** `audit_resolved == 0`
            # → 等价于空操作：回退它的写入并结束循环（下一批还是同一批目标）。
            _no_gain = (bool(_decision.get("accepted"))
                        and not int(_decision.get("resolved") or 0)
                        and not int(_decision.get("audit_resolved") or 0))
            if not _decision["accepted"]:
                # v6.93 修（自查发现的 bug）：诊断必须在**回退之前**抓快照。
                #
                # 原实现把 `_shot_field_diff(_round_before_script, script)` 放在
                # `script = _round_before_script` **之后**——那时 `script` 已经等于基线，
                # 于是永远得到空 diff（2026-09-22 21:31 实测：`非目标镜头被改动 9 处`
                # 后面没有 `变化字段:` 行，就是这么来的）。诊断自己失效 = 白加。
                _script_at_reject = script
                script = _round_before_script
                # v6.87：**必须打出到底什么变了**——`non_target_changed` 连续三次成为
                # 回退原因，而日志从不说明是哪个镜头/哪个字段，只能靠事后复现才能定位。
                if _decision.get("non_target_changed"):
                    _logger.warn(
                        f"[REPAIR] 非目标镜头被改动 {len(_decision['non_target_changed'])} 处: "
                        f"{_decision['non_target_changed'][:5]}"
                    )
                    # 字段级定位（v6.92）：否则只能靠重新复现一次运行来猜是哪个字段
                    try:
                        _fld = _shot_field_diff(_round_before_script, _script_at_reject)
                    except Exception:
                        _fld = []
                    if _fld:
                        _logger.warn(f"[REPAIR] 变化字段: {_fld}")
                    else:
                        # 自相矛盾要**说出来**，不能静默：`changed_shot_keys` 说这些镜头变了，
                        # 而语义字段逐个比对却无差异 → 说明两者口径不一致（或诊断调用点写错）。
                        # 这是把「诊断失效」本身变成可见信号——v6.93 就吃过一次静默失效。
                        _logger.warn(
                            "[REPAIR] ⚠️ 非目标改动不在语义字段内（诊断与逐镜 revision 口径不一致，"
                            "请查 `_shot_field_diff` 的调用点是否在回退之后）"
                        )
                if _decision.get("locked_changed"):
                    # v6.94：字段级定位——`locked_changed` 只有布尔，从不说变了什么
                    try:
                        _lsd = _locked_signature_diff(_locked_before, _locked_after_patch)
                    except Exception:
                        _lsd = []
                    if _lsd:
                        _logger.warn("[REPAIR] 锁定字段变化: %s" % _lsd)
                if _decision.get("locked_by_llm_changed"):
                    _logger.warn(
                        f"[REPAIR] LLM 改动了锁定字段 "
                        f"{len(_decision['locked_by_llm_changed'])} 处: "
                        f"{_decision['locked_by_llm_changed'][:5]}"
                    )
                _logger.warn(
                    "[REPAIR] 修复未满足接受条件"
                    f"（reason={_decision['reason']}, resolved={_decision['resolved']}, "
                    f"audit_resolved={_decision['audit_resolved']}, "
                    f"new={_decision['new_blocking']}, "
                    f"checker_failed={_decision['checker_failed']}, "
                    f"locked_changed={_decision['locked_changed']}），回退本轮修复"
                )
        except Exception as _acc_err:
            script = _round_before_script
            _metrics_inc("repair.check_failed")
            _metrics_set("repair.last_error", str(_acc_err))
            _logger.warn(f"[REPAIR] 接受条件检查失败，回退本轮修复: {_acc_err}")
        if not _round_ok:
            # 本轮已被回退 → 停止（本轮失败，再发下一批没有意义）
            return script
        if _no_gain:
            # v6.102：通过验收但零改善 → 回退本轮（不留无收益的改写）并停止后续轮次
            script = _round_before_script
            _metrics_inc("repair.no_gain_rolled_back")
            _logger.warn(
                "[REPAIR] 本轮无任何可测量改善（resolved=0 / audit_resolved=0）→ "
                "回退本轮并停止后续轮次（继续发只会重复同一批目标）"
            )
            return script
        _metrics_inc("repair.rounds_accepted")
        # 本轮通过验收 → 继续下一批（v6.101：每轮一个独立事务）

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
    # -- 场景/道具资产提取（v6.43 08-25 改为并发跑）--
    scene_assets_bank = SceneAssetsBank()
    scene_assets_bank.load()
    prop_assets_bank = PropAssetsBank()
    prop_assets_bank.load()

    _scene_assets: list = []
    _prop_assets: list = []
    if SCENE_ASSET_EXTRACTION_ENABLED or PROP_ASSET_EXTRACTION_ENABLED:
        _logger.info("[INFO] 并发提取 场景资产/道具资产（2 路并发）...")
        from step01_script_gen.concurrent_extract import extract_assets_concurrent
        _asset_results = extract_assets_concurrent(
            script,
            scene_assets_bank,
            prop_assets_bank,
            enable_scene_assets=SCENE_ASSET_EXTRACTION_ENABLED,
            enable_props=PROP_ASSET_EXTRACTION_ENABLED,
            log_func=_logger.info,
        )

        if _asset_results.get("scene_assets") is not None:
            _scene_assets = _asset_results["scene_assets"]
            for _sa in _scene_assets:
                scene_assets_bank.register_scene(_sa, NOVEL_CHAPTER)
            scene_assets_bank.save()
            _logger.info(f"[INFO] 已提取 {len(_scene_assets)} 个场景资产（已保存到场景资产库）")

        if _asset_results.get("prop_assets") is not None:
            _prop_assets = _asset_results["prop_assets"]
            for _pa in _prop_assets:
                prop_assets_bank.register_prop(_pa, NOVEL_CHAPTER)
            prop_assets_bank.save()
            _logger.info(f"[INFO] 已提取 {len(_prop_assets)} 个道具资产（已保存到道具资产库）")

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
    # 场景别名 canonical merge：同一物理场景的细分命名只保留一个主场景，
    # 避免 15 个 scene asset 造成 step02a 重复生图和 shot_count_bounds 假超限。
    _scene_alias_map = {}
    _scene_canonical = {}
    for _scene_name in sorted(_scene_assets_out, key=lambda x: (len(x), x)):
        _base = next(
            (_b for _b in sorted(_scene_canonical, key=len, reverse=True)
             if len(_b) >= 2 and _scene_name.startswith(_b)),
            None,
        )
        if _base is None:
            _scene_canonical[_scene_name] = dict(_scene_assets_out[_scene_name])
            _scene_alias_map[_scene_name] = _scene_name
            continue
        _dst = _scene_canonical[_base]
        _src = _scene_assets_out[_scene_name]
        for _k, _v in _src.items():
            if _k == "consistent_terms":
                _merged = list(dict.fromkeys(list(_dst.get(_k) or []) + list(_v or [])))
                if _merged:
                    _dst[_k] = _merged
            elif not _dst.get(_k) and _v:
                _dst[_k] = _v
        _scene_alias_map[_scene_name] = _base
    if len(_scene_canonical) < len(_scene_assets_out):
        _scene_assets_out = _scene_canonical
        for _s in list(script.get("shots") or []) + ([script.get("hook")] if isinstance(script.get("hook"), dict) else []):
            if isinstance(_s, dict) and _s.get("scene") in _scene_alias_map:
                _s["scene"] = _scene_alias_map[_s["scene"]]
        _logger.info(f"[ASSET] 场景别名合并：{len(_scene_alias_map)} → {len(_scene_canonical)}")
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
    # v6.62.1（09-06）：分镜未引用的道具过滤——本次 LLM 提的道具若在任何分镜
    # 文本（prompt/ff/lf/motion/kf/main_subject）都无命中（标准名/别名都不出现），
    # step02a 仍会为它生成白底资产图（烧额度），step02b r2v 也永不引用。
    # 「马车」09-06 实测：原文有、本章分镜没拍到 → 6 道具里白生成 1 张。
    # 用 consistent_terms（含别名）做覆盖判定，命中任一分镜文本才保留。
    _shot_text_pool = []
    for _s in script.get("shots", []) + ([script.get("hook")] if isinstance(script.get("hook"), dict) else []):
        if not isinstance(_s, dict):
            continue
        _parts = [str(_s.get(f) or "") for f in
                  ("prompt", "ff_desc", "lf_desc", "motion_desc", "main_subject")]
        for _kf in _s.get("keyframes") or []:
            if isinstance(_kf, dict):
                _parts.append(str(_kf.get("desc") or ""))
        _shot_text_pool.append(" ".join(_parts))
    _unused = []
    _PROP_ACTION_VERBS = ("持", "握", "提", "拿", "举", "执", "放", "横放", "挂",
                          "撑", "牵", "推", "掷", "抛", "拿好", "托", "抱", "挥")
    for _pid in list(_prop_assets_out):
        _terms = [str(t) for t in (_prop_assets_out[_pid].get("consistent_terms") or [])]
        _probe_terms = [str(_pid), *_terms]
        # 单字别名（车/箫/伞）在散文里歧义命中率高（09-06 实测「车轮大的红日」
        # 的「车」把未出镜的「马车」误判已引用），但一刀切剔除又把真用的
        # 「箫」（双手持箫/箫横放膝头）误删。单字 probe 收紧为
        # 「动词+单字」形态（持箫/推车/撑伞）才算引用；≥2 字 probe 子串照旧。
        _long_terms = [t for t in _probe_terms if len(t) >= 2]
        _short_terms = [t for t in _probe_terms if len(t) == 1]
        _short_patterns = [f"{v}{t}" for t in _short_terms for v in _PROP_ACTION_VERBS]

        def _hit(text: str) -> bool:
            if any(t in text for t in _long_terms):
                return True
            return any(p in text for p in _short_patterns)

        _referenced = any(_hit(text) for text in _shot_text_pool if text)
        if not _referenced:
            _unused.append(_pid)
            _prop_assets_out.pop(_pid)
    if _unused:
        _logger.info(
            f"[INFO] 分镜未引用的道具已从 prop_assets 剔除（不生成资产图）: {sorted(_unused)}"
        )
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


def _snapshot_run_artifacts(script: dict | None = None) -> None:
    """跑批快照（v6.50 P3，08-28）：script + step01.log 时间戳备份到 snapshots/.

    output 不进 git（P2 教训：想回滚 LLM 原始输出时无 .bak 无 raw 缓存），
    每次落盘后快照一份，只保留最近 N 份（N=SNAPSHOTS_KEEP env，默认 20，
    单源 shared/paths.MAX_SNAPSHOTS_PER_CHAPTER；按 script_result_*.json 计，
    配对删除同名 step01_*.log）。快照失败只告警，不影响主流程。
    """
    try:
        import shutil
        import time as _time
        snap_dir = OUTPUT_FILE.parent / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = _time.strftime("%Y%m%d_%H%M%S")
        if script is not None:
            from shared.json_io import atomic_dump_json as _adj
            _adj(snap_dir / f"script_result_{ts}.json", script,
                 ensure_ascii=False, indent=2)
        log_file = OUTPUT_FILE.parent / "step01.log"
        if log_file.exists():
            shutil.copy2(log_file, snap_dir / f"step01_{ts}.log")
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        # 轮转上限单源 shared/paths.MAX_SNAPSHOTS_PER_CHAPTER（env SNAPSHOTS_KEEP，默认 20）
        from shared.paths import MAX_SNAPSHOTS_PER_CHAPTER
        for old in snaps[:-MAX_SNAPSHOTS_PER_CHAPTER]:
            old.unlink(missing_ok=True)
            log_pair = old.name.replace("script_result_", "step01_").replace(".json", ".log")
            (snap_dir / log_pair).unlink(missing_ok=True)
    except Exception as _snap_e:
        try:
            _logger.warn(f"[SNAPSHOT] 快照失败（不影响主流程）: {_snap_e}")
        except Exception:
            pass


def _pid_is_alive(pid: int) -> bool:
    """安全查询 PID 是否存活（Windows 不使用 os.kill(pid, 0)）.

    Windows 的 os.kill(pid, 0) 会调用 TerminateProcess，而不是 POSIX 的
    「只探测不发送信号」。因此 Windows 必须走 OpenProcess；无权查询时保守
    当作存活，避免误删其他进程持有的 chapter 锁。
    """
    if pid <= 0:
        return False
    import os as _os
    if _os.name != "nt":
        try:
            _os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    import ctypes
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _process_query_limited_information = 0x1000
    _synchronize = 0x00100000
    _handle = _kernel32.OpenProcess(
        _process_query_limited_information | _synchronize,
        False,
        pid,
    )
    if _handle:
        _kernel32.CloseHandle(_handle)
        return True
    # ERROR_ACCESS_DENIED：进程存在但当前用户无权打开。
    return ctypes.get_last_error() == 5


def _acquire_step01_lock() -> bool:
    """章节级 single-flight 锁：禁止两个 step01 并发写同一章.

    O_EXCL 创建 .step01.lock；创建者 PID 已退出才允许接管 stale 锁。
    """
    import os as _os
    import time as _time
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CHAPTER_DIR / ".step01.lock"
    try:
        fd = _os.open(lock_path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL)
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": _os.getpid(), "started_at": _time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        return True
    except FileExistsError:
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                info = json.load(f)
            pid = int(info.get("pid", 0))
        except Exception:
            pid = 0
        if pid <= 0:
            try:
                lock_path.unlink()
            except OSError:
                pass
            return _acquire_step01_lock()
        if _pid_is_alive(pid):
            _logger.warn(f"[LOCK] 已有 step01 运行中（PID={pid}），本次跳过")
            _console(f"[LOCK] 已有 step01 运行中（PID={pid}），本次跳过")
            return False
        try:
            lock_path.unlink()
        except OSError:
            pass
        return _acquire_step01_lock()


def _release_step01_lock() -> None:
    """仅删除本进程创建的锁，避免释放他人锁."""
    import os as _os
    lock_path = CHAPTER_DIR / ".step01.lock"
    try:
        if not lock_path.exists():
            return
        with open(lock_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        if int(info.get("pid", 0)) == _os.getpid():
            lock_path.unlink()
    except Exception as _e:
        _logger.warn(f"[LOCK] 释放 step01 锁失败: {_e}")


def _candidate_early_stop_enabled() -> bool:
    """候选早停开关，默认开启；=0 时保持 v6.65「凑满 TOPK」旧行为."""
    import os as _os
    return _os.getenv("CANDIDATE_EARLY_STOP", "1").strip() != "0"


def _candidate_gen_timeout_sec() -> int | None:
    """候选生成请求级超时（v6.70.3，默认 300s；env 设 0 = 回退 provider 默认 600s）.

    09-14 实测：候选 3 耗 12 分钟把「100 分候选 64s 到手」的 run 拖成 17m38s；
    超时判 success=False → 走既有「失败重试/凑不够就用手头候选」路径，不再盲等。
    """
    import os as _os
    try:
        _v = int(_os.getenv("CANDIDATE_GEN_TIMEOUT_SEC", "300"))
    except ValueError:
        _v = 300
    return _v if _v > 0 else None


def _full_script_timeout_sec() -> int | None:
    """全脚本级 LLM 调用超时（GAP 重生 / 结构收敛，v6.70.3 默认 300s）.

    v6.101：结构收敛轮从 `_repair_timeout_sec()`（180s）改到本档——它也是
    「整章输入 + 整脚本输出（~26k token）」，与 GAP 重生同量级；
    第十四次重跑实测 180s 不够（客户端超时 → 候选拿不到）。
    """
    import os as _os
    try:
        _v = int(_os.getenv("FULL_SCRIPT_TIMEOUT_SEC", "300"))
    except ValueError:
        _v = 300
    return _v if _v > 0 else None


def _repair_timeout_sec() -> int | None:
    """定向修复/超限收敛/hook 超时（v6.70.3 默认 180s）.

    这些站点失败语义都是「保留原版继续」，600s 盲等只会把整轮拖死（09-13/14
    两晚修复轮 + hook 重生各白等一次 600s）。
    """
    import os as _os
    try:
        _v = int(_os.getenv("REPAIR_TIMEOUT_SEC", "180"))
    except ValueError:
        _v = 180
    return _v if _v > 0 else None


# ============================================================================
# v6.70.3（09-14）：阶段计时 + 总预算
# 09-14 监控审计发现 17m38s 的 run 无任何结构化耗时数据，事后只能靠日志时间戳
# 人工拼时间线。timing 落盘 step01_timing.json；预算超时跳过「失败即保留原版」
# 语义的可选修复轮（regen_over_limit / repair rounds），核心链路（候选/GAP/hook）不受影响。
# ============================================================================
_STEP_TIMING: list = []
_STEP_TIMING_T0 = None
_STEP_LAST_MARK_T = None
_RUN_METRICS: dict = {}
_METRICS_LOCK = None


def _metrics_lock():
    global _METRICS_LOCK
    if _METRICS_LOCK is None:
        import threading as _threading
        _METRICS_LOCK = _threading.Lock()
    return _METRICS_LOCK


def _metrics_bucket(path: str) -> dict:
    """Create/return a nested metrics bucket for dotted path."""
    cur = _RUN_METRICS
    for part in path.split("."):
        if not part:
            continue
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    return cur


def _metrics_inc(path: str, n: int = 1) -> None:
    try:
        with _metrics_lock():
            parts = path.split(".")
            cur = _RUN_METRICS
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            key = parts[-1]
            cur[key] = int(cur.get(key, 0) or 0) + int(n)
    except Exception:
        pass


def _metrics_set(path: str, value) -> None:
    try:
        with _metrics_lock():
            parts = path.split(".")
            cur = _RUN_METRICS
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value
    except Exception:
        pass


def _metrics_update(path: str, values: dict) -> None:
    try:
        with _metrics_lock():
            bucket = _metrics_bucket(path)
            bucket.update(values or {})
    except Exception:
        pass


def _metrics_dump(path) -> None:
    """Persist structured call/time/repair metrics without affecting the run."""
    try:
        import time as _t
        from shared.json_io import atomic_dump_json as _adj

        payload = dict(_RUN_METRICS)
        payload["phases"] = list(_STEP_TIMING)
        if _STEP_TIMING_T0 is not None:
            payload["total_elapsed_sec"] = round(_t.time() - _STEP_TIMING_T0, 1)
        _adj(path, payload, ensure_ascii=False, indent=2)
    except Exception as _e:
        try:
            _logger.warn(f"[METRICS] metrics 落盘失败（不阻断）: {_e}")
        except Exception:
            pass


def _timing_start() -> None:
    """run 级计时起点（main() 拿锁后调用）."""
    global _STEP_TIMING_T0, _STEP_LAST_MARK_T
    import time as _t
    _STEP_TIMING_T0 = _t.time()
    _STEP_LAST_MARK_T = _STEP_TIMING_T0
    _STEP_TIMING.clear()
    _RUN_METRICS.clear()
    _RUN_METRICS.update({
        "run_started": _STEP_TIMING_T0,
        "calls": {},
        "repair": {},
        "audit": {},
        "hook": {},
        "duration": {},
        "quality": {},
    })


def _timing_mark(phase: str, status: str = "ok", **fields) -> None:
    """记录一个阶段完成点（毫秒级开销，失败静默）.

    ``elapsed_sec`` is cumulative since run start and is retained for backward
    compatibility.  ``phase_duration_sec`` is the independent duration since
    the previous mark, which is the field optimization work should consume.
    """
    global _STEP_LAST_MARK_T
    import time as _t
    try:
        if _STEP_TIMING_T0 is None:
            return
        now = _t.time()
        last = _STEP_LAST_MARK_T if _STEP_LAST_MARK_T is not None else _STEP_TIMING_T0
        _rec = {
            "phase": phase,
            "status": status,
            "phase_duration_sec": round(max(0.0, now - last), 1),
            "elapsed_since_start_sec": round(now - _STEP_TIMING_T0, 1),
            # Backward-compatible alias; treat as cumulative, not phase cost.
            "elapsed_sec": round(now - _STEP_TIMING_T0, 1),
        }
        _STEP_LAST_MARK_T = now
        _rec.update(fields)
        _STEP_TIMING.append(_rec)
    except Exception:
        pass


def _timing_dump(path) -> None:
    """落盘 timing JSON（失败只告警，绝不影响主流程）."""
    try:
        import time as _t
        if _STEP_TIMING_T0 is not None:
            now = _t.time()
            last = _STEP_LAST_MARK_T if _STEP_LAST_MARK_T is not None else _STEP_TIMING_T0
            _STEP_TIMING.append({
                "phase": "_total", "status": "ok",
                "phase_duration_sec": round(max(0.0, now - last), 1),
                "elapsed_since_start_sec": round(now - _STEP_TIMING_T0, 1),
                # Backward-compatible alias; cumulative, not phase cost.
                "elapsed_sec": round(now - _STEP_TIMING_T0, 1),
            })
        _payload = {"run_started": _STEP_TIMING_T0, "phases": _STEP_TIMING}
        import json as _json
        from shared.json_io import atomic_dump_json as _adj
        _adj(path, _payload, ensure_ascii=False, indent=2)
    except Exception as _e:
        try:
            _logger.warn(f"[TIMING] timing 落盘失败（不阻断）: {_e}")
        except Exception:
            pass


def _total_budget_sec() -> float:
    """run 总预算（v6.70.3 默认 900s；0 = 不限时）."""
    import os as _os
    try:
        _v = float(_os.getenv("STEP01_TOTAL_BUDGET_SEC", "900"))
    except ValueError:
        _v = 900.0
    return _v


def _budget_exceeded() -> bool:
    """超预算判定（未 start 或预算 0 = False）."""
    import time as _t
    if _STEP_TIMING_T0 is None or _total_budget_sec() <= 0:
        return False
    return (_t.time() - _STEP_TIMING_T0) > _total_budget_sec()


def _early_stop_score() -> float:
    """早停阈值；默认 95，可用 env 调整（非法值回退 95）."""
    import os as _os
    try:
        return float(_os.getenv("CANDIDATE_EARLY_STOP_SCORE", "95"))
    except ValueError:
        return 95.0


def _candidate_early_stop_max_shots() -> int:
    """早停镜头上限；默认 20，0 = 不限制.

    09-15 实测：100 分、20 镜、90s、高潮完整的候选已经拿到，却因旧的
    `len(shots) > 18` 不放行，继续等同一模型两次 300s 超时，候选阶段
    861.7s。20 镜仍是下游可生成规模，且分数已包含高潮完整性，因此把
    上限做成配置，默认 20。
    """
    import os as _os
    try:
        _v = int(_os.getenv("CANDIDATE_EARLY_STOP_MAX_SHOTS", "20"))
    except ValueError:
        _v = 20
    return _v if _v > 0 else 0


def _candidate_is_early_stop(score: dict, script: dict) -> bool:
    """高分候选 + 基础结构健康 → 允许放弃等待慢候选.

    只做确定性结构护栏（shot 数 / 时长 / 钩子），避免把「剧情高分但不可生成」
    的候选误判为终稿；下游 hard gate 仍照常执行。
    """
    if not _candidate_early_stop_enabled():
        return False
    if score.get("gate_status") == "BLOCK":
        return False
    if float(score.get("final_score", score.get("total", 0)) or 0) < _early_stop_score():
        return False
    if score.get("climax_complete") is not True:
        return False
    # Quality-aware gate: a raw 100 with blocking visual risks or corrupted
    # text must not win early-stop and lock the run into an unsafe candidate.
    if score.get("quality_checked") is False:
        return False
    if int(score.get("blocking_risk_count", 0) or 0) > 0:
        return False
    if score.get("quality_checked") and score.get("integrity_ok") is not True:
        return False
    shots = script.get("shots", [])
    if not shots:
        return False
    _max_shots = _candidate_early_stop_max_shots()
    if _max_shots and len(shots) > _max_shots:
        return False
    duration = sum(float(shot.get("duration_sec", 0) or 0) for shot in shots)
    if not 60.0 <= duration <= 90.0:
        return False
    if not script.get("hook"):
        return False
    return True


def _generate_script_candidates(
    novel_text: str,
    character_bank=None,
    scene_objects_bank=None,
    outline: dict | None = None,
) -> list:
    """多路径候选生成（v6.65 并行化，09-12）.

    生成 SCRIPT_CANDIDATE_TOPK 个独立候选，返回 [(score_dict, script)]（分数降序）。
    score_dict 额外带 "_model" 键（生成本候选的模型 ID，供 v6.67 接力修复选互补模型）。
    - TOPK=1：串行路径零变化（deepseek 默认）
    - TOPK>1：并行生成——doubao 单候选 2-3 分钟，串行 3 候选 8 分钟是流水线
      最大耗时项；AGENT_PLAN_TEXT_RPM=10 限速下 3 路并发合法。
      每轮并行补齐差额，失败/解析坏的候选进入下一轮重试（至 _max_attempts）。
    - 不同候选用不同温度增加多样性（第1个默认 0.2，后续 +0.15 递增）
    """
    from shared.config import SCRIPT_CANDIDATE_TOPK, SCRIPT_CANDIDATE_MODELS
    import threading as _threading
    _candidates: list = []  # [(score_dict, script)]
    _last_err: Exception | None = None
    _max_attempts = SCRIPT_PARSE_RETRIES * max(1, SCRIPT_CANDIDATE_TOPK)
    _attempt = 0
    _task_idx = 0  # 候选序号（温度梯度 0.2 + 0.15*idx）
    _model_list = list(SCRIPT_CANDIDATE_MODELS or [])
    _model_cooldowns: set[str] = set()
    _model_lock = _threading.Lock()

    def _pick_model(_idx: int) -> str | None:
        """候选模型轮转；超时模型本 run 冷却，优先改用其他模型."""
        if not _model_list:
            return None
        with _model_lock:
            _cooled = set(_model_cooldowns)
        _available = [m for m in _model_list if m not in _cooled]
        _pool = _available or _model_list
        return _pool[_idx % len(_pool)]

    def _cool_model_after_timeout(_err: RuntimeError) -> None:
        """把超时模型加入本 run 冷却，避免同模型继续高成本重试."""
        if not getattr(_err, "candidate_timed_out", False):
            return
        _model = getattr(_err, "candidate_model", None)
        if not _model:
            return
        with _model_lock:
            if _model in _model_cooldowns:
                return
            _model_cooldowns.add(_model)
        _logger.warn(
            f"[CANDIDATE] 模型 {_model} 候选超时，本 run 冷却，"
            f"后续候选改用其他模型"
        )

    def _gen_one_candidate(_idx: int):
        """生成单个候选：调 LLM → 解析 → 打分（线程内完成，返回 (score, script)）."""
        _temp = 0.2 + 0.15 * _idx
        # v6.66: 跨模型候选池——候选按序号轮转分配模型；空列表=全用默认模型
        _model = _pick_model(_idx)
        try:
            raw = gen_spy_script(
                novel_text, character_bank, scene_objects_bank,
                outline=outline,
                temperature=_temp,
                model=_model,
                timeout_sec=_candidate_gen_timeout_sec(),  # v6.70.3: 300s——慢候选由早停+超时双通道兜住
            )
        except RuntimeError as _e:
            _e.candidate_model = _model
            _e.candidate_timed_out = (
                "超时" in str(_e) or "timeout" in str(_e).lower()
            )
            raise
        _cand = parse_response(raw, novel_text=novel_text)
        _scr = _score_script(_cand, novel_text)
        # v6.73: attach the same blocking-risk/integrity view used by the final
        # gate before candidate ranking and early-stop decide anything.
        _scr.update(_candidate_quality_summary(_cand, novel_text))
        content_score = float(_scr.get("total", 0) or 0)
        q = _scr
        gate_pass = (
            q.get("quality_checked") is True
            and int(q.get("blocking_risk_count", 0) or 0) == 0
            and q.get("integrity_ok") is True
        )
        _scr["content_score"] = round(content_score, 1)
        _scr["final_score"] = round(content_score if gate_pass else min(content_score, 95.0), 1)
        _scr["gate_status"] = "PASS" if gate_pass else "BLOCK"
        _tps = _scr.get("temporal_plan_stats") or {}
        _scr["detail"] = (
            f"{_scr.get('detail', '')}，内容{_scr['content_score']:.0f}"
            f"，最终{_scr['final_score']:.0f}，{_scr['gate_status']}"
            f"，风险{_scr.get('blocking_risk_count')}"
            f"{'' if _scr.get('integrity_ok') else '，完整性异常'}"
            f"，plan {_tps.get('present', 0)}/{_tps.get('shots', 0)}"
            f"（valid {_tps.get('valid', 0)}, grounded {_tps.get('grounded', 0)}）"
        )
        _metrics_update("candidate.temporal_plan", _tps)
        # v6.67 接力修复：把生成本候选的模型挂在分数上，main() 据此选互补修复模型
        _scr["_model"] = _model
        return _scr, _cand

    if SCRIPT_CANDIDATE_TOPK <= 1:
        # TOPK=1：原串行路径零变化（deepseek 默认，含重试 sleep 语义）
        while _attempt < SCRIPT_PARSE_RETRIES:
            _attempt += 1
            try:
                _scr, _cand = _gen_one_candidate(_task_idx)
                _candidates.append((_scr, _cand))
                _logger.info(f"[CANDIDATE] 候选 1/1: {_scr.get('detail', '')}")
                _console(f"  [候选] 1/1: {_scr.get('detail', '')}")
                # v6.84（二次审核发现 1）：**结构不合法的候选要拒收**，不是「先收下
                # 再在末尾抱怨」。候选在结构上不合法（内容需求超 cap / 时长低于
                # 下限）时，只要还有生成预算就继续取下一个候选；预算用尽则明确
                # 结束为 BLOCK——由质量门判定，不假装收敛成功。
                # v6.102（第十六次修复项 #2）：**只有「超载」一类结构问题时提前采纳**。
                #
                # 实测（第十二～十五次每轮都一样）：同两条长台词（原文 50 / 59 字）
                # 让候选 1、2、3 全部结构不合法，每次白烧 3 个候选（各 ~26k token），
                # 而第三个候选手上已经是 100 分。而 `shot_content_over_cap` **有确定性
                # 修复路径**——后面的定向拆分（`_scoped_split_over_cap_shots`）会把它
                # 按时长上限切开，拆完照样过验收。既然问题一定能被修掉，就没必要为了
                # 「结构干净」再抽两次卡。
                #
                # 只放行这一类：`shot_duration_below_min`（过短）**没有**确定性路径
                # （要靠整脚本 regen 合并），所以仍然拒收重取。
                if _candidate_only_splittable(_scr):
                    _logger.info(
                        "[CANDIDATE] 候选只有「内容超载」（可被定向拆分确定性修掉）→ "
                        f"提前采纳，不再耗费候选：{(_scr.get('structural_issues') or [])[:2]}"
                    )
                    _console("  [候选] 仅超载（定向拆分可修），提前采纳")
                    break
                if _scr.get("structural_ok") is not True and _attempt < SCRIPT_PARSE_RETRIES:
                    _logger.warn(
                        "[CANDIDATE] 候选结构不合法（内容超 cap / 低于下限），"
                        f"拒收并重取（{_attempt}/{SCRIPT_PARSE_RETRIES}）："
                        f"{(_scr.get('structural_issues') or [])[:2]}"
                    )
                    _console(
                        f"  [候选] 结构不合法（超载/过短），重新生成"
                        f"（{_attempt}/{SCRIPT_PARSE_RETRIES}）..."
                    )
                    _task_idx += 1
                    continue
                if _scr.get("structural_ok") is not True:
                    _logger.warn(
                        "[CANDIDATE] 生成预算用尽，结构仍不合法——明确结束为 BLOCK："
                        f"{(_scr.get('structural_issues') or [])[:3]}"
                    )
                    _console("  [候选] 预算用尽，结构仍不合法（结果将判 BLOCK）")
                break
            except RuntimeError as e:
                _last_err = e
                _logger.warn(f"[WARN] LLM 生成失败（尝试 {_attempt}/{SCRIPT_PARSE_RETRIES}）: {e}")
                _console(f"  [重试] LLM 生成失败，重新调用（{_attempt}/{SCRIPT_PARSE_RETRIES}）...")
                import time as _t
                _t.sleep(SCRIPT_PARSE_RETRY_SLEEP_SEC)
            except (ValueError, json.JSONDecodeError) as e:
                _last_err = e
                _logger.warn(f"[WARN] 分镜 JSON 解析失败（尝试 {_attempt}/{SCRIPT_PARSE_RETRIES}）: {e}")
                _console(f"  [重试] JSON 解析失败，重新调用 LLM（{_attempt}/{SCRIPT_PARSE_RETRIES}）...")
    else:
        import concurrent.futures as _fut
        _early_stop_hit = False
        while len(_candidates) < SCRIPT_CANDIDATE_TOPK and _attempt < _max_attempts:
            _need = SCRIPT_CANDIDATE_TOPK - len(_candidates)
            _batch = min(_need, _max_attempts - _attempt)
            if _batch <= 0:
                break
            _ex = _fut.ThreadPoolExecutor(max_workers=_batch)
            _futs = {
                _ex.submit(_gen_one_candidate, _task_idx + _j): _j
                for _j in range(_batch)
            }
            _task_idx += _batch
            _batch_done = 0
            try:
                for _f in _fut.as_completed(_futs):
                    _attempt += 1
                    _batch_done += 1
                    try:
                        _scr, _cand = _f.result()
                        _candidates.append((_scr, _cand))
                        _logger.info(
                            f"[CANDIDATE] 候选 {len(_candidates)}/{SCRIPT_CANDIDATE_TOPK}: "
                            f"{_scr['detail']}"
                        )
                        # v6.68：高分早停。批量提交仍保留（不额外发请求），当前批剩余
                        # 未完成候选不再阻塞等待；provider 请求无法可靠取消，已发出的
                        # 调用会自然结束。
                        if _candidate_is_early_stop(_scr, _cand):
                            _remaining = len(_futs) - _batch_done
                            _logger.info(
                                f"[CANDIDATE] 早停：候选分 {_scr['total']} ≥ "
                                f"CANDIDATE_EARLY_STOP_SCORE={_early_stop_score()}，"
                                f"放弃等待 {_remaining} 个慢候选"
                            )
                            _early_stop_hit = True
                            break
                    except RuntimeError as e:
                        _cool_model_after_timeout(e)
                        _last_err = e
                        _logger.warn(f"[WARN] LLM 生成失败（尝试 {_attempt}/{_max_attempts}）: {e}")
                        _console(f"  [重试] LLM 生成失败（{_attempt}/{_max_attempts}）...")
                    except (ValueError, json.JSONDecodeError) as e:
                        _last_err = e
                        _logger.warn(f"[WARN] 分镜 JSON 解析失败（尝试 {_attempt}/{_max_attempts}）: {e}")
                        _console(f"  [重试] JSON 解析失败（{_attempt}/{_max_attempts}）...")
            finally:
                # v6.70.3（09-14）：早停必须真正不等慢线程——with 块退出会
                # shutdown(wait=True)，09-14 实测 break 后仍被 600s 慢候选拖住
                # （100 分候选 64s 到手，总耗时 17m38s）。wait=False 让
                # _generate_script_candidates 立即返回；孤儿线程随进程退出，
                # 不影响主流程（写盘都在主线程）。
                _ex.shutdown(wait=not _early_stop_hit)
            if _early_stop_hit:
                break

    if not _candidates:
        raise RuntimeError(f"分镜 JSON 连续 {_max_attempts} 次解析失败: {_last_err}")
    _candidates.sort(key=_candidate_sort_key)
    return _candidates


def _cross_run_best_guard(script: dict, novel_text: str,
                          hook_ctx: dict | None = None) -> dict | None:
    """跨 run 保优护栏（P4.6，2026-09-11）：防止更好的历史版本被差 run 覆盖.

    背景：doubao 输出方差大（实测同章 38~90 分），90 分终稿可能被下一次
    60 分 run 直接覆盖。快照机制只留档不比较，用户事后才知道被回退。

    行为：
      1. 给新终稿打分
      2. 给 snapshots/ 里最近 N（env BEST_GUARD_SCAN_N，默认 8）份快照打分，
         取最优（**不含本次快照**——按时间戳排除）
      3. 快照最优 > 新终稿 + BEST_GUARD_MARGIN（默认 3 分）：
         - env KEEP_BEST_SNAPSHOT=1（默认）→ 自动恢复最优快照为正式输出，
           本次较差版本仍留在 snapshots/ 可随时取回
         - 否则只告警不动作
      4. 打分/恢复失败只告警，不影响主流程
    """
    try:
        import os as _os
        import shutil as _shutil

        snap_dir = OUTPUT_FILE.parent / "snapshots"
        if not snap_dir.is_dir():
            return None
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        if not snaps:
            return None

        scan_n = max(1, int(_os.getenv("BEST_GUARD_SCAN_N", "8")))
        margin = float(_os.getenv("BEST_GUARD_MARGIN", "3"))
        keep_best = _os.getenv("KEEP_BEST_SNAPSHOT", "1") == "1"

        new_score = _score_script(script, novel_text)["total"]
        new_quality = _candidate_quality_summary(script, novel_text)
        new_rank = _quality_rank(new_score, new_quality)

        # 本次快照是 snaps 里最新一份（刚由 _snapshot_run_artifacts 写入）
        candidates = snaps[:-1] if len(snaps) > 1 else []
        candidates = candidates[-scan_n:]

        best_path = None
        best_score = -1.0
        best_quality = None
        best_rank = None
        for sp in candidates:
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    old = json.load(f)
                sc = _score_script(old, novel_text)["total"]
                q = _candidate_quality_summary(old, novel_text)
                rank = _quality_rank(sc, q)
            except Exception:
                continue
            # v6.73: compare integrity -> blocking risks -> total, not only
            # the raw story score.  A clean 90 must not lose to a corrupted 100.
            if best_rank is None or rank < best_rank:
                best_score = sc
                best_quality = q
                best_rank = rank
                best_path = sp

        if best_path is None:
            return None

        _same_risk_tier = (
            best_rank[0] == new_rank[0] and best_rank[1] == new_rank[1]
        )
        if _same_risk_tier:
            if best_score <= new_score + margin:
                return None
        else:
            # Prefer the safer snapshot whenever its integrity/risk tier is
            # strictly better than the new run, regardless of raw score.
            if not (best_rank[0] < new_rank[0]
                    or (best_rank[0] == new_rank[0] and best_rank[1] < new_rank[1])):
                return None

        _logger.warn(
            "[BEST-GUARD] 新终稿 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + " < 快照最优 "
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "（" + best_path.name + "）"
        )
        if not keep_best:
            _logger.warn("[BEST-GUARD] KEEP_BEST_SNAPSHOT=0 → 仅告警，保留新终稿")
            return None
        with open(best_path, "r", encoding="utf-8") as f:
            best_script = json.load(f)
        # 恢复版补跑标准 hard_fix（与落盘前管线同路径）：快照可能出自
        # 旧代码版本，补跑让它享受最新 constraint 修复（幂等，失败不阻断）
        try:
            # v6.83（二次审核发现 1 实施 #3）：BEST-GUARD 恢复路径此前也直接跑
            # hard-fix、没有阶段屏障——恢复历史快照等于绕过全部守卫落盘。
            best_script, _bg_errs = finalize_step01_script(
                best_script, novel_text=novel_text, label="BEST-GUARD 恢复版")
        except Exception as _bg_he:
            _logger.warn(f"[BEST-GUARD] 恢复版补跑 hard_fix 失败（保留原样）: {_bg_he}")
        # 09-14：恢复版必须过与正式落盘相同的钩子质量门——旧快照的病句/无主
        # 刀尖 + 非幂等补修复污染（刀尖句翻倍/对象库碎片）曾绕过全部门直接落盘
        if hook_ctx:
            try:
                _ensure_hook_quality(best_script, novel_text=novel_text, **hook_ctx)
            except Exception as _bg_he2:
                _logger.warn(f"[BEST-GUARD] 恢复版钩子质量门异常（不阻断）: {_bg_he2}")
        # Old snapshots may predate timeline unification.  Rebuild from final
        # duration_sec before scoring/gating the restored version.
        if _bg_errs:
            _logger.warn(f"[BEST-GUARD] 恢复版时间轴归一失败，放弃恢复: {'; '.join(_bg_errs[:3])}")
            return None
        restored_score = _score_script(best_script, novel_text)["total"]
        restored_quality = _candidate_quality_summary(best_script, novel_text)
        restored_rank = _quality_rank(restored_score, restored_quality)
        # A historical snapshot that still fails the current integrity/risk
        # gate must never overwrite the new output.  This is the guard that
        # retires old pre-v6.73 corrupted snapshots.
        if (restored_quality.get("quality_checked") is not True
                or restored_rank[0] > 0 or restored_rank[1] > 0):
            _logger.warn(
                "[BEST-GUARD] 恢复版未通过当前质量门，放弃恢复"
                f"（风险{restored_quality.get('blocking_risk_count')}，"
                f"完整性={'OK' if restored_quality.get('integrity_ok') else '异常'}）"
            )
            return None
        from shared.json_io import atomic_dump_json as _adj
        ensure_script_revision(best_script)
        _adj(OUTPUT_FILE, _strip_runtime_fields(best_script),
             ensure_ascii=False, indent=2)
        _logger.warn(
            "[BEST-GUARD] 已自动恢复快照最优版为正式输出（补修复后 "
            + str(restored_score) + " 分/风险"
            + str(restored_quality.get("blocking_risk_count")) + "）；本次 "
            + str(new_score) + " 分版本保留于 snapshots/"
        )
        _console(
            "  [BEST-GUARD] 本次 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + " 低于历史最优 "
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "，已恢复历史最优版（补修复后 "
            + str(restored_score) + " 分/风险"
            + str(restored_quality.get("blocking_risk_count"))
            + "；本次版本在 snapshots/ 可取回）"
        )
        return best_script
    except Exception as _bg_e:
        try:
            _logger.warn(f"[BEST-GUARD] 护栏执行失败（不影响主流程）: {_bg_e}")
        except Exception:
            pass


def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存.

    控制台静默策略：运行期间所有 print()（校验 WARN、LLM 推理过程等）
    重定向到 step01.log，控制台只显示关键进度和最终摘要。
    """

    _logger.separator()
    _logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _logger.info("  新增: 钩子分镜 + 敏感词校验")
    _logger.separator()

    # v6.70.2：章节级 single-flight 锁——禁止两个 step01 并发写同一章
    # （09-14 实测两个 Python 进程交错写 step01.log/script_result.json）。
    if not _acquire_step01_lock():
        return

    _timing_start()  # v6.70.3: 阶段计时起点（拿锁成功 = 本 run 独占计时器）

    try:
        # 无声视频模型告警：VIDEO_MODEL_VOICE=1（对话交给视频模型）但当前模型不生成语音时，
        # 成片会无对白/背景音。Seedance 1.0 系列 / MiniMax v1 Hailio 均无声（08-16 适配）。
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

        # 加载场景物体库（v6.6 新增）
        scene_objects_bank = SceneObjectsBank()
        scene_objects_bank.load()

        # v6.43（08-25）：3 个 LLM 预提取并发跑（角色/场景物体/大纲）——
        # 原来按顺序执行 = 3 × LLM 延迟（~16s 总耗时），并发后 = max(3) ≈ 5s，
        # 节省 ~10s。3 个 extractor 各自独立 schema/服务/写入目标，并发安全。
        pre_extracted_outline = None
        if CHARACTER_EXTRACTION_ENABLED or SCENE_OBJECT_EXTRACTION_ENABLED:
            _logger.info("[INFO] 并发预提取 角色/场景物体/大纲（3 路并发）...")
            from step01_script_gen.concurrent_extract import extract_all_concurrent
            from shared.config import SCRIPT_CANDIDATE_MODELS as _pre_models
            _pre_model = _pre_models[0] if len(_pre_models) == 1 else None
            pre_results = extract_all_concurrent(
                novel_text,
                character_bank,
                scene_objects_bank,
                enable_characters=CHARACTER_EXTRACTION_ENABLED,
                enable_scene_objects=SCENE_OBJECT_EXTRACTION_ENABLED,
                enable_outline=OUTLINE_ENABLED,
                model=_pre_model,
                log_func=_logger.info,
            )
            # === 角色：注册到 bank（主流程后段仍可复用） ===
            chars = pre_results.get("characters")
            if chars is not None:
                pre_extracted_chars = chars
                for c in chars:
                    ident = c.get("identifier", "")
                    if not ident:
                        continue
                    if c.get("is_new", True):
                        character_bank.register_character(
                            ident,
                            c.get("static_features", ""),
                            c.get("dynamic_features", ""),
                            NOVEL_CHAPTER,
                        )
                    elif c.get("dynamic_features"):
                        character_bank.update_dynamic_features(
                            ident, c.get("dynamic_features", ""),
                        )
                        character_bank.update_character_chapter(ident, NOVEL_CHAPTER)
                character_bank.save()
                _logger.info(f"[INFO] 预提取到 {len(chars)} 个角色特征（已保存到角色库）")
            # === 场景物体：注册到 bank ===
            objs = pre_results.get("scene_objects")
            if objs is not None:
                for o in objs:
                    scene_objects_bank.register_object(o, NOVEL_CHAPTER)
                scene_objects_bank.save()
                _logger.info(f"[INFO] 预提取到 {len(objs)} 个场景物体（已保存到场景物体库）")
            # === 大纲：预提取，主分镜生成时注入 prompt ===
            pre_extracted_outline = pre_results.get("outline")

        # 检查缓存（场景物体库内容变化也视为源变更，v6.6）
        objects_bank_data = json.dumps(
            scene_objects_bank._data, ensure_ascii=False, sort_keys=True
        )
        prompt_changed = _prompt_changed(
            Path(__file__).parent / "prompts.py", SCRIPT_FILE, novel_preview, NOVEL_CHAPTER,
            objects_bank_data, rule_version=RULE_VERSION,
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
            # v7: 缓存命中时也要跑场景/道具资产提取（老缓存无 scene_assets/prop_assets），
            # 结果写回 script 再落盘，保证 step02a 资产生成能拿到数据
            _extract_assets_and_update_script(script)
            # v6.62.3（09-08）：缓存命中路径此前**漏跑 hard_fix** —— 只算时间轴+提资产就
            # 落盘，导致两个落盘点行为不一致（正常生成路径见 line ~1926「每次落盘前跑一次
            # hard_fix」，v6.41 P19 铁律）。后果 09-08 实测：命中缓存把 v6.61 期的节拍词
            # 占位（「冲突，反转，真相，局势骤变」17 处）+ F8 合并标记（3 处）原样重新落盘，
            # 且 log 承诺的 W6 时长拉伸（constraints/runtime_extend）从未执行（正片 54.9s
            # < 60s 硬下限）。必须放在所有 transform 之后、dump 之前，且**不可**再调
            # _compute_timecodes（会按 motion_desc 覆盖 W6 拉伸的 duration_sec）。
            # v6.83（二次审核发现 1 实施 #3）：缓存命中路径此前直接跑 hard-fix、
            # **没有接入阶段屏障**（发现 1 第 2 条失守点）。现在与正常生成路径走
            # 同一个收口函数。
            script, _cached_errs = finalize_step01_script(
                script, novel_text=novel_text, label="缓存命中落盘前")
            if _cached_errs:
                _logger.warn(f"[WARN] 缓存命中最终时间轴归一失败: {'; '.join(_cached_errs[:3])}")
            # C5 修复（08-11）：fps 一致性校验（timeline 此时已存在）
            # v6.62.3：移到 hard_fix 之后 —— runtime_extend 会改 duration_sec，
            # 早于 hard_fix 校验会拿到过期时长（与 line ~1931 非缓存路径同序）。
            _check_fps_consistency(script)
            from shared.json_io import atomic_dump_json as _adj
            ensure_script_revision(script)
            _adj(SCRIPT_FILE, _strip_runtime_fields(script),
                 ensure_ascii=False, indent=2)
            _snapshot_run_artifacts(script)
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
        # v6.64（P4.5，09-11）：多路径择优——生成 SCRIPT_CANDIDATE_TOPK 个独立候选，
        # 取质量分最高的进入 GAP 循环。用 token 换稳定性，对冲模型方差。
        # 默认 doubao=2 / deepseek=1（向下兼容：=1 时行为零变化）。
        _candidates = _generate_script_candidates(
            novel_text, character_bank, scene_objects_bank, pre_extracted_outline,
        )
        _timing_mark("candidate_generation",
                     n_candidates=len(_candidates),
                     best_score=_candidates[0][0]["total"] if _candidates else None)


        # 选总分最高的候选
        _candidates.sort(key=_candidate_sort_key)
        _best_score, script = _candidates[0]
        # v6.67 接力修复：记录当前稿件的产出模型。GAP 重生成若被采纳会重置为 None
        #（产出者回到 provider 默认模型，接力标识失效）——见下方 GAP 循环。
        _producer_model = _best_score.get("_model")
        _logger.warn(f"[DEBUG-RELAY] _best_score keys={list(_best_score.keys())[:5]}, _producer_model={_producer_model!r}")
        if SCRIPT_CANDIDATE_TOPK > 1:
            _logger.info(
                f"[CANDIDATE] 多路径择优完成：{len(_candidates)} 个候选 → "
                f"最优分 {_best_score['total']}（{_best_score['detail']}）"
            )
            _console(
                f"  [择优] {len(_candidates)} 个候选 → 最优 {_best_score['total']}分 "
                f"（{_best_score['detail']}）"
            )
            # 其余候选存盘供调试
            try:
                import copy as _cp
                from shared.json_io import atomic_dump_json as _adj
                _cand_dir = CHAPTER_DIR / "candidates"
                _cand_dir.mkdir(parents=True, exist_ok=True)
                for _ci, (_cs, _cscript) in enumerate(_candidates):
                    _cpath = _cand_dir / f"candidate_{_ci+1}_score{int(_cs['total'])}.json"
                    _adj(_cpath, _strip_runtime_fields(_cp.deepcopy(_cscript)),
                         ensure_ascii=False, indent=2)
            except Exception:
                pass

        # ========== 差距分析（v5.0 新增）==========
        # 若差距过大，重新生成（最多2次）

        if GAP_ANALYSIS_ENABLED:
            # v6.62.4（09-08）预算动态化：原 `for attempt in range(GAP_MAX_REGEN_ATTEMPTS)`。
            # agent_plan(doubao) 预算是 1（v6.62.1 实测「2 次重试全部劣化回退、白烧
            # 5 分钟」刻意压到 1）。但「正片零高潮覆盖」是**结构性缺陷**（整段剧情没拍），
            # 不是措辞问题，值得单独破例给 1 次。仅在预算即将耗尽时追加，且只追加一次；
            # deepseek（预算 2）不会凭空变 3，除非它也零高潮。
            # 追加那次自动受下方 v6.47 `_is_retry_degraded` 回退保护（时长塌缩/台词
            # 失锚即回退上一版），所以多给一次不引入劣化风险。
            _gap_budget = GAP_MAX_REGEN_ATTEMPTS
            _zero_climax_bonus_used = False
            attempt = 0
            while attempt < _gap_budget:
                # 快速校验
                validation = validate_script_gap(script, novel_text)

                if validation["overall_pass"]:
                    _logger.info(f"[GAP] 差距分析通过 (尝试 {attempt + 1}/{_gap_budget})")
                    # 保存脚本并生成完整报告
                    partial_path = CHAPTER_DIR / "script_result.partial.json"
                    from shared.json_io import atomic_dump_json as _adj
                    _adj(partial_path, _strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
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
                if not gap_needs_full_regen(validation):
                    _logger.warn(
                        "[GAP] 仅软告警（角色特征锚点等确定性可修复项），"
                        "跳过整稿重生，转交下游约束修复"
                    )
                    _console("  [GAP] 仅软告警，跳过整稿重生")
                    break

                # 发现问题，反馈给 LLM 重新生成
                # v6.62.4：零高潮=结构性缺陷 → 预算耗尽前额外追加 1 次（只一次）
                _dist = validation.get("distribution", {}) or {}
                if (_dist.get("climax_shots", 0) == 0
                        and not _zero_climax_bonus_used
                        and attempt + 1 >= _gap_budget):
                    _gap_budget += 1
                    _zero_climax_bonus_used = True
                    _logger.warn(
                        "[GAP] 正片零高潮覆盖（结构性缺陷：原文核心冲突整段未拍）"
                        f"→ 额外追加 1 次重试预算（{_gap_budget} 次）"
                    )
                    _console("  [GAP] 正片零高潮覆盖 → 额外追加 1 次重试")

                _logger.warn(f"[GAP] 发现差距问题， 重新生成 (尝试 {attempt + 1}/{_gap_budget})...")

                # 获取差距分析上下文并追加到 prompt（函数已在模块级导入）
                gap_context = _load_gap_analysis_context(validation)
                # v6.47（08-27）反馈前置：重生成铁律放在反馈区**最前**——长 prompt 后段
                # 规则易失声（实测重试版把总时长写塌到 11s、编造原文没有的台词），
                # 必须在反馈前重申非协商规则作为拒收标准
                gap_context = _build_regen_iron_rules() + gap_context
                # v6.33（08-12）：附加 checklist 聚合反馈——差距分析之外，把内容密度/景别
                # 跳跃/致命伤等真实缺陷也喂给重生成 LLM（此前只喂覆盖率/分布，重生成还是老问题）
                _checklist_fb = build_regen_feedback(script, novel_text)
                if _checklist_fb:
                    gap_context = gap_context + _checklist_fb

                # 追加差距反馈到 prompt，重新生成
                # v6.68: GAP 重生成未指定模型（=provider 默认模型），prompt 剖面同步跟随。
                # 默认模型 deepseek-v4-flash → deepseek 剖面不截断，救援能直接看到原文高潮
                #（此前 1600 截断下救援只能靠 gap_context 里的高潮摘录）。
                _gap_builder, _gap_system = resolve_model_prompt_profile(None)
                prompt = _gap_builder(novel_text, character_bank, scene_objects_bank)
                prompt = prompt + gap_context

                _metrics_inc("calls.gap_regen")
                service = create_service()
                from _text_gen.deepseek_schema import SCRIPT_SCHEMA
                req = TextGenerationRequest(
                    system_prompt=_gap_system,
                    user_prompt=prompt,
                    temperature=0.2,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
                    json_schema=SCRIPT_SCHEMA,
                    timeout_sec=_full_script_timeout_sec(),  # v6.70.3: GAP 重生 300s 分级
                )
                result = service.generate(req)
                if not result.success:
                    # v6.62.5（09-08）：原先这里 `raise RuntimeError` 会**穿出 GAP 循环
                    # 直接崩掉 step01**（本循环只 catch ValueError/JSONDecodeError）。
                    # v6.62.5 起 provider 把「输出截断」也判为 success=False，这条路径
                    # 从罕见（网络错误）变常见（截断），必须与站点 422/1146 一致地软降级：
                    # 保留上一版脚本继续跑，而不是让整个流水线挂掉。
                    _logger.warn(f"[WARN] GAP 重生成失败，保留上一版: {result.error}")
                    _console("  [GAP] 重生成失败（详见 step01.log），保留上一版")
                    break

                import re
                raw = result.text.strip()
                raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
                raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
                raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

                try:
                    new_script = parse_response(raw, novel_text=novel_text)
                except (ValueError, json.JSONDecodeError) as e:
                    # 重生成结果 JSON 损坏 → 保留上一版，不中断流水线
                    _logger.warn(f"[WARN] 重生成 JSON 解析失败，保留上一版: {e}")
                    break
                # v6.62.7（09-09）零高潮救援：先判断这次重试是不是**修好了结构性
                # 缺陷**。回退保护此前对「结构性缺陷 vs 措辞瑕疵」一视同仁，于是把
                # 唯一一次修好结构的重试也扔掉——09-08 实测链路：正片零高潮 →
                # v6.62.4 特批 1 次重试 → 23:31 重试版以「台词失锚 2 条」被判退化回退
                # → 终稿仍零高潮（原文 3100-4847 字的刺杀/尖叫/掩尸整段没拍，刺客与
                # 张十三被提取进 characters 却零出镜，成片是 63s 吹箫风光片配一个
                # 「公子遇刺」的钩子）。两个修复互相抵消，跟 v6.62.6 那次同构。
                _new_climax = ((validate_script_gap(new_script, novel_text)
                                .get("distribution") or {}).get("climax_shots", 0))
                _climax_rescue = _dist.get("climax_shots", 0) == 0 and _new_climax > 0
                # 救援态下被豁免的软判据仍要留痕，否则「放宽了什么」不可追溯
                _waived = (_retry_degrade_reason(script, new_script, novel_text)
                           if _climax_rescue else None)
                # v6.47（08-27）回退保护：重试版质量退化（时长塌缩/台词失锚变多）→
                # 保留上一版。GAP 通过≠质量好——08-27 重试版 11 镜×1s 靠加镜头骗过
                # 覆盖检查但时长崩了，事后 W6 把 1s 拉到 8s（8 倍慢动作）。
                if _is_retry_degraded(script, new_script, novel_text,
                                      climax_rescue=_climax_rescue):
                    _reason = _retry_degrade_reason(script, new_script, novel_text,
                                                    climax_rescue=_climax_rescue)
                    _logger.warn(f"[GAP] 重试版质量退化：{_reason}，回退保留上一版")
                    _console(f"  [GAP] 重试版质量退化，回退保留上一版（{_reason}）")
                    break
                if _climax_rescue:
                    _logger.warn(
                        f"[GAP] 零高潮救援：上一版 0 个高潮镜 → 重试版 {_new_climax} 个，"
                        f"采纳重试版" + (f"（已豁免软判据：{_waived}）" if _waived else "")
                    )
                    _console(f"  [GAP] 零高潮救援：采纳重试版（+{_new_climax} 高潮镜）")
                script = new_script
                # v6.67 接力修复：GAP 重生成走的是 provider 默认模型，产出者标识重置
                _producer_model = None
                # 不在循环内 post_process：循环退出后统一在 line 334 做一次
                # （code-audit 07-31 #7：原 :328 与 :334 重复调用 _post_process）
                attempt += 1   # v6.62.4：while 循环手动推进（原 for 由 range 推进）
            else:
                # 达到最大尝试次数仍有问题，记录但不中断
                # while...else 与原 for...else 语义一致：无 break 走完才告警
                _logger.warn("[GAP] 达到最大重试次数，仍有差距问题，将继续处理")
            _timing_mark("gap_loop")  # v6.70.3: 通过/耗尽均在此收口（含 else 分支）

        # v6.67（09-12）接力修复：赢家生成、输家修复——修复轮用互补模型换视角。
        # 修复 prompt 已有「只动告警镜头」+ Mask 字段约束，换模型影响面可控。
        # 候选池空/单模型/产出者无标识（GAP 重生成已采纳）→ _repair_model=None=
        # 同 provider 默认模型，零行为变化。GAP 重生成本身暂不接力（方案②范围）。
        _repair_model = _complementary_repair_model(_producer_model)
        if _repair_model:
            if _repair_model == _producer_model:
                _logger.info(f"[RELAY] 单模型继续修复: {_repair_model}")
                _console(f"  [单模型] 规划/生成/审查/修复均使用 {_repair_model}")
            else:
                _logger.info(
                    f"[RELAY] 产出模型 {_producer_model} → 修复轮接力 {_repair_model}"
                )
                _console(
                    f"  [接力] 修复轮改用 {_repair_model}（产出模型 {_producer_model} 的互补模型）"
                )

        # v6.7: 镜头数严重超限时收敛重生成（独立于 gap_analysis，默认开启）
        # v6.70.3: 超总预算 → 跳过可选修复轮（失败即保留原版语义，跳过无数据风险）
        if SHOT_COUNT_REGEN_ENABLED and not _budget_exceeded():
            _timing_mark("pre_regen_over_limit")
            script = _regen_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
            )
            _timing_mark("regen_over_limit")
        elif SHOT_COUNT_REGEN_ENABLED:
            _timing_mark("regen_over_limit", status="skipped_budget")
            _logger.warn("[BUDGET] 总预算超时，跳过镜头超限收敛轮（保留当前稿）")
            _console("  [预算] 总预算超时，跳过镜头超限收敛轮")

        # v6.84（二次审核发现 1，P0）：**结构收敛轮**——单镜内容需求超 cap /
        # 时长低于下限。这两类缺陷字段级修复轮修不了（不是文本问题，是镜头切分
        # 问题），此前只有报告没有收敛路径，必然带着 BLOCK 落盘。这里让 LLM
        # **重做 shot-plan**（超限段落拆成多个完整子镜；过短镜头并入相邻剧情）。
        if SHOT_COUNT_REGEN_ENABLED and not _budget_exceeded():
            _timing_mark("pre_regen_structural")
            script = _regen_structural_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
            )
            _timing_mark("regen_structural")
        elif SHOT_COUNT_REGEN_ENABLED:
            _timing_mark("regen_structural", status="skipped_budget")

        # v6.73: same-model independent audit before targeted repair.
        _audit_issues = []
        try:
            from shared.config import SCRIPT_CANDIDATE_MODELS as _audit_models
            _audit_enabled = (
                len(_audit_models) == 1
                and os.getenv("SINGLE_MODEL_AUDIT", "1").strip() == "1"
            )
            if _audit_enabled and not _budget_exceeded():
                from step01_script_gen.audit import run_same_model_audit_with_status
                _metrics_inc("calls.audit")
                _audit_result = run_same_model_audit_with_status(
                    novel_text, pre_extracted_outline, script,
                    model=_repair_model or _producer_model,
                    timeout_sec=int(os.getenv("AUDIT_TIMEOUT_SEC", "180") or "180"),
                )
                _audit_issues = _audit_result.issues
                _metrics_set("audit.status", _audit_result.status)
                _metrics_set("audit.issues", len(_audit_issues))
                try:
                    from shared.json_io import atomic_dump_json as _audit_dump
                    _audit_dump(
                        CHAPTER_DIR / "step01_audit.json",
                        {
                            "status": _audit_result.status,
                            "model": _repair_model or _producer_model,
                            "error": _audit_result.error,
                            "issues": [i.to_dict() for i in _audit_issues],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                except Exception as _audit_dump_err:
                    _logger.warn(f"[AUDIT] 审查结果落盘失败（不阻断）: {_audit_dump_err}")
                if _audit_result.status == "ok":
                    _logger.info(f"[AUDIT] same-model audit issues={len(_audit_issues)}")
                else:
                    _metrics_set("audit.error", _audit_result.error)
                    _logger.warn(
                        f"[AUDIT] same-model audit {_audit_result.status}"
                        f"（区别于 0 issue，不阻断）: {_audit_result.error}"
                    )
        except Exception as _audit_err:
            _metrics_set("audit.status", "exception")
            _metrics_set("audit.error", str(_audit_err))
            _logger.warn(f"[AUDIT] same-model audit failed（不阻断）: {_audit_err}")

        # v6.35（08-15，P18 闭环）：GAP 通过后仍存在的高价值告警（开篇冲突/
        # 爆点密度/威胁源朝向/关键帧质量/内容密度等）→ 定向修复轮，只重写
        # 告警镜头（此前这些 WARN 纯打印落盘，无人修，带伤进 step02）。
        if REPAIR_ROUNDS_ENABLED and not _budget_exceeded():
            _timing_mark("pre_repair_rounds")
            script = _repair_shot_warnings(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                audit_issues=_audit_issues,
            )
            _timing_mark("repair_rounds")
        elif REPAIR_ROUNDS_ENABLED:
            _timing_mark("repair_rounds", status="skipped_budget")
            _logger.warn("[BUDGET] 总预算超时，跳过定向修复轮（保留当前稿，告警仍会落 SAVE-WARN）")
            _console("  [预算] 总预算超时，跳过定向修复轮")

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
                for _sub_path, _shots_root in (
                    (vimax_working_dir(), vimax_shots_dir()),
                    (stretched_dir(), stretched_dir() / "shots"),
                ):
                    if _sub_path.exists():
                        if _shots_root.exists():
                            for _sid in _diff.get("changed", []) + _diff.get("removed", []):
                                _shot_dir = _shots_root / str(_sid)
                                if _shot_dir.exists():
                                    import shutil as _sh
                                    _sh.rmtree(_shot_dir)
                                    _logger.verbose(f"  [已清理] {_sub_path.name}/shots/{_sid}/（内容变更）")
        except Exception as _preserve_err:
            _logger.warn(f"[PRESERVE] 逐镜对比失败，全量清理: {_preserve_err}")

        if not _preserve_shots:
            _logger.info(f"[INFO] 分镜脚本已更新，清理旧的音视频文件...")
            for subdir_path in (audio_dir(), vimax_working_dir(), stretched_dir()):
                if subdir_path.exists():
                    import shutil
                    try:
                        shutil.rmtree(subdir_path)
                        _logger.verbose(f"  [已删除] {subdir_path.name}/")
                    except PermissionError as _e:
                        # 09-13 实测：Explorer/看图器/杀毒占用目录 → WinError 32。
                        # 清理失败就 FATAL 会吞掉整个 LLM 生成结果（此时 script 尚未
                        # 落盘，两轮实跑各 ~10 min 全白跑）。旧帧由内容哈希自动失效
                        # （ff/kf desc 变更 → hash mismatch → step02a 重抽），跳过即可。
                        _logger.warn(f"  [WARN] {subdir_path.name}/ 被占用（{type(_e).__name__}），"
                                     "跳过清理——旧文件由内容哈希自动失效")
        # 删除最终视频和字幕（脚本结构变了，最终产物必须重做）
        for fpath in (final_file(), subtitles_file()):
            if fpath.exists():
                try:
                    fpath.unlink()
                    _logger.verbose(f"  [已删除] {fpath.name}")
                except PermissionError:
                    _logger.warn(f"  [WARN] {fpath.name} 被占用（播放器？），跳过重删")

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
        _forbidden_lines: list[str] | None = None
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
            _timing_mark("hook_generation", ok=hook is not None)

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

                # ===== ④ 钩子后置校验轮（v6.70.1，09-13；09-14 抽出共享门）=====
                # 结构性缺口：钩子生成时点在定向修复轮之后，checklist 的 LLM 修复轮
                # 永远轮不到 hook——09-13 实测：修复轮 600s 超时保留原版 + 钩子原生
                # 输出带无主刀尖直接落盘 → step02a QC 名单打架整镜烧穿。质量门抽为
                # _ensure_hook_quality 共享：对两条正式输出来源（本次 run /
                # BEST-GUARD 恢复的快照）同等生效。
                try:
                    _ensure_hook_quality(
                        script, novel_text=novel_text, bank=character_bank,
                        objects_bank=scene_objects_bank,
                        forbidden_lines=_forbidden_lines,
                        producer_model=_producer_model)
                except Exception as _e:
                    _logger.warn(f"[WARN] 钩子后置校验异常（不阻断）: {_e}")
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
        _timing_mark("asset_extraction")

        # 计算时间轴（v5.0 运镜量化参数）
        _compute_timecodes(script)
        # 08-23 v6.41 P19 闭环：硬修复层必须在 _compute_timecodes 之后调用——
        # 此前 hard_fix 在 REPAIR 内部调用，但 line 1659 _compute_timecodes 会按
        # motion_desc 重算 duration_sec，覆盖 W6 时长拉伸的值。改为：每次落盘前
        # 跑一次 hard_fix_shot_prompts（含 W6 时长拉伸），保证最终值持久化。
        # v6.42：改用新 Constraint 框架（is_final_pass=True 让 RuntimeExtendConstraint 拉伸时长）
        # v6.83（09-20，二次审核发现 1 实施 #3）：三条路径统一走
        # `finalize_step01_script()` —— hard-fix（含阶段屏障）+ timeline 序列化 +
        # 最终时间不变量，顺序写在一处，任何入口都不能绕过。
        script, _finalize_errs = finalize_step01_script(
            script, novel_text=novel_text, label="落盘前")
        # C5 修复（08-11）：fps 一致性校验（timeline 此时已存在）
        _check_fps_consistency(script)

        # 章节元信息（部名/章名，来自小说标题行「第一部 杀青州 第NNN章 章名」）
        script.update(get_chapter_meta("锦衣夜行_77119小说.txt", NOVEL_CHAPTER))

        # 写入 hash
        # v6.83（二次审核发现 6）：**必须用非下划线键**。
        #
        # 落盘前会调 `_strip_runtime_fields()`，它递归删除所有下划线开头的键——
        # 写 `_source_hash` 等于写了个立刻被删的字段，`_prompt_changed()` 下次
        # 永远读到空值、永远返回 True：**每次运行 step01 都会重新调用模型**，
        # 「重跑 = 缓存重验」这条路径从未生效过（chapter 001 实测
        # `_source_hash=None`）。缓存/version 迁移测试也因此失去意义。
        # 读侧仍兼容旧文件里的 `_source_hash`（见 hash_cache._prompt_changed）。
        script["source_hash"] = _hash_source(
            str(Path(__file__).parent / "prompts.py"), novel_preview, NOVEL_CHAPTER,
            objects_bank_data, rule_version=RULE_VERSION,
        )

        # v6.50（08-29）：保存前重算 shot_count——timecode pre-pass 拆镜发生在
        # parse 期校正之后，元数据会落后实际镜头数（15:12 run 16 vs 20 实测）
        script["shot_count"] = len(script.get("shots", []))

        # 保存（v6.63.1：剥运行时字段——_run_metadata 等下划线键不入盘；
        # 2026-09-13 原子写，防中断留半截 JSON）
        # v6.73: freeze a stage revision so stale quality reports can be rejected
        # by step02a/02b/03/04 even when their JSON files still exist.
        ensure_script_revision(script)
        from shared.json_io import atomic_dump_json as _adj
        _adj(OUTPUT_FILE, _strip_runtime_fields(script),
             ensure_ascii=False, indent=2)
        _snapshot_run_artifacts(script)
        # v6.70.1（09-14）：BEST-GUARD 可能恢复历史快照为正式输出——钩子质量门
        # 必须对恢复版同样生效（实测恢复版曾绕过全部门：旧快照病句/无主刀尖 +
        # 非幂等补修复污染直接落盘）。返回实际写盘的最终 script（无恢复返回 None）。
        script = _cross_run_best_guard(script, novel_text, hook_ctx={
            "bank": character_bank, "objects_bank": scene_objects_bank,
            "forbidden_lines": _forbidden_lines,
            "producer_model": _producer_model,
        }) or script

        # v6.70.1（09-13，09-14 移到 BEST-GUARD 之后）：落盘前未解决 issue 汇总——
        # 对最终写盘版本（本次 run 或恢复版）生效。定向修复轮超时/失败走「保留
        # 原版」时只有一行 WARN，残伤数据静默落盘（09-13 hook 无主刀尖即此形态），
        # 这里显式打出（不阻塞；确定性护栏已在 hard_fix 修过一轮，剩下的是 LLM 级）。
        try:
            _final_hook_issues = _collect_hook_issues(script) if script.get("hook") else []
            _final_hard = _collect_repair_warnings(script, novel_text)
            _n_hard_shots = len([k for k in (_final_hard.get("shots") or {})
                                 if k not in ("_has_hard",)])
            if _final_hard.get("_has_hard") or _final_hook_issues:
                _logger.warn(
                    f"[SAVE-WARN] ⚠️ 落盘脚本仍带未解决 issue：{_n_hard_shots} 镜 + "
                    f"钩子 {len(_final_hook_issues)} 条——修复轮可能超时保留原版，"
                    f"请过目 step01 日志再进 step02a")
                _console(f"  ⚠️ 落盘前仍有未解决 issue（{_n_hard_shots} 镜 / 钩子 "
                         f"{len(_final_hook_issues)} 条），详见日志")
        except Exception as _e:
            _logger.warn(f"[WARN] 落盘前 issue 汇总异常（不阻断）: {_e}")

        _quality = _write_step01_quality_gate(script, novel_text)
        _shot_total = sum(float(s.get("duration_sec") or 0) for s in script.get("shots", []))
        _hook_total = float((script.get("hook") or {}).get("duration_sec") or 0)
        _metrics_update("duration", {
            "shots_total_sec": round(_shot_total, 3),
            "hook_sec": round(_hook_total, 3),
            "total_sec": round(_shot_total + _hook_total, 3),
            "budget_status": (
                "PASS" if 60.0 - 0.01 <= _shot_total <= 90.0 + 0.01 else "BLOCK"
            ),
        })
        _metrics_update("quality", {
            "status": _quality.get("status"),
            "hard_count": _quality.get("hard_count", 0),
            "visual_risk_count": _quality.get("visual_risk_count", 0),
            "advisory_count": _quality.get("advisory_count", 0),
            "video_advisory_count": _quality.get("video_advisory_count", 0),
            "blocking_categories": _quality.get("blocking_categories", []),
            "rule_version": _quality.get("rule_version"),
        })
        if _quality.get("blocking"):
            _logger.warn(
                f"[QUALITY-GATE] BLOCK: hard={_quality.get("hard_count", 0)}, "
                f"visual={_quality.get("visual_risk_count", 0)}; "
                f"categories={_quality.get("blocking_categories", [])}"
            )
            _console("  [QUALITY-GATE] BLOCK: 仍有生图阻塞告警，见 step01_quality.json")
        else:
            _logger.info("[QUALITY-GATE] PASS: 无 HARD / VisualRisk 阻塞项")
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
            if sub:
                _console(f"    - 镜{idx}: {sub[:42]}")
            else:
                # subtitle 为空时回退到 main_subject（避免误报"空镜"）
                ms = (shot.get('main_subject', '') or '').strip()
                _console(f"    - 镜{idx}: [{ms[:42]}] （subtitle 空）" if ms else f"    - 镜{idx}: (无 main_subject)")
        _console("=" * 56)
        _console("  ⚠️ 详细校验 WARN 已写入 step01.log，控制台不再重复输出")
        _console("=" * 56)

        # 恢复 stdout
        sys.stdout = _REAL_STDOUT
        _logger.console_silent = False
    finally:
        _release_step01_lock()
        _timing_dump(CHAPTER_DIR / "step01_timing.json")  # v6.70.3: 阶段耗时指标落盘
        _metrics_dump(CHAPTER_DIR / "step01_metrics.json")  # 调用/修复/时长结构化指标


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
