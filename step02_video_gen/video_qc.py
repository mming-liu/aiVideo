"""step02_video_gen.video_qc - 视频产物视觉质检（A6 修复 08-11）.

对下载成功的视频做内容级质检：抽取首/中/尾帧 → 检查人物缺失/字幕/幻觉/物理逻辑错误。
与图片质检共用 shared/image_qc.maybe_qc_sync（OpenCode Go 视觉通道），
只是输入从单张 PNG 换成视频抽帧。

设计：
- 默认不启用（VIDEO_QC_ENABLED=0，视觉质检昂贵，逐镜跑会拖慢全片）。
- 启用时由 submit_poll._qc_video_product 调用；失败仅标记该镜重生成，不阻断流水线。
- 软失败原则（与图片 QC 一致）：router 缺失/超时/解析失败 → 视为"未检出问题"，
  绝不误杀正常视频。
"""
from __future__ import annotations

import os
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


# ===========================================================================
# S06/V3：0 元 FFmpeg 硬探针（黑场/静帧/残缺/首帧偏离）——调视觉 QC 之前先跑
# ---------------------------------------------------------------------------
# 判据分层（与 `reroll_decision` 的 media/contract 分类对齐）：
# - **critical**：整片黑场/整片静帧/无法解码/时长严重不符/首帧几乎完全偏离输入帧；
# - **warning**：局部黑场/局部静帧（可能是设计内的淡入淡出/有意静镜）、
#   首帧结构相似度偏低（模型重绘很正常）。
# 阈值都可由 env 覆盖，且**不在此处决定重抽还是后处理**——那由
# `reroll_decision.decide()` 按 issue.kind 分类裁决。
# ===========================================================================


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _probe_media_info(path: Path) -> dict:
    from shared.media_inspect import probe_media
    try:
        return probe_media(path) or {}
    except Exception:
        return {}


def _stream_duration(streams: list, codec_type: str) -> float | None:
    for stream in streams or []:
        if str(stream.get("codec_type") or "") != codec_type:
            continue
        try:
            return float(stream.get("duration"))
        except (TypeError, ValueError):
            continue
    return None


