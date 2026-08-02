"""step04_merge - Video merge entry point"""
import sys
import subprocess
from pathlib import Path
import os

# Ensure imports resolve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# Configuration
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))

# 输出目录（统一路径来源: shared/paths.py）
from shared.paths import chapter_dir, script_file, audio_dir, clips_dir, vimax_working_dir
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
AUDIO_PATH = audio_dir() / "narration.mp3"
AUDIO_SHOT_DIR = audio_dir()
FINAL_OUT_A = CHAPTER_DIR / "final-A.mp4"
FINAL_OUT_B = CHAPTER_DIR / "final-B.mp4"
SRT_OUT = CHAPTER_DIR / "subtitles.srt"
TEMP_DIR = CHAPTER_DIR / "stretched"

# 模块级日志
from shared.tee_logger import get_logger

# 编码预设（匹配 merge_core.py，默认 ultrafast）
try:
  from shared.config import MERGE_PRESET as _CFG_PRESET
  VIDEO_PRESET = _CFG_PRESET
except ImportError:
  VIDEO_PRESET = "ultrafast"

_logger = get_logger(CHAPTER_DIR / "step04.log", console_level="INFO")

# Video source
CLIP_SOURCE = os.getenv("CLIP_SOURCE", "vimax")
# Merge mode: stretch(音画同步) | concat(直接拼接) | native(纯视频拼接，语音由视频模型生成)
MERGE_MODE = os.getenv("MERGE_MODE", "stretch").strip().lower()
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
import re


def _parse_transition(text: str, default_dur: float = 0.5):
    """解析 clip_transition 字符串 → (kind, duration_seconds).

    kind: 'cut'(硬切) | 'fade'(交叉溶解) | 'fade_in'(淡入) | 'fade_out'(淡出)
    duration 从「转场时长0.5s/0.5秒」提取，clamp 到 0.1~1.5s（短转场防突兀）。
    """
    text = (text or "").strip()
    if not text:
        return "cut", 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*[秒s]", text)
    dur = float(m.group(1)) if m else default_dur
    # 是否显式提到转场（带「转场」字样或时长），用于区分「硬切」和「硬切+快速转场」
    has_transition_word = "转场" in text or m is not None
    if "淡入淡出" in text or "叠化" in text or "dissolve" in text.lower():
        kind = "fade"
    elif "淡入" in text:
        kind = "fade_in"
    elif "淡出" in text:
        kind = "fade_out"
    elif "硬切" in text or "cut" in text.lower():
        # 「硬切，战斗转场时长0.2s」→ 快速交叉溶解（时长即转场时间）
        # 纯「硬切」→ 无转场
        kind = "fade" if has_transition_word else "cut"
    else:
        kind = "fade"
    if kind == "cut":
        dur = 0.0
    else:
        dur = max(0.1, min(dur, 1.5))
    return kind, dur


def _build_native_transition_filter(clips, transitions, video_filter):
    """为 native 模式构建带转场的 filter_complex。

    视频：xfade（交叉溶解，要求各输入已归一化为同尺寸/帧率）
    音频：acrossfade（同步交叉淡化，避免硬切爆音）
    纯「硬切」：concat 直接拼接（不重叠）。

    Returns:
        (filter_complex, video_label, audio_label, total_duration, srt_durations)
        srt_durations 已按转场重叠量扣减，供字幕时间轴对齐。
    """
    durs = [get_media_duration(c) for c in clips]
    n = len(clips)
    parts = []

    # 归一化 + 截断每个输入（hook/shot1=704x1280@24，shot2+=720x1280@30，必须统一）
    for i, d in enumerate(durs):
        v_chain = (
            f"[{i}:v]fps=24,scale=720:1280:force_original_aspect_ratio=decrease,"
            f"pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
            f"trim=duration={d:.2f},setpts=PTS-STARTPTS,settb=AVTB"
        )
        # 首个镜头的「淡入」→ 从黑场淡入
        if i == 0 and transitions[0][0] == "fade_in":
            v_chain += f",fade=t=in:st=0:d={min(transitions[0][1], d):.2f}"
        parts.append(f"{v_chain}[nv{i}]")
        parts.append(
            f"[{i}:a]atrim=duration={d:.2f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=44100[na{i}]"
        )

    total = durs[0]
    cur_v, cur_a = "[nv0]", "[na0]"
    x = 0
    # transitions[i] 描述「从 clip i-1 进入 clip i」的转场
    for i in range(1, n):
        kind, tdur = transitions[i]
        if kind == "cut" or tdur <= 0.01:
            x += 1
            parts.append(f"{cur_v}[nv{i}]concat=n=2:v=1:a=0[vx{x}]")
            parts.append(f"{cur_a}[na{i}]concat=n=2:v=0:a=1[ax{x}]")
            cur_v, cur_a = f"[vx{x}]", f"[ax{x}]"
            total += durs[i]
        else:
            offset = max(0.0, total - tdur - 0.001)
            x += 1
            parts.append(
                f"{cur_v}[nv{i}]xfade=transition=fade:duration={tdur:.3f}:offset={offset:.3f}[vx{x}]"
            )
            parts.append(f"{cur_a}[na{i}]acrossfade=d={tdur:.3f}[ax{x}]")
            cur_v, cur_a = f"[vx{x}]", f"[ax{x}]"
            total = total + durs[i] - tdur

    # 最后一个镜头的「淡出」→ 末尾淡出到黑
    if transitions[n - 1][0] == "fade_out":
        fade_d = min(transitions[n - 1][1], durs[-1])
        parts.append(f"{cur_v}fade=t=out:st={max(0.0, total - fade_d):.2f}:d={fade_d:.2f}[nvout]")
        cur_v = "[nvout]"

    # 字幕+调色叠加在最终视频流上（与 filter_complex 同图，不能另用 -vf）
    parts.append(f"{cur_v}{video_filter}[outv]")

    # 字幕时长：首镜全量；后续每镜按转场重叠量扣减（xfade 时）
    srt_durations = [durs[0]]
    for i in range(1, n):
        kind, tdur = transitions[i]
        srt_durations.append(durs[i] if kind == "cut" else durs[i] - tdur)

    return ";".join(parts), "[outv]", cur_a, total, srt_durations


