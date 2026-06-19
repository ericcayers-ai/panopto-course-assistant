"""v3 study features: glossary, keywords, workload, streak, next-up, citations,
practice quiz, study guide, plus the notes/tags DB DAOs. All dependency-free and
deterministic - no AI model, no network."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from app import (citations, core, glossary, keywords, lectures, nextup,
                 practice, streak, studyguide, workload)
from app.database import Database


def _seed_lecture(out: Path, title: str, sentences):
    it = core.LectureItem(title=title, url="u", duration=600,
                          pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    segs = [{"start": i * 6, "end": i * 6 + 6, "text": s}
            for i, s in enumerate(sentences)]
    text = " ".join(sentences)
    core.write_outputs(it, segs, text, core.output_dir_for(out, it, "week"),
                       ["txt", "json", "md"], 30, {"course": "CS234"})


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    _seed_lecture(tmp_path, "Week1 Networking Basics", [
        "TCP is a reliable transport protocol that guarantees ordered delivery.",
        "A router forwards packets between networks based on their addresses.",
        "Latency refers to the delay before a data transfer begins.",
        "The transport layer sits above the network layer in the stack.",
        "Networks connect computers so they can exchange messages reliably."])
    _seed_lecture(tmp_path, "Week2 Routing", [
        "A routing table stores the paths a router uses to forward packets.",
        "Bandwidth means the maximum rate of data transfer across a link.",
        "The transport layer multiplexes connections using port numbers.",
        "Routing protocols exchange reachability information between routers."])
    return tmp_path


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


# -- keywords ---------------------------------------------------------------

def test_keywords_ranks_domain_terms():
    text = ("The transport layer manages connections. The transport layer uses "
            "ports. Routing decides the path. Routing uses a routing table.")
    kws = keywords.keywords(text, limit=5)
    terms = [k["term"] for k in kws]
    assert "transport" in terms or "routing" in terms
    # stopwords never surface
    assert "the" not in terms and "uses" not in terms


def test_key_phrases_finds_multiword_concepts():
    text = "The transport layer is key. The transport layer handles ports. " \
           "A routing table maps routes. The routing table is consulted."
    phrases = [p["phrase"] for p in keywords.key_phrases(text, limit=10)]
    assert "transport layer" in phrases
    assert "routing table" in phrases


# -- glossary ---------------------------------------------------------------

def test_extract_terms_pulls_definitions():
    text = ("TCP is a reliable transport protocol that guarantees delivery. "
            "Latency refers to the delay before a transfer begins.")
    terms = {t["term"].lower(): t["definition"] for t in glossary.extract_terms(text)}
    assert "tcp" in terms
    assert "transport protocol" in terms["tcp"].lower()
    assert "latency" in terms


def test_build_glossary_over_library(library: Path):
    gloss = glossary.build_glossary(library, course="CS234")
    assert gloss["lectures_scanned"] == 2
    names = {t["term"].lower() for t in gloss["terms"]}
    assert "tcp" in names and "latency" in names
    md = glossary.glossary_markdown(gloss)
    assert "Glossary" in md and "TCP" in md


def test_write_glossary_creates_file(library: Path):
    res = glossary.write_glossary(library, course="CS234")
    assert (library / res["markdown"]).is_file()


# -- workload ---------------------------------------------------------------

def test_workload_estimate(library: Path):
    est = workload.estimate(library)
    assert est["lectures"] == 2
    assert est["total_words"] > 0
    assert est["total_review_min"] >= est["total_read_min"]  # review is slower
    weeks = {b["week"] for b in est["by_week"]}
    assert 1 in weeks and 2 in weeks


def test_workload_wpm_scales_time(library: Path):
    fast = workload.estimate(library, read_wpm=400)
    slow = workload.estimate(library, read_wpm=100)
    assert slow["total_read_min"] > fast["total_read_min"]


def test_humanize_minutes():
    assert workload.humanize_minutes(45) == "45m"
    assert workload.humanize_minutes(90) == "1h 30m"
    assert workload.humanize_minutes(120) == "2h"


# -- streak -----------------------------------------------------------------

def test_streak_counts_consecutive_days(db: Database):
    cid = db.create_course("CS234")
    for day in (17, 18, 19):
        db.log_study_session(cid, f"2026-06-{day}T08:00:00+00:00", 15, "review")
    now = dt.datetime(2026, 6, 19, 20, 0, tzinfo=dt.timezone.utc)
    s = streak.compute(db, now=now, goal_minutes=30)
    assert s["current_streak"] == 3
    assert s["longest_streak"] == 3
    assert s["active_today"] is True
    assert s["today_minutes"] == 15.0 and s["goal_met"] is False


def test_streak_goal_met_and_gap_breaks_streak(db: Database):
    cid = db.create_course("CS234")
    db.log_study_session(cid, "2026-06-15T08:00:00+00:00", 10, "review")
    db.log_study_session(cid, "2026-06-19T08:00:00+00:00", 40, "review")  # gap
    now = dt.datetime(2026, 6, 19, 20, 0, tzinfo=dt.timezone.utc)
    s = streak.compute(db, now=now, goal_minutes=30)
    assert s["current_streak"] == 1            # only today; the 15th doesn't chain
    assert s["goal_met"] is True and s["goal_pct"] == 100


# -- next-up ----------------------------------------------------------------

def test_nextup_ranks_overdue_assessment_first(db: Database, library: Path):
    cid = db.create_course("CS234")
    db.add_review_item(cid, "What is TCP?", "A transport protocol",
                       due="2026-06-10T00:00:00+00:00")
    db.create_assessment(cid, "Final Exam", due_date="2026-06-15", status="not_started")
    db.create_assessment(cid, "Far Off", due_date="2026-12-01", status="not_started")
    now = dt.datetime(2026, 6, 19, tzinfo=dt.timezone.utc)
    res = nextup.compute(db, library, course_id=cid, now=now)
    kinds = [a["kind"] for a in res["actions"]]
    assert res["actions"][0]["kind"] == "assessment"   # overdue beats due reviews
    assert "review" in kinds
    assert "summarize" in kinds                          # seeded lectures lack summaries
    # the far-off assessment (>14 days) is filtered out
    assert all(a.get("title") != "Far Off" for a in res["actions"])


# -- citations --------------------------------------------------------------

def test_citation_styles():
    meta = {"title": "Three-Way Handshake", "course": "CS234",
            "date": "2026-03-09", "video_url": "https://x/v"}
    out = citations.cite_all(meta)
    assert "2026" in out["apa"] and "Three-Way Handshake" in out["apa"]
    assert out["mla"].startswith('"Three-Way Handshake."') and out["mla"].endswith(".")
    assert out["bibtex"].startswith("@misc{") and "url = {https://x/v}" in out["bibtex"]


def test_citation_missing_date_is_nd():
    assert "n.d." in citations.cite({"title": "X", "course": "CS"}, "apa")


# -- practice quiz ----------------------------------------------------------

def _cards():
    return [{"front": "What is TCP?", "back": "A reliable transport protocol"},
            {"front": "What is a router?", "back": "Forwards packets between networks"},
            {"front": "What is latency?", "back": "Delay before transfer begins"},
            {"front": "What is bandwidth?", "back": "Maximum data transfer rate"}]


def test_practice_quiz_well_formed_and_deterministic():
    q1 = practice.build_quiz(_cards(), count=4, choices=4, seed=7)
    q2 = practice.build_quiz(_cards(), count=4, choices=4, seed=7)
    assert q1 == q2                                  # deterministic given a seed
    assert q1["count"] == 4
    for q in q1["questions"]:
        assert q["answer"] in q["options"]
        assert q["options"][q["answer_index"]] == q["answer"]
        assert len(q["options"]) == 4


def test_practice_quiz_grade():
    quiz = practice.build_quiz(_cards(), count=4, choices=4, seed=1)
    perfect = [q["answer_index"] for q in quiz["questions"]]
    res = practice.grade(quiz["questions"], perfect)
    assert res["score"] == 4 and res["pct"] == 100
    wrong = practice.grade(quiz["questions"], [-1, -1, -1, -1])
    assert wrong["score"] == 0


def test_practice_quiz_needs_distinct_answers():
    res = practice.build_quiz([{"front": "a", "back": "x"}], count=4)
    assert res["count"] == 0 and "reason" in res


def test_practice_from_db(db: Database):
    cid = db.create_course("CS234")
    for f, b in [("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3")]:
        db.add_review_item(cid, f, b, due="2026-06-10T00:00:00+00:00")
    quiz = practice.from_db(db, course_id=cid, count=3, seed=2)
    assert quiz["count"] == 3


# -- study guide ------------------------------------------------------------

def test_study_guide_assembles(library: Path):
    built = studyguide.build_markdown(library, course="CS234")
    assert built["lectures"] == 2
    md = built["markdown"]
    assert "# Study Guide" in md and "## Contents" in md
    assert "Key points" in md and "Glossary" in md
    res = studyguide.write_guide(library, course="CS234")
    assert (library / res["path"]).is_file()


# -- lectures read model ----------------------------------------------------

def test_iter_lectures(library: Path):
    lecs = lectures.iter_lectures(library)
    assert len(lecs) == 2
    assert all(lec["text"] for lec in lecs)
    assert {lec["week"] for lec in lecs} == {1, 2}


# -- notes DAO --------------------------------------------------------------

def test_notes_crud(db: Database):
    cid = db.create_course("CS234")
    nid = db.add_note("week1/lec.txt", "Key idea here", course_id=cid,
                      timestamp_s=42.5, bookmark=True)
    notes = db.list_notes(path="week1/lec.txt")
    assert len(notes) == 1 and notes[0]["body"] == "Key idea here"
    assert notes[0]["bookmark"] == 1 and notes[0]["timestamp_s"] == 42.5
    assert db.update_note(nid, body="Edited") is True
    assert db.list_notes(path="week1/lec.txt")[0]["body"] == "Edited"
    assert db.count_notes(cid) == 1
    assert db.delete_note(nid) is True
    assert db.list_notes(path="week1/lec.txt") == []


def test_notes_cascade_on_course_delete(db: Database):
    cid = db.create_course("CS234")
    db.add_note("p", "note", course_id=cid)
    db.delete_course(cid)
    assert db.count_notes() == 0


# -- tags DAO ---------------------------------------------------------------

def test_tags_dao(db: Database):
    db.add_item_tag("week1/lec.txt", "Important")
    db.add_item_tag("week1/lec.txt", "Exam")
    db.add_item_tag("week2/lec.txt", "Exam")
    assert set(db.tags_for_path("week1/lec.txt")) == {"Important", "Exam"}
    assert set(db.paths_for_tag("Exam")) == {"week1/lec.txt", "week2/lec.txt"}
    # idempotent (same tag/path) and case-insensitive tag reuse
    db.add_item_tag("week1/lec.txt", "important")
    assert db.tags_for_path("week1/lec.txt").count("Important") == 1
    listed = {r["name"]: r["n"] for r in db.list_tags()}
    assert listed["Exam"] == 2
    assert db.remove_item_tag("week1/lec.txt", "Exam") is True
    assert set(db.tags_for_path("week1/lec.txt")) == {"Important"}
    bulk = db.all_item_tags()
    assert "week2/lec.txt" in bulk and "Exam" in bulk["week2/lec.txt"]
