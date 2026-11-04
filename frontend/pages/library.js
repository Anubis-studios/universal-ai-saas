import { api, mediaUrl } from "../lib/api.js";
import { $, el, setStatus } from "../components/ui.js";

const PAGE = 24;
let offset = 0;
let total = 0;
let busy = false;

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

function card(record) {
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
      src: imageUrl.startsWith("data:") ? imageUrl : mediaUrl(imageUrl), alt: record.title || record.prompt, loading: "lazy",
    }));
  }

  return el("article", { class: "record", dataset: { id: record.id } }, [
    ...thumbs,
    el("div", { class: "record-head" }, [
      el("span", { class: `badge kind-${record.kind}` }, KIND_LABEL[record.kind] || record.kind),
      el("span", { class: "hint" }, when(record.created_at)),
    ]),
    el("h3", {}, record.title || record.prompt || "(untitled)"),
    el("p", { class: "hint" }, (record.prompt || "").slice(0, 140)),
    el("div", { class: "record-actions" }, [
      url
        ? el("a", { class: "ghost", href: url, target: "_blank", rel: "noopener" }, "open ↗")
        : null,
      el("button", {
        class: "ghost", type: "button",
        onclick: () => show(record.id),
      }, "details"),
      el("button", {
        class: "ghost danger", type: "button",
        onclick: () => remove(record.id),
      }, "delete"),
    ]),
  ]);
}

async function show(id) {
  const pre = $("#library-detail");
  try {
    const record = await api.record(id);
    pre.hidden = false;
    pre.textContent = JSON.stringify(record, null, 2);
  } catch (err) {
    setStatus(pre, err.message, true);
    pre.hidden = false;
  }
}

async function remove(id) {
  try {
    await api.deleteRecord(id);
    $(`#library-list .record[data-id="${id}"]`)?.remove();
    total = Math.max(0, total - 1);
    render();
  } catch (err) {
    setStatus($("#library-stats"), err.message, true);
  }
}

function render() {
  $("#library-count").textContent = total
    ? `${Math.min(offset, total)} of ${total} records`
    : "";
  $("#library-more").hidden = offset >= total;
}

async function load(reset = false) {
  if (busy) return;
  busy = true;
  const list = $("#library-list");
  if (reset) {
    offset = 0;
    list.replaceChildren();
  }
  $("#library-count").textContent = "searching…";
  try {
    const page = await api.library({
      q: $("#library-search").value.trim(),
      kind: $("#library-kind").value,
      limit: PAGE,
      offset,
    });
    total = page.total;
    const cards = page.records.map(card);
    if (!cards.length && reset) {
      list.replaceChildren(
        el("div", { class: "empty" }, [
          el("h1", {}, "Nothing here yet"),
          el("p", {}, "Generate a game, image or skill run — it lands here automatically."),
        ]),
      );
    }
    list.append(...cards);
    offset += page.count;
    render();
  } catch (err) {
    setStatus($("#library-stats"), err.message, true);
  } finally {
    busy = false;
  }
}

let timer;
export async function initLibrary() {
  const search = $("#library-search");
  if (!search || search.dataset.ready) return;
  search.dataset.ready = "1";

  try {
    const status = await api.dbStatus();
    $("#library-backend").textContent =
      `${status.backend} · ${status.records} records · ${status.persistent ? "persistent" : "ephemeral"}`;
    $("#library-stats").textContent = status.hint || "";
  } catch (err) {
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
