"""视频合并 — 将分镜片段、旁白音频合成为最终成片.

流程:
  1. 读取 script_result.json 获取字幕与镜头信息
  2. 以视频为时间轴基准，音频自然播放
  3. 视频保持原速不压缩；音频>视频时拉伸视频（上限 2.5x）
  4. 字幕按视频时长均匀分布（每镜独占一段），覆盖全程无空白
  5. ffmpeg concat + 字幕烧录 + 调色 + 暗角 + 混音
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

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
VIGNETTE_ANGLE = float(os.getenv("FFMPEG_VIGNETTE_ANGLE", "0.5236"))  # PI/6
AUDIO_VOLUME = os.getenv("FFMPEG_AUDIO_VOLUME", "5dB")

# 字幕样式（ASS 标准十六进制颜色 ABGR 格式）
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Sans")
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "20"))
SUBTITLE_COLOR = os.getenv("SUBTITLE_COLOR", "&H00FFFFFF")        # 白色
SUBTITLE_OUTLINE_COLOR = os.getenv("SUBTITLE_OUTLINE_COLOR", "&H00000000")  # 黑边
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
# 时间轴计算（以视频为基准，限制拉伸倍数）
# ---------------------------------------------------------------------------
MAX_STRETCH_FACTOR = float(os.getenv("FFMPEG_MAX_STRETCH", "2.5"))

def compute_target_durations(
    script_durations: List[float],
    audio_duration: float,
    video_total: float,
) -> List[float]:
    """计算每个镜头的目标时长.

    铁律：视频长度绝对不改（不拉伸、不压缩），video_total 就是 effective_duration.
    音频与视频不对齐时：
      - 音频 < 视频：音频播完即止，后续无声（字幕覆盖全程）
      - 音频 > 视频：音频被截断
    真正的解决方式是 01_script_gen 控制 narration 字数让 TTS ≈ 30s.
    """
    effective_duration = video_total
    mode = "视频绝对不改（铁律）"

    total_script = sum(script_durations)
    if total_script <= 0:
        n = len(script_durations)
        return [effective_duration / n] * n

    targets = [d / total_script * effective_duration for d in script_durations]
    print(f"[INFO] 时间轴策略: {mode}")
    print(f"[INFO] 音频={audio_duration:.1f}s, "
          f"视频实际总长={video_total:.1f}s, "
          f"有效目标总长={effective_duration:.1f}s")
    return targets


def stretch_clip(input_path: Path, output_path: Path, factor: float) -> Path:
    """用 setpts 拉伸/压缩视频片段到目标时长.

    factor = target_duration / actual_duration
    factor > 1: 慢放（拉伸）
    factor < 1: 快放（压缩）
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 陈旧检测（调试时可用 DEBUG_SKIP_CACHE=1 跳过全部拉伸缓存）
    if not DEBUG_SKIP_CACHE and output_path.exists() and output_path.stat().st_size > 20000:
        src_mtime = input_path.stat().st_mtime
        out_mtime = output_path.stat().st_mtime
        if src_mtime > out_mtime:
            print(f"  [!] {output_path.name} 早于源 {input_path.name}"
                  f"（源画面已更新），将重新拉伸")
        else:
            dur = get_media_duration(output_path)
            print(f"  [OK] 跳过已拉伸片段 {output_path.name}（{dur:.1f}s）")
            return output_path

    # setpts 拉伸 + fps 固定帧率
    vf = f"setpts={factor:.4f}*PTS,fps=30"
    cmd = [
        FFMPEG, "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-an",  # 去掉原片音频（用旁白替换）
        str(output_path),
    ]
    print(f"  [拉伸] {input_path.name}: factor={factor:.3f}, "
          f"目标 {get_media_duration(input_path) * factor:.1f}s")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"拉伸失败 {input_path.name}:\n{result.stderr}")
    return output_path


