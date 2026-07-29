"""step02_video_gen.continuity - 连续链状态机 + 尾帧提取

双模调度系统（Mode A / Mode B）的核心模块：

  Mode A【关键帧规划模式】：使用 LLM 分镜提供的 start/end/中间关键帧
    图片 -> Agens Image 批量生图 -> Agens Video keyframes 生成视频
    适用: shot 0（钩子）、shot 1（首个正文镜头）

  Mode B【I2V 尾帧接续模式】：复用上一条视频的真实尾帧作为参考图，
    I2V 推演画面，丢弃当前镜头 start_frame_prompt（不再生成首帧图片）
    适用: shot >= 2（正文镜头，必须等上一个视频完成后启动）

设计原则：
  - shot 0/1 并行提交，互不依赖（钩子与正文首镜无必然联系）
  - shot >= 2 必须等上一个视频生成完毕、提取尾帧后才提交
  - 尾帧从已生成的视频中提取（FFmpeg），而非另生成图片
  - 单章级生命周期：连续状态在章节内积累，不跨章
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# ---- 配置常量 ----

# 模式B起始镜头序号：shot < MODE_B_START_INDEX 使用模式A，>= 使用模式B
MODE_B_START_INDEX = int(os.getenv("MODE_B_START_INDEX", "2"))

# 尾帧提取偏移（秒）：从视频末尾向前偏移，避免末尾黑帧
TAIL_FRAME_OFFSET = float(os.getenv("CONTINUITY_TAIL_FRAME_OFFSET", "0.25"))

# ---- FFmpeg / FFprobe 查找 ----

def _find_exe(name: str) -> str:
    """查找可执行文件."""
    path = shutil.which(name)
    if path:
        return path
    ffmpeg_bin = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if ffmpeg_bin:
        exe_path = Path(ffmpeg_bin) / f"{name}.exe"
        if exe_path.exists():
            return str(exe_path)
    raise FileNotFoundError(f"Cannot find {name}. Please install FFmpeg.")


FFMPEG = _find_exe("ffmpeg")
FFPROBE = _find_exe("ffprobe")


# ---- 尾帧提取 ----

def extract_tail_frame(
    video_path: str | Path,
    output_dir: str | Path,
    shot_idx: int,
    offset: float = TAIL_FRAME_OFFSET,
) -> Optional[str]:
    """从已生成视频中提取最后一帧作为 PNG 图片.

    使用 FFmpeg -sseof 定位到文件末尾前 offset 秒，
    避免末尾黑帧或编码残留帧。
    """
    video_path = Path(video_path)
    if not video_path.exists() or video_path.stat().st_size < 1024:
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"tail_shot_{shot_idx:03d}.png"

    cmd = [
        FFMPEG, "-y",
        "-sseof", f"-{offset}",
        "-i", str(video_path),
        "-update", "1",
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            stderr_tail = proc.stderr[-300:] if proc.stderr else "(no stderr)"
            from shared.tee_logger import get_logger
            from shared.paths import chapter_dir
            log = get_logger(chapter_dir() / "step02.log", console_level="INFO")
            log.verbose(f"  [TAIL] shot_{shot_idx} extract failed: {stderr_tail}")
            return None
    except Exception as e:
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        log = get_logger(chapter_dir() / "step02.log", console_level="INFO")
        log.verbose(f"  [TAIL] shot_{shot_idx} ffmpeg error: {e}")
        return None

    return str(out_path)


# ---- 连续链状态机 ----

class ContinuityTracker:
    """跨镜头连续性状态跟踪.

    规则极简：
      shot < MODE_B_START_INDEX (0/1) -> Mode A（并行，无依赖）
      shot >= MODE_B_START_INDEX (2+) -> Mode B（串行，等上一个视频尾帧）

    单章级生命周期：在 generate_alls() 开始时创建，结束时销毁。
    """

    def __init__(self, working_dir: Path | str):
        self._working_dir = Path(working_dir)
        self._tail_dir = self._working_dir / "tail_frames"
        self._tail_dir.mkdir(parents=True, exist_ok=True)

        # 状态
        self.last_tail_frame_path: str | None = None

        # 调度记录：{shot_idx: "A" | "B"}，供 merge 阶段查询
        self._mode_log: dict[int, str] = {}

    # ---- 判定 ----

    def should_use_mode_a(self, shot: dict) -> bool:
        """判定当前镜头是否应使用模式A（关键帧规划）.

        shot 0 (钩子) 和 shot 1 (正文首镜) 使用模式A（并行提交），
        shot >= 2 使用模式B（I2V尾帧接续，必须在上一镜视频完成后才能启动）。
        """
        idx = shot.get("index", 0)
        return idx < MODE_B_START_INDEX

    # ---- 状态更新 ----

    def record_mode(self, shot_idx: int, mode: str) -> None:
        """记录镜头使用模式."""
        self._mode_log[shot_idx] = mode

    def advance(self, shot: dict, video_path: str | Path) -> str | None:
        """当前镜头视频生成完毕后调用：提取尾帧供下一镜使用.

        Args:
            shot: 当前镜头数据
            video_path: 刚生成的视频文件路径

        Returns:
            提取的尾帧路径，失败时返回 None
        """
        idx = shot.get("index", 0)

        # 提取尾帧
        tail = extract_tail_frame(video_path, self._tail_dir, idx)
        self.last_tail_frame_path = tail

        return tail

    def reset_chain(self) -> None:
        """重置连续链（钩子镜头结束后调用）."""
        self.last_tail_frame_path = None

    # ---- 查询 ----

    def get_mode(self, shot_idx: int) -> str | None:
        """查询镜头使用的模式."""
        return self._mode_log.get(shot_idx)

    def is_mode_b(self, shot_idx: int) -> bool:
        """镜头是否使用模式B."""
        return self._mode_log.get(shot_idx) == "B"

    def get_tail_frame(self) -> str | None:
        """获取当前可用的尾帧参考路径."""
        if self.last_tail_frame_path and Path(self.last_tail_frame_path).exists():
            return self.last_tail_frame_path
        return None

    # ---- 持久化（供 merge 阶段读取） ----

    def save_meta(self) -> None:
        """将调度记录写入 continuity_meta.json（供 step04 读取）."""
        meta_path = self._working_dir / "continuity_meta.json"
        data = {
            "mode_b_start_index": MODE_B_START_INDEX,
            "tail_frame_offset": TAIL_FRAME_OFFSET,
            "shots": {
                str(idx): mode for idx, mode in self._mode_log.items()
            },
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_meta(working_dir: Path | str) -> dict | None:
        """从 working_dir 读取调度记录."""
        meta_path = Path(working_dir) / "continuity_meta.json"
        if not meta_path.exists():
            return None
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def is_shot_mode_b(working_dir: Path | str, shot_idx: int) -> bool:
        """静态方法：查询某个镜头是否使用模式B."""
        meta = ContinuityTracker.load_meta(working_dir)
        if not meta:
            return False
        shots = meta.get("shots", {})
        return shots.get(str(shot_idx)) == "B"


# ---- 便捷函数 ----

def load_continuity_meta(working_dir: Path | str) -> dict:
    """加载连续性调度元数据（供 step04 使用）."""
    meta = ContinuityTracker.load_meta(working_dir)
    return meta or {}
