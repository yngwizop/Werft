"""Backward-compatible NetBox exports — prefer app.services.ipam. """

from app.services.ipam.base import IpamError as NetBoxError
from app.services.ipam.base import ReservedIp
from app.services.ipam.netbox import NetBoxIpam as NetBoxService

__all__ = ["NetBoxError", "NetBoxService", "ReservedIp"]
