"""
flashcards.py — generate Anki-style flashcards from course text, and tag/
categorise existing flashcards. Dependency-free (stdlib only).

Two jobs:
  1. GENERATE: pull question/answer pairs out of transcripts / summaries /
     converted documents using conservative heuristics (definitions + acronym
     expansions), and attach study tags (course :: week :: topic).
  2. CATEGORISE: take a deck the user already has (CSV/TSV: front, back[, tags])
     and add tags by matching each card's text against the course's
     week/topic vocabulary.

Output is written as:
  * an Anki-importable ``.txt`` (tab-separated, with Anki header directives so
    the third column maps straight onto Tags), and
  * a plain ``.csv`` (front, back, tags) that also imports anywhere else.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core

FLASHCARDS_DIRNAME = "_flashcards"


# ---------------------------------------------------------------------------
# Card model helpers
# ---------------------------------------------------------------------------

def _tag(text: str) -> str:
    """Anki tags cannot contain spaces; slug them with underscores."""
    t = re.sub(r"[^\w:]+", "_", (text or "").strip())
    return re.sub(r"_+", "_", t).strip("_")


def base_tags(course: str = "", week: Optional[int] = None, topic: str = "") -> List[str]:
    tags: List[str] = []
    course_slug = _tag(course) if course else ""
    if course_slug and week is not None:
        tags.append(f"{course_slug}::Week{week:02d}")  # hierarchical Anki tag
    elif course_slug:
        tags.append(course_slug)
    elif week is not None:
        tags.append(f"Week{week:02d}")
    if topic:
        tags.append(_tag(topic))
    return [t for t in dict.fromkeys(tags) if t]


# ---------------------------------------------------------------------------
# Generation heuristics
# ---------------------------------------------------------------------------

# "Transmission Control Protocol (TCP)" / "TCP (Transmission Control Protocol)"
_ACRONYM_AFTER = re.compile(r"\b([A-Z][A-Za-z][\w-]+(?:\s+[A-Za-z][\w-]+){1,5})\s*\(([A-Z]{2,6})\)")
_ACRONYM_BEFORE = re.compile(r"\b([A-Z]{2,6})\s*\(([^)]{6,70})\)")
_ACRONYM_STANDS = re.compile(r"\b([A-Z]{2,6})\s+stands for\s+([^.;:]{5,70})", re.I)

# "X is/are/refers to/provides/organises … " descriptive sentences. The verb set
# is broadened beyond bare copulas (real lecture prose rarely says "X is …"), and
# the subject is case-insensitive so lowercase mid-sentence terms ("a deadlock is
# …") are captured, not just title-cased ones.
_DEF_VERBS = (
    "is|are|was|were|refers to|means|is defined as|is called|is known as|"
    "describes|defines|denotes|represents|provides|organizes|organises|reduces|"
    "removes|enables|allows|consists of|comprises|contains|specifies|"
    "is used to|are used to"
)
_DEF_RE = re.compile(
    r"^(?:The|A|An)?\s*([A-Za-z][\w-]+(?:\s+[\w-]+){0,4}?)\s+"
    r"(" + _DEF_VERBS + r")\s+(.+)$",
    re.I,
)
_PRONOUNS = {"it", "this", "that", "these", "those", "there", "they", "he", "she", "we", "you", "i",
             "here", "today", "now", "so", "then", "okay", "ok", "well", "right",
             "yeah", "alright", "basically", "essentially", "anyway"}
# Discourse fillers that lecturers prefix sentences with — stripped before
# matching so a definition isn't lost (or mis-subjected as "Okay so throughput").
_FILLER_PREFIX = re.compile(
    r"^(?:(?:okay|ok|so|now|well|um+|uh+|right|yeah|yep|alright|basically|"
    r"essentially|remember|today|anyway|anyways)\b[\s,]*)+", re.I)


def _clean_sentence(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _front_for_definition(subject: str, verb: str) -> str:
    plural = verb.lower() in ("are", "were", "are used to")
    return f"What {'are' if plural else 'is'} {subject.strip()}?"


def extract_cards(text: str, tags: List[str], max_cards: int = 20) -> List[Dict[str, Any]]:
    """Heuristically extract flashcards from a block of prose."""
    text = text or ""
    cards: List[Dict[str, Any]] = []
    seen_fronts: set = set()

    def add(front: str, back: str) -> None:
        front, back = _clean_sentence(front), _clean_sentence(back)
        if not front or not back or len(back) < 6:
            return
        key = front.lower()
        if key in seen_fronts:
            return
        seen_fronts.add(key)
        cards.append({"front": front, "back": back, "tags": list(tags)})

    # 1) Acronyms / abbreviations.
    for full, abbr in _ACRONYM_AFTER.findall(text):
        add(f"What does {abbr} stand for?", full)
    for abbr, full in _ACRONYM_BEFORE.findall(text):
        if not abbr.isupper():
            continue
        add(f"What does {abbr} stand for?", full)
    for abbr, full in _ACRONYM_STANDS.findall(text):
        add(f"What does {abbr} stand for?", full)

    # 2) Definitional / descriptive sentences (abbreviation-aware split).
    for raw in core.split_sentences(text):
        if len(cards) >= max_cards:
            break
        sent = _clean_sentence(_FILLER_PREFIX.sub("", raw))
        if len(sent) < 20 or len(sent) > 240:
            continue
        m = _DEF_RE.match(sent)
        if not m:
            continue
        subject, verb, predicate = m.group(1), m.group(2), m.group(3)
        if subject.split()[0].lower() in _PRONOUNS:
            continue
        if len(predicate) < 12:
            continue
        add(_front_for_definition(subject, verb), sent)

    return cards[:max_cards]


# ---------------------------------------------------------------------------
# Generate from the transcript library
# ---------------------------------------------------------------------------

def _best_text_for_group(output_dir: Path, group: Dict[str, Any], prefer: str = "summary") -> str:
    """Pick text to mine: summary (clean) -> json text -> txt/md."""
    fmts = group["formats"]
    order = (["summary", "json", "txt", "md"] if prefer == "summary"
             else ["json", "txt", "md", "summary"])
    for key in order:
        if key not in fmts:
            continue
        try:
            raw = (output_dir / fmts[key]).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if key == "json":
            import json
            try:
                return json.loads(raw).get("text", "") or ""
            except Exception:
                continue
        if key in ("md", "summary"):
            raw = re.sub(r"^#.*$", "", raw, flags=re.M)          # drop headings
            raw = re.sub(r"^[-*]\s*", "", raw, flags=re.M)        # bullet markers
            raw = re.sub(r"\[\d{1,2}:\d{2}:\d{2}\]", "", raw)     # stray timestamps
        return raw
    return ""


def generate_from_library(
    output_dir: Path,
    *,
    selection: Optional[List[str]] = None,
    course: str = "",
    prefer: str = "summary",
    max_per_lecture: int = 15,
) -> List[Dict[str, Any]]:
    """Build flashcards from existing transcripts. Tags come from each lecture's
    course/week/topic so the deck is pre-categorised."""
    cards: List[Dict[str, Any]] = []
    groups = core.list_transcripts(output_dir)
    if selection:
        wanted = set(selection)
        groups = [g for g in groups
                  if g["stem"] in wanted or f"{g['folder']}/{g['stem']}".strip("/") in wanted]
    for g in groups:
        text = _best_text_for_group(output_dir, g, prefer)
        if not text.strip():
            continue
        week = core.infer_week(g["stem"]) if core.infer_week(g["stem"]) is not None else core.infer_week(g["folder"])
        topic = core.infer_topic(g["stem"])
        tags = base_tags(course, week, topic) + [_tag(g["stem"])]
        for card in extract_cards(text, tags, max_per_lecture):
            card["lecture"] = g["stem"]
            cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# Categorise an existing deck
# ---------------------------------------------------------------------------

def parse_cards_text(text: str) -> List[Dict[str, Any]]:
    """Parse pasted/uploaded flashcards. Accepts TSV or CSV with 2-3 columns
    (front, back[, tags]); skips Anki '#...' header directives and blank lines."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    delim = "\t" if sum(ln.count("\t") for ln in lines) >= len(lines) else ","
    cards: List[Dict[str, Any]] = []
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
    for row in reader:
        row = [c.strip() for c in row]
        if not row or not row[0]:
            continue
        front = row[0]
        back = row[1] if len(row) > 1 else ""
        tags = row[2].split() if len(row) > 2 and row[2] else []
        cards.append({"front": front, "back": back, "tags": tags})
    return cards


