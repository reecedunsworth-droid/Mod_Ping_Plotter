/* PathPulse front-end controller.
 * Manages WebSocket live updates, target tabs, the hop table, charts,
 * controls (interval/pause/window/alerts), and CSV/PNG export.
 */
(function () {
  "use strict";

  const state = {
    targets: new Map(),   // target -> snapshot
    active: null,
    window: "1h",
    windows: { "10m": 600, "1h": 3600, "6h": 21600, "24h": 86400 },
    selectedHop: null,
    destChart: null,
    hopChart: null,
    ws: null,
    alertTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  const enc = encodeURIComponent;

  // ---- API helpers ----
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
  async function sendJSON(url, method, body) {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  // ---- WebSocket ----
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    state.ws = ws;
    ws.onopen = () => setWsDot(true);
    ws.onclose = () => { setWsDot(false); setTimeout(connectWS, 1500); };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "init") {
        (msg.targets || []).forEach((s) => state.targets.set(s.target, s));
        if (!state.active && msg.targets && msg.targets.length) {
          selectTarget(msg.targets[0].target);
        }
        renderTabs();
        if (state.active) renderActive();
      } else if (msg.type === "update") {
        applyUpdate(msg.snapshot);
      }
    };
  }
  function setWsDot(on) {
    const d = $("ws-dot");
    d.className = "dot " + (on ? "dot-on" : "dot-off");
  }

  // ---- update handling ----
  // Accept a snapshot only if it is newer than what we already hold, so an
  // out-of-order WebSocket message can never regress a control action.
  function acceptSnap(snap) {
    const prev = state.targets.get(snap.target);
    if (prev && snap.version != null && prev.version != null &&
        snap.version < prev.version) {
      return false;
    }
    state.targets.set(snap.target, snap);
    return true;
  }

  function applyUpdate(snap) {
    if (!acceptSnap(snap)) return;
    updateModeBadge(snap.mode);
    renderTabs();
    if (snap.target === state.active) {
      renderActive();
      pushLivePoint(snap);
      if (snap.alert_event) {
        const e = snap.alert_event;
        toast(`${e.target}: ${e.message}`, e.kind === "alert" ? "err" : "ok");
      }
    } else if (snap.alert_event) {
      const e = snap.alert_event;
      toast(`${e.target}: ${e.message}`, e.kind === "alert" ? "err" : "ok");
    }
  }

  function pushLivePoint(snap) {
    if (!state.destChart || snap.last_dest_rtt == null || !snap.last_pass_ts) return;
    const destHop = snap.hops && snap.hops.length ? snap.hops[snap.hops.length - 1] : null;
    state.destChart.pushPoint({
      ts: snap.last_pass_ts,
      rtt: snap.last_dest_rtt,
      loss: destHop ? destHop.loss_pct : 0,
    }, state.windows[state.window]);
    if (state.selectedHop != null && state.hopChart) {
      const hop = (snap.hops || []).find((h) => h.ttl === state.selectedHop);
      if (hop) state.hopChart.pushPoint({
        ts: snap.last_pass_ts, rtt: hop.current, loss: hop.loss_pct,
      }, state.windows[state.window]);
    }
  }

  function updateModeBadge(mode) {
    if (!mode) return;
    const b = $("mode-badge");
    b.textContent = mode === "raw" ? "raw ICMP" : mode;
    b.className = "badge " + (mode === "raw" ? "badge-ok" : "badge-warn");
    b.title = mode === "raw"
      ? "Privileged raw sockets: full per-hop detail"
      : "Unprivileged mode: hop detail is best-effort (run with sudo for full detail)";
  }

  // ---- tabs ----
  function worstColor(snap) {
    if (snap.status === "error") return "red";
    if (!snap.hops || !snap.hops.length) return "muted";
    const order = { green: 0, yellow: 1, red: 2 };
    let worst = "green";
    for (const h of snap.hops) if (order[h.color] > order[worst]) worst = h.color;
    if (!snap.dest_reached) worst = "red";
    return worst;
  }

  function renderTabs() {
    const nav = $("tabs");
    nav.innerHTML = "";
    for (const snap of state.targets.values()) {
      const tab = document.createElement("div");
      tab.className = "tab" + (snap.target === state.active ? " active" : "");
      const dot = document.createElement("span");
      dot.className = "tab-dot c-" + worstColor(snap);
      const label = document.createElement("span");
      label.textContent = snap.target;
      label.className = "tab-label";
      const rtt = document.createElement("span");
      rtt.className = "tab-rtt";
      rtt.textContent = snap.last_dest_rtt != null ? `${snap.last_dest_rtt.toFixed(0)}ms`
                        : (snap.status === "error" ? "err" : "…");
      const close = document.createElement("button");
      close.className = "tab-close";
      close.textContent = "×";
      close.title = "remove";
      close.onclick = (e) => { e.stopPropagation(); removeTarget(snap.target); };
      tab.append(dot, label, rtt, close);
      tab.onclick = () => selectTarget(snap.target);
      nav.appendChild(tab);
    }
  }

  // ---- active target rendering ----
  function selectTarget(target) {
    state.active = target;
    state.selectedHop = null;
    $("hop-chart-card").hidden = true;
    $("empty").hidden = true;
    $("target-view").hidden = false;
    ensureCharts();
    renderTabs();
    renderActive();
    loadDestSeries();
  }

  function ensureCharts() {
    if (!state.destChart) state.destChart = new TimeChart($("dest-chart"));
    if (!state.hopChart) state.hopChart = new TimeChart($("hop-chart"));
  }

  function renderActive() {
    const snap = state.targets.get(state.active);
    if (!snap) return;
    $("tv-target").textContent = snap.target;
    $("tv-destip").textContent = snap.dest_ip || "";
    const st = $("tv-status");
    st.textContent = snap.status || "";
    st.className = "badge " + statusClass(snap.status);
    const md = $("tv-mode");
    md.textContent = snap.mode || "—";
    md.className = "badge badge-muted";

    const err = $("err-banner");
    if (snap.error) { err.hidden = false; err.textContent = snap.error; }
    else err.hidden = true;

    if (document.activeElement !== $("interval")) {
      $("interval").value = snap.interval != null ? snap.interval : 2.5;
    }
    $("dest-label").textContent = snap.dest_reached
      ? `→ ${snap.dest_ip}` : "(destination not reached)";
    $("btn-pause").textContent = snap.status === "paused" ? "Resume" : "Pause";
    $("hop-summary").textContent = snap.hop_count ? `(${snap.hop_count} hops)` : "";

    renderHopTable(snap);
    renderAlertFields(snap.alert);
    loadEvents();
  }

  function statusClass(s) {
    if (s === "running") return "badge-ok";
    if (s === "error") return "badge-err";
    if (s === "paused") return "badge-warn";
    return "badge-muted";
  }

  function renderHopTable(snap) {
    const body = $("hops-body");
    body.innerHTML = "";
    for (const h of snap.hops || []) {
      const tr = document.createElement("tr");
      tr.className = "hop-row" + (h.ttl === state.selectedHop ? " selected" : "");
      const host = h.hostname ? h.hostname : (h.address || "*");
      const sub = h.hostname && h.address ? h.address : "";
      const asn = h.asn ? `AS${h.asn}${h.as_name ? " " + shorten(h.as_name) : ""}`
                        : (h.private ? "private" : "");
      tr.innerHTML =
        `<td class="num">${h.ttl}</td>` +
        `<td><span class="dot-sm c-${h.color}"></span>` +
        `<span class="hop-host">${escapeHtml(host)}</span>` +
        (sub ? `<span class="hop-ip">${escapeHtml(sub)}</span>` : "") + `</td>` +
        `<td class="asn">${escapeHtml(asn)}</td>` +
        `<td class="num c-text-${h.color}">${h.loss_pct.toFixed(1)}%</td>` +
        `<td class="num">${fmt(h.current)}</td>` +
        `<td class="num">${fmt(h.avg)}</td>` +
        `<td class="num">${fmt(h.min)}</td>` +
        `<td class="num">${fmt(h.max)}</td>` +
        `<td class="num">${fmt(h.jitter)}</td>`;
      tr.onclick = () => selectHop(h.ttl);
      body.appendChild(tr);
    }
  }

  function renderAlertFields(alert) {
    if (!alert) return;
    if (document.activeElement && document.activeElement.closest(".alert-card")) return;
    $("alert-enabled").checked = !!alert.enabled;
    $("alert-latency").value = alert.latency_ms;
    $("alert-loss").value = alert.loss_pct;
    $("alert-consec").value = alert.consecutive;
  }

  // ---- hop selection + charts ----
  function selectHop(ttl) {
    state.selectedHop = ttl;
    $("hop-chart-card").hidden = false;
    $("hop-chart-label").textContent = "#" + ttl;
    const snap = state.targets.get(state.active);
    renderHopTable(snap);
    loadHopSeries(ttl);
  }

  async function loadDestSeries() {
    if (!state.active) return;
    try {
      const data = await getJSON(
        `/api/targets/${enc(state.active)}/dest_series?window=${state.window}`);
      state.destChart.setData(data.series, state.windows[state.window]);
    } catch (e) { /* target may not be ready yet */ }
  }

  async function loadHopSeries(ttl) {
    try {
      const data = await getJSON(
        `/api/targets/${enc(state.active)}/hop_series?ttl=${ttl}&window=${state.window}`);
      state.hopChart.setData(data.series, state.windows[state.window]);
    } catch (e) { /* ignore */ }
  }

  async function loadEvents() {
    if (!state.active) return;
    try {
      const data = await getJSON(`/api/targets/${enc(state.active)}/events?limit=30`);
      const ul = $("events-list");
      ul.innerHTML = "";
      if (!data.events.length) {
        ul.innerHTML = '<li class="ev-empty">No events yet.</li>';
        return;
      }
      for (const e of data.events) {
        const li = document.createElement("li");
        li.className = "ev ev-" + e.kind;
        const t = new Date(e.ts * 1000).toLocaleTimeString();
        li.innerHTML = `<span class="ev-time">${t}</span> ` +
                       `<span class="ev-kind">${e.kind}</span> ${escapeHtml(e.message)}`;
        ul.appendChild(li);
      }
    } catch (e) { /* ignore */ }
  }

  // ---- controls ----
  async function control(action, extra) {
    if (!state.active) return;
    try {
      const res = await sendJSON(`/api/targets/${enc(state.active)}/control`, "POST",
                                 Object.assign({ action }, extra || {}));
      // A paused monitor emits no WS updates, so apply the response snapshot
      // directly to keep the UI in sync with the control action.
      if (res && res.snapshot && acceptSnap(res.snapshot)) {
        renderTabs();
        if (res.snapshot.target === state.active) renderActive();
      }
    } catch (e) { toast("control failed: " + e.message, "err"); }
  }

  async function addTarget(target) {
    try {
      const res = await sendJSON("/api/targets", "POST", { target });
      acceptSnap(res.snapshot);
      renderTabs();
      selectTarget(target);
    } catch (e) { toast("could not add: " + e.message, "err"); }
  }

  async function removeTarget(target) {
    try {
      await fetch(`/api/targets/${enc(target)}`, { method: "DELETE" });
    } catch (e) { /* ignore */ }
    state.targets.delete(target);
    if (state.active === target) {
      state.active = null;
      const first = state.targets.keys().next().value;
      if (first) selectTarget(first);
      else { $("target-view").hidden = true; $("empty").hidden = false; loadHistory(); }
    }
    renderTabs();
  }

  async function loadHistory() {
    try {
      const data = await getJSON("/api/history");
      const past = (data.monitors || []).filter((m) => !m.active);
      const block = $("history-block");
      const chips = $("history-chips");
      chips.innerHTML = "";
      if (!past.length) { block.hidden = true; return; }
      block.hidden = false;
      for (const m of past) {
        const chip = document.createElement("button");
        chip.className = "history-chip";
        chip.textContent = m.target;
        chip.title = `resume monitoring ${m.target}` +
                     (m.dest_ip ? ` (${m.dest_ip})` : "");
        chip.onclick = () => addTarget(m.target);
        chips.appendChild(chip);
      }
    } catch (e) { /* ignore */ }
  }

  function setWindow(w) {
    state.window = w;
    document.querySelectorAll(".win").forEach((b) =>
      b.classList.toggle("active", b.dataset.w === w));
    loadDestSeries();
    if (state.selectedHop != null) loadHopSeries(state.selectedHop);
  }

  // ---- utils ----
  function fmt(v) { return v == null ? "—" : v.toFixed(1); }
  function shorten(s) { return s.length > 22 ? s.slice(0, 21) + "…" : s; }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast toast-" + (kind || "info");
    el.textContent = msg;
    $("toasts").appendChild(el);
    setTimeout(() => el.classList.add("show"), 10);
    setTimeout(() => { el.classList.remove("show");
      setTimeout(() => el.remove(), 300); }, 5000);
  }

  // ---- wire up DOM ----
  function init() {
    $("add-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("add-input").value.trim();
      if (v) { addTarget(v); $("add-input").value = ""; }
    });
    $("windows").addEventListener("click", (e) => {
      if (e.target.dataset.w) setWindow(e.target.dataset.w);
    });
    $("interval").addEventListener("change", (e) =>
      control("set_interval", { seconds: parseFloat(e.target.value) }));
    $("btn-pause").addEventListener("click", () => {
      const snap = state.targets.get(state.active);
      control(snap && snap.status === "paused" ? "resume" : "pause");
    });
    $("btn-remove").addEventListener("click", () => {
      if (state.active) removeTarget(state.active);
    });
    $("btn-csv").addEventListener("click", () => {
      if (state.active)
        window.location = `/api/targets/${enc(state.active)}/export.csv?window=${state.window}`;
    });
    $("btn-png").addEventListener("click", () => {
      if (state.destChart)
        state.destChart.toPNG(`pathpulse_${state.active}_${Date.now()}.png`);
    });
    $("hop-chart-close").addEventListener("click", () => {
      state.selectedHop = null;
      $("hop-chart-card").hidden = true;
      const snap = state.targets.get(state.active);
      if (snap) renderHopTable(snap);
    });

    const alertChange = () => {
      clearTimeout(state.alertTimer);
      state.alertTimer = setTimeout(() => {
        control("set_alert", {
          enabled: $("alert-enabled").checked,
          latency_ms: parseFloat($("alert-latency").value),
          loss_pct: parseFloat($("alert-loss").value),
          consecutive: parseInt($("alert-consec").value, 10),
        });
      }, 400);
    };
    ["alert-enabled", "alert-latency", "alert-loss", "alert-consec"]
      .forEach((id) => $(id).addEventListener("change", alertChange));

    connectWS();
    loadHistory();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
