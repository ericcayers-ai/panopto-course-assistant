"""
imports/moodle_api.py - import a Moodle course through the official Moodle
Mobile **web-service API** instead of scraping HTML or reading browser cookies.

Why this exists
---------------
The earlier importer fetched the rendered course page with the browser's session
cookies and recovered structure with regexes. That is fragile: file names come
from anchor text (often truncated or wrong), the activity *type* has to be
guessed, and Panopto/other content is easy to miss. Moodle already exposes a
clean, typed REST API used by its own mobile app - given a *web-service token*
it returns the exact course tree: every section, every module with its real
``modname`` (resource / folder / page / url / quiz / assign / lti / …), and
every file with its exact ``filename``, ``mimetype``, ``filesize`` and a
downloadable ``fileurl``. That is what lets us label lectures, documents and
links with 100% fidelity - no guessing.

Acknowledgement
---------------
The REST conventions used here (the ``webservice/rest/server.php`` envelope, the
``login/token.php`` token grant, appending ``token=`` to ``pluginfile.php`` URLs
for downloads, and the section→module→contents shape consumed by
:func:`build_course_model`) follow the approach proven by **Moodle-DL**
(https://github.com/C0D3V/Moodle-DL, GPL-3.0). This is a focused, dependency-
light re-implementation for the course-assistant import flow, not a vendoring of
that project.

Testability
-----------
All network is funnelled through two small, injectable callables
(``http_post`` / ``http_get``). :func:`build_course_model` - which holds the
labelling logic the rest of the app depends on - is pure: it turns a raw
``core_course_get_contents`` payload into a labelled model with no I/O, so it is
covered exhaustively offline with recorded API fixtures.
"""
from __future__ import annotations

import json as _json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from .. import core
from ..errors import AppError

# http_post(url, data) -> (status_code, response_text)
HttpPost = Callable[[str, Dict[str, str]], Tuple[int, str]]
# http_get(url) -> (status_code, body_bytes, filename, content_type)
HttpGet = Callable[[str], Tuple[int, bytes, str, str]]

MOBILE_SERVICE = "moodle_mobile_app"


class MoodleApiError(AppError):
    """Raised when the Moodle web service rejects a request or is unreachable."""

    category = "authentication"
    status_code = 400


# ---------------------------------------------------------------------------
# Labelling vocabulary - the single source of truth for how Moodle module and
# file types map onto the assistant's lecture / document / link / activity
# buckets. Kept here (not scattered) so labelling is consistent and testable.
# ---------------------------------------------------------------------------

# Module kinds whose payload is *always* lecture/recording content.
# Moodle errorcode values that mean "username/password login is not available here
# because this site uses SSO / external authentication."  The user must paste a
# token obtained from their Moodle security keys page instead.
_SSO_CODES = frozenset({
    "loginerrorothers",      # external-auth plugin - password login disabled
    "loginerrorexternal",    # variant used by some SSO plugins
    "webservicesnotenabled", # mobile web services completely disabled
})

_VIDEO_MODNAMES = {"panopto", "kalvidres", "helixmedia", "kalvidpres", "facetoface_video"}
# Module kinds that carry downloadable course files (documents).
_FILE_MODNAMES = {"resource", "folder", "page", "book", "imscp"}
# Hosts that indicate a lecture/recording when seen in a url module or link.
_VIDEO_HOSTS = (
    "panopto", "youtube.com", "youtu.be", "vimeo.com", "echo360",
    "kaltura", "zoom.us", "stream.", "mediasite", "video.",
)
_VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv", ".flv", ".wmv"}
_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".oga", ".flac", ".opus"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tif", ".tiff"}
_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
_DOC_EXTS = {e.lower() for e in core.DOC_EXTS}

# Friendly labels for Moodle activity module types (outline metadata only).
_ACTIVITY_KIND = {
    "assign": "Assignment", "quiz": "Quiz", "forum": "Forum", "choice": "Choice",
    "lesson": "Lesson", "glossary": "Glossary", "wiki": "Wiki", "workshop": "Workshop",
    "feedback": "Feedback", "data": "Database", "scorm": "SCORM",
    "h5pactivity": "Interactive", "chat": "Chat", "survey": "Survey",
    "attendance": "Attendance", "lti": "External tool",
}


