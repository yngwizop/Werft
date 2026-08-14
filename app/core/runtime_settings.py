"""Encrypted application settings stored in Postgres."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.core.config import (
    BOOL_FIELDS,
    INT_FIELDS,
    SECRET_FIELDS,
    Settings,
    VAULT_FIELDS,
    get_infra,
)
from app.core.crypto import decrypt_blob, encrypt_blob, load_or_create_master_key

logger = logging.getLogger(__name__)

_CACHE_TTL = 5.0
_cache: tuple[float, Settings] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


def _coerce(field: str, value: Any) -> Any:
    if field in BOOL_FIELDS:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if field in INT_FIELDS:
        return int(value)
    if field in {"proxmox_endpoints", "vmware_endpoints"}:
        if isinstance(value, (list, dict)):
            return json.dumps(value, separators=(",", ":"))
        if value is None:
            return ""
        return str(value)
    if value is None:
        return ""
    return value


def vault_dict(settings: Settings) -> dict[str, Any]:
    return {name: getattr(settings, name) for name in VAULT_FIELDS}


def merge_overlay(overlay: dict[str, Any] | None) -> Settings:
    infra = get_infra()
    data = infra.model_dump()
    if overlay:
        for name in VAULT_FIELDS:
            if name in overlay:
                data[name] = _coerce(name, overlay[name])
    return Settings.model_validate(data)


def _load_overlay_from_db() -> dict[str, Any] | None:
    from app.db import SessionLocal
    from app.models.settings import AppSettingsRow

    db = SessionLocal()
    try:
        row = db.get(AppSettingsRow, 1)
        if row is None:
            return None
        key = load_or_create_master_key()
        raw = decrypt_blob(row.nonce, row.ciphertext, key)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load encrypted settings: %s", exc.__class__.__name__)
        return None
    finally:
        db.close()


def load_settings() -> Settings:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]
    overlay = _load_overlay_from_db()
    settings = merge_overlay(overlay)
    _cache = (now, settings)
    return settings


def save_vault(overlay: dict[str, Any]) -> Settings:
    from app.db import SessionLocal
    from app.models.settings import AppSettingsRow

    current = vault_dict(load_settings())
    for name, value in overlay.items():
        if name not in VAULT_FIELDS:
            continue
        if name in SECRET_FIELDS and (value is None or value == ""):
            continue
        current[name] = _coerce(name, value)
    payload = json.dumps(current, separators=(",", ":")).encode("utf-8")
    key = load_or_create_master_key()
    nonce, ciphertext = encrypt_blob(payload, key)
    db = SessionLocal()
    try:
        row = db.get(AppSettingsRow, 1)
        if row is None:
            row = AppSettingsRow(id=1, nonce=nonce, ciphertext=ciphertext, key_version=1)
            db.add(row)
        else:
            row.nonce = nonce
            row.ciphertext = ciphertext
        db.commit()
    finally:
        db.close()
    invalidate_cache()
    return load_settings()


def mask_settings(settings: Settings) -> dict[str, Any]:
    from app.core.endpoints import list_proxmox_endpoints, list_vmware_endpoints

    out: dict[str, Any] = {}
    for name in VAULT_FIELDS:
        value = getattr(settings, name)
        if name == "proxmox_endpoints":
            out[name] = [
                {
                    "host": item.host,
                    "name": item.name,
                    "kind": item.kind,
                    "user": item.user,
                    "token_name": item.token_name,
                    "verify_ssl": item.verify_ssl,
                    "token_value": {"configured": bool(item.token_value), "value": ""},
                }
                for item in list_proxmox_endpoints(settings)
            ]
        elif name == "vmware_endpoints":
            out[name] = [
                {
                    "host": item.host,
                    "name": item.name,
                    "kind": item.kind,
                    "user": item.user,
                    "verify_ssl": item.verify_ssl,
                    "password": {"configured": bool(item.password), "value": ""},
                }
                for item in list_vmware_endpoints(settings)
            ]
        elif name in SECRET_FIELDS:
            out[name] = {"configured": bool(value), "value": ""}
        else:
            out[name] = value
    return out
