from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.config as cfg
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


@pytest.mark.anyio
async def test_admin_login_success(session):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "admin_token_ttl_seconds", 600),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post(
                "/v1/admin/auth/login",
                json={"email": "admin@example.com", "password": "super-secret"},
            )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["email"] == "admin@example.com"
    assert payload["access_token"]


@pytest.mark.anyio
async def test_admin_me_requires_bearer_token(session):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/v1/admin/auth/me")

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_admin_token"


@pytest.mark.anyio
async def test_browser_calls_endpoint_requires_admin_auth(client: AsyncClient):
    resp = await client.post("/v1/browser-calls", json={"label": "sandbox"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "admin_auth_not_configured"


@pytest.mark.anyio
async def test_browser_calls_endpoint_accepts_admin_token(session):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "gemini_api_key", "test-gemini-key"),
        patch.object(cfg.settings, "direct_voice_strategy", "gemini_primary"),
        patch.object(cfg.settings, "gemini_audio_output_enabled", True),
        patch.object(cfg.settings, "gemini_audio_input_enabled", True),
        patch.object(cfg.settings, "direct_initial_greeting_enabled", False),
    ):
        from tests.conftest import MockGeminiLiveClient

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            login_resp = await ac.post(
                "/v1/admin/auth/login",
                json={"email": "admin@example.com", "password": "super-secret"},
            )
            token = login_resp.json()["access_token"]
            with patch(
                "app.integrations.direct.session_manager.GeminiLiveClient",
                new=MockGeminiLiveClient,
            ):
                create_resp = await ac.post(
                    "/v1/browser-calls",
                    json={"label": "sandbox"},
                    headers={"Authorization": f"Bearer {token}"},
                )

    assert create_resp.status_code == 201
    assert create_resp.json()["voice_strategy"] == "gemini_primary"


@pytest.mark.anyio
async def test_browser_call_debug_test_tone_endpoint_accepts_admin_token(tmp_path):
    app = create_app()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'browser_debug_tone.sqlite3'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "gemini_api_key", "test-gemini-key"),
        patch.object(cfg.settings, "direct_voice_strategy", "gemini_primary"),
        patch.object(cfg.settings, "gemini_audio_output_enabled", True),
        patch.object(cfg.settings, "gemini_audio_input_enabled", True),
        patch.object(cfg.settings, "direct_initial_greeting_enabled", False),
    ):
        from tests.conftest import MockGeminiLiveClient

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            login_resp = await ac.post(
                "/v1/admin/auth/login",
                json={"email": "admin@example.com", "password": "super-secret"},
            )
            token = login_resp.json()["access_token"]
            with patch(
                "app.integrations.direct.session_manager.GeminiLiveClient",
                new=MockGeminiLiveClient,
            ):
                create_resp = await ac.post(
                    "/v1/browser-calls",
                    json={"label": "sandbox"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                call_id = create_resp.json()["call_id"]
                debug_resp = await ac.post(
                    f"/v1/browser-calls/{call_id}/debug/test-tone",
                    headers={"Authorization": f"Bearer {token}"},
                )
                await ac.post(
                    f"/v1/browser-calls/{call_id}/stop",
                    headers={"Authorization": f"Bearer {token}"},
                )

    await engine.dispose()
    assert debug_resp.status_code == 200
    payload = debug_resp.json()
    assert payload["action"] == "test_tone"
    assert payload["chunks_enqueued"] > 0


@pytest.mark.anyio
async def test_browser_call_debug_test_tts_requires_real_voice_provider(tmp_path):
    app = create_app()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'browser_debug_tts.sqlite3'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "gemini_api_key", "test-gemini-key"),
        patch.object(cfg.settings, "direct_voice_strategy", "gemini_primary"),
        patch.object(cfg.settings, "gemini_audio_output_enabled", True),
        patch.object(cfg.settings, "gemini_audio_input_enabled", True),
        patch.object(cfg.settings, "direct_initial_greeting_enabled", False),
        patch.object(cfg.settings, "elevenlabs_enabled", False),
    ):
        from tests.conftest import MockGeminiLiveClient

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            login_resp = await ac.post(
                "/v1/admin/auth/login",
                json={"email": "admin@example.com", "password": "super-secret"},
            )
            token = login_resp.json()["access_token"]
            with patch(
                "app.integrations.direct.session_manager.GeminiLiveClient",
                new=MockGeminiLiveClient,
            ):
                create_resp = await ac.post(
                    "/v1/browser-calls",
                    json={"label": "sandbox"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                call_id = create_resp.json()["call_id"]
                debug_resp = await ac.post(
                    f"/v1/browser-calls/{call_id}/debug/test-tts",
                    headers={"Authorization": f"Bearer {token}"},
                )
                await ac.post(
                    f"/v1/browser-calls/{call_id}/stop",
                    headers={"Authorization": f"Bearer {token}"},
                )

    await engine.dispose()
    assert debug_resp.status_code == 409
    assert "stub voice returns silence" in debug_resp.json()["detail"]
