"""API tests for GET /api/collections (§17).

PANOPTO_OUTPUT is pinned to a temp dir before importing app.main, as in test_api.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app), main, tmp_path


def _seed(tmp_path: Path):
    from app import core
    for title, sents in [
        ("Week1 Networking", [
            "TCP is a reliable transport protocol that guarantees delivery.",
            "Latency refers to the delay before a transfer begins.",
            "A router forwards packets between networks."]),
        ("Week2 Routing", [
            "Bandwidth means the maximum data transfer rate of a link.",
            "A routing table stores the paths used to forward packets."]),
    ]:
        it = core.LectureItem(title=title, url="u", duration=600,
                              pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
        segs = [{"start": i * 6, "end": i * 6 + 6, "text": s}
                for i, s in enumerate(sents)]
        core.write_outputs(it, segs, " ".join(sents),
                           core.output_dir_for(tmp_path, it, "week"),
                           ["txt", "json", "md"], 30, {"course": "CS234"})


def _first_lecture(c: TestClient) -> str:
    """The path the library actually opens: a concrete transcript file."""
    g = c.get("/api/transcripts").json()["items"][0]
    return g["formats"]["txt"]


def test_collections_requires_a_lecture(app_ctx):
    c, _, _ = app_ctx
    assert c.get("/api/collections").status_code == 422   # missing required query param


def test_unknown_lecture_is_404(app_ctx):
    c, _, tmp = app_ctx
    _seed(tmp)
    assert c.get("/api/collections", params={"lecture": "nope"}).status_code == 404


def test_collection_returns_every_derived_artifact(app_ctx):
    c, _, tmp = app_ctx
    _seed(tmp)
    cid = c.post("/api/courses", json={"name": "CS234"}).json()["id"]
    c.post(f"/api/courses/{cid}/activate")

    lec = _first_lecture(c)
    c.post("/api/tags", json={"path": lec, "name": "exam"})
    c.post("/api/notes", json={"path": lec, "body": "review the handshake"})

    d = c.get("/api/collections", params={"lecture": lec}).json()
    assert d["lecture"]["title"]
    assert d["glossary"] and d["keywords"]
    assert "apa" in d["citations"]
    assert d["related"], "the sibling lecture should be reachable"
    assert d["tags"] == ["exam"]
    assert [n["body"] for n in d["notes"]] == ["review the handshake"]
    assert d["counts"]["notes"] == 1 and d["counts"]["related"] == len(d["related"])
