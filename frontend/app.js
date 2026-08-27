const $ = (id) => document.getElementById(id);

const SECRET_FIELDS = new Set([
  "webhook_api_key",
  "netbox_token",
  "nautobot_token",
  "otobo_password",
]);

const SECRET_PLACEHOLDER = "bereits hinterlegt — leer lassen";

const TEXT_FIELDS = [
  "webhook_allow_from",
  "otobo_url",
  "otobo_user_login",
  "otobo_webservice_name",
  "otobo_status_provisioning",
  "otobo_status_done",
  "otobo_status_failed",
  "otobo_ssh_host",
  "otobo_ssh_user",
  "otobo_ssh_key",
  "otobo_home",
  "otobo_os_user",
  "netbox_url",
  "nautobot_url",
  "ipam_provider",
];

const BOOL_FIELDS = ["otobo_verify_ssl", "netbox_verify_ssl", "nautobot_verify_ssl"];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("de-DE");
}

function fmtDuration(start, end, status) {
  if (!start) return "";
  const a = new Date(start).getTime();
  const running = status === "queued" || status === "ip_reserved" || status === "provisioning";
  const b = !running && end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return "";
  const s = Math.round((b - a) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function show(id, on) {
  $(id).classList.toggle("is-hidden", !on);
}

function setError(id, message) {
  const el = $(id);
  if (!message) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = message;
}

function error_text(data, fallback) {
  const detail = data && data.detail;
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return String(detail);
}

let uiPhase = "boot";

function showPhase(phase) {
  uiPhase = phase;
  show("gate", phase === "login");
  show("pw-gate", phase === "pw");
  show("app", phase === "app");
  if (phase === "login") refreshLoginHint();
}

async function refreshLoginHint() {
  const hint = $("login-hint");
  if (!hint) return;
  hint.textContent = "Anmelden.";
  try {
    const res = await fetch("/api/v1/auth/bootstrap", { credentials: "include" });
    if (!res.ok) return;
    const data = await res.json();
    if (data.default_credentials) {
      hint.innerHTML =
        "Erstes Login: Benutzer <code>admin</code>, Passwort <code>changeme</code> — danach sofort ändern.";
      const user = $("login-user");
      if (user && !user.value.trim()) user.value = "admin";
    }
  } catch {
    /* keep generic hint */
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "include", ...options });
  if (res.status === 401) {
    if (uiPhase !== "pw" && uiPhase !== "login") {
      showPhase("login");
    }
    throw new Error("Nicht angemeldet");
  }
  if (res.status === 403) {
    const body = await res.json().catch(() => ({}));
    if (String(body.detail || "").includes("password change")) {
      showPhase("pw");
      throw new Error("Passwortänderung nötig");
    }
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res;
}

function healthCard(name, component) {
  const status = component?.status || (component?.ok ? "ok" : "error");
  const value = status === "ok" ? "ok" : status === "skip" ? "—" : "fehler";
  return `<article class="card ${status}">
    <div class="label">${esc(name)}</div>
    <div class="value">${value}</div>
    <div class="muted">${esc(component?.detail || "")}</div>
  </article>`;
}

function ipamCardLabel(data) {
  const fromConfig = String(data?.config?.ipam || "").toLowerCase();
  if (fromConfig === "nautobot") return "Nautobot";
  const detail = String(data?.netbox?.detail || "").toLowerCase();
  if (detail.includes("nautobot")) return "Nautobot";
  return "NetBox";
}

function ticketCell(job) {
  const id = esc(job.ticket_id);
  if (job.ticket_url) {
    return `<a href="${esc(job.ticket_url)}" target="_blank" rel="noopener">${id}</a>`;
  }
  return id;
}

function hostnameCell(job) {
  const ref = job.hypervisor_ref
    ? `<div class="muted">${esc(job.hypervisor_ref)}</div>`
    : "";
  return `${esc(job.hostname || "")}${ref}`;
}

function renderDaemon(component) {
  const state = $("daemon-state");
  const startBtn = $("daemon-start");
  const restartBtn = $("daemon-restart");
  if (!state) return;
  const status = component?.status || (component?.ok ? "ok" : "error");
  const detail = component?.detail || "—";
  const running = status === "ok" && /running/i.test(detail);
  state.textContent = detail;
  state.classList.toggle("is-ok", status === "ok");
  state.classList.toggle("is-bad", status === "error");
  const usable = status !== "skip";
  if (startBtn) {
    startBtn.disabled = !usable || running;
    startBtn.title = running ? "Daemon läuft bereits" : "";
  }
  if (restartBtn) restartBtn.disabled = !usable;
}

async function daemonAction(action) {
  setError("daemon-msg");
  const startBtn = $("daemon-start");
  const restartBtn = $("daemon-restart");
  if (startBtn) startBtn.disabled = true;
  if (restartBtn) restartBtn.disabled = true;
  try {
    const res = await api("/api/v1/ops/otobo-daemon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const data = await res.json();
    renderDaemon({
      ok: Boolean(data.running) || action === "stop",
      status: data.running ? "ok" : action === "stop" ? "error" : "error",
      detail: data.detail || (data.running ? "running" : "not running"),
    });
    await refreshStatus();
  } catch (err) {
    setError("daemon-msg", String(err));
    await refreshStatus().catch(() => {});
  } finally {
    /* buttons restored by renderDaemon via refreshStatus */
  }
}

async function refreshStatus() {
  const data = await (await api("/api/v1/ops/status")).json();
  $("health").innerHTML = [
    healthCard("API", data.api),
    healthCard("Postgres", data.postgres),
    healthCard("Redis", data.redis),
    healthCard("Worker", data.worker),
    healthCard("OTOBO", data.otobo),
    healthCard(ipamCardLabel(data), data.netbox),
    healthCard("Proxmox", data.proxmox),
    healthCard("VMware", data.vmware),
    healthCard("Katalog", data.catalog),
  ].join("");

  renderDaemon(data.otobo_daemon);

  const jobs = data.jobs || {};
  const keys = ["queued", "ip_reserved", "provisioning", "completed", "failed"];
  $("job-counts").innerHTML = keys
    .map((k) => `<span class="pill">${k}: ${jobs[k] || 0}</span>`)
    .join("");

  const rows = (data.recent || []).slice(0, 5);
  $("jobs-body").innerHTML = rows.length
    ? rows
        .map(
          (j) => `<tr>
            <td>${esc(fmtTime(j.updated_at))}</td>
            <td>${esc(fmtDuration(j.created_at, j.updated_at, j.status))}</td>
            <td>${hostnameCell(j)}</td>
            <td>${ticketCell(j)}</td>
            <td>${esc(j.hypervisor || "")}</td>
            <td class="status-${esc(j.status || "")}">${esc(j.status || "")}</td>
            <td>${esc(j.reserved_ip || "")}</td>
            <td>${esc(j.error_message || "")}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="8" class="muted">Noch keine Jobs</td></tr>`;

  const hosts = data.hosts || [];
  $("hosts").innerHTML = hosts.length
    ? hosts
        .map(
          (h) =>
            `<span class="host-chip">${esc(h.label || h.id)} <small>${esc(h.hypervisor)}</small></span>`
        )
        .join("")
    : `<span class="muted">Keine Hosts erreichbar</span>`;

  const config = data.config || {};
  $("config").innerHTML = Object.entries(config)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
    .join("");
}

function secretConfigured(meta) {
  if (meta == null || meta === "") return false;
  if (typeof meta === "string") return meta.trim().length > 0;
  return Boolean(meta.configured);
}

let proxmoxConns = [];
let vmwareConns = [];
let connEditor = { hypervisor: "proxmox", index: -1 };

function cloneConn(row) {
  return { ...(row || {}) };
}

function kindLabel(hypervisor, kind) {
  if (hypervisor === "proxmox") return kind === "host" ? "Node" : "Cluster";
  return kind === "esxi" ? "ESXi" : "vCenter";
}

function connTitle(row) {
  return (row.name || "").trim() || row.host || "ohne Namen";
}

function renderConnTable(boxId, rows, hypervisor) {
  const box = $(boxId);
  if (!rows.length) {
    box.innerHTML = `<p class="conn-empty">Keine Verbindung</p>`;
    return;
  }
  box.innerHTML = rows
    .map((row, index) => {
      const tokenOk =
        hypervisor === "proxmox" ? secretConfigured(row.token_value) : secretConfigured(row.password);
      const badge = kindLabel(hypervisor, row.kind);
      const secretHint = tokenOk ? "" : " · Secret fehlt";
      return `<article class="conn-row" data-index="${index}">
        <div class="conn-row-main">
          <strong>${esc(connTitle(row))}</strong>
          <span>${esc(badge)} · ${esc(row.host || "—")}${secretHint}</span>
        </div>
        <div class="conn-row-actions">
          <button type="button" class="text-btn conn-edit">Bearbeiten</button>
          <button type="button" class="text-btn conn-remove">Entfernen</button>
        </div>
      </article>`;
    })
    .join("");
}

function renderProxmox() {
  renderConnTable("proxmox-endpoints", proxmoxConns, "proxmox");
}

function renderVmware() {
  renderConnTable("vmware-endpoints", vmwareConns, "vmware");
}

function selectedKind() {
  const picked = document.querySelector('#conn-kind-box input[name="conn-kind"]:checked');
  return picked ? picked.value : "";
}

function setKindOptions(hypervisor, kind) {
  const box = $("conn-kind-box");
  box.querySelectorAll("label").forEach((label) => {
    const value = label.querySelector("input").value;
    const proxmox = value === "cluster" || value === "host";
    label.classList.toggle("is-hidden", hypervisor === "proxmox" ? !proxmox : proxmox);
  });
  const wanted = kind || (hypervisor === "proxmox" ? "cluster" : "vcenter");
  box.querySelectorAll("input").forEach((input) => {
    input.checked = input.value === wanted;
  });
  updateHostHelp(hypervisor);
}

function updateHostHelp(hypervisor) {
  const kind = selectedKind();
  const help = $("conn-host-help");
  if (hypervisor === "proxmox" && kind === "cluster") {
    help.textContent =
      "Eine Adresse für den ganzen Cluster: VIP oder die IP eines beliebigen Nodes. Nicht alle Nodes einzeln.";
  } else if (hypervisor === "proxmox") {
    help.textContent = "IP oder Hostname genau dieses Standalone-Hosts. Eigenes API-Token.";
  } else if (kind === "esxi") {
    help.textContent = "IP oder Hostname dieses ESXi-Hosts.";
  } else {
    help.textContent = "vCenter-Adresse. Die ESXi-Hosts kommen aus dem Inventar ins Ticket.";
  }
}

function openConnModal(hypervisor, index) {
  connEditor = { hypervisor, index };
  const isProxmox = hypervisor === "proxmox";
  const rows = isProxmox ? proxmoxConns : vmwareConns;
  const row = index >= 0 ? rows[index] : {};
  $("conn-title").textContent = index >= 0 ? "Verbindung bearbeiten" : "Verbindung hinzufügen";
  $("conn-lead").textContent = isProxmox
    ? "Cluster oder einzelner Node — jeweils eine API-Adresse und ein Token."
    : "vCenter oder einzelner ESXi — jeweils eine API-Adresse und ein Login.";
  setKindOptions(hypervisor, row.kind);
  $("conn-name").value = row.name || "";
  $("conn-host").value = row.host || "";
  $("conn-user-wrap").classList.toggle("is-hidden", isProxmox);
  $("conn-user").value = row.user || "";
  $("conn-token-name").value = row.token_name || "";
  $("conn-token-value").value = "";
  const tokenSet = secretConfigured(row.token_value);
  $("conn-token-value").placeholder = tokenSet ? SECRET_PLACEHOLDER : "";
  $("conn-token-help").textContent = tokenSet
    ? "Secret ist gespeichert. Leer lassen zum Behalten, nur ausfüllen zum Ändern."
    : "Secret aus Proxmox (Datacenter → Permissions → API Tokens).";
  $("conn-password").value = "";
  $("conn-password").placeholder = secretConfigured(row.password) ? SECRET_PLACEHOLDER : "";
  $("conn-tls").checked = row.verify_ssl !== false;
  $("conn-proxmox-auth").classList.toggle("is-hidden", !isProxmox);
  $("conn-vmware-auth").classList.toggle("is-hidden", isProxmox);
  setError("conn-error");
  $("conn-modal").classList.remove("is-hidden");
  $("conn-name").focus();
}

function closeConnModal() {
  $("conn-modal").classList.add("is-hidden");
}

function applyConnModal(event) {
  event.preventDefault();
  const hypervisor = connEditor.hypervisor;
  const host = $("conn-host").value.trim();
  if (!host) {
    setError("conn-error", "API-IP oder Hostname fehlt");
    return;
  }
  const isProxmox = hypervisor === "proxmox";
  const rows = isProxmox ? proxmoxConns : vmwareConns;
  const existing = connEditor.index >= 0 ? rows[connEditor.index] : {};
  const row = {
    ...existing,
    name: $("conn-name").value.trim(),
    host,
    kind: selectedKind() || (isProxmox ? "cluster" : "vcenter"),
    user: isProxmox ? "" : $("conn-user").value.trim(),
    verify_ssl: $("conn-tls").checked,
    previous_host: existing.host || "",
  };
  if (isProxmox) {
    row.token_name = $("conn-token-name").value.trim();
    if (row.token_name.includes("!")) {
      row.user = row.token_name.split("!")[0].trim();
    }
    const secret = $("conn-token-value").value;
    if (secret) row.token_value = secret;
    else if (!secretConfigured(existing.token_value)) {
      setError("conn-error", "Token-Secret fehlt");
      return;
    }
  } else {
    const secret = $("conn-password").value;
    if (secret) row.password = secret;
    else if (!secretConfigured(existing.password) && connEditor.index < 0) {
      setError("conn-error", "Passwort fehlt");
      return;
    }
  }
  if (connEditor.index >= 0) rows[connEditor.index] = row;
  else rows.push(row);
  if (isProxmox) renderProxmox();
  else renderVmware();
  closeConnModal();
}

function bindConnTable(boxId, hypervisor) {
  const box = $(boxId);
  if (box.dataset.bound) return;
  box.dataset.bound = "1";
  box.addEventListener("click", (event) => {
    const rowEl = event.target.closest(".conn-row");
    if (!rowEl || !box.contains(rowEl)) return;
    const index = Number(rowEl.dataset.index);
    if (event.target.closest(".conn-edit")) {
      openConnModal(hypervisor, index);
      return;
    }
    if (event.target.closest(".conn-remove")) {
      const rows = hypervisor === "proxmox" ? proxmoxConns : vmwareConns;
      rows.splice(index, 1);
      if (hypervisor === "proxmox") renderProxmox();
      else renderVmware();
    }
  });
}

function payloadConns(rows, hypervisor) {
  return rows
    .filter((row) => (row.host || "").trim())
    .map((row) => {
      const out = {
        name: row.name || "",
        host: row.host,
        kind: row.kind,
        user: row.user || "",
        verify_ssl: Boolean(row.verify_ssl),
        previous_host: row.previous_host || row.host,
      };
      if (hypervisor === "proxmox") {
        out.token_name = row.token_name || "";
        if (typeof row.token_value === "string" && row.token_value) out.token_value = row.token_value;
      } else if (typeof row.password === "string" && row.password) {
        out.password = row.password;
      }
      return out;
    });
}

function fillSettings(settings) {
  for (const name of TEXT_FIELDS) {
    const el = $(`s-${name}`);
    if (el) el.value = settings[name] ?? "";
  }
  const port = $("s-otobo_ssh_port");
  if (port) port.value = settings.otobo_ssh_port ?? 22;
  for (const name of BOOL_FIELDS) {
    const el = $(`s-${name}`);
    if (el) el.checked = Boolean(settings[name]);
  }
  for (const name of SECRET_FIELDS) {
    const el = $(`s-${name}`);
    if (!el) continue;
    const meta = settings[name] || {};
    el.value = "";
    el.placeholder = meta.configured ? SECRET_PLACEHOLDER : "";
  }
  const webhookState = $("webhook-key-state");
  if (webhookState) {
    webhookState.textContent = secretConfigured(settings.webhook_api_key)
      ? "Key hinterlegt — leer lassen."
      : "Kein Key — OTOBO-Setup legt einen an.";
  }
  proxmoxConns = (settings.proxmox_endpoints || []).map(cloneConn);
  vmwareConns = (settings.vmware_endpoints || []).map(cloneConn);
  bindConnTable("proxmox-endpoints", "proxmox");
  bindConnTable("vmware-endpoints", "vmware");
  renderProxmox();
  renderVmware();
  updateSshHostHint();
  updateSetupBanner(settings);
  const provider = $("s-ipam_provider");
  if (provider) {
    const value = String(settings.ipam_provider || "netbox").toLowerCase();
    provider.value = value === "nautobot" ? "nautobot" : "netbox";
  }
  syncIpamProviderFields();
}

function activeIpamProvider() {
  const el = $("s-ipam_provider");
  return el && el.value === "nautobot" ? "nautobot" : "netbox";
}

function syncIpamProviderFields() {
  const provider = activeIpamProvider();
  const nb = $("ipam-fields-netbox");
  const nt = $("ipam-fields-nautobot");
  if (nb) nb.classList.toggle("is-hidden", provider !== "netbox");
  if (nt) nt.classList.toggle("is-hidden", provider !== "nautobot");
}

function ipamGuideReq() {
  return activeIpamProvider() === "nautobot"
    ? ["s-nautobot_url", "s-nautobot_token"]
    : ["s-netbox_url", "s-netbox_token"];
}


async function loadSettings() {
  const data = await (await api("/api/v1/ops/settings")).json();
  fillSettings(data.settings || {});
}

function otoboUrlHost() {
  try {
    const raw = $("s-otobo_url").value.trim();
    if (!raw) return "";
    return new URL(raw).hostname || "";
  } catch {
    return "";
  }
}

function updateSshHostHint() {
  const hint = $("ssh-host-hint");
  if (!hint) return;
  const host = $("s-otobo_ssh_host").value.trim();
  const fromUrl = otoboUrlHost();
  if (host) {
    hint.textContent = "Nur wenn SSH anders erreichbar ist als die OTOBO-URL.";
    return;
  }
  if (fromUrl) {
    hint.textContent = `Leer = ${fromUrl} aus der OTOBO-URL.`;
    return;
  }
  hint.textContent = "Leer = Host aus der OTOBO-URL.";
}

function updateSetupBanner(settings) {
  const banner = $("setup-banner");
  if (!banner) return;
  const url = settings
    ? String(settings.otobo_url || "").trim()
    : $("s-otobo_url").value.trim();
  banner.classList.toggle("is-hidden", Boolean(url));
}

const GUIDE_STEPS = [
  {
    title: "Webhook",
    skippable: false,
    targets: ["fs-webhook"],
    body:
      "Key fehlt: legt das OTOBO-Setup an. Schon da: Feld leer lassen. Erlaubte IPs = OTOBO-IP.",
  },
  {
    title: "OTOBO",
    skippable: false,
    targets: ["fs-otobo"],
    req: ["s-otobo_url", "s-otobo_user_login", "s-otobo_password"],
    body: "URL, Login und Passwort (leer lassen, wenn schon hinterlegt).",
  },
  {
    title: "SSH",
    skippable: true,
    targets: ["fs-ssh"],
    openDetails: "fs-ssh",
    req: ["s-otobo_ssh_user", "s-otobo_ssh_key", "s-otobo_home", "s-otobo_os_user"],
    body:
      "Für das OTOBO-Setup: SSH zur OTOBO-VM. Key-Pfad = privater Key auf Werft. Host leer = aus der OTOBO-URL.",
  },
  {
    title: "IPAM",
    skippable: true,
    targets: ["fs-netbox"],
    reqFrom: "ipam",
    body: "NetBox oder Nautobot wählen — URL und Token gelten nur für diesen Provider (getrennt gespeichert). Oder überspringen.",
  },
  {
    title: "Hypervisor",
    skippable: true,
    targets: ["fs-proxmox", "fs-vmware"],
    body: "Mindestens Proxmox oder VMware. Cluster: eine API, nicht jeden Node.",
  },
  {
    title: "Speichern",
    skippable: false,
    save: true,
    targets: ["settings-save"],
    body: "Speichern, dann Tab OTOBO-Setup — zuerst Dry-Run.",
  },
];

let guideIndex = -1;
let guideSavedOtobo = false;

function guideActive() {
  return guideIndex >= 0;
}

function clearGuideFocus() {
  document.querySelectorAll(".guide-focus").forEach((el) => el.classList.remove("guide-focus"));
  document.querySelectorAll("label.is-required").forEach((el) => el.classList.remove("is-required"));
}

function markGuideRequired(step) {
  const req = step.reqFrom === "ipam" ? ipamGuideReq() : step.req || [];
  req.forEach((id) => {
    const input = $(id);
    if (!input) return;
    const label = input.closest("label");
    if (!label || label.classList.contains("check")) return;
    label.classList.add("is-required");
  });
}

function closeGuide() {
  guideIndex = -1;
  clearGuideFocus();
  const dock = $("guide-dock");
  if (dock) dock.classList.add("is-hidden");
  setError("guide-warn");
}

function renderGuide() {
  const step = GUIDE_STEPS[guideIndex];
  if (!step) {
    closeGuide();
    return;
  }
  $("guide-dock").classList.remove("is-hidden");
  $("guide-progress").textContent = `Schritt ${guideIndex + 1} von ${GUIDE_STEPS.length}`;
  $("guide-title").textContent = step.title;
  $("guide-body").textContent = step.body;
  setError("guide-warn");
  $("guide-back").disabled = guideIndex === 0;
  $("guide-skip").classList.toggle("is-hidden", !step.skippable);
  $("guide-next").disabled = false;
  $("guide-next").textContent = step.save ? "Speichern und zu OTOBO-Setup" : "Weiter";
  clearGuideFocus();
  const ssh = $("fs-ssh");
  if (ssh) ssh.open = step.openDetails === "fs-ssh";
  markGuideRequired(step);
  (step.targets || []).forEach((id) => {
    const el = $(id);
    if (el) el.classList.add("guide-focus");
  });
  const first = $(step.targets[0]);
  if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
}

function beginGuide() {
  guideIndex = 0;
  guideSavedOtobo = Boolean($("s-otobo_url").value.trim());
  renderGuide();
}

function openGuide() {
  const onSettings = !$("panel-settings").classList.contains("is-hidden");
  if (onSettings) {
    beginGuide();
    return;
  }
  setTab("settings", { openGuide: true });
}

function finishGuideToSetup() {
  closeGuide();
  $("dry-run").checked = true;
  setTab("setup", { fromGuide: true });
}

async function saveSettings() {
  const msg = $("settings-msg");
  msg.hidden = true;
  await api("/api/v1/ops/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: collectSettings() }),
  });
  await loadSettings();
  msg.hidden = false;
  msg.textContent = "Gespeichert.";
  guideSavedOtobo = Boolean($("s-otobo_url").value.trim());
}

async function guideNext() {
  const step = GUIDE_STEPS[guideIndex];
  if (!step) return;
  if (step.req || step.reqFrom) {
    const req = step.reqFrom === "ipam" ? ipamGuideReq() : step.req || [];
    const missing = req.filter((id) => {
      const el = $(id);
      if (!el) return false;
      if (el.placeholder === SECRET_PLACEHOLDER) return false;
      return !el.value.trim();
    });
    if (missing.length) {
      setError(
        "guide-warn",
        step.skippable
          ? "Felder mit * ausfüllen oder überspringen."
          : "Bitte die Felder mit * ausfüllen (Passwort nur, wenn noch keins hinterlegt ist).",
      );
      return;
    }
  }
  const next = $("guide-next");
  if (step.save) {
    next.disabled = true;
    try {
      await saveSettings();
    } catch (err) {
      setError("guide-warn", String(err));
      next.disabled = false;
      return;
    }
    next.disabled = false;
    if (!$("s-otobo_url").value.trim()) {
      setError("guide-warn", "OTOBO-URL speichern, sonst kann das Setup nicht per SSH verbinden.");
      return;
    }
    finishGuideToSetup();
    return;
  }
  if (guideIndex < GUIDE_STEPS.length - 1) {
    guideIndex += 1;
    renderGuide();
  }
}

function guideSkip() {
  const step = GUIDE_STEPS[guideIndex];
  if (!step || !step.skippable) return;
  if (guideIndex < GUIDE_STEPS.length - 1) {
    guideIndex += 1;
    renderGuide();
  }
}

function guideBack() {
  if (guideIndex > 0) {
    guideIndex -= 1;
    renderGuide();
  }
}

function collectSettings() {
  const values = {};
  for (const name of TEXT_FIELDS) {
    const el = $(`s-${name}`);
    if (el) values[name] = el.value.trim();
  }
  values.otobo_ssh_port = Number($("s-otobo_ssh_port").value || 22);
  const provider = $("s-ipam_provider");
  if (provider) values.ipam_provider = provider.value.trim() || "netbox";
  for (const name of BOOL_FIELDS) {
    values[name] = $(`s-${name}`).checked;
  }
  for (const name of SECRET_FIELDS) {
    const el = $(`s-${name}`);
    if (el && el.value) values[name] = el.value;
  }
  values.proxmox_endpoints = payloadConns(proxmoxConns, "proxmox");
  values.vmware_endpoints = payloadConns(vmwareConns, "vmware");
  return values;
}

function setTab(name, opts = {}) {
  if (name !== "settings" && !opts.fromGuide) closeGuide();
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.tab === name);
  });
  $("panel-status").classList.toggle("is-hidden", name !== "status");
  $("panel-settings").classList.toggle("is-hidden", name !== "settings");
  $("panel-setup").classList.toggle("is-hidden", name !== "setup");
  if (name === "settings") {
    const pending = loadSettings().catch(() => {});
    if (opts.openGuide) pending.then(() => beginGuide());
    return pending;
  }
  return Promise.resolve();
}

