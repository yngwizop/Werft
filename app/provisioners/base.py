from dataclasses import dataclass
from typing import Protocol

from app.schemas.otobo import ProvisionVmRequest


@dataclass
class ProvisionResult:
    hypervisor_ref: str
    details: dict | None = None


class HypervisorProvisioner(Protocol):
    def provision(self, request: ProvisionVmRequest, ip_address: str) -> ProvisionResult:
        """Clone template, apply network/cloud-init, power on."""

    def destroy(self, hypervisor_ref: str) -> None:
        """Best-effort cleanup of a partially created VM."""
