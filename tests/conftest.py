"""
Pytest fixtures for the test suite.

Uses an in-memory SQLite database (via aiosqlite) so tests run without
a real Postgres instance. The engine uses a sync-compatible async driver.

For integration tests that require Postgres-specific features (JSONB, etc.),
set TEST_DATABASE_URL to a real Postgres URL in your CI environment.
"""

import os

# Set environment to 'testing' before any imports that use settings
os.environ["ENVIRONMENT"] = "testing"
import uuid

# Import and configure settings AFTER environment is set
from app.core.config import settings
# Disable rate limiting by default for all tests (can be overridden per-test if needed)
settings.rate_limit_enabled = False
# Keep tests hermetic even if local bootstrap created a developer `.env`.
settings.admin_email = ""
settings.admin_password = ""
settings.admin_auth_secret = ""
settings.provider_settings_secret = ""
from collections.abc import AsyncGenerator
from typing import Callable, List, Optional

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.api.deps import get_call_engine
from app.integrations.call_engine.stub import StubEngine
from app.integrations.direct.engine import DirectGeminiEngine
from app.integrations.direct.session_manager import DirectSessionManager
from app.integrations.telephony.stub import StubTelephonyAdapter
from app.integrations.transfer_engine.stub import StubTransferEngine
from app.integrations.voice.stub import StubVoiceProvider
from app.main import create_app
from app.models.manager import Manager
from app.models.transfer import TransferRecord
from app.repositories.manager_repo import ManagerRepository
from app.services.call_service import CallService
from app.services.transfer_service import TransferService


# ── MockGeminiLiveClient ──────────────────────────────────────────────────────

class MockGeminiLiveClient:
    """
    Тестовая замена GeminiLiveClient — не открывает WebSocket.

    Используется в тестах DirectSessionManager и DirectGeminiEngine.
    Методы записывают вызовы в атрибуты для проверки в тестах.
    simulate_text() позволяет сымитировать ответ от Gemini.
    """

    def __init__(
        self,
        on_text: Callable[[str, str], None],
        on_audio: Callable[[bytes], None],
        on_close: Callable[[], None],
        on_interrupted: Optional[Callable[[], None]] = None,
        on_turn_complete: Optional[Callable[[], None]] = None,
        on_tool_call: Optional[Callable] = None,
        audio_input: bool = False,
        audio_output: bool = False,
        transcription_output: bool = False,
        voice_name: Optional[str] = None,
        language_code: str = "ru-RU",
        model_id: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> None:
        self._on_text = on_text
        self._on_audio = on_audio
        self._on_close = on_close
        self._on_interrupted = on_interrupted
        self._on_turn_complete = on_turn_complete
        self.injected_instructions: List[str] = []
        self.sent_audio_chunks: List[bytes] = []
        self.connected: bool = False
        self.closed: bool = False

    async def connect(self, system_prompt: str) -> None:
        self.connected = True

    async def inject_instruction(self, instruction: str) -> None:
        self.injected_instructions.append(instruction)

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self.sent_audio_chunks.append(pcm_bytes)

    async def close(self) -> None:
        self.closed = True

    def simulate_text(self, role: str, text: str) -> None:
        """Сымитировать текстовый ответ от Gemini."""
        self._on_text(role, text)

    def simulate_audio(self, pcm: bytes) -> None:
        """Сымитировать аудио-ответ от Gemini."""
        self._on_audio(pcm)

    def simulate_close(self) -> None:
        """Сымитировать закрытие WS соединения."""
        self._on_close()

# Use aiosqlite for unit tests, real Postgres for integration tests
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as s:
        yield s
        await s.rollback()  # Rollback after each test for isolation


@pytest.fixture
def app(session: AsyncSession) -> FastAPI:
    """FastAPI app with the DB session overridden to use the test session."""
    application = create_app()

    async def override_get_db():
        yield session

    async def override_get_call_engine():
        # Use RoutingCallEngine with no real sub-engines to keep routing logic
        # (e.g. mode=vapi without credentials → 502) but avoid real Gemini connections.
        from app.integrations.call_engine.router_engine import RoutingCallEngine
        return RoutingCallEngine(
            vapi_engine=None,
            direct_engine=None,
            browser_engine=None,
            fallback_engine=StubEngine(),
        )

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_call_engine] = override_get_call_engine
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def call_service(session: AsyncSession) -> CallService:
    return CallService(session=session, engine=StubEngine())


@pytest.fixture
def test_session_factory(test_engine) -> async_sessionmaker:
    """async_sessionmaker для тестов DirectEventHandler."""
    return async_sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture
def direct_session_manager() -> DirectSessionManager:
    """Свежий DirectSessionManager для каждого теста."""
    return DirectSessionManager()


@pytest.fixture
def direct_engine(direct_session_manager, test_session_factory) -> DirectGeminiEngine:
    """DirectGeminiEngine со stub telephony и voice, mock session manager."""
    engine = DirectGeminiEngine(
        session_manager=direct_session_manager,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=test_session_factory,
    )
    return engine


@pytest.fixture
def transfer_service(session: AsyncSession) -> TransferService:
    return TransferService(session=session, engine=StubTransferEngine())


@pytest.fixture
async def manager_sales(session: AsyncSession) -> Manager:
    """Active, available manager in the 'sales' department, priority 1."""
    mgr = Manager(
        name="Иван Продаж",
        phone="+79991110001",
        telegram_id=100001,
        is_active=True,
        is_available=True,
        priority=1,
        department="sales",
    )
    repo = ManagerRepository(Manager, session)
    return await repo.save(mgr)


@pytest.fixture
async def manager_support(session: AsyncSession) -> Manager:
    """Active, available manager in the 'support' department, priority 3."""
    mgr = Manager(
        name="Мария Поддержки",
        phone="+79992220002",
        telegram_id=100002,
        is_active=True,
        is_available=True,
        priority=3,
        department="support",
    )
    repo = ManagerRepository(Manager, session)
    return await repo.save(mgr)
