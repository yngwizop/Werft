from app.provisioners.proxmox import parse_proxmox_hosts


def test_parse_proxmox_hosts_unique() -> None:
    assert parse_proxmox_hosts("192.0.2.10", "") == ["192.0.2.10"]
    assert parse_proxmox_hosts("pve-01", "pve-02, pve-01") == ["pve-01", "pve-02"]
    assert parse_proxmox_hosts("", "pve-02") == ["pve-02"]
