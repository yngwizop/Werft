from app.provisioners.vmware import (
    guess_firmware,
    guess_guest_id,
    normalize_datastore_path,
    parse_vmware_hosts,
)
from app.services.catalog import parse_image_id


def test_parse_vmware_hosts_unique() -> None:
    assert parse_vmware_hosts("192.0.2.40", "") == ["192.0.2.40"]
    assert parse_vmware_hosts("esxi-01", "esxi-02, esxi-01") == ["esxi-01", "esxi-02"]


def test_normalize_datastore_path() -> None:
    assert normalize_datastore_path("[ISO]ubuntu.iso") == "[ISO] ubuntu.iso"
    assert normalize_datastore_path("[ISO] ubuntu.iso") == "[ISO] ubuntu.iso"
    assert normalize_datastore_path("[ISO] /linux/alpine.iso") == "[ISO] linux/alpine.iso"


def test_parse_vmware_iso_id() -> None:
    hv, kind, raw = parse_image_id("vmware:iso:[ISO] ubuntu-24.04.4-live-server-amd64.iso")
    assert hv == "vmware"
    assert kind == "iso"
    assert raw == "[ISO] ubuntu-24.04.4-live-server-amd64.iso"


def test_guest_and_firmware() -> None:
    assert guess_guest_id("linux", "alpine-virt.iso") == "otherLinux64Guest"
    assert guess_firmware("linux", "alpine-virt.iso") == "bios"
    assert guess_firmware("linux", "ubuntu-24.04.iso") == "efi"
    assert "windows" in guess_guest_id("windows", "win.iso").lower()
