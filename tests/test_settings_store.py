"""Tests for persistent preferences (§1): JSON round-trips, reserved-key
protection, and the active-course helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import settings_store
from app.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


def test_roundtrips_typed_values(db: Database):
    settings_store.set(db, "theme", "dark")
    settings_store.set(db, "retrieval_depth", 5)
    settings_store.set(db, "ai_enabled", False)
    settings_store.set(db, "export_defaults", {"format": "md", "scope": "course"})
    assert settings_store.get(db, "theme") == "dark"
    assert settings_store.get(db, "retrieval_depth") == 5
    assert settings_store.get(db, "ai_enabled") is False
    assert settings_store.get(db, "export_defaults")["scope"] == "course"


def test_get_default_when_missing(db: Database):
    assert settings_store.get(db, "nope", default="x") == "x"


def test_all_filters_reserved_schema_version(db: Database):
    settings_store.set(db, "theme", "light")
    everything = settings_store.all(db)
    assert everything["theme"] == "light"
    assert "schema_version" not in everything       # migration-owned, hidden


def test_update_ignores_reserved_keys(db: Database):
    before = db.schema_version()
    settings_store.update(db, {"schema_version": 999, "theme": "dark"})
    assert db.schema_version() == before            # not clobbered
    assert settings_store.get(db, "theme") == "dark"


def test_active_course_helpers(db: Database):
    assert settings_store.get_active_course(db) is None
    settings_store.set_active_course(db, 7)
    assert settings_store.get_active_course(db) == 7
    settings_store.set_active_course(db, None)
    assert settings_store.get_active_course(db) is None
