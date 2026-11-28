import asyncio
import threading
from types import SimpleNamespace as NS

import pytest

from _text_gen import deepseek_provider as dp
from _text_gen.models import TextGenerationRequest, TextGenerationResult
from _text_gen.service import TextGenerationService, _LedgeredService


@pytest.mark.parametrize("status", ["completed", "incomplete", "failed", "missing", "mismatch"])
def test_real_provider_accepts_only_a_complete_terminal_stream(monkeypatch, status):
    monkeypatch.setattr(dp.config, "DEEPSEEK_STREAM", True)
    monkeypatch.setattr(dp.config, "validate", lambda: None)
    calls=[]
    final=NS(status=status, output=[NS(type="message", content=[NS(type="output_text", text='{"ok":true}')])],
             usage=None, error=NS(message="remote failure"), incomplete_details=NS(reason="max_output_tokens"))
    events=[NS(type="response.output_text.delta", delta='{"ok":')]
    if status != "missing":
        events.append(NS(type="response.completed" if status=="mismatch" else "response."+status, response=final))

    class Stream:
        closed=False
        def __iter__(self): return iter(events)
        def close(self): self.closed=True

    stream=Stream()
    def create(**kwargs):
        calls.append(kwargs)
        return stream
    client=NS(responses=NS(create=create))
    monkeypatch.setattr(__import__('openai'), "OpenAI", lambda **kw: client)
    result=dp.DeepSeekTextService().generate(TextGenerationRequest('system','user'))
    assert calls[0]["stream"] is True and len(calls)==1
    assert "background" not in calls[0]
    assert stream.closed
    assert result.success is (status=="completed")
    assert result.text == ('{"ok":true}' if status in ("completed", "incomplete") else '')
    if status == "incomplete":
        assert result.truncated is True


def test_async_service_keeps_the_event_loop_available_and_uses_one_ledger_call(monkeypatch):
    entered=threading.Event(); release=threading.Event(); recorded=[]
    class Service(TextGenerationService):
        def generate(self, req):
            entered.set()
            assert release.wait(3)
            return TextGenerationResult(success=True,text="done")
    monkeypatch.setattr('shared.call_ledger.record_call', lambda *a, **kw: recorded.append(kw))
    async def run():
        service=_LedgeredService(Service(),'offline')
        task=asyncio.create_task(service.generate_async(TextGenerationRequest('s','u')))
        for _ in range(1000):
            if entered.is_set(): break
            await asyncio.sleep(.001)
        assert entered.is_set() and not task.done()
        release.set()
        result=await task
        assert result.text=="done" and result.call_id
    try: asyncio.run(run())
    finally: release.set()
    assert len(recorded)==1


def test_slow_first_event_is_bounded_by_wall_clock(monkeypatch):
    """P2（2026-10-29）：首事件迟迟不到时，必须在墙钟预算附近返回，而不是等事件到来.

    旧实现把流式迭代放在 TransportGate 之外：create() 返回后主线程按超时返回，
    工作线程（或主线程）仍阻塞在"取下一条事件"。本用例让首事件一直等到测试放行，
    要求调用按墙钟预算（0.05s）返回失败，而不是陪流一起等；迟到的流最终被关闭。
    """
    import time as _time

    monkeypatch.setattr(dp.config, "DEEPSEEK_STREAM", True)
    monkeypatch.setattr(dp.config, "validate", lambda: None)
    release = threading.Event()
    seen = {}
    final = NS(status="completed", output=[], usage=None, error=None,
               incomplete_details=None)

    class Stream:
        def __iter__(self):
            release.wait(5)
            return iter([NS(type="response.completed", response=final)])

        def close(self):
            seen["closed"] = True

    stream = Stream()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return stream

    monkeypatch.setattr(__import__("openai"), "OpenAI",
                        lambda **kw: NS(responses=NS(create=create)))
    req = TextGenerationRequest("system", "user",
                                deadline_monotonic=_time.monotonic() + 0.05)
    start = _time.monotonic()
    try:
        result = dp.DeepSeekTextService().generate(req)
        elapsed = _time.monotonic() - start
        assert len(calls) == 1, "create() 只允许一次"
        assert result.success is False
        assert elapsed < 0.5, "超时必须按墙钟预算返回，实际 %.3fs" % elapsed
        err = result.error or ""
        assert ("墙钟" in err) or ("截止" in err) or ("未返回" in err), err
    finally:
        release.set()
    _time.sleep(0.25)
    assert seen.get("closed") is True, "迟到的流必须关闭，不能继续读取"
