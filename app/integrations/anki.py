"""
integrations/anki.py - push flashcards into Anki via AnkiConnect (§5).

Talks to the AnkiConnect add-on (default http://127.0.0.1:8765). Creates the
deck if needed, adds notes tagged by course · week · topic, and is duplicate-aware
(Anki rejects exact duplicates; we count those rather than failing). Supports a
dry-run preview. Transport is injectable for offline testing.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

DEFAULT_URL = "http://127.0.0.1:8765"

Transport = Callable[[Dict[str, Any]], Dict[str, Any]]


class AnkiError(Exception):
    pass


def _http_transport(payload: Dict[str, Any], url: str = DEFAULT_URL) -> Dict[str, Any]:
    import requests
    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise AnkiError(f"AnkiConnect not reachable ({e}). Is Anki running with the "
                        "AnkiConnect add-on?") from e


class AnkiConnect:
    def __init__(self, transport: Optional[Transport] = None, url: str = DEFAULT_URL) -> None:
        self.url = url
        self._t = transport or (lambda p: _http_transport(p, url))

    def invoke(self, action: str, **params: Any) -> Any:
        resp = self._t({"action": action, "version": 6, "params": params})
        if isinstance(resp, dict) and resp.get("error"):
            raise AnkiError(resp["error"])
        return resp.get("result") if isinstance(resp, dict) else resp

    def deck_names(self) -> List[str]:
        return self.invoke("deckNames") or []

    def create_deck(self, name: str) -> None:
        self.invoke("createDeck", deck=name)

    def add_notes(self, notes: List[Dict[str, Any]]) -> List[Optional[int]]:
        # addNotes returns a note id per note, or null where it was a duplicate.
        return self.invoke("addNotes", notes=notes) or []


def _note(deck: str, front: str, back: str, tags: List[str]) -> Dict[str, Any]:
    return {
        "deckName": deck, "modelName": "Basic",
        "fields": {"Front": front, "Back": back},
        "options": {"allowDuplicate": False},
        "tags": tags,
    }


def sync_flashcards(cards: List[Dict[str, Any]], deck: str, *, course: str = "",
                   dry_run: bool = False, transport: Optional[Transport] = None,
                   url: str = DEFAULT_URL) -> Dict[str, Any]:
    """Push ``cards`` ([{front, back, tags?}]) into ``deck``. Tags include the
    course; per-card week/topic tags are preserved when present."""
    notes = []
    for c in cards:
        front, back = c.get("front", ""), c.get("back", "")
        if not front:
            continue
        tags = ["course-assistant"]
        if course:
            tags.append(course.replace(" ", "_"))
        raw = c.get("tags", "")
        if isinstance(raw, str):
            tags += [t for t in raw.split() if t]
        elif isinstance(raw, list):
            tags += raw
        notes.append(_note(deck, front, back, tags))

    if dry_run:
        return {"dry_run": True, "deck": deck, "would_add": len(notes)}

    client = AnkiConnect(transport=transport, url=url)
    if deck not in client.deck_names():
        client.create_deck(deck)
    ids = client.add_notes(notes)
    added = sum(1 for i in ids if i)
    duplicates = sum(1 for i in ids if not i)
    return {"dry_run": False, "deck": deck, "added": added, "duplicates": duplicates}
