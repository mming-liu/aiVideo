# -*- coding: utf-8 -*-
"""shared.media_inspect - B 包：连续时间技术检查与切点定位（计划 §B1/§B2/§B3）.

只报告、不改片：**检出黑场/静止/静音/切点不等于生成错误**，本模块不接生产拒收、
不触发重生成、不引入新的内容硬门。

## 数据

- spec ``media_inspection.v1``：``input_sha256, source_identity, source_revision,
  input_kind(shot|final), checks, detector_parameters, allowed_intervals``。
  **缺省空且不猜"应该允许"**：没有显式允许区间时事件只能落到 undecided。
- report ``media_inspection_report.v1``：``source, tool_versions, parameters,
  checks[{name,status,error}], events[...], report_status``。
- decode 证据 ``media_decode_evidence.v1``：**读取**（生产者是 S 包候选验收器）。
  同 hash **且** 同 ffmpeg build 才复用；否则独立 decode，不假定历史记录存在。

## 事件分类（§B2）

| 情况 | 分类 |
|---|---|
| 事件**完全**落在同类型显式允许区间内 | ``expected`` |
| 来源**明确声明禁止**该类型且发生 | ``suspicious`` |
| 缺声明 / 与允许区间边界交叉 / 类型不匹配 | ``undecided`` |

允许区间命中优先于禁止声明（同一份 spec 自相矛盾时按"已显式许可"读，并在
``notes`` 里点出冲突）。有 ``suspicious`` **不等于运行失败**。

## 状态

``checks[].status``：``completed``（工具跑完）/ ``failed``（工具跑失败，含 stderr
解析失败）/ ``unavailable``（工具或隔离运行时缺失）。
``report_status``：任一 check ``failed`` ⇒ ``partial``；全部 ``unavailable`` ⇒
``unverified``；全 ``completed`` ⇒ ``complete``；``completed``+``unavailable`` 混合
（无 failed）⇒ ``partial``（覆盖不完整，不能报 complete）。
**空 events 与 unverified 是两件事**：没有事件是结论，没有工具是没结论。

## 工具调用

一律 ``subprocess`` **参数列表**（禁 shell 拼串），记录 ffmpeg build 与返回码、
stderr 摘要；解析失败时保留原始 stderr 行，不生成"零事件成功报告"。
cuts 走 :mod:`tools.enhancement_workers` 的 Python 3.12 子进程协议——宿主**不 import**
PySceneDetect。
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from shared.enhancement_io import (
    EXIT_INVALID_INPUT,
    EXIT_TOOL_FAIL,
    EnhancementError,
    sha256_file,
    sha256_text,
)

# ---------------------------------------------------------------- schema 与常量

SPEC_SCHEMA = "media_inspection.v1"
REPORT_SCHEMA = "media_inspection_report.v1"
DECODE_EVIDENCE_SCHEMA = "media_decode_evidence.v1"

INPUT_KIND_SHOT = "shot"
INPUT_KIND_FINAL = "final"
INPUT_KINDS = (INPUT_KIND_SHOT, INPUT_KIND_FINAL)

#: 来源 manifest 自己声明的产物种类 → 本 spec 的 input_kind。
#: S 包（step04）的 merge manifest 顶层写 ``kind="merge"``——它就是**成片**输入。
MANIFEST_KIND_TO_INPUT_KIND = {"merge": INPUT_KIND_FINAL, "final": INPUT_KIND_FINAL,
                               "shot": INPUT_KIND_SHOT}

#: 允许区间 kind → 它**覆盖**的事件 kind（时间语义相同，只是词汇不同）。
#: S 把边界过渡声明成 ``transition``，而切点检测在过渡窗内报 ``cut``：不做这层显式
#: 适配，成片的每个合法过渡都会退化成 undecided。未列出的 kind 只与自身匹配（严格
#: 相等），**不猜近义词**。
INTERVAL_KIND_EVENT_KINDS = {"transition": ("cut",)}

CHECK_DECODE = "decode"
CHECK_BLACK = "black"
CHECK_FREEZE = "freeze"
CHECK_SILENCE = "silence"
CHECK_CUTS = "cuts"
ALL_CHECKS = (CHECK_DECODE, CHECK_BLACK, CHECK_FREEZE, CHECK_SILENCE, CHECK_CUTS)

EVENT_KIND_BLACK = "black"
EVENT_KIND_FREEZE = "freeze"
EVENT_KIND_SILENCE = "silence"
EVENT_KIND_CUT = "cut"

VIDEO_EVENT_KINDS = (EVENT_KIND_BLACK, EVENT_KIND_FREEZE, EVENT_KIND_CUT)

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_UNAVAILABLE = "unavailable"
CHECK_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_UNAVAILABLE)

REPORT_COMPLETE = "complete"
REPORT_PARTIAL = "partial"
REPORT_UNVERIFIED = "unverified"

CLASS_EXPECTED = "expected"
CLASS_SUSPICIOUS = "suspicious"
CLASS_UNDECIDED = "undecided"

#: 首轮检测参数 = **工具自身默认值**（ffmpeg blackdetect/freezedetect/silencedetect
#: 文档默认、PySceneDetect AdaptiveDetector 默认），不是新造的生产阈值。
DEFAULT_DETECTOR_PARAMETERS = {
    "black": {"min_duration_sec": 2.0, "pixel_black_th": 0.10,
              "picture_black_ratio_th": 0.98},
    "freeze": {"noise_tolerance_db": -60.0, "min_duration_sec": 2.0},
    "silence": {"noise_tolerance_db": -60.0, "min_duration_sec": 2.0},
    "cuts": {"adaptive_threshold": 3.0, "min_scene_len_frames": 15, "window_width": 2},
    "coverage_tolerance_sec": 0.05,
}

ENV_FFMPEG = "MEDIA_INSPECT_FFMPEG"
ENV_FFPROBE = "MEDIA_INSPECT_FFPROBE"

DEFAULT_TIMEOUT_SEC = 120.0
DEFAULT_CUTS_TIMEOUT_SEC = 300.0
DEFAULT_MAX_EVIDENCE_FRAMES = 6

# ---------------------------------------------------------------- stderr 解析正则

_BLACK_RE = re.compile(
    r"black_start:\s*(?P<start>-?\d+(?:\.\d+)?)\s+black_end:\s*(?P<end>-?\d+(?:\.\d+)?)"
    r"\s+black_duration:\s*(?P<dur>-?\d+(?:\.\d+)?)")
_FREEZE_START_RE = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(?P<start>-?\d+(?:\.\d+)?)")
_FREEZE_END_RE = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*(?P<end>-?\d+(?:\.\d+)?)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>-?\d+(?:\.\d+)?)(?:\s*\|\s*silence_duration:\s*(?P<dur>-?\d+(?:\.\d+)?))?")


# ---------------------------------------------------------------- 工具执行（唯一入口）

@dataclass
class ToolRun:
    """一次外部工具调用的事实（argv **参数列表**、返回码、stderr 原样摘要）."""

    argv: list
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None
    duration_sec: float | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.error is None

    def payload(self) -> dict:
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "error": self.error,
            "duration_sec": self.duration_sec,
            "stderr_digest": sha256_text(self.stderr),
            "stdout_digest": sha256_text(self.stdout),
        }


def run_argv(argv, *, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> ToolRun:
    """模块内**唯一**的外部工具执行入口（subprocess 参数列表，``shell=False``）.

    测试通过对本函数的注入证明"工具缺失/返回码非零/stderr 解析失败"分支真的走到；
    生产路径不吞任何异常：spawn 失败与超时都如实写进 :class:`ToolRun`。
    """
    argv = [str(a) for a in argv]
    started = time.time()
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True, timeout=float(timeout_sec))
    except subprocess.TimeoutExpired as exc:
        return ToolRun(argv=argv, returncode=None, timed_out=True,
                       stdout=(exc.stdout or b"").decode("utf-8", "replace"),
                       stderr=(exc.stderr or b"").decode("utf-8", "replace"),
                       error=f"timeout>{timeout_sec}s",
                       duration_sec=round(time.time() - started, 3))
    except OSError as exc:
        return ToolRun(argv=argv, returncode=None, error=f"{type(exc).__name__}: {exc}",
                       duration_sec=round(time.time() - started, 3))
    return ToolRun(argv=argv, returncode=proc.returncode,
                   stdout=(proc.stdout or b"").decode("utf-8", "replace"),
                   stderr=(proc.stderr or b"").decode("utf-8", "replace"),
                   duration_sec=round(time.time() - started, 3))


_TOOL_CACHE: dict = {}


def reset_tool_cache() -> None:
    _TOOL_CACHE.clear()


def ffmpeg_executable() -> str | None:
    override = (os.getenv(ENV_FFMPEG) or "").strip()
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("ffmpeg")


def ffprobe_executable() -> str | None:
    override = (os.getenv(ENV_FFPROBE) or "").strip()
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("ffprobe")


def tool_build(kind: str) -> dict:
    """工具版本/build（``-version`` 首行 + configuration 摘要）；取不到如实写 None."""
    key = f"build:{kind}"
    if key in _TOOL_CACHE:
        return _TOOL_CACHE[key]
    exe = ffmpeg_executable() if kind == "ffmpeg" else ffprobe_executable()
    info_ = {"kind": kind, "executable": exe, "version_line": None, "configuration": None,
             "configuration_digest": None, "available": False, "error": None}
    if not exe:
        info_["error"] = "tool_missing"
    else:
        run = run_argv([exe, "-version"], timeout_sec=30.0)
        if run.ok:
            lines = run.stdout.splitlines()
            info_["version_line"] = lines[0].strip() if lines else None
            for line in lines:
                if line.startswith("configuration:"):
                    info_["configuration"] = line.strip()
                    info_["configuration_digest"] = sha256_text(line.strip())[:32]
                    break
            info_["available"] = bool(info_["version_line"])
        else:
            info_["error"] = run.error or f"returncode={run.returncode}"
    _TOOL_CACHE[key] = info_
    return info_


def probe_media(path, *, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> dict:
    """ffprobe 读流/帧率/时长（读取动作，不是检查项）."""
    exe = ffprobe_executable()
    if not exe:
        return {"status": "unavailable", "error": {"code": "tool_missing",
                                                   "message": "找不到 ffprobe"},
                "streams": None}
    argv = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
            str(path)]
    run = run_argv(argv, timeout_sec=timeout_sec)
    base = {"status": "completed", "argv": argv, "returncode": run.returncode,
            "stderr_digest": sha256_text(run.stderr), "streams": None, "video": None,
            "audio": None, "duration_sec": None, "error": None}
    if not run.ok:
        base["status"] = "failed"
        base["error"] = {"code": "probe_failed" if not run.timed_out else "probe_timeout",
                         "message": run.error or f"ffprobe 返回码 {run.returncode}"}
        return base
    try:
        data = json.loads(run.stdout or "{}")
    except Exception as exc:  # noqa: BLE001
        base["status"] = "failed"
        base["error"] = {"code": "probe_output_unparsable", "message": f"{exc}"}
        return base
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = fmt.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    base.update({
        "streams": [{"index": s.get("index"), "codec_type": s.get("codec_type"),
                     "codec_name": s.get("codec_name"), "width": s.get("width"),
                     "height": s.get("height"), "r_frame_rate": s.get("r_frame_rate"),
                     "avg_frame_rate": s.get("avg_frame_rate"),
                     "sample_rate": s.get("sample_rate"), "channels": s.get("channels"),
                     "duration": s.get("duration")} for s in streams],
        "video": ({"codec_name": video.get("codec_name"), "width": video.get("width"),
                   "height": video.get("height"),
                   "fps": _fps_of(video)} if video else None),
        "audio": ({"codec_name": audio.get("codec_name"),
                   "sample_rate": audio.get("sample_rate"),
                   "channels": audio.get("channels")} if audio else None),
        "duration_sec": duration,
        "format_name": fmt.get("format_name"),
    })
    return base


def _fps_of(stream: dict) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if not raw or raw in ("0/0", "N/A"):
            continue
        try:
            num, _, den = str(raw).partition("/")
            value = float(num) / float(den or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if value > 0:
            return round(value, 6)
    return None


# ---------------------------------------------------------------- spec：校验与计划

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def validate_spec(spec) -> list:
    """spec 结构校验（返回问题记录：code/field_path/message）."""
    problems: list[dict] = []

    def add(code, field_path, message):
        problems.append({"code": code, "field_path": field_path, "message": message})

    if not isinstance(spec, dict):
        return [{"code": "spec_not_object", "field_path": "", "message": "spec 必须是 JSON 对象"}]
    if spec.get("schema_version") != SPEC_SCHEMA:
        add("spec_schema", "schema_version",
            f"spec schema 不符: {spec.get('schema_version')!r} != {SPEC_SCHEMA!r}")
    for key in ("input_sha256", "source_identity", "source_revision", "input_kind", "checks"):
        if spec.get(key) in (None, ""):
            add("spec_missing_field", key, f"spec 缺少必需字段 {key}")
    if spec.get("input_kind") not in (None, *INPUT_KINDS):
        add("spec_input_kind", "input_kind",
            f"input_kind 必须是 {INPUT_KINDS} 之一，得到 {spec.get('input_kind')!r}")
    checks = spec.get("checks")
    if checks is not None:
        if not isinstance(checks, list) or not checks:
            add("spec_checks", "checks", "checks 必须是非空数组（显式选择检查项）")
        else:
            for i, name in enumerate(checks):
                if name not in ALL_CHECKS:
                    add("spec_check_unknown", f"checks[{i}]",
                        f"未知检查项 {name!r}（合法: {list(ALL_CHECKS)}）")
    params = spec.get("detector_parameters")
    if params is not None and not isinstance(params, dict):
        add("spec_parameters", "detector_parameters", "detector_parameters 必须是对象")
    intervals = spec.get("allowed_intervals")
    if not isinstance(intervals, list):
        add("spec_allowed_intervals", "allowed_intervals",
            "allowed_intervals 必须是数组（没有允许区间就用空数组，**不许猜**）")
    else:
        for i, item in enumerate(intervals):
            fp = f"allowed_intervals[{i}]"
            if not isinstance(item, dict):
                add("spec_interval_shape", fp, "允许区间必须是对象")
                continue
            if not item.get("kind"):
                add("spec_interval_kind", f"{fp}.kind", "允许区间缺少 kind")
            start, end = item.get("start_sec"), item.get("end_sec")
            if not _is_number(start) or not _is_number(end):
                add("spec_interval_time", fp, f"允许区间时间必须是有限数：start={start!r} end={end!r}")
            elif float(end) < float(start):
                add("spec_interval_order", fp, f"允许区间 end 不能小于 start：{start} → {end}")
            # start == end 是**允许的**：切点是零长时间点（S 的 declared_internal_cuts
            # 就用 start==end 表示一个切点），不是非法区间。
            if "source_locator" not in item:
                add("spec_interval_locator", f"{fp}.source_locator",
                    "允许区间必须带 source_locator（说明它凭什么被允许）")
    prohibitions = spec.get("source_declarations") or {}
    if prohibitions and not isinstance(prohibitions, dict):
        add("spec_declarations", "source_declarations", "source_declarations 必须是对象")
    return problems


def load_spec(path) -> dict:
    """读 spec 文件并校验；不合法 ⇒ ``EnhancementError``（退出码 2）."""
    p = Path(path)
    try:
        spec = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EnhancementError("spec_read_failed", "spec", f"读不到 spec: {p}（{exc}）",
                               exit_code=EXIT_INVALID_INPUT, field_path="spec") from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancementError("spec_unparsable", "spec", f"spec JSON 无法解析: {p}（{exc}）",
                               exit_code=EXIT_INVALID_INPUT, field_path="spec") from exc
    problems = validate_spec(spec)
    if problems:
        raise EnhancementError("spec_invalid", "spec",
                               f"spec 结构不合法（{len(problems)} 项）",
                               exit_code=EXIT_INVALID_INPUT, field_path="spec",
                               residual_issues=problems)
    return spec


def make_spec(input_path, *, source_identity: str, source_revision: str, input_kind: str,
              checks=None, detector_parameters=None, allowed_intervals=None,
              prohibited_kinds=None, source_manifest_path=None, notes=None) -> dict:
    """构造 ``media_inspection.v1``（纯函数；**不执行任何检查**）."""
    if input_kind not in INPUT_KINDS:
        raise EnhancementError("spec_input_kind", "plan",
                               f"input_kind 必须是 {INPUT_KINDS} 之一：{input_kind!r}",
                               exit_code=EXIT_INVALID_INPUT, field_path="input_kind")
    if not source_identity or not source_revision:
        raise EnhancementError("spec_identity_missing", "plan",
                               "source_identity / source_revision 必填（来源身份不能猜）",
                               exit_code=EXIT_INVALID_INPUT,
                               field_path="source_identity" if not source_identity
                               else "source_revision")
    params = _merge_detector_parameters(detector_parameters)
    declarations = {"prohibited_kinds": list(prohibited_kinds or [])}
    spec = {
        "schema_version": SPEC_SCHEMA,
        "input_path": str(Path(input_path).resolve()),
        "input_sha256": sha256_file(Path(input_path)),
        "source_identity": str(source_identity),
        "source_revision": str(source_revision),
        "input_kind": input_kind,
        "checks": list(checks or ALL_CHECKS),
        "detector_parameters": params,
        "allowed_intervals": list(allowed_intervals or []),
        "source_declarations": declarations,
        "source_manifest_path": (str(Path(source_manifest_path).resolve())
                                 if source_manifest_path else None),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": list(notes or []),
    }
    problems = validate_spec(spec)
    if problems:
        raise EnhancementError("spec_invalid", "plan", f"生成的 spec 不合法（{len(problems)} 项）",
                               exit_code=EXIT_INVALID_INPUT, residual_issues=problems)
    return spec


def _merge_detector_parameters(overrides) -> dict:
    merged = {k: dict(v) if isinstance(v, dict) else v
              for k, v in DEFAULT_DETECTOR_PARAMETERS.items()}
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def plan_inspection(input_path, source_manifest_path, out_dir=None, *, input_kind=None,
                    checks=None, detector_parameters=None) -> dict:
    """``plan``：从输入 manifest 生成 spec —— **只读产出，不执行检查**（§B1）.

    manifest 认得的显式声明（都不猜，缺省即空）：

    - ``input_kind``/``kind``：``shot`` 或 ``final``；S 包（step04）的 merge manifest 顶层写
      ``kind="merge"``，按**成片**读（``MANIFEST_KIND_TO_INPUT_KIND``）；
    - ``source_identity``（或 ``unit_id``/``shot_id``/``input_key``——S 的 merge manifest 给
      ``input_key``）、``source_revision``（或 ``script_revision``）；
    - ``allowed_intervals``：**直接复制**（final 的转场窗由 S 的真实时间表给出：
      端点淡入/淡出 ``kind="black"``、边界过渡 ``kind="transition"``，各带 RFC6901
      ``source_locator``）；shot 的合法内部切镜由 ``declared_internal_cuts`` 给出——
      **零长区间（start==end）表示一个切点**，本模块接受这种写法；
    - ``prohibited_kinds``：来源明确声明"不该出现"的事件类型（→ suspicious 的唯一来源）；
      来源没声明时是空表，**不倒推**；
    - ``checks`` / ``detector_parameters``：可选覆盖（例如成片端点淡出只有 0.5s 时，
      把 ``black.min_duration_sec`` 调到小于淡出时长，否则按工具默认 2.0s 检不出事件）。
    """
    manifest_path = Path(source_manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EnhancementError("manifest_read_failed", "plan", f"读不到 source manifest: {exc}",
                               exit_code=EXIT_INVALID_INPUT, field_path="source_manifest") from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancementError("manifest_unparsable", "plan",
                               f"source manifest JSON 无法解析: {exc}",
                               exit_code=EXIT_INVALID_INPUT, field_path="source_manifest") from exc
    if not isinstance(manifest, dict):
        raise EnhancementError("manifest_shape", "plan", "source manifest 顶层不是对象",
                               exit_code=EXIT_INVALID_INPUT, field_path="source_manifest")

    declared_kind = manifest.get("input_kind") or manifest.get("kind")
    kind = input_kind or MANIFEST_KIND_TO_INPUT_KIND.get(str(declared_kind), declared_kind)
    if kind not in INPUT_KINDS:
        raise EnhancementError(
            "manifest_input_kind", "plan",
            f"manifest 未显式声明 input_kind ∈ {INPUT_KINDS}（得到 {kind!r}）——不猜输入类型",
            exit_code=EXIT_INVALID_INPUT, field_path="input_kind")

    identity = (manifest.get("source_identity") or manifest.get("unit_id")
                or manifest.get("shot_id") or manifest.get("input_key"))  # S 的 merge manifest 给 input_key
    revision = manifest.get("source_revision") or manifest.get("script_revision")
    if not identity or not revision:
        raise EnhancementError(
            "manifest_identity_missing", "plan",
            "manifest 缺少 source_identity/unit_id/shot_id 或 source_revision/script_revision",
            exit_code=EXIT_INVALID_INPUT,
            field_path="source_identity" if not identity else "source_revision")

    intervals: list[dict] = []
    notes: list[str] = []
    for i, item in enumerate(manifest.get("allowed_intervals") or []):
        if not isinstance(item, dict):
            raise EnhancementError("manifest_interval_shape", "plan",
                                   f"allowed_intervals[{i}] 不是对象",
                                   exit_code=EXIT_INVALID_INPUT,
                                   field_path=f"allowed_intervals[{i}]")
        intervals.append({"kind": item.get("kind"), "start_sec": item.get("start_sec"),
                          "end_sec": item.get("end_sec"),
                          "source_locator": item.get("source_locator")})
    for i, item in enumerate(manifest.get("declared_internal_cuts") or []):
        if not isinstance(item, dict):
            raise EnhancementError("manifest_interval_shape", "plan",
                                   f"declared_internal_cuts[{i}] 不是对象",
                                   exit_code=EXIT_INVALID_INPUT,
                                   field_path=f"declared_internal_cuts[{i}]")
        intervals.append({"kind": EVENT_KIND_CUT, "start_sec": item.get("start_sec"),
                          "end_sec": item.get("end_sec"),
                          "source_locator": item.get("source_locator")})
    prohibited = list(manifest.get("prohibited_kinds") or [])
    if kind == INPUT_KIND_FINAL and not intervals:
        notes.append("final 输入未给出转场允许窗：S 的时间表是唯一来源，本 spec 不从检测结果倒推")

    spec = make_spec(input_path,
                     source_identity=identity,
                     source_revision=revision,
                     input_kind=kind,
                     checks=checks or manifest.get("checks") or ALL_CHECKS,
                     detector_parameters=detector_parameters or manifest.get("detector_parameters"),
                     allowed_intervals=intervals,
                     prohibited_kinds=prohibited,
                     source_manifest_path=manifest_path,
                     notes=notes)
    spec["source_manifest_sha256"] = sha256_file(manifest_path)
    if out_dir is not None:
        target = Path(out_dir) / "specs" / f"{Path(input_path).stem}.media_inspection.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, spec)
        spec["spec_path"] = str(target.resolve())
    return spec


def write_json(path, obj) -> Path:
    """原子写 JSON（同目录 .tmp → os.replace）——本模块与 CLI 的唯一 JSON 落盘入口."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- stderr 解析

