"""
imports/moodle_resources.py - download a Moodle course's resource files (§7).

The course page lists file-backed activities (``/mod/resource/view.php?id=…`` and
``/mod/folder/…``). Each, once you're logged in, redirects to the actual file
(``pluginfile.php/…/lecture.pdf``). This module walks those activities and saves
the files locally so the document converter can turn them into Markdown.

Network is reached only here, only on an explicit import, and only with the
cookies the user supplies. The fetcher is injectable so the walk/dedup/naming
logic is fully testable offline (no real Moodle needed).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin

from .. import core
from ..errors import AppError

# fetch(url, cookie_header) -> (content_bytes, filename, content_type)
ResourceFetcher = Callable[[str, str], Tuple[bytes, str, str]]

# Files we can convert to Markdown (matches core.DOC_EXTS) + common media.
_KEEP_EXTS = {e.lower() for e in core.DOC_EXTS} | {
    ".png", ".jpg", ".jpeg", ".gif", ".zip"}

_PLUGINFILE_RE = re.compile(r'href="([^"]*pluginfile\.php/[^"]+)"', re.I)
_CD_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.I)


class ResourceError(AppError):
    """A course resource file could not be downloaded."""

    category = "network"
    status_code = 502


def _default_fetcher(url: str, cookie_header: str) -> Tuple[bytes, str, str]:
    import requests
    headers = {"User-Agent": "Mozilla/5.0 CourseAssistant"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        r = requests.get(url, headers=headers, timeout=120, allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        raise ResourceError(str(e)) from e
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    fname = ""
    cd = r.headers.get("Content-Disposition", "")
    m = _CD_FILENAME_RE.search(cd)
    if m:
        fname = unquote(m.group(1).strip())
    if not fname:
        fname = unquote(Path(r.url.split("?")[0]).name)
    return r.content, fname, ctype


def _ext_for(filename: str, ctype: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext:
        return ext
    return {"application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "text/html": ".html"}.get(ctype, "")


def download_resources(activities: List[Dict[str, Any]], dest_dir: Path, *,
                      cookies: str = "", fetcher: Optional[ResourceFetcher] = None,
                      max_files: int = 200) -> Dict[str, Any]:
    """Download every file-backed activity into ``dest_dir``.

    Returns a manifest: ``{downloaded, files[], skipped[], errors[]}``. A resource
    "view" page that returns HTML with a ``pluginfile`` link is followed one hop to
    the real file. Never raises for a single bad file - it's recorded and skipped.
    """
    fetch = fetcher or _default_fetcher
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    files: List[Dict[str, Any]] = []
    skipped: List[str] = []
    errors: List[Dict[str, str]] = []
    seen_names: set = set()

    targets = [a for a in activities
               if a.get("downloadable") and a.get("url")][:max_files]
    for a in targets:
        try:
            content, fname, ctype = fetch(a["url"], cookies)
            # A resource intro page (HTML) → follow its pluginfile link one hop.
            if ctype == "text/html" and b"pluginfile.php" in content:
                m = _PLUGINFILE_RE.search(content.decode("utf-8", "replace"))
                if m:
                    href = urljoin(a["url"], m.group(1).replace("&amp;", "&"))
                    content, fname, ctype = fetch(href, cookies)
        except ResourceError as e:
            errors.append({"name": a["name"], "error": str(e)})
            continue

        ext = _ext_for(fname, ctype)
        if ext and ext not in _KEEP_EXTS:
            skipped.append(f"{a['name']} ({ext or ctype})")
            continue
        # Name the saved file from the activity (stable, readable), keeping its ext.
        base = core.safe_name(a["name"]) or core.safe_name(Path(fname).stem) or "file"
        name = f"{base}{ext}"
        i = 2
        while name.lower() in seen_names:
            name = f"{base}_{i}{ext}"; i += 1
        seen_names.add(name.lower())
        (dest_dir / name).write_bytes(content)
        files.append({"name": a["name"], "file": name, "kind": a["kind"],
                      "bytes": len(content)})

    return {"downloaded": len(files), "files": files,
            "skipped": skipped, "errors": errors, "dest": str(dest_dir)}
