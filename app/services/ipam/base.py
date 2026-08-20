"""Shared IPAM types for NetBox and Nautobot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class IpamError(RuntimeError):
    pass


@dataclass
class ReservedIp:
    address: str  # without CIDR
    cidr: str  # with prefix, e.g. 10.0.0.5/24
    ip_id: str
    vm_id: str | None = None


class IpamClient(Protocol):
    def reserve_ip(self, subnet_cidr: str, ticket_id: str, hostname: str) -> ReservedIp: ...

    def create_vm_if_possible(
        self,
        *,
        hostname: str,
        ticket_id: str,
        vcpus: int,
        memory_mb: int,
        disk_gb: int,
        cluster_name: str | None = None,
    ) -> str | None: ...

    def finalize(self, *, ip_id: str, vm_id: str | None = None) -> None: ...

    def compensate(self, *, ip_id: str | None = None, vm_id: str | None = None) -> None: ...
