# -*- coding: utf-8 -*-
"""核验提示的义务渲染：`obligation_id` 与 `text` **分列**（2026-10-25 P4 提示澄清）.

背景：旧写法 `1. [claim] <text>` 把 id 与正文挤在同一行；P4 的三次真实核验里，
模型分别回了 `"[claim] <text>"` 与 `"1"`，三份记录全部因义务身份被拒。澄清只改
**呈现**（两个提示入口共用 `_obligation_basis_lines`），schema/集合/id/解析/准入不变。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import content_verify as V                      # noqa: E402
from tests.step01_script_gen import p3_entry_fixtures as fx            # noqa: E402

A = fx.load_sample("a")


def _prompts():
    base = fx.baseline(A)
    view = base["view"]
    body = V.build_verification_prompt(
        view, novel_text=base["novel"],
        novel_digest=base["record"]["novel_digest"])
    display = V.build_display_prompt(view)
    return view, body, display


def test_both_prompt_entry_points_render_obligations_as_explicit_fields():
    """综合核验与展示核验都给出两键对象；旧 `[id] 文本` 形式不再出现."""
    view, body, display = _prompts()
    _obligations = V.frozen_obligations(view)
    assert _obligations, "样本必须至少给出一条待核义务"
    for fact, rows in _obligations.items():
        for row in rows:
            obj = json.dumps({"obligation_id": row["obligation_id"],
                              "text": row["text"]}, ensure_ascii=False)
            assert obj in body, (fact, obj)
            assert obj in display, (fact, obj)
            old = "[%s] %s" % (row["obligation_id"], row["text"])
            assert old not in body
            assert old not in display


def test_obligation_marker_is_claim_and_not_a_numbered_placeholder():
    """id 取代码给定的 `claim`；提示里不再出现「原样照抄」却无字段边界的旧措辞."""
    view, body, display = _prompts()
    _ids = {r["obligation_id"] for rows in V.frozen_obligations(view).values()
            for r in rows}
    assert _ids == {"claim"}, _ids
    for txt in (body, display):
        assert '"obligation_id": "claim"' in txt
