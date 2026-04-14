from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.integrations.telephony.mango_client import MangoApiConfig, MangoClient, MangoClientError


@pytest.mark.anyio
async def test_list_extensions_uses_cache_after_429(monkeypatch):
    client = MangoClient(
        MangoApiConfig(
            base_url="https://example.invalid/vpbx",
            api_key="key",
            api_salt="salt",
        )
    )
    MangoClient._shared_extensions_cache.clear()
    try:
        calls = {"count": 0}

        async def fake_post_json(path: str, payload: dict):
            calls["count"] += 1
            if calls["count"] == 1:
                return {
                    "users": [
                        {
                            "general": {"name": "Ext User"},
                            "telephony": {"extension": "10", "outgoingline": "79585382099"},
                        }
                    ]
                }
            raise MangoClientError(
                "rate limited",
                stage="http_response",
                http_status=429,
                detail={"status": 429},
            )

        monkeypatch.setattr(client, "_post_json", fake_post_json)

        first = await client.list_extensions()
        second = await client.list_extensions()

        assert len(first) == 1
        assert len(second) == 1
        assert second[0].extension == "10"
        assert calls["count"] == 1
    finally:
        await client.aclose()
        MangoClient._shared_extensions_cache.clear()
