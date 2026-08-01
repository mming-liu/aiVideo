"""step04_merge.stretch - Video stretch module using setpts for precise audio-video alignment

v5.3: Mode B trim support — clips generated via I2V continuity chain have their
first N frames trimmed to eliminate startup jitter from the reference image hold.
"""
import sys
from pathlib import Path
import os
import subprocess

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


import shutil

from shared.error_fmt import fmt_err_tail


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
# 超过此比率使用 tpad 补黑帧（避免大幅慢放导致画面变形）
STRETCH_TPAD_RATIO = float(os.getenv("STRETCH_TPAD_RATIO", "1.2"))

# 模式B I2V 接续镜头：裁掉开头 N 帧消除起始画面顿挫
MODE_B_TRIM_FRAMES = int(os.getenv("MODE_B_TRIM_FRAMES", "2"))


def _run_cmd(cmd, **kwargs):
    """Execute shell command and print."""
    print(f"[CMD] {' '.join(cmd)}")
    # ffmpeg/ffprobe 输出 UTF-8；不显式指定时 Windows 会用 GBK 解码导致
    # UnicodeDecodeError（路径含中文目录名时必现）
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


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


def stretch_clip(clip_path: Path, target_dur: float, output_path: Path,
                 keep_audio: bool = False, trim_start_frames: int = 0) -> bool:
    """Stretch video to target duration using setpts filter.

    Args:
        clip_path: Source video path
        target_dur: Target duration in seconds
        output_path: Output video path
        keep_audio: If True, preserve video audio; if False, remove audio (default)
        trim_start_frames: Trim first N frames from the beginning
            (used for Mode B I2V continuity clips to eliminate startup jitter)
    """
    if target_dur <= 0:
        return False
    actual_dur = get_media_duration(clip_path)
    if actual_dur <= 0:
        return False

    ratio = target_dur / actual_dur
    # Only warn for setpts branch - tpad branch naturally reaches target duration
    if ratio < STRETCH_RAIO_MIN or ratio > STRETCH_RAIO_MAX:
        if ratio < STRETCH_TPAD_RATIO:
            # setpts branch: actual clamping happens
            clamped = max(STRETCH_RAIO_MIN, min(STRETCH_RAIO_MAX, ratio))
            print(f"  [WARN] Stretch ratio {ratio:.3f} exceeds limits, clamped to {clamped:.3f}")
            ratio = clamped
        # tpad branch: ratio > 1.2 will use tpad to reach target, no clamping needed

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build video filter chain
    vf_parts = []

    # Build the ffmpeg command
    cmd = [FFMPEG, "-y"]

    # Step 0: Mode B frame trim - use -ss to skip frames BEFORE input for A/V sync
    # -ss before -i applies to both video and audio streams
    trim_start_sec = 0.0
    if trim_start_frames > 0:
        trim_start_sec = trim_start_frames / 24.0  # Assume 24fps from Agnes
        cmd.extend(["-ss", str(trim_start_sec)])
        print(f"  [TRIM] Mode B clip: trimming {trim_start_frames} frames (~{trim_start_sec:.3f}s) from start (A/V sync)")

    cmd.extend(["-i", str(clip_path)])

    # Step 1: Stretch
    if ratio < STRETCH_TPAD_RATIO:
        # 轻微慢放（<1.2倍），肉眼不易察觉
        if abs(ratio - 1.0) >= 0.01:
            vf_parts.append(f"setpts={ratio}*PTS")
    else:
        # 大幅延长：保持原速播放，末尾补黑帧
        pad_duration = target_dur - actual_dur
        print(f"  [INFO] Using tpad for large stretch: +{pad_duration:.1f}s black frames")
        vf_parts.append(f"tpad=stop_mode=add:stop_duration={pad_duration}")

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])

    # Audio handling
    if not keep_audio:
        cmd.append("-an")
    else:
        cmd.extend(["-c:a", "aac", "-ar", "44100", "-ac", "2"])

    cmd.extend([
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-profile:v", "main",
        "-level", "4.0",
        "-preset", "ultrafast",
        "-crf", "20",
        str(output_path),
    ])
    try:
        _run_cmd(cmd)
        size_kb = output_path.stat().st_size // 10240
        trim_tag = f" trim={trim_start_frames}f" if trim_start_frames > 0 else ""
        print(f"  [STRETCH] {clip_path.name} {actual_dur:.1f}s -> {target_dur:.1f}s"
              f" (ratio={ratio:.3f}{trim_tag}, {size_kb}KB)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Stretch failed: {fmt_err_tail(e.stderr)}")
        return False
