"""step01_script_gen - 分镜脚本生成入口（v5.0 增强版）

功能：
  - 从小说提取章节内容
  - 调用 LLM 生成分镜脚本
  - 验证音画同步 + 合规校验（scene_compliance）
  - 注入全局渲染参数（style_prefix.inject_global_config_into_script）
  - 计算时间轴（含运镜量化参数）
  - 角色提取与持久化

输出：
  - script_result.json: 分镜脚本（位于 OUTPUT_BASE/book_N/chapter_N/）
"""
from __future__ import annotations

import sys
from pathlib import Path
import os

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
import json

load_dotenv(_PROJECT_ROOT / ".env")

# ============================================================================
# 配置（从环境变量读取）
# ============================================================================
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))
OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "./output/video"))
CHARACTER_EXTRACTION_ENABLED = os.getenv("CHARACTER_EXTRACTION_ENABLED", "1").strip() == "1"
SHOT_COUNT_MIN = int(os.getenv("SHOT_COUNT_MIN", "4"))
SHOT_COUNT_MAX = int(os.getenv("SHOT_COUNT_MAX", "20"))

# v5.0: 合规模式
COMPLIANCE_STRICT = os.getenv("COMPLIANCE_STRICT", "0").strip() == "1"
COMPLIANCE_AUTO_FIX = os.getenv("COMPLIANCE_AUTO_FIX", "1").strip() == "1"

# 输出目录基于 book/chapter 计算
CHAPTER_DIR = OUTPUT_BASE / f"book_{BOOK_INDEX:02d}" / f"chapter_{CHAPTER_INDEX:02d}"
OUTPUT_FILE = CHAPTER_DIR / "script_result.json"
SCRIPT_FILE = OUTPUT_FILE

# ============================================================================
# 导入子模块
# ============================================================================
from shared.chapter_reader import extract_section
from _text_gen import create_service, TextGenerationRequest
from shared.character_bank import CharacterBank
from step01_script_gen.prompts import build_prompt, SYSTEM_PROMPT
from step01_script_gen.character_extractor import extract_characters
from step01_script_gen.validation import parse_response
from step01_script_gen.timecode import _compute_timecodes
from step01_script_gen.hash_cache import _prompt_changed, _hash_source

import style_prefix
import scene_compliance


# ============================================================================
# 1. 从小说提取指定章节
# ============================================================================

def read_chapter(path: str = "锦衣夜行_77119小说.txt", chapter: int = 1) -> str:
    """提取小说指定章节（合并多个小节），返回完整内容."""
    full_text = extract_section(path, chapter)
    print(f"[INFO] 选取第 {chapter} 章全文 {len(full_text)} 字符作为素材")
    return full_text


# ============================================================================
# 2. 文本生成
# ============================================================================

def gen_spy_script(novel_text: str) -> str:
    """调用 LLM 生成脚本，返回原始文本."""
    prompt = build_prompt(novel_text, SHOT_COUNT_MIN, SHOT_COUNT_MAX)
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        max_tokens=40000,
        temperature=0.8,
    )
    print("[INFO] 正在调用 LLM 生成分镜脚本...")
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
# 3. 后处理：注入全局配置 + 合规过滤
# ============================================================================

def _post_process(script: dict) -> dict:
    """增强：注入全局渲染配置 + 合规过滤."""
    # 注入全局渲染参数（若 LLM 未输出则补上）
    script = style_prefix.inject_global_config_into_script(script)

    # 自动合规过滤
    if COMPLIANCE_AUTO_FIX:
        script = style_prefix.sanitize_script_shots(script, verbose=True)
        script = scene_compliance.auto_sanitize_shot_prompts(script, verbose=True)

    return script


# ============================================================================
# 4. 主流程
# ============================================================================

def main() -> None:
    """主流程：读取章节 → 生成脚本 → 验证 → 后处理 → 保存."""
    print("=" * 50)
    print("  步骤1: 分镜脚本生成 (v5.0)")
    print("=" * 50)

    # 创建输出目录
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

    # 读取章节
    novel_text = read_chapter("锦衣夜行_77119小说.txt", chapter=CHAPTER_INDEX)
    novel_preview = novel_text[:1000]

    # 检查缓存
    if not _prompt_changed(
        Path(__file__).parent / "prompts.py", SCRIPT_FILE, novel_preview, CHAPTER_INDEX
    ):
        print(f"[CACHE] 脚本已缓存: {SCRIPT_FILE}")
        with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
            script = json.load(f)
        # 重新计算时间轴（使用增强算法）
        _compute_timecodes(script)
        with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"时间轴已重新计算")
        return

    # 生成脚本
    raw = gen_spy_script(novel_text)
    script = parse_response(raw)

    # 后处理：注入全局配置 + 合规过滤
    script = _post_process(script)

    # 提取角色（跨章节一致性）
    if CHARACTER_EXTRACTION_ENABLED:
        print("[INFO] 提取角色信息...")
        bank = CharacterBank()
        bank.load()
        chars = extract_characters(novel_text, bank)
        script["characters"] = chars
        for c in chars:
            if c.get("is_new", True):
                bank.register_character(
                    c["identifier"],
                    c.get("static_features", ""),
                    c.get("dynamic_features", ""),
                    CHAPTER_INDEX,
                )
        bank.save()

    # 计算时间轴（含 v5.0 运镜量化参数）
    _compute_timecodes(script)

    # 写入 hash
    script["_source_hash"] = _hash_source(
        str(Path(__file__).parent / "prompts.py"), novel_preview, CHAPTER_INDEX
    )

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    print(f"[OK] 分镜脚本已保存至 {OUTPUT_FILE}")

    # 摘要
    print(f"\n{'='*60}")
    print(f"分镜脚本摘要")
    print(f"{'='*60}")
    shots = script.get("shots", [])
    print(f"共 {len(shots)} 个镜头")
    for k in ("resolution", "ratio", "fps", "render_style", "global_weather", "global_atmosphere"):
        if k in script:
            print(f"  {k}: {script[k]}")
    for shot in shots[:3]:
        idx = shot.get("index", "?")
        ct = shot.get("color_tone", shot.get("clip_transition", ""))
        print(f"  镜头{idx}: {shot.get('subtitle', '')[:40]}... [{ct}]")


if __name__ == "__main__":
    main()
