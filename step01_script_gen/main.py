"""step01_script_gen - 分镜脚本生成入口

功能：
  - 从小说提取章节内容
  - 调用 LLM 生成分镜脚本
  - 验证音画同步
  - 计算时间轴
  - 角色提取与持久化

输出：
  - script_result.json: 分镜脚本（位于 OUTPUT_BASE/book_N/chapter_N/）
"""
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
    # 去除可能的 markdown 代码块
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return raw


# ============================================================================
# 3. 主流程
# ============================================================================

def main() -> None:
    """主流程：读取章节 → 生成脚本 → 验证 → 保存."""
    print("=" * 50)
    print("  步骤1: 分镜脚本生成")
    print("=" * 50)

    # 创建输出目录
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

    # 读取章节
    novel_text = read_chapter("锦衣夜行_77119小说.txt", chapter=CHAPTER_INDEX)
    novel_preview = novel_text[:1000]

    # 检查缓存
    from step01_script_gen.hash_cache import _prompt_changed as prompt_changed
    if not prompt_changed(Path(__file__).parent / "prompts.py", SCRIPT_FILE, novel_preview, CHAPTER_INDEX):
        print(f"[CACHE] 脚本已缓存: {SCRIPT_FILE}")
        # 仍需计算时间轴（因为使用了新的时间轴算法）
        with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
            script = json.load(f)
        _compute_timecodes(script)
        with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"时间轴已重新计算")
        return

    # 生成脚本
    raw = gen_spy_script(novel_text)
    script = parse_response(raw)

    # 提取角色（跨章节一致性）
    if CHARACTER_EXTRACTION_ENABLED:
        print("[INFO] 提取角色信息...")
        bank = CharacterBank()
        bank.load()
        chars = extract_characters(novel_text, bank)
        # 存入脚本
        script["characters"] = chars
        # 更新角色库
        for c in chars:
            if c.get("is_new", True):
                bank.register_character(
                    c["identifier"],
                    c.get("static_features", ""),
                    c.get("dynamic_features", ""),
                    CHAPTER_INDEX,
                )
        bank.save()

    # 计算时间轴（基于 TTS 语速）
    _compute_timecodes(script)

    # 写入 hash
    from step01_script_gen.hash_cache import _hash_source
    script["_source_hash"] = _hash_source(str(Path(__file__).parent / "prompts.py"), novel_preview, CHAPTER_INDEX)

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    print(f"[OK] 分镜脚本已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()