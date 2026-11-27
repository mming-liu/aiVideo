# -*- coding: utf-8 -*-
"""S06/M3：成片自动化终检（0 元媒体检查，写 `step04_quality.json` 的媒体部分）.

检查项（全部确定性、无模型调用，工具不可用时如实记 `unavailable`）：
1. **编码规范**：H.264 + AAC、配置的宽高/帧率、音频采样率；
2. **音画时长一致**：|视频时长 − 音频时长| ≤ 0.1s；
3. **音轨存在性**：按本次声音合同要求音频时必须真有音轨；
4. **黑场**：与时间线声明的转场/端点无关的黑场 ≥ 0.5s ⇒ 阻断；声明窗口内的黑场只记录；
5. **静帧**：整片静帧（≥90%）⇒ 阻断，局部静帧仅记录（有意静镜合法）；
6. **静音死区**：≥3s 的静音段记录为告警（是否"设计内"由声明决定，不自动判死）；
7. **字幕时间戳**：SRT 最后一条结束时间不得越过成片总时长；
8. **响度**：EBU R128 实测（目标 −16 LUFS）；偏差 > 3 LU 判阻断、> 1.5 LU 告警；
9. **moov atom 前置**：`moov` 在 `mdat` 之前（快启）；否则告警。

**分级（2026-11-27 复核后）**：
- **deterministic（默认硬拦）**：文件缺失、无视频/音频流、编码/分辨率/帧率/采样率不符、
  音画时长差 >100ms、成片与时间线时长不符、字幕结束越过成片、发布失败；
- **uncalibrated（默认影子评估，只告警）**：未声明黑场、整片静帧、响度偏离——
  这些阈值尚未用真实生成片校准，`MERGE_QUALITY_ENFORCE=1` 才升级为硬拦；
- **unavailable（未验证）**：必要检测跑不了时状态是 `UNVERIFIED`（**不是 PASS**），
  由调用方按 required checks 决定不发布。
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path

SCHEMA = "step04_media_quality.v1"

ENV_ENFORCE = "MERGE_QUALITY_ENFORCE"
#: 默认**必须真测到**的检查项（缺失即 UNVERIFIED，不能当 PASS）
DEFAULT_REQUIRED_CHECKS = ("video_stream", "audio_stream", "av_duration",
                           "black_detect", "freeze_detect", "loudness")

#: 音画时长容差（秒）——方案指标 ≤ 100ms
AV_DRIFT_TOLERANCE_SEC = 0.10
#: 未声明黑场的最短判废时长（秒）
UNDECLARED_BLACK_MIN_SEC = 0.5
#: 整片静帧判废比例
FULL_FREEZE_RATIO = 0.90
#: 静音死区告警下限（秒）
SILENCE_WARN_SEC = 3.0
#: 响度目标（LUFS）与容差
LOUDNESS_TARGET_LUFS = -16.0
LOUDNESS_WARN_LU = 1.5
LOUDNESS_BLOCK_LU = 3.0
#: 静帧检测参数与 blackdetect 一致（不复制 ffmpeg 默认值的语义）
_FREEZE_NOISE_DB = -60
_FREEZE_MIN_SEC = 0.5
_BLACK_MIN_SEC = 0.2
_BLACK_PIX_TH = 0.10

_LOUDNESS_I_RE = re.compile(r"I:\s*(?P<value>-?\d+(?:\.\d+)?)\s*LUFS")
_LOUDNESS_PEAK_RE = re.compile(r"Peak:\s*(?P<value>-?\d+(?:\.\d+)?)\s*dBFS")


def _ffmpeg() -> str | None:
    from shared.media_inspect import ffmpeg_executable
    try:
        return ffmpeg_executable()
    except Exception:
        return None


def _run(cmd: list, *, timeout: float = 120.0) -> tuple[int, str, str]:
    from shared.media_inspect import run_argv
    try:
        run = run_argv(cmd, timeout_sec=timeout)
    except Exception as exc:  # noqa: BLE001
        return 127, "", str(exc)
    return int(getattr(run, "returncode", 1)), str(getattr(run, "stdout", "") or ""), \
        str(getattr(run, "stderr", "") or "")


def _probe(path: Path) -> dict:
    from shared.media_inspect import probe_media
    try:
        return probe_media(path) or {}
    except Exception:
        return {}


def _streams(probe: dict) -> list:
    return list(probe.get("streams") or [])


def _stream_of(probe: dict, codec_type: str) -> dict | None:
    return next((s for s in _streams(probe) if s.get("codec_type") == codec_type), None)


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def measure_loudness(path: Path) -> dict:
    """EBU R128 实测（ffmpeg `ebur128` 滤镜；工具不可用/解析失败 → unavailable）."""
    exe = _ffmpeg()
    if not exe:
        return {"status": "unavailable", "reason": "ffmpeg_unavailable"}
    code, _out, err = _run([exe, "-hide_banner", "-nostats", "-i", str(path),
                            "-af", "ebur128=peak=true", "-f", "null", "-"])
    if code != 0:
        return {"status": "unavailable", "reason": f"ebur128_failed:{code}"}
    integrated = None
    peak = None
    for match in _LOUDNESS_I_RE.finditer(err):
        integrated = float(match.group("value"))
    for match in _LOUDNESS_PEAK_RE.finditer(err):
        peak = float(match.group("value"))
    if integrated is None:
        return {"status": "unavailable", "reason": "ebur128_unparsed"}
    return {"status": "measured", "integrated_lufs": integrated,
            "true_peak_dbfs": peak, "target_lufs": LOUDNESS_TARGET_LUFS}


def _detect_events(path: Path, vf: str, parser, *, audio: bool = False) -> tuple[list, str]:
    """跑一个 ffmpeg 检测滤镜到 null 输出（视频滤镜用 `-vf`，音频滤镜用 `-af`）."""
    exe = _ffmpeg()
    if not exe:
        return [], "ffmpeg_unavailable"
    filter_args = (["-af", vf, "-vn"] if audio else ["-vf", vf, "-an"])
    code, _out, err = _run([exe, "-hide_banner", "-nostats", "-v", "info",
                            "-i", str(path)] + filter_args + ["-f", "null", "-"])
    if code != 0:
        return [], f"detect_failed:{code}"
    events, _problems = parser(err)
    return events, ""


def _declared_windows(declared_black_windows) -> list:
    out = []
    for item in declared_black_windows or ():
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, IndexError, ValueError):
            continue
        if end >= start:
            out.append((start, end))
    return out


def _enforce_uncalibrated() -> bool:
    """未校准判据是否升级为硬拦（默认关闭 = 影子评估）."""
    return str(os.getenv(ENV_ENFORCE, "0")).strip().lower() in ("1", "true", "yes", "on")


def _uncovered_total(start: float, end: float, windows: list) -> float:
    """事件区间扣掉**已声明**窗口后，仍未覆盖的秒数（相交 ≠ 全豁免）."""
    segments = [(float(start), float(end))]
    for w_start, w_end in windows or ():
        remaining = []
        for seg_start, seg_end in segments:
            if w_end <= seg_start or w_start >= seg_end:
                remaining.append((seg_start, seg_end))
                continue
            if w_start > seg_start:
                remaining.append((seg_start, w_start))
            if w_end < seg_end:
                remaining.append((w_end, seg_end))
        segments = remaining
        if not segments:
            break
    return sum(max(0.0, seg_end - seg_start) for seg_start, seg_end in segments)


def _overlaps(start: float, end: float, windows: list) -> bool:
    return any(not (end < w_start or start > w_end) for w_start, w_end in windows)


def _parse_srt_last_end(path: Path) -> tuple[float | None, str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable:{exc}"
    last = None
    # 取每块 "start --> END" 里的 **END**（结束时间戳），最后一块即字幕总长
    pattern = re.compile(r"-->\s*(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})")
    for match in pattern.finditer(text):
        try:
            hh, mm, rest = match.group("end").split(":")
            ss, ms = re.split(r"[,.]", rest)
            last = int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
        except ValueError:
            continue
    return last, ""


def _moov_before_mdat(path: Path) -> tuple[bool | None, str]:
    """扫描 MP4 顶层 box 顺序：`moov` 必须在 `mdat` 之前（快启）."""
    try:
        with open(path, "rb") as fh:
            order: list[str] = []
            size = Path(path).stat().st_size
            offset = 0
            while offset < size and len(order) < 8:
                fh.seek(offset)
                header = fh.read(8)
                if len(header) < 8:
                    break
                box_size, box_type = struct.unpack(">I4s", header)
                kind = box_type.decode("latin-1")
                order.append(kind)
                if box_size == 1:
                    large = fh.read(8)
                    if len(large) < 8:
                        break
                    box_size = struct.unpack(">Q", large)[0]
                elif box_size == 0:
                    break
                if box_size < 8:
                    break
                offset += box_size
            if "moov" not in order or "mdat" not in order:
                return None, "boxes_not_found"
            return order.index("moov") < order.index("mdat"), ",".join(order)
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable:{exc}"


def evaluate_final(final_path, *, expected_duration: float | None = None,
                   width: int | None = None, height: int | None = None,
                   fps: int | float | None = None,
                   audio_required: bool = True,
                   audio_sample_rate: int | None = None,
                   video_codec: str = "h264",
                   audio_codec: str = "aac",
                   declared_black_windows=(),
                   srt_path=None,
                   subtitle_required: bool | None = None,
                   check_loudness: bool = True,
                   required_checks=None,
                   enforce_uncalibrated: bool | None = None) -> dict:
    """成片终检（0 元）。返回 ``{"schema","status","checks","defects","metrics"}``.

    ``status``：`BLOCK`（确定性问题/未校准项在 enforce 下命中）/
    `UNVERIFIED`（必要检测跑不了，**不是 PASS**）/ `PASS`。
    """
    final = Path(final_path)
    enforce = (_enforce_uncalibrated() if enforce_uncalibrated is None
               else bool(enforce_uncalibrated))
    required = set(DEFAULT_REQUIRED_CHECKS if required_checks is None
                   else required_checks)
    if not check_loudness:
        required.discard("loudness")
    # **字幕合同决定必检项**：有应交付字幕时，字幕缺失/不可读/未完成必须 UNVERIFIED，
    # 不能因为没有检查项就 PASS（2026-11-27 复核 P1）。
    _subtitle_required = (bool(subtitle_required) if subtitle_required is not None
                          else srt_path is not None)
    if _subtitle_required:
        required.add("subtitle_duration")
    else:
        required.discard("subtitle_duration")
    checks: list[dict] = []
    defects: list[dict] = []
    metrics: dict = {"expected_duration": expected_duration,
                     "enforce_uncalibrated": enforce}
    unverified: list[str] = []

    def _check(name: str, status: str, **detail) -> None:
        checks.append({"name": name, "status": status, **detail})
        if status == "unavailable" and name in required:
            unverified.append(name)

    def _defect(code: str, message: str, *, severity: str = "block", **detail) -> None:
        defects.append({"code": code, "severity": severity, "message": message, **detail})
        _check(code, "fail" if severity == "block" else "warn", message=message)

    def _soft_defect(code: str, message: str, **detail) -> None:
        """未校准判据：默认只告警；`MERGE_QUALITY_ENFORCE=1` 时升级为阻断."""
        _defect(code, message, severity="block" if enforce else "warn", **detail)

    if not final.is_file() or final.stat().st_size <= 2048:
        _defect("final_missing", "成片文件缺失或过小")
        return {"schema": SCHEMA, "status": "BLOCK", "checks": checks,
                "defects": defects, "metrics": metrics,
                "unverified": unverified, "provider_calls": 0}

    probe = _probe(final)
    video = _stream_of(probe, "video")
    audio = _stream_of(probe, "audio")
    if video is None:
        _check("video_stream", "fail")
        _defect("video_stream_missing", "成片没有视频流")
    else:
        _check("video_stream", "pass")
    if audio is None:
        if audio_required:
            _check("audio_stream", "fail")
            _defect("audio_stream_missing", "本次声音合同要求音频，但成片没有音频流")
        else:
            _check("audio_stream", "pass", note="不需要音频")
    else:
        _check("audio_stream", "pass")

    # ---- 1. 编码规范 ----
    if video is not None:
        codec = str(video.get("codec_name") or "")
        metrics.update({"video_codec": codec,
                        "width": video.get("width"), "height": video.get("height"),
                        "fps": video.get("r_frame_rate")})
        if codec and codec != str(video_codec):
            _defect("video_codec_unsupported",
                    f"视频编码 {codec} 不是目标 {video_codec}")
        if width and video.get("width") and int(video["width"]) != int(width):
            _defect("video_width_mismatch",
                    f"宽 {video['width']} ≠ 配置 {width}")
        if height and video.get("height") and int(video["height"]) != int(height):
            _defect("video_height_mismatch",
                    f"高 {video['height']} ≠ 配置 {height}")
        if fps:
            actual = str(video.get("r_frame_rate") or "")
            try:
                num, den = actual.split("/")
                actual_fps = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                actual_fps = None
            metrics["fps_float"] = actual_fps
            if actual_fps is not None and abs(actual_fps - float(fps)) > 0.5:
                _defect("video_fps_mismatch", f"帧率 {actual_fps:.2f} ≠ 配置 {fps}")
    if audio is not None:
        metrics.update({"audio_codec": audio.get("codec_name"),
                        "audio_sample_rate": audio.get("sample_rate"),
                        "audio_channels": audio.get("channels")})
        if str(audio.get("codec_name") or "") not in ("", str(audio_codec)):
            _defect("audio_codec_unsupported",
                    f"音频编码 {audio.get('codec_name')} 不是目标 {audio_codec}")
        if audio_sample_rate and str(audio.get("sample_rate") or ""):
            if int(audio["sample_rate"]) != int(audio_sample_rate):
                _defect("audio_sample_rate_mismatch",
                        f"采样率 {audio['sample_rate']} ≠ 配置 {audio_sample_rate}")

    # ---- 2. 音画时长一致 ----
    def _dur(stream, fallback=None):
        value = _num((stream or {}).get("duration"))
        if value is None:
            value = _num(probe.get("duration")) if fallback is None else fallback
        return value

    video_dur = _dur(video)
    audio_dur = _dur(audio)
    total = _num(probe.get("duration")) or video_dur
    metrics.update({"duration_sec": total, "video_duration_sec": video_dur,
                    "audio_duration_sec": audio_dur})
    if video_dur is not None and audio_dur is not None:
        drift = abs(video_dur - audio_dur)
        metrics["av_drift_ms"] = round(drift * 1000.0, 1)
        if drift > AV_DRIFT_TOLERANCE_SEC:
            _check("av_duration", "fail", drift_ms=round(drift * 1000.0, 1))
            _defect("av_duration_drift",
                    "音画时长差 %.0fms 超过 %.0fms"
                    % (drift * 1000, AV_DRIFT_TOLERANCE_SEC * 1000))
        else:
            _check("av_duration", "pass", drift_ms=round(drift * 1000.0, 1))
    elif audio is None and not audio_required:
        _check("av_duration", "pass", note="无音频合同")
    else:
        _check("av_duration", "unavailable",
               reason="音视频时长不可读", video_sec=video_dur, audio_sec=audio_dur)
    if expected_duration and total:
        delta = total - float(expected_duration)
        metrics["duration_delta_sec"] = round(delta, 4)
        if abs(delta) > max(0.25, float(expected_duration) * 0.02):
            _defect("final_duration_mismatch",
                    "成片 %.2fs 与时间线 %.2fs 不一致（差 %.2fs）"
                    % (total, float(expected_duration), delta))

    # ---- 3. 黑场（只豁免**已声明且完整覆盖**的部分） ----
    from shared.media_inspect import parse_blackdetect, parse_freezedetect
    black_events, black_err = _detect_events(
        final, "blackdetect=d=%.2f:pix_th=%.2f" % (_BLACK_MIN_SEC, _BLACK_PIX_TH),
        parse_blackdetect)
    if black_err:
        _check("black_detect", "unavailable", reason=black_err)
    else:
        declared = _declared_windows(declared_black_windows)
        unexpected = []
        for event in black_events:
            start, end = float(event["start_sec"]), float(event["end_sec"])
            metrics.setdefault("black_segments", []).append([round(start, 3), round(end, 3)])
            # 扣掉声明窗口后仍未覆盖的时长才算"未声明黑场"：
            # 只声明 0–0.5s 淡入时，0–9s 黑场仍有 8.5s 未被覆盖，不能整体豁免。
            uncovered = _uncovered_total(start, end, declared)
            if uncovered >= UNDECLARED_BLACK_MIN_SEC:
                unexpected.append((start, end, round(uncovered, 3)))
        if unexpected:
            _check("black_detect", "warn", segments=len(black_events),
                   uncovered=len(unexpected))
            _soft_defect("unexpected_black",
                         "未声明的黑场 %d 段：%s" % (len(unexpected),
                                                 [[round(s, 2), round(e, 2), u]
                                                  for s, e, u in unexpected[:4]]))
        else:
            _check("black_detect", "pass", segments=len(black_events))

    # ---- 4. 静帧 ----
    freeze_events, freeze_err = _detect_events(
        final, "freezedetect=n=%ddB:d=%.1f" % (_FREEZE_NOISE_DB, _FREEZE_MIN_SEC),
        lambda stderr: parse_freezedetect(stderr, duration_sec=total))
    if freeze_err:
        _check("freeze_detect", "unavailable", reason=freeze_err)
    else:
        frozen = 0.0
        for event in freeze_events:
            end = float(event.get("end_sec") or total or 0.0)
            start = float(event["start_sec"])
            frozen += max(0.0, end - start)
            metrics.setdefault("freeze_segments", []).append([round(start, 3), round(end, 3)])
        ratio = (frozen / total) if total else 0.0
        metrics["freeze_ratio"] = round(ratio, 4)
        if total and ratio >= FULL_FREEZE_RATIO:
            _check("freeze_detect", "warn", ratio=round(ratio, 4))
            _soft_defect("final_frozen", "整片静帧 %.0f%%（画面卡死）" % (100 * ratio))
        elif freeze_events:
            _check("freeze_detect", "warn", ratio=round(ratio, 4))
        else:
            _check("freeze_detect", "pass")

    # ---- 5. 静音死区（告警；是否设计内由声明决定，不自动判死） ----
    from shared.media_inspect import parse_silencedetect
    silence_events, silence_err = _detect_events(
        final, "silencedetect=n=-50dB:d=%.1f" % SILENCE_WARN_SEC,
        lambda stderr: parse_silencedetect(stderr, duration_sec=total), audio=True)
    if silence_err:
        _check("silence_detect", "unavailable", reason=silence_err)
    else:
        long_silences = [[round(float(e["start_sec"]), 3),
                          round(float(e.get("end_sec") or total or 0.0), 3)]
                         for e in silence_events]
        metrics["silence_segments"] = long_silences
        if long_silences:
            _check("silence_detect", "warn", segments=long_silences)
        else:
            _check("silence_detect", "pass")

    # ---- 6. 字幕时间戳不越过成片总时长 ----
    if _subtitle_required or srt_path is not None:
        if srt_path is None:
            _check("subtitle_duration", "unavailable", reason="subtitle_path_missing")
        elif not Path(srt_path).is_file():
            _check("subtitle_duration", "unavailable", reason="subtitle_file_missing",
                   path=str(srt_path))
        else:
            last_end, srt_err = _parse_srt_last_end(Path(srt_path))
            if srt_err:
                _check("subtitle_duration", "unavailable", reason=srt_err)
            elif last_end is None or total is None:
                _check("subtitle_duration", "unavailable",
                       reason="subtitle_unparsed_or_duration_unknown")
            else:
                metrics["srt_last_end_sec"] = round(last_end, 3)
                if last_end > total + 0.05:
                    _check("subtitle_duration", "fail", last_end_sec=round(last_end, 3))
                    _defect("subtitle_beyond_duration",
                            "字幕最后结束 %.2fs 超过成片 %.2fs" % (last_end, total))
                else:
                    _check("subtitle_duration", "pass",
                           last_end_sec=round(last_end, 3))

    # ---- 7. 响度（EBU R128） ----
    if check_loudness and audio is not None:
        loud = measure_loudness(final)
        metrics["loudness"] = loud
        if loud.get("status") == "measured":
            value = float(loud["integrated_lufs"])
            diff = abs(value - LOUDNESS_TARGET_LUFS)
            if value <= -60.0:
                # 整轨静音（测试样片/design 内静音）：响度目标无从谈起，
                # 记 warn 并交给静音检测说明，不按"响度不达标"判废。
                _check("loudness", "warn", integrated_lufs=value,
                       reason="silent_track")
            elif diff > LOUDNESS_BLOCK_LU:
                _check("loudness", "warn", integrated_lufs=value,
                       delta_lu=round(diff, 2))
                _soft_defect("loudness_out_of_range",
                             "实测响度 %.1f LUFS 偏离目标 %.1f LUFS 超过 %.1f LU"
                             % (value, LOUDNESS_TARGET_LUFS, LOUDNESS_BLOCK_LU))
            elif diff > LOUDNESS_WARN_LU:
                _check("loudness", "warn", integrated_lufs=value, delta_lu=round(diff, 2))
            else:
                _check("loudness", "pass", integrated_lufs=value,
                       true_peak_dbfs=loud.get("true_peak_dbfs"))
        else:
            _check("loudness", "unavailable", reason=loud.get("reason"))

    # ---- 8. moov 前置（快启；只告警，不改既有编码行为） ----
    faststart, box_detail = _moov_before_mdat(final)
    metrics["moov_before_mdat"] = faststart
    if faststart is False:
        _check("moov_faststart", "warn", detail=box_detail)
    elif faststart is True:
        _check("moov_faststart", "pass")
    else:
        _check("moov_faststart", "unavailable", detail=box_detail)

    # 必检项必须**实际产出有效结果**：没有检查项、或结果是 unavailable，都算未验证。
    # （只统计已返回 unavailable 的列表会漏掉"从未产生检查项"的情况——复核 P1。）
    _by_name = {c.get("name"): c for c in checks}
    unverified = [name for name in sorted(required)
                  if name not in _by_name
                  or _by_name[name].get("status") == "unavailable"]
    blocking = [d for d in defects if d.get("severity") == "block"]
    if blocking:
        status = "BLOCK"
    elif unverified:
        # 必要检测跑不了 ⇒ **未验证**，不得当 PASS 采纳
        status = "UNVERIFIED"
    else:
        status = "PASS"
    return {"schema": SCHEMA, "status": status,
            "checks": checks, "defects": defects, "metrics": metrics,
            "unverified": unverified, "provider_calls": 0}


__all__ = ["AV_DRIFT_TOLERANCE_SEC", "LOUDNESS_TARGET_LUFS", "SCHEMA",
           "evaluate_final", "measure_loudness"]
