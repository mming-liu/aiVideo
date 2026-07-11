"""step02_video_gen.audio_pregen - 音频预生成与时长回填模块

功能：
- load_script(): 加载脚本
- _measure_media_duration(): 测量媒体时长
- _gen_shot_audio_duration(): 为镜头生成 TTS 音频并获取实际时长
- _measure_actual_duration(): 测量视频实际时长
- _update_script_with_actual_durations(): 回填实际时长到 JSON
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
load_dotenv(_PROJECT_ROOT / ".env")

from tts import create_provider, TTSRequest
from tts.config import config as tts_config


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
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {path.name}: {e}")
    return 0.0


def _gen_shot_audio_duration(shot: dict, audio_dir: Path, provider=None) -> float:
    """为指定镜头生成 TTS 音频，返回实际时长（秒）.

    核心优化：先生成音频拿到实际时长，再按音频时长计算视频帧数，保证音画精准对齐.
    """
    idx = shot.get("index", 0)
    text = shot.get("narration_segment", "").strip()
    if not text:
        print(f"  [{idx}] 无 narration_segment，回退使用 LLM 预估时长")
        return shot.get("duration", 5)

    audio_path = audio_dir / f"shot_{idx}.mp3"
    if audio_path.exists() and audio_path.stat().st_size > 10240:
        duration = _measure_media_duration(audio_path)
        if duration > 0:
            return duration
        audio_path.unlink()

    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
        if provider is None:
            provider = create_provider()
        req = TTSRequest(
            text=text,
            model=tts_config.MODEL,
            rate=tts_config.RATE,
            volume=tts_config.VOLUME,
            format=tts_config.FORMAT,
            sample_rate=tts_config.SAMPLE_RATE,
        )
        result = provider.synthesize(req, output=str(audio_path))
        if not result.success:
            raise RuntimeError(result.error)

        duration = _measure_media_duration(audio_path)
        if duration > 0:
            size_kb = audio_path.stat().st_size / 1024
            print(f"  [{idx}] TTS ({size_kb:.0f}KB/{duration:.1f}s)")
            return duration
    except Exception as e:
        print(f"  [{idx}] TTS 失败: {e}")

    print(f"  [{idx}] 回退使用 LLM 预估时长")
    return shot.get("duration", 5)


def _measure_actual_duration(video_path: Path) -> float:
    """用 ffprobe 测量视频的实际时长（秒）."""
    if not video_path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {video_path.name}: {e}")
    return 0.0


def _update_script_with_actual_durations(working_dir: Path, script_path: Path) -> None:
    """测量所有已生成视频的实际时长，回填到 script_Result.json."""
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
        video_path = shots_dir / str(idx) / "video.mp4"
        if video_path.exists():
            actual_duration = _measure_actual_duration(video_path)
            if actual_duration > 0:
                shot["actual_duration"] = round(actual_duration, 2)
                # 相对于音频时长的偏差
                audio_ref = shot.get("actual_audio_duration", 0)
                expected = audio_ref if audio_ref > 0 else shot.get("duration", 5)
                deviation = abs(actual_duration - expected) / expected * 100 if expected > 0 else 0
                shot["duration_deviation_pct"] = round(deviation, 1)
                updated_count += 1

    if updated_count > 0:
        # 保存回脚本文件
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 已回填 {updated_count} 个镜头的实际时长到 JSON")
    else:
        print("[INFO] 没有视频文件需要回填时长")