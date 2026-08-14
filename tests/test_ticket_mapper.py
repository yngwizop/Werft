from app.services.ticket_mapper import extract_ticket_id, provision_request_from_ticket


def test_extract_ticket_id_from_event() -> None:
    assert extract_ticket_id({"TicketID": 42}) == "42"
    assert extract_ticket_id({"Data": {"TicketID": "99"}}) == "99"
    assert extract_ticket_id({"Ticket": {"TicketID": 7}}) == "7"


def test_map_dynamic_fields() -> None:
    ticket = {
        "TicketID": "15",
        "Owner": "noah",
        "DynamicField": [
            {"Name": "VMHostname", "Value": "app-web-01"},
            {"Name": "VMHypervisor", "Value": "proxmox"},
            {"Name": "VMCpu", "Value": "4"},
            {"Name": "VMRamMB", "Value": "8192"},
            {"Name": "VMDiskGB", "Value": "80"},
            {"Name": "VMSubnet", "Value": "10.20.30.0/24"},
            {"Name": "VMTemplate", "Value": "ubuntu-24.04-cloud"},
            {"Name": "VMNode", "Value": "PVE-01"},
            {"Name": "VMDatastore", "Value": "local-lvm"},
            {"Name": "VMGateway", "Value": "10.20.30.1"},
        ],
    }
    req = provision_request_from_ticket(ticket, "15")
    assert req.hostname == "app-web-01"
    assert req.hypervisor == "proxmox"
    assert req.cpu == 4
    assert req.ram_mb == 8192
    assert req.disks[0].size_gb == 80
    assert req.node == "PVE-01"
    assert req.disks[0].datastore == "local-lvm"
    assert req.gateway == "10.20.30.1"


def test_map_proxmox_node_field() -> None:
    ticket = {
        "TicketID": "16",
        "DynamicField": [
            {"Name": "VMHostname", "Value": "web-02"},
            {"Name": "VMHypervisor", "Value": "proxmox"},
            {"Name": "VMCpu", "Value": "2"},
            {"Name": "VMRamMB", "Value": "2048"},
            {"Name": "VMDiskGB", "Value": "20"},
            {"Name": "VMSubnet", "Value": "10.0.0.0/24"},
            {"Name": "VMTemplate", "Value": "100"},
            {"Name": "VMProxmoxNode", "Value": "PVE-02"},
        ],
    }
    req = provision_request_from_ticket(ticket, "16")
    assert req.node == "PVE-02"
