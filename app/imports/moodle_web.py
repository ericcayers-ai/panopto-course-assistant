"""
imports/moodle_web.py — import a Moodle course straight from its live URL (§7).

The user pastes the course page URL (e.g.
``https://elearn.waikato.ac.nz/course/view.php?id=77547``) and, because Moodle
requires a login, supplies the session cookies from their already-logged-in
browser. We fetch the main page with those cookies, discover the linked
``section.php`` pages (lecture materials, assignments, …), fetch each, and merge
everything through the existing :mod:`app.sources` parser — so "the link alone"
reconstructs the course outline + activities + Panopto feeds.

The HTTP fetcher is injectable (``fetcher=``) so the crawl/merge logic is tested
fully offline. Nothing here runs unless the user initiates an import.
"""
from __future__ import annotations

import re
from http.cookies import SimpleCookie
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

from .. import sources

# fetcher(url, cookie_header) -> html
Fetcher = Callable[[str, str], str]

_SECTION_HREF_RE = re.compile(r'href="([^"]*course/section\.php\?id=\d+[^"]*)"', re.I)
_VIEW_SECTION_RE = re.compile(r'href="([^"]*course/view\.php\?id=\d+&[^"]*section=\d+[^"]*)"', re.I)


class MoodleWebError(Exception):
    pass


def parse_cookies(raw: str) -> str:
    """Accept cookies as a browser ``Cookie:`` header, ``k=v; k2=v2`` pairs, or
    Netscape ``cookies.txt`` lines, and normalise to a single Cookie header."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    # Bare session token — the most common copy/paste mistake is pasting just the
    # MoodleSession *value* (no name). Wrap it so it becomes a usable cookie.
    if "=" not in raw and ";" not in raw and "\t" not in raw and len(raw.split()) == 1:
        return f"MoodleSession={raw}"
    # Netscape cookies.txt: tab-separated, domain in col 0, name/value in last two.
    if "\t" in raw or raw.lstrip().startswith("# Netscape"):
        pairs = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) >= 7:
                pairs.append(f"{cols[5]}={cols[6]}")
        if pairs:
            return "; ".join(pairs)
    # Already a header / k=v list — collapse whitespace, keep as-is.
    return re.sub(r"\s*;\s*", "; ", raw).strip().rstrip(";")


def _default_fetcher(url: str, cookie_header: str) -> str:
    import requests
    headers = {"User-Agent": "Mozilla/5.0 CourseAssistant",
               "Accept": "text/html"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        return r.text
    except Exception as e:
        raise MoodleWebError(f"fetch failed for {url}: {e}") from e


def _looks_logged_out(raw: str) -> bool:
    low = raw.lower()
    # A logged-IN Moodle page always carries a logout link — use that as a strong
    # "you're fine" signal before we sniff for login markers.
    if "login/logout.php" in low:
        return False
    # Moodle / SSO login page markers; a logged-out fetch returns the login form.
    return ("loginform" in low or "id=\"login\"" in low
            or "sign in to your account" in low
            or "login/index.php" in low
            or ("log in" in low and "course" not in low and len(raw) < 8000))


def _discover_section_urls(raw: str, base_url: str) -> List[str]:
    urls: List[str] = []
    seen = set()
    for m in _SECTION_HREF_RE.findall(raw) + _VIEW_SECTION_RE.findall(raw):
        full = urljoin(base_url, m.replace("&amp;", "&"))
        if full not in seen:
            seen.add(full); urls.append(full)
    return urls


def import_course(url: str, cookies: str = "", *, fetcher: Optional[Fetcher] = None,
                 follow_sections: bool = True, max_sections: int = 25) -> Dict[str, Any]:
    """Crawl a live Moodle course from ``url`` and return the parsed outline.

    Returns the same shape as :func:`app.sources.parse_moodle_html` plus a
    ``pages_fetched`` count and the discovered ``panopto_feeds`` so the caller can
    queue lecture transcription.
    """
    if not re.search(r"course/view\.php\?id=\d+", url or ""):
        raise MoodleWebError(
            "Expected a Moodle course URL like .../course/view.php?id=12345")
    fetch = fetcher or _default_fetcher
    cookie_header = parse_cookies(cookies)
    main = fetch(url, cookie_header)
    if _looks_logged_out(main):
        raise MoodleWebError(
            "That page looks logged-out — paste your browser session cookies for "
            "the Moodle site so the course can be read.")

    extra_sections: List[Dict[str, Any]] = []
    extra_activities: List[Dict[str, Any]] = []
    feeds: List[str] = list(sources.extract_panopto_feeds(main))
    pages = 1
    if follow_sections:
        for section_url in _discover_section_urls(main, url)[:max_sections]:
            try:
                page = fetch(section_url, cookie_header)
            except MoodleWebError:
                continue
            pages += 1
            sub = sources.parse_moodle_html(page)
            extra_sections += sub["sections"]
            extra_activities += sub["activities"]
            feeds += sub.get("panopto_feeds", [])

    parsed = sources.parse_moodle_html(main, extra_sections=extra_sections,
                                      extra_activities=extra_activities)
    # de-dup feeds discovered across pages
    seen, merged = set(), []
    for f in feeds:
        if f not in seen:
            seen.add(f); merged.append(f)
    parsed["panopto_feeds"] = merged
    parsed["source_url"] = url
    parsed["pages_fetched"] = pages
    return parsed
