# -*- coding: utf-8 -*-
"""v6.x.x step02a 落盘 QcResult 不可序列化修复测试.

实抽 `python step02a_frame_gen/main.py --shots 0` 在 json.dump 处崩：
shot["_qc_last"] 是 QcResult dataclass 实例，json 默认 encoder 不认识。
修法：落盘前 `_strip_runtime_fields(script)` 剥下划线开头字段（约定俗成
的"不入盘"信号）+ `default=` 兜底任何漏网 dataclass。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step02a_frame_gen.main import _strip_runtime_fields, _atomic_dump_json


@dataclass
class FakeQcResult:
    has_hallucination: bool
    issues: list
    ok: bool


def test_strip_drops_underscore_top_level():
    """下划线开头字段从顶层被剥掉."""
    obj = {"ff_desc": "x", "_qc_last": FakeQcResult(True, [], True), "index": 1}
    out = _strip_runtime_fields(obj)
    assert out == {"ff_desc": "x", "index": 1}
    assert "_qc_last" not in out


def test_strip_recurses_into_nested_dicts():
    """下划线字段在嵌套 dict 里也剥."""
    obj = {
        "shots": [
            {"index": 1, "ff_desc": "a", "_qc_last": FakeQcResult(False, [], True)},
            {"index": 2, "_qc_last": FakeQcResult(True, [], True)},
        ],
        "hook": {"prompt": "p", "_qc_last": FakeQcResult(True, [], True)},
    }
    out = _strip_runtime_fields(obj)
    assert out == {
        "shots": [
            {"index": 1, "ff_desc": "a"},
            {"index": 2},
        ],
        "hook": {"prompt": "p"},
    }


def test_strip_preserves_business_fields():
    """业务字段（无下划线）原样保留."""
    obj = {"shots": [{"index": 1, "ff_desc": "a", "lf_desc": "b"}],
           "characters": [{"identifier": "杨旭"}]}
    out = _strip_runtime_fields(obj)
    assert out == obj


def test_strip_handles_list_of_lists():
    """list 元素递归；下划线 key 元素位置保留为 {}（剥字段不删元素）."""
    obj = {"keyframes": [[{"_k": 1}, {"x": 2}], [{"_k": 3}]]}
    out = _strip_runtime_fields(obj)
    assert out == {"keyframes": [[{}, {"x": 2}], [{}]]}


def test_strip_handles_non_dict_objects():
    """叶子（非 dict/list）直接返回，不抛."""
    assert _strip_runtime_fields(42) == 42
    assert _strip_runtime_fields("hello") == "hello"
    assert _strip_runtime_fields(None) is None


def test_dump_script_with_qc_result_does_not_raise():
    """回归测试：模拟落盘逻辑，剥后能正常 json.dump."""
    # 模拟 gen_shot_frames 回写后的 script
    script = {
        "shots": [
            {"index": 0, "ff_desc": "神秘人持刀", "_qc_last": FakeQcResult(True, [], True)},
        ],
        "hook": {"prompt": "p", "_qc_last": FakeQcResult(True, [], True)},
        "characters": [{"identifier": "杨旭"}],
    }
    # 修前会抛 TypeError
    out = json.dumps(_strip_runtime_fields(script),
                     ensure_ascii=False,
                     default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))
    parsed = json.loads(out)
    assert "_qc_last" not in str(parsed)
    assert parsed["shots"][0]["ff_desc"] == "神秘人持刀"
    assert parsed["hook"]["prompt"] == "p"


# ── atomic write（v6.x.x.x）：dump 中途抛错不能污染原文件 ───────────

def test_atomic_write_normal(tmp_path):
    """正常路径：.tmp 写完被替换，文件存在且内容正确."""
    p = tmp_path / "x.json"
    _atomic_dump_json(p, {"a": 1, "b": [1, 2]}, ensure_ascii=False, indent=2)
    assert p.exists()
    assert not (p.with_suffix(".json.tmp")).exists()  # .tmp 已清理
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2]}


def test_atomic_write_failure_preserves_original(tmp_path):
    """回归测试（v6.x.x 实抽崩点）：dump 中途抛错 → 原文件完整无损.

    场景：script 里仍混着 _qc_last（剥后清干净了，但假设另一字段触发异常），
    模拟「不可序列化对象」让 default= 救不回来 → dump 抛错。
    """
    p = tmp_path / "y.json"
    # 先写一个「干净的原文件」
    p.write_text('{"safe": true, "version": 1}', encoding="utf-8")
    original = p.read_text(encoding="utf-8")

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("boom")
        # 让 hasattr(o, "__dict__") 返 False，强制走 str() 路径；
        # 而 __str__ 走 __getattr__ → 抛 RuntimeError，dump 中断
        __slots__ = ()

    # 触发抛错：default= 也救不了（hasattr __dict__=False, str() 也抛）
    try:
        _atomic_dump_json(p, {"x": _Boom()},
                          default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))
    except (RuntimeError, TypeError):
        pass

    # 关键断言：原文件内容字节级未变
    assert p.read_text(encoding="utf-8") == original, \
        "atomic write 失败时原文件必须完整保留，不能被截断"
    # .tmp 残留被清理
    assert not (p.with_suffix(".json.tmp")).exists()
