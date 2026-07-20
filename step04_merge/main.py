"""step04_merge - Video merge entry point"""
import sys
import subprocess
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

# 输出目录（统一路径来源: shared/paths.py）
from shared.paths import chapter_dir, script_file, audio_dir, clips_dir, vimax_working_dir
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
AUDIO_PATH = audio_dir() / "narration.mp3"
AUDIO_SHOT_DIR = audio_dir()
FINAL_OUT_A = CHAPTER_DIR / "final-A.mp4"  # TTS only
FINAL_OUT_B = CHAPTER_DIR / "final-B.mp4"  # Video audio + TTS mixed
SRT_OUT = CHAPTER_DIR / "subtitles.srt"
TEMP_DIR = CHAPTER_DIR / "stretched"

# Video source
CLIP_SOURCE = os.getenv("CLIP_SOURCE", "vimax")
VIMAX_WD = vimax_working_dir()
if CLIP_SOURCE == "vimax":
    CLIP_DIR = VIMAX_WD / "shots"
else:
    CLIP_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", str(clips_dir())))

DEBUG_SKIP_CACHE = os.getenv("DEBUG_SKIP_CACHE", "0").strip() == "1"

# Submodule imports
from step04_merge.stretch import stretch_clip, get_media_duration
from step04_merge.srt_gen import generate_srt, get_shot_target_duration, find_shot_audio, seconds_to_srt_time
from step04_merge.merge_core import merge_final_video, concat_audio_files
import json


