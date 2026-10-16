"""step02_video_gen.video_qc - 视频产物视觉质检（A6 修复 08-11）.

对下载成功的视频做内容级质检：抽取中间帧 → 用 ds-vision-skill 检查人物缺失/
字幕/幻觉/物理逻辑错误。与图片质检（shared/image_qc.py）共用 vision-router.ps1，
只是输入从单张 PNG 换成视频抽帧。

设计：
- 默认不启用（VIDEO_QC_ENABLED=0，视觉质检昂贵，逐镜跑会拖慢全片）。
- 启用时由 submit_poll._qc_video_product 调用；失败仅标记该镜重生成，不阻断流水线。
- 软失败原则（与图片 QC 一致）：router 缺失/超时/解析失败 → 视为"未检出问题"，
  绝不误杀正常视频。
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# 视频抽帧时机（视频时长比例）：中段帧最能反映内容质量（开头/结尾易黑场/淡出）。
# v6.35（08-15）：首/中/尾三帧——单帧只查中段漏检开头黑场/结尾黑场淡出/人物
# 中段消失。三帧任一检出 critical 即判坏（重生成）。
_MID_FRAME_RATIO = 0.5
_START_FRAME_RATIO = 0.12
_END_FRAME_RATIO = 0.88
_FRAME_RATIOS = (
    ("start", _START_FRAME_RATIO),
    ("mid", _MID_FRAME_RATIO),
    ("end", _END_FRAME_RATIO),
)


def _find_ffmpeg() -> str | None:
    """定位 ffmpeg（PATH 优先，FFMPEG_BIN_DIR 兜底），复用 stretch._find_exe 逻辑."""
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    import os
    ffmpeg_bin = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if ffmpeg_bin:
        cand = Path(ffmpeg_bin) / "ffmpeg.exe"
        if cand.exists():
            return str(cand)
    return None


def _extract_frame(video_path: Path, out_dir: Path, ratio: float, tag: str) -> Path | None:
    """用 ffmpeg 抽取视频 ratio 时刻单帧到 out_dir，返回帧路径（失败 None）."""
    try:
        from step04_merge.stretch import get_media_duration
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            return None
        dur = get_media_duration(video_path)
        if dur <= 0:
            return None
        t = max(0.0, min(dur - 0.05, dur * ratio))
        frame = out_dir / f"qc_{tag}_{video_path.stem}.png"
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{t:.2f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(frame),
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        if frame.exists() and frame.stat().st_size > 3 * 1024:
            return frame
        return None
    except Exception:
        return None


def qc_video_file(video_path: Path, shot_idx: int,
                  shot: dict | None = None,
                  characters: list | None = None) -> bool:
    """对视频文件做视觉质检（v6.35：首/中/尾三帧）——**旧接口，方向固定**。

    旧语义（§15「QC证据」）：``True`` = 检出 critical 问题（应重生成）；
    ``False`` = 无问题 **或软失败**（抽帧失败/质检服务失败都归 False）。

    软失败与"真的没问题"在旧接口里不可区分——需要区分时用
    :func:`qc_video_file_result`（结构化结果带 ``measured`` 与 ``status``）。
    """
    return qc_video_file_result(video_path, shot_idx, shot=shot,
                                characters=characters).status == STATUS_CRITICAL


#: 结构化质检状态。
STATUS_CLEAN = "clean"            # 真测过，无 critical
STATUS_CRITICAL = "critical"      # 真测过，检出 critical（应重生成）
STATUS_UNVERIFIED = "unverified"  # 抽帧/质检服务失败 ⇒ **未验证**（不是"通过"）
STATUS_UNAVAILABLE = "unavailable"  # 前置工具/依赖不可用


@dataclass
class VideoQcResult:
    """结构化质检结果：把"测过没有"与"测出什么"分开（§V3/§15）。

    ``measured=False`` 时 ``status`` 必为 unverified/unavailable——
    **不得**据此宣称视频内容合格（required_check 要求真实测量时必须拒绝）。
    """

    status: str
    measured: bool = False
    frames_checked: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    reason: str | None = None

    def passed(self) -> bool:
        return self.status == STATUS_CLEAN

    def to_dict(self) -> dict:
        return {"status": self.status, "measured": self.measured,
                "frames_checked": list(self.frames_checked),
                "issues": list(self.issues), "reason": self.reason}


def qc_video_file_result(video_path: Path, shot_idx: int,
                         shot: dict | None = None,
                         characters: list | None = None) -> VideoQcResult:
    """结构化版本：clean / critical / unverified / unavailable（方向与判据单源）。"""
    try:
        from shared.image_qc import maybe_qc_sync
        from shared.paths import vimax_working_dir
        real_shot = dict(shot or {})
        real_shot.setdefault("index", shot_idx)
        with tempfile.TemporaryDirectory(dir=str(vimax_working_dir())) as td:
            frames = [
                (tag, _extract_frame(video_path, Path(td), ratio, tag))
                for tag, ratio in _FRAME_RATIOS
            ]
            frames = [(t, f) for t, f in frames if f is not None]
            if not frames:
                return VideoQcResult(STATUS_UNAVAILABLE, measured=False,
                                     reason="抽帧全部失败（ffmpeg/媒体不可用）")

            checked: list[str] = []
            issues: list[dict] = []
            failures = 0
            for tag, frame in frames:
                frame_kind = "first_frame"
                kf_index = None
                if tag == "end":
                    frame_kind = "last_frame"
                elif tag == "mid":
                    kfs = real_shot.get("keyframes") or []
                    if kfs:
                        frame_kind = "keyframe"
                        kf_index = min(len(kfs) - 1, max(0, len(kfs) // 2))
                empty_scene = "空镜" in str(real_shot.get("main_subject") or "")
                result = maybe_qc_sync(
                    frame,
                    shot=real_shot,
                    frame_kind=frame_kind,
                    characters=characters or [],
                    empty_scene=empty_scene,
                    kf_index=kf_index,
                )
                if not result.ok:
                    failures += 1
                    continue  # 质检服务失败=未验证（不计入"测过"）
                checked.append(tag)
                critical = [i for i in result.issues
                            if getattr(i, "severity", "") == "critical"]
                if critical:
                    issues.extend({
                        "frame": tag, "kind": "visual_critical",
                        "severity": "critical",
                        "message": str(getattr(i, "message", "") or i),
                        "evidence_paths": [str(frame)],
                    } for i in critical)
            if not checked:
                return VideoQcResult(STATUS_UNVERIFIED, measured=False,
                                     reason=f"{failures} 帧质检服务全部失败（未验证）")
            if issues:
                return VideoQcResult(STATUS_CRITICAL, measured=True, frames_checked=checked,
                                     issues=issues, reason="检出 critical 视觉问题")
            return VideoQcResult(STATUS_CLEAN, measured=True, frames_checked=checked,
                                 reason=None if not failures else f"{failures} 帧未验证")
    except Exception as exc:  # 依赖缺失/环境异常：如实 unverified，不误杀
        return VideoQcResult(STATUS_UNAVAILABLE, measured=False, reason=f"质检不可用: {exc}")