def file_category(filename: str, mimetype: str = "") -> str:
    """Classify a single file into video / audio / document / image / archive /
    other from its extension first, then its mimetype. Extension wins because
    Moodle's mimetype can be a generic ``application/octet-stream``."""
    ext = Path(filename or "").suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _DOC_EXTS:
        return "document"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _ARCHIVE_EXTS:
        return "archive"
    mt = (mimetype or "").lower()
    if mt.startswith("video/"):
        return "video"
    if mt.startswith("audio/"):
        return "audio"
    if mt.startswith("image/"):
        return "image"
    if mt in ("application/pdf",) or "officedocument" in mt or "msword" in mt or "ms-powerpoint" in mt:
        return "document"
    return "other"


def _looks_like_video_url(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in _VIDEO_HOSTS)


# ---------------------------------------------------------------------------
# Low-level HTTP (injectable) + REST client
# ---------------------------------------------------------------------------

def _default_post(url: str, data: Dict[str, str]) -> Tuple[int, str]:
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 CourseAssistant MoodleMobile",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        r = requests.post(url, data=data, headers=headers, timeout=60)
        return r.status_code, r.text
    except Exception as e:  # pragma: no cover - network failure path
        raise MoodleApiError(f"Connection error: {e}") from e


def _default_get(url: str) -> Tuple[int, bytes, str, str]:
    import requests
    from urllib.parse import unquote
    headers = {"User-Agent": "Mozilla/5.0 CourseAssistant MoodleMobile"}
    try:
        r = requests.get(url, headers=headers, timeout=180, allow_redirects=True)
    except Exception as e:  # pragma: no cover - network failure path
        raise MoodleApiError(f"Connection error: {e}") from e
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    fname = ""
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
    if m:
        fname = unquote(m.group(1).strip())
    if not fname:
        fname = unquote(Path(urlparse(r.url).path).name)
    return r.status_code, r.content, fname, ctype


def normalize_base_url(url: str) -> str:
    """Return the Moodle site root (scheme://host[/path]/) for a pasted course or
    site URL. Strips any ``/course/view.php`` / ``/my`` / ``/login`` tail."""
    url = (url or "").strip()
    if not url:
        raise MoodleApiError("A Moodle site URL is required.")
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    if not parts.hostname:
        raise MoodleApiError(f"That does not look like a URL: {url!r}")
    path = parts.path or "/"
    # Trim known Moodle entry-point tails to recover the wwwroot.
    for marker in ("/course/", "/my", "/login", "/webservice/", "/user/", "/mod/"):
        i = path.find(marker)
        if i >= 0:
            path = path[:i]
            break
    path = path.rstrip("/") + "/"
    return urlunparse((parts.scheme, parts.netloc, path, "", "", ""))


def fetch_token(base_url: str, username: str, password: str, *,
                service: str = MOBILE_SERVICE, http_post: Optional[HttpPost] = None) -> str:
    """Exchange a username + password for a web-service token via
    ``login/token.php``. Many institutions that use SSO disable this endpoint;
    in that case the user pastes a token from their Moodle mobile-app settings
    instead. Raises :class:`MoodleApiError` with a readable reason."""
    base = normalize_base_url(base_url)
    post = http_post or _default_post
    data = {"username": username, "password": password, "service": service}
    status, text = post(f"{base}login/token.php", data)
    if status != 200:
        raise MoodleApiError(f"The Moodle site returned HTTP {status} for the sign-in request.")
    try:
        payload = _json.loads(text)
    except ValueError:
        raise MoodleApiError("The Moodle site did not return a valid token response. "
                             "Web services may be disabled, or this is not a Moodle site.") from None
    if "token" in payload:
        return str(payload["token"])
    err_code = payload.get("errorcode", "")
    err_msg = payload.get("error") or err_code or "unknown error"
    if err_code in _SSO_CODES:
        raise MoodleApiError(
            "SSO_REJECTED: This Moodle site uses SSO or external authentication - "
            "username/password sign-in is disabled. Open your Moodle security keys "
            "page to get a token, then use the ‘Paste a token’ tab."
        )
    raise MoodleApiError(f"Could not obtain a token: {err_msg}")


