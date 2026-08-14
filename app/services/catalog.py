"""Catalog of provisionable images: Proxmox templates/ISOs and VMware templates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings, get_settings
from app.core.endpoints import (
    connection_display_name,
    list_proxmox_endpoints,
    list_vmware_endpoints,
)
from app.provisioners.proxmox import ProxmoxProvisioner
from app.provisioners.vmware import VMwareProvisioner

logger = logging.getLogger(__name__)

OsFamily = Literal["linux", "windows", "other"]
ImageKind = Literal["template", "iso"]


@dataclass(frozen=True)
class CatalogImage:
    id: str  # stable value stored in OTOBO, e.g. proxmox:template:144 or proxmox:iso:ISO:iso/debian.iso
    label: str
    hypervisor: Literal["proxmox", "vmware"]
    kind: ImageKind
    os_family: OsFamily
    supports_cloud_init: bool
    node: str | None = None
    raw_ref: str = ""  # vmid or volid


def _guess_os_family(name: str) -> OsFamily:
    n = name.lower()
    if any(x in n for x in ("win", "windows", "virtio-win")):
        return "windows"
    if any(
        x in n
        for x in (
            "ubuntu",
            "debian",
            "rocky",
            "rhel",
            "centos",
            "alma",
            "suse",
            "alpine",
            "linux",
            "cloud",
        )
    ):
        return "linux"
    return "other"


def catalog_target_id(*, api_host: str, local_name: str, standalone: bool, multi_api: bool) -> str:
    if standalone:
        return api_host if multi_api else local_name
    return f"{api_host}/{local_name}" if multi_api else local_name


def format_scope_label(*, kind: str, connection: str) -> str:
    titles = {"cluster": "Cluster", "host": "Host", "vcenter": "vCenter", "esxi": "ESXi"}
    title = titles.get(kind, kind)
    return f"{title} {connection}"


def _append_proxmox_images(
    px: ProxmoxProvisioner,
    images: list[CatalogImage],
    seen_iso: set[str],
    *,
    label_suffix: str,
    api_host: str,
    multi_api: bool,
    standalone: bool,
) -> None:
    for node_info in px.client.nodes.get() or []:
        node = str(node_info["node"])
        node_id = catalog_target_id(
            api_host=api_host,
            local_name=node,
            standalone=standalone,
            multi_api=multi_api,
        )
        for vm in px.client.nodes(node).qemu.get() or []:
            if int(vm.get("template") or 0) != 1:
                continue
            name = str(vm.get("name") or f"vm-{vm['vmid']}")
            vmid = str(vm["vmid"])
            family = _guess_os_family(name)
            raw_ref = f"{api_host}/{vmid}" if multi_api else vmid
            images.append(
                CatalogImage(
                    id=f"proxmox:template:{raw_ref}",
                    label=f"[Template] {name} (VMID {vmid}) @ {node}{label_suffix}",
                    hypervisor="proxmox",
                    kind="template",
                    os_family=family,
                    supports_cloud_init=family == "linux",
                    node=node_id,
                    raw_ref=raw_ref,
                )
            )

        try:
            storages = px.client.nodes(node).storage.get()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not list storage on %s: %s", node, exc)
            continue
        for storage in storages:
            content = storage.get("content") or ""
            if "iso" not in content:
                continue
            storage_name = storage["storage"]
            try:
                items = px.client.nodes(node).storage(storage_name).content.get()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not list ISOs on %s/%s: %s", node, storage_name, exc)
                continue
            for item in items:
                volid = str(item.get("volid") or "")
                if not volid or item.get("content") not in (None, "iso") and not volid.endswith(".iso"):
                    if "iso/" not in volid and not volid.endswith(".iso"):
                        continue
                iso_key = f"{px.connect_host}:{volid}"
                if iso_key in seen_iso:
                    continue
                seen_iso.add(iso_key)
                short = volid.split("/")[-1]
                family = _guess_os_family(short)
                iso_ref = f"{api_host}/{volid}" if multi_api else volid
                images.append(
                    CatalogImage(
                        id=f"proxmox:iso:{iso_ref}",
                        label=f"[ISO] {short}{label_suffix}",
                        hypervisor="proxmox",
                        kind="iso",
                        os_family=family,
                        supports_cloud_init=False,
                        node=node_id,
                        raw_ref=iso_ref,
                    )
                )


def list_proxmox_images(settings: Settings | None = None) -> list[CatalogImage]:
    cfg = settings or get_settings()
    images: list[CatalogImage] = []
    seen_iso: set[str] = set()
    endpoints = list_proxmox_endpoints(cfg)
    multi_api = len(endpoints) > 1

    for endpoint in endpoints:
        addr = endpoint.host
        try:
            px = ProxmoxProvisioner(cfg, connect_host=addr)
            nodes = px._node_names()
            standalone = endpoint.kind == "host" or (endpoint.kind != "cluster" and len(nodes) == 1)
            scope = format_scope_label(
                kind="host" if standalone else "cluster",
                connection=connection_display_name(addr, endpoint.name),
            )
            _append_proxmox_images(
                px,
                images,
                seen_iso,
                label_suffix=f" · {scope}",
                api_host=addr,
                multi_api=multi_api,
                standalone=standalone,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Proxmox catalog unavailable on %s: %s", addr, exc)

    return sorted(images, key=lambda i: (i.kind != "template", i.label.lower()))


def list_vmware_images(settings: Settings | None = None) -> list[CatalogImage]:
    """ISOs from standalone ESXi (and templates when talking to vCenter)."""
    cfg = settings or get_settings()
    images: list[CatalogImage] = []
    endpoints = list_vmware_endpoints(cfg)
    multi_api = len(endpoints) > 1

    for endpoint in endpoints:
        addr = endpoint.host
        vw = None
        try:
            vw = VMwareProvisioner(cfg, connect_host=addr)
            inventory = vw.list_inventory_hosts()
            is_vcenter = vw.is_vcenter
            scope = format_scope_label(
                kind="vcenter" if is_vcenter else "esxi",
                connection=connection_display_name(addr, endpoint.name),
            )
            catalog_node = catalog_target_id(
                api_host=addr,
                local_name=inventory[0][0] if inventory else addr,
                standalone=not is_vcenter and len(inventory) <= 1,
                multi_api=multi_api,
            )
            for item in vw.list_catalog_images():
                kind: ImageKind = "iso" if item.get("kind") == "iso" else "template"
                family = _guess_os_family(item.get("label") or item.get("raw_ref") or "")
                label = item["label"]
                if f" · {scope}" not in label:
                    label = f"{label} · {scope}"
                raw_ref = item.get("raw_ref") or ""
                image_id = item["id"]
                if multi_api and raw_ref and f"{addr}/" not in image_id:
                    hv, img_kind, _old = parse_image_id(image_id)
                    raw_ref = f"{addr}/{raw_ref}"
                    image_id = f"{hv}:{img_kind}:{raw_ref}"
                images.append(
                    CatalogImage(
                        id=image_id,
                        label=label,
                        hypervisor="vmware",
                        kind=kind,
                        os_family=family,
                        supports_cloud_init=kind == "template" and family == "linux",
                        node=item.get("node") or catalog_node,
                        raw_ref=raw_ref,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("VMware catalog unavailable on %s: %s", addr, exc)
        finally:
            if vw is not None:
                vw.close()
    return images


def list_images(
    *,
    hypervisor: Literal["proxmox", "vmware"] | None = None,
    os_family: OsFamily | None = None,
    kind: ImageKind | None = None,
) -> list[CatalogImage]:
    images: list[CatalogImage] = []
    if hypervisor in (None, "proxmox"):
        images.extend(list_proxmox_images())
    if hypervisor in (None, "vmware"):
        images.extend(list_vmware_images())
    if os_family:
        images = [i for i in images if i.os_family == os_family or i.os_family == "other"]
    if kind:
        images = [i for i in images if i.kind == kind]
    return images


def parse_image_id(image_id: str) -> tuple[str, str, str]:
    """Return (hypervisor, kind, raw_ref) from catalog id."""
    parts = image_id.split(":", 2)
    if len(parts) != 3:
        # backward compatible: bare vmid / name
        return ("proxmox", "template", image_id)
    return parts[0], parts[1], parts[2]


@dataclass(frozen=True)
class CatalogHost:
    id: str  # stored in OTOBO, e.g. PVE-01 or 192.0.2.40
    label: str
    hypervisor: Literal["proxmox", "vmware"]


def list_proxmox_hosts(settings: Settings | None = None) -> list[CatalogHost]:
    cfg = settings or get_settings()
    hosts: list[CatalogHost] = []
    seen: set[str] = set()
    endpoints = list_proxmox_endpoints(cfg)
    multi_api = len(endpoints) > 1

    for endpoint in endpoints:
        addr = endpoint.host
        try:
            px = ProxmoxProvisioner(cfg, connect_host=addr)
            nodes = px.client.nodes.get() or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Proxmox host catalog failed on %s: %s", addr, exc)
            continue
        standalone = endpoint.kind == "host" or (endpoint.kind != "cluster" and len(nodes) == 1)
        kind = "host" if standalone else "cluster"
        scope = connection_display_name(addr, endpoint.name)
        for node in nodes:
            name = str(node["node"])
            host_id = catalog_target_id(
                api_host=addr,
                local_name=name,
                standalone=standalone,
                multi_api=multi_api,
            )
            label = f"{name} · {format_scope_label(kind=kind, connection=scope)}"
            if host_id in seen:
                continue
            seen.add(host_id)
            hosts.append(CatalogHost(id=host_id, label=label, hypervisor="proxmox"))
    return sorted(hosts, key=lambda h: h.id.lower())


def list_vmware_hosts(settings: Settings | None = None) -> list[CatalogHost]:
    cfg = settings or get_settings()
    hosts: list[CatalogHost] = []
    seen: set[str] = set()
    endpoints = list_vmware_endpoints(cfg)
    multi_api = len(endpoints) > 1
    for endpoint in endpoints:
        addr = endpoint.host
        vw = None
        try:
            vw = VMwareProvisioner(cfg, connect_host=addr)
            found = vw.list_inventory_hosts()
            is_vcenter = vw.is_vcenter
        except Exception as exc:  # noqa: BLE001
            logger.warning("VMware host catalog failed on %s: %s", addr, exc)
            found = [(addr, addr)]
            is_vcenter = False
        finally:
            if vw is not None:
                vw.close()
        standalone = not is_vcenter and len(found) <= 1
        kind = "vcenter" if is_vcenter else "esxi"
        scope = connection_display_name(addr, endpoint.name)
        for local_id, _label in found:
            host_id = catalog_target_id(
                api_host=addr,
                local_name=local_id,
                standalone=standalone,
                multi_api=multi_api,
            )
            if host_id in seen:
                continue
            seen.add(host_id)
            hosts.append(
                CatalogHost(
                    id=host_id,
                    label=f"{local_id} · {format_scope_label(kind=kind, connection=scope)}",
                    hypervisor="vmware",
                )
            )
    return hosts


def list_hosts(
    hypervisor: Literal["proxmox", "vmware"] | None = None,
) -> list[CatalogHost]:
    hosts: list[CatalogHost] = []
    if hypervisor in (None, "proxmox"):
        try:
            hosts.extend(list_proxmox_hosts())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Proxmox host catalog failed: %s", exc)
    if hypervisor in (None, "vmware"):
        try:
            hosts.extend(list_vmware_hosts())
        except Exception as exc:  # noqa: BLE001
            logger.warning("VMware host catalog failed: %s", exc)
    return hosts


@dataclass(frozen=True)
class CatalogDatastore:
    id: str
    label: str
    hypervisor: Literal["proxmox", "vmware"]


def list_proxmox_datastores(settings: Settings | None = None) -> list[CatalogDatastore]:
    cfg = settings or get_settings()
    seen: set[str] = set()
    stores: list[CatalogDatastore] = []
    endpoints = list_proxmox_endpoints(cfg)
    multi_api = len(endpoints) > 1
    for endpoint in endpoints:
        addr = endpoint.host
        try:
            px = ProxmoxProvisioner(cfg, connect_host=addr)
            node_list = px.client.nodes.get() or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Proxmox datastore catalog failed on %s: %s", addr, exc)
            continue
        standalone = endpoint.kind == "host" or (endpoint.kind != "cluster" and len(node_list) == 1)
        scope = format_scope_label(
            kind="host" if standalone else "cluster",
            connection=connection_display_name(addr, endpoint.name),
        )
        for node_info in node_list:
            node = node_info["node"]
            try:
                items = px.client.nodes(node).storage.get() or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not list storage on %s: %s", node, exc)
                continue
            for storage in items:
                content = str(storage.get("content") or "")
                if "images" not in content:
                    continue
                active = storage.get("active")
                if active not in (None, 1, True, "1"):
                    continue
                name = str(storage["storage"])
                store_id = catalog_target_id(
                    api_host=addr,
                    local_name=name,
                    standalone=False,
                    multi_api=multi_api,
                )
                if store_id in seen:
                    continue
                seen.add(store_id)
                kind = str(storage.get("type") or "")
                base = f"{name} ({kind})" if kind else name
                stores.append(
                    CatalogDatastore(id=store_id, label=f"{base} · {scope}", hypervisor="proxmox")
                )
    return sorted(stores, key=lambda item: item.id.lower())


def list_vmware_datastores(settings: Settings | None = None) -> list[CatalogDatastore]:
    cfg = settings or get_settings()
    stores: list[CatalogDatastore] = []
    seen: set[str] = set()
    endpoints = list_vmware_endpoints(cfg)
    multi_api = len(endpoints) > 1
    for endpoint in endpoints:
        addr = endpoint.host
        vw = None
        try:
            vw = VMwareProvisioner(cfg, connect_host=addr)
            found = vw.list_inventory_datastores()
            is_vcenter = vw.is_vcenter
        except Exception as exc:  # noqa: BLE001
            logger.warning("VMware datastore catalog failed on %s: %s", addr, exc)
            continue
        finally:
            if vw is not None:
                vw.close()
        scope = format_scope_label(
            kind="vcenter" if is_vcenter else "esxi",
            connection=connection_display_name(addr, endpoint.name),
        )
        for local_id, label in found:
            store_id = catalog_target_id(
                api_host=addr,
                local_name=local_id,
                standalone=False,
                multi_api=multi_api,
            )
            if store_id in seen:
                continue
            seen.add(store_id)
            stores.append(
                CatalogDatastore(id=store_id, label=f"{label} · {scope}", hypervisor="vmware")
            )
    return stores


def list_datastores(
    hypervisor: Literal["proxmox", "vmware"] | None = None,
) -> list[CatalogDatastore]:
    stores: list[CatalogDatastore] = []
    if hypervisor in (None, "proxmox"):
        try:
            stores.extend(list_proxmox_datastores())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Proxmox datastore catalog failed: %s", exc)
    if hypervisor in (None, "vmware"):
        try:
            stores.extend(list_vmware_datastores())
        except Exception as exc:  # noqa: BLE001
            logger.warning("VMware datastore catalog failed: %s", exc)
    return stores