def build_vocabulary(output_dir: Path, course: str = "", extra: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """Build a {tag: [keywords]} map from the course's weeks/topics + extras, used
    to categorise loose flashcards."""
    vocab: Dict[str, List[str]] = {}
    for g in core.list_transcripts(output_dir):
        week = core.infer_week(g["stem"])
        topic = core.infer_topic(g["stem"]).replace("_", " ")
        if topic and topic != "uncategorized":
            tag = base_tags(course, week, topic)
            if tag:
                kws = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", topic)]
                if kws:
                    vocab[tag[-1]] = kws
    for kw in (extra or []):
        kw = kw.strip()
        if kw:
            vocab[_tag(kw)] = [kw.lower()]
    return vocab


def categorise_cards(cards: List[Dict[str, Any]], vocabulary: Dict[str, List[str]],
                     course: str = "") -> List[Dict[str, Any]]:
    """Add tags to each card by matching its text against the vocabulary."""
    course_tag = _tag(course) if course else ""
    for card in cards:
        blob = f"{card.get('front', '')} {card.get('back', '')}".lower()
        tags = set(card.get("tags") or [])
        if course_tag:
            tags.add(course_tag)
        for tag, keywords in vocabulary.items():
            if any(kw in blob for kw in keywords):
                tags.add(tag)
        # detect an explicit "Week N" mention in the card text
        m = re.search(r"\bweek\s*0*(\d{1,2})\b", blob)
        if m:
            tags.add(f"Week{int(m.group(1)):02d}")
        card["tags"] = sorted(tags)
    return cards


# ---------------------------------------------------------------------------
# Serialisation + writing
# ---------------------------------------------------------------------------

def to_anki_tsv(cards: List[Dict[str, Any]]) -> str:
    """Anki-importable tab-separated text with header directives (Front, Back, Tags)."""
    out = io.StringIO()
    out.write("#separator:tab\n#html:false\n#columns:Front\tBack\tTags\n#tags column:3\n")
    for c in cards:
        front = (c.get("front") or "").replace("\t", " ").replace("\n", " ")
        back = (c.get("back") or "").replace("\t", " ").replace("\n", " ")
        tags = " ".join(c.get("tags") or [])
        out.write(f"{front}\t{back}\t{tags}\n")
    return out.getvalue()


def to_csv(cards: List[Dict[str, Any]]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")  # avoid \r\r\n after write_text on Windows
    w.writerow(["Front", "Back", "Tags"])
    for c in cards:
        w.writerow([c.get("front", ""), c.get("back", ""), " ".join(c.get("tags") or [])])
    return out.getvalue()


def write_deck(output_dir: Path, cards: List[Dict[str, Any]], deck: str = "flashcards") -> Dict[str, Any]:
    """Write a deck as both Anki TSV (.txt) and .csv under <output>/_flashcards/."""
    dest = core.ensure_dir(output_dir / FLASHCARDS_DIRNAME)
    stem = core.safe_name(deck) or "flashcards"
    tsv_path = dest / f"{stem}.txt"
    csv_path = dest / f"{stem}.csv"
    tsv_path.write_text(to_anki_tsv(cards), encoding="utf-8")
    csv_path.write_text(to_csv(cards), encoding="utf-8")
    return {
        "count": len(cards),
        "anki_tsv": tsv_path.relative_to(output_dir).as_posix(),
        "csv": csv_path.relative_to(output_dir).as_posix(),
        "preview": cards[:8],
    }