function defaultMiddlewareUrl() {
  // OTOBO ruft den Webhook typischerweise über Port 8000 (HTTP) an, nicht die HTTPS-Ops-UI.
  return `http://${window.location.hostname}:8000`;
}

async function runSetup(event) {
  event.preventDefault();
  const btn = $("setup-run");
  const log = $("setup-log");
  btn.disabled = true;
  log.textContent = "";
  const body = {
    confirm: "setup",
    dry_run: $("dry-run").checked,
    middleware_url: defaultMiddlewareUrl(),
    webservice_name: $("s-otobo_webservice_name").value.trim() || "REST-API",
    skip_catalog_sync: $("skip-catalog").checked,
  };
  try {
    const res = await fetch("/api/v1/ops/setup", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401 || res.status === 403) {
      await api("/api/v1/auth/me");
      return;
    }
    if (!res.ok || !res.body) {
      log.textContent = await res.text();
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";
      for (const chunk of parts) {
        const line = chunk
          .split("\n")
          .filter((l) => l.startsWith("data: "))
          .map((l) => l.slice(6))
          .join("");
        if (!line) continue;
        try {
          const msg = JSON.parse(line);
          if (msg.line !== undefined) log.textContent += msg.line + "\n";
          else if (msg.exit_code !== undefined) log.textContent += `\nexit ${msg.exit_code}\n`;
        } catch {
          log.textContent += line + "\n";
        }
        log.scrollTop = log.scrollHeight;
      }
    }
  } catch (err) {
    log.textContent += String(err);
  } finally {
    btn.disabled = false;
  }
}

