# -*- coding: utf-8 -*-
from __future__ import annotations

from step01_script_gen.hash_cache import _hash_source


def test_rule_version_changes_source_hash(tmp_path):
    build = tmp_path / "prompts.py"
    build.write_text("x", encoding="utf-8")
    h1 = _hash_source(str(build), "novel", 1, "", rule_version="v1")
    h2 = _hash_source(str(build), "novel", 1, "", rule_version="v2")
    assert h1 != h2


def test_cache_hits_on_second_run(tmp_path):
    """**首次落盘后第二次 `_prompt_changed` 必须为 False**（二次审核发现 6）.

    落盘会经过 `_strip_runtime_fields()`，它递归删除所有下划线开头的键。原先写的是
    `script["_source_hash"]`——写了个立刻被删的字段，`_prompt_changed()` 下次永远
    读到空值、永远返回 True：**每次运行 step01 都重新调用模型**（chapter 001 实测
    `_source_hash=None`）。本用例走真实的「写入 → 再读」路径。
    """
    import json

    from step01_script_gen.hash_cache import _prompt_changed
    from step01_script_gen.main import _strip_runtime_fields

    build = tmp_path / "prompts.py"
    build.write_text("x", encoding="utf-8")
    script_json = tmp_path / "script_result.json"

    # 首次：脚本不存在 → 必须重生成
    assert _prompt_changed(build, script_json, "novel", 1, "", rule_version="v1") is True

    # 落盘：算 hash 后过一遍运行时字段清理（这正是生产路径）
    script = {"shots": [], "source_hash": _hash_source(str(build), "novel", 1, "",
                                                      rule_version="v1")}
    script_json.write_text(
        json.dumps(_strip_runtime_fields(script), ensure_ascii=False), encoding="utf-8")

    # 第二次：源未变 → 命中缓存
    assert _prompt_changed(build, script_json, "novel", 1, "", rule_version="v1") is False

    # 规则版本变化 → 必须失效
    assert _prompt_changed(build, script_json, "novel", 1, "", rule_version="v2") is True


def test_legacy_underscore_hash_still_read(tmp_path):
    """旧文件里的 `_source_hash` 仍要能读（已有 chapter 目录不需要迁移）."""
    import json

    from step01_script_gen.hash_cache import _prompt_changed, _hash_source

    build = tmp_path / "prompts.py"
    build.write_text("x", encoding="utf-8")
    script_json = tmp_path / "script_result.json"
    script_json.write_text(json.dumps({
        "_source_hash": _hash_source(str(build), "novel", 1, "", rule_version="v1"),
    }), encoding="utf-8")
    assert _prompt_changed(build, script_json, "novel", 1, "", rule_version="v1") is False


def test_source_hash_survives_strip_and_is_not_a_runtime_field():
    """`source_hash` 不是运行时字段，且 `_source_hash` 仍然是（权限边界要清楚）."""
    from step01_script_gen.main import _strip_runtime_fields

    out = _strip_runtime_fields({"source_hash": "abc", "_source_hash": "abc",
                                 "_run_metadata": {"x": 1}})
    assert out == {"source_hash": "abc"}


def test_rule_source_fingerprint_covers_shared_rule_implementations():
    """规则源指纹必须覆盖决定「什么算 blocking / 时间字段谁能写」的共享实现.

    发现 6：只改 `shared/motion_segments.py` / `shared/quality_gate.py` /
    `shared/semantic_barrier.py` / `_text_gen/deepseek_schema.py` 而不动 step01
    目录时，旧报告会被错误地当成仍然有效。

    用**静态**断言（不搬文件）——指纹函数的文件清单里必须出现这四条路径，
    而且它们都真实存在。
    """
    import inspect
    from pathlib import Path

    from step01_script_gen.quality_issues import _compute_rule_source_fingerprint

    root = Path(__file__).resolve().parents[2]
    src = inspect.getsource(_compute_rule_source_fingerprint)
    for rel in ("shared/motion_segments.py", "shared/quality_gate.py",
                "shared/semantic_barrier.py", "_text_gen/deepseek_schema.py"):
        assert (root / rel).exists(), rel
        parts = rel.split("/")
        assert all(f'"{p}"' in src for p in parts), (
            f"规则源指纹未覆盖 {rel}"
        )
