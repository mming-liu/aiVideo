"""step02_video_gen.audio_pregen - 媒体时长测量与回填模块

功能：
- _find_ffprobe(): 查找 ffprobe
- _measure_media_duration(): 测量媒体时长
- _measure_actual_duration(): 测量视频实际时长
- _update_script_with_actual_durations(): 回填实际时长到 JSON

⚠️ 2026-08 架构变更：step02 已不再调用本模块的 TTS 预生成（对话交给视频模型，
视频时长由 shot["duration_sec"] 驱动）。原 _gen_shot_audio_duration（TTS 音频时长）
已删除（死代码，审计 P0-1），保留 ffprobe 时长测量供 submit_poll 复用。
"""
import sys
from pathlib import Path
import os
import json
import subprocess
import shutil

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"分镜脚本不存在: {path}\n请先运行 step01_script_gen")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_ffprobe() -> str:
    """查找 ffprobe 可执行文件."""
    path = shutil.which("ffprobe")
    if path:
        return path
    ffmpeg_bin = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if ffmpeg_bin:
        ffprobe_path = Path(ffmpeg_bin) / "ffprobe.exe"
        if ffprobe_path.exists():
            return str(ffprobe_path)
    raise FileNotFoundError("ffprobe 未找到，请确保 FFmpeg 已安装并加入 PATH")


FFPROBE = _find_ffprobe()


def _measure_media_duration(path: Path) -> float:
    """用 ffprobe 测量媒体时长（秒）."""
    if not path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {path.name}: {e}")
    return 0.0


def _measure_actual_duration(video_path: Path) -> float:
    """用 ffprobe 测量视频的实际时长（秒）."""
    if not video_path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {video_path.name}: {e}")
    return 0.0


def _update_script_with_actual_durations(working_dir: Path, script_path: Path) -> None:
    """测量所有已生成视频的实际时长，回填到 script_Result.json."""
    from shared.paths import SHOT_VIDEO_FILENAME
    shots_dir = working_dir / "shots"
    if not shots_dir.exists():
        print("[WARN] shots 目录不存在，跳过时长回填")
        return

    # 读取现有脚本
    script = load_script(script_path)
    shots = script.get("shots", [])
    updated_count = 0

    for shot in shots:
        idx = shot.get("index", 0)
        video_path = shots_dir / str(idx) / SHOT_VIDEO_FILENAME
        if video_path.exists():
            actual_duration = _measure_actual_duration(video_path)
            if actual_duration > 0:
                shot["actual_duration"] = round(actual_duration, 2)
                # 2026-09-13 审计修复：期望时长用权威字段 duration_sec（step01
                # timecode 权威计算）；此前用 shot.get("duration", 5)，字段名不对
                # 且缺字段静默退回 5 秒，时长偏差统计基准全错。缺失时跳过偏差统计。
                audio_ref = shot.get("actual_audio_duration", 0)
                expected = audio_ref if audio_ref > 0 else (
                    shot.get("duration_sec") or shot.get("duration") or 0
                )
                if expected > 0:
                    deviation = abs(actual_duration - expected) / expected * 100
                    shot["duration_deviation_pct"] = round(deviation, 1)
                else:
                    shot.pop("duration_deviation_pct", None)
                updated_count += 1

    if updated_count > 0:
        # 原子写：防半截 JSON（2026-09-13 审计修复，收敛 shared.json_io）
        from shared.json_io import atomic_dump_json
        atomic_dump_json(script_path, script, ensure_ascii=False, indent=2)
        print(f"[INFO] 已回填 {updated_count} 个镜头实际时长")