"""step04_merge.merge_core - Core merge module with stretch, concat, and filter chain"""
import sys
from pathlib import Path
import os
import subprocess
import json

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from typing import List, Tuple, Optional
from step04_merge.stretch import stretch_clip, get_media_duration

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

# Color grading parameters
CONTRAST = float(os.getenv("FFMPEG_CONTRAST", "1.15"))
SATURATION = float(os.getenv("FFMPEG_SATURATION", "0.85"))
VIGNETTE_ANGLE = float(os.getenv("FFMPEG_VIGNETTE_ANGLE", "0.5"))
AUDIO_VOLUME = os.getenv("FFMPEG_AUDIO_VOLUME", "5dB")

# Subtitle style
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Sans")
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "20"))
SUBTITLE_COLOR = os.getenv("SUBTITLE_COLOR", "&H00FFFFFF")
SUBTITLE_OUTLINE_COLOR = os.getenv("SUBTITLE_OUTLINE_COLOR", "&H00FFFFFF")
SUBTITLE_OUTLINE_WIDTH = int(os.getenv("SUBTITLE_OUTLINE_WIDTH", "1"))

DEBUG_SKIP_CACHE = os.getenv("DEBUG_SKIP_CACHE", "0").strip() == "1"


def _run_cmd(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Execute shell command and print."""
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def concat_clips(clips: List[Path], output_path: Path) -> None:
    """Concat multiple video clips using ffmpeg (-c copy, no re-encode)."""
    list_path = output_path.parent / "concat_list_stretched.txt"
    lines = [f"file '{c.resolve().as_posix()}'" for c in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(output_path),
    ]
    print(f"  [CONCAT] Concatenating {len(clips)} stretched videos")
    try:
        _run_cmd(cmd)
    finally:
        if list_path.exists():
            list_path.unlink()


def concat_audio_files(audio_paths: List[Path], output_path: Path) -> Path:
    """Merge multiple audio files using ffmpeg."""
    list_file = output_path.parent / "audio_list.txt"
    lines = [f"file '{p.resolve()}'" for p in audio_paths]
    list_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path)
    ]
    print(f"  [CONCAT] Merging {len(audio_paths)} audio files")
    result = subprocess.run(cmd, capture_output=True, text=True)
    list_file.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"Audio merge failed:\n{result.stderr}")
    return output_path


def merge_final_video(
    clips: List[Path],
    shot_audio_durations: List[float],
    stretched_dir: Path,
    audio_path: Path,
    output_path: Path,
    srt_path: Path,
) -> None:
    """Per-shot stretch to audio duration -> concat -> subtitle + color + vignette + mix.

    Core improvement: No more -shortest truncation. Each video clip is stretched
    via setpts to match its audio duration for zero-deviation audio-video alignment.
    """
    # 1. Stretch each video clip
    stretched_clips = []
    stretched_dir.mkdir(parents=True, exist_ok=True)

    for i, (clip, target_dur) in enumerate(zip(clips, shot_audio_durations)):
        stretched_path = stretched_dir / f"stretched_{i:04d}.mp4"
        if stretched_path.exists() and not DEBUG_SKIP_CACHE:
            cached_dur = get_media_duration(stretched_path)
            if abs(cached_dur - target_dur) < 0.1:
                stretched_clips.append(stretched_path)
                continue
            stretched_path.unlink()

        if not stretch_clip(clip, target_dur, stretched_path):
            print(f"  [WARN] Shot {i+1} stretch failed, use original clip")
            stretched_clips.append(clip)
        else:
            stretched_clips.append(stretched_path)

    # 2. Concat stretched clips
    concat_video = stretched_dir / "concat_video.mp4"
    if concat_video.exists() and not DEBUG_SKIP_CACHE:
        print(f"  [CACHE] Concat video cached: {concat_video}")
    else:
        concat_clips(stretched_clips, concat_video)

    # 3. Final merge (subtitle + color + vignette + mix)
    srt_path_abs = srt_path.resolve().as_posix()
    escaped_srt = srt_path_abs.replace(":", "\\:")
    style = (
        f"FontName={SUBTITLE_FONT}"
        f"\\,FontSize={SUBTITLE_FONT_SIZE}"
        f"\\,PrimaryColour={SUBTITLE_COLOR}"
        f"\\,OutlineColour={SUBTITLE_OUTLINE_COLOR}"
        f"\\,Outline={SUBTITLE_OUTLINE_WIDTH}"
    )
    video_filter = (
        f"subtitles='{escaped_srt}':force_style='{style}',"
        f"eq=contrast={CONTRAST}:saturation={SATURATION},"
        f"vignette=angle={VIGNETTE_ANGLE}"
    )

    cmd = [
        FFMPEG, "-y",
        "-i", str(concat_video),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf", video_filter,
        "-af", f"volume={AUDIO_VOLUME}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ]

    print(f"\n{'='*60}")
    print("[INFO] Starting final merge...")
    print(f"[INFO] Video clips: {len(clips)}")
    print(f"[INFO] Audio file: {audio_path}")
    print(f"[INFO] Subtitle file: {srt_path}")
    print(f"[INFO] Final output: {output_path}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[ERROR] ffmpeg failed:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg returned code {result.returncode}")

    if output_path.exists():
        size_mb = output_path.stat().st_size / (10 * 10240 * 10240)
        print(f"\n[OK] Final video generated: {output_path} ({size_mb:.1f} MB)")
    else:
        raise RuntimeError(f"Output file not generated: {output_path}")