/**
 * Local mirror of the library.
 *
 * The server is the source of truth, but a refresh, a sleeping free instance or
 * a redeploy that resets the disk should never blank the UI. Every record the
 * browser has seen is kept here, write-through, so the Library tab paints
 * instantly and stays useful offline.
 *
 * This is a mirror, not a second database: ids are the merge key, deletions are
 * tombstones so a deleted record cannot come back from the server.
 */

const KEY = "uas.library.mirror.v1";
const MAX_RECORDS = 400;
const MAX_BYTES = 3_500_000; // stay well inside the ~5 MB localStorage budget

function read() {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      records: parsed.records && typeof parsed.records === "object" ? parsed.records : {},
      deleted: Array.isArray(parsed.deleted) ? parsed.deleted : [],
      savedAt: parsed.savedAt || "",
    };
  } catch {
    return { records: {}, deleted: [], savedAt: "" };
  }
}

function write(state) {
  let entries = Object.entries(state.records);
  // Newest first, then trim until both caps are satisfied.
  entries.sort((a, b) => String(b[1].created_at).localeCompare(String(a[1].created_at)));
  entries = entries.slice(0, MAX_RECORDS);
  let records = Object.fromEntries(entries);

  let blob = JSON.stringify({ ...state, records });
  while (blob.length > MAX_BYTES && entries.length > 1) {
    entries = entries.slice(0, Math.floor(entries.length * 0.8));
    records = Object.fromEntries(entries);
    blob = JSON.stringify({ ...state, records });
  }

  try {
    localStorage.setItem(KEY, blob);
    return true;
  } catch {
    // Quota or private mode: drop the mirror rather than throw at the caller.
    try { localStorage.removeItem(KEY); } catch { /* nothing left to do */ }
    return false;
  }
}

export function remember(records) {
  if (!records || !records.length) return;
  const state = read();
  for (const record of records) {
    if (record && record.id) state.records[record.id] = record;
  }
  state.savedAt = new Date().toISOString();
  write(state);
}

export function forget(id) {
  const state = read();
  delete state.records[id];
  if (!state.deleted.includes(id)) state.deleted.push(id);
  write(state);
}

export function clear() {
  const state = read();
  write({ records: {}, deleted: state.deleted, savedAt: new Date().toISOString() });
}

export function all() {
  const state = read();
  const dead = new Set(state.deleted);
  return Object.values(state.records)
    .filter((record) => record && record.id && !dead.has(record.id))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

export function get(id) {
  if (!id) return null;
  const state = read();
  if (state.deleted.includes(id)) return null;
  return state.records[id] || null;
}

export function count() {
  return all().length;
}

export function savedAt() {
  return read().savedAt;
}

/** Filter locally the same way the server does, for offline results. */
export function find({ q = "", kind = "" } = {}) {
  const needle = q.trim().toLowerCase();
  return all().filter((record) => {
    if (kind && record.kind !== kind) return false;
    if (!needle) return true;
    return [record.title, record.prompt, record.model, record.kind]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(needle));
  });
}
