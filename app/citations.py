"""
citations.py - lecture citation generator (v3).

Turns a lecture's metadata (title / course / date / URL) into ready-to-paste
citations in APA, MLA and BibTeX. Dependency-free string formatting; gracefully
degrades when fields are missing (e.g. no date -> "n.d." in APA).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict

STYLES = ("apa", "mla", "bibtex")


def _year(date: str) -> str:
    m = re.search(r"\b(19|20)\d{2}\b", date or "")
    return m.group(0) if m else ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _bibkey(title: str, year: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "", (title or "lecture").lower())[:24] or "lecture"
    return f"{base}{year}" if year else base


def cite(meta: Dict[str, Any], style: str = "apa") -> str:
    style = (style or "apa").lower()
    title = _clean(meta.get("title")) or "Untitled lecture"
    course = _clean(meta.get("course"))
    date = _clean(meta.get("date"))
    url = _clean(meta.get("video_url") or meta.get("url"))
    year = _year(date)
    author = course or "Course lecture"

    if style == "mla":
        # "Title." Course, Date, URL.
        parts = [f'"{title}."']
        if course:
            parts.append(f"{course},")
        if date:
            parts.append(f"{date},")
        if url:
            parts.append(url)
        return " ".join(parts).rstrip(",") + "."

    if style == "bibtex":
        fields = [f"  title = {{{title}}}"]
        if course:
            fields.append(f"  howpublished = {{{course} lecture recording}}")
        if year:
            fields.append(f"  year = {{{year}}}")
        if url:
            fields.append(f"  url = {{{url}}}")
        body = ",\n".join(fields)
        return f"@misc{{{_bibkey(title, year)},\n{body}\n}}"

    # APA (default): Author. (Year). *Title* [Lecture recording]. URL
    yr = year or "n.d."
    out = f"{author}. ({yr}). {title} [Lecture recording]."
    if url:
        out += f" {url}"
    return out


def cite_all(meta: Dict[str, Any]) -> Dict[str, str]:
    return {style: cite(meta, style) for style in STYLES}