def parse_blackdetect(stderr: str) -> tuple[list, list]:
    events, problems = [], []
    for line in stderr.splitlines():
        if "black_start" not in line:
            continue
        m = _BLACK_RE.search(line)
        if not m:
            problems.append({"code": "stderr_parse_failed", "kind": EVENT_KIND_BLACK,
                             "raw_line": line.strip()[:400]})
            continue
        start, end = float(m.group("start")), float(m.group("end"))
        events.append({"kind": EVENT_KIND_BLACK, "start_sec": start, "end_sec": end,
                       "raw_score": end - start})
    return events, problems


def _parse_paired(stderr: str, start_re, end_re, kind: str, *, duration_sec) -> tuple[list, list]:
    events, problems = [], []
    open_start: float | None = None
    for line in stderr.splitlines():
        matches_start = f"{kind}_start" in line
        matches_end = f"{kind}_end" in line
        if not matches_start and not matches_end:
            continue
        ms, me = start_re.search(line), end_re.search(line)
        if ms:
            if open_start is not None:  # 上一段没有 end ⇒ 记问题，不静默合并
                problems.append({"code": "stderr_parse_failed", "kind": kind,
                                 "raw_line": line.strip()[:400],
                                 "detail": "连续两个 start 之间没有 end"})
                events.append({"kind": kind, "start_sec": open_start,
                               "end_sec": _infer_end(None, duration_sec),
                               "raw_score": None, "end_inferred": "eof"})
            open_start = float(ms.group("start"))
            continue
        if me:
            end = float(me.group("end"))
            start = open_start if open_start is not None else end
            events.append({"kind": kind, "start_sec": start, "end_sec": end,
                           "raw_score": (end - start), "end_inferred": None})
            open_start = None
            continue
        problems.append({"code": "stderr_parse_failed", "kind": kind,
                         "raw_line": line.strip()[:400]})
    if open_start is not None:
        # 工具在媒体结束前没有打 end：按"延续到末尾"读，但**显式标注**是推断
        events.append({"kind": kind, "start_sec": open_start,
                       "end_sec": _infer_end(None, duration_sec),
                       "raw_score": None, "end_inferred": "eof"})
    return events, problems


