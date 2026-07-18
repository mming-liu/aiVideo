"""step02_video_gen - 视频生成模块入口
"""
import sys
from pathlib import Path
import os

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import shutil
import subprocess
from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

# ============================================================================
# 配置
# ============================================================================
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))

# 输出目录（统一路径来源: shared/paths.py）
from shared.paths import chapter_dir, script_file, audio_dir, vimax_working_dir
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
AUDIO_TTS_DIR = audio_dir()
WORKING_DIR = vimax_working_dir()

MAX_RETRIES = int(os.getenv("VIDEO_MAX_RETRIES", "3"))
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "6"))

_VIDEO_RPM = os.getenv("VIDEO_MAX_REQUESTS_PER_MINUTE")

from vimax.utils.rate_limiter import RateLimiter

VIDEO_RATE_LIMITER = RateLimiter(
    max_requests_per_minute=int(_VIDEO_RPM) if _VIDEO_RPM else None,
)

# ============================================================================
# 子模块导入
# ============================================================================
from step02_video_gen.audio_pregen import load_script
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

    # 检查前置步骤产物
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {SCRIPT_FILE}\n"
            f"请先运行 step01: python step01_script_gen/main.py"
        )
    if not CHAPTER_DIR.exists() or not any(CHAPTER_DIR.iterdir()):
        print(f"[WARN] 输出目录为空: {CHAPTER_DIR}")

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
    failed = generate_all(script, WORKING_DIR, force_first_frame=force_ff)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()