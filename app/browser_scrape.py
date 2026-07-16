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
_CALENDAR_HREF_RE = re.compile(
    r'href=["\']([^"\']*calendar/export(?:_execute)?\.php[^"\']*authtoken[^"\']*)["\']',
    re.I,
)
_CALENDAR_HREF_FALLBACK_RE = re.compile(
    r'(https?://[^"\'\s<>]+calendar/export(?:_execute)?\.php[^"\'\s<>]*authtoken=[^"\'\s<>&]+)',
    re.I,
)


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


def _goto_settled(page, url: str, timeout_ms: int) -> None:
    """Navigate and wait out SSO / meta-refresh redirects that destroy contexts."""
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(15000, timeout_ms))
    except Exception:
        pass
    try:
        page.wait_for_timeout(400)
    except Exception:
        pass


def _collect_hrefs(page, selector: str) -> List[tuple]:
    """Return (href, text) pairs via locator API (survives soft navigations)."""
    out: List[tuple] = []
    try:
        loc = page.locator(selector)
        n = loc.count()
    except Exception:
        return out
    for i in range(n):
        try:
            el = loc.nth(i)
            href = el.get_attribute("href") or ""
            text = (el.inner_text() or "").strip()
            if href:
                out.append((href, text))
        except Exception:
            continue
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
        _goto_settled(page, panopto_url, timeout_ms)
        # Primary selector from plan
        for href, _text in _collect_hrefs(page, 'a.rssLink[href*="Podcast.ashx"]'):
            feeds.append(urljoin(panopto_url, href))
        # Fallback: any Podcast.ashx link
        if not feeds:
            try:
                html = page.content()
            except Exception:
                html = ""
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
        _goto_settled(page, course_url, timeout_ms)
        # Prefer forum discussion links under Announcements / News
        hrefs = []
        for href, text in _collect_hrefs(page, 'a[href*="mod/forum/"]'):
            if "discuss" in href or "view" in href:
                hrefs.append((urljoin(course_url, href), text))
        # Visit up to 15 discussion pages
        for href, text in hrefs[:15]:
            try:
                _goto_settled(page, href, timeout_ms)
                body = ""
                title = text or "Announcement"
                try:
                    body_loc = page.locator(
                        ".post-content-container, .forumpost .content, .posting",
                    )
                    if body_loc.count() > 0:
                        body = (body_loc.first.inner_text() or "").strip()
                except Exception:
                    body = ""
                try:
                    title_loc = page.locator("h3, h2, .discussionname")
                    if title_loc.count() > 0:
                        title = (title_loc.first.inner_text() or title).strip() or title
                except Exception:
                    pass
                announcements.append({
                    "title": title.strip(),
                    "body": body,
                    "author": "",
                    "posted_at": "",
                    "source_url": href,
                })
            except Exception:
                continue
        browser.close()
    return {"announcements": announcements, "count": len(announcements), "source": "playwright"}


def _extract_calendar_urls(html: str, base_url: str) -> List[str]:
    """Pull Moodle calendar ICS export links from page HTML."""
    urls: List[str] = []
    seen: set = set()
    for m in _CALENDAR_HREF_RE.findall(html or ""):
        full = urljoin(base_url, m.replace("&amp;", "&"))
        if full not in seen:
            seen.add(full)
            urls.append(full)
    if not urls:
        for m in _CALENDAR_HREF_FALLBACK_RE.findall(html or ""):
            full = m.replace("&amp;", "&")
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


def discover_calendar_url(
    moodle_base_url: str,
    *,
    cookies: str = "",
    headless: bool = True,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """Discover a Moodle calendar export URL containing an authtoken.

    Tries cookie/HTML fetch first, then Playwright on calendar and home pages.
    """
    base = moodle_base_url.rstrip("/") + "/"
    candidates = [
        urljoin(base, "calendar/view.php"),
        urljoin(base, "my/"),
        base,
    ]
    found: List[str] = []

    if cookies:
        import requests
        headers = {"User-Agent": "Mozilla/5.0 CourseAssistant", "Cookie": cookies}
        for page_url in candidates:
            try:
                r = requests.get(page_url, headers=headers, timeout=30)
                if r.status_code == 200:
                    found.extend(_extract_calendar_urls(r.text, base))
            except Exception:
                continue
        if found:
            return {"url": found[0], "source": "html", "candidates": len(found)}

    if not playwright_available():
        return {"url": "", "source": "none", "candidates": 0}

    sync_playwright = _require_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        if cookies:
            try:
                context.add_cookies(_cookie_header_to_playwright(cookies, base))
            except Exception:
                pass
        page = context.new_page()
        for page_url in candidates:
            try:
                _goto_settled(page, page_url, timeout_ms)
                html = page.content()
                found.extend(_extract_calendar_urls(html, base))
                if found:
                    break
            except Exception:
                continue
        browser.close()

    uniq: List[str] = []
    seen: set = set()
    for u in found:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return {"url": uniq[0] if uniq else "", "source": "playwright" if uniq else "none",
            "candidates": len(uniq)}


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
        _goto_settled(page, course_url, timeout_ms)
        seen = set()
        for href, name in _collect_hrefs(page, 'a[href*="mod/forum/view.php"]'):
            full = urljoin(course_url, href)
            label = name or "Forum"
            if full in seen or not href:
                continue
            seen.add(full)
            forums.append({"title": label, "url": full, "posts": []})
        for forum in forums[:8]:
            try:
                _goto_settled(page, forum["url"], timeout_ms)
                for dhref, subject in _collect_hrefs(page, 'a[href*="discuss.php"]')[:10]:
                    forum["posts"].append({
                        "subject": subject,
                        "url": urljoin(forum["url"], dhref),
                        "message": "",
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
