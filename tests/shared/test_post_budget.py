# -*- coding: utf-8 -*-
"""POST 预算账本：跨进程持久化、并发原子、损坏 fail-closed、额度熔断."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from shared import post_budget


@pytest.fixture(autouse=True)
def _clean_budget(monkeypatch, tmp_path):
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_FILE", raising=False)
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_TOTAL", raising=False)
    monkeypatch.delenv("STEP02B_VIDEO_BUDGET_FILE", raising=False)
    monkeypatch.delenv("STEP02B_VIDEO_BUDGET_TOTAL", raising=False)
    # 章目录钉到本用例空目录：WP1a 的自动发现只看本用例自己的布置，
    # 不能被 .pytest_tmp 里别的用例残留的账本点亮。
    from shared import paths
    chapter = tmp_path / "chapter_001"
    chapter.mkdir()
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: chapter)
    post_budget.reset()
    yield
    post_budget.reset()


def _enable(monkeypatch, tmp_path, total=3, kind='image'):
    prefix = "STEP02A_IMAGE" if kind == "image" else "STEP02B_VIDEO"
    monkeypatch.setenv(prefix + "_BUDGET_FILE", str(tmp_path / "budget.json"))
    monkeypatch.setenv(prefix + "_BUDGET_TOTAL", str(total))
    return tmp_path / "budget.json"


def test_disabled_when_total_unset():
    assert post_budget.acquire("image") == 0
    assert post_budget.usage("image") == {}
    assert post_budget.quota_exhausted("video") == ""


def test_acquire_persists_and_exhausts(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=2)
    assert post_budget.acquire("image") == 1
    assert post_budget.acquire("image") == 2
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["used"] == 2 and data["limit"] == 2


def test_exhaustion_survives_memory_reset(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=1)
    post_budget.acquire("image")
    post_budget._QUOTA.clear()
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_corrupt_ledger_fails_closed(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    ledger.write_text("{not json", encoding="utf-8")
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    assert post_budget.quota_exhausted("image")


def test_quota_marker_persists_and_blocks(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=5, kind='video')
    post_budget.mark_quota_exhausted("video", "403 SetLimitExceeded")
    post_budget._QUOTA.clear()
    assert "SetLimitExceeded" in post_budget.quota_exhausted("video")
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("video")


def test_concurrent_acquire_never_exceeds_limit(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=5)
    ok = []
    bad = []
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        try:
            ok.append(post_budget.acquire("image"))
        except post_budget.PostBudgetExhausted:
            bad.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 5 and len(bad) == 15
    assert post_budget.usage("image")["used"] == 5


def test_is_quota_error_markers():
    assert post_budget.is_quota_error('{"code":"SetLimitExceeded"}')
    assert post_budget.is_quota_error("QuotaExceeded: no balance")
    assert post_budget.is_quota_error("账户额度不足")
    assert not post_budget.is_quota_error("400 bad request")


def _write_ledger(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_missing_used_is_rejected_not_reset(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    _write_ledger(ledger, {"kind": "image", "limit": 5})
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_illegal_used_values_are_rejected(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    for bad in (-1, "1", True, 1.5):
        _write_ledger(ledger, {"kind": "image", "used": bad, "limit": 5})
        with pytest.raises(post_budget.PostBudgetExhausted):
            post_budget.acquire("image")


def test_ledger_kind_mismatch_is_rejected(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    _write_ledger(ledger, {"kind": "video", "used": 0, "limit": 5})
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_changed_total_cannot_expand_existing_ledger(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    post_budget.acquire("image")
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "9")
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["used"] == 1, "被拒的请求不得改写账本"
    assert data["limit"] == 5


def test_clear_quota_keeps_used_and_reallows(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    post_budget.acquire("image")
    post_budget.mark_quota_exhausted("image", "SetLimitExceeded")
    assert post_budget.quota_exhausted("image")

    post_budget.clear_quota("image")
    assert post_budget.quota_exhausted("image") == ""
    assert post_budget.acquire("image") == 2
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["used"] == 2, "clear_quota 不得重置已用次数"


# ---------------------------------------------------------------------------
# U6-FIN WP1a：账本自动发现（"账本在、门禁就在"）
# ---------------------------------------------------------------------------
def _valid_ledger(used: int, limit: int) -> dict:
    return {"kind": "image", "used": used, "limit": limit,
            "schema_version": 2, "pending": 0,
            "outcomes": {"generated_ok": used, "account_quota_refused": 0,
                         "transport_failed": 0, "qc_rejected": 0}}


def test_auto_discovers_chapter_ledger_without_any_env(tmp_path):
    """一个环境变量都不给，只要章目录里有账本，门禁照样生效并扣减.

    2026-11-14 R6 S07 实测事故：漏传 STEP02A_IMAGE_BUDGET_TOTAL 时，
    章目录里躺着 used=31/limit=172 的账本，护栏却被静默旁路——真发了 2 次
    POST 却没记账，事后只能靠事件流人工补。这里钉住"不会再旁路"。
    """
    chapter = tmp_path / "chapter_001"
    _write_ledger(chapter / "step02a_image_budget.json", _valid_ledger(4, 5))

    assert post_budget.acquire("image") == 5
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")

    data = post_budget.usage("image")
    assert data["budget_source"] == "ledger_autodiscovered"
    assert data["limit"] == 5 and data["used"] == 5
    assert data["path"].endswith("step02a_image_budget.json")


def test_auto_discovery_without_ledger_stays_disabled(tmp_path):
    """没有账本、也没给配置 → 行为与历史版本一致（不启用）."""
    assert post_budget.acquire("image") == 0
    assert post_budget.usage("image") == {}
    assert post_budget.quota_exhausted("image") == ""


def test_explicit_zero_total_is_the_only_way_to_close_the_gate(
        monkeypatch, tmp_path):
    """显式 TOTAL=0 才算"我确实不要门禁"；这是唯一的合法关闭写法."""
    chapter = tmp_path / "chapter_001"
    _write_ledger(chapter / "step02a_image_budget.json", _valid_ledger(4, 5))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "0")

    assert post_budget.acquire("image") == 0
    assert post_budget.usage("image") == {}
    data = json.loads((chapter / "step02a_image_budget.json").read_text(encoding="utf-8"))
    assert data["used"] == 4, "关闭门禁不得改写账本"


def test_explicit_total_still_wins_over_ledger_limit(monkeypatch, tmp_path):
    """显式上限优先：与账本 limit 不一致时仍然拒绝（不静默扩缩）."""
    chapter = tmp_path / "chapter_001"
    _write_ledger(chapter / "step02a_image_budget.json", _valid_ledger(1, 172))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "3")

    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_corrupt_discovered_ledger_fails_closed(tmp_path):
    """自动发现到的账本坏了 → 拒绝发送，不因"读不懂"而放行."""
    chapter = tmp_path / "chapter_001"
    (chapter / "step02a_image_budget.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    assert post_budget.quota_exhausted("image")


def test_discovered_ledger_without_limit_is_not_silently_blessed(tmp_path):
    """账本存在但上限读不出 → 照样启用，交给 _read 判死；不重建、不静默归零."""
    chapter = tmp_path / "chapter_001"
    _write_ledger(chapter / "step02a_image_budget.json",
                  {"kind": "image", "used": 2})

    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    data = json.loads((chapter / "step02a_image_budget.json").read_text(encoding="utf-8"))
    assert data["used"] == 2, "被拒的请求不得改写账本"


def test_reset_never_deletes_auto_discovered_ledger(tmp_path):
    """reset() 只删显式启用的账本：一次 reset 不能抹掉生产账本."""
    chapter = tmp_path / "chapter_001"
    ledger = chapter / "step02a_image_budget.json"
    _write_ledger(ledger, _valid_ledger(33, 172))

    post_budget.reset()

    assert ledger.exists(), "自动发现出来的真实账本不在 reset 的删除范围"
    assert json.loads(ledger.read_text(encoding="utf-8"))["used"] == 33


# ---------------------------------------------------------------------------
# 2026-11-14 复核 P1：显式配置无效应报错，不得静默关闭门禁
# ---------------------------------------------------------------------------
def test_explicit_file_without_ledger_is_an_error_not_a_silent_off(
        monkeypatch, tmp_path):
    """只给 BUDGET_FILE、文件不存在 ⇒ 报配置错误；不能当成"没配"放行.

    旧行为返回 `enabled=false`、`acquire()` 返回 0 直接发送——护栏整条消失。
    """
    missing = tmp_path / "nope" / "budget.json"
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_FILE", str(missing))

    with pytest.raises(post_budget.PostBudgetConfigError) as excinfo:
        post_budget.acquire("image")
    assert excinfo.value.source == "ledger_env_file_missing"

    described = post_budget.describe("image")
    assert described["enabled"] is True
    assert described["source"] == "ledger_env_file_missing"
    assert "不存在" in described["error"]

    used = post_budget.usage("image")
    assert "不存在" in used["error"]
    assert not missing.exists(), "被拒的预检不得顺手创建账本"


@pytest.mark.parametrize("bad_total", ["abc", "3.0", "-1"])
def test_invalid_explicit_total_is_rejected(monkeypatch, tmp_path, bad_total):
    """非法 TOTAL（非整数/负数）不是"显式关闭"：只有整数 0 才关."""
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", bad_total)

    with pytest.raises(post_budget.PostBudgetConfigError) as excinfo:
        post_budget.acquire("image")
    assert excinfo.value.source == "invalid_total"
    assert post_budget.describe("image")["enabled"] is True
    assert "error" in post_budget.describe("image")


def test_negative_total_does_not_disable_the_gate(monkeypatch, tmp_path):
    """负数曾被当成 `explicit_off`；现在必须报错，且不得扣减/建账."""
    chapter = tmp_path / "chapter_001"
    _write_ledger(chapter / "step02a_image_budget.json", _valid_ledger(4, 5))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "-2")

    with pytest.raises(post_budget.PostBudgetConfigError):
        post_budget.acquire("image")
    data = json.loads((chapter / "step02a_image_budget.json").read_text(encoding="utf-8"))
    assert data["used"] == 4


def test_describe_reports_the_same_identity_error_as_acquire(monkeypatch, tmp_path):
    """预检与发送门禁口径一致：显式 TOTAL 与账本 limit 不符时两边都报错.

    旧 describe 直接用账本 limit 算 remaining，预检显示"还有额度"，
    实际 acquire 会因身份不一致拒收。
    """
    chapter = tmp_path / "chapter_001"
    _write_ledger(chapter / "step02a_image_budget.json", _valid_ledger(1, 172))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "3")

    described = post_budget.describe("image")
    assert "error" in described, described
    assert "身份不一致" in described["error"]
    assert "remaining" not in described
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_describe_never_creates_lock_file(monkeypatch, tmp_path):
    """只读预检对章目录零写入（plan-only 的硬约束）."""
    chapter = tmp_path / "chapter_001"
    ledger = chapter / "step02a_image_budget.json"
    _write_ledger(ledger, _valid_ledger(2, 5))

    described = post_budget.describe("image")
    assert described["remaining"] == 3
    assert not ledger.with_suffix(ledger.suffix + ".lock").exists()