def _infer_end(_value, duration_sec):
    return float(duration_sec) if _is_number(duration_sec) else None


def parse_freezedetect(stderr: str, *, duration_sec=None) -> tuple[list, list]:
    return _parse_paired(stderr, _FREEZE_START_RE, _FREEZE_END_RE, EVENT_KIND_FREEZE,
                         duration_sec=duration_sec)


def parse_silencedetect(stderr: str, *, duration_sec=None) -> tuple[list, list]:
    events, problems = [], []
    open_start: float | None = None
    for line in stderr.splitlines():
        if "silence_start" in line:
            m = _SILENCE_START_RE.search(line)
            if not m:
                problems.append({"code": "stderr_parse_failed", "kind": EVENT_KIND_SILENCE,
                                 "raw_line": line.strip()[:400]})
                continue
            open_start = float(m.group("start"))
            continue
        if "silence_end" in line:
            m = _SILENCE_END_RE.search(line)
            if not m:
                problems.append({"code": "stderr_parse_failed", "kind": EVENT_KIND_SILENCE,
                                 "raw_line": line.strip()[:400]})
                continue
            end = float(m.group("end"))
            start = open_start if open_start is not None else end
            events.append({"kind": EVENT_KIND_SILENCE, "start_sec": start, "end_sec": end,
                           "raw_score": (end - start), "end_inferred": None})
            open_start = None
    if open_start is not None:
        events.append({"kind": EVENT_KIND_SILENCE, "start_sec": open_start,
                       "end_sec": _infer_end(None, duration_sec),
                       "raw_score": None, "end_inferred": "eof"})
    return events, problems


