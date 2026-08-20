"""OTOBO daemon status/control via SSH (otobo.Daemon.pl)."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

DaemonAction = Literal["start", "restart", "stop"]


@dataclass
class DaemonResult:
    running: bool
    detail: str
    raw: str = ""
    ok: bool = True


def _ssh_host(settings: Settings) -> str:
    host = (settings.otobo_ssh_host or "").strip()
    if host:
        return host
    return urlparse(settings.otobo_url or "").hostname or ""


def _ssh_base(settings: Settings) -> list[str]:
    host = _ssh_host(settings)
    if not host:
        raise RuntimeError("OTOBO SSH-Host fehlt (SSH-Host oder Host aus OTOBO-URL)")
    if not (settings.otobo_ssh_key or "").strip():
        raise RuntimeError("OTOBO SSH-Key-Pfad fehlt")
    return [
        "ssh",
        "-i",
        settings.otobo_ssh_key,
        "-p",
        str(settings.otobo_ssh_port or 22),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=8",
        f"{settings.otobo_ssh_user}@{host}",
    ]


def _daemon_bin(settings: Settings) -> str:
    home = (settings.otobo_home or "/opt/otobo").rstrip("/")
    return f"{home}/bin/otobo.Daemon.pl"


def _run_remote(settings: Settings, remote: str, *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_ssh_base(settings), remote],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_running(text: str) -> bool | None:
    lowered = (text or "").lower()
    if "not running" in lowered:
        return False
    if "running" in lowered:
        return True
    return None


def daemon_status(settings: Settings | None = None) -> DaemonResult:
    settings = settings or get_settings()
    if not (settings.otobo_url or settings.otobo_ssh_host):
        return DaemonResult(running=False, detail="OTOBO nicht konfiguriert", ok=True)
    os_user = settings.otobo_os_user or "otobo"
    try:
        proc = _run_remote(settings, f"sudo -u {os_user} {_daemon_bin(settings)} status", timeout=12.0)
    except subprocess.TimeoutExpired:
        return DaemonResult(running=False, detail="SSH timeout", ok=False)
    except Exception as exc:  # noqa: BLE001
        return DaemonResult(running=False, detail=str(exc)[:140], ok=False)

    raw = ((proc.stdout or "") + (proc.stderr or "")).strip()
    parsed = _parse_running(raw)
    if parsed is True:
        return DaemonResult(running=True, detail="running", raw=raw, ok=True)
    if parsed is False:
        return DaemonResult(running=False, detail="not running", raw=raw, ok=True)
    detail = raw.splitlines()[-1] if raw else f"exit {proc.returncode}"
    return DaemonResult(running=False, detail=detail[:140], raw=raw, ok=False)


def daemon_control(action: DaemonAction, settings: Settings | None = None) -> DaemonResult:
    settings = settings or get_settings()
    if action not in {"start", "restart", "stop"}:
        return DaemonResult(running=False, detail=f"Unbekannte Aktion: {action}", ok=False)
    os_user = settings.otobo_os_user or "otobo"
    try:
        proc = _run_remote(
            settings,
            f"sudo -u {os_user} {_daemon_bin(settings)} {action}",
            timeout=30.0,
        )
    except subprocess.TimeoutExpired:
        return DaemonResult(running=False, detail="SSH timeout", ok=False)
    except Exception as exc:  # noqa: BLE001
        return DaemonResult(running=False, detail=str(exc)[:140], ok=False)

    raw = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0 and action != "stop":
        logger.warning("otobo daemon %s failed: %s", action, raw)
        # still re-check status; start may print warnings but succeed
    return daemon_status(settings)
