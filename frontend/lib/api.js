const DEFAULT_BASE = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? "http://localhost:8000"
  : `${window.location.origin}/api`;

export function apiBase() {
  return localStorage.getItem("universal.apiBase") || DEFAULT_BASE;
}

export function setApiBase(value) {
  if (value) localStorage.setItem("universal.apiBase", value.replace(/\/$/, ""));
  else localStorage.removeItem("universal.apiBase");
}

async function request(path, { method = "GET", body, signal } = {}) {
  const res = await fetch(`${apiBase()}/api/v1${path}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const message = data.error || data.detail || `HTTP ${res.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}

export const api = {
  models: () => request("/models"),
  chat: (payload) => request("/chat", { method: "POST", body: payload }),
  runModel: (name, payload) => request(`/models/${encodeURIComponent(name)}/run`, { method: "POST", body: payload }),
  generateImage: (payload) => request("/models/sdxl-turbo/run", { method: "POST", body: payload }),
  generateVideo: (payload) => request("/video/generate", { method: "POST", body: payload }),
  videoHealth: () => request("/video/health"),
  skills: () => request("/skills"),
  runSkill: (name, params) => request(`/skills/${encodeURIComponent(name)}/run`, { method: "POST", body: { params } }),
};

/** Stream chat via SSE; yields {event, text} objects. */
export async function streamChat(payload, onEvent, signal) {
  const res = await fetch(`${apiBase()}/api/v1/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...payload, stream: true }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (data === "[DONE]") return;
      try { onEvent(JSON.parse(data)); } catch { /* ignore keep-alives */ }
    }
  }
}

export const mediaUrl = (path) => (path?.startsWith("http") ? path : `${apiBase()}${path}`);

export function downloadB64(b64, filename) {
  const a = document.createElement("a");
  a.href = `data:image/png;base64,${b64}`;
  a.download = filename;
  a.click();
}