# ---------------------------------------------------------------- decode 证据

#: 证据记录里"解码成功"的写法：S 包生产侧写 ``decoded``，本包历史上写过 ``completed``
DECODE_SUCCESS_STATUSES = ("decoded", "completed")


def read_decode_evidence(path) -> dict | None:
    """读 ``media_decode_evidence.v1``（生产者是 S 包候选验收器）；不合法返回 None.

    字段以 S 的生产实现为准：**恰好六字段** ``input_hash/ffmpeg_build/argv/returncode/
    stderr_digest/status``；``schema_version`` 可有可无——有就必须是本 schema。
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 坏证据=没有证据（独立 decode），不静默当通过
        return None
    if not isinstance(data, dict):
        return None
    declared = data.get("schema_version")
    if declared is not None and declared != DECODE_EVIDENCE_SCHEMA:
        return None
    for key in ("input_hash", "ffmpeg_build", "argv", "returncode", "stderr_digest", "status"):
        if data.get(key) in (None, ""):
            return None
    if not isinstance(data.get("argv"), list):
        return None
    data["_path"] = str(p.resolve())
    data["_sha256"] = sha256_file(p)
    return data


def decode_evidence_candidates(input_path, spec) -> list:
    """证据查找：spec 显式路径优先，其次输入同目录的约定名（**只读，不写输入目录**）."""
    out: list = []
    explicit = spec.get("decode_evidence_path") if isinstance(spec, dict) else None
    if explicit:
        out.append(Path(explicit))
    p = Path(input_path)
    # S 包候选验收器的**固定位置**（candidate outputs/media_decode_evidence.json）
    out.append(p.parent / "media_decode_evidence.json")
    out.append(p.with_name(p.stem + ".decode_evidence.json"))
    out.append(p.with_name(p.name + ".decode_evidence.json"))
    seen, uniq = set(), []
    for item in out:
        key = str(item)
        if key not in seen:
            seen.add(key)
            uniq.append(item)
    return uniq


def ffmpeg_build_tokens(build: dict) -> tuple:
    """本机 ffmpeg build 的可比写法：完整版本行 **和** S 用的短版本号（首行第 3 段）.

    ``ffmpeg -version`` 首行形如 ``ffmpeg version 8.1.2-full_build-... Copyright ...``，
    S 包 ``ffmpeg_version()`` 取的是 ``parts[2]``；两种写法都算"同一个 build"。
    """
    line = str(build.get("version_line") or "").strip()
    tokens = [line] if line else []
    parts = line.split()
    if len(parts) >= 3 and parts[0] == "ffmpeg":
        tokens.append(parts[2])
    return tuple(t for t in tokens if t)


def _reusable_decode_evidence(evidence: dict, spec: dict, build: dict) -> tuple[bool, str]:
    if evidence.get("input_hash") != spec.get("input_sha256"):
        return False, "input_hash_mismatch"
    if str(evidence.get("status")) not in DECODE_SUCCESS_STATUSES:
        return False, "evidence_status_not_success"
    if int(evidence.get("returncode") or 0) != 0:
        return False, "evidence_returncode_nonzero"
    declared_build = str(evidence.get("ffmpeg_build") or "").strip()
    if declared_build not in ffmpeg_build_tokens(build):
        return False, "ffmpeg_build_mismatch"
    return True, "reused"


# ---------------------------------------------------------------- 检查执行

def _ffmpeg_prefix(exe: str) -> list:
    # -nostdin: 不读宿主 stdin；-nostats: 不把进度统计混进我们要解析的 stderr
    return [exe, "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-i"]


def _check_record(name: str) -> dict:
    return {"name": name, "status": STATUS_UNAVAILABLE, "error": None, "events": [],
            "problems": [], "notes": []}


def _fail_check(check: dict, code: str, message: str, **detail) -> dict:
    check["status"] = STATUS_FAILED
    check["error"] = {"code": code, "message": message, "detail": detail}
    return check


def _unavailable_check(check: dict, code: str, message: str, **detail) -> dict:
    check["status"] = STATUS_UNAVAILABLE
    check["error"] = {"code": code, "message": message, "detail": detail}
    return check


def _assign_event_ids(checks: list) -> None:
    """事件 id 由生产者在遍历时给出（报告层不按文案猜身份）；此处只按 kind+序号派生."""
    counters: dict = {}
    for check in checks:
        for event in check.get("events") or []:
            kind = event.get("kind") or "event"
            counters[kind] = counters.get(kind, 0) + 1
            event.setdefault("event_id", f"{kind}-{counters[kind]:03d}")


def run_checks(spec: dict, *, input_path, work_dir=None, evidence_frames=True,
               max_evidence_frames: int = DEFAULT_MAX_EVIDENCE_FRAMES,
               timeout_sec: float = DEFAULT_TIMEOUT_SEC,
               cuts_timeout_sec: float = DEFAULT_CUTS_TIMEOUT_SEC,
               probe: dict | None = None) -> tuple[list, dict]:
    """按 spec 执行检查，返回 ``(checks, tool_versions)``."""
    exe = ffmpeg_executable()
    duration = (probe or {}).get("duration_sec")
    params = spec.get("detector_parameters") or {}
    checks: list[dict] = []
    tool_versions = {
        "ffmpeg": tool_build("ffmpeg"),
        "ffprobe": tool_build("ffprobe"),
        "media_inspect": {"python": python_version()},
    }
    for name in spec.get("checks") or []:
        if name == CHECK_DECODE:
            checks.append(_check_decode(spec, input_path=input_path, exe=exe,
                                        timeout_sec=timeout_sec))
        elif name == CHECK_BLACK:
            checks.append(_check_ffmpeg_events(
                spec, name=CHECK_BLACK, input_path=input_path, exe=exe, params=params,
                duration=duration, timeout_sec=timeout_sec,
                build_filter=lambda p: ("-vf", "blackdetect=d={d}:pix_th={th}:"
                                             "picture_black_ratio_th={r}".format(
                                                 d=p["min_duration_sec"], th=p["pixel_black_th"],
                                                 r=p["picture_black_ratio_th"]), "-an")))
        elif name == CHECK_FREEZE:
            checks.append(_check_ffmpeg_events(
                spec, name=CHECK_FREEZE, input_path=input_path, exe=exe, params=params,
                duration=duration, timeout_sec=timeout_sec,
                build_filter=lambda p: ("-vf", "freezedetect=n={n}dB:d={d}".format(
                    n=p["noise_tolerance_db"], d=p["min_duration_sec"]), "-an")))
        elif name == CHECK_SILENCE:
            checks.append(_check_silence(spec, input_path=input_path, exe=exe, params=params,
                                         duration=duration, probe=probe,
                                         timeout_sec=timeout_sec))
        elif name == CHECK_CUTS:
            checks.append(_check_cuts(spec, input_path=input_path, params=params,
                                      work_dir=work_dir, timeout_sec=cuts_timeout_sec,
                                      tool_versions=tool_versions))
    _assign_event_ids(checks)
    if evidence_frames and work_dir is not None:
        _attach_evidence_frames(checks, input_path=input_path, exe=exe, work_dir=work_dir,
                                duration=duration, limit=max_evidence_frames,
                                timeout_sec=timeout_sec)
    return checks, tool_versions


def python_version() -> str:
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _check_decode(spec: dict, *, input_path, exe, timeout_sec: float) -> dict:
    check = _check_record(CHECK_DECODE)
    build = tool_build("ffmpeg")
    if not exe:
        return _unavailable_check(check, "tool_missing", "找不到 ffmpeg")
    for cand in decode_evidence_candidates(input_path, spec):
        evidence = read_decode_evidence(cand)
        if evidence is None:
            continue
        ok, reason = _reusable_decode_evidence(evidence, spec, build)
        if ok:
            check.update({"status": STATUS_COMPLETED, "evidence_source": "reused",
                          "evidence_path": evidence["_path"],
                          "evidence_sha256": evidence["_sha256"],
                          "argv": evidence.get("argv"),
                          "returncode": evidence.get("returncode"),
                          "stderr_digest": evidence.get("stderr_digest"),
                          "ffmpeg_build": evidence.get("ffmpeg_build")})
            return check
        check["notes"].append({"code": "decode_evidence_not_reused", "path": evidence["_path"],
                               "reason": reason})
    # 独立解码（不假定历史记录存在）
    argv = [exe, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-i", str(input_path),
            "-f", "null", "-"]
    run = run_argv(argv, timeout_sec=timeout_sec)
    check.update({"evidence_source": "independent", "argv": argv,
                  "returncode": run.returncode, "stderr_digest": sha256_text(run.stderr),
                  "ffmpeg_build": build.get("version_line"), "duration_sec": run.duration_sec})
    if run.timed_out:
        return _fail_check(check, "decode_timeout", f"decode 超时（>{timeout_sec}s）")
    if run.error:
        return _fail_check(check, "decode_spawn_failed", run.error)
    if run.returncode != 0:
        return _fail_check(check, "decode_failed", f"ffmpeg 解码返回码 {run.returncode}",
                           stderr_tail=run.stderr[-800:])
    check["status"] = STATUS_COMPLETED
    return check


def _check_ffmpeg_events(spec: dict, *, name, input_path, exe, params, duration, timeout_sec,
                         build_filter) -> dict:
    check = _check_record(name)
    if not exe:
        return _unavailable_check(check, "tool_missing", "找不到 ffmpeg")
    p = params.get(name) or {}
    filt, value, extra = build_filter(p)
    argv = [*_ffmpeg_prefix(exe), str(input_path), filt, value, extra, "-f", "null", "-"]
    run = run_argv(argv, timeout_sec=timeout_sec)
    check.update({"argv": argv, "returncode": run.returncode,
                  "stderr_digest": sha256_text(run.stderr),
                  "detector_parameters": p, "ffmpeg_build": tool_build("ffmpeg").get("version_line")})
    if run.timed_out:
        return _fail_check(check, "ffmpeg_timeout", f"{name} 检测超时（>{timeout_sec}s）")
    if run.error:
        return _fail_check(check, "ffmpeg_spawn_failed", run.error)
    if run.returncode != 0:
        return _fail_check(check, "ffmpeg_returncode",
                           f"{name} 检测返回码 {run.returncode}", stderr_tail=run.stderr[-800:])
    if name == CHECK_BLACK:
        events, problems = parse_blackdetect(run.stderr)
    else:
        events, problems = parse_freezedetect(run.stderr, duration_sec=duration)
    check["events"] = events
    check["problems"] = problems
    if problems:  # §B2：parser 异常保留原始输出，不生成"零事件成功报告"
        return _fail_check(check, "stderr_parse_failed",
                           f"{name} 的 stderr 有 {len(problems)} 行无法解析",
                           raw_lines=[x["raw_line"] for x in problems],
                           stderr_tail=run.stderr[-800:])
    check["status"] = STATUS_COMPLETED
    return check


def _check_silence(spec: dict, *, input_path, exe, params, duration, probe, timeout_sec) -> dict:
    check = _check_record(CHECK_SILENCE)
    if not exe:
        return _unavailable_check(check, "tool_missing", "找不到 ffmpeg")
    probe = probe or {}
    if probe.get("status") == "completed" and probe.get("audio") is None:
        # 没有音轨：不是"检测到静音"，也不是失败——如实记覆盖范围，事件为空
        check.update({"status": STATUS_COMPLETED, "coverage": "no_audio_stream",
                      "events": [], "notes": [{"code": "no_audio_stream",
                                               "message": "输入无音轨，静音检测没有可检对象"}]})
        return check
    p = params.get(CHECK_SILENCE) or {}
    argv = [*_ffmpeg_prefix(exe), str(input_path), "-vn", "-af",
            "silencedetect=n={n}dB:d={d}".format(n=p["noise_tolerance_db"],
                                                 d=p["min_duration_sec"]),
            "-f", "null", "-"]
    run = run_argv(argv, timeout_sec=timeout_sec)
    check.update({"argv": argv, "returncode": run.returncode,
                  "stderr_digest": sha256_text(run.stderr), "detector_parameters": p,
                  "ffmpeg_build": tool_build("ffmpeg").get("version_line")})
    if run.timed_out:
        return _fail_check(check, "ffmpeg_timeout", f"silence 检测超时（>{timeout_sec}s）")
    if run.error:
        return _fail_check(check, "ffmpeg_spawn_failed", run.error)
    if run.returncode != 0:
        return _fail_check(check, "ffmpeg_returncode", f"silence 检测返回码 {run.returncode}",
                           stderr_tail=run.stderr[-800:])
    events, problems = parse_silencedetect(run.stderr, duration_sec=duration)
    check["events"] = events
    check["problems"] = problems
    if problems:
        return _fail_check(check, "stderr_parse_failed",
                           f"silence 的 stderr 有 {len(problems)} 行无法解析",
                           raw_lines=[x["raw_line"] for x in problems],
                           stderr_tail=run.stderr[-800:])
    check["status"] = STATUS_COMPLETED
    return check


def _check_cuts(spec: dict, *, input_path, params, work_dir, timeout_sec, tool_versions) -> dict:
    check = _check_record(CHECK_CUTS)
    payload = {"video_path": str(Path(input_path).resolve()),
               "detector": dict((params.get(CHECK_CUTS) or {}))}
    try:
        from tools.enhancement_workers import protocol as worker_protocol
    except Exception:  # noqa: BLE001 - 走 scripts 相对路径的兼容分支
        try:
            import sys
            tools_dir = Path(__file__).resolve().parent.parent / "tools"
            if str(tools_dir) not in sys.path:
                sys.path.insert(0, str(tools_dir))
            import enhancement_workers.protocol as worker_protocol  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return _unavailable_check(check, "worker_infra_missing",
                                      f"worker 协议模块不可用: {exc}")
    work = Path(work_dir) / "worker" if work_dir else Path(input_path).parent / ".worker_tmp"
    outcome = worker_protocol.run_worker("media_cuts", payload, work_dir=work,
                                         timeout_sec=timeout_sec)
    check["worker"] = {"status": outcome.status, "tool_exit_code": outcome.tool_exit_code,
                       "runtime": outcome.runtime, "request_path": outcome.request_path,
                       "response_path": outcome.response_path,
                       "request_sha256": outcome.request_sha256,
                       "stderr_digest": outcome.stderr_digest}
    check["detector_parameters"] = dict((params.get(CHECK_CUTS) or {}))
    if outcome.status == worker_protocol.HOST_OK:
        result = outcome.result or {}
        cuts = []
        for item in result.get("cuts") or []:
            cuts.append({"kind": EVENT_KIND_CUT, "start_sec": item.get("start_sec"),
                         "end_sec": item.get("start_sec"), "raw_score": None,
                         "start_frame": item.get("start_frame")})
        check.update({"status": STATUS_COMPLETED, "events": cuts,
                      "method": result.get("method"),
                      "media": result.get("media"),
                      "tool_versions": result.get("tool_versions"),
                      "detector_parameters_reported": result.get("detector_parameters")})
        tool_versions["scenedetect"] = {"version": (result.get("tool_versions") or {}).get(
            "scenedetect"), "worker_runtime": outcome.runtime}
        return check
    if outcome.status == worker_protocol.HOST_UNAVAILABLE:
        err = outcome.error or {}
        return _unavailable_check(check, err.get("code") or "worker_unavailable",
                                  err.get("message") or "worker 不可用",
                                  detail=err.get("detail"), runtime=outcome.runtime)
    err = outcome.error or {}
    return _fail_check(check, err.get("code") or "worker_failed",
                       err.get("message") or "worker 调用失败",
                       detail=err.get("detail"), tool_exit_code=outcome.tool_exit_code,
                       stderr_tail=outcome.stderr_tail)


# ---------------------------------------------------------------- 帧证据

def _attach_evidence_frames(checks, *, input_path, exe, work_dir, duration, limit, timeout_sec):
    if not exe:
        return
    out_dir = Path(work_dir) / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    budget = int(limit)
    for check in checks:
        if check.get("status") != STATUS_COMPLETED:
            continue
        for event in check.get("events") or []:
            if event["kind"] not in VIDEO_EVENT_KINDS or budget <= 0:
                continue
            frames = []
            wants = [("start", event.get("start_sec"))]
            end = event.get("end_sec")
            if _is_number(end) and _is_number(duration) and float(end) < float(duration) - 0.05:
                wants.append(("end", end))
            for role, t_sec in wants:
                if budget <= 0 or not _is_number(t_sec):
                    break
                budget -= 1
                target = out_dir / f"{event.get('event_id', 'evt')}_{role}.png"
                argv = [exe, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
                        "-ss", f"{float(t_sec):.3f}", "-i", str(input_path),
                        "-frames:v", "1", str(target)]
                run = run_argv(argv, timeout_sec=timeout_sec)
                if run.ok and target.is_file():
                    frames.append({"role": role, "t_sec": float(t_sec), "path": str(target.resolve()),
                                   "sha256": sha256_file(target), "status": "completed",
                                   "argv": argv, "returncode": run.returncode})
                else:
                    frames.append({"role": role, "t_sec": float(t_sec), "path": str(target),
                                   "status": "failed",
                                   "error": run.error or f"returncode={run.returncode}",
                                   "stderr_digest": sha256_text(run.stderr)})
                    check.setdefault("problems", []).append(
                        {"code": "evidence_frame_failed", "role": role,
                         "t_sec": float(t_sec),
                         "error": run.error or f"returncode={run.returncode}"})
            event["evidence_frames"] = frames
    return checks


# ---------------------------------------------------------------- 分类与汇总

def _kind_of_interval(interval: dict) -> str | None:
    kind = interval.get("kind")
    return str(kind) if kind else None


def interval_covers_kind(interval_kind, event_kind) -> bool:
    """允许区间 kind 是否覆盖该事件 kind（同义适配见 :data:`INTERVAL_KIND_EVENT_KINDS`）."""
    if interval_kind == event_kind:
        return True
    return event_kind in INTERVAL_KIND_EVENT_KINDS.get(str(interval_kind), ())


def _coverage_tolerance(params: dict, fps) -> float:
    base = params.get("coverage_tolerance_sec", DEFAULT_DETECTOR_PARAMETERS["coverage_tolerance_sec"])
    try:
        base = float(base)
    except (TypeError, ValueError):
        base = DEFAULT_DETECTOR_PARAMETERS["coverage_tolerance_sec"]
    frame = (1.0 / float(fps)) if _is_number(fps) and float(fps) > 0 else base
    return max(base, min(frame, 0.5))


def classify_events(events: list, spec: dict, *, fps=None) -> list:
    """给每个事件打分类：expected / suspicious / undecided（§B2）."""
    params = spec.get("detector_parameters") or {}
    tol = _coverage_tolerance(params, fps)
    allowed = spec.get("allowed_intervals") or []
    prohibited = {str(x.get("kind")) if isinstance(x, dict) else str(x)
                  for x in ((spec.get("source_declarations") or {}).get("prohibited_kinds") or [])}
    for event in events:
        kind, start, end = event.get("kind"), event.get("start_sec"), event.get("end_sec")
        matched = []
        covered = False
        crossed = False
        if _is_number(start) and (_is_number(end) or end is None):
            lo = float(start)
            hi = float(end) if _is_number(end) else float(start)
            for interval in allowed:
                if not interval_covers_kind(_kind_of_interval(interval), kind):
                    continue
                i_start, i_end = float(interval["start_sec"]), float(interval["end_sec"])
                if lo >= i_start - tol and hi <= i_end + tol:
                    covered = True
                    matched.append(interval.get("source_locator") or interval.get("id")
                                   or f"{kind}:{i_start}-{i_end}")
                elif lo < i_end + tol and hi > i_start - tol:
                    crossed = True
        if covered:
            event["classification"] = CLASS_EXPECTED
        elif kind in prohibited:
            event["classification"] = CLASS_SUSPICIOUS
        else:
            event["classification"] = CLASS_UNDECIDED
        event["matched_allowed_ids"] = matched
        if crossed:
            event.setdefault("notes", []).append("boundary_cross")
    return events


def report_status(checks: list) -> str:
    """§B1：至少一个 failed ⇒ partial；全 unavailable ⇒ unverified；全 completed ⇒ complete."""
    statuses = [c.get("status") for c in checks]
    if not statuses:
        return REPORT_UNVERIFIED
    if any(s == STATUS_FAILED for s in statuses):
        return REPORT_PARTIAL
    if all(s == STATUS_UNAVAILABLE for s in statuses):
        return REPORT_UNVERIFIED
    if all(s == STATUS_COMPLETED for s in statuses):
        return REPORT_COMPLETE
    # completed + unavailable 混合：覆盖不完整，不能报 complete
    return REPORT_PARTIAL


def build_report(spec: dict, checks: list, tool_versions: dict, *, input_path, probe: dict,
                 notes=None) -> dict:
    events: list[dict] = []
    for check in checks:
        for event in check.get("events") or []:
            item = dict(event)
            item["check"] = check.get("name")
            events.append(item)
    for i, event in enumerate(events, start=1):
        event.setdefault("event_id", f"{event.get('kind', 'event')}-{i:03d}")
        event.setdefault("evidence_frames", [])
        event.setdefault("raw_score", None)
    classify_events(events, spec, fps=((probe or {}).get("video") or {}).get("fps"))
    checks_public = []
    for check in checks:
        public = {k: v for k, v in check.items() if k != "events"}
        public["event_count"] = len(check.get("events") or [])
        public["problem_count"] = len(check.get("problems") or [])
        checks_public.append(public)
    notes = list(notes or [])
    if not events and any(c.get("status") == STATUS_COMPLETED for c in checks):
        notes.append("checks_completed_with_zero_events")
    report = {
        "schema_version": REPORT_SCHEMA,
        "source": {
            "input_path": str(Path(input_path).resolve()),
            "input_sha256": spec.get("input_sha256"),
            "source_identity": spec.get("source_identity"),
            "source_revision": spec.get("source_revision"),
            "input_kind": spec.get("input_kind"),
            "spec_path": spec.get("spec_path"),
            "spec_sha256": (sha256_file(spec["spec_path"]) if spec.get("spec_path")
                            and Path(spec["spec_path"]).is_file() else None),
            "source_manifest_path": spec.get("source_manifest_path"),
            "input_media": probe,
        },
        "tool_versions": tool_versions,
        "parameters": {"detector_parameters": spec.get("detector_parameters"),
                       "checks_requested": list(spec.get("checks") or []),
                       "coverage_tolerance_sec": (spec.get("detector_parameters") or {}).get(
                           "coverage_tolerance_sec")},
        "allowed_intervals": list(spec.get("allowed_intervals") or []),
        "source_declarations": spec.get("source_declarations") or {},
        "checks": checks_public,
        "events": events,
        "report_status": report_status(checks),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": notes,
    }
    return report


def inspect(spec, *, input_path=None, work_dir=None, evidence_frames=True,
            max_evidence_frames: int = DEFAULT_MAX_EVIDENCE_FRAMES,
            timeout_sec: float = DEFAULT_TIMEOUT_SEC,
            cuts_timeout_sec: float = DEFAULT_CUTS_TIMEOUT_SEC) -> dict:
    """执行一次完整检查（**只读输入**；仅在 ``work_dir`` 下写证据帧与 worker 临时文件）."""
    problems = validate_spec(spec)
    if problems:
        raise EnhancementError("spec_invalid", "inspect", f"spec 结构不合法（{len(problems)} 项）",
                               exit_code=EXIT_INVALID_INPUT, field_path="spec",
                               residual_issues=problems)
    path = Path(input_path if input_path is not None else spec.get("input_path") or "")
    if not path.is_file():
        raise EnhancementError("input_missing", "inspect", f"输入媒体不存在: {path}",
                               exit_code=EXIT_INVALID_INPUT, field_path="input")
    actual = sha256_file(path)
    if spec.get("input_sha256") and actual != spec.get("input_sha256"):
        raise EnhancementError(
            "input_sha256_mismatch", "inspect",
            "输入与 spec 绑定的 input_sha256 不一致——身份不符，拒收（不猜是哪一版）",
            exit_code=EXIT_INVALID_INPUT, field_path="input_sha256",
            residual_issues=[{"code": "input_sha256_mismatch", "field_path": "input_sha256",
                              "expected": spec.get("input_sha256"), "actual": actual}])
    probe = probe_media(path)
    checks, tool_versions = run_checks(spec, input_path=path, work_dir=work_dir,
                                       evidence_frames=evidence_frames,
                                       max_evidence_frames=max_evidence_frames,
                                       timeout_sec=timeout_sec, cuts_timeout_sec=cuts_timeout_sec,
                                       probe=probe)
    notes = []
    if probe.get("status") != "completed":
        notes.append("probe_failed: " + str((probe.get("error") or {}).get("code")))
    return build_report(spec, checks, tool_versions, input_path=path, probe=probe, notes=notes)


def exit_code_for(report: dict) -> int:
    """enhancement 退出码：报告写完但工具不可用/失败 ⇒ 3；报告完整 ⇒ 0（§F3）."""
    return EXIT_TOOL_FAIL if report.get("report_status") != REPORT_COMPLETE else 0


__all__ = [name for name in dir() if not name.startswith("_")]
