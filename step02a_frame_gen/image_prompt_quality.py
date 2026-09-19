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


def collect_image_prompt_quality(script: dict) -> dict:
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
    payload = {
        "warning_count": len(warnings),
        "warnings": warnings,
        "source": "step02a.image_prompt_quality",
        "report_type": "prompt_precheck",
        "status": "PASS",
        "skill_contract": ["byted-ark-seedream-skill", "qianwen-image-generation"],
    }
    attach_gate_metadata(payload, script, stage="step02a.image_prompt_quality")
    atomic_dump_json(chapter_dir() / "step02a_prompt_quality.json", payload, ensure_ascii=False, indent=2)
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


def write_image_product_quality(script: dict, work_dir: Path) -> dict:
    """Validate actual Step02a frame products and write acceptance report."""
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
    from step01_script_gen.quality_issues import RULE_VERSION

    units = []
    if isinstance(script.get("hook"), dict):
        units.append(script["hook"])
    units.extend([s for s in (script.get("shots") or []) if isinstance(s, dict)])
    missing = []
    outputs = {}
    for shot in units:
        idx = shot.get("index", 0)
        shot_dir = Path(work_dir) / "shots" / str(idx)
        ff = shot_dir / FIRST_FRAME_FILENAME
        lf = shot_dir / LAST_FRAME_FILENAME
        kfs = sorted(shot_dir.glob("keyframe_*.png"))
        expected_kfs = sum(1 for k in (shot.get("keyframes") or []) if isinstance(k, dict) and str(k.get("desc") or "").strip())
        if not ff.exists() or ff.stat().st_size <= 0 or not _image_decodable(ff):
            missing.append(f"shot {idx}: missing/undecodable {ff.name}")
        if not lf.exists() or lf.stat().st_size <= 0 or not _image_decodable(lf):
            missing.append(f"shot {idx}: missing/undecodable {lf.name}")
        if len(kfs) < expected_kfs:
            missing.append(f"shot {idx}: keyframes {len(kfs)}/{expected_kfs}")
        for kf in kfs:
            if not _image_decodable(kf):
                missing.append(f"shot {idx}: undecodable {kf.name}")
        outputs[str(idx)] = {
            "ff": {"path": str(ff), "sha1": _file_digest(ff)} if ff.exists() else None,
            "lf": {"path": str(lf), "sha1": _file_digest(lf)} if lf.exists() else None,
            "keyframes": {p.name: {"path": str(p), "sha1": _file_digest(p)} for p in kfs},
        }
    payload = {
        "source": "step02a.product_acceptance",
        "report_type": "product_acceptance",
        "rule_version": RULE_VERSION,
        "status": "BLOCK" if missing else "PASS",
        "blocking": bool(missing),
        "missing": missing,
        "outputs": outputs,
    }
    attach_gate_metadata(payload, script, stage="step02a.product_acceptance")
    atomic_dump_json(chapter_dir() / "step02a_quality.json", payload, ensure_ascii=False, indent=2)
    return payload
