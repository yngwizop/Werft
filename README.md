# Werft

Werft ist ein Selfservice-VM-Provisioning Middleware-Tool, worüber Admins per Ticket VMs anfordern können.


`OTOBO` · `NetBox` · `Nautobot` · `Proxmox` · `VMware ESXi` · `vCenter`

## Was macht Werft?

Werft wurde erschaffen, um den VM-Erstellungsprozess via Ticket zu vereinfachen. Dabei verknüpft sich Werft mit Otobo, NetBox/Nautobot und Proxmox/VMware und agiert als Orchestrator. Werft sorgt dafür, dass in Otobo eine VM Provisioning Maske zur Erstellung von VMs bereitgestellt wird. In dieser Maske werden alle Infos, die zur Erstellung einer VM auf Proxmox oder VMware nötig sind eingegeben. Über den nachfolgenden "Genehmigungsprozess" wird die VM letztendlich erstellt und in NetBox/Nautobot dokumentiert.

## Was bringt mir das Tool nun?

Normalerweise sieht der "Ich brauche eine VM" Prozess so aus: Ticket schreiben, Admin loggt sich in NetBox/Nautobot ein und reserviert eine IP. Nun loggt sich der Admin in Proxmox/vCenter ein und klickt die VM zusammen und trägt danach die VM/IP wieder ins Ticket ein.
Drei Systeme, alles wird manuell erledigt und ist demzufolge fehleranfällig.

Werft nimmt Admins genau diesen Teil ab. Es sitzt als Middleware dazwischen und verwaltet den gesamten Prozess, sodass nur noch im Otobo-Prozessticket die VM-Provisioning Maske ausgefüllt werden muss.


## Ablauf

In OTOBO ein **Prozessticket** „VM Provisioning“ (kein „Neues Telefon-Ticket“). Freigabe ist **Genehmigen**, nicht „Sofort schließen“ — das macht Werft nach Erfolg.

```text
Neues Prozessticket: 
        → VM Provisioning auswählen aund ausfüllen
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
| **Werft** | API, Worker, Werft-UI, Katalog-Sync |
| **OTOBO** | Antrag und Freigabe. Siehe Voraussetzungen unten. |
| **NetBox / Nautobot** | Provider in den Einstellungen wählen (URL + Token). Prefixe müssen vorhanden sein. Im Ticket nur CIDR (z. B. `10.10.10.0/24`) — Werft reserviert die nächste freie IP, setzt sie nach Erfolg auf active, bei Fehler wieder frei. |
| **Proxmox und/oder VMware** | Mindestens eines. Standalone-ESXi nur ISO; Clone nur mit vCenter. |
| **Netz** | Werft erreicht OTOBO, IPAM und Hypervisor. OTOBO erreicht den Werft-Webhook (nicht localhost). |

Zugänge und Hosts werden in der Werft-UI unter **Einstellungen** gepflegt (verschlüsselt in Postgres). Erstes Login: `admin` / `changeme`, danach Passwort ändern.

### OTOBO-Voraussetzungen

Werft installiert das nicht — muss auf der OTOBO-Box stehen, bevor das Setup-Skript / der Tab **OTOBO-Setup** läuft:

| Abhängigkeit | Wozu |
|---|---|
| SSH von Werft, `sudo -u otobo` ohne Passwort | Console.pl, Prozessimport, Katalog |
| Paket **Znuny4OTOBO-DynamicFieldScreen** | VM-Felder nur auf dem Prozessticket, nicht auf Telefon/E-Mail. Ohne das Paket bricht der Prozess-Deploy ab (`Znuny4OTOBO::DynamicFieldScreen::*`). Admin → Paketverwaltung. |
| **`otobo.Daemon.pl` läuft** | Asynchroner Invoker `ProvisionVM`. Setup startet ihn bei Bedarf; Status/Start/Neustart auch in der Werft-UI. Dauerhaft: systemd auf der OTOBO-Box. |
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

4. **Werft-UI öffnen** — `https://<werft-host>/`  
   Erstes Login: `admin` / `changeme` → Passwort sofort ändern.

5. **Einstellungen** (Assistent starten über "Einrichtung starten" oder manuell): Webhook, OTOBO (URL/Login/SSH), IPAM (NetBox oder Nautobot), mindestens ein Hypervisor → **Speichern**.

6. **OTOBO-Setup**-Tab: zuerst **Dry-Run**, dann ohne Häkchen ausführen.  
   Legt Webservice, Prozess und Felder auf OTOBO an (idempotent). Webhook-Ziel ist automatisch `http://<werft-host>:8000`.

7. **Prüfen:** Status-Tab (OTOBO, IPAM, Hypervisor, Daemon). Agenten: *Neues Prozessticket → VM Provisioning → Genehmigen*.

| | |
|---|---|
| Werft-UI | `https://<werft-host>/` (Session-Login) |
| Webhook | `POST /api/v1/provision-vm` (API-Key; optional IP-Allowlist) |
| Health | `GET /healthz` |

Details zu OTOBO-Voraussetzungen: unten und [docs/otobo-setup.md](docs/otobo-setup.md). Agenten-Ablauf: [docs/otobo-workflow.md](docs/otobo-workflow.md).

## OTOBO konfigurieren (Details)

**Emfohlen:** Otobo Einstellungen --> "Einrichtung starten" Guide abrabteiten, danach ready.

Webservice, Status, Queue, Felder und Prozess legt Werft per SSH an — idempotent, bestehendes bleibt. Bevorzugt über den Tab  (Schritt 6 oben).

Hinweis: das Otobo Setup läuft **idempotent** durch.

CLI alternativ:

```bash
python scripts/install_otobo_setup.py --yes --dry-run \
  --werft-url http://<werft-host>:8000
```

Ohne `--dry-run` wird geschrieben. `--write-vault` speichert Webservice-Name und API-Key in die verschlüsselte DB (die GUI setzt das automatisch).