function enterApp() {
  showPhase("app");
  refreshStatus().catch(() => {});
}

async function boot() {
  try {
    const res = await fetch("/api/v1/auth/me", { credentials: "include" });
    if (uiPhase !== "boot") return;
    if (res.status === 401) {
      showPhase("login");
      return;
    }
    if (!res.ok) {
      showPhase("login");
      return;
    }
    const me = await res.json();
    if (uiPhase !== "boot") return;
    if (me.must_change_password) {
      showPhase("pw");
      return;
    }
    enterApp();
  } catch {
    if (uiPhase === "boot") showPhase("login");
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("login-error");
  const btn = $("login-submit");
  if (btn) btn.disabled = true;
  uiPhase = "login";
  try {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("login-user").value.trim(),
        password: $("login-pass").value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError("login-error", error_text(data, "Anmeldung fehlgeschlagen"));
      return;
    }
    $("login-pass").value = "";
    if (data.must_change_password) {
      showPhase("pw");
      return;
    }
    enterApp();
  } catch (err) {
    setError("login-error", String(err));
  } finally {
    if (btn) btn.disabled = false;
  }
});

$("pw-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("pw-error");
  try {
    const res = await fetch("/api/v1/auth/password", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_password: $("pw-new").value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError("pw-error", error_text(data, "Speichern fehlgeschlagen"));
      return;
    }
    $("pw-new").value = "";
    enterApp();
  } catch (err) {
    setError("pw-error", String(err));
  }
});

