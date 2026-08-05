"""step02_video_gen.audio_pregen - 音频预生成与时长回填模块

功能：
- load_script(): 加载脚本
- _measure_media_duration(): 测量媒体时长
- _gen_shot_audio_duration(): 为镜头生成 TTS 音频并获取实际时长
- _measure_actual_duration(): 测量视频实际时长
- _update_script_with_actual_durations(): 回填实际时长到 JSON

⚠️ 2026-08 架构变更：step02 已不再调用本模块的 TTS 预生成（对话交给视频模型，
视频时长由 shot["duration_sec"] 驱动）。本文件保留供 step03_tts_audio 及日后
需要测量媒体时长/TTS 音频时长的地方复用，勿删除。
"""
import sys
from pathlib import Path
import os
import json
import subprocess
import shutil
import hashlib

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {path.name}: {e}")
    return 0.0


def _gen_shot_audio_duration(shot: dict, audio_dir: Path, provider=None) -> float:
    """为指定镜头生成 TTS 音频，返回实际时长（秒）."""
    idx = shot.get("index", 0)
    text = shot.get("narration_segment", "").strip()
    if not text:
        return shot.get("duration", 5)

    audio_path = audio_dir / f"shot_{idx}.mp3"
    if audio_path.exists() and audio_path.stat().st_size > 10240:
        duration = _measure_media_duration(audio_path)
        if duration > 0:
            hash_path = audio_dir / f"shot_{idx}.mp3.hash"
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if hash_path.exists():
                cached_hash = hash_path.read_text(encoding="utf-8").strip()
                if cached_hash == text_hash:
                    return duration
            audio_path.unlink()
            hash_path.unlink(missing_ok=True)

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
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            Path(str(audio_path) + ".hash").write_text(text_hash, encoding="utf-8")
            return duration
    except Exception as e:
        print(f"  [{idx}] TTS 失败: {e}")

    return shot.get("duration", 5)


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
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 已回填 {updated_count} 个镜头实际时长")