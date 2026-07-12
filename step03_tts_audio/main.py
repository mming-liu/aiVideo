"""step03_tts_audio - TTS audio generation entry point"""
import sys
from pathlib import Path
import os

# Ensure imports resolve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

# Configuration
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))
OUTPUT_RAW = os.getenv("OUTPUT_BASE", "./output/video")
OUTPUT_BASE = _PROJECT_ROOT / OUTPUT_RAW if not Path(OUTPUT_RAW).is_absolute() else Path(OUTPUT_RAW)

CHAPTER_DIR = OUTPUT_BASE / f"book_{BOOK_INDEX:02d}" / f"chapter_{CHAPTER_INDEX:02d}"
SCRIPT_FILE = CHAPTER_DIR / "script_result.json"
AUDIO_OUTPUT_DIR = CHAPTER_DIR / "audio"

# Submodule imports
from step03_tts_audio.shot_tts import load_script as load_script_module, gen_all_shot_tts
from step03_tts_audio.narration_tts import load_narration, gen_narration_tts
from tts.config import config as tts_config


def main():
    print("=" * 50)
    print("  Step 3: TTS Audio Generation")
    print("=" * 50)

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
    narration = script.get("narration", "")

    print(f"Shots: {len(shots)}")
    print(f"Narration ({len(narration)} chars): {narration[:80]}...\n")

    # 检查 step02 是否已完成音频生成（复用产物，避免重复调用 TTS API）
    existing_count = 0
    for shot in shots:
        idx = shot.get("index", 0)
        audio_path = AUDIO_OUTPUT_DIR / f"shot_{idx}.mp3"
        if audio_path.exists() and audio_path.stat().st_size > 10240:
            existing_count += 1

    if existing_count == len(shots) and existing_count > 0:
        print(f"[INFO] 发现 {existing_count} 个已生成的音频文件（来自 step02）")
        print(f"       跳过 TTS 生成，直接使用现有音频")
        return

    if existing_count > 0:
        print(f"[INFO] 部分音频已存在 ({existing_count}/{len(shots)})，将补充生成缺失的")

    answer = input("确认 TTS 生成? (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消.")
        sys.exit(0)

    try:
        tts_config.validate()
        print("\n=== Generate per-shot TTS ===")
        gen_all_shot_tts(script, AUDIO_OUTPUT_DIR)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()