"""
studyguide.py - one-document study guide (v3).

Assembles a single Markdown revision guide from the whole library: a table of
contents, per-lecture key points (extractive summary) and key phrases, and a
course glossary at the end. Built on the dependency-free ``core.summarize_text``,
``keywords`` and ``glossary`` engines, so it works with no AI model configured.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import core, glossary, keywords, lectures


def _anchor(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "lecture"


def build_markdown(output_dir: Path, course: str = "", *,
                   sentences: int = 5, phrases: int = 8) -> Dict[str, Any]:
    lecs = [lec for lec in lectures.iter_lectures(output_dir)
            if (lec.get("text") or "").strip()]
    title = "# Study Guide" + (f" - {course}" if course else "")
    lines = [title, ""]

    if lecs:
        lines.append("## Contents")
        lines.append("")
        for lec in lecs:
            wk = f"Week {lec['week']}: " if lec["week"] is not None else ""
            lines.append(f"- [{wk}{lec['title']}](#{_anchor(lec['title'])})")
        lines.append("")

    for lec in lecs:
        wk = f"Week {lec['week']} - " if lec["week"] is not None else ""
        lines.append(f"## {wk}{lec['title']}")
        lines.append("")
        points = core.summarize_text(lec["text"], max_sentences=sentences)
        if points:
            lines.append("**Key points**")
            lines.append("")
            for p in points:
                lines.append(f"- {p}")
            lines.append("")
        phr = keywords.key_phrases(lec["text"], limit=phrases)
        if phr:
            lines.append("**Key terms:** " + ", ".join(p["phrase"] for p in phr))
            lines.append("")

    gloss = glossary.build_glossary(output_dir, course)
    if gloss["terms"]:
        lines.append("## Glossary")
        lines.append("")
        for t in gloss["terms"]:
            lines.append(f"- **{t['term']}** - {t['definition']}")
        lines.append("")

    markdown = "\n".join(lines).strip() + "\n"
    return {
        "course": course,
        "lectures": len(lecs),
        "glossary_terms": gloss["count"],
        "markdown": markdown,
    }


def write_guide(output_dir: Path, course: str = "",
                filename: str = "study_guide") -> Dict[str, Any]:
    built = build_markdown(output_dir, course)
    dest = core.ensure_dir(output_dir / "_exports")
    md_path = dest / f"{core.safe_name(filename)}.md"
    md_path.write_text(built["markdown"], encoding="utf-8")
    return {
        "course": course,
        "lectures": built["lectures"],
        "glossary_terms": built["glossary_terms"],
        "path": md_path.relative_to(output_dir).as_posix(),
    }
