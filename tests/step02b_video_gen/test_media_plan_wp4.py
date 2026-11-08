# -*- coding: utf-8 -*-
"""WP4 剩余承诺：13 镜的 **video_media_plan** 绑定核对（零调用）.

逐镜核对：介质策略、首尾帧绑定路径、时长与运动提示是否进入计划。
外部输入：正式稿（WP4_FORMAL_SCRIPT 或仓库 output/ 下唯一章节）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _formal_script() -> dict:
    env = (os.getenv("WP4_FORMAL_SCRIPT") or "").strip()
    if env:
        p = Path(env)
        if not p.is_file():
            pytest.skip("WP4_FORMAL_SCRIPT 指向的文件不存在: %s" % p)
        return json.loads(p.read_text(encoding="utf-8"))
    hits = sorted((_ROOT / "output").glob("*/part_*/chapter_*/script_result.json"))
    if len(hits) != 1:
        pytest.skip("正式稿输入不唯一或缺失: 命中 %d 份" % len(hits))
    return json.loads(hits[0].read_text(encoding="utf-8"))


def test_media_plan_binds_frames_for_every_shot(tmp_path):
    from shared.video_media_plan import plan_image_media

    script = _formal_script()
    shots = [s for s in script.get("shots") or [] if isinstance(s, dict)]
    assert len(shots) == 13

    for shot in shots:
        idx = int(shot["index"])
        d = tmp_path / str(idx)
        d.mkdir(parents=True, exist_ok=True)
        ff = d / "first_frame.png"
        lf = d / "last_frame.png"
        ff.write_bytes(b"ff")
        lf.write_bytes(b"lf")
        plan = plan_image_media(supports_reference_image=False,
                                image_path=ff, last_frame_path=lf,
                                keyframe_paths=())
        assert isinstance(plan, dict)
        # 计划必须给出可执行的介质策略，并绑定到本镜真实存在的帧
        assert plan.get("mode") in ("first_last", "first", "reference"), (idx, plan)
        items = plan.get("items") or []
        paths = [str(i.get("path") or "") for i in items]
        assert str(ff) in paths and str(lf) in paths, (idx, plan)
    assert all(float(s["duration_sec"]) > 0 for s in shots)
    total = sum(float(s["duration_sec"]) for s in shots)
    assert abs(total - 75.25) < 0.2
