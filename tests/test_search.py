"""§2 library index + search: filtering, sorting, fuzzy fallback, related content."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import core, search


def _seed(tmp: Path, title: str, text: str, organize: str = "week"):
    item = core.LectureItem(title=title, url="u",
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": text}], text,
                       core.output_dir_for(tmp, item, organize), ["txt", "json"], 30, {})


def test_index_infers_week_topic_and_type(tmp_path: Path):
    _seed(tmp_path, "Week2_CPU_Scheduling", "round robin scheduling")
    items = search.build_index(tmp_path)
    cpu = next(it for it in items if "CPU" in it["title"])
    assert cpu["week"] == 2
    assert cpu["type"] == "transcript"
    assert "week-2" in cpu["tags"]


def test_library_view_filters_and_sorts(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro", "welcome to the course")
    _seed(tmp_path, "Week3_TCP", "transmission control protocol")
    # filter by week
    wk3 = search.library_view(tmp_path, week=3)
    assert wk3["count"] == 1 and wk3["items"][0]["week"] == 3
    # sort by week ascending
    byweek = search.library_view(tmp_path, sort="week")
    weeks = [it["week"] for it in byweek["items"]]
    assert weeks == [1, 3]
    # type filter excludes everything when no docs
    assert search.library_view(tmp_path, ftype="document")["count"] == 0


def test_search_exact_then_fuzzy(tmp_path: Path):
    _seed(tmp_path, "Week3_Transport", "the transmission control protocol is reliable")
    exact = search.search(tmp_path, "transmission")
    assert exact and exact[0]["via"] == "exact"
    assert exact[0]["week"] == 3
    # a near-miss title query finds nothing exact -> fuzzy title match kicks in
    fuzzy = search.search(tmp_path, "Transport")
    assert fuzzy and any(r["via"] == "fuzzy" for r in fuzzy)
    # fuzzy disabled -> no fallback
    assert search.search(tmp_path, "Transprt", fuzzy=False) == []


def test_related_prefers_same_week(tmp_path: Path):
    _seed(tmp_path, "Week5_Sockets", "socket programming")
    _seed(tmp_path, "Week5_Threads", "thread pools")
    _seed(tmp_path, "Week9_Security", "tls handshake")
    idx = search.build_index(tmp_path)
    sockets = next(it for it in idx if "Sockets" in it["title"])
    rel = search.related(tmp_path, sockets["path"])
    assert rel and "Week5_Threads" == rel[0]["title"]


def test_builtin_views_present():
    names = [v["name"] for v in search.BUILTIN_VIEWS]
    assert len(names) >= 6
    assert "Recent Imports" in names
