from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app


def test_edge_proxy_forwards_request(monkeypatch):
    monkeypatch.setattr(settings, "edge_proxy_target_url", "http://upstream.internal")

    mock_request = AsyncMock(
        return_value=httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"status":"ok","source":"vps"}',
        )
    )

    with patch("app.main.httpx.AsyncClient.request", mock_request):
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/health", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "source": "vps"}
    kwargs = mock_request.await_args.kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["url"] == "http://upstream.internal/health"
    assert kwargs["headers"]["authorization"] == "Bearer token"

