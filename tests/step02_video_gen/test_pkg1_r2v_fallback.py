# -*- coding: utf-8 -*-
"""r2v 首帧缺失时的身份兜底：取用肖像，但**不得**挤掉场景资产图（零网络）.

第 1 包 M04-c 补回 `vimax_working_dir` 导入后，兜底肖像被放进首帧构图锚点位，
组装时据此排除了场景图——首帧缺失时最终参考只剩一张肖像（2026-11-19 复核 P2）。
肖像只能提供身份/外观，提供不了场景、时间与动作起点。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _SceneBank:
    def __init__(self, wide):
        self._wide = wide

    def load(self):
        pass

    def get_all_identifiers(self):
        return ["场景一"]

    def get_scene(self, sid):
        return {"name": sid}

    def get_wide_image(self, sid):
        return self._wide

    def get_near_image(self, sid):
        return None


class _PropBank:
    def load(self):
        pass

    def get_all_identifiers(self):
        return []


@pytest.fixture
def env(tmp_path, monkeypatch):
    import shared.assets_bank as ab
    import shared.paths as paths
    import shared.scene_identity as si
    import step02_video_gen.prompt_builder as pb
    portrait = tmp_path / "portrait.png"
    wide = tmp_path / "wide.png"
    first = tmp_path / "first_frame.png"
    for p in (portrait, wide):
        p.write_bytes(b"png")
    logs, seen = [], []
    monkeypatch.setattr(paths, "vimax_working_dir", lambda: tmp_path)
    monkeypatch.setattr(ab, "SceneAssetsBank", lambda: _SceneBank(str(wide)))
    monkeypatch.setattr(ab, "PropAssetsBank", _PropBank)
    monkeypatch.setattr(si, "resolve_scene_identity",
                        lambda entries, **k: SimpleNamespace(ok=True, scene_id="场景一",
                                                             reason="test", diagnostics=[]))
    monkeypatch.setattr(pb, "is_empty_scene", lambda shot, chars: False)

    def _fallback(working_dir, shot=None, characters=None):
        seen.append(working_dir)
        return str(portrait)

    monkeypatch.setattr(pb, "_find_fallback_reference", _fallback)
    monkeypatch.setattr(pb, "_log_r2v_collect", lambda msg: logs.append(str(msg)))
    return SimpleNamespace(pb=pb, paths=paths, tmp=tmp_path, portrait=str(portrait),
                           wide=str(wide), first=first, logs=logs, seen=seen,
                           monkeypatch=monkeypatch)


_SHOT = {"index": 3, "location": "场景一", "ff_desc": "甲立于门前", "motion_desc": "甲转身"}


def test_missing_first_frame_keeps_scene_and_adds_portrait(env):
    env.monkeypatch.setattr(env.paths, "first_frame_file", lambda idx: env.tmp / "missing.png")
    refs = env.pb._collect_r2v_references(dict(_SHOT), characters=[], registry={},
                                          materialize=False)
    assert env.seen == [env.tmp]
    assert env.wide in refs, "首帧缺失时场景资产图必须保留"
    assert env.portrait in refs, "身份兜底肖像应进入参考"
    assert refs.index(env.wide) < refs.index(env.portrait), "场景在前、角色在后（类别顺序）"
    assert not any("首帧构图锚点收集异常" in x for x in env.logs), env.logs


def test_real_first_frame_still_replaces_scene(env):
    env.first.write_bytes(b"x" * (11 * 1024))
    env.monkeypatch.setattr(env.paths, "first_frame_file", lambda idx: env.first)
    refs = env.pb._collect_r2v_references(dict(_SHOT), characters=[], registry={},
                                          materialize=False)
    assert refs[0] == str(env.first)
    assert env.wide not in refs, "有真实首帧时仍以它替代场景图（原行为不变）"
    assert env.seen == [], "有首帧时不取身份兜底"
