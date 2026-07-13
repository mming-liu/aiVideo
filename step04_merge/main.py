"""step04_merge - Video merge entry point"""
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
AUDIO_PATH = CHAPTER_DIR / "audio" / "narration.mp3"
AUDIO_SHOT_DIR = CHAPTER_DIR / "audio"
FINAL_OUT = CHAPTER_DIR / "final.mp4"
SRT_OUT = CHAPTER_DIR / "subtitles.srt"
TEMP_DIR = CHAPTER_DIR / "stretched"

# Video source
CLIP_SOURCE = os.getenv("CLIP_SOURCE", "vimax")
VIMAX_WD = Path(os.getenv("VIMAX_WORKING_DIR", str(CHAPTER_DIR / "vimax_working")))
if CLIP_SOURCE == "vimax":
    CLIP_DIR = VIMAX_WD / "shots"
else:
    CLIP_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "./output/clips"))

DEBUG_SKIP_CACHE = os.getenv("DEBUG_SKIP_CACHE", "0").strip() == "1"

# Submodule imports
from step04_merge.stretch import stretch_clip, get_media_duration
from step04_merge.srt_gen import generate_srt, get_shot_target_duration, find_shot_audio, seconds_to_srt_time
from step04_merge.merge_core import merge_final_video, concat_audio_files
import json


def main():
    print("=" * 60)
    print("  Step 4: Video Merge (Stretch + Concat + Subtitle + Color)")
    print("=" * 60)

    # 检查前置步骤产物
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {SCRIPT_FILE}\n"
            f"请先运行 step01: python step01_script_gen/main.py"
        )

    if CLIP_SOURCE == "vimax":
        if not VIMAX_WD.exists():
            raise FileNotFoundError(
                f"ViMax 工作目录不存在: {VIMAX_WD}\n"
                f"请先运行 step02: python step02_video_gen/main.py"
            )
        if not CLIP_DIR.exists():
            raise FileNotFoundError(
                f"视频片段目录不存在: {CLIP_DIR}\n"
                f"请先运行 step02 生成视频片段"
            )
    else:
        if not CLIP_DIR.exists():
            raise FileNotFoundError(
                f"视频片段目录不存在: {CLIP_DIR}\n"
                f"请先运行 step02 生成视频片段"
            )

    # Ensure output directories exist
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load script
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Script not found: {SCRIPT_FILE}\nPlease run step01 first")

    with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
        script = json.load(f)

    shots = script.get("shots", [])
    if not shots:
        raise ValueError("No shots in script")

    print(f"[INFO] Loaded {len(shots)} shots")

    # 2. Collect video clips
    clips = []
    valid_shots = []
    missing_idxs = []

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

    # 提取转场信息
    clip_transitions = [s.get("clip_transition", "硬切 cut") for s in valid_shots]
    print(f"[INFO] Transitions: {clip_transitions}")

    # 获取章节标题（从环境变量或使用默认值）
    chapter_title = os.getenv("CHAPTER_TITLE", "第一章")

    # 6. Merge video
    if use_stretch:
        merge_final_video(
            clips, target_durations,
            stretched_dir=TEMP_DIR,
            audio_path=audio_path,
            output_path=FINAL_OUT,
            srt_path=SRT_OUT,
            clip_transitions=clip_transitions,
            chapter_title=chapter_title,
        )
    else:
        # Old mode fallback
        print(f"\n{'='*60}")
        print("[INFO] Using old mode merge (no per-shot audio, direct concat)")
        print(f"{'='*60}\n")
        # For simplicity, just call merge with stretch=False logic
        from step04_merge.merge_core import concat_clips, _run__cmd, _find_exe
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
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", video_filter,
            "-af", "volume=5dB",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(FINAL_OUT),
        ]
        subprocess.run(cmd, check=True)
        if concat_list.exists():
            concat_list.unlink()
        if FINAL_OUT.exists():
            print(f"\n[OK] Final video generated: {FINAL_OUT}")

    # 7. Cleanup
    if use_per_shot_audio and 'merged_audio' in locals() and Path(merged_audio).exists():
        Path(merged_audio).unlink()

    # 8. Report
    final_duration = get_media_duration(FINAL_OUT)
    print("\n[INFO] Merge Report:")
    print(f"  Audio total:     {audio_duration:.1f}s")
    print(f"  Final duration:  {final_duration:.1f}s")
    mismatch = abs(final_duration - audio_duration)
    print(f"  A/V mismatch:    {mismatch:.1f}s (ideal: <0.1s)")
    if use_stretch:
        # 检查是否因 clamp 导致 mismatch（STRETCH_RATIO_MAX = 2.5）
        max_ratio = float(os.getenv("STRETCH_RAIO_MAX", "2.5"))
        any_clamped = False
        for orig, target in zip([get_media_duration(c) for c in clips], target_durations):
            if orig > 0:
                ratio = target / orig
                if ratio > max_ratio:
                    any_clamped = True
                    break
        if any_clamped or mismatch > 1.0:
            print(f"  [WARN] 视频被强制拉伸（>2.5x），画面可能变形")
            print(f"         建议：回到 step02 重新生成匹配音频时长的视频")
        else:
            print(f"  [OK] Video stretched to match audio")
    else:
        if mismatch < 1:
            print(f"  [OK] A/V aligned (-shortest mode)")
        else:
            print(f"  [WARN] Large A/V mismatch, recommend per-shot audio mode")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)