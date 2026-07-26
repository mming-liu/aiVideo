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
from pathlib import Path
import json

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 统一配置（从 shared.config 读取 .env）
from shared.config import (
    NOVEL_CHAPTER, BOOK_INDEX,
    SHOT_COUNT_MIN, SHOT_COUNT_MAX,
    CHARACTER_EXTRACTION_ENABLED,
    COMPLIANCE_STRICT, COMPLIANCE_AUTO_FIX,
    HOOK_ENABLED,
    GAP_ANALYSIS_ENABLED, GAP_MAX_REGEN_ATTEMPTS,
)

# 输出目录基于 book/chapter 计算（统一路径来源: shared/paths.py）
from shared.paths import chapter_dir, script_file
CHAPTER_DIR = chapter_dir()
OUTPUT_FILE = script_file()
SCRIPT_FILE = OUTPUT_FILE

# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(CHAPTER_DIR / "step01.log", console_level="INFO")

# ============================================================================
# 导入子模块
# ============================================================================
from shared.chapter_reader import extract_section, get_chapter_meta
from _text_gen import create_service, TextGenerationRequest
from shared.character_bank import CharacterBank
from step01_script_gen.prompts import (
    build_prompt, SYSTEM_PROMPT, build_hook_prompt, parse_hook_response,
    _load_gap_analysis_context,
)
from step01_script_gen.character_extractor import extract_characters
from step01_script_gen.validation import parse_response
from step01_script_gen.timecode import _compute_timecodes
from step01_script_gen.hash_cache import _prompt_changed, _hash_source
from step01_script_gen.gap_analysis import validate_script_gap, generate_gap_report, print_gap_report

import style_prefix
import scene_compliance


# ============================================================================
# 1. 从小说提取指定章节
# ============================================================================

def read_chapter(path: str = "锦衣夜行_77119小说.txt", chapter: int = 1) -> str:
    """提取小说指定章节（合并多个小节），返回完整内容."""
    full_text = extract_section(path, chapter)
    _logger.info(f"[INFO] 选取第 {chapter} 章全文 {len(full_text)} 字符作为素材")
    return full_text


# ============================================================================
# 2. 文本生成
# ============================================================================

def gen_spy_script(novel_text: str, bank=None) -> str:
    """调用 LLM 生成脚本，返回原始文本."""
    prompt = build_prompt(novel_text, SHOT_COUNT_MIN, SHOT_COUNT_MAX, bank)
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        max_tokens=40000,
        temperature=0.2,  # v6.5: 降低温度防止幻觉
    )
    _logger.info("[INFO] 正在调用 LLM 生成分镜脚本...")
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

def gen_hook_script(novel_text: str) -> dict | None:
    """调用 LLM 生成钩子分镜，返回 hook dict 或 None."""
    if not HOOK_ENABLED:
        return None

    prompt = build_hook_prompt(novel_text)
    service = create_service()
    req = TextGenerationRequest(
        system_prompt="你是短视频脚本大师，擅长在视频开头制造悬念和吸引力。禁止血腥暴力细节。",
        user_prompt=prompt,
        max_tokens=8000,
        temperature=0.2,  # v6.5: 降低温度防止幻觉
    )
    _logger.info("[INFO] 正在调用 LLM 生成钩子分镜...")
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
        return hook
    except Exception as e:
        _logger.warn(f"[WARN] 钩子分镜解析失败: {e}，跳过钩子")
        return None


# ============================================================================
# 5. 主流程
# ============================================================================

