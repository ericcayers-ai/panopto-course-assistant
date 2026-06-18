"""§5 Anki sync via AnkiConnect: deck creation, add, dedup, dry-run - offline."""
from __future__ import annotations

import pytest

from app.integrations import anki as anki_sync


class FakeAnki:
    def __init__(self, decks=(), dup_indices=()):
        self.decks = list(decks)
        self.dup_indices = set(dup_indices)
        self.added_batches = []

    def __call__(self, payload):
        action = payload["action"]
        if action == "deckNames":
            return {"result": self.decks}
        if action == "createDeck":
            self.decks.append(payload["params"]["deck"])
            return {"result": 1}
        if action == "addNotes":
            notes = payload["params"]["notes"]
            self.added_batches.append(notes)
            # null id where the note is a duplicate
            return {"result": [None if i in self.dup_indices else 1000 + i
                               for i in range(len(notes))]}
        return {"result": None}


CARDS = [{"front": "What is TCP?", "back": "a transport protocol", "tags": "week-2"},
         {"front": "What is UDP?", "back": "a datagram protocol"}]


def test_dry_run_counts_without_calling(tmp_path):
    res = anki_sync.sync_flashcards(CARDS, "Deck", course="COMPX234", dry_run=True)
    assert res == {"dry_run": True, "deck": "Deck", "would_add": 2}


def test_creates_deck_and_adds_notes(tmp_path):
    fake = FakeAnki(decks=[])
    res = anki_sync.sync_flashcards(CARDS, "COMPX234", course="COMPX234",
                                    transport=fake)
    assert res["added"] == 2 and res["duplicates"] == 0
    assert "COMPX234" in fake.decks  # deck auto-created
    # course + per-card tags propagated
    tags = fake.added_batches[0][0]["tags"]
    assert "COMPX234" in tags and "week-2" in tags


def test_duplicates_counted_not_failed():
    fake = FakeAnki(decks=["COMPX234"], dup_indices=[1])
    res = anki_sync.sync_flashcards(CARDS, "COMPX234", transport=fake)
    assert res["added"] == 1 and res["duplicates"] == 1


def test_cards_without_front_skipped():
    fake = FakeAnki(decks=["D"])
    res = anki_sync.sync_flashcards([{"back": "no front"}, CARDS[0]], "D",
                                    transport=fake)
    assert res["added"] == 1
