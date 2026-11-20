/* Dashboard — tabs: Tickets & SLA | Routing rules | Classify. */

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 5000;

let POLICY = null;
let STATUS_OPTIONS = ["open", "in_progress", "pending_customer", "resolved", "closed"];
let TENANT_OPTIONS = ["C-1001", "C-1002"];
let TENANT_NAMES = {}; // C-1001 -> "Infosys"

function tenantLabel(id) {
  const name = TENANT_NAMES[id];
  return name ? `${name} (${id})` : id;
}

async function jget(url) {
  const res = await fetch(url);
  return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
}

async function jpost(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---------- tabs ---------- */

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    for (const name of ["home", "intake", "tickets", "channels", "worklist", "sla", "rules", "sla-rules"]) {
      $("tab-" + name).hidden = btn.dataset.tab !== name;
    }
    if (btn.dataset.tab === "home") loadAnalytics();
    if (btn.dataset.tab === "channels") loadChannels();
  });
});

/* ---------- health + clock ---------- */

async function pollHealth() {
  const dot = $("health-dot");
  const text = $("health-text");
  try {
    const { ok, body } = await jget("/health");
    dot.className = `dot ${ok && body.status === "ok" ? "dot-ok" : "dot-bad"}`;
    text.textContent = ok ? "API healthy" : "API error";
  } catch {
    dot.className = "dot dot-bad";
    text.textContent = "API unreachable";
  }
  $("clock").textContent = new Date().toLocaleTimeString();
}

/* ---------- dynamic options ---------- */

function fillTenantSelect(sel, includeAll) {
  const tenants = [...new Set(TENANT_OPTIONS)].sort();
  const current = sel.value;
  sel.innerHTML = "";
  if (includeAll) {
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "All tenants";
    sel.appendChild(all);
  }
  for (const t of tenants) {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = tenantLabel(t);
    sel.appendChild(o);
  }
  if ([...sel.options].some((o) => o.value === current)) sel.value = current;
}

let homeTenantDefaulted = false;

function rebuildTenantOptions(extra = []) {
  TENANT_OPTIONS = [...new Set([...TENANT_OPTIONS, ...extra])].sort();
  const homeSel = $("home-tenant");
  fillTenantSelect($("db-tenant"), true);
  fillTenantSelect($("rules-tenant"), true);
  fillTenantSelect($("sla-tenant"), true);
  fillTenantSelect($("sla-tenant-filter"), true);
  fillTenantSelect($("intake-tenant"), false);
  fillTenantSelect($("home-tenant"), true);
  fillTenantSelect($("ch-tenant"), true);
  // Support overview is always per-tenant: default to the first tenant once.
  if (homeSel && !homeTenantDefaulted && TENANT_OPTIONS.length) {
    homeTenantDefaulted = true;
    homeSel.value = TENANT_OPTIONS[0];
  }
  const ctxSel = $("ctx-tenant");
  if (ctxSel) {
    const prev = ctxSel.value;
    ctxSel.innerHTML = `<option value="">Select tenant…</option>` +
      TENANT_OPTIONS.map((t) => `<option value="${t}">${esc(tenantLabel(t))} (${t})</option>`).join("");
    ctxSel.value = prev;
  }
}

/* ---------- tickets ---------- */

let statusFilter = null;
let channelFilter = null;

async function loadDashboard() {
  const tenant = $("db-tenant").value;
  const qs = tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : "";
  const { ok, status, body } = await jget(`/v1/dashboard/summary${qs}`);
  LAST_SUMMARY = body;
  if (!ok) {
    $("db-cards").innerHTML = `<div class="card bad"><b>!</b><span>${body.detail || status}</span></div>`;
    return;
  }

  const cards = [`<div class="card${statusFilter === "__all" ? " card-active" : ""}" data-status="__all"><b>${body.total}</b><span>total tickets</span></div>`];
  for (const s of STATUS_OPTIONS) {
    const cls = ["resolved", "closed"].includes(s) ? "card"
      : ["in_progress", "pending_customer"].includes(s) ? "card warn" : "card";
    cards.push(`<div class="${cls}${statusFilter === s ? " card-active" : ""}" data-status="${s}"><b>${body.by_status[s] ?? 0}</b><span>${s.replaceAll("_", " ")}</span></div>`);
  }
  cards.push(`<div class="card${statusFilter === "__auto" ? " card-active" : ""}" data-status="__auto"><b>${body.auto_routed}</b><span>auto-routed</span></div>`);
  $("db-cards").innerHTML = cards.join("");
  $("db-cards").querySelectorAll(".card[data-status]").forEach((el) => {
    el.style.cursor = "pointer";
    el.title = "Click to filter the ticket table";
    el.addEventListener("click", () => {
      const s = el.dataset.status;
      statusFilter = statusFilter === s ? null : s;
      loadDashboard();
    });
  });

  rebuildTenantOptions(body.tickets.map((t) => t.tenant_id));

  const channelSel = $("db-channel");
  const channels = [...new Set(body.tickets.map((t) => t.source ?? "unknown"))].sort();
  const curCh = channelSel.value;
  channelSel.innerHTML = `<option value="">All channels</option>` +
    channels.map((c) => `<option value="${esc(c)}"${c === curCh ? " selected" : ""}>${esc(c)}</option>`).join("");

  const visible = statusFilter === "__auto"
    ? body.tickets.filter((t) => t.auto_route)
    : statusFilter && statusFilter !== "__all"
      ? body.tickets.filter((t) => t.status === statusFilter)
      : body.tickets;
  const filtered = channelFilter
    ? visible.filter((t) => (t.source ?? "unknown") === channelFilter)
    : visible;

  const tbody = $("db-tbody");
  tbody.innerHTML = "";
  $("db-empty").hidden = filtered.length > 0;
  $("db-empty").textContent = statusFilter || channelFilter
    ? "No tickets match the current filters."
    : "No tickets yet — submit one from the Intake tab.";
  for (const t of filtered) {
    const tr = document.createElement("tr");
    if (["resolved", "closed"].includes(t.status)) tr.className = "row-closed";
    const opts = STATUS_OPTIONS.map(
      (s) => `<option value="${s}"${s === t.status ? " selected" : ""}>${s.replaceAll("_", " ")}</option>`,
    ).join("");
    tr.innerHTML = `
      <td><button type="button" class="link ticket-link" data-tenant="${esc(t.tenant_id)}" data-ticket="${esc(t.ticket_id)}">${esc(t.ticket_id)}</button></td>
      <td>${esc(tenantLabel(t.tenant_id))}</td>
      <td><span class="badge badge-channel">${esc(t.source ?? "unknown")}</span></td>
      <td>${t.priority ? `<span class="badge pri-${esc(t.priority.toLowerCase())}">${esc(t.priority)}</span>` : `<span class="muted">—</span>`}</td>
      <td>${esc(t.queue_id ?? "—")}</td>
      <td><code>${esc(t.rule_id ?? "—")}</code></td>
      <td><select class="status-sel status-${t.status}" data-tenant="${esc(t.tenant_id)}" data-ticket="${esc(t.ticket_id)}">${opts}</select></td>
      <td>v${t.canonical_version}</td>
      <td class="muted">${new Date(t.updated_at).toLocaleString()}</td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll(".status-sel").forEach((sel) => {
    sel.addEventListener("change", () => updateStatus(sel));
  });
  tbody.querySelectorAll(".ticket-link").forEach((btn) => {
    btn.addEventListener("click", () => openTicket(btn.dataset.tenant, btn.dataset.ticket));
  });

  renderWorklists(body.tickets);
}

const CLOSED = new Set(["resolved", "closed"]);

function ticketChip(t) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "link ticket-link";
  btn.dataset.tenant = t.tenant_id;
  btn.dataset.ticket = t.ticket_id;
  btn.textContent = t.ticket_id;
  btn.addEventListener("click", () => openTicket(t.tenant_id, t.ticket_id));
  const meta = document.createElement("span");
  meta.className = "chip-meta muted";
  meta.textContent = ` ${tenantLabel(t.tenant_id)} · ${t.queue_id ?? "unassigned"} · ${t.rule_id ?? "—"}`;
  const wrap = document.createElement("div");
  wrap.className = "chip";
  wrap.appendChild(btn);
  wrap.appendChild(meta);
  return wrap;
}

function renderWorklists(tickets) {
  // Pending agent: routed to a queue/draft lane but not yet resolved.
  const pending = tickets.filter(
    (t) => !CLOSED.has(t.status) && t.lane !== "auto_answer",
  );
  // Bot-solved: pipeline auto-resolved (auto_answer lane or auto_route+closed).
  const botDone = tickets.filter(
    (t) =>
      t.lane === "auto_answer" ||
      (t.auto_route === true && CLOSED.has(t.status)),
  );
  // Human-solved: closed by an agent (resolved/closed, not bot-handled).
  const humanDone = tickets.filter(
    (t) => CLOSED.has(t.status) && !botDone.includes(t),
  );

  $("pending-count").textContent = `(${pending.length})`;
  $("bot-count").textContent = `(${botDone.length})`;
  $("human-count").textContent = `(${humanDone.length})`;

  // Resolution analysis: share of closed tickets handled by bot vs human.
  const closed = botDone.length + humanDone.length;
  const pct = (n) => (closed ? Math.round((n / closed) * 100) : 0);
  $("wl-analysis").innerHTML = [
    `<div class="card"><b>${pending.length}</b><span>awaiting action</span></div>`,
    `<div class="card"><b>${botDone.length}</b><span>bot-solved (${pct(botDone.length)}% of closed)</span></div>`,
    `<div class="card"><b>${humanDone.length}</b><span>human-solved (${pct(humanDone.length)}% of closed)</span></div>`,
    `<div class="card"><b>${closed ? Math.round(((botDone.length) / closed) * 100) : 0}%</b><span>automation rate</span></div>`,
  ].join("");

  const pl = $("pending-list");
  const bl = $("bot-list");
  const hl = $("human-list");
  pl.innerHTML = "";
  bl.innerHTML = "";
  hl.innerHTML = "";
  const empty = (el, msg) => { el.innerHTML = `<p class="muted">${msg}</p>`; };
  if (!pending.length) empty(pl, "Nothing waiting on an agent.");
  if (!botDone.length) empty(bl, "No tickets auto-resolved yet.");
  if (!humanDone.length) empty(hl, "No agent-resolved tickets yet.");
  for (const t of pending) pl.appendChild(ticketChip(t));
  for (const t of botDone) bl.appendChild(ticketChip(t));
  for (const t of humanDone) hl.appendChild(ticketChip(t));
}

async function updateStatus(sel) {
  const { tenant, ticket } = sel.dataset;
  const res = await fetch(
    `/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticket)}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: sel.value }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || `update failed: ${res.status}`);
  }
  loadDashboard();
}

