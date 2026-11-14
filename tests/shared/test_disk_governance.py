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


def _governance_violation(rel: str, size: int) -> str | None:
    """判定一个被跟踪文件是否违反体积门禁（纯函数，供正反例单测复用）.

    与上面三条真实仓库断言共用同一套规则；返回值是违规类别名，`None` 表示合法。
    """
    if rel.startswith("output_step01_"):
        return "historical_output_snapshot"
    if rel.endswith("step01_replan_rounds.jsonl"):
        return "runtime_large_jsonl"
    if rel.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        if rel.startswith(_IMAGE_ALLOW_PREFIXES):
            return None
        if size > 500_000:
            return "large_image_outside_fixtures"
    return None


@pytest.mark.parametrize("rel,size,expect", [
    # 应拒绝样本
    ("output_step01_n03fix_20261105/锦衣夜行/x.png", 10, "historical_output_snapshot"),
    ("output/锦衣夜行/ch1/step01_replan_rounds.jsonl", 10, "runtime_large_jsonl"),
    ("output/锦衣夜行/ch1/images/a.png", 800_000, "large_image_outside_fixtures"),
    # 合法样本（受控夹具 / 文档 / 技能资产的明确例外）
    ("tests/fixtures/a.png", 800_000, None),
    ("docs/artifacts/a.png", 800_000, None),
    ("skills/x/assets/a.png", 1_000_000, None),
    # 合法样本（小位图、非位图）
    ("output/锦衣夜行/ch1/small.png", 100_000, None),
    ("step01_script_gen/main.py", 10, None),
])
def test_governance_guard_positive_and_negative_samples(rel, size, expect):
    assert _governance_violation(rel, size) == expect
