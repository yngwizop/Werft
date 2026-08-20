from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.ipam.base import IpamClient, IpamError, ReservedIp
from app.services.ipam.nautobot import NautobotIpam
from app.services.ipam.netbox import NetBoxIpam

__all__ = [
    "IpamClient",
    "IpamError",
    "ReservedIp",
    "get_ipam",
]


def get_ipam(settings: Settings | None = None) -> IpamClient:
    settings = settings or get_settings()
    provider = (settings.ipam_provider or "netbox").strip().lower()
    if provider == "nautobot":
        return NautobotIpam(settings)
    if provider in {"netbox", ""}:
        return NetBoxIpam(settings)
    raise IpamError(f"Unbekannter IPAM-Provider: {provider}")
