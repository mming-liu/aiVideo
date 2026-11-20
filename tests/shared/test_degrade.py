# -*- coding: utf-8 -*-
"""shared.degrade：降级登记自身不得抛异常、上限与日志路由（离线）."""
from __future__ import annotations

import logging

import pytest

from shared import degrade


@pytest.fixture(autouse=True)
def _reset():
    degrade.reset_degraded()
    yield
    degrade.reset_degraded()


class _Logger:
    def __init__(self):
        self.lines = []

    def warn(self, msg):
        self.lines.append(msg)


class _BrokenLogger:
    def warn(self, msg):
        raise RuntimeError("logger down")


class _BadStr(Exception):
    def __str__(self):
        raise ValueError("no str")


def test_records_and_routes_to_logger():
    log = _Logger()
    degrade.note_degraded("pkg/mod.py:fn", ImportError("boom"), logger=log)
    assert degrade.degraded_sites() == [{"site": "pkg/mod.py:fn", "error": "ImportError: boom"}]
    assert log.lines == ["[DEGRADED] pkg/mod.py:fn: ImportError: boom"]


def test_never_raises_with_broken_logger_or_bad_exception(caplog):
    with caplog.at_level(logging.WARNING, logger="aiVideo.degrade"):
        degrade.note_degraded("a:b", RuntimeError("x"), logger=_BrokenLogger())
        degrade.note_degraded("a:c", _BadStr())
    sites = degrade.degraded_sites()
    assert [s["site"] for s in sites] == ["a:b", "a:c"]
    assert sites[1]["error"] == "_BadStr"
    assert any("a:b" in r.getMessage() for r in caplog.records)


def test_record_cap_and_per_site_log_cap():
    log = _Logger()
    for _ in range(degrade._MAX_RECORDS + 10):
        degrade.note_degraded("hot:path", ValueError("v"), logger=log)
    assert len(degrade.degraded_sites()) == degrade._MAX_RECORDS
    assert len(log.lines) == degrade._MAX_LOGS_PER_SITE


def test_degraded_sites_returns_copy():
    degrade.note_degraded("x:y", ValueError("v"))
    got = degrade.degraded_sites()
    got[0]["site"] = "mutated"
    assert degrade.degraded_sites()[0]["site"] == "x:y"
