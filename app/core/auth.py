"""Ops-UI authentication: Argon2 passwords and HMAC session cookies."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.crypto import load_or_create_master_key
from app.db import get_db
from app.models.auth import OpsUser

logger = logging.getLogger(__name__)

INIT_USERNAME = "admin"
INIT_PASSWORD = "changeme"
COOKIE_NAME = "werft_session"
SESSION_TTL_SECONDS = 8 * 3600
LOGIN_RATE = 5
LOGIN_WINDOW = 60.0
FORBIDDEN_NEW_PASSWORDS = frozenset({INIT_PASSWORD, INIT_USERNAME, "admin", "password"})

_hasher = PasswordHasher()
_login_hits: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def password_acceptable(password: str) -> str | None:
    if len(password) < 10:
        return "Mindestens 10 Zeichen"
    lowered = password.strip().lower()
    if lowered in FORBIDDEN_NEW_PASSWORDS or lowered == INIT_PASSWORD:
        return "Dieses Passwort ist nicht erlaubt"
    return None


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def allow_login_attempt(ip: str) -> bool:
    now = time.monotonic()
    hits = [stamp for stamp in _login_hits.get(ip, []) if now - stamp < LOGIN_WINDOW]
    if len(hits) >= LOGIN_RATE:
        _login_hits[ip] = hits
        return False
    hits.append(now)
    _login_hits[ip] = hits
    return True


def _sign(raw: bytes) -> bytes:
    return hmac.new(load_or_create_master_key(), raw, hashlib.sha256).digest()


def encode_session(username: str, session_version: int, now: int | None = None) -> str:
    payload = {
        "sub": username,
        "sv": session_version,
        "exp": int(now if now is not None else time.time()) + SESSION_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    token = raw + b"." + _sign(raw)
    return urlsafe_b64encode(token).decode("ascii")


def decode_session(token: str) -> dict | None:
    try:
        blob = urlsafe_b64decode(token.encode("ascii"))
        raw, sig = blob.rsplit(b".", 1)
        if not hmac.compare_digest(sig, _sign(raw)):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("exp") or 0) < time.time():
            return None
        return payload
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


def cookie_secure(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    return proto == "https"


def set_session_cookie(
    response: Response,
    username: str,
    session_version: int,
    *,
    secure: bool = True,
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        encode_session(username, session_version),
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def user_from_request(request: Request, db: Session) -> OpsUser | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = decode_session(token)
    if not payload:
        return None
    username = str(payload.get("sub") or "")
    session_version = int(payload.get("sv") or 0)
    user = db.query(OpsUser).filter(OpsUser.username == username).one_or_none()
    if user is None or user.session_version != session_version:
        return None
    return user


def require_user(
    request: Request,
    db: Session = Depends(get_db),
) -> OpsUser:
    user = user_from_request(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_ready_user(
    user: Annotated[OpsUser, Depends(require_user)],
) -> OpsUser:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password change required",
        )
    return user
