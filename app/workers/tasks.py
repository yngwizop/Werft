import logging

from app.db import SessionLocal
from app.services.orchestrator import Orchestrator
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="provision_vm",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(ConnectionError, TimeoutError),
)
def provision_vm(self, job_id: str) -> dict:
    logger.info("Starting provisioning job %s (attempt %s)", job_id, self.request.retries + 1)
    db = SessionLocal()
    try:
        Orchestrator(db).run(job_id)
        return {"job_id": job_id, "status": "completed"}
    finally:
        db.close()
