# -*- coding: utf-8 -*-
"""质量报告必须**逐字节可复现**（同一脚本 + 同一规则 ⇒ 同一摘要）.

背景（2026-11-14 实测）：R5 候选连续重建两次得到两个不同的
`quality_sha256`（`cb08db51…` / `7fbc5863…`），也就是「冻结候选」的摘要
根本无法被第三方复现。根因是规则 13.7 把 `known_chars`（**集合**）直接拼进
advisory 文案：集合迭代顺序随 `PYTHONHASHSEED` 变化，字符串随之变化。

本文件在**进程内**钉住这条：同一组角色，无论集合的插入顺序如何，产出的
advisory 必须逐字相同。跨进程的等价性由 `PYTHONHASHSEED=0/12345/999` 三次
重建比对覆盖（见 docs 报告），进程内用插入顺序模拟同一机制。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from step01_script_gen.validation import _check_frame_desc_anchors  # noqa: E402

#: 两位角色同时出镜、且都没在描述里重述服装色 ⇒ 同一条 advisory 里带两个名字。
#: 服装词必须能被 `_extract_dress_tokens` 识别，否则角色被跳过、只报一个人。
_DRESS = {
    "甲某": "深青色长衫，袖口磨损",
    "乙某": "青灰色道袍，袍角沾泥",
}
_TEXT = "甲某与乙某并肩立在门前，雨线斜落，两人都不说话"
_LABEL = "镜头1: ff_desc"


def _messages(order) -> list[str]:
    chars = set(order)          # 集合：迭代顺序由哈希决定，不由插入顺序决定
    dress = {name: _DRESS[name] for name in order}
    return _check_frame_desc_anchors(_TEXT, _LABEL, chars, dress)


def test_message_is_identical_regardless_of_insertion_order():
    forward = _messages(["甲某", "乙某"])
    backward = _messages(["乙某", "甲某"])
    assert forward == backward


def test_names_are_sorted_in_message():
    order = ["乙某", "甲某"]
    msgs = [m for m in _messages(order) if "服装色" in m]
    assert len(msgs) == 1, msgs
    text = msgs[0]
    # 不许假设"甲在乙前"：按码位排序（乙 U+4E59 < 甲 U+7532），期望值现算。
    expected = sorted(order)
    positions = [text.index(name) for name in expected]
    assert positions == sorted(positions), (expected, text)


def test_single_character_case_still_reports():
    chars = {"甲某"}
    dress = {"甲某": _DRESS["甲某"]}
    msgs = _check_frame_desc_anchors(_TEXT, _LABEL, chars, dress)
    assert any("服装色" in m for m in msgs), msgs