/* ---------- home analytics + channels ---------- */

let LAST_SUMMARY = null;
let LAST_ANALYTICS = null;

const PALETTE = ["#465fff", "#12b76a", "#f79009", "#f04438", "#7a5af8", "#0ba5ec", "#ee46bc", "#66c61c"];

function _smoothPath(p) {
  if (p.length < 2) return "";
  let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += `C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

function areaChart(el, data, { label = (d) => d.date.slice(5) } = {}) {
  if (!el) return;
  const W = 640, H = 190;
  const P = { t: 18, r: 14, b: 28, l: 36 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const n = data.length;
  const max = Math.max(1, ...data.map((d) => d.count || 0));
  if (!data.some((d) => d.count > 0)) {
    el.innerHTML = `<div class="chart-empty"><p class="muted">No ticket activity in the last 14 days.</p></div>`;
    return;
  }
  const x = (i) => P.l + (n > 1 ? (i * iw) / (n - 1) : iw / 2);
  const y = (v) => P.t + ih - (v / max) * ih;
  const pts = data.map((d, i) => [x(i), y(d.count || 0)]);
  const line = _smoothPath(pts);
  const base = P.t + ih;
  const area = `${line} L${x(n - 1).toFixed(1)},${base} L${x(0).toFixed(1)},${base} Z`;
  const gid = `ag-${Math.random().toString(36).slice(2, 8)}`;

  const grid = [0.25, 0.5, 0.75].map((f) => {
    const gy = P.t + ih * (1 - f);
    const val = Math.round(max * f);
    return `<line x1="${P.l}" y1="${gy}" x2="${W - P.r}" y2="${gy}" stroke="#eef1f5" stroke-dasharray="3 4"/>
      <text x="${P.l - 8}" y="${gy + 3}" font-size="9" fill="#98a2b3" text-anchor="end">${val}</text>`;
  }).join("");
  const xlabels = data.map((d, i) =>
    (i % 2 === 0 || i === n - 1)
      ? `<text x="${x(i)}" y="${H - 8}" font-size="9" fill="#98a2b3" text-anchor="middle">${esc(label(d).slice(5))}</text>` : ""
  ).join("");
  const dots = data.map((d, i) => d.count
    ? `<circle cx="${x(i)}" cy="${y(d.count)}" r="3" fill="#fff" stroke="#465fff" stroke-width="2"/>` : ""
  ).join("");

  el.innerHTML = `
    <div class="chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" width="100%" style="height:12rem;display:block">
        <defs>
          <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#465fff" stop-opacity="0.22"/>
            <stop offset="100%" stop-color="#465fff" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        ${grid}
        <path d="${area}" fill="url(#${gid})"/>
        <path d="${line}" fill="none" stroke="#465fff" stroke-width="2.2" stroke-linecap="round"/>
        ${dots}
        ${xlabels}
        <line x1="${P.l}" y1="${base}" x2="${W - P.r}" y2="${base}" stroke="#e4e7ec"/>
        <rect class="chart-hit" x="${P.l}" y="0" width="${iw}" height="${H}" fill="transparent"/>
      </svg>
      <div class="chart-tip" hidden></div>
    </div>`;

  const tip = el.querySelector(".chart-tip");
  const hit = el.querySelector(".chart-hit");
  const wrap = el.querySelector(".chart-wrap");
  hit.addEventListener("mousemove", (e) => {
    const rect = wrap.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((mx - P.l) / iw) * (n - 1))));
    const d = data[i];
    tip.hidden = false;
    tip.innerHTML = `<b>${esc(label(d))}</b><span>${d.count} ticket${d.count === 1 ? "" : "s"}</span>`;
    const px = (x(i) / W) * rect.width;
    tip.style.left = `${Math.min(Math.max(px, 40), rect.width - 40)}px`;
    tip.style.top = `${(y(d.count || 0) / H) * rect.height - 8}px`;
  });
  hit.addEventListener("mouseleave", () => { tip.hidden = true; });
}

function hbars(el, entries, { total } = {}) {
  if (!el) return;
  const max = Math.max(1, ...entries.map(([, v]) => v));
  const rows = entries.length
    ? entries.map(([k, v], i) => `
      <div class="hbar-row">
        <span class="hbar-label" title="${esc(k)}">${esc(k)}</span>
        <span class="hbar-track"><span class="hbar-fill" style="width:${(v / max * 100).toFixed(1)}%;background:${PALETTE[i % PALETTE.length]}"></span></span>
        <b class="hbar-val">${v}${total != null ? ` · ${Math.round(v / total * 100)}%` : ""}</b>
      </div>`).join("")
    : `<p class="muted">No data yet.</p>`;
  el.innerHTML = rows;
}

function donut(el, slices) {
  if (!el) return;
  const total = slices.reduce((a, [, v]) => a + v, 0);
  if (!total) { el.innerHTML = `<p class="muted">No closed tickets yet.</p>`; return; }
  const R = 34, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = slices.filter(([, v]) => v > 0).map(([k, v], i) => {
    const len = (v / total) * C;
    const seg = `<circle r="${R}" cx="50" cy="50" fill="none" stroke="${PALETTE[i % PALETTE.length]}" stroke-width="14"
      stroke-dasharray="${len.toFixed(2)} ${C.toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}"
      transform="rotate(-90 50 50)"><title>${esc(k)}: ${v}</title></circle>`;
    off += len;
    return seg;
  }).join("");
  const legend = slices.filter(([, v]) => v > 0).map(([k, v], i) =>
    `<div class="legend-item"><span class="legend-dot" style="background:${PALETTE[i % PALETTE.length]}"></span>${esc(k)} <b>${Math.round(v / total * 100)}%</b></div>`).join("");
  el.innerHTML = `<div class="donut-wrap"><svg viewBox="0 0 100 100" class="donut">${arcs}</svg><div class="donut-legend">${legend}</div></div>`;
}

function renderAnalytics() {
  const a = LAST_ANALYTICS;
  if (!a) return;
  try { renderKpis(a); } catch (e) { console.error("kpis", e); }
  try { areaChart($("chart-daily"), a.by_day); } catch (e) { console.error("daily", e); }
  try {
    const queues = Object.entries(a.by_queue).sort((x, y) => y[1] - x[1]);
    hbars($("chart-queue"), queues, { total: a.total });
  } catch (e) { console.error("queue", e); }
  try {
    donut($("chart-resolution"), [
      ["Bot-solved", a.bot_solved_count],
      ["Human-solved", a.human_solved_count],
      ["Open / in progress", a.open_count],
    ]);
  } catch (e) { console.error("donut", e); }
  renderSlaRisk(a);
  renderRecent(a);
}

function renderKpis(a) {
  const KPI_SVG = {
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    bot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 8V4M9 13h.01M15 13h.01"/><path d="M9 17h6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    pct: '<path d="M19 5 5 19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>',
    alert: '<path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4M12 17.5h.01"/>',
    money: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M15.5 9.5c-.7-1-2-1.5-3.5-1.5-2 0-3.5 1-3.5 2.5 0 3.5 7 1.5 7 5 0 1.5-1.5 2.5-3.5 2.5-1.5 0-2.8-.5-3.5-1.5"/>',
  };
  const kpiIcon = (k) =>
    `<span class="kpi-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${KPI_SVG[k]}</svg></span>`;
  $("home-kpis").innerHTML = [
    [`${a.open_count}`, "open tickets", "plain", "inbox"],
    [`${a.pending_agent_count}`, "awaiting agent", "warn", "clock"],
    [`${a.bot_solved_count}`, "bot-solved", "ok", "bot"],
    [`${a.human_solved_count}`, "human-solved", "plain", "user"],
    [`${Math.round(a.automation_rate * 100)}%`, "automation coverage", "ok", "pct"],
    [`${a.sla_breached_count}`, "SLA breached", a.sla_breached_count ? "bad" : "ok", "alert"],
    ...Object.entries(a.sla_penalties_by_currency).map(([c, amt]) => [`${c} ${amt}`, "penalty exposure", "bad", "money"]),
  ].map(([v, l, cls, icon]) => `<div class="kpi ${cls}">${kpiIcon(icon)}<div class="kpi-body"><b>${esc(v)}</b><span>${esc(l)}</span></div></div>`).join("");
}

