# -*- coding: utf-8 -*-
"""S02-C2：旧快照过渡（2026-11-17）.

受控复现 479062b **之前** 的 key 算法（``requirements`` 原样、含
``execution.audio.policy.reason``），验证：
- 完整旧快照 + 当前冻结请求 ⇒ ``mismatch``（不是 legacy）；
- 快照缺失 ⇒ ``legacy``（旧兼容）；``require_exact`` 下 legacy 拒绝；
- 快照损坏 ⇒ ``mismatch``；
- 读路径不改写快照字节；``reason`` 仍完整可读。

旧算法只在测试里复现，不依赖旧代码仍存在。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from shared.enhancement_io import canonical_json_digest
from step02_video_gen import submit_poll as sp
from step02_video_gen.request_plan import FrozenVideoRequest

SHOT_INDEX = 1


def _shot() -> dict:
    return {"index": SHOT_INDEX, "prompt": "甲转身", "ff_desc": "甲站",
            "lf_desc": "甲走", "duration_sec": 5.0}


def _script() -> dict:
    return {"characters": [], "shots": [_shot()]}


def _frozen() -> FrozenVideoRequest:
    frozen = FrozenVideoRequest(
        request_id="req-legacy", provider="stub", model_id="m", mode="image",
        prompt="p", parameters={"duration": 5}, references=[],
        source={"scope": {"chapter": 1}, "script_revision": "rev",
                "shot_id": "shot-1"},
        requirements={"execution": {"audio": {"policy": {
            "reason": "video model m has no native audio",
            "effective_merge_mode": "tts_mix"}}}})
    frozen.compute_key()
    return frozen


def _legacy_key(frozen: FrozenVideoRequest) -> str:
    """复现 479062b 之前的算法：requirements 原样进 key（含 reason）。"""
    facts = frozen.key_facts()
    facts["requirements"] = copy.deepcopy(frozen.requirements)
    return canonical_json_digest(facts)


def _paths(tmp_path) -> tuple[Path, Path, Path]:
    shot_dir = tmp_path / "1"
    shot_dir.mkdir()
    (shot_dir / "video.mp4").write_bytes(b"v" * (21 * 1024))
    clip = tmp_path / "clip_1.mp4"
    clip.write_bytes(b"c" * (21 * 1024))
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(_shot(), _script(), shot_dir=shot_dir), encoding="utf-8")
    return shot_dir, shot_dir / "video.mp4", clip


def _write_snapshot(shot_dir: Path, frozen: FrozenVideoRequest, key: str) -> Path:
    data = frozen.to_dict()
    data["request_key"] = key
    path = shot_dir / sp.FROZEN_SNAPSHOT_FILENAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decide(shot_dir: Path, video: Path, clip: Path, *, frozen=None, require_exact=False):
    return sp.cache_decision(_shot(), _script(), shot_dir, video, clip_path=clip,
                             frozen_request=frozen, require_exact=require_exact)


def test_legacy_snapshot_is_mismatch_and_bytes_unchanged(tmp_path):
    shot_dir, video, clip = _paths(tmp_path)
    frozen = _frozen()
    assert _legacy_key(frozen) != frozen.request_key
    snap = _write_snapshot(shot_dir, frozen, _legacy_key(frozen))
    before = _sha(snap)

    result = _decide(shot_dir, video, clip, frozen=frozen)

    assert result["hit"] is False and result["relation"] == "mismatch", result
    assert _sha(snap) == before, "读路径不得改写旧快照"
    stored = json.loads(snap.read_text(encoding="utf-8"))
    assert stored["requirements"]["execution"]["audio"]["policy"]["reason"]


def test_missing_snapshot_is_legacy_hit(tmp_path):
    shot_dir, video, clip = _paths(tmp_path)
    result = _decide(shot_dir, video, clip)
    assert result["hit"] is True and result["relation"] == "legacy", result


def test_missing_snapshot_require_exact_rejects_legacy(tmp_path):
    shot_dir, video, clip = _paths(tmp_path)
    result = _decide(shot_dir, video, clip, require_exact=True)
    assert result["hit"] is False and result["relation"] == "legacy", result


def test_corrupt_snapshot_is_mismatch(tmp_path):
    shot_dir, video, clip = _paths(tmp_path)
    (shot_dir / sp.FROZEN_SNAPSHOT_FILENAME).write_text("{not-json", encoding="utf-8")
    result = _decide(shot_dir, video, clip, frozen=_frozen())
    assert result["hit"] is False and result["relation"] == "mismatch", result
    assert "不可读" in result["reason"]


def test_non_object_snapshot_is_mismatch(tmp_path):
    shot_dir, video, clip = _paths(tmp_path)
    (shot_dir / sp.FROZEN_SNAPSHOT_FILENAME).write_text("[1,2]", encoding="utf-8")
    result = _decide(shot_dir, video, clip, frozen=_frozen())
    assert result["hit"] is False and result["relation"] == "mismatch", result


def test_current_key_snapshot_is_exact(tmp_path):
    shot_dir, video, clip = _paths(tmp_path)
    frozen = _frozen()
    _write_snapshot(shot_dir, frozen, frozen.request_key)
    result = _decide(shot_dir, video, clip, frozen=frozen)
    assert result["hit"] is True and result["relation"] == "exact", result
