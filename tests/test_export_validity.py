"""Regression tests for export output validity (the 'jumbled/irrelevant text'
class of defects found by the export-validity audit).

Each test reproduces a confirmed defect and asserts the fixed behaviour, so the
exports keep shipping real, well-formed content.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import core, flashcards


def _seed_lecture(out: Path, title: str, segs, fmts=("txt", "json", "md")):
    it = core.LectureItem(title=title, url="u", duration=600,
                          pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    text = " ".join(s["text"] for s in segs)
    core.write_outputs(it, segs, text, core.output_dir_for(out, it, "week"),
                       list(fmts), 30, {"course": "CS234"})


# ---------------------------------------------------------------------------
# Fix 1 - an empty/interrupted transcript must not pollute the NotebookLM export
# ---------------------------------------------------------------------------

def test_empty_transcript_excluded_from_notebooklm(tmp_path: Path):
    _seed_lecture(tmp_path, "Week2 TCP", [
        {"start": 0, "end": 6, "text": "The transmission control protocol provides reliable delivery."},
        {"start": 6, "end": 12, "text": "It uses a three-way handshake to establish a connection."}])
    # a valid-but-empty json (interrupted transcription) with no other format
    it = core.LectureItem(title="Week6 Empty", url="", duration=0)
    core.write_outputs(it, [], "", core.output_dir_for(tmp_path, it, "week"),
                       ["json"], 30, {"course": "CS234"})

    res = core.export_notebooklm(tmp_path, combined=True, course="CS234")
    assert res["count"] == 1                                   # only the real lecture
    assert not any("Week6_Empty" in f for f in res["files"])
    pack = (tmp_path / res["combined"]).read_text(encoding="utf-8")
    assert "Week6 Empty" not in pack                           # not in TOC or body
    assert "transmission control protocol" in pack            # real content present


# ---------------------------------------------------------------------------
# Fix 2 - every section in a combined pack is anchored by its own heading
# ---------------------------------------------------------------------------

def test_headerless_document_gets_heading_in_everything_pack(tmp_path: Path):
    _seed_lecture(tmp_path, "Week1 Intro", [
        {"start": 0, "end": 6, "text": "Networks connect computers so they can exchange messages."}])
    docs = tmp_path / "_docs"
    docs.mkdir()
    (docs / "raw_notes.md").write_text(
        "Just plain prose with no heading at all. Second sentence here.", encoding="utf-8")

    res = core.export_all_sources(tmp_path, combined=True, course="CS234")
    pack = (tmp_path / res["combined"]).read_text(encoding="utf-8")
    # the document body must carry its own H1, not run on from the transcript
    assert "# raw notes" in pack
    # no section body starts with bare prose right after a rule
    for chunk in pack.split("\n---\n"):
        first = chunk.strip().splitlines()[0] if chunk.strip() else ""
        # every non-empty chunk begins with a markdown heading
        assert first.startswith("#"), f"headerless section: {first!r}"


def test_with_heading_helper():
    assert core.with_heading("Title", "# Already").startswith("# Already")
    assert core.with_heading("Title", "bare prose") == "# Title\n\nbare prose"
    assert core.with_heading("Title", "") == "# Title"


# ---------------------------------------------------------------------------
# Fix 3 - sentence splitting must not fragment on abbreviations
# ---------------------------------------------------------------------------

def test_split_sentences_keeps_abbreviations_whole():
    sents = core.split_sentences(
        "Prof. Smith explained the algorithm. It runs in O(n log n) time, e.g. mergesort. "
        "The result is optimal.")
    assert "Prof." not in sents and "e.g." not in sents
    assert any("Prof. Smith explained the algorithm." == s for s in sents)
    assert any("mergesort" in s for s in sents)
    assert len(sents) == 3


def test_split_sentences_handles_decimals():
    sents = core.split_sentences("The value is 3.14 today. It grew to 2.71 later.")
    assert len(sents) == 2
    assert "3.14" in sents[0]


def test_summary_has_no_fragment_bullets(tmp_path: Path):
    text = ("Prof. Smith introduced sorting. Mergesort runs in O(n log n) time, e.g. by divide "
            "and conquer. Quicksort can degrade to quadratic time when the pivot is poor. "
            "Randomised pivots fix this in expectation. The midterm covers chapters three to five, "
            "etc. Heapsort also achieves the n log n bound. Stable sorts preserve input order. "
            "Counting sort is linear for small key ranges.")
    points = core.summarize_text(text, max_sentences=5)
    assert points, "summary should not be empty"
    for p in points:
        # no degenerate one-word/abbreviation fragments
        assert len(p.split()) >= 3, f"fragment bullet: {p!r}"
        assert p not in ("Prof.", "e.g.", "etc.")


# ---------------------------------------------------------------------------
# Fix 4/5 - flashcards generate from natural prose and lowercase subjects
# ---------------------------------------------------------------------------

def test_flashcards_from_natural_prose():
    cards = flashcards.extract_cards(
        "The transmission control protocol provides reliable delivery of data between hosts. "
        "A relational database organizes data into tables of rows and columns. "
        "Normalization reduces redundancy across the schema.",
        ["CS234::Week02"], max_cards=10)
    assert len(cards) >= 2, "natural-verb sentences should yield cards"
    for c in cards:
        assert c["front"] and c["back"] and c["front"] != c["back"]
        assert isinstance(c["tags"], list)


def test_flashcards_lowercase_subject_and_fillers():
    cards = flashcards.extract_cards(
        "Okay so a deadlock is a situation where two processes wait forever for each other. "
        "Now throughput is the amount of work completed per unit of time.",
        ["CS234"], max_cards=10)
    fronts = " ".join(c["front"].lower() for c in cards)
    assert "deadlock" in fronts          # lowercase mid-sentence term captured
    assert "throughput" in fronts
    # the discourse filler must not leak into the question subject
    assert "okay so" not in fronts and "now throughput" not in fronts


def test_flashcards_acronym_still_works():
    cards = flashcards.extract_cards(
        "The Transmission Control Protocol (TCP) is a connection-oriented protocol.",
        ["CS234"], max_cards=10)
    assert any(c["front"] == "What does TCP stand for?" for c in cards)


# ---------------------------------------------------------------------------
# Labelling - hidden config dotfiles must never masquerade as transcripts
# (found by the black-box E2E: connecting to Moodle writes .secrets.json into
#  the output dir, whose .json was being counted as a phantom lecture).
# ---------------------------------------------------------------------------

def test_secrets_dotfile_not_listed_as_transcript(tmp_path: Path):
    # secrets-store sidecars land in the output dir; they must be invisible
    (tmp_path / ".secrets.json").write_text('{"moodle_token:host":"abc"}', encoding="utf-8")
    (tmp_path / ".secret_names.json").write_text('["moodle_token:host"]', encoding="utf-8")
    (tmp_path / ".secrets.key").write_text("k", encoding="utf-8")

    lib = core.list_library(tmp_path)
    assert lib["counts"]["transcripts"] == 0
    assert lib["counts"]["others"] == 0
    assert not any(g["stem"].startswith(".") for g in core.list_transcripts(tmp_path))

    # a real transcript alongside the dotfiles is still counted correctly
    _seed_lecture(tmp_path, "Week1 Networks", [
        {"start": 0, "end": 6, "text": "Networks let computers exchange messages over links."}])
    lib2 = core.list_library(tmp_path)
    assert lib2["counts"]["transcripts"] == 1


def test_course_outline_md_not_exported_as_transcript(tmp_path: Path):
    """A saved course outline (lone .md) is a source, not a lecture - it must not
    be counted/exported under 'Lecture transcripts' (label consistency between the
    library listing and the exporters)."""
    _seed_lecture(tmp_path, "Week1 Real Lecture", [
        {"start": 0, "end": 6, "text": "Caching stores frequently used data closer to the processor."}])
    (tmp_path / "COMPX234_outline.md").write_text(
        "# COMPX234\n\n## Outline\n\n- Routing notes\n- Recorded lecture\n", encoding="utf-8")

    # library: outline is 'other', not a transcript
    lib = core.list_library(tmp_path)
    assert lib["counts"]["transcripts"] == 1
    assert lib["counts"]["others"] == 1

    # notebooklm export: only the real lecture, never the outline
    nb = core.export_notebooklm(tmp_path, combined=True, course="COMPX234")
    assert nb["count"] == 1
    assert not any("outline" in f.lower() for f in nb["files"])

    # export_all: the outline is not double-counted as a transcript
    ex = core.export_all_sources(tmp_path, combined=True, course="COMPX234")
    assert ex["transcripts"] == 1
