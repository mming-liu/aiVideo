"""json_repair 截断恢复回归测试.

背景（09-06 实测）：object_extractor 旧解析走裸 json.loads，agent_plan 响应
finish_reason=length 截断 → JSON 断尾即整体失败 →「预提取到 0 个场景物体」、
物体库零增量。接入 shared 单源 safe_parse_json 后由 _truncate_fix 兜底：
丢弃未完成尾对象/尾字段，保留已完整序列化的部分。
"""
from __future__ import annotations

import json

import pytest

from shared.utils.json_repair import safe_parse_json_object


# 模拟 09-06 21:27 实测截断形态：完整对象 + 断在字符串中间的尾对象
_TRUNCATED_SCENE_OBJECTS = """{
  "scene_objects": [
    {
      "identifier": "照月湾小舟",
      "category": "船",
      "length_m": 5.5,
      "width_m": 1.5,
      "capacity": "可容2-3人（杨旭、听香、张十三） + 炭炉、酒菜"
    },
    {
      "identifier": "青石马车",
      "category": "车",
      "length_m": 3.2,
      "size_reasoning": "原文\\"马车窄小\\"→两轮车，排除四轮大车"""


def test_truncated_array_recovers_complete_objects():
    """断尾响应抢救出全部完整对象，未完成尾对象被丢弃."""
    data = safe_parse_json_object(_TRUNCATED_SCENE_OBJECTS)
    objs = data["scene_objects"]
    assert [o["identifier"] for o in objs] == ["照月湾小舟", "青石马车"]
    assert objs[0]["length_m"] == 5.5


def test_truncated_array_idempotent_with_full_json():
    """完整 JSON 走同一路径必须零操作（解析结果一致）."""
    full = json.dumps(
        {"scene_objects": [{"identifier": "照月湾小舟", "category": "船", "length_m": 5.5}]},
        ensure_ascii=False,
    )
    data = safe_parse_json_object(full)
    assert data["scene_objects"][0]["length_m"] == 5.5


def test_garbage_still_raises():
    """抢救不了的内容仍抛 ValueError（调用方走失败路径，不静默吞）."""
    with pytest.raises(ValueError):
        safe_parse_json_object("这不是 JSON，也没有任何结构")