# --- Browser SSO launch flow (for Microsoft / Google SSO Moodle sites) --------
#
# Sites that use SSO disable login/token.php, and many hide the mobile token from
# the security-keys page. Moodle's mobile app instead uses a *browser* launch:
#   1. open  {base}admin/tool/mobile/launch.php?service=…&passport=<rand>&urlscheme=…
#   2. the browser authenticates through the institution's SSO
#   3. Moodle redirects to  <urlscheme>://token=<BASE64>
#      where BASE64 = base64( "<passport>:::<token>:::<privatetoken>" )
# We open step 1 in a new tab and have the user paste the final URL back; this
# helper decodes it. (Protocol per Moodle admin/tool/mobile/launch.php.)

URL_SCHEME = "moodlemobile"


def build_launch_url(base_url: str, *, passport: str = "courseassistant",
                     service: str = MOBILE_SERVICE, url_scheme: str = URL_SCHEME) -> str:
    """Return the ``admin/tool/mobile/launch.php`` URL that starts the browser SSO
    token flow. ``passport`` is echoed back inside the redirect so we can verify
    the response matches our request."""
    base = normalize_base_url(base_url)
    return (f"{base}admin/tool/mobile/launch.php"
            f"?service={service}&passport={passport}&urlscheme={url_scheme}")


# A Moodle web-service token is a 32-character lowercase hex string.
_WS_TOKEN_RE = re.compile(r"\b[a-f0-9]{32}\b")


def decode_launch_token(raw: str, *, expected_passport: str = "") -> str:
    """Extract the web-service token from the ``moodlemobile://token=…`` URL that
    Moodle issues after a successful browser SSO login.

    Handles the standard ``base64(passport:::token:::privatetoken)`` payload, the
    older ``base64(json)`` payload, and a bare 32-hex token as a last resort.
    Raises :class:`MoodleApiError` with a readable reason on failure."""
    import base64 as _b64

    s = (raw or "").strip()
    # Strip any "<scheme>://token=" prefix (any scheme, in case the user changed it).
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://token=", "", s, flags=re.IGNORECASE)
    s = s.strip().strip('"').strip("'")
    if not s:
        raise MoodleApiError("Paste the full moodlemobile:// URL from your browser's address bar.")

    # URL-decode in case the browser percent-encoded the payload.
    from urllib.parse import unquote
    s = unquote(s)

    # base64 may be standard or URL-safe; pad to a multiple of 4.
    candidate = s.replace("-", "+").replace("_", "/")
    candidate += "=" * (-len(candidate) % 4)
    decoded = b""
    try:
        decoded = _b64.b64decode(candidate)
    except Exception:
        decoded = b""

    if decoded:
        try:
            text = decoded.decode("utf-8", "replace")
        except Exception:
            text = ""
        # Standard format: passport:::token:::privatetoken
        # The token is ALWAYS the second field. Note the passport is itself a
        # 32-hex string (Moodle re-issues it), so a "looks like a token" heuristic
        # would wrongly pick the passport - we must index positionally.
        if ":::" in text:
            parts = text.split(":::")
            if len(parts) >= 2 and parts[1].strip():
                return parts[1].strip()
            if parts and parts[0].strip():        # 1-field variant: token only
                return parts[0].strip()
        # Older format: base64(json)
        try:
            payload = _json.loads(text)
            tok = payload.get("token") or payload.get("wstoken") or payload.get("moodletoken")
            if tok:
                return str(tok)
        except Exception:
            pass
        # Any 32-hex token embedded in the decoded text.
        m = _WS_TOKEN_RE.search(text)
        if m:
            return m.group(0)

    # Last resort: a bare 32-hex token in the raw string itself.
    m = _WS_TOKEN_RE.search(s)
    if m:
        return m.group(0)

    raise MoodleApiError(
        "Could not find a token in that URL. Copy the whole address-bar value "
        "(it starts with moodlemobile://token=) and paste it again.")


