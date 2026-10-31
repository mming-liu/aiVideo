# -*- coding: utf-8 -*-
"""R07/F8：血状态标记的**语义区分**（2026-10-30 复核）.

钉的是同一处否定范围：否定必须作用在「血迹/血污/血痕」上，不能把「毫无血色」（脸色）
当成「没有血迹」；「已经消失」与「已消失」同等处理。不扩成词表治理。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from step01_script_gen.validation import _extract_state_markers


@pytest.mark.parametrize("text,expected", [
    ("他脸色毫无血色，胸口血迹扩大。", True),   # 脸色不是血迹：同句的血迹才是真状态
    ("胸口没有血迹。", False),
    ("血迹已经消失。", False),
    ("血迹已消失。", False),
    ("血迹不见。", False),
    ("血迹被衣襟遮住。", None),               # 遮挡 = 不知道，不是"消失"
    ("他站在门口，脸色苍白。", None),
])
def test_blood_state_reads_the_negated_object(text, expected):
    assert _extract_state_markers(text)["has_blood"] is expected
