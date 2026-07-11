"""Tests for the lecture collection aggregator (§17).

`collections.build` is the join that lets one lecture render as one thing -
glossary terms, keywords, citations, notes, tags and siblings together - rather
than six separately-fetched panels. It is pure over the library + DB, so it is
tested without HTTP.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import collections, core
from app.database import Database


def _seed_lecture(out: Path, title: str, sentences):
    it = core.LectureItem(title=title, url="u", duration=600,
                          pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    segs = [{"start": i * 6, "end": i * 6 + 6, "text": s}
            for i, s in enumerate(sentences)]
    core.write_outputs(it, segs, " ".join(sentences),
                       core.output_dir_for(out, it, "week"),
                       ["txt", "json", "md"], 30, {"course": "CS234"})


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    _seed_lecture(tmp_path, "Week1 Networking Basics", [
        "TCP is a reliable transport protocol that guarantees ordered delivery.",
        "A router forwards packets between networks based on their addresses.",
        "Latency refers to the delay before a data transfer begins.",
        "Networks connect computers so they can exchange messages reliably."])
    _seed_lecture(tmp_path, "Week2 Routing", [
        "A routing table stores the paths a router uses to forward packets.",
        "Bandwidth means the maximum rate of data transfer across a link.",
        "Routing protocols exchange reachability information between routers."])
    return tmp_path


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


def _first_path(library: Path) -> str:
    groups = [g for g in core.list_transcripts(library) if core._is_transcript_group(g)]
    return (groups[0].get("folder", "") + "/" + groups[0]["stem"]).lstrip("/")


def test_unknown_lecture_raises_lookup_error(library):
    with pytest.raises(LookupError):
        collections.build(library, "no/such/lecture")


def test_collection_joins_every_derived_artifact(library):
    out = collections.build(library, _first_path(library), course_name="CS234")

    assert out["lecture"]["title"]
    assert out["formats"]                       # txt/json/md written by the seed
    assert out["glossary"], "definitions in the transcript should surface as terms"
    assert out["keywords"]
    assert "apa" in out["citations"]
    # the sibling lecture is reachable from this one
    assert out["related"]
    assert out["counts"]["related"] == len(out["related"])
    assert out["counts"]["glossary"] == len(out["glossary"])


def test_lecture_is_resolvable_by_any_of_its_file_paths(library):
    key = _first_path(library)
    by_key = collections.build(library, key)
    for fmt_path in by_key["formats"].values():
        # Same lecture every time, whichever of its files you name.
        assert collections.build(library, fmt_path)["canonical_path"] == by_key["canonical_path"]


def test_notes_are_scoped_to_the_file_the_user_opened(library, db):
    """The library lets you open a lecture's .txt or .md, and notes attach to
    whichever you opened. The collection must look them up under that same path,
    not silently under the lecture's primary file."""
    course = db.create_course("CS234")
    formats = collections.build(library, _first_path(library))["formats"]
    txt, md = formats["txt"], formats["md"]

    db.add_note(md, "note on the markdown", course_id=course)
    db.add_item_tag(md, "md-only", course_id=course)

    on_md = collections.build(library, md, db=db, course_id=course)
    assert [n["body"] for n in on_md["notes"]] == ["note on the markdown"]
    assert on_md["tags"] == ["md-only"]

    on_txt = collections.build(library, txt, db=db, course_id=course)
    assert on_txt["notes"] == [] and on_txt["tags"] == []


def test_notes_and_tags_come_from_the_database(library, db):
    key = _first_path(library)
    canonical = collections.build(library, key)["canonical_path"]
    course = db.create_course("CS234")

    db.add_note(canonical, "revisit the handshake", course_id=course, bookmark=True)
    db.add_item_tag(canonical, "exam", course_id=course)

    # A folder/stem key falls back to the lecture's primary file.
    out = collections.build(library, key, db=db, course_id=course)
    assert [n["body"] for n in out["notes"]] == ["revisit the handshake"]
    assert out["notes"][0]["bookmark"] is True
    assert out["tags"] == ["exam"]
    assert out["counts"]["notes"] == 1 and out["counts"]["tags"] == 1


def test_collection_without_a_db_still_builds(library):
    """The library is the source of truth; the DB only adds notes/tags."""
    out = collections.build(library, _first_path(library))
    assert out["notes"] == [] and out["tags"] == []
    assert out["glossary"] and out["citations"]
