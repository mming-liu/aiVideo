# -*- coding: utf-8 -*-
"""WP4/WP3：路径契约 + 13 镜提示词/参考/QC 装配的映射证据（零调用）.

外部输入：正式稿（仓库 output/ 下唯一章节），缺失则 skip。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _formal_script() -> dict:
    import json
    hits = sorted((_ROOT / "output").glob("*/part_*/chapter_*/script_result.json"))
    if len(hits) != 1:
        pytest.skip("正式稿输入不唯一或缺失: %d 份" % len(hits))
    return json.loads(hits[0].read_text(encoding="utf-8"))


def test_step02b_write_path_equals_merge_read_path():
    """step02b 写出的片段路径必须正是 step04 读取的那一个（防止目录断层）."""
    from shared.paths import SHOT_VIDEO_FILENAME, vimax_shots_dir, vimax_working_dir

    chapter_write_root = Path(vimax_working_dir()) / "shots"
    merge_read_root = Path(vimax_shots_dir())
    assert chapter_write_root.resolve() == merge_read_root.resolve(), (
        "step02b 写 %s，step04 读 %s" % (chapter_write_root, merge_read_root))

    merged = merge_read_root / "7" / SHOT_VIDEO_FILENAME
    written = chapter_write_root / "7" / SHOT_VIDEO_FILENAME
    assert str(merged) == str(written)


def test_thirteen_shots_have_prompts_refs_and_qc(monkeypatch):
    """逐镜核对：帧 prompt 可组装、参考图选择可执行、QC prompt 可生成."""
    from shared.image_qc import build_qc_prompt
    from shared.vimax_bridge import (_collect_scene_asset_refs,
                                     compose_frame_prompt)

    script = _formal_script()
    shots = [s for s in script.get("shots") or [] if isinstance(s, dict)]
    chars = script.get("characters") or []
    assert len(shots) == 13

    for shot in shots:
        idx = shot["index"]
        for kind in ("first_frame", "last_frame"):
            prompt = compose_frame_prompt(shot, chars, kind)
            # 空镜没有角色名单块，但必须有可下发的完整 prompt
            assert prompt and len(prompt) > 80, (idx, kind)
        qc = build_qc_prompt(shot, "first_frame", chars)
        # 空镜没有人名清单，QC 不做人数断言；有角色的镜必须带 extra_person
        assert "质检" in qc and "category" in qc, idx
        if shot.get("char_layout"):
            assert "extra_person" in qc, idx
        refs = _collect_scene_asset_refs(shot)
        assert isinstance(refs, list), idx
