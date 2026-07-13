"""Fetch Moodle announcements and forum posts as a separate content entity."""
from __future__ import annotations

import html as html_lib
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

from .core import now_iso
from .errors import AppError
from .imports.moodle_web import MoodleWebError, _default_fetcher, parse_cookies, _looks_logged_out

Fetcher = Callable[[str, str], str]


class MoodleContentError(AppError):
    category = "invalid_source"
    status_code = 400


_ANNOUNCE_RE = re.compile(
    r'<div[^>]*class="[^"]*(?:forumpost|announcement|news)[^"]*"[^>]*>(.*?)</div>',
    re.I | re.S,
)
_FORUM_POST_RE = re.compile(
    r'<article[^>]*id="p\d+"[^>]*>(.*?)</article>',
    re.I | re.S,
)
_SUBJECT_RE = re.compile(r'class="[^"]*subject[^"]*"[^>]*>(.*?)</', re.I | re.S)
_AUTHOR_RE = re.compile(r'class="[^"]*author[^"]*"[^>]*>(.*?)</', re.I | re.S)
_TIME_RE = re.compile(r'class="[^"]*(?:time|date)[^"]*"[^>]*>(.*?)</', re.I | re.S)
_LINK_RE = re.compile(
    r'href="([^"]*(?:mod/forum/view\.php|mod/forum/discuss\.php)[^"]*)"',
    re.I,
)


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _extract_posts(raw: str) -> List[Dict[str, str]]:
    posts: List[Dict[str, str]] = []
    for block in _FORUM_POST_RE.findall(raw):
        title = _strip_html(_SUBJECT_RE.search(block).group(1)) if _SUBJECT_RE.search(block) else ""
        author = _strip_html(_AUTHOR_RE.search(block).group(1)) if _AUTHOR_RE.search(block) else ""
        posted = _strip_html(_TIME_RE.search(block).group(1)) if _TIME_RE.search(block) else ""
        body = _strip_html(block)[:4000]
        if title or body:
            posts.append({"title": title or "Forum post", "author": author,
                          "posted_at": posted, "body": body})
    if posts:
        return posts

    # Fallback: activity names that look like announcements on the course page.
    for m in re.finditer(
        r'activityname[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>',
        raw, re.I,
    ):
        name = html_lib.unescape(m.group(2)).strip()
        if any(k in name.lower() for k in ("announcement", "news", "forum")):
            posts.append({
                "title": name,
                "author": "",
                "posted_at": "",
                "body": f"Linked activity: {name}",
                "source_url": html_lib.unescape(m.group(1)),
            })
    return posts


def discover_announcement_urls(raw: str, base_url: str) -> List[str]:
    urls: List[str] = []
    seen = set()
    for href in _LINK_RE.findall(raw):
        full = urljoin(base_url, href.replace("&amp;", "&"))
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls[:20]


def fetch_announcements(url: str, cookies: str = "", *,
                        fetcher: Optional[Fetcher] = None,
                        follow_links: bool = True) -> Dict[str, Any]:
    """Download Moodle announcements/forum posts without mixing into schedules."""
    if not re.search(r"course/view\.php\?id=\d+", url or ""):
        raise MoodleContentError(
            "Expected a Moodle course URL like .../course/view.php?id=12345")
    fetch = fetcher or _default_fetcher
    cookie_header = parse_cookies(cookies)
    main = fetch(url, cookie_header)
    if _looks_logged_out(main):
        raise MoodleWebError(
            "That page looks logged-out - paste your browser session cookies.")

    announcements: List[Dict[str, Any]] = []
    pages = 1

    def _collect(page_html: str, page_url: str) -> None:
        for post in _extract_posts(page_html):
            announcements.append({
                **post,
                "source_url": page_url,
                "fetched_at": now_iso(),
                "content_type": "announcement",
            })

    _collect(main, url)
    if follow_links:
        for link in discover_announcement_urls(main, url):
            try:
                page = fetch(link, cookie_header)
            except MoodleWebError:
                continue
            pages += 1
            _collect(page, link)

    # De-duplicate by title+body prefix
    seen = set()
    unique: List[Dict[str, Any]] = []
    for a in announcements:
        key = (a.get("title", ""), a.get("body", "")[:120])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    moodle_id = ""
    m = re.search(r"id=(\d+)", url)
    if m:
        moodle_id = m.group(1)

    return {
        "moodle_course_id": moodle_id,
        "source_url": url,
        "pages_fetched": pages,
        "announcement_count": len(unique),
        "announcements": unique,
    }
