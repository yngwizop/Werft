from app.core.security import compute_signature
from app.provisioners.proxmox_auth import parse_proxmox_token_id
from app.schemas.otobo import ProvisionVmRequest


def test_proxmox_token_id_from_ui() -> None:
    parsed = parse_proxmox_token_id("netbox@pve!netbox-sync", fallback_user="root@pam")
    assert parsed.user == "netbox@pve"
    assert parsed.token_name == "netbox-sync"


def test_proxmox_token_bare_name() -> None:
    parsed = parse_proxmox_token_id("middleware", fallback_user="root@pam")
    assert parsed.user == "root@pam"
    assert parsed.token_name == "middleware"


def test_hmac_signature_stable() -> None:
    body = b'{"ticket_id":"42"}'
    a = compute_signature("secret", body)
    b = compute_signature("secret", body)
    assert a == b
    assert a.startswith("sha256=")
    assert a != compute_signature("other", body)


def test_provision_payload_validates() -> None:
    req = ProvisionVmRequest(
        ticket_id="T-100",
        hostname="web-01",
        hypervisor="proxmox",
        cpu=2,
        ram_mb=4096,
        disks=[{"size_gb": 40, "datastore": "local-lvm"}],
        subnet="10.20.30.0/24",
        gateway="10.20.30.1",
        template="100",
        node="pve1",
    )
    assert req.hostname == "web-01"
    assert req.disks[0].size_gb == 40
