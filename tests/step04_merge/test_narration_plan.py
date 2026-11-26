# -*- coding: utf-8 -*-
"""S44/P1：Step04 旁白收集必须消费外置有效计划."""
from __future__ import annotations

import wave

from step04_merge.main import _collect_narration_tracks


def _wav(path):
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(8000)
        fh.writeframes(b"\0\0" * 8000)


def test_collect_narration_tracks_uses_external_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATION_ALLOW_STALE", "1")
    paths = {}
    for i in range(1, 5):
        paths[i] = tmp_path / f"n{i}.wav"
        _wav(paths[i])
    texts = [f"旁白{i}" for i in range(1, 5)]
    beats = [f"节拍：{t}｜载体：旁白{i + 1}" for i, t in enumerate(texts)]
    plan = {"narration_lines": [{"text": t} for t in texts], "shot_beats": beats}
    shots = [{"index": i, "narration_segment": texts[i - 1],
              "plan_refs": {"beats": [i], "lines": []}} for i in range(1, 5)]
    script = {"shots": shots}  # 稿内没有 chapter_outline
    tracks = _collect_narration_tracks(
        script, shots, [(0, 20)] * 4, resolve=lambda _kind, i: paths[i], plan=plan)
    assert [t["index"] for t in tracks] == [1, 2, 3, 4]
    assert all(t["dur"] > 0 for t in tracks)