function renderSlaRisk(a) {
  const risk = $("home-sla-risk");
  const tenantRows = (a.sla_by_tenant ?? []).map((t) => {
    const pen = Object.entries(t.penalties ?? {})
      .map(([c, amt]) => `${c} ${amt}`).join(", ");
    return `<div class="risk-tenant-head">
      <b>${esc(tenantLabel(t.tenant_id))}</b>
      <span class="muted">${t.unanswered} unanswered · ${t.breached} breached${pen ? ` · <b class="pen-amt">${esc(pen)}</b>` : ""}</span>
    </div>`;
  }).join("");
  if (!a.sla_at_risk.length) {
    risk.innerHTML = tenantRows + `<p class="muted">No SLA exposure — all tickets within thresholds.</p>`;
  } else {
    const byTenant = new Map();
    for (const e of a.sla_at_risk) {
      if (!byTenant.has(e.tenant_id)) byTenant.set(e.tenant_id, []);
      byTenant.get(e.tenant_id).push(e);
    }
    risk.innerHTML = [...byTenant.entries()].map(([tid, evs]) => {
      const pen = {};
      for (const e of evs) {
        if (e.breach) {
          const k = `${e.breach.currency} ${e.breach.penalty_amount}`;
          pen[k] = (pen[k] || 0) + 1;
        }
      }
      const penTxt = Object.entries(pen).map(([k, n]) => `${k}${n > 1 ? ` ×${n}` : ""}`).join(", ");
      return `
        <div class="risk-tenant">
          <div class="risk-tenant-head">
            <b>${esc(tenantLabel(tid))}</b>
            <span class="muted">${evs.length} at risk${penTxt ? ` · ${esc(penTxt)}` : ""}</span>
          </div>
          ${evs.map((e) => `
            <div class="risk-row">
              <button type="button" class="link ticket-link" data-tenant="${esc(e.tenant_id)}" data-ticket="${esc(e.ticket_id)}">${esc(e.ticket_id)}</button>
              <span class="muted">${esc(e.status)} · ${e.age_hours}h old</span>
              ${e.breach ? `<span class="badge badge-expired">${esc(e.breach.currency)} ${esc(e.breach.penalty_amount)}</span>` : `<span class="badge badge-in_progress">unanswered</span>`}
            </div>`).join("")}
        </div>`;
    }).join("");
    risk.innerHTML = tenantRows + risk.innerHTML;
    risk.querySelectorAll(".ticket-link").forEach((b) =>
      b.addEventListener("click", () => openTicket(b.dataset.tenant, b.dataset.ticket)));
  }
}

function renderRecent(a) {
  const recent = $("home-recent");
  const items = (a.recent ?? []).slice(0, 3);
  recent.innerHTML = items.length ? "" : `<p class="muted">No tickets yet — submit one from the Intake tab.</p>`;
  for (const t of items) recent.appendChild(ticketChip(t));
}

async function loadAnalytics() {
  const tenant = $("home-tenant").value;
  const qs = tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : "";
  const { ok, body } = await jget(`/v1/dashboard/analytics${qs}`);
  if (!ok) {
    $("home-kpis").innerHTML = `<div class="kpi bad"><b>!</b><span>${esc(body.detail || "analytics unavailable")}</span></div>`;
    return;
  }
  LAST_ANALYTICS = body;
  renderAnalytics();
}

/* ---------- channels ---------- */

function channelIcon(src) {
  return { widget: "💬", email: "✉️", partner: "🤝", whatsapp: "🟢", phone: "📞" }[src] ?? "•";
}

function renderChannels() {
  const tickets = LAST_SUMMARY?.tickets ?? [];
  const analytics = LAST_ANALYTICS;
  if (!tickets.length) {
    $("ch-cards").innerHTML = `<p class="muted">No tickets yet.</p>`;
    $("ch-tbody").innerHTML = "";
    return;
  }
  const breachedIds = new Set(
    (analytics?.sla_at_risk ?? []).map((e) => `${e.tenant_id}/${e.ticket_id}`));
  const byCh = new Map();
  for (const t of tickets) {
    const ch = t.source ?? "unknown";
    if (!byCh.has(ch)) byCh.set(ch, []);
    byCh.get(ch).push(t);
  }
  const CLOSED = new Set(["resolved", "closed"]);
  const rows = [...byCh.entries()].map(([ch, list]) => {
    const open = list.filter((t) => !CLOSED.has(t.status)).length;
    const done = list.length - open;
    const bot = list.filter((t) => t.lane === "auto_answer" || (t.auto_route && CLOSED.has(t.status))).length;
    const breached = list.filter((t) => breachedIds.has(`${t.tenant_id}/${t.ticket_id}`)).length;
    return { ch, total: list.length, open, done, bot, breached, tickets: list };
  }).sort((a, b) => b.total - a.total);

  $("ch-cards").innerHTML = rows.map((r) => `
    <div class="channel-card" data-channel="${esc(r.ch)}">
      <div class="ch-head"><span class="ch-icon">${channelIcon(r.ch)}</span><b>${esc(r.ch)}</b></div>
      <div class="ch-stat"><b>${r.total}</b><span>tickets</span></div>
      <div class="ch-stat"><b>${r.open}</b><span>open</span></div>
      <div class="ch-stat"><b>${Math.round(r.bot / Math.max(1, r.total) * 100)}%</b><span>bot-handled</span></div>
    </div>`).join("");
  $("ch-cards").querySelectorAll(".channel-card").forEach((el) => {
    el.style.cursor = "pointer";
    el.title = "View tickets from this channel";
    el.addEventListener("click", () => {
      switchTab("tickets");
      channelFilter = el.dataset.channel;
      $("db-channel").value = channelFilter;
      loadDashboard();
    });
  });

  const tbody = $("ch-tbody");
  tbody.innerHTML = rows.map((r) => `
    <tr${r.breached ? ' class="row-breach"' : ""}>
      <td><b>${esc(r.ch)}</b></td>
      <td>${r.total}</td>
      <td>${r.open}</td>
      <td>${r.done}</td>
      <td>${r.bot} <span class="muted">(${Math.round(r.bot / Math.max(1, r.total) * 100)}%)</span></td>
      <td>${r.breached || "—"}</td>
      <td>${r.breached ? "exposure" : "—"}</td>
    </tr>`).join("");
}

