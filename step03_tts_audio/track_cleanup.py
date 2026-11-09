# -*- coding: utf-8 -*-
"""Step03 精准分轨与脏轨清理（M4，2026-11-09）.

**精准分轨**：期望音轨集合只由生产判据单源给出——

- 对白轨：`plan_feasibility.shot_needs_dialogue_track(shot, plan)`（与 main /
  step04 消费侧同源）；
- 旁白轨：`shot_tts.narration_subset_entries(...)`（与生成器同源，按 N 编号）。

因此不会把「纯画面镜」误判成缺对白轨，也不会把跨镜旁白拆成多条。

**脏轨清理**：只处理本模块管理的命名（`shot_<n>.mp3` / `narration_<NN>.mp3`）
及其音频身份边车（`.identity.json` / `.voice`）。默认 **dry-run**，只返回计划；
显式 apply 时把脏轨**移入隔离目录**并写 manifest——不直接删除，旧音轨仍可人工
找回。正式稿、图片缓存、TTS 缓存不会被本模块改写。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from shared.paths import (NARRATION_AUDIO_FILENAME_FMT, SHOT_AUDIO_FILENAME_FMT,
                          audio_dir)

SIDECAR_SUFFIXES = (".identity.json", ".voice")
SCHEMA = "step03_track_cleanup.v1"


def expected_tracks(script: dict, *, only_indices=None,
                    full_shots: list | None = None) -> dict:
    """返回 `{"dialogue": {文件名: 镜头号}, "narration": {文件名: N}}`.

    只规划、不读盘、不调模型。
    """
    from step01_script_gen.plan_feasibility import shot_needs_dialogue_track
    from step03_tts_audio.shot_tts import narration_subset_entries

    plan = script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    dialogue: dict[str, int] = {}
    for shot in shots:
        idx = int(shot.get("index", 0))
        if only_indices is not None and idx not in only_indices:
            continue
        if shot_needs_dialogue_track(shot, plan):
            dialogue[SHOT_AUDIO_FILENAME_FMT.format(idx)] = idx
    narration: dict[str, int] = {}
    subset_shots = full_shots if full_shots is not None else shots
    for index, _entry in narration_subset_entries(plan, subset_shots, only_indices):
        narration[NARRATION_AUDIO_FILENAME_FMT.format(int(index))] = int(index)
    return {"dialogue": dialogue, "narration": narration}


def _is_managed(name: str) -> bool:
    name = str(name)
    if not name.endswith(".mp3"):
        return False
    stem = name[:-4]
    if stem.startswith("shot_"):
        return stem[5:].isdigit()
    if stem.startswith("narration_"):
        return stem[10:].isdigit()
    return False


def _sidecar_paths(media: Path) -> list[Path]:
    return [media.with_name(media.name + suffix) for suffix in SIDECAR_SUFFIXES]


def plan_cleanup(directory, expected: dict) -> dict:
    """扫描管理命名，返回 keep / dirty（含边车）与孤儿边车.

    不写盘。`expected` 是 :func:`expected_tracks` 的结果。
    """
    root = Path(directory)
    expected_names = set((expected.get("dialogue") or {}).keys()) \
        | set((expected.get("narration") or {}).keys())
    keep: list[str] = []
    dirty: list[dict] = []
    present: set[str] = set()
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            name = path.name
            if _is_managed(name):
                present.add(name)
                if name in expected_names:
                    keep.append(name)
                else:
                    dirty.append({
                        "path": str(path),
                        "name": name,
                        "kind": "dialogue" if name.startswith("shot_") else "narration",
                        "reason": "orphan_track",
                        "sidecars": [str(p) for p in _sidecar_paths(path) if p.exists()],
                    })
    # 媒体已不存在、但边车还在 → 也是脏轨（按边车名归组）。
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if not path.is_file():
            continue
        for suffix in SIDECAR_SUFFIXES:
            if path.name.endswith(suffix):
                media_name = path.name[:-len(suffix)]
                if _is_managed(media_name) and media_name not in present:
                    dirty.append({
                        "path": str(path),
                        "name": path.name,
                        "kind": "sidecar",
                        "reason": "orphan_sidecar",
                        "sidecars": [],
                    })
                break
    missing = sorted(expected_names - present)
    return {
        "schema": SCHEMA,
        "directory": str(root),
        "expected": sorted(expected_names),
        "keep": sorted(keep),
        "dirty": dirty,
        "missing": missing,
        "dry_run": True,
    }


def apply_cleanup(plan: dict, quarantine_dir) -> dict:
    """把计划里的脏轨**移动**到隔离目录并写 manifest；不删除任何文件."""
    root = Path(quarantine_dir)
    root.mkdir(parents=True, exist_ok=True)
    moved: list[dict] = []
    for item in plan.get("dirty") or []:
        for raw in [item.get("path")] + list(item.get("sidecars") or []):
            if not raw:
                continue
            src = Path(raw)
            if not src.exists():
                continue
            dst = root / src.name
            if dst.exists():
                dst = root / ("%s.%d%s" % (src.stem, int(time.time() * 1000),
                                            src.suffix))
            os.replace(src, dst)
            moved.append({"from": str(src), "to": str(dst)})
    manifest = {
        "schema": SCHEMA,
        "source": plan.get("directory"),
        "quarantine": str(root),
        "moved": moved,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "expected": plan.get("expected") or [],
        "missing": plan.get("missing") or [],
    }
    manifest_path = root / "cleanup_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return manifest


def run(script: dict, *, directory=None, only_indices=None,
        full_shots: list | None = None, apply: bool = False,
        quarantine_dir=None) -> dict:
    """生产入口：算期望轨 → 扫描 → （可选）隔离脏轨。默认 dry-run."""
    root = Path(directory) if directory is not None else audio_dir()
    expected = expected_tracks(script, only_indices=only_indices,
                               full_shots=full_shots)
    plan = plan_cleanup(root, expected)
    if apply and plan["dirty"]:
        quarantine = (Path(quarantine_dir) if quarantine_dir is not None
                      else root / "_dirty_tracks" / time.strftime("%Y%m%d-%H%M%S"))
        plan["applied"] = apply_cleanup(plan, quarantine)
        plan["dry_run"] = False
    return plan


__all__ = ["SCHEMA", "apply_cleanup", "expected_tracks", "plan_cleanup", "run"]
