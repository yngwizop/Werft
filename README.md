# Werft

OTOBO · NetBox · Nautobot · Proxmox · VMware ESXi · vCenter · IPAM · VM-Provisioning · Katalog-Sync · GenericInterface · Self-Service

OTOBO-Middleware, die NetBox/Nautobot und Hypervisoren wie Proxmox/VMware zu einem Workflow verbindet.

Für **VM-Provisioning** und Automatisierung: genehmigte **OTOBO**-Prozesstickets werden zu VMs auf **Proxmox VE** oder **VMware** (ESXi / vCenter), IPs kommen aus **NetBox** oder **Nautobot** (IPAM), Status und IP zurück ins Ticket. Ein periodischer **Katalog-Sync** füllt die OTOBO-Dropdowns (Nodes, Vorlagen/ISOs, Datastores) aus den Hypervisoren. Anbindung über GenericInterface-Webhook; Agenten arbeiten nur in OTOBO.


## Wozu?

Admins sollen VMs **über ein OTOBO-Ticket** anfordern und freigeben — ohne sich in Proxmox, vCenter oder NetBox/Nautobot einzuloggen.  
**Werft** ist die Middleware dazwischen und nimmt die Freigabe aus OTOBO entgegen, reserviert eine IP, baut die VM und schreibt Ergebnis (oder Fehler) zurück ins Ticket.

| Werft verbindet | Rolle |
|---|---|
| **OTOBO** | Antrag, Genehmigung, Status im Ticket |
| **NetBox** oder **Nautobot** | IPAM — IP aus dem gewählten Prefix (in den Einstellungen wählbar) |
| **Proxmox VE** und/oder **VMware** (ESXi / vCenter) | eigentliches Provisioning |
| **Katalog-Sync** | Nodes, Vorlagen/ISOs, Datastores → OTOBO-Dropdowns |

Kurz der Ablauf: Prozessticket „VM Provisioning“ → **Genehmigen** → Werft (IP + VM) → Kommentar und Ticket-Status in OTOBO.

## Ablauf

In OTOBO ein **Prozessticket** „VM Provisioning“ (kein „Neues Telefon-Ticket“). Freigabe ist **Genehmigen**, nicht „Sofort schließen“ — das macht Werft nach Erfolg.

```text
Neues Prozessticket → VM Provisioning
        → Genehmigen
        → Werft: IP in NetBox/Nautobot → VM auf Proxmox oder VMware
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
| **NetBox / Nautobot** | Prefix muss existieren; Token darf IPs anlegen und freigeben. Provider in den Einstellungen wählen. |
| **Proxmox und/oder VMware** | Mindestens eines. Standalone-ESXi nur ISO; Clone nur mit vCenter. |
| **Netz** | Werft erreicht OTOBO, IPAM und Hypervisor. OTOBO erreicht den Werft-Webhook (nicht localhost). |

Zugänge und Hosts werden in der Ops-UI unter **Einstellungen** gepflegt (verschlüsselt in Postgres). Erstes Login: `admin` / `changeme`, danach Passwort ändern.

### OTOBO-Voraussetzungen

Werft installiert das nicht — muss auf der OTOBO-Box stehen, bevor das Setup-Skript / der Tab **OTOBO-Setup** läuft:

| Abhängigkeit | Wozu |
|---|---|
| SSH von Werft, `sudo -u otobo` ohne Passwort | Console.pl, Prozessimport, Katalog |
| Paket **Znuny4OTOBO-DynamicFieldScreen** | VM-Felder nur auf dem Prozessticket, nicht auf Telefon/E-Mail. Ohne das Paket bricht der Prozess-Deploy ab (`Znuny4OTOBO::DynamicFieldScreen::*`). Admin → Paketverwaltung. |
| **`otobo.Daemon.pl` läuft** | Asynchroner Invoker `ProvisionVM`. Setup startet ihn bei Bedarf; Status/Start/Neustart auch in der Ops-UI. Dauerhaft: systemd auf der OTOBO-Box. |
| GenericInterface HTTP::REST, Process Management | Kernfunktionen; Webservice Provider/Requester und Prozessticket. |

Details: [docs/otobo-setup.md](docs/otobo-setup.md).

## Schnellstart

Voraussetzungen auf dem Werft-Host: **Docker Compose** und **Git**.

1. **Repo holen**

```bash
git clone https://github.com/yngwizop/Werft.git
cd Werft
```

2. **`.env` anlegen** (nur für Compose — alles andere später in der UI)

```bash
cp .env.example .env
```

```bash
TLS_CN=<werft-hostname-oder-ip>
OTOBO_SSH_KEY=/pfad/zum/privaten/key
```

| Variable | Bedeutung |
|---|---|
| `TLS_CN` | Common Name fürs selbstsignierte nginx-Zertifikat (Hostname oder IP von Werft) |
| `OTOBO_SSH_KEY` | **Nur der Dateipfad** zum privaten SSH-Key **auf dem Werft-Host** (z. B. `/root/.ssh/id_ed25519`). Wird in den Container gemountet. |

SSH-User, OTOBO-Home und OS-User (Default oft `root` / `/opt/otobo` / `otobo`) setzt du in den **Einstellungen**, nicht in `.env`. Auf der OTOBO-VM muss der passende **öffentliche** Key in `authorized_keys` liegen, und der SSH-User sollte ohne Passwort `sudo -u <otobo-os-user>` können.

3. **Starten**

```bash
docker compose up --build -d
```

4. **Ops-UI öffnen** — `https://<werft-host>/`  
   Erstes Login: `admin` / `changeme` → Passwort sofort ändern.

5. **Einstellungen** (Assistent starten über "Einrichtung starten" oder manuell): Webhook, OTOBO (URL/Login/SSH), IPAM (NetBox oder Nautobot), mindestens ein Hypervisor → **Speichern**.

6. **OTOBO-Setup**-Tab: zuerst **Dry-Run**, dann ohne Häkchen ausführen.  
   Legt Webservice, Prozess und Felder auf OTOBO an (idempotent). Webhook-Ziel ist automatisch `http://<werft-host>:8000`.

7. **Prüfen:** Status-Tab (OTOBO, IPAM, Hypervisor, Daemon). Agenten: *Neues Prozessticket → VM Provisioning → Genehmigen*.

| | |
|---|---|
| Ops-UI | `https://<werft-host>/` (Session-Login) |
| Webhook | `POST /api/v1/provision-vm` (API-Key; optional IP-Allowlist) |
| Health | `GET /healthz` |

Details zu OTOBO-Voraussetzungen: unten und [docs/otobo-setup.md](docs/otobo-setup.md). Agenten-Ablauf: [docs/otobo-workflow.md](docs/otobo-workflow.md).

## OTOBO konfigurieren (Details)

Webservice, Status, Queue, Felder und Prozess legt Werft per SSH an — idempotent, bestehendes bleibt. Bevorzugt über den Tab **OTOBO-Setup** (Schritt 6 oben).

CLI alternativ:

```bash
python scripts/install_otobo_setup.py --yes --dry-run \
  --werft-url http://<werft-host>:8000
```

Ohne `--dry-run` wird geschrieben. `--write-vault` speichert Webservice-Name und API-Key in die verschlüsselte DB (die GUI setzt das automatisch).
