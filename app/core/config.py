from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class InfraSettings(BaseSettings):
    """Process/infrastructure settings. Not stored in the encrypted vault."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "werft"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://middleware:middleware@localhost:5432/middleware"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    werft_master_key_path: str = "/var/lib/werft/master.key"
    werft_env_import: str = "/app/.env.import"


class Settings(InfraSettings):
    """Runtime settings. Vault fields come from Postgres, not from .env after first import."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    webhook_hmac_secret: str = "change-me"
    webhook_api_key: str = ""
    webhook_allow_from: str = ""

    netbox_url: str = ""
    netbox_token: str = ""
    netbox_verify_ssl: bool = True
    ipam_provider: str = "netbox"  # netbox | nautobot
    nautobot_url: str = ""
    nautobot_token: str = ""
    nautobot_verify_ssl: bool = True

    proxmox_host: str = ""
    proxmox_hosts: str = ""
    proxmox_user: str = "root@pam"
    proxmox_token_name: str = ""
    proxmox_token_value: str = ""
    proxmox_verify_ssl: bool = True
    proxmox_default_node: str = ""
    proxmox_default_storage: str = "local-lvm"
    proxmox_endpoints: str = ""

    vmware_host: str = ""
    vmware_user: str = ""
    vmware_password: str = ""
    vmware_verify_ssl: bool = True
    vmware_datacenter: str = ""
    vmware_default_folder: str = ""
    vmware_hosts: str = ""
    vmware_default_datastore: str = ""
    vmware_default_network: str = "VM Network"
    vmware_endpoints: str = ""

    otobo_url: str = ""
    otobo_webservice_name: str = "Werft-Sync-Api"
    otobo_user_login: str = ""
    otobo_password: str = ""
    otobo_verify_ssl: bool = True
    otobo_status_provisioning: str = "Provisioning"
    otobo_status_done: str = "Done"
    otobo_status_failed: str = "Failed"

    otobo_ssh_host: str = ""
    otobo_ssh_user: str = "root"
    otobo_ssh_port: int = 22
    otobo_ssh_key: str = "/root/.ssh/id_ed25519"
    otobo_home: str = "/opt/otobo"
    otobo_os_user: str = "otobo"
    catalog_sync_interval_seconds: int = 900

    otobo_df_hostname: str = "VMHostname"
    otobo_df_hypervisor: str = "VMHypervisor"
    otobo_df_cpu: str = "VMCpu"
    otobo_df_ram_mb: str = "VMRamMB"
    otobo_df_disk_gb: str = "VMDiskGB"
    otobo_df_subnet: str = "VMSubnet"
    otobo_df_vlan_id: str = "VMVlanID"
    otobo_df_gateway: str = "VMGateway"
    otobo_df_template: str = "VMTemplate"
    otobo_df_node: str = "VMNode"
    otobo_df_os: str = "VMOS"
    otobo_df_datastore: str = "VMDatastore"


VAULT_FIELDS: tuple[str, ...] = (
    "webhook_hmac_secret",
    "webhook_api_key",
    "webhook_allow_from",
    "netbox_url",
    "netbox_token",
    "netbox_verify_ssl",
    "ipam_provider",
    "nautobot_url",
    "nautobot_token",
    "nautobot_verify_ssl",
    "proxmox_host",
    "proxmox_hosts",
    "proxmox_user",
    "proxmox_token_name",
    "proxmox_token_value",
    "proxmox_verify_ssl",
    "proxmox_default_node",
    "proxmox_default_storage",
    "proxmox_endpoints",
    "vmware_host",
    "vmware_user",
    "vmware_password",
    "vmware_verify_ssl",
    "vmware_datacenter",
    "vmware_default_folder",
    "vmware_hosts",
    "vmware_default_datastore",
    "vmware_default_network",
    "vmware_endpoints",
    "otobo_url",
    "otobo_webservice_name",
    "otobo_user_login",
    "otobo_password",
    "otobo_verify_ssl",
    "otobo_status_provisioning",
    "otobo_status_done",
    "otobo_status_failed",
    "otobo_ssh_host",
    "otobo_ssh_user",
    "otobo_ssh_port",
    "otobo_ssh_key",
    "otobo_home",
    "otobo_os_user",
    "catalog_sync_interval_seconds",
    "otobo_df_hostname",
    "otobo_df_hypervisor",
    "otobo_df_cpu",
    "otobo_df_ram_mb",
    "otobo_df_disk_gb",
    "otobo_df_subnet",
    "otobo_df_vlan_id",
    "otobo_df_gateway",
    "otobo_df_template",
    "otobo_df_node",
    "otobo_df_os",
    "otobo_df_datastore",
)

SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "webhook_hmac_secret",
        "webhook_api_key",
        "netbox_token",
        "nautobot_token",
        "proxmox_token_value",
        "vmware_password",
        "otobo_password",
    }
)

BOOL_FIELDS: frozenset[str] = frozenset(
    {
        "netbox_verify_ssl",
        "nautobot_verify_ssl",
        "proxmox_verify_ssl",
        "vmware_verify_ssl",
        "otobo_verify_ssl",
    }
)

INT_FIELDS: frozenset[str] = frozenset({"otobo_ssh_port", "catalog_sync_interval_seconds"})


@lru_cache
def get_infra() -> InfraSettings:
    return InfraSettings()


def get_settings() -> Settings:
    from app.core.runtime_settings import load_settings

    return load_settings()


def invalidate_settings_cache() -> None:
    from app.core.runtime_settings import invalidate_cache

    invalidate_cache()


# Older scripts still call get_settings.cache_clear() from the lru_cache era.
get_settings.cache_clear = invalidate_settings_cache
