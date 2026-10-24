# -*- coding: utf-8 -*-
"""受控运行器的**实际发送**预算：内部重试也逐次计数，预写失败则不发."""
import pytest

from _text_gen.agent_plan_provider import AgentPlanTextService
import tools.minimal_real_run as m


class _Resp:
    status_code = 200


def _reset():
    m.SENDS.update({"logical": 0, "attempts": 0, "cap": 3,
                    "abort": False, "calls": [], "sends": []})


def test_send_budget_counts_each_actual_attempt_and_stops_at_cap(monkeypatch, tmp_path):
    _reset()
    _calls = []

    def _fake(self, url, payload, timeout, trace):
        _calls.append(payload)
        return _Resp()

    _orig = AgentPlanTextService._send_once
    monkeypatch.setattr(AgentPlanTextService, "_send_once", _fake)
    m._install_send_budget(tmp_path)
    try:
        for _i in range(3):
            AgentPlanTextService._send_once(None, "u", {"i": _i}, 1, {})
        with pytest.raises(RuntimeError):
            AgentPlanTextService._send_once(None, "u", {"i": 4}, 1, {})
    finally:
        monkeypatch.setattr(AgentPlanTextService, "_send_once", _orig)
    assert len(_calls) == 3
    assert m.SENDS["attempts"] == 3
    assert len(list((tmp_path / "sends").glob("*_request.json"))) == 3


def test_presend_evidence_write_failure_sends_nothing(monkeypatch, tmp_path):
    _reset()
    _calls = []

    def _fake(self, url, payload, timeout, trace):
        _calls.append(payload)
        return _Resp()

    _orig = AgentPlanTextService._send_once
    monkeypatch.setattr(AgentPlanTextService, "_send_once", _fake)
    monkeypatch.setattr(m, "_write", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError("denied")))
    m._install_send_budget(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            AgentPlanTextService._send_once(None, "u", {"i": 1}, 1, {})
    finally:
        monkeypatch.setattr(AgentPlanTextService, "_send_once", _orig)
    assert _calls == []
    assert m.SENDS["attempts"] == 1      # 预算已扣，但没有发出
    assert m.SENDS["abort"] is True
