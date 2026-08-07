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
    _load_gap_analysis_context,
)
from step01_script_gen.character_extractor import extract_characters
from step01_script_gen.object_extractor import extract_scene_objects
from step01_script_gen.asset_extractor import extract_scene_assets, extract_props
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

def gen_spy_script(novel_text: str, bank=None, objects_bank=None) -> str:
    """调用 LLM 生成脚本，返回原始文本."""
    from _text_gen.deepseek_schema import SCRIPT_SCHEMA
    prompt = build_prompt(novel_text, bank, objects_bank)
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

def gen_hook_script(novel_text: str, bank=None, objects_bank=None) -> dict | None:
    """调用 LLM 生成钩子分镜，返回 hook dict 或 None.

    Args:
        novel_text: 小说原文
        bank: CharacterBank 实例，用于注入角色锚点（prompt 中用角色名而非模糊称呼）
        objects_bank: SceneObjectsBank 实例，用于注入场景物体尺寸锚点（prompt 中写出锚定尺寸）
    """
    if not HOOK_ENABLED:
        return None

    prompt = build_hook_prompt(novel_text, bank=bank, objects_bank=objects_bank)
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
        return hook
    except Exception as e:
        _logger.warn(f"[WARN] 钩子分镜解析失败: {e}，跳过钩子")
        return None


# ============================================================================
# 5. 镜头数超限收敛重生成（v6.7 新增）
# ============================================================================

def _regen_over_limit(script: dict, novel_text: str, bank=None, objects_bank=None,
                      max_attempts: int = 2) -> dict:
    """镜头数严重超限时，反馈给 LLM 合并冗余镜头，重新生成一次（软收敛）.

    与 gap_analysis 相互独立：这里只看「镜头数/总时长」超限，不校验内容分布。
    仍超限或 JSON 解析失败时保留原版，不中断流水线（下游有 TTS rate / merge
    truncation / stretch cap 兜底，v4.1 哲学是镜头数交 LLM，此处只收敛废输出）。

    Returns:
        重新生成且通过校验的 script；否则返回原 script。
    """
    from shared.config import SHOT_COUNT_MAX

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
        from shared.config import SHOT_COUNT_MAX as _max
        feedback = (
            f"\n\n# 镜头数收敛反馈（来自本地校验）\n"
            f"当前生成的镜头数为 {current} 个、总时长 {total_duration:.0f} 秒，"
            f"超过上限 {_max} 个镜头。短视频平台成片以 60 秒为佳。\n"
            f"请重新生成，严格做到：\n"
            f"1. 合并同场景连续镜头（同一地点、连续动作、多镜一拍）\n"
            f"2. 删减冗余过渡镜头与重复表达的空镜\n"
            f"3. 确保镜头数 ≤ {_max}，总时长控制在 110 秒以内\n"
            f"4. 保留全部关键剧情节点与高潮部分，不要删剧情，只合并冗余\n"
        )
        prompt = build_prompt(novel_text, bank, objects_bank)
        prompt = prompt + feedback

        service = create_service()
        from _text_gen.deepseek_schema import SCRIPT_SCHEMA
        req = TextGenerationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=40000,
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
        _logger.warn(
            f"[COUNT] 重试 {max_attempts} 次后镜头数仍超上限，保留当前版本继续流水线"
        )
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
    for _pid in prop_assets_bank.get_all_identifiers():
        _pa = prop_assets_bank.get_prop(_pid)
        if _pa:
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

def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存.

    控制台静默策略：运行期间所有 print()（校验 WARN、LLM 推理过程等）
    重定向到 step01.log，控制台只显示关键进度和最终摘要。
    """

    _logger.separator()
    _logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _logger.info("  新增: 钩子分镜 + 敏感词校验")
    _logger.separator()

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

    # 后处理：注入全局配置 + 合规过滤 + 敏感词校验
    script = _post_process(script)

    # ========== v6新增：钩子分镜生成 ==========
    if HOOK_ENABLED:
        _logger.info("\n[INFO] 开始生成钩子分镜...")
        hook = gen_hook_script(novel_text, bank=character_bank, objects_bank=scene_objects_bank)
        if hook:
            script["hook"] = hook
            _logger.info(f"[OK] 钩子分镜已生成: {hook.get('narration_segment', '')[:30]}...")
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
            chars = extract_characters(novel_text, character_bank)
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