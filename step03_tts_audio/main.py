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
OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "./output/video"))

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

    # Create output directories
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load script
    script = load_script_module(SCRIPT_FILE)
    shots = script.get("shots", [])
    narration = script.get("narration", "")

    print(f"Shots: {len(shots)}")
    print(f"Narration ({len(narration)} chars): {narration[:80]}...\n")

    answer = input("Confirm TTS generation? (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("Cancelled.")
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