def main():
  _logger.separator("=", 60)
  _logger.info(" Step 4: Video Merge (Stretch + Concat + Subtitle + Color)")
  _logger.info(" Output: final-A.mp4 (TTS only) + final-B.mp4 (mixed)")
  _logger.separator("=", 60)

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

  _logger.info(f"[INFO] Loaded {len(shots)} shots")

  # 1.5. Check for hook video
  # Hook视频位置: clips/clip_0.mp4
  hook_clip = None
  hook = script.get("hook")
  if hook and isinstance(hook, dict):
    from shared.paths import clips_dir
    hook_clip_path = clips_dir() / "clip_0.mp4"

    if hook_clip_path.exists() and hook_clip_path.stat().st_size > 20000:
      hook_clip = hook_clip_path
      _logger.info(f"[INFO] Hook video found: {hook_clip_path}")
    else:
      _logger.info("[INFO] Hook video not found (skipping)")


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
      _logger.warn(f"[WARN] Video clip not found: {clip_path}, skip this shot")
      missing_idxs.append(idx)
      continue
    clips.append(clip_path)
    valid_shots.append(shot)

  if missing_idxs:
    _logger.warn(f"[WARN] Skipped {len(missing_idxs)} missing shots: {missing_idxs}")
  if not clips:
    raise FileNotFoundError(f"No valid video clips. Please run step02 first")

  _logger.info(f"[INFO] Video clips ready: {len(clips)}")

  # 3. Get target durations (audio durations)
  target_durations = [get_shot_target_duration(s) for s in valid_shots]
  total_target = sum(target_durations)
  _logger.info(f"[INFO] Target duration (audio): {total_target:.1f}s")

  # 4. Handle audio: prefer per-shot audio, fallback to narration
  # Native mode: skip all TTS, just concat videos directly
  if MERGE_MODE == "native":
    _logger.info("[INFO] MERGE_MODE=native, skipping TTS, direct video concat")
    # Use video durations directly as target
    target_durations = [get_media_duration(c) for c in clips]
    audio_duration = sum(target_durations)
    audio_path = None # No audio
    use_stretch = False
    use_per_shot_audio = False

    # 解析每个分镜的转场（native 模式下转场时长会重叠，字幕时长需同步扣减）
    native_transitions = [_parse_transition(s.get("clip_transition")) for s in valid_shots]
    srt_durations = [target_durations[0]]
    for i in range(1, len(target_durations)):
      kind, tdur = native_transitions[i]
      srt_durations.append(target_durations[i] if kind == "cut" else target_durations[i] - tdur)
    target_durations = srt_durations
    _logger.info(f"[INFO] Native transitions: {[(k, round(t, 2)) for k, t in native_transitions]}")
  else:
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
      _logger.info(f"[INFO] Using per-shot audio: {len(audio_paths)} clips, total {audio_duration:.1f}s")
      use_stretch = True
    else:
      # Fallback to narration
      if not AUDIO_PATH.exists():
        raise FileNotFoundError(f"Narration audio not found: {AUDIO_PATH}")
      audio_path = AUDIO_PATH
      audio_duration = get_media_duration(audio_path)
      _logger.info(f"[INFO] Using narration audio: {audio_duration:.1f}s")
      _logger.info(f"[INFO] No per-shot audio, skipping per-shot stretch")
      target_durations = [get_media_duration(c) for c in clips]
      use_stretch = False

  # 5. Generate SRT subtitles
  generate_srt(valid_shots, target_durations, SRT_OUT)

  # Extract Mode B indices from continuity metadata (step02 writes this)
  mode_b_indices = set()
  try:
    from step02_video_gen.continuity import ContinuityTracker
    meta = ContinuityTracker.load_meta(VIMAX_WD)
    if meta:
      shots_meta = meta.get("shots", {})
      mode_b_indices = {
        int(idx) for idx, mode in shots_meta.items() if mode == "B"
      }
    if mode_b_indices:
      _logger.info(f"[INFO] Mode B clips (will trim first frames): {sorted(mode_b_indices)}")
  except Exception:
    pass # continuity_meta.json may not exist (old runs), gracefully ignore

  # Clip transition info
  clip_transitions = [s.get("clip_transition", "cut") for s in valid_shots]
  _logger.info(f"[INFO] Transitions: {clip_transitions}")

  # Get chapter title
  chapter_title = os.getenv("CHAPTER_TITLE", "Chapter 1")

  # 6. Merge video - Version A: TTS only, Version B: Video audio + TTS mixed
  if use_stretch:
    # Version A: TTS only (discard video audio)
    _logger.separator("=", 60)
    _logger.info("[INFO] Generating version A: TTS audio only")
    _logger.separator("=", 60)
    merge_final_video(
      clips, target_durations,
      stretched_dir=TEMP_DIR,
      audio_path=audio_path,
      output_path=FINAL_OUT_A,
      srt_path=SRT_OUT,
      clip_transitions=clip_transitions,
      chapter_title=chapter_title,
      keep_video_audio=False,
      mode_b_indices=mode_b_indices,
    )

    # Version B: Video audio + TTS mixed
    _logger.separator("=", 60)
    _logger.info("[INFO] Generating version B: Video audio + TTS mixed")
    _logger.separator("=", 60)
    merge_final_video(
      clips, target_durations,
      stretched_dir=TEMP_DIR / "version_b",
      audio_path=audio_path,
      output_path=FINAL_OUT_B,
      srt_path=SRT_OUT,
      clip_transitions=clip_transitions,
      chapter_title=chapter_title,
      keep_video_audio=True,
      mode_b_indices=mode_b_indices,
    )
  else:
    # Old mode fallback (no per-shot audio, direct concat)
    _logger.separator("=", 60)
    _logger.info("[INFO] Using old mode merge (no per-shot audio, direct concat)")
    _logger.separator("=", 60)
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

    if audio_path is None:
      # Native mode - use xfade/acrossfade transition chain
      _logger.info("[INFO] Native mode: applying transitions + concat filter")
      filter_complex, v_label, a_label, _, _ = _build_native_transition_filter(
        clips, native_transitions, video_filter
      )

      # Build command with multiple inputs
      cmd = [FFMPEG, "-y"]
      for c in clips:
        cmd.extend(["-i", str(c)])
      cmd.extend([
        "-filter_complex", filter_complex,
        "-map", v_label, "-map", a_label,
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", "20",
        "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        str(FINAL_OUT_A),
      ])
      subprocess.run(cmd, check=True)
      _logger.info(f"[OK] Version A generated: {FINAL_OUT_A}")

      # Version B is same as A in native mode (no external audio to mix)
      FINAL_OUT_B = FINAL_OUT_A
    else:
      # Version A: TTS only
      cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", video_filter,
        "-af", "volume=5dB,aformat=sample_fmts=fltp:channel_layouts=stereo",
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", "20",
        "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44k", "-ac", "2",
        "-shortest",
        str(FINAL_OUT_A),
      ]
      subprocess.run(cmd, check=True)
      _logger.info(f"[OK] Version A generated: {FINAL_OUT_A}")

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
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", "20",
        "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44k", "-ac", "2",
        "-shortest",
        str(FINAL_OUT_B),
      ]
      subprocess.run(cmd, check=True)
      _logger.info(f"[OK] Version B generated: {FINAL_OUT_B}")

    if concat_list.exists():
      concat_list.unlink()

  # 7. Cleanup
  if use_per_shot_audio and 'merged_audio' in locals() and Path(merged_audio).exists():
    Path(merged_audio).unlink()

  # 8. Report
  duration_a = get_media_duration(FINAL_OUT_A)
  duration_b = get_media_duration(FINAL_OUT_B)
  _logger.info("[INFO] Merge Report:")
  if audio_path is None:
    _logger.info(f" Audio:       Original video audio (native mode)")
    _logger.info(f" Version A duration: {duration_a:.1f}s")
    _logger.info(f" Version B duration: {duration_b:.1f}s")
  else:
    _logger.info(f" Audio total:    {audio_duration:.1f}s")
    _logger.info(f" Version A duration: {duration_a:.1f}s (TTS only)")
    _logger.info(f" Version B duration: {duration_b:.1f}s (mixed)")
    _logger.info(f" A/V mismatch A:   {abs(duration_a - audio_duration):.1f}s")
    _logger.info(f" A/V mismatch B:   {abs(duration_b - audio_duration):.1f}s")


if __name__ == "__main__":
  try:
    main()
  except Exception as e:
    _logger.error(f"[ERROR] {e}")
    sys.exit(1)