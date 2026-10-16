# -*- coding: utf-8 -*-
"""step04_merge.backends.ffmpeg - 唯一 FFmpeg 后端：滤镜编译 + 执行 + 产物验收（计划 §S2/§S3）.

关键不变量：

- **输入次序**：clips → 逐镜 TTS → 图外旁白 → PNG 序列；``-i`` 参数与 filter 里的
  ``[i:v]``/``[i:a]`` 索引来自**同一个** :func:`build_inputs` 列表（extra 输入数同源）。
- **失败阶段可区分**：validate / compile / encode / verify / publish；任何一段失败都
  **不**静默换内核、不改声音策略，临时文件删除、旧 accepted 不动。
- **端点与边界分开**：首淡入/末淡出是端点效果（不改总长）；边界过渡用 xfade/acrossfade
  并按 ``start_i = total_{i-1} - tdur_i`` 递推出的绝对 offset。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.media_contract import (
    BOUNDARY_CUT,
    ENDPOINT_FADE_IN,
    ENDPOINT_FADE_OUT,
    MERGE_FPS,
    quantize_frame_count,
)
from shared.subtitle_contract import SubtitleStyle, ffmpeg_subtitle_filter
from step04_merge.timeline import MergeTimeline, TimelineError, validate_timeline

BACKEND_NAME = "ffmpeg"

#: 与 B 包共用的解码证据 schema（**恰好**这六个字段；B 只在同哈希且同 ffmpeg build 时复用）
SCHEMA_MEDIA_DECODE_EVIDENCE = "media_decode_evidence.v1"

#: AAC 帧长（1024 采样）——时长判据要同时容纳视频帧与音频帧粒度
AAC_FRAME_SAMPLES = 1024
AUDIO_SAMPLE_RATE = 44100

#: `enable` 表达式的**浮点容差**（秒）：帧时间戳 `N*(1/fps)` 与边界 `end_frame/fps`
#: 可能差 1 ulp（1e-15 量级）。容差必须远大于它、又远小于任何一帧——**不能退半帧**，
#: 那会改变非帧对齐窗口（如 `[0.52, 1.02)`）的取整规则（复核第十二轮二轮 P2）。
ENABLE_EPS = 1e-6


class RenderError(TimelineError):
    """渲染失败（带阶段）：encode / verify / publish 分别可辨。"""

    def __init__(self, code: str, stage: str, message: str, *, exit_code: int = 3, **fields):
        super().__init__(code, message, stage=stage, exit_code=exit_code, **fields)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blob in iter(lambda: f.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


# ------------------------------------------------------------------ 设置与结果

@dataclass(frozen=True)
class EncodeSettings:
    """编码/滤镜参数（由调用方从现有配置构造，后端不自读 env）。"""

    width: int = 720
    height: int = 1280
    crf: str = "18"
    preset: str = "medium"
    audio_volume: str = ""            # FFMPEG_AUDIO_VOLUME（固定增益，旧行为兼容）
    loudnorm: bool = True             # EBU R128 响度归一
    tts_base_gain: float = 0.6        # tts_mix：原声权重（TTS 固定 1.0）
    narration_base_gain: float = 0.6  # 图外旁白：拼接后原声权重
    narration_gain: float = 1.0       # 图外旁白权重
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    require_explicit_font: bool = False
    audio_encoder_args: tuple = ("-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2")
    video_encoder_args: tuple = ("-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
                                 "-pix_fmt", "yuv420p")
    ffmpeg: str | None = None
    ffprobe: str | None = None
    timeout_sec: float | None = None


@dataclass(frozen=True)
class MediaInfo:
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass(frozen=True)
class InputSpec:
    """一个 FFmpeg 输入：``index`` 就是 filter 图里用的 ``[i:*]``。"""

    index: int
    role: str          # "clip" | "shot_tts" | "narration" | "overlay"
    path: Path
    stable_id: str
    extra_args: tuple = ()

    def args(self) -> list:
        return [*self.extra_args, "-i", str(self.path)]


@dataclass(frozen=True)
class RenderResult:
    path: Path
    sha256: str
    ffmpeg_version: str
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool
    bytes: int
    elapsed_sec: float
    expected_duration_sec: float
    stages: tuple[str, ...]
    failure_stage: None = None
    command: tuple = ()


# ------------------------------------------------------------------ 探测

def _find_exe(name: str, explicit: str | None = None) -> str:
    if explicit:
        return str(explicit)
    path = shutil.which(name)
    if not path:
        envelope = os.getenv("FFMPEG_BIN_DIR", "").strip()
        if envelope:
            candidate = Path(envelope) / f"{name}.exe"
            if candidate.exists():
                return str(candidate)
        raise RenderError("tool_missing", "compile", f"找不到 {name}（请安装 FFmpeg）",
                          exit_code=3, tool=name)
    return path


def _fraction(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return 0.0
        return num_f / den_f if den_f else 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_media(path, *, ffprobe: str | None = None) -> MediaInfo:
    """ffprobe 实测：时长 / 尺寸 / 帧率 / 是否有音轨（探测失败显式报错）。"""
    exe = _find_exe("ffprobe", ffprobe)
    cmd = [exe, "-v", "error", "-show_entries",
           "format=duration:stream=codec_type,width,height,r_frame_rate,avg_frame_rate",
           "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, check=False)
    except OSError as exc:
        raise RenderError("probe_failed", "verify", f"ffprobe 调用失败: {exc}") from exc
    if proc.returncode != 0:
        raise RenderError("probe_failed", "verify",
                          f"ffprobe 探测失败（{Path(path).name}）: {(proc.stderr or '')[-300:]}")
    import json as _json
    try:
        data = _json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise RenderError("probe_failed", "verify", f"ffprobe 输出不可解析: {exc}") from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = 0.0
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    fps = 0.0
    if video:
        fps = _fraction(str(video.get("avg_frame_rate") or "")) or \
            _fraction(str(video.get("r_frame_rate") or ""))
    return MediaInfo(duration_sec=duration, width=int((video or {}).get("width") or 0),
                     height=int((video or {}).get("height") or 0), fps=fps, has_audio=has_audio)


def ffmpeg_version(*, ffmpeg: str | None = None) -> str:
    exe = _find_exe("ffmpeg", ffmpeg)
    proc = subprocess.run([exe, "-version"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, check=False)
    first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    parts = first.split()
    if len(parts) >= 3 and parts[0] == "ffmpeg":
        return parts[2]
    return first.strip() or "unknown"


def build_decode_evidence(media_path, *, argv, returncode, stderr_text,
                          ffmpeg: str | None = None) -> dict:
    """``media_decode_evidence.v1``：恰好六个字段（B 逐字消费）.

    B 只在**同 input_hash 且同 ffmpeg_build** 时复用它；否则自行解码并在 notes 写 not_reused。
    """
    return {
        "input_hash": _sha256_file(Path(media_path)),
        "ffmpeg_build": ffmpeg_version(ffmpeg=ffmpeg),
        "argv": [str(a) for a in argv],
        "returncode": int(returncode),
        "stderr_digest": hashlib.sha256((stderr_text or "").encode("utf-8")).hexdigest(),
        "status": "decoded" if int(returncode) == 0 else "failed",
    }


def write_decode_evidence(directory, record: dict) -> Path:
    """把解码证据写到 ``<dir>/media_decode_evidence.json``（候选/验收侧的固定位置）."""
    target = Path(directory) / "media_decode_evidence.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def duration_tolerance_sec(fps: int = MERGE_FPS) -> float:
    """时长判据：一视频帧 + 一 AAC 帧（编码粒度），对 24fps/44.1kHz ≈ 0.083s。"""
    return 1.0 / float(fps) + max(1.0 / float(fps),
                                  AAC_FRAME_SAMPLES / float(AUDIO_SAMPLE_RATE))


# ------------------------------------------------------------------ 输入列表（同源）

def build_inputs(timeline: MergeTimeline) -> tuple[InputSpec, ...]:
    """按固定次序构造输入：clips → 逐镜 TTS → 图外旁白 → PNG 序列。

    ``-i`` 参数与 filter 索引**只**能从这个列表派生（不许两处各自遍历、谓词不同）。
    """
    inputs: list[InputSpec] = []
    clip_input_index: dict[int, int] = {}
    for position, clip in enumerate(timeline.clips):
        clip_input_index[position] = len(inputs)
        inputs.append(InputSpec(len(inputs), "clip", Path(clip.path), clip.stable_id))
    tts_by_clip: dict[int, int] = {}
    if timeline.audio_mode == "tts_mix":
        for position in range(len(timeline.clips)):
            # 注意：clip_index=0 是**合法**值，不能用 `or -1` 兜底（会把第 1 镜的 TTS 丢掉）
            match = next((t for t in timeline.audio_tracks
                          if t.kind == "shot_tts" and t.clip_index is not None
                          and int(t.clip_index) == position), None)
            if match is None:
                continue
            tts_by_clip[position] = len(inputs)
            inputs.append(InputSpec(len(inputs), "shot_tts", Path(match.path), match.stable_id))
    narration_inputs: list[int] = []
    for track in timeline.audio_tracks:
        if track.kind != "narration":
            continue
        narration_inputs.append(len(inputs))
        inputs.append(InputSpec(len(inputs), "narration", Path(track.path), track.stable_id))
    overlay_inputs: list[int] = []
    for overlay in timeline.overlays:
        extra: tuple = ()
        if overlay.kind == "png_sequence":
            extra = ("-framerate", str(int(overlay.fps or MERGE_FPS)))
        overlay_inputs.append(len(inputs))
        inputs.append(InputSpec(len(inputs), "overlay", Path(overlay.path),
                                overlay.overlay_id, extra))
    return tuple(inputs)


def _index_map(inputs) -> dict:
    """稳定身份 → 输入序号（不靠数组位置猜身份）。"""
    return {spec.stable_id: spec.index for spec in inputs}


# ------------------------------------------------------------------ 滤镜编译

def compile_graph(timeline: MergeTimeline, settings: EncodeSettings, *,
                  color_filter: str = "", subtitle_filter: str = "",
                  trailing_filter: str = "", quality_warnings: list | None = None,
                  logger=None) -> tuple[tuple[InputSpec, ...], str, str, str, dict]:
    """编译 ``(inputs, filter_complex, video_label, audio_label, meta)``。

    非法时间线/非法字体/缺字形在这里（编码前）拒收。
    """
    warnings: list[str] = quality_warnings if quality_warnings is not None else []

    def _warn(message: str) -> None:
        warnings.append(message)
        if logger is not None:
            getattr(logger, "warn", logger.info)(message)

    validated = timeline  # validate_timeline 由 render() 先行调用
    inputs = build_inputs(validated)
    index_of = _index_map(inputs)
    windows = validated.windows
    total = validated.total_duration_sec
    fps = int(validated.fps or MERGE_FPS)
    width, height = int(validated.width), int(validated.height)
    boundaries = {int(b.index): b for b in validated.boundary_transitions}
    endpoints = {int(e.clip_index): e for e in validated.endpoint_effects}

    if validated.subtitle_plan is not None and validated.subtitle_plan.cues:
        style: SubtitleStyle = settings.subtitle_style
        if settings.require_explicit_font and style.font is None:
            raise RenderError(
                "subtitle_font_not_configured", "compile",
                "增强/候选模式必须给显式子集字体（MERGE_SUBTITLE_FONT_FILE + 摘要）；"
                "缺字体不静默 fallback 到系统字体", exit_code=2)
        if style.font is not None:
            from shared.subtitle_contract import require_glyph_coverage
            try:
                require_glyph_coverage(style.font, [c.text for c in validated.subtitle_plan.cues])
            except Exception as exc:  # SubtitleContractError
                raise RenderError(getattr(exc, "code", "font_rejected"), "compile",
                                  str(exc), exit_code=2) from exc

    parts: list[str] = []
    for position, clip in enumerate(validated.clips):
        in_index = index_of[clip.stable_id]
        duration = float(clip.duration_sec)
        chain = (
            f"[{in_index}:v]fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,format=yuv420p,"
            f"trim=duration={duration:.6f},setpts=PTS-STARTPTS,settb=AVTB"
        )
        effect = endpoints.get(position)
        if effect is not None and effect.kind == ENDPOINT_FADE_IN:
            chain += f",fade=t=in:st=0:d={min(float(effect.duration_sec), duration):.6f}"
        parts.append(f"{chain}[nv{position}]")

        if clip.has_audio:
            audio_chain = (
                f"[{in_index}:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample={AUDIO_SAMPLE_RATE}"
            )
        else:
            _warn(f"[WARN] 片段无音频流（补静音轨）: {Path(clip.path).name}")
            # anullsrc 的选项是 `channel_layout`（复数写法在 ffmpeg 8.1 直接 "Option not found"，
            # 旧实现的 `channel_layouts=stereo` 从来没有真正跑通过——这里一并修正）
            audio_chain = (
                f"anullsrc=r={AUDIO_SAMPLE_RATE}:channel_layout=stereo,"
                f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo"
            )
        if settings.loudnorm and not settings.audio_volume:
            audio_chain += ",loudnorm=I=-16:TP=-1.5:LRA=11"
        elif settings.audio_volume:
            audio_chain += f",volume={settings.audio_volume}"

        tts_index = index_of.get(_tts_stable_id(validated, position))
        if tts_index is not None:
            parts.append(
                f"[{tts_index}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"aresample={AUDIO_SAMPLE_RATE},atrim=duration={duration:.6f},"
                f"asetpts=PTS-STARTPTS,apad=whole_dur={duration:.6f}[tts{position}]"
            )
            audio_chain += (
                f",[tts{position}]amix=inputs=2:duration=first:"
                f"weights={settings.tts_base_gain} 1[na{position}]"
            )
        else:
            audio_chain += f"[na{position}]"
        parts.append(audio_chain)

    cur_v, cur_a = "[nv0]", "[na0]"
    for i in range(1, len(validated.clips)):
        boundary = boundaries[int(i)]
        if boundary.kind == BOUNDARY_CUT or float(boundary.duration_sec) <= 0.01:
            parts.append(f"{cur_v}[nv{i}]concat=n=2:v=1:a=0[vx{i}]")
            parts.append(f"{cur_a}[na{i}]concat=n=2:v=0:a=1[ax{i}]")
        else:
            offset = max(0.0, windows[i].start_sec - 0.001)
            parts.append(
                f"{cur_v}[nv{i}]xfade=transition={boundary.kind}:"
                f"duration={float(boundary.duration_sec):.6f}:offset={offset:.6f}[vx{i}]")
            parts.append(f"{cur_a}[na{i}]acrossfade=d={float(boundary.duration_sec):.6f}[ax{i}]")
        cur_v, cur_a = f"[vx{i}]", f"[ax{i}]"

    last_effect = endpoints.get(len(validated.clips) - 1)
    if last_effect is not None and last_effect.kind == ENDPOINT_FADE_OUT:
        fade_d = min(float(last_effect.duration_sec),
                     float(validated.clips[-1].duration_sec))
        parts.append(f"{cur_v}fade=t=out:st={max(0.0, total - fade_d):.6f}:"
                     f"d={fade_d:.6f}[nvout]")
        cur_v = "[nvout]"

    narration_tracks = [t for t in validated.audio_tracks if t.kind == "narration"]
    if narration_tracks:
        labels = []
        for j, track in enumerate(narration_tracks):
            in_index = index_of[track.stable_id]
            delay_ms = max(0, int(round(float(track.start_sec or 0.0) * 1000)))
            parts.append(
                f"[{in_index}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"aresample={AUDIO_SAMPLE_RATE},adelay={delay_ms}|{delay_ms}[nr{j}]"
            )
            labels.append(f"[nr{j}]")
        weights = " ".join([str(settings.narration_base_gain)]
                           + [str(float(t.gain or settings.narration_gain)) for t in narration_tracks])
        parts.append(
            f"{cur_a}{''.join(labels)}amix=inputs={len(labels) + 1}"
            f":duration=first:normalize=0:weights={weights}[outa]")
        cur_a = "[outa]"

    # 调色 → 动效叠加 → 字幕（A4 固定次序）
    if color_filter:
        parts.append(f"{cur_v}{color_filter}[vcol]")
        cur_v = "[vcol]"
    for j, overlay in enumerate(validated.overlays):
        in_index = index_of[overlay.overlay_id]
        start_sec, end_sec = float(overlay.start_sec), float(overlay.end_sec)
        chain = f"[{in_index}:v]format=rgba"
        if overlay.kind == "png_sequence":
            chain += f",setpts=PTS-STARTPTS+{start_sec:.6f}/TB"
        chain += f",scale={width}:{height}[ov{j}]"
        parts.append(chain)
        if overlay.kind == "png_sequence":
            # 序列按实际 fps 从 start 起定位；播完即止，**不重复最后一帧**
            eof_args = "eof_action=pass:repeatlast=0"
        else:
            # 单帧图（title/letter）：保持到 enable 窗口结束
            eof_args = "eof_action=repeat:repeatlast=1"
        # ⚠️ **半开窗口 `[start, end)`**（复核第十二轮补查 P2）：清单里的窗口一直是半开的
        # （`start_frame` / `end_frame`），而 `between(t,S,E)` **两端都含**——实测 24fps
        # 下窗口第 12~23 帧，第 24 帧仍然有卡片，第 25 帧才消失（多显示一帧）。
        #
        # ⚠️⚠️ 容差只能是**常数**，不能是"半帧"（复核第十二轮二轮 P2）：把整个窗口平移
        # 半帧等于**改了时间取整规则**——消费者允许**非帧对齐**的秒数窗口，实测
        # `[0.52, 1.02)` @24fps 会被平移成"第 12 帧就出现、第 24 帧已经消失"（各错一帧）。
        # 真正要补偿的只是浮点表示误差：帧时间戳是 `N*(1/fps)`、边界是 `E=end_frame/fps`，
        # 两者可能差 1 ulp（1e-15 量级），所以取 1e-6 秒——比 ulp 大九个数量级，
        # 又比任何一帧都小得多，**不改变窗口的语义**。
        parts.append(
            f"{cur_v}[ov{j}]overlay=0:0:format=auto:{eof_args}:"
            f"enable='gte(t,{start_sec - ENABLE_EPS:.9f})"
            f"*lt(t,{end_sec - ENABLE_EPS:.9f})'[vov{j}]")
        cur_v = f"[vov{j}]"

    tail_filters = [f for f in (subtitle_filter, trailing_filter) if f]
    if tail_filters:
        parts.append(f"{cur_v}{','.join(tail_filters)}[outv]")
    else:
        parts.append(f"{cur_v}null[outv]")
    cur_v = "[outv]"

    meta = {"total_duration_sec": total,
            "windows": [(w.start_sec, w.end_sec) for w in windows],
            "inputs": [{"index": s.index, "role": s.role, "path": str(s.path),
                        "stable_id": s.stable_id} for s in inputs],
            "warnings": list(warnings)}
    return inputs, ";".join(parts), cur_v, cur_a, meta


def _tts_stable_id(timeline: MergeTimeline, clip_position: int) -> str:
    for track in timeline.audio_tracks:
        if track.kind == "shot_tts" and track.clip_index is not None                 and int(track.clip_index) == clip_position:
            return track.stable_id
    return ""


def command_for(timeline: MergeTimeline, settings: EncodeSettings, inputs, filter_complex: str,
                v_label: str, a_label: str, output: Path) -> list:
    ffmpeg = _find_exe("ffmpeg", settings.ffmpeg)
    cmd = [ffmpeg, "-y", "-nostdin", "-hide_banner"]
    for spec in inputs:
        cmd.extend(spec.args())
    cmd.extend(["-filter_complex", filter_complex, "-map", v_label, "-map", a_label])
    cmd.extend(list(settings.video_encoder_args))
    cmd.extend(["-preset", str(settings.preset), "-crf", str(settings.crf)])
    cmd.extend(list(settings.audio_encoder_args))
    cmd.append(str(output))
    return cmd


# ------------------------------------------------------------------ 渲染

def render(timeline: MergeTimeline, candidate_path, work_dir, *, settings: EncodeSettings | None = None,
           color_filter: str = "", subtitle_filter: str = "", trailing_filter: str = "",
           logger=None, srt_path=None, timeout_sec: float | None = None,
           evidence_dir=None) -> RenderResult:
    """把已验证的时间线渲染成候选文件；返回 :class:`RenderResult`。

    失败按阶段抛 :class:`RenderError`，工作目录里的临时文件被删除；调用方据此
    保留旧 accepted（不做静默降级、不换内核）。
    """
    settings = settings or EncodeSettings()
    started = time.monotonic()
    stages: list[str] = []
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    target = Path(candidate_path)
    tmp = work / (target.stem + ".tmp.mp4")
    if tmp.exists():
        tmp.unlink()

    # 1) validate
    try:
        validated = validate_timeline(timeline)
    except TimelineError:
        raise
    except Exception as exc:
        raise RenderError(getattr(exc, "code", "validate_failed"), "validate", str(exc),
                          exit_code=2) from exc
    stages.append("validate")

    # 2) compile（字幕侧先按同一份 cue 列表落盘/核对）
    if subtitle_filter == "" and validated.subtitle_plan is not None \
            and validated.subtitle_plan.cues and srt_path is not None:
        try:
            subtitle_filter = ffmpeg_subtitle_filter(srt_path, validated.subtitle_plan,
                                                     settings.subtitle_style)
        except Exception as exc:
            raise RenderError(getattr(exc, "code", "subtitle_rejected"), "compile", str(exc),
                              exit_code=2) from exc
    try:
        inputs, filter_complex, v_label, a_label, meta = compile_graph(
            validated, settings, color_filter=color_filter, subtitle_filter=subtitle_filter,
            trailing_filter=trailing_filter, logger=logger)
    except RenderError:
        raise
    except TimelineError:
        raise
    except Exception as exc:
        raise RenderError(getattr(exc, "code", "compile_failed"), "compile", str(exc),
                          exit_code=2) from exc
    cmd = command_for(validated, settings, inputs, filter_complex, v_label, a_label, tmp)
    stages.append("compile")

    log_path = work / (target.stem + ".ffmpeg.log")
    deadline = timeout_sec if timeout_sec is not None else settings.timeout_sec
    proc = None
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
            try:
                returncode = proc.wait(timeout=deadline)
            except subprocess.TimeoutExpired as exc:
                _terminate(proc)
                raise RenderError("encode_timeout", "encode",
                                  f"FFmpeg 编码超时（>{deadline}s）", exit_code=3) from exc
        if returncode != 0:
            raise RenderError("encode_failed", "encode",
                              f"FFmpeg 编码失败（rc={returncode}）: {_log_tail(log_path)}",
                              exit_code=3)
        stages.append("encode")

        # 3) verify：完整解码 + 实测媒体 + 时长/音轨判据
        info = probe_media(tmp, ffprobe=settings.ffprobe)
        expected = float(meta["total_duration_sec"])
        tolerance = duration_tolerance_sec(int(validated.fps or MERGE_FPS))
        if info.duration_sec <= 0:
            raise RenderError("verify_duration_unreadable", "verify",
                              "产物时长探测失败", exit_code=4)
        if abs(info.duration_sec - expected) > tolerance:
            raise RenderError(
                "verify_duration_mismatch", "verify",
                f"产物时长偏差过大: actual={info.duration_sec:.3f}s "
                f"expected={expected:.3f}s tolerance={tolerance:.3f}s", exit_code=4)
        if (info.width, info.height) != (int(validated.width), int(validated.height)):
            raise RenderError(
                "verify_dimension_mismatch", "verify",
                f"产物尺寸不符: {info.width}x{info.height} != "
                f"{int(validated.width)}x{int(validated.height)}", exit_code=4)
        if abs(info.fps - float(validated.fps)) > 0.01:
            raise RenderError("verify_fps_mismatch", "verify",
                              f"产物帧率不符: {info.fps} != {validated.fps}", exit_code=4)
        if not info.has_audio:
            raise RenderError("verify_no_audio", "verify",
                              "产物没有音频流，拒绝标记为可发布", exit_code=4)
        decode_argv, decode_rc, decode_stderr = _decode_check(tmp, settings)
        if evidence_dir is not None:
            # 候选验收侧的解码证据（B/C 共用 schema；生产路径不写，输出契约不变）
            write_decode_evidence(evidence_dir, build_decode_evidence(
                tmp, argv=decode_argv, returncode=decode_rc, stderr_text=decode_stderr,
                ffmpeg=settings.ffmpeg))
        stages.append("verify")

        # 4) publish：原子替换到候选路径（同卷；失败不动旧文件）
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(tmp, target)
        except OSError as exc:
            raise RenderError("publish_failed", "publish",
                              f"候选文件发布失败: {exc}", exit_code=3) from exc
        stages.append("publish")
    except BaseException:
        if proc is not None and proc.poll() is None:
            _terminate(proc)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise

    return RenderResult(
        path=target, sha256=_sha256_file(target),
        ffmpeg_version=ffmpeg_version(ffmpeg=settings.ffmpeg),
        duration_sec=info.duration_sec, width=info.width, height=info.height, fps=info.fps,
        has_audio=info.has_audio, bytes=target.stat().st_size,
        elapsed_sec=round(time.monotonic() - started, 3),
        expected_duration_sec=expected, stages=tuple(stages), command=tuple(cmd),
    )


def _terminate(proc) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _log_tail(path: Path, limit: int = 600) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def _decode_check(path: Path, settings: EncodeSettings, timeout: float = 300.0):
    """完整解码一遍；返回 ``(argv, returncode, stderr)`` 供解码证据留档."""
    exe = _find_exe("ffmpeg", settings.ffmpeg)
    argv = [exe, "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"]
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False)
    stderr = proc.stderr or proc.stdout or ""
    if proc.returncode != 0:
        raise RenderError("verify_decode_failed", "verify",
                          f"产物完整解码失败: {stderr[-400:]}", exit_code=4)
    return argv, proc.returncode, stderr


def get_backend(name: str = BACKEND_NAME):
    """后端注册表：只提供 ffmpeg（§S1 固定选型，不做双后端）。"""
    if name != BACKEND_NAME:
        raise RenderError("unknown_backend", "compile",
                          f"未知后端 {name!r}（本项目固定 {BACKEND_NAME}）", exit_code=2)
    return sys.modules[__name__]


__all__ = [
    "BACKEND_NAME", "SCHEMA_MEDIA_DECODE_EVIDENCE", "EncodeSettings", "InputSpec", "MediaInfo",
    "RenderError", "RenderResult", "build_decode_evidence", "build_inputs", "command_for",
    "compile_graph", "duration_tolerance_sec", "ffmpeg_version", "get_backend", "probe_media",
    "render", "write_decode_evidence",
]
