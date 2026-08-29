# -*- coding: utf-8 -*-
"""content_safety 替换重叠折叠（v6.50，08-28）单元测试.

治「一片暗红血迹」→「血迹→暗红痕迹」→「一片暗红暗红痕迹」：
替换值前缀与前置文本重叠（08-20 评估记 14 处、08-28 hook prompt 仍在）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_collapse_doubled_tokens_basic():
    from shared.content_safety import collapse_doubled_tokens

    assert collapse_doubled_tokens("心口一片暗红暗红痕迹洇开") == "心口一片暗红痕迹洇开"
    assert collapse_doubled_tokens("暗红暗红") == "暗红"


def test_collapse_preserves_legit_reduplication():
    from shared.content_safety import collapse_doubled_tokens

    assert collapse_doubled_tokens("月色慢慢沉下去") == "月色慢慢沉下去"
    assert collapse_doubled_tokens("层层涟漪") == "层层涟漪"
    assert collapse_doubled_tokens("水面粼粼波光") == "水面粼粼波光"


def test_sanitize_no_doubled_output():
    """「一片暗红血迹」经敏感词替换后不得产生「暗红暗红」."""
    from shared.content_safety import sanitize

    out = sanitize("杨旭心口一片暗红血迹洇开")
    assert "暗红暗红" not in out
    assert "暗红" in out


def test_sanitize_legit_redup_survives():
    from shared.content_safety import sanitize

    assert "慢慢" in sanitize("他慢慢拔出匕首")
