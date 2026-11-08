# -*- coding: utf-8 -*-
"""WP3：Step 02a 帧生成的**零调用重放**（真实入口，桩在最底层适配器）.

验证三件事，且都不触网：
1. 首帧按 compose_frame_prompt 组装后交给适配器一次；
2. 内容没变时命中哈希缓存，**不再调用**适配器；
3. 只有本帧描述变了才重新生成（未受影响帧保持可用）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SHOT = {
    "index": 1,
    "main_subject": "甲",
    "location": "河边",
    "ff_desc": "甲站在船头，衣袍被风吹起，远处水面平静",
    "lf_desc": "甲坐下，双手放在膝上，水面起雾",
    "motion_desc": "0-3秒：站立；3-6秒：坐下",
}
CHARS = [{"identifier": "甲"}]


class _Adapter:
    def __init__(self):
        self.prompts: list[str] = []

    async def generate_single_image(self, **kwargs):
        self.prompts.append(str(kwargs.get("prompt") or ""))

        def _save(path):
            Image.new("RGB", (1024, 1024), (18, 24, 30)).save(path)

        return SimpleNamespace(data=SimpleNamespace(save=_save))


def _gen(adapter, shots_dir, shot, **kw):
    from shared import vimax_bridge as vb
    return asyncio.run(vb._gen_first_frame_async(
        adapter, shots_dir, dict(shot), False,
        script_mtime=None, reference_paths=[], max_retries=0,
        characters=CHARS, **kw))


def test_first_frame_replay_reuses_and_invalidates(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "none")
    shots_dir = tmp_path / "shots"
    adapter = _Adapter()

    first = _gen(adapter, shots_dir, SHOT)
    assert first and Path(first).is_file()
    assert len(adapter.prompts) == 1
    # 提示来自 compose_frame_prompt（含严格约束块），并在末尾附加 per-attempt 扰动
    assert "甲站在船头" in adapter.prompts[0]
    assert "【人物数量限制】" in adapter.prompts[0]

    # 内容不变 ⇒ 缓存命中，零新增调用
    again = _gen(adapter, shots_dir, SHOT)
    assert again == first
    assert len(adapter.prompts) == 1, "未改动的帧被重新生成"

    # 只有尾帧描述变化 ⇒ 首帧仍可复用
    lf_changed = dict(SHOT, lf_desc="甲站起，水面结冰")
    assert _gen(adapter, shots_dir, lf_changed) == first
    assert len(adapter.prompts) == 1

    # 首帧描述变化 ⇒ 重新生成
    ff_changed = dict(SHOT, ff_desc="甲跌入水中，水面炸开")
    _gen(adapter, shots_dir, ff_changed)
    assert len(adapter.prompts) == 2
