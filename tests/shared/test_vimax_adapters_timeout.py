# -*- coding: utf-8 -*-
"""第 1 包 M03：视频提交外层超时分支（原先引用未定义的 `_log_utils`）.

断言：超时后抛出的仍是超时异常（不是 NameError），且告警写进日志文件。
零网络：绕过 `__init__`，用桩服务替代真实 provider。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _SlowSvc:
    def create_task(self, req):
        time.sleep(0.3)
        return "late-task"

    def create_prepared_task(self, prepared, context=None):
        time.sleep(0.3)
        return "late-task"


class _Ready:
    @staticmethod
    def is_ready():
        return True


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    from shared import log_utils
    from shared.vimax_adapters import VideoGeneratorAiVideoAdapter
    log_file = tmp_path / "step02.log"
    monkeypatch.setattr(log_utils, "resolve_log_file", lambda name="step02b.log": log_file)
    obj = object.__new__(VideoGeneratorAiVideoAdapter)
    obj._svc = _SlowSvc()
    obj._VIDEO_SUBMIT_TIMEOUT = 0.05
    obj.prepare_request = lambda req, ctx=None: _Ready()
    return obj, log_file


def test_submit_task_timeout_reraises_timeout_and_logs(adapter):
    obj, log_file = adapter
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(obj.submit_task(object()))
    assert "外层超时" in log_file.read_text(encoding="utf-8")


def test_submit_prepared_timeout_reraises_timeout_and_logs(adapter):
    obj, log_file = adapter
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(obj._submit_prepared(object(), None))
    assert "候选提交" in log_file.read_text(encoding="utf-8")