def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存."""

    _logger.separator()
    _logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _logger.info("  新增: 钩子分镜 + 敏感词校验")
    _logger.separator()

    # 创建输出目录
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

    # 读取章节
    novel_text = read_chapter("锦衣夜行_77119小说.txt", chapter=NOVEL_CHAPTER)
    novel_preview = novel_text[:1000]

    # 加载角色库（v6.1 新增）
    character_bank = CharacterBank()
    character_bank.load()

    # 预提取本章角色信息，辅助分镜生成（v6.1 新增）
    # 这样 LLM 生成脚本时能看到本章新角色的特征描述
    if CHARACTER_EXTRACTION_ENABLED:
        _logger.info("[INFO] 预提取本章角色信息以辅助分镜生成...")
        try:
            chars = extract_characters(novel_text, character_bank)
            # 注册新角色到 bank（暂存本章角色，供生成时使用）
            for c in chars:
                if c.get("is_new", True):
                    character_bank.register_character(
                        c["identifier"],
                        c.get("static_features", ""),
                        c.get("dynamic_features", ""),
                        NOVEL_CHAPTER,
                    )
            # v6.1 优化：立即保存，确保即使脚本生成失败，角色库也已更新
            character_bank.save()
            _logger.info(f"[INFO] 预提取到 {len(chars)} 个角色特征（已保存到角色库）")
        except Exception as e:
            _logger.warn(f"[WARN] 预提取角色失败: {e}，继续生成脚本...")

    # 检查缓存
    prompt_changed = _prompt_changed(
        Path(__file__).parent / "prompts.py", SCRIPT_FILE, novel_preview, NOVEL_CHAPTER
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
        with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        _logger.info("时间轴已重新计算")
        return

    # 脚本已更新，删除旧的音频和视频文件
    _logger.info(f"[INFO] 分镜脚本已更新，清理旧的音视频文件...")
    for subdir in ("audio", "vimax_working", "stretched"):
        subdir_path = CHAPTER_DIR / subdir
        if subdir_path.exists():
            import shutil
            shutil.rmtree(subdir_path)
    _logger.verbose(f"  [已删除] {subdir}/")
    # 删除最终视频和字幕
    for f in ("final.mp4", "subtitles.srt"):
        fpath = CHAPTER_DIR / f
        if fpath.exists():
            fpath.unlink()
            _logger.verbose(f"  [已删除] {f}")

    # 生成脚本
    raw = gen_spy_script(novel_text, character_bank)
    script = parse_response(raw, novel_text=novel_text)

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

            # 追加差距反馈到 prompt，重新生成
            prompt = build_prompt(novel_text, SHOT_COUNT_MIN, SHOT_COUNT_MAX, character_bank)
            prompt = prompt + gap_context

            service = create_service()
            req = TextGenerationRequest(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=40000,
                temperature=0.2,  # v6.5: 降低温度防止幻觉
            )
            result = service.generate(req)
            if not result.success:
                raise RuntimeError(f"LLM 重新生成失败: {result.error}")

            import re
            raw = result.text.strip()
            raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

            script = parse_response(raw, novel_text=novel_text)
            script = _post_process(script)
        else:
            # 达到最大尝试次数仍有问题，记录但不中断
            _logger.warn("[GAP] 达到最大重试次数，仍有差距问题，将继续处理")

    # 后处理：注入全局配置 + 合规过滤 + 敏感词校验
    script = _post_process(script)

    # ========== v6新增：钩子分镜生成 ==========
    if HOOK_ENABLED:
        _logger.info("\n[INFO] 开始生成钩子分镜...")
        hook = gen_hook_script(novel_text)
        if hook:
            script["hook"] = hook
            _logger.info(f"[OK] 钩子分镜已生成: {hook.get('narration_segment', '')[:30]}...")
        else:
            _logger.info("[INFO] 跳过钩子分镜")

    # 提取角色（跨章节一致性）
    if CHARACTER_EXTRACTION_ENABLED:
        _logger.info("[INFO] 提取角色信息...")
        # 复用预提取的 character_bank，角色库已在预提取阶段保存（第206行）
        chars = extract_characters(novel_text, character_bank)
        script["characters"] = chars
        # 注意：角色库已在预提取阶段保存，无需重复保存
        _logger.info(f"[INFO] 已提取 {len(chars)} 个角色")

    # 计算时间轴（v5.0 运镜量化参数）
    _compute_timecodes(script)

    # 章节元信息（部名/章名，来自小说标题行「第一部 杀青州 第NNN章 章名」）
    script.update(get_chapter_meta("锦衣夜行_77119小说.txt", NOVEL_CHAPTER))

    # 写入 hash
    script["_source_hash"] = _hash_source(
        str(Path(__file__).parent / "prompts.py"), novel_preview, NOVEL_CHAPTER
    )

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    _logger.info(f"[OK] 分镜脚本已保存至 {OUTPUT_FILE}")

    # 摘要
    _logger.separator("=", 60)
    _logger.info("分镜脚本摘要")
    _logger.separator("=", 60)
    shots = script.get("shots", [])
    _logger.info(f"共 {len(shots)} 个镜头")
    if script.get("hook"):
        _logger.info("含 1 个钩子分镜（开场悬念）")
    for k in ("resolution", "ratio", "fps", "render_style", "global_weather", "global_atmosphere"):
        if k in script:
            _logger.verbose(f"  {k}: {script[k]}")
    for shot in shots[:3]:
        idx = shot.get("index", "?")
        ct = shot.get("color_tone", shot.get("clip_transition", ""))
        _logger.verbose(f"  镜头{idx}: {shot.get('subtitle', '')[:40]}... [{ct}]")



if __name__ == "__main__":
    main()