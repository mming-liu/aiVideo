# -*- coding: utf-8 -*-
"""第十六轮复算工具的缺陷（复核 P2-⑤）：**编号变换必须覆盖全部引用**.

`hook_carrier` 可以多句（`B2/台词1；B3/台词2`，`outline.py`：「多句用「；」分隔」）。
旧实现只改第一条（`re.sub(..., count=1)`）⇒ 展开后得到 `B3/台词1；B3/台词2`，第二句
被指到第一条节拍上。这里把"全部引用"钉住：`required_facts` 的每条「承载：Bn/…」与
`hook_carrier` 的**每一条**声明都要各按自己的键重映射（产品规范化尚未实施，此工具是
它的测量基准，基准错了收益就假了）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _tool():
    _p = _ROOT / ".tmp" / "_p1_carrier_replay.py"
    if not _p.exists():
        return None
    _spec = importlib.util.spec_from_file_location("_replay_r16", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)          # 工具已加 `__main__` 守卫：import 无副作用
    return _mod


def _beat(body, carrier) -> str:
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, body, carrier)


def test_every_hook_declaration_is_remapped():
    """复核反例：`B2/台词1；B3/台词2` 展开后必须是 `B3/台词1；B4/台词2`（不是 `B3/；B3/`）."""
    _t = _tool()
    if _t is None:
        return
    plan = {"shot_beats": [_beat("甲", "画面"),          # B1
                           _beat("乙", "台词1"),          # B2 → 展开成 B2..B3
                           _beat("丙", "台词2")],         # B3 → 展开成 B4..B5
            "dialogue_lines": ["甲", "乙"],
            "hook_carrier": "B2/台词1；B3/台词2"}
    # 手工把 B2 扩成两条单值节拍（模拟"区间载体展开"的效果），再看引用有没有跟着走
    p2, mapping, _notes = _t.expand_ranges(plan)
    assert mapping[2] == [2], mapping
    # 造一个真的需要位移的场景：B1 拆成两条 ⇒ 后面所有编号 +1
    plan2 = {"shot_beats": [_beat("甲", "画面1-2"), _beat("乙", "台词1"), _beat("丙", "台词2")],
             "dialogue_lines": ["甲", "乙"], "hook_carrier": "B2/台词1；B3/台词2"}
    p3, m3, _ = _t.expand_ranges(plan2)
    _t.relocate_refs(p3, m3, _t.carrier_index(p3))
    assert m3[1] == [1, 2], m3
    assert p3["hook_carrier"] == "B3/台词1；B4/台词2", p3["hook_carrier"]


def test_all_fact_declarations_are_remapped():
    """事实侧同理：每条「承载：Bn/…」各按自己的键重映射（多条一起移位）."""
    _t = _tool()
    if _t is None:
        return
    plan = {"shot_beats": [_beat("甲", "画面1-2"), _beat("乙", "台词1")],
            "dialogue_lines": ["甲"],
            "required_facts": ["事实一｜原文证据：“甲”｜承载：B1/画面",
                               "事实二｜原文证据：“甲”｜承载：B2/台词1"]}
    p2, mapping, _ = _t.expand_ranges(plan)
    _t.relocate_refs(p2, mapping, _t.carrier_index(p2))
    _f = p2["required_facts"]
    assert "承载：B1/画面" in _f[0], _f[0]      # 画面那条落在展开后的第一条
    assert "承载：B3/台词1" in _f[1], _f[1]     # 台词那条按键定位到展位后的新号
    assert _t.ref_surface(p2) == [("F1", "承载：B1/画面"), ("F2", "承载：B3/台词1")],         _t.ref_surface(p2)


def test_a_unique_key_is_required_before_relocating_by_key():
    """按键重定位**先统计唯一性**：两条节拍同载 D1 时不许猜，退回按旧号平移并报出来."""
    _t = _tool()
    if _t is None:
        return
    plan = {"shot_beats": [_beat("甲", "台词1"), _beat("乙", "台词1"), _beat("丙", "画面1-2")],
            "dialogue_lines": ["甲"],
            "required_facts": ["事实一｜原文证据：“甲”｜承载：B2/台词1"]}
    assert len(_t.carrier_index(plan)[("台词", 1)]) == 2, _t.carrier_index(plan)
    p2, mapping, _ = _t.expand_ranges(plan)
    _notes = _t.relocate_refs(p2, mapping, _t.carrier_index(p2))
    assert any("不唯一" in _n for _n in _notes), _notes
    assert "承载：B2/台词1" in p2["required_facts"][0], p2["required_facts"][0]
