import hashlib
import hmac
import ipaddress
import logging
import secrets

from fastapi import Header, HTTPException, Request, status

from app.core.auth import client_ip
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _header_ci(request: Request, name: str) -> str | None:
    for key, value in request.headers.items():
        if key.lower() == name.lower():
            return value
    return None


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
    """Accept either HMAC body signature or a static X-Api-Key (OTOBO-friendly)."""
    settings = get_settings()
    source = client_ip(request)
    if not ip_allowed(source, settings.webhook_allow_from):
        logger.warning("Rejected webhook: source IP %s not allowed", source)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    presented = x_api_key or _header_ci(request, "x-api-key") or _header_ci(request, "api-key")

    if settings.webhook_api_key and presented:
        if secrets.compare_digest(presented, settings.webhook_api_key):
            return
        logger.warning("Rejected webhook: invalid API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    body = await request.body()
    expected = compute_signature(settings.webhook_hmac_secret, body)

    if x_webhook_signature and hmac.compare_digest(x_webhook_signature, expected):
        return

    logger.warning("Rejected webhook: missing/invalid auth")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid webhook signature or API key",
    )