def main():
    print("=" * 60)
    print("  Step 4: Video Merge (Stretch + Concat + Subtitle + Color)")
    print("  Output: final-A.mp4 (TTS only) + final-B.mp4 (mixed)")
    print("=" * 60)

    # Check prerequisites
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(
            f"Script not found: {SCRIPT_FILE}\n"
            f"Please run step01 first"
        )

    if CLIP_SOURCE == "vimax":
        if not VIMAX_WD.exists():
            raise FileNotFoundError(
                f"ViMax working dir not found: {VIMAX_WD}\n"
                f"Please run step02 first"
            )
        if not CLIP_DIR.exists():
            raise FileNotFoundError(
                f"Video clips dir not found: {CLIP_DIR}\n"
                f"Please run step02 to generate clips"
            )
    else:
        if not CLIP_DIR.exists():
            raise FileNotFoundError(
                f"Video clips dir not found: {CLIP_DIR}\n"
                f"Please run step02 first"
            )

    # Ensure output directories exist
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load script
    with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
        script = json.load(f)

    shots = script.get("shots", [])
    if not shots:
        raise ValueError("No shots in script")

    print(f"[INFO] Loaded {len(shots)} shots")

    # 1.5. Check for hook video
    hook_clip = None
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        hook_clip_path = CLIP_DIR / "0" / "video.mp4"
        if hook_clip_path.exists() and hook_clip_path.stat().st_size > 20 * 1024:
            hook_clip = hook_clip_path
            print(f"[INFO] Hook video found: {hook_clip_path}")
        else:
            print(f"[INFO] Hook video not found (skipping)")

    # 2. Collect video clips
    clips = []
    valid_shots = []
    missing_idxs = []

    # Hook goes first
    if hook_clip:
        clips.append(hook_clip)
        # Create a pseudo-shot for the hook so that SRT/subtitle handling works
        hook_shot = dict(hook)
        hook_shot["index"] = 0
        valid_shots.append(hook_shot)

    for shot in shots:
        idx = shot.get("index", 0)
        clip_path = CLIP_DIR / str(idx) / "video.mp4"
        if not clip_path.exists():
            print(f"[WARN] Video clip not found: {clip_path}, skip this shot")
            missing_idxs.append(idx)
            continue
        clips.append(clip_path)
        valid_shots.append(shot)

    if missing_idxs:
        print(f"[WARN] Skipped {len(missing_idxs)} missing shots: {missing_idxs}")
    if not clips:
        raise FileNotFoundError(f"No valid video clips. Please run step02 first")

    print(f"[INFO] Video clips ready: {len(clips)}")

    # 3. Get target durations (audio durations)
    target_durations = [get_shot_target_duration(s) for s in valid_shots]
    total_target = sum(target_durations)
    print(f"[INFO] Target duration (audio): {total_target:.1f}s")

    # 4. Handle audio: prefer per-shot audio, fallback to narration
    audio_path = None
    use_per_shot_audio = True

    for shot in valid_shots:
        idx = shot.get("index")
        audio = find_shot_audio(idx, AUDIO_SHOT_DIR)
        if audio is None:
            use_per_shot_audio = False
            break

    if use_per_shot_audio:
        # Merge per-shot audio
        audio_paths = [find_shot_audio(s.get("index"), AUDIO_SHOT_DIR) for s in valid_shots]
        merged_audio = TEMP_DIR / "merged_audio.mp3"
        concat_audio_files(audio_paths, merged_audio)
        audio_path = merged_audio
        audio_duration = get_media_duration(audio_path)
        print(f"[INFO] Using per-shot audio: {len(audio_paths)} clips, total {audio_duration:.1f}s")
        use_stretch = True
    else:
        # Fallback to narration
        if not AUDIO_PATH.exists():
            raise FileNotFoundError(f"Narration audio not found: {AUDIO_PATH}")
        audio_path = AUDIO_PATH
        audio_duration = get_media_duration(audio_path)
        print(f"[INFO] Using narration audio: {audio_duration:.1f}s")
        print(f"[INFO] No per-shot audio, skipping per-shot stretch")
        target_durations = [get_media_duration(c) for c in clips]
        use_stretch = False

    # 5. Generate SRT subtitles
    generate_srt(valid_shots, target_durations, SRT_OUT)

    # Extract transition info
    clip_transitions = [s.get("clip_transition", "cut") for s in valid_shots]
    print(f"[INFO] Transitions: {clip_transitions}")

    # Get chapter title
    chapter_title = os.getenv("CHAPTER_TITLE", "Chapter 1")

    # 6. Merge video - Version A: TTS only, Version B: Video audio + TTS mixed
    if use_stretch:
        # Version A: TTS only (discard video audio)
        print(f"\n{'='*60}")
        print("[INFO] Generating version A: TTS audio only")
        print(f"{'='*60}\n")
        merge_final_video(
            clips, target_durations,
            stretched_dir=TEMP_DIR,
            audio_path=audio_path,
            output_path=FINAL_OUT_A,
            srt_path=SRT_OUT,
            clip_transitions=clip_transitions,
            chapter_title=chapter_title,
            keep_video_audio=False,
        )

        # Version B: Video audio + TTS mixed
        print(f"\n{'='*60}")
        print("[INFO] Generating version B: Video audio + TTS mixed")
        print(f"{'='*60}\n")
        merge_final_video(
            clips, target_durations,
            stretched_dir=TEMP_DIR / "version_b",
            audio_path=audio_path,
            output_path=FINAL_OUT_B,
            srt_path=SRT_OUT,
            clip_transitions=clip_transitions,
            chapter_title=chapter_title,
            keep_video_audio=True,
        )
    else:
        # Old mode fallback (no per-shot audio, direct concat)
        print(f"\n{'='*60}")
        print("[INFO] Using old mode merge (no per-shot audio, direct concat)")
        print(f"{'='*60}\n")
        from step04_merge.merge_core import concat_clips, _run_cmd, _find_exe
        FFMPEG = _find_exe("ffmpeg")

        # Concat clips directly
        concat_list = TEMP_DIR / "merge_concat.txt"
        concat_list.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"file '{c.resolve().as_posix()}'" for c in clips]
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        srt_path_abs = SRT_OUT.resolve().as_posix()
        escaped_srt = srt_path_abs.replace(":", "\\:")
        style = (
            f"FontName=Sans"
            f"\\,FontSize=20"
            f"\\,PrimaryColour=&H00FFFFFF"
            f"\\,OutlineColour=&H00FFFFFF"
            f"\\,Outline=1"
        )
        video_filter = (
            f"subtitles='{escaped_srt}':force_style='{style}',"
            f"eq=contrast=1.15:saturation=0.85,"
            f"vignette=angle=0.5"
        )

        # Version A: TTS only
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", video_filter,
            "-af", "volume=5dB,aformat=sample_fmts=fltp:channel_layouts=stereo",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44k", "-ac", "2",
            "-shortest",
            str(FINAL_OUT_A),
        ]
        subprocess.run(cmd, check=True)
        print(f"\n[OK] Version A generated: {FINAL_OUT_A}")

        # Version B: Video audio + TTS mixed
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "0:a:0", "-map", "1:a:0",
            "-vf", video_filter,
            "-filter_complex",
            # 处理视频音频：降低音量 + 格式化
            "[0:a]volume=0.3,aformat=sample_fmts=fltp:channel_layouts=stereo[bg];"
            # 处理 TTS 音频：格式化
            "[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo[tts];"
            # 混合
            "[bg][tts]amix=inputs=2:duration=first[audio]",
            "-map", "0:v:0", "-map", "[audio]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44k", "-ac", "2",
            "-shortest",
            str(FINAL_OUT_B),
        ]
        subprocess.run(cmd, check=True)
        print(f"[OK] Version B generated: {FINAL_OUT_B}")

        if concat_list.exists():
            concat_list.unlink()

    # 7. Cleanup
    if use_per_shot_audio and 'merged_audio' in locals() and Path(merged_audio).exists():
        Path(merged_audio).unlink()

    # 8. Report
    duration_a = get_media_duration(FINAL_OUT_A)
    duration_b = get_media_duration(FINAL_OUT_B)
    print("\n[INFO] Merge Report:")
    print(f"  Audio total:        {audio_duration:.1f}s")
    print(f"  Version A duration: {duration_a:.1f}s (TTS only)")
    print(f"  Version B duration: {duration_b:.1f}s (mixed)")
    print(f"  A/V mismatch A:     {abs(duration_a - audio_duration):.1f}s")
    print(f"  A/V mismatch B:     {abs(duration_b - audio_duration):.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)