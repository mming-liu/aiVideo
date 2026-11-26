"""Step02a image-prompt quality gate.

Ownership contract:
- Step01 provides image-safe facts in ff_desc/lf_desc/keyframes/frame_layouts.
- Seedream and Qwen image skills govern image prompt construction here.
- This gate does not judge camera movement, transition, duration or video audio.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.json_io import atomic_dump_json
from shared.paths import chapter_dir
from shared.quality_gate import attach_gate_metadata

_VIDEO_ONLY_TERMS = ("运镜", "推镜", "推近", "拉镜", "拉远", "跟拍", "环绕镜", "转场", "时长")


def _lint_final_prompt(prompt: str, shot: dict, frame_kind: str) -> list[dict]:
    """S25：对“最终组装 Prompt”做离线体检（不是只查 ff_desc 字段）."""
    import re
    from collections import Counter

    out: list[dict] = []
    text = str(prompt or "")
    if not text:
        return out
    if "无篷" in text or "无船篷" in text:
        out.append({"category": "image_final_prompt_negated_object",
                    "message": "final prompt still contains 无篷/无船篷"})
    if re.search(r"young\s*(男子|女子)", text):
        out.append({"category": "image_final_prompt_untranslated_age",
                    "message": "final prompt contains untranslated young男子/女子"})
    desc = " ".join(str(shot.get(k) or "") for k in ("ff_desc", "lf_desc", "main_subject"))
    if ("披头散发" in text or "后步摇落水" in text) and not any(
            w in desc for w in ("披头散发", "落水", "散发", "凌乱")):
        out.append({"category": "image_final_prompt_stale_state",
                    "message": "final prompt contains later-scene disheveled state"})
    han = re.sub(r"[^\u4e00-\u9fff]", "", text)
    grams = Counter(han[i:i + 4] for i in range(max(0, len(han) - 3)))
    # 2026-11-08（WP2 实测）：13 镜 26 条最终 prompt 的 4-gram 重复次数
    # 中位数就是 3（同一物件在 250 字描述里被自然提到三次），阈值 3 会命中
    # 81% 的合法 prompt。提高到 5 仍然能抓真正的复读（实测最大值 4）。
    repeated = [g for g, n in grams.most_common(5) if n >= 5]
    if repeated:
        out.append({"category": "image_final_prompt_repeated_phrase",
                    "message": f"repeated 4-char phrases: {repeated[:3]}"})
    # 2026-11-08（WP2 实测）：当前组装包含风格前缀 + 角色布局 + 道具布局 +
    # 首尾帧强制约束四块，26 条 prompt 长度 554–1230（中位 965）；800 的旧阈值
    # 会命中 21 条。上限放到 1400：只拦真正失控的拼接。
    if len(text) > 1400:
        out.append({"category": "image_final_prompt_length",
                    "message": f"final prompt {len(text)} > 1400 chars"})
    return out


def collect_image_prompt_quality(script: dict, *, write: bool = True) -> dict:
    warnings: list[dict] = []
    targets = ([script.get("hook")] if isinstance(script.get("hook"), dict) else []) + list(script.get("shots") or [])
    for shot in targets:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("index", 0)
        ff = str(shot.get("ff_desc") or "")
        lf = str(shot.get("lf_desc") or "")
        kfs = [str(k.get("desc") or "") for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
        for field, text in (("ff_desc", ff), ("lf_desc", lf), *[(f"keyframes[{i}].desc", d) for i, d in enumerate(kfs)]):
            if not text:
                continue
            leaked = [w for w in _VIDEO_ONLY_TERMS if w in text]
            if leaked:
                warnings.append({
                    "category": "image_video_directive_leak",
                    "shot": idx,
                    "field": field,
                    "message": f"video-only terms in image prompt: {leaked}",
                })
        if len(ff) > 120:
            warnings.append({"category": "image_ff_desc_length", "shot": idx,
                             "message": f"ff_desc {len(ff)} > 120"})
        if len(lf) > 120:
            warnings.append({"category": "image_lf_desc_length", "shot": idx,
                             "message": f"lf_desc {len(lf)} > 120"})
        # S25：最终组装 Prompt 体检（复用生产组装入口，避免“只查原始字段”）
        from shared.vimax_bridge import compose_frame_prompt
        try:
            chars = script.get("characters") or []
            for kind, field in (("first_frame", "ff_desc"), ("last_frame", "lf_desc")):
                if not str(shot.get(field) or ""):
                    continue
                final = compose_frame_prompt(shot, chars, kind)
                for w in _lint_final_prompt(final, shot, kind):
                    w.update({"shot": idx, "field": f"{field}_full", "frame": kind})
                    warnings.append(w)
                # G01/G02：用事实投影检查未出镜实体是否被写进最终 Prompt。
                from shared.prompt_facts import (
                    project_frame_facts, audit_prompt_against_projection)
                try:
                    proj = project_frame_facts(shot, kind, chars)
                    if proj.get("entities"):
                        for p in audit_prompt_against_projection(final, proj, chars):
                            p.update({"category": "image_final_prompt_offscreen_entity",
                                      "shot": idx, "field": f"{field}_full", "frame": kind})
                            warnings.append(p)
                except Exception as _degrade_exc:
                    from shared.degrade import note_degraded as _note_degraded
                    _note_degraded("step02a_frame_gen/image_prompt_quality.py:collect_image_prompt_quality", _degrade_exc)
        except Exception as _degrade_exc:
            from shared.degrade import note_degraded as _note_degraded
            _note_degraded("step02a_frame_gen/image_prompt_quality.py:collect_image_prompt_quality", _degrade_exc)
    payload = {
        "warning_count": len(warnings),
        "warnings": warnings,
        "source": "step02a.image_prompt_quality",
        "report_type": "prompt_precheck",
        "status": "PASS",
        "skill_contract": ["byted-ark-seedream-skill", "qianwen-image-generation"],
    }
    attach_gate_metadata(payload, script, stage="step02a.image_prompt_quality")
    if write:
        atomic_dump_json(chapter_dir() / "step02a_prompt_quality.json", payload,
                         ensure_ascii=False, indent=2)
    return payload



def _image_decodable(path: Path) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def _file_digest(path: Path) -> str:
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _keyframes_consumed_by_video() -> bool:
    """当前激活的视频模型是否真的消费 keyframe 文件。

    1.0-pro 这类 supports_reference_image=False 的模型，shared.video_media_plan
    会把 keyframe_*.png 全部丢弃（reason=model_unsupported_reference_image）。
    这种情况下，子集运行（--shots N）不该为它要求关键帧。
    不确定时返回 True —— 保持旧的严格行为。
    """
    from shared.video_media_plan import video_consumes_intermediate_frames
    return video_consumes_intermediate_frames()


PRODUCT_ACCEPTANCE_SCHEMA = "step02a.product_acceptance/2"


def build_image_product_quality(script: dict, work_dir: Path, *,
                                 only_indices=None, require_keyframes=None,
                                 media_policy=None) -> dict:
    """Validate actual Step02a frame products, bound QC, and admission separately.

    S05: qc_rejected, qc_not_run, qc_call_failed, or qc_stale blocks admission
    even when all image files exist and decode cleanly.
    """
    from shared.image_qc import build_qc_frame_meta, qc_state
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME, is_core_keyframe_file
    from step01_script_gen.quality_issues import RULE_VERSION

    units = []
    if isinstance(script.get("hook"), dict):
        units.append(script["hook"])
    units.extend([s for s in (script.get("shots") or []) if isinstance(s, dict)])
    if only_indices is not None:
        _want = {int(i) for i in only_indices}
        available = {int(u.get("index", 0)) for u in units}
        if not _want or _want - available:
            raise ValueError(f"invalid shot subset: {sorted(_want - available)}")
        units = [u for u in units if int(u.get("index", 0)) in _want]
    if require_keyframes is None:
        require_keyframes = _keyframes_consumed_by_video()
    # S02：成品验收与生成选择、Step02b 准入共用同一份素材需求。
    from shared.video_media_plan import active_media_policy, required_frames
    policy = media_policy or active_media_policy()
    need = required_frames(policy)
    characters = list(script.get("characters") or [])

    missing = []
    file_missing = []
    qc_failures = []
    outputs = {}
    issues_by_shot = {}

    def _is_frame_locked(shot_dir_obj: Path, role: str, kf_i: int | None = None) -> bool:
        if role == "keyframe" and kf_i is not None:
            names = (f".lock_frame_keyframe_{kf_i}", ".lock_frame_keyframe")
        elif role == "first_frame":
            names = (".lock_frame_first_frame", ".lock_frame_ff")
        elif role == "last_frame":
            names = (".lock_frame_last_frame", ".lock_frame_lf")
        else:
            names = ()
        return any((shot_dir_obj / nm).exists() for nm in names)

    def _check_frame_qc(
        shot_obj: dict, frame_path: Path, frame_kind: str, kf_index: int | None = None,
    ) -> dict:
        meta = build_qc_frame_meta(shot_obj, frame_kind, characters, kf_index=kf_index)
        st = qc_state(frame_path, expected_frame=meta)
        return {
            "status": str(st.get("status") or "qc_not_run"),
            "reason": str(st.get("reason") or ""),
            "sha256": str((st.get("image") or {}).get("sha256") or ""),
            "frame": meta,
        }

    all_required_locked = bool(units)
    for shot in units:
        idx = shot.get("index", 0)
        issue_start = len(missing)
        shot_dir = Path(work_dir) / "shots" / str(idx)
        ff = shot_dir / FIRST_FRAME_FILENAME
        lf = shot_dir / LAST_FRAME_FILENAME

        expected_kfs = sum(1 for k in (shot.get("keyframes") or [])
                           if isinstance(k, dict) and str(k.get("desc") or "").strip())
        kfs = sorted([p for p in shot_dir.glob("keyframe_*.png")
                      if is_core_keyframe_file(p)],
                     key=lambda p: int(p.stem.split("_")[1]))

        ff_file_ok = ff.exists() and ff.stat().st_size > 0 and _image_decodable(ff)
        lf_file_ok = lf.exists() and lf.stat().st_size > 0 and _image_decodable(lf)
        if "ff" in need and not ff_file_ok:
            msg = f"shot {idx}: missing/undecodable {ff.name}"
            missing.append(msg)
            file_missing.append(msg)
        if "lf" in need and not lf_file_ok:
            msg = f"shot {idx}: missing/undecodable {lf.name}"
            missing.append(msg)
            file_missing.append(msg)
        if policy == "reference" and not (ff.exists() or lf.exists() or kfs):
            msg = f"shot {idx}: reference 策略需要 ff/lf/kf 任一素材"
            missing.append(msg)
            file_missing.append(msg)
        _miss_kf = [i for i in range(expected_kfs)
                    if not (shot_dir / ("keyframe_%d.png" % i)).exists()]
        if _miss_kf and require_keyframes:
            msg = (
                f"shot {idx}: 缺关键帧文件 {_miss_kf}（应有 {expected_kfs} 帧，"
                f"实有核心帧 {len(kfs)} 个）")
            missing.append(msg)
            file_missing.append(msg)
        if not require_keyframes:
            kfs = []
        for kf in kfs:
            if not _image_decodable(kf):
                msg = f"shot {idx}: undecodable {kf.name}"
                missing.append(msg)
                file_missing.append(msg)

        qc_states: dict = {}
        locked_map: dict = {}
        if "ff" in need or (policy == "reference" and ff_file_ok):
            locked_map["ff"] = _is_frame_locked(shot_dir, "first_frame")
        if ff_file_ok and ("ff" in need or policy == "reference"):
            ff_qc = _check_frame_qc(shot, ff, "first_frame")
            qc_states["ff"] = ff_qc
            if ff_qc["status"] != "qc_passed":
                msg = (
                    f"shot {idx}: {ff.name} qc={ff_qc['status']}"
                    + (f" ({ff_qc['reason']})" if ff_qc["reason"] else "")
                )
                missing.append(msg)
                qc_failures.append(msg)
        if "lf" in need or (policy == "reference" and lf_file_ok):
            locked_map["lf"] = _is_frame_locked(shot_dir, "last_frame")
        if lf_file_ok and ("lf" in need or policy == "reference"):
            lf_qc = _check_frame_qc(shot, lf, "last_frame")
            qc_states["lf"] = lf_qc
            if lf_qc["status"] != "qc_passed":
                msg = (
                    f"shot {idx}: {lf.name} qc={lf_qc['status']}"
                    + (f" ({lf_qc['reason']})" if lf_qc["reason"] else "")
                )
                missing.append(msg)
                qc_failures.append(msg)
        kf_qc_map: dict[str, dict] = {}
        for kf in kfs:
            if not _image_decodable(kf):
                continue
            try:
                kf_i = int(kf.stem.split("_")[1])
            except (IndexError, ValueError):
                kf_i = None
            locked_map[kf.name] = _is_frame_locked(shot_dir, "keyframe", kf_i)
            kf_qc = _check_frame_qc(shot, kf, "keyframe", kf_index=kf_i)
            kf_qc_map[kf.name] = kf_qc
            if kf_qc["status"] != "qc_passed":
                msg = (
                    f"shot {idx}: {kf.name} qc={kf_qc['status']}"
                    + (f" ({kf_qc['reason']})" if kf_qc["reason"] else "")
                )
                missing.append(msg)
                qc_failures.append(msg)
        if kf_qc_map:
            qc_states["keyframes"] = kf_qc_map

        shot_issues = missing[issue_start:]
        shot_locked = bool(locked_map) and all(locked_map.values())
        if not shot_locked:
            all_required_locked = False
        shot_admitted = not shot_issues
        issues_by_shot[str(idx)] = shot_issues
        outputs[str(idx)] = {
            "ff": {"path": str(ff), "sha1": _file_digest(ff)} if ff.exists() else None,
            "lf": {"path": str(lf), "sha1": _file_digest(lf)} if lf.exists() else None,
            "keyframes": {p.name: {"path": str(p), "sha1": _file_digest(p)} for p in kfs},
            "qc_states": qc_states,
            "locked_human_approved": {"frames": locked_map, "all_required": shot_locked},
            "admitted": shot_admitted,
        }

    status = "BLOCK" if missing else "PASS"
    payload = {
        "schema_version": PRODUCT_ACCEPTANCE_SCHEMA,
        "source": "step02a.product_acceptance",
        "report_type": "product_acceptance",
        "rule_version": RULE_VERSION,
        "status": status,
        "admission_status": status,
        "admitted": status == "PASS",
        "locked_human_approved": all_required_locked,
        "file_ok": not bool(file_missing),
        "qc_ok": not bool(qc_failures),
        "blocking": bool(missing),
        "missing": missing,
        "file_missing": file_missing,
        "qc_failures": qc_failures,
        "outputs": outputs,
        "issues_by_shot": issues_by_shot,
        "scope": {"shots": sorted(int(u.get("index", 0)) for u in units),
                  "keyframes_required": bool(require_keyframes),
                  "media_policy": policy,
                  "requirements": {"frames": list(need),
                                   "keyframes": bool(require_keyframes)}},
    }
    attach_gate_metadata(payload, script, stage="step02a.product_acceptance")
    return payload


def write_image_product_quality(script: dict, work_dir: Path, *,
                                 only_indices=None, require_keyframes=None,
                                 media_policy=None) -> dict:
    """构建并原子写入正式 ``step02a_quality.json``（原入口，签名不变）."""
    payload = build_image_product_quality(
        script, work_dir, only_indices=only_indices,
        require_keyframes=require_keyframes, media_policy=media_policy)
    atomic_dump_json(chapter_dir() / "step02a_quality.json", payload,
                     ensure_ascii=False, indent=2)
    return payload
