# -*- coding: utf-8 -*-
"""2026-08-29 三 bug 修复单测：vimax_bridge 重试膨胀 + lf 软跳过 + style_prefix 措辞.

v6.32.1 修复：
- Bug 1: prompt 组装移出重试循环（避免 attempt=2 时 prompt 膨胀 25-40%）
- Bug 2: lf 矛盾门包 try-except → 软跳过（返回 None），不再让 lf 矛盾 fail-fast
  整条 shot；ff 仍 fail-fast 保留主防线
- Bug 3 (style_prefix): 黑影尾句改措辞，不触发矛盾自检器
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest


# ── Bug 1: vimax_bridge prompt 组装移出循环 ─────────────────────────


def test_ff_prompt_assembly_outside_retry_loop(monkeypatch, tmp_path):
    """ff prompt 在 3 次 attempt 长度必须相等（无叠加注入）.

    用 mock adapter 抓取每次重试实际发送的 prompt 字符串，比较长度。
    旧实现 attempt=2 会比 attempt=1 长 25-40%（实测）；新实现恒等。

    设计：让前 2 次 attempt 的图片文件不达标（_validate_frame 返 False），
    强制走 3 次循环；第 3 次成功，捕获所有 prompt 比较。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")  # 总尝试 3 次

    from shared.vimax_bridge import _gen_first_frame_async

    captured_prompts: list[str] = []
    validate_call_count = [0]

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            captured_prompts.append(kwargs["prompt"])
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    shot = {
        "index": 99,
        "ff_desc": "杨旭与听香对坐舱中，月色清凉",
        "lf_desc": "",
        "motion_desc": "",
        "scene": "照月湾小舟上",
        "location": "照月湾",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _no_qc(*a, **k): return False
    monkeypatch.setattr(vb, "_maybe_qc_frame", _no_qc)

    # 前 2 次 validate 返 False（不达标）→ 强制重试；第 3 次返 True
    def _validate_v2(path):
        validate_call_count[0] += 1
        return validate_call_count[0] >= 3
    monkeypatch.setattr(vb, "_validate_frame", _validate_v2)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)

    async def run():
        return await _gen_first_frame_async(
            MockAdapter(), work_dir, shot,
            force=True,
            script_mtime=None,
            reference_paths=None,
            max_retries=2,  # 总 3 次
            empty_scene=False,
            characters=characters,
        )

    result = asyncio.run(run())
    assert result is not None
    # 期望：3 次 prompt 字符串完全相等（无叠加）
    assert len(captured_prompts) == 3, f"期望 3 次 attempt，实测 {len(captured_prompts)}"
    p1, p2, p3 = captured_prompts
    assert p1 == p2, f"attempt 1 vs 2 长度 {len(p1)} vs {len(p2)}（应恒等）"
    assert p2 == p3, f"attempt 2 vs 3 长度 {len(p2)} vs {len(p3)}（应恒等）"


# ── Bug 2: lf 矛盾软跳过（不抛） ─────────────────────────────────────


def test_lf_contradiction_soft_skip_returns_none(monkeypatch, tmp_path):
    """lf prompt 含确定矛盾时 → _gen_last_frame_async 返回 None 不抛.

    ff fail-fast 保留主防线；lf 软跳过避免单帧矛盾 kill 整条 shot。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "1")

    from shared.vimax_bridge import _gen_last_frame_async

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            raise AssertionError("矛盾时不应调到图片生成")

    # 矛盾 prompt：匕首在杨旭手中 + 杨旭手中没有武器
    shot = {
        "index": 7,
        "ff_desc": "杨旭与听香对坐舱中",
        "lf_desc": "杨旭手中匕首寒光如月",
        "motion_desc": "",
        "scene": "照月湾小舟上",
        "location": "照月湾",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)

    from shared import vimax_bridge as vb
    # 注入矛盾的 _build_final_frame_prompt（绕过真实组装，确保进入矛盾门）
    monkeypatch.setattr(
        vb, "_build_final_frame_prompt",
        lambda *a, **k: "【道具锚定】神秘人匕首在杨旭手中。"
                       "杨旭手中没有武器，没有刀，没有剑，没有短刃。"
    )

    async def run():
        return await _gen_last_frame_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None,
            reference_paths=None, max_retries=1,
            empty_scene=False, characters=characters,
        )

    result = asyncio.run(run())
    assert result is None, f"lf 矛盾应软跳过返回 None，实测 {result!r}"


# ── Bug 3: style_prefix 黑影尾句不再触发矛盾自检器 ───────────────────


def test_invisible_threat_wording_passes_contradiction_check():
    """新措辞代入最终 prompt 跑矛盾自检器，必须 0 报矛盾.

    旧措辞「禁止…第二个黑影」+ 剧情「水下黑影轮廓」会触发对称矛盾。
    沿用原 test_count_anchor_invisible_threat_shows_entity_count 的 shot
    设定（保证走 _threat_invisible_only 分支，应用新措辞）。
    """
    from shared.prompt_contradiction import find_contradictions
    from style_prefix import add_image_strict_constraints

    shot = {
        "location": "小舟上",
        "ff_desc": "神秘人化作水下黑影一闪而过，杨旭仰卧未觉，听香在旁未察",
        "lf_desc": "", "motion_desc": "",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}, {"identifier": "刺客"}]
    base = "杨旭仰卧，水下黑影，"
    tail = add_image_strict_constraints(base, "首帧", shot=shot, characters=characters)
    full = base + tail
    issues = find_contradictions(full)
    assert not any("对称矛盾" in i for i in issues), \
        f"新措辞不应触发黑影自触矛盾，实测 issues={issues}"
