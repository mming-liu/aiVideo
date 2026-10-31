"""Explicit, content-independent candidate editing and offline delivery views.

The edit specification is data, not a keyword-based selector. Digests prove
source identity; they do not prove that edited visuals preserve story meaning.
This module neither calls models nor writes or commits files.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math


def object_digest(value) -> str:
    """SHA-256 of canonical UTF-8 JSON (distinct from raw file SHA-256)."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_candidate(plan: dict, source_script: dict, edit_spec: dict) -> tuple[dict, list[dict]]:
    """Apply explicitly supplied target shots after checking frozen sources.

    Each edit has edit_id, sources[{index,sha256}], reason and target. Source
    reuse is legal for splits, multiple sources for merges; each target is
    unique. The caller must separately check voice/semantic contracts and run
    finalize. No durations, keyframes or prose are silently repaired here.
    """
    def reject(code, path, detail):
        return {}, [{"code": code, "field_path": path, "detail": detail}]

    if not all(isinstance(x, dict) for x in (plan, source_script, edit_spec)):
        return reject("invalid_edit_spec", "", "Expected objects")
    if edit_spec.get("schema") != "candidate_edit.v1":
        return reject("invalid_edit_spec", "schema", "Unsupported schema")
    for name, value in (("plan", plan), ("source", source_script)):
        if edit_spec.get(name + "_sha256") != object_digest(value):
            return reject(name + "_digest_mismatch", name, "Frozen object changed")
    originals = source_script.get("shots")
    if not isinstance(originals, list) or not originals:
        return reject("invalid_source", "shots", "Expected nonempty shots")
    by_id = {}
    for shot in originals:
        if not isinstance(shot, dict) or type(shot.get("index")) is not int:
            return reject("invalid_source", "shots.index", "Expected integer identity")
        if shot["index"] in by_id:
            return reject("duplicate_source", "shots.index", str(shot["index"]))
        by_id[shot["index"]] = shot
    edits = edit_spec.get("shots")
    if not isinstance(edits, list) or not edits:
        return reject("invalid_edit_spec", "shots", "Expected explicit nonempty edits")
    targets, seen, ids = [], set(), set()
    for n, edit in enumerate(edits):
        path = f"shots[{n}]"
        if not isinstance(edit, dict):
            return reject("invalid_edit_spec", path, "Expected edit object")
        eid = edit.get("edit_id")
        target = edit.get("target")
        if not isinstance(eid, str) or not eid.strip() or not isinstance(target, dict):
            return reject("invalid_edit_spec", path, "Missing edit identity or target")
        index = target.get("index")
        if eid in ids or (type(index) is int and index in seen):
            return reject("duplicate_target", path, eid)
        if type(index) is not int or index != n + 1:
            return reject("invalid_target", path + ".index", "Targets must be sequential")
        duration = target.get("duration_sec")
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            return reject("invalid_target", path + ".duration_sec", "Expected finite positive duration")
        refs = edit.get("sources")
        if not isinstance(refs, list) or not refs:
            return reject("source_identity_missing", path + ".sources", "No explicit source")
        if not isinstance(edit.get("reason"), str) or not edit["reason"].strip():
            return reject("invalid_edit_spec", path + ".reason", "Missing edit rationale")
        local_sources = set()
        for ref in refs:
            sid = ref.get("index") if isinstance(ref, dict) else None
            if type(sid) is not int or sid not in by_id:
                return reject("source_identity_missing", path + ".sources", str(sid))
            if sid in local_sources:
                return reject("duplicate_source", path + ".sources", str(sid))
            local_sources.add(sid)
            if ref.get("sha256") != object_digest(by_id[sid]):
                return reject("source_identity_mismatch", path + ".sources", str(sid))
        ids.add(eid)
        seen.add(index)
        targets.append(deepcopy(target))
    result = deepcopy(source_script)
    result["shots"] = targets
    result["chapter_outline"] = deepcopy(plan)
    if "hook" in edit_spec:
        result["hook"] = deepcopy(edit_spec["hook"])
    result.pop("timeline", None)
    return result, []


def finalize_candidate(candidate, finalize, strip_runtime):
    """Run the real finalizer twice; errors or persisted-field drift reject.

    This proves finalizer stability only, not semantic audit or commit success.
    The caller supplies the existing finalizer and runtime-field projection.
    """
    current, errors = finalize(deepcopy(candidate))
    if errors:
        raise ValueError("finalize_errors: " + json.dumps(errors, ensure_ascii=False))
    first = deepcopy(strip_runtime(current))
    second, errors = finalize(deepcopy(first))
    if errors:
        raise ValueError("finalize_errors: " + json.dumps(errors, ensure_ascii=False))
    second = strip_runtime(second)
    if object_digest(first) != object_digest(second):
        raise ValueError("finalize_drift: persisted candidate changed after reload")
    return first


def reading_markdown(script, *, revision: str) -> str:
    """Render complete final fields; absolute time includes the hook."""
    hook = script.get("hook") or {}
    offset = float(hook.get("duration_sec") or 0)
    total = sum(float(s["duration_sec"]) for s in script["shots"])
    rows = ["# 候选阅读稿", "", f"- script_revision：{revision}",
            f"- 正片：{total:.2f} s；钩子：{offset:.2f} s。",
            "- 绝对时间含钩子；正片相对时间扣除钩子。语义、声源及制作质量须单独验收。", ""]
    for shot in script["shots"]:
        tc = shot["timecode"]
        start, end = float(tc["start"]), float(tc["end"])
        rows += [f"## 镜 {shot['index']}", "",
                 f"- 绝对时间：{start:.2f}–{end:.2f} s；正片：{start-offset:.2f}–{end-offset:.2f} s",
                 f"- 声音：{shot.get('narration_segment') or '（空）'}",
                 "- 引用：" + json.dumps(shot.get("plan_refs"), ensure_ascii=False)]
        for label, field in (("首帧", "ff_desc"), ("尾帧", "lf_desc"),
                             ("动作", "motion_desc"), ("运镜", "camera_movement"),
                             ("布局", "frame_layouts"), ("关键帧", "keyframes")):
            value = shot.get(field, "")
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            rows.append(f"- {label}：{value}")
        rows.append("")
    if hook:
        rows += ["## 钩子", "", json.dumps(hook, ensure_ascii=False, indent=2), ""]
    return "\n".join(rows)
