from fastapi.testclient import TestClient

from app.api.v1.routes.ops import _safe_config, ticket_zoom_url
from app.core.config import Settings
from app.main import app
from app.schemas.ops import OpsSetupRequest


def test_healthz() -> None:
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200


def test_ops_setup_requires_confirm_literal() -> None:
    try:
        OpsSetupRequest(
            confirm="nope",
            middleware_url="https://middleware.example.com",
        )
        raise AssertionError("expected validation error")
    except Exception:
        pass
    req = OpsSetupRequest(
        confirm="setup",
        middleware_url="https://middleware.example.com",
        dry_run=True,
    )
    assert req.confirm == "setup"
    assert req.dry_run is True


def test_ops_setup_rejects_wrong_confirm() -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/ops/setup",
        json={
            "confirm": "please",
            "middleware_url": "https://middleware.example.com",
        },
    )
    assert resp.status_code == 401


def test_ticket_zoom_url() -> None:
    assert (
        ticket_zoom_url("7", "http://192.0.2.20")
        == "http://192.0.2.20/otobo/index.pl?Action=AgentTicketZoom;TicketID=7"
    )
    assert ticket_zoom_url("", "http://example") is None
    assert ticket_zoom_url("1", "") is None


def test_safe_config_has_no_secrets() -> None:
    cfg = _safe_config(
        Settings(
            otobo_url="http://otobo.example",
            otobo_password="secret",
            netbox_token="nb-secret",
            proxmox_token_value="px-secret",
            vmware_password="vw-secret",
        )
    )
    blob = " ".join(cfg.values())
    assert "secret" not in blob
    assert cfg["otobo"] == "http://otobo.example"
