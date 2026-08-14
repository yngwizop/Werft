#!/usr/bin/env python3
"""Sync Proxmox/VMware catalog (images + hosts) into OTOBO dropdowns and ACLs."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.catalog import list_datastores, list_hosts, list_images  # noqa: E402

PERL_TEMPLATE = r'''
use strict;
use warnings;
use JSON::PP;
use lib "__OTOBO_HOME__";
use lib "__OTOBO_HOME__/Kernel/cpan-lib";
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new();
my $json = do { local $/; open my $fh, "<", "/tmp/otobo_catalog_values.json" or die $!; <$fh> };
my $data = decode_json($json);
my $DF = $Kernel::OM->Get("Kernel::System::DynamicField");
my $UserID = 1;
my $labels = $data->{labels} || {};
my $fn = $data->{field_names} || {};
my $node_f = $fn->{node} || "VMNode";
my $tpl_f  = $fn->{template} || "VMTemplate";
my $hyp_f  = $fn->{hypervisor} || "VMHypervisor";
my $ds_f   = $fn->{datastore} || "VMDatastore";
my $home   = $data->{otobo_home} || "__OTOBO_HOME__";

for my $name (sort keys %{ $data->{fields} }) {
    my $field = $DF->DynamicFieldGet(Name => $name);
    die "missing field $name" unless $field && $field->{ID};
    my %cfg = %{ $field->{Config} // {} };
    $cfg{PossibleValues} = $data->{fields}{$name};
    $cfg{PossibleNone} = 1;
    $cfg{TranslatableValues} = 0;
    $cfg{DefaultValue} = "";
    my $ok = $DF->DynamicFieldUpdate(
        ID         => $field->{ID},
        Name       => $field->{Name},
        Label      => $labels->{$name} || $field->{Label},
        FieldOrder => $field->{FieldOrder},
        FieldType  => "Dropdown",
        ObjectType => $field->{ObjectType},
        Config     => \%cfg,
        ValidID    => 1,
        UserID     => $UserID,
        Reorder    => 0,
    );
    die "update failed for $name" unless $ok;
    print "Updated $name values=", scalar(keys %{ $data->{fields}{$name} }), "\n";
}

my $ACL = $Kernel::OM->Get("Kernel::System::ACL::DB::ACL");

sub upsert_acl {
    my ( $name, $match, $change, $comment ) = @_;
    my $existing = $ACL->ACLGet( Name => $name, UserID => $UserID );
    if ( $existing && $existing->{ID} ) {
        my $ok = $ACL->ACLUpdate(
            ID             => $existing->{ID},
            Name           => $name,
            Comment        => $comment,
            Description    => $existing->{Description} // "",
            StopAfterMatch => 0,
            ValidID        => 1,
            UserID         => $UserID,
            ConfigMatch    => $match,
            ConfigChange   => $change,
        );
        die "ACLUpdate failed $name" unless $ok;
        print "Updated ACL $name\n";
        return;
    }
    my $id = $ACL->ACLAdd(
        Name           => $name,
        Comment        => $comment,
        Description    => $comment,
        StopAfterMatch => 0,
        ValidID        => 1,
        UserID         => $UserID,
        ConfigMatch    => $match,
        ConfigChange   => $change,
    );
    die "ACLAdd failed $name" unless $id;
    print "Created ACL $name ($id)\n";
}

my $pve_nodes = $data->{acl}{proxmox_nodes} || [];
my $esx_hosts = $data->{acl}{vmware_hosts} || [];
my $pve_imgs  = $data->{acl}{proxmox_images} || [];
my $esx_imgs  = $data->{acl}{vmware_images} || [];
my $pve_ds    = $data->{acl}{proxmox_datastores} || [];
my $esx_ds    = $data->{acl}{vmware_datastores} || [];

sub with_empty {
    my ($vals) = @_;
    my @out = ("");
    for my $v ( @{ $vals || [] } ) {
        next unless defined $v && $v ne "";
        push @out, $v;
    }
    return \@out;
}

# Keep "" in Possible so ACL refresh does not auto-select the first catalog item.
my %pve_ticket = (
    "DynamicField_$node_f" => with_empty($pve_nodes),
    "DynamicField_$tpl_f"  => with_empty($pve_imgs),
    "DynamicField_$ds_f"   => with_empty($pve_ds),
);
upsert_acl(
    "200-VM-Hypervisor-Proxmox",
    { Properties => { DynamicField => { "DynamicField_$hyp_f" => ["proxmox"] } } },
    { Possible => { Ticket => \%pve_ticket } },
    "Restrict node/template/datastore dropdowns to Proxmox inventory",
);

my %esx_ticket = (
    "DynamicField_$node_f" => with_empty($esx_hosts),
    "DynamicField_$tpl_f"  => with_empty($esx_imgs),
    "DynamicField_$ds_f"   => with_empty($esx_ds),
);
upsert_acl(
    "201-VM-Hypervisor-VMware",
    { Properties => { DynamicField => { "DynamicField_$hyp_f" => ["vmware"] } } },
    { Possible => { Ticket => \%esx_ticket } },
    "Restrict node/template/datastore dropdowns to VMware inventory",
);

$ACL->ACLDump(
    UserID     => $UserID,
    ResultType => 'FILE',
    Location   => "$home/Kernel/Config/Files/ZZZACL.pm",
);
$ACL->ACLsNeedSyncReset();
my $Cache = $Kernel::OM->Get("Kernel::System::Cache");
$Cache->CleanUp(Type => "TicketACL");
$Cache->CleanUp(Type => "HiddenFields");
$Kernel::OM->Get("Kernel::System::Ticket::FieldRestrictions")->SetACLPreselectionCache();
print "ACL_DUMP_OK\n";
print "SYNC_OK\n";
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


def main() -> int:
    get_settings.cache_clear()
    s = get_settings()
    images = list_images()
    hosts = list_hosts()
    datastores = list_datastores()

    template_values = {img.id: img.label for img in images}
    node_values = {h.id: h.label for h in hosts}
    datastore_values = {d.id: d.label for d in datastores}
    os_values = {"linux": "Linux", "windows": "Windows", "other": "Other"}
    hypervisor_values = {"proxmox": "Proxmox", "vmware": "VMware"}

    proxmox_nodes = [h.id for h in hosts if h.hypervisor == "proxmox"]
    vmware_hosts = [h.id for h in hosts if h.hypervisor == "vmware"]
    proxmox_images = [i.id for i in images if i.hypervisor == "proxmox"]
    vmware_images = [i.id for i in images if i.hypervisor == "vmware"]
    proxmox_datastores = [d.id for d in datastores if d.hypervisor == "proxmox"]
    vmware_datastores = [d.id for d in datastores if d.hypervisor == "vmware"]

    otobo_home = s.otobo_home.rstrip("/")
    payload = {
        "otobo_home": otobo_home,
        "field_names": {
            "template": s.otobo_df_template,
            "os": s.otobo_df_os,
            "node": s.otobo_df_node,
            "hypervisor": s.otobo_df_hypervisor,
            "datastore": s.otobo_df_datastore,
        },
        "labels": {
            s.otobo_df_node: "Ziel (Cluster-Node / Host)",
            s.otobo_df_datastore: "Datastore / Storage",
        },
        "fields": {
            s.otobo_df_template: template_values,
            s.otobo_df_os: os_values,
            s.otobo_df_node: node_values,
            s.otobo_df_hypervisor: hypervisor_values,
            s.otobo_df_datastore: datastore_values,
        },
        "acl": {
            "proxmox_nodes": proxmox_nodes,
            "vmware_hosts": vmware_hosts,
            "proxmox_images": proxmox_images,
            "vmware_images": vmware_images,
            "proxmox_datastores": proxmox_datastores,
            "vmware_datastores": vmware_datastores,
        },
    }
    local = Path("/tmp/otobo_catalog_values.json")
    local.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    perl = PERL_TEMPLATE.replace("__OTOBO_HOME__", otobo_home)
    Path("/tmp/otobo_sync_catalog.pl").write_text(perl, encoding="utf-8")
    _scp_to(str(local), "/tmp/otobo_catalog_values.json")
    _scp_to("/tmp/otobo_sync_catalog.pl", "/tmp/otobo_sync_catalog.pl")
    os_user = shlex.quote(s.otobo_os_user)
    console = shlex.quote(f"{otobo_home}/bin/otobo.Console.pl")
    subprocess.check_call(
        [
            *_ssh_cmd(),
            f"sudo -u {os_user} perl /tmp/otobo_sync_catalog.pl && sudo -u {os_user} {console} Maint::Config::Rebuild",
        ]
    )
    print(f"Synced {len(template_values)} images, {len(node_values)} hosts, {len(datastore_values)} datastores")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
