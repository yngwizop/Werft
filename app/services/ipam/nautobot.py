"""Nautobot IPAM adapter (REST via httpx)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.services.ipam.base import IpamError, ReservedIp

logger = logging.getLogger(__name__)


class NautobotIpam:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.nautobot_url or not self.settings.nautobot_token:
            raise IpamError("IPAM URL und Token müssen konfiguriert sein (Nautobot)")
        self.base = self.settings.nautobot_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base,
            headers={
                "Authorization": f"Token {self.settings.nautobot_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            verify=self.settings.nautobot_verify_ssl,
            timeout=30.0,
        )
        self._namespace_id: str | None = None

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = self._client.get(path, params=params)
        if resp.status_code >= 400:
            raise IpamError(f"Nautobot GET {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def _post(self, path: str, payload: dict) -> Any:
        resp = self._client.post(path, json=payload)
        if resp.status_code >= 400:
            raise IpamError(f"Nautobot POST {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def _patch(self, path: str, payload: dict) -> Any:
        resp = self._client.patch(path, json=payload)
        if resp.status_code >= 400:
            raise IpamError(f"Nautobot PATCH {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def _delete(self, path: str) -> None:
        resp = self._client.delete(path)
        if resp.status_code >= 400 and resp.status_code != 404:
            raise IpamError(f"Nautobot DELETE {path}: HTTP {resp.status_code} {resp.text[:200]}")

    def _results(self, payload: Any) -> list:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return list(payload.get("results") or [])
        return []

    def _namespace(self) -> str | None:
        if self._namespace_id is not None:
            return self._namespace_id or None
        try:
            data = self._get("/api/ipam/namespaces/", params={"name": "Global"})
            rows = self._results(data)
            if rows:
                self._namespace_id = str(rows[0].get("id") or "")
            else:
                data = self._get("/api/ipam/namespaces/")
                rows = self._results(data)
                self._namespace_id = str(rows[0].get("id") or "") if rows else ""
        except IpamError:
            self._namespace_id = ""
        return self._namespace_id or None

    def _find_prefix(self, subnet_cidr: str) -> dict:
        data = self._get("/api/ipam/prefixes/", params={"prefix": subnet_cidr})
        rows = self._results(data)
        if not rows:
            data = self._get("/api/ipam/prefixes/", params={"q": subnet_cidr.split("/")[0]})
            rows = [r for r in self._results(data) if str(r.get("prefix")) == subnet_cidr]
        if not rows:
            raise IpamError(f"Prefix not found in Nautobot: {subnet_cidr}")
        return rows[0]

    def reserve_ip(self, subnet_cidr: str, ticket_id: str, hostname: str) -> ReservedIp:
        prefix = self._find_prefix(subnet_cidr)
        prefix_id = prefix.get("id")
        if not prefix_id:
            raise IpamError(f"Prefix without id: {subnet_cidr}")

        # Prefer allocate-via-available-ips (creates the address).
        payload: dict[str, Any] = {
            "status": "Reserved",
            "description": f"Reserved for OTOBO ticket {ticket_id} ({hostname})",
            "dns_name": hostname,
        }
        ns = self._namespace()
        if ns:
            payload["namespace"] = ns

        created: dict | None = None
        try:
            raw = self._post(f"/api/ipam/prefixes/{prefix_id}/available-ips/", payload)
            if isinstance(raw, list) and raw:
                created = raw[0]
            elif isinstance(raw, dict):
                created = raw
        except IpamError as exc:
            logger.info("Nautobot available-ips allocate failed (%s) — fallback create", exc)

        if not created:
            avail = self._get(f"/api/ipam/prefixes/{prefix_id}/available-ips/")
            candidates = avail if isinstance(avail, list) else self._results(avail)
            if not candidates:
                raise IpamError(f"No free IPs in prefix {subnet_cidr}")
            first = candidates[0]
            candidate = str(first.get("address") if isinstance(first, dict) else first)
            create_body = {
                "address": candidate if "/" in candidate else f"{candidate}/{subnet_cidr.split('/')[1]}",
                "status": "Reserved",
                "description": f"Reserved for OTOBO ticket {ticket_id} ({hostname})",
                "dns_name": hostname,
            }
            if ns:
                create_body["namespace"] = ns
            created = self._post("/api/ipam/ip-addresses/", create_body)

        assert created is not None
        cidr = str(created.get("address") or "")
        address_only = cidr.split("/")[0]
        ip_id = str(created.get("id") or "")
        if not ip_id or not address_only:
            raise IpamError("Nautobot IP create returned incomplete payload")
        logger.info("Reserved Nautobot IP %s (id=%s) for ticket %s", cidr, ip_id, ticket_id)
        return ReservedIp(address=address_only, cidr=cidr, ip_id=ip_id)

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
            payload: dict[str, Any] = {
                "name": hostname,
                "status": "Planned",
                "vcpus": vcpus,
                "memory": memory_mb,
                "disk": disk_gb,
                "comments": f"otobo:{ticket_id}",
            }
            if cluster_name:
                data = self._get("/api/virtualization/clusters/", params={"name": cluster_name})
                clusters = self._results(data)
                if not clusters:
                    logger.info("Cluster %s not in Nautobot — skipping VM object", cluster_name)
                    return None
                payload["cluster"] = clusters[0]["id"]
            else:
                logger.info("No cluster provided — skipping Nautobot VM object for %s", hostname)
                return None
            vm = self._post("/api/virtualization/virtual-machines/", payload)
            vm_id = str(vm.get("id") or "")
            logger.info("Created Nautobot VM %s (id=%s)", hostname, vm_id)
            return vm_id or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Nautobot VM create skipped: %s", exc)
            return None

    def finalize(self, *, ip_id: str, vm_id: str | None = None) -> None:
        self._patch(f"/api/ipam/ip-addresses/{ip_id}/", {"status": "Active"})
        if vm_id:
            try:
                self._patch(f"/api/virtualization/virtual-machines/{vm_id}/", {"status": "Active"})
            except IpamError as exc:
                logger.warning("Nautobot VM finalize skipped: %s", exc)

    def release_ip(self, ip_id: str | None) -> None:
        if not ip_id:
            return
        try:
            self._delete(f"/api/ipam/ip-addresses/{ip_id}/")
            logger.info("Released Nautobot IP id=%s", ip_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to release Nautobot IP id=%s: %s", ip_id, exc)

    def delete_vm(self, vm_id: str | None) -> None:
        if not vm_id:
            return
        try:
            self._delete(f"/api/virtualization/virtual-machines/{vm_id}/")
            logger.info("Deleted Nautobot VM id=%s", vm_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to delete Nautobot VM id=%s: %s", vm_id, exc)

    def compensate(self, *, ip_id: str | None = None, vm_id: str | None = None) -> None:
        self.release_ip(ip_id)
        self.delete_vm(vm_id)
