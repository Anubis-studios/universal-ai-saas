import { api, mediaUrl } from "../lib/api.js";
import { $, el } from "../components/ui.js";

const state = { genre: "", busy: false };

function log(rows) {
  $("#game-log").innerHTML = "";
  for (const row of rows) {
    $("#game-log").append(
      el("div", { class: `step${row.done ? " done" : ""}` }, [
        el("b", {}, row.label),
        document.createTextNode(row.text),
      ])
    );
  }
}

function showGame(url) {
  const stage = $("#game-stage");
  stage.innerHTML = "";
  const frame = el("iframe", {
    src: mediaUrl(url),
    title: "Generated game",
    allow: "autoplay; fullscreen",
    sandbox: "allow-scripts allow-pointer-lock allow-same-origin",
  });
  stage.append(frame);

  // Phones get a real fullscreen button; iOS Safari ignores keyboard entirely.
  const fullscreen = el("button", {
    type: "button",
    class: "ghost",
    onclick: () => (frame.requestFullscreen ? frame.requestFullscreen() : null),
  }, "Fullscreen");
  $("#game-stage").after(fullscreen);
}

export function initGame(models) {
  const select = $("#game-model");
  select.append(el("option", { value: "auto" }, "Auto (best available)"));
  for (const m of models.filter((m) => m.category === "text" && m.available)) {
    select.append(el("option", { value: m.name }, m.name));
  }

  $("#game-genres").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    state.genre = chip.dataset.genre;
    for (const c of $("#game-genres").children) c.classList.toggle("on", c === chip);
  });
  $("#game-genres").firstElementChild.classList.add("on");

  $("#game-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.busy) return;
    const prompt = $("#game-prompt").value.trim();
    if (!prompt) return;
    state.busy = true;
    $("#game-build").disabled = true;
    $("#game-build").textContent = "Forging…";
    log([
      { label: "design", text: "writing mechanics and win/lose conditions" },
      { label: "code", text: "generating engine and rendering loop" },
      { label: "verify", text: "checking it is self-contained" },
    ]);

    try {
      const game = await api.generateGame({ prompt, genre: state.genre || null, model: select.value });
      log([
        { label: "design", text: `${game.title} — ${game.genre}`, done: true },
        { label: "code", text: `${game.lines.toLocaleString()} lines · ${(game.bytes / 1024).toFixed(0)} KB`, done: true },
        { label: "verify", text: Object.values(game.checks).every(Boolean) ? "all checks passed" : "some checks flagged", done: true },
        { label: "model", text: game.model },
        { label: "build", text: `${(game.latency_ms / 1000).toFixed(1)} s` },
      ]);
      showGame(game.url);

      const open = $("#game-open");
      open.href = mediaUrl(game.url);
      open.hidden = false;
      const dl = $("#game-download");
      dl.hidden = false;
      dl.onclick = () => {
        const a = el("a", { href: mediaUrl(game.url), download: `${game.title}.html` });
        document.body.append(a);
        a.click();
        a.remove();
      };
      loadLibrary(game.url);
    } catch (err) {
      log([{ label: "error", text: err.message }]);
    } finally {
      state.busy = false;
      $("#game-build").disabled = false;
      $("#game-build").textContent = "Forge game";
    }
  });

  $("#game-library").addEventListener("change", (e) => {
    if (e.target.value) showGame(e.target.value);
  });

  loadLibrary();
}

async function loadLibrary(selected) {
  try {
    const { games } = await api.games();
    const select = $("#game-library");
    select.innerHTML = '<option value="">— previous builds —</option>';
    for (const g of games) {
      const opt = el("option", { value: g.url }, `${g.title} · ${(g.bytes / 1024).toFixed(0)} KB`);
      if (g.url === selected) opt.selected = true;
      select.append(opt);
    }
  } catch {
    /* library is a nicety; generation still works */
  }
}
