# -*- coding: utf-8 -*-
"""第 1 包 M04-c：r2v 首帧缺失时的兜底参考图必须真的被取用（零网络）.

原先 `_collect_r2v_references` 里调用未导入的 `vimax_working_dir()`，NameError 被
「首帧构图锚点收集异常」吞掉，兜底从未生效。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_missing_first_frame_uses_fallback_reference(tmp_path, monkeypatch):
    import shared.paths as paths
    import step02_video_gen.prompt_builder as pb
    seen, logs = [], []
    portrait = tmp_path / "portrait.png"
    portrait.write_bytes(b"png")
    monkeypatch.setattr(paths, "first_frame_file", lambda idx: tmp_path / "missing.png")
    monkeypatch.setattr(paths, "vimax_working_dir", lambda: tmp_path)

    def _fallback(working_dir, shot=None, characters=None):
        seen.append(working_dir)
        return str(portrait)

    monkeypatch.setattr(pb, "_find_fallback_reference", _fallback)
    monkeypatch.setattr(pb, "_log_r2v_collect", lambda msg: logs.append(str(msg)))
    pb._collect_r2v_references({"index": 3, "location": ""}, characters=[], registry={})
    assert seen == [tmp_path]
    assert any("fallback 参考图" in x for x in logs), logs
    assert not any("首帧构图锚点收集异常" in x for x in logs), logs
