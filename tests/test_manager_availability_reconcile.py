from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.telephony.base import TelephonyChannel, TelephonyLegState
from app.integrations.telephony.mango import MangoTelephonyAdapter
from app.integrations.transfer_engine.mango import MangoTransferEngine
from app.models.manager import Manager
from app.repositories.manager_repo import ManagerRepository
from app.services.manager_availability import reconcile_manager_availability_job


class _FakeTelephony(MangoTelephonyAdapter):
    async def connect(self, phone: str, caller_id=None, metadata=None) -> TelephonyChannel:  # pragma: no cover - not used
        return TelephonyChannel(
            channel_id="ch",
            phone=phone,
            provider_leg_id="leg",
            state=TelephonyLegState.INITIATING,
        )


@pytest.mark.anyio
async def test_mark_unavailable_persists_available_after(
    session: AsyncSession, test_engine
) -> None:
    manager = Manager(
        name="Cooldown Durable",
        phone="+79991110009",
        telegram_id=777009,
        is_active=True,
        is_available=True,
        priority=1,
        department="sales",
    )
    await ManagerRepository(Manager, session).save(manager)
    await session.commit()

    engine = MangoTransferEngine(
        telephony=_FakeTelephony(),
        session_factory=async_sessionmaker(bind=test_engine, expire_on_commit=False),
        direct_session_manager=None,
    )

    old_cd = settings.transfer_manager_cooldown_seconds
    try:
        settings.transfer_manager_cooldown_seconds = 60
        await engine.mark_manager_temporarily_unavailable(manager.id)

        await session.refresh(manager)
        assert manager.is_available is False
        assert manager.available_after is not None
        deadline = manager.available_after
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        assert deadline > datetime.now(timezone.utc)
    finally:
        settings.transfer_manager_cooldown_seconds = old_cd
        await session.delete(manager)
        await session.commit()


@pytest.mark.anyio
async def test_reconcile_job_restores_due_manager(
    session: AsyncSession, test_engine
) -> None:
    manager = Manager(
        name="Restore Due",
        phone="+79991110010",
        telegram_id=777010,
        is_active=True,
        is_available=False,
        priority=1,
        department="sales",
        available_after=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    await ManagerRepository(Manager, session).save(manager)
    await session.commit()

    try:
        restored = await reconcile_manager_availability_job(
            async_sessionmaker(bind=test_engine, expire_on_commit=False)
        )
        assert restored >= 1

        await session.refresh(manager)
        assert manager.is_available is True
        assert manager.available_after is None
    finally:
        await session.delete(manager)
        await session.commit()


@pytest.mark.anyio
async def test_reconcile_job_keeps_not_due_manager_unavailable(
    session: AsyncSession, test_engine
) -> None:
    manager = Manager(
        name="Not Due",
        phone="+79991110011",
        telegram_id=777011,
        is_active=True,
        is_available=False,
        priority=1,
        department="sales",
        available_after=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    await ManagerRepository(Manager, session).save(manager)
    await session.commit()

    try:
        restored = await reconcile_manager_availability_job(
            async_sessionmaker(bind=test_engine, expire_on_commit=False)
        )
        assert restored == 0

        await session.refresh(manager)
        assert manager.is_available is False
        assert manager.available_after is not None
    finally:
        await session.delete(manager)
        await session.commit()
