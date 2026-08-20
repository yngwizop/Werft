import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.catalog import router as catalog_router
from app.api.v1.routes.ops import router as ops_router
from app.api.v1.routes.provision import router as provision_router
from app.api.v1.routes.settings import router as settings_router
from app.core.auth import user_from_request
from app.core.bootstrap import bootstrap
from app.core.config import get_infra
from app.db import SessionLocal, init_db
from app.schemas.otobo import HealthResponse

infra = get_infra()
logging.basicConfig(level=infra.log_level)
logger = logging.getLogger(__name__)

OPEN_EXACT = {"/healthz", "/api/v1/auth/login", "/api/v1/auth/bootstrap"}
PASSWORD_OK = {"/api/v1/auth/password", "/api/v1/auth/me", "/api/v1/auth/logout"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    bootstrap()
    logger.info("%s started", infra.app_name)
    yield


app = FastAPI(title=infra.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(provision_router)
app.include_router(catalog_router)
app.include_router(ops_router)
app.include_router(settings_router)


@app.middleware("http")
async def session_guard(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in OPEN_EXACT or path.startswith("/api/v1/provision-vm"):
        return await call_next(request)
    if path.startswith("/api/"):
        db = SessionLocal()
        try:
            user = user_from_request(request, db)
            if user is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            if user.must_change_password and path not in PASSWORD_OK:
                return JSONResponse({"detail": "password change required"}, status_code=403)
            request.state.user = user
        finally:
            db.close()
    return await call_next(request)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(status="ok", app=infra.app_name)
