# -*- coding: utf-8 -*-
"""落盘安全：下划线运行时字段不入盘 + 原子写不污染原文件.

**本文件的覆盖对象已于 2026-10 复核 P0 迁移（契约变更，非测试失效）**

原覆盖对象是 `step02a_frame_gen.main` 的两个**本地副本**：
`_strip_runtime_fields` / `_atomic_dump_json`。它们只服务于 step02a 的一处
「把 ff_desc 归一化结果回写 `script_result.json`」——而那句回写本身违反了
「已提交产物字节级只读」契约（`script_result.json` 是 step01 的正式产物，
`step01_commit.json` 锁着它的 SHA1，下游每个阶段入口都核），会把
step02a/step02b 之后的 step03/step04 与任何重跑判成
`artifact changed after commit` 而拒收。回写连同两个副本一并移除。

**被保护的行为本身仍然有效**，所以用例原样保留，只把目标指向**唯一在用的实现**：

  * 剥字段  → `step01_script_gen.main._strip_runtime_fields`（现为单源实现，
    step01 落盘前统一调用）
  * 原子写  → `shared.json_io.atomic_dump_json`（step02a 的副本只是它的转发别名）
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.json_io import atomic_dump_json  # noqa: E402
from step01_script_gen.main import _strip_runtime_fields  # noqa: E402


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
    """回归测试：剥掉运行时字段后能正常 json.dumps（原实抽崩点）."""
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


# ── atomic write：dump 中途抛错不能污染原文件 ───────────

def test_atomic_write_normal(tmp_path):
    """正常路径：.tmp 写完被替换，文件存在且内容正确."""
    p = tmp_path / "x.json"
    atomic_dump_json(p, {"a": 1, "b": [1, 2]}, ensure_ascii=False, indent=2)
    assert p.exists()
    assert not (p.with_suffix(".json.tmp")).exists()  # .tmp 已清理
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2]}


def test_atomic_write_failure_preserves_original(tmp_path):
    """回归测试（实抽崩点）：dump 中途抛错 → 原文件完整无损.

    场景：模拟「不可序列化对象」让 default= 救不回来 → dump 抛错。
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
        atomic_dump_json(p, {"x": _Boom()},
                         default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))
    except (RuntimeError, TypeError):
        pass

    # 关键断言：原文件内容字节级未变
    assert p.read_text(encoding="utf-8") == original, \
        "atomic write 失败时原文件必须完整保留，不能被截断"
    # .tmp 残留被清理
    assert not (p.with_suffix(".json.tmp")).exists()
