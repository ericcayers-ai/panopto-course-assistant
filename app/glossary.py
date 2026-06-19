"""
glossary.py - key-term + definition extraction (v3).

Mines transcripts for "X is/are/means/refers to ..." style sentences - the way
lecturers introduce concepts - and turns them into a deduplicated, per-course
glossary. Dependency-free and deterministic; reuses ``core.split_sentences`` so a
definition never starts mid-abbreviation. Falls back to ``keywords`` so a course
with few explicit definitions still gets a useful term list.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from . import core, keywords
from .keywords import STOPWORDS

# "<term> is/are/means/refers to/is defined as/is called <definition>."
_DEFINER = re.compile(
    r"^(?P<term>[A-Za-z][A-Za-z0-9 \-/]{1,48}?)\s+"
    r"(?:is|are|means|refers to|is defined as|is called|describes|denotes)\s+"
    r"(?:a |an |the |simply |basically |essentially |when |where )?"
    r"(?P<def>.{12,240}?[.!?])",
    re.IGNORECASE,
)


def _clean_term(term: str) -> str:
    term = re.sub(r"\s+", " ", term).strip(" \t-")
    # drop a leading filler/pronoun so "This protocol is ..." -> "protocol"
    term = re.sub(r"^(?:this|that|these|those|the|a|an|so|now|here)\s+",
                  "", term, flags=re.IGNORECASE).strip()
    return term


def _plausible_term(term: str) -> bool:
    if not (2 < len(term) <= 50):
        return False
    words = term.split()
    if len(words) > 5:
        return False
    # at least one content word, and not just a stopword run
    return any(w.lower() not in STOPWORDS for w in words)


def extract_terms(text: str, limit: int = 40) -> List[Dict[str, str]]:
    """Definition pairs from one transcript, first occurrence wins, deduped by term."""
    found: Dict[str, Dict[str, str]] = {}
    for sent in core.split_sentences(text or ""):
        sent = sent.strip()
        m = _DEFINER.match(sent)
        if not m:
            continue
        term = _clean_term(m.group("term"))
        if not _plausible_term(term):
            continue
        definition = re.sub(r"\s+", " ", m.group("def")).strip()
        key = term.lower()
        if key not in found:
            found[key] = {"term": term, "definition": definition}
        if len(found) >= limit:
            break
    return list(found.values())


def build_glossary(output_dir: Path, course: str = "",
                   limit: int = 200) -> Dict[str, Any]:
    """Course-wide glossary: merged definition pairs across all lectures, plus a
    keyword list for terms that were never explicitly defined."""
    from . import lectures

    terms: Dict[str, Dict[str, Any]] = {}
    all_text: List[str] = []
    lec_count = 0
    for lec in lectures.iter_lectures(output_dir):
        text = lec.get("text") or ""
        if not text.strip():
            continue
        lec_count += 1
        all_text.append(text)
        for pair in extract_terms(text):
            key = pair["term"].lower()
            entry = terms.get(key)
            if entry is None:
                terms[key] = {**pair, "lectures": [lec["title"]]}
            elif lec["title"] not in entry["lectures"]:
                entry["lectures"].append(lec["title"])
        if len(terms) >= limit:
            break

    defined = sorted(terms.values(), key=lambda d: d["term"].lower())
    defined_keys = set(terms.keys())
    kws = [k for k in keywords.keywords("\n".join(all_text), limit=30)
           if k["term"].lower() not in defined_keys]
    return {
        "course": course,
        "lectures_scanned": lec_count,
        "count": len(defined),
        "terms": defined,
        "undefined_keywords": kws,
    }


def glossary_markdown(glossary: Dict[str, Any]) -> str:
    course = glossary.get("course") or ""
    title = "# Glossary" + (f" - {course}" if course else "")
    lines = [title, "",
             f"_{glossary['count']} terms from {glossary['lectures_scanned']} lectures._",
             ""]
    for t in glossary["terms"]:
        lines.append(f"**{t['term']}** - {t['definition']}")
        lines.append("")
    if glossary.get("undefined_keywords"):
        lines.append("## Other key terms")
        lines.append("")
        lines.append(", ".join(k["term"] for k in glossary["undefined_keywords"]))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_glossary(output_dir: Path, course: str = "",
                   filename: str = "glossary") -> Dict[str, Any]:
    gloss = build_glossary(output_dir, course)
    dest = core.ensure_dir(output_dir / "_exports")
    md_path = dest / f"{core.safe_name(filename)}.md"
    md_path.write_text(glossary_markdown(gloss), encoding="utf-8")
    return {**gloss, "markdown": md_path.relative_to(output_dir).as_posix()}
