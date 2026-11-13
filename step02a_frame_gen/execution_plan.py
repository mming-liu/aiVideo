# -*- coding: utf-8 -*-
"""step02a.execution_plan - 画面执行计划（S03）.

本模块把 Step01 语义稿转成**可保存、可消费、可重验**的 ``visual_plan.json``：

- 每个实际帧（ff/lf/kf）的时刻职责、最终提示、模型、内容哈希；
- 可见人物/道具与画外声音的关系、必要道具与静态载体；
- 参考图身份（sha256 + purpose）与未准备输入；
- 未决项与反馈路由。

它同时是 S02 R1 的**真实装载入口**：``build_visual_plan`` 调用
``shared.execution_contract.build_execution_binding`` 构建绑定并立即用
``validate_execution_binding`` 重验；``check_execution_semantics`` 的结果通过
``attach_semantic_checks`` 写入绑定。Step02a 的 ``--plan-only`` 与真实生成
（``gen_all_frames``）共用本模块，发送前再次核对输入身份。

生成前的结构检查（声明矛盾/引用缺失/参考能力/首尾状态）在这里完成；生成后的
实际人物/道具/身份/构图核对走 ``verify_frame_observations``——它消费显式观测
记录，不假装做了视觉模型判断。未提供观测时明确记 ``unverified``。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from shared.execution_contract import (
    attach_semantic_checks,
    build_execution_binding,
    script_digest,
    unresolved_gate_issues,
    validate_execution_binding,
)
from shared.execution_semantics import (
    CHECKER_VERSION,
    apply_execution_candidate,
    check_execution_semantics,
)

VISUAL_PLAN_SCHEMA = "step02a.visual_plan.v1"
VISUAL_PLAN_STAGE = "step02a.visual_plan"

#: 执行阶段允许细化的字段（必须与绑定里的 allowed_changes/check 记录一致）。
VISUAL_ALLOWED_CHANGES = [
    "prompt", "camera_movement", "frame_layouts", "visual_plan.visible_characters",
    "visual_plan.visible_props", "visual_plan.offscreen_speakers",
]

#: 需要语义检查通过记录才允许改写的字段。
VISUAL_GUARDED_PATHS = [
    "prompt", "frame_layouts", "visual_plan.visible_characters",
    "visual_plan.visible_props", "visual_plan.offscreen_speakers",
]

_PROP_HINT_RE = re.compile(r"(信封|铜灯|漆桶|砂纸|船|琴|道具|prop|letter|lamp|boat)", re.I)


class VisualPlanError(RuntimeError):
    """画面计划构建/重验失败（带 code 与结构化 issues）."""

    def __init__(self, code: str, message: str, *, issues: list[dict] | None = None,
                 path: str = ""):
        super().__init__(message)
        self.code = code
        self.path = path
        self.issues = list(issues or [])

    def payload(self) -> dict:
        return {"code": self.code, "path": self.path, "message": str(self),
                "issues": self.issues}


def _scope_of(scope: dict) -> dict:
    return {"chapter": scope.get("chapter"), "book_index": scope.get("book_index"),
            "book_name": scope.get("book_name")}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _layouts(unit: dict) -> dict:
    layouts = unit.get("frame_layouts")
    return layouts if isinstance(layouts, dict) else {}


def _visible_names(unit: dict) -> list[str]:
    names: list[str] = []
    for frame in ("ff", "lf", "kfs"):
        for entry in (_layouts(unit).get(frame) or []):
            if not isinstance(entry, dict) or entry.get("on_screen") is False:
                continue
            name = _text(entry.get("name") or entry.get("character"))
            if name and name not in names:
                names.append(name)
    return names


def _visible_props(unit: dict) -> list[str]:
    props: list[str] = []
    for frame in ("ff", "lf", "kfs"):
        for entry in (_layouts(unit).get(frame) or []):
            if not isinstance(entry, dict):
                continue
            for prop in (entry.get("props") or []):
                name = _text(prop)
                if name and name not in props:
                    props.append(name)
    return props


def _unit_voice_units(script: dict, index: int) -> list[dict]:
    outline = script.get("chapter_outline") or {}
    units = outline.get("voice_units") if isinstance(outline, dict) else None
    if not isinstance(units, list):
        return []
    return [u for u in units if isinstance(u, dict)
            and u.get("shot_index") == index]


def _image_model() -> str:
    try:
        from _image_gen.config import ImageGenCfg
        provider = str(getattr(ImageGenCfg, "PROVIDER", "") or "unknown")
        model = ""
        for attr in ("AGNES_IMAGE_MODEL", "VOLCENGINE_IMAGE_MODEL",
                     "MINIMAX_IMAGE_MODEL"):
            value = getattr(ImageGenCfg, attr, "")
            if value:
                model = str(value)
                break
        return f"{provider}:{model or 'unknown'}"
    except Exception:
        return "unknown:unknown"


def _frame_kinds(frames: set[str] | None) -> list[str]:
    if frames is None:
        return ["ff", "lf"]
    out = []
    if "ff" in frames:
        out.append("ff")
    if "lf" in frames:
        out.append("lf")
    if "kf" in frames or any(str(f).startswith("kf") for f in frames):
        out.append("kf")
    return out or ["ff"]


def _frame_prompt(unit: dict, characters: list[dict], kind: str) -> tuple[str, str | None]:
    try:
        from shared.vimax_bridge import compose_frame_prompt
        frame_kind = "last_frame" if kind == "lf" else "first_frame"
        return compose_frame_prompt(unit, characters, frame_kind), None
    except Exception as exc:  # noqa: BLE001 - 单帧失败不拖垮整份计划
        return "", str(exc)[:500]


def _frame_refs(unit: dict, characters: list[dict], kind: str) -> list[dict]:
    from shared.vimax_bridge import _collect_frame_refs_for_shot

    paths = _collect_frame_refs_for_shot(
        unit, characters, limit=12,
        for_frame="尾帧" if kind == "lf" else "首帧")
    return _ref_entries(list(paths or []))


def _ref_entries(paths: list) -> list[dict]:
    entries: list[dict] = []
    for raw in paths:
        if not raw:
            continue
        path = Path(str(raw))
        name = path.name.lower()
        role = "reference"
        if "first_frame" in name:
            role = "first_frame"
        elif "last_frame" in name:
            role = "last_frame"
        elif "keyframe" in name:
            role = "keyframe"
        elif "portrait" in name or "characters_bank" in str(path).lower():
            role = "design_reference"
        entries.append({
            "role": role,
            "path": str(path),
            "purpose": ("身份锚点" if role == "design_reference"
                        else "连续性/构图参考" if role in ("first_frame", "last_frame")
                        else "参考"),
            "sha256": _sha256_file(path),
            "prepared": path.is_file(),
        })
    return entries


def _preflight_unit(unit: dict, characters: list[dict], frames: set[str] | None,
                    ref_limit: int | None) -> list[dict]:
    issues: list[dict] = []
    idx = unit.get("index", 0)
    text = "\n".join(_text(unit.get(k)) for k in ("prompt", "ff_desc", "lf_desc",
                                                  "motion_desc"))
    try:
        from shared.prompt_contradiction import find_contradictions
        for problem in find_contradictions(text):
            issues.append({"code": "prompt_contradiction", "shot_index": idx,
                           "path": f"shots[{idx}].prompt", "message": problem})
    except Exception:
        pass
    kinds = _frame_kinds(frames)
    for kind in kinds:
        if kind == "kf":
            continue
        if not _text(unit.get("ff_desc" if kind == "ff" else "lf_desc")):
            issues.append({"code": "frame_description_missing", "shot_index": idx,
                           "path": f"shots[{idx}].{'ff_desc' if kind == 'ff' else 'lf_desc'}",
                           "message": f"{kind} 帧描述为空，无法构建可核对的画面计划"})
    names = _visible_names(unit)
    subject = _text(unit.get("main_subject"))
    known = {_text(c.get("identifier") or c.get("name"))
             if isinstance(c, dict) else _text(c) for c in characters}
    if subject and subject in known and subject not in names:
        issues.append({"code": "required_character_missing", "shot_index": idx,
                       "path": f"shots[{idx}].main_subject",
                       "message": f"主主体 {subject!r} 不在任何可见布局中"})
    if ref_limit is not None:
        try:
            refs = _frame_refs(unit, characters, kinds[0] if kinds else "ff")
            if len(refs) > ref_limit:
                issues.append({"code": "reference_capacity", "shot_index": idx,
                               "path": f"shots[{idx}].references",
                               "message": f"参考图 {len(refs)} 张超过当前上限 {ref_limit}"})
        except Exception:
            pass
    return issues


def _frame_entry(unit: dict, characters: list[dict], kind: str, model: str,
                 *, frame_label: str | None = None) -> dict:
    idx = unit.get("index", 0)
    label = frame_label or kind
    prompt, prompt_error = _frame_prompt(unit, characters, kind)
    try:
        refs = _frame_refs(unit, characters, kind)
    except Exception as exc:  # noqa: BLE001
        refs = []
        prompt_error = prompt_error or str(exc)[:500]
    ref_digests = [{"role": r["role"], "sha256": r["sha256"]}
                   for r in refs if r.get("sha256")]
    content_hash = _sha256_text(_canonical({
        "shot_index": idx, "frame": label, "model": model, "prompt": prompt,
        "references": ref_digests,
    }))
    moment = {"ff": "start", "lf": "end", "kf": "key_moment"}[kind]
    responsibility = {
        "ff": "本镜动作起始时刻的构图（不得提前画出终态）",
        "lf": "本镜动作终态（与首帧同一外观/空间连续）",
        "kf": "关键帧时刻的中间状态",
    }[kind]
    unresolved: list[dict] = []
    for ref in refs:
        if not ref.get("prepared"):
            unresolved.append({"code": "reference_unprepared", "path": ref.get("path"),
                               "message": f"参考素材尚未准备: {ref.get('path')}"})
    if prompt_error:
        unresolved.append({"code": "frame_prompt_unavailable", "path": f"shots[{idx}].{kind}",
                           "message": prompt_error})
    return {
        "shot_index": idx,
        "frame": label,
        "kind": kind,
        "frame_id": f"shot-{idx}:{label}",
        "moment": moment,
        "responsibility": responsibility,
        "prompt": prompt,
        "model": model,
        "content_hash": content_hash,
        "references": refs,
        "visible_characters": _visible_names(unit),
        "visible_props": _visible_props(unit),
        "status": "blocked" if unresolved else "planned",
        "unresolved": unresolved,
    }


def build_visual_plan(script: dict, *, scope: dict, stage: str = VISUAL_PLAN_STAGE,
                      only_indices: set[int] | None = None,
                      frames: set[str] | None = None,
                      characters: list | None = None,
                      source_digest: str | None = None,
                      candidate: dict | None = None) -> dict:
    """构建并重验画面执行计划（纯离线；不写盘、不调 provider）.

    ``candidate`` 是待评估的执行细化（``visual_plan``/``frame_layouts``/``prompt``
    等）；给出时计划按合并后的候选构建，语义检查也针对候选后的结果。绑定来源
    仍是原稿，因此候选改变不了输入身份。
    """
    if not isinstance(script, dict):
        raise VisualPlanError("script_invalid", "script 必须是对象", path="script")
    scope_norm = _scope_of(scope)
    digest = source_digest or script_digest(script)
    binding = build_execution_binding(
        script, scope=scope_norm, stage=stage, source_digest=digest)
    binding_issues = validate_execution_binding(
        binding, script, expected_scope=scope_norm, expected_stage=stage)
    if binding_issues:
        raise VisualPlanError("binding_invalid", "执行绑定未通过消费前重验",
                              issues=binding_issues, path="execution_binding")
    semantic = check_execution_semantics(script, candidate)
    binding = attach_semantic_checks(
        binding, checked_paths=VISUAL_GUARDED_PATHS, findings=semantic,
        checker=CHECKER_VERSION)
    binding["allowed_changes"] = list(VISUAL_ALLOWED_CHANGES)
    # 始终走 `apply_execution_candidate`：它**先深拷贝**再合并，避免计划构建过程中
    # 的运行期写入（`_scene_match` 等由参考收集器写进 shot 的缓存）污染调用方稿件。
    planned = apply_execution_candidate(script, candidate)
    # 2026-11-12 复核：候选相对原稿改了哪些路径必须写进绑定——消费前的
    # `validate_execution_changes` 要求每条合法变化都带理由，否则一律
    # `change_reason_missing`。差异实现与校验同一份（`diff_script_paths`）。
    if candidate:
        from shared.execution_contract import diff_script_paths
        binding["changes"] = [
            {"path": item["path"], "reason": "execution_candidate"}
            for item in diff_script_paths(script, planned)
        ]
    unresolved = list(binding.get("unresolved") or [])
    feedback = list(binding.get("feedback") or [])

    units = ([planned["hook"]] if isinstance(planned.get("hook"), dict) else []) + \
            [s for s in (planned.get("shots") or []) if isinstance(s, dict)]
    if only_indices is not None:
        units = [u for u in units if int(u.get("index", -1)) in only_indices]
    chars = (list(planned.get("characters") or []) if characters is None
             else list(characters))
    model = _image_model()
    try:
        from shared.ref_compose import active_image_ref_limit
        ref_limit = active_image_ref_limit()
    except Exception:
        ref_limit = None

    preflight: list[dict] = []
    shot_entries: list[dict] = []
    all_frames: list[dict] = []
    for unit in units:
        idx = int(unit.get("index", 0))
        issues = _preflight_unit(unit, chars, frames, ref_limit)
        preflight.extend(issues)
        shot_issues = list(issues)
        for finding in semantic:
            if finding.get("shot_index") == idx:
                shot_issues.append(dict(finding))
        entry = {
            "shot_index": idx,
            "main_subject": _text(unit.get("main_subject")),
            "visible_characters": _visible_names(unit),
            "visible_props": _visible_props(unit),
            "necessary_props": _visible_props(unit),
            "offscreen_speakers": sorted({
                _text(u.get("speaker")) for u in _unit_voice_units(planned, idx)
                if _text(u.get("speaker"))
                and ("offscreen" in _text(u.get("kind")).lower()
                     or u.get("on_screen") is False)
            }),
            "voice_units": _unit_voice_units(planned, idx),
            "frames": [],
            "issues": shot_issues,
            # M05（2026-11-12 复核 P2）：**生成侧要消费的那一份参数**。
            # 此前计划只做验收、生成仍从原稿 `script` 取 `shot` 重新构建请求，
            # 于是「计划通过、发送内容没采用计划」可以同时成立。这里把合并后的
            # 单元整份带进计划，`gen_all_frames` 直接把它交给 `gen_shot_frames`。
            # `_` 前缀是运行期缓存（如 `_scene_match`），不进计划、也不参与差异比较。
            "execution": {"unit": {k: v for k, v in copy.deepcopy(unit).items()
                                   if not str(k).startswith("_")}},
            "status": "blocked" if any(i.get("code") in {
                "prompt_contradiction", "required_character_missing",
                "reference_capacity"} for i in shot_issues) else "planned",
        }
        for kind in _frame_kinds(frames):
            if kind == "kf":
                for i, kf in enumerate(unit.get("keyframes") or []):
                    frame = _frame_entry(unit, chars, "ff", model, frame_label=f"kf{i}")
                    frame.update({"moment": "key_moment",
                                  "responsibility": f"关键帧 {i}: {_text(kf.get('desc')) or '关键状态'}"})
                    entry["frames"].append(frame)
                    all_frames.append(frame)
                continue
            frame = _frame_entry(unit, chars, kind, model)
            if issues:
                frame["status"] = entry["status"]
            entry["frames"].append(frame)
            all_frames.append(frame)
        shot_entries.append(entry)

    # 2026-11-12（复核 P4）：只有**阻塞级**语义 finding 才把计划判成 blocked。
    # advisory 是计划结构记录（例如 `causal_chain` 缺「行动」标签、而镜头里过程完整），
    # 让它在 Step02a 再次拦下合法省略，等于把 Step01 已经通过的结论推翻。
    _semantic_blocking = [f for f in semantic
                          if str(f.get("severity") or "reject").strip().lower()
                          != "advisory"]
    plan = {
        "schema": VISUAL_PLAN_SCHEMA,
        "stage": stage,
        "scope": scope_norm,
        "source": dict(binding["source"]),
        "execution_binding": binding,
        "allowed_changes": list(VISUAL_ALLOWED_CHANGES),
        "model": model,
        "frames": all_frames,
        "shots": shot_entries,
        "checks": {
            "binding": binding_issues,
            "semantic": semantic,
            "preflight": preflight,
            "unresolved_gate": unresolved_gate_issues(binding),
        },
        "unresolved": unresolved,
        "feedback": feedback,
        "unverified": [],
        "status": "blocked" if (_semantic_blocking or unresolved_gate_issues(binding)
                                or any(s["status"] == "blocked" for s in shot_entries))
        else "planned",
    }
    plan["unverified"] = _unverified_items(plan)
    return plan


def _unverified_items(plan: dict) -> list[dict]:
    items: list[dict] = []
    for shot in plan.get("shots") or []:
        for frame in shot.get("frames") or []:
            items.append({
                "code": "actual_pixels_unverified",
                "path": frame.get("frame_id"),
                "message": "计划阶段只核对声明/身份/引用；实际人物、道具、构图需视觉 QC 或显式观测",
            })
    return items


def validate_plan_identity(plan: dict, script: dict, *, scope: dict,
                           stage: str = VISUAL_PLAN_STAGE) -> list[dict]:
    """发送前重验：计划自带的绑定必须仍对应当前稿件与 scope."""
    binding = plan.get("execution_binding") if isinstance(plan, dict) else None
    if not isinstance(binding, dict):
        return [{"code": "execution_binding_missing", "severity": "reject",
                 "path": "visual_plan.execution_binding",
                 "message": "画面计划没有执行绑定"}]
    issues = validate_execution_binding(binding, script, expected_scope=scope,
                                        expected_stage=stage)
    issues.extend(unresolved_gate_issues(binding))
    if plan.get("status") == "blocked":
        issues.append({"code": "visual_plan_blocked", "severity": "reject",
                       "path": "visual_plan.status",
                       "message": "画面计划存在阻塞项，不得发送生成"})
    declared = plan.get("source") or {}
    if declared.get("source_digest") != script_digest(script):
        issues.append({"code": "source_digest_stale", "severity": "reject",
                       "path": "visual_plan.source.source_digest",
                       "message": "画面计划的 source_digest 与当前稿件不一致"})
    return issues


def verify_plan_execution(plan: dict, *, characters: list | None = None,
                          check_reference_files: bool = True) -> list[dict]:
    """发送前：**计划里的最终提示与参考摘要必须仍能由计划自己的参数复算出来**.

    2026-11-12（复核 P2）：`validate_plan_identity` 只核上游身份（绑定/scope/digest），
    不核计划内部的最终提示与参考文件。于是「计划被改过」「参考文件在计划之后被替换」
    都能通过身份检查，而生成侧仍然照旧构建请求——出现「计划验收通过、发送内容没采用
    计划」的假贯通。本函数补的就是这一段：

    - 逐帧用 `execution.unit` 重算 `compose_frame_prompt`，与计划记录的 `prompt` 比对；
    - 逐参考重算文件 sha256，与计划记录的 `sha256` 比对（文件缺失也算失配）。

    Returns:
        问题列表（空 = 通过）；每条含 `code` / `severity` / `path` / `message`。
    """
    import hashlib as _hashlib
    from pathlib import Path as _Path

    issues: list[dict] = []
    chars = list(characters or [])
    for shot in (plan or {}).get("shots") or []:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("shot_index")
        unit = ((shot.get("execution") or {}).get("unit"))
        if not isinstance(unit, dict):
            issues.append({"code": "plan_execution_missing", "severity": "reject",
                           "path": f"visual_plan.shots[{idx}].execution",
                           "message": "计划缺少生成侧要消费的执行参数"})
            continue
        if str(unit.get("index")) != str(idx):
            issues.append({"code": "plan_execution_identity", "severity": "reject",
                           "path": f"visual_plan.shots[{idx}].execution.unit",
                           "message": f"执行参数属于镜 {unit.get('index')}，与条目镜 {idx} 不一致"})
            continue
        for frame in shot.get("frames") or []:
            if not isinstance(frame, dict):
                continue
            kind = frame.get("kind") or frame.get("frame")
            prompt, err = _frame_prompt(unit, chars, str(kind))
            if err or prompt != frame.get("prompt"):
                issues.append({
                    "code": "plan_prompt_drift", "severity": "reject",
                    "path": f"visual_plan.{frame.get('frame_id')}.prompt",
                    "message": ("计划的最终提示与执行参数复算结果不一致"
                                + (f"（{err}）" if err else "")),
                })
            if not check_reference_files:
                continue
            for ref in frame.get("references") or []:
                if not isinstance(ref, dict):
                    continue
                expected = str(ref.get("sha256") or "")
                path = str(ref.get("path") or "")
                if not expected or not path:
                    continue
                target = _Path(path)
                if not target.is_file():
                    issues.append({
                        "code": "plan_reference_missing", "severity": "reject",
                        "path": f"visual_plan.{frame.get('frame_id')}.references",
                        "message": f"计划引用的参考文件不存在：{path}"})
                    continue
                actual = _hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected:
                    issues.append({
                        "code": "plan_reference_drift", "severity": "reject",
                        "path": f"visual_plan.{frame.get('frame_id')}.references",
                        "message": f"参考文件在计划之后被替换：{path}"})
    return issues


def verify_plan_changes(plan: dict, script: dict, *,
                        protection: dict | None = None) -> list[dict]:
    """消费前：计划单元相对**冻结原稿**的变化必须仍在授权与保护范围内.

    2026-11-12 复核：`verify_plan_execution` 只能证明「计划自洽」——单元和提示一起
    变化时，内部一致性检查完全无法独立守住上游内容保护。这里把计划代表的合并结果
    还原成一份稿件，交给 :func:`shared.execution_contract.validate_execution_changes`
    逐路径判定：受保护字段、需语义证据字段、越界字段、缺理由字段四类都会被拒。
    """
    from shared.execution_contract import validate_execution_changes

    binding = (plan or {}).get("execution_binding")
    if not isinstance(binding, dict):
        return [{"code": "execution_binding_missing", "severity": "reject",
                 "path": "visual_plan.execution_binding",
                 "message": "画面计划没有执行绑定，无法核对变化范围"}]
    units: dict[int, dict] = {}
    for entry in (plan or {}).get("shots") or []:
        if not isinstance(entry, dict):
            continue
        unit = ((entry.get("execution") or {}).get("unit"))
        if isinstance(unit, dict):
            try:
                units[int(entry.get("shot_index"))] = unit
            except (TypeError, ValueError):
                continue
    after = copy.deepcopy(script)
    if isinstance(after.get("shots"), list):
        after["shots"] = [
            {k: v for k, v in units[int(s.get("index", -1))].items()
             if not str(k).startswith("_")}
            if int(s.get("index", -1)) in units else s
            for s in after["shots"] if isinstance(s, dict)]
    if isinstance(after.get("hook"), dict) and 0 in units:
        after["hook"] = {k: v for k, v in units[0].items()
                         if not str(k).startswith("_")}
    allowed = list((plan or {}).get("allowed_changes") or VISUAL_ALLOWED_CHANGES)
    return validate_execution_changes(script, after, binding=binding,
                                      allowed_changes=allowed,
                                      protection=protection)


def verify_plan_coverage(plan: dict, *, only_indices: set | None = None,
                         frames: set | None = None,
                         script: dict | None = None) -> list[dict]:
    """消费前：**请求的镜头与帧必须都在计划里**，否则拒绝发送（不允许回退原稿）.

    2026-11-12 复核：任务构造此前是 `_plan_units.get(idx, shot)`——显式请求的镜头
    没被计划覆盖时仍会用原稿生成。计划模式下缺单元必须零调用拒收。

    2026-11-13 复核补充（F1）：`only_indices is None` 表示**全量运行**，此时必须用
    完整稿 `script["shots"]` 的全集比较，而不是计划自己的条目——否则计划漏掉的
    镜头不会出现在 `wanted` 里，主入口随后又把 `only_indices` 收窄为计划镜头，
    漏镜就被静默跳过。只有显式给出 `only_indices` 时才保留「只核请求范围」语义。
    """
    issues: list[dict] = []
    entries: dict[int, dict] = {}
    for entry in (plan or {}).get("shots") or []:
        if not isinstance(entry, dict):
            continue
        try:
            entries[int(entry.get("shot_index"))] = entry
        except (TypeError, ValueError):
            continue
    if only_indices is not None:
        wanted = sorted(int(i) for i in only_indices)
    elif isinstance(script, dict):
        wanted = sorted({
            int(shot.get("index"))
            for shot in (script.get("shots") or [])
            if isinstance(shot, dict) and shot.get("index") is not None
        })
    else:
        wanted = sorted(entries)
    for idx in wanted:
        entry = entries.get(idx)
        if entry is None:
            issues.append({"code": "plan_coverage_missing_shot", "severity": "reject",
                           "path": f"visual_plan.shots[{idx}]",
                           "message": f"镜头 {idx} 不在画面计划内，计划模式不得回退原稿"})
            continue
        if not frames:
            continue
        labels = {str(f.get("frame")) for f in entry.get("frames") or []
                  if isinstance(f, dict)}
        kinds = {str(f.get("kind")) for f in entry.get("frames") or []
                 if isinstance(f, dict)}
        for token in frames:
            token = str(token)
            if token in ("ff", "lf"):
                ok = token in labels
            elif token == "kf":
                ok = "kf" in kinds
            elif token.startswith("kf"):
                ok = token in labels
            else:
                ok = True
            if not ok:
                issues.append({"code": "plan_coverage_missing_frame",
                               "severity": "reject",
                               "path": f"visual_plan.shots[{idx}].frames",
                               "message": f"镜头 {idx} 的计划里没有请求的帧 {token}"})
    return issues


def write_visual_plan(plan: dict, path: Path) -> Path:
    """原子写候选计划（只写调用方显式给出的路径）."""
    from shared.enhancement_io import commit_json_swap, prepare_json_swap
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    commit_json_swap(target, prepare_json_swap(target, plan, ensure_ascii=False, indent=2))
    return target


# ---------------------------------------------------------------------------
# 生成后的观测核对（显式观测，不假装视觉模型）
# ---------------------------------------------------------------------------

def verify_frame_observations(plan: dict, observations: list[dict]) -> dict:
    """把显式观测记录与计划逐帧对照，返回 ``{checks, attempts}``.

    observation 形状::

        {"frame_id": "shot-2:ff",
         "visible_characters": [...], "visible_props": [...],
         "reference_sha256": {"design_reference": "..."},
         "composition": "..."}

    单个帧失败只让该帧 attempt 失效；其它已验证帧按 frame_id 保留。
    """
    by_id = {frame.get("frame_id"): frame for frame in plan.get("frames") or []}
    attempts: dict[str, dict] = {}
    issues: list[dict] = []
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        frame_id = str(obs.get("frame_id") or "")
        frame = by_id.get(frame_id)
        if frame is None:
            issues.append({"code": "observation_unknown_frame", "path": frame_id,
                           "message": "观测记录指向计划中不存在的帧"})
            continue
        frame_issues: list[dict] = []
        expected_chars = set(frame.get("visible_characters") or [])
        expected_props = set(frame.get("visible_props") or [])
        observed_chars = {_text(c) for c in (obs.get("visible_characters") or [])
                          if _text(c)}
        observed_props = {_text(p) for p in (obs.get("visible_props") or []) if _text(p)}
        for missing in sorted(expected_chars - observed_chars):
            frame_issues.append({"code": "required_character_missing", "path": frame_id,
                                 "message": f"计划要求 {missing!r} 可见，观测未检出",
                                 "expected": missing})
        for missing in sorted(expected_props - observed_props):
            frame_issues.append({"code": "required_prop_missing", "path": frame_id,
                                 "message": f"计划要求道具 {missing!r} 可见，观测未检出",
                                 "expected": missing})
        offscreen = set(frame.get("offscreen_speakers") or [])
        for leaked in sorted(offscreen & observed_chars):
            frame_issues.append({"code": "offscreen_speaker_visible", "path": frame_id,
                                 "message": f"画外说话人 {leaked!r} 被观测为可见",
                                 "speaker": leaked})
        expected_refs = {str(r.get("role")): r.get("sha256")
                         for r in (frame.get("references") or []) if r.get("sha256")}
        for role, observed_sha in (obs.get("reference_sha256") or {}).items():
            expected_sha = expected_refs.get(str(role))
            if expected_sha and observed_sha and expected_sha != str(observed_sha):
                frame_issues.append({"code": "reference_identity_mismatch",
                                     "path": f"{frame_id}.references[{role}]",
                                     "message": f"{role} 参考身份摘要与计划不符",
                                     "expected": expected_sha, "actual": observed_sha})
        if obs.get("composition") and frame.get("responsibility"):
            if not str(obs["composition"]).strip():
                frame_issues.append({"code": "composition_unverified", "path": frame_id,
                                     "message": "观测未给出构图描述"})
        ok = not frame_issues
        attempts[frame_id] = {
            "frame_id": frame_id, "status": "verified" if ok else "failed",
            "issues": frame_issues,
        }
        issues.extend(frame_issues)
    return {
        "checks": [
            {"frame_id": fid, "status": rec["status"],
             "issue_codes": sorted({i["code"] for i in rec["issues"]})}
            for fid, rec in attempts.items()
        ],
        "attempts": attempts,
        "issues": issues,
        "verified_count": sum(1 for a in attempts.values() if a["status"] == "verified"),
        "failed_count": sum(1 for a in attempts.values() if a["status"] == "failed"),
    }


def apply_frame_observations(plan: dict, observations: list[dict]) -> dict:
    """把观测结果写回计划副本；失败帧标 failed，其它帧的已验证状态保留."""
    import copy
    updated = copy.deepcopy(plan)
    result = verify_frame_observations(updated, observations)
    by_shot = {s.get("shot_index"): s for s in updated.get("shots") or []}
    for frame in updated.get("frames") or []:
        frame_id = frame.get("frame_id")
        attempt = result["attempts"].get(frame_id)
        if attempt is None:
            continue
        frame["verification"] = attempt
        if attempt["status"] == "failed":
            frame["status"] = "failed"
            shot = by_shot.get(frame.get("shot_index"))
            if shot is not None:
                shot["status"] = "failed_attempt"
    updated["verification"] = {k: v for k, v in result.items() if k != "attempts"}
    updated["unverified"] = [u for u in updated.get("unverified") or []
                             if u.get("path") not in result["attempts"]]
    return updated


__all__ = [
    "VISUAL_PLAN_SCHEMA", "VISUAL_PLAN_STAGE", "VISUAL_ALLOWED_CHANGES",
    "VisualPlanError", "build_visual_plan", "validate_plan_identity",
    "write_visual_plan", "verify_frame_observations", "apply_frame_observations",
]
