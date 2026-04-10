from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.integrations.browser.engine import BrowserDirectEngine
from app.integrations.browser.registry import BrowserSessionRegistry
from app.integrations.browser.telephony import BrowserTelephonyAdapter
from app.integrations.call_engine.router_engine import RoutingCallEngine
from app.integrations.direct.session_manager import DirectSessionManager
from app.integrations.voice.stub import StubVoiceProvider
from app.models.call import CallMode, CallStatus
from app.models.transcript import TranscriptEntry
from app.repositories.transcript_repo import TranscriptRepository
from app.services.agent_profile_service import AgentProfileService
from app.services.call_service import CallService
from app.services.knowledge_base_service import KnowledgeBaseService
from tests.conftest import MockGeminiLiveClient


@pytest.fixture
def browser_test_env():
    old_values = {
        "gemini_api_key": settings.gemini_api_key,
        "direct_voice_strategy": settings.direct_voice_strategy,
        "gemini_audio_output_enabled": settings.gemini_audio_output_enabled,
        "gemini_audio_input_enabled": settings.gemini_audio_input_enabled,
        "direct_initial_greeting_enabled": settings.direct_initial_greeting_enabled,
        "direct_model_response_timeout_seconds": settings.direct_model_response_timeout_seconds,
    }
    settings.gemini_api_key = "test-gemini-key"
    settings.direct_voice_strategy = "gemini_primary"
    settings.gemini_audio_output_enabled = True
    settings.gemini_audio_input_enabled = True
    settings.direct_initial_greeting_enabled = False
    settings.direct_model_response_timeout_seconds = 30.0
    yield
    for key, value in old_values.items():
        setattr(settings, key, value)


def _make_browser_service(session, test_session_factory):
    registry = BrowserSessionRegistry()
    session_manager = DirectSessionManager()
    engine = BrowserDirectEngine(
        session_manager=session_manager,
        telephony=BrowserTelephonyAdapter(registry),
        voice=StubVoiceProvider(),
        session_factory=test_session_factory,
    )
    router = RoutingCallEngine(
        vapi_engine=None,
        direct_engine=None,
        fallback_engine=engine,
        browser_engine=engine,
    )
    return (
        CallService(session=session, engine=router),
        registry,
        session_manager,
    )


