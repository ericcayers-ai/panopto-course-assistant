"""Tests for the Notion study-database CSV export."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from app import core, study


def _seed(tmp_path: Path, title="Week9_Transport_Layer", outputs=("txt", "json", "summary")):
    it = core.LectureItem(title=title, url="u", duration=3600,
                          pub_date="Mon, 30 Mar 2026 02:00:00 GMT")
    text = "TCP is reliable. Flow control prevents overload. Congestion control avoids collapse."
    segs = [{"start": i * 5, "end": i * 5 + 5, "text": s} for i, s in enumerate(text.split(". "))]
    core.write_outputs(it, segs, text, core.output_dir_for(tmp_path, it, "week"),
                       list(outputs), 30, {"course": "COMPX234"})


def test_build_rows(tmp_path: Path):
    _seed(tmp_path)
    rows = study.build_study_rows(tmp_path, course="COMPX234")
    assert len(rows) == 1
    r = rows[0]
    assert r["Name"] == "Week9_Transport_Layer"
    assert r["Week"] == 9
    assert r["Date"] == "2026-03-30"
    assert "Transport Layer" in r["Topic"]
    assert "COMPX234" in r["Tags"] and "Week 9" in r["Tags"]
    assert r["Status"] == "Transcribed"
    assert r["Summary"]


def test_csv_has_header_and_no_blank_rows(tmp_path: Path):
    _seed(tmp_path, title="Week2_CPU")
    _seed(tmp_path, title="Week3_Memory")
    res = study.write_study_database(tmp_path, course="COMPX234")
    raw = (tmp_path / res["csv"]).read_bytes()
    assert b"\r\r\n" not in raw                      # no Windows double-newline
    parsed = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    assert parsed[0] == study.COLUMNS                # header
    assert all(row for row in parsed)               # no blank rows
    assert len(parsed) == 3                          # header + 2 lectures


def test_summary_prefers_summary_md(tmp_path: Path):
    _seed(tmp_path, outputs=("json", "summary"))
    rows = study.build_study_rows(tmp_path)
    assert rows[0]["Summary"]  # came from the .summary.md bullets


def test_export_excluded_from_library(tmp_path: Path):
    _seed(tmp_path)
    study.write_study_database(tmp_path)
    # _exports is internal, so the CSV never appears as a transcript
    assert [g["stem"] for g in core.list_transcripts(tmp_path)] == ["Week9_Transport_Layer"]


def test_empty_library(tmp_path: Path):
    res = study.write_study_database(tmp_path)
    assert res["count"] == 0
