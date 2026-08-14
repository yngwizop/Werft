from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.security import verify_webhook_signature
from app.db import get_db
from app.models.job import JobStatus, ProvisioningJob
from app.schemas.otobo import JobStatusResponse, ProvisionVmAccepted, ProvisionVmRequest
from app.services.otobo import OTOBOClient, OTOBOError
from app.services.ticket_mapper import (
    TicketMappingError,
    extract_ticket_id,
    looks_like_direct_request,
    provision_request_from_ticket,
)
from app.workers.tasks import provision_vm

router = APIRouter(prefix="/api/v1", tags=["provisioning"])


def resolve_provision_request(payload: dict[str, Any]) -> ProvisionVmRequest:
    if looks_like_direct_request(payload):
        return ProvisionVmRequest.model_validate(payload)

    ticket_id = extract_ticket_id(payload)
    if not ticket_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not find TicketID in webhook payload",
        )
    try:
        ticket = OTOBOClient().get_ticket(ticket_id)
        return provision_request_from_ticket(ticket, ticket_id)
    except (OTOBOError, TicketMappingError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post(
    "/provision-vm",
    response_model=ProvisionVmAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_webhook_signature)],
)
def provision_vm_endpoint(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> ProvisionVmAccepted:
    payload = resolve_provision_request(payload)
    existing = db.query(ProvisioningJob).filter(ProvisioningJob.ticket_id == payload.ticket_id).one_or_none()
    if existing:
        if existing.status in {JobStatus.QUEUED, JobStatus.IP_RESERVED, JobStatus.PROVISIONING}:
            return ProvisionVmAccepted(
                job_id=existing.id,
                ticket_id=existing.ticket_id,
                status=existing.status,
            )
        if existing.status == JobStatus.COMPLETED:
            return ProvisionVmAccepted(
                job_id=existing.id,
                ticket_id=existing.ticket_id,
                status=existing.status,
            )
        # FAILED: allow retry by resetting and re-enqueueing.
        existing.status = JobStatus.QUEUED
        existing.error_message = None
        existing.request_payload = payload.model_dump(mode="json")
        existing.hostname = payload.hostname
        existing.hypervisor = payload.hypervisor
        existing.reserved_ip = None
        existing.netbox_ip_id = None
        existing.netbox_vm_id = None
        existing.hypervisor_ref = None
        db.commit()
        provision_vm.delay(existing.id)
        return ProvisionVmAccepted(
            job_id=existing.id,
            ticket_id=existing.ticket_id,
            status=existing.status,
        )

    job = ProvisioningJob(
        ticket_id=payload.ticket_id,
        status=JobStatus.QUEUED,
        hypervisor=payload.hypervisor,
        hostname=payload.hostname,
        request_payload=payload.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    provision_vm.delay(job.id)
    return ProvisionVmAccepted(job_id=job.id, ticket_id=job.ticket_id, status=job.status)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    job = db.get(ProvisioningJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobStatusResponse(
        job_id=job.id,
        ticket_id=job.ticket_id,
        status=job.status,
        hostname=job.hostname,
        hypervisor=job.hypervisor,
        reserved_ip=job.reserved_ip,
        hypervisor_ref=job.hypervisor_ref,
        error_message=job.error_message,
    )
