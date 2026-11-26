# -*- coding: utf-8 -*-
"""step02a --verify-only：零生成、零视觉 QC 的产品验收重建/合并（2026-11-18 O2）.

用途：正式章已有**锁定且通过 QC** 的帧资产时，不重新生图、不跑视觉 QC，仅按
``build_image_product_quality`` 的同一判据重建逐镜验收，并合并为一份支持
**逐镜策略**的 ``step02a_quality.json``（例如 shot 1 `auto`、shot 2 `first_only`），
从而让 Step02b 的 ``_assert_step02a_scope`` 能按镜头放行。

安全边界：
- 只读帧/QC/锁图；写入前备份原报告；
- 任一镜缺锁、QC 未通过或产物摘要不匹配 ⇒ 整体拒绝，不写半份报告；
- 不调用任何 provider / 视觉 QC / ffprobe 之外的媒体探测。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from shared.json_io import atomic_dump_json
from shared.paths import chapter_dir


def _shot_dir(work_dir: Path, idx: int) -> Path:
    return Path(work_dir) / "shots" / str(int(idx))


def _lock_names(role: str) -> tuple[str, ...]:
    if role == "first_frame":
        return (".lock_frame_first_frame", ".lock_frame")
    if role == "last_frame":
        return (".lock_frame_last_frame", ".lock_frame")
    return (".lock_frame",)


def _is_locked(shot_dir: Path, role: str) -> bool:
    return any((shot_dir / name).exists() for name in _lock_names(role))


def _qc_passed(
    shot_dir: Path,
    role: str,
    *,
    expected_frame: dict | None = None,
) -> bool:
    from shared.image_qc import qc_state
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME

    filename = {
        "first_frame": FIRST_FRAME_FILENAME,
        "last_frame": LAST_FRAME_FILENAME,
    }.get(role)
    if filename is None:
        return True
    st = qc_state(shot_dir / filename, expected_frame=expected_frame)
    return st.get("status") == "qc_passed"


def _roles_for_outputs(outputs: dict) -> list[str]:
    roles = []
    if outputs.get("ff"):
        roles.append("first_frame")
    if outputs.get("lf"):
        roles.append("last_frame")
    for _name in (outputs.get("keyframes") or {}):
        roles.append("keyframe")
    return roles


def _verify_shot(
    payload: dict,
    idx: int,
    work_dir: Path,
    *,
    shot: dict | None = None,
    characters: list | None = None,
) -> list[str]:
    from shared.image_qc import build_qc_frame_meta

    problems = []
    outputs = (payload.get("outputs") or {}).get(str(idx)) or {}
    if payload.get("status") != "PASS":
        problems.append("shot %s acceptance=%s" % (idx, payload.get("status")))
    if not outputs:
        problems.append("shot %s acceptance has no outputs" % idx)
        return problems
    shot_dir = _shot_dir(work_dir, idx)
    # 只核**该镜策略要求**的帧：first_only 下存在的 lf 不参与验收，也不要求锁。
    _need = list(((payload.get("scope") or {}).get("requirements") or {}).get("frames") or [])
    _roles = [{"ff": "first_frame", "lf": "last_frame"}[f] for f in _need if f in ("ff", "lf")]
    for role in _roles:
        if not _is_locked(shot_dir, role):
            problems.append("shot %s %s not locked" % (idx, role))
        exp = build_qc_frame_meta(shot, role, characters) if isinstance(shot, dict) else None
        if not _qc_passed(shot_dir, role, expected_frame=exp):
            problems.append("shot %s %s QC not passed" % (idx, role))
    return problems


def merge_acceptances(payloads: dict[int, dict], policies: dict[int, str],
                      script: dict) -> dict:
    """把逐镜 payload 合并成支持逐镜策略的一份验收报告."""
    base = payloads[sorted(payloads)[0]]
    merged = {k: v for k, v in base.items()
              if k not in ("outputs", "issues_by_shot", "scope", "status",
                           "blocking", "missing")}
    outputs = {}
    issues_by_shot = {}
    requirements = {}
    for idx, payload in sorted(payloads.items()):
        outputs[str(idx)] = (payload.get("outputs") or {}).get(str(idx)) or {}
        issues_by_shot[str(idx)] = (payload.get("issues_by_shot") or {}).get(str(idx), [])
        requirements[str(idx)] = (payload.get("scope") or {}).get("requirements") or {}
    merged.update({
        "status": "PASS" if all(p.get("status") == "PASS" for p in payloads.values()) else "BLOCK",
        "admitted": all(p.get("status") == "PASS" for p in payloads.values()),
        "locked_human_approved": all(bool(p.get("locked_human_approved")) for p in payloads.values()),
        "blocking": any(p.get("blocking") for p in payloads.values()),
        "missing": [m for p in payloads.values() for m in (p.get("missing") or [])],
        "outputs": outputs,
        "issues_by_shot": issues_by_shot,
        "scope": {
            "shots": sorted(int(i) for i in payloads),
            "media_policy": "per_shot",
            "policies": {str(i): policies[i] for i in sorted(policies)},
            "requirements": requirements,
            "keyframes_required": False,
        },
    })
    return merged


def _merge_visual_plan(work_dir: Path, script: dict,
                       policies: dict[int, str]) -> dict:
    """把逐镜画面计划合并进 ``vimax_working/visual_plan.json``（零生成）.

    Step02b 的执行要求按 ``visual_plan.json`` 取本镜条目；只有质量报告覆盖
    shot 2 而画面计划仍只有 shot 1 时，执行仍会被 ``visual_plan_missing`` 拦下。
    """
    from step02a_frame_gen.execution_plan import (
        build_visual_plan, validate_plan_identity,
    )
    from shared.run_scope import current_scope_identity
    from shared.video_media_plan import default_frame_selection

    path = Path(work_dir) / "visual_plan.json"
    existing = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    scope = current_scope_identity()
    base = dict(existing) if isinstance(existing, dict) else {}
    built = []
    for idx, policy in sorted(policies.items()):
        built.append(build_visual_plan(
            script, scope=scope, only_indices={idx},
            frames=default_frame_selection(policy)))
    if not base and built:
        base = dict(built[0])
    shots = {int(s.get("shot_index")): s for s in (base.get("shots") or [])
             if isinstance(s, dict) and s.get("shot_index") is not None}
    frames = {str(f.get("frame_id")): f for f in (base.get("frames") or [])
              if isinstance(f, dict) and f.get("frame_id")}
    # 2026-11-21 R7.2：本次覆盖的镜头只保留**新策略实际要求**的帧。
    # 旧计划可能残留祖先版本留下的 shot-N:lf 等条目（例如 shot 2 已锁 first_only，
    # 计划里却仍有 last_frame）——不剪掉会让执行侧画面计划与锁定策略不一致。
    covered = {int(i) for i in policies}
    required = {str(f.get("frame_id")) for plan in built
                for f in (plan.get("frames") or [])
                if isinstance(f, dict) and f.get("frame_id")}

    def _belongs_to_covered(frame: dict) -> bool:
        try:
            return int(frame.get("shot_index")) in covered
        except (TypeError, ValueError):
            return False

    frames = {fid: f for fid, f in frames.items()
              if fid in required or not _belongs_to_covered(f)}
    for plan in built:
        for s in plan.get("shots") or []:
            if isinstance(s, dict) and s.get("shot_index") is not None:
                shots[int(s["shot_index"])] = s
        for f in plan.get("frames") or []:
            if isinstance(f, dict) and f.get("frame_id"):
                frames[str(f["frame_id"])] = f
    base["shots"] = [shots[k] for k in sorted(shots)]
    base["frames"] = [frames[k] for k in sorted(frames)]
    base["scope"] = scope
    base.setdefault("stage", "step02a_visual_plan")
    issues = validate_plan_identity(base, script, scope=scope)
    if issues:
        raise ValueError("visual_plan invalid: %s" % (issues[:3],))
    return base


def run_verify_only(script_path: Path, work_dir: Path, *,
                    shots: set[int], policies: dict[int, str]) -> int:
    from step02a_frame_gen.image_prompt_quality import build_image_product_quality

    script_path = Path(script_path)
    work_dir = Path(work_dir)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if not shots:
        print(json.dumps({"ok": False, "code": "no_shots"}, ensure_ascii=False))
        return 2
    missing_policies = sorted(set(shots) - set(policies))
    if missing_policies:
        print(json.dumps({"ok": False, "code": "policy_missing",
                          "shots": missing_policies}, ensure_ascii=False))
        return 2

    units_by_idx: dict[int, dict] = {}
    if isinstance(script.get("hook"), dict):
        units_by_idx[int(script["hook"].get("index", 0))] = script["hook"]
    for s in (script.get("shots") or []):
        if isinstance(s, dict) and s.get("index") is not None:
            units_by_idx[int(s["index"])] = s
    characters = list(script.get("characters") or [])

    payloads: dict[int, dict] = {}
    problems: list[str] = []
    for idx in sorted(shots):
        payload = build_image_product_quality(
            script, work_dir, only_indices={idx},
            media_policy=policies[idx], require_keyframes=False)
        payloads[idx] = payload
        problems.extend(_verify_shot(
            payload, idx, work_dir, shot=units_by_idx.get(idx), characters=characters,
        ))
    if problems:
        print(json.dumps({"ok": False, "code": "verify_failed",
                          "problems": problems, "model_sends": 0},
                         ensure_ascii=False))
        return 1

    try:
        visual = _merge_visual_plan(work_dir, script, policies)
    except Exception as exc:  # noqa: BLE001 - 计划合并失败必须拒绝，不写半份准入
        print(json.dumps({"ok": False, "code": "visual_plan_invalid",
                          "error": str(exc)[:500], "model_sends": 0},
                         ensure_ascii=False))
        return 1

    merged = merge_acceptances(payloads, policies, script)
    target = chapter_dir() / "step02a_quality.json"
    visual_target = Path(work_dir) / "visual_plan.json"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    bdir = chapter_dir() / "_enhancement" / "verify_only"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.is_file():
        backup = bdir / ("step02a_quality_%s.json" % stamp)
        shutil.copy2(target, backup)
    visual_backup = None
    if visual_target.is_file():
        visual_backup = bdir / ("visual_plan_%s.json" % stamp)
        shutil.copy2(visual_target, visual_backup)
    atomic_dump_json(target, merged, ensure_ascii=False, indent=2)
    atomic_dump_json(visual_target, visual, ensure_ascii=False, indent=2)
    print(json.dumps({
        "ok": True, "report": str(target), "backup": str(backup) if backup else None,
        "visual_plan": str(visual_target),
        "visual_backup": str(visual_backup) if visual_backup else None,
        "status": merged["status"], "shots": merged["scope"]["shots"],
        "policies": merged["scope"]["policies"], "model_sends": 0,
        "image_posts": 0, "vision_qc_calls": 0,
    }, ensure_ascii=False))
    return 0
