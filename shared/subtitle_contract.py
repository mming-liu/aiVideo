# -*- coding: utf-8 -*-
"""shared.subtitle_contract - 字幕 cue 的**唯一**时窗来源（计划 §15「字幕cue唯一源」、§C2）.

规格要点：

1. 原字幕解析（旧 ``generate_srt`` 的按标点分句 / 逐镜时窗）与合法对齐结果，
   各由**一个适配器**转成 :class:`SubtitleCue` 列表；
2. SRT 导出（:func:`render_srt`）与 FFmpeg ``subtitles`` 滤镜
   （:func:`ffmpeg_subtitle_filter`）消费**同一份** cue 列表——滤镜侧在编译前
   按字节比对盘上的 SRT 与列表渲染结果，不一致即拒收（``srt_cues_mismatch``），
   从而机械地禁止"两侧各自重新切句/重算时窗"；
3. 字体只认**显式路径 + 摘要**：缺字体文件（``font_missing``）、摘要不符
   （``font_digest_mismatch``）、字体不可解析（``font_unreadable``）、
   正文里有字体覆盖不到的字形（``missing_glyphs``）都在**编码前**拒收，
   不静默回退到系统字体解析。

**字体机制（可执行，不靠约定）**：ASS/force_style 没有 fontfile 字段，字体只能表达成
``fontsdir=<字体所在目录>`` + ``FontName=<字体 family>``；family 从字体自身的 ``name``
表（nameID=1）读出，字形覆盖由本模块**自己解析** ``cmap``（格式 4/12，含 ttc）判定。
两处缺口——目录里没有该字体、正文字形不在 cmap 里——都在编码前拒收。
本模块只依赖标准库：不引入 fontTools/Pillow，``requirements``/test-requirements 无需新增依赖。
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: SRT 落盘编码：utf-8-sig（部分 Windows 播放器/ffmpeg 按 ANSI 误读无 BOM 的中文）
SRT_ENCODING = "utf-8-sig"

MAX_CHARS_PER_LINE = 30
MAX_DISPLAY_TIME_SEC = 8.0

#: 对齐候选（C 包）的输入 schema；未知版本拒收，不猜字段。
#: - ``subtitle_validated_timings.v1``：C 的 ``tools/align_subtitles.py`` 实际写出的形状
#:   （**单镜**、``time_base="shot_local_seconds"``、``timings[]`` + ``source_audio.sha256``）；
#: - ``subtitle_timing.v1``：多镜聚合形状（``shots[]``，每镜各自的 segments）。
#: 两者都按**镜内局部时间**解读：由本适配器平移到绝对时间再裁到实际镜头窗。
ALIGNMENT_SCHEMA = "subtitle_timing.v1"
ALIGNMENT_SCHEMA_SHOT_LOCAL = "subtitle_validated_timings.v1"
ALIGNMENT_TIME_BASE = "shot_local_seconds"


class SubtitleContractError(ValueError):
    """字幕契约违例（拒收）。``code`` 供报告/退出码映射，message 直接给人看。"""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(message)
        self.code = code
        self.fields = fields

    def payload(self) -> dict:
        return {"code": self.code, "message": str(self), **self.fields}


# ------------------------------------------------------------------ cue 与纯函数

@dataclass(frozen=True)
class SubtitleCue:
    """一条字幕 cue：**唯一**的时窗与文本事实源。"""

    cue_id: str
    text: str
    start_sec: float
    end_sec: float
    shot_index: int | None = None
    source: str = "legacy"          # "legacy" | "alignment"
    part_index: int = 1
    char_start: int | None = None    # alignment 来源时，相对镜头正文的 code point 区间
    char_end: int | None = None

    @property
    def duration_sec(self) -> float:
        return float(self.end_sec) - float(self.start_sec)


@dataclass(frozen=True)
class SubtitlePlan:
    """一次成片字幕的完整事实：cue 列表 + 来源 + 逐镜降级记录。"""

    cues: tuple[SubtitleCue, ...]
    source: str = "legacy"                  # "legacy" | "alignment"
    fallbacks: tuple[dict, ...] = ()

    @property
    def text(self) -> str:
        return render_srt(self.cues)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format HH:MM:SS,mmm."""
    total_ms = int(round(float(seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_subtitle_by_punctuation(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Split long subtitle into shorter parts by punctuation marks.

    旧实现原样迁入（step04_merge.srt_gen -> 这里），语义不变：
    先在标点处切（每段至少 5 字），超长段再按字符数硬切。
    """
    if not text or len(text) <= max_chars:
        return [text] if text else []

    punctuation = ['。', '；', '！', '？', '，', '.', ';', '!', '?', '、', ',']
    parts: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in punctuation and len(current) > 5:
            parts.append(current.strip())
            current = ""
    if current.strip():
        parts.append(current.strip())

    result: list[str] = []
    for part in parts:
        if len(part) > max_chars:
            for i in range(0, len(part), max_chars):
                chunk = part[i:i + max_chars]
                if chunk:
                    result.append(chunk)
        else:
            result.append(part)
    return result if result else [text[:max_chars]]


def _cue_id(shot_index, part_index: int, source: str) -> str:
    prefix = "a" if source == "alignment" else "s"
    return f"{prefix}{shot_index}:{int(part_index)}"


def cue_text(shot) -> str:
    """镜头字幕正文（旧口径：``shot["subtitle"]``，strip 后）。"""
    return str((shot or {}).get("subtitle", "") or "").strip()


def _is_dialogue_cue(text: str) -> bool:
    from shared.dialogue import has_dialogue  # 判定单源（08-01 code-audit #2）
    return bool(has_dialogue(text))


def _resolve_window(index: int, dur, clip_windows, current_time: float):
    if clip_windows and index - 1 < len(clip_windows):
        start, end = clip_windows[index - 1]
        return float(start), float(end), max(0.0, float(end) - float(start))
    start = float(current_time)
    return start, start + float(dur), float(dur)


def _legacy_cues_for_shot(index: int, shot, dur: float, clip_windows, speech_durations,
                          current_time: float, warn=None):
    """单个镜头的旧口径 cue（原 ``generate_srt`` 的循环体，逐字保语义）。

    Returns: ``(cues, current_time_after)``
    """
    start, end, dur = _resolve_window(index, dur, clip_windows, current_time)
    subtitle = cue_text(shot)
    if not subtitle or not _is_dialogue_cue(subtitle):
        return [], end

    subtitle_duration = dur
    if speech_durations and index - 1 < len(speech_durations):
        _sp = speech_durations[index - 1]
        if _sp and float(_sp) > 0:
            subtitle_duration = max(0.1, min(dur, float(_sp)))

    shot_index = shot.get("index", index)
    parts = split_subtitle_by_punctuation(subtitle, MAX_CHARS_PER_LINE)
    cues: list[SubtitleCue] = []
    if len(parts) == 1:
        if subtitle_duration > MAX_DISPLAY_TIME_SEC and warn is not None:
            warn(f"  [WARN] Shot {index} subtitle duration {subtitle_duration:.1f}s "
                 f"> {MAX_DISPLAY_TIME_SEC}s (consider splitting in script)")
        cues.append(SubtitleCue(
            cue_id=_cue_id(shot_index, 1, "legacy"), text=subtitle,
            start_sec=start, end_sec=start + subtitle_duration,
            shot_index=shot_index, source="legacy", part_index=1))
        return cues, end

    total_chars = sum(len(p) for p in parts)
    temp_start = start
    part_end = start
    for j, part_text in enumerate(parts, 1):
        ratio = (len(part_text) / total_chars) if total_chars > 0 else 1.0 / len(parts)
        part_duration = subtitle_duration * ratio
        part_start = temp_start
        part_end = temp_start + part_duration
        cues.append(SubtitleCue(
            cue_id=_cue_id(shot_index, j, "legacy"), text=part_text,
            start_sec=part_start, end_sec=part_end,
            shot_index=shot_index, source="legacy", part_index=j))
        temp_start = part_end
    # v2026-10 复核：时间轴推进必须用本镜**真实结束**（`end`），不能用最后一段字幕的
    # 结束（`part_end`）。`part_end` 只累加 `subtitle_duration = min(dur, speech_duration)`，
    # 一旦说话短于镜头（`speech_duration < dur`），后续所有镜头的 cue 起点会被整体前移，
    # 形成累积漂移。同函数的单段分支（`:189`）与无字幕分支（`:170`）本来就返回 `end`——
    # 只有多段分支偏离，属实现不一致而非设计差异（旧 `srt_gen.py:161` 的
    # `end = part_end; current_time = end` 在重构时被原样搬到这里）。
    # 注意：**cue 自身的时窗仍按 `part_end`**（上一条 append 不变），只改时间轴推进。
    return cues, end


def cues_from_shots(shots, durations, clip_windows=None, speech_durations=None,
                    warn=None) -> tuple[SubtitleCue, ...]:
    """旧字幕解析 → cue 列表（**唯一**适配器；两侧都不得再自行切句）。"""
    cues: list[SubtitleCue] = []
    current_time = 0.0
    for index, (shot, dur) in enumerate(zip(shots, durations), 1):
        shot_cues, current_time = _legacy_cues_for_shot(
            index, shot, dur, clip_windows, speech_durations, current_time, warn)
        cues.extend(shot_cues)
    return tuple(cues)


# ------------------------------------------------------------------ 对齐候选适配

def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
        and value not in (float("inf"), float("-inf"))


def cues_from_alignment(validated_timings, *, shots, durations, clip_windows=None,
                        speech_durations=None, script_revision=None,
                        shot_audio_sha256=None, warn=None):
    """合法对齐结果 → cue 列表（逐镜）；不足的镜头**整镜**回退旧口径。

    Returns: ``(cues, report)``；report 记录每镜采纳/回退及原因（不静默跳过）。
    整请求级不符（schema/revison/文本/音频身份）直接拒收，不降级掩盖错绑定。
    """
    data = validated_timings or {}
    schema = data.get("schema_version")
    if schema == ALIGNMENT_SCHEMA_SHOT_LOCAL:
        # C 的真实产物形状：单镜、镜内局部秒、timings[]（含 source_audio.sha256）
        base = data.get("time_base")
        if base not in (None, ALIGNMENT_TIME_BASE):
            raise SubtitleContractError(
                "unsupported_alignment_time_base",
                f"对齐候选的 time_base 不是镜内局部时间: {base!r}", time_base=base)
        source_audio = data.get("source_audio") or {}
        data = {
            "schema_version": ALIGNMENT_SCHEMA,
            "script_revision": data.get("script_revision"),
            "shots": [{"shot_index": data.get("shot_index"),
                       "segments": list(data.get("timings") or []),
                       "audio_sha256": source_audio.get("sha256"),
                       "unit_id": data.get("unit_id")}],
        }
    elif schema != ALIGNMENT_SCHEMA:
        raise SubtitleContractError(
            "unsupported_alignment_schema",
            f"对齐候选 schema 不认识: {schema!r}"
            f"（期望 {ALIGNMENT_SCHEMA} 或 {ALIGNMENT_SCHEMA_SHOT_LOCAL}）",
            schema_version=schema)
    revision = str(data.get("script_revision") or "")
    if not revision or (script_revision is not None and revision != str(script_revision)):
        raise SubtitleContractError(
            "alignment_revision_mismatch",
            f"对齐候选的 script_revision 与本次成片不符: {revision!r} != {script_revision!r}",
            revision=revision, expected=script_revision)

    by_index = {}
    for entry in (data.get("shots") or []):
        if isinstance(entry, dict) and entry.get("shot_index") is not None:
            by_index[str(entry["shot_index"])] = entry

    cues: list[SubtitleCue] = []
    report: list[dict] = []
    current_time = 0.0
    for index, (shot, dur) in enumerate(zip(shots, durations), 1):
        shot_index = shot.get("index", index)
        text = cue_text(shot)
        entry = by_index.get(str(shot_index))
        start, end, _dur = _resolve_window(index, dur, clip_windows, current_time)
        if entry is None or not text or not _is_dialogue_cue(text):
            fallback = "no_alignment" if entry is None else "no_dialogue_subtitle"
            shot_cues, current_time = _legacy_cues_for_shot(
                index, shot, dur, clip_windows, speech_durations, current_time, warn)
            cues.extend(shot_cues)
            report.append({"shot_index": shot_index, "status": "fallback", "reason": fallback})
            continue
        # 整请求级身份：文本摘要不符即拒收（不按镜降级掩盖错绑定）
        expected_text_sha = entry.get("text_sha256")
        if expected_text_sha and expected_text_sha != hashlib.sha256(text.encode("utf-8")).hexdigest():
            raise SubtitleContractError(
                "alignment_text_mismatch",
                f"对齐候选的文本摘要与镜头 {shot_index} 正文不符", shot_index=shot_index)
        expected_audio_sha = entry.get("audio_sha256")
        if expected_audio_sha and shot_audio_sha256:
            actual_audio_sha = shot_audio_sha256.get(str(shot_index))
            if actual_audio_sha and actual_audio_sha != expected_audio_sha:
                raise SubtitleContractError(
                    "alignment_audio_mismatch",
                    f"对齐候选的音频摘要与镜头 {shot_index} 的实际音轨不符",
                    shot_index=shot_index)

        segments = entry.get("segments")
        segments = list(segments) if isinstance(segments, (list, tuple)) else []
        shot_cues, reason = _alignment_cues_for_shot(
            shot_index, text, segments, start, end, source_revision=revision)
        if reason:
            shot_cues, current_time = _legacy_cues_for_shot(
                index, shot, dur, clip_windows, speech_durations, current_time, warn)
            cues.extend(shot_cues)
            report.append({"shot_index": shot_index, "status": "fallback", "reason": reason})
            continue
        cues.extend(shot_cues)
        current_time = end
        report.append({"shot_index": shot_index, "status": "alignment",
                       "cues": len(shot_cues)})
    source = "alignment"
    if any(r["status"] == "fallback" for r in report):
        source = "alignment+fallback"
    return tuple(cues), {"schema_version": ALIGNMENT_SCHEMA, "script_revision": revision,
                         "source": source, "shots": report,
                         "fallback_count": sum(1 for r in report if r["status"] == "fallback")}


def _alignment_cues_for_shot(shot_index, text: str, segments, window_start: float,
                             window_end: float, *, source_revision: str):
    """把一镜的合法对齐段转成 cue。返回 ``(cues, fallback_reason)``。

    要求（§C2）：段覆盖正文全部非空白字符、单调不重叠、时间有限且在镜头窗内；
    任一条不满足 ⇒ 返回原因，由调用方整镜回退（不做半句新半句旧的假成功）。
    """
    if not segments:
        return [], "no_segments"
    for seg in segments:
        if not isinstance(seg, dict):
            return [], "invalid_segment"
        if not all(_finite(seg.get(key)) for key in ("char_start", "char_end",
                                                     "start_sec", "end_sec")):
            return [], "invalid_segment"
    # 平移用 C 的**同一个** helper（`shift_timings`）：本包不重写第二套换算
    try:
        from shared.subtitle_alignment import shift_timings
        shifted = shift_timings(list(segments), float(window_start))
    except Exception:                     # C 的模块不可用时退回等价加法（同一语义）
        shifted = [{**seg, "start_sec": float(seg["start_sec"]) + float(window_start),
                    "end_sec": float(seg["end_sec"]) + float(window_start)}
                   for seg in segments]
    ordered = []
    for seg in shifted:
        cs, ce = int(seg["char_start"]), int(seg["char_end"])
        if not (0 <= cs < ce <= len(text)):
            return [], "char_range_out_of_text"
        abs_start = float(seg["start_sec"])
        abs_end = float(seg["end_sec"])
        if not (abs_start < abs_end):
            return [], "invalid_segment_time"
        eps = 1.0 / 1000.0
        if abs_start < window_start - eps or abs_end > window_end + eps:
            return [], "segment_out_of_window"
        ordered.append({"char_start": cs, "char_end": ce,
                        "start_sec": max(abs_start, window_start),
                        "end_sec": min(abs_end, window_end),
                        "segment_id": seg.get("segment_id")})
    ordered.sort(key=lambda s: s["char_start"])
    covered = 0
    prev_end = 0
    cues: list[SubtitleCue] = []
    for j, seg in enumerate(ordered, 1):
        if seg["char_start"] < prev_end:
            return [], "overlapping_segments"
        for pos in range(prev_end, seg["char_start"]):
            if not text[pos].isspace():
                return [], "incomplete_coverage"
        covered = seg["char_end"]
        prev_end = seg["char_end"]
        cue_body = text[seg["char_start"]:seg["char_end"]].strip()
        if not cue_body:
            continue
        cues.append(SubtitleCue(
            cue_id=_cue_id(shot_index, j, "alignment"), text=cue_body,
            start_sec=seg["start_sec"], end_sec=seg["end_sec"],
            shot_index=shot_index, source="alignment", part_index=j,
            char_start=seg["char_start"], char_end=seg["char_end"]))
    for pos in range(covered, len(text)):
        if not text[pos].isspace():
            return [], "incomplete_coverage"
    if not cues:
        return [], "no_speakable_segments"
    return cues, None


def build_subtitle_plan(shots, durations, clip_windows=None, speech_durations=None,
                        validated_timings=None, *, script_revision=None,
                        shot_audio_sha256=None, warn=None) -> SubtitlePlan:
    """唯一入口：旧口径或（逐镜降级的）对齐结果 → :class:`SubtitlePlan`。"""
    if validated_timings is None:
        cues = cues_from_shots(shots, durations, clip_windows, speech_durations, warn)
        plan = SubtitlePlan(cues=cues, source="legacy")
    else:
        cues, report = cues_from_alignment(
            validated_timings, shots=shots, durations=durations, clip_windows=clip_windows,
            speech_durations=speech_durations, script_revision=script_revision,
            shot_audio_sha256=shot_audio_sha256, warn=warn)
        plan = SubtitlePlan(cues=cues, source=report["source"],
                            fallbacks=tuple(report["shots"]))
    validate_cues(plan.cues)
    return plan


def validate_cues(cues, *, clip_windows=None) -> None:
    """cue 列表自身的判据（有限、非负、时窗为正、ID 唯一、起点不倒序）.

    注意**不**要求 cue 两两不重叠：xfade 边界下相邻镜头的窗口本身重叠，旧口径的字幕
    也随窗口延伸（这是既有产品语义，不在这里收紧）。镜内对齐段的单调/不重叠判据在
    对齐适配器里（:func:`_alignment_cues_for_shot`）。
    """
    seen: set[str] = set()
    prev_start = 0.0
    for cue in cues:
        if not cue.cue_id or cue.cue_id in seen:
            raise SubtitleContractError("duplicate_cue_id", f"cue id 重复或为空: {cue.cue_id!r}")
        seen.add(cue.cue_id)
        if not cue.text.strip():
            raise SubtitleContractError("empty_cue_text", f"cue {cue.cue_id} 文本为空")
        if not (_finite(cue.start_sec) and _finite(cue.end_sec)):
            raise SubtitleContractError("invalid_cue_window",
                                        f"cue {cue.cue_id} 时间非有限值")
        if cue.start_sec < 0 or cue.end_sec <= cue.start_sec:
            raise SubtitleContractError(
                "invalid_cue_window",
                f"cue {cue.cue_id} 时窗非法: {cue.start_sec}..{cue.end_sec}")
        if cue.start_sec + 1e-9 < prev_start:
            raise SubtitleContractError(
                "cue_order",
                f"cue {cue.cue_id} 起点倒序（{cue.start_sec:.3f} < {prev_start:.3f}）")
        prev_start = cue.start_sec


def render_srt(cues) -> str:
    """cue 列表 → SRT 文本（**唯一**渲染实现，导出与滤镜共用）。"""
    lines: list[str] = []
    for number, cue in enumerate(cues, 1):
        lines.append(str(number))
        lines.append(f"{seconds_to_srt_time(cue.start_sec)} --> {seconds_to_srt_time(cue.end_sec)}")
        lines.append(cue.text)
        lines.append("")
    return "\n".join(lines)


def write_srt(plan: SubtitlePlan, path: Path) -> Path:
    """把计划的 SRT 文本落盘（utf-8-sig；与 :func:`render_srt` 逐字节一致）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_srt(plan.cues), encoding=SRT_ENCODING)
    return target


def read_srt_text(path: Path) -> str:
    return Path(path).read_text(encoding=SRT_ENCODING)


# ------------------------------------------------------------------ 字体与字形覆盖

@dataclass(frozen=True)
class FontSpec:
    path: Path
    sha256: str
    family: str


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blob in iter(lambda: f.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


def _sfnt_offset(data: bytes) -> int:
    if data[:4] == b"ttcf":                      # TrueType Collection：取第一个字体
        if len(data) < 16:
            raise SubtitleContractError("font_unreadable", "字体文件头不完整（ttcf）")
        return struct.unpack(">I", data[12:16])[0]
    return 0


def _tables(data: bytes) -> dict:
    base = _sfnt_offset(data)
    if len(data) < base + 12:
        raise SubtitleContractError("font_unreadable", "字体文件头不完整")
    num_tables = struct.unpack(">H", data[base + 4:base + 6])[0]
    tables = {}
    off = base + 12
    for _ in range(num_tables):
        if len(data) < off + 16:
            raise SubtitleContractError("font_unreadable", "字体表目录被截断")
        tag = data[off:off + 4]
        offset, length = struct.unpack(">II", data[off + 8:off + 16])
        tables[tag] = (offset, length)
        off += 16
    return tables


def _parse_cmap(data: bytes, offset: int, length: int) -> set[int]:
    table = data[offset:offset + length]
    if len(table) < 4:
        raise SubtitleContractError("font_unreadable", "cmap 表不完整")
    count = struct.unpack(">H", table[2:4])[0]
    subtables = []
    for i in range(count):
        rec = 4 + i * 8
        if len(table) < rec + 8:
            break
        platform, encoding, sub_off = struct.unpack(">HHI", table[rec:rec + 8])
        subtables.append((platform, encoding, sub_off))
    # 优先级：Windows UCS-4 (3,10) > Windows BMP (3,1) > Unicode (0,*) > Mac (1,0)
    priority = {(3, 10): 0, (3, 1): 1, (1, 0): 3}
    ordered = sorted(subtables, key=lambda s: priority.get((s[0], s[1]), 2 if s[0] == 0 else 4))
    for _p, _e, sub_off in ordered:
        if sub_off + 2 > len(table):
            continue
        fmt = struct.unpack(">H", table[sub_off:sub_off + 2])[0]
        try:
            if fmt == 4:
                return _cmap_format4(table, sub_off)
            if fmt == 12:
                return _cmap_format12(table, sub_off)
            if fmt == 6:
                return _cmap_format6(table, sub_off)
        except (struct.error, IndexError):
            continue
    raise SubtitleContractError("font_unreadable", "找不到可解析的 cmap 子表（格式 4/12）")


def _cmap_format4(table: bytes, off: int) -> set[int]:
    seg_x2 = struct.unpack(">H", table[off + 6:off + 8])[0]
    seg_count = seg_x2 // 2
    end_off = off + 14
    ends = struct.unpack(f">{seg_count}H", table[end_off:end_off + seg_x2])
    start_off = end_off + seg_x2 + 2
    starts = struct.unpack(f">{seg_count}H", table[start_off:start_off + seg_x2])
    delta_off = start_off + seg_x2
    deltas = struct.unpack(f">{seg_count}h", table[delta_off:delta_off + seg_x2])
    range_off = delta_off + seg_x2
    range_offsets = struct.unpack(f">{seg_count}H", table[range_off:range_off + seg_x2])
    glyphs: set[int] = set()
    for i in range(seg_count):
        start, end = starts[i], ends[i]
        if start == 0xFFFF:
            continue
        for code in range(start, end + 1):
            if range_offsets[i] == 0:
                gid = (code + deltas[i]) & 0xFFFF
            else:
                addr = range_off + i * 2 + range_offsets[i] + (code - start) * 2
                if addr + 2 > len(table):
                    continue
                gid = struct.unpack(">H", table[addr:addr + 2])[0]
                if gid:
                    gid = (gid + deltas[i]) & 0xFFFF
            if gid:
                glyphs.add(code)
    return glyphs


def _cmap_format12(table: bytes, off: int) -> set[int]:
    n_groups = struct.unpack(">I", table[off + 12:off + 16])[0]
    glyphs: set[int] = set()
    pos = off + 16
    for _ in range(n_groups):
        if pos + 12 > len(table):
            break
        start, end, _gid = struct.unpack(">III", table[pos:pos + 12])
        for code in range(start, min(end, start + 0x10FFFF) + 1):
            glyphs.add(code)
        pos += 12
    return glyphs


def _cmap_format6(table: bytes, off: int) -> set[int]:
    first, count = struct.unpack(">HH", table[off + 6:off + 10])
    glyphs: set[int] = set()
    pos = off + 10
    for i in range(count):
        if pos + 2 > len(table):
            break
        gid = struct.unpack(">H", table[pos:pos + 2])[0]
        if gid:
            glyphs.add(first + i)
        pos += 2
    return glyphs


def _parse_family(data: bytes, tables: dict) -> str:
    try:
        offset, length = tables[b"name"]
    except KeyError:
        return ""
    table = data[offset:offset + length]
    if len(table) < 6:
        return ""
    count, string_off = struct.unpack(">HH", table[2:6])
    best = ""
    for i in range(count):
        rec = 6 + i * 12
        if len(table) < rec + 12:
            break
        platform, _enc, _lang, name_id, length_, off_ = struct.unpack(">HHHHHH", table[rec:rec + 12])
        if name_id != 1:
            continue
        raw = table[string_off + off_:string_off + off_ + length_]
        try:
            text = raw.decode("utf-16-be") if platform in (0, 3) else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        text = text.strip()
        if not text:
            continue
        if platform == 3:      # 优先 Windows 英文名
            return text
        best = best or text
    return best


@lru_cache(maxsize=8)
def _font_index(path_str: str, size: int, mtime_ns: int):
    data = Path(path_str).read_bytes()
    try:
        tables = _tables(data)
        if b"cmap" not in tables:
            raise SubtitleContractError("font_unreadable", "字体没有 cmap 表")
        offset, length = tables[b"cmap"]
        glyphs = frozenset(_parse_cmap(data, offset, length))
        family = _parse_family(data, tables)
    except SubtitleContractError:
        raise
    except Exception as exc:  # 解析失败即"不可用"，不猜
        raise SubtitleContractError("font_unreadable", f"字体解析失败: {exc}") from exc
    if not glyphs:
        raise SubtitleContractError("font_unreadable", "字体 cmap 为空")
    return family, glyphs


def font_glyphs(font: FontSpec) -> frozenset:
    stat = Path(font.path).stat()
    return _font_index(str(Path(font.path).resolve()), stat.st_size, stat.st_mtime_ns)[1]


def missing_glyphs(font: FontSpec, text: str) -> list[str]:
    """正文里字体覆盖不到的字符（去重，保持出现顺序）。"""
    glyphs = font_glyphs(font)
    missing: list[str] = []
    for ch in text:
        if ch.isspace() or not ch.isprintable():
            continue
        if 0xFE00 <= ord(ch) <= 0xFE0F:      # 变体选择符不要求 cmap 映射
            continue
        if ord(ch) not in glyphs and ch not in missing:
            missing.append(ch)
    return missing


def require_glyph_coverage(font: FontSpec, texts, *, limit: int = 20) -> None:
    """编码前的字形覆盖检查：缺字即拒收（不静默 fallback 到系统字体）。"""
    missing: list[str] = []
    for text in texts:
        for ch in missing_glyphs(font, str(text)):
            if ch not in missing:
                missing.append(ch)
    if missing:
        raise SubtitleContractError(
            "missing_glyphs",
            f"字体 {Path(font.path).name} 覆盖不到 {len(missing)} 个字形: "
            f"{''.join(missing[:limit])}{'…' if len(missing) > limit else ''}",
            glyphs=missing[:limit], font=str(font.path))


def load_font_spec(path, expected_sha256: str | None = None) -> FontSpec:
    """显式字体路径 → :class:`FontSpec`（必须存在；给了摘要必须相符）。"""
    p = Path(path)
    if not p.is_file():
        raise SubtitleContractError("font_missing", f"字幕字体文件不存在: {p}", path=str(p))
    digest = _sha256_file(p)
    if expected_sha256 and digest != str(expected_sha256):
        raise SubtitleContractError(
            "font_digest_mismatch",
            f"字幕字体摘要不符: {digest[:12]} != {str(expected_sha256)[:12]}", path=str(p))
    family, _glyphs = _font_index(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns)
    if not family:
        raise SubtitleContractError("font_unreadable", f"字体缺少 family 名: {p}", path=str(p))
    return FontSpec(path=p.resolve(), sha256=digest, family=family)


@dataclass(frozen=True)
class SubtitleStyle:
    """SRT 导出之外的**绘制**参数（不参与 cue 时窗）。"""

    font: FontSpec | None = None
    family_fallback: str = "Microsoft YaHei"
    font_size: int = 22
    primary_colour: str = "&H00FFFFFF"
    outline_colour: str = "&H00000000"
    outline: int = 2
    shadow: int = 1

    def force_style(self) -> str:
        family = self.family if self.family else self.family_fallback
        parts = [f"FontName={family}", f"FontSize={int(self.font_size)}",
                 f"PrimaryColour={self.primary_colour}",
                 f"OutlineColour={self.outline_colour}",
                 f"Outline={int(self.outline)}", f"Shadow={int(self.shadow)}"]
        return "\\,".join(parts)

    @property
    def family(self) -> str:
        return self.font.family if self.font is not None else ""


def ffmpeg_subtitle_filter(srt_path, plan: SubtitlePlan, style: SubtitleStyle) -> str:
    """字幕滤镜：消费**同一份** cue 列表（盘上文件必须与列表渲染逐字一致）。

    不一致即拒收 ``srt_cues_mismatch``——禁止"一侧重新切句/重算时窗"。
    """
    path = Path(srt_path)
    if not path.is_file():
        raise SubtitleContractError("srt_missing", f"字幕文件不存在: {path}", path=str(path))
    actual = read_srt_text(path)
    expected = render_srt(plan.cues)
    if actual != expected:
        raise SubtitleContractError(
            "srt_cues_mismatch",
            "盘上的 SRT 与本次 cue 列表不一致（两侧重新切句/算时窗？）", path=str(path))
    if style.font is not None:
        require_glyph_coverage(style.font, [c.text for c in plan.cues])
    escaped = path.resolve().as_posix().replace(":", "\\:")
    options = [f"force_style='{style.force_style()}'"]
    if style.font is not None:
        fontsdir = Path(style.font.path).parent.as_posix().replace(":", "\\:")
        options.insert(0, f"fontsdir='{fontsdir}'")
    return f"subtitles='{escaped}':" + ":".join(options)


__all__ = [
    "ALIGNMENT_SCHEMA", "FontSpec", "SRT_ENCODING", "SubtitleContractError", "SubtitleCue",
    "SubtitlePlan", "SubtitleStyle", "build_subtitle_plan", "cue_text", "cues_from_alignment",
    "cues_from_shots", "ffmpeg_subtitle_filter", "font_glyphs", "load_font_spec",
    "missing_glyphs", "read_srt_text", "render_srt", "require_glyph_coverage",
    "seconds_to_srt_time", "split_subtitle_by_punctuation", "validate_cues", "write_srt",
]
