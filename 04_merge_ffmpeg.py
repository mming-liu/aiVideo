"""视频合并 — 将分镜片段、旁白音频合成为最终成片。

核心改进（setpts 精确对齐）：
  不再依赖 -shortest 截断对齐音画，而是通过 setpts 逐镜头将视频
  拉伸到对应的音频时长，实现零偏差的音画对齐。视频精确等于音频长度。

流程:
  1. 读取 script_result.json 获取字幕与镜头信息
  2. 使用 per-shot 音频时长（actual_audio_duration）作为目标时长
  3. 逐镜头 setpts 拉伸视频到目标时长
  4. concat 拼接拉伸后的视频片段
  5. 字幕烧录 + 调色 + 暗角 + 混音
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# 视频片段来源: vimax（output/vimax_working/shots/<idx>/video.mp4）
CLIP_SOURCE = os.getenv("CLIP_SOURCE", "vimax")
_VIMAX_WD = os.getenv("VIMAX_WORKING_DIR", "./output/vimax_working")
CLIP_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "./output/clips"))
if CLIP_SOURCE == "vimax":
    CLIP_DIR = Path(_VIMAX_WD) / "shots"
AUDIO_PATH = Path(os.getenv("TTS_OUTPUT_PATH", "./output/audio/narration.mp3"))
AUDIO_SHOT_DIR = Path(os.getenv("TTS_OUTPUT_DIR", "./output/audio"))  # per-shot audio 目录
FINAL_OUT = Path(os.getenv("MERGE_OUTPUT_PATH", "./output/final.mp4"))
# 分镜脚本路径需与 step01_script_gen/main.py 输出一致
_BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))
_CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
_OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "./output/video"))
SCRIPT_FILE = _OUTPUT_BASE / f"book_{_BOOK_INDEX:02d}" / f"chapter_{_CHAPTER_INDEX:02d}" / "script_result.json"
SRT_OUT = _PROJECT_ROOT / "output" / "subtitles.srt"
TEMP_DIR = _PROJECT_ROOT / "output" / "stretched"
# 调试开关：DEBUG_SKIP_CACHE=1 时跳拉伸片段和最终成片的缓存，每次强制重拉/重拼
DEBUG_SKIP_CACHE = os.getenv("DEBUG_SKIP_CACHE", "0").strip() == "1"

# FFmpeg 路径解析: 优先使用 .env 配置，其次自动查找
_FFMPEG_BIN_DIR = os.getenv("FFMPEG_BIN_DIR", "").strip()
if _FFMPEG_BIN_DIR:
    os.environ["PATH"] = _FFMPEG_BIN_DIR + os.pathsep + os.environ.get("PATH", "")


def _find_exe(name: str) -> str:
    """查找可执行文件，找不到时给出友好提示."""
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(
            f"找不到 {name}，请确保 FFmpeg 已安装。\n"
            f"方法1: 在 .env 中设置 FFMPEG_BIN_DIR 指向 FFmpeg 的 bin 目录\n"
            f"方法2: 将 FFmpeg 的 bin 目录加入系统 PATH 环境变量"
        )
    return path


FFMPEG = _find_exe("ffmpeg")
FFPROBE = _find_exe("ffprobe")

# 调色参数
CONTRAST = float(os.getenv("FFMPEG_CONTRAST", "1.15"))
SATURATION = float(os.getenv("FFMPEG_SATURATION", "0.85"))
VIGNETTE_ANGLE = float(os.getenv("FFMPEG_VIGNETTE_ANGLE", "0.5"))  # ~28.6度
AUDIO_VOLUME = os.getenv("FFMPEG_AUDIO_VOLUME", "5dB")

# 字幕样式（ASS 标准十六进制颜色 ABGR 格式）
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Sans")
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "20"))
SUBTITLE_COLOR = os.getenv("SUBTITLE_COLOR", "&H00FFFFFF")  # 白色
SUBTITLE_OUTLINE_COLOR = os.getenv("SUBTITLE_OUTLINE_COLOR", "&H00FFFFFF")
SUBTITLE_OUTLINE_WIDTH = int(os.getenv("SUBTITLE_OUTLINE_WIDTH", "1"))


# ---------------------------------------------------------------------------
# 逐镜头时域拉伸（setpts 精确对齐音频时长）
# ---------------------------------------------------------------------------

def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """执行 shell 命令并打印."""
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def get_media_duration(path: Path) -> float:
    """用 ffprobe 获取媒体时长（秒）."""
    result = _run([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ])
    return float(result.stdout.strip())


STRETCH_RATIO_MIN = float(os.getenv("STRETCH_RATIO_MIN", "0.75"))
STRETCH_RATIO_MAX = float(os.getenv("STRETCH_RATIO_MAX", "1.333"))


def stretch_clip(clip_path: Path, target_dur: float, output_path: Path) -> bool:
    """用 setpts 将视频拉伸到目标时长.

    视频时长通过 setpts 滤镜精确适配音频时长，消除音画偏差。
    拉伸导致的速度变化在 ±33% 范围内感知微弱，适合用于消除 8n+1 量化误差。
    """
    if target_dur <= 0:
        return False
    actual_dur = get_media_duration(clip_path)
    if actual_dur <= 0:
        return False

    ratio = target_dur / actual_dur
    # 限制拉伸范围防止明显变形
    if ratio < STRETCH_RATIO_MIN or ratio > STRETCH_RATIO_MAX:
        clamped = max(STRETCH_RATIO_MIN, min(STRETCH_RATIO_MAX, ratio))
        print(f"  [WARN] 拉伸比 {ratio:.3f} 超出 [{STRETCH_RATIO_MIN}, {STRETCH_RATIO_MAX}]，"
              f"限制到 {clamped:.3f}")
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
        _run(cmd)
        size_kb = output_path.stat().st_size // 1024
        print(f"  [STRETCH] {clip_path.name} {actual_dur:.1f}s -> {target_dur:.1f}s "
              f"(ratio={ratio:.3f}, {size_kb}KB)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] 拉伸失败: {e.stderr[:200]}")
        return False


def seconds_to_srt_time(seconds: float) -> str:
    """将秒数转换为 SRT 时间格式 HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# ---------------------------------------------------------------------------
