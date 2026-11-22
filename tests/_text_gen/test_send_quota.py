# -*- coding: utf-8 -*-
"""M07 发送额度：每次实际 POST 前原子占用（含重试），第 41 次到不了传输层."""
from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from _text_gen import send_quota as sq
from _text_gen.models import TextGenerationRequest
from tests._text_gen.test_agent_plan_provider import OPENCODE_URL, _make_text_service, _ok_response


@pytest.fixture
def quota(tmp_path, monkeypatch):
    path = tmp_path / "send_quota.json"
    monkeypatch.setenv(sq.ENV_FILE, str(path))
    return path


def _used(path):
    return json.loads(path.read_text(encoding="utf-8"))["used"]


def test_no_env_means_no_quota(monkeypatch):
    monkeypatch.delenv(sq.ENV_FILE, raising=False)
    assert sq.reserve(provider="p", endpoint="e") is None


def test_40th_send_goes_out_and_41st_never_reaches_transport(monkeypatch, quota):
    sq.init_file(quota, 40)
    svc = _make_text_service(monkeypatch, base_url=OPENCODE_URL, shared_base_url=OPENCODE_URL)
    req = TextGenerationRequest(system_prompt="s", user_prompt="u")
    with patch("requests.post", return_value=_ok_response("ok")) as post, \
            patch("time.sleep", return_value=None):
        for _ in range(40):
            assert svc.generate(req).success
        assert post.call_count == 40 and _used(quota) == 40
        with pytest.raises(sq.SendQuotaExhausted):
            svc.generate(req)
    assert post.call_count == 40, "第 41 次不得到达传输层"
    assert _used(quota) == 40
    # 额度只管 POST：用满之后本地收尾记录照常可写
    (quota.parent / "summary.json").write_text("{}", encoding="utf-8")


def test_retries_are_counted(monkeypatch, quota):
    """5xx 时 provider 自己重发一次：两次 POST 各占一次额度."""
    sq.init_file(quota, 3)
    svc = _make_text_service(monkeypatch, base_url=OPENCODE_URL, shared_base_url=OPENCODE_URL)
    req = TextGenerationRequest(system_prompt="s", user_prompt="u")
    with patch("requests.post", return_value=MagicMock(status_code=503, text="busy")) as post, \
            patch("time.sleep", return_value=None):
        svc.generate(req)
    assert post.call_count == 2 and _used(quota) == 2
    with patch("requests.post", return_value=MagicMock(status_code=503, text="busy")) as post, \
            patch("time.sleep", return_value=None), pytest.raises(sq.SendQuotaExhausted):
        svc.generate(req)
    assert post.call_count == 1, "只剩 1 次额度：首发后重试被挡在传输层之前"
    assert _used(quota) == 3


@pytest.mark.parametrize("content", [None, "{bad", json.dumps({"max": "40", "used": 0}),
                                     json.dumps({"max": 40, "used": -1})])
def test_unreadable_quota_state_blocks_sending(monkeypatch, quota, content):
    if content is not None:
        quota.write_text(content, encoding="utf-8")
    svc = _make_text_service(monkeypatch, base_url=OPENCODE_URL, shared_base_url=OPENCODE_URL)
    with patch("requests.post", return_value=_ok_response("ok")) as post, \
            pytest.raises(sq.SendQuotaUnavailable):
        svc.generate(TextGenerationRequest(system_prompt="s", user_prompt="u"))
    assert post.call_count == 0


def test_concurrent_reservations_never_exceed_max(quota):
    sq.init_file(quota, 25)
    got, denied = [], []

    def worker():
        for _ in range(10):
            try:
                got.append(sq.reserve(provider="p", endpoint="e"))
            except sq.SendQuotaExhausted:
                denied.append(1)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == list(range(1, 26)) and len(denied) == 25
    assert _used(quota) == 25


def test_init_file_refuses_to_overwrite(quota):
    sq.init_file(quota, 1)
    with pytest.raises(FileExistsError):
        sq.init_file(quota, 1)
