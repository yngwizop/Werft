# Werft

OTOBO-Middleware, die NetBox und Hypervisoren wie Proxmox/VMware zu einem Workflow verbindet.

Für VM-Provisioning und Automatisierung: genehmigte Tickets werden zu VMs auf **Proxmox** oder **VMware** (ESXi/vCenter), IPs kommen aus **NetBox**, Status und IP zurück ins Ticket.

Der Agent arbeitet nur in OTOBO. Werft sitzt dahinter: nach der Freigabe wird eine IP reserviert, eine VM angelegt und das Ergebnis ins Ticket geschrieben.

## Ablauf

In OTOBO ein **Prozessticket** „VM Provisioning“ (kein „Neues Telefon-Ticket“). Freigabe ist **Genehmigen**, nicht „Sofort schließen“ — das macht Werft nach Erfolg.

```text
Neues Prozessticket → VM Provisioning
        → Genehmigen
        → Werft: IP in NetBox → VM auf Proxmox oder VMware
        → Erfolg: Kommentar mit IP, Ticket geschlossen
        → Fehler: Kommentar, Status Failed, IP wieder frei
```

| Ziel | Provisioning |
|---|---|
| Proxmox | Template-Clone (Cloud-Init) oder VM + ISO |
| Standalone ESXi | nur ISO |
| vCenter | Template-Clone, optional ISO |

Vorlagen, Nodes und Datastores kommen periodisch von den Hypervisoren in die Ticket-Dropdowns.

Schritt für Schritt für Agenten: [docs/otobo-workflow.md](docs/otobo-workflow.md).

## Bestandteile

| Komponente | Rolle |
|---|---|
| **Werft** | API, Worker, Ops-UI, Katalog-Sync |
| **OTOBO** | Antrag und Freigabe. Siehe Voraussetzungen unten. |
| **NetBox** | Prefix muss existieren; Token darf IPs anlegen und freigeben. |
| **Proxmox und/oder VMware** | Mindestens eines. Standalone-ESXi nur ISO; Clone nur mit vCenter. |
| **Netz** | Werft erreicht OTOBO, NetBox und Hypervisor. OTOBO erreicht den Werft-Webhook (nicht localhost). |

Zugänge und Hosts werden in der Ops-UI unter **Einstellungen** gepflegt (verschlüsselt in Postgres). Erstes Login: `admin` / `changeme`, danach Passwort ändern.

### OTOBO-Voraussetzungen

Werft installiert das nicht — muss auf der OTOBO-Box stehen, bevor das Setup-Skript / der Tab **OTOBO-Setup** läuft:

| Abhängigkeit | Wozu |
|---|---|
| SSH von Werft, `sudo -u otobo` ohne Passwort | Console.pl, Prozessimport, Katalog |
| Paket **Znuny4OTOBO-DynamicFieldScreen** | VM-Felder nur auf dem Prozessticket, nicht auf Telefon/E-Mail. Ohne das Paket bricht der Prozess-Deploy ab (`Znuny4OTOBO::DynamicFieldScreen::*`). Admin → Paketverwaltung. |
| **`otobo.Daemon.pl` läuft** | Asynchroner Invoker `ProvisionVM`. Das Setup prüft nur den Status, startet den Daemon nicht. |
| GenericInterface HTTP::REST, Process Management | Kernfunktionen; Webservice Provider/Requester und Prozessticket. |

Details: [docs/otobo-setup.md](docs/otobo-setup.md).

## Starten

```bash
docker compose up --build -d
```

`.env` ist nur noch für Compose: `TLS_CN` (Zertifikat) und optional `OTOBO_SSH_KEY` (Pfad der Key-Datei im Container). Zugänge liegen in der Werft-UI.

| | |
|---|---|
| Ops-UI | `https://<werft-host>/` (Session-Login) |
| Webhook | `POST /api/v1/provision-vm` (API-Key, ohne Login; optional IP-Allowlist) |
| Health | `GET /healthz` (ohne Login) |

## OTOBO konfigurieren

Webservice, Status, Queue, Felder und Prozess legt Werft einmalig per SSH an — idempotent, bestehendes bleibt. Technische Details: [docs/otobo-setup.md](docs/otobo-setup.md).

**Empfohlener Start:** Werft-UI → Tab **OTOBO-Setup** (nicht mehr per `.env` pflegen).

1. Unter **Einstellungen** OTOBO (URL, Login, SSH), Webhook-Key, NetBox und Hypervisor eintragen und speichern.
2. Tab **OTOBO-Setup**: öffentliche Werft-URL prüfen (so, wie OTOBO sie erreichen kann; Lab oft `http://<werft-ip>:8000`).
3. **Dry-Run** — zeigt, was passieren würde, schreibt nichts.
4. Häkchen weg, nochmal ausführen. Der verwendete Webhook-Key wird in der Werft-DB gehalten und in den Invoker geschrieben.

Dasselbe per CLI:

```bash
python scripts/install_otobo_setup.py --yes --dry-run \
  --werft-url http://<werft-host>:8000
```

Ohne `--dry-run` wird geschrieben. `--write-vault` speichert Webservice-Name und API-Key in die verschlüsselte DB (nicht in `.env`; die GUI setzt das automatisch).
