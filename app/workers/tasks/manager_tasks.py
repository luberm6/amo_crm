from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.services.manager_availability import reconcile_manager_availability_job
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.manager_tasks.reconcile_manager_availability")
def reconcile_manager_availability() -> dict:
    """
    Durable periodic job: restores managers whose cooldown expired.
    """
    restored = asyncio.run(reconcile_manager_availability_job(AsyncSessionLocal))
    if restored:
        log.info("manager_tasks.reconciled", restored=restored)
    return {"restored": int(restored)}

