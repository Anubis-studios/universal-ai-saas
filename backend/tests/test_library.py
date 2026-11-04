import os
import tempfile
from pathlib import Path

import pytest

from app.services.library_db import LibraryDB


@pytest.fixture
def db(tmp_path):
    handle = LibraryDB(tmp_path / "t.db").connect()
    yield handle
    handle.close()


def test_sqlite_is_the_automatic_default(db):
    status = db.status()
    assert status["backend"] == "sqlite"
    assert status["free"] is True
    assert status["full_text_search"] is True


def test_add_get_and_hydrate_payload(db):
    record = db.add(
        "game", title="Neon Wormhole Speeder", prompt="dodge asteroids",
        model="pollinations/x", url="/games/x.html", payload={"lines": 233},
    )
    assert record["payload"] == {"lines": 233}
    assert record["created_at"].endswith("Z")
    assert db.get(record["id"])["title"] == "Neon Wormhole Speeder"


def test_full_text_search_ranks_and_filters(db):
    db.add("image", prompt="a coral reef at dusk", title="reef")
    db.add("game", prompt="asteroid dodging arcade game", title="speeder")
    assert db.search("asteroid")["total"] == 1
    assert db.search("reef")["records"][0]["kind"] == "image"
    assert db.search("", kind="game")["total"] == 1
    assert db.search("nonexistentterm")["total"] == 0


def test_search_survives_punctuation_and_empty_input(db):
    db.add("chat", prompt="what is SQLite?", title="sqlite")
    assert db.search("SQLite?!")["total"] >= 1
    assert db.search("   ")["total"] == 1


def test_pagination_and_counts(db):
    for i in range(7):
        db.add("chat", prompt=f"prompt {i}", title=f"t{i}")
    page = db.search("", limit=3, offset=0)
    assert page["count"] == 3
    assert page["total"] == 7
    assert len(db.search("", limit=3, offset=6)["records"]) == 1


def test_delete_removes_from_index_too(db):
    record = db.add("chat", prompt="ephemeral", title="gone")
    assert db.delete(record["id"]) is True
    assert db.search("ephemeral")["total"] == 0
    assert db.delete(record["id"]) is False


def test_stats_group_by_kind(db):
    db.add("chat", title="a")
    db.add("chat", title="b")
    db.add("game", title="c")
    assert db.stats()["by_kind"] == {"chat": 2, "game": 1}


def test_data_persists_across_reconnect(tmp_path):
    path = tmp_path / "keep.db"
    first = LibraryDB(path).connect()
    first.add("image", prompt="persist me", title="keeper")
    first.close()
    second = LibraryDB(path).connect()
    assert second.search("persist")["total"] == 1
    second.close()


def test_huge_payload_is_truncated_not_fatal(db):
    record = db.add("image", title="big", payload={"b64": "x" * 500_000})
    assert len(record["payload"]["b64"]) <= 200_000


async def test_api_records_and_serves_library(client):
    status = await client.get("/api/v1/db/status")
    assert status.status_code == 200
    assert status.json()["backend"] in {"sqlite", "turso", "postgres"}

    listing = await client.get("/api/v1/library")
    assert listing.status_code == 200
    assert "records" in listing.json()

    missing = await client.get("/api/v1/library/nope")
    assert missing.status_code == 404
    assert (await client.delete("/api/v1/library/nope")).status_code == 404


# ---------------------------------------------------------------- durability
def test_snapshot_survives_a_fresh_connection(tmp_path):
    """A recycled container reconnects to the same file: no records lost."""
    path = tmp_path / "s.db"
    first = LibraryDB(path).connect()
    first.add("game", title="Speeder", prompt="dodge", payload={"lines": 233})
    first.save_snapshot()
    first.close()

    second = LibraryDB(path).connect()
    try:
        assert second.search("")["total"] == 1
        assert second.search("dodge")["records"][0]["title"] == "Speeder"
    finally:
        second.close()


def test_snapshot_restores_when_the_database_file_is_lost(tmp_path):
    """Render's ephemeral disk wipes the .db but the snapshot sits beside it."""
    path = tmp_path / "s.db"
    first = LibraryDB(path).connect()
    first.add("image", title="reef", prompt="a coral reef")
    first.save_snapshot()
    first.close()

    path.unlink()
    for orphan in tmp_path.glob("s.db-wal"), tmp_path.glob("s.db-shm"):
        for file in orphan:
            file.unlink(missing_ok=True)

    second = LibraryDB(path).connect()
    try:
        assert second.search("coral")["total"] == 1
    finally:
        second.close()


def test_export_and_import_round_trip_merge(db, tmp_path):
    record = db.add("game", title="Speeder", prompt="dodge asteroids")
    dump = db.dump()
    assert dump["records"][0]["id"] == record["id"]

    other = LibraryDB(tmp_path / "other.db").connect()
    try:
        assert other.restore(dump) == 1
        assert other.restore(dump) == 0, "importing twice must not duplicate"
        assert other.get(record["id"])["title"] == "Speeder"
        assert other.get(record["id"])["created_at"] == record["created_at"]
    finally:
        other.close()


def test_snapshot_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_SNAPSHOT", "0")
    handle = LibraryDB(tmp_path / "off.db").connect()
    try:
        handle.add("chat", prompt="hello")
        assert handle.save_snapshot() is False
        assert handle.status()["snapshot"] is None
    finally:
        handle.close()
