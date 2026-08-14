"""Shared helpers for Proxmox API token IDs as shown in the Proxmox UI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProxmoxTokenId:
    """Proxmox token id format: user@realm!tokenname"""

    user: str
    token_name: str


def parse_proxmox_token_id(token_id: str, fallback_user: str | None = None) -> ProxmoxTokenId:
    """Accept either ``user@realm!tokenname`` (as copied from Proxmox) or a bare token name.

    If a bare token name is given, ``fallback_user`` (e.g. PROXMOX_USER) is required.
    """
    value = (token_id or "").strip()
    if not value:
        raise ValueError("Proxmox token id/name is empty")

    if "!" in value:
        user, token_name = value.split("!", 1)
        user = user.strip()
        token_name = token_name.strip()
        if not user or not token_name:
            raise ValueError(
                "Invalid Proxmox token id. Expected 'user@realm!tokenname' "
                f"(got {token_id!r})"
            )
        return ProxmoxTokenId(user=user, token_name=token_name)

    if not fallback_user:
        raise ValueError(
            "Bare token name requires PROXMOX_USER, or set "
            "PROXMOX_TOKEN_NAME=user@realm!tokenname"
        )
    return ProxmoxTokenId(user=fallback_user.strip(), token_name=value)
