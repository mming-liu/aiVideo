"""tools/motion_lab.py - A 包动效协议 CLI（计划 §A3/§A5/§11）。

四个子命令（**各命令独立成行**，§15「A3三个CLI各单独代码行」）：

    python tools/motion_lab.py validate --bundle FILE --out-dir ROOT/motion
    python tools/motion_lab.py render   --bundle FILE --out-dir ROOT/motion
    python tools/motion_lab.py revise   --request FILE --out-dir ROOT/motion
    python tools/motion_lab.py demo     --out-dir ROOT/motion

事务一律走 ``shared.enhancement_io``（§F2/§F3）：单写者锁 → 冻结输入与素材 →
候选副本 → 完整校验 → 渲染 → 不可变 manifest → accepted 指针两阶段替换与读回。
本文件不实现另一套提交协议。

退出码（§F3，仅适用于本增强 CLI）：0 成功；2 输入/身份非法；3 工具/IO 失败或
渲染底座不可用；4 明确拒收；5 锁忙/base 变化；6 commit_unconfirmed；7 恢复不可用。

``main(argv=None, *, shooter=None)`` 允许测试注入取帧实现（缺 Playwright 时用桩跑通
整条链路；真实浏览器渲染记 not_run，不伪造成功）。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import enhancement_io as eio  # noqa: E402
from shared.motion_render import overlay as overlay_mod  # noqa: E402
from shared.motion_render import render as render_mod  # noqa: E402
from shared.motion_render import schema as schema_mod  # noqa: E402
from shared.motion_render import validate as validate_mod  # noqa: E402
from shared.media_contract import MERGE_FPS  # noqa: E402

PKG = "motion"

#: 轴 → 检查名（报告层只按轴分类，不读文案猜）。
AXIS_TO_CHECK = {
    validate_mod.AXIS_SCHEMA: "schema",
    validate_mod.AXIS_CANVAS: "canvas",
    validate_mod.AXIS_SOURCE: "source",
    validate_mod.AXIS_ASSETS: "assets",
    validate_mod.AXIS_LOCATOR: "source",
    validate_mod.AXIS_SEGMENTS: "time",
    validate_mod.AXIS_TIME: "time",
    validate_mod.AXIS_LAYOUT: "layout",
    validate_mod.AXIS_FONTS: "fonts",
    validate_mod.AXIS_REFS: "references",
    validate_mod.AXIS_OVERLAYS: "references",
    validate_mod.AXIS_MAP: "references",
    "render": "render",
    "encode": "encode",
    "demo": "render",
}

#: 渲染/编码类检查单独计算（它们的"未跑"是 not_run，不是 fail）。
RENDER_AXES = ("render", "encode", "demo")

#: 消费/修订一个动效基线时**必须**已通过的检查（渲染类检查另行按需声明）。
REQUIRED_BASE_CHECKS = ("assets", "canvas", "fonts", "layout", "references",
                        "schema", "source", "time")
ALL_AXES = tuple(sorted(set(AXIS_TO_CHECK)))
ALL_CHECKS = tuple(sorted(set(AXIS_TO_CHECK.values())))


# ---------------------------------------------------------------- 小工具

def _now_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _load_json_strict(path: Path) -> Any:
    return schema_mod.load_json_strict(Path(path).read_text(encoding="utf-8"))


def _problem(code: str, axis: str, path: str, message: str, **detail) -> dict:
    return schema_mod.problem(code, axis, path, message, **detail)


def _tool_versions() -> dict:
    import platform
    node = render_mod.node_binary()
    node_version = None
    if node:
        import subprocess
        try:
            node_version = subprocess.run([node, "--version"], capture_output=True, text=True,
                                          timeout=30, check=False).stdout.strip()
        except Exception:
            node_version = None
    rt = render_mod.runtime_status()
    return {
        "python": platform.python_version(),
        "node": node_version,
        "playwright": rt.get("playwright", {}).get("version"),
        "playwright_pinned": render_mod.PINNED_PLAYWRIGHT,
        "ffmpeg": render_mod.ffmpeg_binary(),
        "merge_fps": MERGE_FPS,
        "template_dir": str(render_mod.template_dir()),
    }


def _render_check(problems: Sequence[Mapping[str, Any]]) -> dict:
    """渲染/编码检查：全 pass→pass；底座缺失→not_run；其它→fail。"""
    items = [p for p in problems if str(p.get("axis")) in RENDER_AXES]
    if not items:
        return {"name": "render", "status": "not_run", "problems": [], "count": 0}
    only_runtime = all(p.get("code") == "render_runtime_unavailable" for p in items)
    return {"name": "render", "status": "not_run" if only_runtime else "fail",
            "problems": [{"code": i.get("code"), "path": i.get("path")} for i in items],
            "count": len(items)}


def _checks_from_problems(problems: Sequence[Mapping[str, Any]]) -> list[dict]:
    """把问题按轴归到检查名；每个检查名给 pass/fail + 该轴的问题摘要。"""
    by_check: dict[str, list[dict]] = {name: [] for name in ALL_CHECKS if name not in ("render", "encode")}
    for p in problems:
        name = AXIS_TO_CHECK.get(str(p.get("axis")), "schema")
        if name in ("render", "encode"):
            continue
        by_check.setdefault(name, []).append(dict(p))
    checks: list[dict] = []
    for name in sorted(by_check):
        items = by_check[name]
        checks.append({
            "name": name,
            "status": "fail" if items else "pass",
            "problems": [{"code": i.get("code"), "path": i.get("path")} for i in items],
            "count": len(items),
        })
    return checks


def _seal_failure(package_root: Path, attempt: Path, *, kind: str, payload: dict,
                  problems: list[dict], status: str = "rejected") -> dict:
    """拒收/失败尝试：写报告 → 封存失败 manifest（不可变）→ **不动 accepted**。"""
    attempt.mkdir(parents=True, exist_ok=True)
    report_path = attempt / "validate_report.json"
    report_path.write_text(json.dumps({"kind": kind, "problems": problems, **payload},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = eio.seal_manifest(attempt, {
        "package": PKG, "kind": kind, "status": status,
        "tool_versions": _tool_versions(),
        "checks": _checks_from_problems(problems) + [_render_check(problems)],
        "problems": problems,
        "artifacts": [eio.artifact_record(report_path, role="report")],
        **payload,
    })
    return manifest


# ---------------------------------------------------------------- 运行上下文

class MotionJob:
    """一次动效作业的上下文（attempt 目录、冻结产物、来源、校验结果）。"""

    def __init__(self, *, package_root: Path, attempt_id: str | None, kind: str):
        self.package_root = Path(package_root)
        self.kind = kind
        self.experiment_id = self.package_root.parent.name
        self.attempt_id = attempt_id or f"{kind}-{_now_stamp()}-{int(time.time_ns() % 100000):05d}"
        self.attempt = self.package_root / eio.ATTEMPTS_DIRNAME / self.attempt_id
        self.lock = eio.AttemptLock(self.package_root / eio.LOCK_DIRNAME, purpose=f"motion:{kind}")

    # -- 生命周期 --
    def __enter__(self) -> "MotionJob":
        self.package_root.mkdir(parents=True, exist_ok=True)
        self.lock.acquire()
        self.attempt.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc) -> bool:
        self.lock.release()
        return False


def _resolve_package_root(out_dir: str | Path, bundle_or_none: Mapping[str, Any] | None,
                          *, kind: str) -> tuple[Path | None, list[dict], str]:
    """``--out-dir`` 必须是 ``<实验根>/motion``（结构身份，不是随手一个目录）。"""
    problems: list[dict] = []
    p = Path(out_dir)
    if not p.is_absolute():
        problems.append(_problem("out_dir_not_absolute", "schema", "",
                                 f"--out-dir 必须是绝对路径: {p}"))
        return None, problems, "unknown"
    if p.name != PKG:
        problems.append(_problem("out_dir_not_package_root", "schema", "",
                                 f"--out-dir 必须是包 root（目录名 'motion'），实际 {p.name!r}"))
        return None, problems, "unknown"
    experiment_id = p.parent.name
    try:
        from shared.paths import validate_experiment_id
        validate_experiment_id(experiment_id)
    except Exception as exc:
        problems.append(_problem("experiment_id_invalid", "source", "",
                                 f"实验根目录名不是合法 experiment_id: {experiment_id!r} ({exc})"))
        return None, problems, "unknown"
    layout = "isolated"
    if bundle_or_none is not None:
        declared = str(bundle_or_none.get("experiment_id") or "")
        if declared and declared != experiment_id:
            problems.append(_problem(
                "experiment_id_mismatch", "source", "/experiment_id",
                f"bundle.experiment_id={declared!r} 与实验根目录名 {experiment_id!r} 不一致",
                declared=declared, actual=experiment_id))
            return None, problems, layout
    try:
        canonical = eio.package_root(PKG, experiment_id)
        if Path(canonical).resolve() == p.resolve():
            layout = "canonical"
    except Exception:
        layout = "isolated"
    return p.resolve(), problems, layout


def _freeze_assets(job: MotionJob, bundle: Mapping[str, Any], asset_root: Path,
                   problems: list[dict]) -> dict[str, Path]:
    """把素材复制进 attempt 并在**冻结副本**上核对摘要（渲染只用冻结副本）。"""
    frozen_root = job.attempt / "frozen"
    frozen_root.mkdir(parents=True, exist_ok=True)
    frozen: dict[str, Path] = {}
    records: dict[str, dict] = {}
    for aid, asset in (bundle.get("assets") or {}).items():
        if not isinstance(asset, Mapping):
            continue
        src = Path(asset_root) / str(asset.get("path") or "")
        if not src.is_file():
            problems.append(_problem("asset_missing", "assets", f"/assets/{aid}/path",
                                     f"素材不存在，无法冻结: {src}"))
            continue
        dst = frozen_root / "assets" / str(aid)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        digest = eio.sha256_file(dst)
        if digest != str(asset.get("sha256")):
            problems.append(_problem("asset_digest_mismatch", "assets", f"/assets/{aid}/sha256",
                                     "冻结副本摘要与声明不符",
                                     expected=str(asset.get("sha256")), actual=digest))
            continue
        frozen[str(aid)] = dst
        records[str(aid)] = {"path": str(dst), "sha256": digest,
                             "declared_path": str(asset.get("path")),
                             "license_source": asset.get("license_source")}
    return frozen, records  # type: ignore[return-value]


def _bundle_input_key(bundle: Mapping[str, Any]) -> str:
    """事实输入摘要（不含任何派生/状态字段——这里就是整份 bundle）。"""
    return eio.canonical_json_digest(bundle)


def _freeze_bundle(job: MotionJob, bundle: Mapping[str, Any], bundle_path: Path) -> tuple[Path, str]:
    frozen_root = job.attempt / "frozen"
    frozen_root.mkdir(parents=True, exist_ok=True)
    target = frozen_root / "bundle.json"
    target.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
                      encoding="utf-8")
    return target, eio.sha256_file(target)


# ---------------------------------------------------------------- 校验与渲染主流程

def _validate(job: MotionJob, bundle: Mapping[str, Any], *, asset_root: Path,
              source_dir: Path | None, measure=None, preloaded=None
              ) -> tuple[validate_mod.ValidationReport, Any, str | None]:
    """结构 + 语义校验。``preloaded`` 允许复用已加载的来源（修订流程先加载一次）。"""
    if preloaded is None:
        source_doc, revision, source_problems = validate_mod.load_bundle_with_sources(
            bundle, asset_root=asset_root, source_dir=source_dir)
    else:
        source_doc, revision, source_problems = preloaded
    report = validate_mod.validate_motion_bundle(
        bundle, asset_root=asset_root, source_doc=source_doc, measure=measure)
    report.problems.extend(source_problems)
    return report, source_doc, revision


def _resolve_scope(args, bundle: Mapping[str, Any]) -> tuple[dict, str]:
    """Step04 计划里的 scope：显式 ``--scope-file`` 优先，否则由来源派生（并如实标注）。"""
    path = getattr(args, "scope_file", None)
    if path:
        return _load_json_strict(Path(path)), "cli"
    return overlay_mod.derive_scope(bundle), "derived"


def _script_revision_for(args, bundle: Mapping[str, Any], source_doc: Any) -> str:
    explicit = getattr(args, "script_revision", None)
    if explicit:
        return str(explicit)
    src = bundle.get("source") or {}
    if src.get("script_revision"):
        return str(src["script_revision"])
    if isinstance(source_doc, Mapping):
        return str(source_doc.get("script_revision") or "")
    return ""


def _rendered_index(report: validate_mod.ValidationReport) -> dict:
    return {str(sid): {"frames_dir": v.get("frames_dir"), "frame_count": v.get("frame_count")}
            for sid, v in (report.facts.get("render_index") or {}).items()}


def _emit_overlay_plan(job: MotionJob, bundle: Mapping[str, Any], report, *, args,
                       source_doc: Any, mode: str = "animated",
                       filename: str = "motion_overlay_plan.json") -> tuple[Path | None, list[dict]]:
    """产出 Step04 消费的 ``motion_overlay_plan.v1``（**镜头局部**时间；平移/越界由 S 负责）。

    没有 overlay 的 bundle（纯样片、map）不产计划文件，调用方如实记 not_applicable。
    """
    if not bundle.get("overlays"):
        return None, []
    scope, _tag = _resolve_scope(args, bundle)
    plan_report = overlay_mod.build_overlay_plan_file(
        bundle, scope=scope, script_revision=_script_revision_for(args, bundle, source_doc),
        rendered=_rendered_index(report), mode=mode)
    if plan_report.problems:
        return None, plan_report.problems
    target = job.attempt / "outputs" / filename
    overlay_mod.write_overlay_plan_file(plan_report.plan, target)
    return target, []


def _run_render_phase(job: MotionJob, bundle: Mapping[str, Any], report: validate_mod.ValidationReport,
                      *, frozen_assets: Mapping[str, Path], shooter, encode_mp4: bool,
                      mode: str = "animated") -> list[dict]:
    """对每个 spec 取帧；返回新问题（渲染/编码类）。``report.facts`` 记录产物索引。"""
    problems: list[dict] = []
    layouts_raw = report.facts.get("layouts") or {}
    index: dict[str, dict] = {}
    for spec in bundle.get("specs") or []:
        sid = str(spec["spec_id"])
        layout_dict = layouts_raw.get(sid)
        if layout_dict is None:
            continue
        layout = validate_mod.layout_spec(spec, bundle["canvas"], axis_path="")
        work_dir = job.attempt / "outputs" / sid
        result = render_mod.render_spec(
            spec, bundle["canvas"], layout, work_dir=work_dir,
            asset_paths={str(a): p for a, p in frozen_assets.items()},
            shooter=shooter, mode=mode, encode_mp4=encode_mp4)
        problems.extend(result.problems)
        index[sid] = result.to_dict()
        if result.status == render_mod.STATUS_PASS and result.frames_dir:
            problems.extend(_record_frame_sequence(result, job.attempt))
    report.facts.setdefault("render_index", {}).update(index)
    return problems


def _record_frame_sequence(result: render_mod.SpecRenderResult, attempt: Path) -> list[dict]:
    """逐帧摘要写进 ``frames_manifest.json``（序列摘要 + 每帧 sha256）。"""
    frames = sorted(Path(result.frames_dir).glob("frame_*.png"))
    records = [{"name": p.name, "sha256": eio.sha256_file(p), "bytes": p.stat().st_size}
               for p in frames]
    seq = hashlib.sha256("".join(r["sha256"] for r in records).encode("ascii")).hexdigest()
    payload = {"schema_version": "motion_frames.v1", "spec_id": result.spec_id,
               "frame_count": len(records), "fps": MERGE_FPS,
               "seek_first": result.seek_times[0] if result.seek_times else None,
               "seek_last": result.seek_times[-1] if result.seek_times else None,
               "sequence_sha256": seq, "frames": records}
    path = Path(result.frames_dir) / "frames_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    problems: list[dict] = []
    if len(records) != result.frame_count:
        problems.append(_problem("frames_manifest_mismatch", "render", "",
                                 f"帧文件数 {len(records)} != 期望 {result.frame_count}",
                                 spec_id=result.spec_id))
    return problems


def _artifacts(specs: Sequence[Mapping[str, Any]], report: validate_mod.ValidationReport | None,
               *, extra: Sequence[Path] = (), plan_artifact: Path | None = None) -> list[dict]:
    arts: list[dict] = []
    for p in extra:
        if Path(p).is_file():
            arts.append(eio.artifact_record(Path(p), role="report"))
    if plan_artifact is not None and Path(plan_artifact).is_file():
        arts.append(eio.artifact_record(Path(plan_artifact), role="overlay_plan",
                                        media={"schema": "motion_overlay_plan.v1"}))
    if report is None:
        return arts
    for spec in specs:
        sid = str(spec.get("spec_id"))
        entry = (report.facts.get("render_index") or {}).get(sid) or {}
        if entry.get("frames_dir"):
            fm = Path(entry["frames_dir"]) / "frames_manifest.json"
            if fm.is_file():
                arts.append(eio.artifact_record(fm, role="frames",
                                                media={"kind": "png_sequence",
                                                       "frame_count": entry.get("frame_count")}))
        if entry.get("mp4_path") and Path(entry["mp4_path"]).is_file():
            arts.append(eio.artifact_record(Path(entry["mp4_path"]), role="display_mp4",
                                            media={"kind": "video", "fps": MERGE_FPS}))
    return arts


def _commit(job: MotionJob, *, kind: str, input_key: str, payload: dict,
            problems: list[dict], report: validate_mod.ValidationReport | None,
            bundle_path: Path, frozen_bundle: Path, frozen_bundle_sha: str,
            asset_records: Mapping[str, dict], overlay_plan: dict | None,
            specs: Sequence[Mapping[str, Any]] = (), extra_artifacts: Sequence[Path] = (),
            plan_artifact: Path | None = None) -> tuple[dict, int]:
    """封存 manifest 并按需提交 accepted；任何失败检查 ⇒ 不提交（accepted 不动）。"""
    checks = _checks_from_problems(problems)
    render_status = "not_run"
    if report is not None:
        index = report.facts.get("render_index") or {}
        statuses = {str(v.get("status")) for v in index.values()}
        if statuses and statuses == {render_mod.STATUS_PASS}:
            render_status = "pass"
        elif render_mod.STATUS_FAIL in statuses:
            render_status = "fail"
        elif render_mod.STATUS_UNAVAILABLE in statuses:
            render_status = "not_run"
    checks.append({"name": "render", "status": render_status,
                   "count": len((report.facts.get("render_index") or {})) if report else 0,
                   "problems": []})
    failed = [c["name"] for c in checks if c["status"] == "fail"]
    status = "accepted" if not failed else "rejected"
    manifest = eio.seal_manifest(job.attempt, {
        "package": PKG, "kind": kind, "status": status,
        "tool_versions": _tool_versions(),
        "checks": checks,
        "problems": problems,
        "artifacts": _artifacts(specs, report, extra=extra_artifacts,
                                plan_artifact=plan_artifact),
        "bundle": {"path": str(bundle_path), "frozen_path": str(frozen_bundle),
                   "frozen_sha256": frozen_bundle_sha},
        "assets": dict(asset_records),
        "overlay_plan": overlay_plan,
        **{k: v for k, v in payload.items() if not k.startswith("_")},
    })
    if failed:
        return manifest, eio.EXIT_REJECTED
    pointer = eio.commit_accepted(job.package_root, attempt_id=job.attempt_id,
                                  manifest_path=job.attempt / eio.MANIFEST_FILENAME,
                                  manifest_sha256=eio.sha256_file(job.attempt / eio.MANIFEST_FILENAME),
                                  input_key=input_key)
    return pointer, eio.EXIT_OK


# ---------------------------------------------------------------- revise：范围与来源守卫

def _locator_key(locator: Mapping[str, Any]) -> str:
    return json.dumps({str(k): locator[k] for k in sorted(locator)}, ensure_ascii=False)


def _group_points(bundle: Mapping[str, Any], leaf_of) -> dict[str, dict]:
    """按来源定位叶把各 spec 的覆盖**折算到叶坐标**（重复显示按 spec 计数）。

    折算到叶坐标后，拆分（切成更细的段）、切片（source_text 只取一段）与重排都不会
    被误判成"文字减少"；真正的删字/瘦身才落在 covered 的差集里。
    """
    groups: dict[str, dict] = {}
    if leaf_of is None:
        return groups
    for spec in bundle.get("specs") or []:
        loc = spec.get("source_locator")
        if not isinstance(loc, Mapping):
            continue
        leaf = leaf_of(loc, str(spec.get("template")))
        if not isinstance(leaf, str):
            continue
        offset = validate_mod.leaf_offset(leaf, str(spec.get("source_text") or ""))
        if offset is None:
            continue  # 定位问题由 validate 报，这里不重复
        g = groups.setdefault(_locator_key(loc), {"leaf": leaf, "covered": set(), "counts": {}})
        for seg in spec.get("segments") or []:
            if not isinstance(seg, Mapping):
                continue
            try:
                cs, ce = int(seg["char_start"]), int(seg["char_end"])
            except Exception:
                continue
            for point in range(offset + cs, offset + ce):
                g["covered"].add(point)
                g["counts"][point] = g["counts"].get(point, 0) + 1
    return groups


def revision_guard_problems(base: Mapping[str, Any], candidate: Mapping[str, Any], *,
                            leaf_of=None) -> list[dict]:
    """§A3「范围及来源守卫」：来源不可变、核心文字不减少、重复显示需有基线旧绑定。

    G1 ``source`` 身份（kind/sha/身份字段）逐字不变 ⇒ 来源不得被换掉；
    G2 每个来源叶的**覆盖并集**不得缩小（拆分成更细的段合法，删字瘦身拒收）；
    G3 同一 code point 被多个 spec 覆盖时，覆盖数不得超过基线
       （新造重复显示 = 把正文计两次，拒收）。
    覆盖一律折算到**叶坐标**，因此拆分/切片/重排不会被误判。
    """
    problems: list[dict] = []
    base_src = base.get("source") or {}
    cand_src = candidate.get("source") or {}
    for key in ("kind", "source_sha256", "fixture_id", "novel", "book", "chapter",
                "script_revision"):
        if base_src.get(key) != cand_src.get(key):
            problems.append(_problem(
                "source_changed", "source", f"/source/{key}",
                f"修订不得改变来源字段 {key}（范围与来源守卫）",
                base=base_src.get(key), candidate=cand_src.get(key)))
    if problems:
        return problems

    base_groups = _group_points(base, leaf_of)
    cand_groups = _group_points(candidate, leaf_of)
    for key, g in base_groups.items():
        cand = cand_groups.get(key)
        missing = sorted(g["covered"] - (cand["covered"] if cand else set()))
        if missing:
            leaf = g["leaf"]
            preview = "".join(leaf[i] for i in missing[:20] if 0 <= i < len(leaf))
            problems.append(_problem(
                "core_text_reduced", "segments", "/specs",
                f"核心文字减少：来源叶缺 {len(missing)} 个 code point（例如 {preview!r}）",
                locator=key, missing_total=len(missing), missing_indexes=missing[:40]))
        base_counts = g["counts"]
        cand_counts = (cand or {}).get("counts") or {}
        over = sorted(p for p, n in cand_counts.items() if n > max(1, base_counts.get(p, 0)))
        if over:
            problems.append(_problem(
                "duplicate_binding_without_base", "references", "/specs",
                f"{len(over)} 个 code point 被更多 spec 重复显示，但基线没有对应旧绑定"
                "（重复显示需显式旧绑定对应）",
                locator=key, points=over[:40], count=len(over)))
    return problems


def _leaf_resolver(source_doc: Any, bundle: Mapping[str, Any]):
    """给修订守卫用：``(locator, template) -> 叶文本 | None``（与校验同一个解析器）。"""
    if source_doc is None:
        return None
    kind = str((bundle.get("source") or {}).get("kind"))
    shots_len = validate_mod.shot_table(source_doc).get("count")

    def resolve(locator, template):
        ok, value, _ = validate_mod.resolve_locator(
            locator, source_kind=kind, source_doc=source_doc, template=template,
            shots_len=shots_len, path="")
        return value if ok else None

    return resolve


def _recompute_derived(candidate: Mapping[str, Any], base: Mapping[str, Any]) -> list[dict]:
    """代码重算 char 引用 / 帧数 / 最终窗口径（不采信请求里写的派生值）。

    * char 引用：``char_end`` 必须等于 ``char_start + len(text)``（code point 数）；
      分段连续性/完整性由 ``validate`` 统一判（同一口径，不重复实现）。
    * 帧数：``frame_count = quantize_frame_count(duration_sec)``（唯一量化函数）。
    * 最终窗：overlay 的绝对窗由 ``overlay.build_overlay_plan`` 用时间线窗重算。
    """
    problems: list[dict] = []
    for i, spec in enumerate(candidate.get("specs") or []):
        for j, seg in enumerate(spec.get("segments") or []):
            cs, ce = int(seg["char_start"]), int(seg["char_end"])
            text = str(seg.get("text") or "")
            want = cs + len(text)
            if ce != want:
                problems.append(_problem(
                    "derived_char_index_mismatch", "segments",
                    f"/specs/{i}/segments/{j}/char_end",
                    f"char_end 必须由代码重算为 char_start + code point 数 = {want}，实际 {ce}",
                    declared=ce, recomputed=want))
    return problems


# ---------------------------------------------------------------- 子命令实现

def _cmd_validate(args, *, shooter) -> int:
    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(json.dumps({"code": "bundle_missing", "path": str(bundle_path)},
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    try:
        bundle = _load_json_strict(bundle_path)
    except Exception as exc:
        print(json.dumps({"code": "bundle_unreadable", "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    root, root_problems, layout = _resolve_package_root(
        args.out_dir, bundle if isinstance(bundle, Mapping) else None, kind="validate")
    if root_problems:
        print(json.dumps({"problems": root_problems}, ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    asset_root = Path(args.asset_root) if args.asset_root else bundle_path.parent / "assets"
    source_dir = Path(args.source_dir) if args.source_dir else None
    with MotionJob(package_root=root, attempt_id=args.attempt_id, kind="validate") as job:
        frozen_assets, asset_records = _freeze_assets(job, bundle, asset_root, [])
        frozen_bundle, frozen_sha = _freeze_bundle(job, bundle, bundle_path)
        report, _doc, _rev = _validate(job, bundle, asset_root=asset_root, source_dir=source_dir)
        problems = list(report.problems)
        input_key = _bundle_input_key(bundle)
        eio.freeze_inputs(job.attempt, {
            "kind": "validate", "bundle_path": str(bundle_path.resolve()),
            "bundle_sha256": eio.sha256_file(bundle_path), "input_key": input_key,
            "asset_root": str(asset_root), "layout": layout,
            "asset_records": asset_records,
        })
        eio.append_event(job.attempt, "validate", after={"state": "validated" if not problems else "rejected"},
                         input_key=input_key, problem_count=len(problems))
        report_path = job.attempt / "validate_report.json"
        report_path.write_text(json.dumps(
            {"ok": not problems, "problems": problems, "facts": report.facts, "layout": layout},
            ensure_ascii=False, indent=2), encoding="utf-8")
        if problems:
            _seal_failure(root, job.attempt, kind="validate",
                          payload={"input_key": input_key}, problems=problems)
            _print_result(problems, job, 0)
            return eio.EXIT_REJECTED
        _commit(job, kind="validate", input_key=input_key,
                payload={"input_key": input_key, "facts": report.facts, "layout": layout,
                         "production_admission": report.facts.get("production_admission")},
                problems=[], report=None, bundle_path=bundle_path,
                frozen_bundle=frozen_bundle, frozen_bundle_sha=frozen_sha,
                asset_records=asset_records, overlay_plan=None,
                extra_artifacts=[report_path])
        _print_result([], job, 0)
        return eio.EXIT_OK


def _cmd_render(args, *, shooter) -> int:
    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(json.dumps({"code": "bundle_missing", "path": str(bundle_path)},
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    try:
        bundle = _load_json_strict(bundle_path)
    except Exception as exc:
        print(json.dumps({"code": "bundle_unreadable", "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    root, root_problems, layout = _resolve_package_root(
        args.out_dir, bundle if isinstance(bundle, Mapping) else None, kind="render")
    if root_problems:
        print(json.dumps({"problems": root_problems}, ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    asset_root = Path(args.asset_root) if args.asset_root else bundle_path.parent / "assets"
    source_dir = Path(args.source_dir) if args.source_dir else None
    with MotionJob(package_root=root, attempt_id=args.attempt_id, kind="render") as job:
        frozen_assets, asset_records = _freeze_assets(job, bundle, asset_root, [])
        frozen_bundle, frozen_sha = _freeze_bundle(job, bundle, bundle_path)
        report, _doc, _rev = _validate(job, bundle, asset_root=asset_root, source_dir=source_dir)
        problems = list(report.problems)
        input_key = _bundle_input_key(bundle)
        eio.freeze_inputs(job.attempt, {
            "kind": "render", "bundle_path": str(bundle_path.resolve()),
            "bundle_sha256": eio.sha256_file(bundle_path), "input_key": input_key,
            "asset_root": str(asset_root), "layout": layout, "asset_records": asset_records,
        })
        if problems:
            _seal_failure(root, job.attempt, kind="render", payload={"input_key": input_key},
                          problems=problems)
            _print_result(problems, job, 0)
            return eio.EXIT_REJECTED
        runtime = render_mod.runtime_status()
        render_problems = _run_render_phase(job, bundle, report, frozen_assets=frozen_assets,
                                            shooter=shooter, encode_mp4=not args.no_encode)
        problems = list(render_problems)
        eio.append_event(job.attempt, "render",
                         after={"state": "rendered" if not problems else "render_failed"},
                         input_key=input_key, runtime_available=runtime.get("available"),
                         problem_count=len(problems))
        plan_path = None
        if not problems:
            plan_path, plan_problems = _emit_overlay_plan(job, bundle, report, args=args,
                                                          source_doc=_doc)
            problems.extend(plan_problems)
        if problems:
            status = "not_run" if _only_runtime(problems) else "rejected"
            _seal_failure(root, job.attempt, kind="render",
                          payload={"input_key": input_key, "runtime": runtime,
                                   "facts": report.facts}, problems=problems, status=status)
            _print_result(problems, job, report.facts.get("frame_count_by_spec", {}))
            return eio.EXIT_TOOL_FAIL if status == "not_run" else eio.EXIT_REJECTED
        pointer, code = _commit(job, kind="render", input_key=input_key,
                                payload={"input_key": input_key, "facts": report.facts,
                                         "layout": layout, "runtime": runtime,
                                         "overlay_plan_file": str(plan_path) if plan_path else None,
                                         "overlay_plan_status": "written" if plan_path else "not_applicable"},
                                problems=[], report=report, bundle_path=bundle_path,
                                frozen_bundle=frozen_bundle, frozen_bundle_sha=frozen_sha,
                                asset_records=asset_records, overlay_plan=None,
                                specs=bundle.get("specs") or (),
                                extra_artifacts=[], plan_artifact=plan_path)
        _print_result([], job, report.facts.get("frame_count_by_spec", {}))
        return code


def _only_runtime(problems: Sequence[Mapping[str, Any]]) -> bool:
    return bool(problems) and all(p.get("code") == "render_runtime_unavailable" for p in problems)


def _cmd_revise(args, *, shooter) -> int:
    request_path = Path(args.request)
    if not request_path.is_file():
        print(json.dumps({"code": "request_missing", "path": str(request_path)},
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    try:
        request = _load_json_strict(request_path)
    except Exception as exc:
        print(json.dumps({"code": "request_unreadable", "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    structural = schema_mod.check_revision_request(request)
    root, root_problems, layout = _resolve_package_root(
        args.out_dir, request.get("replacement_bundle") if isinstance(request, Mapping) else None,
        kind="revise")
    if structural or root_problems:
        problems = structural + root_problems
        if root:
            with MotionJob(package_root=root, attempt_id=args.attempt_id, kind="revise") as job:
                _seal_failure(root, job.attempt, kind="revise",
                              payload={"request_path": str(request_path)}, problems=problems)
        print(json.dumps({"problems": problems}, ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT

    # 核 base key（当前 accepted 基线）
    try:
        accepted = eio.verify_accepted(root, required_checks=REQUIRED_BASE_CHECKS)
    except eio.EnhancementError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    pointer, manifest = accepted["pointer"], accepted["manifest"]
    if str(request["base_input_key"]) != str(pointer.get("input_key")):
        err = eio.EnhancementError(
            "stale_base_key", "revise",
            "请求基于的不是当前 accepted 基线（旧 key/revision）：请加载当前实验基线再提交",
            exit_code=eio.EXIT_REJECTED, field_path="base_input_key",
            current_input_key=pointer.get("input_key"),
            requested_base_input_key=request["base_input_key"])
        print(json.dumps(err.payload(), ensure_ascii=False), file=sys.stderr)
        return err.exit_code

    base_frozen = Path((manifest.get("bundle") or {}).get("frozen_path") or "")
    if not base_frozen.is_file():
        err = eio.EnhancementError("base_frozen_missing", "revise",
                                   f"基线冻结 bundle 缺失: {base_frozen}",
                                   exit_code=eio.EXIT_REJECTED)
        print(json.dumps(err.payload(), ensure_ascii=False), file=sys.stderr)
        return err.exit_code
    if eio.sha256_file(base_frozen) != (manifest.get("bundle") or {}).get("frozen_sha256"):
        err = eio.EnhancementError("base_frozen_digest_mismatch", "revise",
                                   "基线冻结 bundle 摘要不符（可能被改写）",
                                   exit_code=eio.EXIT_REJECTED)
        print(json.dumps(err.payload(), ensure_ascii=False), file=sys.stderr)
        return err.exit_code
    base_bundle = json.loads(base_frozen.read_text(encoding="utf-8"))

    candidate = copy.deepcopy(request["replacement_bundle"])
    asset_root = Path(args.asset_root) if args.asset_root else request_path.parent / "assets"
    source_dir = Path(args.source_dir) if args.source_dir else None
    # 来源只加载一次：守卫（叶坐标覆盖）与完整校验共用同一份解析结果与同一套判据
    preloaded = validate_mod.load_bundle_with_sources(candidate, asset_root=asset_root,
                                                      source_dir=source_dir)
    problems = revision_guard_problems(base_bundle, candidate,
                                       leaf_of=_leaf_resolver(preloaded[0], candidate))
    problems.extend(_recompute_derived(candidate, base_bundle))
    input_key = _bundle_input_key(candidate)
    no_change = input_key == str(pointer.get("input_key"))

    attempt_id = args.attempt_id or f"revise-{_now_stamp()}-{int(time.time_ns() % 100000):05d}"
    with MotionJob(package_root=root, attempt_id=attempt_id, kind="revise") as job:
        frozen_assets, asset_records = _freeze_assets(job, candidate, asset_root, problems)
        frozen_bundle, frozen_sha = _freeze_bundle(job, candidate, request_path)
        eio.freeze_inputs(job.attempt, {
            "kind": "revise", "request_path": str(request_path.resolve()),
            "request_id": str(request.get("request_id")),
            "request_sha256": eio.sha256_file(request_path),
            "base_input_key": request["base_input_key"], "input_key": input_key,
            "change_reason": request.get("change_reason"),
            "base_manifest": str(pointer.get("manifest_path")),
            "asset_root": str(asset_root), "layout": layout,
            "asset_records": asset_records, "no_change": no_change,
        })
        if problems:
            _seal_failure(root, job.attempt, kind="revise",
                          payload={"request_id": request.get("request_id"),
                                   "base_input_key": request["base_input_key"],
                                   "input_key": input_key, "no_change": no_change},
                          problems=problems)
            _print_result(problems, job, 0)
            print(json.dumps({"residual_issues": problems}, ensure_ascii=False), file=sys.stderr)
            return eio.EXIT_REJECTED

        report, _doc, _rev = _validate(job, candidate, asset_root=asset_root,
                                       source_dir=source_dir, preloaded=preloaded)
        problems = list(report.problems)
        if problems:
            _seal_failure(root, job.attempt, kind="revise",
                          payload={"request_id": request.get("request_id"),
                                   "base_input_key": request["base_input_key"],
                                   "input_key": input_key, "no_change": no_change},
                          problems=problems)
            _print_result(problems, job, 0)
            print(json.dumps({"residual_issues": problems}, ensure_ascii=False), file=sys.stderr)
            return eio.EXIT_REJECTED

        runtime = render_mod.runtime_status()
        problems = _run_render_phase(job, candidate, report, frozen_assets=frozen_assets,
                                     shooter=shooter, encode_mp4=not args.no_encode)
        eio.append_event(job.attempt, "revise",
                         before={"input_key": pointer.get("input_key")},
                         after={"state": "rendered" if not problems else "render_failed"},
                         request_id=request.get("request_id"), input_key=input_key,
                         no_change=no_change)
        plan_path = None
        if not problems:
            plan_path, plan_problems = _emit_overlay_plan(job, candidate, report, args=args,
                                                          source_doc=_doc)
            problems.extend(plan_problems)
        if problems:
            status = "not_run" if _only_runtime(problems) else "rejected"
            _seal_failure(root, job.attempt, kind="revise",
                          payload={"request_id": request.get("request_id"),
                                   "base_input_key": request["base_input_key"],
                                   "input_key": input_key, "no_change": no_change,
                                   "runtime": runtime, "facts": report.facts},
                          problems=problems, status=status)
            _print_result(problems, job, report.facts.get("frame_count_by_spec", {}))
            return eio.EXIT_TOOL_FAIL if status == "not_run" else eio.EXIT_REJECTED
        pointer2, code = _commit(job, kind="revise", input_key=input_key,
                                 payload={"request_id": request.get("request_id"),
                                          "base_input_key": request["base_input_key"],
                                          "input_key": input_key, "no_change": no_change,
                                          "change_reason": request.get("change_reason"),
                                          "facts": report.facts, "layout": layout,
                                          "runtime": runtime,
                                          "overlay_plan_file": str(plan_path) if plan_path else None,
                                          "overlay_plan_status": "written" if plan_path else "not_applicable"},
                                 problems=[], report=report, bundle_path=request_path,
                                 frozen_bundle=frozen_bundle, frozen_bundle_sha=frozen_sha,
                                 asset_records=asset_records, overlay_plan=None,
                                 specs=candidate.get("specs") or (),
                                 extra_artifacts=[], plan_artifact=plan_path)
        _print_result([], job, report.facts.get("frame_count_by_spec", {}))
        return code


def _print_result(problems: Sequence[Mapping[str, Any]], job: MotionJob, frames: Any) -> None:
    payload = {
        "attempt_id": job.attempt_id,
        "attempt_dir": str(job.attempt),
        "package_root": str(job.package_root),
        "problems": list(problems),
        "frames": frames,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- demo

def _cmd_demo(args, *, shooter) -> int:
    out_dir = Path(args.out_dir)
    root, root_problems, layout = _resolve_package_root(out_dir, None, kind="demo")
    if root_problems:
        print(json.dumps({"problems": root_problems}, ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_INVALID_INPUT
    experiment_id = root.parent.name
    try:
        from tests.enhancements import build_fixtures  # type: ignore
    except Exception as exc:
        print(json.dumps(_problem("fixture_builder_missing", "demo", "",
                                  f"夹具生成器不可用（tests/enhancements/build_fixtures.py）: {exc}"),
                         ensure_ascii=False), file=sys.stderr)
        return eio.EXIT_TOOL_FAIL
    font_dir = Path(args.font_dir) if args.font_dir else Path(r"C:\Windows\Fonts")
    attempt_id = args.attempt_id or f"demo-{_now_stamp()}-{int(time.time_ns() % 100000):05d}"
    with MotionJob(package_root=root, attempt_id=attempt_id, kind="demo") as job:
        fixture_root = root.parent / "fixtures"
        try:
            built = build_fixtures.build_motion_bundle(
                fixture_root, experiment_id=experiment_id, font_dir=font_dir,
                scale=float(args.scale))
        except Exception as exc:
            print(json.dumps(_problem("fixture_build_failed", "demo", "", str(exc)),
                             ensure_ascii=False), file=sys.stderr)
            return eio.EXIT_TOOL_FAIL
        cases = built["cases"]
        report: dict[str, Any] = {"cases": {}, "contact_sheets": [], "keyframes": [],
                                  "overlay_plans": [], "render_status": "pass"}
        problems: list[dict] = []
        for name, case in cases.items():
            bundle_path = Path(case["bundle"])
            bundle = _load_json_strict(bundle_path)
            entry: dict[str, Any] = {"bundle": str(bundle_path),
                                     "template": case.get("template"),
                                     "fixture_id": case.get("fixture_id"),
                                     "duration_sec": case.get("duration_sec"),
                                     "frame_count": case.get("frame_count")}
            sub_root = root / "cases" / name
            sub_root.mkdir(parents=True, exist_ok=True)
            report_obj, _doc, _rev = _validate(job, bundle, asset_root=Path(built["asset_root"]),
                                               source_dir=None)
            if report_obj.problems:
                problems.extend([{**p, "case": name} for p in report_obj.problems])
                entry["status"] = "rejected"
                entry["problems"] = report_obj.problems
                report["cases"][name] = entry
                continue
            frozen_assets, _rec = _freeze_assets(job, bundle, Path(built["asset_root"]), [])
            frames_ok = True
            for mode in ("animated", "static"):
                work_dir = job.attempt / "outputs" / name / mode
                layout_obj = validate_mod.layout_spec(bundle["specs"][0], bundle["canvas"])
                res = render_mod.render_spec(
                    bundle["specs"][0], bundle["canvas"], layout_obj, work_dir=work_dir,
                    asset_paths=frozen_assets, shooter=shooter, mode=mode,
                    encode_mp4=not args.no_encode)
                entry[mode] = res.to_dict()
                if res.problems:
                    problems.extend([{**p, "case": name, "mode": mode} for p in res.problems])
                    frames_ok = False
                if res.status == render_mod.STATUS_PASS and res.frames_dir:
                    entry.setdefault("frames", {})[mode] = str(res.frames_dir)
                    kf, kf_problems = render_mod.copy_keyframes(
                        res.frames_dir, job.attempt / "screenshots" / name,
                        case["keyframe_indexes"], f"{name}_{mode}")
                    problems.extend([{**p, "case": name} for p in kf_problems])
                    entry.setdefault("keyframes", {})[mode] = [str(p) for p in kf]
                    report["keyframes"].extend(str(p) for p in kf)
            if frames_ok and entry.get("frames"):
                # Step04 计划文件：动画版 → png_sequence，静态版 → image（S 侧两种 kind 都要能收）
                doc = None
                try:
                    doc = validate_mod.load_fixture_doc(Path(built["asset_root"]),
                                                        bundle["source"]["fixture_id"])
                except Exception:
                    doc = None
                plan_modes = (("animated", "motion_overlay_plan.json"),
                              ("static", "motion_overlay_plan_static.json"))                     if bundle.get("overlays") else ()
                for mode, filename in plan_modes:
                    plan_report = overlay_mod.build_overlay_plan_file(
                        bundle, scope=overlay_mod.derive_scope(bundle),
                        script_revision=_script_revision_for(args, bundle, doc),
                        rendered={str(bundle["specs"][0]["spec_id"]): {
                            "frames_dir": entry["frames"][mode],
                            "frame_count": entry[mode]["frame_count"]}},
                        mode=mode)
                    if plan_report.problems:
                        problems.extend([{**pp, "case": name} for pp in plan_report.problems])
                        continue
                    plan_path = job.attempt / "outputs" / name / filename
                    overlay_mod.write_overlay_plan_file(plan_report.plan, plan_path)
                    entry.setdefault("overlay_plans", {})[mode] = str(plan_path)
                    report["overlay_plans"].append(
                        {"case": name, "mode": mode, "kind": plan_report.plan["overlays"][0]["kind"],
                         "path": str(plan_path)})
                items = []
                for mode in ("animated", "static"):
                    fdir = Path(entry["frames"][mode])
                    mid = max(0, (max(case["keyframe_indexes"]) ) )
                    items.append({"label": f"{name} [{mode}]",
                                  "image": fdir / (render_mod.FRAME_PATTERN % mid)})
                sheet = job.attempt / "contact_sheets" / f"{name}.png"
                cs_problems = render_mod.compose_contact_sheet(
                    items, sheet, font_path=Path(built["fonts"]["label"]))
                problems.extend([{**p, "case": name} for p in cs_problems])
                if sheet.is_file():
                    report["contact_sheets"].append(str(sheet))
            entry["status"] = "rendered" if frames_ok else "not_run"
            report["cases"][name] = entry
        if problems and _only_runtime(problems):
            report["render_status"] = "not_run"
        elif problems:
            report["render_status"] = "partial"
        report["problems"] = problems
        report["layout"] = layout
        report["fixture_root"] = str(fixture_root)
        report["runtime"] = render_mod.runtime_status()
        report["scale"] = float(args.scale)
        report_path = job.attempt / "demo_manifest.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        eio.append_event(job.attempt, "demo",
                         after={"state": report["render_status"]},
                         case_count=len(cases), problem_count=len(problems))
        demo_artifacts = [eio.artifact_record(report_path, role="demo_manifest")]
        for p in report["contact_sheets"]:
            demo_artifacts.append(eio.artifact_record(Path(p), role="contact_sheet"))
        for plan in report["overlay_plans"]:
            demo_artifacts.append(eio.artifact_record(Path(plan["path"]), role="overlay_plan",
                                                      media={"kind": plan["kind"]}))
        eio.seal_manifest(job.attempt, {
            "package": PKG, "kind": "demo", "status": report["render_status"],
            "tool_versions": _tool_versions(),
            "checks": [{"name": "render",
                        "status": {"pass": "pass", "not_run": "not_run",
                                   "partial": "fail"}[report["render_status"]]}],
            "problems": problems,
            "artifacts": demo_artifacts,
            "layout": layout,
            "runtime": report["runtime"],
            "fixture_root": str(fixture_root),
            "scale": float(args.scale),
            "case_count": len(cases),
        })
        print(json.dumps({"attempt_dir": str(job.attempt), "report": str(report_path),
                          "render_status": report["render_status"],
                          "problem_count": len(problems),
                          "contact_sheets": report["contact_sheets"],
                          "overlay_plans": report["overlay_plans"],
                          "keyframes": len(report["keyframes"])},
                         ensure_ascii=False, indent=2))
        if report["render_status"] == "pass":
            return eio.EXIT_OK
        return eio.EXIT_TOOL_FAIL if report["render_status"] == "not_run" else eio.EXIT_REJECTED


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="motion_lab",
        description=("aiVideo A 包动效协议：MotionBundle v1 校验 / 渲染 / 完整替换修订 / 样片。\n"
                     "python tools/motion_lab.py validate --bundle FILE --out-dir ROOT/motion\n"
                     "python tools/motion_lab.py render   --bundle FILE --out-dir ROOT/motion\n"
                     "python tools/motion_lab.py revise   --request FILE --out-dir ROOT/motion\n"
                     "python tools/motion_lab.py demo     --out-dir ROOT/motion"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--scope-file", default=None,
                        help="Step04 时间线的 scope JSON（必须与 S 的 timeline 相同；"
                             "缺省时按来源派生并在 manifest 标注 scope_source=derived）")
        sp.add_argument("--script-revision", default=None,
                        help="overlay 编号所属 revision（缺省取 fixture 文档/采纳稿的字段）")
        sp.add_argument("--asset-root", default=None,
                        help="只读素材根（相对 path 的解析基准；默认 <bundle 所在目录>/assets）")
        sp.add_argument("--source-dir", default=None,
                        help="accepted_script 的章目录（默认 <asset-root>/source）")
        sp.add_argument("--attempt-id", default=None, help="显式 attempt id（默认按时间生成）")
        sp.add_argument("--no-encode", action="store_true", help="跳过展示用 MP4 编码")

    sp = sub.add_parser("validate", help="结构 + 语义 + 版式 + 字体完整校验（不渲染）")
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--out-dir", required=True)
    common(sp)
    sp.set_defaults(func=_cmd_validate)

    sp = sub.add_parser("render", help="校验通过后取帧（RGBA PNG 序列 + 展示 MP4）")
    sp.add_argument("--bundle", required=True)
    sp.add_argument("--out-dir", required=True)
    common(sp)
    sp.set_defaults(func=_cmd_render)

    sp = sub.add_parser("revise", help="motion_revision.v1 完整替换：核 base key → 守卫 → 重算 → 渲染 → 提交")
    sp.add_argument("--request", required=True)
    sp.add_argument("--out-dir", required=True)
    common(sp)
    sp.set_defaults(func=_cmd_revise)

    sp = sub.add_parser("demo", help="三模板 × 两套内容 = 6 动效 + 6 静态对照 + 联系表 + 关键帧 + manifest")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--font-dir", default=None, help="字体目录（默认 C:\\Windows\\Fonts）")
    sp.add_argument("--scale", type=float, default=1.0,
                    help="样片几何缩放（1.0=画幅全尺寸；离线自测可调小以缩短取帧时间）")
    sp.add_argument("--attempt-id", default=None)
    sp.add_argument("--no-encode", action="store_true")
    sp.set_defaults(func=_cmd_demo)
    return p


def _force_utf8_stdio() -> None:
    """Windows 上被重定向到文件/管道时 stdout 会退回 GBK，中文 JSON 读不回来。

    这里显式改成 UTF-8（``errors="replace"``）；接在真实控制台时 Python 本就是
    UTF-8，不受影响。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if getattr(stream, "encoding", "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: Sequence[str] | None = None, *, shooter=None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)
    if shooter is None and args.command in ("render", "revise", "demo"):
        shooter = render_mod.PlaywrightShooter()
    try:
        return int(args.func(args, shooter=shooter))
    except eio.BusyError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except eio.EnhancementError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except eio.CommitUnconfirmedError as exc:  # pragma: no cover - 子类顺序保险
        print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
