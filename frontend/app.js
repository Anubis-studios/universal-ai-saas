import { api, setApiBase, setApiKey } from "./lib/api.js";
import { $, $$ } from "./components/ui.js";
import { initChat } from "./pages/chat.js";
import { initGame } from "./pages/game.js";
import { initImage } from "./pages/image.js";
import { initVideo } from "./pages/video.js";
import { initSkills } from "./pages/skills.js";
import { initModels } from "./pages/models.js";
import { initLibrary } from "./pages/library.js";
import { initDashboard, stopDashboard, pushLog } from "./pages/dashboard.js";

let currentView = "dashboard";

function switchView(name) {
  currentView = name;
  $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
  // the dashboard polls the server; stop the timer while it is off-screen
  if (name !== "dashboard") stopDashboard();
  if (name === "library") initLibrary();
  if (name === "dashboard") initDashboard(state.models);
  document.body.dataset.view = name;
}

const state = { models: [] };

$$(".tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));

$("#settings-toggle").addEventListener("click", () => {
  const dialog = $("#settings-dialog");
  $("#api-base").value = localStorage.getItem("universal.apiBase") || "";
  $("#api-key").value = localStorage.getItem("universal.apiKey") || "";
  dialog.showModal();
});
$("#settings-dialog").addEventListener("close", (e) => {
  setApiBase($("#api-base").value.trim());
  setApiKey($("#api-key").value);
  if (e.target.returnValue === "save") initLibrary();
boot();
});

async function boot() {
  try {
    const [{ models }, skills] = await Promise.all([api.models(), api.skills()]);
    state.models = models;
    const available = models.filter((m) => m.available).length;
    $("#provider-summary").textContent = `${available}/${models.length} models ready`;
    initDashboard(models);
    pushLog(`model registry loaded: ${available}/${models.length} available`, "ok");
    initChat(models);
    initGame(models);
    initImage(models);
    initVideo();
    initSkills(skills);
    initModels(models);
    initLibrary();
  } catch (err) {
    $("#provider-summary").textContent = "backend unreachable — set the API URL";
    pushLog(`boot failed: ${err.message}`, "err");
    initDashboard([]);
    console.error(err);
  }
}

boot();
