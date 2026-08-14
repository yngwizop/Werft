import json

from app.core.config import Settings
from app.core.endpoints import (
    list_proxmox_endpoints,
    list_vmware_endpoints,
    merge_proxmox_incoming,
    merge_vmware_incoming,
    proxmox_endpoint_for,
    vmware_endpoint_for,
)
from app.core.runtime_settings import mask_settings, merge_overlay


def test_legacy_proxmox_hosts_become_endpoints() -> None:
    settings = merge_overlay(
        {
            "proxmox_host": "192.0.2.10",
            "proxmox_hosts": "192.0.2.11",
            "proxmox_user": "root@pam",
            "proxmox_token_name": "root@pam!werft",
            "proxmox_token_value": "shared-token",
        }
    )
    hosts = [item.host for item in list_proxmox_endpoints(settings)]
    assert hosts == ["192.0.2.10", "192.0.2.11"]
    second = proxmox_endpoint_for(settings, "192.0.2.11")
    assert second is not None
    assert second.token_value == "shared-token"


def test_per_host_proxmox_tokens() -> None:
    settings = merge_overlay(
        {
            "proxmox_endpoints": json.dumps(
                [
                    {
                        "host": "192.0.2.10",
                        "user": "root@pam",
                        "token_name": "root@pam!a",
                        "token_value": "token-a",
                        "verify_ssl": False,
                    },
                    {
                        "host": "192.0.2.12",
                        "user": "root@pam",
                        "token_name": "root@pam!b",
                        "token_value": "token-b",
                        "verify_ssl": False,
                    },
                ]
            )
        }
    )
    a = proxmox_endpoint_for(settings, "192.0.2.10")
    b = proxmox_endpoint_for(settings, "192.0.2.12")
    assert a is not None and a.token_value == "token-a"
    assert b is not None and b.token_value == "token-b"


def test_merge_keeps_existing_token_when_blank() -> None:
    settings = merge_overlay(
        {
            "proxmox_host": "192.0.2.10",
            "proxmox_token_name": "root@pam!werft",
            "proxmox_token_value": "keep-me",
        }
    )
    overlay = merge_proxmox_incoming(
        settings,
        [{"host": "192.0.2.10", "user": "root@pam", "token_name": "root@pam!werft", "verify_ssl": False}],
    )
    assert overlay["proxmox_token_value"] == "keep-me"
    stored = json.loads(overlay["proxmox_endpoints"])
    assert stored[0]["token_value"] == "keep-me"


def test_merge_keeps_connection_name() -> None:
    settings = merge_overlay(
        {
            "proxmox_endpoints": json.dumps(
                [
                    {
                        "host": "192.0.2.10",
                        "name": "Prod-Cluster",
                        "user": "root@pam",
                        "token_name": "root@pam!werft",
                        "token_value": "keep-me",
                        "verify_ssl": False,
                    }
                ]
            )
        }
    )
    overlay = merge_proxmox_incoming(
        settings,
        [{"host": "192.0.2.10", "name": "Prod-Cluster", "user": "root@pam", "token_name": "root@pam!werft"}],
    )
    stored = json.loads(overlay["proxmox_endpoints"])
    assert stored[0]["name"] == "Prod-Cluster"
    masked = mask_settings(Settings.model_validate({**settings.model_dump(), **overlay}))
    assert masked["proxmox_endpoints"][0]["name"] == "Prod-Cluster"


def test_split_connection_ref_and_catalog_ids() -> None:
    from app.core.endpoints import split_connection_ref, strip_connection_prefix
    from app.services.catalog import catalog_target_id, format_scope_label

    hosts = ["192.0.2.10", "10.0.0.5"]
    assert split_connection_ref("192.0.2.10/pve1", hosts) == ("192.0.2.10", "pve1")
    assert split_connection_ref("pve1", hosts) == (None, "pve1")
    assert strip_connection_prefix("192.0.2.10/local-lvm", hosts) == "local-lvm"
    assert strip_connection_prefix("local:iso/debian.iso", hosts) == "local:iso/debian.iso"
    assert catalog_target_id(api_host="10.0.0.5", local_name="pve", standalone=True, multi_api=True) == "10.0.0.5"
    assert catalog_target_id(api_host="10.0.0.5", local_name="pve1", standalone=False, multi_api=True) == "10.0.0.5/pve1"
    assert catalog_target_id(api_host="10.0.0.5", local_name="pve1", standalone=False, multi_api=False) == "pve1"
    assert format_scope_label(kind="cluster", connection="Prod") == "Cluster Prod"
    assert format_scope_label(kind="host", connection="192.0.2.10") == "Host 192.0.2.10"


def test_merge_keeps_kind_and_token_after_host_change() -> None:
    settings = merge_overlay(
        {
            "proxmox_endpoints": json.dumps(
                [
                    {
                        "host": "192.0.2.10",
                        "name": "Prod",
                        "kind": "cluster",
                        "user": "root@pam",
                        "token_name": "root@pam!werft",
                        "token_value": "keep-me",
                        "verify_ssl": False,
                    }
                ]
            )
        }
    )
    overlay = merge_proxmox_incoming(
        settings,
        [
            {
                "host": "192.0.2.13",
                "previous_host": "192.0.2.10",
                "name": "Prod",
                "kind": "cluster",
                "user": "root@pam",
                "token_name": "root@pam!werft",
            }
        ],
    )
    stored = json.loads(overlay["proxmox_endpoints"])
    assert stored[0]["host"] == "192.0.2.13"
    assert stored[0]["kind"] == "cluster"
    assert stored[0]["token_value"] == "keep-me"


def test_get_settings_cache_clear_still_works() -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    assert get_settings() is not None


def test_vmware_endpoints_and_mask() -> None:
    settings = merge_overlay(
        {
            "vmware_host": "vcenter.example",
            "vmware_user": "admin@vsphere.local",
            "vmware_password": "super-secret",
        }
    )
    masked = mask_settings(settings)
    dumped = json.dumps(masked)
    assert "super-secret" not in dumped
    assert masked["vmware_endpoints"][0]["host"] == "vcenter.example"
    assert masked["vmware_endpoints"][0]["password"] == {"configured": True, "value": ""}
    other = merge_vmware_incoming(
        settings,
        [
            {"host": "vcenter.example", "user": "admin@vsphere.local", "verify_ssl": False},
            {"host": "esxi-02", "user": "root", "password": "esxi-secret", "verify_ssl": False},
        ],
    )
    rows = json.loads(other["vmware_endpoints"])
    assert rows[0]["password"] == "super-secret"
    assert rows[1]["password"] == "esxi-secret"
    assert vmware_endpoint_for(Settings.model_validate({**settings.model_dump(), **other}), "esxi-02").password == "esxi-secret"
    assert list_vmware_endpoints(settings)[0].host == "vcenter.example"
