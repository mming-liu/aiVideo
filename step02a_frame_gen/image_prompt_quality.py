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
        "skill_contract": ["byted-ark-seedream-skill", "qianwen-image-generation"],
    }
    attach_gate_metadata(payload, script, stage='step02a.image_prompt_quality')
    atomic_dump_json(chapter_dir() / "step02a_quality.json", payload, ensure_ascii=False, indent=2)
    return payload