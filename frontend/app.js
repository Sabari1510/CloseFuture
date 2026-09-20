/* Chat widget (index.html) + admin workbench (admin.html), one script.
   Public page shows chat only; session IDs, API URL and trace/sweep live on
   the admin page. Admin endpoints stay key-gated server-side. No secrets in
   code — the admin key lives in sessionStorage (tab lifetime). */
const $ = (id) => document.getElementById(id);
const log = $("cf-log"), form = $("cf-form"), input = $("cf-input");
const store = {
  get api() { return localStorage.getItem("cf_api") || "http://localhost:8000"; },
  get visitor() { return localStorage.getItem("cf_visitor"); },
  get session() { return localStorage.getItem("cf_session"); },
  get admin() { return sessionStorage.getItem("cf_admin") || ""; },
};
let pending = false;

function bubble(cls, text, badge) {
  if (!log) return null;
  const d = document.createElement("div");
  d.className = cls;
  const b = document.createElement("div");
  b.className = "txt";
  b.textContent = text;
  d.appendChild(b);
  if (badge) {
    const s = document.createElement("span");
    s.className = "badge";
    s.textContent = badge;
    d.appendChild(s);
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

async function ping() {
  /* Backend reachability: poll + browser signals + send outcomes all feed
     the same pill, so it never goes stale. */
  setConn("checking");
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 5000);
    const r = await fetch(`${store.api}/healthz`, {signal: ctl.signal});
    clearTimeout(t);
    setConn(r.ok ? "online" : "offline");
  } catch { setConn("offline"); }
}

function setConn(state) {
  const c = $("conn");
  if (!c) return;
  c.className = state;
  const txt = $("conn-txt");
  if (txt) txt.textContent = state === "online" ? "Online"
    : state === "offline" ? "Offline" : "Checking…";
  c.title = state === "offline"
    ? "Backend unreachable — check it runs and this origin is in ALLOWED_ORIGINS"
    : "Backend connection";
}

function beacon(state) {
  /* Tab presence: closing/hiding starts the away-grace clock server-side so
     the lead summary goes out minutes later; returning cancels it. $0. */
  if (!store.session || !store.visitor) return;
  const url = `${store.api}/v1/sessions/${store.session}/presence`;
  const body = JSON.stringify({visitor_id: store.visitor, state});
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url, new Blob([body], {type: "application/json"}));
  } else {
    fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
                body, keepalive: true}).catch(() => {});
  }
}

function syncFields() {
  if ($("f-visitor")) $("f-visitor").value = store.visitor || "";
  if ($("f-session")) $("f-session").value = store.session || "";
  if ($("f-api")) $("f-api").value = store.api;
  if ($("f-admin")) $("f-admin").value = store.admin;
}

async function restore() {
  if (!log) return;
  log.innerHTML = "";
  if (!store.session || !store.visitor) {
    bubble("a", "Hi — ask me about CloseFuture's services, case studies and rates, or book a discovery call.");
    return;
  }
  try {
    const r = await fetch(`${store.api}/v1/sessions/${store.session}/messages?visitor_id=${store.visitor}`);
    if (!r.ok) throw 0;
    const msgs = (await r.json()).messages;
    if (!msgs.length) throw 0;
    for (const m of msgs) bubble(m.role === "visitor" ? "v" : "a", m.content);
  } catch {
    bubble("a", "Hi — ask me about CloseFuture's services, case studies and rates, or book a discovery call.");
  }
}

async function send(text) {
  if (pending || !text.trim()) return;
  pending = true;
  $("send").disabled = true;
  bubble("v", text.trim());
  input.value = "";
  $("count").textContent = "0/500";
  const typing = bubble("a typing", "…");
  try {
    const r = await fetch(`${store.api}/v1/chat`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        message: text.trim().slice(0, 500),
        visitor_id: store.visitor,
        session_id: store.session,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    });
    const b = await r.json();
    localStorage.setItem("cf_visitor", b.visitor_id);
    localStorage.setItem("cf_session", b.session_id);
    typing.remove();
    setConn("online");
    const labels = {blocked: "blocked by guardrail", rate_limited: "rate limited",
                    degraded: "degraded", busy: "busy — retry"};
    bubble("a", b.status === "rate_limited" && b.retry_after_seconds
      ? `Please wait a moment… (retry in ${b.retry_after_seconds}s)` : b.reply,
      b.status === "ok" ? "" : (labels[b.status] || b.status));
  } catch {
    typing.remove();
    setConn("offline");
    bubble("a", "Couldn't reach the assistant — is the backend running?", "offline");
  } finally {
    pending = false;
    $("send").disabled = false;
    syncFields();
    ping();
  }
}

