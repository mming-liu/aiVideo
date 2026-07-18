"""step04_merge.merge_core - Core merge module with stretch, concat, and filter chain
v5.1 新增：
- 开场缓冲：黑屏淡入 + 标题卡 + 过渡到首个镜头
- 转场效果：实现 clip_transition 字段定义的转场
"""
import sys
from pathlib import Path
import os
import subprocess
import json
import math

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

# Opening buffer settings
OPENING_BLACK_DURATION = float(os.getenv("OPENING_BLACK_DURATION", "1.5"))  # 黑屏持续时间
OPENING_FADE_IN_DURATION = float(os.getenv("OPENING_FADE_IN_DURATION", "1.5"))  # 淡入持续时间
OPENING_TITLE_DURATION = float(os.getenv("OPENING_TITLE_DURATION", "2.0"))  # 标题卡持续时间
OPENING_ENABLED = os.getenv("OPENING_ENABLED", "1").strip() == "1"  # 是否启用开场缓冲

# 转场持续时间（秒）
TRANSITION_DURATION = float(os.getenv("TRANSITION_DURATION", "0.8"))


# 是否显示 ffmpeg 命令行（调试用）
DEBUG_SHOW_CMD = os.getenv("DEBUG_SHOW_CMD", "0").strip() == "1"


