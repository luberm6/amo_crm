"""
Postgres integration tests for transfer SELECT FOR UPDATE behaviour.

These tests require a real PostgreSQL database because SQLite silently ignores
FOR UPDATE clauses, meaning the row-level lock logic is never exercised in the
standard test suite.

Skip conditions:
  - TEST_DATABASE_URL not set, OR
  - TEST_DATABASE_URL does not contain "postgresql" or "asyncpg"

Run with:
  TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test_db \\
    python3 -m pytest tests/test_transfer_postgres.py -v -m postgres

3 tests:
  1. SELECT FOR UPDATE blocks second concurrent transfer → InvalidCallStateError
  2. Concurrent status writes do not corrupt data
  3. FOR UPDATE prevents double-transfer on simultaneous requests
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.call import Call, CallMode, CallStatus
from app.models.manager import Manager
from app.repositories.call_repo import CallRepository
from app.repositories.manager_repo import ManagerRepository

# ── Skip logic ────────────────────────────────────────────────────────────────

_PG_URL = os.getenv("TEST_DATABASE_URL", "")
_POSTGRES_AVAILABLE = "postgresql" in _PG_URL or "asyncpg" in _PG_URL

pytestmark = pytest.mark.postgres


# ── Session-scoped Postgres engine ────────────────────────────────────────────

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def pg_engine():
    if not _POSTGRES_AVAILABLE:
        pytest.skip(
            "Postgres tests require TEST_DATABASE_URL=postgresql+asyncpg://... "
            "(current value: {!r})".format(_PG_URL or "<not set>")
        )
    engine = create_async_engine(_PG_URL, echo=False, pool_size=5)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine) -> AsyncSession:
    """Per-test session with rollback for isolation."""
    factory = async_sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.fixture
async def pg_factory(pg_engine) -> async_sessionmaker:
    """Session factory for tests that need multiple concurrent sessions."""
    return async_sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)


# ── Test helpers ──────────────────────────────────────────────────────────────

async def _create_call(session: AsyncSession, status: CallStatus = CallStatus.IN_PROGRESS) -> Call:
    call = Call(phone="+79991234567", mode=CallMode.AUTO, status=status)
    session.add(call)
    await session.flush()
    return call


# ── Test 1: SELECT FOR UPDATE blocks concurrent transfer ─────────────────────

@pytest.mark.anyio
async def test_select_for_update_blocks_concurrent_transfer(pg_factory):
    """
    Two concurrent initiate_transfer calls on the same call_id:
    the second must see TRANSFERRING status and raise InvalidCallStateError.

    This test uses two separate DB sessions (as would happen with two HTTP
    requests hitting the same endpoint simultaneously) to simulate the real
    concurrent scenario that FOR UPDATE protects against.
    """
    from app.core.exceptions import InvalidCallStateError
    from app.services.transfer_service import TransferService
    from app.integrations.transfer_engine.stub import StubTransferEngine

    # Create the call in a separate session so both test sessions can find it
    async with pg_factory() as setup_session:
        call = await _create_call(setup_session, CallStatus.IN_PROGRESS)
        call_id = call.id
        await setup_session.commit()

    # Add a manager
    async with pg_factory() as setup_session:
        mgr = Manager(name="Тестовый менеджер", phone="+70001112233", telegram_id=12345)
        setup_session.add(mgr)
        await setup_session.commit()
        mgr_id = mgr.id

    barrier = asyncio.Barrier(2)
    results: list = []

    async def attempt_transfer(session_factory):
        async with session_factory() as session:
            from app.repositories.transfer_repo import TransferRepository
            from app.models.transfer import TransferRecord
            svc = TransferService(
                session=session,
                engine=StubTransferEngine(),
            )
            try:
                await barrier.wait()  # Both tasks start simultaneously
                result = await svc.initiate_transfer(call_id, [mgr_id])
                results.append(("ok", result))
            except InvalidCallStateError as exc:
                results.append(("blocked", str(exc)))
            except Exception as exc:
                results.append(("error", str(exc)))

    await asyncio.gather(
        attempt_transfer(pg_factory),
        attempt_transfer(pg_factory),
    )

    # Exactly one should succeed, one should be blocked
    statuses = [r[0] for r in results]
    assert statuses.count("ok") == 1, f"Expected 1 success, got: {results}"
    assert statuses.count("blocked") == 1, f"Expected 1 blocked, got: {results}"


# ── Test 2: Concurrent status updates do not corrupt data ─────────────────────

@pytest.mark.anyio
async def test_concurrent_status_update_no_corruption(pg_factory):
    """
    Two concurrent writes to call.status in separate transactions:
    last-writer-wins is expected. Verify that after both complete, the call
    has a valid status (no partial write / corrupt row).
    """
    async with pg_factory() as setup_session:
        call = await _create_call(setup_session, CallStatus.IN_PROGRESS)
        call_id = call.id
        await setup_session.commit()

    async def set_status(target_status: CallStatus) -> None:
        async with pg_factory() as session:
            async with session.begin():
                repo = CallRepository(Call, session)
                call = await repo.get(call_id)
                call.status = target_status

    await asyncio.gather(
        set_status(CallStatus.TRANSFERRING),
        set_status(CallStatus.STOPPED),
    )

    # Verify no corruption — status is one of the two valid written values
    async with pg_factory() as session:
        repo = CallRepository(Call, session)
        final_call = await repo.get(call_id)
        assert final_call.status in (CallStatus.TRANSFERRING, CallStatus.STOPPED), (
            f"Unexpected status after concurrent writes: {final_call.status}"
        )


# ── Test 3: FOR UPDATE prevents double transfer ───────────────────────────────

@pytest.mark.anyio
async def test_for_update_prevents_double_transfer(pg_factory):
    """
    Simulate real race: two tasks call get_for_update simultaneously.
    The FOR UPDATE lock ensures only one proceeds — the second sees the
    updated TRANSFERRING status and must not create a second transfer.
    """
    from app.core.exceptions import InvalidCallStateError
    from app.services.transfer_service import TransferService
    from app.integrations.transfer_engine.stub import StubTransferEngine

    async with pg_factory() as setup_session:
        call = await _create_call(setup_session, CallStatus.IN_PROGRESS)
        call_id = call.id
        mgr = Manager(name="Менеджер", phone="+70009998877", telegram_id=99999)
        setup_session.add(mgr)
        await setup_session.commit()
        mgr_id = mgr.id

    success_count = 0
    blocked_count = 0

    async def try_transfer():
        nonlocal success_count, blocked_count
        async with pg_factory() as session:
            from app.repositories.transfer_repo import TransferRepository
            from app.models.transfer import TransferRecord
            svc = TransferService(
                session=session,
                engine=StubTransferEngine(),
            )
            try:
                await svc.initiate_transfer(call_id, [mgr_id])
                success_count += 1
            except InvalidCallStateError:
                blocked_count += 1

    await asyncio.gather(try_transfer(), try_transfer())

    assert success_count == 1, f"Expected exactly 1 successful transfer, got {success_count}"
    assert blocked_count == 1, f"Expected exactly 1 blocked transfer, got {blocked_count}"
