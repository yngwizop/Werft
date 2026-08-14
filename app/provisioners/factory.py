from app.core.config import Settings, get_settings
from app.provisioners.base import HypervisorProvisioner
from app.provisioners.proxmox import ProxmoxProvisioner
from app.provisioners.vmware import VMwareProvisioner


def get_provisioner(hypervisor: str, settings: Settings | None = None) -> HypervisorProvisioner:
    cfg = settings or get_settings()
    if hypervisor == "proxmox":
        return ProxmoxProvisioner(cfg)
    if hypervisor == "vmware":
        return VMwareProvisioner(cfg)
    raise ValueError(f"Unsupported hypervisor: {hypervisor}")
