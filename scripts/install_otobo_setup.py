#!/usr/bin/env python3
"""Interactive, idempotent OTOBO bootstrap for VM provisioning.

Creates only VM-specific objects (states, queue, dynamic fields, process,
webservice invoker). Existing webservices are merged, not replaced, so other
invokers/operations stay intact.

Usage:
  cd /opt/werft && .venv/bin/python scripts/install_otobo_setup.py
  .venv/bin/python scripts/install_otobo_setup.py --yes --dry-run
"""

from __future__ import annotations

import argparse
import json
import secrets
import shlex
import subprocess
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402

WS_TEMPLATE = ROOT / "otobo" / "webservice-provisioning.yml"

PERL_BOOTSTRAP = r'''
use strict;
use warnings;
use lib "__OTOBO_HOME__";
use lib "__OTOBO_HOME__/Kernel/cpan-lib";
use JSON::PP;
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new();

my $UserID = 1;
my $dry = ("__DRY_RUN__" eq "1");
my $json = do { local $/; open my $fh, "<", "/tmp/otobo_bootstrap.json" or die $!; <$fh> };
my $plan = decode_json($json);

sub say_step { print @_, "\n"; }

# --- states ---
my $State = $Kernel::OM->Get("Kernel::System::State");
my %type_by_name;
{
    my %types = $State->StateTypeList(UserID => $UserID);
    %type_by_name = reverse %types;
}
for my $row (@{ $plan->{states} }) {
    my %existing = $State->StateList(UserID => $UserID, Valid => 0);
    my %by_name = reverse %existing;
    if ($by_name{ $row->{name} }) {
        say_step("SKIP state $row->{name}");
        next;
    }
    my $type_id = $type_by_name{ $row->{type} };
    die "unknown state type $row->{type}\n" unless $type_id;
    say_step($dry ? "WOULD create state $row->{name} ($row->{type})" : "CREATE state $row->{name}");
    next if $dry;
    my $id = $State->StateAdd(
        Name    => $row->{name},
        Comment => $row->{comment} // "",
        TypeID  => $type_id,
        ValidID => 1,
        UserID  => $UserID,
    );
    die "StateAdd failed for $row->{name}\n" unless $id;
}

# --- queue ---
my $Queue = $Kernel::OM->Get("Kernel::System::Queue");
my $Group = $Kernel::OM->Get("Kernel::System::Group");
my $qname = $plan->{queue_name};
my %qlist = $Queue->QueueList(Valid => 0);
my %qby = reverse %qlist;
if ($qby{$qname}) {
    say_step("SKIP queue $qname");
}
else {
    my $gname = $plan->{queue_group} || "users";
    my $gid = $Group->GroupLookup(Group => $gname);
    die "queue group not found: $gname\n" unless $gid;
    say_step($dry ? "WOULD create queue $qname (group $gname)" : "CREATE queue $qname");
    if (!$dry) {
        my $qid = $Queue->QueueAdd(
            Name            => $qname,
            GroupID         => $gid,
            ValidID         => 1,
            SystemAddressID => 1,
            SalutationID    => 1,
            SignatureID     => 1,
            FollowUpID      => 1,
            Comment         => "VM requests from process tickets",
            UserID          => $UserID,
        );
        die "QueueAdd failed\n" unless $qid;
    }
}

# --- dynamic fields ---
my $DF = $Kernel::OM->Get("Kernel::System::DynamicField");
for my $row (@{ $plan->{fields} }) {
    my $existing = $DF->DynamicFieldGet(Name => $row->{name});
    if ($existing && $existing->{ID}) {
        say_step("SKIP field $row->{name}");
        next;
    }
    say_step($dry ? "WOULD create field $row->{name}" : "CREATE field $row->{name}");
    next if $dry;
    my $ok = $DF->DynamicFieldAdd(
        Name       => $row->{name},
        Label      => $row->{label},
        FieldOrder => int($row->{order}),
        FieldType  => $row->{type},
        ObjectType => "Ticket",
        Config     => $row->{config},
        ValidID    => 1,
        UserID     => $UserID,
    );
    die "DynamicFieldAdd failed $row->{name}\n" unless $ok;
}

# --- webservice merge / add ---
my $WS = $Kernel::OM->Get("Kernel::System::GenericInterface::Webservice");
my $wanted_name = $plan->{webservice_name};
my $force_host = $plan->{force_requester_host} ? 1 : 0;
my $mw = $plan->{middleware_url};
$mw =~ s{/$}{};
my $api_key = $plan->{webhook_api_key};

my $template_yaml = do { local $/; open my $fh, "<", "/tmp/otobo-webservice-provisioning.yml" or die $!; <$fh> };
my $template = $Kernel::OM->Get("Kernel::System::YAML")->Load(Data => $template_yaml);
die "webservice template YAML invalid\n" unless $template && ref $template eq "HASH";
$template->{Requester}{Transport}{Config}{Host} = $mw;
$template->{Requester}{Transport}{Config}{AdditionalHeaders}{"X-Api-Key"} = $api_key;
$template->{Requester}{Transport}{Config}{OutboundHeaders}{"X-Api-Key"} = $api_key;

sub find_ws {
    my ($name) = @_;
    my $list = $WS->WebserviceList(Valid => 0);
    for my $id (keys %{$list || {}}) {
        return $WS->WebserviceGet(ID => $id) if $list->{$id} eq $name;
    }
    return;
}

sub ws_has_provision_invoker {
    my ($cfg) = @_;
    return $cfg->{Requester}{Invoker}{ProvisionVM} ? 1 : 0;
}

my $target = find_ws($wanted_name);
# Only create/update the configured name (default Werft-Sync-Api). Do not adopt
# another webservice that already has ProvisionVM — that left the vault name wrong.

if (!$target) {
    say_step($dry ? "WOULD add webservice $wanted_name" : "CREATE webservice $wanted_name");
    if (!$dry) {
        my $id = $WS->WebserviceAdd(
            Name    => $wanted_name,
            Config  => $template,
            ValidID => 1,
            UserID  => $UserID,
        );
        die "WebserviceAdd failed\n" unless $id;
    }
}
else {
    my $cfg = $target->{Config} || {};
    my $invokers = $cfg->{Requester}{Invoker} || {};
    my @others = grep { $_ ne "ProvisionVM" } keys %$invokers;
    my $old_host = $cfg->{Requester}{Transport}{Config}{Host} // "";
    $old_host =~ s{/$}{};
    if ($old_host && lc($old_host) ne lc($mw) && @others && !$force_host) {
        die "Webservice $target->{Name} requester host is '$old_host' and has other invokers ("
          . join(", ", @others)
          . "). Refusing to change Host. Use a new --webservice-name or --force-requester-host.\n";
    }

    my $want_map = $template->{Requester}{Transport}{Config}{InvokerControllerMapping}{ProvisionVM} || {};
    my $have_map = $cfg->{Requester}{Transport}{Config}{InvokerControllerMapping}{ProvisionVM} || {};
    my $have_key_add = $cfg->{Requester}{Transport}{Config}{AdditionalHeaders}{"X-Api-Key"} // "";
    my $have_key_out = $cfg->{Requester}{Transport}{Config}{OutboundHeaders}{"X-Api-Key"} // "";
    my @missing_ops;
    for my $op (sort keys %{ $template->{Provider}{Operation} || {} }) {
        push @missing_ops, $op unless $cfg->{Provider}{Operation}{$op};
    }
    my @diff;
    push @diff, "ProvisionVM missing" unless ws_has_provision_invoker($cfg);
    push @diff, "Host $old_host -> $mw" if lc($old_host) ne lc($mw);
    push @diff, "Controller mapping" if (
        ($have_map->{Command} // "") ne ($want_map->{Command} // "")
        || ($have_map->{Controller} // "") ne ($want_map->{Controller} // "")
    );
    push @diff, "X-Api-Key" if ($have_key_add ne $api_key || $have_key_out ne $api_key);
    push @diff, "Provider ops: " . join(",", @missing_ops) if @missing_ops;

    if (!@diff) {
        say_step("SKIP webservice $target->{Name} (ProvisionVM already configured)");
    }
    else {
        # Additive provider ops + routes
        $cfg->{Provider}{Transport}{Type} ||= "HTTP::REST";
        $cfg->{Provider}{Transport}{Config}{MaxLength} ||= "10000000";
        for my $op (keys %{ $template->{Provider}{Operation} }) {
            $cfg->{Provider}{Operation}{$op} ||= $template->{Provider}{Operation}{$op};
            $cfg->{Provider}{Transport}{Config}{RouteOperationMapping}{$op}
              ||= $template->{Provider}{Transport}{Config}{RouteOperationMapping}{$op};
        }
        $cfg->{Requester}{Transport}{Type} = "HTTP::REST";
        $cfg->{Requester}{Transport}{Config}{DefaultCommand} ||= "POST";
        $cfg->{Requester}{Transport}{Config}{Timeout} ||= "120";
        $cfg->{Requester}{Invoker}{ProvisionVM} = $template->{Requester}{Invoker}{ProvisionVM};
        $cfg->{Requester}{Transport}{Config}{InvokerControllerMapping}{ProvisionVM}
          = $template->{Requester}{Transport}{Config}{InvokerControllerMapping}{ProvisionVM};
        $cfg->{Requester}{Transport}{Config}{Host} = $mw;
        $cfg->{Requester}{Transport}{Config}{AdditionalHeaders}{"X-Api-Key"} = $api_key;
        $cfg->{Requester}{Transport}{Config}{OutboundHeaders}{"X-Api-Key"} = $api_key;
        $cfg->{Debugger} ||= $template->{Debugger};

        my $reason = join("; ", @diff);
        say_step(
            $dry
            ? "WOULD merge ProvisionVM into webservice $target->{Name} ($reason)"
            : "UPDATE webservice $target->{Name} ($reason)"
        );
        if (!$dry) {
            my $ok = $WS->WebserviceUpdate(
                ID      => $target->{ID},
                Name    => $target->{Name},
                Config  => $cfg,
                ValidID => $target->{ValidID} || 1,
                UserID  => $UserID,
            );
            die "WebserviceUpdate failed\n" unless $ok;
        }
    }
}

say_step("WEBSERVICE_NAME $wanted_name");
say_step("BOOTSTRAP_OK");
'''


