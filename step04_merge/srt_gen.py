"""step04_merge.srt_gen - SRT subtitle generation module"""
import sys
from pathlib import Path
import os

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from typing import List


def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def get_shot_target_duration(shot: dict) -> float:
    """Get shot target duration - audio duration. Video will be stretched to this.

    Priority: actual_audio_duration (TTS measured) > duration (text proportion estimate)
    """
    audio = shot.get("actual_audio_duration")
    if audio and audio > 0:
        return float(audio)
    return shot.get("duration", 5)


def find_shot_audio(shot_idx: int, audio_dir: Path):
    """Find audio file for a shot."""
    audio_path = audio_dir / f"shot_{shot_idx}.mp3"
    if audio_path.exists():
        return audio_path
    return None


def generate_srt(shots: List[dict], durations: List[float], output: Path) -> None:
    """Generate SRT subtitle file based on per-shot target durations."""
    output.parent.mkdir(parents=True, exist_ok=True)

    current_time = 0.0
    srt_lines = []

    for i, (shot, dur) in enumerate(zip(shots, durations), 1):
        subtitle = shot.get("subtitle", "").strip()
        if not subtitle:
            continue

        # Subtitle duration = shot target duration (audio duration)
        start = current_time
        end = current_time + dur
        srt_lines.append(str(i))
        srt_lines.append(f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}")
        srt_lines.append(subtitle)
        srt_lines.append("")
        current_time = end

    output.write_text("\n".join(srt_lines), encoding="utf-8")
    print(f"[OK] SRT generated: {output} ({len(shots)} subtitles, total {current_time:.1f}s)")