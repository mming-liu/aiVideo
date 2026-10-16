# -*- coding: utf-8 -*-
"""shared.subtitle_alignment - C 包：已知文本的字幕句界候选（计划 §C1/§C2/§15）.

**已知文本对齐**不是 ASR：文本由生产者给定，这里只把"哪个字符在什么时候发声"变成
可校验的时间候选；不从对齐结果反推"声音说对了文本"。

## 坐标（两套，不许混用）

- **spoke 坐标**：``spoken_text`` 的 Unicode 码点，0 起、半开区间 ``[start, end)``；
  ``dialogue_speech_text(shot, plan, characters)`` 的 ``text`` 分支就是它（§15）。
- **subtitle 坐标**：``subtitle_text``（要显示的正文，含标点）的码点。

``mapping`` 是两套坐标之间的**显式单调映射**：逐字相同用 identity mapping；前缀/切片
不同**必须**给出显式映射，缺失即拒收。对齐产出的 unit 一律用 **spoke 坐标**。

## 富身份（§15「对齐输入与富身份入口」）

进入对齐前先核：候选 manifest（``--audio-candidates``）+ 音轨旁的
``<audio>.identity.json``（``audio_identity.v2``，由 T 包生成）。
``voice_signature`` 只是**附带核对**，**不能**用它反推富身份：富身份不完整一律
``identity_incomplete`` 拒收，不降级、不猜。

## 可采纳性（§C1/§C2）

只有"**本 segment 内所有非空白发声字符都有映射**"且时间证据存在的 segment 可采纳；
其余 segment 整体回退既有字幕策略（报告 fallback 与原因），**不拼半新半旧**。
标点照常显示，时间取同 segment 已对齐正文的首尾包络——**不创造独立发声时间**。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from shared.enhancement_io import (
    EXIT_INVALID_INPUT,
    EXIT_REJECTED,
    EnhancementError,
    canonical_json_digest,
    sha256_file,
    sha256_text,
)

REQUEST_SCHEMA = "subtitle_alignment_request.v1"
ALIGNMENT_SCHEMA = "subtitle_alignment.v1"
AUDIO_IDENTITY_SCHEMA = "audio_identity.v2"
AUDIO_IDENTITY_LEGACY_SCHEMA = "audio_identity.v1"

REQUIRED_REQUEST_FIELDS = (
    "script_revision", "shot_index", "audio_path", "audio_sha256", "unit_id",
    "audio_manifest_path", "audio_manifest_hash", "spoken_text", "subtitle_text",
    "mapping", "voice_signature", "segments",
)

COORD_SPOKEN = "spoken_text_unicode_codepoints"
COORD_SUBTITLE = "subtitle_text_unicode_codepoints"

#: 时间边界容差（一帧 24fps ≈ 0.0417s；包络取整不因此判越界）
TIME_TOLERANCE_SEC = 0.05


# ---------------------------------------------------------------- 小工具

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def text_digest(text: str) -> str:
    return sha256_text(text if isinstance(text, str) else "")


def text_digest_stripped(text: str) -> str:
    return sha256_text(str(text or "").strip())


def digests_match(declared, text: str) -> bool:
    """声明摘要与文本是否相符（原文与 strip 后两种写法都认——**只此两种**）."""
    if not declared:
        return False
    return str(declared) in (text_digest(text), text_digest_stripped(text))


def request_sha256(request: dict) -> str:
    """请求摘要（规范 JSON：key 排序 + 紧凑分隔 + UTF-8）——生产者用同一算法."""
    return canonical_json_digest(request)


def _problem(code: str, field_path: str, message: str, **extra) -> dict:
    return {"code": code, "field_path": field_path, "message": message, **extra}


# ---------------------------------------------------------------- 请求：结构校验

def validate_request_structure(request) -> list:
    problems: list[dict] = []

    def add(code, field_path, message, **extra):
        problems.append(_problem(code, field_path, message, **extra))

    if not isinstance(request, dict):
        return [{"code": "request_not_object", "field_path": "", "message": "请求必须是 JSON 对象"}]
    if request.get("schema_version") != REQUEST_SCHEMA:
        add("request_schema", "schema_version",
            f"请求 schema 不符: {request.get('schema_version')!r} != {REQUEST_SCHEMA!r}")
    for key in REQUIRED_REQUEST_FIELDS:
        if key not in request:
            add("request_missing_field", key, f"请求缺少必需字段 {key}")
    spoken = request.get("spoken_text")
    subtitle = request.get("subtitle_text")
    if not isinstance(spoken, str) or not spoken.strip():
        add("no_speech_text", "spoken_text",
            "发声正文为空/纯空白：没有可对齐的字符（不猜、不伪造时间）")
    if not isinstance(subtitle, str):
        add("request_field_type", "subtitle_text", "subtitle_text 必须是字符串")

    # segments
    segments = request.get("segments")
    if not isinstance(segments, list) or not segments:
        add("segments_missing", "segments", "segments 必须是非空数组（每个 segment 是显示句界）")
    else:
        last_end = None
        for i, seg in enumerate(segments):
            fp = f"segments[{i}]"
            if not isinstance(seg, dict):
                add("segment_shape", fp, "segment 必须是对象")
                continue
            if not seg.get("segment_id"):
                add("segment_id_missing", f"{fp}.segment_id", "segment 缺少 segment_id")
            start, end = seg.get("char_start"), seg.get("char_end")
            if not isinstance(start, int) or not isinstance(end, int) or isinstance(start, bool):
                add("segment_index_type", fp, f"char 索引必须是整数：{start!r}/{end!r}")
                continue
            if start < 0 or end <= start:
                add("segment_index_range", fp, f"segment 区间非法（半开，需 0 ≤ start < end）：{start}..{end}")
            if isinstance(subtitle, str) and end > len(subtitle):
                add("segment_index_oob", fp,
                    f"segment 区间超出 subtitle_text（len={len(subtitle)}）：{start}..{end}")
            if last_end is not None and start < last_end:
                add("segment_overlap", fp, f"segment 与上一段重叠/倒序：{start} < {last_end}")
            last_end = max(last_end or 0, end)

    problems.extend(_validate_mapping(request))
    return problems


def _validate_mapping(request: dict) -> list:
    problems: list[dict] = []
    spoken, subtitle = request.get("spoken_text"), request.get("subtitle_text")
    mapping = request.get("mapping")
    identity = isinstance(spoken, str) and isinstance(subtitle, str) and spoken == subtitle

    def add(code, field_path, message, **extra):
        problems.append(_problem(code, field_path, message, **extra))

    if identity and mapping in (None, []):
        return problems  # 逐字相同：identity mapping（显式省略等价）
    if not isinstance(mapping, list) or not mapping:
        add("mapping_missing", "mapping",
            "spoken_text 与 subtitle_text 不同（前缀/切片/标点差异）时必须给显式单调映射"
            if not identity else "mapping 必须是数组（逐字相同可省略或给 identity）")
        return problems
    prev_spoken_end, prev_subtitle_end = None, None
    for i, block in enumerate(mapping):
        fp = f"mapping[{i}]"
        if not isinstance(block, dict):
            add("mapping_shape", fp, "mapping 块必须是对象")
            continue
        vals = {}
        for key in ("spoken_start", "spoken_end", "subtitle_start", "subtitle_end"):
            value = block.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                add("mapping_index_type", f"{fp}.{key}", f"{key} 必须是整数索引：{value!r}")
                vals = None
                break
            vals[key] = value
        if vals is None:
            continue
        if vals["spoken_end"] <= vals["spoken_start"] or vals["subtitle_end"] <= vals["subtitle_start"]:
            add("mapping_block_order", fp,
                f"mapping 块必须是非空半开区间：{vals}")
        if isinstance(spoken, str) and vals["spoken_end"] > len(spoken):
            add("mapping_index_oob", fp, f"映射超出 spoken_text（len={len(spoken)}）")
        if isinstance(subtitle, str) and vals["subtitle_end"] > len(subtitle):
            add("mapping_index_oob", fp, f"映射超出 subtitle_text（len={len(subtitle)}）")
        if prev_spoken_end is not None:
            if vals["spoken_start"] < prev_spoken_end:
                add("mapping_not_monotonic", fp,
                    f"mapping 在 spoken 轴上倒序/重叠：{vals['spoken_start']} < {prev_spoken_end}")
            if vals["subtitle_start"] < prev_subtitle_end:
                add("mapping_not_monotonic", fp,
                    f"mapping 在 subtitle 轴上倒序/重叠：{vals['subtitle_start']} < {prev_subtitle_end}")
        prev_spoken_end, prev_subtitle_end = vals["spoken_end"], vals["subtitle_end"]
    return problems


def load_request(path) -> dict:
    p = Path(path)
    try:
        request = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EnhancementError("request_read_failed", "request", f"读不到请求: {p}（{exc}）",
                               exit_code=EXIT_INVALID_INPUT, field_path="request") from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancementError("request_unparsable", "request", f"请求 JSON 无法解析: {p}（{exc}）",
                               exit_code=EXIT_INVALID_INPUT, field_path="request") from exc
    problems = validate_request_structure(request)
    if problems:
        raise EnhancementError("request_invalid", "request",
                               f"请求结构不合法（{len(problems)} 项）",
                               exit_code=EXIT_REJECTED, field_path="request",
                               residual_issues=problems)
    return request


# ---------------------------------------------------------------- 映射与应用

def mapping_blocks(request: dict) -> list:
    """规范化映射块（逐字相同且未给映射 ⇒ identity 一块）."""
    mapping = request.get("mapping") or []
    spoken, subtitle = request.get("spoken_text") or "", request.get("subtitle_text") or ""
    if not mapping:
        if spoken != subtitle:
            raise EnhancementError("mapping_missing", "mapping",
                                   "文本不同且没有显式映射——拒绝猜对应关系",
                                   exit_code=EXIT_REJECTED, field_path="mapping")
        return [{"spoken_start": 0, "spoken_end": len(spoken),
                 "subtitle_start": 0, "subtitle_end": len(subtitle)}]
    return [{"spoken_start": int(b["spoken_start"]), "spoken_end": int(b["spoken_end"]),
             "subtitle_start": int(b["subtitle_start"]), "subtitle_end": int(b["subtitle_end"])}
            for b in mapping]


def subtitle_range_to_spoken(request: dict, start: int, end: int) -> tuple[list, list]:
    """subtitle 区间 → spoken 区间并集（+ 问题记录）.

    长度保持的映射块允许在段内切开；长度不同的块（前缀/切片偏移）**必须**与
    segment 边界对齐，否则 ``segment_boundary_unmappable``——不按比例硬切。
    """
    ranges: list[list[int]] = []
    problems: list[dict] = []
    for i, block in enumerate(mapping_blocks(request)):
        sub_s, sub_e = block["subtitle_start"], block["subtitle_end"]
        lo, hi = max(sub_s, int(start)), min(sub_e, int(end))
        if hi <= lo:
            continue
        sp_s, sp_e = block["spoken_start"], block["spoken_end"]
        if (lo, hi) == (sub_s, sub_e):
            ranges.append([sp_s, sp_e])
            continue
        if (sub_e - sub_s) == (sp_e - sp_s):
            offset = lo - sub_s
            ranges.append([sp_s + offset, sp_s + (hi - sub_s)])
            continue
        problems.append(_problem(
            "segment_boundary_unmappable", f"mapping[{i}]",
            f"非等长映射块 {sub_s}..{sub_e} → {sp_s}..{sp_e} 被 segment 边界 {start}..{end} 切开，"
            "无法在不猜的前提下换算索引", segment_range=[int(start), int(end)]))
    ranges.sort()
    merged: list[list[int]] = []
    for item in ranges:
        if merged and item[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], item[1])
        else:
            merged.append(list(item))
    return merged, problems


def unit_char_range(unit) -> tuple | None:
    """unit 的 char 区间（畸形/缺字段 ⇒ None：覆盖分析不因此崩，问题由校验层报）."""
    if not isinstance(unit, dict):
        return None
    cs, ce = unit.get("char_start"), unit.get("char_end")
    if not isinstance(cs, int) or not isinstance(ce, int) or isinstance(cs, bool):
        return None
    if cs < 0 or ce <= cs:
        return None
    return cs, ce


def _merge_units(units: list) -> list:
    intervals = sorted([list(r) for r in (unit_char_range(u) for u in units) if r])
    merged: list[list[int]] = []
    for item in intervals:
        if merged and item[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], item[1])
        else:
            merged.append(list(item))
    return merged


def _covered(index: int, intervals: list) -> bool:
    for start, end in intervals:
        if start <= index < end:
            return True
        if start > index:
            break
    return False


# ---------------------------------------------------------------- 候选：校验与覆盖

def validate_alignment(request: dict, alignment, *, audio_duration_sec=None) -> list:
    """候选 ``subtitle_alignment.v1`` 的完整校验（含请求摘要与时间边界）."""
    problems: list[dict] = []

    def add(code, field_path, message, **extra):
        problems.append(_problem(code, field_path, message, **extra))

    if not isinstance(alignment, dict):
        return [_problem("alignment_not_object", "", "候选必须是 JSON 对象")]
    if alignment.get("schema_version") != ALIGNMENT_SCHEMA:
        add("alignment_schema", "schema_version",
            f"候选 schema 不符: {alignment.get('schema_version')!r} != {ALIGNMENT_SCHEMA!r}")
    if alignment.get("request_sha256") != request_sha256(request):
        add("request_sha256_mismatch", "request_sha256",
            "候选回带的请求摘要与本次请求不符——它可能属于另一段音频/另一版正文")
    if not alignment.get("backend"):
        add("backend_missing", "backend", "候选缺少 backend")
    if alignment.get("backend_version") in (None, ""):
        add("backend_version_missing", "backend_version", "候选缺少 backend_version（版本未知要如实写）")
    if "model_revision" not in alignment:
        add("model_revision_missing", "model_revision",
            "候选缺少 model_revision（无模型后端也要显式写 null，不能省略）")

    spoken = request.get("spoken_text") or ""
    units = alignment.get("units")
    if not isinstance(units, list):
        add("units_missing", "units", "候选缺少 units 数组")
        return problems
    if audio_duration_sec is None:
        # 拿不到音轨时长就没法证明"时间在音轨内"——这是**拒绝采纳**的理由，
        # 不是"跳过检查"的理由（报告一次，不按 unit 重复刷屏）。
        add("audio_bounds_unverified", "audio_duration_sec",
            "拿不到音轨时长 ⇒ 无法证明 unit 落在音轨内（拒绝在未验证边界时采纳）")
    prev_end_char, prev_end_time = None, None
    for i, unit in enumerate(units):
        fp = f"units[{i}]"
        if not isinstance(unit, dict):
            add("unit_shape", fp, "unit 必须是对象")
            continue
        cs, ce = unit.get("char_start"), unit.get("char_end")
        if not isinstance(cs, int) or not isinstance(ce, int) or isinstance(cs, bool):
            add("unit_char_type", fp, f"unit char 索引必须是整数：{cs!r}/{ce!r}")
            continue
        if cs < 0 or ce <= cs:
            add("unit_char_range", fp, f"unit 区间非法（半开，0 ≤ start < end）：{cs}..{ce}")
            continue
        if ce > len(spoken):
            add("unit_char_oob", fp, f"unit 区间超出 spoken_text（len={len(spoken)}）：{cs}..{ce}")
        if prev_end_char is not None and cs < prev_end_char:
            add("unit_char_overlap", fp, f"unit 与上一个重叠/倒序：{cs} < {prev_end_char}")
        prev_end_char = max(prev_end_char or 0, ce)
        ts, te = unit.get("start_sec"), unit.get("end_sec")
        if not _is_number(ts) or not _is_number(te):
            add("unit_time_null", fp, f"unit 时间必须是有限数：start={ts!r} end={te!r}")
            continue
        if float(ts) < 0 or float(te) <= float(ts):
            add("unit_time_range", fp, f"unit 时间区间非法（0 ≤ start < end）：{ts}..{te}")
            continue
        if audio_duration_sec is not None and float(te) > float(audio_duration_sec) + TIME_TOLERANCE_SEC:
            add("unit_time_oob", fp,
                f"unit 时间超出音轨（{te} > 时长 {audio_duration_sec}"\
                f" + 容差 {TIME_TOLERANCE_SEC}）")
        if prev_end_time is not None and float(ts) < prev_end_time - TIME_TOLERANCE_SEC:
            add("unit_time_overlap", fp, f"unit 时间与上一个重叠/倒序：{ts} < {prev_end_time}")
        prev_end_time = float(te)
    return problems


def compute_coverage(request: dict, alignment: dict) -> dict:
    """按 segment 计算覆盖：谁能采纳、谁是 fallback、残余未覆盖区间在哪（spoke 坐标）."""
    spoken = request.get("spoken_text") or ""
    units = [u for u in (alignment.get("units") or []) if isinstance(u, dict)]
    covered_intervals = _merge_units(units) if units else []
    timed_units = []
    for u in units:
        cr = unit_char_range(u)
        ts, te = u.get("start_sec"), u.get("end_sec")
        if cr and _is_number(ts) and _is_number(te):
            timed_units.append({"char_start": cr[0], "char_end": cr[1],
                                "start_sec": float(ts), "end_sec": float(te)})
    segments_out: list[dict] = []
    uncovered: list[dict] = []
    for seg in request.get("segments") or []:
        seg_id = seg.get("segment_id")
        if not isinstance(seg.get("char_start"), int) or not isinstance(seg.get("char_end"), int)                 or isinstance(seg.get("char_start"), bool):
            segments_out.append({"segment_id": seg_id, "adoptable": False,
                                 "reason": "segment_index_invalid", "problems": [],
                                 "char_start": seg.get("char_start"),
                                 "char_end": seg.get("char_end"), "spoken_ranges": [],
                                 "start_sec": None, "end_sec": None, "uncovered_chars": 0,
                                 "unit_count": 0})
            continue
        start, end = int(seg["char_start"]), int(seg["char_end"])
        spoken_ranges, problems = subtitle_range_to_spoken(request, start, end)
        if not spoken_ranges or problems:
            # 映射本身有问题（边界落在非等长块里 / 整段没有对应）⇒ 本段不可采纳：
            # "只映射上了一半"不是覆盖完整，宁可交给原字幕策略。
            if not problems:
                problems = [_problem("segment_not_mapped", f"segments[{seg_id}].char_range",
                                     "该 segment 在映射里没有对应发声区间（可能整段是插入内容）",
                                     segment_id=seg_id)]
            segments_out.append({"segment_id": seg_id, "adoptable": False,
                                 "reason": problems[0]["code"], "problems": problems,
                                 "char_start": start, "char_end": end,
                                 "spoken_ranges": spoken_ranges, "start_sec": None,
                                 "end_sec": None, "uncovered_chars": 0, "unit_count": 0})
            continue
        gaps: list[list[int]] = []
        uncovered_count = 0
        for lo, hi in spoken_ranges:
            run_start = None
            for index in range(lo, min(hi, len(spoken))):
                ch = spoken[index]
                blank = ch.isspace()
                if blank or _covered(index, covered_intervals):
                    if run_start is not None:
                        gaps.append([run_start, index])
                        run_start = None
                    continue
                uncovered_count += 1
                if run_start is None:
                    run_start = index
            if run_start is not None:
                gaps.append([run_start, min(hi, len(spoken))])
        for gap in gaps:
            uncovered.append({"segment_id": seg_id, "char_start": gap[0], "char_end": gap[1],
                              "reason": "char_uncovered", "coordinates": COORD_SPOKEN})
        # 时间 = 本 segment 内已对齐 unit 的首尾包络（不创造独立发声时间）
        in_seg = [u for u in timed_units if _unit_in_ranges(u, spoken_ranges)]
        start_sec = min((float(u["start_sec"]) for u in in_seg), default=None)
        end_sec = max((float(u["end_sec"]) for u in in_seg), default=None)
        if gaps and uncovered_count:
            reason = "char_uncovered"
        elif not in_seg:
            reason = "no_aligned_units"
        else:
            reason = None
        segments_out.append({
            "segment_id": seg_id, "adoptable": reason is None, "reason": reason,
            "problems": problems if reason else [],
            "char_start": start, "char_end": end, "spoken_ranges": spoken_ranges,
            "start_sec": start_sec, "end_sec": end_sec,
            "uncovered_chars": uncovered_count,
            "unit_count": len(in_seg),
            "coordinates": COORD_SPOKEN,
        })
    return {
        "units": units,
        "uncovered_ranges": uncovered,
        "segments": segments_out,
        "adoptable_segments": [s for s in segments_out if s["adoptable"]],
        "fallback_segments": [s for s in segments_out if not s["adoptable"]],
        "coordinates": {"units": COORD_SPOKEN, "segments": COORD_SUBTITLE},
    }


def _unit_in_ranges(unit: dict, ranges: list) -> bool:
    cs, ce = unit_char_range(unit) or (None, None)
    if cs is None:
        return False
    for lo, hi in ranges:
        if cs < hi and ce > lo:
            return True
    return False


def analyze(request: dict, alignment: dict, *, audio_duration_sec=None) -> dict:
    """校验 + 覆盖分析（成对使用；候选不合法即拒收，不降级）."""
    problems = validate_alignment(request, alignment, audio_duration_sec=audio_duration_sec)
    coverage = compute_coverage(request, alignment) if not problems else None
    return {"problems": problems, "coverage": coverage}


# ---------------------------------------------------------------- 产出与消费

def build_alignment(request: dict, units: list, *, backend: str, backend_version,
                    model_revision, diagnostics=None) -> dict:
    """构造 ``subtitle_alignment.v1``（uncovered_ranges 由覆盖分析给出，不手工填）.

    输入先过结构判据：畸形 unit（缺字段 / null 时间）在这里就报结构化错误，
    而不是等到下游 `float(None)` 抛裸异常。
    """
    malformed = []
    for i, unit in enumerate(units or []):
        if not isinstance(unit, dict):
            malformed.append(_problem("unit_shape", f"units[{i}]", "unit 必须是对象"))
            continue
        for key in ("char_start", "char_end", "start_sec", "end_sec"):
            value = unit.get(key)
            if key.startswith("char"):
                ok = isinstance(value, int) and not isinstance(value, bool)
            else:
                ok = _is_number(value)
            if not ok:
                malformed.append(_problem("unit_field_invalid", f"units[{i}].{key}",
                                          f"{key} 不是合法值: {value!r}",
                                          coordinates=COORD_SPOKEN))
    if malformed:
        raise EnhancementError("alignment_build_invalid", "alignment",
                               f"待构造的候选不合法（{len(malformed)} 项）",
                               exit_code=EXIT_REJECTED, field_path="units",
                               residual_issues=malformed)
    alignment = {
        "schema_version": ALIGNMENT_SCHEMA,
        "request_sha256": request_sha256(request),
        "backend": backend,
        "backend_version": backend_version,
        "model_revision": model_revision,
        "units": [{"char_start": int(u["char_start"]), "char_end": int(u["char_end"]),
                   "start_sec": float(u["start_sec"]), "end_sec": float(u["end_sec"])}
                  for u in units],
        "uncovered_ranges": [],
        "diagnostics": dict(diagnostics or {}),
        "coordinates": COORD_SPOKEN,
    }
    coverage = compute_coverage(request, alignment)
    alignment["uncovered_ranges"] = coverage["uncovered_ranges"]
    alignment["diagnostics"].update({
        "adoptable_segments": [s["segment_id"] for s in coverage["adoptable_segments"]],
        "fallback_segments": [s["segment_id"] for s in coverage["fallback_segments"]],
        "unit_count": len(alignment["units"]),
        "uncovered_char_count": sum(s["uncovered_chars"] for s in coverage["segments"]),
    })
    return alignment, coverage


def to_validated_timings(coverage: dict) -> list:
    """给 Step04 ``generate_srt(validated_timings=...)`` 的消费形状（**镜内局部时间**）.

    只给可采纳 segment；时间轴换算（clip_window.start 加局部时间、裁到实际镜头窗）
    属于 Step04 的职责——这里**不**替它算绝对时间，也不给 fallback segment 任何时间。
    """
    out = []
    for seg in coverage.get("adoptable_segments") or []:
        out.append({"segment_id": seg["segment_id"], "char_start": seg["char_start"],
                    "char_end": seg["char_end"], "start_sec": seg["start_sec"],
                    "end_sec": seg["end_sec"]})
    return out


def shift_timings(timings: list, offset_sec: float) -> list:
    """把镜内局部时间平移到绝对时间（Step04 用；本函数只做加法，不裁剪）."""
    return [{**t, "start_sec": float(t["start_sec"]) + float(offset_sec),
             "end_sec": float(t["end_sec"]) + float(offset_sec)} for t in timings]


# ---------------------------------------------------------------- 富身份

def read_audio_candidates(path) -> dict:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EnhancementError("audio_candidates_read_failed", "identity",
                               f"读不到音频候选 manifest: {p}（{exc}）",
                               exit_code=EXIT_REJECTED, field_path="audio_manifest_path") from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancementError("audio_candidates_unparsable", "identity",
                               f"音频候选 manifest 无法解析: {exc}",
                               exit_code=EXIT_REJECTED,
                               field_path="audio_manifest_path") from exc
    if not isinstance(data, dict):
        raise EnhancementError("audio_candidates_shape", "identity",
                               "音频候选 manifest 顶层不是对象", exit_code=EXIT_REJECTED,
                               field_path="audio_manifest_path")
    data["_path"] = str(p.resolve())
    data["_sha256"] = sha256_file(p)
    return data


def find_unit_in_manifest(manifest: dict, unit_id: str) -> dict | None:
    """在候选 manifest 里按显式 unit_id 找音轨记录（不按数组位置猜身份）."""
    units = manifest.get("units")
    if isinstance(units, dict):
        item = units.get(unit_id)
        return _normalize_unit(item, unit_id) if item is not None else None
    if isinstance(units, list):
        for item in units:
            if isinstance(item, dict) and str(item.get("unit_id") or item.get("id")) == str(unit_id):
                return _normalize_unit(item, unit_id)
    return None


def _normalize_unit(item: dict, unit_id: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    return {"unit_id": unit_id, "path": item.get("path") or item.get("audio_path"),
            "sha256": item.get("sha256") or item.get("artifact_hash") or item.get("media_sha256"),
            "text": item.get("text") or item.get("spoken_text"),
            "text_sha256": (item.get("text_sha256") or item.get("text_hash")
                            or item.get("hash")),
            "script_revision": item.get("script_revision"),
            "raw": item}


def _audio_identity_api():
    """T 包的 audio_identity 单源（有就用它，避免第二套身份判据漂移）."""
    try:
        from shared import audio_identity as ai
    except Exception:  # noqa: BLE001
        return None
    for name in ("identity_path", "read_identity", "identity_problems", "verify_identity",
                 "load_audio_candidates", "expectation_for"):
        if not hasattr(ai, name):
            return None
    return ai


def read_identity_sidecar(audio_path) -> dict | None:
    """音轨旁的 ``<audio 文件名>.identity.json``（T 包生成的富身份侧车）.

    优先走 ``shared.audio_identity.read_identity``（同一份判据）；T 模块不可用时退回
    本地读取（同样的文件名规则）。缺席返回 None；存在但坏 ⇒ 带 ``_unparsable``。
    """
    api = _audio_identity_api()
    if api is not None:
        path = api.identity_path(audio_path)
        data = api.read_identity(audio_path)
        if data is None:
            if not Path(path).is_file():
                return None
            return {"_path": str(path), "_unparsable": True}
        data = dict(data)
        data["_path"] = str(path)
        try:
            data["_sha256"] = sha256_file(path)
        except OSError:
            data["_sha256"] = None
        return data
    p = Path(str(audio_path) + ".identity.json")
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 坏侧车=没有侧车（拒收由调用方给出）
        return {"_path": str(p), "_unparsable": True}
    if not isinstance(data, dict):
        return {"_path": str(p), "_unparsable": True}
    data["_path"] = str(p)
    data["_sha256"] = sha256_file(p)
    return data


def _first_present(payload: dict, keys: tuple):
    for key in keys:
        if payload.get(key) not in (None, ""):
            return payload[key]
    return None


def unit_lookup(manifest_path, unit_id: str):
    """按显式 unit_id 取期望身份：返回 ``(entry, api)``（T 的消费入口优先）."""
    api = _audio_identity_api()
    if api is not None:
        try:
            candidates = api.load_audio_candidates(manifest_path)
            return dict(api.expectation_for(str(unit_id), candidates)), api
        except Exception:  # noqa: BLE001 - 未知形态/缺席都交给调用方判"没找到"
            return None, api
    manifest = read_audio_candidates(manifest_path)
    return find_unit_in_manifest(manifest, unit_id), None


def verify_audio_identity(request: dict, *, audio_candidates_path=None,
                          require_rich_identity: bool = True) -> dict:
    """对齐前置身份校验（§15）：富身份 + scope/revision；不靠 voice_signature 反推.

    身份判据**单源**：优先调用 T 包 ``shared.audio_identity`` 的 ``identity_problems``
    / ``verify_identity`` / ``expectation_for`` / ``load_audio_candidates``；
    本模块只补"与本次请求声明的关系"（audio_path / audio_sha256 / unit_id / 正文）。

    返回 ``{"problems": [...], "evidence": {...}}``；problems 非空 ⇒ 整个请求拒收。
    """
    problems: list[dict] = []

    def add(code, field_path, message, **extra):
        problems.append(_problem(code, field_path, message, **extra))

    audio_path = Path(request.get("audio_path") or "")
    if not audio_path.is_file():
        add("audio_missing", "audio_path", f"音轨不存在: {audio_path}")
        return {"problems": problems, "evidence": {}}

    actual = sha256_file(audio_path)
    if actual != request.get("audio_sha256"):
        add("audio_sha256_mismatch", "audio_sha256",
            "音轨字节摘要与请求声明不符（音轨被换过或被改名）",
            expected=request.get("audio_sha256"), actual=actual)

    api = _audio_identity_api()
    evidence: dict = {"audio_path": str(audio_path.resolve()), "audio_sha256": actual,
                      "candidates": None, "sidecar": None, "rich_identity": "unverified",
                      "legacy_voice_signature": None,
                      "identity_source": ("shared.audio_identity" if api is not None
                                          else "local")}

    manifest_path = request.get("audio_manifest_path")
    if audio_candidates_path and manifest_path:
        declared = Path(str(manifest_path))
        supplied = Path(str(audio_candidates_path))
        try:
            same = declared.resolve() == supplied.resolve()
        except OSError:
            same = False
        if not same:
            add("audio_manifest_path_mismatch", "audio_manifest_path",
                "命令行 --audio-candidates 与请求里的 audio_manifest_path 不是同一份清单")
    if not manifest_path:
        add("identity_incomplete", "audio_manifest_path",
            "请求未给 audio_manifest_path（富身份入口必需，不能用 voice_signature 反推）")
    else:
        mp = Path(manifest_path)
        if not mp.is_file():
            add("audio_manifest_missing", "audio_manifest_path", f"候选 manifest 不存在: {mp}")
        else:
            if sha256_file(mp) != request.get("audio_manifest_hash"):
                add("audio_manifest_hash_mismatch", "audio_manifest_hash",
                    "候选 manifest 摘要与请求声明不符（读到的是另一份清单）")
            evidence["candidates"] = {"path": str(mp.resolve()), "sha256": sha256_file(mp)}
            entry, _api = unit_lookup(mp, str(request.get("unit_id")))
            if entry is None:
                add("unit_not_in_manifest", "unit_id",
                    f"候选 manifest 里没有 unit_id={request.get('unit_id')!r} 的音轨记录")
            else:
                declared_path = _first_present(entry, ("artifact_path", "path", "audio_path"))
                declared_hash = _first_present(entry, ("artifact_hash", "sha256", "media_sha256"))
                declared_text = _first_present(entry, ("text", "spoken_text"))
                declared_text_hash = _first_present(entry, ("text_hash", "text_sha256", "hash"))
                evidence["unit"] = {"path": declared_path, "sha256": declared_hash}
                if declared_path and Path(str(declared_path)).resolve() != audio_path.resolve():
                    add("unit_audio_path_mismatch", "audio_path",
                        "请求的 audio_path 与 manifest 里该 unit 的音轨路径不一致")
                if declared_hash and str(declared_hash) != actual:
                    add("unit_audio_hash_mismatch", "audio_sha256",
                        "请求的音轨与 manifest 里该 unit 声明的媒体摘要不一致")
                if declared_text is not None and str(declared_text) != str(request.get("spoken_text")):
                    add("unit_text_mismatch", "spoken_text",
                        "请求的 spoken_text 与 manifest 里该 unit 的文本不一致")
                elif declared_text_hash and not digests_match(declared_text_hash,
                                                              request.get("spoken_text")):
                    add("unit_text_mismatch", "spoken_text",
                        "请求的 spoken_text 摘要与 manifest 里该 unit 的文本摘要不一致")
                rev = _first_present(entry, ("script_revision",))
                if rev and str(rev) != str(request.get("script_revision")):
                    add("script_revision_mismatch", "script_revision",
                        "manifest 里该 unit 的 script_revision 与请求不一致")

    sidecar = read_identity_sidecar(audio_path)
    evidence["sidecar"] = None if sidecar is None else {
        "path": sidecar.get("_path"), "schema_version": sidecar.get("schema_version"),
        "sha256": sidecar.get("_sha256")}
    if sidecar is None:
        if require_rich_identity:
            add("identity_incomplete", "audio_path",
                f"缺少富身份侧车 {Path(str(audio_path) + '.identity.json').name}"
                "（T 包 audio_identity.v2；不靠 voice_signature 反推）")
    elif sidecar.get("_unparsable"):
        add("identity_unparsable", "audio_path", "富身份侧车存在但无法解析")
    else:
        schema = sidecar.get("schema_version")
        if schema == AUDIO_IDENTITY_LEGACY_SCHEMA:
            add("identity_incomplete", "audio_path",
                "只有旧 audio_identity.v1 侧车：旧签名不能用于采纳新模型音轨（§T2a）")
        elif schema != AUDIO_IDENTITY_SCHEMA:
            add("identity_incomplete", "audio_path",
                f"富身份侧车 schema 不符: {schema!r} != {AUDIO_IDENTITY_SCHEMA!r}")
        else:
            evidence["rich_identity"] = "v2"
            if api is not None:
                structural = list(api.identity_problems(sidecar) or [])
                if structural:
                    add("identity_incomplete", "audio_path",
                        "富身份侧车结构不完整（T 包判据）",
                        identity_problems=structural[:8])
            if str(sidecar.get("unit_id") or "") != str(request.get("unit_id")):
                add("identity_unit_mismatch", "unit_id", "富身份侧车的 unit_id 与请求不一致")
            sc_text = sidecar.get("text")
            if sc_text is not None and str(sc_text) != str(request.get("spoken_text")):
                add("identity_text_mismatch", "spoken_text",
                    "富身份侧车里记录的文本与请求的 spoken_text 不一致（这是另一段声音）")
            elif not digests_match(_first_present(sidecar, ("text_hash", "text_sha256")),
                                   request.get("spoken_text")):
                add("identity_text_mismatch", "spoken_text",
                    "富身份侧车的文本摘要与请求的 spoken_text 摘要不一致")
            artifact_hash = _first_present(sidecar, ("artifact_hash", "audio_sha256"))
            if artifact_hash and str(artifact_hash) != actual:
                add("identity_audio_hash_mismatch", "audio_sha256",
                    "富身份侧车记录的媒体摘要与磁盘上的音轨不一致")
            if sidecar.get("script_revision") and \
                    str(sidecar["script_revision"]) != str(request.get("script_revision")):
                add("script_revision_mismatch", "script_revision",
                    "富身份侧车的 script_revision 与请求不一致")
            scope = sidecar.get("scope")
            if isinstance(scope, dict) and request.get("scope") is not None \
                    and scope != request.get("scope"):
                add("scope_mismatch", "scope", "富身份侧车的 scope 与请求不一致")
            if api is not None and request.get("audio_manifest_path"):
                entry, _ = unit_lookup(request.get("audio_manifest_path"),
                                       str(request.get("unit_id")))
                if entry is not None:
                    reason = api.verify_identity(audio_path, expected=entry, require_media=True)
                    if reason:
                        incomplete = str(getattr(api, "IDENTITY_INCOMPLETE",
                                                 "identity_incomplete"))
                        add("identity_incomplete" if str(reason).startswith(incomplete)
                            else "identity_relation_mismatch", "audio_identity",
                            f"富身份关系核对未通过（T 包判据）：{reason}")

    # 来源政策：本包只支持"独立对白 TTS"；显式声明 native/旁白/重叠说话一律拒收
    policy = request.get("source_policy")
    if policy is not None and str(policy) not in ("dialogue_tts", "tts", "tts_dialogue"):
        add("unsupported_source_policy", "source_policy",
            f"本包首版只支持 tts_mix 里的独立对白 TTS，不支持 {policy!r}"
            "（native 对白/图外旁白/重叠说话仍走原字幕策略）")

    # 旧 .voice 边车只作**附带核对**（存在且不符 ⇒ 拒收；不存在不影响 v2 富身份）
    legacy = Path(str(audio_path)).with_suffix(".voice")
    if legacy.is_file():
        try:
            content = legacy.read_text(encoding="utf-8").strip()
        except OSError:
            content = None
        evidence["legacy_voice_signature"] = content
        if content and request.get("voice_signature") and content != str(request["voice_signature"]):
            add("voice_signature_mismatch", "voice_signature",
                "旧 .voice 边车与请求的 voice_signature 不符（附带核对失败，不因此认定身份 OK）")
    return {"problems": problems, "evidence": evidence}


def identity_gate(request: dict, **kwargs) -> dict:
    """身份门：有问题直接抛 ``EnhancementError``（exit 4，明确拒收）."""
    result = verify_audio_identity(request, **kwargs)
    if result["problems"]:
        raise EnhancementError("identity_rejected", "identity",
                               f"音频富身份校验未通过（{len(result['problems'])} 项）",
                               exit_code=EXIT_REJECTED, field_path="audio_identity",
                               residual_issues=result["problems"])
    return result["evidence"]


__all__ = [name for name in dir() if not name.startswith("_")]
