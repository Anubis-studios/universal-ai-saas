import { api, streamChat, downloadB64 } from "../lib/api.js";
import { rememberResult } from "../lib/persist.js";
import { $, el } from "../components/ui.js";

const state = { history: [], busy: false, controller: null };

function addMessage(role, text, meta) {
  const log = $("#chat-log");
  $("#chat-empty")?.remove();
  const bubble = el("div", { class: "bubble" }, [
    document.createTextNode(text),
    meta ? el("div", { class: "meta" }, meta) : null,
  ]);
  const node = el("div", { class: `msg ${role}` }, [
    el("div", { class: "avatar" }, role === "user" ? "You" : "AI"),
    bubble,
  ]);
  log.append(node);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

export function initChat(models) {
  const select = $("#chat-model");
  select.append(el("option", { value: "auto" }, "Auto (best available)"));
  for (const m of models.filter((m) => m.category === "text")) {
    select.append(
      el("option", { value: m.name, disabled: !m.available },
        `${m.name}${m.available ? "" : " — needs key"}`)
    );
  }

  const input = $("#chat-input");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });

  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = input.value.trim();
    if (!prompt || state.busy) return;
    state.busy = true;
    $("#chat-send").disabled = true;
    input.value = "";
    input.style.height = "auto";

    state.history.push({ role: "user", content: prompt });
    addMessage("user", prompt);

    const bubble = addMessage("assistant", "");
    const payload = {
      model: select.value,
      messages: state.history.slice(-20),
      temperature: 0.7,
      max_tokens: 1024,
    };

    try {
      if ($("#chat-stream").checked) {
        state.controller = new AbortController();
        let text = "";
        let meta = "";
        await streamChat(payload, (evt) => {
          if (evt.event === "delta" && evt.text) {
            text += evt.text;
            bubble.firstChild.textContent = text;
          } else if (evt.event === "error") {
            throw new Error(evt.error || "stream error");
          } else if (evt.event === "done") {
            meta = `${evt.provider} · ${evt.model}${evt.usage?.total_tokens ? ` · ${evt.usage.total_tokens} tokens` : ""}`;
          }
        }, state.controller.signal);
        if (meta) bubble.append(el("div", { class: "meta" }, meta));
        state.history.push({ role: "assistant", content: text });
      } else {
        const res = await api.chat(payload);
        bubble.firstChild.textContent = res.text;
        bubble.append(el("div", { class: "meta" }, `${res.provider} · ${res.model} · ${res.latency_ms} ms`));
        state.history.push({ role: "assistant", content: res.text });
        rememberResult(res, { kind: "chat", prompt: payload.prompt || "" });
        if (res.images?.length) renderImages(res.images);
      }
    } catch (err) {
      if (err.name === "AbortError") {
        bubble.firstChild.textContent += "\n[stopped]";
      } else {
        bubble.firstChild.textContent = `Error: ${err.message}`;
      }
    } finally {
      state.busy = false;
      state.controller = null;
      $("#chat-send").disabled = false;
      $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
    }
  });
}

function renderImages(images) {
  for (const image of images) {
    const fig = el("figure", {}, [
      el("img", { src: image.url || `data:image/png;base64,${image.b64}`, alt: image.prompt || "generated image" }),
      el("figcaption", {}, `Image · ${image.model}`),
    ]);
    fig.querySelector("img").addEventListener("click", () => {
      if (image.b64) downloadB64(image.b64, `image-${Date.now()}.png`);
    });
    $("#chat-log").append(fig);
  }
}
