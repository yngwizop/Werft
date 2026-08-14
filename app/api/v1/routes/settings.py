from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import require_ready_user
from app.core.config import SECRET_FIELDS, VAULT_FIELDS, get_settings
from app.core.runtime_settings import mask_settings, save_vault
from app.models.auth import OpsUser

router = APIRouter(
    prefix="/api/v1/ops/settings",
    tags=["settings"],
    dependencies=[Depends(require_ready_user)],
)


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def get_ops_settings(_: OpsUser = Depends(require_ready_user)) -> dict[str, Any]:
    return {"settings": mask_settings(get_settings())}


@router.put("")
def put_ops_settings(body: SettingsUpdate, _: OpsUser = Depends(require_ready_user)) -> dict[str, Any]:
    from app.core.endpoints import merge_proxmox_incoming, merge_vmware_incoming

    incoming = {key: value for key, value in body.values.items() if key in VAULT_FIELDS}
    settings = get_settings()
    if "proxmox_endpoints" in body.values:
        raw = body.values.get("proxmox_endpoints") or []
        if not isinstance(raw, list):
            raw = []
        incoming.update(merge_proxmox_incoming(settings, raw))
    if "vmware_endpoints" in body.values:
        raw = body.values.get("vmware_endpoints") or []
        if not isinstance(raw, list):
            raw = []
        incoming.update(merge_vmware_incoming(settings, raw))
    for key in SECRET_FIELDS:
        if key in incoming and incoming[key] == "":
            incoming.pop(key)
    save_vault(incoming)
    return {"settings": mask_settings(get_settings())}


@router.post("/webhook-key")
def rotate_webhook_key(_: OpsUser = Depends(require_ready_user)) -> dict[str, Any]:
    import secrets

    save_vault({"webhook_api_key": secrets.token_urlsafe(32)})
    # Return the new key once so the operator can copy it into OTOBO.
    settings = get_settings()
    masked = mask_settings(settings)
    return {"settings": masked, "webhook_api_key": settings.webhook_api_key}
