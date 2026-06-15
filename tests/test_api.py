"""API-layer tests via FastAPI's TestClient.

The output directory is pinned to a temp folder (via PANOPTO_OUTPUT) before the
app module is imported, so these tests never touch the real ./transcripts.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)        # rebind OUTPUT_DIR to the temp path
    return TestClient(main.app), tmp_path


FEED = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel><title>COMPX234</title>
    <item><title>Week2_CPU</title>
      <enclosure url="http://x/y.mp4" length="100" type="video/mp4"/>
      <pubDate>Mon, 09 Mar 2026 02:13:40 GMT</pubDate>
      <itunes:duration>60</itunes:duration>
    </item>
  </channel></rss>"""


def _seed(tmp_path: Path):
    from app import core
    item = core.LectureItem(title="Week2_CPU", url="u", duration=60,
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": "TCP handshake basics"}],
                       "TCP handshake basics", core.output_dir_for(tmp_path, item, "week"),
                       ["txt", "json"], 30, {"course": "COMPX234"})


def test_status(client):
    c, _ = client
    r = c.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "engines" in body and "output_choices" in body


def test_feed_upload_and_bad_feed(client):
    c, _ = client
    r = c.post("/api/feed/upload", files={"file": ("feed.xml", FEED, "text/xml")})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["channel"] == "COMPX234"

    r2 = c.post("/api/feed", json={"source": ""})
    assert r2.status_code == 400


def test_transcripts_and_read_and_traversal(client):
    c, tmp = client
    _seed(tmp)
    items = c.get("/api/transcripts").json()["items"]
    assert len(items) == 1
    rel = items[0]["formats"]["txt"]
    assert "TCP handshake" in c.get("/api/transcript", params={"path": rel}).json()["content"]
    # traversal blocked -> 400
    assert c.get("/api/transcript", params={"path": "../../x"}).status_code == 400
    # missing -> 404
    assert c.get("/api/transcript", params={"path": "nope.txt"}).status_code == 404


def test_search(client):
    c, tmp = client
    _seed(tmp)
    r = c.get("/api/search", params={"q": "handshake"})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 1


def test_export_empty_then_success(client):
    c, tmp = client
    assert c.post("/api/export/notebooklm", json={}).status_code == 404
    _seed(tmp)
    r = c.post("/api/export/notebooklm", json={"combined": True, "course": "COMPX234"})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["combined"]


def test_organize(client):
    c, tmp = client
    from app import core
    item = core.LectureItem(title="Week5_Sockets", url="u",
                            pub_date="Mon, 30 Mar 2026 02:00:00 GMT")
    core.write_outputs(item, [{"start": 0, "end": 1, "text": "x"}], "x", tmp,
                       ["txt"], 30, {})  # flat
    r = c.post("/api/organize", json={"by": "week"})
    assert r.status_code == 200 and r.json()["moved"] >= 1
    assert c.post("/api/organize", json={"by": "bogus"}).status_code == 400


def test_transcribe_validation(client):
    c, _ = client
    # no engine installed in CI -> 503; but if one is present, lacking url -> 400.
    from app import transcribe
    r = c.post("/api/transcribe", json={"lecture": {"title": "t"}})
    if transcribe.engine_status()["any_engine"]:
        assert r.status_code == 400  # missing media URL
    else:
        assert r.status_code == 503


def test_pdf_and_materials_bad_path(client):
    c, _ = client
    assert c.post("/api/pdf/convert", json={"input_path": "C:/no/such/dir/xyz"}).status_code == 400
    assert c.get("/api/materials", params={"path": "C:/no/such/dir/xyz"}).status_code == 400
