"""Step02b video-prompt quality gate.

Ownership contract:
- Step01 owns story facts, image-safe frame fields, and timing structure.
- Seedream skill governs image prompt construction (step02a).
- Seedance skill governs video prompt construction (step02b): duration,
  motion, camera, transitions, fixed-camera and audio capabilities belong here.

This module checks the prompt actually built for video generation, not the
legacy Step01 aggregate prompt.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.json_io import atomic_dump_json
from shared.model_caps import active_video_duration_cap, active_video_prompt_cap
from shared.paths import chapter_dir
from shared.quality_gate import attach_gate_metadata


_MOVEMENT_WORDS = (
    "推镜", "推近", "拉镜", "拉远", "跟拍", "摇镜", "移镜", "环绕",
    "仰拍", "俯拍", "固定机位", "固定镜头",
)


def collect_video_prompt_quality(script: dict) -> dict:
    """Build video prompts and return/write non-blocking video QA report."""
    from step02_video_gen.prompt_builder import _build_prompt, _build_seedance_concise_prompt
    from shared.model_caps import active_video_is_seedance

    work = copy.deepcopy(script)
    shots = list(work.get("shots") or [])
    hook = work.get("hook")
    targets = ([hook] if isinstance(hook, dict) else []) + shots

    warnings: list[dict] = []
    prompt_cap = active_video_prompt_cap() or 0
    duration_cap = active_video_duration_cap() or 0

    for shot in targets:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("index", 0)
        try:
            if active_video_is_seedance():
                prompt = _build_seedance_concise_prompt(shot)
                if prompt_cap:
                    from _video_gen.volcengine_provider import _seedance_compress_prompt
                    prompt = _seedance_compress_prompt(
                        prompt, max_chars=max(200, prompt_cap - 20)
                    )
            else:
                prompt = _build_prompt(shot, work, mode="A")
        except Exception as exc:
            warnings.append({"category": "video_prompt_build", "shot": idx,
                             "message": str(exc)})
            continue
        if prompt_cap and len(prompt) > prompt_cap:
            warnings.append({
                "category": "video_prompt_length",
                "shot": idx,
                "message": f"video prompt {len(prompt)} > cap {prompt_cap}",
            })
        moves = sorted({w for w in _MOVEMENT_WORDS if w in prompt})
        if len(moves) > 1:
            warnings.append({
                "category": "video_camera_stacking",
                "shot": idx,
                "message": f"single shot has multiple camera movements: {moves}",
            })
        try:
            duration = float(shot.get("duration_sec") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration_cap and duration > duration_cap:
            warnings.append({
                "category": "video_duration_cap",
                "shot": idx,
                "message": f"duration {duration:.2f}s > model cap {duration_cap}s",
            })

    for prev, curr in zip(shots, shots[1:]):
        if not isinstance(prev, dict) or not isinstance(curr, dict):
            continue
        prev_scene = str(prev.get("scene") or prev.get("location") or "")
        curr_scene = str(curr.get("scene") or curr.get("location") or "")
        if prev_scene and curr_scene and prev_scene != curr_scene:
            transition = str(curr.get("clip_transition") or "").strip()
            if not transition:
                warnings.append({
                    "category": "video_transition_missing",
                    "shot": curr.get("index"),
                    "message": f"scene switch {prev_scene} -> {curr_scene} has no clip_transition",
                })

    payload = {
        "warning_count": len(warnings),
        "warnings": warnings,
        "source": "step02b.prompt_builder._build_prompt",
        "report_type": "prompt_precheck",
        "status": "PASS",
        "skill_contract": ["byted-ark-seedance-skill", "byted-ark-seedream-skill"],
    }
    attach_gate_metadata(payload, script, stage='step02b.video_prompt_quality')
    atomic_dump_json(chapter_dir() / "step02b_prompt_quality.json", payload,
                     ensure_ascii=False, indent=2)
    return payload


def _file_digest(path: Path) -> str:
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_video_product_quality(script: dict, work_dir: Path) -> dict:
    """Validate actual Step02b video products and write acceptance report."""
    from pathlib import Path as _Path
    from shared.paths import SHOT_VIDEO_FILENAME
    from shared.audio_policy import resolve_audio_policy
    from step04_merge.stretch import get_media_duration, has_audio_stream
    from step01_script_gen.quality_issues import RULE_VERSION

    policy = resolve_audio_policy()
    require_audio = policy.effective_merge_mode == "native"
    units = []
    if isinstance(script.get("hook"), dict):
        units.append(script["hook"])
    units.extend([s for s in (script.get("shots") or []) if isinstance(s, dict)])
    missing = []
    outputs = {}
    for shot in units:
        idx = shot.get("index", 0)
        video = _Path(work_dir) / "shots" / str(idx) / SHOT_VIDEO_FILENAME
        if not video.exists() or video.stat().st_size <= 20 * 1024:
            missing.append(f"shot {idx}: missing/too small {video.name}")
            continue
        duration = get_media_duration(video)
        if duration <= 0:
            missing.append(f"shot {idx}: duration probe failed")
        if require_audio and not has_audio_stream(video):
            missing.append(f"shot {idx}: missing audio stream")
        outputs[str(idx)] = {
            "video": {"path": str(video), "sha1": _file_digest(video)},
            "duration_sec": round(float(duration or 0), 3),
            "has_audio": bool(has_audio_stream(video)),
        }
    payload = {
        "source": "step02b.product_acceptance",
        "report_type": "product_acceptance",
        "rule_version": RULE_VERSION,
        "status": "BLOCK" if missing else "PASS",
        "blocking": bool(missing),
        "missing": missing,
        "outputs": outputs,
    }
    attach_gate_metadata(payload, script, stage="step02b.product_acceptance")
    atomic_dump_json(chapter_dir() / "step02b_quality.json", payload,
                     ensure_ascii=False, indent=2)
    return payload
