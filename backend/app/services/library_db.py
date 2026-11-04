"""One database, three free backends, chosen automatically at startup.

Why SQLite by default: it is the only "free, fully automatic, zero signup"
database that is also a single file. It ships inside CPython (no server, no
account, no network, no extra dependency) and gives us real SQL plus FTS5
full-text search. Nothing to provision, nothing to pay for, nothing to
configure — the app just works on a fresh clone.

The catch on a free host is that the disk is ephemeral, so the file is lost on
redeploy. Two free hosted tiers fix that, and both are picked up from the
environment with no code change:

  * Turso / libSQL  — free tier, SQLite dialect, spoken over HTTPS (no driver).
  * Postgres        — Neon/Supabase free tier, via psycopg if it is installed.

So: set nothing -> SQLite. Set TURSO_DATABASE_URL -> Turso. Set DATABASE_URL
to a postgres:// URL -> Postgres. The API reports which one is live at
GET /api/v1/db/status, so a deploy can never be vague about where data lives.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", "outputs/library.db"))

_SCHEMA_SQLITE = [
    """CREATE TABLE IF NOT EXISTS records (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        prompt TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at DESC)",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
]

# FTS5 is compiled into CPython's sqlite3 almost everywhere, but not all builds.
_FTS_SQLITE = """CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    id UNINDEXED, title, prompt, kind UNINDEXED, tokenize='porter unicode61')"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return secrets.token_urlsafe(9)


class LibraryDB:
    """Persistent record store for everything the platform generates."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or DB_PATH)
        self.backend = "sqlite"
        self.detail = str(self.path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._fts = False
        self._turso_url = os.environ.get("TURSO_DATABASE_URL", "").strip()
        self._turso_token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
        self._pg_url = os.environ.get("DATABASE_URL", "").strip()
        self._pg = None
        self.error = ""

    # ------------------------------------------------------------- selection
    def connect(self) -> "LibraryDB":
        if self._pg_url.startswith(("postgres://", "postgresql://")):
            try:
                import psycopg  # type: ignore
            except ImportError:
                self.error = "DATABASE_URL set but psycopg is not installed; using SQLite"
            else:
                self._pg = psycopg.connect(self._pg_url, autocommit=True)
                self.backend = "postgres"
                self.detail = self._pg_url.split("@")[-1]
                self._init_postgres()
                logger.info("library database: postgres (%s)", self.detail)
                return self
        elif self._turso_url and self._turso_token:
            self.backend = "turso"
            self.detail = self._turso_url
            self._init_turso()
            logger.info("library database: turso (%s)", self.detail)
            return self
        self._init_sqlite()
        logger.info("library database: sqlite (%s)", self.detail)
        return self

    def close(self) -> None:
        self.save_snapshot()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._pg is not None:
            self._pg.close()
            self._pg = None

    # ---------------------------------------------------------------- sqlite
    def _init_sqlite(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        for statement in _SCHEMA_SQLITE:
            conn.execute(statement)
        try:
            conn.execute(_FTS_SQLITE)
            self._fts = True
        except sqlite3.OperationalError:  # build without FTS5
            self._fts = False
        conn.commit()
        self._conn = conn

        # If the container was recycled and the database came back empty, the
        # snapshot next to it is the last known good state. Only used when the
        # table is genuinely empty, so it can never overwrite live rows.
        if not self._run("SELECT id FROM records LIMIT 1"):
            count = self.load_snapshot()
            if count:
                logger.info("restored %s records from snapshot", count)

    # ---------------------------------------------------------------- turso
    def _turso(self, statements: list[tuple[str, list[Any]]]) -> list[dict[str, Any]]:
        """Run one batched pipeline request against libSQL over HTTPS."""
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                        "args": [
                            {"type": "null"} if v is None
                            else {"type": "integer", "value": str(v)} if isinstance(v, int)
                            else {"type": "float", "value": str(v)} if isinstance(v, float)
                            else {"type": "text", "value": str(v)}
                            for v in params
                        ],
                    },
                }
                for sql, params in statements
            ]
        }
        response = httpx.post(
            f"{self._turso_url.rstrip('/')}/v2/pipeline",
            json=payload,
            headers={"Authorization": f"Bearer {self._turso_token}"},
            timeout=20.0,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        out: list[dict[str, Any]] = []
        for result in results:
            if result.get("type") == "error":
                raise RuntimeError(result.get("error", {}).get("message", "turso error"))
            reply = result.get("response", {}).get("result", {})
            cols = [c.get("name") for c in reply.get("cols", [])]
            rows = []
            for row in reply.get("rows", []):
                rows.append({c: cell.get("value") for c, cell in zip(cols, row)})
            out.append({"rows": rows, "affected": reply.get("affected_row_count", 0)})
        return out

    def _init_turso(self) -> None:
        wanted = [s for s in _SCHEMA_SQLITE] + [_FTS_SQLITE]
        try:
            self._turso([(s, []) for s in wanted])
            self._fts = True
        except Exception as exc:  # noqa: BLE001 - FTS may be unavailable; retry without
            logger.warning("turso FTS unavailable (%s); continuing without it", exc)
            self._turso([(s, []) for s in _SCHEMA_SQLITE])
            self._fts = False

    # ------------------------------------------------------------- postgres
    def _init_postgres(self) -> None:
        assert self._pg is not None
        with self._pg.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"""
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind, created_at DESC)"
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"""
            )

    # -------------------------------------------------------------- plumbing
    def _run(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        params = params or []
        if self.backend == "postgres":
            assert self._pg is not None
            translated = sql
            for index in range(len(params), 0, -1):
                translated = translated.replace("?", f"${index}")
            with self._pg.cursor() as cur:
                cur.execute(translated, params)
                if cur.description is None:
                    return []
                cols = [c.name for c in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        if self.backend == "turso":
            return self._turso([(sql, params)])[0]["rows"]
        assert self._conn is not None
        cur = self._conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        self._conn.commit()
        return rows

    # -------------------------------------------------------------- writing
    # ----------------------------------------------------------- snapshots
    def dump(self) -> dict[str, Any]:
        """Every record as portable JSON, oldest first."""
        rows = self._run("SELECT * FROM records ORDER BY created_at ASC")
        return {
            "version": 1,
            "exported_at": _now(),
            "backend": self.backend,
            "records": [_hydrate(r) for r in rows],
        }

    def restore(self, data: dict[str, Any]) -> int:
        """Insert records from a dump, skipping ids that already exist."""
        records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(records, list):
            return 0
        existing = {r["id"] for r in self._run("SELECT id FROM records")}
        restored = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = str(record.get("id") or "")
            if not record_id or record_id in existing:
                continue
            self.add(
                str(record.get("kind") or "chat"),
                title=str(record.get("title") or ""),
                prompt=str(record.get("prompt") or ""),
                model=str(record.get("model") or ""),
                url=str(record.get("url") or ""),
                payload=record.get("payload") or {},
                record_id=record_id,
                created_at=str(record.get("created_at") or ""),
            )
            existing.add(record_id)
            restored += 1
        return restored

    @property
    def snapshot_path(self) -> Path:
        return Path(os.environ.get("DB_SNAPSHOT_PATH", str(self.path) + ".snapshot.json"))

    def save_snapshot(self) -> bool:
        """Write the whole table to a JSON file next to the database.

        On a host whose disk survives restarts (local docker, a paid Render disk)
        this is what makes a refresh or a crash lossless without a managed
        database. It is a no-op for managed backends, which already persist.
        """
        if self.backend != "sqlite" or os.environ.get("AUTO_SNAPSHOT", "1") == "0":
            return False
        try:
            payload = self.dump()
            target = self.snapshot_path
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
            self._last_snapshot = time.time()
            return True
        except Exception:  # noqa: BLE001 - never break a request over a backup
            logger.warning("snapshot write failed", exc_info=True)
            return False

    def load_snapshot(self) -> int:
        target = self.snapshot_path
        if self.backend != "sqlite" or not target.exists():
            return 0
        try:
            return self.restore(json.loads(target.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            logger.warning("snapshot restore failed", exc_info=True)
            return 0

    def _after_write(self) -> None:
        """Debounced snapshot: a burst of writes costs one dump, not one each."""
        if self.backend != "sqlite" or os.environ.get("AUTO_SNAPSHOT", "1") == "0":
            return
        if time.time() - getattr(self, "_last_snapshot", 0.0) < 2.0:
            return
        self.save_snapshot()

    def add(
        self, kind: str, *, title: str = "", prompt: str = "", model: str = "",
        url: str = "", payload: dict[str, Any] | None = None, record_id: str | None = None,
        created_at: str = "",
    ) -> dict[str, Any]:
        record_id = record_id or _new_id()
        created = created_at or _now()
        blob = _compact(payload or {})
        with self._lock:
            self._run(
                "INSERT INTO records (id, kind, title, prompt, model, url, payload, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [record_id, kind, title[:300], prompt[:4000], model[:200], url[:1000], blob, created],
            )
            if self._fts:
                try:
                    self._run(
                        "INSERT INTO records_fts (id, title, prompt, kind) VALUES (?,?,?,?)",
                        [record_id, title[:300], prompt[:4000], kind],
                    )
                except Exception as exc:  # noqa: BLE001 - index is a nice-to-have
                    logger.debug("fts insert skipped: %s", exc)
            self._run(
                "INSERT INTO events (kind, detail, created_at) VALUES (?,?,?)",
                [f"{kind}.create", record_id, created],
            )
        self._after_write()
        return self.get(record_id) or {}

    def get(self, record_id: str) -> dict[str, Any] | None:
        rows = self._run("SELECT * FROM records WHERE id = ?", [record_id])
        return _hydrate(rows[0]) if rows else None

    def delete(self, record_id: str) -> bool:
        with self._lock:
            existed = bool(self._run("SELECT id FROM records WHERE id = ?", [record_id]))
            self._run("DELETE FROM records WHERE id = ?", [record_id])
            if self._fts:
                try:
                    self._run("DELETE FROM records_fts WHERE id = ?", [record_id])
                except Exception:  # noqa: BLE001
                    pass
        if existed:
            self._after_write()
        return existed

    # -------------------------------------------------------------- reading
    def search(
        self, query: str = "", *, kind: str = "", limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        ids: list[str] = []
        if query.strip() and self._fts:
            try:
                hits = self._run(
                    "SELECT id FROM records_fts WHERE records_fts MATCH ? LIMIT 500",
                    [_fts_query(query)],
                )
                ids = [h["id"] for h in hits]
            except Exception as exc:  # noqa: BLE001 - fall back to LIKE
                logger.debug("fts query failed (%s); using LIKE", exc)
        if query.strip() and not ids:
            where.append("(title LIKE ? OR prompt LIKE ?)")
            like = f"%{query.strip()}%"
            params.extend([like, like])
        elif ids:
            where.append("id IN (" + ",".join("?" * len(ids)) + ")")
            params.extend(ids)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self._run(f"SELECT COUNT(*) AS n FROM records {clause}", params)
        rows = self._run(
            f"SELECT * FROM records {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return {
            "records": [_hydrate(r) for r in rows],
            "count": len(rows),
            "total": int(total[0]["n"]) if total else 0,
            "limit": limit,
            "offset": offset,
        }

    def stats(self) -> dict[str, Any]:
        by_kind = self._run(
            "SELECT kind, COUNT(*) AS n FROM records GROUP BY kind ORDER BY n DESC"
        )
        total = self._run("SELECT COUNT(*) AS n FROM records")
        return {
            "backend": self.backend,
            "location": self.detail,
            "records": int(total[0]["n"]) if total else 0,
            "by_kind": {r["kind"]: int(r["n"]) for r in by_kind},
            "full_text_search": self._fts,
            "persistent": self.backend != "sqlite" or bool(os.environ.get("PERSISTENT_DISK")),
            "free": True,
        }

    def status(self) -> dict[str, Any]:
        payload = self.stats()
        payload |= {
            "size_bytes": self.path.stat().st_size if self._conn and self.path.exists() else None,
            "snapshot": (
                str(self.snapshot_path) if self.backend == "sqlite"
                and os.environ.get("AUTO_SNAPSHOT", "1") != "0" else None
            ),
            "error": self.error,
            "hint": (
                "Set TURSO_DATABASE_URL + TURSO_AUTH_TOKEN (free tier) or DATABASE_URL "
                "(Neon/Supabase free tier) to keep records across redeploys."
                if self.backend == "sqlite" else "Managed backend: records survive redeploys."
            ),
        }
        return payload


_PAYLOAD_BUDGET = 150_000


def _compact(payload: dict[str, Any], budget: int = _PAYLOAD_BUDGET) -> str:
    """Serialise a payload that always parses back, trimming the biggest blobs.

    Slicing the JSON text (the obvious thing) yields invalid JSON, which would
    silently blank the record on read. Trim values instead, and halve the
    largest offender repeatedly until it fits.
    """
    fields = dict(payload)
    blob = json.dumps(fields, ensure_ascii=False)
    while len(blob) > budget:
        keys = [k for k, v in fields.items() if isinstance(v, str) and len(v) > 200]
        if not keys:
            fields = {"_truncated": True, "keys": list(fields)[:50]}
            break
        victim = max(keys, key=lambda k: len(fields[k]))
        original = len(fields[victim])
        fields[victim] = fields[victim][: max(200, original // 2)] + f"…(from {original} chars)"
        blob = json.dumps(fields, ensure_ascii=False)
    return blob


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    try:
        record["payload"] = json.loads(record.get("payload") or "{}")
    except json.JSONDecodeError:
        record["payload"] = {}
    if record.get("created_at") and "T" in record["created_at"]:
        record["created_at"] = record["created_at"].replace("+00:00", "Z")
    return record


def _fts_query(text: str) -> str:
    tokens = [t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if t]
    return " AND ".join(f'"{t}"*' for t in tokens[:10]) or '""'
