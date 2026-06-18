"""White-box tests for the Moodle web-service importer (app/imports/moodle_api.py).

Everything is exercised offline through the injectable ``http_post`` / ``http_get``
callables and the pure :func:`build_course_model` labeller - no real Moodle.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.imports import moodle_api as ma


# ---------------------------------------------------------------------------
# normalize_base_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://elearn.waikato.ac.nz/course/view.php?id=77547", "https://elearn.waikato.ac.nz/"),
    ("elearn.waikato.ac.nz", "https://elearn.waikato.ac.nz/"),
    ("https://moodle.x.edu/my/", "https://moodle.x.edu/"),
    ("https://moodle.x.edu/moodle/course/view.php?id=5", "https://moodle.x.edu/moodle/"),
    ("https://moodle.x.edu/login/index.php", "https://moodle.x.edu/"),
])
def test_normalize_base_url(raw, expected):
    assert ma.normalize_base_url(raw) == expected


def test_normalize_base_url_rejects_blank():
    with pytest.raises(ma.MoodleApiError):
        ma.normalize_base_url("")


# ---------------------------------------------------------------------------
# file_category
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,mime,cat", [
    ("Lecture 1.pdf", "application/pdf", "document"),
    ("slides.pptx", "", "document"),
    ("notes.docx", "", "document"),
    ("recording.mp4", "video/mp4", "video"),
    ("clip.MOV", "", "video"),
    ("audio.m4a", "", "audio"),
    ("podcast.mp3", "audio/mpeg", "audio"),
    ("diagram.png", "image/png", "image"),
    ("bundle.zip", "", "archive"),
    ("weird.xyz", "application/octet-stream", "other"),
    # mimetype fallback when extension is unknown
    ("blob", "video/webm", "video"),
    ("blob", "application/pdf", "document"),
])
def test_file_category(name, mime, cat):
    assert ma.file_category(name, mime) == cat


# ---------------------------------------------------------------------------
# build_course_model - the labelling heart
# ---------------------------------------------------------------------------

def _sample_sections():
    return [
        {"id": 0, "name": "General", "summary":
            '<a href="https://uni.hosted.panopto.com/Panopto/Podcast/abc.xml?type=mp4">Video podcast</a>',
         "modules": [
            {"id": 1, "modname": "label", "name": "Welcome label"},
        ]},
        {"id": 1, "name": "Week 1 - Foundations", "summary": "", "modules": [
            {"id": 11, "modname": "resource", "name": "Lecture 1 PDF", "contents": [
                {"type": "file", "filename": "Lecture 01 - Intro.pdf",
                 "fileurl": "https://m/pluginfile.php/1/mod_resource/content/0/intro.pdf",
                 "mimetype": "application/pdf", "filesize": 1000, "timemodified": 111}]},
            {"id": 12, "modname": "url", "name": "Recorded lecture",
             "url": "https://uni.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=xyz",
             "contents": [{"type": "url",
                           "fileurl": "https://uni.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=xyz"}]},
            {"id": 13, "modname": "folder", "name": "Week 1 Slides", "contents": [
                {"type": "file", "filename": "w1-slides.pptx",
                 "fileurl": "https://m/pluginfile.php/2/mod_folder/content/0/w1.pptx",
                 "mimetype": "", "filesize": 2000},
                {"type": "file", "filename": "bonus-recording.mp4",
                 "fileurl": "https://m/pluginfile.php/3/mod_folder/content/0/b.mp4",
                 "mimetype": "video/mp4", "filesize": 30000}]},
            {"id": 14, "modname": "quiz", "name": "Quiz 1",
             "url": "https://m/mod/quiz/view.php?id=14"},
            {"id": 15, "modname": "assign", "name": "Assignment 1",
             "url": "https://m/mod/assign/view.php?id=15"},
            {"id": 16, "modname": "url", "name": "Background reading",
             "url": "https://example.com/paper.html",
             "contents": [{"type": "url", "fileurl": "https://example.com/paper.html"}]},
            {"id": 17, "modname": "page", "name": "Course info page", "contents": [
                {"type": "file", "filename": "Course info page.html",
                 "fileurl": "https://m/pluginfile.php/4/mod_page/content/index.html",
                 "mimetype": "text/html", "filesize": 500}]},
        ]},
    ]


@pytest.fixture()
def model():
    return ma.build_course_model(
        _sample_sections(),
        {"id": 77, "fullname": "COMPX234-25B Computer Networks", "shortname": "COMPX234-25B"},
    )


def test_counts(model):
    c = model["counts"]
    assert c["sections"] == 2
    assert c["lectures"] == 2       # the panopto url + the bonus mp4 in the folder
    assert c["documents"] == 3      # pdf + pptx + html page  (mp4 is NOT a document)
    assert c["links"] == 1          # external reading
    assert c["activities"] == 2     # quiz + assignment
    assert c["panopto_feeds"] == 1  # from the General section summary


def test_course_code_extracted(model):
    assert model["course"]["code"] == "COMPX234-25B"
    assert model["course"]["fullname"] == "COMPX234-25B Computer Networks"


def test_lectures_are_video_only(model):
    names = {l["name"] for l in model["lectures"]}
    assert names == {"Recorded lecture", "Week 1 Slides"}
    for l in model["lectures"]:
        assert l["kind"] == "lecture"


def test_documents_keep_exact_filenames(model):
    files = {d["filename"] for d in model["documents"]}
    assert files == {"Lecture 01 - Intro.pdf", "w1-slides.pptx", "Course info page.html"}
    # the video inside the folder must NOT be a document
    assert "bonus-recording.mp4" not in files
    for d in model["documents"]:
        assert d["category"] in ("document", "image", "archive", "other")
        assert d["fileurl"]


def test_links_and_activities(model):
    assert model["links"][0]["name"] == "Background reading"
    labels = {a["kind_label"] for a in model["activities"]}
    assert labels == {"Quiz", "Assignment"}


def test_panopto_feed_normalised(model):
    assert model["panopto_feeds"][0].startswith("https://")
    assert "panopto" in model["panopto_feeds"][0].lower()


def test_outline_markdown_is_clean(model):
    md = model["outline_markdown"]
    assert md.startswith("# COMPX234-25B Computer Networks")
    assert "## Lectures / recordings" in md
    assert "## Documents" in md
    # plain text only - no emoji decoration (kept UTF-8 clean for AI sources)
    assert not any(ord(ch) >= 0x1F000 for ch in md)
    # section grouping present
    assert "### Week 1 - Week 1 - Foundations" in md


def test_empty_sections_safe():
    model = ma.build_course_model([], {"id": 1, "fullname": "Empty", "shortname": "E"})
    assert model["counts"]["sections"] == 0
    assert model["outline_markdown"].startswith("# Empty")


# ---------------------------------------------------------------------------
# MoodleClient - REST envelope, errors, token append
# ---------------------------------------------------------------------------

def _mock_post(routes):
    """Build an http_post that returns recorded JSON per wsfunction in the URL."""
    def post(url, data):
        for fn, payload in routes.items():
            if f"wsfunction={fn}" in url:
                status = payload.get("__status__", 200)
                return status, json.dumps(payload["body"]) if "body" in payload else payload["text"]
        return 200, json.dumps({"exception": "unknown", "errorcode": "nofunction",
                                "message": f"no route for {url}"})
    return post


def test_client_requires_token():
    with pytest.raises(ma.MoodleApiError):
        ma.MoodleClient("https://m/", "")


def test_client_site_info():
    post = _mock_post({"core_webservice_get_site_info":
                       {"body": {"userid": 5, "sitename": "Uni", "version": "2022112800"}}})
    cli = ma.MoodleClient("https://m/", "TOK", http_post=post)
    info = cli.site_info()
    assert info["userid"] == 5 and info["sitename"] == "Uni"


def test_client_invalid_token_message():
    post = _mock_post({"core_webservice_get_site_info":
                       {"body": {"exception": "moodle_exception", "errorcode": "invalidtoken",
                                 "message": "Invalid token"}}})
    cli = ma.MoodleClient("https://m/", "BAD", http_post=post)
    with pytest.raises(ma.MoodleApiError, match="invalid or expired"):
        cli.site_info()


def test_client_non_json_response():
    post = lambda url, data: (200, "<html>login</html>")
    cli = ma.MoodleClient("https://m/", "TOK", http_post=post)
    with pytest.raises(ma.MoodleApiError, match="non-JSON"):
        cli.call("core_webservice_get_site_info")


def test_client_http_error():
    post = lambda url, data: (500, "")
    cli = ma.MoodleClient("https://m/", "TOK", http_post=post)
    with pytest.raises(ma.MoodleApiError, match="HTTP 500"):
        cli.call("x")


def test_list_courses():
    post = _mock_post({
        "core_webservice_get_site_info": {"body": {"userid": 9}},
        "core_enrol_get_users_courses": {"body": [
            {"id": 1, "fullname": "Networks &amp; Systems", "shortname": "NET"},
            {"id": 2, "fullname": "Databases", "shortname": "DB"}]},
    })
    cli = ma.MoodleClient("https://m/", "TOK", http_post=post)
    courses = cli.list_courses()
    assert [c["id"] for c in courses] == [1, 2]
    assert courses[0]["fullname"] == "Networks & Systems"   # html-unescaped


def test_add_token_to_url():
    cli = ma.MoodleClient("https://m/", "SEKRET", http_post=lambda u, d: (200, "{}"))
    out = cli.add_token_to_url("https://m/pluginfile.php/1/x.pdf?forcedownload=1")
    assert "token=SEKRET" in out and "forcedownload=1" in out


# ---------------------------------------------------------------------------
# fetch_token
# ---------------------------------------------------------------------------

def test_fetch_token_success():
    post = lambda url, data: (200, json.dumps({"token": "abc123"}))
    assert ma.fetch_token("https://m/", "u", "p", http_post=post) == "abc123"


def test_fetch_token_error():
    post = lambda url, data: (200, json.dumps(
        {"error": "Invalid login", "errorcode": "invalidlogin"}))
    with pytest.raises(ma.MoodleApiError, match="Invalid login"):
        ma.fetch_token("https://m/", "u", "p", http_post=post)


def test_fetch_token_non_json():
    post = lambda url, data: (200, "<html/>")
    with pytest.raises(ma.MoodleApiError, match="valid token response"):
        ma.fetch_token("https://m/", "u", "p", http_post=post)


def test_fetch_token_sso_rejection():
    """SSO sites return loginerrorothers - must raise with SSO_REJECTED: prefix
    so the frontend can auto-switch to the token tab."""
    post = lambda url, data: (200, json.dumps(
        {"error": "Authentication with username and password is not used on this site.",
         "errorcode": "loginerrorothers"}))
    with pytest.raises(ma.MoodleApiError, match="SSO_REJECTED:"):
        ma.fetch_token("https://m/", "u", "p", http_post=post)


# ---------------------------------------------------------------------------
# build_launch_url / decode_launch_token - browser SSO token flow
# ---------------------------------------------------------------------------

def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode()).decode()


def test_build_launch_url():
    url = ma.build_launch_url("https://m.edu/course/view.php?id=9", passport="px")
    assert url == ("https://m.edu/admin/tool/mobile/launch.php"
                   "?service=moodle_mobile_app&passport=px&urlscheme=moodlemobile")


def test_decode_launch_token_standard():
    # Real Moodle format: base64(passport:::token:::privatetoken). The passport is
    # ALSO 32-hex, so a "looks like a token" heuristic would wrongly pick it - the
    # decoder must take the second field positionally.
    passport = "5fa1d9bdd6ccb22c871d58b275ed593d"
    token = "058dc43834290df552f52c70885af368"
    priv = "OVxE8MYC0VZZougHlemauEiLyjVwsm4S"
    raw = f"moodlemobile://token={_b64(f'{passport}:::{token}:::{priv}')}"
    assert ma.decode_launch_token(raw, expected_passport=passport) == token


def test_decode_launch_token_urlsafe_and_unpadded():
    import base64
    token = "058dc43834290df552f52c70885af368"
    payload = base64.urlsafe_b64encode(f"px:::{token}:::p".encode()).decode().rstrip("=")
    raw = "moodlemobile://token=" + payload
    assert ma.decode_launch_token(raw) == token


def test_decode_launch_token_legacy_json():
    token = "058dc43834290df552f52c70885af368"
    raw = "moodlemobile://token=" + _b64(json.dumps({"token": token, "privatetoken": "x"}))
    assert ma.decode_launch_token(raw) == token


def test_decode_launch_token_rejects_garbage():
    with pytest.raises(ma.MoodleApiError):
        ma.decode_launch_token("not a real url at all")
    with pytest.raises(ma.MoodleApiError):
        ma.decode_launch_token("")


# ---------------------------------------------------------------------------
# download_documents - exact names, dedup, error capture
# ---------------------------------------------------------------------------

def test_download_documents(tmp_path: Path):
    def http_get(url):
        # body encodes which file was requested via the path
        return 200, b"DATA:" + url.encode(), "ignored.bin", "application/pdf"
    cli = ma.MoodleClient("https://m/", "TOK",
                          http_post=lambda u, d: (200, "{}"), http_get=http_get)
    docs = [
        {"filename": "Lecture 01.pdf", "fileurl": "https://m/pluginfile.php/1/a.pdf",
         "category": "document", "section": "Week 1"},
        {"filename": "Lecture 01.pdf", "fileurl": "https://m/pluginfile.php/2/b.pdf",
         "category": "document", "section": "Week 2"},   # name collision
        {"filename": "no-url.pdf", "fileurl": "", "category": "document"},  # skipped
    ]
    res = ma.download_documents(cli, docs, tmp_path)
    assert res["downloaded"] == 2
    saved = {f["file"] for f in res["files"]}
    assert "Lecture_01.pdf" in saved
    assert "Lecture_01_2.pdf" in saved        # collision disambiguated
    for f in res["files"]:
        assert (tmp_path / f["file"]).exists()


def test_download_documents_records_errors(tmp_path: Path):
    def http_get(url):
        raise ma.MoodleApiError("403 Forbidden")
    cli = ma.MoodleClient("https://m/", "TOK",
                          http_post=lambda u, d: (200, "{}"), http_get=http_get)
    res = ma.download_documents(
        cli, [{"filename": "x.pdf", "fileurl": "https://m/pluginfile.php/1/x.pdf"}], tmp_path)
    assert res["downloaded"] == 0
    assert res["errors"][0]["error"] == "403 Forbidden"


# ---------------------------------------------------------------------------
# import_course - end-to-end orchestration with a mock client
# ---------------------------------------------------------------------------

def test_import_course_end_to_end():
    post = _mock_post({
        "core_webservice_get_site_info": {"body": {"userid": 9}},
        "core_enrol_get_users_courses": {"body": [
            {"id": 77, "fullname": "COMPX234 Networks", "shortname": "COMPX234"}]},
        "core_course_get_contents": {"body": _sample_sections()},
    })
    cli = ma.MoodleClient("https://m/", "TOK", http_post=post)
    model = ma.import_course(cli, 77)
    assert model["course"]["fullname"] == "COMPX234 Networks"
    assert model["counts"]["documents"] == 3
    assert model["counts"]["lectures"] == 2
