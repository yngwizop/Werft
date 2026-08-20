from types import SimpleNamespace

import pytest

from app.services.ipam import IpamError, get_ipam
from app.services.ipam.nautobot import NautobotIpam
from app.services.ipam.netbox import NetBoxIpam


def _settings(**overrides):
    base = {
        "netbox_url": "https://ipam.example",
        "netbox_token": "secret-token",
        "netbox_verify_ssl": False,
        "ipam_provider": "netbox",
        "nautobot_url": "",
        "nautobot_token": "",
        "nautobot_verify_ssl": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_get_ipam_netbox() -> None:
    client = get_ipam(_settings(ipam_provider="netbox"))
    assert isinstance(client, NetBoxIpam)


def test_get_ipam_nautobot() -> None:
    client = get_ipam(
        _settings(
            ipam_provider="nautobot",
            nautobot_url="https://nautobot.example",
            nautobot_token="nb-token",
            nautobot_verify_ssl=False,
        )
    )
    assert isinstance(client, NautobotIpam)
    client.close()


def test_get_ipam_unknown() -> None:
    with pytest.raises(IpamError, match="Unbekannter"):
        get_ipam(_settings(ipam_provider="phpipam"))


def test_ipam_requires_credentials() -> None:
    with pytest.raises(IpamError):
        get_ipam(_settings(netbox_url="", netbox_token=""))
    with pytest.raises(IpamError):
        get_ipam(_settings(ipam_provider="nautobot", nautobot_url="", nautobot_token=""))
