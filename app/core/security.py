import hashlib
import hmac
import ipaddress
import logging
import secrets

from fastapi import Header, HTTPException, Request, status

from app.core.auth import client_ip
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WEAK_HMAC_SECRETS = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "secret",
        "password",
        "webhook",
    }
)


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _header_ci(request: Request, name: str) -> str | None:
    for key, value in request.headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _token_equal(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    try:
        return secrets.compare_digest(presented, expected)
    except (TypeError, ValueError):
        return False


def hmac_secret_usable(secret: str) -> bool:
    text = (secret or "").strip()
    if len(text) < 16:
        return False
    return text.lower() not in _WEAK_HMAC_SECRETS


def ip_allowed(ip: str, allow_from: str) -> bool:
    text = (allow_from or "").strip()
    if not text:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            if "/" in item:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            elif addr == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


async def verify_webhook_signature(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Auth for OTOBO webhooks: X-Api-Key preferred; HMAC only if no key is configured."""
    settings = get_settings()
    source = client_ip(request)
    if not ip_allowed(source, settings.webhook_allow_from):
        logger.warning("Rejected webhook: source IP %s not allowed", source)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    presented = x_api_key or _header_ci(request, "x-api-key") or _header_ci(request, "api-key")
    configured_key = (settings.webhook_api_key or "").strip()

    # Key configured → key is mandatory (no HMAC fallback).
    if configured_key:
        if not presented:
            logger.warning("Rejected webhook: API key required")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key required",
            )
        if not _token_equal(presented, configured_key):
            logger.warning("Rejected webhook: invalid API key")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return

    # No API key: HMAC with a non-default secret.
    secret = (settings.webhook_hmac_secret or "").strip()
    if not hmac_secret_usable(secret):
        logger.warning("Rejected webhook: neither API key nor usable HMAC secret configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook authentication not configured",
        )

    body = await request.body()
    expected = compute_signature(secret, body)
    if x_webhook_signature and _token_equal(x_webhook_signature, expected):
        return

    logger.warning("Rejected webhook: missing/invalid auth")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid webhook signature or API key",
    )