class MoodleClient:
    """Thin authenticated REST client for one Moodle site. All requests carry the
    web-service token; all responses are checked for Moodle error envelopes."""

    def __init__(self, base_url: str, token: str, *,
                 http_post: Optional[HttpPost] = None, http_get: Optional[HttpGet] = None):
        if not token:
            raise MoodleApiError("A Moodle web-service token is required.")
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self._post = http_post or _default_post
        self._get = http_get or _default_get

    # -- core REST ---------------------------------------------------------
    def call(self, function: str, params: Optional[Dict[str, str]] = None) -> Any:
        """Call a web-service function and return its parsed JSON result, raising
        :class:`MoodleApiError` on transport or Moodle-level errors."""
        url = (f"{self.base_url}webservice/rest/server.php"
               f"?moodlewsrestformat=json&wsfunction={quote(function)}")
        data = {
            "wstoken": self.token,
            "moodlewssettingfilter": "true",
            "moodlewssettingfileurl": "true",
        }
        if params:
            data.update(params)
        status, text = self._post(url, data)
        if status != 200:
            raise MoodleApiError(f"Moodle returned HTTP {status} for {function}.")
        try:
            result = _json.loads(text) if text else None
        except ValueError:
            raise MoodleApiError(
                f"Moodle returned a non-JSON response for {function} "
                "(the web-service API may be unavailable).") from None
        if isinstance(result, dict) and ("exception" in result or "errorcode" in result):
            code = result.get("errorcode", "")
            msg = result.get("message") or result.get("exception") or "request rejected"
            if code in ("invalidtoken", "accessexception"):
                raise MoodleApiError("Your Moodle token is invalid or expired - reconnect to get a new one.")
            raise MoodleApiError(f"Moodle rejected the request ({code}): {msg}")
        return result

    # -- typed helpers -----------------------------------------------------
    def site_info(self) -> Dict[str, Any]:
        info = self.call("core_webservice_get_site_info")
        if not isinstance(info, dict) or "userid" not in info:
            raise MoodleApiError("Could not read site info - the token may lack the required permissions.")
        return info

    def list_courses(self, userid: Optional[int] = None) -> List[Dict[str, Any]]:
        if userid is None:
            userid = self.site_info().get("userid")
        raw = self.call("core_enrol_get_users_courses", {"userid": str(userid)})
        courses = []
        for c in raw or []:
            courses.append({
                "id": c.get("id", 0),
                "fullname": core_unescape(c.get("fullname", "")),
                "shortname": core_unescape(c.get("shortname", "")),
            })
        return courses

    def course_contents(self, course_id: int) -> List[Dict[str, Any]]:
        raw = self.call("core_course_get_contents", {"courseid": str(course_id)})
        if not isinstance(raw, list):
            raise MoodleApiError("Unexpected course-contents response from Moodle.")
        return raw

    def add_token_to_url(self, url: str) -> str:
        """Append the web-service token to a ``pluginfile.php`` URL so it can be
        downloaded without a browser session (mirrors Moodle-DL)."""
        parts = list(urlparse(url))
        query = dict(parse_qsl(parts[4]))
        query["token"] = self.token
        parts[4] = urlencode(query)
        return urlunparse(parts)

    def download_file(self, fileurl: str, dest_path: Path) -> int:
        """Download one Moodle file to ``dest_path``; returns bytes written."""
        status, body, _fname, _ctype = self._get(self.add_token_to_url(fileurl))
        if status != 200:
            raise MoodleApiError(f"Download failed (HTTP {status}) for {fileurl}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(body)
        return len(body)


def core_unescape(text: str) -> str:
    import html
    return html.unescape(text or "").strip()


# ---------------------------------------------------------------------------
# Pure labelling: raw core_course_get_contents payload -> labelled model
# ---------------------------------------------------------------------------

def build_course_model(sections: List[Dict[str, Any]], course_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a raw ``core_course_get_contents`` payload into a fully-labelled
    course model. Pure function (no network), so labelling is unit-tested with
    recorded fixtures.

    Buckets are mutually exclusive and exhaustive per the rules above:

    * ``lectures``   - video/audio modules and recordings (transcription input)
    * ``documents``  - downloadable course files (pdf/pptx/docx/… → Markdown)
    * ``links``      - external URL modules that are *not* recordings
    * ``activities`` - assignments/quizzes/forums/… (outline metadata only)

    Every file keeps its **exact** Moodle ``filename``/``mimetype``/``filesize``
    and a downloadable ``fileurl`` so nothing is misnamed or mislabelled.
    """
    lectures: List[Dict[str, Any]] = []
    documents: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    activities: List[Dict[str, Any]] = []
    panopto_html_blobs: List[str] = []
    outline_sections: List[Dict[str, Any]] = []

    for section in sections or []:
        sec_name = core_unescape(section.get("name", ""))
        sec_summary = section.get("summary", "") or ""
        if sec_summary:
            panopto_html_blobs.append(sec_summary)
        sec_modules_out: List[Dict[str, Any]] = []

        for module in section.get("modules", []) or []:
            modname = (module.get("modname", "") or "").lower()
            mod_name = core_unescape(module.get("name", ""))
            mod_url = module.get("url", "") or ""
            contents = module.get("contents", []) or []
            description = module.get("description", "") or ""
            if description:
                panopto_html_blobs.append(description)

            entry = {"name": mod_name, "modname": modname, "section": sec_name}

            # label modules carry only text - skip from buckets but keep in outline
            if modname == "label":
                sec_modules_out.append({**entry, "kind": "label"})
                continue

            # URL module: a recording if it points at a video host, else a link.
            if modname == "url":
                target = mod_url
                for c in contents:
                    if c.get("fileurl"):
                        target = c["fileurl"]
                        break
                if _looks_like_video_url(target):
                    lectures.append({**entry, "kind": "lecture", "source": "url", "url": target})
                    sec_modules_out.append({**entry, "kind": "lecture"})
                else:
                    links.append({**entry, "url": target})
                    sec_modules_out.append({**entry, "kind": "link"})
                continue

            # Known video/recording module types.
            if modname in _VIDEO_MODNAMES:
                lectures.append({**entry, "kind": "lecture", "source": "module", "url": mod_url})
                sec_modules_out.append({**entry, "kind": "lecture"})
                continue

            # File-bearing modules: classify each file individually.
            if modname in _FILE_MODNAMES or contents:
                produced = False
                for c in contents:
                    ctype = (c.get("type", "") or "").lower()
                    filename = core_unescape(c.get("filename", ""))
                    fileurl = c.get("fileurl", "") or ""
                    mimetype = c.get("mimetype", "") or ""
                    if ctype == "url" or (not fileurl and not filename):
                        # an embedded link inside the module (e.g. page → external)
                        if fileurl and _looks_like_video_url(fileurl):
                            lectures.append({**entry, "kind": "lecture", "source": "embedded", "url": fileurl})
                            produced = True
                        continue
                    cat = file_category(filename, mimetype)
                    filerec = {
                        **entry,
                        "filename": filename,
                        "fileurl": fileurl,
                        "mimetype": mimetype,
                        "filesize": int(c.get("filesize", 0) or 0),
                        "timemodified": int(c.get("timemodified", 0) or 0),
                        "category": cat,
                    }
                    if cat in ("video", "audio"):
                        filerec["kind"] = "lecture"
                        filerec["source"] = "file"
                        filerec["url"] = fileurl
                        lectures.append(filerec)
                    elif cat in ("document", "image", "archive", "other"):
                        documents.append(filerec)
                    produced = True
                if produced:
                    sec_modules_out.append({**entry, "kind": "document"})
                else:
                    sec_modules_out.append({**entry, "kind": "document", "empty": True})
                continue

            # Everything else is an activity (assignment, quiz, forum, …).
            activities.append({
                **entry,
                "kind_label": _ACTIVITY_KIND.get(modname, modname.title()),
                "url": mod_url,
            })
            sec_modules_out.append({**entry, "kind": "activity"})

        outline_sections.append({
            "name": sec_name,
            "week": core.infer_number(sec_name, "week"),
            "modules": sec_modules_out,
        })

    # Panopto RSS feeds embedded in section summaries / descriptions (the Panopto
    # block lives outside the module tree, but its feeds are often echoed in HTML).
    from .. import sources
    feeds: List[str] = []
    seen = set()
    for blob in panopto_html_blobs:
        for f in sources.extract_panopto_feeds(blob):
            if f not in seen:
                seen.add(f)
                feeds.append(f)

    fullname = core_unescape(course_meta.get("fullname", ""))
    shortname = core_unescape(course_meta.get("shortname", ""))
    code = _course_code(shortname) or _course_code(fullname)

    model = {
        "course": {
            "id": course_meta.get("id", 0),
            "fullname": fullname,
            "shortname": shortname,
            "code": code,
        },
        "sections": outline_sections,
        "lectures": lectures,
        "documents": documents,
        "links": links,
        "activities": activities,
        "panopto_feeds": feeds,
        "counts": {
            "sections": len(outline_sections),
            "lectures": len(lectures),
            "documents": len(documents),
            "links": len(links),
            "activities": len(activities),
            "panopto_feeds": len(feeds),
        },
    }
    model["outline_markdown"] = render_outline(model)
    return model


_CODE_RE = re.compile(r"\b([A-Z]{3,6}\d{2,3}(?:-\d{2}[A-Z])?)\b")


def _course_code(text: str) -> str:
    m = _CODE_RE.search((text or "").upper())
    return m.group(1) if m else ""


def render_outline(model: Dict[str, Any]) -> str:
    """Render a clean Markdown outline grouped by section, with each module shown
    under its labelled kind. Suitable as an AI/NotebookLM source."""
    c = model["course"]
    title = c.get("fullname") or c.get("code") or "Course outline"
    lines = [f"# {title}", ""]
    if c.get("code"):
        lines += [f"*Course code: {c['code']}*", ""]

    lines += ["## Outline", ""]
    kind_tag = {"lecture": "lecture", "document": "document", "link": "link",
                "activity": "activity"}
    for sec in model["sections"]:
        header = sec["name"] or "(unnamed section)"
        wk = sec.get("week")
        lines.append(f"### {('Week %d - ' % wk) if wk is not None else ''}{header}")
        mods = [m for m in sec["modules"] if m.get("kind") != "label" and m.get("name")]
        if not mods:
            lines.append("_(no items)_")
        for m in mods:
            tag = kind_tag.get(m.get("kind", ""))
            suffix = f"  _[{tag}]_" if tag else ""
            lines.append(f"- {m['name']}{suffix}")
        lines.append("")

    if model["lectures"]:
        lines += ["## Lectures / recordings", ""]
        lines += [f"- {l['name']}" for l in model["lectures"] if l.get("name")]
        lines.append("")
    if model["documents"]:
        lines += ["## Documents", ""]
        lines += [f"- {d['filename']} ({d['category']})" for d in model["documents"]]
        lines.append("")
    if model["links"]:
        lines += ["## Links", ""]
        lines += [f"- {ln['name']}: {ln['url']}" for ln in model["links"] if ln.get("name")]
        lines.append("")
    if model["activities"]:
        lines += ["## Activities", ""]
        for a in model["activities"]:
            lines.append(f"- **{a['kind_label']}**: {a['name']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# High-level import orchestration
# ---------------------------------------------------------------------------

def import_course(client: MoodleClient, course_id: int,
                  course_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fetch and label one course's full content tree. No files are downloaded
    here - :func:`download_documents` does that step so the caller controls it."""
    if course_meta is None:
        course_meta = {"id": course_id}
        for c in client.list_courses():
            if c["id"] == course_id:
                course_meta = {**c}
                break
    sections = client.course_contents(course_id)
    return build_course_model(sections, course_meta)


def download_documents(client: MoodleClient, documents: List[Dict[str, Any]],
                       dest_dir: Path, *, max_files: int = 300) -> Dict[str, Any]:
    """Download labelled document files into ``dest_dir`` using their **exact**
    Moodle filenames (de-duplicated). Returns a manifest; never raises for a
    single bad file - it is recorded under ``errors``."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    seen: set = set()

    for doc in documents[:max_files]:
        fileurl = doc.get("fileurl")
        if not fileurl:
            continue
        filename = doc.get("filename") or "file"
        # Preserve the real name + extension; disambiguate collisions.
        stem = Path(filename).stem
        ext = Path(filename).suffix
        safe = core.safe_name(stem) + ext
        i = 2
        while safe.lower() in seen:
            safe = f"{core.safe_name(stem)}_{i}{ext}"
            i += 1
        seen.add(safe.lower())
        try:
            n = client.download_file(fileurl, dest_dir / safe)
        except MoodleApiError as e:
            errors.append({"name": filename, "error": str(e)})
            continue
        saved.append({"name": filename, "file": safe, "category": doc.get("category", ""),
                      "section": doc.get("section", ""), "bytes": n})

    return {"downloaded": len(saved), "files": saved, "errors": errors, "dest": str(dest_dir)}
