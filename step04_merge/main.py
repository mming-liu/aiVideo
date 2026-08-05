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
from shared.paths import chapter_dir, script_file, clips_dir, vimax_working_dir
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
FINAL_OUT = CHAPTER_DIR / "final.mp4"
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
# 合并模式：2026-08 起仅 native（纯视频拼接，语音/背景音由视频模型生成，保留视频原声）
VIMAX_WD = vimax_working_dir()
if CLIP_SOURCE == "vimax":
  CLIP_DIR = VIMAX_WD / "shots"
else:
  CLIP_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", str(clips_dir())))

DEBUG_SKIP_CACHE = os.getenv("DEBUG_SKIP_CACHE", "0").strip() == "1"

# Submodule imports
from step04_merge.stretch import get_media_duration
from step04_merge.srt_gen import generate_srt
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
  _logger.info(" Output: final.mp4 (native, 语音/背景音由视频模型生成)")
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

  # 3. Get target durations (视频模型生成，native 模式直接以视频实际时长为准)
  target_durations = [get_media_duration(c) for c in clips]
  audio_duration = sum(target_durations)
  _logger.info(f"[INFO] Total video duration: {audio_duration:.1f}s")

  # 4. Native merge（唯一模式，2026-08 起删除版本A/B双输出与TTS混音路径）：
  #    对话/背景音由视频模型生成，直接拼接保留每段视频原声。
  _logger.info("[INFO] Native merge: 直接拼接视频并保留原声（语音由视频模型生成）")

  # 解析每个分镜的转场（native 模式下转场时长会重叠，字幕时长需同步扣减）
  native_transitions = [_parse_transition(s.get("clip_transition")) for s in valid_shots]
  srt_durations = [target_durations[0]]
  for i in range(1, len(target_durations)):
    kind, tdur = native_transitions[i]
    srt_durations.append(target_durations[i] if kind == "cut" else target_durations[i] - tdur)
  target_durations = srt_durations
  _logger.info(f"[INFO] Native transitions: {[(k, round(t, 2)) for k, t in native_transitions]}")

  # 5. Generate SRT subtitles
  generate_srt(valid_shots, target_durations, SRT_OUT)

  # 6. Merge video - native（唯一模式）：直接拼接保留视频原声
  _logger.separator("=", 60)
  _logger.info("[INFO] Native merge: 转场 + 拼接 + 字幕 + 调色（保留视频原声）")
  _logger.separator("=", 60)
  from step04_merge.merge_core import _run_cmd, _find_exe
  FFMPEG = _find_exe("ffmpeg")

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
    str(FINAL_OUT),
  ])
  subprocess.run(cmd, check=True)
  _logger.info(f"[OK] Final video generated: {FINAL_OUT}")

  # 7. Report
  duration = get_media_duration(FINAL_OUT)
  _logger.info("[INFO] Merge Report:")
  _logger.info(f" Audio:       Original video audio (native mode, 语音由视频模型生成)")
  _logger.info(f" Final duration: {duration:.1f}s")
  _logger.info(f" Total video duration: {audio_duration:.1f}s")


if __name__ == "__main__":
  try:
    main()
  except Exception as e:
    _logger.error(f"[ERROR] {e}")
    sys.exit(1)