"""step03_tts_audio - TTS audio generation entry point"""
import sys
from pathlib import Path
import os

# Ensure imports resolve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# Configuration
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))

# 输出目录（统一路径来源: shared/paths.py）
from shared.paths import chapter_dir, script_file, audio_dir
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
AUDIO_OUTPUT_DIR = audio_dir()

# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(CHAPTER_DIR / "step03.log", console_level="INFO")

# Submodule imports
from step03_tts_audio.shot_tts import load_script as load_script_module, gen_all_shot_tts
from tts.config import config as tts_config
from tts.voice_registry import get_registry


def main():
    _logger.separator()
    _logger.info("  Step 3: TTS Audio Generation")
    _logger.info("  (Multi-voice support enabled)")
    _logger.separator()

    # 检查前置步骤产物
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {SCRIPT_FILE}\n"
            f"请先运行 step01: python step01_script_gen/main.py"
        )

    # Create output directories
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load script
    script = load_script_module(SCRIPT_FILE)
    shots = script.get("shots", [])

    _logger.info(f"Shots: {len(shots)}")
    _logger.info(f"Chapter: {CHAPTER_INDEX}, Book: {BOOK_INDEX}")

    # 检查已生成音频（仅作进度提示；M10 修复 2026-08-14：不能凭 mp3 存在就
    # 整体跳过——音色可能已变更，.voice 缓存由 gen_all_shot_tts 逐镜校验并
    # 对音色变化的镜头自动重生成，整体跳过会绕过 voice 再生）。
    existing_count = 0
    for shot in shots:
        idx = shot.get("index", 0)
        audio_path = AUDIO_OUTPUT_DIR / f"shot_{idx}.mp3"
        if audio_path.exists() and audio_path.stat().st_size > 10240:
            existing_count += 1

    if existing_count == len(shots) and existing_count > 0:
        _logger.info(f"[INFO] 发现 {existing_count} 个已生成音频，将按 .voice 音色缓存逐镜复用/重生成")
    elif existing_count > 0:
        _logger.info(f"[INFO] 部分音频已存在 ({existing_count}/{len(shots)})，将补充生成缺失的")

    # M10 修复 2026-08-14：阻断式 input() 改为可配置确认——TTS_AUTO_CONFIRM=1
    # 或 stdin 非交互（CI/批处理重定向）时自动继续，不阻塞管线。
    auto_confirm = os.getenv("TTS_AUTO_CONFIRM", "0").strip().lower() in ("1", "yes", "y", "true")
    if not auto_confirm and sys.stdin.isatty():
        try:
            answer = input("确认 TTS 生成? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _logger.info("已取消.")
            sys.exit(0)
        if answer not in ("y", "yes"):
            _logger.info("已取消.")
            sys.exit(0)

    try:
        tts_config.validate()
        _logger.info("\n=== Generate per-shot TTS (dynamic voice) ===")
        # 初始化音色注册表（跨章节保持角色声音一致）
        voice_registry = get_registry()
        gen_all_shot_tts(script, AUDIO_OUTPUT_DIR, voice_registry, use_async=True)
    except Exception as e:
        _logger.error(f"[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()