def generate_srt(
    shots: List[dict],
    target_durations: List[float],
    audio_duration: float,
    output: Path,
) -> None:
    """生成 SRT 字幕文件.

    字幕与音频严格对应：
      - 按旁白字数比例切分 audio_duration
      - 每条字幕的开始/结束时间 = 前面累加字数/总字数 * audio_duration
      - 字幕若超过视频时长会自然被截断（字幕由视频承载）
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    # 计算总旁白字数
    total_chars = sum(len(shot.get("subtitle", "").strip()) for shot in shots)
    if total_chars <= 0:
        total_chars = 1

    current_time = 0.0
    srt_lines = []

    for i, shot in enumerate(shots, 1):
        subtitle = shot.get("subtitle", "").strip()
        chars = len(subtitle)
        if not subtitle:
            continue
        # 每条字幕占 audio_duration 的比例 = 该字幕字数 / 总字数
        snippet_dur = audio_duration * chars / total_chars
        start = current_time
        end = current_time + snippet_dur
        srt_lines.append(str(i))
        srt_lines.append(
            f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}"
        )
        srt_lines.append(subtitle)
        srt_lines.append("")
        current_time = end

    output.write_text("\n".join(srt_lines), encoding="utf-8")
    print(f"[OK] 字幕文件生成: {output} ({len(shots)} 条字幕, "
          f"总{total_chars}字, 时长范围 0.0 ~ {current_time:.1f}s)")


# ---------------------------------------------------------------------------
# FFmpeg 合并命令
# ---------------------------------------------------------------------------

def build_ffmpeg_command(
    stretched_clips: List[Path],
    srt_path: Path,
    audio_path: Path,
    output_path: Path,
    concat_list_path: Path,
) -> List[str]:
    """构建 ffmpeg 合并命令（concat + 字幕 + 调色 + 暗角 + 混音）."""
    # 1. 写入 concat 列表文件
    concat_lines = []
    for clip in stretched_clips:
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
        f"subtitles='{escaped_srt}'"
        f":force_style='{style}'"
        f",eq=contrast={CONTRAST}:saturation={SATURATION}"
        f",vignette=angle={VIGNETTE_ANGLE}"
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
        str(output_path),
    ]
    return cmd


def merge_final_video(
    stretched_clips: List[Path],
    audio_path: Path,
    output_path: Path,
    srt_path: Path,
) -> None:
    """执行 ffmpeg 合并."""
    concat_list_path = output_path.parent / "concat_list.txt"

    cmd = build_ffmpeg_command(
        stretched_clips, srt_path, audio_path, output_path,
        concat_list_path,
    )

    print(f"\n{'='*60}")
    print("[INFO] 开始合并成片 ...")
    print(f"[INFO] 视频片段: {len(stretched_clips)} 个")
    print(f"[INFO] 旁白音频: {audio_path}")
    print(f"[INFO] 字幕文件: {srt_path}")
    print(f"[INFO] 最终输出: {output_path}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, capture_output=True, text=True)

    # 清理临时文件
    for tmp_path in [concat_list_path]:
        if tmp_path.exists():
            tmp_path.unlink()

    if result.returncode != 0:
        print(f"[ERROR] ffmpeg 失败:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg 返回码 {result.returncode}")

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
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

    # 2. 检查旁白音频
    if not AUDIO_PATH.exists():
        raise FileNotFoundError(
            f"旁白音频不存在: {AUDIO_PATH}\n请先运行 03_tts_audio.py"
        )

    audio_duration = get_media_duration(AUDIO_PATH)
    print(f"[INFO] 旁白音频时长: {audio_duration:.1f}s")

    # 3. 检查视频片段
    clips = []
    script_durations = []
    for shot in shots:
        idx = shot.get("index", 0)
        clip_path = CLIP_DIR / str(idx) / "video.mp4"
        if not clip_path.exists():
            raise FileNotFoundError(
                f"视频片段不存在: {clip_path}\n请先运行 02b_vimax_gen.py"
            )
        clips.append(clip_path)
        script_durations.append(shot.get("duration", 6))

    print(f"[INFO] 视频片段就绪 ({CLIP_SOURCE}): {len(clips)} 个")

    # 4. 计算目标时长（以视频为基准）
    video_durations = [get_media_duration(c) for c in clips]
    video_total = sum(video_durations)
    print(f"[INFO] 视频实际总时长: {video_total:.1f}s")
    target_durations = compute_target_durations(
        script_durations, audio_duration, video_total,
    )
    print(f"[INFO] 目标时长分配:")
    for i, (sd, td, s) in enumerate(
        zip(script_durations, target_durations, shots), 1
    ):
        print(f"  镜头{i}: 脚本{sd}s -> 目标{td:.1f}s "
              f"\"{s.get('subtitle', '')[:20]}...\"")

    # 5. 拉伸每个片段
    print("\n[INFO] 拉伸片段 ...")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    stretched_clips = []
    for i, (clip, target_dur) in enumerate(zip(clips, target_durations)):
        actual_dur = get_media_duration(clip)
        factor = target_dur / actual_dur if actual_dur > 0 else 1.0
        stretched_path = TEMP_DIR / f"stretched_clip_{i+1}.mp4"
        print(f"  镜头{i+1}: 实际{actual_dur:.1f}s -> 目标{target_dur:.1f}s "
              f"(factor={factor:.2f})")
        stretch_clip(clip, stretched_path, factor)
        stretched_clips.append(stretched_path)

    # 6. 生成 SRT 字幕（以视频时间为基准）
    generate_srt(shots, target_durations, audio_duration, SRT_OUT)

    # 7. 合并
    merge_final_video(stretched_clips, AUDIO_PATH, FINAL_OUT, SRT_OUT)

    final_duration = get_media_duration(FINAL_OUT)
    print("\n[INFO] 合成报告：")
    print(f"  脚本请求总时长: {sum(script_durations)}s")
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
