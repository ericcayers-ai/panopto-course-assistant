"""Black-box API tests for the Moodle web-service import endpoints.

The HTTP layer of app.imports.moodle_api is patched so no real Moodle is hit;
everything else (routing, token storage, labelling, document download, outline
file, response shape) runs for real through FastAPI's TestClient.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_SECTIONS = [
    {"id": 1, "name": "Week 1 - Intro", "summary": "", "modules": [
        {"id": 11, "modname": "resource", "name": "Lecture 1", "contents": [
            {"type": "file", "filename": "Lecture 01 - Intro.pdf",
             "fileurl": "https://elearn.test/pluginfile.php/1/mod_resource/content/0/intro.pdf",
             "mimetype": "application/pdf", "filesize": 12}]},
        {"id": 12, "modname": "url", "name": "Recording",
         "url": "https://uni.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1",
         "contents": [{"type": "url",
                       "fileurl": "https://uni.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1"}]},
        {"id": 13, "modname": "quiz", "name": "Quiz 1",
         "url": "https://elearn.test/mod/quiz/view.php?id=13"},
    ]},
]


def _fake_post(url, data):
    if "login/token.php" in url:
        return 200, json.dumps({"token": "TESTTOKEN"})
    if "wsfunction=core_webservice_get_site_info" in url:
        return 200, json.dumps({"userid": 42, "sitename": "Test Uni",
                                "fullname": "Test Student", "version": "2022112800"})
    if "wsfunction=core_enrol_get_users_courses" in url:
        return 200, json.dumps([
            {"id": 77, "fullname": "COMPX234 Networks", "shortname": "COMPX234"}])
    if "wsfunction=core_course_get_contents" in url:
        return 200, json.dumps(_SECTIONS)
    return 200, json.dumps({"exception": "x", "errorcode": "nofn", "message": url})


def _fake_get(url):
    return 200, b"%PDF-1.4 fake", "intro.pdf", "application/pdf"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    # patch the network layer of the importer module the app uses
    monkeypatch.setattr(main.moodle_api, "_default_post", _fake_post)
    monkeypatch.setattr(main.moodle_api, "_default_get", _fake_get)
    return TestClient(main.app), tmp_path


def test_connect_lists_courses_and_stores_token(client):
    c, tmp = client
    r = c.post("/api/moodle/connect", json={
        "url": "https://elearn.test/course/view.php?id=77",
        "username": "stu", "password": "pw"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host"] == "elearn.test"
    assert body["sitename"] == "Test Uni"
    assert [cc["id"] for cc in body["courses"]] == [77]


def test_import_without_connect_is_rejected(client):
    c, _ = client
    r = c.post("/api/moodle/api-import", json={
        "url": "https://never.connected/", "course_id": 1})
    assert r.status_code == 400
    assert "connect first" in r.json()["detail"].lower()


def test_full_connect_then_import(client):
    c, tmp = client
    # connect (stores token)
    r = c.post("/api/moodle/connect", json={
        "url": "https://elearn.test/", "token": "PASTED"})
    assert r.status_code == 200, r.text

    # import course 77 - download docs, no markitdown convert (kept hermetic)
    r = c.post("/api/moodle/api-import", json={
        "url": "https://elearn.test/", "course_id": 77,
        "grab_docs": True, "convert": False, "grab_lectures": True})
    assert r.status_code == 200, r.text
    body = r.json()

    # labelling fidelity
    assert body["course"]["code"] == "COMPX234"
    assert body["counts"]["documents"] == 1
    assert body["counts"]["lectures"] == 1
    assert body["counts"]["activities"] == 1
    assert body["documents"][0]["filename"] == "Lecture 01 - Intro.pdf"
    assert body["activities"][0]["kind_label"] == "Quiz"

    # the document was downloaded under its exact (safe) name
    assert body["resources"]["downloaded"] == 1
    saved = (tmp / "_resources")
    assert (saved / "Lecture_01_-_Intro.pdf").exists()

    # the labelled outline was saved as an AI source
    outline = tmp / body["outline"]
    assert outline.exists()
    text = outline.read_text(encoding="utf-8")
    assert "COMPX234" in text and "## Documents" in text


def test_create_course_flag_activates_local_course(client):
    c, tmp = client
    c.post("/api/moodle/connect", json={"url": "https://elearn.test/", "token": "T"})
    r = c.post("/api/moodle/api-import", json={
        "url": "https://elearn.test/", "course_id": 77,
        "grab_docs": False, "create_course": True})
    assert r.status_code == 200, r.text
    local = r.json()["course"]["local_course"]
    assert local and local["name"].startswith("COMPX234")
    # it is now the active course
    courses_body = c.get("/api/courses").json()
    assert courses_body["active_course"] == local["id"]
