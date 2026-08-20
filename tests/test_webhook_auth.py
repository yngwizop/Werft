import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import compute_signature
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _settings(**overrides):
    base = get_settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


def test_provision_requires_api_key_when_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: _settings(webhook_api_key="test-key-please-use", webhook_allow_from="", webhook_hmac_secret="change-me"),
    )
    body = json.dumps({"ticket_id": "T-1"}).encode()
    # Missing key: must not fall back to weak HMAC.
    assert client.post(
        "/api/v1/provision-vm",
        content=body,
        headers={"Content-Type": "application/json"},
    ).status_code == 401
    # Wrong key.
    assert client.post(
        "/api/v1/provision-vm",
        content=body,
        headers={"Content-Type": "application/json", "X-Api-Key": "nope"},
    ).status_code == 401


def test_provision_hmac_rejected_when_secret_weak(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: _settings(webhook_api_key="", webhook_allow_from="", webhook_hmac_secret="change-me"),
    )
    body = b"{}"
    sig = compute_signature("change-me", body)
    resp = client.post(
        "/api/v1/provision-vm",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
    )
    assert resp.status_code == 503


def test_provision_hmac_ok_with_strong_secret(client: TestClient, monkeypatch) -> None:
    secret = "a-long-enough-hmac-secret"
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: _settings(webhook_api_key="", webhook_allow_from="", webhook_hmac_secret=secret),
    )
    from app.api.v1.routes import provision as provision_mod

    class _Fake:
        def delay(self, job_id: str) -> None:
            return None

    monkeypatch.setattr(provision_mod, "provision_vm", _Fake())
    # Auth passes; payload validation / DB may still fail — must not be 401/503.
    payload = {
        "ticket_id": "T-hmac-1",
        "hostname": "web-01",
        "hypervisor": "proxmox",
        "cpu": 2,
        "ram_mb": 2048,
        "disks": [{"size_gb": 20}],
        "subnet": "10.0.0.0/24",
        "template": "100",
        "node": "pve1",
    }
    body = json.dumps(payload).encode()
    sig = compute_signature(secret, body)
    resp = client.post(
        "/api/v1/provision-vm",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
    )
    assert resp.status_code not in {401, 403, 503}


def test_catalog_public_requires_session(client: TestClient) -> None:
    assert client.get("/api/v1/catalog/images/public").status_code == 401
    assert client.get("/api/v1/catalog/hosts/public").status_code == 401
    assert client.get("/api/v1/catalog/datastores/public").status_code == 401


def test_allowlist_blocks_source(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: _settings(
            webhook_api_key="test-key-please-use",
            webhook_allow_from="10.255.255.1",
            webhook_hmac_secret="change-me",
        ),
    )
    resp = client.post(
        "/api/v1/provision-vm",
        content=b"{}",
        headers={"Content-Type": "application/json", "X-Api-Key": "test-key-please-use"},
    )
    assert resp.status_code == 403
