from __future__ import annotations

import logging
import time
from typing import Any

from proxmoxer import ProxmoxAPI

from app.core.config import Settings, get_settings
from app.core.endpoints import (
    list_proxmox_endpoints,
    proxmox_endpoint_for,
    split_connection_ref,
    strip_connection_prefix,
)
from app.provisioners.base import ProvisionResult
from app.provisioners.proxmox_auth import parse_proxmox_token_id
from app.schemas.otobo import ProvisionVmRequest

logger = logging.getLogger(__name__)


class ProxmoxError(RuntimeError):
    pass


def parse_proxmox_hosts(primary: str, extra: str = "") -> list[str]:
    """Unique API list: PROXMOX_HOST plus optional comma-separated PROXMOX_HOSTS."""
    hosts: list[str] = []
    for raw in (primary, *extra.split(",")):
        host = raw.strip()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def proxmox_connection_targets(settings: Settings | None = None) -> list[str]:
    cfg = settings or get_settings()
    hosts = [item.host for item in list_proxmox_endpoints(cfg)]
    return hosts or parse_proxmox_hosts(cfg.proxmox_host, cfg.proxmox_hosts)


class ProxmoxProvisioner:
    def __init__(self, settings: Settings | None = None, *, connect_host: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.connect_host = connect_host or self.settings.proxmox_host
        if not self.connect_host:
            raise ProxmoxError("PROXMOX_HOST is not configured")
        self.client = self._connect()

    def _connect(self) -> ProxmoxAPI:
        endpoint = proxmox_endpoint_for(self.settings, self.connect_host)
        raw_token_id = (endpoint.token_name if endpoint else "") or self.settings.proxmox_token_name
        token_value = (endpoint.token_value if endpoint else "") or self.settings.proxmox_token_value
        user = (endpoint.user if endpoint else "") or self.settings.proxmox_user
        verify_ssl = endpoint.verify_ssl if endpoint else self.settings.proxmox_verify_ssl
        if not (raw_token_id and token_value):
            raise ProxmoxError("Proxmox API token credentials are not configured")

        try:
            parsed = parse_proxmox_token_id(raw_token_id, fallback_user=user)
        except ValueError as exc:
            raise ProxmoxError(str(exc)) from exc

        logger.info("Connecting to Proxmox %s as %s with token %s", self.connect_host, parsed.user, parsed.token_name)
        return ProxmoxAPI(
            self.connect_host,
            user=parsed.user,
            token_name=parsed.token_name,
            token_value=token_value,
            verify_ssl=verify_ssl,
        )

    def _node_names(self) -> list[str]:
        return [str(node["node"]) for node in (self.client.nodes.get() or [])]

    def _node_on_this_api(self, node: str) -> bool:
        wanted = node.lower()
        if wanted == self.connect_host.lower():
            return True
        return any(name.lower() == wanted for name in self._node_names())

    def _qemu_node(self, requested: str) -> str:
        _api, local = split_connection_ref(requested, proxmox_connection_targets(self.settings))
        names = self._node_names()
        for name in names:
            if name.lower() == local.lower():
                return name
        if local.lower() == self.connect_host.lower() and len(names) == 1:
            return names[0]
        if len(names) == 1:
            return names[0]
        raise ProxmoxError(
            f"Proxmox node {requested!r} not found on {self.connect_host} (have {', '.join(names) or 'none'})"
        )

    def _provisioner_for_node(self, node: str) -> ProxmoxProvisioner:
        api_host, local = split_connection_ref(node, proxmox_connection_targets(self.settings))
        if api_host and api_host.lower() != self.connect_host.lower():
            return ProxmoxProvisioner(self.settings, connect_host=api_host)
        if self._node_on_this_api(local):
            return self
        for addr in proxmox_connection_targets(self.settings):
            if addr.lower() == self.connect_host.lower():
                continue
            other = ProxmoxProvisioner(self.settings, connect_host=addr)
            if other._node_on_this_api(local):
                return other
        raise ProxmoxError(
            f"Proxmox node {node!r} is not reachable via configured API connections"
        )

    def provision(self, request: ProvisionVmRequest, ip_address: str) -> ProvisionResult:
        requested = request.node or self.settings.proxmox_default_node
        if not requested:
            raise ProxmoxError("Proxmox node is required (request.node or PROXMOX_DEFAULT_NODE)")

        target = self._provisioner_for_node(requested)
        if target is not self:
            return target.provision(request, ip_address)

        node = self._qemu_node(requested)

        existing = self._find_by_ticket(node, request.ticket_id)
        if existing is not None:
            logger.info("Idempotent hit: VM %s already exists for ticket %s", existing, request.ticket_id)
            return ProvisionResult(hypervisor_ref=f"{node}/{existing}")

        from app.services.catalog import parse_image_id

        _hv, kind, raw_ref = parse_image_id(request.template)
        raw_ref = strip_connection_prefix(raw_ref, proxmox_connection_targets(self.settings))
        if kind == "iso":
            return self._provision_from_iso(request, ip_address, node, raw_ref)
        return self._provision_from_template(request, ip_address, node, raw_ref)

    def _provision_from_template(
        self,
        request: ProvisionVmRequest,
        ip_address: str,
        node: str,
        template_ref: str,
    ) -> ProvisionResult:
        template_vmid = self._resolve_template_vmid(node, template_ref)
        new_vmid = self._next_vmid()
        storage = (request.disks[0].datastore if request.disks else None) or self.settings.proxmox_default_storage
        storage = strip_connection_prefix(storage, proxmox_connection_targets(self.settings)) or storage

        logger.info(
            "Cloning Proxmox template %s -> VMID %s on node %s for ticket %s",
            template_vmid,
            new_vmid,
            node,
            request.ticket_id,
        )

        upid = (
            self.client.nodes(node)
            .qemu(template_vmid)
            .clone.post(
                newid=new_vmid,
                name=request.hostname,
                full=1,
                storage=storage,
                description=f"otobo:{request.ticket_id}",
            )
        )
        self._wait_task(node, upid)
        self._apply_runtime_config(node, new_vmid, request, ip_address, cloud_init=True)
        start_upid = self.client.nodes(node).qemu(new_vmid).status.start.post()
        self._wait_task(node, start_upid)
        return ProvisionResult(
            hypervisor_ref=f"{node}/{new_vmid}",
            details={"vmid": new_vmid, "node": node, "mode": "template"},
        )

    def _provision_from_iso(
        self,
        request: ProvisionVmRequest,
        ip_address: str,
        node: str,
        iso_volid: str,
    ) -> ProvisionResult:
        """Create empty VM and attach ISO.

        Note: guest OS install is not fully automated here. Prefer cloud-init templates
        for Linux. ISO mode is for lab / when only install media exists.
        """
        new_vmid = self._next_vmid()
        storage = (request.disks[0].datastore if request.disks else None) or self.settings.proxmox_default_storage
        storage = strip_connection_prefix(storage, proxmox_connection_targets(self.settings)) or storage
        disk_gb = request.disks[0].size_gb if request.disks else 20

        logger.info(
            "Creating Proxmox VM %s from ISO %s on %s for ticket %s",
            new_vmid,
            iso_volid,
            node,
            request.ticket_id,
        )
        create = self.client.nodes(node).qemu.post(
            vmid=new_vmid,
            name=request.hostname,
            cores=request.cpu,
            memory=request.ram_mb,
            scsihw="virtio-scsi-pci",
            scsi0=f"{storage}:{disk_gb}",
            ide2=f"{iso_volid},media=cdrom",
            boot="order=ide2;scsi0",
            net0="virtio,bridge=vmbr0",
            description=f"otobo:{request.ticket_id}",
            tags=self._build_tags(request),
        )
        if isinstance(create, str) and create.startswith("UPID:"):
            self._wait_task(node, create)

        # Network annotation only — no cloud-init on blank disk+ISO.
        notes = f"otobo:{request.ticket_id} planned_ip={ip_address}"
        if request.gateway:
            notes += f" gw={request.gateway}"
        self.client.nodes(node).qemu(new_vmid).config.put(description=notes)
        start_upid = self.client.nodes(node).qemu(new_vmid).status.start.post()
        self._wait_task(node, start_upid)
        return ProvisionResult(
            hypervisor_ref=f"{node}/{new_vmid}",
            details={
                "vmid": new_vmid,
                "node": node,
                "mode": "iso",
                "warning": "ISO boot only — guest install not automated; prefer templates+cloud-init",
            },
        )

    def _apply_runtime_config(
        self,
        node: str,
        vmid: int,
        request: ProvisionVmRequest,
        ip_address: str,
        *,
        cloud_init: bool,
    ) -> None:
        config: dict[str, Any] = {
            "cores": request.cpu,
            "memory": request.ram_mb,
            "name": request.hostname,
            "description": f"otobo:{request.ticket_id}",
            "tags": self._build_tags(request),
        }
        if cloud_init:
            prefix_len = self._guess_prefix_len(request.subnet)
            ipconfig = f"ip={ip_address}/{prefix_len}"
            if request.gateway:
                ipconfig += f",gw={request.gateway}"
            config["ipconfig0"] = ipconfig
            if request.dns_servers:
                config["nameserver"] = " ".join(str(dns) for dns in request.dns_servers)

        if request.disks and request.disks[0].size_gb:
            try:
                self.client.nodes(node).qemu(vmid).resize.put(
                    disk="scsi0",
                    size=f"{request.disks[0].size_gb}G",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Disk resize skipped/failed for VMID %s: %s", vmid, exc)

        self.client.nodes(node).qemu(vmid).config.put(**config)

    def destroy(self, hypervisor_ref: str) -> None:
        try:
            node, vmid_s = hypervisor_ref.split("/", 1)
            vmid = int(vmid_s)
        except ValueError as exc:
            raise ProxmoxError(f"Invalid hypervisor_ref: {hypervisor_ref}") from exc

        try:
            self.client.nodes(node).qemu(vmid).status.stop.post(timeout=30)
        except Exception:  # noqa: BLE001
            logger.warning("Stop before destroy failed for %s", hypervisor_ref)

        try:
            self.client.nodes(node).qemu(vmid).delete(purge=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Destroy failed for %s: %s", hypervisor_ref, exc)

    def _build_tags(self, request: ProvisionVmRequest) -> str:
        tags = {f"otobo-{request.ticket_id}", *request.tags}
        # Proxmox tags: semicolon-separated, restricted charset — sanitize lightly.
        cleaned = [t.replace(":", "-").replace(" ", "-")[:64] for t in tags if t]
        return ";".join(cleaned)

    def _find_by_ticket(self, node: str, ticket_id: str) -> int | None:
        needle = f"otobo:{ticket_id}"
        tag_needle = f"otobo-{ticket_id}"
        try:
            vms = self.client.nodes(node).qemu.get()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not list VMs on %s: %s", node, exc)
            return None
        for vm in vms:
            desc = (vm.get("description") or "") if isinstance(vm, dict) else ""
            tags = (vm.get("tags") or "") if isinstance(vm, dict) else ""
            if needle in desc or tag_needle in tags:
                return int(vm["vmid"])
        return None

    def _resolve_template_vmid(self, node: str, template: str) -> int:
        if template.isdigit():
            return int(template)
        vms = self.client.nodes(node).qemu.get()
        for vm in vms:
            if vm.get("name") == template and int(vm.get("template", 0)) == 1:
                return int(vm["vmid"])
        raise ProxmoxError(f"Template not found on node {node}: {template}")

    def _next_vmid(self) -> int:
        # Cluster-wide next ID when available.
        try:
            return int(self.client.cluster.nextid.get())
        except Exception:  # noqa: BLE001
            used = {int(vm["vmid"]) for vm in self.client.cluster.resources.get(type="vm")}
            candidate = 100
            while candidate in used:
                candidate += 1
            return candidate

    def _wait_task(self, node: str, upid: str, timeout_s: int = 600) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status = self.client.nodes(node).tasks(upid).status.get()
            if status.get("status") == "stopped":
                if status.get("exitstatus") == "OK":
                    return
                raise ProxmoxError(f"Proxmox task failed: {upid} -> {status.get('exitstatus')}")
            time.sleep(2)
        raise ProxmoxError(f"Proxmox task timed out: {upid}")

    @staticmethod
    def _guess_prefix_len(subnet: str) -> int:
        if "/" in subnet:
            try:
                return int(subnet.split("/", 1)[1])
            except ValueError:
                return 24
        return 24