def _otobo_ssh_host(s) -> str:
    host = s.otobo_ssh_host or (urlparse(s.otobo_url).hostname or "")
    if not host:
        raise SystemExit("OTOBO_SSH_HOST or OTOBO_URL must be set")
    return host


def _ssh_opts(s, *, scp: bool = False) -> list[str]:
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


def _ssh_cmd(s) -> list[str]:
    return ["ssh", *_ssh_opts(s), f"{s.otobo_ssh_user}@{_otobo_ssh_host(s)}"]


def _scp_to(s, local: str, remote: str) -> None:
    subprocess.check_call(
        [
            "scp",
            *_ssh_opts(s, scp=True),
            local,
            f"{s.otobo_ssh_user}@{_otobo_ssh_host(s)}:{remote}",
        ]
    )


def _ask(prompt: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    label = f"{prompt}{suffix}: "
    if secret:
        value = getpass(label)
    else:
        value = input(label)
    value = value.strip()
    return value if value else default


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    reply = input(f"{prompt} [y/N]: ").strip().lower()
    return reply in {"y", "yes", "j", "ja"}


def _patch_env(path: Path, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0] if stripped and not stripped.startswith("#") and "=" in stripped else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _plan(s, args) -> dict:
    mw = args.middleware_url.rstrip("/")
    return {
        "queue_name": args.queue_name,
        "queue_group": args.queue_group,
        "webservice_name": args.webservice_name,
        "middleware_url": mw,
        "webhook_api_key": args.webhook_api_key,
        "force_requester_host": bool(args.force_requester_host),
        "states": [
            {
                "name": s.otobo_status_provisioning or "Provisioning",
                "type": "open",
                "comment": "VM clone/ISO in progress",
            },
            {
                "name": "Genehmigt",
                "type": "pending reminder",
                "comment": "Triggers Werft webhook",
            },
            {
                "name": s.otobo_status_failed or "Failed",
                "type": "open",
                "comment": "VM provisioning failed",
            },
        ],
        "fields": [
            {
                "name": s.otobo_df_hostname,
                "label": "Hostname",
                "order": 200,
                "type": "Text",
                "config": {"DefaultValue": ""},
            },
            {
                "name": s.otobo_df_hypervisor,
                "label": "Hypervisor",
                "order": 201,
                "type": "Dropdown",
                "config": {
                    "DefaultValue": "",
                    "PossibleNone": 1,
                    "TranslatableValues": 0,
                    "PossibleValues": {"proxmox": "Proxmox", "vmware": "VMware"},
                },
            },
            {
                "name": s.otobo_df_cpu,
                "label": "CPU (vCPU)",
                "order": 202,
                "type": "Text",
                "config": {"DefaultValue": "2"},
            },
            {
                "name": s.otobo_df_ram_mb,
                "label": "RAM (MB)",
                "order": 203,
                "type": "Text",
                "config": {"DefaultValue": "2048"},
            },
            {
                "name": s.otobo_df_disk_gb,
                "label": "Disk (GB)",
                "order": 204,
                "type": "Text",
                "config": {"DefaultValue": "20"},
            },
            {
                "name": s.otobo_df_subnet,
                "label": "Subnet (CIDR)",
                "order": 205,
                "type": "Text",
                "config": {"DefaultValue": ""},
            },
            {
                "name": s.otobo_df_vlan_id,
                "label": "VLAN ID",
                "order": 206,
                "type": "Text",
                "config": {"DefaultValue": ""},
            },
            {
                "name": s.otobo_df_gateway,
                "label": "Gateway",
                "order": 207,
                "type": "Text",
                "config": {"DefaultValue": ""},
            },
            {
                "name": s.otobo_df_template,
                "label": "Vorlage",
                "order": 208,
                "type": "Dropdown",
                "config": {
                    "DefaultValue": "",
                    "PossibleNone": 1,
                    "TranslatableValues": 0,
                    "PossibleValues": {},
                },
            },
            {
                "name": s.otobo_df_node,
                "label": "Ziel (Cluster-Node / Host)",
                "order": 209,
                "type": "Dropdown",
                "config": {
                    "DefaultValue": "",
                    "PossibleNone": 1,
                    "TranslatableValues": 0,
                    "PossibleValues": {},
                },
            },
            {
                "name": s.otobo_df_os,
                "label": "OS",
                "order": 210,
                "type": "Dropdown",
                "config": {
                    "DefaultValue": "",
                    "PossibleNone": 1,
                    "TranslatableValues": 0,
                    "PossibleValues": {"linux": "Linux", "windows": "Windows", "other": "Other"},
                },
            },
            {
                "name": s.otobo_df_datastore,
                "label": "Datastore / Storage",
                "order": 211,
                "type": "Dropdown",
                "config": {
                    "DefaultValue": "",
                    "PossibleNone": 1,
                    "TranslatableValues": 0,
                    "PossibleValues": {},
                },
            },
        ],
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Idempotent OTOBO setup for VM provisioning")
    p.add_argument("--yes", action="store_true", help="Use vault defaults, no prompts")
    p.add_argument("--dry-run", action="store_true", help="Show remote actions without writing")
    p.add_argument("--skip-process", action="store_true")
    p.add_argument("--skip-catalog-sync", action="store_true")
    p.add_argument("--skip-daemon-check", action="store_true")
    p.add_argument(
        "--write-vault",
        "--write-env",
        dest="write_vault",
        action="store_true",
        help="Save webservice name and webhook key into the encrypted vault (not .env)",
    )
    p.add_argument("--force-requester-host", action="store_true")
    p.add_argument("--webservice-name", default="")
    p.add_argument("--queue-name", default="VM Provisioning")
    p.add_argument("--queue-group", default="users")
    p.add_argument("--middleware-url", "--werft-url", default="", help="Public Werft URL (no trailing slash)")
    p.add_argument("--webhook-api-key", default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    get_settings.cache_clear()
    s = get_settings()

    default_ws = args.webservice_name or s.otobo_webservice_name or "Werft-Sync-Api"
    if args.middleware_url:
        default_mw = args.middleware_url.rstrip("/")
    else:
        import socket

        host_guess = "127.0.0.1"
        try:
            host_guess = socket.gethostbyname(socket.gethostname()) or host_guess
        except OSError:
            pass
        default_mw = f"http://{host_guess}:8000"
    default_key = args.webhook_api_key or s.webhook_api_key or secrets.token_urlsafe(24)

    if args.yes:
        args.webservice_name = default_ws
        args.middleware_url = default_mw
        args.webhook_api_key = default_key
    else:
        print("OTOBO VM-Provisioning Setup (idempotent; Webservice Werft-Sync-Api)\n")
        print("SSH:", f"{s.otobo_ssh_user}@{_otobo_ssh_host(s)}  home={s.otobo_home}")
        args.webservice_name = _ask("Webservice-Name", default_ws)
        args.middleware_url = _ask("Werft-URL (ohne Slash)", default_mw)
        key = _ask("X-Api-Key (leer = vorhandener/neu)", default_key if s.webhook_api_key else "", secret=True)
        args.webhook_api_key = key or default_key
        args.queue_name = _ask("Queue", args.queue_name)
        args.queue_group = _ask("Queue-Gruppe", args.queue_group)
        if not args.skip_catalog_sync:
            args.skip_catalog_sync = not _confirm("Katalog (Nodes/ISOs) jetzt syncen?", assume_yes=False)
        if not args.write_vault:
            args.write_vault = _confirm("Webservice/API-Key in der Werft-DB speichern?", assume_yes=False)

    if not WS_TEMPLATE.is_file():
        raise SystemExit(f"Missing {WS_TEMPLATE}")

    print("\nPlan:", flush=True)
    print(f"  OTOBO      {s.otobo_url or _otobo_ssh_host(s)}", flush=True)
    print(f"  Queue      {args.queue_name} (Gruppe {args.queue_group})", flush=True)
    print(f"  Webservice {args.webservice_name}", flush=True)
    print(f"  Werft {args.middleware_url.rstrip('/')}", flush=True)
    print(f"  Dry-run    {args.dry_run}", flush=True)
    if not args.yes and not args.dry_run:
        if not _confirm("Auf OTOBO anwenden?", assume_yes=False):
            print("Abgebrochen")
            return 1

    payload = _plan(s, args)
    Path("/tmp/otobo_bootstrap.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    perl = (
        PERL_BOOTSTRAP.replace("__OTOBO_HOME__", s.otobo_home.rstrip("/"))
        .replace("__DRY_RUN__", "1" if args.dry_run else "0")
    )
    Path("/tmp/otobo_bootstrap.pl").write_text(perl, encoding="utf-8")
    _scp_to(s, "/tmp/otobo_bootstrap.json", "/tmp/otobo_bootstrap.json")
    _scp_to(s, str(WS_TEMPLATE), "/tmp/otobo-webservice-provisioning.yml")
    _scp_to(s, "/tmp/otobo_bootstrap.pl", "/tmp/otobo_bootstrap.pl")

    os_user = shlex.quote(s.otobo_os_user)
    console = shlex.quote(f"{s.otobo_home.rstrip('/')}/bin/otobo.Console.pl")
    rebuild = "" if args.dry_run else f" && sudo -u {os_user} {console} Maint::Config::Rebuild"
    subprocess.check_call(
        [
            *_ssh_cmd(s),
            f"sudo -u {os_user} perl /tmp/otobo_bootstrap.pl{rebuild}",
        ]
    )

    if not args.dry_run and not args.skip_process:
        print("\nProzess + Masken…")
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / "deploy_otobo_vm_process.py")])

    if not args.dry_run and not args.skip_catalog_sync:
        print("\nKatalog-Sync…")
        rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "sync_otobo_catalog.py")])
        if rc != 0:
            print("Katalog-Sync fehlgeschlagen (Hypervisor/SSH). Später erneut: scripts/sync_otobo_catalog.py")

    if not args.skip_daemon_check:
        print("\nOTOBO-Daemon:")
        daemon_bin = f"{s.otobo_home.rstrip('/')}/bin/otobo.Daemon.pl"
        status = subprocess.run(
            [*_ssh_cmd(s), f"sudo -u {os_user} {daemon_bin} status"],
            capture_output=True,
            text=True,
        )
        status_text = (status.stdout or "") + (status.stderr or "")
        if status_text.strip():
            print(status_text.rstrip())
        lowered = status_text.lower()
        if "not running" in lowered:
            running = False
        elif "running" in lowered:
            running = True
        else:
            running = False
            print("WARN: Daemon-Status unklar — starte nicht automatisch.")
        if running:
            print("SKIP daemon (already running)")
        elif "not running" not in lowered:
            pass
        elif args.dry_run:
            print("WOULD start daemon (asynchroner Invoker ProvisionVM)")
        else:
            print("START daemon…")
            start = subprocess.run(
                [*_ssh_cmd(s), f"sudo -u {os_user} {daemon_bin} start"],
                capture_output=True,
                text=True,
            )
            if start.stdout.strip():
                print(start.stdout.rstrip())
            if start.stderr.strip():
                print(start.stderr.rstrip())
            check = subprocess.run(
                [*_ssh_cmd(s), f"sudo -u {os_user} {daemon_bin} status"],
                capture_output=True,
                text=True,
            )
            check_text = (check.stdout or "") + (check.stderr or "")
            if check_text.strip():
                print(check_text.rstrip())
            if "not running" in check_text.lower():
                print("WARN: Daemon start fehlgeschlagen — auf der OTOBO-VM prüfen.")
            else:
                print("Daemon läuft.")

    if args.write_vault and not args.dry_run:
        from app.core.runtime_settings import save_vault

        overlay: dict[str, str] = {
            "otobo_webservice_name": args.webservice_name,
        }
        if args.webhook_api_key:
            overlay["webhook_api_key"] = args.webhook_api_key
        save_vault(overlay)
        print("Gespeichert in der verschlüsselten Werft-DB (Webservice, API-Key)")

    print("\nFertig. Agenten: Tickets → Neues Prozessticket → VM Provisioning → Genehmigen.")
    print("Bestehende fremde Webservices/Felder wurden nicht gelöscht.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
