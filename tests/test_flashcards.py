"""Tests for the flashcards module: generation, parsing, categorisation, export."""
from __future__ import annotations

from pathlib import Path

from app import core, flashcards


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------

def test_tag_slugs_spaces():
    assert flashcards._tag("transport layer") == "transport_layer"
    assert flashcards._tag("COMPX234::Week09") == "COMPX234::Week09"


def test_base_tags_hierarchical():
    assert flashcards.base_tags("COMPX234", 9, "Transport Layer") == \
        ["COMPX234::Week09", "Transport_Layer"]
    assert flashcards.base_tags("", 3, "") == ["Week03"]
    assert flashcards.base_tags("", None, "") == []


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def test_extract_acronyms():
    text = ("The Transmission Control Protocol (TCP) is reliable. "
            "UDP stands for User Datagram Protocol. "
            "HTTP (Hypertext Transfer Protocol) powers the web.")
    cards = flashcards.extract_cards(text, ["t"])
    fronts = {c["front"] for c in cards}
    assert "What does TCP stand for?" in fronts
    assert "What does UDP stand for?" in fronts
    assert "What does HTTP stand for?" in fronts


def test_extract_definitions():
    text = ("Flow control is a mechanism that prevents a fast sender from "
            "overwhelming a slow receiver. Congestion control refers to "
            "avoiding network overload.")
    cards = flashcards.extract_cards(text, [])
    fronts = {c["front"] for c in cards}
    assert "What is Flow control?" in fronts
    assert "What is Congestion control?" in fronts


def test_extract_skips_pronoun_and_short():
    text = "It is important. This is good. X is."
    assert flashcards.extract_cards(text, []) == []


def test_extract_dedupes_and_caps():
    text = "TCP (Transmission Control Protocol) is here. " * 5
    cards = flashcards.extract_cards(text, [], max_cards=3)
    assert len(cards) <= 3
    assert len({c["front"] for c in cards}) == len(cards)


def test_extract_attaches_tags():
    cards = flashcards.extract_cards("UDP stands for User Datagram Protocol.", ["COMPX234::Week09"])
    assert cards and cards[0]["tags"] == ["COMPX234::Week09"]


# ---------------------------------------------------------------------------
# generate from the library
# ---------------------------------------------------------------------------

def _seed(tmp_path: Path):
    it = core.LectureItem(title="Week9_Transport_Layer", url="u")
    text = ("The Transmission Control Protocol (TCP) is reliable. "
            "A socket is an endpoint. Flow control is a mechanism that "
            "prevents overwhelming a receiver.")
    core.write_outputs(it, [{"start": 0, "end": 5, "text": text}], text,
                       core.output_dir_for(tmp_path, it, "week"),
                       ["txt", "json", "summary"], 30, {"course": "COMPX234"})


def test_generate_from_library(tmp_path: Path):
    _seed(tmp_path)
    cards = flashcards.generate_from_library(tmp_path, course="COMPX234")
    assert cards
    assert any("TCP" in c["front"] for c in cards)
    assert all("COMPX234::Week09" in c["tags"] for c in cards)


def test_generate_empty_library(tmp_path: Path):
    assert flashcards.generate_from_library(tmp_path) == []


# ---------------------------------------------------------------------------
# parse + categorise existing decks
# ---------------------------------------------------------------------------

def test_parse_tsv_with_anki_header():
    text = "#separator:tab\n#tags column:3\nWhat is TCP?\tReliable protocol\tnet\n"
    cards = flashcards.parse_cards_text(text)
    assert len(cards) == 1
    assert cards[0]["front"] == "What is TCP?"
    assert cards[0]["back"] == "Reliable protocol"
    assert cards[0]["tags"] == ["net"]


def test_parse_csv_two_columns():
    cards = flashcards.parse_cards_text("Front,Back here\nQ2,A2")
    assert len(cards) == 2
    assert cards[1] == {"front": "Q2", "back": "A2", "tags": []}


def test_parse_empty():
    assert flashcards.parse_cards_text("") == []
    assert flashcards.parse_cards_text("#only headers\n") == []


def test_categorise_adds_tags(tmp_path: Path):
    _seed(tmp_path)
    vocab = flashcards.build_vocabulary(tmp_path, course="COMPX234", extra=["router"])
    cards = [
        {"front": "What is a router?", "back": "Forwards packets", "tags": []},
        {"front": "Define transport reliability", "back": "TCP in week 9", "tags": ["keep"]},
    ]
    out = flashcards.categorise_cards(cards, vocab, course="COMPX234")
    assert "COMPX234" in out[0]["tags"] and "router" in out[0]["tags"]
    assert "keep" in out[1]["tags"]
    assert "Week09" in out[1]["tags"]  # "week 9" detected in text


# ---------------------------------------------------------------------------
# serialise + write
# ---------------------------------------------------------------------------

def test_anki_tsv_has_headers():
    tsv = flashcards.to_anki_tsv([{"front": "Q", "back": "A", "tags": ["x", "y"]}])
    assert tsv.startswith("#separator:tab")
    assert "#tags column:3" in tsv
    assert "Q\tA\tx y" in tsv


def test_to_csv_roundtrips_via_parse():
    cards = [{"front": "Q1", "back": "A1", "tags": ["t1"]}]
    csv_text = flashcards.to_csv(cards)
    parsed = flashcards.parse_cards_text(csv_text)
    # header row "Front,Back,Tags" parses as a card too; the data row is last
    assert parsed[-1]["front"] == "Q1" and parsed[-1]["tags"] == ["t1"]


def test_write_deck_outputs_and_is_internal(tmp_path: Path):
    cards = [{"front": "Q", "back": "A", "tags": ["t"]}]
    res = flashcards.write_deck(tmp_path, cards, "mydeck")
    assert res["count"] == 1
    assert (tmp_path / res["anki_tsv"]).exists()
    assert (tmp_path / res["csv"]).exists()
    # _flashcards is an internal folder, so decks never show as transcripts
    assert core.list_transcripts(tmp_path) == []