async function loadChannels() {
  // Reuse summary (all tenants unless ch-tenant set) + analytics for SLA risk.
  const tenant = $("ch-tenant").value;
  const qs = tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : "";
  const [{ ok: okS, body: sum }, { ok: okA, body: ana }] = await Promise.all([
    jget(`/v1/dashboard/summary${qs}`),
    jget(`/v1/dashboard/analytics${qs}`),
  ]);
  if (okS) { LAST_SUMMARY = sum; rebuildTenantOptions(sum.tickets.map((t) => t.tenant_id)); }
  if (okA) LAST_ANALYTICS = ana;
  renderChannels();
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  for (const n of TAB_NAMES) $("tab-" + n).hidden = n !== name;
  if (name === "home") loadAnalytics();
  if (name === "channels") loadChannels();
}

/* ---------- inline ticket detail ---------- */

function renderFacts(facts) {
  const rows = [
    ["Payroll run ID", facts.run_id],
    ["Linked Jira tickets", facts.jira_keys],
    ["Knowledge base articles", facts.kb_ids],
    ["Error codes", facts.error_codes],
    ["Refund references", facts.refund_refs],
  ];
  const parts = rows
    .filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => {
      const val = Array.isArray(v) ? v.map(esc).join(", ") : v === true ? "yes" : esc(v);
      return `<div class="kv"><span>${esc(k)}</span><b>${val}</b></div>`;
    });
  return parts.length ? parts.join("") : `<p class="muted">No structured facts extracted.</p>`;
}

/* ---------- SPA detail views (render inside the active tab) ---------- */

const TAB_NAMES = ["home", "intake", "tickets", "channels", "worklist", "sla", "rules", "sla-rules"];

function activeTab() {
  return document.querySelector(".tab.active")?.dataset.tab || "home";
}

function openSpaDetail(title, bodyHtml) {
  const panel = $("tab-" + activeTab());
  panel.querySelectorAll(":scope > section").forEach((el) => { el.hidden = true; });
  const slot = panel.querySelector(".spa-detail");
  slot.hidden = false;
  slot.innerHTML = `
    <section>
      <div class="section-head">
        <h2>${esc(title)}</h2>
        <button type="button" class="secondary spa-back">← Back</button>
      </div>
      <div class="spa-detail-body">${bodyHtml}</div>
    </section>`;
  slot.querySelector(".spa-back").addEventListener("click", closeSpaDetail);
}

function closeSpaDetail() {
  for (const name of TAB_NAMES) {
    const panel = $("tab-" + name);
    const slot = panel.querySelector(".spa-detail");
    if (slot) {
      slot.hidden = true;
      slot.innerHTML = "";
    }
    panel.querySelectorAll(":scope > section").forEach((el) => { el.hidden = false; });
  }
}

function renderSignals(sig) {
  const pri = (sig.priority ?? "—").toUpperCase();
  return `
    <div class="signals">
      <div class="kv"><span>AI priority</span><b><span class="badge pri-${esc(pri.toLowerCase())}">${esc(pri)}</span>${sig.p1 ? ' <span class="badge warn-badge">P1 flag</span>' : ""}</b></div>
      ${sig.summary ? `<div class="kv"><span>AI summary</span><b class="wrap">${esc(sig.summary)}</b></div>` : ""}
    </div>`;
}

function renderProvenance(prov) {
  const entries = Object.entries(prov ?? {});
  if (!entries.length) return `<p class="muted">No provenance recorded.</p>`;
  return entries.map(([k, v]) => {
    const val = typeof v === "object" && v !== null
      ? `<code>${esc(JSON.stringify(v))}</code>` : esc(v);
    return `<div class="kv"><span>${esc(k.replaceAll("_", " "))}</span><b>${val}</b></div>`;
  }).join("");
}

