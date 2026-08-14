"""First-boot: master key, admin user, optional .env import into the vault."""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.auth import INIT_PASSWORD, INIT_USERNAME, hash_password
from app.core.config import BOOL_FIELDS, INT_FIELDS, VAULT_FIELDS
from app.core.crypto import load_or_create_master_key
from app.core.runtime_settings import save_vault
from app.db import SessionLocal
from app.models.auth import OpsUser
from app.models.settings import AppSettingsRow

logger = logging.getLogger(__name__)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def ensure_admin() -> None:
    db = SessionLocal()
    try:
        if db.query(OpsUser).count() > 0:
            return
        user = OpsUser(
            username=INIT_USERNAME,
            password_hash=hash_password(INIT_PASSWORD),
            must_change_password=True,
            session_version=1,
        )
        db.add(user)
        db.commit()
        logger.info("Created initial ops user %s (password must be changed)", INIT_USERNAME)
    finally:
        db.close()


def import_env_if_needed() -> None:
    from app.core.config import get_infra

    db = SessionLocal()
    try:
        if db.get(AppSettingsRow, 1) is not None:
            return
    finally:
        db.close()

    path = Path(get_infra().werft_env_import)
    overlay: dict[str, object] = {}
    if path.is_file():
        parsed = _parse_env_file(path)
        for field in VAULT_FIELDS:
            env_key = field.upper()
            if env_key not in parsed:
                continue
            raw = parsed[env_key]
            if field in BOOL_FIELDS:
                overlay[field] = raw
            elif field in INT_FIELDS:
                try:
                    overlay[field] = int(raw)
                except ValueError:
                    continue
            else:
                overlay[field] = raw
        logger.info("Imported %s setting(s) from %s into encrypted vault", len(overlay), path)
    if overlay:
        save_vault(overlay)


def bootstrap() -> None:
    load_or_create_master_key()
    ensure_admin()
    import_env_if_needed()
