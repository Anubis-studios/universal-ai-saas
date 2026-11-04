import { api, setApiBase, setApiKey } from "./lib/api.js";
import { $, $$ } from "./components/ui.js";
import { initChat } from "./pages/chat.js";
import { initGame } from "./pages/game.js";
import { initImage } from "./pages/image.js";
import { initVideo } from "./pages/video.js";
import { initSkills } from "./pages/skills.js";
import { initModels } from "./pages/models.js";

function switchView(name) {
  $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
}

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
  if (e.target.returnValue === "save") boot();
});

async function boot() {
  try {
    const [{ models }, skills] = await Promise.all([api.models(), api.skills()]);
    const available = models.filter((m) => m.available).length;
    $("#provider-summary").textContent = `${available}/${models.length} models ready`;
    initChat(models);
    initGame(models);
    initImage(models);
    initVideo();
    initSkills(skills);
    initModels(models);
  } catch (err) {
    $("#provider-summary").textContent = "backend unreachable — set the API URL";
    console.error(err);
  }
}

boot();
