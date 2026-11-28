# -*- coding: utf-8 -*-
"""Step E：Step 04 正式合成入口的声音准入（四条路径）.

接线点是 ``step04_merge.main._assert_voice_gate``（``_run_merge`` 唯一调用）；
这里直接打这个缝，覆盖正式合成与 ``--dry-run`` 两种模式：

1. ``prepared`` 正式合成 → 拒收；
2. ``confirmed``（同 revision）→ 放行；
3. ``confirmed`` 但 revision 与正式稿不一致 → 拒收；
4. ``--dry-run`` 在 ``prepared`` 下 → 放行（只推演时间线）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import voice_state as vs
from step04_merge.main import _assert_voice_gate


def _write(chapter: Path, *, state: str, revision: str) -> Path:
    chapter.mkdir(parents=True, exist_ok=True)
    target = chapter / vs.FILENAME
    target.write_text(json.dumps({
        "schema": vs.SCHEMA, "state": state, "script_revision": revision,
        "units": [{"unit_id": "shot-1", "state": state, "sha256": "a" * 64}],
        "blocking": [] if state == vs.STATE_CONFIRMED else [{"code": "not_confirmed"}],
    }, ensure_ascii=False), encoding="utf-8")
    return target


def test_prepared_is_rejected_for_formal_merge(tmp_path):
    _write(tmp_path, state=vs.STATE_PREPARED, revision="rev-1")
    with pytest.raises(vs.VoiceStateError) as excinfo:
        _assert_voice_gate(tmp_path, script_revision="rev-1",
                           voice_required=True, dry_run=False)
    assert excinfo.value.code == "voice_state_not_confirmed"
    assert excinfo.value.exit_code == 2


def test_confirmed_is_allowed(tmp_path):
    _write(tmp_path, state=vs.STATE_CONFIRMED, revision="rev-1")
    report = _assert_voice_gate(tmp_path, script_revision="rev-1",
                               voice_required=True, dry_run=False)
    assert report["status"] == "confirmed"
    assert report["gate"] == "passed"
    assert report["script_revision"] == "rev-1"


def test_revision_drift_is_rejected(tmp_path):
    _write(tmp_path, state=vs.STATE_CONFIRMED, revision="rev-old")
    with pytest.raises(vs.VoiceStateError) as excinfo:
        _assert_voice_gate(tmp_path, script_revision="rev-new",
                           voice_required=True, dry_run=False)
    assert excinfo.value.code == "voice_state_revision_mismatch"


def test_missing_state_is_rejected(tmp_path):
    with pytest.raises(vs.VoiceStateError) as excinfo:
        _assert_voice_gate(tmp_path, script_revision="rev-1",
                           voice_required=True, dry_run=False)
    assert excinfo.value.code == "voice_state_missing"


def test_dry_run_allows_prepared(tmp_path):
    _write(tmp_path, state=vs.STATE_PREPARED, revision="rev-1")
    report = _assert_voice_gate(tmp_path, script_revision="rev-1",
                               voice_required=True, dry_run=True)
    assert report["status"] == "dry_run_prepared"
    assert report["gate"] == "dry_run_allowed"
    assert report["reasons"]


def test_dry_run_allows_missing_state(tmp_path):
    report = _assert_voice_gate(tmp_path, script_revision="rev-1",
                               voice_required=True, dry_run=True)
    assert report["status"] == "dry_run_prepared"


def test_no_voice_required_skips_gate(tmp_path):
    report = _assert_voice_gate(tmp_path, script_revision="rev-1",
                               voice_required=False, dry_run=False)
    assert report["status"] == "not_required"
