"""Celery app. Background jobs (Tally import processing, outstanding recalculation, promise monitoring,
notifications) are added module by module; every task must be idempotent (dedupe keys / unique constraints)."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("tayal_erp", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="Asia/Kolkata",
    enable_utc=True,
    beat_schedule={},
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
