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
import os


# v6.22：音频音量归一（FFMPEG_AUDIO_VOLUME 环境变量，如 5dB/0.8；默认空=不处理）
_AUDIO_VOLUME = os.getenv("FFMPEG_AUDIO_VOLUME", "").strip()


# v6.22：多转场映射表（clip_transition 文本关键词 → xfade transition 类型）
_TRANSITION_MAP = (
    # 黑场闪白 / 冲击转场
    ("黑场闪白", "fadeblack"),
    ("画面冲击", "fadeblack"),
    ("冲击转场", "fadeblack"),
    ("光线闪白", "fadewhite"),
    ("闪白", "fadewhite"),
    # 推移 / 推镜 / 拉远
    ("推移转场", "slideleft"),
    ("推近匹配", "smoothleft"),
    ("拉远揭示", "smoothright"),
    ("推移", "slideleft"),
    # 擦拭 / 扫光
    ("擦拭", "wipeleft"),
    ("扫光", "wipeleft"),
    ("物体擦拭", "wipeup"),
    # 平滑 / 柔化 / 虚化
    ("焦点虚化", "smoothup"),
    ("模糊叠化", "smoothdown"),
    ("平滑", "smoothleft"),
    ("柔化", "smoothdown"),
    # 旋转 / 径向
    ("旋转", "radial"),
    ("光圈", "radial"),
    ("径向", "radial"),
    # 叠化 / 溶解
    ("叠化", "fade"),
    ("dissolve", "fade"),
    ("淡入淡出", "fade"),
    ("交叉溶解", "fade"),
    # 匹配 / 空镜
    ("空镜匹配", "fade"),
    ("匹配剪辑", "fade"),
    ("动作匹配", "fade"),
    # 淡入 / 淡出
    ("淡入", "fade_in"),
    ("淡出", "fade_out"),
    # 硬切 / 跳跃
    ("硬切", "cut"),
    ("jump", "cut"),
    ("跳跃剪辑", "cut"),
)

# 可用的 xfade transition 白名单（校验 kind 合法，非法回退 fade）
_VALID_XFADE = {
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
}


def _parse_transition(text: str, default_dur: float = 0.5):
    """解析 clip_transition 字符串 → (kind, duration_seconds).

    kind: 'cut'(硬切) | 'fade_in'(淡入) | 'fade_out'(淡出) | xfade 转场名
    （fade/wipeleft/slideleft/fadeblack/radial... 多转场增强 v6.22）
    duration 从「转场时长0.5s/0.5秒」提取，clamp 到 0.1~1.5s（短转场防突兀）。
    """
    text = (text or "").strip()
    if not text:
        return "cut", 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*[秒s]", text)
    dur = float(m.group(1)) if m else default_dur
    # 是否显式提到转场（带「转场」字样或时长），用于区分「硬切」和「硬切+快速转场」
    has_transition_word = "转场" in text or m is not None

    # v6.22：多转场映射（长词优先）——黑场闪白/冲击→fadeblack、推移→slide、擦拭→wipe、
    # 旋转→radial、平滑→smooth、光线闪白→fadewhite，其余叠化/淡入淡出→fade
    kind = None
    for kw, xf in _TRANSITION_MAP:
        if kw in text:
            kind = xf
            break
    if kind is None:
        kind = "fade"
    # 纯「硬切」（无转场时长）→ cut
    if "硬切" in text and not has_transition_word:
        kind = "cut"
    # 「硬切，战斗转场时长0.2s」→ 快速交叉溶解（保留 fade）
    elif kind == "cut" and has_transition_word:
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
        # v6.22：音频音量归一（读取 FFMPEG_AUDIO_VOLUME，如 5dB/0.8；默认不处理）
        _a_chain = (
            f"[{i}:a]atrim=duration={d:.2f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=44100"
        )
        if _AUDIO_VOLUME:
            _a_chain += f",volume={_AUDIO_VOLUME}"
        parts.append(f"{_a_chain}[na{i}]")

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
            # v6.22：xfade transition 类型动态（fade/wipeleft/slideleft/fadeblack...），
            # kind 直接作 xfade transition 参数；非法回退 fade
            xfade_type = kind if kind in _VALID_XFADE else "fade"
            parts.append(
                f"{cur_v}[nv{i}]xfade=transition={xfade_type}:duration={tdur:.3f}:offset={offset:.3f}[vx{x}]"
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
    # v6.22：按 CLIP_SOURCE 分支处理 clip 路径——vimax 用 shots/{idx}/video.mp4，
    # 非 vimax（agens-clips 等）用扁平 clip_{idx}.mp4（此前硬编码 vimax 结构导致
    # 非 vimax 时合并路径不可用，code-review-2026-08-06 #10）
    if CLIP_SOURCE == "vimax":
      clip_path = CLIP_DIR / str(idx) / "video.mp4"
    else:
      clip_path = CLIP_DIR / f"clip_{idx}.mp4"
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
  zero_durations = [(i, str(c)) for i, c in enumerate(clips) if target_durations[i] <= 0]
  if zero_durations:
    # M9 修复 2026-08-14：时长探测失败不再静默——明确告警，字幕时间轴将受影响
    _logger.warn(f"[WARN] {len(zero_durations)} 个片段时长探测失败（为0），"
                 f"字幕时间轴可能错位: {[c for _, c in zero_durations]}")
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
  # v6.22：调色配置化（读取 merge_core 的环境变量，替代硬编码）
  from step04_merge.merge_core import CONTRAST, SATURATION, VIGNETTE_ANGLE
  color_filter = f"eq=contrast={CONTRAST}:saturation={SATURATION}"
  if VIGNETTE_ANGLE:
    color_filter += f",vignette=angle={VIGNETTE_ANGLE}"
  video_filter = (
    f"subtitles='{escaped_srt}':force_style='{style}',"
    f"{color_filter}"
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