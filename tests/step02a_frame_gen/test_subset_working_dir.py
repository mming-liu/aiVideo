# -*- coding: utf-8 -*-
"""build_working_dir 的子集写入边界：不得用子集覆盖全章结构缓存."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_subset_keeps_full_chapter_storyboard_and_camera_tree(tmp_path):
    from shared.vimax_bridge import build_working_dir

    script = {
        "characters": [],
        "shots": [
            {"index": 1, "prompt": "甲", "ff_desc": "甲", "lf_desc": "甲",
             "motion_desc": "甲", "cam_idx": 1},
            {"index": 2, "prompt": "乙", "ff_desc": "乙", "lf_desc": "乙",
             "motion_desc": "乙", "cam_idx": 1},
        ],
    }
    script_path = tmp_path / "script_result.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    work = tmp_path / "wd"
    work.mkdir()
    (work / "storyboard.json").write_text('{"keep": "full"}', encoding="utf-8")
    (work / "camera_tree.json").write_text('{"keep": "full"}', encoding="utf-8")

    asyncio.run(build_working_dir(script_path, work, object(), skip_frames=True,
                                  only_indices={1}))

    assert (work / "storyboard.json").read_text(encoding="utf-8") == '{"keep": "full"}'
    assert (work / "camera_tree.json").read_text(encoding="utf-8") == '{"keep": "full"}'
    assert (work / "shots" / "1" / "shot_description.json").is_file()
    assert not (work / "shots" / "2" / "shot_description.json").exists()
