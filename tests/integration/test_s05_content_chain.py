# -*- coding: utf-8 -*-
"""S05：后处理修好的内容必须到达**最终提示**与**生成器桩**（跨内容离线回放）.

2026-11-13 复核要求：S02 的确定性修复不能只停在 Step01 内存/落盘对象里，必须沿
真实消费链走完 ——

1. `run_hard_fix`（Step01 后处理唯一入口）修正/保留内容；
2. Step01 落盘的 `script_result.json` 被 Step02a 的提示组装读取；
3. `compose_frame_prompt`（生成与预检共用的**唯一**组装入口）产出最终提示；
4. `_gen_first_frame_async`（真实逐帧入口）把该提示交给适配器（生成器桩）。

两个匿名样本使用不同角色、船名、船型、尺寸与露头高度：断言只针对**有来源 /
无来源**，不绑定任何小说、角色或章节。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 两个内容不同的匿名样本（角色/船名/船型/尺寸/露头高度都不同）。
_VARIANTS = {
    "river": {
        "a": "甲", "b": "乙",
        "boat_key": "河湾小舟", "boat_subtype": "无篷舴艋舟",
        "length": 6.0, "width": 1.4,
        "second_key": "救生艇", "second_subtype": "封闭式救生艇",
        "second_length": 6.0, "second_width": 2.0,
        "depth_cm": 5,
    },
    "harbor": {
        "a": "丙", "b": "丁",
        "boat_key": "港湾渔船", "boat_subtype": "有篷渔船",
        "length": 4.2, "width": 1.1,
        "second_key": "快艇", "second_subtype": "敞篷快艇",
        "second_length": 3.5, "second_width": 1.2,
        "depth_cm": 9,
    },
}


class _Adapter:
    """生成器桩：只记录真实入口交给适配器的 prompt，不触网。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def generate_single_image(self, **kwargs):
        self.prompts.append(str(kwargs.get("prompt") or ""))

        def _save(path):
            Image.new("RGB", (1024, 1024), (18, 24, 30)).save(path)

        return SimpleNamespace(data=SimpleNamespace(save=_save))


def _script(v: dict) -> dict:
    a, b = v["a"], v["b"]
    return {
        "chapter_index": 1,
        "chapter_title": "匿名样本",
        "characters": [{"identifier": a}, {"identifier": b}],
        "scene_objects": {
            v["boat_key"]: {"category": "船", "subtype": v["boat_subtype"],
                            "length_m": v["length"], "width_m": v["width"]},
            v["second_key"]: {"category": "船", "subtype": v["second_subtype"],
                              "length_m": v["second_length"],
                              "width_m": v["second_width"]},
        },
        "shots": [
            {
                "index": 1,
                "duration_sec": 4.0,
                "location": v["boat_key"],
                "scene": "水边",
                "main_subject": a,
                "prompt": (f"[近景] {a}坐在一叶小舟"
                           f"（长{v['length']:g}米窄身{v['boat_subtype']}）船头。"),
                "ff_desc": (f"{a}坐在一叶小舟"
                            f"（长{v['length']:g}米窄身{v['boat_subtype']}）船头，"
                            f"窄身机甲站在小舟旁。"),
                "lf_desc": f"{a}抬头看向岸边。",
                "motion_desc": f"前2秒：{a}坐着；后2秒：{a}抬头。",
                "narration_segment": "",
                "frame_layouts": {
                    "ff": [{"name": a, "on_screen": True, "position": "船头",
                            "pose": "坐着", "gaze_target": "水面"}],
                    "lf": [{"name": a, "on_screen": True, "position": "船头",
                            "pose": "抬头", "gaze_target": "岸边"}],
                },
                "keyframes": [],
                "camera_movement": "固定机位",
                "plan_refs": {"beats": [1], "lines": []},
            },
            {
                "index": 2,
                "duration_sec": 4.0,
                "location": "水面",
                "scene": "水边",
                "main_subject": b,
                "prompt": f"[中景] {b}潜伏水下，仅头顶约{v['depth_cm']}cm露出。",
                "ff_desc": f"{b}潜伏水下，仅头顶约{v['depth_cm']}cm露出。",
                "lf_desc": f"{b}仍潜伏水下。",
                "motion_desc": "前2秒：静伏；后2秒：不动。",
                "narration_segment": "",
                "frame_layouts": {
                    "ff": [{"name": b, "on_screen": True, "position": "水下",
                            "pose": "潜伏", "gaze_target": "水面"}],
                    "lf": [{"name": b, "on_screen": True, "position": "水下",
                            "pose": "潜伏", "gaze_target": "水面"}],
                },
                "keyframes": [],
                "camera_movement": "固定机位",
                "plan_refs": {"beats": [2], "lines": []},
            },
            {
                "index": 3,
                "duration_sec": 4.0,
                "location": "渡口",
                "scene": "渡口",
                "main_subject": a,
                "prompt": f"[中景] {a}走下一叶小舟，踏上渡口石阶。",
                "ff_desc": f"{a}走下一叶小舟，踏上渡口石阶。",
                "lf_desc": f"{a}站在石阶上回头。",
                "motion_desc": "前2秒：下船；后2秒：回望。",
                "narration_segment": "",
                "frame_layouts": {
                    "ff": [{"name": a, "on_screen": True, "position": "船头",
                            "pose": "下船", "gaze_target": "石阶"}],
                    "lf": [{"name": a, "on_screen": True, "position": "石阶",
                            "pose": "回望", "gaze_target": "水面"}],
                },
                "keyframes": [],
                "camera_movement": "固定机位",
                "plan_refs": {"beats": [3], "lines": []},
            },
            {
                "index": 4,
                "duration_sec": 4.0,
                "location": v["second_key"],
                "scene": "码头",
                "main_subject": b,
                "prompt": f"[中景] 小舟靠岸，{b}把缆绳系在石桩上。",
                "ff_desc": f"小舟靠岸，{b}把缆绳系在石桩上。",
                "lf_desc": f"缆绳绷紧，小舟停稳。",
                "motion_desc": "前2秒：系绳；后2秒：拉紧。",
                "narration_segment": "",
                "frame_layouts": {
                    "ff": [{"name": b, "on_screen": True, "position": "船头",
                            "pose": "系绳", "gaze_target": "石桩"}],
                    "lf": [{"name": b, "on_screen": True, "position": "岸上",
                            "pose": "拉绳", "gaze_target": "小舟"}],
                },
                "keyframes": [],
                "camera_movement": "固定机位",
                "plan_refs": {"beats": [4], "lines": []},
            },
        ],
        "chapter_outline": {
            "shot_beats": ["节拍：坐船头", "节拍：潜伏水下",
                           "节拍：下船上岸", "节拍：系船靠岸"],
            "main_events": ["坐船头", "潜伏", "下船", "系船"],
            "causal_chain": ["起因：出行", "结果：到达"],
            "narration_lines": [],
            "dialogue_lines": [],
            "required_facts": [],
            "fact_uids": [],
        },
        "total_duration": 16.0,
        "body_duration": 16.0,
        "hook_duration": 0.0,
    }


