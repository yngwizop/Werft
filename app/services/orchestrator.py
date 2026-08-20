from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.job import JobStatus, ProvisioningJob
from app.provisioners.factory import get_provisioner
from app.schemas.otobo import ProvisionVmRequest
from app.services.ipam import IpamClient, get_ipam
from app.services.otobo import OTOBOClient

logger = logging.getLogger(__name__)


class Orchestrator:
    """Saga: reserve IP → provision VM → finalize → OTOBO feedback; compensate on failure."""

    def __init__(
        self,
        db: Session,
        ipam: IpamClient | None = None,
        otobo: OTOBOClient | None = None,
        *,
        netbox: IpamClient | None = None,
    ) -> None:
        self.db = db
        # `netbox=` kept for older call sites / tests.
        self.ipam = ipam or netbox or get_ipam()
        self.otobo = otobo or OTOBOClient()

    def run(self, job_id: str) -> None:
        job = self.db.get(ProvisioningJob, job_id)
        if job is None:
            raise RuntimeError(f"Job not found: {job_id}")

        if job.status == JobStatus.COMPLETED:
            logger.info("Job %s already completed — skipping", job_id)
            return

        request = ProvisionVmRequest.model_validate(job.request_payload)
        reserved_ip_id = job.netbox_ip_id
        reserved_vm_id = job.netbox_vm_id
        hypervisor_ref = job.hypervisor_ref

        try:
            self.otobo.notify_provisioning(request.ticket_id, job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTOBO provisioning notify failed (continuing): %s", exc)

        try:
            if not job.reserved_ip:
                reserved = self.ipam.reserve_ip(request.subnet, request.ticket_id, request.hostname)
                job.reserved_ip = reserved.address
                job.netbox_ip_id = reserved.ip_id
                reserved_ip_id = reserved.ip_id
                job.status = JobStatus.IP_RESERVED
                self.db.commit()

                disk_gb = request.disks[0].size_gb if request.disks else 0
                vm_id = self.ipam.create_vm_if_possible(
                    hostname=request.hostname,
                    ticket_id=request.ticket_id,
                    vcpus=request.cpu,
                    memory_mb=request.ram_mb,
                    disk_gb=disk_gb,
                    cluster_name=request.node,
                )
                if vm_id:
                    job.netbox_vm_id = vm_id
                    reserved_vm_id = vm_id
                    self.db.commit()

            assert job.reserved_ip

            job.status = JobStatus.PROVISIONING
            self.db.commit()

            provisioner = get_provisioner(request.hypervisor)
            result = provisioner.provision(request, job.reserved_ip)
            hypervisor_ref = result.hypervisor_ref
            job.hypervisor_ref = hypervisor_ref
            self.db.commit()

            if job.netbox_ip_id:
                self.ipam.finalize(ip_id=job.netbox_ip_id, vm_id=job.netbox_vm_id)
            job.status = JobStatus.COMPLETED
            job.error_message = None
            self.db.commit()

            try:
                self.otobo.notify_success(
                    request.ticket_id,
                    hostname=request.hostname,
                    ip=job.reserved_ip,
                    hypervisor_ref=hypervisor_ref or "",
                    node=request.node or "",
                    hypervisor=request.hypervisor,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("OTOBO success notify failed: %s", exc)

        except Exception as exc:
            logger.exception("Provisioning saga failed for job %s", job_id)
            try:
                self.ipam.compensate(ip_id=reserved_ip_id, vm_id=reserved_vm_id)
            except Exception as comp_exc:  # noqa: BLE001
                logger.error("Compensation failed: %s", comp_exc)

            if hypervisor_ref:
                try:
                    get_provisioner(request.hypervisor).destroy(hypervisor_ref)
                except Exception as destroy_exc:  # noqa: BLE001
                    logger.error("Hypervisor destroy failed: %s", destroy_exc)

            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.reserved_ip = None
            job.netbox_ip_id = None
            job.netbox_vm_id = None
            self.db.commit()

            try:
                self.otobo.notify_failure(request.ticket_id, str(exc))
            except Exception as notify_exc:  # noqa: BLE001
                logger.error("OTOBO failure notify failed: %s", notify_exc)
            raise
