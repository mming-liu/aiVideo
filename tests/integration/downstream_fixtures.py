# -*- coding: utf-8 -*-
"""Anonymous, self-contained fixtures & media builders for S01-S07 downstream tests.

All characters, locations, dialogue, and narration are parameterized and strictly
novel-agnostic (no dependence on .env.keys, production output/, or .tmp/).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from shared.json_io import atomic_dump_json
from shared.plan_cache import write_plan_cache
from shared.quality_gate import (
    QUALITY_RULE_VERSION,
    attach_gate_metadata,
    build_step01_commit_manifest,
    compute_script_revision,
    write_step01_commit_manifest,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_tree(root: Path, *, ignore_parts: tuple[str, ...] = ()) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    ignored = set(ignore_parts)
    for p in sorted(root.rglob("*")):
        if ignored:
            rel_parts = p.relative_to(root).parts
            if any(part in ignored for part in rel_parts):
                continue
        if p.is_file():
            out[str(p)] = sha256_file(p)
    return out


def assert_tree_unchanged(
    root: Path,
    before: dict[str, str],
    label: str = "formal",
    *,
    ignore_parts: tuple[str, ...] = (),
) -> None:
    after = snapshot_tree(root, ignore_parts=ignore_parts)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(k for k in (set(before) & set(after)) if before[k] != after[k])
    assert not added and not removed and not modified, (
        f"{label} directory mutated: added={added}, removed={removed}, modified={modified}"
    )


def make_test_png(path: Path, *, size: tuple[int, int] = (128, 192), color: tuple[int, int, int] = (24, 60, 120)) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def make_test_wav(path: Path, seconds: float, *, freq: int = 440, sample_rate: int = 16000) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg executable is required for local test audio synthesis")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={sample_rate}:duration={seconds:.3f}",
            "-c:a", "pcm_s16le", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg wav failed: {proc.stderr[-400:]}")
    return path


def make_test_mp4(
    path: Path,
    seconds: float,
    *,
    color: str = "blue",
    width: int = 360,
    height: int = 640,
    fps: int = 24,
    with_audio: bool = False,
    tone_hz: int = 520,
    black: bool = False,
    freeze: bool = False,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg executable is required for local test video synthesis")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(seconds * fps)))
    if black:
        vsrc = f"color=c=black:s={width}x{height}:r={fps}:d={seconds:.4f}"
    elif freeze:
        vsrc = f"color=c={color}:s={width}x{height}:r={fps}:d={seconds:.4f}"
    else:
        vsrc = f"testsrc2=size={width}x{height}:rate={fps}:duration={seconds:.4f}"
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", vsrc,
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=44100:duration={seconds:.4f}"]
    cmd += [
        "-frames:v", str(frames), "-t", f"{seconds:.4f}",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
        "-b:v", "160k", "-minrate", "80k", "-bufsize", "320k",
    ]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mp4 failed: {proc.stderr[-500:]}")
    return path


def build_anonymous_story_pack(
    variant: str = "compact",
    *,
    chapter: int = 1,
    book_name: str = "匿名测试卷",
    book_index: int = 1,
    novel_name: str = "匿名样本作品",
) -> dict:
    """Build distinct anonymous chapter scripts for content-agnostic downstream tests."""
    if variant == "compact":
        characters = [
            {"identifier": "甲", "gender": "male", "age_range": "young", "static_features": "方脸短发", "dynamic_features": "青衫"},
            {"identifier": "乙", "gender": "female", "age_range": "young", "static_features": "鹅蛋脸长发", "dynamic_features": "素裙"},
        ]
        shots = [
            {
                "index": 1,
                "duration_sec": 5.0,
                "main_subject": "甲",
                "location": "石桥",
                "prompt": "[中景] 甲站在石桥旁抬头看天色",
                "ff_desc": "甲站在石桥东端",
                "lf_desc": "甲迈步走上石桥",
                "motion_desc": "0-2.5秒：甲原地观察；2.5-5秒：甲迈步上桥",
                "subtitle": "雨快落下来了。",
                "narration_segment": "甲道：雨快落下来了。",
                "clip_transition": "硬切",
                "keyframes": [],
                "plan_refs": {"beats": [1], "lines": [1]},
            },
            {
                "index": 2,
                "duration_sec": 5.0,
                "main_subject": "乙",
                "location": "石桥",
                "prompt": "[近景] 乙在石桥另一侧撑伞回望",
                "ff_desc": "乙在石桥西侧收拢纸伞",
                "lf_desc": "乙转身面向甲点头",
                "motion_desc": "0-2.5秒：乙收伞；2.5-5秒：乙点头致意",
                "subtitle": "我们早点过桥。",
                "narration_segment": "乙道：我们早点过桥。",
                "clip_transition": "硬切",
                "keyframes": [],
                "plan_refs": {"beats": [2], "lines": [2]},
            },
        ]
        outline = {
            "mode": "legacy_audio",
            "dialogue_lines": ["甲道：雨快落下来了。", "乙道：我们早点过桥。"],
            "narration_lines": [],
            "shot_beats": [
                "节拍1：甲判断雨势｜载体：台词1",
                "节拍2：乙回应过桥｜载体：台词2",
            ],
        }
    elif variant == "multi_speaker":
        specs = [
            ("甲", "male", "young"),
            ("乙", "female", "young"),
            ("丙", "male", "middle"),
            ("丁", "female", "middle"),
            ("戊", "male", "old"),
            ("己", "female", "old"),
            ("庚", "male", "young"),
            ("辛", "female", "young"),
        ]
        characters = [
            {"identifier": name, "gender": g, "age_range": age, "static_features": "清晰轮廓", "dynamic_features": "常服"}
            for name, g, age in specs
        ]
        shots = []
        dialogue_lines = []
        beats = []
        adaptations = []
        for idx, (name, _, _) in enumerate(specs, 1):
            line = f"{name}道：第{idx}号门已经打开。"
            spoken = f"第{idx}号门已经打开。"
            dialogue_lines.append(line)
            beats.append(f"节拍{idx}：{name}发言｜载体：台词{idx}")
            adaptations.append({
                "shot_index": idx,
                "entries": [{"kind": "dialogue", "index": idx, "speaker": name, "text": spoken}],
            })
            shots.append({
                "index": idx,
                "duration_sec": 5.0,
                "main_subject": name,
                "location": f"长廊{idx}",
                "prompt": f"[中景] {name}站在长廊{idx}前开口说话",
                "ff_desc": f"{name}站在门前",
                "lf_desc": f"{name}推开门框",
                "motion_desc": f"0-5秒：{name}原地说话并示意",
                "subtitle": spoken,
                "narration_segment": line,
                "clip_transition": "硬切",
                "keyframes": [],
                "plan_refs": {"beats": [idx], "lines": [idx]},
            })
        outline = {
            "mode": "legacy_audio",
            "dialogue_lines": dialogue_lines,
            "narration_lines": [],
            "shot_beats": beats,
        }
    elif variant == "narration_and_dialogue":
        characters = [
            {"identifier": "甲", "gender": "male", "age_range": "middle", "static_features": "高鼻深目", "dynamic_features": "灰袍"},
            {"identifier": "乙", "gender": "female", "age_range": "young", "static_features": "清秀眉眼", "dynamic_features": "蓝衫"},
            {"identifier": "丙", "gender": "male", "age_range": "old", "static_features": "白须长眉", "dynamic_features": "褐衣"},
        ]
        shots = [
            {
                "index": 1,
                "duration_sec": 4.0,
                "main_subject": "空镜",
                "location": "北渡口",
                "prompt": "[远景] 清晨薄雾中的北渡口水面平静",
                "ff_desc": "薄雾笼罩的渡口木桩",
                "lf_desc": "水面泛起一圈波纹",
                "motion_desc": "0-4秒：镜头缓慢横移掠过水面",
                "subtitle": "",
                "narration_segment": "",
                "clip_transition": "硬切",
                "keyframes": [],
                "plan_refs": {"beats": [1], "lines": []},
            },
            {
                "index": 2,
                "duration_sec": 5.0,
                "main_subject": "甲",
                "location": "北渡口",
                "prompt": "[中景] 甲把木箱放上渡船甲板",
                "ff_desc": "甲提着木箱站在岸边",
                "lf_desc": "甲将木箱稳稳放到甲板",
                "motion_desc": "0-2.5秒：甲登船；2.5-5秒：放下木箱",
                "subtitle": "风向变了，按原路撤。",
                "narration_segment": "甲道：风向变了，按原路撤。",
                "clip_transition": "交叉溶解 0.5秒",
                "keyframes": [],
                "plan_refs": {"beats": [2], "lines": [1]},
            },
            {
                "index": 3,
                "duration_sec": 4.5,
                "main_subject": "丙",
                "location": "北渡口",
                "prompt": "[近景] 丙收起缆绳示意开船",
                "ff_desc": "丙双手握住船头缆绳",
                "lf_desc": "丙把缆绳盘好放到船舱边",
                "motion_desc": "0-4.5秒：丙收拢缆绳后退半步",
                "subtitle": "渡船立刻离岸。",
                "narration_segment": "丙道：渡船立刻离岸。",
                "clip_transition": "硬切",
                "keyframes": [],
                "plan_refs": {"beats": [3], "lines": [2]},
            },
        ]
        outline = {
            "mode": "legacy_audio",
            "dialogue_lines": ["甲道：风向变了，按原路撤。", "丙道：渡船立刻离岸。"],
            "narration_lines": [
                {"text": "清晨的渡口尚无人声，薄雾贴着水面流动。", "voice_role": "narrator"},
            ],
            "shot_beats": [
                "节拍1：薄雾渡口开场｜载体：旁白1",
                "节拍2：甲登船定计｜载体：台词1",
                "节拍3：丙收缆离岸｜载体：台词2",
            ],
        }
    else:
        raise ValueError(f"unknown fixture variant: {variant}")

    script = {
        "novel_name": novel_name,
        "book_name": book_name,
        "book_index": int(book_index),
        "chapter_index": int(chapter),
        "chapter_title": f"第{chapter}章 匿名章节",
        "characters": characters,
        "shots": shots,
        "chapter_outline": outline,
    }
    script["script_revision"] = compute_script_revision(script)
    return {
        "variant": variant,
        "scope": {"book_name": book_name, "book_index": int(book_index), "chapter": int(chapter)},
        "script": script,
        "outline": outline,
    }


def install_committed_chapter(ch_dir: Path, pack: dict, *, run_id: str = "20261127T000000-00001") -> dict:
    """Install a full Step01-committed chapter directory using shared.quality_gate + plan_cache."""
    ch_dir = Path(ch_dir)
    ch_dir.mkdir(parents=True, exist_ok=True)
    script = dict(pack["script"])
    scope = dict(pack["scope"])
    revision = compute_script_revision(script)
    script["script_revision"] = revision

    atomic_dump_json(ch_dir / "script_result.json", script, ensure_ascii=False, indent=2)
    write_plan_cache(ch_dir / "chapter_outline.cache.json", pack["outline"], scope=scope, script_revision=revision)

    quality = attach_gate_metadata(
        {"blocking": False, "blocking_issues": [], "warning_count": 0, "warnings": []},
        script,
        stage="step01.quality_gate",
    )
    atomic_dump_json(ch_dir / "step01_quality.json", quality, ensure_ascii=False, indent=2)

    audit = attach_gate_metadata(
        {"status": "ok", "blocking": False, "issues": []},
        script,
        stage="step01.audit",
    )
    atomic_dump_json(ch_dir / "step01_audit.json", audit, ensure_ascii=False, indent=2)

    manifest = build_step01_commit_manifest(
        run_id=run_id,
        created_at="2026-11-27T00:00:00",
        revision=revision,
        rule_source_fingerprint="fixture-rule-fp",
        rule_version=QUALITY_RULE_VERSION,
        audit_scope="final_after_deterministic_fixes",
        chapter_dir=ch_dir,
        status="COMMITTED",
        audit_status="ok",
    )
    write_step01_commit_manifest(ch_dir, manifest)
    return {"chapter_dir": ch_dir, "script": script, "revision": revision, "manifest": manifest}


def install_step02a_acceptance(
    ch_dir: Path,
    script: dict,
    *,
    shot_policies: dict[int, str],
    media_policy: str = "auto",
) -> Path:
    """Write a valid step02a_quality.json and corresponding PNG frames under vimax_working/shots."""
    ch_dir = Path(ch_dir)
    shots_dir = ch_dir / "vimax_working" / "shots"
    outputs: dict = {}
    issues_by_shot: dict = {}
    for idx in sorted(shot_policies):
        pol = shot_policies[idx]
        sdir = shots_dir / str(idx)
        ff = make_test_png(sdir / "first_frame.png", color=(40 + idx * 20, 80, 140))
        entry = {"ff": {"path": str(ff), "sha1": sha1_file(ff)}, "keyframes": {}}
        if pol in ("auto", "first_last", "reference"):
            lf = make_test_png(sdir / "last_frame.png", color=(160, 60 + idx * 15, 50))
            entry["lf"] = {"path": str(lf), "sha1": sha1_file(lf)}
        outputs[str(idx)] = entry
        issues_by_shot[str(idx)] = []
    report = {
        "source": "step02a.product_acceptance",
        "report_type": "product_acceptance",
        "rule_version": QUALITY_RULE_VERSION,
        "status": "PASS",
        "blocking": False,
        "missing": [],
        "outputs": outputs,
        "issues_by_shot": issues_by_shot,
        "scope": {
            "shots": sorted(int(k) for k in shot_policies),
            "keyframes_required": False,
            "media_policy": media_policy,
            "policies": {str(k): v for k, v in sorted(shot_policies.items())},
        },
    }
    attach_gate_metadata(report, script, stage="step02a.product_acceptance")
    report["report_type"] = "product_acceptance"
    out = ch_dir / "step02a_quality.json"
    atomic_dump_json(out, report, ensure_ascii=False, indent=2)
    return out
