"""Ops dashboard API: status, jobs, OTOBO setup (SSE)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import require_ready_user
from app.core.config import Settings, get_settings
from app.core.endpoints import list_proxmox_endpoints, list_vmware_endpoints
from app.db import get_db
from app.models.job import ProvisioningJob
from app.schemas.ops import (
    OpsComponent,
    OpsDaemonRequest,
    OpsHostRow,
    OpsJobRow,
    OpsSetupRequest,
    OpsStatusResponse,
)
from app.services.otobo_daemon import daemon_control, daemon_status

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/ops",
    tags=["ops"],
    dependencies=[Depends(require_ready_user)],
)

ROOT = Path(__file__).resolve().parents[4]
SETUP_SCRIPT = ROOT / "scripts" / "install_otobo_setup.py"
_setup_lock = asyncio.Lock()
PROBE_TIMEOUT = 4.0


def ticket_zoom_url(ticket_id: str, otobo_url: str) -> str | None:
    base = (otobo_url or "").rstrip("/")
    tid = (ticket_id or "").strip()
    if not base or not tid:
        return None
    return f"{base}/otobo/index.pl?Action=AgentTicketZoom;TicketID={tid}"


def _short(exc: BaseException, limit: int = 140) -> str:
    msg = str(exc).replace("\n", " ").strip()
    return msg[:limit] if msg else exc.__class__.__name__


def _ok(detail: str) -> OpsComponent:
    return OpsComponent(ok=True, detail=detail, status="ok")


def _error(detail: str) -> OpsComponent:
    return OpsComponent(ok=False, detail=detail, status="error")


def _skip(detail: str = "nicht konfiguriert") -> OpsComponent:
    return OpsComponent(ok=True, detail=detail, status="skip")


def _job_row(job: ProvisioningJob, otobo_url: str) -> OpsJobRow:
    return OpsJobRow(
        job_id=job.id,
        ticket_id=job.ticket_id,
        status=job.status,
        hostname=job.hostname,
        hypervisor=job.hypervisor,
        reserved_ip=job.reserved_ip,
        hypervisor_ref=job.hypervisor_ref,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
        ticket_url=ticket_zoom_url(job.ticket_id, otobo_url),
    )


def _safe_config(settings: Settings) -> dict[str, str]:
    proxmox = ", ".join(item.host for item in list_proxmox_endpoints(settings)) or "—"
    vmware = ", ".join(item.host for item in list_vmware_endpoints(settings)) or "—"
    return {
        "otobo": settings.otobo_url or "—",
        "webservice": settings.otobo_webservice_name or "—",
        "ipam": (settings.ipam_provider or "netbox").strip().lower(),
        "netbox": settings.netbox_url or "—",
        "nautobot": settings.nautobot_url or "—",
        "proxmox": proxmox,
        "vmware": vmware,
        "katalog-sync": f"{settings.catalog_sync_interval_seconds}s",
    }


def _otobo_daemon() -> OpsComponent:
    result = daemon_status()
    if "nicht konfiguriert" in result.detail:
        return _skip(result.detail)
    if not result.ok:
        return _error(result.detail)
    if result.running:
        return _ok("running")
    return _error("not running")


def _postgres(db: Session) -> OpsComponent:
    try:
        db.execute(text("SELECT 1"))
        return _ok("ok")
    except Exception as exc:  # noqa: BLE001
        return _error(_short(exc))


def _redis_client():
    import redis

    settings = get_settings()
    return redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _redis() -> OpsComponent:
    try:
        _redis_client().ping()
        return _ok("ok")
    except Exception as exc:  # noqa: BLE001
        return _error(_short(exc))


def _worker() -> OpsComponent:
    try:
        client = _redis_client()
        client.ping()
        queued = int(client.llen("celery") or 0)
        if queued == 0:
            return _ok("Warteschlange leer")
        return _ok(f"{queued} wartend")
    except Exception as exc:  # noqa: BLE001
        return _error(_short(exc))


def _otobo() -> OpsComponent:
    settings = get_settings()
    if not settings.otobo_url:
        return _skip()
    try:
        with httpx.Client(
            verify=settings.otobo_verify_ssl,
            timeout=PROBE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(settings.otobo_url)
        return _ok(f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return _error(_short(exc))


def _ipam() -> OpsComponent:
    settings = get_settings()
    provider = (settings.ipam_provider or "netbox").strip().lower()
    if provider == "nautobot":
        base = settings.nautobot_url
        token = settings.nautobot_token
        verify = settings.nautobot_verify_ssl
        label = "Nautobot"
    else:
        base = settings.netbox_url
        token = settings.netbox_token
        verify = settings.netbox_verify_ssl
        label = "NetBox"
    if not base:
        return _skip()
    url = base.rstrip("/") + "/api/status/"
    headers = {}
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        with httpx.Client(verify=verify, timeout=PROBE_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            return _error(f"HTTP {resp.status_code}")
        version = ""
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                version = str(
                    payload.get("nautobot-version")
                    or payload.get("netbox-version")
                    or payload.get("version")
                    or ""
                )
        except Exception:  # noqa: BLE001
            version = ""
        detail = f"{label} {version}".strip() if version else label
        return _ok(detail)
    except Exception as exc:  # noqa: BLE001
        return _error(_short(exc))


def _looks_like_addr(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if ":" in text and text.count(":") >= 2:
        return True
    parts = text.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _chip_label(node: str, kind: str, connection_name: str, api_host: str) -> str:
    scope = (connection_name or "").strip()
    if not scope or scope == api_host or _looks_like_addr(scope):
        return f"{node} · {kind}"
    return f"{node} · {kind} {scope}"


def _proxmox() -> tuple[OpsComponent, list[OpsHostRow]]:
    settings = get_settings()
    from app.provisioners.proxmox import ProxmoxProvisioner, proxmox_connection_targets

    targets = proxmox_connection_targets(settings)
    if not targets:
        return _skip(), []
    endpoints = list_proxmox_endpoints(settings)
    missing = [item.host for item in endpoints if not (item.token_name and item.token_value)]
    if missing and len(missing) == len(endpoints):
        return _error(f"API-Token fehlt ({', '.join(missing)})"), []
    hosts: list[OpsHostRow] = []
    seen: set[str] = set()
    errors: list[str] = []
    multi_api = len(targets) > 1
    by_host = {item.host: item for item in endpoints}
    for addr in targets:
        try:
            px = ProxmoxProvisioner(settings, connect_host=addr)
            nodes = px.client.nodes.get() or []
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{addr}: {_short(exc, 80)}")
            continue
        endpoint = by_host.get(addr)
        standalone = (
            endpoint.kind == "host"
            if endpoint and endpoint.kind in {"host", "cluster"}
            else len(nodes) == 1
        )
        kind = "Host" if standalone else "Cluster"
        conn_name = endpoint.name if endpoint else ""
        for node in nodes:
            name = str(node["node"])
            host_id = addr if standalone and multi_api else name
            if multi_api and not standalone:
                host_id = f"{addr}/{name}"
            label = _chip_label(name, kind, conn_name, addr)
            if host_id in seen:
                continue
            seen.add(host_id)
            hosts.append(OpsHostRow(id=host_id, label=label, hypervisor="proxmox"))
    n = len(hosts)
    count = f"{n} node" if n == 1 else f"{n} nodes"
    if hosts:
        return _ok(count), hosts
    return _error(errors[0] if errors else "keine Nodes"), []


def _vmware() -> tuple[OpsComponent, list[OpsHostRow]]:
    settings = get_settings()
    from app.provisioners.vmware import VMwareProvisioner, vmware_connection_targets

    targets = vmware_connection_targets(settings)
    if not targets:
        return _skip(), []
    if not settings.vmware_user and not any(item.user for item in list_vmware_endpoints(settings)):
        return _error("VMware-Login fehlt"), []
    hosts: list[OpsHostRow] = []
    seen: set[str] = set()
    errors: list[str] = []
    by_host = {item.host: item for item in list_vmware_endpoints(settings)}
    for addr in targets:
        vw = None
        try:
            vw = VMwareProvisioner(settings, connect_host=addr)
            found = vw.list_inventory_hosts()
            endpoint = by_host.get(addr)
            kind = "ESXi"
            if endpoint and endpoint.kind == "vcenter":
                kind = "vCenter"
            elif vw.is_vcenter:
                kind = "vCenter"
            conn_name = endpoint.name if endpoint else ""
            for host_id, host_name in found:
                if host_id in seen:
                    continue
                seen.add(host_id)
                label = _chip_label(host_name, kind, conn_name, addr)
                hosts.append(OpsHostRow(id=host_id, label=label, hypervisor="vmware"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{addr}: {_short(exc, 80)}")
        finally:
            if vw is not None:
                vw.close()
    n = len(hosts)
    count = f"{n} host" if n == 1 else f"{n} hosts"
    if hosts and not errors:
        return _ok(count), hosts
    if hosts and errors:
        return _ok(count), hosts
    return _error(errors[0] if errors else "keine Hosts"), []


def _catalog(proxmox: OpsComponent, vmware: OpsComponent, hosts: list[OpsHostRow]) -> OpsComponent:
    if proxmox.status == "skip" and vmware.status == "skip":
        return _skip("kein Hypervisor")
    n = len(hosts)
    detail = f"{n} host" if n == 1 else f"{n} hosts"
    if n:
        return _ok(detail)
    if proxmox.status == "error" or vmware.status == "error":
        return _error(detail)
    return _ok(detail)


@router.get("/status", response_model=OpsStatusResponse)
def ops_status(db: Session = Depends(get_db)) -> OpsStatusResponse:
    settings = get_settings()
    counts = dict(
        Counter(
            status
            for (status,) in db.query(ProvisioningJob.status).all()
        )
    )
    recent = (
        db.query(ProvisioningJob)
        .order_by(ProvisioningJob.updated_at.desc())
        .limit(5)
        .all()
    )
    with ThreadPoolExecutor(max_workers=7) as pool:
        fut_otobo = pool.submit(_otobo)
        fut_daemon = pool.submit(_otobo_daemon)
        fut_netbox = pool.submit(_ipam)
        fut_proxmox = pool.submit(_proxmox)
        fut_vmware = pool.submit(_vmware)
        fut_redis = pool.submit(_redis)
        fut_worker = pool.submit(_worker)
        otobo = _await(fut_otobo, _error("timeout"))
        otobo_daemon = _await(fut_daemon, _error("timeout"))
        netbox = _await(fut_netbox, _error("timeout"))
        proxmox, px_hosts = _await_hosts(fut_proxmox)
        vmware, vw_hosts = _await_hosts(fut_vmware)
        redis = _await(fut_redis, _error("timeout"))
        worker = _await(fut_worker, _error("timeout"))
    hosts = [*px_hosts, *vw_hosts]
    return OpsStatusResponse(
        app=settings.app_name,
        api=_ok("ok"),
        postgres=_postgres(db),
        redis=redis,
        worker=worker,
        otobo=otobo,
        otobo_daemon=otobo_daemon,
        netbox=netbox,
        proxmox=proxmox,
        vmware=vmware,
        catalog=_catalog(proxmox, vmware, hosts),
        jobs=counts,
        recent=[_job_row(job, settings.otobo_url) for job in recent],
        hosts=hosts,
        config=_safe_config(settings),
    )


def _await(fut, fallback: OpsComponent) -> OpsComponent:
    try:
        return fut.result(timeout=PROBE_TIMEOUT + 1.5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ops probe failed: %s", exc)
        return fallback if isinstance(exc, FuturesTimeout) else _error(_short(exc))


def _await_hosts(fut) -> tuple[OpsComponent, list[OpsHostRow]]:
    try:
        result = fut.result(timeout=PROBE_TIMEOUT + 1.5)
        if isinstance(result, tuple) and len(result) == 2:
            return result
        return _error("unerwartete Antwort"), []
    except FuturesTimeout:
        return _error("timeout"), []
    except Exception as exc:  # noqa: BLE001
        logger.warning("ops host probe failed: %s", exc)
        return _error(_short(exc)), []


@router.get("/jobs", response_model=list[OpsJobRow])
def ops_jobs(db: Session = Depends(get_db), limit: int = 50) -> list[OpsJobRow]:
    limit = max(1, min(limit, 200))
    settings = get_settings()
    rows = (
        db.query(ProvisioningJob)
        .order_by(ProvisioningJob.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [_job_row(job, settings.otobo_url) for job in rows]


@router.post("/otobo-daemon")
def ops_otobo_daemon(body: OpsDaemonRequest) -> dict:
    result = daemon_control(body.action)
    if not result.ok and "nicht konfiguriert" in result.detail:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.detail)
    return {
        "action": body.action,
        "running": result.running,
        "detail": result.detail,
        "status": "ok" if result.running or body.action == "stop" else "error",
    }


@router.post("/setup")
async def ops_setup(body: OpsSetupRequest) -> StreamingResponse:
    if not SETUP_SCRIPT.is_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="install_otobo_setup.py is missing in this image",
        )
    if _setup_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup is already running",
        )

    cmd = [
        sys.executable,
        str(SETUP_SCRIPT),
        "--yes",
        "--write-vault",
        "--middleware-url",
        body.middleware_url.rstrip("/"),
        "--webservice-name",
        body.webservice_name,
    ]
    if body.dry_run:
        cmd.append("--dry-run")
    if body.skip_catalog_sync:
        cmd.append("--skip-catalog-sync")
    if body.skip_process:
        cmd.append("--skip-process")

    async def events():
        async with _setup_lock:
            logger.info("Ops setup start dry_run=%s url=%s", body.dry_run, body.middleware_url)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ROOT),
            )
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                yield f"data: {json.dumps({'line': line})}\n\n"
            code = await proc.wait()
            yield f"data: {json.dumps({'exit_code': code})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
