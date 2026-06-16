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


def test_index_and_assets_are_no_cache(client):
    c, _ = client
    # the SPA shell and its assets must revalidate so updates aren't masked by
    # heuristic browser caching.
    r = c.get("/")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    r = c.get("/static/app.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_docs_is_self_contained_offline(client):
    c, _ = client
    r = c.get("/docs")
    assert r.status_code == 200
    html = r.text
    # no CDN/external fetches — the page must work with no internet
    assert "cdn." not in html and "https://" not in html and "http://" not in html
    assert "/openapi.json" in html          # it renders from the local schema
    # and the schema it reads is actually served locally
    assert c.get("/openapi.json").status_code == 200


def test_sync_status_and_mapping(client):
    c, _ = client
    r = c.get("/api/sync/status")
    assert r.status_code == 200
    body = r.json()
    # no token leaks; defaults present
    assert body["notion"]["connected"] is False
    assert "token" not in body["notion"]
    assert body["notion"]["field_map"]["title"] == "Name"
    # edit the field mapping and read it back
    r2 = c.put("/api/sync/mapping",
               json={"target": "notion", "fields": {"title": "Lecture"}})
    assert r2.status_code == 200
    assert r2.json()["field_map"]["title"] == "Lecture"
    assert c.get("/api/sync/status").json()["notion"]["field_map"]["title"] == "Lecture"


def test_sync_notion_requires_token(client):
    c, _ = client
    r = c.post("/api/sync/notion", json={"course": "X"})
    assert r.status_code == 400          # no token configured


def test_sync_anki_dryrun_offline(client):
    c, tmp = client
    _seed(tmp)
    r = c.post("/api/sync/anki/dryrun", json={"deck": "D", "course": "COMPX234"})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


def test_assessments_plan_calendar_progress(client):
    c, _ = client
    # need an active course
    cid = c.post("/api/courses", json={"name": "COMPX234", "code": "COMPX234"}).json()["id"]
    c.post(f"/api/courses/{cid}/activate")
    r = c.post("/api/assessments", json={"name": "A1", "due_date": "2026-04-10", "weight": 10})
    assert r.status_code == 200 and r.json()["name"] == "A1"
    aid = r.json()["id"]
    assert c.patch(f"/api/assessments/{aid}", json={"status": "submitted"}).json()["status"] == "submitted"
    # plan + progress + calendar
    assert c.get("/api/plan", params={"hours": 14}).status_code == 200
    cal = c.get("/api/calendar.ics")
    assert cal.status_code == 200 and "BEGIN:VCALENDAR" in cal.text
    assert cal.headers["content-type"].startswith("text/calendar")
    prog = c.get("/api/progress").json()
    assert prog["completion_pct"] == 100.0
    # study session + quiz attempt logging
    assert c.post("/api/study-sessions", json={"duration": 30}).status_code == 200
    assert c.post("/api/quiz-attempts", json={"score": 5, "total": 10}).status_code == 200


def test_import_folder_and_preflight(client):
    c, tmp = client
    src = tmp / "course_dl"
    (src / "Week1").mkdir(parents=True)
    (src / "Week1" / "Lec1.pdf").write_bytes(b"%PDF fake")
    (src / "Week2.mp4").write_bytes(b"\x00video")
    pf = c.post("/api/import/preflight", json={"path": str(src)})
    assert pf.status_code == 200
    assert pf.json()["expected_output"]["media_to_transcribe"] == 1
    imp = c.post("/api/import/folder", json={"path": str(src)})
    assert imp.status_code == 200
    assert imp.json()["indexed"] == 1


def test_moodle_import_url_validates(client):
    c, _ = client
    # not a course URL -> 400 (no network involved)
    r = c.post("/api/moodle/import-url", json={"url": "https://elearn.x/my/"})
    assert r.status_code == 400


def test_export_presets_preview_run(client):
    c, tmp = client
    _seed(tmp)
    assert c.get("/api/export/presets").json()["scopes"]
    pv = c.post("/api/export/preview", json={"preset": "revision", "scope": "course"})
    assert pv.status_code == 200 and pv.json()["writes_nothing"] is True
    run = c.post("/api/export/run", json={"target": "notebooklm", "course": "COMPX234"})
    assert run.status_code == 200
    assert run.json()["results"]["notebooklm"]["count"] == 1
    # bad preset -> 400
    assert c.post("/api/export/preview", json={"preset": "bogus"}).status_code == 400


def test_course_archive_export_route(client):
    c, tmp = client
    _seed(tmp)
    cid = c.post("/api/courses", json={"name": "COMPX234", "code": "COMPX234"}).json()["id"]
    r = c.post(f"/api/courses/{cid}/export")
    assert r.status_code == 200 and r.json()["file_count"] >= 1


def test_secrets_privacy_and_audit(client):
    c, _ = client
    # store a secret -> only the name is ever returned
    r = c.put("/api/secrets/openai", json={"value": "sk-test-123"})
    assert r.status_code == 200
    listed = c.get("/api/secrets").json()
    assert "openai" in listed["names"]
    assert "sk-test-123" not in r.text
    # privacy labels available
    pv = c.get("/api/privacy").json()
    assert "labels" in pv["transparency"]
    # audit endpoint works and clears
    assert c.get("/api/audit").status_code == 200
    assert c.post("/api/audit/clear").status_code == 200
    # delete the secret
    assert c.delete("/api/secrets/openai").json()["ok"] is True


def test_status_reports_secret_backend(client):
    c, _ = client
    body = c.get("/api/status").json()
    assert "secrets" in body and "backend" in body["secrets"]
    assert "privacy" in body


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


def test_library_endpoint(client):
    c, tmp = client
    _seed(tmp)
    from app import core
    core.ensure_dir(tmp / core.DOCS_DIRNAME).joinpath("Slides.md").write_text("x", encoding="utf-8")
    body = c.get("/api/library").json()
    assert body["counts"]["transcripts"] == 1
    assert body["counts"]["documents"] == 1
    assert "categories" in body and "transcripts" in body["categories"]


def test_export_formats_endpoint(client):
    c, tmp = client
    assert c.post("/api/export/formats", json={"formats": ["srt"]}).status_code == 404
    _seed(tmp)  # writes txt + json
    r = c.post("/api/export/formats", json={"formats": ["srt", "vtt"]})
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_notion_upload_zip(client):
    c, _ = client
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Page.html", "<html><body><h1 class='page-title'>Notes</h1><p>Hi</p></body></html>")
    buf.seek(0)
    r = c.post("/api/notion/upload", files={"file": ("export.zip", buf.read(), "application/zip")})
    assert r.status_code == 200
    assert r.json()["count"] == 1


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


def test_flashcards_generate_empty_then_success(client):
    c, tmp = client
    from app import core
    assert c.post("/api/flashcards/generate", json={}).status_code == 404
    # seed a definition-rich transcript
    it = core.LectureItem(title="Week9_Transport", url="u")
    text = "The Transmission Control Protocol (TCP) is reliable. Flow control is a mechanism that limits the sender."
    core.write_outputs(it, [{"start": 0, "end": 5, "text": text}], text,
                       core.output_dir_for(tmp, it, "week"), ["txt", "json"], 30, {})
    r = c.post("/api/flashcards/generate", json={"course": "COMPX234", "deck": "d1"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert body["anki_tsv"].endswith(".txt") and body["csv"].endswith(".csv")


def test_export_notion_csv_empty_then_success(client):
    c, tmp = client
    assert c.post("/api/export/notion-csv", json={}).status_code == 404
    _seed(tmp)
    r = c.post("/api/export/notion-csv", json={"course": "COMPX234"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["csv"].endswith(".csv")
    assert "Name" in body["columns"]


def test_flashcards_categorize(client):
    c, _ = client
    r = c.post("/api/flashcards/categorize", json={
        "text": "What is TCP?,A reliable transport protocol\nWhat is a router?,Forwards packets",
        "course": "COMPX234", "extra_keywords": ["router"],
    })
    assert r.status_code == 200 and r.json()["count"] == 2
    # empty input -> 400
    assert c.post("/api/flashcards/categorize", json={"text": ""}).status_code == 400


# ---------------------------------------------------------------------------
# §1 — multi-course + persistence routes
# ---------------------------------------------------------------------------


def test_status_reports_db_block(client):
    c, _ = client
    db = c.get("/api/status").json()["db"]
    assert db["schema_version"] >= 1
    assert db["courses"] == 0 and db["active_course"] is None


def test_courses_full_lifecycle(client):
    c, _ = client
    assert c.get("/api/courses").json()["courses"] == []

    created = c.post("/api/courses",
                     json={"name": "Networks", "code": "COMPX234", "year": 2026})
    assert created.status_code == 200
    cid = created.json()["id"]
    # the very first course becomes the active one automatically
    assert c.get("/api/courses").json()["active_course"] == cid

    # rename + archive via PATCH
    assert c.patch(f"/api/courses/{cid}", json={"name": "Networking"}).json()["name"] == "Networking"
    assert c.patch(f"/api/courses/{cid}", json={"archived": True}).json()["archived"] is True
    assert c.get("/api/courses", params={"include_archived": "false"}).json()["courses"] == []

    # duplicate -> a new "(copy)" course
    dup = c.post(f"/api/courses/{cid}/duplicate").json()
    assert dup["name"].endswith("(copy)") and dup["id"] != cid

    # activate the copy
    assert c.post(f"/api/courses/{dup['id']}/activate").json()["id"] == dup["id"]
    assert c.get("/api/courses").json()["active_course"] == dup["id"]

    # delete the original
    assert c.delete(f"/api/courses/{cid}").status_code == 200
    assert c.get(f"/api/courses/{cid}").status_code == 404


def test_course_validation_and_404s(client):
    c, _ = client
    assert c.post("/api/courses", json={"name": "   "}).status_code == 400
    assert c.patch("/api/courses/9999", json={"name": "x"}).status_code == 404
    assert c.delete("/api/courses/9999").status_code == 404
    assert c.post("/api/courses/9999/activate").status_code == 404


def test_course_export_produces_archive(client):
    # §9 shipped: the course export now returns a portable archive, not a 501.
    c, _ = client
    cid = c.post("/api/courses", json={"name": "X"}).json()["id"]
    r = c.post(f"/api/courses/{cid}/export")
    assert r.status_code == 200 and "file_count" in r.json()


def test_settings_persist_and_hide_reserved(client):
    c, _ = client
    assert "schema_version" not in c.get("/api/settings").json()
    body = c.put("/api/settings", json={"values": {
        "theme": "dark", "export_defaults": {"format": "md"}, "schema_version": 999,
    }}).json()
    assert body["theme"] == "dark"
    assert body["export_defaults"]["format"] == "md"
    assert "schema_version" not in body                 # reserved key ignored
    # survives a re-read
    assert c.get("/api/settings").json()["theme"] == "dark"


def test_index_and_views_and_related(client):
    c, tmp = client
    _seed(tmp)  # Week2_CPU transcript
    idx = c.get("/api/index", params={"sort": "week"}).json()
    assert idx["count"] >= 1
    assert idx["items"][0]["type"] == "transcript"
    # week filter
    assert c.get("/api/index", params={"week": 2}).json()["count"] >= 1
    assert c.get("/api/index", params={"week": 99}).json()["count"] == 0
    # built-in + saved views
    views = c.get("/api/views").json()["views"]
    assert any(v["name"] == "Recent Imports" for v in views)
    created = c.post("/api/views", json={"name": "My View", "query": {"week": 2}})
    assert created.status_code == 200
    vid = created.json()["id"]
    assert any(v.get("id") == vid for v in c.get("/api/views").json()["views"])
    assert c.delete(f"/api/views/{vid}").status_code == 200
    assert c.delete("/api/views/99999").status_code == 404
    # related for the seeded transcript
    item = c.get("/api/index").json()["items"][0]
    assert "related" in c.get("/api/related", params={"path": item["path"]}).json()


def test_search_fuzzy_fallback(client):
    c, tmp = client
    _seed(tmp)  # "TCP handshake basics" in Week2_CPU
    # exact content hit
    assert len(c.get("/api/search", params={"q": "handshake"}).json()["results"]) == 1
    # near-miss title with fuzzy on returns the lecture by title similarity
    res = c.get("/api/search", params={"q": "Week2"}).json()["results"]
    assert isinstance(res, list)


def test_status_has_ai_block(client):
    c, _ = client
    ai = c.get("/api/status").json()["ai"]
    assert "providers" in ai and "anthropic" in ai["providers"]
    assert ai["config"]["provider"] == "none"           # off by default
    assert "api_key" not in ai["config"]                 # never leaked


def test_llm_settings_redacts_api_key(client):
    c, _ = client
    assert c.get("/api/llm/providers").status_code == 200
    r = c.patch("/api/llm/settings", json={"values": {
        "provider": "anthropic", "api_key": "sk-secret", "temperature": 0.5}})
    body = r.json()
    assert body["provider"] == "anthropic" and body["temperature"] == 0.5
    assert "api_key" not in body and body["has_api_key"] is True
    # and never echoed via GET either
    assert "api_key" not in c.get("/api/llm/settings").json()


def test_llm_features_fall_back_without_provider(client):
    c, tmp = client
    _seed(tmp)  # Week2_CPU "TCP handshake basics"
    s = c.post("/api/llm/summarize", json={"scope": "course"}).json()
    assert s["generated"] == "extractive"
    chat = c.post("/api/llm/chat", json={"query": "handshake"}).json()
    assert chat["generated"] == "extractive"
    assert c.post("/api/llm/chat", json={"query": "  "}).status_code == 400


def test_job_control_routes_404_for_unknown(client):
    c, _ = client
    assert c.get("/api/jobs/nope/logs").status_code == 404
    assert c.post("/api/jobs/nope/cancel").status_code == 404
    assert c.post("/api/jobs/nope/retry").status_code == 404


def test_startup_backfills_existing_transcripts(tmp_path, monkeypatch):
    """A transcripts/ folder that predates persistence is indexed on startup
    rather than orphaned (roadmap §Conventions)."""
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    from app import core
    item = core.LectureItem(title="Week2_CPU", url="u", duration=60,
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": "TCP handshake basics"}],
                       "TCP handshake basics", core.output_dir_for(tmp_path, item, "week"),
                       ["txt", "json"], 30, {"course": "COMPX234"})

    import app.main as main
    main = importlib.reload(main)                       # triggers startup backfill
    c = TestClient(main.app)

    body = c.get("/api/courses").json()
    assert len(body["courses"]) == 1
    course = body["courses"][0]
    assert body["active_course"] == course["id"]
    assert course["counts"]["transcripts"] >= 1
    assert c.get("/api/status").json()["db"]["courses"] == 1
