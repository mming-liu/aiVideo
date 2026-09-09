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


# ─────────────── v6.62.6（09-09）截断形态覆盖 ───────────────
#
# 上面只覆盖「截断落在字符串内部」这一种形态，而 _truncate_fix 的前向扫描对其它
# 形态判定 in_str=False 后**原样返回**——实测截在数值/字面量/分隔符处时整份解析
# 失败、列表型提取拿到 0 项。既然 allow_truncated 已放行这些响应进解析层，
# 每种形态都必须有对应回归。


@pytest.mark.parametrize(
    "name,text,expect_ids",
    [
        (
            "截在数值中间",
            '{"scene_objects": [{"identifier": "小舟", "length_m": 5.5}, '
            '{"identifier": "马车", "length_m": 3.2',
            ["小舟"],
        ),
        (
            "截在字面量中间",
            '{"scene_objects": [{"identifier": "小舟"}, {"identifier": "马车", "covered": tr',
            ["小舟"],
        ),
        (
            "截在元素分隔符之后",
            '{"scene_objects": [{"identifier": "小舟", "length_m": 5.5},',
            ["小舟"],
        ),
        (
            "截在嵌套子对象中间",
            '{"scene_objects": [{"identifier": "小舟", "dim": {"l": 5.5, "w": 1.5}}, '
            '{"identifier": "马车", "dim": {"l": 3.2',
            ["小舟"],
        ),
        (
            "截在字符串中间（保住尾对象已写完的字段）",
            '{"scene_objects": [{"identifier": "小舟", "length_m": 5.5}, '
            '{"identifier": "马车", "size_reasoning": "原文说马车窄小',
            ["小舟", "马车"],
        ),
    ],
)
def test_truncation_forms_preserve_complete_prefix(name, text, expect_ids):
    """各种截断形态都保住**前面完整的元素**，只丢未写完的尾部.

    关键不变式：抢救**不得**吃掉此前已完整序列化的对象——那才是数据丢失。
    """
    objs = safe_parse_json_object(text)["scene_objects"]
    ids = [o.get("identifier") for o in objs if o.get("identifier")]
    assert ids == expect_ids, f"{name}: 完整前缀被吃掉了"


def test_truncation_at_next_key_yields_empty_tail_object():
    """截断恰好落在**下一个 key** 上时，尾对象被削成 {}——调用方必须能识别.

    这不是缺陷而是取舍（key/value 成对丢弃），但它意味着解析结果里会出现无
    identifier 的空壳；object_extractor 据此过滤，故在这里把行为钉死。
    """
    text = '{"scene_objects": [{"identifier": "小舟", "length_m": 5.5}, {"identifier": "马车", "ca'
    objs = safe_parse_json_object(text)["scene_objects"]
    assert objs[0]["identifier"] == "小舟", "完整对象必须保住"
    assert not objs[-1].get("identifier"), "尾部残壳没有 identifier"