@pytest.fixture
async def browser_db_engine(tmp_path):
    db_path = tmp_path / "browser_sandbox.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def browser_session(browser_db_engine) -> AsyncSession:
    factory = async_sessionmaker(bind=browser_db_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def browser_session_factory(browser_db_engine):
    return async_sessionmaker(bind=browser_db_engine, expire_on_commit=False)


@pytest.mark.anyio
async def test_create_browser_session(browser_session, browser_session_factory, browser_test_env):
    service, registry, session_manager = _make_browser_service(browser_session, browser_session_factory)
    with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
        call = await service.create_call(raw_phone="qa-sandbox", mode=CallMode.BROWSER)
        await browser_session.commit()

    assert call.status == CallStatus.IN_PROGRESS
    assert call.route_used == "browser"
    assert call.mango_call_id is not None
    assert session_manager.get_session(call.mango_call_id) is not None
    bridge = registry.get_bridge(call.id)
    assert bridge is not None
    assert bridge.snapshot().is_open is True
    await service.stop_call(call.id, actor="browser-test")
    await browser_session.commit()


@pytest.mark.anyio
async def test_stop_browser_session(browser_session, browser_session_factory, browser_test_env):
    service, _registry, session_manager = _make_browser_service(browser_session, browser_session_factory)
    with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
        call = await service.create_call(raw_phone="qa-stop", mode=CallMode.BROWSER)
        await browser_session.commit()
        stopped = await service.stop_call(call.id, actor="browser-test")
        await browser_session.commit()

    assert stopped.status == CallStatus.STOPPED
    assert session_manager.get_session(call.mango_call_id) is None


@pytest.mark.anyio
async def test_browser_transcript_persists(browser_session, browser_session_factory, browser_test_env):
    service, _registry, session_manager = _make_browser_service(browser_session, browser_session_factory)
    with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
        call = await service.create_call(raw_phone="qa-transcript", mode=CallMode.BROWSER)
        await browser_session.commit()
        live_session = session_manager.get_session(call.mango_call_id)
        assert live_session is not None
        assert live_session.gemini_client is not None

        live_session.gemini_client.simulate_text("assistant", "Привет из browser sandbox")
        await live_session.event_handler.flush(timeout=1.0)

    async with browser_session_factory() as verify_session:
        repo = TranscriptRepository(TranscriptEntry, verify_session)
        entries = await repo.get_by_call(call.id)
    assert any(entry.text == "Привет из browser sandbox" for entry in entries)
    await service.stop_call(call.id, actor="browser-test")
    await browser_session.commit()


@pytest.mark.anyio
async def test_browser_cleanup_on_disconnect(browser_session, browser_session_factory, browser_test_env):
    service, registry, session_manager = _make_browser_service(browser_session, browser_session_factory)
    with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
        call = await service.create_call(raw_phone="qa-disconnect", mode=CallMode.BROWSER)
        await browser_session.commit()
        bridge = registry.get_bridge(call.id)
        assert bridge is not None

        bridge.attach_client()
        await bridge.detach_client(reason="browser_disconnect")
        await asyncio.sleep(0.2)

    assert session_manager.get_session(call.mango_call_id) is None
    async with browser_session_factory() as verify_session:
        refreshed = await verify_session.get(type(call), call.id)
    assert refreshed.status in {
        CallStatus.COMPLETED,
        CallStatus.STOPPED,
        CallStatus.FAILED,
    }


@pytest.mark.anyio
async def test_browser_session_uses_agent_runtime_prompt_and_knowledge_context(
    browser_session,
    browser_session_factory,
    browser_test_env,
):
    service, _registry, _session_manager = _make_browser_service(browser_session, browser_session_factory)

    class RecordingGeminiClient(MockGeminiLiveClient):
        prompts: list[str] = []

        async def connect(self, system_prompt: str) -> None:
            type(self).prompts.append(system_prompt)
            await super().connect(system_prompt)

    kb_service = KnowledgeBaseService(browser_session)
    company = await kb_service.upsert_company_profile(
        name="AMO Voice",
        legal_name=None,
        description="Conversational sales automation",
        value_proposition="Qualify leads faster",
        target_audience="Sales teams",
        contact_info="support@example.com",
        website_url=None,
        working_hours=None,
        compliance_notes="Never invent discounts",
        is_active=True,
        config={"locale": "ru-RU"},
    )
    assert company.name == "AMO Voice"

    agent_service = AgentProfileService(browser_session)
    agent = await agent_service.create_profile(
        name="Browser QA Agent",
        is_active=True,
        system_prompt="Speak clearly and helpfully.",
        tone_rules=None,
        business_rules=None,
        sales_objectives=None,
        greeting_text="Здравствуйте! Чем могу помочь?",
        transfer_rules=None,
        prohibited_promises=None,
        voice_strategy="gemini_primary",
        config={},
    )
    document = await kb_service.create_document(
        title="Refund policy",
        category="company_policy",
        content="Refunds require manager approval within 14 days.",
        is_active=True,
        notes=None,
        metadata={"source": "manual"},
    )
    await kb_service.bind_document(
        agent_id=agent.id,
        knowledge_document_id=document.id,
        role="policy",
    )
    await browser_session.commit()

    RecordingGeminiClient.prompts = []
    with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=RecordingGeminiClient):
        call = await service.create_call(
            raw_phone="qa-agent-context",
            mode=CallMode.BROWSER,
            agent_profile_id=agent.id,
        )
        await browser_session.commit()

    assert call.agent_profile_id == agent.id
    assert RecordingGeminiClient.prompts
    runtime_prompt = RecordingGeminiClient.prompts[-1]
    assert "Speak clearly and helpfully." in runtime_prompt
    assert "Company Context:" in runtime_prompt
    assert "AMO Voice" in runtime_prompt
    assert "Knowledge Context:" in runtime_prompt
    assert "Refund policy" in runtime_prompt
    assert "Refunds require manager approval within 14 days." in runtime_prompt

    await service.stop_call(call.id, actor="browser-test")
    await browser_session.commit()
