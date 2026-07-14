"""
panopto_discover.py - find Panopto Podcast.ashx RSS feeds for a paper/course.

Discovery order (plan §4):
  1. Moodle HTML / API blob via ``sources.extract_panopto_feeds``
  2. Authenticated HTTP fetch of a known Panopto course page (cookies)
  3. Playwright scrape of the Panopto site (optional dep) looking for
     ``a.rssLink[href*="Podcast.ashx"]``
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from . import sources
from .errors import AppError

Fetcher = Callable[[str, str], str]  # (url, cookie_header) -> html

_RSS_LINK_RE = re.compile(
    r'''<a[^>]+class=["'][^"']*rssLink[^"']*["'][^>]+href=["']([^"']*Podcast\.ashx[^"']*)["']''',
    re.I,
)
_RSS_LINK_RE_ALT = re.compile(
    r'''href=["']([^"']*Podcast\.ashx[^"']*)["'][^>]*class=["'][^"']*rssLink''',
    re.I,
)
_PODCAST_ASHX_RE = re.compile(r'''href=["']([^"']*Podcast\.ashx[^"']*)["']''', re.I)

DEFAULT_PANOPTO_HOST = "https://waikato.au.panopto.com"


class PanoptoDiscoverError(AppError):
    category = "network"
    status_code = 502


def feeds_from_html(html: str) -> List[str]:
    """Extract Podcast.ashx / Panopto RSS URLs from page HTML."""
    found: List[str] = []
    seen = set()
    for pattern in (_RSS_LINK_RE, _RSS_LINK_RE_ALT, _PODCAST_ASHX_RE):
        for m in pattern.findall(html or ""):
            url = m.strip()
            if url and url not in seen:
                seen.add(url)
                found.append(url)
    # Also reuse the Moodle-oriented extractor for Podcast/Rss links.
    for url in sources.extract_panopto_feeds(html or ""):
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _default_fetcher(url: str, cookie_header: str) -> str:
    import requests
    headers = {"User-Agent": "Mozilla/5.0 CourseAssistant", "Accept": "text/html"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        r = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception as e:
        raise PanoptoDiscoverError(f"fetch failed for {url}: {e}") from e


def discover_from_moodle_html(html: str) -> Dict[str, Any]:
    feeds = feeds_from_html(html)
    return {"feeds": feeds, "source": "moodle_html", "count": len(feeds)}


def discover_from_url(
    url: str,
    *,
    cookies: str = "",
    fetcher: Optional[Fetcher] = None,
) -> Dict[str, Any]:
    """Fetch a Moodle or Panopto page and extract RSS links."""
    fetch = fetcher or _default_fetcher
    html = fetch(url, cookies or "")
    feeds = feeds_from_html(html)
    # Absolutize relative hrefs
    abs_feeds = []
    for f in feeds:
        abs_feeds.append(urljoin(url, f))
    return {"feeds": abs_feeds, "source": "http", "url": url, "count": len(abs_feeds)}


def discover_with_playwright(
    panopto_url: str,
    *,
    cookies: str = "",
    headless: bool = True,
) -> Dict[str, Any]:
    """Open a Panopto folder/course page and scrape ``a.rssLink`` Podcast.ashx links.

    Requires playwright (optional). Falls back to a clear error if missing.
    """
    try:
        from . import browser_scrape
    except ImportError as e:
        raise PanoptoDiscoverError("browser scrape module unavailable") from e
    return browser_scrape.scrape_panopto_rss(panopto_url, cookies=cookies, headless=headless)


def discover(
    *,
    moodle_html: str = "",
    moodle_url: str = "",
    panopto_url: str = "",
    cookies: str = "",
    use_playwright: bool = False,
    fetcher: Optional[Fetcher] = None,
) -> Dict[str, Any]:
    """Run discovery in plan order; stop early when feeds are found unless forced."""
    steps: List[Dict[str, Any]] = []
    feeds: List[str] = []
    seen = set()

    def _add(result: Dict[str, Any]) -> None:
        nonlocal feeds
        steps.append(result)
        for f in result.get("feeds") or []:
            if f not in seen:
                seen.add(f)
                feeds.append(f)

    if moodle_html:
        _add(discover_from_moodle_html(moodle_html))

    if moodle_url and not feeds:
        try:
            _add(discover_from_url(moodle_url, cookies=cookies, fetcher=fetcher))
        except Exception as e:
            steps.append({"source": "http", "url": moodle_url, "error": str(e), "feeds": []})

    if panopto_url and not feeds:
        try:
            _add(discover_from_url(panopto_url, cookies=cookies, fetcher=fetcher))
        except Exception as e:
            steps.append({"source": "http", "url": panopto_url, "error": str(e), "feeds": []})

    if use_playwright and panopto_url and not feeds:
        try:
            from . import browser_scrape
            if not browser_scrape.playwright_available():
                steps.append({
                    "source": "playwright",
                    "url": panopto_url,
                    "feeds": [],
                    "skipped": True,
                    "error": (
                        "Playwright is not installed. "
                        "Run: pip install -r requirements-browser.txt && playwright install chromium"
                    ),
                })
            else:
                _add(discover_with_playwright(panopto_url, cookies=cookies))
        except Exception as e:
            steps.append({"source": "playwright", "url": panopto_url, "error": str(e), "feeds": []})

    return {
        "feeds": feeds,
        "count": len(feeds),
        "steps": steps,
        "host_hint": _host_hint(feeds or [panopto_url or moodle_url]),
    }


def _host_hint(urls: List[str]) -> str:
    for u in urls:
        if not u:
            continue
        try:
            host = urlparse(u).netloc
            if host:
                return host
        except Exception:
            continue
    return urlparse(DEFAULT_PANOPTO_HOST).netloc
