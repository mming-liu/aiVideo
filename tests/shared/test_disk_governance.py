# -*- coding: utf-8 -*-
"""工作区体积门禁：禁止大体积运行产物误入 git（2026-11-14 P2）.

背景：历史上 6 个 ``output_step01_*_20261105/`` 目录在忽略规则生效前被 ``git add``，
把 66 MB 重复立绘与百万字级 ``step01_replan_rounds.jsonl`` 带进版本库；清理时
需要 ``git rm`` + ``git gc`` 才能回收。本测试把「哪些东西不得被跟踪」固化成门禁：

- 任何 ``output_step01_*`` 快照目录；
- 运行期大体积 ``step01_replan_rounds.jsonl``；
- 非测试/文档/技能资产位置上的 >500KB 位图。

在干净检出（git 可用）上运行；git 不可用时跳过（门禁是仓库约束，不是运行时依赖）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_IMAGE_ALLOW_PREFIXES = (
    "tests/", "docs/", "skills/", "baselines/", "reference_skills/", "vimax/",
)


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("git 不可用：体积门禁只在版本库工作区生效")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def test_no_historical_output_snapshots_tracked():
    bad = [p for p in _tracked_files() if p.startswith("output_step01_")]
    assert bad == [], "历史 output_step01_* 快照不得被 git 跟踪：%s" % bad[:5]


def test_no_large_replan_rounds_jsonl_tracked():
    bad = [p for p in _tracked_files() if p.endswith("step01_replan_rounds.jsonl")]
    assert bad == [], "运行期 step01_replan_rounds.jsonl 不得被 git 跟踪：%s" % bad[:5]


def test_no_large_images_outside_fixtures_tracked():
    bad = []
    for rel in _tracked_files():
        if not rel.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        if rel.startswith(_IMAGE_ALLOW_PREFIXES):
            continue
        try:
            size = (ROOT / rel).stat().st_size
        except OSError:
            continue
        if size > 500_000:
            bad.append((rel, size))
    assert bad == [], "非夹具位置的大体积位图不得被 git 跟踪：%s" % bad[:5]
