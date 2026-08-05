"""step04_merge.stretch - 媒体时长工具（native 模式精简版）

2026-08 架构变更：合并改为唯一 native 模式（直接拼接保留视频原声），
不再按音频时长拉伸视频，stretch_clip / MODE_B_TRIM_FRAMES 已移除。
仅保留 get_media_duration 供合并/字幕时间轴使用。
"""
import sys
from pathlib import Path
import os
import subprocess
import shutil

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


def _find_exe(name: str) -> str:
    """Find executable file."""
    path = shutil.which(name)
    if not path:
        ffmpeg_bin = os.getenv("FFMPEG_BIN_DIR", "").strip()
        if ffmpeg_bin:
            exe_path = Path(ffmpeg_bin) / f"{name}.exe"
            if exe_path.exists():
                return str(exe_path)
        raise FileNotFoundError(f"Cannot find {name}. Please install FFmpeg.")
    return path


FFPROBE = _find_exe("ffprobe")


def get_media_duration(path: Path) -> float:
    """Get media duration using ffprobe."""
    if not path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0