async function viewTrace() {
  const out = $("admin-out");
  out.textContent = "loading…";
  try {
    const r = await fetch(`${store.api}/v1/sessions/${store.session}/trace`, {
      headers: {"X-Admin-Key": $("f-admin").value},
    });
    if (!r.ok) throw new Error(r.status === 403 ? "wrong admin key" : `HTTP ${r.status}`);
    sessionStorage.setItem("cf_admin", $("f-admin").value);
    const evs = (await r.json()).events;
    out.innerHTML = "";
    const order = ["guardrail_check", "route_decision", "agent_call", "tool_call",
                   "tool_result", "retry", "fallback", "email_sent", "state_write"];
    for (const e of evs) {
      const d = document.createElement("div");
      d.className = "ev";
      const agent = e.agent ? ` · ${e.agent}` : "";
      const short = JSON.stringify(e.payload).slice(0, 220);
      d.innerHTML = `<b>#${e.seq} ${e.type}</b><span>${agent}</span><code>${short}</code>`;
      if (!order.includes(e.type)) d.classList.add("other");
      out.appendChild(d);
    }
    if (!evs.length) out.textContent = "no events yet — send a message first.";
  } catch (err) { out.textContent = `trace failed: ${err.message}`; }
}

async function sweepStatus() {
  const out = $("admin-out");
  try {
    const r = await fetch(`${store.api}/internal/sweep/status`, {
      headers: {"X-Admin-Key": $("f-admin").value},
    });
    if (!r.ok) throw new Error(r.status === 403 ? "wrong admin key" : `HTTP ${r.status}`);
    sessionStorage.setItem("cf_admin", $("f-admin").value);
    const s = await r.json();
    const last = s.last_run
      ? `last run ${s.last_run}: ${JSON.stringify(s.last_result)}`
      : "no run yet";
    $("sweep-info").textContent =
      `Auto-sweep every ${s.interval_min} min (${s.auto ? "on" : "off"}); ${last}.`;
    out.textContent = "";
  } catch (err) { out.textContent = `status failed: ${err.message}`; }
}

async function runSweep() {
  const out = $("admin-out");
  out.textContent = "sweeping…";
  try {
    const r = await fetch(`${store.api}/internal/sweep`, {
      method: "POST", headers: {"X-Admin-Key": $("f-admin").value},
    });
    if (!r.ok) throw new Error(r.status === 403 ? "wrong admin key" : `HTTP ${r.status}`);
    sessionStorage.setItem("cf_admin", $("f-admin").value);
    out.textContent = `sweep done: ${JSON.stringify(await r.json())}`;
  } catch (err) { out.textContent = `sweep failed: ${err.message}`; }
}

async function loadSessions() {
  /* Company-side discovery: newest conversations with status flags.
     Each row loads that chat (or its trace) for review. */
  const out = $("sess-out");
  out.textContent = "loading…";
  try {
    const r = await fetch(`${store.api}/internal/sessions?limit=50`, {
      headers: {"X-Admin-Key": $("f-admin").value},
    });
    if (!r.ok) throw new Error(r.status === 403 ? "wrong admin key" : `HTTP ${r.status}`);
    sessionStorage.setItem("cf_admin", $("f-admin").value);
    const rows = (await r.json()).sessions;
    out.innerHTML = "";
    if (!rows.length) { out.textContent = "no conversations yet."; return; }
    const t = document.createElement("table");
    t.className = "sess";
    t.innerHTML = "<tr><th>Last active</th><th>Status</th><th>Turns</th>" +
      "<th>Booked</th><th>Email</th><th>Summary</th><th></th></tr>";
    for (const s of rows) {
      const tr = document.createElement("tr");
      const when = new Date(s.last_activity * 1000).toLocaleString();
      tr.innerHTML = `<td>${when}</td><td>${s.status}</td><td>${s.turns}</td>` +
        `<td>${s.booked ? "yes" : "no"}</td><td>${s.email_known ? "yes" : "no"}</td>` +
        `<td>${s.summary_sent ? "sent" : "pending"}</td><td></td>`;
      const btn = document.createElement("button");
      btn.textContent = "Open";
      btn.addEventListener("click", () => {
        localStorage.setItem("cf_visitor", s.visitor_id);
        localStorage.setItem("cf_session", s.session_id);
        window.location.href = "index.html";
      });
      tr.lastChild.appendChild(btn);
      t.appendChild(tr);
    }
    out.appendChild(t);
  } catch (err) { out.textContent = `sessions failed: ${err.message}`; }
}

