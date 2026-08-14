#!/usr/bin/env python3
"""Hide VM fields on generic OTOBO masks and import the VM Provisioning process.

Requires OTOBO package Znuny4OTOBO-DynamicFieldScreen (console commands
Znuny4OTOBO::DynamicFieldScreen::Add / Remove). Install via OTOBO package manager.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402

PROCESS_YAML = ROOT / "otobo" / "process-vm-provisioning.yml"

HIDE_SCREENS = [
    "Ticket::Frontend::AgentTicketPhone###DynamicField",
    "Ticket::Frontend::AgentTicketEmail###DynamicField",
    "Ticket::Frontend::AgentTicketFreeText###DynamicField",
]

SHOW_SCREENS = [
    "Ticket::Frontend::AgentTicketZoom###DynamicField",
    "Ticket::Frontend::AgentTicketZoom###ProcessWidgetDynamicField",
]

PERL_IMPORT = r'''
use strict;
use warnings;
use lib "__OTOBO_HOME__";
use lib "__OTOBO_HOME__/Kernel/cpan-lib";
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new();
my $UserID = 1;
my $Home = "__OTOBO_HOME__";
my $content = do { local $/; open my $fh, "<", "/tmp/process-vm-provisioning.yml" or die $!; <$fh> };
my $ProcessObject = $Kernel::OM->Get("Kernel::System::ProcessManagement::DB::Process");
my %Result = $ProcessObject->ProcessImport(
    Content                    => $content,
    OverwriteExistingEntities  => 1,
    UserID                     => $UserID,
);
if ( !$Result{Success} ) {
    die "ProcessImport failed: " . ($Result{Message} // "unknown") . " " . ($Result{Comment} // "") . "\n";
}
print $Result{Message} // "ProcessImport OK", "\n";
my $ok = $ProcessObject->ProcessDump(
    ResultType => "FILE",
    Location   => "$Home/Kernel/Config/Files/ZZZProcessManagement.pm",
    UserID     => $UserID,
);
die "ProcessDump failed\n" unless $ok;
print "PROCESS_DUMP_OK\n";
'''


def _otobo_ssh_host() -> str:
    s = get_settings()
    host = s.otobo_ssh_host or (urlparse(s.otobo_url).hostname or "")
    if not host:
        raise SystemExit("OTOBO_SSH_HOST or OTOBO_URL must be set")
    return host


def _ssh_opts(*, scp: bool = False) -> list[str]:
    s = get_settings()
    port_flag = "-P" if scp else "-p"
    return [
        "-i",
        s.otobo_ssh_key,
        port_flag,
        str(s.otobo_ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def _ssh_cmd() -> list[str]:
    s = get_settings()
    return ["ssh", *_ssh_opts(), f"{s.otobo_ssh_user}@{_otobo_ssh_host()}"]


def _scp_to(local: str, remote: str) -> None:
    s = get_settings()
    subprocess.check_call(
        [
            "scp",
            *_ssh_opts(scp=True),
            local,
            f"{s.otobo_ssh_user}@{_otobo_ssh_host()}:{remote}",
        ]
    )


def _vm_field_names() -> list[str]:
    s = get_settings()
    return [
        s.otobo_df_hostname,
        s.otobo_df_hypervisor,
        s.otobo_df_cpu,
        s.otobo_df_ram_mb,
        s.otobo_df_disk_gb,
        s.otobo_df_subnet,
        s.otobo_df_vlan_id,
        s.otobo_df_gateway,
        s.otobo_df_template,
        s.otobo_df_node,
        s.otobo_df_os,
        s.otobo_df_datastore,
    ]


def _console(remote: str) -> None:
    s = get_settings()
    os_user = shlex.quote(s.otobo_os_user)
    console = shlex.quote(f"{s.otobo_home.rstrip('/')}/bin/otobo.Console.pl")
    subprocess.check_call([*_ssh_cmd(), f"sudo -u {os_user} {console} {remote}"])


def main() -> int:
    get_settings.cache_clear()
    s = get_settings()
    if not PROCESS_YAML.is_file():
        raise SystemExit(f"Missing {PROCESS_YAML}")

    fields = _vm_field_names()
    df_args = " ".join(f"--dynamicfield {shlex.quote(name)}" for name in fields)
    hide_args = " ".join(f"--screen {shlex.quote(screen)}" for screen in HIDE_SCREENS)
    show_args = " ".join(f"--screen {shlex.quote(screen)}" for screen in SHOW_SCREENS)

    print("Hiding VM fields on Phone/Email/FreeText…")
    _console(f"Znuny4OTOBO::DynamicFieldScreen::Remove {df_args} {hide_args}")

    print("Keeping VM fields on Zoom + process widget…")
    _console(f"Znuny4OTOBO::DynamicFieldScreen::Add {df_args} --state 1 {show_args}")

    groups = {
        "VM": ",".join(fields),
    }
    groups_yaml = "---\n" + "".join(f"{k}: {v}\n" for k, v in groups.items())
    local_groups = Path("/tmp/otobo_process_widget_groups.yml")
    local_groups.write_text(groups_yaml, encoding="utf-8")
    _scp_to(str(local_groups), "/tmp/otobo_process_widget_groups.yml")
    _console(
        "Admin::Config::Update "
        "--setting-name Ticket::Frontend::AgentTicketZoom###ProcessWidgetDynamicFieldGroups "
        "--source-path /tmp/otobo_process_widget_groups.yml"
    )

    print("Importing VM Provisioning process…")
    otobo_home = s.otobo_home.rstrip("/")
    perl = PERL_IMPORT.replace("__OTOBO_HOME__", otobo_home)
    local_perl = Path("/tmp/otobo_import_vm_process.pl")
    local_perl.write_text(perl, encoding="utf-8")
    _scp_to(str(PROCESS_YAML), "/tmp/process-vm-provisioning.yml")
    _scp_to(str(local_perl), "/tmp/otobo_import_vm_process.pl")
    os_user = shlex.quote(s.otobo_os_user)
    console = shlex.quote(f"{otobo_home}/bin/otobo.Console.pl")
    subprocess.check_call(
        [
            *_ssh_cmd(),
            f"sudo -u {os_user} perl /tmp/otobo_import_vm_process.pl && sudo -u {os_user} {console} Maint::Config::Rebuild",
        ]
    )
    print("VM process deployed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
