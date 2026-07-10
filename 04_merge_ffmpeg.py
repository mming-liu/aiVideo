"""视频合并 — 将分镜片段、旁白音频合成为最终成片。

流程:
  1. 读取 script_result.json 获取字幕与镜头信息
  2. 使用实际视频时长（actual_duration）计算时间轴
  3. 使用 per-shot TTS 音频（shot_N.mp3）实现精确音画对齐
  4. 字幕按实际视频时长分布，覆盖全程无空白
  5. ffmpeg concat + 字幕烧录 + 调色 + 暗角 + 混音
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
SCRIPT_FILE = _PROJECT_ROOT / "script_result.json"
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
# 工具函数
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


def seconds_to_srt_time(seconds: float) -> str:
    """将秒数转换为 SRT 时间格式 HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# ---------------------------------------------------------------------------
# 时间轴计算（使用实际视频时长 + per-shot 音频）
# ---------------------------------------------------------------------------

def get_shot_duration(shot: dict) -> float:
    """获取镜头实际时长。

    优先级：actual_duration（真实测量）> effective_duration（01 计算的带缓冲时长）> duration（请求时长）
    """
    # 02b 视频生成后写入的实际时长
    actual = shot.get("actual_duration")
    if actual and actual > 0:
        return actual

    # 01 计算的有效时长（已减去 0.3s 缓冲）
    effective = shot.get("effective_duration")
    if effective and effective > 0:
        return effective

    # 回退到原始请求时长
    return shot.get("duration", 5)


def compute_timeline_from_actual(script: dict) -> Tuple[List[float], float]:
    """基于实际视频时长计算每个镜头的时间轴。

    Returns:
        durations: 每个镜头的实际/有效时长列表
        total: 总时长
    """
    shots = script.get("shots", [])
    durations = [get_shot_duration(s) for s in shots]
    total = sum(durations)
    print(f"[INFO] 时间轴: {len(durations)} 镜, 总计 {total:.1f}s")
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

    字幕按每个镜头的实际时长比例分配时间。
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    current_time = 0.0
    srt_lines = []

    for i, (shot, dur) in enumerate(zip(shots, durations), 1):
        subtitle = shot.get("subtitle", "").strip()
        if not subtitle:
            continue

        # 字幕时长按该镜头实际视频时长
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
# FFmpeg 合并命令
# ---------------------------------------------------------------------------

def build_ffmpeg_command(
    clips: List[Path],
    srt_path: Path,
    audio_path: Path,
    output_path: Path,
    concat_list_path: Path,
) -> List[str]:
    """构建 ffmpeg 合并命令（concat + 字幕 + 调色 + 暗角 + 混音）。"""
    # 1. 写入 concat 列表文件
    concat_lines = []
    for clip in clips:
        safe_path = clip.resolve().as_posix()
        concat_lines.append(f"file '{safe_path}'")
    concat_list_path.write_text("\n".join(concat_lines), encoding="utf-8")

    # 2. 视频滤镜链（字幕 -> 调色 -> 暗角）
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

    # 3. 命令
    cmd = [
        FFMPEG,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_path),
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
        "-shortest",
        str(output_path),
    ]
    return cmd


def merge_final_video(
    clips: List[Path],
    audio_path: Path,
    output_path: Path,
    srt_path: Path,
) -> None:
    """执行 ffmpeg 合并."""
    concat_list_path = output_path.parent / "concat_list.txt"

    cmd = build_ffmpeg_command(
        clips, srt_path, audio_path, output_path,
        concat_list_path,
    )

    print(f"\n{'='*60}")
    print("[INFO] 开始合并成片 ...")
    print(f"[INFO] 视频片段: {len(clips)} 个")
    print(f"[INFO] 旁白音频: {audio_path}")
    print(f"[INFO] 字幕文件: {srt_path}")
    print(f"[INFO] 最终输出: {output_path}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, capture_output=True, text=True)

    # 清理临时文件
    if concat_list_path.exists():
        concat_list_path.unlink()

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
            f"分镜脚本不存在: {SCRIPT_FILE}\n请先运行 01_script_gen.py"
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

    if missing_idxs:
        print(f"[WARN] 共跳过 {len(missing_idxs)} 个缺失镜头: {missing_idxs}")
    if not clips:
        raise FileNotFoundError(
            f"没有任何有效视频片段，请先运行 02b_vimax_gen.py\n"
            f"检查目录: {CLIP_DIR}"
        )

    print(f"[INFO] 视频片段就绪 ({CLIP_SOURCE}): {len(clips)} 个")

    # 3. 计算时间轴（使用实际时长）
    durations, video_total = compute_timeline_from_actual(script)
    # 过滤到有效镜头
    durations = [durations[shots.index(s)] for s in valid_shots]

    print(f"[INFO] 视频实际总时长: {video_total:.1f}s")

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
    else:
        # 回退到整段 narration
        if not AUDIO_PATH.exists():
            raise FileNotFoundError(
                f"旁白音频不存在: {AUDIO_PATH}\n请先运行 03_tts_audio.py"
            )
        audio_path = AUDIO_PATH
        audio_duration = get_media_duration(audio_path)
        print(f"[INFO] 回退使用整段 narration: {audio_duration:.1f}s")

    # 5. 生成 SRT 字幕（基于实际时长）
    generate_srt(valid_shots, durations, SRT_OUT)

    # 6. 合并（视频不拉伸，直接按实际时长 concat）
    merge_final_video(clips, audio_path, FINAL_OUT, SRT_OUT)

    # 7. 清理临时合并音频
    if use_per_shot_audio and 'merged_audio' in locals() and Path(merged_audio).exists():
        Path(merged_audio).unlink()

    final_duration = get_media_duration(FINAL_OUT)
    print("\n[INFO] 合成报告：")
    print(f"  视频实际总时长: {video_total:.1f}s")
    print(f"  音频实际总时长: {audio_duration:.1f}s")
    print(f"  最终输出时长:   {final_duration:.1f}s")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