$("logout").addEventListener("click", async () => {
  await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
  showPhase("login");
});

$("daemon-start")?.addEventListener("click", () => daemonAction("start"));
$("daemon-restart")?.addEventListener("click", () => daemonAction("restart"));

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveSettings();
  } catch (err) {
    const msg = $("settings-msg");
    msg.hidden = false;
    msg.textContent = String(err);
  }
});

$("add-proxmox").addEventListener("click", () => openConnModal("proxmox", -1));
$("add-vmware").addEventListener("click", () => openConnModal("vmware", -1));
$("conn-cancel").addEventListener("click", closeConnModal);
$("conn-form").addEventListener("submit", applyConnModal);
let modalDownOnBackdrop = false;
$("conn-modal").addEventListener("pointerdown", (event) => {
  modalDownOnBackdrop = event.target.id === "conn-modal";
});
$("conn-modal").addEventListener("click", (event) => {
  if (modalDownOnBackdrop && event.target.id === "conn-modal") closeConnModal();
  modalDownOnBackdrop = false;
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!$("conn-modal").classList.contains("is-hidden")) {
    closeConnModal();
    return;
  }
  if (guideActive()) closeGuide();
});
$("conn-kind-box").addEventListener("change", () => updateHostHelp(connEditor.hypervisor));

