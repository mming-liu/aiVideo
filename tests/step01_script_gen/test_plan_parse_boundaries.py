# -*- coding: utf-8 -*-
"""**规划输入到修订应用的解析边界**（2026-10-01 复核第 30 轮）.

修订控制器要走这四类入口解析模型给的东西：节拍单值载体、区间载体、`plan_refs` 引用编号、
操作载体。复核确认的缺口：**安全数字转换只覆盖了承载声明**——另外三处仍直接 `int()`，
`台词`+5000 位数字、`coerce_ref_index("B²")`、操作载体 `台词²` 全都抛 `ValueError` 穿过
计划层；而操作应用器还按"数字前缀"截取，模型给的 `new_carrier="台词1-2"` 被**静默**改成
`台词1`，契约检查照样通过——修订控制器要反馈给规划器的意思被缩小了。

本文件的判据：**四类入口 × 五类输入**，每一格都要求"**不抛异常** + 该入口自己的结构化结果"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.outline import _norm_beat_carrier, apply_replan_ops  # noqa: E402
from step01_script_gen.plan_feasibility import (  # noqa: E402
    BeatRecord, coerce_ref_index, normalize_plan_ranges, plan_contract_problems)
from step01_script_gen.plan_feasibility import carrier_mode_hint  # noqa: E402


#: 拒收文案由判据侧渲染（`carrier_mode_hint()`）——夹具不另抄一份写法表。
_CARRIER_HINT = carrier_mode_hint()

_LONG = "9" * 5000
_CITE_A = "丙把茶壶踢翻了，茶水流了一地。"
#: 与「丙把茶壶踢翻了」这条事实**同范围**的证据（路线 3，见下）
_CITE_ONE = "丙把茶壶踢翻了。"
_CITE_B = "丁吓得发抖，连手里的茶盏都拿不稳了。"
_SRC = "甲道：“%s”乙道：“%s”" % (_CITE_A, _CITE_B)


def _beat(body, carrier):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, _CITE_B, carrier)


def _plan(carriers=("台词1", "画面", "台词2")):
    # 路线 3（2026-10-04）：证据与**这条事实**同范围——本体的主张就是「踢翻了茶壶」，
    # 证据只引那半句即可（原来的 `_CITE_A` 还含「茶水流了一地」，而承载台词只说得出前半句）。
    return {"required_facts": ["丙把茶壶踢翻了｜原文证据：“%s”｜承载：B2/画面" % _CITE_ONE],
            "shot_beats": [_beat("甲喝止", carriers[0]), _beat("丙失手", carriers[1]),
                           _beat("丁发抖", carriers[2])],
            "dialogue_lines": ["丙把茶壶踢翻了。", "丁吓得发抖。"],
            "narration_lines": []}


def _range_plan(value):
    return {"shot_beats": [_beat("开场", "画面"), _beat("块", value)],
            "required_facts": [], "dialogue_lines": ["甲"],
            "narration_lines": [{"text": "n%d" % i, "source": "s%d" % i, "fact_refs": []}
                                for i in range(1, 4)]}


_OLD_REC = BeatRecord.parse(_beat("旧正文", "台词1"))

#: 入口 → 类 → (输入, 期望)。期望一律是**结构化结果**，不是异常。
_MATRIX = {
    "节拍单值载体": {
        "合法": ("台词3", ("dialogue", 3)),
        "非法": ("歌词1", ("invalid", None)),
        "越界": ("台词0", ("dialogue", 0)),          # 越界由承载关系/契约门按轴的判
        "空白": ("台词 3", ("dialogue", 3)),         # 与去空白口径一致
        "超长": ("台词" + _LONG, ("invalid", None)),
    },
    "区间载体": {
        "合法": ("旁白1-3", 1),
        "非法": ("旁白²-3", 0),
        "越界": ("旁白1-9", 0),                      # 上界超过条目数 ⇒ 不展开
        "空白": ("旁白 1 - 3", 1),
        "超长": ("旁白1-" + _LONG, 0),
    },
    "引用编号": {
        "合法": ("B1", 1),
        "非法": ("Bx", None),
        "越界": ("B9", 9),                           # 越界由调用方的范围判据拦
        "空白": ("B 1", 1),
        "超长": ("B" + _LONG, None),
    },
    "操作载体": {
        "合法": ("台词1", ("台词1", "")),
        # 拒收文案由判据侧渲染（`carrier_mode_hint()`）——这里不另抄一份写法表
        "非法": ("歌词1", ("", "载体只能是 %s，收到「歌词1」" % _CARRIER_HINT)),
        "越界": ("台词3", ("", "载体「台词3」越界（本集共 2 句台词）")),
        "空白": ("台词 1", ("台词1", "")),
        "超长": ("台词" + _LONG,
                 ("", "载体只能是 %s，收到「台词%s」" % (_CARRIER_HINT, _LONG[:10]))),
    },
}


def _run(entry: str, value):
    """跑一个入口 —— **任何异常都算失败**（判据就是"不许抛"）."""
    if entry == "节拍单值载体":
        return BeatRecord.parse(_beat("正文", value)).carrier()
    if entry == "区间载体":
        return len(normalize_plan_ranges(_range_plan(value))[1])
    if entry == "引用编号":
        return coerce_ref_index(value, "B")
    if entry == "操作载体":
        return _norm_beat_carrier(value, 2, _OLD_REC, 2)
    raise AssertionError(entry)


@pytest.mark.parametrize("entry", sorted(_MATRIX))
@pytest.mark.parametrize("cls", ["合法", "非法", "越界", "空白", "超长"])
def test_parse_boundaries_are_structured_and_never_raise(entry, cls):
    """四类入口 × 五类输入：结果必须**结构化**（该入口自己的口径），且**不许抛异常**."""
    value, want = _MATRIX[entry][cls]
    got = _run(entry, value)
    assert got == want, (entry, cls, value[:24], got, want)


def test_non_ascii_and_overlong_digits_never_reach_the_front_gate_as_exceptions():
    """生成前门（`plan_contract_problems`）对非法/超长输入必须**留下诊断**，不是异常."""
    for _decl in ["B" + _LONG + "/画面", "B²/画面"]:
        _p = _plan()
        _p["required_facts"] = ["丙把茶壶踢翻了｜原文证据：“%s”｜承载：%s" % (_CITE_A, _decl)]
        assert plan_contract_problems(_p), _decl
    for _car in ["台词" + _LONG, "旁白²"]:
        _p = _plan(carriers=("台词1", _car, "台词2"))
        assert plan_contract_problems(_p), _car


def test_the_revision_applier_rejects_a_carrier_it_cannot_apply_whole():
    """**完整匹配**：`new_carrier="台词1-2"`（一条连续片段）不得被截成 `台词1` 照收.

    旧实现按数字前缀截取 ⇒ 应用成功、B2 的载体变成 `台词1`，模型声明的意思被静默缩小，
    契约检查还判通过（复核第 30 轮实测）。现在必须拒收并说明原因。
    """
    _ops = {"beat_ops": [{"index": 2, "new_carrier": "台词1-2",
                          "new_text": _beat("丙失手把茶壶踢翻", "画面")}],
            "fact_carriers": [{"index": 1, "beat": 2, "mode": "台词1"}]}
    _new, _why = apply_replan_ops(_plan(), _ops, _SRC)
    assert _new is None, "带区间的载体被应用了：%s" % (_new or {}).get("shot_beats")
    assert "载体只能是" in _why and "台词1-2" in _why, _why


def test_the_legal_carrier_op_still_applies():
    """对照：合法的 `台词1` 照旧可用（拒收只针对模糊写法）."""
    _ops = {"beat_ops": [{"index": 2, "new_carrier": "台词1",
                          "new_text": _beat("丙失手把茶壶踢翻", "画面")}],
            "fact_carriers": [{"index": 1, "beat": 2, "mode": "台词1"}]}
    _new, _why = apply_replan_ops(_plan(), _ops, _SRC)
    assert _new is not None, _why
    assert _new["shot_beats"][1].endswith("｜载体：台词1")