async function endChat(email) {
  /* Close the session: no email known -> server asks once (need-email);
     otherwise the complete lead summary is sent now. Idempotent. */
  if (!store.session || !store.visitor) {
    bubble("a", "There's no active chat to end — say hi to start one.");
    return;
  }
  try {
    const r = await fetch(`${store.api}/v1/sessions/${store.session}/end`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({visitor_id: store.visitor, email: email || null}),
    });
    const b = await r.json();
    if (b.status === "need-email") {
      bubble("a", b.reply);
      const row = $("end-email");
      if (row) row.hidden = false;
    } else {
      bubble("a", b.reply || "Chat ended — the team will follow up by email.", "ended");
      const row = $("end-email");
      if (row) row.hidden = true;
      if (form) {
        $("cf-input").disabled = true;
        $("send").disabled = true;
      }
    }
  } catch {
    bubble("a", "Couldn't reach the assistant — is the backend running?", "offline");
  }
}

function onNewChat() {
  localStorage.removeItem("cf_session");
  if ($("cf-input")) { $("cf-input").disabled = false; $("send").disabled = false; }
  const row = $("end-email");
  if (row) row.hidden = true;
  syncFields();
  restore();
}

if (form) {
  form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  input.addEventListener("input", () => { $("count").textContent = `${input.value.length}/500`; });
  document.querySelectorAll("#quick button").forEach((b) =>
    b.addEventListener("click", () => send(b.dataset.q)));
}
if ($("btn-new")) $("btn-new").addEventListener("click", onNewChat);
if ($("btn-restore")) $("btn-restore").addEventListener("click", restore);
if ($("btn-load")) $("btn-load").addEventListener("click", () => {
  /* Review an old conversation: paste its IDs, jump to the chat page which
     restores it (visitor-scoped: IDs must match or the API returns 403). */
  const v = $("f-visitor").value.trim(), s = $("f-session").value.trim();
  if (!v || !s) { $("admin-out").textContent = "paste both IDs first."; return; }
  localStorage.setItem("cf_visitor", v);
  localStorage.setItem("cf_session", s);
  window.location.href = "index.html";
});
if ($("btn-end")) $("btn-end").addEventListener("click", () => endChat(null));
if ($("btn-end-confirm")) $("btn-end-confirm").addEventListener("click", () => {
  const v = $("end-email-input").value.trim();
  if (!v || !v.includes("@")) { $("end-email-input").focus(); return; }
  endChat(v);
});
if ($("btn-api")) $("btn-api").addEventListener("click", () => {
  const v = $("f-api").value.trim().replace(/\/$/, "");
  if (v) localStorage.setItem("cf_api", v);
  syncFields(); ping();
});
if ($("btn-trace")) $("btn-trace").addEventListener("click", viewTrace);
if ($("btn-sweep")) $("btn-sweep").addEventListener("click", async () => { await runSweep(); sweepStatus(); });
if ($("btn-sweep-status")) $("btn-sweep-status").addEventListener("click", sweepStatus);
if ($("btn-sessions")) $("btn-sessions").addEventListener("click", loadSessions);

syncFields();
restore();
ping();
setInterval(() => { if (!document.hidden) ping(); }, 15000);
window.addEventListener("online", ping);
window.addEventListener("offline", () => setConn("offline"));
document.addEventListener("visibilitychange", () => {
  beacon(document.hidden ? "away" : "here");
});
window.addEventListener("pagehide", () => beacon("away"));
