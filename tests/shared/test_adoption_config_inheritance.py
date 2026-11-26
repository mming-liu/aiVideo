# -*- coding: utf-8 -*-
"""采纳收据的生效配置必须被下游继承（S10R 扩展：声音改编 / hook）."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEYS = ("DURATION_MIN_SEC", "DURATION_MAX_SEC", "DURATION_TARGET_MIN_SEC",
         "DURATION_TARGET_MAX_SEC", "VOICE_ADAPTATION_ENABLED", "HOOK_ENABLED")


@pytest.fixture(autouse=True)
def _restore_inherited_env():
    """`_inherit_adoption_duration_band` 会调 `os.environ.setdefault`；用例前后还原全部键."""
    before = {k: os.environ.get(k) for k in _KEYS}
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_inherits_voice_adaptation_and_hook(tmp_path, monkeypatch):
    from shared import config as cfg
    import shared.paths as paths

    ch = tmp_path / "chapter_001"
    ch.mkdir()
    (ch / "step01_adoption.json").write_text(json.dumps({
        "effective_config": {
            "VOICE_ADAPTATION_ENABLED": "1",
            "HOOK_ENABLED": "1",
            "DURATION_MIN_SEC": "60",
        }}), encoding="utf-8")
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: ch)
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)

    cfg._inherit_adoption_duration_band()

    assert os.environ["VOICE_ADAPTATION_ENABLED"] == "1"
    assert os.environ["HOOK_ENABLED"] == "1"


def test_explicit_env_wins(tmp_path, monkeypatch):
    from shared import config as cfg
    import shared.paths as paths

    ch = tmp_path / "chapter_001"
    ch.mkdir()
    (ch / "step01_adoption.json").write_text(json.dumps({
        "effective_config": {"VOICE_ADAPTATION_ENABLED": "1"}}), encoding="utf-8")
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: ch)
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOICE_ADAPTATION_ENABLED", "0")

    cfg._inherit_adoption_duration_band()

    assert os.environ["VOICE_ADAPTATION_ENABLED"] == "0"


def test_historical_60_90_receipt_stays_60_90_under_new_default(tmp_path, monkeypatch):
    """S46：新默认改为 60–120 / 85–110，但已采纳的 60–90 历史收据仍按收据继承."""
    from shared import config as cfg
    import shared.paths as paths

    ch = tmp_path / "chapter_001"
    ch.mkdir()
    (ch / "step01_adoption.json").write_text(json.dumps({
        "effective_config": {
            "DURATION_MIN_SEC": 60,
            "DURATION_MAX_SEC": 90,
            "DURATION_TARGET_MIN_SEC": 70,
            "DURATION_TARGET_MAX_SEC": 80,
        }}), encoding="utf-8")
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: ch)
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)

    cfg._inherit_adoption_duration_band()

    assert os.environ["DURATION_MIN_SEC"] == "60"
    assert os.environ["DURATION_MAX_SEC"] == "90"
    assert os.environ["DURATION_TARGET_MIN_SEC"] == "70"
    assert os.environ["DURATION_TARGET_MAX_SEC"] == "80"
