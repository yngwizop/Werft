"""VMware provisioner for standalone ESXi and vCenter.

Uses the vSphere SOAP API (pyvmomi). That works against:
- standalone ESXi (HostAgent) — create VM from ISO
- vCenter (VirtualCenter) — clone template, or create from ISO
"""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any

from pyVim.connect import Disconnect, SmartConnect
from pyVim.task import WaitForTask
from pyVmomi import vim

from app.core.config import Settings, get_settings
from app.core.endpoints import (
    list_vmware_endpoints,
    split_connection_ref,
    strip_connection_prefix,
    vmware_endpoint_for,
)
from app.provisioners.base import ProvisionResult
from app.schemas.otobo import ProvisionVmRequest

logger = logging.getLogger(__name__)


class VMwareError(RuntimeError):
    pass


def parse_vmware_hosts(primary: str, extra: str = "") -> list[str]:
    """Unique host list: VMWARE_HOST plus optional comma-separated VMWARE_HOSTS."""
    hosts: list[str] = []
    for raw in (primary, *extra.split(",")):
        host = raw.strip()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def normalize_datastore_path(path: str) -> str:
    """Canonical '[datastore] file/path' (space after closing bracket)."""
    path = path.strip()
    if not (path.startswith("[") and "]" in path):
        return path
    ds, rest = path.split("]", 1)
    rest = rest.lstrip(" /")
    if not rest:
        return f"{ds}]"
    return f"{ds}] {rest}"


def guess_guest_id(os_family: str | None, name: str = "") -> str:
    blob = f"{os_family or ''} {name}".lower()
    if "win" in blob:
        return "windows2019srv_64Guest"
    if "ubuntu" in blob:
        return "ubuntu64Guest"
    if "debian" in blob:
        return "debian11_64Guest"
    if any(x in blob for x in ("rhel", "rocky", "alma", "centos")):
        return "rhel9_64Guest"
    if "linux" in blob or "alpine" in blob:
        return "otherLinux64Guest"
    return "otherGuest64"


def guess_firmware(os_family: str | None, name: str = "") -> str:
    blob = f"{os_family or ''} {name}".lower()
    if "alpine" in blob:
        return "bios"
    if "win" in blob or "ubuntu" in blob or os_family in {"linux", "windows"}:
        return "efi"
    return "bios"


