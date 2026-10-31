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


def _keyframes_consumed_by_video() -> bool:
    """当前激活的视频模型是否真的消费 keyframe 文件。

    1.0-pro 这类 supports_reference_image=False 的模型，shared.video_media_plan
    会把 keyframe_*.png 全部丢弃（reason=model_unsupported_reference_image）。
    这种情况下，子集运行（--shots N）不该为它要求关键帧。
    不确定时返回 True —— 保持旧的严格行为。
    """
    try:
        from shared.model_caps import active_video_model, get_seedance_cap
        cap = get_seedance_cap(active_video_model())
        return bool(getattr(cap, "supports_reference_image", False))
    except Exception:
        return True


def write_image_product_quality(script: dict, work_dir: Path, *,
                                  only_indices=None, require_keyframes=None) -> dict:
    """Validate actual Step02a frame products and write acceptance report.

    only_indices: 只核验这些镜头（--shots 子集运行）；None=全章。
    require_keyframes: None ⇒ 全章运行按旧行为要求关键帧；子集运行按当前视频
        模型是否消费 keyframe 决定（1.0-pro 不消费则不要求）。
    """
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
    from step01_script_gen.quality_issues import RULE_VERSION

    units = []
    if isinstance(script.get("hook"), dict):
        units.append(script["hook"])
    units.extend([s for s in (script.get("shots") or []) if isinstance(s, dict)])
    if only_indices is not None:
        _want = {int(i) for i in only_indices}
        units = [u for u in units if int(u.get("index", 0)) in _want]
    if require_keyframes is None:
        require_keyframes = True if only_indices is None else _keyframes_consumed_by_video()
    missing = []
    outputs = {}
    for shot in units:
        idx = shot.get("index", 0)
        shot_dir = Path(work_dir) / "shots" / str(idx)
        ff = shot_dir / FIRST_FRAME_FILENAME
        lf = shot_dir / LAST_FRAME_FILENAME
        # v6.110：只认**核心**关键帧文件，并按**序号逐一核对**。
        #
        # 原实现有两个漏洞（实测 shot 3 缺 `keyframe_0.png` 却报 PASS）：
        #   ① `keyframe_*.png` 把二修标注图 `keyframe_1_doodle.png` 也算进来 →
        #      数量凑够，缺图被掩盖；
        #   ② 只比**数量**不比**序号** → 即便没有 doodle，「缺 0 号、多一个 4 号」
        #      这种错位也照样通过。
        from shared.paths import is_core_keyframe_file
        expected_kfs = sum(1 for k in (shot.get("keyframes") or [])
                           if isinstance(k, dict) and str(k.get("desc") or "").strip())
        kfs = sorted([p for p in shot_dir.glob("keyframe_*.png")
                      if is_core_keyframe_file(p)],
                     key=lambda p: int(p.stem.split("_")[1]))
        if not ff.exists() or ff.stat().st_size <= 0 or not _image_decodable(ff):
            missing.append(f"shot {idx}: missing/undecodable {ff.name}")
        if not lf.exists() or lf.stat().st_size <= 0 or not _image_decodable(lf):
            missing.append(f"shot {idx}: missing/undecodable {lf.name}")
        _miss_kf = [i for i in range(expected_kfs)
                    if not (shot_dir / ("keyframe_%d.png" % i)).exists()]
        if _miss_kf and require_keyframes:
            missing.append(
                f"shot {idx}: 缺关键帧文件 {_miss_kf}（应有 {expected_kfs} 帧，"
                f"实有核心帧 {len(kfs)} 个）")
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
        "scope": {"shots": sorted(int(u.get("index", 0)) for u in units),
                  "keyframes_required": bool(require_keyframes)},
    }
    attach_gate_metadata(payload, script, stage="step02a.product_acceptance")
    atomic_dump_json(chapter_dir() / "step02a_quality.json", payload, ensure_ascii=False, indent=2)
    return payload
