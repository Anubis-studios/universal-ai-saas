import { api } from "./api.js";
import * as mirror from "./mirror.js";

/**
 * Mirror a freshly generated result into the browser.
 *
 * Generation responses carry a `record_id`. We fetch the canonical record so the
 * mirror matches the server exactly, and fall back to a local stub if that call
 * fails — either way the Library survives a refresh.
 */
export async function rememberResult(result, { kind, title = "", prompt = "" } = {}) {
  if (!result) return null;
  const id = result.record_id;
  if (!id) return null;

  try {
    const record = await api.record(id);
    mirror.remember([record]);
    return record;
  } catch {
    const stub = {
      id,
      kind: kind || "chat",
      title: title || prompt.slice(0, 120),
      prompt,
      model: result.model || "",
      url: result.url || (result.images || [])[0]?.url || "",
      payload: { result },
      created_at: new Date().toISOString(),
      created_at_local: new Date().toISOString(),
    };
    mirror.remember([stub]);
    return stub;
  }
}
