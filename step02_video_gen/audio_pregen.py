"""step02_video_gen.audio_pregen - 媒体时长测量与回填模块

功能：
- _find_ffprobe(): 查找 ffprobe
- _measure_media_duration(): 测量媒体时长
- _measure_actual_duration(): 测量视频实际时长
- collect_actual_durations(): 汇总实际时长派生指标（只读 script，不落盘）
- write_actual_durations_metrics(): 派生指标落 step02_video_metrics.json sidecar
  （**不写回 script_result.json**——那是 step01 已提交产物，见 shared/paths.py）

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


def collect_actual_durations(working_dir: Path, script: dict) -> dict:
    """测量所有已生成视频的实际时长 → 派生指标（**只读 script，不落盘**）.

    返回 ``{"shots": {"<idx>": {...}}, "updated_count": n, "total_actual_duration": t}``。
    期望时长基准是 `duration_sec`（step01 timecode 权威计算），缺字段时按 `duration`
    回退，两者都缺则**不记偏差**——旧实现缺字段静默退回 5 秒，时长偏差基准全错
    （2026-09-13 审计）。缺 shots 目录或视频时返回空指标，不抛。
    """
    from shared.paths import SHOT_VIDEO_FILENAME
    metrics: dict = {"shots": {}, "updated_count": 0, "total_actual_duration": 0.0}
    shots_dir = working_dir / "shots"
    if not shots_dir.exists():
        print("[WARN] shots 目录不存在，跳过时长测量")
        return metrics

    total = 0.0
    for shot in script.get("shots", []) or []:
        idx = shot.get("index", 0)
        video_path = shots_dir / str(idx) / SHOT_VIDEO_FILENAME
        if not video_path.exists():
            continue
        actual_duration = _measure_actual_duration(video_path)
        if actual_duration <= 0:
            continue
        entry = {"actual_duration": round(actual_duration, 2)}
        audio_ref = shot.get("actual_audio_duration", 0)
        expected = audio_ref if audio_ref > 0 else (
            shot.get("duration_sec") or shot.get("duration") or 0
        )
        if expected > 0:
            entry["expected_duration"] = round(float(expected), 3)
            entry["duration_deviation_pct"] = round(
                abs(actual_duration - expected) / expected * 100, 1)
        metrics["shots"][str(idx)] = entry
        metrics["updated_count"] += 1
        total += actual_duration
    metrics["total_actual_duration"] = round(total, 2)
    return metrics


def write_actual_durations_metrics(working_dir: Path, script_path: Path) -> Path | None:
    """把实际时长派生指标落到 ``step02_video_metrics.json`` sidecar.

    **不写回 `script_result.json`**（2026-10 复核 P0）：那是 step01 的已提交正式产物，
    `step01_commit.json` 锁着它的 SHA1，下游每个阶段入口都核
    （`shared.quality_gate.verify_step01_commit_manifest`）。旧实现把
    `actual_duration` / `duration_deviation_pct` 直接写回该文件，于是 step02b 一旦
    生成过视频，step03/step04 与任何重跑都会被判 `artifact changed after commit`。

    sidecar 与产物同目录、与产物身份同批（记 `script_revision`），因此核摘要时
    正式产物仍逐字节不变。无可用测量结果时不写文件，返回 None。
    """
    from shared.paths import STEP02_VIDEO_METRICS_FILENAME
    from shared.json_io import atomic_dump_json

    script = load_script(script_path)
    metrics = collect_actual_durations(working_dir, script)
    if metrics["updated_count"] <= 0:
        print("[WARN] 无可用实际时长，未写 step02 视频指标 sidecar")
        return None

    payload = {
        "schema_version": 1,
        "script_revision": script.get("script_revision"),
        "chapter_index": script.get("chapter_index"),
        **metrics,
    }
    out = Path(script_path).parent / STEP02_VIDEO_METRICS_FILENAME
    atomic_dump_json(out, payload, ensure_ascii=False, indent=2)
    print(f"[INFO] 已记录 {metrics['updated_count']} 个镜头的实际时长 → {out.name}")
    return out