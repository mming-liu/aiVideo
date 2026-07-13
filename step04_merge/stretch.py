"""step04_merge.stretch - Video stretch module using setpts for precise audio-video alignment"""
import sys
from pathlib import Path
import os
import subprocess

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import shutil


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


FFMPEG = _find_exe("ffmpeg")
FFPROBE = _find_exe("ffprobe")

STRETCH_RAIO_MIN = float(os.getenv("STRETCH_RAIO_MIN", "0.75"))
STRETCH_RAIO_MAX = float(os.getenv("STRETCH_RAIO_MAX", "2.5"))


def _run_cmd(cmd, **kwargs):
    """Execute shell command and print."""
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def get_media_duration(path: Path) -> float:
    """Get media duration using ffprobe."""
    if not path.exists():
        return 0.0
    try:
        result = _run_cmd([
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ])
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def stretch_clip(clip_path: Path, target_dur: float, output_path: Path) -> bool:
    """Stretch video to target duration using setpts filter."""
    if target_dur <= 0:
        return False
    actual_dur = get_media_duration(clip_path)
    if actual_dur <= 0:
        return False

    ratio = target_dur / actual_dur
    # Clamp ratio to prevent obvious distortion
    if ratio < STRETCH_RAIO_MIN or ratio > STRETCH_RAIO_MAX:
        clamped = max(STRETCH_RAIO_MIN, min(STRETCH_RAIO_MAX, ratio))
        print(f"  [WARN] Stretch ratio {ratio:.3f} exceeds limits, clamped to {clamped:.3f}")
        ratio = clamped

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        FFMPEG, "-y",
        "-i", str(clip_path),
        "-vf", f"setpts={ratio}*PTS",
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        str(output_path),
    ]
    try:
        _run_cmd(cmd)
        size_kb = output_path.stat().st_size // 10240
        print(f"  [STRETCH] {clip_path.name} {actual_dur:.1f}s -> {target_dur:.1f}s (ratio={ratio:.3f}, {size_kb}KB)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Stretch failed: {e.stderr[:200]}")
        return False