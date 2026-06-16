"""Tests for the SQLite persistence layer (§1): schema, migrations, DAOs,
cascade deletes, idempotent backfill inserts, and crashed-job recovery."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.database import Database, SCHEMA_VERSION, init, get_db


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


def test_migrate_sets_latest_schema_version(db: Database):
    assert db.schema_version() == SCHEMA_VERSION >= 1


def test_reopening_db_does_not_remigrate(tmp_path: Path):
    p = tmp_path / "course_assistant.db"
    d1 = Database(p)
    cid = d1.create_course("Networks")
    d1.close()
    # reopening an existing DB must not error or wipe data
    d2 = Database(p)
    assert d2.schema_version() == SCHEMA_VERSION
    assert d2.get_course(cid)["name"] == "Networks"


def test_course_crud(db: Database):
    cid = db.create_course("Networks", code="COMPX234", semester="A", year=2026)
    row = db.get_course(cid)
    assert row["name"] == "Networks" and row["code"] == "COMPX234"
    assert row["year"] == 2026 and row["archived"] == 0
    assert db.update_course(cid, name="Networking", archived=1) is True
    row = db.get_course(cid)
    assert row["name"] == "Networking" and row["archived"] == 1
    assert db.update_course(cid) is False             # no-op update
    assert db.count_courses() == 1
    assert db.delete_course(cid) is True
    assert db.get_course(cid) is None


def test_cascade_delete_removes_children(db: Database):
    cid = db.create_course("Networks")
    db.insert_document(cid, title="Slides", path="_docs/slides.md", type="document")
    db.insert_transcript(cid, title="Week1", path="week-01/w1.txt", week=1)
    assert db.count_documents(cid) == 1 and db.count_transcripts(cid) == 1
    db.delete_course(cid)
    assert db.count_documents() == 0 and db.count_transcripts() == 0


def test_inserts_are_idempotent_by_path(db: Database):
    cid = db.create_course("Networks")
    db.insert_transcript(cid, title="Week1", path="week-01/w1.txt", week=1)
    db.insert_transcript(cid, title="Week1 again", path="week-01/w1.txt", week=1)
    assert db.count_transcripts() == 1                # UNIQUE(path) -> one row


def test_recover_running_jobs(db: Database):
    ts = "2026-06-16T00:00:00+00:00"
    db.insert_job("a", "transcribe", "A", "running", "downloading", 0.5, "{}", None, ts, ts)
    db.insert_job("b", "transcribe", "B", "queued", "", 0.0, "{}", None, ts, ts)
    db.insert_job("c", "transcribe", "C", "done", "done", 1.0, "{}", None, ts, ts)
    assert db.recover_running_jobs() == 2             # a + b reset, c untouched
    assert db.get_job("a")["status"] == "interrupted"
    assert db.get_job("b")["status"] == "interrupted"
    assert db.get_job("c")["status"] == "done"


def test_init_rebinds_default_and_closes_previous(tmp_path: Path):
    d1 = init(tmp_path / "one.db")
    assert get_db() is d1
    d2 = init(tmp_path / "two.db")              # should close d1, swap default
    assert get_db() is d2 and d2 is not d1
