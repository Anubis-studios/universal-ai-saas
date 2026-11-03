"""Skill index: a self-contained, fully automatic search database.

SQLite FTS5 is the free, zero-infrastructure option that actually fits this app:
it ships inside Python's stdlib, needs no server, no API key and no network, and
builds the whole 2,877-skill catalogue in memory at startup in well under a
second. BM25 ranking comes for free, which a LIKE scan does not give us.

The catalogue ships as ``app/data/skills_index.json.gz`` (built by
``scripts/build_skills_index.py``), so a deploy needs nothing but this repo.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "skills_index.json.gz"

# Column order matters: it is also the bm25() weight order.
_COLUMNS = ("name", "title", "category", "description", "tags", "body", "risk", "source")
_WEIGHTS = (10.0, 8.0, 3.0, 6.0, 5.0, 1.0, 0.5, 0.5)
_TOKEN = re.compile(r"[a-z0-9]+")


def _fts_query(text: str, mode: str = "AND") -> str:
    """Turn free text into a safe FTS5 MATCH expression with prefix matching."""
    tokens = [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 or t.isdigit()]
    if not tokens:
        return ""
    joiner = f" {mode} "
    return joiner.join(f'"{t}"*' for t in tokens[:12])


class SkillDB:
    """In-memory FTS5 database over the bundled skill catalogue."""

    def __init__(self, index_path: Path | None = None) -> None:
        self.path = index_path or INDEX_PATH
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.count = 0
        self.categories: dict[str, int] = {}

    # ---------------------------------------------------------------- lifecycle
    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if not self.path.exists():
            logger.warning("skill index missing at %s; catalogue will be empty", self.path)
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.execute(
                "CREATE VIRTUAL TABLE skills USING fts5("
                + ", ".join(_COLUMNS)
                + ", tokenize='porter unicode61')"
            )
            return self._conn

        rows = json.loads(gzip.decompress(self.path.read_bytes()))
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute(
            "CREATE VIRTUAL TABLE skills USING fts5("
            + ", ".join(_COLUMNS)
            + ", tokenize='porter unicode61')"
        )
        conn.executemany(
            "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    r["name"],
                    r.get("title", ""),
                    r.get("category", ""),
                    r.get("description", ""),
                    " ".join(r.get("tags") or []),
                    r.get("instructions", ""),
                    r.get("risk", "safe"),
                    r.get("source", "community"),
                )
                for r in rows
            ],
        )
        conn.commit()
        self.count = len(rows)
        cats: dict[str, int] = {}
        for r in rows:
            cats[r.get("category", "uncategorised")] = cats.get(r.get("category", "uncategorised"), 0) + 1
        self.categories = dict(sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])))
        self._conn = conn
        logger.info("skill index ready: %d skills, %d categories", self.count, len(cats))
        return conn

    def add(self, rows: list[dict[str, Any]]) -> None:
        """Merge hand-written skills into the index so one search covers everything."""
        if not rows:
            return
        conn = self._connect()
        with self._lock:
            conn.executemany(
                "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        r["name"],
                        r.get("title", ""),
                        r.get("category", ""),
                        r.get("description", ""),
                        " ".join(r.get("tags") or []),
                        r.get("body", ""),
                        r.get("risk", "safe"),
                        "builtin",
                    )
                    for r in rows
                ],
            )
            conn.commit()
            self.count += len(rows)
            for r in rows:
                cat = r.get("category", "uncategorised")
                self.categories[cat] = self.categories.get(cat, 0) + 1
            self.categories = dict(sorted(self.categories.items(), key=lambda kv: (-kv[1], kv[0])))

    def warm(self) -> None:
        """Build the index eagerly (called on startup so the first search is instant)."""
        with self._lock:
            self._connect()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -------------------------------------------------------------------- query
    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        cur = conn.execute(sql, args)
        names = [c[0] for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        limit: int = 24,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Ranked search. Falls back from AND to OR, then to a substring scan."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        query = (query or "").strip()

        if not query:
            return self._browse(category, limit, offset)

        for mode in ("AND", "OR"):
            match = _fts_query(query, mode)
            if not match:
                break
            where, args = "skills MATCH ?", [match]
            if category:
                where += " AND category = ?"
                args.append(category)
            try:
                total = self._rows(f"SELECT count(*) AS n FROM skills WHERE {where}", tuple(args))[0]["n"]
            except sqlite3.OperationalError:
                continue
            if not total:
                continue
            rows = self._rows(
                f"SELECT name, title, category, description, tags, risk, source, "
                f"substr(body, 1, 400) AS excerpt, bm25(skills, "
                + ",".join(str(w) for w in _WEIGHTS)
                + ") AS score "
                f"FROM skills WHERE {where} ORDER BY score LIMIT ? OFFSET ?",
                tuple(args) + (limit, offset),
            )
            return {"skills": rows, "total": total, "query": query, "mode": f"fts-{mode.lower()}"}

        # Last resort: substring match (covers typos inside long words).
        like = f"%{query.lower()}%"
        where, args = "(lower(name) LIKE ? OR lower(title) LIKE ? OR lower(description) LIKE ?)", [like, like, like]
        if category:
            where += " AND category = ?"
            args.append(category)
        total = self._rows(f"SELECT count(*) AS n FROM skills WHERE {where}", tuple(args))[0]["n"]
        rows = self._rows(
            f"SELECT name, title, category, description, tags, risk, source, "
            f"substr(body, 1, 400) AS excerpt, 0.0 AS score "
            f"FROM skills WHERE {where} ORDER BY length(description) DESC LIMIT ? OFFSET ?",
            tuple(args) + (limit, offset),
        )
        return {"skills": rows, "total": total, "query": query, "mode": "like"}

    def _browse(self, category: str | None, limit: int, offset: int) -> dict[str, Any]:
        where, args = ("1=1", [])
        if category:
            where, args = "category = ?", [category]
        total = self._rows(f"SELECT count(*) AS n FROM skills WHERE {where}", tuple(args))[0]["n"]
        rows = self._rows(
            f"SELECT name, title, category, description, tags, risk, source, "
            f"substr(body, 1, 400) AS excerpt, 0.0 AS score "
            f"FROM skills WHERE {where} ORDER BY name LIMIT ? OFFSET ?",
            tuple(args) + (limit, offset),
        )
        return {"skills": rows, "total": total, "query": "", "mode": "browse"}

    def get(self, name: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT name, title, category, description, risk, source, body FROM skills WHERE name = ?",
            (name,),
        )
        if rows:
            return rows[0]
        # Friendly lookup: exact name misses are common (case, spacing).
        rows = self._rows(
            "SELECT name, title, category, description, risk, source, body FROM skills "
            "WHERE lower(name) = lower(?) LIMIT 1",
            (name,),
        )
        return rows[0] if rows else None

    def suggest(self, query: str, limit: int = 8) -> list[str]:
        return [r["name"] for r in self.search(query, limit=limit)["skills"]]