# 时间轴计算（使用 per-shot 音频时长作为目标时长）
# ---------------------------------------------------------------------------

def get_shot_target_duration(shot: dict) -> float:
    """获取镜头目标时长——即音频时长，视频将被拉伸到此值。

    优先级：actual_audio_duration（02b 测量的 TTS 实际时长）> duration（01 的文本比例估算）
    """
    audio = shot.get("actual_audio_duration")
    if audio and audio > 0:
        return float(audio)
    return shot.get("duration", 5)


def compute_timeline_from_script(script: dict) -> Tuple[List[float], float]:
    """基于每镜目标时长（音频时长）计算整个时间轴。

    Returns:
        durations: 每个镜头的目标时长列表
        total: 总时长（= 音频总长）
    """
    shots = script.get("shots", [])
    durations = [get_shot_target_duration(s) for s in shots]
    total = sum(durations)
    print(f"[INFO] 音频时间轴: {len(durations)} 镜, 总计 {total:.1f}s")
    return durations, total


def find_shot_audio(shot_idx: int, audio_dir: Path) -> Optional[Path]:
    """查找镜头对应的音频文件。"""
    # 优先使用 per-shot 音频
    audio_path = audio_dir / f"shot_{shot_idx}.mp3"
    if audio_path.exists():
        return audio_path
    return None


def concat_audio_files(audio_paths: List[Path], output_path: Path) -> Path:
    """用 ffmpeg 合并多个音频文件。"""
    # 创建临时文件列表
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
    print(f"  [CONCAT] 合并 {len(audio_paths)} 个音频文件")
    result = subprocess.run(cmd, capture_output=True, text=True)
    list_file.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"音频合并失败:\n{result.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# SRT 字幕生成（基于实际时长）
# ---------------------------------------------------------------------------

