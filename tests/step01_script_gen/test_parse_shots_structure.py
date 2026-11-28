# -*- coding: utf-8 -*-
"""parse_response shots 结构守卫测试.

09-13 实测：候选 3 的 LLM 把 shots 输出成字符串数组，_fill_missing_new_fields
对 str 调 setdefault 抛 AttributeError——穿透 RuntimeError/ValueError 重试网
（main.py 1777-1816 只捕这两类）FATAL 打穿整个 run。修复：parse_response 在
填充前按 ValueError 拒收结构错（走既有重试换一次生成），fill 循环兜底跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _fill_missing_new_fields, parse_response


class TestParseShotsStructureGuard:
    def test_shots_with_string_element_raises_valueerror(self):
        """shots 混入字符串 → ValueError（重试网可接），不得 AttributeError."""
        import json
        raw = json.dumps({"shots": ["不是 dict 的元素"], "narration": ""})
        try:
            parse_response(raw)
        except ValueError as e:
            assert "shots 结构非法" in str(e)
        except AttributeError as e:  # pragma: no cover
            raise AssertionError(f"应抛 ValueError 走重试网，实际 AttributeError: {e}")

    def test_shots_not_a_list_raises_valueerror(self):
        """shots 本身是字符串 → ValueError."""
        import json
        raw = json.dumps({"shots": "整段文本", "narration": ""})
        try:
            parse_response(raw)
        except ValueError as e:
            assert "shots 结构非法" in str(e)


class TestFillSkipsNonDict:
    def test_fill_skips_non_dict_shots(self):
        """fill 对非 dict 元素跳过不炸（兜底其他调用路径）."""
        script = {"shots": ["垃圾", {"index": 1, "prompt": "p"}]}
        _fill_missing_new_fields(script)
        assert script["shots"][0] == "垃圾"  # 原样跳过
        assert script["shots"][1]["content"] == ""  # dict 正常补全

    def test_fill_empty_shots_ok(self):
        _fill_missing_new_fields({"shots": []})
        _fill_missing_new_fields({})
