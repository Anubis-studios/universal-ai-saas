import { api, mediaUrl } from "../lib/api.js";
import { $, el, setStatus } from "../components/ui.js";
import * as mirror from "../lib/mirror.js";

const PAGE = 24;
let offset = 0;
let total = 0;
let busy = false;
let offline = false;

const KIND_LABEL = {
  game: "game", image: "image", video: "video", skill: "skill", chat: "chat",
};

function when(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso || "";
  const mins = Math.round((Date.now() - date.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return date.toLocaleDateString();
}

function card(record, { cached = false } = {}) {
  const payload = record.payload || {};
  const result = payload.result || {};
  const url = record.url ? mediaUrl(record.url) : "";

  const thumbs = [];
  const firstImage = (result.images || [])[0] || {};
  const imageUrl = record.url
    || result.url
    || firstImage.url
    || (firstImage.b64 ? `data:image/png;base64,${firstImage.b64}` : "");
  if (record.kind === "image" && imageUrl) {
    thumbs.push(el("img", {
      src: imageUrl.startsWith("data:") ? imageUrl : mediaUrl(imageUrl),
      alt: record.title || record.prompt,
      loading: "lazy",
    }));
  }

  return el("article", { class: "record", dataset: { id: record.id } }, [
    ...thumbs,
    el("div", { class: "record-head" }, [
      el("span", { class: `badge kind-${record.kind}` }, KIND_LABEL[record.kind] || record.kind),
      el("span", { class: "hint" }, cached ? "local copy" : when(record.created_at)),
    ]),
    el("h3", {}, record.title || record.prompt || "(untitled)"),
    el("p", { class: "hint" }, (record.prompt || "").slice(0, 140)),
    el("div", { class: "record-actions" }, [
      url ? el("a", { class: "ghost", href: url, target: "_blank", rel: "noopener" }, "open ↗") : null,
      el("button", { class: "ghost", type: "button", onclick: () => show(record.id) }, "details"),
      el("button", { class: "ghost danger", type: "button", onclick: () => remove(record.id) }, "delete"),
    ]),
  ]);
}

async function show(id) {
  const pre = $("#library-detail");
  pre.hidden = false;
  try {
    const record = await api.record(id);
    mirror.remember([record]);
    pre.textContent = JSON.stringify(record, null, 2);
  } catch (err) {
    const local = mirror.get(id);
    if (local) {
      pre.textContent = JSON.stringify(local, null, 2);
      return;
    }
    setStatus(pre, err.message, true);
  }
}

async function remove(id) {
  // Tombstone first: a failed network call must not resurrect it on refresh.
  mirror.forget(id);
  $(`#library-list .record[data-id="${id}"]`)?.remove();
  total = Math.max(0, total - 1);
  render();
  try {
    await api.deleteRecord(id);
  } catch {
    /* already gone locally; the server copy is harmless */
  }
}

function render() {
  const local = mirror.count();
  $("#library-count").textContent = total
    ? `${Math.min(offset, total)} of ${total} records`
    : "";
  $("#library-more").hidden = offset >= total;
  $("#library-stats").textContent = offline
    ? `Server unreachable — showing ${local} locally saved records. Everything new still saves to this browser.`
    : local
      ? `${local} records also mirrored in this browser.`
      : "";
}

/** Server results plus anything the browser has that the server lost. */
function merge(records, { q = "", kind = "" } = {}) {
  const seen = new Set(records.map((r) => r.id));
  const needle = q.trim().toLowerCase();
  const extra = mirror.all().filter((record) => {
    if (seen.has(record.id)) return false;
    if (kind && record.kind !== kind) return false;
    if (!needle) return true;
    return [record.title, record.prompt, record.model, record.kind]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(needle));
  });
  return { records: [...records, ...extra.map((r) => ({ ...r, __cached: true }))], extra: extra.length };
}

async function load(reset = false) {
  if (busy) return;
  busy = true;
  const list = $("#library-list");
  const params = { q: $("#library-search").value.trim(), kind: $("#library-kind").value };

  if (reset) {
    offset = 0;
    list.replaceChildren();
    // Paint from the mirror immediately — no spinner on refresh.
    const cached = mirror.find(params).slice(0, PAGE);
    if (cached.length) {
      list.append(...cached.map((r) => card(r, { cached: true })));
      $("#library-count").textContent = "showing saved copy…";
    }
  }

  try {
    const page = await api.library({ ...params, limit: PAGE, offset });
    offline = false;
    mirror.remember(page.records);

    if (reset) list.replaceChildren();
    const { records, extra } = merge(page.records, params);
    total = Math.max(page.total, page.records.length + extra);

    if (!records.length) {
      list.replaceChildren(el("div", { class: "empty" }, [
        el("h1", {}, "Nothing here yet"),
        el("p", {}, "Generate a game, image or skill run — it lands here automatically."),
      ]));
    } else {
      list.append(...records.map((r) => card(r, { cached: Boolean(r.__cached) })));
    }
    offset = page.count;
    render();
  } catch (err) {
    offline = true;
    const local = mirror.find(params);
    total = local.length;
    if (reset) list.replaceChildren();
    if (!local.length) {
      setStatus($("#library-stats"), `${err.message} — nothing saved locally yet.`, true);
    }
    if (local.length) {
      list.replaceChildren(...local.slice(0, PAGE).map((r) => card(r, { cached: true })));
    }
    render();
  } finally {
    busy = false;
  }
}

let timer;
export async function initLibrary() {
  const search = $("#library-search");
  if (!search || search.dataset.ready) return;
  search.dataset.ready = "1";

  // Paint from the mirror before any network call, so a refresh is instant.
  const early = mirror.all().slice(0, PAGE);
  if (early.length) {
    $("#library-list").replaceChildren(...early.map((r) => card(r, { cached: true })));
  }

  try {
    const status = await api.dbStatus();
    $("#library-backend").textContent =
      `${status.backend} · ${status.records} records · ${status.persistent ? "persistent" : "ephemeral"}`;
    $("#library-stats").textContent = status.hint || "";
  } catch {
    $("#library-backend").textContent = "database unreachable";
  }

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(true), 220);
  });
  $("#library-kind").addEventListener("change", () => load(true));
  $("#library-more").addEventListener("click", () => load(false));
  load(true);
}

// Keep the mirror honest when the network returns mid-session.
window.addEventListener("online", () => { if (offline) load(true); });
