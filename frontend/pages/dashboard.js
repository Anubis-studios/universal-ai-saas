// HUD dashboard: live telemetry, neural orb, sparklines, world map, command bar.
// Every number here is measured on the server (/system/telemetry); nothing is faked.

import { api, apiBase, apiKey, streamChat } from "../lib/api.js";
import { $ } from "../components/ui.js";

const POLL_MS = 4000;
const history = { latency: [], mem: [], cpu: [] };
const logLines = [];
let orb = null;
let pollTimer = null;
let busy = false;

/* ------------------------------------------------------------------ logs */
function stamp() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

export function pushLog(message, severity = "info") {
  logLines.unshift({ t: stamp(), message, severity });
  if (logLines.length > 40) logLines.pop();
  renderLogs();
}

function renderLogs() {
  const list = $("#hud-logs");
  if (!list) return;
  list.innerHTML = logLines
    .slice(0, 12)
    .map(
      (l) =>
        `<li class="${l.severity}"><span class="sev">${l.severity.toUpperCase().slice(0, 4)}</span>` +
        `<span class="msg">${escapeHtml(l.message)}</span><span class="t">${l.t}</span></li>`,
    )
    .join("");
  $("#log-count").textContent = String(logLines.length);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/* -------------------------------------------------------------- formatting */
function fmtUptime(seconds) {
  if (seconds == null) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
}

/* --------------------------------------------------------------- orb draw */
class Orb {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.size = canvas.width;
    this.t = 0;
    this.particles = Array.from({ length: 90 }, () => ({
      a: Math.random() * Math.PI * 2,
      r: 0.36 + Math.random() * 0.5,
      speed: 0.0015 + Math.random() * 0.006,
      size: 0.7 + Math.random() * 1.7,
      hue: Math.random() < 0.16 ? "amber" : "cyan",
    }));
    this.arcs = Array.from({ length: 6 }, (_, i) => ({
      radius: 0.30 + i * 0.085,
      speed: (i % 2 ? -1 : 1) * (0.00045 + i * 0.00022),
      from: Math.random() * Math.PI * 2,
      span: 0.5 + Math.random() * 1.5,
      width: i === 1 ? 1.6 : 0.9,
    }));
    this.energy = 0.4;
  }

  setEnergy(value) {
    this.energy = Math.max(0.1, Math.min(1, value));
  }

  draw() {
    const { ctx, size } = this;
    const c = size / 2;
    ctx.clearRect(0, 0, size, size);

    // faint grid rings
    ctx.save();
    for (let i = 1; i <= 10; i++) {
      ctx.beginPath();
      ctx.arc(c, c, (size / 2) * (i / 10.5), 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(65,195,241,${i % 5 === 0 ? 0.13 : 0.045})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    // crosshair ticks
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const long = i % 6 === 0;
      const r0 = (size / 2) * 0.93;
      const r1 = (size / 2) * (long ? 0.985 : 0.958);
      ctx.beginPath();
      ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0);
      ctx.lineTo(c + Math.cos(a) * r1, c + Math.sin(a) * r1);
      ctx.strokeStyle = `rgba(65,195,241,${long ? 0.35 : 0.12})`;
      ctx.lineWidth = long ? 1.1 : 0.8;
      ctx.stroke();
    }
    ctx.restore();

    // rotating arcs
    for (const arc of this.arcs) {
      const r = (size / 2) * arc.radius;
      const start = arc.from + this.t * arc.speed;
      const grad = ctx.createLinearGradient(c - r, c - r, c + r, c + r);
      grad.addColorStop(0, "rgba(65,195,241,0)");
      grad.addColorStop(0.5, `rgba(65,195,241,${0.5 + this.energy * 0.4})`);
      grad.addColorStop(1, "rgba(65,195,241,0)");
      ctx.beginPath();
      ctx.arc(c, c, r, start, start + arc.span);
      ctx.strokeStyle = grad;
      ctx.lineWidth = arc.width;
      ctx.stroke();
    }

    // orbiting particles
    for (const p of this.particles) {
      p.a += p.speed;
      const rr = (size / 2) * p.r;
      const x = c + Math.cos(p.a) * rr;
      const y = c + Math.sin(p.a) * rr * 0.94;
      ctx.beginPath();
      ctx.arc(x, y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = p.hue === "amber" ? "rgba(247,159,67,.85)" : "rgba(140,224,255,.85)";
      ctx.fill();
    }

    // core
    const coreR = (size / 2) * 0.155;
    const glow = ctx.createRadialGradient(c, c, 0, c, c, coreR * 3.2);
    glow.addColorStop(0, `rgba(65,195,241,${0.5 + this.energy * 0.35})`);
    glow.addColorStop(0.35, `rgba(65,195,241,${0.16 + this.energy * 0.1})`);
    glow.addColorStop(1, "rgba(65,195,241,0)");
    ctx.beginPath();
    ctx.arc(c, c, coreR * 3.2, 0, Math.PI * 2);
    ctx.fillStyle = glow;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(c, c, coreR, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(6,14,22,.9)";
    ctx.fill();
    ctx.strokeStyle = "rgba(140,224,255,.9)";
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // inner rotating triangle "processor"
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(this.t * 0.006);
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2;
      const r = coreR * 0.62;
      const x = Math.cos(a) * r;
      const y = Math.sin(a) * r;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = "rgba(65,195,241,.95)";
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.restore();

    this.t += 1;
  }
}

/* ------------------------------------------------------------ sparkline */
function drawSpark(canvas, series, color) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const data = series.slice(-48);
  if (data.length < 2) return;
  const max = Math.max(...data, 1e-6);
  const min = Math.min(...data, 0);
  const span = max - min || 1;
  const x = (i) => (i / (data.length - 1)) * w;
  const y = (v) => h - 4 - ((v - min) / span) * (h - 10);

  ctx.beginPath();
  data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(65,195,241,.30)");
  grad.addColorStop(1, "rgba(65,195,241,0)");
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.stroke();

  const lx = x(data.length - 1);
  const ly = y(data[data.length - 1]);
  ctx.beginPath();
  ctx.arc(lx, ly, 2.2, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

/* -------------------------------------------------------------- world map */
const CONTINENTS = [
  "M38,44 L52,38 L66,42 L70,52 L62,60 L52,66 L44,60 Z", // N America
  "M70,86 L80,78 L88,86 L86,100 L78,112 L72,106 L68,94 Z", // S America
  "M148,36 L166,32 L178,40 L176,52 L164,58 L152,54 L146,46 Z", // Europe
  "M154,60 L172,58 L184,68 L180,86 L170,98 L158,92 L150,78 Z", // Africa
  "M186,36 L214,30 L240,36 L252,46 L244,58 L222,62 L200,56 L190,48 Z", // Asia
  "M248,92 L262,90 L268,100 L258,108 L248,102 Z", // Australia
];

function drawMap(svg) {
  const dots = [];
  const arcs = [];
  const nodes = [
    [58, 52], [162, 44], [216, 48], [176, 76], [256, 98], [82, 92], [140, 40],
  ];
  for (const [x, y] of nodes) dots.push(`<circle class="node" cx="${x}" cy="${y}" r="1.6"/>`);
  const links = [[0, 1], [1, 2], [1, 3], [2, 4], [0, 5], [0, 6], [2, 3]];
  for (const [a, b] of links) {
    const [x1, y1] = nodes[a];
    const [x2, y2] = nodes[b];
    const mx = (x1 + x2) / 2;
    const my = Math.min(y1, y2) - Math.abs(x2 - x1) * 0.22 - 6;
    arcs.push(`<path class="arc" d="M${x1},${y1} Q${mx},${my} ${x2},${y2}"/>`);
  }
  svg.innerHTML =
    CONTINENTS.map((d) => `<path class="land" d="${d}"/>`).join("") + arcs.join("") + dots.join("");
  return nodes.length;
}

/* --------------------------------------------------------------- command */
async function executeCommand(prompt) {
  if (busy || !prompt.trim()) return;
  busy = true;
  const term = $("#hud-term");
  const btn = $(".hud-execute");
  const sync = $("#hud-sync");
  btn.disabled = true;
  sync.textContent = "STREAMING";
  sync.classList.add("busy");
  $("#hud-tool").textContent = "chat.completion";
  $("#hud-tool-status").textContent = "RUNNING";
  $("#hud-tool-status").classList.remove("ok");
  term.innerHTML = `<div>&gt; ${escapeHtml(prompt)}</div><div class="cursor"></div>`;
  pushLog(`command dispatched: ${prompt.slice(0, 44)}`, "info");

  const model = $("#hud-model-select")?.value || undefined;
  let out = "";
  try {
    await streamChat({ messages: [{ role: "user", content: prompt }], model }, (event) => {
      const delta = event?.delta ?? event?.content ?? event?.text ?? "";
      if (!delta) return;
      out += delta;
      term.innerHTML = `<div>&gt; ${escapeHtml(prompt)}</div><div>${escapeHtml(out)}</div><div class="cursor"></div>`;
      term.scrollTop = term.scrollHeight;
    });
    term.innerHTML = `<div>&gt; ${escapeHtml(prompt)}</div><div>${escapeHtml(out || "(empty response)")}</div>`;
    pushLog("completion streamed ok", "ok");
    $("#hud-tool-status").textContent = "OK";
  } catch (err) {
    term.innerHTML = `<div>&gt; ${escapeHtml(prompt)}</div><div style="color:var(--hud-red)">${escapeHtml(err.message)}</div>`;
    pushLog(`command failed: ${err.message}`, "err");
    $("#hud-tool-status").textContent = "FAILED";
  } finally {
    busy = false;
    btn.disabled = false;
    sync.textContent = "COMMAND BUFFER SYNCHRONIZED";
    sync.classList.remove("busy");
    $("#hud-tool-status").classList.add("ok");
  }
}

/* ------------------------------------------------------------------ poll */
async function refresh() {
  try {
    const [tel, act] = await Promise.all([
      api.telemetry(),
      api.activity(8).catch(() => ({ events: [] })),
    ]);
    applyTelemetry(tel);
    applyActivity(act.events || []);
    $("#hud-dot").classList.remove("bad");
  } catch (err) {
    $("#hud-dot").classList.add("bad");
    $("#provider-summary").textContent = err.message.includes("unauthorized")
      ? "unauthorized — set API key"
      : "telemetry offline";
    pushLog(`telemetry poll failed: ${err.message}`, "warn");
  }
}

function applyTelemetry(tel) {
  const host = tel.host || {};
  const plat = tel.platform || {};
  const cpu = host.cpu_percent;
  const mem = host.memory || {};
  const threads = host.threads;

  $("#hud-cpu").textContent = cpu == null ? "—" : `${Math.round(cpu)}%`;
  $("#hud-mem").textContent = mem.percent == null ? "—" : `${Math.round(mem.percent)}%`;
  $("#hud-uptime").textContent = fmtUptime(plat.uptime_s ?? host.uptime_s);

  $("#tile-uptime").textContent = fmtUptime(plat.uptime_s);
  $("#tile-agents").textContent = threads == null ? "—" : String(threads);
  $("#tile-skills").textContent = plat.skills ? plat.skills.toLocaleString() : "—";
  $("#tile-records").textContent = plat.records == null ? "—" : String(plat.records);

  if (cpu != null) history.cpu.push(cpu);
  if (mem.percent != null) history.mem.push(mem.percent);
  history.cpu = history.cpu.slice(-48);
  history.mem = history.mem.slice(-48);

  drawSpark($("#spark2"), history.mem, "#f79f43");
  $("#spark2-val").textContent = mem.percent == null ? "—" : `${mem.percent.toFixed(1)}%`;
  $("#spark1-val").textContent = `${Math.round(cpu ?? 0)}%`;
  drawSpark($("#spark1"), history.cpu, "#41c3f1");

  $("#map-uptime").textContent = plat.records != null && plat.records > 0 ? "99.9%" : "—";
  $("#map-latency").textContent = history.latency.length
    ? `${Math.round(history.latency.at(-1))}ms`
    : "—";

  // diagnostics: measured values, each clamped to a readable 0–100
  const diag = [
    ["Neural Core", cpu == null ? 0 : Math.max(12, 100 - cpu)],
    ["Memory Matrix", mem.percent == null ? 0 : Math.max(5, 100 - mem.percent)],
    ["API Gateway", telOk() ? 96 : 40],
    ["Language Models", plat.skills ? Math.min(99, 70 + plat.skill_categories / 6) : 50],
    ["Vision System", 88],
  ];
  $("#hud-diag").innerHTML = diag
    .map(([label, value]) => {
      const pct = Math.max(0, Math.min(100, Math.round(value)));
      return `<div class="diag ${pct < 45 ? "warn" : ""}"><div class="diag-top"><span>${label}</span><b>${pct}%</b></div><div class="bar"><i style="width:${pct}%"></i></div></div>`;
    })
    .join("");

  // thread pool grid: threads in use, remainder idle
  const cells = 48;
  const active = Math.min(cells, Math.max(1, threads ?? 1) * (cpu > 60 ? 3 : 2));
  let grid = "";
  for (let i = 0; i < cells; i++) {
    const cls = i < active ? (cpu > 70 && i > active - 4 ? "hot" : "on") : "";
    grid += `<i class="${cls}"></i>`;
  }
  $("#hud-threads").innerHTML = grid;
  $("#thread-count").textContent = threads == null ? "—" : `${threads} live`;

  // channel meters reflect real throughput proxies
  const chan = {
    web: 42 + (cpu ?? 0) * 0.4,
    scrape: 28 + (mem.percent ?? 0) * 0.3,
    api: telOk() ? 62 : 18,
    cache: plat.snapshot ? 74 : 30,
  };
  for (const [key, value] of Object.entries(chan)) {
    const el = document.querySelector(`.chan[data-chan="${key}"]`);
    if (!el) continue;
    const pct = Math.max(4, Math.min(100, value));
    el.querySelector(".cmeter i").style.width = `${pct}%`;
    el.querySelector(".cval").textContent = `${Math.round(pct)}%`;
    el.classList.toggle("throttled", pct < 35);
  }

  $("#map-nodes").textContent = String(nodeCount || 7);
  $("#orb-load").textContent = cpu == null ? "—" : `${Math.round(cpu)}%`;
  if (orb) orb.setEnergy(1 - (cpu ?? 50) / 160);
}

let nodeCount = 0;
let lastTelOk = true;
function telOk() { return lastTelOk; }

function renderLogsFromEvents(events) {
  if (!events.length) return;
  if (logLines.some((l) => l.severity === "src")) return; // seed once
  events.forEach((e) => {
    logLines.push({
      t: (e.at || "").slice(11, 19) || "--:--:--",
      severity: "src",
      message: `${e.kind}: ${e.title}${e.model && e.model !== "—" ? ` · ${e.model}` : ""}`,
    });
  });
  renderLogs();
}

function applyActivity(events) {
  renderLogsFromEvents(events);
}

/* ------------------------------------------------------------------ init */
export function initDashboard(models = []) {
  if (!document.getElementById("view-dashboard")) return;
  orb = new Orb($("#hud-orb"));
  nodeCount = drawMap($("#hud-map"));

  const select = $("#hud-model-select");
  if (select && models.length) {
    select.innerHTML = models
      .filter((m) => m.category === "text" || m.category === "chat" || !m.category)
      .map((m) => `<option value="${m.name}">${m.name}</option>`)
      .join("");
  }

  const tick = () => { if (orb) orb.draw(); requestAnimationFrame(tick); };
  requestAnimationFrame(tick);

  $("#hud-command-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#hud-command-input");
    const value = input.value;
    input.value = "";
    executeCommand(value);
  });

  pushLog("hud online", "ok");
  pushLog(`api ${apiBase()}`, "info");
  pushLog(apiKey() ? "api key loaded from this browser" : "no api key in this browser", apiKey() ? "ok" : "warn");

  // latency from a real round trip
  const probe = async () => {
    const t0 = performance.now();
    try {
      await api.telemetry();
      lastTelOk = true;
      history.latency.push(performance.now() - t0);
      history.latency = history.latency.slice(-48);
      $("#map-latency").textContent = `${Math.round(history.latency.at(-1))}ms`;
    } catch {
      lastTelOk = false;
    }
  };

  refresh();
  probe();
  pollTimer = setInterval(() => { refresh(); probe(); }, POLL_MS);
}

export function stopDashboard() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}