async function openTicket(tenant, ticket) {
  openSpaDetail(`${tenantLabel(tenant)} / ${ticket}`, `<p class="muted">loading…</p>`);
  const body = $("tab-" + activeTab()).querySelector(".spa-detail-body");

  const { ok, body: data } = await jget(
    `/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticket)}`,
  );
  if (!ok) {
    body.innerHTML = `<p class="muted">${esc(data.detail || "error")}</p>`;
    return;
  }
  const c = data.canonical ?? {};
  const t = c.ticket ?? {};
  const content = c.content ?? {};
  const facts = c.facts ?? {};
  const atts = c.attachments ?? [];
  const d = data.decision ?? {};
  const sla = data.sla ?? null;

  const fmtTs = (iso) => iso ? esc(iso.slice(0, 19).replace("T", " ")) : "—";
  const ctime = c.time ?? {};
  const steps = [
    { label: "Received", ts: ctime.received_at ?? ctime.customer_message_at, state: "done", note: "customer message ingested" },
    { label: "LLM analysis", ts: data.analysis?.created_at, state: data.analysis ? "done" : "pending", note: data.analysis?.payload?.status ?? "not run" },
    { label: "Classification", ts: d.created_at, state: d.lane ? "done" : "pending", note: `${(d.lane ?? "—").replaceAll("_", " ")} · ${d.rule_id ?? "no rule"}` },
    { label: "Bot answer", ts: data.llm_answer?.payload?.composed_at ?? data.llm_answer?.created_at, state: data.llm_answer?.payload?.answer ? "done" : (data.llm_answer ? "skipped" : "pending"), note: data.llm_answer?.payload?.verdict?.replaceAll("_", " ") ?? "not recorded" },
    { label: "Resolution", ts: data.ticket.human_intervened_at ?? data.ticket.updated_at, state: ["resolved", "closed"].includes(data.ticket.status) ? "done" : "pending", note: `${data.ticket.status}${data.ticket.human_intervened_at ? " · human touched" : ""}` },
  ];
  const flowHtml = `
    <div class="detail-card flow-card">
      <h3>Ticket flow</h3>
      <div class="flow">
        ${steps.map((s, i) => `
          <div class="flow-step flow-${s.state}">
            <div class="flow-dot">${i + 1}</div>
            <div class="flow-label">${esc(s.label)}</div>
            <div class="flow-note muted">${esc(s.note)}</div>
            <div class="flow-ts muted">${fmtTs(s.ts)}</div>
          </div>${i < steps.length - 1 ? `<div class="flow-line"></div>` : ""}`).join("")}
      </div>
    </div>`;

  const sig = data.analysis?.payload?.signals ?? {};
  const pri = (sig.priority ?? "").toUpperCase();
  const llmAnswer = data.llm_answer?.payload ?? null;

  body.innerHTML = `
    <div class="ticket-head">
      <div>
        <div class="ticket-title">${esc(ticket)}</div>
        <div class="ticket-sub muted">${esc(tenantLabel(tenant))} · ${esc(t.region ?? "")} · ${esc(data.ticket.source ?? "unknown")} · v${data.ticket.canonical_version}</div>
      </div>
      <div class="ticket-badges">
        <span class="badge status-${esc(data.ticket.status)}">${esc(data.ticket.status.replaceAll("_", " "))}</span>
        ${d.lane ? `<span class="badge lane">${esc(d.lane.replaceAll("_", " "))}</span>` : ""}
        ${pri ? `<span class="badge pri-${esc(pri.toLowerCase())}">${esc(pri)}</span>` : ""}
      </div>
    </div>
    <div class="ticket-layout">
      <div class="tl-rail">
        <div class="detail-card">
          <h3>Requester</h3>
          <div class="kv"><span>Tenant</span><b>${esc(tenantLabel(tenant))}</b></div>
          <div class="kv"><span>Channel</span><b>${esc(data.ticket.source ?? "unknown")}</b></div>
          <div class="kv"><span>Region</span><b>${esc(t.region ?? "—")}</b></div>
          <div class="kv"><span>Received</span><b>${fmtTs(ctime.received_at ?? ctime.customer_message_at)}</b></div>
          <div class="kv"><span>Canonical v</span><b>v${data.ticket.canonical_version}</b></div>
        </div>
        <div class="detail-card">
          <h3>Properties</h3>
          <div class="kv"><span>Status</span><b>${esc(data.ticket.status.replaceAll("_", " "))}</b></div>
          <div class="kv"><span>Queue</span><b>${esc(data.ticket.queue_id ?? "—")}</b></div>
          <div class="kv"><span>Routing rule</span><b><code>${esc(d.rule_id ?? data.ticket.rule_id ?? "—")}</code></b></div>
          <div class="kv"><span>Decided by</span><b>${esc(d.decided_by ?? "—")}</b></div>
          <div class="kv"><span>Auto-route</span><b>${d.auto_route ? "yes" : "no"}</b></div>
          <div class="kv"><span>Last modified</span><b>${fmtTs(data.ticket.updated_at)}</b></div>
          <div class="kv"><span>Human intervention</span><b>${data.ticket.human_intervened_at ? fmtTs(data.ticket.human_intervened_at) : "—"}</b></div>
          ${d.reason_codes?.length ? `<div class="kv"><span>Reason codes</span><b class="wrap">${d.reason_codes.map(esc).join(", ")}</b></div>` : ""}
        </div>
        ${sla ? `<div class="detail-card">
          <h3>SLA</h3>
          <div class="kv"><span>Rule</span><b><code>${esc(sla.breach?.sla_rule_id ?? "—")}</code></b></div>
          <div class="kv"><span>Age / threshold</span><b>${sla.age_hours}h / ${esc(sla.breach?.unanswered_hours ?? "—")}h</b></div>
          <div class="kv"><span>Penalty</span><b>${sla.breach ? `${esc(sla.breach.currency)} ${esc(sla.breach.penalty_amount)}` : "—"}</b></div>
        </div>` : ""}
      </div>
      <div class="tl-main">
        <div class="timeline">
          <div class="tl-item tl-customer">
            <div class="tl-head"><span class="tl-avatar">👤</span><div><b>Customer</b><span class="muted tl-ts">${fmtTs(ctime.customer_message_at ?? ctime.received_at)}</span></div></div>
            <div class="msg-subject">${esc(content.subject_masked ?? "—")}</div>
            <div class="msg-body">${esc(content.body_masked ?? "—")}</div>
          </div>
          <div class="tl-item tl-bot">
            <div class="tl-head"><span class="tl-avatar">🤖</span><div><b>Bot answer</b><span class="muted tl-ts">${llmAnswer?.composed_at ? fmtTs(llmAnswer.composed_at) : "post-routing"}</span></div>
              ${llmAnswer?.verdict ? `<span class="badge badge-${llmAnswer.verdict === "auto_answer" ? "approved" : llmAnswer.verdict === "draft_for_agent" ? "in_progress" : "expired"}">${esc(llmAnswer.verdict.replaceAll("_", " "))}</span>` : ""}</div>
            ${llmAnswer
              ? `${llmAnswer.answer ? `<div class="draft-answer">${esc(llmAnswer.answer).replaceAll("\n", "<br>")}</div>`
                 : `<p class="muted">${llmAnswer.status === "skipped" ? "Skipped — " + esc(llmAnswer.reason_code ?? "") : "No answer produced."}</p>`}
                 ${llmAnswer.citations?.length ? `<div class="kv"><span>Citations</span><b>${llmAnswer.citations.map((c) => `<code>${esc(c)}</code>`).join(" ")}</b></div>` : ""}`
              : `<p class="muted">No post-routing answer recorded.</p>`}
          </div>
          ${(data.replies ?? []).map((r) => `
            <div class="tl-item tl-${esc(r.author)}">
              <div class="tl-head"><span class="tl-avatar">${r.author === "agent" ? "🧑‍💼" : r.author === "system" ? "⚙️" : "👤"}</span><div><b>${esc(r.author)}</b><span class="muted tl-ts">${fmtTs(r.created_at)}</span></div></div>
              <div class="reply-body">${esc(r.body)}</div>
            </div>`).join("")}
          <div class="tl-item tl-composer">
            <textarea id="reply-body" rows="3" placeholder="Type a reply…"></textarea>
            <div class="row" style="display:flex;gap:.5rem;margin-top:.5rem;flex-wrap:wrap">
              <button type="button" class="secondary" id="btn-agent-reply">Send as agent (resolves)</button>
              <button type="button" class="secondary" id="btn-customer-reply">Simulate customer reply</button>
              ${["resolved", "closed"].includes(data.ticket.status)
                ? `<button type="button" class="secondary" id="btn-reopen">Reopen ticket</button>` : ""}
            </div>
          </div>
        </div>
      </div>
      <div class="tl-side">
        ${flowHtml}
        <div class="detail-card">
          <h3>LLM analysis</h3>
          ${data.analysis
            ? `<div class="kv"><span>Status</span><b>${esc(data.analysis.payload?.status ?? "—")}</b></div>
               <div class="kv"><span>Model</span><b>${esc(data.analysis.model_id ?? data.analysis.payload?.model_id ?? "—")}</b></div>
               <div class="kv"><span>Saved at</span><b>${fmtTs(data.analysis.created_at)}</b></div>
               ${data.analysis.payload?.declared_priority ? `<div class="kv"><span>Declared priority</span><b>${esc(data.analysis.payload.declared_priority)}</b></div>` : ""}
               ${data.analysis.payload?.signals ? renderSignals(data.analysis.payload.signals) : ""}
               ${data.analysis.payload?.reason ? `<div class="kv"><span>Reason</span><b>${esc(data.analysis.payload.reason)}</b></div>` : ""}`
            : `<p class="muted">No analysis saved yet.</p>`}
          <button type="button" class="secondary" id="btn-analyze" style="margin-top:.5rem">Run LLM analysis</button>
        </div>
        <div class="detail-card">
          <h3>Bot draft (RAG)</h3>
          <p class="muted" style="margin:.2rem 0 .6rem">Grounded answer attempt against the tenant-scoped KB, gated by the answerability policy.</p>
          <button type="button" class="secondary" id="btn-draft">Generate bot draft</button>
          <div id="draft-result" style="margin-top:.8rem"></div>
        </div>
        <div class="detail-card">
          <h3>Extracted facts</h3>
          ${renderFacts(facts)}
        </div>
        <div class="detail-card">
          <h3>Attachments (${atts.length})</h3>
          ${atts.length
            ? atts.map((a) => `<div class="kv"><span>${esc(a.filename ?? a.attachment_id)}</span><b>${esc(a.scan_status)} / ${esc(a.pii_class)} / ${esc(a.processing_status)}</b></div>`).join("")
            : `<p class="muted">none</p>`}
        </div>
        <div class="detail-card">
          <h3>Provenance</h3>
          ${renderProvenance(c.provenance)}
        </div>
      </div>
    </div>`;

  const draftBtn = $("btn-draft");
  const draftOut = $("draft-result");
  draftBtn.addEventListener("click", async () => {
    draftBtn.disabled = true;
    draftBtn.textContent = "Generating…";
    draftOut.innerHTML = `<p class="muted">retrieving KB context…</p>`;
    const { ok, body: res } = await jpost(
      `/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticket)}/draft`, {});
    draftBtn.disabled = false;
    draftBtn.textContent = "Generate bot draft";
    if (!ok) {
      draftOut.innerHTML = `<p class="muted">${esc(res.detail || "draft failed")}</p>`;
      return;
    }
    const badgeCls = res.verdict === "auto_answer" ? "approved"
      : res.verdict === "draft_for_agent" ? "in_progress" : "expired";
    const answerHtml = res.answer
      ? `<div class="draft-answer">${esc(res.answer).replaceAll("\n", "<br>")}</div>`
      : `<p class="muted">No answer produced — routed to human.</p>`;
    draftOut.innerHTML = `
      <div class="kv"><span>Verdict</span><b><span class="badge badge-${badgeCls}">${esc(res.verdict.replaceAll("_", " "))}</span></b></div>
      <div class="kv"><span>Reasons</span><b>${res.verdict_reasons.map(esc).join(", ")}</b></div>
      <div class="kv"><span>RAG status</span><b>${esc(res.rag_status)} (${esc(res.rag_reason_code)})</b></div>
      <div class="kv"><span>Provider</span><b>${esc(res.llm_provider)}${res.cache_hit ? " · cache hit" : ""}</b></div>
      <div class="kv"><span>Tokens</span><b>${res.input_tokens} in / ${res.output_tokens} out</b></div>
      ${res.citations.length ? `<div class="kv"><span>Citations</span><b>${res.citations.map((c) => `<code>${esc(c)}</code>`).join(" ")}</b></div>` : ""}
      ${answerHtml}`;
  });

  const replyBody = $("reply-body");
  const postReply = async (author) => {
    const text = (replyBody.value || "").trim();
    if (!text) { replyBody.focus(); return; }
    await jpost(
      `/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticket)}/reply`,
      { author, body: text });
    openTicket(tenant, ticket); // re-render with updated thread/status
    loadDashboard();
  };
  $("btn-agent-reply").addEventListener("click", () => postReply("agent"));
  $("btn-customer-reply").addEventListener("click", () => postReply("customer"));
  const reopenBtn = $("btn-reopen");
  if (reopenBtn) {
    reopenBtn.addEventListener("click", async () => {
      await jpost(
        `/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticket)}/reopen`, {});
      openTicket(tenant, ticket);
      loadDashboard();
    });
  }
  $("btn-analyze").addEventListener("click", async () => {
    const btn = $("btn-analyze");
    btn.disabled = true;
    btn.textContent = "Analyzing…";
    await jpost(
      `/v1/dashboard/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticket)}/analyze`, {});
    openTicket(tenant, ticket);
    loadDashboard();
  });
}