def _run_detector(video_path: Path, vf: str) -> tuple[str, str]:
    """跑一个 ffmpeg 检测滤镜到 null 输出，返回 `(stderr, error)`（不写文件）."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return "", "ffmpeg_unavailable"
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-v", "info", "-i", str(video_path),
           "-vf", vf, "-an", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=90, check=False)
    except Exception as exc:  # noqa: BLE001 - 探测失败=未验证，不误杀
        return "", f"probe_failed:{exc}"
    return (proc.stderr or "").decode("utf-8", errors="replace"), ""


def _first_frame_similarity(video_path: Path, first_frame_path: Path) -> tuple[dict, str]:
    """首帧与输入 `first_frame.png` 的**结构相关**（32×32 灰度，0 调用、确定性）.

    返回 ``({"corr": ..., "luma_similarity": ..., "comparable": bool}, method)``。
    纯亮度差对"整帧重绘/换景"不敏感（黑 vs 彩色也能到 0.5），所以判据用相关系数；
    输入帧本身无结构（纯色/纯黑）时 ``comparable=False``——**不据此宣称偏离**。
    """
    try:
        from PIL import Image
    except Exception:
        return {"comparable": False}, "pillow_unavailable"
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return {"comparable": False}, "ffmpeg_unavailable"
    try:
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "first.png"
            proc = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "2", str(frame)],
                capture_output=True, timeout=60, check=False)
            if proc.returncode != 0 or not frame.is_file():
                return {"comparable": False}, "frame_extract_failed"
            size = (32, 32)
            a = list(Image.open(frame).convert("L").resize(size).tobytes())
            b = list(Image.open(first_frame_path).convert("L").resize(size).tobytes())
            n = len(a)
            ma, mb = sum(a) / n, sum(b) / n
            va = sum((x - ma) ** 2 for x in a)
            vb = sum((y - mb) ** 2 for y in b)
            luma = round(max(0.0, 1.0 - sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * n)), 4)
            if va <= 1e-6 or vb <= 1e-6:
                return {"comparable": False, "luma_similarity": luma}, "structure_flat"
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            corr = cov / ((va ** 0.5) * (vb ** 0.5))
            return {"comparable": True, "corr": round(float(corr), 4),
                    "luma_similarity": luma}, "structure_corr"
    except Exception as exc:  # noqa: BLE001
        return {"comparable": False}, f"similarity_failed:{exc}"


def probe_video_media(video_path: Path, *, expected_duration: float | None = None,
                      first_frame_path: Path | None = None) -> "MediaProbeResult":
    """0 元硬探针：解码/时长/分辨率/黑场/静帧/首帧偏离（**不调用任何模型**）.

    Returns:
        :class:`MediaProbeResult`；``measured=False`` 表示探测本身不可用（未验证），
        此时**不得**据此宣称视频合格，也不应判生成错误。
    """
    video_path = Path(video_path)
    issues: list[dict] = []
    # 2026-11-27 复核：黑场/静帧/首帧偏离**尚未用真实生成片校准**，默认只做影子评估
    # （warning，进报告与重抽判据），`VIDEO_MEDIA_ENFORCE=1` 才升级为 critical 硬拦。
    # 确定损坏（无流/不可解码/时长严重不符）始终 critical。
    enforce = str(os.getenv("VIDEO_MEDIA_ENFORCE", "0")).strip().lower() in (
        "1", "true", "yes", "on")

    def _severity(code: str, *, calibrated: bool = False) -> str:
        return "critical" if (calibrated or enforce) else "warning"

    metrics: dict = {"expected_duration": expected_duration,
                     "enforce_uncalibrated": enforce}
    tool_versions = {"ffmpeg": bool(_find_ffmpeg()), "probe": "shared.media_inspect.v1"}
    if not video_path.is_file():
        return MediaProbeResult(status=STATUS_UNAVAILABLE, measured=False,
                                reason="视频文件不存在", metrics=metrics,
                                tool_versions=tool_versions)
    info = _probe_media_info(video_path)
    streams = list(info.get("streams") or [])
    if not streams:
        return MediaProbeResult(status=STATUS_UNAVAILABLE, measured=False,
                                reason="ffprobe 读不到流（残缺/不可解码）",
                                metrics=metrics, tool_versions=tool_versions)
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        issues.append({"code": "media_no_video_stream", "kind": "decode",
                       "severity": "critical", "message": "文件没有视频流",
                       "evidence": {"path": str(video_path)}, "time_range": None})
    duration = None
    try:
        duration = float(info.get("duration"))
    except (TypeError, ValueError):
        duration = _stream_duration(streams, "video")
    metrics.update({
        "duration_sec": duration,
        "width": video_stream.get("width") if video_stream else None,
        "height": video_stream.get("height") if video_stream else None,
        "fps": video_stream.get("r_frame_rate") if video_stream else None,
    })
    if expected_duration and duration:
        dev = abs(duration - float(expected_duration))
        metrics["duration_dev_sec"] = round(dev, 4)
        if dev > _env_float("VIDEO_MEDIA_DURATION_DEV_SEC", 0.5):
            issues.append({"code": "media_duration_mismatch", "kind": "decode",
                           "severity": "critical",
                           "message": ("实际时长 %.2fs 与期望 %.2fs 相差 %.2fs"
                                       % (duration, float(expected_duration), dev)),
                           "evidence": {"duration_sec": duration,
                                        "expected_sec": float(expected_duration)},
                           "time_range": None})

    # ---- 黑场（blackdetect；局部=warning，覆盖过半=critical） ----
    stderr, err = _run_detector(video_path, "blackdetect=d=0.1:pix_th=0.10")
    if err:
        metrics["black_probe"] = err
    else:
        from shared.media_inspect import parse_blackdetect
        events, problems = parse_blackdetect(stderr)
        metrics["black_segments"] = [[round(e["start_sec"], 3), round(e["end_sec"], 3)]
                                     for e in events]
        total_black = sum(max(0.0, e["end_sec"] - e["start_sec"]) for e in events)
        metrics["black_ratio"] = (round(total_black / duration, 4)
                                  if duration and duration > 0 else None)
        if events and duration and duration > 0 and total_black >= duration * 0.6:
            issues.append({"code": "media_black_whole", "kind": "black",
                           "severity": _severity("media_black_whole"),
                           "message": "黑场覆盖成片 %.0f%%（整段黑屏）"
                                      % (100 * total_black / duration),
                           "evidence": {"segments": metrics["black_segments"]},
                           "time_range": [0.0, round(duration, 3)]})
        elif events:
            issues.append({"code": "media_black_segment", "kind": "black",
                           "severity": "warning",
                           "message": "检测到 %d 段黑场（可能是有意淡入/淡出）"
                                      % len(events),
                           "evidence": {"segments": metrics["black_segments"]},
                           "time_range": [round(events[0]["start_sec"], 3),
                                          round(events[-1]["end_sec"], 3)]})
        metrics["black_problems"] = problems

    # ---- 静帧（freezedetect；整片静帧=critical，局部=warning） ----
    stderr, err = _run_detector(video_path,
                                "freezedetect=n=-60dB:d=0.5")
    if err:
        metrics["freeze_probe"] = err
    else:
        from shared.media_inspect import parse_freezedetect
        events, problems = parse_freezedetect(stderr, duration_sec=duration)
        metrics["freeze_segments"] = [[round(e["start_sec"], 3),
                                       round(e["end_sec"] or 0.0, 3)] for e in events]
        frozen = sum(max(0.0, float(e["end_sec"] or 0.0) - e["start_sec"]) for e in events)
        metrics["freeze_ratio"] = (round(frozen / duration, 4)
                                   if duration and duration > 0 else None)
        if events and duration and duration > 0 and frozen >= duration * 0.9:
            issues.append({"code": "media_freeze_whole", "kind": "freeze",
                           "severity": _severity("media_freeze_whole"),
                           "message": "整片静帧（画面卡死 %.0f%%）"
                                      % (100 * frozen / duration),
                           "evidence": {"segments": metrics["freeze_segments"]},
                           "time_range": [0.0, round(duration, 3)]})
        elif events:
            issues.append({"code": "media_freeze_segment", "kind": "freeze",
                           "severity": "warning",
                           "message": "检测到 %d 段静帧（可能是有意静镜）" % len(events),
                           "evidence": {"segments": metrics["freeze_segments"]},
                           "time_range": None})
        metrics["freeze_problems"] = problems

    # ---- 首帧偏离（有输入帧时才测；模型重绘属正常，按结构相关分级） ----
    if first_frame_path is not None and Path(first_frame_path).is_file():
        sim, method = _first_frame_similarity(video_path, Path(first_frame_path))
        metrics["first_frame_similarity"] = sim
        metrics["first_frame_metric"] = method
        corr = sim.get("corr") if sim.get("comparable") else None
        if corr is not None:
            critical_below = _env_float("VIDEO_FIRST_FRAME_CRITICAL_CORR", -0.10)
            warn_below = _env_float("VIDEO_FIRST_FRAME_WARN_CORR", 0.25)
            evidence = {"structure_corr": corr,
                        "luma_similarity": sim.get("luma_similarity"),
                        "metric": method, "first_frame": str(first_frame_path)}
            if corr < critical_below:
                issues.append({"code": "media_first_frame_drift", "kind": "frame_binding",
                               "severity": _severity("media_first_frame_drift"),
                               "message": ("首帧与输入 first_frame 结构相关 %.2f < %.2f"
                                           "（画面几乎无关）" % (corr, critical_below)),
                               "evidence": evidence, "time_range": [0.0, 0.0]})
            elif corr < warn_below:
                issues.append({"code": "media_first_frame_drift", "kind": "frame_binding",
                               "severity": "warning",
                               "message": ("首帧与输入 first_frame 结构相关偏低 %.2f"
                                           "（需人工/视觉复核是否同一场景）" % corr),
                               "evidence": evidence, "time_range": [0.0, 0.0]})

    status = (STATUS_CRITICAL if any(i["severity"] == "critical" for i in issues)
              else (STATUS_CLEAN if not issues else "warned"))
    return MediaProbeResult(status=status, measured=True, issues=issues,
                            metrics=metrics, tool_versions=tool_versions)


@dataclass
class MediaProbeResult:
    """0 元探针结果：``measured=False`` 时 status 必为 unverified/unavailable."""

    status: str
    measured: bool = False
    issues: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    tool_versions: dict = field(default_factory=dict)
    reason: str | None = None

    @property
    def critical(self) -> list:
        return [i for i in self.issues if i.get("severity") == "critical"]

    def to_dict(self) -> dict:
        return {"status": self.status, "measured": self.measured,
                "issues": list(self.issues), "metrics": dict(self.metrics),
                "tool_versions": dict(self.tool_versions), "reason": self.reason}


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
        # S06/V3：空镜判据**单一权威**在 `shared.style_prefix.is_empty_scene`
        # （`vimax_bridge` 也用它）；不再用 `"空镜" in main_subject` 这种脆弱字面量。
        # F2 文字合同（`frame_text_spec`）由 `maybe_qc_sync` 按同一 shot 自行提取，
        # 这里只传真实的 shot/characters，不另建第二份 spec。
        from shared.style_prefix import is_empty_scene
        real_shot = dict(shot or {})
        real_shot.setdefault("index", shot_idx)
        empty_scene = bool(is_empty_scene(real_shot, characters or []))
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
