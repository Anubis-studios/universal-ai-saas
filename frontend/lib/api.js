// Served by the same FastAPI app in production, so the origin is the API root.
const DEFAULT_BASE = window.location.origin.startsWith("file:")
  ? "http://localhost:8000"
  : window.location.origin;

export function apiBase() {
  return localStorage.getItem("universal.apiBase") || DEFAULT_BASE;
}

export function apiKey() {
  return localStorage.getItem("universal.apiKey") || "";
}

export function setApiKey(value) {
  if (value) localStorage.setItem("universal.apiKey", value.trim());
  else localStorage.removeItem("universal.apiKey");
}

function authHeaders(extra) {
  const key = apiKey();
  return key ? { ...extra, authorization: `Bearer ${key}` } : { ...extra };
}

export function setApiBase(value) {
  if (value) localStorage.setItem("universal.apiBase", value.replace(/\/$/, ""));
  else localStorage.removeItem("universal.apiBase");
}

async function request(path, { method = "GET", body, signal } = {}) {
  const res = await fetch(`${apiBase()}/api/v1${path}`, {
    method,
    headers: authHeaders(body ? { "content-type": "application/json" } : undefined),
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const message = res.status === 401
      ? "unauthorized — add your PLATFORM_API_KEY in settings"
      : data.error || data.detail || `HTTP ${res.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}

export const api = {
  models: () => request("/models"),
  chat: (payload) => request("/chat", { method: "POST", body: payload }),
  runModel: (name, payload) => request(`/models/${encodeURIComponent(name)}/run`, { method: "POST", body: payload }),
  generateImage: (payload) => request("/images/generate", { method: "POST", body: payload }),
  generateVideo: (payload) => request("/video/generate", { method: "POST", body: payload }),
  generateGame: (payload) => request("/games/generate", { method: "POST", body: payload }),
  games: () => request("/games"),
  videoHealth: () => request("/video/health"),
  skills: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== "" && v != null),
    ).toString();
    return request(`/skills${qs ? `?${qs}` : ""}`);
  },
  skill: (name) => request(`/skills/${encodeURIComponent(name)}`),
  skillCategories: () => request("/skills/categories"),
  runSkill: (name, params) => request(`/skills/${encodeURIComponent(name)}/run`, { method: "POST", body: { params } }),
};

/** Stream chat via SSE; yields {event, text} objects. */
export async function streamChat(payload, onEvent, signal) {
  const res = await fetch(`${apiBase()}/api/v1/chat`, {
    method: "POST",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ ...payload, stream: true }),
    signal,
  });
  if (res.status === 401) throw new Error("unauthorized — add your PLATFORM_API_KEY in settings");
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