/* ---------- SLA & penalties ---------- */

let SLA_RULES = null;

function renderSlaRules() {
  if (!SLA_RULES) return;
  const tenant = $("sla-tenant").value;
  const statusFilter = $("sla-status").value;
  const visible = SLA_RULES.filter((r) => {
    if (tenant && r.tenant_id && r.tenant_id !== tenant) return false;
    const eff = r.effective_status ?? r.status;
    if (statusFilter && eff !== statusFilter) return false;
    return true;
  });
  $("sla-policy-meta").textContent =
    "Expired/scheduled rules are shown but never applied to tickets.";
  const tbody = $("sla-rules-tbody");
  tbody.innerHTML = "";
  for (const r of visible) {
    const eff = r.effective_status ?? r.status;
    const tr = document.createElement("tr");
    tr.classList.add("rule-row");
    tr.dataset.ruleId = r.id;
    tr.innerHTML = `
      <td><button type="button" class="link ticket-link" data-rule="${esc(r.id)}">${esc(r.id)}</button></td>
      <td><span class="badge badge-${eff === "scheduled" ? "draft" : eff}">${esc(eff)}</span></td>
      <td>${esc(r.tenant_id ?? "any")}</td>
      <td class="muted">${esc(r.valid_from ?? "—")}</td>
      <td class="muted">${esc(r.valid_to ?? "open")}</td>
      <td>${r.unanswered_hours}h</td>
      <td>${esc(r.currency)} ${esc(r.penalty_amount)}</td>
      <td class="muted">${esc(r.description ?? "")}</td>`;
    tbody.appendChild(tr);
  }
  if (!visible.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No SLA rules match the filters.</td></tr>`;
  }
}

async function loadSlaRules() {
  const { ok, body } = await jget("/v1/dashboard/sla/rules");
  if (!ok) return;
  SLA_RULES = body.rules;
  renderSlaRules();
}

$("sla-tenant").addEventListener("change", renderSlaRules);
$("sla-status").addEventListener("change", renderSlaRules);

async function loadSla() {
  const tenant = $("sla-tenant-filter").value;
  const qs = tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : "";
  const { ok, body } = await jget(`/v1/dashboard/sla${qs}`);
  if (!ok) {
    $("sla-cards").innerHTML = `<div class="card bad"><b>!</b><span>${body.detail || "error"}</span></div>`;
    return;
  }
  const cards = [
    `<div class="card"><b>${body.total_tickets}</b><span>tickets</span></div>`,
    `<div class="card warn"><b>${body.unanswered_count}</b><span>unanswered</span></div>`,
    `<div class="card bad"><b>${body.breached_count}</b><span>SLA breached</span></div>`,
  ];
  for (const [cur, amt] of Object.entries(body.penalties_by_currency)) {
    cards.push(`<div class="card bad"><b>${cur} ${amt}</b><span>penalty due</span></div>`);
  }
  $("sla-cards").innerHTML = cards.join("");

  const tbody = $("sla-tbody");
  tbody.innerHTML = "";
  for (const e of body.evaluations) {
    if (!e.unanswered) continue;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button type="button" class="link ticket-link" data-tenant="${esc(e.tenant_id)}" data-ticket="${esc(e.ticket_id)}">${esc(e.ticket_id)}</button></td>
      <td>${esc(tenantLabel(e.tenant_id))}</td>
      <td>${esc(e.status)}</td>
      <td>${e.age_hours}h</td>
      <td><code>${esc(e.rule_id ?? "—")}</code></td>
      <td><code>${esc(e.breach?.sla_rule_id ?? "—")}</code></td>
      <td>${e.breach ? `${esc(e.breach.currency)} ${esc(e.breach.penalty_amount)}` : "—"}</td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll(".ticket-link").forEach((btn) => {
    btn.addEventListener("click", () => openTicket(btn.dataset.tenant, btn.dataset.ticket));
  });
}

$("btn-sla").addEventListener("click", loadSla);

/* ---------- routing rules + tenant context ---------- */

function selectedRulesTenant() {
  return $("rules-tenant").value || null;
}

function renderPolicy() {
  if (!POLICY) return;
  $("policy-meta").textContent =
    `policy ${POLICY.policy_version} · ${POLICY.rules.length} rules (incl. expired)`;

  const tenant = selectedRulesTenant();
  const statusFilter = $("rules-status").value;
  const region = tenant ? POLICY.tenant_contexts?.[tenant]?.region ?? null : null;
  const visible = POLICY.rules.filter((r) => {
    if (region && r.region && r.region !== region) return false;
    if (tenant && r.tenant_id && r.tenant_id !== tenant) return false;
    if (statusFilter && r.status !== statusFilter) return false;
    return true;
  });
  const tbody = $("rules-tbody");
  tbody.innerHTML = "";
  for (const r of visible) {
    const tr = document.createElement("tr");
    const validity = r.valid_from || r.valid_to
      ? `${r.valid_from ?? "…"} → ${r.valid_to ?? "open"}` : "always";
    tr.classList.add("rule-row");
    tr.dataset.ruleId = r.id;
    tr.innerHTML = `
      <td><button type="button" class="link ticket-link" data-rule="${esc(r.id)}">${esc(r.id)}</button></td>
      <td><span class="badge badge-${r.status}">${esc(r.status)}</span></td>
      <td>${esc(r.tenant_id ?? "any")}</td>
      <td class="muted">${esc(validity)}</td>
      <td>${esc(r.queue_id)}</td>
      <td>${esc(r.lane.replaceAll("_", " "))}</td>
      <td>${esc(r.region ?? "any")}</td>
      <td class="muted">${r.signals.length ? r.signals.map(esc).join(", ") : "—"}</td>`;
    tbody.appendChild(tr);
  }
  if (!visible.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No rules match the filters.</td></tr>`;
  }

  renderCtxTenant();
}

/* ---------- tenant classification context sub-tab ---------- */