$("rotate-webhook").addEventListener("click", async () => {
  const once = $("webhook-once");
  try {
    const data = await (
      await api("/api/v1/ops/settings/webhook-key", { method: "POST" })
    ).json();
    fillSettings(data.settings || {});
    once.hidden = false;
    once.textContent = `Neuer Key (einmalig): ${data.webhook_api_key}`;
  } catch (err) {
    once.hidden = false;
    once.textContent = String(err);
  }
});

$("s-otobo_url").addEventListener("input", updateSshHostHint);
$("s-otobo_ssh_host").addEventListener("input", updateSshHostHint);
$("s-ipam_provider")?.addEventListener("change", () => {
  syncIpamProviderFields();
  if (guideActive()) renderGuide();
});
$("start-guide").addEventListener("click", () => openGuide());
$("banner-guide").addEventListener("click", () => openGuide());
$("setup-to-guide").addEventListener("click", () => openGuide());
$("guide-next").addEventListener("click", () => guideNext());
$("guide-back").addEventListener("click", guideBack);
$("guide-skip").addEventListener("click", guideSkip);
$("guide-close").addEventListener("click", closeGuide);

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});
$("setup-form").addEventListener("submit", runSetup);

boot();
setInterval(() => {
  if (!$("app").classList.contains("is-hidden") && !$("panel-status").classList.contains("is-hidden")) {
    refreshStatus().catch(() => {});
  }
}, 10000);
