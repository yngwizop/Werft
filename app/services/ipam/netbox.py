"""NetBox IPAM adapter (pynetbox)."""

from __future__ import annotations

import logging

import pynetbox

from app.core.config import Settings, get_settings
from app.services.ipam.base import IpamError, ReservedIp

logger = logging.getLogger(__name__)


class NetBoxIpam:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.netbox_url or not self.settings.netbox_token:
            raise IpamError("IPAM URL und Token müssen konfiguriert sein (NetBox)")
        self.nb = pynetbox.api(
            self.settings.netbox_url,
            token=self.settings.netbox_token,
        )
        self.nb.http_session.verify = self.settings.netbox_verify_ssl

    def reserve_ip(self, subnet_cidr: str, ticket_id: str, hostname: str) -> ReservedIp:
        prefixes = list(self.nb.ipam.prefixes.filter(prefix=subnet_cidr))
        if not prefixes:
            prefixes = [
                p
                for p in self.nb.ipam.prefixes.filter(q=subnet_cidr.split("/")[0])
                if str(p.prefix) == subnet_cidr
            ]
        if not prefixes:
            raise IpamError(f"Prefix not found in NetBox: {subnet_cidr}")

        prefix = prefixes[0]
        available = prefix.available_ips.list()
        if not available:
            raise IpamError(f"No free IPs in prefix {subnet_cidr}")

        first = available[0]
        candidate = str(first.address if hasattr(first, "address") else first)
        created = self.nb.ipam.ip_addresses.create(
            {
                "address": candidate if "/" in candidate else f"{candidate}/{subnet_cidr.split('/')[1]}",
                "status": "reserved",
                "description": f"Reserved for OTOBO ticket {ticket_id} ({hostname})",
                "dns_name": hostname,
                "custom_fields": {},
            }
        )
        address_only = str(created.address).split("/")[0]
        logger.info("Reserved NetBox IP %s (id=%s) for ticket %s", created.address, created.id, ticket_id)
        return ReservedIp(address=address_only, cidr=str(created.address), ip_id=str(created.id))

    def create_vm_if_possible(
        self,
        *,
        hostname: str,
        ticket_id: str,
        vcpus: int,
        memory_mb: int,
        disk_gb: int,
        cluster_name: str | None = None,
    ) -> str | None:
        try:
            payload: dict = {
                "name": hostname,
                "status": "planned",
                "vcpus": vcpus,
                "memory": memory_mb,
                "disk": disk_gb,
                "comments": f"otobo:{ticket_id}",
            }
            if cluster_name:
                clusters = list(self.nb.virtualization.clusters.filter(name=cluster_name))
                if clusters:
                    payload["cluster"] = clusters[0].id
                else:
                    logger.info("Cluster %s not in NetBox — skipping VM object", cluster_name)
                    return None
            else:
                logger.info("No cluster provided — skipping NetBox VM object for %s", hostname)
                return None

            vm = self.nb.virtualization.virtual_machines.create(payload)
            logger.info("Created NetBox VM %s (id=%s)", hostname, vm.id)
            return str(vm.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("NetBox VM create skipped: %s", exc)
            return None

    def finalize(self, *, ip_id: str, vm_id: str | None = None) -> None:
        ip = self.nb.ipam.ip_addresses.get(ip_id)
        if ip:
            ip.status = "active"
            ip.save()
        if vm_id:
            vm = self.nb.virtualization.virtual_machines.get(vm_id)
            if vm:
                vm.status = "active"
                vm.save()

    def release_ip(self, ip_id: str | None) -> None:
        if not ip_id:
            return
        try:
            ip = self.nb.ipam.ip_addresses.get(ip_id)
            if ip:
                ip.delete()
                logger.info("Released NetBox IP id=%s", ip_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to release NetBox IP id=%s: %s", ip_id, exc)

    def delete_vm(self, vm_id: str | None) -> None:
        if not vm_id:
            return
        try:
            vm = self.nb.virtualization.virtual_machines.get(vm_id)
            if vm:
                vm.delete()
                logger.info("Deleted NetBox VM id=%s", vm_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to delete NetBox VM id=%s: %s", vm_id, exc)

    def compensate(self, *, ip_id: str | None = None, vm_id: str | None = None) -> None:
        self.release_ip(ip_id)
        self.delete_vm(vm_id)
