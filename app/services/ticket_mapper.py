"""Map OTOBO webhook / TicketGet payloads to ProvisionVmRequest."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.schemas.otobo import DiskSpec, ProvisionVmRequest


def dynamic_field_map(settings: Settings | None = None) -> dict[str, str]:
    s = settings or get_settings()
    return {
        s.otobo_df_hostname: "hostname",
        s.otobo_df_hypervisor: "hypervisor",
        s.otobo_df_cpu: "cpu",
        s.otobo_df_ram_mb: "ram_mb",
        s.otobo_df_disk_gb: "disk_gb",
        s.otobo_df_subnet: "subnet",
        s.otobo_df_vlan_id: "vlan_id",
        s.otobo_df_gateway: "gateway",
        s.otobo_df_template: "template",
        s.otobo_df_node: "node",
        s.otobo_df_os: "os",
        s.otobo_df_datastore: "datastore",
        "VMProxmoxNode": "proxmox_node",
        "VMVMwareHost": "vmware_host",
    }


class TicketMappingError(ValueError):
    pass


def extract_ticket_id(payload: dict[str, Any]) -> str | None:
    """Find TicketID in OTOBO event / GI payloads."""
    candidates = [
        payload.get("ticket_id"),
        payload.get("TicketID"),
        payload.get("TicketId"),
    ]
    ticket = payload.get("Ticket")
    if isinstance(ticket, dict):
        candidates.extend([ticket.get("TicketID"), ticket.get("TicketNumber")])
    data = payload.get("Data")
    if isinstance(data, dict):
        candidates.extend([data.get("TicketID"), data.get("ticket_id")])
        inner = data.get("Ticket")
        if isinstance(inner, dict):
            candidates.append(inner.get("TicketID"))
    for value in candidates:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _dynamic_fields_from_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    raw_list = ticket.get("DynamicField")
    if isinstance(raw_list, list):
        for item in raw_list:
            if isinstance(item, dict) and item.get("Name"):
                values[str(item["Name"])] = item.get("Value")
    for key, value in ticket.items():
        if key.startswith("DynamicField_"):
            values[key.removeprefix("DynamicField_")] = value
    return values


def provision_request_from_ticket(ticket: dict[str, Any], ticket_id: str) -> ProvisionVmRequest:
    fields = _dynamic_fields_from_ticket(ticket)
    mapped: dict[str, Any] = {}
    for df_name, attr in dynamic_field_map().items():
        if fields.get(df_name) not in (None, ""):
            mapped[attr] = fields[df_name]

    disk_gb = mapped.pop("disk_gb", None)
    datastore = mapped.pop("datastore", None)
    disks: list[DiskSpec] = []
    if disk_gb not in (None, ""):
        ds = str(datastore).strip() if datastore not in (None, "") else None
        if ds and ds.startswith("proxmox:"):
            ds = ds.split(":", 1)[1]
        elif ds and ds.startswith("vmware:"):
            ds = ds.split(":", 1)[1]
        disks = [DiskSpec(size_gb=int(disk_gb), datastore=ds or None)]

    hypervisor = str(mapped.get("hypervisor") or "").strip().lower()
    if hypervisor in {"pve", "proxmox ve"}:
        hypervisor = "proxmox"

    os_raw = str(mapped.get("os") or "").strip().lower()
    if os_raw in {"linux", "windows", "other"}:
        mapped["os"] = os_raw
    elif "win" in os_raw:
        mapped["os"] = "windows"
    elif os_raw:
        mapped["os"] = "linux"

    # Prefer catalog ids (proxmox:iso:... / proxmox:template:...). Keep bare values for labs.
    template = str(mapped.get("template") or "").strip()

    node = (
        mapped.get("proxmox_node")
        or mapped.get("vmware_host")
        or mapped.get("node")
        or None
    )
    if node:
        node = str(node).strip()
        if node.startswith("proxmox:"):
            node = node.split(":", 1)[1]
        elif node.startswith("vmware:"):
            node = node.split(":", 1)[1]

    try:
        return ProvisionVmRequest(
            ticket_id=str(ticket_id),
            hostname=str(mapped.get("hostname") or ticket.get("Title") or "").lower(),
            hypervisor=hypervisor,  # type: ignore[arg-type]
            cpu=int(mapped["cpu"]),
            ram_mb=int(mapped["ram_mb"]),
            disks=disks,
            subnet=str(mapped.get("subnet") or ""),
            vlan_id=int(mapped["vlan_id"]) if mapped.get("vlan_id") not in (None, "") else None,
            gateway=str(mapped["gateway"]) if mapped.get("gateway") else None,
            template=template,
            node=node or None,
            requester=ticket.get("CustomerUserID") or ticket.get("Owner"),
            os=str(mapped["os"]) if mapped.get("os") else None,
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise TicketMappingError(
            f"Ticket {ticket_id} is missing required VM fields: {exc}"
        ) from exc


def looks_like_direct_request(payload: dict[str, Any]) -> bool:
    return all(k in payload for k in ("hostname", "hypervisor", "cpu", "ram_mb", "disks", "subnet", "template"))
