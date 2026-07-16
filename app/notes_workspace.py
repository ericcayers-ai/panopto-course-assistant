"""notes_workspace.py - notes library helpers (folders, import, flashcard sets).

Extends the path-attached notes DAO into a small workspace: folders, session
types (lecture / tutorial / lab), Word/PDF import into notes, and one-tap
flashcard sets seeded into spaced-repetition review items.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import flashcards, study_planner
from .core import now_iso
from .database import Database

SESSION_TYPES = ("", "lecture", "tutorial", "lab", "other")
NOTE_IMPORT_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".html", ".htm"}


def folder_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "course_id": row["course_id"],
        "name": row["name"],
        "parent_id": row["parent_id"],
        "created_at": row["created_at"],
    }


def note_dict(row) -> Dict[str, Any]:
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": row["id"],
        "course_id": row["course_id"] if "course_id" in keys else None,
        "path": row["path"] or "",
        "body": row["body"],
        "title": row["title"] if "title" in keys else "",
        "folder_id": row["folder_id"] if "folder_id" in keys else None,
        "session_type": row["session_type"] if "session_type" in keys else "",
        "timestamp_s": row["timestamp_s"],
        "bookmark": bool(row["bookmark"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def set_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "course_id": row["course_id"],
        "name": row["name"],
        "source_note_id": row["source_note_id"],
        "source_path": row["source_path"] or "",
        "card_count": row["card_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_workspace(db: Database, course_id: Optional[int] = None) -> Dict[str, Any]:
    folders = [folder_dict(r) for r in db.list_note_folders(course_id)]
    notes = [note_dict(r) for r in db.list_notes(course_id=course_id)]
    sets = [set_dict(r) for r in db.list_flashcard_sets(course_id)]
    return {
        "folders": folders,
        "notes": notes,
        "flashcard_sets": sets,
        "counts": {
            "folders": len(folders),
            "notes": len(notes),
            "flashcard_sets": len(sets),
        },
    }


def _read_plain(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_document_text(path: Path) -> str:
    """Return plain/markdown text from a Word, PDF, or text file."""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    ext = path.suffix.lower()
    if ext not in NOTE_IMPORT_EXTS:
        raise ValueError(f"Unsupported file type: {ext or '(none)'}. "
                         f"Use Word, PDF, Markdown, HTML, or plain text.")
    if ext in {".txt", ".md", ".markdown"}:
        return _read_plain(path)
    if ext in {".html", ".htm"}:
        raw = _read_plain(path)
        # Light strip of tags for note body (stdlib only).
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()
    # PDF / Word via markitdown when available.
    try:
        from .core import _markitdown_converter
        convert = _markitdown_converter()
        return convert(path)
    except Exception as e:
        raise RuntimeError(
            f"Could not convert {path.name}. Install markitdown "
            f"(pip install markitdown) or paste the text manually. ({e})"
        ) from e


def import_file_as_note(db: Database, file_path: str | Path, *,
                        course_id: Optional[int] = None,
                        folder_id: Optional[int] = None,
                        session_type: str = "",
                        attach_path: str = "",
                        title: str = "") -> Dict[str, Any]:
    path = Path(file_path).expanduser()
    body = extract_document_text(path).strip()
    if not body:
        raise ValueError(f"No text extracted from {path.name}.")
    st = (session_type or "").strip().lower()
    if st and st not in SESSION_TYPES:
        raise ValueError(f"session_type must be one of {SESSION_TYPES}")
    note_title = (title or path.stem).strip()
    nid = db.add_note(
        attach_path or "",
        body,
        course_id=course_id,
        title=note_title,
        folder_id=folder_id,
        session_type=st,
    )
    row = db.get_note(nid)
    return note_dict(row)


def cards_from_note_body(body: str, max_cards: int = 40) -> List[Dict[str, str]]:
    """Heuristic cards from note text (definitions + Q/A lines)."""
    cards: List[Dict[str, str]] = []
    text = body or ""
    try:
        raw = flashcards.extract_cards(text, tags=[], max_cards=max_cards) or []
        for c in raw:
            front = (c.get("front") or "").strip()
            back = (c.get("back") or "").strip()
            if front and back:
                cards.append({"front": front, "back": back})
    except Exception:
        cards = []
    if len(cards) < max_cards:
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^(.{2,80}?)\s*[—–\-:=]\s+(.{8,400})$", line)
            if m:
                front, back = m.group(1).strip(), m.group(2).strip()
                if not any(c["front"] == front for c in cards):
                    cards.append({"front": front, "back": back})
            if len(cards) >= max_cards:
                break
    if len(cards) < max_cards:
        qa = re.findall(
            r"(?im)^\s*Q[:\.]?\s*(.+?)\s*$\s*A[:\.]?\s*(.+?)\s*$",
            text)
        for q, a in qa:
            cards.append({"front": q.strip(), "back": a.strip()})
            if len(cards) >= max_cards:
                break
    return cards[:max_cards]


def create_set_from_note(db: Database, note_id: int, *,
                         name: str = "",
                         max_cards: int = 40) -> Dict[str, Any]:
    row = db.get_note(note_id)
    if row is None:
        raise ValueError("Note not found")
    cards = cards_from_note_body(row["body"], max_cards=max_cards)
    if not cards:
        raise ValueError("No flashcards could be extracted from this note.")
    title = name or (row["title"] if "title" in row.keys() and row["title"]
                     else "Flashcard set")
    if not title.strip():
        title = f"Notes set · {now_iso()[:10]}"
    course_id = row["course_id"]
    if course_id is None:
        raise ValueError("Note has no course. Assign it to a course first.")
    sid = db.create_flashcard_set(
        course_id, title.strip(),
        source_note_id=note_id,
        source_path=row["path"] or "",
        card_count=len(cards),
    )
    # Seed SM-2 review items tagged to this set.
    seeded = study_planner.add_review_items(
        db, course_id,
        [{"front": c["front"], "back": c["back"], "ref": f"set:{sid}"} for c in cards],
    )
    db.update_flashcard_set(sid, card_count=seeded if isinstance(seeded, int) else len(cards))
    return {
        "set": set_dict(db.get_flashcard_set(sid)),
        "cards": cards,
        "seeded": seeded if isinstance(seeded, int) else len(cards),
    }
