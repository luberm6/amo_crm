"""
Celery application instance.
Why Celery over RQ:
- Production-grade: battle-tested at scale, robust retry/backoff policies
- Flower dashboard for real-time task monitoring
- Flexible routing: priority queues, dedicated workers per queue type
- Better ecosystem: integrates with Prometheus, Sentry, etc.
Run worker:
    celery -A app.workers.celery_app worker --loglevel=info -Q default
Run beat scheduler (periodic tasks):
    celery -A app.workers.celery_app beat --loglevel=info
Monitor via Flower:
    celery -A app.workers.celery_app flower
"""
from celery import Celery
from app.core.config import settings
celery_app = Celery(
    "amo_crm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.manager_tasks",
    ],
)
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone — always UTC
    timezone="UTC",
    enable_utc=True,
    # Reliability
    task_acks_late=True,           # Acknowledge only after successful execution
    task_reject_on_worker_lost=True,  # Re-queue on worker crash
    worker_prefetch_multiplier=1,  # One task at a time per worker thread
    # Result expiry
    result_expires=3600,           # 1 hour
    # Beat schedule (add periodic tasks here as needed)
    beat_schedule={
        "reconcile-manager-availability": {
            "task": "app.workers.tasks.manager_tasks.reconcile_manager_availability",
            "schedule": float(max(5, int(settings.transfer_manager_restore_interval_seconds))),
        },
    },
)
