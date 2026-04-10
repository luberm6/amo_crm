from __future__ import annotations

from app.core.config import settings


async def test_metrics_endpoint_available(client) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert body
    # If prometheus-client is installed, at least one metric family should exist.
    # Otherwise fallback text is returned explicitly.
    assert (
        "freeswitch_session_attach_total" in body
        or "metrics_disabled_or_prometheus_unavailable" in body
    )


async def test_metrics_endpoint_respects_feature_flag(client) -> None:
    old = settings.metrics_enabled
    try:
        settings.metrics_enabled = False
        resp = await client.get("/metrics")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "metrics_disabled"
    finally:
        settings.metrics_enabled = old
