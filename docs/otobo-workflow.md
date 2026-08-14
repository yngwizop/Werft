# OTOBO-Workflow: VM beantragen und genehmigen

Für Agenten. Technische Webservice-Einrichtung steht in [otobo-setup.md](otobo-setup.md).

**Normale Tickets** und **VM-Anfragen** sind getrennt:

| Ziel | Menü |
|---|---|
| Normales Ticket (ohne VM-Felder) | **Tickets → Neues Telefon-Ticket** |
| VM anlegen | **Tickets → Neues Prozessticket → VM Provisioning** |

Freigabe ist der Prozess-Button **Genehmigen** (nicht „Schließen“).

```text
Neues Prozessticket → VM Provisioning (Felder füllen)
        │
        ▼
Prozessaktion „Genehmigen“
        │
        ▼
Middleware: IP in NetBox → VM auf Proxmox/VMware
        │
        ├── Erfolg → Kommentar mit IP, Status „closed successful“
        └── Fehler → Kommentar mit Fehler, Status „Failed“
```

---

## 1. VM-Ticket anlegen

1. Oben **Tickets → Neues Prozessticket**.
2. Prozess **VM Provisioning** wählen.
3. Pflicht:
   - Kundenbenutzer
   - **Queue:** `VM Provisioning` (ist vorausgefüllt)
   - Betreff / Text
4. VM-Felder ausfüllen:

| Feld | Hinweis |
|---|---|
| Hostname | Kleinbuchstaben, z. B. `testvm` |
| Hypervisor | Proxmox oder VMware |
| OS | Linux / Windows / Other |
| Vorlage | Dropdown aus Katalog: **Template** (Cloud-Init) oder **ISO** |
| CPU / RAM / Disk | Zahlen |
| Subnet (CIDR) | Netz aus NetBox, z. B. `192.0.2.0/24` — keine Host-IP, keine Netzmaske `255.255.255.0` |
| Node / Host | Nach Hypervisor-Wahl nur passende Ziele. Proxmox-Cluster-Nodes stehen als **Cluster …**, Standalone-PVE als **Host …**, ESXi/vCenter analog. |
| Gateway / VLAN | optional |

Hostname muss je Hypervisor eindeutig sein (kein zweites `testvm` auf demselben ESXi/Cluster).

5. **VM-Ticket erstellen**.

Danach bist du in der **Ticket-Zoom**-Ansicht. Wiederfinden: **Tickets → Ansicht nach Queues → VM Provisioning** (oder **Meine Queues**).

Ein normales Anliegen bleibt **Neues Telefon-Ticket** — dort gibt es keine VM-Felder mehr.

---

## 2. Was man (nicht) bearbeitet

| Aktion | Wo |
|---|---|
| Status **Genehmigt** / Provisioning starten | Prozess-Widget **Genehmigen** |
| CPU/RAM/Vorlage ändern | **Verschiedenes** → Freie Felder / **Ticket kategorisieren** (Zoom zeigt die Werte) |
| Kommentar | **Kommunikation** → Notiz |
| Andere Queue | **Verschieben** |
| Ticket an sich nehmen | **Sperren** (nicht zwingend zum Genehmigen) |

**Nicht** klicken für die Freigabe (und auch nicht nach Erfolg nötig):

- Schließen
- Sofort schließen

Das beendet das Ticket, ohne eine VM zu bauen. Nach Erfolg setzt die Middleware den Status selbst auf **closed successful**. Das Prozess-Widget kann bei **In Bereitstellung** stehen bleiben („keine Dialoge“) — das ist nur die Prozessansicht, nicht der Ticket-Status.

Falls das Prozess-Widget fehlt: Status **Genehmigt** geht weiter über **Warten** (Fallback).

---

## 3. Genehmigen (Trigger)

1. Ticket öffnen.
2. Im Prozess-Widget **Genehmigen** (Status ist vorausgefüllt: `Genehmigt`).
3. Falls **Warten bis** erscheint, ein Datum setzen (egal welches; der Webhook braucht nur den Status).
4. Optional eine interne Notiz schreiben.
5. Absenden.

Der OTOBO-Daemon schickt dann den Webhook an die Middleware. Kurz darauf kommen die Artikel **VM Provisioning started** und **completed** / **failed**.

---

## 4. Was danach passiert

| Ticket-Status | Bedeutung |
|---|---|
| `offen` / `new` | Angelegt, noch nicht freigegeben |
| `Genehmigt` | Webhook ausgelöst, Job in der Middleware |
| `Provisioning` | Clone/ISO-Build läuft |
| `closed successful` | VM steht; IP steht im Ticket-Kommentar |
| `Failed` | Fehler; IP in NetBox sollte wieder frei sein |

Kommentar der Middleware im Ticket lesen (neuer Artikel). Job-Status der Middleware: `GET /api/v1/jobs/{job_id}` bzw. Docker-Logs `docker compose logs -f worker`.

---

## 5. Vorlage: Template vs. ISO

- **Proxmox-Template** (Cloud-Init): Clone, IP per Cloud-Init — bevorzugter Automatik-Pfad.
- **ISO (Proxmox oder ESXi):** VM wird angelegt und bootet vom ISO. OS-Installation ist **nicht** vollautomatisch.
- **vCenter-Template:** Clone wie bei Proxmox. Ein nackter ESXi kann nicht klonen — dort nur ISOs aus dem Datastore.

Katalog (Dropdown „Vorlage“ / „Node“) kommt von Proxmox und ESXi/vCenter. Der Docker-Dienst `catalog-sync` aktualisiert OTOBO alle 15 Minuten. Sofort-Sync nur nötig nach neuen ISOs/Nodes:

```bash
cd /opt/werft && .venv/bin/python scripts/sync_otobo_catalog.py
```

---

## 6. Typische Stolperer

| Symptom | Ursache |
|---|---|
| VM-Felder im Telefon-Ticket | Alte Maske; VM nur über **Neues Prozessticket** |
| Prozess „VM Provisioning“ fehlt | Deploy: `python scripts/deploy_otobo_vm_process.py`, dann Prozesse in OTOBO deployen |
| Queue leer / Ticket unsichtbar | Nicht in „Meine Queues“; Queue **VM Provisioning** wählen |
| Kein Webhook | Status nicht **Genehmigt**, oder Daemon steht nicht (`otobo.Daemon.pl start`) |
| 401 an der Middleware | Header `X-Api-Key` am Requester-Webservice |
| NetBox: Prefix not found | Subnet ist kein existierendes CIDR in NetBox |
| Proxmox 403 | API-Token ohne VM.Allocate / Clone / Config / PowerMgmt |
| Hostname abgelehnt | Großbuchstaben oder Leerzeichen; nur `a-z0-9-` |
| Node/ISO passen nicht zum Hypervisor | ACL filtert beim Wechsel; sonst neues Prozessticket / hart neu laden. Nicht als OTOBO-Admin (UserID 1) testen — der umgeht ACLs |
| VMware-Vorlage ist ein Proxmox-ISO | Falsches Dropdown; bei VMware eine `vmware:iso:…`-Vorlage wählen |
| ESXi: „folder is required“ / REST 400 | Veralteter Adapter; aktueller Build spricht ESXi per SOAP |
| Prozess bleibt „In Bereitstellung“ | Normal nach Genehmigen; Erfolg steht im Artikel und Status **closed successful** |
