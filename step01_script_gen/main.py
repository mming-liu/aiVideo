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

# 运行范围（--chapter / --book / --book-name）必须在下面第一个 config/env 读取之前生效：
# shared.config 的 NOVEL_CHAPTER 与本章的 CHAPTER_DIR/OUTPUT_FILE/_logger 都是 import 时求值，
# 在 main() 里再设 env 已经晚了（会静默用错章）。守卫见 shared/run_scope.bootstrap 文档。
if __name__ == "__main__":
    from shared.run_scope import bootstrap as _bootstrap_scope
    _bootstrap_scope("step01 分镜脚本生成", presentation_mode=True)

# 统一配置（从 shared.config 读取 .env）
from shared.config import (DURATION_MIN_SEC, DURATION_MAX_SEC,
                           DURATION_TARGET_MIN_SEC, DURATION_TARGET_MAX_SEC,
                           duration_band, duration_band_matches)
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
    step01_log_file,
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
    PROMPT_SOURCE_FINGERPRINT,
)
CHAPTER_DIR = chapter_dir()
OUTPUT_FILE = script_file()
SCRIPT_FILE = OUTPUT_FILE

# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(step01_log_file(), console_level="INFO")

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
    resolve_model_prompt_profile, render_execution_contract,
    append_execution_contract,
)

# v2026-09-03: 按 TEXT_PROVIDER 自动选择 prompt builder。
# agent_plan（doubao-seed-2-1-turbo）→ doubao 专属（XML schema + few-shot + workflow）
# 其他 provider → 沿用 deepseek 调优的 build_prompt / build_hook_prompt
from _text_gen.config import TEXT_PROVIDER_DEFAULT
_TEXT_PROVIDER = (os.getenv("TEXT_PROVIDER") or TEXT_PROVIDER_DEFAULT).strip().lower()
build_prompt, build_hook_prompt = auto_select_prompt_builder(_TEXT_PROVIDER)
_SYSTEM_PROMPT = auto_select_system_prompt(_TEXT_PROVIDER)
if _TEXT_PROVIDER == "agent_plan":
    # v6.143：这行原写「使用 doubao 专属 prompt」——**错**。v6.68 起 prompt 剖面跟
    # **模型**走（`resolve_model_prompt_profile`，见 `MODEL_PROMPT_PROFILES`）：
    # agent_plan 通道跑 deepseek-v4-flash 时用的是 `build_prompt` + `system.md`，
    # 与 doubao 毫无关系。硬编码这句话会主动误导「step01 在用 doubao」——
    # 2026-09-26 用户据此提出「改用 deepseek-flash」，而实测 764 次提交**全是**
    # deepseek-v4-flash。改为按**实际候选模型**逐个解析后打印。
    from shared.config import SCRIPT_CANDIDATE_MODELS as _CAND_MODELS
    _profiles = ", ".join(
        "%s→%s" % (_m, resolve_model_prompt_profile(_m)[0].__name__)
        for _m in (_CAND_MODELS or [None])) or "(provider 默认)"
    _logger.info("[INFO] agent_plan 通道：prompt 剖面按模型解析 = %s" % _profiles)
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
    # 锚点，保证冲突/高潮分布。
    # v6.132（用户 2026-09-25）：构造失败**不再降级为「无锚点继续生成」**——见下方 except。
    outline_anchor = ""
    prop_anchor_block = ""
    contract_block = ""
    if outline:
        # v6.131（2026-09-25 收口）：**可行性门不在本函数里**。本函数是"每个候选都会走"
        # 的路径，门放这里等于每个候选重算一遍，而且会被候选循环的 `except RuntimeError`
        # 当成生成失败重试。门已上移到候选扇出入口 `_generate_script_candidates()`，
        # 一轮只验算一次、在**完整分镜生成之前**（角色/场景物体/大纲三个提取模型此前
        # 已并发调用过，所以不是"任何模型调用之前"）。
        try:
            from step01_script_gen.outline import outline_to_anchor
            outline_anchor = outline_to_anchor(outline, novel_text)
            # v6.132（用户 2026-09-25）：**生成执行契约**（不压缩）——本集选用的全部台词 +
            # 必保留事实 + 事件顺序。200 字大纲摘要（doubao/agent_plan 剖面）只能装摘要，
            # 选材必须走这一块；四个完整稿生成入口共用。
            contract_block = render_execution_contract(outline)
            # v6.128（用户 2026-09-25 决定）：道具候选短提示**默认不再注入**。
            # 两次诊断生成都没过验收（提前释放且布局不跟随 / 连释放都不写），提示词
            # 路线暂停；离线代码与取证保留，`STEP01_PROP_ANCHOR_BLOCK=1` 可复测。
            from step01_script_gen.prop_state_anchor import prop_anchor_block_for
            prop_anchor_block = prop_anchor_block_for(outline)
            if prop_anchor_block:
                _logger.info("[INFO] 道具状态候选锚点 %d 条已注入独立短提示"
                             % len((outline or {}).get("prop_state_candidates") or []))
            elif (outline or {}).get("prop_state_candidates"):
                _logger.info("[INFO] 道具候选短提示已关闭（默认；"
                             "STEP01_PROP_ANCHOR_BLOCK=1 可开启），本轮不注入")
            _logger.info("[INFO] 章节大纲已预提取（并发），注入分镜 prompt")
        except Exception as e:
            # v6.132（用户 2026-09-25 收口）：这里**不再吞异常**。门已经判过计划可行，
            # 走到本函数说明计划应当被完整传递；构造失败还继续生成，就会产出一份
            # **没有执行契约**的稿子（选材不受约束），而报告仍会说"已过计划门"。
            # 显式失败 —— 异常在 `create_service()` 之前抛出，**不会调用模型**。
            # 用 `PlanInfeasibleError`（非 RuntimeError）：候选循环的 `except RuntimeError`
            # 是"生成失败→重试"语义，而这是确定性的计划传递失败，重试没有意义。
            from step01_script_gen.plan_feasibility import PlanInfeasibleError
            raise PlanInfeasibleError(
                "计划锚点／执行契约构造失败 ⇒ 不生成（否则稿件不受选材约束）：%s" % e
            ) from e
    # v6.68: prompt/system 跟着实际模型走——deepseek-v4-flash → deepseek 剖面；
    # doubao → doubao 剖面。两者都保留完整 novel，由 PROMPT_MAX_CHARS 统一控上限。
    # 此前 agent_plan 下所有模型共用 doubao prompt，是候选结构性零高潮的根因。
    _main_builder, _system = resolve_model_prompt_profile(model)
    prompt = append_execution_contract(
        _main_builder(novel_text, bank, objects_bank, outline_anchor,
                      prop_anchor_block=prop_anchor_block),
        contract_block)
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

def _candidate_model_for_audit() -> "str | None":
    """final semantic audit 用的模型（单模型配置下与生成同源）.

    与既有的「同模型审计」口径一致：候选模型只有一个时，审计就该用它——
    审计与被审对象同源才有意义；配了多个候选模型时没有唯一的「同源模型」，
    交 provider 默认（`None`），与旧行为相同。
    """
    try:
        from shared.config import SCRIPT_CANDIDATE_MODELS as _models
        return _models[0] if len(_models) == 1 else None
    except Exception:
        return None


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


_ABSENT = object()


def _bind_regen_plan_identity(baseline: dict, candidate: dict, *, plan) -> list:
    """整份重生成采纳前：**绑定本请求的计划身份 + 核完整契约**；返回拒绝原因.

    2026-09-30 复核第 4/5 轮「重生成身份维护」：时长收敛 / 结构收敛 / GAP 重生成都会
    **整份替换**稿件（`script = new_script`），而它们此前只比「不退化」——计划身份、
    `plan_refs` 事件契约、选用台词逐字都不在判据里。一旦采纳，提交层的契约门只能在
    **已经丢掉基线**的前提下报错；更糟的是重生成稿通常不带 `chapter_outline`/`plan_id`，
    会被当成「无可核」放行。

    ⚠️ `plan` 是**必填关键字参数**、且由调用方给出**本请求的权威计划**（复核第 5 轮
    P1-1）：不能从 `baseline["chapter_outline"]` 取——生成候选时它还没写入，取不到就
    当「没有契约」放行，等于这道门在正常新稿路径上不生效；也不能把「缺少上下文」
    自动解释成旧稿兼容。`plan` 为 `None`/空**只**表示"本请求确实没有计划"（旧缓存
    路径），此时留一条可见日志后放行（没有计划 ⇒ 基线自身也没有契约可验）。

    ① **绑定**：把 `plan_identity(plan)` 与计划本身写进候选——身份由**本请求**决定，
       不要求模型回抄；
    ② **验证**：事件履约契约（`require_declaration=True`）+ 选用台词逐字完整性。
       任一不过 ⇒ 拒绝（调用方保留基线；绑定被回滚，不留半份状态）。
    """
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return []
    if not isinstance(plan, dict) or not plan:
        _logger.info("[REGEN] 本请求没有计划（旧缓存路径）⇒ 无契约可验，不绑定身份")
        return []
    _prev = {k: candidate.get(k, _ABSENT) for k in ("chapter_outline", "plan_id")}
    candidate["chapter_outline"] = plan
    try:
        from step01_script_gen.plan_feasibility import plan_identity
        _pid = plan_identity(plan)
    except Exception:
        _pid = ""
    if _pid:
        candidate["plan_id"] = _pid
    reasons: list = []
    try:
        from step01_script_gen.plan_feasibility import (
            check_event_contract, check_script_dialogue_integrity,
            plan_dialogue_lines)
    except Exception as e:                      # 导入失败 = 无法验证 → 不采纳
        reasons.append("契约检查器不可用，按拒绝处理: %s" % e)
        plan_dialogue_lines = None
    if not reasons:
        try:
            _ok, _viol, _ = check_event_contract(candidate, plan,
                                                 require_declaration=True)
            if not _ok:
                reasons.append("事件履约契约破损（%s）" % "；".join(
                    "%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                  str(v.get("detail"))[:40])
                    for v in (_viol or [])[:3]))
        except Exception as e:
            reasons.append("事件履约契约检查器异常，按拒绝处理: %s" % e)
        if plan_dialogue_lines and plan_dialogue_lines(plan):
            try:
                _d_ok, _d_broken = check_script_dialogue_integrity(candidate, plan)
                if not _d_ok:
                    reasons.append("选用台词未逐字保住 %d 句（例：%s）" % (
                        len(_d_broken),
                        str((_d_broken or [{}])[0].get("line") or "")[:20]))
            except Exception as e:
                reasons.append("台词完整性检查器异常，按拒绝处理: %s" % e)
    if reasons:
        # 拒绝时回滚绑定：调用方虽然丢弃候选，但不留半份状态更安全（准则 7）。
        for _k, _v in _prev.items():
            if _v is _ABSENT:
                candidate.pop(_k, None)
            else:
                candidate[_k] = _v
    return reasons


def _accept_regen_candidate(before: dict, candidate: dict, novel_text: str, *,
                            plan) -> tuple:
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
    # v6.157（2026-09-30 复核第 4/5 轮）：**计划身份与完整契约**——「不退化」不等于
    # 「还是这一集」。`plan` 由调用方给出**本请求的权威计划**（不从基线里翻找，
    # 见 `_bind_regen_plan_identity` 的说明），不合格同样保留基线。
    reasons.extend(_bind_regen_plan_identity(before, candidate, plan=plan))
    return (not reasons), reasons


def _build_gap_regen_prompt(novel_text: str, bank, objects_bank, outline, contract: str,
                            gap_context: str) -> tuple:
    """GAP 重生的**最终提示组装**：大纲锚点 → 反馈（gap_context）→ **执行契约（末尾）**.

    v6.132（用户 2026-09-25）：契约必须由最终组装处追加——builder 之后还有 gap_context，
    契约在 builder 里就不在末尾。

    Returns `(prompt, system_prompt)`。
    """
    _gap_builder, _gap_system = resolve_model_prompt_profile(None)
    # v6.116（审核）：GAP 重生同样复用大纲锚点
    from step01_script_gen.outline import outline_to_anchor as _o2a
    # v6.128：道具候选短提示默认关闭（用户 2026-09-25 决定）
    from step01_script_gen.prop_state_anchor import prop_anchor_block_for as _pabf
    prompt = _gap_builder(
        novel_text, bank, objects_bank,
        _o2a(outline, novel_text),
        prop_anchor_block=_pabf(outline))
    prompt = prompt + gap_context
    return append_execution_contract(prompt, contract), _gap_system


def _regen_over_limit(script: dict, novel_text: str, bank=None, objects_bank=None,
                      max_attempts: int | None = None,
                      model: str | None = None,
                      outline_anchor: str = "",
                      contract_block: str = "",
                      plan=None) -> dict:
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
    duration_over = shots_total > DURATION_MAX_SEC + 0.01
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
                f"[DURATION] 正片 {shots_total:.1f}s > {DURATION_MAX_SEC:g}s，"
                f"反馈 LLM 精简/合并到 {DURATION_TARGET_MIN_SEC:g}-{DURATION_TARGET_MAX_SEC:g}s（尝试 {attempt}/{max_attempts}）..."
            )
            _console(f"  [时长] 正片 {shots_total:.1f}s 超 {DURATION_MAX_SEC:g}s，重新规划为 {DURATION_TARGET_MIN_SEC:g}-{DURATION_TARGET_MAX_SEC:g}s（{attempt}/{max_attempts}）...")

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
                f"v6.38 红果硬约束：正片总时长 {DURATION_MIN_SEC:g}-{DURATION_MAX_SEC:g}s（钩子另算）、单镜 3.5-{_shot_cap():.0f}s（不得超过 {_shot_cap():.0f}s）。\n"
            )
        if duration_over:
            feedback += (
                f"\n\n# ★ 正片时长硬约束（本轮拒收，最高优先级）\n"
                f"当前正片分镜合计 {shots_total:.1f}s，超过 {DURATION_MAX_SEC:g}s 上限。\n"
                f"**本轮必须把正片分镜 duration_sec 之和压到 {DURATION_TARGET_MIN_SEC:g}-{DURATION_TARGET_MAX_SEC:g}s，最多 {DURATION_MAX_SEC:g}s**。\n"
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
        # v6.131（2026-09-25 收口）：超限收敛也是**完整稿生成入口**，必须同样带上大纲锚点
        # ——否则它会绕开计划（选用的逐字台词/必保留事实/事件顺序）另写一版，等于在收敛
        # 这一步重新决定选材。此处此前只传 novel_text/bank/objects_bank（漏了大纲锚点）。
        prompt = _rl_builder(novel_text, bank, objects_bank, outline_anchor)
        prompt = prompt + feedback
        # 契约**最后**追加（用户 2026-09-25）：反馈之后才是模型最后一眼看到的内容
        prompt = append_execution_contract(prompt, contract_block)

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
        _ok, _why = _accept_regen_candidate(script, new_script, novel_text, plan=plan)
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
        duration_over = shots_total > DURATION_MAX_SEC + 0.01
        if over_max:
            if len(new_shots) <= SHOT_COUNT_MAX:
                _logger.info(
                    f"[COUNT] 收敛成功: {current} → {len(new_shots)} 镜, "
                    f"总时长 {total_duration:.0f}s"
                )
            over_max = len(new_shots) > SHOT_COUNT_MAX
        if not duration_over:
            _logger.info(
                f"[DURATION] 时长收敛成功: 正片 {shots_total:.1f}s（目标 {DURATION_TARGET_MIN_SEC:g}-{DURATION_TARGET_MAX_SEC:g}s）"
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

    ⚠️ **缺帧（`kf_segment_missing`）不在此集合**（2026-09-28 复核决定，撤回 v6.143 P3）：
    十次跑实测 11 次「缺帧触发整篇结构重生成」**0 次被采纳**（每次 3.3–4.3 分钟，
    并把 7/10 次跑顶过 900s 总预算、截断仍在收敛的定向修复轮）。缺帧的收敛通道是
    **质量门 `kf_segment_missing` + 定向修复轮**（实测 run 03 一轮 `resolved=6` 且
    `kf_structural_violations=[]` 被正常采纳）——整篇重写这条杠杆对它不成立：
    结构轮是「重做 shot-plan」，而候选验收要求「零新 blocking 身份」，整篇重写
    必然改动切分与内容 → 结构性不可采纳。
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
        f"3. 单镜 duration_sec 必须落在 3.5-{_shot_cap():.0f}s；正片合计 {DURATION_MIN_SEC:g}-{DURATION_MAX_SEC:g}s（钩子另算）。\n"
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
    # v6.138（九审 P1-3）：与台词门/核验层同一说话人前缀判据（单一事实源）——
    # 冒号前段含子句标点（"且慢，我告诉你："）不是前缀，不得从比对正文里丢掉
    from step01_script_gen.plan_feasibility import _split_speaker_prefix as _ssp
    body = _ssp(text)[1]
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


def _deterministic_narration_cuts(narr: str, cap: float, max_parts: int = 4):
    """按**句末合法切点**把一条台词确定性地切成 N 段，使**每段估时 ≤ cap**（B：统一判据）.

    2026-09-26 用户决策 A+B：切点判据与台词完整性门/规划层可表示性检查**单源**
    （`plan_feasibility.find_legal_split`，见其 docstring）——只认句末边界
    （。！？…；）；**逗号/顿号切点与等字长兜底废除**：句中碎片正是终稿台词门
    `cross_shot_split` 判破损、审计报"同一句被截成两半"的形态，"拆分成功、终稿
    失败"的直接机制（09-26 三次实跑复现）。无合法切法 → None——调用方
    `_scoped_split_one` 据此在**调用拆分模型之前**停下（原 v6.92 的"标点不足
    交整脚本 regen"路径收窄为"无句末合法切点"）。

    v6.92 历史注（仍然成立）：切点不交给模型——模型切分实测采纳率 1/4-2/4；
    本函数保持确定性纯函数，只是合法切点集合收窄为句末。
    """
    from step01_script_gen.plan_feasibility import find_legal_split
    return find_legal_split(narr, cap, max_parts=max_parts)


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
        # v6.136 B：无**句末合法切点**（逗号/等字长兜底已废除，与台词完整性门同判据）
        # ——在调用拆分模型之前停下，交整脚本 regen / 规划层处理
        _logger.warn(
            "[SPLIT] 镜头%s 无句末合法切点（单句超长且句内只有逗号）——"
            "不调用拆分模型，交整脚本 regen／规划层重选"
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
            # v6.138：事件履约声明随拆分继承——子镜承载与父镜相同的节拍/台词（台词
            # 已按段逐字校验，B/D 序列非降由「同值重复」构造成立，覆盖不变）
            if isinstance(shot.get("plan_refs"), dict):
                import copy as _copy_mod
                sub["plan_refs"] = _copy_mod.deepcopy(shot["plan_refs"])
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
                                 model: str | None = None,
                                 outline_anchor: str = "",
                                 prop_anchor_block: str = "",
                                 contract_block: str = "",
                                 plan=None) -> dict:
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
        # v6.143 P3 已撤回（2026-09-28 复核）：目标集合回到「超载/过短 + 开篇空镜」，
        # 缺帧不再触发整篇重生成——它走质量门 + 定向修复轮（见 `_structural_shot_issues`）。
        _logger.warn(
            f"[STRUCT] {len(issues)} 个镜头结构不合法（内容超 cap / 低于下限 / 开篇空镜）："
            f"{[str(i.shot_id) for i in issues]}，重做 shot-plan（尝试 {attempt}/{max_attempts}）"
        )
        _console(f"  [结构] {len(issues)} 镜超载/过短/空镜，重做分镜规划"
                 f"（{attempt}/{max_attempts}）...")

        _sb_builder, _sb_system = resolve_model_prompt_profile(model)
        # v6.116（审核）：重生必须**复用首稿用过的大纲锚点**——否则「花 79 秒重做
        # 整章」却把主要事件/高潮/时间预算全丢了，等于换个随机种子重来。
        prompt = _sb_builder(novel_text, bank, objects_bank, outline_anchor,
                             prop_anchor_block=prop_anchor_block)
        prompt = prompt + _structural_feedback(issues)
        try:
            _checklist_fb = build_regen_feedback(script, novel_text)
            if _checklist_fb:
                prompt += _checklist_fb
        except Exception:
            pass
        # 契约**最后**追加（用户 2026-09-25）：所有反馈之后
        prompt = append_execution_contract(prompt, contract_block)

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
                f"[STRUCT] 新稿未改善（{len(issues)} → {len(new_issues)} 个非法镜头），"
                f"保留原版"
            )
            return script
        _ok, _why = _accept_regen_candidate(script, new_script, novel_text, plan=plan)
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
        "duration_band": duration_band(),
        "issues": [i.to_dict() for i in issues],
    }


# hook 质量门只消费高价值 warning（避免普通告警触发重生循环）。
# gaze_state_conflict 是 09-13 实证会烧穿 QC 的确定性矛盾：闭眼角色 gaze 非无。
_HOOK_QC_WARNING_CATEGORIES = {"gaze_state_conflict"}


def _issue_from_record(rec: dict) -> Issue:
    """`Issue.to_dict()` 记录 → `Issue` 对象（授权推导要它的 `field_path`）."""
    fields = getattr(Issue, "__dataclass_fields__", {})
    return Issue(**{k: v for k, v in (rec or {}).items() if k in fields})


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
        "duration_band": duration_band(),
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


def _build_step01_quality_gate(script: dict, novel_text: str) -> dict:
    """构造确定性质量门报告（**不落盘**）——唯一提交点的 staged 产物之一."""
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
        "duration_band": duration_band(),
        "status": "BLOCK" if (hard or visual or checker_failed) else "PASS",
    }
    attach_gate_metadata(payload, script, stage="step01.quality_gate")
    return payload


def _write_step01_quality_gate(script: dict, novel_text: str) -> dict:
    """Write the final structured quality-gate report.

    ⚠️ v6.103 起**生产路径不再调用它**——正式产物由唯一提交点
    （`_commit_step01_outputs`）按 staged → 自验 → 原子替换的顺序写，且
    `script_result.json` 是**最后**一个被替换的文件。本函数保留给既有的
    单点测试与离线复算（它一次写 quality + script 两个文件）。
    """
    payload = _build_step01_quality_gate(script, novel_text)
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


# ===========================================================================
# v6.103（2026-09-23 总计划阶段 4）：**唯一最终提交点**
# ===========================================================================
# 正常生成 / 缓存命中 / BEST-GUARD 恢复三条入口此前各写一遍落盘序列，时序互不
# 相同：缓存路径不写 quality/audit；BEST-GUARD 在 finalize 之后还能重生 hook；
# final semantic audit 只由离线工具 `tools/run_step01_final_audit.py` 补跑，
# 生产链路里根本没有「审计 → 提交」这一步。结果是 `step01_quality.json` PASS、
# 同 revision 的审计却有 4 条 blocking，`require_semantic_gate()` 必然拒绝。
#
# 现在只有一条提交路径，顺序固定（总计划 §3）：
#
#   候选（BEST-GUARD 只**选**）→ hook QC/regen → finalize → revision
#     → 确定性 quality → final semantic audit
#         ├─ 无 blocking → staged 三件套 → require_semantic_gate 自验
#         │                → audit → quality → script 依次替换 → commit manifest
#         │                → 正式路径再自验 + manifest 校验
#         └─ 有 blocking → 受限修复（≤2 轮，每轮重新 finalize+revision）
#                          → 仍有 blocking 就 BLOCK（不写三份产物）
#
# 「final audit 之后禁止任何语义写入」由两件事保证：① 提交点之后不再调用任何
# 修复/约束/post_fix；② audit 前后各算一次 revision，不等则本轮审计作废。
STEP01_AUDIT_SCOPE = "final_after_deterministic_fixes"
# v6.137（2026-09-26 用户决策）：2 → 4。§1.1 定 2 轮时修复轮还不可靠（零改善/
# new_blocking 死路一轮即弃）；v6.137 之后每轮都有验收闸兜底且实测连续净改善
# （resolved=4/6、new=0），2 轮不够把 24 条 blocking 收敛到 0（实测 24→21→13）。
# 4 轮 = 修复调用 ≤4 次 + 每轮 1 次新审计（脚本已变不可复用），成本约 +5 分钟/次。
STEP01_AUDIT_REPAIR_MAX_ROUNDS = 4


def _step01_run_id() -> str:
    """本轮运行身份（提交产物/台账/诊断/bank/调用记录都用它自证是哪一轮）.

    实现在 `shared.run_identity`（**单一来源**）：`STEP01_RUN_ID` 由跑批驱动注入
    （父子同身份），否则**进程内首次调用生成一次并缓存**。

    2026-10-02 复核：旧实现每次按**当前秒**重拼 `%Y%m%dT%H%M%S-<pid>`——同一轮里
    相隔一秒调用就得到两个不同 id（实测 `…183824-18820` / `…183825-18820`），
    于是「诊断」与「manifest」各写一个 id，产物无法自证同轮。缓存是硬要求。
    """
    from shared.run_identity import current_run_id
    return current_run_id()


def _step01_now() -> str:
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json_quiet(path) -> "dict | None":
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _audit_blocking_records(payload: "dict | None") -> list:
    """审计 payload 里的 blocking 条目（与 `require_semantic_gate` 同口径）."""
    return [
        issue for issue in ((payload or {}).get("issues") or [])
        if isinstance(issue, dict)
        and str(issue.get("severity") or "").strip() in ("hard", "visual_risk", "blocking")
    ]


#: 单条审计问题的成对目标**最多覆盖的镜头数** = 联动组上限（`_REPAIR_MAX_SHOTS_PER_GROUP`）。
#: 定义在这里而不是从 audit.py 反向 import：本文件是消费方，且审计侧只负责**声明**，
#: 上限属于修复侧的容量契约。两侧同值由 `test_audit_oversize_target_split.py` 锁定。
_AUDIT_MAX_TARGET_SHOTS = 6


def _split_oversize_repair_targets(rt, *, cap: int = _AUDIT_MAX_TARGET_SHOTS) -> list:
    """把成对目标按**镜头**分块（每块 ≤ `cap` 镜，块内保留该镜的全部目标）.

    v6.143（2026-09-27 十次连跑根因 P2）：实测 4/10 次 BLOCK 的直接原因是
    **单条审计问题**声明了 7/8/10/12/14 个镜头的成对目标，而修复侧联动组上限是 6
    且**拒绝拆组**（拆组=半条修复）→ `_linked_repair_groups` 判 `oversize` →
    不调 LLM、不再发审计，直接 BLOCK。

    取证（`docs/step01-ten-run-problem-report-2026-09-27.md` P2）：逐轮扫描审计台账里
    `repair_targets` 覆盖镜头数 > 6 的条目，`run 02/04/06/10` 四次**全部是单条声明**
    （7/7+12/14/8+10 镜），没有一次是多条合并出来的组。

    拆的**只是「一条问题的声明范围」**，不是「联动事务」：
      - 同一镜头内的多条目标**始终留在同一块里** → 镜内原子性（帧+布局的联动修）
        完全不变；
      - 跨镜目标本来就该按契约「每一镜各写一条」声明（见 `audit._AUDIT_RULES_TEMPLATE`
        的 repair_targets 段），模型写成一条时，拆条正是把它还原成契约形状；
      - 块与块之间是**独立**的可修单元（systemic 缺陷如「stage 取值非法遍布全篇」
        本来就逐镜独立），所以不存在「半条修复」。

    Returns:
        `[chunk, ...]`；`rt` 为 `None`（旧条目）/ `[]`（显式无目标）/ 块数 ≤ 1 时
        **原样**返回单元素列表——三态语义不在本函数里改变。
    """
    if not isinstance(rt, list) or not rt:
        return [rt]
    _by_shot: dict = {}
    for _t in rt:
        if not isinstance(_t, dict):
            continue
        _sid = str(_t.get("shot_id") or "").strip()
        _by_shot.setdefault(_sid, []).append(_t)
    if len(_by_shot) <= cap:
        return [rt]
    _keys = list(_by_shot)
    out: list = []
    for _i in range(0, len(_keys), cap):
        _chunk: list = []
        for _k in _keys[_i:_i + cap]:
            _chunk.extend(_by_shot[_k])
        out.append(_chunk)
    return out


def _audit_payload_to_issues(payload: "dict | None") -> list:
    """把审计 payload 里的 blocking 条目还原成 `Issue`（供受限修复轮消费）.

    v6.119 审核实测的**断点**：本函数此前**不传** `repair_targets`——模型就算正确输出
    了成对目标，进到修复轮时也已经丢了，授权重新退回旧的单字段 `field_path` 推导。
    整条成对授权链在生产提交路径上是**死的**（离线单测覆盖不到，因为它直接构造
    `Issue` 而不过这一跳）。

    `repair_targets` 的三态必须原样带走（`None`=旧条目 / `[]`=显式无目标 /
    非空=成对），**不能**用 `or []` 把前两者合并——那正是上一条要修的静默降级。

    v6.143（P2）：**单条声明超限（> `_AUDIT_MAX_TARGET_SHOTS` 镜）时按镜拆成多条
    Issue**（见 `_split_oversize_repair_targets`）。这里是审计 payload → `Issue` 的
    唯一转换点，也是联动组分组（`_linked_repair_groups`）的唯一输入源；不在这一跳拆，
    超限声明就会带着 7-14 个镜头进分组、被判 `oversize` 后整轮 BLOCK。
    """
    out: list = []
    _split_n = 0
    for rec in _audit_blocking_records(payload):
        _rt = rec.get("repair_targets")
        if _rt is not None and not isinstance(_rt, list):
            _rt = []          # 形状非法 → 按「显式无目标」处理（fail-closed）
        _chunks = _split_oversize_repair_targets(_rt)
        if len(_chunks) > 1:
            _split_n += len(_chunks) - 1
            _logger.warn(
                "[COMMIT] 审计问题 %s 的成对目标覆盖 %d 镜 > 上限 %d → 按镜拆成 %d 条"
                "（镜内目标保持整组；这是声明粒度，不是拆联动事务）"
                % (rec.get("code"), len({str(t.get("shot_id")) for t in _rt
                                        if isinstance(t, dict)}),
                   _AUDIT_MAX_TARGET_SHOTS, len(_chunks)))
        for _chunk in _chunks:
            out.append(Issue(
                code=str(rec.get("code") or "model_audit_issue"),
                severity="visual_risk",
                message=str(rec.get("message") or ""),
                shot_id=rec.get("shot_id"),
                frame_id=str(rec.get("frame_id") or ""),
                entity_id=str(rec.get("entity_id") or ""),
                field_path=str(rec.get("field_path") or ""),
                evidence=str(rec.get("evidence") or ""),
                repair_targets=_chunk,
                repairable=True,
            ))
    if _split_n:
        _metrics_inc("commit.audit_targets_split", _split_n)
    return out


def _audit_payload_to_relations(payload: "dict | None") -> dict:
    """审计 payload → `{表层称谓: identifier}`（§14 P2 的 `characters[].aliases` 窄协议）.

    只做**形状归一**，不做语义扩展：

      · 缺 `character_aliases`（旧响应）⇒ 空表 = **未声明任何身份关系**。消费者
        （`repair_effects.resolve_frame_actor_binding`）据此判 `ambiguous`，
        **不得**当成「是另一个实体」——「没写」与「写了没关系」是两件事。
      · `identifier` / `aliases` 缺失或类型不对 ⇒ **整条丢弃**（不猜意图）；
      · 同一表层写法被多条声明指向不同 identifier ⇒ **互相矛盾**，该写法整条丢弃
        （矛盾的关系比没有关系更危险：拿它裁决会静默选边）。丢弃次数打日志。
      · 表层写法等于 identifier 自身 ⇒ 无信息，丢弃。

    代码只**派生编号**（这里是「写法 → 身份」的映射），关系本身由生产者给。
    """
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("character_aliases")
    if not isinstance(raw, list):
        return {}
    out: dict = {}
    conflicts: list = []
    blocked: set = set()
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        ident = str(item.get("identifier") or "").strip()
        aliases = item.get("aliases")
        if not ident or not isinstance(aliases, list):
            dropped += 1
            continue
        for a in aliases:
            if not isinstance(a, str):
                dropped += 1
                continue
            name = a.strip()
            if not name or name == ident:
                dropped += 1
                continue
            if name in blocked:
                continue
            if name in out and out[name] != ident:
                conflicts.append((name, out[name], ident))
                out.pop(name, None)
                blocked.add(name)
                continue
            out[name] = ident
    if dropped:
        _logger.info("[AUDIT] 身份关系声明丢弃 %d 项（形状不合法）" % dropped)
    if conflicts:
        _logger.warn("[AUDIT] 身份关系声明**互相矛盾**！同一个称谓被指向不同角色，"
                     "已整条丢弃（不猜）：%s" % conflicts[:4])
    return out


def _target_of_record(_rec) -> str:
    """审计条目（dict 或 `Issue`）→ 事务 target 名（`shot_5` / `hook`）.

    **唯一**口径在 `repair_fields._target_id_of`。这里此前是第二份实现
    （`f"shot_{_ai.shot_id}"`）——2026-09-25 实测的 BLOCK 正好是两份实现
    「改一处漏一处」的代价：模型第 2 轮把 `shot_id` 写成 `"shot5"`，归一必须
    同时覆盖**成对目标**与**问题定位**两处，任何一处漏掉都会让授权/落点错位。
    """
    from step01_script_gen.repair_fields import _target_id_of
    _sid = (_rec.get("shot_id") if isinstance(_rec, dict)
            else getattr(_rec, "shot_id", None))
    return _target_id_of(_sid)


def _audit_repair_target_problems(audit: "dict | None", shots_by_id: dict) -> list:
    """审计 blocking 条目的**成对目标契约**是否可执行（v6.119，用户口径）.

    三类都算「不可执行」：

      ① 缺 `repair_targets` —— 契约未遵守（schema 里它是 required）；
      ② 显式 `[]` —— 该 blocking 问题没有可授权的定向修复；
      ③ 任一条目标无效（镜头不存在 / 路径不是该镜的真实字段）—— **整条**拒绝，
         不允许留下半组继续修。

    只查 blocking 条目：advisory/info 不驱动定向修复，不构成阻塞。

    返回可读原因（空列表 = 全部条目契约可执行）。

    **v6.142（09-26 用户决定，P1-3）：调用方不再据此就地 BLOCK**。旧口径
    「有一条不可执行就整轮 BLOCK、一轮修复都不发」被 20:25 run 证伪——一条坏
    目标（shot 3 的 12 条越界路径）把同轮其它可修目标全部连坐。现在调用方只
    **告警 + 保留 blocking**，本轮不授权这条、其余目标照常修；改不动的终局由
    「提交前修复轮零改善」判据给出。所以本函数是**诊断**，不是**判决**。
    """
    from step01_script_gen.repair_fields import (validate_repair_targets,
                                                 _is_structural_code)
    out: list = []
    for rec in _audit_blocking_records(audit):
        code = str(rec.get("code") or "model_audit_issue")
        tag = f"shot={rec.get('shot_id')} code={code}"
        if "repair_targets" not in rec:
            out.append(f"{tag}：缺 `repair_targets`（schema required）——审计未按契约输出")
            continue
        declared = rec.get("repair_targets")
        if not isinstance(declared, list) or not declared:
            out.append(f"{tag}：`repair_targets` 为空——该 blocking 问题"
                       f"没有可授权的定向修复目标")
            continue
        _valid, rejected = validate_repair_targets(
            declared, shots_by_id, structural=_is_structural_code(code))
        if rejected:
            out.append(f"{tag}：{len(rejected)} 条目标无效（{rejected[:3]}）——整条拒绝")
    return out


def _terminal_blocking_records(audit: "dict | None",
                              blocking: list) -> tuple:
    """终局 BLOCK 的 manifest 记录：审计**调用失败**时丢弃陈旧清单.

    v6.152（C1，2026-09-29 五次跑实测）：终局若是审计调用失败（超时 / 传输耗尽 /
    解析耗尽），`blocking` 还停在**上一次成功审计那一轮**的清单上——run 02 的
    manifest 因此报 22 条、run 04 报 6 条，而真因只是「这次审计没跑出来」。
    调用失败 **不等于** 稿件有这些语义缺陷，把它当成 blocking 清单会同时误导读数
    （下游按 code 统计）与污染欠账台账。

    返回 `(blocking_for_manifest, audit_status, audit_call_failed)`：

    - 审计跑出结论（`status == "ok"`）或本 run **根本没调用审计**
      （台词完整性门直接判定 → `audit is None`）：原样返回，`audit_call_failed=False`；
    - 审计调用失败：返回**只含本次失败**的一条记录，并置 `audit_call_failed=True`，
      由调用方决定是否把原清单降级为诊断字段、以及**跳过欠账登记**。
    """
    _status = (str((audit or {}).get("status") or "").strip().lower()
               if isinstance(audit, dict) else "")
    if not audit or _status == "ok":
        return blocking, _status, False
    return ([{
        "code": "audit_call_failed",
        "severity": "blocking",
        "shot_id": None,
        "transient": True,
        "message": ("final semantic audit %s（调用失败 != 0 issue；"
                    "本条为本次终局原因，不是稿件缺陷清单）" % _status),
    }], _status, True)


def _quality_blocking_records(quality: "dict | None") -> list:
    """确定性质量门里的 blocking 条目（与 `require_gate` 同口径）.

    v6.103（第二十一次重跑实测）：提交循环此前**只看审计 blocking**，确定性
    quality 为 BLOCK 时既不进修复轮、也不写进 manifest —— 结果一条 9 条阻塞的
    稿子一路走到 staged 自验才被拦下，`blocking_count` 还报 0，原因写成
    「staged 自验失败」。「质量门阻塞」和「审计阻塞」都是 blocking，必须在
    同一处判定、同一处记账（否则日志与 manifest 都在误导）。
    """
    q = quality or {}
    records = [r for r in (q.get("hard") or []) if isinstance(r, dict)]
    records += [r for r in (q.get("visual_risk") or []) if isinstance(r, dict)]
    if q.get("checker_failed"):
        records.append({"code": "CHECK_FAILED", "severity": "hard",
                        "shot_id": None, "message": "质量检查器异常"})
    return records


# 定向审查项的**模板**（v6.111，审核 P2-5）：`_final_audit_focus` 从这里取文案，
# 版本哈希随之自动跟随——手写「版本号记得改」是迟早会忘的约定。
_AUDIT_FOCUS_TEMPLATES: tuple = (
    "逐镜检查上一镜 LF → 本镜 FF → 全部 KF → 本镜 LF → 下一镜 FF 的状态链；"
    "最多返回 20 条，证据简洁。",
    "检查武器/道具释放后是否持续释放，直到明确重新获得。",
    "检查是否存在动作重复、遗漏、未来状态提前或无事件依据的动作补充。",
    "未决项必须保留并给出原文证据，不得为了通过门禁而删除证据。",
)


def _audit_focus_version() -> str:
    """final audit 定向审查项的版本（模板 + 动态段拼装规则）."""
    import hashlib
    import json as _json
    blob = _json.dumps(
        {"templates": list(_AUDIT_FOCUS_TEMPLATES),
         "dynamic": "quality.visual_risk 镜头号清单（本节内容随本轮 blocking 变化）",
         "limit": 8},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _final_audit_focus(script: dict, quality: "dict | None") -> list:
    """final audit 的定向审查项.

    **不写死任何镜头号**（换小说代码不用改）：通用状态链检查 + 从本轮确定性
    blocking 里动态取出的「重点镜头」。
    """
    focus = list(_AUDIT_FOCUS_TEMPLATES)
    performance = [str(rec.get("shot_id"))
                   for rec in (quality or {}).get("advisory") or []
                   if rec.get("code") == "emotion_visualization"]
    if performance:
        focus.append(
            "表演语义复核（镜头 %s）：词表未命中示例动作仅是候选，不能据此判错。"
            "区分外貌/发式修饰与当前表演；按人物及对应帧核对是否已有具体可见的"
            "表情或动作，不要求固定措辞。确实只有抽象表演而无可执行画面时，"
            "按实际影响报告问题、原字段证据及成对修复目标。"
            % "、".join(dict.fromkeys(performance)))
    ids: list = []
    for rec in (quality or {}).get("visual_risk") or []:
        sid = rec.get("shot_id")
        if sid is None:
            continue
        key = "hook" if str(sid) in ("0", "hook") else str(sid)
        if key not in ids:
            ids.append(key)
    if ids:
        focus.append(
            "重点核对镜头 %s 的跨字段一致性（在场状态 / 道具持有 / 帧阶段 / "
            "结果动作支撑）。" % "、".join(ids[:8])
        )
    return focus


def _final_audit_metadata(script: dict, *, run_id: str = "") -> dict:
    """final audit 的审查元数据（**唯一源**）.

    请求构造（`_run_final_semantic_audit`）与**请求身份**（`_final_audit_identity`）
    必须用同一份——元数据会**逐字进提示**，两边各写一份就会让"身份相同"与
    "请求逐字相同"分家，而复用判据的全部依据就是后者。
    """
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.audit import AUDIT_TEMPERATURE, audit_prompt_version

    return {
        "input_revision": compute_script_revision(script),
        "rule_version": RULE_VERSION,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "duration_band": duration_band(),
        # v6.112：生成/修复提示词版本——**诊断字段**，不参与复用判定
        # （改提示词改变的是稿件内容，内容变化已由 revision 覆盖）。
        "prompt_source_fingerprint": PROMPT_SOURCE_FINGERPRINT,
        "audit_scope": STEP01_AUDIT_SCOPE,
        # v6.111（审核 P2-5 / 总计划 §1.3）：模型、温度与 prompt 版本都要进元数据，
        # 并参与复用判定——只比 revision + 规则指纹 + scope 时，改了审计提示词
        # 旧结论仍会被复用。
        "audit_prompt_version": audit_prompt_version(),
        "audit_focus_version": _audit_focus_version(),
        "audit_temperature": AUDIT_TEMPERATURE,
        "run_id": run_id,
    }


def _final_audit_identity(script: dict, novel_text: str, plan: "dict | None",
                          focus: "list | None", model: "str | None",
                          run_id: str) -> str:
    """本次即将发出的审计请求的身份（见 `audit.final_audit_request_identity`）."""
    from step01_script_gen.audit import final_audit_request_identity

    return final_audit_request_identity(
        novel_text, plan, script, audit_focus=focus,
        metadata=_final_audit_metadata(script, run_id=run_id), model=model)


def _memoizable_final_audit(audit: "dict | None") -> "dict | None":
    """可进「循环内复用」memo 的审计结论——**只有真的跑出结论的**才返回原对象.

    调用失败（超时 / 请求失败 / 解析失败 / 预算耗尽）一律返回 `None`：失败**不能**
    冒充可复用结论（复核之十九 P1 "失败审计不能冒充可复用结论"）。抽成独立函数是
    为了让这条判据**可被直接断言**——写在循环里就只能靠"构造一个走不到的路径"来测。
    """
    if not isinstance(audit, dict):
        return None
    if str(audit.get("status") or "").strip().lower() != "ok":
        return None
    if not duration_band_matches(audit):
        return None
    return audit


def _run_final_semantic_audit(script: dict, novel_text: str, plan: "dict | None", *,
                              model: "str | None" = None, focus: "list | None" = None,
                              run_id: str = "") -> dict:
    """对**当前不可变对象**跑一次 final semantic audit，返回完整 payload.

    契约（总计划 §4.3）：`status / model / error / issues / input_revision /
    rule_source_fingerprint / audit_scope` 必需；`created_at` / `run_id` 为诊断
    字段，不参与准入语义。
    """
    from step01_script_gen.audit import run_same_model_audit_with_status

    metadata = _final_audit_metadata(script, run_id=run_id)
    _metrics_inc("calls.final_audit")
    _allow, _deadline, _to_info = _audit_budget_allowance()
    _logger.info("[AUDIT] 本次审计可用 %ss（%s）｜总预算剩余 %s 秒｜截止时刻 %s"
                 % (_allow, _to_info.get("why") or "-",
                    _to_info.get("left") if _to_info.get("left") is not None else "未知",
                    ("不限" if _deadline is None else "now+%ss" % _allow)))
    result = run_same_model_audit_with_status(
        novel_text, plan, script, model=model,
        timeout_sec=int(_to_info.get("cap") or AUDIT_TIMEOUT_CAP_SEC),
        deadline_ts=_deadline,
        audit_focus=focus or _final_audit_focus(script, None),
        metadata=metadata,
    )
    # v6.134：parse_failed 有界重试后的**真实**调用次数（audit.AuditResult.attempts）。
    # 仅 ≠1 时进产物——单次成功的 payload 形状与历史完全一致（同 raw_text_head 口径）。
    _attempts = int(getattr(result, "attempts", 1) or 1)
    # v6.117（方案 A）：审计可能走**专用**通道（`AUDIT_TEXT_PROVIDER`），此时调用方
    # 传来的 `model` 是生成侧的名字、并非实际提交的模型。产物与台账必须记**实际**值，
    # 否则「这份审计由哪个通道/模型产出」在事后无从核对。
    _deviations = [str(d) for d in (getattr(result, "schema_deviations", None) or [])]
    if _deviations:
        _logger.warn("[AUDIT] 响应体与 AUDIT_SCHEMA 有偏离（已归一，结论仍可用）: "
                     + "；".join(_deviations))
    return {
        **metadata,
        "status": result.status,
        "model": getattr(result, "channel_model", "") or model,
        "channel_provider": getattr(result, "channel_provider", "") or "",
        "schema_deviations": _deviations,
        # v6.118：provider 自报用量原样落盘（取不到 = None，**不填估算**）
        "usage": getattr(result, "usage", None),
        "error": result.error,
        "issues": [i.to_dict() for i in result.issues],
        # §14 P2（2026-10-03）：**身份关系声明**（`characters[].aliases` 窄协议）。
        # 审计说「某称谓与某已在场人物是重复实体」时必须把关系写出来，否则修复端
        # 只剩「照做」或「猜」两条路——实测第 21 轮 ch2 就是「模型删对了、约束又加
        # 回来、却被记成没修好」。空表 = 未声明（旧响应缺该键的兼容读法）。
        "character_aliases": list(getattr(result, "character_aliases", None) or []),
        "created_at": _step01_now(),
        # v6.133（2026-09-26 实跑 BLOCK：final audit parse_failed，00:58 那次重跑）：
        # 失败时**必须**留下原始响应头——`AuditResult.raw_text` 本来带着原文，但原先
        # 没进 payload，台账里只剩一句 "not valid JSON or lacks issues[]"，于是
        # 「根本不是 JSON」与「是 JSON 但形状不符」这两种**处置完全不同的**故障
        # 事后无从区分（前者要改通道/提示词，后者要改 shape 判据）。
        # 同"修复调用失败"的同一标准：失败原因进结构化诊断，而不是只活在 step01.log。
        **({"raw_text_head": str(getattr(result, "raw_text", "") or "")[:_DIAG_RAW_HEAD_MAX],
            "raw_text_len": len(str(getattr(result, "raw_text", "") or ""))}
           if result.status != "ok" else {}),
        # v6.134：重试发生时记录真实调用次数（≠1 才出现，单次调用产物形状不变）
        **({"attempts": _attempts} if _attempts != 1 else {}),
        # v6.134.1（审核）：被重试覆盖的失败尝试留档（坏响应头部/总长/该次用量）——
        # 仅非空时出现；「重试后成功」的 payload 不再丢失坏响应证据
        **({"failed_attempts": list(getattr(result, "failed_attempts", None) or [])}
           if getattr(result, "failed_attempts", None) else {}),
        # A3（复核 2026-10-04 第三轮）：**授权元数据补全**那次调用的完整记录
        # （`call_id` / 成败 / 响应头与长度 / 用量 / 请求与补上条数，或 `{"skipped": 原因}`）。
        # 只在真的走到这一步时出现——没缺字段的轮次 payload 形状与历史完全一致。
        **({"completion": getattr(result, "completion", None)}
           if getattr(result, "completion", None) is not None else {}),
    }


_AUDIT_DIAG_NAME = "step01_audit_rounds.json"
# 台账保留条数：单条约等于一次审计的完整 issue 列表（约 10KB），40 条 ≈ 400KB 封顶。
_AUDIT_DIAG_KEEP = max(5, int(os.getenv("AUDIT_DIAG_KEEP", "40") or "40"))


def _read_json_list_quiet(path) -> list:
    """读一个 JSON 数组；缺失/损坏/不是数组一律当空表（台账是诊断文件）."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _active_text_provider() -> str:
    """本轮审计**实际**走的文本通道（TEXT_PROVIDER），用于区分通道级差异."""
    try:
        from _text_gen import config as _text_cfg
        return str(getattr(_text_cfg, "PROVIDER", "") or "")
    except Exception:
        return ""


def _record_audit_diag(audit: "dict | None", *, phase: str, round_index: int,
                       reused: bool = False, run_id: str = "",
                       script: "dict | None" = None,
                       reuse_source: str = "") -> None:
    """每轮审计的诊断台账（v6.112，总计划 §1.3 观测点）.

    为什么需要它：正式产物 `step01_audit.json` 只保留**最后一轮**结论，修复轮之间
    到底发生了什么无从区分——同一个 shot 从 blocking 变 advisory，可能是
    （i）关联镜头的修改顺带修好了它，也可能是（ii）同一份内容两次审计给出不同
    severity。审计 payload 此前不落盘，日志只打 blocking 计数与 (shot, code)，
    没有 issue 文本，两种解释因此**无法区分**。

    本台账把每轮审计的 轮次 / 输入 revision / 完整 issue / 模型与实际 provider
    追加落盘到 `step01_audit_rounds.json`：**不覆盖**正式三份产物，也**不参与**
    任何准入或复用判定。写失败只告警。
    """
    _payload = audit if isinstance(audit, dict) else {}
    issues = [i for i in (_payload.get("issues") or []) if isinstance(i, dict)]
    _blocking = [i for i in issues
                 if str(i.get("severity") or "").strip() in ("hard", "visual_risk", "blocking")]
    rec = {
        "recorded_at": _step01_now(),
        "run_id": run_id or str(_payload.get("run_id") or ""),
        "phase": phase,
        "round": int(round_index),
        "reused": bool(reused),
        "status": str(_payload.get("status") or ""),
        "error": _payload.get("error"),
        "model": _payload.get("model"),
        # v6.117（方案 A）：审计可能走**专用**通道（`AUDIT_TEXT_PROVIDER`），此时全局
        # `TEXT_PROVIDER` 不是审计实际用的通道。优先记该次审计自报的通道，没有才退回
        # 全局值——未配置专用通道时行为与之前完全一致。
        "provider": (str(_payload.get("channel_provider") or "").strip()
                     or _active_text_provider()),
        "input_revision": _payload.get("input_revision"),
        "rule_source_fingerprint": (_payload.get("rule_source_fingerprint")
                                    or RULE_SOURCE_FINGERPRINT),
        "duration_band": _payload.get("duration_band"),
        "prompt_source_fingerprint": PROMPT_SOURCE_FINGERPRINT,
        "audit_scope": _payload.get("audit_scope"),
        "audit_prompt_version": _payload.get("audit_prompt_version"),
        "audit_focus_version": _payload.get("audit_focus_version"),
        "audit_temperature": _payload.get("audit_temperature"),
        "issue_count": len(issues),
        "blocking_count": len(_blocking),
        # v6.117（方案 A）：通道是否真的在守 schema 的可核对证据（归一化前的偏离）
        "schema_deviations": [str(d) for d in (_payload.get("schema_deviations") or [])],
        # A3：补全调用的记录（见 `_audit_payload`）——只在该轮真的走到补全时出现
        **({"completion": _payload.get("completion")}
           if _payload.get("completion") is not None else {}),
        # v6.118：本轮的 token 用量（provider 自报）。**取不到就是 None**——不填估算，
        # 否则「实际消耗」与「我们的猜测」在台账里再也分不开。
        "usage": _payload.get("usage"),
        "blocking": [(str(i.get("shot_id")), str(i.get("code"))) for i in _blocking][:20],
        "issues": issues,
    }
    # v6.133：审计**失败**轮带上原始响应头与总长（成功轮不带这两个键）；
    # v6.134：parse_failed 重试发生时带真实调用次数（payload 仅 ≠1 时携带）；
    # v6.134.1：被重试覆盖的失败尝试留档（payload 仅非空时携带）
    for _k in ("raw_text_head", "raw_text_len", "attempts", "failed_attempts"):
        if _payload.get(_k) is not None:
            rec[_k] = _payload.get(_k)
    # v6.153（复核之十九 P1）：复用**来源**——`in_loop` = 本次提交循环里沿用上一轮
    # 已完成的同身份审计（未发生调用）；`disk` = 沿用盘上正式产物/sidecar 的结论。
    # 只在复用轮出现，成功的新调用轮不带这个键（形状与历史一致）。
    if reuse_source:
        rec["reuse_source"] = str(reuse_source)
    if not rec["input_revision"] and script is not None:
        try:
            from shared.quality_gate import compute_script_revision
            rec["input_revision"] = compute_script_revision(script)
        except Exception:
            pass
    try:
        from shared.json_io import atomic_dump_json
        path = CHAPTER_DIR / _AUDIT_DIAG_NAME
        rows = _read_json_list_quiet(path)
        rows.append(rec)
        atomic_dump_json(path, rows[-_AUDIT_DIAG_KEEP:], ensure_ascii=False, indent=2)
    except Exception as exc:
        _logger.warn(f"[AUDIT] 每轮审计台账写入失败（不影响结论）: {exc}")


# 失败响应留档上限（字符）：只留头部，够判"是不是 JSON / 断在哪"即可，避免台账膨胀。
_DIAG_RAW_HEAD_MAX = 2000

_REPAIR_DIAG_NAME = "step01_repair_rounds.json"
# 保留条数：单条约 1KB，与审计台账同样封顶
_REPAIR_DIAG_KEEP = max(5, int(os.getenv("REPAIR_DIAG_KEEP", "40") or "40"))


def _record_repair_diag(*, run_id: str, attempt: int, records: list) -> None:
    """定向修复事务的逐轮诊断台账（v6.119，2026-09-25 用户决定）.

    为什么单独一个文件、而不并进 `step01_audit_rounds.json`：两者粒度不同
    （审计轮 vs 修复轮），且审计台账的字段契约已被既有测试锁定；混进同一数组会
    逼消费方按形状过滤。

    v6.133（用户 2026-09-25~26）：台账里现在有**四类**记录，靠 `kind` 区分——
    `call_ok`（成功调用，带 max_tokens 与 reasoning/output token 用量）/
    `call_failed`（调用异常或响应被截断：整份丢弃、稿件没变）/
    `patch_unusable`（响应完整但补丁不可用：解析失败 / 结构无效 / 空补丁 /
    全被校验拒）；其余（有 `accepted`/`resolved` 字段的）是修复事务本身的逐轮诊断。
    三类调用记录此前都没有，BLOCK 的直接原因只会被外层记成"零改善"。

    为什么必须有它：2026-09-25 离线取证实测——一次真实重跑只留下 `new=3` 这个
    **计数**。`new_blocking_keys` 原本只在「整轮回退」分支打印，而该轮走的是
    「**按目标回退**」：`_decision` 被回退后的决策覆盖（其 keys 已是空列表），
    那条日志再也不会执行 → 「哪三条新阻塞」事后无从回答。本台账把**回退前**捕获的
    keys、落点目标、被回退的目标与联动组、修复前后的 revision 一起留下来。

    与审计台账同样的边界：**不覆盖**正式三份产物、不参与任何准入判定；写失败只告警。
    """
    if not records:
        return
    try:
        from shared.json_io import atomic_dump_json
        path = CHAPTER_DIR / _REPAIR_DIAG_NAME
        rows = _read_json_list_quiet(path)
        for _rec in records:
            rows.append({"recorded_at": _step01_now(), "run_id": run_id,
                         "commit_round": int(attempt), **dict(_rec)})
        atomic_dump_json(path, rows[-_REPAIR_DIAG_KEEP:], ensure_ascii=False, indent=2)
    except Exception as exc:
        _logger.warn(f"[REPAIR] 修复轮诊断台账写入失败（不影响结论）: {exc}")


def _reusable_final_audit(script: dict) -> "dict | None":
    """旧审计可复用的条件：**revision + 规则指纹 + audit scope 全部相同且为 ok**.

    总计划 §1.1 的既定决策。三者任一不同都必须重新审计——否则等于拿旧规则/旧稿
    的结论给新稿盖章。`created_at`/`run_id` 只是诊断字段，不参与复用判定。
    """
    from shared.quality_gate import compute_script_revision

    revision = compute_script_revision(script)
    # v6.109（总计划 §1.3「同一 revision 不通过反复调用 full audit 获取一次偶然
    # PASS」）：BLOCK 时审计其实**已经跑过**并给出了未决条目，只是没写成正式产物。
    # 若下一次运行又拿到同一 revision，重新调用全量审计就是抽签——两次审计结论不同
    # 只因模型波动，而不是因为稿子变好了。所以除正式产物外，也读 BLOCK 时留下的
    # sidecar，把它当作**本轮未决清单**复用：让修复轮去修这些条目，而不是再掷一次骰子。
    # 修复真正落地后 revision 会变，下一次审计自然是对新对象的全新调用。
    for name in ("step01_audit.json", "step01_audit.blocked.json"):
        audit = _read_json_quiet(CHAPTER_DIR / name)
        if not audit:
            continue
        # 只复用**真的跑出结论**的审计；调用失败（超时/解析失败）不得被冻结复用。
        if str(audit.get("status") or "").strip().lower() != "ok":
            continue
        if str(audit.get("input_revision") or "").strip() != revision:
            continue
        if str(audit.get("rule_source_fingerprint") or "").strip() != RULE_SOURCE_FINGERPRINT:
            continue
        if not duration_band_matches(audit):
            continue
        if str(audit.get("audit_scope") or "").strip() != STEP01_AUDIT_SCOPE:
            continue
        # v6.111：提示词/focus 版本也要一致——否则「改了审计提示词」之后旧结论
        # 仍会被当成有效（审核 P2-5）。
        from step01_script_gen.audit import audit_prompt_version as _apv
        if str(audit.get("audit_prompt_version") or "") != _apv():
            continue
        if str(audit.get("audit_focus_version") or "") != _audit_focus_version():
            continue
        # v6.117（方案 A）：审计**通道**也是被审对象的一部分——配了审计专用通道后，
        # 旧通道（agent_plan，schema 被降级成 json_object）产出的结论不得复用，否则
        # 切换通道在「同 revision」（缓存命中 / BEST-GUARD 路径）上**完全不生效**：
        # 稿子没变 → revision 相同 → 直接拿旧通道的结论盖章。
        # 未配置专用通道时不加这条判据：与既有复用规则逐字一致（零行为变化）。
        from step01_script_gen.audit import resolve_audit_channel as _rac
        _chan_provider, _chan_model = _rac()
        if _chan_provider is not None:
            if str(audit.get("channel_provider") or "").strip().lower() != _chan_provider:
                continue
            if str(audit.get("model") or "").strip() != _chan_model:
                continue
        return audit
    return None


def _clear_staged_dir() -> None:
    """清掉 `_staged/`（v6.103，第二十二次重跑实测）.

    `_staged/` 只在**自验通过那一刻**才有意义（它的内容与提交后的正式产物一致）。
    上一轮 BLOCK 留下的 staged 副本会在章节目录里滞留，看起来像「权威副本」——
    实测就是这样：run 2 在 staged 自验失败留下的 `_staged/step01_quality.json`
    在 run 3 BLOCK 之后仍然存在。BLOCK 路径一律清掉，诊断证据改由 BLOCK
    manifest（含 blocking 明细）+ snapshots/ 承担。
    """
    import shutil as _shutil
    try:
        _shutil.rmtree(CHAPTER_DIR / "_staged", ignore_errors=True)
    except Exception:
        pass


def _write_blocked_audit_sidecar(audit: "dict | None") -> None:
    """BLOCK 时把本轮审计结论落成 sidecar（v6.109）.

    正式产物 `step01_audit.json` 在 BLOCK 时**不能**替换（它必须与另外两份产物
    同一 revision）。但审计确实跑过了，结论必须留下来：否则下一次运行拿到同一
    revision 时会重新调用一次全量审计——那就是「反复抽样博一次偶然 PASS」
    （总计划 §1.3）。sidecar 只被 `_reusable_final_audit` 读，不参与准入。
    """
    if not isinstance(audit, dict) or audit.get("input_revision") is None:
        return
    try:
        from shared.json_io import atomic_dump_json
        atomic_dump_json(CHAPTER_DIR / "step01_audit.blocked.json", audit,
                         ensure_ascii=False, indent=2)
    except Exception as exc:
        _logger.warn(f"[COMMIT] BLOCK 审计 sidecar 写入失败（不影响结论）: {exc}")


def _write_blocked_manifest(revision: str, *, run_id: str, detail: str,
                            blocking: list, audit_status: str = "") -> None:
    """BLOCK 时只写**带 run id 的诊断 manifest**，不写三份产物.

    写 BLOCK manifest（而不是留着上一次 COMMITTED manifest）是刻意的：留着旧
    manifest 会让下游以为「上一版仍然有效」，于是带着本轮未解决的 blocking 继续
    生图。`verify_step01_commit_manifest` 见 `status != COMMITTED` 直接拒绝。
    """
    from shared.quality_gate import (build_step01_commit_manifest,
                                     write_step01_commit_manifest)
    try:
        manifest = build_step01_commit_manifest(
            run_id=run_id, created_at=_step01_now(), revision=revision,
            rule_source_fingerprint=RULE_SOURCE_FINGERPRINT,
            rule_version=RULE_VERSION, audit_scope=STEP01_AUDIT_SCOPE,
            prompt_source_fingerprint=PROMPT_SOURCE_FINGERPRINT,
            chapter_dir=CHAPTER_DIR, status="BLOCK",
            blocking=blocking, detail=detail, audit_status=audit_status)
        write_step01_commit_manifest(CHAPTER_DIR, manifest)
    except Exception as exc:
        _logger.warn(f"[COMMIT] BLOCK manifest 写入失败（不影响结论）: {exc}")


def _commit_step01_outputs(script: dict, novel_text: str, *,
                           plan: "dict | None" = None,
                           hook_ctx: "dict | None" = None,
                           audit_model: "str | None" = None,
                           label: str = "落盘前",
                           source_revision: str = "",
                           source_aliases: "dict | None" = None) -> tuple:
    """**唯一最终提交点**：三条入口（正常 / 缓存 / BEST-GUARD）都只走这里.

    `source_revision` / `source_aliases`：BEST-GUARD 选中的**历史快照自身**的
    revision 与各版本身份（选稿边界算好带过来的，v6.129 / 2026-09-30）。它可能与
    本函数入口的 revision 不同（大纲覆盖会改哈希），也不等于修后 revision——
    BLOCK 时三者都要登记，否则选稿那边永远查不到这份欠账。

    Returns:
        `(script, summary)`。`summary["status"]` 是 `COMMITTED` / `BLOCK`；
        BLOCK 时正式三份产物**未被替换**，只写了 BLOCK manifest 与诊断快照。
    """
    from shared.quality_gate import (SCRIPT_REVISION_VERSION,
                                     build_step01_commit_manifest,
                                     compute_script_revision,
                                     ensure_script_revision,
                                     require_semantic_gate,
                                     verify_step01_commit_manifest,
                                     write_step01_commit_manifest)

    run_id = _step01_run_id()
    summary: dict = {"status": "BLOCK", "run_id": run_id, "audit_repair_rounds": 0,
                     # v6.133：修复轮结论（improved / no_progress / call_failed /
                     # patch_unusable / oversized_group）——"零改善"不再吞掉
                     # "修复调用失败"与"响应不可用"这两类
                     "repair_outcome": "",
                     "blocking": [], "reused_audit": False, "detail": ""}
    _metrics_inc("commit.attempts")

    # v6.125（用户 2026-09-25 决定，第 1 项）：BLOCK 时必须**同时**登记「进入提交点
    # 的原稿 revision」。
    #
    # 只登记修后 revision 的后果（实测 run 193736）：台账新增的是修后的
    # `1d1cb454`，而被 BEST-GUARD 选中的 `script_result_20260924_215530.json`
    # 自身 revision `f434e5090d7b` **不在台账内** → 下一轮选稿照样可以选中它 →
    # 又 BLOCK 一次。台账的本意（"让选稿避开已知欠账的旧稿"）在这条路径上落空。
    #
    # 2026-09-27（用户裁决 4）：`_cross_run_best_guard` 里「全部候选都欠账 →
    # 放开限制」的兜底分支**已删除**——判据指纹未变的欠账稿不再被放回候选池
    # （没有合格历史候选时由新稿进提交门）。这条登记因此是**硬排除**，不再是
    # 只在「有别的候选时」生效的软护栏。
    _entry_revision = ""
    _entry_aliases: dict = {}
    try:
        # 2026-09-30 复核第 4 轮 P1：别名要在**这一时点**算（此刻的 `script` 是入口
        # 原稿）——后面 `script` 会被修复轮替换，再算就对不上这份欠账了。
        _entry_aliases = _revision_aliases_or_empty(script)
        _entry_revision = str(_entry_aliases.get(SCRIPT_REVISION_VERSION) or "")
    except Exception as _e:
        _logger.warn(f"[COMMIT] 进入提交点的 revision 计算失败（欠账台账少记一条）: {_e}")
    summary["entry_revision"] = _entry_revision

    # 统一收口：缓存稿和 BEST-GUARD 历史快照可能仍含旧钩子。
    if not HOOK_ENABLED:
        script.pop("hook", None)

    # ---- 1. hook QC / 必要的 hook regen（允许语义写入的最后阶段之前）----
    if hook_ctx is not None and isinstance(script.get("hook"), dict):
        try:
            _ensure_hook_quality(script, novel_text=novel_text, **hook_ctx)
        except Exception as exc:
            _logger.warn(f"[COMMIT] 钩子质量门异常（继续提交，问题会在审计里暴露）: {exc}")

    # §14 P3：**已通过验收的布局效果**跨 attempt 累积，供下面 `_finalize` 那一趟
    # `is_final_pass=True` 的 hard-fix 复用——「本轮已授权删除的条目不得按类别别名
    # 重建」这条规则因此要在**两个 pass** 上都成立，而不是只在修复轮内成立。
    # （必须声明在 `_finalize` **之前**：闭包按调用时解析，而 `_finalize` 在下面
    #   就被调了一次。）
    _effect_state: dict = {"effects": [], "bindings": {}, "revision": ""}

    def _finalize(_s):
        # §14：把「已通过验收的布局效果」交给 finalize 那趟 hard-fix。没有效果时
        # `repair_effects=None` ⇒ 约束行为**逐字不变**（三条入口共用本函数）。
        _view = None
        if _effect_state.get("effects"):
            try:
                from step01_script_gen.constraints.types import RepairEffectView
                _view = RepairEffectView(
                    round_id="finalize", source_revision=_effect_state.get("revision", ""),
                    effects=tuple(_effect_state["effects"]),
                    bindings=dict(_effect_state.get("bindings") or {}))
            except Exception as _eff_view_err:
                _logger.warn(f"[COMMIT] 布局效果视图构建失败（finalize 按无效果处理）:"
                             f" {_eff_view_err}")
                _view = None
        _s, _errs = finalize_step01_script(_s, novel_text=novel_text, label=label,
                                           repair_effects=_view)
        if _errs:
            _logger.warn(f"[COMMIT] {label} 最终时间轴归一告警: {'; '.join(_errs[:3])}")
        # 身份与审计必须覆盖实际落盘的投影；序列化之后不能再改变语义 payload。
        # 原始运行证据由独立台账保存，既有下划线字段不入盘约定保持不变。
        _s = _strip_runtime_fields(_s)
        ensure_script_revision(_s)
        return _s

    script = _finalize(script)

    audit = None
    quality = None
    blocking: list = []
    # v6.135（工作包 sunny-kindling-wilkinson，审核约束③）：提交层**二次校验**（辅门，
    # 防单候选/缓存路径绕过候选层门）。位置契约：hook QC、finalize 等**所有可能改动
    # narration 的步骤之后**、最终提交判定之前。破损 ⇒ 不进审计/修复轮（修复按锁定
    # 契约不可修 `narration_segment`，09-26 02:30 实损：locked_changed 死锁），
    # BLOCK 原因记 `dialogue_truncated`（与 locked_changed/零改善区分，决策点 2 口径：
    # 这是生成稿完整性问题，不触发计划重选语义）。
    _dlg_skip_audit = False
    # v6.135：门只核「稿件 vs 本轮计划的选材台词」。**计划覆盖面（三审 P2-1 更正）**：
    # 生成/快照两入口的计划经 `check_plan_before_generation` 保证 dialogue_lines 非空；
    # **缓存入口的旧缓存可能没有 chapter_outline——此时门不启用（兼容取舍，显式记录），
    # 这类稿件不受台词门保护**，不得引申为"三入口均受硬门保护"。要给旧缓存补保护，
    # 需先给缓存路径接计划来源（另一工作包）。
    from step01_script_gen.plan_feasibility import (check_script_dialogue_integrity,
                                                    plan_dialogue_lines)
    _dlg_plan_lines = plan_dialogue_lines(plan) if plan else []
    _dlg_ok, _dlg_broken = True, []
    if _dlg_plan_lines:
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(script, plan)
        except Exception as _e:
            # v6.135 三审 P1-1：门自身异常 = **无法证明台词完整** → fail-closed BLOCK，
            # 不放行（缓存路径只靠本门复核，放行 = 门失效时裸奔进审计/提交）。
            _dlg_ok = False
            _dlg_broken = [{"line": "", "reason": "gate_error: %s" % _e}]
            _logger.error(f"[COMMIT] 台词完整性门异常，fail-closed BLOCK：{_e}")
    if not _dlg_ok:
        _dlg_total = len(_dlg_plan_lines)
        _gate_error = bool(_dlg_broken) and str(
            _dlg_broken[0].get("reason") or "").startswith("gate_error")
        # v6.135 四审 P2：门自身异常 ≠ 稿件截断——结构化消费者按 repair_outcome/
        # blocking[0].code 统计，检查故障不得计入 dialogue_truncated（把门故障算成
        # 稿件问题会污染"截断率"口径）。独立代码 dialogue_gate_error，detail 已区分。
        summary["repair_outcome"] = "dialogue_gate_error" if _gate_error \
            else "dialogue_truncated"
        summary["detail"] = (
            ("台词完整性硬门自身异常（fail-closed 不放行）：%s"
             % str(_dlg_broken[0].get("reason") or "")[:200])
            if _gate_error else
            "台词完整性硬门：%d/%d 句破损（%s），需在候选层重取——"
            "提交前修复按锁定契约不可修 narration_segment（09-26 locked_changed 死锁）"
            % (len(_dlg_broken), _dlg_total,
               "；".join("%s（%s）" % (b.get("line", "")[:30], b.get("reason"))
                         for b in _dlg_broken[:3])))
        _logger.error(f"[COMMIT] x {summary['detail']}")
        _console(f"  [COMMIT] 台词完整性硬门 BLOCK（{len(_dlg_broken)}/{_dlg_total} 句破损）")
        blocking = [{"code": summary["repair_outcome"], "severity": "blocking",
                     "message": summary["detail"]}]
        _dlg_skip_audit = True
    # v6.156（2026-09-30 复核第 5 组）：**事件履约契约同源复核**。拆分/修复/终稿都可能
    # 让「哪个镜头承载哪条节拍、说哪句台词」断开，而覆盖门与台词门**各自都能过**
    # （B1 声明由 D1 承载，成稿却在承载 B2 的镜头说 D1）。判据与候选入口**同一个
    # 函数**（不新造第二套口径）；只在计划带承载契约时生效（旧缓存沿用容忍口径）。
    if _dlg_plan_lines and not blocking:
        from step01_script_gen.plan_feasibility import check_event_contract
        try:
            # 2026-09-30 复核 P1-1：**终检也要强制声明**——删掉 `plan_id` 或整稿不填
            # `plan_refs` 时，候选入口过门不等于后续稿仍受约束（时长/结构/GAP 重生成
            # 会整稿替换）。带承载契约的计划在这里缺声明 ⇒ 显式失败。
            _ct_ok, _ct_viol, _ct_declared = check_event_contract(
                script, plan, require_declaration=True)
        except Exception as _e:
            _ct_ok = False
            _ct_viol = [{"code": "gate_error", "shot": "", "detail": str(_e)}]
            _logger.error(f"[COMMIT] 事件履约契约门异常，fail-closed BLOCK：{_e}")
        if not _ct_ok:
            summary["repair_outcome"] = "event_contract_broken"
            summary["detail"] = (
                "事件履约契约破损（B→D→镜头关系，拆分/修复后同源复核）："
                + "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                          str(v.get("detail"))[:60])
                            for v in _ct_viol[:3]))
            _logger.error(f"[COMMIT] x {summary['detail']}")
            _console("  [COMMIT] 事件履约契约 BLOCK（节拍/台词/镜头关系断开）")
            blocking = [{"code": "event_contract_broken", "severity": "blocking",
                         "message": summary["detail"]}]
            _dlg_skip_audit = True
    # `audit_ok` 必须**显式**记录：只看 `audit` 是否为 None 是 fail-open ——
    # 审计「调用失败」时 payload 照样是 dict（status=request_failed），
    # `if blocking or not audit` 会放行到提交。调用失败 **不等于** 0 issue。
    audit_ok = False
    # v6.135：台词完整性门已判破损时**零轮循环**——直接落到 BLOCK 路径
    # （blocking 已带 dialogue_truncated；summary["detail"] 已写）。
    _retry_state: dict = {}   # v6.137：修复轮 new_blocking 拒绝后的跨 attempt 重试状态
    # v6.139（修复 2，九审根因 2）：跨 attempt 的「已尝试未核实」审计问题身份集
    # ——触达兜底对同一身份只记一次账； `(shot, family)` 归并措辞漂移。
    _audit_attempted: set = set()
    _prev_audit_ids: set = set()   # 上一轮审计的身份集（跨轮比对：仍在/消失/新现）
    # v6.140（修复 3，九审根因 3）：本 run **暂缓**的联动组成员——某轮零解决且
    # 失败者是联动组时整组暂缓，剩余预算继续修其他目标；组问题留待提交门按
    # BLOCK（不做半条修复）。止损从「全局停机」限定到「失败组」。
    _deferred_targets: set = set()

    def _contract_reject_reason(_s) -> str:
        """**采纳修复稿之前**的契约复核（与候选入口同一个函数）；`""` = 通过.

        2026-09-30 复核第 4 轮 P1-1：提交层的契约门位于修复循环**之前**，而修复轮
        会写 narration / 拆镜 / 删补镜头并整份替换稿件（`script = _finalize(repaired)`）。
        破损的修复稿**不采纳**（保留基线），不把「改之前是对的」当成「改之后还是对的」。
        """
        if not _dlg_plan_lines:
            return ""
        from step01_script_gen.plan_feasibility import check_event_contract
        try:
            _ok, _viol, _ = check_event_contract(_s, plan, require_declaration=True)
        except Exception as _e:
            return "gate_error: %s" % _e
        if _ok:
            return ""
        return "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                      str(v.get("detail"))[:60])
                         for v in (_viol or [])[:3])

    # v6.153（2026-10-03 复核之十九 P1）：**同一次提交循环内**的审计复用。
    # 修复事务被**整轮回退**时稿件一个字没改（`compute_script_revision` 相同），
    # 于是下一轮的审计请求与上一轮**逐字相同**——再 POST 一次就是既定规则
    # （总计划 §1.3）禁止的"同一 revision 反复抽样博一次偶然 PASS"。实测代价：
    # 同一次运行内同请求指纹两次调用，白花 133.5 秒，且换回一份**不同**的问题
    # 清单（2 条 vs 5 条），把"零改善"这个结论也搅浑。
    # 键是**整条请求的身份**（提示逐字 + 请求参数），不是 revision：稿变了、
    # 定向项变了、规则/提示版本变了、档位或通道变了，身份都会变 ⇒ 自然重审。
    # **失败**的审计不进 memo（失败不能冒充可复用结论）。
    _audit_memo: dict = {}
    for _attempt in range(0 if _dlg_skip_audit else STEP01_AUDIT_REPAIR_MAX_ROUNDS + 1):
        quality = _build_step01_quality_gate(script, novel_text)
        _timing_mark("commit_quality", status=quality.get("status"))
        _q_blocking = _quality_blocking_records(quality)
        _audit_focus_now = _final_audit_focus(script, quality)
        _audit_identity = _final_audit_identity(
            script, novel_text, plan, _audit_focus_now, audit_model, run_id)
        _reuse_source = ""
        audit = _audit_memo.get(_audit_identity)
        if audit is not None:
            _reuse_source = "in_loop"
            _audit_reused = True
        else:
            audit = _reusable_final_audit(script)
            _audit_reused = audit is not None
            if _audit_reused:
                _reuse_source = "disk"
                _logger.info(
                    "[COMMIT] 复用已有 final audit（revision + 规则指纹 + scope 全同且为 ok）"
                )
            else:
                audit = _run_final_semantic_audit(
                    script, novel_text, plan, model=audit_model,
                    focus=_audit_focus_now, run_id=run_id)
                _timing_mark("commit_audit", status=audit.get("status"))
                # 只把**真的跑出结论**、且身份可复核的审计放进 memo。
                _memo = _memoizable_final_audit(audit)
                if _memo is not None:
                    _audit_memo[_audit_identity] = _memo
        if _reuse_source == "in_loop":
            summary["reused_audit"] = True
            _logger.info(
                "[COMMIT] 复用**本轮已完成**的 final audit（稿件与请求逐字未变，不再调用）："
                "身份 %s" % _audit_identity[:16]
            )
        elif _reuse_source == "disk":
            summary["reused_audit"] = True
        # v6.112：每轮审计都进诊断台账（含复用轮），供区分「跨镜顺带修好」
        # 与「同一内容 severity 波动」——见 `_record_audit_diag`。
        _record_audit_diag(audit, phase="commit_round", round_index=_attempt + 1,
                           reused=_audit_reused, run_id=run_id, script=script,
                           reuse_source=_reuse_source)
        # 审计失败（超时/请求失败/解析失败）**不等于 0 issue** —— 直接 BLOCK。
        if str(audit.get("status") or "").strip().lower() != "ok":
            summary["detail"] = (
                "final semantic audit %s: %s" % (audit.get("status"), audit.get("error"))
            )
            _logger.warn(f"[COMMIT] x {summary['detail']}（调用失败 != 0 issue，直接 BLOCK）")
            break
        audit_ok = True
        # blocking = 审计条目 ∪ 确定性质量门条目（两者都必须被修掉才算通过）
        blocking = _audit_blocking_records(audit) + _q_blocking
        if not blocking:
            break
        _logger.warn(
            "[COMMIT] 第 %d 轮发现 %d 条 blocking（审计 %d + 确定性质量门 %d）：%s"
            % (_attempt + 1, len(blocking), len(_audit_blocking_records(audit)),
               len(_q_blocking),
               [(b.get("shot_id"), b.get("code")) for b in blocking[:5]])
        )
        # v6.139（修复 2）：审计问题**跨轮身份比对**——按 (shot, family) 归并措辞
        # 漂移（同一问题四轮四名是实测常态），跨轮追踪「具体哪些事件矛盾还在」，
        # 不以审计条数判断收敛。
        _audit_issue_recs = _audit_payload_to_issues(audit)
        _now_ids = {_audit_issue_identity(i) for i in _audit_issue_recs
                    if is_blocking(i)}
        if _prev_audit_ids:
            _still = _now_ids & _prev_audit_ids
            _gone = _prev_audit_ids - _now_ids
            _fresh = _now_ids - _prev_audit_ids
            _logger.warn(
                "[COMMIT] 审计身份比对（(镜头,族) 归并措辞漂移）：仍在 %s｜消失 %s｜"
                "新现 %s%s"
                % (sorted(_still) or "无", sorted(_gone) or "无", sorted(_fresh) or "无",
                   "｜其中 %s 为已尝试未核实身份（触达兜底已停用）"
                   % sorted(_fresh & _audit_attempted) if _fresh & _audit_attempted else "")
            )
        _prev_audit_ids = _now_ids
        if _attempt >= STEP01_AUDIT_REPAIR_MAX_ROUNDS:
            summary["detail"] = (
                "达到 %d 个修复轮上限后仍有 %d 条 blocking"
                "（审计 %d + 确定性质量门 %d）"
                % (STEP01_AUDIT_REPAIR_MAX_ROUNDS, len(blocking),
                   len(_audit_blocking_records(audit)), len(_q_blocking))
            )
            break
        if _budget_exceeded():
            summary["detail"] = "总预算超时，停止 final audit 修复轮"
            _logger.warn(f"[COMMIT] {summary['detail']}")
            break
        # v6.119（用户口径）：进修复轮**之前**先查成对目标契约——不可执行就显式
        # BLOCK，不再花两个审计轮去博一次「也许能顺带修好」（那纯属烧审计额度）。
        _shots_by_id_rt = {f"shot_{_s.get('index')}": _s
                           for _s in (script.get("shots") or [])
                           if isinstance(_s, dict)}
        if isinstance(script.get("hook"), dict):
            _shots_by_id_rt["hook"] = script["hook"]
        # 镜头号记法归一（`"shot5"` → `shot_5`，双射、指向不变）本身在
        # `_target_id_of` 里做；这里只让它**可见**——同一份提示词下模型可能第 1 轮
        # 写 `"5"`、第 2 轮写 `"shot5"`，静默归一就没人知道输入形态变了。
        from step01_script_gen.repair_fields import shot_id_aliases
        _sid_aliases = shot_id_aliases((audit or {}).get("issues") or [])
        if _sid_aliases:
            _logger.warn("[COMMIT] 审计镜头号记法已归一（%d 处，指向不变）：%s"
                         % (len(_sid_aliases), _sid_aliases[:8]))
        _rt_problems = _audit_repair_target_problems(audit, _shots_by_id_rt)
        if _rt_problems:
            # v6.142（09-26 用户决定，P1-3）：**降级为告警，不再就地 BLOCK**。
            # 旧口径（v6.119）是「任一条目标无效 → 整条拒绝」+「有一条不可执行就
            # 整轮 BLOCK、**一轮修复都不发**」。20:25 run 实锤：shot 3 的 12 条越界
            # 目标把整轮修复全部掐掉，同轮里完全可修的其它目标一个都没动——被一条
            # 坏目标连坐。新口径：有问题的条目**保留 blocking**（下一轮审计照常
            # 重报），本轮不授权它；其余合法目标照常进修复轮。改不动 / 全是坏目标
            # 的终局，由既有「提交前修复轮零改善即停」判据给出（失败照常显式）。
            _logger.warn(
                "[COMMIT] %d 条审计条目缺少可执行的定向修复目标"
                "（保留 blocking；本轮不授权该条，本轮其余目标继续修）：%s"
                % (len(_rt_problems), _rt_problems[:3]))
        # ---- 受限修复（字段授权 + 内容保留 + 候选验收，全部走修复事务）----
        _before_rev = compute_script_revision(script)
        _repair_diag: list = []
        _repair_block_reasons: list = []
        repaired = _repair_shot_warnings(
            script, novel_text,
            bank=(hook_ctx or {}).get("bank"),
            objects_bank=(hook_ctx or {}).get("objects_bank"),
            model=audit_model,
            audit_issues=_audit_payload_to_issues(audit),
            max_rounds=1,          # §1.1：提交点每一轮只发一次修复调用
            repair_diag=_repair_diag,
            block_reasons=_repair_block_reasons,
            retry_state=_retry_state,
            audit_attempted=_audit_attempted,
            defer_targets=_deferred_targets,
            effect_state=_effect_state,
            # §14 P2 窄协议：审计产出的「称谓 → 已在场人物」显式关系。
            # 修复端**只**用它裁决「这条删除该不该保留」；缺它 = 未声明 ⇒ 不猜。
            audit_relations=_audit_payload_to_relations(audit),
        )
        # v6.119：修复事务诊断立刻落台账（不等提交结果）——BLOCK 路径正是最需要它
        # 的时候，而 BLOCK 时后续代码可能直接 break/return。
        _record_repair_diag(run_id=run_id, attempt=_attempt + 1,
                            records=_repair_diag)
        # v6.122（用户决定 B）：**一次改不完的联动组 → 立即 BLOCK**。
        # 这一轮没有调用 LLM、也不会再发审计（省下两轮修复 + 两次审计调用）：
        # 组超过上限时跳过它只会让同一份审计每轮重复声明、直到额度耗尽。
        if _repair_block_reasons:
            summary["repair_outcome"] = "oversized_group"
            summary["detail"] = (
                "联动修复目标超出单轮可修上限（%d 条）：%s"
                % (len(_repair_block_reasons), _repair_block_reasons[:2]))
            _logger.error(f"[COMMIT] {summary['detail']}")
            break
        summary["audit_repair_rounds"] += 1
        # 零改善：修复轮被拒或没动一个字 → 再发一轮只会重复同一批目标。
        #
        # v6.133（用户 2026-09-25）：**"模型没修好"与"修复请求没得到完整结果"必须分开报**。
        # 09-25 实跑的真实 BLOCK 直接原因是修复响应被截断（`max_output_tokens`，16k 上限含
        # 思维链 token）→ 整份丢弃 → 稿件一个字没变 → 这里只报"零改善"，把调用失败说成了
        # 内容结论。现在按台账里的 `kind="call_failed"` 记录区分，并把原因写进 detail。
        if compute_script_revision(repaired) == _before_rev:
            # v6.137：new_blocking 被拒 → 重试状态已导出（脚本回到基线，revision
            # 与被审计稿一致 ⇒ 下一轮审计**零审计调用**）。只在
            # 下一次 attempt 仍属修复预算（§1.1 最多 2 轮）时续跑。
            # v6.153：这句此前是**空头承诺**——盘上的 `_reusable_final_audit` 读的是
            # 正式产物/sidecar，而本轮刚跑完的审计**根本没写盘**，于是下一轮照旧
            # POST（实测同指纹两次调用、白花 133.5 秒）。现在由循环内的
            # `_audit_memo`（按**请求身份**索引）兜住这条路径。
            if (_retry_state.get("feedback")
                    and _attempt + 1 < STEP01_AUDIT_REPAIR_MAX_ROUNDS):
                _logger.warn(
                    "[COMMIT] 修复轮被拒（new_blocking）但带反馈重试待跑"
                    "（剔除 %s）→ 下一轮继续"
                    % (_retry_state.get("excluded"),))
                continue
            _call_failed = [r for r in _repair_diag if r.get("kind") == "call_failed"]
            # v6.133（2026-09-26 复核补口）：**"响应不可用"与"没改动稿件"也要分开**。
            # API 调用成功但补丁 JSON 解析失败 / envelope 结构无效 / 没返回替换 /
            # 替换全被校验拒——这四条此前只打日志，提交层看不见记录，于是与
            # "补丁有效但零改善"共用一个 `no_progress`，把"收到不可用响应"说成了
            # "修复没进展"。现在它们各自落 `kind="patch_unusable"` + `reason`。
            _unusable = [r for r in _repair_diag if r.get("kind") == "patch_unusable"]
            if _call_failed:
                summary["repair_outcome"] = "call_failed"
                summary["detail"] = ("修复调用失败/响应不完整（未得到完整结果，"
                                     "不是「修复无进展」）：%s"
                                     % str(_call_failed[-1].get("error") or "调用失败")[:200])
                _logger.error(f"[COMMIT] {summary['detail']}")
            elif _unusable:
                _u_last = _unusable[-1]
                summary["repair_outcome"] = "patch_unusable"
                summary["detail"] = (
                    "修复响应不可用（拿到了完整响应、但补丁不可用，不是「修复无进展」）："
                    "%s%s" % (_u_last.get("reason") or "unknown",
                              ("｜%s" % _u_last.get("detail"))
                              if _u_last.get("detail") else ""))
                _logger.error(f"[COMMIT] {summary['detail']}")
            else:
                # `no_progress` 只表示**稿件没有改动**（且台账里没有调用失败/不可用补丁
                # 记录）；能取到上一轮事务时把它的结论一并写出，便于分辨"改完被回滚"
                # 与"根本没动"。
                _tx = [r for r in _repair_diag if "accepted" in r]
                # v6.140（修复 3，九审根因 3）：零解决发生在**真联动组**（≥2 成员，
                # `_linked_repair_groups` 会把单镜问题也折成单成员组）身上 → 止损
                # 局部化——组整组暂缓（本 run 后续轮次不再选它），剩余预算继续修
                # 其他目标；组的 blocking 保留，仍会照常 BLOCK（不做半条修复）。
                # 只有当 blocking 里还存在**组外**目标时才值得续跑，否则与直接
                # 停止等价、白烧一轮。
                _grp_members: set = set()
                if _tx:
                    for _g in (_tx[-1].get("groups") or []):
                        if len(_g) >= 2:
                            _grp_members.update(_g)
                _outside_exists = False
                if _grp_members:
                    from step01_script_gen.repair_fields import _target_id_of
                    for _b in blocking:
                        _tid = _target_id_of(str(_b.get("shot_id") or ""))
                        if _tid and _tid not in _grp_members:
                            _outside_exists = True
                            break
                if (_grp_members and _outside_exists
                        and _attempt + 1 < STEP01_AUDIT_REPAIR_MAX_ROUNDS):
                    _deferred_targets |= _grp_members
                    _logger.warn(
                        "[COMMIT] 联动组 %s 本轮零解决（reason=%s）→ 整组暂缓"
                        "（本 run 后续轮次不再选它）；组外目标（如 %s）继续用剩余"
                        "预算修复；组的 blocking 保留、仍会照常 BLOCK"
                        % (sorted(_grp_members), _tx[-1].get("reason"),
                           [b.get("shot_id") for b in blocking
                            if _target_id_of(str(b.get("shot_id") or ""))
                            not in _grp_members][:3]))
                    continue
                summary["repair_outcome"] = "no_progress"
                if _tx:
                    summary["detail"] = (
                        "提交前修复轮零改善（稿件未变；上一轮事务 accepted=%s resolved=%s "
                        "reason=%s），停止修复"
                        % (bool(_tx[-1].get("accepted")), _tx[-1].get("resolved"),
                           str(_tx[-1].get("reason") or "-")[:80]))
                else:
                    summary["detail"] = ("提交前修复轮零改善（稿件未变；台账中无调用失败/"
                                         "不可用补丁记录），停止修复")
                _logger.warn(f"[COMMIT] {summary['detail']}")
            script = repaired
            break
        _cand = _finalize(repaired)
        _ct_why = _contract_reject_reason(_cand)
        if _ct_why:
            # 契约破损的修复稿**不采纳**：`script` 保持在基线（本轮的审计 blocking 与
            # 这份基线同 revision，仍然有效），并显式说明「这一轮为什么白跑」。
            summary["repair_outcome"] = "contract_broken"
            summary["detail"] = (
                "修复稿破坏事件履约契约（%s）→ **不采纳**，保留基线；"
                "基线自身的 blocking 仍在（见 blocking 清单）" % _ct_why)
            _logger.error(f"[COMMIT] x {summary['detail']}")
            _console("  [COMMIT] 修复稿破坏事件履约契约 → 不采纳（保留基线）")
            break
        summary["repair_outcome"] = "improved"
        script = _cand

    # ---- 写后不可变守卫：audit 前后 revision 必须相同 ----
    revision = ensure_script_revision(script)
    audit_rev = str((audit or {}).get("input_revision") or "")
    if audit and audit_rev and audit_rev != revision:
        # 审计之后又有语义写入 → 本轮审计作废（不写产物，交下一轮重来）
        summary["detail"] = (
            "final audit 之后 revision 发生变化（audit=%s current=%s）——审计作废"
            % (audit_rev[:12], revision[:12])
        )
        _logger.warn(f"[COMMIT] x {summary['detail']}")
        blocking = blocking or [{"code": "revision_changed_after_audit",
                                 "severity": "blocking", "message": summary["detail"]}]

    if blocking or not audit_ok:
        summary["blocking"] = blocking
        summary["detail"] = summary["detail"] or (
            "final semantic audit 未通过" if audit_ok else "final semantic audit 未产出可用结论")
        # v6.152（C1/C2，2026-09-29 五次跑实测）：终局若是**审计调用失败**
        # （超时 / 传输耗尽 / 解析耗尽），`blocking` 还停在**上一次成功审计那一轮**
        # 的清单上——那是陈旧清单，写进 manifest 会误导读数（run 02 报 22 条、
        # run 04 报 6 条，真因却是调用失败），登记进欠账台账更会把一次**临时故障**
        # 变成稿件的**长期语义欠账**，污染 BEST-GUARD 选稿。
        # 处置：① manifest 只记本次失败这一条（`blocking_codes` 不再陈旧）；
        #      ② 陈旧清单降级为 `summary` 里的**诊断**字段，不进 manifest；
        #      ③ **不登记欠账**——「没有可用审计结论」不等于「稿件有这些欠账」。
        # 正式产物 `step01_audit.json` 依旧不动（与另两份同 revision）；本次是否
        # 审出来了由 manifest 的 `audit_status` 与 blocked sidecar 承担。
        _stale_blocking = blocking          # 只在审计调用失败时降级为诊断字段
        blocking, _audit_status, _audit_call_failed = _terminal_blocking_records(
            audit, blocking)
        summary["blocking"] = blocking
        if _audit_call_failed:
            summary["last_known_blocking"] = _stale_blocking
            summary["last_known_blocking_note"] = (
                "审计调用失败（%s）——上述清单来自**上一次成功审计那一轮**，"
                "仅作诊断，不是本次结论" % _audit_status)
        _metrics_inc("commit.blocked")
        if _audit_call_failed:
            _logger.warn(
                "[COMMIT] 审计调用失败（%s）——本次**不登记欠账台账**："
                "「没有可用审计结论」不是稿件的语义欠账，登记会污染选稿台账；"
                "陈旧清单已降级为诊断字段 last_known_blocking（%d 条）"
                % (_audit_status, len(_stale_blocking)))
        else:
            _debt_issues = _debt_issue_records(blocking)
            _record_blocked_revision(revision, issues=_debt_issues,
                                     versions=_revision_aliases_or_empty(script))
            # v6.125（第 1 项）：同时登记**原稿** revision——被选中的历史稿本身才是
            # 「已知欠账的那一份」，只记修后版本等于把它留在候选池里反复入选。
            if _entry_revision and _entry_revision != revision:
                _record_blocked_revision(_entry_revision, issues=_debt_issues,
                                         versions=_entry_aliases)
                _logger.warn(
                    "[COMMIT] 欠账台账同时登记进入提交点的原稿 revision %s"
                    "（修后 %s）——否则该历史稿会因漏记而在后续选稿中再次入选"
                    % (_entry_revision[:12], revision[:12]))
            # v6.129（用户 2026-09-25 根因）：**选稿边界**记录的快照自身 revision 也要
            # 登记。它与入口/修后 revision 都可能不同（大纲覆盖参与哈希），而 guard 下次
            # 恰恰是用这个值查台账——不记它，同一份欠账稿会第三次被选中。
            if source_revision and source_revision not in (revision, _entry_revision):
                _record_blocked_revision(source_revision, issues=_debt_issues,
                                         versions=dict(source_aliases or {}))
                _logger.warn(
                    "[COMMIT] 欠账台账登记**被选中的历史快照自身** revision %s"
                    "（提交入口 %s / 修后 %s）——BEST-GUARD 用快照自身 revision 判欠账"
                    % (source_revision[:12], (_entry_revision or "")[:12], revision[:12]))
        _write_blocked_audit_sidecar(audit)
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=blocking,
                               audit_status=_audit_status)
        _clear_staged_dir()
        try:
            _snapshot_run_artifacts(script)      # 保留诊断证据供人工恢复
        except Exception:
            pass
        _console(f"  [COMMIT] x BLOCK：{summary['detail']}")
        _console("  [COMMIT] 三份正式产物未替换（step01_commit.json 标记为 BLOCK）")
        return script, summary

    # ---- staged 三件套 + 真自验 ----
    stage_dir = CHAPTER_DIR / "_staged"
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json
        staged = {}
        for _name, _payload in (("step01_audit.json", audit),
                                ("step01_quality.json", quality),
                                (OUTPUT_FILE.name, _strip_runtime_fields(script))):
            _path = stage_dir / _name
            atomic_dump_json(_path, _payload, ensure_ascii=False, indent=2)
            staged[_name] = _path
        # 用**真实** gate 函数读 staged 文件——不是 mock，也不是自己重写一遍判据。
        staged_script = json.loads(staged[OUTPUT_FILE.name].read_text(encoding="utf-8"))
        require_semantic_gate(
            staged["step01_quality.json"], staged["step01_audit.json"],
            staged_script, stage="step01")
    except Exception as exc:
        summary["detail"] = f"staged 自验失败：{exc}"
        _metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=blocking)
        _clear_staged_dir()
        _console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    # ---- 原子替换：audit → quality → script（script 最后）----
    try:
        from shared.json_io import atomic_dump_json
        atomic_dump_json(CHAPTER_DIR / "step01_audit.json", audit,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(CHAPTER_DIR / "step01_quality.json", quality,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(OUTPUT_FILE, _strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
    except Exception as exc:
        summary["detail"] = f"正式产物替换失败：{exc}"
        _metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=[])
        _clear_staged_dir()
        _console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    # ---- commit manifest 是「提交完成」的唯一标志 ----
    manifest = build_step01_commit_manifest(
        run_id=run_id, created_at=_step01_now(), revision=revision,
        rule_source_fingerprint=RULE_SOURCE_FINGERPRINT,
        rule_version=RULE_VERSION, audit_scope=STEP01_AUDIT_SCOPE,
        prompt_source_fingerprint=PROMPT_SOURCE_FINGERPRINT,
        chapter_dir=CHAPTER_DIR, status="COMMITTED")
    write_step01_commit_manifest(CHAPTER_DIR, manifest)

    # ---- 正式路径再自验一次（含 manifest）----
    try:
        committed_script = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        require_semantic_gate(CHAPTER_DIR / "step01_quality.json",
                              CHAPTER_DIR / "step01_audit.json", committed_script,
                              stage="step01")
        verify_step01_commit_manifest(CHAPTER_DIR, committed_script, stage="step01",
                                      expected_chapter=NOVEL_CHAPTER)
    except Exception as exc:
        summary["detail"] = f"提交后自验失败：{exc}"
        _metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=[])
        _clear_staged_dir()
        _console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    summary["status"] = "COMMITTED"
    summary["revision"] = revision
    summary["manifest"] = manifest
    # 调用方（main 的摘要/metrics）消费同一个 quality 对象——不再重算，
    # 避免「落盘的报告」与「打印的报告」是两个对象（那正是漂移的老形态）。
    summary["quality"] = quality
    summary["audit"] = audit
    _metrics_inc("commit.committed")
    _metrics_update("commit.last", {
        "status": "COMMITTED", "revision": revision,
        "audit_repair_rounds": summary["audit_repair_rounds"],
        "reused_audit": summary["reused_audit"],
        "blocking": 0, "run_id": run_id,
    })
    _logger.info(
        f"[COMMIT] OK 三份产物已提交（revision={revision[:12]}，"
        f"审计修复轮 {summary['audit_repair_rounds']}，"
        f"复用审计 {'是' if summary['reused_audit'] else '否'}）"
    )
    return script, summary


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

    下划线开头键 = 「不入盘」信号（_run_metadata/_merge_candidate/_qc_last 等
    只留在内存）。本函数现为**单源实现**：step02a 的同名副本已随它唯一服务的
    那段「回写 script_result.json」一并移除（2026-10 复核 P0，已提交产物只读）。
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
    if DURATION_MIN_SEC <= eff_dur <= DURATION_MAX_SEC:
        score += 10
    elif DURATION_MIN_SEC - 10 <= eff_dur < DURATION_MIN_SEC or DURATION_MAX_SEC < eff_dur <= DURATION_MAX_SEC + 30:
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
    if (prev_dur >= DURATION_MIN_SEC and new_dur > DURATION_MAX_SEC) or (_new_n - _prev_n >= 3):
        return (f"镜数/时长膨胀（新版 {_new_n} 镜 {new_dur:.0f}s，上一版 {_prev_n} 镜 "
                f"{prev_dur:.0f}s）——重生版把镜头预算炸出合法带（{DURATION_MIN_SEC:g}-{DURATION_MAX_SEC:g}s），"
                f"越修越多的镜头会让总时长超出本轮合法上限（{DURATION_MAX_SEC:g}s）")
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


#: 修复轮**实际执行**的锁定契约——`_locked_repair_signature` 逐字取自这几项。
#: v6.149：**修复可执行性预检**必须用它，不能只读 `_FINAL_LOCKED_FIELDS`——后者对
#: 「已授权字段」有例外，而轮级签名照旧锁住这几项（实测：审计把 `narration_segment`
#: 列为目标 → 授权通过 → 修复改了它 → `locked_changed` 回退，白花一轮修复调用）。
_ROUND_LOCKED_SHOT_FIELDS = ("duration_sec", "narration_segment", "subtitle",
                             "scene", "location", "variation_type")


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
        rec = {"id": sid, "index": shot.get("index")}
        for f in _ROUND_LOCKED_SHOT_FIELDS:      # 轮级锁定契约：单源
            rec[f] = shot.get(f)
        out.append(rec)
    return _json.dumps(out, ensure_ascii=False, sort_keys=True)


def _split_executable_allowed(allowed) -> tuple:
    """把某目标的授权字段拆成 `(可执行字段, 命中的锁定字段)`（v6.149 可执行性预检）.

    修复轮**实际执行**的锁定契约是 `_ROUND_LOCKED_SHOT_FIELDS`（轮级签名），比
    `_FINAL_LOCKED_FIELDS` 严：后者对「已授权字段」有例外，前者没有。授权面里出现
    锁定字段 = 这条问题**最终修复不可执行**——预检据此把它摘出来（保留 blocking、
    交上游阶段），而不是让它进修复调用再被 `locked_changed` 回退。
    """
    _locked = {str(f) for f in _ROUND_LOCKED_SHOT_FIELDS}
    _all = [str(a) for a in (allowed or [])]
    return [a for a in _all if a not in _locked], sorted(set(_all) & _locked)


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


from step01_script_gen.repair_fields import (merge_authorized as _merge_authorized,
                                          apply_scoped_field_updates as _apply_scoped_field_updates,
                                          canonical_path_diff,
                                          frame_layout_structural_violations,
                                          MODEL_AUDIT_WARNING_PREFIX)


def _assemble_keyframe_additions(target: dict, additions) -> tuple:
    """把「帧 + 对应布局」的**成对数据**装配进完整镜头 → `(new_shot, 拒绝原因)`.

    2026-09-27（用户决定）：缺段这类**联动**修复不再要求模型返回一个自洽的完整
    镜头。实测模型会把 `keyframes` 加到 4 而 `frame_layouts.kfs` 留在 3 →
    长度校验把整条替换拒收（两轮都没修成）。改为模型只给**成对的两半**
    `{"frame": {...}, "layout": [...]}`，由代码按 `t_sec` 的时间顺序把两半装进
    **同一声序号**。

    **代码只做配对与装配**：不从邻帧或 `motion_desc` 猜测人物姿态/持物等布局
    语义（那样的回填等于伪造布局）。缺任一半 → 拒收并记明原因。
    """
    if not isinstance(additions, list) or not additions:
        return None, "keyframe_additions 必须是非空列表"
    import copy as _copy

    from shared.kf_time import get_kf_t_sec

    out = _copy.deepcopy(target)
    kfs = out.get("keyframes")
    if not isinstance(kfs, list):
        return None, "目标镜头没有 keyframes 列表"
    _fl = out.get("frame_layouts")
    lkfs = _fl.get("kfs") if isinstance(_fl, dict) else None
    if not isinstance(lkfs, list):
        return None, "目标镜头没有 frame_layouts.kfs 列表"
    try:
        dur = float(out.get("duration_sec") or out.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0

    for i, item in enumerate(additions):
        if not isinstance(item, dict):
            return None, f"keyframe_additions[{i}] 不是对象"
        frame, layout = item.get("frame"), item.get("layout")
        if not isinstance(frame, dict):
            return None, f"keyframe_additions[{i}] 缺 frame——必须与 layout **成对**给出"
        if not isinstance(layout, list):
            return None, f"keyframe_additions[{i}] 缺 layout——必须与 frame **成对**给出"
        if not str(frame.get("desc") or "").strip():
            return None, f"keyframe_additions[{i}] 的 frame 缺 desc"
        t = get_kf_t_sec(frame, out)
        if t is None:
            return None, f"keyframe_additions[{i}] 的 frame 缺可解析的 t_sec/time_ratio"
        if dur > 0 and not (0 < t <= dur + 0.01):
            return None, f"keyframe_additions[{i}] t_sec={t:g} 越出镜长 {dur:g}"
        # 按**时间顺序**定位插入点（与 keyframes 的升序约定一致），
        # `frame` 与 `layout` 插到**同一个下标**——配对由代码保证。
        idx = len(kfs)
        for j, kf in enumerate(kfs):
            tj = get_kf_t_sec(kf, out)
            if tj is not None and t < tj:
                idx = j
                break
        kfs.insert(idx, frame)
        lkfs.insert(idx, layout)

    if len(kfs) != len(lkfs):
        return None, "装配后 keyframes 与 frame_layouts.kfs 长度不等"
    return out, ""


def _repair_answer_example(shots_by_id: dict, picked, allowed_by_target=None) -> str:
    """Render a valid answer envelope from the actual authorized objects."""
    import json
    answer = {"shot_replacements": []}
    for target_id in picked:
        if target_id not in shots_by_id:
            continue
        replacement = {"shot_id": str(target_id).removeprefix("shot_"), "shot": shots_by_id[target_id]}
        if allowed_by_target is not None:
            from step01_script_gen.repair_fields import path_allowed
            updates = []
            allowed = allowed_by_target.get(target_id, set())
            def visit(value, path):
                if path and path_allowed(path, allowed):
                    updates.append({"field_path": path, "value": value})
                elif isinstance(value, dict):
                    for key, child in value.items():
                        visit(child, f"{path}.{key}" if path else key)
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        visit(child, f"{path}.{index}")
            visit(shots_by_id[target_id], "")
            if updates:
                replacement = {"shot_id": replacement["shot_id"], "updates": updates}
        if target_id == "hook":
            answer["hook_replacement"] = replacement
        else:
            answer["shot_replacements"].append(replacement)
    return json.dumps(answer, ensure_ascii=False, default=str)


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
        forms = [key for key in ("shot", "updates", "field_patches", "keyframe_additions")
                 if key in item]
        if len(forms) != 1 or item.get(forms[0]) is None:
            rejected.append("同一目标只能提供一种替换形态")
            return
        if forms[0] == "shot" and not isinstance(new_shot, dict):
            rejected.append("shot 必须是对象")
            return
        if forms and forms[0] in ("updates", "field_patches") and set(item) != {"shot_id", forms[0]}:
            rejected.append("窄字段补丁条目含未知字段")
            return
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
        # 2026-09-27（用户决定）：联动修复的**成对数据**形状——模型只给
        # 「帧 + 对应布局」，代码按时间顺序把两半装进同一声序号。缺任一半即拒收。
        if new_shot is None and item.get("keyframe_additions") is not None:
            new_shot, _why = _assemble_keyframe_additions(
                target, item.get("keyframe_additions"))
            if _why:
                rejected.append(f"shot {sid}: {_why}")
                return
        allowed = None
        if isinstance(allowed_by_target, dict):
            allowed = allowed_by_target.get(f"shot_{sid}", allowed_by_target.get(sid))
            if allowed is None and not force_hook:
                # 授权集合缺失 = 该目标没有明确字段路径 → 不得走定向修复
                rejected.append(f"shot {sid}: 本轮未授权任何字段（问题未点名字段路径）")
                return
            allowed = allowed or set()
        # 窄字段补丁（Scoped Field Patch）：当模型返回 `updates`（或 `field_patches`）
        # 而非完整 `shot` 对象时，先校验每个补丁路径在 `allowed` 内并装配到原镜副本，
        # 彻底消除模型漏抄未修改锁定字段引发的 `locked_changed` 误伤与 Token 浪费。
        _raw_updates = item.get("updates") if item.get("updates") is not None else item.get("field_patches")
        if new_shot is None and _raw_updates is not None:
            new_shot, _why = _apply_scoped_field_updates(target, _raw_updates, allowed=allowed)
            if _why:
                rejected.append(f"shot {sid}: {_why}")
                return
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
            # v6.104.1（第二十四次重跑排查）：**必须能看出模型在授权字段上到底改了什么**。
            #
            # 该轮实测 `resolved=0` 却看不到任何依据：日志只有「未授权路径已回填
            # ['motion_desc']」，无法判断模型是「没加 KF」还是「加了但被回填」，
            # 只能靠翻 16MB 日志并撞上 581 字符的行截断。把授权字段的实际
            # canonical diff（含 `keyframes.__len__` 这种长度变化）打出来，
            # 「模型有没有真的补帧」一眼可判。
            try:
                _changed = canonical_path_diff(target, new_shot)
            except Exception:
                _changed = []
            _kf_delta = (len(target.get("keyframes") or []),
                         len(new_shot.get("keyframes") or []))
            if _changed or _kf_delta[0] != _kf_delta[1]:
                _logger.info(
                    f"[REPAIR] shot {sid}: 授权字段实际改动 {len(_changed)} 处 "
                    f"（keyframes {_kf_delta[0]}->{_kf_delta[1]}）: {_changed[:8]}"
                )
        # duration_sec 是派生值：授权合并后再同步，避免被回填成旧时长。
        if _raw_updates is not None and isinstance(new_shot, dict) and new_shot.get("duration") != target.get("duration"):
            new_shot["duration_sec"] = new_shot["duration"]
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


def collapse_repetition_fields(script: dict) -> int:
    """折叠各文本字段里**连续 ≥3 次**的重复词/句组；返回改动字段数（v6.115）.

    为什么由 `finalize` 兜底：三个入口（正常生成 / 缓存命中 / BEST-GUARD 恢复）
    都经过 `finalize_step01_script`，而**历史快照会绕过 `post_fix_shot_prompts`**
    ——实测 run 18 的 BLOCK 快照里 shot 8 仍有 6 个连续「眼神」与 6 组重复短句
    （折叠规则本身没失效，是旧快照没走早段）。

    必须在 `write_timeline_from_durations`（时间派生）**之前**调用——`motion_desc`
    含时段标记，被时间屏障视为时间字段，晚写会让已派生的 timecode 失配。
    """
    from shared.content_safety import collapse_repetition as _cr
    changed = 0
    units = list(script.get("shots") or []) + [script.get("hook")]
    for shot in units:
        if not isinstance(shot, dict):
            continue
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = shot.get(fld)
            if isinstance(v, str) and v:
                nv = _cr(v)
                if nv != v:
                    shot[fld] = nv
                    changed += 1
        for kf in shot.get("keyframes") or []:
            if isinstance(kf, dict):
                v = kf.get("desc")
                if isinstance(v, str) and v:
                    nv = _cr(v)
                    if nv != v:
                        kf["desc"] = nv
                        changed += 1
    return changed


def finalize_step01_script(script: dict, *, novel_text: str = "",
                           label: str = "落盘前",
                           repair_effects=None) -> tuple[dict, list[str]]:
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
    # v6.115：早段清理兜底（时间派生之前；幂等，已清理过的稿子零改动）
    # §14 P3：`repair_effects` 是提交路径带过来的**只读**效果视图（修复轮通过验收
    # 的布局增删）。它让「本轮已授权删除的条目不得按类别别名重建」这条规则在这一趟
    # `is_final_pass=True` 的 hard-fix 上**同样成立**——只保证修复轮内成立的话，
    # 合法删除会在 finalize 被 `group_actor_continuity` 补回，下一轮审计重报
    # blocking，又判零改善。缺省 `None` ⇒ 约束行为**逐字不变**（三条入口共用本函数）。
    try:
        _n_rep = collapse_repetition_fields(script)
        if _n_rep:
            _logger.info(f"[FINALIZE] 折叠连续重复词句 {_n_rep} 处（{label}）")
    except Exception as _rep_err:
        _logger.warn(f"[FINALIZE] 重复折叠失败（保留原版）: {_rep_err}")

    from step01_script_gen.constraints import run_hard_fix as _hfix_final
    from shared.semantic_barrier import TimeFieldWriteTracker
    from step01_script_gen.quality_issues import final_time_invariant_issues

    tracker = TimeFieldWriteTracker(script)
    try:
        script = _hfix_final(script, is_in_repair_loop=False, is_final_pass=True,
                             novel_text=novel_text,
                             on_constraint_done=tracker.observe,
                             repair_effects=repair_effects)
    except Exception as _hfe:
        # 环境故障（依赖缺失 / 语法不兼容 / 名字未定义）意味着**约束链根本没跑完**。
        # 既不能降级成「保留原版」，更不能让后续提交照常写正式产物——2026-10 复核 P0：
        # `engine.apply_constraints` 上抛后若在此吞掉，排在该约束后面的全部约束都被
        # 连带跳过，而流程照旧打出 `[COMMIT] OK 三份产物已提交`。必须 fail-closed。
        from step01_script_gen.constraints.engine import ENV_FAULT_TYPES
        if isinstance(_hfe, ENV_FAULT_TYPES):
            raise
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


def _reindexed_kf_shots(script_before: dict | None,
                        script_after: dict | None) -> set[str]:
    """本轮 KF 列表长度发生变化的镜头集合（v6.104.1）.

    为什么需要：kf 类 issue 的定位符里带**位置序号**（`frame_id="kf1"`）。修复轮
    给某镜**新增一帧**之后，该镜后续帧的序号整体后移——同一个缺陷换了 key，
    于是 `after_keys - before_keys` 把它记成「新阻塞」，`before_keys - after_keys`
    又把旧 key 记成「已解决」。一减一增都是假的，而这条路径**只有补帧才会走到**，
    正是 v6.104 新放开的修复方式。

    实测（第二十五次重跑）：shot 9 帧数 2→4，`resolved=4` 的一轮被 `new=2`
    整轮回退。所以「帧数变了的镜头」在身份比较里必须**丢掉位置序号**。
    """
    def _counts(script):
        out = {}
        if not isinstance(script, dict):
            return out
        for s in list(script.get("shots") or []) + ([script["hook"]]
                                                    if isinstance(script.get("hook"), dict)
                                                    else []):
            if isinstance(s, dict):
                out[str(s.get("index"))] = len(s.get("keyframes") or [])
        return out

    before, after = _counts(script_before), _counts(script_after)
    return {sid for sid in set(before) | set(after)
            if before.get(sid, -1) != after.get(sid, -1)}


def _blocking_record_keys(state: dict | None,
                          reindexed: set[str] | None = None) -> set[tuple]:
    keys: set[tuple] = set()
    for rec in (state or {}).get("issues") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("severity") or "") not in ("hard", "visual_risk"):
            continue
        key = list(_blocking_record_key(rec))
        # 该镜本轮帧数变了 → 位置序号不再是稳定定位符，降级为「该镜的该类问题」
        if (reindexed and str(rec.get("frame_id") or "").startswith("kf")
                and str(rec.get("shot_id")) in reindexed):
            key[2] = "*"
        keys.add(tuple(key))
    return keys


#: audit 问题代码 → **跨轮稳定 family**（v6.139，修复 2）。同一语义问题跨轮换名
#: 是实测常态（FUTURE_STATE → FUTURE_STATE_IN_FF → result_shown_in_ff →
#: FUTURE_STATE_IN_PRE_FRAME，2026-09-26 20:49 run 四轮四名）——按 (shot, family)
#: 归并才能比对「同一问题」，这是触达兜底单次记账与跨轮身份比对的事实源。
#: 匹配子串、大小写不敏感、具体在前；都不命中 → family = 代码本身（保守不归并）。
_AUDIT_FAMILY_PATTERNS: tuple = (
    ("future_state_in_frame", ("future_state", "result_shown", "_in_ff",
                               "_in_pre", "premature", "state_in_")),
    ("state_contradiction", ("contradict", "inconsistency", "mismatch",
                             "conflict", "garbled", "state_chain")),
    ("missing_beat", ("missing",)),
    ("prop_state", ("prop",)),
    ("text_truncation", ("truncat", "text_error", "garbled_text")),
    ("layout_gap", ("layout", "person_missing", "frame_duty",
                    "frame_role", "duty")),
    ("temporal_window", ("time_window", "temporal", "kf_")),
)


def _audit_issue_identity(issue) -> tuple:
    """audit 问题的**跨轮稳定身份** `(shot, family)`.

    family 按代码子串归并（见 `_AUDIT_FAMILY_PATTERNS`）；`shot` 经
    `_issue_shot_index` 归一（`shot5`/`5` 同身份）。措辞漂移不影响身份；
    同镜同族的两个不同问题会被视为同一身份——对「兜底记账只记一次」这是
    保守方向（少记功绩），可接受。
    """
    code = str(getattr(issue, "code", "") or "")
    lower = code.lower()
    family = code[len("model_audit_"):] if lower.startswith("model_audit_") else code
    for fam, keys in _AUDIT_FAMILY_PATTERNS:
        if any(k in lower for k in keys):
            family = fam
            break
    return (str(_issue_shot_index(issue)), family)


def _audit_evidence_text(issue) -> str | None:
    """既有消失判据的最短证据要求；不足长度表示不可判，不表示未修复。"""
    evidence = str(getattr(issue, "evidence", "") or "").strip()
    return evidence if len(evidence) >= 8 else None


def _audit_paired_fields(script: dict | None, issue) -> dict:
    """已声明目标的实际授权投影；缺席维持旧路径，空/非法声明不回退。"""
    from step01_script_gen.repair_fields import allowed_repair_fields, _target_id_of
    if not isinstance(script, dict):
        return {}
    shots = {_target_id_of(s.get("index")): s for s in script.get("shots") or []
             if isinstance(s, dict)}
    if isinstance(script.get("hook"), dict):
        shots["hook"] = script["hook"]
    return {tid: allowed_repair_fields([], [issue], target_shot_id=tid, shots_by_id=shots)
            for tid in shots}


def _audit_issue_newly_resolved(script_after: dict, script_before: dict | None,
                                issue: Issue, attempted: set | None = None) -> bool:
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
    # 发现位置不等于修法位置：上游补建立、同镜补另一侧时应保留来源证据。
    # 这种授权路径只能给一次触达机会，不能冒充已解决；必须再次完整审计。
    from step01_script_gen.repair_fields import path_allowed, _target_id_of
    declared = getattr(issue, "repair_targets", None)
    observation_allowed = True
    if declared is not None:
        fields = _audit_paired_fields(script_before, issue)
        observation_allowed = path_allowed(str(getattr(issue, "field_path", "") or ""),
                                          fields.get(_target_id_of(issue.shot_id), set()))
    if (not observation_allowed or _audit_evidence_text(issue) is None
            or _audit_issue_resolved(script_before, issue)):
        # v6.108（2026-09-24 第三十次重跑实测）：证据在**改前稿子里就找不到**
        # → 这个判据没有区分度（自由文本证据），于是「本轮新解决」永远为 False，
        # 审计类阻塞**结构性无法被修复轮证明解决**：
        #
        #   实测：提交点确定性阻塞已归零、只剩 1 条审计问题（shot 2 ff_desc 道具
        #   状态与原文不符）。模型按 issue 点名的字段改了 ff_desc、没有引入新阻塞，
        #   却因 `audit_resolved=0` 被判 `no_target_resolved` → 整轮回退 → BLOCK。
        #
        # 兜底口径：改前不可判 → 用「本轮是否动过该 issue 点名的字段」计一次
        # 已解决。**这不是放宽最终门禁**：提交循环每轮都会重跑全量审计，
        # `blocking = 审计 ∪ 确定性` 必须为空才可能提交；改得不对下一轮会再次报出来。
        # v6.139（修复 2，九审根因 2）：兜底记账**每个问题身份只记一次**——同一问题
        # 靠「动过字段」反复通过验收 = 把尝试当进展（18:46 run 台账第 21 条
        # accepted/resolved=0 实锤；触达证明尝试过，不证明语义已解决）。已尝试
        # 未被后续审计核实的身份，第二轮起不再给兜底分——要么真解决（证据判据
        # 不受影响），要么如实 no_target_resolved。
        if attempted and _audit_issue_identity(issue) in attempted:
            return False
        return _audit_issue_field_touched(script_before, script_after, issue)
    return _audit_issue_resolved(script_after, issue)


def _issue_shot_index(issue) -> str:
    """issue 的 `shot_id` → 镜头身份：序号裸串、钩子的 `"hook"`，或**不可解析的原串**.

    「可解析」= `"hook"` 或纯数字串；空串与 `shot_x` / `第5镜` 这类**指不明**的写法
    原样返回（保留原值便于日志回查），调用方必须按「定位不到」处理——
    `_issue_target_shot` 返回 None、`_audit_issue_resolved` 判「无法证明已解决」。

    v6.120：模型审计的 `shot_id` 记法不保证是裸数字（实测 `"shot5"`），这里必须与
    `_target_id_of` 同口径——否则「按 index 找镜头」的每一处都会找不到（`shot5`
    匹配不上 `index: 5`），进而把 issue 误判成「目标镜头已被结构补丁删除」。
    """
    from step01_script_gen.repair_fields import _shot_index_of
    return _shot_index_of(getattr(issue, "shot_id", ""))


def _issue_target_shot(script: dict, issue) -> "dict | None":
    """该 issue 点名的镜头对象（hook 用 `hook`，其余按 index）；**找不回来就是 None**.

    v6.120：`shot_id` 为空或**指不明**（`""` / `shot_x` / `第5镜`）时返回 None——
    调用方必须把它读成「无法定位」，不得读成「这个镜头不存在于稿中」。
    """
    if not isinstance(script, dict):
        return None
    _idx = _issue_shot_index(issue)
    if _idx == "hook":
        return script.get("hook") if isinstance(script.get("hook"), dict) else None
    if not _idx.isdigit():
        return None
    for item in script.get("shots") or []:
        if isinstance(item, dict) and str(item.get("index")) == _idx:
            return item
    return None


def _frame_layout_of(shot: "dict | None", frame_id: str):
    """该**镜头**里这一帧在 `frame_layouts` 的条目（kf 用序号，越界回退整表）."""
    fl = shot.get("frame_layouts") if isinstance(shot, dict) else None
    if not isinstance(fl, dict):
        return None
    fid = str(frame_id or "")
    if fid == "ff":
        return fl.get("ff")
    if fid == "lf":
        return fl.get("lf")
    if fid.startswith("kf"):
        try:
            i = int(fid[2:])
        except ValueError:
            return fl.get("kfs")
        kfs = fl.get("kfs")
        if isinstance(kfs, list) and 0 <= i < len(kfs):
            return kfs[i]
        return kfs
    return fl


def _audit_issue_field_touched(before_script: "dict | None", after_script: dict,
                               issue) -> bool:
    """本轮是否**改动了该 issue 点名的字段**（v6.108）.

    用于「证据判据没有区分度」时的兜底——见 `_audit_issue_newly_resolved`。
    只回答「模型有没有按 issue 指的地方动手」，不回答「改得对不对」；后者由
    下一轮全量审计裁决（提交循环每轮都会重跑审计）。
    """
    if getattr(issue, "repair_targets", None) is not None:
        from step01_script_gen.repair_fields import merge_authorized, shot_by_target
        for tid, allowed in _audit_paired_fields(before_script, issue).items():
            before = shot_by_target(before_script, tid)
            after = shot_by_target(after_script, tid)
            if not allowed or not isinstance(before, dict) or not isinstance(after, dict):
                continue
            projected, _ = merge_authorized(before, after, allowed)
            if projected != before:
                return True
        return False
    fp = str(getattr(issue, "field_path", "") or "")
    root = fp.replace("[", ".").split(".")[0].strip() if fp else ""
    # v6.113：审计的 field_path 语法很活——`props` / `ff_desc.props` /
    # `frame_layouts.ff[0].props` 都出现过。**裸的布局叶子名**（`props`/`pose`…）
    # 此前不在识别范围内 → 模型明明改了那一帧的布局，却被判「没动过」→
    # `audit_resolved=0` → 整轮回退（第三十一次重跑：shot 2 `props` / shot 5 `props`）。
    _LEAF_ROOTS = ("props", "pose", "position", "gaze_target", "on_screen",
                   "facing", "surface", "name")
    if root in _LEAF_ROOTS:
        fid = str(getattr(issue, "frame_id", "") or "")
        _b = _issue_target_shot(before_script, issue)
        _a = _issue_target_shot(after_script, issue)
        if not isinstance(_b, dict) or not isinstance(_a, dict):
            return False
        return _frame_layout_of(_b, fid) != _frame_layout_of(_a, fid)
    if root not in ("prompt", "ff_desc", "lf_desc", "motion_desc", "color_tone",
                    "keyframes", "frame_layouts"):
        return False
    b = _issue_target_shot(before_script, issue)
    a = _issue_target_shot(after_script, issue)
    if not isinstance(b, dict) or not isinstance(a, dict):
        return False
    if root == "keyframes":
        # 帧类 issue：帧序号可能因补帧整体后移，所以按「该帧的 desc 集合是否变化」
        # 兜底，而不是死扣下标。
        fid = str(getattr(issue, "frame_id", "") or "")
        try:
            idx = int(fid[2:]) if fid.startswith("kf") else -1
        except ValueError:
            idx = -1
        bd = [str(k.get("desc") or "") for k in (b.get("keyframes") or [])
              if isinstance(k, dict)]
        ad = [str(k.get("desc") or "") for k in (a.get("keyframes") or [])
              if isinstance(k, dict)]
        if 0 <= idx < len(bd) and 0 <= idx < len(ad):
            return bd[idx] != ad[idx]
        return bd != ad
    if root == "frame_layouts":
        return b.get("frame_layouts") != a.get("frame_layouts")
    return str(b.get(root) or "") != str(a.get(root) or "")


def _audit_issue_resolved(script: dict, issue: Issue) -> bool:
    """Best-effort verification for an audit-only issue without a second LLM call.

    An audit issue is considered resolved only when its original evidence is no
    longer present in the targeted text.  This is deliberately conservative:
    unverifiable audit issues cannot justify keeping a patch.

    ⚠️ 只在证据**确实出现在改前文本里**时才有区分度——见
    `_audit_issue_newly_resolved`：审计证据常常是自由文本描述（「原文X，但
    ff_desc 写 Y，与原文不符」），它永远不会字面出现在稿子里，此时本函数对
    改前改后都返回 True，等于没有判据。
    """
    evidence = _audit_evidence_text(issue)
    if evidence is None:
        return False
    # v6.120（用户口径）：`shot_id` 为空或**指不明**时，这条 issue 的解决与否
    # **无法证明**（返回 False）。旧实现会走到下面的「镜头已被结构补丁删除」分支
    # 返回 True —— 把「定位不到」当成「已解决」，与「不可验证的审计问题不能用来
    # 保住一轮改动」的既定口径相反（见 docstring）。注意：不是本次 BLOCK 的成因
    # （正式入口传的是修前稿，改前改后同为 True → `newly_resolved` 仍为 False），
    # 这里补的是「不静默伪造结论」这一条。
    _sid = _issue_shot_index(issue)
    if not (_sid == "hook" or _sid.isdigit()):
        return False
    shot = _issue_target_shot(script, issue)
    if shot is None and not any(
            isinstance(s, dict) and str(s.get("index")) == _sid
            for s in (script.get("shots") or [])):
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


# v6.103（总计划阶段 3）：跨字段 issue 的**判据证据**必须留在高优先字段里。
#
# 优先级（总计划 §1.2）：原文/明确旁白 > motion_desc/temporal_plan >
# ff/kf/lf 描述 > frame_layouts > 聚合 prompt。四条跨字段 issue 里，判据证据都
# 落在**比待修字段更高优先**的那一层：
#
#   - 在场/持物：证据在**帧描述**里（布局是更低的派生层）→ 正确修法是改布局；
#   - 结果动作支撑：证据在 **lf_desc** 里（motion 才是要补的那层）→ 正确修法是
#     给 motion 加过程段；
#
# 于是「把描述里那句话删掉」能让检测器立刻闭嘴 —— 那不是修复，是**改写检测
# 输入**，正是总计划 §阶段 3 点名的失败模式（「模型只改写措辞就可能骗过字符串
# 判据」）。`frame_stage_pose_consistency` 不在表里：它的证据是**布局**里的姿势，
# 属于低优先字段，改掉它正是合法修复方向。
#
# ⚠️ v6.103 修（真实重跑实测）：闸门**只**对 `result_action_support` 生效。
# 在场/持物这两类的「哪一侧是错的」由**原文**决定（§1.2 的优先级最高位），
# 确定性层判不了：第二十次重跑里 shot 2 的正解就是改 `ff_desc`（原文写「左手
# 钓杆垂在湖面」），而闸门会把这条正解判成「删证据」→ 整轮 resolved=0 → BLOCK。
# 那两类的方向交给模型按原文切片决定，确定性层只保证「矛盾必须消失」
# （修复后重跑同一检测）+「不许越权改别处」。
# v6.144（2026-09-29，用户批准改判据）：口径从**逐字**改为**事件级**。
# 逐字口径实测把审计自己要求的改写判成「删证据」：第一章第 3 轮的 `minimal_fix`
# 明令把结果句改写为同义表述（「刺中」→「刺入」），照办的修复被按目标回退，
# 下一轮审计再报同一条，三轮空转在同一个镜头上。事件级只拦「这句的结果动作没了」。
_CROSS_FIELD_EVIDENCE_GUARD = {
    "result_action_support": "lf",
}

#: 按 code 走**事件级**口径的清单。事件级 = 「同一结果动作组里任一表述仍在」
#: 即通过（措辞可变）；只有该组**一个表述都不剩**才算「删证据」——那正是让
#: `result_action_support` 闭嘴所需的唯一动作，所以拦它不会误伤合法改写。
#: v6.145（2026-09-29 复核反例）：**逐组**判定——同一分句可命中多组
#: （`_clauses` 不切「、」），并成一次 `any()` 会漏掉「第二个动作整段被删」。
#: 不在表里的 code 只能逐字比：认不出动作**不放行**（准则 7）。
_CROSS_FIELD_EVIDENCE_IDENTITY_CODES = {"result_action_support"}


def _cross_field_evidence_removed(before_state: dict | None, script_after: dict,
                                  picked: set) -> list[str]:
    """跨字段 issue 判据证据在修复后**从高优先字段消失**的清单（空 = 通过）.

    只检查被点名镜头；证据按 `" | "` 拆分后**逐段**判定，两条口径按 code 选：

    - **事件级**（`_CROSS_FIELD_EVIDENCE_IDENTITY_CODES`，v6.144/.145）：证据是
      「含结果动词的整个分句」，要求**每个命中的结果动作组都还剩一个表述**
      （词表单源 `cross_field_checks._RESULT_ACTIONS`）。措辞可变——审计自己的
      `minimal_fix` 就可能明令改写；但**当事角色的替换不在覆盖内**（见
      `result_action_evidence_groups` 的未覆盖声明）；
    - **逐字**（其余 code，或分句里认不出结果动作时）：证据必须原样仍在。
      「认不出」是退回严口径的理由，**不是**放行的理由。
    """
    from step01_script_gen.cross_field_checks import (
        frame_text, result_action_evidence_groups)

    by_key: dict = {}
    for s in script_after.get("shots") or []:
        if isinstance(s, dict):
            by_key[str(s.get("index"))] = s
    if isinstance(script_after.get("hook"), dict):
        by_key["hook"] = script_after["hook"]

    removed: list[str] = []
    for rec in (before_state or {}).get("issues") or []:
        if not isinstance(rec, dict):
            continue
        mode = _CROSS_FIELD_EVIDENCE_GUARD.get(str(rec.get("code") or ""))
        if mode is None:
            continue
        sid = str(rec.get("shot_id") if rec.get("shot_id") is not None else "")
        tid = "hook" if sid in ("0", "hook") else f"shot_{sid}"
        if tid not in picked:
            continue
        shot = by_key.get("hook" if tid == "hook" else sid)
        if not isinstance(shot, dict):
            continue
        frame_id = "lf" if mode == "lf" else str(rec.get("frame_id") or "")
        if not frame_id:
            continue
        text = frame_text(shot, frame_id)
        _code = str(rec.get("code") or "")
        _event_level = _code in _CROSS_FIELD_EVIDENCE_IDENTITY_CODES
        for part in str(rec.get("evidence") or "").split(" | "):
            part = part.strip()
            if not part:
                continue
            _groups = (result_action_evidence_groups(part)
                       if _event_level else ())
            if _groups:
                # **逐组**要求还剩一个表述——并成一次 any() 会让同一分句里的
                # 第二个动作整段被删也判通过（复核反例）。
                _lost = [g for g in _groups if not any(w in text for w in g)]
                if _lost:
                    removed.append(
                        f"{tid}.{frame_id} 结果动作被删除（{_code}）："
                        f"「{part[:40]}」（%d/%d 个动作组无残留）"
                        % (len(_lost), len(_groups))
                    )
            elif part not in text:
                removed.append(
                    f"{tid}.{frame_id} 判据证据被删除（{_code}）：「{part[:40]}」"
                )
    return removed


# v6.104（2026-09-24 总计划 §1.1）：本条修复轮**授权了 KF 列表本身**的镜头，
# 必须通过 `kf_structural_guard` 的两条确定性验收（KF 单调在镜内 / motion 原事件
# 文本仍在、顺序不变）。
#
# 为什么闸门挂在这里而不是 `_validate_replacement_shot`：授权列表只发生在
# 「必须增删 KF 才能修」的问题上，普通修复轮不适用——把单调性检查加到全局
# 校验里，会把「本来就乱序但从未被点名的镜头」也判成越权，重现本仓反复出现过的
# 「假拒绝 → resolved=0」形态。范围收在「本轮拿到列表授权」的镜头上。
def _kf_structural_violations(before_script: dict, after_script: dict,
                              allowed_by_target: dict | None,
                              picked: set, novel_text: str = "") -> list[str]:
    from step01_script_gen.kf_structural_guard import (
        kf_structural_violations, narrow_motion_patch)
    from step01_script_gen.validation import shot_source_window_raw

    def _shot(script: dict, tid: str):
        if tid == "hook":
            return script.get("hook") if isinstance(script.get("hook"), dict) else None
        sid = str(tid)[5:] if str(tid).startswith("shot_") else str(tid)
        for s in script.get("shots") or []:
            if isinstance(s, dict) and str(s.get("index")) == sid:
                return s
        return None

    # v6.146：**名册**（本脚本自己的角色名 + 布局里的名字）——纠错判据用它查
    # 「施事/受事是否被换掉」。取自脚本自身，不写死任何具体角色名。
    _canonical: set = set()
    for _c in (before_script.get("characters") or []):
        if isinstance(_c, dict):
            _nm = str(_c.get("identifier") or "").strip()
            if _nm:
                _canonical.add(_nm)     # 正名：出处关系校验要求「同句含全部当事角色」

    def _collect_names(node) -> None:
        if isinstance(node, dict):
            _nm = str(node.get("name") or "").strip()
            if _nm:
                _roster.add(_nm)
        elif isinstance(node, list):
            for _x in node:
                _collect_names(_x)

    _collect_names(before_script.get("frame_layouts"))
    for _s in (before_script.get("shots") or []):
        if isinstance(_s, dict):
            _collect_names(_s.get("frame_layouts"))
    if isinstance(before_script.get("hook"), dict):
        _collect_names(before_script["hook"].get("frame_layouts"))

    out: list[str] = []
    for tid in picked or ():
        allowed = (allowed_by_target or {}).get(tid) or ()
        _allowed_set = {str(a) for a in allowed}
        if "keyframes" not in _allowed_set:
            continue                      # 本轮未授权改 KF 列表 → 不适用
        before, after = _shot(before_script, tid), _shot(after_script, tid)
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        # v6.149（复核收窄）：本轮**授权改 motion_desc** 时，由**代码**做范围收窄——
        # `narrow_motion_patch` 只采纳通过「同一原文事件里的动作 + 施事/受事」校验的
        # 时段，其余时段**按基线原样合回**（改错了不会被采纳，而不是把整轮判死）。
        # 合回后本函数不再叠加逐字事件保全（`skip_motion`），否则会把刚采纳的时段
        # 判成「原事件文本丢失」。
        _editable_motion = any(a == "motion_desc" or a.startswith("motion_desc.")
                               for a in _allowed_set)
        _skip_motion = False
        if _editable_motion:
            _window, _strict = shot_source_window_raw(before, novel_text)
            _new_motion, _notes = narrow_motion_patch(
                before, after, canonical=_canonical, novel_window=_window,
                window_strict=_strict)
            if _new_motion is None:
                # 结构不可安全合并（段数变了/解析不出）→ 按违例处理，不放行
                out.extend("%s: %s" % (tid, m) for m in _notes)
            else:
                for _n in _notes:
                    _logger.info("[REPAIR] %s 动作范围收窄: %s" % (tid, _n))
                if _new_motion != str(after.get("motion_desc") or ""):
                    # `after` 就是 `after_script` 里的那个镜头对象 → 原地写回即生效
                    after["motion_desc"] = _new_motion
                _skip_motion = True
        out.extend("%s: %s" % (tid, m) for m in kf_structural_violations(
            before, after, skip_motion=_skip_motion))
    return out


def _repair_acceptance_decision(before_state: dict | None,
                                after_state: dict | None,
                                audit_issues: list[Issue] | None,
                                script_after: dict,
                                locked_changed: bool,
                                script_before: dict | None = None,
                                non_target_changed: list | None = None,
                                locked_by_llm_changed: list | None = None,
                                evidence_removed: list | None = None,
                                kf_structural_violations: list | None = None,
                                layout_structural_violations: list | None = None,
                                audit_attempted: set | None = None) -> dict:
    """Pure acceptance decision for one targeted-repair round.

    Required conditions:
      * both checker runs completed successfully;
      * no new blocking issue identity appears;
      * locked fields are unchanged;
      * **未被点名的镜头语义 revision 完全不变**（v6.77 第四阶段新增）——
        修复轮是「镜头事务」，只许动被点名的镜头；
      * **LLM 未改动被点名镜头的锁定字段**（v6.87：基线取「合并后、后处理前」，
        只含 LLM 的改动；后处理是管线自己的确定性归一化，不算越权）；
      * **跨字段 issue 的判据证据没被删掉**（v6.103，总计划阶段 3）——
        删证据能让检测器闭嘴，但那是改写检测输入，不是修复；
      * **增删 KF 的结构修复通过了 §1.1 验收**（v6.104）——KF 落在镜内且按时段
        单调、`motion_desc` 的原事件文本仍在且顺序不变（授权 KF 列表的配套代价）；
      * at least one target issue (deterministic or audit) is verifiably gone.
    """
    # v6.104.1：帧数变了的镜头，kf 类 issue 按「该镜的该类问题」比较（位置序号
    # 在补帧后会整体后移，逐位置比较会把同一缺陷记成「一减一增」的假信号）。
    _reindexed = _reindexed_kf_shots(script_before, script_after)
    before_keys = _blocking_record_keys(before_state, _reindexed)
    after_keys = _blocking_record_keys(after_state, _reindexed)
    resolved = before_keys - after_keys
    new_blocking = after_keys - before_keys
    checker_failed = bool((before_state or {}).get("checker_failed")
                          or (after_state or {}).get("checker_failed"))
    audit_targets = [i for i in (audit_issues or []) if is_blocking(i)]
    # v6.102：audit 判据必须是「**本轮新**解决」——`_audit_issue_resolved` 本身幂等，
    # 拿它当轮内功绩会让空转轮次靠陈旧计数通过验收。
    # v6.139：`audit_attempted` = 本 run 已尝试过（触达兜底记过账）的问题身份集——
    # 同一身份不再给兜底分（见 `_audit_issue_newly_resolved`）。
    _touched_ids: list = []
    audit_field_touched = 0
    for i in audit_targets:
        if _audit_issue_newly_resolved(script_after, script_before, i,
                                       attempted=audit_attempted):
            audit_field_touched += 1
            _touched_ids.append(_audit_issue_identity(i))
    has_targets = bool(before_keys or audit_targets)
    _non_target = list(non_target_changed or [])
    # v6.87：LLM 对锁定字段的写入与「非目标镜头被改动」是**两个不同的闸门**，
    # 分开命名——v6.83 起两者被并在一个列表里、统一报 `non_target_changed`，
    # 结果日志永远指不清是哪一道拦下的（三次运行都是这个 reason，无从定位）。
    _locked_by_llm = list(locked_by_llm_changed or [])
    # v6.103：证据被删是**独立闸门**——它和「新增 blocking」「越权」都是不同性质的
    # 失败，混进任何一条都会让日志指不清方向。
    _evidence_removed = list(evidence_removed or [])
    # v6.104：授权 KF 列表之后，增删必须仍然满足结构验收（独立闸门，理由同上）。
    _kf_struct = list(kf_structural_violations or [])
    # v6.154（复核之二十 P1）：点名**帧子列表**（增删布局条目）之后，只允许纯增删——
    # 保留条目逐字不变、相对顺序不变、非点名帧不动。独立闸门，理由同上：
    # 与「KF 结构」「证据被删」「越权」混在一起会让日志指不清方向。
    _layout_struct = list(layout_structural_violations or [])
    accepted = (
        not checker_failed
        and not new_blocking
        and not locked_changed
        and not _non_target
        and not _locked_by_llm
        and not _evidence_removed
        and not _kf_struct
        and not _layout_struct
        and (not has_targets or bool(resolved) or audit_field_touched > 0)
    )
    reason = ""
    if checker_failed:
        reason = "checker_failed"
    elif new_blocking:
        reason = "new_blocking"
    elif _evidence_removed:
        reason = "evidence_removed"
    elif _kf_struct:
        reason = "kf_structure_invalid"
    elif _layout_struct:
        reason = "layout_structure_invalid"
    elif locked_changed:
        reason = "locked_changed"
    elif _locked_by_llm:
        reason = "locked_field_changed"
    elif _non_target:
        reason = "non_target_changed"
    elif has_targets and not resolved and audit_field_touched <= 0:
        reason = "no_target_resolved"
    return {
        "accepted": accepted,
        "reason": reason,
        "before": len(before_keys),
        "after": len(after_keys),
        "resolved": len(resolved),
        "new_blocking": len(new_blocking),
        # 诊断（v6.104.1）：被拒时必须说清「新增了什么」——此前只有计数，
        # 排查只能靠复现整轮运行（且日志行会被 581 字符截断）。
        # v6.119：**不截断**。此前是 `[:6]`，而这份 keys 不只用于日志——`_new_blocking_targets`
        # 用它定位「肇事目标」，截断会让按目标回退**漏掉**目标（正确性问题），
        # 台账也因此永远追不全。日志侧按需取前 N 条并显式标出「另 M 条」。
        "new_blocking_keys": sorted(new_blocking),
        "resolved_keys": sorted(resolved)[:6],
        "reindexed_kf_shots": sorted(_reindexed),
        "audit_targets": len(audit_targets),
        # v6.115（审核）：**如实命名**——自由文本证据下它只证明「点名字段被改过」，
        # 不证明矛盾消失。真实解决数以**修复后的审计复核**为准，不得用它推导
        # 「每轮净修 N 条」（那会高估修复效率）。
        "audit_field_touched": audit_field_touched,
        # v6.139：本轮靠兜底记账的问题身份（shot, family）——调用方据此维护
        # 「已尝试未核实」集合，同一身份第二轮起不再给兜底分。
        "audit_touched_ids": _touched_ids,
        "checker_failed": checker_failed,
        "locked_changed": bool(locked_changed),
        "non_target_changed": _non_target,
        "locked_by_llm_changed": _locked_by_llm,
        "evidence_removed": _evidence_removed,
        "kf_structural_violations": _kf_struct,
        "layout_structural_violations": _layout_struct,
    }


# v6.84（二次审核发现 3 实施 #4）：每轮定向修复的镜头数。
# 6 个完整镜头对象 + 上下文会顶到 8000 token 上限（本次实测 `finish_reason=length`
# 整份丢弃）；按 3 镜分批，靠**批数**而不是靠抬高 token 上限来解决上下文体积。
# 每轮名下的镜头数（v6.101：3 → 4）。配套把 `REPAIR_MAX_OUTPUT_TOKENS`
# 从 8000 抬到 16000——4 个完整镜头对象 + 上下文按实测约 5-7k token，
# 16k 有 2 倍余量；**不靠抬 token 掩盖上下文体积**这条原则仍然成立，
# 所以批量只加到 4，不是 6/8。
_REPAIR_MAX_SHOTS_PER_ROUND = 4

# v6.122（用户决定 B，2026-09-25）：**联动组的专用上限**。
#
# `_REPAIR_MAX_SHOTS_PER_ROUND`（4）继续管**互不相关**的目标（它约束的是 prompt
# 体积）。但一个**原子联动组**既不能被拆（拆 = 半条修复，正是 shot 5 被整镜回退的
# 成因），也不该被容量规则毙掉——毙掉它等于**结构性无法提交**：同一份稿的审计每轮
# 都会声明同一组，而这条问题能力上可修。
#
# 实测 run 20260925T145218-32008：一条跨镜问题声明 5 镜（4 镜 prompt + 1 镜 ff_desc，
# 已按 audited revision 逐字核实**全部必要**），当时上限 4 → 组永远进不了本轮 →
# 只剩 BLOCK 一条路。
#
# 6 的来历：4 个完整镜头对象实测 5-7k token，输出上限 16k；6 镜约 8-10k，仍在 16k
# 内——**不靠抬 token 预算掩盖上下文体积**这条原则不变。超过 6 的组属「一次改不完」
# → 调用方**立即 BLOCK**，不再空耗修复轮与审计调用。
_REPAIR_MAX_SHOTS_PER_GROUP = 6


def _shot_by_target_id(script: dict, tid: str):
    """按修复目标名取镜头对象（`shot_N` / 裸镜号 / `hook`）——取不到返回 None."""
    if not isinstance(script, dict):
        return None
    if str(tid) == "hook":
        return script.get("hook") if isinstance(script.get("hook"), dict) else None
    sid = str(tid)[5:] if str(tid).startswith("shot_") else str(tid)
    for s in script.get("shots") or []:
        if isinstance(s, dict) and str(s.get("index")) == sid:
            return s
    return None


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


def _deterministic_targets(issues) -> set:
    """本轮带**确定性**阻塞的目标 id 集合（`shot_<id>` / `hook`）.

    `model_audit_issue` 是模型审计条目，不算确定性阻塞——验收里 `resolved`
    计量的是前者（`audit_resolved` 是另一条并行判据），质量门也按前者判 BLOCK。
    """
    out: set = set()
    for rec in issues or ():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("code") or "") == "model_audit_issue":
            continue
        if str(rec.get("severity") or "") not in ("hard", "visual_risk"):
            continue
        sid = rec.get("shot_id")
        if sid is None:
            continue
        out.add("hook" if str(sid) in ("0", "hook") else f"shot_{sid}")
    return out


def _pick_repair_targets(shot_items: dict, deterministic: set, limit: int,
                         excluded=()) -> dict:
    """本轮修复目标（v6.107）：**带确定性阻塞的镜头优先**，其次按告警条数.

    实测（第二十九次重跑）：提交点第 1 轮有 1 条确定性阻塞 + 4 条审计问题，4 个名额
    按告警条数排序时两类打平 → 带确定性阻塞的那一镜掉出前 4 → 该轮**根本没尝试**
    它，`resolved=0` → 停止修复 → BLOCK。质量门计量的是确定性阻塞，名额分配必须
    先满足它，否则修复预算花在计量不到的目标上。

    v6.138（九审 P1-2）：`excluded`（上轮肇事目标）在**排序取名额之前**过滤——
    旧实现"取前 4 再剔除"会让被剔除目标的名额空转（后续目标补不上来）。反例：
    5 个目标、前 2 个被排除 → 旧实现本轮只修 2 镜；本实现修满 4 镜。
    """
    import itertools as _it
    _ex = set(excluded or ())
    _pool = {k: v for k, v in shot_items.items() if k not in _ex}
    return dict(_it.islice(
        sorted(_pool.items(),
               key=lambda kv: (0 if kv[0] in deterministic else 1, -len(kv[1]))),
        limit))


def _new_blocking_targets(keys, picked) -> set:
    """新增 blocking 落在哪些**本轮点名的**目标上（v6.105）.

    返回空集表示**无法定位**：出现了全局问题（`shot_id` 为空）或落在非目标镜头上
    ——后者不是本轮写入造成的，回退被点名的镜头解决不了它，只能整轮回退。
    """
    out: set = set()
    for key in keys or ():
        try:
            sid = str(key[1])
        except Exception:
            return set()
        if not sid:
            return set()
        tid = "hook" if sid in ("0", "hook") else f"shot_{sid}"
        if tid not in picked:
            return set()
        out.add(tid)
    return out


#: `_final_field_mask_violations` 的消息形状：`shot <id>.<字段>: 旧 -> 新`
_LOCKED_VIOLATION_RE = re.compile(r"^shot (\S+?)\.(\w+):")
#: `_kf_structural_violations` 的消息形状：`<目标名>: <违例说明>`
_KF_STRUCT_VIOLATION_RE = re.compile(r"^([A-Za-z_0-9]+):\s")


def _norm_target_name(raw) -> str:
    """把消息里的目标名归一到 `shot_<id>` / `hook`（与本文件其余口径一致）."""
    _t = str(raw or "").strip()
    if not _t:
        return ""
    if _t in ("0", "hook"):
        return "hook"
    if _t.startswith("shot_"):
        return _t
    return f"shot_{_t}"


def _locate_offending_targets(decision: dict, picked: set,
                              locked_diff: list | None = None) -> set:
    """从闸门给出的消息里定位**肇事目标**（v6.143 P4，纯函数）.

    为什么要它：`locked_changed` / `locked_field_changed` / `kf_structure_invalid`
    三个闸门此前都只给出**轮级**结论，于是「一处越界 → 整轮 `resolved` 清零」——
    实测 10 次跑 20 个修复轮算出 `resolved=109`，其中 **60 条（55%）**被整轮丢弃，
    `run 03` 一次就丢 13 条。

    可定位性来自消息本身（都是既有格式，不新增字段）：
      · `locked_field_changed` → `_final_field_mask_violations` 的 `shot <id>.<字段>: 旧 -> 新`；
      · `locked_changed`（round 级签名变化，可能来自**已授权**的锁定字段）→
        回退到 `_locked_signature_diff` 的 `<id>.<字段>: 旧 -> 新`（由调用方传入）；
      · `kf_structure_invalid` → `_kf_structural_violations` 的 `<目标名>: 违例`；
      · `layout_structure_invalid` → `frame_layout_structural_violations` 的同形状消息。

    **不可定位**的一律返回空集 → 调用方保持**整轮**回退语义（不是放松）：
      · `non_target_changed`：肇事镜头在 `picked` **之外**，回退被点名镜头解决不了；
      · `evidence_removed` / `checker_failed`：前者是「改写检测输入」这类整体性违规，
        后者是基础设施失败——都不该按目标部分采纳。

    只返回**本轮点名的**目标：定位到 `picked` 之外说明归因有误，宁可退回整轮。
    """
    out: set = set()
    for _msg in (decision.get("locked_by_llm_changed") or []):
        _m = _LOCKED_VIOLATION_RE.match(str(_msg))
        if _m:
            _tid = _norm_target_name(_m.group(1))
            if _tid in picked:
                out.add(_tid)
    for _msg in ((decision.get("kf_structural_violations") or [])
                 + (decision.get("layout_structural_violations") or [])):
        _m = _KF_STRUCT_VIOLATION_RE.match(str(_msg))
        if _m:
            _tid = _norm_target_name(_m.group(1))
            if _tid in picked:
                out.add(_tid)
    if not out:
        for _msg in (locked_diff or []):
            _m = re.match(r"^(\S+?)\.\w+: ", str(_msg))
            if _m:
                _tid = _norm_target_name(_m.group(1))
                if _tid in picked:
                    out.add(_tid)
    return out



def _linked_repair_groups(audit_issues, shots_by_id: dict, picked, *, cap: int) -> tuple:
    """把审计的**成对修复目标**折成「联动组」（v6.119，纯函数）.

    一条 issue 的 `repair_targets` 声明了「要修好这条问题必须一起改的 (镜头, 字段)」。
    本函数只做分组，不做授权、不改稿子——**同一组必须整组进本轮**，因为半次修复会
    制造新的自相矛盾（实测 shot 5：只授权 `keyframes[2].desc`、回填 `stage` 与布局
    `props` → 半条修复引入 3 条新阻塞 → 整镜回退、缺陷零进展直至 BLOCK）。

    规则：
      ① 组内镜头与本轮目标（或问题**定位**所在镜头）有交集才算「本轮相关」；
      ② 共享镜头的组**合并**（否则同一批镜头会被拆成两轮各修一半）；
      ③ 组自身超过 `cap` → 归入 `oversize`，调用方**整组放弃**（留 BLOCK，
         不拆半次）；
      ④ 只有**校验通过**的成对条目参与（镜头存在 + 路径是该镜真实叶子路径）。

    Returns:
        `(groups, rejects, oversize)`；`groups` 是 `[frozenset(target 名)]`。
    """
    from step01_script_gen.repair_fields import (
        validate_repair_targets, _target_id_of, _is_structural_code,
    )
    # ① 折候选组：每组记「成员」与「相关锚点」（成员 ∪ 问题**定位**所在镜头）
    candidates: list = []
    rejects: list = []
    for _ai in (audit_issues or []):
        # `None` = 旧条目（无成对目标，靠定位挑镜头）；`[]` = 显式「无修复目标」
        # （两态都不构成联动组，但语义不同：后者是 fail-closed 的不授权）
        _declared = getattr(_ai, "repair_targets", None)
        if not _declared:
            continue
        _code = str(getattr(_ai, "code", "") or "")
        _valid, _rejected = validate_repair_targets(
            _declared, shots_by_id, structural=_is_structural_code(_code))
        rejects.extend(_rejected)
        if _rejected:
            # 整条拒绝：有一条目标无效就不成组、不留半组（用户口径）
            continue
        _members = {_target_id_of(_p["shot_id"]) for _p in _valid}
        _members.discard("")
        if not _members:
            continue
        candidates.append((frozenset(_members),
                           frozenset(_members | {_target_id_of(
                               getattr(_ai, "shot_id", ""))})))
    # ② **闭包**：锚点与本轮目标集相交的组被纳入；纳入后其成员也进入目标集，
    #    可能让本来「无关」的组变相关（组 A 带进 shot_7 → 定位在 shot_7 的组 B
    #    随之相关）。只对初始 `picked` 求一次交集会漏掉 B，联动链就断在半途。
    _targets = set(picked)
    _admitted: list = []
    _changed = True
    while _changed:
        _changed = False
        for _cand in list(candidates):
            _members, _anchors = _cand
            if _members <= _targets:
                # 组内镜头**已经全在本轮目标里** → 仍必须**登记为组**，不能直接丢弃
                # 候选：否则后续「按目标回退」只回退组内肇事的那一镜、留下半组
                # （用户口径：整组修 / 整组验收 / 整组回退）。
                _admitted.append(_members)
                candidates.remove(_cand)
                continue
            if _anchors & _targets:
                _admitted.append(_members)
                _targets |= _members
                candidates.remove(_cand)
                _changed = True
    # ③ 合并共享镜头的组（否则同一批镜头会被拆成两轮各修一半）
    groups: list = []
    for _members in _admitted:
        _cur = set(_members)
        for _other in list(groups):
            if _cur & _other:
                _cur |= _other
                groups.remove(_other)
        groups.append(frozenset(_cur))
    oversize = [g for g in groups if len(g) > cap]
    return [g for g in groups if len(g) <= cap], rejects, oversize


def _expand_rollback_by_groups(offend: set, groups) -> tuple:
    """把「回退目标集」按联动组补全：组内任一镜肇事 → 整组回退（v6.119，纯函数）.

    Returns `(补全后的目标集, 被整组回退的组列表)`。
    """
    hit: list = []
    out = set(offend or ())
    for _grp in (groups or ()):
        if out & _grp:
            hit.append(sorted(_grp))
            out |= _grp
    return out, hit


def _restore_targets(script: dict, before_script: dict, targets: set) -> dict:
    """返回副本：把 `targets` 里的镜头还原为本轮开始前的版本，其余保持现状（v6.105）.

    与 `_restore_untargeted` 的分工：那个用于**未点名**镜头（后处理副作用），
    本函数用于**被点名但引入了新阻塞**的目标——「逐目标提交」要成立，验收侧也必须
    能只回退坏的那个目标，而不是连坐同轮其它合法修复。
    """
    import copy as _c
    out = _c.deepcopy(script)
    if "hook" in targets:
        old_hook = before_script.get("hook")
        if isinstance(out.get("hook"), dict) and isinstance(old_hook, dict):
            out["hook"] = _c.deepcopy(old_hook)
    want = {str(t)[5:] for t in (targets or ()) if str(t).startswith("shot_")}
    if not want:
        return out
    old_by_id = {str(b.get("index")): b
                 for b in (before_script.get("shots") or []) if isinstance(b, dict)}
    shots = out.get("shots") or []
    for i, sh in enumerate(shots):
        if isinstance(sh, dict) and str(sh.get("index")) in want:
            old = old_by_id.get(str(sh.get("index")))
            if isinstance(old, dict):
                shots[i] = _c.deepcopy(old)
    return out


def _record_repair_call_failure(repair_diag: list | None, repair_round: int, *,
                                error: str, truncated: bool = False,
                                usage: dict | None = None,
                                max_tokens: int | None = None) -> None:
    """把"修复调用没拿到完整结果"记进修复台账（用户 2026-09-25）.

    为什么必须单独记：2026-09-25 实跑的 BLOCK 直接原因是定向修复响应被截断
    （`incomplete_details.reason=max_output_tokens`，16k 上限含思维链 token），
    整份丢弃后稿件一个字没变，提交层只看到 revision 相同 → 记为"零改善"。
    于是**结构化诊断里没有"导致 BLOCK 的直接原因"**，只能翻 `step01.log`。
    这里把调用失败与"改了但没进展"分开落台账；`usage` 一并留下，为上限该设多大提供实测依据。
    **口径（2026-09-26 复核更正）**：`output_tokens` 已包含思维链 token，`reasoning_tokens`
    只是它内部的明细——余量只看 `output_tokens`（见 `_repair_cap_margin`），
    两个数相加会把思维链扣两遍。
    """
    if repair_diag is None:
        return
    _u = dict(usage or {})
    repair_diag.append({
        "repair_round": repair_round,
        "kind": "call_failed",          # 与"accepted/zero_gain"那类记录区分开
        "truncated": bool(truncated),
        "error": error[:400],
        "usage": _u,
        "max_tokens": max_tokens,       # 本轮**请求**的上限；None = 未收窄（跟随通道全局值）
        "reasoning_tokens": _u.get("reasoning_tokens"),
        "output_tokens": _u.get("output_tokens") or _u.get("completion_tokens"),
    })


def _record_repair_unusable(repair_diag: list | None, repair_round: int, *,
                            reason: str, detail: str = "",
                            raw_head: str = "") -> None:
    """记一次"拿到了完整响应、但补丁不可用"（`kind="patch_unusable"`）.

    v6.133（2026-09-26 用户复核）：此前只有 **API 调用失败/截断**才算 `call_failed`，
    而「调用成功但补丁 JSON 解析失败 / envelope 结构无效 / 没返回任何替换 /
    替换全被校验拒」这四条早退路径**只打日志**——提交层看不到记录，
    于是一律报 `no_progress`，把"收到不可用响应"说成了"修复没有进展"。
    现在这四条各带 `reason` 落台账，提交层据此把它报成 `patch_unusable`。

    `reason` 取值：`parse_failed` / `invalid_structure` / `empty_patch` /
    `rejected_by_validation`（另可用 `budget_exhausted` 之类后续扩）。

    `raw_head`：**响应原文的头部**（封顶 `_DIAG_RAW_HEAD_MAX`）。与审计侧同一条理由：
    只留一句错误文案时，"模型回了一段散文"和"JSON 断在中间"事后分不开。
    """
    if repair_diag is None:
        return
    _raw = str(raw_head or "")
    repair_diag.append({
        "repair_round": repair_round,
        "kind": "patch_unusable",
        "reason": str(reason),
        "detail": str(detail)[:300],
        "raw_text_head": _raw[:_DIAG_RAW_HEAD_MAX],
        "raw_text_len": len(_raw),
    })


def _repair_max_output_tokens() -> int | None:
    """修复轮的输出上限：**默认为 `None`** = 跟随所在文本通道的全局上限（不额外收窄）.

    v6.133（用户 2026-09-25，实跑 BLOCK 根因）：这里原来硬编码 `16000`，注释理由是
    "agent_plan 的全局上限是 32768，16k 不触顶，留了 2 倍余量"——该假设在**文本通道切到
    原生 DeepSeek 后不再成立**，而 `.env.content` 对修复轮的推荐值本来就是 **50000**：

      - `DEEPSEEK_MAX_TOKENS=50000`，其注释写明"推荐分镜生成场景：40000-50000；
        钩子：8000；**修复轮：50000**"；
      - 且 `max_output_tokens` **包含思维链 token**（provider 注释与审计路径实测同证）。

    ⚠️ **适用范围**（2026-09-26 复核）：只有 **DeepSeek**（`req.max_tokens or
    config.DEEPSEEK_MAX_TOKENS`）与 **AgentPlan**（`AGENT_PLAN_TEXT_MAX_TOKENS`）
    这两条通道有项目级回退，默认 `None` 才等于"跟随通道全局值"；**DashScope / Agnes
    把 `req.max_tokens` 原样透传**，`None` 不会回退到任何项目上限（交给服务端默认）。
    文本通道切到这两条时，下面这段的前提不成立。

    实测（2026-09-25，`.tmp/repair_truncation_probe.py`，真实修复路径两次调用）：
    同一请求同一 16000 上限，**一次截断**（`incomplete_details.reason=max_output_tokens`，
    整份 JSON 丢弃 → 稿件零变化 → 提交层报"零改善"→ BLOCK）、**一次成功**，
    成功那次 `output_tokens=8172`（其中思维链 3695；**余量按 `output_tokens` 算**：
    16000−8172≈7.8k，不能再减一次 `reasoning_tokens`）；把上限放到
    32768 后两次都成功（`output 7553 / reasoning 2380`）。⇒ 16k 只是"典型调用的 2 倍"，
    思维链一长就踩线；这跟同一天审计路径 17:33 的截断是**同一个根因**（当时 16000 → 32000）。

    保留 `REPAIR_MAX_OUTPUT_TOKENS` 作为**显式收窄**的手段（设了才生效，值非法则 WARN 后
    回退为不传——宁可放宽也不要因为一个手误把上限变小）。
    """
    _v = os.getenv("REPAIR_MAX_OUTPUT_TOKENS", "").strip()
    if not _v:
        return None
    try:
        _n = int(_v)
    except ValueError:
        _logger.warn(f"[WARN] REPAIR_MAX_OUTPUT_TOKENS={_v!r} 不是整数，本轮不设上限"
                     f"（跟随文本通道全局值）")
        return None
    # v6.133（2026-09-26 用户复核）：本变量**只用于收窄**——`0`/负数不是合法上限，
    # 高于当前通道项目上限的值会**放宽**上限（与说明矛盾）。两者都不接受：
    # 记 WARN 后当作"未设置"，跟随通道全局值（宁可放宽也不因手误把上限变小，
    # 更不能因为一个手误把契约说反）。
    if _n <= 0:
        _logger.warn(f"[WARN] REPAIR_MAX_OUTPUT_TOKENS={_v!r} 不是正数，本轮不设上限"
                     f"（跟随文本通道全局值）")
        return None
    _chan_cap = _channel_text_max_tokens()
    if _chan_cap and _n > _chan_cap:
        _logger.warn(f"[WARN] REPAIR_MAX_OUTPUT_TOKENS={_n} 高于当前文本通道"
                     f"（{_active_text_provider() or '未知'}）的项目上限 {_chan_cap}——"
                     f"该变量只用于收窄，本轮忽略它（跟随通道全局值 {_chan_cap}）")
        return None
    return _n


def _channel_text_max_tokens() -> int | None:
    """当前文本通道的**项目级**输出上限；该通道没有项目级上限时返回 `None`.

    DeepSeek / AgentPlan 在 provider 里做 `req.max_tokens or config.X` 回退；
    DashScope / Agnes 把 `req.max_tokens` 原样透传（没有项目级上限）——
    对它们而言"收窄"没有可比较的基准，`REPAIR_MAX_OUTPUT_TOKENS` 原样生效。
    """
    _p = (_active_text_provider() or "").strip().lower()
    try:
        from _text_gen.config import config as _tcfg
    except Exception:
        return None
    if _p == "deepseek":
        return getattr(_tcfg, "DEEPSEEK_MAX_TOKENS", None)
    if _p == "agent_plan":
        return getattr(_tcfg, "AGENT_PLAN_TEXT_MAX_TOKENS", None)
    return None


def _repair_cap_margin(record: dict) -> int | None:
    """单条修复调用记录的**剩余上限**（`max_tokens - output_tokens`）；数不全则 `None`.

    **口径（2026-09-26 用户复核更正）**：`output_tokens`（= `completion_tokens`）
    **已经包含**思维链 token，`reasoning_tokens` 只是它内部的明细
    （DeepSeek 官方把后者列在 `output_tokens_details` 下）——所以**不能**再减一次
    `reasoning_tokens`，那会把思维链扣两遍。两个数都留在台账里：
    `output_tokens` 用来算余量，`reasoning_tokens` 用来看思维链占比。
    """
    _cap = record.get("max_tokens")
    _out = record.get("output_tokens") or record.get("completion_tokens")
    if _cap is None or _out is None:
        return None
    try:
        return int(_cap) - int(_out)
    except (TypeError, ValueError):
        return None


def _record_repair_call_usage(repair_diag: list | None, repair_round: int, *,
                              usage: dict | None = None,
                              max_tokens: int | None = None) -> None:
    """记一次**成功**的修复调用的 token 用量（`kind="call_ok"`）.

    用途是把"上限够不够"从感觉变成数字：`REPAIR_MAX_OUTPUT_TOKENS` 是**输出上限**，
    而 DeepSeek Responses 路径上 `max_output_tokens` **包含思维链 token**——所以要看的是
    `max_tokens - output_tokens`（见 `_repair_cap_margin`；**不**再减 `reasoning_tokens`）。
    2026-09-25：同一请求同一 16000 上限，一次 `incomplete/max_output_tokens` 截断、
    一次成功 ⇒ 余量已在临界，抬上限要有数字依据。
    """
    if repair_diag is None:
        return
    _u = dict(usage or {})
    _rec = {
        "repair_round": repair_round,
        "kind": "call_ok",
        "usage": _u,
        "max_tokens": max_tokens,
        "reasoning_tokens": _u.get("reasoning_tokens"),
        "output_tokens": _u.get("output_tokens") or _u.get("completion_tokens"),
        "total_tokens": _u.get("total_tokens"),
        # v6.152（D2）：**另加**总预算剩余秒数。上面的「余量」是**输出 token 余量**
        # （`max_tokens − output_tokens`），口径不同、保留不动；两者并排放，
        # 才看得出「这次离 token 上限多近」与「本跑还剩多少时间」。
        "budget_left_sec": _budget_left_sec(),
    }
    repair_diag.append(_rec)
    # 把"余量"直接打进 step01.log：不必翻台账就知道这次离上限多近（口径同上）
    _margin = _repair_cap_margin(_rec)
    _logger.info("[REPAIR] 输出用量 output_tokens=%s（其中思维链 %s）｜本轮上限 %s"
                 "｜余量 %s｜总预算剩余 %s 秒"
                 % (_rec["output_tokens"], _rec["reasoning_tokens"],
                    max_tokens if max_tokens is not None else "通道全局值",
                    _margin if _margin is not None else "未知",
                    _rec["budget_left_sec"] if _rec["budget_left_sec"] is not None
                    else "未知"))


REPAIR_PROVIDER_ENV = "REPAIR_TEXT_PROVIDER"
REPAIR_MODEL_ENV = "REPAIR_TEXT_MODEL"


def resolve_repair_channel() -> tuple:
    """定向修复的通道覆盖 → `(provider | None, model | None)`；未配置时 `(None, None)`.

    为什么要单开（2026-09-28 实测）：agent_plan 通道把 `json_schema` 降级成
    `response_format=json_object`（`REPAIR_SHOT_REPLACEMENT_SCHEMA` 整体失效），
    `deepseek-v4.1-flash` 在该条件下**整镜原样回抄**——6/6 次 `resolved=0`、
    `audit_field_touched=0`、没有任何「授权字段实际改动」行，修复轮等于不可用；
    原生 deepseek 通道保留 strict json_schema。

    解析语义（三态、缺模型显式抛错）见单一来源 `shared.text_channel.resolve_channel`。
    """
    from shared.text_channel import resolve_channel
    return resolve_channel(REPAIR_PROVIDER_ENV, REPAIR_MODEL_ENV)


def _frame_list_path(frame_id) -> str:
    """`ff` / `lf` / `kfN` → `frame_layouts.ff` / `.lf` / `.kfs[N]`（指不明 = 空串）.

    与 `repair_fields._layout_frame_lists` 同一套坐标（帧键 → 那一帧的人物列表），
    这里只做**方向相反**的换算：从问题定位用的 `frame_id` 得到那一帧的路径。
    """
    _f = str(frame_id or "").strip().lower()
    if _f in ("ff", "lf"):
        return "frame_layouts.%s" % _f
    if _f.startswith("kf") and _f[2:].isdigit():
        return "frame_layouts.kfs[%d]" % int(_f[2:])
    return ""


def _path_covered_by(tid: str, path: str, allowed_by_target: dict,
                     frame_id: str = "") -> bool:
    """`(镜头, 路径)` 是否落在该镜**自己**的授权面里（跨镜同名字段不算覆盖）.

    复核 2026-10-04 P1-3：旧写法对所有入轮目标求"任一路径被任一目标授权"，
    于是**镜 9 的问题会被镜 10 同名字段的授权判成"已覆盖"**——覆盖判据必须绑定镜头身份。

    第三轮的补充：覆盖要认**父帧授权**。实测反例——blocking 定位在同镜 `kf0/pose`，
    另一条审计授权的是那一帧的人物列表 `frame_layouts.kfs[0]`；按列表授权**删掉姿态
    错误的重复条目**是合法修法（真实守卫通过、验收 `accepted=True`），而只按"授权路径
    末段等于 `pose`"判会把这一轮当成零能力**提前跳过**。`frame_id` 正是生产者给的
    那一位身份，用它换算成帧路径去比，不靠猜。

    裸叶子（`field_path` 只写 `pose` / `props`）另按"该镜有哪条授权路径以它收尾"判。
    """
    from step01_script_gen.repair_fields import _norm_keep_index, path_allowed
    _allowed = set((allowed_by_target or {}).get(str(tid or "")) or ())
    if not _allowed:
        return False
    if path and path_allowed(path, _allowed):
        return True
    _frame = _frame_list_path(frame_id)
    if _frame and path_allowed(_frame, _allowed):
        return True
    if path and "." not in path and "[" not in path:
        return any(_norm_keep_index(a).split(".")[-1] == path for a in _allowed)
    return False


def _repair_blocking_coverage(blocking_issues, det_targets, allowed_by_target) -> list:
    """本轮入轮目标里，**能改到某条 blocking 点名字段**的那些目标（空 = 注定零产出）.

    v6.155（2026-10-04 实测根因，交付台账 §19.2）：章 2 提交前的最后一轮，5 条 blocking
    的 `repair_targets` 全为空 ⇒ 它们一条字段也授权不出来，而修复轮照样发起了调用：
    模型按审计建议新建了 `frame_layouts.ff` / `lf`、删了 `kfs` 里的重复条目、改了
    `kfs[1][0].pose` —— 全部被判「未授权路径已回填」，`字段触达=0` ⇒
    `no_target_resolved` ⇒ 整轮回退 ⇒ BLOCK。**这一轮从开场就没有能成功的路径。**

    判据（复核 2026-10-04 P1-3 修正后）分两路，缺一路就会跳过**原本能成功**的轮次：

    ① **确定性阻塞**：它的改善出口是 `before_keys - after_keys`（与路径无关，见
       `_repair_acceptance_decision`）——只要**带确定性阻塞的目标**在本轮拿到任何授权，
       就有改善出口。旧写法只喂模型审计 blocking，于是"审计缺授权 + 同镜确定性姿态问题
       有合法授权"这类轮次被提前返回基线，而真实验收本会给出 `accepted=True, resolved=1`。
    ② **模型审计阻塞**：按 `(镜头, 路径)` 判——`repair_targets` 的每一对用它自己的
       `shot_id`，定位字段用它自己的 `shot_id`；**跨镜同名字段不算覆盖**。

    **不能**拿 `_issue_reaches` 当判据：它答的是「这条问题授权了哪些目标」，而模型审计
    成对目标缺失时它一律返回 False——可本轮完全可能**经由确定性告警**授权了同一条路径
    （实测：`field_path=prompt` 的审计问题，授权来自告警路径，修复轮照样能把它记成已触达）。

    与 `_unauthorized_targets` 同一口径：不放开授权去硬修，只是不再为一个
    不可能成功的轮次付代价。返回的是**参与覆盖的目标名**（供日志与单测断言）。
    """
    from step01_script_gen.repair_fields import _target_id_of
    cover: list = []

    def _add(_tid: str) -> None:
        if _tid and _tid not in cover:
            cover.append(_tid)

    # ① 确定性阻塞：目标带确定性阻塞且本轮有授权 ⇒ 有改善出口
    for _tid in (det_targets or ()):
        if str(_tid) in (allowed_by_target or {}):
            _add(str(_tid))
    # ② 模型审计阻塞：按 (镜头, 路径) 判，绑定各自的镜头身份
    for _ai in (blocking_issues or []):
        _sid = getattr(_ai, "shot_id", None)
        for _p in (getattr(_ai, "repair_targets", None) or []):
            if not isinstance(_p, dict):
                continue
            if _path_covered_by(_target_id_of(_p.get("shot_id")),
                                str(_p.get("field_path") or "").strip(),
                                allowed_by_target):
                _add(_target_id_of(_p.get("shot_id")))
        _fp = str(getattr(_ai, "field_path", "") or "").strip()
        if _fp and _path_covered_by(_target_id_of(_sid), _fp, allowed_by_target,
                                    getattr(_ai, "frame_id", "")):
            _add(_target_id_of(_sid))
    return cover


def _repair_shot_warnings(script: dict, novel_text: str,
                          bank=None, objects_bank=None,
                          model: str | None = None,
                          audit_issues: list[Issue] | None = None,
                          max_rounds: int | None = None,
                          repair_diag: list | None = None,
                          block_reasons: list | None = None,
                          retry_state: dict | None = None,
                          audit_attempted: set | None = None,
                          defer_targets: set | None = None,
                          effect_state: dict | None = None,
                          audit_relations: dict | None = None) -> dict:
    """定向修复轮（v6.35，P18 闭环核心）。

    GAP 通过后仍有高价值告警 → 只重写告警镜头的相关字段，重新校验，收敛即停。
    全脚本重生成会把没问题的镜头也重画一遍（白烧额度 + 引入新随机）；
    单镜修复只动有问题的地方，且每次轮次只挑「最值得修」的镜头（防 prompt 爆炸）。

    v6.43 08-25 HARD-only 策略：
    - 无 HARD 违例时（_has_hard=False）**整轮跳过**，节省 60s LLM 调用
    - WARN/soft-suggestion 全部由 hard_fix 兜底（runtime_extend / 12 个 constraint）
    - 实测：精简 prompt + hard_fix 增强后，HARD 违例基本为 0，**修复轮几乎不再触发**

    Args:
        repair_diag: 可选。给了就把**每一轮的修复事务诊断**追加进去（调用方写台账）：
            轮次 / 目标 / 联动组 / **回退前的 new_blocking_keys** / 回退了哪些目标 /
            接受与否 / resolved / 修复前后 revision。早退也会追加——记录点在决策落地
            之后、早退之前，所以「本轮为什么失败」不会随 `return` 一起丢掉。

    Returns:
        修复后的 script（任一轮失败/无告警则原样返回）。
    """
    # v6.87：**合并后立刻**抓的「LLM 写的锁定字段」快照（循环内赋值）。在这里
    # 预置，避免「某轮提交了替换却没走到赋值」时尾部读到未定义名——v6.83 的
    # `allowed_targets` 未定义名事故就是这个形态（被外层 except 吞掉、整段接受
    # 判定从未生效）。
    _mask_violations_after_patch: list = []
    # v6.104.1：KF 结构验收快照（合并后立刻取，见循环内）
    _kf_struct_after_patch: list = []

    # v6.94（2026-09-22 实测）：④ 的根因**不在这里**——是轮尾 `run_hard_fix` 跑到
    # `shot_index_renumber`（它没有 repair-loop 守卫），把结构收敛轮拆出的半整数
    # 子镜归一成整数，其后所有 index 平移；而 `_snapshot_untargeted` 与本轮点名的
    # `picked` / audit `shot_id` 都按 index 标识镜头 → 还原到错镜 + 找不到目标
    # → `非目标镜头被改动 9 处: [13, 13.333, 14, 14.25, 14.5]` 这种**假拒绝**。
    #
    # 修法在 `shot_index_renumber` 一侧（加 `ctx.is_in_repair_loop` 守卫，轮内不
    # 重编号），本函数不再需要「轮首先跑到幂等」那种绕法——实测它反而会把 fixture
    # 的 `index=7` 改成 `1`，导致 audit issue 的 `shot_id` 失配、修复轮提前退出。

    # v6.111（审核 P1-1）：**本轮次上限由调用方给**。
    #
    # 提交点的预算契约是「最多 2 个修复轮」（总计划 §1.1，用户已确认），而本函数
    # 自身默认跑 `_REPAIR_MAX_ROUNDS = 3` 轮——外层 2 次 × 内层 3 轮 = 最多 6 次
    # LLM 修复调用，与已确认的预算不符。提交路径显式传 `max_rounds=1`，由外层
    # 的两轮循环精确控制总预算；GAP 路径不传，保持原有的分批收敛能力。
    _rounds = max(1, int(max_rounds)) if max_rounds else _REPAIR_MAX_ROUNDS
    # v6.137（2026-09-26 第七次实跑根因）：**new_blocking 型拒绝带反馈重试一轮**。
    # 实测（run 20260926T083731）：4 个目标里只有 shot_4 的补丁引入新 blocking，
    # 按目标回退后其余 3 个目标的补丁也未通过验收（resolved=0/字段触达=0）→
    # `not _round_ok` 直接 return——修复阶段一轮即死，提交层"零改善 BLOCK"。
    # 而拒绝原因本身可反馈：肇事目标已定位（剔除不再重试）、其余目标"上一轮
    # 补丁没修掉"也该告诉 LLM。重试只做一次（_retry_used），其余拒绝原因
    # （locked_changed/non_target_changed 等）保持原停止语义。
    # v6.137：重试状态**跨调用**——提交路径每次只给 max_rounds=1，内层 continue
    # 装不下第二轮；调用方（提交层）传同一个 dict 进来：入口预载上一轮的
    # 「剔除目标+反馈」并清空（消费），出口在 new_blocking 终拒时回填新状态，
    # 由调用方决定是否用下一次 attempt 续跑（预算仍由外层 §1.1 上限管住）。
    _rejected_targets: set = set(retry_state.get("excluded") or [])         if retry_state else set()
    _retry_feedback = str(retry_state.get("feedback") or "")         if retry_state else ""
    _retry_used = bool(_retry_feedback)
    # §14 P3：**跨轮/跨调用**的布局效果冲突——上一次已发出去的冲突反馈。
    # 「相同冲突且请求信息无变化 ⇒ 停止盲修并封存残余」要能跨调用判出来：提交路径
    # 每次只给 `max_rounds=1`，同一对话的第二次请求是**下一次 attempt**，只能借
    # `retry_state` 携带。额度与截止沿用既有实现（这里**不改重试上限**）。
    _effect_conflict_prev = dict(retry_state.get("layout_conflict") or {}) \
        if retry_state else {}
    if retry_state:
        retry_state.clear()
    # v6.140（修复 3，九审根因 3）：调用方传来的**本 run 暂缓目标**（失败联动组
    # 的成员）与重试剔除集合同一过滤口径——暂缓的组不再被重发。⚠️ 合并集必须
    # **每轮重算**（`_rejected_targets` 会在轮次间被重试门更新），不能在循环外
    # 快照——2026-09-26 单测实锤：快照版让重试轮的剔除静默失效。
    _defer = set(defer_targets or ())
    for _round in range(1, _rounds + 1):
        _excluded_all = _rejected_targets | _defer   # v6.140：每轮重算（见上）
        # §14 P3：**已封存的布局效果冲突 ⇒ 不再发请求**。
        #
        # 「相同冲突且请求信息无变化 ⇒ 停止盲修并封存残余」要在**发请求之前**生效，
        # 否则「停止」只是事后描述：上一轮已经把同一条冲突反馈发出去、又原样收到同一个
        # 回答，再发第三次就是纯空转。封存只影响本判据，**不改重试上限与截止**。
        if _effect_conflict_prev.get("sealed"):
            _seal_msg = (
                "布局效果冲突已封存（reason=%s，责任 writer=%s，目标=%s）——"
                "相同冲突与请求信息在上一轮已被证明无变化，本轮不再发修复请求；"
                "残余按原样交给提交点判 BLOCK"
                % (_effect_conflict_prev.get("reason") or "?",
                   _effect_conflict_prev.get("writer") or "?",
                   _effect_conflict_prev.get("targets") or []))
            _logger.error("[REPAIR] %s" % _seal_msg)
            if block_reasons is not None:
                block_reasons.append(_seal_msg)
            if repair_diag is not None:
                repair_diag.append({"repair_round": _round,
                                    "kind": "effect_conflict_sealed",
                                    "reason": _effect_conflict_prev.get("reason") or "",
                                    "writer": _effect_conflict_prev.get("writer") or "",
                                    "targets": _effect_conflict_prev.get("targets") or [],
                                    "signature": _effect_conflict_prev.get("sig") or "",
                                    "source_revision": _effect_conflict_prev.get(
                                        "source_revision") or "",
                                    "sealed_before_send": True})
            return script
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
        # v6.137：**重试轮同样合并**——new_blocking 重试轮的遗留目标往往只有
        # audit 类告警（确定性阻塞可能已在上一轮解决），不合并则 picked 为空、
        # 重试轮空转返回。
        if (_round == 1 or _retry_feedback) and audit_issues:
            audited = per_shot_dict.setdefault("shots", {})
            for _issue in audit_issues:
                if not is_blocking(_issue):
                    continue
                per_shot_dict["_has_hard"] = True
                _sid = _issue.shot_id
                _detail = (
                    f"{MODEL_AUDIT_WARNING_PREFIX} "
                    f"code={_issue.code} frame={_issue.frame_id or '-'} "
                    f"entity={_issue.entity_id or '-'} field={_issue.field_path or '-'} "
                    f"evidence={_issue.evidence or '-'} :: {_issue.message}"
                )
                # v6.120：按 `_target_of_record` 归一（`"shot5"` ≡ `"5"`）。
                # 旧实现直接拼 `f"shot_{_sid}"`：模型第 2 轮写 `"shot5"` 时这里会挂到
                # 不存在的 `shot_shot5` 上 → 成为本轮 picked 目标 → LLM 被要求修一个
                # 不存在的镜头、真正的 shot 5 反而没进目标集（离线实跑复现）。
                if str(_sid or "").strip() == "":
                    continue          # 无定位镜头的全局问题：不挂到任何目标
                audited.setdefault(_target_of_record(_issue), []).append(_detail)
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
        # v6.107（2026-09-24 第二十九次重跑实测）：名额分配必须**先给带确定性阻塞的
        # 镜头**。
        #
        # 实测：提交点第 1 轮只有 1 条确定性阻塞（shot 9 的 `kf_event_window_mismatch`）
        # 和 4 条模型审计问题，4 个名额按「告警条数」排序时两类目标条数相同 →
        # shot 9 掉出前 4，被替换的是 2/5/8 等纯审计目标 → 那一轮**根本没有尝试**
        # 那条确定性阻塞，`resolved=0` → 停止修复 → BLOCK。
        #
        # 而验收里 `resolved` 计量的是**确定性**阻塞（`audit_resolved` 只是另一条
        # 并行判据），质量门也是按它判 BLOCK。所以排序主键用「是否带确定性阻塞」，
        # 次键才用告警条数——否则修复轮会把预算花在计量不到的目标上。
        _det_targets = _deterministic_targets(per_shot_dict.get("issues"))
        # v6.138（九审 P1-2）：上轮肇事目标在**取名额之前**过滤——剔除后空出的名额
        # 由排序中的后续目标补齐，重试轮不再空转容量。v6.140：本 run 暂缓的联动组
        # 成员同一过滤口径。
        picked = _pick_repair_targets(shot_items, _det_targets,
                                      _REPAIR_MAX_SHOTS_PER_ROUND,
                                      excluded=_excluded_all)
        if _excluded_all:
            _skipped = sorted(set(shot_items) & _excluded_all)
            if _skipped:
                _logger.warn("[REPAIR] 本轮剔除目标（肇事剔除 %s｜联动组暂缓 %s）"
                             % (sorted(set(shot_items) & _rejected_targets),
                                sorted(set(shot_items) & _defer)))
        if not picked:
            return script
        import copy as _copy_repair
        _round_before_script = _copy_repair.deepcopy(script)
        _locked_before = _locked_repair_signature(script)
        _locked_after_patch = _locked_before
        # §14 P1：本轮**效果账本**——四个冻结节点里的前两个（原候选 / 授权合并后）
        # 在这里起头。效果只属于**本轮事务**，跨轮不作事实（跨轮只留证据），
        # 所以每轮重建，不进生产分镜 schema。
        from step01_script_gen.repair_effects import (
            RepairEffectLedger, build_layout_effects, resolve_frame_actor_binding,
            classify_effect_outcome, conflict_signature,
            render_conflict_feedback, WRITER_GLOBAL_STYLE, WRITER_TIMELINE,
            WRITER_POST_FIX, WRITER_HARD_FIX, WRITER_INJURY_ANCHOR,
            WRITER_RESTORE_UNTARGETED,
        )
        from step01_script_gen.constraints.types import RepairEffectView
        from step01_script_gen.repair_fields import shot_by_target
        from shared.quality_gate import compute_script_revision as _csr_eff
        # ⚠️ 这个 revision 是「**本轮基线稿**」的，**不是**
        # `_commit_step01_outputs(source_revision=...)` 里那个「BEST-GUARD 选中快照
        # 自身」的 revision——两者在 BEST-GUARD 路径上必然不同，传错会让效果的版本
        # 身份绑到另一份稿子上（`remap_layout_effects` 的核对随之全部失效）。
        _effect_revision = _csr_eff(_round_before_script)
        _effect_ledger = RepairEffectLedger(round_id=str(_round),
                                            source_revision=_effect_revision)
        _effect_ledger.freeze_candidate(_round_before_script, _effect_revision)
        _effect_bindings: dict = {}
        _effect_view = None
        _effect_outcome: dict = {}
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
                    # v6.138（九审 P1-2）/v6.140：补选不得越过肇事剔除与本 run 暂缓集
                    if (_partner in shot_items and _partner not in picked
                            and _partner not in _excluded_all):
                        picked[_partner] = shot_items[_partner]

        # 构建单镜修复 prompt：只带被选镜头原文 + 告警
        shots_by_id = {f"shot_{s.get('index')}": s for s in script.get("shots", [])}
        if "hook" in picked and script.get("hook"):
            shots_by_id["hook"] = script["hook"]

        # ---- v6.119：成对目标**成组扩目标**（用户口径：联动目标必须作为一个事务）----
        #
        # 一条审计问题可以声明跨镜的 `repair_targets`（同一事实在 shot5 与 shot7-9
        # 的 ff_desc 里互相矛盾，只改 shot5 永远修不好）。而目标集此前只按「问题
        # **定位**在哪」挑镜头、授权构建处又按 `shot_id` 过滤 issue → 联动镜头既进
        # 不了本轮、也拿不到授权，结构上不可能修好（实测 shot 5 的跨镜睁眼问题就是
        # 这样活到最后一轮）。
        #
        # 规则：
        #   ① 组内镜头**整组**进本轮——不拆成半次修复；
        #   ② 组超单轮容量 → **整组放弃**并显式记录（半次修复会制造新的自相矛盾，
        #      正是 shot 5 被整镜回退的成因），留到最终审计按 BLOCK 处理；
        #   ③ 组内镜头与本轮已选目标或问题定位有交集才算「本轮相关」；
        #   ④ 只有**校验通过**的成对条目参与（镜头必须存在、路径必须是该镜真实叶子）。
        from step01_script_gen.repair_fields import (
            validate_repair_targets, _target_id_of, _is_structural_code,
            _is_model_audit_code,
        )
        # v6.122（用户决定 B）：分组用**联动组专用上限**（6），不是单轮上限（4）。
        _repair_groups, _pair_rejects, _oversize_groups = _linked_repair_groups(
            audit_issues, shots_by_id, picked, cap=_REPAIR_MAX_SHOTS_PER_GROUP)
        if _oversize_groups:
            # 超过联动组上限 → **立即明确 BLOCK**：不调用 LLM、不再发审计。
            #
            # 为什么不再「整组跳过 + 留给最终审计」：跳过它只意味着同一份稿的审计
            # 每轮重复声明同一组、直到轮数与审计额度耗尽——那不是显式失败，是必然
            # 失败（组能力上改不完，而拆组=半条修复、删目标=漏声明，两者都禁止）。
            # 省下这两轮 LLM 修复 + 审计调用，把结论直接交给提交点。
            for _grp in _oversize_groups:
                _reason = (
                    "联动组 %s 共 %d 镜，超过联动组上限 %d → **无法整组修复**"
                    "（拆组=半条修复、删目标=漏声明，两者都禁止；该镜数是单条问题的"
                    "声明或多条共享镜头合并后的并集）——请检查审计契约或稿件本身"
                    % (sorted(_grp), len(_grp), _REPAIR_MAX_SHOTS_PER_GROUP))
                _logger.error("[REPAIR] %s" % _reason)
                if block_reasons is not None:
                    block_reasons.append(_reason)
            return script
        _grouped_all: set = set()
        for _g in _repair_groups:
            _grouped_all |= _g
        _active_groups: list = []
        # v6.122：**按组大小降序**处理。大组（>4 镜）本轮**独占名额**，如果先处理
        # 小组、它会把容量占掉，随后大组因「可腾位不足」被跳过——那正是要避免的
        # 结构性失败（同一份审计每轮都会声明它，跳过=必然 BLOCK）。
        _exclusive_taken = False
        for _grp in sorted(_repair_groups, key=lambda g: (-len(g), sorted(g))):
            # v6.138（九审 P1-2）：扩选入口同一排除约束——组内含上轮肇事目标 →
            # 整组跳过（组是原子事务，剔除单个成员=半组修复；重发大概率复发）。
            # v6.140：本 run 已暂缓的联动组同理（失败组不重发）。该组的阻塞留待
            # 提交门按 BLOCK 处理，与其他组的修复互不拖累。
            if _grp & _excluded_all:
                _logger.warn(
                    "[REPAIR] 联动组 %s 含剔除/暂缓目标 %s → 整组跳过"
                    "（重发大概率复发；该组问题留待提交门）"
                    % (sorted(_grp), sorted(_grp & _excluded_all)))
                continue
            if len(_grp) > _REPAIR_MAX_SHOTS_PER_ROUND:
                # **大组独占本轮名额**：组外目标（含其他联动组的成员）整批让位。
                # 让位是**整组推迟**而不是拆分——其他组的成员要么全在组外、要么
                # 与本组合并（`_linked_repair_groups` 已按共享镜头合并），所以
                # 不会留下半组在稿子上。
                if _exclusive_taken:
                    _logger.warn(
                        "[REPAIR] 联动组 %s 共 %d 镜也超过单轮上限 %d → 本轮已由"
                        "另一组独占，**整组推迟到下一轮**（不拆成半次修复）"
                        % (sorted(_grp), len(_grp), _REPAIR_MAX_SHOTS_PER_ROUND)
                    )
                    continue
                _exclusive_taken = True
                _deferred = sorted(t for t in picked
                                   if t != "global" and t not in _grp)
                for _t in _deferred:
                    picked.pop(_t, None)
                _logger.info(
                    "[REPAIR] 联动组 %s 共 %d 镜 > 单轮上限 %d → **独占本轮名额**"
                    "；让出（推迟到下一轮）：%s"
                    % (sorted(_grp), len(_grp), _REPAIR_MAX_SHOTS_PER_ROUND,
                       _deferred)
                )
            elif not (_grp <= set(picked)):
                # 小组（≤ 单轮上限）：沿用 v6.119 的腾位——只让出「不在任何联动组
                # 里」的单镜目标（联动组优先，否则组永远挤不进容量）。
                # 腾位算的是**本轮实际容量**（= 单轮上限），不是组的镜数。
                _round_cap = _REPAIR_MAX_SHOTS_PER_ROUND
                _need = len(set(picked) | _grp) - _round_cap
                if _need > 0:
                    _evictable = sorted(t for t in picked
                                        if t != "global" and t not in _grouped_all)
                    if len(_evictable) < _need:
                        _logger.warn(
                            "[REPAIR] 联动组 %s 无法在本轮容量 %d 内成组"
                            "（可腾位 %d < 需 %d）→ 整组推迟到下一轮"
                            "（不拆成可能互相矛盾的半次修复）"
                            % (sorted(_grp), _round_cap, len(_evictable), _need)
                        )
                        continue
                    for _t in _evictable[:_need]:
                        picked.pop(_t, None)
                    _logger.info(
                        "[REPAIR] 为联动组 %s 让出单镜目标 %s（本轮容量 %d）"
                        % (sorted(_grp), _evictable[:_need], _round_cap)
                    )
            for _g in _grp:
                # v6.122：**组内成员一律进本轮**（自身没有告警就给空列表）。
                # 联动组的定义就是「这些镜头必须一起改」；只纳入有自身告警的那些，
                # 组在稿子上就只能改一半——其余成员的矛盾原样留着（用户口径：
                # 整组修/整组验收/整组回退，不留半组）。授权与改动依据由
                # `_issue_reaches` + 成对目标提供，不依赖 `shot_items`。
                picked.setdefault(_g, shot_items.get(_g) or [])
            _active_groups.append(_grp)
        if _active_groups:
            _logger.info("[REPAIR] 本轮联动组（整组修/整组验收/整组回退）: %s"
                         % [sorted(g) for g in _active_groups])
        if _pair_rejects:
            # 成对目标校验失败必须显式说清——它直接决定「本轮能不能修这条问题」
            _logger.warn("[REPAIR] repair_targets 校验未通过 %d 项（这些条目不参与授权）: %s"
                         % (len(_pair_rejects), _pair_rejects[:8]))

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
            if not _warn_lines:
                # v6.122：联动组成员可能**自身没有任何告警**（问题定位在锚点镜）——
                # 那时它的改动依据只在 REPAIR FEEDBACK 的 `audit_issues` 里，
                # 明确指路，避免模型面对空「问题:」无从下手。
                _warn_lines.append(
                    "  - （本镜自身无告警：它是联动目标，改动依据见 REPAIR FEEDBACK "
                    "里本镜的 audit_issues；只改 allowed_fields 列出的字段）")
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
        from step01_script_gen.repair_fields import (validate_repair_targets,
                                                     _is_structural_code,
                                                     _target_id_of)
        _inexec_issue_keys: list = []      # 只能改锁定字段的审计问题（v6.150）
        for _ai in (audit_issues or []):
            # v6.150（复核发现 3）：**按问题**过滤，而不是只按字段授权面过滤。
            # 同一镜头可能同时挂着「只能改锁定字段」与「可修」的问题——只过滤字段
            # 会让前者仍进反馈、仍被模型要求修（白耗轮次），而它最终修复不可执行。
            _ai_paths = [str(getattr(_ai, "field_path", "") or "")]
            _ai_declared = getattr(_ai, "repair_targets", None)
            if isinstance(_ai_declared, list):
                for _p in _ai_declared:
                    if isinstance(_p, dict) and _p.get("field_path"):
                        _ai_paths.append(str(_p.get("field_path")))
            _ai_exec, _ai_locked = _split_executable_allowed(_ai_paths)
            if _ai_paths and not _ai_exec:
                _inexec_issue_keys.append((str(getattr(_ai, "code", "") or ""),
                                           _ai_locked[:2]))
                continue
            _detail = {
                "code": _ai.code,
                "frame_id": _ai.frame_id,
                "entity_id": _ai.entity_id,
                "field_path": _ai.field_path,
                "evidence": _ai.evidence,
                "message": _ai.message,
            }
            _keys = {_target_of_record(_ai)}
            # v6.122：**成对目标点到的镜头也要看到这条问题的原文**。联动组成员常常
            # 自身没有任何告警（问题只定位在锚点镜），若只给它 allowed_fields 而不给
            # 问题描述，模型无从知道那一镜要改成什么——「整组入选」却修不动，
            # 结果还是半条修复。
            _declared = getattr(_ai, "repair_targets", None)
            if _declared:
                _v, _rej = validate_repair_targets(
                    _declared, shots_by_id,
                    structural=_is_structural_code(str(getattr(_ai, "code", "") or "")))
                if not _rej:
                    _keys |= {_target_id_of(_p["shot_id"]) for _p in _v}
            for _k in _keys:
                if _k:
                    _audit_by_shot.setdefault(_k, []).append(_detail)
        if _inexec_issue_keys:
            _logger.warn(
                "[REPAIR] %d 条审计问题**只能改锁定字段**——最终修复不可执行，"
                "已从本轮修复反馈中摘除（保留 blocking 交上游）：%s"
                % (len(_inexec_issue_keys), _inexec_issue_keys[:4]))
            _metrics_inc("repair.inexecutable_locked_issues", len(_inexec_issue_keys))
        # v6.83（二次审核发现 2）：**按 issue 授权**——每个 target 只允许改它自己的
        # 问题点名的字段。允许集合由 deterministic warning + audit issue 推导
        # （`repair_fields.allowed_repair_fields`）；**推不出字段路径的目标不进本轮
        # 定向修复**（那类交整镜 regen），因为没有任何依据限制它可以改什么。
        from step01_script_gen.repair_fields import allowed_repair_fields

        # v6.103：授权优先取**结构化 issue 的精确字段路径**（确定性层 + 模型审计），
        # 只有当结构化路径一条都推不出来时才回退到从告警文案解析关键词。
        #
        # 为什么必须这样：文案是给人看的——「关键帧2 描述称…」里的「关键帧」会把
        # 授权面撑到 `keyframes.*.desc`，而这条 issue 真正要改的是
        # `frame_layouts.kfs.*.on_screen` 这一个叶子。宽授权会让模型顺手动同镜的
        # 其他帧，正是总计划 §2.3「禁止顺带修改」要拦的。
        # v6.119：issue 的**可达性**判据从「问题定位在本镜」改成「成对目标含本镜」。
        # 上面那条旧判据把跨镜 issue 挡在了组内成员之外——联动镜头因此拿不到授权，
        # 结构上修不好（shot 5 跨镜睁眼问题的成因之一）。
        _all_structured: list = [
            _issue_from_record(_rec) for _rec in (per_shot_dict.get("issues") or [])
        ] + list(audit_issues or [])

        def _issue_reaches(_ai, _tid: str) -> bool:
            """该 issue 是否参与 `_tid` 的授权.

            有成对目标 → **只看成对目标里有没有这一镜**（校验通过的才算）；
            没有成对目标 → 沿用「问题定位在哪」的旧口径（旧审计产物 / 模型写空数组）。
            """
            _declared = getattr(_ai, "repair_targets", None)
            _model_audit = _is_model_audit_code(getattr(_ai, "code", ""))
            if _model_audit or _declared is not None:
                # 模型审计（成对目标是契约）或显式声明 → **只**认成对目标：
                #   缺 / `[]` / 任一无效 → 不参与任何镜头的授权（fail-closed，
                #   不退回「问题定位在哪」的旧口径——那是静默降级）；
                #   全部有效 → 只给成对目标点到的镜头。
                if not _declared:
                    return False
                _valid, _rejected = validate_repair_targets(
                    _declared, shots_by_id,
                    structural=_is_structural_code(getattr(_ai, "code", "")))
                if _rejected:
                    return False          # 整条拒绝，不留半组
                return any(_target_id_of(_p["shot_id"]) == _tid for _p in _valid)
            return _target_of_record(_ai) == _tid

        _allowed_by_target: dict = {}
        _unauthorized_targets: list = []
        _inexecutable_locked: dict = {}     # tid → 命中的锁定字段（v6.149 预检）
        for _tid, _ws in picked.items():
            if _tid == "global":
                continue
            _structured = [_ai for _ai in _all_structured if _issue_reaches(_ai, _tid)]
            _allowed = (allowed_repair_fields(
                            [], _structured, target_shot_id=_tid,
                            shots_by_id=shots_by_id)
                        or allowed_repair_fields(
                            _ws, [], target_shot_id=_tid, shots_by_id=shots_by_id))
            if not _allowed:
                _unauthorized_targets.append(_tid)
                continue
            # v6.149 可执行性预检：摘掉轮级锁定字段。逐**目标**判断——同一目标若还有
            # 别的可执行字段，照常进修复（不按 code 一刀切）。
            _exec, _locked_hit = _split_executable_allowed(_allowed)
            if not _exec:
                _inexecutable_locked[_tid] = _locked_hit or [str(a) for a in _allowed]
                continue
            _allowed_by_target[_tid] = _exec
        if _unauthorized_targets:
            _logger.warn(
                "[REPAIR] %d 个目标的问题未点名字段路径，本轮不走定向修复：%s"
                "——本实现**没有**自动整镜 regen 路径，这些目标会保留到最终审计，"
                "仍不通过就由提交点 BLOCK（不放开授权去硬修）"
                % (len(_unauthorized_targets), _unauthorized_targets[:6])
            )
        if _inexecutable_locked:
            _logger.warn(
                "[REPAIR] %d 个目标的修复**只能改锁定字段**——最终修复不可执行，"
                "保留 blocking 交上游阶段（本轮不为它们发起修复，也不放开授权硬修）：%s"
                % (len(_inexecutable_locked),
                   dict(list(_inexecutable_locked.items())[:4])))
            _metrics_inc("repair.inexecutable_locked_targets",
                         len(_inexecutable_locked))
        if not _allowed_by_target:
            _logger.warn("[REPAIR] 无任何目标具备明确字段授权，跳过本轮定向修复")
            return script
        # v6.155（2026-10-04 实测根因，台账 §19.2）：入轮目标里若**没有任何一个**目标
        # 能改到本轮 blocking 点名的字段，这一轮**注定零产出**——章 2 实测：5 条 blocking
        # 的 `repair_targets` 全空 ⇒ 零授权，修复轮照样发起了一次调用，模型按审计建议改了
        # `frame_layouts.*`，全部被判「未授权」回填 ⇒ 字段触达=0 ⇒ `no_target_resolved`
        # ⇒ 整轮回退 ⇒ BLOCK。白烧一次调用，日志还看起来像「修了但没修好」。
        # 这里在**发起前**判死并如实记原因（与 `_unauthorized_targets` 同一口径：不放开
        # 授权去硬修，只是不再为一个不可能成功的轮次付代价）。
        _blocking_issues = [_ai for _ai in (audit_issues or []) if is_blocking(_ai)]
        if _blocking_issues and not _repair_blocking_coverage(
                _blocking_issues, _det_targets, _allowed_by_target):
            _logger.warn(
                "[REPAIR] %d 条 blocking 的授权面与本轮入轮目标的授权面**没有交集**"
                "——本轮任何修复都不可能让它们中的任何一条通过验收，跳过本轮定向修复"
                "（保留 blocking 交提交点 BLOCK，不放开授权去硬修）"
                % len(_blocking_issues))
            _metrics_inc("repair.round_skipped_no_blocking_coverage",
                         len(_blocking_issues))
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
        # §14 P3：**冲突 issue 进下一修复请求**的承载层——`_feedback_json["conflicts"]`
        # 与下面的 `_effect_conflict_block` 同源（同一份 `render_conflict_feedback`）。
        # 内含四项：source_revision、目标、原文/绑定证据、请求→应用→撤销阶段、允许修改范围。
        if _effect_conflict_prev.get("text"):
            _feedback_json["conflicts"] = {
                "attribution": _effect_conflict_prev.get("reason", ""),
                "writer": _effect_conflict_prev.get("writer", ""),
                "source_revision": _effect_conflict_prev.get("source_revision", ""),
                "targets": _effect_conflict_prev.get("targets") or [],
                "text": _effect_conflict_prev["text"],
            }
        _effect_conflict_block = str(_effect_conflict_prev.get("text") or "")

        # 08-21 mode 合并：单一 unified prompt 替代原 A/B/C 三套
        # 含：全片错误分布（_input_turn）+ top-3 hint + 4 类约束 + 决策树 + 3 组正反例
        # + 同角色跨镜一致性 + 5 条修复要点 + 同镜内 anchor 保留
        _repair_turn = (
            "## REPAIR FEEDBACK（结构化 JSON，按 shot 分组）\n"
            "```json\n"
            f"{_json.dumps(_feedback_json, ensure_ascii=False, indent=1)}\n"
            "```\n\n"
            + _top3_hint + _retry_feedback + _effect_conflict_block +
            "## TASK（精简修复）\n"
            # v6.120（用户决定）：原文案要求「一条 warn 只动一处」，与成对授权
            # 直接冲突：一条 kf 时序矛盾的正解往往是 `kf.desc + kf.stage +
            # 布局条目` 三处一起改，只改一处的「半条修复」自相矛盾、引入新阻塞、
            # 整组被回退（2026-09-25 实测 shot 5 正是这个形态）。
            "请**逐条**修复下方列出的问题；每条问题只改**解决它所需的已授权字段**——"
            "**需要联动时一次改全**（例如 kf 时序矛盾牵涉 kf 的 desc / stage /"
            "对应布局条目，就一起改；审计已把这些字段都列为授权目标）。\n"
            "优先只返回被点名镜头的窄字段补丁 `updates`（路径和值），无需重抄未改字段。格式："
            f"{_repair_answer_example(shots_by_id, picked, _allowed_by_target)}\n"
            "上面是本次授权字段与当前值的 JSON 示例；只提交需要修改的授权路径。`updates` 可为路径到值的字典，或 field_path/value 列表；未提交的字段由代码保留。完整 shot 替换和 keyframe_additions 仍兼容，同一目标只选一种形态。\n"
            "**hook 必须放在 `hook_replacement` 里**（它与普通镜头字段不同，无 scene/location），"
            "普通镜头放 `shot_replacements`；两个键都可为空数组/省略。\n"
            # 2026-09-27（用户决定）：缺帧这类**联动**修复改「成对数据」形状——
            # 模型给帧 + 对应布局，**代码**按时间顺序装配两半。实测返回完整镜头时
            # 模型会把 keyframes 加到 4 而 frame_layouts.kfs 留在 3，长度校验整条拒收。
            "**联动修复（问题里出现「缺帧 / 没有按时间归属它的关键帧」时）用成对形状**："
            "{\"shot_replacements\":[{\"shot_id\":\"3\",\"keyframe_additions\":"
            "[{\"frame\":{\"desc\":\"…描写该时段动作…\",\"t_sec\":0.5,"
            "\"stage\":\"pre\"},\"layout\":[{\"name\":\"…\",\"pose\":\"…\","
            "\"on_screen\":true}]}]}]}——`frame` 与 `layout` **必须成对给出**，"
            "代码按 `frame.t_sec` 的时间顺序把两半装进**同一声序号**"
            "（两表长度由装配保证一致），缺任一半整条拒收。"
            "**代码不会替你猜姿态/持物**：`layout` 里的角色姿态、持物、on_screen "
            "必须由你写全。\n"
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
            "kf.t_sec 落在 (0, duration] 内；stage 取 pre/approach/climax/react/exit/after。\n"
            "**若 `allowed_fields` 里出现 `keyframes`**（只出现在「kf 时段缺段」这类"
            "**必须增删帧**才能修的问题上）：允许**增删帧**，但必须同时满足——"
            "① `frame_layouts.kfs` 同步增删，两者**等长且序号对应**；"
            "② 每帧 t_sec 落在 (0, duration] 且**按时间升序**排列；"
            "③ 新增帧的 desc/stage 对应 `motion_desc` 的**未覆盖时段**"
            "（time_ratio = 段中点 / duration_sec）；"
            "④ **`motion_desc` 的原有事件文本逐字保留、顺序不变**——"
            "只允许追加过程描述，禁止删事件、改顺序、合并时段"
            "（那是让告警消失而不是修好，会被确定性闸门整轮回退）；"
            "⑤ **默认不动已有帧**（只在列表里**追加**新帧）：把已有帧挪到别的"
            "时刻却**不改它的文案**，会让内容落进另一个时段，立刻触发新的"
            "`kf_event_window_mismatch`（实测：补 1 帧换来 1 条新阻塞，整轮被回退）。"
            "**例外（问题文案明确要求时）**：当问题写「已有帧与相邻段**时间/内容"
            "错位**」时，允许**一并调整**该帧的 `t_sec` / `desc` 与**对应序号**的"
            "`frame_layouts.kfs` 条目——但改 `t_sec` 必须与改文案**成对**做；"
            "只搬时刻不改文案等于把缺陷挪到相邻段（实测：1-2s 的「反手一掌」"
            "被画在 2.5s、文案写「掌风扫向」）。**验收口径：每个需帧时段都有一帧、"
            "且该帧文案描写的是它自己那段。**\n\n"
            "**手术刀原则（最高优先级）**：\n"
            "1. 一条 warn 只改**它授权范围内的字段值**；镜头内其余字段**逐字保留**——"
            "优先输出 updates 窄补丁；兼容完整镜头时也不得重写未授权字段。"
            # v6.116（审核）：**例外**——同镜多个关联问题必须一次改到自洽，
            # 否则「一条 warn 只改一处」会让 motion/KF/尾帧三处互相矛盾。
            # v6.120（用户决定）：例外**不再局限于「同镜多个问题」**——一条问题
            # 本身就可能需要多字段联动（kf.desc + kf.stage + 布局条目）。
            "**例外（联动必须一次改全）**：一条问题需要多个字段才能改到自洽时"
            "（例如 kf 时序矛盾要同时改该帧的 desc / stage / 所属布局条目），"
            "**必须一次改全**——漏改的部分会被回填，留下自相矛盾的半条修复；"
            "同镜多个关联问题同理（补了过程动作就同步补对应 KF 与尾帧承接），"
            "不得机械地一处一处改而互相矛盾；\n"
            "2. 禁止**无关**新增（动作/情绪/运镜/光位/转场/视角/装饰性方位）；"
            # v6.116（审核）：原句「禁止新增任何短语」与正解**自相矛盾**——被点名的问题
            # 常常正是「motion_desc 缺少跃船动作」，正解就是**补动作**。改为只禁无关新增。
            "**为修复被点名的问题，可以在授权字段补必要动作**——"
            "「motion_desc 缺少跃船过程」的正解就是补上该动作，不得因为「禁止新增」而回避；"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但**禁止写成「望着她」「看杨旭」这类无空间方位词**\n"
            "3. 禁止未经授权改景别/时长；台词/剧情/narration_segment 文本保持原样\n"
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
            "- 报「kf 时序矛盾」→ 改 kf.desc（不要改 prompt），按 pre-stab/stab/post-stab 递进写；"
            "若该帧的 `stage` / 所属布局条目（`frame_layouts.kfs[i][n].props` 等）与该矛盾"
            "直接相关（如道具在丢后又出现），**一并改**——这些字段审计已列为授权目标，"
            "只改 desc 会留下半条修复\n"
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
            "以下是分镜问题清单。请**逐条**修复；每条问题只改**解决它所需的已授权字段**——"
            "**需要联动时一次改全**（如 kf 时序矛盾牵涉 kf 的 desc / stage / 布局条目）。"
            "优先返回被点名镜头的窄字段补丁 `updates`（路径和值）。格式："
            f"{_repair_answer_example(shots_by_id, picked, _allowed_by_target)}\n"
            "上面是本次授权字段与当前值的 JSON 示例；只提交需要修改的授权路径。`updates` 可为路径到值的字典，或 field_path/value 列表；未提交的字段由代码保留。完整 shot 替换和 keyframe_additions 仍兼容，同一目标只选一种形态。\n"
            "hook 放 hook_replacement，普通镜头放 shot_replacements，两者都可为空；"
            "shot_id 用裸镜号（\"10\"，不是 \"shot_10\"）；"
            "每个被点名镜头恰好出现一次（重复/遗漏/多出则整批回滚）。"
            "未点名镜头绝不出现；镜头内**未被 warn 授权的字段逐字保留**"
            "（见 REPAIR FEEDBACK 的 allowed_fields）。"
            f"{_global_block}"
            + _top3_hint +
            "**手术刀原则（最高优先级）**：一条 warn 只改它授权范围内、解决该问题所需的字段"
            "（需要联动时**一次改全**）——不要批量重写整镜；"
            "禁止**无关**新增（动作/情绪/运镜/光位/转场/视角/装饰性方位）——"
            "但为修复被点名的问题**可以补必要动作**（如「motion_desc 缺跃船过程」就补该动作）；"
            "同镜多个关联问题要一次性让 motion_desc/KF/尾帧自洽，"
            "**视线方位（画左/画右/画上方/目光/凝望/仰望/俯视）** 是规则 22.5a 硬性要求——"
            "禁止砍/省略，但禁止写成无空间方位词；"
            "禁止未经授权改景别/时长；台词/剧情/narration_segment 文本保持原样；禁止增删镜头、改 shot.index；"
            "**lf_desc 至少 40 字**，含本镜所有出镜角色完整静态布局——过短会产生内部矛盾。\n"
            "**关键字段保留白名单（最高优先级）**：致命伤 anchor（心口/洇痕/暗红/中刀/中剑/伤口/血痕 等）"
            "+ 服装色（每个出镜角色，取自角色库）+ 角色当前状态（坐/站/仰卧/跪坐/持物 等）"
            "+ 锚点连贯子句（前镜 lf_desc 已锚定的姿态/位置/朝向，跨镜承接必须保留）"
            "+ color_tone（场景色调基底词）——即使不在 warn 列表中也**禁止删/改/移位**"
            "（C 模式实验教训：改服装色 warn 连带删掉致命伤 anchor）。\n"
            "**Mask 字段清单（本轮禁止修改）**：narration_segment / index / duration_sec /"
            " subtitle / variation_type / scene / location——结构性字段，改则破坏排序/时长/原文锚定。\n"
            "**若 `allowed_fields` 含 `keyframes`**（「kf 时段缺段」这类问题）："
            "允许增删帧，但 `frame_layouts.kfs` 必须同步增删（等长、序号对应）、"
            "t_sec 落在 (0, duration] 且按时间升序、新帧对应 motion_desc 未覆盖时段；"
            "**motion_desc 原有事件文本逐字保留、顺序不变**（只可追加，禁删改合并）；"
            "**已有帧默认逐字保留**，只在列表追加新帧（只挪时刻不改文案会让它落进"
            "别的时段 → 立刻多出一条窗口错配）；**仅当问题文案说「时间/内容错位」时**，"
            "才可同时改该帧的 t_sec + desc + 对应布局条目（改时刻必须与改文案成对）；"
            "缺帧优先用 `keyframe_additions` 成对形状（frame + layout 一起给，代码装配）。\n"
            "**同镜内 warn 改字段时必须保留同字段 anchor（最高优先级）**：改 ff_desc/lf_desc/prompt"
            " 的某条 warn 时，**禁止**顺手删该字段已有的 anchor 子句（致命伤/服装色/角色状态）。\n"
            "**修复决策树**（精简版）：缺服装色→追加；视线无方位→加画X/目光；致命伤缺→补洇痕；"
            "kf矛盾→改kf.desc（连带 stage/布局条目一起改）；景别跳→改中间级；光位模糊→具体光位。\n"
            "**3 组正反例（按例做）**：致命伤保护=追加5字不删anchor；视线方位=加方位词不砍原意；"
            "lf_desc完整性=追加不砍到<40字（防「已没入水下」+「举刀正对」矛盾）。\n"
            "**同角色跨镜一致性**：服装色/状态/kf顺序跨镜一致（杨旭素白轻袍不中途换色，致命伤持续到所有伤后镜）。"
            + _retry_feedback
            + "\n".join(parts)
        )

        _metrics_inc("calls.repair_round")
        # v6.133：上限只在这里取一次——既发给请求，也随调用记录落台账（余量可算）
        _repair_cap = _repair_max_output_tokens()
        # v6.133（2026-09-26 用户复核）：`create_service()` 原先在 try **之外**——
        # 工厂因配置/依赖问题抛异常时既不会被记成 `call_failed`，也进不了提交层归因
        # （只会以未捕获异常收场）。现在把初始化也纳入同一条失败记录路径
        # （`_repair_cap` 因此必须先取，否则记录里带不出本轮上限）。
        # 定向修复的**通道覆盖**（2026-09-28 实测后新增，语义与审计同源）：
        # agent_plan 通道把 `json_schema` 降级成 json_object → 修复的形状契约失效 →
        # `deepseek-v4.1-flash` **整镜原样回抄**（6/6 次 resolved=0、audit_field_touched=0），
        # 修复轮等于不可用；原生 deepseek 通道保留 strict json_schema。
        # 放在 try **之外**：配置写错（设了 provider 没给 model）必须**显式失败**，
        # 不能被下面「服务初始化失败 → 保留原版」吞成静默降级。
        _rep_channel_provider, _rep_channel_model = resolve_repair_channel()
        try:
            service = (create_service() if _rep_channel_provider is None
                       else create_service(_rep_channel_provider))
        except Exception as e:
            _logger.warn(f"[WARN] 定向修复服务初始化失败（保留原版，未做任何后处理）: {e}")
            _record_repair_call_failure(repair_diag, _round,
                                        error="服务初始化失败：%s" % e,
                                        max_tokens=_repair_cap)
            return script
        from _text_gen.deepseek_schema import REPAIR_SHOT_REPLACEMENT_SCHEMA
        req = TextGenerationRequest(
            system_prompt=_rep_system,
            user_prompt=prompt,
            user_inputs=_user_inputs,  # v6.37 结构化多轮
            # v6.101：8000 → 16000（每批 4 个完整镜头对象 + 上下文）。
            # v6.133（用户 2026-09-25 实跑 BLOCK）：**不再硬编码 16000**——该上限含
            # 思维链 token，实测同一请求一次截断一次成功（见 `_repair_max_output_tokens`），
            # 而 .env.content 对修复轮的推荐值本就是 50000。默认不传 = 跟随通道全局值。
            max_tokens=_repair_cap,
            temperature=0.1,
            json_schema=REPAIR_SHOT_REPLACEMENT_SCHEMA,
            # 通道覆盖优先（2026-09-28）：设了修复专用通道就用它的模型；
            # 否则沿用调用方模型（v6.67 接力修复语义不变）。
            model=_rep_channel_model or model,
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
            _record_repair_call_failure(repair_diag, _round, error=str(e),
                                        max_tokens=_repair_cap)
            return script
        if not result.success:
            _logger.warn(f"[WARN] 定向修复失败（保留原版，未做任何后处理）: {result.error}")
            _record_repair_call_failure(
                repair_diag, _round, error=str(result.error),
                truncated=bool(getattr(result, "truncated", False)),
                usage=getattr(result, "usage", None), max_tokens=_repair_cap)
            return script
        # v6.133：**成功调用也记 usage**——判断 `REPAIR_MAX_OUTPUT_TOKENS` 该设多大，
        # 靠的是"实际花了多少输出 token（含思维链）"，而不是"这次有没有踩线"。
        # 2026-09-25 实测：同一请求同一上限，一次截断一次成功 ⇒ 余量已在临界。
        _record_repair_call_usage(repair_diag, _round,
                                  usage=getattr(result, "usage", None),
                                  max_tokens=_repair_cap)

        import re as _re, json as _patch_json
        raw = result.text.strip()
        raw = _re.sub(r"^```json\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"^```\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"```\s*$", "", raw, flags=_re.MULTILINE)
        try:
            _patch_data = _patch_json.loads(raw)
        except Exception as e:
            _logger.warn(f"[WARN] 定向修复补丁 JSON 解析失败（保留原版，未做任何后处理）: {e}")
            _record_repair_unusable(repair_diag, _round,
                                    reason="parse_failed", detail=str(e),
                                    raw_head=result.text)
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
            _record_repair_unusable(repair_diag, _round, reason="invalid_structure",
                                    detail="shot_replacements 不是数组：%s"
                                           % type(_repls).__name__,
                                    raw_head=result.text)
            return script
        if not _repls and not isinstance(_hook_repl, dict):
            _logger.warn("[WARN] 定向修复未返回任何替换镜头"
                         "（shot_replacements 与 hook_replacement 皆空），保留原版"
                         "（未做任何后处理）")
            _record_repair_unusable(repair_diag, _round, reason="empty_patch",
                                    detail="shot_replacements 与 hook_replacement 皆空",
                                    raw_head=result.text)
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
            _record_repair_unusable(repair_diag, _round, reason="rejected_by_validation",
                                    detail="；".join(str(x) for x in _rejected[:3]))
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
        # v6.104.1（2026-09-24 第二十四次重跑实测）：KF 结构验收的基线必须与
        # `_mask_violations_after_patch` **同源**——「合并后、后处理前」，只含
        # LLM 的写入。
        #
        # 原实现把它算在**后处理之后**的 `script` 上，于是 `run_hard_fix` 对原稿
        # 既有的重复残片做清洗（实测 shot 8 的 motion 里「语气眼神眼神眼神眼神眼神
        # 冰冷」被清成正常文本）时，判据拿**带残片的原文**去比 → 报「原事件文本
        # 丢失」→ 本轮 3 个已提交的替换被整轮回退，`resolved=0`。这与 v6.87/v6.95
        # 修的是同一类缺陷：闸门只管 LLM 的写入，管线自己的确定性清洗不算越权。
        _kf_struct_after_patch = _kf_structural_violations(
            _round_before_script, merged, _allowed_by_target, set(picked),
            novel_text)
        # v6.154（复核之二十 P1）：**帧子列表**被逐字点名时只允许纯增删条目
        # （保留条目逐字不变、相对顺序不变、非点名帧不动）。与 kf 闸门同时算、
        # 同源基线（都只看 LLM 的写入，不看管线自己的确定性清洗）。
        _layout_struct_after_patch = frame_layout_structural_violations(
            _round_before_script, merged, _allowed_by_target, set(picked))
        # §14 P1 节点②：**授权合并后**就是效果的基线——它只含 LLM 的写入。
        # 只登记「被逐字点名的帧子列表」上、且已过上述结构守卫的增删；
        # 之后每个后处理 writer 跑完再冻一次，看这条已授权效果有没有被抵消。
        try:
            _effect_records = build_layout_effects(
                _round_before_script, merged, _allowed_by_target,
                _effect_revision, round_id=str(_round))
        except Exception as _eff_err:
            _effect_records = []
            _logger.warn("[REPAIR] 布局效果登记失败（本轮按无效果处理）: %s" % _eff_err)
        _effect_ledger.freeze_merged(merged, _effect_records)
        if _effect_records:
            _logger.info(
                "[REPAIR] 本轮已授权布局效果 %d 条: %s"
                % (len(_effect_records),
                   [(e["shot_id"], e["frame_locator"],
                     "删" + str([x.get("name") for x in e["removed_entries"]]),
                     "加" + str([x.get("name") for x in e["added_entries"]]))
                    for e in _effect_records[:4]]))
            # 人物绑定裁决（只读）：条目是类别别名时，它与已在场人物的身份关系
            # 决定后面的归因走哪一类。关系取**稿件**的人物表与 `chapter_outline`，
            # 不从名字或句式猜（缺关系就是缺，交回生产者）。
            for _eff in _effect_records:
                _names = [str((x or {}).get("name") or "")
                          for x in (_eff.get("removed_entries") or [])
                          if isinstance(x, dict)]
                _names += [str((x or {}).get("name") or "")
                           for x in (_eff.get("added_entries") or [])
                           if isinstance(x, dict)]
                # **逐条目**绑定（2026-10-03 复核 P1-9）：只挑一个条目判关系，会让
                # 该帧所有删除条目一起获得"不必重建"的许可。这里对每个条目各解析一次
                # （解析器自己会核该帧的在场事实），键为「目标＋帧＋条目」。
                for _name in sorted({n for n in _names if n}):
                    try:
                        _binding = resolve_frame_actor_binding(
                            shot_by_target(_round_before_script, _eff["shot_id"]),
                            _eff["frame_locator"],
                            _round_before_script.get("characters"),
                            _round_before_script.get("chapter_outline"),
                            novel_text, entry_name=_name,
                            # §14 P2 窄协议：生产者（审计）显式声明的身份关系优先于
                            # 一切推断——「小厮 ≡ 张十三」只有它给得出来。没给 ⇒ 返回
                            # ambiguous，不猜（这正是 `repair_capability_missing` 的来源）。
                            declared_relations=audit_relations or None)
                        _effect_bindings[RepairEffectView.binding_key(
                            _eff["shot_id"], _eff["frame_locator"], _name)] = _binding
                    except Exception as _bind_err:
                        _logger.warn("[REPAIR] 人物绑定解析失败（按未解析处理）: %s"
                                     % _bind_err)
            # 只读视图：供**同一事务内**的约束按帧消费（本轮已授权删除的条目不得
            # 按类别别名重建）。没有它时约束行为逐字不变。
            _effect_view = RepairEffectView(
                round_id=str(_round), source_revision=_effect_revision,
                effects=tuple(_effect_records),
                bindings={k: v.as_record() for k, v in _effect_bindings.items()})
        script = merged
        # v6.95（①，2026-09-22 实测）：**锁定字段基线必须在合并后立刻取**。
        #
        # 原实现在**尾部**（归一化与还原之后）才赋值 `_locked_after_patch`，于是它
        # 比的是「本轮前 vs 全流程之后」，把**管线自己的确定性清洗**算成 LLM 越权——
        # 实测第十次：拆分子镜台词末尾的「，」被归一化清掉 → `locked_changed` → 整轮
        # 假拒绝（`new_blocking=0`、`非目标镜头被改动`=0、`resolved=5` 全被这一个闸门否掉）。
        # 这与 v6.87 修 mask 基线是**同一原则**：闸门只管 **LLM 的**写入。
        _locked_after_patch = _locked_repair_signature(script)
        # v6.143（P4）：**合并后立刻**取字段级差异，供两处消费——① 被拒时的日志
        # （原来只在 `locked_changed` 分支里现算）；② `_locate_offending_targets`
        # 的兜底定位源（锁定字段**已授权**时 `_final_field_mask_violations` 会跳过它，
        # 但轮级签名照样变了，此时只能靠这份 diff 定位到镜）。
        _locked_diff_round: list = []
        try:
            _locked_diff_round = _locked_signature_diff(_locked_before, _locked_after_patch)
        except Exception:
            _locked_diff_round = []
        # 修复后需重新算时间轴/注入全局渲染参数
        # 08-15 修复：正确函数在 style_prefix（_post_process:186 同款），原 import 自
        # shared.vimax_bridge 不存在 → 修复轮合并后 ImportError 崩溃，修复结果从未落地
        merged = style_prefix.inject_global_config_into_script(merged)
        # §14 P1 节点③之一：全局渲染参数注入**也是后处理 writer**（它改写 prompt 等
        # 字段）。只挂 `on_constraint_done` 覆盖不到它——少挂一个，抵消就会被归到
        # 后一个 writer 头上，责任 writer 指错比不指更坏。
        _effect_ledger.observe_writer(WRITER_GLOBAL_STYLE, merged)
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
        _effect_ledger.observe_writer(WRITER_TIMELINE, merged)
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
        _effect_ledger.observe_writer(WRITER_POST_FIX, script)
        # 08-19：C 模式默认后，修复轮 LLM 可能删掉致命伤锚定（A→C 实测 2 个镜头"
        # 心口暗红洇痕"被删，违反规则 8 最高优先级）。修复完成后跑 hard_fix 致命伤
        # 兜底 + kf 时段对齐，把 LLM 弄丢的确定性补回来（幂等，不伤已有正确内容）。
        # v6.42：改用新 Constraint 框架；HARD_FIX_USE_OLD 回退开关已删除（2026-09-12）
        #
        # §14 P1：本调用点此前**没传** `on_constraint_done`——于是约束层对帧布局的
        # 每一次 append 都无处观测，第 21 轮 ch2 的抵消只能靠事后定位才看得出来。
        # 现在两个回调同时挂上：`on_constraint_done` 给**每个约束**一个冻结点（抵消的
        # 责任粒度止于那一个约束），`repair_effects` 把本轮的只读效果视图交给约束，
        # 让「按类别别名重建一个已被授权删掉的条目」这件事在**写入侧**就被拦下。
        from step01_script_gen.constraints import run_hard_fix as _hfix
        try:
            script = _hfix(script, is_in_repair_loop=True, is_final_pass=False,
                           novel_text=novel_text,
                           on_constraint_done=_effect_ledger.observe_writer,
                           repair_effects=_effect_view)
        except Exception as _hfx:
            # 同上：环境故障不降级（否则约束链没跑完仍继续往下走）
            from step01_script_gen.constraints.engine import ENV_FAULT_TYPES
            if isinstance(_hfx, ENV_FAULT_TYPES):
                raise
            _logger.warn(f"[WARN] 修复后 hard_fix 兜底失败（保留原版）: {_hfx}")
        _effect_ledger.observe_writer(WRITER_HARD_FIX, script)
        # 08-19：hard_fix_injury_continuity 按 location 精确匹配 + victim 提取不准，
        # 会漏判"仰卧但 location 写岸边"这类情况。此处保留**识别**，但不再补写正文
        # （2026-10 复核）。原实现在这里把「。伤处白袍暗红洇痕持续可见」直接拼进 prompt，
        # 三个问题：
        #   ① 它**造出模型没写过的状态**——"白袍"是硬编码服装，与真实人物着装无关，
        #      正是"血迹所属人物 / 伤处"混淆的污染源之一；
        #   ② 它与 `prompt_semantic.registry.INJURY_ANCHOR_TAIL`（"伤处白袍心口一片
        #      暗红血迹洇开…"）是**两个不同字符串**，两套幂等判据互相看不见，会与约束
        #      侧叠加到同一镜头；
        #   ③ 它用 `if "杨旭" not in _text` 把规则绑死在本作主角上——人名不得当通用判据。
        # 现行口径与约束侧一致：**只报告候选**，由终审判断是否真的缺伤处描述。
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
        _inj_candidates = []
        if _build_idx is not None:
            for _i, _s in enumerate(script.get("shots", [])):
                if _i <= _build_idx:
                    continue  # 致命伤建立点之前不判（夜宴仰卧喝酒不算伤）
                _text = _s.get("prompt", "") + _s.get("ff_desc", "") + _s.get("lf_desc", "")
                if not any(w in _text for w in _injury_lay_words):
                    continue  # 本镜没人卧倒/倒地
                if any(w in _text for w in _anchor_words):
                    continue  # 已有伤处锚，无需提示
                _inj_candidates.append(_s.get("index", _i))
        if _inj_candidates:
            _logger.warn(
                "[REPAIR] 致命伤后疑似缺伤处描述（不自动补写，交终审判定）: 镜 %s"
                % _inj_candidates)
        # §14 P1 节点③之三：原先此处**直写** `shot["prompt"]`，是后处理 writer。
        # 现不再写入，但仍给 ledger 留一个观测点，避免阶段屏障漏记该节点的状态快照。
        _effect_ledger.observe_writer(WRITER_INJURY_ANCHOR, script)


        # v6.84：还原未点名镜头（`_frozen` 快照）——后处理不得改动事务外的镜头。
        _restored_n = _restore_untargeted(script, _frozen)
        if _restored_n:
            _logger.info(f"[REPAIR] 事务内后处理已限定范围：还原 {_restored_n} 个未点名镜头")
        # §14 P1 节点③之四：还原未点名镜头同样是后处理 writer（它把事务外的镜头
        # 写回基线）。至此四个节点的中间三个全部落地，且**每个 writer 各自一个节点**。
        _effect_ledger.observe_writer(WRITER_RESTORE_UNTARGETED, script)
        # §14 P1 节点④：**最终重验后**——下面取的就是本轮接受判据所依据的那份稿子。
        _effect_ledger.freeze_reverify(script, _effect_revision)

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
                if _target_of_record(_ai) in picked
            ]
            # v6.103（总计划阶段 3）：跨字段 issue 的判据证据不得被删掉。
            # 必须用**后处理结束后的最终 script** 来查——后处理层可能把它补回来
            # （幂等约束会重建布局条目），那就不是「删证据」而是「还没修完」。
            _evidence_removed = _cross_field_evidence_removed(
                _det_before_state, script, set(picked))
            # v6.104：结构验收在**合并后立刻**算好（见循环内 `_kf_struct_after_patch`）
            # ——基线只含 LLM 的写入，与 `_mask_violations_after_patch` 同源。
            _kf_struct = _kf_struct_after_patch
            _layout_struct = _layout_struct_after_patch
            _decision = _repair_acceptance_decision(
                _det_before_state, _after_state, _allowed_targets, script, _locked_changed,
                non_target_changed=_non_target,
                locked_by_llm_changed=_locked_by_llm,
                script_before=_round_before_script,
                evidence_removed=_evidence_removed,
                kf_structural_violations=_kf_struct,
                layout_structural_violations=_layout_struct,
                audit_attempted=audit_attempted)
            _logger.info(
                "[REPAIR] 接受条件: "
                f"before={_decision['before']}+audit{_decision['audit_targets']} "
                f"after={_decision['after']} resolved={_decision['resolved']} "
                f"字段触达={_decision.get('audit_field_touched', 0)} "
                f"new={_decision['new_blocking']}"
                # 09-26 P0-2：把兜底记账的**问题身份**一并打出来。只报计数时
                # 「同一身份第二轮不再给兜底分」（v6.139 修复 2）在真 run 里
                # **无法核验**——本轮 3 次修复轮都是 `字段触达=N` 而无身份，
                # 只能靠单测证明该判据存在。
                + (" 触达身份=" + str(_decision.get("audit_touched_ids"))
                   if _decision.get("audit_touched_ids") else "")
            )
            _metrics_inc("repair.decisions")
            _metrics_update("repair.last_decision", _decision)
            # v6.139：本轮兜底记账的问题身份先暂存——**轮次最终落定**（未被零改善/
            # 回退路径撤销）后才并入「已尝试」集合；被回退轮次的触达没进稿子，
            # 不消耗该身份的一次兜底记账名额。
            _touched_ids_round = list(_decision.get("audit_touched_ids") or [])
            # §14 P3：**效果被后处理抵消 ≠ 模型没修好**。
            #
            # 第 21 轮 ch2 的完整因果链：审计说「小厮 是重复实体」→ 模型删对了
            # （授权与合并都通过）→ 同轮的 `group_actor_continuity` 按「文本提到群体」
            # 加回来 → 验收看到与基线相同 ⇒ `字段触达=0` ⇒ `no_target_resolved`
            # ⇒ 整轮回退 ⇒ BLOCK。**没有一步是模型没改**，却被记成模型的问题。
            #
            # 这里只改**归因**，不改采纳判据：`_round_ok` 仍按原样为假、基线照样
            # 保留（§P0：身份关系矛盾或未解决时保留原采纳稿）。四类归因
            # （`postprocess_effect_reverted` / `actor_presence_conflict` /
            # `actor_binding_ambiguous` / `repair_capability_missing`）落点：
            #   · `_decision["reason"]`——本轮 reason 的自由串（消费者：诊断、日志、
            #     `_locate_offending_targets` 之外的所有 reason 判读）；
            #   · `_decision["effect_conflict"]`——完整事实（mechanism/writer/
            #     first_revert/bindings），随 `repair_diag` 落台账；
            #   · `_feedback_json["conflicts"]` + `_repair_turn`——下一轮**修复请求**
            #     的承载层（§P3「冲突issue须进入下一修复请求」）。
            _effect_outcome = {}
            try:
                _effect_outcome = classify_effect_outcome(
                    _effect_ledger, script,
                    bindings=dict(_effect_bindings))
            except Exception as _eff_cls_err:
                _logger.warn("[REPAIR] 布局效果归因失败（沿用既有 reason）: %s"
                             % _eff_cls_err)
            if _effect_outcome.get("reason"):
                _decision["effect_conflict"] = _effect_outcome
                if str(_decision.get("reason") or "") == "no_target_resolved":
                    # 只在**归因指向模型之外**时改写：`no_target_resolved` 的原意是
                    # 「模型没修」，而账本已经证明模型改了、是后处理抵消的。
                    _decision["reason"] = _effect_outcome["reason"]
                    _decision["origin_reason"] = "no_target_resolved"
                _logger.warn(
                    "[REPAIR] 已授权布局效果被抵消：reason=%s mechanism=%s "
                    "责任writer=%s 目标=%s 首次撤销=%s"
                    % (_effect_outcome["reason"], _effect_outcome.get("mechanism"),
                       _effect_outcome.get("writer") or "?",
                       _effect_outcome.get("targets"),
                       (_effect_outcome.get("first_revert") or {})))
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
                        and not int(_decision.get("audit_field_touched") or 0))
            # v6.105（2026-09-24 第二十六次重跑实测）：**先尝试按目标回退**，再考虑
            # 整轮回退。
            #
            # 实测：一轮 `resolved=7 new=1`——7 条阻塞被真正解决，只因 shot 8 的补帧
            # 连带改动了已有帧、产生 1 条新的窗口错配，整轮（含另外 3 个目标的合法
            # 修复）被一起丢掉。这与 v6.86 之前的「整批全有或全无」是**同一个缺陷的
            # 应用侧与验收侧**：v6.86 已经把应用改成逐目标提交，验收侧必须同步——
            # 否则「逐目标提交」在验收这一关又被收回。
            #
            # 安全性不变：最终被采纳的结果**仍然满足「没有新增 blocking」**（这正是
            # 总计划 §6 的停止条件），只是不再让一个坏目标连坐其它好目标。定位不到
            # 目标（全局问题 / 新阻塞落在非目标镜头）时不做这种回退，退回整轮回退。
            # v6.119（用户决定）：**先在回退之前**捕获并记录新增阻塞的**身份**。
            #
            # 为什么必须在这里：`new_blocking_keys` 原本仅有一条日志，位置在
            # 「整轮回退」分支里；而本分支一旦成功按目标回退，`_decision` 就被
            # `_dec_try` 覆盖（其 `new_blocking_keys` 已是**回退后的空列表**）→
            # 那条日志再也不会执行。2026-09-25 离线取证时确认：`new=3` 只剩计数，
            # 「哪三条」无从回答。keys 在此捕获后被下游覆盖也不受影响。
            _nb_keys_round = [list(k) for k in (_decision.get("new_blocking_keys") or [])]
            _offend_raw = _new_blocking_targets(
                _decision.get("new_blocking_keys") or [], set(picked))
            # v6.143（P4，2026-09-27 十次连跑根因）：**其余可定位闸门也走按目标回退**。
            #
            # 此前只有 `new_blocking` 能按目标回退（v6.105）；`locked_changed` /
            # `locked_field_changed` / `kf_structure_invalid` 一律**整轮**回退——
            # 实测 10 次跑 20 个修复轮算出 `resolved=109`，其中 **60 条（55%）**被
            # 整轮丢弃（`locked_changed` 5轮丢35、`new_blocking` 2轮丢12、
            # `kf_structure_invalid` 3轮丢13），`run 03` 一次丢 **13 条**。
            #
            # 三个闸门本身都对（都在防「让告警消失而不是修好」），错的只是粒度：
            # 「shot 6 改了锁定台词」不该让同轮 shot 1/5/9 的真修复一起陪葬。
            # 定位源见 `_locate_offending_targets`（消息里的 `<目标名>` 前缀）；
            # **定位不到就保持整轮语义**（`non_target_changed` / `evidence_removed`
            # / `checker_failed` 明确不在此列——见该函数 docstring）。
            if not _offend_raw:
                _offend_raw = _locate_offending_targets(
                    _decision, set(picked), _locked_diff_round)
                if _offend_raw:
                    _logger.info(
                        "[REPAIR] 按闸门消息定位到肇事目标 %s（reason=%s）→ 只回退它们，"
                        "同轮其余目标的修复保留"
                        % (sorted(_offend_raw), _decision.get("reason")))
            _offend_round = _offend_raw
            # 原始拒绝原因（`_decision` 会被 `_dec_try` 覆盖，日志要报的是**起因**）
            _origin_reason = str(_decision.get("reason") or "")
            # v6.144 取证：按目标回退会把 `_decision` 换成回退后那版（accepted，
            # reason 恒为空），起因与被保护/被删的证据文本随之丢失——台账里只能看到
            # 一个空 reason，事后无法判断模型是**删了**证据还是**改写**了证据。
            _evidence_removed_round = list(_decision.get("evidence_removed") or [])
            _rolled_back_groups: list = []
            if not _decision["accepted"] and _offend_raw:
                if _nb_keys_round:
                    _logger.warn(
                        "[REPAIR] 新增 blocking %d 条（**按目标回退前**记录）: %s%s  "
                        "定位=%s"
                        % (len(_nb_keys_round), _nb_keys_round[:10],
                           (f"（另 {len(_nb_keys_round) - 10} 条见台账）"
                            if len(_nb_keys_round) > 10 else ""),
                           sorted(_offend_round) or "无法定位（全局或非目标镜头）")
                    )
                # v6.119：联动组**整组回退**——组内任一镜肇事即回退整组。
                # 只回退肇事那一镜等于把一次联动修复拆成半次，剩下的半次会自相矛盾
                # （shot 5 的 kf 时序倒置 + 灯盏回返正是这么留下的）。
                _offend, _rolled_back_groups = _expand_rollback_by_groups(
                    _offend_round, _active_groups)
                _offend_round = _offend
                if _offend and _offend != set(picked):
                    # v6.144 取证：`script` 下面会被换成回退后的版本，肇事目标的
                    # **修复文本**必须先抓下来，否则「删了证据还是改写了证据」
                    # 在日志里无从区分。
                    _rb_texts = {}
                    for _t_rb in _offend:
                        _s_rb = _shot_by_target_id(script, _t_rb) or {}
                        _rb_texts[_t_rb] = {
                            _f: str(_s_rb.get(_f) or "")
                            for _f in ("lf_desc", "motion_desc", "ff_desc", "prompt")}
                    try:
                        _try_script = _restore_targets(script, _round_before_script, _offend)
                        _merged_try = _restore_targets(merged, _round_before_script, _offend)
                        # v6.143（P4）：锁定字段判据必须**按回退后的稿子重算**。
                        # 传原来的轮级 `_locked_changed` 会让 locked 型拒绝在回退后
                        # 依然为真（它描述的是**回退前**那一版），按目标回退永远过不了
                        # 验收——这正是 P4 要修的那条链。
                        _locked_try = (_locked_before
                                       != _locked_repair_signature(_merged_try))
                        _dec_try = _repair_acceptance_decision(
                            _det_before_state,
                            _collect_repair_warnings(_try_script, novel_text),
                            _allowed_targets, _try_script, _locked_try,
                            non_target_changed=_non_target,
                            locked_by_llm_changed=_final_field_mask_violations(
                                _round_before_script, _merged_try, set(picked),
                                allowed_by_target=_allowed_by_target),
                            script_before=_round_before_script,
                            evidence_removed=_cross_field_evidence_removed(
                                _det_before_state, _try_script, set(picked)),
                            kf_structural_violations=_kf_structural_violations(
                                _round_before_script, _merged_try,
                                _allowed_by_target, set(picked), novel_text),
                            layout_structural_violations=frame_layout_structural_violations(
                                _round_before_script, _merged_try,
                                _allowed_by_target, set(picked)))
                    except Exception as _try_err:
                        _logger.warn(f"[REPAIR] 按目标回退尝试失败（退回整轮回退）: {_try_err}")
                        _dec_try = None
                    if _dec_try and _dec_try.get("accepted") and not _dec_try.get("new_blocking"):
                        script = _try_script
                        _decision = _dec_try
                        _round_ok = True
                        # v6.139：决策换成了按目标回退后的版本，兜底记账身份同步
                        _touched_ids_round = list(_dec_try.get("audit_touched_ids") or [])
                        # 决策换了，no-gain 判据必须跟着重算（否则会拿被否决那轮
                        # 的 accepted=False 当作「不是空转」）
                        _no_gain = (bool(_dec_try.get("accepted"))
                                    and not int(_dec_try.get("resolved") or 0)
                                    and not int(_dec_try.get("audit_field_touched") or 0))
                        _metrics_inc("repair.accepted_after_target_rollback")
                        _metrics_update("repair.last_decision", _dec_try)
                        _logger.info(
                            "[REPAIR] 按目标回退 %s（origin_reason=%s）后通过验收："
                            "resolved=%d new=0（同轮其余目标的修复保留）"
                            % (sorted(_offend), _origin_reason,
                               _dec_try.get("resolved") or 0)
                        )
                        if _evidence_removed_round:
                            _logger.warn(
                                "[REPAIR] 回退前判定（origin,%s）%s"
                                % (_origin_reason, str(_evidence_removed_round[:2])[:300]))
                        for _t_rb in sorted(_rb_texts):
                            _s_after = _shot_by_target_id(script, _t_rb) or {}
                            for _f, _bv in _rb_texts[_t_rb].items():
                                _av = str(_s_after.get(_f) or "")
                                if _bv != _av:
                                    _logger.warn(
                                        "[REPAIR] 回退目标 %s.%s 修复稿⇒回退后：%s ⇒ %s"
                                        % (_t_rb, _f, _bv[:110], _av[:110]))
                    elif _dec_try:
                        # v6.137：回退重试失败必须可见（本 run 的盲区——重试为何没过无从归因）
                        _logger.warn(
                            "[REPAIR] 按目标回退 %s 后仍未通过验收"
                            "（reason=%s resolved=%s 字段触达=%s new=%s）→ 整轮回退"
                            % (sorted(_offend), _dec_try.get("reason"),
                               _dec_try.get("resolved") or 0,
                               _dec_try.get("audit_field_touched") or 0,
                               _dec_try.get("new_blocking") or 0))
                        # v6.146 取证：**回退失败**这条分支此前不打修复稿文本，
                        # 于是「模型把哪一段改成了什么」在事后无从判断——而它决定
                        # 「合法改写被误杀」还是「动作被改没了」这两种相反结论。
                        for _t_rb in sorted(_rb_texts):
                            _rb_base = _shot_by_target_id(_round_before_script, _t_rb) or {}
                            for _f, _rep in _rb_texts[_t_rb].items():
                                _bs = str(_rb_base.get(_f) or "")
                                if _bs != _rep:
                                    _logger.warn(
                                        "[REPAIR] 回退未过 目标 %s.%s 基线⇒修复稿：%s ⇒ %s"
                                        % (_t_rb, _f, _bs[:110], _rep[:110]))
            if not _decision["accepted"] or not _round_ok:
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
                if _decision.get("evidence_removed"):
                    _logger.warn(
                        "[REPAIR] 跨字段判据证据被删除 "
                        f"{len(_decision['evidence_removed'])} 处（删证据能让检测器闭嘴，"
                        "但那不是修复）："
                        f"{_decision['evidence_removed'][:3]}"
                    )
                if _decision.get("new_blocking_keys"):
                    _nb_all = list(_decision["new_blocking_keys"])
                    _logger.warn(
                        "[REPAIR] 本轮新增 blocking "
                        f"{_decision['new_blocking']} 条: {_nb_all[:10]}"
                        + (f"（另 {len(_nb_all) - 10} 条见 step01_repair_rounds.json）"
                           if len(_nb_all) > 10 else "")
                        + (f"（其中 KF 帧数变动的镜头 {_decision['reindexed_kf_shots']}"
                           " 已按「该镜的该类问题」比较，位置序号不计）"
                           if _decision.get("reindexed_kf_shots") else "")
                    )
                if _decision.get("kf_structural_violations"):
                    _logger.warn(
                        "[REPAIR] KF 结构验收未过 "
                        f"{len(_decision['kf_structural_violations'])} 处（授权增删 KF 的"
                        "配套闸门，**只计本轮新引入/恶化的**：KF 须落在镜内且按时段单调，"
                        "motion 原事件文本须仍在、顺序不变；稿内既有越窗帧由最终质量门"
                        "绝对判定）："
                        f"{_decision['kf_structural_violations'][:3]}"
                    )
                _logger.warn(
                    "[REPAIR] 修复未满足接受条件"
                    f"（reason={_decision['reason']}, resolved={_decision['resolved']}, "
                    f"字段触达={_decision.get('audit_field_touched', 0)}, "
                    f"new={_decision['new_blocking']}, "
                    f"checker_failed={_decision['checker_failed']}, "
                    f"locked_changed={_decision['locked_changed']}），回退本轮修复"
                )
        except Exception as _acc_err:
            script = _round_before_script
            _metrics_inc("repair.check_failed")
            _metrics_set("repair.last_error", str(_acc_err))
            _logger.warn(f"[REPAIR] 接受条件检查失败，回退本轮修复: {_acc_err}")
        # ---- v6.119：本轮修复事务的诊断记录（含**回退前**捕获的 new_blocking_keys）----
        # 记录点在「决策已落地」之后、早退之前——下面两个 `return` 不会把诊断带走。
        # 台账消费方据此回答：这一轮引入了哪几条新阻塞、落在哪些镜头、回退了哪些
        # 目标/联动组、修前修后的 revision 各是什么。
        if repair_diag is not None:
            try:
                from shared.quality_gate import (
                    compute_script_revision as _csr_diag)
                repair_diag.append({
                    "repair_round": _round,
                    "targets": sorted(picked),
                    "groups": [sorted(g) for g in _active_groups],
                    "new_blocking_keys": _nb_keys_round,
                    "new_blocking_targets": sorted(_offend_round),
                    "rolled_back_groups": _rolled_back_groups,
                    "targets_rolled_back": sorted(_offend_round) if _rolled_back_groups else [],
                    "accepted": bool(_decision.get("accepted")),
                    "round_ok": bool(_round_ok),
                    "resolved": int(_decision.get("resolved") or 0),
                    "reason": str(_decision.get("reason") or ""),
                    # v6.144 取证：按目标回退后 `reason` 恒为空，起因（origin_reason）
                    # 与证据明细只能从这里读。
                    "origin_reason": _origin_reason,
                    "evidence_removed": _evidence_removed_round,
                    "audit_field_touched": int(
                        _decision.get("audit_field_touched") or 0),
                    "audit_touched_ids": [list(t) for t in
                                          (_decision.get("audit_touched_ids") or [])],
                    "revision_before": _csr_diag(_round_before_script),
                    "revision_after": _csr_diag(script),
                    # §14 P3：四类归因的**台账承载层**——reason 自由串只够人读，
                    # 这里落完整事实（mechanism/writer/first_revert/bindings）。
                    # 消费者：`step01_repair_rounds.json` 的读者与离线回放。
                    "effect_conflict": (_decision.get("effect_conflict") or None),
                })
            except Exception as _diag_err:
                _logger.warn(f"[REPAIR] 修复轮诊断记录失败（不影响结论）: {_diag_err}")
        # §14 P3：**冲突进下一请求**，以及**相同冲突 + 请求信息无变化 ⇒ 停止盲修**.
        #
        # 判据只看「会改变下一轮请求内容」的那几项（原因/责任 writer/目标/被抵消的
        # 条目/镜头/帧），不含摘要与时间戳——否则每轮都不同，「无变化」永远判不出来。
        # 相同 ⇒ 再发一次是同一份请求，必然得到同一个结果：停止，并把残余封存成
        # 显式阻塞理由（不清 BLOCK、不写虚假 PASS、不改重试上限与截止）。
        if _effect_outcome.get("reason"):
            _eff_sig = conflict_signature(_effect_outcome)
            _eff_prev_sig = str(_effect_conflict_prev.get("sig") or "")
            if _eff_prev_sig and _eff_sig and _eff_sig == _eff_prev_sig:
                _seal = (
                    "布局修复效果被后处理抵消且**冲突与请求信息均无变化**"
                    "（reason=%s，责任 writer=%s，目标=%s，被抵消条目=%s）——"
                    "上一轮已把同一条冲突发出去、又原样收到同一个回答，"
                    "**不再发第三次**（重试上限与截止未改）；"
                    "该残余按原样交给提交点判 BLOCK"
                    % (_effect_outcome["reason"],
                       _effect_outcome.get("writer") or "?",
                       _effect_outcome.get("targets"),
                       (_effect_outcome.get("first_revert") or {}).get("restored_names"))
                )
                _logger.error("[REPAIR] %s" % _seal)
                if block_reasons is not None:
                    block_reasons.append(_seal)
                if repair_diag is not None:
                    repair_diag.append({
                        "repair_round": _round,
                        "kind": "effect_conflict_sealed",
                        "reason": _effect_outcome["reason"],
                        "mechanism": _effect_outcome.get("mechanism", ""),
                        "writer": _effect_outcome.get("writer", ""),
                        "targets": _effect_outcome.get("targets") or [],
                        "first_revert": _effect_outcome.get("first_revert"),
                        "signature": _eff_sig,
                        "source_revision": _effect_revision,
                        "sealed_before_send": False,
                    })
                if retry_state is not None:
                    # **留档而不清空**：下一次调用要在**发请求之前**就停下
                    # （见循环开头的封存判据）。清空等于把封存忘掉，第三次照发。
                    retry_state["layout_conflict"] = {
                        "sig": _eff_sig,
                        "reason": _effect_outcome["reason"],
                        "writer": _effect_outcome.get("writer", ""),
                        "targets": _effect_outcome.get("targets") or [],
                        "source_revision": _effect_revision,
                        "text": _effect_conflict_prev.get("text") or "",
                        "sealed": True,
                    }
                return script
            if retry_state is not None:
                retry_state["layout_conflict"] = {
                    "sig": _eff_sig,
                    "text": render_conflict_feedback(_effect_outcome),
                    "reason": _effect_outcome["reason"],
                    "writer": _effect_outcome.get("writer", ""),
                    "targets": _effect_outcome.get("targets") or [],
                    "source_revision": _effect_revision,
                }
        if not _round_ok:
            # v6.137：new_blocking 型拒绝 → **带反馈重试一次**（剔除肇事目标）。
            # 内层还有轮次就内层 continue（GAP 路径，max_rounds>=2）；没有（提交
            # 路径 max_rounds=1）就把状态导出 retry_state，由调用方用下一次
            # attempt 续跑（预算仍由外层 §1.1 上限管住，审计经 _reusable 复用）。
            # 其余拒绝原因（locked_changed / non_target_changed / evidence_removed
            # 等系统性越权）保持原停止语义——那些不是"换个目标重发"能解决的。
            #
            # v6.138（九审 P1-2）：排除集只用**真实肇事目标**（`_offend_raw`），不用
            # 组扩展后的回退集——整组回退是**回滚原子性**（v6.119），不是该组成员
            # 都引入了新 blocking；把非肇事成员也排除，会让"整组回退"退化为
            # "整组放弃"（重试轮名额被白吃，剩余目标无人修）。
            _remaining = [p for p in picked if p not in _offend_raw]
            _retry_fb_text = ""
            if (_decision.get("reason") == "new_blocking"
                    and _offend_raw and _remaining):
                _retry_fb_text = (
                    "\n**上一轮补丁被拒（带反馈重试）**：\n"
                    "- 目标 %s 的补丁引入了新 blocking（%s），**该目标已从本轮移除**，"
                    "不要再输出它；\n"
                    "- 其余目标（%s）上一轮的补丁**未通过验收**（问题证据仍在稿中），"
                    "请给出真正消除矛盾的修改，不是换一种说法复述现状。\n"
                    % (sorted(_offend_raw),
                       str(_nb_keys_round[:2])[:200],
                       sorted(_remaining)))
                if retry_state is not None:
                    retry_state.clear()
                    retry_state.update({"excluded": sorted(_offend_raw),
                                        "feedback": _retry_fb_text})
            if (not _retry_used and _retry_fb_text):
                _retry_used = True
                _rejected_targets |= set(_offend_raw)
                _retry_feedback = _retry_fb_text
                _logger.warn(
                    "[REPAIR] 带反馈重试：剔除肇事目标 %s，其余 %s 重新修复（仅一次）"
                    % (sorted(_offend_round), sorted(_remaining)))
                continue
            # 本轮已被回退 → 停止（其余原因：再发下一批没有意义）
            return script
        if _no_gain:
            # v6.102：通过验收但零改善 → 回退本轮（不留无收益的改写）并停止后续轮次
            script = _round_before_script
            _metrics_inc("repair.no_gain_rolled_back")
            _logger.warn(
                "[REPAIR] 本轮无任何可测量改善（resolved=0 / 字段触达=0）→ "
                "回退本轮并停止后续轮次（继续发只会重复同一批目标）"
            )
            return script
        _metrics_inc("repair.rounds_accepted")
        # §14 P3：**通过验收的轮次**才把效果交出去（`effect_state`）。
        #
        # 为什么必须交出去：提交路径在 `_repair_shot_warnings` 之后还会跑一次
        # `finalize_step01_script`，那里 `run_hard_fix(is_final_pass=True)`
        # **再跑一遍** `group_actor_continuity`（该约束全文没有 is_final_pass /
        # is_in_repair_loop 守卫）。只保证修复轮内的规则成立，合法删除会在 finalize
        # 被补回 → 新审计重报 blocking → 又判零改善。所以规则要在**两个 pass** 上成立。
        #
        # 只交**最终仍然成立**的效果：被抵消的那条已经在稿子上不成立了，把它的
        # 「不得重建」约束带过去反而会阻止后处理做正确的事。
        if effect_state is not None and _effect_records:
            try:
                _still = _effect_ledger.surviving_keys(script)
            except Exception:
                _still = set()
            _keep = [e for e in _effect_records
                     if "%s|%s" % (e["shot_id"], e["frame_locator"]) in _still]
            if _keep:
                effect_state.setdefault("effects", []).extend(_keep)
                for _e in _keep:
                    # 逐条目绑定 ⇒ 按前缀带过去（只带帧级键会丢掉所有条目绑定）
                    _pfx = "%s|%s|" % (_e["shot_id"], _e["frame_locator"])
                    for _bk, _bd in _effect_bindings.items():
                        if _bk.startswith(_pfx):
                            effect_state.setdefault("bindings", {})[_bk] = _bd.as_record()
                effect_state["revision"] = _effect_revision
        # v6.139：轮次落定 → 本轮兜底触达正式记入「已尝试」集合（下一轮起同一
        # 问题身份不再给兜底分，见 `_repair_acceptance_decision`）。
        if audit_attempted is not None and _touched_ids_round:
            audit_attempted.update(_touched_ids_round)
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


def _snapshot_log_delta(log_file, dest, snap_dir) -> None:
    """只快照**本次 run 的增量**日志（v6.112）.

    原来用 `shutil.copy2` 整份复制**累积**日志，而日志逐 run 增长 → snapshots/
    占用接近平方增长（实测 20 份 ≈ 449MB）。改成按字节偏移切片：偏移记在
    `snapshots/_log_offset`（上一次快照时的 EOF），本次只写 [offset, EOF) 这一段。
    快照文件仍是**可直接 grep 的纯文本**，只是范围限定为本次 run——文件头有一行
    `#` 注释标出字节区间，避免被误读成完整日志。

    偏移小于 0 或大于当前文件大小（日志被重建/截断）时退化为全量复制并重置偏移。
    """
    try:
        offset = int((snap_dir / "_log_offset").read_text(encoding="utf-8").strip() or "0")
    except Exception:
        offset = 0
    size = log_file.stat().st_size
    if offset < 0 or offset > size:
        offset = 0
    with log_file.open("rb") as fh:
        fh.seek(offset)
        blob = fh.read()
    header = b"# step01.log delta bytes=%d..%d (v6.112 per-run slice)" % (offset, size)
    dest.write_bytes(header + bytes([10]) + blob)
    try:
        (snap_dir / "_log_offset").write_text(str(size), encoding="utf-8")
    except Exception:
        pass


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
            _snapshot_log_delta(log_file, snap_dir / f"step01_{ts}.log", snap_dir)
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        # 轮转上限单源 shared/paths.MAX_SNAPSHOTS_PER_CHAPTER（env SNAPSHOTS_KEEP，默认 20）
        from shared.paths import MAX_SNAPSHOTS_PER_CHAPTER
        for old in snaps[:-MAX_SNAPSHOTS_PER_CHAPTER]:
            old.unlink(missing_ok=True)
            log_pair = old.name.replace("script_result_", "step01_").replace(".json", ".log")
            (snap_dir / log_pair).unlink(missing_ok=True)
        # v6.123：BEST-GUARD 选择前的候选快照（独立前缀）按**同一上限**轮转——
        # 它不进候选池，但同样不能无限增长。
        _cand_snaps = sorted(snap_dir.glob("candidate_pre_bestguard_*.json"))
        for old in _cand_snaps[:-MAX_SNAPSHOTS_PER_CHAPTER]:
            old.unlink(missing_ok=True)
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
#: run 级计时起点的 monotonic 孪生（`_timing_start()` 一起设；墙上时钟会被系统时间调整
#: 污染，**截止时刻只认 monotonic**——工作包 §4 B）
_STEP_TIMING_T0_MONO = None
_STEP_LAST_MARK_T = None
_RUN_METRICS: dict = {}
_METRICS_LOCK = None
#: **本轮终局**（落进 metrics/timing）：`{"status": "ok"|"failed", "code": "", "detail": ""}`。
#: 由 `_run_finish()` 设置；`_timing_dump` / `_metrics_dump` 只读它，
#: 不再各自硬编码 "ok"（2026-10-02 复核：失败运行被报成 ok）。
_RUN_OUTCOME: dict = {"status": "ok", "code": "", "detail": ""}


def _run_outcome_reset() -> None:
    _RUN_OUTCOME.update({"status": "ok", "code": "", "detail": ""})


def _run_finish(status: str, code: str = "", detail: str = "") -> None:
    """记录本轮终局（幂等：**失败优先**，失败后再写 ok 不覆盖）."""
    _s = str(status or "").strip() or "ok"
    if _RUN_OUTCOME["status"] == "failed" and _s == "ok":
        return
    _RUN_OUTCOME.update({"status": _s, "code": str(code or ""), "detail": str(detail or "")[:300]})


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
    """Persist structured call/time/repair metrics without affecting the run.

    2026-10-02 复核：载荷里**必须**有 `run_id` 与**真实终局**——旧版两章都以
    `PlanInfeasibleError` 收场，metrics 却写 `_total status=ok` 且 `calls={}`，
    等于把失败运行报成成功、且无任何调用读数可对账。
    """
    try:
        import time as _t
        from shared.json_io import atomic_dump_json as _adj

        payload = dict(_RUN_METRICS)
        payload["run_id"] = _step01_run_id()
        payload["status"] = _RUN_OUTCOME["status"]
        payload["outcome_code"] = _RUN_OUTCOME["code"]
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
    global _STEP_TIMING_T0, _STEP_LAST_MARK_T, _STEP_TIMING_T0_MONO
    import time as _t
    _STEP_TIMING_T0 = _t.time()
    _STEP_TIMING_T0_MONO = _t.monotonic()          # 截止时刻只用 monotonic（工作包 §4 B）
    _STEP_LAST_MARK_T = _STEP_TIMING_T0
    _STEP_TIMING.clear()
    _run_outcome_reset()
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


def _project_commit_outcome(commit_summary: dict | None) -> None:
    """提交结果 → **运行终局**（两处提交返回共用的唯一投影）.

    复核 2026-10-04 C1：章 2 那次 `step01_commit.json` = BLOCK（5 条 blocking、修复轮零改善），
    而 `step01_metrics.json` / `step01_timing.json` 记的是 `status=ok`、`outcome_code=""`
    ——提交点是**唯一的正式产物出口**，它 BLOCK 就是这一轮失败，运行终局不能报 ok。
    审计自己的 `audit_status=ok` 留在提交清单里（那是"审计调用成功"的事实，不冲突）。

    失败优先：`_run_finish` 本身幂等，早期已记的失败不会被这里的 `ok` 覆盖。
    """
    _s = (commit_summary or {}).get("status")
    if _s == "COMMITTED":
        _run_finish("ok")
        return
    _detail = str((commit_summary or {}).get("detail") or "")
    _code = str((commit_summary or {}).get("blocking_code") or "") or "commit_blocked"
    _run_finish("failed", _code, _detail or "提交点未产出 COMMITTED 清单")


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
                "phase": "_total", "status": _RUN_OUTCOME["status"],
                "phase_duration_sec": round(max(0.0, now - last), 1),
                "elapsed_since_start_sec": round(now - _STEP_TIMING_T0, 1),
                # Backward-compatible alias; cumulative, not phase cost.
                "elapsed_sec": round(now - _STEP_TIMING_T0, 1),
            })
        _payload = {"run_started": _STEP_TIMING_T0, "run_id": _step01_run_id(),
                    "status": _RUN_OUTCOME["status"],
                    "outcome_code": _RUN_OUTCOME["code"],
                    "phases": _STEP_TIMING}
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


def _planning_deadline_at(cap_sec=None):
    """整次 Step 01 的**规划截止时刻**（monotonic 绝对时刻；工作包 §4 B）.

    `deadline = min(整次剩余预算换算出的截止点, 现在 + cap_sec)`；两者都没有 ⇒ `None`
    （不设截止 ⇒ 通道行为同改造前）。换算只做**一次**，此后所有环节按它重算余量。
    """
    from _text_gen.deadline import planning_deadline_at
    return planning_deadline_at(_budget_left_sec(), cap_sec)


def _budget_left_sec():
    """run 总预算**剩余秒数**（未 start 或预算 0 → None）."""
    import time as _t
    if _STEP_TIMING_T0 is None:
        return None
    _total = _total_budget_sec()
    if _total <= 0:
        return None
    return round(_total - (_t.time() - _STEP_TIMING_T0), 1)


#: 审计请求超时上限（v6.152 / A1）：对齐通道自身的 `AGENT_PLAN_TEXT_TIMEOUT_SEC` = 300
AUDIT_TIMEOUT_CAP_SEC = 300
#: 给提交收尾（写盘 / 自检 / BLOCK 记账）留的余量秒数
AUDIT_COMMIT_RESERVE_SEC = 60


def _audit_budget_allowance() -> tuple:
    """**整次审计**的可用时长与截止时刻：`(allowance_sec, deadline_ts | None, info)`.

    v6.152（A1，2026-09-29 复核两轮）：上限 180 → 300（对齐通道自身），且**必须受本跑
    剩余总预算约束**——实测单跑已到 950–995s，只抬上限等于把「审计超时 BLOCK」换成
    「总预算耗尽 BLOCK」。

    这里给的是**整次审计**（含截断/解析重采样）的额度，不是单次请求的超时：调用方把它
    换成 `deadline_ts` 传进审计，由审计在**每次实际发起前**重算单次超时；剩余不足以
    发起一次有意义的调用时，审计**不发请求**、直接记 `budget_exhausted`。
    旧实现给单次请求截一个 5s 下限，既注定超时、又照样挤占收尾余量。

    无总预算约束（未 start / 预算 0）→ `deadline_ts=None`，行为与旧版一致。
    """
    import os as _os
    try:
        cap = int(float(_os.getenv("AUDIT_TIMEOUT_SEC",
                                   str(AUDIT_TIMEOUT_CAP_SEC)) or AUDIT_TIMEOUT_CAP_SEC))
    except (TypeError, ValueError):
        cap = AUDIT_TIMEOUT_CAP_SEC
    left = _budget_left_sec()
    if left is None:
        return cap, None, {"cap": cap, "left": None, "reserve": None,
                           "why": "无总预算约束（未 start 或预算 0），用上限 %ss" % cap}
    allowance = int(max(0, min(cap, left - AUDIT_COMMIT_RESERVE_SEC)))
    import time as _t
    if allowance >= cap:
        return cap, _t.time() + cap, {
            "cap": cap, "left": left, "reserve": AUDIT_COMMIT_RESERVE_SEC,
            "why": "余量充足，用上限 %ss" % cap}
    return allowance, _t.time() + allowance, {
        "cap": cap, "left": left, "reserve": AUDIT_COMMIT_RESERVE_SEC,
        "why": ("上限 %ss 受剩余总预算约束（剩余 %.1fs - 收尾预留 %ss）"
                % (cap, left, AUDIT_COMMIT_RESERVE_SEC)),
    }


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
    if not DURATION_MIN_SEC <= duration <= DURATION_MAX_SEC:
        return False
    if not script.get("hook"):
        return False
    return True


class _CandidateDialogueBroken(Exception):
    """单候选台词完整性破损（内部控制流标记）：携带 broken 明细，不进候选池.

    不继承 RuntimeError——候选循环的 `except RuntimeError` 是"生成失败→重试"语义，
    台词破损是**内容结论**，必须与 LLM 调用失败分开计数、分开报告。
    """

    def __init__(self, broken):
        super().__init__("%d 句破损" % len(broken or []))
        self.broken = list(broken or [])


class _CandidateGateError(Exception):
    """台词完整性门**自身异常**（如净化器 `sanitize_narration` 抛错），不是稿件破损.

    v6.135 四审 P1：门内匹配不再吞净化异常退回原样（那会在"两侧原样恰好相同"时
    静默放行、fail-closed 分支收不到错误）——异常向外传播到这里转成独立控制流标记。
    fail-closed：异常候选判无效不进池、尝试名额照常消耗；计数进 attempt_stats 的
    `gate_errors` 桶，**不得混入 dialogue_broken**（把检查故障统计成稿件截断 =
    四审 P2 同型误归因）。
    """


class _CandidateContractBroken(Exception):
    """候选违反**事件履约契约**（v6.138）——节拍覆盖/声明顺序/引用越界.

    与台词破损同性质（内容结论、非检查故障）：fail-closed 不进池、尝试名额照常
    消耗；独立类型独立计数（attempt_stats.contract_broken），**不得混入
    dialogue_broken**——把「节拍无承载」统计成「台词截断」= 五审同型误归因。
    """

    def __init__(self, violations):
        super().__init__(violations)
        #: check_event_contract 的 violations（list[dict]：code/shot/detail）
        self.violations = violations


def _stop_if_outline_unavailable(res, novel_text: str, deadline_monotonic) -> None:
    """结构化大纲不可执行 ⇒ 写对应诊断并**停止**（工作包 §4 D / §7.6）.

    - **调用类失败**（`call_failed` / `illegal_response` / `outline_invalid`）：**它就是主
      终局**——即使手上还有一份基线计划（复核 2026-10-02 之三 P1：旧写法把这种情形交给
      生成前内容门，终局变成 `PlanContractInvalidError` / `plan_contract_invalid`，真实的
      调用失败只剩在修订历史里，归因反了）。基线的内容问题用 `baseline_problems` **单独
      记录**在同一份诊断里，不改终局。
    - **计划层结论候选**（`contract_unclosed` / `budget_infeasible` / `no_change` / `loop`）
      且有基线：交给**生成前统一入口**（同一份验收链、同一份诊断）——它必定抛出；截止已到
      时由那里新增的**时间门**负责，错误文本与"故事不可行"分开。
    - **没有基线**：写 `unevaluable` 的"判不了"诊断（`conclusion=<失败码>`），**不冒充**
      内容结论，也不把 None 继续传成"无计划"。
    - `res is None`（未启用大纲提取）⇒ 什么都不做：后续由生成前门按"计划缺失"fail-closed。
    """
    if not isinstance(res, dict) or res.get("executable"):
        return
    from step01_script_gen.plan_feasibility import (
        dump_plan_unavailable, check_plan_before_generation, PlanInfeasibleError,
        OUTLINE_CALL_FAILURE_CODES)
    # 复核 2026-10-04：失败投影一律走**有界且显式标注**的展示出口，
    # 不用裸 `[:N]`（切在句中断开语义边界，读起来像另一句话）。
    from step01_script_gen.plan_revision import rejection_display
    _f = dict(res.get("failure") or {})
    _code = str(_f.get("code") or "unknown")
    _detail = str(_f.get("detail") or "")
    _plan = res.get("plan")
    _logger.error("[PLAN] 大纲未产出可执行计划（%s）⇒ 停在计划层，不生成候选：%s"
                  % (_code, rejection_display(_detail, limit=200)))
    if _code in OUTLINE_CALL_FAILURE_CODES:
        # 基线的内容问题**由生产者给**（`acceptance.problems`——大纲侧已算过同一份验收链）：
        # 报告层只组合透传，不重算第二份。复核 2026-10-02 之四 D-1：旧写法在这里重算，
        # 重算自己抛异常时兜底项是**字符串**，接着被 `.get()` 调用 ⇒ `AttributeError`，
        # 诊断一次也没写成。现在归一集中在一处（`problem_records` / `record_text`），
        # 兜底项也保持**记录形状**。
        from step01_script_gen.plan_revision import problem_records, record_text
        _acc = res.get("acceptance")
        _base = problem_records(_acc.get("problems") if isinstance(_acc, dict) else None)
        if not _base and isinstance(_plan, dict) and _plan:
            try:
                from step01_script_gen.outline import plan_acceptance_problems
                _base = problem_records(plan_acceptance_problems(_plan, novel_text))
            except Exception as _e:                   # noqa: BLE001 —— 另列栏不许拖垮终局
                _base = [{"code": "baseline_problems_unavailable",
                          "detail": "基线内容问题无法列出：%s: %s" % (type(_e).__name__, _e)}]
        if _base:
            _rest = max(0, len(_base) - 3)
            _logger.error(
                "[PLAN] 基线计划仍有 %d 条内容问题（**另列**，不改本轮终局）：%s%s"
                % (len(_base), "；".join(record_text(x) for x in _base[:3]),
                   "（另有 %d 条未显示，完整清单见 step01_plan_infeasible.json）" % _rest
                   if _rest else ""))
        # 本轮**响应为什么被拒**的具体记录（复核 2026-10-02 之五）：诊断只有一句
        # "关键字段缺失/类型错误"时，人看不出违反哪条规则、也回灌不了下一轮。
        _fprobs = problem_records(_f.get("problems"))
        if _fprobs:
            _logger.error("[PLAN] 首版被拒的具体原因（%d 条）：%s"
                          % (len(_fprobs), "；".join(record_text(x) for x in _fprobs[:3])))
        # **逐轮**拒收记录（复核 2026-10-02 之五）：`failure.detail` 只留最后一条，
        # 三轮各自为什么被拒收是收敛分析的输入（"留档要宽"）。
        _rev = res.get("revision")
        _rej = problem_records(_rev.get("rejections") if isinstance(_rev, dict) else None)
        dump_plan_unavailable(CHAPTER_DIR, code=_code, detail=_detail, plan=_plan,
                              baseline_problems=_base, failure_problems=_fprobs or None,
                              replan_rejections=_rej or None)
        raise PlanInfeasibleError(
            "大纲未产出可执行计划（%s）⇒ 停在计划层，不生成完整稿：%s"
            % (_code, rejection_display(_detail, limit=200)))
    if isinstance(_plan, dict) and _plan:
        check_plan_before_generation(_plan, chapter_dir=CHAPTER_DIR, novel_text=novel_text,
                                     deadline_monotonic=deadline_monotonic)
        # 走到这里说明基线通过了生成前门——与 `executable=False` 自相矛盾（两处判据不一致）
        raise PlanInfeasibleError(
            "大纲判定为不可执行，但基线计划通过了生成前统一入口——两处判据不一致，"
            "按 fail-closed 停在计划层")
    dump_plan_unavailable(CHAPTER_DIR, code=_code, detail=_detail)
    raise PlanInfeasibleError(
        "大纲未产出可执行计划（%s）⇒ 停在计划层，不生成完整稿：%s" % (_code, _detail[:200]))


def _generate_script_candidates(
    novel_text: str,
    character_bank=None,
    scene_objects_bank=None,
    outline: dict | None = None,
    deadline_monotonic=None,
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
    # v6.131（2026-09-25 收口）：**生成前的计划门**——原文核验 + 预算核算，挂在候选扇出
    # **之前**，因此一轮只验算一次；不可行/不可评估/缺计划时**零次候选生成**，直接终止。
    # `PlanInfeasibleError` 不是 `RuntimeError`，下方候选循环的 `except RuntimeError`
    # （"生成失败→重试"语义）接不住它，不存在"每个候选重试一次"。
    from step01_script_gen.plan_feasibility import check_plan_before_generation
    from step01_script_gen.plan_feasibility import (DialogueGateError,
                                                    DialogueIncompleteError,
                                                    EventContractBrokenError,
                                                    check_event_contract,
                                                    check_script_dialogue_integrity,
                                                    bind_plan_identity,
                                                    dump_dialogue_incomplete,
                                                    plan_dialogue_lines)
    # 工作包 §4 D：生成前门接**同一个**绝对截止时刻（默认 None 保持旧行为）——
    # 截止已到时它先写 `deadline_exhausted` 诊断再停，不把"时间用尽"写成"故事不可行"。
    check_plan_before_generation(outline, chapter_dir=CHAPTER_DIR, novel_text=novel_text,
                                 deadline_monotonic=deadline_monotonic)

    from shared.config import SCRIPT_CANDIDATE_TOPK, SCRIPT_CANDIDATE_MODELS
    import threading as _threading
    _candidates: list = []  # [(score_dict, script)]
    _last_err: Exception | None = None
    # v6.135：台词完整性硬门的破损明细（每元素 = 一个破损候选的 broken 列表）
    _dialogue_broken: list = []
    _dialogue_gate_errs: list = []
    # v6.138：事件履约契约的违规明细（每元素 = 一个违规候选的 violations 列表）
    _contract_broken: list = []
    _dlg_total = len(plan_dialogue_lines(outline))
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
        # v6.135（工作包 sunny-kindling-wilkinson；预算语义 2026-09-26 二审 C1 定稿）：
        # 候选层**台词完整性硬门**——破损候选直接判无效、不占合格名额；**差额在既有
        # `_max_attempts` 预算内继续补取**（与解析失败共用同一预算池，不扩预算）；
        # 预算用尽仍不足 → 按实际合格数收池，**全部候选破损才显式失败**；早停只由
        # 合格候选触发。
        # ⚠️ 职责边界（二审 H3）：本门在 parse（含 post_fix/hard_fix）之后、但在
        # `finalize_step01_script` 与 `_scoped_split_over_cap_shots` **之前**——
        # 拆分层产生的句中碎片**不由本门负责**，由主流程 finalize 后、BEST-GUARD 前
        # 的终稿门负责（那里才盖到送选稿/提交门的最终形态）。
        # 在评分之前跑（确定性、零模型调用），省掉破损稿的评分/质检开销。
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(_cand, outline)
        except Exception as _e:
            # v6.135 四审 P1：门自身异常（净化器故障等）不得退回原样匹配放行，
            # 也不得计入"台词破损"——独立类型，循环层 fail-closed 分账。
            raise _CandidateGateError(str(_e)) from _e
        if not _dlg_ok:
            raise _CandidateDialogueBroken(_dlg_broken)
        # v6.138（九审根因 1）：**事件履约契约**——plan_refs 声明的节拍覆盖/顺序/
        # 越界（确定性、零模型调用）。台词逐字已由上一道门收口；这里核「声明与
        # 计划一致」，缺载节拍（如「恐吓隐瞒」无承载）在入池前就判无效，不再等
        # 提交点语义审计撞上修复轮不可修复死锁。
        # 2026-09-27（复核 P0-1）：**先绑定身份、再核验**。绑定是「替新稿声明来源」，
        # 不是「免检」——已有身份**不覆盖**（覆盖会把「对另一版计划作的声明」洗成对
        # 当前计划的），随后 `require_declaration=True` 要求它真的声明过并逐项核验。
        # 旧顺序（先核验、通过后才落戳）有洞：新稿没有 `plan_id` ⇒ 校验函数直接按
        # 「未声明」返回 ⇒ **未经覆盖/顺序核验**就被盖上「已核验」的身份。
        bind_plan_identity(_cand, outline)
        try:
            _ct_ok, _ct_viol, _ct_declared = check_event_contract(
                _cand, outline, require_declaration=True)
        except Exception as _e:
            raise _CandidateGateError(str(_e)) from _e
        if not _ct_ok:
            raise _CandidateContractBroken(_ct_viol)
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
            except _CandidateDialogueBroken as _e:
                # v6.135：台词破损 = 内容结论，与"生成失败"分开计数分开报告；
                # 尝试名额照常消耗（预算语义见 _gen_one_candidate 注释）
                _dialogue_broken.append(_e.broken)
                _logger.warn(
                    "[CANDIDATE] dialogue integrity failed（台词完整性硬门，判无效不进池，"
                    f"{len(_e.broken)}/{_dlg_total} 句破损）："
                    + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                                for b in _e.broken[:3]))
                _console(f"  [候选] 台词破损（{len(_e.broken)}/{_dlg_total} 句），判无效"
                         f"（{_attempt}/{SCRIPT_PARSE_RETRIES}）...")
            except _CandidateGateError as _e:
                # v6.135 四审 P1：门自身异常 fail-closed——判无效不进池、名额消耗，
                # 与"台词破损"分账（attempt_stats.gate_errors），不裸崩不放行
                _dialogue_gate_errs.append(str(_e))
                _logger.error(
                    "[CANDIDATE] dialogue gate error（台词门自身异常，fail-closed 判无效"
                    f"不进池，{_attempt}/{SCRIPT_PARSE_RETRIES}）：{_e}")
                _console(f"  [候选] 台词门异常（fail-closed，"
                         f"{_attempt}/{SCRIPT_PARSE_RETRIES}）...")
            except _CandidateContractBroken as _e:
                # v6.138：事件契约破损 = 内容结论，独立分账（attempt_stats.contract_broken）
                _contract_broken.append(_e.violations)
                _logger.warn(
                    "[CANDIDATE] event contract failed（事件履约契约，判无效不进池，"
                    f"{_attempt}/{SCRIPT_PARSE_RETRIES}）："
                    + "；".join("%s@%s:%s" % (v.get("code"), v.get("shot"),
                                             str(v.get("detail"))[:60])
                                for v in _e.violations[:3]))
                _console(f"  [候选] 事件契约破损（{len(_e.violations)} 条），判无效"
                         f"（{_attempt}/{SCRIPT_PARSE_RETRIES}）...")
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
                    except _CandidateDialogueBroken as _e:
                        # v6.135：同串行路径——破损候选不进池，尝试名额照常消耗
                        _dialogue_broken.append(_e.broken)
                        _logger.warn(
                            "[CANDIDATE] dialogue integrity failed（台词完整性硬门，"
                            f"判无效不进池，{len(_e.broken)}/{_dlg_total} 句破损）："
                            + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                                        for b in _e.broken[:3]))
                    except _CandidateGateError as _e:
                        # v6.135 四审 P1：同串行路径——门自身异常 fail-closed 分账
                        _dialogue_gate_errs.append(str(_e))
                        _logger.error(
                            "[CANDIDATE] dialogue gate error（台词门自身异常，"
                            f"fail-closed 判无效不进池，{_attempt}/{_max_attempts}）：{_e}")
                        _console(f"  [候选] 台词门异常（fail-closed，"
                                 f"{_attempt}/{_max_attempts}）...")
                    except _CandidateContractBroken as _e:
                        # v6.138：同串行路径——事件契约破损独立分账
                        _contract_broken.append(_e.violations)
                        _logger.warn(
                            "[CANDIDATE] event contract failed（事件履约契约，判无效不进池，"
                            f"{_attempt}/{_max_attempts}）："
                            + "；".join("%s@%s:%s" % (v.get("code"), v.get("shot"),
                                                     str(v.get("detail"))[:60])
                                        for v in _e.violations[:3]))
                        _console(f"  [候选] 事件契约破损（{len(_e.violations)} 条），判无效"
                                 f"（{_attempt}/{_max_attempts}）...")
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
        # v6.135 四审 P1：门自身异常与"台词破损"都无合格候选 ⇒ 显式失败，
        # 但三者在计数与文案里严格分账（检查故障 ≠ 稿件截断 ≠ 生成失败）。
        # v6.135 五审：顶层结论随实际失败类型区分——纯门故障抛 `DialogueGateError`
        # + 诊断 `conclusion=dialogue_gate_error`（稿件完整性未被判定）；
        # 含真实破损（含混合失败）抛 `DialogueIncompleteError` + `dialogue_incomplete`，
        # 各类计数留在 attempt_stats。
        if _dialogue_broken or _dialogue_gate_errs or _contract_broken:
            # v6.135（决策点 1，用户采纳审核建议）：存在台词破损且无合格候选 → **显式失败**，
            # 不回退"最少破损"候选、不静默落回历史快照（破损稿进提交门 = 死锁）。
            # v6.135 三审 P2-2：尝试构成**分开计数**——破损与生成/解析失败不得混写成
            # "全部破损"（混合失败时那句结论会掩盖另一类故障）。
            # v6.138：事件契约破损同样显式失败，第四个独立分账桶（contract_broken）。
            _n_broken = len(_dialogue_broken)
            _n_gate = len(_dialogue_gate_errs)
            _n_contract = len(_contract_broken)
            _n_other = max(0, _attempt - _n_broken - _n_gate - _n_contract)
            _stats = {"total": _attempt, "dialogue_broken": _n_broken,
                      "gate_errors": _n_gate, "contract_broken": _n_contract,
                      "other_failed": _n_other}
            _mix = ("%d 次台词破损、%d 次台词门异常、%d 次事件契约破损、%d 次生成/解析失败"
                    % (_n_broken, _n_gate, _n_contract, _n_other))
            if _dialogue_broken:
                _dump_batches = _dialogue_broken
                _first = _dialogue_broken[0][0] if _dialogue_broken[0] else {}
                _exc_cls = DialogueIncompleteError
                _head = "候选层台词完整性硬门"
                _tail = "（破损稿在提交门不可修复，见 09-26 实跑 locked_changed 死锁）"
            elif _contract_broken:
                _dump_batches = [[{"line": "",
                                   "reason": "event_contract: %s"
                                             % _contract_broken[0][0].get("code")}]]
                _first = {"line": "事件履约契约违规（%s…）"
                                  % str(_contract_broken[0][0].get("detail"))[:40],
                          "reason": "event_contract: %s"
                                    % _contract_broken[0][0].get("code")}
                _exc_cls = EventContractBrokenError
                _head = "候选层事件履约契约（节拍承载/声明顺序）"
                _tail = "（无承载节拍的稿子在提交门不可修复，见 09-26 实跑 MISSING_PLOT 死锁）"
            else:
                _first = {"line": "",
                          "reason": "gate_error: %s" % _dialogue_gate_errs[0]}
                _dump_batches = [[_first]]
                _exc_cls = DialogueGateError
                _head = "候选层台词完整性门自身异常"
                _tail = "（fail-closed 不进入选稿/提交）"
            dump_dialogue_incomplete(CHAPTER_DIR, _dump_batches,
                                     total_lines=_dlg_total, attempt_stats=_stats,
                                     plan=outline,
                                     conclusion=("event_contract_broken"
                                                 if _exc_cls is EventContractBrokenError
                                                 else None))
            raise _exc_cls(
                "%s：%d 次尝试中 %s"
                "（计划 %d 句；示例：%s…［%s］）⇒ 无合格候选，不进入选稿/提交%s"
                % (_head, _attempt, _mix, _dlg_total,
                   str(_first.get("line") or "")[:40], _first.get("reason"), _tail),
                broken_batches=_dump_batches)
        raise RuntimeError(f"分镜 JSON 连续 {_max_attempts} 次解析失败: {_last_err}")
    _candidates.sort(key=_candidate_sort_key)
    return _candidates


# 「已知语义欠账」台账文件名（v6.115，审核）
_BLOCKED_LEDGER_NAME = "_blocked_revisions.json"


def _blocked_ledger_path():
    return OUTPUT_FILE.parent / "snapshots" / _BLOCKED_LEDGER_NAME


def _revision_aliases_or_empty(script) -> dict:
    """`{版本号: revision}`——一份稿件在**各版 revision 算法**下的身份（失败 → 空表）.

    2026-09-30 复核第 4 轮 P1：换哈希算法不能让未解决的语义欠账失去匹配。
    第 5 轮 P1-2 补：身份**按版本**使用（判同/欠账都不许把各版本混成一个等价关系），
    所以这里返回的是版本字典而不是扁平列表。计算失败只降级为「少记兼容身份」，
    **绝不**因此打断 BLOCK / 选稿路径。
    """
    try:
        from shared.quality_gate import script_revision_aliases
        return {int(k): str(v) for k, v in dict(script_revision_aliases(script)).items()}
    except Exception as exc:
        _logger.warn(f"[BEST-GUARD] revision 别名计算失败（台账少记兼容身份）: {exc}")
        return {}


def _current_criteria() -> dict:
    """当前判据指纹（规则源 + 提示源）——台账按它判断「欠账是否仍然成立」."""
    return {"rules": str(RULE_SOURCE_FINGERPRINT or ""),
            "prompts": str(PROMPT_SOURCE_FINGERPRINT or "")}


def _load_blocked_ledger() -> dict:
    """台账原样读出（读坏/缺失 → 空 dict，不阻断选稿）."""
    try:
        with open(_blocked_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_blocked_revisions() -> dict:
    """**在现行判据下仍然欠账**的身份，按**登记版本**分组：`{版本号: {revision}}`.

    （旧签名是 `set`；改按版本分组的原因见 `shared.quality_gate.script_revision_aliases`
    ——各版本身份不能混成一个等价关系。）

    2026-09-27（用户裁决 4）：台账条目带上登记时的判据指纹。判据/提示变了
    （`rules` / `prompts` 指纹不同）→ 那条欠账是**旧判据下**的结论，不再据此
    排除该稿：判据本身可能已经修掉（本轮 `frame_presence_consistency` 的时态误判、
    缺段重复计量都是这一类）。指纹不符的条目在选稿日志里显式列出，不静默丢弃。

    没有指纹的老条目（本改动之前登记的）按**仍然欠账**处理——保守方向：
    宁可少选一份旧稿，不可凭无法核对的旧结论放它回来。
    """
    data = _load_blocked_ledger()
    revs = [str(x) for x in (data.get("revisions") or [])]
    crit = data.get("criteria")
    cur = _current_criteria()
    # 2026-09-30 复核第 5 轮 P1-2：每条欠账记**登记时的算法版本**，选稿按版本比对。
    # 复核第 6 轮更正：**「旧记录只可能是 v1」这个假设要撤回**——上一版代码已经写出过
    # `"revision_version": 2` 但没有逐条 `entry_versions` 的台账（值本身就是 v2 算的），
    # 一律当 v1 会让同一份稿件的旧 v2 欠账**查不到**（离线已复现）。三级推断：
    #   ① 逐条 `entry_versions[rev]` → 用它（现行写法，唯一权威）；
    #   ② 顶层 `revision_version` + 无逐条映射（单版本台账）→ **按该版本解释**；这类
    #      台账无法逐条区分 v1/v2，故同时按 v1 登记（欠账宁可多排除一稿，不因迁移丢失，
    #      与台账既有的保守口径一致）；
    #   ③ 两者都没有（真·旧台账）→ v1。
    _ver_raw = (data.get("entry_versions")
                if isinstance(data.get("entry_versions"), dict) else {})
    _alias_raw = (data.get("alias") if isinstance(data.get("alias"), dict) else {})
    _ledger_ver = 0
    try:
        _ledger_ver = int(data.get("revision_version") or 0)
    except (TypeError, ValueError):
        _ledger_ver = 0

    def _versions_of(rev: str) -> set:
        _r = str(rev)
        if _r in _ver_raw:                       # ① 逐条权威
            try:
                return {int(_ver_raw[_r])}
            except (TypeError, ValueError):
                return {1}
        if _ledger_ver >= 2:                     # ② 单版本台账（旧 v2 欠账）
            return {1, _ledger_ver} if _r not in _alias_raw else {_ledger_ver}
        return {1}                               # ③ 真·旧台账

    def _put(dest: dict, rev: str) -> None:
        for _v in _versions_of(rev):
            dest.setdefault(_v, set()).add(str(rev))

    if not isinstance(crit, dict):
        out: dict = {}
        for _r in revs:
            _put(out, _r)
        return out
    _issues = data.get("issues") if isinstance(data.get("issues"), dict) else {}
    out, stale = {}, []
    _partly: list = []
    for rev in revs:
        _rec = crit.get(rev)
        _iss = _issues.get(rev)
        if isinstance(_iss, list) and _iss:
            # v6.149：**逐条**判——只把「所依赖判据已变」的那些条目作废，
            # 其余仍然排除该稿（旧口径是任何指纹一动就整批放行）。
            _force = [it for it in _iss
                      if isinstance(it, dict) and _debt_issue_in_force(it)]
            if _force:
                _put(out, rev)
                if len(_force) < len(_iss):
                    _partly.append((rev, len(_iss) - len(_force)))
                continue
            stale.append(rev)
            continue
        # 老条目（无逐条记录）：退回整条指纹口径
        if isinstance(_rec, dict) and (_rec.get("rules"), _rec.get("prompts")) != (
                cur["rules"], cur["prompts"]):
            stale.append(rev)
            continue
        _put(out, rev)
    if _partly:
        _logger.info(
            "[BEST-GUARD] 台账里 %d 份欠账稿**部分**问题所依赖的判据已变"
            "（按具体问题计入，其余仍排除）：%s"
            % (len(_partly), ["%s×%d" % (r[:12], n) for r, n in _partly[:6]]))
    if stale:
        _logger.info(
            "[BEST-GUARD] 台账里 %d 份欠账稿是在**旧判据**下登记的（规则/提示指纹已变）"
            "——本轮不据此排除，按现行判据重新评估：%s"
            % (len(stale), [r[:12] for r in stale[:6]]))
    return out


def _snapshot_cross_field_blocking(script: dict):
    """快照在**现行确定性判据**下的跨字段 blocking（v6.149 候选资格初筛用）.

    与提交点同源（`cross_field_issues`），而不是质量门的风险计数——实测
    2026-09-29：快照 `script_result_20260929_053150.json` 以「风险1」入选，一进
    提交点就带 `result_action_support` 被判 blocking，反复被选中、反复 BLOCK。
    读坏/异常 → 空表（不阻断选稿）。
    """
    from step01_script_gen.cross_field_checks import cross_field_issues
    try:
        return [i for i in (cross_field_issues(script)[0] or [])
                if str(getattr(i, "severity", "") or "")
                in ("hard", "visual_risk", "blocking")]
    except Exception as exc:
        # v6.150（复核发现 4）：**校验器异常 ≠ 没有问题**。旧实现返回空表，与「无
        # blocking」不可区分——校验器一旦坏掉，所有快照都会「干净」地进候选池。
        # 返回 None 表示**未能校验**，调用方据此让该快照本次失去候选资格。
        _logger.warn("[BEST-GUARD] 快照跨字段校验器异常（本次不具候选资格）: %s" % exc)
        return None


def _debt_issue_records(blocking) -> list:
    """把 BLOCK 清单转成**逐条欠账记录**（v6.149，复核根因 3）.

    每条记 `code/shot/field` + **该条所依赖的判据指纹**。判据分流：
      · `model_audit_*` → **审计提示版本** `audit_prompt_version()`；
      · 其余（确定性）→ **规则源指纹** `RULE_SOURCE_FINGERPRINT`。

    为什么必须逐条：旧口径只在整条 entry 上记「规则源 + 提示源」两个**全局**指纹，
    任何一处提示改动都会让**整批**欠账（实测 59 份）一起恢复候选资格，「改无关提示
    不能自动洗掉欠账」这条就守不住。分流之后，无关提示改动不再移动
    `audit_prompt_version()`，审计类欠账继续排除。
    """
    from step01_script_gen.audit import audit_prompt_version
    out: list = []
    for b in (blocking or []):
        if not isinstance(b, dict):
            continue
        code = str(b.get("code") or "").strip()
        if not code:
            continue
        if code.startswith("model_audit_"):
            kind, fp = "audit", str(audit_prompt_version() or "")
        else:
            kind, fp = "rules", str(RULE_SOURCE_FINGERPRINT or "")
        out.append({"code": code,
                    "shot": str(b.get("shot_id") if b.get("shot_id") is not None else ""),
                    "field": str(b.get("field_path") or ""),
                    "crit_kind": kind, "crit_fp": fp})
    return out


def _debt_issue_in_force(rec: dict) -> bool:
    """这条欠账的判据**未变** → 仍然成立（现行判据下还会报同一条）."""
    from step01_script_gen.audit import audit_prompt_version
    kind = str(rec.get("crit_kind") or "")
    if kind == "audit":
        return str(rec.get("crit_fp") or "") == str(audit_prompt_version() or "")
    if kind == "rules":
        return str(rec.get("crit_fp") or "") == str(RULE_SOURCE_FINGERPRINT or "")
    return True          # 认不出判据 → 保守：按仍然欠账


def _record_blocked_revision(revision: str, issues=None, versions=None) -> None:
    """把 BLOCK 的 revision 记进台账（v6.115，审核）.

    为什么需要：BEST-GUARD 按**确定性**分数与风险排序，它**不知道**某份历史快照
    还欠哪些**语义**问题——实测 run 17/18 反复选回 21:55 的旧快照，而那份保留着
    Rule-7 造成的旧文本（审计每轮重报）。台账让选稿能避开已知欠账的旧稿。

    `versions`：`{算法版本号: revision}`。台账只记**主 key 的登记版本**
    （`entry_versions`），选稿侧据此按版本比对——旧版本身份只用于匹配明确属于那个
    版本的旧记录（2026-09-30 复核第 4/5 轮）。

    v6.142（09-26 P1-4）：同时记 `debt[revision]` = **该稿卷入过几次 BLOCK 的
    run**（每次登记 +1）。台账原本只是个集合——「全部快照都欠账」时整表放开、
    改按分数/风险选，于是**欠账最重的那份照样被反复选中**（P1-4 实损形态）。
    有了次数，饱和放开时就能「优先选欠账最少的一份」，且被选中者下一轮计数
    +1、自然降位——棘轮不再失效。

    2026-09-27（用户裁决 4）：同时记 `criteria[revision]` = 登记时的**判据指纹**
    （规则源 + 提示源）。判据/提示改过之后，「已知 BLOCK」不再等于「现在还会
    BLOCK」（本轮就有两条：`frame_presence_consistency` 的时态误判、缺段重复计量）
    ——没有这个字段，修好的判据也换不回那份稿的重评机会。
    """
    if not revision:
        return
    try:
        path = _blocked_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cur, debt, crit, iss, vers = [], {}, {}, {}, {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                _data = json.load(f) or {}
            cur = list(_data.get("revisions") or [])
            # 容错口径与 `_load_blocked_debt` 同一处（`_coerce_debt_map`）——
            # 一条脏计数**不得**让整张台账写不进去（那等于静默废掉护栏）
            debt = _coerce_debt_map(_data.get("debt"), cur)
            _raw_crit = _data.get("criteria")
            if isinstance(_raw_crit, dict):
                crit = {str(k): v for k, v in _raw_crit.items() if isinstance(v, dict)}
            _raw_iss = _data.get("issues")
            if isinstance(_raw_iss, dict):
                iss = {str(k): v for k, v in _raw_iss.items() if isinstance(v, list)}
            # 登记版本表（2026-09-30 复核第 5 轮 P1-2）：`{主 revision: 算法版本号}`
            _raw_ver = _data.get("entry_versions")
            if isinstance(_raw_ver, dict):
                _tmp: dict = {}
                for _k, _v in _raw_ver.items():
                    try:
                        _tmp[str(_k)] = int(_v)
                    except (TypeError, ValueError):
                        continue
                vers = _tmp
        except Exception:
            cur, debt, crit, iss, vers = [], {}, {}, {}, {}
        cur = [str(x) for x in cur if str(x) != revision]
        cur.append(str(revision))
        cur = cur[-64:]
        debt = {k: v for k, v in debt.items() if k in set(cur)}
        debt[str(revision)] = int(debt.get(str(revision), 0)) + 1
        # 2026-09-30 复核第 4/5 轮：记下**这条身份是哪个版本的算法产生的**。换算法
        # 后选稿按版本比对才能命中它（旧欠账不消失），同时不会让「同一段文字、契约
        # 不同」的另一份稿被它连坐。`debt` / `criteria` / `issues` 仍只按主 revision
        # 记一次——版本号不是一次新的欠账，不重复计数。
        vers = {k: v for k, v in vers.items() if k in set(cur)}
        from shared.quality_gate import SCRIPT_REVISION_VERSION as _rv_ver
        vers[str(revision)] = int(_rv_ver)
        # 2026-09-27（用户裁决 4）：记下**登记时的判据指纹**——下次读台账才知道
        # 这条欠账是不是在现行判据下得出的（判据变了要重新评估，不能一直排除）。
        crit = {k: v for k, v in crit.items() if k in set(cur)}
        crit[str(revision)] = _current_criteria()
        # v6.149：逐条欠账（含各自依赖的判据指纹）。**只补不覆盖**——同一条 revision
        # 多次 BLOCK 时，把新出现的条目并进去，已排除过的问题不会因覆盖而丢。
        if issues:
            _old_iss = iss.get(str(revision)) or []
            _merged, _seen = [], set()
            # v6.150（复核发现 2）：**新的在前**——同一 `(code, shot, field)` 在现行
            # 判据下再次 BLOCK 时，必须由新记录**覆盖**旧版。旧实现把旧条目排在前面，
            # 于是新记录被去重跳过、台账留着**已过期**的判据指纹，该稿下次又被判「可重评」
            # 放回候选池（正是要治的那条）。`[:40]` 截断同理——留最新的 40 条。
            for _it in list(issues) + list(_old_iss):
                if not isinstance(_it, dict):
                    continue
                _k = (str(_it.get("code")), str(_it.get("shot")), str(_it.get("field")))
                if _k in _seen:
                    continue
                _seen.add(_k)
                _merged.append(_it)
            iss[str(revision)] = _merged[:40]
        iss = {k: v for k, v in iss.items() if k in set(cur)}
        from shared.json_io import atomic_dump_json
        atomic_dump_json(path, {"revisions": cur, "debt": debt, "criteria": crit,
                                "issues": iss, "entry_versions": vers,
                                "revision_version": int(_rv_ver)},
                         ensure_ascii=False, indent=2)
    except Exception as exc:
        _logger.warn(f"[BEST-GUARD] 欠账台账写入失败（不影响结论）: {exc}")


def _coerce_debt_map(raw, revisions) -> dict:
    """把台账里的 `debt` 段规整成 `{revision: episodes}`（**读写两侧共用**）.

    容错口径只有一处，读写必须一致——否则「读出来是 1 轮、再登记一次却变回 1」
    这种自相矛盾会让棘轮计数无声漂移：
      - 段缺失 / 不是 dict（老台账）→ 表内每条记 **1 轮**（至少欠过一次；记 0 会
        让老条目在饱和放开时无条件胜出，正是 P1-4 要治的方向）；
      - 单条脏值 → 只按「1 轮」计它自己，**不牵连同表其余条目**；
      - 不在 `revisions` 表内的键丢弃（表只留最近 64 条，计数跟着表走）。
    """
    _revs = {str(x) for x in (revisions or [])}
    if not isinstance(raw, dict):
        return {r: 1 for r in _revs}
    out: dict = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            out[str(k)] = 1
    return {k: v for k, v in out.items() if k in _revs}


def _load_blocked_debt() -> dict:
    """台账里的「欠账轮次」：`{revision: episodes}`（读坏/缺失返回空 dict）.

    2026-09-27（用户裁决 4）：**不再参与选稿排序**（候选池里已没有欠账稿）。
    保留读取口径是为了**可见性**：选稿日志会打出「欠账最重的几份」，
    以及台账写侧的容错读数（`_coerce_debt_map` 读写同源）。

    老台账（只有 `revisions`、没有 `debt`）→ 一律按 1 轮计：它们至少欠过一次，
    但不知道欠了几次；按 1 计不会让任何一份凭空占优（不写 = 0 会让老条目
    无条件胜出，正是要治的那个方向）。
    """
    try:
        with open(_blocked_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return _coerce_debt_map(data.get("debt"), data.get("revisions"))
    except Exception:
        return {}


def _snapshot_new_candidate(script: dict) -> str:
    """给「BEST-GUARD 选择之前的新生成候选」留一份独立诊断快照，返回路径（失败 ""）.

    v6.123（用户口径）：此前的做法是——若 BEST-GUARD 选中历史稿，新稿只在内存里存在
    到 `script = _bg_selected` 那一刻；失败快照写的是**被选稿**，成功快照写的也是
    被选稿。于是「本次生成的质量」在事后没有任何落盘证据，而日志却宣称
    「本次 N 分版本保留在 snapshots/」。

    文件名用 `candidate_pre_bestguard_<ts>.json`（**不带** `script_result_` 前缀）：
    BEST-GUARD 的候选扫描就是按 `script_result_*.json` 找文件的，这份未定稿
    （无 `chapter_outline`、未过 hook QC）不得进入候选池。轮转在
    `_snapshot_run_artifacts()` 里按同一上限处理。
    """
    try:
        import time as _time

        snap_dir = OUTPUT_FILE.parent / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json as _adj
        _path = snap_dir / ("candidate_pre_bestguard_%s.json"
                            % _time.strftime("%Y%m%d_%H%M%S"))
        _adj(_path, script, ensure_ascii=False, indent=2)
        return str(_path)
    except Exception as _snap_e:
        try:
            _logger.warn(f"[BEST-GUARD] 新候选快照写入失败（不影响主流程）: {_snap_e}")
        except Exception:
            pass
        return ""


def _non_script_snapshot_reason(obj) -> str:
    """该 JSON 是否**不是分镜稿** → 返回跳过原因（空串 = 是稿件）.

    v6.124.2（用户 2026-09-25 决定）：BEST-GUARD 的候选必须先**验明正身**再评分。

    为什么必须挡在评分**之前**：`_candidate_quality_summary()` 在一份没有分镜的
    JSON 上查不出任何 issue，于是 `structural_ok=True`、`integrity_ok=True`、
    `blocking_risk_count=0`，`_quality_rank()` 给出 `(0, 0, 0)`——**优于任何真实
    稿件**（真稿至少带着 blocking 风险）。于是「**没有数据**」被读成「最干净」，
    这正是准则 7 的失败形态，而且落在选稿这个最关键的位置上。

        实测（2026-09-25 19:14）：审计诊断产物
        `snapshots/script_result_20260925_173311.audit.compare.json`（我的 (c)
        实验输出，非分镜稿）被选为终稿 → 公共管线审计它 → 报
        `empty_storyboard` + `missing_end_hook`（无字段可授权）→ 提交点 BLOCK。

    **刻意只校验最小结构**（用户口径：「别把旧版合法快照误挡掉」）：只要求
    顶层有非空的 `shots` 数组、元素是对象、且至少一个对象带 `index`/`prompt`。
    不校验 `timeline`/`characters` 等——那会把最小的合法稿件与旧版快照一起误挡，
    而本项目自己的最小稿件夹具就只有 `shots`（见
    `tests/step01_script_gen/test_best_guard.py::_script_with_dialogue`）。
    """
    if not isinstance(obj, dict):
        return "不是 JSON 对象"
    shots = obj.get("shots")
    if not isinstance(shots, list) or not shots:
        return "缺 shots 或 shots 为空数组"
    if not any(isinstance(s, dict) and (s.get("index") is not None or s.get("prompt"))
               for s in shots):
        return "shots 里没有可识别的镜头对象（缺 index/prompt）"
    return ""


def _cross_run_best_guard(script: dict, novel_text: str, *,
                          plan: "dict | None" = None) -> tuple:
    """跨 run 保优护栏（P4.6，2026-09-11）：防止更好的历史版本被差 run 覆盖.

    v6.103（2026-09-23 总计划阶段 4.1）：**它只选择候选，不再落盘**。

    旧实现在「新终稿已落盘」之后还可能把历史快照写回 `script_result.json`，
    并在 finalize **之后**重跑钩子质量门（可能重生 hook）——于是最终落盘的 hook
    从未被 final semantic audit 覆盖过；同时正式路径上有**两个**写入者，提交时序
    无法收敛成一个点。

    现在它返回 `(被选中的脚本 | None, 元数据)`：调用方拿到候选后仍走
    `_commit_step01_outputs()` 这一条公共管线（hook QC → finalize → revision →
    quality → final audit → 提交）。历史快照必须通过**当前版本**的质量初筛才有
    资格成为候选，但「能不能落盘」由公共管线的最终审计决定。

    Returns:
        `(selected_script, meta)`；没有更优快照时 `(None, meta)`。
    """
    meta: dict = {"selected": False, "reason": "", "new_score": None,
                  "best_score": None, "best_path": None}
    try:
        import os as _os
        from step01_script_gen.plan_feasibility import check_script_dialogue_integrity
        from shared.quality_gate import SCRIPT_REVISION_VERSION

        snap_dir = OUTPUT_FILE.parent / "snapshots"
        if not snap_dir.is_dir():
            return None, meta
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        if not snaps:
            return None, meta

        scan_n = max(1, int(_os.getenv("BEST_GUARD_SCAN_N", "8")))
        margin = float(_os.getenv("BEST_GUARD_MARGIN", "3"))
        keep_best = _os.getenv("KEEP_BEST_SNAPSHOT", "1") == "1"
        # v6.123（用户口径：候选留痕）：一旦选中历史稿，本次新生成的稿件在内存里被
        # 丢弃（`script = _bg_selected`），而**失败路径的快照保存的是被选稿**——于是
        # 「本次新稿质量如何」事后无从评价。旧文案还宣称「本次 N 分版本保留在
        # snapshots/」，实际上这一步什么都没写（日志与事实不符）。
        # 在**选择之前**真写一份独立诊断快照，并把真实路径带进 meta 供日志引用。
        # 文件名**不用** `script_result_` 前缀：那是候选扫描的口径，而这份是
        # **未定稿**（没有 chapter_outline、没过 hook QC），不得进入候选池。
        if keep_best:
            meta["new_candidate_snapshot"] = _snapshot_new_candidate(script)

        new_score = _score_script(script, novel_text)["total"]
        new_quality = _candidate_quality_summary(script, novel_text)
        new_rank = _quality_rank(new_score, new_quality)
        meta.update({"new_score": new_score,
                     "new_risk": new_quality.get("blocking_risk_count")})

        # 本次稿件的语义 revision——用它排除「就是当前这一版」的快照。
        # 旧实现靠「snaps[:-1] 排除最新一份」来排除本次快照；那隐含假设「最新
        # 快照刚由本次写入」。选择点提前到落盘**之前**后该假设不再成立（最新快照
        # 是上一次 run 的，是合法候选），所以改成按 revision 排除。
        # 2026-09-30 复核第 4/5 轮：「是不是同一份稿」只比**现行版本**的完整身份；
        # 「是不是已知欠账」按**登记版本**逐版比对（旧版本身份只匹配明确属于那个
        # 版本的旧记录）。两处都不能把各版本混成一个等价关系——「同一段文字、契约
        # 不同」的两份稿 v2 不同而 v1 相同，混用会让好稿被旧欠账连坐。
        _cur_aliases = _revision_aliases_or_empty(script)
        if not _cur_aliases:
            # 「算不出身份」既不能证明快照不是当前这一版，也不能证明它不欠账——
            # 显式不选稿（准则 7），不把无法核验的东西放进候选池。
            meta["reason"] = "revision_uncomputable"
            _logger.warn("[BEST-GUARD] 本次终稿 revision 别名无法计算 → 本轮不做跨 run 选稿")
            return None, meta
        _blocked_by_ver = _load_blocked_revisions()
        _blocked_names: list = []

        # v6.124.2：非分镜稿**不进候选池**（见 `_non_script_snapshot_reason`）
        _invalid: list = []
        # v6.135（审核约束①）：历史快照也按**本轮计划**执行台词完整性门——快照生成时
        # 的计划与本轮不同，它没保住本轮选材 = 进提交门后必然死锁（09-26 实损链）。
        # 门**不参与**下方"全欠账放宽"（那是欠账台账的例外，不是完整性门的）。
        _dlg_broken_snaps: list = []
        _stale_plan_snaps: list = []

        def _plan_snapshot_reason(old: dict) -> str:
            """快照的 `plan_refs` 是不是**对本轮计划**作的声明（2026-09-27 复核 P0-2）.

            带身份且与本轮计划不符 → 排除（它的编号指的不是同一条节拍；只查编号范围
            会静默放行——`insert_after`/`split` 会重排 B 编号）。**无身份**的老快照按
            既定兼容政策继续参与排名，但**不算**「通过了本轮事件契约」（提交门的语义
            审计仍是它的兜底）。
            """
            if plan is None:
                return ""
            # 就地 import：本闭包所在的 BEST-GUARD 函数与候选门的 import 块**不在同一
            # 作用域**（实跑被 `test_best_guard_excludes_broken_snapshot...` 抓到：
            # NameError 被外围的「护栏失败不影响主流程」吞掉，身份门**静默失效**）。
            from step01_script_gen.plan_feasibility import plan_identity_mismatch
            return plan_identity_mismatch(old, plan)

        def _dlg_snapshot_reason(old: dict) -> str:
            if plan is None:
                return ""
            try:
                _ok, _broken = check_script_dialogue_integrity(old, plan)
            except Exception as _e:
                # v6.135 四审 P1：门自身异常 → 该快照**无法证明台词完整** → 排除出
                # 候选池（fail-closed：宁可不选，不可选中无法验证的快照），原因留痕
                return "gate_error: %s" % _e
            if _ok:
                return ""
            return "%d 句破损，例：%s（%s）" % (
                len(_broken), str((_broken or [{}])[0].get("line") or "")[:30],
                (_broken or [{}])[0].get("reason"))

        candidates = []
        for sp in snaps:
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    old = json.load(f)
            except Exception as _e:
                _invalid.append(f"{sp.name}（JSON 解析失败：{_e}）")
                continue
            _why = _non_script_snapshot_reason(old)
            if _why:
                _invalid.append(f"{sp.name}（{_why}）")
                continue
            if not HOOK_ENABLED:
                old.pop("hook", None)
            _old_aliases = _revision_aliases_or_empty(old)
            if not _old_aliases:
                # 同上：快照身份不可核 → 本次不具候选资格，原因留痕（而不是当作
                # 「不是当前这一版、也不欠账」默默放行）。
                _invalid.append(f"{sp.name}（revision 别名无法计算：身份不可核）")
                continue
            # 就是当前这一版：只比**现行版本**的完整身份（复核第 5 轮 P1-2）。
            if _old_aliases.get(SCRIPT_REVISION_VERSION) == _cur_aliases.get(
                    SCRIPT_REVISION_VERSION):
                continue
            # v6.115（审核）：跳过**已知语义欠账**的旧稿——它们确定性分数可能很高
            # （所以此前总被选中），却带着审计反复报的旧问题。
            # 2026-09-30 第 5 轮：**逐版本**比对——台账条目只按其登记版本的身份命中
            # （v1 记录只认 v1 身份，v2 记录只认 v2 身份），跨版本不算等价。
            if any(_old_aliases.get(_v) in (_blocked_by_ver.get(_v) or ())
                   for _v in _old_aliases):
                _blocked_names.append(sp.name)
                continue
            _dlg_why = _dlg_snapshot_reason(old)
            if _dlg_why:
                _dlg_broken_snaps.append(f"{sp.name}（{_dlg_why}）")
                continue
            _plan_why = _plan_snapshot_reason(old)
            if _plan_why:
                _stale_plan_snaps.append(f"{sp.name}（{_plan_why}）")
                continue
            candidates.append((sp, old))
        # 诊断清单去重（`invalid` / 台词破损 / 计划不符各自只扫一遍，但同一份快照
        # 可能同时命中多条）——准则 7 要的是**准确**的可见性，不是更大的数字。
        _dlg_broken_snaps = list(dict.fromkeys(_dlg_broken_snaps))
        _stale_plan_snaps = list(dict.fromkeys(_stale_plan_snaps))
        if _dlg_broken_snaps:
            # 显式记录（准则 7）：哪几份快照因台词破损被排除，为什么
            _logger.warn(
                "[BEST-GUARD] 台词完整性门排除 %d 份历史快照（未过本轮计划的选材门，"
                "不参与评分）：%s"
                % (len(_dlg_broken_snaps), _dlg_broken_snaps[:6])
            )
            meta["dialogue_broken_snapshots"] = _dlg_broken_snaps
        if _stale_plan_snaps:
            # 显式记录（准则 7 / 复核 P0-2）：哪几份快照因**计划身份不符**被排除。
            # 这些快照的 plan_refs 是对另一版计划作的声明，编号即使仍在范围内也指向
            # 别的节拍（`insert_after`/`split` 会重排 B 编号）。
            _logger.warn(
                "[BEST-GUARD] 计划身份不符，排除 %d 份历史快照（它们的 plan_refs 不是"
                "对本轮计划作的声明，不参与评分）：%s"
                % (len(_stale_plan_snaps), _stale_plan_snaps[:6])
            )
            meta["stale_plan_snapshots"] = _stale_plan_snaps
        if _invalid:
            # 显式记录（准则 7）：跳过的原因必须能看见，否则"为什么没选历史稿"无从归因
            _logger.warn(
                "[BEST-GUARD] 跳过 %d 份**非分镜稿**的 JSON（不评分、不参与选稿；"
                "常见原因是审计/诊断产物落进了 snapshots/ 候选目录）：%s"
                % (len(_invalid), _invalid[:6])
            )
            meta["invalid_candidates"] = _invalid
        # ------------------------------------------------------------------
        # 2026-09-27（用户裁决 4）：**欠账稿不再放回候选池**。
        #
        # 旧行为是「全都有欠账 → 放开限制，按欠账轮次升序选一份」。实测（08:11
        # run）：全部 20 份快照都在台账内 → 选中上一轮刚 BLOCK 的 `072416`
        # （风险 2 只是**确定性**风险，欠的语义问题不在这个数里）→ 重跑同一套审计、
        # 同样的 4 条 blocking、`resolved=0` → 再 BLOCK，并把它自己的新 revision
        # 也写进台账。每轮原地空转：既拿不到新稿的检验结果，又把「确定性风险低」
        # 读成了「已能提交」。
        #
        # 现在的语义：**已 BLOCK 且判据/修复契约未变**的稿不再作为「最佳」稿被重复
        # 修复；没有合格历史候选时如实返回 None——本轮新稿照常进提交门（门仍可
        # BLOCK，正式产物仍受保护：不过门只写 BLOCK manifest）。
        #
        # 判据变过的欠账稿不在此列：`_load_blocked_revisions()` 只把**指纹相符**的
        # 条目当欠账（旧结论可能正是被修掉的那条判据得出的）。
        if not candidates and _blocked_names:
            _debt_map = _load_blocked_debt()
            _owed = sorted(
                ((int(_debt_map.get(r, 1)), r)
                 for _revs in _blocked_by_ver.values() for r in _revs),
                reverse=True)[:5]
            _logger.warn(
                "[BEST-GUARD] 全部历史快照都在欠账台账内（%d 份，判据指纹未变）"
                "——本轮**不选历史稿**，让新稿直接进提交门检验；"
                "欠账最重的几份：%s（正式产物仍受保护）"
                % (len(_blocked_names),
                   "、".join(f"{r[:12]}×{n}" for n, r in _owed)))
            meta["reason"] = "all_snapshots_owed"
            meta["blocked_snapshots"] = _blocked_names
            return None, meta
        candidates = candidates[-scan_n:]

        best_path = None
        best_score = -1.0
        best_quality = None
        best_rank = None
        best_script = None
        _prefilter_rejected: list[str] = []
        for sp, old in candidates:
            try:
                sc = _score_script(old, novel_text)["total"]
                q = _candidate_quality_summary(old, novel_text)
                rank = _quality_rank(sc, q)
            except Exception:
                continue
            # 初筛是候选资格，不是冠军资格。先排除不合格快照再排名，
            # 否则第一名被拒后，同一窗口内的次优合格快照永远得不到机会。
            _new_risk = new_quality.get("blocking_risk_count")
            _old_risk = q.get("blocking_risk_count")
            # v6.149（复核根因 3）：候选资格初筛**用与提交点同源的确定性判据**。
            # 此前初筛只看质量门的风险计数，而**跨字段**检查是另一个来源——实测
            # 2026-09-29：快照 `script_result_20260929_053150.json`（它本身就是同一天
            # 早先被判死的输出）以「风险1」入选，一进提交点就带 `result_action_support`
            # 被判 blocking，于是反复被选中、反复 BLOCK（59 份欠账重评也没拦住它）。
            # 全部快照都不合格时 BEST-GUARD 返回 None → 按既有路径改选新稿进提交门。
            _cf_blocking = _snapshot_cross_field_blocking(old)
            if _cf_blocking is None:
                _prefilter_rejected.append(f"{sp.name}（跨字段校验器异常，未能校验）")
                continue
            if _cf_blocking:
                _prefilter_rejected.append(
                    "%s（跨字段 blocking %d 条，例：%s）"
                    % (sp.name, len(_cf_blocking),
                       str(getattr(_cf_blocking[0], "code", "?"))))
                continue
            if (q.get("quality_checked") is not True
                    or rank[0] > 0 or rank[1] > 0
                    or (_new_risk is not None and _old_risk is not None
                        and int(_old_risk) > int(_new_risk))):
                _prefilter_rejected.append(
                    f"{sp.name}（风险{_old_risk}，结构={q.get('structural_ok')}，"
                    f"完整性={q.get('integrity_ok')}）")
                continue
            # v6.73: compare integrity -> blocking risks -> total, not only
            # the raw story score.  A clean 90 must not lose to a corrupted 100.
            # 2026-09-27（用户裁决 4）：欠账轮次不再参与排序——候选池里已经没有
            # 欠账稿（它们不再被放回），那道次键恒为 0，留着只会误导读者。
            _key = rank
            if best_rank is None or _key < best_rank:
                best_score = sc
                best_quality = q
                best_rank = rank
                best_path = sp
                best_script = old

        if best_path is None:
            if _prefilter_rejected:
                # v6.152（C5）：清单**打全**——此前 `[:6]` 截断，事后无法复核
                # 到底哪几份被拒（run 01 就因此答不出 081241 是否在列）。
                _logger.warn("[BEST-GUARD] %d 份快照未通过当前质量初筛：%s"
                             % (len(_prefilter_rejected), _prefilter_rejected))
                meta["prefilter_rejected_snapshots"] = _prefilter_rejected
                meta["reason"] = "snapshot_failed_prefilter"
            return None, meta
        if _prefilter_rejected:
            _logger.warn("[BEST-GUARD] %d 份快照未通过当前质量初筛：%s"
                         % (len(_prefilter_rejected), _prefilter_rejected))
            meta["prefilter_rejected_snapshots"] = _prefilter_rejected
        meta.update({"best_score": best_score, "best_path": str(best_path),
                     "best_risk": (best_quality or {}).get("blocking_risk_count")})

        _same_risk_tier = (
            best_rank[:3] == new_rank[:3]
        )
        # 09-26 P1：**新稿胜出也要留日志**。此前只有「快照胜出」有两行 WARN，
        # 新稿胜出（同档在 margin 内 / 新稿风险档不更差）静默 return——20:49 run
        # 日志里只能看到 BEST-GUARD 存在，看不出快照为什么失利，稳定版与新版本的
        # 取舍无法复核。这里补一行对称 WARN（含 best/new 分数与风险、tier、margin）。
        def _log_new_kept(_reason: str):
            _logger.warn(
                "[BEST-GUARD] 保留新终稿（" + _reason + "）：本次 "
                + str(new_score) + " 分/风险"
                + str(new_quality.get("blocking_risk_count"))
                + " vs 快照最优 " + str(best_score) + " 分/风险"
                + str((best_quality or {}).get("blocking_risk_count"))
                + "（" + best_path.name + "，tier "
                + str(tuple(best_rank)) + " vs " + str(tuple(new_rank))
                + "，margin=" + str(margin) + "）"
                + "——快照未胜出，不替换"
            )

        if _same_risk_tier:
            if best_score <= new_score + margin:
                meta["reason"] = "same_tier_within_margin"
                _log_new_kept("同风险档，快照分未超出新稿 + margin")
                return None, meta
        else:
            # Prefer the safer snapshot whenever its integrity/risk tier is
            # strictly better than the new run, regardless of raw score.
            if not (best_rank[:3] < new_rank[:3]):
                meta["reason"] = "new_tier_not_worse"
                _log_new_kept("新稿结构/完整性/风险档不更差")
                return None, meta

        _logger.warn(
            "[BEST-GUARD] 快照综合排序胜出：新终稿 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + "；快照最优 "
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "（" + best_path.name + "）"
        )
        if not keep_best:
            _logger.warn("[BEST-GUARD] KEEP_BEST_SNAPSHOT=0 → 仅告警，保留新终稿")
            meta["reason"] = "keep_best_disabled"
            return None, meta

        # v6.123：文案必须与事实一致——真写了才说「已留档 <路径>」，没写成要说没写成。
        _new_snap = str(meta.get("new_candidate_snapshot") or "")
        _logger.warn(
            "[BEST-GUARD] 选中历史快照作为候选（" + str(best_score) + " 分/风险"
            + str(best_quality.get("blocking_risk_count")) + "）；本次 "
            + str(new_score) + " 分版本"
            + ("已留档 " + _new_snap if _new_snap
               else "**快照写入失败，未留档**（见上方 WARN）")
            + "。候选仍走公共提交管线（hook QC → finalize → quality → final audit）"
        )
        _console(
            "  [BEST-GUARD] 本次 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + "，快照综合排序更优："
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "，已**选中**历史最优版为候选（仍须通过公共提交与最终审计）"
        )
        meta["selected"] = True
        meta["reason"] = "snapshot_strictly_better"
        # v6.129（用户 2026-09-25 根因）：**在选稿边界**把快照**自身**的 revision
        # 一并带出去。它在选稿时成立，但主流程随后会用**本轮大纲**覆盖
        # `script["chapter_outline"]`，而 `chapter_outline` 参与 revision 哈希
        # （`shared/quality_gate._stable_payload`）→ 分镜一字未改，进入提交点时
        # revision 已经变成另一个值（实测 f434e509→380da4c1→0fe1f2b2）。
        # 台账若只记后两者，下次 guard 仍拿快照自身的 revision 查台账 → 漏拦、
        # 同一份欠账稿被反复选中（这就是旧稿反复入选的完整触发链）。
        meta["selected_path"] = str(best_path)
        # 版本字典一并带出去：提交点 BLOCK 时台账要把**主 key 的登记版本**记上，
        # 否则换算法之后这份欠账又查不到了（2026-09-30 复核第 4/5 轮）。
        _sel_aliases = _revision_aliases_or_empty(best_script)
        meta["selected_revision"] = str(_sel_aliases.get(SCRIPT_REVISION_VERSION) or "")
        meta["selected_revision_aliases"] = _sel_aliases
        if not meta["selected_revision"]:
            _logger.warn("[BEST-GUARD] 快照自身 revision 计算失败"
                         "（欠账台账可能漏一条）")
        return best_script, meta
    except Exception as _bg_e:
        try:
            _logger.warn(f"[BEST-GUARD] 护栏执行失败（不影响主流程）: {_bg_e}")
        except Exception:
            pass
        meta["reason"] = f"error: {_bg_e}"
        return None, meta


def _fatal_summary(exc, tb: str) -> str:
    """失败摘要的**单行完整**形态（复核 2026-10-04）.

    `tb.strip().splitlines()[-1]` 在异常消息**含换行**时只拿到最后**一行片断**——计划层的
    停止原因恰好是多行的（问题清单逐条列全），于是控制台上只剩半句：

        [FATAL] 2) F8 声明由 B8 的**画面**承载，但该节拍标的是「载体：台词2」——一条节拍）——额度已耗尽；这是**响应违约**（模型没

    前半截（"连续 2 次响应被拒收…1) F6 …"）在别的行上，读失败摘要的人看不到。改成从
    **异常对象**取完整消息并压掉换行——与 `shared.error_fmt` 的单行打印约定一致。
    """
    if exc is not None:
        _text = "%s: %s" % (type(exc).__name__, exc)
    else:
        _tb = (tb or "").strip()
        _text = _tb.splitlines()[-1] if _tb else ""
    return " ".join(str(_text).split())


def _presentation_mode_argv(argv=None) -> str:
    """早期解析 `--presentation-mode`（**不用 argparse**：本模块没有顶层 parser，
    且必须能在任何配置/日志求值之前跑完）.

    只认这一个参数；其余 argv 原样忽略（Step01 由上层 runner 传参）。非法值抛
    `ValueError` —— 由 `main()` 的调用方转成退出码 2，**不在生成侧才发现**。
    """
    import sys as _sys
    _av = list(_sys.argv[1:] if argv is None else argv)
    _flag = "--presentation-mode"
    if _flag not in _av:
        return ""
    _i = _av.index(_flag)
    if _i + 1 >= len(_av):
        raise ValueError("--presentation-mode 缺少取值（legacy_audio|screen_hybrid）")
    return _av[_i + 1]


def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存.

    控制台静默策略：运行期间所有 print()（校验 WARN、LLM 推理过程等）
    重定向到 step01.log，控制台只显示关键进度和最终摘要。

    **早期 CLI**（主专题 §6.3）：`--presentation-mode legacy_audio|screen_hybrid`，
    **缺省仍是 `legacy_audio`**。在拿锁、建目录、算缓存键**之前**解析并写入运行身份——
    模式进计划/缓存身份（`presentation.mode` + `plan_contract_version`），下游 Step03/04
    从**经提交验证的稿件**读它，不在下游另传开关改稿。非法模式在启动期就退出（码 2），
    不走到生成侧才发现。
    """
    from step01_script_gen.outline import presentation_mode

    _cli_mode = presentation_mode(_presentation_mode_argv())
    globals()["PRESENTATION_MODE"] = _cli_mode
    os.environ["STEP01_PRESENTATION_MODE"] = _cli_mode

    _logger.separator()
    _logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _logger.info("  新增: 钩子分镜 + 敏感词校验")
    _logger.separator()

    # v6.70.2：章节级 single-flight 锁——禁止两个 step01 并发写同一章
    # （09-14 实测两个 Python 进程交错写 step01.log/script_result.json）。
    if not _acquire_step01_lock():
        return

    _timing_start()  # v6.70.3: 阶段计时起点（拿锁成功 = 本 run 独占计时器）
    # 每次模型调用记一条**带 run_id** 的台账（2026-10-02 复核：metrics.calls 全空、
    # 日志里也没有 run_id，事后无法对账）。落点：章节目录的 step01_calls.jsonl。
    try:
        from shared.call_ledger import set_ledger_path
        set_ledger_path(CHAPTER_DIR / "step01_calls.jsonl")
    except Exception:
        pass

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
        # v6.115（审核）：缓存指纹此前只取章节**前 1000 字**——后文改动不会改变
        # 指纹，于是「原文变了但缓存命中」→ 用旧稿当新稿用。指纹必须覆盖全部输入。
        novel_source_text = novel_text

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
        # 工作包 §4 D / §7.4：**整次运行唯一的绝对截止时刻**在这里算一次，之后
        # `extract_all_concurrent` → `generate_outline_result` → 修订控制器 → 生成前门
        # 全部用它；任何一层都不许再算 `now + cap`（`_planning_deadline_at` 只在此处调用）。
        _deadline_monotonic = _planning_deadline_at()
        # 结构化大纲结果（§7.2）：未启用大纲提取时为 None；失败原因从它读，不再丢成 None。
        _outline_result = None
        if CHARACTER_EXTRACTION_ENABLED or SCENE_OBJECT_EXTRACTION_ENABLED or OUTLINE_ENABLED:
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
                outline_cache_path=CHAPTER_DIR / "chapter_outline.cache.json",
                outline_deadline_monotonic=_deadline_monotonic,
                log_func=_logger.info,
            )
            # === 角色：注册到 bank（主流程后段仍可复用） ===
            # `None` = **这次没产出**（调用/解析失败）⇒ **不写库、不报"已保存"**
            # （2026-10-02 日志复核：鉴权失败那轮把 0 条写进库并报成功）。
            # `[]` = 真的没有角色 ⇒ 正常保存（内容不变，仍留一次来源 run_id）。
            chars = pre_results.get("characters")
            if chars is None:
                _logger.warn("[WARN] 角色预提取未产出（不写库；既有角色库原样保留）")
            else:
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
                # 来源=**这一轮确实发生**的调用（从台账取，复核之七）：条目上记
                # `last_call_ids`（最近若干次）+ `last_source`，不编造模型调用；
                # 写盘**成功**才说"已保存"（旧写法无条件报成功）。
                from shared import call_ledger as _cl
                _ok = character_bank.save(
                    run_id=_step01_run_id(),
                    call_ids=_cl.call_ids_for(schema="characters"))
                if _ok:
                    _logger.info(f"[INFO] 预提取到 {len(chars)} 个角色特征（已保存到角色库）")
                else:
                    _logger.warn("[WARN] 角色库**未写入**（保存失败，盘上原样保留）——"
                                 "本次预提取结果未留档")
            # === 场景物体：注册到 bank ===
            objs = pre_results.get("scene_objects")
            if objs is None:
                _logger.warn("[WARN] 场景物体预提取未产出（不写库；既有物体库原样保留）")
            else:
                for o in objs:
                    scene_objects_bank.register_object(o, NOVEL_CHAPTER)
                from shared import call_ledger as _cl
                _ok = scene_objects_bank.save(
                    run_id=_step01_run_id(),
                    call_ids=_cl.call_ids_for(schema="scene_objects"))
                if _ok:
                    _logger.info(f"[INFO] 预提取到 {len(objs)} 个场景物体（已保存到场景物体库）")
                else:
                    _logger.warn("[WARN] 场景物体库**未写入**（保存失败，盘上原样保留）"
                                 "——本次预提取结果未留档")
            # === 大纲：预提取，主分镜生成时注入 prompt（**结构化结果**，§7.2） ===
            _outline_result = pre_results.get("outline")
            if isinstance(_outline_result, dict) and _outline_result.get("executable"):
                pre_extracted_outline = _outline_result.get("plan")
            # `executable=False` **不在这里停**（§7.6）：缓存命中路径不需要计划，停点放在
            # 真正要用它的生成入口——见下方 `_stop_if_outline_unavailable` 的调用。

        # 检查缓存（场景物体库内容变化也视为源变更，v6.6）
        objects_bank_data = json.dumps(
            scene_objects_bank._data, ensure_ascii=False, sort_keys=True
        )
        prompt_changed = _prompt_changed(
            Path(__file__).parent / "prompts.py", SCRIPT_FILE, novel_source_text, NOVEL_CHAPTER,
            objects_bank_data, rule_version=RULE_VERSION,
        )

        if not prompt_changed:
            _logger.info(f"[CACHE] 脚本已缓存: {SCRIPT_FILE}")
            with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
                script = json.load(f)
            if not HOOK_ENABLED:
                script.pop("hook", None)
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
            # v6.103：finalize 由唯一提交点统一执行（此处不再重复跑一遍，
            # 避免同一个 run 里出现两个「最后一次时间写入者」）。
            # C5 修复（08-11）：fps 一致性校验（timeline 由提交点的 finalize 产出）
            # v6.62.3：必须晚于 hard_fix —— runtime_extend 会改 duration_sec，
            # 早于 hard_fix 校验会拿到过期时长（与非缓存路径同序）。
            _check_fps_consistency(script)
            # v6.103（总计划阶段 4）：缓存命中路径此前**只写 script_result.json**
            # ——不产 quality、不产 final audit、不写 commit manifest，于是「三份
            # 产物同 revision」在缓存路径上从来不成立，下游 `require_semantic_gate`
            # 必然拿到过期或缺失的审计。现在与正常生成路径共用唯一提交点。
            # 钩子禁止台词清单：正常路径由钩子生成分支采集（在缓存分支**之后**的
            # 代码里赋值）——缓存命中时那条分支根本不会执行，直接引用会
            # UnboundLocalError。这里就地按同一口径重建（shots 已在缓存里）。
            _cached_forbidden: list = []
            for _cs in script.get("shots") or []:
                _cb = _speech_body(_cs.get("narration_segment", ""))
                if _cb:
                    _cached_forbidden.append(_cb)
            if pre_extracted_outline:
                script["chapter_outline"] = pre_extracted_outline
            script, _commit_summary = _commit_step01_outputs(
                script, novel_text,
                plan=script.get("chapter_outline") or {},
                hook_ctx={"bank": character_bank,
                          "objects_bank": scene_objects_bank,
                          "forbidden_lines": _cached_forbidden,
                          "producer_model": None},
                audit_model=_candidate_model_for_audit(),
                label="缓存命中提交")
            # C1：**两处提交返回走同一个投影**——提交 BLOCK 必须落到运行终局，
            # 缓存路径此前只打了一行 console，metrics/timing 仍报 ok。
            _project_commit_outcome(_commit_summary)
            _timing_mark("commit", status=_commit_summary.get("status"))
            _snapshot_run_artifacts(script)
            _logger.info("时间轴已重新计算")
            # 缓存命中：恢复 stdout 并显示最终摘要
            sys.stdout = _REAL_STDOUT
            _logger.console_silent = False
            shots = script.get("shots", [])
            _console("")
            _console("=" * 56)
            _console("  [步骤1] 分镜脚本摘要（缓存命中，未重新生成）"
                     if _commit_summary.get("status") == "COMMITTED"
                     else "  [步骤1] ✗ BLOCK —— 正式产物未更新（缓存命中路径）")
            _console("=" * 56)
            if _commit_summary.get("status") != "COMMITTED":
                _console(f"  [BLOCK] {_commit_summary.get('detail')}")
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
        # v6.132（用户 2026-09-25）：生成执行契约（不压缩）——四个完整稿生成入口共用。
        # 在这里算：计划已定稿（缓存命中或重选都已完成），后续 GAP/收敛轮直接复用。
        # 工作包 §4 D / §7.6：真正要用计划时才判"能不能下发"——缓存命中路径已在上面
        # 提前返回，不受影响；这里停下 ⇒ **零次候选生成**，且失败原因保真到 main。
        _stop_if_outline_unavailable(_outline_result, novel_text, _deadline_monotonic)

        _contract = render_execution_contract(pre_extracted_outline)
        _logger.info("[INFO] 生成执行契约 %d 字符（选用台词 %d 句）"
                     % (len(_contract),
                        len((pre_extracted_outline or {}).get("dialogue_lines") or [])))
        _candidates = _generate_script_candidates(
            novel_text, character_bank, scene_objects_bank, pre_extracted_outline,
            deadline_monotonic=_deadline_monotonic,
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
                # v6.132（用户 2026-09-25）：最终组装（锚点 → 反馈 → **契约在末尾**）
                # 抽成 `_build_gap_regen_prompt()`——"块在提示里"不等于"块在末尾"，
                # 抽出来才能对**最终请求**断言末尾位置。
                prompt, _gap_system = _build_gap_regen_prompt(
                    novel_text, character_bank, scene_objects_bank,
                    pre_extracted_outline, _contract, gap_context)

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
                # v6.157（2026-09-30 复核第 4 轮「重生成身份维护」）：GAP 重生成
                # 也是**整份替换**——采纳前绑定本轮计划身份并核完整契约，不合格保留
                # 上一版（此前这里只判「质量退化」，契约与身份都不在判据里）。
                _id_why = _bind_regen_plan_identity(script, new_script,
                                                    plan=pre_extracted_outline)
                if _id_why:
                    _logger.warn("[GAP] 重生成稿未保住计划身份/契约：%s，回退保留上一版"
                                 % "；".join(_id_why[:2]))
                    _console("  [GAP] 重生成稿未保住计划身份/契约，回退保留上一版")
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
            from step01_script_gen.outline import outline_to_anchor as _o2a
            script = _regen_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                # v6.131：与 GAP 重生/结构收敛同源——带上大纲锚点（含核验过的选用台词）
                outline_anchor=_o2a(pre_extracted_outline, novel_text),
                contract_block=_contract,
                # 2026-09-30 复核第 5 轮 P1-1：**本请求的权威计划**显式交给采纳入口
                # （不能让它去基线里翻 `chapter_outline`——候选阶段它还没写入）
                plan=pre_extracted_outline,
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
            from step01_script_gen.outline import outline_to_anchor as _o2a
            # v6.128：结构收敛轮的道具候选短提示同样默认关闭（用户 2026-09-25 决定）
            from step01_script_gen.prop_state_anchor import (
                prop_anchor_block_for as _pabf)
            script = _regen_structural_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                outline_anchor=_o2a(pre_extracted_outline, novel_text),
                prop_anchor_block=_pabf(pre_extracted_outline),
                contract_block=_contract,
                plan=pre_extracted_outline,      # 同上（复核第 5 轮 P1-1）
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
                _allow, _deadline, _to_info = _audit_budget_allowance()
                _logger.info("[AUDIT] 本次审计可用 %ss（%s）｜总预算剩余 %s 秒"
                             % (_allow, _to_info.get("why") or "-",
                                _to_info.get("left") if _to_info.get("left") is not None
                                else "未知"))
                _audit_result = run_same_model_audit_with_status(
                    novel_text, pre_extracted_outline, script,
                    model=_repair_model or _producer_model,
                    timeout_sec=int(_to_info.get("cap") or AUDIT_TIMEOUT_CAP_SEC),
                    deadline_ts=_deadline,
                )
                _audit_issues = _audit_result.issues
                _metrics_set("audit.status", _audit_result.status)
                _metrics_set("audit.issues", len(_audit_issues))
                # v6.112：中间轮审计也进同一个台账（phase 区分），
                # 否则「修复轮之前那一次审计说了什么」仍然无处可查。
                _record_audit_diag(
                    {"status": _audit_result.status, "error": _audit_result.error,
                     # v6.117（方案 A）：中间轮审计与提交轮走同一个解析器，可能同样走
                     # 专用通道——台账必须记实际通道/模型，否则同一份台账里两个 phase
                     # 的 provider 归属口径不一致。
                     "model": (getattr(_audit_result, "channel_model", "") or ""
                               or _repair_model or _producer_model),
                     "channel_provider": getattr(_audit_result, "channel_provider", "") or "",
                     "schema_deviations": [str(d) for d in (
                         getattr(_audit_result, "schema_deviations", None) or [])],
                     "usage": getattr(_audit_result, "usage", None),
                     "issues": [i.to_dict() for i in _audit_issues]},
                    phase="mid_pipeline", round_index=0, script=script)
                try:
                    from shared.json_io import atomic_dump_json as _audit_dump
                    # v6.103：中间轮的 advisory 审计**不得**写正式
                    # `step01_audit.json`——那是唯一提交点的产物，被这里覆盖会
                    # ① 让「提交点之外的第二个写入者」重新出现；
                    # ② 破坏 final audit 的复用判据（revision/scope 都对不上）。
                    _audit_dump(
                        CHAPTER_DIR / "step01_audit.advisory.json",
                        {
                            "status": _audit_result.status,
                            # 与提交轮同一口径：记**实际提交**的模型/通道
                            "model": (getattr(_audit_result, "channel_model", "") or ""
                                      or _repair_model or _producer_model),
                            "channel_provider": getattr(_audit_result, "channel_provider", "") or "",
                            "usage": getattr(_audit_result, "usage", None),
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
                # 2026-10-02：提取器把"没产出"（调用/解析失败）与"真的没有角色"分开
                # ——前者返回 `None`，这里同样按失败回退（旧实现两者都是 `[]`，
                # 于是 `script["characters"]` 会被写成 None / 记成"0 个"）。
                try:
                    chars = extract_characters(novel_text, character_bank)
                    if chars is None:
                        _logger.warn(
                            "[WARN] 角色提取未产出（调用/解析失败）——回退使用脚本内已有 characters")
                        chars = script.get("characters", []) or []
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
            str(Path(__file__).parent / "prompts.py"), novel_source_text, NOVEL_CHAPTER,
            objects_bank_data, rule_version=RULE_VERSION,
        )

        # v6.50（08-29）：保存前重算 shot_count——timecode pre-pass 拆镜发生在
        # parse 期校正之后，元数据会落后实际镜头数（15:12 run 16 vs 20 实测）
        script["shot_count"] = len(script.get("shots", []))

        # v6.70.1（09-14）：BEST-GUARD 可能选中历史快照——v6.103 起它**只选候选**，
        # 不再落盘、不再 finalize、不再跑钩子门；被选中的快照与本次终稿走**同一条**
        # 公共提交管线（hook QC → finalize → revision → quality → final audit）。
        if not HOOK_ENABLED:
            script.pop("hook", None)
        # v6.135（工作包 sunny-kindling-wilkinson，审核约束③时序）：**终稿台词完整性门**
        # ——盖在定向拆分（`_scoped_split_over_cap_shots` 会重排 narration）与
        # finalize **之后**、BEST-GUARD 之前：这一版才是实际送入选稿/提交门的最终形态。
        # 09-26 02:30 实损证明碎片可能由拆分层产生（候选层单镜完整、拆后句中截断），
        # 只查候选会漏。破损 ⇒ 显式失败（决策点 1），不静默落回历史快照。
        from step01_script_gen.plan_feasibility import (DialogueGateError,
                                                        DialogueIncompleteError,
                                                        check_script_dialogue_integrity,
                                                        dump_dialogue_incomplete,
                                                        plan_dialogue_lines)
        # v6.156（2026-09-30 复核第 5 组）：事件履约契约**同源复核**——拆分层可能
        # 让 plan_refs 的节拍/台词关系断开（覆盖门与台词门各自都能过），这里与台词门
        # 同位置、同 fail-closed 口径再核一次。
        try:
            from step01_script_gen.plan_feasibility import (EventContractBrokenError,
                                                           check_event_contract)
            # 同上（P1-1）：拆分/finalize 之后的终稿层同样要求逐镜声明
            _ct_ok, _ct_viol, _ = check_event_contract(
                script, pre_extracted_outline, require_declaration=True)
        except Exception as _e:
            _ct_ok = False
            _ct_viol = [{"code": "gate_error", "shot": "", "detail": str(_e)}]
            _logger.error(f"[CANDIDATE] 事件履约契约门异常（终稿层，fail-closed）：{_e}")
        if not _ct_ok:
            _logger.error(
                "[CANDIDATE] event contract failed（终稿层，拆分/finalize 后）："
                + "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                          str(v.get("detail"))[:60])
                            for v in _ct_viol[:3]))
            raise EventContractBrokenError(_ct_viol)
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(script, pre_extracted_outline)
        except Exception as _e:
            # v6.135 四审 P1：门自身异常 = 无法证明台词完整 → fail-closed 显式 BLOCK
            # （独立诊断），不裸崩、不退回原样匹配放行
            _dlg_ok = False
            _dlg_broken = [{"line": "", "reason": "gate_error: %s" % _e}]
            _logger.error(f"[CANDIDATE] 台词门异常（终稿层，fail-closed BLOCK）：{_e}")
        if not _dlg_ok:
            _dlg_total = len(plan_dialogue_lines(pre_extracted_outline))
            dump_dialogue_incomplete(CHAPTER_DIR, [_dlg_broken], total_lines=_dlg_total,
                                     layer="终稿层", plan=pre_extracted_outline)
            _dlg_gate_err = str(_dlg_broken[0].get("reason") or "").startswith("gate_error")
            _logger.error(
                "[CANDIDATE] dialogue integrity failed（终稿台词完整性门，拆分/finalize 后）："
                + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                            for b in _dlg_broken[:3]))
            if _dlg_gate_err:
                # v6.135 五审：纯门故障 ≠ 稿件不完整——独立异常类 + 诊断
                # conclusion=dialogue_gate_error（dump 按批次自动归类）
                raise DialogueGateError(
                    "终稿台词完整性硬门自身异常（fail-closed 不放行）：%s"
                    % str(_dlg_broken[0].get("reason") or "")[:200],
                    broken_batches=[_dlg_broken])
            raise DialogueIncompleteError(
                "终稿台词完整性硬门：%d/%d 句破损（示例：%s…［%s］）——"
                "碎片大概率由定向拆分产生，破损稿不进入选稿/提交门"
                % (len(_dlg_broken), _dlg_total,
                   str(_dlg_broken[0].get("line") or "")[:40],
                   _dlg_broken[0].get("reason")),
                broken_batches=[_dlg_broken])
        _bg_selected, _bg_meta = _cross_run_best_guard(script, novel_text,
                                                       plan=pre_extracted_outline)
        if _bg_selected is not None:
            script = _bg_selected
        if pre_extracted_outline:
            script["chapter_outline"] = pre_extracted_outline
        _metrics_update("best_guard", {
            "selected": bool(_bg_meta.get("selected")),
            "reason": _bg_meta.get("reason"),
            "new_score": _bg_meta.get("new_score"),
            "best_score": _bg_meta.get("best_score"),
            # v6.152（C5）：被初筛拒的快照清单落进产物（此前只在内存 meta 里，
            # 日志又截断到 6 条 ⇒ 事后无从复核）
            "prefilter_rejected": list(
                _bg_meta.get("prefilter_rejected_snapshots") or []),
        })

        # ===== 唯一最终提交点（v6.103，总计划阶段 4）=====
        # hook QC/重生 → finalize → revision → 确定性 quality → final semantic
        # audit → （有 blocking 则受限修复 ≤2 轮）→ staged 三件套自验 →
        # audit → quality → script 依次原子替换 → step01_commit.json。
        # 三条入口（正常生成 / 缓存命中 / BEST-GUARD 选中）都只走这里。
        script, _commit_summary = _commit_step01_outputs(
            script, novel_text,
            plan=script.get("chapter_outline") or pre_extracted_outline,
            hook_ctx={"bank": character_bank,
                      "objects_bank": scene_objects_bank,
                      "forbidden_lines": _forbidden_lines,
                      "producer_model": _producer_model},
            audit_model=_repair_model or _producer_model,
            label="落盘前提交",
            # v6.129：选稿边界记录的快照自身 revision（见 `_cross_run_best_guard`）
            source_revision=str(_bg_meta.get("selected_revision") or ""),
            source_aliases=dict(_bg_meta.get("selected_revision_aliases") or {}))
        # C1：提交结果 → 运行终局（**与缓存命中路径同一投影**）
        _project_commit_outcome(_commit_summary)
        # C5 修复（08-11）：fps 一致性校验（finalize 之后 timeline 已存在）
        _check_fps_consistency(script)
        # 提交成功后留档（失败时提交点自己会写诊断快照）
        if _commit_summary.get("status") == "COMMITTED":
            _snapshot_run_artifacts(script)
        _timing_mark("commit", status=_commit_summary.get("status"))

        # v6.70.1（09-13）：提交后未解决 issue 汇总——对最终写盘版本生效。
        # 定向修复轮超时/失败走「保留原版」时只有一行 WARN，残伤数据静默落盘
        # （09-13 hook 无主刀尖即此形态），这里显式打出（不阻塞；确定性护栏已在
        # hard_fix 修过一轮，剩下的是 LLM 级）。
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

        # 质量报告由提交点产出（staged → 自验 → 原子替换），这里只消费它的结果。
        _quality = _commit_summary.get("quality") or {}
        _shot_total = sum(float(s.get("duration_sec") or 0) for s in script.get("shots", []))
        _hook_total = float((script.get("hook") or {}).get("duration_sec") or 0)
        _metrics_update("duration", {
            "duration_band": duration_band(),
            "shots_total_sec": round(_shot_total, 3),
            "hook_sec": round(_hook_total, 3),
            "total_sec": round(_shot_total + _hook_total, 3),
            "budget_status": (
                "PASS" if DURATION_MIN_SEC - 0.01 <= _shot_total <= DURATION_MAX_SEC + 0.01 else "BLOCK"
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
        if not _quality or _quality.get("status") is None:
            # 2026-09-27（复核三轮）：提交提前中止时（本轮实测：最终审计调用
            # `request_failed: Connection error`）**根本没有产出质量报告**——
            # `_commit_summary` 里没有 `quality` 键，于是下面那个 `else` 会打出
            # 「PASS: 无 HARD / VisualRisk 阻塞项」。那是**没检出**被说成**没问题**
            # （工作准则 7），而且同一个 run 的 manifest 里还列着 10 条 blocking。
            # 「没有可用的质量判定」必须显式说出来。
            _logger.warn(
                "[QUALITY-GATE] 未产出质量报告（提交提前中止 / 审计未完成）——"
                "本次没有可用的质量判定，不得据此认为无阻塞项"
            )
        elif _quality.get("blocking"):
            _logger.warn(
                f"[QUALITY-GATE] BLOCK: hard={_quality.get('hard_count', 0)}, "
                f"visual={_quality.get('visual_risk_count', 0)}; "
                f"categories={_quality.get('blocking_categories', [])}"
            )
            _console("  [QUALITY-GATE] BLOCK: 仍有生图阻塞告警，见 step01_quality.json")
        else:
            _logger.info("[QUALITY-GATE] PASS: 无 HARD / VisualRisk 阻塞项")
        # v6.103：**只有真的提交了**才能说「已保存」。提交点 BLOCK 时三份正式产物
        # 一个字都没换（旧脚本会被骗成「已保存」——真实重跑实测过：BLOCK 之后仍打
        # 了「[OK] 分镜脚本已保存至 …」，而文件时间戳还是上一版）。
        _committed = _commit_summary.get("status") == "COMMITTED"
        if _committed:
            _logger.info(f"[OK] 分镜脚本已提交至 {OUTPUT_FILE}")
        else:
            _logger.error(
                f"[BLOCK] 分镜脚本**未提交**（三份正式产物保持上一版）："
                f"{_commit_summary.get('detail')}"
            )

        # ---- 最终摘要：写真实 stdout（控制台），绕过重定向 ----
        shots = script.get("shots", [])
        _console("")
        _console("=" * 56)
        _console("  [步骤1] " + ("完成 —— 分镜脚本摘要" if _committed
                                  else "✗ BLOCK —— 正式产物未更新"))
        _console("=" * 56)
        if not _committed:
            _console(f"  [BLOCK] {_commit_summary.get('detail')}")
            _console("  step01_commit.json 标记为 BLOCK；下游（step02a）会拒绝启动")
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
        # **终局判定单点**（2026-10-02 复核）：`finally` 里 `sys.exc_info()` 非空 ⇒ 本轮
        # 是带着异常退出的（含 `PlanInfeasibleError` 这类"计划层停下"）⇒ metrics/timing
        # 如实写 failed + 码；旧实现无论怎么死都写 `_total status=ok`。
        _exc = sys.exc_info()[0]
        if _exc is not None:
            _run_finish("failed", getattr(_exc, "__name__", str(_exc)))
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
        _logger.error(f"[FATAL] {_fatal_summary(sys.exc_info()[1], tb)}")
        sys.exit(1)
