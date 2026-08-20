from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import (
    INIT_PASSWORD,
    INIT_USERNAME,
    allow_login_attempt,
    clear_session_cookie,
    client_ip,
    cookie_secure,
    hash_password,
    password_acceptable,
    require_user,
    set_session_cookie,
    verify_password,
)
from app.db import get_db
from app.models.auth import OpsUser
from app.schemas.auth import AuthBootstrapResponse, AuthUserResponse, LoginRequest, PasswordChangeRequest

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/bootstrap", response_model=AuthBootstrapResponse)
def bootstrap_info(db: Session = Depends(get_db)) -> AuthBootstrapResponse:
    """Unauthenticated: show default-password hint only while still required."""
    user = db.query(OpsUser).filter(OpsUser.username == INIT_USERNAME).one_or_none()
    return AuthBootstrapResponse(default_credentials=bool(user and user.must_change_password))


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    ip = client_ip(request) or "unknown"
    if not allow_login_attempt(ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts")
    user = db.query(OpsUser).filter(OpsUser.username == body.username.strip()).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    payload = AuthUserResponse(
        username=user.username,
        must_change_password=user.must_change_password,
    )
    resp = JSONResponse(payload.model_dump())
    set_session_cookie(resp, user.username, user.session_version, secure=cookie_secure(request))
    return resp


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=AuthUserResponse)
def me(user: OpsUser = Depends(require_user)) -> AuthUserResponse:
    return AuthUserResponse(username=user.username, must_change_password=user.must_change_password)


@router.post("/password")
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_user),
) -> JSONResponse:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    error = password_acceptable(body.new_password)
    if error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error)
    if body.new_password == INIT_PASSWORD:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Dieses Passwort ist nicht erlaubt")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    user.session_version += 1
    db.commit()
    db.refresh(user)
    payload = AuthUserResponse(
        username=user.username,
        must_change_password=user.must_change_password,
    )
    resp = JSONResponse(payload.model_dump())
    set_session_cookie(
        resp,
        user.username,
        user.session_version,
        secure=cookie_secure(request),
    )
    return resp
