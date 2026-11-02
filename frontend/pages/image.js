import { api, mediaUrl, downloadB64 } from "../lib/api.js";
import { $, el, setStatus } from "../components/ui.js";

export function initImage(models) {
  const select = $("#image-model");
  for (const m of models.filter((m) => m.category === "image")) {
    select.append(el("option", { value: m.name, disabled: !m.available },
      `${m.name}${m.available ? "" : " — unavailable"}`));
  }

  $("#image-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("button[type=submit]");
    const status = $("#image-status");
    button.disabled = true;
    setStatus(status, "Generating…");
    const prompt = $("#image-prompt").value.trim();
    try {
      const res = await api.runModel(select.value, {
        prompt,
        negative_prompt: $("#image-negative").value.trim() || undefined,
        image_size: $("#image-size").value,
        steps: Number($("#image-steps").value),
      });
      const results = $("#image-results");
      for (const image of res.images || []) {
        const src = image.url ? mediaUrl(image.url) : `data:${image.mime};base64,${image.b64}`;
        results.prepend(el("figure", {}, [
          el("a", { href: src, target: "_blank", rel: "noopener" }, [el("img", { src, alt: prompt })]),
          el("figcaption", {}, `${res.model} · ${res.latency_ms} ms`),
        ]));
      }
      setStatus(status, `Done in ${res.latency_ms} ms`);
    } catch (err) {
      setStatus(status, `Error: ${err.message}`, true);
    } finally {
      button.disabled = false;
    }
  });
}
