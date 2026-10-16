"""shared.motion_render.overlay - 动效产物给 Step04 的**唯一接线契约**（计划 §A2/§A4/§15）。

Step04（S 包）不解析 MotionBundle，也不猜身份；它只消费这里产出的
``motion_overlay_plan.v1``。本模块是**唯一**做以下换算的地方：

1. **局部时间 → 绝对时间**：``abs = shot_window.start_sec + overlay.start_sec``；
   边界不得越出该镜窗口（§A4）。
2. **身份**：``shot_index`` 只是**定位映射**，稳定身份是 ``shot_id``；
   ``shot_index=0`` 是 **hook 的独立身份**，不能当第一正文镜头（§15「overlay编号」）。
3. **顺序**：同镜多层按 ``overlays`` 列表 **底 → 顶**（layer_index 递增即越靠上）。
4. **守卫**：跨镜最终区间重叠拒收；map 只能导出隔离单片；fixture 不进行生产叠加；
   跨 revision 必须由代码按**明确 ID map** 重算，无唯一对应则拒收。

给 Step04 的接线要求（Python 侧只提供计划与滤镜片段，不改 step04 源码）：

* FFmpeg 输入次序固定 ``clips → TTS → 旁白 → PNG 序列``，PNG 输入**逐个 overlay**
  追加，且输入下标由同一份 ``layers`` 列表派生（不手抄递增数字）；
* 叠加滤镜显式 ``alpha=straight``（§15「RGBA与字体」），透明区保留底图；
* PNG 按**实际 fps 与最终起点**定位，结束后不重复最后一帧；
* 绘制区必须避开 ``subtitle_exclusion_rect``；模板无音轨，不引入新的声音源。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from shared.media_contract import (MERGE_FPS, quantize_frame_count,
                                   quantized_duration_sec)

from .render import FRAME_PATTERN
from .schema import (
    pointer_child,
    OVERLAY_TEMPLATES,
    SCHEMA_MOTION_OVERLAY_PLAN,
    SOURCE_KIND_ACCEPTED_SCRIPT,
    SOURCE_KIND_FIXTURE,
    TEMPLATE_MAP,
    pointer_join,
    problem,
)
from .validate import (AXIS_OVERLAYS, AXIS_REFS, AXIS_TIME,
                        OVERLAY_DURATION_TOLERANCE_FRAMES)

#: 绝对时间比较容差（一帧）。
_EPS = 1.0 / float(MERGE_FPS)


@dataclass
class OverlayPlanReport:
    problems: list[dict] = field(default_factory=list)
    plan: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {"ok": self.ok, "problems": list(self.problems), "plan": self.plan}


def _rect_overlap(a: Sequence[int], b: Sequence[int]) -> bool:
    ax, ay, aw, ah = (int(v) for v in a)
    bx, by, bw, bh = (int(v) for v in b)
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def build_overlay_plan(bundle: Mapping[str, Any], *, shot_windows: Mapping[Any, Mapping[str, Any]],
                       script_revision: str | None = None,
                       shot_index_map: Mapping[Any, Any] | None = None,
                       rendered: Mapping[str, Mapping[str, Any]] | None = None,
                       allow_fixture: bool = False,
                       bundle_revision: str | None = None) -> OverlayPlanReport:
    """把 bundle 的 overlays 解析成 Step04 可直接消费的绝对时间层表。

    Args:
        bundle: 已通过 ``validate_motion_bundle`` 的 MotionBundle v1。
        shot_windows: 镜头窗表 ``shot_index -> {start_sec, end_sec, shot_id}``
            （由 S 的 MergeTimeline 提供；**身份取自这里，不猜**）。
        script_revision: 时间线所属 revision；与 bundle 的 ``source.script_revision``
            不一致时必须有 ``shot_index_map``，否则拒收。
        shot_index_map: 明确的重编号映射 ``旧 shot_index -> 新 shot_index``。
        rendered: ``spec_id -> {"frames_dir":..., "frame_count":...}``（渲染产物索引）。
        allow_fixture: 仅隔离样片/测试为 True；生产叠加必须 False。
        bundle_revision: overlay 所依据的 revision。``accepted_script`` 从
            ``source.script_revision`` 取；``fixture`` 来源按 §A2 **不带该字段**，
            调用方必须显式给出（拿不到就无法做跨版本判据 ⇒ fail-closed 拒收）。
    """
    report = OverlayPlanReport()
    source = bundle.get("source") or {}
    kind = str(source.get("kind"))
    canvas = bundle.get("canvas") or {}
    overlays = list(bundle.get("overlays") or [])
    specs_by_id = {str(s.get("spec_id")): s for s in (bundle.get("specs") or [])
                   if isinstance(s, Mapping)}

    if kind != SOURCE_KIND_ACCEPTED_SCRIPT and not allow_fixture:
        report.problems.append(problem(
            "fixture_source_not_production", AXIS_OVERLAYS, "/source/kind",
            "fixture 来源只能用于隔离样片，不能进入生产叠加（§A2）", source_kind=kind))
    if not overlays:
        report.problems.append(problem(
            "empty_overlay_plan", AXIS_OVERLAYS, "/overlays",
            "消费 Step04 时 overlays 必须非空（纯样片允许为空，但不能进入叠加）"))
        return report

    declared_revision = str(source.get("script_revision") or bundle_revision or "")
    if script_revision is not None:
        if not declared_revision:
            report.problems.append(problem(
                "shot_index_revision_unknown", AXIS_OVERLAYS, "/source/script_revision",
                "无法确定 overlay 所依据的 revision（fixture 来源不带该字段）："
                "调用方必须显式给出 bundle_revision，否则跨版本判据不可证",
                timeline_revision=str(script_revision)))
            return report
        if str(script_revision) != declared_revision and not shot_index_map:
            report.problems.append(problem(
                "shot_index_revision_mismatch", AXIS_OVERLAYS, "/source/script_revision",
                "overlay 的 shot_index 属于 bundle 的 script_revision；跨版本必须由代码按明确 "
                "ID map 重算，无唯一对应即拒收",
                bundle_revision=declared_revision, timeline_revision=str(script_revision)))
            return report

    # 窗表键归一：S 从 JSON 拿到的往往是字符串键（"0"/"1"），身份是同一个
    windows: dict[int, Mapping[str, Any]] = {}
    for raw_key, value in (shot_windows or {}).items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            report.problems.append(problem(
                "shot_window_key_invalid", AXIS_OVERLAYS, "/overlays",
                f"时间线窗表含非整数镜头键: {raw_key!r}", actual=str(raw_key)))
            continue
        if isinstance(value, Mapping):
            windows[key] = value
    if report.problems:
        return report

    layers: list[dict] = []
    isolated: list[dict] = []
    intervals: list[tuple[float, float, str, str]] = []
    warnings: list[dict] = []
    for i, ov in enumerate(overlays):
        path = pointer_join("overlays", i)
        sid = str(ov.get("spec_id"))
        spec = specs_by_id.get(sid)
        if spec is None:
            report.problems.append(problem("dangling_reference", AXIS_REFS,
                                           pointer_child(path, "spec_id"),
                                           f"overlay 指向不存在的 spec: {sid!r}", spec_id=sid))
            continue
        template = str(spec.get("template"))
        if template == TEMPLATE_MAP:
            report.problems.append(problem(
                "map_not_overlayable", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                "map 模板只能导出隔离单片（§A2），禁止进入 Step04 overlays", spec_id=sid))
            continue
        if template not in OVERLAY_TEMPLATES:
            report.problems.append(problem(
                "template_not_overlayable", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                f"template {template!r} 不可叠加（仅 {list(OVERLAY_TEMPLATES)}）", spec_id=sid))
            continue

        raw_index = int(ov["shot_index"])
        shot_index = raw_index
        remapped_from = None
        if shot_index_map and raw_index in {int(k) for k in shot_index_map}:
            shot_index = int({int(k): int(v) for k, v in shot_index_map.items()}[raw_index])
            remapped_from = raw_index
        window = windows.get(shot_index)
        if window is None:
            report.problems.append(problem(
                "shot_window_missing", AXIS_OVERLAYS, pointer_child(path, "shot_index"),
                f"镜头 {shot_index} 不在本次时间线窗表内（越界/跨 revision）",
                shot_index=shot_index, known=sorted(windows)))
            continue
        w_start = float(window["start_sec"])
        w_end = float(window["end_sec"])
        shot_id = str(window.get("shot_id") or "")
        if not shot_id:
            report.problems.append(problem(
                "shot_id_missing", AXIS_OVERLAYS, pointer_child(path, "shot_index"),
                "时间线未给出稳定 shot_id（身份必须由生产者给出，不能按数组位置猜）",
                shot_index=shot_index))
            continue
        if shot_index == 0 and not window.get("is_hook"):
            report.problems.append(problem(
                "hook_identity_conflict", AXIS_OVERLAYS, pointer_child(path, "shot_index"),
                "shot_index=0 是 hook 的独立身份，不能当第一正文镜头（§15「overlay编号」）",
                shot_index=0, shot_id=shot_id))
            continue

        abs_start = w_start + float(ov["start_sec"])
        abs_end = w_start + float(ov["end_sec"])
        if abs_start < w_start - _EPS or abs_end > w_end + _EPS:
            report.problems.append(problem(
                "overlay_exceeds_shot_window", AXIS_TIME, path,
                f"overlay 绝对窗 [{abs_start:.4f}, {abs_end:.4f}) 超出镜头窗 "
                f"[{w_start:.4f}, {w_end:.4f})",
                overlay_id=str(ov["overlay_id"]), shot_index=shot_index, shot_id=shot_id))
            continue
        try:
            frame_count = quantize_frame_count(float(spec["duration_sec"]), MERGE_FPS)
            decl_frames = quantize_frame_count(float(ov["end_sec"]) - float(ov["start_sec"]),
                                               MERGE_FPS)
        except Exception as exc:
            report.problems.append(problem(
                "bad_overlay_duration", AXIS_TIME, path, f"overlay 时长无法量化: {exc}",
                overlay_id=str(ov["overlay_id"])))
            continue
        if abs(decl_frames - frame_count) > 1:
            report.problems.append(problem(
                "overlay_duration_mismatch", AXIS_OVERLAYS, path,
                f"overlay 声明帧数 {decl_frames} 与 spec 量化帧数 {frame_count} 相差超过 1 帧",
                overlay_id=str(ov["overlay_id"]), spec_id=sid,
                declared_frames=decl_frames, spec_frames=frame_count))
            continue
        if canvas and _rect_overlap(spec["rect"], canvas["subtitle_exclusion_rect"]):
            report.problems.append(problem(
                "rect_overlaps_subtitle_exclusion", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                "绘制区与 subtitle_exclusion_rect 重叠（Step04 接线要求避开字幕区）",
                spec_id=sid, subtitle_exclusion_rect=list(canvas["subtitle_exclusion_rect"])))

        frames_dir = None
        if rendered is not None:
            entry = rendered.get(sid) or {}
            frames_dir = entry.get("frames_dir")
            if not frames_dir or not Path(frames_dir).is_dir():
                report.problems.append(problem(
                    "rendered_frames_missing", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                    f"spec {sid!r} 的帧序列缺失（先 render 再叠加，不猜路径）",
                    spec_id=sid, frames_dir=frames_dir))
                continue
            actual = entry.get("frame_count")
            if actual is not None and int(actual) != frame_count:
                report.problems.append(problem(
                    "frame_count_mismatch", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                    f"帧数 {actual} != overlay 量化帧数 {frame_count}",
                    spec_id=sid, actual=int(actual), expected=frame_count))
                continue

        intervals.append((abs_start, abs_end, str(ov["overlay_id"]), shot_id))
        layers.append({
            "overlay_id": str(ov["overlay_id"]),
            "spec_id": sid,
            "template": template,
            "shot_index": shot_index,
            "shot_id": shot_id,
            "remapped_from_index": remapped_from,
            "local_start_sec": float(ov["start_sec"]),
            "local_end_sec": float(ov["end_sec"]),
            "abs_start_sec": abs_start,
            "abs_end_sec": abs_end,
            "frame_count": frame_count,
            "declared_overlay_frames": decl_frames,
            "fps": MERGE_FPS,
            "rect": [int(v) for v in spec["rect"]],
            "frames_dir": str(frames_dir) if frames_dir else None,
            "frame_pattern": FRAME_PATTERN,
            "layer_index": len(layers),
        })

    # 跨镜最终区间重叠（跨 revision / 撞窗）拒收；同镜多层允许（底→顶）
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            a0, a1, aid, ashot = intervals[i]
            b0, b1, bid, bshot = intervals[j]
            if ashot == bshot:
                continue
            if a0 < b1 - _EPS and b0 < a1 - _EPS:
                report.problems.append(problem(
                    "cross_shot_overlay_overlap", AXIS_TIME, "/overlays",
                    f"跨镜 overlay 最终区间重叠：{aid} [{a0:.4f},{a1:.4f}) 与 "
                    f"{bid} [{b0:.4f},{b1:.4f})",
                    overlay_ids=[aid, bid], shot_ids=[ashot, bshot]))

    for sid, spec in specs_by_id.items():
        if any(l["spec_id"] == sid for l in layers):
            continue
        isolated.append({
            "spec_id": sid,
            "template": str(spec.get("template")),
            "reason": "map_isolated" if str(spec.get("template")) == TEMPLATE_MAP
            else "unbound_spec",
        })
    if isolated:
        warnings.append(problem(
            "isolated_specs", AXIS_OVERLAYS, "/specs",
            f"{len(isolated)} 个 spec 不进入叠加（map 只能单片导出 / 未绑定 overlay）",
            isolated=isolated))

    if report.problems:
        return report

    plan = {
        "schema_version": SCHEMA_MOTION_OVERLAY_PLAN,
        "source_kind": kind,
        "production_admission": kind == SOURCE_KIND_ACCEPTED_SCRIPT,
        "experiment_id": str(bundle.get("experiment_id") or ""),
        "script_revision": declared_revision,
        "canvas": {
            "width": int(canvas.get("width", 0)),
            "height": int(canvas.get("height", 0)),
            "fps": int(canvas.get("fps", MERGE_FPS)),
            "safe_rect": [int(v) for v in canvas.get("safe_rect", [])],
            "subtitle_exclusion_rect": [int(v) for v in canvas.get("subtitle_exclusion_rect", [])],
        },
        "layers": layers,
        "isolated": isolated,
        "warnings": warnings,
        # 给 Step04 的明示接线要求（不靠读源码猜）
        "ffmpeg": {
            "input_order": ["clips", "tts", "narration", "png_sequences"],
            "png_inputs_in_layer_order": True,
            "alpha": "straight",
            "alpha_literal": "overlay=format=auto:alpha=straight",
            "no_audio": True,
            "position_by": "abs_start_sec + frame_index / fps",
        },
    }
    report.plan = plan
    return report


# ---------------------------------------------------------------- Step04 消费的计划文件
#
# **S 包已固定的接口**（2026-10-03 与 S 对齐）：motion_overlay_plan.v1 只有五个顶层键，
# overlays 每条给**镜头局部**时间，绝对时间由 S 用 `window.start + 局部时间` 平移：
#
#   {"schema_version":"motion_overlay_plan.v1", "scope": {...}, "script_revision": "...",
#    "fps": 24,
#    "overlays":[{"overlay_id","spec_id","shot_index","kind":"image"|"png_sequence",
#                 "path":"<单帧 PNG 或 %05d 序列路径>","start_sec","end_sec","layer"}]}
#
# 本模块只**产出这个文件**：平移、越界拒收、跨镜重叠由 S 负责；`shot_index` 用
# source.script_revision 的编号域（0 只表示 hook 独立身份），不引入第二套身份。

KIND_IMAGE = "image"
KIND_PNG_SEQUENCE = "png_sequence"
OVERLAY_PLAN_KEYS = ("schema_version", "scope", "script_revision", "fps", "overlays")
OVERLAY_ENTRY_KEYS = ("overlay_id", "spec_id", "shot_index", "kind", "path",
                      "start_sec", "end_sec", "layer")


def derive_scope(bundle: Mapping[str, Any]) -> dict:
    """默认 scope（调用方没显式给时）：来源身份 + 实验 id。

    scope 必须与 S 的时间线 scope 相同；生产路径请用 ``--scope-file`` 传 S 的那一份。
    """
    src = bundle.get("source") or {}
    scope: dict[str, Any] = {"kind": str(src.get("kind") or ""),
                             "experiment_id": str(bundle.get("experiment_id") or "")}
    for key in ("fixture_id", "novel", "book", "chapter", "script_revision"):
        if src.get(key) is not None:
            scope[key] = src[key]
    return scope


def build_overlay_plan_file(bundle: Mapping[str, Any], *, scope: Mapping[str, Any],
                            script_revision: str, rendered: Mapping[str, Mapping[str, Any]],
                            mode: str = "animated",
                            default_mode: str | None = None) -> OverlayPlanReport:
    """产出 S 消费的 ``motion_overlay_plan.v1``（局部时间 + image/png_sequence 两种 kind）。

    Args:
        scope: 必须与 S 时间线的 scope 相同（本函数不比对，由 S 消费时校验）。
        script_revision: overlay 编号所属的 revision（0=hook 的编号域）。
        rendered: ``spec_id -> {"frames_dir", "frame_count"}``（已完成校验的帧序列）。
        mode: ``animated`` → ``png_sequence``（``frame_%05d.png``）；
              ``static`` → ``image``（``frame_00000.png`` 单帧）。
    """
    report = OverlayPlanReport()
    specs = {str(s.get("spec_id")): s for s in (bundle.get("specs") or [])
             if isinstance(s, Mapping)}
    overlays = [o for o in (bundle.get("overlays") or []) if isinstance(o, Mapping)]
    kind = KIND_PNG_SEQUENCE if mode == "animated" else KIND_IMAGE
    entries: list[dict] = []
    for i, ov in enumerate(overlays):
        sid = str(ov.get("spec_id"))
        spec = specs.get(sid)
        path = pointer_child("overlays", i)
        if spec is None:
            report.problems.append(problem("dangling_reference", AXIS_REFS,
                                           pointer_child(path, "spec_id"),
                                           f"overlay 指向不存在的 spec: {sid!r}", spec_id=sid))
            continue
        if str(spec.get("template")) == TEMPLATE_MAP:
            report.problems.append(problem(
                "map_not_overlayable", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                "map 模板只能导出隔离单片（§A2），禁止写入 Step04 计划", spec_id=sid))
            continue
        entry = rendered.get(sid) or {}
        frames_dir = entry.get("frames_dir")
        if not frames_dir or not Path(frames_dir).is_dir():
            report.problems.append(problem(
                "rendered_frames_missing", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                f"spec {sid!r} 的帧序列缺失（先 render 再产出计划）", spec_id=sid))
            continue
        frame_count = entry.get("frame_count")
        want = quantize_frame_count(float(spec["duration_sec"]), MERGE_FPS)
        if frame_count is not None and int(frame_count) != want:
            report.problems.append(problem(
                "frame_count_mismatch", AXIS_OVERLAYS, pointer_child(path, "spec_id"),
                f"帧数 {frame_count} != spec 量化帧数 {want}", spec_id=sid))
            continue
        start, end = float(ov["start_sec"]), float(ov["end_sec"])
        expected = quantized_duration_sec(float(spec["duration_sec"]), MERGE_FPS)
        if abs((end - start) - expected) > (OVERLAY_DURATION_TOLERANCE_FRAMES / MERGE_FPS) + 1e-9:
            report.problems.append(problem(
                "overlay_duration_mismatch", AXIS_OVERLAYS, path,
                f"overlay 时长 {end - start:.4f}s 必须等于 spec 量化时长 {expected:.4f}s",
                overlay_id=str(ov.get("overlay_id")), spec_id=sid))
            continue
        entries.append({
            "overlay_id": str(ov.get("overlay_id")),
            "spec_id": sid,
            "shot_index": int(ov["shot_index"]),
            "kind": kind,
            "path": str(Path(frames_dir) / ("frame_%05d.png" if kind == KIND_PNG_SEQUENCE
                                            else "frame_00000.png")),
            "start_sec": start,
            "end_sec": end,
            "layer": i,  # 列表顺序即层序：越大越靠上（同镜多层底→顶）
        })
    if report.problems:
        return report
    if not entries:
        report.problems.append(problem(
            "empty_overlay_plan", AXIS_OVERLAYS, "/overlays",
            "bundle 没有可写入 Step04 计划的 overlay（纯样片/map 不产计划文件）"))
        return report
    report.plan = {
        "schema_version": SCHEMA_MOTION_OVERLAY_PLAN,
        "scope": dict(scope),
        "script_revision": str(script_revision),
        "fps": MERGE_FPS,
        "overlays": entries,
    }
    return report


def write_overlay_plan_file(plan: Mapping[str, Any], path: Path) -> Path:
    """写计划文件（UTF-8、缩进 2；调用方负责放进 attempt 的 outputs）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def overlay_filter_fragment(layer: Mapping[str, Any], base_label: str, png_label: str) -> str:
    """单层叠加的滤镜片段（Step04 可选用；``enable`` 用绝对时间窗）.

    ⚠️ 窗口是**半开**的 `[start, end)`（与计划里的帧区间同一口径）——`between(t,S,E)`
    两端都含，会**多显示一帧**（复核第十二轮补查 P2 在真实后端上实测到）。

    ⚠️ 容差是**常数 `ENABLE_EPS`（1e-6 秒）**，不是"半帧"（复核第十二轮二轮 P2）：
    退半帧等于把整个窗口平移，会改变**非帧对齐**窗口的取整规则（实测 `[0.52, 1.02)`
    @24fps 会被错成"第 12 帧就出现、第 24 帧已消失"）。这里要补偿的只是帧时间戳
    `N*(1/fps)` 与边界 `end_frame/fps` 之间的 1 ulp 表示误差。
    与 `step04_merge.backends.ffmpeg` 用**同一个**表达式与同一个容差。
    """
    from step04_merge.backends.ffmpeg import ENABLE_EPS
    start = float(layer["abs_start_sec"])
    end = float(layer["abs_end_sec"])
    return (f"[{base_label}][{png_label}]overlay=format=auto:alpha=straight:"
            f"x={int(layer['rect'][0])}:y={int(layer['rect'][1])}:"
            f"enable='gte(t,{start - ENABLE_EPS:.9f})*lt(t,{end - ENABLE_EPS:.9f})'")


def png_input_args(layer: Mapping[str, Any]) -> list[str]:
    """PNG 序列的 FFmpeg 输入参数（帧率与实际起点由调用方按 plan 决定）。"""
    frames_dir = layer.get("frames_dir")
    if not frames_dir:
        raise ValueError(f"layer {layer.get('overlay_id')!r} 没有已渲染的帧序列")
    return ["-framerate", str(int(layer["fps"])),
            "-start_number", "0",
            "-i", str(Path(frames_dir) / str(layer["frame_pattern"]))]


__all__ = ["build_overlay_plan", "build_overlay_plan_file", "write_overlay_plan_file",
           "derive_scope", "OverlayPlanReport", "overlay_filter_fragment", "png_input_args",
           "KIND_IMAGE", "KIND_PNG_SEQUENCE", "OVERLAY_PLAN_KEYS", "OVERLAY_ENTRY_KEYS"]
