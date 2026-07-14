"""
browser_scrape.py - Playwright fallback for Moodle forums / announcements / Panopto.

Optional dependency (``requirements-browser.txt``). Callers should treat import
and runtime errors as soft failures and fall back to API / cookie HTML paths.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from .errors import AppError

_RSS_HREF_RE = re.compile(r'Podcast\.ashx[^"\']*', re.I)


class BrowserScrapeError(AppError):
    category = "network"
    status_code = 502


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as e:
        raise BrowserScrapeError(
            "Playwright is not installed. Run: pip install -r requirements-browser.txt "
            "&& playwright install chromium"
        ) from e


def _cookie_header_to_playwright(cookies: str, url: str) -> List[Dict[str, Any]]:
    """Convert a Cookie header string into Playwright cookie dicts."""
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    out: List[Dict[str, Any]] = []
    for part in (cookies or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        out.append({
            "name": name, "value": value,
            "domain": host, "path": "/",
        })
    return out


def scrape_panopto_rss(
    panopto_url: str,
    *,
    cookies: str = "",
    headless: bool = True,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """Open a Panopto page and collect ``a.rssLink[href*=Podcast.ashx]`` URLs."""
    sync_playwright = _require_playwright()
    feeds: List[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        if cookies:
            try:
                context.add_cookies(_cookie_header_to_playwright(cookies, panopto_url))
            except Exception:
                pass
        page = context.new_page()
        page.goto(panopto_url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Primary selector from plan
        for el in page.query_selector_all('a.rssLink[href*="Podcast.ashx"]'):
            href = el.get_attribute("href") or ""
            if href:
                feeds.append(urljoin(panopto_url, href))
        # Fallback: any Podcast.ashx link
        if not feeds:
            html = page.content()
            for m in re.findall(r'href=["\']([^"\']*Podcast\.ashx[^"\']*)["\']', html, re.I):
                feeds.append(urljoin(panopto_url, m))
        browser.close()
    # de-dupe preserving order
    seen = set()
    uniq = []
    for f in feeds:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return {"feeds": uniq, "source": "playwright", "url": panopto_url, "count": len(uniq)}


def scrape_moodle_announcements(
    course_url: str,
    *,
    cookies: str = "",
    headless: bool = True,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """Scrape announcement / news forum posts from a Moodle course page."""
    sync_playwright = _require_playwright()
    announcements: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        if cookies:
            try:
                context.add_cookies(_cookie_header_to_playwright(cookies, course_url))
            except Exception:
                pass
        page = context.new_page()
        page.goto(course_url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Prefer forum discussion links under Announcements / News
        links = page.query_selector_all('a[href*="mod/forum/"]')
        hrefs = []
        for el in links:
            href = el.get_attribute("href") or ""
            text = (el.inner_text() or "").strip()
            if href and ("discuss" in href or "view" in href):
                hrefs.append((urljoin(course_url, href), text))
        # Visit up to 15 discussion pages
        for href, text in hrefs[:15]:
            try:
                page.goto(href, wait_until="domcontentloaded", timeout=timeout_ms)
                body_el = page.query_selector(".post-content-container, .forumpost .content, .posting")
                body = (body_el.inner_text() if body_el else "") or ""
                title_el = page.query_selector("h3, h2, .discussionname")
                title = (title_el.inner_text() if title_el else text) or "Announcement"
                announcements.append({
                    "title": title.strip(),
                    "body": body.strip(),
                    "author": "",
                    "posted_at": "",
                    "source_url": href,
                })
            except Exception:
                continue
        browser.close()
    return {"announcements": announcements, "count": len(announcements), "source": "playwright"}


def scrape_moodle_forums(
    course_url: str,
    *,
    cookies: str = "",
    headless: bool = True,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """Scrape forum list + sample discussions from a Moodle course."""
    sync_playwright = _require_playwright()
    forums: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        if cookies:
            try:
                context.add_cookies(_cookie_header_to_playwright(cookies, course_url))
            except Exception:
                pass
        page = context.new_page()
        page.goto(course_url, wait_until="domcontentloaded", timeout=timeout_ms)
        forum_links = page.query_selector_all('a[href*="mod/forum/view.php"]')
        seen = set()
        for el in forum_links:
            href = el.get_attribute("href") or ""
            name = (el.inner_text() or "").strip() or "Forum"
            full = urljoin(course_url, href)
            if full in seen or not href:
                continue
            seen.add(full)
            forums.append({"title": name, "url": full, "posts": []})
        for forum in forums[:8]:
            try:
                page.goto(forum["url"], wait_until="domcontentloaded", timeout=timeout_ms)
                for disc in page.query_selector_all('a[href*="discuss.php"]')[:10]:
                    subject = (disc.inner_text() or "").strip()
                    dhref = urljoin(forum["url"], disc.get_attribute("href") or "")
                    forum["posts"].append({
                        "subject": subject, "url": dhref, "message": "",
                    })
            except Exception:
                continue
        browser.close()
    return {"forums": forums, "count": len(forums), "source": "playwright"}


MOODLE_CAPABILITIES = {
    "api": {
        "course_list": True,
        "resources_files": True,
        "paper_code_detection": True,
        "panopto_rss_moodle_page": "if_linked",
        "panopto_rss_panopto_site": "cookie_html_then_playwright",
        "announcements": "limited",
        "forums": "limited",
        "calendar": "ics_or_api",
    },
    "browser": {
        "course_list": True,
        "resources_files": True,
        "paper_code_detection": True,
        "panopto_rss_moodle_page": True,
        "panopto_rss_panopto_site": True,
        "announcements": True,
        "forums": True,
        "calendar": "scrape_and_ics",
    },
}


def capability_matrix(mode: str = "api") -> Dict[str, Any]:
    mode = (mode or "api").lower()
    if mode not in ("api", "browser"):
        raise ValueError("mode must be api or browser")
    return {
        "mode": mode,
        "playwright_available": playwright_available(),
        "capabilities": MOODLE_CAPABILITIES[mode],
        "matrix": [
            {
                "capability": "Course list / resources / files",
                "api": True,
                "browser": True,
            },
            {
                "capability": "Paper code detection",
                "api": True,
                "browser": True,
            },
            {
                "capability": "Panopto RSS on Moodle page",
                "api": "Yes if linked",
                "browser": True,
            },
            {
                "capability": "Panopto RSS only on Panopto site",
                "api": "Cookie/HTML fetch → Playwright if needed",
                "browser": "Playwright",
            },
            {
                "capability": "Announcements / news",
                "api": "Often limited",
                "browser": True,
            },
            {
                "capability": "Forums / discussions",
                "api": "Often limited/unavailable",
                "browser": True,
            },
            {
                "capability": "Calendar",
                "api": "ICS URL / API",
                "browser": "Scrape + ICS",
            },
        ],
    }
