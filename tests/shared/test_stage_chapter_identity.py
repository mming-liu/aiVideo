# -*- coding: utf-8 -*-
"""**阶段入口的请求章号**：执行级回归（2026-10-01 复核 P1）.

复核原话：「step03 漏了请求章号校验……应核查全部阶段入口，补执行级回归；仅扫描
`run_scope` 引导块盖不到这类遗漏。」

`shared.quality_gate.verify_step01_commit_manifest(..., expected_chapter=)` 的语义是
「**请求的这一章**的产物」，章号由**入口**传。入口漏传 → 默认只核 script 自身的一致性，
于是「请求第 2 章、目录与脚本都属于第 1 章」照样通过（复核给的反例）。

本文件不扫源码，而是**真的调**每个入口的 `main()`：把两个门替换成记录器（记录实参后
立刻抛出哨兵异常，阻止它继续跑），断言记录到的 `expected_chapter` **等于本次请求的章号**。
漏传关键字参数、写死常量、或沿用已导入模块里的旧值，都会在这里失败。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

REQUESTED = 2


class _Stop(Exception):
    """记录到门调用后立刻中断入口执行（不真的跑流水线）."""


def _prepare_env(monkeypatch, chapter: int):
    """把 OUTPUT_BASE / NOVEL_CHAPTER 指到临时目录，并造一份脚本产物."""
    import shared.paths as paths
    monkeypatch.setenv("NOVEL_CHAPTER", str(chapter))
    chd = paths.chapter_dir()
    chd.mkdir(parents=True, exist_ok=True)
    (chd / "script_result.json").write_text(
        json.dumps({"chapter_index": chapter, "shots": [{"index": 1}],
                    "characters": [], "chapter_outline": {}}, ensure_ascii=False),
        encoding="utf-8")
    return chd


def _recording_gate(recorded: list):
    def _verify(chapter_dir, script, *, stage, expected_chapter=None, **kw):
        recorded.append({"stage": stage, "expected_chapter": expected_chapter,
                         "chapter_dir": str(chapter_dir)})
        raise _Stop()
    return _verify


def _run_entry(module_name: str, monkeypatch, *, extra=None):
    """import（必要时 reload，让 import 时求值的章级常量跟着 env 走）→ 调 main() → 返回记录."""
    import shared.quality_gate as qg
    recorded: list = []
    monkeypatch.setattr(qg, "verify_step01_commit_manifest", _recording_gate(recorded))
    monkeypatch.setattr(qg, "require_gate", lambda *a, **k: None)
    monkeypatch.setattr(qg, "require_semantic_gate", lambda *a, **k: None)
    mod = importlib.import_module(module_name)
    mod = importlib.reload(mod)          # 让模块级常量按当前 env 重新求值
    if extra:
        extra(mod, monkeypatch)
    try:
        mod.main()
    except _Stop:
        pass
    except SystemExit as exc:            # 入口自己提前退出 ⇒ 没走到门，视为失败
        pytest.fail("入口在章号校验之前就退出了（rc=%s）" % exc.code)
    return recorded


def _names(mod):
    return {n: getattr(mod, n) for n in dir(mod)}


# ---------------------------------------------------------------------------
# 四个下游入口：都要把**请求章号**传给提交清单核验
# ---------------------------------------------------------------------------
def test_step03_passes_the_requested_chapter(monkeypatch):
    _prepare_env(monkeypatch, REQUESTED)
    rec = _run_entry("step03_tts_audio.main", monkeypatch)
    assert rec, "step03 没有走到提交清单核验"
    assert rec[0]["expected_chapter"] == REQUESTED, rec


def test_step04_passes_the_requested_chapter(monkeypatch):
    import step04_merge.main as m0                    # 先取一份，用于造目录
    _prepare_env(monkeypatch, REQUESTED)

    def _extra(mod, mp):
        mod.VIMAX_WD.mkdir(parents=True, exist_ok=True)
        mod.CLIP_DIR.mkdir(parents=True, exist_ok=True)

    rec = _run_entry("step04_merge.main", monkeypatch, extra=_extra)
    assert rec, "step04 没有走到提交清单核验"
    assert rec[0]["expected_chapter"] == REQUESTED, rec


def test_step02b_passes_the_requested_chapter(monkeypatch):
    _prepare_env(monkeypatch, REQUESTED)

    def _extra(mod, mp):
        mp.setattr(sys, "argv", ["main.py"])

    rec = _run_entry("step02b_video_gen.main", monkeypatch, extra=_extra)
    assert rec, "step02b 没有走到提交清单核验"
    assert rec[0]["expected_chapter"] == REQUESTED, rec


def test_step02a_passes_the_requested_chapter(monkeypatch):
    _prepare_env(monkeypatch, REQUESTED)

    def _extra(mod, mp):
        mp.setattr(sys, "argv", ["main.py"])

    rec = _run_entry("step02a_frame_gen.main", monkeypatch, extra=_extra)
    assert rec, "step02a 没有走到提交清单核验"
    assert rec[0]["expected_chapter"] == REQUESTED, rec