function ruleLifecycle(r) {
  // effective lifecycle for the per-tenant policy list
  const today = new Date().toISOString().slice(0, 10);
  if (r.status === "expired" || (r.valid_to && r.valid_to < today)) return "expired";
  if (r.valid_from && r.valid_from > today) return "scheduled";
  if (r.status === "approved") return "active";
  return "inactive"; // draft / in_progress / outdated
}

function renderCtxTenant() {
  const sel = $("ctx-tenant");
  if (!sel || !POLICY) return;
  const tid = sel.value;
  const box = $("ctx-detail");
  if (!tid) {
    box.innerHTML = `<p class="muted">Select a tenant to view its classification context and routing policies.</p>`;
    return;
  }
  const ctx = POLICY.tenant_contexts?.[tid];
  const rules = POLICY.rules.filter((r) => r.tenant_id === tid || !r.tenant_id);
  const counts = { active: 0, scheduled: 0, expired: 0, inactive: 0 };
  const rows = rules.map((r) => {
    const life = ruleLifecycle(r);
    counts[life]++;
    const validity = r.valid_from || r.valid_to
      ? `${r.valid_from ?? "…"} → ${r.valid_to ?? "open"}` : "always";
    return `<tr>
      <td><button type="button" class="link ticket-link" data-rule="${esc(r.id)}">${esc(r.id)}</button></td>
      <td><span class="badge badge-${life}">${esc(life)}</span></td>
      <td class="muted">${esc(validity)}</td>
      <td>${esc(r.queue_id)}</td>
      <td>${esc(r.lane.replaceAll("_", " "))}</td>
      <td class="muted">${r.signals?.length ? r.signals.map(esc).join(", ") : "—"}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `
    <div class="ctx-card open">
      <div class="ctx-head static">
        <span><b>${esc(tenantLabel(tid))}</b> <span class="muted">${esc(tid)}${ctx?.region ? " · " + esc(ctx.region) : ""}</span></span>
        <span class="ctx-meta muted">${counts.active} active · ${counts.scheduled} scheduled · ${counts.expired} expired · ${counts.inactive} inactive</span>
      </div>
      <div class="ctx-body">
        ${ctx?.notes ? `<p class="muted">${esc(ctx.notes)}</p>` : ""}
        <div class="ctx-cols">
          <div>
            <b class="ctx-label">QUEUES</b>
            <ul>${Object.entries(ctx?.queue_hints ?? {}).map(([q, d]) => `<li><b>${esc(q)}</b> — ${esc(d)}</li>`).join("") || "<li class='muted'>none</li>"}</ul>
          </div>
          <div>
            <b class="ctx-label">PRIORITY GUIDANCE</b>
            <ul>${(ctx?.priority_guidance ?? []).map((g) => `<li>${esc(g)}</li>`).join("") || "<li class='muted'>none</li>"}</ul>
          </div>
        </div>
      </div>
    </div>
    <h3 class="ctx-label" style="margin:.9rem 0 .4rem">ROUTING POLICIES — ${esc(tenantLabel(tid))}</h3>
    <table class="mini-table">
      <thead><tr><th>Rule</th><th>Lifecycle</th><th>Validity</th><th>Queue</th><th>Lane</th><th>Signals</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6" class="muted">No policies for this tenant.</td></tr>`}</tbody>
    </table>`;
  box.querySelectorAll("[data-rule]").forEach((b) =>
    b.addEventListener("click", () => openRoutingRule(b.dataset.rule)));
}

async function loadPolicy() {
  const { ok, body } = await jget("/v1/routing/policy");
  if (ok) {
    POLICY = body;
    TENANT_NAMES = Object.fromEntries(
      Object.entries(body.tenant_contexts ?? {})
        .filter(([, c]) => c.display_name)
        .map(([id, c]) => [id, c.display_name]),
    );
    if (body.ticket_statuses?.length) STATUS_OPTIONS = body.ticket_statuses;
    const ctxTenants = Object.keys(body.tenant_contexts ?? {});
    if (ctxTenants.length) {
      TENANT_OPTIONS = [...new Set([...TENANT_OPTIONS, ...ctxTenants])].sort();
      rebuildTenantOptions();
    }
    renderPolicy();
  }
}

$("rules-tenant").addEventListener("change", renderPolicy);
$("rules-status").addEventListener("change", renderPolicy);
$("ctx-tenant").addEventListener("change", renderCtxTenant);

document.querySelectorAll("#tab-rules .subtab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tab-rules .subtab").forEach((b) =>
      b.classList.toggle("active", b === btn));
    $("rules-main").hidden = btn.dataset.sub !== "rules-main";
    $("rules-ctx").hidden = btn.dataset.sub !== "rules-ctx";
  });
});

/* ---------- rule detail (SPA inline view, editable) ---------- */

const RULE_STATUSES = ["approved", "in_progress", "outdated", "draft", "expired"];


function fieldRow(label, controlHtml) {
  return `<div class="form-row"><label>${esc(label)}</label>${controlHtml}</div>`;
}

function statusSelect(name, current) {
  return `<select name="${name}">${RULE_STATUSES.map(
    (s) => `<option value="${s}"${s === current ? " selected" : ""}>${s.replaceAll("_", " ")}</option>`,
  ).join("")}</select>`;
}

function textInput(name, value, placeholder = "") {
  return `<input name="${name}" type="text" value="${esc(value ?? "")}" placeholder="${esc(placeholder)}" />`;
}

function openRoutingRule(ruleId) {
  const r = POLICY?.rules.find((x) => x.id === ruleId);
  if (!r) return;
  openSpaDetail(`Routing rule · ${r.id}`, `
    <form id="rule-form" data-kind="routing" data-id="${esc(r.id)}">
      <div class="detail-grid">
        <div class="detail-card">
          <h3>Classification</h3>
          ${fieldRow("Status", statusSelect("status", r.status))}
          ${fieldRow("Queue", textInput("queue_id", r.queue_id, "e.g. Statutory-Compliance"))}
          ${fieldRow("Lane", textInput("lane", r.lane, "draft_for_agent"))}
          ${fieldRow("Region", `<select name="region"><option value="">any</option>${["IN", "UAE"].map((g) => `<option${g === r.region ? " selected" : ""}>${g}</option>`).join("")}</select>`)}
          ${fieldRow("Tenant", `<select name="tenant_id"><option value="">any</option>${TENANT_OPTIONS.map((t) => `<option value="${t}"${t === r.tenant_id ? " selected" : ""}>${esc(tenantLabel(t))}</option>`).join("")}</select>`)}
        </div>
        <div class="detail-card">
          <h3>Validity window</h3>
          ${fieldRow("Active from", `<input name="valid_from" type="date" value="${esc(r.valid_from ?? "")}" />`)}
          ${fieldRow("Deactivates", `<input name="valid_to" type="date" value="${esc(r.valid_to ?? "")}" />`)}
          <p class="muted" style="margin-top:.6rem">Empty dates = always active. Past <code>valid_to</code> marks the rule expired automatically.</p>
        </div>
        <div class="detail-card">
          <h3>Match criteria (read-only)</h3>
          <div class="kv"><span>Pattern</span><b><code>${esc(r.pattern)}</code></b></div>
          <div class="kv"><span>Signal aliases</span><b>${r.signals.length ? r.signals.map(esc).join(", ") : "—"}</b></div>
          <div class="kv"><span>Reason codes</span><b>${r.reason_codes.map(esc).join(", ") || "—"}</b></div>
        </div>
      </div>
      <div class="form-actions">
        <button type="submit">Save changes</button>
        <span id="rule-save-status" class="muted"></span>
      </div>
    </form>`);
}

function multiSelect(name, options, selected) {
  const sel = new Set(selected);
  const label = sel.size
    ? [...sel].map((s) => s.replaceAll("_", " ")).join(", ")
    : "Select statuses…";
  return `
    <div class="ms-wrap" data-ms="${name}">
      <button type="button" class="ms-toggle">${esc(label)} <span class="ms-caret">▾</span></button>
      <div class="ms-panel">
        ${options.map((o) => `
          <label class="ms-item">
            <input type="checkbox" name="${name}" value="${o}"${sel.has(o) ? " checked" : ""} />
            <span>${esc(o.replaceAll("_", " "))}</span>
          </label>`).join("")}
      </div>
    </div>`;
}

document.addEventListener("click", (e) => {
  const wrap = e.target.closest(".ms-wrap");
  document.querySelectorAll(".ms-wrap.open").forEach((w) => {
    if (w !== wrap) w.classList.remove("open");
  });
  if (wrap) {
    if (e.target.closest(".ms-toggle")) wrap.classList.toggle("open");
    else if (e.target.closest(".ms-item")) {
      // keep panel open; refresh toggle label after change
      const boxes = wrap.querySelectorAll('input[type="checkbox"]:checked');
      const names = [...boxes].map((b) => b.value.replaceAll("_", " "));
      wrap.querySelector(".ms-toggle").firstChild.textContent =
        names.length ? names.join(", ") + " " : "Select statuses… ";
    }
  }
});

function openSlaRule(ruleId) {
  const r = SLA_RULES?.find((x) => x.id === ruleId);
  if (!r) return;
  const eff = r.effective_status ?? r.status;
  openSpaDetail(`SLA rule · ${r.id}`, `
    <form id="rule-form" data-kind="sla" data-id="${esc(r.id)}">
      <div class="detail-grid">
        <div class="detail-card">
          <h3>Penalty</h3>
          ${fieldRow("Effective status", `<span class="badge badge-${eff === "scheduled" ? "draft" : eff}">${esc(eff)}</span>`)}
          ${fieldRow("Unanswered threshold (h)", `<input name="unanswered_hours" type="number" min="0.1" step="0.5" value="${esc(r.unanswered_hours)}" />`)}
          <div class="form-row"><label>Penalty amount</label>
            <div class="amount-input">
              <select name="currency">${["INR", "AED", "USD", "EUR", "GBP"].map((c) => `<option${c === r.currency ? " selected" : ""}>${c}</option>`).join("")}</select>
              <input name="penalty_amount" type="text" value="${esc(r.penalty_amount)}" placeholder="0.00" />
            </div>
          </div>
          ${fieldRow("Status", statusSelect("status", r.status))}
          ${fieldRow("Applies to statuses", multiSelect("applies_to_statuses", STATUS_OPTIONS, r.applies_to_statuses ?? []))}
        </div>
        <div class="detail-card">
          <h3>Scope &amp; validity</h3>
          ${fieldRow("Tenant", `<select name="tenant_id"><option value="">any tenant</option>${TENANT_OPTIONS.map((t) => `<option value="${t}"${t === r.tenant_id ? " selected" : ""}>${esc(tenantLabel(t))}</option>`).join("")}</select>`)}
          ${fieldRow("Region", `<select name="region"><option value="">any</option>${["IN", "UAE"].map((g) => `<option${g === r.region ? " selected" : ""}>${g}</option>`).join("")}</select>`)}
          ${fieldRow("Active from", `<input name="valid_from" type="date" value="${esc(r.valid_from ?? "")}" />`)}
          ${fieldRow("Deactivates", `<input name="valid_to" type="date" value="${esc(r.valid_to ?? "")}" />`)}
          ${fieldRow("Description", textInput("description", r.description, "What this tier covers"))}
        </div>
      </div>
      <div class="form-actions">
        <button type="submit">Save changes</button>
        <span id="rule-save-status" class="muted"></span>
      </div>
    </form>`);
}

$("rules-tbody").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-rule]");
  if (btn) openRoutingRule(btn.dataset.rule);
});
$("sla-rules-tbody").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-rule]");
  if (btn) openSlaRule(btn.dataset.rule);
});

document.addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  if (form.id !== "rule-form") return;
  const kind = form.dataset.kind;
  const id = form.dataset.id;
  const fd = new FormData(form);
  const payload = {};
  for (const [k, v] of fd.entries()) {
    if (k === "applies_to_statuses") continue; // handled below
    if (v === "") continue; // empty = leave unchanged
    if (k === "unanswered_hours") payload[k] = parseFloat(v);
    else payload[k] = v;
  }
  if (kind === "sla") {
    const checked = [...form.querySelectorAll('input[name="applies_to_statuses"]:checked')].map((c) => c.value);
    payload.applies_to_statuses = checked;
  }
  const url = kind === "sla"
    ? `/v1/dashboard/sla/rules/${encodeURIComponent(id)}`
    : `/v1/routing/rules/${encodeURIComponent(id)}`;
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const status = form.querySelector("#rule-save-status") ?? $("rule-save-status");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    status.textContent = `Save failed: ${err.detail ?? res.status}`;
    status.style.color = "#dc2626";
    return;
  }
  status.textContent = "Saved ✓";
  status.style.color = "#16a34a";
  await Promise.all([loadPolicy(), loadSlaRules(), loadSla()]);
});

/* ---------- ticket intake payload ---------- */

function b64(s) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(s)));
}

function seedPayload(tenant, ticketId, subject, body, extra = {}, ageHours = null, status = "open", priority = null) {
  const raw = JSON.stringify({
    ticket_id: ticketId, case_id: `CASE-${ticketId}`, subject, body,
    ...(priority ? { priority: priority.toUpperCase() } : {}), ...extra,
  });
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    principal_id: "demo-seed",
    channel_account_id: "widget-in",
    ingress_region: "IN",
    authenticated_tenant_id: tenant,
    verified_contact_id: tenant === "C-1001" ? "contact-a" : null,
    source: "widget",
    source_event_id: `seed-${ticketId}-${Date.now()}`,
    received_at: now,
    source_occurred_at: now,
    payload_schema: "widget/v1",
    raw_body_base64: b64(raw),
    parsed_payload: JSON.parse(raw),
    attachments: [],
    p1: priority === "p1",
    priority,
    ticket_status: status,
    ticket_age_hours: ageHours,
  };
}

/* ---------- ticket intake ---------- */

$("intake-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("btn-intake");
  const out = $("intake-result");
  const tenant = $("intake-tenant").value;
  const tid = $("intake-ticket-id").value.trim() ||
    `PN-U${Date.now().toString(36).toUpperCase()}`;
  const subject = $("intake-subject").value.trim();
  const bodyText = $("intake-body").value.trim();
  const age = parseFloat($("intake-age").value) || null;
  const status = $("intake-status").value;
  const priority = $("intake-priority").value || null;
  if (!tenant || !subject || !bodyText) {
    out.textContent = "Tenant, subject and body are required.";
    return;
  }
  btn.disabled = true;
  out.textContent = "Processing…";
  const res = await jpost("/v1/pipeline/process",
    seedPayload(tenant, tid, subject, bodyText, {}, age, status, priority));
  btn.disabled = false;
  if (!res.ok) {
    out.textContent = `Failed: ${res.body?.detail ?? res.status}`;
    return;
  }
  const d = res.body;
  out.innerHTML = `Ticket <strong>${esc(d.ticket_id)}</strong> · lane ` +
    `<strong>${esc((d.lane ?? "").replaceAll("_", " "))}</strong> · rule ` +
    `<code>${esc(d.rule_id ?? "—")}</code> · auto_route=${d.auto_route}`;
  await loadDashboard();
  await loadSla();
  await openTicket(tenant, d.ticket_id);
});

/* ---------- wiring ---------- */

$("btn-refresh").addEventListener("click", () => {
  pollHealth();
  loadDashboard();
  loadPolicy();
  loadSla();
  loadAnalytics();
});
$("db-tenant").addEventListener("change", loadDashboard);
$("db-channel").addEventListener("change", () => {
  channelFilter = $("db-channel").value || null;
  loadDashboard();
});
$("sla-tenant-filter").addEventListener("change", loadSla);
$("home-tenant").addEventListener("change", loadAnalytics);
$("ch-tenant").addEventListener("change", loadChannels);
$("btn-home-refresh").addEventListener("click", loadAnalytics);
$("btn-ch-refresh").addEventListener("click", loadChannels);

setInterval(() => {
  if ($("db-auto").checked && !document.querySelector(".spa-detail:not([hidden])")) {
    loadDashboard();
    loadSla();
    if (!$("tab-home").hidden) loadAnalytics();
    if (!$("tab-channels").hidden) loadChannels();
  }
  pollHealth();
}, REFRESH_MS);

pollHealth();
loadDashboard();
loadAnalytics();
loadChannels();
loadPolicy();
loadSla();
loadSlaRules();
