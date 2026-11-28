import httpx
import pytest
import requests

from tools.probe_isolation import block_external_transport, ProbeIsolationError


@pytest.mark.parametrize("url", ["https://example.invalid/path", "http://example.invalid/path"])
def test_probe_blocks_http_authority_even_if_proxy_connection_is_local(monkeypatch, url):
    calls = []
    def send(self, request):
        calls.append(str(request.url))
        return httpx.Response(200)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
    with block_external_transport(), httpx.Client() as client:
        with pytest.raises(ProbeIsolationError):
            client.get(url)
    assert calls == []


def test_probe_allows_explicit_mock_http_transport():
    with block_external_transport(), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        assert client.get("https://example.invalid/path").status_code == 200


def test_probe_blocks_requests_authority_before_adapter_send(monkeypatch):
    calls = []
    def send(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with block_external_transport(), requests.Session() as session:
        with pytest.raises(ProbeIsolationError):
            session.get("https://example.invalid/path", proxies={"https": "http://127.0.0.1:1"})
    assert calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "example.invalid"])
def test_probe_checks_async_http_target_and_preserves_loopback(monkeypatch, host):
    import asyncio
    calls = []
    async def send(self, request):
        calls.append(str(request.url))
        return httpx.Response(200)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    async def run():
        with block_external_transport():
            async with httpx.AsyncClient() as client:
                if host == "127.0.0.1":
                    assert (await client.get("http://" + host)).status_code == 200
                else:
                    with pytest.raises(ProbeIsolationError):
                        await client.get("http://" + host)
    asyncio.run(run())
    assert len(calls) == (1 if host == "127.0.0.1" else 0)


def test_probe_checks_urllib_target_before_local_proxy_transport(monkeypatch):
    import urllib.request
    calls = []
    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", lambda *a, **k: calls.append(a))
    with block_external_transport():
        with pytest.raises(ProbeIsolationError):
            urllib.request.HTTPHandler().do_open(None, urllib.request.Request("http://example.invalid/path"))
    assert calls == []


def test_probe_blocks_nonblocking_connect_ex(monkeypatch):
    import socket
    calls = []
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: calls.append(address) or 0)
    with block_external_transport(), socket.socket() as sock:
        with pytest.raises(ProbeIsolationError):
            sock.connect_ex(("198.51.100.1", 443))
        assert sock.connect_ex(("127.0.0.1", 443)) == 0
    assert calls == [("127.0.0.1", 443)]
