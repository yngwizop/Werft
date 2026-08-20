# OTOBO ↔ Middleware Setup

Kurzanleitung, um den VM-Provisioning-Webhook nachzuziehen.

**Agenten-Workflow** (Prozessticket anlegen, Genehmigen): [otobo-workflow.md](otobo-workflow.md)

## 0. Empfohlen: Setup über die Werft-UI

Zugänge liegen in der Ops-UI (verschlüsselt in Postgres), nicht mehr in `.env`.

1. **Einstellungen:** OTOBO-URL/Login, SSH für Setup, Webhook-Key, NetBox, Proxmox/VMware.
2. Tab **OTOBO-Setup** → Dry-Run, dann ohne Häkchen ausführen.

CLI:

```bash
cd /opt/werft && .venv/bin/python scripts/install_otobo_setup.py --yes --dry-run \
  --middleware-url http://<werft-host>:8000
```

Das Skript ist **idempotent für fremde Objekte**: vorhandene VM-Felder/Status/Queue werden übersprungen. Ein bestehender Webservice wird **gemerged** (Invoker `ProvisionVM` plus Provider-Operationen SessionCreate/TicketGet/TicketUpdate). Andere Invoker bleiben. Der Invoker `ProvisionVM` selbst und Host/`X-Api-Key` werden bei erneutem Lauf auf die Vorlage/aktuellen Key gesetzt.

Der Prozess **VM Provisioning** (feste Entity-IDs) wird bei jedem Lauf **überschrieben** — nur dieser Prozess, keine anderen. Lab-ISOs und Node-Listen füllt `catalog-sync`.

### Voraussetzungen auf OTOBO

Das Setup legt Status, Queue, Felder, Webservice und Prozess an. Folgendes muss **vorher** auf der OTOBO-Box existieren (Werft spielt das nicht ein):

| Abhängigkeit | Prüfung |
|---|---|
| SSH-Zugang, Key in Werft-Einstellungen, `sudo -u otobo` ohne Passwort | `ssh … sudo -u otobo /opt/otobo/bin/otobo.Console.pl Maint::Config::Rebuild` (Pfad ggf. `OTOBO_HOME`) |
| Paket **Znuny4OTOBO-DynamicFieldScreen** | Admin → Paketverwaltung. Console muss `Znuny4OTOBO::DynamicFieldScreen::Add` kennen. Ohne das Paket schlägt `deploy_otobo_vm_process.py` fehl. |
| Daemon | Setup startet `otobo.Daemon.pl` bei Bedarf (nicht im Dry-Run). Nach Reboot ggf. systemd. |
| Process Management + GenericInterface (HTTP::REST) | Standard-OTOBO; sonst kein Prozessticket und kein Webservice. |

Katalog-Dropdowns (Node, Vorlage, Datastore) füllt erst `catalog-sync` bzw. der Setup-Lauf ohne „Katalog überspringen“. Bis dahin sind die Felder leer, der Prozess existiert trotzdem.

---

## Rollen

| Richtung | OTOBO-Rolle | Zweck |
|---|---|---|
| Middleware → OTOBO | **Provider** | Kommentar + Status nach Provisioning |
| OTOBO → Middleware | **Requester** | Nach Freigabe Webhook an Middleware |

Webservice-Name (Beispiel): `REST-API`  
Middleware-URL für den Requester: `http://192.0.2.30:8000` (weiterhin gültig, nginx mappt 8000→Webhook) oder `https://192.0.2.30` (selbstsigniert — SSL-Prüfung am Invoker ggf. aus). Route bleibt `/api/v1/provision-vm`. **Kein** Basic Auth auf diesem Pfad, nur `X-Api-Key`.

---

## 1. Provider (Rückweg)

1. Admin → Web Services → Webservice anlegen (`REST-API`, gültig, Debug optional).
2. **OTOBO als Provider** → Transport `HTTP::REST` → Konfigurieren:
   - Maximale Nachrichtenlänge: `10000000`
   - Keep-Alive: Nein
3. Operationen:
   - `SessionCreate` → Backend `Session::SessionCreate`
   - `TicketUpdate` → Backend `Ticket::TicketUpdate`
   - `TicketGet` → Backend `Ticket::TicketGet`
4. Wieder Transport konfigurieren → Route-Mapping:

