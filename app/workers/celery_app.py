from celery import Celery

from app.core.config import get_infra

infra = get_infra()

celery_app = Celery(
    "vm_provisioning",
    broker=infra.celery_broker_url,
    backend=infra.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=850,
    timezone="UTC",
    enable_utc=True,
)
