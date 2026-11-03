import { api, mediaUrl } from "../lib/api.js";
import { $, el, setStatus } from "../components/ui.js";

export function initVideo() {
  // Video needs a local ComfyUI/SD WebUI renderer; say so up front instead of
  // letting the user wait for a request that cannot succeed on a cloud host.
  api.videoHealth().then((h) => {
    if (h.comfyui) return;
    const status = $("#video-status");
    setStatus(status, "Video rendering needs a local ComfyUI service — none is reachable from this server. Chat and images work.", true);
    const button = $("#video-form button[type=submit]");
    if (button) button.disabled = true;
  }).catch(() => {});

  $("#video-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("button[type=submit]");
    const status = $("#video-status");
    button.disabled = true;
    setStatus(status, "Rendering… this takes a few minutes on CPU.");
    try {
      const res = await api.generateVideo({
        prompt: $("#video-prompt").value.trim(),
        width: Number($("#video-width").value),
        height: Number($("#video-height").value),
        fps: Number($("#video-fps").value),
        motion_bucket_id: Number($("#video-motion").value),
      });
      $("#video-result").prepend(el("div", { class: "stack" }, [
        el("video", { src: mediaUrl(res.video_url), controls: true, autoplay: true, loop: true, muted: true }),
        el("a", { href: mediaUrl(res.video_url), download: "", class: "hint" }, "Download MP4"),
      ]));
      setStatus(status, `Rendered in ${(res.latency_ms / 1000).toFixed(1)} s`);
    } catch (err) {
      setStatus(status, `Error: ${err.message}`, true);
    } finally {
      button.disabled = false;
    }
  });
}
