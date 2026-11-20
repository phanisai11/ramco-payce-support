/**
 * Same-origin fetches against FastAPI (MDN fetch: https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API)
 */

function $(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
}

async function showJson(preId, promise) {
  const pre = $(preId);
  pre.textContent = "…";
  try {
    const res = await promise;
    const text = await res.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
    pre.textContent = JSON.stringify(
      { ok: res.ok, status: res.status, body },
      null,
      2,
    );
  } catch (e) {
    pre.textContent = String(e);
  }
}

function utf8ToBase64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  bytes.forEach((b) => {
    bin += String.fromCharCode(b);
  });
  return btoa(bin);
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function setDefaultSignalTime() {
  const d = new Date();
  const local = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  $("in-when").value = local;
}

document.getElementById("btn-health")?.addEventListener("click", () => {
  showJson("out-health", fetch("/health"));
});

document.getElementById("btn-statutory")?.addEventListener("click", () => {
  const region = $("st-region").value;
  const topic = $("st-topic").value;
  const as_of = $("st-asof").value;
  const qs = new URLSearchParams({ region, topic, as_of });
  showJson("out-statutory", fetch(`/v1/statutory/resolve?${qs}`));
});

document.getElementById("btn-routing")?.addEventListener("click", () => {
  const payload = {
    tenant_id: $("rt-tenant").value.trim(),
    text: $("rt-text").value,
    region: $("rt-region").value.trim(),
    p1: $("rt-p1").checked,
    assist: $("rt-assist").checked,
  };
  showJson(
    "out-routing",
    fetch("/v1/routing/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
});

document.getElementById("btn-incident")?.addEventListener("click", () => {
  const local = $("in-when").value;
  if (!local) setDefaultSignalTime();
  const iso = new Date(($("in-when").value || "").replace(" ", "T")).toISOString();
  const payload = {
    tenant_id: $("in-tenant").value.trim(),
    channel: $("in-channel").value.trim(),
    received_at: iso,
    error_code: $("in-code").value.trim() || null,
    subject: $("in-subj").value,
    body: $("in-body").value,
  };
  showJson(
    "out-incident",
    fetch("/v1/incident/signals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
});

document.getElementById("btn-intake")?.addEventListener("click", () => {
  let seid = $("in-seid").value.trim();
  if (!seid) {
    seid = `dev-ui-${crypto.randomUUID()}`;
    $("in-seid").value = seid;
  }
  const rawStr = $("in-raw").value;
  const now = new Date();
  const received = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  const payload = {
    principal_id: $("in-principal").value.trim(),
    channel_account_id: $("in-chacc").value.trim(),
    ingress_region: "IN",
    authenticated_tenant_id: $("in-tenant2").value.trim(),
    verified_contact_id: null,
    source: "widget",
    source_event_id: seid,
    received_at: received,
    source_occurred_at: received,
    payload_schema: "widget/v1",
    raw_body_base64: utf8ToBase64(rawStr),
    parsed_payload: JSON.parse(rawStr),
    attachments: [],
  };
  showJson(
    "out-intake",
    fetch("/v1/intake/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
});

/* ---------- Dashboard ---------- */

const STATUS_OPTIONS = ["open", "in_progress", "pending_customer", "resolved", "closed"];

async function loadDashboard() {
  const tenant = $("db-tenant").value.trim();
  const qs = tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : "";
  const res = await fetch(`/v1/dashboard/summary${qs}`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    $("db-cards").innerHTML = `<p class="muted">${body.detail || res.status}</p>`;
    $("db-tbody").innerHTML = "";
    return;
  }
  const cards = [`<div class="card"><b>${body.total}</b><span>total</span></div>`];
  for (const [status, count] of Object.entries(body.by_status)) {
    cards.push(`<div class="card"><b>${count}</b><span>${status}</span></div>`);
  }
  $("db-cards").innerHTML = cards.join("");

  const tbody = $("db-tbody");
  tbody.innerHTML = "";
  for (const t of body.tickets) {
    const tr = document.createElement("tr");
    const opts = STATUS_OPTIONS.map(
      (s) => `<option value="${s}"${s === t.status ? " selected" : ""}>${s}</option>`,
    ).join("");
    tr.innerHTML = `
      <td>${t.ticket_id}</td>
      <td>${t.tenant_id}</td>
      <td>${t.queue_id ?? "—"}</td>
      <td><select class="status-sel" data-tenant="${t.tenant_id}" data-ticket="${t.ticket_id}">${opts}</select></td>
      <td>v${t.canonical_version}</td>
      <td class="muted">${new Date(t.updated_at).toLocaleString()}</td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll(".status-sel").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const tenant = sel.dataset.tenant;
      const ticket = sel.dataset.ticket;
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
    });
  });
}

document.getElementById("btn-dashboard")?.addEventListener("click", loadDashboard);

/* ---------- LLM routing assist (via /v1/routing/classify) ---------- */

document.getElementById("btn-llm")?.addEventListener("click", () => {
  showJson(
    "out-llm",
    fetch("/v1/routing/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tenant_id: $("llm-tenant").value.trim(),
        text: $("llm-text").value,
        region: $("llm-region").value,
        p1: false,
        assist: true,
      }),
    }),
  );
});

setDefaultSignalTime();
loadDashboard();
