from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.security import verify_webhook_signature
from app.services.catalog import list_datastores, list_hosts, list_images

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


@router.get("/images")
def catalog_images(
    hypervisor: Literal["proxmox", "vmware"] | None = None,
    os_family: Literal["linux", "windows", "other"] | None = None,
    kind: Literal["template", "iso"] | None = None,
    _: None = Depends(verify_webhook_signature),
) -> dict:
    """List provisionable templates/ISOs for OTOBO dropdown sync / operators."""
    images = list_images(hypervisor=hypervisor, os_family=os_family, kind=kind)
    return {
        "count": len(images),
        "images": [
            {
                "id": i.id,
                "label": i.label,
                "hypervisor": i.hypervisor,
                "kind": i.kind,
                "os_family": i.os_family,
                "supports_cloud_init": i.supports_cloud_init,
                "node": i.node,
                "raw_ref": i.raw_ref,
            }
            for i in images
        ],
    }


@router.get("/images/public")
def catalog_images_public(
    hypervisor: Literal["proxmox", "vmware"] | None = Query(default=None),
    os_family: Literal["linux", "windows", "other"] | None = Query(default=None),
    kind: Literal["template", "iso"] | None = Query(default=None),
) -> dict:
    """Unauthenticated read for lab/demo — lock down in production."""
    images = list_images(hypervisor=hypervisor, os_family=os_family, kind=kind)
    return {
        "count": len(images),
        "images": [
            {
                "id": i.id,
                "label": i.label,
                "hypervisor": i.hypervisor,
                "kind": i.kind,
                "os_family": i.os_family,
                "supports_cloud_init": i.supports_cloud_init,
            }
            for i in images
        ],
    }


def _hosts_payload(hypervisor: Literal["proxmox", "vmware"] | None) -> dict:
    hosts = list_hosts(hypervisor=hypervisor)
    return {
        "count": len(hosts),
        "hosts": [
            {"id": h.id, "label": h.label, "hypervisor": h.hypervisor}
            for h in hosts
        ],
    }


@router.get("/hosts")
def catalog_hosts(
    hypervisor: Literal["proxmox", "vmware"] | None = None,
    _: None = Depends(verify_webhook_signature),
) -> dict:
    return _hosts_payload(hypervisor)


@router.get("/hosts/public")
def catalog_hosts_public(
    hypervisor: Literal["proxmox", "vmware"] | None = Query(default=None),
) -> dict:
    return _hosts_payload(hypervisor)


def _datastores_payload(hypervisor: Literal["proxmox", "vmware"] | None) -> dict:
    stores = list_datastores(hypervisor=hypervisor)
    return {
        "count": len(stores),
        "datastores": [
            {"id": d.id, "label": d.label, "hypervisor": d.hypervisor}
            for d in stores
        ],
    }


@router.get("/datastores")
def catalog_datastores(
    hypervisor: Literal["proxmox", "vmware"] | None = None,
    _: None = Depends(verify_webhook_signature),
) -> dict:
    return _datastores_payload(hypervisor)


@router.get("/datastores/public")
def catalog_datastores_public(
    hypervisor: Literal["proxmox", "vmware"] | None = Query(default=None),
) -> dict:
    return _datastores_payload(hypervisor)