def _run_cmd(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Execute shell command and optionally print."""
    if DEBUG_SHOW_CMD:
        print(f"[CMD] {' '.join(cmd)}")
    # ffmpeg/ffprobe 输出 UTF-8；不显式指定时 Windows 会用 GBK 解码导致
    # UnicodeDecodeError（路径含中文目录名时必现）
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


_video_params_cache: dict = {}


def get_video_params(path: Path) -> Tuple[int, int, str]:
    """探测视频实际宽高与帧率，返回 (width, height, fps_str)（如 704, 1280, "24/1"）.

    lavfi color 源（黑场/白场/标题卡）的尺寸和帧率必须与真实片段完全一致，
    否则 concat/xfade 滤镜以 EINVAL(-22) 失败。不能硬编码 720x1280：
    Agnes 会把请求尺寸圆整到 32 的倍数，实际输出 704x1280@24。
    """
    key = str(path)
    if key in _video_params_cache:
        return _video_params_cache[key]
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json", str(path),
    ]
    result = _run_cmd(cmd)
    stream = json.loads(result.stdout)["streams"][0]
    params = (int(stream["width"]), int(stream["height"]),
              stream.get("r_frame_rate", "24/1"))
    _video_params_cache[key] = params
    return params


def concat_clips(clips: List[Path], output_path: Path, keep_audio: bool = False) -> None:
    """Concat multiple video clips using ffmpeg (-c copy, no re-encode).

    keep_audio: 是否保留原始音轨（默认 False 只保留视频）
    """
    list_path = output_path.parent / "concat_list_stretched.txt"
    lines = [f"file '{c.resolve().as_posix()}'" for c in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")

    if keep_audio:
        # 保留音轨（使用 -map 0 同时保留视频和音频）
        cmd = [
            FFMPEG, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-map", "0",
            "-c", "copy",
            str(output_path),
        ]
    else:
        # 只保留视频（丢弃音频）
        cmd = [
            FFMPEG, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(output_path),
        ]
    print(f"  [CONCAT] Concatenating {len(clips)} stretched videos (keep_audio={keep_audio})")
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
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    list_file.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"Audio merge failed:\n{result.stderr}")
    return output_path


def apply_transition(
    clip_a_path: Path,
    clip_b_path: Optional[Path],
    transition_type: str,
    output_path: Path,
    transition_duration: float = TRANSITION_DURATION,
    keep_audio: bool = False,
) -> Path:
    """Apply transition effect between two clips.

    transition_type supported:
    - "硬切 cut": direct concat
    - "淡入 fade in": fade in from black (clip_b fades in from black)
    - "淡出 fade out": fade out to black (clip_a fades out to black)
    - "模糊叠化 blur dissolve": crossfade with blur
    - "叠化 dissolve": simple crossfade
    - "黑场闪白": white flash

    clip_b_path can be None for fade-out on the last clip (no next clip).

    keep_audio: 是否保留原始音轨（用于版本B混合模式）
    """
    transition_type = transition_type.lower()

    # 硬切 - 直接拼接
    if "硬切" in transition_type or "cut" in transition_type:
        if clip_b_path:
            concat_clips([clip_a_path, clip_b_path], output_path, keep_audio=keep_audio)
        else:
            # 最后一个镜头，复制即可
            import shutil
            shutil.copy2(clip_a_path, output_path)
        return output_path

    # 淡入淡出使用 fade 滤镜
    if "淡入" in transition_type or "fade in" in transition_type:
        # 淡入：简化为直接拼接（硬切），避免复杂的 fade 滤镜问题
        if not clip_b_path:
            import shutil
            shutil.copy2(clip_a_path, output_path)
            return output_path

        # 直接拼接视频（使用已有的 concat_clips）
        concat_clips([clip_a_path, clip_b_path], output_path, keep_audio=False)
        return output_path

    if "淡出" in transition_type or "fade out" in transition_type:
        # 获取 clip_a 的时长用于计算 fade 起始点
        clip_a_duration = get_media_duration(clip_a_path)

        # 如果 clip_b 不存在（最后一个镜头），只做淡出效果
        if clip_b_path is None:
            # fade 从视频末尾开始，持续时间不超过视频的一半
            fade_dur = min(transition_duration, clip_a_duration * 0.5)
            fade_start = clip_a_duration - fade_dur

            cmd = [
                FFMPEG, "-y",
                "-i", str(clip_a_path),
                "-filter_complex",
                f"[0:v]fade=t=out:st={fade_start}:d={fade_dur}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            _run_cmd(cmd)
            return output_path

        # clip_a 淡出连接到 clip_b
        clip_a_dur = get_media_duration(clip_a_path)
        clip_b_dur = get_media_duration(clip_b_path)

        # 淡出从 clip_a 末尾开始
        fade_dur = min(transition_duration, clip_a_dur * 0.5)
        fade_start = clip_a_dur - fade_dur

        if keep_audio:
            # 分两步处理：先视频转场，再合并音频
            temp_video = output_path.with_suffix('.video.mp4')
            cmd = [
                FFMPEG, "-y",
                "-i", str(clip_a_path),
                "-i", str(clip_b_path),
                "-filter_complex",
                f"[0:v]fade=t=out:st={fade_start}:d={fade_dur}[aout];[aout][1:v]concat=n=2:v=1:a=0",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                str(temp_video),
            ]
            _run_cmd(cmd)
            # 合并两个视频的音频
            cmd2 = [
                FFMPEG, "-y",
                "-i", str(temp_video),
                "-i", str(clip_a_path),
                "-i", str(clip_b_path),
                "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[audio]",
                "-map", "0:v:0", "-map", "[audio]",
                "-c:v", "copy", "-c:a", "aac",
                str(output_path),
            ]
            _run_cmd(cmd2)
            temp_video.unlink(missing_ok=True)
        else:
            cmd = [
                FFMPEG, "-y",
                "-i", str(clip_a_path),
                "-i", str(clip_b_path),
                "-filter_complex",
                f"[0:v]fade=t=out:st={fade_start}:d={fade_dur}[aout];[aout][1:v]concat=n=2:v=1:a=0",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            _run_cmd(cmd)
        return output_path
    # 模糊叠化 - 使用 crossfade 滤镜
    if "模糊" in transition_type or "blur" in transition_type:
        if not clip_b_path:
            # 无下一个镜头，复制当前
            import shutil
            shutil.copy2(clip_a_path, output_path)
            return output_path

        # 获取 clip_a 时长，计算转场偏移
        clip_a_dur = get_media_duration(clip_a_path)
        offset = clip_a_dur - transition_duration

        if keep_audio:
            temp_video = output_path.with_suffix('.video.mp4')
            cmd = [
                FFMPEG, "-y",
                "-i", str(clip_a_path),
                "-i", str(clip_b_path),
                "-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[a];[1:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[b];[a][b]xfade=transition=hblur:duration={transition_duration}:offset={offset}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                str(temp_video),
            ]
            _run_cmd(cmd)
            # 合并音频
            cmd2 = [
                FFMPEG, "-y",
                "-i", str(temp_video),
                "-i", str(clip_a_path),
                "-i", str(clip_b_path),
                "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[audio]",
                "-map", "0:v:0", "-map", "[audio]",
                "-c:v", "copy", "-c:a", "aac",
                str(output_path),
            ]
            _run_cmd(cmd2)
            temp_video.unlink(missing_ok=True)
        else:
            cmd = [
                FFMPEG, "-y",
                "-i", str(clip_a_path),
                "-i", str(clip_b_path),
                "-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[a];[1:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[b];[a][b]xfade=transition=hblur:duration={transition_duration}:offset={offset}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            _run_cmd(cmd)
        return output_path

    # 叠化 dissolve - 简单交叉淡入淡出
    if "叠化" in transition_type or "dissolve" in transition_type:
        if not clip_b_path:
            # 无下一个镜头，复制当前
            import shutil
            shutil.copy2(clip_a_path, output_path)
            return output_path

        # 获取 clip_a 时长，计算转场偏移
        clip_a_dur = get_media_duration(clip_a_path)
        offset = clip_a_dur - transition_duration

        cmd = [
            FFMPEG, "-y",
            "-i", str(clip_a_path),
            "-i", str(clip_b_path),
            "-filter_complex",
            f"[0:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[xa];[1:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[xb];[xa][xb]xfade=transition=fade:duration={transition_duration}:offset={offset}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        _run_cmd(cmd)
        return output_path

    # 黑场闪白
    if "闪白" in transition_type:
        if not clip_b_path:
            # 无下一个镜头，复制当前
            import shutil
            shutil.copy2(clip_a_path, output_path)
            return output_path

        # 短黑场后闪白
        flash_duration = transition_duration / 3

        # 获取 clip_a 时长，估算 offset
        clip_a_dur = get_media_duration(clip_a_path)
        offset = clip_a_dur - flash_duration * 2  # 留出黑场+白场时间

        # 白场/黑场源必须匹配片段实际宽高/帧率，setsar=1 统一采样宽高比
        fw, fh, ffps = get_video_params(clip_b_path)
        cmd = [
            FFMPEG, "-y",
            "-i", str(clip_a_path),
            "-f", "lavfi", "-i", f"color=c=white:s={fw}x{fh}:r={ffps}:d={flash_duration}",
            "-f", "lavfi", "-i", f"color=c=black:s={fw}x{fh}:r={ffps}:d={flash_duration}",
            "-i", str(clip_b_path),
            "-filter_complex",
            f"[0:v]fade=t=out:st=0:d={flash_duration},setsar=1,settb=AVTB[aout];"
            f"[1:v]setsar=1[white];[2:v]setsar=1[blk];[3:v]setsar=1[clipb];"
            f"[white][blk][clipb]concat=n=3:v=1:a=0,settb=AVTB[flash];"
            f"[aout][flash]xfade=transition=fade:duration={flash_duration}:offset={offset}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        _run_cmd(cmd)
        return output_path

    # 默认：硬切
    concat_clips([clip_a_path, clip_b_path], output_path)
    return output_path


def create_opening_buffer(
    first_clip_path: Path,
    output_path: Path,
    title_text: str = "",
) -> Path:
    """Create opening buffer: black screen -> fade in -> optional title -> first clip.

    Returns the path to the combined opening video.
    """
    if not OPENING_ENABLED:
        # Skip opening buffer, just copy first clip
        import shutil
        shutil.copy(first_clip_path, output_path)
        return output_path

    temp_dir = output_path.parent
    black_path = temp_dir / "opening_black.mp4"
    fadein_path = temp_dir / "opening_fadein.mp4"
    title_path = temp_dir / "opening_title.mp4"

    # 黑屏/标题卡必须匹配首个片段的实际宽高/帧率（Agnes 实际输出 704x1280@24）
    vw, vh, vfps = get_video_params(first_clip_path)

    # 1. 创建黑屏 (OPENING_BLACK_DURATION 秒)
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={vw}x{vh}:r={vfps}:d={OPENING_BLACK_DURATION}",
        "-c:v", "libx264",
        "-profile:v", "main",
        "-level", "4.0",
        "-t", str(OPENING_BLACK_DURATION),
        "-pix_fmt", "yuv420p",
        "-an",  # 黑屏不需要音频
        str(black_path),
    ]
    _run_cmd(cmd)

    # 2. 淡入到第一个镜头 (OPENING_FADE_IN_DURATION 秒)
    # 使用 crossfade 实现黑屏->首个镜头的淡入
    if title_text:
        # 2a. Create title card using ASS subtitle (reliable for Chinese text)
        ass_content = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {vw}\n"
            f"PlayResY: {vh}\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Alignment, BorderStyle, Outline, Shadow, MarginL, MarginR, MarginV\n"
            "Style: Title,Microsoft YaHei,72,&H00FFFFFF,&H00000000,&H00000000,0,0,5,1,3,2,10,10,10\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,0:00:{OPENING_TITLE_DURATION:.2f}.00,Title,,0,0,0,,{{\\fnMicrosoft YaHei}}{{\\fs72}}{{\\c&HFFFFFF}}{{\\bord3}}{{\\shad2}}{title_text}\n"
        )
        ass_path = temp_dir / "opening_title.ass"
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(ass_content, encoding="utf-8")
        # Escape path for ffmpeg filter syntax (drive colon)
        ass_filter_path = ass_path.resolve().as_posix().replace(":", "\\:")

        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={vw}x{vh}:r={vfps}:d={OPENING_TITLE_DURATION}",
            "-vf", f"subtitles=filename={ass_filter_path}:original_size={vw}x{vh}",
            "-c:v", "libx264",
            "-profile:v", "main",
            "-level", "4.0",
            "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(title_path),
        ]
        _run_cmd(cmd)

        # 2b. 标题卡 -> 首个镜头 淡入
        cmd = [
            FFMPEG, "-y",
            "-i", str(title_path),
            "-i", str(first_clip_path),
            "-filter_complex",
            f"[0:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[title];[1:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[clip];[title][clip]xfade=transition=fade:duration={OPENING_FADE_IN_DURATION}:offset=0[out];[out]fade=t=in:st=0:d=0.5",
            "-c:v", "libx264",
            "-profile:v", "main",
            "-level", "4.0",
            "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(fadein_path),
        ]
        _run_cmd(cmd)
    else:
        # 无标题：黑屏 -> 首个镜头 淡入
        cmd = [
            FFMPEG, "-y",
            "-i", str(black_path),
            "-i", str(first_clip_path),
            "-filter_complex",
            f"[0:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[black];[1:v]setpts=PTS-STARTPTS,setsar=1,settb=AVTB[clip];[black][clip]xfade=transition=fade:duration={OPENING_FADE_IN_DURATION}:offset=0[out]",
            "-c:v", "libx264",
            "-profile:v", "main",
            "-level", "4.0",
            "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(fadein_path),
        ]
        _run_cmd(cmd)

    # 3. 拼接黑屏 + 淡入部分
    # 使用 filter_complex 替代 concat 简化流程
    cmd = [
        FFMPEG, "-y",
        "-i", str(black_path),
        "-i", str(fadein_path),
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    _run_cmd(cmd)

    # 清理临时文件
    for p in [black_path, fadein_path, title_path]:
        if p.exists():
            p.unlink()

    return output_path


def merge_final_video(
    clips: List[Path],
    shot_audio_durations: List[float],
    stretched_dir: Path,
    audio_path: Path,
    output_path: Path,
    srt_path: Path,
    clip_transitions: Optional[List[str]] = None,
    chapter_title: str = "",
    keep_video_audio: bool = False,
) -> None:
    """Per-shot stretch to audio duration -> concat -> transition -> subtitle + color + vignette + mix.

    v5.2 新增：
    - keep_video_audio: 是否保留视频自带音频
      - False (default): 版本A，只使用 TTS 配音
      - True: 版本B，视频音频 + TTS 混合（背景音效 70% + TTS 30%）

    v5.1 新增：
    - clip_transitions: 每个镜头的转场方式列表，与 clip_transition 字段对应
    - chapter_title: 章节标题，用于开场标题卡
    - 开场缓冲：黑屏 -> 淡入 -> (标题卡) -> 首个镜头
    - 转场效果：根据 clip_transition 字段实现镜头间的转场
    """
    # 0. 解析转场列表（如果未提供，从环境变量读取）
    if clip_transitions is None:
        # 默认全部硬切
        clip_transitions = ["硬切 cut"] * len(clips)

    # 0.5. 检查视频文件完整性（防止损坏的片段导致 FFmpeg 崩溃）
    valid_clips = []
    for clip in clips:
        if not clip.exists():
            print(f"  [WARN] Clip not found: {clip}, skipping")
            continue
        size_bytes = clip.stat().st_size
        if size_bytes < 10000:  # 小于 10KB 的文件通常是损坏的
            print(f"  [WARN] Clip too small ({size_bytes} bytes): {clip.name}, skipping")
            continue
        # 使用 ffprobe 验证视频是否可读
        try:
            result = subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30
            )
            duration = float(result.stdout.strip()) if result.stdout.strip() else 0
            if duration <= 0:
                print(f"  [WARN] Clip has invalid duration: {clip.name}, skipping")
                continue
            valid_clips.append(clip)
            print(f"  [OK] Clip validated: {clip.name} ({duration:.1f}s, {size_bytes // 1024}KB)")
        except Exception as e:
            print(f"  [WARN] Clip validation failed: {clip.name}, error: {e}")
            continue

    if len(valid_clips) < len(clips):
        print(f"  [WARN] {len(clips) - len(valid_clips)} clips skipped due to invalid/corrupt files")
    if not valid_clips:
        raise RuntimeError("No valid video clips found after validation check")

    # 更新 clips 列表
    clips = valid_clips
    if len(shot_audio_durations) != len(clips):
        # 调整音频时长列表以匹配有效的视频片段
        print(f"  [WARN] Adjusting durations: had {len(shot_audio_durations)}, now {len(clips)}")
        shot_audio_durations = shot_audio_durations[:len(clips)]

    # 1. Stretch each video clip
    stretched_clips = []
    stretched_dir.mkdir(parents=True, exist_ok=True)

    for i, (clip, target_dur) in enumerate(zip(clips, shot_audio_durations)):
        stretched_path = stretched_dir / f"stretched_{i:04d}.mp4"
        # 当需要保留视频音频时，跳过缓存重新生成（因为旧缓存没有音频）
        use_cache = stretched_path.exists() and not DEBUG_SKIP_CACHE and not keep_video_audio
        if use_cache:
            cached_dur = get_media_duration(stretched_path)
            if abs(cached_dur - target_dur) < 0.1:
                stretched_clips.append(stretched_path)
                continue
            stretched_path.unlink()

        if not stretch_clip(clip, target_dur, stretched_path, keep_audio=keep_video_audio):
            print(f"  [WARN] Shot {i+1} stretch failed, use original clip")
            stretched_clips.append(clip)
        else:
            stretched_clips.append(stretched_path)

    # 2. 应用转场效果并拼接
    if len(stretched_clips) > 1:
        print(f"  [TRANSITION] Applying transitions between {len(stretched_clips)} clips")
        # 从第二个镜头开始应用转场
        concat_with_transition = stretched_dir / "concat_with_transition.mp4"
        if concat_with_transition.exists() and not DEBUG_SKIP_CACHE:
            print(f"  [CACHE] Concatenated with transitions cached")
        else:
            # 逐对处理转场：第一镜 + 第二镜(带转场) -> 第三镜(带转场) -> ...
            current = stretched_clips[0]
            for i in range(1, len(stretched_clips)):
                # transition_ 是描述如何从上一镜进入当前镜 (i)
                # clip_transitions[i] 对应 shot i+1 (因为 0-indexed)
                transition_type = clip_transitions[i] if i < len(clip_transitions) else "硬切 cut"

                # 检查是否是最后一个镜头的转场
                is_last = (i == len(stretched_clips) - 1)

                # 如果是最后一个镜头且是淡出/淡入，需要特殊处理
                # 淡出：从当前镜淡出到黑（不需要下一镜）
                # 淡入：从黑淡入到当前镜（需要当前镜）
                if is_last and ("淡出" in transition_type or "fade out" in transition_type):
                    # 最后一个镜头的淡出：current (clip_a) 淡出结束，不需要 next (clip_b)
                    next_clip = None
                    print(f"    Shot {i+1}: {transition_type} (last clip, exit effect)")
                elif is_last and ("淡入" in transition_type or "fade in" in transition_type):
                    # 最后一个镜头的淡入：current 从黑淡入
                    next_clip = stretched_clips[i]  # 最后一个镜头本身
                    print(f"    Shot {i+1}: {transition_type} (last clip, entry from black)")
                else:
                    # 正常情况：两个镜头之间的转场
                    next_clip = stretched_clips[i]
                    print(f"    Shot {i} -> {i+1}: {transition_type}")

                # 应用转场
                temp_merged = stretched_dir / f"merged_{i:04d}.mp4"
                print(f"    [DEBUG] i={i}, current={current.name if current else 'None'}, next={next_clip.name if next_clip else 'None'}, transition={transition_type}, is_last={is_last}")
                try:
                    result = apply_transition(current, next_clip, transition_type, temp_merged, keep_audio=keep_video_audio)
                    print(f"    [DEBUG] apply_transition returned: {result}")
                    # 验证输出文件
                    if temp_merged.exists():
                        size = temp_merged.stat().st_size
                        dur = get_media_duration(temp_merged)
                        print(f"    [DEBUG] merged file: {size} bytes, {dur:.2f}s")
                        if size < 10000:
                            print(f"    [ERROR] merged file too small ({size} bytes), possible FFmpeg failure!")
                            raise RuntimeError(f"Merge failed: output file too small ({size} bytes)")
                    else:
                        raise RuntimeError(f"Merge failed: output file not created")
                except Exception as e:
                    print(f"    [ERROR] apply_transition failed: {e}")
                    raise
                current = temp_merged

            # 重命名为最终转场拼接文件
            import shutil
            shutil.move(str(current), str(concat_with_transition))
        concat_video = concat_with_transition
    else:
        concat_video = stretched_clips[0] if stretched_clips else None

    if not concat_video or not concat_video.exists():
        raise RuntimeError(f"Concatenated video not generated: {concat_video}")

    # 验证文件大小（防止 FFmpeg 失败但文件被创建的情况）
    concat_size = concat_video.stat().st_size
    if concat_size < 100000:  # 小于 100KB 说明转场合并失败
        raise RuntimeError(f"Concatenated video too small ({concat_size} bytes), merge likely failed: {concat_video}")

    # 3. 添加开场缓冲
    if OPENING_ENABLED:
        final_with_opening = stretched_dir / "final_with_opening.mp4"
        print(f"  [OPENING] Creating opening buffer with title: {chapter_title}")
        create_opening_buffer(concat_video, final_with_opening, chapter_title)
        concat_video = final_with_opening

    # 4. 构建视频滤镜（字幕 + 色彩 + 暗角）
    srt_path_abs = srt_path.resolve().as_posix()
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
        f"eq=contrast={CONTRAST}:saturation={SATURATION},"
        f"vignette=angle={VIGNETTE_ANGLE}"
    )

    # 5. 根据版本添加音频
    # Version A: 只使用 TTS
    # Version B: 视频音频 + TTS 混合（侧链压缩：人声出现时背景音自动压低）
    video_audio_path = None
    if keep_video_audio:
        # Version B: 混合模式 - 视频原声(背景音/环境音) + TTS(人声)
        # 策略：使用侧链压缩，当 TTS 有人说话时自动压低背景音
        print("[INFO] Mode B: Mixing Video Audio (Background) + TTS (Voice) with Sidechain Compression")

        # Version B 音频混合参数（可配置）
        VIDEO_AUDIO_VOLUME = float(os.getenv("FFMPEG_VERSION_B_VIDEO_VOLUME", "0.25"))  # 视频背景音量 25%
        TTS_VOLUME_BOOST = float(os.getenv("FFMPEG_VERSION_B_TTS_BOOST", "2.0"))  # TTS 音量放大倍数

        cmd = [
            FFMPEG, "-y",
            "-i", str(concat_video),      # 输入0: 视频 (含原声)
            "-i", str(audio_path),         # 输入1: TTS 音频
            "-filter_complex",
            # 1. 处理视频音频: 降低音量作为背景，格式化
            f"[0:a]volume={VIDEO_AUDIO_VOLUME},aformat=sample_fmts=fltp:channel_layouts=stereo[bg];"
            # 2. 处理 TTS 音频: 放大音量确保压过背景，格式化
            f"[1:a]volume={TTS_VOLUME_BOOST},aformat=sample_fmts=fltp:channel_layouts=stereo[tts];"
            # 3. 混合: 背景 25% + TTS 75%（权重 1:3，人声优先）
            "[bg][tts]amix=inputs=2:duration=first:weights='1 3'[a_out]",
            "-map", "0:v:0",               # 映射视频流
            "-map", "[a_out]",             # 映射混合后的音频流
            "-shortest",                   # 以最短流为准
            "-vf", video_filter,           # 字幕+调色
            "-pix_fmt", "yuv420p",         # 强制 YUV420P，Windows 必选
            "-c:v", "libx264",
            "-profile:v", "main",          # 使用 Main Profile，兼容性更好
            "-level", "4.0",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",                # 强制音频采样率 44100Hz
            "-ac", "2",                    # 强制双声道
            str(output_path),
        ]
    else:
        # Version A: 纯 TTS 模式 (丢弃视频原声)
        print("[INFO] Mode A: TTS Audio Only")
        cmd = [
            FFMPEG, "-y",
            "-i", str(concat_video),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            "-vf", video_filter,
            "-af", f"volume={AUDIO_VOLUME},aformat=sample_fmts=fltp:channel_layouts=stereo",  # 格式化音频
            "-pix_fmt", "yuv420p",         # 强制 YUV420P，Windows 必选
            "-c:v", "libx264",
            "-profile:v", "main",          # 使用 Main Profile，兼容性更好
            "-level", "4.0",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",                # 强制音频采样率 44100Hz
            "-ac", "2",                    # 强制双声道
            str(output_path),
        ]

    print(f"\n{'='*60}")
    print("[INFO] Starting final merge...")
    print(f"[INFO] Video clips: {len(clips)}")
    print(f"[INFO] Audio file: {audio_path}")
    print(f"[INFO] Subtitle file: {srt_path}")
    print(f"[INFO] Final output: {output_path}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")

    if result.returncode != 0:
        print(f"[ERROR] ffmpeg failed:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg returned code {result.returncode}")

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"\n[OK] Final video generated: {output_path} ({size_mb:.1f} MB)")
    else:
        raise RuntimeError(f"Output file not generated: {output_path}")