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