| Operation | Route | Methode |
|---|---|---|
| SessionCreate | `/SessionCreate` | POST |
| TicketUpdate | `/TicketUpdate` | POST |
| TicketGet | `/TicketGet` | POST |

5. Webservice speichern.

Endpoints:

```text
POST {OTOBO}/otobo/nph-genericinterface.pl/Webservice/REST-API/SessionCreate
POST {OTOBO}/otobo/nph-genericinterface.pl/Webservice/REST-API/TicketUpdate
```

Middleware-Env:

```env
OTOBO_URL=http://192.0.2.20
OTOBO_WEBSERVICE_NAME=REST-API
OTOBO_USER_LOGIN=...
OTOBO_PASSWORD=...
```

---

## 2. Requester (Trigger nach Genehmigung)

1. Im selben Webservice: **OTOBO als Requester** → Transport `HTTP::REST`:
   - Endpunkt: `http://<werft-host>:8000` oder `https://<werft-host>` (ohne Slash, ohne `/api/...`)
   - Timeout: `120`
   - Standardbefehl: **POST**
   - Zusätzlicher Header: `X-Api-Key` = Wert aus Middleware `WEBHOOK_API_KEY`
2. Invoker hinzufügen:
   - Name: `ProvisionVM`
   - Backend: `Generic::PassThrough` (reicht; TicketGet muss nicht existieren)
   - Mappings: leer lassen (Mapping kommt später über Middleware/Felder)
3. Event-Auslöser:
   - Objekt: `Ticket`
   - Event: `TicketStateUpdate`
   - Asynchron: **Ja** (Daemon muss laufen)
4. Requester-Transport erneut konfigurieren → Invoker-Route:

| Invoker | Controller / Route | Methode |
|---|---|---|
| ProvisionVM | `/api/v1/provision-vm` | POST |

5. Webservice speichern.

Webhook-Key in der Werft-UI unter **Einstellungen** erzeugen und am Invoker als Header `X-Api-Key` eintragen.

---

## 3. Pflicht: Status-Bedingung

Ohne Bedingung feuert der Invoker bei **jedem** Statuswechsel.

1. In Invoker `ProvisionVM` bei Event `TicketStateUpdate` → Spalte **Bedingung** bearbeiten.
2. Bedingung: Ticket-Feld **`State`** (englisch, nicht „Status“/„Stata“) gleich `Genehmigt` — ohne Leerzeichen.

Empfehlung: eigenen Status anlegen, z. B. `Genehmigt` (Typ: `open` oder `pending reminder`), und nur diesen verwenden.

Standard-Status (ohne Extra-Status) z. B.:

- new, open, closed successful, …

**Nicht** `ArticleEdit` als Event hinzufügen.

---

## 4. Formular / Dynamische Felder

Die VM-Felder gehören **nicht** auf die globale Maske „Neues Telefon-Ticket“. Sie hängen am Prozess **VM Provisioning**.

| Dynamisches Feld | UI | Bedeutung |
|---|---|---|
| `VMHostname` | Text | Hostname |
| `VMHypervisor` | Dropdown | `proxmox` / `vmware` |
| `VMOS` | Dropdown | `linux` / `windows` / `other` |
| `VMTemplate` | Dropdown | Katalog-ID (`proxmox:template:…`, `proxmox:iso:…`, `vmware:iso:…`, `vmware:template:…`) |
| `VMCpu` / `VMRamMB` / `VMDiskGB` | Text | Ressourcen |
| `VMSubnet` | Text | IPAM-Prefix CIDR (NetBox/Nautobot), z. B. `192.0.2.0/24` |
| Node / Host | Dropdown | Proxmox-Nodes bzw. VMware-Hosts (ACL filtert nach Hypervisor) |
| `VMDatastore` | Dropdown | Proxmox-Storage bzw. VMware-Datastore (ACL filtert nach Hypervisor) |
| weitere | optional | Gateway, VLAN, … |

Prozess und Masken einmalig auf OTOBO ausrollen:

```bash
cd /opt/werft && .venv/bin/python scripts/install_otobo_setup.py
# oder nur Prozess/Masken, wenn Webservice schon steht:
cd /opt/werft && .venv/bin/python scripts/deploy_otobo_vm_process.py
```

Das nimmt die VM-Felder von `AgentTicketPhone` / Email / FreeText, lässt sie auf Zoom + Prozess-Widget, importiert [`otobo/process-vm-provisioning.yml`](../otobo/process-vm-provisioning.yml) und deployed `ZZZProcessManagement.pm`.

