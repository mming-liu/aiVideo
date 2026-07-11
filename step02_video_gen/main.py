"""step02_video_gen - 视频生成模块入口
"""
import sys
from pathlib import Path
import os

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio
import json
import shutil
import subprocess
from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

# ============================================================================
# 配置
# ============================================================================
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))
OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "./output/video"))

CHAPTER_DIR = OUTPUT_BASE / f"book_{BOOK_INDEX:02d}" / f"chapter_{CHAPTER_INDEX:02d}"
SCRIPT_FILE = CHAPTER_DIR / "script_result.json"
AUDIO_TTS_DIR = CHAPTER_DIR / "audio"
WORKING_DIR = Path(os.getenv("VIMAX_WORKING_DIR", str(CHAPTER_DIR / "vimax_working")))

MAX_RETRIES = int(os.getenv("VIDEO_MAX_RETRIES", "3"))
SUBMIT_INTERVAL = float(os.getenv("VIDEO_CLIP_INTERVAL", "60"))
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "6"))

_VIDEO_RPM = os.getenv("VIDEO_MAX_REQUESTS_PER_MINUTE")

from vimax.utils.rate_limiter import RateLimiter

VIDEO_RATE_LIMITER = RateLimiter(
    max_requests_per_minute=int(_VIDEO_RPM) if _VIDEO_RPM else None,
)

# ============================================================================
# 子模块导入
# ============================================================================
from step02_video_gen.audio_pregen import (
    load_script,
    _gen_shot_audio_duration,
    _update_script_with_actual_durations,
)
from step02_video_gen.prompt_builder import _build_prompt
from step02_video_gen.submit_poll import generate_all


# ============================================================================
# ffprobe 查找
# ============================================================================

def _find_ffprobe() -> str:
    """查找 ffprobe 可执行文件."""
    path = shutil.which("ffprobe")
    if path:
        return path
    # 尝试从 FFMPEG_BIN_DIR 查找
    ffmpeg_bin = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if ffmpeg_bin:
        ffprobe_path = Path(ffmpeg_bin) / "ffprobe.exe"
        if ffprobe_path.exists():
            return str(ffprobe_path)
    raise FileNotFoundError("ffprobe 未找到，请确保 FFmpeg 已安装并加入 PATH")


FFPROBE = _find_ffprobe()


# ============================================================================
# 主流程
# ============================================================================

def main() -> None:
    print("=" * 50)
    print("  ViMax 视频生成 (流水线优化版)")
    print("=" * 50)

    # 创建输出目录
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    WORKING_DIR.mkdir(parents=True, exist_ok=True)

    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    narration = script.get("narration", "")
    total_duration = sum(s.get("duration", 0) for s in shots)
    print(f"\n分镜: {len(shots)} 个, ~{total_duration}s")
    print(f"旁白: {narration[:50]}...")

    auto_confirm = os.getenv("VIDEO_AUTO_CONFIRM", "").strip() == "1"
    if auto_confirm:
        answer = "y"
        print("确认: y (AUTO)")
    else:
        answer = input("\n确认生成? (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消")
        sys.exit(0)

    force_ff = os.getenv("SCRIPT_FIRST_FRAME_FORCE", "0").strip() == "1"
    failed = asyncio.run(generate_all(script, WORKING_DIR, force_first_frame=force_ff))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()