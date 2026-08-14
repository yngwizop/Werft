"""Hypervisor API connections: one entry per cluster or standalone host."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings


@dataclass
class ProxmoxEndpoint:
    host: str
    user: str = "root@pam"
    token_name: str = ""
    token_value: str = ""
    verify_ssl: bool = True
    name: str = ""
    kind: str = "cluster"


@dataclass
class VMwareEndpoint:
    host: str
    user: str = ""
    password: str = ""
    verify_ssl: bool = True
    name: str = ""
    kind: str = "vcenter"


def connection_display_name(host: str, name: str = "") -> str:
    return (name or "").strip() or host


def _proxmox_kind(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"cluster", "host"}:
        return value
    return "cluster"


def _vmware_kind(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"vcenter", "esxi"}:
        return value
    return "vcenter"


def split_connection_ref(value: str, hosts: list[str]) -> tuple[str | None, str]:
    """Split catalog ids like ``192.0.2.10/pve`` into (api host, local name)."""
    raw = (value or "").strip()
    if not raw:
        return None, ""
    lowered = raw.lower()
    for host in sorted((item for item in hosts if item), key=len, reverse=True):
        prefix = host.lower()
        if lowered == prefix:
            return host, raw
        marked = prefix + "/"
        if lowered.startswith(marked):
            return host, raw[len(host) + 1 :]
    return None, raw


def strip_connection_prefix(value: str | None, hosts: list[str]) -> str:
    if value is None:
        return ""
    _api, local = split_connection_ref(str(value), hosts)
    return local


def _parse_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _split_hosts(primary: str, extra: str = "") -> list[str]:
    hosts: list[str] = []
    for raw in (primary, *extra.split(",")):
        host = raw.strip()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def list_proxmox_endpoints(settings: Settings) -> list[ProxmoxEndpoint]:
    parsed = _parse_list(settings.proxmox_endpoints)
    if parsed:
        out: list[ProxmoxEndpoint] = []
        for item in parsed:
            host = str(item.get("host") or "").strip()
            if not host:
                continue
            out.append(
                ProxmoxEndpoint(
                    host=host,
                    user=str(item.get("user") or settings.proxmox_user or "root@pam"),
                    token_name=str(item.get("token_name") or settings.proxmox_token_name or ""),
                    token_value=str(item.get("token_value") or settings.proxmox_token_value or ""),
                    verify_ssl=bool(item.get("verify_ssl", settings.proxmox_verify_ssl)),
                    name=str(item.get("name") or "").strip(),
                    kind=_proxmox_kind(item.get("kind")),
                )
            )
        if out:
            return out
    return [
        ProxmoxEndpoint(
            host=host,
            user=settings.proxmox_user or "root@pam",
            token_name=settings.proxmox_token_name,
            token_value=settings.proxmox_token_value,
            verify_ssl=settings.proxmox_verify_ssl,
        )
        for host in _split_hosts(settings.proxmox_host, settings.proxmox_hosts)
    ]


def list_vmware_endpoints(settings: Settings) -> list[VMwareEndpoint]:
    parsed = _parse_list(settings.vmware_endpoints)
    if parsed:
        out: list[VMwareEndpoint] = []
        for item in parsed:
            host = str(item.get("host") or "").strip()
            if not host:
                continue
            out.append(
                VMwareEndpoint(
                    host=host,
                    user=str(item.get("user") or settings.vmware_user or ""),
                    password=str(item.get("password") or settings.vmware_password or ""),
                    verify_ssl=bool(item.get("verify_ssl", settings.vmware_verify_ssl)),
                    name=str(item.get("name") or "").strip(),
                    kind=_vmware_kind(item.get("kind")),
                )
            )
        if out:
            return out
    return [
        VMwareEndpoint(
            host=host,
            user=settings.vmware_user,
            password=settings.vmware_password,
            verify_ssl=settings.vmware_verify_ssl,
        )
        for host in _split_hosts(settings.vmware_host, settings.vmware_hosts)
    ]


def proxmox_endpoint_for(settings: Settings, host: str) -> ProxmoxEndpoint | None:
    wanted = (host or "").strip().lower()
    endpoints = list_proxmox_endpoints(settings)
    for item in endpoints:
        if item.host.lower() == wanted:
            return item
    return endpoints[0] if len(endpoints) == 1 and wanted else None


def vmware_endpoint_for(settings: Settings, host: str) -> VMwareEndpoint | None:
    wanted = (host or "").strip().lower()
    endpoints = list_vmware_endpoints(settings)
    for item in endpoints:
        if item.host.lower() == wanted:
            return item
    return endpoints[0] if len(endpoints) == 1 and wanted else None


def merge_proxmox_incoming(settings: Settings, incoming: list[dict[str, Any]]) -> dict[str, Any]:
    old = {item.host.lower(): item for item in list_proxmox_endpoints(settings)}
    merged: list[ProxmoxEndpoint] = []
    for item in incoming:
        host = str(item.get("host") or "").strip()
        if not host:
            continue
        prev = old.get(host.lower())
        previous_host = str(item.get("previous_host") or "").strip().lower()
        if prev is None and previous_host:
            prev = old.get(previous_host)
        token_value = str(item.get("token_value") or "").strip()
        if not token_value:
            token_value = (prev.token_value if prev else "") or settings.proxmox_token_value
        merged.append(
            ProxmoxEndpoint(
                host=host,
                user=str(item.get("user") or (prev.user if prev else "") or settings.proxmox_user or "root@pam"),
                token_name=str(
                    item.get("token_name") or (prev.token_name if prev else "") or settings.proxmox_token_name or ""
                ),
                token_value=token_value,
                verify_ssl=bool(item.get("verify_ssl", prev.verify_ssl if prev else settings.proxmox_verify_ssl)),
                name=str(item.get("name") if "name" in item else (prev.name if prev else "") or "").strip(),
                kind=_proxmox_kind(item.get("kind") if item.get("kind") else (prev.kind if prev else "")),
            )
        )
    first = merged[0] if merged else None
    return {
        "proxmox_endpoints": json.dumps(
            [
                {
                    "host": item.host,
                    "name": item.name,
                    "kind": item.kind,
                    "user": item.user,
                    "token_name": item.token_name,
                    "token_value": item.token_value,
                    "verify_ssl": item.verify_ssl,
                }
                for item in merged
            ],
            separators=(",", ":"),
        ),
        "proxmox_host": first.host if first else "",
        "proxmox_hosts": ",".join(item.host for item in merged[1:]),
        "proxmox_user": first.user if first else settings.proxmox_user,
        "proxmox_token_name": first.token_name if first else settings.proxmox_token_name,
        "proxmox_token_value": first.token_value if first else settings.proxmox_token_value,
        "proxmox_verify_ssl": first.verify_ssl if first else settings.proxmox_verify_ssl,
    }


def merge_vmware_incoming(settings: Settings, incoming: list[dict[str, Any]]) -> dict[str, Any]:
    old = {item.host.lower(): item for item in list_vmware_endpoints(settings)}
    merged: list[VMwareEndpoint] = []
    for item in incoming:
        host = str(item.get("host") or "").strip()
        if not host:
            continue
        prev = old.get(host.lower())
        previous_host = str(item.get("previous_host") or "").strip().lower()
        if prev is None and previous_host:
            prev = old.get(previous_host)
        password = str(item.get("password") or "").strip()
        if not password:
            password = (prev.password if prev else "") or settings.vmware_password
        merged.append(
            VMwareEndpoint(
                host=host,
                user=str(item.get("user") or (prev.user if prev else "") or settings.vmware_user or ""),
                password=password,
                verify_ssl=bool(item.get("verify_ssl", prev.verify_ssl if prev else settings.vmware_verify_ssl)),
                name=str(item.get("name") if "name" in item else (prev.name if prev else "") or "").strip(),
                kind=_vmware_kind(item.get("kind") if item.get("kind") else (prev.kind if prev else "")),
            )
        )
    first = merged[0] if merged else None
    return {
        "vmware_endpoints": json.dumps(
            [
                {
                    "host": item.host,
                    "name": item.name,
                    "kind": item.kind,
                    "user": item.user,
                    "password": item.password,
                    "verify_ssl": item.verify_ssl,
                }
                for item in merged
            ],
            separators=(",", ":"),
        ),
        "vmware_host": first.host if first else "",
        "vmware_hosts": ",".join(item.host for item in merged[1:]),
        "vmware_user": first.user if first else settings.vmware_user,
        "vmware_password": first.password if first else settings.vmware_password,
        "vmware_verify_ssl": first.verify_ssl if first else settings.vmware_verify_ssl,
    }
