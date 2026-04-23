import pytest

from app.core.config import settings
from app.integrations.direct.gemini_client import GeminiLiveClient


class _DummyWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_gemini_connect_forces_ipv4_and_open_timeout(monkeypatch):
    ws = _DummyWebSocket()
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return ws

    monkeypatch.setattr("app.integrations.direct.gemini_client.websockets.connect", fake_connect)

    settings.gemini_api_key = "test-key"
    settings.gemini_open_timeout = 17.0
    settings.gemini_force_ipv4 = True

    client = GeminiLiveClient(
        on_text=lambda *_: None,
        on_audio=lambda *_: None,
        on_close=lambda: None,
    )

    async def fast_wait():
        client._setup_done.set()
        await original_wait()

    original_wait = client._setup_done.wait
    monkeypatch.setattr(client._setup_done, "wait", fast_wait)

    await client.connect("test system prompt")

    assert captured["kwargs"]["open_timeout"] == 17.0
    assert captured["kwargs"]["family"]
    assert captured["kwargs"]["ping_interval"] == 20
    assert captured["kwargs"]["ping_timeout"] == 10
    assert ws.sent, "setup message should be sent after websocket opens"

    await client.close()


@pytest.mark.asyncio
async def test_gemini_connect_can_skip_ipv4_force(monkeypatch):
    ws = _DummyWebSocket()
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["kwargs"] = kwargs
        return ws

    monkeypatch.setattr("app.integrations.direct.gemini_client.websockets.connect", fake_connect)

    settings.gemini_api_key = "test-key"
    settings.gemini_force_ipv4 = False

    client = GeminiLiveClient(
        on_text=lambda *_: None,
        on_audio=lambda *_: None,
        on_close=lambda: None,
    )

    async def fast_wait():
        client._setup_done.set()
        await original_wait()

    original_wait = client._setup_done.wait
    monkeypatch.setattr(client._setup_done, "wait", fast_wait)

    await client.connect("test system prompt")

    assert "family" not in captured["kwargs"]

    await client.close()