def _gen(adapter, shots_dir, shot, characters):
    from shared import vimax_bridge as vb
    return asyncio.run(vb._gen_first_frame_async(
        adapter, shots_dir, dict(shot), False,
        script_mtime=None, reference_paths=[], max_retries=0,
        characters=characters))


@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_postprocessed_content_reaches_the_final_prompt_and_generator(
        variant, tmp_path, monkeypatch):
    v = _VARIANTS[variant]
    monkeypatch.setenv("QC_PROVIDER", "none")
    from step01_script_gen.constraints.engine import run_hard_fix

    postprocessed = run_hard_fix(_script(v))

    # Step01 唯一落盘入口的消费形态：script_result.json
    chapter = tmp_path / "chapter_001"
    chapter.mkdir(parents=True)
    (chapter / "script_result.json").write_text(
        json.dumps(postprocessed, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("shared.paths.chapter_dir", lambda *a, **k: chapter)

    characters = [{"identifier": v["a"]}, {"identifier": v["b"]}]
    adapter = _Adapter()
    shots = {s["index"]: s for s in postprocessed["shots"]}
    for idx in (1, 2, 3, 4):
        assert _gen(adapter, tmp_path / "shots", shots[idx], characters)
    assert len(adapter.prompts) == 4, adapter.prompts

    p1, p2, p3, p4 = adapter.prompts

    # 1) 有规格的船：宽度来自 scene_objects；其他实体的「窄身」保留；
    #    不再追加无依据的肩宽比例/裁切构图。
    assert f"长{v['length']}米" in p1 and f"宽{v['width']}米" in p1
    assert "窄身机甲" in p1, "其他实体的属性不得被船宽改写删除"
    assert "肩宽" not in p1
    assert "只画船头" not in p1 and "向画外延伸" not in p1

    # 2) 水下姿态：保留输入的露头高度，不补造「约 2cm」；补的是否定说明。
    assert f"{v['depth_cm']}cm" in p2
    assert "约 2cm" not in p2
    assert "不是上半身出水" in p2

    # 3) 无规格的船：保留原文，不编造尺寸/船型。
    assert "一叶小舟" in p3
    assert "敞口木舟" not in p3
    assert f"宽{v['width']}米" not in p3

    # 4) 非舴艋船型：保留输入名称/形制/材质，不统一改写成「敞口木舟」。
    assert v["second_subtype"] in p4
    assert "敞口木舟" not in p4