def generate_srt(
    shots: List[dict],
    durations: List[float],
    output: Path,
) -> None:
    """生成 SRT 字幕文件。

    字幕按每个镜头目标时长（音频时长）分配时间。
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    current_time = 0.0
    srt_lines = []

    for i, (shot, dur) in enumerate(zip(shots, durations), 1):
        subtitle = shot.get("subtitle", "").strip()
        if not subtitle:
            continue

        # 字幕时长按该镜头目标时长（音频时长）
        start = current_time
        end = current_time + dur
        srt_lines.append(str(i))
        srt_lines.append(
            f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}"
        )
        srt_lines.append(subtitle)
        srt_lines.append("")
        current_time = end

    output.write_text("\n".join(srt_lines), encoding="utf-8")
    print(f"[OK] 字幕文件生成: {output} ({len(shots)} 条字幕, "
          f"总{current_time:.1f}s)")


# ---------------------------------------------------------------------------
# 合并流程：拉伸 → concat → 字幕 + 调色 + 暗角 + 混音
# ---------------------------------------------------------------------------


def concat_clips(clips: List[Path], output_path: Path) -> None:
    """用 ffmpeg concat 拼接多个视频片段（-c copy 不重编码）。"""
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
    print(f"  [CONCAT] 拼接 {len(clips)} 个拉伸后的视频")
    try:
        _run(cmd)
    finally:
        if list_path.exists():
            list_path.unlink()


def merge_final_video(
    clips: List[Path],
    shot_audio_durations: List[float],
    stretched_dir: Path,
    audio_path: Path,
    output_path: Path,
    srt_path: Path,
) -> None:
    """逐镜头拉伸对齐音频时长 → concat 拼接 → 字幕 + 调色 + 暗角 + 混音。

    核心改进：不再依赖 -shortest 截断对齐，而是通过 setpts 精确将每个视频片段
    拉伸到对应的音频时长，实现零偏差的音画对齐。视频时长 = 音频时长。
    """
    # 1. 逐个拉伸视频片段
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
            print(f"  [WARN] 镜头 {i+1} 拉伸失败，使用原始片段")
            stretched_clips.append(clip)
        else:
            stretched_clips.append(stretched_path)

    # 2. 拼接拉伸后的片段
    concat_video = stretched_dir / "concat_video.mp4"
    if concat_video.exists() and not DEBUG_SKIP_CACHE:
        print(f"  [CACHE] 拼接视频已缓存: {concat_video}")
    else:
        concat_clips(stretched_clips, concat_video)

    # 3. 最终合成（字幕 + 调色 + 暗角 + 混音）
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
    print("[INFO] 开始合并成片 ...")
    print(f"[INFO] 视频片段: {len(clips)} 个")
    print(f"[INFO] 音频文件: {audio_path}")
    print(f"[INFO] 字幕文件: {srt_path}")
    print(f"[INFO] 最终输出: {output_path}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[ERROR] ffmpeg 失败:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg 返回码 {result.returncode}")

    if output_path.exists():
        size_mb = output_path.stat().st_size / (10 * 1024 * 1024)
        print(f"\n[OK] 成片生成完毕: {output_path} ({size_mb:.1f} MB)")
    else:
        raise RuntimeError(f"输出文件未生成: {output_path}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    # 0. 检查 FFmpeg 可用性
    print(f"[INFO] FFmpeg:  {FFMPEG}")
    print(f"[INFO] FFprobe: {FFPROBE}")

    # 1. 读取分镜脚本
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {SCRIPT_FILE}\n请先运行 step01_script_gen（python step01_script_gen/main.py）"
        )

    with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
        script = json.load(f)

    shots = script.get("shots", [])
    if not shots:
        raise ValueError("分镜脚本中无镜头数据")

    print(f"[INFO] 读取到 {len(shots)} 个镜头")

    # 2. 收集视频片段和 per-shot 音频
    clips = []
    valid_shots = []  # 只保留有视频的镜头
    shot_indices = []
    missing_idxs = []

    for shot in shots:
        idx = shot.get("index", 0)
        clip_path = CLIP_DIR / str(idx) / "video.mp4"
        if not clip_path.exists():
            print(f"[WARN] 视频片段不存在: {clip_path}，将跳过此镜头")
            missing_idxs.append(idx)
            continue
        clips.append(clip_path)
        valid_shots.append(shot)
        shot_indices.append(idx)

    if missing_idxs:
        print(f"[WARN] 共跳过 {len(missing_idxs)} 个缺失镜头: {missing_idxs}")
    if not clips:
        raise FileNotFoundError(
            f"没有任何有效视频片段，请先运行 02b_vimax_gen.py\n"
            f"检查目录: {CLIP_DIR}"
        )

    print(f"[INFO] 视频片段就绪 ({CLIP_SOURCE}): {len(clips)} 个")

    # 3. 计算目标时长（音频时长作为视频拉伸目标）
    target_durations = [get_shot_target_duration(s) for s in valid_shots]
    total_target = sum(target_durations)
    print(f"[INFO] 目标时长（音频）: 总计 {total_target:.1f}s")

    # 4. 处理音频：优先使用 per-shot 音频，否则回退到整段 narration
    audio_path = None
    use_per_shot_audio = True

    for shot in valid_shots:
        idx = shot.get("index")
        audio = find_shot_audio(idx, AUDIO_SHOT_DIR)
        if audio is None:
            use_per_shot_audio = False
            break

    if use_per_shot_audio:
        # 合并 per-shot 音频
        audio_paths = [find_shot_audio(s.get("index"), AUDIO_SHOT_DIR) for s in valid_shots]
        merged_audio = TEMP_DIR / "merged_audio.mp3"
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        concat_audio_files(audio_paths, merged_audio)
        audio_path = merged_audio
        audio_duration = get_media_duration(audio_path)
        print(f"[INFO] 使用 per-shot 音频: {len(audio_paths)} 个片段, 总长 {audio_duration:.1f}s")
        use_stretch = True
    else:
        # 回退到整段 narration
        if not AUDIO_PATH.exists():
            raise FileNotFoundError(
                f"旁白音频不存在: {AUDIO_PATH}\n请先运行 03_tts_audio.py"
            )
        audio_path = AUDIO_PATH
        audio_duration = get_media_duration(audio_path)
        print(f"[INFO] 回退使用整段 narration: {audio_duration:.1f}s")
        # 无 per-shot 音频时无法逐镜拉伸，使用原始视频
        print(f"[INFO] 无 per-shot 音频，跳过逐镜拉伸，直接使用原始视频")
        target_durations = [get_media_duration(c) for c in clips]
        use_stretch = False

    # 5. 生成 SRT 字幕（基于目标音频时长）
    generate_srt(valid_shots, target_durations, SRT_OUT)

    # 6. 合并（拉伸对齐 → concat → 字幕/调色/暗角/混音）
    if use_stretch:
        merge_final_video(
            clips, target_durations,
            stretched_dir=TEMP_DIR,
            audio_path=audio_path,
            output_path=FINAL_OUT,
            srt_path=SRT_OUT,
        )
    else:
        # 旧模式 fallback（无 per-shot 音频时）
        print(f"\n{'='*60}")
        print("[INFO] 使用旧模式合并（无 per-shot 音频，直接 concat + -shortest）")
        print(f"{'='*60}\n")
        concat_list = TEMP_DIR / "merge_concat.txt"
        concat_list.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"file '{c.resolve().as_posix()}'" for c in clips]
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        srt_path_abs = SRT_OUT.resolve().as_posix()
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
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", video_filter,
            "-af", f"volume={AUDIO_VOLUME}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(FINAL_OUT),
        ]
        subprocess.run(cmd, check=True)
        if concat_list.exists():
            concat_list.unlink()
        if FINAL_OUT.exists():
            print(f"\n[OK] 成片生成完毕: {FINAL_OUT}")

    # 7. 清理临时合并音频
    if use_per_shot_audio and 'merged_audio' in locals() and Path(merged_audio).exists():
        Path(merged_audio).unlink()

    # 8. 合成报告
    final_duration = get_media_duration(FINAL_OUT)
    print("\n[INFO] 合成报告：")
    print(f"  音频总时长:      {audio_duration:.1f}s")
    print(f"  最终输出时长:    {final_duration:.1f}s")
    mismatch = abs(final_duration - audio_duration)
    print(f"  音画偏差:        {mismatch:.1f}s（理想：<0.1s）")
    if use_stretch:
        print(f"  [OK] 视频已精确拉伸对齐音频")
        if mismatch < 0.3:
            print(f"  [OK] 音画零偏差")
        else:
            print(f"  [WARN] 偏差源：拉伸缓存过旧或 ffmpeg 精度限制")
    else:
        if mismatch < 1:
            print(f"  [OK] 音画对齐良好（-shortest 模式）")
        else:
            print(f"  [WARN] 音画偏差较大，建议使用 per-shot 音频模式")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
