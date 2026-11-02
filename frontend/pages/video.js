import { api, mediaUrl } from "../lib/api.js";
import { $, el, setStatus } from "../components/ui.js";

export function initVideo() {
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
