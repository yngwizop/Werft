from typing import Literal

from pydantic import BaseModel, Field


class OpsComponent(BaseModel):
    ok: bool
    detail: str = ""
    status: Literal["ok", "error", "skip"] = "ok"


class OpsJobRow(BaseModel):
    job_id: str
    ticket_id: str
    status: str
    hostname: str
    hypervisor: str
    reserved_ip: str | None = None
    hypervisor_ref: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ticket_url: str | None = None


class OpsHostRow(BaseModel):
    id: str
    label: str
    hypervisor: str


class OpsStatusResponse(BaseModel):
    app: str
    api: OpsComponent
    postgres: OpsComponent
    redis: OpsComponent
    worker: OpsComponent
    otobo: OpsComponent
    netbox: OpsComponent
    proxmox: OpsComponent
    vmware: OpsComponent
    catalog: OpsComponent
    jobs: dict[str, int]
    recent: list[OpsJobRow]
    hosts: list[OpsHostRow]
    config: dict[str, str]


class OpsSetupRequest(BaseModel):
    confirm: Literal["setup"]
    dry_run: bool = True
    middleware_url: str = Field(min_length=8, max_length=256)
    webservice_name: str = Field(default="VM-Provisioning", min_length=1, max_length=64)
    skip_catalog_sync: bool = False
    skip_process: bool = False