Agenten: **Tickets → Neues Prozessticket → VM Provisioning**. Normale Tickets bleiben **Neues Telefon-Ticket**.

Katalog synchronisieren (füllt Vorlage, Node und Datastore aus Proxmox/VMware):

Der Compose-Dienst `catalog-sync` macht das automatisch alle 15 Minuten. Manuell (sofort, z. B. nach neuem ISO):

```bash
cd /opt/werft && .venv/bin/python scripts/sync_otobo_catalog.py
```

SSH und Katalog-Intervall stehen in der Ops-UI (Vault). SSH-Host leer lassen, dann gilt der Host aus der OTOBO-URL. Nodes/ISOs kommen live von Proxmox und ESXi/vCenter. Der Sync schreibt auch die ACLs `200-VM-Hypervisor-Proxmox` / `201-VM-Hypervisor-VMware` (Node + Vorlage abhängig vom Hypervisor).

API-Liste (Ops-Login): `GET /api/v1/catalog/images/public?hypervisor=proxmox` bzw. `?hypervisor=vmware`. Ohne Session: dieselben Pfade ohne `/public` plus Webhook-`X-Api-Key`.

**Wichtig:** Für Automatisierung bevorzugt **Cloud-Init-Templates** (Proxmox/vCenter-Clone). ISO-Auswahl (Proxmox und standalone ESXi) erzeugt nur eine VM mit eingelegtem Medium — die OS-Installation ist dann manuell/Autoinstall, nicht vollautomatisch.

Standalone ESXi und mehrere Hosts: in der Ops-UI als Verbindungen (vCenter / ESXi), nicht in `.env`.

Proxmox: ein Cluster = eine API-Adresse + ein Token in der GUI. Unabhängige PVE-Boxen = je eine Verbindung „Einzelner Node“.

Rückmeldungs-Status (müssen in OTOBO existieren; Namen in der GUI unter OTOBO → Status):

- Provisioning
- closed successful
- Failed

---

## 5. Daemon

Asynchrone Invoker brauchen den OTOBO-Daemon. Das Setup **prüft** den Status und **startet** ihn bei Bedarf (Dry-Run nur „WOULD start“):

```bash
sudo -u otobo /opt/otobo/bin/otobo.Daemon.pl status
sudo -u otobo /opt/otobo/bin/otobo.Daemon.pl start
```

Nach Reboot dauerhaft halten (systemd-Unit auf der OTOBO-Box) — sonst muss Setup/Admin ihn erneut starten.

---

## 6. Smoke-Tests

Provider:

```bash
curl -sS -X POST \
  "$OTOBO_URL/otobo/nph-genericinterface.pl/Webservice/REST-API/SessionCreate" \
  -H 'Content-Type: application/json' \
  -d '{"UserLogin":"...","Password":"..."}'
```

Middleware erreichbar von OTOBO:

```bash
curl -sS -k https://<werft-host>/healthz
```

Webhook-Auth (erwartet 422 ohne volles Payload, aber **nicht** 401):

```bash
curl -sS -k -X POST https://<werft-host>/api/v1/provision-vm \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: <webhook-key-aus-der-gui>' \
  -d '{"ticket_id":"test"}'
```

---

## Checkliste

- [ ] Provider: SessionCreate + TicketUpdate + Routes
- [ ] Requester: Host, POST, X-Api-Key, Invoker ProvisionVM, Route `/api/v1/provision-vm`
- [ ] Event TicketStateUpdate (asynchron)
- [ ] **Bedingung nur Genehmigt-Status**
- [ ] SSH + `sudo -u otobo` von Werft zur OTOBO-Box
- [ ] Paket **Znuny4OTOBO-DynamicFieldScreen** installiert
- [ ] Setup-Skript `scripts/install_otobo_setup.py` **oder** GUI-Tab OTOBO-Setup
- [ ] Dynamische Felder **nicht** auf AgentTicketPhone; Prozess **VM Provisioning** deployed
- [ ] `catalog-sync` läuft bzw. `scripts/sync_otobo_catalog.py` einmal ausgeführt
- [ ] OTOBO-Daemon läuft (Setup startet ihn bei Bedarf; nach Reboot systemd prüfen)
- [ ] Middleware API + Worker + nginx (`https://…/` Ops-UI mit Login, Webhook ohne Session)
