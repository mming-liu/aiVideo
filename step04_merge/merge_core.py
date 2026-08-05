"""step04_merge.merge_core - 合并核心工具（native 模式精简版）

2026-08 架构变更：合并改为唯一 native 模式（纯视频拼接，保留视频原声，
语音/背景音由视频模型生成）。删除版本A/B双输出与 TTS 混音路径，以及
配套的 stretch/转场/开场缓冲逻辑（merge_final_video、apply_transition、
create_opening_buffer、concat_clips、concat_audio_files 均被移除）。

仅保留 main.py 需要的通用工具：_find_exe / _run_cmd。
"""
import sys
from pathlib import Path
import os
import subprocess

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")

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

# 编码预设（读取 shared.config，默认 ultrafast）
try:
    from shared.config import MERGE_PRESET as _CFG_PRESET
    VIDEO_PRESET = _CFG_PRESET
except ImportError:
    VIDEO_PRESET = "ultrafast"


def _run_cmd(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """Execute shell command.

    ffmpeg/ffprobe 输出 UTF-8；不显式指定时 Windows 会用 GBK 解码导致
    UnicodeDecodeError（路径含中文目录名时必现）。
    """
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)
