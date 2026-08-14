from ipaddress import IPv4Address
from typing import Literal

from pydantic import BaseModel, Field


class DiskSpec(BaseModel):
    size_gb: int = Field(ge=1, description="Disk size in GiB")
    datastore: str | None = Field(
        default=None,
        description="VMware datastore or Proxmox storage name",
    )


class ProvisionVmRequest(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=128)
    hostname: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
    hypervisor: Literal["proxmox", "vmware"]
    cpu: int = Field(ge=1, le=64)
    ram_mb: int = Field(ge=512)
    disks: list[DiskSpec] = Field(min_length=1)
    subnet: str = Field(description="CIDR, e.g. 10.20.30.0/24")
    vlan_id: int | None = None
    gateway: str | None = None
    dns_servers: list[IPv4Address] = Field(default_factory=list)
    template: str = Field(description="Proxmox template VMID/name or vSphere template")
    node: str | None = Field(default=None, description="Proxmox node or VMware host/cluster")
    resource_pool: str | None = None
    folder: str | None = None
    tags: list[str] = Field(default_factory=list)
    requester: str | None = None
    os: str | None = Field(default=None, description="OS label from OTOBO form")


class ProvisionVmAccepted(BaseModel):
    job_id: str
    ticket_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    ticket_id: str
    status: str
    hostname: str
    hypervisor: str
    reserved_ip: str | None = None
    hypervisor_ref: str | None = None
    error_message: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
