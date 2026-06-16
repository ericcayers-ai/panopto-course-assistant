"""Tests for the course service layer (§1): validation, active-course logic,
duplication, archiving, and per-course folder creation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import courses, settings_store
from app.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


def test_create_requires_a_name(db: Database):
    with pytest.raises(ValueError):
        courses.create_course(db, name="   ")


def test_first_course_becomes_active(db: Database):
    a = courses.create_course(db, name="Networks", code="COMPX234")
    assert settings_store.get_active_course(db) == a["id"]
    # a second course does not steal active focus
    courses.create_course(db, name="Algorithms")
    assert settings_store.get_active_course(db) == a["id"]


def test_create_makes_a_course_folder(db: Database, tmp_path: Path):
    courses.create_course(db, name="Networks", code="COMPX234")
    assert (tmp_path / "COMPX234").is_dir()


def test_list_includes_counts_and_archive_filter(db: Database):
    a = courses.create_course(db, name="Networks")
    courses.update_course(db, a["id"], archived=True)
    assert courses.list_courses(db, include_archived=False) == []
    full = courses.list_courses(db, include_archived=True)
    assert len(full) == 1 and "counts" in full[0]


def test_update_rejects_empty_name(db: Database):
    a = courses.create_course(db, name="Networks")
    with pytest.raises(ValueError):
        courses.update_course(db, a["id"], name="")


def test_duplicate_clones_metadata_as_new_course(db: Database):
    a = courses.create_course(db, name="Networks", code="COMPX234", year=2026)
    dup = courses.duplicate_course(db, a["id"])
    assert dup["id"] != a["id"]
    assert dup["name"] == "Networks (copy)"
    assert dup["code"] == "COMPX234" and dup["year"] == 2026


def test_delete_reassigns_active_course(db: Database):
    a = courses.create_course(db, name="A")
    b = courses.create_course(db, name="B")
    courses.set_active(db, a["id"])
    courses.delete_course(db, a["id"])
    # active falls back to a surviving course rather than dangling
    assert settings_store.get_active_course(db) == b["id"]


def test_delete_last_course_clears_active(db: Database):
    a = courses.create_course(db, name="Only")
    courses.delete_course(db, a["id"])
    assert settings_store.get_active_course(db) is None
