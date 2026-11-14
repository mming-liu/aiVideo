# -*- coding: utf-8 -*-
"""工作区体积门禁：禁止大体积运行产物误入 git（2026-11-14 P2）.

背景：历史上 6 个 ``output_step01_*_20261105/`` 目录在忽略规则生效前被 ``git add``，
把 66 MB 重复立绘与百万字级 ``step01_replan_rounds.jsonl`` 带进版本库；清理时
需要 ``git rm`` + ``git gc`` 才能回收。本测试把「哪些东西不得被跟踪」固化成门禁：

- 任何 ``output_step01_*`` 快照目录；
- 运行期大体积 ``step01_replan_rounds.jsonl``；
- 非测试/文档/技能资产位置上的 >500KB 位图。

**判据只有一份**：`_governance_violation` 是纯函数，真实仓库断言
（`_tracked_violations`）与下面的正/反例参数化用例**调用同一个函数**。
2026-11-14 复核 P2：此前真实断言各写一份规则、纯函数另写一份，正反例全绿
也不能证明实际守卫有效——现在规则副本已删除。

在干净检出（git 可用）上运行；git 不可用时跳过（门禁是仓库约束，不是运行时依赖）。
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_IMAGE_ALLOW_PREFIXES = (
    "tests/", "docs/", "skills/", "baselines/", "reference_skills/", "vimax/",
)
_LARGE_IMAGE_BYTES = 500_000


def _governance_violation(rel: str, size: int) -> str | None:
    """判定一个被跟踪文件是否违反体积门禁（纯函数）.

    返回值是违规类别名，`None` 表示合法。**真实仓库断言与正/反例共用这一份**；
    改判据只需改这里，不存在"测试里的规则"与"守卫的规则"两份副本。
    """
    if rel.startswith("output_step01_"):
        return "historical_output_snapshot"
    if rel.endswith("step01_replan_rounds.jsonl"):
        return "runtime_large_jsonl"
    if rel.lower().endswith(_IMAGE_SUFFIXES):
        if rel.startswith(_IMAGE_ALLOW_PREFIXES):
            return None
        if size > _LARGE_IMAGE_BYTES:
            return "large_image_outside_fixtures"
    return None


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("git 不可用：体积门禁只在版本库工作区生效")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


@lru_cache(maxsize=1)
def _tracked_violations() -> tuple[tuple[str, str], ...]:
    """当前仓库所有被跟踪文件的违规 `(path, category)`——**真实守卫的唯一入口**."""
    out: list[tuple[str, str]] = []
    for rel in _tracked_files():
        size = 0
        if rel.lower().endswith(_IMAGE_SUFFIXES):
            try:
                size = (ROOT / rel).stat().st_size
            except OSError:
                continue
        code = _governance_violation(rel, size)
        if code:
            out.append((rel, code))
    return tuple(out)


def _violations_of(category: str) -> list[str]:
    return [rel for rel, code in _tracked_violations() if code == category]


def test_no_historical_output_snapshots_tracked():
    bad = _violations_of("historical_output_snapshot")
    assert bad == [], "历史 output_step01_* 快照不得被 git 跟踪：%s" % bad[:5]


def test_no_large_replan_rounds_jsonl_tracked():
    bad = _violations_of("runtime_large_jsonl")
    assert bad == [], "运行期 step01_replan_rounds.jsonl 不得被 git 跟踪：%s" % bad[:5]


def test_no_large_images_outside_fixtures_tracked():
    bad = _violations_of("large_image_outside_fixtures")
    assert bad == [], "非夹具位置的大体积位图不得被 git 跟踪：%s" % bad[:5]


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


def test_tracked_violations_uses_the_same_rule(monkeypatch):
    """守卫入口必须真的调用纯函数——防止规则再次被复制成两份.

    把 `_tracked_files` 换成一棵假的被跟踪清单（含一个应拒样本），断言
    `_tracked_violations()` 报出它。若真实守卫绕过纯函数、另写一份规则，
    这条会红。
    """
    import sys
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_tracked_files",
                        lambda: ["output_step01_fake/run.json",
                                 "step01_script_gen/main.py"])
    _tracked_violations.cache_clear()
    try:
        assert _tracked_violations() == (("output_step01_fake/run.json",
                                          "historical_output_snapshot"),)
    finally:
        _tracked_violations.cache_clear()