class VMwareProvisioner:
    def __init__(self, settings: Settings | None = None, *, connect_host: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.connect_host = connect_host or self.settings.vmware_host
        self.endpoint = vmware_endpoint_for(self.settings, self.connect_host)
        if not self.connect_host:
            raise VMwareError("VMWARE_HOST is not configured")
        user = (self.endpoint.user if self.endpoint else "") or self.settings.vmware_user
        if not user:
            raise VMwareError("VMWARE_USER is not configured")
        self._user = user
        self._password = (self.endpoint.password if self.endpoint else "") or self.settings.vmware_password
        self._verify_ssl = self.endpoint.verify_ssl if self.endpoint else self.settings.vmware_verify_ssl
        self._si: Any = None
        self._content: Any = None
        self._api_type: str = ""

    @property
    def is_vcenter(self) -> bool:
        self._ensure_connected()
        return self._api_type == "VirtualCenter"

    def _ensure_connected(self) -> None:
        if self._si is not None:
            return
        kwargs: dict[str, Any] = {
            "host": self.connect_host,
            "user": self._user,
            "pwd": self._password,
            "port": 443,
        }
        try:
            self._si = SmartConnect(
                **kwargs,
                disableSslCertValidation=not self._verify_ssl,
            )
        except TypeError:
            ctx = ssl.create_default_context()
            if not self._verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._si = SmartConnect(**kwargs, sslContext=ctx)
        self._content = self._si.RetrieveContent()
        self._api_type = str(self._content.about.apiType)
        logger.info(
            "Connected to %s (%s %s) as %s",
            self.connect_host,
            self._api_type,
            self._content.about.apiVersion,
            self._user,
        )

    def close(self) -> None:
        if self._si is not None:
            try:
                Disconnect(self._si)
            except Exception:  # noqa: BLE001
                logger.debug("vSphere disconnect ignored", exc_info=True)
            self._si = None
            self._content = None

    def provision(self, request: ProvisionVmRequest, ip_address: str) -> ProvisionResult:
        target = self._provisioner_for_node(request.node)
        try:
            return target._provision(request, ip_address)
        finally:
            target.close()
            if target is not self:
                self.close()

    def destroy(self, hypervisor_ref: str) -> None:
        try:
            self._destroy(hypervisor_ref)
        finally:
            self.close()

    def list_inventory_hosts(self) -> list[tuple[str, str]]:
        """Return (id, label) for ESXi hosts visible on this connection."""
        try:
            self._ensure_connected()
            hosts = []
            for host in self._find_all(vim.HostSystem):
                name = str(host.name)
                hosts.append((name, name))
            if not hosts:
                hosts.append((self.connect_host, self.connect_host))
            return sorted(hosts, key=lambda h: h[0].lower())
        finally:
            self.close()

    def list_inventory_datastores(self) -> list[tuple[str, str]]:
        """Return (id, label) for datastores visible on this connection."""
        try:
            self._ensure_connected()
            rows: list[tuple[str, str]] = []
            for datastore in self._find_all(vim.Datastore):
                name = str(datastore.name)
                summary = getattr(datastore, "summary", None)
                if summary is not None and getattr(summary, "accessible", True) is False:
                    continue
                rows.append((name, name))
            return sorted(rows, key=lambda item: item[0].lower())
        finally:
            self.close()

    def list_catalog_images(self) -> list[dict[str, str]]:
        """ISOs (and vCenter templates) for the OTOBO catalog."""
        try:
            self._ensure_connected()
            images: list[dict[str, str]] = []
            node = self._primary_host_name()
            images.extend(self._list_isos(node))
            if self.is_vcenter:
                images.extend(self._list_templates(node))
            return images
        finally:
            self.close()

    def _provision(self, request: ProvisionVmRequest, ip_address: str) -> ProvisionResult:
        self._ensure_connected()
        existing = self._find_vm(name=request.hostname, ticket_id=request.ticket_id)
        if existing is not None:
            logger.info(
                "Idempotent hit: VM %s already exists for ticket %s",
                existing.name,
                request.ticket_id,
            )
            return ProvisionResult(hypervisor_ref=existing.name)

        from app.services.catalog import parse_image_id

        hv, kind, raw_ref = parse_image_id(request.template)
        raw_ref = strip_connection_prefix(raw_ref, [item.host for item in list_vmware_endpoints(self.settings)])
        if hv == "proxmox":
            raise VMwareError(
                "Selected image is a Proxmox ISO/template. Choose a VMware ISO from this ESXi host."
            )

        if kind == "iso" or raw_ref.lower().endswith(".iso"):
            return self._provision_from_iso(request, ip_address, raw_ref)
        if not self.is_vcenter:
            raise VMwareError(
                "Standalone ESXi cannot clone VMs (that needs vCenter). "
                "Choose an ISO from the ESXi datastore."
            )
        return self._provision_from_template(request, ip_address, raw_ref)

    def _provision_from_iso(
        self,
        request: ProvisionVmRequest,
        ip_address: str,
        iso_ref: str,
    ) -> ProvisionResult:
        iso_path = normalize_datastore_path(iso_ref)
        datastore = self._pick_datastore(request)
        network = self._pick_network()
        pool = self._resource_pool()
        folder = self._vm_folder(request.folder)
        host = self._placement_host(request.node)
        disk_gb = request.disks[0].size_gb if request.disks else 20
        guest_id = guess_guest_id(request.os, iso_path)
        firmware = guess_firmware(request.os, iso_path)
        annotation = f"otobo:{request.ticket_id} planned_ip={ip_address}"
        if request.gateway:
            annotation += f" gw={request.gateway}"

        logger.info(
            "Creating ESXi/vSphere VM %s from ISO %s on datastore %s (host=%s, ip=%s)",
            request.hostname,
            iso_path,
            datastore.name,
            host.name if host else "",
            ip_address,
        )

        config = vim.vm.ConfigSpec()
        config.name = request.hostname
        config.guestId = guest_id
        config.numCPUs = request.cpu
        config.memoryMB = request.ram_mb
        config.firmware = firmware
        config.annotation = annotation
        config.files = vim.vm.FileInfo(vmPathName=f"[{datastore.name}]")
        config.deviceChange = [
            self._scsi_controller_spec(),
            self._disk_spec(datastore, disk_gb),
            self._sata_controller_spec(),
            self._cdrom_iso_spec(iso_path),
            self._nic_spec(network),
        ]

        task = folder.CreateVM_Task(config=config, pool=pool, host=host)
        vm = self._wait(task)
        try:
            self._wait(vm.PowerOnVM_Task())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Power-on after create failed for %s: %s", request.hostname, exc)
        return ProvisionResult(
            hypervisor_ref=vm.name,
            details={
                "vm": vm.name,
                "mode": "iso",
                "iso": iso_path,
                "host": host.name if host else self.connect_host,
                "warning": "ISO boot only — guest install is not automated",
            },
        )

    def _provision_from_template(
        self,
        request: ProvisionVmRequest,
        ip_address: str,
        template_ref: str,
    ) -> ProvisionResult:
        template = self._find_template(template_ref)
        if template is None:
            raise VMwareError(f"vSphere template not found: {template_ref}")
        datastore = self._pick_datastore(request)
        pool = self._resource_pool()
        folder = self._vm_folder(request.folder)
        host = self._placement_host(request.node)

        relocate = vim.vm.RelocateSpec()
        relocate.datastore = datastore
        relocate.pool = pool
        if host is not None:
            relocate.host = host

        spec = vim.vm.CloneSpec()
        spec.location = relocate
        spec.powerOn = True
        spec.template = False
        spec.config = vim.vm.ConfigSpec(
            numCPUs=request.cpu,
            memoryMB=request.ram_mb,
            annotation=f"otobo:{request.ticket_id} ip={ip_address}",
        )

        logger.info(
            "Cloning vSphere template %s -> %s for ticket %s (ip=%s)",
            template.name,
            request.hostname,
            request.ticket_id,
            ip_address,
        )
        vm = self._wait(template.CloneVM_Task(folder=folder, name=request.hostname, spec=spec))
        return ProvisionResult(
            hypervisor_ref=vm.name,
            details={"vm": vm.name, "mode": "template", "ip": ip_address},
        )

    def _destroy(self, hypervisor_ref: str) -> None:
        self._ensure_connected()
        name = hypervisor_ref.split("/")[-1]
        vm = self._find_vm(name=name)
        if vm is None:
            logger.warning("Destroy skipped, VM not found: %s", hypervisor_ref)
            return
        try:
            if vm.runtime.powerState != vim.VirtualMachinePowerState.poweredOff:
                self._wait(vm.PowerOffVM_Task())
                time.sleep(1)
        except Exception:  # noqa: BLE001
            logger.warning("Power-off before destroy failed for %s", hypervisor_ref)
        try:
            self._wait(vm.Destroy_Task())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Destroy failed for %s: %s", hypervisor_ref, exc)

    def _provisioner_for_node(self, node: str | None) -> VMwareProvisioner:
        if not node:
            return self
        hosts = [item.host for item in list_vmware_endpoints(self.settings)]
        api_host, local = split_connection_ref(node, hosts)
        if api_host and api_host.lower() != self.connect_host.lower():
            return VMwareProvisioner(self.settings, connect_host=api_host)
        self._ensure_connected()
        if self._node_matches_this_connection(local):
            return self
        for item in list_vmware_endpoints(self.settings):
            addr = item.host
            if addr.lower() == local.lower() or addr.lower() == node.lower():
                if addr.lower() == self.connect_host.lower():
                    return self
                return VMwareProvisioner(self.settings, connect_host=addr)
        raise VMwareError(
            f"ESXi host {node!r} is not this connection ({self.connect_host}) "
            "and not listed in configured VMware connections"
        )

    def _node_matches_this_connection(self, node: str) -> bool:
        hosts = [item.host for item in list_vmware_endpoints(self.settings)]
        _api, local = split_connection_ref(node, hosts)
        wanted = local.lower()
        if wanted == self.connect_host.lower():
            return True
        for host in self._find_all(vim.HostSystem):
            if str(host.name).lower() == wanted:
                return True
        return False

    def _primary_host_name(self) -> str:
        hosts = self._find_all(vim.HostSystem)
        if hosts:
            return str(hosts[0].name)
        return self.connect_host

    def _list_isos(self, node: str) -> list[dict[str, str]]:
        from app.services.catalog import _guess_os_family

        images: list[dict[str, str]] = []
        seen: set[str] = set()
        for datastore in self._find_all(vim.Datastore):
            try:
                spec = vim.host.DatastoreBrowser.SearchSpec()
                spec.matchPattern = ["*.iso"]
                spec.searchCaseInsensitive = True
                task = datastore.browser.SearchDatastoreSubFolders_Task(f"[{datastore.name}]", spec)
                results = self._wait(task) or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("ISO search failed on datastore %s: %s", datastore.name, exc)
                continue
            for result in results:
                folder = str(getattr(result, "folderPath", "") or "")
                for entry in getattr(result, "file", None) or []:
                    name = str(getattr(entry, "path", "") or "")
                    if not name.lower().endswith(".iso"):
                        continue
                    path = normalize_datastore_path(_join_datastore_folder(folder, name))
                    if path in seen:
                        continue
                    seen.add(path)
                    family = _guess_os_family(name)
                    images.append(
                        {
                            "id": f"vmware:iso:{path}",
                            "label": f"[ISO] {name}",
                            "kind": "iso",
                            "os_family": family,
                            "node": node,
                            "raw_ref": path,
                        }
                    )
        return images

    def _list_templates(self, node: str) -> list[dict[str, str]]:
        from app.services.catalog import _guess_os_family

        images: list[dict[str, str]] = []
        for vm in self._find_all(vim.VirtualMachine):
            try:
                is_template = bool(vm.config and vm.config.template)
            except Exception:  # noqa: BLE001
                continue
            if not is_template:
                continue
            name = str(vm.name)
            family = _guess_os_family(name)
            images.append(
                {
                    "id": f"vmware:template:{name}",
                    "label": f"[Template] {name}",
                    "kind": "template",
                    "os_family": family,
                    "node": node,
                    "raw_ref": name,
                }
            )
        return images

    def _find_all(self, vimtype: type) -> list[Any]:
        self._ensure_connected()
        view = self._content.viewManager.CreateContainerView(self._content.rootFolder, [vimtype], True)
        try:
            return list(view.view)
        finally:
            view.Destroy()

    def _find_vm(self, *, name: str | None = None, ticket_id: str | None = None) -> Any | None:
        needle = f"otobo:{ticket_id}" if ticket_id else None
        for vm in self._find_all(vim.VirtualMachine):
            if name and vm.name == name:
                return vm
            if needle:
                try:
                    annotation = str(vm.config.annotation or "") if vm.config else ""
                except Exception:  # noqa: BLE001
                    annotation = ""
                if needle in annotation:
                    return vm
        return None

    def _find_template(self, template_ref: str) -> Any | None:
        wanted = template_ref.strip()
        for vm in self._find_all(vim.VirtualMachine):
            if vm.name == wanted:
                return vm
            try:
                if vm._moId == wanted:  # noqa: SLF001
                    return vm
            except Exception:  # noqa: BLE001
                continue
        return None

    def _pick_datastore(self, request: ProvisionVmRequest) -> Any:
        named = None
        if request.disks and request.disks[0].datastore:
            named = request.disks[0].datastore
        named = named or self.settings.vmware_default_datastore
        named = strip_connection_prefix(named, [item.host for item in list_vmware_endpoints(self.settings)]) or named
        stores = self._find_all(vim.Datastore)
        if not stores:
            raise VMwareError("No datastore found on this ESXi/vCenter")
        if named:
            for ds in stores:
                if ds.name == named:
                    return ds
            raise VMwareError(f"Datastore not found: {named}")
        # Prefer a VM datastore over an ISO-only store.
        ranked = sorted(
            stores,
            key=lambda ds: (
                ds.name.lower() == "iso",
                -(getattr(ds.summary, "freeSpace", 0) or 0),
            ),
        )
        return ranked[0]

    def _pick_network(self) -> Any:
        wanted = self.settings.vmware_default_network
        networks = self._find_all(vim.Network)
        if wanted:
            for net in networks:
                if net.name == wanted:
                    return net
            raise VMwareError(f"Network/portgroup not found: {wanted}")
        if not networks:
            raise VMwareError("No network/portgroup found on this ESXi/vCenter")
        return networks[0]

    def _resource_pool(self) -> Any:
        pools = self._find_all(vim.ResourcePool)
        if not pools:
            raise VMwareError("No resource pool found")
        # Standalone ESXi: pool named "Resources". vCenter: first pool is a fallback.
        for pool in pools:
            if pool.name == "Resources":
                return pool
        return pools[0]

    def _vm_folder(self, folder_name: str | None) -> Any:
        wanted = folder_name or self.settings.vmware_default_folder
        dcs = self._find_all(vim.Datacenter)
        if not dcs:
            raise VMwareError("No datacenter found")
        root = dcs[0].vmFolder
        if not wanted:
            return root
        for folder in self._find_all(vim.Folder):
            if folder.name == wanted:
                return folder
        raise VMwareError(f"VM folder not found: {wanted}")

    def _placement_host(self, node: str | None) -> Any | None:
        hosts = self._find_all(vim.HostSystem)
        if not hosts:
            return None
        if not node:
            return hosts[0]
        wanted = strip_connection_prefix(
            node, [item.host for item in list_vmware_endpoints(self.settings)]
        ).lower() or node.lower()
        for host in hosts:
            if str(host.name).lower() == wanted:
                return host
        if wanted == self.connect_host.lower():
            return hosts[0]
        raise VMwareError(f"ESXi host not found in inventory: {node}")

    def _scsi_controller_spec(self) -> vim.vm.device.VirtualDeviceSpec:
        spec = vim.vm.device.VirtualDeviceSpec()
        spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        ctrl = vim.vm.device.ParaVirtualSCSIController()
        ctrl.key = 1000
        ctrl.busNumber = 0
        ctrl.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
        spec.device = ctrl
        return spec

    def _sata_controller_spec(self) -> vim.vm.device.VirtualDeviceSpec:
        spec = vim.vm.device.VirtualDeviceSpec()
        spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        ctrl = vim.vm.device.VirtualAHCIController()
        ctrl.key = 15000
        ctrl.busNumber = 0
        spec.device = ctrl
        return spec

    def _disk_spec(self, datastore: Any, size_gb: int) -> vim.vm.device.VirtualDeviceSpec:
        spec = vim.vm.device.VirtualDeviceSpec()
        spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
        spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        disk = vim.vm.device.VirtualDisk()
        disk.key = 2000
        disk.controllerKey = 1000
        disk.unitNumber = 0
        disk.capacityInKB = size_gb * 1024 * 1024
        backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
        backing.diskMode = "persistent"
        backing.thinProvisioned = True
        backing.datastore = datastore
        disk.backing = backing
        spec.device = disk
        return spec

    def _cdrom_iso_spec(self, iso_path: str) -> vim.vm.device.VirtualDeviceSpec:
        spec = vim.vm.device.VirtualDeviceSpec()
        spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        cdrom = vim.vm.device.VirtualCdrom()
        cdrom.key = 3000
        cdrom.controllerKey = 15000
        cdrom.unitNumber = 0
        backing = vim.vm.device.VirtualCdrom.IsoBackingInfo()
        backing.fileName = iso_path
        cdrom.backing = backing
        connect = vim.vm.device.VirtualDevice.ConnectInfo()
        connect.startConnected = True
        connect.allowGuestControl = True
        connect.connected = True
        cdrom.connectable = connect
        spec.device = cdrom
        return spec

    def _nic_spec(self, network: Any) -> vim.vm.device.VirtualDeviceSpec:
        spec = vim.vm.device.VirtualDeviceSpec()
        spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        nic = vim.vm.device.VirtualVmxnet3()
        nic.key = 4000
        backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
        backing.deviceName = network.name
        backing.network = network
        nic.backing = backing
        nic.addressType = "generated"
        connect = vim.vm.device.VirtualDevice.ConnectInfo()
        connect.startConnected = True
        connect.allowGuestControl = True
        connect.connected = True
        nic.connectable = connect
        spec.device = nic
        return spec

    def _wait(self, task: Any, timeout_s: int = 600) -> Any:
        try:
            WaitForTask(task, raiseOnError=True, maxWaitTime=timeout_s)
        except TypeError:
            WaitForTask(task)
        if getattr(task.info, "state", None) == vim.TaskInfo.State.error:
            err = task.info.error
            msg = getattr(err, "msg", None) or str(err)
            raise VMwareError(f"vSphere task failed: {msg}")
        return getattr(task.info, "result", None)


def _join_datastore_folder(folder: str, filename: str) -> str:
    folder = (folder or "").rstrip()
    if folder.endswith("]"):
        return f"{folder} {filename}"
    if folder.endswith("/"):
        return f"{folder}{filename}"
    if folder:
        return f"{folder}/{filename}"
    return filename


def vmware_connection_targets(settings: Settings | None = None) -> list[str]:
    cfg = settings or get_settings()
    hosts = [item.host for item in list_vmware_endpoints(cfg)]
    return hosts or parse_vmware_hosts(cfg.vmware_host, cfg.vmware_hosts)
